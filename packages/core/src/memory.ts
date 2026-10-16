import Database from 'better-sqlite3';
import { mkdirSync, chmodSync } from 'node:fs';
import { dirname } from 'node:path';
import type { Message } from '@yusufclaw/models';

export class SqliteMemory {
  private readonly db: Database.Database;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.db = new Database(path);
    if (path !== ':memory:') chmodSync(path, 0o600);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('busy_timeout = 5000');
    this.db.exec(`CREATE TABLE IF NOT EXISTS turns (
      id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
      request_id TEXT NOT NULL, messages TEXT NOT NULL, reply TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(session, request_id));
      CREATE INDEX IF NOT EXISTS turns_session ON turns(session, id);
      CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);`);
  }
  history(session: string, limit = 20): Message[] {
    const rows = this.db.prepare('SELECT messages FROM (SELECT id, messages FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?) ORDER BY id').all(session, limit) as { messages: string }[];
    return rows.flatMap(row => JSON.parse(row.messages) as Message[]);
  }
  reply(session: string, requestId: string): string | undefined {
    return (this.db.prepare('SELECT reply FROM turns WHERE session = ? AND request_id = ?').get(session, requestId) as { reply: string } | undefined)?.reply;
  }
  save(session: string, requestId: string, messages: Message[], reply: string): void {
    this.db.prepare('INSERT INTO turns(session, request_id, messages, reply) VALUES (?, ?, ?, ?)').run(session, requestId, JSON.stringify(messages), reply);
  }
  clear(session: string): void { this.db.prepare('DELETE FROM turns WHERE session = ?').run(session); }
  getState(key: string): string | undefined { return (this.db.prepare('SELECT value FROM state WHERE key = ?').get(key) as { value: string } | undefined)?.value; }
  setState(key: string, value: string): void { this.db.prepare('INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(key, value); }
  close(): void { this.db.close(); }
}
