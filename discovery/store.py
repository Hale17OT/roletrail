"""Raw evidence storage and serialized, loss-resistant dashboard writes."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .policy import canonical_url, identity, now_utc, signature

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".discovery"
PROTECTED = {"applied", "declined", "rejected", "interviewing", "offer"}
_thread_lock = threading.RLock()


def correct_employer_rejections(job_ids, path=None):
    """Explicit user correction only; retain original events and record when corrected."""
    def mutate(jobs):
        changed = []
        for job in jobs:
            if job['id'] not in job_ids or job.get('status') != 'declined':
                continue
            job.update(status='rejected', outcomeType='employer_rejected')
            job.setdefault('activity', []).append({
                'type': 'outcome_corrected', 'at': now_utc().isoformat(),
                'fromStatus': 'declined', 'toStatus': 'rejected',
                'actor': 'user_clarification',
                'reason': 'User confirmed the employer rejected this application; the candidate did not decline it.'})
            changed.append(job['id'])
        return changed
    return update_jobs(mutate, path)


class LeadStore:
    def __init__(self, path=None):
        self.path = Path(path or STATE_DIR / "leads.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS leads (key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    audit TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, created_at TEXT, payload TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, lead, audit):
        key, stamp = identity(lead), now_utc().isoformat()
        with self.connect() as db:
            old = db.execute("SELECT payload FROM leads WHERE key=?", (key,)).fetchone()
            provenance = set(lead.get("provenance", [])) | {lead.get("source", "unknown")}
            if old:
                provenance.update(json.loads(old[0]).get("provenance", []))
            lead = {**lead, "provenance": sorted(provenance)}
            db.execute("""INSERT INTO leads VALUES (?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET
                       payload=excluded.payload, audit=excluded.audit, last_seen=excluded.last_seen""",
                       (key, json.dumps(lead, ensure_ascii=False), json.dumps(audit), stamp, stamp))

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))

    def report(self, report):
        with self.connect() as db:
            db.execute("INSERT INTO runs(created_at,payload) VALUES (?,?)",
                       (now_utc().isoformat(), json.dumps(report)))
        self.set("last_report", report)

    def pending(self, limit=10):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM leads WHERE json_extract(audit,'$.state')='needs_evidence' ORDER BY last_seen LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def history(self, limit=20):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]


@contextmanager
def jobs_lock(path):
    """A separate lock inode survives atomic jobs.json replacement (Windows/POSIX)."""
    lock = Path(str(path) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock, open(lock, "a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 30
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Jobs file is busy")
                time.sleep(.05)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)


def read_jobs(path=None):
    path = Path(path or ROOT / "jobs.json")
    if path.resolve()==(ROOT/'jobs.json').resolve():
        from .job_database import read
        with jobs_lock(path):
            return read(STATE_DIR/'leads.sqlite3',path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def update_jobs(mutator, path=None):
    path = Path(path or ROOT / "jobs.json")
    with jobs_lock(path):
        if path.resolve()==(ROOT/'jobs.json').resolve():
            from .job_database import update
            return update(STATE_DIR/'leads.sqlite3',path,mutator)
        jobs = read_jobs(path)
        result = mutator(jobs)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return result


def blocked(job, existing):
    """All current identities prevent duplicates; tombstones also match signatures."""
    url = canonical_url(job.get("applyUrl") or job.get("url", ""))
    sig = signature(job.get("company"), job.get("title"))
    for old in existing:
        if job.get("id") and job["id"] == old.get("id"):
            return True
        if url and url in {canonical_url(old.get("applyUrl", "")), canonical_url(old.get("url", ""))}:
            return True
        if sig == signature(old.get("company"), old.get("title")):
            incoming_req = (job.get("sourceEvidence", {}).get("lead", {}) or job).get("source_id")
            old_req = (old.get("sourceEvidence", {}).get("lead", {}) or old).get("source_id")
            if old.get("status") == "deleted" or not incoming_req or not old_req or incoming_req == old_req:
                return True
    return False


def merge_verified(new_jobs, path=None):
    from .dossier import validate_dossier
    from .policy import evaluate, parse_date
    def merge(jobs):
        added = 0
        index = next((i for i, j in enumerate(jobs) if j.get("status") == "ready"), len(jobs))
        for job in new_jobs:
            # Re-evaluate timestamps and policy at write time, not just at collection time.
            lead = job.get("sourceEvidence", {}).get("lead", {})
            stamp = parse_date(job.get("sourceEvidence", {}).get("auditedAt"))
            if not stamp or not 0 <= (now_utc() - stamp[0]).total_seconds() <= 1800:
                continue
            if signature(job.get("company"), job.get("title")) != signature(lead.get("company"), lead.get("title")) or canonical_url(job.get("applyUrl", "")) != canonical_url(lead.get("url", "")):
                continue
            audit = evaluate(lead)
            if audit["state"] != "verified" or validate_dossier(job) or blocked(job, jobs):
                continue
            jobs.insert(index, job)
            index += 1
            added += 1
        return added
    return update_jobs(merge, path)


def merge_ui_snapshot(incoming, path=None):
    """Legacy full-array UI saves may change statuses, never replace server records."""
    if not isinstance(incoming, list) or any(not isinstance(j, dict) or not j.get("id") for j in incoming):
        raise ValueError("Expected a jobs array with IDs")
    wanted = {j["id"]: j for j in incoming}
    allowed = {"ready", "saved", "applied", "interviewing", "offer", "declined", "rejected", "deleted"}
    def merge(jobs):
        for old in jobs:
            new = wanted.get(old["id"])
            if not new or old.get("status") == "deleted":
                continue
            target = new.get("status")
            if target not in allowed:
                raise ValueError("Invalid job status")
            # Stale clients cannot revert/delete an application already in progress.
            if old.get("status") in PROTECTED and target != old.get("status"):
                continue
            if target != old.get("status") and target in {"ready", "applied"}:
                raise ValueError("Use the verified, explicit job-action endpoint for promotion or submission")
            from dashboard import TRANSITIONS, add_event
            if target != old.get('status'):
                if target not in TRANSITIONS.get(old.get('status'), []):
                    raise ValueError('Invalid stage transition')
                add_event(old, 'stage_changed', fromStatus=old['status'], toStatus=target, actor='legacy_client')
            old["status"] = target
            if target == "deleted":
                old["deletedAt"] = new.get("deletedAt") or now_utc().isoformat()
        return jobs
    return update_jobs(merge, path)


def apply_email_updates(updates, path=None):
    from .policy import parse_date
    def merge(jobs):
        applied = []
        by_id = {j["id"]: j for j in jobs}
        # Resolve each job from its latest event, never by IMAP iteration order.
        latest = {}
        ambiguous = set()
        for update in updates:
            # Older email adapters called employer rejections "declined".
            if update.get('to_status') == 'declined':
                update = {**update, 'to_status':'rejected'}
            stamp = parse_date(update.get("eventAt"))
            key = stamp[0].isoformat() if stamp else ""
            if update["job_id"] not in latest or key > latest[update["job_id"]][0]:
                latest[update["job_id"]] = (key, update)
                ambiguous.discard(update['job_id'])
            elif key == latest[update['job_id']][0] and update['to_status'] != latest[update['job_id']][1]['to_status']:
                ambiguous.add(update['job_id'])
        for _, update in latest.values():
            if update['job_id'] in ambiguous:
                continue
            old = by_id.get(update["job_id"])
            if not old or old.get("status") == "deleted":
                continue
            stamp = parse_date(update.get("eventAt"))
            previous = parse_date(old.get("lastEmailEventAt"))
            if not stamp or stamp[0] > now_utc() or old.get('status') in {'offer','declined','rejected','ready','saved'}:
                continue
            if stamp and previous and stamp[0] <= previous[0]:
                continue
            from dashboard import TRANSITIONS, add_event
            target = update["to_status"]
            if old.get('status') == update['from_status'] == target:
                old['lastEmailEventAt'] = stamp[0].isoformat()
                add_event(old, 'email_observed', eventAt=update['eventAt'], messageId=update.get('messageId',''), reason=update.get('reason',''))
                continue
            if old.get("status") == update["from_status"] and target in TRANSITIONS.get(old.get("status"), []):
                old["status"] = update["to_status"]
                if target == 'rejected':
                    old['outcomeType'] = 'employer_rejected'
                if stamp:
                    old["lastEmailEventAt"] = stamp[0].isoformat()
                add_event(old, "email_stage_changed", fromStatus=update["from_status"], toStatus=target,
                          eventAt=update.get("eventAt", ""), messageId=update.get("messageId", ""), reason=update.get("reason", ""))
                applied.append(update)
        return applied
    return update_jobs(merge, path)


def change_status(job_id, expected, target, path=None):
    allowed = {"ready", "saved", "applied", "interviewing", "offer", "declined", "rejected", "deleted"}
    if target not in allowed:
        raise ValueError("Invalid job status")
    def mutate(jobs):
        job = next((j for j in jobs if j["id"] == job_id), None)
        if not job or job.get("status") != expected or expected == "deleted":
            raise ValueError("Job changed since it was loaded; reload and try again")
        if expected in PROTECTED and target in {"ready", "saved", "deleted"}:
            raise ValueError("Protected applications cannot be reset or blacklisted")
        from dashboard import TRANSITIONS, readiness, add_event
        if target != expected and target not in TRANSITIONS.get(expected, []):
            raise ValueError("Invalid stage transition")
        if target == "ready" and target != expected and not readiness(job)["canApply"]:
            raise ValueError("Official source verification is required")
        if target == "applied" and target != expected:
            raise ValueError("Confirm submission using the job-action endpoint")
        if target != expected:
            add_event(job, 'stage_changed', fromStatus=expected, toStatus=target, actor='user')
        job["status"] = target
        if target == "deleted":
            job["deletedAt"] = now_utc().isoformat()
        return job
    return update_jobs(mutate, path)
