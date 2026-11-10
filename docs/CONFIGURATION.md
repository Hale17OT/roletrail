# Configuration

Run `python setup_local.py` once. It copies examples only when a local target does not exist.

- `discovery/candidate.json`: your truthful identity, contact details, degree, optional certification and 3–5 project evidence entries. Never use template statements as real achievements.
- `frontend/src/data/cvs.json`: local CV definitions. Start with `[]`; add entries with `fileName`, `roleTitle`, `shortLabel`, `targetRole`, `summary`, `skills`, `atsKeywords` and `url` (`/cvs/<filename>.pdf`). Store PDFs directly in `cvs/`.
- `discovery/dossier.py` `CVS`: map software, Power Platform and hybrid tracks to the corresponding local PDF filenames. A missing PDF blocks a complete application kit.
- `discovery/sources.json`: public ATS boards, feeds, queries, source budgets and Telegram channel names. The recurring profile prefers 30-day postings and allows older/undated listings only with fresh exact-vacancy active proof. Responsibilities require manual review; a keyword score is not a hiring probability.
- `discovery/employers.json`: public employer discovery seeds.
- `discovery/review_sources.json`: ignored per-vacancy official source review metadata. It is empty on a new setup.

Personal configurations are ignored and must not be force-added to Git. Current geography policy is a regional preset in `discovery/policy.py`, not fully driven by the profile JSON. Adapt it and test the explicit exclusions for your region before relying on the eligibility labels.

## Optional Telegram

Install `requirements-telegram.txt`. On Windows, run `python connect_telegram.py` and enter your own Telegram API credentials locally. DPAPI binds the encrypted session to your Windows user. Public preview mode works without this connection, with less coverage. Revoke connected sessions through Telegram if needed. No channels are joined and no messages are sent.

## Optional email monitoring

`email_monitor.py` reads a local `.email_config.json` or supported environment variables. Inspect its configuration reader and configure your own provider-specific IMAP credentials locally. Never put credentials in source or logs. The monitor observes inbound messages and records outcomes; employer rejection is separate from candidate withdrawal.
