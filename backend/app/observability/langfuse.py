# backend/app/observability/langfuse.py
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

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

# Tên prompt được quản lý tập trung trên Langfuse Prompt Management.
PROMPT_NAME = "telecom-agent-system"


class LangfuseTelemetryTracker:
    def __init__(self, configuration=settings):
        self.public_key = configuration.LANGFUSE_PUBLIC_KEY
        self.secret_key = configuration.LANGFUSE_SECRET_KEY
        self.host = configuration.LANGFUSE_HOST
        # Cấu hình Prompt Management (an toàn với settings thiếu field nhờ getattr).
        self.prompt_label = getattr(configuration, "LANGFUSE_PROMPT_LABEL", "production")
        self.prompt_cache_ttl = getattr(configuration, "LANGFUSE_PROMPT_CACHE_TTL_SECONDS", 300)
        self._client: Langfuse | None = None
        self._active_runs: dict[str, Any] = {}

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

    def get_system_prompt(self, fallback_text: str | None = None):
        """Lấy prompt object từ Langfuse Prompt Management.

        Trả về prompt object (có `.compile(**vars)`); kể cả khi Langfuse lỗi/không
        tồn tại prompt, SDK trả về fallback object dựng từ `fallback_text`.
        Trả `None` khi chưa có client (thiếu SDK/credentials) để caller tự compile cứng.
        """
        if not self._client:
            return None
        try:
            return self._client.get_prompt(
                PROMPT_NAME,
                type="text",
                label=self.prompt_label,
                fallback=fallback_text,
                cache_ttl_seconds=self.prompt_cache_ttl,
            )
        except Exception as exc:
            app_logger.warning("Không lấy được prompt từ Langfuse: %s", exc)
            return None

    def get_active_prompt_version(self, fallback_version: str) -> str:
        """Trả version prompt đang active trên Langfuse (cho run record).

        Rơi về `fallback_version` khi không có client hoặc đang dùng fallback prompt.
        """
        prompt = self.get_system_prompt(fallback_text="")
        if prompt is None or getattr(prompt, "is_fallback", False):
            return fallback_version
        version = getattr(prompt, "version", None)
        return str(version) if version is not None else fallback_version

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
        prompt_name: str | None = None,
    ):
        """Đẩy chỉ số Token, Prompt và câu trả lời của AI lên hệ thống giám sát tập trung"""
        if not self._client:
            return

        try:
            trace_id = self._normalize_trace_id(run_id)
            # Khử độc toàn bộ dữ liệu văn bản trước khi đẩy lên mây
            clean_prompts = [DataRedactor.redact_text(str(m)) for m in prompt_messages]
            clean_completion = DataRedactor.redact_text(completion_content)

            # Link version prompt vào generation (chỉ khi không phải fallback).
            linked_prompt = None
            if prompt_name == PROMPT_NAME:
                candidate = self.get_system_prompt(fallback_text="")
                if candidate is not None and not getattr(candidate, "is_fallback", False):
                    linked_prompt = candidate

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
                if linked_prompt is not None:
                    update_args["prompt"] = linked_prompt
                self._client.update_current_generation(**update_args)

            record_generation(langfuse_trace_id=trace_id, langfuse_public_key=self.public_key)
            self._client.flush()
        except Exception as e:
            app_logger.error(f"Thất bại khi đẩy telemetry trace lên Langfuse: {str(e)}")

    def _ensure_root(
        self,
        trace_id: str,
        session_id: str | None = None,
        input_content: str | None = None,
    ):
        """Lấy (hoặc tạo lười) span gốc của trace để mọi observation con gắn đúng 1 cụm.

        QUAN TRỌNG: con phải được tạo bằng `root.start_observation(...)` (method trên
        chính span gốc), KHÔNG dùng `client.start_observation(parent_span_id=...)` —
        cách cũ khiến Langfuse 'thăng cấp' con thành trace riêng (bị nhân đôi).
        Tạo lười để run resume (ở tiến trình chưa có gốc trong _active_runs) vẫn gom đúng trace.
        """
        root = self._active_runs.get(trace_id)
        if root is not None:
            return root
        clean_input = DataRedactor.redact_text(input_content) if input_content else None
        # input/output cấp trace tự suy ra từ span gốc (set_trace_io đã deprecated).
        root = self._client.start_observation(
            trace_context={"trace_id": trace_id},
            name="telecom_agent_run",
            as_type="span",
            input=clean_input,
            metadata={"session_id": session_id} if session_id else None,
        )
        self._active_runs[trace_id] = root
        return root

    def trace_run_start(
        self,
        session_id: str,
        run_id: str,
        input_content: str,
    ) -> None:
        """Mở span gốc duy nhất cho lượt chạy (KHÔNG flush ở đây để khỏi chặn event loop)."""
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            self._ensure_root(trace_id, session_id=session_id, input_content=input_content)
        except Exception as e:
            app_logger.error(f"Thất bại khi khởi tạo trace Langfuse: {str(e)}")

    def trace_run_end(
        self,
        run_id: str,
        output_content: str,
        status: str,
    ) -> None:
        """Đóng span gốc + set output cấp trace, và flush MỘT lần cho cả lượt chạy.

        Các observation con đã được tạo/đóng trước đó (không flush riêng) sẽ được gom
        và đẩy lên trong lần flush duy nhất này -> hết lag do flush từng bước.
        """
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            clean_output = DataRedactor.redact_text(output_content)
            root_span = self._active_runs.pop(trace_id, None)
            if root_span is not None:
                root_span.update(output=clean_output, metadata={"status": status})
                root_span.end()
            self._client.flush()
        except Exception as e:
            app_logger.error(f"Thất bại khi cập nhật trace Langfuse: {str(e)}")

    @staticmethod
    def _redact_observation_input(input_data: Any) -> Any:
        """Khử secret/PII cho input của observation (hỗ trợ list message hoặc text)."""
        if isinstance(input_data, list):
            cleaned = []
            for item in input_data:
                obj = item.model_dump() if hasattr(item, "model_dump") else item
                if isinstance(obj, dict):
                    cleaned.append({k: DataRedactor.redact_text(str(v)) for k, v in obj.items()})
                else:
                    cleaned.append(DataRedactor.redact_text(str(obj)))
            return cleaned
        return DataRedactor.redact_text(str(input_data))

    def trace_generation(
        self,
        run_id: str,
        generation_name: str,
        model_name: str,
        input_data: Any,
        output_data: Any,
        input_tokens: int = 0,
        output_tokens: int = 0,
        prompt_name: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Ghi 1 generation con (1 lượt LLM nghĩ/chọn tool) vào span gốc. KHÔNG flush."""
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            clean_input = self._redact_observation_input(input_data)
            clean_output = DataRedactor.redact_text(str(output_data))

            linked_prompt = None
            if prompt_name == PROMPT_NAME:
                candidate = self.get_system_prompt(fallback_text="")
                if candidate is not None and not getattr(candidate, "is_fallback", False):
                    linked_prompt = candidate

            root_span = self._ensure_root(trace_id)
            generation = root_span.start_observation(
                name=generation_name,
                as_type="generation",
                model=model_name,
                input=clean_input,
                output=clean_output,
                usage_details={"input": input_tokens, "output": output_tokens},
                prompt=linked_prompt,
            )
            generation.end(end_time=int(end_time.timestamp() * 1e9) if end_time else None)
        except Exception as e:
            app_logger.error(f"Thất bại khi đẩy telemetry generation lên Langfuse: {str(e)}")

    def trace_span(
        self,
        run_id: str,
        span_name: str,
        input_data: Any,
        output_data: Any,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        status: str = "completed",
    ) -> None:
        """Ghi 1 span con (thực thi tool) vào span gốc. KHÔNG flush."""
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            clean_input = DataRedactor.redact_text(str(input_data))
            clean_output = DataRedactor.redact_text(str(output_data))

            root_span = self._ensure_root(trace_id)
            span = root_span.start_observation(
                name=span_name,
                as_type="tool",
                input=clean_input,
                output=clean_output,
                metadata={"status": status},
            )
            span.end(end_time=int(end_time.timestamp() * 1e9) if end_time else None)
        except Exception as e:
            app_logger.error(f"Thất bại khi đẩy telemetry span lên Langfuse: {str(e)}")

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
