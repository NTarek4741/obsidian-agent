"""Transcription orchestrator.

Thin wrapper around run_persona for cleaning up transcripts into polished
Obsidian markdown notes.
"""

from pathlib import Path

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.tools import vault_append, vault_read, vault_write

_HERE = Path(__file__).parent
TRANSCRIBE_TASK = (_HERE / "system_prompts" / "system_prompt.md").read_text(encoding="utf-8")


TRANSCRIBE = Persona(
    id="transcribe",
    name="Transcription Cleaner",
    description="Clean up a raw transcript into a polished Obsidian note.",
    model="anthropic/claude-haiku-4-5-20251001",
    system_prompt=TRANSCRIBE_TASK,
    tools=[vault_read, vault_write, vault_append],
    mcp_servers=[],
    temperature=0.1,
    max_steps=5,
    max_tokens=16384,
)


async def transcribe_orchestration(messages: list):
    """Run the transcription cleaner on a set of messages."""
    return await run_persona(TRANSCRIBE, messages)


from pathlib import Path
from obsidian_agent.orchestrator.transcription.pipelines import (
    transcribe_youtube,
    transcribe_file,
    transcribe_live,
)

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


async def transcribe_auto(content: str | bytes, on_progress=None) -> object:
    """Auto-detect input type, run the matching pipeline, then orchestrate into an Obsidian note."""

    def emit(msg: str):
        if on_progress:
            on_progress(msg)

    if isinstance(content, bytes):
        emit("● Transcribing live recording…")
        data = await transcribe_live(content)
    elif any(h in content for h in _YOUTUBE_HOSTS):
        emit("● Fetching YouTube transcript…")
        data = await transcribe_youtube(content)
        emit(f"● {data['source_info'].splitlines()[0]}")
    else:
        p = Path(content.strip())
        emit(f"● Transcribing: {p.name}")
        transcript = await transcribe_file(content)
        data = {"transcript": transcript, "source_info": f"File: {p.name}"}

    emit("● Running AI agent…")
    msg_content = f"{data['source_info']}\n\n{data['transcript']}"
    messages = [{"role": "user", "content": msg_content}]
    return await run_persona(TRANSCRIBE, messages)
