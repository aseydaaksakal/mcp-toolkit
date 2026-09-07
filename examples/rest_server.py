"""Front an internal HTTP API with a small, explicit set of tools.

Credentials live in the adapter's headers, never in tool arguments: the model
cannot read them, leak them, or invent a different value for them.
"""

from __future__ import annotations

import os

from mcp_toolkit import MCPServer, RestAdapter

api = RestAdapter(
    base_url=os.environ.get("ORDERS_API", "https://orders.internal"),
    headers={"X-Api-Key": os.environ.get("ORDERS_API_KEY", "")},
    timeout=8.0,
)

api.endpoint(
    "lookup_order",
    "GET",
    "/v1/orders/{order_id}",
    description="Fetch one order by its internal identifier.",
    path_params={"order_id": 'Internal order identifier, e.g. "ORD-8812".'},
    query_params={"expand": 'Comma-separated relations to expand, e.g. "lines".'},
)

api.endpoint(
    "search_orders",
    "GET",
    "/v1/orders",
    description="Search orders by customer email or status.",
    query_params={
        "customer": "Exact customer email address.",
        "status": 'One of "processing", "shipped", "cancelled".',
    },
)

server = MCPServer("orders-api", version="0.1.0")
server.mount(api)

if __name__ == "__main__":
    server.run()
