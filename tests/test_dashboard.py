import copy
import json
import threading
import time
from datetime import timedelta
from email.message import EmailMessage

import pytest
import dashboard
import email_monitor
from discovery import runs
from discovery.dossier import build_dossier
from discovery.policy import now_utc, evaluate
from discovery.store import LeadStore, apply_email_updates, read_jobs, change_status, merge_ui_snapshot


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = LeadStore(tmp_path / 'state.db')
    monkeypatch.setattr(dashboard, 'LeadStore', lambda: store)
    monkeypatch.setattr(email_monitor, 'LeadStore', lambda: store)
    monkeypatch.setattr(dashboard, 'STATE_DIR', tmp_path)
    monkeypatch.setattr(runs, 'STATE_DIR', tmp_path)
    raw = {'title': 'Junior Power Platform Developer', 'company': 'Example',
           'description': 'Build Power Apps and Dataverse. 2 years of experience. Remote worldwide.',
           'url': 'https://jobs.ashbyhq.com/example/123', 'location': 'Worldwide', 'workplace': 'Remote',
           'published_at': (now_utc() - timedelta(hours=12)).isoformat(), 'source': 'ashby:Example',
           'official_verified': True, 'audit_method': 'live_ats_api'}
    job = build_dossier(raw, evaluate(raw), 'saved')
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([job]), encoding='utf-8')
    return path, store, job


def test_legacy_and_protected_projections_do_not_mutate(fixture):
    path, store, job = fixture
    job.pop('sourceEvidence'); job['status'] = 'applied'
    path.write_text(json.dumps([job]), encoding='utf-8')
    before = path.read_bytes()
    p = dashboard.project_jobs(path)[0]
    assert p['_meta']['eligibility']['state'] == 'needs_recheck'
    assert 'deleted' not in p['_meta']['actions']
    assert path.read_bytes() == before


def test_every_manual_promotion_route_is_gated(fixture):
    path, store, job = fixture
    job.pop('sourceEvidence'); path.write_text(json.dumps([job]), encoding='utf-8')
    for invoke in [lambda: dashboard.mutate_job(job['id'], {'revision': dashboard.revision(job), 'status': 'ready'}, path),
                   lambda: change_status(job['id'], 'saved', 'ready', path),
                   lambda: merge_ui_snapshot([{'id': job['id'], 'status': 'ready'}], path)]:
        with pytest.raises(ValueError): invoke()
    assert read_jobs(path)[0] == job


def test_readiness_requires_identity_complete_kit_and_recent_audit(fixture):
    _, _, job = fixture
    assert dashboard.readiness(job)['canApply']
    for mutate in [lambda j: j.update(company='Other'), lambda j: j.update(instructions=''),
                   lambda j: j['sourceEvidence'].update(auditedAt=(now_utc()-timedelta(hours=25)).isoformat()),
                   lambda j: j['sourceEvidence']['lead'].update(published_at=(now_utc()-timedelta(hours=169)).isoformat())]:
        changed=copy.deepcopy(job); mutate(changed)
        assert not dashboard.readiness(changed)['canApply']


def test_submission_freezes_material_and_stale_edits_fail(fixture):
    path, _, job = fixture
    rev = dashboard.revision(job)
    with pytest.raises(ValueError): dashboard.mutate_job(job['id'], {'revision': rev, 'status':'applied'}, path)
    p = dashboard.mutate_job(job['id'], {'revision':rev,'status':'applied','confirmSubmission':True}, path)
    snapshot=read_jobs(path)[0]['submittedKit']
    assert (dashboard.STATE_DIR/'submitted'/(snapshot['cvSha256']+'.pdf')).is_file()
    assert snapshot['letter']==job['tailoredLetter']
    with pytest.raises(ValueError): dashboard.mutate_job(job['id'], {'revision':rev,'note':'stale'}, path)
    with pytest.raises(ValueError): dashboard.mutate_job(job['id'], {'revision':p['_meta']['revision'],'letter':'New letter '*10}, path)
    p=dashboard.mutate_job(job['id'],{'revision':p['_meta']['revision'],'status':'interviewing','note':'Recruiter called','nextAction':{'text':'Prepare examples','due':'2026-11-12'}},path)
    assert read_jobs(path)[0]['submittedKit']==snapshot
    assert p['activity'][-2]['text']=='Recruiter called'


def test_delete_blacklists_and_is_read_only(fixture):
    path,_,job=fixture
    p=dashboard.mutate_job(job['id'],{'revision':dashboard.revision(job),'status':'deleted','reason':'Wrong location'},path)
    assert p['deletedAt'] and p['deletionReason']=='Wrong location'
    with pytest.raises(ValueError): dashboard.mutate_job(job['id'],{'revision':p['_meta']['revision'],'note':'No'},path)


def test_recheck_failure_does_not_modify_protected_records(fixture):
    path,store,job=fixture
    job['status']='interviewing';path.write_text(json.dumps([job]),encoding='utf-8');before=path.read_bytes()
    class Failing:
        def get(self,*a,**kw): raise OSError('offline')
    result=dashboard.recheck(job['id'],path,Failing(),store)
    assert not result['_meta']['eligibility']['canApply']
    assert path.read_bytes()==before


def test_latest_email_wins_and_old_event_cannot_reverse_it(fixture):
    path,_,job=fixture
    job['status']='applied';path.write_text(json.dumps([job]),encoding='utf-8')
    base={'job_id':job['id'],'from_status':'applied'}
    older={**base,'to_status':'declined','eventAt':'2026-11-08T10:00:00+00:00','messageId':'older'}
    newer={**base,'to_status':'offer','eventAt':'2026-11-09T10:00:00+00:00','messageId':'newer'}
    assert apply_email_updates([newer,older],path)==[newer]
    before=read_jobs(path)
    assert not apply_email_updates([{**older,'from_status':'offer'}],path)
    assert read_jobs(path)==before


def test_newer_same_stage_email_blocks_older_rejection(fixture):
    path,_,job=fixture
    job['status']='interviewing';path.write_text(json.dumps([job]),encoding='utf-8')
    common={'job_id':job['id'],'from_status':'interviewing'}
    older={**common,'to_status':'declined','eventAt':'2026-11-08T10:00:00+00:00'}
    newer={**common,'to_status':'interviewing','eventAt':'2026-11-09T10:00:00+00:00'}
    assert apply_email_updates([older,newer],path)==[]
    assert read_jobs(path)[0]['status']=='interviewing'
    assert not apply_email_updates([older],path)
    assert read_jobs(path)[0]['lastEmailEventAt']==newer['eventAt']


def test_ambiguous_same_employer_stays_for_review():
    jobs=[{'id':'a','company':'Example','title':'Power Apps Developer'},{'id':'b','company':'Example','title':'Power Automate Developer'}]
    assert email_monitor.match_application(jobs,'example power developer') is None
    assert email_monitor.match_application(jobs,'example power apps developer')['id']=='a'


def test_email_uid_readonly_idempotence_and_auth_failure(fixture,monkeypatch):
    path,store,job=fixture
    job['status']='applied'
    msg=EmailMessage();msg['Subject']='Example Junior Power Platform Developer';msg['From']='recruiter@example.com';msg['Message-ID']='<unique-example>'
    msg['Date']='Mon, 09 Nov 2026 10:00:00 +0000';msg.set_content('We are pleased to offer you the position.')
    calls=[]
    class Fake:
        def __init__(self,*a,**kw):pass
        def login(self,*a):pass
        def select(self,*a,**kw):calls.append(kw);return 'OK',[]
        def uid(self,verb,*args):
            calls.append((verb,args));return ('OK',[b'1']) if verb=='search' else ('OK',[(b'1',msg.as_bytes())])
        def logout(self):pass
    monkeypatch.setattr(email_monitor.imaplib,'IMAP4_SSL',Fake)
    account={'email_user':'test@example.com','app_password':'fixture'}  # pragma: allowlist secret
    updates=[];outcome=email_monitor.check_account_emails(account,[job],updates,store)
    assert outcome['state']=='ok' and len(updates)==1
    assert calls[0]=={'readonly':True} and 'BODY.PEEK' in str(calls)
    store.set(updates[0]['_processedKey'],{'state':'applied'})
    repeated=[];email_monitor.check_account_emails(account,[job],repeated,store);assert repeated==[]
    def fail(*a,**kw):raise OSError('fixture auth failure')
    monkeypatch.setattr(email_monitor.imaplib,'IMAP4_SSL',fail)
    assert email_monitor.check_account_emails(account,[job],[],store)['state']=='failed'


def test_search_duplicate_start_cancel_and_saved_progress(fixture):
    _,store,_=fixture
    entered,release=threading.Event(),threading.Event()
    def runner(**kwargs):
        entered.set();release.wait(3);kwargs['checkpoint']({'sources':[],'funnel':{'fetched':1}});return [],{}
    run=runs.start({},runner,store);assert entered.wait(2)
    try:
        with pytest.raises(ValueError):runs.start({},runner,store)
        assert runs.listing(store)[0]['id']==run['id']
        assert runs.cancel(run['id'],store)['state']=='cancelling'
    finally:release.set()
    for _ in range(100):
        saved=store.get('search-run:'+run['id'])
        if saved['state']=='cancelled':break
        time.sleep(.01)
    assert saved['state']=='cancelled'


def test_interrupted_owner_recovery(fixture):
    _,store,_=fixture
    store.set('search-run:gone',{'id':'gone','owner':-1,'state':'running','startedAt':'2026-11-08'})
    assert runs.listing(store)[0]['state']=='interrupted'


def test_explicit_draft_rebuild_preserves_previous_material_and_id(fixture):
    path,store,job=fixture
    store.set('job-audit:'+job['id'],job['sourceEvidence'])
    job['tailoredLetter']='Historical contact details';job['keyPitch']='Legacy pitch';job['matchedCv']['diskPath']='legacy/missing.pdf'
    path.write_text(json.dumps([job]),encoding='utf-8')
    result=dashboard.mutate_job(job['id'],{'revision':dashboard.revision(job),'rebuildKit':True},path)
    assert result['id']==job['id'] and result['_meta']['eligibility']['canApply']
    assert dashboard.candidate()['email'] in result['tailoredLetter']
    assert result['activity'][-1]['previousKit']['tailoredLetter']=='Historical contact details'
    result.pop('_meta');result['status']='applied';path.write_text(json.dumps([result]),encoding='utf-8')
    with pytest.raises(ValueError):dashboard.mutate_job(result['id'],{'revision':dashboard.revision(result),'rebuildKit':True},path)


def test_partial_email_outcomes_and_review_queue_survive_repeat(fixture,monkeypatch):
    path,store,job=fixture
    monkeypatch.setattr(email_monitor,'JOBS_FILE',str(path))
    monkeypatch.setattr(email_monitor,'get_accounts',lambda:[{'email_user':'one'},{'email_user':'two'}])
    store.set('email-review:held',{'reviewId':'held','state':'pending','subject':'Ambiguous application'})
    monkeypatch.setattr(email_monitor,'check_account_emails',lambda a,*args:{'account':a['email_user'],'state':'ok' if a['email_user']=='one' else 'failed','suggestions':[]})
    for _ in range(2):
        report=email_monitor.check_emails()
        assert report['status']=='partial' and report['suggestions'][0]['reviewId']=='held'


def test_build_health_detects_source_drift(tmp_path,monkeypatch):
    import hashlib
    monkeypatch.setattr(dashboard,'ROOT',tmp_path)
    root=tmp_path/'frontend';(root/'src').mkdir(parents=True);(root/'dist').mkdir()
    assert dashboard.build_health()['frontendStale']

    for name in ['src/App.vue','package.json','package-lock.json','index.html','vite.config.js']:
        (root/name).write_text('fixture',encoding='utf-8')
    digest=hashlib.sha256()
    for name in sorted(['src/App.vue','package.json','package-lock.json','index.html','vite.config.js']):
        digest.update((name+'\0').encode());digest.update(b'fixture')
    (root/'dist/index.html').write_text('compiled',encoding='utf-8')
    (root/'dist/build-info.json').write_text(json.dumps({'sourceSha256':digest.hexdigest(),'builtAt':'fixture'}),encoding='utf-8')
    assert not dashboard.build_health()['frontendStale']
    (root/'src/App.vue').write_text('changed',encoding='utf-8')
    assert dashboard.build_health()['frontendStale']


def test_equal_latest_email_conflicts_stay_in_durable_review(fixture,monkeypatch):
    path,store,job=fixture
    job['status']='applied';path.write_text(json.dumps([job]),encoding='utf-8')
    monkeypatch.setattr(email_monitor,'JOBS_FILE',str(path))
    monkeypatch.setattr(email_monitor,'get_accounts',lambda:[{}])
    def scan(account,jobs,updates,store):
        for i,target in enumerate(['offer','declined']):
            updates.append({'job_id':job['id'],'from_status':'applied','to_status':target,'eventAt':'2026-11-09T10:00:00+00:00','_processedKey':'email-message:tie'+str(i)})
        return {'state':'ok','suggestions':[]}
    monkeypatch.setattr(email_monitor,'check_account_emails',scan)
    report=email_monitor.check_emails()
    assert report['updates']==[] and len(report['suggestions'])==2
    assert read_jobs(path)[0]['status']=='applied'
