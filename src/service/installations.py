"""Track GitHub App installations and their repositories."""

from __future__ import annotations

import logging
from typing import Any

from src.service.store import now_iso

logger = logging.getLogger("prpulse.installations")


def upsert_installation(
    conn,
    *,
    installation_id: int,
    account_login: str | None = None,
    account_type: str | None = None,
    repository_selection: str | None = None,
    suspended: bool = False,
    uninstalled: bool = False,
) -> None:
    ts = now_iso()
    existing = conn.execute(
        "SELECT installation_id FROM installations WHERE installation_id = ?",
        (installation_id,),
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO installations (
                installation_id, account_login, account_type, repository_selection,
                suspended, created_at, updated_at, uninstalled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                installation_id,
                account_login,
                account_type,
                repository_selection,
                int(bool(suspended)),
                ts,
                ts,
                ts if uninstalled else None,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE installations
            SET account_login = COALESCE(?, account_login),
                account_type = COALESCE(?, account_type),
                repository_selection = COALESCE(?, repository_selection),
                suspended = ?,
                updated_at = ?,
                uninstalled_at = CASE WHEN ? = 1 THEN ? ELSE uninstalled_at END
            WHERE installation_id = ?
            """,
            (
                account_login,
                account_type,
                repository_selection,
                int(bool(suspended)),
                ts,
                int(bool(uninstalled)),
                ts if uninstalled else None,
                installation_id,
            ),
        )
    conn.commit()


def add_repos(conn, installation_id: int, repos: list[str]) -> None:
    ts = now_iso()
    for full_name in repos:
        if not full_name or "/" not in full_name:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO installation_repos (installation_id, repo_full_name, added_at)
            VALUES (?, ?, ?)
            """,
            (installation_id, full_name, ts),
        )
    conn.commit()


def remove_repos(conn, installation_id: int, repos: list[str]) -> None:
    for full_name in repos:
        conn.execute(
            "DELETE FROM installation_repos WHERE installation_id = ? AND repo_full_name = ?",
            (installation_id, full_name),
        )
    conn.commit()


def count_active_installations(conn) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM installations
        WHERE suspended = 0 AND uninstalled_at IS NULL
        """
    ).fetchone()
    return int(row["n"] if hasattr(row, "keys") else row[0])


def installation_id_for_repo(conn, repo_full_name: str) -> int | None:
    row = conn.execute(
        """
        SELECT ir.installation_id
        FROM installation_repos ir
        JOIN installations i ON i.installation_id = ir.installation_id
        WHERE ir.repo_full_name = ?
          AND i.suspended = 0
          AND i.uninstalled_at IS NULL
        ORDER BY ir.added_at DESC
        LIMIT 1
        """,
        (repo_full_name,),
    ).fetchone()
    return int(row["installation_id"]) if row else None


def apply_installation_event(conn, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if installation_id is None:
        return {"status": "ignored", "reason": "no installation id"}
    installation_id = int(installation_id)
    account = installation.get("account") or {}
    action = payload.get("action")

    if event == "installation":
        if action in ("deleted", "suspend"):
            upsert_installation(
                conn,
                installation_id=installation_id,
                account_login=account.get("login"),
                account_type=account.get("type"),
                repository_selection=installation.get("repository_selection"),
                suspended=action == "suspend",
                uninstalled=action == "deleted",
            )
            if action == "deleted":
                conn.execute("DELETE FROM installation_repos WHERE installation_id = ?", (installation_id,))
                conn.commit()
            return {"status": "installation_updated", "action": action}
        upsert_installation(
            conn,
            installation_id=installation_id,
            account_login=account.get("login"),
            account_type=account.get("type"),
            repository_selection=installation.get("repository_selection"),
            suspended=False,
            uninstalled=False,
        )
        repos = [r.get("full_name") for r in (payload.get("repositories") or []) if isinstance(r, dict)]
        add_repos(conn, installation_id, [r for r in repos if r])
        return {"status": "installation_updated", "action": action, "repos": len(repos)}

    if event == "installation_repositories":
        upsert_installation(
            conn,
            installation_id=installation_id,
            account_login=account.get("login"),
            account_type=account.get("type"),
            repository_selection=installation.get("repository_selection"),
        )
        added = [r.get("full_name") for r in (payload.get("repositories_added") or []) if isinstance(r, dict)]
        removed = [r.get("full_name") for r in (payload.get("repositories_removed") or []) if isinstance(r, dict)]
        add_repos(conn, installation_id, [r for r in added if r])
        remove_repos(conn, installation_id, [r for r in removed if r])
        return {"status": "installation_repos_updated", "added": len(added), "removed": len(removed)}

    return {"status": "ignored", "reason": f"event {event!r}"}
