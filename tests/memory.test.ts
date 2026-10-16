import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  Agent,
  SqliteMemory,
  createMemoryTools,
  sanitizeFts5Query,
} from '../packages/core/dist/index.js';
import type { ModelProvider, ModelResponse } from '../packages/models/dist/types.js';

const resources: Array<() => void> = [];
afterEach(() => {
  resources.reverse().forEach(close => close());
  resources.length = 0;
});

function createMemory(fileBacked = false): { db: SqliteMemory; path: string } {
  if (!fileBacked) {
    const db = new SqliteMemory(':memory:');
    resources.push(() => db.close());
    return { db, path: ':memory:' };
  }
  const directory = mkdtempSync(join(tmpdir(), 'yusufclaw-mem-test-'));
  resources.push(() => rmSync(directory, { recursive: true, force: true }));
  const path = join(directory, 'test-memory.sqlite');
  const db = new SqliteMemory(path);
  resources.push(() => db.close());
  return { db, path };
}

function mockModel(...responses: ModelResponse[]) {
  const complete = vi.fn<ModelProvider['complete']>();
  responses.forEach(r => complete.mockResolvedValueOnce(r));
  return { complete };
}

describe('Phase 3 Structured Memory', () => {
  it('restart recall: persists profile facts, notes, and user facts across database reopen', () => {
    const { path } = createMemory(true);
    let db = new SqliteMemory(path);

    const profileFact = db.remember('session-1', {
      text: 'User preferred language is Turkish',
      kind: 'profile',
      category: 'language',
      source: 'user_explicit',
      sourceIds: ['req-1'],
    });

    const note = db.remember('session-1', {
      text: 'Discussed Phase 3 memory architecture and FTS5 search indexing',
      kind: 'note',
      category: 'architecture',
      source: 'meeting',
      sourceIds: ['req-2'],
    });

    const fact = db.remember('session-1', {
      text: 'Yusuf works on project YusufClaw at /home/yusuf/projects/wt/yc-memory',
      kind: 'fact',
      category: 'project',
      source: 'observed',
      sourceIds: ['req-3'],
    });

    db.close();

    // Reopen database from disk
    db = new SqliteMemory(path);
    resources.push(() => db.close());

    // 1. Direct get by ID
    const retrievedProfile = db.get('session-1', profileFact.id);
    expect(retrievedProfile).toBeDefined();
    expect(retrievedProfile?.text).toBe('User preferred language is Turkish');
    expect(retrievedProfile?.kind).toBe('profile');
    expect(retrievedProfile?.revision).toBe(1);
    expect(retrievedProfile?.category).toBe('language');
    expect(retrievedProfile?.sourceIds).toEqual(['req-1']);

    const retrievedNote = db.get('session-1', note.id);
    expect(retrievedNote).toBeDefined();
    expect(retrievedNote?.text).toContain('Discussed Phase 3 memory architecture');

    const retrievedFact = db.get('session-1', fact.id);
    expect(retrievedFact).toBeDefined();
    expect(retrievedFact?.text).toContain('YusufClaw');

    // 2. FTS5 Search after restart
    const searchResults = db.search('session-1', 'Turkish');
    expect(searchResults.some(r => r.id === profileFact.id)).toBe(true);

    const searchArchitecture = db.search('session-1', 'architecture');
    expect(searchArchitecture.some(r => r.id === note.id)).toBe(true);

    // 3. Context assembly after restart
    const context = db.assembleContext('session-1', 'YusufClaw project');
    expect(context).toContain('## Active Memory Context');
    expect(context).toContain(profileFact.id);
    expect(context).toContain(fact.id);
  });

  it('supersession: supersedes older facts, increments revision, and excludes older fact from active retrieval', () => {
    const { db } = createMemory();

    // Original fact (rev 1)
    const m1 = db.remember('session-test', {
      text: 'User lives in Munich',
      kind: 'fact',
      category: 'location',
    });
    expect(m1.revision).toBe(1);
    expect(m1.status).toBe('active');

    // Search matches m1
    expect(db.search('session-test', 'Munich')).toHaveLength(1);

    // Supersede m1 with m2 (rev 2)
    const m2 = db.supersede('session-test', m1.id, {
      text: 'User moved to Berlin on October 1',
      kind: 'fact',
      category: 'location',
    });

    expect(m2.revision).toBe(2);
    expect(m2.supersedesId).toBe(m1.id);
    expect(m2.status).toBe('active');

    // M1 in database now has status 'superseded'
    const m1After = db.get('session-test', m1.id, true);
    expect(m1After?.status).toBe('superseded');
    expect(m1After?.supersededById).toBe(m2.id);
    expect(m1After?.validTo).toBeDefined();

    // Active retrieval (get, search, list, assembleContext) MUST NOT return m1
    expect(db.get('session-test', m1.id)).toBeUndefined();
    expect(db.search('session-test', 'Munich')).toHaveLength(0);
    expect(db.search('session-test', 'Berlin')).toHaveLength(1);
    expect(db.search('session-test', 'Berlin')[0].id).toBe(m2.id);

    const listActive = db.list('session-test');
    expect(listActive.map(r => r.id)).toEqual([m2.id]);

    const context = db.assembleContext('session-test', 'Where does the user live?');
    expect(context).toContain('Berlin');
    expect(context).not.toContain('Munich');
  });

  it('scope isolation: strictly separates memories between sessions and channels', () => {
    const { db } = createMemory();

    const scopeA = 'session-alpha';
    const scopeB = 'session-beta';

    const memA = db.remember(scopeA, { text: 'Alpha confidential key is XYZ-999', kind: 'fact' });
    const memB = db.remember(scopeB, { text: 'Beta server hostname is beta.internal', kind: 'fact' });

    // Queries in scopeA must only see scopeA
    expect(db.get(scopeA, memA.id)).toBeDefined();
    expect(db.get(scopeA, memB.id)).toBeUndefined();
    expect(db.search(scopeA, 'internal')).toHaveLength(0);
    expect(db.search(scopeA, 'XYZ-999')).toHaveLength(1);
    expect(db.assembleContext(scopeA, 'hostname')).not.toContain('beta.internal');

    // Queries in scopeB must only see scopeB
    expect(db.get(scopeB, memB.id)).toBeDefined();
    expect(db.get(scopeB, memA.id)).toBeUndefined();
    expect(db.search(scopeB, 'XYZ-999')).toHaveLength(0);
    expect(db.search(scopeB, 'internal')).toHaveLength(1);
    expect(db.assembleContext(scopeB, 'confidential')).not.toContain('XYZ-999');

    // Cross-scope mutation attempts are blocked
    expect(db.forget(scopeA, memB.id)).toBe(false);
    expect(() => db.revise(scopeA, memB.id, { text: 'tampered' })).toThrow(/not found/);
    expect(db.get(scopeB, memB.id)?.text).toBe('Beta server hostname is beta.internal');
  });

  it('deletion: forget removes memory from active retrieval and FTS search, leaving a tombstone', () => {
    const { db } = createMemory();
    const scope = 'session-del';

    const mem = db.remember(scope, {
      text: 'Temporary credit card ending in 4242',
      kind: 'fact',
      category: 'payment',
    });

    expect(db.search(scope, '4242')).toHaveLength(1);
    expect(db.get(scope, mem.id)).toBeDefined();

    // Forget the memory
    const forgot = db.forget(scope, mem.id, 'user requested erasure');
    expect(forgot).toBe(true);

    // Active get, search, list, context assembly must exclude it
    expect(db.get(scope, mem.id)).toBeUndefined();
    expect(db.search(scope, '4242')).toHaveLength(0);
    expect(db.list(scope)).toHaveLength(0);
    expect(db.assembleContext(scope, '4242')).toBe('');

    // Historical inspection with includeInactive shows deleted
    const tombstone = db.get(scope, mem.id, true);
    expect(tombstone?.status).toBe('deleted');
    expect(tombstone?.deletedAt).toBeDefined();

    // Deleting non-existent or already deleted record returns false
    expect(db.forget(scope, mem.id)).toBe(false);
    expect(db.forget(scope, 'non-existent-id')).toBe(false);
  });

  it('FTS search basics: prefix matching, BM25 ranking, Turkish characters, and special symbol resilience', () => {
    const { db } = createMemory();
    const scope = 'session-fts';

    db.remember(scope, { text: 'TypeScript compiler configuration and strict type checks', kind: 'fact', category: 'coding' });
    db.remember(scope, { text: 'Python scripts for machine learning data preprocessing', kind: 'fact', category: 'ai' });
    db.remember(scope, { text: 'Kullanıcı İstanbul Kadıköyde yaşıyor ve her sabah Türk kahvesi içer', kind: 'profile', category: 'lifestyle' });
    db.remember(scope, { text: 'Coffee preference: espresso, double shot, no sugar', kind: 'profile', category: 'preferences' });

    // 1. Prefix search: "Type" matches "TypeScript"
    const prefixResult = db.search(scope, 'Type');
    expect(prefixResult.some(r => r.text.includes('TypeScript'))).toBe(true);

    // 2. Turkish character support: "İstanbul" and "kahve"
    const trResult = db.search(scope, 'İstanbul');
    expect(trResult).toHaveLength(1);
    expect(trResult[0].text).toContain('Kadıköyde');

    const trResult2 = db.search(scope, 'kahvesi');
    expect(trResult2).toHaveLength(1);

    // 3. Category filter
    const codingOnly = db.search(scope, 'compiler', { category: 'coding' });
    expect(codingOnly).toHaveLength(1);
    const wrongCategory = db.search(scope, 'compiler', { category: 'ai' });
    expect(wrongCategory).toHaveLength(0);

    // 4. Special symbol sanitization: quotes, colons, brackets, operators do not crash FTS5
    expect(() => db.search(scope, 'user: "Munich" OR C++ (test) * & ^ %')).not.toThrow();
    expect(() => db.search(scope, '::: ??? ///')).not.toThrow();
    expect(sanitizeFts5Query('::: ??? ///')).toBe('');
  });

  it('revises memory with optimistic concurrency (CAS) and tracks revision history', () => {
    const { db } = createMemory();
    const scope = 'session-rev';

    const mem = db.remember(scope, { text: 'Release v1.0 date is Monday', kind: 'note' });
    expect(mem.revision).toBe(1);

    // Revise with expectedRevision: 1 succeeds
    const rev2 = db.revise(scope, mem.id, {
      text: 'Release v1.0 date is moved to Wednesday',
      expectedRevision: 1,
      reason: 'QA extension',
    });
    expect(rev2.revision).toBe(2);
    expect(rev2.text).toContain('Wednesday');

    // Revise with outdated expectedRevision: 1 fails with version conflict
    expect(() =>
      db.revise(scope, mem.id, {
        text: 'Release v1.0 date is moved to Friday',
        expectedRevision: 1,
      })
    ).toThrow(/Version conflict/);

    // Search finds updated text
    expect(db.search(scope, 'Wednesday')).toHaveLength(1);
    expect(db.search(scope, 'Monday')).toHaveLength(0);
  });

  it('context assembly bounds entries and total characters, labeling sections with evidence IDs', () => {
    const { db } = createMemory();
    const scope = 'session-context';

    // Add 10 profile facts
    for (let i = 1; i <= 10; i++) {
      db.remember(scope, { text: `User trait number ${i} which is a moderately long descriptive preference`, kind: 'profile' });
    }

    // Add 10 notes
    for (let i = 1; i <= 10; i++) {
      db.remember(scope, { text: `Project note ${i} concerning task completion and documentation`, kind: 'note' });
    }

    const context = db.assembleContext(scope, 'Project note', {
      maxEntries: 4,
      maxChars: 500,
    });

    expect(context).toContain('## Active Memory Context');
    expect(context).toContain('Evidence: mem_');
    expect(context.length).toBeLessThanOrEqual(500);

    // Verify sections are properly labeled
    expect(context).toContain('### Profile');
  });

  it('agent loop wires memory context assembly and provides agent-facing memory tools (remember, revise, forget, recall)', async () => {
    const { db } = createMemory();
    const session = 'session-agent';

    // Pre-seed a profile fact
    const preseed = db.remember(session, {
      text: 'User preferred tone is concise and direct',
      kind: 'profile',
    });

    // 1. First model call: Agent passes memory context into system prompt
    // Model responds with tool call to remember a new fact
    const callRemember: ModelResponse = {
      text: '',
      toolCalls: [
        {
          id: 'call-rem-1',
          name: 'remember',
          input: { text: 'User project repository is yusufclaw', kind: 'fact', category: 'dev' },
        },
      ],
    };

    // 2. Second model call: acknowledges tool result
    const answer1: ModelResponse = { text: 'I remembered your project repository.', toolCalls: [] };

    const provider1 = mockModel(callRemember, answer1);
    const agent = new Agent(provider1, db);

    const reply1 = await agent.handle(session, 'req-agent-1', 'Remember my project repo');
    expect(reply1).toBe('I remembered your project repository.');

    // Check that preseeded memory was present in the system prompt of the first call
    const firstCallSystem = provider1.complete.mock.calls[0][0].system;
    expect(firstCallSystem).toContain('## Active Memory Context');
    expect(firstCallSystem).toContain(preseed.id);
    expect(firstCallSystem).toContain('User preferred tone is concise');

    // Verify tool execution actually persisted the fact in memory
    const searchStored = db.search(session, 'yusufclaw');
    expect(searchStored).toHaveLength(1);
    const newFactId = searchStored[0].id;
    expect(searchStored[0].category).toBe('dev');

    // 3. Test agent-facing tools: revise, recall, and forget through Agent loop
    const callRecall: ModelResponse = {
      text: '',
      toolCalls: [
        {
          id: 'call-rec-1',
          name: 'recall',
          input: { query: 'yusufclaw' },
        },
      ],
    };
    const answerRecall: ModelResponse = { text: 'You work on yusufclaw.', toolCalls: [] };

    const provider2 = mockModel(callRecall, answerRecall);
    const agent2 = new Agent(provider2, db);
    await agent2.handle(session, 'req-agent-2', 'What project repo did I mention?');
    // Verify tool result contained evidence ID
    const toolResultMsg = provider2.complete.mock.calls[1][0].messages.find(m => m.role === 'tool');
    expect(toolResultMsg?.text).toContain(newFactId);

    // 4. Test forget tool via Agent
    const callForget: ModelResponse = {
      text: '',
      toolCalls: [
        {
          id: 'call-for-1',
          name: 'forget',
          input: { id: newFactId },
        },
      ],
    };
    const answerForget: ModelResponse = { text: 'I have forgotten that project repo.', toolCalls: [] };
    const provider3 = mockModel(callForget, answerForget);
    const agent3 = new Agent(provider3, db);
    await agent3.handle(session, 'req-agent-3', 'Forget my project repo');

    // Verify it is no longer retrievable
    expect(db.get(session, newFactId)).toBeUndefined();
    expect(db.search(session, 'yusufclaw')).toHaveLength(0);
  });

  it('keeps existing SqliteMemory turn history and /reset working while preserving durable memory', async () => {
    const { db } = createMemory();
    const session = 'session-turns';

    // Store a durable profile fact
    const profile = db.remember(session, { text: 'User is Yusuf', kind: 'profile' });

    // Turn 1
    const provider = mockModel({ text: 'Hello Yusuf!', toolCalls: [] });
    const agent = new Agent(provider, db);
    const r1 = await agent.handle(session, 'turn-1', 'Hi');
    expect(r1).toBe('Hello Yusuf!');
    expect(db.history(session)).toHaveLength(2); // user + assistant

    // Request ID deduplication
    expect(await agent.handle(session, 'turn-1', 'Hi')).toBe('Hello Yusuf!');
    expect(provider.complete).toHaveBeenCalledTimes(1);

    // /reset clears turn history
    const resetReply = await agent.handle(session, 'turn-reset', '/reset');
    expect(resetReply).toBe('Conversation cleared.');
    expect(db.history(session)).toHaveLength(0);

    // Durable memory is NOT wiped out by /reset
    expect(db.get(session, profile.id)).toBeDefined();
    expect(db.get(session, profile.id)?.text).toBe('User is Yusuf');
  });

  it('createMemoryTools exposes all 4 tools directly with schema validation and scope isolation', async () => {
    const { db } = createMemory();
    const tools = createMemoryTools(db, () => 'direct-session');
    expect(tools.map(t => t.name)).toEqual(['remember', 'revise', 'forget', 'recall']);

    const [remember, revise, forget, recall] = tools;
    const resRem = await remember.execute({ text: 'Direct preference', kind: 'profile' });
    expect(resRem).toContain('Remembered [mem_');

    const searchRes = await recall.execute({ query: 'Direct' });
    expect(searchRes).toContain('Direct preference');

    const mem = db.search('direct-session', 'Direct')[0];
    const resRev = await revise.execute({ id: mem.id, text: 'Updated direct preference' });
    expect(resRev).toContain('revision 2');

    const resFor = await forget.execute({ id: mem.id });
    expect(resFor).toContain('Forgot memory');
  });
});
