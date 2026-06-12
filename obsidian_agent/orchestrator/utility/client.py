"""Local-side client for the utility machine.

Each function mirrors one TUI command: reduce the input locally to the
minimal form the agent needs (fetch captions, transcribe audio via the
Dedalus API), ensure the machine, mirror the local agent/ folder onto it
(root-hash check — a no-op round trip when nothing changed), start a
machine-side job, poll it while mirroring its progress lines into the local
Job, then write the returned deliverables into the real local vault.

The machine is a work desk for the agents: it holds the personas, their
vault tools, and the synced corpus — input preprocessing happens here, and
only the reduced input (transcript text, topic, note relpath) crosses the
wire. Deliverables come back as {vault-relpath: base64-content} and are
copied — never moved — so the machine vault keeps accumulating agent content
across jobs; /chat is the consumer of that accumulation.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path, PurePosixPath

import httpx
from dedalus_labs import AsyncDedalus

from api.utils import Job
from obsidian_agent.machine import (
    ensure_machine,
    get_spec,
    request,
    tail_server_log,
)
from obsidian_agent.orchestrator.config import _ensure_vault_path, _load_api_key
from obsidian_agent.sync import sync_agent_folder

POLL_INTERVAL_S = 2.0
JOB_TIMEOUT_S = 3600.0
_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")

# Fail fast on bad inputs before any machine is woken.
AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".wav",
    ".ogg",
    ".webm",
    ".mp4",
    ".mov",
    ".flac",
    ".aac",
}


def _validate_file_path(file_path: str) -> Path:
    path_str = file_path.strip()
    if not path_str:
        raise ValueError("No path provided.")
    path = Path(path_str)
    if not path.exists():
        raise ValueError(f"File not found: {path_str}")
    if path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError(f"'{path_str}' does not look like a supported audio file.")
    return path


# ---------------------------------------------------------------------------
# Input pipelines — run locally, ship only the reduced input to the machine
# ---------------------------------------------------------------------------

_YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)


def _format_timestamp(seconds: float) -> str:
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


def _segments_to_transcript(segments: list) -> str:
    """[(start_seconds, text)] → '[mm:ss] text' lines."""
    lines = []
    for start, text in segments:
        text = (text or "").strip()
        if text:
            lines.append(f"[{_format_timestamp(start)}] {text}")
    return "\n".join(lines)


def _fetch_youtube_transcript(url: str) -> tuple[str, str]:
    """Fetch captions + metadata locally; returns (transcript, source_info).

    Runs on the user's machine on purpose: YouTube blocks transcript requests
    from datacenter IPs (which the VM has), but not residential ones. Title
    and channel come from YouTube's keyless oEmbed endpoint, best effort.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    m = _YOUTUBE_ID_RE.search(url)
    if not m:
        raise ValueError(f"Could not extract a YouTube video ID from: {url}")

    fetched = YouTubeTranscriptApi().fetch(m.group(1))
    transcript = _segments_to_transcript(
        [(snippet.start, snippet.text) for snippet in fetched]
    )

    parts = [f"Source: {url}"]
    try:
        meta = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=10.0,
        ).json()
        parts.insert(0, f"Title: {meta.get('title', 'Untitled Video')}")
        if meta.get("author_name"):
            parts.append(f"Channel: {meta['author_name']}")
    except Exception:
        parts.insert(0, "Title: Untitled Video")

    return transcript, "\n".join(parts)


def _seg_value(seg, key: str, default=None):
    if hasattr(seg, "get"):
        return seg.get(key, default)
    return getattr(seg, key, default)


async def _transcribe_audio_locally(path: Path) -> str:
    """Send audio bytes straight to the Dedalus whisper API from here.

    The bytes travel once (Mac → API) instead of twice (Mac → machine →
    API); the machine only ever sees the resulting transcript text.
    """
    client = AsyncDedalus(api_key=_load_api_key(), timeout=1800)
    response = await client.audio.transcriptions.create(
        file=(path.name, path.read_bytes()),
        model="openai/whisper-1",
        response_format="verbose_json",
    )

    segments = getattr(response, "segments", None)
    if segments is None:
        try:
            data = (
                response.model_dump()
                if hasattr(response, "model_dump")
                else dict(response)
            )
            segments = data.get("segments", [])
        except Exception:
            segments = []

    if not segments:
        return response.text
    return _segments_to_transcript(
        [(_seg_value(s, "start", 0.0), _seg_value(s, "text", "")) for s in segments]
    )


async def transcribe_remote(content: str, job: Job) -> dict:
    """Transcribe a YouTube URL or local audio file, then clean it up on the
    utility machine. All input reduction happens locally — the machine agent
    receives only transcript text."""
    content = content.strip()
    if any(h in content for h in _YOUTUBE_HOSTS):
        job.progress.append("● Fetching YouTube transcript locally…")
        transcript, source_info = await asyncio.to_thread(
            _fetch_youtube_transcript, content
        )
        job.progress.append(f"● {source_info.splitlines()[0]}")
    else:
        local = _validate_file_path(content)
        job.progress.append(f"● Transcribing locally via whisper: {local.name}")
        transcript = await _transcribe_audio_locally(local)
        source_info = f"File: {local.name}"

    return await _run_remote_job(
        "/transcribe-text",
        {"transcript": transcript, "source_info": source_info},
        job,
    )


async def fast_research_remote(topic: str, job: Job) -> dict:
    return await _run_remote_job("/fast-research", {"topic": topic}, job)


async def deep_research_remote(topic: str, job: Job) -> dict:
    return await _run_remote_job("/deep-research", {"topic": topic}, job)


async def mind_map_remote(note_path: str, job: Job) -> dict:
    """Mind-map a note: upload it at its vault relpath so the agent can read it."""
    local = Path(note_path)
    vault_root = Path(_ensure_vault_path()).parent
    try:
        rel = local.resolve().relative_to(vault_root.resolve()).as_posix()
    except ValueError:
        rel = f"agent/imports/{local.name}"
    return await _run_remote_job(
        "/mind-map", {"note_relpath": rel}, job, input_files={rel: local},
    )


# In-process conversation memory: /chat is conversational within one backend
# run; the machine endpoint is stateless and receives the history each call.
_chat_history: list[dict] = []
_CHAT_HISTORY_MAX = 12  # messages (6 exchanges)


async def chat_remote(question: str, job: Job) -> dict:
    """Ask a short-form question over the machine's synced agent corpus.

    Rides the same path as every utility job, so the pre-job sync guarantees
    the corpus is current before the agent answers.
    """
    question = question.strip()
    if not question:
        raise ValueError("Question is empty.")
    result = await _run_remote_job(
        "/chat",
        {"question": question, "history": list(_chat_history)},
        job,
    )
    answer = str(result.get("text") or "")
    if answer:
        _chat_history.append({"role": "user", "content": question})
        _chat_history.append({"role": "assistant", "content": answer})
        del _chat_history[:-_CHAT_HISTORY_MAX]
    return result


# ---------------------------------------------------------------------------
# Shared machinery
# ---------------------------------------------------------------------------


async def _run_remote_job(
    endpoint: str,
    payload: dict,
    job: Job,
    *,
    input_files: dict[str, Path] | None = None,
) -> dict:
    log = job.progress.append
    handle = await asyncio.to_thread(ensure_machine, get_spec("utility"), log=log)

    # Mirror the local agent/ folder before every job: one round trip when
    # nothing changed, so agents (and /chat) always see the current corpus.
    await asyncio.to_thread(sync_agent_folder, handle, log=log)

    if input_files:
        files = []
        paths = []
        for rel, local in input_files.items():
            files.append(("files", (Path(local).name, Path(local).read_bytes())))
            paths.append(rel)
        log(f"● Uploading {len(files)} input file(s) to machine vault…")
        await asyncio.to_thread(
            request, handle, "POST", "/vault-files",
            data={"paths": paths}, files=files, log=log,
        )

    resp = await asyncio.to_thread(
        request, handle, "POST", endpoint, json_body=payload, log=log
    )
    remote_id = resp.json()["job_id"]

    seen = 0
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while True:
        resp = await asyncio.to_thread(
            request, handle, "GET", f"/jobs/{remote_id}", timeout=30.0, log=log
        )
        data = resp.json()
        progress = data.get("progress") or []
        for line in progress[seen:]:
            log(line)
        seen = len(progress)

        if data["status"] == "done":
            break
        if data["status"] == "failed":
            await asyncio.to_thread(tail_server_log, handle, log=log)
            raise RuntimeError(data.get("error") or "Remote job failed")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Utility job {remote_id} exceeded {JOB_TIMEOUT_S:.0f}s")
        await asyncio.sleep(POLL_INTERVAL_S)

    result = data.get("result") or {}
    _apply_deliverables(result.pop("deliverables", None) or {}, log=log)
    return result


def _apply_deliverables(manifest: dict[str, str], *, log) -> None:
    """Write machine-vault files back into the real local vault."""
    if not manifest:
        return
    vault_root = Path(_ensure_vault_path()).parent
    for rel in sorted(manifest):
        relp = PurePosixPath(rel)
        if relp.is_absolute() or ".." in relp.parts or not relp.parts:
            log(f"● Skipping unsafe deliverable path: {rel}")
            continue
        target = vault_root / relp
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(manifest[rel]))
        log(f"● Saved {rel}")
