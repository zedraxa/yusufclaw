import Database from 'better-sqlite3';
import { mkdirSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomBytes } from 'node:crypto';
import type { Message } from '@yusufclaw/models';

export type MemoryKind = 'profile' | 'fact' | 'note';
export type MemoryTrust = 'user_explicit' | 'observed' | 'model_inferred' | 'external';
export type MemoryStatus = 'active' | 'superseded' | 'deleted';

export interface MemoryRecord {
  id: string;
  scope: string;
  kind: MemoryKind;
  text: string;
  category?: string;
  source: string;
  sourceIds: string[];
  trust: MemoryTrust;
  createdAt: string;
  updatedAt: string;
  validFrom?: string;
  validTo?: string;
  supersedesId?: string;
  supersededById?: string;
  revision: number;
  status: MemoryStatus;
  deletedAt?: string;
}

export interface RememberInput {
  text: string;
  kind?: MemoryKind;
  category?: string;
  source?: string;
  sourceIds?: string[];
  trust?: MemoryTrust;
  validFrom?: string;
  validTo?: string;
  supersedesId?: string;
}

export interface ReviseInput {
  text: string;
  category?: string;
  expectedRevision?: number;
  source?: string;
  sourceIds?: string[];
  reason?: string;
}

export interface SearchOptions {
  kind?: MemoryKind;
  category?: string;
  limit?: number;
}

export interface ListOptions {
  kind?: MemoryKind;
  status?: MemoryStatus;
  limit?: number;
}

export interface ContextAssemblyOptions {
  maxEntries?: number;
  maxChars?: number;
  maxProfileEntries?: number;
  maxSearchEntries?: number;
}

export interface EmbeddingProvider {
  dimension: number;
  model: string;
  embed(text: string): Promise<number[]>;
}

export interface SqliteMemoryOptions {
  embeddingProvider?: EmbeddingProvider;
  enableEmbeddings?: boolean;
}

export function sanitizeFts5Query(query: string): string {
  const tokens = query.match(/[\p{L}\p{N}_]+/gu) || [];
  if (!tokens.length) return '';
  return tokens.map(t => `"${t.replace(/"/g, '')}"*`).join(' OR ');
}

interface RawMemoryRow {
  id: string;
  scope: string;
  kind: string;
  text: string;
  category: string | null;
  source: string;
  source_ids: string;
  trust: string;
  created_at: string;
  updated_at: string;
  valid_from: string | null;
  valid_to: string | null;
  supersedes_id: string | null;
  superseded_by_id: string | null;
  revision: number;
  status: string;
  deleted_at: string | null;
}

function toMemoryRecord(row: RawMemoryRow): MemoryRecord {
  let sourceIds: string[] = [];
  try {
    sourceIds = JSON.parse(row.source_ids || '[]');
  } catch {}
  return {
    id: row.id,
    scope: row.scope,
    kind: row.kind as MemoryKind,
    text: row.text,
    category: row.category ?? undefined,
    source: row.source,
    sourceIds,
    trust: row.trust as MemoryTrust,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    validFrom: row.valid_from ?? undefined,
    validTo: row.valid_to ?? undefined,
    supersedesId: row.supersedes_id ?? undefined,
    supersededById: row.superseded_by_id ?? undefined,
    revision: Number(row.revision),
    status: row.status as MemoryStatus,
    deletedAt: row.deleted_at ?? undefined,
  };
}

export class SqliteMemory {
  private readonly db: Database.Database;
  readonly options?: SqliteMemoryOptions;

  constructor(path: string, options?: SqliteMemoryOptions) {
    this.options = options;
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.db = new Database(path);
    if (path !== ':memory:') chmodSync(path, 0o600);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 5000');
    this.db.pragma('foreign_keys = ON');

    this.db.exec(`
      -- Turn history and state
      CREATE TABLE IF NOT EXISTS turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session TEXT NOT NULL,
        request_id TEXT NOT NULL,
        messages TEXT NOT NULL,
        reply TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(session, request_id)
      );
      CREATE INDEX IF NOT EXISTS turns_session ON turns(session, id);
      CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);

      -- Structured memory records: profile facts, episodic notes, user facts
      CREATE TABLE IF NOT EXISTS memory_records (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('profile', 'fact', 'note')),
        text TEXT NOT NULL,
        category TEXT,
        source TEXT NOT NULL DEFAULT 'user_explicit',
        source_ids TEXT NOT NULL DEFAULT '[]',
        trust TEXT NOT NULL DEFAULT 'user_explicit',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        valid_from TEXT,
        valid_to TEXT,
        supersedes_id TEXT,
        superseded_by_id TEXT,
        revision INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'deleted')),
        deleted_at TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_memory_scope_status ON memory_records(scope, status, kind);
      CREATE INDEX IF NOT EXISTS idx_memory_supersedes ON memory_records(supersedes_id);

      -- Revision history
      CREATE TABLE IF NOT EXISTS memory_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        text TEXT NOT NULL,
        category TEXT,
        updated_at TEXT NOT NULL,
        reason TEXT,
        source TEXT,
        source_ids TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY (record_id) REFERENCES memory_records(id)
      );

      -- Tombstones / Deletions log
      CREATE TABLE IF NOT EXISTS memory_deletions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        deleted_at TEXT NOT NULL,
        reason TEXT
      );

      -- SQLite FTS5 table for fast lexical retrieval
      CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
        record_id UNINDEXED,
        scope UNINDEXED,
        kind UNINDEXED,
        text,
        category
      );
    `);
  }

  // --- Turn history & state (Phase 1 & 2 methods preserved) ---

  history(session: string, limit = 20): Message[] {
    const rows = this.db.prepare(
      'SELECT messages FROM (SELECT id, messages FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?) ORDER BY id'
    ).all(session, limit) as { messages: string }[];
    return rows.flatMap(row => JSON.parse(row.messages) as Message[]);
  }

  reply(session: string, requestId: string): string | undefined {
    return (this.db.prepare('SELECT reply FROM turns WHERE session = ? AND request_id = ?').get(session, requestId) as { reply: string } | undefined)?.reply;
  }

  save(session: string, requestId: string, messages: Message[], reply: string): void {
    this.db.prepare('INSERT INTO turns(session, request_id, messages, reply) VALUES (?, ?, ?, ?)').run(session, requestId, JSON.stringify(messages), reply);
  }

  clear(session: string): void {
    this.db.prepare('DELETE FROM turns WHERE session = ?').run(session);
  }

  getState(key: string): string | undefined {
    return (this.db.prepare('SELECT value FROM state WHERE key = ?').get(key) as { value: string } | undefined)?.value;
  }

  setState(key: string, value: string): void {
    this.db.prepare('INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(key, value);
  }

  close(): void {
    this.db.close();
  }

  // --- Structured Memory (Phase 3 methods) ---

  remember(scope: string, input: RememberInput): MemoryRecord {
    const text = input.text.trim();
    if (!text) throw new Error('Memory text must not be empty');

    const id = `mem_${Date.now().toString(36)}_${randomBytes(4).toString('hex')}`;
    const kind = input.kind ?? 'fact';
    const now = new Date().toISOString();
    const source = input.source ?? 'user_explicit';
    const sourceIds = JSON.stringify(input.sourceIds ?? []);
    const trust = input.trust ?? 'user_explicit';
    const category = input.category?.trim() || null;
    const validFrom = input.validFrom ?? now;

    let revision = 1;
    let supersedesId: string | null = null;

    const txn = this.db.transaction(() => {
      if (input.supersedesId) {
        const targetId = input.supersedesId.trim();
        const oldRow = this.db.prepare(
          'SELECT * FROM memory_records WHERE id = ? AND scope = ?'
        ).get(targetId, scope) as RawMemoryRow | undefined;

        if (oldRow && oldRow.status === 'active') {
          supersedesId = targetId;
          revision = Number(oldRow.revision) + 1;
          this.db.prepare(`
            UPDATE memory_records
            SET status = 'superseded', superseded_by_id = ?, valid_to = ?, updated_at = ?
            WHERE id = ? AND scope = ?
          `).run(id, now, now, targetId, scope);

          this.db.prepare('DELETE FROM memory_fts WHERE record_id = ?').run(targetId);
        }
      }

      this.db.prepare(`
        INSERT INTO memory_records (
          id, scope, kind, text, category, source, source_ids, trust,
          created_at, updated_at, valid_from, valid_to, supersedes_id,
          superseded_by_id, revision, status, deleted_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, NULL, ?,
          NULL, ?, 'active', NULL
        )
      `).run(
        id, scope, kind, text, category, source, sourceIds, trust,
        now, now, validFrom, supersedesId, revision
      );

      this.db.prepare(`
        INSERT INTO memory_fts (record_id, scope, kind, text, category)
        VALUES (?, ?, ?, ?, ?)
      `).run(id, scope, kind, text, category ?? '');
    });

    txn();

    return this.get(scope, id, true)!;
  }

  revise(scope: string, id: string, input: ReviseInput): MemoryRecord {
    const text = input.text.trim();
    if (!text) throw new Error('Updated memory text must not be empty');

    const now = new Date().toISOString();
    const targetId = id.trim();

    const txn = this.db.transaction(() => {
      const oldRow = this.db.prepare(
        'SELECT * FROM memory_records WHERE id = ? AND scope = ?'
      ).get(targetId, scope) as RawMemoryRow | undefined;

      if (!oldRow || oldRow.status !== 'active') {
        throw new Error(`Memory record not found or not active: ${targetId}`);
      }

      if (input.expectedRevision !== undefined && Number(oldRow.revision) !== input.expectedRevision) {
        throw new Error(`Version conflict: expected revision ${input.expectedRevision}, but found revision ${oldRow.revision}`);
      }

      // Record old state in revisions history
      this.db.prepare(`
        INSERT INTO memory_revisions (
          record_id, revision, text, category, updated_at, reason, source, source_ids
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
      `).run(
        oldRow.id,
        oldRow.revision,
        oldRow.text,
        oldRow.category,
        oldRow.updated_at,
        input.reason ?? null,
        oldRow.source,
        oldRow.source_ids
      );

      const newCategory = input.category !== undefined ? (input.category?.trim() || null) : oldRow.category;
      const newSource = input.source ?? oldRow.source;
      const newSourceIds = input.sourceIds ? JSON.stringify(input.sourceIds) : oldRow.source_ids;
      const newRevision = Number(oldRow.revision) + 1;

      this.db.prepare(`
        UPDATE memory_records
        SET text = ?, category = ?, revision = ?, updated_at = ?, source = ?, source_ids = ?
        WHERE id = ? AND scope = ?
      `).run(text, newCategory, newRevision, now, newSource, newSourceIds, targetId, scope);

      // Update FTS
      this.db.prepare('DELETE FROM memory_fts WHERE record_id = ?').run(targetId);
      this.db.prepare(`
        INSERT INTO memory_fts (record_id, scope, kind, text, category)
        VALUES (?, ?, ?, ?, ?)
      `).run(targetId, scope, oldRow.kind, text, newCategory ?? '');
    });

    txn();

    return this.get(scope, targetId, true)!;
  }

  supersede(scope: string, oldId: string, input: RememberInput): MemoryRecord {
    return this.remember(scope, { ...input, supersedesId: oldId });
  }

  forget(scope: string, id: string, reason?: string): boolean {
    const targetId = id.trim();
    const now = new Date().toISOString();

    let deleted = false;
    const txn = this.db.transaction(() => {
      const row = this.db.prepare(
        'SELECT id, status FROM memory_records WHERE id = ? AND scope = ?'
      ).get(targetId, scope) as { id: string; status: string } | undefined;

      if (!row || row.status === 'deleted') {
        deleted = false;
        return;
      }

      this.db.prepare(`
        UPDATE memory_records
        SET status = 'deleted', deleted_at = ?, updated_at = ?
        WHERE id = ? AND scope = ?
      `).run(now, now, targetId, scope);

      this.db.prepare('DELETE FROM memory_fts WHERE record_id = ?').run(targetId);

      this.db.prepare(`
        INSERT INTO memory_deletions (record_id, scope, deleted_at, reason)
        VALUES (?, ?, ?, ?)
      `).run(targetId, scope, now, reason ?? null);

      deleted = true;
    });

    txn();
    return deleted;
  }

  get(scope: string, id: string, includeInactive = false): MemoryRecord | undefined {
    const query = includeInactive
      ? 'SELECT * FROM memory_records WHERE id = ? AND scope = ?'
      : "SELECT * FROM memory_records WHERE id = ? AND scope = ? AND status = 'active'";
    const row = this.db.prepare(query).get(id.trim(), scope) as RawMemoryRow | undefined;
    return row ? toMemoryRecord(row) : undefined;
  }

  search(scope: string, query: string, options: SearchOptions = {}): MemoryRecord[] {
    const limit = Math.max(1, Math.min(options.limit ?? 10, 100));
    const sanitized = sanitizeFts5Query(query);

    if (sanitized) {
      try {
        const rows = this.db.prepare(`
          SELECT m.*, f.rank
          FROM memory_records m
          JOIN memory_fts f ON m.id = f.record_id
          WHERE m.scope = ? AND m.status = 'active'
            AND (? IS NULL OR m.kind = ?)
            AND (? IS NULL OR m.category = ?)
            AND memory_fts MATCH ?
          ORDER BY f.rank
          LIMIT ?
        `).all(
          scope,
          options.kind ?? null, options.kind ?? null,
          options.category ?? null, options.category ?? null,
          sanitized,
          limit
        ) as RawMemoryRow[];

        return rows.map(toMemoryRecord);
      } catch {
        // Fallback below if FTS match encounters any unexpected driver issue
      }
    }

    // Fallback: list active records filtered by kind and category, ordered by recency
    const rows = this.db.prepare(`
      SELECT *
      FROM memory_records
      WHERE scope = ? AND status = 'active'
        AND (? IS NULL OR kind = ?)
        AND (? IS NULL OR category = ?)
      ORDER BY updated_at DESC
      LIMIT ?
    `).all(
      scope,
      options.kind ?? null, options.kind ?? null,
      options.category ?? null, options.category ?? null,
      limit
    ) as RawMemoryRow[];

    return rows.map(toMemoryRecord);
  }

  list(scope: string, options: ListOptions = {}): MemoryRecord[] {
    const status = options.status ?? 'active';
    const limit = Math.max(1, Math.min(options.limit ?? 50, 200));

    const rows = this.db.prepare(`
      SELECT *
      FROM memory_records
      WHERE scope = ? AND status = ?
        AND (? IS NULL OR kind = ?)
      ORDER BY updated_at DESC
      LIMIT ?
    `).all(
      scope,
      status,
      options.kind ?? null, options.kind ?? null,
      limit
    ) as RawMemoryRow[];

    return rows.map(toMemoryRecord);
  }

  getProfile(scope: string, limit = 10): MemoryRecord[] {
    return this.list(scope, { kind: 'profile', status: 'active', limit });
  }

  assembleContext(scope: string, query?: string, options: ContextAssemblyOptions = {}): string {
    const maxEntries = options.maxEntries ?? 10;
    const maxChars = options.maxChars ?? 2000;
    const maxProfileEntries = options.maxProfileEntries ?? 5;
    const maxSearchEntries = options.maxSearchEntries ?? 5;

    const profileRecords = this.getProfile(scope, maxProfileEntries);
    const seenIds = new Set<string>(profileRecords.map(r => r.id));

    let searchRecords: MemoryRecord[] = [];
    if (query && query.trim()) {
      const rawSearch = this.search(scope, query.trim(), { limit: maxSearchEntries });
      searchRecords = rawSearch.filter(r => !seenIds.has(r.id));
    }

    const totalCount = profileRecords.length + searchRecords.length;
    if (totalCount === 0) return '';

    const lines: string[] = [
      '## Active Memory Context',
      'The following memory records are retrieved from long-term storage for this session. Use their evidence IDs when referencing facts.',
    ];

    if (profileRecords.length > 0) {
      lines.push('### Profile');
      for (const r of profileRecords.slice(0, maxEntries)) {
        lines.push(`- [Evidence: ${r.id}] (${r.kind}, rev ${r.revision}${r.category ? `, cat: ${r.category}` : ''}): ${r.text}`);
      }
    }

    const remainingSlots = Math.max(0, maxEntries - profileRecords.length);
    if (searchRecords.length > 0 && remainingSlots > 0) {
      lines.push('### Relevant Facts & Notes');
      for (const r of searchRecords.slice(0, remainingSlots)) {
        const datePart = r.updatedAt ? `, updated ${r.updatedAt.slice(0, 10)}` : '';
        lines.push(`- [Evidence: ${r.id}] (${r.kind}, rev ${r.revision}${datePart}${r.category ? `, cat: ${r.category}` : ''}): ${r.text}`);
      }
    }

    let result = lines.join('\n');
    if (result.length > maxChars) {
      result = result.slice(0, maxChars - 3) + '...';
    }
    return result;
  }
}
