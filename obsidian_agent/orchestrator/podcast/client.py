"""Podcast client — thin async client over the shared machine lifecycle.

obsidian_agent.machine owns the VM (find/wake/create/deploy/preview); this
module just sends the note to the podcast server and saves the returned audio,
reporting progress into the local Job.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from api.utils import Job
from obsidian_agent.machine import ensure_machine, get_spec, request, tail_server_log
from obsidian_agent.config import _ensure_vault_path


async def generate_podcast(note_path: str, note_content: str, job: Job) -> dict:
    """Generate a podcast using the persistent podcast machine.

    First call:        create VM, run full setup (~12 min), generate audio.
    Warm reuse:        machine still running, generate audio immediately.
    After idle window: Dedalus auto-slept the VM; wake (~5 min), generate audio.
    """
    log = job.progress.append
    handle = await asyncio.to_thread(ensure_machine, get_spec("podcast"), log=log)

    try:
        log(f"● Sending note ({len(note_content)} chars)…")
        response = await asyncio.to_thread(
            request, handle, "POST", "/generate-podcast",
            json_body={"note_content": note_content}, log=log,
        )

        log(f"● Received audio ({len(response.content)} bytes)")
        agent_dir = Path(_ensure_vault_path())
        output_dir = agent_dir / "podcasts"
        output_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(note_path).stem
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{timestamp}.wav"
        filepath = output_dir / filename
        filepath.write_bytes(response.content)
        log(f"● Saved to: {filepath}")

        return {
            "filepath": str(filepath),
            "filename": filename,
            "size_bytes": len(response.content),
            "machine_id": handle.machine_id,
            "reused_machine": handle.reused,
        }

    except Exception as exc:
        log(f"● Podcast generation failed: {exc}")
        await asyncio.to_thread(tail_server_log, handle, log=log)
        raise
