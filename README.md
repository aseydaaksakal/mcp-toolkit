# mcp-toolkit

Expose internal systems to LLM agents over the [Model Context Protocol](https://modelcontextprotocol.io), without hand-writing JSON-RPC or JSON Schema.

Point it at a database, an internal HTTP API or a directory, decide what the agent may reach, and run it. Python and Go implementations live in the same repository and speak the same wire format.

```python
from mcp_toolkit import MCPServer

server = MCPServer("internal-tools")

@server.tool()
def get_order(order_id: str) -> dict:
    """Look up an order in the fulfilment database.

    Args:
        order_id: Internal order identifier, e.g. "ORD-8812".
    """
    return db.orders.find(order_id)

server.run()
```

That is a complete MCP server. The tool name, description and input schema are derived from the function, so they cannot drift away from the code they describe.

---

## Why this exists

Wiring an agent to an internal system is mostly not protocol work. The protocol is a few hundred lines. The work is everything around it:

- **Schemas are prompt surface.** A model picks tools by reading their JSON Schema. Hand-maintained schemas fall out of step with the functions, and the failure is silent: the model keeps calling the tool with the arguments the schema promised.
- **The caller is not trusted.** Anything in the model's context can steer a tool call, including text the model was merely asked to summarise. "Only expose read queries" has to be enforced, not documented.
- **Backend errors leak.** A stack trace containing a connection string is a stack trace the model can be persuaded to repeat.

mcp-toolkit takes positions on all three. Schemas come from type hints. Access rules, rate limits and redaction are constructor arguments rather than an integration guide. Unexpected exceptions reach the audit log in full and the model as a type name.

## Install

```bash
pip install mcp-toolkit          # once published
pip install -e ".[dev]"          # from a clone, with the test extras
```

Python 3.10+. No runtime dependencies — the core is standard library only.

The Go implementation is a separate module:

```bash
go get github.com/aseydaaksakal/mcp-toolkit/go
```

## Try it in 30 seconds

Serve the current directory over stdio and talk to it by hand:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | python -m mcp_toolkit --root .
```

To use it from Claude Desktop or any other MCP client, add it to the client's server config:

```json
{
  "mcpServers": {
    "workspace": {
      "command": "python",
      "args": ["-m", "mcp_toolkit", "--root", "/path/to/project"]
    }
  }
}
```

## Adapters

Three ready-made bridges for the systems that come up most often.

### SQL — read-only, allowlisted

```python
from mcp_toolkit import MCPServer, SqlAdapter

server = MCPServer("warehouse")
server.mount(
    SqlAdapter(connection, allowed_tables=["orders", "order_lines"], max_rows=100),
    prefix="db.",
)
```

The guard rejects anything that is not a single `SELECT` or `WITH`: no second statement, no comments, no DDL or DML keywords, and no table outside the allowlist. `api_keys` is not reachable no matter how the request is phrased. Results are capped and flagged with `truncated` so the agent knows it saw a page rather than the whole answer.

### REST — one endpoint at a time

```python
api = RestAdapter("https://orders.internal", headers={"X-Api-Key": key})
api.endpoint(
    "lookup_order", "GET", "/v1/orders/{order_id}",
    description="Fetch one order by its internal identifier.",
    path_params={"order_id": 'Internal identifier, e.g. "ORD-8812".'},
)
server.mount(api)
```

There is no "expose the whole API" switch, because which endpoints an agent may call is a security decision. Path parameters are URL-quoted, so `../admin/keys` stays inside the declared path. Write methods raise unless you pass `allow_write_methods=True`.

### Files — sandboxed

```python
server.mount(FileAdapter(root="/srv/runbooks", include=["*.md"]), prefix="fs.")
```

Paths are resolved and checked against the root, so symlinks and `../` cannot walk out. Reads are size-capped and restricted to text suffixes; `.pem`, `.key` and `.env` are excluded by default.

## Guardrails

Each one is an object you pass to the server, and each one is independently testable.

```python
from mcp_toolkit import AccessPolicy, AuditLog, MCPServer, RateLimiter, Redactor

server = MCPServer(
    "internal-tools",
    policy=AccessPolicy(allow=["orders.*"], deny=["orders.delete"]),
    rate_limiter=RateLimiter(rate=5, burst=20),
    redactor=Redactor(extra={"employee_id": r"\bEMP-\d{5}\b"}),
    audit_log=AuditLog(stream=sys.stderr),
)
```

**AccessPolicy** filters `tools/list` as well as `tools/call`, so a blocked tool is invisible rather than merely refused — the model never learns it exists and never spends turns trying. Deny beats allow. Tools registered with `scopes=[...]` also need those scopes granted on the session:

```python
@server.tool(scopes=["orders:write"])
def cancel_order(order_id: str) -> str:
    ...

server.policy = server.policy.with_scopes("orders:write")   # after your auth check
```

**RateLimiter** is a token bucket per tool name. Policy is checked before the limiter, so a denied caller cannot drain another tool's budget.

**Redactor** scrubs every tool result and every audit record. The defaults catch emails, bearer tokens, `sk-`/`ghp-` style keys, AWS access key ids, IBANs, card numbers and PEM headers. It does not know your internal identifier formats — add those.

**AuditLog** writes one JSON object per call to stderr (stdout is the protocol channel). Arguments are redacted before they are written, so the log is safe to ship to a normal pipeline.

## Errors

Two paths, on purpose:

| You raise | The agent sees | Use it for |
|---|---|---|
| `ToolError("no order named 'X'")` | `isError: true`, your message | Expected failures the model can recover from |
| anything else | `isError: true`, `"Tool 'x' failed: RuntimeError"` | Bugs and backend failures |

The second path keeps exception text out of the model's context. The full message still reaches the audit log:

```python
raise RuntimeError("connect failed: postgres://user:hunter2@db")
# model sees:  Tool 'lookup' failed: RuntimeError
# audit log:   {"tool": "lookup", "ok": false, "error": "RuntimeError: connect failed: ..."}
```

Argument validation happens before the handler runs, and failures come back as JSON-RPC `-32602` with a message naming the specific problem — `missing required argument 'order_id'` rather than a schema dump.

## Go

Same protocol, same argument validation, no dependencies:

```go
server := mcp.NewServer("internal-tools", "0.1.0")
server.Tool("lookup_order", "Look up an order by its identifier.",
    mcp.ObjectSchema(map[string]mcp.Property{
        "order_id": {Type: "string", Description: "Internal order identifier."},
    }, []string{"order_id"}),
    func(args map[string]any) (any, error) {
        return lookup(args["order_id"].(string))
    })
server.Serve(os.Stdin, os.Stdout)
```

```bash
cd go && go test ./... && go run ./cmd/example-server
```

Go has no type hints to introspect, so schemas are declared with `ObjectSchema`. Validation accounts for `encoding/json` decoding every number as `float64`: `"integer"` means a `float64` with no fractional part, so `1.5` is rejected and `2.0` is not.

## Documentation

| Guide | Covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Install, first server, connecting a client |
| [docs/tools.md](docs/tools.md) | Schema generation, docstrings, overrides, errors |
| [docs/adapters.md](docs/adapters.md) | SQL, REST and file adapters in depth |
| [docs/security.md](docs/security.md) | Threat model and how each guardrail addresses it |
| [docs/protocol.md](docs/protocol.md) | Wire format and supported methods |

Runnable examples: [`examples/`](examples/).

## Status

Early. The API described here is tested and works; it is not yet stable across minor versions. Pre-1.0 releases may break it, and the changelog will say so when they do.

Not implemented: server-initiated messages (sampling, roots, progress notifications), resource subscriptions, HTTP/SSE transport, async handlers.

## Contributing

`pytest` and `go test ./...` both pass before anything merges. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
