# backend/app/observability/redaction.py
from __future__ import annotations

import re
from typing import Any


class DataRedactor:
    # 🕵️ Quét Regex tìm kiếm các chuỗi nhạy cảm dạng mật mã hoặc khóa bảo mật
    SECRET_KEY_PATTERN = r"password|passwd|pwd|secret|api[_-]?key|token|private_key"
    REDACT_PATTERNS = [
        (
            re.compile(
                rf"\b({SECRET_KEY_PATTERN})\b(\s*[:=]\s*)(['\"])[^'\"]+\3",
                re.IGNORECASE,
            ),
            r"\1\2\3[REDACTED]\3",
        ),
        (
            re.compile(
                rf"\b({SECRET_KEY_PATTERN})\b(\s*[:=]\s*)([^'\"\s,;]+)",
                re.IGNORECASE,
            ),
            r"\1\2[REDACTED]",
        ),
        (re.compile(r"(--password|--token|-p)\s+([^\s]+)", re.IGNORECASE), r"\1 [REDACTED]"),
        (
            re.compile(
                r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                re.IGNORECASE,
            ),
            r"[REDACTED PRIVATE KEY]",
        ),
    ]

    @classmethod
    def redact_text(cls, text: str) -> str:
        """Che giấu thông tin nhạy cảm trong chuỗi văn bản thô (Log SSH, SQL)"""
        if not text:
            return text
        redacted = text
        for pattern, repl in cls.REDACT_PATTERNS:
            redacted = pattern.sub(repl, redacted)
        return redacted

    @classmethod
    def redact_dict(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Duyệt đệ quy qua Object/Dict để xóa vết các key nằm trong danh mục nhạy cảm"""
        if not data:
            return data

        sensitive_keys = {"password", "passwd", "api_key", "secret", "token", "private_key", "auth"}
        cleaned = {}

        for k, v in data.items():
            normalized_key = k.lower().replace("-", "_")
            if normalized_key in sensitive_keys:
                cleaned[k] = "[REDACTED]"
            elif isinstance(v, dict):
                cleaned[k] = cls.redact_dict(v)
            elif isinstance(v, list):
                cleaned[k] = [
                    cls.redact_dict(item) if isinstance(item, dict) else item for item in v
                ]
            elif isinstance(v, str):
                cleaned[k] = cls.redact_text(v)
            else:
                cleaned[k] = v
        return cleaned
