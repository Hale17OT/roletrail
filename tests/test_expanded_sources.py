"""Regression checks for evidence recovered by the expanded live sweep."""
import copy
import json
from datetime import timedelta

import pytest

from discovery.audit import is_ats_host
from discovery.jobspy_compat import normalize_zip_timestamp
from discovery.policy import evaluate
from discovery.sources import ats_board, collect_ats, collect_feed
from discovery.telegram import parse_post
from test_discovery import FakeFetcher, NOW, lead


def test_smartrecruiters_skips_old_details_without_skipping_unknown_dates(monkeypatch):
    monkeypatch.setattr('discovery.sources.now_utc', lambda: NOW)
    root='https://api.smartrecruiters.com/v1/companies/example/postings'
    rows=[{'id':'old','name':'Junior Software Engineer','releasedDate':(NOW-timedelta(days=8)).isoformat()},
          {'id':'fresh','name':'Junior Software Engineer','releasedDate':NOW.isoformat()},
          {'id':'unknown','name':'Junior Software Engineer'}]
    detail={'name':'Junior Software Engineer','releasedDate':NOW.isoformat()}
    fetcher=FakeFetcher({root+'?limit=100&offset=0':json.dumps({'content':rows,'totalFound':3}),
                         root+'/fresh':json.dumps(detail),root+'/unknown':json.dumps(detail)})
    results=list(collect_ats({'kind':'smartrecruiters','board':'example','company':'Example'},fetcher))
    assert [r['source_id'] for r in results]==['fresh','unknown']
    assert fetcher.requests==3


def test_sponsored_destination_remote_dossier_preserves_actual_arrangement():
    from discovery.dossier import build_dossier
    raw=lead(location='Tokyo, Japan',description='Build software. 2 years of experience. Remote work (Only in Japan). We provide work visa sponsorship.')
    audit=evaluate(raw,NOW)
    job=build_dossier(raw,audit)
    assert job['locationType']=='Remote within destination country (Tokyo, Japan, Visa Sponsored)'
    assert 'relocating to Tokyo, Japan' in job['tailoredLetter']


def test_ashby_remote_country_constraints_are_not_lost(monkeypatch):
    monkeypatch.setattr('discovery.sources.now_utc', lambda: NOW)
    root='https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true'
    url='https://jobs.ashbyhq.com/example/123'
    row={'title':'Junior Software Engineer','descriptionPlain':'Build software.',
         'jobUrl':url,'publishedAt':NOW.isoformat(),'workplaceType':'Remote'}
    schema={'@type':'JobPosting','title':row['title'],'applicantLocationRequirements':[{'@type':'Country','name':'US'},{'@type':'Country','name':'CA'}]}
    fetcher=FakeFetcher({root:json.dumps({'jobs':[row]}),url:'<script type="application/ld+json">'+json.dumps(schema)+'</script>'})
    result=list(collect_ats({'kind':'ashby','board':'example','company':'Example'},fetcher))[0]
    assert 'domestic_only_eligibility' in evaluate(result,NOW)['reasons']
    assert result['published_at']==NOW.isoformat() and result['constraint_audit_url']==url
    unavailable=list(collect_ats({'kind':'ashby','board':'example','company':'Example'},FakeFetcher({root:json.dumps({'jobs':[row]})})))[0]
    assert not unavailable['official_verified']


def test_original_greenhouse_publication_and_advertised_seniority():
    root = 'https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true'
    row = {'id': 1, 'title': 'Software Engineer', 'content': '&lt;p&gt;Build Python software. 2 years of experience. Fully remote.&lt;/p&gt;',
           'absolute_url': 'https://job-boards.greenhouse.io/example/jobs/1',
           'first_published': NOW.isoformat(), 'updated_at': (NOW+timedelta(days=1)).isoformat(),
           'metadata': [{'name': 'Standard Job Title', 'value': 'Senior Team Member'}]}
    result = list(collect_ats({'kind':'greenhouse','board':'example','company':'Example'}, FakeFetcher({root:json.dumps({'jobs':[row]})})))[0]
    assert result['published_at'] == NOW.isoformat()
    assert '<p>' not in result['description'] and result['official_verified']
    assert 'seniority' in evaluate(result, NOW)['reasons']


def test_null_greenhouse_metadata_does_not_abort_board():
    root='https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true'
    row={'id':1,'title':'Junior Software Engineer','content':'Build Python software.',
         'absolute_url':'https://job-boards.greenhouse.io/example/jobs/1',
         'first_published':NOW.isoformat(),'metadata':None}
    rows=list(collect_ats({'kind':'greenhouse','board':'example','company':'Example'},FakeFetcher({root:json.dumps({'jobs':[row,{**row,'id':2}]})})))
    assert len(rows)==2 and rows[0]['seniority']==''


def test_lever_workplace_is_from_matching_job_header_only():
    from discovery.audit import same_page_workplace
    raw={'title':'Junior Software Engineer','workplace':''}
    html='<div class="posting-headline"><h2>Junior Software Engineer</h2><div class="posting-categories"><div class="workplaceTypes">Remote</div></div></div>'
    assert same_page_workplace(raw,html,'https://jobs.lever.co/example/123')['workplace']=='Remote'
    assert same_page_workplace(raw,html.replace('Junior Software','Other Software'),'https://jobs.lever.co/example/123')['workplace']==''
    assert same_page_workplace(raw,'<footer>Remote</footer>','https://jobs.lever.co/example/123')['workplace']==''
    assert same_page_workplace(raw,html,'https://untrusted.example/123')['workplace']==''


def test_remoteok_publication_stays_discovery_only():
    url='https://remoteok.com/api'
    row={'position':'Junior Software Engineer','company':'Example','url':'https://remoteok.com/jobs/123',
         'date':'2026-11-08','description':'Python and TypeScript.'}
    rows=list(collect_feed({'name':'Remote OK','kind':'remoteok','url':url},FakeFetcher({url:json.dumps([{'legal':'attribution'},row])})))
    assert len(rows)==1 and rows[0]['published_at']=='' and rows[0]['board_date']=='2026-11-08'
    assert not rows[0].get('official_verified') and rows[0]['attribution_url']==row['url']


def test_jobicy_cursor_is_opaque_and_keeps_filters():
    url='https://jobicy.com/api/jobs?count=100'
    next_url=url+'&cursor=a%2Bb%2Fc%3D'
    row={'id':1,'jobTitle':'Junior Developer','companyName':'Example','url':'https://jobicy.com/jobs/1'}
    fetcher=FakeFetcher({url:json.dumps({'jobs':[row],'nextCursor':'a+b/c='}),
                         next_url:json.dumps({'jobs':[row,{**row,'id':2,'url':'https://jobicy.com/jobs/2'}],'nextCursor':None})})
    rows=list(collect_feed({'name':'Jobicy','kind':'jobicy','url':url,'max_pages':3},fetcher))
    assert len(rows)==2 and fetcher.requests==2


def test_himalayas_search_pages_and_requires_employer_evidence():
    from discovery.audit import inspect_source
    from discovery.sources import resolve_discovery
    url='https://himalayas.app/jobs/api/search?q=junior&sort=recent'
    row={'title':'Junior Software Engineer','companyName':'Example','guid':'https://himalayas.app/companies/example/jobs/junior',
         'applicationLink':'https://himalayas.app/companies/example/jobs/junior','pubDate':123,'seniority':['Entry-level']}
    fetcher=FakeFetcher({url:json.dumps({'jobs':[row],'limit':1,'totalCount':2}),
                         url+'&page=2':json.dumps({'jobs':[{**row,'guid':row['guid']+'2','applicationLink':row['applicationLink']+'2'}],'limit':1,'totalCount':2})})
    rows=list(collect_feed({'name':'Himalayas','kind':'himalayas_search','url':url,'max_pages':3},fetcher))
    assert len(rows)==2 and all(r['published_at']=='' for r in rows)
    assert not rows[0].get('seniority') # Aggregator level is not official experience proof.
    before=fetcher.requests
    lead=resolve_discovery(rows[0],fetcher)
    assert not inspect_source(lead,fetcher,[])['official_verified']
    assert fetcher.requests==before # Use documented API, never protected job HTML.


def test_observed_internal_apply_get_redirect_reaches_employer():
    from discovery.sources import resolve_discovery
    url='https://www.arbeitnow.nl/jobs/companies/example/junior'
    class RedirectFetcher(FakeFetcher):
        def get(self,url,**kwargs):
            result=super().get(url,**kwargs)
            if url.endswith('/apply'):result['url']='https://jobs.lever.co/example/123'
            return result
    fetcher=RedirectFetcher({url:'<a href="'+url+'/apply">Apply Now</a>',url+'/apply':'Official job'})
    result=resolve_discovery({'url':url,'company':'Example','title':'Junior Developer'},fetcher)
    assert result['url']=='https://jobs.lever.co/example/123'
    assert result['discovery_url']==url and fetcher.requests==2


def test_live_native_ats_evidence_does_not_exhaust_page_audit_budget(tmp_path,monkeypatch):
    import discovery.pipeline as pipeline
    import discovery.runs as runs
    from discovery.policy import now_utc
    from discovery.store import LeadStore
    monkeypatch.setattr(runs,'STATE_DIR',tmp_path)
    rows=[lead(company='Example '+str(i),url='https://jobs.ashbyhq.com/example/'+str(i),
               published_at=now_utc().isoformat()) for i in range(3)]
    for raw in rows:
        raw['responsibility_review']={'approved':True,'basis':'Reviewed fixture','gaps':[]}
        raw['open_check']={'active':True,'identity_matched':True,'source_url':raw['url'],'application_url':raw['url']+'/apply',
            'checked_at':now_utc().isoformat(),'evidence':'Matching application verified in fixture'}
    monkeypatch.setattr(pipeline,'collect_feed',lambda *args:iter(rows))
    monkeypatch.setattr(pipeline,'resolve_discovery',lambda raw,_:raw)
    config=pipeline.load_config();config['feeds']=[{'name':'Fixture'}];config['verification_budget']=1
    path=tmp_path/'jobs.json';path.write_text('[]',encoding='utf-8');fetcher=FakeFetcher({})
    jobs,report=pipeline.run_discovery(sources=['feeds'],config=config,employers=[],
        store=LeadStore(tmp_path/'state.db'),fetcher=fetcher,jobs_path=path)
    assert len(jobs)==3 and fetcher.requests==0
    assert 'verification_budget_reached' not in report['reasons']
    assert path.read_text(encoding='utf-8')=='[]'


def test_hidden_telegram_application_link_survives_visible_channel_link():
    post = {'postId':'example/123', 'url':'https://t.me/example/123',
            'text':'Job Title: Junior Python Developer\nCompany: Example\nhttps://t.me/example',
            'links':['https://jobs.ashbyhq.com/example/123']}
    result = parse_post(post)[0]
    assert result['url'] == post['links'][0]
    assert result['published_at'] == ''


def test_zip_timestamp_patch_is_narrow_and_does_not_mutate_input():
    row = {'status':{'postedAtUtc':{'seconds':'$n1794038400'}},'other':[1]}
    before = copy.deepcopy(row)
    fixed = normalize_zip_timestamp(row)
    assert row == before
    assert fixed['status']['postedAtUtc']['seconds'] == 1794038400
    assert normalize_zip_timestamp({'status':{'postedAtUtc':{'seconds':'not-a-date'}}})['status']['postedAtUtc']['seconds'] == 'not-a-date'


@pytest.mark.parametrize('host,expected',[
    ('example.applytojob.com',True),('example.myworkdayjobs.com',True),
    ('apply.workable.com',True),('example.applytojob.com.evil.test',False),
    ('evilapplytojob.com',False)])
def test_ats_trust_requires_domain_boundary(host,expected):
    assert is_ats_host(host) is expected


@pytest.mark.parametrize('changes,reason',[
    ({'description':'Build software. 2 years of experience. One in-person interview is required.'},'mandatory_foreign_presence'),
    ({'description':'Build software. 2 years of experience. We are remote-first, but not remote-only. We expect to get together quarterly for intense in-person working sessions.'},'mandatory_foreign_presence'),
    ({'description':'Build software. 2 years of experience. As the first senior AI hire, own our platform.'},'seniority')])
def test_source_specific_disqualifiers_override_remote_label(changes,reason):
    assert reason in evaluate(lead(**changes),NOW)['reasons']


def test_intermediate_role_and_explicit_remote_description():
    raw = lead(title='Intermediate Backend Engineer', workplace='', location='Bangalore, India',
               description='Build software in Python. All of our roles are remote.')
    assert evaluate(raw,NOW)['state'] == 'verified'
    raw['description'] += ' In-person gatherings are optional.'
    assert evaluate(raw,NOW)['state'] == 'verified'


def test_workable_uses_publication_and_explicit_entry_level():
    url = 'https://www.workable.com/api/accounts/example?details=true'
    row = {'title':'Software Engineer','shortcode':'ABC123','application_url':'https://apply.workable.com/example/j/ABC123/',
           'published_on':NOW.date().isoformat(),'created_at':'2020-01-01','telecommuting':True,
           'experience':'Entry Level','description':'Build software with Python and TypeScript.',
           'locations':[{'city':'','country':'Egypt'}]}
    result = list(collect_ats({'kind':'workable','board':'example','company':'Example'},FakeFetcher({url:json.dumps({'jobs':[row]})})))[0]
    assert result['published_at'] == NOW.date().isoformat()
    assert result['seniority'] == 'Entry-level'
    assert evaluate(result,NOW)['state'] == 'verified'
    assert ats_board('https://apply.workable.com/example/j/ABC123/')['board'] == 'example'
    assert ats_board('https://apply.workable.com/j/ABC123/') is None


def test_workable_recheck_preserves_original_publication_for_geographic_duplicates():
    from discovery.review import source_lead
    url='https://www.workable.com/api/accounts/example?details=true'
    row={'title':'Junior Software Engineer','shortcode':'ABC123','description':'Build Python software.',
         'published_on':'2026-11-08','created_at':'2020-01-01','telecommuting':True,'country':'Poland'}
    source={'format':'workable','authority':'official','url':url,'company':'Example',
            'postingId':'ABC123','postingUrl':'https://apply.workable.com/example/j/ABC123'}
    result,facts=source_lead(source,FakeFetcher({url:json.dumps({'jobs':[row,{**row,'country':'Portugal'}]})}))
    assert result['official_verified'] and result['published_at']=='2026-11-08'
    assert result['location']=='Poland; Portugal'
    assert facts['postingUrl']==source['postingUrl']


def test_employer_linked_publisher_includes_requirements_and_holds_blocked_form():
    from discovery.review import source_lead, assess
    url='https://ethiojobs.net/job/example';form='https://docs.google.com/forms/example'
    schema={'@type':'JobPosting','title':'Software Engineer','hiringOrganization':{'name':'Example'},
            'description':'Build software.','datePosted':NOW.isoformat(),
            'jobLocation':{'address':{'addressLocality':'Addis Ababa'}}}
    page={'jobSchema':schema,'data':{'title':'Software Engineer','description':'Build software.',
          'requirement':'2 years of experience. Strong deployed portfolio accepted for less experience.',
          'career_page_link':form}}
    html='<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':page}})+'</script>'
    # The missing form fixture simulates an unavailable authenticated destination.
    raw,facts=source_lead({'url':url,'format':'ethiojobs','authority':'official'},FakeFetcher({url:html}))
    assert 'portfolio' in raw['description'] and raw['published_at']==NOW.isoformat()
    audit,failed,pending=assess(raw,facts)
    assert audit['state']=='needs_evidence'
    assert any(f['code']=='application_destination_unverified' for f in pending)
