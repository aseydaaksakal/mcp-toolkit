"""Run a demo server: ``python -m mcp_toolkit --root .``

Exposes the given directory over stdio so you can point an MCP client at it
and confirm the wiring before writing any code of your own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import MCPServer, RateLimiter, __version__
from .adapters import FileAdapter
from .security import AuditLog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-toolkit", description=__doc__)
    parser.add_argument("--root", default=".", help="directory to expose (default: .)")
    parser.add_argument("--name", default="mcp-toolkit-demo", help="server name")
    parser.add_argument("--rate", type=float, default=5.0, help="calls per second")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    server = MCPServer(
        args.name,
        version=__version__,
        rate_limiter=RateLimiter(rate=args.rate, burst=20),
        audit_log=AuditLog(stream=sys.stderr),
        instructions="Read-only access to a single directory. Start with list_files.",
    )
    server.mount(FileAdapter(root=Path(args.root)))
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
