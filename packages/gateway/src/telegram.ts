import { createHash } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { z } from 'zod';
import { ActionCardStore, type ActionCard, type SqliteMemory } from '@yusufclaw/core';
import type { ToolCall } from '@yusufclaw/models';
import type { ApproveTool } from '@yusufclaw/tools';
import type { MessageBus } from './bus.js';

const updateSchema = z.object({
  update_id: z.number().int(),
  message: z.object({
    text: z.string().optional(),
    from: z.object({ id: z.number().int(), is_bot: z.boolean().optional() }).optional(),
    chat: z.object({ id: z.number().int(), type: z.string() }),
  }).optional(),
  callback_query: z.object({
    id: z.string(),
    from: z.object({ id: z.number().int() }),
    message: z.object({
      message_id: z.number().int(),
      chat: z.object({ id: z.number().int(), type: z.string().optional() }),
      text: z.string().optional(),
    }).optional(),
    data: z.string().optional(),
  }).optional(),
});

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

export function formatTelegramCardMessage(card: ActionCard, agentClaim?: string): string {
  const taintBadge = card.rendered.taint === 'contaminated' ? '🔴 KİRLİ (Güvenilmeyen veri görüldü)' : '🟢 TEMİZ';
  const effectsStr = card.rendered.effects && card.rendered.effects.length ? card.rendered.effects.join(', ') : 'yok';
  const reversibleStr = card.rendered.reversible ? 'Evet' : 'Hayır (Geri alınamaz)';

  let msg = `⚠️ [ONAY GEREKİYOR: ${card.rendered.title}]\n\n` +
    `• Eylem (Kesin): ${card.rendered.exact}\n` +
    `• Etkiler: ${effectsStr}\n` +
    `• Oturum Durumu: ${taintBadge}\n` +
    `• Geri Alınabilir: ${reversibleStr}\n` +
    `• Kart No: ${card.approvalId.slice(0, 8)}\n`;

  if (agentClaim && agentClaim.trim()) {
    msg += `\n[Ajanın İddiası (Güvenilmeyen Açıklama)]:\n"${agentClaim.trim()}"\n`;
  }

  return msg;
}

export function buildTelegramCardKeyboard(card: ActionCard) {
  return {
    inline_keyboard: [
      [
        { text: '✅ Bir kez onayla', callback_data: `approve:${card.approvalId}:${card.nonce}` },
        { text: '❌ Reddet', callback_data: `deny:${card.approvalId}:${card.nonce}` },
      ],
      [
        { text: 'ℹ️ Ayrıntı', callback_data: `details:${card.approvalId}:${card.nonce}` }
      ]
    ]
  };
}

export class TelegramAdapter {
  private readonly stateKey: string;
  readonly cardStore: ActionCardStore;
  private readonly pendingApprovals = new Map<string, (approved: boolean) => void>();
  private readonly activeCalls = new Map<string, Record<string, unknown>>();

  constructor(
    private readonly bus: MessageBus,
    private readonly memory: SqliteMemory,
    private readonly api: TelegramTransport,
    private readonly allowed: Set<string>,
    token: string,
    private readonly wait: (ms: number, signal: AbortSignal) => Promise<void> = async (ms, signal) => { await delay(ms, undefined, { signal }); },
    cardStore?: ActionCardStore
  ) {
    if (!allowed.size) throw new Error('TELEGRAM_ALLOWED_USER_IDS must not be empty');
    this.stateKey = `telegram-offset:${createHash('sha256').update(token).digest('hex')}`;
    this.cardStore = cardStore ?? new ActionCardStore();
  }

  async handleCallbackQuery(query: NonNullable<TelegramUpdate['callback_query']>, signal?: AbortSignal): Promise<void> {
    if (!this.allowed.has(String(query.from.id))) {
      await this.api.call('answerCallbackQuery', {
        callback_query_id: query.id,
        text: 'Yetkisiz kullanıcı',
        show_alert: true
      }, signal).catch(() => {});
      return;
    }

    const data = query.data ?? '';
    const [action, approvalId, nonce] = data.split(':');
    if (!action || !approvalId || !nonce) {
      await this.api.call('answerCallbackQuery', {
        callback_query_id: query.id,
        text: 'Geçersiz onay isteği',
        show_alert: true
      }, signal).catch(() => {});
      return;
    }

    if (action === 'approve') {
      const actualArgs = this.activeCalls.get(approvalId);
      const result = this.cardStore.consume(approvalId, nonce, actualArgs);
      if (!result.success) {
        await this.api.call('answerCallbackQuery', {
          callback_query_id: query.id,
          text: `Onay reddedildi: ${result.reason}`,
          show_alert: true
        }, signal).catch(() => {});

        const resolver = this.pendingApprovals.get(approvalId);
        if (resolver) resolver(false);
        return;
      }

      await this.api.call('answerCallbackQuery', {
        callback_query_id: query.id,
        text: '✅ Eylem onaylandı ve çalıştırılıyor'
      }, signal).catch(() => {});

      const resolver = this.pendingApprovals.get(approvalId);
      if (resolver) resolver(true);
      return;
    }

    if (action === 'deny') {
      this.cardStore.deny(approvalId, nonce);
      await this.api.call('answerCallbackQuery', {
        callback_query_id: query.id,
        text: '❌ Eylem reddedildi'
      }, signal).catch(() => {});

      const resolver = this.pendingApprovals.get(approvalId);
      if (resolver) resolver(false);
      return;
    }

    if (action === 'details') {
      const record = this.cardStore.get(approvalId);
      const info = record
        ? `Etkiler: ${record.card.rendered.effects.join(', ')}\nArgs SHA: ${record.card.argsSha256.slice(0, 16)}...`
        : 'Kart bulunamadı';
      await this.api.call('answerCallbackQuery', {
        callback_query_id: query.id,
        text: info,
        show_alert: true
      }, signal).catch(() => {});
      return;
    }
  }

  async handle(update: TelegramUpdate, signal?: AbortSignal): Promise<void> {
    if (update.callback_query) {
      await this.handleCallbackQuery(update.callback_query, signal);
      return;
    }

    const message = update.message;
    // Private chats only: avoid revealing personal memory in a group or channel.
    if (!message?.text || !message.from || message.from.is_bot || message.chat.type !== 'private' || !this.allowed.has(String(message.from.id))) return;

    let reply: string;
    try {
      const approveCallback: ApproveTool = async (call: ToolCall, card?: unknown): Promise<boolean> => {
        const actionCard = (card && typeof card === 'object' && 'approvalId' in card) ? (card as ActionCard) : undefined;
        if (!actionCard) {
          return false;
        }

        this.cardStore.register(actionCard, call.input);
        this.activeCalls.set(actionCard.approvalId, call.input);

        const cardText = formatTelegramCardMessage(actionCard);
        const reply_markup = buildTelegramCardKeyboard(actionCard);

        await this.api.call('sendMessage', {
          chat_id: message.chat.id,
          text: cardText,
          reply_markup
        }, signal);

        return new Promise<boolean>((resolve) => {
          const timer = setTimeout(() => {
            if (this.pendingApprovals.has(actionCard.approvalId)) {
              this.pendingApprovals.delete(actionCard.approvalId);
              this.activeCalls.delete(actionCard.approvalId);
              resolve(false);
            }
          }, 5 * 60 * 1000);
          timer.unref();

          this.pendingApprovals.set(actionCard.approvalId, (approved: boolean) => {
            clearTimeout(timer);
            this.pendingApprovals.delete(actionCard.approvalId);
            this.activeCalls.delete(actionCard.approvalId);
            resolve(approved);
          });
        });
      };

      reply = await this.bus.dispatch(
        { channel: 'telegram', userId: String(message.from.id), conversationId: String(message.chat.id), requestId: String(update.update_id), text: message.text },
        approveCallback,
        signal
      );
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
        const updates = z.array(updateSchema).parse(await this.api.call('getUpdates', { offset, timeout: 30, allowed_updates: ['message', 'callback_query'] }, signal));
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

