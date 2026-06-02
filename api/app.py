"""FastAPI backend server for ObsidianAgent.

Each orchestrator is exposed as a separate endpoint. Long-running operations
return a job_id that can be polled via GET /jobs/{job_id}.

Run:
    uv run uvicorn api.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from obsidian_agent.orchestrator import (
    deep_research_execute,
    deep_research_plan,
    fast_research_orchestration,
    generate_flashcards,
    generate_podcast,
    mind_map_generate,
)
from obsidian_agent.orchestrator.transcription.orchestration import transcribe_auto

from api.utils import (
    Job,
    JobResponse,
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
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/transcribe", response_model=JobResponse)
async def transcribe_endpoint(req: TranscribeRequest):
    require_configured()
    job = _new_job("transcribe")

    async def _coro():
        result = await transcribe_auto(req.content, on_progress=job.progress.append)
        final = str(result.final_output) if hasattr(result, "final_output") else str(result)
        return {"output": final}

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/research/fast", response_model=JobResponse)
async def fast_research(req: TopicRequest):
    require_configured()
    job = _new_job("research_fast")

    async def _coro():
        job.progress.append("Running research agent…")
        messages = [{"role": "user", "content": req.topic}]
        result = await fast_research_orchestration(messages)
        final = str(result.final_output) if hasattr(result, "final_output") else str(result)
        return {"result": final}

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/mind-map", response_model=JobResponse)
async def mind_map(req: NotePathRequest):
    require_configured()
    note_path, note_content = resolve_note_path(req.note_path)
    job = _new_job("mind_map")

    async def _coro():
        job.progress.append("Generating mind map…")
        return await mind_map_generate(note_path, note_content)

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/research/deep", response_model=JobResponse)
async def deep_research(req: TopicRequest):
    require_configured()
    job = _new_job("deep_research")

    async def _coro():
        messages = [{"role": "user", "content": req.topic}]
        plan_result = await deep_research_plan(messages)
        if plan_result.get("error"):
            raise RuntimeError(f"Planning failed: {plan_result['error']}")
        plan = plan_result["plan"]
        exec_result = await deep_research_execute(plan)
        return {
            "plan": plan,
            "verified": exec_result.get("verified", False),
            "missing_files": exec_result.get("missing_files", []),
        }

    _fire(_run_job(job, _coro(), capture_stdout=True))
    return {"job_id": job.job_id}


@app.post("/podcast", response_model=JobResponse)
async def podcast(req: NotePathRequest):
    require_configured()
    note_path, note_content = resolve_note_path(req.note_path)
    job = _new_job("podcast")
    _fire(_run_job(job, asyncio.to_thread(generate_podcast, note_path, note_content), capture_stdout=True))
    return {"job_id": job.job_id}


@app.post("/flashcard", response_model=JobResponse)
async def flashcard(req: NotePathRequest):
    require_configured()
    note_path, note_content = resolve_note_path(req.note_path)
    job = _new_job("flashcard")
    _fire(_run_job(job, asyncio.to_thread(generate_flashcards, note_path, note_content), capture_stdout=True))
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
