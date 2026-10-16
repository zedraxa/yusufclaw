"""Skills subpackage for YusufClaw.

Implements agent skill lifecycle and execution:
- Trusted local markdown skill parsing and metadata extraction.
- Dynamic skill injection based on task description and relevance.
- Skill evolution pipeline: task -> solve -> verify -> extract -> test -> version.
- Skill repository management and isolation.

Note: Logic ported from TypeScript packages/core skills system.
"""

from yusufclaw.skills.loader import (
    MAX_DEPTH,
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    load_skills,
    load_skills_sync,
    loadSkills,
)

__all__ = [
    "MAX_DEPTH",
    "MAX_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "loadSkills",
    "load_skills",
    "load_skills_sync",
]
