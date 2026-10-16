"""Tests for YusufClaw skill lifecycle, pinning, and quarantine subsystem."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yusufclaw.skills.lifecycle import (
    SkillCandidate,
    SkillLifecycle,
    SkillLock,
    TaskSolution,
    TaskSpec,
    VerificationResult,
)


def test_skill_candidate_formatting() -> None:
    """Verify SkillCandidate generates valid Agent Skills markdown frontmatter."""
    cand = SkillCandidate(
        name="csv_data_cleaner",
        description="Clean and normalize messy CSV files",
        allowed_tools=["file_read", "file_write"],
        version="1.0.0",
        instructions="Step 1: Read CSV.\nStep 2: Strip whitespace.\nStep 3: Save.",
    )

    md = cand.format_markdown()
    assert "---" in md
    assert "name: csv_data_cleaner" in md
    assert "description: Clean and normalize messy CSV files" in md
    assert "allowed-tools:" in md
    assert "- file_read" in md
    assert "- file_write" in md
    assert "version: 1.0.0" in md
    assert "Step 1: Read CSV." in md

    h1 = cand.compute_hash()
    assert len(h1) == 64  # SHA-256 hex digest


def test_skill_lock_tamper_detection(tmp_path: Path) -> None:
    """Verify SkillLock detects hash mismatch and unpinned skills."""
    lock_file = tmp_path / "skills.lock"
    lock = SkillLock(lock_path=lock_file)

    skill_name = "deploy_helper"
    content = "---\nname: deploy_helper\n---\nRun deploy"
    h = "abc123sha256"

    # 1. Unpinned
    is_ok, reason = lock.verify_skill(skill_name, content)
    assert not is_ok
    assert reason == "unpinned"

    # 2. Pinned
    real_hash = lock.pin(skill_name, content_sha256=h, version="1.0.0").hash
    assert real_hash == h

    # Verified
    is_ok, reason = lock.verify_skill(skill_name, "tampered content")
    assert not is_ok
    assert reason == "hash_mismatch"

    # Quarantine
    lock.quarantine(skill_name, reason="Security issue")
    assert lock.is_quarantined(skill_name)


@pytest.mark.asyncio
async def test_skill_lifecycle_pipeline(tmp_path: Path) -> None:
    """Test full 7-stage skill lifecycle: task -> solve -> test -> extract -> skill -> retest -> version."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    # Initialize a clean git repo in repo_dir
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@yusufclaw.local"],
        cwd=str(repo_dir),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "YusufClaw Agent"],
        cwd=str(repo_dir),
        check=True,
    )

    lifecycle = SkillLifecycle(
        base_dir=repo_dir / "skills",
        lock_path=repo_dir / "skills.lock",
        repo_path=repo_dir,
    )

    # 1. Solver callback
    async def mock_solver(spec: TaskSpec) -> TaskSolution:
        return TaskSolution(
            task_id=spec.task_id,
            success=True,
            steps=[
                "Parse raw inputs",
                "Validate schema",
                "Compute summary statistics",
            ],
            output="Processed 100 rows successfully.",
            methodology="Use streaming JSON parser to prevent memory exhaustion.",
        )

    # 2. Verifier callback
    async def mock_verifier(spec: TaskSpec, sol: TaskSolution) -> VerificationResult:
        if "streaming" in sol.methodology:
            return VerificationResult(passed=True, details="Methodology meets efficiency criteria")
        return VerificationResult(passed=False, details="Inefficient methodology")

    # 3. Retest callback
    async def mock_retest(cand: SkillCandidate, spec: TaskSpec) -> VerificationResult:
        # Check that candidate instructions contain the steps
        if "Parse raw inputs" in cand.instructions:
            return VerificationResult(passed=True, details="Retest passed with candidate skill")
        return VerificationResult(passed=False, details="Retest failed")

    # Run complete pipeline
    versioned = await lifecycle.run_pipeline(
        task_id="json_stream_etl",
        description="Extract and transform large JSON datasets using streaming",
        prompt="Process large JSON dataset without crashing",
        solver_fn=mock_solver,
        verifier_fn=mock_verifier,
        retest_fn=mock_retest,
        allowed_tools=["file_read", "file_write"],
        commit=True,
    )

    assert versioned.name == "json_stream_etl"
    assert versioned.version == "1.0.0"
    assert versioned.status == "active"
    assert versioned.commit_sha is not None  # Committed to git!
    assert Path(versioned.file_path).exists()

    # Verify skill is pinned in lock
    is_valid, status = lifecycle.lock.verify_skill(
        "json_stream_etl", Path(versioned.file_path).read_text(encoding="utf-8")
    )
    assert is_valid
    assert status == "verified"

    # Verify tampering triggers rejection
    is_tampered, reason = lifecycle.lock.verify_skill(
        "json_stream_etl", "corrupted skill content"
    )
    assert not is_tampered
    assert reason == "hash_mismatch"


@pytest.mark.asyncio
async def test_skill_lifecycle_retest_failure(tmp_path: Path) -> None:
    """Verify that a retest failure stops promotion and keeps skill in quarantine."""
    lifecycle = SkillLifecycle(
        base_dir=tmp_path / "skills",
        lock_path=tmp_path / "skills.lock",
        repo_path=tmp_path,
    )

    spec = lifecycle.task("flaky_task", "Flaky task", "Do something flaky")
    solution = TaskSolution(
        task_id="flaky_task",
        success=True,
        steps=["Step A"],
        methodology="Flaky method",
    )

    candidate = lifecycle.extract(spec, solution)
    candidate, skill_file = lifecycle.skill(candidate, quarantine=True)
    assert candidate.status == "quarantined"
    assert lifecycle.lock.is_quarantined("flaky_task")

    # Retest fails
    async def failing_retest(c: SkillCandidate, s: TaskSpec) -> VerificationResult:
        return VerificationResult(passed=False, details="Non-deterministic failure")

    res = await lifecycle.retest(candidate, spec, failing_retest)
    assert not res.passed
    assert candidate.status == "rejected"
    assert lifecycle.lock.is_quarantined("flaky_task")
