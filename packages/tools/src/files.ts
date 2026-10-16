import { existsSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, normalize, relative, resolve } from 'node:path';
import type { AgentTool } from './index.js';

export interface FileToolsOptions {
  workspaceRoot?: string;
  maxReadBytes?: number;
  maxListEntries?: number;
}

/**
 * Confines a path strictly to workspaceRoot, preventing any path traversal,
 * symlink escape, or null byte injections.
 */
export function resolveSafePath(workspaceRoot: string, userPath: string): string {
  if (typeof userPath !== 'string' || !userPath.trim()) {
    throw new Error('Path must be a non-empty string');
  }
  if (userPath.includes('\0')) {
    throw new Error('Invalid path: null bytes are forbidden');
  }

  const trimmed = userPath.trim();
  const normalized = normalize(trimmed);
  const resolved = isAbsolute(normalized) ? resolve(normalized) : resolve(workspaceRoot, normalized);

  const rel = relative(workspaceRoot, resolved);
  if (rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error(`Path escape detected: "${userPath}" is outside workspace root`);
  }

  // If path already exists, verify its realpath to catch symlinks pointing outside
  if (existsSync(resolved)) {
    const real = realpathSync(resolved);
    const realRel = relative(workspaceRoot, real);
    if (realRel.startsWith('..') || isAbsolute(realRel)) {
      throw new Error(`Path escape detected: symlink "${userPath}" resolves outside workspace root`);
    }
    return real;
  }

  // If file doesn't exist yet, verify closest existing ancestor directory for symlink escape
  let ancestor = dirname(resolved);
  while (ancestor.length >= workspaceRoot.length) {
    if (existsSync(ancestor)) {
      const realAncestor = realpathSync(ancestor);
      const realRel = relative(workspaceRoot, realAncestor);
      if (realRel.startsWith('..') || isAbsolute(realRel)) {
        throw new Error(`Path escape detected: parent directory symlink resolves outside workspace root`);
      }
      break;
    }
    ancestor = dirname(ancestor);
  }

  return resolved;
}

export function createFileReadTool(options: FileToolsOptions = {}): AgentTool {
  const root = resolve(process.cwd(), options.workspaceRoot || process.env.WORKSPACE_DIR || '.');
  const defaultMaxBytes = options.maxReadBytes ?? 64_000;

  return {
    name: 'file_read',
    description: 'Read the contents of a file within the workspace root.',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Path to the file relative to the workspace root.' },
        encoding: {
          type: 'string',
          enum: ['utf-8', 'utf8', 'ascii', 'base64', 'hex'],
          description: 'Encoding to read the file with (default utf-8).'
        },
        maxBytes: { type: 'number', description: 'Maximum bytes to read (default 64000, max 1000000).' }
      },
      required: ['path'],
      additionalProperties: false
    },
    requiresApproval: false,
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();
      const rawPath = typeof input.path === 'string' ? input.path : '';
      const safePath = resolveSafePath(root, rawPath);

      if (!existsSync(safePath)) {
        throw new Error(`File not found: ${rawPath}`);
      }

      const stat = statSync(safePath);
      if (stat.isDirectory()) {
        throw new Error(`Path is a directory, not a file: ${rawPath}`);
      }

      const reqMax = typeof input.maxBytes === 'number' && Number.isFinite(input.maxBytes) ? input.maxBytes : defaultMaxBytes;
      const maxBytes = Math.min(Math.max(1, reqMax), 1_000_000);
      const encoding = (input.encoding as BufferEncoding) || 'utf-8';

      const buffer = readFileSync(safePath);
      if (buffer.length > maxBytes) {
        const truncated = buffer.subarray(0, maxBytes).toString(encoding);
        return truncated + `\n[File truncated: exceeded ${maxBytes} bytes limit]`;
      }

      return buffer.toString(encoding);
    }
  };
}

export function createFileWriteTool(options: FileToolsOptions = {}): AgentTool {
  const root = resolve(process.cwd(), options.workspaceRoot || process.env.WORKSPACE_DIR || '.');

  return {
    name: 'file_write',
    description: 'Write or overwrite a file within the workspace root. Requires explicit user approval.',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Path to the file relative to workspace root.' },
        content: { type: 'string', description: 'Content to write to the file.' },
        encoding: {
          type: 'string',
          enum: ['utf-8', 'utf8', 'ascii', 'base64', 'hex'],
          description: 'Encoding to write with (default utf-8).'
        }
      },
      required: ['path', 'content'],
      additionalProperties: false
    },
    requiresApproval: true,
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();
      const rawPath = typeof input.path === 'string' ? input.path : '';
      const content = typeof input.content === 'string' ? input.content : '';
      const encoding = (input.encoding as BufferEncoding) || 'utf-8';
      const safePath = resolveSafePath(root, rawPath);

      const parentDir = dirname(safePath);
      if (!existsSync(parentDir)) {
        mkdirSync(parentDir, { recursive: true, mode: 0o755 });
      }

      writeFileSync(safePath, content, { encoding, mode: 0o644 });
      const bytes = Buffer.byteLength(content, encoding);
      return `File successfully written: ${rawPath} (${bytes} bytes)`;
    }
  };
}

export function createFileListTool(options: FileToolsOptions = {}): AgentTool {
  const root = resolve(process.cwd(), options.workspaceRoot || process.env.WORKSPACE_DIR || '.');
  const defaultMaxEntries = options.maxListEntries ?? 200;

  return {
    name: 'file_list',
    description: 'List files and directories within a directory inside the workspace root.',
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Directory relative to workspace root (default .).' },
        recursive: { type: 'boolean', description: 'Whether to list recursively (default false).' },
        maxEntries: { type: 'number', description: 'Maximum entries to return (default 200, max 1000).' }
      },
      additionalProperties: false
    },
    requiresApproval: false,
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();
      const rawPath = typeof input.path === 'string' && input.path.trim() ? input.path : '.';
      const safePath = resolveSafePath(root, rawPath);

      if (!existsSync(safePath)) {
        throw new Error(`Directory not found: ${rawPath}`);
      }

      const stat = statSync(safePath);
      if (!stat.isDirectory()) {
        throw new Error(`Path is a file, not a directory: ${rawPath}`);
      }

      const recursive = Boolean(input.recursive);
      const reqMax = typeof input.maxEntries === 'number' && Number.isFinite(input.maxEntries) ? input.maxEntries : defaultMaxEntries;
      const maxEntries = Math.min(Math.max(1, reqMax), 1000);

      const entries: string[] = [];

      function scan(currentDir: string, relPrefix: string) {
        if (entries.length >= maxEntries || signal?.aborted) return;
        const dirEntries = readdirSync(currentDir, { withFileTypes: true });

        for (const entry of dirEntries) {
          if (entries.length >= maxEntries || signal?.aborted) break;
          // Skip internal hidden/heavy directories during recursive scanning
          if (entry.name === '.git' || entry.name === 'node_modules') continue;

          const entryRelPath = relPrefix ? `${relPrefix}/${entry.name}` : entry.name;
          const fullPath = resolve(currentDir, entry.name);

          if (entry.isDirectory()) {
            entries.push(`[DIR]  ${entryRelPath}`);
            if (recursive) {
              scan(fullPath, entryRelPath);
            }
          } else {
            try {
              const fileStat = statSync(fullPath);
              entries.push(`[FILE] ${entryRelPath} (${fileStat.size} bytes)`);
            } catch {
              entries.push(`[FILE] ${entryRelPath}`);
            }
          }
        }
      }

      scan(safePath, rawPath === '.' ? '' : rawPath);

      if (!entries.length) {
        return `[Directory is empty: ${rawPath}]`;
      }

      let result = entries.join('\n');
      if (entries.length >= maxEntries) {
        result += `\n[List truncated: reached maximum ${maxEntries} entries]`;
      }
      return result;
    }
  };
}

export function createFileTools(options: FileToolsOptions = {}): AgentTool[] {
  return [
    createFileReadTool(options),
    createFileWriteTool(options),
    createFileListTool(options)
  ];
}

export const fileReadTool: AgentTool = createFileReadTool();
export const fileWriteTool: AgentTool = createFileWriteTool();
export const fileListTool: AgentTool = createFileListTool();
