import sqlite3
from pathlib import Path

db = Path("data/prpulse.db")
print("db_bytes", db.stat().st_size if db.exists() else 0)
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
print("prs", conn.execute("SELECT COUNT(*) FROM prs").fetchone()[0])
print(
    "complete",
    conn.execute("SELECT COUNT(*) FROM prs WHERE ingest_status = 'complete'").fetchone()[0],
)
print(
    "partial",
    conn.execute("SELECT COUNT(*) FROM prs WHERE ingest_status = 'partial'").fetchone()[0],
)
print("events", conn.execute("SELECT COUNT(*) FROM pr_events").fetchone()[0])
print("files", conn.execute("SELECT COUNT(*) FROM pr_files").fetchone()[0])
print("commits", conn.execute("SELECT COUNT(*) FROM pr_commits").fetchone()[0])
print("ci", conn.execute("SELECT COUNT(*) FROM ci_runs").fetchone()[0])
for row in conn.execute("SELECT full_name FROM repos"):
    print("repo", row["full_name"])
