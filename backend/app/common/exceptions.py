# backend/app/common/exceptions.py
from typing import Any


class TelecomAgentException(Exception):
    """Ngoại lệ nền tảng tối cao của hệ thống"""

    def __init__(self, message: str, code: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class SafetyViolationError(TelecomAgentException):
    """Ném ra khi Bộ quét tĩnh AST hoặc Bộ lọc lệnh SSH phát hiện hành vi phá hoại"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="SAFETY_VIOLATION_LIMIT", details=details)


class SkillCompilationError(TelecomAgentException):
    """Ném ra khi hàm exec() không thể biên dịch chuỗi text Python của kỹ sư tải lên"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="DYNAMIC_COMPILE_FAILED", details=details)


class SkillRuntimeError(TelecomAgentException):
    """Ném ra khi kịch bản kĩ thuật đang chạy trên trạm thì bị crash nửa chừng"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="SKILL_RUNTIME_CRASH", details=details)


class ConnectorExecutionError(TelecomAgentException):
    """Ném ra khi đường ống vật lý (Paramiko SSH, Clickhouse) bị đứt gãy kết nối"""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, code="CONNECTOR_IO_ERROR", details=details)
