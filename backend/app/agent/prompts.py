TELECOM_AGENT_PROMPT_VERSION = "0.0.3"

TELECOM_AGENT_SYSTEM_PROMPT = """
Bạn là AI Agent chuyên nghiệp cho vận hành và giám sát mạng viễn thông.
Vai trò của bạn là hỗ trợ kỹ sư phân tích log, xử lý sự cố trạm, kiểm tra trạng thái cụm/hệ thống, và đề xuất hướng xử lý tối ưu.

Quy tắc an toàn vận hành:
1. Bạn có quyền sử dụng tất cả các tool được cung cấp để đọc dữ liệu, truy vấn và kiểm tra hệ thống. Khi truy vấn SQL, nếu chưa biết rõ tên cột/bảng thì hãy truy vấn schema trước (information_schema hoặc DESCRIBE TABLE) thay vì đoán mò.
2. Với mọi thao tác có thể thay đổi trạng thái hạ tầng như restart service, clear cache, hoặc sửa cấu hình: bạn BẮT BUỘC gọi đúng tool và nêu rõ lý do để kích hoạt luồng phê duyệt của người vận hành. Không được tự ý thực hiện thao tác thay đổi trạng thái một cách ngầm định.
3. Ưu tiên phân tích nguyên nhân gốc (RCA) bằng các lệnh chỉ đọc trước khi đề xuất hoặc thực hiện bất kỳ thay đổi nào trên hệ thống.
4. Trả lời ngắn gọn, rõ ràng, có số liệu và tên node/trạm cụ thể lấy từ log hoặc dữ liệu tool. Không đoán mò.
5. Nếu thiếu dữ kiện hoặc ý định người dùng chưa rõ, hãy hỏi lại thay vì tự bịa tham số.
"""


def _split_csv(raw_value: str | None) -> list[str]:
    return [item.strip() for item in (raw_value or "").split(",") if item.strip()]


def _build_runtime_resource_context(settings=None) -> str:
    if settings is None:
        return (
            "## Tài nguyên backend đang cấu hình\n"
            "- Backend không cung cấp snapshot cấu hình runtime trong lượt gọi này. "
            "Chỉ dùng tool khi người dùng cung cấp đủ tham số cần thiết."
        )

    lines = [
        "## Tài nguyên backend đang cấu hình",
        "Danh sách này được sinh động từ cấu hình backend hiện tại, không phải tri thức tĩnh của model. "
        "Chỉ sử dụng đúng tool và node/schema được backend công bố ở đây; không tự bịa node, host, bảng hoặc credential.",
    ]

    allowed_nodes = _split_csv(getattr(settings, "SSH_ALLOWED_NODES", ""))
    ssh_host = (getattr(settings, "SSH_HOST", "") or "").strip()
    ssh_node_map = (getattr(settings, "SSH_NODE_HOST_MAP", "") or "").strip()
    ssh_configured = bool(allowed_nodes or ssh_host or ssh_node_map)
    if ssh_configured:
        lines.append("- SSH: `run_ssh_command` có cấu hình backend để kiểm tra server/node.")
        if allowed_nodes:
            lines.append(f"  Các giá trị `node_name` được phép: {', '.join(allowed_nodes)}.")
            if len(allowed_nodes) == 1:
                lines.append(
                    f"  Nếu người dùng nói chung chung như 'server tôi' hoặc 'node này' mà không nêu tên, "
                    f"dùng `node_name` là `{allowed_nodes[0]}`."
                )
            else:
                lines.append(
                    "  Nếu người dùng không nêu rõ server/node, hãy hỏi lại node cần kiểm tra trước khi gọi SSH."
                )
        elif ssh_host:
            lines.append(
                "  Backend có một SSH host mặc định; khi người dùng nói chung về server đã cấu hình, "
                "dùng `node_name` là `default`."
            )
        else:
            lines.append(
                "  Backend có map node SSH nhưng không công bố allowlist; hỏi lại `node_name` nếu người dùng chưa nêu rõ."
            )
        lines.append(
            "  Khi người dùng báo server lag/chậm, ưu tiên các lệnh chỉ đọc qua SSH như "
            "`hostname`, `uptime`, `free -m`, `df -h`, "
            "`ps -eo pid,ppid,comm,%mem,%cpu --sort=-%cpu` trước khi kết luận."
        )
        lines.append(
            "  Mỗi lần gọi `run_ssh_command` chỉ được chứa MỘT lệnh đơn. Không dùng chuỗi lệnh "
            "hoặc shell operator như `&&`, `;`, `|`, `||`, backtick, `$()`. Nếu cần nhiều phép kiểm tra, "
            "hãy gọi nhiều tool call riêng biệt. Không dùng `| head`; output dài sẽ được backend tự cắt."
        )
    else:
        lines.append("- SSH: chưa cấu hình host/node; không được nói rằng đã SSH vào server.")

    if (getattr(settings, "CLICKHOUSE_HOST", "") or "").strip():
        lines.append(
            "- ClickHouse: `query_clickhouse` đã cấu hình cho log/alarm/KPI. "
            "Nếu chưa biết bảng/cột, dùng `SHOW TABLES` hoặc `DESCRIBE TABLE <table>` trước."
        )
    else:
        lines.append("- ClickHouse: chưa cấu hình host; không tự nhận đã kiểm tra log/KPI trong ClickHouse.")

    if (getattr(settings, "EXTERNAL_POSTGRES_HOST", "") or "").strip():
        lines.append(
            "- External PostgreSQL: `query_postgres` đã cấu hình cho dữ liệu nghiệp vụ/inventory. "
            "Nếu chưa biết schema, truy vấn `information_schema.columns` trước."
        )
    else:
        lines.append("- External PostgreSQL: chưa cấu hình host; không tự nhận đã kiểm tra inventory DB.")

    return "\n".join(lines)


def build_system_prompt(ready_skills, settings=None) -> str:
    """Ghép system prompt tĩnh với catalog skill đang sẵn sàng (progressive disclosure - L1).

    ready_skills: danh sách Skill ở trạng thái 'ready' (có .name, .description).
    Chỉ chèn metadata (name + description); nội dung đầy đủ chỉ được nạp khi LLM gọi load_skill.
    """
    base = TELECOM_AGENT_SYSTEM_PROMPT.strip()
    resource_context = _build_runtime_resource_context(settings)
    if not ready_skills:
        return (
            f"{base}\n\n## Skill vận hành khả dụng\n"
            "(Chưa có custom skill nào được duyệt. Hãy chẩn đoán bằng các tool built-in.)"
            f"\n\n{resource_context}"
        )

    lines = [f"- {skill.name}: {skill.description}" for skill in ready_skills]
    catalog = "\n".join(lines)
    return (
        f"{base}\n\n## Skill vận hành khả dụng\n"
        "Mỗi skill là một gói hướng dẫn vận hành. Khi một skill phù hợp với yêu cầu, "
        "bạn BẮT BUỘC gọi `load_skill(skill_name)` để nạp đầy đủ quy trình trước khi hành động.\n"
        f"{catalog}\n\n{resource_context}"
    )
