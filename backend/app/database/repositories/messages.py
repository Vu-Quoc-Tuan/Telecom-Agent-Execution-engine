import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database.models.chat_messages import ChatMessage


class MessageRepository:
    @staticmethod
    def save_message(
        db: Session,
        session_id: uuid.UUID,
        run_id: uuid.UUID | None,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessage:
        """
        Save a chat message to chat flow
        """
        if metadata is None:
            metadata = {}

        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:session_id, 0))"),
            {"session_id": str(session_id)},
        )
        max_seq_stmt = select(func.max(ChatMessage.sequence_no)).where(
            ChatMessage.session_id == session_id
        )
        curr_max = db.scalar(max_seq_stmt)
        next_seq = (curr_max or 0) + 1

        new_message = ChatMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            run_id=run_id,
            role=role,
            content=content,
            status="completed",
            sequence_no=next_seq,
            metadata_json=metadata,
        )
        db.add(new_message)
        db.commit()
        db.refresh(new_message)
        return new_message

    @staticmethod
    def get_chat_history(db: Session, session_id: uuid.UUID) -> list[ChatMessage]:
        """
        Get chat history for a session.
        """
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.sequence_no.asc())
        )
        return list(db.execute(stmt).scalars())
