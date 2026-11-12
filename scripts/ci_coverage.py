import sqlite3

conn = sqlite3.connect("data/prpulse.db")
conn.row_factory = sqlite3.Row
for row in conn.execute(
    """
    SELECT r.full_name,
           COUNT(p.id) AS prs,
           SUM(CASE WHEN EXISTS (
               SELECT 1 FROM ci_runs x WHERE x.pr_id = p.id
           ) THEN 1 ELSE 0 END) AS with_ci,
           SUM(p.ci_label_available) AS label_ok
    FROM repos AS r
    JOIN prs AS p ON p.repo_id = r.id
    GROUP BY r.id
    ORDER BY r.full_name
    """
):
    print(dict(row))
