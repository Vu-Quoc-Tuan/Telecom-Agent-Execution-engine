# backend/app/observability/langfuse.py
from __future__ import annotations

import os
import uuid
from datetime import datetime

if not os.environ.get("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "telecom-agent-backend"

try:
    from langfuse import Langfuse, observe
except Exception:  # SDK chưa cài / lỗi import -> tracing trở thành no-op, không sập app
    Langfuse = None
    observe = None

from app.config import settings
from app.observability.logging import app_logger
from app.observability.redaction import DataRedactor


class LangfuseTelemetryTracker:
    def __init__(self, configuration=settings):
        self.public_key = configuration.LANGFUSE_PUBLIC_KEY
        self.secret_key = configuration.LANGFUSE_SECRET_KEY
        self.host = configuration.LANGFUSE_HOST
        self._client: Langfuse | None = None

    def initialize(self):
        """Kích hoạt kết nối Client Langfuse (an toàn khi thiếu SDK hoặc credentials)."""
        if Langfuse is None or observe is None:
            app_logger.warning("Langfuse SDK không khả dụng. Bỏ qua tầng tracing.")
            return None
        if not self.public_key or not self.secret_key:
            app_logger.warning("Langfuse credentials chưa được cấu hình. Bỏ qua tầng tracing.")
            return None

        try:
            self._client = Langfuse(
                public_key=self.public_key,
                secret_key=self.secret_key,
                host=self.host,
            )
        except Exception as exc:
            app_logger.warning("Khởi tạo Langfuse client thất bại: %s", exc)
            self._client = None
        return self._client

    def trace_llm_generation(
        self,
        session_id: str,
        run_id: str,
        model_name: str,
        prompt_messages: list,
        completion_content: str,
        prompt_tokens: int,
        completion_tokens: int,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """Đẩy chỉ số Token, Prompt và câu trả lời của AI lên hệ thống giám sát tập trung"""
        if not self._client:
            return

        try:
            trace_id = self._normalize_trace_id(run_id)
            # Khử độc toàn bộ dữ liệu văn bản trước khi đẩy lên mây
            clean_prompts = [DataRedactor.redact_text(str(m)) for m in prompt_messages]
            clean_completion = DataRedactor.redact_text(completion_content)

            @observe(
                name="llm_gateway_call",
                as_type="generation",
                capture_input=False,
                capture_output=False,
            )
            def record_generation(**_langfuse_kwargs):
                update_args = {
                    "name": "telecom_agent_run",
                    "model": model_name,
                    "input": clean_prompts,
                    "output": clean_completion,
                    "metadata": {"session_id": session_id, "run_id": run_id},
                    "usage_details": {"input": prompt_tokens, "output": completion_tokens},
                }
                # SDK 4.x: span start/end do @observe tự quản; chỉ nhận completion_start_time
                if start_time:
                    update_args["completion_start_time"] = start_time
                self._client.update_current_generation(**update_args)

            record_generation(langfuse_trace_id=trace_id, langfuse_public_key=self.public_key)
            self._client.flush()
        except Exception as e:
            app_logger.error(f"Thất bại khi đẩy telemetry trace lên Langfuse: {str(e)}")

    def get_trace_url(self, run_id: str) -> str:
        """Sinh đường link URL động trỏ thẳng sang Dashboard trực quan của Langfuse"""
        if not self._client:
            return ""
        try:
            return self._client.get_trace_url(trace_id=self._normalize_trace_id(run_id)) or ""
        except Exception as exc:
            app_logger.warning("Không sinh được Langfuse trace URL: %s", exc)
            return ""

    @staticmethod
    def _normalize_trace_id(run_id: str) -> str:
        try:
            return uuid.UUID(str(run_id)).hex
        except (TypeError, ValueError):
            return str(run_id).replace("-", "").lower()


# Thực thể độc quyền quản lý tracing toàn cục
telemetry_tracker = LangfuseTelemetryTracker()
telemetry_tracker.initialize()
