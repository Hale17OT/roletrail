import copy
import json
from datetime import timedelta

import dashboard
from discovery.policy import now_utc, canonical_url
from discovery.review import assess, apply_review, fingerprint, review_one, source_lead
from discovery.store import LeadStore, correct_employer_rejections, read_jobs, apply_email_updates
import pytest


def test_transport_failure_does_not_invent_missing_publication_or_experience(tmp_path,monkeypatch):
    def unavailable(*args):
        raise TimeoutError('Source temporarily unavailable')
    monkeypatch.setattr('discovery.review.source_lead',unavailable)
    job={'id':'example','title':'Software Engineer','company':'Example','applyUrl':'https://example.org/job','status':'saved'}
    result=review_one(job,[job],{},LeadStore(tmp_path/'state.db'))
    assert result['decision']=='held' and result['violations']==[]
    assert {f['code'] for f in result['unverified']}=={'official_source_unavailable','application_link_unverified'}


@pytest.mark.parametrize('title',['Junior Software Engineer','Junior Python Developer','Frontend Engineer','Full-Stack Developer','Backend / Platform Engineer','Junior .NET Developer','C# Developer','QA Automation Engineer'])
def test_software_track_does_not_require_power_platform(title):
    from discovery.policy import evaluate
    from discovery.dossier import build_dossier, validate_dossier
    lead={'title':title,'company':'Example','description':'2 years of experience. Python, TypeScript, REST APIs. Remote worldwide.',
          'location':'Worldwide','workplace':'Remote','official_verified':True,'url':'https://example.org/job',
          'published_at':(now_utc()-timedelta(hours=2)).isoformat(),'source':'test'}
    audit=evaluate(lead)
    assert audit['state']=='verified' and audit['checks']['domainTrack']=='software'
    job=build_dossier(lead,audit)
    assert job['matchedCv']['fileName']=='software-engineer.pdf'
    assert 'Power Platform' not in job['tags'] and 'PL-200' not in job['tags']
    assert 'Microsoft business applications' not in job['tailoredLetter']
    assert not validate_dossier(job)


def test_preference_bonus_without_weakening_other_gates():
    from discovery.policy import evaluate
    from discovery.dossier import build_dossier
    base={'title':'Junior Software Engineer','company':'Example','description':'2 years of experience. Python, TypeScript. Remote worldwide.',
          'location':'Worldwide','workplace':'Remote','official_verified':True,'url':'https://example.org/job',
          'published_at':(now_utc()-timedelta(hours=2)).isoformat(),'source':'test'}
    preferred={**base,'description':base['description']+' Power Platform.'}
    assert build_dossier(preferred,evaluate(preferred))['matchScore']>build_dossier(base,evaluate(base))['matchScore']
    for changes,reason in [({'title':'Senior Software Engineer'},'seniority'),
                           ({'description':'Software developer, 6 years of experience.'},'experience_above_five_years'),
                           ({'description':base['description']+' Must reside in the US.'},'residency_citizenship_or_clearance_restriction'),
                           ({'published_at':(now_utc()-timedelta(days=8)).isoformat()},'outside_freshness_window')]:
        assert reason in evaluate({**base,**changes})['reasons']


def test_scope_correction_only_reverses_traceable_alignment_exclusions(tmp_path):
    from discovery.review import apply_scope_correction
    pp={'rule':2,'code':'no_power_platform_alignment','detail':'Old Power Platform-only rule'}
    stale={'rule':5,'code':'outside_freshness_window','detail':'Old source'}
    original=[];prior=[];current=[]
    for id,failures in [('restore',[pp]),('stale',[pp,stale]),('manual',[pp]),('protected',[pp])]:
        job={'id':id,'status':'deleted','deletedAt':'old','deletionReason':'old','ruleViolations':failures,
             'verificationAudit':{'checkedAt':'old'},'tailoredLetter':'Keep','activity':[{'type':'eligibility_removed','toStatus':'deleted','actor':'source_audit'}]}
        if id=='manual':job['activity'].append({'type':'stage_changed','toStatus':'deleted','actor':'user'})
        if id=='protected':job['status']='applied'
        original.append(copy.deepcopy(job))
        prior.append({'id':id,'checkedAt':'old','violations':failures})
        current.append({'id':id,'revision':fingerprint(job),'decision':'held' if id!='stale' else 'removed',
                        'checkedAt':'new','sources':[],'violations':[] if id!='stale' else [stale]})
    path=tmp_path/'jobs.json';path.write_text(json.dumps(original))
    assert apply_scope_correction({'jobs':current},{'jobs':prior,'changedIds':[j['id'] for j in prior]},path)==['restore']
    result=read_jobs(path)
    assert result[0]['status']=='saved' and result[0]['tailoredLetter']=='Keep'
    assert result[0]['verificationHistory'][0]['ruleViolations']==[pp]
    assert result[0]['activity'][:-1]==original[0]['activity']
    assert result[1]['status']=='deleted' and result[1]['ruleViolations']==[stale]
    assert result[2:]==original[2:]


def test_user_rejection_correction_preserves_materials_and_old_events(tmp_path):
    path = tmp_path / 'jobs.json'
    old = {'id':'a','status':'declined','tailoredLetter':'Keep letter','matchedCv':{'url':'keep.pdf'},
           'activity':[{'type':'email_stage_changed','toStatus':'declined'}]}
    other = {'id':'b','status':'applied'}
    path.write_text(json.dumps([old,other]))
    assert correct_employer_rejections(['a'],path)==['a']
    result=read_jobs(path)
    assert result[0]['status']=='rejected' and result[0]['outcomeType']=='employer_rejected'
    for key in ['tailoredLetter','matchedCv']:assert result[0][key]==old[key]
    assert result[0]['activity'][:-1]==old['activity']
    assert result[1]==other
    assert correct_employer_rejections(['a'],path)==[]


def test_legacy_email_rejection_maps_to_employer_rejected(tmp_path):
    path=tmp_path/'jobs.json';path.write_text(json.dumps([{'id':'a','status':'applied'}]))
    apply_email_updates([{'job_id':'a','from_status':'applied','to_status':'declined','eventAt':(now_utc()-timedelta(hours=1)).isoformat()}],path)
    assert read_jobs(path)[0]['status']=='rejected'


def test_unavailable_age_is_removed_under_user_evidence_rule(tmp_path):
    job={'id':'a','title':'Junior Power Apps Developer','company':'Example','status':'ready','applyUrl':'mailto:no-source@example.org'}
    store=LeadStore(tmp_path/'db')
    result=review_one(job,[job],{},store)
    assert result['decision']=='removed' and {f['rule'] for f in result['violations']}=={5}
    assert {f['rule'] for f in result['unverified']}=={6,8}
    assert dashboard.readiness(job,store)['state']=='needs_recheck'


def test_sponsored_hybrid_is_not_a_location_failure():
    lead={'title':'Product Engineer','company':'Example','description':'2 years of experience in software engineering. We are equally open to the visa sponsorship of non-EU candidates.',
          'url':'https://example.org/job','location':'Paris','workplace':'Hybrid','official_verified':True,
          'published_at':(now_utc()-timedelta(hours=2)).isoformat()}
    facts={'authority':'official','url':lead['url']}
    _,failures,_=assess(lead,facts)
    assert facts['visaSponsorshipVerified']
    assert not failures


def test_lever_creation_is_not_publication():
    class Fetch:
        def get(self,*args,**kwargs):return {'url':'https://example.org/api','status':200,'text':json.dumps({
            'text':'Junior Power Apps Developer','id':'123','createdAt':1234,'categories':{'location':'Worldwide'},
            'workplaceType':'remote','descriptionPlain':'2 years of experience. Power Apps. Remote worldwide.'})}
    lead,facts=source_lead({'url':'https://example.org/api','format':'lever','authority':'official','company':'Example'},Fetch())
    assert lead['published_at']=='' and facts['recordCreatedAt']==1234


def test_only_confirmed_unchanged_opportunity_gets_tombstone(tmp_path):
    path=tmp_path/'jobs.json'
    original=[{'id':name,'status':status,'custom':'keep'} for name,status in [('bad','ready'),('hold','saved'),('protected','applied'),('changed','ready')]]
    entries=[]
    for j in original:
        entries.append({'id':j['id'],'revision':fingerprint(j),'decision':'held' if j['id']=='hold' else 'removed',
                        'checkedAt':now_utc().isoformat(),'sources':[],
                        'violations':[] if j['id']=='hold' else [{'rule':5,'detail':'Stale source','code':'outside_freshness_window'}]})
    updated=copy.deepcopy(original);updated[-1]['custom']='concurrent update'
    path.write_text(json.dumps(updated))
    assert apply_review({'jobs':entries},path)==['bad']
    result=read_jobs(path)
    assert result[0]['status']=='deleted' and result[0]['deletedAt'] and result[0]['ruleViolations']
    assert result[0]['custom']=='keep' and len(result)==4
    assert result[1:]==updated[1:]


def test_tracking_and_application_variants_share_blacklist_identity():
    base='https://quetica.applytojob.com/apply/ABC/Developer'
    assert canonical_url(base+'?source=INDE&~')==canonical_url(base+'?utm_source=test')
    ashby='https://jobs.ashbyhq.com/example/1b808b09-18d0-4486-a03a-f4cc2f38aba0'
    assert canonical_url(ashby)==canonical_url(ashby+'/application?utm_source=test')


def test_country_work_authorization_without_sponsorship_is_restricted():
    from discovery.policy import evaluate
    lead={'title':'Junior Power Apps Developer','company':'Example','description':
          'Power Apps. Candidates must be legally authorized to work in the country where employment is offered. Example does not provide employment sponsorship for this position.',
          'location':'UK - Remote','workplace':'Remote','official_verified':True}
    assert 'residency_citizenship_or_clearance_restriction' in evaluate(lead)['reasons']
    lead['description']='Power Apps. Location: Remote Anywhere in Canada.'
    assert 'domestic_only_eligibility' in evaluate(lead)['reasons']
