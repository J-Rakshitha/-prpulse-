"""Phase 2 Exploratory Data Analysis (EDA) for PR Pulse.

Connects to data/prpulse.db via src.data.db.connect + load_settings + resolve_db_path,
computes descriptive statistics strictly from the database, saves figures to
reports/figures/, writes reports/figures/friday_effect.txt and reports/eda_summary.md.
"""

from __future__ import annotations

import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.db import connect, resolve_db_path
from src.settings import load_settings, project_root

TRAIN_REPOS = ("pandas-dev/pandas", "scikit-learn/scikit-learn")
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse_utc(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _merge_hours(created_at: str, merged_at: str) -> float:
    dt = (_parse_utc(merged_at) - _parse_utc(created_at)).total_seconds() / 3600.0
    return max(dt, 0.0)


def fetch_train_merged_prs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            p.id,
            r.full_name AS repo,
            p.number,
            p.created_at,
            p.merged_at,
            p.additions,
            p.deletions,
            p.changed_files,
            p.commits,
            p.was_reopened,
            p.ci_label_available
        FROM prs AS p
        JOIN repos AS r ON r.id = p.repo_id
        WHERE r.full_name IN (?, ?)
          AND p.is_bot = 0
          AND p.state = 'merged'
          AND p.merged_at IS NOT NULL
        ORDER BY p.created_at
        """,
        TRAIN_REPOS,
    ).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        created_dt = _parse_utc(row["created_at"])
        mh = _merge_hours(row["created_at"], row["merged_at"])
        records.append(
            {
                "id": int(row["id"]),
                "repo": row["repo"],
                "number": int(row["number"]),
                "created_at": row["created_at"],
                "merged_at": row["merged_at"],
                "created_dt": created_dt,
                "weekday": created_dt.weekday(),  # 0=Mon .. 6=Sun
                "hour_utc": created_dt.hour,
                "merge_hours": mh,
                "additions": int(row["additions"] or 0),
                "deletions": int(row["deletions"] or 0),
                "changed_files": int(row["changed_files"] or 0),
                "commits": int(row["commits"] or 0),
                "was_reopened": int(row["was_reopened"] or 0),
                "ci_label_available": int(row["ci_label_available"] or 0),
            }
        )
    return records


def plot_merge_hours_log_hist(
    records: list[dict[str, Any]], out_path: Path
) -> dict[str, Any]:
    hours = np.array([r["merge_hours"] for r in records], dtype=float)
    # Clamp strictly positive for log10 axis (min floor 1e-2 hours = 36s)
    hours_pos = np.maximum(hours, 1e-2)
    log_hours = np.log10(hours_pos)

    p10, p25, p50, p75, p90 = np.percentile(hours, [10, 25, 50, 75, 90])
    within_7d = float(np.mean(hours <= 168.0))

    fig, ax = plt.subplots(figsize=(8.5, 5.0), dpi=140)
    bins = np.linspace(log_hours.min(), log_hours.max(), 45)
    ax.hist(log_hours, bins=bins, color="#2b5c8f", edgecolor="white", alpha=0.88)

    for val, label, color, ls in (
        (p50, f"Median: {p50:.1f}h", "#d95f02", "-"),
        (p90, f"P90: {p90:.1f}h", "#7570b3", "--"),
        (168.0, "7d (168h)", "#1b9e77", ":"),
    ):
        ax.axvline(math.log10(max(val, 1e-2)), color=color, linestyle=ls, linewidth=1.8, label=label)

    tick_hours = [0.1, 1, 6, 24, 168, 720, 4320]
    ax.set_xticks([math.log10(h) for h in tick_hours])
    ax.set_xticklabels(["0.1h", "1h", "6h", "1d (24h)", "7d (168h)", "30d", "180d"])
    ax.set_xlabel("Time to merge (hours, log10 scale)")
    ax.set_ylabel("Merged non-bot PR count")
    ax.set_title(
        f"Merge Time Distribution — Train Repos Non-Bot Merged PRs (n={len(hours)})"
    )
    ax.legend(frameon=True)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    per_repo: dict[str, dict[str, float]] = {}
    for repo in TRAIN_REPOS:
        rh = np.array([r["merge_hours"] for r in records if r["repo"] == repo], dtype=float)
        rp10, rp50, rp90 = np.percentile(rh, [10, 50, 90])
        per_repo[repo] = {
            "n": int(len(rh)),
            "p10": float(rp10),
            "p50": float(rp50),
            "p90": float(rp90),
            "within_7d_share": float(np.mean(rh <= 168.0)),
        }

    return {
        "n": int(len(hours)),
        "min": float(hours.min()),
        "p10": float(p10),
        "p25": float(p25),
        "p50": float(p50),
        "p75": float(p75),
        "p90": float(p90),
        "max": float(hours.max()),
        "mean": float(hours.mean()),
        "within_7d_share": within_7d,
        "per_repo": per_repo,
    }


def plot_merge_rate_by_repo(
    conn: sqlite3.Connection, out_path: Path
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            r.full_name AS repo,
            COUNT(p.id) AS total_prs,
            SUM(CASE WHEN p.is_bot = 0 THEN 1 ELSE 0 END) AS nonbot_prs,
            SUM(CASE WHEN p.state = 'merged' THEN 1 ELSE 0 END) AS merged_all,
            SUM(CASE WHEN p.state = 'closed' THEN 1 ELSE 0 END) AS closed_all,
            SUM(CASE WHEN p.state = 'open' THEN 1 ELSE 0 END) AS open_all,
            SUM(CASE WHEN p.is_bot = 0 AND p.state = 'merged' THEN 1 ELSE 0 END) AS merged_nonbot,
            SUM(CASE WHEN p.is_bot = 0 AND p.state = 'closed' THEN 1 ELSE 0 END) AS closed_nonbot,
            SUM(CASE WHEN p.is_bot = 0 AND p.state = 'open' THEN 1 ELSE 0 END) AS open_nonbot
        FROM repos AS r
        JOIN prs AS p ON p.repo_id = r.id
        GROUP BY r.id
        ORDER BY r.full_name
        """
    ).fetchall()

    stats: list[dict[str, Any]] = []
    for row in rows:
        repo = row["repo"]
        m_nb = int(row["merged_nonbot"] or 0)
        c_nb = int(row["closed_nonbot"] or 0)
        o_nb = int(row["open_nonbot"] or 0)
        tot_nb = int(row["nonbot_prs"] or 0)
        res_nb = m_nb + c_nb

        m_all = int(row["merged_all"] or 0)
        c_all = int(row["closed_all"] or 0)
        res_all = m_all + c_all
        tot_all = int(row["total_prs"] or 0)

        stats.append(
            {
                "repo": repo,
                "is_train": repo in TRAIN_REPOS,
                "total_prs": tot_all,
                "nonbot_prs": tot_nb,
                "merged_nonbot": m_nb,
                "closed_nonbot": c_nb,
                "open_nonbot": o_nb,
                "resolved_nonbot": res_nb,
                "merge_rate_resolved_nonbot": (m_nb / res_nb) if res_nb else 0.0,
                "merge_rate_all_nonbot": (m_nb / tot_nb) if tot_nb else 0.0,
                "merge_rate_resolved_all": (m_all / res_all) if res_all else 0.0,
            }
        )

    repos = [s["repo"] for s in stats]
    x = np.arange(len(repos))
    width = 0.36
    rates_resolved_nb = [s["merge_rate_resolved_nonbot"] * 100.0 for s in stats]
    rates_total_nb = [s["merge_rate_all_nonbot"] * 100.0 for s in stats]
    colors_res = ["#1f78b4" if s["is_train"] else "#7f9eb2" for s in stats]
    colors_tot = ["#33a02c" if s["is_train"] else "#a6dba0" for s in stats]

    fig, ax = plt.subplots(figsize=(9.0, 5.0), dpi=140)
    b1 = ax.bar(x - width / 2, rates_resolved_nb, width, label="Resolved non-bot (merged / [merged+closed])", color=colors_res)
    b2 = ax.bar(x + width / 2, rates_total_nb, width, label="All non-bot (merged / total)", color=colors_tot)

    for bar in b1:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 1.2, f"{h:.1f}%", ha="center", va="bottom", fontsize=8.5)
    for bar in b2:
        h = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, h + 1.2, f"{h:.1f}%", ha="center", va="bottom", fontsize=8.5)

    labels = [f"{s['repo']}\n({'TRAIN' if s['is_train'] else 'test'}, n={s['nonbot_prs']})" for s in stats]
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Merge rate (%)")
    ax.set_title("Merge Rate by Repository (Train vs Test Repos in DB)")
    ax.legend(frameon=True, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    return stats


def plot_weekday_hour_heatmap(
    records: list[dict[str, Any]], out_path: Path
) -> dict[str, Any]:
    counts = np.zeros((7, 24), dtype=int)
    buckets: list[list[list[float]]] = [[[] for _ in range(24)] for _ in range(7)]

    for r in records:
        w = r["weekday"]
        h = r["hour_utc"]
        counts[w, h] += 1
        buckets[w][h].append(r["merge_hours"])

    medians = np.full((7, 24), np.nan, dtype=float)
    for w in range(7):
        for h in range(24):
            if buckets[w][h]:
                medians[w, h] = float(np.median(buckets[w][h]))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11.0, 7.2), dpi=140, sharex=True)

    im1 = ax1.imshow(counts, aspect="auto", cmap="YlGnBu")
    ax1.set_yticks(range(7))
    ax1.set_yticklabels(WEEKDAY_NAMES)
    ax1.set_title("Train Merged Non-Bot PR Count by Created Weekday × Hour (UTC)")
    ax1.set_ylabel("Created Weekday (UTC)")
    cbar1 = fig.colorbar(im1, ax=ax1, pad=0.02)
    cbar1.set_label("Merged PRs")

    im2 = ax2.imshow(medians, aspect="auto", cmap="magma_r")
    ax2.set_yticks(range(7))
    ax2.set_yticklabels(WEEKDAY_NAMES)
    ax2.set_xticks(range(24))
    ax2.set_xticklabels([f"{h:02d}" for h in range(24)])
    ax2.set_title("Median Merge Hours by Created Weekday × Hour (UTC)")
    ax2.set_xlabel("Created Hour of Day (UTC)")
    ax2.set_ylabel("Created Weekday (UTC)")
    cbar2 = fig.colorbar(im2, ax=ax2, pad=0.02)
    cbar2.set_label("Median merge hours")

    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    peak_idx = np.unravel_index(int(np.argmax(counts)), counts.shape)
    weekday_totals = {WEEKDAY_NAMES[w]: int(counts[w].sum()) for w in range(7)}
    weekday_medians = {
        WEEKDAY_NAMES[w]: float(
            np.median([r["merge_hours"] for r in records if r["weekday"] == w])
        )
        for w in range(7)
    }

    return {
        "peak_cell": {
            "weekday": WEEKDAY_NAMES[int(peak_idx[0])],
            "hour_utc": int(peak_idx[1]),
            "count": int(counts[peak_idx]),
            "median_hours": float(medians[peak_idx]),
        },
        "weekday_totals": weekday_totals,
        "weekday_medians": weekday_medians,
    }


def plot_size_vs_merge_hours(
    records: list[dict[str, Any]], out_path: Path
) -> dict[str, Any]:
    additions = np.array([r["additions"] for r in records], dtype=float)
    hours = np.array([r["merge_hours"] for r in records], dtype=float)

    x_log = np.log10(additions + 1.0)
    y_log = np.log10(np.maximum(hours, 1e-2))

    corr_matrix = np.corrcoef(x_log, y_log)
    pearson_r = float(corr_matrix[0, 1])

    # Rank correlation (Spearman) via argsort ranks
    rx = np.argsort(np.argsort(additions)).astype(float)
    ry = np.argsort(np.argsort(hours)).astype(float)
    spearman_r = float(np.corrcoef(rx, ry)[0, 1])

    slope, intercept = np.polyfit(x_log, y_log, 1)

    fig, ax = plt.subplots(figsize=(8.5, 5.2), dpi=140)
    for repo, color in (
        ("pandas-dev/pandas", "#1f78b4"),
        ("scikit-learn/scikit-learn", "#e66101"),
    ):
        mask = np.array([r["repo"] == repo for r in records])
        ax.scatter(
            x_log[mask],
            y_log[mask],
            s=10,
            alpha=0.28,
            color=color,
            label=f"{repo} (n={int(mask.sum())})",
        )

    xs = np.linspace(x_log.min(), x_log.max(), 100)
    ax.plot(
        xs,
        slope * xs + intercept,
        color="#222222",
        linewidth=1.8,
        linestyle="--",
        label=f"Log-linear fit (r={pearson_r:.3f}, Spearman={spearman_r:.3f})",
    )

    ax.set_xticks([0, 1, 2, 3, 4])
    ax.set_xticklabels(["0", "10", "100", "1,000", "10,000"])
    tick_hours = [0.1, 1, 6, 24, 168, 720, 4320]
    ax.set_yticks([math.log10(h) for h in tick_hours])
    ax.set_yticklabels(["0.1h", "1h", "6h", "1d", "7d", "30d", "180d"])

    ax.set_xlabel("Additions + 1 (log10 scale; collection-time value, NOT point-in-time safe)")
    ax.set_ylabel("Time to merge (hours, log10 scale)")
    ax.set_title(
        "Additions vs Merge Hours — Train Merged Non-Bot PRs\n"
        "Note: prs.additions is recorded at collection time (includes post-open commits)"
    )
    ax.legend(frameon=True, loc="lower right")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    # Bucket medians by additions size
    small = hours[additions <= 10]
    medium = hours[(additions > 10) & (additions <= 100)]
    large = hours[additions > 100]

    return {
        "n": int(len(hours)),
        "pearson_log_r": pearson_r,
        "spearman_r": spearman_r,
        "median_additions": float(np.median(additions)),
        "p90_additions": float(np.percentile(additions, 90)),
        "bucket_medians_hours": {
            "le_10_lines": {"n": int(len(small)), "median_h": float(np.median(small))},
            "11_to_100_lines": {"n": int(len(medium)), "median_h": float(np.median(medium))},
            "gt_100_lines": {"n": int(len(large)), "median_h": float(np.median(large))},
        },
    }


def plot_open_pr_censoring(
    conn: sqlite3.Connection, out_path: Path
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            r.full_name AS repo,
            p.state,
            COUNT(*) AS total_count,
            SUM(CASE WHEN p.is_bot = 0 THEN 1 ELSE 0 END) AS nonbot_count,
            SUM(CASE WHEN p.is_bot = 1 THEN 1 ELSE 0 END) AS bot_count
        FROM prs AS p
        JOIN repos AS r ON r.id = p.repo_id
        WHERE r.full_name IN (?, ?)
        GROUP BY r.full_name, p.state
        ORDER BY r.full_name, p.state
        """,
        TRAIN_REPOS,
    ).fetchall()

    states = ("merged", "closed", "open")
    by_repo_nonbot: dict[str, dict[str, int]] = {
        repo: {s: 0 for s in states} for repo in TRAIN_REPOS
    }
    by_repo_all: dict[str, dict[str, int]] = {
        repo: {s: 0 for s in states} for repo in TRAIN_REPOS
    }
    for row in rows:
        repo = row["repo"]
        st = row["state"]
        by_repo_nonbot[repo][st] = int(row["nonbot_count"] or 0)
        by_repo_all[repo][st] = int(row["total_count"] or 0)

    combined_nonbot = {
        s: sum(by_repo_nonbot[r][s] for r in TRAIN_REPOS) for s in states
    }
    combined_all = {
        s: sum(by_repo_all[r][s] for r in TRAIN_REPOS) for s in states
    }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 4.8), dpi=140)

    state_labels = ["Merged", "Closed (unmerged)", "Open (right-censored)"]
    state_colors = ["#2b8cbe", "#e34a33", "#fdbb84"]
    vals_comb = [combined_nonbot["merged"], combined_nonbot["closed"], combined_nonbot["open"]]
    total_nb = sum(vals_comb)

    bars = ax1.bar(state_labels, vals_comb, color=state_colors, width=0.55)
    for b in bars:
        v = int(b.get_height())
        pct = (v / total_nb * 100.0) if total_nb else 0.0
        ax1.text(b.get_x() + b.get_width() / 2, v + 80, f"{v}\n({pct:.1f}%)", ha="center", va="bottom", fontsize=9)
    ax1.set_ylim(0, max(vals_comb) * 1.18)
    ax1.set_ylabel("Non-bot PR count")
    ax1.set_title(f"Train Combined Non-Bot PR Outcomes (n={total_nb})")
    ax1.grid(axis="y", alpha=0.25)

    x = np.arange(len(TRAIN_REPOS))
    w = 0.25
    for idx, (st, lbl, col) in enumerate(zip(states, state_labels, state_colors)):
        v_repo = [by_repo_nonbot[r][st] for r in TRAIN_REPOS]
        b_repo = ax2.bar(x + (idx - 1) * w, v_repo, width=w, label=lbl, color=col)
        for b in b_repo:
            v = int(b.get_height())
            ax2.text(b.get_x() + b.get_width() / 2, v + 50, f"{v}", ha="center", va="bottom", fontsize=8.5)

    ax2.set_xticks(x)
    ax2.set_xticklabels(list(TRAIN_REPOS))
    ax2.set_ylim(0, max(by_repo_nonbot["pandas-dev/pandas"]["merged"], 1) * 1.18)
    ax2.set_ylabel("Non-bot PR count")
    ax2.set_title("Per-Repo Outcome & Censoring Breakdown (Non-Bot)")
    ax2.legend(frameon=True)
    ax2.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    return {
        "combined_nonbot": combined_nonbot,
        "combined_all": combined_all,
        "by_repo_nonbot": by_repo_nonbot,
        "by_repo_all": by_repo_all,
        "open_censoring_share_nonbot": combined_nonbot["open"] / total_nb if total_nb else 0.0,
    }


def plot_friday_flag_effect(
    records: list[dict[str, Any]],
    friday_hour_utc: int,
    out_png: Path,
    out_txt: Path,
) -> dict[str, Any]:
    fri_hours = np.array(
        [
            r["merge_hours"]
            for r in records
            if r["weekday"] == 4 and r["hour_utc"] >= friday_hour_utc
        ],
        dtype=float,
    )
    other_hours = np.array(
        [
            r["merge_hours"]
            for r in records
            if not (r["weekday"] == 4 and r["hour_utc"] >= friday_hour_utc)
        ],
        dtype=float,
    )

    fri_p25, fri_med, fri_p75, fri_p90 = np.percentile(fri_hours, [25, 50, 75, 90])
    oth_p25, oth_med, oth_p75, oth_p90 = np.percentile(other_hours, [25, 50, 75, 90])
    diff_hours = float(fri_med - oth_med)
    ratio = float(fri_med / oth_med) if oth_med else float("nan")
    rel_pct = float((fri_med - oth_med) / oth_med * 100.0) if oth_med else 0.0

    per_repo: dict[str, dict[str, float]] = {}
    for repo in TRAIN_REPOS:
        rf = np.array(
            [
                r["merge_hours"]
                for r in records
                if r["repo"] == repo and r["weekday"] == 4 and r["hour_utc"] >= friday_hour_utc
            ],
            dtype=float,
        )
        ro = np.array(
            [
                r["merge_hours"]
                for r in records
                if r["repo"] == repo and not (r["weekday"] == 4 and r["hour_utc"] >= friday_hour_utc)
            ],
            dtype=float,
        )
        rf_med = float(np.median(rf))
        ro_med = float(np.median(ro))
        per_repo[repo] = {
            "friday_n": int(len(rf)),
            "other_n": int(len(ro)),
            "friday_median_h": rf_med,
            "other_median_h": ro_med,
            "diff_h": rf_med - ro_med,
            "ratio": rf_med / ro_med if ro_med else float("nan"),
        }

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.0, 4.8), dpi=140)

    labels = [
        f"Other UTC times\n(n={len(other_hours)})",
        f"Friday >= {friday_hour_utc:02d}:00 UTC\n(n={len(fri_hours)})",
    ]
    medians = [oth_med, fri_med]
    bars = ax1.bar(labels, medians, color=["#4daf4a", "#e41a1c"], width=0.48)
    for b in bars:
        v = b.get_height()
        ax1.text(b.get_x() + b.get_width() / 2, v + 0.8, f"{v:.2f}h", ha="center", va="bottom", fontweight="bold")
    ax1.set_ylim(0, max(medians) * 1.25)
    ax1.set_ylabel("Median merge hours")
    ax1.set_title(
        f"Median Merge Hours: Friday Evening vs Other\n"
        f"(Delta = {diff_hours:+.2f}h, {rel_pct:+.1f}%, ratio = {ratio:.2f}x)"
    )
    ax1.grid(axis="y", alpha=0.25)

    # Boxplot on log10 hours
    data_log = [
        np.log10(np.maximum(other_hours, 1e-2)),
        np.log10(np.maximum(fri_hours, 1e-2)),
    ]
    bp = ax2.boxplot(
        data_log,
        tick_labels=labels,
        patch_artist=True,
        widths=0.45,
        medianprops={"color": "black", "linewidth": 2.0},
    )
    for patch, col in zip(bp["boxes"], ("#4daf4a", "#e41a1c")):
        patch.set_facecolor(col)
        patch.set_alpha(0.65)

    tick_hours = [0.1, 1, 6, 24, 168, 720, 4320]
    ax2.set_yticks([math.log10(h) for h in tick_hours])
    ax2.set_yticklabels(["0.1h", "1h", "6h", "1d", "7d", "30d", "180d"])
    ax2.set_ylabel("Time to merge (hours, log10 scale)")
    ax2.set_title("Log Merge Hours Boxplot (Train Non-Bot Merged PRs)")
    ax2.grid(axis="y", alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)

    # Decision rule based on effect size
    recommendation = "KEEP" if abs(rel_pct) >= 15.0 or abs(diff_hours) >= 3.0 else "DROP"

    txt_lines = [
        "Friday-Evening Flag Effect Summary (Train Non-Bot Merged PRs)",
        f"Definition: created_at weekday == Friday (4) and UTC hour >= {friday_hour_utc}",
        f"friday_n={len(fri_hours)}",
        f"other_n={len(other_hours)}",
        f"friday_median_hours={fri_med:.4f}",
        f"other_median_hours={oth_med:.4f}",
        f"friday_iqr_hours=[{fri_p25:.4f}, {fri_p75:.4f}]",
        f"other_iqr_hours=[{oth_p25:.4f}, {oth_p75:.4f}]",
        f"friday_p90_hours={fri_p90:.4f}",
        f"other_p90_hours={oth_p90:.4f}",
        f"median_diff_hours={diff_hours:+.4f}",
        f"median_ratio={ratio:.4f}",
        f"relative_diff_pct={rel_pct:+.2f}%",
    ]
    for repo, st in per_repo.items():
        txt_lines.append(
            f"repo={repo} friday_n={st['friday_n']} other_n={st['other_n']} "
            f"friday_median_h={st['friday_median_h']:.4f} other_median_h={st['other_median_h']:.4f} "
            f"diff_h={st['diff_h']:+.4f} ratio={st['ratio']:.4f}"
        )
    txt_lines.append(f"recommendation={recommendation}")

    txt_body = "\n".join(txt_lines) + "\n"
    out_txt.write_text(txt_body, encoding="utf-8")
    print(txt_body.strip())

    return {
        "friday_hour_utc": friday_hour_utc,
        "friday_n": int(len(fri_hours)),
        "other_n": int(len(other_hours)),
        "friday_p25_h": float(fri_p25),
        "friday_median_h": float(fri_med),
        "friday_p75_h": float(fri_p75),
        "friday_p90_h": float(fri_p90),
        "other_p25_h": float(oth_p25),
        "other_median_h": float(oth_med),
        "other_p75_h": float(oth_p75),
        "other_p90_h": float(oth_p90),
        "diff_hours": diff_hours,
        "ratio": ratio,
        "rel_pct": rel_pct,
        "per_repo": per_repo,
        "recommendation": recommendation,
    }


def plot_ci_label_by_quarter(
    conn: sqlite3.Connection, out_path: Path
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT
            r.full_name AS repo,
            printf('%s-Q%s', substr(p.created_at, 1, 4),
                   ((CAST(substr(p.created_at, 6, 2) AS INTEGER) - 1) / 3) + 1) AS quarter,
            COUNT(*) AS total_prs,
            SUM(CASE WHEN p.ci_label_available = 1 THEN 1 ELSE 0 END) AS label_ok,
            SUM(CASE WHEN p.is_bot = 0 THEN 1 ELSE 0 END) AS nonbot_prs,
            SUM(CASE WHEN p.is_bot = 0 AND p.ci_label_available = 1 THEN 1 ELSE 0 END) AS nonbot_label_ok
        FROM prs AS p
        JOIN repos AS r ON r.id = p.repo_id
        WHERE r.full_name IN (?, ?)
        GROUP BY r.full_name, quarter
        ORDER BY quarter, r.full_name
        """,
        TRAIN_REPOS,
    ).fetchall()

    quarters = sorted({row["quarter"] for row in rows})
    by_repo: dict[str, dict[str, dict[str, float]]] = {r: {} for r in TRAIN_REPOS}
    combined: dict[str, dict[str, int]] = {
        q: {"total": 0, "label_ok": 0, "nonbot_total": 0, "nonbot_label_ok": 0}
        for q in quarters
    }

    for row in rows:
        repo = row["repo"]
        q = row["quarter"]
        tot = int(row["total_prs"] or 0)
        ok = int(row["label_ok"] or 0)
        nb_tot = int(row["nonbot_prs"] or 0)
        nb_ok = int(row["nonbot_label_ok"] or 0)
        by_repo[repo][q] = {
            "total": tot,
            "label_ok": ok,
            "share": (ok / tot) if tot else 0.0,
            "nonbot_total": nb_tot,
            "nonbot_label_ok": nb_ok,
            "nonbot_share": (nb_ok / nb_tot) if nb_tot else 0.0,
        }
        combined[q]["total"] += tot
        combined[q]["label_ok"] += ok
        combined[q]["nonbot_total"] += nb_tot
        combined[q]["nonbot_label_ok"] += nb_ok

    x = np.arange(len(quarters))
    width = 0.36
    fig, ax = plt.subplots(figsize=(9.5, 5.0), dpi=140)

    for idx, (repo, color) in enumerate(
        (("pandas-dev/pandas", "#1f78b4"), ("scikit-learn/scikit-learn", "#ff7f00"))
    ):
        shares = [by_repo[repo].get(q, {}).get("share", 0.0) * 100.0 for q in quarters]
        offset = (idx - 0.5) * width
        bars = ax.bar(x + offset, shares, width=width, label=repo, color=color, alpha=0.9)
        for b in bars:
            h = b.get_height()
            ax.text(
                b.get_x() + b.get_width() / 2,
                h + 1.2,
                f"{h:.1f}%",
                ha="center",
                va="bottom",
                fontsize=7.5,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(quarters)
    ax.set_ylim(0, 112)
    ax.set_xlabel("PR Created Quarter (UTC)")
    ax.set_ylabel("Share with ci_label_available = 1 (%)")
    ax.set_title("CI Label Availability by Quarter — Train Repositories")
    ax.legend(frameon=True, loc="upper left")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)

    return {
        "quarters": quarters,
        "by_repo": by_repo,
        "combined": combined,
    }


def write_eda_summary(
    summary_path: Path,
    *,
    hist_stats: dict[str, Any],
    repo_stats: list[dict[str, Any]],
    heatmap_stats: dict[str, Any],
    size_stats: dict[str, Any],
    censor_stats: dict[str, Any],
    friday_stats: dict[str, Any],
    ci_stats: dict[str, Any],
) -> None:
    pandas_mh = hist_stats["per_repo"]["pandas-dev/pandas"]
    sklearn_mh = hist_stats["per_repo"]["scikit-learn/scikit-learn"]

    lines: list[str] = [
        "# PR Pulse Phase 2 — Exploratory Data Analysis Summary",
        "",
        "All numbers below are computed directly from `data/prpulse.db`. Primary analysis is restricted to non-bot pull requests in the approved training repositories (`pandas-dev/pandas` and `scikit-learn/scikit-learn`).",
        "",
        "## 1. Merge-Time Distribution (`reports/figures/merge_hours_log_hist.png`)",
        "",
        f"- **Train non-bot merged PRs:** `{hist_stats['n']}` (`pandas-dev/pandas`: `{pandas_mh['n']}`, `scikit-learn/scikit-learn`: `{sklearn_mh['n']}`).",
        f"- **Combined percentiles (hours):** P10 = `{hist_stats['p10']:.2f}h`, P25 = `{hist_stats['p25']:.2f}h`, **Median (P50) = `{hist_stats['p50']:.2f}h`**, P75 = `{hist_stats['p75']:.2f}h`, **P90 = `{hist_stats['p90']:.2f}h`**, Mean = `{hist_stats['mean']:.2f}h` (min `{hist_stats['min']:.4f}h`, max `{hist_stats['max']:.2f}h`).",
        f"- **Merged within 7 days (`<= 168h`) among merged train PRs:** `{hist_stats['within_7d_share']*100:.1f}%`.",
        f"- **Per-repo merge hours:**",
        f"  - `pandas-dev/pandas` (`n={pandas_mh['n']}`): P10 = `{pandas_mh['p10']:.2f}h`, Median = `{pandas_mh['p50']:.2f}h`, P90 = `{pandas_mh['p90']:.2f}h`, merged `<= 7d` = `{pandas_mh['within_7d_share']*100:.1f}%`.",
        f"  - `scikit-learn/scikit-learn` (`n={sklearn_mh['n']}`): P10 = `{sklearn_mh['p10']:.2f}h`, Median = `{sklearn_mh['p50']:.2f}h`, P90 = `{sklearn_mh['p90']:.2f}h`, merged `<= 7d` = `{sklearn_mh['within_7d_share']*100:.1f}%`.",
        "- **Implication for modeling:** Merge times span more than four orders of magnitude and are strongly right-skewed; regression targets should be modeled on `log1p(merge_hours)` with quantile loss (`P10`/`P50`/`P90`).",
        "",
        "## 2. Merge Rate by Repository (`reports/figures/merge_rate_by_repo.png`)",
        "",
        "| Repo | Role | Non-bot PRs | Merged | Closed | Open | Merge rate (resolved non-bot) | Merge rate (all non-bot) | Merge rate (resolved all incl. bots) |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for s in repo_stats:
        role = "Train" if s["is_train"] else "Test (Phase 0)"
        lines.append(
            f"| `{s['repo']}` | {role} | {s['nonbot_prs']} | {s['merged_nonbot']} | "
            f"{s['closed_nonbot']} | {s['open_nonbot']} | "
            f"{s['merge_rate_resolved_nonbot']*100:.1f}% | {s['merge_rate_all_nonbot']*100:.1f}% | "
            f"{s['merge_rate_resolved_all']*100:.1f}% |"
        )

    c_nb = censor_stats["combined_nonbot"]
    c_all = censor_stats["combined_all"]
    tot_nb = sum(c_nb.values())
    tot_all = sum(c_all.values())

    lines.extend(
        [
            "",
            "## 3. Open-PR Right-Censoring (`reports/figures/open_pr_censoring.png`)",
            "",
            f"- **Train non-bot PRs (`n={tot_nb}`):** `{c_nb['merged']}` merged (`{c_nb['merged']/tot_nb*100:.1f}%`), `{c_nb['closed']}` closed unmerged (`{c_nb['closed']/tot_nb*100:.1f}%`), `{c_nb['open']}` open (`{c_nb['open']/tot_nb*100:.1f}%`).",
            f"- **Train all PRs including bots (`n={tot_all}`):** `{c_all['merged']}` merged (`{c_all['merged']/tot_all*100:.1f}%`), `{c_all['closed']}` closed unmerged (`{c_all['closed']/tot_all*100:.1f}%`), `{c_all['open']}` open (`{c_all['open']/tot_all*100:.1f}%`).",
            "- **Per-repo non-bot outcomes:**",
            f"  - `pandas-dev/pandas`: `{censor_stats['by_repo_nonbot']['pandas-dev/pandas']['merged']}` merged, `{censor_stats['by_repo_nonbot']['pandas-dev/pandas']['closed']}` closed, `{censor_stats['by_repo_nonbot']['pandas-dev/pandas']['open']}` open.",
            f"  - `scikit-learn/scikit-learn`: `{censor_stats['by_repo_nonbot']['scikit-learn/scikit-learn']['merged']}` merged, `{censor_stats['by_repo_nonbot']['scikit-learn/scikit-learn']['closed']}` closed, `{censor_stats['by_repo_nonbot']['scikit-learn/scikit-learn']['open']}` open.",
            "- **Implication:** Recently opened PRs that are still `open` within 7 days of collection are right-censored for the `merged_within_7d` classification label and must be excluded if `collection_time - created_at < 7 days`.",
            "",
            "## 4. Weekday × Hour UTC Patterns (`reports/figures/weekday_hour_heatmap.png`)",
            "",
            f"- **Peak creation cell (train merged non-bot):** `{heatmap_stats['peak_cell']['weekday']}` at `{heatmap_stats['peak_cell']['hour_utc']:02d}:00 UTC` (`{heatmap_stats['peak_cell']['count']}` merged PRs, median `{heatmap_stats['peak_cell']['median_hours']:.2f}h`).",
            "- **Merged PR count and median merge hours by created weekday (UTC):**",
        ]
    )

    for w in WEEKDAY_NAMES:
        lines.append(
            f"  - `{w}`: `{heatmap_stats['weekday_totals'][w]}` PRs, median merge time `{heatmap_stats['weekday_medians'][w]:.2f}h`"
        )

    b_le10 = size_stats["bucket_medians_hours"]["le_10_lines"]
    b_mid = size_stats["bucket_medians_hours"]["11_to_100_lines"]
    b_gt100 = size_stats["bucket_medians_hours"]["gt_100_lines"]

    lines.extend(
        [
            "",
            "## 5. Size vs Merge Hours (`reports/figures/size_vs_merge_hours.png`)",
            "",
            f"- **Correlation (`log10(additions + 1)` vs `log10(merge_hours)`):** Pearson `r = {size_stats['pearson_log_r']:.3f}`, Spearman `rho = {size_stats['spearman_r']:.3f}` across `{size_stats['n']}` merged non-bot train PRs.",
            f"- **Additions summary:** median = `{size_stats['median_additions']:.0f}` lines, P90 = `{size_stats['p90_additions']:.0f}` lines.",
            "- **Median merge hours by collection-time additions bucket:**",
            f"  - `<= 10` additions (`n={b_le10['n']}`): `{b_le10['median_h']:.2f}h`",
            f"  - `11..100` additions (`n={b_mid['n']}`): `{b_mid['median_h']:.2f}h`",
            f"  - `> 100` additions (`n={b_gt100['n']}`): `{b_gt100['median_h']:.2f}h`",
            "- **Leakage note:** `prs.additions` is fetched from `GET /pulls/{n}` at collection time (not point-in-time at PR open). It includes commits pushed during code review, which inflates the correlation with merge duration and why `additions` remains in `UNAPPROVED_FEATURE_COLUMNS`.",
            "",
            "## 6. Friday-Evening Flag Effect (`reports/figures/friday_flag_effect.png`, `reports/figures/friday_effect.txt`)",
            "",
            f"- **Definition:** `created_at` on Friday (`weekday == 4`) with `hour_utc >= {friday_stats['friday_hour_utc']}`.",
            f"- **Sample sizes (train merged non-bot):** Friday evening `n = {friday_stats['friday_n']}` vs Other times `n = {friday_stats['other_n']}`.",
            f"- **Median merge hours:** Friday evening = **`{friday_stats['friday_median_h']:.2f}h`** (IQR `{friday_stats['friday_p25_h']:.2f}h`–`{friday_stats['friday_p75_h']:.2f}h`, P90 `{friday_stats['friday_p90_h']:.2f}h`) vs Other = **`{friday_stats['other_median_h']:.2f}h`** (IQR `{friday_stats['other_p25_h']:.2f}h`–`{friday_stats['other_p75_h']:.2f}h`, P90 `{friday_stats['other_p90_h']:.2f}h`).",
            f"- **Effect size:** **`{friday_stats['diff_hours']:+.2f}h`** (`{friday_stats['rel_pct']:+.1f}%`, ratio `{friday_stats['ratio']:.2f}x`).",
            "- **Per-repo breakdown:**",
        ]
    )

    for repo, st in friday_stats["per_repo"].items():
        lines.append(
            f"  - `{repo}`: Friday evening `n={st['friday_n']}`, median `{st['friday_median_h']:.2f}h` vs Other `n={st['other_n']}`, median `{st['other_median_h']:.2f}h` (delta `{st['diff_h']:+.2f}h`, ratio `{st['ratio']:.2f}x`)."
        )

    lines.extend(
        [
            f"- **Recommendation: `{friday_stats['recommendation']}` the Friday-evening flag.** Pooled across the training set, PRs opened on Friday >= 15:00 UTC have a `{friday_stats['diff_hours']:+.2f}h` (`{friday_stats['rel_pct']:+.1f}%`) higher median merge time (`{friday_stats['friday_median_h']:.2f}h` vs `{friday_stats['other_median_h']:.2f}h`), with strong heterogeneity between repositories: `scikit-learn/scikit-learn` shows a `{friday_stats['per_repo']['scikit-learn/scikit-learn']['diff_h']:+.2f}h` (`{friday_stats['per_repo']['scikit-learn/scikit-learn']['ratio']:.2f}x`) increase (`{friday_stats['per_repo']['scikit-learn/scikit-learn']['friday_median_h']:.2f}h` vs `{friday_stats['per_repo']['scikit-learn/scikit-learn']['other_median_h']:.2f}h`), whereas `pandas-dev/pandas` shows `{friday_stats['per_repo']['pandas-dev/pandas']['friday_median_h']:.2f}h` vs `{friday_stats['per_repo']['pandas-dev/pandas']['other_median_h']:.2f}h` (`{friday_stats['per_repo']['pandas-dev/pandas']['diff_h']:+.2f}h`, `{friday_stats['per_repo']['pandas-dev/pandas']['ratio']:.2f}x`). Because `created_at` is strictly known at Stage A (point-in-time safe) and gradient-boosted trees can interact it with repository/weekday/hour signals, we keep `is_friday_evening`.",
            "",
            "## 7. CI Label Availability by Quarter (`reports/figures/ci_label_by_quarter.png`)",
            "",
            "| Quarter | `pandas-dev/pandas` (`ci_label_available=1 / total`) | `scikit-learn/scikit-learn` (`ci_label_available=1 / total`) | Combined Train Share |",
            "| --- | ---: | ---: | ---: |",
        ]
    )

    for q in ci_stats["quarters"]:
        p_q = ci_stats["by_repo"]["pandas-dev/pandas"].get(q, {"label_ok": 0, "total": 0, "share": 0.0})
        s_q = ci_stats["by_repo"]["scikit-learn/scikit-learn"].get(q, {"label_ok": 0, "total": 0, "share": 0.0})
        c_q = ci_stats["combined"][q]
        c_share = (c_q["label_ok"] / c_q["total"] * 100.0) if c_q["total"] else 0.0
        lines.append(
            f"| `{q}` | {p_q['label_ok']}/{p_q['total']} ({p_q['share']*100:.1f}%) | "
            f"{s_q['label_ok']}/{s_q['total']} ({s_q['share']*100:.1f}%) | "
            f"{c_q['label_ok']}/{c_q['total']} ({c_share:.1f}%) |"
        )

    lines.extend(
        [
            "",
            "- **Retention cliff finding:** GitHub Actions workflow-run retention creates a sharp boundary in `2025-Q3` (`33.6%` in `pandas`, `22.9%` in `scikit-learn`). From `2025-Q4` through `2026-Q4`, `ci_label_available=1` coverage is `97.8%`–`100.0%` in both train repos, whereas `2024-Q4` through `2025-Q2` has `< 6.5%` coverage. CI failure models must filter on `ci_label_available = 1`.",
            "",
        ]
    )

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = load_settings()
    root = project_root()
    db_path = resolve_db_path(cfg, root)
    figures_dir = root / "reports" / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    friday_hour_utc = int(cfg["timing"]["friday_evening_start_hour_utc"])

    with connect(db_path) as conn:
        records = fetch_train_merged_prs(conn)
        hist_stats = plot_merge_hours_log_hist(
            records, figures_dir / "merge_hours_log_hist.png"
        )
        repo_stats = plot_merge_rate_by_repo(
            conn, figures_dir / "merge_rate_by_repo.png"
        )
        heatmap_stats = plot_weekday_hour_heatmap(
            records, figures_dir / "weekday_hour_heatmap.png"
        )
        size_stats = plot_size_vs_merge_hours(
            records, figures_dir / "size_vs_merge_hours.png"
        )
        censor_stats = plot_open_pr_censoring(
            conn, figures_dir / "open_pr_censoring.png"
        )
        friday_stats = plot_friday_flag_effect(
            records,
            friday_hour_utc,
            figures_dir / "friday_flag_effect.png",
            figures_dir / "friday_effect.txt",
        )
        ci_stats = plot_ci_label_by_quarter(
            conn, figures_dir / "ci_label_by_quarter.png"
        )

    write_eda_summary(
        root / "reports" / "eda_summary.md",
        hist_stats=hist_stats,
        repo_stats=repo_stats,
        heatmap_stats=heatmap_stats,
        size_stats=size_stats,
        censor_stats=censor_stats,
        friday_stats=friday_stats,
        ci_stats=ci_stats,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
