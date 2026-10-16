import { createHash, randomBytes, randomUUID } from 'node:crypto';
import type { ToolManifest } from '@yusufclaw/tools';
import type { ActionCard, PolicyDecision, SessionTaint, Value } from './types.js';

export function canonicalJson(obj: unknown): string {
  if (obj === null || typeof obj !== 'object') {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return '[' + obj.map(canonicalJson).join(',') + ']';
  }
  const keys = Object.keys(obj as Record<string, unknown>).sort();
  const entries = keys.map(k => `${JSON.stringify(k)}:${canonicalJson((obj as Record<string, unknown>)[k])}`);
  return '{' + entries.join(',') + '}';
}

export class Grants {
  constructor(
    private readonly options: {
      allowedTools?: Set<string>;
      allowedHosts?: Set<string>;
      allowedPaths?: Set<string>;
    } = {}
  ) {}

  covers(m: ToolManifest, input: Record<string, unknown>): boolean {
    if (this.options.allowedTools && !this.options.allowedTools.has(m.id)) {
      return false;
    }
    if (this.options.allowedHosts && typeof input.url === 'string') {
      try {
        const host = new URL(input.url).hostname;
        if (!this.options.allowedHosts.has(host)) return false;
      } catch {
        return false;
      }
    }
    return true;
  }
}

export function renderCard(
  m: ToolManifest,
  args: Value<Record<string, unknown>>,
  s: SessionTaint
): ActionCard {
  const approvalId = randomUUID();
  const nonce = randomBytes(16).toString('hex');
  const expiresAt = new Date(Date.now() + 5 * 60 * 1000).toISOString();
  const argsSha256 = createHash('sha256').update(canonicalJson(args.v)).digest('hex');

  let title = m.id;
  let exact = JSON.stringify(args.v);

  switch (m.id) {
    case 'web_fetch':
      title = 'Fetch External Web URL';
      exact = typeof args.v.url === 'string' ? args.v.url : exact;
      break;
    case 'browser':
      title = 'Browse External Web Page';
      exact = `${String(args.v.action ?? 'navigate')} ${String(args.v.url ?? '')}`;
      break;
    case 'shell':
      title = 'Execute Shell Command';
      exact = typeof args.v.command === 'string' ? args.v.command : exact;
      break;
    case 'file_write':
      title = 'Write Local File';
      exact = `${String(args.v.path ?? '')} (${Buffer.byteLength(String(args.v.content ?? ''), 'utf-8')} bytes)`;
      break;
    case 'delegate_agent':
      title = 'Delegate to Local Agent';
      exact = `${String(args.v.agent ?? '')} ${String(args.v.prompt ?? '')}`;
      break;
  }

  const reversible = !m.effects.includes('exec') &&
                     !m.effects.includes('write_local') &&
                     !m.effects.includes('spend');

  return {
    approvalId,
    nonce,
    expiresAt,
    toolId: m.id,
    toolVersion: m.version,
    argsSha256,
    rendered: {
      title,
      exact,
      effects: m.effects,
      taint: s.contaminated ? 'contaminated' : 'clean',
      reversible
    }
  };
}

export function sensitiveParamsFromUntrusted(
  _m: ToolManifest,
  args: Value<Record<string, unknown>>
): boolean {
  if (args.labels && args.labels.some(l => l.integrity === 'untrusted')) {
    if (args.v.url || args.v.to || args.v.path || args.v.command) {
      return true;
    }
  }
  return false;
}

export function decide(
  m: ToolManifest,
  args: Value<Record<string, unknown>>,
  s: SessionTaint,
  g?: Grants
): PolicyDecision {
  const grants = g ?? new Grants();

  // 1) Capability: tool + resource must be covered by grants (Progent, monotonic)
  if (!grants.covers(m, args.v)) {
    return { kind: 'deny', reason: 'grant' };
  }

  // 2) Secret protection: secret-labeled data cannot be egressed (FIDES)
  if (m.effects.includes('egress') && args.labels.some(l => l.secrecy === 'secret')) {
    return { kind: 'deny', reason: 'secret-egress' };
  }

  // 3) Lethal Trifecta / Ölümcül üçlü: contaminated session with egress/send_message/spend/exec -> ALWAYS requires card
  if (s.contaminated && m.effects.some(e => ['egress', 'send_message', 'spend', 'exec'].includes(e))) {
    return { kind: 'card', card: renderCard(m, args, s) };
  }

  // 4) Sensitive parameter from untrusted source -> requires card
  if (sensitiveParamsFromUntrusted(m, args)) {
    return { kind: 'card', card: renderCard(m, args, s) };
  }

  return { kind: 'allow' };
}
