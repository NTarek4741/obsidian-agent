"""FastAPI server that runs on a Dedalus Machine to generate podcasts.

This file is deployed to /home/machine/podcast_app/server.py on the VM.

Endpoints:
    GET  /health
        Health check — returns 200 when server is alive.
    POST /generate-podcast
        Body: {"note_content": "<markdown note>"}
        Auth: Bearer <DEDALUS_API_KEY>
        Response: audio/mp4 file (M4A podcast)
"""

import os
import subprocess
import tempfile
import traceback

import numpy as np
import uvicorn
import requests
import soundfile as sf
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from kokoro import KPipeline
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ.get("DEDALUS_API_KEY", "")
DEDALUS_API_URL = "https://api.dedaluslabs.ai/v1/chat/completions"

# Fixed podcast hosts — no AI choice
HOST_A_VOICE = "af_bella"
HOST_B_VOICE = "am_adam"


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


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call Dedalus LLM API via OpenAI-compatible endpoint."""
    print(f"[server] Calling Dedalus LLM API (model: anthropic/claude-sonnet-4-20250514)...")
    try:
        response = requests.post(
            DEDALUS_API_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "anthropic/claude-sonnet-4-20250514",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.8,
                "max_tokens": 4096,
            },
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        print(f"[server] LLM response received ({len(data)} top-level keys)")
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as exc:
        print(f"[server] ERROR: LLM API request failed: {exc}")
        if hasattr(exc, 'response') and exc.response is not None:
            print(f"[server] Response status: {exc.response.status_code}")
            print(f"[server] Response body: {exc.response.text[:500]}")
        raise
    except (KeyError, IndexError) as exc:
        print(f"[server] ERROR: Unexpected LLM response format: {exc}")
        raise


def _generate_script(note_content: str) -> list[tuple[str, str]]:
    """Generate podcast script via Dedalus API.
    
    Returns: lines — each is (speaker, text).
    Host A is always af_bella, Host B is always am_adam.
    """
    print("[server] Generating script via Dedalus...")
    
    system = """You are an expert podcast producer. Create a NotebookLM-style conversational podcast from the provided note.

FORMAT YOUR RESPONSE EXACTLY LIKE THIS — no other text:

HOST_A: <line of dialogue>
HOST_B: <line of dialogue>
HOST_A: <line of dialogue>
HOST_B: <line of dialogue>
...etc

RULES:
- Each line must start with HOST_A: or HOST_B:
- Make it conversational, warm, and engaging
- 30-60 lines total (about 5-10 minutes when spoken)
- Host A (Bella) asks questions and shows curiosity
- Host B (Adam) explains clearly with enthusiasm
- Build on each other's points
- End with a memorable takeaway"""

    text = _call_llm(system, f"Create a podcast from this note:\n\n{note_content}")
    print(f"[server] Script received ({len(text)} chars)")
    
    # Parse dialogue lines
    lines = []
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('HOST_A:'):
            lines.append(('A', line[7:].strip()))
        elif line.startswith('HOST_B:'):
            lines.append(('B', line[7:].strip()))
    
    print(f"[server] Parsed {len(lines)} dialogue lines")
    return lines


def _generate_audio(lines: list[tuple[str, str]]) -> str:
    """Generate audio from script lines using Kokoro with fixed voices.
    
    Host A = af_bella, Host B = am_adam.
    Returns: path to output WAV file.
    """
    print("[server] Generating audio with Kokoro...")
    
    # Check disk space before downloading model weights
    try:
        stat = os.statvfs('/')
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        print(f"[server] Disk space before Kokoro init: {free_gb:.1f} GB free")
        if free_gb < 1.0:
            print("[server] WARNING: Low disk space, model download may fail")
    except Exception:
        pass
    
    try:
        pipeline = KPipeline(lang_code='a')
        print("[server] Kokoro pipeline initialized")
    except Exception as exc:
        print(f"[server] ERROR: Failed to initialize Kokoro pipeline: {exc}\n{traceback.format_exc()}")
        raise
    
    all_audio = []
    sample_rate = 24000
    
    for i, (speaker, text) in enumerate(lines):
        voice = HOST_A_VOICE if speaker == 'A' else HOST_B_VOICE
        print(f"[server]  Line {i+1}/{len(lines)} ({speaker}, {voice}): {text[:60]}...")
        
        try:
            generator = pipeline(
                text,
                voice=voice,
                speed=1.0,
                split_pattern=r'\n+',
            )
            
            for _, _, audio in generator:
                if audio is not None and len(audio) > 0:
                    all_audio.append(audio)
        except Exception as exc:
            print(f"[server]  Warning: failed to generate audio for line {i+1}: {exc}")
            continue
    
    if not all_audio:
        raise RuntimeError("No audio was generated")
    
    # Concatenate
    print(f"[server] Concatenating {len(all_audio)} segments...")
    final = np.concatenate(all_audio)
    
    wav_path = tempfile.mktemp(suffix='.wav')
    sf.write(wav_path, final, sample_rate)
    print(f"[server] WAV saved: {wav_path} ({os.path.getsize(wav_path)} bytes)")
    
    return wav_path


def _convert_to_m4a(wav_path: str) -> str:
    """Convert WAV to M4A using ffmpeg."""
    print("[server] Converting to M4A with ffmpeg...")
    m4a_path = tempfile.mktemp(suffix='.m4a')
    
    result = subprocess.run(
        [
            'ffmpeg', '-y', '-i', wav_path,
            '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart',
            m4a_path,
        ],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    
    # Clean up WAV
    os.unlink(wav_path)
    
    print(f"[server] M4A saved: {m4a_path} ({os.path.getsize(m4a_path)} bytes)")
    return m4a_path


@app.post("/generate-podcast")
async def generate_podcast(request: PodcastRequest, token: str = Depends(verify_auth)):
    print(f"[server] Received podcast request ({len(request.note_content)} chars)")
    
    try:
        # 1. Generate script
        lines = _generate_script(request.note_content)
        
        if not lines:
            raise HTTPException(status_code=500, detail="No dialogue lines generated")
        
        # 2. Generate audio (fixed voices: af_bella + am_adam)
        wav_path = _generate_audio(lines)
        
        # 3. Convert to M4A
        m4a_path = _convert_to_m4a(wav_path)
        
        # 4. Return
        print(f"[server] Serving: {m4a_path}")
        return FileResponse(
            m4a_path,
            media_type="audio/mp4",
            filename="podcast.m4a",
        )
        
    except HTTPException:
        raise
    except Exception as exc:
        error_msg = f"[server] Error in generate_podcast: {exc}\n{traceback.format_exc()}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
