"""Task analyzer for classifying incoming requests.

Implements:
1. Deterministic heuristic classification:
   - Evaluates text length, step conjunctions, and numbered lists.
   - Identifies candidate tools (shell, files, fetch, memory).
   - Determines risk level (low, medium, high, critical).
   - Decides whether a plan-and-execute or delegation strategy is required.
2. Optional model-assisted mode using the existing ModelProvider interface with
   safe fallback to the deterministic heuristic.
3. OpenTelemetry distributed tracing spans.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from yusufclaw.models.router import ModelRole, ModelRouter
from yusufclaw.models.types import Message, ModelProvider, ModelRequest, ToolDefinition
from yusufclaw.orchestration.models import (
    ComplexityLevel,
    OrchestrationStrategy,
    RiskLevel,
    TaskAnalysis,
)
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)

# Tool identification patterns
TOOL_PATTERNS: dict[str, list[str]] = {
    "shell": [
        "shell",
        "bash",
        "terminal",
        "command",
        "run ",
        "execute",
        "pip ",
        "npm ",
        "git ",
        "komut",
        "konsol",
    ],
    "file_read": [
        "read file",
        "view file",
        "cat ",
        "show file",
        "dosya oku",
        "dosyayı göster",
    ],
    "file_write": [
        "write file",
        "save file",
        "save to",
        "create file",
        "edit file",
        "modify file",
        "dosya yaz",
        "dosyaya kaydet",
        "dosya oluştur",
    ],
    "file_list": [
        "list files",
        "ls ",
        "directory",
        "dosyaları listele",
        "dizin",
    ],
    "web_fetch": [
        "http://",
        "https://",
        "fetch",
        "download",
        "scrape",
        "curl ",
        "api call",
        "web",
        "url",
        "indir",
    ],
    "memory": [
        "remember",
        "recall",
        "forget",
        "hatırla",
        "not al",
        "hafıza",
        "kaydet",
        "unut",
    ],
}

# Risk indicators
CRITICAL_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bdrop\s+table\b",
    r"\bdrop\s+database\b",
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"curl\s+.*\|\s*bash",
    r"\beval\(",
    r"\bshutdown\b",
    r"\breboot\b",
]

HIGH_RISK_PATTERNS = [
    r"\bdelete\b",
    r"\brm\s+",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bsudo\b",
    r"\bkill\s+-9\b",
    r"\btruncate\b",
    r"\btoken\b",
    r"\bsecret\b",
    r"\bpassword\b",
    r"\bprivate_key\b",
    r"\bid_rsa\b",
]

MEDIUM_RISK_PATTERNS = [
    r"\bwrite\b",
    r"\bedit\b",
    r"\bupdate\b",
    r"\bpost\b",
    r"\bupload\b",
    r"\boverwrite\b",
    r"\bcurl\b",
    r"\bfetch\b",
    r"\bbash\b",
    r"\bexecute\b",
]

# Sequential / multi-step conjunctions
STEP_CONJUNCTIONS = [
    " then ",
    " after that ",
    " afterwards ",
    " and then ",
    " followed by ",
    " ve ardından ",
    " sonra ",
    " daha sonra ",
    " ilk olarak ",
    " birinci olarak ",
]

DELEGATION_PATTERNS = [
    r"\bdelegate\b",
    r"\bsubagent\b",
    r"\bin parallel\b",
    r"\bbackground worker\b",
    r"\balt ajan\b",
    r"\bparalel olarak\b",
    r"\bişçiye devret\b",
]


class TaskAnalyzer:
    """Classifies incoming user tasks into complexity, risk, tools, and orchestration strategy."""

    def __init__(
        self,
        provider: ModelProvider | None = None,
        available_tools: Sequence[ToolDefinition | str] | None = None,
        router: ModelRouter | None = None,
    ) -> None:
        self.provider = provider
        self.router = router
        self.available_tool_names: list[str] = []
        if available_tools:
            for t in available_tools:
                if isinstance(t, str):
                    self.available_tool_names.append(t)
                elif hasattr(t, "name"):
                    self.available_tool_names.append(t.name)

    def analyze_heuristic(self, prompt: str) -> TaskAnalysis:
        """Determines task classification using deterministic pattern-matching heuristics."""
        text = prompt.strip()
        lower = text.lower()

        # 1. Tool Identification
        detected_tools: list[str] = []
        for tool_name, patterns in TOOL_PATTERNS.items():
            if any(p in lower for p in patterns):
                detected_tools.append(tool_name)

        # 2. Risk Assessment
        risk: RiskLevel = "low"
        if any(re.search(pat, lower) for pat in CRITICAL_PATTERNS):
            risk = "critical"
        elif any(re.search(pat, lower) for pat in HIGH_RISK_PATTERNS):
            risk = "high"
        elif any(re.search(pat, lower) for pat in MEDIUM_RISK_PATTERNS):
            risk = "medium"

        # 3. Multi-step extraction
        subtasks: list[str] = []
        # Numbered items: 1. ... 2. ...
        numbered_matches = re.findall(
            r"(?:^|\n|\s)(?:\d+[\.)]|\badım\s*\d+:?|\bstep\s*\d+:?)\s*([^\n;]+)",
            text,
            flags=re.IGNORECASE,
        )
        if numbered_matches:
            subtasks = [m.strip() for m in numbered_matches if m.strip()]

        # If no numbered list found, check sequential conjunctions
        if not subtasks:
            for conj in STEP_CONJUNCTIONS:
                if conj in lower:
                    parts = re.split(re.escape(conj), text, flags=re.IGNORECASE)
                    if len(parts) > 1:
                        subtasks = [p.strip() for p in parts if p.strip()]
                        break

        # Semicolon separated commands / instructions
        if not subtasks and ";" in text and len(detected_tools) >= 2:
            parts = [p.strip() for p in text.split(";") if p.strip()]
            if len(parts) > 1:
                subtasks = parts

        has_multi_steps = len(subtasks) >= 2

        # 4. Complexity Assessment
        complexity: ComplexityLevel = "simple"
        if has_multi_steps or len(detected_tools) >= 3 or len(text) > 400:
            complexity = "complex"
        elif len(detected_tools) >= 1 or len(text) > 120 or risk in ("medium", "high"):
            complexity = "medium"

        # 5. Delegation / Planning Strategy
        has_delegation_intent = any(re.search(pat, lower) for pat in DELEGATION_PATTERNS)
        delegate_required = False
        plan_required = False

        if has_delegation_intent:
            delegate_required = True
            strategy: OrchestrationStrategy = "delegate"
        elif complexity == "complex":
            # If multiple distinct tools or subtasks >= 3, delegate
            if len(subtasks) >= 3 or (len(detected_tools) >= 2 and len(subtasks) >= 2):
                delegate_required = True
                strategy = "delegate"
            else:
                plan_required = True
                strategy = "plan_and_execute"
        elif complexity == "medium" and has_multi_steps:
            plan_required = True
            strategy = "plan_and_execute"
        else:
            strategy = "direct"

        estimated_steps = max(1, len(subtasks)) if subtasks else (3 if complexity == "complex" else 1)

        reasoning = (
            f"Classified as {complexity} (risk={risk}, strategy={strategy}). "
            f"Detected tools: {detected_tools or 'none'}; subtasks count: {len(subtasks)}."
        )

        return TaskAnalysis(
            complexity=complexity,
            required_tools=detected_tools,
            risk=risk,
            plan_required=plan_required,
            delegate_required=delegate_required,
            strategy=strategy,
            reasoning=reasoning,
            subtasks=subtasks,
            estimated_steps=estimated_steps,
        )

    async def analyze_model(self, prompt: str) -> TaskAnalysis:
        """Classifies the task using the model provider, falling back to heuristics on error."""
        active_provider = self.provider
        if self.router is not None:
            active_provider = self.router.get_provider(ModelRole.CLASSIFY)

        if active_provider is None:
            return self.analyze_heuristic(prompt)

        system_instruction = (
            "You are the task classification engine for YusufClaw. "
            "Analyze the given user task and return a JSON object with strictly these keys:\n"
            "{\n"
            '  "complexity": "simple" | "medium" | "complex",\n'
            '  "required_tools": ["tool_name", ...],\n'
            '  "risk": "low" | "medium" | "high" | "critical",\n'
            '  "plan_required": boolean,\n'
            '  "delegate_required": boolean,\n'
            '  "strategy": "direct" | "plan_and_execute" | "delegate",\n'
            '  "reasoning": "brief explanation",\n'
            '  "subtasks": ["subtask 1", ...],\n'
            '  "estimated_steps": integer\n'
            "}\n"
            "Return valid JSON only, without commentary."
        )

        req = ModelRequest(
            system=system_instruction,
            messages=[Message(role="user", content=prompt)],
            temperature=0.0,
        )

        try:
            resp = await active_provider.complete(req)
            raw_text = resp.text.strip()
            # Handle markdown code blocks
            if "```" in raw_text:
                json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text)
                if json_match:
                    raw_text = json_match.group(1).strip()

            parsed = json.loads(raw_text)
            return TaskAnalysis(
                complexity=parsed.get("complexity", "medium"),
                required_tools=parsed.get("required_tools", []),
                risk=parsed.get("risk", "low"),
                plan_required=bool(parsed.get("plan_required", False)),
                delegate_required=bool(parsed.get("delegate_required", False)),
                strategy=parsed.get("strategy", "direct"),
                reasoning=parsed.get("reasoning", "Model-assisted classification"),
                subtasks=parsed.get("subtasks", []),
                estimated_steps=int(parsed.get("estimated_steps", 1)),
            )
        except Exception as exc:
            logger.warning("Model-assisted task analysis failed, falling back to heuristic: %s", exc)
            analysis = self.analyze_heuristic(prompt)
            analysis.reasoning += f" (Model assist fallback due to: {exc})"
            return analysis

    async def analyze(self, prompt: str, *, model_assisted: bool = False) -> TaskAnalysis:
        """Analyze the task and record OpenTelemetry telemetry span."""
        tracer = get_tracer("yusufclaw.orchestration")
        with tracer.start_as_current_span("yusufclaw.orchestration.task_analysis") as span:
            has_provider = self.provider is not None or self.router is not None
            if model_assisted and has_provider:
                analysis = await self.analyze_model(prompt)
            else:
                analysis = self.analyze_heuristic(prompt)

            if span.is_recording():
                span.set_attribute("yusufclaw.task.complexity", analysis.complexity)
                span.set_attribute("yusufclaw.task.risk", analysis.risk)
                span.set_attribute("yusufclaw.task.strategy", analysis.strategy)
                span.set_attribute("yusufclaw.task.plan_required", analysis.plan_required)
                span.set_attribute("yusufclaw.task.delegate_required", analysis.delegate_required)
                span.set_attribute("yusufclaw.task.estimated_steps", analysis.estimated_steps)
                if self.router is not None and model_assisted and has_provider:
                    _, p_name, _ = self.router.route(ModelRole.CLASSIFY)
                    span.set_attribute("yusufclaw.routing.role", ModelRole.CLASSIFY.value)
                    span.set_attribute("yusufclaw.routing.provider", p_name)

            return analysis


__all__ = ["TaskAnalyzer"]
