import { createHash } from 'node:crypto';
import type { ToolManifest } from '@yusufclaw/tools';
import { canonicalJson, renderCard } from './engine.js';
import type { ActionCard, SessionTaint, Value } from './types.js';

export interface CardRecord {
  card: ActionCard;
  status: 'pending' | 'consumed' | 'expired' | 'denied';
  createdAt: number;
  expectedArgs?: Record<string, unknown>;
}

export class ActionCardStore {
  private readonly records = new Map<string, CardRecord>();

  register(card: ActionCard, expectedArgs?: Record<string, unknown>): void {
    this.records.set(card.approvalId, {
      card,
      status: 'pending',
      createdAt: Date.now(),
      expectedArgs
    });
  }

  create(
    m: ToolManifest,
    args: Value<Record<string, unknown>>,
    s: SessionTaint,
    ttlMs = 5 * 60 * 1000
  ): ActionCard {
    const card = renderCard(m, args, s);
    card.expiresAt = new Date(Date.now() + ttlMs).toISOString();
    this.register(card, args.v);
    return card;
  }

  get(approvalId: string): CardRecord | undefined {
    return this.records.get(approvalId);
  }

  consume(
    approvalId: string,
    nonce: string,
    actualArgs?: Record<string, unknown>
  ): { success: boolean; reason?: string; card?: ActionCard } {
    const record = this.records.get(approvalId);
    if (!record) {
      return { success: false, reason: 'Action card not found' };
    }

    if (record.status === 'consumed') {
      return { success: false, reason: 'Replay detected: action card already consumed' };
    }

    if (record.status === 'denied') {
      return { success: false, reason: 'Action card was denied' };
    }

    if (new Date(record.card.expiresAt).getTime() < Date.now()) {
      record.status = 'expired';
      return { success: false, reason: 'Action card has expired' };
    }

    if (record.card.nonce !== nonce) {
      return { success: false, reason: 'Invalid nonce: nonce mismatch' };
    }

    if (actualArgs !== undefined) {
      const actualHash = createHash('sha256').update(canonicalJson(actualArgs)).digest('hex');
      if (actualHash !== record.card.argsSha256) {
        return { success: false, reason: 'Argument tampering detected: args hash mismatch' };
      }
    }

    record.status = 'consumed';
    return { success: true, card: record.card };
  }

  deny(approvalId: string, nonce: string): { success: boolean; reason?: string } {
    const record = this.records.get(approvalId);
    if (!record) {
      return { success: false, reason: 'Action card not found' };
    }

    if (record.status === 'consumed') {
      return { success: false, reason: 'Replay detected: action card already consumed' };
    }

    if (record.card.nonce !== nonce) {
      return { success: false, reason: 'Invalid nonce: nonce mismatch' };
    }

    record.status = 'denied';
    return { success: true };
  }

  clear(): void {
    this.records.clear();
  }
}
