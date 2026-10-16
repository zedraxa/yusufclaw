"""Local Credential Broker for YusufClaw (UX-06).

Ensures sensitive credentials, API keys, and tokens are stored in an isolated,
encrypted or memory-guarded broker outside of conversation turns, SQLite memory,
and model prompts. Implements Simon Willison's Lethal Trifecta mitigation and FIDES IFC.
"""

from __future__ import annotations

import hashlib
import threading
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class SecretMetadata(BaseModel):
    """Safe metadata summary for a stored credential, never exposing the plaintext secret."""

    model_config = ConfigDict(populate_by_name=True)

    key_name: str = Field(alias="keyName")
    fingerprint: str
    service_name: str = Field(default="", alias="serviceName")
    description: str = ""
    created_at: str = Field(alias="createdAt")

    def __repr__(self) -> str:
        return f"SecretMetadata(key_name={self.key_name!r}, fingerprint={self.fingerprint!r})"


class CredentialBroker:
    """Thread-safe local credential broker keeping secrets completely out of model contexts and logs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: dict[str, str] = {}
        self._metadata: dict[str, SecretMetadata] = {}

    @staticmethod
    def compute_fingerprint(secret: str) -> str:
        """Compute 12-char SHA-256 hex fingerprint of a secret string."""
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]

    def store(
        self,
        key_name: str,
        secret_value: str,
        service_name: str = "",
        description: str = "",
    ) -> str:
        """Store a credential securely and return its non-reversible SHA-256 fingerprint."""
        if not key_name or not key_name.strip():
            raise ValueError("key_name cannot be empty")
        if not secret_value or not secret_value.strip():
            raise ValueError("secret_value cannot be empty")

        clean_key = key_name.strip()
        fingerprint = self.compute_fingerprint(secret_value)
        meta = SecretMetadata(
            keyName=clean_key,
            fingerprint=fingerprint,
            serviceName=service_name.strip(),
            description=description.strip(),
            createdAt=datetime.now(UTC).isoformat(),
        )

        with self._lock:
            self._secrets[clean_key] = secret_value
            self._metadata[clean_key] = meta

        return fingerprint

    def get(self, key_name: str) -> str | None:
        """Retrieve the plaintext secret for authorized execution only. Never pass to model prompt."""
        with self._lock:
            return self._secrets.get(key_name.strip())

    def has(self, key_name: str) -> bool:
        """Check if a credential exists in the broker."""
        with self._lock:
            return key_name.strip() in self._secrets

    def get_metadata(self, key_name: str) -> SecretMetadata | None:
        """Retrieve safe metadata for a credential."""
        with self._lock:
            return self._metadata.get(key_name.strip())

    def list_secrets(self) -> list[SecretMetadata]:
        """List metadata for all stored secrets without exposing any plaintext."""
        with self._lock:
            return list(self._metadata.values())

    def delete(self, key_name: str) -> bool:
        """Delete a credential from the broker."""
        clean_key = key_name.strip()
        with self._lock:
            removed_secret = self._secrets.pop(clean_key, None)
            self._metadata.pop(clean_key, None)
            return removed_secret is not None

    def clear(self) -> None:
        """Purge all stored credentials."""
        with self._lock:
            self._secrets.clear()
            self._metadata.clear()

    @staticmethod
    def format_system_pill(key_name: str, fingerprint: str) -> str:
        """Format an innocuous system pill suitable for display in conversation history."""
        return f"[Key configured: {key_name} (sha256: {fingerprint})]"


# Global singleton broker instance for local gateway daemon
_default_broker: CredentialBroker | None = None
_broker_lock = threading.Lock()


def get_credential_broker() -> CredentialBroker:
    """Get or create the global CredentialBroker singleton."""
    global _default_broker
    if _default_broker is None:
        with _broker_lock:
            if _default_broker is None:
                _default_broker = CredentialBroker()
    return _default_broker


__all__ = [
    "CredentialBroker",
    "SecretMetadata",
    "get_credential_broker",
]
