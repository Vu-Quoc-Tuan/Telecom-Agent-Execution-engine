# Telecom Agent Architecture

## Overview

The backend is a FastAPI and LangGraph execution engine for telecom operations. It separates
operational knowledge from infrastructure access:

- Agent Skills packages contain instructions and bundled resources.
- Built-in tools own SSH, ClickHouse, and external PostgreSQL access.
- Dangerous built-in operations require human approval before execution.
- PostgreSQL stores sessions, runs, timeline steps, approvals, skills, and audit records.

Authentication is intentionally outside the current development scope. The API must remain on a
trusted network until authentication and authorization are added.

## Layers

```text
FastAPI API and SSE streaming
            |
LangGraph orchestration and run lifecycle
            |
LLM gateway (OpenAI-compatible / Anthropic)
            |
Agent Skills catalog and progressive disclosure
            |
Safety routing and human approval
            |
SSH / ClickHouse / external PostgreSQL connectors
            |
Application PostgreSQL and LangGraph checkpointer
```

## Agent Skills

The registry accepts a ZIP archive containing one Agent Skill directory:

```text
check-kpis/
├── SKILL.md
├── scripts/       # Optional, stored and disclosed as resources
├── references/    # Optional supporting documentation
└── assets/        # Optional text or binary assets
```

`SKILL.md` uses the Agent Skills frontmatter contract. `name` and `description` are required;
`license`, `compatibility`, `metadata`, and `allowed-tools` are optional. The package parser checks
naming rules, folder-name matching, path safety, duplicate files, file counts, compressed and
uncompressed sizes, and compression ratios.

The system follows progressive disclosure:

1. The system prompt lists only `name` and `description` for every `ready` skill.
2. The model calls `load_skill` to load the selected Markdown instructions.
3. The model calls `read_skill_file` only for a referenced script, reference, or asset.

Uploaded scripts are untrusted resources. Python scripts receive static AST analysis, but they are
not executed directly. Operational actions go through fixed built-in tools whose input schemas,
credentials, risk classification, and approval behavior are controlled by the backend.

## Skill Validation Lifecycle

```text
ZIP upload
   -> package and frontmatter validation
   -> resource limits and path validation
   -> secret scan and Python AST scan
   -> telecom taxonomy score
   -> optional LLM domain judge
   -> testing (human review)
   -> ready or rejected
```

Uploading a package whose name already exists returns HTTP `409`; it never deletes or replaces the
active skill. Updating a skill requires an explicit revision workflow, which is not implemented yet.

## Tool Safety

The model receives fixed tool definitions for `run_ssh_command`, `query_clickhouse`, and
`query_postgres`. Skill-loading tools are registered only when at least one skill is `ready`, and
their `skill_name` schema is constrained to the current catalog.

Read-only calls execute directly. State-changing SSH commands are routed to an approval request and
resume only after an operator approves them. Destructive commands on the critical blocklist are
rejected regardless of approval.
