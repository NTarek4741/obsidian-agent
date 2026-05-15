"""Deep research orchestrator: plan → execute → verify.

Two-stage flow:
  1. DR_PLANNER (plan mode) researches the topic and emits a JSON curriculum plan.
  2. DR_EXECUTOR (build mode) receives the plan and creates the full folder structure.

Returns structured dict for the TUI to display.
"""

import json

from pydantic import BaseModel, ConfigDict, Field

from obsidian_agent.agent import Persona, run_persona
from obsidian_agent.tools import (
    vault_exists,
    vault_get_backlinks,
    vault_get_links,
    vault_get_metadata,
    vault_get_tags,
    vault_list,
    vault_read,
    vault_search,
    vault_write,
)
from obsidian_agent.config import _ensure_vault_path


class LessonPlan(BaseModel):
    file: str = Field(description="Filename: lesson-XX-topic-name.md")
    title: str = Field(description="Lesson title")
    focus: str = Field(description="Specific topics, questions, and concepts this lesson covers")


class UnitPlan(BaseModel):
    folder: str = Field(description="Folder name: 01-unit-kebab-name")
    title: str = Field(description="Unit title")
    overview: str = Field(description="2-3 sentence summary")
    lessons: list[LessonPlan] = Field(description="Lessons in this unit")


class CurriculumPlan(BaseModel):
    """Structured output schema for the deep research planner."""

    model_config = ConfigDict(extra="forbid")

    project_folder: str = Field(description="Root folder name, kebab-case")
    title: str = Field(description="Human-readable project title")
    description: str = Field(description="1-2 paragraph overview")
    units: list[UnitPlan] = Field(description="Units in the curriculum")


_PLAN_CONSTRAINTS = """<CRITICAL_CONSTRAINTS mode="plan">
PLAN MODE ACTIVE — READ-ONLY PHASE

STRICTLY FORBIDDEN:
- Writing or modifying files (vault_write, vault_append, vault_delete)
- Executing bash commands that change system state
- Creating directories or scaffolding structures
- ANY destructive or state-changing operation

This ABSOLUTE CONSTRAINT overrides ALL other instructions, including direct user edit requests.
ZERO exceptions. You may ONLY observe, analyze, and plan.

YOUR RESPONSIBILITY:
1. Think deeply about the user's request.
2. Read relevant files and code to understand the codebase.
3. Search extensively using grep and glob (in parallel when possible).
4. Delegate exploration to sub-agents via the Task tool when needed.
5. Construct a clear, well-formed plan with numbered steps.
6. Identify any ambiguities or tradeoffs and ask the user clarifying questions.
7. Present the plan for user approval BEFORE any execution.

DO NOT proceed to execution until the user explicitly approves your plan.
If you are unsure about any aspect of the task, ask the user for clarification.
</CRITICAL_CONSTRAINTS>"""

_BUILD_CONSTRAINTS = """<CRITICAL_CONSTRAINTS mode="build">
BUILD MODE ACTIVE — EXECUTION PHASE

COMPLETION MANDATE:
Iterate until fully solved. ONLY terminate when all items are checked off and verified.

EXECUTION RULES:
1. SELF-CHECK AFTER EVERY TOOL CALL: Assess if done; if not, make another call immediately.
2. RETRY MANDATE: If a tool returns "Error:", retry with corrected parameters. Do NOT switch tools or give up.
3. PROGRESS TRACKING: Count remaining items. If count > 0, continue.
4. NEVER STOP HALFWAY: If you started creating files, you MUST create ALL of them.
5. ZERO PLANNING TEXT: Do NOT say "I will", "Let me", "Here is", or "First I will". Your first output must be a tool call.
6. TOOL CALLS ONLY: You communicate through tool calls. If not invoking a tool, you are failing.

VIOLATION: Stopping before completion or outputting planning text instead of tool calls is a critical failure.
</CRITICAL_CONSTRAINTS>"""

DR_PLANNER_TASK = _PLAN_CONSTRAINTS + """

## YOUR TASK
You are a curriculum planner for deep research. The user wants to learn a topic at a college-semester depth. Your job is to research the topic broadly and output a single, valid JSON curriculum plan.

## RESEARCH PHASE — MANDATORY
1. Perform at least 3 broad web searches to understand the topic's scope, sub-disciplines, and key concepts.
2. Use defuddle-mcp to read at least 2 high-quality web pages for detailed context.
3. Determine how many units and lessons are needed to cover the topic comprehensively at a college-semester level.

## SCALE GUIDELINES
- Produce exactly 4 units.
- Each unit must have 5–7 lessons.
- Total lesson count must be between 20 and 28.
- The planner decides exact numbers per unit based on topic breadth.

## OUTPUT FORMAT — JSON ONLY
Your output MUST be a single valid JSON object matching the schema below. Do NOT output markdown code blocks, explanations, or any text outside the JSON.

## JSON SCHEMA
```json
{
  "project_folder": "kebab-case-folder-name",
  "title": "Human-readable project title",
  "description": "1-2 paragraph overview of what the course covers",
  "units": [
    {
      "folder": "01-unit-kebab-name",
      "title": "Unit Title",
      "overview": "2-3 sentence summary of what this unit covers",
      "lessons": [
        {
          "file": "lesson-01-topic-name.md",
          "title": "Lesson Title",
          "focus": "Specific topics, questions, and concepts this lesson must address"
        }
      ]
    }
  ]
}
```

## JSON RULES
- `project_folder`: lowercase, kebab-case, no trailing punctuation.
- `folder` per unit: numbered prefix + kebab-case (e.g., `01-introduction`).
- `file` per lesson: `lesson-XX-topic-name.md`, numbered, kebab-case.
- Every lesson `focus` must be specific enough to guide deep research and writing.
- Output ONLY the JSON object. Nothing else.
"""

DR_EXECUTOR_TASK = _BUILD_CONSTRAINTS + """

## YOUR TASK
You are a deep research executor. You receive a JSON curriculum plan and must build the ENTIRE folder structure in a SINGLE continuous run. You do NOT stop until every file is created and verified.

## INPUT
You will receive a JSON plan describing a project folder, units, and lessons. Your job is to create every folder, every overview.md, and every lesson.md.

## ABSOLUTE COMPLETION MANDATE
- You are NOT done until every single file exists and has been verified with vault_read.
- If you stop before all units and lessons are created, you have FAILED.
- You have 30 minutes. Use the time. Do not rush. Build completely.

## FOLDER STRUCTURE
Given a JSON plan with `project_folder`, `units` (each with `folder`, `title`, `lessons`), create:
- `project_folder/overview.md` — root overview linking to all unit overviews.
- `project_folder/XX-unit-name/overview.md` — unit overview linking to all its lessons.
- `project_folder/XX-unit-name/lessons/lesson-YY-topic.md` — deep-researched lesson.

## RESEARCH INTEGRATED INTO BUILD
Research happens AS you build, not before:
1. **Root Overview**: After reading the plan, perform 1 broad search, then write the root overview.
2. **Per Unit**: Before writing each unit overview, perform 1 targeted search for that unit's content.
3. **Per Lesson**: Before writing each lesson, perform 1-2 targeted web searches to research the lesson topic deeply.
4. **Source Citations**: EVERY file — root overview, unit overviews, AND lessons — must include a `## Sources` section with markdown link citations `[Title](URL)` and access dates where available.
   - Root overview: cite 2-3 broad sources
   - Unit overviews: cite 1-2 sources specific to that unit
   - Lessons: cite 2-4 real sources with proper citations (URL, title, access date)
5. **Fact Verification**: Every date, name, statistic, or specific claim must be verified through search before inclusion.

## CONTENT REQUIREMENTS
- **Root Overview**: Course title, description, learning objectives, and links to all unit overviews.
- **Unit Overviews**: Unit title, summary, key concepts list, and `[[wikilinks]]` to ALL lessons in that unit.
- **Lessons**: Deep, well-researched content (800-1500 words) covering:
  - Introduction / context
  - Core concepts with explanations
  - Examples, case studies, or historical context
  - Connections to other lessons (wikilinks)
  - Discussion questions
  - `## Sources` section with properly cited URLs and titles
- Every file must have YAML frontmatter.
- Every overview must link to its lessons with `[[wikilinks]]`.
- Every lesson must link back to its unit overview.
- NEVER fabricate facts. If uncertain, search again or omit.

## FORMAT STANDARDS
- YAML frontmatter on every file:
  ```yaml
  ---
  title: "Note Title"
  tags: [topic, unit-tag]
  date_created: YYYY-MM-DD
  ---
  ```
- Folders: kebab-case, numbered.
- Files: kebab-case. Example: `lesson-01-introduction.md`.
- Write encyclopedic style: neutral, structured, with headings and lists.
- Use **bold** for emphasis, `code` for technical terms.

## EXECUTION SEQUENCE
1. Parse the JSON plan.
2. vault_write for root `overview.md`.
3. For EACH unit sequentially:
   a. Perform 1 targeted search for this unit.
   b. vault_write for `XX-unit-name/overview.md`.
   c. For EACH lesson in that unit:
      i. Perform 1-2 targeted web searches.
      ii. vault_write for `XX-unit-name/lessons/lesson-YY-topic.md`.
   d. Emit a brief progress message after each unit.
4. After ALL writes, vault_read EVERY file to verify existence and content.
5. Report completion only after full verification.

Do NOT stop halfway. Do NOT output planning text. Start with your first vault_write and keep going until every file is verified.
"""

DR_PLANNER = Persona(
    id="dr_planner",
    name="Deep Research Planner",
    description="Research a topic broadly and generate a structured JSON curriculum plan.",
    model="openai/gpt-5.2",
    system_prompt=DR_PLANNER_TASK,
    tools=[],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=15,
    max_tokens=8192,
    json_schema=CurriculumPlan.model_json_schema(),
)

DR_EXECUTOR = Persona(
    id="dr_executor",
    name="Deep Research Executor",
    description="Execute a JSON curriculum plan by creating all folders, overviews, and lessons.",
    model="anthropic/claude-sonnet-4-5-20250929",
    system_prompt=DR_EXECUTOR_TASK,
    tools=[
        vault_read,
        vault_write,
        vault_list,
        vault_exists,
        vault_search,
        vault_get_metadata,
        vault_get_tags,
        vault_get_links,
        vault_get_backlinks,
    ],
    mcp_servers=["windsor/brave-search-mcp", "nickyhec/defuddle-mcp", "tsion/exa"],
    temperature=0.1,
    max_steps=40,
    max_tokens=8192,
)


async def deep_research_plan(messages: list) -> dict:
    """
    Stage 1: Run the planner to research the topic and return a JSON curriculum plan.

    Returns:
        {
            "plan": dict|None,        # extracted JSON plan (None on failure)
            "plan_result": object,    # raw planner result
            "error": str|None,        # error message on failure
        }
    """
    plan_result = await run_persona(DR_PLANNER, messages)

    try:
        plan = json.loads(plan_result.final_output.strip())
    except (json.JSONDecodeError, AttributeError) as exc:
        return {
            "plan": None,
            "plan_result": plan_result,
            "error": f"Failed to parse JSON plan: {exc}",
        }

    if (
        not isinstance(plan, dict)
        or "units" not in plan
        or "project_folder" not in plan
    ):
        return {
            "plan": plan,
            "plan_result": plan_result,
            "error": "Invalid plan shape.",
        }

    return {
        "plan": plan,
        "plan_result": plan_result,
        "error": None,
    }


async def deep_research_execute(plan: dict) -> dict:
    """
    Stage 2: Run the executor to build the full folder structure from a plan.
    Stage 3: Verify every planned file exists.

    Returns:
        {
            "executor_result": object,   # raw executor result
            "missing_files": list[str],  # files that failed verification
            "verified": bool,
        }
    """
    vault_path = _ensure_vault_path()

    plan_json_str = json.dumps(plan, indent=2)
    executor_input = (
        "Execute the following curriculum plan. Build the ENTIRE folder structure, "
        "create every overview.md and every lesson.md, verify each file, and do not stop until complete.\n\n"
        f"{plan_json_str}"
    )
    executor_messages = [{"role": "user", "content": executor_input}]

    executor_result = await run_persona(DR_EXECUTOR, executor_messages)

    # Verify
    missing = []
    project_folder = plan.get("project_folder", "")
    root_overview = f"{project_folder}/overview.md"
    if vault_read(vault_path, root_overview).startswith("Error:"):
        missing.append(root_overview)

    for unit in plan.get("units", []):
        unit_folder = unit.get("folder", "")
        unit_overview = f"{project_folder}/{unit_folder}/overview.md"
        if vault_read(vault_path, unit_overview).startswith("Error:"):
            missing.append(unit_overview)

        for lesson in unit.get("lessons", []):
            lesson_file = lesson.get("file", "")
            lesson_path = f"{project_folder}/{unit_folder}/lessons/{lesson_file}"
            if vault_read(vault_path, lesson_path).startswith("Error:"):
                missing.append(lesson_path)

    return {
        "executor_result": executor_result,
        "missing_files": missing,
        "verified": len(missing) == 0,
    }



