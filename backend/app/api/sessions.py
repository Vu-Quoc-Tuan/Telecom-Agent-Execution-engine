# backend/app/api/sessions.py
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.repositories.messages import MessageRepository
from app.services.session import SessionService

router = APIRouter()


class CreateSessionBody(BaseModel):
    title: str = "New Session"


@router.post("")
def create_chat_session(body: CreateSessionBody, db: Session = Depends(get_db)):
    session = SessionService.create_new_session(db, body.title)
    return {"status": "success", "session_id": str(session.id), "title": session.title}


@router.get("")
def list_all_active_sessions(db: Session = Depends(get_db)):
    sessions = SessionService.list_user_sessions(db, limit=40)
    return [
        {"id": str(s.id), "title": s.title, "created_at": s.created_at.isoformat()}
        for s in sessions
    ]


@router.get("/{session_id}/messages")
def get_chat_session_messages(session_id: uuid.UUID, db: Session = Depends(get_db)):
    session = SessionService.get_active_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên chat yêu cầu.")

    messages = MessageRepository.get_chat_history(db, session_id)
    return [
        {
            "id": str(message.id),
            "session_id": str(message.session_id),
            "run_id": str(message.run_id) if message.run_id else None,
            "role": message.role,
            "content": message.content,
            "status": message.status,
            "sequence_no": message.sequence_no,
            "metadata": message.metadata_json,
            "created_at": message.created_at.isoformat(),
        }
        for message in messages
    ]


@router.delete("/{session_id}")
def delete_chat_session(session_id: uuid.UUID, db: Session = Depends(get_db)):
    success = SessionService.soft_delete_session(db, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên chat yêu cầu.")
    return {"status": "success", "message": "Đã xóa mềm phiên làm việc thành công."}
