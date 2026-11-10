"""Telegram discovery, preserving entities and dates rather than inventing jobs."""
import os
import re
from datetime import timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .audit import plain
from .policy import canonical_url, clean, now_utc, parse_date


def parse_preview(html, channel):
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for message in soup.select(".tgme_widget_message[data-post]"):
        text_node = message.select_one(".tgme_widget_message_text")
        stamp = message.select_one("time[datetime]")
        # Inline emphasis/links must not split a field label from its value.
        if text_node:
            for br in text_node.select("br"):
                br.replace_with("\n")
        text = "\n".join(" ".join(line.split()) for line in text_node.get_text().splitlines()) if text_node else ""
        urls = []
        for a in message.select(".tgme_widget_message_text a[href], .tgme_widget_message_inline_button[href]"):
            url = canonical_url(urljoin("https://t.me/", a["href"]))
            if url:
                urls.append(url)
        posts.append({"postId": message["data-post"], "url": "https://t.me/" + message["data-post"],
                      "text": text, "links": list(dict.fromkeys(urls)),
                      "datetime": stamp["datetime"] if stamp else "", "pinned": "pinned" in message.get("class", [])})
    more = soup.select_one("a.tme_messages_more[data-before]") or soup.select_one("a[data-before]")
    return posts, more.get("data-before") if more else None


def fetch_preview(channel, fetcher, store, max_pages=30, now=None, hours=168):
    now = now or now_utc()
    cutoff = now - timedelta(hours=hours)
    key = "telegram:" + channel + (':window:'+str(hours) if hours!=168 else '')
    state = store.get(key, {})
    previous_id = state.get("last_id", 0)
    reconcile = now.timestamp() - state.get("last_reconcile", 0) >= 86400
    resume = state.get("resume", {})
    before = resume.get("before")
    high_water = max(previous_id, resume.get("high_water", 0))
    posts, seen, complete = [], set(), False
    reason = "page_budget_reached"
    pages = 0
    for _ in range(max_pages):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        try:
            response = fetcher.get(url, ttl=300)
        except Exception as exc:
            reason = "fetch_failed: " + str(exc)
            break
        batch, next_before = parse_preview(response["text"], channel)
        pages += 1
        if not batch:
            reason = "preview_unavailable_or_parser_failed"
            break
        ids = []
        dated = []
        for post in batch:
            try:
                message_id = int(post["postId"].rsplit("/", 1)[1])
            except (ValueError, IndexError):
                continue
            ids.append(message_id)
            high_water = max(high_water, message_id)
            bounds = parse_date(post["datetime"])
            if bounds and not post["pinned"]:
                dated.append(bounds[0])
            if post["postId"] in seen:
                continue
            seen.add(post["postId"])
            # Keep unknown dates pending. They never acquire a synthetic current date.
            if not bounds or bounds[0] >= cutoff:
                posts.append(post)
        if dated and max(dated) < cutoff:
            complete, reason = True, "freshness_window_covered"
            break
        if previous_id and not reconcile and ids and max(ids) <= previous_id:
            complete, reason = True, "cursor_reached"
            break
        if not next_before:
            complete, reason = True, "history_exhausted"
            break
        if next_before == before:
            reason = "pagination_stalled"
            break
        before = next_before
    if complete:
        store.set(key, {"last_id": high_water, "last_success": now.isoformat(),
                        "last_reconcile": now.timestamp() if reconcile else state.get("last_reconcile", 0)})
    else:
        store.set(key, {**state, "resume": {"before": before, "high_water": high_water}})
    return posts, {"state": "ok" if complete else "incomplete", "reason": reason,
                   "pages": pages, "fetched": len(posts), "complete": complete}


def field(text, names):
    match = re.search(r"(?im)^\s*(?:" + names + r")\s*[:፡፦-]\s*([^\n]+)", text)
    return clean(match[1]) if match else ""


def parse_post(post, is_local=False):
    """The channel's category is provenance, never location/employer evidence."""
    text = clean(post.get("text"))
    if not text:
        return []
    # Separate explicitly labelled vacancies; ambiguous multi-role posts remain pending.
    chunks = re.split(r"(?im)(?=^\s*(?:job title|position title|የሥራ መደብ|የስራ መደብ)\s*[:፡፦])", text)
    chunks = [c for c in chunks if c.strip()]
    labelled = [c for c in chunks if re.match(r"(?i)\s*(?:job title|position title|የሥራ መደብ|የስራ መደብ)\s*[:፡፦]", c)]
    chunks = labelled if labelled else [text]
    leads = []
    for i, chunk in enumerate(chunks):
        title = field(chunk, "job title|position title|position|የሥራ መደብ|የስራ መደብ")
        company = field(chunk, "company|employer|organization|ድርጅት|የድርጅቱ ስም")
        location = field(chunk, "work location|location|የሥራ ቦታ|የስራ ቦታ")
        if not title:
            lines = [l.strip() for l in chunk.splitlines() if l.strip()]
            title = lines[0] if lines and len(lines[0]) <= 160 else ""
            # HaHuJobs posts often put company in the next hashtag line.
            if not company and len(lines) > 1 and re.fullmatch(r"#[\w_]+", lines[1]):
                company = lines[1].lstrip("#").replace("_", " ")
        inline_urls = re.findall(r"https?://[^\s<>]+", chunk)
        # A visible channel URL must not hide the actual application link stored
        # in a text entity or button (common in Ethiojobs digest posts).
        extracted = inline_urls + (post.get('links',[]) if len(chunks)==1 else [])
        candidates = list(dict.fromkeys(canonical_url(u) for u in extracted if canonical_url(u)))
        external = [u for u in candidates if not re.match(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/", u)]
        url = external[0] if len(external) == 1 else post["url"]
        leads.append({"title": title, "company": company, "location": location,
                      "salary": field(chunk, "salary|compensation|pay|ደመወዝ"),
                      "description": chunk, "url": url, "discovery_url": post["url"],
                      "source": "telegram:" + post["postId"].split("/")[0],
                      "source_id": post["postId"] + f":{i}", "message_date": post.get("datetime", ""),
                      # A forward/repost date is not the original vacancy publication date.
                      "published_at": "", "workplace": field(chunk,'workplace|work mode|job type'), "links": candidates,
                      'closed':bool(re.search(r'(?im)^\s*[- ]*CLOSED[- ]*\s*$',chunk))})
    return leads


def fetch_mtproto(channel, store, now=None, hours=168):
    """Run an isolated async reader, including in an HTTP server worker thread."""
    import asyncio
    return asyncio.run(_fetch_mtproto(channel, store, now, hours))


async def _fetch_mtproto(channel, store, now=None, hours=168):
    try:
        from telethon.errors import FloodWaitError
    except ImportError as exc:
        raise RuntimeError("Optional Telethon adapter is not installed; use requirements-telegram.txt") from exc
    from .telegram_auth import make_client, write_status
    now = now or now_utc()
    cutoff = now - timedelta(hours=hours)
    posts, complete = [], False
    client = make_client()
    try:
        await client.connect()
        if not await client.is_user_authorized():
            write_status("needs_login", reason="session_not_authorized")
            raise RuntimeError("Telegram session needs login: python connect_telegram.py")
        async for msg in client.iter_messages(channel, limit=2000):
            if msg.date < cutoff:
                complete = True
                break
            urls = re.findall(r"https?://[^\s<>]+", msg.message or "")
            for entity in msg.entities or []:
                if getattr(entity, "url", None):
                    urls.append(entity.url)
            for row in msg.buttons or []:
                for button in row:
                    if getattr(button, "url", None):
                        urls.append(button.url)
            posts.append({"postId": f"{channel}/{msg.id}", "url": f"https://t.me/{channel}/{msg.id}",
                          "text": msg.message or "", "datetime": msg.date.isoformat(), "links": urls,
                          "edited_at": msg.edit_date.isoformat() if msg.edit_date else "",
                          "has_media": bool(msg.media)})
        else:
            complete = len(posts) < 2000
        store.set("mtproto:" + channel, {"last_success": now.isoformat(), "last_id": max((int(p["postId"].rsplit("/", 1)[1]) for p in posts), default=0)})
    except FloodWaitError as exc:
        raise RuntimeError(f"Telegram throttled; retry after {exc.seconds} seconds") from exc
    finally:
        await client.disconnect()
    return posts, {"state": "ok" if complete else "incomplete", "complete": complete,
                   "fetched": len(posts), "reason": "window_reconciled" if complete else "message_budget_reached"}


def login():
    from connect_telegram import main
    main()
