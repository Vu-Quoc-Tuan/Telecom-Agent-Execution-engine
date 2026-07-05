import re

# Centralized sensitive keys used in dictionary/object value checks
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "api_key",
    "secret",
    "token",
    "private_key",
    "auth",
}

# Unified pattern of secret keywords for regex checks
SECRET_KEY_PATTERN = r"password|passwd|pwd|secret|api[_-]?key|token|private_key"

# Shared regex patterns
SECRET_MASK_PATTERN = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)

PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.IGNORECASE,
)
