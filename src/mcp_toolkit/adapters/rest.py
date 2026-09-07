"""Turn an internal HTTP API into MCP tools.

You declare each endpoint you want reachable, one at a time. There is no
"expose the whole API" switch, because the set of endpoints an agent may call
is a security decision, not a convenience one.

    adapter = RestAdapter("https://orders.internal", headers={"X-Api-Key": key})
    adapter.endpoint(
        "lookup_order",
        "GET",
        "/orders/{order_id}",
        description="Fetch one order by its internal identifier.",
        path_params={"order_id": "Internal order identifier."},
    )
    server.mount(adapter)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import ToolError

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass(slots=True)
class Endpoint:
    """One declared HTTP operation."""

    name: str
    method: str
    path: str
    description: str
    path_params: dict[str, str] = field(default_factory=dict)
    query_params: dict[str, str] = field(default_factory=dict)
    body_schema: dict[str, Any] | None = None

    def input_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, doc in self.path_params.items():
            properties[name] = {"type": "string", "description": doc}
            required.append(name)
        for name, doc in self.query_params.items():
            properties[name] = {"type": "string", "description": doc}
        if self.body_schema is not None:
            properties["body"] = self.body_schema
            required.append("body")
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema


class RestAdapter:
    """Declare endpoints, register them as tools.

    Args:
        base_url: Scheme and host of the internal service.
        headers: Sent with every request. Put service credentials here, never
            in the tool arguments where the model can see or invent them.
        timeout: Per-request timeout in seconds.
        allow_write_methods: Off by default. Turn it on knowingly.
        opener: Injection point for tests; defaults to :mod:`urllib.request`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
        allow_write_methods: bool = False,
        opener: Callable[[urllib.request.Request, float], Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.allow_write_methods = allow_write_methods
        self._opener = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self.endpoints: list[Endpoint] = []

    def endpoint(
        self,
        name: str,
        method: str,
        path: str,
        *,
        description: str,
        path_params: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
        body_schema: dict[str, Any] | None = None,
    ) -> Endpoint:
        """Declare one endpoint and return it."""
        method = method.upper()
        if method not in SAFE_METHODS and not self.allow_write_methods:
            raise ValueError(
                f"{method} is a write method; construct RestAdapter with "
                "allow_write_methods=True if that is intended"
            )
        declared = Endpoint(
            name=name,
            method=method,
            path=path,
            description=description,
            path_params=dict(path_params or {}),
            query_params=dict(query_params or {}),
            body_schema=body_schema,
        )
        self.endpoints.append(declared)
        return declared

    # -- invocation ---------------------------------------------------------

    def call(self, endpoint: Endpoint, arguments: dict[str, Any]) -> Any:
        """Perform the request described by *endpoint*."""
        path = endpoint.path
        for name in endpoint.path_params:
            value = arguments.get(name)
            if value is None:
                raise ToolError(f"missing path parameter {name!r}")
            # Quote so a value like "../admin" cannot escape the declared path.
            path = path.replace("{" + name + "}", urllib.parse.quote(str(value), safe=""))

        query = {
            name: str(arguments[name])
            for name in endpoint.query_params
            if arguments.get(name) is not None
        }
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)

        data = None
        headers = dict(self.headers)
        if endpoint.body_schema is not None:
            data = json.dumps(arguments.get("body")).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")

        request = urllib.request.Request(
            url, data=data, headers=headers, method=endpoint.method
        )
        try:
            with self._opener(request, self.timeout) as response:
                raw = response.read()
                content_type = ""
                if hasattr(response, "headers"):
                    content_type = response.headers.get("Content-Type", "") or ""
        except urllib.error.HTTPError as exc:
            raise ToolError(f"{endpoint.name} returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ToolError(f"{endpoint.name} is unreachable: {exc.reason}") from exc

        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        if "json" in content_type or text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text

    # -- wiring -------------------------------------------------------------

    def register(self, server: Any, prefix: str = "") -> None:
        """Register every declared endpoint as a tool on *server*."""
        for endpoint in self.endpoints:
            server.add_tool(
                f"{prefix}{endpoint.name}",
                self._make_handler(endpoint),
                description=endpoint.description,
                schema=endpoint.input_schema(),
            )

    def _make_handler(self, endpoint: Endpoint) -> Callable[..., Any]:
        def handler(**arguments: Any) -> Any:
            return self.call(endpoint, arguments)

        handler.__name__ = endpoint.name
        handler.__doc__ = endpoint.description
        return handler
