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
	$(COMPOSE) --profile observability down --remove-orphans

clean: ## Stop the stack AND delete all data volumes
	$(COMPOSE_HA) --profile observability down -v --remove-orphans

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

# --- Simulator ---------------------------------------------------------------
.PHONY: sim sim-demo sim-stop sim-bench
sim: env ## Stream simulated telemetry into Kafka (VEHICLE_COUNT / EVENTS_PER_SECOND from .env)
	$(COMPOSE) --profile sim up -d --build simulator

sim-demo: env ## As `sim`, plus 5 scripted failures 10-18 minutes after start
	SIM_SCENARIO=demo $(COMPOSE) --profile sim up -d --build simulator

sim-stop: ## Stop the simulator gracefully (releases held-back events, flushes Kafka)
	$(COMPOSE) --profile sim stop simulator

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
	uv run mypy tests packages/common/src database/postgres/seeds apps/simulator/src

test: ## Unit tests (fast, no Docker)
	uv run pytest tests/unit packages apps/simulator/tests --cov --cov-report=term

test-integration: ## Integration tests against real Postgres/ClickHouse (needs Docker)
	uv run pytest tests/integration

check: lint typecheck test config ## Everything CI runs locally (except integration)
