# PR Pulse Phase 2 — Exploratory Data Analysis Summary

All numbers below are computed directly from `data/prpulse.db`. Primary analysis is restricted to non-bot pull requests in the approved training repositories (`pandas-dev/pandas` and `scikit-learn/scikit-learn`).

## 1. Merge-Time Distribution (`reports/figures/merge_hours_log_hist.png`)

- **Train non-bot merged PRs:** `6496` (`pandas-dev/pandas`: `4268`, `scikit-learn/scikit-learn`: `2228`).
- **Combined percentiles (hours):** P10 = `1.72h`, P25 = `5.36h`, **Median (P50) = `25.30h`**, P75 = `142.46h`, **P90 = `593.94h`**, Mean = `249.47h` (min `0.0022h`, max `12027.77h`).
- **Merged within 7 days (`<= 168h`) among merged train PRs:** `77.5%`.
- **Per-repo merge hours:**
  - `pandas-dev/pandas` (`n=4268`): P10 = `1.62h`, Median = `23.47h`, P90 = `459.86h`, merged `<= 7d` = `79.4%`.
  - `scikit-learn/scikit-learn` (`n=2228`): P10 = `1.91h`, Median = `31.60h`, P90 = `792.73h`, merged `<= 7d` = `74.1%`.
- **Implication for modeling:** Merge times span more than four orders of magnitude and are strongly right-skewed; regression targets should be modeled on `log1p(merge_hours)` with quantile loss (`P10`/`P50`/`P90`).

## 2. Merge Rate by Repository (`reports/figures/merge_rate_by_repo.png`)

| Repo | Role | Non-bot PRs | Merged | Closed | Open | Merge rate (resolved non-bot) | Merge rate (all non-bot) | Merge rate (resolved all incl. bots) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `encode/httpx` | Test (Phase 0) | 159 | 37 | 62 | 60 | 37.4% | 23.3% | 36.0% |
| `pallets/flask` | Test (Phase 0) | 316 | 55 | 258 | 3 | 17.6% | 17.4% | 17.6% |
| `pandas-dev/pandas` | Train | 6463 | 4268 | 1873 | 322 | 69.5% | 66.0% | 70.0% |
| `psf/requests` | Test (Phase 0) | 482 | 91 | 348 | 43 | 20.7% | 18.9% | 27.9% |
| `scikit-learn/scikit-learn` | Train | 3763 | 2228 | 1207 | 328 | 64.9% | 59.2% | 64.8% |

## 3. Open-PR Right-Censoring (`reports/figures/open_pr_censoring.png`)

- **Train non-bot PRs (`n=10226`):** `6496` merged (`63.5%`), `3080` closed unmerged (`30.1%`), `650` open (`6.4%`).
- **Train all PRs including bots (`n=10432`):** `6671` merged (`63.9%`), `3110` closed unmerged (`29.8%`), `651` open (`6.2%`).
- **Per-repo non-bot outcomes:**
  - `pandas-dev/pandas`: `4268` merged, `1873` closed, `322` open.
  - `scikit-learn/scikit-learn`: `2228` merged, `1207` closed, `328` open.
- **Implication:** Recently opened PRs that are still `open` within 7 days of collection are right-censored for the `merged_within_7d` classification label and must be excluded if `collection_time - created_at < 7 days`.

## 4. Weekday × Hour UTC Patterns (`reports/figures/weekday_hour_heatmap.png`)

- **Peak creation cell (train merged non-bot):** `Mon` at `05:00 UTC` (`317` merged PRs, median `4.09h`).
- **Merged PR count and median merge hours by created weekday (UTC):**
  - `Mon`: `1295` PRs, median merge time `17.82h`
  - `Tue`: `939` PRs, median merge time `26.66h`
  - `Wed`: `934` PRs, median merge time `23.93h`
  - `Thu`: `968` PRs, median merge time `25.19h`
  - `Fri`: `972` PRs, median merge time `46.10h`
  - `Sat`: `709` PRs, median merge time `43.79h`
  - `Sun`: `679` PRs, median merge time `31.35h`

## 5. Size vs Merge Hours (`reports/figures/size_vs_merge_hours.png`)

- **Correlation (`log10(additions + 1)` vs `log10(merge_hours)`):** Pearson `r = 0.345`, Spearman `rho = 0.342` across `6496` merged non-bot train PRs.
- **Additions summary:** median = `21` lines, P90 = `151` lines.
- **Median merge hours by collection-time additions bucket:**
  - `<= 10` additions (`n=2336`): `12.17h`
  - `11..100` additions (`n=3174`): `35.45h`
  - `> 100` additions (`n=986`): `107.91h`
- **Leakage note:** `prs.additions` is fetched from `GET /pulls/{n}` at collection time (not point-in-time at PR open). It includes commits pushed during code review, which inflates the correlation with merge duration and why `additions` remains in `UNAPPROVED_FEATURE_COLUMNS`.

## 6. Friday-Evening Flag Effect (`reports/figures/friday_flag_effect.png`, `reports/figures/friday_effect.txt`)

- **Definition:** `created_at` on Friday (`weekday == 4`) with `hour_utc >= 15`.
- **Sample sizes (train merged non-bot):** Friday evening `n = 450` vs Other times `n = 6046`.
- **Median merge hours:** Friday evening = **`43.59h`** (IQR `2.97h`–`158.52h`, P90 `663.79h`) vs Other = **`25.15h`** (IQR `5.69h`–`142.16h`, P90 `590.82h`).
- **Effect size:** **`+18.44h`** (`+73.3%`, ratio `1.73x`).
- **Per-repo breakdown:**
  - `pandas-dev/pandas`: Friday evening `n=343`, median `17.96h` vs Other `n=3925`, median `23.73h` (delta `-5.77h`, ratio `0.76x`).
  - `scikit-learn/scikit-learn`: Friday evening `n=107`, median `93.69h` vs Other `n=2121`, median `28.78h` (delta `+64.91h`, ratio `3.25x`).
- **Recommendation: `KEEP` the Friday-evening flag.** Pooled across the training set, PRs opened on Friday >= 15:00 UTC have a `+18.44h` (`+73.3%`) higher median merge time (`43.59h` vs `25.15h`), with strong heterogeneity between repositories: `scikit-learn/scikit-learn` shows a `+64.91h` (`3.25x`) increase (`93.69h` vs `28.78h`), whereas `pandas-dev/pandas` shows `17.96h` vs `23.73h` (`-5.77h`, `0.76x`). Because `created_at` is strictly known at Stage A (point-in-time safe) and gradient-boosted trees can interact it with repository/weekday/hour signals, we keep `is_friday_evening`.

## 7. CI Label Availability by Quarter (`reports/figures/ci_label_by_quarter.png`)

| Quarter | `pandas-dev/pandas` (`ci_label_available=1 / total`) | `scikit-learn/scikit-learn` (`ci_label_available=1 / total`) | Combined Train Share |
| --- | ---: | ---: | ---: |
| `2024-Q4` | 0/303 (0.0%) | 0/223 (0.0%) | 0/526 (0.0%) |
| `2025-Q1` | 3/367 (0.8%) | 5/392 (1.3%) | 8/759 (1.1%) |
| `2025-Q2` | 15/308 (4.9%) | 26/415 (6.3%) | 41/723 (5.7%) |
| `2025-Q3` | 186/553 (33.6%) | 103/450 (22.9%) | 289/1003 (28.8%) |
| `2025-Q4` | 748/753 (99.3%) | 532/532 (100.0%) | 1280/1285 (99.6%) |
| `2026-Q1` | 1183/1209 (97.8%) | 513/516 (99.4%) | 1696/1725 (98.3%) |
| `2026-Q2` | 962/975 (98.7%) | 579/579 (100.0%) | 1541/1554 (99.2%) |
| `2026-Q3` | 1396/1401 (99.6%) | 479/481 (99.6%) | 1875/1882 (99.6%) |
| `2026-Q4` | 771/774 (99.6%) | 201/201 (100.0%) | 972/975 (99.7%) |

- **Retention cliff finding:** GitHub Actions workflow-run retention creates a sharp boundary in `2025-Q3` (`33.6%` in `pandas`, `22.9%` in `scikit-learn`). From `2025-Q4` through `2026-Q4`, `ci_label_available=1` coverage is `97.8%`–`100.0%` in both train repos, whereas `2024-Q4` through `2025-Q2` has `< 6.5%` coverage. CI failure models must filter on `ci_label_available = 1`.
