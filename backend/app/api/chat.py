# backend/app/api/chat.py (Bản update liên thông mạch map)
from __future__ import annotations

import uuid

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_llm_gateway, settings
from app.database.connection import SessionLocal
from app.services.agent_execution import AgentExecutionService
from app.streaming.event_mapper import TelecomStreamEventMapper
from app.streaming.sse import format_sse_event

router = APIRouter()


class ChatStreamBody(BaseModel):
    session_id: uuid.UUID
    user_message: str = Field(min_length=1, max_length=20_000)
    provider: str | None = None
    model: str | None = None


@router.post("/stream")
async def stream_agent_conversation(
    body: ChatStreamBody,
):
    llm_gateway = get_llm_gateway()

    provider = body.provider or settings.PROVIDER
    model = body.model or settings.MODEL_NAME

    # DB session phải sống xuyên suốt vòng đời của stream, không lấy từ Depends(get_db):
    # FastAPI đóng dependency-session ngay khi endpoint trả về StreamingResponse, trong khi
    # generator dưới đây mới truy vấn DB sau đó -> dùng SessionLocal() bên trong generator.
    async def sse_pipeline_transport():
        with SessionLocal() as db:
            # 1. Bốc generator nhả Tuple thô từ Service
            raw_generator = AgentExecutionService.run_agent_lifecycle(
                db=db,
                llm_gateway=llm_gateway,
                session_id=body.session_id,
                user_content=body.user_message,
                provider=provider,
                model=model,
            )
            # 2. Đút qua bộ mapper và format sse rạch ròi nhiệm vụ
            async for event_type, payload_dict in raw_generator:
                # Lớp 1: Map và validate cấu trúc bằng Pydantic
                envelope = TelecomStreamEventMapper.map_raw_payload_to_envelope(
                    event_type, payload_dict
                )
                # Lớp 2: Biến thành chuỗi text sse truyền mạng
                yield format_sse_event(envelope.event_type.value, envelope.payload.model_dump())

    return StreamingResponse(sse_pipeline_transport(), media_type="text/event-stream")
