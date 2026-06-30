from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.common.exceptions import SkillRuntimeError
from app.llm.schemas import LLMToolDefinition


def _schema_type_name(schema_type: object) -> str:
    if isinstance(schema_type, list):
        return " or ".join(str(item) for item in schema_type)
    return str(schema_type)


def _value_matches_type(value: Any, schema_type: object) -> bool:
    if isinstance(schema_type, list):
        return any(_value_matches_type(value, item) for item in schema_type)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int | float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "null":
        return value is None
    return True


def _schema_allows_type(schema_type: object, expected_type: str) -> bool:
    if schema_type is None:
        return True
    if isinstance(schema_type, list):
        return expected_type in schema_type
    return schema_type == expected_type


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    schema_type = schema.get("type")
    if schema_type is not None and not _value_matches_type(value, schema_type):
        raise SkillRuntimeError(
            f"Invalid tool arguments: '{path}' must be {_schema_type_name(schema_type)}.",
            details={
                "path": path,
                "expected_type": schema_type,
                "actual_type": type(value).__name__,
            },
        )

    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isinstance(schema.get("minimum"), int | float)
        and value < schema["minimum"]
    ):
        raise SkillRuntimeError(
            f"Invalid tool arguments: '{path}' must be >= {schema['minimum']}.",
            details={"path": path, "minimum": schema["minimum"], "actual": value},
        )

    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and isinstance(schema.get("maximum"), int | float)
        and value > schema["maximum"]
    ):
        raise SkillRuntimeError(
            f"Invalid tool arguments: '{path}' must be <= {schema['maximum']}.",
            details={"path": path, "maximum": schema["maximum"], "actual": value},
        )

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(item) for item in schema["enum"])
        raise SkillRuntimeError(
            f"Invalid tool arguments: '{path}' must be one of: {allowed}.",
            details={"path": path, "allowed": schema["enum"], "actual": value},
        )

    if _schema_allows_type(schema_type, "object") and isinstance(value, dict):
        _validate_object(value, schema, path)

    if (
        _schema_allows_type(schema_type, "array")
        and isinstance(value, list)
        and isinstance(schema.get("items"), dict)
    ):
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]")


def _validate_object(arguments: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []

    for key in required:
        if key not in arguments:
            raise SkillRuntimeError(
                f"Invalid tool arguments: Missing required argument '{key}'.",
                details={"path": path, "missing": key},
            )

    if schema.get("additionalProperties") is False:
        for key in arguments:
            if key not in properties:
                raise SkillRuntimeError(
                    f"Invalid tool arguments: Unexpected argument '{key}'.",
                    details={"path": path, "unexpected": key},
                )

    for key, value in arguments.items():
        property_schema = properties.get(key)
        if isinstance(property_schema, dict):
            _validate_value(value, property_schema, f"{path}.{key}" if path else key)


def validate_tool_call_arguments(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    tools: Sequence[LLMToolDefinition],
) -> None:
    tool_by_name = {tool.name: tool for tool in tools}
    tool = tool_by_name.get(tool_name)
    if tool is None:
        raise SkillRuntimeError(
            f"Tool '{tool_name}' is not available.",
            details={"tool_name": tool_name},
        )

    schema = tool.input_schema
    if not isinstance(schema, dict):
        return
    _validate_value(arguments, schema, tool_name)


def validate_json_value_against_schema(
    *,
    value: Any,
    schema: dict[str, Any],
    path: str = "value",
) -> None:
    _validate_value(value, schema, path)
