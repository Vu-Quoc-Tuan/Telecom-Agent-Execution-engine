# Telecom Agent Execution Engine: System Architecture & LLM Provider Configuration Guide

This document provides a comprehensive guide to the system architecture, security controls, and LLM provider configurations for the Telecom Agent Execution Engine.

---

## 1. System Architecture

The Telecom Agent Execution Engine is built using a **Zero-Trust layered architecture** designed to execute untrusted operator-uploaded scripts and interact safely with physical telecom nodes.

### 1.1 Layered Design

```mermaid
flowchart TD
    %% Clients
    A["Operator UI (Next.js Frontend)"] <-->|REST API / SSE Streams| B["FastAPI API Gateway"]

    %% Core Services
    subgraph Backend Engine (FastAPI)
        B <--> C["Agent Services & Run Coordinator"]
        C <--> D["LangGraph Orchestrator (StateGraph)"]
        D <--> E["LLM Gateway (Multi-Provider Adapters)"]
        D <--> F["Agent Skills Catalog & Registry"]
    end

    %% Security & Sandbox
    subgraph Security Layer
        G["Upload Validation (AST + Domain Judge)"]
        H["Backend-owned Capabilities + Safety Guard"]
        I["Docker Sandbox (read-only, network=none by default)"]
    end

    %% External Infrastructure
    subgraph External Infrastructure
        J["ClickHouse DB (KPIs & Alarms)"]
        K["PostgreSQL (Inventory DB)"]
        L["Remote Nodes (SSH Server)"]
    end

    %% Telemetry & Storage
    M[(App PostgreSQL & LangGraph Checkpointer)] <--> D & C
    N["Langfuse (Telemetry & Prompt Management)"] <--> E & D

    %% Connections
    F --> G
    D --> I
    D --> H
    H --> J & K & L
```

### 1.2 Component Breakdown

1. **API Gateway (FastAPI):** Exposes RESTful endpoints for session management, run control, skill registry upload/review, and streams real-time step progress using Server-Sent Events (SSE).
2. **LangGraph Orchestrator:** Coordinates the agent's reasoning-acting loop using a stateful cyclic graph (`StateGraph`). It uses a PostgreSQL checkpointer to persist execution states, allowing runs to be suspended for human approval and resumed seamlessly.
3. **LLM Gateway:** Decouples model calls from provider SDK details through `LLMGateway`,
   `BaseLLMAdapter`, and the shared `LangChainChatAdapter`. OpenAI-compatible and Anthropic
   configurations use the same normalized request/response contract.
4. **Agent Skills Registry:** Manages user-uploaded skills packages structured according to the `agentskills.io` specification.
5. **Security & Sandbox Layer:** Implements strict containment and validation rules:
   - **Static AST Scan (upload-time):** Evaluates uploaded Python scripts to block unauthorized imports and system calls.
   - **Docker Sandbox Executor:** Launches short-lived, read-only, CPU/memory-constrained containers. Network access is denied by default and must be enabled explicitly for reviewed runtime skills.
   - **Capability-first access + Safety Guard:** The model only sees backend-owned tools with fixed templates/allowlists. Connectors still enforce read-only SQL settings, row limits, and SSH command validation for the fixed command sets they run.
6. **Telemetry & Observability (Langfuse):** Logs execution traces, session runs, model invocation latency, input/output tokens, and hosts prompt templates versioned under the `production` tag.

---

## 2. LLM Provider Configuration Guide

The engine supports dynamic routing between different LLM providers using the **Gateway Adapter Pattern**. All configurations are loaded from environment variables in the `.env` file.

### 2.1 LLM Gateway Configuration Parameters

| Environment Variable | Allowed Values | Description |
|----------------------|----------------|-------------|
| `PROVIDER` | `openai`, `anthropic` | The active LLM provider adapter. |
| `OPENAI_API_KEY` | String (`sk-proj-...`) | Required if `PROVIDER=openai`. |
| `OPENAI_API_URL` | URL | OpenAI endpoint or an OpenAI-compatible router ending in `/v1`. |
| `OPENAI_MODEL_NAME` | String (`gpt-4o`, `gpt-4-turbo`) | OpenAI model identifier. |
| `ANTHROPIC_API_KEY` | String (`sk-ant-api03-...`) | Required if `PROVIDER=anthropic`. |
| `ANTHROPIC_API_URL` | URL | Anthropic-compatible endpoint. |
| `ANTHROPIC_MODEL_NAME` | String (`claude-3-5-sonnet-20241022`) | Anthropic Claude model identifier. |
| `LLM_TIMEOUT_SECONDS` | Positive number | Timeout applied to each provider request. |
| `LLM_MAX_RETRIES` | Non-negative integer | Provider-client retries after the initial attempt. |
| `LLM_MAX_TOKENS` | Positive integer | Default max tokens for provider responses (default `4096`). |
| `OPENAI_SUPPORTS_TOOL_STRICT` | `true` / `false` / empty | Override strict tool-schema support for OpenAI-compatible routers. |

### 2.2 Adapter Implementation Details

- **Shared adapter (`LangChainChatAdapter`):** Wraps `ChatOpenAI` and `ChatAnthropic`, normalizes
  messages, tool calls, usage, finish reasons, errors, and stream chunks into the contracts in
  `backend/app/llm/schemas.py`.
- **OpenAI-compatible mode:** Supports the official OpenAI API and custom routers through
  `OPENAI_API_URL`. Strict tool schemas default to enabled only for the official OpenAI endpoint;
  set `OPENAI_SUPPORTS_TOOL_STRICT` explicitly when a custom router supports them.
- **Anthropic mode:** Uses `ChatAnthropic` with the configured base URL and normalized tool
  definitions.
- **Gateway fallback:** `LLMGateway` can try configured fallback providers before any streaming
  chunk is emitted. It does not switch providers after partial text because that would duplicate or
  corrupt the response.

Settings are loaded when the backend process starts, and `get_llm_gateway()` is cached. Changing a
model, URL, key, timeout, or retry count therefore requires restarting the backend.

### 2.3 Context Window Compaction Settings

To prevent token exhaustion during multi-turn diagnostic tasks, the engine enforces automatic context window compaction. Settings live in `backend/app/config.py` (env names uppercase) and are passed into the agent run config with the lowercase keys below:

| Env / Settings field | Run-config key | Default | Description |
|---|---|---:|---|
| `CONTEXT_WINDOW_TOKENS` | `context_window_tokens` | `200000` | Maximum estimated context limit. |
| `CONTEXT_COMPACTION_TRIGGER_RATIO` | `context_compaction_trigger_ratio` | `0.65` | Compaction starts when estimated tokens exceed $65\%$ of the limit. |
| `CONTEXT_COMPACTION_TARGET_RATIO` | `context_compaction_target_ratio` | `0.45` | Compaction trims history down toward $45\%$ of the limit. |

When triggered, the engine keeps recent turns and valid tool-call pairs, then asks the configured LLM to summarize the older prefix using the `telecom-context-compactor` prompt managed in Langfuse. The returned summary is inserted as a single system message (`[AUTO-COMPACTED CONTEXT]`).

---

## 3. Security Architecture & Sandboxing

### 3.1 6-Stage Upload Validation Pipeline

Every skill ZIP uploaded is verified against the automated pipeline logged as **VONG 1–6** in `SkillValidationService`:

1. **VONG 1 — Package parse + static security scan:** Verifies archive integrity, path safety (no path traversal, no backslashes, no symlinks/encrypted entries), size limits (archive $\le 10$ MB, single file $\le 5$ MB, total uncompressed $\le 25$ MB, $\le 200$ files), and a valid `SKILL.md`. Then runs:
   - **AST scan** (`AdvancedASTSecurityAnalyzer`) on bundled `.py` files:
     - Blocks process, reflection, background-execution, native-memory, and dynamic-import primitives such as `subprocess`, `sys`, `importlib`, and `ctypes`.
     - Allows connection clients such as `requests`, `httpx`, and `paramiko` at import time; runtime network access is still denied unless sandbox network is explicitly enabled.
     - Blocks execution helpers (`eval`, `exec`, `getattr`, `__import__`) and shell/process calls such as `os.system`.
     - Blocks private-attribute (dunder) references and sensitive path literals (`/etc/passwd`, `.env`, `id_rsa`).
   - **Secret scan** on text sources using `AgentSafetyGuard.PII_AND_SECRET_PATTERNS`.
2. **VONG 2 — Taxonomy validation:** Scores telecom keyword occurrences in the skill definition to ensure domain relevance.
3. **VONG 3 — LLM domain judge:** An LLM-assisted judge audits skill **name, description, and body**. Skills with `taxonomy_score < 0.25` **and** `llm_score < 0.5` are rejected. If the judge is unavailable, `llm_score` is treated as `0.0` (taxonomy alone can still pass when $\ge 0.25$).
4. **VONG 4 — Script run-spec proposal:** LLM-assisted preparation of per-script run specs (parameters schema, smoke-test arguments, limits, output contract) for Python scripts in the package.
5. **VONG 5 — Docker smoke test:** When the Docker sandbox executor is available, each script is run with proposed smoke-test arguments in a network-less, resource-constrained container. Exit code must be `0` and stdout must satisfy the output contract. **If the sandbox is unavailable** (for example `SANDBOX_ENABLED=false` or Docker missing), scripts are auto-marked as passed for upload review only — they still need human approval, and `run_skill_script` remains unavailable at runtime without a working sandbox.
6. **VONG 6 — Human-in-the-loop review:** The skill is staged as `testing` (`PENDING_REVIEW`). An operator must approve it via the admin API/UI to switch status to `ready`.

### 3.2 3-Stage Runtime Gates

When the agent invokes an approved skill script using `run_skill_script`, three checkpoints are executed at runtime:

```text
[Agent Invokes run_skill_script]
               |
               v
  [Gate 1: SHA256 Hash Verification]  ---> Mismatch? ---> [Abort & Raise Error]
               | (Matches approved catalog)
               v
  [Gate 2: Input JSON Schema Validation]  ---> Invalid? ---> [Abort & Raise Error]
               | (Matches parameters schema)
               v
  [Per-run Human Approval]
               |
               v
  [Docker Sandbox Execution (Isolated)]
               |
               v
  [Gate 3: Output Contract Validation] ---> Mismatch? ---> [Abort & Raise Error]
               | (Matches return schema)
               v
  [Result Returned to Agent]
```

### 3.3 Safe Database & SSH Access Controls

Direct infrastructure access is exposed through backend-owned capabilities rather than free-form
SQL or shell tools:

- **ClickHouse / PostgreSQL:** The model selects fixed capabilities with validated JSON arguments.
  Backend-owned runners choose the SQL/template. PostgreSQL read-only operations also set the
  transaction read-only, while ClickHouse read paths use readonly server settings and row limits.
- **SSH:** The model selects fixed capabilities such as health snapshots, ping, or an allowlisted
  service restart. Node names are resolved by backend configuration. Read-only operations may
  auto-execute; state-changing operations require approval. Arbitrary model-generated shell/SSH
  commands are not exposed as tools.
- **Approved skill scripts:** `run_skill_script` verifies the approved script hash, validates input,
  requires per-run approval, executes in the Docker sandbox, and validates the output contract.

---

## 4. Observability & Telemetry

Observability is handled via the **Langfuse SDK**, pushing traces asynchronously to avoid API call blocking:

- **Trace Routing:** Every execution run starts a trace identified by `run_id` and grouped under `session_id`.
- **Two DLP layers** (different mask formats — do not conflate them):
  - **Input sanitization** (`AgentSafetyGuard.sanitize_input_prompt`): redacts secrets in user prompts before they enter the agent/LLM path, using placeholders such as `[[MASKED_SECRET]]` and `[[MASKED_PRIVATE_KEY]]`.
  - **Trace/log redaction** (`DataRedactor`): scrubs API keys, passwords, and private keys from Langfuse/trace payloads, replacing them with `[REDACTED]` / `[REDACTED PRIVATE KEY]` (and sensitive dict keys with `[REDACTED]`).
- **Prompt Registry:** Prompt templates are fetched dynamically from the Langfuse registry using the `production` label (`LANGFUSE_PROMPT_LABEL`), enabling safe, live updates to safety prompts without code modification.

## 5. CI and Evaluation Workflows

```text
Pull request / push main
└── ci.yml
    ├── backend job
    │   ├── ruff check
    │   ├── alembic upgrade head (test Postgres)
    │   ├── pytest backend
    │   ├── pytest evals (test_provider.py, test_assertions.py)
    │   └── Promptfoo offline (promptfoo.yaml + scenarios.yaml)
    └── frontend job
        ├── npm test
        ├── npm run lint
        ├── tsc --noEmit
        └── npm run build

Manual only (workflow_dispatch)
└── online-eval.yml
    ├── requires EVAL_DATASET_URL (input or secret)
    └── promptfoo.online.yaml

Manual only (workflow_dispatch)
└── redteam.yml
    ├── start PostgreSQL + backend
    └── redteam.yaml
        ├── OpenAI generates adversarial prompts
        └── backend_http.py → live Agent backend
```

Offline Promptfoo (`make eval` / `evals/scenarios.yaml`) currently runs **8** local policy scenarios covering domain taxonomy, capability routing, removed free-form tools, SSH safety, and DLP. It exercises backend policy code and does not require a live LLM provider.
