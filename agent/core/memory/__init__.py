"""Long-term user memory: goals, preferences, knowledge state, project decisions."""
from agent.core.memory.service import MemoryService
from agent.core.memory.policy import (
    MEMORY_TYPES,
    can_promote_candidate,
    resolve_conflict,
)
from agent.core.memory.extractor import extract_candidates
from agent.core.memory.retrieval import search_ranked

__all__ = [
    "MemoryService",
    "MEMORY_TYPES",
    "can_promote_candidate",
    "resolve_conflict",
    "extract_candidates",
    "search_ranked",
]
