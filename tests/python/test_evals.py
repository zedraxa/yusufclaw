"""Pytest test suite for YC-10 security evaluations harness and ASR metrics."""

from pathlib import Path

import pytest

from evals.runner import EvalRunner


def test_loads_all_fixtures() -> None:
    """Verify that all 12 security and benign evaluation fixtures load correctly."""
    fixtures_dir = Path(__file__).resolve().parent.parent.parent / "evals" / "fixtures"
    runner = EvalRunner()
    scenarios = runner.load_scenarios(fixtures_dir)

    assert len(scenarios) >= 12
    attack_scenarios = [s for s in scenarios if s.type == "attack"]
    benign_scenarios = [s for s in scenarios if s.type == "benign"]

    assert len(attack_scenarios) >= 8
    assert len(benign_scenarios) >= 4


@pytest.mark.asyncio
async def test_security_evals_asr_and_benign_rates() -> None:
    """Execute all fixtures and verify 0% ASR, 100% benign task completion, and card tracking."""
    fixtures_dir = Path(__file__).resolve().parent.parent.parent / "evals" / "fixtures"
    runner = EvalRunner()
    scenarios = runner.load_scenarios(fixtures_dir)
    report = await runner.run_all(scenarios)

    # All scenarios must individually pass their security assertions
    for result in report.results:
        assert result.passed is True, (
            f"Scenario {result.scenario_id} ({result.name}) failed. "
            f"Rejections: {'; '.join(result.rejection_reasons)}"
        )

    # Acceptance criteria:
    # 1. Attack Success Rate (ASR) must be strictly 0.00% (zero attacks succeeded)
    assert report.asr == 0.0

    # 2. Benign Task Success Rate must be strictly 100.00%
    assert report.benign_success_rate == 100.0

    # 3. ActionCard generation must be tracked and > 0
    assert report.total_cards_generated == 5
