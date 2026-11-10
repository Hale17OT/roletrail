from datetime import timedelta
import pytest
from discovery.policy import SKILLS_TRIAL, evaluate
from discovery.dossier import build_dossier, validate_dossier
from discovery.review import source_lead
from test_discovery import lead, NOW


def trial(**changes):
    return lead(search_profile=SKILLS_TRIAL,
        responsibility_review={'approved':True,'basis':'Reviewed delivery scope against verified projects','gaps':[]},
        **changes)


def open_check(raw, **changes):
    return {'active':True,'identity_matched':True,'source_url':raw['url'],
        'application_url':raw['url']+'/apply','evidence':'Exact vacancy and active application inspected',
        'checked_at':NOW.isoformat(),**changes}


def test_trial_does_not_change_normal_window_or_require_certification():
    raw=trial(published_at=(NOW-timedelta(days=20)).isoformat())
    assert evaluate(raw,NOW)['state']=='verified'
    raw.pop('search_profile')
    assert 'outside_freshness_window' in evaluate(raw,NOW)['reasons']


def test_responsibilities_need_actual_review():
    raw=trial(title='Business Applications Analyst',description='Support Microsoft 365 and SharePoint. Fully remote.')
    assert evaluate(raw,NOW)['state']=='verified'
    raw['responsibility_review']={}
    assert 'responsibility_review_required' in evaluate(raw,NOW)['reasons']
    raw['description']='Build Power Apps. 4 years experience. Fully remote.'
    assert 'experience_above_three_years' not in evaluate(raw,NOW)['reasons']
    raw['description']='Build Power Apps. 6 years experience. Fully remote.'
    assert 'experience_above_five_years' in evaluate(raw,NOW)['reasons']


@pytest.mark.parametrize('change',[{'active':False},{'identity_matched':False},
    {'checked_at':(NOW-timedelta(hours=25)).isoformat()},
    {'source_url':'https://example.com/other'}, {'application_url':''}])
def test_unknown_date_requires_current_same_job_open_proof(change):
    raw=trial(published_at='')
    raw['open_check']=open_check(raw,**change)
    assert 'open_status_not_verified' in evaluate(raw,NOW)['reasons']


def test_unknown_and_old_dates_remain_honest_and_closed_never_passes():
    raw=trial(published_at='')
    raw['open_check']=open_check(raw)
    audit=evaluate(raw,NOW)
    assert audit['state']=='verified'
    assert audit['checks']['publicationBasis']=='confirmed_open_date_unknown'
    dossier=build_dossier(raw,audit)
    assert dossier['postedDate']=='Date not disclosed'
    assert 'Publication date not disclosed' in dossier['postedAgo']
    assert not validate_dossier(dossier)
    raw['published_at']=(NOW-timedelta(days=60)).isoformat()
    assert evaluate(raw,NOW)['state']=='verified'
    raw['closed']=True
    assert 'closed' in evaluate(raw,NOW)['reasons']


def test_explicit_restrictions_and_seniority_survive_trial():
    assert 'seniority' in evaluate(trial(title='Senior Power Apps Developer'),NOW)['reasons']
    assert 'domestic_only_eligibility' in evaluate(trial(applicant_locations='United Kingdom'),NOW)['reasons']
    assert 'no_business_application_alignment' in evaluate(trial(title='React Developer',description='Develop React websites. Fully remote.'),NOW)['reasons']


def test_open_dom_requires_exact_title_and_inspected_application():
    class Fetch:
        def get(self,url,**kwargs):
            return {'url':url,'status':200,'text':'<main>Junior Power Apps Developer</main><a id="apply" href="/apply">Apply</a>'}
    config={'url':'https://example.com/job','authority':'official','format':'dom','title':'Junior Power Apps Developer',
        'company':'Example','openSelector':'main','applicationSelector':'#apply'}
    raw,_=source_lead(config,Fetch())
    assert raw['open_check']['application_url']=='https://example.com/apply'
    config['title']='Different vacancy'
    raw,_=source_lead(config,Fetch())
    assert 'open_check' not in raw


def test_employer_post_age_is_refreshed_only_from_configured_time():
    class Fetch:
        def get(self,url,**kwargs):
            return {'url':url,'status':200,'text':'<main>Junior Power Apps Developer<time class="post">4d</time><time>1h</time></main>'}
    raw,facts=source_lead({'url':'https://example.com/post','authority':'official','format':'dom',
        'title':'Junior Power Apps Developer','company':'Example','publicationSelector':'time.post'},Fetch())
    assert raw['published_at']=='4 days ago'
    assert raw['publication_observed_at']==facts['checkedAt']
