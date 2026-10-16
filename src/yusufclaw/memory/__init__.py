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

Note: Logic will be ported from TypeScript packages/core/src/memory.ts and tests.
"""

__all__: list[str] = []
