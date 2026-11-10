# Roletrail

A local career workspace for finding opportunities, checking eligibility, preparing application kits and tracking outcomes.

Roletrail combines a Vue interface with a Python API and SQLite storage. Discovery adapters collect leads from public ATS boards, JobSpy, feeds, Telegram and public LinkedIn hiring posts. Source evidence and review gates keep discovery separate from a recommendation.

## Features

- Opportunity cards, filters, saved views and an application pipeline.
- Source-level verification, explicit exclusion reasons and expiring eligibility checks.
- Separate labels for inferred remote eligibility and inferred visa support.
- CV library, editable letters, notes and follow-up dates.
- Immutable submitted kits and protected historical outcomes.
- Soft deletion and blacklists across job IDs, URLs and employer/title signatures.
- Transactional SQLite storage and local rotating backups.
- Optional Telegram channel reading and inbound email outcome monitoring.

## Quick start

Requires Python 3.10+ and Node.js 22.12+ (or a newer supported Node release).

```sh
python -m venv .venv
# Activate .venv using your shell's standard activation command.
python -m pip install -r requirements-discovery.txt
python setup_local.py
npm --prefix frontend ci
npm --prefix frontend run build
python server.py
```

Open http://localhost:3000. The app starts with an empty pipeline. No real CV, account, application or session is included. `ROLETRAIL_PORT` changes the API/server port.

Before discovery, edit your ignored local `discovery/candidate.json`, add your own PDFs to `cvs/`, and fill `frontend/src/data/cvs.json`. See [configuration](docs/CONFIGURATION.md). The examples are setup placeholders, not assertions about a candidate. The tool does not generate your evidence for you.

## Discovery

```sh
python harvester.py --sources ats feeds people --dry-run
python harvester.py --sources jobspy --sites indeed glassdoor zip_recruiter linkedin --dry-run
python harvester.py --report
```

Runs are dry-run by default. `--sync` adds only complete verified kits. Review responsibilities and truthful candidate evidence before promotion. Searching does not send messages, submit applications or mark jobs Applied. Public search coverage is partial; unavailable or rate-limited sources must not be reported as complete coverage. Google Jobs is currently marked unavailable by the upstream adapter.

The policy currently includes an Ethiopia/Addis Ababa regional eligibility preset and Power Platform preference alongside general software tracks. It is not a universal immigration or qualification engine. Review `discovery/policy.py`, the candidate profile and source configuration before adapting it to another region. Remote labels do not guarantee cross-border employment, and relocation assistance does not guarantee initial work-visa sponsorship.

## Development

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
npm --prefix frontend test
npm --prefix frontend run dev
```

Vite proxies API requests to localhost:3000. Rebuild the frontend before serving with Python. The Python server binds only to loopback and checks request origins. Do not expose it directly to the Internet; it is a single-user local application, without multi-user authentication.

## Privacy and integrations

Personal inputs, credentials, SQLite databases, caches, PDFs, logs and submissions are ignored by Git. See [privacy](docs/PRIVACY.md) and [security](SECURITY.md). Review `git status` and run `python scripts/check_public_tree.py` before publishing changes. `.gitignore` is a guardrail, not a substitute for reviewing committed content.

Telegram authenticated setup is optional and currently uses Windows DPAPI. Public preview mode is the default; respect source terms, access controls and rate limits. Email monitoring is optional, read-only against the inbox and configured locally. Neither integration is connected automatically.

## License

MIT. Dependencies retain their own licenses. No third-party reference repositories or harvested job descriptions are redistributed in this repository.
