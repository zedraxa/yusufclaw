"""Minimal LangGraph agent loop for YusufClaw.

Defines a clean interface over a LangGraph state machine consisting of:
- AgentState: holds message history and step count.
- model_node: executes provider completion with system prompt and available tools.
- tool_node: invokes tool handlers for any requested tool calls.
- conditional edge: determines whether to continue executing tools or end the turn.
"""

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
        system_prompt: str = "You are YusufClaw, an autonomous personal AI agent.",
        tools: Sequence[ToolDefinition] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 8,
    ) -> None:
        self.provider = provider
        self.system_prompt = system_prompt
        self.tools = list(tools) if tools else []
        self.tool_executor = tool_executor
        self.max_steps = max_steps
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

            for call in tool_calls:
                call_id = call.get("id") or str(uuid.uuid4())
                name = call.get("name", "")
                args = call.get("args", {})

                if self.tool_executor:
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


__all__ = ["Agent", "AgentState", "ToolExecutor"]
