import json
from collections.abc import Mapping


def parse_positive_int(
    config: Mapping[str, object],
    key: str,
    default: int | None = None,
) -> int | None:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def parse_node_host_map(raw_value: str) -> dict[str, str]:
    raw = raw_value.strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return {
            str(node_name).strip(): str(host).strip()
            for node_name, host in parsed.items()
            if str(node_name).strip() and str(host).strip()
        }

    result: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        node_name, host = item.split("=", 1)
        node_name, host = node_name.strip(), host.strip()
        if node_name and host:
            result[node_name] = host
    return result
