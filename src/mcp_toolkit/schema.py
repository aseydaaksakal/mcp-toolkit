"""Derive JSON Schema from a function signature.

Agents pick tools by reading their schemas, so the schema is part of the
prompt whether you think of it that way or not. Writing it by hand means it
drifts from the code; generating it from type hints and the docstring means
it cannot.
"""

from __future__ import annotations

import enum
import inspect
import re
import types
import typing
from collections.abc import Callable
from dataclasses import MISSING, fields, is_dataclass
from typing import Any

_PRIMITIVES: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    type(None): {"type": "null"},
    Any: {},
}

_ARGS_HEADER = re.compile(r"^\s*(Args|Arguments|Parameters)\s*:\s*$", re.IGNORECASE)
_GOOGLE_PARAM = re.compile(r"^\s*(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)$")
_SPHINX_PARAM = re.compile(r"^\s*:param\s+(?:[\w\[\], ]+\s+)?(\w+)\s*:\s*(.+)$")


def summarize_docstring(func: Callable[..., Any]) -> str:
    """Return everything above the ``Args:`` block, collapsed to one blob."""
    doc = inspect.getdoc(func) or ""
    lines: list[str] = []
    for line in doc.splitlines():
        if _ARGS_HEADER.match(line) or _SPHINX_PARAM.match(line):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def parse_param_docs(func: Callable[..., Any]) -> dict[str, str]:
    """Pull per-parameter descriptions out of a Google- or Sphinx-style docstring."""
    doc = inspect.getdoc(func) or ""
    found: dict[str, str] = {}
    in_args = False
    current: str | None = None

    for raw in doc.splitlines():
        sphinx = _SPHINX_PARAM.match(raw)
        if sphinx:
            found[sphinx.group(1)] = sphinx.group(2).strip()
            current = sphinx.group(1)
            continue

        if _ARGS_HEADER.match(raw):
            in_args = True
            current = None
            continue

        if not in_args:
            continue

        if raw.strip() and not raw.startswith((" ", "\t")):
            # Dedented back to a new section such as ``Returns:``.
            break

        match = _GOOGLE_PARAM.match(raw)
        if match:
            current = match.group(1).lstrip("*")
            found[current] = match.group(2).strip()
        elif current and raw.strip():
            found[current] += " " + raw.strip()

    return found


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    """Reduce ``T | None`` to ``(T, True)``; leave everything else alone."""
    origin = typing.get_origin(annotation)
    if origin not in (typing.Union, types.UnionType):
        return annotation, False
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if len(args) == 1:
        return args[0], True
    reduced = args[0]
    for extra in args[1:]:
        reduced = reduced | extra
    return reduced, True


def type_to_schema(annotation: Any) -> dict[str, Any]:
    """Translate a type annotation into a JSON Schema fragment."""
    if annotation is inspect.Parameter.empty:
        return {}

    annotation, _ = _unwrap_optional(annotation)

    if annotation in _PRIMITIVES:
        return dict(_PRIMITIVES[annotation])

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin is typing.Literal:
        schema: dict[str, Any] = {"enum": list(args)}
        kinds = {type(a) for a in args}
        if kinds == {str}:
            schema["type"] = "string"
        elif kinds <= {int, bool}:
            schema["type"] = "integer"
        return schema

    if origin in (list, set, frozenset, tuple):
        item = type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item}

    if origin is dict:
        value = type_to_schema(args[1]) if len(args) == 2 else {}
        return {"type": "object", "additionalProperties": value or True}

    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [type_to_schema(a) for a in args]}

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return {"enum": [member.value for member in annotation]}

    if is_dataclass(annotation):
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in fields(annotation):
            props[f.name] = type_to_schema(f.type)
            if f.default is MISSING and f.default_factory is MISSING:  # type: ignore[misc]
                required.append(f.name)
        out: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            out["required"] = required
        return out

    # Unknown types stay permissive rather than silently rejecting valid input.
    return {}


def schema_from_signature(func: Callable[..., Any]) -> dict[str, Any]:
    """Build the ``inputSchema`` object MCP expects for a tool.

    ``self``, ``cls`` and ``*args``/``**kwargs`` are skipped. Parameters with
    defaults become optional; everything else lands in ``required``.
    """
    try:
        hints = typing.get_type_hints(func)
    except Exception:  # pragma: no cover - exotic forward refs
        hints = {}

    signature = inspect.signature(func)
    docs = parse_param_docs(func)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue

        annotation = hints.get(name, param.annotation)
        prop = type_to_schema(annotation)
        if name in docs:
            prop["description"] = docs[name]
        if param.default is not inspect.Parameter.empty:
            # ``default: null`` tells the model nothing it cannot infer from the
            # parameter being optional, so leave it out.
            if param.default is not None and _is_json_scalar(param.default):
                prop["default"] = param.default
        else:
            required.append(name)
        properties[name] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _is_json_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None)))


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Check *arguments* against *schema*, returning human-readable problems.

    Deliberately small: presence, primitive type, enum membership and unknown
    keys. That catches the mistakes models actually make. Reach for
    ``jsonschema`` if you need the full specification.
    """
    problems: list[str] = []
    properties: dict[str, Any] = schema.get("properties", {})

    for name in schema.get("required", []):
        if name not in arguments:
            problems.append(f"missing required argument {name!r}")

    if schema.get("additionalProperties") is False:
        for name in arguments:
            if name not in properties:
                problems.append(f"unexpected argument {name!r}")

    for name, value in arguments.items():
        prop = properties.get(name)
        if not prop:
            continue
        problems.extend(_check_value(name, value, prop))

    return problems


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
    "null": (type(None),),
}


def _check_value(name: str, value: Any, prop: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expected = prop.get("type")

    if expected in _JSON_TYPES:
        allowed = _JSON_TYPES[expected]
        # ``bool`` is a subclass of ``int``; do not let True pass as an integer.
        if expected in ("integer", "number") and isinstance(value, bool):
            problems.append(f"{name!r} must be {expected}, got boolean")
        elif not isinstance(value, allowed):
            problems.append(
                f"{name!r} must be {expected}, got {type(value).__name__}"
            )

    if "enum" in prop and value not in prop["enum"]:
        allowed_values = ", ".join(repr(v) for v in prop["enum"])
        problems.append(f"{name!r} must be one of: {allowed_values}")

    if expected == "array" and isinstance(value, (list, tuple)):
        item_schema = prop.get("items") or {}
        if item_schema:
            for index, item in enumerate(value):
                problems.extend(_check_value(f"{name}[{index}]", item, item_schema))

    return problems
