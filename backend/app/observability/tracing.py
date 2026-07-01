# backend/app/observability/tracing.py
from __future__ import annotations

import time
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Any

from app.observability.logging import app_logger


class TelecomTaskTracer(AbstractContextManager):
    """
    Context Manager chịu trách nhiệm cô lập, đo đạc latency,
    và trích xuất vết tracing tự động cho từng công đoạn xử lý của Agent.
    """

    def __init__(
        self, task_name: str, session_id: str, run_id: str, metadata: dict[str, Any] | None = None
    ):
        self.task_name = task_name
        self.session_id = session_id
        self.run_id = run_id
        self.metadata = metadata or {}
        self.start_time: float = 0.0

    def __enter__(self) -> TelecomTaskTracer:
        self.start_time = time.perf_counter()
        app_logger.info(
            f"[TRACE START] Bắt đầu tác vụ: {self.task_name}",
            extra={"session_id": self.session_id, "run_id": self.run_id, **self.metadata},
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        latency_ms = int((time.perf_counter() - self.start_time) * 1000)

        if exc_type:
            # Ghi nhận log cấu trúc JSON nếu tác vụ bị lỗi
            app_logger.error(
                f"[TRACE FAILED] Tác vụ '{self.task_name}' bị lỗi sau {latency_ms}ms. Lỗi: {str(exc_val)}",
                extra={
                    "session_id": self.session_id,
                    "run_id": self.run_id,
                    "latency_ms": latency_ms,
                    "error_type": exc_type.__name__,
                },
            )
            return False

        app_logger.info(
            f"[TRACE SUCCESS] Tác vụ '{self.task_name}' hoàn thành trong {latency_ms}ms.",
            extra={"session_id": self.session_id, "run_id": self.run_id, "latency_ms": latency_ms},
        )
        return True
