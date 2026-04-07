# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AID Work Agent is an enterprise intelligent agent system (企业员工智能代理系统) built for handling daily enterprise employee tasks via conversational AI. It uses Chinese domestic LLM providers (Qwen/DashScope and ZhipuAI) and supports multi-channel access (WeCom, DingTalk, Feishu). The system is multi-tenant and supports hundreds of concurrent users.

**Design principles**: Stability and predictability over creativity. Professional, concise responses. When uncertain, honestly admit it. No humor, no entertainment, no academic speculation. Sensitive information must be encrypted and never returned in plaintext to users.

## Commands

### Backend
```bash
pip install -r requirements.txt          # Install dependencies
python -m src.main                       # Start FastAPI server (port 8000)
CLI_MODE=true python -m src.main         # CLI chat mode (no web server)
python gradio_app.py                     # Gradio debug UI (port 7860)
gunicorn -c deploy/gunicorn.conf.py src.main:app  # Production server
```

### Frontend
```bash
cd frontend && npm install               # Install frontend deps
cd frontend && npm run dev               # Dev server (port 5173)
cd frontend && npm run build             # Production build
```

### Testing
```bash
pytest tests/                            # Run all tests
pytest tests/test_agent.py -v            # Run single test file
pytest tests/ --asyncio-mode=auto        # Required for async tests
```

### Docker
```bash
docker compose up -d                     # Dev environment
docker compose -f docker-compose.prod.yml up -d --build  # Production
```

## Architecture

### Request Flow
```
User/Channel (WeCom, DingTalk, Feishu, Web)
  → FastAPI (src/main.py) — HTTP routes, SSE streaming
    → Master Agent (src/core/agent.py) — LLM agent loop (max 20 iterations)
      → LLM Gateway (src/llm/gateway.py) + Tool Registry (src/tools/)
        → SubAgent Executor / Skill Executor / Direct Tool Execution
```

### Core Components

**Agent** (`src/core/agent.py`): The brain. A single `Agent` class serves as both master and sub-agent (via `is_master` flag). The `master_agent` singleton is exported from `src/core/__init__.py`. The agent loop calls LLM, executes tools, accumulates results, and pushes progress events via callbacks.

**Tool System** (`src/tools/`): Tools inherit `BaseTool` and implement `async execute(args)`. **Critical**: Tool schemas for LLM function calling are defined in `AGENT_TOOLS` list at the top of `agent.py`, which is SEPARATE from `ToolRegistry` implementations. Adding a tool requires updating both places: the schema in `AGENT_TOOLS` and registration in `Agent._register_builtin_tools()`.

**Skill System** (`src/core/skill_*.py` + `src/skills/`): Domain knowledge extension packages stored as directories with `SKILL.md` files (YAML frontmatter + Markdown). `SkillRegistry` discovers and indexes skills. Skills are loaded into context when the LLM calls `use_skill`, and executed via `skill_execute`. Skills support auto-matching by file extension.

**SubAgent System** (`subagents/` + `src/subagents/`): Defined in `subagents/<name>/SUBAGENT.md` (YAML + Markdown). The master agent delegates tasks via `delegate_to_subagent`. `SubagentExecutor` instantiates a child `Agent(is_master=False)` and runs it in a separate thread.

**LLM Gateway** (`src/llm/gateway.py`): Unified interface to `qwen` (DashScope) and `zhipu` (ZhipuAI) providers. All calls go through `chat_with_tools()`. Provider is switchable via `llm.provider` in `configs/config.yaml`. `KeyPool` manages multiple API keys with semaphore-based concurrency control.

**Channel System** (`src/channels/`): Each channel (WeCom, DingTalk, Feishu) extends `ChannelAdapter`. `ChannelManager` dispatches messages. Enabled/disabled via `configs/config.yaml` per-channel `enabled` flag.

**Memory** (`src/memory/short_term.py`): `ShortTermMemory` uses a deque-based sliding window per session_id, with configurable max_messages and TTL.

**Knowledge Base** (`src/knowledge/`): RAG pipeline with parsers → chunker → embedding (via LLM gateway) → sqlite-vec vector DB → hybrid retriever.

**Database** (`src/db/`): Default SQLite (`aid_work_agent.db`), configurable via `DATABASE_URL` env var to PostgreSQL or MySQL. Used for session persistence and auth.

### Key Entry Points

- `src/main.py` — FastAPI app with all HTTP routes (chat, SSE stream, upload, channel callbacks)
- `gradio_app.py` — Standalone debug UI that directly imports `master_agent`, bypassing FastAPI
- `v4_skills_agent.py` — Standalone Claude/Anthropic API demo, not part of the main system

### Configuration

Configuration loads in layers (later overrides earlier):
1. `configs/config.yaml` — Base YAML config with `${ENV_VAR}` placeholders
2. `.env` file — Environment variables
3. OS environment variables — Direct overrides

Key env vars: `LLM_PROVIDER` (qwen|zhipu), `QWEN_API_KEYS`, `ZHIPU_API_KEYS`, `DATABASE_URL`, `TAVILY_API_KEY`, channel configs (`WECOM_*`, `DINGTALK_*`, `FEISHU_*`), `SMTP_*`/`IMAP_*` for email.

## Extension Points

### Adding a New Tool
1. Create tool class in `src/tools/<category>/`, inherit `BaseTool`
2. Define Pydantic `InputModel` for parameter validation (with Chinese Field descriptions)
3. Set `name`, `description`, `display_name`, `InputModel` on the class
4. Implement `async execute(self, **kwargs) -> Dict[str, Any]`
5. Optionally override `get_display_name()` for dynamic display names
6. Register in `Agent._register_builtin_tools()`

**Schema 来源**：每个工具类通过 `InputModel`（Pydantic BaseModel）或 `parameters_schema` 定义参数 schema，`ToolRegistry.get_tool_definitions()` 自动收集。不再需要手动维护 `schemas.py`。

### Adding a New Skill
Create directory `src/skills/<name>-<version>/` with a `SKILL.md` file (see existing skills for format). Auto-loaded on restart.

### Adding a New SubAgent
Create `subagents/<name>/SUBAGENT.md` with YAML frontmatter (name, description, capabilities, triggers, tools, skills.allowed) + Markdown body. Auto-loaded on restart.

**SUBAGENT.md 格式规范**：
```
---
name: 智能体中文名
description: 一行描述
version: 1.0.0
author: system
capabilities: [...]
triggers:
  file_patterns: [...]
  keywords: [...]
tools:
  inherit: true
skills:
  allowed: [...]
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---

（此处为 Markdown body，将作为 system_prompt 使用）
```

**关键陷阱 — system_prompt 必须放在 body 中，不能放在 YAML frontmatter 里**：
- Loader 用正则 `^---\s*\n(.*?)\n---\s*\n(.*)$` 分割 frontmatter 和 body，要求第二个 `---` 必须**顶格**（无缩进）
- 如果 `system_prompt: |` 写在 YAML 中且内容包含 `---` 水平线（Markdown 分隔符），即使这些 `---` 有缩进，也很容易导致 frontmatter 没有闭合的顶格 `---`，正则匹配失败，子智能体不会被加载（静默失败，fallback 到 master agent）
- 正确做法：**不要在 frontmatter 中定义 system_prompt**，把系统提示词写在闭合 `---` 之后的 body 中。Loader 代码 `frontmatter.get("system_prompt", body.strip())` 会自动使用 body 作为 system_prompt
- URL 路由匹配：`/chat/<目录名>` 通过 `dir_name` 字段匹配（例如 `/chat/contract-archive-review` 匹配 `subagents/contract-archive-review/`）

### Adding a New Channel
Extend `src/channels/base.py` `ChannelAdapter`, implement `parse_message`/`send_message`/`verify_signature`, register in `src/main.py` lifespan.

## Language Note

The codebase uses mixed languages: README, comments, and prompts are primarily Chinese (Simplified). Code identifiers are English. UI text is Chinese.
