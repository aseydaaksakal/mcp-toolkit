# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning is [semantic](https://semver.org/), with the pre-1.0 caveat that
minor releases may break the API.

## [Unreleased]

## [0.1.0] - 2026-09-07

First release.

### Added

- `MCPServer` with `tool`, `resource` and `prompt` decorators, plus `add_tool`
  and `mount` for runtime registration.
- JSON Schema generation from type hints and Google- or Sphinx-style
  docstrings. Handles `Literal`, `Enum`, `Optional`, containers and dataclasses.
- Argument validation before dispatch: required keys, primitive types, enum
  membership, array items, unknown keys. All problems reported at once.
- Guardrails: `AccessPolicy` (glob allow/deny plus scopes, filtering
  `tools/list` as well as `tools/call`), `RateLimiter` (per-tool token bucket),
  `Redactor` (secret scrubbing on results and audit records), `AuditLog`
  (JSONL to stderr).
- Adapters: `SqlAdapter` (read-only guard, table allowlist, row cap),
  `RestAdapter` (declared endpoints, quoted path parameters, opt-in write
  methods), `FileAdapter` (sandboxed root, size caps, exclusions).
- `StdioTransport` and `MemoryTransport`.
- MCP revisions `2024-11-05`, `2025-03-26` and `2025-06-18`, negotiated at
  `initialize`.
- Go implementation of the server, schema helpers and validation, with no
  dependencies.
- `python -m mcp_toolkit --root DIR` demo server.

[Unreleased]: https://github.com/aseydaaksakal/mcp-toolkit/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aseydaaksakal/mcp-toolkit/releases/tag/v0.1.0
