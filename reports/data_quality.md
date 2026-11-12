# PR Pulse data-quality report

Generated at: `2026-11-05T12:01:50Z` (UTC)
Lookback cutoff (`created_at` >=): `2024-11-05T12:01:50Z`
API requests in this collector process: `0`

## Scope

Repos listed in `config.yaml` for this run: `pandas-dev/pandas`, `scikit-learn/scikit-learn`. Other repos already in the database from earlier waves are included below.

## Per-repo summary

| Repo | PRs | Complete | Partial | Merged | Closed | Open | Merge rate (resolved) | Bots | Reopened | CI runs | PRs with any CI | ci_label_available=1 | Non-bot merged |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| encode/httpx | 177 | 177 | 0 | 41 | 73 | 63 | 36.0% | 18 | 1 | 133 | 75 | 74 | 37 |
| pallets/flask | 316 | 316 | 0 | 55 | 258 | 3 | 17.6% | 0 | 5 | 407 | 188 | 188 | 55 |
| pandas-dev/pandas | 6643 | 6643 | 0 | 4427 | 1893 | 323 | 70.0% | 180 | 167 | 42614 | 5270 | 5264 | 4268 |
| psf/requests | 536 | 536 | 0 | 137 | 354 | 45 | 27.9% | 54 | 4 | 595 | 123 | 122 | 91 |
| scikit-learn/scikit-learn | 3789 | 3789 | 0 | 2244 | 1217 | 328 | 64.8% | 26 | 70 | 23934 | 2492 | 2438 | 2228 |

**Non-bot merged PRs available for merge-time training (all repos in DB): `6679`**

## CI coverage and linking

| Repo | Capped list runs | Matched | Unmatched | Match rate | By number | By SHA | Missing after list | Backfill max SHAs | Backfill hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| encode/httpx | 200 | 133 | 67 | 66.5% | 17 | 116 |  |  |  |
| pallets/flask | 451 | 407 | 44 | 90.2% | 10 | 397 |  |  |  |
| pandas-dev/pandas |  |  |  | n/a |  |  |  |  |  |
| psf/requests | 718 | 595 | 123 | 82.9% | 20 | 575 |  |  |  |
| scikit-learn/scikit-learn |  |  |  | n/a |  |  |  |  |  |

All complete PRs: `11461`; with any CI run: `8148` (71.1%); with `ci_label_available=1`: `8086` (70.6%).

## CI label availability by created_at quarter

`ci_label_available = 1` means at least one completed workflow run with conclusion in `success` / `failure` / `timed_out`. Compare the quarter mix for flag 0 vs 1 to spot recency bias from the Actions 1000-run list + head_sha backfill.

### encode/httpx

| Quarter (UTC created_at) | ci_label_available=0 | ci_label_available=1 | Share with label |
| --- | ---: | ---: | ---: |
| 2024-Q4 | 23 | 1 | 4.2% |
| 2025-Q1 | 27 | 1 | 3.6% |
| 2025-Q2 | 17 | 0 | 0.0% |
| 2025-Q3 | 26 | 8 | 23.5% |
| 2025-Q4 | 5 | 37 | 88.1% |
| 2026-Q1 | 5 | 26 | 83.9% |
| 2026-Q4 | 0 | 1 | 100.0% |

### pallets/flask

| Quarter (UTC created_at) | ci_label_available=0 | ci_label_available=1 | Share with label |
| --- | ---: | ---: | ---: |
| 2024-Q4 | 15 | 0 | 0.0% |
| 2025-Q1 | 21 | 0 | 0.0% |
| 2025-Q2 | 35 | 0 | 0.0% |
| 2025-Q3 | 33 | 4 | 10.8% |
| 2025-Q4 | 3 | 34 | 91.9% |
| 2026-Q1 | 11 | 44 | 80.0% |
| 2026-Q2 | 5 | 43 | 89.6% |
| 2026-Q3 | 5 | 61 | 92.4% |
| 2026-Q4 | 0 | 2 | 100.0% |

### pandas-dev/pandas

| Quarter (UTC created_at) | ci_label_available=0 | ci_label_available=1 | Share with label |
| --- | ---: | ---: | ---: |
| 2024-Q4 | 303 | 0 | 0.0% |
| 2025-Q1 | 364 | 3 | 0.8% |
| 2025-Q2 | 293 | 15 | 4.9% |
| 2025-Q3 | 367 | 186 | 33.6% |
| 2025-Q4 | 5 | 748 | 99.3% |
| 2026-Q1 | 26 | 1183 | 97.8% |
| 2026-Q2 | 13 | 962 | 98.7% |
| 2026-Q3 | 5 | 1396 | 99.6% |
| 2026-Q4 | 3 | 771 | 99.6% |

### psf/requests

| Quarter (UTC created_at) | ci_label_available=0 | ci_label_available=1 | Share with label |
| --- | ---: | ---: | ---: |
| 2024-Q4 | 11 | 0 | 0.0% |
| 2025-Q1 | 29 | 1 | 3.3% |
| 2025-Q2 | 22 | 1 | 4.3% |
| 2025-Q3 | 32 | 2 | 5.9% |
| 2025-Q4 | 67 | 11 | 14.1% |
| 2026-Q1 | 101 | 33 | 24.6% |
| 2026-Q2 | 134 | 57 | 29.8% |
| 2026-Q3 | 18 | 15 | 45.5% |
| 2026-Q4 | 0 | 2 | 100.0% |

### scikit-learn/scikit-learn

| Quarter (UTC created_at) | ci_label_available=0 | ci_label_available=1 | Share with label |
| --- | ---: | ---: | ---: |
| 2024-Q4 | 223 | 0 | 0.0% |
| 2025-Q1 | 387 | 5 | 1.3% |
| 2025-Q2 | 389 | 26 | 6.3% |
| 2025-Q3 | 347 | 103 | 22.9% |
| 2025-Q4 | 0 | 532 | 100.0% |
| 2026-Q1 | 3 | 513 | 99.4% |
| 2026-Q2 | 0 | 579 | 100.0% |
| 2026-Q3 | 2 | 479 | 99.6% |
| 2026-Q4 | 0 | 201 | 100.0% |


## Missing fields

| Field | Missing count | Notes |
| --- | ---: | --- |
| title | 0 | |
| body | 114 | Empty description is common; keep as a feature later. |
| additions/deletions/changed_files/commits | 0/0/0/0 | Taken from GET /pulls/{{n}} at collection time (not at-open). |
| head_sha | 0 | |
| base_ref | 0 | |

## Bot authors found

| Login | API user.type | PR count |
| --- | --- | ---: |
| `dependabot[bot]` | Bot | 210 |
| `github-actions[bot]` | Bot | 27 |
| `pre-commit-ci[bot]` | Bot | 25 |
| `Copilot` | Bot | 16 |

## Reopened pull requests

Total reopened PRs: **247**

| Repo | Number | created_at | closed_at | merged_at | state |
| --- | ---: | --- | --- | --- | --- |
| encode/httpx | 3742 | 2026-01-07T14:46:12Z |  |  | open |
| pallets/flask | 5736 | 2025-05-17T04:29:30Z | 2025-08-19T19:36:01Z | 2025-08-19T19:36:01Z | merged |
| pallets/flask | 5761 | 2025-06-17T17:15:00Z | 2025-06-17T18:27:56Z |  | closed |
| pallets/flask | 5827 | 2025-10-09T07:06:09Z | 2026-01-25T02:46:57Z | 2026-01-25T02:46:57Z | merged |
| pallets/flask | 5918 | 2026-02-12T22:03:40Z |  |  | open |
| pallets/flask | 6067 | 2026-06-29T10:29:48Z | 2026-06-29T14:13:37Z |  | closed |
| pandas-dev/pandas | 60209 | 2024-11-06T01:54:02Z | 2024-11-07T07:57:33Z | 2024-11-07T07:57:33Z | merged |
| pandas-dev/pandas | 60239 | 2024-11-08T08:42:33Z | 2024-11-18T14:06:48Z |  | closed |
| pandas-dev/pandas | 60247 | 2024-11-08T16:15:40Z | 2024-11-12T21:46:05Z | 2024-11-12T21:46:05Z | merged |
| pandas-dev/pandas | 60436 | 2024-11-28T13:17:46Z | 2025-01-26T11:29:25Z | 2025-01-26T11:29:25Z | merged |
| pandas-dev/pandas | 60458 | 2024-12-01T06:18:16Z | 2025-03-17T02:44:59Z |  | closed |
| pandas-dev/pandas | 60624 | 2024-12-30T12:17:02Z | 2025-01-31T17:38:03Z |  | closed |
| pandas-dev/pandas | 60788 | 2025-01-25T17:34:18Z | 2025-01-25T18:03:42Z |  | closed |
| pandas-dev/pandas | 60883 | 2025-02-08T06:18:33Z | 2025-03-10T17:36:09Z |  | closed |
| pandas-dev/pandas | 60941 | 2025-02-16T14:48:22Z | 2025-09-22T02:33:32Z | 2025-09-22T02:33:31Z | merged |
| pandas-dev/pandas | 60948 | 2025-02-17T09:37:37Z | 2025-06-12T14:04:12Z | 2025-06-12T14:04:12Z | merged |
| pandas-dev/pandas | 60990 | 2025-02-23T10:21:48Z | 2025-03-05T00:39:08Z | 2025-03-05T00:39:08Z | merged |
| pandas-dev/pandas | 61023 | 2025-03-01T05:33:18Z | 2025-03-01T06:58:23Z |  | closed |
| pandas-dev/pandas | 61024 | 2025-03-01T07:07:01Z | 2025-03-09T01:26:41Z |  | closed |
| pandas-dev/pandas | 61073 | 2025-03-07T00:25:43Z | 2025-09-21T14:02:23Z | 2025-09-21T14:02:23Z | merged |

## CI conclusions observed

| conclusion (raw) | count |
| --- | ---: |
| `success` | 42326 |
| `skipped` | 19849 |
| `failure` | 3244 |
| `cancelled` | 2241 |
| `action_required` | 16 |
| `<null>` | 5 |
| `startup_failure` | 2 |

Target rule: only `failure` and `timed_out` count as failed. `cancelled` / `skipped` / other are not usable labels (`ci_label_available` stays 0 unless a success/failure/timed_out run exists).

## File statuses observed

| status | count |
| --- | ---: |
| `added` | 55730 |
| `modified` | 48282 |
| `removed` | 5921 |
| `renamed` | 438 |

## Not yet downloaded

Still waiting for approval: `numpy/numpy`, `scipy/scipy`, `fastapi/fastapi`, `pydantic/pydantic` (and optional `huggingface/transformers`).

