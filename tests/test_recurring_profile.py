import json
from datetime import timedelta
from discovery.policy import RECURRING_PROFILE,evaluate
from discovery.afriwork import inspect,posting_url
from discovery.people_posts import post_time,read_post
from test_all_tracks_active_trial import trial
from test_discovery import NOW

def test_recurring_older_active_roles_preserve_age_and_hard_gates():
    raw=trial(search_profile=RECURRING_PROFILE,published_at=(NOW-timedelta(days=80)).isoformat())
    audit=evaluate(raw,NOW)
    assert audit['state']=='verified' and audit['checks']['publicationBasis']=='confirmed_open_older_posting'
    raw['open_check']['checked_at']=(NOW-timedelta(days=2)).isoformat()
    assert evaluate(raw,NOW)['state']!='verified'
    raw=trial(search_profile=RECURRING_PROFILE,title='Senior Python Developer')
    assert 'seniority' in evaluate(raw,NOW)['reasons']
    raw=trial(search_profile=RECURRING_PROFILE,description='Build software. 3-5 years of experience. Remote worldwide.')
    assert evaluate(raw,NOW)['checks']['experienceBand']=='stretch'
    raw['description']='Build software. 7 years of experience. Remote worldwide.'
    assert evaluate(raw,NOW)['state']=='rejected'
    raw['description']='Build software. Remote worldwide.\nExperience Level\nMid-level\n3 - 5 years\nWork Arrangement\nRemote'
    assert evaluate(raw,NOW)['checks']['experienceBand']=='stretch'

def test_unknown_tenure_needs_review_not_junior_word():
    raw=trial(search_profile=RECURRING_PROFILE,title='Backend Developer',description='Develop APIs. Remote worldwide.')
    assert evaluate(raw,NOW)['state']=='verified'
    raw.pop('responsibility_review')
    assert 'responsibility_review_required' in evaluate(raw,NOW)['reasons']

def test_recurring_ats_discovery_does_not_hide_older_open_candidates():
    from discovery.sources import collect_ats
    from discovery.policy import now_utc
    class Fetch:
        def json(self,url,**kwargs):
            row={'id':'x','name':'Junior Python Developer','releasedDate':(now_utc()-timedelta(days=80)).isoformat(),'ref':'https://api.smartrecruiters.com/detail/x','location':{'remote':True,'city':'Remote'},'jobAd':{'sections':{'jobDescription':{'text':'Build software APIs'}}}}
            return {'content':[row],'totalFound':1} if 'offset=' in url else row
    board={'kind':'smartrecruiters','board':'example','company':'Example'}
    assert list(collect_ats(board,Fetch(),hours=720))==[]
    assert len(list(collect_ats({**board,'include_older':True},Fetch(),hours=720)))==1

def test_afriwork_expands_exact_button_and_uses_published_not_refresh():
    guid='a80df53e-71b1-40b9-9cda-746d38a3b018'
    raw={'url':'https://t.me/freelance_ethio/1','links':['https://t.me/afriworkapplicantbot/applicantapp?startapp='+guid]}
    assert posting_url(raw)=='https://afriworket.com/jobs/'+guid
    row={'id':guid,'title':'Python Developer','description':'<p>Develop APIs.</p>','entity':{'name':'Example','type':'company'},
      'published_at':'2026-10-10T10:00:00Z','refreshed_at':'2026-11-09T10:00:00Z','experience_level':'JUNIOR','city':{'en':'Addis Ababa'},'job_site':'ONSITE'}
    class Fetch:
      def get(self,url,**kwargs):return {'text':'<script id="__NUXT_DATA__">'+json.dumps([json.dumps({'jobs_by_pk':row})])+'</script>'}
    got=inspect(raw,Fetch())
    assert got['published_at']==row['published_at'] and got['company']=='Example' and got['official_verified']
    assert 'open_check' not in got # no email/form verification invented
    raw['closed']=True
    assert inspect(raw,Fetch())['closed']

def test_people_post_time_is_not_vacancy_publication_or_authority():
    url='https://www.linkedin.com/posts/person_hiring-activity-'+str(int(NOW.timestamp()*1000)<<22)+'-abc'
    assert post_time(url)==NOW.isoformat()
    class Fetch:
      def get(self,url,**kwargs):return {'url':url,'text':'<title>Hiring | Jane</title><div class="base-main-feed-card__actor-name">Jane</div><article>I am hiring a Python Developer. Remote.</article>'}
    raw=read_post(url,Fetch())
    assert raw['postAuthor']=='Jane' and not raw['official_verified'] and not raw['published_at']
    assert raw['postPublishedAt']==NOW.isoformat()

def test_browser_proof_for_exact_smartrecruiters_vacancy_expires():
    from discovery.review import source_lead
    from discovery.policy import now_utc
    row={'id':'123','name':'Junior Java Developer','applyUrl':'https://jobs.smartrecruiters.com/example/123','location':{'hybrid':True,'city':'Munich'},'releasedDate':'2026-11-09T10:00:00Z','jobAd':{'sections':{'jobDescription':{'text':'Develop software'}}}}
    class Fetch:
        def get(self,url,**kwargs):
            if 'api.' in url:return {'url':url,'status':200,'text':json.dumps(row)}
            raise RuntimeError('Application requires browser')
    config={'url':'https://api.smartrecruiters.com/v1/companies/example/postings/123','postingId':'123','company':'Example','format':'smartrecruiters','authority':'official','verifyActiveApplication':True,
      'applicationBrowserVerification':{'method':'live_browser','state':'accessible','postingId':'123','url':row['applyUrl'],'formTitle':row['name'],'evidence':'Exact form and CV upload visible','checkedAt':now_utc().isoformat()}}
    raw,facts=source_lead(config,Fetch())
    assert raw['open_check']['active'] and facts['applicationBrowserVerification']
    config['applicationBrowserVerification']['postingId']='other'
    assert 'open_check' not in source_lead(config,Fetch())[0]
    config['applicationBrowserVerification']['postingId']='123'
    config['applicationBrowserVerification']['checkedAt']=(now_utc()-timedelta(days=2)).isoformat()
    assert 'open_check' not in source_lead(config,Fetch())[0]
