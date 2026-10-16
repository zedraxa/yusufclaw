"""Memory tools for agent execution (remember, revise, forget, recall)."""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from yusufclaw.memory.context import active_session_context
from yusufclaw.models.types import ToolDefinition


class MemoryTool(ToolDefinition):
    """Callable agent tool wrapping memory operations."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Awaitable[str]],
    ) -> None:
        super().__init__(
            name=name, description=description, input_schema=input_schema
        )
        self.handler = handler

    async def execute(self, input_data: dict[str, Any]) -> str:
        """Execute the tool handler with supplied arguments."""
        return await self.handler(input_data)


def create_memory_tools(
    memory: Any, get_session: Callable[[], str] | None = None
) -> list[MemoryTool]:
    """Create agent-facing memory tools (remember, revise, forget, recall)."""

    def resolve_scope(input_scope: Any) -> str:
        if isinstance(input_scope, str) and input_scope.strip():
            return input_scope.strip()
        active = active_session_context.get()
        if active and active.session:
            return active.session
        if get_session:
            return get_session()
        return "default"

    async def _call_mem(method_name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(memory, method_name)
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        res = method(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res

    async def remember_handler(input_data: dict[str, Any]) -> str:
        text_val = str(input_data.get("text") or "").strip()
        if not text_val:
            raise ValueError("Memory text must not be empty")

        scope = resolve_scope(input_data.get("scope"))
        active = active_session_context.get()
        source_ids = [active.request_id] if active and active.request_id else []

        kind = input_data.get("kind") or "fact"
        category = input_data.get("category")
        supersedes_id = (
            input_data.get("supersedesId")
            or input_data.get("supersedes_id")
        )

        record = await _call_mem(
            "remember",
            scope,
            {
                "text": text_val,
                "kind": kind,
                "category": str(category).strip() if category else None,
                "supersedes_id": str(supersedes_id).strip() if supersedes_id else None,
                "source": "agent_tool",
                "source_ids": source_ids,
                "trust": "observed",
            },
        )

        superseded_part = f" (superseded {supersedes_id})" if supersedes_id else ""
        return (
            f'Remembered [{record.id}] ({record.kind}, rev {record.revision}): '
            f'"{record.text}". Evidence ID: {record.id}{superseded_part}.'
        )

    async def revise_handler(input_data: dict[str, Any]) -> str:
        rec_id = str(input_data.get("id") or "").strip()
        text_val = str(input_data.get("text") or "").strip()
        if not rec_id:
            raise ValueError("Memory ID must not be empty")
        if not text_val:
            raise ValueError("Updated text must not be empty")

        scope = resolve_scope(input_data.get("scope"))
        active = active_session_context.get()
        source_ids = [active.request_id] if active and active.request_id else []

        expected_rev = (
            input_data.get("expectedRevision")
            if input_data.get("expectedRevision") is not None
            else input_data.get("expected_revision")
        )
        category = input_data.get("category")

        record = await _call_mem(
            "revise",
            scope,
            rec_id,
            {
                "text": text_val,
                "category": str(category).strip() if category else None,
                "expected_revision": int(expected_rev) if expected_rev is not None else None,
                "source": "agent_tool",
                "source_ids": source_ids,
            },
        )

        return f'Revised memory [{record.id}] to revision {record.revision}: "{record.text}".'

    async def forget_handler(input_data: dict[str, Any]) -> str:
        rec_id = str(input_data.get("id") or "").strip()
        if not rec_id:
            raise ValueError("Memory ID must not be empty")

        scope = resolve_scope(input_data.get("scope"))
        reason = input_data.get("reason")

        deleted = await _call_mem(
            "forget",
            scope,
            rec_id,
            str(reason) if reason is not None else None,
        )

        if not deleted:
            return f"Memory record [{rec_id}] not found in this scope."
        return f"Forgot memory [{rec_id}]. It will no longer be retrieved."

    async def recall_handler(input_data: dict[str, Any]) -> str:
        query_val = str(input_data.get("query") or "").strip()
        if not query_val:
            raise ValueError("Query must not be empty")

        scope = resolve_scope(input_data.get("scope"))
        limit_val = input_data.get("limit")
        limit = min(max(1, int(limit_val)), 20) if limit_val is not None else 5
        kind_val = input_data.get("kind")

        results = await _call_mem(
            "search",
            scope,
            query_val,
            {"kind": kind_val, "limit": limit},
        )

        if not results:
            return f'No memories found matching "{query_val}".'

        lines = [
            f"- [Evidence: {r.id}] ({r.kind}, rev {r.revision}"
            f"{f', cat: {r.category}' if r.category else ''}): {r.text}"
            for r in results
        ]
        return f"Found {len(results)} memories:\n" + "\n".join(lines)

    remember_tool = MemoryTool(
        name="remember",
        description="Store a durable fact, profile preference, or episodic note into long-term memory with provenance and an evidence ID.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The fact, preference, or note to store in memory.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["profile", "fact", "note"],
                    "description": "Memory kind: 'profile' (enduring user traits/preferences), 'fact' (user or project facts), or 'note' (episodic notes/observations). Default is 'fact'.",
                },
                "category": {
                    "type": "string",
                    "description": 'Optional category tag (e.g. "preferences", "work", "contacts").',
                },
                "supersedesId": {
                    "type": "string",
                    "description": "Optional evidence ID of an existing memory record that this new fact replaces/supersedes.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional explicit scope identifier. Defaults to active session.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        handler=remember_handler,
    )

    revise_tool = MemoryTool(
        name="revise",
        description="Revise or update an existing memory record by evidence ID, incrementing its revision and preserving historical revisions.",
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": 'Evidence ID of the memory to revise (e.g. "mem_...").',
                },
                "text": {
                    "type": "string",
                    "description": "The updated fact or note text.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional updated category tag.",
                },
                "expectedRevision": {
                    "type": "integer",
                    "description": "Optional expected current revision for optimistic concurrency check.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional explicit scope identifier. Defaults to active session.",
                },
            },
            "required": ["id", "text"],
            "additionalProperties": False,
        },
        handler=revise_handler,
    )

    forget_tool = MemoryTool(
        name="forget",
        description="Remove a memory record by evidence ID from active retrieval and search (soft delete with tombstone).",
        input_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": 'Evidence ID of the memory to forget (e.g. "mem_...").',
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for forgetting.",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional explicit scope identifier. Defaults to active session.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        handler=forget_handler,
    )

    recall_tool = MemoryTool(
        name="recall",
        description="Search long-term structured memory by keyword or semantic query using FTS5, scoped to this session.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords or search phrase to look up in memory.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["profile", "fact", "note"],
                    "description": "Optional filter by memory kind.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return (default 5, max 20).",
                },
                "scope": {
                    "type": "string",
                    "description": "Optional explicit scope identifier. Defaults to active session.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=recall_handler,
    )

    return [remember_tool, revise_tool, forget_tool, recall_tool]


__all__ = ["MemoryTool", "create_memory_tools"]
