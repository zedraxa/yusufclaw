"""Memory subpackage for YusufClaw.

Implements durable structured memory systems:
- SQLite FTS5 BM25 search for local single-file episodic/semantic memory.
- PostgreSQL + pgvector for scalable vector and relational memory storage.
- Memory taxonomy:
  * Working: in-session turn context and scratchpads.
  * Episodic: historical conversations and events with session scope isolation.
  * Semantic: durable facts, profiles, and lessons with provenance tracking.
  * Procedural: learned skills and execution routines.
- Revision history and optimistic concurrency control (CAS).
- Tombstone logging and purge handling for GDPR/right-to-forget compliance.
- Context assembly with bounded limits and evidence IDs for LLM prompt context.
"""

from yusufclaw.memory.compaction import (
    CompactionOptions,
    CompactionResult,
    ContextCompactor,
    DeterministicSummarizer,
    LLMSummarizer,
    Summarizer,
)
from yusufclaw.memory.consolidation import (
    ConsolidationJob,
    ConsolidationOptions,
    ConsolidationResult,
)
from yusufclaw.memory.context import (
    ActiveSessionContext,
    active_session_context,
    memory_score,
    rank_memories,
    session_scope,
)
from yusufclaw.memory.fts import sanitize_fts5_query, sanitize_tsquery
from yusufclaw.memory.models import (
    ContextAssemblyOptions,
    EmbeddingProvider,
    ListOptions,
    MemoryDeletionRecord,
    MemoryKind,
    MemoryRecord,
    MemoryRevisionRecord,
    MemoryStatus,
    MemoryTrust,
    RememberInput,
    ReviseInput,
    SearchOptions,
)
from yusufclaw.memory.postgres import PostgresMemory
from yusufclaw.memory.sqlite import SqliteMemory, generate_memory_id
from yusufclaw.memory.store import MemoryStore
from yusufclaw.memory.tools import MemoryTool, create_memory_tools

__all__ = [
    "CompactionOptions",
    "CompactionResult",
    "ContextCompactor",
    "DeterministicSummarizer",
    "LLMSummarizer",
    "Summarizer",
    "ConsolidationJob",
    "ConsolidationOptions",
    "ConsolidationResult",
    "MemoryStore",
    "memory_score",
    "rank_memories",
    "ActiveSessionContext",
    "ContextAssemblyOptions",
    "EmbeddingProvider",
    "ListOptions",
    "MemoryDeletionRecord",
    "MemoryKind",
    "MemoryRecord",
    "MemoryRevisionRecord",
    "MemoryStatus",
    "MemoryTool",
    "MemoryTrust",
    "PostgresMemory",
    "RememberInput",
    "ReviseInput",
    "SearchOptions",
    "SqliteMemory",
    "active_session_context",
    "create_memory_tools",
    "generate_memory_id",
    "sanitize_fts5_query",
    "sanitize_tsquery",
    "session_scope",
]
