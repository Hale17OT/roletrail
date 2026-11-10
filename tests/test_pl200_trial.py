from datetime import timedelta
import pytest
from discovery.policy import evaluate,PL200_TRIAL,PL200_PREFERRED_TRIAL,pl200_requirement
from test_discovery import lead,NOW


def trial(**changes):
    return lead(search_profile=PL200_TRIAL,description='Build Power Apps and Dataverse. 2 years of experience. Fully remote worldwide. PL-200 certification is required.',**changes)


def test_trial_accepts_twelve_days_without_changing_standard_window():
    raw=trial(published_at=(NOW-timedelta(days=10)).isoformat())
    assert evaluate(raw,NOW)['state']=='verified'
    raw.pop('search_profile')
    assert 'outside_freshness_window' in evaluate(raw,NOW)['reasons']
    assert 'outside_freshness_window' in evaluate(trial(published_at=(NOW-timedelta(days=12,seconds=1)).isoformat()),NOW)['reasons']
    assert evaluate(trial(published_at='11 days ago'),NOW)['state']=='verified'
    assert 'outside_freshness_window' in evaluate(trial(published_at='12 days ago'),NOW)['reasons']


@pytest.mark.parametrize('text,kind',[
 ('PL-200 certification is required.','required'),
 ('Required certifications:\nPL-200 or PL-400.','required'),
 ('Required skills:\nPower Apps\nPL-200 preferred.','preferred'),
 ('Required skills:\nPreferred certifications:\nPL-200.','preferred'),
 ('Microsoft Certified: Power Platform Functional Consultant Associate required.','required'),
 ('Requirements:\nPower Apps\nDataverse\nJavaScript\nPL-200 certification.','required'),
 ('Must-haves:\nMicrosoft certifications:\nPL-400 and\nPL-200.','required'),
 ('Required Qualifications:\nSQL\nPreferred Certifications:\nPL-400\nAI-102\nPL-200.','preferred'),
 ('PL-200 is a bonus.','preferred'),('Certifications: PL-200.','mentioned')])
def test_certification_requirement_is_not_inferred_from_mention(text,kind):
    assert pl200_requirement(text)[0]['classification']==kind


def test_trial_needs_cross_border_remote_and_preserves_other_rules():
    raw=trial(location='Poland')
    raw['description']=raw['description'].replace('Fully remote worldwide.','Fully remote.')
    assert 'cross_border_remote_not_established' in evaluate(raw,NOW)['reasons']
    raw['description']+=' Remote across EMEA.'
    assert evaluate(raw,NOW)['state']=='verified'
    raw['applicant_locations']='Poland, Germany'
    assert 'domestic_only_eligibility' in evaluate(raw,NOW)['reasons']
    assert 'seniority' in evaluate(trial(title='Senior Power Platform Developer'),NOW)['reasons']
    raw=trial(location='Ethiopia');raw['description']=raw['description'].replace('Fully remote worldwide.','Remote from Ethiopia.')
    assert 'cross_border_remote_not_established' in evaluate(raw,NOW)['reasons']


def test_preferred_trial_changes_only_certification_gate():
    raw=trial(published_at=(NOW-timedelta(days=10)).isoformat())
    raw['description']=raw['description'].replace('is required','is preferred')
    assert 'pl200_not_explicitly_required' in evaluate(raw,NOW)['reasons']
    raw['search_profile']=PL200_PREFERRED_TRIAL
    audit=evaluate(raw,NOW)
    assert audit['state']=='verified' and audit['checks']['freshnessHours']==288
    assert audit['checks']['searchProfile']==PL200_PREFERRED_TRIAL
    raw['description']=raw['description'].replace('Fully remote worldwide.','Fully remote.')
    raw['location']='India'
    inferred=evaluate(raw,NOW)
    assert inferred['state']=='verified' and inferred['checks']['crossBorderEligibilityBasis']=='inferred_no_explicit_exclusion'
    assert not inferred['checks']['crossBorderRemoteExplicit']
    restricted={**raw,'description':raw['description']+' Candidates must reside in India.'}
    assert 'domestic_only_eligibility' in evaluate(restricted,NOW)['reasons']
    assert 'domestic_only_eligibility' in evaluate({**raw,'applicant_locations':'India'},NOW)['reasons']
    raw['description']=raw['description'].replace('is preferred','is listed')
    assert 'pl200_not_required_or_preferred' in evaluate(raw,NOW)['reasons']


def test_inferred_scope_still_needs_real_remote_evidence_and_no_explicit_exclusion():
    raw=trial(location='Germany',workplace='')
    raw['search_profile']=PL200_PREFERRED_TRIAL
    raw['description']='Build Power Apps. 2 years of experience. PL-200 preferred.'
    assert 'cross_border_remote_not_established' in evaluate(raw,NOW)['reasons']
    raw['description']+='\nLocation: Remote'
    assert evaluate(raw,NOW)['state']=='verified'
    assert evaluate(raw,NOW)['checks']['crossBorderEligibilityBasis']=='inferred_no_explicit_exclusion'
    assert 'residency_citizenship_or_clearance_restriction' in evaluate({**raw,'description':raw['description']+'\nUS residents only.'},NOW)['reasons']
    assert 'mandatory_foreign_presence' in evaluate({**raw,'description':raw['description']+'\nMust attend the office weekly.'},NOW)['reasons']


def test_pipeline_does_not_accept_a_twelve_day_standard_run():
    from discovery.pipeline import _run_discovery
    with pytest.raises(ValueError,match='selected search profile'):
        _run_discovery(hours=288,config={'default_search_profile':'standard'})


def test_trial_dossier_retains_window_through_merge_dashboard_and_recheck(tmp_path,monkeypatch):
    from discovery.policy import now_utc
    from discovery.dossier import build_dossier
    from discovery.store import merge_verified,LeadStore
    from discovery.review import review_one,assess
    from dashboard import readiness
    raw=trial(published_at=(now_utc()-timedelta(days=10)).isoformat())
    job=build_dossier(raw,evaluate(raw))
    path=tmp_path/'jobs.json';path.write_text('[]')
    assert merge_verified([job],path)==1
    store=LeadStore(tmp_path/'state.db')
    assert readiness(job,store)['canApply']
    fresh={k:v for k,v in raw.items() if k!='search_profile'}
    facts={'authority':'official','url':raw['url'],'httpStatus':200}
    monkeypatch.setattr('discovery.review.source_lead',lambda *args:(fresh,facts))
    assert review_one(job,[job],{},store)['decision']=='verified'
    raw['description']=raw['description'].replace('is required','is preferred')
    _,failed,_=assess(raw,facts)
    assert any(f['code']=='pl200_not_explicitly_required' and f['rule']==2 for f in failed)


def test_jobspy_receives_full_trial_window(tmp_path,monkeypatch):
    import jobspy
    import pandas as pd
    from discovery.sources import collect_jobspy
    from discovery.pipeline import load_config
    from discovery.store import LeadStore
    calls=[]
    def scrape(**kwargs):calls.append(kwargs);return pd.DataFrame()
    monkeypatch.setattr(jobspy,'scrape_jobs',scrape)
    list(collect_jobspy(load_config(),LeadStore(tmp_path/'db'),lambda *a,**k:None,
        queries=['PL-200'],sites=['indeed'],countries=['UK'],hours=288))
    assert calls[0]['hours_old']==288


def test_smartrecruiters_trial_does_not_drop_ten_day_old_metadata(monkeypatch):
    import json
    from discovery.sources import collect_ats
    from test_discovery import FakeFetcher
    monkeypatch.setattr('discovery.sources.now_utc',lambda:NOW)
    root='https://api.smartrecruiters.com/v1/companies/example/postings'
    stamp=(NOW-timedelta(days=10)).isoformat()
    listing={'content':[{'id':'ten-day','name':'Junior Power Platform Developer','releasedDate':stamp}],'totalFound':1}
    f=FakeFetcher({root+'?limit=100&offset=0':json.dumps(listing),root+'/ten-day':json.dumps({'name':'Junior Power Platform Developer','releasedDate':stamp})})
    assert len(list(collect_ats({'kind':'smartrecruiters','board':'example','company':'Example'},f,hours=288)))==1


@pytest.mark.parametrize('flag',['--trial-pl200','--trial-pl200-preferred'])
def test_cli_trial_defaults_are_remote_and_certification_focused(monkeypatch,flag):
    import harvester
    captured={}
    def run(**kwargs):
        captured.update(kwargs)
        return [],{'discovered':0,'verified':0,'added':0,'dryRun':True}
    monkeypatch.setattr(harvester,'run_discovery',run)
    monkeypatch.setattr(harvester,'log_harvest',lambda *args:None)
    harvester.main([flag,'--sources','feeds','--dry-run'])
    assert captured['hours']==288 and (captured['trial_pl200'] or captured['trial_pl200_preferred'])
    assert captured['queries']==['PL-200','Power Platform Functional Consultant','Power Apps PL-200']
    assert captured['config']['jobspy']['modes']==['remote']


def test_preferred_trial_audits_power_platform_teaser_before_rejecting_missing_certificate(tmp_path,monkeypatch):
    from discovery import pipeline,runs
    from discovery.policy import now_utc
    from discovery.store import LeadStore
    monkeypatch.setattr(runs,'STATE_DIR',tmp_path)
    raw=lead(description='Build Power Apps. Read the employer page for full requirements.',
             published_at='',official_verified=False,audit_method='',source='Fixture')
    monkeypatch.setattr(pipeline,'collect_feed',lambda *args:iter([raw]))
    monkeypatch.setattr(pipeline,'resolve_discovery',lambda raw,*args:raw)
    def inspect(raw,*args):
        return {**raw,'description':'Build Power Apps and Dataverse. 2 years of experience. Fully remote worldwide. PL-200 is a plus.',
                'published_at':(now_utc()-timedelta(days=10)).isoformat(),'official_verified':True,'audit_method':'live_jobposting_jsonld'}
    monkeypatch.setattr(pipeline,'inspect_source',inspect)
    cfg=pipeline.load_config();cfg['feeds']=[{'name':'Fixture'}]
    path=tmp_path/'jobs.json';path.write_text('[]')
    jobs,report=pipeline.run_discovery(sources=['feeds'],trial_pl200_preferred=True,
        config=cfg,employers=[],store=LeadStore(tmp_path/'db'),jobs_path=path)
    assert len(jobs)==1 and report['freshnessHours']==288
    assert jobs[0]['sourceEvidence']['lead']['search_profile']==PL200_PREFERRED_TRIAL
    from discovery.store import merge_verified
    from dashboard import readiness
    assert merge_verified(jobs,path)==1
    assert readiness(jobs[0],LeadStore(tmp_path/'db'))['canApply']


def test_telegram_trial_backfill_includes_older_posts_without_standard_cursor(tmp_path):
    from discovery.telegram import fetch_preview
    from discovery.store import LeadStore
    from test_discovery import FakeFetcher
    stamp=(NOW-timedelta(days=10)).isoformat()
    html='<div class="tgme_widget_message" data-post="example/12"><div class="tgme_widget_message_text">Power Platform Developer</div><time datetime="'+stamp+'"></time></div>'
    store=LeadStore(tmp_path/'db')
    store.set('telegram:example',{'last_id':99,'last_reconcile':NOW.timestamp()})
    f=FakeFetcher({'https://t.me/s/example':html})
    posts,report=fetch_preview('example',f,store,max_pages=1,now=NOW,hours=288)
    assert len(posts)==1 and report['reason']!='cursor_reached'
    assert store.get('telegram:example')['last_id']==99
