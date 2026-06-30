from __future__ import annotations

import unittest

from app.agent.tool_validation import (
    validate_json_value_against_schema,
    validate_tool_call_arguments,
)
from app.common.exceptions import SkillRuntimeError
from app.llm.schemas import LLMToolDefinition


class ToolValidationTests(unittest.TestCase):
    def test_unknown_tool_raises_error(self) -> None:
        tools = [
            LLMToolDefinition(
                name="test_tool",
                description="A test tool",
                input_schema={"type": "object", "properties": {}},
            )
        ]
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_tool_call_arguments(
                tool_name="unknown_tool",
                arguments={},
                tools=tools,
            )
        self.assertIn("not available", ctx.exception.message)

    def test_schema_none_passes(self) -> None:
        tools = [
            LLMToolDefinition(
                name="test_tool",
                description="A test tool",
                input_schema={},
            )
        ]
        # Should pass without error
        validate_tool_call_arguments(
            tool_name="test_tool",
            arguments={"any": "thing"},
            tools=tools,
        )

    def test_validate_string_type(self) -> None:
        schema = {"type": "string"}
        # Pass
        validate_json_value_against_schema(value="hello", schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=123, schema=schema)
        self.assertIn("must be string", ctx.exception.message)

    def test_validate_integer_type(self) -> None:
        schema = {"type": "integer"}
        # Pass
        validate_json_value_against_schema(value=123, schema=schema)
        # Fail (float)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=123.45, schema=schema)
        # Fail (bool is not integer)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=True, schema=schema)
        self.assertIn("must be integer", ctx.exception.message)

    def test_validate_number_type(self) -> None:
        schema = {"type": "number"}
        # Pass
        validate_json_value_against_schema(value=123, schema=schema)
        validate_json_value_against_schema(value=123.45, schema=schema)
        # Fail (bool)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=True, schema=schema)
        self.assertIn("must be number", ctx.exception.message)

    def test_validate_boolean_type(self) -> None:
        schema = {"type": "boolean"}
        # Pass
        validate_json_value_against_schema(value=True, schema=schema)
        validate_json_value_against_schema(value=False, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value="true", schema=schema)
        self.assertIn("must be boolean", ctx.exception.message)

    def test_validate_object_type(self) -> None:
        schema = {"type": "object"}
        # Pass
        validate_json_value_against_schema(value={"a": 1}, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=[1, 2], schema=schema)
        self.assertIn("must be object", ctx.exception.message)

    def test_validate_array_type(self) -> None:
        schema = {"type": "array"}
        # Pass
        validate_json_value_against_schema(value=[1, 2], schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value={"a": 1}, schema=schema)
        self.assertIn("must be array", ctx.exception.message)

    def test_validate_null_type(self) -> None:
        schema = {"type": "null"}
        # Pass
        validate_json_value_against_schema(value=None, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value="null", schema=schema)
        self.assertIn("must be null", ctx.exception.message)

    def test_validate_union_types(self) -> None:
        schema = {"type": ["string", "null"]}
        # Pass
        validate_json_value_against_schema(value="hello", schema=schema)
        validate_json_value_against_schema(value=None, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=123, schema=schema)
        self.assertIn("must be string or null", ctx.exception.message)

    def test_validate_minimum_range(self) -> None:
        schema = {"type": "number", "minimum": 10.5}
        # Pass
        validate_json_value_against_schema(value=10.5, schema=schema)
        validate_json_value_against_schema(value=11, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=10, schema=schema)
        self.assertIn("must be >= 10.5", ctx.exception.message)

    def test_validate_maximum_range(self) -> None:
        schema = {"type": "integer", "maximum": 100}
        # Pass
        validate_json_value_against_schema(value=100, schema=schema)
        validate_json_value_against_schema(value=99, schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=101, schema=schema)
        self.assertIn("must be <= 100", ctx.exception.message)

    def test_validate_enum(self) -> None:
        schema = {"type": "string", "enum": ["apple", "banana"]}
        # Pass
        validate_json_value_against_schema(value="apple", schema=schema)
        # Fail
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value="cherry", schema=schema)
        self.assertIn("must be one of: apple, banana", ctx.exception.message)

    def test_validate_object_properties(self) -> None:
        schema = {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        # Pass
        validate_json_value_against_schema(value={"name": "Alice", "age": 30}, schema=schema)
        validate_json_value_against_schema(value={"name": "Bob"}, schema=schema)
        # Fail (missing required)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value={"age": 30}, schema=schema)
        self.assertIn("Missing required argument 'name'", ctx.exception.message)
        # Fail (additional properties false)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(
                value={"name": "Alice", "extra": True}, schema=schema
            )
        self.assertIn("Unexpected argument 'extra'", ctx.exception.message)

    def test_validate_nested_array_items(self) -> None:
        schema = {"type": "array", "items": {"type": "integer", "minimum": 1}}
        # Pass
        validate_json_value_against_schema(value=[1, 2, 3], schema=schema)
        # Fail (nested item fails validation)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=[1, 0, 3], schema=schema)
        self.assertIn("must be >= 1", ctx.exception.message)

    def test_union_object_still_validates_properties(self) -> None:
        schema = {
            "type": ["object", "null"],
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
            },
        }

        validate_json_value_against_schema(value={"name": "Alice"}, schema=schema)
        validate_json_value_against_schema(value=None, schema=schema)
        with self.assertRaises(SkillRuntimeError) as missing_ctx:
            validate_json_value_against_schema(value={}, schema=schema)
        self.assertIn("Missing required argument 'name'", missing_ctx.exception.message)
        with self.assertRaises(SkillRuntimeError) as extra_ctx:
            validate_json_value_against_schema(
                value={"name": "Alice", "extra": True}, schema=schema
            )
        self.assertIn("Unexpected argument 'extra'", extra_ctx.exception.message)

    def test_union_array_still_validates_items(self) -> None:
        schema = {
            "type": ["array", "null"],
            "items": {"type": "integer", "minimum": 1},
        }

        validate_json_value_against_schema(value=[1, 2, 3], schema=schema)
        validate_json_value_against_schema(value=None, schema=schema)
        with self.assertRaises(SkillRuntimeError) as ctx:
            validate_json_value_against_schema(value=[1, 0, 3], schema=schema)
        self.assertIn("must be >= 1", ctx.exception.message)
