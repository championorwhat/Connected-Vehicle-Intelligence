# Prognos — developer entry points. Run `make help`.
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE      := docker compose
COMPOSE_HA   := docker compose -f docker-compose.yml -f infra/docker/compose.kafka-ha.yml

.PHONY: help
help: ## Show available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Setup -------------------------------------------------------------------
.PHONY: doctor env bootstrap
doctor: ## Check required local tools and Docker resources
	@./scripts/setup.sh --check

env: ## Create .env from .env.example if it does not exist
	@test -f .env && echo ".env already exists (not overwritten)" || (cp .env.example .env && echo "created .env")

bootstrap: env ## Install Python dev tooling (uv) and git hooks
	uv sync --group dev
	uv run pre-commit install

# --- Stack -------------------------------------------------------------------
.PHONY: config up up-obs up-ha down clean ps logs topics migrate seed seed-100k schema-dump psql chsql
config: env ## Validate docker-compose files
	$(COMPOSE) config --quiet && echo "docker-compose.yml: OK"
	$(COMPOSE_HA) config --quiet && echo "kafka HA overlay: OK"

up: env ## Start Kafka, Postgres, ClickHouse, Redis; create topics; run migrations
	./scripts/up.sh

up-obs: env ## Same as `up` plus Prometheus/Grafana
	./scripts/up.sh --profile observability

up-ha: env ## Start core with a 3-broker Kafka cluster (needs more memory)
	$(COMPOSE_HA) up -d --wait

down: ## Stop the stack (keeps data volumes)
	$(COMPOSE) --profile observability --profile pipeline down --remove-orphans

clean: ## Stop the stack AND delete all data volumes (all profiles, incl. pipeline)
	$(COMPOSE_HA) --profile observability --profile pipeline down -v --remove-orphans

ps: ## Show service status
	$(COMPOSE) --profile observability ps

logs: ## Follow logs (make logs s=kafka)
	$(COMPOSE) logs -f $(s)

topics: ## List Kafka topics with partition details
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:19092 --describe

migrate: ## Apply PostgreSQL + ClickHouse migrations (also run by `make up`)
	./scripts/migrate.sh

seed: ## Seed PostgreSQL with VEHICLE_COUNT synthetic vehicles (from .env)
	./scripts/seed.sh

seed-100k: ## Reset and seed 100,000 vehicles; saves timing evidence
	./scripts/seed.sh --vehicles 100000 --reset --evidence evidence/benchmarks/m2-seed-100k.json

schema-dump: ## Regenerate database/postgres/schema.sql from the running database
	$(COMPOSE) exec -T postgres sh -c 'pg_dump -s --no-owner --no-privileges -U $$POSTGRES_USER $$POSTGRES_DB' \
	  | grep -v '^\\\(un\)\?restrict ' > database/postgres/schema.sql

psql: ## Open a psql shell
	$(COMPOSE) exec postgres sh -c 'psql -U $$POSTGRES_USER $$POSTGRES_DB'

chsql: ## Open a clickhouse-client shell
	$(COMPOSE) exec clickhouse sh -c 'clickhouse-client --user $$CLICKHOUSE_USER --password $$CLICKHOUSE_PASSWORD -d $$CLICKHOUSE_DB'

# --- Pipeline ----------------------------------------------------------------
.PHONY: pipeline pipeline-demo pipeline-stop dlq-peek alerts-tail signals-tail plan reconcile backtest \
	calibrate radar-backtest ml-data ml-train sim-bench
pipeline: env ## Simulator + normalizer (run `make seed` first with the same VEHICLE_COUNT)
	$(COMPOSE) --profile pipeline up -d --build simulator normalizer detector sink planner radar

pipeline-demo: env ## As `pipeline`, plus 5 scripted failures and a firmware defect for the radar
	SIM_SCENARIO=demo,firmware_defect RADAR_WINDOW_SECONDS=300 $(COMPOSE) --profile pipeline up -d --build simulator normalizer detector sink planner radar

pipeline-stop: ## Stop the pipeline services gracefully (flush + commit)
	$(COMPOSE) --profile pipeline stop simulator normalizer detector sink planner radar

alerts-tail: ## Follow alerts as they are raised
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 \
	  --topic alerts | cut -c1-300

signals-tail: ## Follow emerging-fault signals from the radar
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 \
	  --topic fleet.signals --from-beginning | cut -c1-400

plan: ## Run one planner cycle now and print the proposals
	$(COMPOSE) --profile pipeline run --rm planner --once --output /dev/stdout

reconcile: ## Prove no data loss: Kafka vs ClickHouse vs PostgreSQL (after `make pipeline`)
	set -a; . ./.env; set +a; uv run python scripts/reconcile.py --output evidence/benchmarks/m6-reconciliation.json

backtest: ## Detection back-test against simulator ground truth (~10 min)
	uv run python -m prognos_stream.evaluate --vehicles 300 --hours 8 --fault-rate 0.2 \
	  --time-scale 48 --output evidence/benchmarks/m5-detection-backtest.json

calibrate: ## Rule calibration: fit (seed 42) + held-out check (seed 7), then ship rules-v1.json (~20 min)
	uv run python -m prognos_stream.evaluate --vehicles 300 --hours 8 --fault-rate 0.2 \
	  --time-scale 48 --seed 42 --output evidence/benchmarks/m7-calibration-fit-seed42.json
	uv run python -m prognos_stream.evaluate --vehicles 300 --hours 8 --fault-rate 0.2 \
	  --time-scale 48 --seed 7 --output evidence/benchmarks/m7-calibration-holdout-seed7.json
	uv run python scripts/build_calibration.py evidence/benchmarks/m7-calibration-fit-seed42.json \
	  apps/stream-processor/src/prognos_stream/calibration/rules-v1.json v1 \
	  --holdout evidence/benchmarks/m7-calibration-holdout-seed7.json

radar-backtest: ## Radar: firmware-defect run vs control run on 3,000 vehicles (~3 min)
	uv run python -m prognos_stream.radar_eval --vehicles 3000 --hours 3 \
	  --output evidence/benchmarks/m7-radar-backtest.json

ml-data: ## M8 datasets: 2 training runs + held-out and shifted test runs (~25 min, parallel)
	for spec in "train_a 42 48 0.2" "train_b 43 48 0.2" "test_heldout 7 48 0.2" \
	            "test_slow 11 32 0.2" "test_rare 13 48 0.05"; do \
	  set -- $$spec; uv run prognos-ml generate --name $$1 --seed $$2 --time-scale $$3 \
	    --fault-rate $$4 > /dev/null & \
	done; wait

ml-train: ## M8: train LightGBM, compare with the calibrated rules on held-out runs, save the model
	uv run prognos-ml run --train train_a,train_b --test test_heldout,test_slow,test_rare \
	  --model-dir ml/models/failure-7d-v1 --output evidence/benchmarks/m8-model-vs-baseline.json

dlq-peek: ## Show the 5 most recent DLQ records with their reasons
	$(COMPOSE) exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:19092 \
	  --topic telemetry.dlq --from-beginning --max-messages 5 --property print.headers=true \
	  --timeout-ms 10000 2>/dev/null | cut -c1-400

sim-bench: ## Standalone generation benchmark at 100K vehicles (no Kafka)
	uv run python -m prognos_sim.bench --vehicles 100000 --seconds 20 --workers 1,2,4

# --- Quality -----------------------------------------------------------------
.PHONY: lint fmt typecheck test test-integration check
lint: ## Lint Python code
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format Python code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type check
	uv run mypy tests packages/common/src database/postgres/seeds apps/simulator/src apps/stream-processor/src scripts ml/src ml/tests

test: ## Unit tests (fast, no Docker)
	uv run pytest tests/unit tests/contract packages apps/simulator/tests apps/stream-processor/tests ml/tests --cov --cov-report=term

test-integration: ## Integration tests against real Postgres/ClickHouse (needs Docker)
	uv run pytest tests/integration

check: lint typecheck test config ## Everything CI runs locally (except integration)
