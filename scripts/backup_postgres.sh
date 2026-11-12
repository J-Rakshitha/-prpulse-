#!/usr/bin/env bash
# Compressed, timestamped Postgres backup with simple retention.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$BACKUP_DIR/prpulse_${stamp}.sql.gz"
pg_dump "$DATABASE_URL" | gzip -c > "$out"
echo "wrote $out"
find "$BACKUP_DIR" -name 'prpulse_*.sql.gz' -mtime +"$RETENTION_DAYS" -delete || true
