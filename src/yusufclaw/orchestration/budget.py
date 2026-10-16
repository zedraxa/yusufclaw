"""Budget manager for tracking resource limits and enforcing graceful stops.

Monitors:
- Token consumption (prompt, completion, total)
- Dollar cost computed against configurable pricing
- Step counts (model loops, tool executions, subagent calls)
- Elapsed wall-clock time

Maintains independent tracking for per-turn and per-session scopes with
graceful degradation when ceilings are reached.
"""

from __future__ import annotations

import logging
import time

from yusufclaw.orchestration.models import (
    BudgetExceededError,
    BudgetLimits,
    BudgetStatus,
    BudgetUsage,
    TokenPricing,
)
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)


class BudgetManager:
    """Tracks token, cost, step, and time consumption against configurable limits."""

    def __init__(
        self,
        limits: BudgetLimits | None = None,
        pricing: TokenPricing | None = None,
    ) -> None:
        self.limits = limits or BudgetLimits()
        self.pricing = pricing or TokenPricing()
        self.turn_usage = BudgetUsage()
        self.session_usage = BudgetUsage()
        self._turn_start_time: float = time.monotonic()
        self._session_start_time: float = time.monotonic()

    def start_turn(self) -> None:
        """Initialize or reset budget tracking for a new conversational turn."""
        self.turn_usage = BudgetUsage()
        self._turn_start_time = time.monotonic()

    def get_turn_elapsed_seconds(self) -> float:
        """Return the elapsed wall-clock seconds for the current turn."""
        return max(0.0, time.monotonic() - self._turn_start_time)

    def get_session_elapsed_seconds(self) -> float:
        """Return cumulative elapsed seconds for the active session."""
        return max(0.0, time.monotonic() - self._session_start_time)

    def record_tokens(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model_name: str | None = None,
    ) -> float:
        """Record token consumption and compute financial cost.

        Returns:
            The incremental cost in USD for this call.
        """
        prompt = max(0, prompt_tokens)
        completion = max(0, completion_tokens)
        total = prompt + completion

        incremental_cost = (
            (prompt / 1_000_000.0) * self.pricing.prompt_cost_per_1m
            + (completion / 1_000_000.0) * self.pricing.completion_cost_per_1m
        )

        # Update turn usage
        self.turn_usage.prompt_tokens += prompt
        self.turn_usage.completion_tokens += completion
        self.turn_usage.total_tokens += total
        self.turn_usage.cost += incremental_cost

        # Update session usage
        self.session_usage.prompt_tokens += prompt
        self.session_usage.completion_tokens += completion
        self.session_usage.total_tokens += total
        self.session_usage.cost += incremental_cost

        return incremental_cost

    def record_step(self, count: int = 1) -> None:
        """Increment model/tool execution step count."""
        steps = max(0, count)
        self.turn_usage.steps += steps
        self.session_usage.steps += steps

    def end_turn(self) -> BudgetUsage:
        """Finalize the turn, updating timing, and return turn usage summary."""
        turn_elapsed = self.get_turn_elapsed_seconds()
        self.turn_usage.elapsed_seconds = turn_elapsed
        self.session_usage.elapsed_seconds = self.get_session_elapsed_seconds()
        return self.turn_usage

    def check_limits(self) -> BudgetStatus:
        """Assess current consumption against configured limits.

        Returns:
            BudgetStatus detailing whether limits are exceeded and remaining allowances.
        """
        turn_time = self.get_turn_elapsed_seconds()
        session_time = self.get_session_elapsed_seconds()

        rem_tokens_turn = max(0, self.limits.max_tokens_per_turn - self.turn_usage.total_tokens)
        rem_cost_turn = max(0.0, self.limits.max_cost_per_turn - self.turn_usage.cost)
        rem_steps_turn = max(0, self.limits.max_steps_per_turn - self.turn_usage.steps)
        rem_time_turn = max(0.0, self.limits.max_time_seconds_per_turn - turn_time)

        rem_tokens_session = max(
            0, self.limits.max_tokens_per_session - self.session_usage.total_tokens
        )
        rem_cost_session = max(0.0, self.limits.max_cost_per_session - self.session_usage.cost)
        rem_steps_session = max(0, self.limits.max_steps_per_session - self.session_usage.steps)
        rem_time_session = max(0.0, self.limits.max_time_seconds_per_session - session_time)

        is_exceeded = False
        reason: str | None = None

        # Turn level limit violations
        if self.turn_usage.total_tokens >= self.limits.max_tokens_per_turn:
            is_exceeded = True
            reason = (
                f"Turn token limit exceeded: used {self.turn_usage.total_tokens} "
                f"(max {self.limits.max_tokens_per_turn})"
            )
        elif self.turn_usage.cost >= self.limits.max_cost_per_turn:
            is_exceeded = True
            reason = (
                f"Turn cost limit exceeded: spent ${self.turn_usage.cost:.4f} "
                f"(max ${self.limits.max_cost_per_turn:.4f})"
            )
        elif self.turn_usage.steps >= self.limits.max_steps_per_turn:
            is_exceeded = True
            reason = (
                f"Turn step limit exceeded: took {self.turn_usage.steps} "
                f"(max {self.limits.max_steps_per_turn})"
            )
        elif turn_time >= self.limits.max_time_seconds_per_turn:
            is_exceeded = True
            reason = (
                f"Turn execution time exceeded: elapsed {turn_time:.1f}s "
                f"(max {self.limits.max_time_seconds_per_turn:.1f}s)"
            )
        # Session level limit violations
        elif self.session_usage.total_tokens >= self.limits.max_tokens_per_session:
            is_exceeded = True
            reason = (
                f"Session token limit exceeded: used {self.session_usage.total_tokens} "
                f"(max {self.limits.max_tokens_per_session})"
            )
        elif self.session_usage.cost >= self.limits.max_cost_per_session:
            is_exceeded = True
            reason = (
                f"Session cost limit exceeded: spent ${self.session_usage.cost:.4f} "
                f"(max ${self.limits.max_cost_per_session:.4f})"
            )
        elif self.session_usage.steps >= self.limits.max_steps_per_session:
            is_exceeded = True
            reason = (
                f"Session step limit exceeded: took {self.session_usage.steps} "
                f"(max {self.limits.max_steps_per_session})"
            )
        elif session_time >= self.limits.max_time_seconds_per_session:
            is_exceeded = True
            reason = (
                f"Session execution time exceeded: elapsed {session_time:.1f}s "
                f"(max {self.limits.max_time_seconds_per_session:.1f}s)"
            )

        status = BudgetStatus(
            is_exceeded=is_exceeded,
            exceeded_reason=reason,
            remaining_tokens_turn=rem_tokens_turn,
            remaining_cost_turn=rem_cost_turn,
            remaining_steps_turn=rem_steps_turn,
            remaining_time_turn=rem_time_turn,
            remaining_tokens_session=rem_tokens_session,
            remaining_cost_session=rem_cost_session,
            remaining_steps_session=rem_steps_session,
            remaining_time_session=rem_time_session,
        )

        tracer = get_tracer("yusufclaw.orchestration")
        with tracer.start_as_current_span("yusufclaw.orchestration.budget_check") as span:
            if span.is_recording():
                span.set_attribute("yusufclaw.budget.is_exceeded", is_exceeded)
                span.set_attribute("yusufclaw.budget.turn_tokens", self.turn_usage.total_tokens)
                span.set_attribute("yusufclaw.budget.turn_cost", self.turn_usage.cost)
                span.set_attribute("yusufclaw.budget.turn_steps", self.turn_usage.steps)
                span.set_attribute("yusufclaw.budget.session_tokens", self.session_usage.total_tokens)
                span.set_attribute("yusufclaw.budget.session_cost", self.session_usage.cost)
                span.set_attribute("yusufclaw.budget.session_steps", self.session_usage.steps)
                if reason:
                    span.set_attribute("yusufclaw.budget.exceeded_reason", reason)

        return status

    def raise_if_exceeded(self) -> None:
        """Check limits and raise BudgetExceededError if exhausted."""
        status = self.check_limits()
        if status.is_exceeded and status.exceeded_reason:
            raise BudgetExceededError(
                limit_name="BudgetExceeded",
                current_value=self.turn_usage.total_tokens,
                limit_value=self.limits.max_tokens_per_turn,
                scope=status.exceeded_reason,
            )

    def get_graceful_stop_message(self, reason: str | None = None) -> str:
        """Generate user-facing graceful degradation message when budget is exhausted."""
        expl = reason or "Allocated resource budget reached."
        return (
            f"Operation halted gracefully: {expl}\n"
            f"Summary: Tokens={self.turn_usage.total_tokens}, "
            f"Cost=${self.turn_usage.cost:.4f}, Steps={self.turn_usage.steps}. "
            "Partial progress and session state have been preserved."
        )

    def reset_session(self) -> None:
        """Reset all session and turn counters."""
        self.turn_usage = BudgetUsage()
        self.session_usage = BudgetUsage()
        self._turn_start_time = time.monotonic()
        self._session_start_time = time.monotonic()


__all__ = ["BudgetManager"]
