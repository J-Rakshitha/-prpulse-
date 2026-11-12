# Phase 13 audit (before changes)

| Item | Status | Where today | Risk if left as-is |
| --- | --- | --- | --- |
| 13.1 Fast webhook + background queue | **partial** | `src/service/webhook.py` runs `process_event` / `PRBot` inline via `run_in_threadpool`; no Redis/RQ; compose has no worker | GitHub kills deliveries after **10s** ([docs](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)); model/API work can time out the ack |
| 13.2 GitHub App auth | **partial** | `build_github_client()` in `src/service/bot.py` uses `GITHUB_TOKEN` PAT only; App env vars listed in `.env.example` but unused | PAT is user-scoped, hard to rotate, wrong trust model for multi-repo installs |
| 13.3 Postgres | **missing** | `src/data/db.py` + `schema.sql` are SQLite-only; compose has no Postgres | Single-file DB does not survive multi-instance / real ops; no volume durability story |
| 13.4 Unseen-repo fallback | **missing** | `render_comment` always shows hour estimate + suggestions; no `coverage_status` column | Overconfident comments on repos outside training data |
| 13.5 Failure handling | **partial** | Bot catches `HTTPStatusError` and returns error; no retries/backoff/DLQ; no rate-limit reschedule; httpx timeout only on client default | Transient GitHub errors can drop work; failures may still race with delivery cache release |
| 13.6 Idempotency | **partial** | In-memory `DeliveryCache`; marker-based comment upsert + in-process `threading.Lock` | Restarts lose delivery ids; multi-worker can double-post; no durable delivery table |
| 13.7 Config / secrets | **partial** | `src/settings.py` loads yaml + dotenv; secrets not validated at API startup as a unit; no log redaction filter; `.env` gitignored | Misconfig fails late; logs may leak bodies/tokens; placeholder secrets unnoticed |
| 13.8 Dashboard auth | **missing** | Streamlit published on `8501`; `/metrics` public on API | Dashboard and metrics exposed without auth |
| 13.9 Monitoring / alerts | **partial** | `src/live/signals.py` + `deploy/prometheus.yml` + Grafana JSON; no Alertmanager, no alert rules file | No paging on queue/depth/ack latency/DLQ/rate-limit |
| 13.10 Backups / model rollback | **partial** | Retrain can `--promote`; registry has active version; no `rollback` CLI, no `pg_dump` job, no startup smoke gate that refuses serve | Cannot restore DB or cleanly roll back a bad promote; broken artifacts can serve silently |
| 13.11 Load test | **missing** | — | No measured local capacity numbers |
| 13.12 CI/CD | **skipped** | `.github/workflows/ci.yml` exists; out of scope | — |
| 13.13 Privacy / terms | **partial** | Short Privacy blurb in README; opt-out in bot; no `PRIVACY.md`, no deletion/retention scripts, weak comment footer | Cannot honour deletion/retention requests; incomplete disclosure |

## Notes

- Conservative webhook budget used later: respond well under **10s** (target ~1s ack). Source: GitHub “Respond within 10 seconds” best practice.
- Collector / training / `reports/results.md` numbers are out of scope for this phase.
