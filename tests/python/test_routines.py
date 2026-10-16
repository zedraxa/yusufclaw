"""Tests for routines: cron parser, JSON loader, queue-backed scheduler, and runner protocols."""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from yusufclaw.agent import Agent
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.models import FakeProvider
from yusufclaw.routines import (
    AbortController,
    ChannelRunnerOptions,
    Routine,
    RoutineMessage,
    RoutineScheduler,
    RoutineTarget,
    SingleFlightQueue,
    create_routine_scheduler,
    load_routines,
    load_routines_sync,
    parse_cron,
)

SAMPLE_ROUTINE = Routine(
    id="morning",
    cron="0 9 * * 1-5",
    prompt="Plan today",
    target=RoutineTarget(channel="cli", user_id="local", conversation_id="planning"),
)


def test_matches_utc_cron_lists_ranges_steps_sunday_alias_and_day_or_semantics() -> None:
    """Port of Vitest: matches UTC cron lists/ranges/steps, Sunday alias and day OR semantics."""
    # Friday 2026-10-16 09:30 UTC
    assert (
        parse_cron("*/15 9-17 * * 1-5")(datetime.fromisoformat("2026-10-16T09:30:00+00:00")) is True
    )
    # Friday 2026-10-16 09:15 UTC (0,30 does not include 15)
    assert parse_cron("0,30 9 * * *")(datetime.fromisoformat("2026-10-16T09:15:00+00:00")) is False
    # Sunday 2026-10-18 00:00 UTC (day 1 or Sunday 0)
    assert parse_cron("0 0 1 * 0")(datetime.fromisoformat("2026-10-18T00:00:00+00:00")) is True
    # Sunday alias 7
    assert parse_cron("0 0 * * 7")(datetime.fromisoformat("2026-10-18T00:00:00+00:00")) is True
    # Friday 2026-10-16 00:00 UTC (neither 1st of month nor Sunday)
    assert parse_cron("0 0 1 * 0")(datetime.fromisoformat("2026-10-16T00:00:00+00:00")) is False

    # Invalid crons must throw ValueError
    for bad_cron in [
        "* * * *",
        "60 * * * *",
        "*/0 * * * *",
        "* 22-2 * * *",
        "* * * 13 *",
        "* * * * 8",
        "@daily",
    ]:
        with pytest.raises(ValueError):
            parse_cron(bad_cron)


@pytest.mark.asyncio
async def test_validates_routine_files_without_exposing_prompts_or_config_contents() -> None:
    """Port of Vitest: validates routine files without exposing prompts or config contents."""
    with tempfile.TemporaryDirectory(prefix="yc-routines-") as tmpdir:
        file_path = Path(tmpdir) / "routines.json"

        # Valid single routine
        routine_dict = {
            "id": SAMPLE_ROUTINE.id,
            "cron": SAMPLE_ROUTINE.cron,
            "prompt": SAMPLE_ROUTINE.prompt,
            "target": {
                "channel": SAMPLE_ROUTINE.target.channel,
                "userId": SAMPLE_ROUTINE.target.user_id,
                "conversationId": SAMPLE_ROUTINE.target.conversation_id,
            },
        }
        file_path.write_text(json.dumps([routine_dict]), encoding="utf-8")

        loaded = await load_routines(str(file_path))
        assert len(loaded) == 1
        assert loaded[0] == SAMPLE_ROUTINE
        assert load_routines_sync(str(file_path)) == [SAMPLE_ROUTINE]

        # Invalid cases: must reject with strictly "Invalid routines configuration"
        invalid_payloads = [
            "SECRET",
            json.dumps([routine_dict, routine_dict]),  # Duplicate id
            json.dumps([{**routine_dict, "cron": "bad SECRET"}]),
            json.dumps([{**routine_dict, "target": {"channel": "email"}}]),
            json.dumps([{**routine_dict, "id": "invalid id!"}]),
            json.dumps([{**routine_dict, "prompt": "   "}]),
            json.dumps([{**routine_dict, "prompt": "x" * 16001}]),
            json.dumps([routine_dict for _ in range(101)]),  # Exceeds max 100
            json.dumps(
                [
                    {
                        **routine_dict,
                        "target": {"channel": "cli", "userId": "", "conversationId": "x"},
                    }
                ]
            ),
        ]

        for payload in invalid_payloads:
            file_path.write_text(payload, encoding="utf-8")
            with pytest.raises(ValueError) as exc_info:
                await load_routines(str(file_path))
            assert str(exc_info.value) == "Invalid routines configuration"
            assert "SECRET" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_dispatches_retries_delivery_and_persists_deduplication() -> None:
    """Port of Vitest: dispatches through transport, retries delivery with saved reply, and persists deduplication."""
    with tempfile.TemporaryDirectory(prefix="yc-routines-db-") as tmpdir:
        db_path = str(Path(tmpdir) / "memory.sqlite")
        memory = SqliteMemory(db_path)

        provider = FakeProvider(default_text="Plan ready")
        agent = Agent(provider=provider, memory=memory)

        deliver_mock = AsyncMock()
        deliver_mock.side_effect = [RuntimeError("failed"), None]

        async def mock_dispatch(message: RoutineMessage, signal: Any = None) -> str:
            return await agent.handle(
                session=message.conversation_id,
                request_id=message.request_id,
                text=message.text,
            )

        mock_transport = MagicMock()
        mock_transport.dispatch = mock_dispatch
        mock_transport.deliver = deliver_mock

        scheduler = RoutineScheduler(
            routines=[SAMPLE_ROUTINE],
            memory=memory,
            transport=mock_transport,
        )

        # 08:59:00Z: not due, provider not called
        before_due = datetime.fromisoformat("2026-10-16T08:59:00+00:00")
        await scheduler.tick(before_due)
        assert len(provider.recorded_requests) == 0

        # 09:00:00Z: due! First tick fails on deliver
        now = datetime.fromisoformat("2026-10-16T09:00:00+00:00")
        await scheduler.tick(now)
        assert len(provider.recorded_requests) == 1
        assert deliver_mock.call_count == 1

        # Second tick at 09:00:00Z: deliver retries and succeeds!
        # Because agent/memory cached the turn by requestId, provider is not called again!
        await scheduler.tick(now)
        assert len(provider.recorded_requests) == 1
        assert deliver_mock.call_count == 2
        assert deliver_mock.call_args[0][0] == SAMPLE_ROUTINE.target
        assert deliver_mock.call_args[0][1] == "Plan ready"

        # Third tick at 09:00:00Z: already recorded in memory state, so skipped
        await scheduler.tick(now)
        assert deliver_mock.call_count == 2
        assert len(provider.recorded_requests) == 1

        # Reopened memory database: persists deduplication
        reopened = SqliteMemory(db_path)
        try:
            reopened_scheduler = RoutineScheduler(
                routines=[SAMPLE_ROUTINE],
                memory=reopened,
                transport=mock_transport,
            )
            await reopened_scheduler.tick(now)
            assert deliver_mock.call_count == 2

            # Next Monday: 2026-10-19T09:00:00Z: due again, complete called second time
            next_monday = datetime.fromisoformat("2026-10-19T09:00:00+00:00")
            await scheduler.tick(next_monday)
            assert len(provider.recorded_requests) == 2
        finally:
            reopened.close()
            memory.close()


@pytest.mark.asyncio
async def test_prevents_overlapping_ticks_and_cancels_before_delivery() -> None:
    """Port of Vitest: prevents overlapping ticks and cancels before delivery."""
    dispatch_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    deliver_mock = AsyncMock()

    async def mock_dispatch(message: RoutineMessage, signal: Any = None) -> str:
        return await dispatch_future

    mock_transport = MagicMock()
    mock_transport.dispatch = mock_dispatch
    mock_transport.deliver = deliver_mock

    memory = SqliteMemory(":memory:")
    try:
        scheduler = RoutineScheduler(
            routines=[SAMPLE_ROUTINE],
            memory=memory,
            transport=mock_transport,
        )

        controller = AbortController()
        now = datetime.fromisoformat("2026-10-16T09:00:00+00:00")

        # Concurrent tick calls return the identical in-flight task
        first = scheduler.tick(now, controller.signal)
        second = scheduler.tick(now, controller.signal)
        assert first is second

        # Abort before dispatch resolution
        controller.abort()
        dispatch_future.set_result("result")

        # Waiting on the task must reject/cancel, and deliver must NOT have been called
        with pytest.raises(asyncio.CancelledError):
            await first

        assert deliver_mock.call_count == 0
    finally:
        memory.close()


@pytest.mark.asyncio
async def test_validates_configured_delivery_identities_and_delivers_channels() -> None:
    """Port of Vitest: validates configured delivery identities and delivers CLI/Telegram via shared core."""
    memory = SqliteMemory(":memory:")
    try:
        provider = FakeProvider(default_text="Scheduled reply")
        agent = Agent(provider=provider, memory=memory)

        output_mock = MagicMock()
        telegram_mock = MagicMock()
        telegram_mock.call = AsyncMock(return_value={})

        options = ChannelRunnerOptions(
            cli_user_id="local",
            telegram_allowed={"123"},
            telegram=telegram_mock,
            web_user_id="local",
            output=output_mock,
        )

        tg_routine = Routine(
            id="telegram",
            cron="0 9 * * 1-5",
            prompt="Plan today",
            target=RoutineTarget(channel="telegram", user_id="123", conversation_id="123"),
        )

        scheduler = create_routine_scheduler(
            routines=[SAMPLE_ROUTINE, tg_routine],
            memory=memory,
            bus=agent,
            options=options,
        )

        now = datetime.fromisoformat("2026-10-16T09:00:00+00:00")
        await scheduler.tick(now)

        output_mock.assert_called_with("[planning] Scheduled reply")
        telegram_mock.call.assert_called_with(
            "sendMessage",
            {"chat_id": "123", "text": "Scheduled reply"},
            None,
        )

        # Unauthorized targets
        unauthorized_targets = [
            RoutineTarget(channel="telegram", user_id="123", conversation_id="-999"),
            RoutineTarget(channel="web", user_id="local", conversation_id="default"),
            RoutineTarget(channel="cli", user_id="other", conversation_id="x"),
        ]

        for unauth_target in unauthorized_targets:
            bad_routine = Routine(
                id="unauth",
                cron="0 9 * * 1-5",
                prompt="Secret",
                target=unauth_target,
            )
            with pytest.raises(ValueError, match="unauthorized"):
                create_routine_scheduler(
                    routines=[bad_routine],
                    memory=memory,
                    bus=agent,
                    options=options,
                )
    finally:
        memory.close()


@pytest.mark.asyncio
async def test_stops_scheduler_loop_on_cancellation() -> None:
    """Port of Vitest: stops the scheduler loop on cancellation."""
    memory = SqliteMemory(":memory:")
    try:
        mock_transport = MagicMock()
        mock_transport.dispatch = AsyncMock(return_value="done")
        mock_transport.deliver = AsyncMock()

        scheduler = RoutineScheduler(
            routines=[],
            memory=memory,
            transport=mock_transport,
        )

        controller = AbortController()
        running_task = asyncio.create_task(scheduler.run(controller.signal))

        # Cancel immediately
        controller.abort()
        await running_task
    finally:
        memory.close()


@pytest.mark.asyncio
async def test_single_flight_queue_concurrency() -> None:
    """Verify SingleFlightQueue prevents concurrent execution of the same routine ID."""
    queue = SingleFlightQueue()
    execution_order: list[str] = []
    active_count = 0
    max_concurrent = 0

    async def slow_routine(label: str) -> str:
        nonlocal active_count, max_concurrent
        active_count += 1
        max_concurrent = max(max_concurrent, active_count)
        await asyncio.sleep(0.05)
        execution_order.append(label)
        active_count -= 1
        return label

    # Schedule two concurrent calls for the SAME routine_id
    t1 = asyncio.create_task(queue.run_single_flight("routine_1", lambda: slow_routine("first")))
    t2 = asyncio.create_task(queue.run_single_flight("routine_1", lambda: slow_routine("second")))

    res1, res2 = await asyncio.gather(t1, t2)
    assert res1 == "first"
    assert res2 == "second"
    # Under single flight for the same routine ID, they must run sequentially
    assert max_concurrent == 1
    assert execution_order == ["first", "second"]
