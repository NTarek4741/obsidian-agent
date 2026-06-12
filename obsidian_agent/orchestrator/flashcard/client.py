"""Flashcard client — thin async client over the ephemeral machine lifecycle.

The flashcard agent writes and executes model-generated Python (genanki
scripts), so it runs on a throwaway sandbox: obsidian_agent.machine creates
a fresh VM for the job, deploys + bootstraps the server, and destroys the
machine when the `with` block exits — success or failure. Nothing persists
and nothing is reused between jobs.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from api.utils import Job
from obsidian_agent.machine import ephemeral_machine, get_spec, request, tail_server_log
from obsidian_agent.orchestrator.config import _ensure_vault_path


async def generate_flashcards(note_path: str, note_content: str, job: Job) -> dict:
    """Generate an Anki deck on a single-job sandbox machine.

    Every run: create VM + setup (~2-3 min), run the code-writing agent,
    download the deck, destroy the VM. The cold start is the price of the
    sandbox story — untrusted generated code never touches a long-lived
    environment.
    """
    log = job.progress.append

    def _run() -> bytes:
        with ephemeral_machine(get_spec("flashcard"), log=log) as handle:
            try:
                log(f"● Sending note ({len(note_content)} chars)…")
                response = request(
                    handle, "POST", "/make-flashcard",
                    json_body={"note_content": note_content}, log=log,
                )
                log(f"● Received .apkg ({len(response.content)} bytes)")
                return response.content
            except Exception as exc:
                log(f"● Flashcard generation failed: {exc}")
                tail_server_log(handle, log=log)  # before the sandbox is destroyed
                raise

    deck = await asyncio.to_thread(_run)

    agent_dir = Path(_ensure_vault_path())
    output_dir = agent_dir / "flashcards"
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = Path(note_path).stem
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{stem}_{timestamp}.apkg"
    filepath = output_dir / filename
    filepath.write_bytes(deck)
    log(f"● Saved to: {filepath}")

    return {
        "filepath": str(filepath),
        "filename": filename,
        "size_bytes": len(deck),
        "ephemeral": True,
    }
