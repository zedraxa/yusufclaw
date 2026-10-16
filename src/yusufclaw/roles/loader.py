"""Loader, validator, and registry for YusufClaw specialist role catalogs.

Discovers, parses, and validates YAML role definitions against the CapabilityMatrix
Pydantic schema with support for aliases, caching, and fallback directory resolution.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from yusufclaw.roles.models import A2AAgentCard, CapabilityMatrix

logger = logging.getLogger(__name__)

# Search paths for role catalog YAML definitions
DEFAULT_CATALOG_CANDIDATES = [
    Path("roles/catalog"),
    Path(__file__).parent / "catalog",
    Path("src/yusufclaw/roles/catalog"),
]


class RoleCatalogError(RuntimeError):
    """Raised when loading or validating the role catalog fails."""


class RoleNotFoundError(KeyError):
    """Raised when a requested role or alias cannot be located in the catalog."""


class RoleCatalog:
    """Manages discovery, parsing, validation, and retrieval of specialist agent roles."""

    def __init__(self, catalog_dir: str | Path | None = None) -> None:
        self.catalog_dir = self._resolve_catalog_dir(catalog_dir)
        self._roles: dict[str, CapabilityMatrix] = {}
        self._alias_map: dict[str, str] = {}
        self._ordered_roles: list[CapabilityMatrix] = []
        self.reload()

    def _resolve_catalog_dir(self, catalog_dir: str | Path | None) -> Path:
        """Resolve the effective directory containing role catalog YAML files."""
        if catalog_dir is not None:
            p = Path(catalog_dir)
            if p.exists() and p.is_dir():
                return p

        for candidate in DEFAULT_CATALOG_CANDIDATES:
            if candidate.exists() and candidate.is_dir():
                return candidate

        # Return default Path even if it doesn't exist yet to allow late creation
        return Path("roles/catalog")

    def reload(self) -> None:
        """Scan catalog directory and parse all role YAML definition files."""
        self._roles.clear()
        self._alias_map.clear()
        self._ordered_roles.clear()

        if not self.catalog_dir.exists() or not self.catalog_dir.is_dir():
            logger.warning("Role catalog directory does not exist: %s", self.catalog_dir)
            return

        yaml_files = sorted(
            list(self.catalog_dir.glob("*.yaml")) + list(self.catalog_dir.glob("*.yml"))
        )

        for file_path in yaml_files:
            try:
                raw_text = file_path.read_text(encoding="utf-8")
                parsed: Any = yaml.safe_load(raw_text)
                if not isinstance(parsed, dict):
                    logger.warning("Skipping non-dict YAML file in catalog: %s", file_path)
                    continue

                matrix = CapabilityMatrix.model_validate(parsed)
                role_id = matrix.role_id.lower().strip()

                if role_id in self._roles:
                    raise RoleCatalogError(
                        f"Duplicate role ID '{role_id}' defined in {file_path}"
                    )

                self._roles[role_id] = matrix
                self._ordered_roles.append(matrix)
                self._alias_map[role_id] = role_id

                for alias in matrix.aliases:
                    norm_alias = alias.lower().strip()
                    if norm_alias in self._alias_map and self._alias_map[norm_alias] != role_id:
                        logger.warning(
                            "Alias conflict: '%s' already mapped to '%s', overriding with '%s'",
                            norm_alias,
                            self._alias_map[norm_alias],
                            role_id,
                        )
                    self._alias_map[norm_alias] = role_id

            except Exception as exc:
                raise RoleCatalogError(
                    f"Failed to load role definition from {file_path}: {exc}"
                ) from exc

    def get_role(self, name_or_alias: str) -> CapabilityMatrix:
        """Retrieve a specialist role by its canonical ID or registered alias.

        Raises:
            RoleNotFoundError: If no matching role or alias exists.
        """
        role = self.find_role(name_or_alias)
        if role is None:
            available = sorted(list(self._alias_map.keys()))
            raise RoleNotFoundError(
                f"Role '{name_or_alias}' not found in catalog. Available: {available}"
            )
        return role

    def find_role(self, name_or_alias: str) -> CapabilityMatrix | None:
        """Retrieve a specialist role by ID or alias, returning None if not found."""
        key = name_or_alias.lower().strip()
        role_id = self._alias_map.get(key)
        if role_id is not None:
            return self._roles.get(role_id)
        return self._roles.get(key)

    def list_roles(self) -> list[CapabilityMatrix]:
        """Return all unique specialist roles in insertion order."""
        return list(self._ordered_roles)

    def list_role_ids(self) -> list[str]:
        """Return the unique canonical role IDs in the catalog."""
        return [r.role_id for r in self._ordered_roles]

    def get_a2a_agent_cards(self) -> list[A2AAgentCard]:
        """Export A2A discovery cards for all loaded specialist roles."""
        return [r.to_a2a_card() for r in self._ordered_roles]

    def validate_catalog(self) -> tuple[bool, list[str]]:
        """Validate all catalog roles and return validity status with error messages."""
        errors: list[str] = []
        if not self._roles:
            errors.append(f"Catalog is empty or directory not found: {self.catalog_dir}")

        for role_id, matrix in self._roles.items():
            if not matrix.allowed_tools and not matrix.allowed_mcp_servers:
                logger.warning("Role '%s' has no allowed tools or MCP servers", role_id)
            if matrix.sandbox_required and matrix.sandbox_mode == "none":
                errors.append(
                    f"Role '{role_id}' mandates sandbox_required=True but sandbox_mode is 'none'"
                )

        return len(errors) == 0, errors


_GLOBAL_CATALOG: RoleCatalog | None = None


def get_role_catalog(catalog_dir: str | Path | None = None) -> RoleCatalog:
    """Return the global shared RoleCatalog instance, initializing it if necessary."""
    global _GLOBAL_CATALOG
    if _GLOBAL_CATALOG is None or catalog_dir is not None:
        _GLOBAL_CATALOG = RoleCatalog(catalog_dir)
    return _GLOBAL_CATALOG


__all__ = [
    "RoleCatalog",
    "RoleCatalogError",
    "RoleNotFoundError",
    "get_role_catalog",
]
