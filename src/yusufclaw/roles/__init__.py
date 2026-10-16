"""YusufClaw specialist role catalog, capability matrix, and policy enforcement package."""

from yusufclaw.roles.enforcement import RolePolicyEnforcer, decide_with_role
from yusufclaw.roles.generator import (
    generate_capability_matrix_markdown,
    write_capability_matrix_docs,
)
from yusufclaw.roles.loader import (
    RoleCatalog,
    RoleCatalogError,
    RoleNotFoundError,
    get_role_catalog,
)
from yusufclaw.roles.models import (
    A2AAgentCard,
    CapabilityMatrix,
    MemoryScopeConfig,
    ModelTier,
    RoleBudgetLimits,
    RoleRoutingDecision,
    SandboxMode,
)
from yusufclaw.roles.router import RoleRouter

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
