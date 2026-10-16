"""Minimal LangGraph agent loop for YusufClaw.

Defines a clean interface over a LangGraph state machine consisting of:
- AgentState: holds message history and step count.
- model_node: executes provider completion with system prompt, memory context, and available tools.
- tool_node: invokes tool handlers for any requested tool calls.
- conditional edge: determines whether to continue executing tools or end the turn.
"""

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

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


class AgentState(TypedDict):
    """Execution state for the LangGraph agent."""

    messages: Annotated[list[BaseMessage], add_messages]
    step_count: int
    session_id: str


class Agent:
    """Core agent loop wrapping a compiled LangGraph state graph."""

    def __init__(
        self,
        provider: ModelProvider,
        memory: Any | None = None,
        system_prompt: str = "You are YusufClaw, an autonomous personal AI agent.",
        tools: Sequence[ToolDefinition] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 8,
    ) -> None:
        if isinstance(memory, str):
            system_prompt = memory
            memory = None

        self.provider = provider
        self.memory = memory
        self.system_prompt = system_prompt
        self.tools = list(tools) if tools else []
        self.tool_executor = tool_executor
        self.max_steps = max_steps

        self._memory_tools_map: dict[str, MemoryTool] = {}
        if self.memory is not None:
            mem_tools = create_memory_tools(self.memory)
            for mt in mem_tools:
                self._memory_tools_map[mt.name] = mt
                if not any(t.name == mt.name for t in self.tools):
                    self.tools.append(mt)

        self.graph = self._build_graph()

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

            system = self.system_prompt
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
                        system = f"{self.system_prompt}\n\n{mem_ctx}"

            request = ModelRequest(
                system=system,
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

            for call in tool_calls:
                call_id = call.get("id") or str(uuid.uuid4())
                name = call.get("name", "")
                args = call.get("args", {})

                if name in self._memory_tools_map:
                    try:
                        output = await self._memory_tools_map[name].execute(args)
                    except Exception as e:
                        output = f"Tool execution error: {e}"
                elif self.tool_executor:
                    try:
                        output = await self.tool_executor(name, args)
                    except Exception as e:
                        output = f"Tool execution error: {e}"
                else:
                    output = f"Executed {name} with args {args}"

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

    async def run(self, prompt: str, session_id: str = "default") -> str:
        """Run a single conversational turn and return the final assistant response."""
        state = await self.run_state(prompt, session_id=session_id)
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                return str(msg.content)
        return ""

    async def run_state(self, prompt: str, session_id: str = "default") -> AgentState:
        """Run a conversational turn and return the final LangGraph state."""
        initial_state: AgentState = {
            "messages": [HumanMessage(content=prompt)],
            "step_count": 0,
            "session_id": session_id,
        }
        final_state: AgentState = await self.graph.ainvoke(initial_state)
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

            initial_state: AgentState = {
                "messages": [*history_messages, HumanMessage(content=text)],
                "step_count": 0,
                "session_id": session,
            }
            final_state: AgentState = await self.graph.ainvoke(initial_state)

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


__all__ = ["Agent", "AgentState", "ToolExecutor"]
