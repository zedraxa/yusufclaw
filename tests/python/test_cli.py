"""Tests for Typer CLI entrypoint."""

from typer.testing import CliRunner

from yusufclaw import __version__
from yusufclaw.gateway.cli import cli_app

runner = CliRunner()


def test_cli_help() -> None:
    """Verify CLI help output."""
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "YusufClaw" in result.stdout
    assert "turn" in result.stdout
    assert "chat" in result.stdout


def test_cli_version() -> None:
    """Verify CLI version command."""
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert f"YusufClaw v{__version__}" in result.stdout


def test_cli_turn_fake_provider() -> None:
    """Verify running a single turn via CLI with fake provider."""
    result = runner.invoke(
        cli_app,
        ["turn", "Hello from test suite", "--provider", "fake"],
    )
    assert result.exit_code == 0
    assert "Hello from test suite" in result.stdout


def test_cli_chat_argument() -> None:
    """Verify running chat command with direct argument message."""
    result = runner.invoke(
        cli_app,
        ["chat", "How is the weather?", "--provider", "fake"],
    )
    assert result.exit_code == 0
    assert "YusufClaw:" in result.stdout
    assert "How is the weather?" in result.stdout
