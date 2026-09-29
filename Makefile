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
.PHONY: config up up-obs up-ha down clean ps logs topics
config: env ## Validate docker-compose files
	$(COMPOSE) config --quiet && echo "docker-compose.yml: OK"
	$(COMPOSE_HA) config --quiet && echo "kafka HA overlay: OK"

up: env ## Start the core data plane (Kafka, Postgres, ClickHouse, Redis)
	$(COMPOSE) up -d --wait

up-obs: env ## Start core + Prometheus/Grafana
	$(COMPOSE) --profile observability up -d --wait

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

# --- Quality -----------------------------------------------------------------
.PHONY: lint fmt typecheck test check
lint: ## Lint Python code
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format Python code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Static type check
	uv run mypy tests

test: ## Run unit tests
	uv run pytest tests/unit

check: lint typecheck test config ## Everything CI runs locally
