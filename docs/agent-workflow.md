# Agent And Skill Workflows

## Agent Run

```text
POST /api/v1/chat/stream
   -> create agent_run (running)
   -> call LLM with built-in tools and ready-skill catalog
   -> no tool call: persist final answer and complete run
   -> read-only tool: execute and return result to LLM
   -> dangerous tool: create approval request and suspend graph
   -> approved: resume and execute
   -> rejected or expired: return tool error and continue safely
```

Every LLM call, tool call, approval, and error is represented as a `run_steps` timeline entry. Run
status supports `pending`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`, and
`timed_out`.

## Upload An Agent Skill

The endpoint accepts one multipart `.zip` file:

```bash
curl -X POST http://localhost:8000/api/v1/skills/upload \
  -F 'file=@check-kpis.zip;type=application/zip'
```

Minimal package:

```text
check-kpis/
└── SKILL.md
```

Minimal `SKILL.md`:

```markdown
---
name: check-kpis
description: Check telecom KPI alarms and latency. Use during NOC incident triage.
metadata:
  version: "1.0"
---

# Check KPIs

1. Query current alarms.
2. Compare latency and packet loss against the baseline.
3. Read `references/thresholds.md` when threshold details are needed.
```

Successful automated validation returns `PENDING_REVIEW`. An operator then calls:

```text
POST /api/v1/skills/{skill_id}/approve
POST /api/v1/skills/{skill_id}/reject
```

Only `ready` skills appear in the agent catalog.

## Skill Storage

The `skills` table stores:

- `name`, `description`, and an internal version derived from `metadata.version`
- `skill_md`: Markdown instructions without YAML frontmatter
- `frontmatter`: validated Agent Skills metadata
- `bundled_files`: resources keyed by paths relative to the skill root
- `status`, security review log, and malicious flag

Each resource record contains `encoding`, `content`, `media_type`, and byte `size`. UTF-8 resources
are stored as text; binary resources are stored as base64.

## Progressive Disclosure

At LLM call time, the backend adds only ready-skill names and descriptions to the system prompt. A
matching skill is activated through `load_skill`; its result contains the instructions and a resource
manifest. `read_skill_file` loads one relative resource path on demand.

Skill instructions can direct the agent to fixed infrastructure tools. They cannot inject connector
credentials or execute uploaded Python directly.

## Validation Errors

- `400`: invalid ZIP, package structure, frontmatter, resource, domain, or security policy
- `409`: a skill with the same name already exists
- `415`: uploaded file is not a `.zip`

ZIP uploads are bounded by archive size, file count, per-file size, total uncompressed size, and
compression ratio to prevent resource exhaustion.
