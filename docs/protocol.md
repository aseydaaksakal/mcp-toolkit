# Protocol notes

MCP is JSON-RPC 2.0 over a bidirectional byte stream. This page records what
this implementation does, so you do not have to read the source to find out.

## Transport

The stdio transport is **newline-delimited JSON**: one object per line, no
`Content-Length` framing.

Practical consequence: stdout belongs to the protocol. A `print()` in a tool
puts a non-JSON line into the stream, and most clients respond by going quiet
rather than reporting an error. Log to stderr.

`StdioTransport` skips lines it cannot parse and notes them on stderr, rather
than tearing down a working session over one bad line. A malformed line has no
`id`, so there is nobody to answer anyway.

`MemoryTransport` takes a list of messages and collects the replies, which is
what the tests use and what you want when embedding the dispatcher in a larger
process.

## Version negotiation

Supported revisions, newest first:

```
2025-06-18, 2025-03-26, 2024-11-05
```

The client sends its newest in `initialize`. If it is one of ours we echo it;
otherwise we answer with `2025-06-18` and the client decides whether it can
continue. This implementation uses no feature that differs across those three.

## Methods

| Method | Supported | Notes |
|---|---|---|
| `initialize` | yes | Returns `protocolVersion`, `capabilities`, `serverInfo`, optional `instructions` |
| `notifications/initialized` | yes | Accepted, no reply, as required for notifications |
| `ping` | yes | Returns `{}` |
| `tools/list` | yes | Filtered by `AccessPolicy` |
| `tools/call` | yes | Validates arguments before dispatch |
| `resources/list` | yes | |
| `resources/read` | yes | Non-string values are JSON-encoded |
| `prompts/list` | yes | |
| `prompts/get` | yes | Returns one user message |
| `notifications/*` (other) | accepted | Ignored rather than erroring |
| `completion/complete` | no | |
| `sampling/*`, `roots/*` | no | Requires server-initiated requests |
| `resources/subscribe` | no | |

Capabilities are advertised only for what is registered: a server with no
prompts does not claim a `prompts` capability.

## Error codes

| Code | Name | Raised when |
|---|---|---|
| `-32700` | parse error | The Go server received unparseable JSON |
| `-32600` | invalid request | Bad envelope: wrong `jsonrpc`, missing `method` |
| `-32601` | method not found | Unknown method, tool, resource or prompt |
| `-32602` | invalid params | Argument validation failed |
| `-32603` | internal error | Unexpected failure in the dispatcher |
| `-32001` | access denied | `AccessPolicy` refused the call |
| `-32002` | rate limited | Bucket empty; `data.retry_after_seconds` is set |

`-32001` and `-32002` are outside the reserved `-32768..-32000` range, which is
where JSON-RPC says implementation-defined codes belong.

## Tool failures are not protocol failures

A tool that raises returns a **successful** JSON-RPC response whose result has
`isError: true`:

```json
{"jsonrpc": "2.0", "id": 3,
 "result": {"content": [{"type": "text", "text": "no order named 'ORD-9'"}],
            "isError": true}}
```

This is deliberate and matches the specification. The distinction is between
"the call could not be made" (JSON-RPC error, the client's problem) and "the
call was made and failed" (`isError`, the model's problem). Only the second
belongs in the model's context, where it can act on it.

## Content blocks

Results are always a list of blocks. This implementation emits `text` blocks;
if a tool returns blocks of its own — `image`, `resource` — they pass through
unchanged.

## Parity between the Python and Go servers

Same wire format, same method set, same error codes, same `isError` semantics.

Differences:

- Go declares schemas with `ObjectSchema` — there are no type hints to read.
- Go validates `"integer"` as a `float64` with no fractional part, because
  `encoding/json` decodes every number as `float64`.
- The guardrails (`AccessPolicy`, `RateLimiter`, `Redactor`, `AuditLog`) are
  Python-only so far.
