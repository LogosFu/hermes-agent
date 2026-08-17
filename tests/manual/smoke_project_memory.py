"""Smoke: real agent init with temp HERMES_HOME + projects.db; both memory blocks in prompt.

Ad-hoc script (not a pytest). Run from anywhere:
    uv run python tests/manual/smoke_project_memory.py
"""
import os
import pathlib
import sys
import tempfile

# When run as a file, sys.path[0] is this directory and ``import run_agent``
# would resolve to the managed install (~/.hermes/hermes-agent), not this
# checkout. Pin the repo root so the smoke exercises THIS source tree.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

home = pathlib.Path(tempfile.mkdtemp(prefix="hermes-smoke-"))
os.environ["HERMES_HOME"] = str(home)
os.environ.pop("TERMINAL_CWD", None)

# Public layer (shared MEMORY.md) — the existing behavior.
memdir = home / "memories"
memdir.mkdir(parents=True)
(memdir / "MEMORY.md").write_text("public smoke fact: prefers tea", encoding="utf-8")

# A registered project whose primary_path is /tmp/fakeproj.
projdir = pathlib.Path("/tmp/fakeproj")
projdir.mkdir(exist_ok=True)

from hermes_cli import projects_db

with projects_db.connect_closing() as conn:
    pid = projects_db.create_project(conn, name="Smoke Project", primary_path=str(projdir))

# Per-project memory file (pre-seeded so the block renders non-empty).
(memdir / "projects" / pid).mkdir(parents=True, exist_ok=True)
(memdir / "projects" / pid / "MEMORY.md").write_text(
    "project smoke fact: builds with make", encoding="utf-8"
)

# Pin the session cwd the way the TUI/gateway does, then build a real agent.
from agent.runtime_cwd import set_session_cwd

_cwd_token = set_session_cwd(str(projdir))

from run_agent import AIAgent

agent = AIAgent(
    quiet_mode=True,
    platform="cli",
    skip_background_review=True,
    load_soul_identity=False,
    skip_context_files=True,
    enabled_toolsets=["memory"],
    # Upstream now fails fast when no provider is configured; explicit
    # creds route around the config lookup. No network call happens at
    # client construction — the smoke never talks to a model.
    api_key="smoke",
    base_url="http://127.0.0.1:9/v1",
    model="smoke-model",
)
assert agent._memory_store is not None, "memory store missing"
assert agent._project_memory_enabled is True
assert agent._memory_store.project_key == pid, (
    agent._memory_store.project_key,
    pid,
)
assert agent._memory_store.project_name == "Smoke Project"

from agent.system_prompt import build_system_prompt_parts

parts = build_system_prompt_parts(agent)
vol = parts["volatile"]

assert "MEMORY (your personal notes)" in vol, "public block missing"
assert "public smoke fact: prefers tea" in vol, "public entry missing"
assert "PROJECT MEMORY (Smoke Project)" in vol, "project block missing"
assert "project smoke fact: builds with make" in vol, "project entry missing"

print("== SMOKE OK ==")
print("project_key:", agent._memory_store.project_key)
print("project_name:", agent._memory_store.project_name)
for line in vol.splitlines():
    if "MEMORY" in line or "PROJECT MEMORY" in line or "smoke fact" in line:
        print(" |", line)

# Frozen-snapshot discipline: a mid-session write must not change this session's
# prompt block.
_before = agent._memory_store.format_for_system_prompt("project")
agent._memory_store.add("project", "written after init")
assert agent._memory_store.format_for_system_prompt("project") == _before
print("== SNAPSHOT FROZEN OK ==")

# No-project session: target=project refuses with the friendly error.
import json

from tools.memory_tool import MemoryStore, memory_tool

_nostore = MemoryStore()
_nostore.load_from_disk()
_out = json.loads(memory_tool("add", "project", "fact", store=_nostore))
assert _out["success"] is False
assert "not associated with a project" in _out["error"]
print("== NO-PROJECT ERROR OK ==", _out["error"])
