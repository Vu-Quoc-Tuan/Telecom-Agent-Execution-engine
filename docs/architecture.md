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
        G["Advanced AST Security Analyzer"]
        H["Agent Safety Guard (SSH/SQL Filters)"]
        I["Docker Sandbox Executor (Isolated Run)"]
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
    I -->|No Network / Read-Only Workspace| G
    D --> H
    H --> J & K & L
```

### 1.2 Component Breakdown

1. **API Gateway (FastAPI):** Exposes RESTful endpoints for session management, run control, skill registry upload/review, and streams real-time step progress using Server-Sent Events (SSE).
2. **LangGraph Orchestrator:** Coordinates the agent's reasoning-acting loop using a stateful cyclic graph (`StateGraph`). It uses a PostgreSQL checkpointer to persist execution states, allowing runs to be suspended for human approval and resumed seamlessly.
3. **LLM Gateway:** Decouples model calls from specific API implementations, exposing a unified interface (`BaseLLMProvider`) supporting OpenAI and Anthropic adapters.
4. **Agent Skills Registry:** Manages user-uploaded skills packages structured according to the `agentskills.io` specification.
5. **Security & Sandbox Layer:** Implements strict containment and validation rules:
   - **Static AST Scan:** Evaluates uploaded Python scripts to block unauthorized imports and system calls.
   - **Docker Sandbox Executor:** Launches short-lived, network-isolated, CPU/memory-constrained containers to execute approved skill scripts.
   - **Safety Guards:** Filters runtime SQL commands (read-only enforcement) and SSH commands (command blocking and privilege escalation prevention).
6. **Telemetry & Observability (Langfuse):** Logs execution traces, session runs, model invocation latency, input/output tokens, and hosts prompt templates versioned under the `production` tag.

---

## 2. LLM Provider Configuration Guide

The engine supports dynamic routing between different LLM providers using the **Gateway Adapter Pattern**. All configurations are loaded from environment variables in the `.env` file.

### 2.1 LLM Gateway Configuration Parameters

| Environment Variable | Allowed Values | Description |
|----------------------|----------------|-------------|
| `PROVIDER` | `openai`, `anthropic` | The active LLM provider adapter. |
| `OPENAI_API_KEY` | String (`sk-proj-...`) | Required if `PROVIDER=openai`. |
| `OPENAI_MODEL_NAME` | String (`gpt-4o`, `gpt-4-turbo`) | OpenAI model identifier. |
| `ANTHROPIC_API_KEY` | String (`sk-ant-api03-...`) | Required if `PROVIDER=anthropic`. |
| `ANTHROPIC_MODEL_NAME` | String (`claude-3-5-sonnet-20241022`) | Anthropic Claude model identifier. |

### 2.2 Adapter Implementation Details

- **OpenAI Adapter (`OpenAICompatibleAdapter`):** Integrates with the official `openai` Python SDK. It formats tool definitions according to the Chat Completion API and supports strict schemas for tool calling (`strict=True`) to guarantee argument matches.
- **Anthropic Adapter (`AnthropicAdapter`):** Integrates with the official `anthropic` SDK. It converts standard chat messages and system instructions to Claude's structure, translating tools into XML-like schemas.

### 2.3 Context Window Compaction Settings

To prevent token exhaustion during multi-turn diagnostic tasks, the engine enforces automatic context window compaction. The behavior is governed by the following settings in `backend/app/config.py`:

- `context_window_tokens` (Default: `200000`): The maximum context limit.
- `context_compaction_trigger_ratio` (Default: `0.65`): Compaction starts when estimated tokens exceed $65\%$ of the limit.
- `context_compaction_target_ratio` (Default: `0.45`): Compaction trims history down to target $45\%$ of the limit.

When triggered, the engine summarizes older turns into a single system message (`[AUTO-COMPACTED CONTEXT]`) while preserving recent turns and tool-calling parity.

---

## 3. Security Architecture & Sandboxing

### 3.1 6-Stage Upload Validation Pipeline

Every skill ZIP uploaded is verified against a strict pipeline:

1. **Package & Structure Scan:** Verifies archive integrity, file path safety (no path traversal, no backslashes), file sizes (archive $\le 10$ MB, single file $\le 5$ MB), and confirms a valid `SKILL.md` is present.
2. **Static AST Scan:** Parses Python code using `AdvancedASTSecurityAnalyzer` to block:
   - System imports (`os`, `subprocess`, `sys`, `socket`, `paramiko`, etc.).
   - Execution helpers (`eval`, `exec`, `getattr`, `__import__`).
   - Private attributes (dunder references).
   - Sensitive file paths (`/etc/passwd`, `.env`, `id_rsa`).
3. **Taxonomy Validation:** Scores telecom keyword occurrences in the skill definition to ensure relevance.
4. **LLM Domain Judge:** The skill description is audited by an LLM-assisted judge. Skills with `taxonomy_score < 0.25` AND `llm_score < 0.5` are rejected.
5. **Docker Smoke Test:** If Docker is configured, the script is run with proposed test arguments in a network-less, resource-constrained container to verify it exits with code `0` and satisfies output contracts.
6. **Human-in-the-Loop Review:** The skill is staged as `testing`. A human operator must manually approve the skill via the admin API to switch its status to `ready`.

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
  [Docker Sandbox Execution (Isolated)]
               |
               v
  [Gate 3: Output Contract Validation] ---> Mismatch? ---> [Abort & Raise Error]
               | (Matches return schema)
               v
  [Result Returned to Agent]
```

### 3.3 Safe Database & SSH Access Controls

For direct infrastructure checks, the engine overrides free-form code with hardcoded guards:
- **ClickHouse / PostgreSQL Guards:** Blocks SQL keywords modifying structures/data (`UPDATE`, `INSERT`, `DELETE`, `ALTER`, etc.). Enforces select-only statements and restricts nested output dumps (`INTO OUTFILE`).
- **SSH Guardrails:** Classifies commands into `AUTO_EXECUTE` (read-only checks like `uname`, `free -m`, `ping`) and `REQUIRE_APPROVAL` (status-changing commands like `systemctl restart`). Commands containing system paths or blocklisted patterns (`rm -rf`, `reboot`, `chmod 777`) are blocked outright.

---

## 4. Observability & Telemetry

Observability is handled via the **Langfuse SDK**, pushing traces asynchronously to avoid API call blocking:

- **Trace Routing:** Every execution run starts a trace identified by `run_id` and grouped under `session_id`.
- **DLP Redactor:** A local regex-based data loss prevention layer (`DataRedactor`) scrubs API keys, passwords, and private key strings from trace inputs/outputs, replacing them with `[[MASKED_SECRET]]` before they leave the host.
- **Prompt Registry:** Prompt templates are fetched dynamically from the Langfuse registry using the `production` tag, enabling safe, live updates to safety prompts without code modification.
