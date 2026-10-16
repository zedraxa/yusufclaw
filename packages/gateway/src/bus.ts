import type { Agent } from '@yusufclaw/core';
import type { ApproveTool } from '@yusufclaw/tools';
export interface IncomingMessage {
  channel: 'cli' | 'telegram' | 'web'; userId: string; conversationId: string; requestId: string; text: string;
}
export class MessageBus {
  constructor(private readonly agent: Agent, private readonly allowed: { cli: Set<string>; telegram: Set<string>; web?: Set<string> }) {}
  async dispatch(message: IncomingMessage, approve?: ApproveTool, signal?: AbortSignal): Promise<string> {
    if (!this.allowed[message.channel]?.has(message.userId)) throw new Error('User is not authorized');
    const session = JSON.stringify([message.channel, message.userId, message.conversationId]);
    return this.agent.handle(session, message.requestId, message.text, approve, signal);
  }
}
