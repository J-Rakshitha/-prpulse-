"""Scripted check that Caddy basic-auth protects /dashboard and denies /metrics.

Run when docker compose is up (proxy on :8080):

    python scripts/check_proxy_auth.py
    python scripts/check_proxy_auth.py --base http://127.0.0.1:8080 --user prpulse --password prpulse
"""

from __future__ import annotations

import argparse
import base64
import sys
import urllib.error
import urllib.request


def fetch(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return int(resp.status), resp.read(200).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read(200).decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--user", default="prpulse")
    parser.add_argument("--password", default="prpulse")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    failures: list[str] = []

    code, _ = fetch(f"{base}/metrics")
    if code != 404:
        failures.append(f"/metrics expected 404, got {code}")

    code, _ = fetch(f"{base}/dashboard")
    if code not in (401, 403):
        failures.append(f"/dashboard without auth expected 401/403, got {code}")

    token = base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
    code, body = fetch(f"{base}/dashboard", headers={"Authorization": f"Basic {token}"})
    if code >= 400:
        failures.append(f"/dashboard with auth expected <400, got {code}: {body[:120]}")

    code, _ = fetch(f"{base}/health")
    if code != 200:
        failures.append(f"/health expected 200, got {code}")

    if failures:
        print("FAIL")
        for line in failures:
            print("-", line)
        return 1
    print("OK: /metrics denied, /dashboard requires basic auth, /health open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
