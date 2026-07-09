SHELL := /bin/bash

COMPOSE ?= docker compose
BACKEND_DIR := backend
FRONTEND_DIR := frontend
RUN_DIR := $(CURDIR)/.run
BACKEND_PID_FILE := $(RUN_DIR)/backend.pid
BACKEND_PORT_FILE := $(RUN_DIR)/backend.port
BACKEND_LOG_FILE := $(RUN_DIR)/backend.log
PROMPTFOO_VERSION ?= 0.121.17
REDTEAM_ENV_FILE ?= .env
PROMPTFOO_ENV := PROMPTFOO_CONFIG_DIR=/tmp/telecom-promptfoo \
	PROMPTFOO_DISABLE_WAL_MODE=true \
	PROMPTFOO_DISABLE_TELEMETRY=true \
	PROMPTFOO_CACHE_ENABLED=false \
	npm_config_ignore_scripts=true

.DEFAULT_GOAL := help

.PHONY: help setup setup-backend setup-frontend \
	up down stop-backend restart build rebuild logs ps \
	frontend-shell db-shell migrate init-db \
	dev-backend dev-frontend test test-backend test-evals test-frontend \
	lint lint-backend lint-frontend format eval eval-online redteam \
	clean

help:
	@printf "Telecom Agent make targets\n\n"
	@printf "Docker:\n"
	@printf "  make up              Start DB + frontend + reloadable backend in background\n"
	@printf "  PUBLIC_URL=... make up  Start for one-origin ngrok edge testing\n"
	@printf "  make down            Stop backend, edge, frontend, and database\n"
	@printf "  make restart         Restart the full local stack\n"
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
	@printf "  make dev-backend     Run backend locally with uvicorn only\n"
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
	@set -euo pipefail; \
	mkdir -p "$(RUN_DIR)"; \
	if [[ -f "$(BACKEND_PID_FILE)" ]]; then \
		old_pid=$$(cat "$(BACKEND_PID_FILE)" 2>/dev/null || true); \
		if [[ -n "$$old_pid" ]] && kill -0 "$$old_pid" 2>/dev/null; then \
			cmdline=$$(tr '\0' ' ' < "/proc/$$old_pid/cmdline" 2>/dev/null || true); \
			if [[ "$$cmdline" == *"uvicorn app.main:app"* ]]; then \
				echo "Backend already running (PID $$old_pid). Run 'make down' first." >&2; \
				exit 1; \
			fi; \
		fi; \
		rm -f "$(BACKEND_PID_FILE)" "$(BACKEND_PORT_FILE)"; \
	fi; \
	bport=$${BACKEND_PORT:-8000}; \
	while (exec 3<>/dev/tcp/127.0.0.1/$$bport) 2>/dev/null; do \
		echo "Backend port $$bport in use, trying $$((bport+1))"; bport=$$((bport+1)); \
	done; \
	fport=$${FRONTEND_PORT:-3000}; \
	while (exec 3<>/dev/tcp/127.0.0.1/$$fport) 2>/dev/null; do \
		echo "Frontend port $$fport in use, trying $$((fport+1))"; fport=$$((fport+1)); \
	done; \
	echo "Starting postgres + frontend containers"; \
	echo "BACKEND_PORT=$$bport FRONTEND_PORT=$$fport"; \
	eport=$${EDGE_PORT:-8080}; \
	local_cors_origins="http://localhost:$$fport,http://127.0.0.1:$$fport,http://localhost:$$eport,http://127.0.0.1:$$eport"; \
	public_url=$${PUBLIC_URL:-}; \
	public_url=$${public_url%/}; \
	if [[ -n "$$public_url" ]]; then \
		local_cors_origins="$$public_url,$$local_cors_origins"; \
	fi; \
	if [[ -f .env ]]; then \
		env_cors=$$(grep -E '^CORS_ORIGINS=' .env | cut -d= -f2- || true); \
		if [[ -n "$$env_cors" ]]; then \
			local_cors_origins="$$local_cors_origins,$$env_cors"; \
		fi; \
	fi; \
	BACKEND_PORT=$$bport FRONTEND_PORT=$$fport \
	NEXT_PUBLIC_API_BASE_URL=$${NEXT_PUBLIC_API_BASE_URL:-http://127.0.0.1:$$bport/api/v1} \
	$(COMPOSE) up --build -d postgres frontend edge || exit 1; \
	echo "Starting reloadable backend on http://127.0.0.1:$$bport"; \
	cd "$(BACKEND_DIR)"; \
	if command -v uv >/dev/null 2>&1; then \
		backend_launcher=(uv run uvicorn app.main:app); \
	elif [[ -x ".venv/bin/python" ]]; then \
		backend_launcher=(.venv/bin/python -m uvicorn app.main:app); \
	else \
		echo "Neither 'uv' nor $(BACKEND_DIR)/.venv/bin/python is available. Run 'make setup-backend' first." >&2; \
		exit 1; \
	fi; \
	nohup setsid env UV_CACHE_DIR=/tmp/uv-cache \
		CORS_ORIGINS=$${CORS_ORIGINS:-$$local_cors_origins} \
		"$${backend_launcher[@]}" \
		--reload --host 0.0.0.0 --port $$bport \
		>"$(BACKEND_LOG_FILE)" 2>&1 < /dev/null & \
	backend_pid=$$!; \
	cd "$(CURDIR)"; \
	echo "$$backend_pid" > "$(BACKEND_PID_FILE)"; \
	echo "$$bport" > "$(BACKEND_PORT_FILE)"; \
	ready=0; \
	for _ in $$(seq 1 30); do \
		if curl -fsS "http://127.0.0.1:$$bport/health" >/dev/null 2>&1; then ready=1; break; fi; \
		if ! kill -0 "$$backend_pid" 2>/dev/null; then break; fi; \
		sleep 1; \
	done; \
	if [[ "$$ready" != "1" ]]; then \
		echo "Backend failed to become healthy. Last log lines:" >&2; \
		tail -n 40 "$(BACKEND_LOG_FILE)" >&2 || true; \
		kill -TERM -- "-$$backend_pid" 2>/dev/null || true; \
		rm -f "$(BACKEND_PID_FILE)" "$(BACKEND_PORT_FILE)"; \
		$(COMPOSE) down; \
		exit 1; \
	fi; \
	echo "Backend PID $$backend_pid; log: $(BACKEND_LOG_FILE)"

stop-backend:
	@set -u; \
	if [[ ! -f "$(BACKEND_PID_FILE)" ]]; then \
		echo "Backend PID file not found; backend already stopped or was started manually."; \
		exit 0; \
	fi; \
	pid=$$(cat "$(BACKEND_PID_FILE)" 2>/dev/null || true); \
	if [[ -z "$$pid" ]] || ! kill -0 "$$pid" 2>/dev/null; then \
		echo "Removing stale backend PID file."; \
		rm -f "$(BACKEND_PID_FILE)" "$(BACKEND_PORT_FILE)"; \
		exit 0; \
	fi; \
	cmdline=$$(tr '\0' ' ' < "/proc/$$pid/cmdline" 2>/dev/null || true); \
	if [[ "$$cmdline" != *"uvicorn app.main:app"* ]]; then \
		echo "PID $$pid is not this project's Uvicorn process; refusing to kill it." >&2; \
		rm -f "$(BACKEND_PID_FILE)" "$(BACKEND_PORT_FILE)"; \
		exit 0; \
	fi; \
	echo "Stopping backend process group $$pid"; \
	kill -TERM -- "-$$pid" 2>/dev/null || kill -TERM "$$pid" 2>/dev/null || true; \
	for _ in $$(seq 1 20); do \
		kill -0 "$$pid" 2>/dev/null || break; \
		sleep 0.25; \
	done; \
	if kill -0 "$$pid" 2>/dev/null; then \
		echo "Backend did not stop cleanly; forcing process group shutdown." >&2; \
		kill -KILL -- "-$$pid" 2>/dev/null || kill -KILL "$$pid" 2>/dev/null || true; \
	fi; \
	rm -f "$(BACKEND_PID_FILE)" "$(BACKEND_PORT_FILE)"

down: stop-backend
	$(COMPOSE) down

restart:
	$(MAKE) down
	$(MAKE) up

build:
	$(COMPOSE) build

rebuild:
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d postgres frontend edge

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
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run uvicorn app.main:app --reload --host 0.0.0.0 --port $${BACKEND_PORT:-8000}

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev -- --hostname 127.0.0.1 --port $${FRONTEND_PORT:-3000}

test: test-backend test-evals test-frontend

test-backend:
	cd $(BACKEND_DIR) && UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q

test-evals:
	cd $(BACKEND_DIR) && PYTHONPATH=.. UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q ../evals

test-frontend:
	cd $(FRONTEND_DIR) && npm test
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
	cd evals && $(PROMPTFOO_ENV) npx --yes promptfoo@$(PROMPTFOO_VERSION) eval -c promptfoo.yaml --no-progress-bar

eval-online:
	cd evals && $(PROMPTFOO_ENV) npx --yes promptfoo@$(PROMPTFOO_VERSION) eval -c promptfoo.online.yaml --no-progress-bar

redteam:
	@test -n "$${REDTEAM_CONFIG}" || (echo "Set REDTEAM_CONFIG=evals/redteam.yaml or another config path" >&2; exit 1)
	@env_args=(); \
	if [ -n "$(REDTEAM_ENV_FILE)" ] && [ -f "$(REDTEAM_ENV_FILE)" ]; then \
		env_args=(--env-file "$(REDTEAM_ENV_FILE)"); \
	fi; \
	$(PROMPTFOO_ENV) npx --yes promptfoo@$(PROMPTFOO_VERSION) redteam run -c "$${REDTEAM_CONFIG}" -j "$${REDTEAM_CONCURRENCY:-2}" -o redteam-generated.yaml --no-progress-bar "$${env_args[@]}"

clean: stop-backend
	$(COMPOSE) down -v
