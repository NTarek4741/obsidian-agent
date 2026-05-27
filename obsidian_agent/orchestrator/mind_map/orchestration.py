"""Mind map orchestrator.

One-shot mind map generation with web research.
Reads a note, researches sources, generates a radial mind map,
and appends sources + learning path to the original note.
"""

import json
import re
from pathlib import Path

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.orchestrator.utility import _ensure_vault_path
from obsidian_agent.tools import (
    _resolve,
    vault_read,
    vault_search,
    vault_write,
    vault_write_canvas,
)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
MIND_MAP_TASK = (_HERE / "system_prompts" / "system_prompt.md").read_text(encoding="utf-8")

MIND_MAP_AGENT = Persona(
    id="mind_map_agent",
    name="Mind Map Agent",
    description="Transform notes into radial mind maps with research-backed sources and learning paths.",
    model="anthropic/claude-sonnet-4-5-20250929",
    system_prompt=MIND_MAP_TASK,
    tools=[
        vault_read,
        vault_write,
        vault_write_canvas,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.2,
    max_steps=20,
    max_tokens=16384,
)


# ---------------------------------------------------------------------------
# Orchestration functions
# ---------------------------------------------------------------------------


async def mind_map_generate(note_path: str, note_content: str) -> dict:
    """
    One-shot mind map generation with research.

    Args:
        note_path: Path to the original note (for refinement)
        note_content: Full markdown content of the note

    Returns:
        {
            "result": object,         # raw result from agent
            "mind_map_path": str | None,
            "mind_map_data": dict | None,
            "sources_found": list,    # list of source dicts
            "note_refined": bool,
            "error": str | None,
        }
    """
    vault_path = _ensure_vault_path()

    messages = [
        {
            "role": "user",
            "content": (
                f"Create a mind map from this note and research deeper sources.\n\n"
                f"**Original Note Path:** {note_path}\n\n"
                f"**Note Content:**\n```markdown\n{note_content}\n```\n\n"
                f"Instructions:\n"
                f"1. Research the topic using web search to find books, papers, and courses.\n"
                f"2. Build a radial mind map canvas visualizing the note's concepts.\n"
                f"3. Append a '## Mind Map Sources & Learning Path' section to the original note.\n"
                f"4. Save the mind map as a .canvas file with the same name as the note (e.g., '{Path(note_path).stem}.canvas')."
            ),
        }
    ]

    result = await run_persona(MIND_MAP_AGENT, messages)

    # Extract mind map data from tool results
    mind_map_data = None
    mind_map_path = None
    for tr in result.tool_results:
        if tr.get("name") == "vault_write_canvas":
            args = tr.get("arguments", {})
            mind_map_data = args.get("canvas_data")
            mind_map_path = args.get("path")
            if mind_map_data is None:
                result_str = str(tr.get("result", ""))
                if "Success" in result_str:
                    m = re.search(r'"([^"]+\.canvas)"', result_str)
                    if m:
                        mind_map_path = m.group(1)
                        try:
                            target = _resolve(vault_path, mind_map_path)
                            mind_map_data = json.loads(
                                target.read_text(encoding="utf-8")
                            )
                        except Exception:
                            pass
            break

    # Check if note was refined (vault_write to the original path)
    note_refined = False
    for tr in result.tool_results:
        if tr.get("name") == "vault_write":
            args = tr.get("arguments", {})
            written_path = args.get("path", "")
            if (
                written_path == note_path
                or Path(written_path).name == Path(note_path).name
            ):
                note_refined = True
                break

    # Extract sources from the agent's output or tool results
    sources_found = []
    try:
        # Try to find sources in the final output
        output = str(result.final_output)
        # Look for source patterns
        source_patterns = re.findall(
            r"\*\*([^*]+)\*\*\s*[-–]\s*([^\n]+)(?:\n|$)", output
        )
        for title, desc in source_patterns:
            if len(title) > 3 and len(desc) > 5:
                sources_found.append(
                    {"title": title.strip(), "description": desc.strip()}
                )
    except Exception:
        pass

    error = None
    if not mind_map_data:
        error = "No mind map found in results."

    return {
        "result": result,
        "mind_map_path": mind_map_path,
        "mind_map_data": mind_map_data,
        "sources_found": sources_found,
        "note_refined": note_refined,
        "error": error,
    }
