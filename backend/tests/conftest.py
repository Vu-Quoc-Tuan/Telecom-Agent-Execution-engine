# backend/tests/conftest.py
from __future__ import annotations

import os

# Ngăn chặn pytest kết nối và gửi trace giả tới Langfuse của người dùng trong quá trình chạy test
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["ENVIRONMENT"] = "testing"
