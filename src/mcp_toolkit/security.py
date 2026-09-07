"""Guardrails for tools that touch internal systems.

Exposing an internal system to an agent means exposing it to whatever ends up
in the model's context, including text the model was asked to summarise. The
pieces here assume the caller is untrusted:

* :class:`AccessPolicy` decides which tools a session may call at all.
* :class:`RateLimiter` bounds how often, per tool.
* :class:`Redactor` scrubs values on the way back out.
* :class:`AuditLog` records what happened, after redaction.
"""

from __future__ import annotations

import fnmatch
import json
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from re import Pattern
from typing import Any, TextIO

from .errors import AccessDenied, RateLimited

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


@dataclass
class AccessPolicy:
    """Allow/deny tool names by glob, optionally gated on granted scopes.

    Deny wins over allow. An empty *allow* means "everything not denied", so
    start from ``AccessPolicy(allow=["reports.*"])`` when you want the
    tighter default.

    >>> policy = AccessPolicy(allow=["orders.*"], deny=["orders.delete"])
    >>> policy.permits("orders.lookup")
    True
    >>> policy.permits("orders.delete")
    False
    """

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    scopes: set[str] = field(default_factory=set)
    required_scopes: dict[str, set[str]] = field(default_factory=dict)

    def permits(self, tool_name: str) -> bool:
        if any(fnmatch.fnmatch(tool_name, pattern) for pattern in self.deny):
            return False
        if self.allow and not any(
            fnmatch.fnmatch(tool_name, pattern) for pattern in self.allow
        ):
            return False
        needed = self.required_scopes.get(tool_name, set())
        return needed <= self.scopes

    def enforce(self, tool_name: str) -> None:
        """Raise :class:`~mcp_toolkit.errors.AccessDenied` if the call is not allowed."""
        if not self.permits(tool_name):
            raise AccessDenied(f"tool {tool_name!r} is not available in this session")

    def with_scopes(self, *scopes: str) -> AccessPolicy:
        """Return a copy that has been granted additional scopes."""
        return AccessPolicy(
            allow=list(self.allow),
            deny=list(self.deny),
            scopes=self.scopes | set(scopes),
            required_scopes=dict(self.required_scopes),
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token bucket, one bucket per tool, shared across threads.

    *rate* is refills per second and *burst* is the bucket size, so
    ``RateLimiter(rate=2, burst=10)`` allows a burst of ten calls and then a
    steady two per second.
    """

    def __init__(
        self,
        rate: float,
        burst: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.burst = float(burst if burst is not None else max(1.0, rate))
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, key: str) -> None:
        """Consume one token for *key* or raise :class:`~mcp_toolkit.errors.RateLimited`."""
        now = self._clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                wait = (1.0 - tokens) / self.rate
                self._buckets[key] = (tokens, now)
                raise RateLimited(
                    f"rate limit reached for {key!r}; retry in {wait:.1f}s",
                    data={"retry_after_seconds": round(wait, 3)},
                )
            self._buckets[key] = (tokens - 1.0, now)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS: dict[str, Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "bearer_token": re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{16,}"),
    "api_key": re.compile(r"\b(?:sk|pk|rk|ghp|gho|xox[baprs])[-_][A-Za-z0-9]{16,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
}


class Redactor:
    """Replace secret-looking substrings before they reach the model.

    A safety net, not a guarantee: it does not know your internal identifier
    formats. Add patterns for those.

    >>> Redactor().scrub("write to ops@example.com")
    'write to [redacted:email]'
    """

    def __init__(
        self,
        patterns: dict[str, Pattern[str]] | None = None,
        *,
        extra: dict[str, str | Pattern[str]] | None = None,
        placeholder: str = "[redacted:{name}]",
        enabled: bool = True,
    ) -> None:
        self.patterns = dict(DEFAULT_PATTERNS if patterns is None else patterns)
        for name, pattern in (extra or {}).items():
            self.patterns[name] = (
                re.compile(pattern) if isinstance(pattern, str) else pattern
            )
        self.placeholder = placeholder
        self.enabled = enabled

    def scrub(self, value: Any) -> Any:
        """Walk *value* recursively, redacting every string it contains."""
        if not self.enabled:
            return value
        if isinstance(value, str):
            return self._scrub_text(value)
        if isinstance(value, dict):
            return {key: self.scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub(item) for item in value]
        return value

    def _scrub_text(self, text: str) -> str:
        for name, pattern in self.patterns.items():
            text = pattern.sub(self.placeholder.format(name=name), text)
        return text


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AuditRecord:
    """One tool invocation, as written to the audit log."""

    tool: str
    ok: bool
    duration_ms: float
    arguments: dict[str, Any]
    error: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": round(self.timestamp, 3),
                "tool": self.tool,
                "ok": self.ok,
                "duration_ms": round(self.duration_ms, 2),
                "arguments": self.arguments,
                "error": self.error,
            },
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )


class AuditLog:
    """Append one JSON object per call to a stream.

    Arguments pass through *redactor* first, so the log is safe to ship to a
    normal log pipeline. Writes go to stderr by default because stdout is the
    protocol channel.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        redactor: Redactor | None = None,
        enabled: bool = True,
    ) -> None:
        self.stream = stream
        self.redactor = redactor or Redactor()
        self.enabled = enabled
        self.records: list[AuditRecord] = []
        self._lock = threading.Lock()

    def write(self, record: AuditRecord) -> None:
        if not self.enabled:
            return
        record.arguments = self.redactor.scrub(record.arguments)
        if record.error:
            record.error = self.redactor.scrub(record.error)
        with self._lock:
            self.records.append(record)
            if self.stream is not None:
                self.stream.write(record.to_json() + "\n")
                self.stream.flush()

    def entries(self, tool: str | None = None) -> Iterable[AuditRecord]:
        """Iterate over in-memory records, optionally filtered by tool name."""
        for record in self.records:
            if tool is None or record.tool == tool:
                yield record
