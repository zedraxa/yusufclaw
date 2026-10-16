"""Security subpackage for YusufClaw.

Implements security policies, taint tracking, and approval mechanisms:
- Deterministic policy engine: pre-execution validation of actions.
- Taint tracking: tracks data provenance across agent turns.
- Action cards: human-in-the-loop approval cards generated from concrete actions.
- Access control: SSRF prevention, path traversal defense, and secret-file denial.
- Allowlist verification: channel and user authorization checks.

Note: Logic will be ported from TypeScript packages/tools and tests.
"""

__all__: list[str] = []
