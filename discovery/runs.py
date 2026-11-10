"""Durable search observation and one cross-process search owner (API and CLI)."""
import json
import os
import threading
import uuid
import hashlib
from contextlib import contextmanager
from .store import LeadStore, STATE_DIR
from .policy import now_utc
PROCESS_TOKEN = uuid.uuid4().hex


class Cancelled(BaseException):
    pass


@contextmanager
def search_lock():
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_DIR / 'search.lock', 'a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise ValueError('A search is already running in another window or process')
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def listing(store=None):
    store = store or LeadStore()
    with store.connect() as db:
        rows = db.execute("SELECT payload FROM state WHERE key LIKE 'search-run:%'").fetchall()
    runs = [json.loads(r[0]) for r in rows]
    for run in runs:
        if run['state'] in ('running', 'cancelling') and run.get('ownerToken') != PROCESS_TOKEN:
            try:
                # The OS lock, rather than PID reuse, establishes whether an owner survived.
                with search_lock():
                    run.update(state='interrupted', finishedAt=now_utc().isoformat())
                    store.set('search-run:' + run['id'], run)
            except ValueError:
                pass
    represented={r.get('progress',{}).get('startedAt') for r in runs}
    for report in store.history(20):
        if report.get('startedAt') and report['startedAt'] not in represented:
            runs.append({'id':'report-'+hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()[:12],
                         'state':'completed','origin':'historical report','startedAt':report['startedAt'],
                         'finishedAt':report.get('finishedAt'),'params':{'sync':not report.get('dryRun',True)},'progress':report})
    return sorted(runs, key=lambda r: r['startedAt'], reverse=True)[:30]


def start(params, runner, store=None):
    store = store or LeadStore()
    # Acquire before returning 202, closing the duplicate-start race.
    guard = search_lock()
    guard.__enter__()
    run = {'id': uuid.uuid4().hex, 'state': 'running', 'owner': os.getpid(), 'ownerToken': PROCESS_TOKEN,
           'startedAt': now_utc().isoformat(), 'params': params, 'progress': {'sources': [], 'funnel': {}}}
    store.set('search-run:' + run['id'], run)
    def checkpoint(report):
        key = 'search-run:' + run['id']
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = json.loads(db.execute('SELECT payload FROM state WHERE key=?', (key,)).fetchone()[0])
            if current.get('cancelRequested'):
                raise Cancelled()
            run['progress'] = json.loads(json.dumps(report))
            run['updatedAt'] = now_utc().isoformat()
            db.execute('UPDATE state SET payload=? WHERE key=?', (json.dumps(run), key))
    def worker():
        try:
            _, report = runner(**params, store=store, checkpoint=checkpoint, _locked=True)
            run.update(state='completed', progress=report)
        except Cancelled:
            run['state'] = 'cancelled'
        except Exception as exc:
            run.update(state='failed', error=str(exc))
        finally:
            run['finishedAt'] = now_utc().isoformat()
            store.set('search-run:' + run['id'], run)
            guard.__exit__(None, None, None)
    threading.Thread(target=worker, daemon=True).start()
    return run


def cancel(run_id, store=None):
    store = store or LeadStore()
    key = 'search-run:' + run_id
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT payload FROM state WHERE key=?', (key,)).fetchone()
        if not row:
            raise ValueError('Search not found')
        run = json.loads(row[0])
        if run['state'] in ('running', 'cancelling'):
            run.update(state='cancelling', cancelRequested=True)
            db.execute('UPDATE state SET payload=? WHERE key=?', (json.dumps(run), key))
    return run


def run_sync(params, runner):
    """CLI searches share the same observable lifecycle and cancellation contract."""
    with search_lock():
        store = params.get('store') or LeadStore()
        public = {k:v for k,v in params.items() if k in {'sources','sync','status','queries','sites','countries','hours','results','request_budget','trial_pl200','trial_pl200_preferred','trial_skills_first','trial_all_tracks'}}
        run = {'id': uuid.uuid4().hex, 'state':'running', 'owner':os.getpid(), 'ownerToken':PROCESS_TOKEN,
               'origin':'cli', 'params':public, 'startedAt':now_utc().isoformat(), 'progress':{'sources':[], 'funnel':{}}}
        key = 'search-run:' + run['id']
        store.set(key, run)
        original = params.get('checkpoint')
        def checkpoint(report):
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = json.loads(db.execute('SELECT payload FROM state WHERE key=?',(key,)).fetchone()[0])
                if current.get('cancelRequested'):
                    raise Cancelled()
                run['progress'] = json.loads(json.dumps(report))
                run['updatedAt'] = now_utc().isoformat()
                db.execute('UPDATE state SET payload=? WHERE key=?',(json.dumps(run),key))
            if original:
                original(report)
        try:
            result = runner(**{**params, 'checkpoint':checkpoint})
            run.update(state='completed',progress=result[1])
            return result
        except Cancelled:
            run['state']='cancelled'
            return [], {**run['progress'], 'status':'cancelled'}
        except BaseException as exc:
            run.update(state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error=type(exc).__name__)
            raise
        finally:
            run['finishedAt']=now_utc().isoformat()
            store.set(key,run)
