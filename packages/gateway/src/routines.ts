import { RoutineScheduler, type Routine, type RoutineTarget, type SqliteMemory } from '@yusufclaw/core';
import type { MessageBus } from './bus.js';
import type { WebAdapter } from './web.js';
import { splitTelegramText, type TelegramTransport } from './telegram.js';

export function createRoutineScheduler(routines: Routine[], memory: SqliteMemory, bus: MessageBus, options: {
  cliUserId: string; telegramAllowed: Set<string>; telegram?: TelegramTransport; web?: WebAdapter; webUserId: string;
  output?: (text: string) => void;
}): RoutineScheduler {
  function authorize(target: RoutineTarget): void {
    if (target.channel === 'cli' && target.userId === options.cliUserId) return;
    if (target.channel === 'telegram' && options.telegram && options.telegramAllowed.has(target.userId) && target.conversationId === target.userId) return;
    if (target.channel === 'web' && options.web && target.userId === options.webUserId) return;
    throw new Error('Routine target is unavailable or unauthorized');
  }
  routines.forEach(routine => authorize(routine.target));
  return new RoutineScheduler(routines, memory, {
    dispatch: (message, signal) => { authorize(message); return bus.dispatch(message, undefined, signal); },
    deliver: async (target, reply, requestId, signal) => {
      authorize(target);
      if (target.channel === 'cli') (options.output ?? console.log)(`[${target.conversationId}] ${reply}`);
      else if (target.channel === 'web') await options.web!.deliver(target, reply, requestId);
      else for (const text of splitTelegramText(reply)) await options.telegram!.call('sendMessage', { chat_id: target.conversationId, text }, signal);
    },
  }, () => console.error('Scheduled routine failed; retrying while its minute is current.'));
}
