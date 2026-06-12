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
    Script-first: a `DedalusRunner` agent writes the complete podcast script
    as clean spoken prose, then makes exactly one tool call —
      * generate_audio(script) → cleans the script, synthesizes the whole
        thing with Kokoro (chunked internally on paragraph breaks), returns
        the final wav_path.
    TTS is deterministic post-processing of the agent's output; the agent
    never loops over clips, so the job costs ~3 model steps instead of ~50.
    The single host voice is `af_bella`.
"""

import os
import re
import tempfile
import traceback
from pathlib import Path

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

# Deployed alongside this file by the machine.py bundle.
PODCAST_SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

HOST_VOICE = "af_bella"
SAMPLE_RATE = 24000
MAX_AGENT_STEPS = 8  # script generation + one generate_audio call + retry headroom

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


# ---------------------------------------------------------------------------
# Script cleanup — deterministic defense-in-depth before TTS
# ---------------------------------------------------------------------------

# Square brackets are never spoken prose: [pause], [BEAT], [music swells]...
_BRACKET_DIRECTION_RE = re.compile(r"\[[^\]]{0,60}\]")
# Parentheses CAN carry spoken content ("(ICAO)"), so only strip known
# stage-direction vocabulary.
_PAREN_DIRECTION_RE = re.compile(
    r"\((?:beat|pause[sd]?|laugh(?:s|ing|ter)?|chuckl\w*|sigh\w*|music[^)]*|"
    r"sound[^)]*|sfx[^)]*)\)",
    re.IGNORECASE,
)
# Speaker labels at line start: "Host:", "HOST —", "Narrator -"
_SPEAKER_LABEL_RE = re.compile(
    r"^\s*(?:host|narrator|speaker(?: \d+)?)\s*[:—–-]\s*", re.IGNORECASE | re.MULTILINE
)
# Markdown the TTS should never see: headings, emphasis, backticks
_MARKDOWN_RE = re.compile(r"^#{1,6}\s+|[*_`]+", re.MULTILINE)


def _clean_script(script: str) -> str:
    """Strip anything that is not words the host says aloud.

    The system prompt demands clean prose; this catches what slips through.
    Paragraph breaks are preserved — they are the pacing unit (Kokoro splits
    on newlines via split_pattern).
    """
    text = _BRACKET_DIRECTION_RE.sub("", script or "")
    text = _PAREN_DIRECTION_RE.sub("", text)
    text = _SPEAKER_LABEL_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


# ---------------------------------------------------------------------------
# Agent tool — the single deterministic post-processing step. The endpoint
# recovers the final wav path from runner.run()'s tool_results.
# ---------------------------------------------------------------------------


def generate_audio(script: str) -> dict:
    """Synthesise the complete podcast script into the final WAV.

    Pass the FULL script as clean spoken prose: one paragraph per beat,
    blank lines between beats, no speaker labels, stage directions, pause
    markers, or markdown — only the words the host says aloud. Call this
    exactly once; it returns the final wav_path and duration_s.
    """
    cleaned = _clean_script(script)
    if not cleaned:
        return {"error": "script is empty after cleanup"}
    try:
        wav_path, duration_s = _synthesize_to_wav(cleaned)
        size = os.path.getsize(wav_path)
        print(
            f"  generate_audio: {len(cleaned)} chars → {wav_path} "
            f"({duration_s}s, {size} bytes)"
        )
        return {"wav_path": wav_path, "duration_s": duration_s, "bytes": size}
    except Exception as exc:
        print(f"  generate_audio FAIL: {exc}")
        return {"error": f"synthesis failed: {exc}"}


TOOLS = [generate_audio]


def _extract_delivered_path(tool_results: list[dict]) -> str | None:
    """Find the wav_path produced by the agent's generate_audio call."""
    for entry in tool_results:
        if entry.get("name") != "generate_audio":
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
