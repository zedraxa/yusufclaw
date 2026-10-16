import { createHash, timingSafeEqual } from 'node:crypto';
import { createServer, type IncomingMessage as HttpRequest, type ServerResponse } from 'node:http';
import { z } from 'zod';
import type { SqliteMemory, RoutineTarget } from '@yusufclaw/core';
import type { MessageBus } from './bus.js';
import { chatPage, chatScript, chatStyle } from './web-page.js';

const messageSchema = z.object({ text: z.string().trim().min(1).max(16000), conversationId: z.string().trim().min(1).max(128), requestId: z.string().trim().min(1).max(128) }).strict();
interface InboxItem { requestId: string; conversationId: string; reply: string }
export class WebAdapter {
  private readonly controller = new AbortController();
  private readonly pending = new Set<Promise<void>>();
  private readonly tokenHash: Buffer;
  readonly server;
  constructor(private readonly bus: MessageBus, private readonly memory: SqliteMemory, private readonly options: { token: string; userId: string; host?: string; port?: number }) {
    if (!options.token.trim()) throw new Error('WEB_AUTH_TOKEN is required');
    this.tokenHash = createHash('sha256').update(options.token).digest();
    this.server = createServer((request, response) => {
      const task = this.handle(request, response).catch(() => { if (!response.destroyed) this.json(response, 500, { error: 'Request failed' }); });
      this.pending.add(task);
      void task.finally(() => this.pending.delete(task));
    });
    this.server.requestTimeout = 15000;
    this.server.headersTimeout = 10000;
  }
  private json(response: ServerResponse, status: number, body: unknown): void {
    response.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
    response.end(JSON.stringify(body));
  }
  private async handle(request: HttpRequest, response: ServerResponse): Promise<void> {
    response.setHeader('cache-control', 'no-store');
    response.setHeader('x-content-type-options', 'nosniff');
    response.setHeader('referrer-policy', 'no-referrer');
    response.setHeader('content-security-policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'");
    const path = request.url;
    if (request.method === 'GET' && (path === '/' || path === '/app.js' || path === '/style.css')) {
      response.setHeader('content-type', path === '/' ? 'text/html; charset=utf-8' : path === '/app.js' ? 'text/javascript; charset=utf-8' : 'text/css; charset=utf-8');
      response.end(path === '/' ? chatPage : path === '/app.js' ? chatScript : chatStyle); return;
    }
    const supplied = request.headers.authorization ?? '';
    const valid = supplied.startsWith('Bearer ') && timingSafeEqual(this.tokenHash, createHash('sha256').update(supplied.slice(7)).digest());
    if (!valid) { this.json(response, 401, { error: 'Unauthorized' }); return; }
    // Browser requests must originate from this host; never enable cross-origin API access.
    if (request.headers.origin && request.headers.origin !== `http://${request.headers.host}` && request.headers.origin !== `https://${request.headers.host}`) {
      this.json(response, 403, { error: 'Origin denied' }); return;
    }
    if (path === '/api/routines' && request.method === 'GET') { this.json(response, 200, this.inbox()); return; }
    if (path !== '/api/chat' || request.method !== 'POST') { this.json(response, 404, { error: 'Not found' }); return; }
    if (request.headers['content-type']?.split(';')[0].trim() !== 'application/json') { this.json(response, 415, { error: 'JSON required' }); return; }
    const chunks: Buffer[] = [];
    let size = 0;
    // Send the error before closing an oversized request's connection.
    for await (const chunk of request) {
      size += Buffer.byteLength(chunk);
      if (size > 100_000) { response.setHeader('connection', 'close'); this.json(response, 413, { error: 'Request too large' }); return; }
      chunks.push(Buffer.from(chunk));
    }
    let parsed;
    try { parsed = messageSchema.safeParse(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
    catch { this.json(response, 400, { error: 'Invalid message' }); return; }
    if (!parsed.success) { this.json(response, 400, { error: 'Invalid message' }); return; }
    const controller = new AbortController();
    const abort = () => { if (!response.writableEnded) controller.abort(); };
    response.once('close', abort);
    try {
      const reply = await this.bus.dispatch({ channel: 'web', userId: this.options.userId, ...parsed.data }, undefined, AbortSignal.any([controller.signal, this.controller.signal]));
      this.json(response, 200, { reply });
    } catch { if (!response.destroyed) this.json(response, 502, { error: 'Request failed. Check provider configuration and connectivity.' }); }
    finally { response.removeListener('close', abort); }
  }
  private inboxKey(): string { return `web-inbox:${this.options.userId}`; }
  private inbox(): InboxItem[] { return JSON.parse(this.memory.getState(this.inboxKey()) ?? '[]') as InboxItem[]; }
  async deliver(target: RoutineTarget, reply: string, requestId: string): Promise<void> {
    if (target.channel !== 'web' || target.userId !== this.options.userId) throw new Error('Invalid web delivery target');
    const items = this.inbox();
    if (!items.some(item => item.requestId === requestId && item.conversationId === target.conversationId)) {
      items.push({ requestId, conversationId: target.conversationId, reply });
      this.memory.setState(this.inboxKey(), JSON.stringify(items.slice(-100)));
    }
  }
  async listen(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const failed = (error: Error) => reject(error);
      this.server.once('error', failed);
      this.server.listen(this.options.port ?? 3000, this.options.host ?? '127.0.0.1', () => { this.server.removeListener('error', failed); resolve(); });
    });
  }
  async close(): Promise<void> {
    this.controller.abort();
    const closed = new Promise<void>((resolve, reject) => this.server.close(error => error ? reject(error) : resolve()));
    this.server.closeAllConnections();
    await Promise.allSettled([...this.pending]);
    await closed;
  }
}
