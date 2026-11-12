#!/usr/bin/env bash
# Restore a gzipped pg_dump into DATABASE_URL (target should be empty/fresh).
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${1:?usage: restore_postgres.sh /path/to/prpulse_XXX.sql.gz}"
gunzip -c "$1" | psql "$DATABASE_URL"
echo "restored $1 into DATABASE_URL"
