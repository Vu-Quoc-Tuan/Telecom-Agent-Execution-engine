SHELL := /bin/bash

COMPOSE ?= docker compose
BACKEND_DIR := backend
FRONTEND_DIR := frontend
PROMPTFOO_ENV := PROMPTFOO_CONFIG_DIR=/tmp/telecom-promptfoo \
	PROMPTFOO_DISABLE_WAL_MODE=true \
	PROMPTFOO_DISABLE_TELEMETRY=true \
	PROMPTFOO_CACHE_ENABLED=false

.DEFAULT_GOAL := help

.PHONY: help setup setup-backend setup-frontend \
	up down restart build rebuild logs ps \
	frontend-shell db-shell migrate init-db \
	dev-backend dev-frontend test test-backend test-evals test-frontend \
	lint lint-backend lint-frontend format eval eval-online redteam \
	clean

help:
	@printf "Telecom Agent make targets\n\n"
	@printf "Docker:\n"
	@printf "  make up              Build and start postgres + frontend (backend: make dev-backend)\n"
	@printf "  make down            Stop compose services\n"
	@printf "  make restart         Restart compose services\n"
	@printf "  make build           Build compose images\n"
	@printf "  make rebuild         Rebuild images without cache and start\n"
	@printf "  make logs            Follow compose logs\n"
	@printf "  make ps              Show compose service status\n\n"
	@printf "Database / shells:\n"
	@printf "  make migrate         Run alembic upgrade head against the configured DB\n"
	@printf "  make frontend-shell  Open shell in frontend container\n"
	@printf "  make db-shell        Open psql in postgres container\n\n"
	@printf "Local dev:\n"
	@printf "  make setup           Install backend and frontend deps\n"
	@printf "  make dev-backend     Run backend locally with uvicorn\n"
	@printf "  make dev-frontend    Run frontend locally\n\n"
	@printf "Quality / eval:\n"
	@printf "  make test            Backend + eval unit tests + frontend lint/build\n"
	@printf "  make lint            Backend and frontend lint\n"
	@printf "  make format          Backend ruff format\n"
	@printf "  make eval            Promptfoo eval\n"
	@printf "  make eval-online     Online promptfoo eval; needs EVAL_DATASET_URL\n"
	@printf "  make redteam         Manual promptfoo redteam; needs REDTEAM_CONFIG\n"

setup: setup-backend setup-frontend

setup-backend:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv sync

setup-frontend:
	cd $(FRONTEND_DIR) && npm ci

up:
	@bport=$${BACKEND_PORT:-8000}; \
	while (exec 3<>/dev/tcp/127.0.0.1/$$bport) 2>/dev/null; do \
		echo "Backend port $$bport in use, trying $$((bport+1))"; bport=$$((bport+1)); \
	done; \
	fport=$${FRONTEND_PORT:-3000}; \
	while (exec 3<>/dev/tcp/127.0.0.1/$$fport) 2>/dev/null; do \
		echo "Frontend port $$fport in use, trying $$((fport+1))"; fport=$$((fport+1)); \
	done; \
	echo "Starting postgres + frontend (backend tren host: make dev-backend)"; \
	echo "BACKEND_PORT=$$bport FRONTEND_PORT=$$fport"; \
	BACKEND_PORT=$$bport FRONTEND_PORT=$$fport \
	NEXT_PUBLIC_API_BASE_URL=$${NEXT_PUBLIC_API_BASE_URL:-http://127.0.0.1:$$bport/api/v1} \
	$(COMPOSE) up --build -d postgres frontend

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

build:
	$(COMPOSE) build

rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d postgres frontend

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

migrate init-db:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head

frontend-shell:
	$(COMPOSE) exec frontend sh

db-shell:
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-postgres} -d $${POSTGRES_DB:-telecom_agent}

dev-backend:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev -- --hostname 127.0.0.1 --port $${FRONTEND_PORT:-3000}

test: test-backend test-evals test-frontend

test-backend:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q

test-evals:
	cd $(BACKEND_DIR) && PYTHONPATH=.. UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q ../evals

test-frontend:
	cd $(FRONTEND_DIR) && npm run lint
	cd $(FRONTEND_DIR) && npx tsc --noEmit
	cd $(FRONTEND_DIR) && npm run build

lint: lint-backend lint-frontend

lint-backend:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests scripts

lint-frontend:
	cd $(FRONTEND_DIR) && npm run lint

format:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run ruff format app tests scripts

eval:
	cd evals && $(PROMPTFOO_ENV) npx --yes promptfoo@latest eval -c promptfoo.yaml --no-progress-bar

eval-online:
	cd evals && $(PROMPTFOO_ENV) npx --yes promptfoo@latest eval -c promptfoo.online.yaml --no-progress-bar

redteam:
	@test -n "$${REDTEAM_CONFIG}" || (echo "Set REDTEAM_CONFIG=evals/redteam.yaml or another config path" >&2; exit 1)
	$(PROMPTFOO_ENV) npx --yes promptfoo@latest redteam run -c "$${REDTEAM_CONFIG}" -j "$${REDTEAM_CONCURRENCY:-2}" -o redteam-generated.yaml --no-progress-bar

clean:
	$(COMPOSE) down -v