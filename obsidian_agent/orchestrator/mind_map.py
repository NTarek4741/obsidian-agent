"""Mind map orchestrator.

One-shot mind map generation with web research.
Reads a note, researches sources, generates a radial mind map,
and appends sources + learning path to the original note.
"""

import json
import re
from pathlib import Path

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.config import _ensure_vault_path
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

MIND_MAP_TASK = (
    _BUILD_CONSTRAINTS
    + """

## YOUR TASK
Transform the provided markdown note into a visual mind map, using the note as the SOURCE OF TRUTH.

## SOURCE OF TRUTH RULE
The note content is PRIMARY and AUTHORITATIVE. All concepts in the mind map MUST come from the note.
Web research is ONLY for finding sources to learn more — never for replacing or contradicting the note.

## INPUT
The user message contains the FULL markdown note. Read it carefully.

## STEP 1: UNDERSTAND THE NOTE
Extract all key concepts, relationships, and hierarchies from the note itself.
The mind map structure must faithfully represent what's in the note.

## STEP 2: RESEARCH SOURCES ONLY (Use MCP tools)
After understanding the note, find external sources for deeper learning:

1. Use brave-search-mcp to perform 1-2 web searches for books/papers on the topic.
2. Use defuddle-mcp to read 1 high-quality page for source recommendations.
3. Use exa to find 1 academic source if available.

**Search strategy:**
- Search for "best books on [topic]"
- Search for "[topic] learning path"

Collect:
- 3-5 recommended books/papers/courses (titles + 1-line descriptions)
- A 3-stage learning progression (Foundation → Application → Mastery)
- 1-2 key insights that DEEPEN (not replace) what's in the note

## STEP 2: BUILD THE MIND MAP
Call `vault_write_canvas` with `canvas_data={"nodes": [...], "edges": [...]}`.

### RADIAL MIND MAP LAYOUT
**ULTRA-GENEROUS SPACING. NO OVERLAPS. NO CROSSING EDGES. PERFECT FLOW.**

**CRITICAL RULES (VIOLATE = FAILURE):**
- **STRICT MAX SIZES**: No node may exceed its max width/height. EVER.
- **NO EDGE CROSSING**: If two edges cross, move nodes until they don't.
- **FAN-OUT RULE**: When one node connects to 3+ children, each child edge MUST originate from a DIFFERENT side of the parent (bottom, right, left, top). Never all from the same side.
- **SIBLING GAP**: Nodes at the same level must have ≥300px gap between them.
- **CANVAS SIZE**: Use coordinates up to 6000x4000. Spread everything out.

---

1. **CENTRAL NODE** at (0, 0):
   - Size: **width 500, height 160** (STRICT MAX)
   - Color: "3" (Yellow)
   - Text: Topic title (1 line) + subtitle (1 line). Short phrases only.

2. **MAIN BRANCHES** (4-6 branches):
   - Distance from center: **1000-1200px**
   - Size: **width 360, height 110** (STRICT MAX)
   - Position angles: 30deg, 90deg, 150deg, 210deg, 270deg, 330deg
   - **Each branch gets a unique color**
   - **Gap between adjacent main branches: ≥400px**
   - Edge from center: fromSide="bottom" or "right" or "left" (pick closest), toSide="top"

3. **SUB-BRANCHES** (2-3 per main branch, MAX 3):
   - Distance from parent main branch: **700-800px**
   - Size: **width 320, height 100** (STRICT MAX)
   - Same color as parent
   - Arranged in an arc around the parent, NOT in a straight line
   - **Gap between sub-branches: ≥350px**
   - Edge: fromSide on parent = side facing the child, toSide="top" or "left" or "right"

4. **DETAIL NODES** (0-2 per sub-branch, MAX 2):
   - Distance from parent sub-branch: **600-700px**
   - Size: **width 280, height 90** (STRICT MAX)
   - Same color as parent branch
   - **Gap between detail nodes: ≥300px**
   - Text: 1-2 lines maximum. Keywords only.

5. **SOURCES GROUP** (separate area, x: **2500**, y: **-500** to **500**):
   - Group label: "Sources & Next Steps"
   - Group size: width 1800, height 1200
   - Source nodes inside: **width 400, height 100** (STRICT MAX)
   - Source nodes stacked vertically with **250px gap**
   - Color: "6" (Purple) for all sources
   - **NO connection edges between sources and the main mind map** — sources are standalone reference nodes

### EDGE ROUTING RULES (CRITICAL)
- **Parent → Child**: Direct straight line. NO curves.
- **NO edge may pass through any node** (not source, not target, not unrelated).
- **If edges cross**: Move one of the nodes 200px+ until they no longer cross.
- **Fan-out from one node**: When node A connects to B, C, D:
  - B edge fromSide="bottom", C edge fromSide="right", D edge fromSide="left"
  - Space children 300px+ apart
  - NEVER originate all edges from the same side

### COLOR MAP
| Preset | Color | Use For |
|--------|-------|---------|
| "1" | Red | Branch 1 + all its children |
| "2" | Orange | Branch 2 + all its children |
| "3" | Yellow | Central node only |
| "4" | Green | Branch 3 + all its children |
| "5" | Cyan | Branch 4 + all its children |
| "6" | Purple | Sources group only |

### SIZING — STRICT MAXIMUMS
- Central: **width 500, height 160**
- Main branch: **width 360, height 110**
- Sub-branch: **width 320, height 100**
- Detail: **width 280, height 90**
- Source: **width 400, height 100**
- **NEVER exceed these sizes. If text doesn't fit, shorten the text.**

### SPACING — MINIMUM GAPS
- Center to main: **1000-1200px**
- Main to sub: **700-800px**
- Sub to detail: **600-700px**
- Between siblings at same level: **≥300px**
- Between branches: **≥400px**
- Sources group to nearest mind map node: **≥500px**
- **When in doubt: ADD 200px more.**

### VERIFICATION CHECKLIST (DO THIS BEFORE SAVING)
1. [ ] No two nodes overlap (check every pair)
2. [ ] No edge crosses another edge
3. [ ] No edge passes through an unrelated node
4. [ ] All nodes within strict size limits
5. [ ] All sibling gaps ≥300px
6. [ ] Sources group is isolated (no edges to main map)
7. [ ] Colors consistent within each branch

## STEP 3: REFINE THE ORIGINAL NOTE
After creating the mind map, read the original note and append a "## Mind Map Sources & Learning Path" section.

Use vault_write to update the original note with:

```markdown
## Mind Map Sources & Learning Path

*Generated by Mind Map Agent*

### Key Insights Discovered
- [Insight 1 from research]
- [Insight 2 from research]

### Recommended Sources
1. **[Title]** — [Brief description]. [URL if available]
2. **[Title]** — [Brief description]. [URL if available]
3. **[Title]** — [Brief description]. [URL if available]

### Suggested Learning Path
1. **Foundation**: [What to learn first]
2. **Application**: [How to apply it]
3. **Mastery**: [Advanced topics to explore]

### Related Concepts to Explore
- [Concept 1]
- [Concept 2]
- [Concept 3]
```

## EXECUTION MANDATE
1. FIRST output: tool call (search or vault_write_canvas).
2. Research FIRST, then build mind map, then refine note.
3. Call `vault_write_canvas` for the mind map.
4. Call `vault_write` to append the sources section to the original note.
5. Verify both files with `vault_read`.
6. NEVER say "Here is the mind map" — just create it.
"""
)

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
