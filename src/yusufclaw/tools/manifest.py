"""Tool manifest and GuardedTool interfaces for YusufClaw security policies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from yusufclaw.models.types import ToolDefinition
from yusufclaw.security.models import ToolManifest

ApprovalCheck = bool | Callable[[dict[str, Any]], bool]
ToolFunction = Callable[[dict[str, Any]], Awaitable[str]]


class GuardedTool(ToolDefinition):
    """An executable tool wrapped with security manifest and approval metadata."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        manifest: ToolManifest,
        execute_fn: ToolFunction,
        requires_approval: ApprovalCheck = False,
    ) -> None:
        super().__init__(name=name, description=description, input_schema=input_schema)
        self.manifest = manifest
        self.requires_approval = requires_approval
        self.execute_fn = execute_fn

    async def execute(self, input_args: dict[str, Any]) -> str:
        """Execute the tool function with provided arguments."""
        return await self.execute_fn(input_args)

    def check_approval(self, input_args: dict[str, Any]) -> bool:
        """Check whether explicit user approval is required for the given arguments."""
        if callable(self.requires_approval):
            return bool(self.requires_approval(input_args))
        return bool(self.requires_approval)
