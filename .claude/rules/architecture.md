# 系统架构

## 请求流程
```
用户/渠道（企业微信、钉钉、飞书、Web）
  → FastAPI (src/main.py) — HTTP 路由、SSE 流式输出
    → 主智能体 (src/core/agent.py) — 大模型智能体循环（最多 20 轮）
      → 大模型网关 (src/llm/gateway.py) + 工具注册表 (src/tools/)
        → 子智能体执行器 / 技能执行器 / 直接工具执行
```

## 核心组件

**智能体** (`src/core/agent.py`)：核心大脑。单个 `Agent` 类同时作为主智能体和子智能体（通过 `is_master` 标志区分）。`master_agent` 单例从 `src/core/__init__.py` 导出。智能体循环调用大模型、执行工具、累积结果，并通过回调推送进度事件。

**工具系统** (`src/tools/`)：工具继承 `BaseTool` 并实现 `async execute(args)`。**关键**：用于大模型函数调用的工具 schema 定义在 `agent.py` 顶部的 `AGENT_TOOLS` 列表中，与 `ToolRegistry` 实现是**分开的**。添加工具需要同时更新两处：`AGENT_TOOLS` 中的 schema 和 `Agent._register_builtin_tools()` 中的注册。

**技能系统** (`src/core/skill_*.py` + `src/skills/`)：领域知识扩展包，存储在带 `SKILL.md` 文件（YAML 头部 + Markdown）的目录中。`SkillRegistry` 发现并索引技能。当大模型调用 `use_skill` 时，技能被加载到上下文中，并通过 `skill_execute` 执行。技能支持按文件扩展名自动匹配。

**子智能体系统** (`subagents/` + `src/subagents/`)：在 `subagents/<name>/SUBAGENT.md` 中定义（YAML + Markdown）。主智能体通过 `delegate_to_subagent` 委托任务。`SubagentExecutor` 实例化一个子 `Agent(is_master=False)` 并在线程中运行。

**大模型网关** (`src/llm/gateway.py`)：统一接口到 `qwen`（DashScope）和 `zhipu`（ZhipuAI）提供商。所有调用都通过 `chat_with_tools()`。提供商可通过 `configs/config.yaml` 中的 `llm.provider` 切换。`KeyPool` 通过信号量控制并发，管理多个 API 密钥。

**渠道系统** (`src/channels/`)：每个渠道（企业微信、钉钉、飞书）继承 `ChannelAdapter`。`ChannelManager` 分发消息。渠道通过 `configs/config.yaml` 中的 `enabled` 标志启用/禁用。

**记忆** (`src/memory/short_term.py`)：`ShortTermMemory` 使用基于 deque 的滑动窗口，按 session_id 分隔，配置 max_messages 和 TTL。

**知识库** (`src/knowledge/`)：RAG 流程，包含解析器 → 分块器 → 嵌入（通过大模型网关）→ 向量数据库 → 混合检索器。

**数据库** (`src/db/`)：PostgreSQL。

## 关键入口点

- `src/main.py` — FastAPI 应用，包含所有 HTTP 路由（聊天、SSE 流式上传、渠道回调）
- `gradio_app.py` — 独立调试 UI，直接导入 `master_agent`，绕过 FastAPI
- `v4_skills_agent.py` — 独立的 Claude/Anthropic API 演示，不属于主系统

## 配置加载

配置按以下层级加载（后者覆盖前者）：
1. `configs/config.yaml` — 基础 YAML 配置，支持 `${ENV_VAR}` 占位符
2. `.env` 文件 — 环境变量
3. OS 环境变量 — 直接覆盖

关键环境变量：`LLM_PROVIDER`（qwen|zhipu）、`API_KEYS`、`MODEL_CODE`、`BASE_URL`、`DATABASE_URL`、`TAVILY_API_KEY`、渠道配置（`WECOM_*`、`DINGTALK_*`、`FEISHU_*`）、邮箱配置（`SMTP_*`/`IMAP_*`）。

## 扩展点

### 添加工具
1. 在 `src/tools/<category>/` 中创建工具类，继承 `BaseTool`
2. 定义 Pydantic `InputModel` 用于参数验证（带中文 Field 描述）
3. 在类上设置 `name`、`description`、`display_name`、`InputModel`
4. 实现 `async execute(self, **kwargs) -> Dict[str, Any]`
5. 可选择重写 `get_display_name()` 用于动态显示名称
6. 在 `Agent._register_builtin_tools()` 中注册

**Schema 来源**：每个工具类通过 `InputModel`（Pydantic BaseModel）或 `parameters_schema` 定义参数 schema，`ToolRegistry.get_tool_definitions()` 自动收集。不再需要手动维护 `schemas.py`。

### 添加技能
创建目录 `src/skills/<name>-<version>/`，包含 `SKILL.md` 文件（参考现有技能格式）。重启后自动加载。

**Skill 白名单层级（2 层模型）**：

| 层级 | 配置位置 | 作用范围 | 说明 |
|------|---------|---------|------|
| 1. 主智能体白名单 | `configs/config.yaml` `skills.master_agent.allowed` | 仅主智能体运行时 | 控制主智能体可调用哪些 skill。子智能体运行时**不读这一层** |
| 2. 子智能体白名单 | 管理后台 / DB `subagent_definitions.skills.allowed` | 子智能体运行时 | 控制子智能体可调用哪些 skill。由 `SubagentConfig.get_allowed_skills()` 在 `agent.py` 中读取 |

每新增一个 skill 给子智能体用，**只需改 1 处**：管理后台 / DB 的 `subagent_definitions.skills.allowed`。

**关键**：管理后台技能选择器（3 个 API：`/api/admin/agent-definitions/meta/skills`、`/api/admin/subagents/skills`、`/api/subagents/skills`）读取的是 `SkillRegistry.list_all_loaded_skills()`——全部基础目录已加载 skill（未经主智能体白名单过滤），这样管理员能看到全部可选 skill。`_all_skills` 只含基础目录 skill，不含租户私有 skill（符合 `subagent_definitions` 表无 `tenant_id` 的语义）。

### 添加子智能体
创建 `subagents/<name>/SUBAGENT.md`，包含 YAML 头部（name、description、capabilities、triggers、tools、skills.allowed）+ Markdown 正文。重启后自动加载。

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

### 添加渠道
继承 `src/channels/base.py` 的 `ChannelAdapter`，实现 `parse_message`/`send_message`/`verify_signature`，在 `src/main.py` 生命周期中注册。

### 添加业务页面

详细设计见 [page-metadata-registry-design.md](../../docs/system/digital-employee/page-metadata-registry-design.md)。

开发一个新的业务数据页面，按以下顺序操作：

**1. 注册页面元数据**（最早做）

在 `configs/page_metadata.yaml` 的 `pages` 列表中添加条目，status 标为 `planned`：

```yaml
- page_id: {domain}-{child-route}
  title: 页面中文名
  description: 页面功能描述（中文，用于搜索匹配和 AI 推荐）
  route: /{domain}/{child-route}
  icon: "emoji"
  domain: {domain}
  status: planned
```

如果对应的 domain 尚不存在，还需在 `domains` 列表中添加域定义。

**2. 创建 Vue 组件**

在 `frontend/src/components/<domain>/` 目录下创建组件，遵循 [list-page-convention.md](./list-page-convention.md) 规范。

**3. 注册路由**

在 `frontend/src/main.ts` 中注册路由，每个业务域的路由需要注册两份（demo 模式 + tenant 模式），参照现有模式。

**4. 发布**

开发完成并测试通过后，将 `configs/page_metadata.yaml` 中的 `status` 改为 `published`。只有 `published` 状态的页面才会出现在业务页面选择器中。

**页面状态**：`planned`（规划中）→ `developing`（开发中）→ `published`（已发布）。只有 `published` 状态对用户可见。