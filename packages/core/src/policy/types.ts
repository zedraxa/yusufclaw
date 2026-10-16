import type { ToolEffect } from '@yusufclaw/tools';

export type Integrity = 'trusted' | 'untrusted';
export type Secrecy = 'public' | 'private' | 'secret';

export interface Label {
  origin: string;
  integrity: Integrity;
  secrecy: Secrecy;
}

export interface Value<T = unknown> {
  v: T;
  labels: Label[];
}

export interface SessionTaint {
  contaminated: boolean;
  sources: string[];
}

export interface ActionCard {
  approvalId: string;
  nonce: string;
  expiresAt: string;
  toolId: string;
  toolVersion: string;
  argsSha256: string;
  rendered: {
    title: string;
    exact: string;
    effects: ToolEffect[];
    taint: 'clean' | 'contaminated';
    reversible: boolean;
    uninspectable?: string;
  };
}

export type PolicyDecision =
  | { kind: 'allow' }
  | { kind: 'deny'; reason: string }
  | { kind: 'card'; card: ActionCard };
