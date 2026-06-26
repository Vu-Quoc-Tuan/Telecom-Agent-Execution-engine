# backend/app/common/enums.py
from enum import StrEnum


class SkillStatus(StrEnum):
    UPLOADED = "uploaded"
    TESTING = "testing"
    READY = "ready"
    REJECTED = "rejected"


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    SAFE_ACTION = "safe_action"
    DANGEROUS_ACTION = "dangerous_action"
    PROHIBITED = "prohibited"


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
