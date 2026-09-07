"""JSON-RPC 2.0 envelopes and Model Context Protocol constants.

The Model Context Protocol is JSON-RPC 2.0 over a bidirectional byte stream.
This module keeps the wire format in one place so the server, the transports
and the tests all agree on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JSONRPC_VERSION = "2.0"

#: Protocol revisions this implementation understands, newest first.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

#: Version advertised when the client asks for something we do not know.
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

# ---------------------------------------------------------------------------
# Method names
# ---------------------------------------------------------------------------

INITIALIZE = "initialize"
INITIALIZED = "notifications/initialized"
PING = "ping"
TOOLS_LIST = "tools/list"
TOOLS_CALL = "tools/call"
RESOURCES_LIST = "resources/list"
RESOURCES_READ = "resources/read"
PROMPTS_LIST = "prompts/list"
PROMPTS_GET = "prompts/get"

# ---------------------------------------------------------------------------
# Error codes: -32768..-32000 is reserved by JSON-RPC, above that is ours.
# ---------------------------------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

#: Raised when an :class:`~mcp_toolkit.security.AccessPolicy` denies a call.
ACCESS_DENIED = -32001
#: Raised when a :class:`~mcp_toolkit.security.RateLimiter` bucket is empty.
RATE_LIMITED = -32002


def negotiate_protocol_version(requested: str | None) -> str:
    """Return the revision to speak with a client that asked for *requested*.

    MCP clients send the newest revision they support. Servers either echo it
    back or answer with their own newest revision and let the client decide
    whether it can continue.
    """
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested  # type: ignore[return-value]
    return DEFAULT_PROTOCOL_VERSION


@dataclass(slots=True)
class Request:
    """An inbound JSON-RPC call. ``id is None`` marks a notification."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None

    @property
    def is_notification(self) -> bool:
        return self.id is None

    @classmethod
    def from_payload(cls, payload: Any) -> Request:
        if not isinstance(payload, dict):
            raise ValueError("JSON-RPC message must be an object")
        if payload.get("jsonrpc") != JSONRPC_VERSION:
            raise ValueError("unsupported or missing 'jsonrpc' version")
        method = payload.get("method")
        if not isinstance(method, str) or not method:
            raise ValueError("missing 'method'")
        params = payload.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("'params' must be an object")
        return cls(method=method, params=params, id=payload.get("id"))


def success(request_id: str | int | None, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC result envelope."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def failure(
    request_id: str | int | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error envelope."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}
