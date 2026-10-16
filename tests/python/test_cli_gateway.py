"""Tests for CLI gateway conforming to TypeScript spec (interactive + one-shot)."""

import io
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from yusufclaw.gateway.bus import MessageBus
from yusufclaw.gateway.cli import cli_app, run_cli


class FakeAgent:
    def __init__(self, reply: str = "CLI reply") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def handle(
        self,
        session: str,
        request_id: str,
        text: str,
        approve: Any = None,
        signal: Any = None,
    ) -> str:
        self.calls.append(
            {
                "session": session,
                "request_id": request_id,
                "text": text,
                "approve": approve,
            }
        )
        if approve is not None:
            # Simulate invoking tool approval callback
            dummy_tool_call = MagicMock()
            dummy_tool_call.name = "shell"
            dummy_tool_call.input = {"command": "ls"}
            approved = await approve(dummy_tool_call)
            if not approved:
                return "Action denied"
        return self.reply


@pytest.mark.asyncio
async def test_run_cli_one_shot_mode() -> None:
    """run_cli in one-shot mode sends message, prints reply, and exits."""
    agent = FakeAgent(reply="The clock was checked.")
    bus = MessageBus(agent, cli={"local"})

    out = io.StringIO()
    err = io.StringIO()

    await run_cli(
        bus=bus,
        user_id="local",
        args=["--message", "What time is it?", "--session", "custom-session"],
        stdout=out,
        stderr=err,
    )

    assert "The clock was checked." in out.getvalue()
    assert len(agent.calls) == 1
    assert agent.calls[0]["text"] == "What time is it?"
    assert "custom-session" in agent.calls[0]["session"]


@pytest.mark.asyncio
async def test_run_cli_non_interactive_without_message_fails() -> None:
    """run_cli without --message raises RuntimeError if stdin is not a TTY."""
    agent = FakeAgent()
    bus = MessageBus(agent, cli={"local"})

    fake_stdin = io.StringIO("")  # not a TTY

    with pytest.raises(RuntimeError, match="Use --message for non-interactive input"):
        await run_cli(
            bus=bus,
            user_id="local",
            args=[],
            stdin=fake_stdin,
        )


@pytest.mark.asyncio
async def test_run_cli_interactive_session_with_approvals() -> None:
    """run_cli runs interactive loop, prompts for input and approvals, and handles /exit."""
    agent = FakeAgent(reply="Command completed.")
    bus = MessageBus(agent, cli={"local"})

    # Mock stdin pretending to be a TTY
    class MockTtyStdin(io.StringIO):
        def isatty(self) -> bool:
            return True

    # User enters: 'run command', approves with 'yes', then quits with '/exit'
    input_text = "run command\nyes\n/exit\n"
    stdin = MockTtyStdin(input_text)
    stdout = io.StringIO()
    stderr = io.StringIO()

    await run_cli(
        bus=bus,
        user_id="local",
        args=[],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()
    assert "YusufClaw — /help for commands, /exit to quit." in output
    assert "You> " in output
    assert "Approve shell" in output
    assert "YusufClaw> Command completed." in output
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_run_cli_error_sanitization() -> None:
    """run_cli catches unexpected errors and prints sanitized error message to stderr."""

    class ThrowingAgent:
        async def handle(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("CRITICAL API SECRET")

    bus = MessageBus(ThrowingAgent(), cli={"local"})

    class MockTtyStdin(io.StringIO):
        def isatty(self) -> bool:
            return True

    stdin = MockTtyStdin("test error\n/exit\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    await run_cli(
        bus=bus,
        user_id="local",
        args=[],
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )

    err_output = stderr.getvalue()
    assert "CRITICAL API SECRET" not in err_output
    assert "Request failed. Check provider configuration and connectivity" in err_output


def test_typer_cli_commands() -> None:
    """Verify typer subcommands turn, chat, and version continue working."""
    runner = CliRunner()

    res_version = runner.invoke(cli_app, ["version"])
    assert res_version.exit_code == 0
    assert "YusufClaw v" in res_version.stdout

    res_turn = runner.invoke(cli_app, ["turn", "hi", "--provider", "fake"])
    assert res_turn.exit_code == 0
    assert "hi" in res_turn.stdout

    res_one_shot = runner.invoke(cli_app, ["--message", "ping"])
    assert res_one_shot.exit_code == 0
    assert "ping" in res_one_shot.stdout
