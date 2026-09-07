from enum import Enum
from typing import Literal, Optional  # noqa: UP035

import pytest

from mcp_toolkit.schema import (
    parse_param_docs,
    schema_from_signature,
    summarize_docstring,
    type_to_schema,
    validate_arguments,
)


class Priority(str, Enum):
    LOW = "low"
    HIGH = "high"


def sample(
    order_id: str,
    limit: int = 10,
    include_lines: bool = False,
    status: Literal["open", "closed"] = "open",
    tags: list[str] | None = None,
) -> dict:
    """Look up an order.

    Args:
        order_id: Internal order identifier.
        limit: Maximum number of line items to return.
        tags: Optional tags to filter by.

    Returns:
        The order record.
    """
    return {}


def test_summary_stops_before_args_block():
    assert summarize_docstring(sample) == "Look up an order."


def test_param_docs_are_extracted():
    docs = parse_param_docs(sample)
    assert docs["order_id"] == "Internal order identifier."
    assert docs["limit"] == "Maximum number of line items to return."
    assert "include_lines" not in docs


def test_sphinx_style_param_docs():
    def f(name: str):
        """Do a thing.

        :param name: The name to use.
        """

    assert parse_param_docs(f)["name"] == "The name to use."


def test_schema_marks_only_defaultless_params_required():
    schema = schema_from_signature(sample)
    assert schema["required"] == ["order_id"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["limit"]["default"] == 10


def test_schema_types():
    props = schema_from_signature(sample)["properties"]
    assert props["order_id"]["type"] == "string"
    assert props["limit"]["type"] == "integer"
    assert props["include_lines"]["type"] == "boolean"
    assert props["status"]["enum"] == ["open", "closed"]
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Optional tags to filter by.",
    }


def test_optional_is_unwrapped():
    assert type_to_schema(Optional[int]) == {"type": "integer"}  # noqa: UP045
    assert type_to_schema(int | None) == {"type": "integer"}


def test_enum_becomes_enum_values():
    assert type_to_schema(Priority) == {"enum": ["low", "high"]}


def test_unknown_type_stays_permissive():
    class Weird: ...

    assert type_to_schema(Weird) == {}


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ({"order_id": "A"}, []),
        ({}, ["missing required argument 'order_id'"]),
        ({"order_id": "A", "nope": 1}, ["unexpected argument 'nope'"]),
        ({"order_id": 5}, ["'order_id' must be string, got int"]),
        ({"order_id": "A", "limit": True}, ["'limit' must be integer, got boolean"]),
    ],
)
def test_validation_messages(arguments, expected):
    assert validate_arguments(schema_from_signature(sample), arguments) == expected


def test_validation_checks_enum_and_array_items():
    schema = schema_from_signature(sample)
    problems = validate_arguments(
        schema, {"order_id": "A", "status": "archived", "tags": ["ok", 3]}
    )
    assert "'status' must be one of: 'open', 'closed'" in problems
    assert "'tags[1]' must be string, got int" in problems
