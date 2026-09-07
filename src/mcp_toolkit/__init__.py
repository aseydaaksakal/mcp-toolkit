"""mcp-toolkit: expose internal systems to LLM agents over MCP.

    from mcp_toolkit import MCPServer

    server = MCPServer("internal-tools")

    @server.tool()
    def get_order(order_id: str) -> dict:
        '''Look up an order in the fulfilment database.'''
        return {"id": order_id, "status": "shipped"}

    if __name__ == "__main__":
        server.run()
"""

from .adapters import Endpoint, FileAdapter, RestAdapter, SqlAdapter
from .errors import (
    AccessDenied,
    InvalidParams,
    MCPError,
    MethodNotFound,
    RateLimited,
    ToolError,
)
from .schema import schema_from_signature, validate_arguments
from .security import AccessPolicy, AuditLog, AuditRecord, RateLimiter, Redactor
from .server import MCPServer, Prompt, Resource, Tool
from .transport import MemoryTransport, StdioTransport

__version__ = "0.1.0"

__all__ = [
    "MCPServer",
    "Tool",
    "Resource",
    "Prompt",
    "AccessPolicy",
    "RateLimiter",
    "Redactor",
    "AuditLog",
    "AuditRecord",
    "StdioTransport",
    "MemoryTransport",
    "SqlAdapter",
    "RestAdapter",
    "Endpoint",
    "FileAdapter",
    "MCPError",
    "ToolError",
    "AccessDenied",
    "RateLimited",
    "InvalidParams",
    "MethodNotFound",
    "schema_from_signature",
    "validate_arguments",
    "__version__",
]
