# Local data and privacy

SQLite at `.discovery/leads.sqlite3` is authoritative for the pipeline. `jobs.json` is a compatibility export. Local compressed SQLite snapshots are retained under `.discovery/backups`; these contain personal data too and must remain private. This is local recovery, not off-device disaster recovery.

The public repository contains only source, synthetic examples, tests and public source configuration. No CVs, submitted kits, candidate employment history, real application records, browser screenshots, handoffs, chat transcripts, email settings or Telegram sessions are included.

On your machine, candidate and CV configurations, PDFs, email settings and all `.discovery` state stay ignored. Optional integrations necessarily communicate with their configured providers. HTTP caches and logs may contain job and application metadata. Restrict access to the local directory and use encrypted storage where appropriate.

The browser may cache your pipeline locally. Clearing browser storage affects that browser cache, not the SQLite source of truth. Use the app's actions to update authoritative records. Soft deletion retains a tombstone to prevent rediscovery.

Before sharing, run `python scripts/check_public_tree.py`, inspect staged files, and scan commit history with a dedicated secret scanner. Pattern scanning cannot prove that arbitrary content is non-personal.
