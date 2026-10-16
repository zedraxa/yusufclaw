import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { expect, it } from 'vitest';
const exec = promisify(execFile);
it('runs the compiled CLI through an HTTP tool round trip and restores SQLite history', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'yusufclaw-cli-'));
  const requests: Array<{ messages: Array<{ role: string; content: string }> }> = [];
  const server = createServer(async (request, response) => {
    let body = '';
    for await (const chunk of request) body += chunk;
    const data = JSON.parse(body) as typeof requests[number]; requests.push(data);
    const first = requests.length === 1;
    response.writeHead(200, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ done: true, message: {
      role: 'assistant', content: first ? '' : 'The clock was checked.',
      ...(first ? { tool_calls: [{ function: { name: 'current_time', arguments: {} } }] } : {}),
    } }));
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('No HTTP address');
  const env = { ...process.env, MODEL_PROVIDER: 'ollama', BEDROCK_MODEL_ID: '', OLLAMA_BASE_URL: `http://127.0.0.1:${address.port}`, MEMORY_PATH: join(directory, 'memory.sqlite'), TELEGRAM_BOT_TOKEN: undefined, TELEGRAM_ALLOWED_USER_IDS: '', DOTENV_CONFIG_PATH: join(directory, 'absent.env') };
  const entry = resolve('packages/gateway/dist/main.js');
  try {
    const first = await exec(process.execPath, [entry, 'cli', '--message', 'What time is it?'], { env });
    expect(first.stdout.trim()).toBe('The clock was checked.');
    expect(requests[1].messages.at(-1)).toMatchObject({ role: 'tool' });
    await exec(process.execPath, [entry, 'cli', '--message', 'What did I ask?'], { env });
    expect(requests[2].messages.some(m => m.content === 'What time is it?')).toBe(true);
    await exec(process.execPath, [entry, 'cli', '--message', '/reset'], { env });
    await exec(process.execPath, [entry, 'cli', '--message', 'Fresh conversation'], { env });
    expect(requests[3].messages).toHaveLength(2);
  } finally {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    rmSync(directory, { recursive: true, force: true });
  }
}, 15000);
