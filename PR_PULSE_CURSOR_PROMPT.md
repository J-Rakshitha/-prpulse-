PR Pulse: Master Build Prompt for Cursor

Paste this whole file into Cursor (Agent mode) as the project brief. Then tell Cursor: "Start with Phase 0. Stop at every STOP and wait for me to say continue."

0. ROLE AND WORKING RULES

You are a senior ML engineer building "PR Pulse" end to end with me. I am a student who must be able to explain every design choice in an interview.

Rules for the whole project:

Work in phases. At the end of each phase, STOP, print a plain-English summary (what you built, why, assumptions, anything uncertain), and wait for me to say "continue".
If an API detail, field name or limit is uncertain, say so and ask me. Never guess silently.
NO DATA LEAKAGE. Every model feature must be knowable at the moment the prediction is made (see section 4). Maintain reports/features.md listing each feature, its source and the time it becomes known. Add a pytest that fails if a forbidden column enters the feature matrix.
TIME-BASED SPLITS ONLY (train on older data, validate on the next period, test on the newest). Never a random split.
Never hardcode secrets. Read from .env (GITHUB_TOKEN, WEBHOOK_SECRET, GITHUB_APP credentials if used).
Small readable functions, docstrings, type hints, pytest tests for leakage, split logic, feature building and the API.
Never claim results you have not run. All numbers in README must come from reports/results.md produced by the code.
Suggestions shown to users are model estimates (correlation), not causal guarantees. Wording must say "estimate".
1. WHAT PR PULSE IS

A GitHub bot plus dashboard. When a pull request (PR) is opened, PR Pulse comments with:

Predicted time to merge, with an uncertainty range
Calibrated probability the PR merges within 7 days
Probability CI (GitHub Actions) fails
Top reasons (SHAP) for delay or risk
Concrete suggestions (estimates) Predictions update as early signals arrive (reviewer assigned, first CI result, first comment). After the PR resolves, the real outcome is stored automatically, so live accuracy against a baseline is measured continuously. Maintainers get a dashboard of open PRs ranked by "stuck risk".

Unique angles to keep: live bot, delayed labels that arrive automatically, live-measured accuracy vs baseline, calibration, SHAP explanations, early-signal rolling updates, delivery-risk extension (CI failure, revert risk).

2. ARCHITECTURE
GitHub REST API (history)          GitHub Webhooks (live PR events)
        |                                     |
   Collector (resumable)              FastAPI /webhook (signature verified)
        |                                     |
        +--------> Postgres (SQLite for dev) <+
                         |
        Feature builder (point-in-time correct)
                         |
   Training (LightGBM): merge-time quantile regressors,
   merged<=7d classifier, CI-fail classifier, calibration
                         |
              Model registry (versioned files + MLflow optional)
                         |
   FastAPI inference service: /predict, /explain, /health
                         |
   Bot: posts/updates one PR comment (idempotent)
                         |
   Outcome tracker (on merge/close, store actual vs predicted)
                         |
   Dashboard (Streamlit or React) + Grafana metrics
                         |
   Weekly retrain job + drift check (PSI) -> promote model if better

Repo layout:

prpulse/
  config.yaml
  .env.example
  requirements.txt
  docker-compose.yml  Dockerfile
  src/
    data/        collect.py  db.py  schema.sql
    features/    build.py  leakage.py
    models/      train.py  evaluate.py  calibrate.py  explain.py  registry.py
    service/     api.py  webhook.py  bot.py  schemas.py
    live/        outcomes.py  signals.py  retrain.py  drift.py
    dashboard/   app.py
  tests/
  reports/       features.md  results.md  figures/
  notebooks/     eda.ipynb
  README.md

Database tables: repos, prs (static fields at open time), pr_files, pr_events (timestamped: reviewer_assigned, first_comment, ci_started, ci_finished, review_submitted, merged, closed), ci_runs, authors_history_view, predictions (pr, model_version, made_at, stage, predicted values, shap json), outcomes (actual merge hours, merged flag, ci result), model_versions.

3. PHASES
Phase 0: Setup
Python 3.11 project structure above, requirements, .env.example, config.yaml (list of repos, lookback years, thresholds), SQLite dev DB with schema.
Logging, a Makefile or tasks file (collect, features, train, serve).
STOP. Summarise structure.
Phase 1: Data collection
Use the GitHub REST API (and GraphQL only if it clearly reduces calls) to download PRs from the last 2 years for repos in config.yaml (test with 3 repos first, then 10 to 15 active ones).
Save: repo, number, author, created_at, merged_at, closed_at, state, additions, deletions, changed_files, commits, title, body, labels, draft flag, base branch, changed file paths, review requests, and the timeline events with timestamps (review requested, reviewed, commented, merged, closed).
Also collect GitHub Actions workflow runs per PR (conclusion, started_at, completed_at) for the CI targets. If a repo has no Actions data, mark it and exclude it from CI tasks.
Pagination, rate-limit handling (check remaining, sleep until reset), retries with backoff, resumable (skip stored items), progress logging.
Data-quality report: counts per repo, merge rate, missing fields, bot-authored PRs (flag and exclude dependabot/renovate from training, keep a bot flag).
STOP. Show the data-quality report.
Phase 2: EDA
Notebook: distribution of merge time (log scale), merge rate per repo, weekday/hour effects, size vs time, right-censoring note (open PRs have unknown outcome; exclude them from targets).
STOP.
Phase 3: Features (point-in-time correct)

Define three prediction stages so predictions can be refreshed:

Stage A "at open" (T0): only information at created_at.
Stage B "T0+30min": adds early signals known by then.
Stage C "T0+2h": adds more signals.

Stage A features:

Size: additions, deletions, changed_files, commits, total lines, log transforms
Content: title length, body length, has description, tests touched, docs-only, config/dependency files touched, risky-path flags (auth, db, migrations, build/CI config), label flags, draft flag
Author (computed strictly from data before created_at): prior merged PRs in repo, prior merge rate, median prior merge time, first-time contributor, prior CI fail rate, bot flag
File-level failure history (strictly before created_at): for the files touched by this PR, how many earlier PRs touching them had a failed first CI run, were later reverted, or needed a follow-up fix; aggregate as mean and max over touched files and per top-level module
Dependency/lockfile changes (package.json, requirements, go.mod, etc.), CI/build config changes
Timing: hour, weekday, weekend, month, "Friday afternoon or evening" flag
Repo state at created_at: open PR count, median merge time of last 30 days (only PRs merged before created_at), reviewer activity in last 14 days Stage B/C adds: reviewer assigned (yes/no, count), minutes to first comment, minutes to first CI result, CI status so far, review state so far.

Targets:

merge_hours (merged PRs only, modeled on log scale)
merged_within_7d (only PRs with at least 7 days of history)
ci_failed_first_run (first workflow run conclusion, only repos with Actions)
ci_duration_minutes (first full CI run duration, log scale, only completed runs, only repos with Actions)

Leakage guard (leakage.py): a forbidden-column list (final comment count, total review rounds, later commits, merged_by, merge commit info) plus a test that builds features for a sample PR using only events before its stage cutoff and asserts equality with the stored features.

STOP. Show reports/features.md.
Phase 4: Baselines and models
Time-based split: train oldest ~70%, validation next ~15%, test newest ~15%, by created_at (also report per-repo results).
Baselines (fit on train only): per-repo median merge time; per-repo merge rate; per-repo CI failure rate; plus a "global median" fallback for unseen repos.
Models: LightGBM regressor on log merge hours, LightGBM quantile regressors (10th, 50th, 90th percentile) for ranges, LightGBM classifiers for merged_within_7d and ci_failed_first_run. Tune lightly with time-series CV on the training period only.
Metrics: MAE and median absolute error (hours), pinball loss and interval coverage (does the 10-90 range really contain ~80%?), PR-AUC, ROC-AUC, Brier score, calibration curves.
Calibration: isotonic or Platt on the validation period, evaluated on test.
Ablation: effect of author features, repo-state features, early signals (Stage A vs B vs C).
Error analysis: where does the model fail (huge PRs, first-time contributors, specific repos).
Write everything to reports/results.md with tables and figures. Be honest if the model does not beat the baseline.
STOP. Show results.md.
Phase 5: Explainability and suggestions
SHAP: global summary plot, and explain(pr) returning top 5 signed contributions in plain English ("diff size +1.8 days", "no tests +0.4 days").
Suggestions engine (estimates only): re-predict with a modified feature (e.g. smaller size, tests added, reviewer assigned) and report the predicted change, labelled "estimate, correlation not causation". Only suggest changes the author can realistically make.
STOP.
Phase 6: Inference API

FastAPI:

POST /predict (PR details, stage) returns merge-time median and 10-90 range, P(merge<=7d), P(CI fail), top reasons, suggestions, model_version
POST /explain, GET /health, GET /models
Pydantic schemas, input validation, model loaded once, latency logged, prediction rows stored in predictions.
Tests with sample payloads.
STOP.
Phase 7: GitHub bot (live)
POST /webhook: verify the signature (HMAC with WEBHOOK_SECRET), handle pull_request (opened, ready_for_review, review_requested, synchronize), pull_request_review, issue_comment, check_run/workflow_run, and pull_request closed/merged.
On open: fetch needed data, build Stage A features, predict, post ONE comment. On later events: update the same comment (idempotent, find it by a hidden marker), re-predict at Stage B and C.
Comment format (short, readable): predicted time with range, merge chance, CI fail risk, top 3 reasons, 1 or 2 suggestions, a footer "estimate by PR Pulse, model vX".
Opt-out via a config file in the repo (.prpulse.yml) or a label.
Document how to run locally with ngrok and how to register the webhook or GitHub App; list the manual steps I must do myself.
STOP.
Phase 8: Live evaluation and outcomes
On merge/close/CI finish, store the actual outcome and compute errors for every stored prediction (per stage).
Live metrics: rolling MAE vs baseline, calibration of the live probabilities, interval coverage, number of predictions, all by week.
Persist metrics to a table and expose a Prometheus /metrics endpoint.
STOP.
Phase 9: Dashboard
Streamlit (or React) with pages: (1) Live accuracy (model vs baseline over time, calibration), (2) Open PRs ranked by stuck risk with reasons, (3) Repo health (median merge time trend, CI fail rate), (4) Model info and drift.
Grafana dashboard JSON for latency, request volume, live MAE, drift.
STOP.
Phase 10: Retraining and drift
Weekly job: rebuild features, retrain on all resolved PRs, compare with the current model on the newest holdout, promote only if better, keep version history and a rollback.
Drift: PSI on key features and prediction distribution, alert (log plus dashboard flag) when high.
STOP.
Phase 11: Delivery-risk extension (do only after Phases 0 to 10 are stable)

Goal: extend PR Pulse from "when will it merge" to "how risky is it to ship".

11.1 Build/test duration prediction

Model ci_duration_minutes (LightGBM regressor on log scale, plus 10/50/90 quantiles) using Stage A features. Baseline: per-repo median CI duration. Report MAE and interval coverage vs baseline.
Bot shows "CI will likely take about X minutes (range a to b)".

11.2 CI failure model upgrade

Make sure the file-level failure history features from Phase 3 are used. Add an ablation table: with vs without file history.

11.3 Revert/hotfix risk

Label = a later PR that reverts this one (detect PRs titled "Revert ..." or referencing the original PR/commit) within 30 days after merge. Only PRs with at least 30 days of post-merge history are labelled.
Expect heavy class imbalance. Report base rate, PR-AUC, recall at a fixed precision, and calibration. Say plainly if the model is weak.

11.4 Time-to-deploy (conditional)

Only for repos with clear release or deploy workflow runs or tags. Label = time from merge to the first release/deploy event containing the merge commit. If a repo has no such signal, skip it and document why in results.md. Do not invent a deploy signal.

11.5 Combined Deploy Risk Score

Combine P(merge delay beyond threshold), P(CI fail), and P(revert) into one score (Low / Medium / High) with a transparent, documented rule (not a black box), and show the top reasons from SHAP (e.g. "touches auth module", "no tests", "Friday evening", "dependency change").
Evaluate the score: do High-risk PRs actually have more CI failures and reverts than Low-risk ones on the test period? Show the table.

11.6 Product features

Bot comment gets a "Deploy risk" line with reasons.
Merge-timing suggestion (estimate only): for example "risk is higher on Friday evening; merging Monday morning may lower it", only if the model shows a real timing effect in the data. Otherwise do not show it.
Estimate-only suggestions such as "add tests for this file" or "split this PR", computed by re-predicting with the modified feature and labelled as estimates.
Maintainer dashboard "Risky PRs" page: open PRs ranked by combined risk with reasons.

11.7 Explicitly NOT included

Post-merge incident or bug risk. Labels linking a later issue to this PR are too noisy to defend. Do not build it.
STOP. Show updated results.md and limitations.
Phase 12: Packaging and docs
Dockerfile and docker-compose (api, db, dashboard, optional grafana/prometheus), .env.example, CI workflow running tests and lint.
README: problem, architecture diagram (mermaid), results table copied from reports/results.md, live-evaluation section, limitations, how to run, how to install the bot on a repo, ethics/privacy note (only public PR metadata, no private code content).
Limitations section must state: prior research exists on PR merge-time prediction; live labels arrive after days; small repos have little data; suggestions are estimates; CI/revert labels are imbalanced.
Demo script for a 3-minute video.
STOP. Final checklist.
4. LEAKAGE RULES (summary)

Allowed only if known at the stage cutoff: static PR fields at creation, author/repo history strictly before created_at, early events with timestamps before the stage cutoff. Forbidden: final comment or review counts, final commit count, anything after merge/close, merged_by, time spent in review, final labels added after the cutoff, outcome-derived repo statistics that include the PR itself or later PRs.

5. FEATURE CHECKLIST (what the finished product must have)

ML: baselines, LightGBM regression, quantile ranges, calibrated probabilities, CI-failure model, CI-duration model, revert-risk model, file-level failure history features, staged predictions, ablations, error analysis, time-based evaluation. Delivery risk: combined Deploy Risk Score validated against real CI failures and reverts, risky-PR queue, timing suggestion only if supported by data. Explainability: SHAP global and per-PR, plain-English reasons, estimate-only suggestions. Product: webhook bot with a single updating comment, opt-out, maintainer stuck-risk queue, dashboard. MLOps: versioned models, live outcome tracking, live accuracy vs baseline, drift detection, weekly retrain with safe promotion, Prometheus/Grafana, Docker, CI tests. Honesty: README limitations, numbers only from generated reports.

6. MANUAL STEPS FOR ME (list these clearly when relevant)

GitHub token creation, webhook or GitHub App registration, ngrok, creating a test repo and opening test PRs, installing the bot on a few repos, deciding which repos to monitor.
</user_query>