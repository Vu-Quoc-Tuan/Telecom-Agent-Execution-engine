from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from pydantic import BaseModel, Field

from app.llm.schemas import LLMMessage, LLMResponse

MAX_CHECKPOINT_MESSAGES = 40


def append_messages(
    current: list[LLMMessage], new_messages: Sequence[LLMMessage] | LLMMessage
) -> list[LLMMessage]:
    """
    Reducer giúp LangGraph tự động cộng dồn tin nhắn mới vào lịch sử
    hội thoại mà không làm ghi đè dữ liệu cũ.
    """
    if not new_messages:
        return current

    output = list(current)
    if isinstance(new_messages, LLMMessage):
        output.append(new_messages)
    else:
        output.extend(new_messages)
    return output[-MAX_CHECKPOINT_MESSAGES:]


class AgentState(BaseModel):
    """
    Trạng thái hội thoại và vết kỹ thuật chạy ngầm của Telecom AI Agent.
    """

    # Trục tin nhắn tích lũy qua reducer
    messages: Annotated[list[LLMMessage], append_messages] = Field(default_factory=list)

    # Định danh định vị phiên làm việc xuống hệ quản trị cơ sở dữ liệu
    session_id: str
    run_id: str

    # Biến đếm bước để chống lỗi lặp vô hạn (Infinite Loop Protection)
    current_step_index: int = 0
    max_steps: int = 12

    # Bộ nhớ tạm giữ gói tin phản hồi gần nhất của LLM Gateway để làm căn cứ rẽ nhánh
    latest_response: LLMResponse | None = None

    # Lưu vết lỗi hệ thống nếu tiến trình gõ trạm hoặc chạy code bị crash
    execution_error: str | None = None

    # Set only for the single LLM turn that explains a human rejection.
    approval_rejected: bool = False
