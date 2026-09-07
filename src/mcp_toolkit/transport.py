"""Moving JSON-RPC messages in and out of the server.

MCP's stdio transport is newline-delimited JSON: one object per line, no
Content-Length framing. Nothing else may be written to stdout, so anything
you would normally print belongs on stderr.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from typing import Any, Protocol, TextIO


class Transport(Protocol):
    """Minimum surface the server needs from a transport."""

    def receive(self) -> Iterator[Any]:
        """Yield decoded JSON messages until the peer disconnects."""

    def send(self, message: dict[str, Any]) -> None:
        """Write one JSON message back to the peer."""


class StdioTransport:
    """Newline-delimited JSON over a pair of text streams.

    Args:
        stdin: Where messages are read from. Defaults to ``sys.stdin``.
        stdout: Where responses are written. Defaults to ``sys.stdout``.
    """

    def __init__(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout

    def receive(self) -> Iterator[Any]:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A malformed line has no id, so there is no one to answer.
                # Skip it rather than tearing down a working session.
                print(f"mcp-toolkit: dropped unparseable line: {line[:120]!r}",
                      file=sys.stderr)

    def send(self, message: dict[str, Any]) -> None:
        self.stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        self.stdout.flush()


class MemoryTransport:
    """Feed the server a fixed list of messages and collect its replies.

    Useful in tests and when embedding the dispatcher in a larger process.

    >>> from mcp_toolkit import MCPServer
    >>> transport = MemoryTransport([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    >>> MCPServer("demo").run(transport)
    >>> transport.sent[0]["result"]
    {}
    """

    def __init__(self, messages: list[Any] | None = None) -> None:
        self.messages = list(messages or [])
        self.sent: list[dict[str, Any]] = []

    def receive(self) -> Iterator[Any]:
        while self.messages:
            yield self.messages.pop(0)

    def send(self, message: dict[str, Any]) -> None:
        # Round-trip through JSON so tests catch values that cannot be encoded.
        self.sent.append(json.loads(json.dumps(message, default=str)))
