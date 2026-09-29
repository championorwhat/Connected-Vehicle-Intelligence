#!/usr/bin/env bash
# Start the stack, wait for long-running services to be healthy, then require
# every one-shot job (topic creation, migrations) to exit 0.
#   ./scripts/up.sh [extra docker compose args, e.g. --profile observability]
set -euo pipefail
cd "$(dirname "$0")/.."

ONE_SHOTS=(kafka-init migrate-postgres migrate-clickhouse)
SERVICES=(kafka postgres clickhouse redis)

docker compose "$@" up -d --wait --wait-timeout 240 "${SERVICES[@]}"
docker compose "$@" up -d "${ONE_SHOTS[@]}"

# Poll one-shot jobs until they exit (up to 180 s), then check exit codes.
failed=0
for svc in "${ONE_SHOTS[@]}"; do
  state=""; code=""
  for _ in $(seq 1 180); do
    read -r state code < <(docker compose "$@" ps -a --format '{{.State}} {{.ExitCode}}' "$svc")
    [[ "$state" == "exited" ]] && break
    sleep 1
  done
  if [[ "$state" == "exited" && "$code" == "0" ]]; then
    echo "✔ $svc completed"
  else
    echo "✘ $svc state=${state:-missing} exit=${code:-?}"
    docker compose "$@" logs --no-color "$svc" | tail -20
    failed=1
  fi
done

if [[ " $* " == *" observability "* ]]; then
  docker compose "$@" up -d --wait --wait-timeout 120 prometheus grafana
fi
exit $failed
