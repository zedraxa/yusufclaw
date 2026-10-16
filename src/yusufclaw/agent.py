"""Minimal LangGraph agent loop for YusufClaw with security policies.

Defines a clean interface over a LangGraph state machine consisting of:
- AgentState: holds message history, step count, and session context.
- model_node: executes provider completion with system prompt and available tools.
- tool_node: evaluates deterministic security policies, tracks session taints,
  manages human-in-the-loop ActionCard approvals, and invokes tool handlers.
- conditional edge: determines whether to continue executing tools or end the turn.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from yusufclaw.models.types import (
    Message,
    ModelProvider,
    ModelRequest,
    ToolCall,
    ToolDefinition,
)
from yusufclaw.security import (
    ActionCard,
    ActionCardStore,
    Grants,
    Label,
    SessionTaint,
    ToolManifest,
    Value,
    decide,
    render_card,
)
from yusufclaw.tools.manifest import GuardedTool

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]
ApproveCallback = Callable[[ToolCall, ActionCard | None], Awaitable[bool]]


class AgentState(TypedDict):
    """Execution state for the LangGraph agent."""

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    session_id: str


class Agent:
    """Core agent loop wrapping a compiled LangGraph state graph with security policies."""

    def __init__(
        self,
        provider: ModelProvider,
        system_prompt: str = "You are YusufClaw, an autonomous personal AI agent.",
        tools: Sequence[ToolDefinition] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 8,
        grants: Grants | None = None,
        card_store: ActionCardStore | None = None,
        approve_callback: ApproveCallback | None = None,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = list(tools) if tools else []
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.grants = grants
        self.card_store = card_store or ActionCardStore()
        self.approve_callback = approve_callback
        self.taints: dict[str, SessionTaint] = {}
        self.tools_by_name: dict[str, ToolDefinition] = {t.name: t for t in self.tools}
        self.graph = self._build_graph()

    def get_taint(self, session_id: str) -> SessionTaint:
        """Get or initialize the taint state for a session."""
        if session_id not in self.taints:
            self.taints[session_id] = SessionTaint(contaminated=False, sources=[])
        return self.taints[session_id]

    def set_taint(self, session_id: str, taint: SessionTaint) -> None:
        """Set the taint state for a session."""
        self.taints[session_id] = SessionTaint(
            contaminated=taint.contaminated,
            sources=list(taint.sources),
        )

    def _convert_to_model_messages(self, messages: list[BaseMessage]) -> list[Message]:
        converted: list[Message] = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                converted.append(Message(role="user", content=str(msg.content)))
            elif isinstance(msg, AIMessage):
                tool_calls: list[ToolCall] = []
                for tc in getattr(msg, "tool_calls", []):
                    tool_calls.append(
                        ToolCall(
                            id=tc.get("id") or str(uuid.uuid4()),
                            name=tc.get("name", ""),
                            input=tc.get("args", {}),
                        )
                    )
                converted.append(
                    Message(
                        role="assistant",
                        content=str(msg.content),
                        tool_calls=tool_calls,
                    )
                )
            elif isinstance(msg, ToolMessage):
                converted.append(
                    Message(
                        role="tool",
                        content=str(msg.content),
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", None),
                    )
                )
            else:
                converted.append(Message(role="user", content=str(msg.content)))
        return converted

    def _build_graph(self) -> Any:
        workflow = StateGraph(AgentState)

        async def model_node(state: AgentState) -> dict[str, Any]:
            model_messages = self._convert_to_model_messages(state["messages"])
            request = ModelRequest(
                system=self.system_prompt,
                messages=model_messages,
                tools=self.tools,
            )
            response = await self.provider.complete(request)

            lc_tool_calls: list[dict[str, Any]] = [
                {
                    "name": tc.name,
                    "args": tc.input,
                    "id": tc.id,
                }
                for tc in response.tool_calls
            ]

            ai_message = AIMessage(
                content=response.text,
                tool_calls=lc_tool_calls,
            )

            current_steps = state.get("step_count", 0)
            return {
                "messages": [ai_message],
                "step_count": current_steps + 1,
            }

        def should_continue(state: AgentState) -> str:
            messages = state["messages"]
            if not messages:
                return END
            last = messages[-1]
            has_tool_calls = bool(getattr(last, "tool_calls", None))
            within_limits = state.get("step_count", 0) < self.max_steps
            if has_tool_calls and within_limits:
                return "tools"
            return END

        async def tool_node(state: AgentState) -> dict[str, Any]:
            last = state["messages"][-1]
            tool_calls = getattr(last, "tool_calls", [])
            results: list[ToolMessage] = []
            session_id = state.get("session_id", "default")
            taint = self.get_taint(session_id)

            for call in tool_calls:
                call_id = call.get("id") or str(uuid.uuid4())
                name = call.get("name", "")
                args = call.get("args", {})
                tool_def = self.tools_by_name.get(name)

                # 1. Unknown tool check
                if tool_def is None and self.tool_executor is None:
                    results.append(
                        ToolMessage(
                            content="Unknown tool",
                            tool_call_id=call_id,
                            name=name,
                        )
                    )
                    continue

                # 2. Extract manifest
                manifest: ToolManifest
                if isinstance(tool_def, GuardedTool):
                    manifest = tool_def.manifest
                else:
                    manifest = ToolManifest(
                        id=name,
                        version="1.0.0",
                        source="builtin",
                        effects=[],
                    )

                # 3. Security Label and Value wrapper
                args_value = Value[dict[str, Any]](
                    v=args,
                    labels=[
                        Label(
                            origin="agent",
                            integrity="untrusted" if taint.contaminated else "trusted",
                            secrecy="public",
                        )
                    ],
                )

                # 4. Evaluate deterministic security policy
                decision = decide(manifest, args_value, taint, self.grants)
                if decision.kind == "deny":
                    results.append(
                        ToolMessage(
                            content=f"Tool policy denied: {decision.reason}",
                            tool_call_id=call_id,
                            name=name,
                        )
                    )
                    continue

                # 5. Check if human-in-the-loop ActionCard approval is required
                needs_approval = False
                action_card: ActionCard | None = None

                if decision.kind == "card":
                    needs_approval = True
                    action_card = decision.card
                elif isinstance(tool_def, GuardedTool) and tool_def.check_approval(args):
                    needs_approval = True
                    action_card = render_card(manifest, args_value, taint)

                if needs_approval:
                    if action_card is not None and self.card_store is not None:
                        self.card_store.register(action_card, expected_args=args)

                    approved = False
                    if self.approve_callback is not None:
                        try:
                            tc_obj = ToolCall(id=call_id, name=name, input=args)
                            approved = await self.approve_callback(tc_obj, action_card)
                        except Exception:
                            approved = False

                    if not approved:
                        results.append(
                            ToolMessage(
                                content="Tool approval denied",
                                tool_call_id=call_id,
                                name=name,
                            )
                        )
                        continue

                # 6. Tool execution
                output: str
                try:
                    if isinstance(tool_def, GuardedTool):
                        output = await tool_def.execute(args)
                    elif self.tool_executor:
                        output = await self.tool_executor(name, args)
                    else:
                        output = f"Executed {name} with args {args}"

                    # 7. Update taint if tool ingested untrusted data
                    if "read_untrusted" in manifest.effects:
                        taint.contaminated = True
                        if name not in taint.sources:
                            taint.sources.append(name)
                except Exception as e:
                    err_msg = str(e)
                    if any(
                        kw in err_msg
                        for kw in (
                            "restricted sensitive path",
                            "restricted sensitive file",
                            "SSRF",
                            "outside workspace",
                            "allowlist",
                            "Tool policy denied",
                            "Tool approval denied",
                        )
                    ):
                        output = err_msg
                    else:
                        output = f"Tool execution error: {err_msg}"

                results.append(
                    ToolMessage(
                        content=output,
                        tool_call_id=call_id,
                        name=name,
                    )
                )

            return {"messages": results}

        workflow.add_node("model", model_node)
        workflow.add_node("tools", tool_node)

        workflow.add_edge(START, "model")
        workflow.add_conditional_edges("model", should_continue, ["tools", END])
        workflow.add_edge("tools", "model")

        return workflow.compile()

    async def run(
        self,
        prompt: str,
        session_id: str = "default",
        approve_callback: ApproveCallback | None = None,
    ) -> str:
        """Run a single conversational turn and return the final assistant response."""
        if prompt.strip() == "/reset":
            self.taints.pop(session_id, None)
            return "Conversation cleared."

        original_cb = self.approve_callback
        if approve_callback is not None:
            self.approve_callback = approve_callback
        try:
            state = await self.run_state(prompt, session_id=session_id)
            for msg in reversed(state["messages"]):
                if isinstance(msg, AIMessage):
                    return str(msg.content)
            return ""
        finally:
            self.approve_callback = original_cb

    async def run_state(self, prompt: str, session_id: str = "default") -> AgentState:
        """Run a conversational turn and return the final LangGraph state."""
        initial_state: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "step_count": 0,
            "session_id": session_id,
        }
        final_state: AgentState = await self.graph.ainvoke(initial_state)
        return final_state


__all__ = ["Agent", "AgentState", "ApproveCallback", "ToolExecutor"]
