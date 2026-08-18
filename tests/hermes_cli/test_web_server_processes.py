"""Tests for the dashboard background-process monitor API (/api/processes)."""

import time

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.web_routers import processes as processes_routes
from hermes_cli.projects_db import connect, create_project
from tools import process_registry as registry_module
from tools.process_registry import ProcessSession


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
def processes_client(monkeypatch):
    """TestClient with an isolated registry and an empty title cache."""
    monkeypatch.setattr(registry_module.process_registry, "_running", {})
    monkeypatch.setattr(registry_module.process_registry, "_finished", {})
    monkeypatch.setattr(processes_routes, "_process_title_cache", {})

    client, prev_auth_required, prev_bound_host = _client_with_app_state()
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            close()
        _restore_app_state(prev_auth_required, prev_bound_host)


def _register(session, *, finished=False):
    target = (
        registry_module.process_registry._finished
        if finished
        else registry_module.process_registry._running
    )
    target[session.id] = session


def test_list_running_first_with_details(processes_client, tmp_path):
    brief = tmp_path / "brief.md"
    brief.write_text("# 任务书", encoding="utf-8")

    project_dir = tmp_path / "Alpha"
    project_dir.mkdir()
    with connect() as conn:
        pid = create_project(conn, name="Alpha", primary_path=str(project_dir))

    running = ProcessSession(
        id="proc_run",
        command=f"kimi --prompt @{brief}",
        cwd=str(project_dir),
        started_at=time.time() - 65,
        output_buffer="\x1b[31mhello\x1b[0m world",
    )
    finished = ProcessSession(
        id="proc_done",
        command="echo done",
        cwd=str(tmp_path),
        started_at=time.time() - 3600,
        exited=True,
        exit_code=2,
        output_buffer="bye",
    )
    _register(running)
    _register(finished, finished=True)

    resp = processes_client.get("/api/processes")
    assert resp.status_code == 200
    rows = resp.json()["processes"]
    assert [r["id"] for r in rows] == ["proc_run", "proc_done"]

    run_row = rows[0]
    assert run_row["exited"] is False
    assert run_row["exit_code"] is None
    assert run_row["project_id"] == pid
    assert run_row["project_name"] == "Alpha"
    assert run_row["brief_path"] == str(brief)
    assert run_row["duration_sec"] >= 64
    # ANSI escapes are stripped from the tail preview.
    assert run_row["output_tail"] == "hello world"

    done_row = rows[1]
    assert done_row["exited"] is True
    assert done_row["exit_code"] == 2
    assert done_row["brief_path"] is None
    # A cwd under no registered project resolves to no project, never an error.
    assert done_row["project_id"] is None
    assert done_row["project_name"] is None


def test_list_empty(processes_client):
    resp = processes_client.get("/api/processes")
    assert resp.status_code == 200
    assert resp.json() == {"processes": []}


def test_titles_uses_llm_and_caches(processes_client, monkeypatch):
    calls = []

    def fake_llm(command):
        calls.append(command)
        return "实施记忆管理界面"

    monkeypatch.setattr(processes_routes, "_process_llm_title", fake_llm)

    body = {"items": [{"id": "proc_a", "command": "kimi --prompt @/tmp/x.md"}]}
    resp = processes_client.post("/api/processes/titles", json=body)
    assert resp.status_code == 200
    assert resp.json()["titles"] == {"proc_a": "实施记忆管理界面"}
    assert calls == ["kimi --prompt @/tmp/x.md"]

    # Second request for the same command is a cache hit — no new LLM call.
    resp = processes_client.post(
        "/api/processes/titles",
        json={"items": [{"id": "proc_b", "command": "kimi --prompt @/tmp/x.md"}]},
    )
    assert resp.json()["titles"] == {"proc_b": "实施记忆管理界面"}
    assert calls == ["kimi --prompt @/tmp/x.md"]


def test_titles_fall_back_when_llm_fails(processes_client, monkeypatch, tmp_path):
    monkeypatch.setattr(processes_routes, "_process_llm_title", lambda command: None)
    brief = tmp_path / "kimi-hermes-tasks-ui.md"
    brief.write_text("# x", encoding="utf-8")

    long_command = "omp run " + "x" * 100
    resp = processes_client.post(
        "/api/processes/titles",
        json={
            "items": [
                {"id": "proc_brief", "command": f"kimi --prompt @{brief}"},
                {"id": "proc_cmd", "command": long_command},
                {"id": "proc_empty", "command": "   "},
            ]
        },
    )
    assert resp.status_code == 200
    titles = resp.json()["titles"]
    # Brief filename (without extension) wins over the raw command slice.
    assert titles["proc_brief"] == "kimi-hermes-tasks-ui"
    assert titles["proc_cmd"] == long_command[:30]
    assert titles["proc_empty"] == "—"


def test_brief_returns_file_content(processes_client, tmp_path):
    brief = tmp_path / "task.md"
    brief.write_text("# 任务书\n做点什么", encoding="utf-8")
    _register(ProcessSession(id="proc_a", command=f"kimi @{brief}", started_at=time.time()))

    resp = processes_client.get("/api/processes/proc_a/brief")
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == str(brief)
    assert body["content"] == "# 任务书\n做点什么"


def test_brief_404_without_brief_and_ignores_client_path(processes_client):
    _register(ProcessSession(id="proc_a", command="echo hi", started_at=time.time()))

    # The endpoint derives the path from the tracked command only — a
    # client-supplied path must not be honored (arbitrary-read guard).
    resp = processes_client.get("/api/processes/proc_a/brief", params={"path": "/etc/passwd"})
    assert resp.status_code == 404
    assert resp.text.find("root:") == -1


def test_brief_404_for_missing_file(processes_client, tmp_path):
    _register(
        ProcessSession(
            id="proc_a",
            command=f"kimi @{tmp_path / 'gone.md'}",
            started_at=time.time(),
        )
    )
    resp = processes_client.get("/api/processes/proc_a/brief")
    assert resp.status_code == 404


def test_brief_404_for_unknown_process(processes_client):
    resp = processes_client.get("/api/processes/proc_nope/brief")
    assert resp.status_code == 404
