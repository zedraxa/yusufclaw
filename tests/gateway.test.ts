import { afterEach, describe, expect, it, vi } from 'vitest';
import { Agent, SqliteMemory } from '../packages/core/dist/index.js';
import { MessageBus, loadConfig, TelegramAdapter, TelegramApi, TelegramApiError, splitTelegramText, type TelegramTransport, type TelegramUpdate } from '../packages/gateway/dist/index.js';
import type { ModelProvider } from '../packages/models/dist/index.js';
const databases: SqliteMemory[] = [];
afterEach(() => { databases.forEach(db => db.close()); databases.length = 0; });
function setup() {
  const memory = new SqliteMemory(':memory:'); databases.push(memory);
  const complete = vi.fn<ModelProvider['complete']>().mockResolvedValue({ text: 'hello', toolCalls: [] });
  const bus = new MessageBus(new Agent({ complete }, memory), { cli: new Set(['local']), telegram: new Set(['123']) });
  return { memory, bus, complete };
}
const update: TelegramUpdate = { update_id: 1, message: { text: 'Hi', from: { id: 123 }, chat: { id: 123, type: 'private' } } };
describe('configuration and bus', () => {
  it('defaults to named Bedrock profile and rejects invalid config without values', () => {
    expect(loadConfig({ BEDROCK_MODEL_ID: 'model' })).toMatchObject({ MODEL_PROVIDER: 'bedrock', AWS_PROFILE: 'yusufclaw', AWS_REGION: 'us-east-1' });
    expect(loadConfig({ MODEL_PROVIDER: 'ollama', BEDROCK_MODEL_ID: '' }).MODEL_PROVIDER).toBe('ollama');
    expect(() => loadConfig({})).toThrow('BEDROCK_MODEL_ID');
    expect(() => loadConfig({ MODEL_PROVIDER: 'ollama', MAX_AGENT_STEPS: '0' })).toThrow('MAX_AGENT_STEPS');
    expect(() => loadConfig({ MODEL_PROVIDER: 'ollama', TELEGRAM_ALLOWED_USER_IDS: 'anyone' })).toThrow('numeric');
    expect(() => loadConfig({ MODEL_PROVIDER: 'ollama', TELEGRAM_BOT_TOKEN: 'SECRET' })).toThrow(/^Invalid configuration: TELEGRAM_BOT_TOKEN$/);
  });
  it('denies unlisted senders and isolates channel/user/conversation history', async () => {
    const { bus, complete } = setup();
    await expect(bus.dispatch({ channel: 'telegram', userId: '999', conversationId: '1', requestId: '1', text: 'hi' })).rejects.toThrow('authorized');
    expect(complete).not.toHaveBeenCalled();
    await bus.dispatch({ channel: 'cli', userId: 'local', conversationId: '1', requestId: '1', text: 'secret' });
    await bus.dispatch({ channel: 'telegram', userId: '123', conversationId: '1', requestId: '1', text: 'hi' });
    expect(complete.mock.calls[1][0].messages).toEqual([{ role: 'user', text: 'hi' }]);
  });
});
describe('Telegram', () => {
  it('ignores unlisted users, groups, bots and non-text updates', async () => {
    const { bus, memory, complete } = setup(); const call = vi.fn<TelegramTransport['call']>();
    const adapter = new TelegramAdapter(bus, memory, { call }, new Set(['123']), 'token');
    await adapter.handle({ update_id: 1 });
    await adapter.handle({ ...update, message: { ...update.message!, from: { id: 999 } } });
    await adapter.handle({ ...update, message: { ...update.message!, from: { id: 123, is_bot: true } } });
    await adapter.handle({ ...update, message: { ...update.message!, chat: { id: 123, type: 'group' } } });
    expect(complete).not.toHaveBeenCalled(); expect(call).not.toHaveBeenCalled();
    expect(() => new TelegramAdapter(bus, memory, { call }, new Set(), 'token')).toThrow('empty');
  });
  it('reuses a saved reply if Telegram retries delivery', async () => {
    const { bus, memory, complete } = setup(); const call = vi.fn<TelegramTransport['call']>().mockRejectedValueOnce(new Error('network')).mockResolvedValue({});
    const adapter = new TelegramAdapter(bus, memory, { call }, new Set(['123']), 'token');
    await expect(adapter.handle(update)).rejects.toThrow('network');
    await adapter.handle(update); expect(complete).toHaveBeenCalledTimes(1);
    expect(call).toHaveBeenLastCalledWith('sendMessage', { chat_id: 123, text: 'hello' }, undefined);
  });
  it('saves offsets and resumes after restart', async () => {
    const { bus, memory } = setup(); const controller = new AbortController();
    const call = vi.fn<TelegramTransport['call']>();
    call.mockImplementation(async (method: string): Promise<unknown> => {
      if (method === 'getUpdates') {
        if (call.mock.calls.filter(c => c[0] === 'getUpdates').length > 1) { controller.abort(); return []; }
        return [update];
      }
      return {};
    });
    await new TelegramAdapter(bus, memory, { call }, new Set(['123']), 'token').run(controller.signal);
    expect(call.mock.calls.filter(c => c[0] === 'getUpdates')[1][1].offset).toBe(2);
    const again = new AbortController();
    const resumed = vi.fn<TelegramTransport['call']>().mockImplementation(async (): Promise<unknown> => { again.abort(); return []; });
    await new TelegramAdapter(bus, memory, { call: resumed }, new Set(['123']), 'token').run(again.signal);
    expect(resumed.mock.calls[0][1].offset).toBe(2);
  });
  it('fails fast on polling conflict and sanitizes provider errors', async () => {
    const { bus, memory, complete } = setup();
    const call = vi.fn<TelegramTransport['call']>().mockRejectedValueOnce(new TelegramApiError(409)).mockResolvedValue({});
    const adapter = new TelegramAdapter(bus, memory, { call }, new Set(['123']), 'token');
    await expect(adapter.run(new AbortController().signal)).rejects.toThrow('409');
    complete.mockRejectedValueOnce(new Error('SECRET'));
    await adapter.handle(update);
    expect(call.mock.calls.at(-1)?.[1].text).not.toContain('SECRET');
  });
  it('retries transient polling failures and exits promptly on cancellation', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { bus, memory } = setup(); const controller = new AbortController();
    const call = vi.fn<TelegramTransport['call']>().mockRejectedValueOnce(new TelegramApiError(429, 7))
      .mockImplementationOnce(async () => { controller.abort(); return []; });
    const wait = vi.fn(async () => {});
    try {
      await new TelegramAdapter(bus, memory, { call }, new Set(['123']), 'token', wait).run(controller.signal);
      expect(wait).toHaveBeenCalledExactlyOnceWith(7000, controller.signal);
      expect(call).toHaveBeenCalledTimes(2);
    } finally { log.mockRestore(); }
  });
  it('splits long replies without breaking emoji', () => {
    const text = 'a'.repeat(3999) + '😀'.repeat(5000);
    const chunks = splitTelegramText(text);
    expect(chunks.join('')).toBe(text); expect(chunks.every(chunk => chunk.length <= 4000)).toBe(true);
    expect(chunks.every(chunk => !/^[\uDC00-\uDFFF]/u.test(chunk))).toBe(true);
  });
  it('sanitizes token-bearing network failures and respects API errors', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValueOnce(new Error('https://api.telegram.org/botSECRET'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, error_code: 429, parameters: { retry_after: 7 } }), { status: 429 }));
    const api = new TelegramApi('SECRET', fetcher);
    await expect(api.call('getUpdates', {})).rejects.toThrow(/^Telegram network request failed$/);
    await expect(api.call('getUpdates', {})).rejects.toMatchObject({ code: 429, retryAfter: 7 });
  });
});
