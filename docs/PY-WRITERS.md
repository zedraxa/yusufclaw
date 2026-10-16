# Python writer delegates (P2-01 / P2-02)

External coding CLIs (`agy` and `codex`) are invoked as bounded, isolated child jobs to perform repository edits and tasks on behalf of YusufClaw (YC-24). Writer delegates operate under strict filesystem, security, budget, and concurrency boundaries.

## Architecture and Protocol Probe

Coding CLIs run as child workers outside the main agent event loop. Before dispatching work, `CLIProtocolProbe` verifies the external binary's availability and protocol compatibility on the host system:

- Probes `--version` asynchronously with a 5-second timeout.
- Extracts semantic versioning or reported release identifiers (`agy 1.4.0`, `codex-cli 0.162.0-alpha.17.2`).
- Fails closed if the executable is missing, non-executable, returns a non-zero exit code, or times out.

## Isolated Git Worktree Workspaces

Writers never modify the parent repository or working tree directly. `WorkspaceManager` manages isolated git worktrees:

- **Location**: Worktrees are created under a configurable parent directory (`YUSUFCLAW_WRITERS_WORKSPACE_DIR`, defaulting to `/tmp/yusufclaw-worktrees`). Parent directories are created with secure permissions (`0o700`).
- **Path Confinement**: `validate_workspace_path` strictly asserts that the destination worktree resides within the configured parent directory and prevents path traversal (`../`) or escapes.
- **Detached by Default**: Unless an explicit branch name is provided in `WriterSpec`, worktrees use detached HEAD (`git worktree add --detach <path> <base_ref>`) to avoid polluting the host branch namespace.
- **Change Detection**: Once the child process completes, `get_changed_paths` inspects both working tree/index modifications (`git status --porcelain -uall`) and committed changes (`git diff --name-only <base_ref>`), returning relative file paths.
- **Teardown**: Automated cleanup (`cleanup_worktree`) runs `git worktree remove --force`, prunes lingering references (`git worktree prune`), and deletes temporary branches if created.

## Security Boundary and Environment Scrubbing

Child delegates inherit zero host secrets:

- `scrub_environment` strips all environment variables matching sensitive patterns:
  - AWS credentials and profile keys (`AWS_*`)
  - Model provider keys (`OPENAI_*`, `ANTHROPIC_*`, `GEMINI_*`, `MISTRAL_*`, `GROQ_*`)
  - Repository tokens (`GITHUB_*`, `GH_*`, `GITLAB_*`)
  - System credentials (`SSH_*`, `DATABASE_*`, `REDIS_*`, `PG*`, `POSTGRES_*`)
  - YusufClaw internal tokens (`YUSUFCLAW_*_KEY`, `YUSUFCLAW_WEB_AUTH_TOKEN`, `TELEGRAM_*`)
- Only an allowlist of standard safe system variables (`PATH`, `USER`, `LANG`, `TMPDIR`, `TERM`, `HOME`, `VIRTUAL_ENV`, etc.) is passed to the child process.
- Deterministic, non-privileged git committer and author identities are injected if absent (`YusufClaw Writer <writer@yusufclaw.local>`).
- Explicit `env_overrides` containing secret or credential tokens are rejected immediately with `SecurityViolationError`.
- `ChildBudget` caps wall-clock duration (`max_time_seconds`, default 120s), output retention (`max_output_bytes`, default 1MB), and modified file count (`max_changed_files`, default 100).

## Concurrency and Depth Bounds (P2-02)

Delegation is strictly bounded to prevent recursion and resource exhaustion:

- **Depth 1**: Writer delegates cannot spawn child subagents or recursive writer delegates (`spec.depth <= 1`). Any job with depth > 1 is rejected immediately.
- **At Most Two Concurrent Children**: `WriterDelegateRunner` enforces a concurrency gate via an `asyncio.Semaphore(2)`. When two children are actively running, additional delegate requests queue until a slot is released.

## Parent Verification Hook

A writer job is never reported as successful merely because the CLI process exited with 0. The parent must verify the resulting workspace:

- `WriterVerifier` runs an acceptance test suite directly inside the child worktree before reporting success.
- Defaults to running pytest (`[sys.executable, "-m", "pytest", "-q"]`) using the current virtual environment.
- Accepts custom test commands via `spec.test_command` (list or shell string).
- If the verification hook fails (exit code != 0) or times out, the job result is marked `success = False` and `test_passed = False`, with captured test output and error details retained for inspection.

## Execution Modes: Queue vs. Inline Subprocess

`WriterDelegateRunner` supports two interchangeable execution backends with identical contracts:

1. **Durable Queue (`YUSUFCLAW_QUEUE_ENABLED=1`)**:
   - Enqueues job with kind `"writer"`, role `"writer"`, and concurrency key `"writer_delegate"`.
   - Links to parent task via `parent_id=spec.parent_job_id` under the same user ID.
   - Worker claims the job with heartbeat fencing, executing the isolated worktree pipeline and verification.
2. **Inline Async Subprocess (Default)**:
   - Runs directly via `asyncio.create_subprocess_exec` inside an isolated process group (`start_new_session=True`).
   - Governed by the in-memory concurrency semaphore and timeout watcher.

## Conflict and Cancellation Handling

- **Workspace Conflicts**: Target collisions (pre-existing directory or checked-out branch) raise `WorkspaceConflictError` and populate `WriterResult.conflict = True`.
- **Cancellation**: Cancelling the task (`asyncio.CancelledError` or queue cancellation) delivers `SIGTERM` to the process group (`os.killpg`), cleans up the worktree, and flags `cancelled = True`.
- **Timeouts**: Process exceeding `ChildBudget.max_time_seconds` is killed via process group termination and reported with `timed_out = True`.

## Verification

Run:
```bash
uv run pytest -q && uv run ruff check . && uv run mypy src
```
Tests in `tests/python/test_writers.py` mock CLI execution via executable test stubs and isolated temporary git repositories, requiring zero live network access or live cloud credentials.
