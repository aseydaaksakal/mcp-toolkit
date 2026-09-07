# Getting started

## Install

```bash
pip install mcp-toolkit
```

From a clone, with the test dependencies:

```bash
git clone https://github.com/aseydaaksakal/mcp-toolkit
cd mcp-toolkit
pip install -e ".[dev]"
pytest
```

Python 3.10 or newer. The core has no runtime dependencies.

## Your first server

Create `server.py`:

```python
from mcp_toolkit import MCPServer, ToolError

server = MCPServer("hello", version="0.1.0")

ORDERS = {"ORD-1": "shipped", "ORD-2": "processing"}


@server.tool()
def lookup_order(order_id: str) -> dict:
    """Look up the status of an order.

    Args:
        order_id: Internal order identifier, e.g. "ORD-1".
    """
    if order_id not in ORDERS:
        raise ToolError(f"no order named {order_id!r}")
    return {"id": order_id, "status": ORDERS[order_id]}


if __name__ == "__main__":
    server.run()
```

Run it and drive it by hand. MCP over stdio is newline-delimited JSON, so a
here-doc is a perfectly good client:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"lookup_order","arguments":{"order_id":"ORD-1"}}}' \
  | python server.py
```

You should see three responses. The third contains:

```json
{"content": [{"type": "text", "text": "{\n  \"id\": \"ORD-1\",\n  \"status\": \"shipped\"\n}"}], "isError": false}
```

Note what you did not write: no JSON Schema, no method table, no envelope
handling. `tools/list` reports `order_id` as a required string with your
docstring as its description, all read off the function.

## Connecting a real client

Most desktop MCP clients take a command to run. For Claude Desktop, edit
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hello": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

Use absolute paths — the client does not run from your shell's working
directory. Restart the client afterwards; most read this file only at startup.

## Debugging

**Nothing happens.** stdout is the protocol channel. A stray `print()` in your
tool corrupts the stream and the client will usually go quiet rather than
report an error. Log to stderr:

```python
import sys
print("debug", file=sys.stderr)
```

`AuditLog(stream=sys.stderr)` gives you one JSON line per call for free.

**The client sees no tools.** Check your `AccessPolicy`. Denied tools are
filtered out of `tools/list` deliberately, so a typo in an allow pattern looks
exactly like a server with no tools. `server.visible_tools()` shows what a
client would receive.

**A tool is never called.** Read the schema the model is reading:

```python
import json
print(json.dumps(server.tools["lookup_order"].input_schema, indent=2))
```

If the description is vague or a parameter has no documentation, the model has
to guess. Fill in the docstring `Args:` block, or pass `description=` and
`schema=` to the decorator to override what is generated.

## Next

- [tools.md](tools.md) — how schemas are generated and how to override them
- [adapters.md](adapters.md) — connect a database, an HTTP API or a directory
- [security.md](security.md) — what to lock down before this touches production
