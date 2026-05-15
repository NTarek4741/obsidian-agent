"""Orchestrators for complex multi-agent workflows.

Exports:
    transcribe_orchestration: Transcript → polished Obsidian note.
    fast_research_orchestration: Topic → single comprehensive markdown note.
    deep_research_plan: Topic → curriculum plan.
    deep_research_execute: Plan → full folder structure.
    mind_map_generate: Note → radial mind map canvas + sources.
"""

from .transcription import transcribe_orchestration
from .fast_research import fast_research_orchestration
from .deep_research import deep_research_plan, deep_research_execute
from .mind_map import mind_map_generate

__all__ = [
    "transcribe_orchestration",
    "fast_research_orchestration",
    "deep_research_plan",
    "deep_research_execute",
    "mind_map_generate",
]