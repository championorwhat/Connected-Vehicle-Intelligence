#!/usr/bin/env bash
# Apply PostgreSQL (dbmate) and ClickHouse (clickhouse-client runner) migrations.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm migrate-postgres
docker compose run --rm migrate-clickhouse
