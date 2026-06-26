"""Bộ công cụ built-in cố định cho Telecom Agent (mô hình Agent Skills hybrid).

Skill (gói SKILL.md) chỉ là *tri thức/hướng dẫn*. Việc gõ trạm thật do các tool
built-in trong file này đảm nhận: chúng có schema tĩnh (tương thích strict), có chính
sách phân loại rủi ro để kích hoạt HITL, và dispatcher chạy in-process (code tin cậy).

Hai tool đầu phục vụ progressive disclosure:
- load_skill: trả về full body SKILL.md (L2).
- read_skill_file: trả về một file kèm trong gói skill (L3).
"""

from __future__ import annotations

import json
from copy import deepcopy
from html import escape
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.agent.safety import AgentSafetyGuard
from app.common.enums import RiskLevel
from app.common.exceptions import SafetyViolationError, SkillRuntimeError
from app.config import settings as default_settings
from app.connectors.clickhouse import TelcoClickHouseConnector
from app.connectors.postgres import TelcoPostgresConnector
from app.connectors.ssh import TelcoSSHConnector
from app.database.repositories.skills import SkillRepository
from app.llm.schemas import LLMToolDefinition
from app.observability.redaction import DataRedactor

LOAD_SKILL = "load_skill"
READ_SKILL_FILE = "read_skill_file"
RUN_SSH_COMMAND = "run_ssh_command"
QUERY_CLICKHOUSE = "query_clickhouse"
QUERY_POSTGRES = "query_postgres"

# connector_name lưu vào bảng tool_calls cho mỗi tool built-in.
_CONNECTOR_BY_TOOL = {
    LOAD_SKILL: "internal",
    READ_SKILL_FILE: "internal",
    RUN_SSH_COMMAND: "ssh",
    QUERY_CLICKHOUSE: "clickhouse",
    QUERY_POSTGRES: "external_postgres",
}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    # strict-compatible: mọi property nằm trong required + additionalProperties=False.
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


BUILTIN_TOOL_DEFINITIONS: list[LLMToolDefinition] = [
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
            "Truy vấn SQL READ-ONLY vào kho log/alarm tập trung trên ClickHouse "
            "để phân tích KPI, cảnh báo, throughput, latency của trạm. "
            "Nếu chưa biết cấu trúc bảng hoặc tên cột, hãy truy vấn 'DESCRIBE TABLE <table_name>' hoặc 'SHOW TABLES' trước."
        ),
        input_schema=_schema(
            {"sql": {"type": "string", "description": "Câu lệnh SELECT ClickHouse."}},
            ["sql"],
        ),
    ),
    LLMToolDefinition(
        name=QUERY_POSTGRES,
        description=(
            "Truy vấn SQL READ-ONLY vào cơ sở dữ liệu PostgreSQL nghiệp vụ ngoài "
            "(thông tin cấu hình trạm, inventory). Giao dịch luôn ở chế độ read-only. "
            "Nếu chưa biết cấu trúc bảng hoặc tên cột, hãy truy vấn từ 'information_schema.columns' để có cấu trúc chính xác."
        ),
        input_schema=_schema(
            {"sql": {"type": "string", "description": "Câu lệnh SELECT PostgreSQL."}},
            ["sql"],
        ),
    ),
]

BUILTIN_TOOL_NAMES = {tool.name for tool in BUILTIN_TOOL_DEFINITIONS}


def build_builtin_tool_definitions(ready_skills) -> list[LLMToolDefinition]:
    """Build the per-request tool catalog and constrain skill names to real records."""
    ready_names = sorted({skill.name for skill in ready_skills})
    tools = [
        deepcopy(tool)
        for tool in BUILTIN_TOOL_DEFINITIONS
        if ready_names or tool.name not in {LOAD_SKILL, READ_SKILL_FILE}
    ]
    if ready_names:
        for tool in tools:
            if tool.name in {LOAD_SKILL, READ_SKILL_FILE}:
                tool.input_schema["properties"]["skill_name"]["enum"] = ready_names
    return tools


def connector_name_for(tool_name: str) -> str:
    return _CONNECTOR_BY_TOOL.get(tool_name, "internal")


def classify_builtin_risk(tool_name: str, arguments: dict[str, Any]) -> str:
    """Phân loại rủi ro để router quyết định có cần HITL hay không."""
    if tool_name not in BUILTIN_TOOL_NAMES:
        return RiskLevel.PROHIBITED.value
    if tool_name == RUN_SSH_COMMAND:
        return AgentSafetyGuard.classify_ssh_command(str(arguments.get("command", ""))).value
    if tool_name == QUERY_CLICKHOUSE:
        is_read_only, _ = AgentSafetyGuard.verify_read_only_sql(str(arguments.get("sql", "")))
        return RiskLevel.READ_ONLY.value if is_read_only else RiskLevel.PROHIBITED.value
    if tool_name == QUERY_POSTGRES:
        is_read_only, _ = AgentSafetyGuard.verify_read_only_sql(str(arguments.get("sql", "")))
        return RiskLevel.READ_ONLY.value if is_read_only else RiskLevel.PROHIBITED.value
    # load_skill và read_skill_file có rào chắn chỉ đọc riêng.
    return RiskLevel.READ_ONLY.value


def _allowed_nodes(settings) -> set[str]:
    raw = getattr(settings, "SSH_ALLOWED_NODES", "") or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _node_host_map(settings) -> dict[str, str]:
    raw = getattr(settings, "SSH_NODE_HOST_MAP", "") or ""
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        node_name, host = item.split("=", 1)
        node_name = node_name.strip()
        host = host.strip()
        if node_name and host:
            mapping[node_name] = host
    return mapping


def _prepare_tool_output(output: str) -> tuple[str, bool]:
    return AgentSafetyGuard.truncate_output(DataRedactor.redact_text(output))


def _resolve_ssh_host(settings, target_node: str, allowed_nodes: set[str]) -> str:
    mapped_host = _node_host_map(settings).get(target_node)
    if mapped_host:
        return mapped_host
    if allowed_nodes and target_node:
        return target_node
    return getattr(settings, "SSH_HOST", "") or target_node


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


def _run_read_skill_file(db: Session, arguments: dict[str, Any]) -> tuple[str, bool]:
    skill_name = str(arguments.get("skill_name", "")).strip()
    file_path = str(arguments.get("file_path", "")).strip()
    normalized_path = PurePosixPath(file_path)
    if (
        not file_path
        or normalized_path.is_absolute()
        or any(part in {"", ".", ".."} for part in normalized_path.parts)
        or "\\" in file_path
    ):
        raise SkillRuntimeError("Đường dẫn resource không hợp lệ.")
    file_path = normalized_path.as_posix()
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


async def _run_ssh_command(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    target_node = str(arguments.get("node_name", "")).strip()
    command = AgentSafetyGuard.normalize_ssh_command(str(arguments.get("command", "")))
    allowed = _allowed_nodes(settings)
    if allowed and target_node not in allowed:
        raise SkillRuntimeError(f"Node '{target_node}' không nằm trong SSH_ALLOWED_NODES.")
    host = _resolve_ssh_host(settings, target_node, allowed)
    if not host:
        raise SkillRuntimeError("Chưa cấu hình SSH host và không có node_name.")

    connector = TelcoSSHConnector(
        host=host,
        username=getattr(settings, "SSH_USER", ""),
        password=getattr(settings, "SSH_PASSWORD", ""),
        port=getattr(settings, "SSH_PORT", 22),
        timeout=getattr(settings, "SSH_TIMEOUT_SECONDS", 30),
        known_hosts_path=getattr(settings, "SSH_KNOWN_HOSTS", "") or None,
        auto_add_host_keys=getattr(settings, "SSH_AUTO_ADD_HOST_KEYS", False),
    )
    try:
        stdout, stderr = await connector.execute_command(command)
    finally:
        connector.close()
    combined = stdout if not stderr else f"{stdout}\n[STDERR]\n{stderr}"
    return _prepare_tool_output(combined)


async def _run_query_clickhouse(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    sql = str(arguments.get("sql", ""))
    is_read_only, error_message = AgentSafetyGuard.verify_read_only_sql(sql)
    if not is_read_only:
        raise SafetyViolationError(error_message or "ClickHouse query is not read-only.")
    connector = TelcoClickHouseConnector(
        host=getattr(settings, "CLICKHOUSE_HOST", ""),
        port=getattr(settings, "CLICKHOUSE_PORT", 8123),
        username=getattr(settings, "CLICKHOUSE_USER", ""),
        password=getattr(settings, "CLICKHOUSE_PASSWORD", ""),
        database=getattr(settings, "CLICKHOUSE_DATABASE", "default"),
        timeout_seconds=getattr(settings, "EXTERNAL_CONNECTOR_TIMEOUT_SECONDS", 15),
        max_result_rows=getattr(settings, "QUERY_MAX_RESULT_ROWS", 1000),
    )
    try:
        rows = await connector.query(sql)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


async def _run_query_postgres(arguments: dict[str, Any], settings) -> tuple[str, bool]:
    sql = str(arguments.get("sql", ""))
    is_read_only, error_message = AgentSafetyGuard.verify_read_only_sql(sql)
    if not is_read_only:
        raise SafetyViolationError(error_message or "PostgreSQL query is not read-only.")
    connector = TelcoPostgresConnector(
        host=getattr(settings, "EXTERNAL_POSTGRES_HOST", ""),
        port=getattr(settings, "EXTERNAL_POSTGRES_PORT", 5432),
        username=getattr(settings, "EXTERNAL_POSTGRES_USER", ""),
        password=getattr(settings, "EXTERNAL_POSTGRES_PASSWORD", ""),
        database=getattr(settings, "EXTERNAL_POSTGRES_DATABASE", "postgres"),
        read_only=True,
        timeout_seconds=getattr(settings, "EXTERNAL_CONNECTOR_TIMEOUT_SECONDS", 15),
        max_result_rows=getattr(settings, "QUERY_MAX_RESULT_ROWS", 1000),
    )
    try:
        rows = await connector.query(sql)
    finally:
        connector.close()
    return _prepare_tool_output(json.dumps(rows, ensure_ascii=False, default=str))


async def execute_builtin_tool(
    tool_name: str,
    arguments: dict[str, Any],
    db: Session,
    settings=default_settings,
) -> tuple[str, bool]:
    """Dispatch một tool built-in. Trả về (output, was_truncated).

    Mọi lỗi nghiệp vụ/hạ tầng được ném dưới dạng TelecomAgentException để node
    execute_tools ghi nhận failed và phản hồi lại cho LLM đọc tiếp.
    """
    if tool_name == LOAD_SKILL:
        return _run_load_skill(db, arguments)
    if tool_name == READ_SKILL_FILE:
        return _run_read_skill_file(db, arguments)
    if tool_name == RUN_SSH_COMMAND:
        return await _run_ssh_command(arguments, settings)
    if tool_name == QUERY_CLICKHOUSE:
        return await _run_query_clickhouse(arguments, settings)
    if tool_name == QUERY_POSTGRES:
        return await _run_query_postgres(arguments, settings)
    raise SkillRuntimeError(f"Tool built-in không tồn tại: '{tool_name}'.")
