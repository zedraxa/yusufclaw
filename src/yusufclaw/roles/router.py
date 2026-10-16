"""Task-to-role routing engine using orchestration task analysis.

Analyzes incoming user tasks via TaskAnalyzer and routes them to the best-matching
specialist role(s) based on domain intent, required tools, risk, and subtasks.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from yusufclaw.models.types import ModelProvider
from yusufclaw.orchestration.models import TaskAnalysis
from yusufclaw.orchestration.task_analyzer import TaskAnalyzer
from yusufclaw.roles.loader import RoleCatalog, get_role_catalog
from yusufclaw.roles.models import CapabilityMatrix, RoleRoutingDecision
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)


class RoleRouter:
    """Intelligent router matching incoming tasks to specialist roles using TaskAnalyzer."""

    DOMAIN_PATTERNS: ClassVar[dict[str, list[str]]] = {
        "coder": [
            r"\bcode\b",
            r"\bpython\b",
            r"\btypescript\b",
            r"\brust\b",
            r"\bjavascript\b",
            r"\brefactor\b",
            r"\bgit\b",
            r"\bcommit\b",
            r"\bpull request\b",
            r"\bpr\b",
            r"\bbug\b",
            r"\btest\b",
            r"\bpytest\b",
            r"\bunit test\b",
            r"\blint\b",
            r"\brepo\b",
            r"\brepository\b",
            r"\bkod\b",
            r"\byazılım\b",
            r"\bhata ayıkla\b",
            r"\bprogramla\b",
        ],
        "writer": [
            r"\bdraft\b",
            r"\bwrite\s+(?:a\s+)?(?:document|doc|report|summary|email draft|memo)\b",
            r"\bdocumentation\b",
            r"\breport\b",
            r"\bexecutive summary\b",
            r"\bessay\b",
            r"\breadme\b",
            r"\bblog post\b",
            r"\barticle\b",
            r"\bcopy-edit\b",
            r"\bmetin\s+yaz\b",
            r"\brapor\b",
            r"\bdoküman\b",
            r"\btaslak\b",
        ],
        "mail": [
            r"\bemail\b",
            r"\binbox\b",
            r"\btriage\b",
            r"\breply\s+to\s+email\b",
            r"\bmail\b",
            r"\bunread\s+messages\b",
            r"\bspam\b",
            r"\beposta\b",
            r"\be-posta\b",
            r"\bgelen\s+kutusu\b",
            r"\bmailleri\s+özetle\b",
        ],
        "browser": [
            r"\bbrowse\b",
            r"\bwebsite\b",
            r"\bweb\s+page\b",
            r"\bscrape\b",
            r"\bnavigate\s+to\b",
            r"\bform\b",
            r"\bclick\s+on\b",
            r"\bfill\s+in\b",
            r"\bplaywright\b",
            r"\bscreenshot\b",
            r"\btarayıcı\b",
            r"\bweb\s+sitesi\b",
            r"\bsayfayı\s+aç\b",
        ],
        "researcher": [
            r"\bpaper\b",
            r"\barxiv\b",
            r"\bresearch\b",
            r"\bliterature\b",
            r"\bscientific\b",
            r"\bcitation\b",
            r"\bpeer\s+review\b",
            r"\bscholar\b",
            r"\bakademik\b",
            r"\bmakale\s+araştır\b",
            r"\bderin\s+araştırma\b",
            r"\bliteratür\b",
        ],
        "ops": [
            r"\baws\b",
            r"\binfra\b",
            r"\binfrastructure\b",
            r"\bcdk\b",
            r"\bcloud\b",
            r"\bdeploy\b",
            r"\bdeployment\b",
            r"\bterraform\b",
            r"\bs3\b",
            r"\blambda\b",
            r"\bdocker\b",
            r"\bkubernetes\b",
            r"\bdevops\b",
            r"\bsunucu\b",
            r"\baltyapı\b",
            r"\byayınla\b",
        ],
        "scheduler": [
            r"\bschedule\b",
            r"\bcalendar\b",
            r"\bmeeting\b",
            r"\breminder\b",
            r"\balarm\b",
            r"\bevent\b",
            r"\bappointment\b",
            r"\btomorrow\s+at\b",
            r"\btakvim\b",
            r"\bhatırlatıcı\b",
            r"\brandevu\b",
            r"\btoplantı\b",
            r"\betkinlik\b",
        ],
    }

    def __init__(
        self,
        catalog: RoleCatalog | None = None,
        analyzer: TaskAnalyzer | None = None,
        provider: ModelProvider | None = None,
    ) -> None:
        self.catalog = catalog or get_role_catalog()
        self.analyzer = analyzer or TaskAnalyzer(provider=provider)

    def route_from_analysis(self, prompt: str, analysis: TaskAnalysis) -> RoleRoutingDecision:
        """Deterministically determine the primary and secondary roles given a TaskAnalysis."""
        lower = prompt.lower()
        domain_scores: dict[str, int] = {role_id: 0 for role_id in self.DOMAIN_PATTERNS}

        # 1. Score matching domain regex patterns
        for role_id, patterns in self.DOMAIN_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, lower):
                    domain_scores[role_id] += 2

        # 2. Correlate with tools detected by TaskAnalyzer
        if "shell" in analysis.required_tools or "file_write" in analysis.required_tools:
            domain_scores["coder"] += 2
        if "web_fetch" in analysis.required_tools:
            domain_scores["browser"] += 1
            domain_scores["researcher"] += 1
        if "memory" in analysis.required_tools:
            domain_scores["scheduler"] += 1

        # 3. Consider risk level
        if analysis.risk in ("high", "critical"):
            if domain_scores["ops"] > 0:
                domain_scores["ops"] += 2
            elif domain_scores["coder"] > 0:
                domain_scores["coder"] += 1

        # Determine highest scoring specialist domain
        sorted_domains = sorted(domain_scores.items(), key=lambda kv: kv[1], reverse=True)
        top_domain, top_score = sorted_domains[0]

        # Multi-role / delegation case
        if analysis.strategy == "delegate" or len(analysis.subtasks) >= 2:
            # When multiple steps or delegating, orchestrator coordinates subagents
            orchestrator = self.catalog.get_role("orchestrator")
            secondaries: list[CapabilityMatrix] = []
            for dom, score in sorted_domains:
                if score > 0:
                    try:
                        role_obj = self.catalog.get_role(dom)
                        if role_obj.role_id != orchestrator.role_id:
                            secondaries.append(role_obj)
                    except KeyError:
                        pass

            reason = (
                f"Multi-step/delegation task ({len(analysis.subtasks)} subtasks). "
                f"Orchestrator assigned as master coordinator with specialists: "
                f"{[s.role_id for s in secondaries]}."
            )
            return RoleRoutingDecision(
                primary_role=orchestrator,
                secondary_roles=secondaries,
                confidence=0.95,
                reasoning=reason,
                task_analysis=analysis,
            )

        # Single specialist match
        if top_score > 0:
            primary = self.catalog.get_role(top_domain)
            confidence = min(0.99, 0.60 + (top_score * 0.08))
            reason = (
                f"Matched specialist domain '{top_domain}' (score={top_score}) "
                f"based on intent keywords and tools {analysis.required_tools}."
            )
            return RoleRoutingDecision(
                primary_role=primary,
                secondary_roles=[],
                confidence=confidence,
                reasoning=reason,
                task_analysis=analysis,
            )

        # Fallback to general orchestrator assistant
        primary = self.catalog.get_role("orchestrator")
        reason = "General query or conversational request; assigned to Orchestrator assistant."
        return RoleRoutingDecision(
            primary_role=primary,
            secondary_roles=[],
            confidence=0.85,
            reasoning=reason,
            task_analysis=analysis,
        )

    async def route(self, prompt: str, *, model_assisted: bool = False) -> RoleRoutingDecision:
        """Route the user task to specialist role(s) with OpenTelemetry tracing span."""
        tracer = get_tracer("yusufclaw.roles")
        with tracer.start_as_current_span("yusufclaw.roles.route") as span:
            analysis = await self.analyzer.analyze(prompt, model_assisted=model_assisted)
            decision = self.route_from_analysis(prompt, analysis)

            if span.is_recording():
                span.set_attribute("yusufclaw.roles.primary", decision.primary_role.role_id)
                span.set_attribute("yusufclaw.roles.confidence", decision.confidence)
                span.set_attribute(
                    "yusufclaw.roles.secondaries",
                    [s.role_id for s in decision.secondary_roles],
                )

            return decision


__all__ = ["RoleRouter"]
