"""Adapters that turn an existing internal system into MCP tools."""

from .files import FileAdapter
from .rest import Endpoint, RestAdapter
from .sql import SqlAdapter

__all__ = ["FileAdapter", "RestAdapter", "Endpoint", "SqlAdapter"]
