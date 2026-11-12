# 3-minute demo script

1. **Problem (20s):** Show a busy open PR. Ask: how long to merge, will CI fail, how risky to ship?
2. **Data (30s):** Open `reports/data_quality.md` — pandas + sklearn, 2y lookback, CI label coverage by quarter.
3. **Features (30s):** Open `reports/features.md` — Stage A/B/C, blocked live size fields, approx file stats.
4. **Results (40s):** Open `reports/results.md` — model vs per-repo median baseline MAE; say honestly if baseline wins.
5. **API (30s):** `curl` `/health` and `/predict` with a sample payload; show range + reasons.
6. **Bot (20s):** Show comment format from docs; mention webhook HMAC + opt-out.
7. **Limits (20s):** Two repos, CI retention bias, suggestions are estimates, live labels arrive late.
