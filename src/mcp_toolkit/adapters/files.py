"""Expose a directory to an agent without exposing the filesystem.

Every path is resolved and then checked against the sandbox root, so symlinks
and ``../`` sequences cannot walk out. Reads are size-capped, because a single
large file is enough to blow a context window.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ToolError

TEXT_SUFFIXES = {
    ".txt", ".md", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".log", ".sql", ".py", ".go", ".js", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".xml", ".sh", ".env.example",
}


@dataclass
class FileAdapter:
    """Sandboxed read access to files under *root*.

    Args:
        root: Directory the agent may read. Resolved once at construction.
        include: Glob patterns to expose. Defaults to everything.
        exclude: Glob patterns to hide. Applied after *include*.
        max_bytes: Largest single file returned, in bytes.
        max_files: Largest listing returned.
    """

    root: Path
    include: Iterable[str] = ("*",)
    exclude: Iterable[str] = (".git/*", "*.pem", "*.key", ".env")
    max_bytes: int = 200_000
    max_files: int = 500
    _root: Path = field(init=False)

    def __post_init__(self) -> None:
        self._root = Path(self.root).expanduser().resolve()
        if not self._root.is_dir():
            raise ValueError(f"{self._root} is not a directory")

    # -- path handling ------------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """Resolve *relative* inside the sandbox or refuse."""
        if relative.startswith(("/", "\\")) or ":" in relative[:3]:
            raise ToolError("absolute paths are not allowed")
        candidate = (self._root / relative).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise ToolError("path escapes the exposed directory")
        if not self._visible(candidate):
            raise ToolError(f"{relative!r} is not exposed by this server")
        return candidate

    def _visible(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self._root).as_posix()
        except ValueError:
            return False
        if not any(fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(path.name, p)
                   for p in self.include):
            return False
        return not any(
            fnmatch.fnmatch(relative, p) or fnmatch.fnmatch(path.name, p)
            for p in self.exclude
        )

    # -- tools --------------------------------------------------------------

    def list_files(self, subdirectory: str = "") -> dict[str, Any]:
        """List readable files, recursively.

        Args:
            subdirectory: Restrict the listing to this path, relative to the
                exposed root. Empty means the whole sandbox.
        """
        base = self.resolve(subdirectory) if subdirectory else self._root
        if not base.is_dir():
            raise ToolError(f"{subdirectory!r} is not a directory")

        found: list[dict[str, Any]] = []
        for path in sorted(base.rglob("*")):
            if len(found) >= self.max_files:
                break
            if not path.is_file() or not self._visible(path):
                continue
            found.append(
                {
                    "path": path.relative_to(self._root).as_posix(),
                    "bytes": path.stat().st_size,
                }
            )
        return {"root": self._root.name, "files": found, "count": len(found)}

    def read_file(self, path: str) -> str:
        """Read one text file from the exposed directory.

        Args:
            path: File path relative to the exposed root.
        """
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"{path!r} is not a file")

        size = target.stat().st_size
        if size > self.max_bytes:
            raise ToolError(
                f"{path!r} is {size} bytes, over the {self.max_bytes} byte limit; "
                "read a smaller file or raise max_bytes"
            )
        if target.suffix and target.suffix.lower() not in TEXT_SUFFIXES:
            raise ToolError(f"{target.suffix!r} files are not served as text")
        return target.read_text(encoding="utf-8", errors="replace")

    def search(self, pattern: str, max_matches: int = 50) -> dict[str, Any]:
        """Find lines containing *pattern* across the exposed files.

        Args:
            pattern: Case-insensitive literal substring to look for.
            max_matches: Stop after this many hits.
        """
        needle = pattern.lower()
        if not needle:
            raise ToolError("pattern must not be empty")

        matches: list[dict[str, Any]] = []
        for path in sorted(self._root.rglob("*")):
            if len(matches) >= max_matches:
                break
            if not path.is_file() or not self._visible(path):
                continue
            if path.stat().st_size > self.max_bytes:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    matches.append(
                        {
                            "path": path.relative_to(self._root).as_posix(),
                            "line": number,
                            "text": line.strip()[:300],
                        }
                    )
                    if len(matches) >= max_matches:
                        break
        return {"pattern": pattern, "matches": matches, "count": len(matches)}

    # -- wiring -------------------------------------------------------------

    def register(self, server: Any, prefix: str = "") -> None:
        """Attach ``list_files``, ``read_file`` and ``search`` to *server*."""
        server.add_tool(f"{prefix}list_files", self.list_files)
        server.add_tool(f"{prefix}read_file", self.read_file)
        server.add_tool(f"{prefix}search", self.search)
