"""Disposable local browser fixture: python tests/ui_fixture.py (port 3009).

Never reads inboxes or searches providers. All mutations use a temporary directory.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
from datetime import timedelta
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dashboard
import server
import email_monitor
from discovery import runs
from discovery.store import LeadStore
from discovery.policy import now_utc, evaluate
from discovery.dossier import build_dossier


def main():
    with tempfile.TemporaryDirectory(prefix='dossier-ui-') as directory:
        root = Path(directory)
        store = LeadStore(root / 'state.db')
        dashboard.STATE_DIR = runs.STATE_DIR = server.STATE_DIR = root
        dashboard.LeadStore = server.LeadStore = lambda: store
        server.JOBS_FILE = str(root / 'jobs.json')
        jobs = []
        for i, status in enumerate(['saved','ready','applied','deleted']):
            raw = {'title': 'Junior Power Platform Developer' + (' — Application lifecycle and Dataverse integration fixture' if i==1 else ''),
                   'company': 'UI Fixture Employer', 'description': 'TEST FIXTURE ONLY. Build Power Apps and Dataverse integrations. 2 years experience. Remote worldwide.\nSecond paragraph remains readable.',
                   'location':'Worldwide', 'workplace':'Remote', 'source':'ashby:Fixture',
                   'url':f'https://jobs.ashbyhq.com/fixture/{i}', 'published_at':(now_utc()-timedelta(hours=12)).isoformat(),
                   'official_verified':True,'audit_method':'fixture_only'}
            job = build_dossier(raw,evaluate(raw),'saved')
            job['status']=status
            job['instructions']='1. Review the PDF.\n2. Prepare the application.\n3. Confirm only after submission.'
            if status=='deleted':job['deletedAt']=now_utc().isoformat()
            jobs.append(job)
        Path(server.JOBS_FILE).write_text(json.dumps(jobs),encoding='utf-8')
        def fake_search(**kwargs):
            report={'sources':[{'source':'fixture provider','state':'ok','fetched':2}], 'funnel':{'fetched':2,'verified':0},'verified':0,'added':0}
            kwargs['checkpoint'](report)
            return [], report
        server.run_discovery=fake_search
        def fake_email():
            report={'status':'partial','accounts':[{'account':'fixture@example.com','state':'failed','reason':'Fixture authentication failure'}], 'updates':[], 'suggestions':[], 'checkedAt':now_utc().isoformat()}
            store.set('email:last_report',report)
            return report
        email_monitor.check_emails=fake_email
        print('Disposable UI fixture: http://localhost:3009; data: '+directory,flush=True)
        ThreadingHTTPServer(('127.0.0.1',3009),server.Handler).serve_forever()


if __name__=='__main__':main()
