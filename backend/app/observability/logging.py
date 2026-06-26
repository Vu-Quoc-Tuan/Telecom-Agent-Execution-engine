# backend/app/observability/logging.py
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from app.observability.redaction import DataRedactor


class JSONStructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Nếu log có đính kèm metadata (trường extra)
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            log_entry["metadata"] = record.extra

        # 🛡️ BỌC GIÁP: Ép qua bộ lọc khử độc dữ liệu nhạy cảm trước khi xuất xưởng
        log_entry = DataRedactor.redact_dict(log_entry)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_telecom_logger(name: str = "telecom-agent") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONStructuredFormatter())
        logger.addHandler(handler)

    return logger


# Thực thể logger dùng chung toàn hệ thống
app_logger = setup_telecom_logger()
