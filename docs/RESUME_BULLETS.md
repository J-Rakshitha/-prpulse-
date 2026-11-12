# Resume bullets (numbers only from generated reports)

1. Built an end-to-end PR merge-risk system where a calibrated `merged_within_7d` classifier reaches Stage A PR-AUC **0.768** vs per-repo baseline **0.578**, rising to **0.863 / 0.877** with Stage B/C early signals (`reports/results.md` lines 11–12, 46–48).

2. Improved continuous merge-hour MAE with early signals: Stage B/C model MAE **77.15h / 74.65h** vs baseline **85.79h** on the same time-ordered test split (`reports/results.md` lines 12, 22–24).

3. Diagnosed and reported a Stage A MedAE failure honestly: continuous model MedAE **36.22h** vs per-repo median baseline **21.19h**, with ablation/diagnosis showing L2-on-log bias on a heavy-tailed target (`reports/results.md` lines 14, 22; `reports/merge_medae_diagnosis.md`).

4. Removed leaky collection-time `pr_files` size/path features after ablation: Stage A PR-AUC dropped from **0.875** to **0.768**, and the safer feature set was kept (`reports/stage_a_leakage_ablation.md` lines 10–11; `reports/results.md` lines 50–52).

5. Load-tested the local Docker webhook path at 50 concurrent users; single-worker `/webhook` ack p50/p95/p99 **870 / 1300 / 1400 ms**, two-worker **760 / 1100 / 1300 ms**, both 0% HTTP failures (`reports/loadtest.md` comparison table).
