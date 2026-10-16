"""CLI gateway entry point for YusufClaw using Typer."""

import asyncio

import typer
from rich.console import Console

from yusufclaw import __version__
from yusufclaw.agent import Agent
from yusufclaw.config import Settings, get_settings
from yusufclaw.models import ModelProvider, get_model_provider
from yusufclaw.models.fake import FakeProvider

cli_app = typer.Typer(
    name="yusufclaw",
    help="YusufClaw — autonomous personal AI agent CLI",
    add_completion=False,
)
console = Console()


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
        # Override config provider choice if explicitly passed via CLI
        overridden = settings.model_copy(update={"model_provider": provider_name.lower()})
        provider = get_model_provider(overridden)

    agent = Agent(
        provider=provider,
        max_steps=settings.max_agent_steps,
    )
    return await agent.run(prompt, session_id=session_id)


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
        help="Optional initial message; if omitted, runs one prompt interactively",
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
