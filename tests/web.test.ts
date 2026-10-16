import { afterEach, expect, it, vi } from 'vitest';
import { Agent, SqliteMemory } from '../packages/core/dist/index.js';
import { MessageBus, WebAdapter, loadConfig, createRoutineScheduler } from '../packages/gateway/dist/index.js';
import type { ModelProvider } from '../packages/models/dist/index.js';
const cleanups: Array<() => Promise<void>> = [];
afterEach(async () => { await Promise.all(cleanups.splice(0).map(fn => fn())); });
async function setup() {
  const memory = new SqliteMemory(':memory:');
  const complete = vi.fn<ModelProvider['complete']>().mockResolvedValue({ text: '<script>hello</script>', toolCalls: [] });
  const agent = new Agent({ complete }, memory);
  const bus = new MessageBus(agent, { cli: new Set(['local']), telegram: new Set(), web: new Set(['local']) });
  const web = new WebAdapter(bus, memory, { token: 'fixture-token', userId: 'local', port: 0 }); await web.listen();
  cleanups.push(async () => { if (web.server.listening) await web.close(); memory.close(); });
  const address = web.server.address(); if (!address || typeof address === 'string') throw new Error();
  expect(address.address).toBe('127.0.0.1');
  const base = `http://127.0.0.1:${address.port}`;
  const headers = { authorization: 'Bearer fixture-token', 'content-type': 'application/json' };
  const post = (body: unknown, extra: Record<string, string> = {}) => fetch(`${base}/api/chat`, { method: 'POST', headers: { ...headers, ...extra }, body: JSON.stringify(body) });
  return { memory, complete, bus, web, base, headers, post };
}
const message = { text: 'hello', requestId: '1', conversationId: 'default' };
it('requires auth, denies cross-origin requests, validates input, and serves a token-free page', async () => {
  const { post, base, complete } = await setup();
  expect((await post(message, { authorization: '' })).status).toBe(401);
  expect((await post(message, { authorization: 'Bearer wrong' })).status).toBe(401);
  expect((await post(message, { origin: 'http://evil.example' })).status).toBe(403);
  expect((await post(message, { 'content-type': 'text/plain' })).status).toBe(415);
  for (const body of [{ ...message, userId: 'other' }, { ...message, text: '' }, { ...message, text: 'a'.repeat(16001) }, { text: 'hi' }]) expect((await post(body)).status).toBe(400);
  expect((await post({ ...message, text: 'a'.repeat(100001) })).status).toBe(413);
  expect(complete).not.toHaveBeenCalled();
  expect((await fetch(`${base}/api/routines`)).status).toBe(401);
  const page = await fetch(base); expect(await page.text()).not.toContain('fixture-token');
  expect(page.headers.get('content-security-policy')).toContain("frame-ancestors 'none'");
  const script = await (await fetch(`${base}/app.js`)).text(); expect(script).toContain('textContent'); expect(script).not.toMatch(/innerHTML|localStorage|sessionStorage/);
});
it('uses shared core history, isolates sessions/channels, reuses requests, resets and sanitizes errors', async () => {
  const { post, complete, bus } = await setup();
  expect(await (await post(message)).json()).toEqual({ reply: '<script>hello</script>' });
  await post(message); expect(complete).toHaveBeenCalledTimes(1);
  await post({ ...message, requestId: '2', text: 'follow up' }); expect(complete.mock.calls[1][0].messages).toHaveLength(3);
  await post({ ...message, conversationId: 'other' }); expect(complete.mock.calls[2][0].messages).toHaveLength(1);
  await bus.dispatch({ channel: 'cli', userId: 'local', ...message }); expect(complete.mock.calls[3][0].messages).toHaveLength(1);
  await post({ ...message, requestId: '3', text: '/reset' });
  await post({ ...message, requestId: '4' }); expect(complete.mock.calls[4][0].messages).toHaveLength(1);
  complete.mockRejectedValueOnce(new Error('SECRET')); const failure = await post({ ...message, requestId: '5' });
  expect(failure.status).toBe(502); expect(await failure.text()).not.toContain('SECRET');
});
it('delivers scheduled replies to the authenticated web inbox exactly once', async () => {
  const { memory, bus, web, base, headers } = await setup();
  const target = { channel: 'web' as const, userId: 'local', conversationId: 'planning' };
  const scheduler = createRoutineScheduler([{ id: 'plan', cron: '* * * * *', prompt: 'Plan today', target }], memory, bus, { cliUserId: 'local', telegramAllowed: new Set(), web, webUserId: 'local' });
  await scheduler.tick(new Date('2026-10-16T09:00:00Z')); await scheduler.tick(new Date('2026-10-16T09:00:30Z'));
  const response = await fetch(`${base}/api/routines`, { headers });
  expect(await response.json()).toEqual([{ requestId: expect.stringContaining('routine:plan:'), conversationId: 'planning', reply: '<script>hello</script>' }]);
  await expect(web.deliver({ ...target, userId: 'other' }, 'secret', '2')).rejects.toThrow('target');
});
it('cancels in-flight model work when the server closes', async () => {
  const { complete, post, web } = await setup();
  let started!: () => void; const ready = new Promise<void>(resolve => { started = resolve; });
  let signal: AbortSignal | undefined;
  complete.mockImplementationOnce(request => new Promise((_resolve, reject) => {
    signal = request.signal; started(); signal!.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }));
  const pending = post(message).catch(() => undefined); await ready; await web.close(); await pending;
  expect(signal?.aborted).toBe(true);
});
it('validates OpenAI env configuration and requires a web token', async () => {
  expect(() => loadConfig({ MODEL_PROVIDER: 'openai' })).toThrow('OPENAI_API_KEY');
  expect(loadConfig({ MODEL_PROVIDER: 'openai', OPENAI_API_KEY: 'key', OPENAI_MODEL: 'test' })).toMatchObject({ WEB_HOST: '127.0.0.1', WEB_PORT: 3000, OPENAI_BASE_URL: 'https://api.openai.com/v1' });
  const { bus, memory } = await setup(); expect(() => new WebAdapter(bus, memory, { userId: 'local', token: '' })).toThrow('WEB_AUTH_TOKEN');
});
