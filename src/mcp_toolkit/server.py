"""The server: register Python callables, answer MCP requests.

    from mcp_toolkit import MCPServer

    server = MCPServer("internal-tools")

    @server.tool()
    def get_order(order_id: str) -> dict:
        '''Look up an order in the fulfilment database.

        Args:
            order_id: Internal order identifier, e.g. "ORD-8812".
        '''
        return db.orders.find(order_id)

    server.run()
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import protocol
from .errors import AccessDenied, InvalidParams, MCPError, MethodNotFound, ToolError
from .schema import (
    schema_from_signature,
    summarize_docstring,
    validate_arguments,
)
from .security import AccessPolicy, AuditLog, AuditRecord, RateLimiter, Redactor
from .transport import StdioTransport, Transport

Handler = Callable[..., Any]


@dataclass(slots=True)
class Tool:
    """A callable exposed to the agent."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    scopes: set[str] = field(default_factory=set)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass(slots=True)
class Resource:
    """A readable blob addressed by URI."""

    uri: str
    name: str
    description: str
    mime_type: str
    reader: Callable[[], Any]

    def describe(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass(slots=True)
class Prompt:
    """A named, parameterised prompt template."""

    name: str
    description: str
    arguments: list[dict[str, Any]]
    builder: Callable[..., str]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
        }


class MCPServer:
    """Registry plus JSON-RPC dispatcher.

    Args:
        name: Server name reported during ``initialize``.
        version: Your build version, also reported during ``initialize``.
        policy: Access rules. Defaults to allowing every registered tool.
        rate_limiter: Optional per-tool token bucket.
        redactor: Applied to every tool result and audit record.
        audit_log: Where call records go. Defaults to in-memory only.
        instructions: Free text shown to the agent alongside the tool list.
    """

    def __init__(
        self,
        name: str,
        version: str = "0.1.0",
        *,
        policy: AccessPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        redactor: Redactor | None = None,
        audit_log: AuditLog | None = None,
        instructions: str | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.policy = policy or AccessPolicy()
        self.rate_limiter = rate_limiter
        self.redactor = redactor or Redactor()
        self.audit_log = audit_log or AuditLog(redactor=self.redactor)
        self.instructions = instructions

        self._tools: dict[str, Tool] = {}
        self._resources: dict[str, Resource] = {}
        self._prompts: dict[str, Prompt] = {}
        self._protocol_version = protocol.DEFAULT_PROTOCOL_VERSION
        self._initialized = False

    # -- registration -------------------------------------------------------

    def tool(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        schema: dict[str, Any] | None = None,
        scopes: Iterable[str] = (),
    ) -> Callable[[Handler], Handler]:
        """Decorator that registers a function as a tool.

        The name defaults to the function name, the description to the summary
        line of its docstring, and the schema to one derived from its type
        hints. Override any of them when the generated version is not what you
        want the model to see.
        """

        def decorator(func: Handler) -> Handler:
            tool_name = name or func.__name__
            if tool_name in self._tools:
                raise ValueError(f"tool {tool_name!r} is already registered")
            tool_scopes = set(scopes)
            self._tools[tool_name] = Tool(
                name=tool_name,
                description=description or summarize_docstring(func) or tool_name,
                input_schema=schema or schema_from_signature(func),
                handler=func,
                scopes=tool_scopes,
            )
            if tool_scopes:
                self.policy.required_scopes[tool_name] = tool_scopes
            return func

        return decorator

    def add_tool(
        self,
        name: str,
        handler: Handler,
        *,
        description: str | None = None,
        schema: dict[str, Any] | None = None,
        scopes: Iterable[str] = (),
    ) -> Tool:
        """Register a tool imperatively. Used by the adapters."""
        self.tool(name, description=description, schema=schema, scopes=scopes)(handler)
        return self._tools[name]

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str = "",
        mime_type: str = "text/plain",
    ) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
        """Decorator that registers a readable resource at *uri*."""

        def decorator(func: Callable[[], Any]) -> Callable[[], Any]:
            self._resources[uri] = Resource(
                uri=uri,
                name=name or func.__name__,
                description=description or summarize_docstring(func),
                mime_type=mime_type,
                reader=func,
            )
            return func

        return decorator

    def prompt(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
        arguments: Sequence[dict[str, Any]] = (),
    ) -> Callable[[Callable[..., str]], Callable[..., str]]:
        """Decorator that registers a prompt template."""

        def decorator(func: Callable[..., str]) -> Callable[..., str]:
            prompt_name = name or func.__name__
            self._prompts[prompt_name] = Prompt(
                name=prompt_name,
                description=description or summarize_docstring(func),
                arguments=list(arguments),
                builder=func,
            )
            return func

        return decorator

    def mount(self, adapter: Any, prefix: str = "") -> MCPServer:
        """Let an adapter register its tools and resources on this server."""
        adapter.register(self, prefix=prefix)
        return self

    # -- introspection ------------------------------------------------------

    @property
    def tools(self) -> dict[str, Tool]:
        return dict(self._tools)

    def visible_tools(self) -> list[Tool]:
        """Tools the current policy allows, in registration order."""
        return [t for t in self._tools.values() if self.policy.permits(t.name)]

    # -- dispatch -----------------------------------------------------------

    def handle(self, payload: Any) -> dict[str, Any] | None:
        """Handle one decoded JSON-RPC message.

        Returns the response envelope, or ``None`` for notifications, which
        get no reply by definition.
        """
        try:
            request = protocol.Request.from_payload(payload)
        except ValueError as exc:
            request_id = payload.get("id") if isinstance(payload, dict) else None
            return protocol.failure(request_id, protocol.INVALID_REQUEST, str(exc))

        try:
            result = self._dispatch(request)
        except MCPError as exc:
            if request.is_notification:
                return None
            return protocol.failure(request.id, exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            if request.is_notification:
                return None
            return protocol.failure(
                request.id, protocol.INTERNAL_ERROR, f"internal error: {exc}"
            )

        if request.is_notification:
            return None
        return protocol.success(request.id, result)

    def _dispatch(self, request: protocol.Request) -> Any:
        method = request.method
        params = request.params

        if method == protocol.INITIALIZE:
            return self._initialize(params)
        if method == protocol.INITIALIZED:
            self._initialized = True
            return None
        if method == protocol.PING:
            return {}
        if method == protocol.TOOLS_LIST:
            return {"tools": [t.describe() for t in self.visible_tools()]}
        if method == protocol.TOOLS_CALL:
            return self._call_tool(params)
        if method == protocol.RESOURCES_LIST:
            return {"resources": [r.describe() for r in self._resources.values()]}
        if method == protocol.RESOURCES_READ:
            return self._read_resource(params)
        if method == protocol.PROMPTS_LIST:
            return {"prompts": [p.describe() for p in self._prompts.values()]}
        if method == protocol.PROMPTS_GET:
            return self._get_prompt(params)
        if method.startswith("notifications/"):
            return None
        raise MethodNotFound(f"unknown method {method!r}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        self._protocol_version = protocol.negotiate_protocol_version(
            params.get("protocolVersion")
        )
        capabilities: dict[str, Any] = {"tools": {"listChanged": False}}
        if self._resources:
            capabilities["resources"] = {"subscribe": False, "listChanged": False}
        if self._prompts:
            capabilities["prompts"] = {"listChanged": False}

        result: dict[str, Any] = {
            "protocolVersion": self._protocol_version,
            "capabilities": capabilities,
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str):
            raise InvalidParams("'name' is required and must be a string")

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise InvalidParams("'arguments' must be an object")

        tool = self._tools.get(name)
        if tool is None:
            raise MethodNotFound(f"unknown tool {name!r}")

        # Deny before rate limiting so a blocked caller cannot drain buckets.
        self.policy.enforce(name)
        if self.rate_limiter is not None:
            self.rate_limiter.check(name)

        problems = validate_arguments(tool.input_schema, arguments)
        if problems:
            raise InvalidParams("; ".join(problems))

        started = time.perf_counter()
        try:
            value = tool.handler(**arguments)
        except ToolError as exc:
            self._audit(name, arguments, started, ok=False, error=exc.message)
            return {"content": [_text_block(exc.message)], "isError": True}
        except AccessDenied:
            raise
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            self._audit(name, arguments, started, ok=False, error=detail)
            # The traceback goes to the audit log, never into the model's context.
            traceback.clear_frames(exc.__traceback__) if exc.__traceback__ else None
            return {
                "content": [_text_block(f"Tool {name!r} failed: {type(exc).__name__}")],
                "isError": True,
            }

        self._audit(name, arguments, started, ok=True)
        return {"content": to_content(self.redactor.scrub(value)), "isError": False}

    def _audit(
        self,
        tool: str,
        arguments: dict[str, Any],
        started: float,
        *,
        ok: bool,
        error: str | None = None,
    ) -> None:
        self.audit_log.write(
            AuditRecord(
                tool=tool,
                ok=ok,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                arguments=dict(arguments),
                error=error,
            )
        )

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri")
        resource = self._resources.get(uri) if isinstance(uri, str) else None
        if resource is None:
            raise MethodNotFound(f"unknown resource {uri!r}")
        body = self.redactor.scrub(resource.reader())
        text = body if isinstance(body, str) else json.dumps(body, default=str)
        return {
            "contents": [
                {"uri": resource.uri, "mimeType": resource.mime_type, "text": text}
            ]
        }

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        prompt = self._prompts.get(name) if isinstance(name, str) else None
        if prompt is None:
            raise MethodNotFound(f"unknown prompt {name!r}")
        arguments = params.get("arguments") or {}
        text = prompt.builder(**arguments)
        return {
            "description": prompt.description,
            "messages": [
                {"role": "user", "content": {"type": "text", "text": text}}
            ],
        }

    # -- transport ----------------------------------------------------------

    def run(self, transport: Transport | None = None) -> None:
        """Serve requests until the peer closes the stream."""
        active = transport or StdioTransport()
        for payload in active.receive():
            response = self.handle(payload)
            if response is not None:
                active.send(response)


def _text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def to_content(value: Any) -> list[dict[str, Any]]:
    """Normalise a tool's return value into MCP content blocks.

    Strings pass through. Lists of ready-made blocks pass through. Everything
    else is JSON-encoded, because a model reads a stable serialisation more
    reliably than it reads ``repr()``.
    """
    if isinstance(value, str):
        return [_text_block(value)]
    if value is None:
        return [_text_block("")]
    if isinstance(value, list) and all(
        isinstance(item, dict) and "type" in item for item in value
    ):
        return value
    if isinstance(value, dict) and "type" in value:
        return [value]
    return [_text_block(json.dumps(value, indent=2, default=str, ensure_ascii=False))]
