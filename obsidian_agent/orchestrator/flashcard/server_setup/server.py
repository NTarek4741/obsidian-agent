"""FastAPI server that runs on a Dedalus Machine to generate Anki flashcard decks.

This file is deployed to /home/machine/flashcard_app/server.py on the VM.

Endpoints:
    GET  /health
        Health check — returns 200 when server is alive.
    POST /make-flashcard
        Body: {"note_content": "<markdown note>"}
        Auth: Bearer <DEDALUS_API_KEY>
        Response: application/octet-stream (.apkg Anki deck)
"""

import os
import subprocess
import sys
import tempfile
import traceback

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ.get("DEDALUS_API_KEY", "")
DEDALUS_API_URL = "https://api.dedaluslabs.ai/v1/chat/completions"

FLASHCARD_SYSTEM_PROMPT = """You are a Python code generator. Your ONLY output is a complete, executable Python script.
No markdown fences. No explanation. No text before or after the script.

The script MUST:
1. Import genanki and sys at the top level
2. Read: output_path = sys.argv[1]
3. Create a genanki.Model with:
   - A fixed large integer model_id (use 1607392319)
   - A meaningful name
   - fields=[{"name": "Question"}, {"name": "Answer"}]
   - templates=[{"name": "Card 1", "qfmt": "{{Question}}", "afmt": "{{FrontSide}}<hr id=answer>{{Answer}}"}]
4. Create a genanki.Deck with:
   - A fixed large integer deck_id (use 2059400110)
   - A meaningful name derived from the note content
5. For EACH concept, definition, fact, term, or relationship in the note:
   - Create a genanki.Note(model=model, fields=[question_string, answer_string])
   - deck.add_note(note)
6. genanki.Package([deck]).write_to_file(output_path)
7. Generate a MINIMUM of 10 cards and a MAXIMUM of 40 cards
8. Do NOT call sys.exit(). Do NOT use if __name__ == "__main__". The script runs top-level.
9. Questions should be specific and testable. Answers should be concise but complete."""


class FlashcardRequest(BaseModel):
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
                "temperature": 0.3,
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


def _strip_fences(code: str) -> str:
    """Remove markdown code fences if the LLM accidentally included them."""
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        start = 1
        end = len(lines)
        if lines[-1].strip() == "```":
            end = len(lines) - 1
        code = "\n".join(lines[start:end])
    return code.strip()


def _run_genanki_script(note_content: str) -> str:
    """Use LLM to generate a genanki script, execute it, return path to .apkg.

    The LLM writes the Python script; we run it as a subprocess so genanki
    (already installed in the venv) creates the Anki deck file directly.
    """
    user_prompt = (
        "Generate a genanki flashcard script for the following note. "
        "Extract all key concepts, definitions, facts, and relationships as Q&A pairs.\n\n"
        f"NOTE CONTENT:\n{note_content}"
    )

    print("[server] Requesting genanki script from LLM...")
    script_code = _call_llm(FLASHCARD_SYSTEM_PROMPT, user_prompt)
    script_code = _strip_fences(script_code)
    print(f"[server] Script received ({len(script_code)} chars)")

    # Write generated script to a temp file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(script_code)
        script_path = f.name

    apkg_path = tempfile.mktemp(suffix=".apkg")

    try:
        print(f"[server] Executing generated script: {script_path}")
        result = subprocess.run(
            [sys.executable, script_path, apkg_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise RuntimeError(
            f"Generated script failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}\n"
            f"--- Script (first 1000 chars) ---\n{script_code[:1000]}"
        )

    if not os.path.exists(apkg_path) or os.path.getsize(apkg_path) == 0:
        raise RuntimeError(
            "Generated script ran but produced no .apkg file. "
            f"stdout: {result.stdout[:300]}"
        )

    print(f"[server] .apkg created: {apkg_path} ({os.path.getsize(apkg_path)} bytes)")
    return apkg_path


@app.post("/make-flashcard")
async def make_flashcard(request: FlashcardRequest, token: str = Depends(verify_auth)):
    print(f"[server] Received flashcard request ({len(request.note_content)} chars)")

    try:
        apkg_path = _run_genanki_script(request.note_content)
        print(f"[server] Serving: {apkg_path}")
        return FileResponse(
            apkg_path,
            media_type="application/octet-stream",
            filename="flashcards.apkg",
        )

    except HTTPException:
        raise
    except Exception as exc:
        error_msg = f"[server] Error in make_flashcard: {exc}\n{traceback.format_exc()}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
