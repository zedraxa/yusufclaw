"""Agent-to-Agent (A2A 1.0) local teammate protocol package for YusufClaw.

Implements:
- AgentCard declarations (id, role, capabilities, budget).
- TaskRequest and TaskResult envelope contracts.
- Segregated data channels (child cannot read parent full memory).
- Depth-1 TeammateRegistry with child budget caps and cancellation propagation.
- In-process and loopback HTTP JSON transports for same-host agents.
- Teammate status hook for AG-UI UX-12 telemetry (spawned, step, finished).
- SubagentRunner adapters.
"""

from __future__ import annotations

from yusufclaw.a2a.adapter import SubagentA2AAdapter, create_teammate_handler_from_runner
from yusufclaw.a2a.channel import (
    DataChannelSegregation,
    DataChannelViolationError,
    IsolatedChildMemory,
    MemoryAccessDeniedError,
    SegregatedContext,
)
from yusufclaw.a2a.events import TeammateEvent, TeammateEventType
from yusufclaw.a2a.hook import TeammateListener, TeammateStatusHook
from yusufclaw.a2a.models import (
    AgentCard,
    ContextArtifact,
    TaskRequest,
    TaskResult,
    TaskStatus,
    TeammateBudget,
)
from yusufclaw.a2a.registry import (
    ActiveTeammateTask,
    ConcurrencyLimitExceededError,
    DepthLimitExceededError,
    TeammateNotFoundError,
    TeammateRegistry,
)
from yusufclaw.a2a.transport import (
    A2ATransport,
    HttpJsonTransport,
    InProcessTransport,
    create_a2a_router,
)

__all__ = [
    # Models & Envelopes
    "AgentCard",
    "ContextArtifact",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "TeammateBudget",
    "TeammateEvent",
    "TeammateEventType",
    # Channel Segregation
    "DataChannelSegregation",
    "DataChannelViolationError",
    "IsolatedChildMemory",
    "MemoryAccessDeniedError",
    "SegregatedContext",
    # Transports
    "A2ATransport",
    "HttpJsonTransport",
    "InProcessTransport",
    "create_a2a_router",
    # Registry
    "ActiveTeammateTask",
    "ConcurrencyLimitExceededError",
    "DepthLimitExceededError",
    "TeammateNotFoundError",
    "TeammateRegistry",
    # Hooks
    "TeammateListener",
    "TeammateStatusHook",
    # Adapters
    "SubagentA2AAdapter",
    "create_teammate_handler_from_runner",
]
