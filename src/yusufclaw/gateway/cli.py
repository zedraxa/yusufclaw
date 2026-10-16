"""CLI gateway for YusufClaw supporting interactive terminal and one-shot turns."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

import typer
from rich.console import Console

from yusufclaw import __version__
from yusufclaw.config import Settings, get_settings
from yusufclaw.gateway.bus import IncomingMessage, MessageBus
from yusufclaw.models import ModelProvider, get_model_provider
from yusufclaw.models.fake import FakeProvider

cli_app = typer.Typer(
    name="yusufclaw",
    help="YusufClaw — autonomous personal AI agent CLI",
    add_completion=False,
    invoke_without_command=True,
)
console = Console()


async def run_cli(
    bus: MessageBus,
    user_id: str,
    args: list[str],
    signal: Any = None,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
) -> None:
    """Execute CLI in one-shot or interactive mode conforming to TypeScript CLI spec."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    in_stream = stdin or sys.stdin

    parser = argparse.ArgumentParser(prog="yusufclaw cli", add_help=False)
    parser.add_argument("-m", "--message", type=str, default=None)
    parser.add_argument("-s", "--session", type=str, default="default")
    parsed, _ = parser.parse_known_args(args)

    async def send(text: str, approve: Any = None) -> str:
        return await bus.dispatch(
            IncomingMessage(
                channel="cli",
                userId=user_id,
                conversationId=parsed.session,
                requestId=str(uuid.uuid4()),
                text=text,
            ),
            approve=approve,
            signal=signal,
        )

    # One-shot mode via --message / -m
    if parsed.message is not None:
        result = await send(parsed.message)
        out.write(result + "\n")
        out.flush()
        return

    # Non-interactive without --message must fail fast
    is_tty = getattr(in_stream, "isatty", lambda: False)()
    if not is_tty:
        raise RuntimeError("Use --message for non-interactive input")

    out.write("YusufClaw — /help for commands, /exit to quit.\n")
    out.flush()

    while True:
        if signal is not None and getattr(signal, "is_set", lambda: False)():
            break

        out.write("You> ")
        out.flush()
        line = in_stream.readline()
        if not line:
            break

        line_clean = line.strip()
        if line_clean == "/exit":
            break
        if not line_clean:
            continue

        async def cli_approve(call: Any, card: Any = None) -> bool:
            name = getattr(call, "name", "") or (call.get("name") if isinstance(call, dict) else "")
            call_input = getattr(call, "input", {}) or (call.get("input") if isinstance(call, dict) else {})
            out.write(f"Approve {name} {json.dumps(call_input)}? Type yes: ")
            out.flush()
            ans = in_stream.readline()
            return ans.strip().lower() == "yes"

        try:
            result = await send(line_clean, approve=cli_approve)
            out.write(f"YusufClaw> {result}\n")
            out.flush()
        except Exception:
            if signal is not None and getattr(signal, "is_set", lambda: False)():
                break
            err.write(
                "Request failed. Check provider configuration and connectivity, then retry.\n"
            )
            err.flush()


async def _run_agent_turn(
    prompt: str,
    provider_name: str,
    session_id: str,
    settings: Settings,
) -> str:
    """Execute a single agent turn asynchronously."""
    provider: ModelProvider
    if provider_name.lower() == "fake":
        provider = FakeProvider(
            default_text=f"YusufClaw (fake): Received your message '{prompt}'."
        )
    else:
        overridden = settings.model_copy(update={"model_provider": provider_name.lower()})
        provider = get_model_provider(overridden)

    from yusufclaw.agent import Agent

    agent = Agent(
        provider=provider,
        max_steps=settings.max_agent_steps,
    )
    return await agent.run(prompt, session_id=session_id)


@cli_app.callback()
def main_callback(
    ctx: typer.Context,
    message: str | None = typer.Option(
        None,
        "-m",
        "--message",
        help="One-shot prompt message to send to the agent",
    ),
    session: str = typer.Option(
        "default",
        "-s",
        "--session",
        help="Session identifier for conversation memory",
    ),
) -> None:
    """Handle root command with optional --message or fallback to subcommands."""
    if ctx.invoked_subcommand is not None:
        return

    if message is not None:
        settings = get_settings()
        try:
            reply = asyncio.run(_run_agent_turn(message, settings.model_provider, session, settings))
            console.print(reply)
        except Exception as e:
            console.print(f"[bold red]Error running agent turn:[/bold red] {e}")
            raise typer.Exit(code=1) from e


@cli_app.command(name="turn")
def turn_cmd(
    message: str = typer.Argument(..., help="Prompt message to send to the agent"),
    provider: str = typer.Option(
        "fake",
        "--provider",
        "-p",
        help="Model provider to use (fake, ollama, openai, bedrock)",
    ),
    session: str = typer.Option(
        "cli-session",
        "--session",
        "-s",
        help="Session identifier for stateful turns",
    ),
) -> None:
    """Run a single chat turn with the agent and print the response."""
    settings = get_settings()
    try:
        reply = asyncio.run(_run_agent_turn(message, provider, session, settings))
        console.print(reply)
    except Exception as e:
        console.print(f"[bold red]Error running agent turn:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@cli_app.command(name="chat")
def chat_cmd(
    message: str | None = typer.Argument(
        None,
        help="Optional initial message; if omitted, runs interactively",
    ),
    provider: str = typer.Option(
        "fake",
        "--provider",
        "-p",
        help="Model provider to use (fake, ollama, openai, bedrock)",
    ),
    session: str = typer.Option(
        "cli-session",
        "--session",
        "-s",
        help="Session identifier",
    ),
) -> None:
    """Interactive or single chat command."""
    settings = get_settings()
    prompt = message or typer.prompt("You")
    try:
        reply = asyncio.run(_run_agent_turn(prompt, provider, session, settings))
        console.print(f"[bold green]YusufClaw:[/bold green] {reply}")
    except Exception as e:
        console.print(f"[bold red]Error running chat turn:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@cli_app.command(name="version")
def version_cmd() -> None:
    """Print YusufClaw version."""
    console.print(f"YusufClaw v{__version__}")


if __name__ == "__main__":
    cli_app()

__all__ = ["cli_app", "run_cli"]
