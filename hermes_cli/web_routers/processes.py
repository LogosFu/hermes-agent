"""Background-process endpoints — a read-only monitor over the processes
tracked by tools.process_registry (terminal background=true spawns,
delegated runs, ...). The desktop Tasks page polls these.

READ-ONLY on purpose: no kill, no stdin — a control surface is a separate
decision. The registry itself is never mutated here.

Extracted shape follows the sibling routers: helpers/state that tests
monkeypatch on their owning modules are late-bound (cycle-safe).
"""

import asyncio
import hashlib
import logging
import os
import shlex
import time
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException

from hermes_cli.web_models import ProcessTitlesRequest

_log = logging.getLogger("hermes_cli.web_server")
router = APIRouter()

_PROCESS_OUTPUT_TAIL_CHARS = 500
_PROCESS_BRIEF_MAX_CHARS = 200_000
_PROCESS_TITLE_TIMEOUT_SEC = 10
_PROCESS_TITLE_FALLBACK_CHARS = 30
_PROCESS_TITLES_MAX_ITEMS = 50

_PROCESS_TITLE_PROMPT = (
    "把这条 shell 命令概括成一个 15 字以内的中文标题，"
    "说明它在做什么。只输出标题本身，不要标点、不要引号、不要解释。\n\n命令：\n"
)

# command-hash -> title. Process-local on purpose: titles are a rendering
# nicety, and a restart simply re-asks the model once per command. Fallback
# titles are cached too, so a down/unconfigured LLM is paid for once per
# command, not once per poll.
_process_title_cache: Dict[str, str] = {}


def _process_brief_path(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """Extract the ``@file`` task-brief path from a process command.

    Agent-spawned commands reference their brief as an ``@/path/to/brief.md``
    token. Returns the expanded path only when it points at an existing
    regular file — anything else (no token, missing file) means "no brief".
    Relative ``@file`` tokens resolve against the process's cwd.
    """
    if not command:
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:  # unbalanced quotes etc. — best-effort split
        tokens = command.split()
    for token in tokens:
        if not token.startswith("@") or len(token) < 2:
            continue
        candidate = os.path.expanduser(token[1:])
        if not os.path.isabs(candidate) and cwd:
            candidate = os.path.join(cwd, candidate)
        if os.path.isfile(candidate):
            return candidate
    return None


def _process_fallback_title(command: str, cwd: Optional[str] = None) -> str:
    """Deterministic title when the LLM is unavailable: the brief's filename,
    else the head of the command."""
    brief_path = _process_brief_path(command, cwd)
    if brief_path:
        return os.path.splitext(os.path.basename(brief_path))[0]
    compact = " ".join((command or "").split())
    return compact[:_PROCESS_TITLE_FALLBACK_CHARS] if compact else "—"


def _process_llm_title(command: str) -> Optional[str]:
    """One cheap-tier call naming a command; None on any failure.

    Runs on the ``title_generation`` auxiliary task (the same small/fast tier
    session titles use). Never raises — the titles endpoint must always answer.
    """
    from agent.auxiliary_client import call_llm

    try:
        response = call_llm(
            task="title_generation",
            messages=[{"role": "user", "content": _PROCESS_TITLE_PROMPT + command[:1000]}],
            max_tokens=32,
            temperature=0.2,
            timeout=_PROCESS_TITLE_TIMEOUT_SEC,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:
        _log.debug("Process title LLM call failed", exc_info=True)
        return None
    # Keep the first real line — a chatty model's extras are dropped, and a
    # blank response degrades to the deterministic fallback.
    title = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    title = title.strip("\"'").strip()
    return title[:40] if title else None


@router.get("/api/processes")
async def list_processes():
    # Registry access and project resolution (projects DB) are off the event
    # loop, same pattern as GET /api/memory/files.
    def _run():
        from tools.ansi_strip import strip_ansi
        from tools.memory_tool import resolve_project_for_cwd
        from tools.process_registry import process_registry

        with process_registry._lock:
            sessions = list(process_registry._running.values()) + list(
                process_registry._finished.values()
            )

        now = time.time()
        rows = []
        for s in sessions:
            # Same refresh list_sessions() applies to crash-recovered
            # (detached) sessions, so a dead detached PID shows as exited.
            s = process_registry._refresh_detached_session(s)
            if s is None:
                continue
            command = s.command or ""
            project = resolve_project_for_cwd(s.cwd)
            rows.append(
                {
                    "id": s.id,
                    "command": command,
                    "cwd": s.cwd,
                    "project_id": project[0] if project else None,
                    "project_name": project[1] if project else None,
                    "started_at": s.started_at,
                    "duration_sec": max(0, int(now - (s.started_at or now))),
                    "exited": bool(s.exited),
                    "exit_code": s.exit_code,
                    "brief_path": _process_brief_path(command, s.cwd),
                    "output_tail": (
                        strip_ansi(s.output_buffer[-_PROCESS_OUTPUT_TAIL_CHARS:])
                        if s.output_buffer
                        else ""
                    ),
                }
            )
        # Running first, finished after; newest first within each group.
        rows.sort(key=lambda r: (r["exited"], -(r["started_at"] or 0)))
        return {"processes": rows}

    return await asyncio.to_thread(_run)


@router.post("/api/processes/titles")
async def process_titles(body: ProcessTitlesRequest):
    items = body.items[:_PROCESS_TITLES_MAX_ITEMS]

    # Cache hits answer immediately; only misses pay an LLM call.
    titles: Dict[str, str] = {}
    misses = []
    for item in items:
        key = hashlib.sha256(item.command.encode("utf-8", "replace")).hexdigest()
        cached = _process_title_cache.get(key)
        if cached is not None:
            titles[item.id] = cached
        else:
            misses.append((item, key))

    async def _one(item, key):
        def _run():
            if item.command.strip():
                title = _process_llm_title(item.command)
                if title is not None:
                    return title
            return _process_fallback_title(item.command)

        title = await asyncio.to_thread(_run)
        _process_title_cache[key] = title
        return item.id, title

    if misses:
        # One LLM call per miss, concurrently, each bounded by its own
        # timeout — a slow/absent model delays only this endpoint, never the
        # process list.
        for proc_id, title in await asyncio.gather(*(_one(i, k) for i, k in misses)):
            titles[proc_id] = title
    return {"titles": titles}


@router.get("/api/processes/{proc_id}/brief")
async def get_process_brief(proc_id: str):
    def _run():
        from tools.process_registry import process_registry

        with process_registry._lock:
            session = process_registry._running.get(proc_id) or process_registry._finished.get(
                proc_id
            )
        if session is None:
            raise HTTPException(status_code=404, detail="Unknown process id")
        # The brief path is derived HERE from the tracked process's own
        # command — the client can ask for a process's brief, never for an
        # arbitrary path (this endpoint takes no path parameter at all).
        brief_path = _process_brief_path(session.command or "", session.cwd)
        if not brief_path:
            raise HTTPException(status_code=404, detail="Process has no task brief")
        try:
            with open(brief_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read(_PROCESS_BRIEF_MAX_CHARS + 1)
        except OSError:
            raise HTTPException(status_code=404, detail="Task brief is not readable")
        return {"path": brief_path, "content": content[:_PROCESS_BRIEF_MAX_CHARS]}

    return await asyncio.to_thread(_run)
