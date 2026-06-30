# backend/app/sandbox/docker_executor.py
"""Sandbox thực thi skill script bằng Docker container ephemeral trên host.

Đây là backend sandbox DUY NHẤT của hệ thống (đã bỏ Cube/E2B). Không cần API key,
không cần KVM/PVM kernel — chỉ cần Docker daemon trên host. Mỗi lần chạy:
  1. Ghi bundled_files + args.json ra một thư mục tạm (mount thành workspace).
  2. ``docker run --rm --network none`` (giới hạn CPU/RAM/PID, chạy bằng uid host)
     thực thi ``python3 <script_path>`` trong workspace.
  3. Thu stdout/stderr/exit code; nếu quá giờ thì kill container.
  4. Xoá thư mục tạm.

``run_skill_script`` chỉ chạy script đã duyệt Vòng 5 (đã qua AST scan + hash check),
nên cô lập mức container là đủ. Container chia sẻ host kernel nên yếu hơn MicroVM;
không khuyến nghị cho payload không tin cậy ngoài luồng skill đã duyệt.
"""

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
DEFAULT_SANDBOX_IMAGE = "python:3.12-slim"
SANDBOX_WORKSPACE_DIR = "/workspace"
ARGS_FILE_NAME = "args.json"
# Đệm thêm so với timeout của script để Docker kịp teardown trước khi subprocess bị giết.
DOCKER_TEARDOWN_BUFFER_SECONDS = 5


@dataclass(frozen=True)
class SandboxExecutionResult:
    """Kết quả trả về từ một phiên thực thi sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    output_truncated: bool = False
    error: str | None = None


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
    ) -> None:
        self._image = image
        self._timeout_seconds = int(timeout_seconds)
        self._max_output_chars = int(max_output_chars)
        self._memory = memory
        self._cpus = str(cpus)
        self._docker = docker_binary

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
        """Smoke-test một script lúc upload (Vòng 5), dùng cùng runner như runtime."""
        return await self.execute_skill_script(
            script_path=script_path,
            arguments=arguments or {},
            bundled_files=bundled_files,
            timeout_seconds=timeout_seconds,
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
        self, workspace: Path, container_name: str, script_path: str
    ) -> list[str]:
        command = [
            self._docker,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            "--pids-limit",
            "128",
            "-v",
            f"{workspace}:{SANDBOX_WORKSPACE_DIR}",
            "-w",
            SANDBOX_WORKSPACE_DIR,
        ]
        # Chạy bằng uid:gid của host để file container ghi ra không thuộc root
        # (tránh host không xoá được tmp dir). Chỉ áp dụng trên nền POSIX.
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
            command = self._docker_run_command(workspace, container_name, script_path)
            try:
                completed = subprocess.run(  # noqa: S603 - lệnh dựng từ tham số nội bộ
                    command,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout + DOCKER_TEARDOWN_BUFFER_SECONDS,
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
    """True nếu sandbox được bật (SANDBOX_ENABLED) và Docker khả dụng trên host."""
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
    return DockerSandboxExecutor(
        image=image,
        timeout_seconds=int(timeout),
        memory=memory,
        cpus=cpus,
    )
