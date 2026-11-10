"""Loopback-only Roletrail API and compiled frontend. No repository static fallback."""
import http.server
import json
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit, parse_qs

from discovery.pipeline import run_discovery
from discovery.store import LeadStore, merge_ui_snapshot, change_status, ROOT, STATE_DIR
import dashboard
from discovery import runs

PORT = int(os.environ.get('ROLETRAIL_PORT', '3000'))
DIRECTORY = str(ROOT)
JOBS_FILE = str(ROOT / 'jobs.json')
HARVEST_LOCK = threading.Lock()


def search_params(data):
    sources = data.get('sources') or ['ats', 'jobspy', 'telegram', 'feeds', 'pages', 'people']
    if not isinstance(sources, list) or any(s not in {'ats', 'jobspy', 'telegram', 'feeds', 'pages', 'people'} for s in sources):
        raise ValueError('Choose supported search sources')
    query = data.get('query', '')
    if not isinstance(query, str) or len(query) > 300:
        raise ValueError('Search query must be under 300 characters')
    return {'sources': sources, 'sync': not data.get('dryRun', False), 'queries': [query] if query.strip() else None,
            'status': 'ready', 'hours': 720, 'request_budget': 150, 'jobs_path': JOBS_FILE}


class Handler(http.server.BaseHTTPRequestHandler):
    def allowed(self):
        try:
            host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        except ValueError:
            return False
        origin = self.headers.get('Origin')
        allowed_origins = {f'http://{h}:{p}' for h in ('localhost', '127.0.0.1', '[::1]') for p in (self.server.server_port, 5173)}
        return host in {'localhost', '127.0.0.1', '::1'} and (not origin or origin in allowed_origins)

    def end_headers(self):
        origin = self.headers.get('Origin')
        if origin and self.allowed():
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def respond(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.respond({}, 200 if self.allowed() else 403)

    def do_GET(self):
        if not self.allowed():
            return self.respond({'error': 'Only local dashboard origins are allowed'}, 403)
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/api/jobs/apply':
                job_id=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                job=next((j for j in dashboard.read_jobs(JOBS_FILE) if j.get('id')==job_id and j.get('status')!='deleted'),None)
                if not job:
                    return self.respond({'error':'Job unavailable'},404)
                if not dashboard.readiness(job)['canApply']:
                    return self.respond({'error':'Eligibility expired or is incomplete. Return to the dossier and recheck the official source.'},409)
                self.send_response(302)
                self.send_header('Location',dashboard.canonical_url(job['applyUrl']))
                self.end_headers()
                return
            if path == '/api/jobs':
                return self.respond(dashboard.project_jobs(JOBS_FILE))
            if path == '/api/cvs':
                return self.respond(dashboard.cv_library(JOBS_FILE))
            if path == '/api/profile':
                return self.respond(dashboard.candidate())
            if path == '/api/integrations':
                return self.respond(dashboard.integration_summary())
            if path == '/api/searches':
                return self.respond(runs.listing(LeadStore()))
            if path == '/api/telegram/status':
                from discovery.telegram_auth import connection_status
                return self.respond(connection_status())
            if path == '/api/harvest/status':
                return self.respond(LeadStore().get('last_report', {'status': 'not_run'}))
            if path == '/api/health':
                return self.respond({'status': 'ok', **dashboard.build_health(), 'loopbackOnly': True})
            target = None
            if re.fullmatch(r'/api/kits/[a-f0-9]{64}', path):
                target = STATE_DIR / 'submitted' / (path.rsplit('/', 1)[-1] + '.pdf')
            elif path.startswith('/cvs/') and re.fullmatch(r'[A-Za-z0-9_.-]+\.pdf', path[5:]):
                target = ROOT / 'cvs' / path[5:]
            elif path in ('/', '/index.html'):
                target = ROOT / 'frontend/dist/index.html'
                if not target.is_file():
                    return self.respond({'error': 'Frontend build missing. Run npm run build in frontend, then reload.'}, 503)
            elif path.startswith('/assets/') and re.fullmatch(r'[A-Za-z0-9_.-]+', path[8:]):
                target = ROOT / 'frontend/dist/assets' / path[8:]
            elif path in ('/favicon.svg', '/icons.svg'):
                target = ROOT / 'frontend/dist' / path[1:]
            if target and target.is_file():
                body = target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            self.respond({'error': 'Not found'}, 404)
        except Exception:
            self.respond({'error': 'Unable to read local dashboard data'}, 500)

    def do_POST(self):
        if not self.allowed():
            return self.respond({'error': 'Only local dashboard origins are allowed'}, 403)
        path = urlsplit(self.path).path
        try:
            if not self.headers.get('Content-Type', '').startswith('application/json'):
                return self.respond({'error': 'Send application/json'}, 415)
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 2_000_000:
                return self.respond({'error': 'Invalid request size'}, 413)
            data = json.loads(self.rfile.read(length))
            if path == '/api/jobs':
                merge_ui_snapshot(data, JOBS_FILE)
                return self.respond({'status': 'saved'})
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
            if path == '/api/jobs/action':
                return self.respond(dashboard.mutate_job(data['id'], data, JOBS_FILE))
            if path == '/api/jobs/recheck':
                return self.respond(dashboard.recheck(data['id'], JOBS_FILE))
            if path == '/api/jobs/status':
                return self.respond(change_status(data['id'], data['fromStatus'], data['status'], JOBS_FILE))
            if path == '/api/searches':
                return self.respond(runs.start(search_params(data), run_discovery, LeadStore()), 202)
            if path == '/api/searches/cancel':
                return self.respond(runs.cancel(data['id'], LeadStore()))
            if path == '/api/check-emails':
                from email_monitor import check_emails
                result = check_emails()
                return self.respond(result, 503 if result['status'] == 'failed' else 200)
            if path == '/api/email/review':
                store = LeadStore()
                key = 'email-review:' + str(data['reviewId'])
                item = store.get(key)
                if not item:
                    raise ValueError('Review item not found')
                item['state'] = 'reviewed'
                store.set(key, item)
                report = store.get('email:last_report', {})
                report['suggestions'] = [s for s in report.get('suggestions', []) if s.get('reviewId') != data['reviewId']]
                store.set('email:last_report', report)
                return self.respond({'status':'reviewed'})
            if path == '/api/harvest':
                if not HARVEST_LOCK.acquire(blocking=False):
                    return self.respond({'error': 'A search is already running'}, 409)
                try:
                    _, report = run_discovery(**search_params(data))
                    return self.respond({'status': 'ok', **report})
                finally:
                    HARVEST_LOCK.release()
            return self.respond({'error': 'Not found'}, 404)
        except json.JSONDecodeError:
            self.respond({'error': 'Invalid JSON'}, 400)
        except (ValueError, KeyError, TypeError) as exc:
            self.respond({'error': str(exc)}, 409 if 'changed' in str(exc) or 'already running' in str(exc) or path.endswith('/status') else 400)
        except Exception:
            self.respond({'error': 'The operation failed. No success has been recorded; reload to check current state.'}, 500)


def run_server():
    from discovery.store import read_jobs, jobs_lock
    from discovery.job_database import maintain
    read_jobs()
    def storage_maintenance():
        import time
        while True:
            try:
                with jobs_lock(STATE_DIR/'storage-maintenance'):
                    result=maintain(STATE_DIR/'leads.sqlite3',STATE_DIR/'backups/sqlite')
                LeadStore().set('storage:maintenance',result)
            except Exception as exc:
                LeadStore().set('storage:maintenance',{'state':'error','reason':type(exc).__name__})
            time.sleep(3600)
    threading.Thread(target=storage_maintenance,daemon=True).start()
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    httpd.daemon_threads = True
    print(f'Roletrail running at http://localhost:{PORT}', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    run_server()
