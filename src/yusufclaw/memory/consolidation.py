"""Bounded, offline consolidation through the existing memory lifecycle API."""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from yusufclaw.memory.models import ListOptions, MemoryRecord, RememberInput, ReviseInput
from yusufclaw.memory.store import MemoryStore, resolve


class ConsolidationOptions(BaseModel):
    similarity_threshold: float = Field(default=0.92, ge=0.5, le=1)
    promotion_min_entries: int = Field(default=2, ge=2)
    max_records: int = Field(default=200, ge=1, le=200)
    journal_category: str = "journal"
    promote: bool = True
    pinned_ids: set[str] = Field(default_factory=set)


@dataclass
class ConsolidationResult:
    scanned: int = 0
    at_capacity: bool = False
    merged: list[tuple[str, str]] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)


def similarity(left: str, right: str) -> float:
    """Conservative lexical similarity, not an entailment or truth classifier.

    Only punctuation/spacing and a small set of filler words may differ. Names,
    numbers, polarity and other content words must match to avoid conflating claims.
    """
    if re.findall(r"[+-]?\d+(?:[.,]\d+)?", left) != re.findall(r"[+-]?\d+(?:[.,]\d+)?", right):
        return 0.0
    a = re.findall(r"\w+", left.casefold())
    b = re.findall(r"\w+", right.casefold())
    filler = {"a", "an", "the", "really", "very"}
    if [w for w in a if w not in filler] != [w for w in b if w not in filler]:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0


def _compatible(a: MemoryRecord, b: MemoryRecord) -> bool:
    return (a.kind, a.category, a.trust, a.source, a.valid_to) == (
        b.kind,
        b.category,
        b.trust,
        b.source,
        b.valid_to,
    )


class ConsolidationJob:
    """Run with exclusive write access to a scope; operations commit individually.

    Durable facts are merged via revise + audited forget. Repeated explicit-user
    journal claims become facts by superseding a note, then retiring other notes.
    No LLM or embedding service is required, and trust is never upgraded.
    """

    def __init__(self, store: MemoryStore, options: ConsolidationOptions | None = None):
        self.store = store
        self.options = options or ConsolidationOptions()

    async def run(self, scope: str) -> ConsolidationResult:
        opts = self.options
        records = await resolve(self.store.list(scope, ListOptions(limit=opts.max_records)))
        records.sort(key=lambda r: (r.created_at, r.id))
        result = ConsolidationResult(
            scanned=len(records), at_capacity=len(records) == opts.max_records
        )
        consumed: set[str] = set()
        # Finish interrupted retirements using persisted lineage, including promotions.
        for keeper in records:
            if keeper.kind == "note":
                continue
            for candidate in records:
                if (
                    candidate.id != keeper.id
                    and candidate.id not in opts.pinned_ids
                    and f"consolidation:{candidate.id}" in keeper.source_ids
                ):
                    if candidate.trust == keeper.trust and candidate.source == keeper.source:
                        await resolve(
                            self.store.forget(scope, candidate.id, f"consolidated into {keeper.id}")
                        )
                        consumed.add(candidate.id)
                        result.merged.append((candidate.id, keeper.id))
        for seed in records:
            if seed.id in consumed or seed.id in opts.pinned_ids or seed.valid_to is not None:
                continue
            group = [seed] + [
                r
                for r in records
                if r.id != seed.id
                and r.id not in consumed
                and r.id not in opts.pinned_ids
                and _compatible(seed, r)
                and similarity(seed.text, r.text) >= opts.similarity_threshold
            ]
            if len(group) < 2:
                continue
            if seed.kind == "note":
                if not (
                    opts.promote
                    and seed.category == opts.journal_category
                    and seed.trust == "user_explicit"
                    and len(group) >= opts.promotion_min_entries
                ):
                    continue
                # Repeated copies from one source are not independent episodes.
                episodes = {tuple(sorted(r.source_ids)) for r in group if r.source_ids}
                if len(episodes) < opts.promotion_min_entries:
                    continue
            ids = list(
                dict.fromkeys(
                    s for r in group for s in [r.id, f"consolidation:{r.id}", *r.source_ids]
                )
            )
            # Content words match; keep the fullest surface form. Originals remain audited.
            merged_text = max((r.text for r in group), key=len)
            if seed.kind == "note":
                keeper = await resolve(
                    self.store.remember(
                        scope,
                        RememberInput(
                            text=merged_text,
                            kind="fact",
                            category=seed.category,
                            source=seed.source,
                            source_ids=ids,
                            trust=seed.trust,
                            valid_from=seed.valid_from,
                            supersedes_id=seed.id,
                        ),
                    )
                )
                result.promoted.append(keeper.id)
            else:
                keeper = await resolve(
                    self.store.revise(
                        scope,
                        seed.id,
                        ReviseInput(
                            text=merged_text,
                            source_ids=ids,
                            expected_revision=seed.revision,
                            reason="consolidation: merge near-duplicate facts",
                        ),
                    )
                )
            consumed.update(r.id for r in group)
            for duplicate in group[1:]:
                await resolve(
                    self.store.forget(scope, duplicate.id, f"consolidated into {keeper.id}")
                )
                result.merged.append((duplicate.id, keeper.id))
        return result
