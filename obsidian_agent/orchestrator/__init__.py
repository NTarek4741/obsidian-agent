"""Local clients for the three Dedalus machines.

Every agent runs on a machine; each function here is the thin async client
that sends a job to the right machine and reports progress into a local Job.

Exports:
    generate_podcast:     Note → WAV podcast (persistent podcast machine).
    generate_flashcards:  Note → Anki .apkg (ephemeral flashcard sandbox).
    transcribe_remote:    YouTube URL / audio file → polished note (utility machine).
    fast_research_remote: Topic → single comprehensive note (utility machine).
    deep_research_remote: Topic → full curriculum folder (utility machine).
    mind_map_remote:      Note → radial mind map canvas (utility machine).
    chat_remote:          Question → answer grounded in the synced corpus (utility machine).
    AGENT_CLIENTS:        kind → client function, the API's dispatch table.
"""

from .podcast import generate_podcast
from .flashcard import generate_flashcards
from .utility import (
    chat_remote,
    deep_research_remote,
    fast_research_remote,
    mind_map_remote,
    transcribe_remote,
)

# One unified contract: async fn(*inputs, job) -> dict. Keys match Job.kind.
AGENT_CLIENTS = {
    "podcast": generate_podcast,
    "flashcard": generate_flashcards,
    "transcribe": transcribe_remote,
    "research_fast": fast_research_remote,
    "deep_research": deep_research_remote,
    "mind_map": mind_map_remote,
    "chat": chat_remote,
}

__all__ = [
    "generate_podcast",
    "generate_flashcards",
    "transcribe_remote",
    "fast_research_remote",
    "deep_research_remote",
    "mind_map_remote",
    "chat_remote",
    "AGENT_CLIENTS",
]
