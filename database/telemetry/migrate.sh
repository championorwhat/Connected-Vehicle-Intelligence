#!/usr/bin/env bash
# Minimal ClickHouse migration runner (dbmate-compatible file format).
#
# Why not dbmate for ClickHouse: dbmate sends a file as one query and ClickHouse
# rejects multi-statement queries. clickhouse-client --multiquery handles them.
# ClickHouse DDL is not transactional, so every statement uses IF NOT EXISTS and
# a failed run can simply be re-run.
#
# Usage (inside the clickhouse-server image): migrate.sh [up|status]
set -euo pipefail

DIR="${MIGRATIONS_DIR:-/db/migrations}"
CH=(clickhouse-client --host "${CLICKHOUSE_HOST:-clickhouse}" --user "${CLICKHOUSE_USER}"
    --password "${CLICKHOUSE_PASSWORD}" --database "${CLICKHOUSE_DB:-telemetry}")

for _ in $(seq 1 30); do "${CH[@]}" -q "SELECT 1" >/dev/null 2>&1 && break; sleep 1; done

"${CH[@]}" -q "CREATE TABLE IF NOT EXISTS schema_migrations
  (version String, applied_at DateTime DEFAULT now()) ENGINE = ReplacingMergeTree ORDER BY version"

applied="$("${CH[@]}" -q "SELECT version FROM schema_migrations FINAL FORMAT TSV")"

for file in $(ls "$DIR"/*.sql | sort); do
  version="$(basename "$file" | cut -d_ -f1)"
  if grep -qx "$version" <<< "$applied"; then
    [[ "${1:-up}" == "status" ]] && echo "[applied] $(basename "$file")"
    continue
  fi
  [[ "${1:-up}" == "status" ]] && { echo "[pending] $(basename "$file")"; continue; }
  echo "Applying: $(basename "$file")"
  # Keep only the '-- migrate:up' section; substitute deployment-specific placeholders
  # (the Kafka broker list differs between compose, CI and cloud).
  awk '/^-- migrate:up/{f=1;next} /^-- migrate:down/{f=0} f' "$file" \
    | sed "s|{{KAFKA_BROKERS}}|${KAFKA_BROKERS:-kafka:19092}|g" \
    | "${CH[@]}" --multiquery
  "${CH[@]}" -q "INSERT INTO schema_migrations (version) VALUES ('$version')"
  echo "Applied: $(basename "$file")"
done
