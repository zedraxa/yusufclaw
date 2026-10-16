"""Tests for trusted local markdown skills loader and agent prompt injection."""

import tempfile
from pathlib import Path

import pytest

from yusufclaw.agent import Agent
from yusufclaw.memory.sqlite import SqliteMemory
from yusufclaw.models import FakeProvider
from yusufclaw.skills import (
    MAX_DEPTH,
    MAX_FILE_BYTES,
    load_skills,
    load_skills_sync,
    loadSkills,
)


@pytest.mark.asyncio
async def test_loads_only_sorted_trusted_markdown_and_injects_prompt() -> None:
    """Port of Vitest: loads only sorted trusted Markdown and injects it alongside the system prompt."""
    with tempfile.TemporaryDirectory(prefix="yc-skills-") as tmpdir:
        root = Path(tmpdir)
        nested = root / "nested"
        nested.mkdir()

        (root / "b.md").write_text("Second instruction", encoding="utf-8")
        (root / "a.md").write_text("First instruction", encoding="utf-8")
        (nested / "SKILL.md").write_text("Nested instruction", encoding="utf-8")
        (root / "script.js").write_text('throw new Error("never execute")', encoding="utf-8")
        (root / "README.md").write_text("Not instructions", encoding="utf-8")
        (root / ".hidden.md").write_text("Hidden", encoding="utf-8")

        # Symlink should be skipped
        link_path = root / "link.md"
        try:
            link_path.symlink_to(root / "a.md")
        except OSError:
            pass

        skills = await load_skills(str(root))

        assert "First instruction" in skills
        assert "Nested instruction" in skills
        assert "Second instruction" in skills
        assert skills.index("First") < skills.index("Second")
        assert "never execute" not in skills
        assert "Not instructions" not in skills
        assert "Hidden" not in skills
        assert "link.md" not in skills

        # Verify injection alongside the system prompt in Agent
        provider = FakeProvider(default_text="done")
        db = SqliteMemory(":memory:")
        try:
            agent = Agent(
                provider=provider,
                memory=db,
                system_prompt="Base prompt",
                skills=skills,
            )
            reply = await agent.handle("s", "1", "hello")
            assert reply == "done"
            assert len(provider.recorded_requests) == 1
            assert provider.recorded_requests[0].system == f"Base prompt\n\n{skills}"
        finally:
            db.close()

        # Non-existent directory returns empty string
        absent_path = root / "absent"
        assert await load_skills(str(absent_path)) == ""
        assert load_skills_sync(str(absent_path)) == ""
        assert await loadSkills(str(absent_path)) == ""

        # Oversized single file (> 32,768 bytes) throws size limit error
        huge_file = root / "huge.md"
        huge_file.write_text("a" * (MAX_FILE_BYTES + 1), encoding="utf-8")
        with pytest.raises(ValueError, match="size limit"):
            await load_skills(str(root))

        huge_file.unlink()


@pytest.mark.asyncio
async def test_cumulative_size_limit() -> None:
    """Verify cumulative size limit (131,072 bytes) across multiple files."""
    with tempfile.TemporaryDirectory(prefix="yc-skills-") as tmpdir:
        root = Path(tmpdir)
        # 5 files of 30,000 bytes each = 150,000 bytes > 131,072 limit
        for i in range(5):
            (root / f"file_{i}.md").write_text("x" * 30_000, encoding="utf-8")

        with pytest.raises(ValueError, match="size limit"):
            await load_skills(str(root))


@pytest.mark.asyncio
async def test_nesting_depth_limit() -> None:
    """Verify nesting depth limit (depth > 8 throws)."""
    with tempfile.TemporaryDirectory(prefix="yc-skills-") as tmpdir:
        curr = Path(tmpdir)
        for i in range(MAX_DEPTH + 1):  # 9 levels of nesting
            curr = curr / f"level_{i}"
            curr.mkdir()
        (curr / "deep.md").write_text("Too deep", encoding="utf-8")

        with pytest.raises(ValueError, match="nesting limit exceeded"):
            await load_skills(tmpdir)


@pytest.mark.asyncio
async def test_non_directory_path() -> None:
    """Verify passing a regular file or symlink path throws directory error."""
    with tempfile.TemporaryDirectory(prefix="yc-skills-") as tmpdir:
        file_path = Path(tmpdir) / "regular_file.txt"
        file_path.write_text("hello", encoding="utf-8")

        with pytest.raises(ValueError, match="Skills path must be a directory"):
            await load_skills(str(file_path))


@pytest.mark.asyncio
async def test_empty_or_whitespace_markdown_ignored() -> None:
    """Verify empty or whitespace-only markdown files do not create empty skill headers."""
    with tempfile.TemporaryDirectory(prefix="yc-skills-") as tmpdir:
        root = Path(tmpdir)
        (root / "empty.md").write_text("   \n\n  \t  ", encoding="utf-8")
        skills = await load_skills(str(root))
        assert skills == ""
