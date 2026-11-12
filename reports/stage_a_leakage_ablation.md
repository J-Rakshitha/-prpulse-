# Stage A leakage ablation

Same time split as training. Collection-time columns = draft + `pr_files` approx size/paths/module history.
`commits_at_open` is kept in variant 2 (commits with `committed_at <= created_at`).

Merge baseline MAE=`85.79` MedAE=`21.19`. Classifier baseline PR-AUC=`0.578`.

| Variant | n_feat | Merge MAE | Merge MedAE | merged_7d PR-AUC |
| --- | ---: | ---: | ---: | ---: |
| `1_all` | 32 | 80.77 | 30.66 | 0.875 |
| `2_no_collection_time` | 16 | 84.62 | 36.22 | 0.768 |
| `3_no_author` | 26 | 84.62 | 35.19 | 0.721 |
| `4_no_repo_state` | 28 | 81.84 | 31.88 | 0.880 |
| `5_author_repo_only` | 10 | 84.50 | 30.65 | 0.778 |
