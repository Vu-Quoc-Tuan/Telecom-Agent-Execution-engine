# Agent And Skill Workflows

## Agent Run

```text
POST /api/v1/chat/stream
   -> create agent_run (running)
   -> call LLM with backend-owned capabilities and ready-skill catalog
   -> no tool call: persist final answer and complete run
   -> approved skill script or backend-owned capability: execute and return result to LLM
   -> LLM-generated executable payload: reject or create approval request and suspend graph
   -> approved: resume and execute the exact reviewed payload
   -> rejected: do not execute; return HUMAN_REJECTED to one tool-disabled LLM turn
   -> expired: fail the run without executing the tool
```

Every LLM call, tool call, approval, and error is represented as a `run_steps` timeline entry. Run
status supports `pending`, `running`, `waiting_approval`, `completed`, `failed`, `cancelled`, and
`timed_out`.

The runtime does not auto-execute a payload merely because it looks read-only. SQL strings, SSH
commands, shell snippets, Python code, or wrappers written by the model during a run are
LLM-generated executable payloads. They must be blocked or routed to human approval with the exact
payload. Common safe operations should be represented as backend-owned capabilities with fixed
runners/templates and JSON arguments.

Sandbox execution is intentionally split into three classes:

- Validated skill scripts: scripts bundled under `scripts/`, scanned during upload, given a
  backend-validated run spec, passed Cube smoke testing, approved by a human as part of the skill
  package, and executed through
  `run_skill_script(skill_name, script_path, arguments)`.
- Backend-owned capabilities: scripts, query templates, connector operations, or simple helpers
  shipped by this backend. The model chooses the capability and fills JSON arguments; it does not
  write the underlying executable payload.
- Ad-hoc generated executable payloads: any Python, shell, SQL, SSH command, wrapper, or script body
  invented by the model during a run. These are never treated as pre-approved. They must be rejected
  or routed to human approval with the exact generated payload, even when a static scanner does not
  find an obvious violation.

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

After static and domain checks, the upload pipeline may ask an LLM analyzer to read `SKILL.md`,
script names, and script source to propose how each bundled script should be invoked and smoke
tested. That proposal is not trusted until the backend validates it and Cube runs the smoke test.

Successful automated validation returns `PENDING_REVIEW`. Automated validation must be fail-closed:
if a bundled script fails AST/security checks, secret scanning, domain checks, run-spec validation,
or Cube smoke testing, it cannot become an auto-runnable script. The simplest v1 policy is to reject
the package when any script fails. An operator then calls:

```text
POST /api/v1/skills/{skill_id}/approve
POST /api/v1/skills/{skill_id}/reject
```

Approval is blocked if the package still contains any script manifest entry that is not `passed`.
That includes `pending_sandbox` entries produced when Cube Sandbox was not available during upload.
Only `ready` skills appear in the agent catalog.

## Skill Storage

The `skills` table stores:

- `name`, `description`, and an internal version derived from `metadata.version`
- `skill_md`: Markdown instructions without YAML frontmatter
- `frontmatter`: validated Agent Skills metadata
- `bundled_files`: resources keyed by paths relative to the skill root
- `script_manifest`: backend-derived runtime metadata for runnable scripts
- `status`, security review log, and malicious flag

Each resource record contains `encoding`, `content`, `media_type`, and byte `size`. UTF-8 resources
are stored as text; binary resources are stored as base64.

Executable script resources also need runtime metadata derived from validation. The manifest should
be simple and internal: script path, content hash, purpose, approved invocation mode, smoke-test
arguments or command, optional input/output JSON contracts, Cube result summary, and runtime limits.
It does not require skill authors to write a JSON Schema by hand; the upload analyzer can propose a
small schema and the backend sanitizes it. Only scripts with a passing manifest entry may be offered
to the model as callable `script_path` values. If `output_contract.mode` is `json`, upload smoke
testing and runtime both parse stdout and validate it against the stored schema.

## Progressive Disclosure

At LLM call time, the backend adds only ready-skill names and descriptions to the system prompt. A
matching skill is activated through `load_skill`; its result contains the instructions and a resource
manifest. `read_skill_file` loads one relative resource path on demand for documentation,
references, lookup data, or assets.

Normal script execution does not require the model to read or copy source code. The skill
instructions and stored manifest should name validated scripts and describe how to supply runtime
arguments. The model then calls:

```json
{
  "skill_name": "check-kpis",
  "script_path": "scripts/check_latency.py",
  "arguments": {
    "site": "site-a",
    "window_minutes": 15
  }
}
```

The backend resolves `script_path` from the approved skill package, verifies the stored content hash,
uploads the stored script and its bundled files into Cube Sandbox, writes the JSON arguments using
the approved invocation mode, runs the exact reviewed script, validates stdout against any stored
output contract, and returns stdout/stderr to the model.
Scripts that try to create background workers, detached tasks, subprocesses, threads, or shutdown
hooks are rejected during upload AST validation. Runtime execution is expected to finish in the
foreground within Cube limits.

The model may use multiple ready skills and multiple scripts in one answer. Each script call is
validated independently against its skill, manifest entry, hash, and runtime limits.

Skill instructions can direct the agent to backend-owned capabilities. They cannot inject connector
credentials, create new connector definitions, or turn model-generated code, SQL, SSH, or shell text
into an auto-executed tool call. If a task needs behavior that is not covered by approved skill
scripts or backend-owned capabilities, the model must ask for clarification, stop, or request human
approval for the exact new executable payload.

Current backend-owned capabilities include:

- `get_site_alarm_summary(site_id, window_minutes, limit)` for ClickHouse alarm summaries.
- `get_site_kpi_snapshot(site_id, window_minutes, limit)` for ClickHouse KPI snapshots.
- `get_site_inventory(site_id, limit)` for external PostgreSQL inventory.
- `get_node_health_snapshot(node_name)` for fixed read-only SSH health checks.

## Validation Errors

- `400`: invalid ZIP, package structure, frontmatter, resource, domain, or security policy
- `409`: a skill with the same name already exists
- `415`: uploaded file is not a `.zip`

ZIP uploads are bounded by archive size, file count, per-file size, total uncompressed size, and
compression ratio to prevent resource exhaustion.
