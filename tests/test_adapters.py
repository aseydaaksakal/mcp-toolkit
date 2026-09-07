import io
import json
import sqlite3

import pytest

from mcp_toolkit import FileAdapter, MCPServer, RestAdapter, SqlAdapter
from mcp_toolkit.errors import ToolError

# -- SQL --------------------------------------------------------------------


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE orders (id TEXT, total REAL)")
    conn.execute("CREATE TABLE secrets (token TEXT)")
    conn.executemany("INSERT INTO orders VALUES (?, ?)", [("ORD-1", 10.0), ("ORD-2", 20.0)])
    conn.execute("INSERT INTO secrets VALUES ('sk-live-xyz')")
    conn.commit()
    return conn


def test_select_returns_rows(db):
    out = SqlAdapter(db).query("SELECT id, total FROM orders ORDER BY id")
    assert out["columns"] == ["id", "total"]
    assert out["rows"][0] == {"id": "ORD-1", "total": 10.0}
    assert out["truncated"] is False


def test_parameters_are_bound_not_interpolated(db):
    out = SqlAdapter(db).query("SELECT id FROM orders WHERE id = ?", ["ORD-2"])
    assert out["rows"] == [{"id": "ORD-2"}]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "SELECT 1 -- comment",
        "UPDATE orders SET total = 0",
        "PRAGMA table_info(orders)",
        "",
    ],
)
def test_guard_rejects_unsafe_sql(db, sql):
    with pytest.raises(ToolError):
        SqlAdapter(db).query(sql)


def test_table_allowlist(db):
    adapter = SqlAdapter(db, allowed_tables=["orders"])
    adapter.query("SELECT id FROM orders")
    with pytest.raises(ToolError, match="not exposed"):
        adapter.query("SELECT token FROM secrets")


def test_with_clause_is_allowed(db):
    out = SqlAdapter(db).query("WITH t AS (SELECT id FROM orders) SELECT * FROM t")
    assert out["row_count"] == 2


def test_row_cap_sets_truncated(db):
    out = SqlAdapter(db, max_rows=1).query("SELECT id FROM orders")
    assert out["row_count"] == 1 and out["truncated"] is True


def test_sql_adapter_registers_tools(db):
    server = MCPServer("t").mount(SqlAdapter(db, allowed_tables=["orders"]), prefix="db.")
    assert set(server.tools) == {"db.query", "db.list_tables"}
    assert "orders" in server.tools["db.query"].description


# -- REST -------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, payload, content_type="application/json"):
        super().__init__(json.dumps(payload).encode())
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_rest_builds_url_and_returns_json():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = request.headers
        return FakeResponse({"id": "ORD-1"})

    adapter = RestAdapter("https://api.internal", headers={"X-Api-Key": "k"}, opener=opener)
    endpoint = adapter.endpoint(
        "lookup",
        "GET",
        "/orders/{order_id}",
        description="Look up an order.",
        path_params={"order_id": "Order id."},
        query_params={"expand": "Fields to expand."},
    )
    assert adapter.call(endpoint, {"order_id": "ORD-1", "expand": "lines"}) == {"id": "ORD-1"}
    assert seen["url"] == "https://api.internal/orders/ORD-1?expand=lines"
    assert seen["headers"]["X-api-key"] == "k"


def test_path_parameters_cannot_escape_the_declared_path():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse({})

    adapter = RestAdapter("https://api.internal", opener=opener)
    endpoint = adapter.endpoint(
        "lookup", "GET", "/orders/{order_id}", description="d", path_params={"order_id": "id"}
    )
    adapter.call(endpoint, {"order_id": "../admin/keys"})
    assert seen["url"] == "https://api.internal/orders/..%2Fadmin%2Fkeys"


def test_write_methods_are_opt_in():
    adapter = RestAdapter("https://api.internal")
    with pytest.raises(ValueError, match="allow_write_methods"):
        adapter.endpoint("create", "POST", "/orders", description="d")

    writable = RestAdapter("https://api.internal", allow_write_methods=True)
    writable.endpoint("create", "POST", "/orders", description="d")


def test_rest_endpoint_schema_and_registration():
    adapter = RestAdapter("https://api.internal", opener=lambda r, t: FakeResponse({}))
    adapter.endpoint(
        "lookup", "GET", "/o/{id}", description="Look it up.", path_params={"id": "The id."}
    )
    server = MCPServer("t").mount(adapter)
    schema = server.tools["lookup"].input_schema
    assert schema["required"] == ["id"]
    assert schema["properties"]["id"]["description"] == "The id."


# -- Files ------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "runbook.md").write_text("Restart the queue worker.\n")
    (tmp_path / "notes.txt").write_text("second line mentions queue\n")
    (tmp_path / "id_rsa.pem").write_text("secret")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("should not be reachable")
    return tmp_path


def test_listing_hides_excluded_files(sandbox):
    files = FileAdapter(root=sandbox).list_files()["files"]
    paths = {f["path"] for f in files}
    assert paths == {"docs/runbook.md", "notes.txt"}


def test_read_file(sandbox):
    assert "queue worker" in FileAdapter(root=sandbox).read_file("docs/runbook.md")


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "id_rsa.pem"])
def test_paths_outside_the_sandbox_are_refused(sandbox, path):
    with pytest.raises(ToolError):
        FileAdapter(root=sandbox).read_file(path)


def test_size_cap(sandbox):
    with pytest.raises(ToolError, match="over the"):
        FileAdapter(root=sandbox, max_bytes=5).read_file("notes.txt")


def test_search_reports_path_and_line(sandbox):
    hits = FileAdapter(root=sandbox).search("queue")["matches"]
    assert {h["path"] for h in hits} == {"docs/runbook.md", "notes.txt"}
    assert all(h["line"] == 1 for h in hits)


def test_include_pattern_narrows_the_sandbox(sandbox):
    adapter = FileAdapter(root=sandbox, include=["*.md"])
    assert [f["path"] for f in adapter.list_files()["files"]] == ["docs/runbook.md"]


def test_file_adapter_registers_three_tools(sandbox):
    server = MCPServer("t").mount(FileAdapter(root=sandbox), prefix="fs.")
    assert set(server.tools) == {"fs.list_files", "fs.read_file", "fs.search"}


def test_root_must_be_a_directory(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(ValueError):
        FileAdapter(root=f)
