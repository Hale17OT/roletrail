"""Public LinkedIn hiring-post discovery; never messages or logged-in scraping."""
import re,json
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from urllib.parse import urlencode,urlsplit
from bs4 import BeautifulSoup
from .policy import canonical_url,RELEVANT,now_utc

def linkedin_host(url):
    host=urlsplit(url).hostname or ''
    return host=='linkedin.com' or host.endswith('.linkedin.com')

def post_time(url):
    match=re.search(r'(?:activity-|/activity/)(\d{18,20})',url)
    if not match:return ''
    try:return datetime.fromtimestamp((int(match[1])>>22)/1000,timezone.utc).isoformat()
    except (ValueError,OverflowError,OSError):return ''

def read_post(url,fetcher):
    response=fetcher.get(url,fresh=True);s=BeautifulSoup(response['text'],'html.parser')
    article=s.select_one('[data-test-id="main-feed-activity-card__commentary"], .main-feed-activity-card__commentary, .feed-shared-update-v2__description, article')
    meta=s.select_one('meta[property="og:description"]')
    text=article.get_text('\n',strip=True) if article else meta.get('content','') if meta else ''
    if not text or not RELEVANT.search(text):raise ValueError('Public job-specific post unavailable or not technically relevant; sign-in/access controls were not bypassed')
    author=s.select_one('[data-tracking-control-name="public_post_feed-actor-name"], .base-main-feed-card__actor-name')
    name=author.get_text(' ',strip=True) if author else ''
    heading=s.select_one('title')
    return {'title':(heading.get_text(' ',strip=True) if heading else text.splitlines()[0])[:180],
      'company':'','description':text,'url':response['url'],'source':'people:linkedin','official_verified':False,
      'published_at':'','board_date':post_time(url),'postPublishedAt':post_time(url),'postAuthor':name,
      'postPublicationBasis':'Activity-ID timestamp inference; verify against the live post time before recommending. Never vacancy publication proof.',
      'discovery_url':url,'postAuthority':'unverified_individual_or_recruiter',
      'applicationLinks':list(dict.fromkeys(canonical_url(a['href']) for a in s.select('a[href]') if a.get_text(' ',strip=True) and re.search(r'apply|application|careers|jobs',a.get_text(' ',strip=True),re.I) and canonical_url(a['href']))),
      'audit_note':'Post age is not employer vacancy age. Verify employer/client, actual role, geography, source authority and current application route. No outbound contact.'}

def collect(config,fetcher,store,health,queries=None):
    from .sources import rotate
    cfg=config.get('people_posts',{})
    if not cfg.get('enabled'):return
    urls=list(cfg.get('posts',[]));terms=rotate(cfg.get('queries',[]),store,'people_query_cursor',cfg.get('max_queries_per_run',3))
    if queries:terms=['site:linkedin.com/posts/ "hiring" '+q for q in queries[:3]]
    for term in terms:
        label='people:search:'+term
        try:
            response=fetcher.get('https://www.bing.com/search?'+urlencode({'q':term,'format':'rss'}),ttl=21600)
            rows=ET.fromstring(response['text']).findall('.//item')
            found=[x.findtext('link','') for x in rows if linkedin_host(x.findtext('link','')) and '/posts/' in x.findtext('link','')][:cfg.get('max_results_per_query',10)]
            urls.extend(found);health(label,'ok' if found else 'needs_review',fetched=len(found),reason='Public search-index results; partial coverage' if found else 'No public LinkedIn post links returned; not evidence of no hiring posts')
        except Exception as exc:health(label,'unavailable',reason=type(exc).__name__+': '+str(exc)[:180])
    for url in list(dict.fromkeys(urls))[:cfg.get('max_posts_per_run',12)]:
        if not linkedin_host(url) or '/posts/' not in url:continue
        try:yield read_post(url,fetcher)
        except Exception as exc:health('people:post:'+url,'unavailable',reason=type(exc).__name__+': '+str(exc)[:180])
