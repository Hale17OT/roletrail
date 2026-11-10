"""Source-level reviews with explicit exclusions for unproved age or experience."""
import copy
import hashlib
import html
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from .audit import from_jsonld, job_postings, plain, relative_publication_hint, same_page_workplace
from .http import Fetcher
from .policy import EARLY_LEVEL, POLICY_VERSION, PL200_TRIALS, SKILLS_TRIAL, ALL_TRACKS_TRIAL, RECURRING_PROFILE, canonical_url, evaluate, now_utc, parse_date, signature
from .store import ROOT, LeadStore, blocked, read_jobs, update_jobs

RULES={1:'Candidate qualifications and junior / mid-level experience',2:'Software development or Power Platform relevance',3:'Location / residency / visa sponsorship',
       5:'Confirmed maximum age of seven days and active',6:'Complete and accurate application record',7:'Pipeline / deletion blacklist',8:'Live official-source evidence'}
FAILURES={'seniority':1,'required_doctorate_not_met':1,'experience_above_three_years':1,'experience_above_five_years':1,'experience_not_verified':1,'publication_date_unknown':5,'future_publication_date':5,'no_relevant_technical_alignment':2,
          'residency_citizenship_or_clearance_restriction':3,'domestic_only_eligibility':3,'mandatory_foreign_presence':3,
          'outside_freshness_window':5,'expired':5,'closed':5,
          'pl200_not_explicitly_required':2,'pl200_not_required_or_preferred':2,'cross_border_remote_not_established':3,'no_business_application_alignment':2}
PENDING={'publication_date_unknown':5,'future_publication_date':5,'experience_not_verified':1,
         'location_or_sponsorship_not_verified':3,'applicant_location_not_verified_for_ethiopia':3,
         'official_source_not_verified':8,'missing_employer_title_or_description':8,'sponsorship_destination_unknown':3,
         'responsibility_review_required':1,'open_status_not_verified':5}
MESSAGES={'seniority':'The source title is above the permitted seniority.',
 'pl200_not_explicitly_required':'The trial requires PL-200 as a mandatory credential or accepted mandatory-certification alternative; a preferred or incidental mention does not qualify.',
 'pl200_not_required_or_preferred':'The trial requires PL-200 to be required or preferred; an incidental mention is insufficient.',
 'cross_border_remote_not_established':'The selected trial does not establish a compatible remote arrangement. Actual remote-work evidence is required; explicit geographic or authorization exclusions cannot be waived by inference.',
 'required_doctorate_not_met':'The official posting requires a completed or in-progress PhD; the candidate profile lists a BSc.',
 'experience_above_three_years':'The experience requirement exceeds 3 years without an explicit eligible junior/mid-level label.',
 'experience_above_five_years':'The posting exceeds 5 years of experience, beyond the slight junior/mid-level flexibility.',
 'no_relevant_technical_alignment':'The source does not establish software engineering/development or Power Platform/Dynamics responsibilities.',
 'no_business_application_alignment':'The skills-first trial requires Power Platform, CRM, automation or related business-application responsibilities.',
 'residency_citizenship_or_clearance_restriction':'The posting imposes residency, right-to-work or clearance restrictions.',
 'domestic_only_eligibility':'The role is restricted to applicants in a foreign domestic territory.',
 'mandatory_foreign_presence':'Mandatory foreign office attendance is not covered by verified visa sponsorship.',
 'outside_freshness_window':'The maximum possible publication age exceeds seven days (168 hours).',
 'closed':'The exact source vacancy is closed or filled.','expired':'The application deadline has passed.',
 'publication_date_unknown':'Could not establish that this posting is at most seven days old; excluded under the requested evidence rule.',
 'future_publication_date':'The reported date is in the future, so a valid age within seven days cannot be established.',
 'experience_not_verified':'No junior/mid-level label or eligible numeric/no-experience requirement could be established; excluded under the requested experience rule.',
 'location_or_sponsorship_not_verified':'Eligibility from Ethiopia or explicit work-visa sponsorship is not established.',
 'applicant_location_not_verified_for_ethiopia':'The applicant-location requirements do not establish eligibility from Ethiopia.',
 'official_source_not_verified':'A complete official job-specific posting could not be verified.',
 'missing_employer_title_or_description':'The source does not expose a complete, identifiable job posting.'}

def fingerprint(job):
    return hashlib.sha256(json.dumps(job,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]

def readable(value):
    return plain(html.unescape(value or ''))

def source_lead(source, fetcher):
    """Only configured, inspected employer/ATS URLs can claim official authority."""
    r=fetcher.get(source['url'],fresh=True)
    facts={'url':r['url'],'requestedUrl':source['url'],'httpStatus':r['status'],'checkedAt':now_utc().isoformat(),
           'authority':source.get('authority','listing'),'format':source.get('format','jsonld')}
    facts['alignmentReviewRequired']=source.get('alignmentReviewRequired',False)
    kind=source.get('format','jsonld');data=None
    if kind in {'ashby','lever','greenhouse','workday','workable','smartrecruiters'}:
        data=json.loads(r['text'])
    if kind=='smartrecruiters':
        if str(data.get('id'))!=str(source.get('postingId')):
            return None,{**facts,'error':'Exact SmartRecruiters posting ID not found'}
        row=data
        loc=data.get('location') or {}
        lead={'title':' '.join(data['name'].split()),'company':source['company'],
          'description':'\n'.join(readable(v.get('text')) for v in (data.get('jobAd',{}).get('sections') or {}).values()),
          'url':data['applyUrl'],'published_at':data.get('releasedDate',''),'source_id':str(data['id']),
          'location':loc.get('fullLocation') or ', '.join(filter(None,[loc.get('city'),loc.get('country')])),
          'workplace':'Remote' if loc.get('remote') else 'Hybrid' if loc.get('hybrid') else 'On-site'}
    elif kind=='afriwork':
        from .afriwork import inspect
        lead=inspect({'url':source['url']},fetcher)
        if not lead:return None,{**facts,'error':'Exact Afriwork vacancy unavailable'}
        if source.get('company') and source['company'].casefold() in lead['description'].casefold():
            lead['company']=source['company']
    elif kind=='ashby':
        rows=[j for j in data.get('jobs',[]) if j.get('id')==source.get('postingId') or
              (not source.get('postingId') and signature('',j.get('title'))==signature('',source.get('title')))]
        if len(rows)!=1:
            return None,{**facts,'error':'Exact vacancy not found unambiguously on the employer board'}
        row=rows[0]
        lead={'title':row['title'],'company':source['company'],'description':row.get('descriptionPlain') or readable(row.get('descriptionHtml')),
              'url':row['jobUrl'],'location':row.get('location',''),'workplace':row.get('workplaceType',''),
              'published_at':row.get('publishedAt',''),'source_id':row.get('id','')}
    elif kind=='workable':
        rows=[j for j in data.get('jobs',[]) if j.get('shortcode')==source.get('postingId')]
        if not rows or len({signature('',j.get('title')) for j in rows})!=1:
            return None,{**facts,'error':'Exact vacancy not found unambiguously on the employer board'}
        row=rows[0]
        location='; '.join(dict.fromkeys(', '.join(filter(None,(j.get('city'),j.get('country')))) for j in rows))
        lead={'title':row['title'],'company':source['company'],'description':readable(row.get('description')),
              'url':source.get('postingUrl') or row.get('url') or row.get('application_url'),
              'published_at':row.get('published_on',''),'source_id':row.get('shortcode',''),
              'location':location,'workplace':'Remote' if row.get('telecommuting') is True else '',
              'seniority':{'Entry Level':'Entry-level','Associate':'Associate'}.get(row.get('experience'),'')}
    elif kind=='lever':
        lead={'title':data['text'],'company':source['company'],'description':'\n'.join(filter(None,[data.get('descriptionPlain'),
            *[readable(x.get('content')) for x in data.get('lists',[])],data.get('additionalPlain')])),
            'url':data.get('hostedUrl') or source.get('postingUrl') or source['url'],
            'location':(data.get('categories') or {}).get('location',''),'workplace':data.get('workplaceType',''),
            'published_at':'','source_id':data.get('id','')}
        # Lever record creation is not a verified publication/republication date.
        facts['recordCreatedAt']=data.get('createdAt')
    elif kind=='greenhouse':
        lead={'title':data['title'],'company':source['company'],'description':readable(data.get('content')),
              'url':data.get('absolute_url',source['url']),'location':(data.get('location') or {}).get('name',''),
              'workplace':source.get('workplace',''),'published_at':data.get('first_published',''),'source_id':str(data.get('id','')),
              'seniority':' '.join(str(m.get('value') or '') for m in (data.get('metadata') or []) if re.search(r'level|seniority|standard job title',m.get('name',''),re.I))}
    elif kind=='workday':
        row=data.get('jobPostingInfo') or {}
        description=readable(row.get('jobDescription'))
        lead={'title':row.get('title',''),'company':source['company'],'description':description,
              'url':source.get('postingUrl') or row.get('externalUrl') or source['url'],
              'location':'; '.join([row.get('location',''),*row.get('additionalLocations',[])]),
              'workplace':'Remote' if re.search(r'\bremote\b',row.get('location',''),re.I) else '',
              'published_at':row.get('startDate',''),'source_id':row.get('jobReqId','')}
    elif kind=='ethiojobs':
        soup=BeautifulSoup(r['text'],'html.parser')
        script=soup.select_one('#__NEXT_DATA__')
        page=json.loads(script.string or script.get_text())['props']['pageProps'] if script else {}
        row=page.get('data') or {}
        schema=page.get('jobSchema') or {}
        if not row.get('title') or signature('',row['title'])!=signature('',schema.get('title')):
            return None,{**facts,'error':'No matching job-specific publisher data'}
        lead=from_jsonld(schema,r['url'],'existing_opportunity_review')
        lead['description']='\n\n'.join(readable(row.get(k)) for k in ['description','requirement','how_to_apply'] if row.get(k))
        lead['seniority']=(row.get('career_level_name') or {}).get('label','')
        facts['employerAnnouncement']=source.get('employerAnnouncement','')
        facts['applicationUrl']=row.get('career_page_link') or ''
        if facts['applicationUrl']:
            try:
                facts['applicationHttpStatus']=fetcher.get(facts['applicationUrl'],fresh=True)['status']
            except Exception as exc:
                facts['applicationHttpStatus']=getattr(getattr(exc,'response',None),'status_code',None)
                facts['applicationError']=type(exc).__name__
            proof=source.get('applicationBrowserVerification') or {}
            checked=parse_date(proof.get('checkedAt'))
            # Signed-in browser inspection can verify an employer-linked form
            # that properly requires authentication. Keep its actual HTTP 401.
            if (facts.get('applicationHttpStatus')==401 and proof.get('state')=='accessible' and
                proof.get('method')=='signed_in_browser' and proof.get('formTitle') and proof.get('evidence') and
                canonical_url(proof.get('url',''))==canonical_url(facts['applicationUrl']) and
                checked and 0<=(now_utc()-checked[0]).total_seconds()<=86400):
                facts['applicationBrowserVerified']=True
                facts['applicationBrowserVerification']=proof
    elif kind=='dom':
        soup=BeautifulSoup(r['text'],'html.parser')
        node=soup.select_one(source.get('selector','main'))
        if not node:
            return None,{**facts,'error':'Job-specific DOM content unavailable'}
        lead={'title':source['title'],'company':source['company'],'description':node.get_text('\n',strip=True),
              'url':source.get('postingUrl') or r['url'],'published_at':source.get('published_at',''),
              'location':source.get('location',''),'workplace':source.get('workplace','')}
        # An inspected employer post can label publication as "4d" rather
        # than exposing a JobPosting date. Read only the configured post time.
        if source.get('publicationSelector'):
            stamp=soup.select_one(source['publicationSelector'])
            label=stamp.get_text(' ',strip=True) if stamp else ''
            age=re.fullmatch(r'(\d+)\s*([dh])',label,re.I)
            if age:
                lead['published_at']=age[1]+(' days ago' if age[2].lower()=='d' else ' hours ago')
                lead['publication_observed_at']=facts['checkedAt']
                facts['publicationBasis']='configured employer-post publication label; maximum-age bound used'
    else:
        rows=job_postings(r['text'])
        if len(rows)!=1:
            return None,{**facts,'error':'No unambiguous job-specific structured posting'}
        lead=same_page_workplace(from_jsonld(rows[0],r['url'],'existing_opportunity_review'),r['text'],r['url'])
        lead['title']=html.unescape(lead['title']);lead['description']=readable(lead['description'])
    lead.update(source='existing_opportunity_review',official_verified=source.get('authority')=='official',audit_method='live_existing_source_review')
    inference=source.get('sponsorshipInference') or {}
    if inference.get('evidence') and inference['evidence'].casefold() in lead['description'].casefold():
        lead['sponsorship_inference']={**inference,'source_url':lead['url'],'checked_at':facts['checkedAt']}
    if source.get('verifyActiveApplication') and kind in {'workable','ashby','smartrecruiters'} and lead['official_verified']:
        # Board membership is checked above against the exact posting ID.
        # A generic HTTP 200 page without the matching job title is insufficient.
        application_url=row.get('application_url') or row.get('applyUrl') or source.get('applicationUrl')
        if application_url:
            try:
                application=fetcher.get(application_url,fresh=True)
                application_text=' '.join(BeautifulSoup(application['text'],'html.parser').get_text(' ',strip=True).split())
                identity_matches=' '.join(lead['title'].split()).casefold() in application_text.casefold()
                closed=bool(re.search(r'no longer accepting applications|job (?:has been|is) closed|position (?:has been|is) filled',application_text,re.I))
                if application['status']==200 and identity_matches and not closed:
                    lead['open_check']={'active':True,'identity_matched':True,'source_url':lead['url'],
                        'application_url':application_url,'checked_at':facts['checkedAt'],
                        'evidence':'Exact vacancy currently listed on live employer ATS board; matching job title verified at its API-linked application destination (HTTP 200).'}
                    facts['activeApplicationVerified']=True
                if closed:lead['closed']=True
            except Exception as exc:
                facts['activeApplicationError']=type(exc).__name__
                facts['applicationHttpStatus']=getattr(getattr(exc,'response',None),'status_code',None)
        proof=source.get('applicationBrowserVerification') or {}
        checked=parse_date(proof.get('checkedAt'))
        if (kind=='smartrecruiters' and not lead.get('open_check') and proof.get('method')=='live_browser' and
            proof.get('state')=='accessible' and proof.get('postingId')==lead.get('source_id') and
            signature('',proof.get('formTitle'))==signature('',lead['title']) and proof.get('evidence') and
            canonical_url(proof.get('url',''))==canonical_url(lead['url']) and checked and
            0<=(now_utc()-checked[0]).total_seconds()<=86400 and not lead.get('closed')):
            lead['open_check']={'active':True,'identity_matched':True,'source_url':lead['url'],
              'application_url':lead['url'],'checked_at':proof['checkedAt'],'evidence':proof['evidence']}
            facts['activeApplicationVerified']=True
            facts['applicationBrowserVerification']=proof
    if not lead.get('published_at'):
        # A source's labelled publication-day age can establish an upper bound.
        # Creation timestamps, edit timestamps and Telegram reposts cannot.
        posting_html=r['text']
        if kind=='lever' and data.get('hostedUrl'):
            try:
                posting_html=fetcher.get(data['hostedUrl'],fresh=True)['text']
            except Exception as exc:
                facts['publicationLookupError']=type(exc).__name__
        hint=relative_publication_hint(posting_html)
        if hint:
            lead['published_at']=hint
            lead['publication_observed_at']=facts['checkedAt']
            facts['publicationBasis']='source-labelled relative publication age; maximum-age bound used'
    if source.get('workplace'):lead['workplace']=source['workplace']
    facts.update(sourceTitle=lead.get('title'),sourceCompany=lead.get('company'),publishedAt=lead.get('published_at'),
                 location=lead.get('location'),workplace=lead.get('workplace'),applicantLocations=lead.get('applicant_locations',''),postingUrl=lead['url'])
    # Opt-in, inspected application controls. HTTP 200 alone never proves open.
    if source.get('openSelector') and source.get('applicationSelector'):
        soup=BeautifulSoup(r['text'],'html.parser')
        opening=soup.select_one(source['openSelector'])
        application=soup.select_one(source['applicationSelector'])
        title_matches=bool(lead.get('title') and lead['title'].casefold() in (opening.get_text(' ',strip=True).casefold() if opening else ''))
        from urllib.parse import urljoin
        application_url=urljoin(r['url'],application.get('href','')) if application else ''
        if r['status']==200 and title_matches and application_url.startswith(('https://','http://')):
            try:
                destination=fetcher.get(application_url,fresh=True)
                if destination['status']==200:
                    lead['open_check']={'active':True,'identity_matched':True,'source_url':lead['url'],
                        'application_url':application_url,'checked_at':facts['checkedAt'],
                        'evidence':opening.get_text(' ',strip=True)[:1000]+'; inspected application link '+application_url}
            except Exception as exc:
                facts['openCheckError']=type(exc).__name__
    # Some employers embed the exact vacancy's application in the same page.
    # Require the configured title and a usable resume control, not just HTTP 200.
    if source.get('applicationFormSelector') and lead['official_verified']:
        soup=BeautifulSoup(r['text'],'html.parser')
        heading=soup.select_one(source.get('openSelector','h1'))
        form=soup.select_one(source['applicationFormSelector'])
        resume=form.select_one('input[type="file"]:not([disabled])') if form else None
        if (r['status']==200 and heading and
            signature('',heading.get_text(' ',strip=True))==signature('',lead['title']) and resume and
            not re.search(r'no longer accepting applications|position (?:has been|is) filled',lead['description'],re.I)):
            lead['open_check']={'active':True,'identity_matched':True,'source_url':lead['url'],
                'application_url':lead['url'],'checked_at':facts['checkedAt'],
                'evidence':'Exact vacancy title and embedded application form with enabled CV upload inspected on the current official page.'}
            facts['activeApplicationVerified']=True
    return lead,facts

def finding(rule,code,detail=None,source=None):
    return {'rule':rule,'name':RULES[rule],'code':code,'detail':detail or MESSAGES.get(code,code.replace('_',' ')),
            **({'sourceUrl':source} if source else {})}

def assess(lead,facts):
    audit=evaluate(lead);failed=[];pending=[]
    official=facts['authority']=='official'
    if facts.get('format')=='ethiojobs' and facts.get('applicationHttpStatus')!=200 and not facts.get('applicationBrowserVerified'):
        pending.append(finding(6,'application_destination_unverified','The employer-linked application form could not be inspected publicly (HTTP '+str(facts.get('applicationHttpStatus'))+'); source age and experience are established, but the application route needs checking.',facts.get('applicationUrl')))
        audit['state']='needs_evidence'
        audit['reasons'].append('application_destination_unverified')
    for code in audit['reasons']:
        if code=='no_relevant_technical_alignment' and facts.get('alignmentReviewRequired'):
            pending.append(finding(2,'alignment_requires_confirmation','Software development or business-application responsibilities need employer confirmation.',facts['url']))
            continue
        if code in FAILURES and (official or code in {'outside_freshness_window','closed','expired','publication_date_unknown','future_publication_date','experience_not_verified'}):
            detail=MESSAGES[code]
            if lead.get('search_profile') in PL200_TRIALS and code in {'outside_freshness_window','publication_date_unknown','future_publication_date'}:
                detail=detail.replace('seven days (168 hours)','twelve days (288 hours)').replace('seven days','twelve days')
            if lead.get('search_profile') in {SKILLS_TRIAL,ALL_TRACKS_TRIAL,RECURRING_PROFILE} and code in {'outside_freshness_window','publication_date_unknown','future_publication_date'}:
                detail=detail.replace('seven days (168 hours)','thirty days (720 hours), without fresh confirmed-open evidence').replace('seven days','thirty days')
            if code=='outside_freshness_window':detail+=' Published: '+str(lead.get('published_at'))+'.'
            failed.append(finding(FAILURES[code],code,detail,facts['url']))
        elif code in PENDING:
            pending.append(finding(PENDING[code],code,source=facts['url']))
        elif code in FAILURES:
            pending.append(finding(8,'official_evidence_required','The linked board suggests a mismatch; employer-level confirmation is still required.',facts['url']))
    if official:
        checks=audit.get('checks',{})
        work=lead.get('workplace','').lower()
        if re.search(r'hybrid|on.?site|office',work) and not checks.get('localAddis') and not (checks.get('visaSponsored') or checks.get('visaSponsorshipInferred')):
            failed.append(finding(3,'foreign_presence_without_verified_sponsorship','The source requires foreign hybrid/on-site work without an explicit qualifying visa-sponsorship offer.',facts['url']))
        applicant=lead.get('applicant_locations','').strip().lower()
        if applicant in {'us','usa','united states','uk','united kingdom','canada'}:
            failed.append(finding(3,'domestic_applicant_requirement','The structured applicant-location requirement is '+lead['applicant_locations']+'.',facts['url']))
        facts['visaSponsorshipVerified']=bool(checks.get('visaSponsored'))
    return audit,failed,pending

def review_one(job,all_jobs,manifest,store):
    config=manifest.get(job['id'],{})
    sources=config.get('sources') or [{'url':job.get('applyUrl',''),'authority':'listing'}]
    result={'id':job['id'],'title':job['title'],'company':job['company'],'recordedUrl':job.get('applyUrl',''),
            'policyVersion':POLICY_VERSION,
            'revision':fingerprint(job),'checkedAt':now_utc().isoformat(),'sources':[],'violations':[],'unverified':[]}
    best=None;best_audit=None
    for source in sources:
        try:
            lead,facts=source_lead(source,Fetcher(store,budget=10,timeout=12));result['sources'].append(facts)
            if lead:
                prior_profile=((job.get('sourceEvidence') or {}).get('lead') or {}).get('search_profile')
                if prior_profile in PL200_TRIALS or prior_profile in {SKILLS_TRIAL,ALL_TRACKS_TRIAL,RECURRING_PROFILE}:
                    lead['search_profile']=prior_profile
                    if prior_profile in {SKILLS_TRIAL,ALL_TRACKS_TRIAL,RECURRING_PROFILE}:
                        prior_lead=(job.get('sourceEvidence') or {}).get('lead') or {}
                        if signature(prior_lead.get('company'),prior_lead.get('title'))==signature(lead.get('company'),lead.get('title')):
                            # Fit review persists, but open evidence must be refreshed.
                            lead['responsibility_review']=prior_lead.get('responsibility_review',{})
                            if prior_lead.get('description')!=lead.get('description'):
                                lead['responsibility_review']={}
                audit,failed,pending=assess(lead,facts)
                result['violations'].extend(failed);result['unverified'].extend(pending)
                if best is None or source.get('authority')=='official':best,best_audit=lead,audit
        except Exception as exc:
            result['sources'].append({'url':source['url'],'authority':source.get('authority','listing'),
                'checkedAt':now_utc().isoformat(),'httpStatus':getattr(getattr(exc,'response',None),'status_code',None),
                'error':type(exc).__name__+': '+str(exc)[:300]})
    original_status=result['sources'][0].get('httpStatus')
    if best is None:
        transport_failure=all(s.get('httpStatus') in {401,403,408,429,500,502,503,504} or
            re.match(r'(?:ReadTimeout|ConnectTimeout|Timeout|TimeoutError|ConnectionError|BudgetExceeded):',s.get('error',''))
            for s in result['sources'])
        # A timeout/403 cannot establish that a posting omits dates or years.
        # Keep application disabled until a live retry; do not fabricate a
        # confirmed rule violation from an unavailable response.
        if not transport_failure:
            result['violations'].append(finding(5,'publication_date_unknown'))
            if not EARLY_LEVEL.search(job.get('title','')):
                result['violations'].append(finding(1,'experience_not_verified'))
        result['unverified'].extend([finding(8,'official_source_unavailable','No complete official posting could be inspected.'),
                                    finding(6,'application_link_unverified','The recorded application destination could not be validated.')])
    others=[j for j in all_jobs if j['id']!=job['id'] and j.get('status') in {'deleted','applied','interviewing','offer','rejected','declined'}]
    matches=[j for j in others if blocked(job,[j])]
    if matches:
        result['violations'].append(finding(7,'already_in_pipeline_or_blacklist','Matches preserved record(s): '+', '.join(j['id']+' ('+j['status']+')' for j in matches)))
        result['matchedRecords']=[j['id'] for j in matches]
    for key in ['violations','unverified']:
        result[key]=list({(f['rule'],f['code'],f.get('sourceUrl')):f for f in result[key]}.values())
    if result['violations']:result['decision']='removed'
    elif best_audit and best_audit['state']=='verified':
        from .dossier import validate_dossier
        errors=validate_dossier(job)
        if signature(job['title'],job['company'])!=signature(best.get('title'),best.get('company')) or canonical_url(job['applyUrl'])!=canonical_url(best.get('url')):
            errors.append('record_identity_or_application_link_needs_correction')
        if errors:
            result['decision']='held'
            result['unverified'].append(finding(6,'application_material_needs_review','Source eligibility passes; existing application material requires correction: '+', '.join(errors)))
        else:result['decision']='verified'
    else:result['decision']='held'
    evidence={**(best_audit or {'state':'needs_evidence','reasons':['official_source_unavailable']}),
              'auditedAt':result['checkedAt'],'lead':best or {},'review':{k:result[k] for k in ['decision','violations','unverified','sources']}}
    store.set('job-audit:'+job['id'],evidence)
    return result

def apply_review(report,path=None):
    """Only confirmed failures become tombstones. No physical deletion or unknown-as-failure."""
    def mutate(jobs):
        by_id={j['id']:j for j in jobs};changed=[]
        for item in report['jobs']:
            if item.get('writeResult') in {'restored_for_review','exclusion_updated'}:
                continue
            job=by_id.get(item['id'])
            if not job or job.get('status') not in {'ready','saved'} or fingerprint(job)!=item['revision']:
                item['writeResult']='preserved_changed_or_protected';continue
            if item['decision']!='removed' or not item['violations']:
                item['writeResult']='preserved';continue
            old=job['status'];stamp=now_utc().isoformat()
            reason='; '.join('Rule '+str(f['rule'])+': '+f['detail'] for f in item['violations'])
            job.update(status='deleted',deletedAt=stamp,deletionReason=reason,ruleViolations=copy.deepcopy(item['violations']),
                       verificationAudit={'checkedAt':item['checkedAt'],'sources':item['sources'],'decision':'removed'})
            job.setdefault('activity',[]).append({'type':'eligibility_removed','at':stamp,'fromStatus':old,'toStatus':'deleted','reason':reason,'actor':'source_audit'})
            item['writeResult']='tombstoned';changed.append(job['id'])
        return changed
    return update_jobs(mutate,path)

def apply_scope_correction(report, previous, path=None):
    """Explicit policy correction, limited to traceable exclusions from the named audit."""
    originals={j['id']:j for j in previous['jobs'] if j['id'] in previous.get('changedIds',[])}
    def mutate(jobs):
        by_id={j['id']:j for j in jobs};restored=[]
        for item in report['jobs']:
            old=originals.get(item['id']);job=by_id.get(item['id'])
            if not old or not job or job.get('status')!='deleted' or fingerprint(job)!=item['revision']:
                continue
            # Never reverse a manual blacklist or a later decision, even for the same ID.
            if job.get('ruleViolations')!=old['violations'] or (job.get('verificationAudit') or {}).get('checkedAt')!=old['checkedAt']:
                continue
            transition=next((e for e in reversed(job.get('activity',[])) if e.get('toStatus')), {})
            if transition.get('actor') not in {'source_audit','user_policy_clarification'}:
                continue
            prior_codes={f['code'] for f in old['violations']}
            alignment_codes={'no_power_platform_alignment','no_relevant_technical_alignment','outside_freshness_window','experience_above_three_years','experience_not_verified','publication_date_unknown','applicant_location_not_verified_for_ethiopia'}
            if not (prior_codes & alignment_codes):
                continue
            can_restore=prior_codes.issubset(alignment_codes) and item['decision']!='removed'
            if not can_restore and not item['violations']:
                # Inaccessible evidence cannot undo an independent, confirmed exclusion.
                item['violations']=[copy.deepcopy(f) for f in old['violations'] if f['code'] not in alignment_codes]
                item['decision']='removed'
            history={k:copy.deepcopy(job[k]) for k in ['deletedAt','deletionReason','ruleViolations','verificationAudit'] if k in job}
            job.setdefault('verificationHistory',[]).append(history)
            stamp=now_utc().isoformat()
            if can_restore:
                for key in ['deletedAt','deletionReason','ruleViolations']:job.pop(key,None)
                job['status']='saved'
                restored.append(job['id']);item['writeResult']='restored_for_review'
            else:
                job['ruleViolations']=copy.deepcopy(item['violations'])
                job['deletionReason']='; '.join('Rule '+str(f['rule'])+': '+f['detail'] for f in item['violations'])
                item['writeResult']='exclusion_updated'
            job['verificationAudit']={'checkedAt':item['checkedAt'],'sources':item['sources'],'decision':item['decision'],'policyVersion':POLICY_VERSION}
            job.setdefault('activity',[]).append({'type':'eligibility_restored' if can_restore else 'eligibility_reassessed',
                'at':stamp,'actor':'user_policy_clarification','fromStatus':'deleted','toStatus':job['status'],
                'reason':'Reassessed after the user changed eligibility preferences; current rule findings and prior removal history are retained.'})
        return restored
    return update_jobs(mutate,path)


def review_existing(*,sync=False,manifest_path=None,store=None,jobs_path=None,workers=4,reconsider_path=None):
    store=store or LeadStore();jobs=read_jobs(jobs_path)
    manifest_file=Path(manifest_path or ROOT/'discovery/review_sources.json')
    manifest=json.loads(manifest_file.read_text(encoding='utf-8')) if manifest_file.exists() else {}
    previous=json.loads(Path(reconsider_path).read_text(encoding='utf-8')) if reconsider_path else None
    prior_ids={j['id'] for j in previous['jobs']} if previous else set()
    selected=[j for j in jobs if j.get('status') in {'ready','saved'} or (j['id'] in prior_ids and j.get('status')=='deleted')]
    report={'startedAt':now_utc().isoformat(),'dryRun':not sync,'reviewed':len(selected),'jobs':[], 'policyVersion':POLICY_VERSION,
            'scopeNote':'Software development welcome; Power Platform preferred. Seven-day maximum age must be established. Junior/mid-level labels accepted (up to 5 years); otherwise 0–3 years. Remote eligibility assumed unless explicitly restricted.'}
    if reconsider_path:report['reconsideredReport']=str(reconsider_path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        report['jobs']=list(pool.map(lambda j:review_one(j,jobs,manifest,store),selected))
    report['restoredIds']=apply_scope_correction(report,previous,jobs_path) if sync and previous else []
    if previous and previous.get('policyVersion')==POLICY_VERSION:
        current={j['id']:j for j in read_jobs(jobs_path)}
        report['restoredIds']=list(dict.fromkeys(report['restoredIds']+[id for id in previous.get('restoredIds',[]) if current.get(id,{}).get('status') in {'ready','saved'}]))
    report['changedIds']=apply_review(report,jobs_path) if sync else []
    if sync:
        current={j['id']:j for j in read_jobs(jobs_path)}
        report['restoredIds']=[id for id in report['restoredIds'] if current.get(id,{}).get('status') in {'ready','saved'}]
    report['changedIds']+= [j['id'] for j in report['jobs'] if j.get('writeResult') in {'restored_for_review','exclusion_updated'}]
    report['counts']=dict(Counter(j['decision'] for j in report['jobs']))
    report['ruleCounts']=dict(Counter(str(f['rule']) for j in report['jobs'] for f in j['violations']))
    report['finishedAt']=now_utc().isoformat()
    store.set('opportunity-review:last_report',report)
    return report
