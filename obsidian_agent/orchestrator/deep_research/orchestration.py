"""Deep research orchestrator: plan → execute → verify.

Two-stage flow:
  1. DR_PLANNER (plan mode) researches the topic and emits a JSON curriculum plan.
  2. DR_EXECUTOR (build mode) receives the plan and creates the full folder structure.

Returns structured dict for the TUI to display.
"""

import json

from pydantic import BaseModel, ConfigDict, Field

from pathlib import Path

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
from obsidian_agent.orchestrator.utility import _ensure_vault_path

_HERE = Path(__file__).parent


class LessonPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str = Field(description="Filename: lesson-XX-topic-name.md")
    title: str = Field(description="Lesson title")
    focus: str = Field(description="Specific topics, questions, and concepts this lesson covers")


class UnitPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


def _ensure_strict_schema(schema: dict) -> dict:
    """Recursively inject additionalProperties: false into every object node.

    OpenAI-compatible providers require this for strict json_schema mode.
    """
    if not isinstance(schema, dict):
        return schema

    if schema.get("type") == "object":
        schema["additionalProperties"] = False

    for key in ("properties", "items", "$defs", "definitions"):
        if key not in schema:
            continue
        value = schema[key]
        if isinstance(value, dict):
            for k, v in value.items():
                schema[key][k] = _ensure_strict_schema(v)
        elif isinstance(value, list):
            schema[key] = [_ensure_strict_schema(v) for v in value]

    # anyOf / allOf / oneOf / enum are arrays of schemas
    for key in ("anyOf", "allOf", "oneOf"):
        if key in schema and isinstance(schema[key], list):
            schema[key] = [_ensure_strict_schema(v) for v in schema[key]]

    return schema


DR_PLANNER_TASK = (_HERE / "system_prompts" / "system_prompt_planner.md").read_text(encoding="utf-8")
DR_EXECUTOR_TASK = (_HERE / "system_prompts" / "system_prompt_executor.md").read_text(encoding="utf-8")

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
    json_schema=_ensure_strict_schema(CurriculumPlan.model_json_schema()),
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
