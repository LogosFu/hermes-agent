"""Tests for the dashboard memory-file read/edit API (/api/memory/files)."""

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.projects_db import connect, create_project
from hermes_constants import get_hermes_home


def _client_with_app_state():
    prev_auth_required = getattr(web_server.app.state, "auth_required", None)
    prev_bound_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.auth_required = False
    web_server.app.state.bound_host = None

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    return client, prev_auth_required, prev_bound_host


def _restore_app_state(prev_auth_required, prev_bound_host):
    if prev_auth_required is None:
        delattr(web_server.app.state, "auth_required")
    else:
        web_server.app.state.auth_required = prev_auth_required
    if prev_bound_host is None:
        if hasattr(web_server.app.state, "bound_host"):
            delattr(web_server.app.state, "bound_host")
    else:
        web_server.app.state.bound_host = prev_bound_host


@pytest.fixture
def memory_client():
    client, prev_auth_required, prev_bound_host = _client_with_app_state()
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()
        _restore_app_state(prev_auth_required, prev_bound_host)


def _make_project(tmp_path, name):
    with connect() as conn:
        return create_project(conn, name=name, primary_path=str(tmp_path / name))


def _mem_dir():
    return get_hermes_home() / "memories"


def test_get_lists_common_and_project_scopes(memory_client, tmp_path):
    pid = _make_project(tmp_path, "Alpha")
    pid_empty = _make_project(tmp_path, "Beta")

    mem_dir = _mem_dir()
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text("hello memory", encoding="utf-8")
    project_dir = mem_dir / "projects" / pid
    project_dir.mkdir(parents=True)
    (project_dir / "MEMORY.md").write_text("project note", encoding="utf-8")

    resp = memory_client.get("/api/memory/files")
    assert resp.status_code == 200
    files = resp.json()["files"]
    by_key = {(f["scope"], f.get("project_id")): f for f in files}

    common = by_key[("memory", None)]
    assert common["chars"] == len("hello memory")
    assert common["limit"] == 2200
    assert common["content"] == "hello memory"

    user = by_key[("user", None)]
    assert user["limit"] == 1375
    assert user["chars"] == 0
    assert user["content"] == ""

    project = by_key[("project", pid)]
    assert project["project_name"] == "Alpha"
    assert project["chars"] == len("project note")
    assert project["content"] == "project note"

    # A registered project with no memory file yet is still listed, empty.
    empty = by_key[("project", pid_empty)]
    assert empty["project_name"] == "Beta"
    assert empty["chars"] == 0
    assert empty["content"] == ""


def test_put_saves_content_and_reports_usage(memory_client):
    resp = memory_client.put(
        "/api/memory/files", json={"scope": "memory", "content": "  new notes  "}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    # chars follows the memory tool's metric: stripped, parsed entries.
    assert body["chars"] == len("new notes")
    assert body["limit"] == 2200
    # Content is stored verbatim (the writer does not strip).
    saved = (_mem_dir() / "MEMORY.md").read_text(encoding="utf-8")
    assert saved == "  new notes  "


def test_put_rejects_over_limit_content(memory_client):
    resp = memory_client.put(
        "/api/memory/files", json={"scope": "user", "content": "x" * 1400}
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "1400" in detail
    assert "1375" in detail
    assert not (_mem_dir() / "USER.md").exists()


def test_put_rejects_invalid_scope(memory_client):
    resp = memory_client.put(
        "/api/memory/files", json={"scope": "../../etc", "content": "x"}
    )
    assert resp.status_code == 400


def test_put_rejects_unknown_project_id(memory_client):
    resp = memory_client.put(
        "/api/memory/files",
        json={"scope": "project", "project_id": "../escape", "content": "x"},
    )
    assert resp.status_code == 400
    assert "Unknown project_id" in resp.json()["detail"]
    # Nothing may be written for an unvalidated id (path traversal guard).
    assert not (_mem_dir() / "projects").exists()


def test_put_project_creates_directory_and_file(memory_client, tmp_path):
    pid = _make_project(tmp_path, "Gamma")
    resp = memory_client.put(
        "/api/memory/files",
        json={"scope": "project", "project_id": pid, "content": "proj memory"},
    )
    assert resp.status_code == 200
    assert resp.json()["chars"] == len("proj memory")
    saved = _mem_dir() / "projects" / pid / "MEMORY.md"
    assert saved.read_text(encoding="utf-8") == "proj memory"

    # The write then round-trips through GET.
    resp = memory_client.get("/api/memory/files")
    files = resp.json()["files"]
    project = next(f for f in files if f.get("project_id") == pid)
    assert project["content"] == "proj memory"
    assert project["chars"] == len("proj memory")
