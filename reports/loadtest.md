# Phase 13 load test (local only)

**Not a production capacity claim.** Numbers are from one laptop run against `docker compose` on the same machine.

## Machine

| Spec | Value |
| --- | --- |
| OS | Microsoft Windows 11 Home Single Language |
| CPU | 12th Gen Intel Core i5-1235U (10 cores / 12 threads) |
| RAM | 15.7 GB |
| Host | local Docker Desktop + Locust on host |

## Common run settings

- Script: `loadtest/locustfile.py`
- Target: `http://127.0.0.1:8080` (Caddy proxy → api)
- Users: **50** concurrent, spawn-rate 25, duration **30s**, headless
- Signed `pull_request` webhooks (unique delivery ids) + `/predict`
- Date: 2026-11-07

## Comparison: 1 worker vs 2 workers

| Metric | 1 worker (earlier run) | 2 workers (this run) |
| --- | ---: | ---: |
| `/webhook` requests | 867 | 998 |
| `/webhook` failures | 0% | 0% |
| `/webhook` ack p50 (ms) | 870 | 760 |
| `/webhook` ack p95 (ms) | 1300 | 1100 |
| `/webhook` ack p99 (ms) | 1400 | 1300 |
| `/predict` p50 / p95 / p99 (ms) | 1800 / 2400 / 2500 | 1300 / 2000 / 2100 |
| Queue drain to empty | **not completed** (backlog left; see notes) | **not completed** — after Locust, ~1018 deliveries stayed `pending`/`processing` while recovery repeatedly re-enqueued (RQ depth grew past 5000); workers also hit work-horse kills under load |

Raw CSV: `reports/loadtest_raw_stats.csv` (1w), `reports/loadtest_2w_stats.csv` (2w).

## Notes

- GitHub hard ack limit is 10s; design target ~1s. Observed webhook ack p95 was about 1.1–1.3s under this local burst.
- First 1-worker attempt left deliveries `pending` until RQ `job_id` stopped using `:`.
- Full end-to-end drain time is **not reported as a single number** because the queue did not empty under the recovery+timeout conditions above. Ack latency comparison is still valid (Locust measures HTTP `/webhook` only).
