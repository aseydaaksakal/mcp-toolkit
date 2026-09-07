"""The smallest useful server: three tools, a resource, a prompt.

    python examples/quickstart.py

Then paste a JSON-RPC line on stdin, for example:

    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from mcp_toolkit import AccessPolicy, MCPServer, RateLimiter, ToolError

ORDERS = {
    "ORD-8812": {"status": "shipped", "total": 42.50, "customer": "ops@corp.example"},
    "ORD-8813": {"status": "processing", "total": 11.00, "customer": "sre@corp.example"},
}

server = MCPServer(
    "orders-demo",
    version="0.1.0",
    policy=AccessPolicy(deny=["*.delete"]),
    rate_limiter=RateLimiter(rate=5, burst=20),
    instructions="Read-only order lookups. Call list_orders first if you need an id.",
)


@server.tool()
def list_orders(status: Literal["shipped", "processing", "any"] = "any") -> list[str]:
    """List known order identifiers.

    Args:
        status: Only return orders in this state. "any" returns everything.
    """
    if status == "any":
        return sorted(ORDERS)
    return sorted(k for k, v in ORDERS.items() if v["status"] == status)


@server.tool(name="orders.lookup")
def lookup_order(order_id: str, include_customer: bool = False) -> dict:
    """Look up one order by its internal identifier.

    Args:
        order_id: Identifier such as "ORD-8812".
        include_customer: Include the customer contact in the result.
    """
    order = ORDERS.get(order_id)
    if order is None:
        # ToolError text reaches the model, so make it actionable.
        raise ToolError(f"no order named {order_id!r}; call list_orders for valid ids")
    if include_customer:
        return {"id": order_id, **order}
    return {"id": order_id, "status": order["status"], "total": order["total"]}


@server.resource("config://limits", description="Operational limits for this server.",
                 mime_type="application/json")
def limits() -> dict:
    """Report the caps a caller should plan around."""
    return {"rate_per_second": 5, "burst": 20, "as_of": date.today().isoformat()}


@server.prompt(arguments=[{"name": "order_id", "required": True}])
def explain_delay(order_id: str) -> str:
    """Draft a customer-facing explanation for a delayed order."""
    return (
        f"Look up {order_id}, then write two sentences the support team can send "
        "to the customer. Do not invent a delivery date."
    )


if __name__ == "__main__":
    server.run()
