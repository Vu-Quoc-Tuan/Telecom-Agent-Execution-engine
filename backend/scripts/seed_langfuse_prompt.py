"""Seed/cập nhật system prompt lên Langfuse Prompt Management.

Đẩy khung prompt tĩnh (TELECOM_AGENT_SYSTEM_PROMPT, có placeholder {{skill_section}}
và {{resource_context}}) lên Langfuse với tên PROMPT_NAME và gán label production.
Chạy lại sẽ tạo một version mới (idempotent theo nghĩa không phá version cũ).

Cách chạy (cần LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST trong môi trường):
    python backend/scripts/seed_langfuse_prompt.py
"""

from app.agent.prompts import TELECOM_AGENT_PROMPT_VERSION, TELECOM_AGENT_SYSTEM_PROMPT
from app.config import settings
from app.observability.langfuse import PROMPT_NAME


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
    print(f"Seeding prompt '{PROMPT_NAME}' (label={label}) lên {settings.LANGFUSE_HOST} ...")
    client.create_prompt(
        name=PROMPT_NAME,
        type="text",
        prompt=TELECOM_AGENT_SYSTEM_PROMPT.strip(),
        labels=[label],
        config={"fallback_version": TELECOM_AGENT_PROMPT_VERSION},
    )
    client.flush()
    print("Done. Kiểm tra prompt trên Langfuse dashboard.")


if __name__ == "__main__":
    seed_prompt()
