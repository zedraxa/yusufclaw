# Trusted skills

Place trusted Markdown instructions here (for example `planning.md` or
`planning/SKILL.md`). All `.md` files are loaded in sorted path order at startup
and appended to the agent's system prompt for every channel and routine.

`README.md`, hidden entries, symlinks and non-Markdown files are ignored.
A missing folder is allowed. Limits: 32 KiB per file, 128 KiB total, eight nested
directories. Oversized or unreadable instructions fail startup.

These files have system-prompt authority: install only instructions you trust.
The loader does not execute code, expand includes, fetch URLs, parse frontmatter,
or install tools. Restart the process after changing a skill.
