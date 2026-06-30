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

    def test_prompt_management_links_version_and_compiles(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls: dict[str, object] = {}

        class FakePrompt:
            is_fallback = False
            version = 7

            def compile(self, **vars):
                return "compiled:" + vars.get("resource_context", "")

        class FakeLangfuse:
            def __init__(self, **kwargs):
                pass

            def get_prompt(self, name, **kwargs):
                calls["get_prompt"] = {"name": name, **kwargs}
                return FakePrompt()

            def update_current_generation(self, **kwargs):
                calls["generation"] = kwargs

            def flush(self):
                pass

        def fake_observe(**_decorator_kwargs):
            def decorate(func):
                def wrapped(*args, **kwargs):
                    return func(*args, **kwargs)

                return wrapped

            return decorate

        fake_settings = types.SimpleNamespace(
            LANGFUSE_PUBLIC_KEY="pk-test",
            LANGFUSE_SECRET_KEY="sk-test",
            LANGFUSE_HOST="https://langfuse.test",
            LANGFUSE_PROMPT_LABEL="production",
            LANGFUSE_PROMPT_CACHE_TTL_SECONDS=123,
        )

        with (
            patch.object(langfuse_module, "Langfuse", FakeLangfuse),
            patch.object(langfuse_module, "observe", fake_observe),
        ):
            tracker = langfuse_module.LangfuseTelemetryTracker(configuration=fake_settings)
            tracker.initialize()

            self.assertEqual("7", tracker.get_active_prompt_version("0.0.0"))

            tracker.trace_llm_generation(
                session_id="s",
                run_id=str(uuid.uuid4()),
                model_name="gpt-4o",
                prompt_messages=[{"role": "user", "content": "hi"}],
                completion_content="ok",
                prompt_tokens=1,
                completion_tokens=1,
                prompt_name=langfuse_module.PROMPT_NAME,
            )

        self.assertEqual(langfuse_module.PROMPT_NAME, calls["get_prompt"]["name"])
        self.assertEqual("production", calls["get_prompt"]["label"])
        self.assertEqual(123, calls["get_prompt"]["cache_ttl_seconds"])
        self.assertIsInstance(calls["generation"]["prompt"], FakePrompt)

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

    def test_nested_tree_tracing(self) -> None:
        from app.observability import langfuse as langfuse_module

        calls = {
            "trace": [],
            "span": [],
            "generation": [],
            "trace_io": {},
            "flushed": 0,
        }

        class FakeObservation:
            """Span/observation giả: con được tạo qua chính nó (root.start_observation)."""

            def __init__(self, name, as_type, kwargs):
                self.name = name
                self.as_type = as_type
                self.metadata = dict(kwargs.get("metadata") or {})

            def start_observation(self, *, name, as_type="span", **kwargs):
                rec = {
                    "name": name,
                    "as_type": as_type,
                    "input": kwargs.get("input"),
                    "output": kwargs.get("output"),
                    "model": kwargs.get("model"),
                    "usage": kwargs.get("usage_details"),
                }
                if as_type == "generation":
                    calls["generation"].append(rec)
                else:
                    calls["span"].append(rec)
                return FakeObservation(name, as_type, kwargs)

            def update(self, **kwargs):
                # Output cấp trace giờ suy ra từ output của span gốc (qua update).
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
                # Chỉ span GỐC được tạo qua client; con đi qua root.start_observation.
                calls["trace"].append(
                    {
                        "name": name,
                        "input": kwargs.get("input"),
                        "session_id": (kwargs.get("metadata") or {}).get("session_id"),
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
            tracker.trace_generation(
                run_id=run_id,
                generation_name="AI Step 0",
                model_name="gpt-4o",
                input_data="user content with password=secret_password",
                output_data="calling tool: test",
                input_tokens=5,
                output_tokens=3,
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

        # Đúng 1 span gốc cho cả run (không nhân đôi).
        self.assertEqual(1, len(calls["trace"]))
        self.assertEqual("telecom_agent_run", calls["trace"][0]["name"])
        self.assertEqual("session-nested", calls["trace"][0]["session_id"])
        # Redaction DLP trên trace input (span gốc) + generation input + tool output.
        self.assertNotIn("secret_password", str(calls["trace"][0]["input"]))
        self.assertEqual(1, len(calls["generation"]))
        self.assertEqual("AI Step 0", calls["generation"][0]["name"])
        self.assertNotIn("secret_password", str(calls["generation"][0]["input"]))
        self.assertEqual({"input": 5, "output": 3}, calls["generation"][0]["usage"])
        self.assertEqual(1, len(calls["span"]))
        self.assertEqual("tool: test", calls["span"][0]["name"])
        self.assertEqual("tool", calls["span"][0]["as_type"])
        self.assertNotIn("secret_password", str(calls["span"][0]["output"]))
        # Output cấp trace + flush ĐÚNG 1 lần (hết lag flush từng bước).
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
