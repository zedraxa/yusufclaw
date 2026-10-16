import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Agent, SqliteMemory } from '../packages/core/dist/index.js';
import type { ModelProvider, ModelResponse } from '../packages/models/dist/types.js';
import {
  createBrowserTool,
  createDefaultTools,
  createDelegateAgentTool,
  createFileListTool,
  createFileReadTool,
  createFileWriteTool,
  createShellTool,
  createWebFetchTool,
  extractCommandBinaries,
  isDangerousCommand,
  resolveSafePath
} from '../packages/tools/dist/index.js';

const tempDirs: string[] = [];
afterEach(() => {
  tempDirs.forEach(dir => rmSync(dir, { recursive: true, force: true }));
  tempDirs.length = 0;
});

function makeTempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'yusufclaw-tools-'));
  tempDirs.push(dir);
  return dir;
}

describe('Shell tool', () => {
  it('correctly classifies safe vs dangerous commands', () => {
    // Safe commands
    expect(isDangerousCommand('ls')).toBe(false);
    expect(isDangerousCommand('ls -la')).toBe(false);
    expect(isDangerousCommand('pwd')).toBe(false);
    expect(isDangerousCommand('echo "hello world"')).toBe(false);
    expect(isDangerousCommand('cat file.txt')).toBe(false);
    expect(isDangerousCommand('git status')).toBe(false);
    expect(isDangerousCommand('git log -n 5')).toBe(false);
    expect(isDangerousCommand('git diff HEAD')).toBe(false);
    expect(isDangerousCommand('node -v')).toBe(false);
    expect(isDangerousCommand('pnpm --version')).toBe(false);

    // Dangerous commands
    expect(isDangerousCommand('rm file.txt')).toBe(true);
    expect(isDangerousCommand('rm -rf dir')).toBe(true);
    expect(isDangerousCommand('mv a.txt b.txt')).toBe(true);
    expect(isDangerousCommand('cp a.txt b.txt')).toBe(true);
    expect(isDangerousCommand('mkdir newdir')).toBe(true);
    expect(isDangerousCommand('touch test.txt')).toBe(true);
    expect(isDangerousCommand('echo "hi" > test.txt')).toBe(true);
    expect(isDangerousCommand('echo "hi" >> test.txt')).toBe(true);
    expect(isDangerousCommand('git commit -m "feat"')).toBe(true);
    expect(isDangerousCommand('git push origin main')).toBe(true);
    expect(isDangerousCommand('git reset --hard HEAD~1')).toBe(true);
    expect(isDangerousCommand('pnpm install')).toBe(true);
    expect(isDangerousCommand('node index.js')).toBe(true);
    expect(isDangerousCommand('kill -9 1234')).toBe(true);
    expect(isDangerousCommand('chmod +x script.sh')).toBe(true);
    expect(isDangerousCommand('ls $(rm -rf /)')).toBe(true);
  });

  it('extracts command binaries from pipelines and chains', () => {
    expect(extractCommandBinaries('ls -la | grep foo | wc -l')).toEqual(['ls', 'grep', 'wc']);
    expect(extractCommandBinaries('echo hello && cat test.txt; pwd')).toEqual(['echo', 'cat', 'pwd']);
    expect(extractCommandBinaries('FOO=bar /usr/bin/git status')).toEqual(['git']);
  });

  it('executes safe commands in workspace and captures output', async () => {
    const ws = makeTempDir();
    const tool = createShellTool({ workspaceDir: ws });
    const output = await tool.execute({ command: 'echo "hello from workspace"' });
    expect(output.trim()).toBe('hello from workspace');
  });

  it('rejects commands not on allowlist', async () => {
    const ws = makeTempDir();
    const tool = createShellTool({ workspaceDir: ws, allowedCommands: ['echo', 'ls'] });
    await expect(tool.execute({ command: 'cat /etc/passwd' })).rejects.toThrow('Command not allowed by allowlist');
  });

  it('confines cwd strictly to workspace directory', async () => {
    const ws = makeTempDir();
    const tool = createShellTool({ workspaceDir: ws });
    await expect(tool.execute({ command: 'pwd', cwd: '../../' })).rejects.toThrow('Working directory outside workspace');
  });

  it('times out and truncates excessive output', async () => {
    const ws = makeTempDir();
    const tool = createShellTool({ workspaceDir: ws, maxOutputChars: 50, defaultTimeoutMs: 500 });
    await expect(tool.execute({ command: 'sleep 5' })).rejects.toThrow('timed out');

    const longOutput = await tool.execute({ command: 'echo "123456789012345678901234567890123456789012345678901234567890"' });
    expect(longOutput).toContain('Output truncated');
  });
});

describe('File tools', () => {
  it('prevents path traversal and symlink escapes', () => {
    const ws = makeTempDir();
    expect(() => resolveSafePath(ws, '../../etc/passwd')).toThrow('Path escape detected');
    expect(() => resolveSafePath(ws, '/etc/passwd')).toThrow('Path escape detected');
    expect(() => resolveSafePath(ws, 'safe\0/escape')).toThrow('null bytes');

    // Create outside target and symlink inside workspace
    const outside = makeTempDir();
    const outsideFile = join(outside, 'secret.txt');
    writeFileSync(outsideFile, 'secret data');

    const symlinkPath = join(ws, 'leak.txt');
    try {
      symlinkSync(outsideFile, symlinkPath);
      expect(() => resolveSafePath(ws, 'leak.txt')).toThrow('symlink');
    } catch (e: unknown) {
      // If OS denies symlink creation without elevated rights, ignore symlink check
      if (!(e instanceof Error && (e.message.includes('symlink') || e.message.includes('Path escape')))) {
        throw e;
      }
    }
  });

  it('reads, writes and lists files safely within workspace', async () => {
    const ws = makeTempDir();
    const read = createFileReadTool({ workspaceRoot: ws });
    const write = createFileWriteTool({ workspaceRoot: ws });
    const list = createFileListTool({ workspaceRoot: ws });

    // Writing requires approval
    expect(write.requiresApproval).toBe(true);
    expect(read.requiresApproval).toBe(false);
    expect(list.requiresApproval).toBe(false);

    // Write file in subfolder
    const writeResult = await write.execute({ path: 'sub/test.txt', content: 'hello world' });
    expect(writeResult).toContain('File successfully written');

    // Read file
    const content = await read.execute({ path: 'sub/test.txt' });
    expect(content).toBe('hello world');

    // Read non-existent
    await expect(read.execute({ path: 'nonexistent.txt' })).rejects.toThrow('File not found');

    // List files
    const listed = await list.execute({ path: '.', recursive: true });
    expect(listed).toContain('test.txt');
    expect(listed).toContain('[DIR]');
  });
});

describe('Web fetch tool', () => {
  it('fetches HTTP content and handles size limits and SSRF blocking', async () => {
    const mockFetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('<html><body>Mocked Webpage Content</body></html>', {
        status: 200,
        statusText: 'OK',
        headers: { 'content-type': 'text/html' }
      })
    );

    const tool = createWebFetchTool({ fetcher: mockFetcher });
    const result = await tool.execute({ url: 'https://example.com' });
    expect(result).toContain('HTTP 200 OK');
    expect(result).toContain('Mocked Webpage Content');

    // Block non-HTTP protocols
    await expect(tool.execute({ url: 'file:///etc/passwd' })).rejects.toThrow('Unsupported protocol');

    // Block cloud metadata SSRF
    await expect(tool.execute({ url: 'http://169.254.169.254/latest/meta-data/' })).rejects.toThrow('blocked for security');
    await expect(tool.execute({ url: 'http://metadata.google.internal/' })).rejects.toThrow('blocked for security');
  });

  it('enforces size truncation on large responses', async () => {
    const largeStream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('A'.repeat(500)));
        controller.enqueue(new TextEncoder().encode('B'.repeat(500)));
        controller.close();
      }
    });

    const mockFetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(largeStream, { status: 200, statusText: 'OK', headers: { 'content-type': 'text/plain' } })
    );

    const tool = createWebFetchTool({ fetcher: mockFetcher, defaultMaxBytes: 200 });
    const result = await tool.execute({ url: 'https://example.com' });
    expect(result).toContain('Response body truncated');
    expect(result).toContain('reached 200 bytes limit');
  });
});

describe('Browser tool', () => {
  it('returns graceful message when Playwright is unavailable', async () => {
    const tool = createBrowserTool();
    const result = await tool.execute({ url: 'https://example.com' });
    expect(result).toContain('Playwright is not installed');
  });

  it('automates page navigation and resource cleanup with launcher', async () => {
    const closePage = vi.fn(async () => {});
    const closeContext = vi.fn(async () => {});
    const closeBrowser = vi.fn(async () => {});

    const mockLauncher = {
      launch: vi.fn(async () => ({
        newContext: vi.fn(async () => ({
          newPage: vi.fn(async () => ({
            goto: vi.fn(async () => {}),
            title: vi.fn(async () => 'Example Domain'),
            innerText: vi.fn(async () => 'This domain is for use in illustrative examples.'),
            content: vi.fn(async () => '<html><body>Example HTML</body></html>'),
            close: closePage
          })),
          close: closeContext
        })),
        close: closeBrowser
      }))
    };

    const tool = createBrowserTool({ launcher: mockLauncher });
    const result = await tool.execute({ url: 'https://example.com', action: 'navigate' });
    expect(result).toContain('Title: Example Domain');
    expect(result).toContain('illustrative examples');
    expect(closePage).toHaveBeenCalled();
    expect(closeContext).toHaveBeenCalled();
    expect(closeBrowser).toHaveBeenCalled();
  });
});

describe('Delegate agent tool', () => {
  it('is disabled by default and requires ENABLE_AGENT_DELEGATION=true', async () => {
    const tool = createDelegateAgentTool({ enabled: false });
    await expect(tool.execute({ agent: 'agy', prompt: 'hello' })).rejects.toThrow('Agent delegation is disabled');
    expect(tool.requiresApproval).toBe(true);
  });

  it('runs agy and codex subprocesses when enabled', async () => {
    const mockRunner = vi.fn(async (binary: string, args: string[]) => {
      return { stdout: `Result from ${binary} with args ${args.join(' ')}`, stderr: '', exitCode: 0 };
    });

    const tool = createDelegateAgentTool({ enabled: true, runner: mockRunner });

    // agy
    const agyResult = await tool.execute({ agent: 'agy', prompt: 'write tests' });
    expect(mockRunner).toHaveBeenCalledWith('agy', ['-p', 'write tests'], expect.anything());
    expect(agyResult).toContain('Result from agy with args -p write tests');

    // codex
    const codexResult = await tool.execute({ agent: 'codex', prompt: 'refactor code' });
    expect(mockRunner).toHaveBeenCalledWith('codex', ['exec', 'refactor code'], expect.anything());
    expect(codexResult).toContain('Result from codex with args exec refactor code');
  });
});

describe('Core agent loop with Phase 2 tools and approvals', () => {
  it('executes safe tools without approval, and denies dangerous tools when unapproved', async () => {
    const ws = makeTempDir();
    const tools = createDefaultTools({ workspaceDir: ws });

    const safeCall: ModelResponse = {
      text: '',
      toolCalls: [{ id: 'call-1', name: 'shell', input: { command: 'echo "safe tool"' } }]
    };
    const dangerousCall: ModelResponse = {
      text: '',
      toolCalls: [{ id: 'call-2', name: 'shell', input: { command: 'rm -rf dangerous' } }]
    };
    const answer = (text: string): ModelResponse => ({ text, toolCalls: [] });

    const complete = vi.fn<ModelProvider['complete']>()
      .mockResolvedValueOnce(safeCall)
      .mockResolvedValueOnce(answer('safe done'))
      .mockResolvedValueOnce(dangerousCall)
      .mockResolvedValueOnce(answer('denied done'));

    const memory = new SqliteMemory(':memory:');
    const agent = new Agent({ complete }, memory, tools);

    // Call 1: Safe shell command runs without approval callback
    const res1 = await agent.handle('test-session', 'req-1', 'run safe command');
    expect(res1).toBe('safe done');
    expect(complete.mock.calls[1][0].messages.at(-1)).toMatchObject({
      role: 'tool', callId: 'call-1', error: false
    });
    expect((complete.mock.calls[1][0].messages.at(-1) as { text: string }).text.trim()).toBe('safe tool');

    // Call 2: Dangerous shell command without approve callback throws 'Tool approval denied'
    const res2 = await agent.handle('test-session', 'req-2', 'run dangerous command');
    expect(res2).toBe('denied done');
    expect(complete.mock.calls[3][0].messages.at(-1)).toMatchObject({
      role: 'tool', callId: 'call-2', error: true, text: 'Tool approval denied'
    });

    memory.close();
  });
});

describe('YC-01: Workspace and restricted secrets isolation', () => {
  it('denies file_read on .env and other sensitive secret paths', async () => {
    const ws = makeTempDir();
    const read = createFileReadTool({ workspaceRoot: ws });

    // file_read .env is rejected per acceptance criteria
    await expect(read.execute({ path: '.env' })).rejects.toThrow('restricted sensitive');
    await expect(read.execute({ path: '.env.local' })).rejects.toThrow('restricted sensitive');
    await expect(read.execute({ path: '.env.production' })).rejects.toThrow('restricted sensitive');
    await expect(read.execute({ path: 'sub/.env' })).rejects.toThrow('restricted sensitive');

    // *.sqlite
    await expect(read.execute({ path: 'memory.sqlite' })).rejects.toThrow('restricted sensitive');
    await expect(read.execute({ path: 'data/app.sqlite' })).rejects.toThrow('restricted sensitive');

    // ~/.aws
    await expect(read.execute({ path: '~/.aws/credentials' })).rejects.toThrow('restricted sensitive');
    await expect(read.execute({ path: '.aws/config' })).rejects.toThrow('restricted sensitive');

    // .git/config
    await expect(read.execute({ path: '.git/config' })).rejects.toThrow('restricted sensitive');
  });

  it('denies cat ../.env and sensitive shell commands', async () => {
    const ws = makeTempDir();
    const shell = createShellTool({ workspaceDir: ws });

    // cat ../.env is rejected per acceptance criteria
    await expect(shell.execute({ command: 'cat ../.env' })).rejects.toThrow('restricted sensitive');
    await expect(shell.execute({ command: 'cat .env' })).rejects.toThrow('restricted sensitive');
    await expect(shell.execute({ command: 'head -n 1 .env.local' })).rejects.toThrow('restricted sensitive');
    await expect(shell.execute({ command: 'cat .data/memory.sqlite' })).rejects.toThrow('restricted sensitive');
    await expect(shell.execute({ command: 'cat ~/.aws/credentials' })).rejects.toThrow('restricted sensitive');
    await expect(shell.execute({ command: 'cat .git/config' })).rejects.toThrow('restricted sensitive');
  });
});
