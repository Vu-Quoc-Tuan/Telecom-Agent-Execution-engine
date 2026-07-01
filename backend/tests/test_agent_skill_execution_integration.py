# backend/tests/test_agent_skill_execution_integration.py
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.database.connection import SessionLocal
from app.database.repositories.sessions import SessionRepository
from app.database.repositories.skills import SkillRepository
from app.llm.schemas import FinishReason, LLMResponse, MessageRole, NormalizedToolCall
from app.sandbox.docker_executor import SandboxExecutionResult
from app.services.agent_execution import AgentExecutionService
from app.services.skills import SkillValidationService


# Sandbox simulator chạy script bằng interpreter hiện tại trong thư mục tạm.
# Cùng interface với DockerSandboxExecutor (execute_skill_script / validate_skill_script)
# để dùng cho cả smoke-test lúc upload lẫn runtime, không cần Docker thật trong test.
class LocalSubprocessSandboxExecutor:
    @staticmethod
    def _run_sync(
        script_path: str,
        arguments: dict | None,
        bundled_files: dict | None,
        timeout_seconds: int | None,
    ) -> SandboxExecutionResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            if bundled_files:
                for rel_path, file_info in bundled_files.items():
                    full_path = os.path.join(tmpdir, rel_path)
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    content = file_info.get("content", "")
                    if isinstance(content, bytes):
                        with open(full_path, "wb") as handle:
                            handle.write(content)
                    else:
                        with open(full_path, "w", encoding="utf-8") as handle:
                            handle.write(content)

            with open(os.path.join(tmpdir, "args.json"), "w", encoding="utf-8") as handle:
                json.dump(arguments or {}, handle)

            try:
                res = subprocess.run(
                    [sys.executable, script_path],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds or 15,
                )
                return SandboxExecutionResult(
                    stdout=res.stdout,
                    stderr=res.stderr,
                    exit_code=res.returncode,
                    timed_out=False,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = (
                    exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                )
                stderr = (
                    exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                )
                return SandboxExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=124,
                    timed_out=True,
                )

    async def execute_skill_script(
        self,
        *,
        script_path: str,
        arguments: dict = None,
        bundled_files: dict = None,
        timeout_seconds: int = None,
    ) -> SandboxExecutionResult:
        return await asyncio.to_thread(
            self._run_sync, script_path, arguments, bundled_files, timeout_seconds
        )

    async def validate_skill_script(
        self,
        *,
        script_path: str,
        arguments: dict = None,
        bundled_files: dict = None,
        timeout_seconds: int = 15,
    ) -> SandboxExecutionResult:
        return await self.execute_skill_script(
            script_path=script_path,
            arguments=arguments,
            bundled_files=bundled_files,
            timeout_seconds=timeout_seconds,
        )


class DeterministicGateway:
    """Local LLM stand-in for router/tool integration tests.

    It keeps these tests focused on our graph, routing, validation, approval,
    and execution logic instead of depending on a live model choosing the same
    tool every run.
    """

    provider = "fake"
    providers = ("fake",)

    async def invoke(self, *, messages, system_prompt=None, tools=None, options=None, **kwargs):
        text = "\n".join((message.content or "") for message in messages)
        lowered = text.lower()
        tool_names = {tool.name for tool in tools or []}
        tool_messages = [message for message in messages if message.role is MessageRole.TOOL]

        if not tools:
            if "propose a minimal json object keyed by script path" in lowered:
                return LLMResponse(
                    provider="fake",
                    model="deterministic",
                    finish_reason=FinishReason.STOP,
                    content="{}",
                )
            return LLMResponse(
                provider="fake",
                model="deterministic",
                finish_reason=FinishReason.STOP,
                content=(
                    '{"domain_score":0.95,"reason":"telecom NOC workflow",'
                    '"suspicious_points":"None"}'
                ),
            )

        if any(
            marker in (message.content or "")
            for message in tool_messages
            for marker in ("degraded", "PING HNI-002", "nginx restarted successfully")
        ):
            return LLMResponse(
                provider="fake",
                model="deterministic",
                finish_reason=FinishReason.STOP,
                content="Đã hoàn tất kiểm tra.",
            )

        if any("<skill_content" in (message.content or "") for message in tool_messages):
            if "run_skill_script" in tool_names:
                skill_name = None
                for message in messages:
                    for tool_call in message.tool_calls or []:
                        if tool_call.name == "load_skill":
                            skill_name = tool_call.arguments.get("skill_name")
                return LLMResponse(
                    provider="fake",
                    model="deterministic",
                    finish_reason=FinishReason.TOOL,
                    tool_calls=[
                        NormalizedToolCall(
                            id="call-script",
                            name="run_skill_script",
                            arguments={
                                "skill_name": skill_name,
                                "script_path": "scripts/check_kpi.py",
                                "arguments": {"node_id": "HNI-002"},
                            },
                        )
                    ],
                )

        if "ping" in lowered and "ping_node" in tool_names:
            return LLMResponse(
                provider="fake",
                model="deterministic",
                finish_reason=FinishReason.TOOL,
                tool_calls=[
                    NormalizedToolCall(
                        id="call-ping",
                        name="ping_node",
                        arguments={"node_name": "HNI-002", "count": 3},
                    )
                ],
            )

        forced_tool = getattr(getattr(options, "tool_choice", None), "tool_name", None)
        if forced_tool == "load_skill" and "load_skill" in tool_names:
            load_skill = next(tool for tool in tools if tool.name == "load_skill")
            skill_names = (
                load_skill.input_schema.get("properties", {}).get("skill_name", {}).get("enum", [])
            )
            if skill_names:
                return LLMResponse(
                    provider="fake",
                    model="deterministic",
                    finish_reason=FinishReason.TOOL,
                    tool_calls=[
                        NormalizedToolCall(
                            id="call-load",
                            name="load_skill",
                            arguments={"skill_name": skill_names[0]},
                        )
                    ],
                )

        return LLMResponse(
            provider="fake",
            model="deterministic",
            finish_reason=FinishReason.STOP,
            content="Không cần gọi tool.",
        )


class AgentSkillExecutionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = SessionLocal()
        self.temp_zips = []
        self.created_skill_ids = []
        self.created_session_ids = []

        # Initialize memory checkpointer and set agent app
        from app.agent.checkpointer import WorkflowCheckpointer
        from app.agent.graph import build_telecom_agent
        from app.config import settings

        checkpointer = WorkflowCheckpointer(settings=settings, backend="memory")
        saver = await checkpointer.initialize()
        agent_graph = build_telecom_agent(checkpointer=saver)
        AgentExecutionService.configure(agent_graph)

    async def asyncTearDown(self):
        # Cleanup skills created during the test
        for skill_id in self.created_skill_ids:
            try:
                SkillRepository.delete_skill(self.db, skill_id)
            except Exception:
                pass

        # Cleanup sessions
        for session_id in self.created_session_ids:
            try:
                SessionRepository.delete_session(self.db, session_id)
            except Exception:
                pass

        self.db.close()

        def _cleanup_temp_zips() -> None:
            for f in self.temp_zips:
                Path(f).unlink(missing_ok=True)

        await asyncio.to_thread(_cleanup_temp_zips)

    def create_skill_zip_bytes(self, name, description, script_name, script_content) -> bytes:
        skill_md = f"""---
name: {name}
description: "{description}"
---
# Check KPI Skill
This skill is a test skill for verifying the full sandbox execution and agent routing loop.
"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("SKILL.md", skill_md)
            zf.writestr(script_name, script_content)
        return buf.getvalue()

    @patch("app.agent.nodes._sandbox_available", return_value=True)
    @patch("app.sandbox.docker_executor.build_sandbox_executor_from_settings")
    async def test_full_agent_skill_execution_pipeline(
        self, mock_upload_builder, mock_sandbox_available
    ):
        # Setup local micro-VM sandbox executor simulator for both upload smoke test and agent runtime execution
        sandbox_sim = LocalSubprocessSandboxExecutor()
        mock_upload_builder.return_value = sandbox_sim

        # 1. Define a clean Python script that does a KPI diagnostic check and outputs JSON matching a schema
        script_content = """import json
def main():
    args = {}
    try:
        with open("args.json", "r", encoding="utf-8") as f:
            args = json.load(f)
    except FileNotFoundError:
        pass
    node_id = args.get("node_id", "HNI-001")
    # Return output contract compliant JSON
    print(json.dumps({
        "status": "degraded",
        "node_id": node_id,
        "kpi_value": 78.4,
        "diagnostic": "Latency is high and throughput has dropped below threshold."
    }))
if __name__ == "__main__":
    main()
"""

        skill_name = f"test-diag-kpi-{uuid.uuid4().hex[:8]}"
        zip_bytes = self.create_skill_zip_bytes(
            name=skill_name,
            description="Diagnostic skill for checking cell latency and KPI status on node.",
            script_name="scripts/check_kpi.py",
            script_content=script_content,
        )

        print(f"\n[INTEGRATION TEST] Uploading skill '{skill_name}'...")
        service = SkillValidationService()

        from app.services.skills import SkillUploadCommand

        result = await service.upload_skill(
            db=self.db,
            llm_gateway=DeterministicGateway(),
            command=SkillUploadCommand(zip_bytes=zip_bytes),
        )

        skill_id = uuid.UUID(result.skill_id)
        self.created_skill_ids.append(skill_id)

        self.assertEqual(result.status, "PENDING_REVIEW")
        db_skill = SkillRepository.get_skill_by_id(self.db, skill_id)
        self.assertEqual(db_skill.status, "testing")
        print(
            f"[INTEGRATION TEST] Skill uploaded successfully. Response Status: {result.status}, DB Status: {db_skill.status}"
        )

        # 2. Approve the skill so it becomes READY and available to the Agent
        print(f"[INTEGRATION TEST] Approving skill '{skill_name}'...")
        approved = SkillRepository.approve_skill(self.db, skill_id)
        self.assertEqual(approved.status, "ready")
        print(f"[INTEGRATION TEST] Skill approved. Status: {approved.status}")

        # 3. Create a chat session to interact with the Agent
        session = SessionRepository.create_session(self.db, title="Test Session")
        session_id = session.id
        self.created_session_ids.append(session_id)
        print(f"[INTEGRATION TEST] Created session: {session_id}")

        # 4. Prompt the agent to run this specific skill
        prompt = (
            f"Yêu cầu: Hãy chạy ngay lập tức skill {skill_name} cho node HNI-002 để kiểm tra KPI.\n"
            f"Bạn PHẢI sử dụng công cụ run_skill_script với script_path='scripts/check_kpi.py' "
            f'và truyền node_id vào đối số arguments: {{"node_id": "HNI-002"}}.\n'
            f"Lưu ý: Công cụ run_skill_script sẽ tự động ghi các đối số này vào file args.json trong sandbox, "
            f"do đó bạn không cần tạo file args.json thủ công. Hãy chạy công cụ này ngay lập tức để hoàn thành yêu cầu."
        )
        print(f"[INTEGRATION TEST] Sending prompt to agent: '{prompt}'")

        events = []

        async for event_type, payload in AgentExecutionService.run_agent_lifecycle(
            db=self.db,
            llm_gateway=DeterministicGateway(),
            session_id=session_id,
            user_content=prompt,
            provider="fake",
            model="deterministic",
            selected_skill=skill_name,
        ):
            events.append((event_type, payload))
            print(f"[EVENT] {event_type}: {payload}")
            # Optional trace printing
            if event_type == "text_delta":
                sys.stdout.write(payload.get("delta", ""))
                sys.stdout.flush()
            elif event_type == "timeline_updated":
                last_node = payload.get("last_executed_node", "")
                print(f"\n[Timeline update] node: {last_node}")

        print("\n[INTEGRATION TEST] Execution finished.")

        # 5. Assertions on Agent behavior
        # Ensure the agent successfully completed the run
        completion_events = [e for e in events if e[0] == "run_completed"]
        self.assertEqual(len(completion_events), 1, "Agent execution should complete successfully.")

        # Ensure there was a tool call execution event for run_skill_script
        timeline_events = [e for e in events if e[0] == "timeline_updated"]
        tool_executed = False
        for te in timeline_events:
            for step in te[1].get("steps", []):
                if step.get("tool_name") == "run_skill_script":
                    tool_executed = True
                    tool_input = step.get("tool_input", {})
                    # Ensure the agent called the correct skill name and script path
                    self.assertEqual(tool_input.get("skill_name"), skill_name)
                    self.assertEqual(tool_input.get("script_path"), "scripts/check_kpi.py")
                    # Ensure arguments contain the node_id
                    script_args = tool_input.get("arguments", {})
                    self.assertIn("HNI-002", str(script_args.get("node_id", "")))

                    # Ensure the output contract passed successfully
                    self.assertEqual(step.get("tool_status"), "completed")
                    self.assertFalse(step.get("is_error"))
                    tool_output = json.loads(step.get("tool_output", "{}"))
                    self.assertEqual(tool_output.get("status"), "degraded")
                    self.assertEqual(tool_output.get("node_id"), "HNI-002")
                    print(f"[INTEGRATION TEST] Verified tool call: {step}")

        self.assertTrue(tool_executed, "Agent must execute the skill script tool.")
        print("[INTEGRATION TEST] All assertions passed successfully! 100% Correct.")

    @patch("app.agent.builtin_tools._connector_is_configured", return_value=True)
    async def test_default_builtin_capability_execution(self, mock_connector_configured):
        # We patch execute_builtin_tool inside app.agent.nodes
        from app.agent.nodes import execute_builtin_tool as real_execute

        async def side_effect(tool_name, arguments, db, settings, approval_confirmations=0):
            if tool_name == "ping_node":
                return (
                    "PING HNI-002 (10.0.0.2) 56(84) bytes of data.\n"
                    "3 packets transmitted, 3 received, 0% packet loss, time 2003ms\n"
                    "rtt min/avg/max/mdev = 0.045/0.052/0.061/0.009 ms",
                    False,
                )
            return await real_execute(tool_name, arguments, db, settings, approval_confirmations)

        # Create session
        session = SessionRepository.create_session(self.db, title="Test Session Default")
        session_id = session.id
        self.created_session_ids.append(session_id)

        prompt = "Hãy ping trạm HNI-002 với 3 gói tin để xem trễ thế nào."

        events = []

        with patch("app.agent.nodes.execute_builtin_tool", side_effect=side_effect):
            async for event_type, payload in AgentExecutionService.run_agent_lifecycle(
                db=self.db,
                llm_gateway=DeterministicGateway(),
                session_id=session_id,
                user_content=prompt,
                provider="fake",
                model="deterministic",
            ):
                events.append((event_type, payload))
                print(f"[EVENT] {event_type}: {payload}")

        completion_events = [e for e in events if e[0] == "run_completed"]
        self.assertEqual(len(completion_events), 1, "Agent execution should complete successfully.")

        # Verify the ping_node tool was executed
        timeline_events = [e for e in events if e[0] == "timeline_updated"]
        tool_executed = False
        for te in timeline_events:
            for step in te[1].get("steps", []):
                if step.get("tool_name") == "ping_node":
                    tool_executed = True
                    tool_input = step.get("tool_input", {})
                    self.assertEqual(tool_input.get("node_name"), "HNI-002")
                    self.assertEqual(tool_input.get("count"), 3)
                    self.assertEqual(step.get("tool_status"), "completed")
                    self.assertFalse(step.get("is_error"))

        self.assertTrue(tool_executed, "Agent must execute the ping_node tool.")
        print("[INTEGRATION TEST] Default built-in capability execution verified successfully.")


if __name__ == "__main__":
    unittest.main()
