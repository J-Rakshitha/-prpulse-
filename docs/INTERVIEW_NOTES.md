# Interview notes (plain English)

## What problem PR Pulse solves

Reviewers and authors often ask “will this PR merge soon?” PR Pulse estimates that from **public GitHub metadata** (timestamps, author history, file paths, CI conclusions)—not from reading private source code. It posts one updating comment on a pull request with a merge-window estimate and related risk signals.

## Architecture in one paragraph

GitHub sends a signed webhook to a Caddy reverse proxy. The API verifies the signature, stores the delivery id, and enqueues a Redis/RQ job, then returns quickly. A **worker** process builds features, runs the model, and upserts a single bot comment. Predictions and outcomes live in **Postgres** (SQLite for local/tests). Prometheus/Alertmanager watch the service; a Streamlit dashboard sits behind basic auth.

## Why a time-based split

PRs are ordered by time into train / validation / test. A random split would leak the future into the past (later PRs helping predict earlier ones). Time order matches how the bot will be used: predict on new PRs using only history available before them.

## Data leakage and the ablation story

**Leakage** means using information that would not be known at prediction time. Collection-time file lists can include later pushes, so size/path features from `pr_files` were unsafe. An ablation compared models with and without those columns: Stage A `merged_within_7d` PR-AUC fell from **0.875** to **0.768** after removal (`reports/stage_a_leakage_ablation.md`). We kept the safer **0.768** model and reported the drop honestly.

## Why baselines matter; MAE vs MedAE

A model that only slightly beats a simple rule is not impressive. We compare to a **per-repo median** merge time (and classification baselines). **MAE** (mean absolute error) averages all errors and is pulled by long-tail PRs. **MedAE** (median absolute error) is the typical error and better reflects “how wrong are we for a normal PR.” Stage A continuous merge MedAE **lost** to the median baseline; Stage B/C MAE improved.

## Calibration

A probability is **calibrated** if “70%” events happen about 70% of the time. Classifiers use calibration (e.g. isotonic / frozen estimator paths in training). We report Brier scores alongside PR-AUC so sharp but overconfident models are visible (`reports/results.md`).

## Why Stage A, B, and C

- **A**: features known at open (or safely at open).
- **B / C**: early signals after the PR exists (e.g. activity within ~30 / ~120 minutes). Same question, more information as time passes—PR-AUC rises from 0.768 → 0.863 → 0.877 for merge-within-7d.

## Webhook, queue, worker, idempotency

The handler must answer GitHub within ~10 seconds, so it does not run the model inline. Each `X-GitHub-Delivery` id is stored uniquely; duplicates get 200 and are not reprocessed. Comment upserts use a hidden HTML marker so only one bot comment exists per PR, with a per-PR lock under concurrent workers.

## Unseen repositories

Training used two large repos. If a repo has fewer than **N=50** resolved PRs in the DB, the comment shows a low-confidence banner and a coarse time bucket instead of hour estimates and suggestions.

## What failed or did not beat the baseline

- Stage A continuous merge-time MedAE worse than per-repo median (heavy tail + L2-on-log).
- CI failure and CI duration models do not beat useful baselines on this split.
- Revert / deploy-risk positives are tiny; time-to-deploy was skipped.
- Live accuracy needs real closed PRs after install; that is not the same as offline metrics.

## Ten likely questions (short honest answers)

1. **Why not use the full diff?** We only use public metadata/paths; code content is out of scope for privacy and feature design.
2. **Is 0.768 PR-AUC good?** It beats the per-repo baseline (0.578); leakage ablation shows the higher 0.875 was partly unsafe features.
3. **Why Redis?** To ack webhooks fast and process asynchronously, as GitHub recommends.
4. **How do you avoid double comments?** Delivery id uniqueness + marker comment + PR lock.
5. **Why Postgres and SQLite?** SQLite for collector/tests; Postgres for multi-process production design.
6. **Did you deploy to production?** Production-ready **design** tested locally with Docker; no claim of a hosted multi-tenant production fleet.
7. **How do you handle rate limits?** Read GitHub headers, back off / reschedule; no comment on hard failure.
8. **What would you do next?** More repos, live-accuracy loop after outcomes, CI/CD, stronger CI labels.
9. **Why LightGBM?** Strong tabular baseline; team constraint was pandas/sklearn stack with LightGBM for boosters.
10. **Biggest risk in the demo?** Overstating Stage A merge-time or treating suggestions as causal.

## Five biggest limitations

1. Only **pandas** and **scikit-learn** in training data.
2. Unseen repos get **low-confidence** coarse output.
3. CI labels skewed to **recent** quarters (Actions retention).
4. File-size / collection-time path features **removed** (leakage).
5. Local load-test numbers are **not** production capacity; live accuracy needs time.
