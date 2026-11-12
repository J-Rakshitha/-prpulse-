"""Time a few GitHub calls with the configured token."""

from __future__ import annotations

import time

from src.data.github_client import GitHubClient
from src.settings import load_settings, require_env


def timed(client: GitHubClient, label: str, fn) -> None:
    start = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - start
    size = len(result) if isinstance(result, list) else type(result).__name__
    print(f"{label}: {elapsed:.2f}s -> {size}; remaining={client.last_remaining}")


def main() -> None:
    load_settings()
    with GitHubClient(require_env("GITHUB_TOKEN"), per_page=100) as client:
        timed(client, "rate_limit", client.rate_limit)
        timed(
            client,
            "list_pulls_page1",
            lambda: client.get_json(
                "/repos/psf/requests/pulls",
                {"state": "all", "per_page": 100, "page": 1},
            ),
        )
        detail = client.get_json("/repos/psf/requests/pulls/7000")
        assert isinstance(detail, dict)
        print(f"get_pull_7000 remaining={client.last_remaining}")
        num = detail["number"]
        timed(client, "files", lambda: client.paginate(f"/repos/psf/requests/pulls/{num}/files"))
        timed(client, "commits", lambda: client.paginate(f"/repos/psf/requests/pulls/{num}/commits"))
        timed(
            client,
            "timeline",
            lambda: client.paginate(f"/repos/psf/requests/issues/{num}/timeline"),
        )
    print("done")


if __name__ == "__main__":
    main()
