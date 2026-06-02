"""FastAPI server that runs a Dedalus SDK agent to generate podcasts.

This file is deployed to /home/machine/podcast_app/server.py on the VM.

Endpoints:
    GET  /health
        Health check — returns 200 when server is alive.
    POST /generate-podcast
        Body: {"note_content": "<markdown note>"}
        Auth: Bearer <DEDALUS_API_KEY>
        Response: audio/wav file (WAV podcast)

Architecture:
    A `DedalusRunner` agent drives the build with three Python tools that wrap
    the Kokoro library calls available in this VM environment:
      * synthesize_clip(text)       → writes a per-clip WAV, returns clip_path
      * merge_clips(clip_paths)     → concatenates WAVs, returns wav_path
      * deliver_podcast(wav_path)   → marks the final artifact
    The single host voice is `af_bella`.
"""

import os
import tempfile
import traceback

import numpy as np
import soundfile as sf
import uvicorn
from dedalus_labs import AsyncDedalus, DedalusRunner
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from kokoro import KPipeline
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ.get("DEDALUS_API_KEY", "")
MODEL = "anthropic/claude-sonnet-4-20250514"

PODCAST_SYSTEM_PROMPT = """SYSTEM_PROMPT_PLACEHOLDER"""

HOST_VOICE = "af_bella"
SAMPLE_RATE = 24000
MAX_AGENT_STEPS = 50

# Kokoro is heavy to initialise (loads weights). Build it lazily on the first
# synthesis call so /health responds immediately after uvicorn starts —
# otherwise a wake-restart can blow past the orchestrator's health-check window.
_kokoro_pipeline = None


def _get_kokoro_pipeline():
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        print("Initialising Kokoro pipeline...")
        _kokoro_pipeline = KPipeline(lang_code="a")
        print("Kokoro pipeline ready")
    return _kokoro_pipeline


class PodcastRequest(BaseModel):
    note_content: str


def verify_auth(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization[7:]
    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _synthesize_to_wav(text: str) -> tuple[str, float]:
    """Run Kokoro on `text`, write a WAV file, return (path, duration_s)."""
    chunks = []
    generator = _get_kokoro_pipeline()(text, voice=HOST_VOICE, speed=1.0, split_pattern=r"\n+")
    for _, _, audio in generator:
        if audio is not None and len(audio) > 0:
            chunks.append(np.asarray(audio, dtype=np.float32))
    if not chunks:
        raise RuntimeError("Kokoro produced no audio for the given text")
    final = np.concatenate(chunks)
    wav_path = tempfile.mktemp(suffix=".wav")
    sf.write(wav_path, final, SAMPLE_RATE)
    duration_s = round(len(final) / SAMPLE_RATE, 2)
    return wav_path, duration_s


def _concat_wavs(wav_paths: list[str]) -> str:
    """Concatenate WAV files (assumed same sample rate), return new WAV path."""
    arrays = []
    for p in wav_paths:
        data, sr = sf.read(p, dtype="float32")
        if sr != SAMPLE_RATE:
            raise RuntimeError(f"Unexpected sample rate in {p}: {sr}")
        arrays.append(data)
    merged = np.concatenate(arrays) if arrays else np.zeros(0, dtype=np.float32)
    out_path = tempfile.mktemp(suffix=".wav")
    sf.write(out_path, merged, SAMPLE_RATE)
    return out_path


# ---------------------------------------------------------------------------
# Agent tools — module-level, stateless. The endpoint recovers the final
# wav path from runner.run()'s tool_results, so no shared state is needed.
# ---------------------------------------------------------------------------


def synthesize_clip(text: str) -> dict:
    """Synthesise one chunk of the host's narration as a WAV file.

    Use 1-3 natural sentences per call. Do not include speaker labels,
    stage directions, or pause markers — just the words to be spoken.
    Returns the path to a WAV clip which must be passed to merge_clips.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "text is empty"}
    try:
        wav_path, duration_s = _synthesize_to_wav(text)
        print(f"  synthesize_clip ({duration_s}s): {text[:80]!r}")
        return {"clip_path": wav_path, "duration_s": duration_s}
    except Exception as exc:
        print(f"  synthesize_clip FAIL: {exc}")
        return {"error": f"synth failed: {exc}"}


def merge_clips(clip_paths: list[str]) -> dict:
    """Concatenate the given WAV clips in order and produce one WAV file.

    Pass the clip_path values returned by prior synthesize_clip calls,
    in the order the listener should hear them. Returns the wav_path
    which must be passed to deliver_podcast.
    """
    if not clip_paths:
        return {"error": "clip_paths is empty"}
    try:
        wav_path = _concat_wavs(clip_paths)
        size = os.path.getsize(wav_path)
        print(f"  merge_clips: {len(clip_paths)} clips → {wav_path} ({size} bytes)")
        return {"wav_path": wav_path, "bytes": size}
    except Exception as exc:
        print(f"  merge_clips FAIL: {exc}")
        return {"error": f"merge failed: {exc}"}


def deliver_podcast(wav_path: str) -> dict:
    """Mark the WAV at wav_path as the final podcast to ship to the user.

    Call this exactly once, after merge_clips has produced the WAV.
    """
    if not wav_path or not os.path.exists(wav_path):
        return {"error": f"wav_path does not exist: {wav_path}"}
    print(f"  deliver_podcast: {wav_path}")
    return {"status": "ok", "wav_path": wav_path}


TOOLS = [synthesize_clip, merge_clips, deliver_podcast]


def _extract_delivered_path(tool_results: list[dict]) -> str | None:
    """Find the wav_path captured by the agent's deliver_podcast call."""
    for entry in tool_results:
        if entry.get("name") != "deliver_podcast":
            continue
        result = entry.get("result")
        if isinstance(result, dict) and "wav_path" in result:
            return result["wav_path"]
    return None


# ---------------------------------------------------------------------------
# HTTP endpoint — just runs the agent
# ---------------------------------------------------------------------------


@app.post("/generate-podcast")
async def generate_podcast(request: PodcastRequest, token: str = Depends(verify_auth)):
    print(f"Received podcast request ({len(request.note_content)} chars)")

    try:
        client = AsyncDedalus(api_key=API_KEY)
        runner = DedalusRunner(client)
        result = await runner.run(
            model=MODEL,
            instructions=PODCAST_SYSTEM_PROMPT,
            input=f"Create a podcast from this note:\n\n{request.note_content}",
            tools=TOOLS,
            max_steps=MAX_AGENT_STEPS,
            temperature=0.7,
            max_tokens=8192,
        )
    except HTTPException:
        raise
    except Exception as exc:
        msg = f"Error in generate_podcast: {exc}\n{traceback.format_exc()}"
        print(msg)
        raise HTTPException(status_code=500, detail=msg)

    final_path = _extract_delivered_path(result.tool_results)
    if not final_path:
        raise HTTPException(status_code=500, detail="Agent did not deliver a podcast")

    print(f"Serving: {final_path}")
    return FileResponse(final_path, media_type="audio/wav", filename="podcast.wav")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
