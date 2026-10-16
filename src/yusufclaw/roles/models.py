"""Pydantic schemas and data contracts for YusufClaw specialist roles and capability matrices.

Defines schemas for:
- Model tiers (strong/cheap/local) aligned with architectural model routing.
- Memory scope read/write boundaries with wildcard and domain matching.
- A2A (Agent-to-Agent 1.0) agent card fields.
- CapabilityMatrix defining granular permissions, tools, MCP servers, approvals,
  sandbox requirements, and channel communication rules (send = always approval).
- Role routing decisions connecting task analyzer classifications to specialist roles.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from yusufclaw.orchestration.models import BudgetLimits, RiskLevel, TaskAnalysis
from yusufclaw.security import Grants

ModelTier = Literal["strong", "cheap", "local"]
SandboxMode = Literal["auto", "bubblewrap", "container", "none"]


class MemoryScopeConfig(BaseModel):
    """Memory read and write scope boundaries for a specialist role."""

    model_config = ConfigDict(populate_by_name=True)

    read: list[str] = Field(
        default_factory=list,
        description="Allowed memory read scopes (supports '*' for unrestricted read)",
    )
    write: list[str] = Field(
        default_factory=list,
        description="Allowed memory write scopes (supports '*' for unrestricted write)",
    )

    def can_read(self, scope: str) -> bool:
        """Verify if reading from the target scope is authorized."""
        if "*" in self.read:
            return True
        return scope in self.read

    def can_write(self, scope: str) -> bool:
        """Verify if writing or revising the target scope is authorized."""
        if "*" in self.write:
            return True
        return scope in self.write


class A2AAgentCard(BaseModel):
    """A2A (Agent-to-Agent 1.0) compatible discovery card for a specialist role."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Unique role identifier for A2A discovery")
    description: str = Field(..., description="Human and machine readable role description")
    version: str = Field(default="1.0.0", description="SemVer contract version")
    endpoint: str | None = Field(default=None, description="Direct A2A communication endpoint URL")
    capabilities: list[str] = Field(
        default_factory=list, description="Declared functional capabilities"
    )
    skills: list[str] = Field(
        default_factory=list, description="Associated skills available to this agent"
    )
    input_modes: list[str] = Field(
        default_factory=lambda: ["text", "json"],
        description="Supported input MIME or schema types",
    )
    output_modes: list[str] = Field(
        default_factory=lambda: ["text", "action_card"],
        description="Supported output modes",
    )
    author: str = Field(default="YusufClaw", description="Author or system provider")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary provider metadata"
    )


class CapabilityMatrix(BaseModel):
    """Explicit capability matrix governing a specialist agent role.

    Enforces least privilege, sandbox isolation, explicit memory boundaries,
    and strict human approval gating for high-impact actions and external channel sends.
    """

    model_config = ConfigDict(populate_by_name=True)

    role_id: str = Field(
        ...,
        validation_alias=AliasChoices("role_id", "roleId", "id"),
        serialization_alias="roleId",
        description="Unique role identifier (e.g. orchestrator, coder, writer)",
    )
    name: str = Field(..., description="Human-readable specialist role name")
    description: str = Field(..., description="Detailed role purpose and operational domain")
    aliases: list[str] = Field(
        default_factory=list, description="Alternative lookup aliases (e.g. 'web' for 'browser')"
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_tools", "allowedTools"),
        serialization_alias="allowedTools",
        description="Whitelist of tool names permitted for this role",
    )
    allowed_mcp_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("allowed_mcp_servers", "allowedMcpServers"),
        serialization_alias="allowedMcpServers",
        description="Whitelist of MCP server IDs permitted for this role",
    )
    skills: list[str] = Field(
        default_factory=list, description="Specific skills this specialist agent is equipped with"
    )
    model_tier: ModelTier = Field(
        default="cheap",
        validation_alias=AliasChoices("model_tier", "modelTier"),
        serialization_alias="modelTier",
        description="Required model tier: 'strong' (reasoning/coding), 'cheap' (triage), 'local' (offline)",
    )
    memory_scopes: MemoryScopeConfig = Field(
        default_factory=MemoryScopeConfig,
        validation_alias=AliasChoices("memory_scopes", "memoryScopes"),
        serialization_alias="memoryScopes",
        description="Granular read/write boundaries for durable memory",
    )
    risk_level: RiskLevel = Field(
        default="low",
        validation_alias=AliasChoices("risk_level", "riskLevel"),
        serialization_alias="riskLevel",
        description="Inherent operational risk level of this specialist role",
    )
    approval_actions: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("approval_actions", "approvalActions"),
        serialization_alias="approvalActions",
        description="Tools or specific actions that strictly mandate human ActionCard approval",
    )
    budget_limits: BudgetLimits = Field(
        default_factory=BudgetLimits,
        validation_alias=AliasChoices("budget_limits", "budgetLimits"),
        serialization_alias="budgetLimits",
        description="Token, cost, step, and time budget ceilings for this role",
    )
    sandbox_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("sandbox_required", "sandboxRequired"),
        serialization_alias="sandboxRequired",
        description="Whether actions by this role must be executed within rootless sandbox isolation",
    )
    sandbox_mode: SandboxMode = Field(
        default="none",
        validation_alias=AliasChoices("sandbox_mode", "sandboxMode"),
        serialization_alias="sandboxMode",
        description="Preferred sandbox backend mode: auto, bubblewrap, container, or none",
    )
    send_channels: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("send_channels", "sendChannels"),
        serialization_alias="sendChannels",
        description="External channels this role may deliver messages to (send = always approval)",
    )
    a2a_card: A2AAgentCard | None = Field(
        default=None,
        validation_alias=AliasChoices("a2a_card", "a2aCard"),
        serialization_alias="a2aCard",
        description="Exported A2A agent card specification",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        """Handle flat memory scope fields and synthesize default A2A card if omitted."""
        if not isinstance(data, dict):
            return data

        d = dict(data)
        # Normalize memory scopes from flat fields if present
        if "memory_scopes" not in d and "memoryScopes" not in d:
            read_scopes = d.pop("memory_read_scopes", None) or d.pop("memoryReadScopes", None) or []
            write_scopes = d.pop("memory_write_scopes", None) or d.pop("memoryWriteScopes", None) or []
            if read_scopes or write_scopes:
                d["memory_scopes"] = {"read": read_scopes, "write": write_scopes}

        return d

    def model_post_init(self, __context: Any) -> None:
        """Ensure an A2AAgentCard exists for every loaded capability matrix."""
        if self.a2a_card is None:
            caps = list(self.allowed_tools)
            self.a2a_card = A2AAgentCard(
                name=self.role_id,
                description=self.description,
                version="1.0.0",
                capabilities=caps,
                skills=list(self.skills),
            )

    def can_use_tool(self, tool_name: str) -> bool:
        """Check whether the tool is explicitly permitted in this capability matrix."""
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def can_use_mcp_server(self, server_name: str) -> bool:
        """Check whether the MCP server is authorized."""
        if "*" in self.allowed_mcp_servers:
            return True
        return server_name in self.allowed_mcp_servers

    def can_read_memory(self, scope: str) -> bool:
        """Check whether memory read access to the specified scope is authorized."""
        return self.memory_scopes.can_read(scope)

    def can_write_memory(self, scope: str) -> bool:
        """Check whether memory write access to the specified scope is authorized."""
        return self.memory_scopes.can_write(scope)

    def requires_approval(self, action_or_tool: str) -> bool:
        """Check whether the given action or tool strictly requires human approval."""
        return action_or_tool in self.approval_actions

    def can_send_to_channel(self, channel: str) -> bool:
        """Check whether this role is permitted to target the specified channel."""
        if "*" in self.send_channels:
            return True
        return channel.lower() in [c.lower() for c in self.send_channels]

    def to_grants(self) -> Grants:
        """Convert matrix allowed tools into security Grants for deterministic policy checks."""
        return Grants(allowed_tools=set(self.allowed_tools))

    def to_a2a_card(self) -> A2AAgentCard:
        """Export the agent card for A2A protocol discovery."""
        if self.a2a_card is not None:
            return self.a2a_card
        return A2AAgentCard(
            name=self.role_id,
            description=self.description,
            version="1.0.0",
            capabilities=list(self.allowed_tools),
            skills=list(self.skills),
        )


class RoleRoutingDecision(BaseModel):
    """Outcome of analyzing a task and selecting specialist agent roles."""

    model_config = ConfigDict(populate_by_name=True)

    primary_role: CapabilityMatrix = Field(
        ..., description="The main specialist role assigned to lead execution"
    )
    secondary_roles: list[CapabilityMatrix] = Field(
        default_factory=list, description="Secondary specialist roles recommended for subtasks"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Routing confidence score (0.0 - 1.0)"
    )
    reasoning: str = Field(default="", description="Explanation of why this role was selected")
    task_analysis: TaskAnalysis | None = Field(
        default=None, description="Underlying task analyzer classification result"
    )


__all__ = [
    "A2AAgentCard",
    "CapabilityMatrix",
    "MemoryScopeConfig",
    "ModelTier",
    "RoleBudgetLimits",
    "RoleRoutingDecision",
    "SandboxMode",
]

# Type alias for clarity
RoleBudgetLimits = BudgetLimits
