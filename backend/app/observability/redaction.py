# backend/app/observability/redaction.py
from __future__ import annotations

import re
from typing import Any

from app.common.security_patterns import PRIVATE_KEY_PATTERN, SECRET_KEY_PATTERN, SENSITIVE_KEYS


class DataRedactor:
    # 🕵️ Quét Regex tìm kiếm các chuỗi nhạy cảm dạng mật mã hoặc khóa bảo mật
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
            PRIVATE_KEY_PATTERN,
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

        cleaned = {}

        for k, v in data.items():
            normalized_key = k.lower().replace("-", "_")
            if normalized_key in SENSITIVE_KEYS:
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
