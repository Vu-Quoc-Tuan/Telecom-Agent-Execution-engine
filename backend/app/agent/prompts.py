from app.agent.builtin_tools import list_backend_owned_capabilities
from app.observability.langfuse import telemetry_tracker

TELECOM_AGENT_PROMPT_VERSION = "0.1.0"


TELECOM_AGENT_SYSTEM_PROMPT = """Bạn là AI Agent vận hành mạng viễn thông.
{{skill_section}}
{{resource_context}}"""


def _split_csv(raw_value: str | None) -> list[str]:
    return [item.strip() for item in (raw_value or "").split(",") if item.strip()]


def _build_runtime_resource_context(settings=None) -> str:
    if settings is None:
        return (
            "## Tài nguyên backend đang cấu hình\n- Chưa nhận diện được cấu hình cấu hình runtime."
        )

    lines = ["## Tài nguyên backend đang cấu hình"]

    # Capabilities
    caps = list_backend_owned_capabilities(settings)
    if caps:
        lines.append(
            "- Capabilities built-in khả dụng: " + ", ".join(f"`{item['name']}`" for item in caps)
        )
    else:
        lines.append("- Capabilities built-in khả dụng: Không có.")

    # SSH
    allowed_nodes = _split_csv(getattr(settings, "SSH_ALLOWED_NODES", ""))
    ssh_host = (getattr(settings, "SSH_HOST", "") or "").strip()
    ssh_node_map = (getattr(settings, "SSH_NODE_HOST_MAP", "") or "").strip()
    if allowed_nodes or ssh_host or ssh_node_map:
        nodes_str = f"cho phép: {', '.join(allowed_nodes)}" if allowed_nodes else "host mặc định"
        lines.append(f"- SSH: Khả dụng ({nodes_str}).")
    else:
        lines.append("- SSH: Chưa cấu hình.")

    # ClickHouse
    ch_host = (getattr(settings, "CLICKHOUSE_HOST", "") or "").strip()
    lines.append(f"- ClickHouse: {'Khả dụng' if ch_host else 'Chưa cấu hình'}.")

    # PostgreSQL
    pg_host = (getattr(settings, "EXTERNAL_POSTGRES_HOST", "") or "").strip()
    lines.append(f"- External PostgreSQL: {'Khả dụng' if pg_host else 'Chưa cấu hình'}.")

    # Sandbox
    from app.sandbox.docker_executor import sandbox_available

    lines.append(
        f"- Sandbox Python: {'Khả dụng' if sandbox_available(settings) else 'Chưa khả dụng'}."
    )

    return "\n".join(lines)


def _build_skill_section(ready_skills, selected_skill_name: str | None = None) -> str:
    """Dựng phần catalog skill (progressive disclosure - L1) chèn vào prompt.

    Chỉ chèn metadata (name + description); nội dung đầy đủ chỉ được nạp khi LLM gọi load_skill.
    """
    if not ready_skills:
        return "## Skill vận hành khả dụng\n- Chưa có custom skill nào được duyệt."

    catalog = "\n".join(f"- {skill.name}: {skill.description}" for skill in ready_skills)

    instructions = (
        "\n\nQUAN TRỌNG: Bạn KHÔNG ĐƯỢC gọi trực tiếp tên skill (ví dụ: `noc-alarm-enrichment`) như một tool call. "
        "Tên skill KHÔNG PHẢI là tên tool khả dụng để gọi trực tiếp.\n"
        "Để sử dụng hoặc chạy bất kỳ skill nào, bạn PHẢI tuân thủ quy trình sau:\n"
        "1. Trước tiên, gọi tool `load_skill` với tham số `skill_name` để tải tài liệu hướng dẫn (SKILL.md) và danh sách tài nguyên/script của skill đó.\n"
        "2. Sau khi đã đọc hướng dẫn từ kết quả trả về của `load_skill`, nếu cần chạy script, hãy sử dụng tool `run_skill_script` với các đối số tương ứng (như `skill_name`, `script_path`, và `arguments`)."
    )

    sel = f"\nChỉ định nạp skill: `{selected_skill_name}`." if selected_skill_name else ""
    return f"## Skill vận hành khả dụng\n{catalog}{instructions}{sel}"


def build_system_prompt(ready_skills, settings=None, selected_skill_name: str | None = None) -> str:
    """Dựng system prompt: lấy khung tĩnh từ Langfuse rồi compile phần động ở local.

    Khung tĩnh được quản lý trên Langfuse Prompt Management; hai placeholder
    `{{skill_section}}` và `{{resource_context}}` được thay bằng dữ liệu động tính
    từ skill catalog (DB) và cấu hình backend (settings). Nếu Langfuse không khả dụng
    thì rơi về fallback template cứng (TELECOM_AGENT_SYSTEM_PROMPT).
    """
    resource_context = _build_runtime_resource_context(settings)
    skill_section = _build_skill_section(ready_skills, selected_skill_name)

    prompt_obj = telemetry_tracker.get_system_prompt(TELECOM_AGENT_SYSTEM_PROMPT)
    if prompt_obj is not None:
        try:
            return prompt_obj.compile(
                resource_context=resource_context,
                skill_section=skill_section,
            ).strip()
        except Exception:
            pass  # Lỗi compile -> rơi về fallback cứng bên dưới.

    return (
        TELECOM_AGENT_SYSTEM_PROMPT.replace("{{resource_context}}", resource_context)
        .replace("{{skill_section}}", skill_section)
        .strip()
    )
