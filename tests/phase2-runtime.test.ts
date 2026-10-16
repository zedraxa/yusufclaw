import { createServer } from 'node:http';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { expect, it } from 'vitest';
const exec = promisify(execFile);
it('runs configured OpenAI skills/routines through the compiled entry point and shuts down cleanly', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'yc-phase2-'));
  const requests: Array<{ messages: Array<{ content: string }> }> = [];
  const server = createServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    requests.push(JSON.parse(body));
    response.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: 'Routine completed' } }] }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); if (!address || typeof address === 'string') throw new Error();
  const entry = resolve('packages/gateway/dist/main.js');
  await mkdir(join(dir, 'skills'));
  await writeFile(join(dir, 'skills', 'planning.md'), 'Use the trusted planning instruction.');
  await writeFile(join(dir, 'routines.json'), JSON.stringify([{ id: 'plan', cron: '* * * * *', prompt: 'Make a plan', target: { channel: 'cli', userId: 'local', conversationId: 'planning' } }]));
  const env = { ...process.env, MODEL_PROVIDER: 'openai', OPENAI_BASE_URL: `http://127.0.0.1:${address.port}/v1`, OPENAI_API_KEY: 'fixture-key', OPENAI_MODEL: 'fixture-model', MEMORY_PATH: join(dir, 'memory.sqlite'), CLI_USER_ID: 'local', TELEGRAM_BOT_TOKEN: undefined, TELEGRAM_ALLOWED_USER_IDS: '', WEB_AUTH_TOKEN: undefined, SKILLS_DIR: join(dir, 'skills'), ROUTINES_CONFIG: join(dir, 'routines.json'), DOTENV_CONFIG_PATH: join(dir, 'absent.env') };
  const child = spawn(process.execPath, [entry, 'routines'], { env, stdio: ['ignore', 'pipe', 'pipe'] });
  const exited = new Promise<{ code: number | null; signal: string | null }>((resolve, reject) => {
    child.once('error', reject); child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  let stderr = ''; child.stderr.on('data', chunk => { stderr += chunk; });
  const timeout = setTimeout(() => child.kill('SIGKILL'), 10000);
  try {
    await new Promise<void>((resolve, reject) => {
      child.stdout.on('data', chunk => { if (String(chunk).includes('[planning] Routine completed')) resolve(); });
      child.once('exit', () => reject(new Error('Scheduler exited before delivery')));
      child.once('error', reject);
    });
    child.kill('SIGTERM');
    expect(await exited).toEqual({ code: 0, signal: null }); expect(stderr).toBe('');
    expect(requests).toHaveLength(1);
    expect(requests[0].messages[0].content).toContain('Use the trusted planning instruction.');
    expect(requests[0].messages.at(-1)?.content).toBe('Make a plan');
    await expect(exec(process.execPath, [entry, 'web'], { env })).rejects.toMatchObject({ code: 1 });
  } finally {
    clearTimeout(timeout); if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    await exited;
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(dir, { recursive: true, force: true });
  }
}, 15000);
