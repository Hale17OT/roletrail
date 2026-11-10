"""Transactional job storage, lossless migration, and local recovery snapshots."""
import gzip
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def connect(path):
    db=sqlite3.connect(path,timeout=30)
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, position INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS job_storage_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.commit()
    return db


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''


def export(db,json_path):
    records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs ORDER BY position')]
    temporary=json_path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(records,indent=2,ensure_ascii=False),encoding='utf-8')
    temporary.replace(json_path)
    db.execute('INSERT OR REPLACE INTO job_storage_meta VALUES (?,?)',('json_export_sha256',digest(json_path)))
    db.commit()


def initialize(db,json_path):
    if db.execute("SELECT 1 FROM job_storage_meta WHERE key='migrated'").fetchone():return
    db.execute('PRAGMA journal_mode=WAL')
    records=json.loads(json_path.read_text(encoding='utf-8-sig')) if json_path.exists() else []
    ids=[j['id'] for j in records]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate job IDs: migration cancelled')
    if json_path.exists():
        backup=json_path.parent/'.discovery/backups/pre-sqlite-jobs.json'
        backup.parent.mkdir(parents=True,exist_ok=True)
        if not backup.exists():shutil.copyfile(json_path,backup)
    with db:
        db.executemany('INSERT INTO jobs VALUES (?,?,?,?)',[(j['id'],i,j['status'],json.dumps(j,ensure_ascii=False)) for i,j in enumerate(records)])
        db.execute('INSERT INTO job_storage_meta VALUES (?,?)',('migrated',datetime.now(timezone.utc).isoformat()))
    export(db,json_path)


def reconcile_legacy(db,json_path):
    """Old running clients may append outcomes to the compatibility export.

    Only monotonic activity extensions are accepted; their application materials
    never replace current SQLite materials. Divergent snapshots are quarantined.
    """
    row=db.execute("SELECT value FROM job_storage_meta WHERE key='json_export_sha256'").fetchone()
    if row and row[0]==digest(json_path):return
    incoming=json.loads(json_path.read_text(encoding='utf-8')) if json_path.exists() else []
    conflicts=[]
    with db:
        for candidate in incoming:
            row=db.execute('SELECT payload FROM jobs WHERE id=?',(candidate.get('id'),)).fetchone()
            if not row:conflicts.append(candidate);continue
            current=json.loads(row[0])
            if candidate==current:continue
            old=current.get('activity',[]);new=candidate.get('activity',[])
            if current.get('status')!='deleted' and len(new)>len(old) and new[:len(old)]==old:
                for key in ['status','activity','deletedAt','deletionReason','outcomeType','lastEmailEventAt','lastEmailMessageId','lastEmailObservedAt','emailEvents']:
                    if key in candidate:current[key]=candidate[key]
                db.execute('UPDATE jobs SET status=?,payload=? WHERE id=?',(current['status'],json.dumps(current,ensure_ascii=False),current['id']))
            else:conflicts.append(candidate)
        if conflicts:
            folder=json_path.parent/'.discovery/backups/legacy-conflicts'
            folder.mkdir(parents=True,exist_ok=True)
            (folder/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.json')).write_text(json.dumps(conflicts,indent=2,ensure_ascii=False),encoding='utf-8')
    export(db,json_path)


def read(path,json_path):
    db=connect(path)
    try:
        initialize(db,json_path)
        reconcile_legacy(db,json_path)
        return [json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs ORDER BY position')]
    finally:db.close()


def update(path,json_path,mutator):
    db=connect(path)
    try:
        initialize(db,json_path);reconcile_legacy(db,json_path)
        db.execute('BEGIN IMMEDIATE')
        records=[json.loads(r[0]) for r in db.execute('SELECT payload FROM jobs ORDER BY position')]
        result=mutator(records)
        ids=[j['id'] for j in records]
        if len(ids)!=len(set(ids)):raise ValueError('Duplicate job IDs')
        db.execute('DELETE FROM jobs')
        db.executemany('INSERT INTO jobs VALUES (?,?,?,?)',[(j['id'],i,j['status'],json.dumps(j,ensure_ascii=False)) for i,j in enumerate(records)])
        db.commit()
        export(db,json_path)
        return result
    except BaseException:
        db.rollback();raise
    finally:db.close()


def maintain(path,folder,now=None):
    """Daily SQLite backup; seven compressed snapshots; 14-day HTTP cache TTL."""
    now=now or datetime.now(timezone.utc)
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    target=folder/('dossier-'+now.strftime('%Y-%m-%d')+'.sqlite3.gz')
    if target.exists():return {'state':'already_backed_up','backup':str(target)}
    temporary=folder/(target.stem+'.tmp')
    source=sqlite3.connect(path,timeout=30)
    destination=sqlite3.connect(temporary)
    try:source.backup(destination)
    finally:destination.close();source.close()
    compressed=target.with_suffix('.gz.tmp')
    with temporary.open('rb') as src,gzip.open(compressed,'wb') as dest:shutil.copyfileobj(src,dest)
    compressed.replace(target)
    temporary.unlink()
    with sqlite3.connect(path,timeout=30) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone()
        count=db.execute("DELETE FROM state WHERE key LIKE 'http:%' AND CAST(json_extract(payload,'$.cached_at') AS REAL) < ?",(now.timestamp()-14*86400,)).rowcount if exists else 0
    for old in sorted(folder.glob('dossier-*.sqlite3.gz'))[:-7]:
        if old.resolve().parent==folder.resolve():old.unlink()
    return {'state':'backed_up','backup':str(target),'expiredHttpEntries':count}
