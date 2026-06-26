# backend/app/streaming/sse.py
from __future__ import annotations

import json
from typing import Any


def format_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """
    Chuyên biệt hóa khâu đóng gói dữ liệu thô thành chuỗi truyền phát SSE sạch sẽ.
    """
    json_data = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {json_data}\n\n"
