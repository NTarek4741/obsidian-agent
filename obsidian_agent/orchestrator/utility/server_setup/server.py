"""FastAPI server hosting the utility agents on a Dedalus machine.

The machine is a work desk for the agents: this file plus its siblings
(runner.py, tools.py, vault_sync.py, prompts/) are the entire deployment —
the agents (transcript cleanup, fast research, deep research, mind map,
chat) live here with their vault tools and the synced corpus. All input
preprocessing (caption fetching, whisper transcription) happens on the local
client; only reduced inputs arrive here.

Endpoints (Bearer auth, token == DEDALUS_API_KEY):
    GET  /health
    GET  /vault-manifest  {"root", "files": {rel: sha256}} of the synced agent/ tree
    POST /vault-sync      {"writes": {rel: b64}, "deletes": [rel]} → mirror local agent/
    POST /vault-files     multipart upload of input files into the machine vault
    POST /transcribe-text {"transcript": "...", "source_info": "..."} → {"job_id"}
    POST /fast-research   {"topic": "..."} → {"job_id"}
    POST /deep-research   {"topic": "..."} → {"job_id"}
    POST /mind-map        {"note_relpath": "..."} (uploaded first) → {"job_id"}
    POST /chat            {"question": "...", "history": [...]} → {"job_id"}
    GET  /jobs/{job_id}   same shape as the local API's job endpoint

Jobs wrap the agents in a vault snapshot-diff: any file created or modified
under the vault during the job (excluding agent/uploads/) comes back inline
in job.result["deliverables"] as {vault-relpath: base64-content}. The vault
persists across jobs and autosleep — nothing is cleaned up — so the machine
accumulates a copy of all agent content that later runs can read and search
through the normal vault tools. Snapshot-diffing assumes one job at a time
(single-user), matching the local API's stdout-capture caveat.
"""

import asyncio
import base64
import contextlib
import io
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

import vault_sync
from runner import Persona, agent_dir, run_persona
from tools import (
    _resolve,
    vault_append,
    vault_delete,
    vault_exists,
    vault_get_backlinks,
    vault_get_links,
    vault_get_metadata,
    vault_get_tags,
    vault_grep,
    vault_list,
    vault_read,
    vault_search,
    vault_write,
    vault_write_canvas,
)

app = FastAPI()

API_KEY = os.environ.get("DEDALUS_API_KEY", "")

_vault_env = os.environ.get("OBSIDIAN_VAULT_PATH", "").strip()
if not _vault_env:
    raise RuntimeError("OBSIDIAN_VAULT_PATH must be set for the utility server")
VAULT_ROOT = Path(_vault_env)
VAULT_ROOT.mkdir(parents=True, exist_ok=True)

UPLOAD_STAGING = "agent/uploads"  # inputs land here; excluded from deliverables

_PROMPTS = Path(__file__).parent / "prompts"


# ---------------------------------------------------------------------------
# Job system (mirror of the local API's api/utils.py machinery)
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


async def _run_job(job: Job, coro, *, capture_stdout: bool = False) -> None:
    job.status = "running"
    try:
        if capture_stdout:
            writer = _ProgressWriter(job, sys.stdout)
            with contextlib.redirect_stdout(writer):
                result = await coro
        else:
            result = await coro
        job.result = result
        job.status = "done"
    except Exception as exc:
        job.error = str(exc)
        job.status = "failed"


# ---------------------------------------------------------------------------
# Transcription agent
# ---------------------------------------------------------------------------

TRANSCRIBE = Persona(
    id="transcribe",
    name="Transcription Cleaner",
    description="Clean up a raw transcript into a polished Obsidian note.",
    model="anthropic/claude-haiku-4-5-20251001",
    system_prompt=(_PROMPTS / "transcribe.md").read_text(encoding="utf-8"),
    tools=[vault_read, vault_write, vault_append],
    mcp_servers=[],
    temperature=0.1,
    max_steps=5,
    max_tokens=16384,
)


# ---------------------------------------------------------------------------
# Fast research agent
# ---------------------------------------------------------------------------

GENERATE_NOTE = Persona(
    id="fast_research",
    name="Wiki Note Generator",
    description="Research a topic and create a single comprehensive markdown note.",
    model="anthropic/claude-haiku-4-5-20251001",
    system_prompt=(_PROMPTS / "fast_research.md").read_text(encoding="utf-8"),
    tools=[
        vault_read,
        vault_write,
        vault_append,
        vault_delete,
        vault_list,
        vault_exists,
        vault_search,
        vault_grep,
        vault_get_metadata,
        vault_get_tags,
        vault_get_links,
        vault_get_backlinks,
        vault_write_canvas,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=15,
    max_tokens=8192,
)


async def fast_research_orchestration(messages: list):
    """Run fast research on a set of messages."""
    return await run_persona(GENERATE_NOTE, messages)


# ---------------------------------------------------------------------------
# Deep research agent: plan → execute → verify
# ---------------------------------------------------------------------------


class LessonPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="Filename: lesson-XX-topic-name.md")
    title: str = Field(description="Lesson title")
    focus: str = Field(description="Specific topics, questions, and concepts this lesson covers")


class UnitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: str = Field(description="Folder name: 01-unit-kebab-name")
    title: str = Field(description="Unit title")
    overview: str = Field(description="2-3 sentence summary")
    lessons: list[LessonPlan] = Field(description="Lessons in this unit")


class CurriculumPlan(BaseModel):
    """Structured output schema for the deep research planner."""

    model_config = ConfigDict(extra="forbid")

    project_folder: str = Field(description="Root folder name, kebab-case")
    title: str = Field(description="Human-readable project title")
    description: str = Field(description="1-2 paragraph overview")
    units: list[UnitPlan] = Field(description="Units in the curriculum")


def _ensure_strict_schema(schema: dict) -> dict:
    """Recursively inject additionalProperties: false into every object node.

    OpenAI-compatible providers require this for strict json_schema mode.
    """
    if not isinstance(schema, dict):
        return schema

    if schema.get("type") == "object":
        schema["additionalProperties"] = False

    for key in ("properties", "items", "$defs", "definitions"):
        if key not in schema:
            continue
        value = schema[key]
        if isinstance(value, dict):
            for k, v in value.items():
                schema[key][k] = _ensure_strict_schema(v)
        elif isinstance(value, list):
            schema[key] = [_ensure_strict_schema(v) for v in value]

    # anyOf / allOf / oneOf / enum are arrays of schemas
    for key in ("anyOf", "allOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            schema[key] = [_ensure_strict_schema(v) for v in schema[key]]

    return schema


DR_PLANNER = Persona(
    id="dr_planner",
    name="Deep Research Planner",
    description="Research a topic broadly and generate a structured JSON curriculum plan.",
    model="openai/gpt-5.2",
    system_prompt=(_PROMPTS / "deep_research_planner.md").read_text(encoding="utf-8"),
    tools=[],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=15,
    max_tokens=8192,
    json_schema=_ensure_strict_schema(CurriculumPlan.model_json_schema()),
)

DR_EXECUTOR = Persona(
    id="dr_executor",
    name="Deep Research Executor",
    description="Execute a JSON curriculum plan by creating all folders, overviews, and lessons.",
    model="anthropic/claude-sonnet-4-5-20250929",
    system_prompt=(_PROMPTS / "deep_research_executor.md").read_text(encoding="utf-8"),
    tools=[
        vault_read,
        vault_write,
        vault_list,
        vault_exists,
        vault_search,
        vault_get_metadata,
        vault_get_tags,
        vault_get_links,
        vault_get_backlinks,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=40,
    max_tokens=8192,
)


async def deep_research_plan(messages: list) -> dict:
    """
    Stage 1: Run the planner to research the topic and return a JSON curriculum plan.

    Returns:
        {
            "plan": dict|None,        # extracted JSON plan (None on failure)
            "plan_result": object,    # raw planner result
            "error": str|None,        # error message on failure
        }
    """
    plan_result = await run_persona(DR_PLANNER, messages)

    try:
        plan = json.loads(plan_result.final_output.strip())
    except (json.JSONDecodeError, AttributeError) as exc:
        return {
            "plan": None,
            "plan_result": plan_result,
            "error": f"Failed to parse JSON plan: {exc}",
        }

    if (
        not isinstance(plan, dict)
        or "units" not in plan
        or "project_folder" not in plan
    ):
        return {
            "plan": plan,
            "plan_result": plan_result,
            "error": "Invalid plan shape.",
        }

    return {
        "plan": plan,
        "plan_result": plan_result,
        "error": None,
    }


async def deep_research_execute(plan: dict) -> dict:
    """
    Stage 2: Run the executor to build the full folder structure from a plan.
    Stage 3: Verify every planned file exists.

    Returns:
        {
            "executor_result": object,   # raw executor result
            "missing_files": list[str],  # files that failed verification
            "verified": bool,
        }
    """
    vault_path = agent_dir()

    plan_json_str = json.dumps(plan, indent=2)
    executor_input = (
        "Execute the following curriculum plan. Build the ENTIRE folder structure, "
        "create every overview.md and every lesson.md, verify each file, and do not stop until complete.\n\n"
        f"{plan_json_str}"
    )
    executor_messages = [{"role": "user", "content": executor_input}]

    executor_result = await run_persona(DR_EXECUTOR, executor_messages)

    # Verify
    missing = []
    project_folder = plan.get("project_folder", "")
    root_overview = f"{project_folder}/overview.md"
    if vault_read(vault_path, root_overview).startswith("Error:"):
        missing.append(root_overview)

    for unit in plan.get("units", []):
        unit_folder = unit.get("folder", "")
        unit_overview = f"{project_folder}/{unit_folder}/overview.md"
        if vault_read(vault_path, unit_overview).startswith("Error:"):
            missing.append(unit_overview)

        for lesson in unit.get("lessons", []):
            lesson_file = lesson.get("file", "")
            lesson_path = f"{project_folder}/{unit_folder}/lessons/{lesson_file}"
            if vault_read(vault_path, lesson_path).startswith("Error:"):
                missing.append(lesson_path)

    return {
        "executor_result": executor_result,
        "missing_files": missing,
        "verified": len(missing) == 0,
    }


# ---------------------------------------------------------------------------
# Mind map agent
# ---------------------------------------------------------------------------

MIND_MAP_AGENT = Persona(
    id="mind_map_agent",
    name="Mind Map Agent",
    description="Transform notes into radial mind maps with research-backed sources and learning paths.",
    model="anthropic/claude-sonnet-4-5-20250929",
    system_prompt=(_PROMPTS / "mind_map.md").read_text(encoding="utf-8"),
    tools=[
        vault_read,
        vault_write,
        vault_write_canvas,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.2,
    max_tokens=16384,
    max_steps=20,
)


async def mind_map_generate(note_path: str, note_content: str) -> dict:
    """
    One-shot mind map generation with research.

    Args:
        note_path: Path to the original note (for refinement)
        note_content: Full markdown content of the note

    Returns:
        {
            "result": object,         # raw result from agent
            "mind_map_path": str | None,
            "mind_map_data": dict | None,
            "sources_found": list,    # list of source dicts
            "note_refined": bool,
            "error": str | None,
        }
    """
    vault_path = agent_dir()

    messages = [
        {
            "role": "user",
            "content": (
                f"Create a mind map from this note and research deeper sources.\n\n"
                f"**Original Note Path:** {note_path}\n\n"
                f"**Note Content:**\n```markdown\n{note_content}\n```\n\n"
                f"Instructions:\n"
                f"1. Research the topic using web search to find books, papers, and courses.\n"
                f"2. Build a radial mind map canvas visualizing the note's concepts.\n"
                f"3. Append a '## Mind Map Sources & Learning Path' section to the original note.\n"
                f"4. Save the mind map as a .canvas file with the same name as the note (e.g., '{Path(note_path).stem}.canvas')."
            ),
        }
    ]

    result = await run_persona(MIND_MAP_AGENT, messages)

    # Extract mind map data from tool results
    mind_map_data = None
    mind_map_path = None
    for tr in result.tool_results:
        if tr.get("name") == "vault_write_canvas":
            args = tr.get("arguments", {})
            mind_map_data = args.get("canvas_data")
            mind_map_path = args.get("path")
            if mind_map_data is None:
                result_str = str(tr.get("result", ""))
                if "Success" in result_str:
                    m = re.search(r'"([^"]+\.canvas)"', result_str)
                    if m:
                        mind_map_path = m.group(1)
                        try:
                            target = _resolve(vault_path, mind_map_path)
                            mind_map_data = json.loads(
                                target.read_text(encoding="utf-8")
                            )
                        except Exception:
                            pass
            break

    # Check if note was refined (vault_write to the original path)
    note_refined = False
    for tr in result.tool_results:
        if tr.get("name") == "vault_write":
            args = tr.get("arguments", {})
            written_path = args.get("path", "")
            if (
                written_path == note_path
                or Path(written_path).name == Path(note_path).name
            ):
                note_refined = True
                break

    # Extract sources from the agent's output or tool results
    sources_found = []
    try:
        # Try to find sources in the final output
        output = str(result.final_output)
        # Look for source patterns
        source_patterns = re.findall(
            r"\*\*([^*]+)\*\*\s*[-–]\s*([^\n]+)(?:\n|$)", output
        )
        for title, desc in source_patterns:
            if len(title) > 3 and len(desc) > 5:
                sources_found.append(
                    {"title": title.strip(), "description": desc.strip()}
                )
    except Exception:
        pass

    error = None
    if not mind_map_data:
        error = "No mind map found in results."

    return {
        "result": result,
        "mind_map_path": mind_map_path,
        "mind_map_data": mind_map_data,
        "sources_found": sources_found,
        "note_refined": note_refined,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Chat agent: short-form Q&A over the synced agent corpus
# ---------------------------------------------------------------------------

CHAT = Persona(
    id="chat",
    name="Vault Chat",
    description="Answer short-form questions grounded in the synced agent corpus.",
    model="anthropic/claude-haiku-4-5-20251001",
    system_prompt=(_PROMPTS / "chat.md").read_text(encoding="utf-8"),
    # Read-only on purpose: chat answers, it does not mutate. The machine's
    # agent/ tree only ever changes via /vault-sync or deliverable-producing
    # jobs, so the mirror invariant survives any prompt injection in a note.
    tools=[
        vault_read,
        vault_list,
        vault_exists,
        vault_search,
        vault_grep,
        vault_get_metadata,
        vault_get_tags,
        vault_get_links,
        vault_get_backlinks,
    ],
    mcp_servers=[],
    temperature=0.2,
    max_steps=8,
    max_tokens=4096,
)


# ---------------------------------------------------------------------------
# Auth & path safety
# ---------------------------------------------------------------------------


class TopicRequest(BaseModel):
    topic: str


class NoteRelpathRequest(BaseModel):
    note_relpath: str


class ChatRequest(BaseModel):
    question: str
    history: list[dict] = Field(default_factory=list)


class TranscriptRequest(BaseModel):
    transcript: str
    source_info: str = ""


class VaultSyncRequest(BaseModel):
    writes: dict[str, str] = Field(default_factory=dict)   # rel → base64 content
    deletes: list[str] = Field(default_factory=list)


def verify_auth(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization[7:]
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


def _safe_relpath(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise HTTPException(status_code=400, detail=f"Invalid vault path: {rel}")
    return p


def _agent_root() -> Path:
    """The synced corpus: <vault>/agent — the mirror of the local agent folder
    and the sandbox every persona's tools are bound to (see runner.agent_dir)."""
    root = VAULT_ROOT / "agent"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# Deliverables: snapshot the vault before a job, diff after
# ---------------------------------------------------------------------------


def _snapshot() -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    for path in VAULT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(VAULT_ROOT).as_posix()
        if rel.startswith(f"{UPLOAD_STAGING}/"):
            continue
        st = path.stat()
        snap[rel] = (st.st_mtime_ns, st.st_size)
    return snap


def _diff_deliverables(before: dict[str, tuple[int, int]]) -> dict[str, str]:
    deliverables: dict[str, str] = {}
    for rel, stat in _snapshot().items():
        if before.get(rel) != stat:
            data = (VAULT_ROOT / rel).read_bytes()
            deliverables[rel] = base64.b64encode(data).decode("ascii")
    return deliverables


async def _with_deliverables(work: Callable[[], Awaitable[dict]]) -> dict:
    before = _snapshot()
    result = await work()
    result["deliverables"] = _diff_deliverables(before)
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok", "vault": str(VAULT_ROOT)}


@app.post("/vault-files")
async def vault_files(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    token: str = Depends(verify_auth),
):
    """Write uploaded input files into the machine vault at the given relpaths."""
    if len(files) != len(paths):
        raise HTTPException(status_code=400, detail="files/paths length mismatch")
    written = []
    for upload, rel in zip(files, paths):
        relp = _safe_relpath(rel)
        target = VAULT_ROOT / relp
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(await upload.read())
        written.append(relp.as_posix())
    return {"written": written}


@app.get("/vault-manifest")
async def vault_manifest(token: str = Depends(verify_auth)):
    """Manifest + root hash of the synced agent/ tree (sync fast path)."""
    manifest = vault_sync.build_manifest(_agent_root())
    return {"root": vault_sync.root_hash(manifest), "files": manifest}


@app.post("/vault-sync")
async def vault_sync_apply(req: VaultSyncRequest, token: str = Depends(verify_auth)):
    """Make the machine's agent/ tree mirror the local one (local wins)."""
    root = _agent_root()
    for rel in list(req.writes) + req.deletes:
        relp = _safe_relpath(rel)
        if not vault_sync.is_synced_path(relp.parts):
            raise HTTPException(
                status_code=400, detail=f"Path outside sync domain: {rel}"
            )

    for rel, b64 in req.writes.items():
        target = root / PurePosixPath(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(b64))

    deleted = 0
    for rel in req.deletes:
        target = root / PurePosixPath(rel)
        if target.is_file():
            target.unlink()
            deleted += 1

    # Prune synced dirs left empty by deletions so vault_list stays truthful.
    # Excluded paths (uploads/, dotdirs) are never touched.
    for d in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        if not vault_sync.is_synced_path(d.relative_to(root).parts):
            continue
        with contextlib.suppress(OSError):  # not empty / gone — keep it
            d.rmdir()

    manifest = vault_sync.build_manifest(root)
    return {
        "root": vault_sync.root_hash(manifest),
        "written": len(req.writes),
        "deleted": deleted,
    }


@app.post("/transcribe-text")
async def transcribe_text(req: TranscriptRequest, token: str = Depends(verify_auth)):
    """Clean a pre-fetched transcript into a note.

    YouTube blocks transcript fetches from datacenter IPs, so the local
    client fetches captions on the user's machine and ships the text here —
    only the cleanup agent runs on the VM.
    """
    job = _new_job("transcribe")

    async def _coro():
        async def work():
            job.progress.append("● Running AI agent…")
            msg_content = f"{req.source_info}\n\n{req.transcript}".strip()
            messages = [{"role": "user", "content": msg_content}]
            result = await run_persona(TRANSCRIBE, messages)
            final = str(result.final_output) if hasattr(result, "final_output") else str(result)
            return {"output": final}

        return await _with_deliverables(work)

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/fast-research")
async def fast_research(req: TopicRequest, token: str = Depends(verify_auth)):
    job = _new_job("research_fast")

    async def _coro():
        async def work():
            messages = [{"role": "user", "content": req.topic}]
            result = await fast_research_orchestration(messages)
            final = str(result.final_output) if hasattr(result, "final_output") else str(result)
            return {"result": final}

        return await _with_deliverables(work)

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/deep-research")
async def deep_research(req: TopicRequest, token: str = Depends(verify_auth)):
    job = _new_job("deep_research")

    async def _coro():
        async def work():
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

        return await _with_deliverables(work)

    _fire(_run_job(job, _coro(), capture_stdout=True))
    return {"job_id": job.job_id}


@app.post("/mind-map")
async def mind_map(req: NoteRelpathRequest, token: str = Depends(verify_auth)):
    relp = _safe_relpath(req.note_relpath)
    note_abs = VAULT_ROOT / relp
    if not note_abs.exists():
        raise HTTPException(status_code=404, detail=f"Note not found: {req.note_relpath}")
    note_content = note_abs.read_text(encoding="utf-8")
    job = _new_job("mind_map")

    async def _coro():
        async def work():
            outcome = await mind_map_generate(str(note_abs), note_content)
            # The raw runner result is not JSON-serializable over HTTP.
            raw = outcome.pop("result", None)
            outcome["final_output"] = str(getattr(raw, "final_output", raw))
            return outcome

        return await _with_deliverables(work)

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.post("/chat")
async def chat(req: ChatRequest, token: str = Depends(verify_auth)):
    """Short-form Q&A grounded in the synced agent corpus."""
    job = _new_job("chat")

    async def _coro():
        async def work():
            messages = [
                {"role": str(m.get("role", "user")), "content": str(m["content"])}
                for m in req.history
                if isinstance(m, dict) and m.get("content")
            ]
            messages.append({"role": "user", "content": req.question})
            result = await run_persona(CHAT, messages)
            final = str(result.final_output) if hasattr(result, "final_output") else str(result)
            return {"text": final}

        return await _with_deliverables(work)

    _fire(_run_job(job, _coro()))
    return {"job_id": job.job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, token: str = Depends(verify_auth)):
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
