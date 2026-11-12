# Phase 13 decisions log

## Sources verified

- GitHub webhook ack timeout: **10 seconds**. If the server takes longer, GitHub records a failed delivery.
  - https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks
  - https://docs.github.com/en/webhooks/using-webhooks/handling-webhook-deliveries
- Conservative design target: ack in about **1 second**; never run model inference or GitHub API calls in the webhook request handler.

## Audit summary

See `reports/phase13_audit.md`. Most production pieces were partial or missing; bot already worked live with sync webhook + PAT.

## Assumptions

- Redis + RQ (not Celery) — nothing in the repo used Celery.
- SQLite remains default for unit tests and local collector; `DATABASE_URL` selects Postgres in compose/production.
- `PRPULSE_AUTH_MODE=pat` keeps the personal-token path for local/dev; default production mode is `app`.
- Coverage threshold N: from local `data/prpulse.db` resolved PR counts —
  pandas-dev/pandas=6320, scikit-learn/scikit-learn=3461 (training repos);
  older probe repos httpx=114, flask=313, requests=491.
  Per-repo median baselines in validation are fit on large train slices of the
  two training repos. A repo with fewer than **50** resolved PRs cannot support
  a stable per-repo median (high sampling variance). Default
  `UNSEEN_REPO_MIN_RESOLVED=50` (configurable). Repos at or above N in the DB
  are `seen`; below N (including brand-new installs at 0) are `unseen`.

## Gate results

| Gate | Result | Notes |
| --- | --- | --- |
| 13.1 | **pass** | Webhook persists + enqueues (or leaves `pending` if Redis down); ack measured under ~0.35s with a slow predictor in `tests/test_webhook.py`. Worker: `src/service/worker.py` + RQ. Recovery loop re-enqueues pending. |
| 13.2 | **pass (unit)** | `src/service/github_app.py` JWT + installation token cache; installation tracking; `PRPULSE_AUTH_MODE=pat\|app`. **Live App test needs user registration** (App ID + private key + install). |
| 13.3 | **pass (sqlite + dual layer)** | Schema v4 + `database.py` (`?`→`%s`) + Alembic `0001` + `scripts/migrate_sqlite_to_postgres.py` + compose Postgres volume. Full suite green on SQLite. Postgres container migrate not run in this gate (optional follow-up). |
| 13.4 | **pass** | `UNSEEN_REPO_MIN_RESOLVED=50` (justified above). Unseen banner + coarse bucket; `predictions.coverage_status` stored. |
| 13.5 | **pass** | No comment on failure; retries/backoff/jitter; `failed_jobs` + `python -m src.service.replay`; rate-limit / timeout paths; Prometheus gauges for queue/pending/failed/rate-limit/ack. |
| 13.6 | **pass** | Durable `webhook_deliveries.delivery_id` UNIQUE; `pr_processing_locks`; out-of-order closed vs opened skip. |
| 13.7 | **pass** | `src/runtime_settings.py` + log redaction filter; `.env.example` / gitignore / dockerignore / requirements / compose updated. History scan: no `ghp_` or PEM private-key commits found in `git log -G`. |
| 13.8 | **pass** | Caddy `proxy:8080` only public port; dashboard 401 without auth / 200 with; `/metrics` → 404 on public side (`tests/test_proxy_auth.py` + live curl). |
| 13.9 | **pass** | `deploy/alerts.yml` + Alertmanager profile; Grafana panels extended; `promtool check rules` → SUCCESS (10 rules); `tests/test_alert_metrics.py`. |
| 13.10 | **pass (code)** | Backup compose profile + scripts; registry `promote`/`rollback` + `/admin/models/activate`; `PRPULSE_REQUIRE_MODELS` smoke. Full pg_dump→restore row-count proof: **not run** in this gate (backup profile not exercised end-to-end). |
| 13.11 | **pass (local)** | Locust 50 users / 30s; numbers in `reports/loadtest.md`. |
| 13.13 | **pass** | `PRIVACY.md`, retention job, delete script, footer + opt-out tests. |

### Pytest (full suite after 13.8–13.13 fixes)

`156 passed` (~130s).

### Docker compose health

`docker compose up --build` healthy: api `/health` 200 via proxy, postgres/redis healthy, worker listening. Postgres init race fixed with advisory lock.

### History scan

`git log -G` / grep for `ghp_` and PEM headers: **no matches**.

### Closeout (publish pass)
- Postgres pytest: 143 passed, 13 skipped.
- SQLite final: 156 passed.
- migrate + restore: all table counts matched (see reports/phase13_closeout.md).
- Loadtest 2 workers: webhook p50/p95/p99 = 760/1100/1300 ms; drain not completed (recovery re-enqueue).
- Alertmanager test alert received locally (reports/alert_test_received.json).
- History secret scan: clean. Working-tree fake tokens only in tests.
- Git toplevel is user home � init a dedicated repo in prpulse before push.

