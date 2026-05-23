"""Transcription orchestrator.

Thin wrapper around run_persona for cleaning up transcripts into polished
Obsidian markdown notes.
"""

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.tools import vault_append, vault_read, vault_write

_BUILD_CONSTRAINTS = """<CRITICAL_CONSTRAINTS mode="build">
BUILD MODE ACTIVE — EXECUTION PHASE

COMPLETION MANDATE:
You MUST iterate until the problem is fully solved. ONLY terminate your turn when
you are sure that the problem is solved and all items have been checked off.
Go through the problem step by step and verify your changes are correct.
NEVER end your turn without having truly and completely solved the problem.

EXECUTION RULES:
1. SELF-CHECK AFTER EVERY TOOL CALL: After every tool call, assess whether the task
   is fully done. If not, make another tool call immediately. Do NOT stop until done.
2. VERIFY BEFORE DECLARING DONE: Before reporting completion, verify every planned
   file exists with non-empty content by reading it back with vault_read.
3. RETRY MANDATE: If ANY tool returns "Error:", retry the SAME tool with corrected
   parameters. Do NOT switch tools. Do NOT give up. Fix the error and retry.
4. PROGRESS TRACKING: Count remaining items to create or fix. If count > 0, continue.
5. NEVER STOP HALFWAY: If you started creating files, you MUST create ALL of them.
   Partial completion is a critical failure.
6. ZERO PLANNING TEXT: Do NOT say "I will", "Let me", "Here is", or "First I will".
   Your first output should be a tool call, not explanatory text.
7. TOOL CALLS ONLY: You communicate through tool calls. If not invoking a tool, you are failing.

VIOLATION: Stopping before completion, skipping verification, or outputting planning
           text instead of tool calls is a critical failure.
</CRITICAL_CONSTRAINTS>"""

TRANSCRIBE_TASK = _BUILD_CONSTRAINTS + """

## YOUR TASK
You receive a pre-formatted transcript with timestamps. Your job is to turn it into a single, polished, one-page Obsidian markdown note with proper citations.

## ANALYZE & STRUCTURE
1. Read the entire transcript to understand the main topic and flow.
2. Identify natural topic shifts using timestamps.
3. Plan a clear structure with headings (#, ##, ###) for the single note.

## OUTPUT FORMAT
- YAML frontmatter:
  ```yaml
  ---
  title: "Descriptive Title"
  tags: [transcription, topic]
  date_created: YYYY-MM-DD
  source_type: youtube | audio | live
  source_duration: "MM:SS" or "HH:MM:SS"
  ---
  ```
- Use `#` for the main title, `##` and `###` for sections and subsections.
- Group content under sections with timestamp ranges as anchors:
  ```markdown
  ## Core Argument: Neural Networks as Representation Learners
  *[00:04:32 - 00:08:15]*
  ```
- Remove filler words (um, uh, like, you know) and false starts.
- Preserve timestamps as section anchors — do NOT strip them.
- Use bullet points for lists.
- Add a `## Key Takeaways` section at the bottom with 3-5 bullet points.
- Add a `## Sources` section at the bottom:
  - If the input includes a YouTube URL, cite it:
    ```markdown
    ## Sources
    - [Video Title](URL) — Channel Name
    ```
  - If the input includes an audio file path or live recording name, cite it:
    ```markdown
    ## Sources
    - Audio file: path/to/recording.wav
    ```
- Use **bold** for emphasis, `code` for technical terms.

## CITATION RULE
Timestamps are your ONLY source of truth. Do NOT fabricate content not in the transcript.
When summarizing or paraphrasing, anchor claims to the nearest timestamp range.

## NO RESEARCH RULE
Do NOT search the web. Do NOT search the vault for related notes. The transcript IS the source.

## EMERGENCY FALLBACK — USE IF TRANSCRIPT IS EMPTY OR UNUSABLE
If the transcript is literally empty, whitespace-only, or completely unintelligible:
1. Still create exactly ONE note.
2. Title it something like "Untranscribed Audio — <topic if known>".
3. In the body, write a brief explanation: "The audio file did not produce a usable transcript. Possible causes: silence, background noise, unsupported language, or file corruption."
4. Include the original filename or any metadata you have.
5. Add a `## Troubleshooting` section with suggestions.

## EXECUTION MANDATE
- You MUST create exactly ONE markdown file.
- You are NOT done until the file exists and has been verified with vault_read.
- Use vault_write to create the note. Choose a clear filename based on the title.
- Verify the file with vault_read. Report what you created."""


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
