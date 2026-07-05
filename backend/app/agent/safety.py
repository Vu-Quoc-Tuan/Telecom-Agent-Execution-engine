from __future__ import annotations

import posixpath
import re
import shlex
from functools import lru_cache

from app.common.enums import ExecutionMode
from app.common.exceptions import SafetyViolationError
from app.common.security_patterns import PRIVATE_KEY_PATTERN, SECRET_MASK_PATTERN


class AgentSafetyGuard:
    # Cấm tuyệt đối các hành vi xóa phân vùng, tắt nguồn, tải file lậu hoặc thay đổi quyền sâu
    CRITICAL_BLOCKLIST_PATTERNS = [
        r"\brm\s+-(?:rf|fr|f|r)\b",  # rm -rf, rm -f bừa bãi
        r"\bshutdown\b",
        r"\breboot\b",  # Tắt/Khởi động lại máy chủ vật lý trạm
        r"\bpoweroff\b",
        r"\binit\s+0\b",
        r"\bmkfs\b",
        r"\bformat\b",  # Định dạng lại ổ cứng hạ tầng
        r"> /dev/sda",
        r"> /dev/xvda",  # Ghi đè trực tiếp vào phân vùng đĩa cứng
        r"\bchmod\s+777\b",  # Cấm phân quyền bừa bãi gây hổng bảo mật
        r"\bwget\b",
        r"\bcurl\b",  # Chặn tải script lậu từ bên ngoài vào trạm mạng
    ]
    SENSITIVE_PATH_PATTERNS = [
        r"(?:^|\s)/(?:etc/(?:shadow|sudoers)|proc/self/environ)(?:\s|$)",
        r"(?:^|\s)~/(?:\.ssh)(?:/|\s|$)",
        r"(?:^|\s)(?:/root|/home/[^/\s]+/\.ssh)(?:/|\s|$)",
        r"(?:^|\s)(?:id_rsa|id_ed25519|authorized_keys|known_hosts)(?:\s|$)",
        r"(?:^|\s)(?:\.env|[^\s]*/\.env)(?:\s|$)",
    ]

    COMMAND_CHAIN_PATTERN = re.compile(r"(?:;|&&|\|\||`|\$\(|\r|\n)")
    SAFE_OUTPUT_LIMIT_PIPE_PATTERN = re.compile(
        r"^(?P<base>[^|;&`$\r\n]+?)\s*\|\s*(?:head|tail)(?:\s+-n)?\s+\d+\s*$",
        re.IGNORECASE,
    )
    READ_ONLY_SSH_COMMANDS = {
        "cat",
        "df",
        "display",
        "free",
        "get",
        "grep",
        "head",
        "hostname",
        "journalctl",
        "list",
        "ls",
        "ping",
        "ps",
        "show",
        "stat",
        "status",
        "tail",
        "uname",
        "uptime",
        "whoami",
    }
    SQL_MUTATION_STATEMENT_KEYWORDS = {
        "alter",
        "attach",
        "create",
        "delete",
        "detach",
        "drop",
        "grant",
        "insert",
        "kill",
        "optimize",
        "rename",
        "replace",
        "revoke",
        "truncate",
        "update",
    }
    SQL_PROHIBITED_READ_PATTERNS = (
        re.compile(r"\binto\s+(?:out|dump)?file\b", re.IGNORECASE),
        re.compile(
            r"^\s*explain(?:\s+\([^)]*\))?\s+"
            r"(?:alter|attach|create|delete|detach|drop|grant|insert|kill|optimize|"
            r"rename|replace|revoke|truncate|update)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bas\s*\(\s*"
            r"(?:alter|attach|create|delete|detach|drop|grant|insert|kill|optimize|"
            r"rename|replace|revoke|truncate|update)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\)\s*(?:alter|attach|create|delete|detach|drop|grant|insert|kill|"
            r"optimize|rename|replace|revoke|truncate|update)\b",
            re.IGNORECASE,
        ),
    )
    PII_AND_SECRET_PATTERNS = {
        "SECRET": SECRET_MASK_PATTERN,
        "PRIVATE_KEY": PRIVATE_KEY_PATTERN,
    }

    @classmethod
    def is_critical_ssh_command(cls, command: str) -> bool:
        clean_cmd = cls.normalize_ssh_command(command).strip()
        return any(
            re.search(pattern, clean_cmd, re.IGNORECASE)
            for pattern in cls.CRITICAL_BLOCKLIST_PATTERNS
        )

    @classmethod
    def validate_ssh_command(cls, command: str) -> tuple[bool, str | None]:
        """
        Kiểm tra chuyên sâu câu lệnh SSH trước khi bắn ra cổng kết nối vật lý.
        Trả về: (is_safe, error_message)
        """
        clean_cmd = cls.normalize_ssh_command(command).strip()

        # 1. Chống rỗng lệnh
        if not clean_cmd:
            return False, "Câu lệnh trống rỗng, từ chối xử lý."

        if cls.COMMAND_CHAIN_PATTERN.search(clean_cmd):
            return False, "Phát hiện chuỗi lệnh hoặc command substitution không được phép."

        if cls._contains_sensitive_path(clean_cmd):
            return False, "CẢNH BÁO AN NINH: Lệnh SSH cố đọc đường dẫn nhạy cảm."

        if cls.is_critical_ssh_command(clean_cmd):
            return False, "CẢNH BÁO AN NINH: Lệnh SSH nằm trong danh mục cấm thực thi."

        return True, None

    @classmethod
    def verify_ssh_command(
        cls,
        command: str,
        *,
        approval_confirmations: int = 0,
    ) -> tuple[bool, str | None]:
        is_valid, error_message = cls.validate_ssh_command(command)
        if not is_valid:
            return False, error_message

        mode = cls.classify_ssh_command(command)
        if mode == ExecutionMode.REQUIRE_APPROVAL and approval_confirmations < 1:
            return False, "Lệnh SSH cần được người vận hành xác nhận một lần trước khi chạy."
        return True, None

    @classmethod
    def normalize_ssh_command(cls, command: str) -> str:
        """Remove benign LLM-added output limit pipes; backend truncates output itself."""
        clean_cmd = command.strip()
        match = cls.SAFE_OUTPUT_LIMIT_PIPE_PATTERN.fullmatch(clean_cmd)
        if not match:
            return clean_cmd
        return match.group("base").strip()

    @classmethod
    def _contains_sensitive_path(cls, command: str) -> bool:
        candidates = [command, " ".join(cls._normalized_path_tokens(command))]
        return any(
            re.search(pattern, candidate, re.IGNORECASE)
            for candidate in candidates
            for pattern in cls.SENSITIVE_PATH_PATTERNS
        )

    @staticmethod
    def _normalized_path_tokens(command: str) -> list[str]:
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        normalized: list[str] = []
        for token in tokens:
            if token.startswith("~/"):
                normalized.append("~/" + posixpath.normpath(token[2:]))
            elif token.startswith("/") or "/" in token or token.startswith("."):
                normalized.append(posixpath.normpath(token))
            else:
                normalized.append(token)
        return normalized

    @classmethod
    def classify_ssh_command(cls, command: str) -> ExecutionMode:
        is_valid, error_message = cls.validate_ssh_command(command)
        if not is_valid:
            raise SafetyViolationError(error_message or "Lệnh SSH không hợp lệ.")

        first_token = re.match(r"[a-z][a-z0-9_-]*", command.strip().lower())
        if first_token and first_token.group(0) in cls.READ_ONLY_SSH_COMMANDS:
            return ExecutionMode.AUTO_EXECUTE
        return ExecutionMode.REQUIRE_APPROVAL

    @staticmethod
    @lru_cache(maxsize=128)
    def verify_read_only_sql(sql: str) -> tuple[bool, str | None]:
        clean_sql = sql.strip()
        if not clean_sql:
            return False, "SQL query is empty."

        without_comments = re.sub(r"/\*[\s\S]*?\*/|--[^\r\n]*", " ", clean_sql)
        without_literals = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", without_comments)
        statements = [part.strip() for part in without_literals.split(";") if part.strip()]
        if len(statements) != 1:
            return False, "Only one SQL statement is allowed."

        # Extract lowercase word tokens — note that underscores break words,
        tokens = re.findall(r"\b[a-z][a-z0-9]*\b", statements[0].lower())
        if not tokens or tokens[0] not in {"select", "with", "describe", "desc", "show", "explain"}:
            return False, "Only SELECT, WITH, DESCRIBE, DESC, SHOW, or EXPLAIN queries are allowed."
        normalized_stmt = statements[0].lower()
        if tokens[0] in AgentSafetyGuard.SQL_MUTATION_STATEMENT_KEYWORDS:
            return False, f"SQL contains prohibited statement: {tokens[0]}."
        for pattern in AgentSafetyGuard.SQL_PROHIBITED_READ_PATTERNS:
            match = pattern.search(normalized_stmt)
            if match:
                return False, f"SQL contains prohibited clause: {match.group(0).strip()}."
        if tokens[0] == "with" and "select" not in tokens:
            return False, "WITH queries must end in a SELECT."
        return True, None

    @staticmethod
    def truncate_output(output: str, max_characters: int = 15000) -> tuple[str, bool]:
        """
        Rào chắn Output Limit: Chống tràn ngữ cảnh (Context Window) của LLM
        nếu log SSH hoặc query ClickHouse trả về cục văn bản dài hàng triệu dòng.
        """
        if len(output) > max_characters:
            truncated_content = (
                output[:max_characters]
                + "\n\n... [HỆ THỐNG CẮT GIẢM: Nội dung log quá dài đã bị cắt bớt để bảo vệ an toàn bộ nhớ Agent] ..."
            )
            return truncated_content, True
        return output, False

    @classmethod
    def sanitize_input_prompt(cls, user_prompt: str) -> str:
        """
        [Mondoo-Inspired] Input DLP Layer: Quét và che giấu toàn bộ thông tin nhạy cảm
        của khách hàng hoặc hệ thống trước khi gửi sang bên thứ 3 (OpenAI/Claude).
        """
        if not user_prompt:
            return user_prompt

        sanitized = user_prompt
        # Quét và đè mặt nạ bảo mật
        for data_type, pattern in cls.PII_AND_SECRET_PATTERNS.items():
            sanitized = pattern.sub(f"[[MASKED_{data_type}]]", sanitized)

        return sanitized
