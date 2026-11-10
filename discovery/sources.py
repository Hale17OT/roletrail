"""Source adapters return raw leads only. No adapter can write dashboard records."""
import inspect
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from .audit import ATS_HOSTS, from_jsonld, host, is_ats_host, job_postings, plain
from .policy import RELEVANT, SENIOR, clean, now_utc, publication_bounds, signature
from datetime import timedelta


def rotate(values, store, key, count):
    if not values:
        return []
    start = store.get(key, 0) % len(values)
    chosen = [values[(start + i) % len(values)] for i in range(min(count, len(values)))]
    store.set(key, (start + len(chosen)) % len(values))
    return chosen


def ats_board(url):
    p = urlsplit(url)
    parts = p.path.strip("/").split("/")
    if not parts or not parts[0]:
        return None
    h = host(url)
    if h in {"jobs.lever.co", "jobs.eu.lever.co"}:
        return {"kind": "lever", "board": parts[0], "region": "eu" if ".eu." in h else "global"}
    if h in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
        return {"kind": "greenhouse", "board": parts[0], "region": "eu" if ".eu." in h else "global"}
    if h == "jobs.ashbyhq.com":
        return {"kind": "ashby", "board": parts[0]}
    if h == 'apply.workable.com' and parts[0] != 'j':
        return {'kind': 'workable', 'board': parts[0]}
    if h in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"}:
        return {"kind": "smartrecruiters", "board": parts[0]}
    return None


def links(html, url):
    soup = BeautifulSoup(html, "html.parser")
    result = [(urljoin(url, a["href"]), a.get_text(" ", strip=True)) for a in soup.select("a[href]")]
    result += [(urljoin(url, a["src"]), "embedded careers") for a in soup.select("iframe[src]")]
    # Some career pages put the external board URL inside a script config.
    for match in re.findall(r'https://(?:jobs(?:\.eu)?\.lever\.co|(?:job-)?boards(?:\.eu)?\.greenhouse\.io|jobs\.ashbyhq\.com|(?:careers|jobs)\.smartrecruiters\.com)/[\w.-]+', html):
        result.append((match, "embedded ATS"))
    return list(dict.fromkeys(result))


def collect_ats(board, fetcher, hours=168):
    kind, token, company = board["kind"], quote(board["board"], safe=""), board["company"]
    rows = []
    if kind == "greenhouse":
        api_host = "boards-api.eu.greenhouse.io" if board.get("region") == "eu" else "boards-api.greenhouse.io"
        root=f'https://{api_host}/v1/boards/{token}/jobs'
        try:
            rows=fetcher.json(root+'?content=true',fresh=True).get('jobs',[])
        except ValueError as exc:
            if 'response too large' not in str(exc).lower():
                raise
            # Large employers exceed the body limit when every description is
            # requested together. Scan metadata, then fetch matching fresh jobs.
            rows=fetcher.json(root,fresh=True).get('jobs',[])
            selected=[]
            for row in rows:
                bounds=publication_bounds(row.get('first_published'))
                if SENIOR.search(row.get('title','')) or not re.search(r'engineer|develop|programmer|automation|dynamics|power\s',row.get('title',''),re.I):continue
                if bounds and bounds[0]<now_utc()-timedelta(hours=hours) and not board.get('include_older'):continue
                selected.append(fetcher.json(root+'/'+str(row['id'])+'?content=true',fresh=True))
            rows=selected
        for row in rows:
            yield {"title": row["title"], "company": company, "description": plain(row.get("content")),
                   "url": row["absolute_url"], "location": (row.get("location") or {}).get("name", ""),
                   "source": f"greenhouse:{company}", "source_id": str(row["id"]),
                   'seniority':' '.join(str(m.get('value') or '') for m in (row.get('metadata') or []) if re.search(r'level|seniority|standard job title',m.get('name',''),re.I)),
                   # The live API now supplies original publication explicitly.
                   # updated_at remains unsuitable as freshness evidence.
                   "published_at": row.get('first_published',''), "workplace": "",
                   "official_verified": bool(row.get('first_published')),
                   "audit_method": 'live_ats_api' if row.get('first_published') else ''}
    elif kind == "lever":
        prefix = "api.eu.lever.co" if board.get("region") == "eu" else "api.lever.co"
        for offset in range(0, 2000, 100):
            rows = fetcher.json(f"https://{prefix}/v0/postings/{token}?mode=json&skip={offset}&limit=100", fresh=True)
            for row in rows:
                categories = row.get("categories") or {}
                description = row.get("descriptionPlain") or plain(row.get("description"))
                description += "\n" + "\n".join(plain(v.get("text", "") + "\n" + v.get("content", "")) for v in row.get("lists", []))
                description += "\n" + plain(row.get("additional"))
                yield {"title": row["text"], "company": company, "description": description,
                       "url": row["hostedUrl"], "source": f"lever:{company}", "source_id": row["id"],
                       "location": categories.get("location", ""), "workplace": row.get("workplaceType", ""),
                       # createdAt is not evidence of original public release.
                       "published_at": "", "official_verified": False}
            if len(rows) < 100:
                break
        else:
            raise RuntimeError("Lever pagination budget reached; incomplete coverage")
    elif kind == "ashby":
        rows = fetcher.json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true", fresh=True).get("jobs", [])
        for row in rows:
            if not row.get("isListed", True):
                continue
            lead = {"title": row["title"], "company": company, "description": row.get("descriptionPlain") or plain(row.get("descriptionHtml")),
                   "url": row["jobUrl"], "source": f"ashby:{company}", "source_id": row.get("id", row["jobUrl"]),
                   "location": row.get("location", ""), "workplace": row.get("workplaceType", "Remote" if row.get("isRemote") else ""),
                   "published_at": row.get("publishedAt", ""), "salary": (row.get("compensation") or {}).get("scrapeableCompensationSalarySummary", ""),
                   "official_verified": True, "audit_method": "live_ats_api"}
            bounds=publication_bounds(lead['published_at'])
            if not SENIOR.search(lead['title']) and RELEVANT.search(lead['title']) and (board.get('include_older') or not bounds or bounds[1]>=now_utc()-timedelta(hours=hours)):
                # Ashby's board API omits applicantLocationRequirements. Its
                # matching public JobPosting can restrict an otherwise remote job.
                try:
                    page=fetcher.get(lead['url'],fresh=True)
                    matches=[p for p in job_postings(page['text']) if signature('',p.get('title'))==signature('',lead['title'])]
                    if len(matches)!=1:
                        raise ValueError('No unambiguous matching Ashby JobPosting')
                    constraints=from_jsonld(matches[0],page['url'],lead['source'])
                    lead['applicant_locations']=constraints['applicant_locations']
                    lead['valid_through']=constraints['valid_through']
                    lead['constraint_audit_url']=page['url']
                except Exception as exc:
                    lead.update(official_verified=False,audit_method='',audit_error=str(exc))
            yield lead
    elif kind == "smartrecruiters":
        for offset in range(0, 2000, 100):
            result = fetcher.json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset={offset}", fresh=True)
            rows = result.get("content", [])
            for row in rows:
                publication=publication_bounds(row.get('releasedDate'))
                if publication and publication[1]<now_utc()-timedelta(hours=hours) and not board.get('include_older'):
                    # The live listing already establishes this vacancy is old;
                    # preserve the detail-request budget for current releases.
                    continue
                if SENIOR.search(row.get("name", "")) or not (RELEVANT.search(row.get('name','')) or re.search(r"power|dynamics|dataverse|crm|business applications|functional consultant|automation|workflow|application.*analyst", row.get("name", ""), re.I)):
                    continue
                detail_url = f"https://api.smartrecruiters.com/v1/companies/{token}/postings/{quote(str(row['id']), safe='')}"
                detail = fetcher.json(detail_url, fresh=True)
                sections = ((detail.get("jobAd") or {}).get("sections") or {}).values()
                location = detail.get("location") or {}
                yield {"title": detail.get("name", row["name"]), "company": (detail.get("company") or {}).get("name", company),
                       "description": "\n".join(plain(s.get("text")) for s in sections if isinstance(s, dict)),
                       "url": detail.get("applyUrl") or f"https://jobs.smartrecruiters.com/{token}/{row['id']}",
                       "source": f"smartrecruiters:{company}", "source_id": str(row["id"]),
                       "location": ", ".join(str(location[k]) for k in ("city", "region", "country") if location.get(k)),
                       "workplace": detail.get("workPlaceType", "Remote" if location.get("remote") else ""),
                       "published_at": detail.get("releasedDate", ""), "official_verified": True,
                       "audit_method": "live_ats_api", "audit_url": detail_url}
            if offset + len(rows) >= result.get("totalFound", 0) or not rows:
                break
        else:
            raise RuntimeError("SmartRecruiters pagination budget reached; incomplete coverage")
    elif kind == 'workable':
        data = fetcher.json(f'https://www.workable.com/api/accounts/{token}?details=true', fresh=True)
        for row in data.get('jobs', []):
            # published_on is public release; created_at is an internal record date.
            level = clean(row.get('experience'))
            seniority = {'Entry Level': 'Entry-level', 'Associate': 'Associate'}.get(level, '')
            places = row.get('locations') or []
            location = '; '.join(', '.join(filter(None, (p.get('city'), p.get('country')))) for p in places if isinstance(p, dict))
            yield {'title': row['title'], 'company': company, 'description': plain(row.get('description')),
                   'url': row.get('application_url') or row.get('url') or row.get('shortlink'),
                   'source': f'workable:{company}', 'source_id': row.get('shortcode', ''),
                   'location': location or ', '.join(filter(None, (row.get('city'), row.get('country')))),
                   'workplace': 'Remote' if row.get('telecommuting') is True else '',
                   'published_at': row.get('published_on', ''), 'seniority': seniority,
                   'official_verified': True, 'audit_method': 'live_ats_api'}
    else:
        raise ValueError(f"Unsupported ATS {kind}")


def discover_employer(employer, fetcher, store):
    """Detect linked boards from the employer's own careers page; persist provenance."""
    key = "boards:" + employer["name"]
    cached = store.get(key, {})
    if cached and cached.get("boards") and (now_utc().timestamp() - cached.get("checked_at", 0)) < 86400:
        return cached["boards"], []
    try:
        response = fetcher.get(employer["careers_url"])
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 404:
            raise
        # Recover moved career URLs through the same employer's public home page.
        root = urlsplit(employer["careers_url"])
        home = fetcher.get(f"{root.scheme}://{root.netloc}/")
        career_links = [(u, t) for u, t in links(home["text"], home["url"])
                        if re.search(r"career|join us|vacanc|work with us", t, re.I) and u != employer["careers_url"]]
        if not career_links:
            raise
        response = fetcher.get(career_links[0][0])
    boards, leads = [], []
    pages = [response]
    # Follow relevant listing pages rather than an arbitrary navigation link.
    candidates = [(u, t) for u, t in links(response["text"], response["url"])
                  if host(u) == host(response["url"]) and re.search(r"vacanc|open positions|search jobs|job opportunities|all jobs", t + " " + u, re.I)]
    candidates.sort(key=lambda pair: (0 if re.search(r"all vacancies|all jobs|search jobs|open positions", pair[1], re.I) else 1, len(pair[0])))
    for url, _ in candidates[:2]:
        if url != response["url"]:
            pages.append(fetcher.get(url))
    for page in pages:
        for url, _ in [(page["url"], "")] + links(page["text"], page["url"]):
            board = ats_board(url)
            if board and board not in [b["adapter"] for b in boards]:
                boards.append({"adapter": board, "discovered_from": page["url"], "board_url": url})
        for posting in job_postings(page["text"]):
            leads.append(from_jsonld(posting, page["url"], employer["name"]))
        # Employer-owned custom career sites are useful even without a supported ATS.
        soup = BeautifulSoup(page["text"], "html.parser")
        for anchor in soup.select("a[href]"):
            url = urljoin(page["url"], anchor["href"])
            title = anchor.get_text(" ", strip=True)
            if not RELEVANT.search(title):
                parent = anchor.find_parent(["article", "li"])
                if parent:
                    title = parent.get_text(" ", strip=True)
            if (RELEVANT.search(title) or RELEVANT.search(url.replace("-", " "))) and not SENIOR.search(title) and re.search(r"jobs?/|vacanc|position|careers/|/p/", url, re.I):
                leads.append({"title": title if RELEVANT.search(title) else "", "company": "", "url": url,
                              "description": "", "source": employer["name"], "published_at": ""})
    store.set(key, {"checked_at": now_utc().timestamp(), "boards": boards})
    return boards, leads


def collect_jobspy(config, store, health, queries=None, sites=None, countries=None, results=None, hours=168):
    import jobspy
    from .jobspy_compat import install_zip_timestamp_compat
    install_zip_timestamp_compat()
    cfg = config["jobspy"]
    terms = queries or rotate(config["queries"], store, "query_cursor", cfg["max_queries_per_run"])
    regions = countries or rotate(cfg["regions"], store, "region_cursor", cfg["regions_per_run"])
    chosen_sites = sites or cfg["sites"]
    mode = rotate(cfg["modes"], store, "mode_cursor", 1)[0]
    supports_description = "fetch_description" in inspect.signature(jobspy.scrape_jobs).parameters
    for site in chosen_sites:
        if site == "google":
            health("google", "unavailable", reason=cfg["google_note"])
            continue
        # LinkedIn and ZipRecruiter do not use country_indeed. Do not repeat their queries per country.
        targets = regions if site in {"indeed", "glassdoor"} else ["USA" if site == "zip_recruiter" else "global"]
        for region in targets:
            if mode == "addis" and site != "linkedin":
                health(f"{site}:{region}:addis", "unsupported", reason="No Ethiopia portal; local discovery uses LinkedIn, Ethiopian web boards and Telegram")
                continue
            if site == "glassdoor" and region not in cfg["glassdoor_regions"]:
                health(f"{site}:{region}", "unsupported", reason="Glassdoor portal not supported")
                continue
            for term in terms:
                label = f"{site}:{region}:{mode}:{term}"
                params = dict(site_name=[site], search_term=term + (" visa sponsorship" if mode == "sponsored" else ""),
                              location="Addis Ababa, Ethiopia" if mode == "addis" else "Remote" if mode == "remote" else region if region != "global" else "",
                              results_wanted=results or cfg["results_per_site"], hours_old=hours,
                              description_format="html")
                if site in {"indeed", "glassdoor"}:
                    params["country_indeed"] = region
                if mode == "remote":
                    params["is_remote"] = True
                if supports_description:
                    params["fetch_description"] = True
                elif site == "linkedin":
                    params["linkedin_fetch_description"] = True
                try:
                    frame = jobspy.scrape_jobs(**params)
                    if frame is None:
                        health(label, "error", reason="Scraper returned no result object")
                        continue
                    errors = frame.attrs.get("errors", {})
                    warnings = frame.attrs.get("warnings", {})
                    health(label, "error" if errors else "ok", fetched=len(frame), errors=errors, warnings=warnings)
                    for row in frame.to_dict("records"):
                        yield {"title": clean(row.get("title")), "company": clean(row.get("company")),
                               "description": plain(row.get("description")), "source": label,
                               "url": clean(row.get("job_url_direct")) or clean(row.get("job_url")),
                               "discovery_url": clean(row.get("job_url")), "location": clean(row.get("location")),
                               "workplace": "Remote" if row.get("is_remote") is True else "",
                               "published_at": "", "board_date": clean(row.get("date_posted"))}
                except Exception as exc:
                    health(label, "error", reason=str(exc))


def collect_feed(feed, fetcher):
    if feed['kind'] in {'remoteok','jobicy','arbeitnow','himalayas_search'}:
        yield from collect_json_feed(feed,fetcher)
        return
    response = fetcher.get(feed["url"], ttl=feed.get("interval_hours", 6) * 3600)
    if feed["kind"] == "remotive":
        for row in json.loads(response["text"]).get("jobs", []):
            yield {"title": row["title"], "company": row["company_name"], "description": plain(row.get("description")),
                   "url": row["url"], "discovery_url": row["url"], "source": "Remotive", "attribution_url": row["url"],
                   "location": row.get("candidate_required_location", ""), "workplace": "Remote",
                   "published_at": "", "board_date": row.get("publication_date", "")}
    else:
        for item in ET.fromstring(response["text"]).findall(".//item"):
            title = item.findtext("title", "")
            company, _, title = title.partition(": ")
            yield {"title": title or company, "company": company if title else "", "description": plain(item.findtext("description", "")),
                   "url": item.findtext("link", ""), "source": feed["name"], "published_at": "", "workplace": "Remote"}


def collect_json_feed(feed,fetcher):
    """Discovery evidence only: board dates never become employer publication proof."""
    url=feed['url'];seen_cursors=set();seen_jobs=set();kind=feed['kind']
    maximum=min(20,max(1,feed.get('max_pages',1)))
    for page in range(1,maximum+1):
        data=fetcher.json(url,ttl=feed.get('interval_hours',24)*3600)
        rows=data if isinstance(data,list) else data.get('data',[]) if kind=='arbeitnow' else data.get('jobs',[])
        for row in rows:
            if kind=='remoteok':
                title=row.get('position','');company=row.get('company','');link=row.get('apply_url') or row.get('url','');desc=row.get('description','');location=row.get('location','');date=row.get('date','')
            elif kind=='jobicy':
                title=row.get('jobTitle','');company=row.get('companyName','');link=row.get('url','');desc=row.get('jobDescription','');location=row.get('jobGeo','');date=row.get('pubDate','')
            elif kind=='himalayas_search':
                title=row.get('title','');company=row.get('companyName','');link=row.get('applicationLink') or row.get('guid','');desc=row.get('description','');location='; '.join(row.get('locationRestrictions') or []);date=str(row.get('pubDate') or '')
            else:
                title=row.get('title','');company=row.get('company_name','');link=row.get('url','');desc=row.get('description','');location=row.get('location','');date=str(row.get('created_at') or '')
            if not title or not link or link in seen_jobs:continue
            seen_jobs.add(link)
            yield {'title':clean(title),'company':clean(company),'url':link,'description':plain(desc),
                   'source':feed['name'],'discovery_url':row.get('guid') or row.get('url') or link,
                   'attribution_url':row.get('guid') or row.get('url') or link,'location':location,
                   'published_at':'','board_date':date,'workplace':'Remote' if kind!='arbeitnow' or row.get('remote') is True else ''}
        if kind=='remoteok':return
        if kind=='himalayas_search':
            if len(rows)<data.get('limit',20) or page*data.get('limit',20)>=data.get('totalCount',0):return
            separator='&' if '?' in feed['url'] else '?'
            url=feed['url']+separator+urlencode({'page':page+1})
            continue
        cursor=data.get('nextCursor')
        if kind=='arbeitnow':
            next_url=(data.get('links') or {}).get('next')
            if not next_url:return
            if host(next_url)!=host(feed['url']):raise ValueError('Unexpected feed pagination host')
            cursor=next_url;url=next_url
        else:
            if not cursor:return
            url=feed['url']+('&' if '?' in feed['url'] else '?')+urlencode({'cursor':cursor})
        if cursor in seen_cursors:raise RuntimeError('Feed repeated pagination cursor; incomplete coverage')
        seen_cursors.add(cursor)
    raise RuntimeError('Feed pagination budget reached; incomplete coverage')


def collect_page(page, fetcher):
    response = fetcher.get(page["url"])
    postings = job_postings(response["text"])
    for posting in postings:
        yield from_jsonld(posting, response["url"], page["name"])
    # Ethiojobs server-rendered Next data contains full results even when DOM anchors are absent.
    soup = BeautifulSoup(response["text"], "html.parser")
    next_data = soup.select_one("script#__NEXT_DATA__")
    rows = []
    if next_data:
        data = json.loads(next_data.string or next_data.get_text())
        rows = data.get("props", {}).get("pageProps", {}).get("jobs", {}).get("data", [])
        for row in rows:
            yield {"title": row.get("title", ""), "company": (row.get("company") or {}).get("name", ""),
                   "description": plain(row.get("description", "")), "location": row.get("state", ""),
                   "url": urljoin(response["url"], "/job/" + row["slug"]), "source": page["name"],
                   "published_at": "", "board_date": row.get("date_published", "")}
    candidate_count = 0
    for url, text in links(response["text"], response["url"]):
        if re.search(page["link_pattern"], url) and RELEVANT.search(text):
            candidate_count += 1
            yield {"title": text, "company": "", "description": "", "url": url,
                   "source": page["name"], "published_at": ""}
    if not postings and not rows and not candidate_count:
        raise RuntimeError("No structured vacancies or relevant links extracted; source needs an adapter or has no matching visible listings")


def resolve_discovery(lead, fetcher):
    """Follow outbound employer/ATS links, retaining aggregator attribution."""
    from .afriwork import posting_url
    afriwork=posting_url(lead)
    if afriwork:return {**lead,'url':afriwork,'discovery_url':lead.get('discovery_url') or lead['url']}
    if is_ats_host(host(lead['url'])):
        return lead
    if host(lead['url'])=='himalayas.app':
        candidates=list(dict.fromkeys(u for u,_ in links(lead.get('description',''),lead['url']) if is_ats_host(host(u))))
        return {**lead,'url':candidates[0],'discovery_url':lead.get('discovery_url') or lead['url']} if len(candidates)==1 else lead
    from .audit import AGGREGATORS, same_domain
    if not any(same_domain(host(lead["url"]), a) for a in AGGREGATORS):
        return lead
    response = fetcher.get(lead["url"])
    candidates = [(url, text) for url, text in links(response["text"], response["url"])
                  if is_ats_host(host(url)) or (re.search(r"apply|application|employer", text, re.I)
                  and host(url) and not any(same_domain(host(url), a) for a in AGGREGATORS))]
    unique = list(dict.fromkeys(url for url, _ in candidates))
    if len(unique) == 1:
        return {**lead, "url": unique[0], "discovery_url": lead.get("discovery_url") or lead["url"]}
    if not unique:
        # Some boards expose the official destination through a public GET
        # redirect behind their Apply Now link. Follow the observed CTA once;
        # never submit a form or guess a redirect endpoint.
        apply_links=list(dict.fromkeys(url for url,text in links(response['text'],response['url'])
            if host(url)==host(response['url']) and re.fullmatch(r'apply(?: now| for this (?:job|position))?',text.strip(),re.I)
            and re.search(r'/(?:apply|application)(?:[/?]|$)',url)))
        if len(apply_links)==1:
            destination=fetcher.get(apply_links[0])
            target=destination['url']
            if host(target) and not any(same_domain(host(target),a) for a in AGGREGATORS):
                return {**lead,'url':target,'discovery_url':lead.get('discovery_url') or lead['url']}
    return lead
