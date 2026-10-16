# YusufClaw Architecture v2 (synthesis of 3 reports)
Sources: docs/RESEARCH.md (Codex), docs/RESEARCH-ASIST.md (Asist, YC-01..30), Yusuf's "AI Agent Sistemleri 2026" report.

## Decisions
- Keep TypeScript/pnpm monorepo (74 tests, working). Do NOT switch to Python/LangGraph; borrow ideas: durable checkpoints + interrupts (LangGraph), subagents/skills/filesystem (Deep Agents).
- Security first, before any channel goes live: YC-01,02,03,05,10 (deterministic policy engine, taint tracking, action cards generated from the real action, SSRF/secret-file denial, evals in CI).
- Start single agent + tools; add orchestration adaptively, not a fixed team (Google 2026: multi-agent helps parallel tasks, hurts sequential ones).

## Layers
1. Interfaces: CLI, web (AG-UI 1.0 event stream: progress, tool calls, approvals), Telegram; later WhatsApp/voice. All via one gateway.
2. Meta-orchestrator: task analyzer (complexity, dependency, risk, cost) -> single agent | parallel subagents | DAG. Budget-aware (token/$ caps per task; AWS $50/mo). Model routing: strong model for planning/synthesis, cheap/local (Ollama) for classify/search/transform.
3. Subagents: created on demand, own context, least-privilege tools, read-only by default; writers (agy/codex delegates) run in git worktrees with test gate + approved merge (YC-24).
4. Shared coordination substrate: append-only event log + artifact store + FTS/BM25 search (ScaleMAS-style publish/search/visit); citation count never treated as truth, a verifier checks evidence.
5. Memory (SQLite now, DynamoDB/pgvector later): working, episodic, semantic (claims with source/time/scope/history), procedural (skills), shared team. Pipeline event->claim->lesson + nightly consolidation; revise and real forget incl. indexes/summaries (acceptance tests). Graph memory deferred.
6. Skills: task -> solve -> test -> extract method -> skill -> retest -> version (git). Pinning + quarantine for MCP/skill packages.
7. Tools: MCP (2026-07-28 spec); A2A 1.0 for agent-to-agent later; dynamic tool selection when tools > ~30.
8. Verification & governance: verifier with acceptance criteria, rootless-container sandbox, permissions, approvals, reversible action log, OpenTelemetry tracing (tokens, latency, errors per agent).
9. Evals: evals/ runner with security fixtures (attack success rate), harmless success, card count; track failure modes from "Why Do Multi-Agent LLM Systems Fail?".

## Roadmap
P0 (now): security sprint + memory phase3 (both running) -> merge.
P1: Node LTS upgrade; AG-UI web events; OTel tracing; task analyzer + budget manager; verifier; skill lifecycle; Telegram live with approval cards.
P2: dynamic subagent teams + shared substrate; writer delegates; A2A; WhatsApp/voice; AWS deploy (only with Yusuf's approval).
