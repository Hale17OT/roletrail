import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import requests

import server
from discovery.store import LeadStore, read_jobs


@pytest.fixture
def api(tmp_path, monkeypatch):
    from discovery import runs
    monkeypatch.setattr(runs, 'STATE_DIR', tmp_path)
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps([{"id": "a", "status": "applied", "description": "keep"},
                                {"id": "new", "status": "ready"}]), encoding="utf-8")
    monkeypatch.setattr(server, "JOBS_FILE", str(path))
    monkeypatch.setattr(server, "LeadStore", lambda: LeadStore(tmp_path / "state.db"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", path
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_stale_full_snapshot_cannot_erase_new_jobs(api):
    base, path = api
    result = requests.post(base + "/api/jobs", json=[{"id": "a", "status": "ready", "description": "stale"}])
    assert result.status_code == 200
    assert read_jobs(path) == [{"id": "a", "status": "applied", "description": "keep"}, {"id": "new", "status": "ready"}]


def test_explicit_status_change_uses_compare_and_swap(api):
    base, path = api
    result = requests.post(base + "/api/jobs/status", json={"id": "a", "fromStatus": "applied", "status": "interviewing"})
    assert result.status_code == 200
    assert result.json()["status"] == "interviewing"
    stale = requests.post(base + "/api/jobs/status", json={"id": "a", "fromStatus": "applied", "status": "offer"})
    assert stale.status_code == 409
    assert read_jobs(path)[0]["status"] == "interviewing"


def test_deletion_creates_tombstone(api):
    base, path = api
    result = requests.post(base + "/api/jobs/status", json={"id": "new", "fromStatus": "ready", "status": "deleted"})
    assert result.status_code == 200
    assert result.json()["deletedAt"]
    assert len(read_jobs(path)) == 2


def test_private_source_state_is_not_served(api):
    base, _ = api
    assert requests.get(base + "/.discovery/leads.sqlite3").status_code == 404
    assert requests.get(base + "/%2Eemail_config.json").status_code == 404


def test_source_health_endpoint(api):
    base, _ = api
    assert requests.get(base + "/api/harvest/status").json()["status"] == "not_run"


def test_api_uses_shared_discovery_pipeline(api, monkeypatch):
    base, _ = api
    captured = {}
    def run(**kwargs):
        captured.update(kwargs)
        return [], {"discovered": 10, "verified": 0, "added": 0, "dryRun": True}
    monkeypatch.setattr(server, "run_discovery", run)
    response = requests.post(base + "/api/harvest", json={"dryRun": True, "sources": ["telegram"]})
    assert response.status_code == 200
    assert captured["sources"] == ["telegram"] and captured["sync"] is False


def test_concurrent_search_is_reported(api):
    base, _ = api
    server.HARVEST_LOCK.acquire()
    try:
        response = requests.post(base + "/api/harvest", json={})
        assert response.status_code == 409
    finally:
        server.HARVEST_LOCK.release()


def test_local_origin_host_and_static_boundaries(api):
    base, _ = api
    assert requests.get(base+'/api/jobs',headers={'Origin':'https://example.com'}).status_code==403
    assert requests.post(base+'/api/jobs',json=[],headers={'Origin':'https://example.com'}).status_code==403
    assert requests.get(base+'/api/jobs',headers={'Host':'evil.example'}).status_code==403
    assert requests.get(base+'/api/jobs',headers={'Origin':base}).headers['Access-Control-Allow-Origin']==base
    for path in ['/server.py','/jobs.json','/discovery/candidate.json','/frontend/src/App.vue','/assets/../server.py','/api/kits/not-a-checksum']:
        assert requests.get(base+path).status_code==404


def test_json_validation_and_health(api):
    base,_=api
    assert requests.post(base+'/api/jobs/action',data='{}').status_code==415
    assert requests.post(base+'/api/jobs/action',data='{bad',headers={'Content-Type':'application/json'}).status_code==400
    assert requests.post(base+'/api/jobs/action',json={'id':'a'}).status_code==400
    assert requests.get(base+'/api/health').json()['loopbackOnly'] is True
    assert requests.get(base+'/api/jobs/apply?id=new',allow_redirects=False).status_code==409


def test_background_search_validates_sources_and_is_observable(api,monkeypatch):
    base,_=api
    assert requests.post(base+'/api/searches',json={'sources':['unknown']}).status_code==400
    assert requests.post(base+'/api/searches',json={'query':'x'*301}).status_code==400
    def runner(**kwargs):
        kwargs['checkpoint']({'sources':[{'source':'fixture','state':'ok'}],'funnel':{'fetched':2}})
        return [],{'added':0,'verified':0,'sources':[{'source':'fixture','state':'ok'}]}
    monkeypatch.setattr(server,'run_discovery',runner)
    response=requests.post(base+'/api/searches',json={'dryRun':True,'sources':['ats']})
    assert response.status_code==202
    run_id=response.json()['id']
    import time
    for _ in range(100):
        saved=requests.get(base+'/api/searches').json()[0]
        if saved['state']=='completed':break
        time.sleep(.01)
    assert saved['state']=='completed' and saved['id']==run_id
