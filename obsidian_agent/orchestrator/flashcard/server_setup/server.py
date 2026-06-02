"""FastAPI server that runs a Dedalus SDK agent to generate Anki flashcard decks.

This file is deployed to /home/machine/flashcard_app/server.py on the VM.

Endpoints:
    GET  /health
        Health check — returns 200 when server is alive.
    POST /make-flashcard
        Body: {"note_content": "<markdown note>"}
        Auth: Bearer <DEDALUS_API_KEY>
        Response: application/octet-stream (.apkg Anki deck)

Architecture:
    A `DedalusRunner` agent has three Python tools available in this VM env
    (which has python + genanki installed):
      * write_file(path, content)        → writes a genanki script
      * run_python(script, output_apkg)  → executes the script
      * deliver_apkg(apkg_path)          → marks the final artifact
"""

import os
import subprocess
import sys
import tempfile
import traceback

import uvicorn
from dedalus_labs import AsyncDedalus, DedalusRunner
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.environ.get("DEDALUS_API_KEY", "")
MODEL = "anthropic/claude-sonnet-4-20250514"

FLASHCARD_SYSTEM_PROMPT = """SYSTEM_PROMPT_PLACEHOLDER"""

MAX_AGENT_STEPS = 20
MAX_OUTPUT_CHARS = 2000


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


# ---------------------------------------------------------------------------
# Agent tools — module-level, stateless. The endpoint recovers the final
# apkg path from runner.run()'s tool_results, so no shared state is needed.
# ---------------------------------------------------------------------------


def write_file(path: str, content: str) -> dict:
    """Write `content` to `path` as a UTF-8 text file.

    Returns the absolute path and byte count of the written file.
    Use this to write the genanki Python script before running it.
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        size = os.path.getsize(path)
        print(f"  write_file: {path} ({size} bytes)")
        return {"path": path, "bytes": size}
    except OSError as exc:
        return {"error": f"write_file failed: {exc}"}


def run_python(script_path: str, output_apkg_path: str) -> dict:
    """Execute a genanki script via `python script_path output_apkg_path`.

    The script must accept the output path as its first argv and call
    `genanki.Package([deck]).write_to_file(output_apkg_path)`. Returns
    returncode, truncated stdout, and truncated stderr.
    """
    try:
        result = subprocess.run(
            [sys.executable, script_path, output_apkg_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"error": "script timed out after 60s"}
    except OSError as exc:
        return {"error": f"run_python failed to spawn: {exc}"}

    print(
        f"  run_python: rc={result.returncode} "
        f"stdout={len(result.stdout)}b stderr={len(result.stderr)}b"
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[:MAX_OUTPUT_CHARS],
        "stderr": result.stderr[:MAX_OUTPUT_CHARS],
    }


def deliver_apkg(apkg_path: str) -> dict:
    """Mark the .apkg at `apkg_path` as the final deck to ship to the user.

    Validates the file exists and is non-empty. Call this exactly once,
    after run_python has produced a working .apkg.
    """
    if not apkg_path or not os.path.exists(apkg_path):
        return {"error": f"apkg_path does not exist: {apkg_path}"}
    size = os.path.getsize(apkg_path)
    if size == 0:
        return {"error": f"apkg_path is empty: {apkg_path}"}
    print(f"  deliver_apkg: {apkg_path} ({size} bytes)")
    return {"status": "ok", "apkg_path": apkg_path, "size_bytes": size}


TOOLS = [write_file, run_python, deliver_apkg]


def _extract_delivered_path(tool_results: list[dict]) -> str | None:
    """Find the apkg_path captured by the agent's deliver_apkg call."""
    for entry in tool_results:
        if entry.get("name") != "deliver_apkg":
            continue
        result = entry.get("result")
        if isinstance(result, dict) and "apkg_path" in result:
            return result["apkg_path"]
    return None


# ---------------------------------------------------------------------------
# HTTP endpoint — just runs the agent
# ---------------------------------------------------------------------------


@app.post("/make-flashcard")
async def make_flashcard(request: FlashcardRequest, token: str = Depends(verify_auth)):
    print(f"Received flashcard request ({len(request.note_content)} chars)")

    workdir = tempfile.mkdtemp(prefix="flashcard_")
    suggested_apkg = os.path.join(workdir, "deck.apkg")
    user_prompt = (
        "Generate an Anki flashcard deck from the following note. Extract every "
        "key concept, definition, fact, and relationship as a Q&A pair.\n\n"
        f"Suggested working paths:\n"
        f"  script_path = {workdir}/build_deck.py\n"
        f"  output_apkg_path = {suggested_apkg}\n\n"
        f"NOTE CONTENT:\n{request.note_content}"
    )

    try:
        client = AsyncDedalus(api_key=API_KEY)
        runner = DedalusRunner(client)
        result = await runner.run(
            model=MODEL,
            instructions=FLASHCARD_SYSTEM_PROMPT,
            input=user_prompt,
            tools=TOOLS,
            max_steps=MAX_AGENT_STEPS,
            temperature=0.3,
            max_tokens=4096,
        )
    except HTTPException:
        raise
    except Exception as exc:
        msg = f"Error in make_flashcard: {exc}\n{traceback.format_exc()}"
        print(msg)
        raise HTTPException(status_code=500, detail=msg)

    final_path = _extract_delivered_path(result.tool_results)
    if not final_path:
        raise HTTPException(status_code=500, detail="Agent did not deliver an .apkg")

    print(f"Serving: {final_path}")
    return FileResponse(
        final_path,
        media_type="application/octet-stream",
        filename="flashcards.apkg",
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
