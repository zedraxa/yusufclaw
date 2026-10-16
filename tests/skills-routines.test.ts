import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, expect, it, vi } from 'vitest';
import { Agent, SqliteMemory, loadSkills, loadRoutines, parseCron, RoutineScheduler, type Routine } from '../packages/core/dist/index.js';
import { MessageBus, createRoutineScheduler } from '../packages/gateway/dist/index.js';
import type { ModelProvider } from '../packages/models/dist/index.js';
const dirs: string[] = []; const dbs: SqliteMemory[] = [];
afterEach(async () => { dbs.splice(0).forEach(db => db.close()); await Promise.all(dirs.splice(0).map(dir => rm(dir, { recursive: true, force: true }))); });
async function directory() { const path = await mkdtemp(join(tmpdir(), 'yc-skills-')); dirs.push(path); return path; }
function database(path = ':memory:') { const db = new SqliteMemory(path); dbs.push(db); return db; }
const routine: Routine = { id: 'morning', cron: '0 9 * * 1-5', prompt: 'Plan today', target: { channel: 'cli', userId: 'local', conversationId: 'planning' } };
it('loads only sorted trusted Markdown and injects it alongside the system prompt', async () => {
  const dir = await directory(); await mkdir(join(dir, 'nested'));
  await writeFile(join(dir, 'b.md'), 'Second instruction');
  await writeFile(join(dir, 'a.md'), 'First instruction');
  await writeFile(join(dir, 'nested', 'SKILL.md'), 'Nested instruction');
  await writeFile(join(dir, 'script.js'), 'throw new Error("never execute")');
  await writeFile(join(dir, 'README.md'), 'Not instructions');
  await writeFile(join(dir, '.hidden.md'), 'Hidden');
  await symlink(join(dir, 'a.md'), join(dir, 'link.md'));
  const skills = await loadSkills(dir);
  expect(skills).toContain('First instruction'); expect(skills).toContain('Nested instruction');
  expect(skills.indexOf('First')).toBeLessThan(skills.indexOf('Second'));
  expect(skills).not.toMatch(/never execute|Not instructions|Hidden|link.md/);
  const complete = vi.fn<ModelProvider['complete']>().mockResolvedValue({ text: 'done', toolCalls: [] });
  await new Agent({ complete }, database(), [], { system: 'Base prompt', skills }).handle('s', '1', 'hello');
  expect(complete.mock.calls[0][0].system).toBe(`Base prompt\n\n${skills}`);
  expect(await loadSkills(join(dir, 'absent'))).toBe('');
  await writeFile(join(dir, 'huge.md'), 'a'.repeat(32769));
  await expect(loadSkills(dir)).rejects.toThrow('size limit');
});
it('validates routine files without exposing prompts or config contents', async () => {
  const file = join(await directory(), 'routines.json');
  await writeFile(file, JSON.stringify([routine])); expect(await loadRoutines(file)).toEqual([routine]);
  for (const content of ['SECRET', JSON.stringify([routine, routine]), JSON.stringify([{ ...routine, cron: 'bad SECRET' }]), JSON.stringify([{ ...routine, target: { channel: 'email' } }])]) {
    await writeFile(file, content); await expect(loadRoutines(file)).rejects.toThrow(/^Invalid routines configuration$/);
  }
});
it('matches UTC cron lists/ranges/steps, Sunday alias and day OR semantics', () => {
  expect(parseCron('*/15 9-17 * * 1-5')(new Date('2026-10-16T09:30:00Z'))).toBe(true);
  expect(parseCron('0,30 9 * * *')(new Date('2026-10-16T09:15:00Z'))).toBe(false);
  expect(parseCron('0 0 1 * 0')(new Date('2026-10-18T00:00:00Z'))).toBe(true);
  expect(parseCron('0 0 * * 7')(new Date('2026-10-18T00:00:00Z'))).toBe(true);
  expect(parseCron('0 0 1 * 0')(new Date('2026-10-16T00:00:00Z'))).toBe(false);
  for (const cron of ['* * * *', '60 * * * *', '*/0 * * * *', '* 22-2 * * *', '* * * 13 *', '* * * * 8', '@daily']) expect(() => parseCron(cron)).toThrow();
});
it('dispatches through the bus, retries delivery with saved model reply, and persists deduplication', async () => {
  const file = join(await directory(), 'memory.sqlite'); const memory = database(file);
  const complete = vi.fn<ModelProvider['complete']>().mockResolvedValue({ text: 'Plan ready', toolCalls: [] });
  const bus = new MessageBus(new Agent({ complete }, memory), { cli: new Set(['local']), telegram: new Set() });
  const deliver = vi.fn().mockRejectedValueOnce(new Error('failed')).mockResolvedValue(undefined);
  const dispatch = (message: Parameters<MessageBus['dispatch']>[0], signal?: AbortSignal) => bus.dispatch(message, undefined, signal);
  const scheduler = new RoutineScheduler([routine], memory, { dispatch, deliver });
  const now = new Date('2026-10-16T09:00:00Z');
  await scheduler.tick(new Date('2026-10-16T08:59:00Z')); expect(complete).not.toHaveBeenCalled();
  await scheduler.tick(now); await scheduler.tick(now); await scheduler.tick(now);
  expect(complete).toHaveBeenCalledTimes(1); expect(deliver).toHaveBeenCalledTimes(2);
  expect(deliver.mock.calls[1][0]).toEqual(routine.target);
  expect(deliver.mock.calls[1][1]).toBe('Plan ready');
  const reopened = database(file);
  await new RoutineScheduler([routine], reopened, { dispatch, deliver }).tick(now);
  expect(deliver).toHaveBeenCalledTimes(2);
  await scheduler.tick(new Date('2026-10-19T09:00:00Z'));
  expect(complete).toHaveBeenCalledTimes(2);
});
it('prevents overlapping ticks and cancels before delivery', async () => {
  let finish!: (value: string) => void;
  const dispatch = vi.fn(() => new Promise<string>(resolve => { finish = resolve; })); const deliver = vi.fn();
  const scheduler = new RoutineScheduler([routine], database(), { dispatch, deliver });
  const controller = new AbortController(); const now = new Date('2026-10-16T09:00:00Z');
  const first = scheduler.tick(now, controller.signal); const second = scheduler.tick(now, controller.signal);
  expect(first).toBe(second); expect(dispatch).toHaveBeenCalledTimes(1);
  controller.abort(); finish('result'); await expect(first).rejects.toThrow(); expect(deliver).not.toHaveBeenCalled();
});
it('validates configured delivery identities and delivers CLI/Telegram via shared core', async () => {
  const memory = database(); const complete = vi.fn<ModelProvider['complete']>().mockResolvedValue({ text: 'Scheduled reply', toolCalls: [] });
  const bus = new MessageBus(new Agent({ complete }, memory), { cli: new Set(['local']), telegram: new Set(['123']) });
  const output = vi.fn(); const call = vi.fn().mockResolvedValue({});
  const options = { cliUserId: 'local', telegramAllowed: new Set(['123']), telegram: { call }, webUserId: 'local', output };
  const tg: Routine = { ...routine, id: 'telegram', target: { channel: 'telegram', userId: '123', conversationId: '123' } };
  await createRoutineScheduler([routine, tg], memory, bus, options).tick(new Date('2026-10-16T09:00:00Z'));
  expect(output).toHaveBeenCalledWith('[planning] Scheduled reply');
  expect(call).toHaveBeenCalledWith('sendMessage', { chat_id: '123', text: 'Scheduled reply' }, undefined);
  for (const target of [{ channel: 'telegram' as const, userId: '123', conversationId: '-999' }, { channel: 'web' as const, userId: 'local', conversationId: 'default' }, { channel: 'cli' as const, userId: 'other', conversationId: 'x' }]) {
    expect(() => createRoutineScheduler([{ ...routine, target }], memory, bus, options)).toThrow('unauthorized');
  }
});
it('stops the scheduler loop on cancellation', async () => {
  const scheduler = new RoutineScheduler([], database(), { dispatch: vi.fn(), deliver: vi.fn() });
  const controller = new AbortController(); const running = scheduler.run(controller.signal); controller.abort(); await running;
});
