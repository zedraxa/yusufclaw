"""Dynamic tool selection for YusufClaw when tool count exceeds ~30 tools.

Implements RAG-MCP style tool filtering (Gan & Sun 2025, ARCHITECTURE-v2 Layer 7) to:
- Avoid prompt bloat and context degradation when hundreds of MCP tools are present.
- Retain guaranteed core/essential tools (always_include).
- Select top-k most relevant tools based on query token matching and description semantics.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from yusufclaw.models.types import ToolDefinition

WORD_PATTERN = re.compile(r"\b\w{2,}\b", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Tokenize and normalize text into lowercase tokens."""
    return [w.lower() for w in WORD_PATTERN.findall(text)]


class DynamicToolSelector:
    """Selects top-k relevant tools dynamically when the total count exceeds a threshold."""

    def __init__(
        self,
        threshold: int = 30,
        top_k: int = 10,
        always_include: set[str] | Sequence[str] | None = None,
    ) -> None:
        self.threshold = threshold
        self.top_k = top_k
        self.always_include: set[str] = set(always_include or [])

    def score_tool(self, query_tokens: list[str], tool: ToolDefinition) -> float:
        """Calculate a relevance score between query tokens and a tool definition."""
        if not query_tokens:
            return 0.0

        name_tokens = _tokenize(tool.name)
        desc_tokens = _tokenize(tool.description)

        # Include input schema parameter names if available
        param_tokens: list[str] = []
        if isinstance(tool.input_schema, dict):
            props = tool.input_schema.get("properties", {})
            if isinstance(props, dict):
                for p in props:
                    param_tokens.extend(_tokenize(str(p)))

        name_counts = Counter(name_tokens)
        desc_counts = Counter(desc_tokens)
        param_counts = Counter(param_tokens)

        score = 0.0
        query_counts = Counter(query_tokens)

        for token, q_count in query_counts.items():
            # Higher weight for matching tool name tokens (2.5x)
            n_hits = name_counts.get(token, 0)
            d_hits = desc_counts.get(token, 0)
            p_hits = param_counts.get(token, 0)

            if n_hits > 0 or d_hits > 0 or p_hits > 0:
                tf = (n_hits * 2.5) + (d_hits * 1.0) + (p_hits * 1.5)
                score += q_count * (1.0 + math.log1p(tf))

        return score

    def select(
        self,
        tools: Sequence[ToolDefinition],
        query: str,
        top_k: int | None = None,
    ) -> list[ToolDefinition]:
        """Select relevant tools for query if total tool count exceeds threshold."""
        tool_list = list(tools)
        if len(tool_list) <= self.threshold:
            return tool_list

        k = top_k if top_k is not None else self.top_k
        query_tokens = _tokenize(query)

        guaranteed: list[ToolDefinition] = []
        candidates: list[ToolDefinition] = []

        for t in tool_list:
            if t.name in self.always_include:
                guaranteed.append(t)
            else:
                candidates.append(t)

        # Score candidates
        scored: list[tuple[float, str, ToolDefinition]] = []
        for cand in candidates:
            sc = self.score_tool(query_tokens, cand)
            # Tuple: (-score, name, tool) for deterministic sorting
            scored.append((-sc, cand.name, cand))

        scored.sort(key=lambda item: (item[0], item[1]))

        # Take top k candidates
        selected_candidates = [item[2] for item in scored[:k]]

        # Maintain original relative ordering where possible
        selected_names = {t.name for t in guaranteed} | {t.name for t in selected_candidates}
        return [t for t in tool_list if t.name in selected_names]


def select_tools_for_query(
    tools: Sequence[ToolDefinition],
    query: str,
    threshold: int = 30,
    top_k: int = 10,
    always_include: set[str] | Sequence[str] | None = None,
) -> list[ToolDefinition]:
    """Convenience helper to select top-k tools for a query when tool count > threshold."""
    selector = DynamicToolSelector(
        threshold=threshold,
        top_k=top_k,
        always_include=always_include,
    )
    return selector.select(tools=tools, query=query)
