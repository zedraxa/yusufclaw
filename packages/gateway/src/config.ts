import { z } from 'zod';
const positive = (fallback: number, max: number) => z.coerce.number().int().positive().max(max).default(fallback);
const schema = z.object({
  MODEL_PROVIDER: z.enum(['bedrock', 'ollama', 'openai']).default('bedrock'),
  BEDROCK_MODEL_ID: z.preprocess(value => value === '' ? undefined : value, z.string().trim().min(1).optional()),
  AWS_PROFILE: z.string().trim().min(1).default('yusufclaw'),
  AWS_REGION: z.string().trim().min(1).default('us-east-1'),
  OLLAMA_MODEL: z.string().trim().min(1).default('llama3.2'),
  OLLAMA_BASE_URL: z.string().url().default('http://127.0.0.1:11434'),
  OPENAI_BASE_URL: z.string().url().default('https://api.openai.com/v1'),
  OPENAI_API_KEY: z.string().trim().min(1).optional(),
  OPENAI_MODEL: z.string().trim().min(1).optional(),
  WEB_HOST: z.string().trim().min(1).default('127.0.0.1'),
  WEB_PORT: positive(3000, 65535),
  WEB_AUTH_TOKEN: z.string().trim().min(1).optional(),
  WEB_USER_ID: z.string().trim().min(1).default('local'),
  SKILLS_DIR: z.string().min(1).default('skills'),
  ROUTINES_CONFIG: z.string().min(1).optional(),
  MEMORY_PATH: z.string().min(1).default('.data/memory.sqlite'),
  MAX_TOKENS: positive(2048, 65536), MODEL_TIMEOUT_MS: positive(120000, 600000),
  MAX_AGENT_STEPS: positive(8, 32), HISTORY_TURNS: positive(20, 100),
  CLI_USER_ID: z.string().min(1).default('local'),
  TELEGRAM_BOT_TOKEN: z.string().regex(/^\d+:[A-Za-z0-9_-]+$/).optional(),
  TELEGRAM_ALLOWED_USER_IDS: z.string().default(''),
});
export function loadConfig(env: NodeJS.ProcessEnv = process.env) {
  const parsed = schema.safeParse(env);
  if (!parsed.success) throw new Error(`Invalid configuration: ${[...new Set(parsed.error.issues.map(issue => issue.path.join('.')))].join(', ')}`);
  const config = parsed.data;
  if (config.MODEL_PROVIDER === 'bedrock' && !config.BEDROCK_MODEL_ID) throw new Error('BEDROCK_MODEL_ID is required for Bedrock');
  if (config.MODEL_PROVIDER === 'openai' && (!config.OPENAI_API_KEY || !config.OPENAI_MODEL)) throw new Error('OPENAI_API_KEY and OPENAI_MODEL are required for OpenAI-compatible mode');
  const allowedIds = config.TELEGRAM_ALLOWED_USER_IDS.split(',').map(id => id.trim()).filter(Boolean);
  if (allowedIds.some(id => !/^[1-9]\d*$/.test(id))) throw new Error('TELEGRAM_ALLOWED_USER_IDS must contain numeric user IDs');
  if (!['http:', 'https:'].includes(new URL(config.OLLAMA_BASE_URL).protocol)) throw new Error('OLLAMA_BASE_URL must use HTTP or HTTPS');
  return { ...config, allowedIds };
}
