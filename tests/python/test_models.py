"""Tests for model provider interfaces and implementations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from yusufclaw.config import Settings
from yusufclaw.models import (
    BedrockProvider,
    FakeProvider,
    Message,
    ModelRequest,
    ModelResponse,
    OllamaProvider,
    OpenAIProvider,
    ToolCall,
    ToolDefinition,
    get_model_provider,
)


@pytest.mark.asyncio
async def test_fake_provider_default() -> None:
    """Test fake provider with default fallback text."""
    provider = FakeProvider(default_text="Hello test")
    request = ModelRequest(messages=[Message(role="user", content="ping")])
    response = await provider.complete(request)
    assert response.text == "Hello test"
    assert len(provider.recorded_requests) == 1
    assert provider.recorded_requests[0].messages[0].content == "ping"


@pytest.mark.asyncio
async def test_fake_provider_canned_responses() -> None:
    """Test fake provider sequential canned responses."""
    canned = [
        ModelResponse(text="first response"),
        ModelResponse(
            text="",
            tool_calls=[ToolCall(id="c1", name="search", input={"query": "test"})],
        ),
    ]
    provider = FakeProvider(canned_responses=canned)
    req = ModelRequest(messages=[Message(role="user", content="hi")])

    resp1 = await provider.complete(req)
    assert resp1.text == "first response"

    resp2 = await provider.complete(req)
    assert len(resp2.tool_calls) == 1
    assert resp2.tool_calls[0].name == "search"
    assert resp2.tool_calls[0].input == {"query": "test"}


@pytest.mark.asyncio
async def test_bedrock_provider_mocked() -> None:
    """Test Bedrock provider Converse API translation with mocked boto3 client."""
    mock_client = MagicMock()
    mock_client.converse.return_value = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "Searching now."},
                    {
                        "toolUse": {
                            "toolUseId": "call_123",
                            "name": "lookup",
                            "input": {"id": 42},
                        }
                    },
                ],
            }
        },
        "usage": {"inputTokens": 15, "outputTokens": 25},
    }

    provider = BedrockProvider(
        model_id="us.anthropic.claude-3-5-sonnet",
        profile="test-profile",
        region="us-east-1",
        client=mock_client,
    )

    request = ModelRequest(
        system="System prompt",
        messages=[
            Message(role="user", content="find id 42"),
            Message(role="tool", content="42 found", tool_call_id="call_000"),
        ],
        tools=[
            ToolDefinition(
                name="lookup",
                description="Lookup item",
                input_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
            )
        ],
    )

    response = await provider.complete(request)
    assert response.text == "Searching now."
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_123"
    assert response.tool_calls[0].name == "lookup"
    assert response.tool_calls[0].input == {"id": 42}
    assert response.usage == {"prompt_tokens": 15, "completion_tokens": 25}

    mock_client.converse.assert_called_once()
    call_kwargs = mock_client.converse.call_args.kwargs
    assert call_kwargs["modelId"] == "us.anthropic.claude-3-5-sonnet"
    assert call_kwargs["system"] == [{"text": "System prompt"}]


@pytest.mark.asyncio
async def test_bedrock_provider_missing_model_id() -> None:
    """Test Bedrock provider raises ValueError if model_id is empty."""
    provider = BedrockProvider(model_id="", client=MagicMock())
    request = ModelRequest(messages=[Message(role="user", content="hi")])
    with pytest.raises(ValueError, match="bedrock_model_id is required"):
        await provider.complete(request)


@pytest.mark.asyncio
async def test_ollama_provider_mocked() -> None:
    """Test Ollama provider HTTP request formatting and response parsing."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
    request = ModelRequest(
        system="You are helpful",
        messages=[Message(role="user", content="hello")],
        tools=[
            ToolDefinition(
                name="echo",
                description="echo string",
                input_schema={"type": "object"},
            )
        ],
    )

    fake_resp_json = {
        "message": {
            "role": "assistant",
            "content": "Hi there!",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {"name": "echo", "arguments": {"text": "hi"}},
                }
            ],
        },
        "prompt_eval_count": 8,
        "eval_count": 12,
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_resp_json
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        response = await provider.complete(request)

        assert response.text == "Hi there!"
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "echo"
        assert response.tool_calls[0].input == {"text": "hi"}
        assert response.usage == {"prompt_tokens": 8, "completion_tokens": 12}


@pytest.mark.asyncio
async def test_openai_provider_mocked() -> None:
    """Test OpenAI provider chat completions mapping."""
    provider = OpenAIProvider(
        base_url="https://api.openai.com/v1",
        api_key="mock-key",
        model="gpt-4o-mini",
    )
    request = ModelRequest(
        system="System rule",
        messages=[Message(role="user", content="do task")],
    )

    mock_choice = MagicMock()
    mock_choice.message.content = "Done task"
    mock_choice.message.tool_calls = None

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_completion.usage.prompt_tokens = 5
    mock_completion.usage.completion_tokens = 7

    with patch.object(
        provider,
        "_build_client",
    ) as mock_build:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        mock_build.return_value = mock_client

        response = await provider.complete(request)
        assert response.text == "Done task"
        assert response.tool_calls == []
        assert response.usage == {"prompt_tokens": 5, "completion_tokens": 7}


def test_get_model_provider_factory() -> None:
    """Test model provider factory returns proper instances according to config."""
    s_fake = Settings(model_provider="fake")
    assert isinstance(get_model_provider(s_fake), FakeProvider)

    s_ollama = Settings(model_provider="ollama")
    assert isinstance(get_model_provider(s_ollama), OllamaProvider)

    s_openai = Settings(model_provider="openai")
    assert isinstance(get_model_provider(s_openai), OpenAIProvider)

    s_bedrock = Settings(model_provider="bedrock", bedrock_model_id="anthropic.claude")
    assert isinstance(get_model_provider(s_bedrock), BedrockProvider)
