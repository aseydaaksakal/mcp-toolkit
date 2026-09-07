# Security

## Threat model

The agent is not the attacker. The agent is the confused deputy.

A tool call is triggered by tokens in a context window, and those tokens come
from wherever the conversation has been: a user message, a retrieved document,
a support ticket, the output of a previous tool. Any of them can contain
instructions. The model has no reliable way to tell data from instruction, so
the boundary has to be enforced on your side of the call.

Which means:

- Treat every argument as attacker-controlled, including ones the model
  "derived" from a trusted source.
- Assume anything a tool returns may be echoed back to a user later, so it must
  not contain secrets.
- Assume tools will be called in orders you did not anticipate, at rates you
  did not anticipate.

The guardrails below are layers. None is sufficient alone.

## Layer 1 — the credential

Before any of this code runs, decide what the process itself can do. A
read-only database user makes the SQL guard a second opinion rather than the
only thing standing between an agent and `DROP TABLE`. An API key scoped to the
two endpoints you declared makes a bug in `RestAdapter` uninteresting.

Grant the minimum, then use the layers below to narrow further.

## Layer 2 — AccessPolicy

```python
policy = AccessPolicy(allow=["orders.*", "catalog.read"], deny=["orders.delete"])
```

Deny wins. An empty `allow` means "everything not denied", which is the
convenient default and the wrong one for production — start from an explicit
allowlist.

The policy filters `tools/list` as well as `tools/call`. A blocked tool is
invisible rather than merely refused, so the model does not learn it exists,
does not spend turns trying it, and does not report its existence to a user who
was probing for it.

Scopes gate individual tools on session state:

```python
@server.tool(scopes=["orders:write"])
def cancel_order(order_id: str) -> str: ...

# after you have authenticated the caller yourself:
server.policy = server.policy.with_scopes("orders:write")
```

`with_scopes` returns a copy. The base policy is unchanged, so one session
granting a scope cannot widen another.

## Layer 3 — RateLimiter

```python
RateLimiter(rate=5, burst=20)
```

A token bucket per tool name. Bounds the damage from a loop the model cannot
break out of, and from a prompt injection that tells it to enumerate every
order id.

Policy is checked before the limiter, so a denied caller cannot drain the
budget of a tool it is not allowed to use. Rejections carry
`data.retry_after_seconds`, which well-behaved clients will respect.

The buckets are in-process. Across replicas you need a shared store; the class
is small enough to subclass with Redis behind `check()`.

## Layer 4 — Redactor

```python
Redactor(extra={
    "employee_id": r"\bEMP-\d{5}\b",
    "internal_url": r"https://[\w.-]+\.internal\b",
})
```

Applied to every tool result and every audit record. The defaults cover emails,
bearer tokens, `sk-`/`pk-`/`ghp-`/`xox` style keys, AWS access key ids, PEM
headers, IBANs and card numbers.

The defaults do not know your organisation's identifier formats. Add them. A
redactor that has never been extended is mostly decorative.

This is a safety net, not a control. It cannot recognise a secret that looks
like ordinary text. Do not let it substitute for not returning the column in
the first place.

## Layer 5 — error handling

```python
raise ToolError("no order named 'ORD-9'; call list_orders for valid ids")   # model sees this
raise RuntimeError("connect failed: postgres://user:hunter2@db")            # model sees a type name
```

Unexpected exceptions are logged in full and reported to the model as
`Tool 'x' failed: RuntimeError`. Backend error text is a reliable source of
connection strings, internal hostnames and table names, and anything in the
context window can be extracted from it later.

Argument validation runs before your handler, so malformed calls never reach
your code.

## Layer 6 — AuditLog

```python
AuditLog(stream=sys.stderr)
```

One JSON object per call: timestamp, tool, outcome, duration, redacted
arguments, error. stderr rather than stdout because stdout is the protocol
channel — writing anything else there corrupts the session.

```json
{"ts": 1730000000.5, "tool": "orders.lookup", "ok": false, "duration_ms": 12.4,
 "arguments": {"order_id": "ORD-9"}, "error": "ToolError: no order named 'ORD-9'"}
```

Worth alerting on: a spike in `ok: false`, a tool called far outside its usual
rate, arguments that look like enumeration.

## Checklist

Before an agent reaches a system that matters:

- [ ] The process credential is read-only, or scoped to exactly what is needed
- [ ] `AccessPolicy` starts from an explicit `allow` list
- [ ] Write tools require a scope, granted only after your own auth check
- [ ] `RateLimiter` is configured, and the limit is a number someone chose
- [ ] `Redactor` has patterns for your internal identifier formats
- [ ] `AuditLog` is wired to a stream you actually read
- [ ] Every `except` in your tools raises `ToolError` with an actionable message
- [ ] `SqlAdapter` has `allowed_tables` set
- [ ] `RestAdapter` declares only the endpoints the agent needs
- [ ] `FileAdapter` excludes credential file patterns
- [ ] You have tried to talk your own server into something it should refuse

## Reporting a vulnerability

Open a draft security advisory on the repository rather than a public issue.
