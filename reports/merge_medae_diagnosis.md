# Stage A merge MedAE diagnosis

L2 on log1p optimizes mean squared error in log space, which after expm1 targets something closer to a conditional mean on a skewed scale; the per-repo median baseline directly optimizes absolute error at the median. Heavy right tail (long-lived PRs) inflates squared/log losses toward larger predictions.

- test n=`975`; y median=`23.07`h; P90=`291.66`h; P99=`794.09`h; share >7d=`0.182`
- L2 pred median=`45.70`h vs baseline pred median=`22.86`h
- MedAE on y≤7d only: L2=`31.08138499942882` vs baseline=`19.768888888888885`

| Variant | MAE (h) | MedAE (h) | MAE(log1p h) | Beats baseline MedAE? |
| --- | ---: | ---: | ---: | --- |
| `baseline_per_repo_median` | 85.79 | 21.19 | 1.396 | no |
| `l2_on_log1p_expm1` | 84.62 | 36.22 | 1.348 | no |
| `quantile_p50_on_log1p_expm1` | 84.09 | 24.50 | 1.259 | no |
| `quantile_p50_on_raw_hours` | 82.84 | 30.27 | 1.291 | no |
| `bucket_clf_then_bucket_median_hours` | 97.66 | 19.06 | 1.732 | yes |

### Bucket task (<1d / 1–7d / >7d)
- model accuracy=`0.542` macro-F1=`0.346`
- baseline (bucket of per-repo median hours) accuracy=`0.506` macro-F1=`0.284`

**Recommendation:** bucket_clf_then_bucket_median_hours
