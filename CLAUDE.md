# Working in this repository

Python package in `src/mcp_toolkit`, Go module in `go/`, docs in `docs/` (mkdocs-material, published to Pages), in-browser demo in `docs/playground.html` (Pyodide loads the wheel built in CI).

## Before you finish any task
- `pytest` and `ruff check .` pass. In `go/`: `go vet ./... && go test ./... && test -z "$(gofmt -l .)"`.
- Every new behaviour has a test that fails without it. Every error message a model might read is actionable ("no order named 'X'; call list_orders for valid ids"), not a status word.
- Docs updated in the same change: a new adapter appears in `docs/adapters.md`, a new guardrail in `docs/security.md`, protocol coverage in `docs/protocol.md`. `CHANGELOG.md` gets a line under Unreleased.
- Public API additions are exported from `mcp_toolkit/__init__.py` and listed in `__all__`.

## Design rules that do not change
- Schemas come from type hints and docstrings; never hand-write one where generation works.
- Guardrails are objects passed to `MCPServer`, independently testable, applied before the handler runs. Policy is checked before the rate limiter.
- `ToolError` text reaches the model; any other exception reaches the model as a type name only and the audit log in full.
- stdout is the protocol channel. Logs go to stderr.
- New language implementations live in their own top-level directory (`go/`, `ts/`, `rust/`), speak the identical wire format, and get the same CI job pattern as `go/`.

## Style
Ruff, line length 100, Python 3.10+. Comments explain why, not what. No emoji, no marketing language in docs.
