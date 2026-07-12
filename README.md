# Telecom Agent Execution Engine

FastAPI and LangGraph backend for telecom operations with dynamic skills, human approval,
PostgreSQL persistence, SSE events, and offline policy evaluations.

## Documentation

- [Hướng dẫn cài đặt, cấu hình và vận hành](docs/running-guide.md)
- [Kiến trúc hệ thống và LLM gateway](docs/architecture.md)

## Local Setup

1. Copy and configure environment files:
```bash
cp .env.example .env
# Optional: connector overrides (ClickHouse / external Postgres / SSH)
cp .env.external.example .env.external
```

2. Install dependencies and run database migrations:
```bash
make setup
docker compose up -d postgres
make migrate
```

You can start the entire stack together using `make up`, or run the services separately:

```bash
# Run separately:
make dev-backend
make dev-frontend
```

Alternatively, running `make up` starts PostgreSQL, frontend, and edge proxy in Docker, and launches the reloadable backend on the host (so approved skill scripts can use the host Docker daemon for the sandbox).

By default, the backend runs on `http://localhost:8000`, the frontend on `http://localhost:3000/chat`, and the edge proxy on `http://localhost:8080/chat`.

> [!NOTE]
> If any of these default ports are already occupied, the startup script will automatically scan and bind to the next available ports. Always check the console output of `make up` to see the exact ports assigned to the running services.

## Verification

```bash
make test
make lint
make eval
```

`make eval` runs eight local Promptfoo scenarios in `evals/scenarios.yaml` against backend policy
code. It does not require OpenAI, Anthropic, or any other external account or API key.

## Agent Skills

Skills follow the [Agent Skills specification](https://agentskills.io/specification). Upload a ZIP
containing one skill folder with a required `SKILL.md` and optional `scripts/`, `references/`, and
`assets/`.

- **Sandbox Validation:** Uploaded skills are untrusted until they pass validation, Docker sandbox execution (when available), and manual review.
- **Strict Execution:** Free-form code execution (SSH, shell, Python, SQL) is disabled. The model must call approved skill scripts or backend-owned capabilities with structured JSON arguments.
- **Human-in-the-Loop:** All skill script executions and safety-critical operations (e.g., `restart_service`) suspend and require per-run manual approval.
- **SSH & Service Controls:** Remote nodes can be mapped logically (`SSH_NODE_HOST_MAP`). Service restarts are restricted to an explicit whitelist (`SSH_RESTART_ALLOWED_SERVICES`).

## Built-in Capabilities

The backend exposes several pre-built tools (Capabilities) that the LLM agent can invoke directly:

### 1. Monitoring & Alarms (ClickHouse)
- `get_site_alarm_summary`: Summarize alarm counts by severity level for a site.
- `get_active_alarms`: List recent active/unresolved alarms with optional severity filtering.
- `get_site_kpi_snapshot`: Fetch the most recent KPI values for a site.

### 2. Inventory (PostgreSQL)
- `get_site_inventory`: Retrieve hardware configuration and inventory data for a site.

### 3. Diagnostics & Control (SSH)
- `get_node_health_snapshot`: Run a fixed set of read-only diagnostic command templates on a remote node.
- `ping_node`: Perform ICMP ping to measure latency and connectivity.
- `restart_service`: Safely restart allowed systemd services (requires manual operator approval).

### 4. Dynamic Skills
- `load_skill`: Load approved skill metadata.
- `read_skill_file`: View contents of skill source files.
- `run_skill_script`: Run verified/approved skill python scripts inside the Docker sandbox.

## Chat Stream

Chat uses `POST /api/v1/chat/stream` so prompt text is not placed in the URL:

```json
{
  "session_id": "00000000-0000-0000-0000-000000000000",
  "user_message": "Check current alarms for site-a",
  "provider": "openai",
  "model": "gpt-4o",
  "skill_mode": "specific",
  "skill_name": "noc-alarm-enrichment"
}
```

`skill_name` is only an example of a ready catalog skill; use a name that exists in your registry.
The chat model picker reads `GET /api/v1/chat/options` and exposes the configured OpenAI and
Claude adapters. Use `skill_mode: "auto"` without `skill_name` to let the agent choose a ready
skill.

## Ngrok Deploy

Expose the local one-origin edge proxy (port 8080) to the public web via ngrok:

1. **Start the app stack in one-origin mode:**
```bash
NEXT_PUBLIC_API_BASE_URL=/api/v1 \
PUBLIC_URL=https://<your-ngrok-domain>.ngrok-free.dev \
make up
```

2. **Expose the edge proxy:**
```bash
# Direct exposure:
ngrok http 8080 --url https://<your-ngrok-domain>.ngrok-free.dev

# Or if using a forward-internal ngrok traffic policy:
ngrok http 8080 --url https://default.internal
```

3. **Deploy as a background Docker container (Optional):**
```bash
docker run -d --name telecom_agent_ngrok --restart unless-stopped --network host \
  -v "$HOME/.config/ngrok/ngrok.yml:/etc/ngrok.yml:ro" \
  ngrok/ngrok:latest http http://127.0.0.1:8080 --url https://default.internal --config /etc/ngrok.yml
```

## Run Operations

- **Cancel an active run:**
```bash
curl -X POST http://localhost:8000/api/v1/runs/{run_id}/cancel \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Operator stopped this run."}'
```

- **Clean up stale runs (timeouts):**
```bash
curl -X POST http://localhost:8000/api/v1/runs/mark-timeouts \
  -H 'Content-Type: application/json' \
  -d '{"timeout_seconds":3600,"limit":100}'
```
*(Note: If `RUN_TIMEOUT_SWEEPER_ENABLED=true` is set, the backend sweeps for timeouts automatically).*
