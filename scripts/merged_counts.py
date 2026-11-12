import sqlite3

conn = sqlite3.connect("data/prpulse.db")
print(
    "merged_all",
    conn.execute("SELECT COUNT(*) FROM prs WHERE state = 'merged'").fetchone()[0],
)
print(
    "merged_nonbot",
    conn.execute(
        "SELECT COUNT(*) FROM prs WHERE state = 'merged' AND is_bot = 0"
    ).fetchone()[0],
)
print(
    "merged_bot",
    conn.execute(
        "SELECT COUNT(*) FROM prs WHERE state = 'merged' AND is_bot = 1"
    ).fetchone()[0],
)
