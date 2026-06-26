from __future__ import annotations

import types
import unittest
import uuid
from unittest.mock import patch


class LangfuseTelemetryContractTests(unittest.TestCase):
    def test_langfuse_sets_default_otel_service_name(self) -> None:
        from app.observability import langfuse as langfuse_module

        self.assertEqual("telecom-agent-backend", langfuse_module.os.environ["OTEL_SERVICE_NAME"])

    def test_tracker_reads_credentials_from_settings_and_uses_v4_generation_api(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls: dict[str, object] = {}

        class FakeLangfuse:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def update_current_generation(self, **kwargs):
                calls["generation"] = kwargs

            def flush(self):
                calls["flushed"] = True

            def get_trace_url(self, *, trace_id=None):
                return f"https://langfuse.test/project/project-id/traces/{trace_id}"

        def fake_observe(**decorator_kwargs):
            calls["observe"] = decorator_kwargs

            def decorate(func):
                def wrapped(*args, **kwargs):
                    calls["observe_call"] = kwargs
                    return func(*args, **kwargs)

                return wrapped

            return decorate

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
        )

        with (
            patch.object(langfuse_module, "Langfuse", FakeLangfuse),
            patch.object(langfuse_module, "observe", fake_observe),
        ):
            tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
            tracker.initialize()
            run_id = str(uuid.uuid4())
            expected_trace_id = uuid.UUID(run_id).hex
            tracker.trace_llm_generation(
                session_id="session-1",
                run_id=run_id,
                model_name="gpt-4o",
                prompt_messages=[{"role": "user", "content": "password=secret"}],
                completion_content="token: abc",
                prompt_tokens=10,
                completion_tokens=5,
            )
            trace_url = tracker.get_trace_url(run_id)

        self.assertEqual(
            {
                "public_key": "pk-test",
                "secret_key": "sk-test",
                "host": "https://langfuse.test",
            },
            calls["init"],
        )
        self.assertEqual(
            {
                "name": "llm_gateway_call",
                "as_type": "generation",
                "capture_input": False,
                "capture_output": False,
            },
            calls["observe"],
        )
        generation = calls["generation"]
        self.assertEqual("telecom_agent_run", generation["name"])
        self.assertEqual("gpt-4o", generation["model"])
        self.assertEqual({"input": 10, "output": 5}, generation["usage_details"])
        self.assertEqual(expected_trace_id, calls["observe_call"]["langfuse_trace_id"])
        self.assertNotIn("secret", str(generation["input"]))
        self.assertNotIn("abc", generation["output"])
        self.assertTrue(calls["flushed"])
        self.assertEqual(
            f"https://langfuse.test/project/project-id/traces/{expected_trace_id}", trace_url
        )


if __name__ == "__main__":
    unittest.main()
