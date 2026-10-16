import { readdir, readFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';

/** Trusted local instructions only. No scripts, includes, symlinks, or frontmatter execution. */
export async function loadSkills(directory = 'skills'): Promise<string> {
  const parts: string[] = [];
  let bytes = 0;
  async function visit(path: string, prefix: string, depth: number): Promise<void> {
    if (depth > 8) throw new Error('Skills directory nesting limit exceeded');
    const entries = await readdir(path, { withFileTypes: true });
    for (const entry of entries.sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
      if (entry.name.startsWith('.') || entry.isSymbolicLink()) continue;
      const name = prefix + entry.name;
      const file = join(path, entry.name);
      if (entry.isDirectory()) await visit(file, `${name}/`, depth + 1);
      else if (entry.isFile() && /\.md$/i.test(entry.name) && entry.name.toLowerCase() !== 'readme.md') {
        const stat = await lstat(file);
        bytes += stat.size;
        if (stat.size > 32_768 || bytes > 131_072) throw new Error('Skill instructions exceed size limit');
        const content = await readFile(file, 'utf8');
        if (content.trim()) parts.push(`### Skill: ${name}\n${content.trim()}`);
      }
    }
  }
  try {
    if (!(await lstat(directory)).isDirectory()) throw new Error('Skills path must be a directory');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return '';
    throw error;
  }
  await visit(directory, '', 0);
  return parts.length ? `Trusted local skill instructions:\n\n${parts.join('\n\n')}` : '';
}
