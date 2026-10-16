"""Minimal LangGraph agent loop for YusufClaw with security policies and memory.

Defines a clean interface over a LangGraph state machine consisting of:
- AgentState: holds message history, step count, and session context.
- model_node: executes provider completion with system prompt, memory context, and available tools.
- tool_node: evaluates deterministic security policies, tracks session taints,
  manages human-in-the-loop ActionCard approvals, and invokes tool handlers.
- conditional edge: determines whether to continue executing tools or end the turn.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from yusufclaw.memory.context import session_scope
from yusufclaw.memory.tools import MemoryTool, create_memory_tools
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
from yusufclaw.telemetry import (
    record_model_response,
    trace_agent_turn,
    trace_approval_wait,
    trace_model_call,
    trace_tool_call,
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
        memory: Any | None = None,
        system_prompt: str = "You are YusufClaw, an autonomous personal AI agent.",
        tools: Sequence[ToolDefinition] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 8,
        grants: Grants | None = None,
        card_store: ActionCardStore | None = None,
        approve_callback: ApproveCallback | None = None,
        skills: str | None = None,
    ) -> None:
        if isinstance(memory, str):
            system_prompt = memory
            memory = None

        self.provider = provider
        self.memory = memory
        self.system_prompt = system_prompt
        self.skills = skills
        self.tools = list(tools) if tools else []
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.grants = grants
        self.card_store = card_store or ActionCardStore()
        self.approve_callback = approve_callback
        self.taints: dict[str, SessionTaint] = {}

        self._memory_tools_map: dict[str, MemoryTool] = {}
        if self.memory is not None:
            mem_tools = create_memory_tools(self.memory)
            for mt in mem_tools:
                self._memory_tools_map[mt.name] = mt
                if not any(t.name == mt.name for t in self.tools):
                    self.tools.append(mt)

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
            session_id = state.get("session_id", "default")

            system_parts = [self.system_prompt]
            if self.skills:
                system_parts.append(self.skills)
            if self.memory is not None:
                user_text = ""
                for msg in reversed(model_messages):
                    if msg.role == "user":
                        user_text = msg.content
                        break

                assemble_fn = getattr(self.memory, "assemble_context", None)
                if assemble_fn:
                    if inspect.iscoroutinefunction(assemble_fn):
                        mem_ctx = await assemble_fn(session_id, user_text)
                    else:
                        mem_ctx = assemble_fn(session_id, user_text)
                    if mem_ctx:
                        system_parts.append(mem_ctx)
            system = "\n\n".join(system_parts)

            request = ModelRequest(
                system=system,
                messages=model_messages,
                tools=self.tools,
            )
            async with trace_model_call(self.provider, request) as model_span:
                response = await self.provider.complete(request)
                record_model_response(model_span, response, provider=self.provider)

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

                # Memory tools: execute directly (trusted, no egress)
                if name in self._memory_tools_map:
                    async with trace_tool_call(
                        name=name, call_id=call_id, args=args, is_guarded=False
                    ) as tool_ctx:
                        try:
                            output = await self._memory_tools_map[name].execute(args)
                            tool_ctx.set_status("success")
                        except Exception as e:
                            output = f"Tool execution error: {e}"
                            tool_ctx.set_status("error")
                    results.append(
                        ToolMessage(
                            content=output,
                            tool_call_id=call_id,
                            name=name,
                        )
                    )
                    continue

                # 1. Unknown tool check
                if tool_def is None and self.tool_executor is None:
                    async with trace_tool_call(
                        name=name, call_id=call_id, args=args, is_guarded=False
                    ) as tool_ctx:
                        tool_ctx.set_status("error")
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
                    async with trace_tool_call(
                        name=name,
                        call_id=call_id,
                        args=args,
                        is_guarded=isinstance(tool_def, GuardedTool),
                    ) as tool_ctx:
                        tool_ctx.set_status("policy_denied")
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
                        async with trace_approval_wait(
                            action_card=action_card,
                            tool_name=name,
                        ) as app_ctx:
                            try:
                                tc_obj = ToolCall(id=call_id, name=name, input=args)
                                approved = await self.approve_callback(tc_obj, action_card)
                                app_ctx.set_status("approved" if approved else "denied")
                            except Exception:
                                approved = False
                                app_ctx.set_status("error")

                    if not approved:
                        async with trace_tool_call(
                            name=name,
                            call_id=call_id,
                            args=args,
                            is_guarded=isinstance(tool_def, GuardedTool),
                        ) as tool_ctx:
                            tool_ctx.set_status("approval_denied")
                        results.append(
                            ToolMessage(
                                content="Tool approval denied",
                                tool_call_id=call_id,
                                name=name,
                            )
                        )
                        continue

                # 6. Tool execution
                async with trace_tool_call(
                    name=name,
                    call_id=call_id,
                    args=args,
                    is_guarded=isinstance(tool_def, GuardedTool),
                ) as tool_ctx:
                    try:
                        if isinstance(tool_def, GuardedTool):
                            output = await tool_def.execute(args)
                        elif self.tool_executor:
                            output = await self.tool_executor(name, args)
                        else:
                            output = f"Executed {name} with args {args}"
                        tool_ctx.set_status("success")

                        # 7. Update taint if tool ingested untrusted data
                        if "read_untrusted" in manifest.effects:
                            taint.contaminated = True
                            if name not in taint.sources:
                                taint.sources.append(name)
                    except Exception as e:
                        tool_ctx.set_status("error")
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
        async with trace_agent_turn(session_id=session_id) as turn_span:
            initial_state: AgentState = {
                "messages": [HumanMessage(content=prompt)],
                "step_count": 0,
                "session_id": session_id,
            }
            final_state: AgentState = await self.graph.ainvoke(initial_state)
            if turn_span.is_recording():
                turn_span.set_attribute("yusufclaw.turn.step_count", final_state.get("step_count", 0))
            return final_state

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
    ) -> str:
        """Handle turn with session context, deduplication, commands, and persistence."""
        with session_scope(session, request_id):
            clean = text.strip()
            if not clean or len(text) > 16_000:
                raise ValueError("Message must contain 1–16000 characters")

            # Local commands
            if clean == "/reset":
                self.taints.pop(session, None)
                if self.memory is not None:
                    clear_fn = getattr(self.memory, "clear", None)
                    if clear_fn:
                        if inspect.iscoroutinefunction(clear_fn):
                            await clear_fn(session)
                        else:
                            clear_fn(session)
                return "Conversation cleared."

            if clean in ("/help", "/start"):
                return "Send a message to chat. /reset clears this conversation; /help shows this message."

            # Request ID deduplication
            if self.memory is not None:
                reply_fn = getattr(self.memory, "reply", None)
                if reply_fn:
                    if inspect.iscoroutinefunction(reply_fn):
                        cached = await reply_fn(session, request_id)
                    else:
                        cached = reply_fn(session, request_id)
                    if cached is not None:
                        return str(cached)

            # Retrieve prior conversation messages
            history_messages: list[BaseMessage] = []
            if self.memory is not None:
                hist_fn = getattr(self.memory, "history", None)
                if hist_fn:
                    if inspect.iscoroutinefunction(hist_fn):
                        raw_history = await hist_fn(session)
                    else:
                        raw_history = hist_fn(session)
                    for h in raw_history:
                        role = h.get("role")
                        content = h.get("content") or h.get("text") or ""
                        if role == "user":
                            history_messages.append(HumanMessage(content=str(content)))
                        elif role == "assistant":
                            history_messages.append(AIMessage(content=str(content)))

            original_cb = self.approve_callback
            if approve is not None:
                self.approve_callback = approve
            try:
                initial_state: AgentState = {
                    "messages": [*history_messages, HumanMessage(content=text)],
                    "step_count": 0,
                    "session_id": session,
                }
                async with trace_agent_turn(session_id=session, request_id=request_id) as turn_span:
                    final_state: AgentState = await self.graph.ainvoke(initial_state)
                    if turn_span.is_recording():
                        turn_span.set_attribute("yusufclaw.turn.step_count", final_state.get("step_count", 0))
            finally:
                self.approve_callback = original_cb

            final_reply = ""
            for msg in reversed(final_state["messages"]):
                if isinstance(msg, AIMessage):
                    final_reply = str(msg.content)
                    break

            # Persist turn in memory
            if self.memory is not None:
                save_fn = getattr(self.memory, "save", None)
                if save_fn:
                    turn_to_save = [
                        {"role": "user", "content": text},
                        {"role": "assistant", "content": final_reply},
                    ]
                    if inspect.iscoroutinefunction(save_fn):
                        await save_fn(session, request_id, turn_to_save, final_reply)
                    else:
                        save_fn(session, request_id, turn_to_save, final_reply)

            return final_reply


__all__ = ["Agent", "AgentState", "ApproveCallback", "ToolExecutor"]
