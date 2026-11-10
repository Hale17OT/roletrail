"""One orchestration path for CLI, scheduled cycles, Telegram and HTTP."""
import json
import re
import time
from collections import Counter
from pathlib import Path

from .audit import inspect_source
from .dossier import build_dossier, validate_dossier
from .http import BudgetExceeded, Fetcher
from .policy import DOMAIN, RELEVANT, SENIOR, PL200_CERT, PL200_TRIAL, PL200_PREFERRED_TRIAL, SKILLS_TRIAL, ALL_TRACKS_TRIAL, RECURRING_PROFILE, canonical_url, evaluate, now_utc
from .sources import (collect_ats, collect_feed, collect_jobspy, collect_page,
                      discover_employer, resolve_discovery, rotate)
from .store import LeadStore, blocked, merge_verified, read_jobs
from .telegram import fetch_mtproto, fetch_preview, parse_post

HERE = Path(__file__).resolve().parent


def load_config(path=None):
    return json.loads(Path(path or HERE / "sources.json").read_text(encoding="utf-8"))


def run_discovery(_locked=False, **kwargs):
    from .runs import run_sync
    if _locked:
        return _run_discovery(**kwargs)
    return run_sync(kwargs, _run_discovery)


def _run_discovery(*, sources=None, sync=False, status="ready", queries=None, sites=None,
                  countries=None, results=None, hours=168, request_budget=None,
                  store=None, fetcher=None, config=None, employers=None, jobs_path=None, checkpoint=None,
                  retry_only=False,pending_limit=None,trial_pl200=False,trial_pl200_preferred=False,trial_skills_first=False,trial_all_tracks=False):
    config = config or load_config()
    recurring=not any((trial_pl200,trial_pl200_preferred,trial_skills_first,trial_all_tracks)) and config.get('default_search_profile')==RECURRING_PROFILE
    trial_skills_first=trial_skills_first or trial_all_tracks or recurring
    trial_pl200=trial_pl200 or trial_pl200_preferred
    profile=RECURRING_PROFILE if recurring else ALL_TRACKS_TRIAL if trial_all_tracks else SKILLS_TRIAL if trial_skills_first else PL200_PREFERRED_TRIAL if trial_pl200_preferred else PL200_TRIAL if trial_pl200 else 'standard'
    if trial_pl200:hours=288
    if trial_skills_first:hours=720
    if status not in {"ready", "saved"}:
        raise ValueError("Discovery status must be ready or saved")
    if not 1 <= hours <= (720 if trial_skills_first else 288 if trial_pl200 else 168):
        raise ValueError("Freshness window exceeds the selected search profile")
    if results is not None and not 1 <= results <= 100:
        raise ValueError("Results per board must be between 1 and 100")
    if request_budget is not None and not 1 <= request_budget <= 1000:
        raise ValueError("HTTP request budget must be between 1 and 1000")
    config = config or load_config()
    employers = employers if employers is not None else json.loads((HERE / "employers.json").read_text(encoding="utf-8"))
    store = store or LeadStore()
    fetcher = fetcher or Fetcher(store, budget=request_budget or config["request_budget"])
    selected = [] if retry_only else sources or ["ats", "jobspy", "telegram", "feeds", "pages", "people"]
    unknown = set(selected) - {"ats", "jobspy", "telegram", "feeds", "pages", "people"}
    if unknown:
        raise ValueError(f"Unknown sources: {sorted(unknown)}")
    started = time.monotonic()
    report = {"startedAt": now_utc().isoformat(), "dryRun": not sync, "sources": [],
              'searchProfile':profile,'freshnessHours':hours,
              "funnel": dict.fromkeys(["fetched", "unique", "fresh", "eligible", "verified", "promoted"], 0),
              "reasons": {}, "bySource": {}, "added": 0, "discovered": 0, "verified": 0}
    counts = Counter()
    jobs, existing, seen = [], read_jobs(jobs_path), set()
    audits = 0

    def health(source, state, **fields):
        report["sources"].append({"source": source, "state": state, **fields})
        if checkpoint:
            checkpoint(report)

    def process(raw):
        nonlocal audits
        raw={**raw}
        if trial_pl200 or trial_skills_first:raw['search_profile']=profile
        else:raw.pop('search_profile',None)
        if checkpoint:
            checkpoint(report)
        report["funnel"]["fetched"] += 1
        source_counts = report["bySource"].setdefault(raw.get("source", "unknown"),
                         dict.fromkeys(["fetched", "unique", "verified", "held", "rejected"], 0))
        source_counts["fetched"] += 1
        raw = {**raw, "url": canonical_url(raw.get("url", ""))}
        if not raw["url"]:
            counts["invalid_source_url"] += 1
            return
        # Do not deduplicate unknown employer/title signatures against each other.
        key = raw["url"] + (":" + raw.get("source_id", "") if raw.get("source", "").startswith("telegram:") else "")
        if key in seen:
            counts["duplicate_in_run"] += 1
            return
        seen.add(key)
        probe = {"applyUrl": raw["url"], "title": raw.get("title"), "company": raw.get("company")}
        if blocked(probe, existing):
            counts["existing_or_blacklisted"] += 1
            store.record(raw, {"state": "rejected", "reasons": ["existing_or_blacklisted"]})
            return
        report["funnel"]["unique"] += 1
        source_counts["unique"] += 1
        # Cheap exclusions before expensive requests; missing text still gets an official fetch.
        native_complete=raw.get('official_verified') and raw.get('audit_method')=='live_ats_api'
        # A board/Telegram teaser can omit the certification at the bottom of
        # the employer page. Only complete native evidence (or an unrelated
        # technical track) can establish that this credential is absent.
        absent_cert=trial_pl200 and raw.get('description') and not PL200_CERT.search(raw['description'])
        can_skip_absent_cert=absent_cert and (native_complete or not DOMAIN.search(raw.get('title','')+' '+raw['description']))
        relevant_text=raw.get('title','')+' '+raw.get('description','')
        adjacent_trial=trial_skills_first and re.search(r'\b(?:sharepoint|microsoft 365|M365|business applications|CRM|workflow automation|copilot|AB-410)\b',relevant_text,re.I)
        if SENIOR.search(raw.get("title", "")) or (raw.get("description") and not RELEVANT.search(relevant_text) and not adjacent_trial) or can_skip_absent_cert:
            audit = evaluate(raw, hours=hours)
        elif raw.get('official_verified') and raw.get('audit_method')=='live_ats_api':
            # The collector has already fetched current employer API evidence.
            # Evaluating it requires no extra page request and must not consume
            # or be deferred by the independent HTML verification budget.
            audit = evaluate(raw,hours=hours)
        elif audits >= config["verification_budget"]:
            audit = {"state": "needs_evidence", "reasons": ["verification_budget_reached"]}
        else:
            audits += 1
            try:
                raw = resolve_discovery(raw, fetcher)
                raw = inspect_source(raw, fetcher, employers)
                audit = evaluate(raw, hours=hours)
            except Exception as exc:
                audit = {"state": "needs_evidence", "reasons": ["official_fetch_failed"], "error": str(exc)}
        checks = audit.get("checks", {})
        if checks.get("publication") and not any(r in audit["reasons"] for r in ("publication_date_unknown", "outside_freshness_window", "future_publication_date")):
            report["funnel"]["fresh"] += 1
        if audit["state"] == "verified":
            report["funnel"]["eligible"] += 1
            report["funnel"]["verified"] += 1
            dossier = build_dossier(raw, audit, status)
            errors = validate_dossier(dossier)
            if errors:
                audit = {**audit, "state": "needs_evidence", "reasons": errors}
            elif not blocked(dossier, existing + jobs):
                jobs.append(dossier)
        source_counts["verified" if audit["state"] == "verified" else "rejected" if audit["state"] == "rejected" else "held"] += 1
        counts.update(audit["reasons"])
        store.record(raw, audit)

    def consume(label, iterator):
        count = 0
        try:
            for lead in iterator:
                process(lead)
                count += 1
            health(label, "ok", fetched=count)
        except BudgetExceeded as exc:
            health(label, "incomplete", fetched=count, reason=str(exc))
        except Exception as exc:
            health(label, "error", fetched=count, reason=str(exc))

    # Always disclose missing mandated coverage, even when a different source set is selected.
    health("google_jobs", "unavailable", reason=config["jobspy"]["google_note"])
    # Revisit previously incomplete evidence without letting it consume the entire audit budget.
    retried = 0
    for pending in store.pending(limit=pending_limit or min(10, config["verification_budget"] // 4)):
        source = pending.get("source", "")
        family = "telegram" if source.startswith("telegram:") else "ats" if source.split(":")[0] in {"ashby", "lever", "greenhouse", "smartrecruiters"} else "jobspy" if source.split(":")[0] in {"indeed", "glassdoor", "zip_recruiter", "linkedin"} else "feeds" if source in {f["name"] for f in config["feeds"]} else "pages"
        if retry_only or family in selected:
            process({**pending, "official_verified": False})
            retried += 1
    report["retriedPending"] = retried
    if retry_only:health('pending_evidence','ok',fetched=retried)
    # Reserve coverage for hiring posts before large ATS registries consume
    # the shared HTTP budget. Author/role verification remains mandatory.
    if 'people' in selected:
        from .people_posts import collect
        consume('people:linkedin',collect(config,fetcher,store,health,queries))
    if "ats" in selected:
        for board in config.get("boards", []):
            consume(f"{board['kind']}:{board['company']}", collect_ats({**board,'include_older':recurring}, fetcher, hours=hours))
        targets = rotate(employers, store, "employer_cursor", config["employers_per_run"])
        report["employerRegistry"] = {"seeds": len(employers), "selectedThisRun": len(targets),
                                      "note": "Career URLs are discovery seeds; linked ATS boards are verified on fetch."}
        for employer in targets:
            try:
                boards, embedded = discover_employer(employer, fetcher, store)
                health("employer:" + employer["name"], "ok" if boards or embedded else "needs_adapter",
                       boards=len(boards), careerUrl=employer["careers_url"])
                for lead in embedded:
                    process(lead)
                for board in boards:
                    adapter = {**board["adapter"], "company": employer["name"], 'include_older':recurring}
                    consume(f"{adapter['kind']}:{employer['name']}", collect_ats(adapter, fetcher, hours=hours))
            except Exception as exc:
                health("employer:" + employer["name"], "error", reason=str(exc))
    if "jobspy" in selected:
        consume("jobspy", collect_jobspy(config, store, health, queries, sites, countries, results, hours))
    if "telegram" in selected:
        cfg = config["telegram"]
        for channel in rotate(cfg["channels"], store, "channel_cursor", cfg["channels_per_run"]):
            handle = channel["handle"]
            try:
                posts, summary = fetch_mtproto(handle, store, hours=hours) if cfg["adapter"] == "mtproto" else fetch_preview(handle, fetcher, store, cfg["max_pages"],hours=hours)
                health("telegram:" + handle, **summary, ownership=channel.get("ownership", "unknown"))
                parsed = 0
                for post in posts:
                    for lead in parse_post(post):
                        process(lead)
                        parsed += 1
                report["sources"][-1]["parsed"] = parsed
            except Exception as exc:
                health("telegram:" + handle, "error", reason=str(exc))
    if "feeds" in selected:
        for feed in config["feeds"]:
            consume(feed["name"], collect_feed(feed, fetcher))
    if "pages" in selected:
        for page in config["discovery_pages"]:
            consume(page["name"], collect_page(page, fetcher))
    if sync and jobs:
        if checkpoint:
            checkpoint(report)
        report["added"] = merge_verified(jobs, jobs_path)
    report["funnel"]["promoted"] = report["added"]
    report["reasons"] = dict(counts.most_common())
    report["discovered"] = report["funnel"]["unique"]
    report["verified"] = len(jobs)
    report["httpRequests"] = fetcher.requests
    report["httpRequestScope"] = "Discovery fetcher only; JobSpy and MTProto internal traffic is not exposed"
    report["elapsedSeconds"] = round(time.monotonic() - started, 2)
    report["finishedAt"] = now_utc().isoformat()
    store.report(report)
    return jobs, report
