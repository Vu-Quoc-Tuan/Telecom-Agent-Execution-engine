from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.common.exceptions import SkillRuntimeError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_OUTPUT_CHARS = 50_000
DEFAULT_SANDBOX_IMAGE = "telecom-agent-sandbox:latest"
SANDBOX_WORKSPACE_DIR = "/workspace"
ARGS_FILE_NAME = "args.json"


@dataclass(frozen=True)
class SandboxExecutionResult:
    """Kết quả trả về từ một phiên thực thi sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    output_truncated: bool = False


class DockerSandboxExecutor:
    """Chạy skill script trong một Docker container ephemeral trên host."""

    def __init__(
        self,
        *,
        image: str = DEFAULT_SANDBOX_IMAGE,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        memory: str = "256m",
        cpus: str = "1.0",
        docker_binary: str = "docker",
        network: str = "none",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._image = image
        self._timeout_seconds = int(timeout_seconds)
        self._max_output_chars = int(max_output_chars)
        self._memory = memory
        self._cpus = str(cpus)
        self._docker = docker_binary
        self._network = network
        self._extra_env = extra_env or {}

    async def execute_skill_script(
        self,
        *,
        script_path: str,
        arguments: dict[str, Any] | None = None,
        bundled_files: dict[str, dict[str, Any]] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecutionResult:
        """Chạy một script đã duyệt theo đường dẫn trong gói skill."""
        safe_path = self._safe_relative_path(script_path)
        return await asyncio.to_thread(
            self._run_in_container,
            safe_path,
            arguments or {},
            bundled_files or {},
            timeout_seconds,
        )

    async def validate_skill_script(
        self,
        *,
        script_path: str,
        arguments: dict[str, Any] | None = None,
        bundled_files: dict[str, dict[str, Any]] | None = None,
        timeout_seconds: int = 15,
    ) -> SandboxExecutionResult:
        """Smoke-test upload without network access or infrastructure credentials."""
        safe_path = self._safe_relative_path(script_path)
        return await asyncio.to_thread(
            self._run_in_container,
            safe_path,
            arguments or {},
            bundled_files or {},
            timeout_seconds,
            "none",
            False,
        )

    # --- Nội bộ -------------------------------------------------------------

    @staticmethod
    def _safe_relative_path(raw: str) -> str:
        text = str(raw).strip()
        path = PurePosixPath(text)
        if (
            not text
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in text
        ):
            raise SkillRuntimeError(f"Đường dẫn script không hợp lệ: '{raw}'.")
        return path.as_posix()

    def _materialize_workspace(
        self,
        workspace: Path,
        arguments: dict[str, Any],
        bundled_files: dict[str, dict[str, Any]],
    ) -> None:
        for relative_path, record in bundled_files.items():
            if not isinstance(record, dict) or "content" not in record:
                continue
            target = workspace / self._safe_relative_path(relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            encoding = record.get("encoding", "utf-8")
            if encoding == "utf-8":
                target.write_text(str(record["content"]), encoding="utf-8")
            elif encoding == "base64":
                target.write_bytes(base64.b64decode(record["content"]))
            else:
                raise SkillRuntimeError(
                    f"Resource '{relative_path}' dùng encoding không hỗ trợ: {encoding}."
                )
        (workspace / ARGS_FILE_NAME).write_text(
            json.dumps(arguments, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _docker_run_command(
        self,
        workspace: Path,
        container_name: str,
        script_path: str,
        *,
        network: str | None = None,
        forward_connection_env: bool = True,
    ) -> list[str]:
        command = [
            self._docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            network or self._network,
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            "--pids-limit",
            "128",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "-v",
            f"{workspace}:{SANDBOX_WORKSPACE_DIR}:ro",
            "-w",
            SANDBOX_WORKSPACE_DIR,
        ]
        # Forward database and connection environment variables to the sandbox
        env_vars = [
            "CH_HOST",
            "CH_PORT",
            "CH_USER",
            "CH_PASSWORD",
            "CH_DATABASE",
            "PG_DSN",
            "SSH_HOST",
            "SSH_PORT",
            "SSH_USER",
            "SSH_PASSWORD",
        ]
        if forward_connection_env:
            # First use variables defined in settings/extra_env
            forwarded = dict(self._extra_env)
            # Overwrite with direct os.environ if they are set in parent process
            for var in env_vars:
                val = os.environ.get(var)
                if val is not None:
                    forwarded[var] = val

            for var, val in forwarded.items():
                if val:
                    command += ["-e", f"{var}={val}"]

        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        command += [self._image, "python3", script_path]
        return command

    def _run_in_container(
        self,
        script_path: str,
        arguments: dict[str, Any],
        bundled_files: dict[str, dict[str, Any]],
        timeout_seconds: int | None,
        network: str | None = None,
        forward_connection_env: bool = True,
    ) -> SandboxExecutionResult:
        if shutil.which(self._docker) is None:
            raise SkillRuntimeError(
                "Không tìm thấy Docker trên host. Cần Docker daemon để chạy run_skill_script."
            )

        effective_timeout = int(timeout_seconds or self._timeout_seconds)
        tmp_dir = tempfile.mkdtemp(prefix="skill_sandbox_")
        workspace = Path(tmp_dir)
        container_name = f"skill_sb_{workspace.name}"
        try:
            self._materialize_workspace(workspace, arguments, bundled_files)
            command = self._docker_run_command(
                workspace,
                container_name,
                script_path,
                network=network,
                forward_connection_env=forward_connection_env,
            )
            try:
                completed = subprocess.run(  # noqa: S603 - lệnh dựng từ tham số nội bộ
                    command,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                )
            except subprocess.TimeoutExpired:
                logger.warning("Docker sandbox timed out for script %s", script_path)
                self._force_remove_container(container_name)
                return SandboxExecutionResult(
                    stdout="",
                    stderr="[TIMEOUT] Script vượt quá thời gian cho phép trong sandbox.",
                    exit_code=124,
                    timed_out=True,
                )

            stdout, truncated_out = self._truncate(completed.stdout or "")
            stderr, truncated_err = self._truncate(completed.stderr or "")
            return SandboxExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=completed.returncode,
                timed_out=False,
                output_truncated=truncated_out or truncated_err,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _force_remove_container(self, container_name: str) -> None:
        try:
            subprocess.run(  # noqa: S603 - tên container nội bộ
                [self._docker, "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.SubprocessError, OSError):
            logger.warning("Không kill được container sandbox %s", container_name, exc_info=True)

    def _truncate(self, text: str) -> tuple[str, bool]:
        if len(text) > self._max_output_chars:
            return text[: self._max_output_chars] + "\n\n... [OUTPUT TRUNCATED] ...", True
        return text, False


def docker_is_available(docker_binary: str = "docker") -> bool:
    """True nếu tìm thấy Docker CLI trên host."""
    return shutil.which(docker_binary) is not None


def sandbox_available(settings) -> bool:
    """True nếu sandbox được bật và Docker CLI tồn tại trên host.

    Lưu ý: chỉ kiểm tra sự tồn tại của Docker CLI, không kiểm tra Docker daemon
    đang chạy hay không. Lỗi daemon sẽ được phát hiện tại thời điểm thực thi.
    """
    if settings is None:
        return False
    if not bool(getattr(settings, "SANDBOX_ENABLED", True)):
        return False
    return docker_is_available()


def build_sandbox_executor_from_settings(settings) -> DockerSandboxExecutor | None:
    """Khởi tạo DockerSandboxExecutor từ Settings. Trả về None nếu sandbox không khả dụng."""
    if not sandbox_available(settings):
        return None
    image = (
        getattr(settings, "SANDBOX_IMAGE", "") or DEFAULT_SANDBOX_IMAGE
    ).strip() or DEFAULT_SANDBOX_IMAGE
    timeout = getattr(settings, "SANDBOX_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    memory = (getattr(settings, "SANDBOX_MEMORY", "") or "256m").strip() or "256m"
    cpus = str(getattr(settings, "SANDBOX_CPUS", "") or "1.0").strip() or "1.0"
    network = (getattr(settings, "SANDBOX_NETWORK", "none") or "none").strip() or "none"

    pg_host = getattr(settings, "EXTERNAL_POSTGRES_HOST", "")
    pg_port = getattr(settings, "EXTERNAL_POSTGRES_PORT", 5432)
    pg_user = getattr(settings, "EXTERNAL_POSTGRES_USER", "")
    pg_password = getattr(settings, "EXTERNAL_POSTGRES_PASSWORD", "")
    pg_db = getattr(settings, "EXTERNAL_POSTGRES_DATABASE", "")
    pg_dsn = ""
    if pg_host:
        from urllib.parse import quote_plus

        pg_dsn = f"postgresql://{quote_plus(pg_user)}:{quote_plus(pg_password)}@{pg_host}:{pg_port}/{pg_db}"

    extra_env = {
        "CH_HOST": getattr(settings, "CLICKHOUSE_HOST", ""),
        "CH_PORT": str(getattr(settings, "CLICKHOUSE_PORT", 8123)),
        "CH_USER": getattr(settings, "CLICKHOUSE_USER", ""),
        "CH_PASSWORD": getattr(settings, "CLICKHOUSE_PASSWORD", ""),
        "CH_DATABASE": getattr(settings, "CLICKHOUSE_DATABASE", ""),
        "PG_DSN": pg_dsn,
        "SSH_HOST": getattr(settings, "SSH_HOST", ""),
        "SSH_PORT": str(getattr(settings, "SSH_PORT", 22)),
        "SSH_USER": getattr(settings, "SSH_USER", ""),
        "SSH_PASSWORD": getattr(settings, "SSH_PASSWORD", ""),
    }

    return DockerSandboxExecutor(
        image=image,
        timeout_seconds=int(timeout),
        memory=memory,
        cpus=cpus,
        network=network,
        extra_env=extra_env,
    )
