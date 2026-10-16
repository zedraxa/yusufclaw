import 'dotenv/config';
import { Agent, SqliteMemory } from '@yusufclaw/core';
import { BedrockProvider, OllamaProvider } from '@yusufclaw/models';
import { createDefaultTools } from '@yusufclaw/tools';
import { MessageBus } from './bus.js';
import { loadConfig } from './config.js';
import { runCli } from './cli.js';
import { TelegramAdapter, TelegramApi } from './telegram.js';

async function main() {
  const [mode, ...args] = process.argv.slice(2);
  if (mode === '--help' || args.includes('--help')) {
    console.log('Usage: pnpm cli [--message TEXT] [--session NAME]\n       pnpm telegram\nConfigure .env using .env.example.');
    return;
  }
  if (mode !== 'cli' && mode !== 'telegram') throw new Error('Choose cli or telegram');
  const config = loadConfig();
  if (mode === 'telegram' && (!config.TELEGRAM_BOT_TOKEN || !config.allowedIds.length)) throw new Error('Telegram requires a token and allowed user IDs');
  const provider = config.MODEL_PROVIDER === 'bedrock'
    ? new BedrockProvider({ model: config.BEDROCK_MODEL_ID!, region: config.AWS_REGION, profile: config.AWS_PROFILE, maxTokens: config.MAX_TOKENS, timeoutMs: config.MODEL_TIMEOUT_MS })
    : new OllamaProvider({ model: config.OLLAMA_MODEL, baseUrl: config.OLLAMA_BASE_URL, maxTokens: config.MAX_TOKENS, timeoutMs: config.MODEL_TIMEOUT_MS });
  const memory = new SqliteMemory(config.MEMORY_PATH);
  const agent = new Agent(provider, memory, createDefaultTools(), { maxSteps: config.MAX_AGENT_STEPS, historyTurns: config.HISTORY_TURNS });
  const bus = new MessageBus(agent, { cli: new Set([config.CLI_USER_ID]), telegram: new Set(config.allowedIds) });
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  try {
    if (mode === 'cli') await runCli(bus, config.CLI_USER_ID, args, controller.signal);
    else await new TelegramAdapter(bus, memory, new TelegramApi(config.TELEGRAM_BOT_TOKEN!), new Set(config.allowedIds), config.TELEGRAM_BOT_TOKEN!).run(controller.signal);
  } finally {
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
    memory.close();
    if (provider instanceof BedrockProvider) provider.close();
  }
}
void main().catch(() => {
  // SDK/network errors can contain URLs, credentials, or input. Keep stderr sanitized.
  console.error('YusufClaw could not complete the request. Check command arguments, .env, credentials, and provider connectivity.');
  process.exitCode = 1;
});
