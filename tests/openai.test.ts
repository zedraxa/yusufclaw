import { createServer, type Server } from 'node:http';
import { afterEach, expect, it } from 'vitest';
import { OpenAIProvider, type ModelRequest } from '../packages/models/dist/index.js';
import { Agent, SqliteMemory } from '../packages/core/dist/index.js';
import { currentTime } from '../packages/tools/dist/index.js';
const servers: Server[] = [];
afterEach(async () => { await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); }))); });
const request: ModelRequest = { system: 'system', messages: [{ role: 'user', text: 'hi' }], tools: [] };
async function fixture(handler: (body: Record<string, any>, response: import('node:http').ServerResponse, request: import('node:http').IncomingMessage) => void) {
  const server = createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    handler(JSON.parse(body), res, req);
  }); servers.push(server);
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); if (!address || typeof address === 'string') throw new Error();
  return `http://127.0.0.1:${address.port}/proxy/v1/`;
}
it('completes a real HTTP tool round trip with history, IDs, schema, auth and base path', async () => {
  const requests: Record<string, any>[] = [];
  const urls: string[] = []; const auth: (string | undefined)[] = [];
  const baseUrl = await fixture((body, res, req) => {
    requests.push(body); urls.push(req.url!); auth.push(req.headers.authorization);
    res.setHeader('content-type', 'application/json');
    res.end(JSON.stringify({ choices: [{ finish_reason: requests.length === 1 ? 'tool_calls' : 'stop', message: { role: 'assistant', content: requests.length === 1 ? null : 'Checked.',
      ...(requests.length === 1 ? { tool_calls: [{ id: 'clock-1', type: 'function', function: { name: 'current_time', arguments: '{}' } }] } : {}),
    } }] }));
  });
  const memory = new SqliteMemory(':memory:');
  try {
    const agent = new Agent(new OpenAIProvider({ baseUrl, apiKey: 'fixture-key', model: 'fixture-model', maxTokens: 123 }), memory, [currentTime]);
    expect(await agent.handle('session', '1', 'Time?')).toBe('Checked.');
    await agent.handle('session', '2', 'Again?');
    expect(urls).toEqual(Array(3).fill('/proxy/v1/chat/completions'));
    expect(auth).toEqual(Array(3).fill('Bearer fixture-key'));
    expect(requests[0]).toMatchObject({ model: 'fixture-model', stream: false, max_tokens: 123 });
    expect(requests[0].tools[0].function.parameters).toEqual(currentTime.inputSchema);
    expect(requests[1].messages.at(-2).tool_calls[0]).toMatchObject({ id: 'clock-1', function: { arguments: '{}' } });
    expect(requests[1].messages.at(-1)).toMatchObject({ role: 'tool', tool_call_id: 'clock-1' });
    expect(requests[2].messages.some((m: any) => m.content === 'Time?')).toBe(true);
  } finally { memory.close(); }
});
it.each([
  { choices: [] },
  { choices: [{ finish_reason: 'length', message: { role: 'assistant', content: 'partial' } }] },
  { choices: [{ finish_reason: 'content_filter', message: { role: 'assistant', content: 'partial' } }] },
  { choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: '' } }] },
  ...['not-json-SECRET', '[]', 'null', '"SECRET"'].map(arguments_ => ({ choices: [{ finish_reason: 'tool_calls', message: { role: 'assistant', content: null, tool_calls: [{ id: '1', type: 'function', function: { name: 'tool', arguments: arguments_ } }] } }] })),
])('rejects malformed/incomplete output without leaking payloads', async payload => {
  const baseUrl = await fixture((_body, res) => res.end(JSON.stringify(payload)));
  await expect(new OpenAIProvider({ baseUrl, apiKey: 'key', model: 'model' }).complete(request)).rejects.toThrow(/^Invalid or incomplete OpenAI-compatible response$/);
});
it('handles HTTP errors, malformed JSON, timeouts, cancellation and no-tool payloads', async () => {
  let mode = 'http'; let sawTools = false;
  const baseUrl = await fixture((body, res) => {
    sawTools = 'tools' in body;
    if (mode === 'http') { res.writeHead(401); res.end('SECRET'); }
    else if (mode === 'json') res.end('SECRET');
  });
  const provider = new OpenAIProvider({ baseUrl, apiKey: 'key', model: 'model', timeoutMs: 50 });
  await expect(provider.complete(request)).rejects.toThrow('HTTP 401'); expect(sawTools).toBe(false);
  mode = 'json'; await expect(provider.complete(request)).rejects.toThrow('Invalid or incomplete');
  mode = 'hang'; await expect(provider.complete(request)).rejects.toThrow('timed out');
  await expect(provider.complete({ ...request, signal: AbortSignal.abort() })).rejects.toThrow('cancelled');
});
it('rejects unsafe endpoints and missing configuration', () => {
  for (const baseUrl of ['file:///tmp', 'https://user:pass@example.com', 'https://example.com?key=SECRET']) {
    expect(() => new OpenAIProvider({ baseUrl, apiKey: 'key', model: 'model' })).toThrow('configuration');
  }
  expect(() => new OpenAIProvider({ baseUrl: 'http://localhost', apiKey: '', model: 'model' })).toThrow('configuration');
});
