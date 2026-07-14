from __future__ import annotations

import hashlib
import json
from html import escape
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.agent.builtin_tools import (
    GET_ACTIVE_ALARMS,
    GET_NODE_HEALTH_SNAPSHOT,
    GET_SITE_ALARM_SUMMARY,
    GET_SITE_INVENTORY,
    GET_SITE_KPI_SNAPSHOT,
    LOAD_SKILL,
    PING_NODE,
    READ_SKILL_FILE,
    RESTART_SERVICE,
    RUN_SKILL_SCRIPT,
    _allowed_restart_services,
    classify_builtin_risk,
)
from app.agent.safety import AgentSafetyGuard
from app.agent.tool_validation import validate_json_value_against_schema
from app.common.config_parsing import parse_node_host_map
from app.common.enums import ExecutionMode
from app.common.exceptions import SkillRuntimeError
from app.config import settings as default_settings
from app.connectors.clickhouse import TelcoClickHouseConnector
from app.connectors.postgres import TelcoPostgresConnector
from app.connectors.ssh import TelcoSSHConnector
from app.database.repositories.skills import SkillRepository
from app.observability.redaction import DataRedactor


def _allowed_nodes(settings) -> set[str]:
    raw = getattr(settings, "SSH_ALLOWED_NODES", "") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _node_host_map(settings) -> dict[str, str]:
    raw = getattr(settings, "SSH_NODE_HOST_MAP", "") or ""
    return parse_node_host_map(raw)


def _prepare_tool_output(output: str) -> tuple[str, bool]:
    """
    Remove sensitive info from tool output, such as password, api key, ...
    Return tuple of (redacted_output, is_truncated)
    """
    return AgentSafetyGuard.truncate_output(DataRedactor.redact_text(output))


def _resolve_ssh_host(settings, target_node: str, allowed_nodes: set[str]) -> str:
    mapped_host = _node_host_map(settings).get(target_node)
    if mapped_host:
        return mapped_host
    if allowed_nodes and target_node:
        return target_node
    return getattr(settings, "SSH_HOST", "") or target_node


# --------------Build Connector
def _build_ssh_connector(settings, host: str) -> TelcoSSHConnector:
    return TelcoSSHConnector(
        host=host,
        username=getattr(settings, "SSH_USER", ""),
        password=getattr(settings, "SSH_PASSWORD", ""),
        port=getattr(settings, "SSH_PORT", 22),
        timeout=getattr(settings, "SSH_TIMEOUT_SECONDS", 30),
        known_hosts_path=getattr(settings, "SSH_KNOWN_HOSTS", "") or None,
        auto_add_host_keys=getattr(settings, "SSH_AUTO_ADD_HOST_KEYS", False),
    )


def _build_clickhouse_connector(settings) -> TelcoClickHouseConnector:
    return TelcoClickHouseConnector(
        host=getattr(settings, "CLICKHOUSE_HOST", ""),
        port=getattr(settings, "CLICKHOUSE_PORT", 8123),
        username=getattr(settings, "CLICKHOUSE_USER", ""),
        password=getattr(settings, "CLICKHOUSE_PASSWORD", ""),
        database=getattr(settings, "CLICKHOUSE_DATABASE", "default"),
        timeout_seconds=getattr(settings, "EXTERNAL_CONNECTOR_TIMEOUT_SECONDS", 15),
        max_result_rows=getattr(settings, "QUERY_MAX_RESULT_ROWS", 1000),
    )


def _build_postgres_connector(settings, *, read_only: bool) -> TelcoPostgresConnector:
    return TelcoPostgresConnector(
        host=getattr(settings, "EXTERNAL_POSTGRES_HOST", ""),
        port=getattr(settings, "EXTERNAL_POSTGRES_PORT", 5432),
        username=getattr(settings, "EXTERNAL_POSTGRES_USER", ""),
        password=getattr(settings, "EXTERNAL_POSTGRES_PASSWORD", ""),
        database=getattr(settings, "EXTERNAL_POSTGRES_DATABASE", "postgres"),
        read_only=read_only,
        timeout_seconds=getattr(settings, "EXTERNAL_CONNECTOR_TIMEOUT_SECONDS", 15),
        max_result_rows=getattr(settings, "QUERY_MAX_RESULT_ROWS", 1000),
    )


# ------------------------------


def _resolve_capability_node(settings, target_node: str) -> str:
    allowed = _allowed_nodes(settings)
    if allowed and target_node not in allowed:
        raise SkillRuntimeError(f"Node '{target_node}' không nằm trong SSH_ALLOWED_NODES.")
    host = _resolve_ssh_host(settings, target_node, allowed)
    if not host:
        raise SkillRuntimeError("Chưa cấu hình SSH host và không có node_name.")
    return host


# mở phần hướng dẫn
def _run_load_skill(db: Session, arguments: dict[str, Any]) -> tuple[str, bool]:
    skill_name = str(arguments.get("skill_name", "")).strip()
    skill = SkillRepository.get_skill_by_name(db, skill_name)
    if not skill or skill.status != "ready":
        raise SkillRuntimeError(f"Skill '{skill_name}' không tồn tại hoặc chưa được duyệt.")
    resource_lines = "\n".join(
        f"  <file>{escape(path)}</file>" for path in sorted((skill.bundled_files or {}).keys())
    )
    wrapped = (
        f'<skill_content name="{escape(skill.name)}">\n'
        f"{skill.skill_md}\n\n"
        "<skill_resources>\n"
        f"{resource_lines}\n"
        "</skill_resources>\n"
        "Relative resource paths are resolved from the skill directory.\n"
        "</skill_content>"
    )
    return _prepare_tool_output(wrapped)


def _normalize_skill_file_path(file_path: str) -> str:
    normalized_path = PurePosixPath(file_path)
    if (
        not file_path
        or normalized_path.is_absolute()
        or any(part in {"", ".", ".."} for part in normalized_path.parts)
        or "\\" in file_path
    ):
        raise SkillRuntimeError("Đường dẫn resource không hợp lệ.")
    return normalized_path.as_posix()


def _run_read_skill_file(db: Session, arguments: dict[str, Any]) -> tuple[str, bool]:
    skill_name = str(arguments.get("skill_name", "")).strip()
    file_path = _normalize_skill_file_path(str(arguments.get("file_path", "")).strip())
    skill = SkillRepository.get_skill_by_name(db, skill_name)
    if not skill or skill.status != "ready":
        raise SkillRuntimeError(f"Skill '{skill_name}' không tồn tại hoặc chưa được duyệt.")
    files: dict[str, Any] = skill.bundled_files or {}
    if file_path not in files:
        available = ", ".join(sorted(files)) or "(không có)"
        raise SkillRuntimeError(
            f"File '{file_path}' không có trong skill '{skill_name}'. File khả dụng: {available}."
        )
    record = files[file_path]
    if not isinstance(record, dict) or "content" not in record or "encoding" not in record:
        raise SkillRuntimeError(f"Resource '{file_path}' có định dạng lưu trữ không hợp lệ.")
    if record["encoding"] == "utf-8":
        output = str(record["content"])
    elif record["encoding"] == "base64":
        media_type = record.get("media_type", "application/octet-stream")
        output = f"data:{media_type};base64,{record['content']}"
    else:
        raise SkillRuntimeError(f"Resource '{file_path}' dùng encoding không được hỗ trợ.")
    return _prepare_tool_output(output)


# --- Backend-owned capability runners ---------------------------------------
#
# Live ClickHouse schema (alarm_data):
#   table `alarm` with device_id / severity / time_created / time_solved / ...
#   no `site_id`, no `alarms`, no `kpi_snapshots`.
# Site scope is resolved via external Postgres inventory:
#   alarm_data.station + alarm_data.device (station_id or station name).
# Time windows are relative to max(time_created) so historical mock datasets
# (e.g. June-only) still return rows when wall-clock now() is outside the range.

_ALARM_TABLE = "alarm"
# Window relative to latest available alarm timestamp (works for live + mock history).
_ALARM_WINDOW_PREDICATE = (
    "time_created >= (SELECT max(time_created) FROM alarm) "
    "- INTERVAL {window_minutes:UInt32} MINUTE"
)


async def _run_clickhouse_template(
    settings,
    sql: str,
    params: dict[str, Any],
) -> tuple[str, bool]:
    connector = _build_clickhouse_connector(settings)
    try:
        rows = await connector.query(sql, params=params)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


async def _resolve_site_device_ids(settings, site_id: str) -> list[str]:
    """Map station_id or station name → device_id list via external Postgres inventory."""
    site_key = str(site_id or "").strip()
    if not site_key:
        return []
    sql = (
        "SELECT d.device_id "
        "FROM alarm_data.device d "
        "WHERE d.station_id = :site_id "
        "OR d.station_id IN ("
        "  SELECT s.station_id FROM alarm_data.station s "
        "  WHERE s.station_id = :site_id OR s.name = :site_id"
        ") "
        "LIMIT 1000"
    )
    connector = _build_postgres_connector(settings, read_only=True)
    try:
        rows = await connector.query(sql, {"site_id": site_key})
    finally:
        connector.close()
    device_ids: list[str] = []
    for row in rows:
        device_id = str(row.get("device_id") or "").strip()
        if device_id:
            device_ids.append(device_id)
    return device_ids


def _empty_site_payload(site_id: str, *, reason: str) -> tuple[str, bool]:
    return _prepare_tool_output(
        json.dumps(
            {
                "site_id": site_id,
                "device_ids": [],
                "rows": [],
                "note": reason,
            },
            ensure_ascii=False,
        )
    )


async def _run_get_site_alarm_summary(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    site_id = str(arguments.get("site_id", "")).strip()
    window_minutes = int(arguments.get("window_minutes", 60))
    limit = int(arguments.get("limit", 100))
    device_ids = await _resolve_site_device_ids(settings, site_id)
    if not device_ids:
        return _empty_site_payload(
            site_id,
            reason=(
                "No devices found for this site_id in external Postgres inventory "
                "(use station_id like STN-0001 or station name like HN-Station-001)."
            ),
        )
    params: dict[str, Any] = {
        "device_ids": device_ids,
        "window_minutes": window_minutes,
        "limit": limit,
    }
    sql = (
        "SELECT severity, count() AS alarm_count "
        f"FROM {_ALARM_TABLE} "
        "WHERE device_id IN {device_ids:Array(String)} "
        f"AND {_ALARM_WINDOW_PREDICATE} "
        "GROUP BY severity "
        "ORDER BY alarm_count DESC "
        "LIMIT {limit:UInt32}"
    )
    connector = _build_clickhouse_connector(settings)
    try:
        rows = await connector.query(sql, params=params)
    finally:
        connector.close()
    payload = {
        "site_id": site_id,
        "device_count": len(device_ids),
        "window_minutes": window_minutes,
        "summary": rows,
    }
    return _prepare_tool_output(json.dumps(payload, ensure_ascii=False, default=str))


async def _run_get_active_alarms(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params: dict[str, Any] = {
        "window_minutes": int(arguments.get("window_minutes", 60)),
        "limit": int(arguments.get("limit", 100)),
    }
    where_clauses = [
        "time_solved IS NULL",
        _ALARM_WINDOW_PREDICATE,
    ]
    severity = arguments.get("severity")
    if severity:
        # Live data uses Title-case severities (Critical/Major/...); accept any case.
        params["severity"] = str(severity)
        where_clauses.append("lowerUTF8(severity) = lowerUTF8({severity:String})")
    sql = (
        "SELECT alarm_id, error_code, device_id, time_created, time_solved, "
        "status, severity, raw_log, description "
        f"FROM {_ALARM_TABLE} "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY time_created DESC "
        "LIMIT {limit:UInt32}"
    )
    return await _run_clickhouse_template(settings, sql, params)


async def _run_get_site_kpi_snapshot(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    """Operational snapshot for a site derived from alarm table (no kpi_snapshots table)."""
    site_id = str(arguments.get("site_id", "")).strip()
    window_minutes = int(arguments.get("window_minutes", 60))
    limit = int(arguments.get("limit", 100))
    device_ids = await _resolve_site_device_ids(settings, site_id)
    if not device_ids:
        return _empty_site_payload(
            site_id,
            reason=(
                "No devices found for this site_id in external Postgres inventory "
                "(use station_id like STN-0001 or station name like HN-Station-001)."
            ),
        )
    params: dict[str, Any] = {
        "device_ids": device_ids,
        "window_minutes": window_minutes,
        "limit": limit,
    }
    # One compact snapshot row + top error codes (no dedicated KPI table in live CH).
    summary_sql = (
        "SELECT "
        "count() AS alarm_total, "
        "countIf(time_solved IS NULL) AS alarm_active, "
        "countIf(lowerUTF8(severity) = 'critical') AS critical_count, "
        "countIf(lowerUTF8(severity) = 'major') AS major_count, "
        "countIf(lowerUTF8(severity) = 'minor') AS minor_count, "
        "countIf(lowerUTF8(severity) = 'warning') AS warning_count, "
        "uniqExact(device_id) AS devices_with_alarms, "
        "max(time_created) AS latest_alarm_at "
        f"FROM {_ALARM_TABLE} "
        "WHERE device_id IN {device_ids:Array(String)} "
        f"AND {_ALARM_WINDOW_PREDICATE}"
    )
    top_errors_sql = (
        "SELECT error_code, count() AS alarm_count "
        f"FROM {_ALARM_TABLE} "
        "WHERE device_id IN {device_ids:Array(String)} "
        f"AND {_ALARM_WINDOW_PREDICATE} "
        "GROUP BY error_code "
        "ORDER BY alarm_count DESC "
        "LIMIT {limit:UInt32}"
    )
    connector = _build_clickhouse_connector(settings)
    try:
        summary_rows = await connector.query(summary_sql, params=params)
        top_errors = await connector.query(top_errors_sql, params=params)
    finally:
        connector.close()
    payload = {
        "site_id": site_id,
        "device_count": len(device_ids),
        "window_minutes": window_minutes,
        "source": "alarm_table_derived",
        "note": (
            "Live ClickHouse has no kpi_snapshots table; "
            "this snapshot is derived from alarm_data.alarm for the site's devices."
        ),
        "snapshot": summary_rows[0] if summary_rows else {},
        "top_error_codes": top_errors,
    }
    return _prepare_tool_output(json.dumps(payload, ensure_ascii=False, default=str))


async def _run_get_site_inventory(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params = {
        "site_id": str(arguments.get("site_id", "")),
        "limit": int(arguments.get("limit", 100)),
    }
    sql = (
        "SELECT * FROM alarm_data.device "
        "WHERE station_id = :site_id "
        "OR station_id IN (SELECT station_id FROM alarm_data.station WHERE name = :site_id) "
        "LIMIT :limit"
    )
    connector = _build_postgres_connector(settings, read_only=True)
    try:
        rows = await connector.query(sql, params)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


# Bộ lệnh read-only cố định cho snapshot sức khoẻ node.
_NODE_HEALTH_COMMANDS = ("hostname", "uptime", "free -m", "df -h")


async def _run_get_node_health_snapshot(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    target_node = str(arguments.get("node_name", "")).strip()
    host = _resolve_capability_node(settings, target_node)
    connector = _build_ssh_connector(settings, host)
    sections: list[str] = []
    try:
        for command in _NODE_HEALTH_COMMANDS:
            stdout, stderr = await connector.execute_command(command, approval_confirmations=0)
            body = stdout if not stderr else f"{stdout}\n[STDERR]\n{stderr}"
            sections.append(f"$ {command}\n{body}")
    finally:
        connector.close()
    return _prepare_tool_output("\n\n".join(sections))


async def _run_ping_node(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    target_node = str(arguments.get("node_name", "")).strip()
    count = int(arguments.get("count", 4))
    host = _resolve_capability_node(settings, target_node)
    # -w deadline = count + 5 giây để chừa thời gian cho gói cuối phản hồi.
    command = f"ping -c {count} -w {count + 5} {host}"
    connector = _build_ssh_connector(settings, host)
    try:
        stdout, stderr = await connector.execute_command(command, approval_confirmations=0)
    finally:
        connector.close()
    combined = stdout if not stderr else f"{stdout}\n[STDERR]\n{stderr}"
    return _prepare_tool_output(combined)


async def _run_restart_service(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    target_node = str(arguments.get("node_name", "")).strip()
    service_name = str(arguments.get("service_name", "")).strip()
    allowed_services = _allowed_restart_services(settings)
    if service_name not in allowed_services:
        allowed = ", ".join(sorted(allowed_services)) or "(none configured)"
        raise SkillRuntimeError(
            f"Service '{service_name}' không nằm trong SSH_RESTART_ALLOWED_SERVICES: {allowed}."
        )
    host = _resolve_capability_node(settings, target_node)
    connector = _build_ssh_connector(settings, host)
    try:
        restart_stdout, restart_stderr = await connector.execute_command(
            f"systemctl restart {service_name}",
            approval_confirmations=1,
        )
        status_stdout, status_stderr = await connector.execute_command(
            f"systemctl is-active {service_name}",
            approval_confirmations=1,
        )
    finally:
        connector.close()
    sections = [
        f"$ systemctl restart {service_name}\n{restart_stdout}",
        f"$ systemctl is-active {service_name}\n{status_stdout}",
    ]
    stderr = "\n".join(item for item in (restart_stderr, status_stderr) if item)
    output = "\n\n".join(sections)
    if stderr:
        output = f"{output}\n[STDERR]\n{stderr}"
    return _prepare_tool_output(output)


# --- run_skill_script: chạy script đã duyệt Vòng 5 với 3 cổng kiểm tra --------


def _load_ready_skill(db: Session, skill_name: str):
    skill = SkillRepository.get_skill_by_name(db, skill_name)
    if not skill or skill.status != "ready":
        raise SkillRuntimeError(f"Skill '{skill_name}' không tồn tại hoặc chưa được duyệt.")
    return skill


def _approved_manifest_entry(skill, script_path: str) -> dict[str, Any]:
    manifest: dict[str, Any] = skill.script_manifest or {}
    entry = manifest.get(script_path)
    if not isinstance(entry, dict) or entry.get("status") != "passed":
        raise SkillRuntimeError(
            f"Script '{script_path}' chưa được duyệt (Vòng 5) trong skill '{skill.name}'."
        )
    return entry


def _bundled_script_content(skill, script_path: str) -> str:
    files: dict[str, Any] = skill.bundled_files or {}
    record = files.get(script_path)
    if not isinstance(record, dict) or "content" not in record:
        raise SkillRuntimeError(
            f"Script '{script_path}' không có nội dung trong gói skill '{skill.name}'."
        )
    if record.get("encoding") != "utf-8":
        raise SkillRuntimeError(f"Script '{script_path}' phải được lưu dưới dạng utf-8 để chạy.")
    return str(record["content"])


def _verify_script_hash(entry: dict[str, Any], content: str) -> None:
    expected = str(entry.get("script_hash", ""))
    actual = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    if not expected or expected != actual:
        raise SkillRuntimeError(
            "Script hash mismatch: file đã thay đổi so với bản đã duyệt, từ chối chạy.",
            details={"expected_hash": expected, "actual_hash": actual},
        )


def _verify_arguments_against_schema(entry: dict[str, Any], arguments: dict[str, Any]) -> None:
    input_schema = entry.get("input_schema")
    if not isinstance(input_schema, dict):
        return
    try:
        validate_json_value_against_schema(
            value=arguments,
            schema=input_schema,
            path="arguments",
        )
    except SkillRuntimeError as exc:
        raise SkillRuntimeError(
            f"Arguments rejected: tham số không khớp approved schema. {exc.message}",
            details=exc.details,
        ) from exc


def _verify_output_against_contract(entry: dict[str, Any], stdout: str) -> None:
    contract = entry.get("output_contract")
    if not isinstance(contract, dict) or contract.get("mode") != "json":
        return
    schema = contract.get("schema")
    if not isinstance(schema, dict):
        return
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SkillRuntimeError(
            "Output rejected: script không trả về JSON hợp lệ theo output contract.",
            details={"error": str(exc)},
        ) from exc
    try:
        validate_json_value_against_schema(value=parsed, schema=schema, path="output")
    except SkillRuntimeError as exc:
        raise SkillRuntimeError(
            f"Output rejected: kết quả không khớp output contract. {exc.message}",
            details=exc.details,
        ) from exc


async def _run_skill_script(
    db: Session,
    arguments: dict[str, Any],
    settings,
) -> tuple[str, bool]:
    skill_name = str(arguments.get("skill_name", "")).strip()
    script_path = str(arguments.get("script_path", "")).strip()
    script_arguments = arguments.get("arguments", {})
    if not isinstance(script_arguments, dict):
        raise SkillRuntimeError("Tham số 'arguments' của run_skill_script phải là JSON object.")

    skill = _load_ready_skill(db, skill_name)
    entry = _approved_manifest_entry(skill, script_path)

    content = _bundled_script_content(skill, script_path)
    _verify_script_hash(entry, content)

    _verify_arguments_against_schema(entry, script_arguments)

    # Khởi tạo sandbox executor (import lazily để test patch được).
    from app.sandbox.docker_executor import build_sandbox_executor_from_settings

    executor = build_sandbox_executor_from_settings(settings)
    if executor is None:
        raise SkillRuntimeError(
            "Sandbox chưa khả dụng. Cần Docker trên host và SANDBOX_ENABLED=true."
        )

    limits = entry.get("limits") if isinstance(entry.get("limits"), dict) else {}
    timeout_seconds = limits.get("timeout_seconds")

    result = await executor.execute_skill_script(
        script_path=script_path,
        arguments=script_arguments,
        bundled_files=skill.bundled_files or {},
        timeout_seconds=timeout_seconds,
    )

    if result.timed_out:
        raise SkillRuntimeError(
            f"Script '{script_path}' đã hết thời gian thực thi trong sandbox.",
            details={"script_path": script_path},
        )
    if result.exit_code != 0:
        detail = result.stderr or f"exit code {result.exit_code}"
        raise SkillRuntimeError(
            f"Script '{script_path}' kết thúc với lỗi: {detail}",
            details={"exit_code": result.exit_code},
        )

    # Cổng 3: output phải khớp output_contract đã duyệt.
    _verify_output_against_contract(entry, result.stdout)

    return _prepare_tool_output(result.stdout)


_BACKEND_OWNED_RUNNERS = {
    GET_SITE_ALARM_SUMMARY: _run_get_site_alarm_summary,
    GET_ACTIVE_ALARMS: _run_get_active_alarms,
    GET_SITE_KPI_SNAPSHOT: _run_get_site_kpi_snapshot,
    GET_SITE_INVENTORY: _run_get_site_inventory,
    GET_NODE_HEALTH_SNAPSHOT: _run_get_node_health_snapshot,
    PING_NODE: _run_ping_node,
    RESTART_SERVICE: _run_restart_service,
}


async def execute_builtin_tool(
    tool_name: str,
    arguments: dict[str, Any],
    db: Session,
    settings=default_settings,
    approval_confirmations: int = 0,
) -> tuple[str, bool]:
    """Dispatch một tool built-in. Trả về (output, was_truncated).

    Mọi lỗi nghiệp vụ/hạ tầng được ném dưới dạng TelecomAgentException để node
    execute_tools ghi nhận failed và phản hồi lại cho LLM đọc tiếp.
    """
    if (
        classify_builtin_risk(tool_name, arguments) == ExecutionMode.REQUIRE_APPROVAL.value
        and approval_confirmations < 1
    ):
        raise SkillRuntimeError(f"Tool '{tool_name}' cần phê duyệt trước khi chạy.")

    if tool_name == LOAD_SKILL:
        return _run_load_skill(db, arguments)
    if tool_name == READ_SKILL_FILE:
        return _run_read_skill_file(db, arguments)
    if tool_name == RUN_SKILL_SCRIPT:
        return await _run_skill_script(db, arguments, settings)

    runner = _BACKEND_OWNED_RUNNERS.get(tool_name)
    if runner is not None:
        return await runner(arguments, settings)
    raise SkillRuntimeError(f"Tool built-in không tồn tại: '{tool_name}'.")
