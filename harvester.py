"""Roletrail discovery CLI and compatibility API. Dry-run unless --sync is explicit."""
import argparse
import json
from pathlib import Path

from discovery.pipeline import run_discovery
from discovery.policy import now_utc
from discovery.store import ROOT, LeadStore, merge_verified

JOBS_FILE = str(ROOT / "jobs.json")


def harvest_jobs(queries=None, sites=None, countries=None, hours_old=168,
                 results_per_site=None, status="ready", sources=None):
    jobs, _ = run_discovery(queries=queries, sites=sites, countries=countries,
                            hours=hours_old, results=results_per_site, status=status,
                            sources=sources)
    return jobs


def sync_to_jobs_file(new_jobs, status="ready"):
    if status not in {"ready", "saved"}:
        raise ValueError("Invalid harvest status")
    return merge_verified([{**job, "status": status} for job in new_jobs], JOBS_FILE)


def log_harvest(summary):
    with open(ROOT / "harvester.log", "a", encoding="utf-8") as f:
        f.write(f"[{now_utc().isoformat()}] {summary}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", help="Repeat for multiple queries; default rotates configured queries")
    parser.add_argument("--sources", nargs="+", choices=["ats", "jobspy", "telegram", "feeds", "pages", "people"])
    parser.add_argument("--sites", nargs="+", help="JobSpy boards (Google reports unavailable)")
    parser.add_argument("--countries", nargs="+", help="Supported JobSpy region names")
    parser.add_argument("--results", type=int, default=None)
    parser.add_argument("--hours", type=int, default=168)
    trial=parser.add_mutually_exclusive_group()
    trial.add_argument('--trial-pl200',action='store_true',help='One-off PL-200-required, cross-border remote search with a 12-day window; normal defaults stay unchanged')
    trial.add_argument('--trial-pl200-preferred',action='store_true',help='One-off PL-200 required OR preferred, cross-border remote search over twelve days')
    trial.add_argument('--trial-skills-first',action='store_true',help='One-off skills-first trial: 30 days or verified open, inferred remote, manual responsibility review')
    trial.add_argument('--trial-all-tracks',action='store_true',help='One-off software + Power Platform trial: at most 30 days, current active application proof and manual fit review')
    parser.add_argument("--request-budget", type=int)
    parser.add_argument("--status", choices=["ready", "saved"], default="ready")
    parser.add_argument("--sync", action="store_true", help="Promote only officially verified, complete dossiers")
    parser.add_argument("--dry-run", action="store_true", help="Collect and audit without changing jobs.json")
    parser.add_argument("--all-tracks", action="store_true", help="Compatibility flag; the shared technical policy always applies")
    parser.add_argument("--report", action="store_true", help="Show the latest source health report without running a harvest")
    parser.add_argument("--history", type=int, help="Show recent run reports for yield comparisons")
    parser.add_argument('--verify-existing',action='store_true',help='Audit existing opportunities with source evidence and per-rule reasons')
    parser.add_argument('--review-sources',help='Source registry for an existing-opportunity audit')
    parser.add_argument('--review-output',help='Write the structured existing-opportunity review report')
    parser.add_argument('--full-sweep',action='store_true',help='Visit every configured employer and Telegram channel with expanded audit budget')
    parser.add_argument('--mode',choices=['remote','addis','sponsored'],help='Run a specific JobSpy mode instead of rotating')
    parser.add_argument('--audit-pending',action='store_true',help='Audit stored evidence backlog without repeating discovery searches')
    parser.add_argument('--pending-limit',type=int,default=500)
    parser.add_argument('--reconsider-review',help='Named prior audit to reconsider after an explicit user policy correction')
    args = parser.parse_args(argv)
    if args.trial_pl200 or args.trial_pl200_preferred:
        args.hours=288
        args.query=args.query or ['PL-200','Power Platform Functional Consultant','Power Apps PL-200']
        args.mode=args.mode or 'remote'
    if args.trial_skills_first or args.trial_all_tracks:
        args.hours=720
        args.query=args.query or (['Junior Software Engineer','Junior Python Developer','Junior QA Engineer','Power Platform Developer','Power Apps Developer','Business Applications Analyst'] if args.trial_all_tracks else ['Power Platform Developer','Power Apps Developer','Business Applications Analyst','Copilot Studio Developer'])
        args.mode=args.mode or 'remote'
    if args.report or args.history:
        data = LeadStore().history(args.history) if args.history else LeadStore().get("last_report", {"status": "not_run"})
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return data
    if args.sync and args.dry_run:
        parser.error("Choose either --sync or --dry-run")
    if args.verify_existing:
        from discovery.review import review_existing
        report=review_existing(sync=args.sync,manifest_path=args.review_sources,jobs_path=JOBS_FILE,reconsider_path=args.reconsider_review)
        if args.review_output:
            Path(args.review_output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='jobs'},indent=2,ensure_ascii=False))
        return report
    config=None
    if args.full_sweep or args.audit_pending:
        from discovery.pipeline import load_config
        config=load_config()
        config['employers_per_run']=len(json.loads((ROOT/'discovery/employers.json').read_text(encoding='utf-8')))
        config['telegram']['channels_per_run']=len(config['telegram']['channels'])
        config['verification_budget']=400
    if not 1<=args.pending_limit<=1000:
        parser.error('Pending limit must be between 1 and 1000')
    if args.mode:
        from discovery.pipeline import load_config
        config=config or load_config()
        config['jobspy']['modes']=[args.mode]
    _, report = run_discovery(sources=args.sources, sync=args.sync, status=args.status, config=config,
                              retry_only=args.audit_pending,pending_limit=args.pending_limit if args.audit_pending else None,trial_pl200=args.trial_pl200,trial_pl200_preferred=args.trial_pl200_preferred,trial_skills_first=args.trial_skills_first,trial_all_tracks=args.trial_all_tracks,
                              queries=args.query, sites=args.sites, countries=args.countries,
                              results=args.results, hours=args.hours, request_budget=args.request_budget)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    log_harvest(f"Discovered {report['discovered']}; verified {report['verified']}; added {report['added']}; dryRun={report['dryRun']}")
    return report


if __name__ == "__main__":
    main()
