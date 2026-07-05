"""Seed/cập nhật system prompt lên Langfuse Prompt Management.

Đẩy system prompt của agent, context compactor và skill domain judge lên Langfuse
rồi gán label production. Chạy lại sẽ tạo version mới mà không phá version cũ.

Cách chạy (cần LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST trong môi trường):
    python backend/scripts/seed_langfuse_prompt.py
"""

from app.agent.prompts import (
    CONTEXT_COMPACTOR_SYSTEM_PROMPT,
    TELECOM_AGENT_PROMPT_VERSION,
    TELECOM_AGENT_SYSTEM_PROMPT,
)
from app.config import settings
from app.observability.langfuse import (
    CONTEXT_COMPACTOR_PROMPT_NAME,
    PROMPT_NAME,
    SKILL_DOMAIN_JUDGE_PROMPT_NAME,
)

SKILL_DOMAIN_JUDGE_MANAGED_PROMPT = """Bạn là chuyên gia kiểm toán quy trình và kiến trúc mạng viễn thông cấp cao.
Hãy đánh giá Skill sau có thực sự phục vụ vận hành hoặc xử lý sự cố mạng viễn thông không.

- Tên hàm: {{name}}
- Mô tả: {{description}}
- Mã nguồn Python:
{{code_text}}

Chấm điểm từ 0.0 (không liên quan) đến 1.0 (hoàn toàn phục vụ viễn thông).
"""


def seed_prompt() -> None:
    if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
        print("LANGFUSE credentials chưa được cấu hình. Bỏ qua seed.")
        return

    try:
        from langfuse import Langfuse
    except Exception as exc:  # noqa: BLE001
        print(f"Langfuse SDK không khả dụng: {exc}")
        return

    client = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
    )

    label = settings.LANGFUSE_PROMPT_LABEL
    prompts = (
        (
            PROMPT_NAME,
            TELECOM_AGENT_SYSTEM_PROMPT.strip(),
            {"fallback_version": TELECOM_AGENT_PROMPT_VERSION},
        ),
        (
            CONTEXT_COMPACTOR_PROMPT_NAME,
            CONTEXT_COMPACTOR_SYSTEM_PROMPT.strip(),
            None,
        ),
        (SKILL_DOMAIN_JUDGE_PROMPT_NAME, SKILL_DOMAIN_JUDGE_MANAGED_PROMPT.strip(), None),
    )
    for prompt_name, prompt_text, config in prompts:
        print(f"Seeding prompt '{prompt_name}' (label={label}) lên {settings.LANGFUSE_HOST} ...")
        create_args = {
            "name": prompt_name,
            "type": "text",
            "prompt": prompt_text,
            "labels": [label],
        }
        if config is not None:
            create_args["config"] = config
        client.create_prompt(**create_args)

    client.flush()
    print("Done. Langfuse tự gán label 'latest' cho version mới nhất.")


if __name__ == "__main__":
    seed_prompt()
