"""Comprehensive tests for provider streaming, tool call execution, AG-UI event ordering, and OTel tracing."""

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from yusufclaw.agent import Agent
from yusufclaw.models import (
    BedrockProvider,
    FakeStreamingProvider,
    ModelRequest,
    ModelRole,
    ModelRouter,
    OllamaProvider,
    OpenAIProvider,
    RoutingConfig,
    StreamAccumulator,
    StreamChunk,
    ToolCall,
    ToolCallDelta,
    ToolDefinition,
)
from yusufclaw.telemetry import (
    ATTR_GEN_AI_REQUEST_MODEL,
    ATTR_GEN_AI_SYSTEM,
    ATTR_ROUTING_PROVIDER,
    ATTR_ROUTING_ROLE,
    SPAN_MODEL_CALL,
    SPAN_TOOL_EXECUTION,
    reset_telemetry,
    setup_telemetry,
)


@pytest.fixture
def otel_exporter() -> InMemorySpanExporter:
    """Setup and reset in-memory OpenTelemetry tracer for verifying spans."""
    exporter = InMemorySpanExporter()
    setup_telemetry(service_name="test-streaming", span_exporter=exporter, force_reinit=True)
    yield exporter
    reset_telemetry()


@pytest.mark.asyncio
async def test_streaming_provider_emits_text_and_tool_call_in_order(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Verify that a streaming provider emitting text + tool call executes the tool,

    emits AG-UI events in order, and records model + tool spans in OpenTelemetry.
    """
    executed_tools: list[dict[str, Any]] = []

    async def mock_calculator(name: str, args: dict[str, Any]) -> str:
        executed_tools.append({"name": name, "args": args})
        if name == "calculator":
            expr = args.get("expr", "")
            return str(eval(expr, {"__builtins__": {}}))  # safe eval for simple arithmetic
        return "unknown tool"

    calc_tool = ToolDefinition(
        name="calculator",
        description="Evaluate arithmetic expression",
        input_schema={
            "type": "object",
            "properties": {"expr": {"type": "string"}},
            "required": ["expr"],
        },
    )

    # First turn: stream text chunks, then a tool call, and usage
    stream_turn_1: list[StreamChunk] = [
        StreamChunk(text="Let me compute "),
        StreamChunk(text="the sum: "),
        StreamChunk(
            tool_calls=[
                ToolCall(id="tc-calc-1", name="calculator", input={"expr": "19 + 23"})
            ]
        ),
        StreamChunk(usage={"prompt_tokens": 15, "completion_tokens": 8}),
    ]

    # Second turn (after tool result): stream text answer
    stream_turn_2: list[StreamChunk] = [
        StreamChunk(text="The result "),
        StreamChunk(text="is 42."),
        StreamChunk(usage={"prompt_tokens": 30, "completion_tokens": 6}),
    ]

    provider = FakeStreamingProvider(canned_streams=[stream_turn_1, stream_turn_2])

    # Record AG-UI events emitted via turn hook
    emitted_events: list[tuple[str, dict[str, Any]]] = []

    def event_collector(event_name: str, payload: dict[str, Any]) -> None:
        emitted_events.append((event_name, payload))

    agent = Agent(
        provider=provider,
        tools=[calc_tool],
        tool_executor=mock_calculator,
    )

    # Execute the conversational turn
    reply = await agent.handle(
        session="s-stream-1",
        request_id="req-stream-1",
        text="Calculate 19 + 23",
        on_event=event_collector,
    )

    # 1. Assert final reply from agent
    assert "The result is 42." in reply

    # 2. Assert tool execution
    assert len(executed_tools) == 1
    assert executed_tools[0]["name"] == "calculator"
    assert executed_tools[0]["args"] == {"expr": "19 + 23"}

    # 3. Assert AG-UI events were emitted in exact chronological order:
    # Text deltas from turn 1 -> tool_call_start -> tool_call_end -> Text deltas from turn 2
    event_names = [e[0] for e in emitted_events]

    # Verify event sequence
    assert "text_delta" in event_names
    assert "tool_call_start" in event_names
    assert "tool_call_end" in event_names

    # Check relative order
    first_text_idx = event_names.index("text_delta")
    tool_start_idx = event_names.index("tool_call_start")
    tool_end_idx = event_names.index("tool_call_end")
    last_text_idx = len(event_names) - 1 - event_names[::-1].index("text_delta")

    assert first_text_idx < tool_start_idx < tool_end_idx < last_text_idx

    # Check payloads
    tool_start_payload = next(p for n, p in emitted_events if n == "tool_call_start")
    assert tool_start_payload["id"] == "tc-calc-1"
    assert tool_start_payload["name"] == "calculator"
    assert tool_start_payload["args"] == {"expr": "19 + 23"}

    tool_end_payload = next(p for n, p in emitted_events if n == "tool_call_end")
    assert tool_end_payload["id"] == "tc-calc-1"
    assert tool_end_payload["name"] == "calculator"
    assert tool_end_payload["status"] == "success"
    assert tool_end_payload["result"] == "42"

    # 4. Assert OpenTelemetry spans recorded
    spans = otel_exporter.get_finished_spans()
    model_spans = [s for s in spans if s.name == SPAN_MODEL_CALL]
    tool_spans = [s for s in spans if s.name == SPAN_TOOL_EXECUTION]

    # Two model calls (initial query + follow-up with tool result)
    assert len(model_spans) == 2
    # One tool execution span
    assert len(tool_spans) == 1

    # Check model span attributes
    for m_span in model_spans:
        assert ATTR_GEN_AI_SYSTEM in m_span.attributes
        assert m_span.attributes[ATTR_GEN_AI_SYSTEM] == "fake"
        assert m_span.attributes["gen_ai.usage.prompt_tokens"] > 0
        assert m_span.attributes["gen_ai.usage.completion_tokens"] > 0

    # Check tool span attributes
    assert tool_spans[0].attributes["yusufclaw.tool.name"] == "calculator"
    assert tool_spans[0].attributes["yusufclaw.tool.status"] == "success"


@pytest.mark.asyncio
async def test_streaming_with_tool_call_deltas(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Verify that incremental ToolCallDelta chunks are properly assembled into a valid ToolCall."""
    executed_tools: list[dict[str, Any]] = []

    async def mock_echo(name: str, args: dict[str, Any]) -> str:
        executed_tools.append({"name": name, "args": args})
        return f"Echoed: {args.get('text', '')}"

    echo_tool = ToolDefinition(
        name="echo_tool",
        description="Echo input text",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )

    # First turn: stream tool call via incremental ToolCallDelta chunks
    stream_turn_1: list[StreamChunk] = [
        StreamChunk(text="Calling echo: "),
        StreamChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                id="call-delta-99",
                name="echo_tool",
            )
        ),
        StreamChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                args_delta='{"text": "',
            )
        ),
        StreamChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                args_delta='streamed payload"}',
            )
        ),
    ]

    # Second turn: final message
    stream_turn_2: list[StreamChunk] = [
        StreamChunk(text="All done!"),
    ]

    provider = FakeStreamingProvider(canned_streams=[stream_turn_1, stream_turn_2])

    agent = Agent(
        provider=provider,
        tools=[echo_tool],
        tool_executor=mock_echo,
    )

    reply = await agent.run("Please echo")
    assert reply == "All done!"
    assert len(executed_tools) == 1
    assert executed_tools[0]["name"] == "echo_tool"
    assert executed_tools[0]["args"] == {"text": "streamed payload"}


@pytest.mark.asyncio
async def test_streaming_with_routed_provider_attributes_on_span(
    otel_exporter: InMemorySpanExporter,
) -> None:
    """Verify that routed provider and role attributes are attached to the model span during streaming."""
    cheap_streaming = FakeStreamingProvider(default_text="Cheap stream reply")
    cheap_streaming.provider_name = "ollama"  # type: ignore[attr-defined]
    cheap_streaming.model = "llama3.2"  # type: ignore[attr-defined]

    router = ModelRouter(
        providers={"cheap": cheap_streaming},
        config=RoutingConfig(
            enabled=True,
            default_provider="cheap",
            role_routes={"default": "cheap"},
        ),
    )

    agent = Agent(
        provider=router,
    )

    reply = await agent.run("Hello streaming router")
    assert reply == "Cheap stream reply"

    spans = otel_exporter.get_finished_spans()
    model_span = next((s for s in spans if s.name == SPAN_MODEL_CALL), None)
    assert model_span is not None
    assert model_span.attributes.get(ATTR_ROUTING_PROVIDER) == "cheap"
    assert model_span.attributes.get(ATTR_ROUTING_ROLE) == ModelRole.DEFAULT.value
    assert model_span.attributes.get(ATTR_GEN_AI_REQUEST_MODEL) == "llama3.2"


@pytest.mark.asyncio
async def test_streaming_fallback_when_not_implemented() -> None:
    """Verify that if stream raises NotImplementedError, Agent cleanly falls back to complete()."""
    class NonStreamingProvider(FakeStreamingProvider):
        async def stream(self, request: ModelRequest) -> AsyncIterator[StreamChunk]:
            if False:
                yield StreamChunk()
            raise NotImplementedError("Streaming not supported on this device")

    provider = NonStreamingProvider(default_text="Fallback response via complete()")
    agent = Agent(provider=provider)

    events: list[str] = []
    reply = await agent.handle(
        session="s-fallback",
        request_id="req-fallback",
        text="hi",
        on_event=lambda name, payload: events.append(name),
    )

    assert reply == "Fallback response via complete()"
    assert "text_delta" in events


@pytest.mark.asyncio
async def test_stream_accumulator_direct() -> None:
    """Verify StreamAccumulator correctly accumulates deltas, JSON args, and metadata."""
    acc = StreamAccumulator()

    # Add text delta
    t1, tc_d1, new_tc1 = acc.add("Hello ")
    assert t1 == "Hello "
    assert tc_d1 is None
    assert new_tc1 == []

    # Add StreamChunk with tool call delta
    t2, tc_d2, new_tc2 = acc.add(
        StreamChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                id="call-1",
                name="test_tool",
                args_delta='{"key": ',
            )
        )
    )
    assert t2 == ""
    assert tc_d2 is not None
    assert tc_d2.name == "test_tool"

    # Add next args delta
    acc.add(
        StreamChunk(
            tool_call_delta=ToolCallDelta(
                index=0,
                args_delta='"value"}',
            )
        )
    )

    # Add usage
    acc.add(StreamChunk(usage={"prompt_tokens": 12, "completion_tokens": 5}))

    response = acc.finalize()
    assert response.text == "Hello "
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "test_tool"
    assert response.tool_calls[0].input == {"key": "value"}
    assert response.usage == {"prompt_tokens": 12, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_openai_provider_stream_mocked() -> None:
    """Verify OpenAIProvider.stream formats chunks and produces final ModelResponse."""
    provider = OpenAIProvider(api_key="mock", model="gpt-4o")

    # Mock chunk 1: text
    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta.content = "Streamed "
    chunk1.choices[0].delta.tool_calls = None
    chunk1.usage = None

    # Mock chunk 2: tool call delta
    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "tc-mock-1"
    tc_delta.function.name = "get_weather"
    tc_delta.function.arguments = '{"city": "Paris"}'

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta.content = None
    chunk2.choices[0].delta.tool_calls = [tc_delta]
    chunk2.usage = None

    # Mock chunk 3: usage
    chunk3 = MagicMock()
    chunk3.choices = []
    chunk3.usage.prompt_tokens = 10
    chunk3.usage.completion_tokens = 15

    async def fake_stream_iter() -> AsyncIterator[Any]:
        yield chunk1
        yield chunk2
        yield chunk3

    with patch.object(provider, "_build_client") as mock_build:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream_iter())
        mock_build.return_value = mock_client

        chunks: list[StreamChunk] = []
        async for c in provider.stream(ModelRequest(messages=[])):
            chunks.append(c)

        # Chunks yielded + final response chunk
        assert len(chunks) == 4
        assert chunks[0].text == "Streamed "
        assert chunks[1].tool_call_delta is not None
        assert chunks[1].tool_call_delta.name == "get_weather"
        assert chunks[2].usage == {"prompt_tokens": 10, "completion_tokens": 15}

        final = chunks[3].response
        assert final is not None
        assert final.text == "Streamed "
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "get_weather"
        assert final.tool_calls[0].input == {"city": "Paris"}
        assert final.usage == {"prompt_tokens": 10, "completion_tokens": 15}


@pytest.mark.asyncio
async def test_ollama_provider_stream_mocked() -> None:
    """Verify OllamaProvider.stream formats streaming JSON lines."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")

    line1 = json.dumps({"message": {"content": "Hello Ollama "}, "done": False})
    line2 = json.dumps({
        "message": {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "search", "arguments": {"q": "cats"}}}],
        },
        "done": False,
    })
    line3 = json.dumps({"done": True, "prompt_eval_count": 8, "eval_count": 14})

    async def fake_aiter_lines() -> AsyncIterator[str]:
        yield line1
        yield line2
        yield line3

    mock_resp = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines
    mock_resp.raise_for_status = MagicMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp

    with patch("httpx.AsyncClient.stream", return_value=mock_cm):
        chunks: list[StreamChunk] = []
        async for c in provider.stream(ModelRequest(messages=[])):
            chunks.append(c)

        assert len(chunks) == 4
        assert chunks[0].text == "Hello Ollama "
        assert len(chunks[1].tool_calls) == 1
        assert chunks[1].tool_calls[0].name == "search"
        assert chunks[2].usage == {"prompt_tokens": 8, "completion_tokens": 14}

        final = chunks[3].response
        assert final is not None
        assert final.text == "Hello Ollama "
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "search"
        assert final.usage == {"prompt_tokens": 8, "completion_tokens": 14}


@pytest.mark.asyncio
async def test_bedrock_provider_stream_mocked() -> None:
    """Verify BedrockProvider.stream handles ConverseStream events."""
    mock_client = MagicMock()
    mock_client.converse_stream.return_value = {
        "stream": [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Bedrock streamed "}}},
            {
                "contentBlockStart": {
                    "contentBlockIndex": 1,
                    "start": {"toolUse": {"toolUseId": "tu_1", "name": "do_work"}},
                }
            },
            {
                "contentBlockDelta": {
                    "contentBlockIndex": 1,
                    "delta": {"toolUse": {"input": '{"step": 1}'}},
                }
            },
            {"metadata": {"usage": {"inputTokens": 20, "outputTokens": 30}}},
        ]
    }

    provider = BedrockProvider(model_id="anthropic.claude", client=mock_client)

    chunks: list[StreamChunk] = []
    async for c in provider.stream(ModelRequest(messages=[])):
        chunks.append(c)

    assert len(chunks) == 5
    assert chunks[0].text == "Bedrock streamed "
    assert chunks[1].tool_call_delta is not None
    assert chunks[1].tool_call_delta.name == "do_work"
    assert chunks[2].tool_call_delta is not None
    assert chunks[2].tool_call_delta.args_delta == '{"step": 1}'
    assert chunks[3].usage == {"prompt_tokens": 20, "completion_tokens": 30}

    final = chunks[4].response
    assert final is not None
    assert final.text == "Bedrock streamed "
    assert len(final.tool_calls) == 1
    assert final.tool_calls[0].name == "do_work"
    assert final.tool_calls[0].input == {"step": 1}
    assert final.usage == {"prompt_tokens": 20, "completion_tokens": 30}
