from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException


class FakeUploadFile:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.content = content

    async def read(self, size: int = -1) -> bytes:
        return self.content if size < 0 else self.content[:size]


class ApplicationImportTests(unittest.TestCase):
    def test_application_import_does_not_open_external_connections(self) -> None:
        from app.main import app

        paths = {route.path for route in app.routes}
        self.assertIn("/health", paths)
        self.assertIn("/api/v1/chat/stream", paths)
        self.assertIn("/api/v1/sessions", paths)
        self.assertIn("/api/v1/sessions/{session_id}/messages", paths)
        self.assertIn("/api/v1/runs/{run_id}/timeline", paths)
        self.assertIn("/api/v1/runs/{run_id}/cancel", paths)
        self.assertIn("/api/v1/runs/mark-timeouts", paths)
        chat_route = next(route for route in app.routes if route.path == "/api/v1/chat/stream")
        self.assertEqual({"POST"}, chat_route.methods)
        history_route = next(
            route for route in app.routes if route.path == "/api/v1/sessions/{session_id}/messages"
        )
        self.assertEqual({"GET"}, history_route.methods)

    def test_checkpoint_serializer_allows_llm_schema_types(self) -> None:
        from app.agent.checkpointer import build_checkpoint_serializer
        from app.llm.schemas import LLMMessage, MessageRole

        serializer = build_checkpoint_serializer()
        payload = LLMMessage(role=MessageRole.USER, content="check node")

        restored = serializer.loads_typed(serializer.dumps_typed(payload))

        self.assertEqual(payload, restored)

    def test_timeout_sweeper_uses_lifecycle_service(self) -> None:
        from app import main

        fake_db = object()

        class FakeSessionContext:
            def __enter__(self):
                return fake_db

            def __exit__(self, exc_type, exc, tb):
                return False

        with (
            patch("app.main.SessionLocal", return_value=FakeSessionContext()),
            patch(
                "app.main.RunLifecycleService.mark_timed_out_runs",
                return_value=[object(), object()],
            ) as mark_timeouts,
            patch.object(main.settings, "RUN_TIMEOUT_SECONDS", 123),
            patch.object(main.settings, "RUN_TIMEOUT_SWEEPER_LIMIT", 7),
        ):
            count = main.sweep_timed_out_runs_once()

        self.assertEqual(2, count)
        mark_timeouts.assert_called_once_with(db=fake_db, timeout_seconds=123, limit=7)

    def test_session_messages_endpoint_serializes_chat_history(self) -> None:
        from app.api.sessions import get_chat_session_messages

        session_id = uuid4()
        message_id = uuid4()
        run_id = uuid4()
        created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        fake_message = SimpleNamespace(
            id=message_id,
            session_id=session_id,
            run_id=run_id,
            role="user",
            content="kiểm tra lag trên hanoi-core-01",
            status="completed",
            sequence_no=1,
            metadata_json={"source": "chat"},
            created_at=created_at,
        )

        with (
            patch("app.api.sessions.SessionService.get_active_session", return_value=object()),
            patch(
                "app.api.sessions.MessageRepository.get_chat_history",
                return_value=[fake_message],
            ),
        ):
            payload = get_chat_session_messages(session_id=session_id, db=object())

        self.assertEqual(
            payload,
            [
                {
                    "id": str(message_id),
                    "session_id": str(session_id),
                    "run_id": str(run_id),
                    "role": "user",
                    "content": "kiểm tra lag trên hanoi-core-01",
                    "status": "completed",
                    "sequence_no": 1,
                    "metadata": {"source": "chat"},
                    "created_at": created_at.isoformat(),
                }
            ],
        )


class SkillUploadApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_rejects_non_zip_filename(self) -> None:
        from app.api.skills import upload_and_verify_skill_pipeline

        file = FakeUploadFile(filename="SKILL.md", content=b"content")
        with self.assertRaises(HTTPException) as ctx:
            await upload_and_verify_skill_pipeline(file=file, db=object())

        self.assertEqual(415, ctx.exception.status_code)

    async def test_upload_preserves_service_conflict_status(self) -> None:
        from app.api.skills import upload_and_verify_skill_pipeline
        from app.services.skills import SkillValidationError

        error = SkillValidationError(
            status="CONFLICT",
            message="Skill already exists.",
            logs=["duplicate"],
            http_status_code=409,
        )
        file = FakeUploadFile(filename="skill.zip", content=b"PK")
        with (
            patch("app.api.skills.get_llm_gateway", return_value=object()),
            patch(
                "app.api.skills.SkillValidationService.upload_skill",
                new=AsyncMock(side_effect=error),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await upload_and_verify_skill_pipeline(file=file, db=object())

        self.assertEqual(409, ctx.exception.status_code)
        self.assertEqual("CONFLICT", ctx.exception.detail["status"])


if __name__ == "__main__":
    unittest.main()
