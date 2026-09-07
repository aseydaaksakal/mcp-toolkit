# mcp-toolkit

Expose internal systems to LLM agents over the [Model Context Protocol](https://modelcontextprotocol.io), without hand-writing JSON-RPC or JSON Schema. Python and Go, same wire format.

<div class="grid cards" markdown>

-   **[Playground →](playground.html)**

    A real MCP server running in your browser. Send it `initialize`, `tools/list`, `tools/call` and watch the guardrails respond.

-   **[Source on GitHub →](https://github.com/aseydaaksakal/mcp-toolkit)**

    MIT licensed. `pip install -e ".[dev]" && pytest`, `cd go && go test ./...`.

</div>

## What it does

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

That is a complete MCP server. Name, description and input schema come from the function, so they cannot drift from the code they describe.

## Why it exists

The protocol is the small part. The work is everything around it:

- **Schemas are prompt surface.** A model picks tools by reading their JSON Schema. Hand-maintained schemas drift, and the failure is silent.
- **The caller is not trusted.** Anything in a context window can steer a tool call. "Read-only" has to be enforced, not documented.
- **Backend errors leak.** A stack trace with a connection string in it is a stack trace the model can be persuaded to repeat.

Access rules, rate limits and redaction are constructor arguments. Unexpected exceptions reach the audit log in full and the model as a type name. Start with [Getting started](getting-started.md), then read [Security](security.md) before anything touches production.
