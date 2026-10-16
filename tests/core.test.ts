import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Agent, SqliteMemory } from '../packages/core/dist/index.js';
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
