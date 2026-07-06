---
name: unsafe-node-debug-shell
description: Demonstrate static security rejection for a telecom node debug skill that tries to inspect router or switch state with unsafe shell primitives. Use only as a negative upload fixture for alarm, alert, node, service, interface, NOC, and KPI validation testing.
metadata:
  version: "1.0.0"
---

# Unsafe Node Debug Shell

This is a negative upload fixture for the security scanner. The operational idea is recognizable: collect a node debug snapshot for an interface alarm. The bundled implementation is intentionally unsafe and should be rejected by upload validation.

Expected failure:

- The script imports blocked process modules.
- The script attempts dynamic execution.
- The backend static AST scan should reject the package before it reaches human approval.

Use this zip to confirm the upload pipeline blocks unsafe diagnostics instead of allowing shell-style node debug code into the skill registry.
