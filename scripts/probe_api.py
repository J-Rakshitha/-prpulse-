"""One-off public API probe. Prints counts and field names. Does not store bodies."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2026-03-10",
    "User-Agent": "prpulse-phase1-probe",
}
CUTOFF = "2024-11-02"
TEST_REPOS = ["psf/requests", "pallets/flask", "encode/httpx"]
CANDIDATES = [
    "pandas-dev/pandas",
    "scikit-learn/scikit-learn",
    "huggingface/transformers",
    "numpy/numpy",
    "scipy/scipy",
    "fastapi/fastapi",
    "pydantic/pydantic",
    "apache/airflow",
    "matplotlib/matplotlib",
    "lightning-ai/pytorch-lightning",
]


def get(url: str) -> tuple[object, dict[str, str]]:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
            headers = {key.lower(): value for key, value in response.headers.items()}
            return payload, headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        remaining = exc.headers.get("X-RateLimit-Remaining")
        reset = exc.headers.get("X-RateLimit-Reset")
        retry = exc.headers.get("Retry-After")
        print(
            json.dumps(
                {
                    "error": exc.code,
                    "url": url.split("?")[0],
                    "remaining": remaining,
                    "reset": reset,
                    "retry_after": retry,
                    "body": body,
                }
            )
        )
        if exc.code in {403, 429} and reset:
            wait = max(0, int(reset) - int(time.time()) + 1)
            if wait > 120:
                raise SystemExit(f"rate limited for {wait}s; stopping probe")
            time.sleep(wait)
            return get(url)
        raise


def search_count(query: str) -> int:
    url = "https://api.github.com/search/issues?" + urllib.parse.urlencode(
        {"q": query, "per_page": 1}
    )
    payload, headers = get(url)
    print(
        "search_remaining",
        headers.get("x-ratelimit-remaining"),
        "resource",
        headers.get("x-ratelimit-resource"),
        flush=True,
    )
    assert isinstance(payload, dict)
    time.sleep(7)
    return int(payload["total_count"])


def main() -> None:
    print("now", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), flush=True)
    rate_url = "https://api.github.com/rate_limit"
    rate, _ = get(rate_url)
    core = rate["resources"]["core"]
    search = rate["resources"]["search"]
    print(
        json.dumps(
            {
                "core_remaining": core["remaining"],
                "core_limit": core["limit"],
                "search_remaining": search["remaining"],
                "search_limit": search["limit"],
            }
        ),
        flush=True,
    )

    for repo in TEST_REPOS:
        all_count = search_count(f"repo:{repo} is:pr created:>={CUTOFF}")
        merged = search_count(f"repo:{repo} is:pr is:merged created:>={CUTOFF}")
        opened = search_count(f"repo:{repo} is:pr is:open created:>={CUTOFF}")
        runs, run_headers = get(
            f"https://api.github.com/repos/{repo}/actions/runs?per_page=1"
        )
        workflows, _ = get(
            f"https://api.github.com/repos/{repo}/actions/workflows?per_page=1"
        )
        print(
            json.dumps(
                {
                    "repo": repo,
                    "search_prs_since": all_count,
                    "search_merged": merged,
                    "search_open": opened,
                    "actions_run_total_count": runs.get("total_count") if isinstance(runs, dict) else None,
                    "actions_workflow_total": workflows.get("total_count") if isinstance(workflows, dict) else None,
                    "core_remaining": run_headers.get("x-ratelimit-remaining"),
                }
            ),
            flush=True,
        )

    listed, list_headers = get(
        "https://api.github.com/repos/psf/requests/pulls?state=all&sort=created&direction=desc&per_page=2"
    )
    assert isinstance(listed, list) and listed
    sample = listed[0]
    print(
        json.dumps(
            {
                "list_keys_has_additions": "additions" in sample,
                "list_keys_has_changed_files": "changed_files" in sample,
                "list_keys_has_commits": "commits" in sample,
                "list_keys_has_draft": "draft" in sample,
                "list_keys_has_merged_at": "merged_at" in sample,
                "list_number": sample.get("number"),
                "list_created_at": sample.get("created_at"),
                "list_link": list_headers.get("link"),
                "per_page_returned": len(listed),
            }
        ),
        flush=True,
    )
    number = sample["number"]
    detail, _ = get(f"https://api.github.com/repos/psf/requests/pulls/{number}")
    assert isinstance(detail, dict)
    print(
        json.dumps(
            {
                "detail_has_additions": "additions" in detail,
                "detail_additions": detail.get("additions"),
                "detail_deletions": detail.get("deletions"),
                "detail_changed_files": detail.get("changed_files"),
                "detail_commits": detail.get("commits"),
                "detail_draft": detail.get("draft"),
                "detail_state": detail.get("state"),
                "detail_merged_at": detail.get("merged_at"),
                "detail_closed_at": detail.get("closed_at"),
                "detail_created_at": detail.get("created_at"),
                "user_type": (detail.get("user") or {}).get("type"),
                "user_login": (detail.get("user") or {}).get("login"),
                "base_ref": (detail.get("base") or {}).get("ref"),
            }
        ),
        flush=True,
    )
    timeline, timeline_headers = get(
        f"https://api.github.com/repos/psf/requests/issues/{number}/timeline?per_page=100"
    )
    assert isinstance(timeline, list)
    committed = next((event for event in timeline if event.get("event") == "committed"), None)
    committed_keys = sorted(committed.keys()) if isinstance(committed, dict) else None
    author_keys = None
    if isinstance(committed, dict) and isinstance(committed.get("author"), dict):
        author_keys = sorted(committed["author"].keys())
    print(
        json.dumps(
            {
                "timeline_count": len(timeline),
                "timeline_link": timeline_headers.get("link"),
                "timeline_events": sorted({event.get("event") for event in timeline}),
                "committed_keys": committed_keys,
                "committed_author_keys": author_keys,
                "committed_author_date": (committed or {}).get("author", {}).get("date")
                if isinstance(committed, dict)
                else None,
            }
        ),
        flush=True,
    )
    files, file_headers = get(
        f"https://api.github.com/repos/psf/requests/pulls/{number}/files?per_page=100"
    )
    assert isinstance(files, list)
    print(
        json.dumps(
            {
                "files_returned": len(files),
                "file_keys": sorted(files[0].keys()) if files else [],
                "file_status": files[0].get("status") if files else None,
                "files_link": file_headers.get("link"),
            }
        ),
        flush=True,
    )
    runs, _ = get(
        "https://api.github.com/repos/psf/requests/actions/runs?per_page=5&event=pull_request"
    )
    assert isinstance(runs, dict)
    summaries = []
    for run in runs.get("workflow_runs") or []:
        pulls = run.get("pull_requests") or []
        summaries.append(
            {
                "id": run.get("id"),
                "event": run.get("event"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
                "run_started_at": run.get("run_started_at"),
                "updated_at": run.get("updated_at"),
                "pull_count": len(pulls),
                "pull_numbers": [item.get("number") for item in pulls],
                "name": run.get("name"),
            }
        )
    print(
        json.dumps(
            {"actions_sample_total_count": runs.get("total_count"), "runs": summaries}
        ),
        flush=True,
    )

    events, _ = get("https://api.github.com/repos/psf/requests/issues/events?per_page=100")
    assert isinstance(events, list)
    reopened = [event for event in events if event.get("event") == "reopened"]
    print(
        json.dumps(
            {
                "repo_events_page": len(events),
                "reopened_on_page": [
                    {
                        "issue": (event.get("issue") or {}).get("number"),
                        "created_at": event.get("created_at"),
                        "has_pull": "pull_request" in (event.get("issue") or {}),
                    }
                    for event in reopened
                ],
            }
        ),
        flush=True,
    )

    print("CANDIDATES", flush=True)
    for repo in CANDIDATES:
        count = search_count(f"repo:{repo} is:pr created:>={CUTOFF}")
        runs, _ = get(f"https://api.github.com/repos/{repo}/actions/runs?per_page=1")
        workflows, _ = get(f"https://api.github.com/repos/{repo}/actions/workflows?per_page=1")
        print(
            json.dumps(
                {
                    "candidate": repo,
                    "search_prs_since": count,
                    "actions_run_total_count": runs.get("total_count") if isinstance(runs, dict) else None,
                    "workflow_total": workflows.get("total_count") if isinstance(workflows, dict) else None,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
