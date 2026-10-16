"""Skill evolution lifecycle for YusufClaw.

Implements the 7-stage skill lifecycle pipeline (ARCHITECTURE-v2 Layer 6, RESEARCH-ASIST):
1. task: Define goal, input prompt, and acceptance criteria.
2. solve: Execute solution via agent or solver callback.
3. test: Verify initial solution against acceptance criteria.
4. extract: Synthesize reusable procedural skill methodology.
5. skill: Form draft/quarantined skill artifact on disk with SHA-256 fingerprint.
6. retest: Execute task with candidate skill injected to verify reliability.
7. version: Pin content hash in skills.lock, transition to active, and commit to git.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SkillStatus = Literal["draft", "quarantined", "active", "rejected"]


class TaskSpec(BaseModel):
    """Specification of an evaluation or operational task to solve and skill-ify."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    description: str
    prompt: str
    expected_criteria: list[str] = Field(default_factory=list)
    fixture_data: dict[str, Any] = Field(default_factory=dict)


class TaskSolution(BaseModel):
    """Observed solution steps and methodology produced by solving a TaskSpec."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str
    success: bool
    steps: list[str] = Field(default_factory=list)
    output: str = ""
    methodology: str = ""


class VerificationResult(BaseModel):
    """Result of evaluating a solution or skill against task acceptance criteria."""

    model_config = ConfigDict(populate_by_name=True)

    passed: bool
    details: str
    score: float = 1.0


class SkillCandidate(BaseModel):
    """A draft or quarantined skill extracted from a verified solution."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    allowed_tools: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    instructions: str
    resources: list[str] = Field(default_factory=list)
    status: SkillStatus = "draft"
    content_sha256: str = ""

    def format_markdown(self) -> str:
        """Format the skill into standard YAML frontmatter + markdown body."""
        tools_block = "\n".join(f"  - {t}" for t in self.allowed_tools) if self.allowed_tools else "  []"
        if not self.allowed_tools:
            tools_line = "allowed-tools: []"
        else:
            tools_line = f"allowed-tools:\n{tools_block}"

        resources_line = "resources: []"
        if self.resources:
            res_block = "\n".join(f"  - {r}" for r in self.resources)
            resources_line = f"resources:\n{res_block}"

        frontmatter = (
            f"---\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"{tools_line}\n"
            f"version: {self.version}\n"
            f"{resources_line}\n"
            f"---"
        )
        return f"{frontmatter}\n\n{self.instructions.strip()}\n"

    def compute_hash(self) -> str:
        """Compute the canonical SHA-256 digest of the formatted skill markdown."""
        md = self.format_markdown()
        return hashlib.sha256(md.encode("utf-8")).hexdigest()


class VersionedSkill(BaseModel):
    """An active, pinned, git-versioned skill ready for agent execution."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    version: str
    content_sha256: str
    commit_sha: str | None = None
    status: SkillStatus = "active"
    file_path: str


class SkillLockRecord(BaseModel):
    """Audit and lock record pinning a skill's content hash and version."""

    model_config = ConfigDict(populate_by_name=True)

    skill_name: str
    version: str
    hash: str
    status: SkillStatus = "active"
    quarantine_reason: str | None = None
    updated_at: str


class SkillLock:
    """Manages skills.lock file for tamper detection and pinning."""

    def __init__(self, lock_path: str | Path = "skills.lock") -> None:
        self.lock_path = Path(lock_path)
        self._records: dict[str, SkillLockRecord] = {}
        if self.lock_path.exists():
            self.load()

    def load(self) -> None:
        if not self.lock_path.exists():
            return
        content = self.lock_path.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
            skills = data.get("skills", {})
            for name, rec in skills.items():
                if isinstance(rec, dict):
                    self._records[name] = SkillLockRecord.model_validate(rec)
        except Exception:
            pass

    def save(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "skills": {k: v.model_dump() for k, v in self._records.items()},
        }
        self.lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def pin(
        self,
        skill_name: str,
        content_sha256: str,
        version: str = "1.0.0",
    ) -> SkillLockRecord:
        now = datetime.now(UTC).isoformat()
        rec = SkillLockRecord(
            skill_name=skill_name,
            version=version,
            hash=content_sha256,
            status="active",
            quarantine_reason=None,
            updated_at=now,
        )
        self._records[skill_name] = rec
        self.save()
        return rec

    def quarantine(self, skill_name: str, reason: str, hash_val: str = "") -> SkillLockRecord:
        now = datetime.now(UTC).isoformat()
        rec = SkillLockRecord(
            skill_name=skill_name,
            version="1.0.0",
            hash=hash_val,
            status="quarantined",
            quarantine_reason=reason,
            updated_at=now,
        )
        self._records[skill_name] = rec
        self.save()
        return rec

    def verify_skill(self, skill_name: str, content: str) -> tuple[bool, str]:
        """Verify skill content against pinned lock record."""
        rec = self._records.get(skill_name)
        if rec is None:
            return False, "unpinned"
        if rec.status != "active":
            return False, f"quarantined: {rec.quarantine_reason or 'unapproved'}"

        current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if current_hash != rec.hash:
            return False, "hash_mismatch"

        return True, "verified"

    def is_quarantined(self, skill_name: str) -> bool:
        rec = self._records.get(skill_name)
        if rec is None:
            return True  # Unpinned skills are treated as quarantined/untrusted
        return rec.status != "active"


class SkillLifecycle:
    """Orchestrates the 7-stage skill lifecycle pipeline."""

    def __init__(
        self,
        base_dir: str | Path = "skills",
        lock_path: str | Path | None = None,
        repo_path: str | Path = ".",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.repo_path = Path(repo_path)
        lp = lock_path or (self.base_dir.parent / "skills.lock")
        self.lock = SkillLock(lock_path=lp)

    def task(
        self,
        task_id: str,
        description: str,
        prompt: str,
        criteria: list[str] | None = None,
        fixture: dict[str, Any] | None = None,
    ) -> TaskSpec:
        """Stage 1: Define a new task specification."""
        return TaskSpec(
            task_id=task_id,
            description=description,
            prompt=prompt,
            expected_criteria=criteria or [],
            fixture_data=fixture or {},
        )

    async def solve(
        self,
        task_spec: TaskSpec,
        solver_fn: Callable[[TaskSpec], Awaitable[TaskSolution]],
    ) -> TaskSolution:
        """Stage 2: Execute solution for task_spec using solver_fn."""
        return await solver_fn(task_spec)

    async def test(
        self,
        task_spec: TaskSpec,
        solution: TaskSolution,
        verifier_fn: Callable[[TaskSpec, TaskSolution], Awaitable[VerificationResult]],
    ) -> VerificationResult:
        """Stage 3: Verify initial solution against acceptance criteria."""
        return await verifier_fn(task_spec, solution)

    def extract(
        self,
        task_spec: TaskSpec,
        solution: TaskSolution,
        skill_name: str | None = None,
        allowed_tools: list[str] | None = None,
    ) -> SkillCandidate:
        """Stage 4: Synthesize a reusable skill candidate from the verified solution."""
        raw_name = skill_name or task_spec.task_id
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", raw_name).lower().strip("_")

        methodology_text = solution.methodology or solution.output
        steps_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(solution.steps))

        instructions = (
            f"## Objective\n{task_spec.description}\n\n"
            f"## Verified Workflow\n{steps_text}\n\n"
            f"## Methodology & Guidelines\n{methodology_text}\n"
        )

        candidate = SkillCandidate(
            name=safe_name,
            description=task_spec.description,
            allowed_tools=allowed_tools or [],
            version="1.0.0",
            instructions=instructions,
            status="draft",
        )
        candidate.content_sha256 = candidate.compute_hash()
        return candidate

    def skill(
        self,
        candidate: SkillCandidate,
        quarantine: bool = True,
    ) -> tuple[SkillCandidate, Path]:
        """Stage 5: Persist candidate skill to disk in draft or quarantined state."""
        if quarantine:
            target_dir = self.base_dir / "quarantine" / candidate.name
            candidate.status = "quarantined"
        else:
            target_dir = self.base_dir / candidate.name
            candidate.status = "draft"

        target_dir.mkdir(parents=True, exist_ok=True)
        skill_file = target_dir / "SKILL.md"

        content = candidate.format_markdown()
        skill_file.write_text(content, encoding="utf-8")
        candidate.content_sha256 = candidate.compute_hash()

        if quarantine:
            self.lock.quarantine(
                candidate.name,
                reason="Newly synthesized skill awaiting retest and approval",
                hash_val=candidate.content_sha256,
            )

        return candidate, skill_file

    async def retest(
        self,
        candidate: SkillCandidate,
        task_spec: TaskSpec,
        retest_fn: Callable[[SkillCandidate, TaskSpec], Awaitable[VerificationResult]],
    ) -> VerificationResult:
        """Stage 6: Retest the task with the newly candidate skill injected."""
        res = await retest_fn(candidate, task_spec)
        if not res.passed:
            candidate.status = "rejected"
            self.lock.quarantine(
                candidate.name,
                reason=f"Skill retest failed: {res.details}",
                hash_val=candidate.content_sha256,
            )
        return res

    def version(
        self,
        candidate: SkillCandidate,
        commit: bool = True,
        commit_message: str | None = None,
    ) -> VersionedSkill:
        """Stage 7: Promote skill from quarantine to active, pin hash, and commit to git."""
        # 1. Promote to active location under base_dir/<candidate.name>/SKILL.md
        active_dir = self.base_dir / candidate.name
        active_dir.mkdir(parents=True, exist_ok=True)
        active_file = active_dir / "SKILL.md"

        candidate.status = "active"
        md_content = candidate.format_markdown()
        active_file.write_text(md_content, encoding="utf-8")

        # Clean up quarantine directory if it existed
        quarantine_dir = self.base_dir / "quarantine" / candidate.name
        if quarantine_dir.exists():
            shutil.rmtree(quarantine_dir, ignore_errors=True)

        # 2. Pin in skills.lock
        content_hash = candidate.compute_hash()
        self.lock.pin(
            skill_name=candidate.name,
            content_sha256=content_hash,
            version=candidate.version,
        )

        # 3. Git-backed versioning
        commit_sha: str | None = None
        if commit and (self.repo_path / ".git").exists():
            try:
                rel_skill = str(active_file.resolve().relative_to(self.repo_path.resolve()))
                rel_lock = str(self.lock.lock_path.resolve().relative_to(self.repo_path.resolve()))
                subprocess.run(
                    ["git", "add", rel_skill, rel_lock],
                    cwd=str(self.repo_path),
                    check=True,
                    capture_output=True,
                )
                msg = commit_message or f"feat(skill): version skill '{candidate.name}' v{candidate.version}"
                res = subprocess.run(
                    ["git", "commit", "-m", msg],
                    cwd=str(self.repo_path),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0:
                    sha_res = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(self.repo_path),
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    commit_sha = sha_res.stdout.strip()
            except Exception:
                commit_sha = None

        return VersionedSkill(
            name=candidate.name,
            version=candidate.version,
            content_sha256=content_hash,
            commit_sha=commit_sha,
            status="active",
            file_path=str(active_file),
        )

    async def run_pipeline(
        self,
        task_id: str,
        description: str,
        prompt: str,
        solver_fn: Callable[[TaskSpec], Awaitable[TaskSolution]],
        verifier_fn: Callable[[TaskSpec, TaskSolution], Awaitable[VerificationResult]],
        retest_fn: Callable[[SkillCandidate, TaskSpec], Awaitable[VerificationResult]],
        criteria: list[str] | None = None,
        fixture: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        commit: bool = True,
    ) -> VersionedSkill:
        """Run the complete end-to-end 7-stage skill lifecycle pipeline."""
        # 1. task
        spec = self.task(task_id, description, prompt, criteria, fixture)

        # 2. solve
        solution = await self.solve(spec, solver_fn)
        if not solution.success:
            raise RuntimeError(f"Skill lifecycle halted: solution failed for task '{task_id}'")

        # 3. test
        init_test = await self.test(spec, solution, verifier_fn)
        if not init_test.passed:
            raise RuntimeError(
                f"Skill lifecycle halted: initial test failed for task '{task_id}': {init_test.details}"
            )

        # 4. extract
        candidate = self.extract(spec, solution, allowed_tools=allowed_tools)

        # 5. skill
        candidate, _ = self.skill(candidate, quarantine=True)

        # 6. retest
        retest_res = await self.retest(candidate, spec, retest_fn)
        if not retest_res.passed:
            raise RuntimeError(
                f"Skill lifecycle halted: retest failed for candidate '{candidate.name}': {retest_res.details}"
            )

        # 7. version
        return self.version(candidate, commit=commit)
