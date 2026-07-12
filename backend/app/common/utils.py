from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath


def extract_json_object(content: str) -> str:
    """Extract the first JSON object from a string, supporting markdown fences and extra text."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def normalize_safe_relative_posix_path(raw: object) -> str:
    """Return a trimmed relative POSIX path or reject unsafe path components."""
    text = str(raw).strip()
    path = PurePosixPath(text)
    if (
        not text
        or path.is_absolute()
        or any(part in {".", ".."} for part in text.split("/"))
        or "\\" in text
    ):
        raise ValueError("unsafe relative POSIX path")
    return path.as_posix()


def sha256_text(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
