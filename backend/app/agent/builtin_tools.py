from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.common.enums import ExecutionMode
from app.common.exceptions import SkillRuntimeError
from app.llm.schemas import LLMToolDefinition

LOAD_SKILL = "load_skill"
READ_SKILL_FILE = "read_skill_file"
RUN_SKILL_SCRIPT = "run_skill_script"

# Backend-owned capabilities (fixed templates, auto-run).
GET_SITE_ALARM_SUMMARY = "get_site_alarm_summary"
GET_ACTIVE_ALARMS = "get_active_alarms"
GET_SITE_KPI_SNAPSHOT = "get_site_kpi_snapshot"
GET_SITE_INVENTORY = "get_site_inventory"
GET_NODE_HEALTH_SNAPSHOT = "get_node_health_snapshot"
PING_NODE = "ping_node"
RESTART_SERVICE = "restart_service"

# connector_name lưu vào bảng tool_calls cho mỗi tool built-in.
_CONNECTOR_BY_TOOL = {
    LOAD_SKILL: "internal",
    READ_SKILL_FILE: "internal",
    RUN_SKILL_SCRIPT: "sandbox",
    GET_SITE_ALARM_SUMMARY: "clickhouse",
    GET_ACTIVE_ALARMS: "clickhouse",
    GET_SITE_KPI_SNAPSHOT: "clickhouse",
    GET_SITE_INVENTORY: "external_postgres",
    GET_NODE_HEALTH_SNAPSHOT: "ssh",
    PING_NODE: "ssh",
    RESTART_SERVICE: "ssh",
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
_SSH_RESTART_CONFIGURED = "ssh_restart"
_SAFE_RESTART_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")


def _allowed_restart_services(settings) -> set[str]:
    raw = getattr(settings, "SSH_RESTART_ALLOWED_SERVICES", "") or ""
    return {
        item.strip()
        for item in raw.split(",")
        if item.strip() and _SAFE_RESTART_SERVICE_PATTERN.fullmatch(item.strip())
    }


# lọc + cho llm đọc -> name
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
    {
        "name": RESTART_SERVICE,
        "connector": _SSH_RESTART_CONFIGURED,
        "description": "Restart một service trong allowlist trên node qua SSH, cần phê duyệt HITL.",
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
    if connector == _SSH_RESTART_CONFIGURED:
        return _connector_is_configured(_SSH_CONFIGURED, settings) and bool(
            _allowed_restart_services(settings)
        )
    return False


# Tool mà llm có thể gọi (đưa vào context là biết có thể gọi luôn)
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
        strict=False,
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
    RESTART_SERVICE: LLMToolDefinition(
        name=RESTART_SERVICE,
        description=(
            "Restart một service đã nằm trong allowlist trên node qua SSH. "
            "Tool này thay đổi trạng thái hệ thống nên luôn cần người vận hành phê duyệt trước khi chạy. "
            "Không dùng để chạy shell command tự do."
        ),
        input_schema=_schema(
            {
                "node_name": {"type": "string", "description": "Tên/định danh node trạm đích."},
                "service_name": {
                    "type": "string",
                    "description": "Tên service nằm trong SSH_RESTART_ALLOWED_SERVICES.",
                },
            },
            ["node_name", "service_name"],
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
    strict=False,
)

BUILTIN_TOOL_DEFINITIONS: list[LLMToolDefinition] = [
    # skill tools
    *_SKILL_DISCLOSURE_TOOL_DEFINITIONS,
    _RUN_SKILL_SCRIPT_DEFINITION,
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
    for capability in _BACKEND_OWNED_CAPABILITIES:
        if _connector_is_configured(capability["connector"], settings):
            tools.append(deepcopy(_BACKEND_OWNED_TOOL_DEFINITIONS[capability["name"]]))

    if ready_names:
        for tool in tools:
            if tool.name in {LOAD_SKILL, READ_SKILL_FILE, RUN_SKILL_SCRIPT}:
                tool.input_schema["properties"]["skill_name"]["enum"] = ready_names
    restart_services = sorted(_allowed_restart_services(settings))
    if restart_services:
        for tool in tools:
            if tool.name == RESTART_SERVICE:
                tool.input_schema["properties"]["service_name"]["enum"] = restart_services
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
    # Validate arguments against the tool's input schema if available
    tool_def = next((t for t in BUILTIN_TOOL_DEFINITIONS if t.name == tool_name), None)
    if tool_def and isinstance(tool_def.input_schema, dict):
        from app.agent.tool_validation import validate_json_value_against_schema
        validate_json_value_against_schema(value=arguments, schema=tool_def.input_schema, path=tool_name)
    if tool_name == RESTART_SERVICE:
        return ExecutionMode.REQUIRE_APPROVAL.value
    return ExecutionMode.AUTO_EXECUTE.value
