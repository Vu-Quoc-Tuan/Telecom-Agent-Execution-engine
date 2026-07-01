from __future__ import annotations

import json
import os
from typing import Any

import httpx


DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8000"


def _base_url() -> str:
    return os.getenv("REDTEAM_BACKEND_BASE_URL", DEFAULT_BACKEND_BASE_URL).rstrip("/")


def _timeout_seconds() -> float:
    raw_timeout = os.getenv("REDTEAM_BACKEND_TIMEOUT_SECONDS", "180")
    try:
        return float(raw_timeout)
    except ValueError:
        return 180.0


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _dispatch_sse_event(event: str | None, data: str) -> tuple[str | None, str | None]:
    if not event or not data:
        return None, None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return "text_delta", data

    if event == "text_delta":
        return event, str(payload.get("delta", ""))
    if event == "run_completed":
        return event, str(payload.get("final_answer", ""))
    if event == "run_suspended":
        tool_name = payload.get("tool_name") or "unknown_tool"
        risk_level = payload.get("risk_level") or "unknown_risk"
        return event, f"RUN_SUSPENDED: tool={tool_name}; risk={risk_level}; awaiting human approval."
    if event == "run_failed":
        return event, f"RUN_FAILED: {payload.get('error', 'unknown error')}"
    if event == "error":
        return event, f"ERROR: {payload.get('message', 'unknown error')}"
    return event, None


def _read_agent_stream(response: httpx.Response) -> str:
    event: str | None = None
    data_lines: list[str] = []
    deltas: list[str] = []
    terminal_output: str | None = None

    def flush_event() -> None:
        nonlocal event, data_lines, terminal_output
        if not event and not data_lines:
            return
        event_type, output = _dispatch_sse_event(event, "\n".join(data_lines))
        if event_type == "text_delta" and output:
            deltas.append(output)
        elif event_type in {"run_completed", "run_suspended", "run_failed", "error"} and output:
            terminal_output = output
        event = None
        data_lines = []

    for line in response.iter_lines():
        if line == "":
            flush_event()
            if terminal_output is not None:
                break
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())

    flush_event()
    if terminal_output:
        return terminal_output
    return "".join(deltas).strip()


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    base_url = _base_url()
    timeout = _timeout_seconds()
    user_message = str(prompt or "")

    with httpx.Client(timeout=timeout) as client:
        session_response = client.post(
            f"{base_url}/api/v1/sessions",
            json={"title": "Promptfoo redteam"},
        )
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]

        body: dict[str, Any] = {
            "session_id": session_id,
            "user_message": user_message,
        }
        provider = _optional_env("REDTEAM_LLM_PROVIDER")
        model = _optional_env("REDTEAM_LLM_MODEL")
        skill_name = _optional_env("REDTEAM_SKILL_NAME")
        if provider:
            body["provider"] = provider
        if model:
            body["model"] = model
        if skill_name:
            body["skill_mode"] = "specific"
            body["skill_name"] = skill_name

        with client.stream("POST", f"{base_url}/api/v1/chat/stream", json=body) as response:
            response.raise_for_status()
            output = _read_agent_stream(response)

    return {
        "output": output,
        "metadata": {
            "backend_base_url": base_url,
            "session_id": session_id,
        },
    }
