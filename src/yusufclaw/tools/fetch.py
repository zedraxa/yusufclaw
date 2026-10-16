"""HTTP web fetch tool with strict SSRF defenses and redirect validation.

Implements YC-02 defense-in-depth:
- DNS resolution checks blocking private, loopback, link-local, CGNAT, and multicast IPs.
- Explicit blocking of cloud metadata endpoints (169.254.169.254, fd00:ec2::254, etc.).
- Manual HTTP redirect inspection verifying every hop against SSRF policies.
- Strict payload size and timeout limits.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from yusufclaw.security.models import ToolManifest
from yusufclaw.tools.manifest import GuardedTool

BLOCKED_HOST_EXACT = {
    "169.254.169.254",
    "fd00:ec2::254",
    "metadata.google.internal",
    "100.100.100.200",
    "localhost",
}

_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

DnsLookup = Callable[[str], Awaitable[list[str]]]


def is_blocked_hostname(hostname: str) -> bool:
    """Check whether a hostname matches cloud metadata or internal domains."""
    if not hostname or not isinstance(hostname, str):
        return True

    lower = hostname.lower().strip()
    if lower in BLOCKED_HOST_EXACT:
        return True

    if lower.endswith(".internal") or lower.endswith(".local") or lower.endswith(".localhost"):
        return True

    return False


def is_blocked_ip(ip_str: str) -> bool:
    """Check whether an IP address belongs to loopback, private, link-local, or reserved ranges."""
    if not ip_str or not isinstance(ip_str, str):
        return True

    clean = ip_str.strip().lower()
    if clean.startswith("[") and clean.endswith("]"):
        clean = clean[1:-1]

    # Handle IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1)
    if clean.startswith("::ffff:"):
        clean = clean[7:]

    try:
        ip = ipaddress.ip_address(clean)
    except ValueError:
        return True

    if not ip.is_global:
        return True

    if ip.version == 4 and ip in _CGNAT_NET:
        return True

    return False


async def validate_host_ssrf(
    hostname: str,
    dns_lookup: DnsLookup | None = None,
) -> None:
    """Validate that a target hostname does not resolve to private or internal addresses."""
    lower = hostname.lower().strip()
    clean_host = lower[1:-1] if lower.startswith("[") and lower.endswith("]") else lower

    if is_blocked_hostname(clean_host):
        raise PermissionError(f"Access to host {hostname} is blocked for security (SSRF protection)")

    # If the hostname is already an IP address
    try:
        ipaddress.ip_address(clean_host)
        if is_blocked_ip(clean_host):
            raise PermissionError(
                f"Access to private, loopback, or link-local IP address {hostname} is blocked for security (SSRF protection)"
            )
        return
    except ValueError:
        pass

    # Resolve hostname via DNS
    resolved_ips: list[str] = []
    if dns_lookup is not None:
        resolved_ips = await dns_lookup(clean_host)
    else:
        loop = asyncio.get_running_loop()
        try:
            addr_info = await loop.getaddrinfo(
                clean_host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
            for item in addr_info:
                sockaddr = item[4]
                ip_addr = sockaddr[0]
                if isinstance(ip_addr, str):
                    resolved_ips.append(ip_addr)
        except Exception as e:
            raise PermissionError(f"DNS lookup failed for host: {hostname} ({e})") from e

    if not resolved_ips:
        raise PermissionError(f"DNS lookup failed for host: {hostname}")

    for ip in resolved_ips:
        if is_blocked_ip(ip):
            raise PermissionError(
                f"Access to host {hostname} resolving to private/loopback/link-local address {ip} is blocked for security (SSRF protection)"
            )


def create_web_fetch_tool(
    dns_lookup: DnsLookup | None = None,
    client: httpx.AsyncClient | None = None,
    default_timeout_s: float = 15.0,
    max_timeout_s: float = 30.0,
    default_max_bytes: int = 100_000,
    max_bytes_limit: int = 1_000_000,
) -> GuardedTool:
    """Create guarded web_fetch tool with redirect validation and size limits."""
    manifest = ToolManifest(
        id="web_fetch",
        version="1.0.0",
        source="builtin",
        effects=["read_untrusted", "egress"],
    )

    async def execute(input_args: dict[str, Any]) -> str:
        raw_url = str(input_args.get("url", "")).strip()
        if not raw_url:
            raise ValueError("URL must be a non-empty string")

        method = str(input_args.get("method", "GET")).upper()
        if method not in ("GET", "HEAD"):
            method = "GET"

        req_timeout = float(input_args.get("timeoutMs", default_timeout_s * 1000)) / 1000.0
        effective_timeout = min(max(0.5, req_timeout), max_timeout_s)

        req_max_bytes = int(input_args.get("maxBytes", default_max_bytes))
        effective_max_bytes = min(max(1, req_max_bytes), max_bytes_limit)

        headers: dict[str, str] = {
            "User-Agent": "YusufClaw/0.1.0 (Personal AI Agent)",
        }
        if isinstance(input_args.get("headers"), dict):
            for k, v in input_args["headers"].items():
                if isinstance(v, str):
                    headers[k] = v

        current_url = raw_url
        max_redirects = 5
        redirect_count = 0

        async_client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=effective_timeout,
        )

        try:
            while True:
                parsed = urllib.parse.urlparse(current_url)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError(f"Unsupported protocol: {parsed.scheme}. Only HTTP and HTTPS are permitted.")

                hostname = parsed.hostname or ""
                if not hostname:
                    raise ValueError(f"Invalid URL missing hostname: {current_url}")

                await validate_host_ssrf(hostname, dns_lookup=dns_lookup)

                response = await async_client.request(
                    method=method,
                    url=current_url,
                    headers=headers,
                )

                if response.status_code in (301, 302, 303, 307, 308):
                    redirect_count += 1
                    if redirect_count > max_redirects:
                        raise ValueError(f"Too many redirects (exceeded limit of {max_redirects})")

                    location = response.headers.get("Location")
                    if not location:
                        break

                    current_url = urllib.parse.urljoin(current_url, location)
                    continue

                break

            status_line = f"HTTP {response.status_code} {response.reason_phrase}"
            content_type = response.headers.get("content-type", "unknown")

            if method == "HEAD":
                return f"{status_line}\nContent-Type: {content_type}\n\n[HEAD request completed]"

            body_bytes = response.content
            truncated = len(body_bytes) > effective_max_bytes
            if truncated:
                body_bytes = body_bytes[:effective_max_bytes]

            body_text = body_bytes.decode("utf-8", errors="replace")
            if truncated:
                body_text += f"\n[Response body truncated: reached {effective_max_bytes} bytes limit]"

            if len(body_text) > 30_000:
                body_text = body_text[:30_000] + "\n[Response text truncated: exceeded character limit]"

            return f"{status_line}\nContent-Type: {content_type}\nBytes: {len(body_bytes)}\n\n{body_text}"
        finally:
            if client is None:
                await async_client.aclose()

    return GuardedTool(
        name="web_fetch",
        description="Fetch content from an HTTP or HTTPS URL with strict size and timeout limits.",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The HTTP or HTTPS URL to fetch."},
                "method": {"type": "string", "enum": ["GET", "HEAD"], "default": "GET"},
                "headers": {"type": "object"},
                "maxBytes": {"type": "number", "default": 100000},
                "timeoutMs": {"type": "number", "default": 15000},
            },
            "required": ["url"],
        },
        manifest=manifest,
        execute_fn=execute,
        requires_approval=False,
    )
