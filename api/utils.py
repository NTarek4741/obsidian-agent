from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import HTTPException
from pydantic import BaseModel

from obsidian_agent.config import _ensure_vault_path

# ---------------------------------------------------------------------------
# Job system
# ---------------------------------------------------------------------------


@dataclass
class Job:
    job_id: str
    kind: str
    status: str = "pending"          # pending | running | done | failed
    progress: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_background_tasks: set = set()


def _fire(coro):
    t = asyncio.create_task(coro)
    _background_tasks.add(t)
    t.add_done_callback(_background_tasks.discard)
    return t


def _active_job() -> Job | None:
    return next(
        (j for j in _jobs.values() if j.status in ("pending", "running")), None
    )


def _new_job(kind: str) -> Job:
    """Create a job, enforcing the one-job-at-a-time rule for the vault.

    The whole backend runs at most one worker thread because of this gate:
    every agent endpoint funnels through here on the event-loop thread, so
    no lock is needed to make the check atomic.
    """
    active = _active_job()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"job {active.job_id} ({active.kind}) is still running — "
                   f"one job at a time",
        )
    job = Job(job_id=str(uuid.uuid4())[:8], kind=kind)
    _jobs[job.job_id] = job
    return job


async def _run_job(job: Job, coro) -> None:
    job.status = "running"
    try:
        job.result = await coro
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def is_configured() -> bool:
    load_dotenv()
    return bool(os.getenv("DEDALUS_API_KEY", "").strip()) and bool(
        os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    )


def require_configured():
    if not is_configured():
        raise HTTPException(status_code=503, detail="not_configured: run /config first")


def resolve_note_path(note_path: str) -> tuple[str, str]:
    """Resolve note path to (absolute_path, content)."""
    p = Path(note_path).expanduser()
    if not p.is_absolute():
        vault = _ensure_vault_path()
        p = Path(vault).parent / note_path
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Note not found: {note_path}")
    return str(p), p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConfigRequest(BaseModel):
    api_key: str
    vault_path: str


class TranscribeRequest(BaseModel):
    content: str  # YouTube URL or local file path


class TopicRequest(BaseModel):
    topic: str


class NotePathRequest(BaseModel):
    note_path: str


class ChatRequest(BaseModel):
    question: str


class JobResponse(BaseModel):
    job_id: str
