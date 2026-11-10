import json
from datetime import timedelta
from discovery.policy import ALL_TRACKS_TRIAL,SKILLS_TRIAL,evaluate
from discovery.review import source_lead
from test_discovery import lead,NOW


def trial(**changes):
    raw=lead(title='Junior Python Developer',description='Develop software APIs. Remote worldwide.',
        search_profile=ALL_TRACKS_TRIAL,published_at=(NOW-timedelta(days=20)).isoformat(),
        responsibility_review={'approved':True,'basis':'Reviewed actual project evidence','gaps':[]})
    raw.update(changes)
    raw['open_check']={'active':True,'identity_matched':True,'source_url':raw['url'],
        'application_url':raw['url']+'/apply','checked_at':NOW.isoformat(),'evidence':'Exact vacancy listed and matching application verified'}
    return raw


def test_all_tracks_accepts_software_without_changing_previous_trial():
    raw=trial()
    assert evaluate(raw,NOW)['state']=='verified'
    raw['search_profile']=SKILLS_TRIAL
    assert 'no_business_application_alignment' in evaluate(raw,NOW)['reasons']
    raw.pop('search_profile')
    assert 'outside_freshness_window' in evaluate(raw,NOW)['reasons']


def test_active_proof_is_mandatory_even_for_yesterday():
    raw=trial(published_at=(NOW-timedelta(days=1)).isoformat())
    raw.pop('open_check')
    assert 'open_status_not_verified' in evaluate(raw,NOW)['reasons']
    raw['responsibility_review']={}
    assert 'responsibility_review_required' in evaluate(raw,NOW)['reasons']


def test_thirty_day_cap_cannot_be_waived_by_open_proof():
    raw=trial(published_at=(NOW-timedelta(days=31)).isoformat())
    assert 'outside_freshness_window' in evaluate(raw,NOW)['reasons']
    raw['published_at']=(NOW-timedelta(days=29)).isoformat()
    raw['closed']=True
    assert 'closed' in evaluate(raw,NOW)['reasons']
    raw['closed']=False
    raw['applicant_locations']='United States'
    assert 'domestic_only_eligibility' in evaluate(raw,NOW)['reasons']


def test_active_board_entry_needs_matching_application_title():
    row={'shortcode':'abc','title':'Junior Python Developer','description':'Develop APIs','application_url':'https://apply.workable.com/j/abc/apply'}
    class Fetch:
        title='Junior Python Developer'
        def get(self,url,**kwargs):
            return {'status':200,'url':url,'text':'<title>'+self.title+'</title>' if '/apply' in url else json.dumps({'jobs':[row]})}
    fetch=Fetch()
    config={'url':'https://example.com/board','postingId':'abc','company':'Example','format':'workable','authority':'official','verifyActiveApplication':True}
    raw,facts=source_lead(config,fetch)
    assert raw['open_check']['active'] and facts['activeApplicationVerified']
    fetch.title='No job found'
    raw,facts=source_lead(config,fetch)
    assert 'open_check' not in raw


def test_explicit_remote_scope_in_title_or_adverb_is_not_inferred_global():
    raw=trial(description='Develop software APIs remotely (within EU).')
    assert 'mandatory_foreign_presence' in evaluate(raw,NOW)['reasons']
    raw=trial(title='Junior Software Developer - 100% Remote within Spain')
    assert 'mandatory_foreign_presence' in evaluate(raw,NOW)['reasons']


def test_required_years_in_role_are_not_treated_as_unknown():
    raw=trial(description='Develop software APIs.\n7+ years in technology delivery, with 4+ years as Scrum Master.')
    assert 'experience_above_five_years' in evaluate(raw,NOW)['reasons']
