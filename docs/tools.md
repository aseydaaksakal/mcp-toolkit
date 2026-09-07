# Tools

## Registration

The decorator takes the function apart and fills in what you did not specify:

```python
@server.tool()
def lookup_order(order_id: str, include_lines: bool = False) -> dict:
    """Look up an order in the fulfilment database.

    Args:
        order_id: Internal order identifier, e.g. "ORD-8812".
        include_lines: Include line items in the response.
    """
```

Produces:

| Field | Value | Source |
|---|---|---|
| `name` | `lookup_order` | function name |
| `description` | `Look up an order in the fulfilment database.` | docstring, above `Args:` |
| `inputSchema.properties.order_id` | `{"type": "string", "description": "Internal order identifier, e.g. \"ORD-8812\"."}` | annotation + `Args:` entry |
| `inputSchema.required` | `["order_id"]` | parameters without defaults |

Override any of it:

```python
@server.tool(
    name="orders.lookup",
    description="Fetch one order. Prefer orders.search when you only have an email.",
    scopes=["orders:read"],
)
def lookup_order(order_id: str) -> dict: ...
```

Register imperatively when the function is built at runtime:

```python
server.add_tool("orders.lookup", handler, description="...", schema={...})
```

## Supported annotations

| Annotation | Schema |
|---|---|
| `str`, `int`, `float`, `bool` | `{"type": "string"}` and friends |
| `list[str]` | `{"type": "array", "items": {"type": "string"}}` |
| `dict[str, int]` | `{"type": "object", "additionalProperties": {"type": "integer"}}` |
| `Literal["a", "b"]` | `{"type": "string", "enum": ["a", "b"]}` |
| `SomeEnum` | `{"enum": [<member values>]}` |
| `T \| None` | schema for `T`, and `T` is not required |
| a dataclass | nested object with its own `required` list |
| anything else | `{}` — permissive rather than wrongly restrictive |

`Literal` is worth reaching for. `status: Literal["open", "closed"]` puts the
valid values in the model's context and rejects everything else before your
code runs; `status: str` does neither.

Docstrings are parsed in Google style (`Args:` block) and Sphinx style
(`:param x:`). Both produce per-parameter descriptions, which is the part of
the schema models actually read.

## Validation

Arguments are checked before the handler runs. The check covers required keys,
primitive types, enum membership, array item types and unknown keys, and
reports every problem at once:

```
missing required argument 'order_id'; 'limit' must be integer, got boolean
```

That is a JSON-RPC `-32602`, so the client can distinguish "you called it
wrong" from "the tool failed".

Two details worth knowing. `True` does not pass as an `integer`, even though
`bool` subclasses `int` in Python. And `additionalProperties` is `false` by
default, so a hallucinated argument name is rejected instead of being silently
dropped.

If you need full JSON Schema — `oneOf`, `pattern`, numeric bounds — pass an
explicit `schema=` and validate inside the handler with `jsonschema`.

## Return values

| You return | The agent receives |
|---|---|
| `str` | that string as a text block |
| `dict`, `list`, dataclass, anything JSON-serialisable | `json.dumps(..., indent=2)` as a text block |
| `{"type": "image", ...}` or a list of such blocks | passed through unchanged |
| `None` | an empty text block |

JSON rather than `repr()` because a stable serialisation is easier for a model
to parse than Python's formatting rules.

Every result passes through the server's `Redactor` on the way out.

## Errors

Raise `ToolError` for failures the model should see and act on:

```python
raise ToolError(f"no order named {order_id!r}; call list_orders for valid ids")
```

The message reaches the model verbatim, so write it as an instruction rather
than a status code. "Not found" tells the model nothing; the version above
tells it what to do next.

Anything else that escapes the handler is caught, written to the audit log in
full, and reported to the model as a type name only:

```
Tool 'lookup_order' failed: OperationalError
```

Backend exception text tends to contain connection strings, table names and
occasionally credentials. Keeping it out of the context window means it cannot
be repeated back later.

## Resources and prompts

Resources are read by URI rather than called with arguments — configuration,
schemas, anything the agent should be able to consult:

```python
@server.resource("config://limits", mime_type="application/json")
def limits() -> dict:
    """Operational limits for this server."""
    return {"rate_per_second": 5}
```

Prompts are named templates the client can offer to the user:

```python
@server.prompt(arguments=[{"name": "order_id", "required": True}])
def explain_delay(order_id: str) -> str:
    """Draft a customer-facing explanation for a delayed order."""
    return f"Look up {order_id} and write two sentences for the customer."
```

Both are advertised in `initialize` only when at least one is registered, so a
tools-only server does not claim capabilities it does not have.
