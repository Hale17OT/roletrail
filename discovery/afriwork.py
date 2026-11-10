"""Read the public publisher payload behind observed Afriwork mini-app links."""
import json,re
from urllib.parse import urlsplit,parse_qs
from bs4 import BeautifulSoup
from .policy import now_utc,parse_date

def posting_url(lead):
    for url in [lead.get('url',''),*lead.get('links',[])]:
        p=urlsplit(url)
        if (p.hostname or '').removeprefix('www.')=='afriworket.com' and re.fullmatch(r'/jobs/[a-f0-9-]{36}',p.path):return url
        if p.hostname in {'t.me','telegram.me'} and p.path=='/afriworkapplicantbot/applicantapp':
            guid=parse_qs(p.query).get('startapp',[''])[0]
            if re.fullmatch(r'[a-f0-9-]{36}',guid):return 'https://afriworket.com/jobs/'+guid
    return None

def inspect(lead,fetcher):
    url=posting_url(lead)
    if not url:return None
    response=fetcher.get(url,fresh=True);soup=BeautifulSoup(response['text'],'html.parser')
    node=soup.select_one('#__NUXT_DATA__')
    values=json.loads(node.get_text()) if node else []
    row=next((json.loads(v).get('jobs_by_pk') for v in values if isinstance(v,str) and v.startswith('{"jobs_by_pk":')),None)
    if not row or row.get('id')!=urlsplit(url).path.rsplit('/',1)[-1]:raise ValueError('Exact Afriwork vacancy payload unavailable')
    entity=row.get('entity') or {};desc=BeautifulSoup(row.get('description',''),'html.parser').get_text('\n',strip=True)
    company=entity.get('name') if entity.get('type')=='company' else 'Private Client ('+(entity.get('name') or 'name undisclosed')+')'
    result={**lead,'title':row['title'],'company':company,'description':desc,'url':url,'source_id':row['id'],
        'source':lead.get('source','afriwork'),'official_verified':True,'audit_method':'live_publisher_payload',
        'published_at':row.get('published_at',''),'valid_through':row.get('deadline',''),
        'location':', '.join(filter(None,[(row.get('city') or {}).get('en'),row.get('location')])),
        'workplace':{'REMOTE':'Remote','ONSITE':'On-site','HYBRID':'Hybrid'}.get(row.get('job_site'),''),
        'seniority':row.get('experience_level',''),'publisherEntity':entity.get('name'),
        'publisherEmployerIdentityVerified':entity.get('type')=='company',
        'closed':bool(lead.get('closed') or entity.get('is_banned') or row.get('is_closed') or row.get('status') in {'CLOSED','EXPIRED'} or re.search(r'\b(?:this job is closed|no longer accepting applications|job has expired)\b',soup.get_text(' ',strip=True),re.I))}
    amount=row.get('compensation_amount_cents')
    if amount is not None:result['salary']=f"{amount/100:,.0f} {row.get('compensation_currency') or 'currency unspecified'} / {row.get('compensation_type','period unspecified').lower()}"
    emails=re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',desc)
    result['applicationEmails']=list(dict.fromkeys(emails))
    deadline=parse_date(row.get('deadline'))
    # Current original publisher's explicit email route is actionable without
    # pretending a login-gated form was tested, or testing inbox deliverability.
    if not result['closed'] and deadline and deadline[1]>=now_utc() and emails:
        result['open_check']={'active':True,'identity_matched':True,'source_url':url,'application_url':url,
          'checked_at':now_utc().isoformat(),'evidence':'Exact live publisher vacancy and unexpired deadline; current job-specific email application instructions. Login-only form and inbox delivery not independently tested.'}
    return result
