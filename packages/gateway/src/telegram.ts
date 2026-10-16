import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { z } from 'zod';
import type { SqliteMemory } from '@yusufclaw/core';
import type { MessageBus } from './bus.js';
const updateSchema = z.object({ update_id: z.number().int(), message: z.object({
  text: z.string().optional(), from: z.object({ id: z.number().int(), is_bot: z.boolean().optional() }).optional(),
  chat: z.object({ id: z.number().int(), type: z.string() }),
}).optional() });
export type TelegramUpdate = z.infer<typeof updateSchema>;
export class TelegramApiError extends Error {
  constructor(readonly code: number, readonly retryAfter = 3) { super(`Telegram API failed (${code})`); }
}
export interface TelegramTransport {
  call(method: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<unknown>;
}
export class TelegramApi implements TelegramTransport {
  constructor(private readonly token: string, private readonly fetcher: typeof fetch = fetch) {}
  async call(method: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    const timeout = AbortSignal.timeout(40_000);
    let response: Response;
    try {
      response = await this.fetcher(`https://api.telegram.org/bot${this.token}/${method}`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      });
    } catch { throw new Error('Telegram network request failed'); } // Never expose token-bearing URLs.
    let data: { ok?: boolean; result?: unknown; error_code?: number; parameters?: { retry_after?: number } };
    try { data = await response.json() as typeof data; } catch { throw new TelegramApiError(response.status); }
    if (!response.ok || !data.ok) throw new TelegramApiError(data.error_code ?? response.status, data.parameters?.retry_after);
    return data.result;
  }
}
export function splitTelegramText(text: string): string[] {
  // Keep each chunk below Telegram's UTF-16 limit without splitting surrogate pairs.
  const chunks: string[] = [];
  let chunk = '';
  for (const character of text) {
    if (chunk.length + character.length > 4000) { chunks.push(chunk); chunk = ''; }
    chunk += character;
  }
  if (chunk) chunks.push(chunk);
  return chunks;
}
export class TelegramAdapter {
  private readonly stateKey: string;
  constructor(private readonly bus: MessageBus, private readonly memory: SqliteMemory, private readonly api: TelegramTransport, private readonly allowed: Set<string>, token: string, private readonly wait: (ms: number, signal: AbortSignal) => Promise<void> = async (ms, signal) => { await delay(ms, undefined, { signal }); }) {
    if (!allowed.size) throw new Error('TELEGRAM_ALLOWED_USER_IDS must not be empty');
    this.stateKey = `telegram-offset:${createHash('sha256').update(token).digest('hex')}`;
  }
  async handle(update: TelegramUpdate, signal?: AbortSignal): Promise<void> {
    const message = update.message;
    // Private chats only: avoid revealing personal memory in a group or channel.
    if (!message?.text || !message.from || message.from.is_bot || message.chat.type !== 'private' || !this.allowed.has(String(message.from.id))) return;
    let reply: string;
    try {
      reply = await this.bus.dispatch({ channel: 'telegram', userId: String(message.from.id), conversationId: String(message.chat.id), requestId: String(update.update_id), text: message.text }, undefined, signal);
    } catch {
      signal?.throwIfAborted();
      reply = 'Request failed. Check provider configuration and connectivity, then send your message again.';
    }
    for (const text of splitTelegramText(reply)) await this.api.call('sendMessage', { chat_id: message.chat.id, text }, signal);
  }
  async run(signal: AbortSignal): Promise<void> {
    let offset = Number(this.memory.getState(this.stateKey) ?? '0');
    if (!Number.isSafeInteger(offset) || offset < 0) throw new Error('Invalid saved Telegram offset');
    while (!signal.aborted) {
      try {
        const updates = z.array(updateSchema).parse(await this.api.call('getUpdates', { offset, timeout: 30, allowed_updates: ['message'] }, signal));
        for (const update of updates) {
          signal.throwIfAborted();
          if (update.update_id < offset) continue;
          await this.handle(update, signal);
          offset = update.update_id + 1;
          this.memory.setState(this.stateKey, String(offset));
        }
      } catch (error) {
        if (signal.aborted) break;
        if (error instanceof TelegramApiError && [401, 403, 409].includes(error.code)) throw error;
        console.error('Telegram polling failed; retrying.');
        const seconds = error instanceof TelegramApiError ? Math.min(Math.max(error.retryAfter, 1), 300) : 3;
        await this.wait(seconds * 1000, signal).catch(() => {});
      }
    }
  }
}
