from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_discovery_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "Agent_skill/db-schema-discovery/scripts/discover.py"
    )
    spec = importlib.util.spec_from_file_location("db_schema_discovery", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_splits_schema_qualified_postgres_table_name() -> None:
    module = _load_discovery_module()

    assert module.split_postgres_table_reference("alarm_data.error") == (
        "alarm_data",
        "error",
    )


def test_unqualified_postgres_table_name_keeps_schema_unspecified() -> None:
    module = _load_discovery_module()

    assert module.split_postgres_table_reference("error") == (None, "error")
