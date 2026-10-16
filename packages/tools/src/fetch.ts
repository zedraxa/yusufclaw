import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';
import type { AgentTool } from './index.js';

export type DnsLookup = (hostname: string) => Promise<Array<{ address: string; family: number }>>;

export interface WebFetchToolOptions {
  fetcher?: typeof fetch;
  dnsLookup?: DnsLookup;
  defaultTimeoutMs?: number;
  maxTimeoutMs?: number;
  defaultMaxBytes?: number;
  maxBytesLimit?: number;
}

const BLOCKED_HOST_EXACT = new Set([
  '169.254.169.254',
  'fd00:ec2::254',
  'metadata.google.internal',
  '100.100.100.200',
  'localhost'
]);

export function isBlockedHostname(hostname: string): boolean {
  if (!hostname || typeof hostname !== 'string') return true;
  const lower = hostname.toLowerCase().trim();
  if (BLOCKED_HOST_EXACT.has(lower)) return true;
  if (lower.endsWith('.internal') || lower.endsWith('.local') || lower.endsWith('.localhost')) return true;
  return false;
}

export function isBlockedIP(ip: string): boolean {
  if (!ip || typeof ip !== 'string') return true;
  const trimmed = ip.trim().toLowerCase();
  const cleanIp = trimmed.startsWith('[') && trimmed.endsWith(']') ? trimmed.slice(1, -1) : trimmed;

  // Handle IPv4-mapped IPv6 (::ffff:127.0.0.1 or ::ffff:7f00:1)
  if (cleanIp.startsWith('::ffff:')) {
    const v4part = cleanIp.slice(7);
    if (v4part.includes('.')) {
      return isBlockedIP(v4part);
    }
  }

  const version = isIP(cleanIp);
  if (version === 4) {
    const parts = cleanIp.split('.').map(Number);
    if (parts.length !== 4 || parts.some(p => isNaN(p) || p < 0 || p > 255)) return true;
    const [a, b, c] = parts;

    // 0.0.0.0/8 (Current network)
    if (a === 0) return true;
    // 10.0.0.0/8 (Private RFC1918)
    if (a === 10) return true;
    // 100.64.0.0/10 (Shared Address Space / CGNAT)
    if (a === 100 && b >= 64 && b <= 127) return true;
    // 127.0.0.0/8 (Loopback)
    if (a === 127) return true;
    // 169.254.0.0/16 (Link-local)
    if (a === 169 && b === 254) return true;
    // 172.16.0.0/12 (Private RFC1918)
    if (a === 172 && b >= 16 && b <= 31) return true;
    // 192.0.0.0/24 (IETF Protocol Assignments)
    if (a === 192 && b === 0 && c === 0) return true;
    // 192.0.2.0/24 (TEST-NET-1)
    if (a === 192 && b === 0 && c === 2) return true;
    // 192.88.99.0/24 (6to4 Relay Anycast)
    if (a === 192 && b === 88 && c === 99) return true;
    // 192.168.0.0/16 (Private RFC1918)
    if (a === 192 && b === 168) return true;
    // 198.18.0.0/15 (Network Interconnect Device Benchmark)
    if (a === 198 && (b === 18 || b === 19)) return true;
    // 198.51.100.0/24 (TEST-NET-2)
    if (a === 198 && b === 51 && c === 100) return true;
    // 203.0.113.0/24 (TEST-NET-3)
    if (a === 203 && b === 0 && c === 113) return true;
    // 224.0.0.0/4 (Multicast)
    if (a >= 224 && a <= 239) return true;
    // 240.0.0.0/4 (Reserved for Future Use / Broadcast)
    if (a >= 240) return true;

    return false;
  }

  if (version === 6) {
    if (cleanIp === '::1' || cleanIp === '0:0:0:0:0:0:0:1') return true;
    if (cleanIp === '::' || cleanIp === '0:0:0:0:0:0:0:0') return true;
    if (cleanIp.startsWith('fe8') || cleanIp.startsWith('fe9') || cleanIp.startsWith('fea') || cleanIp.startsWith('feb')) return true;
    if (cleanIp.startsWith('fc') || cleanIp.startsWith('fd')) return true;
    if (cleanIp.startsWith('ff')) return true;

    return false;
  }

  return true;
}

export async function validateHostSSRF(hostname: string, dnsLookup?: DnsLookup): Promise<void> {
  const lower = hostname.toLowerCase().trim();
  const cleanHost = lower.startsWith('[') && lower.endsWith(']') ? lower.slice(1, -1) : lower;

  if (isBlockedHostname(cleanHost)) {
    throw new Error(`Access to host ${hostname} is blocked for security (SSRF protection)`);
  }

  if (isIP(cleanHost)) {
    if (isBlockedIP(cleanHost)) {
      throw new Error(`Access to private, loopback, or link-local IP address ${hostname} is blocked for security (SSRF protection)`);
    }
    return;
  }

  try {
    const lookupFn = dnsLookup ?? (async (host: string) => {
      const addresses = await lookup(host, { all: true });
      return addresses;
    });

    const results = await lookupFn(cleanHost);
    if (!results || results.length === 0) {
      throw new Error(`DNS lookup failed for host: ${hostname}`);
    }

    for (const res of results) {
      if (isBlockedIP(res.address)) {
        throw new Error(`Access to host ${hostname} resolving to private/loopback/link-local address ${res.address} is blocked for security (SSRF protection)`);
      }
    }
  } catch (err) {
    if (err instanceof Error && err.message.includes('SSRF protection')) {
      throw err;
    }
    throw new Error(`DNS resolution failed or access blocked for host ${hostname}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

export function createWebFetchTool(options: WebFetchToolOptions = {}): AgentTool {
  const fetcher = options.fetcher ?? fetch;
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 15_000;
  const maxTimeoutMs = options.maxTimeoutMs ?? 30_000;
  const defaultMaxBytes = options.defaultMaxBytes ?? 100_000;
  const maxBytesLimit = options.maxBytesLimit ?? 1_000_000;

  return {
    name: 'web_fetch',
    description: 'Fetch content from an HTTP or HTTPS URL with strict size and timeout limits.',
    manifest: {
      id: 'web_fetch',
      version: '1.0.0',
      source: 'builtin',
      effects: ['read_untrusted', 'egress'],
      resources: {}
    },
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'The HTTP or HTTPS URL to fetch.' },
        method: {
          type: 'string',
          enum: ['GET', 'HEAD'],
          description: 'HTTP method (default GET).'
        },
        headers: {
          type: 'object',
          description: 'Optional HTTP request headers.',
          additionalProperties: { type: 'string' }
        },
        maxBytes: { type: 'number', description: 'Maximum response bytes to read (default 100000, max 1000000).' },
        timeoutMs: { type: 'number', description: 'Timeout in milliseconds (default 15000, max 30000).' }
      },
      required: ['url'],
      additionalProperties: false
    },
    requiresApproval: false,
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();
      const rawUrl = typeof input.url === 'string' ? input.url.trim() : '';
      if (!rawUrl) throw new Error('URL must be a non-empty string');

      let parsed: URL;
      try {
        parsed = new URL(rawUrl);
      } catch {
        throw new Error(`Invalid URL: ${rawUrl}`);
      }

      const method = (typeof input.method === 'string' && input.method.toUpperCase() === 'HEAD') ? 'HEAD' : 'GET';
      const reqTimeout = typeof input.timeoutMs === 'number' && Number.isFinite(input.timeoutMs) ? input.timeoutMs : defaultTimeoutMs;
      const effectiveTimeout = Math.min(Math.max(500, reqTimeout), maxTimeoutMs);

      const reqMaxBytes = typeof input.maxBytes === 'number' && Number.isFinite(input.maxBytes) ? input.maxBytes : defaultMaxBytes;
      const effectiveMaxBytes = Math.min(Math.max(1, reqMaxBytes), maxBytesLimit);

      const timeoutSignal = AbortSignal.timeout(effectiveTimeout);
      const combinedSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

      const reqHeaders: Record<string, string> = {
        'User-Agent': 'YusufClaw/0.1.0 (Personal AI Agent)'
      };
      if (input.headers && typeof input.headers === 'object') {
        for (const [k, v] of Object.entries(input.headers)) {
          if (typeof v === 'string') reqHeaders[k] = v;
        }
      }

      const MAX_REDIRECTS = 5;
      let redirectCount = 0;
      let response: Response;

      while (true) {
        if (!['http:', 'https:'].includes(parsed.protocol)) {
          throw new Error(`Unsupported protocol: ${parsed.protocol}. Only HTTP and HTTPS are permitted.`);
        }

        await validateHostSSRF(parsed.hostname, options.dnsLookup);

        try {
          response = await fetcher(parsed.toString(), {
            method,
            headers: reqHeaders,
            signal: combinedSignal,
            redirect: 'manual'
          });
        } catch (err) {
          if (signal?.aborted) throw new Error('Fetch aborted by user');
          if (timeoutSignal.aborted) throw new Error(`Fetch timed out after ${effectiveTimeout}ms`);
          throw new Error(`Network fetch failed: ${err instanceof Error ? err.message : String(err)}`);
        }

        if ([301, 302, 303, 307, 308].includes(response.status)) {
          redirectCount++;
          if (redirectCount > MAX_REDIRECTS) {
            throw new Error(`Too many redirects (exceeded limit of ${MAX_REDIRECTS})`);
          }
          const location = response.headers.get('location');
          if (!location) {
            break;
          }
          const nextUrl = new URL(location, parsed);
          parsed = nextUrl;
          continue;
        }

        break;
      }

      const statusLine = `HTTP ${response.status} ${response.statusText}`;
      const contentType = response.headers.get('content-type') || 'unknown';

      if (method === 'HEAD' || !response.body) {
        return `${statusLine}\nContent-Type: ${contentType}\n\n[HEAD request completed]`;
      }

      // Stream response body with size limit enforcement
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let totalBytes = 0;
      let truncated = false;

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) {
            if (totalBytes + value.byteLength > effectiveMaxBytes) {
              const allowedPart = value.subarray(0, effectiveMaxBytes - totalBytes);
              chunks.push(allowedPart);
              totalBytes += allowedPart.byteLength;
              truncated = true;
              await reader.cancel('Exceeded maxBytes limit');
              break;
            } else {
              chunks.push(value);
              totalBytes += value.byteLength;
            }
          }
        }
      } finally {
        reader.releaseLock();
      }

      const totalBuffer = new Uint8Array(totalBytes);
      let offset = 0;
      for (const chunk of chunks) {
        totalBuffer.set(chunk, offset);
        offset += chunk.byteLength;
      }

      const decoder = new TextDecoder('utf-8', { fatal: false });
      let bodyText = decoder.decode(totalBuffer);

      if (truncated) {
        bodyText += `\n[Response body truncated: reached ${effectiveMaxBytes} bytes limit]`;
      }

      const maxChars = 30_000;
      if (bodyText.length > maxChars) {
        bodyText = bodyText.slice(0, maxChars) + `\n[Response text truncated: exceeded character limit]`;
      }

      return `${statusLine}\nContent-Type: ${contentType}\nBytes: ${totalBytes}\n\n${bodyText}`;
    }
  };
}

export const webFetchTool: AgentTool = createWebFetchTool();

