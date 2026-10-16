import type { ToolCall, ToolDefinition } from '@yusufclaw/models';
import { createShellTool, shellTool, type ShellToolOptions } from './shell.js';
import { createFileTools, fileReadTool, fileWriteTool, fileListTool, type FileToolsOptions } from './files.js';
import { createWebFetchTool, webFetchTool, type WebFetchToolOptions } from './fetch.js';
import { createBrowserTool, browserTool, type BrowserToolOptions } from './browser.js';
import { createDelegateAgentTool, delegateAgentTool, type DelegateAgentToolOptions } from './delegate.js';

export interface AgentTool extends ToolDefinition {
  requiresApproval?: boolean | ((input: Record<string, unknown>) => boolean);
  execute(input: Record<string, unknown>, signal?: AbortSignal): Promise<string>;
}

export type ApproveTool = (call: ToolCall) => Promise<boolean>;

export const currentTime: AgentTool = {
  name: 'current_time',
  description: 'Return the current date and time in UTC.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  async execute() { return new Date().toISOString(); },
};

export * from './shell.js';
export * from './files.js';
export * from './fetch.js';
export * from './browser.js';
export * from './delegate.js';

export interface DefaultToolsOptions {
  workspaceDir?: string;
  shell?: ShellToolOptions;
  files?: FileToolsOptions;
  fetch?: WebFetchToolOptions;
  browser?: BrowserToolOptions;
  delegate?: DelegateAgentToolOptions;
  enableAgentDelegation?: boolean;
}

/**
 * Creates the complete default toolset for YusufClaw Phase 2:
 * - current_time (read-only clock)
 * - shell (sandboxed shell with allowlist and dangerous command approval)
 * - file_read (scoped workspace file reading)
 * - file_write (scoped workspace file writing, requires approval)
 * - file_list (scoped workspace directory listing)
 * - web_fetch (bounded HTTP/HTTPS fetch with size and time limits)
 * - browser (playwright headless browsing, optional dependency)
 * - delegate_agent (runs agy or codex CLI as subprocess, disabled unless enabled)
 */
export function createDefaultTools(options: DefaultToolsOptions = {}): AgentTool[] {
  const ws = options.workspaceDir;
  return [
    currentTime,
    createShellTool({ workspaceDir: ws, ...options.shell }),
    ...createFileTools({ workspaceRoot: ws, ...options.files }),
    createWebFetchTool(options.fetch),
    createBrowserTool(options.browser),
    createDelegateAgentTool({
      workspaceDir: ws,
      enabled: options.enableAgentDelegation,
      ...options.delegate
    }),
  ];
}

export const defaultTools: AgentTool[] = [
  currentTime,
  shellTool,
  fileReadTool,
  fileWriteTool,
  fileListTool,
  webFetchTool,
  browserTool,
  delegateAgentTool,
];
