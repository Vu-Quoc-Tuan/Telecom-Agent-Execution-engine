from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ----------------Enum----------------
class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    TOOL = "tool"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"
    ERROR = "error"


class StreamEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    FINISH = "finish"


class ToolChoiceMode(StrEnum):
    NONE = "none"
    SPECIFIC = "specific"


# ----------------Schema----------------
class ToolChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ToolChoiceMode
    tool_name: str | None = None

    @model_validator(mode="after")
    def validate_specific_tool(self) -> "ToolChoice":
        if self.mode is ToolChoiceMode.SPECIFIC and not self.tool_name:
            raise ValueError("tool_name is required when mode='specific'")
        if self.mode is not ToolChoiceMode.SPECIFIC and self.tool_name is not None:
            raise ValueError("tool_name is only valid when mode='specific'")
        return self


class LLMToolDefinition(BaseModel):
    """Tool schema exposed by the Skill Registry to an LLM provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    strict: bool = True


class NormalizedToolCall(BaseModel):
    """Provider-independent tool call returned by an LLM."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    """
    Provider-independent conversation message.

    - assistant messages can contain tool_calls.
    - tool messages must reference tool_call_id.
    """

    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str | None = None
    name: str | None = None

    tool_calls: list[NormalizedToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_is_error: bool = False

    @model_validator(mode="after")
    def validate_role_fields(self) -> "LLMMessage":
        if self.role is MessageRole.TOOL:
            if not self.tool_call_id:
                raise ValueError("tool_call_id is required for role='tool'")
            if self.tool_calls:
                raise ValueError("role='tool' cannot contain tool_calls")
            if self.content is None:
                self.content = ""
            return self

        if self.role is MessageRole.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("assistant messages cannot set tool_call_id")
            return self

        if self.tool_calls:
            raise ValueError(f"role='{self.role.value}' cannot contain tool_calls")
        if self.tool_call_id is not None:
            raise ValueError(f"role='{self.role.value}' cannot set tool_call_id")
        if self.tool_is_error:
            raise ValueError("tool_is_error is only valid for role='tool'")
        return self


class TokenUsage(BaseModel):
    """Normalized token usage across OpenAI-compatible and Anthropic APIs."""

    model_config = ConfigDict(extra="allow")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    reasoning_tokens: int = 0

    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_total(self) -> "TokenUsage":
        if self.total_tokens <= 0:
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


class LLMRequestOptions(BaseModel):
    """Cross-provider generation options."""

    model_config = ConfigDict(extra="forbid")

    # Ghi đè model theo từng request; None -> dùng model mặc định cấu hình trên adapter.
    model: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)

    stop: str | list[str] | None = None
    tool_choice: ToolChoice | None = None
    parallel_tool_calls: bool | None = None

    timeout_seconds: float | None = Field(default=None, gt=0)


class LLMResponse(BaseModel):
    """Fully accumulated, provider-independent model response."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    role: MessageRole = MessageRole.ASSISTANT

    provider: str
    model: str
    response_id: str | None = None

    tool_calls: list[NormalizedToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    finish_reason: FinishReason = FinishReason.UNKNOWN


class ToolCallDelta(BaseModel):
    """Incremental tool-call data emitted while streaming."""

    model_config = ConfigDict(extra="forbid")

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None


class LLMStreamChunk(BaseModel):
    """A normalized streaming event produced by an adapter."""

    model_config = ConfigDict(extra="forbid")

    event_type: StreamEventType
    provider: str
    model: str

    response_id: str | None = None
    content_delta: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: TokenUsage | None = None
    finish_reason: FinishReason | None = None

    is_final: bool = False
    final_response: LLMResponse | None = None
