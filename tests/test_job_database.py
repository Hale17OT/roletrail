import gzip,json,sqlite3
from datetime import datetime,timedelta,timezone
import pytest
from discovery.job_database import read,update,maintain


def setup(tmp_path):
    source=tmp_path/'jobs.json';db=tmp_path/'test.sqlite3'
    records=[{'id':'applied','status':'applied','tailoredLetter':'Original letter','activity':[]},
             {'id':'deleted','status':'deleted','deletedAt':'2026-11-10','activity':[]}]
    source.write_text(json.dumps(records),encoding='utf-8')
    return source,db,records


def test_lossless_migration_and_transaction_rollback(tmp_path):
    source,db,records=setup(tmp_path)
    assert read(db,source)==records
    def fail(jobs):
        jobs[0]['tailoredLetter']='changed'
        raise RuntimeError('Interrupted update')
    with pytest.raises(RuntimeError):update(db,source,fail)
    assert read(db,source)==records
    assert json.loads(source.read_text())==records
    assert (tmp_path/'.discovery/backups/pre-sqlite-jobs.json').exists()


def test_legacy_events_preserve_current_materials_and_deleted_records(tmp_path):
    source,db,records=setup(tmp_path)
    read(db,source)
    update(db,source,lambda jobs:jobs[0].update(tailoredLetter='Updated letter'))
    records[0].update(status='interviewing',activity=[{'type':'email_stage_changed','at':'2026-11-10'}])
    records[1]['status']='ready'
    source.write_text(json.dumps(records),encoding='utf-8')
    actual=read(db,source)
    assert actual[0]['status']=='interviewing'
    assert actual[0]['tailoredLetter']=='Updated letter'
    assert actual[1]['status']=='deleted'
    assert list((tmp_path/'.discovery/backups/legacy-conflicts').glob('*.json'))


def test_daily_backup_restore_and_cache_retention(tmp_path):
    source,db,records=setup(tmp_path);read(db,source)
    now=datetime.now(timezone.utc)
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE state(key TEXT PRIMARY KEY,payload TEXT)')
        con.executemany('INSERT INTO state VALUES (?,?)',[
            ('http:expired',json.dumps({'cached_at':now.timestamp()-15*86400})),
            ('http:fresh',json.dumps({'cached_at':now.timestamp()})),
            ('job-audit:keep',json.dumps({'cached_at':0}))])
    folder=tmp_path/'snapshots'
    first=maintain(db,folder,now)
    assert first['expiredHttpEntries']==1
    restored=tmp_path/'restored.sqlite3'
    restored.write_bytes(gzip.open(first['backup'],'rb').read())
    with sqlite3.connect(restored) as con:assert con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
    for day in range(1,9):maintain(db,folder,now+timedelta(days=day))
    assert len(list(folder.glob('*.gz')))==7
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT 1 FROM state WHERE key='job-audit:keep'").fetchone()
    assert maintain(db,folder,now+timedelta(days=8))['state']=='already_backed_up'
