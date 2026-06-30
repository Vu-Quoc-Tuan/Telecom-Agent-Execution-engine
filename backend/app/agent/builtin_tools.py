from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from html import escape
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.agent.safety import AgentSafetyGuard
from app.agent.tool_validation import validate_json_value_against_schema
from app.common.config_parsing import parse_node_host_map
from app.common.enums import ExecutionMode
from app.common.exceptions import SafetyViolationError, SkillRuntimeError
from app.config import settings as default_settings
from app.connectors.clickhouse import TelcoClickHouseConnector
from app.connectors.postgres import TelcoPostgresConnector
from app.connectors.ssh import TelcoSSHConnector
from app.database.repositories.skills import SkillRepository
from app.llm.schemas import LLMToolDefinition
from app.observability.redaction import DataRedactor

logger = logging.getLogger(__name__)

LOAD_SKILL = "load_skill"
READ_SKILL_FILE = "read_skill_file"
RUN_SKILL_SCRIPT = "run_skill_script"
RUN_SSH_COMMAND = "run_ssh_command"
QUERY_CLICKHOUSE = "query_clickhouse"
QUERY_POSTGRES = "query_postgres"

# Backend-owned capabilities (fixed templates, auto-run).
GET_SITE_ALARM_SUMMARY = "get_site_alarm_summary"
GET_ACTIVE_ALARMS = "get_active_alarms"
GET_SITE_KPI_SNAPSHOT = "get_site_kpi_snapshot"
GET_SITE_INVENTORY = "get_site_inventory"
GET_NODE_HEALTH_SNAPSHOT = "get_node_health_snapshot"
PING_NODE = "ping_node"

# connector_name lưu vào bảng tool_calls cho mỗi tool built-in.
_CONNECTOR_BY_TOOL = {
    LOAD_SKILL: "internal",
    READ_SKILL_FILE: "internal",
    RUN_SKILL_SCRIPT: "sandbox",
    RUN_SSH_COMMAND: "ssh",
    QUERY_CLICKHOUSE: "clickhouse",
    QUERY_POSTGRES: "external_postgres",
    GET_SITE_ALARM_SUMMARY: "clickhouse",
    GET_ACTIVE_ALARMS: "clickhouse",
    GET_SITE_KPI_SNAPSHOT: "clickhouse",
    GET_SITE_INVENTORY: "external_postgres",
    GET_NODE_HEALTH_SNAPSHOT: "ssh",
    PING_NODE: "ssh",
}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


# --- Backend-owned capability descriptors -----------------------------------
_CLICKHOUSE_CONFIGURED = "clickhouse"
_POSTGRES_CONFIGURED = "external_postgres"
_SSH_CONFIGURED = "ssh"

# lọc + cho llm đọc
_BACKEND_OWNED_CAPABILITIES: list[dict[str, str]] = [
    {
        "name": GET_SITE_ALARM_SUMMARY,
        "connector": _CLICKHOUSE_CONFIGURED,
        "description": "Tổng hợp số lượng alarm theo mức độ cho một site trong cửa sổ thời gian.",
    },
    {
        "name": GET_ACTIVE_ALARMS,
        "connector": _CLICKHOUSE_CONFIGURED,
        "description": "Liệt kê các alarm đang mở (chưa được xử lý) gần đây, lọc theo severity tuỳ chọn.",
    },
    {
        "name": GET_SITE_KPI_SNAPSHOT,
        "connector": _CLICKHOUSE_CONFIGURED,
        "description": "Snapshot KPI gần nhất của một site trong cửa sổ thời gian.",
    },
    {
        "name": GET_SITE_INVENTORY,
        "connector": _POSTGRES_CONFIGURED,
        "description": "Thông tin inventory/cấu hình của một site từ PostgreSQL nghiệp vụ.",
    },
    {
        "name": GET_NODE_HEALTH_SNAPSHOT,
        "connector": _SSH_CONFIGURED,
        "description": "Chạy bộ lệnh read-only cố định để kiểm tra nhanh sức khoẻ một node.",
    },
    {
        "name": PING_NODE,
        "connector": _SSH_CONFIGURED,
        "description": "Ping ICMP tới một node để đo kết nối/độ trễ.",
    },
]


def _connector_is_configured(connector: str, settings) -> bool:
    """
    Kiểm tra xem connector đã được cấu hình chưa -> để chọn tool nào có thể sử dụng
    """
    if settings is None:
        return False
    if connector == _CLICKHOUSE_CONFIGURED:
        return bool((getattr(settings, "CLICKHOUSE_HOST", "") or "").strip())
    if connector == _POSTGRES_CONFIGURED:
        return bool((getattr(settings, "EXTERNAL_POSTGRES_HOST", "") or "").strip())
    if connector == _SSH_CONFIGURED:
        return bool(
            (getattr(settings, "SSH_ALLOWED_NODES", "") or "").strip()
            or (getattr(settings, "SSH_HOST", "") or "").strip()
            or (getattr(settings, "SSH_NODE_HOST_MAP", "") or "").strip()
        )
    return False

# Tool mà llm có thể gọi --> chi tiết sau khi llm chọn dựa trên def
_BACKEND_OWNED_TOOL_DEFINITIONS: dict[str, LLMToolDefinition] = {
    GET_SITE_ALARM_SUMMARY: LLMToolDefinition(
        name=GET_SITE_ALARM_SUMMARY,
        description=(
            "Tổng hợp alarm theo mức độ (severity) cho một site trong N phút gần đây. "
            "Runner cố định do backend sở hữu — auto-run khi tham số hợp lệ."
        ),
        input_schema=_schema(
            {
                "site_id": {"type": "string", "description": "Định danh site cần tổng hợp alarm."},
                "window_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                    "description": "Cửa sổ thời gian (phút) tính tới hiện tại.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Số dòng tối đa trả về.",
                },
            },
            ["site_id", "window_minutes", "limit"],
        ),
    ),
    GET_ACTIVE_ALARMS: LLMToolDefinition(
        name=GET_ACTIVE_ALARMS,
        description=(
            "Liệt kê alarm đang mở (time_solved IS NULL) trong N phút gần đây, có thể lọc theo "
            "severity. Runner cố định do backend sở hữu — auto-run khi tham số hợp lệ."
        ),
        input_schema=_schema(
            {
                "window_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                    "description": "Cửa sổ thời gian (phút) tính tới hiện tại.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Số dòng tối đa trả về.",
                },
                "severity": {
                    "type": "string",
                    "description": "Tuỳ chọn: chỉ lấy alarm có severity này (vd 'critical').",
                },
            },
            ["window_minutes", "limit"],
        ),
    ),
    GET_SITE_KPI_SNAPSHOT: LLMToolDefinition(
        name=GET_SITE_KPI_SNAPSHOT,
        description=(
            "Snapshot KPI gần nhất của một site trong N phút gần đây. "
            "Runner cố định do backend sở hữu — auto-run khi tham số hợp lệ."
        ),
        input_schema=_schema(
            {
                "site_id": {"type": "string", "description": "Định danh site cần lấy KPI."},
                "window_minutes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1440,
                    "description": "Cửa sổ thời gian (phút) tính tới hiện tại.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Số dòng tối đa trả về.",
                },
            },
            ["site_id", "window_minutes", "limit"],
        ),
    ),
    GET_SITE_INVENTORY: LLMToolDefinition(
        name=GET_SITE_INVENTORY,
        description=(
            "Lấy thông tin inventory/cấu hình của một site từ PostgreSQL nghiệp vụ. "
            "Runner cố định do backend sở hữu — auto-run khi tham số hợp lệ."
        ),
        input_schema=_schema(
            {
                "site_id": {"type": "string", "description": "Định danh site cần tra inventory."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Số dòng tối đa trả về.",
                },
            },
            ["site_id", "limit"],
        ),
    ),
    GET_NODE_HEALTH_SNAPSHOT: LLMToolDefinition(
        name=GET_NODE_HEALTH_SNAPSHOT,
        description=(
            "Chạy bộ lệnh read-only cố định (hostname, uptime, free -m, df -h) trên một node "
            "để kiểm tra nhanh sức khoẻ. Runner cố định — auto-run, không cần phê duyệt."
        ),
        input_schema=_schema(
            {"node_name": {"type": "string", "description": "Tên/định danh node trạm đích."}},
            ["node_name"],
        ),
    ),
    PING_NODE: LLMToolDefinition(
        name=PING_NODE,
        description=(
            "Ping ICMP tới một node để đo kết nối/độ trễ. "
            "Runner cố định do backend sở hữu — auto-run khi tham số hợp lệ."
        ),
        input_schema=_schema(
            {
                "node_name": {"type": "string", "description": "Tên/định danh node trạm đích."},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Số gói ICMP gửi đi.",
                },
            },
            ["node_name", "count"],
        ),
    ),
}

# -------------------Skill Disclosure-------------------
_SKILL_DISCLOSURE_TOOL_DEFINITIONS: list[LLMToolDefinition] = [
    LLMToolDefinition(
        name=LOAD_SKILL,
        description=(
            "Tải toàn bộ hướng dẫn chi tiết (SKILL.md) của một Operational Skill đã được duyệt. "
            "Gọi tool này NGAY khi một skill trong danh sách khả dụng phù hợp với yêu cầu, để đọc "
            "quy trình xử lý đầy đủ trước khi hành động."
        ),
        input_schema=_schema(
            {"skill_name": {"type": "string", "description": "Tên skill cần tải hướng dẫn."}},
            ["skill_name"],
        ),
    ),
    LLMToolDefinition(
        name=READ_SKILL_FILE,
        description=(
            "Đọc nội dung một file tài nguyên/script đi kèm trong gói của một skill "
            "(ví dụ checklist, bảng tra cứu KPI). Chỉ dùng khi SKILL.md có nhắc tới file đó."
        ),
        input_schema=_schema(
            {
                "skill_name": {"type": "string", "description": "Tên skill chứa file."},
                "file_path": {"type": "string", "description": "Đường dẫn file trong gói skill."},
            },
            ["skill_name", "file_path"],
        ),
    ),
]

_RUN_SKILL_SCRIPT_DEFINITION = LLMToolDefinition(
    name=RUN_SKILL_SCRIPT,
    description=(
        "Thực thi một script Python đã được kiểm duyệt và có sẵn trong gói kỹ năng (skill). "
        "Script sẽ được chạy trong môi trường sandbox cách ly. Bạn chỉ được gọi các script "
        "có sẵn trong gói kỹ năng đó và truyền đối số (arguments) khớp với schema của script. "
        "Tuyệt đối không tự viết code Python hoặc câu lệnh shell mới tại đây."
    ),
    input_schema=_schema(
        {
            "skill_name": {"type": "string", "description": "Tên skill chứa script đã duyệt."},
            "script_path": {
                "type": "string",
                "description": "Đường dẫn script trong gói skill (vd 'scripts/check_latency.py').",
            },
            "arguments": {
                "type": "object",
                "description": (
                    "Đối số truyền cho script dưới dạng JSON object, phải khớp input_schema đã duyệt. "
                    "Script đọc từ /home/user/workspace/args.json."
                ),
            },
        },
        ["skill_name", "script_path", "arguments"],
    ),
)

_PROPOSAL_TOOL_DEFINITIONS: list[LLMToolDefinition] = [
    LLMToolDefinition(
        name=RUN_SSH_COMMAND,
        description=(
            "Gõ một lệnh shell READ-ONLY hoặc can thiệp lên một node trạm qua SSH. "
            "Mỗi tool call chỉ được chứa MỘT lệnh đơn; không dùng chuỗi lệnh hoặc shell operator "
            "như &&, ;, |, ||, backtick, hoặc $(). Nếu cần kiểm tra nhiều chỉ số, hãy gọi nhiều "
            "tool call run_ssh_command riêng biệt. "
            "Lệnh thay đổi trạng thái (restart, stop, systemctl, clear...) sẽ tự động kích hoạt "
            "luồng phê duyệt của con người (Human-in-the-loop) trước khi chạy."
        ),
        input_schema=_schema(
            {
                "node_name": {"type": "string", "description": "Tên/định danh node trạm đích."},
                "command": {
                    "type": "string",
                    "description": (
                        "Một lệnh shell đơn cần thực thi. Không được chứa &&, ;, |, ||, "
                        "backtick, $(), newline, hoặc chuỗi nhiều lệnh. Không dùng '| head'; "
                        "backend tự cắt output dài."
                    ),
                },
            },
            ["node_name", "command"],
        ),
    ),
    LLMToolDefinition(
        name=QUERY_CLICKHOUSE,
        description=(
            "Truy vấn SQL vào kho log/alarm tập trung trên ClickHouse. "
            "SELECT/SHOW/DESCRIBE chạy ngay; câu lệnh thay đổi dữ liệu hoặc schema "
            "sẽ yêu cầu người vận hành phê duyệt trước khi chạy. "
            "Nếu chưa biết cấu trúc bảng hoặc tên cột, hãy truy vấn 'DESCRIBE TABLE <table_name>' hoặc 'SHOW TABLES' trước."
        ),
        input_schema=_schema(
            {"sql": {"type": "string", "description": "Một câu lệnh SQL ClickHouse."}},
            ["sql"],
        ),
    ),
    LLMToolDefinition(
        name=QUERY_POSTGRES,
        description=(
            "Truy vấn SQL vào cơ sở dữ liệu PostgreSQL nghiệp vụ ngoài "
            "(thông tin cấu hình trạm, inventory). SELECT chạy trong transaction read-only; "
            "câu lệnh thay đổi dữ liệu hoặc schema cần người vận hành phê duyệt. "
            "Nếu chưa biết cấu trúc bảng hoặc tên cột, hãy truy vấn từ 'information_schema.columns' để có cấu trúc chính xác."
        ),
        input_schema=_schema(
            {"sql": {"type": "string", "description": "Một câu lệnh SQL PostgreSQL."}},
            ["sql"],
        ),
    ),
]


BUILTIN_TOOL_DEFINITIONS: list[LLMToolDefinition] = [
    # skill tools
    *_SKILL_DISCLOSURE_TOOL_DEFINITIONS,
    _RUN_SKILL_SCRIPT_DEFINITION,
    # proposal tools
    *_PROPOSAL_TOOL_DEFINITIONS,
    # backend owned tools
    *_BACKEND_OWNED_TOOL_DEFINITIONS.values(),
]

BUILTIN_TOOL_NAMES = {tool.name for tool in BUILTIN_TOOL_DEFINITIONS}


def build_builtin_tool_definitions(
    ready_skills,
    *,
    sandbox_available: bool = False,
    settings=None,
) -> list[LLMToolDefinition]:
    """Build the per-request tool catalog.

    - load_skill / read_skill_file / run_skill_script chỉ xuất hiện khi có skill ready.
    - run_skill_script còn cần sandbox.
    - Backend-owned capability tool chỉ xuất hiện khi connector tương ứng đã cấu hình.
    """
    ready_names = sorted({skill.name for skill in ready_skills})

    tools: list[LLMToolDefinition] = []
    if ready_names:
        tools.extend(deepcopy(tool) for tool in _SKILL_DISCLOSURE_TOOL_DEFINITIONS)
        if sandbox_available:
            tools.append(deepcopy(_RUN_SKILL_SCRIPT_DEFINITION))
    tools.extend(deepcopy(tool) for tool in _PROPOSAL_TOOL_DEFINITIONS)
    for capability in _BACKEND_OWNED_CAPABILITIES:
        if _connector_is_configured(capability["connector"], settings):
            tools.append(deepcopy(_BACKEND_OWNED_TOOL_DEFINITIONS[capability["name"]]))

    if ready_names:
        for tool in tools:
            if tool.name in {LOAD_SKILL, READ_SKILL_FILE}:
                tool.input_schema["properties"]["skill_name"]["enum"] = ready_names
    return tools


def list_backend_owned_capabilities(settings) -> list[dict[str, str]]:
    """
    Liệt kê các connector đã cấu hình.
    """
    available = [
        dict(capability)
        for capability in _BACKEND_OWNED_CAPABILITIES
        if _connector_is_configured(capability["connector"], settings)
    ]
    return sorted(available, key=lambda item: item["name"])


def connector_name_for(tool_name: str) -> str:
    return _CONNECTOR_BY_TOOL.get(tool_name, "internal")


def classify_builtin_risk(tool_name: str, arguments: dict[str, Any]) -> str:
    """Return one of the two execution modes used by the runtime router."""
    if tool_name not in BUILTIN_TOOL_NAMES:
        raise SkillRuntimeError(f"Tool built-in không tồn tại: '{tool_name}'.")
    if tool_name == RUN_SSH_COMMAND:
        # Loại 3 (ad-hoc do model tự sinh): chạy guard để CHẶN payload cấm
        AgentSafetyGuard.classify_ssh_command(str(arguments.get("command", "")))
        return ExecutionMode.REQUIRE_APPROVAL.value
    if tool_name in {QUERY_CLICKHOUSE, QUERY_POSTGRES}:
        # Tương tự: validate để chặn SQL cấm, nhưng mọi SQL do model sinh (kể cả
        # SELECT read-only) đều phải qua phê duyệt.
        AgentSafetyGuard.classify_sql(str(arguments.get("sql", "")))
        return ExecutionMode.REQUIRE_APPROVAL.value
    # Backend-owned capabilities + run_skill_script + disclosure tools đều auto-run:
    return ExecutionMode.AUTO_EXECUTE.value


def required_approval_confirmations(tool_name: str, arguments: dict[str, Any]) -> int:
    mode = classify_builtin_risk(tool_name, arguments)
    if mode == ExecutionMode.AUTO_EXECUTE.value:
        return 0
    if tool_name == RUN_SSH_COMMAND:
        confirmations = AgentSafetyGuard.required_ssh_confirmations(
            str(arguments.get("command", ""))
        )
        return max(1, confirmations)
    return 1


def _allowed_nodes(settings) -> set[str]:
    raw = getattr(settings, "SSH_ALLOWED_NODES", "") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _node_host_map(settings) -> dict[str, str]:
    raw = getattr(settings, "SSH_NODE_HOST_MAP", "") or ""
    return parse_node_host_map(raw)


def _prepare_tool_output(output: str) -> tuple[str, bool]:
    return AgentSafetyGuard.truncate_output(DataRedactor.redact_text(output))


def _resolve_ssh_host(settings, target_node: str, allowed_nodes: set[str]) -> str:
    mapped_host = _node_host_map(settings).get(target_node)
    if mapped_host:
        return mapped_host
    if allowed_nodes and target_node:
        return target_node
    return getattr(settings, "SSH_HOST", "") or target_node


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


def _resolve_capability_node(settings, target_node: str) -> str:
    allowed = _allowed_nodes(settings)
    if allowed and target_node not in allowed:
        raise SkillRuntimeError(f"Node '{target_node}' không nằm trong SSH_ALLOWED_NODES.")
    host = _resolve_ssh_host(settings, target_node, allowed)
    if not host:
        raise SkillRuntimeError("Chưa cấu hình SSH host và không có node_name.")
    return host


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


def _require_approval_count(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    approval_confirmations: int,
) -> None:
    required = required_approval_confirmations(tool_name, arguments)
    if approval_confirmations < required:
        raise SafetyViolationError(
            f"Tool '{tool_name}' requires {required} approval confirmation(s) before execution.",
            details={"required_confirmations": required},
        )


async def _run_ssh_command(
    arguments: dict[str, Any],
    settings,
    *,
    approval_confirmations: int = 0,
) -> tuple[str, bool]:
    target_node = str(arguments.get("node_name", "")).strip()
    command = AgentSafetyGuard.normalize_ssh_command(str(arguments.get("command", "")))
    host = _resolve_capability_node(settings, target_node)

    connector = _build_ssh_connector(settings, host)
    try:
        if approval_confirmations:
            stdout, stderr = await connector.execute_command(
                command,
                approval_confirmations=approval_confirmations,
            )
        else:
            stdout, stderr = await connector.execute_command(command)
    finally:
        connector.close()
    combined = stdout if not stderr else f"{stdout}\n[STDERR]\n{stderr}"
    return _prepare_tool_output(combined)


async def _run_query_clickhouse(
    arguments: dict[str, Any],
    settings,
    *,
    approval_confirmations: int = 0,
) -> tuple[str, bool]:
    sql = str(arguments.get("sql", ""))
    _require_approval_count(
        tool_name=QUERY_CLICKHOUSE,
        arguments=arguments,
        approval_confirmations=approval_confirmations,
    )
    allow_mutation = AgentSafetyGuard.classify_sql(sql) == ExecutionMode.REQUIRE_APPROVAL
    connector = _build_clickhouse_connector(settings)
    try:
        rows = await connector.execute(sql, allow_mutation=allow_mutation)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


async def _run_query_postgres(
    arguments: dict[str, Any],
    settings,
    *,
    approval_confirmations: int = 0,
) -> tuple[str, bool]:
    sql = str(arguments.get("sql", ""))
    _require_approval_count(
        tool_name=QUERY_POSTGRES,
        arguments=arguments,
        approval_confirmations=approval_confirmations,
    )
    read_only = AgentSafetyGuard.classify_sql(sql) == ExecutionMode.AUTO_EXECUTE
    connector = _build_postgres_connector(settings, read_only=read_only)
    try:
        rows = await connector.query(sql)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


# --- Backend-owned capability runners ---------------------------------------
# SQL/SSH cố định, tham số hoá 100% — LLM không bao giờ chèn được chuỗi tự do.


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


async def _run_get_site_alarm_summary(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params = {
        "site_id": str(arguments.get("site_id", "")),
        "window_minutes": int(arguments.get("window_minutes", 60)),
        "limit": int(arguments.get("limit", 100)),
    }
    sql = (
        "SELECT severity, count() AS alarm_count "
        "FROM alarms "
        "WHERE site_id = {site_id:String} "
        "AND time_created >= now() - INTERVAL {window_minutes:UInt32} MINUTE "
        "GROUP BY severity "
        "ORDER BY alarm_count DESC "
        "LIMIT {limit:UInt32}"
    )
    return await _run_clickhouse_template(settings, sql, params)


async def _run_get_active_alarms(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params: dict[str, Any] = {
        "window_minutes": int(arguments.get("window_minutes", 60)),
        "limit": int(arguments.get("limit", 100)),
    }
    severity = arguments.get("severity")
    where_clauses = [
        "time_solved IS NULL",
        "time_created >= now() - INTERVAL {window_minutes:UInt32} MINUTE",
    ]
    if severity:
        params["severity"] = str(severity)
        where_clauses.append("severity = {severity:String}")
    sql = (
        "SELECT * FROM alarms "
        f"WHERE {' AND '.join(where_clauses)} "
        "ORDER BY time_created DESC "
        "LIMIT {limit:UInt32}"
    )
    return await _run_clickhouse_template(settings, sql, params)


async def _run_get_site_kpi_snapshot(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params = {
        "site_id": str(arguments.get("site_id", "")),
        "window_minutes": int(arguments.get("window_minutes", 60)),
        "limit": int(arguments.get("limit", 100)),
    }
    sql = (
        "SELECT * FROM kpi_snapshots "
        "WHERE site_id = {site_id:String} "
        "AND time_created >= now() - INTERVAL {window_minutes:UInt32} MINUTE "
        "ORDER BY time_created DESC "
        "LIMIT {limit:UInt32}"
    )
    return await _run_clickhouse_template(settings, sql, params)


async def _run_get_site_inventory(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    params = {
        "site_id": str(arguments.get("site_id", "")),
        "limit": int(arguments.get("limit", 100)),
    }
    sql = (
        "SELECT * FROM site_inventory WHERE site_id = %(site_id)s "
        "ORDER BY updated_at DESC LIMIT %(limit)s"
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
    script_arguments = arguments.get("arguments") or {}
    if not isinstance(script_arguments, dict):
        raise SkillRuntimeError("Tham số 'arguments' của run_skill_script phải là JSON object.")

    skill = _load_ready_skill(db, skill_name)
    entry = _approved_manifest_entry(skill, script_path)

    # Cổng 1: hash file phải khớp bản đã duyệt.
    content = _bundled_script_content(skill, script_path)
    _verify_script_hash(entry, content)

    # Cổng 2: tham số phải khớp input_schema đã duyệt.
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
    if tool_name == LOAD_SKILL:
        return _run_load_skill(db, arguments)
    if tool_name == READ_SKILL_FILE:
        return _run_read_skill_file(db, arguments)
    if tool_name == RUN_SKILL_SCRIPT:
        return await _run_skill_script(db, arguments, settings)
    if tool_name == RUN_SSH_COMMAND:
        return await _run_ssh_command(
            arguments,
            settings,
            approval_confirmations=approval_confirmations,
        )
    if tool_name == QUERY_CLICKHOUSE:
        return await _run_query_clickhouse(
            arguments,
            settings,
            approval_confirmations=approval_confirmations,
        )
    if tool_name == QUERY_POSTGRES:
        return await _run_query_postgres(
            arguments,
            settings,
            approval_confirmations=approval_confirmations,
        )
    runner = _BACKEND_OWNED_RUNNERS.get(tool_name)
    if runner is not None:
        return await runner(arguments, settings)
    raise SkillRuntimeError(f"Tool built-in không tồn tại: '{tool_name}'.")
