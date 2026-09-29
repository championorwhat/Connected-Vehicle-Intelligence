#!/usr/bin/env bash
# Seed PostgreSQL with reference data + the deterministic synthetic fleet.
#   ./scripts/seed.sh                 # VEHICLE_COUNT from .env
#   ./scripts/seed.sh --vehicles 100000 --reset
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [[ -f .env ]] && . ./.env; set +a
exec uv run python database/postgres/seeds/load_seed.py "$@"
