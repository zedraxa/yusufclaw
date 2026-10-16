import 'dotenv/config';
import { Agent, SqliteMemory, loadSkills, loadRoutines } from '@yusufclaw/core';
import { BedrockProvider, OllamaProvider, OpenAIProvider } from '@yusufclaw/models';
import { createDefaultTools } from '@yusufclaw/tools';
import { WebAdapter } from './web.js';
import { createRoutineScheduler } from './routines.js';
import { MessageBus } from './bus.js';
import { loadConfig } from './config.js';
import { runCli } from './cli.js';
import { TelegramAdapter, TelegramApi } from './telegram.js';

async function main() {
  const [mode, ...args] = process.argv.slice(2);
  if (mode === '--help' || args.includes('--help')) {
    console.log('Usage: pnpm cli [--message TEXT] [--session NAME]\n       pnpm telegram\n       pnpm web\n       pnpm routines\nConfigure .env using .env.example.');
    return;
  }
  if (mode !== 'cli' && mode !== 'telegram' && mode !== 'web' && mode !== 'routines') throw new Error('Choose cli, telegram, web, or routines');
  const config = loadConfig();
  if (mode === 'telegram' && (!config.TELEGRAM_BOT_TOKEN || !config.allowedIds.length)) throw new Error('Telegram requires a token and allowed user IDs');
  if (mode === 'web' && !config.WEB_AUTH_TOKEN) throw new Error('WEB_AUTH_TOKEN is required');
  if (mode === 'routines' && !config.ROUTINES_CONFIG) throw new Error('ROUTINES_CONFIG is required');
  const skills = await loadSkills(config.SKILLS_DIR);
  const routines = mode !== 'cli' && config.ROUTINES_CONFIG ? await loadRoutines(config.ROUTINES_CONFIG) : [];
  const provider = config.MODEL_PROVIDER === 'bedrock'
    ? new BedrockProvider({ model: config.BEDROCK_MODEL_ID!, region: config.AWS_REGION, profile: config.AWS_PROFILE, maxTokens: config.MAX_TOKENS, timeoutMs: config.MODEL_TIMEOUT_MS })
    : config.MODEL_PROVIDER === 'openai'
    ? new OpenAIProvider({ model: config.OPENAI_MODEL!, baseUrl: config.OPENAI_BASE_URL, apiKey: config.OPENAI_API_KEY!, maxTokens: config.MAX_TOKENS, timeoutMs: config.MODEL_TIMEOUT_MS })
    : new OllamaProvider({ model: config.OLLAMA_MODEL, baseUrl: config.OLLAMA_BASE_URL, maxTokens: config.MAX_TOKENS, timeoutMs: config.MODEL_TIMEOUT_MS });
  const memory = new SqliteMemory(config.MEMORY_PATH);
  const agent = new Agent(provider, memory, createDefaultTools(), { skills, maxSteps: config.MAX_AGENT_STEPS, historyTurns: config.HISTORY_TURNS });
  const bus = new MessageBus(agent, { cli: new Set([config.CLI_USER_ID]), telegram: new Set(config.allowedIds), web: new Set([config.WEB_USER_ID]) });
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  let web: WebAdapter | undefined;
  let scheduling: Promise<void> | undefined;
  try {
    if (mode === 'web') web = new WebAdapter(bus, memory, { token: config.WEB_AUTH_TOKEN!, userId: config.WEB_USER_ID, host: config.WEB_HOST, port: config.WEB_PORT });
    const telegram = config.TELEGRAM_BOT_TOKEN ? new TelegramApi(config.TELEGRAM_BOT_TOKEN) : undefined;
    const scheduler = createRoutineScheduler(routines, memory, bus, { cliUserId: config.CLI_USER_ID, telegramAllowed: new Set(config.allowedIds), telegram, web, webUserId: config.WEB_USER_ID });
    if (web) {
      await web.listen();
      console.log(`Web chat listening on http://${config.WEB_HOST}:${config.WEB_PORT}`);
    }
    if (mode !== 'cli') scheduling = scheduler.run(controller.signal).catch(() => { console.error('Routine scheduler stopped.'); controller.abort(); });
    if (mode === 'cli') await runCli(bus, config.CLI_USER_ID, args, controller.signal);
    else if (mode === 'telegram') await new TelegramAdapter(bus, memory, new TelegramApi(config.TELEGRAM_BOT_TOKEN!), new Set(config.allowedIds), config.TELEGRAM_BOT_TOKEN!).run(controller.signal);
    else await new Promise<void>(resolve => {
      if (controller.signal.aborted) resolve();
      else controller.signal.addEventListener('abort', () => resolve(), { once: true });
    });
  } finally {
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
    controller.abort();
    if (web?.server.listening) await web.close();
    await scheduling;
    memory.close();
    if (provider instanceof BedrockProvider) provider.close();
  }
}
void main().catch(() => {
  // SDK/network errors can contain URLs, credentials, or input. Keep stderr sanitized.
  console.error('YusufClaw could not complete the request. Check command arguments, .env, credentials, and provider connectivity.');
  process.exitCode = 1;
});
