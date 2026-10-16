import type { AgentTool } from './index.js';

export interface PageMock {
  goto(url: string, options?: { timeout?: number; waitUntil?: string }): Promise<unknown>;
  title(): Promise<string>;
  innerText(selector: string): Promise<string>;
  content(): Promise<string>;
  close(): Promise<void>;
}

export interface BrowserContextMock {
  newPage(): Promise<PageMock>;
  close(): Promise<void>;
}

export interface BrowserInstanceMock {
  newContext(options?: Record<string, unknown>): Promise<BrowserContextMock>;
  close(): Promise<void>;
}

export interface BrowserLauncher {
  launch(options?: { headless?: boolean }): Promise<BrowserInstanceMock>;
}

export interface BrowserToolOptions {
  launcher?: BrowserLauncher;
  defaultTimeoutMs?: number;
  maxTimeoutMs?: number;
}

export function createBrowserTool(options: BrowserToolOptions = {}): AgentTool {
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 30_000;
  const maxTimeoutMs = options.maxTimeoutMs ?? 60_000;

  return {
    name: 'browser',
    description: 'Browse web pages using Playwright headless browser. Returns page title and text content.',
    inputSchema: {
      type: 'object',
      properties: {
        url: { type: 'string', description: 'The HTTP or HTTPS URL to visit.' },
        action: {
          type: 'string',
          enum: ['navigate', 'content', 'title'],
          description: 'Browser action: navigate (title + text), content (HTML), or title (page title only).'
        },
        timeoutMs: { type: 'number', description: 'Navigation timeout in milliseconds (max 60000).' }
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

      const reqTimeout = typeof input.timeoutMs === 'number' && Number.isFinite(input.timeoutMs) ? input.timeoutMs : defaultTimeoutMs;
      const effectiveTimeout = Math.min(Math.max(1000, reqTimeout), maxTimeoutMs);
      const action = (typeof input.action === 'string' && ['content', 'title'].includes(input.action)) ? input.action : 'navigate';

      let launcher = options.launcher;
      if (!launcher) {
        try {
          // Dynamic import to keep Playwright as an optional dependency without compile-time failure
          const moduleName = 'playwright';
          const imported = await (import(moduleName) as Promise<{ default?: { chromium?: BrowserLauncher }; chromium?: BrowserLauncher }>);
          const pw = imported.default || imported;
          if (pw && pw.chromium) {
            launcher = pw.chromium;
          }
        } catch {
          return 'Playwright is not installed. To use the browser tool, install playwright and browsers (`pnpm add -D playwright && npx playwright install`).';
        }
      }

      if (!launcher) {
        return 'Playwright is not installed. To use the browser tool, install playwright and browsers (`pnpm add -D playwright && npx playwright install`).';
      }

      let browser: BrowserInstanceMock | undefined;
      let context: BrowserContextMock | undefined;
      let page: PageMock | undefined;

      try {
        signal?.throwIfAborted();
        browser = await launcher.launch({ headless: true });
        context = await browser.newContext({
          userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) YusufClaw/0.1.0'
        });
        page = await context.newPage();

        signal?.throwIfAborted();

        await page.goto(parsed.toString(), {
          timeout: effectiveTimeout,
          waitUntil: 'domcontentloaded'
        });

        const title = (await page.title()) || 'Untitled';

        if (action === 'title') {
          return `Title: ${title}`;
        }

        if (action === 'content') {
          let html = await page.content();
          if (html.length > 30_000) {
            html = html.slice(0, 30_000) + '\n[Content truncated: exceeded 30000 characters]';
          }
          return `Title: ${title}\n\n${html}`;
        }

        // action === 'navigate'
        let text = '';
        try {
          text = await page.innerText('body');
        } catch {
          text = '[Could not extract body text]';
        }

        if (text.length > 30_000) {
          text = text.slice(0, 30_000) + '\n[Page text truncated: exceeded 30000 characters]';
        }

        return `Title: ${title}\nURL: ${parsed.toString()}\n\n${text}`;
      } catch (err) {
        if (signal?.aborted) throw new Error('Browser navigation aborted by user');
        const message = err instanceof Error ? err.message : String(err);
        return `Browser navigation failed: ${message}`;
      } finally {
        if (page) await page.close().catch(() => {});
        if (context) await context.close().catch(() => {});
        if (browser) await browser.close().catch(() => {});
      }
    }
  };
}

export const browserTool: AgentTool = createBrowserTool();
