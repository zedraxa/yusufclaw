import { createInterface } from 'node:readline/promises';
import { parseArgs } from 'node:util';
import { randomUUID } from 'node:crypto';
import type { MessageBus } from './bus.js';

export async function runCli(bus: MessageBus, userId: string, args: string[], signal?: AbortSignal): Promise<void> {
  const { values } = parseArgs({ args, options: { message: { type: 'string', short: 'm' }, session: { type: 'string', short: 's', default: 'default' } }, strict: true });
  const send = (text: string, approve?: Parameters<MessageBus['dispatch']>[1]) => bus.dispatch({
    channel: 'cli', userId, conversationId: values.session!, requestId: randomUUID(), text,
  }, approve, signal);
  if (values.message !== undefined) { console.log(await send(values.message)); return; }
  if (!process.stdin.isTTY) throw new Error('Use --message for non-interactive input');
  const reader = createInterface({ input: process.stdin, output: process.stdout });
  const close = () => reader.close();
  signal?.addEventListener('abort', close, { once: true });
  console.log('YusufClaw — /help for commands, /exit to quit.');
  try {
    while (!signal?.aborted) {
      let input: string;
      try { input = await reader.question('You> '); } catch { break; }
      if (input.trim() === '/exit') break;
      if (!input.trim()) continue;
      try {
        const result = await send(input, async call => {
          const answer = await reader.question(`Approve ${call.name} ${JSON.stringify(call.input)}? Type yes: `);
          return answer.trim() === 'yes';
        });
        console.log(`YusufClaw> ${result}`);
      } catch { if (!signal?.aborted) console.error('Request failed. Check provider configuration and connectivity, then retry.'); }
    }
  } finally { signal?.removeEventListener('abort', close); reader.close(); }
}
