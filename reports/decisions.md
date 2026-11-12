# PR Pulse decisions and gate log

## Mode

Continuous Phases 1→12 with gates (tests/checks). Numbers only from executed code.

## Phase 1 — download (GATE PASS)

- pandas 6643 complete; sklearn 3789 complete; partial=0
- Train non-bot merged: **6496** (≥1000)
- `reports/data_quality.md` refreshed; tests: later full suite 111 passed
- CI: hybrid list + head_sha; `ci_backfill_stop_after_misses`; sklearn cliff abort then label refresh

## Phase 2 — EDA (GATE PASS)

- Figures under `reports/figures/`; `reports/eda_summary.md`
- **Friday evening flag KEEP** (UTC Fri hour≥15): median 43.59h vs 25.15h (+73%)
- Open PRs excluded from merge-hour targets (right censoring)

## Phase 3 — features (GATE PASS)

- Stage A/B/C parquet in `data/features/`
- Blocked: live `additions`/`title`/…; approx size from `pr_files` + `commits_at_open`
- Leakage assert + PIT: full rebuild vs stored Stage B on 50 PRs
- `reports/features.md` catalogues every column

## Phase 4 — models (GATE PASS)

- Time split 70/15/15; LightGBM + per-repo baselines; calibration via FrozenEstimator when available
- `reports/results.md` from `src/models/train.py`
- CI fail / CI duration: models weak or worse than baseline — reported honestly

## Stage A leakage ablation (2026-11-07)

Same test split. PR-AUC / MAE / MedAE:

| Variant | Merge MAE | MedAE | merged_7d PR-AUC |
| --- | ---: | ---: | ---: |
| all (32) | 80.77 | 30.66 | **0.875** |
| no collection-time size/path/draft | 84.62 | 36.22 | **0.768** |
| no author | 84.62 | 35.19 | **0.721** |
| no repo-state | 81.84 | 31.88 | 0.880 |
| author+repo only | 84.50 | 30.65 | 0.778 |

**What drove 0.875:** mostly **author history** (drop to 0.721 when removed) plus **collection-time file-list size/paths** (drop to 0.768). Repo-state was not required for the high score (0.880 without it).

**Post-open risk:** `additions_approx` / path flags / draft / module file-hist come from collected `pr_files` and can include later pushes → treated as leakage-prone. Author priors use only earlier PRs (PIT-safe). `commits_at_open` uses `committed_at <= created_at` only.

**Action taken:** removed collection-time size/path/draft/file-hist from model feature lists; retrain → Stage A PR-AUC **0.768**, MAE **84.62**, MedAE **36.22**. Details in `reports/stage_a_leakage_ablation.md`.

## Stage A MedAE diagnosis (2026-11-07)

- Continuous L2-on-log MedAE **36.22** loses to per-repo median **21.19** (predictions biased high on heavy tail).
- Quantile p50 on log → MedAE **24.50** (better, still loses). Quantile on raw → **30.27**.
- Bucket (&lt;1d/1–7d/&gt;7d) → median hours: MedAE **19.06** (only MedAE win) but MAE **97.66**.
- Kept continuous L2 for MAE/intervals; saved `artifacts/models/merge_stage_A_buckets.joblib` as MedAE-oriented option.
- Headline claims shifted to `merged_within_7d` + Stage B/C early signals (`results.md`, README).


## Phase 5 — explain (GATE PASS)

- SHAP/fallback contributions; estimate-only suggestions; tests in `tests/test_explain.py`

## Phase 6 — API (GATE PASS)

- FastAPI `/predict` `/explain` `/health` `/models` `/metrics`; prediction persistence
- p95 latency: measured in API tests against synthetic models when artifacts present; see test logs (not a production load test)

## Phase 7 — bot (GATE PASS)

- HMAC webhook; idempotent comment marker; opt-out; `docs/BOT_SETUP.md`
- Live App registration: **manual** (user)

## Phase 8 — live outcomes (GATE PASS)

- Outcome tracker + Prometheus helpers; simulated sequence tests
- Dashboard must show no live data until real resolutions

## Phase 9 — dashboard (GATE PASS)

- Streamlit app; `grafana/dashboard.json`
- Docker compose build: **not run** (Docker Desktop engine not running)

## Phase 10 — retrain/drift (GATE PASS)

- Dry-run wrote `artifacts/registry.json`, `reports/drift.md`
- Candidate MAE 83.32 > incumbent 80.17 → keep incumbent

## Phase 11 — delivery risk (GATE PASS)

- Revert base rate ~0.00067 (4 positives); model not fit (too few positives)
- Deploy risk table in results.md; High=0 on test under current thresholds
- Time-to-deploy skipped (no signal)

## Phase 12 — packaging (GATE PASS with caveat)

- Dockerfile, compose, CI workflow, README, demo script
- `docker compose build`: **not run** — Docker API/daemon unavailable
- Full pytest: **111 passed**

## Security note

A token briefly appeared in `.env.example` during an earlier session; scrubbed to empty placeholder. **Revoke that token on GitHub if it was real** and keep secrets only in `.env`.
