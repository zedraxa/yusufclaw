"""Compatibility re-export of yusufclaw.roles package."""

from yusufclaw.roles import (
    A2AAgentCard,
    CapabilityMatrix,
    MemoryScopeConfig,
    ModelTier,
    RoleBudgetLimits,
    RoleCatalog,
    RoleCatalogError,
    RoleNotFoundError,
    RolePolicyEnforcer,
    RoleRouter,
    RoleRoutingDecision,
    SandboxMode,
    decide_with_role,
    generate_capability_matrix_markdown,
    get_role_catalog,
    write_capability_matrix_docs,
)

__all__ = [
    "A2AAgentCard",
    "CapabilityMatrix",
    "MemoryScopeConfig",
    "ModelTier",
    "RoleBudgetLimits",
    "RoleCatalog",
    "RoleCatalogError",
    "RoleNotFoundError",
    "RolePolicyEnforcer",
    "RoleRouter",
    "RoleRoutingDecision",
    "SandboxMode",
    "decide_with_role",
    "generate_capability_matrix_markdown",
    "get_role_catalog",
    "write_capability_matrix_docs",
]
