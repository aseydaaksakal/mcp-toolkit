"""Exceptions that map cleanly onto JSON-RPC error codes."""

from __future__ import annotations

from typing import Any

from . import protocol


class MCPError(Exception):
    """Base class. Carries the JSON-RPC code the client will receive."""

    code = protocol.INTERNAL_ERROR

    def __init__(self, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.data = data


class InvalidParams(MCPError):
    """Arguments failed validation before the handler ran."""

    code = protocol.INVALID_PARAMS


class MethodNotFound(MCPError):
    """Unknown JSON-RPC method, tool, resource or prompt."""

    code = protocol.METHOD_NOT_FOUND


class AccessDenied(MCPError):
    """An :class:`~mcp_toolkit.security.AccessPolicy` refused the call."""

    code = protocol.ACCESS_DENIED


class RateLimited(MCPError):
    """A :class:`~mcp_toolkit.security.RateLimiter` bucket was empty."""

    code = protocol.RATE_LIMITED


class ToolError(MCPError):
    """Raise inside a tool to return a clean failure to the agent.

    Anything else that escapes a tool is caught, logged and reported as an
    internal error with the traceback stripped, so backend exception messages
    never leak into the model's context.
    """

    code = protocol.INTERNAL_ERROR
