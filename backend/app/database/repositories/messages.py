import uuid

from sqlalchemy import func, select, text, update
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
        return list(db.scalars(stmt).all())

    @staticmethod
    def list_pending_interventions(db: Session, run_id: uuid.UUID) -> list[ChatMessage]:
        """
        Return operator messages queued while a run is already executing.

        These messages are visible in chat history immediately, then injected into the
        next LLM turn inside the same run after the current graph step completes.
        """
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.run_id == run_id,
                ChatMessage.role == "user",
                ChatMessage.metadata_json["intervention_status"].as_string() == "pending",
            )
            .order_by(ChatMessage.sequence_no.asc())
        )
        return list(db.execute(statement).scalars())

    @staticmethod
    def mark_interventions_injected(
        db: Session,
        message_ids: list[uuid.UUID],
        *,
        commit: bool = False,
    ) -> None:
        if not message_ids:
            return
        statement = (
            update(ChatMessage)
            .where(ChatMessage.id.in_(message_ids))
            .values(
                metadata_json=func.jsonb_set(
                    ChatMessage.metadata_json,
                    text("'{intervention_status}'::text[]"),
                    text("'\"injected\"'::jsonb"),
                    True,
                )
            )
        )
        db.execute(statement)
        if commit:
            db.commit()
        else:
            db.flush()

    @staticmethod
    def mark_pending_interventions_undelivered(
        db: Session,
        run_id: uuid.UUID,
        *,
        reason: str,
        commit: bool = True,
    ) -> int:
        messages = MessageRepository.list_pending_interventions(db, run_id)
        for message in messages:
            message.metadata_json = {
                **(message.metadata_json or {}),
                "intervention_status": "undelivered",
                "delivery_error": reason,
            }
        if not messages:
            return 0
        if commit:
            db.commit()
        else:
            db.flush()
        return len(messages)

    @staticmethod
    def list_undelivered_interventions(
        db: Session,
        session_id: uuid.UUID,
    ) -> list[ChatMessage]:
        statement = (
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
                ChatMessage.metadata_json["intervention_status"].as_string() == "undelivered",
            )
            .order_by(ChatMessage.sequence_no.asc())
        )
        return list(db.scalars(statement).all())

    @staticmethod
    def requeue_undelivered_interventions(
        db: Session,
        *,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
    ) -> int:
        messages = MessageRepository.list_undelivered_interventions(db, session_id)
        for message in messages:
            previous_run_id = message.run_id
            message.run_id = run_id
            message.metadata_json = {
                **(message.metadata_json or {}),
                "intervention_status": "pending",
                "requeued_from_run_id": (
                    str(previous_run_id) if previous_run_id is not None else None
                ),
            }
            message.metadata_json.pop("delivery_error", None)
        if messages:
            db.commit()
        return len(messages)
