import type { AgentTool } from '@yusufclaw/tools';
import type { SqliteMemory, MemoryKind } from './memory.js';
import { activeSessionContext } from './session-context.js';

export function createMemoryTools(memory: SqliteMemory, getSession?: () => string): AgentTool[] {
  const resolveScope = (inputScope?: unknown): string => {
    if (typeof inputScope === 'string' && inputScope.trim()) return inputScope.trim();
    const active = activeSessionContext.getStore()?.session;
    if (active) return active;
    if (getSession) return getSession();
    return 'default';
  };

  const rememberTool: AgentTool = {
    name: 'remember',
    description: 'Store a durable fact, profile preference, or episodic note into long-term memory with provenance and an evidence ID.',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        text: { type: 'string', description: 'The fact, preference, or note to store in memory.' },
        kind: {
          type: 'string',
          enum: ['profile', 'fact', 'note'],
          description: "Memory kind: 'profile' (enduring user traits/preferences), 'fact' (user or project facts), or 'note' (episodic notes/observations). Default is 'fact'.",
        },
        category: { type: 'string', description: 'Optional category tag (e.g. "preferences", "work", "contacts").' },
        supersedesId: { type: 'string', description: 'Optional evidence ID of an existing memory record that this new fact replaces/supersedes.' },
        scope: { type: 'string', description: 'Optional explicit scope identifier. Defaults to active session.' },
      },
      required: ['text'],
      additionalProperties: false,
    },
    async execute(input: Record<string, unknown>): Promise<string> {
      const text = String(input.text ?? '').trim();
      if (!text) throw new Error('Memory text must not be empty');
      const scope = resolveScope(input.scope);
      const active = activeSessionContext.getStore();
      const record = memory.remember(scope, {
        text,
        kind: (input.kind as MemoryKind) ?? 'fact',
        category: typeof input.category === 'string' ? input.category.trim() : undefined,
        supersedesId: typeof input.supersedesId === 'string' ? input.supersedesId.trim() : undefined,
        source: 'agent_tool',
        sourceIds: active?.requestId ? [active.requestId] : [],
        trust: 'observed',
      });
      const supersededPart = input.supersedesId ? ` (superseded ${input.supersedesId})` : '';
      return `Remembered [${record.id}] (${record.kind}, rev ${record.revision}): "${record.text}". Evidence ID: ${record.id}${supersededPart}.`;
    },
  };

  const reviseTool: AgentTool = {
    name: 'revise',
    description: 'Revise or update an existing memory record by evidence ID, incrementing its revision and preserving historical revisions.',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Evidence ID of the memory to revise (e.g. "mem_...").' },
        text: { type: 'string', description: 'The updated fact or note text.' },
        category: { type: 'string', description: 'Optional updated category tag.' },
        expectedRevision: { type: 'integer', description: 'Optional expected current revision for optimistic concurrency check.' },
        scope: { type: 'string', description: 'Optional explicit scope identifier. Defaults to active session.' },
      },
      required: ['id', 'text'],
      additionalProperties: false,
    },
    async execute(input: Record<string, unknown>): Promise<string> {
      const id = String(input.id ?? '').trim();
      const text = String(input.text ?? '').trim();
      if (!id) throw new Error('Memory ID must not be empty');
      if (!text) throw new Error('Updated text must not be empty');
      const scope = resolveScope(input.scope);
      const active = activeSessionContext.getStore();
      const record = memory.revise(scope, id, {
        text,
        category: typeof input.category === 'string' ? input.category.trim() : undefined,
        expectedRevision: typeof input.expectedRevision === 'number' ? input.expectedRevision : undefined,
        source: 'agent_tool',
        sourceIds: active?.requestId ? [active.requestId] : [],
      });
      return `Revised memory [${record.id}] to revision ${record.revision}: "${record.text}".`;
    },
  };

  const forgetTool: AgentTool = {
    name: 'forget',
    description: 'Remove a memory record by evidence ID from active retrieval and search (soft delete with tombstone).',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        id: { type: 'string', description: 'Evidence ID of the memory to forget (e.g. "mem_...").' },
        reason: { type: 'string', description: 'Optional reason for forgetting.' },
        scope: { type: 'string', description: 'Optional explicit scope identifier. Defaults to active session.' },
      },
      required: ['id'],
      additionalProperties: false,
    },
    async execute(input: Record<string, unknown>): Promise<string> {
      const id = String(input.id ?? '').trim();
      if (!id) throw new Error('Memory ID must not be empty');
      const scope = resolveScope(input.scope);
      const deleted = memory.forget(scope, id, typeof input.reason === 'string' ? input.reason : undefined);
      if (!deleted) return `Memory record [${id}] not found in this scope.`;
      return `Forgot memory [${id}]. It will no longer be retrieved.`;
    },
  };

  const recallTool: AgentTool = {
    name: 'recall',
    description: 'Search long-term structured memory by keyword or semantic query using FTS5, scoped to this session.',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Keywords or search phrase to look up in memory.' },
        kind: { type: 'string', enum: ['profile', 'fact', 'note'], description: 'Optional filter by memory kind.' },
        limit: { type: 'integer', description: 'Maximum number of memories to return (default 5, max 20).' },
        scope: { type: 'string', description: 'Optional explicit scope identifier. Defaults to active session.' },
      },
      required: ['query'],
      additionalProperties: false,
    },
    async execute(input: Record<string, unknown>): Promise<string> {
      const query = String(input.query ?? '').trim();
      if (!query) throw new Error('Query must not be empty');
      const scope = resolveScope(input.scope);
      const limit = typeof input.limit === 'number' ? Math.min(Math.max(1, input.limit), 20) : 5;
      const results = memory.search(scope, query, {
        kind: input.kind as MemoryKind | undefined,
        limit,
      });
      if (!results.length) return `No memories found matching "${query}".`;
      const lines = results.map(r => `- [Evidence: ${r.id}] (${r.kind}, rev ${r.revision}${r.category ? `, cat: ${r.category}` : ''}): ${r.text}`);
      return `Found ${results.length} memories:\n${lines.join('\n')}`;
    },
  };

  return [rememberTool, reviseTool, forgetTool, recallTool];
}
