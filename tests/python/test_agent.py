"""Tests for the LangGraph agent loop."""

from typing import Any

import pytest

from yusufclaw.agent import Agent
from yusufclaw.models import FakeProvider, ModelResponse, ToolCall, ToolDefinition


@pytest.mark.asyncio
async def test_agent_single_turn_no_tools() -> None:
    """Verify single-turn agent execution without tool calls."""
    provider = FakeProvider(default_text="Hello from YusufClaw!")
    agent = Agent(provider=provider)

    reply = await agent.run("Hello there")
    assert reply == "Hello from YusufClaw!"
    assert len(provider.recorded_requests) == 1


@pytest.mark.asyncio
async def test_agent_turn_with_tool_call() -> None:
    """Verify multi-step agent execution with tool call and tool execution."""
    canned_responses = [
        ModelResponse(
            text="Let me calculate that for you.",
            tool_calls=[
                ToolCall(id="call_calc_1", name="add", input={"a": 10, "b": 32})
            ],
        ),
        ModelResponse(text="The calculated total is 42."),
    ]
    provider = FakeProvider(canned_responses=canned_responses)

    async def mock_executor(name: str, args: dict[str, Any]) -> str:
        if name == "add":
            return str(args.get("a", 0) + args.get("b", 0))
        return "Unknown tool"

    tool_def = ToolDefinition(
        name="add",
        description="Add two numbers",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        },
    )

    agent = Agent(
        provider=provider,
        tools=[tool_def],
        tool_executor=mock_executor,
    )

    final_reply = await agent.run("Add 10 and 32")
    assert final_reply == "The calculated total is 42."
    assert len(provider.recorded_requests) == 2


@pytest.mark.asyncio
async def test_agent_max_steps_boundary() -> None:
    """Verify agent loop terminates when max_steps limit is reached."""
    # Endless tool calling canned responses
    canned_responses = [
        ModelResponse(
            text=f"Tool step {i}",
            tool_calls=[ToolCall(id=f"call_{i}", name="ping", input={})],
        )
        for i in range(10)
    ]
    provider = FakeProvider(canned_responses=canned_responses)
    agent = Agent(provider=provider, max_steps=3)

    state = await agent.run_state("Start looping")
    # Step count should cap at 3
    assert state["step_count"] == 3
