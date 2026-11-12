# Results

Generated at: `2026-11-07T03:30:00Z` (UTC) — tables below are from executed training / diagnosis scripts on the fixed time split.

Train/validate/test are time-ordered splits on non-bot PRs from `pandas-dev/pandas` and `scikit-learn/scikit-learn` only.

## Headline (read this first)

The **strong offline results** are:

1. **`merged_within_7d` classifier** — Stage A PR-AUC **0.768** vs per-repo baseline **0.578**; Stage B/C rise to **0.863 / 0.877** with early signals.
2. **Stage B and C early-signal merge-hour models** — MAE improves vs baseline (**77.15 / 74.65** vs **85.79**); Stage C MedAE (**21.23**) essentially matches the baseline MedAE (**21.19**).

**Stage A continuous merge-time regression does not beat the per-repo median on MedAE** (model MedAE **36.22** vs baseline **21.19**). See diagnosis below. Do not headline Stage A merge MAE/MedAE as a win.

## Merge hours — MAE and MedAE by stage (same test split, n=975)

Continuous LightGBM trained with L2 on `log1p(merge_hours)`, then `expm1` for hour-scale metrics. Baseline = per-repo median merge hours fit on train only.

| Stage | Baseline MAE (h) | Model MAE (h) | Baseline MedAE (h) | Model MedAE (h) | 10–90 coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 85.79 | 84.62 | 21.19 | 36.22 | 0.864 |
| B | 85.79 | 77.15 | 21.19 | 29.63 | 0.834 |
| C | 85.79 | 74.65 | 21.19 | 21.23 | 0.805 |

## Stage A MedAE diagnosis (why 36.22 vs baseline 21.19)

**Cause:** L2 on log-hours targets a log-space mean; after `expm1` predictions are pulled up by the heavy right tail (test y median **23.1h**, P90 **292h**, **18%** of test merges take >7 days). The L2 model’s predicted median on test is **45.7h** vs baseline **22.9h**. On the y≤7d subset alone, L2 MedAE is still worse (**31.1** vs **19.8**). The baseline directly optimizes absolute error at the median.

| Variant | MAE (h) | MedAE (h) | MAE(log1p h) | Beats baseline MedAE? |
| --- | ---: | ---: | ---: | --- |
| `baseline_per_repo_median` | 85.79 | **21.19** | 1.396 | — |
| `l2_on_log1p_expm1` (current continuous) | 84.62 | 36.22 | 1.348 | no |
| `quantile_p50_on_log1p_expm1` | 84.09 | 24.50 | **1.259** | no |
| `quantile_p50_on_raw_hours` | 82.84 | 30.27 | 1.291 | no |
| `bucket_clf_then_bucket_median_hours` | 97.66 | **19.06** | 1.732 | **yes** |

Bucket task (&lt;1d / 1–7d / &gt;7d): model accuracy **0.542** macro-F1 **0.346** vs baseline-bucket accuracy **0.506** macro-F1 **0.284**.

**Keep for MedAE:** only the coarse **bucket → bucket-median hours** variant beats baseline MedAE (**19.06 &lt; 21.19**), at the cost of worse MAE (**97.66**). **No continuous regressor (L2 or quantile p50) beats baseline MedAE.** Production continuous merge models remain L2-on-log for MAE/intervals; Stage A point-estimate MedAE should be reported as **not beating** the median baseline unless the bucket model is explicitly used.

## merged_within_7d classifier (calibrated) — primary strong result

| Stage | n_test | train base rate | Baseline PR-AUC | Model PR-AUC | Baseline ROC-AUC | Model ROC-AUC | Model Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 1521 | 0.501 | 0.578 | **0.768** | 0.588 | 0.799 | 0.168 |
| B | 1521 | 0.501 | 0.578 | **0.863** | 0.588 | 0.859 | 0.155 |
| C | 1521 | 0.501 | 0.578 | **0.877** | 0.588 | 0.873 | 0.145 |

## Stage A leakage ablation (context)

Collection-time `pr_files` size/path/draft columns were removed from model features after ablation (see `reports/stage_a_leakage_ablation.md`). With the pre-removal feature set, Stage A PR-AUC was 0.875; without those columns it is **0.768**. Author history remains the largest PIT-safe driver.

| Variant | Merge MAE | Merge MedAE | merged_7d PR-AUC |
| --- | ---: | ---: | ---: |
| `1_all` (incl. collection-time size/path) | 80.77 | 30.66 | 0.875 |
| `2_no_collection_time` (current models) | 84.62 | 36.22 | 0.768 |
| `3_no_author` | 84.62 | 35.19 | 0.721 |
| `4_no_repo_state` | 81.84 | 31.88 | 0.880 |
| `5_author_repo_only` | 84.50 | 30.65 | 0.778 |

## CI failed first run (`ci_label_available=1` only)

| Stage | n_test | train base rate | Baseline PR-AUC | Model PR-AUC | Model ROC-AUC | Model Brier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 1131 | 0.009 | 0.001 | 0.001 | 0.552 | 0.001 |
| B | 1131 | 0.009 | 0.001 | 0.001 | 0.036 | 0.001 |
| C | 1131 | 0.009 | 0.001 | 0.001 | 0.082 | 0.001 |

## CI duration minutes (Stage A)

- n_test=`1064`; baseline MAE=`0.06` MedAE=`0.00` min; model MAE=`3.19` MedAE=`2.91` min; 10-90 coverage=`0.964` — **model does not beat baseline**.

## Leakage / sanity

- Forbidden columns asserted; collection-time size/path proxies removed from model matrices.
- Stage A `merged_within_7d` PR-AUC 0.768 &lt; 0.95; driven mainly by PIT-safe author history + timing/repo features.
