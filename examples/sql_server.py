"""Expose a SQLite database read-only.

    python examples/sql_server.py

The allowlist is the important part: the agent can read ``orders`` and
``order_lines`` and nothing else, whatever it asks for.
"""

from __future__ import annotations

import sqlite3
import sys

from mcp_toolkit import AuditLog, MCPServer, RateLimiter, Redactor, SqlAdapter


def seed() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(
        """
        CREATE TABLE orders (id TEXT PRIMARY KEY, customer TEXT, total REAL, status TEXT);
        CREATE TABLE order_lines (order_id TEXT, sku TEXT, qty INTEGER);
        CREATE TABLE api_keys (owner TEXT, secret TEXT);

        INSERT INTO orders VALUES
            ('ORD-1', 'ops@corp.example', 42.5, 'shipped'),
            ('ORD-2', 'sre@corp.example', 11.0, 'processing');
        INSERT INTO order_lines VALUES ('ORD-1', 'SKU-9', 2), ('ORD-2', 'SKU-3', 1);
        INSERT INTO api_keys VALUES ('billing', 'sk-live-do-not-leak-me-1234');
        """
    )
    conn.commit()
    return conn


server = MCPServer(
    "warehouse",
    version="0.1.0",
    # The defaults cover emails and common key formats. Add your own for the
    # identifier shapes only your organisation knows about.
    redactor=Redactor(extra={"internal_customer_id": r"\bCUST-[A-Z0-9]{8}\b"}),
    audit_log=AuditLog(stream=sys.stderr),
    rate_limiter=RateLimiter(rate=2, burst=10),
    instructions=(
        "SQLite warehouse, read-only. Call db.list_tables first. "
        "Bind values with parameters instead of formatting them into the SQL."
    ),
)

server.mount(
    SqlAdapter(
        seed(),
        allowed_tables=["orders", "order_lines"],  # api_keys stays invisible
        max_rows=100,
    ),
    prefix="db.",
)

if __name__ == "__main__":
    server.run()
