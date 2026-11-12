"""Phase 1 gate checks (completeness + training-size)."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "prpulse.db"
TRAIN = ("pandas-dev/pandas", "scikit-learn/scikit-learn")


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    partial = conn.execute(
        "SELECT COUNT(*) AS n FROM prs WHERE ingest_status != 'complete'"
    ).fetchone()["n"]
    print(f"partial_or_failed_prs={partial}")
    for name in TRAIN:
        row = conn.execute(
            """
            SELECT COUNT(*) AS prs,
                   SUM(CASE WHEN ingest_status='complete' THEN 1 ELSE 0 END) AS complete,
                   SUM(CASE WHEN is_bot=0 AND state='merged' THEN 1 ELSE 0 END) AS nonbot_merged
            FROM prs p JOIN repos r ON r.id = p.repo_id
            WHERE r.full_name = ?
            """,
            (name,),
        ).fetchone()
        print(dict(row) | {"repo": name})
    total = conn.execute(
        """
        SELECT COUNT(*) AS n FROM prs p JOIN repos r ON r.id = p.repo_id
        WHERE r.full_name IN (?, ?) AND p.is_bot = 0 AND p.state = 'merged'
        """,
        TRAIN,
    ).fetchone()["n"]
    print(f"train_nonbot_merged={total}")
    if partial:
        print("GATE FAIL: incomplete PRs remain")
        return 1
    if total < 1000:
        print("GATE FAIL: fewer than 1000 non-bot merged PRs")
        return 1
    print("GATE PASS: Phase 1 completeness + training size")
    return 0


if __name__ == "__main__":
    sys.exit(main())
