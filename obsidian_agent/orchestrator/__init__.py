"""Orchestrators for complex multi-agent workflows.

Exports:
    transcribe_orchestration: Transcript → polished Obsidian note.
    fast_research_orchestration: Topic → single comprehensive markdown note.
    deep_research_plan: Topic → curriculum plan.
    deep_research_execute: Plan → full folder structure.
    mind_map_generate: Note → radial mind map canvas + sources.
    delete_all_machines: Destroy all non-destroyed machines.
    generate_podcast: Note → M4A podcast via Dedalus machine.
    generate_flashcards: Note → Anki .apkg via Dedalus machine.
"""

from .transcription import transcribe_orchestration
from .fast_research import fast_research_orchestration
from .deep_research import deep_research_plan, deep_research_execute
from .mind_map import mind_map_generate
from .podcast import delete_all_machines, generate_podcast
from .flashcard import generate_flashcards

__all__ = [
    "transcribe_orchestration",
    "fast_research_orchestration",
    "deep_research_plan",
    "deep_research_execute",
    "mind_map_generate",
    "delete_all_machines",
    "generate_podcast",
    "generate_flashcards",
]
