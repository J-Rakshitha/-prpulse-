"""Print collector resume status without secrets."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

db = Path("data/prpulse.db")
print("db_bytes", db.stat().st_size if db.exists() else 0)
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
print("schema", conn.execute("PRAGMA user_version").fetchone()[0])
print("---repos---")
for row in conn.execute(
    """
    SELECT r.full_name,
           COUNT(p.id) AS prs,
           SUM(CASE WHEN p.ingest_status = 'complete' THEN 1 ELSE 0 END) AS complete,
           SUM(CASE WHEN p.ingest_status = 'partial' THEN 1 ELSE 0 END) AS partial,
           SUM(CASE WHEN p.state = 'merged' THEN 1 ELSE 0 END) AS merged,
           SUM(CASE WHEN p.ci_label_available = 1 THEN 1 ELSE 0 END) AS ci_label_ok
    FROM repos r
    LEFT JOIN prs p ON p.repo_id = r.id
    GROUP BY r.id
    ORDER BY r.full_name
    """
):
    print(dict(row))
print("---checkpoints---")
for row in conn.execute(
    "SELECT repo_full_name, step, updated_at, cursor_json FROM collection_checkpoints ORDER BY 1, 2"
):
    cursor = json.loads(row["cursor_json"] or "{}")
    print(row["repo_full_name"], row["step"], row["updated_at"], json.dumps(cursor)[:400])
print("---decisions.md---")
dec = Path("reports/decisions.md")
print("exists", dec.exists())
