#!/usr/bin/env bash
# Idempotently create Kafka topics declared in topics.conf.
# Runs inside the apache/kafka image (see kafka-init in docker-compose.yml).
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP_SERVERS:?KAFKA_BOOTSTRAP_SERVERS is required}"
RF="${KAFKA_REPLICATION_FACTOR:-1}"
MIN_ISR="${KAFKA_MIN_INSYNC_REPLICAS:-1}"
CONF="$(dirname "$0")/topics.conf"
KT=/opt/kafka/bin/kafka-topics.sh

expand() { eval "echo \"$1\""; }   # expand ${VAR} placeholders from the environment

while IFS='|' read -r name partitions configs; do
  name="$(echo "$name" | xargs)"
  [[ -z "$name" || "$name" == \#* ]] && continue
  partitions="$(expand "$(echo "$partitions" | xargs)")"
  configs="$(expand "$(echo "$configs" | xargs)"),min.insync.replicas=${MIN_ISR}"

  args=()
  IFS=',' read -ra kvs <<< "$configs"
  for kv in "${kvs[@]}"; do [[ -n "$kv" ]] && args+=(--config "$kv"); done

  echo "ensuring topic ${name} partitions=${partitions} rf=${RF}"
  "$KT" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
        --topic "$name" --partitions "$partitions" --replication-factor "$RF" "${args[@]}"
done < "$CONF"

echo "--- topics ---"
"$KT" --bootstrap-server "$BOOTSTRAP" --list
