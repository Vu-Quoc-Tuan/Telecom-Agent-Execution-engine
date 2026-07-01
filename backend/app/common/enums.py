from enum import StrEnum


class SkillStatus(StrEnum):
    UPLOADED = "uploaded"
    TESTING = "testing"
    READY = "ready"
    REJECTED = "rejected"


class ExecutionMode(StrEnum):
    AUTO_EXECUTE = "auto_execute"
    REQUIRE_APPROVAL = "require_approval"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class StepType(StrEnum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    APPROVAL = "approval"
    ERROR = "error"
