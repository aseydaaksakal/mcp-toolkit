import json

import pytest

from mcp_toolkit import AccessPolicy, MCPServer, MemoryTransport, RateLimiter, ToolError, protocol


def rpc(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return message


@pytest.fixture
def server():
    srv = MCPServer("test-server", version="9.9.9", instructions="Be careful.")

    @srv.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @srv.tool(name="orders.lookup")
    def lookup(order_id: str) -> dict:
        """Look up an order."""
        return {"id": order_id, "status": "shipped"}

    @srv.tool()
    def explode() -> str:
        """Always fails with an unexpected error."""
        raise RuntimeError("database password is hunter2")

    @srv.tool()
    def refuse() -> str:
        """Always fails with a clean tool error."""
        raise ToolError("order is archived")

    return srv


def test_initialize_reports_server_info(server):
    result = server.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}))["result"]
    assert result["serverInfo"] == {"name": "test-server", "version": "9.9.9"}
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["instructions"] == "Be careful."


def test_unknown_protocol_version_falls_back_to_ours(server):
    result = server.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))["result"]
    assert result["protocolVersion"] == protocol.DEFAULT_PROTOCOL_VERSION


def test_tools_list_includes_generated_schema(server):
    tools = server.handle(rpc("tools/list"))["result"]["tools"]
    add = next(t for t in tools if t["name"] == "add")
    assert add["description"] == "Add two integers."
    assert add["inputSchema"]["required"] == ["a", "b"]


def test_tools_call_returns_json_content(server):
    result = server.handle(
        rpc("tools/call", {"name": "orders.lookup", "arguments": {"order_id": "ORD-1"}})
    )["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["status"] == "shipped"


def test_tools_call_validates_arguments(server):
    error = server.handle(rpc("tools/call", {"name": "add", "arguments": {"a": 1}}))["error"]
    assert error["code"] == protocol.INVALID_PARAMS
    assert "missing required argument 'b'" in error["message"]


def test_unknown_tool_is_method_not_found(server):
    error = server.handle(rpc("tools/call", {"name": "nope", "arguments": {}}))["error"]
    assert error["code"] == protocol.METHOD_NOT_FOUND


def test_tool_error_is_reported_as_content_not_transport_error(server):
    result = server.handle(rpc("tools/call", {"name": "refuse", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "order is archived"


def test_unexpected_exception_does_not_leak_its_message(server):
    result = server.handle(rpc("tools/call", {"name": "explode", "arguments": {}}))["result"]
    assert result["isError"] is True
    assert "hunter2" not in result["content"][0]["text"]
    assert "RuntimeError" in result["content"][0]["text"]
    # ...but the operator can still see it in the audit log.
    record = next(server.audit_log.entries("explode"))
    assert "hunter2" in record.error


def test_policy_hides_and_blocks_tools():
    srv = MCPServer("t", policy=AccessPolicy(allow=["public.*"]))

    @srv.tool(name="public.ping")
    def ping() -> str:
        """Public."""
        return "pong"

    @srv.tool(name="private.secret")
    def secret() -> str:
        """Private."""
        return "s3cret"

    names = [t["name"] for t in srv.handle(rpc("tools/list"))["result"]["tools"]]
    assert names == ["public.ping"]

    error = srv.handle(rpc("tools/call", {"name": "private.secret"}))["error"]
    assert error["code"] == protocol.ACCESS_DENIED


def test_scoped_tool_requires_the_scope():
    policy = AccessPolicy()
    srv = MCPServer("t", policy=policy)

    @srv.tool(scopes=["orders:write"])
    def cancel_order(order_id: str) -> str:
        """Cancel an order."""
        return "cancelled"

    call = rpc("tools/call", {"name": "cancel_order", "arguments": {"order_id": "1"}})
    assert srv.handle(call)["error"]["code"] == protocol.ACCESS_DENIED

    srv.policy = policy.with_scopes("orders:write")
    assert srv.handle(call)["result"]["isError"] is False


def test_rate_limiter_blocks_after_burst():
    clock = iter([0.0] * 20)
    srv = MCPServer("t", rate_limiter=RateLimiter(rate=1, burst=2, clock=lambda: next(clock)))

    @srv.tool()
    def ping() -> str:
        """Ping."""
        return "pong"

    assert srv.handle(rpc("tools/call", {"name": "ping"}))["result"]["isError"] is False
    assert srv.handle(rpc("tools/call", {"name": "ping"}))["result"]["isError"] is False
    error = srv.handle(rpc("tools/call", {"name": "ping"}))["error"]
    assert error["code"] == protocol.RATE_LIMITED
    assert error["data"]["retry_after_seconds"] > 0


def test_results_are_redacted(server):
    @server.tool()
    def contact() -> str:
        """Return a contact."""
        return "escalate to oncall@corp.example"

    result = server.handle(rpc("tools/call", {"name": "contact"}))["result"]
    assert result["content"][0]["text"] == "escalate to [redacted:email]"


def test_notifications_get_no_response(server):
    assert server.handle(rpc("notifications/initialized", request_id=None)) is None


def test_malformed_envelope_is_rejected(server):
    error = server.handle({"jsonrpc": "1.0", "method": "ping", "id": 3})["error"]
    assert error["code"] == protocol.INVALID_REQUEST


def test_unknown_method(server):
    error = server.handle(rpc("does/not/exist"))["error"]
    assert error["code"] == protocol.METHOD_NOT_FOUND


def test_resources_and_prompts_round_trip():
    srv = MCPServer("t")

    @srv.resource("config://limits", description="Current limits.", mime_type="application/json")
    def limits():
        return {"max_rows": 200}

    @srv.prompt(arguments=[{"name": "topic", "required": True}])
    def summarize(topic: str) -> str:
        """Summarize a topic."""
        return f"Summarize {topic} in three bullets."

    listed = srv.handle(rpc("resources/list"))["result"]["resources"]
    assert listed[0]["uri"] == "config://limits"

    contents = srv.handle(rpc("resources/read", {"uri": "config://limits"}))["result"]["contents"]
    assert json.loads(contents[0]["text"]) == {"max_rows": 200}

    get = rpc("prompts/get", {"name": "summarize", "arguments": {"topic": "churn"}})
    prompt = srv.handle(get)["result"]
    assert "churn" in prompt["messages"][0]["content"]["text"]

    caps = srv.handle(rpc("initialize", {}))["result"]["capabilities"]
    assert "resources" in caps and "prompts" in caps


def test_run_pumps_a_transport(server):
    transport = MemoryTransport([rpc("ping"), rpc("tools/list", request_id=2)])
    server.run(transport)
    assert [m["id"] for m in transport.sent] == [1, 2]


def test_duplicate_tool_name_is_rejected(server):
    with pytest.raises(ValueError, match="already registered"):
        server.tool(name="add")(lambda: None)
