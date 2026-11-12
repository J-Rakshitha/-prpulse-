# Phase 13 / publish closeout (ran 2026-11-07)

## Migration row counts (SQLite → Postgres `prpulse_migrated`)

| table | sqlite source | copied |
| --- | ---: | ---: |
| repos | 6 | 6 |
| prs | 11462 | 11462 |
| pr_files | 110371 | 110371 |
| pr_commits | 51429 | 51429 |
| pr_events | 270531 | 270531 |
| ci_runs | 67683 | 67683 |
| model_versions | 1 | 1 |
| predictions | 7 | 7 |
| outcomes | 0 | 0 |
| collection_checkpoints | 9 | 9 |
| installations | 0 | 0 |
| installation_repos | 0 | 0 |
| webhook_deliveries | 0 | 0 |
| failed_jobs | 0 | 0 |

## Restore proof (`prpulse_migrated` dump → `prpulse_restore`)

All tables above: migrated count == restore count (**OK** for every table).

## Pytest

| Suite | Result |
| --- | --- |
| Postgres (`PRPULSE_TEST_DATABASE_URL=…/prpulse_test`, host port **5433**) | **143 passed**, 13 skipped (SQLite-only `test_db.py`) |
| SQLite (default, final tree) | see close of session log |

## Alertmanager

Posted `PRPulseTestAlert` to local Alertmanager → webhook receiver on host `:9999`; `reports/alert_test_received.json` written (**599** bytes).

## Secrets

- Git history scan for `ghp_`, `github_pat_`, PEM headers: **no commits found**.
- Working tree hits only fake strings in `tests/test_log_redaction.py` and `tests/test_runtime_settings.py`.
- `.env` exists locally (gitignored); do not commit it.
- **Important:** `git rev-parse --show-toplevel` from this folder currently resolves to the **user home directory**, not `prpulse`. Before publishing, run `git init` **inside** `prpulse` (or otherwise isolate the project repo) so home files are never pushed.
