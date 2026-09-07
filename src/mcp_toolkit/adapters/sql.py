"""Expose a relational database to an agent, read-only.

Works with any PEP 249 connection (sqlite3, psycopg, mysqlclient, ...). The
guard is deliberately conservative: single statement, ``SELECT``/``WITH``
only, no comments, optional table allowlist, mandatory row cap. A model that
asks for something outside those bounds gets a clear refusal it can act on
rather than a database error.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import ToolError

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|"
    r"ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|REPLACE|MERGE|CALL|EXEC|COPY)\b",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"(--|/\*|\*/|#)")
_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\[]?[\w.]+[`\"\]]?)", re.IGNORECASE)
_STARTS_READONLY = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


class Connection(Protocol):
    """The slice of PEP 249 this adapter uses."""

    def cursor(self) -> Any: ...


@dataclass
class SqlAdapter:
    """Register a guarded ``query`` tool plus a schema resource.

    Args:
        connection: An open PEP 249 connection.
        allowed_tables: If set, every table referenced must appear here.
        max_rows: Hard cap on returned rows.
        description: Overrides the tool description shown to the agent.
        dialect_placeholder: Parameter style of your driver (``?`` or ``%s``).

    >>> import sqlite3
    >>> conn = sqlite3.connect(":memory:")
    >>> _ = conn.execute("CREATE TABLE orders (id TEXT, total REAL)")
    >>> _ = conn.execute("INSERT INTO orders VALUES ('ORD-1', 42.0)")
    >>> adapter = SqlAdapter(conn, allowed_tables=["orders"])
    >>> adapter.query("SELECT id FROM orders")["rows"]
    [{'id': 'ORD-1'}]
    """

    connection: Connection
    allowed_tables: Iterable[str] | None = None
    max_rows: int = 200
    description: str | None = None
    dialect_placeholder: str = "?"
    _allowed: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self._allowed = {t.lower() for t in (self.allowed_tables or [])}

    # -- guard --------------------------------------------------------------

    def check(self, sql: str) -> None:
        """Raise :class:`~mcp_toolkit.errors.ToolError` if *sql* is not a safe read."""
        stripped = sql.strip().rstrip(";")
        if not stripped:
            raise ToolError("empty query")
        if ";" in stripped:
            raise ToolError("only one statement per call is allowed")
        if _COMMENT.search(stripped):
            raise ToolError("SQL comments are not allowed")
        if not _STARTS_READONLY.match(stripped):
            raise ToolError("only SELECT and WITH queries are allowed")
        if _FORBIDDEN.search(stripped):
            raise ToolError("the query contains a write or DDL keyword")
        if self._allowed:
            for raw in _TABLE_REF.findall(stripped):
                table = raw.strip('`"[]').lower()
                if table not in self._allowed:
                    allowed = ", ".join(sorted(self._allowed))
                    raise ToolError(
                        f"table {table!r} is not exposed; available tables: {allowed}"
                    )

    # -- tools --------------------------------------------------------------

    def query(self, sql: str, parameters: list[Any] | None = None) -> dict[str, Any]:
        """Run a read-only SQL query and return the rows.

        Args:
            sql: A single SELECT (or WITH ... SELECT) statement.
            parameters: Values for placeholders in the statement. Always use
                these instead of formatting values into the SQL string.
        """
        self.check(sql)
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(parameters or ()))
            columns = [d[0] for d in (cursor.description or [])]
            fetched = cursor.fetchmany(self.max_rows + 1)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError(f"query failed: {type(exc).__name__}") from exc
        finally:
            with contextlib.suppress(Exception):  # driver cleanup is best-effort
                cursor.close()

        truncated = len(fetched) > self.max_rows
        rows = [dict(zip(columns, row, strict=False)) for row in fetched[: self.max_rows]]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    def list_tables(self) -> list[str]:
        """List the tables this server is allowed to read."""
        if self._allowed:
            return sorted(self._allowed)
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as exc:
            raise ToolError(
                "cannot list tables on this driver; pass allowed_tables explicitly"
            ) from exc
        finally:
            with contextlib.suppress(Exception):
                cursor.close()

    # -- wiring -------------------------------------------------------------

    def register(self, server: Any, prefix: str = "") -> None:
        """Attach ``{prefix}query`` and ``{prefix}list_tables`` to *server*."""
        tables = ", ".join(sorted(self._allowed)) if self._allowed else "the database"
        server.add_tool(
            f"{prefix}query",
            self.query,
            description=self.description
            or f"Run a read-only SQL query against {tables}. "
            f"Returns at most {self.max_rows} rows.",
        )
        server.add_tool(
            f"{prefix}list_tables",
            self.list_tables,
            description="List the tables available to this server.",
        )
