"""GitHub PR comment bot.

The bot talks to GitHub only through the ``GitHubClient`` interface, so tests
use an in-memory fake and local runs can use ``DryRunGitHubClient`` (logs the
comment instead of posting it).

Idempotency: every comment starts with ``MARKER``. A second event for the same
PR edits that comment instead of adding another.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from src.data.db import connect
from src.features.build import AUTH_RE, CI_RE, DEP_RE, DOC_RE, MIG_RE, TEST_RE
from src.live import outcomes
from src.live import signals
from src.live.signals import record_webhook
from src.service.coverage import adapt_result_for_coverage, coverage_status_for_repo
from src.settings import load_settings

logger = logging.getLogger("prpulse.bot")

MARKER = "<!-- prpulse:marker -->"
CONFIG_FILE = ".prpulse.yml"
OPT_OUT_LABELS = frozenset({"no-prpulse", "prpulse-ignore", "prpulse:ignore"})
COMMENT_ACTIONS = frozenset({"opened", "reopened", "synchronize", "ready_for_review"})
UNSEEN_BANNER = "> **Low confidence: this repository is outside the training data.**"


class GitHubRateLimitError(Exception):
    """GitHub asked us to back off (403/429 with rate-limit headers)."""

    def __init__(self, message: str, *, retry_after: float | None = None, remaining: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.remaining = remaining


class GitHubClient(Protocol):
    def list_issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def create_issue_comment(self, repo: str, number: int, body: str) -> dict[str, Any]: ...
    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]: ...
    def list_pr_files(self, repo: str, number: int) -> list[dict[str, Any]]: ...
    def get_file_text(self, repo: str, path: str, ref: str | None = None) -> str | None: ...


class DryRunGitHubClient:
    """Never writes to GitHub. Used when no token is configured or PRPULSE_BOT_DRY_RUN=1."""

    def list_issue_comments(self, repo, number):
        return []

    def create_issue_comment(self, repo, number, body):
        logger.info("[dry-run] would comment on %s#%s:\n%s", repo, number, body)
        return {"id": 0, "body": body}

    def update_issue_comment(self, repo, comment_id, body):
        logger.info("[dry-run] would update comment %s on %s:\n%s", comment_id, repo, body)
        return {"id": comment_id, "body": body}

    def list_pr_files(self, repo, number):
        return []

    def get_file_text(self, repo, path, ref=None):
        return None


class HttpGitHubClient:
    """Minimal REST client. The token is passed in and never logged."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout: float | httpx.Timeout = 20.0,
    ):
        if isinstance(timeout, (int, float)):
            timeout = httpx.Timeout(float(timeout), connect=min(5.0, float(timeout)))
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self.last_remaining: int | None = None

    def _note_rate_headers(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                self.last_remaining = int(remaining)
                signals.set_github_rate_remaining(self.last_remaining)
            except ValueError:
                pass
        if response.status_code in (403, 429):
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else None
            if remaining == "0" or response.status_code == 429 or "rate limit" in (response.text or "").lower():
                raise GitHubRateLimitError(
                    f"GitHub rate limited ({response.status_code})",
                    retry_after=delay or 60.0,
                    remaining=self.last_remaining,
                )

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        r = self._http.request(method, url, **kwargs)
        self._note_rate_headers(r)
        r.raise_for_status()
        return r

    def _paged(self, url: str, max_pages: int = 10) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            r = self._request("GET", url, params={"per_page": 100, "page": page})
            batch = r.json()
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    def list_issue_comments(self, repo, number):
        return self._paged(f"/repos/{repo}/issues/{number}/comments")

    def create_issue_comment(self, repo, number, body):
        r = self._request("POST", f"/repos/{repo}/issues/{number}/comments", json={"body": body})
        return r.json()

    def update_issue_comment(self, repo, comment_id, body):
        r = self._request("PATCH", f"/repos/{repo}/issues/comments/{comment_id}", json={"body": body})
        return r.json()

    def list_pr_files(self, repo, number):
        return self._paged(f"/repos/{repo}/pulls/{number}/files")

    def get_file_text(self, repo, path, ref=None):
        r = self._http.get(f"/repos/{repo}/contents/{path}", params={"ref": ref} if ref else None)
        self._note_rate_headers(r)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return None


def build_github_client(settings: Any = None) -> GitHubClient:
    """Build a PAT or App-mode client. Tests inject a fake via ``create_app``."""
    if os.environ.get("PRPULSE_BOT_DRY_RUN") == "1":
        logger.warning("Bot is in dry-run mode: comments are logged, not posted.")
        return DryRunGitHubClient()
    try:
        from src.runtime_settings import get_settings
        from src.service.github_app import resolve_github_client

        cfg = settings if settings is not None else get_settings()
        if cfg.auth_mode == "app":
            # Installation-scoped tokens are resolved per delivery; start dry until then.
            logger.info("Auth mode=app; installation tokens are resolved per webhook delivery")
            return DryRunGitHubClient()
        token = cfg.github_token.get_secret_value().strip() or os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            logger.warning("Bot is in dry-run mode: comments are logged, not posted.")
            return DryRunGitHubClient()
        return resolve_github_client(None, cfg)
    except Exception:  # noqa: BLE001 - keep collector/tests working without pydantic-settings wired
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            logger.warning("Bot is in dry-run mode: comments are logged, not posted.")
            return DryRunGitHubClient()
        return HttpGitHubClient(token)


# --------------------------------------------------------------------------- helpers


def _fmt_hours(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} days"


def render_comment(result: dict[str, Any], stage: str) -> str:
    privacy = os.environ.get("PRIVACY_URL", "").strip()
    footer_privacy = f" Privacy: {privacy}." if privacy else ""
    if result.get("unseen") or result.get("coverage_status") == "unseen":
        bucket = result.get("coarse_bucket") or "unknown"
        lines = [
            MARKER,
            "### PR Pulse estimate",
            "",
            UNSEEN_BANNER,
            "",
            f"- Coarse merge-time bucket: **{bucket}** (no hour estimate for unseen repos)",
        ]
        if result.get("prob_merged_within_7d") is not None:
            lines.append(
                f"- Chance of merging within 7 days: **{result['prob_merged_within_7d'] * 100:.0f}%**"
            )
        if result.get("prob_ci_fail") is not None:
            lines.append(f"- Chance the first CI run fails: **{result['prob_ci_fail'] * 100:.0f}%**")
        lines += [
            "",
            f"<sub>Automated estimate from historical PRs (stage {stage}, model `{result.get('model_version', 'unknown')}`). "
            f"It is not a promise or a review.{footer_privacy} To opt out, add the `no-prpulse` label or put "
            f"`enabled: false` in `{CONFIG_FILE}`.</sub>",
        ]
        return "\n".join(lines)

    p10, p50, p90 = result["merge_hours_p10"], result["merge_hours_p50"], result["merge_hours_p90"]
    lines = [
        MARKER,
        "### PR Pulse estimate",
        "",
        f"- Likely time to merge: **{_fmt_hours(p50)}** (10th to 90th percentile: {_fmt_hours(p10)} to {_fmt_hours(p90)})",
    ]
    if result.get("prob_merged_within_7d") is not None:
        lines.append(f"- Chance of merging within 7 days: **{result['prob_merged_within_7d'] * 100:.0f}%**")
    if result.get("prob_ci_fail") is not None:
        lines.append(f"- Chance the first CI run fails: **{result['prob_ci_fail'] * 100:.0f}%**")
    explanation = result.get("explanation") or []
    if explanation:
        lines += ["", "**Main factors**"]
        lines += [f"- {item['plain_english']}" for item in explanation[:3]]
    suggestions = result.get("suggestions") or []
    if suggestions:
        lines += ["", "**What-ifs (estimates only, not guarantees)**"]
        for s in suggestions[:3]:
            lines.append(
                f"- {s['action']}: about {_fmt_hours(abs(s['estimated_change_hours']))} faster "
                f"({_fmt_hours(s['predicted_hours_before'])} to {_fmt_hours(s['predicted_hours_after'])})"
            )
    lines += [
        "",
        f"<sub>Automated estimate from historical PRs (stage {stage}, model `{result.get('model_version', 'unknown')}`). "
        f"It is not a promise or a review.{footer_privacy} To opt out, add the `no-prpulse` label or put "
        f"`enabled: false` in `{CONFIG_FILE}`.</sub>",
    ]
    return "\n".join(lines)


def parse_prpulse_config(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        logger.warning("%s is not valid YAML; ignoring it", CONFIG_FILE)
        return {}
    return data if isinstance(data, dict) else {}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


# --------------------------------------------------------------------------- bot


class PRBot:
    def __init__(
        self,
        client: GitHubClient,
        service: Any | None,
        cfg: dict[str, Any] | None = None,
        db_path: Path | None = None,
        stage: str = "A",
    ):
        self.client = client
        self.service = service
        self.cfg = cfg if cfg is not None else load_settings()
        if db_path is None:
            self.db_path = None
        else:
            from src.data.database import is_postgres_url

            self.db_path = db_path if is_postgres_url(db_path) else Path(db_path)
        self.stage = stage
        self._locks: dict[tuple[str, int], threading.Lock] = {}
        self._locks_guard = threading.Lock()
        bots = self.cfg.get("bots", {})
        self._bot_logins = {x.lower() for x in [*bots.get("author_logins", []), *bots.get("extra_logins", [])]}

    # -- opt-out ----------------------------------------------------------

    def opt_out_reason(self, payload: dict[str, Any]) -> str | None:
        pr = payload["pull_request"]
        labels = {str(lbl.get("name", "")).lower() for lbl in pr.get("labels", [])}
        if labels & OPT_OUT_LABELS:
            return "label"
        repo = payload["repository"]["full_name"]
        base_ref = (pr.get("base") or {}).get("ref")
        try:
            config = parse_prpulse_config(self.client.get_file_text(repo, CONFIG_FILE, base_ref))
        except Exception:  # noqa: BLE001 - config lookup must never block or crash the bot
            logger.exception("Could not read %s for %s", CONFIG_FILE, repo)
            config = {}
        if config.get("enabled") is False or config.get("opt_out") is True:
            return "config"
        return None

    def is_bot_author(self, pr: dict[str, Any]) -> bool:
        user = pr.get("user") or {}
        login = str(user.get("login", "")).lower()
        return user.get("type") == "Bot" or login in self._bot_logins

    # -- features ---------------------------------------------------------

    def build_features(self, payload: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
        """Features available from the webhook payload plus the file list.

        Repo-state features (open PR count, recent merge time, reviewer activity)
        are not computed live; the model imputes them with training medians.
        """
        pr = payload["pull_request"]
        repo = payload["repository"]["full_name"]
        paths = [f.get("filename", "") for f in files]
        n_files = len(paths) or int(pr.get("changed_files") or 0)
        additions = sum(int(f.get("additions") or 0) for f in files) if files else int(pr.get("additions") or 0)
        deletions = sum(int(f.get("deletions") or 0) for f in files) if files else int(pr.get("deletions") or 0)
        created = _parse_ts(pr.get("created_at")) or datetime.now(timezone.utc)
        fri_hour = int(self.cfg["timing"]["friday_evening_start_hour_utc"])
        features: dict[str, Any] = {
            "draft": int(bool(pr.get("draft"))),
            "commits_at_open": int(pr.get("commits") or 1),
            "n_files_approx": n_files,
            "additions_approx": additions,
            "deletions_approx": deletions,
            "touches_tests": int(any(TEST_RE.search(p) for p in paths)),
            "touches_docs": int(any(DOC_RE.search(p) for p in paths)),
            "touches_ci_config": int(any(CI_RE.search(p) for p in paths)),
            "touches_deps": int(any(DEP_RE.search(p) for p in paths)),
            "touches_auth": int(any(AUTH_RE.search(p) for p in paths)),
            "touches_migration": int(any(MIG_RE.search(p) for p in paths)),
            "docs_only": int(bool(paths) and all(DOC_RE.search(p) for p in paths)),
            "hour_utc": created.hour,
            "weekday": created.weekday(),
            "is_weekend": int(created.weekday() >= 5),
            "month": created.month,
            "is_friday_evening": int(created.weekday() == 4 and created.hour >= fri_hour),
            "repo_is_pandas": int(repo == "pandas-dev/pandas"),
        }
        features.update(self._author_history(repo, pr))
        return features

    def _author_history(self, repo: str, pr: dict[str, Any]) -> dict[str, Any]:
        if self.db_path is None:
            return {}
        login = (pr.get("user") or {}).get("login")
        created = outcomes._norm_ts(pr.get("created_at")) or outcomes.now_iso()
        if not login:
            return {}
        conn = connect(self.db_path)
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS opened,
                       SUM(CASE WHEN p.merged_at IS NOT NULL AND p.merged_at < ? THEN 1 ELSE 0 END) AS merged
                FROM prs p JOIN repos r ON r.id = p.repo_id
                WHERE r.full_name = ? AND p.author_login = ? AND p.created_at < ? AND p.number <> ?
                """,
                (created, repo, login, created, pr["number"]),
            ).fetchone()
        finally:
            conn.close()
        opened = int(row["opened"] or 0)
        merged = int(row["merged"] or 0)
        out: dict[str, Any] = {
            "prior_opened_count": opened,
            "prior_merged_count": merged,
            "first_time_contributor": int(opened == 0),
        }
        if opened:
            out["prior_merge_rate"] = merged / opened
        return out

    # -- events -----------------------------------------------------------

    def _lock_for(self, repo: str, number: int) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault((repo, number), threading.Lock())

    def on_pull_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        repo = payload["repository"]["full_name"]
        pr = payload["pull_request"]
        number = int(pr["number"])

        if action == "closed":
            return self._record_closed(repo, pr)
        if action == "reopened" and self.db_path is not None:
            conn = connect(self.db_path)
            try:
                outcomes.on_pr_reopened(conn, repo, number)
            finally:
                conn.close()
        if action not in COMMENT_ACTIONS:
            return {"status": "ignored", "reason": f"action {action!r} not handled"}
        if pr.get("state", "open") != "open":
            return {"status": "skipped", "reason": "pull request is not open"}
        if self.is_bot_author(pr):
            return {"status": "skipped", "reason": "bot author"}
        reason = self.opt_out_reason(payload)
        if reason:
            return {"status": "skipped", "reason": f"opted out via {reason}"}
        if self.service is None:
            return {"status": "skipped", "reason": "no model loaded"}

        with self._lock_for(repo, number):
            try:
                # Out-of-order guard: never comment if the PR is already closed in the payload.
                if pr.get("state", "open") != "open":
                    return {"status": "skipped", "reason": "pull request is not open"}
                files = self.client.list_pr_files(repo, number)
                features = self.build_features(payload, files)
                coverage = "seen"
                if self.db_path is not None:
                    conn = connect(self.db_path)
                    try:
                        coverage = coverage_status_for_repo(conn, repo)
                    finally:
                        conn.close()
                include_explanation = coverage == "seen"
                result = self.service.predict(
                    self.stage,
                    features,
                    repo_full_name=repo,
                    pr_number=number,
                    author_login=(pr.get("user") or {}).get("login"),
                    include_explanation=include_explanation,
                    created_at=outcomes._norm_ts(pr.get("created_at")),
                    draft=bool(pr.get("draft")),
                    coverage_status=coverage,
                )
                result = adapt_result_for_coverage(result, coverage)
                body = render_comment(result, self.stage)
                return self._upsert_comment(repo, number, body)
            except GitHubRateLimitError as exc:
                logger.error(
                    "GitHub rate limit while handling %s#%s remaining=%s retry_after=%s",
                    repo,
                    number,
                    exc.remaining,
                    exc.retry_after,
                )
                # Propagate to the worker as a retryable error (no comment posted).
                raise
            except httpx.TimeoutException as exc:
                logger.error("GitHub timeout while handling %s#%s: %s", repo, number, exc)
                raise
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                logger.exception("GitHub API %s while handling %s#%s", code, repo, number)
                # 5xx -> retryable via worker; 4xx (except rate limit) -> soft error, no comment.
                if code >= 500:
                    raise
                return {"status": "error", "reason": f"github_api_{code}", "repo": repo, "number": number}
            except Exception:
                # Prediction / feature failures must never post a partial comment.
                logger.exception("Prediction failed for %s#%s; no comment posted", repo, number)
                raise

    def _upsert_comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        existing = next(
            (c for c in self.client.list_issue_comments(repo, number) if MARKER in (c.get("body") or "")),
            None,
        )
        if existing is None:
            created = self.client.create_issue_comment(repo, number, body)
            return {"status": "created", "comment_id": created.get("id")}
        if (existing.get("body") or "") == body:
            return {"status": "unchanged", "comment_id": existing.get("id")}
        self.client.update_issue_comment(repo, int(existing["id"]), body)
        return {"status": "updated", "comment_id": existing.get("id")}

    def _record_closed(self, repo: str, pr: dict[str, Any]) -> dict[str, Any]:
        if self.db_path is None:
            return {"status": "ignored", "reason": "no database configured"}
        conn = connect(self.db_path)
        try:
            pr_id = outcomes.on_pr_closed(
                conn,
                repo,
                int(pr["number"]),
                merged=bool(pr.get("merged")),
                closed_at=pr.get("closed_at"),
                merged_at=pr.get("merged_at"),
            )
        finally:
            conn.close()
        return {"status": "outcome_recorded" if pr_id else "ignored", "reason": None if pr_id else "PR has no stored prediction"}

    def on_workflow_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("action") != "completed" or self.db_path is None:
            return {"status": "ignored", "reason": "not a completed run"}
        run = payload.get("workflow_run") or {}
        repo = payload["repository"]["full_name"]
        recorded = 0
        conn = connect(self.db_path)
        try:
            for item in run.get("pull_requests") or []:
                if outcomes.on_ci_completed(conn, repo, int(item["number"]), run.get("conclusion"), self.cfg):
                    recorded += 1
        finally:
            conn.close()
        return {"status": "outcome_recorded" if recorded else "ignored", "prs": recorded}


def note_result(event: str, result: dict[str, Any]) -> None:
    record_webhook(event, result.get("status", "unknown"))
