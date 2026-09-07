# Adapters

An adapter is any object with a `register(server, prefix="")` method. The three
here cover the systems that come up most often; writing your own is a dozen
lines.

## SQL

```python
from mcp_toolkit import MCPServer, SqlAdapter
import psycopg

server = MCPServer("warehouse")
server.mount(
    SqlAdapter(
        psycopg.connect(DSN),
        allowed_tables=["orders", "order_lines"],
        max_rows=100,
        dialect_placeholder="%s",
    ),
    prefix="db.",
)
```

Registers `db.query` and `db.list_tables`. Works with any PEP 249 driver.

### What the guard rejects

| Query | Reason |
|---|---|
| `DELETE FROM orders` | not a read |
| `SELECT 1; DROP TABLE orders` | more than one statement |
| `SELECT 1 -- comment` | comments can hide a second statement from a naive parser |
| `PRAGMA table_info(orders)` | not `SELECT` or `WITH` |
| `SELECT token FROM api_keys` | table not in `allowed_tables` |

`WITH ... SELECT` is allowed. The allowlist is checked against every `FROM` and
`JOIN` target, so a join into an unexposed table fails like a direct read of it.

This is a keyword and structure guard, not a SQL parser. It is the second line
of defence. The first is a database user with `SELECT` and nothing else — set
that up regardless.

### Rows

`max_rows` is a hard cap. The adapter fetches one row past it to set
`truncated`, so the agent can tell a complete answer from a page:

```json
{"columns": ["id"], "rows": [{"id": "ORD-1"}], "row_count": 1, "truncated": true}
```

Parameters are bound by the driver:

```python
adapter.query("SELECT id FROM orders WHERE customer = ?", ["ops@corp.example"])
```

The tool description says so, which is what keeps models from formatting values
into the SQL string instead.

## REST

```python
api = RestAdapter(
    "https://orders.internal",
    headers={"X-Api-Key": os.environ["ORDERS_API_KEY"]},
    timeout=8.0,
)

api.endpoint(
    "lookup_order", "GET", "/v1/orders/{order_id}",
    description="Fetch one order by its internal identifier.",
    path_params={"order_id": 'Internal identifier, e.g. "ORD-8812".'},
    query_params={"expand": 'Relations to expand, e.g. "lines".'},
)

server.mount(api)
```

Each `endpoint()` call becomes one tool with a schema built from the declared
parameters. Path parameters are required; query parameters are optional.

**Credentials belong in `headers`.** They are attached per request and never
appear in a tool schema, so the model cannot read them, cannot leak them, and
cannot substitute a different value.

**Path parameters are URL-quoted.** `order_id="../admin/keys"` requests
`/v1/orders/..%2Fadmin%2Fkeys`, which your router will reject, rather than
`/v1/admin/keys`, which it might not.

**Write methods are opt-in.** `POST`, `PUT`, `PATCH` and `DELETE` raise
`ValueError` at declaration time unless you pass `allow_write_methods=True`.
The friction is intentional: it makes "this agent can mutate state" a decision
someone made rather than a default someone inherited.

For a write endpoint, declare the body:

```python
api = RestAdapter(BASE, allow_write_methods=True)
api.endpoint(
    "create_note", "POST", "/v1/orders/{order_id}/notes",
    description="Attach an internal note to an order.",
    path_params={"order_id": "Order identifier."},
    body_schema={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Note body."}},
        "required": ["text"],
    },
)
```

Responses are parsed as JSON when the content type says so or the body starts
with `{` or `[`; otherwise you get the text. HTTP errors become `ToolError`
carrying the status code, so the model sees `lookup_order returned HTTP 404`
rather than a stack trace.

## Files

```python
server.mount(
    FileAdapter(
        root="/srv/runbooks",
        include=["*.md", "*.txt"],
        exclude=[".git/*", "*.pem", "drafts/*"],
        max_bytes=200_000,
    ),
    prefix="fs.",
)
```

Registers `fs.list_files`, `fs.read_file` and `fs.search`.

Every path is resolved with `Path.resolve()` and then checked against the
resolved root, which handles `../`, absolute paths and symlinks pointing
outside the sandbox in one step. Excluded files are invisible to listing,
reading and search alike — not filtered out of the listing and then readable by
name.

`max_bytes` matters more than it looks. One large file is enough to fill a
context window, and the agent has no way to know that before asking. The
adapter refuses with a message that names the size and the limit, so the model
can pick a different file instead of retrying.

`fs.search` is a case-insensitive literal substring scan returning path, line
number and the matching line, capped at `max_matches`. It skips files over
`max_bytes` and anything that is not valid UTF-8.

## Writing your own

```python
class MetricsAdapter:
    def __init__(self, client):
        self.client = client

    def query_metric(self, name: str, window: str = "1h") -> dict:
        """Read a time series from the metrics backend.

        Args:
            name: Metric name, e.g. "http.requests.p99".
            window: Lookback window, e.g. "15m", "1h", "7d".
        """
        return self.client.range(name, window)

    def register(self, server, prefix=""):
        server.add_tool(f"{prefix}query_metric", self.query_metric)


server.mount(MetricsAdapter(client), prefix="metrics.")
```

`add_tool` runs the same schema generation as the decorator, so bound methods
get their annotations and docstrings read the same way plain functions do.
