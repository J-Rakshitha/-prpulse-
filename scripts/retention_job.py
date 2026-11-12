"""CLI: clear old webhook payload_json columns (privacy retention)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.service.retention import run_retention


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear webhook payload_json older than PAYLOAD_RETENTION_DAYS")
    parser.add_argument("--days", type=int, default=None, help="Override PAYLOAD_RETENTION_DAYS")
    parser.add_argument("--db", default=None, help="SQLite path or DATABASE_URL")
    args = parser.parse_args()
    result = run_retention(db_target=args.db, days=args.days)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
