"""Unit tests for YusufClaw guarded tools (files, shell, fetch)."""

import os
import tempfile

import httpx
import pytest

from yusufclaw.tools import (
    contains_restricted_path,
    create_default_tools,
    create_file_list_tool,
    create_file_read_tool,
    create_file_write_tool,
    create_shell_tool,
    create_web_fetch_tool,
    extract_command_binaries,
    is_blocked_hostname,
    is_blocked_ip,
    is_dangerous_command,
    is_restricted_secret_path,
    resolve_safe_path,
    validate_host_ssrf,
)


def test_restricted_secret_path_detection() -> None:
    """Verify is_restricted_secret_path identifies sensitive files."""
    # Restricted
    assert is_restricted_secret_path(".env")
    assert is_restricted_secret_path(".env.local")
    assert is_restricted_secret_path(".env.production")
    assert is_restricted_secret_path("config/.env")
    assert is_restricted_secret_path("memory.sqlite")
    assert is_restricted_secret_path("data.sqlite3")
    assert is_restricted_secret_path("db.sqlite-wal")
    assert is_restricted_secret_path("~/.aws/credentials")
    assert is_restricted_secret_path(".aws/config")
    assert is_restricted_secret_path(".git/config")
    assert is_restricted_secret_path("id_rsa")
    assert is_restricted_secret_path("id_ed25519")

    # Safe
    assert not is_restricted_secret_path("notes.txt")
    assert not is_restricted_secret_path("src/main.py")
    assert not is_restricted_secret_path("package.json")
    assert not is_restricted_secret_path("environment.md")


def test_resolve_safe_path_confinement_and_rejections() -> None:
    """Verify resolve_safe_path confines paths and prevents traversals."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = os.path.join(tmpdir, "workspace")
        os.makedirs(ws, exist_ok=True)

        # Safe inside workspace
        safe = resolve_safe_path(ws, "sub/file.txt")
        assert safe.startswith(ws)

        # Null byte injection
        with pytest.raises(ValueError, match="null bytes"):
            resolve_safe_path(ws, "file.txt\0.jpg")

        # Empty string
        with pytest.raises(ValueError, match="non-empty string"):
            resolve_safe_path(ws, "   ")

        # Path traversal out of workspace
        with pytest.raises(PermissionError, match="outside workspace root"):
            resolve_safe_path(ws, "../outside.txt")

        # Direct access to .env
        with pytest.raises(PermissionError, match="restricted sensitive path"):
            resolve_safe_path(ws, ".env")


@pytest.mark.asyncio
async def test_file_tools_execution_and_protection() -> None:
    """Verify file_read and file_write operations and safety boundaries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = os.path.join(tmpdir, "workspace")
        os.makedirs(ws, exist_ok=True)

        read_tool = create_file_read_tool(workspace_root=ws)
        write_tool = create_file_write_tool(workspace_root=ws)
        list_tool = create_file_list_tool(workspace_root=ws)

        # file_write requires approval
        assert write_tool.check_approval({"path": "test.txt", "content": "hello"}) is True
        assert read_tool.check_approval({"path": "test.txt"}) is False

        # Write file
        write_res = await write_tool.execute({"path": "test.txt", "content": "secret content"})
        assert "successfully written" in write_res

        # Read file
        read_res = await read_tool.execute({"path": "test.txt"})
        assert read_res == "secret content"

        # List files
        list_res = await list_tool.execute({"path": "."})
        assert "test.txt" in list_res

        # Attempt to read restricted path .env
        with pytest.raises(PermissionError, match="restricted sensitive path"):
            await read_tool.execute({"path": ".env"})


def test_shell_command_parsing_and_danger_checks() -> None:
    """Verify shell binary extraction, dangerous command classification, and restricted paths."""
    assert extract_command_binaries("ls -la | grep test ; echo done") == ["ls", "grep", "echo"]
    assert extract_command_binaries("FOO=bar cat file.txt") == ["cat"]

    # Safe commands
    assert not is_dangerous_command("ls")
    assert not is_dangerous_command("pwd")
    assert not is_dangerous_command("echo hello")
    assert not is_dangerous_command("git status")
    assert not is_dangerous_command("node -v")

    # Dangerous commands
    assert is_dangerous_command("rm -rf file.txt")
    assert is_dangerous_command("echo hello > out.txt")
    assert is_dangerous_command("git commit -m 'test'")
    assert is_dangerous_command("$(whoami)")
    assert is_dangerous_command("python script.py")

    # Restricted path checks
    assert contains_restricted_path("cat ../.env")
    assert contains_restricted_path("cat .env")
    assert contains_restricted_path("grep key ~/.aws/credentials")
    assert contains_restricted_path("sqlite3 memory.sqlite .dump")
    assert is_dangerous_command("cat ../.env")


@pytest.mark.asyncio
async def test_shell_execution_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify shell tool blocks restricted paths and non-allowlisted commands."""
    with tempfile.TemporaryDirectory() as tmpdir:
        ws = os.path.join(tmpdir, "workspace")
        os.makedirs(ws, exist_ok=True)

        tool = create_shell_tool(workspace_dir=ws)

        from unittest.mock import AsyncMock

        from yusufclaw.sandbox.models import SandboxResult

        runner = AsyncMock()
        runner.run_async.return_value = SandboxResult(
            exit_code=0, stdout="safe output", stderr="", duration_seconds=0, backend="mock",
        )
        monkeypatch.setattr("yusufclaw.tools.shell.get_sandbox_runner", lambda: runner)
        # Safe execution through the sandbox contract
        out = await tool.execute({"command": "echo 'safe output'"})
        assert "safe output" in out

        # Path traversal / .env denial
        with pytest.raises(PermissionError, match="restricted sensitive file or path traversal"):
            await tool.execute({"command": "cat ../.env"})

        # Allowlist violation
        with pytest.raises(PermissionError, match="not allowed by allowlist"):
            await tool.execute({"command": "nmap 127.0.0.1"})


def test_ssrf_host_and_ip_validation() -> None:
    """Verify IP and hostname blocking for SSRF defense."""
    assert is_blocked_hostname("169.254.169.254")
    assert is_blocked_hostname("metadata.google.internal")
    assert is_blocked_hostname("localhost")
    assert is_blocked_hostname("database.internal")
    assert not is_blocked_hostname("example.com")

    # Blocked IPs
    assert is_blocked_ip("127.0.0.1")
    assert is_blocked_ip("10.0.0.1")
    assert is_blocked_ip("172.16.0.1")
    assert is_blocked_ip("192.168.1.1")
    assert is_blocked_ip("169.254.169.254")
    assert is_blocked_ip("100.64.0.1")
    assert is_blocked_ip("::1")
    assert is_blocked_ip("::ffff:127.0.0.1")

    # Safe IPs
    assert not is_blocked_ip("93.184.216.34")


@pytest.mark.asyncio
async def test_ssrf_validate_host_and_redirects() -> None:
    """Verify validate_host_ssrf blocks private IPs and redirect chains to internal services."""
    async def mock_internal_dns(host: str) -> list[str]:
        return ["127.0.0.1"]

    async def mock_public_dns(host: str) -> list[str]:
        return ["93.184.216.34"]

    # Blocked hostname
    with pytest.raises(PermissionError, match="SSRF protection"):
        await validate_host_ssrf("169.254.169.254")

    # Blocked direct IP
    with pytest.raises(PermissionError, match="SSRF protection"):
        await validate_host_ssrf("127.0.0.1")

    # Blocked resolved IP
    with pytest.raises(PermissionError, match="SSRF protection"):
        await validate_host_ssrf("ollama.internal-service.test", dns_lookup=mock_internal_dns)

    # Allowed public resolution
    await validate_host_ssrf("example.com", dns_lookup=mock_public_dns)


@pytest.mark.asyncio
async def test_web_fetch_mocked_redirect_chain_ssrf() -> None:
    """Verify web_fetch stops when redirect targets an internal service."""
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.url == "http://external-site.com/jump":
            return httpx.Response(
                status_code=302,
                headers={"Location": "http://127.0.0.1:11434/api/tags"},
                content=b"Redirecting",
            )
        return httpx.Response(status_code=200, content=b"OK")

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def mock_dns(host: str) -> list[str]:
        if host == "external-site.com":
            return ["93.184.216.34"]
        return ["127.0.0.1"]

    fetch_tool = create_web_fetch_tool(client=mock_client, dns_lookup=mock_dns)

    # Redirect to 127.0.0.1 must be caught and blocked
    with pytest.raises(PermissionError, match="SSRF protection"):
        await fetch_tool.execute({"url": "http://external-site.com/jump"})


def test_create_default_tools() -> None:
    """Verify create_default_tools returns expected guarded tools."""
    tools = create_default_tools()
    names = {t.name for t in tools}
    assert "current_time" in names
    assert "shell" in names
    assert "file_read" in names
    assert "file_write" in names
    assert "file_list" in names
    assert "web_fetch" in names
