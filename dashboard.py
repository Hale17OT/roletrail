"""Dashboard projections and user actions. Legacy records are never silently migrated."""
import copy
import hashlib
import json
import re
import shutil
from pathlib import Path

from discovery.policy import evaluate, now_utc, parse_date, signature, canonical_url
from discovery.store import ROOT, STATE_DIR, LeadStore, PROTECTED, read_jobs, update_jobs

TRANSITIONS = {
    "saved": ["ready", "applied", "deleted"], "ready": ["saved", "applied", "deleted"],
    "applied": ["interviewing", "offer", "rejected", "declined"], "interviewing": ["offer", "rejected", "declined"],
    "offer": ["rejected", "declined"], "declined": [], "rejected": [], "deleted": []
}


def revision(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def candidate():
    return json.loads(((ROOT / "discovery/candidate.json") if (ROOT / "discovery/candidate.json").exists() else ROOT / "discovery/candidate.example.json").read_text(encoding="utf-8"))


def build_health():
    folder=ROOT/'frontend'
    index=folder/'dist/index.html'
    info=folder/'dist/build-info.json'
    if not index.is_file() or not info.is_file():
        return {'frontendBuilt':index.is_file(),'frontendStale':True,'builtAt':None}
    files=[p for p in (folder/'src').rglob('*') if p.is_file() and p.suffix in {'.vue','.js','.mjs','.css','.json'}]
    files += [folder/p for p in ['package.json','package-lock.json','index.html','vite.config.js']]
    digest=hashlib.sha256()
    for file in sorted(files,key=lambda p:p.relative_to(folder).as_posix()):
        digest.update((file.relative_to(folder).as_posix()+'\0').encode())
        digest.update(file.read_bytes())
    metadata=json.loads(info.read_text(encoding='utf-8'))
    return {'frontendBuilt':True,'frontendStale':digest.hexdigest()!=metadata.get('sourceSha256'),'builtAt':metadata.get('builtAt')}


def evidence_for(job, store=None):
    override = (store or LeadStore()).get("job-audit:" + job["id"])
    return override or job.get("sourceEvidence") or {}


def readiness(job, store=None):
    evidence = evidence_for(job, store)
    review = evidence.get('review') or {}
    if review.get('decision') == 'held':
        findings = review.get('unverified', [])
        return {'state': 'needs_recheck', 'label': 'Needs verification', 'canApply': False,
                'reasons': [f['detail'] for f in findings], 'ruleFindings': findings,
                'publishedAt': (evidence.get('lead') or {}).get('published_at'),
                'auditedAt': evidence.get('auditedAt')}
    lead = evidence.get("lead") or {}
    if not lead:
        return {"state": "needs_recheck", "label": "Needs verification", "reasons": ["No current official-source evidence"], "canApply": False}
    audit = evaluate(lead)
    stamp = parse_date(evidence.get("auditedAt"))
    age = (now_utc() - stamp[0]).total_seconds() if stamp else None
    if any(r in audit["reasons"] for r in ("outside_freshness_window", "expired", "closed")):
        state, label = "expired", "Posting expired or closed"
    elif audit["state"] == "rejected":
        state, label = "restricted", "Does not meet current criteria"
    elif audit["state"] != "verified" or age is None or not 0 <= age <= 86400:
        state, label = "needs_recheck", "Needs verification"
    else:
        state, label = "verified", "Verified for Ethiopia"
        if audit.get('checks',{}).get('visaSponsorshipInferred'):
            label='Verified · sponsorship inferred'
        elif audit.get('checks',{}).get('visaSponsored'):
            label='Verified · visa sponsored'
        elif audit.get('checks',{}).get('remoteEligibilityBasis')=='no_explicit_exclusion':
            label='Verified · remote eligibility inferred'
        if audit.get('checks',{}).get('experienceBand')=='stretch':
            label='Reviewed · experience stretch'
    reasons = audit["reasons"][:]
    from discovery.dossier import validate_dossier
    errors = validate_dossier(job)
    if signature(job.get('company'), job.get('title')) != signature(lead.get('company'), lead.get('title')) or canonical_url(job.get('applyUrl', '')) != canonical_url(lead.get('url', '')):
        errors.append('source_identity_changed')
    if errors and state == "verified":
        state, label = "needs_recheck", "Application material needs review"
        reasons.extend(errors)
    if age is None or age > 86400 or age < 0:
        reasons.append("official_audit_needs_refresh")
    return {"state": state, "label": label, "reasons": reasons,
            "canApply": state == "verified", "auditedAt": evidence.get("auditedAt"),
            "publishedAt": lead.get("published_at"), "sourceUrl": lead.get("url"),
            "checks": audit.get("checks", {}), "rankingMethod": evidence.get("rankingMethod", "Internal assessment; not a hiring probability")}


def geography(job, evidence):
    lead = evidence.get("lead") or {}
    location = lead.get("location") or job.get("region", "")
    work = (lead.get("workplace") or job.get("locationType", "")).lower()
    # Candidate-base text is not a job destination. Use the source location when available.
    local = bool(re.search(r"\baddis ababa\b", location, re.I)) and not re.search(r"remote|relocation|based in ethiopia", location, re.I)
    sponsored = bool(evidence.get("checks", {}).get("visaSponsored") or evidence.get('checks',{}).get('visaSponsorshipInferred')) or "visa sponsor" in work
    mode = "sponsored" if sponsored else "local" if local else "remote" if re.search(r"remote|telecommute", work) else "unknown"
    return {"arrangement": mode, "destination": location, "workplace": job.get("locationType", "Not verified")}


def project(job, store=None):
    result = copy.deepcopy(job)
    evidence = evidence_for(job, store)
    result["_meta"] = {"revision": revision(job), "eligibility": readiness(job, store),
                       "geography": geography(job, evidence), "actions": TRANSITIONS.get(job.get("status"), []),
                       "protected": job.get("status") in PROTECTED}
    return result


def project_jobs(path=None):
    store = LeadStore()
    return [project(j, store) for j in read_jobs(path)]


def cv_library(path=None):
    definitions = json.loads(((ROOT / "frontend/src/data/cvs.json") if (ROOT / "frontend/src/data/cvs.json").exists() else ROOT / "frontend/src/data/cvs.example.json").read_text(encoding="utf-8"))
    jobs = read_jobs(path)
    result = []
    for entry in definitions:
        cv = {k: entry[k] for k in ("fileName", "roleTitle", "shortLabel", "targetRole", "summary", "skills", "atsKeywords", "url") if k in entry}
        file = ROOT / "cvs" / cv["fileName"]
        cv["available"] = file.is_file()
        cv["version"] = hashlib.sha256(file.read_bytes()).hexdigest()[:12] if file.is_file() else "unavailable"
        cv["pairings"] = [{"id": j["id"], "title": j["title"], "company": j["company"], "status": j["status"]}
                          for j in jobs if j.get("status") != "deleted" and (j.get("matchedCv") or {}).get("fileName") == cv["fileName"]]
        cv["evidence"] = candidate()["projects"]
        cv["reviewNotice"] = entry.get('evidenceNotice') or "Historical CV content has not been independently validated. Review achievements before submitting. No ATS acceptance score is claimed."
        result.append(cv)
    return result


def add_event(job, kind, **fields):
    job.setdefault("activity", []).append({"type": kind, "at": now_utc().isoformat(), **fields})


def freeze_kit(job):
    file = ROOT / "cvs" / (job.get("matchedCv") or {}).get("fileName", "")
    if file.parent.resolve() != (ROOT / "cvs").resolve() or not file.is_file():
        raise ValueError("The selected CV file is missing")
    sha = hashlib.sha256(file.read_bytes()).hexdigest()
    destination = STATE_DIR / "submitted" / (sha + ".pdf")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(file, destination)
    return {"cvSha256": sha, "cvFileName": file.name, "cvUrl": "/api/kits/" + sha,
            "letter": job.get("tailoredLetter", ""), "pitch": job.get("keyPitch", ""),
            "recordedAt": now_utc().isoformat(), "confirmedByUser": True}


def mutate_job(job_id, payload, path=None):
    if not isinstance(payload, dict) or not payload.get("revision"):
        raise ValueError("Reload this record before saving")
    def mutate(jobs):
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job or revision(job) != payload["revision"]:
            raise ValueError("This record changed. Reload it before saving.")
        if job.get("status") == "deleted":
            raise ValueError("Blacklisted records are read-only")
        if payload.get('rebuildKit'):
            if job.get('status') in PROTECTED:
                raise ValueError('Historical application materials are preserved')
            evidence = evidence_for(job)
            lead, stamp = evidence.get('lead') or {}, parse_date(evidence.get('auditedAt'))
            if not stamp or not 0 <= (now_utc()-stamp[0]).total_seconds() <= 86400 or evaluate(lead)['state'] != 'verified':
                raise ValueError('Recheck the official source and resolve eligibility before rebuilding this draft')
            if signature(job['company'],job['title']) != signature(lead.get('company'),lead.get('title')) or canonical_url(job['applyUrl']) != canonical_url(lead.get('url')):
                raise ValueError('Source identity changed; this draft cannot be automatically rebuilt')
            from discovery.dossier import build_dossier, validate_dossier
            draft = build_dossier(lead, evaluate(lead), job['status'])
            if validate_dossier(draft):
                raise ValueError('The rebuilt draft is incomplete')
            previous = {k:copy.deepcopy(job.get(k)) for k in ('tailoredLetter','keyPitch','matchedCv')}
            preserved_id = job['id']
            job.update(draft)
            job['id'] = preserved_id
            add_event(job, 'kit_rebuilt', actor='user', previousKit=previous)
        target = payload.get("status")
        if target and target != job["status"]:
            if target not in TRANSITIONS.get(job["status"], []):
                raise ValueError("That transition is not allowed for this application")
            if target == "ready" and not readiness(job)["canApply"]:
                raise ValueError("Verify the live official posting before moving it to Ready")
            if target == "applied":
                if not readiness(job)["canApply"]:
                    raise ValueError("Verify eligibility and complete the application kit before submitting")
                if payload.get("confirmSubmission") is not True:
                    raise ValueError("Confirm that you submitted the application and the displayed kit")
                job["submittedKit"] = freeze_kit(job)
                job["appliedAt"] = now_utc().isoformat()
            old = job["status"]
            job["status"] = target
            if target == "deleted":
                job["deletedAt"] = now_utc().isoformat()
                job["deletionReason"] = str(payload.get("reason") or "Blacklisted by user")[:1000]
            if target in {"declined", "rejected"}:
                job["outcomeReason"] = str(payload.get("reason") or "Outcome recorded by user")[:1000]
                job['outcomeType'] = 'employer_rejected' if target=='rejected' else 'candidate_withdrew'
            add_event(job, "stage_changed", fromStatus=old, toStatus=target, reason=payload.get("reason", ""), actor="user")
        if "note" in payload:
            note = str(payload["note"]).strip()
            if not note or len(note) > 5000:
                raise ValueError("Notes must contain 1–5,000 characters")
            add_event(job, "note", text=note, actor="user")
        if "nextAction" in payload:
            action = payload["nextAction"]
            if not isinstance(action, dict):
                raise ValueError("Invalid next action")
            text, due = str(action.get("text", "")).strip(), str(action.get("due", "")).strip()
            if len(text) > 500 or (due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due)):
                raise ValueError("Use a short action and a valid date")
            if due:
                from datetime import date
                date.fromisoformat(due)
            job["nextAction"] = {"text": text, "due": due}
            add_event(job, "next_action", **job["nextAction"])
        if "cvFileName" in payload or "letter" in payload:
            if job.get("status") in PROTECTED:
                raise ValueError("Submitted material is preserved; this application is read-only for kit edits")
            if "cvFileName" in payload:
                chosen = next((c for c in cv_library(path) if c["fileName"] == payload["cvFileName"] and c["available"]), None)
                if not chosen:
                    raise ValueError("Choose an available CV from the library")
                job["matchedCv"] = {**{k: chosen[k] for k in ("fileName", "roleTitle", "shortLabel", "url")},
                                    "reason": "Selected by user", "diskPath": str(ROOT / "cvs" / chosen["fileName"])}
            if "letter" in payload:
                letter = str(payload["letter"]).strip()
                if not 50 <= len(letter) <= 30000:
                    raise ValueError("Cover letters must contain 50–30,000 characters")
                job["tailoredLetter"] = letter
            add_event(job, "kit_edited", actor="user")
        return project(job)
    return update_jobs(mutate, path)


def recheck(job_id, path=None, fetcher=None, store=None):
    from discovery.audit import inspect_source
    from discovery.http import Fetcher
    from discovery.sources import resolve_discovery
    store = store or LeadStore()
    job = next((j for j in read_jobs(path) if j["id"] == job_id and j.get("status") != "deleted"), None)
    if not job:
        raise ValueError("This job is unavailable")
    raw = {"title": job["title"], "company": job["company"], "description": job.get("description", ""),
           "url": job["applyUrl"], "source": "official_source_recheck", "official_verified": False}
    try:
        fetcher = fetcher or Fetcher(store, budget=8)
        raw = resolve_discovery(raw, fetcher)
        employers = json.loads((ROOT / "discovery/employers.json").read_text(encoding="utf-8"))
        raw = inspect_source(raw, fetcher, employers)
        audit = evaluate(raw)
    except Exception as exc:
        raw["official_verified"] = False
        audit = {"state": "needs_evidence", "auditedAt": now_utc().isoformat(),
                 "reasons": ["official_source_unavailable"], "errorType": type(exc).__name__}
    store.set("job-audit:" + job_id, {**audit, "lead": raw})
    # Audit metadata is external so protected objects and their submitted material remain intact.
    return project(job, store)


def integration_summary():
    from discovery.telegram_auth import connection_status
    config = json.loads((ROOT / "discovery/sources.json").read_text(encoding="utf-8"))
    store = LeadStore()
    with store.connect() as db:
        states = dict(db.execute("SELECT json_extract(audit,'$.state'),COUNT(*) FROM leads GROUP BY 1").fetchall())
        reasons = {}
        for (raw,) in db.execute("SELECT audit FROM leads WHERE json_extract(audit,'$.state')='needs_evidence'"):
            for reason in json.loads(raw).get("reasons", []):
                reasons[reason] = reasons.get(reason, 0) + 1
    return {"telegram": {**connection_status(), "configuredAdapter": config["telegram"]["adapter"]},
            "email": store.get("email:last_report", {"status": "not_checked"}),
            "opportunityReview": store.get("opportunity-review:last_report"),
            "evidenceTasks": {"states": states, "reasons": reasons},
            "rules": {"maxAgeHours": 168, "experience": "Junior / mid-level accepted (up to 5 years); otherwise 0–3 years", "domain": "Software engineering/development welcome; Power Platform / Dynamics preferred", "location": "Remote from Ethiopia, Addis Ababa, or verified visa sponsorship"}}
