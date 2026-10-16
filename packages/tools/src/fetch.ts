import type { AgentTool } from './index.js';

export interface WebFetchToolOptions {
  fetcher?: typeof fetch;
  defaultTimeoutMs?: number;
  maxTimeoutMs?: number;
  defaultMaxBytes?: number;
  maxBytesLimit?: number;
}

const BLOCKED_HOSTS = new Set([
  '169.254.169.254',
  'fd00:ec2::254',
  'metadata.google.internal',
  '100.100.100.200'
]);

export function createWebFetchTool(options: WebFetchToolOptions = {}): AgentTool {
  const fetcher = options.fetcher ?? fetch;
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 15_000;
  const maxTimeoutMs = options.maxTimeoutMs ?? 30_000;
  const defaultMaxBytes = options.defaultMaxBytes ?? 100_000;
  const maxBytesLimit = options.maxBytesLimit ?? 1_000_000;

  return {
    name: 'web_fetch',
    description: 'Fetch content from an HTTP or HTTPS URL with strict size and timeout limits.',
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

      if (!['http:', 'https:'].includes(parsed.protocol)) {
        throw new Error(`Unsupported protocol: ${parsed.protocol}. Only HTTP and HTTPS are permitted.`);
      }

      const hostname = parsed.hostname.toLowerCase();
      if (BLOCKED_HOSTS.has(hostname) || hostname.endsWith('.internal')) {
        throw new Error(`Access to host ${hostname} is blocked for security`);
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

      let response: Response;
      try {
        response = await fetcher(parsed.toString(), {
          method,
          headers: reqHeaders,
          signal: combinedSignal
        });
      } catch (err) {
        if (signal?.aborted) throw new Error('Fetch aborted by user');
        if (timeoutSignal.aborted) throw new Error(`Fetch timed out after ${effectiveTimeout}ms`);
        throw new Error(`Network fetch failed: ${err instanceof Error ? err.message : String(err)}`);
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
