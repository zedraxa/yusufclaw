import { execFile } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { isAbsolute, relative, resolve } from 'node:path';
import type { AgentTool } from './index.js';
import { DEFAULT_WORKSPACE_DIR } from './files.js';

export interface ShellToolOptions {
  workspaceDir?: string;
  allowedCommands?: string[];
  defaultTimeoutMs?: number;
  maxTimeoutMs?: number;
  maxOutputChars?: number;
}

export const DEFAULT_ALLOWED_COMMANDS = [
  'ls', 'dir', 'cat', 'head', 'tail', 'grep', 'egrep', 'fgrep',
  'find', 'echo', 'printf', 'pwd', 'which', 'whereis', 'wc',
  'diff', 'sort', 'uniq', 'date', 'whoami', 'uname', 'env',
  'git', 'node', 'pnpm', 'npm', 'npx', 'yarn', 'python', 'python3',
  'mkdir', 'cp', 'mv', 'rm', 'touch', 'rmdir', 'sed', 'awk', 'tr', 'cut',
  'tar', 'gzip', 'gunzip', 'zip', 'unzip', 'curl', 'wget', 'jq', 'sleep'
];

const SAFE_READ_ONLY_BINARIES = new Set([
  'pwd', 'whoami', 'uname', 'date', 'which', 'whereis', 'wc',
  'ls', 'dir', 'cat', 'head', 'tail', 'grep', 'egrep', 'fgrep',
  'diff', 'sort', 'uniq', 'jq'
]);

/**
 * Checks whether a shell command attempts to access restricted sensitive paths
 * (.env*, *.sqlite, ~/.aws, .git/config) or path traversal.
 */
export function containsRestrictedPath(command: string): boolean {
  if (!command || typeof command !== 'string') return false;
  // 1. .env* (.env, .env.local, ../.env, etc.)
  if (/(^|[\s/"'=\\])\.env([a-zA-Z0-9_.-]*)/.test(command)) return true;
  // 2. *.sqlite (*.sqlite, memory.sqlite, etc.)
  if (/\.sqlite($|-|\b|\d)/i.test(command)) return true;
  // 3. ~/.aws or .aws
  if (/(^|[\s/"'=\\])(\~?\/)?\.aws([/\s"'\\$]|$)/.test(command)) return true;
  // 4. .git/config
  if (/\.git\/config/.test(command)) return true;
  // 5. Path traversal attempts like ../
  if (/(^|[\s"'=])\.\.\/|\/\.\.(\/|$|\s|["'])/.test(command)) return true;
  return false;
}

/**
 * Extracts binary names from a shell command string to verify against an allowlist.
 */
export function extractCommandBinaries(command: string): string[] {
  const binaries: string[] = [];
  // Split on pipeline/chain operators: |, ||, &&, ;, \n
  const segments = command.split(/[|;&\n]+/);
  for (const rawSegment of segments) {
    const trimmed = rawSegment.trim();
    if (!trimmed) continue;
    // Tokenize segment by spaces, ignoring inline variable assignments (e.g. FOO=bar)
    const tokens = trimmed.split(/\s+/);
    for (const token of tokens) {
      if (token.includes('=') && !token.startsWith('-')) {
        continue; // skip VAR=val
      }
      // Extract binary basename without path (e.g., /usr/bin/git -> git)
      const cleanToken = token.replace(/^[./\\]+/, '').split(/[/]/).pop() ?? token;
      if (cleanToken) {
        binaries.push(cleanToken);
        break; // Only first executable token per segment
      }
    }
  }
  return binaries;
}

/**
 * Determines whether a command is dangerous or destructive and thus requires explicit approval.
 */
export function isDangerousCommand(command: string): boolean {
  const trimmed = command.trim();
  if (!trimmed) return true;

  // File redirection writes or overwrites files: >, >>, &>, >|, 1>, 2>
  if (/[0-9&]?>>?/.test(trimmed)) {
    return true;
  }

  // Command substitution could hide arbitrary destructive operations
  if (/\$\(|\`/.test(trimmed)) {
    return true;
  }

  // Destructive file/system binaries always require approval
  const destructiveRegex = /\b(rm|rmdir|mv|cp|touch|mkdir|kill|pkill|killall|chmod|chown|chgrp|dd|truncate|tee|unlink|reboot|shutdown|poweroff|systemctl|service)\b/;
  if (destructiveRegex.test(trimmed)) {
    return true;
  }

  // Non-read-only Git commands require approval
  if (/\bgit\b/.test(trimmed)) {
    const isGitReadOnly = /\bgit\s+(status|log|diff|show|rev-parse|describe)(\s|$)/.test(trimmed) ||
      (/\bgit\s+branch(\s|$)/.test(trimmed) && !/-(d|D|m|M)/.test(trimmed));
    if (!isGitReadOnly) {
      return true;
    }
  }

  // Package managers or script interpreters require approval unless checking version
  const isVersionOnly = /^(node|pnpm|npm|python|python3)\s+(-v|--version|-V)$/.test(trimmed);
  if (/\b(node|pnpm|npm|npx|yarn|python|python3)\b/.test(trimmed)) {
    if (!isVersionOnly) {
      return true;
    }
  }

  if (containsRestrictedPath(trimmed)) {
    return true;
  }

  // Check all segments: if any segment binary is not in SAFE_READ_ONLY_BINARIES, require approval
  const binaries = extractCommandBinaries(trimmed);
  if (!binaries.length) return true;
  for (const bin of binaries) {
    if (bin === 'echo' || bin === 'printf') {
      // Safe as long as no redirection (checked above)
      continue;
    }
    if (bin === 'git') {
      // Handled by git check above
      continue;
    }
    if (isVersionOnly && ['node', 'pnpm', 'npm', 'python', 'python3'].includes(bin)) {
      continue;
    }
    if (!SAFE_READ_ONLY_BINARIES.has(bin)) {
      return true;
    }
  }

  return false;
}

export function createShellTool(options: ShellToolOptions = {}): AgentTool {
  const defaultWs = resolve(process.cwd(), DEFAULT_WORKSPACE_DIR);
  const workspaceDir = resolve(process.cwd(), options.workspaceDir || process.env.WORKSPACE_DIR || defaultWs);
  if (!existsSync(workspaceDir)) {
    try { mkdirSync(workspaceDir, { recursive: true, mode: 0o755 }); } catch {}
  }
  const allowed = new Set(
    options.allowedCommands ||
    (process.env.SHELL_ALLOWED_COMMANDS ? process.env.SHELL_ALLOWED_COMMANDS.split(',').map(s => s.trim()).filter(Boolean) : DEFAULT_ALLOWED_COMMANDS)
  );
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 30_000;
  const maxTimeoutMs = options.maxTimeoutMs ?? 60_000;
  const maxOutputChars = options.maxOutputChars ?? 32_000;

  return {
    name: 'shell',
    description: 'Execute a shell command within the workspace directory. Dangerous or destructive commands require explicit user approval.',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string', description: 'The shell command to execute.' },
        cwd: { type: 'string', description: 'Optional directory relative to workspace root.' },
        timeoutMs: { type: 'number', description: 'Execution timeout in milliseconds (max 60000).' }
      },
      required: ['command'],
      additionalProperties: false
    },
    requiresApproval(input: Record<string, unknown>): boolean {
      const command = typeof input.command === 'string' ? input.command : '';
      return isDangerousCommand(command);
    },
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();
      const rawCommand = typeof input.command === 'string' ? input.command.trim() : '';
      if (!rawCommand) throw new Error('Command must be a non-empty string');

      if (containsRestrictedPath(rawCommand)) {
        throw new Error(`Command references restricted sensitive file or path traversal: ${rawCommand}`);
      }

      // Allowlist verification
      const binaries = extractCommandBinaries(rawCommand);
      for (const bin of binaries) {
        if (!allowed.has(bin)) {
          throw new Error(`Command not allowed by allowlist: ${bin}`);
        }
      }

      // Cwd confinement
      const reqCwd = typeof input.cwd === 'string' ? input.cwd.trim() : '.';
      const targetCwd = resolve(workspaceDir, reqCwd);
      const relCwd = relative(workspaceDir, targetCwd);
      if (relCwd.startsWith('..') || isAbsolute(relCwd)) {
        throw new Error(`Working directory outside workspace directory: ${reqCwd}`);
      }

      // Timeout calculation
      const reqTimeout = typeof input.timeoutMs === 'number' && Number.isFinite(input.timeoutMs) ? input.timeoutMs : defaultTimeoutMs;
      const effectiveTimeout = Math.min(Math.max(100, reqTimeout), maxTimeoutMs);

      return new Promise<string>((res, rej) => {
        const timeoutTimer = setTimeout(() => {
          child.kill('SIGTERM');
          setTimeout(() => child.kill('SIGKILL'), 1000).unref();
          rej(new Error(`Command timed out after ${effectiveTimeout}ms`));
        }, effectiveTimeout);

        const onAbort = () => {
          clearTimeout(timeoutTimer);
          child.kill('SIGTERM');
          setTimeout(() => child.kill('SIGKILL'), 1000).unref();
          rej(new Error('Command execution aborted'));
        };

        signal?.addEventListener('abort', onAbort, { once: true });

        const child = execFile('/bin/sh', ['-c', rawCommand], {
          cwd: targetCwd,
          timeout: effectiveTimeout + 1000,
          maxBuffer: 2 * 1024 * 1024,
          env: {
            PATH: process.env.PATH ?? '/bin:/usr/bin:/usr/local/bin',
            HOME: process.env.HOME ?? '/tmp',
            USER: process.env.USER ?? 'user',
            LANG: process.env.LANG ?? 'C.UTF-8',
            TERM: 'dumb'
          }
        }, (error, stdout, stderr) => {
          clearTimeout(timeoutTimer);
          signal?.removeEventListener('abort', onAbort);

          let output = '';
          if (stdout) output += stdout;
          if (stderr) {
            output += (output ? '\n[stderr]\n' : '') + stderr;
          }
          if (error && 'code' in error && typeof error.code === 'number' && error.code !== 0) {
            output += (output ? '\n' : '') + `[exit code: ${error.code}]`;
          }

          if (!output.trim()) {
            output = error ? `Command failed: ${error.message}` : '[Command completed with no output]';
          }

          if (output.length > maxOutputChars) {
            output = output.slice(0, maxOutputChars) + `\n[Output truncated: exceeded ${maxOutputChars} characters limit]`;
          }

          res(output);
        });
      });
    }
  };
}

export const shellTool: AgentTool = createShellTool();
