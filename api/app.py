"""FastAPI backend server for ObsidianAgent.

Each orchestrator is exposed as a separate endpoint.
Long-running operations (podcast, flashcard, deep research) return a job_id
that can be polled via GET /jobs/{job_id}.

Run:
    uv run uvicorn api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from obsidian_agent.orchestrator import (
    deep_research_execute,
    deep_research_plan,
    fast_research_orchestration,
    generate_flashcards,
    generate_podcast,
    mind_map_generate,
)
from obsidian_agent.orchestrator.utility import _load_api_key, _ensure_vault_path
from obsidian_agent.orchestrator.transcription.orchestration import transcribe_auto

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="ObsidianAgent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()

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


class _ProgressWriter(io.TextIOBase):
    """Captures print() output and appends to a Job's progress list."""

    def __init__(self, job: Job, real_stdout):
        self._job = job
        self._real = real_stdout

    def write(self, text: str) -> int:
        if text and text != "\n":
            self._job.progress.append(text.rstrip("\n"))
        if self._real:
            self._real.write(text)
        return len(text)

    def flush(self):
        if self._real:
            self._real.flush()


def _new_job(kind: str) -> Job:
    job = Job(job_id=str(uuid.uuid4())[:8], kind=kind)
    _jobs[job.job_id] = job
    return job


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_configured() -> bool:
    load_dotenv()
    return bool(os.getenv("DEDALUS_API_KEY", "").strip()) and bool(
        os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    )


def _require_configured():
    if not _is_configured():
        raise HTTPException(
            status_code=503,
            detail="not_configured: run /config first",
        )


def _resolve_note_path(note_path: str) -> tuple[str, str]:
    """Resolve note path to (absolute_path, content).

    Accepts absolute paths or paths relative to the vault agent folder.
    """
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


class JobResponse(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# Health & config
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    configured = _is_configured()
    vault = None
    if configured:
        try:
            vault = str(Path(os.getenv("OBSIDIAN_VAULT_PATH", "")).expanduser())
        except Exception:
            pass
    return {"status": "ok", "configured": configured, "vault": vault}


@app.post("/config")
async def configure(req: ConfigRequest):
    """Save API key and vault path to .env."""
    vault = Path(req.vault_path).expanduser().resolve()
    if not vault.exists() or not vault.is_dir():
        raise HTTPException(status_code=400, detail=f"Vault path does not exist: {vault}")

    env_path = Path(".env")
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")

    set_key(str(env_path), "DEDALUS_API_KEY", req.api_key)
    set_key(str(env_path), "OBSIDIAN_VAULT_PATH", str(vault))
    load_dotenv(override=True)

    return {"status": "ok", "vault": str(vault)}


# ---------------------------------------------------------------------------
# Transcription (background job)
# ---------------------------------------------------------------------------


@app.post("/transcribe", response_model=JobResponse)
async def transcribe_endpoint(req: TranscribeRequest):
    _require_configured()
    job = _new_job("transcribe")

    async def _run():
        job.status = "running"
        try:
            result = await transcribe_auto(req.content, on_progress=job.progress.append)
            final = str(result.final_output) if hasattr(result, "final_output") else str(result)
            job.result = {"output": final}
            job.status = "done"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"

    _fire(_run())
    return {"job_id": job.job_id}


# ---------------------------------------------------------------------------
# Fast research (direct — fast)
# ---------------------------------------------------------------------------


@app.post("/research/fast")
async def fast_research(req: TopicRequest):
    _require_configured()
    messages = [{"role": "user", "content": req.topic}]
    result = await fast_research_orchestration(messages)
    return {"result": str(result.final_output) if hasattr(result, "final_output") else str(result)}


# ---------------------------------------------------------------------------
# Mind map (direct — fast)
# ---------------------------------------------------------------------------


@app.post("/mind-map")
async def mind_map(req: NotePathRequest):
    _require_configured()
    note_path, note_content = _resolve_note_path(req.note_path)
    result = await mind_map_generate(note_path, note_content)
    return result


# ---------------------------------------------------------------------------
# Deep research (background job — slow)
# ---------------------------------------------------------------------------


@app.post("/research/deep", response_model=JobResponse)
async def deep_research(req: TopicRequest):
    _require_configured()
    job = _new_job("deep_research")

    async def _run():
        import sys
        writer = _ProgressWriter(job, sys.stdout)
        job.status = "running"
        try:
            with contextlib.redirect_stdout(writer):
                messages = [{"role": "user", "content": req.topic}]
                plan_result = await deep_research_plan(messages)
                if plan_result.get("error"):
                    raise RuntimeError(f"Planning failed: {plan_result['error']}")
                plan = plan_result["plan"]
                exec_result = await deep_research_execute(plan)
            job.result = {
                "plan": plan,
                "verified": exec_result.get("verified", False),
                "missing_files": exec_result.get("missing_files", []),
            }
            job.status = "done"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"

    _fire(_run())
    return {"job_id": job.job_id}


# ---------------------------------------------------------------------------
# Podcast (background job — slow)
# ---------------------------------------------------------------------------


@app.post("/podcast", response_model=JobResponse)
async def podcast(req: NotePathRequest):
    _require_configured()
    note_path, note_content = _resolve_note_path(req.note_path)
    job = _new_job("podcast")

    async def _run():
        import sys
        writer = _ProgressWriter(job, sys.stdout)
        job.status = "running"
        try:
            with contextlib.redirect_stdout(writer):
                result = await asyncio.to_thread(generate_podcast, note_path, note_content)
            job.result = result
            job.status = "done"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"

    _fire(_run())
    return {"job_id": job.job_id}


# ---------------------------------------------------------------------------
# Flashcard (background job — slow)
# ---------------------------------------------------------------------------


@app.post("/flashcard", response_model=JobResponse)
async def flashcard(req: NotePathRequest):
    _require_configured()
    note_path, note_content = _resolve_note_path(req.note_path)
    job = _new_job("flashcard")

    async def _run():
        import sys
        writer = _ProgressWriter(job, sys.stdout)
        job.status = "running"
        try:
            with contextlib.redirect_stdout(writer):
                result = await asyncio.to_thread(generate_flashcards, note_path, note_content)
            job.result = result
            job.status = "done"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"

    _fire(_run())
    return {"job_id": job.job_id}


# ---------------------------------------------------------------------------
# Job polling
# ---------------------------------------------------------------------------


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return {
        "job_id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "result": job.result,
        "error": job.error,
    }


@app.get("/jobs")
async def list_jobs():
    return [
        {"job_id": j.job_id, "kind": j.kind, "status": j.status, "created_at": j.created_at}
        for j in sorted(_jobs.values(), key=lambda j: j.created_at, reverse=True)
    ]


# ---------------------------------------------------------------------------
# Machine cleanup
# ---------------------------------------------------------------------------


@app.delete("/machines")
async def delete_machines():
    from obsidian_agent.orchestrator import delete_all_machines
    deleted = await asyncio.to_thread(delete_all_machines)
    return {"deleted": len(deleted), "machine_ids": deleted}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start():
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
