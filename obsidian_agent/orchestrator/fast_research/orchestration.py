"""Fast research orchestrator.

Thin wrapper around run_persona for researching a topic and creating a single
comprehensive wiki-style markdown note.
"""

from pathlib import Path

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.tools import (
    vault_append,
    vault_delete,
    vault_exists,
    vault_get_backlinks,
    vault_get_links,
    vault_get_metadata,
    vault_get_tags,
    vault_grep,
    vault_list,
    vault_read,
    vault_search,
    vault_write,
    vault_write_canvas,
)

_HERE = Path(__file__).parent
GENERATE_NOTE_TASK = (_HERE / "system_prompts" / "system_prompt.md").read_text(encoding="utf-8")


GENERATE_NOTE = Persona(
    id="fast_research",
    name="Wiki Note Generator",
    description="Research a topic and create a single comprehensive markdown note.",
    model="anthropic/claude-haiku-4-5-20251001",
    system_prompt=GENERATE_NOTE_TASK,
    tools=[
        vault_read,
        vault_write,
        vault_append,
        vault_delete,
        vault_list,
        vault_exists,
        vault_search,
        vault_grep,
        vault_get_metadata,
        vault_get_tags,
        vault_get_links,
        vault_get_backlinks,
        vault_write_canvas,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=15,
    max_tokens=8192,
)


async def fast_research_orchestration(messages: list):
    """Run fast research on a set of messages."""
    return await run_persona(GENERATE_NOTE, messages)
