"""
Email Monitor for Career Dashboard
Monitors Gmail inboxes for interview invites, coding assessments, rejections, and job offers.
Supports multiple monitored accounts and updates career-dashboard/jobs.json.
"""

import os
import sys
import json
import re
import imaplib
import email
import unicodedata
from email.header import decode_header
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from discovery.store import apply_email_updates
from discovery.store import LeadStore, read_jobs
from email.utils import parsedate_to_datetime
import hashlib

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

DIRECTORY = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(DIRECTORY, "jobs.json")
CONFIG_FILE = os.path.join(DIRECTORY, ".email_config.json")

# Acknowledgment / receipt phrases (confirms submission, does NOT trigger interview status)
KEYWORDS_ACKNOWLEDGMENT = re.compile(
    r"(?i)\b("
    r"thank you for applying|"
    r"thanks for applying|"
    r"thank you for sending us your application|"
    r"received your application|"
    r"confirmation of your application|"
    r"confirmation application|"
    r"we have received your application|"
    r"new job application received|"
    r"application was sent to|"
    r"application has been received|"
    r"application has been submitted|"
    r"application submitted|"
    r"your cv is currently under review|"
    r"processing your application|"
    r"in the process of screening applications|"
    r"talent acquisition team received|"
    r"application was submitted successfully|"
    r"you(?:'ve| have)?\s+just applied to|"
    r"you applied to|"
    r"your application to"
    r")\b"
)

# True Interview / Assessment Invitations
KEYWORDS_INTERVIEW = re.compile(
    r"(?i)\b("
    r"invite you to (an |a |the |your )?(interview|call|assessment|challenge|coding task|test)|"
    r"invitation to (an |a |the |your )?(interview|call|assessment|challenge|coding task|test)|"
    r"like to schedule (an? |a call|an interview|a quick chat)|"
    r"pleased to invite you|"
    r"next steps in your application.*(interview|assessment|challenge)|"
    r"(?:you're|you are) through to the next step|"
    r"through to the next step|"
    r"select a time for (your )?interview|"
    r"book (your )?(interview|call|meeting)|"
    r"coding challenge|"
    r"technical assessment|"
    r"coding assessment|"
    r"send you the assessment|"
    r"assessment instructions|"
    r"take the assessment|"
    r"complete the assessment|"
    r"assessment invitation|"
    r"challenge invitation|"
    r"walkthrough|"
    r"screening (chat|call)|"
    r"finish the verification of your profile|"
    r"calendly\.com\/|hirevue\.com\/|hackerrank\.com\/|codility\.com\/|testgorilla\.com\/|coderbyte\.com\/"
    r")\b"
)

# Rejections (exclude conditional clauses like "if you do not hear... consider that not selected")
KEYWORDS_REJECTION = re.compile(
    r"(?i)\b("
    r"unfortunately\b[^\.\n]*?\b(not moving forward|not selected|decided not to|pursuing other|moving forward with other|unable to offer|cannot offer|will not be)|"
    r"not moving forward with your (application|candidacy)|"
    r"decided to move forward with other candidates|"
    r"regret to inform|"
    r"pursuing other candidates|"
    r"not selected for this (role|position)|"
    r"decided not to move forward|"
    r"will not be moving forward|"
    r"will not be proceeding|"
    r"decided to pursue other"
    r")\b"
)

# Offers
KEYWORDS_OFFER = re.compile(
    r"(?i)\b("
    r"offer of employment|"
    r"pleased to offer|"
    r"formal offer|"
    r"congratulations.*offer|"
    r"extend an offer of"
    r")\b"
)

def normalize_text(text):
    if not text:
        return ""
    text = unicodedata.normalize('NFKD', text)
    text = text.replace('’', "'").replace('`', "'").replace('´', "'").replace('“', '"').replace('”', '"')
    return text.lower()

def load_jobs():
    return read_jobs(JOBS_FILE)

def decode_mime_words(s):
    if not s:
        return ""
    decoded_fragments = decode_header(s)
    result = []
    for fragment, encoding in decoded_fragments:
        if isinstance(fragment, bytes):
            result.append(fragment.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(str(fragment))
    return "".join(result)

def extract_body(msg):
    text_content = ""
    html_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            cdispo = str(part.get("Content-Disposition"))
            if "attachment" not in cdispo:
                if ctype == "text/plain":
                    try:
                        p = part.get_payload(decode=True)
                        if p:
                            text_content += p.decode("utf-8", errors="replace") + "\n"
                    except Exception:
                        pass
                elif ctype == "text/html":
                    try:
                        p = part.get_payload(decode=True)
                        if p:
                            html_content += p.decode("utf-8", errors="replace") + "\n"
                    except Exception:
                        pass
    else:
        ctype = msg.get_content_type()
        p = msg.get_payload(decode=True)
        if p:
            decoded = p.decode("utf-8", errors="replace")
            if ctype == "text/html":
                html_content = decoded
            else:
                text_content = decoded

    body_parts = []
    if text_content.strip():
        body_parts.append(text_content.strip())
    if html_content.strip():
        try:
            soup = BeautifulSoup(html_content, "html.parser")
            body_parts.append(soup.get_text(separator=" ", strip=True))
        except Exception:
            body_parts.append(html_content.strip())
    return "\n".join(body_parts)

def get_company_aliases(job):
    co = job.get("company", "").strip()
    norm = normalize_text(co)
    aliases = [norm]
    # Strip common corporate suffixes
    cleaned = re.sub(
        r"\b(inc|ltd|llc|group|solutions|consulting|partners|professionals|technologies|corporation|de belgique|south africa|eit)\b",
        "",
        norm,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace(".", "").replace(",", "").replace("'", "").strip()
    if len(cleaned) >= 4 and cleaned not in aliases:
        aliases.append(cleaned)
    # Special abbreviation mappings
    if "ag consulting" in norm:
        aliases.append("agcp")
    if "infinity advanced" in norm:
        aliases.append("iats")
    if "european dynamics" in norm:
        aliases.append("eurodyn")
    if "arhs" in norm:
        aliases.append("arhs")
    return [a for a in aliases if len(a) >= 3 and a not in ["if", "it", "at", "to", "in", "on"]]

QUALIFIERS = ["junior", "jr", "senior", "sr", "lead", "principal", "staff", "associate", "intern", "graduate"]

def score_job_title_match(job, text):
    t_norm = normalize_text(job.get("title", ""))
    if not t_norm:
        return 0.0
    m = re.search(r"\b" + re.escape(t_norm) + r"\b", text)
    if m:
        start = m.start()
        prefix = text[:start].rstrip()
        preceding_word = prefix.split()[-1] if prefix.split() else ""
        preceding_word = re.sub(r"[^a-z0-9]", "", preceding_word)
        if preceding_word in QUALIFIERS and preceding_word not in t_norm.split():
            return 0.75
        words = t_norm.split()
        return min(1.0, 0.95 + len(words) * 0.02)

    base_title = re.sub(r"\(.*?\)", "", t_norm).strip()
    if base_title and re.search(r"\b" + re.escape(base_title) + r"\b", text):
        return 0.90

    def _token_score(s):
        all_tokens = [t for t in re.split(r"[\s\(\)/,&-]+", s) if t]
        if not all_tokens:
            return 0.0
        missing_qualifiers = any(q in all_tokens and not re.search(r"\b" + re.escape(q) + r"\b", text) for q in QUALIFIERS)
        if missing_qualifiers:
            return 0.3
        tokens = [t for t in all_tokens if t not in [
            "developer", "consultant", "engineer", "senior", "junior", "remote",
            "applied", "specialist", "associate", "lead", "architect"
        ]]
        if not tokens:
            matches_all = sum(1 for t in all_tokens if re.search(r"\b" + re.escape(t) + r"\b", text))
            return (matches_all / len(all_tokens)) * 0.6
        matches = sum(1 for t in tokens if re.search(r"\b" + re.escape(t) + r"\b", text))
        return (matches / len(tokens)) * 0.7

    return max(_token_score(t_norm), _token_score(base_title) if base_title else 0.0)

def get_accounts():
    accounts = []
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "accounts" in cfg and isinstance(cfg["accounts"], list):
                    accounts = cfg["accounts"]
                elif cfg.get("email_user") and cfg.get("app_password"):
                    accounts.append({
                        "email_user": cfg.get("email_user"),
                        "app_password": cfg.get("app_password")
                    })
        except Exception as e:
            print(f"[!] Error reading config: {e}")

    env_user = os.environ.get("GMAIL_USER")
    env_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if env_user and env_pass:
        accounts.append({"email_user": env_user, "app_password": env_pass})

    return accounts


def classify_event(text):
    # Quoted prior correspondence cannot determine the current message's outcome.
    text = re.split(r"(?im)^\s*(?:on .+wrote:|-----original message-----|from: .+|>{1,})", text)[0]
    matches = []
    for kind, pattern in [("rejected", KEYWORDS_REJECTION), ("offer", KEYWORDS_OFFER),
               ("interviewing", KEYWORDS_INTERVIEW)]:
        for m in pattern.finditer(text):
            if kind == "rejected":
                clause_start = max(text.rfind('.', 0, m.start()), text.rfind('\n', 0, m.start()), text.rfind(';', 0, m.start()))
                clause = text[clause_start + 1:m.start()].strip().lower()
                if re.search(r'\bif\b', clause):
                    continue
            matches.append((kind, pattern))
            break
    if len(matches) > 1:
        return None, "Conflicting outcome language; review required"
    if matches:
        return matches[0][0], "Explicit application outcome language"
    if KEYWORDS_ACKNOWLEDGMENT.search(text):
        return "applied", "Application receipt confirmed"
    return None, "No outcome detected"


def match_application(jobs, text):
    candidates = [j for j in jobs if any(re.search(r"\b" + re.escape(a) + r"\b", text) for a in get_company_aliases(j))]
    ranked = sorted([(score_job_title_match(j, text), j) for j in candidates], key=lambda pair: pair[0], reverse=True)
    if not ranked or ranked[0][0] < .8 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < .2):
        return None
    return ranked[0][1]


def check_account_emails(account, monitored_jobs, updates, store=None):
    store = store or LeadStore()
    user = str(account.get("email_user", "")).strip()
    password = str(account.get("app_password", "")).strip().replace(" ", "")
    outcome = {"account": user, "state": "failed", "messagesChecked": 0, "suggestions": []}
    if not user or not password:
        return {**outcome, "reason": "Account credentials are not configured"}
    mail = None
    fetch_failures = 0
    def review(key, subject, reason, job=None, target=None, event_at=None):
        item = {'reviewId': key.rsplit(':', 1)[-1], 'subject': subject, 'reason': reason,
                'jobId': job['id'] if job else None, 'target': target, 'eventAt': event_at,
                'account': user, 'state': 'pending'}
        store.set('email-review:' + item['reviewId'], item)
        outcome['suggestions'].append(item)
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=20)
        mail.login(user, password)
        mail.select("INBOX", readonly=True)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")
        code, found = mail.uid("search", None, f'(SINCE "{cutoff}")')
        if code != "OK":
            raise RuntimeError("Inbox search failed")
        for uid in (found[0].split() if found and found[0] else [])[-300:]:
            code, data = mail.uid("fetch", uid, "(BODY.PEEK[])")
            if code != "OK":
                fetch_failures += 1
                continue
            raw = next((part[1] for part in data if isinstance(part, tuple) and isinstance(part[1], bytes)), None)
            if not raw:
                continue
            message = email.message_from_bytes(raw)
            message_id = message.get("Message-ID") or hashlib.sha256(raw).hexdigest()
            key = "email-message:" + hashlib.sha256(message_id.encode()).hexdigest()
            if store.get(key):
                continue
            subject = decode_mime_words(message.get("Subject", ""))
            body = re.split(r"(?im)^\s*(?:on .+wrote:|-----original message-----|from: .+|>{1,})", extract_body(message))[0]
            text = normalize_text(subject + "\n" + decode_mime_words(message.get("From", "")) + "\n" + body)
            outcome["messagesChecked"] += 1
            target, reason = classify_event(text)
            if not target:
                if "Conflicting" in reason:
                    review(key, subject, reason)
                store.set(key, {"state": "review" if "Conflicting" in reason else "no_event"})
                continue
            job = match_application(monitored_jobs, text)
            try:
                stamp = parsedate_to_datetime(message.get("Date", ""))
                if not stamp.tzinfo:
                    raise ValueError("Unknown timezone")
                event_at = stamp.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError, AttributeError):
                event_at = ""
            from dashboard import TRANSITIONS
            if not job or not event_at or (event_at > datetime.now(timezone.utc).isoformat()) or job['status'] in {'ready', 'saved'} or (job["status"] in {"offer", "declined", "rejected"} and target != job["status"]) or (target != job['status'] and target not in TRANSITIONS.get(job['status'], [])):
                review(key, subject, 'Application identity, date or stage needs review', job, target, event_at)
            else:
                updates.append({"job_id": job["id"], "company": job["company"], "title": job["title"],
                                "from_status": job["status"], "to_status": target, "eventAt": event_at,
                                "messageId": message_id, "account": user, "subject": subject, "reason": reason,
                                "_processedKey": key})
                continue  # Mark after atomic job processing, so failed saves can be retried.
            store.set(key, {"state": "review" if not job else "observed"})
        outcome["state"] = "partial" if fetch_failures else "ok"
        if fetch_failures:
            outcome['reason'] = f'{fetch_failures} messages could not be fetched; retry the check'
    except Exception as exc:
        outcome["reason"] = "Account scan failed: " + type(exc).__name__
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
    return outcome


def check_emails():
    store = LeadStore()
    accounts = get_accounts()
    if not accounts:
        report = {"status": "unconfigured", "updates": [], "accounts": [], "checkedAt": datetime.now(timezone.utc).isoformat()}
        store.set('email:last_report', report)
        return report
    # Read all current application identities, including terminal stages for review-only matching.
    jobs = [j for j in load_jobs() if j.get("status") != "deleted"]
    proposed = []
    outcomes = [check_account_emails(a, jobs, proposed, store) for a in accounts]
    grouped = {}
    for event in proposed:
        grouped.setdefault(event['job_id'], []).append(event)
    ambiguous = set()
    for job_id, events in grouped.items():
        has_transition = any(e['to_status'] != e.get('from_status') for e in events)
        active_events = [e for e in events if e['to_status'] != e.get('from_status')] if has_transition else events
        latest = max(event['eventAt'] for event in active_events)
        if len({event['to_status'] for event in active_events if event['eventAt']==latest}) > 1:
            ambiguous.add(job_id)
            for event in events:
                review_id = event['_processedKey'].rsplit(':',1)[-1]
                store.set('email-review:'+review_id, {'reviewId':review_id,'state':'pending','jobId':job_id,
                          'subject':event.get('subject',''),'reason':'Conflicting outcomes share the latest event timestamp; review required'})
    actionable = []
    for event in proposed:
        if event['job_id'] in ambiguous:
            continue
        events = grouped[event['job_id']]
        if any(e['to_status'] != e.get('from_status') for e in events) and event['to_status'] == event.get('from_status'):
            continue
        actionable.append(event)
    applied = apply_email_updates(actionable, JOBS_FILE) if actionable else []
    for update in proposed:
        store.set(update["_processedKey"], {"state": "applied" if update in applied else "observed_older_or_blocked"})
    with store.connect() as db:
        suggestions = [json.loads(r[0]) for r in db.execute("SELECT payload FROM state WHERE key LIKE 'email-review:%' AND json_extract(payload,'$.state')='pending'")]
    success = sum(o["state"] in {"ok", "partial"} for o in outcomes)
    report = {"status": "ok" if all(o['state']=='ok' for o in outcomes) else "partial" if success else "failed",
              "accounts": outcomes, "accounts_checked": success, "suggestions": suggestions,
              "updates": [{k: v for k, v in u.items() if not k.startswith("_")} for u in applied],
              "checkedAt": datetime.now(timezone.utc).isoformat()}
    store.set("email:last_report", report)
    return report


if __name__ == "__main__":
    print(json.dumps(check_emails(), ensure_ascii=False, indent=2))

