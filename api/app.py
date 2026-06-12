"""FastAPI backend server for ObsidianAgent.

A thin unified router: every agent endpoint resolves its inputs, then
dispatches to the agent's machine client via AGENT_CLIENTS. Long-running
operations return a job_id that can be polled via GET /jobs/{job_id}.

Run:
    uv run uvicorn api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from obsidian_agent.orchestrator import AGENT_CLIENTS

from api.utils import (
    JobResponse,
    ChatRequest,
    ConfigRequest,
    TranscribeRequest,
    TopicRequest,
    NotePathRequest,
    _jobs,
    _fire,
    _new_job,
    _run_job,
    is_configured,
    require_configured,
    resolve_note_path,
)

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
# Health & config
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    configured = is_configured()
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
# Agent endpoints — one dispatch for all six clients
# ---------------------------------------------------------------------------


def _dispatch(kind: str, *inputs) -> dict:
    """Start the agent's machine client as a polled job."""
    require_configured()
    job = _new_job(kind)
    _fire(_run_job(job, AGENT_CLIENTS[kind](*inputs, job)))
    return {"job_id": job.job_id}


@app.post("/transcribe", response_model=JobResponse)
async def transcribe_endpoint(req: TranscribeRequest):
    return _dispatch("transcribe", req.content)


@app.post("/research/fast", response_model=JobResponse)
async def fast_research(req: TopicRequest):
    return _dispatch("research_fast", req.topic)


@app.post("/research/deep", response_model=JobResponse)
async def deep_research(req: TopicRequest):
    return _dispatch("deep_research", req.topic)


@app.post("/mind-map", response_model=JobResponse)
async def mind_map(req: NotePathRequest):
    require_configured()
    note_path, _note_content = resolve_note_path(req.note_path)
    return _dispatch("mind_map", note_path)


@app.post("/podcast", response_model=JobResponse)
async def podcast(req: NotePathRequest):
    require_configured()
    note_path, note_content = resolve_note_path(req.note_path)
    return _dispatch("podcast", note_path, note_content)


@app.post("/flashcard", response_model=JobResponse)
async def flashcard(req: NotePathRequest):
    require_configured()
    note_path, note_content = resolve_note_path(req.note_path)
    return _dispatch("flashcard", note_path, note_content)


@app.post("/chat", response_model=JobResponse)
async def chat(req: ChatRequest):
    return _dispatch("chat", req.question)


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


# ---------------------------------------------------------------------------
# Machine state surface
# ---------------------------------------------------------------------------


@app.get("/machines")
async def machines():
    """Lifecycle snapshot of every Dedalus machine for the TUI panel.

    Returns the in-process state immediately; real phases are refreshed from
    the Dedalus API in the background at most once per 60s, so TUI polling
    stays cheap while autosleep transitions still show up.
    """
    from obsidian_agent.machine import machine_status, refresh_machine_phases

    if is_configured():
        threading.Thread(target=refresh_machine_phases, daemon=True).start()
    return machine_status()


@app.delete("/machines")
async def delete_machines():
    from obsidian_agent.machine import delete_all_machines
    deleted = await asyncio.to_thread(delete_all_machines)
    return {"deleted": len(deleted), "machine_ids": deleted}


# ---------------------------------------------------------------------------
# Startup: mirror the local agent/ folder onto the utility machine
# ---------------------------------------------------------------------------


def _startup_sync_worker() -> None:
    """Ensure the utility machine and sync the agent corpus (background).

    Progress is silent here on purpose — every step lands in the machine
    state surface via _emit(), which the TUI's machines panel renders live.
    """
    from obsidian_agent.machine import _emit, ensure_machine, get_spec
    from obsidian_agent.sync import sync_agent_folder

    try:
        handle = ensure_machine(get_spec("utility"), log=lambda _line: None)
        sync_agent_folder(handle, log=lambda _line: None)
    except Exception as exc:
        _emit("utility", f"startup sync failed: {exc}", phase="error")


@app.on_event("startup")
async def startup_vault_sync():
    if os.getenv("OBSIDIAN_SYNC_ON_START", "1").strip().lower() in ("0", "false", "no"):
        return
    if not is_configured():
        return
    _fire(asyncio.to_thread(_startup_sync_worker))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start():
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    start()
