import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import type { SqliteMemory } from './memory.js';

export interface RoutineTarget { channel: 'cli' | 'telegram' | 'web'; userId: string; conversationId: string }
export interface Routine { id: string; cron: string; prompt: string; target: RoutineTarget }

/** Five numeric fields, UTC. Supports *, lists, inclusive ranges, and steps. */
export function parseCron(expression: string): (date: Date) => boolean {
  const fields = expression.trim().split(/\s+/);
  if (fields.length !== 5) throw new Error('Cron requires five fields');
  const bounds = [[0, 59], [0, 23], [1, 31], [1, 12], [0, 7]];
  const sets = fields.map((field, index) => {
    const [min, max] = bounds[index];
    const values = new Set<number>();
    for (const item of field.split(',')) {
      const match = /^(\*|\d+(?:-\d+)?)(?:\/(\d+))?$/.exec(item);
      if (!match) throw new Error('Invalid cron field');
      const [start, end] = match[1] === '*' ? [min, max] : match[1].includes('-') ? match[1].split('-').map(Number) : [Number(match[1]), match[2] ? max : Number(match[1])];
      const step = Number(match[2] ?? 1);
      if (start < min || end > max || start > end || !Number.isSafeInteger(step) || step < 1 || step > max + 1) throw new Error('Invalid cron range or step');
      for (let value = start; value <= end; value += step) values.add(index === 4 && value === 7 ? 0 : value);
    }
    return values;
  });
  return date => {
    const day = sets[2].has(date.getUTCDate());
    const weekday = sets[4].has(date.getUTCDay());
    // Standard cron: restricted day-of-month and day-of-week fields are ORed.
    const days = fields[2].startsWith('*') || fields[4].startsWith('*') ? day && weekday : day || weekday;
    return sets[0].has(date.getUTCMinutes()) && sets[1].has(date.getUTCHours()) && sets[3].has(date.getUTCMonth() + 1) && days;
  };
}

export async function loadRoutines(path: string): Promise<Routine[]> {
  try {
    const raw: unknown = JSON.parse(await readFile(path, 'utf8'));
    if (!Array.isArray(raw) || raw.length > 100) throw new Error();
    const ids = new Set<string>();
    return raw.map((value: unknown) => {
      if (!value || typeof value !== 'object') throw new Error();
      const routine = value as Routine;
      if (typeof routine.id !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(routine.id) || ids.has(routine.id)) throw new Error();
      ids.add(routine.id);
      if (typeof routine.cron !== 'string' || typeof routine.prompt !== 'string' || !routine.prompt.trim() || routine.prompt.length > 16000) throw new Error();
      parseCron(routine.cron);
      const target = routine.target;
      if (!target || !['cli', 'telegram', 'web'].includes(target.channel) || typeof target.userId !== 'string' || !target.userId.trim() || typeof target.conversationId !== 'string' || !target.conversationId.trim()) throw new Error();
      return { id: routine.id, cron: routine.cron, prompt: routine.prompt, target: { channel: target.channel, userId: target.userId, conversationId: target.conversationId } };
    });
  } catch { throw new Error('Invalid routines configuration'); }
}

export interface RoutineTransport {
  dispatch(message: RoutineTarget & { requestId: string; text: string }, signal?: AbortSignal): Promise<string>;
  deliver(target: RoutineTarget, reply: string, requestId: string, signal?: AbortSignal): Promise<void>;
}

/** One process per config/database. Missed minutes are skipped; failed deliveries retry within their minute. */
export class RoutineScheduler {
  private running?: Promise<void>;
  private readonly entries;
  constructor(routines: Routine[], private readonly memory: Pick<SqliteMemory, 'getState' | 'setState'>, private readonly transport: RoutineTransport, private readonly onError: () => void = () => {}) {
    this.entries = routines.map(routine => ({ routine, matches: parseCron(routine.cron) }));
  }
  tick(now = new Date(), signal?: AbortSignal): Promise<void> {
    if (this.running) return this.running;
    const task = this.execute(now, signal);
    this.running = task;
    void task.finally(() => { if (this.running === task) this.running = undefined; }).catch(() => {});
    return task;
  }
  private async execute(now: Date, signal?: AbortSignal): Promise<void> {
    const minute = Math.floor(now.getTime() / 60_000);
    if (!Number.isFinite(minute)) throw new Error('Invalid routine clock');
    for (const { routine, matches } of this.entries) {
      signal?.throwIfAborted();
      const key = `routine:${routine.id}:${JSON.stringify(routine.target)}`;
      if (!matches(now) || Number(this.memory.getState(key) ?? '-Infinity') >= minute) continue;
      const requestId = `routine:${routine.id}:${minute}`;
      try {
        const reply = await this.transport.dispatch({ ...routine.target, requestId, text: routine.prompt }, signal);
        signal?.throwIfAborted();
        await this.transport.deliver(routine.target, reply, requestId, signal);
        this.memory.setState(key, String(minute));
      } catch {
        signal?.throwIfAborted();
        this.onError();
      }
    }
  }
  async run(signal: AbortSignal): Promise<void> {
    while (!signal.aborted) {
      try {
        await this.tick(new Date(), signal);
        await delay(1000, undefined, { signal });
      } catch { if (!signal.aborted) throw new Error('Routine scheduler failed'); }
    }
  }
}
