"""End-to-end: talk to a real server process over stdin/stdout."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def session(root: Path, messages: list[dict]) -> list[dict]:
    payload = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolkit", "--root", str(root)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "README.md").write_text("# demo\ncontact ops@corp.example\n")
    return tmp_path


def test_full_handshake_and_tool_call(workspace):
    replies = session(
        workspace,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "read_file", "arguments": {"path": "README.md"}}},
        ],
    )

    # The notification produced no reply, so three responses for four messages.
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert replies[0]["result"]["serverInfo"]["name"] == "mcp-toolkit-demo"
    assert {t["name"] for t in replies[1]["result"]["tools"]} == {
        "list_files", "read_file", "search"
    }
    body = replies[2]["result"]["content"][0]["text"]
    assert "# demo" in body
    assert "[redacted:email]" in body


def test_unparseable_line_does_not_kill_the_session(workspace):
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_toolkit", "--root", str(workspace)],
        input='not json\n{"jsonrpc": "2.0", "id": 7, "method": "ping"}\n',
        capture_output=True,
        text=True,
        timeout=30,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout.strip())["id"] == 7
    assert "dropped unparseable line" in proc.stderr
