"""Runner interfaces, protocols, channel routing, and single-flight execution for routines."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from yusufclaw.routines.models import Routine, RoutineMessage, RoutineTarget

T = TypeVar("T")


class AbortSignal:
    """Abort signal matching standard web and TypeScript AbortSignal semantics."""

    def __init__(self) -> None:
        self._aborted = False

    @property
    def aborted(self) -> bool:
        """Return True if the signal has been aborted."""
        return self._aborted

    def throw_if_aborted(self) -> None:
        """Raise asyncio.CancelledError if aborted."""
        if self._aborted:
            raise asyncio.CancelledError("Operation aborted")

    def throwIfAborted(self) -> None:
        """CamelCase alias matching TypeScript."""
        self.throw_if_aborted()


class AbortController:
    """Controller to trigger cancellation on associated AbortSignal."""

    def __init__(self) -> None:
        self.signal = AbortSignal()

    def abort(self) -> None:
        """Trigger cancellation."""
        self.signal._aborted = True


def check_aborted(signal: Any = None) -> None:
    """Check if signal or event has been cancelled or aborted."""
    if signal is None:
        return

    if hasattr(signal, "throw_if_aborted") and callable(signal.throw_if_aborted):
        signal.throw_if_aborted()
    elif hasattr(signal, "throwIfAborted") and callable(signal.throwIfAborted):
        signal.throwIfAborted()
    elif getattr(signal, "is_set", None) and callable(signal.is_set) and signal.is_set():
        raise asyncio.CancelledError("Operation aborted")
    elif getattr(signal, "aborted", False) or getattr(signal, "is_cancelled", False):
        raise asyncio.CancelledError("Operation aborted")


def is_aborted(signal: Any = None) -> bool:
    """Return True if signal is aborted or cancelled."""
    if signal is None:
        return False
    if getattr(signal, "is_set", None) and callable(signal.is_set):
        return bool(signal.is_set())
    return bool(getattr(signal, "aborted", False) or getattr(signal, "is_cancelled", False))


@runtime_checkable
class RoutineTransport(Protocol):
    """Transport protocol matching TypeScript dispatch and deliver interface."""

    async def dispatch(self, message: RoutineMessage, signal: Any = None) -> str:
        """Dispatch prompt to agent/bus and return reply."""
        ...

    async def deliver(
        self,
        target: RoutineTarget,
        reply: str,
        request_id: str,
        signal: Any = None,
    ) -> None:
        """Deliver the reply to target channel (CLI, Telegram, Web)."""
        ...


@runtime_checkable
class RoutineRunner(Protocol):
    """Narrow runner interface that posts a prompt to target channel and returns reply."""

    async def run_routine(self, routine: Routine, request_id: str, signal: Any = None) -> str:
        """Execute routine prompt and deliver reply."""
        ...


class SingleFlightQueue:
    """Queue-backed single-flight coordinator for routine executions.

    Guarantees:
    - Single-flight: Two executions with the same routine ID never run concurrently.
    - Queue-backed serialization: Enqueued executions for the same ID run sequentially.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    def get_lock(self, routine_id: str) -> asyncio.Lock:
        """Retrieve or create a mutex for a given routine ID."""
        if routine_id not in self._locks:
            self._locks[routine_id] = asyncio.Lock()
        return self._locks[routine_id]

    def is_running(self, routine_id: str) -> bool:
        """Return True if an execution for routine_id is currently active."""
        task = self._active_tasks.get(routine_id)
        return task is not None and not task.done()

    async def run_single_flight(
        self,
        routine_id: str,
        coro_fn: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute the coroutine function with mutual exclusion for routine_id."""
        lock = self.get_lock(routine_id)
        async with lock:
            current_task = asyncio.current_task()
            if current_task is not None:
                self._active_tasks[routine_id] = current_task
            try:
                return await coro_fn()
            finally:
                if self._active_tasks.get(routine_id) is current_task:
                    self._active_tasks.pop(routine_id, None)


@dataclass
class ChannelRunnerOptions:
    """Configuration options for channel delivery."""

    cli_user_id: str = "local"
    telegram_allowed: set[str] = field(default_factory=set)
    telegram: Any = None
    web: Any = None
    web_user_id: str = "local"
    output: Callable[[str], None] | None = None


def split_telegram_text(text: str, chunk_size: int = 4096) -> list[str]:
    """Split message into chunks of at most chunk_size characters."""
    if not text:
        return [""]
    if len(text) <= chunk_size:
        return [text]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


class ChannelRunner:
    """Shared core runner that delivers CLI, Telegram, and Web messages."""

    def __init__(
        self,
        bus_or_agent: Any,
        options: ChannelRunnerOptions,
    ) -> None:
        self.bus_or_agent = bus_or_agent
        self.options = options

    def authorize(self, target: RoutineTarget | RoutineMessage) -> None:
        """Verify delivery identity against configured options."""
        user_id = target.user_id
        conv_id = target.conversation_id
        channel = target.channel

        if channel == "cli" and user_id == self.options.cli_user_id:
            return
        if (
            channel == "telegram"
            and self.options.telegram is not None
            and user_id in self.options.telegram_allowed
            and conv_id == user_id
        ):
            return
        if (
            channel == "web"
            and self.options.web is not None
            and user_id == self.options.web_user_id
        ):
            return

        raise ValueError("Routine target is unavailable or unauthorized")

    async def dispatch(self, message: RoutineMessage, signal: Any = None) -> str:
        """Dispatch prompt through agent or bus."""
        self.authorize(message)
        check_aborted(signal)

        # Check if bus.dispatch exists
        dispatch_fn = getattr(self.bus_or_agent, "dispatch", None)
        if dispatch_fn is not None:
            # Bus dispatch signature: (message, callback=None, signal=None)
            sig = inspect.signature(dispatch_fn)
            params_count = len(sig.parameters)
            if params_count >= 3:
                res = dispatch_fn(message, None, signal)
            elif params_count == 2:
                res = dispatch_fn(message, signal)
            else:
                res = dispatch_fn(message)
            if inspect.iscoroutine(res):
                return str(await res)
            return str(res)

        # Check if agent.handle exists
        handle_fn = getattr(self.bus_or_agent, "handle", None)
        if handle_fn is not None:
            res = handle_fn(
                message.conversation_id,
                message.request_id,
                message.text,
            )
            if inspect.iscoroutine(res):
                return str(await res)
            return str(res)

        # Check if callable directly
        if callable(self.bus_or_agent):
            res = self.bus_or_agent(message, signal)
            if inspect.iscoroutine(res):
                return str(await res)
            return str(res)

        raise RuntimeError("No compatible dispatch handler found on bus_or_agent")

    async def deliver(
        self,
        target: RoutineTarget,
        reply: str,
        request_id: str,
        signal: Any = None,
    ) -> None:
        """Deliver reply to authorized channel."""
        self.authorize(target)
        check_aborted(signal)

        if target.channel == "cli":
            out_fn = self.options.output or print
            out_fn(f"[{target.conversation_id}] {reply}")
        elif target.channel == "web":
            if self.options.web is None:
                raise ValueError("Web adapter not configured")
            web_deliver = getattr(self.options.web, "deliver", None)
            if web_deliver is not None:
                res = web_deliver(target, reply, request_id)
                if inspect.iscoroutine(res):
                    await res
        elif target.channel == "telegram":
            if self.options.telegram is None:
                raise ValueError("Telegram transport not configured")
            for chunk in split_telegram_text(reply):
                check_aborted(signal)
                call_fn = getattr(self.options.telegram, "call", None)
                if call_fn is not None:
                    payload = {"chat_id": target.conversation_id, "text": chunk}
                    res = call_fn("sendMessage", payload, signal)
                    if inspect.iscoroutine(res):
                        await res


__all__ = [
    "AbortController",
    "AbortSignal",
    "ChannelRunner",
    "ChannelRunnerOptions",
    "RoutineRunner",
    "RoutineTransport",
    "SingleFlightQueue",
    "check_aborted",
    "is_aborted",
    "split_telegram_text",
]
