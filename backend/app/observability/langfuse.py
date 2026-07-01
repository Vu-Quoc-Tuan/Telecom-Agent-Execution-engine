# backend/app/observability/langfuse.py
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

if not os.environ.get("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "telecom-agent-backend"

try:
    from langfuse import Langfuse
except Exception:
    Langfuse = None

from app.config import settings
from app.observability.logging import app_logger
from app.observability.redaction import DataRedactor

# Tên prompt được quản lý tập trung trên Langfuse Prompt Management.
PROMPT_NAME = "telecom-agent-system"
SKILL_DOMAIN_JUDGE_PROMPT_NAME = "SKILL_DOMAIN_JUDGE_SYSTEM_PROMPT"


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
        self._run_turn_indices: dict[str, int] = {}

    def initialize(self):
        """Kích hoạt kết nối Client Langfuse"""
        if Langfuse is None:
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

    def get_prompt(self, prompt_name: str, fallback_text: str | None = None):
        """Lấy text prompt theo tên từ Langfuse Prompt Management."""
        if not self._client:
            return None
        try:
            return self._client.get_prompt(
                prompt_name,
                type="text",
                label=self.prompt_label,
                fallback=fallback_text,
                cache_ttl_seconds=self.prompt_cache_ttl,
            )
        except Exception as exc:
            app_logger.warning("Không lấy được prompt '%s' từ Langfuse: %s", prompt_name, exc)
            return None

    def get_system_prompt(self, fallback_text: str | None = None):
        return self.get_prompt(PROMPT_NAME, fallback_text=fallback_text)

    def get_active_prompt_version(self, fallback_version: str) -> str:
        """Trả version prompt đang active trên Langfuse (cho run record).

        Rơi về `fallback_version` khi không có client hoặc đang dùng fallback prompt.
        """
        prompt = self.get_system_prompt(fallback_text="")
        if prompt is None or getattr(prompt, "is_fallback", False):
            return fallback_version
        version = getattr(prompt, "version", None)
        return str(version) if version is not None else fallback_version

    def _ensure_root(
        self,
        trace_id: str,
        session_id: str | None = None,
        input_content: str | None = None,
        turn_index: int = 1,
    ):
        """Lấy span của lượt chạy để mọi observation con gắn đúng trace phiên."""
        root = self._active_runs.get(trace_id)
        if root is not None:
            return root
        clean_input = DataRedactor.redact_text(input_content) if input_content else None

        metadata = {"run_id": trace_id}
        if session_id:
            metadata["session_id"] = session_id
        # Tạo tên Span gốc chứa số thứ tự lượt chạy (ví dụ: "agent_turn #1", "agent_turn #2")
        # giúp Langfuse vẽ đồ thị tuần tự đẹp mắt thay vì gộp chung các nút trùng tên lại với nhau.
        span_name = f"agent_turn #{turn_index}"
        root = self._client.start_observation(
            trace_context={"trace_id": trace_id},
            name=span_name,
            as_type="span",
            input=clean_input,
            metadata=metadata,
        )
        if hasattr(root, "_otel_span") and root._otel_span.is_recording():
            try:
                if session_id:
                    root._otel_span.set_attribute("session.id", session_id)
                root._otel_span.set_attribute("langfuse.trace.name", span_name)
            except Exception as e:
                app_logger.warning("Không set được session.id / trace name cho _otel_span: %s", e)
        self._active_runs[trace_id] = root
        self._run_turn_indices[trace_id] = turn_index
        return root

    def get_turn_index(self, run_id: str) -> int:
        """Lấy turn_index hiện tại của run_id."""
        trace_id = self._normalize_trace_id(run_id)
        return self._run_turn_indices.get(trace_id, 1)

    def trace_run_start(
        self,
        session_id: str,
        run_id: str,
        input_content: str,
        turn_index: int = 1,
    ) -> None:
        """Mở span gốc duy nhất cho lượt chạy"""
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            self._ensure_root(
                trace_id,
                session_id=session_id,
                input_content=input_content,
                turn_index=turn_index,
            )
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
            self._run_turn_indices.pop(trace_id, None)
            if root_span is not None:
                root_span.update(output=clean_output, metadata={"status": status})
                if hasattr(root_span, "_otel_span") and root_span._otel_span.is_recording():
                    try:
                        root_span._otel_span.set_attribute("langfuse.trace.output", clean_output)
                    except Exception as e:
                        app_logger.warning("Không set được trace output trên _otel_span: %s", e)
                root_span.end()
            self._client.flush()
        except Exception as e:
            app_logger.error(f"Thất bại khi cập nhật trace Langfuse: {str(e)}")

    def get_langchain_callback_handler(self, run_id: str):
        """Tạo Langfuse CallbackHandler để LangChain log LLM call dưới span lượt chạy."""
        if not self._client:
            return None
        try:
            trace_id = self._normalize_trace_id(run_id)
            root_span = self._active_runs.get(trace_id)
            if root_span is None:
                return None
            parent_span_id = getattr(root_span, "id", None)
            session_trace_id = getattr(root_span, "trace_id", None)
            if not parent_span_id or not session_trace_id:
                return None

            from langfuse.langchain import CallbackHandler

            class MetadataFilteringCallbackHandler(CallbackHandler):
                def _clean_metadata(self, metadata: dict[str, Any] | None) -> dict[str, Any] | None:
                    if not metadata or not isinstance(metadata, dict):
                        return metadata
                    return {k: v for k, v in metadata.items() if not k.startswith("langgraph_")}

                # Chặn chain-level observations: LangChain tạo rất nhiều span
                # "chain start/end" nội bộ (wrapper, serialized chain, ...) gây
                # trùng lặp và làm cây trace Langfuse rất rối. Chỉ giữ LLM generation.
                def on_chain_start(self, *args: Any, **kwargs: Any) -> Any:
                    pass

                def on_chain_end(self, *args: Any, **kwargs: Any) -> Any:
                    pass

                def on_llm_start(self, *args: Any, **kwargs: Any) -> Any:
                    if "metadata" in kwargs:
                        kwargs["metadata"] = self._clean_metadata(kwargs["metadata"])
                    return super().on_llm_start(*args, **kwargs)

                def on_chat_model_start(self, *args: Any, **kwargs: Any) -> Any:
                    if "metadata" in kwargs:
                        kwargs["metadata"] = self._clean_metadata(kwargs["metadata"])
                    return super().on_chat_model_start(*args, **kwargs)

                def on_tool_start(self, *args: Any, **kwargs: Any) -> Any:
                    if "metadata" in kwargs:
                        kwargs["metadata"] = self._clean_metadata(kwargs["metadata"])
                    return super().on_tool_start(*args, **kwargs)

                def on_retriever_start(self, *args: Any, **kwargs: Any) -> Any:
                    if "metadata" in kwargs:
                        kwargs["metadata"] = self._clean_metadata(kwargs["metadata"])
                    return super().on_retriever_start(*args, **kwargs)

            return MetadataFilteringCallbackHandler(
                public_key=self.public_key,
                trace_context={
                    "trace_id": session_trace_id,
                    "parent_span_id": parent_span_id,
                },
            )
        except Exception as exc:
            app_logger.warning("Không tạo được Langfuse LangChain callback: %s", exc)
            return None

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
        """Ghi 1 span con (thực thi tool) vào span gốc. KHÔNG flush.

        Chỉ ghi khi root span đã được tạo trước đó bởi trace_run_start().
        Nếu root span chưa tồn tại, bỏ qua — tránh tạo orphan observation
        làm rối cây trace trên Langfuse.
        """
        if not self._client:
            return
        try:
            trace_id = self._normalize_trace_id(run_id)
            root_span = self._active_runs.get(trace_id)
            if root_span is None:
                return
            clean_input = DataRedactor.redact_text(str(input_data))
            clean_output = DataRedactor.redact_text(str(output_data))

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

    @staticmethod
    def _normalize_trace_id(run_id: str) -> str:
        try:
            return uuid.UUID(str(run_id)).hex
        except (TypeError, ValueError):
            return str(run_id).replace("-", "").lower()


# Thực thể độc quyền quản lý tracing toàn cục
telemetry_tracker = LangfuseTelemetryTracker()
telemetry_tracker.initialize()
