# Contributing

## Setup

```bash
git clone https://github.com/aseydaaksakal/mcp-toolkit
cd mcp-toolkit
pip install -e ".[dev]"
pytest
cd go && go test ./...
```

## Before opening a pull request

```bash
pytest                      # Python suite
ruff check .
cd go && go vet ./... && go test ./... && gofmt -l .
```

CI runs all of these on Python 3.10 through 3.13. `gofmt -l .` printing any
filename is a failure.

## What makes a change easy to merge

**A test that fails without it.** For a bug, the test should reproduce the bug.
For a feature, it should describe the behaviour you want.

**A reason in the commit message.** What the code does is in the diff. Why it
had to change is not.

**Errors written for the reader.** Tool and validation messages end up in a
model's context window, where they are read as instructions. `no order named
'ORD-9'; call list_orders for valid ids` gives the model somewhere to go.
`Invalid input` does not.

**Docs updated in the same change.** If you add an adapter, `docs/adapters.md`
should describe it. If you change what a guardrail enforces, `docs/security.md`
should say so.

## Scope

In scope: adapters for common internal systems, guardrails, protocol coverage,
better schema inference.

Ask first: async handlers, HTTP/SSE transport, server-initiated requests
(sampling, roots). These are wanted but change the architecture, so it is worth
agreeing on an approach in an issue before writing code.

Out of scope: agent frameworks, prompt libraries, vector stores. This library
exposes systems over a protocol and stops there.

## Security

Do not open a public issue for a vulnerability. Open a draft security advisory
on the repository instead.
