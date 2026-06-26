# Telecom Agent Execution Engine

FastAPI and LangGraph backend for telecom operations with dynamic skills, human approval,
PostgreSQL persistence, SSE events, and offline policy evaluations.

Authentication is intentionally not enabled in the current development phase. Do not expose
the API to an untrusted network until an authentication and authorization layer is added.

## Local Setup

```bash
cp .env.example .env
make setup
docker compose up -d postgres
make migrate
```

Run the backend:

```bash
cd backend
.venv/bin/uvicorn app.main:app --reload
```

The health endpoint is available at `http://localhost:8000/health`.

## Verification

```bash
make test
make lint
make eval
```

`make eval` runs six local Promptfoo scenarios against backend policy code. It does not require
OpenAI, Anthropic, or any other external account or API key.

## Agent Skills

Skills follow the [Agent Skills specification](https://agentskills.io/specification). Upload a ZIP
containing one skill folder with a required `SKILL.md` and optional `scripts/`, `references/`, and
`assets/` directories:

```bash
curl -X POST http://localhost:8000/api/v1/skills/upload \
  -F 'file=@check-kpis.zip;type=application/zip'
```

Uploaded scripts are validated and stored as skill resources; they are not executed directly.
Infrastructure access remains in the fixed SSH, ClickHouse, and PostgreSQL tools so credentials,
risk routing, and human approval stay under backend control.

For SSH, `node_name` should normally be a resolvable host. If operators use logical node names,
set `SSH_NODE_HOST_MAP` as comma-separated `node=host` pairs, for example
`site-a=10.0.0.11,site-b=node-b.internal`.

## Chat Stream

Chat uses `POST /api/v1/chat/stream` so prompt text is not placed in the URL:

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "user_message": "Check current alarms for site-a",
  "provider": "openai",
  "model": "gpt-4o"
}
```

## Run Operations

Cancel an active run:

```bash
curl -X POST http://localhost:8000/api/v1/runs/{run_id}/cancel \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Operator stopped this run."}'
```

Mark stale active runs as timed out. The default threshold is `RUN_TIMEOUT_SECONDS`:

```bash
curl -X POST http://localhost:8000/api/v1/runs/mark-timeouts \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds":3600,"limit":100}'
```

The backend also starts an internal timeout sweeper during FastAPI lifespan when
`RUN_TIMEOUT_SWEEPER_ENABLED=true`. It runs every `RUN_TIMEOUT_SWEEPER_INTERVAL_SECONDS`
seconds and marks up to `RUN_TIMEOUT_SWEEPER_LIMIT` stale active runs as `timed_out`.
