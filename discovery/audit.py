"""Extract job-specific official evidence; never audit sitewide navigation as a job."""
import json
import html
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .policy import canonical_url, clean, signature, now_utc

ATS_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co", "boards.greenhouse.io", "job-boards.greenhouse.io",
             "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io", "jobs.ashbyhq.com", "jobs.smartrecruiters.com"}
ATS_SUFFIXES = {'myworkdayjobs.com','applytojob.com','recruitee.com','teamtailor.com','pinpointhq.com','breezy.hr','jobs.personio.com','jobs.personio.de'}
ATS_HOSTS.update({'apply.workable.com','jobs.workable.com','careers.smartrecruiters.com'})
AGGREGATORS = {"linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "remotive.com", "weworkremotely.com",
               "workingnomads.com", "japan-dev.com", "tokyodev.com", "t.me", "hahu.jobs", "ethiojobs.net"}
AGGREGATORS.update({'remoteok.com','jobicy.com','himalayas.app','arbeitnow.com','arbeitnow.co.uk','arbeitnow.nl','arbeitnow.ch','hirify.me','muyalogy.com'})


def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def same_domain(a, b):
    return a == b or a.endswith("." + b)


def is_ats_host(hostname):
    return hostname in ATS_HOSTS or any(hostname.endswith('.'+suffix) for suffix in ATS_SUFFIXES)


def job_postings(html):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            kind = node.get("@type", [])
            if "JobPosting" in (kind if isinstance(kind, list) else [kind]):
                found.append(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    return found


def plain(value):
    return BeautifulSoup(html.unescape(clean(value)), "html.parser").get_text("\n", strip=True)


def relative_publication_hint(html):
    """Only a labelled publication age within the job's main content qualifies."""
    node=BeautifulSoup(html,'html.parser').select_one('main')
    if not node:
        return ''
    match=re.search(r'\b(?:posted|published|date posted)\s*:?\s*(\d+\s+(?:hours?|days?)\s+ago)\b',node.get_text(' ',strip=True),re.I)
    return match[1] if match else ''


def location_text(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (location_text(v) for v in value)))
    if isinstance(value, dict):
        if "address" in value:
            return location_text(value["address"])
        return ", ".join(location_text(value[k]) for k in ("name", "addressLocality", "addressRegion", "addressCountry") if value.get(k))
    return clean(value)


def from_jsonld(item, url, source):
    org = item.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    if not isinstance(org, dict):
        org = {"name": org}
    salary = item.get("baseSalary")
    if isinstance(salary, dict):
        salary = json.dumps(salary, ensure_ascii=False)
    return {"title": clean(item.get("title")), "company": clean(org.get("name")),
            "description": plain(item.get("description")), "url": canonical_url(urljoin(url, item.get("url") or url)),
            "source": source, "source_id": clean((item.get("identifier") or {}).get("value")) if isinstance(item.get("identifier"), dict) else "",
            "published_at": clean(item.get("datePosted")), "valid_through": clean(item.get("validThrough")),
            "location": location_text(item.get("jobLocation")), "workplace": clean(item.get("jobLocationType")),
            "applicant_locations": location_text(item.get("applicantLocationRequirements")),
            "salary": clean(salary), "employer_url": org.get("sameAs") or org.get("url") or ""}


def same_page_workplace(lead, document, url):
    """Lever's job header supplies workplace when its JSON-LD omits it."""
    if lead.get('workplace') or host(url) not in {'jobs.lever.co','jobs.eu.lever.co'}:
        return lead
    header=BeautifulSoup(document,'html.parser').select_one('.posting-headline')
    title=header.select_one('h2') if header else None
    mode=header.select_one('.posting-categories .workplaceTypes') if header else None
    if title and mode and signature('',title.get_text(' ',strip=True))==signature('',lead.get('title')):
        value=mode.get_text(' ',strip=True)
        if value.lower() in {'remote','hybrid','on-site','onsite'}:
            return {**lead,'workplace':value,'workplaceEvidence':'Live matching Lever job header'}
    return lead


def trusted(lead, employers):
    hostname = host(lead.get("url", ""))
    if any(same_domain(hostname, a) for a in AGGREGATORS):
        return False
    if is_ats_host(hostname):
        return True
    for employer in employers:
        if signature(employer["name"], "") == signature(lead.get("company"), ""):
            if same_domain(hostname, host(employer["careers_url"])):
                return True
    return False


def inspect_source(lead, fetcher, employers):
    """ATS API responses can be authoritative. Other leads require live job pages."""
    from .afriwork import inspect as inspect_afriwork
    expanded=inspect_afriwork(lead,fetcher)
    if expanded:return expanded
    if lead.get("official_verified") and lead.get("audit_method") == "live_ats_api":
        return lead
    if host(lead.get('url',''))=='himalayas.app':
        # Their documented public API is the supported automated interface;
        # job HTML is protected. Obtain an original employer URL separately.
        return {**lead,'official_verified':False,'audit_error':'Original employer application URL required; Himalayas API is discovery evidence only'}
    response = fetcher.get(lead["url"], fresh=True)
    postings = job_postings(response["text"])
    matches = [p for p in postings if signature("", p.get("title")) == signature("", lead.get("title"))]
    if not matches and len(postings) == 1 and not lead.get("company"):
        matches = postings
    if len(matches) != 1:
        # Never treat a generic listing page, a successful redirect, or whole site body as evidence.
        return {**lead, "official_verified": False, "audit_error": "No unambiguous matching JobPosting"}
    official = same_page_workplace(from_jsonld(matches[0], response["url"], lead["source"]), response['text'], response['url'])
    if not official.get('published_at'):
        official['published_at']=relative_publication_hint(response['text'])
        if official['published_at']:
            official['publication_observed_at']=now_utc().isoformat()
    if lead.get("company") and signature(lead["company"], "") != signature(official["company"], ""):
        return {**lead, "official_verified": False, "audit_error": "Employer identity mismatch"}
    # Do not inherit freshness, location, or sponsorship claims from a discovery board.
    result = {**lead, **official, "discovery_url": lead.get("discovery_url") or lead["url"],
              "audit_method": "live_jobposting_jsonld"}
    result["closed"] = bool(re.search(r"no longer accepting applications|(?:position|vacancy|job) (?:is|has been) (?:closed|filled)|applications (?:are )?closed", plain(response["text"]), re.I))
    result["official_verified"] = trusted(result, employers)
    return result
