# Python rootless sandbox

Shell commands and language interpreters invoked through `shell` use the sandbox runner after existing allowlist, path, and approval checks. There is no separate code tool in this tree. The runner never falls back to a host shell. Missing or unusable isolation returns exit code 126; timeouts return 124.

Defaults: network disabled, 512 MiB memory, one CPU core quota, 30 seconds wall time, 128 tasks, and 64 KiB retained per output stream. Limits must be positive. Output is drained incrementally with bounded retained buffers. Only explicit `SandboxConfig.env` values reach Bubblewrap payloads; containers receive image defaults plus explicit values, never the agent process environment. The runtime client receives only a small host control environment.

## Runtimes

Selection order is Bubblewrap, rootless Podman, then rootless Docker. A preferred runtime is tried first. Availability is cached per runner instance. No images are pulled automatically.

- **Bubblewrap:** requires a non-root user, unprivileged user namespaces, and a working systemd user scope. A real isolated `/bin/true` probe tests the launch path. The scope enforces `MemoryMax`, `CPUQuota`, `TasksMax`, and `RuntimeMaxSec`; namespace isolation alone is insufficient. System binaries are read-only, the root mount is read-only, `/tmp` is ephemeral, and only the workspace is writable. User/PID/IPC/UTS namespaces, dropped capabilities, a fresh session, and default network isolation are explicit.
- **Podman:** requires JSON info reporting rootless mode and cgroups v2. CPU/memory/PID limits are passed to the runtime; unsupported limits must fail execution.
- **Docker:** requires JSON info reporting rootless security mode, cgroups v2, and the systemd cgroup driver. An ordinary rootful daemon is rejected.
- **Containers:** read-only rootfs, temporary `/tmp`, no capabilities, no new privileges, explicit workspace bind, and no network by default. Preload a trusted image locally; the default is `alpine:latest`. Select an image containing Python when invoking Python (Alpine does not include it). For reproducible installations configure an image digest through `RootlessContainerRunner(image=...)`.

Timeout and cancellation kill the local process group. Container runs have unique names and receive forced removal during cleanup. Cleanup has a five-second deadline; daemon failure can prevent removal and needs operator reconciliation. The host and container runtime are trusted components.

## Mount policy

Workspace and optional read-only mounts are inspected before execution. Mounts containing `.ssh`, private key filenames, `.env*`, `.aws`, `.azure`, `.kube`, `.gnupg`, `gcloud`, credential filenames, SQLite files, or `agent-data` are rejected entirely. Read-only exposure still leaks secrets. Symlinks, hardlinks, special files (including sockets), reserved system paths, and mount-option delimiters are rejected. Keep a dedicated execution workspace without secrets; the default `.data/workspace` can coexist with sibling agent state.

This conservative policy can reject ordinary repositories or dependency trees containing symlinks or example `.env` files. It is not a secret-content scanner: credentials under arbitrary names are not detected. Host operators must keep mounted trees stable during validation and launch; concurrent host mutation is outside this boundary. System binary directories and installed images are trusted. Network access is an explicit configuration override, not automatically granted by a tool proposal.

## Audit and undo

Each dispatched run appends and fsyncs an intent and a correlated outcome to `.data/agent-data/sandbox-actions.jsonl`, outside mounted paths. Outcomes include backend, exit status, timeout, and duration. Intent includes resource/network limits and a command hash; raw commands, environment values, and output are not persisted because they can contain secrets. Denied mount requests have an outcome without dispatch intent. Audit write failure prevents dispatch. An intent without an outcome after a host crash means the effects are unknown; do not automatically replay it.

Arbitrary commands are explicitly **non-reversible**: no general rollback is claimed. Existing file actions with concrete undo hints retain their append-only compensation workflow. Sandbox mutations need application-specific backup/restore or a disposable worktree if rollback is required. The JSONL log is protected from the sandbox, but is not tamper-proof against the host owner and has no cross-process transaction with execution.

## Verification

Run `uv run pytest -q && uv run ruff check . && uv run mypy src`. Sandbox tests mock discovery and rootless info, inspect command construction, reject secret mounts and links, verify shell routing, and exercise audit/timeout handling using a test-only subprocess runner. The live Bubblewrap test skips when the full isolation and resource-control probe fails. These tests do not certify a live Podman/Docker installation.
