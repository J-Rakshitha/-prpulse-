# PR Pulse

Estimate whether a GitHub pull request is likely to **merge within 7 days**, plus a merge-time range that can refresh with early signals. Suggestions are **associational estimates**, not causal guarantees.

## What genuinely worked

| Result (from `reports/results.md`) | Number |
| --- | --- |
| `merged_within_7d` Stage A PR-AUC vs per-repo baseline | **0.768** vs **0.578** |
| Same classifier with Stage B / C early signals | **0.863** / **0.877** |
| Stage B / C merge-hour MAE vs baseline **85.79h** | **77.15h** / **74.65h** |
| Stage C MedAE vs baseline **21.19h** | **21.23h** (≈ tie) |
| Leakage ablation: removing collection-time file features | PR-AUC **0.875 → 0.768** (safer set kept) |

## What did not beat the baseline

- **Stage A continuous merge-time MedAE**: model **36.22h** vs per-repo median **21.19h** (worse). MAE only inches ahead (**84.62** vs **85.79**). See `reports/merge_medae_diagnosis.md`.
- **CI failure** and **CI duration** models do not beat useful baselines on this split (`reports/results.md`).
- **Revert / deploy-risk** positives are tiny; time-to-deploy was skipped.
- Offline metrics are **not** the same as live accuracy; live accuracy needs resolved webhook outcomes over time (not claimed as measured here).

## Status

Phases 0–13: **production-ready design**, trained on `pandas-dev/pandas` and `scikit-learn/scikit-learn` (2-year lookback), **tested locally** with Docker Compose, pytest (SQLite + Postgres), Locust, and a local Alertmanager webhook. Metrics live only in generated reports under `reports/`.

## Architecture

```mermaid
flowchart LR
  GH[GitHub webhooks / REST] --> Proxy[Caddy :8080]
  Proxy --> API[FastAPI api]
  Proxy -->|basic auth /dashboard| Dash[Streamlit]
  API -->|persist + enqueue| PG[(Postgres)]
  API --> Redis[(Redis / RQ)]
  Redis --> Worker[RQ worker]
  Worker --> PG
  Worker --> GHAPI[GitHub API]
  API --> Prom[Prometheus]
  Prom --> AM[Alertmanager]
  Prom --> Graf[Grafana]
  PG --> Backup[pg_dump backup]
  Artifacts[artifacts/models registry] --> API
  Artifacts --> Worker
```

## Quick start (fresh clone)

```powershell
git clone <your-fork-url> prpulse
cd prpulse
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Set at least WEBHOOK_SECRET (and GITHUB_TOKEN if you will collect data).
# For Docker, Postgres is published on host port 5433 (see docker-compose.yml).

# Unit tests (SQLite, default):
python -m pytest -q

# Optional: tests against Compose Postgres (after `docker compose up -d postgres`):
# $env:PRPULSE_TEST_DATABASE_URL='postgresql://prpulse:prpulse@127.0.0.1:5433/prpulse_test'
# python -m pytest -q

docker compose up --build
# Public surface: http://127.0.0.1:8080  (health, webhook, predict)
# Dashboard:     http://127.0.0.1:8080/dashboard  (basic auth; default user prpulse)
# /metrics is not exposed on the public proxy.
```

Local API without Docker (after `python tasks.py train` or with existing artifacts):

```powershell
python tasks.py init-db
python tasks.py serve
```

Bot setup: [`docs/BOT_SETUP.md`](docs/BOT_SETUP.md). GitHub App: [`docs/GITHUB_APP.md`](docs/GITHUB_APP.md). Privacy: [`PRIVACY.md`](PRIVACY.md).

## Production-ready design (tested locally)

| Area | Status | Notes |
| --- | --- | --- |
| Async webhook + RQ worker | done | Ack path avoids model/GitHub work |
| GitHub App auth | code done | Live App install needs your credentials |
| Postgres + Alembic + migrate script | done | SQLite for collector/default unit tests |
| Unseen-repo coverage | done | N=50; coarse bucket + banner |
| Retries / DLQ / rate limits | done | `failed_jobs` + replay |
| Idempotency / locks | done | delivery_id UNIQUE + PR locks |
| Secrets / redaction | done | env-only secrets; log filter |
| Dashboard auth (Caddy) | done | `/metrics` denied on public port |
| Alerts + Alertmanager | done | observability profile; tested via local webhook |
| Backups + model rollback | done | dump/restore row counts matched in local proof |
| Load test | local only | see `reports/loadtest.md` |
| CI/CD deploy | not in this repo phase | workflows may exist; hosting is on you |

## Limitations

- Only **pandas-dev/pandas** and **scikit-learn/scikit-learn** in training.
- Unseen repos get **low-confidence** coarse buckets, not hour estimates.
- CI labels are skewed to **recent quarters** (Actions retention / list caps).
- Collection-time file-size/path features were **removed** after a leakage ablation.
- Suggestions are **estimates**, not causal “do X to save Y hours.”
- Local Locust numbers are **not** a production capacity claim.
- Live accuracy needs resolved outcomes over time after a real install.
- Prior research exists on PR timing; this is an engineering system, not a SOTA claim.

## Demo and interview

- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
- [`docs/INTERVIEW_NOTES.md`](docs/INTERVIEW_NOTES.md)
- [`docs/RESUME_BULLETS.md`](docs/RESUME_BULLETS.md)

## License

MIT — see [`LICENSE`](LICENSE).
