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

Uploaded scripts are untrusted until the upload pipeline validates them. The pipeline may use an LLM
analyzer to propose how each script should be invoked and smoke-tested, but that proposal only
becomes trusted after backend validation, Cube Sandbox execution, and human review. A script that
passes those gates can be executed later by path as an approved skill script. The model should call
the backend with `skill_name`, `script_path`, and JSON arguments; it should not copy script source
into a free-form code execution tool.

Backend-owned read-only capabilities may auto-execute. Every invocation of an approved skill
script requires per-run human approval before the sandbox starts. Model-generated Python, shell,
SQL, SSH commands, wrappers, or script bodies are not pre-approved. They are rejected unless
implemented as a reviewed skill script or backend-owned capability, even when a static scanner says
they look safe. Infrastructure credentials, connector access, risk routing, and human approval stay
under backend control.

Backend-owned capabilities are fixed runners/templates with JSON arguments, for example
`get_site_alarm_summary`, `get_site_kpi_snapshot`, `get_site_inventory`,
`get_node_health_snapshot`, `ping_node`, and `restart_service`. Free-form `query_*` and
`run_ssh_command` calls are intentionally not exposed; model-generated SQL, shell, SSH, Python, or
wrapper payloads are rejected unless they are implemented as a reviewed skill script or a
backend-owned capability.

For SSH, `node_name` should normally be a resolvable host. If operators use logical node names,
set `SSH_NODE_HOST_MAP` as comma-separated `node=host` pairs, for example
`site-a=10.0.0.11,site-b=node-b.internal`. `restart_service` is exposed only when
`SSH_RESTART_ALLOWED_SERVICES` contains at least one safe service/unit name such as `nginx` or
`node-exporter.service`, and it always suspends for human approval before execution.

## Chat Stream

Chat uses `POST /api/v1/chat/stream` so prompt text is not placed in the URL:

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "user_message": "Check current alarms for site-a",
  "provider": "openai",
  "model": "gpt-4o",
  "skill_mode": "specific",
  "skill_name": "check-kpis"
}
```

The chat model picker reads `GET /api/v1/chat/options` and exposes the configured OpenAI and
Claude adapters. Use `skill_mode: "auto"` without `skill_name` to let the agent choose a ready
skill.

## Ngrok Deploy

ngrok free accounts include one automatically assigned Dev Domain. Use that stable domain with the
local edge proxy so the browser sees a single public origin for both the frontend and `/api/v1`.

Start the app stack in one-origin mode, then expose the edge port:

```bash
NEXT_PUBLIC_API_BASE_URL=/api/v1 \
PUBLIC_URL=https://karlene-thermostable-tabatha.ngrok-free.dev \
make up
```

If your ngrok Cloud Endpoint traffic policy uses `forward-internal` to `https://default.internal`,
run the agent against that internal URL:

```bash
ngrok http 8080 --url https://default.internal
```

The public URL remains the Cloud Endpoint URL shown in the ngrok dashboard, for example
`https://karlene-thermostable-tabatha.ngrok-free.dev`.

By default, the ngrok agent is managed outside the application stack and must already be running on
the deploy host, typically as a systemd service or a long-running container. For a Docker agent
while the edge remains loopback-only, use host networking:

```bash
docker run -d \
  --name telecom_agent_ngrok \
  --restart unless-stopped \
  --network host \
  -v "$HOME/.config/ngrok/ngrok.yml:/etc/ngrok.yml:ro" \
  ngrok/ngrok:latest \
  http http://127.0.0.1:8080 \
  --url https://default.internal \
  --config /etc/ngrok.yml \
  --log stdout
```

Do not use Docker's default bridge with `host.docker.internal:8080` while the edge port is bound to
`127.0.0.1`; the bridge gateway cannot reach that loopback-only listener.

Alternatively, set `NGROK_MANAGED=true` and optionally `NGROK_CONFIG_PATH`,
`NGROK_INTERNAL_URL`, and `NGROK_IMAGE` in the deploy host's `.env`. The `Deploy` workflow then
recreates the agent with the host-network configuration above. When `public_url` is supplied, the
workflow polls that URL and fails the deployment if the agent is not forwarding to the edge.

When dispatching the `Deploy` workflow, set:

```bash
public_url=https://karlene-thermostable-tabatha.ngrok-free.dev
```

The workflow writes:

```bash
NEXT_PUBLIC_API_BASE_URL=/api/v1
CORS_ORIGINS=<public_url>,http://localhost:8080,http://127.0.0.1:8080,...
```

The `edge` service routes `/api/v1/*` to the FastAPI backend with buffering disabled, and routes
everything else to the Next.js frontend. That keeps SSE streaming on one stable URL without needing
two ngrok domains.

The current application has no authentication. A public ngrok URL is suitable only for a trusted
development/demo audience until authentication and authorization are implemented.

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
