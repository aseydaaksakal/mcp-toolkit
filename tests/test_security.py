import io

import pytest

from mcp_toolkit import AccessPolicy, AuditLog, AuditRecord, RateLimiter, Redactor
from mcp_toolkit.errors import AccessDenied, RateLimited


def test_empty_allow_permits_everything_not_denied():
    policy = AccessPolicy(deny=["admin.*"])
    assert policy.permits("orders.lookup")
    assert not policy.permits("admin.reset")


def test_deny_beats_allow():
    policy = AccessPolicy(allow=["orders.*"], deny=["orders.delete"])
    assert policy.permits("orders.lookup")
    assert not policy.permits("orders.delete")


def test_enforce_raises():
    with pytest.raises(AccessDenied, match="not available"):
        AccessPolicy(allow=["a"]).enforce("b")


def test_with_scopes_does_not_mutate_the_original():
    base = AccessPolicy(required_scopes={"cancel": {"write"}})
    granted = base.with_scopes("write")
    assert granted.permits("cancel")
    assert not base.permits("cancel")


def test_rate_limiter_refills_over_time():
    now = [0.0]
    limiter = RateLimiter(rate=10, burst=1, clock=lambda: now[0])
    limiter.check("t")
    with pytest.raises(RateLimited):
        limiter.check("t")
    now[0] = 0.2
    limiter.check("t")


def test_rate_limiter_buckets_are_per_key():
    limiter = RateLimiter(rate=1, burst=1, clock=lambda: 0.0)
    limiter.check("a")
    limiter.check("b")
    with pytest.raises(RateLimited):
        limiter.check("a")


def test_rate_must_be_positive():
    with pytest.raises(ValueError):
        RateLimiter(rate=0)


@pytest.mark.parametrize(
    "text, marker",
    [
        ("mail ops@corp.example now", "[redacted:email]"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "[redacted:bearer_token]"),
        ("key sk-0123456789abcdefghij", "[redacted:api_key]"),
        ("AKIAIOSFODNN7EXAMPLE", "[redacted:aws_access_key]"),
        ("-----BEGIN RSA PRIVATE KEY-----", "[redacted:private_key]"),
    ],
)
def test_default_patterns(text, marker):
    assert marker in Redactor().scrub(text)


def test_redaction_walks_nested_structures():
    scrubbed = Redactor().scrub({"a": ["x@y.zz", {"b": "p@q.rr"}], "n": 5})
    assert scrubbed == {"a": ["[redacted:email]", {"b": "[redacted:email]"}], "n": 5}


def test_extra_patterns_and_disabling():
    redactor = Redactor(extra={"employee_id": r"EMP-\d{5}"})
    assert redactor.scrub("EMP-12345") == "[redacted:employee_id]"
    assert Redactor(enabled=False).scrub("a@b.cc") == "a@b.cc"


def test_audit_log_writes_redacted_jsonl():
    stream = io.StringIO()
    log = AuditLog(stream=stream)
    log.write(AuditRecord(tool="t", ok=True, duration_ms=1.234, arguments={"e": "a@b.cc"}))
    line = stream.getvalue().strip()
    assert '"[redacted:email]"' in line
    assert '"tool": "t"' in line
    assert list(log.entries("t"))[0].ok is True


def test_disabled_audit_log_records_nothing():
    log = AuditLog(enabled=False)
    log.write(AuditRecord(tool="t", ok=True, duration_ms=0.0, arguments={}))
    assert log.records == []
