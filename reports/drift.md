# Drift report

Generated at: `2026-11-07T03:14:08Z` (UTC). Stage `A`.

Reference PRs: `7158` (oldest, training period). Current PRs: `1533` (newest, holdout period).

PSI reading (convention): below 0.1 stable, 0.1 to 0.25 moderate, above 0.25 large.

| Feature | PSI | Status |
| --- | ---: | --- |
| `month` | 6.6075 | large |
| `open_pr_count_at_t0` | 5.5577 | large |
| `file_hist_fail_max` | 4.0197 | large |
| `file_hist_fail_mean` | 3.3248 | large |
| `prior_opened_count` | 1.1757 | large |
| `prior_merged_count` | 1.1751 | large |
| `prior_merge_rate` | 1.1569 | large |
| `prior_ci_fail_rate` | 1.0879 | large |
| `median_prior_merge_hours` | 0.8400 | large |
| `touches_tests` | 0.2511 | large |
| `touches_docs` | 0.2420 | moderate |
| `additions_approx` | 0.2419 | moderate |
| `log1p_additions_approx` | 0.2419 | moderate |
| `n_files_approx` | 0.2266 | moderate |
| `log1p_n_files_approx` | 0.2266 | moderate |
| `repo_is_pandas` | 0.2246 | moderate |
| `hour_utc` | 0.1763 | moderate |
| `commits_at_open` | 0.1017 | moderate |
| `touches_deps` | 0.0517 | stable |
| `weekday` | 0.0401 | stable |
| `first_time_contributor` | 0.0302 | stable |
| `deletions_approx` | 0.0284 | stable |
| `log1p_deletions_approx` | 0.0284 | stable |
| `is_friday_evening` | 0.0244 | stable |
| `is_weekend` | 0.0206 | stable |
| `docs_only` | 0.0097 | stable |
| `touches_auth` | 0.0023 | stable |
| `touches_migration` | 0.0017 | stable |
| `touches_ci_config` | 0.0000 | stable |
| `draft` | 0.0000 | stable |
| `median_merge_hours_30d` | 0.0000 | stable |
| `reviewer_activity_14d` | 0.0000 | stable |

Features with large shift: `month`, `open_pr_count_at_t0`, `file_hist_fail_max`, `file_hist_fail_mean`, `prior_opened_count`, `prior_merged_count`, `prior_merge_rate`, `prior_ci_fail_rate`, `median_prior_merge_hours`, `touches_tests`

