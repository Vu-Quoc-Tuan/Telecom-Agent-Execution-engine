---
name: memory-heavy-kpi-audit
description: Audit telecom KPI alarm notes for NOC consistency, latency, packet_loss, throughput, service, site, and node references. This package is intentionally shaped to pass Agent Skills metadata and static checks, then fail Docker sandbox smoke testing because its script consumes excessive memory.
metadata:
  version: "1.0.0"
---

# Memory Heavy KPI Audit

This skill is a negative upload fixture. Its intended task is meaningful: review NOC KPI notes and detect whether each note contains a site, node, alarm, KPI symptom, and escalation owner.

It should pass package structure validation, telecom taxonomy validation, and static AST security scanning. It should fail during Docker sandbox smoke testing because `scripts/audit_kpi_note.py` allocates more memory than the sandbox should allow.

Expected upload behavior:

- VONG 1 package parsing: pass.
- VONG 1 static AST scan: pass.
- VONG 2 telecom taxonomy: pass.
- VONG 5 Docker sandbox validation: fail with a non-zero exit code, timeout, or out-of-memory kill depending on the Docker runtime.

Use this zip to confirm the UI/backend surfaces sandbox resource failures cleanly instead of accepting memory-heavy skill scripts.
