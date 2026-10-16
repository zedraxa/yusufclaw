# YusufClaw Python Scaffold (Step 0)

YusufClaw is migrating from TypeScript to Python 3.12 using `uv`, `LangGraph`, `FastAPI`, `Pydantic-Settings`, and `Typer`.

This repository maintains the existing TypeScript implementation in `packages/` as the canonical reference while building the target Python architecture specified in [`docs/ARCHITECTURE-v2.md`](file:///home/yusuf/projects/wt/yc-py/docs/ARCHITECTURE-v2.md) and [`SPEC.md`](file:///home/yusuf/projects/wt/yc-py/SPEC.md).

---

## 1. Architecture & Directory Layout

```
.
├── docker-compose.dev.yml        # PostgreSQL (pgvector) + Redis dev services
├── pyproject.toml                # Project metadata, dependencies, ruff, mypy, pytest config
├── uv.lock                       # Deterministic dependency lockfile
├── PYTHON-SCAFFOLD.md            # Scaffold documentation and execution guide
├── src/
│   └── yusufclaw/
│       ├── __init__.py           # Package version (0.1.0) and top-level exports
│       ├── config.py             # Pydantic-settings config (env vars, .env, limits)
│       ├── agent.py              # LangGraph state machine (model node + tool node)
│       ├── models/               # Model provider interfaces and implementations
│       │   ├── __init__.py       # Provider factory (get_model_provider)
│       │   ├── types.py          # Message, ToolCall, ToolDefinition, ModelRequest/Response
│       │   ├── fake.py           # Deterministic FakeProvider for tests and offline CLI
│       │   ├── ollama.py         # Ollama HTTP client (/api/chat)
│       │   ├── openai.py         # OpenAI Chat Completions provider
│       │   └── bedrock.py        # AWS Bedrock Converse API via boto3 (profile support)
│       ├── gateway/              # Multi-channel gateway adapters
│       │   ├── __init__.py       # Gateway architecture docstring
│       │   ├── cli.py            # Typer CLI entrypoint (`yusufclaw` command)
│       │   ├── web.py            # FastAPI app with GET /health
│       │   └── telegram.py       # Telegram bot adapter placeholder and docstring
│       ├── security/             # Security policy engine, taint tracking, action cards
│       ├── memory/               # Structured durable memory (SQLite FTS5 + pgvector)
│       ├── tools/                # Sandboxed tools (shell, files, web, browser, MCP)
│       ├── skills/               # Local markdown skill parsing and lifecycle
│       ├── routines/             # Scheduled cron routines and background workers
│       ├── telemetry/            # OpenTelemetry tracing and metrics setup
│       └── queue/                # Redis list-backed async queue abstraction
└── tests/
    ├── python/                   # Python test suite (offline with fakes)
    │   ├── test_agent.py         # LangGraph loop, tool execution, step limits
    │   ├── test_cli.py           # Typer CLI turn, chat, version, and help tests
    │   ├── test_config.py        # Pydantic-settings and environment overrides
    │   ├── test_models.py        # Mocked unit tests for Fake, Ollama, OpenAI, Bedrock
    │   ├── test_subpackages.py   # Docstrings and subpackage interface verification
    │   └── test_web.py           # FastAPI /health endpoint test via AsyncClient
    └── *.test.ts                 # Untouched TypeScript Vitest reference tests
```

---

## 2. Core Components

### 2.1 Configuration (`yusufclaw.config.Settings`)
Powered by `pydantic-settings`. Loads environment variables and `.env` files automatically.
Key settings:
- `model_provider`: `"fake"` (default in tests/scaffold), `"ollama"`, `"openai"`, or `"bedrock"`.
- `aws_profile`: AWS profile for Bedrock (default: `"yusufclaw"`).
- `aws_region`: AWS region for Bedrock (default: `"us-east-1"`).
- `bedrock_model_id`: Model ID or inference profile ARN for Bedrock Converse API.
- `ollama_base_url` & `ollama_model`: Ollama server configuration.
- `openai_base_url` & `openai_api_key` & `openai_model`: OpenAI-compatible completions.
- `redis_url`: Redis connection string (default: `"redis://127.0.0.1:6379/0"`).
- `database_url`: PostgreSQL connection string (default: `"postgresql://postgres:postgres@127.0.0.1:5432/yusufclaw"`).
- `max_agent_steps`: Maximum turns in the tool execution loop (default: `8`).

### 2.2 Model Providers (`yusufclaw.models`)
All providers conform to `ModelProvider.complete(request: ModelRequest) -> ModelResponse`:
- **`FakeProvider`**: Deterministic test provider supporting request recording and sequential canned responses.
- **`OllamaProvider`**: Asynchronous HTTP client communicating with Ollama's `/api/chat`.
- **`OpenAIProvider`**: Communicates with OpenAI-compatible chat completion endpoints.
- **`BedrockProvider`**: Interfaces with AWS Bedrock Converse API via `boto3`. Initialized lazily; supports profile selection and message coalescing for tool results.
- **`get_model_provider()`**: Factory returning the configured provider instance.

### 2.3 Minimal LangGraph Agent Loop (`yusufclaw.agent.Agent`)
Constructs an asynchronous `StateGraph`:
1. **`model_node`**: Normalizes conversation state into `ModelRequest` and invokes the model provider.
2. **`should_continue` conditional edge**: Checks if the model output contains tool calls and if step count remains below `max_steps`. If yes, transitions to `tool_node`; otherwise transitions to `END`.
3. **`tool_node`**: Invokes configured tool executors for each tool call and creates `ToolMessage` results.
Clean interface:
```python
agent = Agent(provider=provider, tools=tools, tool_executor=executor)
response: str = await agent.run("Calculate sum of 10 and 32")
```

### 2.4 FastAPI Web Gateway (`yusufclaw.gateway.web`)
Exposes `GET /health` with system status, version, and active model provider. Configured with OpenTelemetry lifespan hooks.

### 2.5 Typer CLI (`yusufclaw.gateway.cli`)
Registered as the `yusufclaw` executable entrypoint via `pyproject.toml`.
Commands:
- `yusufclaw turn "<message>"`: Runs a single turn and prints output.
- `yusufclaw chat`: Runs a single or interactive turn.
- `yusufclaw version`: Prints application version.

---

## 3. How to Run

### 3.1 Install Dependencies
Ensure Python 3.12 is installed, then run:
```bash
uv sync
```

### 3.2 Run Linters and Type Checking
```bash
# Linting & Formatting Check (Ruff)
uv run ruff check

# Strict Static Type Checking (Mypy)
uv run mypy src
```

### 3.3 Run Offline Test Suite
```bash
uv run pytest
```
All tests execute completely offline with mocked providers and zero network or AWS calls.

### 3.4 Run CLI Single Turn (Offline Fake)
```bash
uv run yusufclaw turn "Hello YusufClaw" --provider fake
```
Output:
```
YusufClaw (fake): Received your message 'Hello YusufClaw'.
```

### 3.5 Run Web Gateway Locally
```bash
uv run uvicorn yusufclaw.gateway.web:app --host 127.0.0.1 --port 3000 --reload
```
Test health check:
```bash
curl http://127.0.0.1:3000/health
# {"status":"ok","version":"0.1.0","service":"yusufclaw","model_provider":"fake"}
```

### 3.6 Run Local Development Services
Start PostgreSQL with `pgvector` and Redis:
```bash
docker compose -f docker-compose.dev.yml up -d
```
Stop services:
```bash
docker compose -f docker-compose.dev.yml down
```

---

## 4. Next Migration Steps (Porting from TypeScript)
1. **Security Policy Engine (`src/yusufclaw/security/`)**: Port deterministic taint tracking, action card approval schemas, SSRF and secret-file protections from TS `packages/tools` and test suites.
2. **Durable Structured Memory (`src/yusufclaw/memory/`)**: Port SQLite FTS5 BM25 memory, scope isolation, revision tracking, tombstone deletions, and pgvector embeddings from TS `packages/core/src/memory.ts`.
3. **Sandboxed Tools (`src/yusufclaw/tools/`)**: Port shell execution, filesystem tools, web fetch with SSRF guardrails, and browser automation.
4. **Channels & Subagents**: Port Telegram bot adapter with interactive approval cards and AG-UI event streaming.
