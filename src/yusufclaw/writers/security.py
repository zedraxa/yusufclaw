"""Security controls, environment scrubbing, and boundary enforcement for writers.

Ensures child coding CLI processes receive no inherited host secrets and operates
strictly within isolated filesystem boundaries.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

# Safe environment variables permitted in child processes by default
SAFE_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TERM",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "PWD",
        "SHELL",
        "HOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }
)

# Regex patterns identifying host credentials, secrets, and auth tokens
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^AWS_", re.IGNORECASE),
    re.compile(r"^OPENAI_", re.IGNORECASE),
    re.compile(r"^ANTHROPIC_", re.IGNORECASE),
    re.compile(r"^GEMINI_", re.IGNORECASE),
    re.compile(r"^MISTRAL_", re.IGNORECASE),
    re.compile(r"^GROQ_", re.IGNORECASE),
    re.compile(r"^GITHUB_", re.IGNORECASE),
    re.compile(r"^GH_", re.IGNORECASE),
    re.compile(r"^GITLAB_", re.IGNORECASE),
    re.compile(r"^SSH_", re.IGNORECASE),
    re.compile(r"^DATABASE_", re.IGNORECASE),
    re.compile(r"^REDIS_", re.IGNORECASE),
    re.compile(r"^PG[A-Z0-9_]*", re.IGNORECASE),
    re.compile(r"^POSTGRES_", re.IGNORECASE),
    re.compile(r"^TELEGRAM_", re.IGNORECASE),
    re.compile(r"^YUSUFCLAW_.*(?:KEY|TOKEN|SECRET|AUTH|PASSWORD)", re.IGNORECASE),
    re.compile(r".*(?:API_KEY|SECRET_KEY|AUTH_TOKEN|PRIVATE_KEY|PASSWORD|BEARER).*", re.IGNORECASE),
)


class SecurityViolationError(RuntimeError):
    """Raised when an operation attempts to breach writer security boundaries."""


def is_secret_variable(name: str) -> bool:
    """Check if an environment variable name matches any known secret pattern."""
    return any(pattern.search(name) is not None for pattern in SECRET_PATTERNS)


def scrub_environment(
    base_env: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
    *,
    custom_allowlist: set[str] | None = None,
) -> dict[str, str]:
    """Produce a sanitized environment dictionary for child subprocesses.

    1. Only retains variables explicitly in the safe allowlist.
    2. Drops all variables matching secret/credential patterns.
    3. Adds deterministic default git author/committer configs if missing.
    4. Validates overrides; raises SecurityViolationError if overrides contain secret names.
    """
    source = os.environ if base_env is None else base_env
    allowed = SAFE_ENV_ALLOWLIST if custom_allowlist is None else (SAFE_ENV_ALLOWLIST | custom_allowlist)
    scrubbed: dict[str, str] = {}

    for key, value in source.items():
        if key in allowed and not is_secret_variable(key):
            scrubbed[key] = value

    # Ensure deterministic and safe Git committer/author identity
    scrubbed.setdefault("GIT_AUTHOR_NAME", "YusufClaw Writer")
    scrubbed.setdefault("GIT_AUTHOR_EMAIL", "writer@yusufclaw.local")
    scrubbed.setdefault("GIT_COMMITTER_NAME", "YusufClaw Writer")
    scrubbed.setdefault("GIT_COMMITTER_EMAIL", "writer@yusufclaw.local")

    # Merge overrides with strict security validation
    if overrides:
        for key, value in overrides.items():
            if is_secret_variable(key):
                raise SecurityViolationError(
                    f"Forbidden: Attempted to inject secret environment variable '{key}' into child writer."
                )
            scrubbed[key] = value

    return scrubbed


def validate_workspace_path(path: Path | str, parent_dir: Path | str) -> Path:
    """Ensure workspace path is strictly contained within the allowed parent directory.

    Guards against path traversal (e.g. '../'), symlink escapes, and unauthorized paths.
    """
    resolved_parent = Path(parent_dir).resolve()
    resolved_path = Path(path).resolve()

    try:
        resolved_path.relative_to(resolved_parent)
    except ValueError as exc:
        raise SecurityViolationError(
            f"Path traversal detected: Workspace '{resolved_path}' is outside parent '{resolved_parent}'."
        ) from exc

    if resolved_path == resolved_parent:
        raise SecurityViolationError("Workspace path cannot be the root parent directory itself.")

    return resolved_path
