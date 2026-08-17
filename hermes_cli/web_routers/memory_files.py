"""Memory file endpoints — read/edit the built-in memory files directly.

Covers the global MEMORY.md / USER.md plus one MEMORY.md per registered
project (memories/projects/<project_id>/MEMORY.md).  Editing here only
affects NEW sessions: a running session's system prompt is a frozen
snapshot by design.

Extracted shape follows the sibling routers: helpers/state that tests
monkeypatch on their owning modules are late-bound (cycle-safe).
"""

import asyncio
import logging
import os
import tempfile

from fastapi import APIRouter, HTTPException

from hermes_cli.web_deps import late
from hermes_cli.web_models import MemoryFileUpdate

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter()

# Late-bound so a test's monkeypatch on the owning module wins at call time.
get_hermes_home = late("get_hermes_home", "hermes_cli.config")
load_config = late("load_config", "hermes_cli.config")

_MEMORY_FILE_SCOPES = ("memory", "user", "project")


def _memory_char_limits() -> dict:
    """Per-scope char limits from config.yaml ``memory.*``, defaults applied.

    Mirrors tools.memory_tool.load_on_disk_store() so the dashboard enforces
    the SAME caps the memory tool does.
    """
    limits = {"memory": 2200, "user": 1375, "project": 2200}
    try:
        mem_cfg = (load_config() or {}).get("memory", {}) or {}
        limits["memory"] = int(mem_cfg.get("memory_char_limit", limits["memory"]))
        limits["user"] = int(mem_cfg.get("user_char_limit", limits["user"]))
        limits["project"] = int(mem_cfg.get("project_char_limit", limits["project"]))
    except Exception:
        pass  # config optional — fall back to the built-in defaults
    return limits


def _memory_chars(content: str) -> int:
    """Char usage of a memory file, matching MemoryStore._char_count's metric."""
    from tools.memory_tool import ENTRY_DELIMITER, MemoryStore

    entries = MemoryStore._parse_entries(content)
    return len(ENTRY_DELIMITER.join(entries)) if entries else 0


def _memory_file_payload(scope: str, path, limit: int, project=None) -> dict:
    from tools.memory_tool import MemoryStore

    raw, read_ok = MemoryStore._read_raw_checked(path)
    if not read_ok:
        raise HTTPException(status_code=500, detail=f"Memory file is not readable: {path.name}")
    entry = {"scope": scope, "chars": _memory_chars(raw), "limit": limit, "content": raw}
    if project is not None:
        entry["project_id"] = project.id
        entry["project_name"] = (project.name or project.slug or project.id).strip()
    return entry


@router.get("/api/memory/files")
async def get_memory_files():
    # Config load, file reads and the projects DB are disk IO — keep them off
    # the event loop (same pattern as GET /api/memory above).
    def _run():
        from hermes_cli.projects_db import connect_closing, list_projects

        limits = _memory_char_limits()
        mem_dir = get_hermes_home() / "memories"
        files = [
            _memory_file_payload("memory", mem_dir / "MEMORY.md", limits["memory"]),
            _memory_file_payload("user", mem_dir / "USER.md", limits["user"]),
        ]
        try:
            with connect_closing() as conn:
                projects = list_projects(conn)  # archived projects excluded
        except Exception:
            _log.exception("GET /api/memory/files: projects DB unavailable")
            projects = []  # the projects DB is optional — never break the page
        for project in projects:
            path = mem_dir / "projects" / project.id / "MEMORY.md"
            files.append(_memory_file_payload("project", path, limits["project"], project))
        return {"files": files}

    return await asyncio.to_thread(_run)


@router.put("/api/memory/files")
async def put_memory_file(body: MemoryFileUpdate):
    scope = (body.scope or "").strip().lower()
    if scope not in _MEMORY_FILE_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be memory, user, or project")

    def _run():
        limits = _memory_char_limits()
        limit = limits[scope]
        mem_dir = get_hermes_home() / "memories"

        if scope == "project":
            project_id = (body.project_id or "").strip()
            if not project_id:
                raise HTTPException(status_code=400, detail="project_id is required for scope=project")
            from hermes_cli.projects_db import connect_closing, get_project

            with connect_closing() as conn:
                project = get_project(conn, project_id)
            if project is None:
                raise HTTPException(status_code=400, detail=f"Unknown project_id: {project_id}")
            # The path is built ONLY from the validated DB row's id — never
            # from the raw request string (path traversal guard).
            path = mem_dir / "projects" / project.id / "MEMORY.md"
        else:
            path = mem_dir / ("USER.md" if scope == "user" else "MEMORY.md")

        chars = _memory_chars(body.content)
        if chars > limit:
            raise HTTPException(
                status_code=400,
                detail=f"Content is {chars} characters, over the {limit} character limit.",
            )

        # Atomic write: temp file in the same directory, then os.replace.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body.content)
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return {"ok": True, "scope": scope, "chars": chars, "limit": limit}

    return await asyncio.to_thread(_run)
