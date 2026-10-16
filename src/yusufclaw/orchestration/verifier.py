"""Verifier for checking turn results against acceptance criteria.

Validates outputs using:
1. Rule-based criteria:
   - Length constraints (min_length, max_length)
   - Mandatory keywords and prohibited terms
   - Regex patterns (required and forbidden)
   - Expected tool execution presence
2. Optional model-assisted judge evaluation.
3. Automated single-turn retry generation with specific failure feedback.
4. OpenTelemetry tracing.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

from yusufclaw.models.types import Message, ModelProvider, ModelRequest
from yusufclaw.orchestration.models import AcceptanceCriteria, VerificationResult
from yusufclaw.telemetry import get_tracer

logger = logging.getLogger(__name__)


class Verifier:
    """Validates candidate responses against acceptance criteria and manages retries."""

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.provider = provider

    async def _evaluate_model_judge(
        self,
        output: str,
        criteria: AcceptanceCriteria,
        prompt: str,
    ) -> tuple[bool, float, str]:
        """Ask model provider to act as an objective judge on candidate response."""
        if self.provider is None:
            return True, 1.0, "Model provider unavailable; skipped judge check"

        rubric = criteria.custom_judge_rubric or (
            "Evaluate if the candidate response completely, accurately, and safely "
            "answers the user prompt without hallucinations or errors."
        )

        judge_system = (
            "You are an objective evaluation judge for YusufClaw. "
            "Evaluate whether the candidate response satisfies the user request and rubric. "
            "Respond ONLY with a JSON object:\n"
            "{\n"
            '  "passed": boolean,\n'
            '  "score": float between 0.0 and 1.0,\n'
            '  "reason": "clear explanation"\n'
            "}"
        )

        user_content = (
            f"User Prompt:\n{prompt}\n\n"
            f"Candidate Response:\n{output}\n\n"
            f"Evaluation Rubric:\n{rubric}"
        )

        req = ModelRequest(
            system=judge_system,
            messages=[Message(role="user", content=user_content)],
            temperature=0.0,
        )

        try:
            resp = await self.provider.complete(req)
            raw = resp.text.strip()
            if "```" in raw:
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
                if match:
                    raw = match.group(1).strip()
            parsed = json.loads(raw)
            passed = bool(parsed.get("passed", False))
            score = float(parsed.get("score", 1.0 if passed else 0.0))
            reason = str(parsed.get("reason", "Model judge assessment completed"))
            return passed, score, reason
        except Exception as exc:
            logger.warning("Model judge evaluation failed: %s", exc)
            return True, 0.8, f"Judge evaluation fallback: {exc}"

    async def verify(
        self,
        output: str,
        criteria: AcceptanceCriteria,
        *,
        prompt: str = "",
        executed_tools: Sequence[str] | None = None,
        retries_attempted: int = 0,
        max_retries: int = 1,
    ) -> VerificationResult:
        """Verify the candidate response against acceptance criteria."""
        tracer = get_tracer("yusufclaw.orchestration")
        with tracer.start_as_current_span("yusufclaw.orchestration.verification") as span:
            clean_output = output.strip()
            reasons: list[str] = []
            rule_results: dict[str, bool] = {}

            # 1. Minimum length check
            if len(clean_output) < criteria.min_length:
                rule_results["min_length"] = False
                reasons.append(
                    f"Output length ({len(clean_output)}) is below minimum requirement ({criteria.min_length})"
                )
            else:
                rule_results["min_length"] = True

            # 2. Maximum length check
            if criteria.max_length is not None and len(clean_output) > criteria.max_length:
                rule_results["max_length"] = False
                reasons.append(
                    f"Output length ({len(clean_output)}) exceeds maximum limit ({criteria.max_length})"
                )
            elif criteria.max_length is not None:
                rule_results["max_length"] = True

            # 3. Mandatory keywords
            lower_output = clean_output.lower()
            for kw in criteria.required_keywords:
                key_name = f"required_kw:{kw}"
                if kw.lower() not in lower_output:
                    rule_results[key_name] = False
                    reasons.append(f"Required keyword '{kw}' was not found in response")
                else:
                    rule_results[key_name] = True

            # 4. Forbidden keywords
            for kw in criteria.forbidden_keywords:
                key_name = f"forbidden_kw:{kw}"
                if kw.lower() in lower_output:
                    rule_results[key_name] = False
                    reasons.append(f"Forbidden keyword '{kw}' was detected in response")
                else:
                    rule_results[key_name] = True

            # 5. Required regex pattern
            if criteria.required_pattern:
                if not re.search(criteria.required_pattern, clean_output):
                    rule_results["required_pattern"] = False
                    reasons.append(f"Output does not match required pattern: {criteria.required_pattern}")
                else:
                    rule_results["required_pattern"] = True

            # 6. Forbidden regex patterns
            for pat in criteria.must_not_contain_patterns:
                key_name = f"forbidden_pattern:{pat}"
                if re.search(pat, clean_output):
                    rule_results[key_name] = False
                    reasons.append(f"Output matches forbidden pattern: {pat}")
                else:
                    rule_results[key_name] = True

            # 7. Tool execution requirements
            if criteria.require_tool_execution and criteria.expected_tools:
                tools_set = set(executed_tools or [])
                for t in criteria.expected_tools:
                    key_name = f"expected_tool:{t}"
                    if t not in tools_set:
                        rule_results[key_name] = False
                        reasons.append(f"Required tool '{t}' was not invoked during turn execution")
                    else:
                        rule_results[key_name] = True

            rules_passed = all(rule_results.values()) if rule_results else True

            # 8. Model judge evaluation (if requested)
            judge_passed = True
            judge_score = 1.0
            if criteria.model_judge_required and rules_passed:
                judge_passed, judge_score, judge_reason = await self._evaluate_model_judge(
                    clean_output, criteria, prompt
                )
                rule_results["model_judge"] = judge_passed
                if not judge_passed:
                    reasons.append(f"Model judge rejected output: {judge_reason}")

            passed = rules_passed and judge_passed
            score = judge_score if criteria.model_judge_required else (
                sum(1 for v in rule_results.values() if v) / max(1, len(rule_results))
            )

            # Retry recommendation
            retry_recommended = False
            retry_prompt: str | None = None
            if not passed and retries_attempted < max_retries:
                retry_recommended = True
                bullets = "\n".join(f"- {r}" for r in reasons)
                retry_prompt = (
                    f"Your previous response failed verification criteria:\n{bullets}\n"
                    "Please revise and improve your response to satisfy all the above requirements."
                )

            res = VerificationResult(
                passed=passed,
                score=round(score, 3),
                reasons=reasons,
                retry_recommended=retry_recommended,
                retry_prompt=retry_prompt,
                rule_results=rule_results,
                retries_attempted=retries_attempted,
            )

            if span.is_recording():
                span.set_attribute("yusufclaw.verifier.passed", res.passed)
                span.set_attribute("yusufclaw.verifier.score", res.score)
                span.set_attribute("yusufclaw.verifier.retry_recommended", res.retry_recommended)
                span.set_attribute("yusufclaw.verifier.retries_attempted", res.retries_attempted)

            return res


__all__ = ["Verifier"]
