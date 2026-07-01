from __future__ import annotations

import types
import unittest
import uuid
from unittest.mock import patch


class LangfuseTelemetryContractTests(unittest.TestCase):
    def test_langfuse_sets_default_otel_service_name(self) -> None:
        from app.observability import langfuse as langfuse_module

        self.assertEqual("telecom-agent-backend", langfuse_module.os.environ["OTEL_SERVICE_NAME"])

    def test_tracker_reads_credentials_and_traces_turn_span(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls: dict[str, object] = {
            "observations": [],
            "updates": [],
            "otel_attributes": {},
            "ended": 0,
            "flushed": 0,
        }

        class FakeOtelSpan:
            def is_recording(self):
                return True

            def set_attribute(self, key, value):
                calls["otel_attributes"][key] = value

        class FakeObservation:
            def __init__(self, name, as_type, trace_id="fake-trace-id", span_id="fake-span-id"):
                self.name = name
                self.as_type = as_type
                self.trace_id = trace_id
                self.id = span_id
                self._otel_span = FakeOtelSpan()

            def update(self, **kwargs):
                calls["updates"].append(kwargs)

            def end(self, **kwargs):
                calls["ended"] += 1

        class FakeLangfuse:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def start_observation(self, *, name, as_type="span", **kwargs):
                calls["observations"].append({"name": name, "as_type": as_type, **kwargs})
                trace_id = (kwargs.get("trace_context") or {}).get("trace_id") or "fake-trace-id"
                return FakeObservation(name, as_type, trace_id=trace_id, span_id="fake-span-id")

            def flush(self):
                calls["flushed"] += 1

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
        )

        with patch.object(langfuse_module, "Langfuse", FakeLangfuse):
            tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
            tracker.initialize()
            run_id = str(uuid.uuid4())
            tracker.trace_run_start(
                session_id="session-1",
                run_id=run_id,
                input_content="password=secret",
            )
            tracker.trace_run_end(
                run_id=run_id,
                output_content="final answer",
                status="completed",
            )

        self.assertEqual(
            {
                "public_key": "pk-test",
                "secret_key": "sk-test",
                "host": "https://langfuse.test",
            },
            calls["init"],
        )
        self.assertEqual(1, len(calls["observations"]))
        turn = calls["observations"][0]
        self.assertEqual("agent_turn #1", turn["name"])
        self.assertEqual("span", turn["as_type"])
        self.assertEqual(run_id.replace("-", "").lower(), turn["trace_context"]["trace_id"])
        self.assertNotIn("secret", str(turn["input"]))
        self.assertEqual(
            {"output": "final answer", "metadata": {"status": "completed"}},
            calls["updates"][0],
        )
        self.assertEqual(1, calls["ended"])
        self.assertEqual(1, calls["flushed"])
        self.assertEqual("session-1", calls["otel_attributes"].get("session.id"))
        self.assertEqual("agent_turn #1", calls["otel_attributes"].get("langfuse.trace.name"))
        self.assertEqual("final answer", calls["otel_attributes"].get("langfuse.trace.output"))

    def test_prompt_management_uses_configured_label(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls: dict[str, object] = {}

        class FakePrompt:
            is_fallback = False
            version = 7

        class FakeLangfuse:
            def __init__(self, **kwargs):
                pass

            def get_prompt(self, name, **kwargs):
                calls["get_prompt"] = {"name": name, **kwargs}
                return FakePrompt()

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
            LANGFUSE_PROMPT_LABEL="production",
            LANGFUSE_PROMPT_CACHE_TTL_SECONDS=123,
        )

        with patch.object(langfuse_module, "Langfuse", FakeLangfuse):
            tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
            tracker.initialize()
            self.assertEqual("7", tracker.get_active_prompt_version("0.0.0"))

        self.assertEqual(langfuse_module.PROMPT_NAME, calls["get_prompt"]["name"])
        self.assertEqual("production", calls["get_prompt"]["label"])
        self.assertEqual(123, calls["get_prompt"]["cache_ttl_seconds"])

    def test_prompt_helpers_noop_without_client(self) -> None:
        from app.observability import langfuse as langfuse_module

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="",
            LANGFUSE_SECRET_KEY="",
            LANGFUSE_HOST="https://langfuse.test",
        )
        tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
        tracker.initialize()

        self.assertIsNone(tracker.get_system_prompt("fallback"))
        self.assertEqual("0.1.0", tracker.get_active_prompt_version("0.1.0"))

    def test_langchain_callback_handler_targets_active_turn_span(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls: dict[str, object] = {}

        class FakeCallbackHandler:
            def __init__(self, **kwargs):
                calls["handler_kwargs"] = kwargs

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
        )
        tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
        tracker._client = object()
        run_id = str(uuid.uuid4())
        tracker._active_runs[uuid.UUID(run_id).hex] = types.SimpleNamespace(
            trace_id="session-trace-id",
            id="turn-span-id",
        )

        with patch("langfuse.langchain.CallbackHandler", FakeCallbackHandler):
            handler = tracker.get_langchain_callback_handler(run_id)

        self.assertIsInstance(handler, FakeCallbackHandler)
        self.assertEqual(
            {
                "public_key": "pk-test",
                "trace_context": {
                    "trace_id": "session-trace-id",
                    "parent_span_id": "turn-span-id",
                },
            },
            calls["handler_kwargs"],
        )

    def test_langchain_callback_handler_noops_without_active_turn_span(self) -> None:
        from app.observability import langfuse as langfuse_module

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
        )
        tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
        tracker._client = object()

        self.assertIsNone(tracker.get_langchain_callback_handler(str(uuid.uuid4())))

    def test_nested_tree_tracing(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls = {
            "trace": [],
            "span": [],
            "trace_io": {},
            "flushed": 0,
        }

        class FakeObservation:
            def __init__(self, name, as_type, kwargs):
                self.name = name
                self.as_type = as_type
                self.metadata = dict(kwargs.get("metadata") or {})
                self.trace_id = (kwargs.get("trace_context") or {}).get(
                    "trace_id"
                ) or "sessionnested"
                self.id = "fake-span-id"

            def start_observation(self, *, name, as_type="span", **kwargs):
                calls["span"].append(
                    {
                        "name": name,
                        "as_type": as_type,
                        "input": kwargs.get("input"),
                        "output": kwargs.get("output"),
                    }
                )
                return FakeObservation(name, as_type, kwargs)

            def update(self, **kwargs):
                if kwargs.get("output") is not None:
                    calls["trace_io"]["output"] = kwargs["output"]
                if kwargs.get("metadata"):
                    calls["trace_io"].setdefault("metadata", {}).update(kwargs["metadata"])

            def end(self, **kwargs):
                pass

        class FakeLangfuse:
            def __init__(self, **kwargs):
                pass

            def start_observation(self, *, name, as_type="span", **kwargs):
                calls["trace"].append(
                    {
                        "name": name,
                        "input": kwargs.get("input"),
                        "session_id": (kwargs.get("metadata") or {}).get("session_id"),
                        "run_id": (kwargs.get("metadata") or {}).get("run_id"),
                        "trace_id": (kwargs.get("trace_context") or {}).get("trace_id"),
                    }
                )
                return FakeObservation(name, as_type, kwargs)

            def flush(self):
                calls["flushed"] += 1

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
        )

        with patch.object(langfuse_module, "Langfuse", FakeLangfuse):
            tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
            tracker.initialize()
            run_id = str(uuid.uuid4())

            tracker.trace_run_start(
                session_id="session-nested",
                run_id=run_id,
                input_content="password=secret_password and more",
            )
            tracker.trace_span(
                run_id=run_id,
                span_name="tool: test",
                input_data="some args",
                output_data="output password=secret_password",
                status="completed",
            )
            tracker.trace_run_end(
                run_id=run_id,
                output_content="final answer",
                status="completed",
            )

        self.assertEqual(1, len(calls["trace"]))
        self.assertEqual("agent_turn #1", calls["trace"][0]["name"])
        self.assertEqual("session-nested", calls["trace"][0]["session_id"])
        self.assertEqual(run_id.replace("-", ""), calls["trace"][0]["run_id"])
        self.assertEqual(run_id.replace("-", ""), calls["trace"][0]["trace_id"])
        self.assertNotIn("secret_password", str(calls["trace"][0]["input"]))
        self.assertEqual(1, len(calls["span"]))
        self.assertEqual("tool: test", calls["span"][0]["name"])
        self.assertEqual("tool", calls["span"][0]["as_type"])
        self.assertNotIn("secret_password", str(calls["span"][0]["output"]))
        self.assertEqual("final answer", calls["trace_io"].get("output"))
        self.assertEqual(1, calls["flushed"])


class SystemPromptBuildTests(unittest.TestCase):
    @patch("app.agent.prompts.telemetry_tracker.get_system_prompt", return_value=None)
    def test_build_system_prompt_falls_back_and_compiles_placeholders(self, mock_get) -> None:
        from app.agent.prompts import build_system_prompt

        prompt = build_system_prompt(ready_skills=[], settings=None, selected_skill_name=None)

        # Phần động phải được thay, không còn placeholder thô.
        self.assertNotIn("{{resource_context}}", prompt)
        self.assertNotIn("{{skill_section}}", prompt)
        # Base + skill_section + resource_context đều có mặt.
        self.assertIn("AI Agent vận hành mạng viễn thông", prompt)
        self.assertIn("## Skill vận hành khả dụng", prompt)
        self.assertIn("## Tài nguyên backend đang cấu hình", prompt)


if __name__ == "__main__":
    unittest.main()
