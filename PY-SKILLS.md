# YusufClaw Python Port: Skills & Routines Subsystems

This document summarizes the Python port of the **Skills** and **Routines** subsystems in YusufClaw, ported from the reference TypeScript implementation at git tag `ts-final` (`packages/core/src/skills.ts`, `packages/core/src/routines.ts`, `packages/gateway/src/routines.ts`, and `tests/skills-routines.test.ts`).

---

## 1. Architectural Summary & Scope Boundaries

### Non-Interference Guarantee
- **Untouched Subsystems**: As instructed, `src/yusufclaw/gateway`, `src/yusufclaw/security`, `src/yusufclaw/tools`, `src/yusufclaw/memory`, and `packages/` (TypeScript) were **not modified**. Another worker owns the gateway subsystem.
- **Offline & Zero Cloud Dependency**: All unit and integration tests run 100% offline with zero external network, Redis, or AWS requirements.

---

## 2. Skills Subsystem (`src/yusufclaw/skills`)

The skills subsystem implements trusted, local markdown instruction loading with strict security boundaries matching the TypeScript implementation.

### Key Components

- **`src/yusufclaw/skills/loader.py`**:
  - `load_skills(directory: str | Path = "skills") -> str` (async)
  - `load_skills_sync(directory: str | Path = "skills") -> str` (sync)
  - `loadSkills`: CamelCase alias matching TypeScript.

### Security & Hardening Boundaries
- **Trusted Local Markdown Only**: Only files ending with `.md` (case-insensitive) are read. Scripts (`.js`, `.py`, `.sh`, `.ts`, etc.) are strictly ignored.
- **No Frontmatter or Script Execution**: Instructions are treated strictly as text data; no AST evaluation, template rendering, includes, or frontmatter code execution occurs.
- **Symlink Traversal Denied**: Both directories and files check `entry.is_symlink()` / `follow_symlinks=False`. Symlinks are skipped, preventing workspace jailbreaks or loop traps.
- **Ignored Files**:
  - Hidden files/directories (`.hidden.md`, `.git`, etc.) are skipped.
  - `README.md` (case-insensitive) is explicitly skipped as project documentation rather than an agent skill instruction.
- **Alphabetical Ordering**: Directories and files are sorted alphabetically before visiting to ensure deterministic instruction concatenation across different filesystems.
- **Strict Size and Depth Limits**:
  - `MAX_DEPTH = 8`: Nesting depth > 8 raises `ValueError("Skills directory nesting limit exceeded")`.
  - `MAX_FILE_BYTES = 32_768` (32 KB): Any single file exceeding 32 KB raises `ValueError("Skill instructions exceed size limit")`.
  - `MAX_TOTAL_BYTES = 131_072` (128 KB): Cumulative size of all instruction files exceeding 128 KB raises `ValueError("Skill instructions exceed size limit")`.
- **Directory Path Validation**:
  - If the directory does not exist, returns `""` cleanly.
  - If the path is a file or non-directory, raises `ValueError("Skills path must be a directory")`.

### Prompt Injection Shape
The returned prompt injection string strictly matches TypeScript:
```text
Trusted local skill instructions:

### Skill: a.md
First instruction

### Skill: nested/SKILL.md
Nested instruction
```

### Agent Integration
`Agent.__init__` in `src/yusufclaw/agent.py` accepts `skills: str | None = None` and injects it alongside the system prompt and memory context during the LangGraph model node execution:
```python
system_parts = [self.system_prompt]
if self.skills:
    system_parts.append(self.skills)
if mem_ctx:
    system_parts.append(mem_ctx)
system = "\n\n".join(system_parts)
```

---

## 3. Routines Subsystem (`src/yusufclaw/routines`)

The routines subsystem implements proactive, scheduled autonomous tasks with cron evaluation, state deduplication, cancellation, channel routing, and queue-backed single-flight execution.

### Key Components

- **`src/yusufclaw/routines/cron.py`**:
  - `parse_cron(expression: str) -> Callable[[datetime], bool]` (and `parseCron`).
  - Standard 5-field UTC cron parser:
    1. Minute (`0-59`)
    2. Hour (`0-23`)
    3. Day of month (`1-31`)
    4. Month (`1-12`)
    5. Day of week (`0-7`, where both `0` and `7` represent Sunday)
  - Supports `*`, lists (`0,15,30`), ranges (`9-17`, `1-5`), and step intervals (`*/15`, `1-5/2`).
  - Standard cron day semantics: restricted day-of-month and day-of-week fields are ORed; if either field starts with `*`, they are ANDed.
  - Invalid field counts, out-of-bound ranges, inverted ranges (`22-2`), or invalid steps (`*/0`) raise `ValueError`.

- **`src/yusufclaw/routines/models.py`**:
  - `RoutineTarget`: frozen dataclass holding `channel` (`'cli' | 'telegram' | 'web'`), `user_id` (`userId`), and `conversation_id` (`conversationId`).
  - `Routine`: holds `id`, `cron`, `prompt`, and `target`. Provides deterministic `dedup_key`:
    `routine:{id}:{"channel":"...","userId":"...","conversationId":"..."}`
  - `RoutineMessage`: immutable payload dispatched to agents or message bus.

- **`src/yusufclaw/routines/loader.py`**:
  - `load_routines(path: str | Path) -> list[Routine]` (and `loadRoutines`, `load_routines_sync`).
  - Strict validation:
    - JSON array up to 100 routines.
    - Routine ID matches `^[a-zA-Z0-9_-]{1,64}$` and is unique.
    - Valid cron expression parsed with `parse_cron`.
    - Non-empty prompt <= 16,000 characters.
    - Authorized target channels (`cli`, `telegram`, `web`) with non-empty user and conversation IDs.
    - **Safe error handling**: Any failure strictly raises `ValueError("Invalid routines configuration")` without leaking configuration contents or sensitive strings (e.g., `"SECRET"`).

- **`src/yusufclaw/routines/runner.py`**:
  - Narrow protocols:
    - `RoutineTransport(Protocol)`: `dispatch(message, signal) -> str` and `deliver(target, reply, request_id, signal) -> None`.
    - `RoutineRunner(Protocol)`: `run_routine(routine, request_id, signal) -> str`.
  - `ChannelRunner` & `ChannelRunnerOptions`:
    - Validates target channel authorization:
      - `cli`: `user_id == cli_user_id`
      - `telegram`: `user_id in telegram_allowed` and `conversation_id == user_id`
      - `web`: `user_id == web_user_id`
    - Delivers replies to target channel without owning gateway code:
      - `cli`: formats `[{conversation_id}] {reply}` via `options.output` (or `print`).
      - `web`: invokes `options.web.deliver(target, reply, request_id)`.
      - `telegram`: chunks messages > 4096 characters and calls `options.telegram.call("sendMessage", ...)`.
  - Cancellation primitives:
    - `AbortSignal` and `AbortController` matching TypeScript / web standards.
    - `check_aborted(signal)` and `is_aborted(signal)` supporting `AbortSignal`, `asyncio.Event`, and `asyncio.CancelledError`.

- **`src/yusufclaw/routines/scheduler.py`**:
  - `RoutineScheduler`:
    - `tick(now: datetime | None = None, signal: Any = None) -> asyncio.Task[None]`:
      - **Scheduler-level single-flight**: If a tick is currently running, subsequent concurrent calls return the identical in-flight task (`assert first is second`).
      - Minute timestamp: `int(now.timestamp() // 60)` matching Unix epoch minutes in TS.
      - Deduplication check: checks memory state (`get_state(key) >= minute`). If already completed, skips.
      - Delivery retry: if delivery fails, memory state is not updated, allowing retry within the same minute using the cached model reply.
      - Success recording: writes `str(minute)` to memory state upon successful delivery.
    - `run(signal: Any = None) -> None`:
      - Continuously ticks every second until the signal is aborted.
      - Cancellation stops the loop cleanly without error.
  - `create_routine_scheduler(...)` (and `createRoutineScheduler`):
    - Factory function wiring `RoutineScheduler` with `ChannelRunner` and validating target authorizations at startup.

- **Queue-Backed Single-Flight (`SingleFlightQueue`)**:
  - `SingleFlightQueue`:
    - Guarantees that two executions with the **same routine ID cannot run concurrently**.
    - If a routine execution is in-flight, subsequent runs for that same routine ID acquire the mutex and run sequentially without concurrent collision.
    - Works with zero external infrastructure (in-memory asyncio locks) and easily bridges with RedisQueue if distributed queueing is configured.

---

## 4. Test Suite and Parity Report

Every test scenario from `tests/skills-routines.test.ts` was ported to pytest under `tests/python/test_skills.py` and `tests/python/test_routines.py`:

| TypeScript Vitest Test Case | Pytest Implementation | Status |
| :--- | :--- | :---: |
| `loads only sorted trusted Markdown and injects it alongside the system prompt` | `tests/python/test_skills.py::test_loads_only_sorted_trusted_markdown_and_injects_prompt` | PASSED |
| Cumulative size limit (131 KB) & nesting depth limit (8 levels) | `tests/python/test_skills.py::test_cumulative_size_limit`, `test_nesting_depth_limit` | PASSED |
| Non-directory path validation | `tests/python/test_skills.py::test_non_directory_path` | PASSED |
| `validates routine files without exposing prompts or config contents` | `tests/python/test_routines.py::test_validates_routine_files_without_exposing_prompts_or_config_contents` | PASSED |
| `matches UTC cron lists/ranges/steps, Sunday alias and day OR semantics` | `tests/python/test_routines.py::test_matches_utc_cron_lists_ranges_steps_sunday_alias_and_day_or_semantics` | PASSED |
| `dispatches through the bus, retries delivery with saved model reply, and persists deduplication` | `tests/python/test_routines.py::test_dispatches_retries_delivery_and_persists_deduplication` | PASSED |
| `prevents overlapping ticks and cancels before delivery` | `tests/python/test_routines.py::test_prevents_overlapping_ticks_and_cancels_before_delivery` | PASSED |
| `validates configured delivery identities and delivers CLI/Telegram via shared core` | `tests/python/test_routines.py::test_validates_configured_delivery_identities_and_delivers_channels` | PASSED |
| `stops the scheduler loop on cancellation` | `tests/python/test_routines.py::test_stops_scheduler_loop_on_cancellation` | PASSED |
| Queue-backed single-flight concurrency lock | `tests/python/test_routines.py::test_single_flight_queue_concurrency` | PASSED |

### Verification Commands
```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

All 71 workspace tests pass offline in ~6 seconds.

---

## 5. Identified Gaps & Integration Hand-Off

1. **Gateway Integration**:
   - The gateway worker can directly import `create_routine_scheduler` and `ChannelRunnerOptions` from `yusufclaw.routines`, passing their `MessageBus` instance, `TelegramTransport`, and `WebAdapter`.
2. **Distributed Redis Locking (Optional)**:
   - For multi-instance deployments where multiple scheduler workers run across different nodes, `SingleFlightQueue` can optionally be backed by Redis distributed locks (`SET key NX EX`). In the current single-process architecture, in-memory mutexes provide strict mutual exclusion with zero infrastructure overhead.
