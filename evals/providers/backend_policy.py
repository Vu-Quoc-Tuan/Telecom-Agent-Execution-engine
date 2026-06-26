from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    from app.agent.routing import decide_tool_route
    from app.agent.builtin_tools import classify_builtin_risk
    from app.agent.safety import AgentSafetyGuard
    from app.sandbox.domain_validator import TelecomDomainValidator

    del prompt, options
    variables = context.get("vars", {})
    case_type = variables.get("case_type")

    if case_type == "domain":
        score = TelecomDomainValidator.calculate_taxonomy_score(
            variables.get("skill_name", ""),
            variables.get("skill_description", ""),
            variables.get("skill_md", variables.get("skill_body", "")),
        )
        output = {
            "domain_score": score,
            "reason": "Calculated by the backend telecom taxonomy policy.",
            "suspicious_points": "None" if score > 0 else "No telecom taxonomy match.",
        }
    elif case_type == "routing":
        risk_level = variables.get("risk_level") or classify_builtin_risk(
            variables["tool_name"],
            variables.get("arguments", {}),
        )
        output = {
            "action": decide_tool_route(risk_levels=[risk_level]),
            "tool_name": variables["tool_name"],
            "reason": f"Calculated by the backend risk routing policy with risk={risk_level}.",
        }
    elif case_type == "ssh_safety":
        safe, reason = AgentSafetyGuard.verify_ssh_command(variables.get("command", ""))
        output = {
            "action": "allowed" if safe else "refusal",
            "tool_name": "",
            "reason": reason or "Command passed the SSH safety policy.",
        }
    elif case_type == "dlp":
        output = {
            "response": AgentSafetyGuard.sanitize_input_prompt(variables.get("user_input", ""))
        }
    else:
        return {"error": f"Unsupported local eval case_type: {case_type}"}

    return {"output": json.dumps(output, ensure_ascii=False)}
