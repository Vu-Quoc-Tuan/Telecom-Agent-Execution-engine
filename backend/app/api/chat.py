# backend/app/api/chat.py (Bản update liên thông mạch map)
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.agent.builtin_tools import list_backend_owned_capabilities
from app.config import get_llm_gateway, settings
from app.database.connection import SessionLocal, get_db
from app.database.repositories.skills import SkillRepository
from app.services.agent_execution import AgentExecutionService
from app.streaming.event_mapper import TelecomStreamEventMapper
from app.streaming.sse import format_sse_event

router = APIRouter()


class ChatStreamBody(BaseModel):
    session_id: uuid.UUID
    user_message: str = Field(min_length=1, max_length=20_000)
    provider: str | None = None
    model: str | None = None
    skill_mode: Literal["auto", "specific"] = "auto"
    skill_name: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_skill_selection(self) -> ChatStreamBody:
        if self.skill_name is not None:
            self.skill_name = self.skill_name.strip()
        if self.skill_mode == "specific" and not self.skill_name:
            raise ValueError("skill_name is required when skill_mode='specific'")
        if self.skill_mode == "auto" and self.skill_name is not None:
            raise ValueError("skill_name is only valid when skill_mode='specific'")
        return self


@router.get("/options")
def get_chat_options(db: Session = Depends(get_db)):
    llm_gateway = get_llm_gateway()
    providers = set(llm_gateway.providers)
    models: list[dict[str, object]] = []
    default_model: dict[str, str] | None = None
    configured_options = (
        ("openai", "OpenAI", settings.OPENAI_MODEL_NAME),
        ("anthropic", "Claude", settings.ANTHROPIC_MODEL_NAME),
    )
    for provider, label, configured_model in configured_options:
        available = provider in providers
        model = llm_gateway.get_adapter(provider).model if available else configured_model
        models.append(
            {
                "provider": provider,
                "model": model,
                "label": label,
                "description": model,
                "available": available,
            }
        )
        if provider == settings.PROVIDER and available:
            default_model = {"provider": provider, "model": model}

    if default_model is None:
        first_available = next((item for item in models if item["available"]), None)
        if first_available:
            default_model = {
                "provider": first_available["provider"],
                "model": first_available["model"],
            }

    ready_skills = SkillRepository.list_ready_skills(db)
    return {
        "default_model": default_model,
        "models": models,
        "skills": [
            {"name": skill.name, "description": skill.description} for skill in ready_skills
        ],
        "capabilities": list_backend_owned_capabilities(settings),
    }


@router.post("/stream")
async def stream_agent_conversation(
    body: ChatStreamBody,
):
    llm_gateway = get_llm_gateway()

    provider = (body.provider or settings.PROVIDER).strip().lower()
    if body.model:
        model = body.model
    elif provider in llm_gateway.providers:
        model = llm_gateway.get_adapter(provider).model
    else:
        model = (
            settings.ANTHROPIC_MODEL_NAME if provider == "anthropic" else settings.OPENAI_MODEL_NAME
        )

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
                selected_skill=body.skill_name if body.skill_mode == "specific" else None,
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
