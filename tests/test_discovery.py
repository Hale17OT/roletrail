import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from discovery.audit import inspect_source, job_postings
from discovery.dossier import build_dossier, validate_dossier
from discovery.pipeline import load_config, run_discovery
from discovery.policy import canonical_url, evaluate, now_utc, parse_date
from discovery.sources import ats_board, collect_ats, collect_jobspy
from discovery.store import LeadStore, apply_email_updates, change_status, merge_ui_snapshot, merge_verified, read_jobs
from discovery.telegram import fetch_preview, parse_post, parse_preview

NOW = datetime(2026, 11, 9, 15, tzinfo=timezone.utc)


def lead(**kwargs):
    return {"title": "Junior Power Platform Developer", "company": "Example",
            "description": "Build Power Apps and Dataverse integrations. 2 years of experience. Remote worldwide.",
            "url": "https://jobs.ashbyhq.com/example/123", "location": "Worldwide", "workplace": "Remote",
            "published_at": (NOW - timedelta(hours=12)).isoformat(), "source": "ashby:Example",
            "official_verified": True, "audit_method": "live_ats_api", **kwargs}


@pytest.mark.parametrize("changes,reason", [
    ({"title": "Senior Power Platform Developer"}, "seniority"),
    ({"title": "Lead Consultant"}, "seniority"),
    ({"title": "Manager, Business Applications"}, "seniority"),
    ({"title":"Power Platform Developer", "description": "Power Apps: minimum 4 years of professional experience."}, "experience_above_three_years"),
    ({"title":"Power Platform Developer", "description": "Power Apps requires 3–5 years of experience."}, "experience_above_three_years"),
    ({"title": "Junior Accountant", "description": "Bookkeeping and payroll. 2 years of experience. Remote worldwide."}, "no_relevant_technical_alignment"),
    ({"description": "Power Apps, US citizens only. Remote worldwide."}, "residency_citizenship_or_clearance_restriction"),
    ({"description": "Power Apps, active security clearance required. Remote worldwide."}, "residency_citizenship_or_clearance_restriction"),
    ({"published_at": ""}, "publication_date_unknown"),
    ({"published_at": "not a date"}, "publication_date_unknown"),
    ({"published_at": (NOW - timedelta(hours=169)).isoformat()}, "outside_freshness_window"),
    ({"published_at": (NOW + timedelta(days=1)).isoformat()}, "future_publication_date"),
    ({"applicant_locations": "United Kingdom"}, "domestic_only_eligibility"),
    ({"official_verified": False}, "official_source_not_verified"),
    ({"closed": True}, "closed"),
    ({"valid_through": (NOW - timedelta(days=1)).isoformat()}, "expired")
])
def test_eligibility_rejects_or_holds(changes, reason):
    audit = evaluate(lead(**changes), NOW)
    assert reason in audit["reasons"]
    assert audit["state"] != "verified"


def test_remote_and_addis_and_sponsorship():
    assert evaluate(lead(), NOW)["state"] == "verified"
    assert evaluate(lead(location="Addis Ababa, Ethiopia", workplace="On-site",
                         description="Power Apps; 1 year of experience."), NOW)["state"] == "verified"
    sponsored = lead(location="Berlin, Germany", workplace="Hybrid",
                     description="Power Apps, 2 years of experience. We provide work visa sponsorship.")
    assert evaluate(sponsored, NOW)["state"] == "verified"
    sponsored["description"] += " Visa sponsorship is not available."
    assert evaluate(sponsored, NOW)["state"] != "verified"


def test_date_only_boundary_is_conservative():
    assert evaluate(lead(published_at="2026-11-02"), NOW)["state"] == "rejected"
    assert parse_date("2026-11-09T15:00:00") is None
    assert parse_date("garbage") is None


@pytest.mark.parametrize("text", ["Minimum four years of experience with Power Apps.", "Power Apps: requires 5 years.", "Power Apps: minimum three (3) to five (5) years of experience."])
def test_experience_wording_cannot_bypass_gate(text):
    assert "experience_above_three_years" in evaluate(lead(title="Power Platform Developer",description=text), NOW)["reasons"]


def test_two_year_minimum_does_not_crash():
    assert evaluate(lead(description="Power Apps, minimum 2 years of experience. Remote worldwide."), NOW)["state"] == "verified"


@pytest.mark.parametrize("text", ["Power Apps. Remote UK only. We operate worldwide.", "Power Apps. Must have the right to work in Canada. Remote worldwide.", "Power Apps. US citizenship is required. Remote worldwide."])
def test_domestic_restrictions_override_worldwide_marketing(text):
    assert evaluate(lead(description=text), NOW)["state"] != "verified"


def test_foreign_office_requirement_overrides_remote_label():
    assert "mandatory_foreign_presence" in evaluate(lead(description="Power Apps. Remote worldwide, but must attend the office weekly."), NOW)["reasons"]


def test_worldwide_customers_do_not_prove_global_hiring():
    raw = lead(location="United States", description="We serve customers worldwide. Power Apps. 2 years of experience.")
    assert evaluate(raw, NOW)["state"] == "verified"  # Relaxed remote eligibility; no explicit restriction.
    raw["description"] += " We welcome applicants worldwide."
    assert evaluate(raw, NOW)["state"] == "verified"


def test_canonical_url_preserves_requisition_parameters():
    assert canonical_url("https://Jobs.Example.com/apply?job=123&utm_source=tg#apply") == "https://jobs.example.com/apply?job=123"
    assert canonical_url("mailto:jobs@example.com") == ""


def test_dossier_complete_and_truthful():
    raw = lead()
    job = build_dossier(raw, evaluate(raw, NOW))
    assert not validate_dossier(job)
    assert "Example Applicant" in job["keyPitch"]
    assert "+1 202 555 0100" in job["tailoredLetter"]
    assert "90%" not in job["tailoredLetter"]
    assert job["description"] == raw["description"]
    assert job["payoutMethod"] == "Not specified in posting"


def preview(message_id=123, date="2026-11-09T10:00:00+00:00", more=None):
    return f'''<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="channel/{message_id}">
      <div class="tgme_widget_message_text">Job Title: <b>Junior Power Apps Developer</b><br>
      Company: Example<br>Location: Addis Ababa<br>Power Apps; 2 years of experience.
      <a href="https://jobs.ashbyhq.com/example/123?utm_source=tg">Apply here</a></div>
      <a><time datetime="{date}"></time></a></div></div>''' + (f'<a class="tme_messages_more" data-before="{more}">More</a>' if more else "")


def test_telegram_preserves_hidden_links_and_inline_fields():
    posts, before = parse_preview(preview(more=100), "channel")
    assert before == "100"
    assert posts[0]["links"] == ["https://jobs.ashbyhq.com/example/123"]
    parsed = parse_post(posts[0])[0]
    assert parsed["title"] == "Junior Power Apps Developer"
    assert parsed["company"] == "Example"
    assert parsed["url"] == "https://jobs.ashbyhq.com/example/123"
    assert parsed["published_at"] == ""  # Repost date is not publication proof.


def test_telegram_never_invents_employer_or_locality():
    post = {"text": "Junior Power Apps Developer", "postId": "local/1", "url": "https://t.me/local/1", "datetime": "bad"}
    parsed = parse_post(post, is_local=True)[0]
    assert parsed["company"] == parsed["location"] == parsed["published_at"] == ""


def test_telegram_amharic_and_multiple_jobs():
    posts = {"postId": "local/1", "url": "https://t.me/local/1", "text": "የስራ መደብ፦ Junior Power Apps Developer\nድርጅት፦ Example\nየስራ ቦታ፦ Addis Ababa\nJob Title: Junior D365 Consultant\nCompany: Other"}
    parsed = parse_post(posts)
    assert len(parsed) == 2
    assert parsed[0]["title"] == "Junior Power Apps Developer"
    assert parsed[0]["company"] == "Example"
    assert parsed[0]["location"] == "Addis Ababa"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.requests = pages, 0

    def get(self, url, **kwargs):
        self.requests += 1
        return {"url": url, "text": self.pages[url], "status": 200}

    def json(self, url, **kwargs):
        return json.loads(self.get(url)["text"])


def test_smartrecruiters_fetches_software_details_without_power_platform():
    root='https://api.smartrecruiters.com/v1/companies/example/postings'
    fetcher=FakeFetcher({root+'?limit=100&offset=0':json.dumps({'totalFound':2,'content':[
        {'id':'one','name':'Junior Software Engineer'},{'id':'two','name':'Senior Software Engineer'}]}),
        root+'/one':json.dumps({'name':'Junior Software Engineer','jobAd':{'sections':{'description':{'text':'Python, TypeScript. Build software.'}}},
                               'location':{'remote':True},'releasedDate':NOW.isoformat()})})
    rows=list(collect_ats({'kind':'smartrecruiters','board':'example','company':'Example'},fetcher))
    assert len(rows)==1 and rows[0]['title']=='Junior Software Engineer'


def test_telegram_budget_is_reported_and_resumed(tmp_path):
    store = LeadStore(tmp_path / "state.db")
    fetcher = FakeFetcher({"https://t.me/s/channel": preview(more=100)})
    posts, report = fetch_preview("channel", fetcher, store, max_pages=1, now=NOW)
    assert not report["complete"]
    assert store.get("telegram:channel")["resume"]["before"] == "100"
    fetcher.pages["https://t.me/s/channel?before=100"] = preview(99, "2026-11-01T10:00:00+00:00")
    _, report = fetch_preview("channel", fetcher, store, max_pages=2, now=NOW)
    assert report["complete"]
    assert store.get("telegram:channel")["last_id"] == 123


def test_telegram_bad_dates_and_unavailable_preview(tmp_path):
    store = LeadStore(tmp_path / "state.db")
    fetcher = FakeFetcher({"https://t.me/s/channel": preview(date="bad")})
    posts, _ = fetch_preview("channel", fetcher, store, now=NOW)
    assert posts[0]["datetime"] == "bad"
    fetcher.pages["https://t.me/s/channel"] = "<html>Channel unavailable</html>"
    _, report = fetch_preview("channel", fetcher, store, now=NOW)
    assert report["state"] == "incomplete"


def test_telegram_fetch_failure_keeps_partial_results_and_cursor(tmp_path):
    store = LeadStore(tmp_path / "state.db")
    fetcher = FakeFetcher({"https://t.me/s/channel": preview(more=100)})
    posts, report = fetch_preview("channel", fetcher, store, max_pages=5, now=NOW)
    assert len(posts) == 1 and not report["complete"]
    assert store.get("telegram:channel")["resume"]["before"] == "100"


@pytest.mark.parametrize("url,kind,board", [
    ("https://jobs.lever.co/example/123", "lever", "example"),
    ("https://job-boards.greenhouse.io/example/jobs/123", "greenhouse", "example"),
    ("https://jobs.ashbyhq.com/example/123", "ashby", "example"),
    ("https://careers.smartrecruiters.com/Example", "smartrecruiters", "Example")
])
def test_board_discovery(url, kind, board):
    assert ats_board(url)["kind"] == kind
    assert ats_board(url)["board"] == board


def test_greenhouse_updated_date_is_not_publication():
    url = "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"
    fetcher = FakeFetcher({url: json.dumps({"jobs": [{"id": 123, "title": "Junior Power Apps Developer", "content": "Power Apps", "absolute_url": "https://job-boards.greenhouse.io/example/jobs/123", "updated_at": NOW.isoformat()}]})})
    result = list(collect_ats({"kind": "greenhouse", "board": "example", "company": "Example"}, fetcher))
    assert result[0]["published_at"] == ""
    assert not result[0]["official_verified"]


def test_ashby_uses_live_api_fields():
    url = "https://api.ashbyhq.com/posting-api/job-board/example?includeCompensation=true"
    fetcher = FakeFetcher({url: json.dumps({"jobs": [{"title": "Junior Power Apps Developer", "descriptionPlain": "Power Apps remote worldwide", "jobUrl": "https://jobs.ashbyhq.com/example/123", "publishedAt": NOW.isoformat(), "workplaceType": "Remote", "location": "Worldwide"}]})})
    fetcher.pages['https://jobs.ashbyhq.com/example/123'] = '<script type="application/ld+json">'+json.dumps({'@type':'JobPosting','title':'Junior Power Apps Developer','jobLocationType':'TELECOMMUTE'})+'</script>'
    result = list(collect_ats({"kind": "ashby", "board": "example", "company": "Example"}, fetcher))[0]
    assert result["published_at"] == NOW.isoformat()
    assert result["official_verified"]


def test_jobposting_audit_does_not_accept_generic_careers_page():
    raw = lead(official_verified=False)
    fetcher = FakeFetcher({raw["url"]: "<html>Remote careers worldwide. Junior Power Platform Developer</html>"})
    assert not inspect_source(raw, fetcher, [])["official_verified"]


def test_http_200_with_closed_job_message_is_rejected():
    raw = lead(official_verified=False)
    posting = {"@type": "JobPosting", "title": raw["title"], "hiringOrganization": {"name": "Example"},
               "description": raw["description"], "datePosted": raw["published_at"], "jobLocationType": "TELECOMMUTE"}
    fetcher = FakeFetcher({raw["url"]: '<p>This position has been filled.</p><script type="application/ld+json">' + json.dumps(posting) + '</script>'})
    assert "closed" in evaluate(inspect_source(raw, fetcher, []), NOW)["reasons"]


def test_live_jsonld_overrides_discovery_claims():
    raw = lead(official_verified=False)
    posting = {"@type": "JobPosting", "title": raw["title"], "hiringOrganization": {"name": "Example"},
               "description": "Power Apps; 6 years of experience", "datePosted": "2020-01-01", "jobLocationType": "TELECOMMUTE"}
    fetcher = FakeFetcher({raw["url"]: '<script type="application/ld+json">' + json.dumps({"@graph": [posting]}) + '</script>'})
    audited = inspect_source(raw, fetcher, [])
    assert audited["published_at"] == "2020-01-01"
    assert "experience_above_five_years" in evaluate(audited, NOW)["reasons"]


def test_merge_preserves_protected_records_and_blacklists(tmp_path):
    path = tmp_path / "jobs.json"
    raw = lead(published_at=(now_utc() - timedelta(hours=1)).isoformat())
    job = build_dossier(raw, evaluate(raw))
    protected = [{"id": s, "title": s, "company": "Old", "status": s, "custom": {"keep": True}} for s in ["applied", "declined", "interviewing", "offer"]]
    path.write_text(json.dumps(protected), encoding="utf-8")
    assert merge_verified([job], path) == 1
    assert read_jobs(path)[:4] == protected
    assert merge_verified([job], path) == 0
    tombstone = {**job, "id": "different-id", "status": "deleted", "deletedAt": NOW.isoformat()}
    path.write_text(json.dumps(protected + [tombstone]), encoding="utf-8")
    assert merge_verified([job], path) == 0


def test_ui_snapshot_and_email_compare_and_swap(tmp_path):
    path = tmp_path / "jobs.json"
    records = [{"id": "applied", "status": "interviewing", "description": "preserve"}, {"id": "new", "status": "ready"}]
    path.write_text(json.dumps(records), encoding="utf-8")
    merge_ui_snapshot([{"id": "applied", "status": "applied", "description": "stale"}], path)
    assert read_jobs(path) == records
    assert not apply_email_updates([{"job_id": "applied", "from_status": "applied", "to_status": "declined"}], path)
    assert len(read_jobs(path)) == 2
    change_status("applied", "interviewing", "offer", path)
    with pytest.raises(ValueError):
        change_status("applied", "interviewing", "declined", path)


def test_blacklist_signature_blocks_changed_source(tmp_path):
    from discovery.store import blocked
    old = [{"id": "old", "company": "Example", "title": "Junior Power Apps Developer", "status": "deleted", "applyUrl": "https://old.example/job"}]
    assert blocked({"id": "new", "company": "EXAMPLE", "title": "Junior Power Apps Developer", "applyUrl": "https://new.example/job"}, old)


def test_jobspy_queries_do_not_repeat_linkedin_per_country(tmp_path, monkeypatch):
    import pandas as pd
    import jobspy
    calls = []
    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame()
    monkeypatch.setattr(jobspy, "scrape_jobs", scrape_jobs)
    config = load_config()
    list(collect_jobspy(config, LeadStore(tmp_path / "state.db"), lambda *a, **kw: None,
                       queries=["Power Apps"], sites=["linkedin", "zip_recruiter"], countries=["UK", "Germany"]))
    assert len(calls) == 2


@pytest.mark.parametrize('software',[False,True])
def test_pipeline_dry_run_does_not_modify_jobs(tmp_path, monkeypatch, software):
    import discovery.pipeline as pipeline
    import discovery.runs as runs
    monkeypatch.setattr(runs, 'STATE_DIR', tmp_path)
    raw = lead(published_at=(now_utc() - timedelta(hours=1)).isoformat())
    raw['responsibility_review']={'approved':True,'basis':'Fixture manual fit review','gaps':[]}
    raw['open_check']={'active':True,'identity_matched':True,'source_url':raw['url'],'application_url':raw['url']+'/apply',
        'checked_at':now_utc().isoformat(),'evidence':'Exact live fixture application'}
    if software:
        raw.update(title='Junior Software Engineer',description='Python, TypeScript, REST APIs. 2 years of experience. Remote worldwide.')
    monkeypatch.setattr(pipeline, "collect_feed", lambda *args: iter([raw]))
    monkeypatch.setattr(pipeline, "resolve_discovery", lambda raw, _: raw)
    config = load_config()
    config["feeds"] = [{"name": "Fixture"}]
    path = tmp_path / "jobs.json"
    path.write_text("[]", encoding="utf-8")
    before = path.read_bytes()
    jobs, report = run_discovery(sources=["feeds"], config=config, store=LeadStore(tmp_path / "state.db"),
                                 fetcher=FakeFetcher({}), jobs_path=path)
    assert report["verified"] == len(jobs) == 1
    assert report["added"] == 0 and report["dryRun"]
    assert path.read_bytes() == before
