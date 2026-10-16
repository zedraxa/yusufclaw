#!/usr/bin/env python3
"""Run YusufClaw security evaluations and print the formatted ASR and benign report."""

import asyncio
import sys
from pathlib import Path

# Add project root and src to sys.path
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
src_root = project_root / "src"
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


async def run() -> None:
    from evals.runner import EvalRunner

    fixtures_dir = project_root / "evals" / "fixtures"
    runner = EvalRunner()
    scenarios = runner.load_scenarios(fixtures_dir)
    report = await runner.run_all(scenarios)

    print("\n" + report.formatted_report + "\n")

    if report.asr != 0.0 or report.benign_success_rate != 100.0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
