from __future__ import annotations

import asyncio

import paramiko

from app.agent.safety import AgentSafetyGuard
from app.common.exceptions import ConnectorExecutionError, SafetyViolationError
from app.connectors.base import BaseConnector


class TelcoSSHConnector(BaseConnector):
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        timeout: int = 30,
        known_hosts_path: str | None = None,
        auto_add_host_keys: bool = False,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.known_hosts_path = known_hosts_path
        self.auto_add_host_keys = auto_add_host_keys
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        """Mở kết nối vật lý thực tế bằng Paramiko Client"""
        if self._client is not None:
            return

        self._client = paramiko.SSHClient()
        if self.known_hosts_path:
            self._client.load_host_keys(self.known_hosts_path)
        else:
            self._client.load_system_host_keys()
        policy = paramiko.AutoAddPolicy() if self.auto_add_host_keys else paramiko.RejectPolicy()
        self._client.set_missing_host_key_policy(policy)
        self._client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.timeout,
        )

    def _sync_execute(self, command: str) -> tuple[str, str]:
        """Xử lý đồng bộ lệnh gõ và đọc buffer dữ liệu đầu ra"""
        self.connect()
        # Thực thi lệnh trên thiết bị trạm vật lý
        stdin, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)

        # Đọc trọn gói nội dung text trả về từ thiết bị
        stdout_str = stdout.read().decode("utf-8", errors="ignore")
        stderr_str = stderr.read().decode("utf-8", errors="ignore")
        return stdout_str, stderr_str

    async def execute_command(
        self,
        command: str,
        *,
        approval_confirmations: int = 0,
    ) -> tuple[str, str]:
        """
        Hàm non-blocking chính thức dành cho Kỹ sư gọi trong kịch bản Skill động.
        Ví dụ: stdout, stderr = await ssh_client.execute_command("pm2 status")
        """
        command = AgentSafetyGuard.normalize_ssh_command(command)
        # 1. Kiểm tra lệnh cấm (rm -rf, shutdown,...) trước khi gửi đi
        is_safe, error_msg = AgentSafetyGuard.verify_ssh_command(
            command,
            approval_confirmations=approval_confirmations,
        )
        if not is_safe:
            raise SafetyViolationError(
                error_msg or "Blocked by Safety Guard.",
                details={"command": command},
            )

        try:
            # 2. Đẩy tác vụ I/O mạng đồng bộ của Paramiko sang Worker Thread để không làm treo API FastAPI
            stdout_str, stderr_str = await asyncio.to_thread(self._sync_execute, command)
        except Exception as e:
            error_text = str(e)
            guidance = ""
            if "known_hosts" in error_text.lower() or "not found in" in error_text.lower():
                guidance = (
                    " Configure SSH_KNOWN_HOSTS with the trusted server fingerprint. "
                    "Use SSH_AUTO_ADD_HOST_KEYS=true only for an explicitly trusted dev environment."
                )
            raise ConnectorExecutionError(
                f"Lỗi kết nối SSH vật lý tới trạm: {error_text}.{guidance}",
                details={
                    "host": self.host,
                    "port": self.port,
                    "known_hosts_path": self.known_hosts_path,
                },
            ) from e

        # 3. Rào chắn Output Limit: Cắt giảm dữ liệu nếu log trạm trả về dài quá ngưỡng cho phép
        stdout_str, _ = AgentSafetyGuard.truncate_output(stdout_str)
        stderr_str, _ = AgentSafetyGuard.truncate_output(stderr_str)
        return stdout_str, stderr_str

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
