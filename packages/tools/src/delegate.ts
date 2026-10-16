import { execFile } from 'node:child_process';
import { isAbsolute, relative, resolve } from 'node:path';
import type { AgentTool } from './index.js';
import { DEFAULT_WORKSPACE_DIR } from './files.js';

export interface DelegateRunner {
  (binary: string, args: string[], options: { cwd: string; timeout: number; signal?: AbortSignal }): Promise<{ stdout: string; stderr: string; exitCode: number }>;
}

export interface DelegateAgentToolOptions {
  enabled?: boolean;
  workspaceDir?: string;
  defaultTimeoutMs?: number;
  maxTimeoutMs?: number;
  maxOutputChars?: number;
  runner?: DelegateRunner;
}

export function createDelegateAgentTool(options: DelegateAgentToolOptions = {}): AgentTool {
  const defaultWs = resolve(process.cwd(), DEFAULT_WORKSPACE_DIR);
  const workspaceDir = resolve(process.cwd(), options.workspaceDir || process.env.WORKSPACE_DIR || defaultWs);
  const defaultTimeoutMs = options.defaultTimeoutMs ?? 60_000;
  const maxTimeoutMs = options.maxTimeoutMs ?? 300_000;
  const maxOutputChars = options.maxOutputChars ?? 32_000;

  const defaultRunner: DelegateRunner = (binary, args, runnerOpts) => {
    return new Promise((res, rej) => {
      execFile(binary, args, {
        cwd: runnerOpts.cwd,
        timeout: runnerOpts.timeout,
        signal: runnerOpts.signal,
        maxBuffer: 4 * 1024 * 1024
      }, (error, stdout, stderr) => {
        if (error && 'code' in error && (error as NodeJS.ErrnoException).code === 'ENOENT') {
          rej(new Error(`Local agent binary "${binary}" not found in PATH.`));
          return;
        }
        const exitCode = typeof error?.code === 'number' ? error.code : 0;
        res({ stdout: stdout || '', stderr: stderr || '', exitCode });
      });
    });
  };

  const runner = options.runner ?? defaultRunner;

  return {
    name: 'delegate_agent',
    description: 'Delegate a task to a local CLI agent (agy or codex). Requires approval and must be enabled via ENABLE_AGENT_DELEGATION=true.',
    manifest: {
      id: 'delegate_agent',
      version: '1.0.0',
      source: 'builtin',
      effects: ['exec', 'write_local'],
      resources: { paths: [workspaceDir] }
    },
    inputSchema: {
      type: 'object',
      properties: {
        agent: {
          type: 'string',
          enum: ['agy', 'codex'],
          description: 'The local CLI agent to invoke: "agy" or "codex".'
        },
        prompt: {
          type: 'string',
          description: 'The instruction or prompt to pass to the agent.'
        },
        cwd: {
          type: 'string',
          description: 'Optional working directory relative to workspace root.'
        },
        timeoutMs: {
          type: 'number',
          description: 'Execution timeout in milliseconds (default 60000, max 300000).'
        }
      },
      required: ['agent', 'prompt'],
      additionalProperties: false
    },
    requiresApproval: true,
    async execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string> {
      signal?.throwIfAborted();

      // Check whether agent delegation is enabled
      const isEnabled = options.enabled ?? (
        process.env.ENABLE_AGENT_DELEGATION === 'true' ||
        process.env.ENABLE_AGENT_DELEGATION === '1'
      );
      if (!isEnabled) {
        throw new Error('Agent delegation is disabled. Set ENABLE_AGENT_DELEGATION=true to enable it.');
      }

      const agent = typeof input.agent === 'string' ? input.agent.trim().toLowerCase() : '';
      if (agent !== 'agy' && agent !== 'codex') {
        throw new Error(`Unsupported agent: "${agent}". Supported agents are "agy" and "codex".`);
      }

      const prompt = typeof input.prompt === 'string' ? input.prompt.trim() : '';
      if (!prompt) {
        throw new Error('Prompt must be a non-empty string');
      }

      // Confine cwd within workspace
      const reqCwd = typeof input.cwd === 'string' ? input.cwd.trim() : '.';
      const targetCwd = resolve(workspaceDir, reqCwd);
      const relCwd = relative(workspaceDir, targetCwd);
      if (relCwd.startsWith('..') || isAbsolute(relCwd)) {
        throw new Error(`Working directory outside workspace directory: ${reqCwd}`);
      }

      const reqTimeout = typeof input.timeoutMs === 'number' && Number.isFinite(input.timeoutMs) ? input.timeoutMs : defaultTimeoutMs;
      const effectiveTimeout = Math.min(Math.max(1000, reqTimeout), maxTimeoutMs);

      const binary = agent;
      const args = agent === 'agy' ? ['-p', prompt] : ['exec', prompt];

      try {
        const { stdout, stderr, exitCode } = await runner(binary, args, {
          cwd: targetCwd,
          timeout: effectiveTimeout,
          signal
        });

        let output = '';
        if (stdout) output += stdout;
        if (stderr) {
          output += (output ? '\n[stderr]\n' : '') + stderr;
        }
        if (exitCode !== 0) {
          output += (output ? '\n' : '') + `[exit code: ${exitCode}]`;
        }

        if (!output.trim()) {
          output = `[Agent "${agent}" completed with no output]`;
        }

        if (output.length > maxOutputChars) {
          output = output.slice(0, maxOutputChars) + `\n[Output truncated: exceeded ${maxOutputChars} characters limit]`;
        }

        return output;
      } catch (err) {
        if (signal?.aborted) throw new Error('Agent delegation aborted by user');
        const message = err instanceof Error ? err.message : String(err);
        return `Agent delegation failed: ${message}`;
      }
    }
  };
}

export const delegateAgentTool: AgentTool = createDelegateAgentTool();
