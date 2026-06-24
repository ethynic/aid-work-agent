# CODEBUDDY.md This file provides guidance to WorkBuddy when working with code in this repository.

## 项目概述

AID Work Agent 是一个**面向企业客户**的员工智能代理系统，基于国产大模型（通义千问/智谱GLM）提供对话式任务执行能力，支持企业微信、钉钉、飞书等多渠道接入，并通过 Skill 和 SubAgent 机制扩展领域能力。

### 项目定位与设计原则

**与 OpenClaw、DeerFlow 等开源项目的核心差异**：

| 特性 | 本项目（AID Work Agent） | 其他开源项目 |
|------|-------------------------|--------------|
| 目标用户 | **企业客户**，多用户租户 | 个人用户 |
| 核心价值 | **稳定生产、规范工作流、快捷响应、可预期的结果** | 娱乐、探索、学术讨论 |
| 用户体验 | 专业、高效、可靠 | 娱乐搞笑、惊喜体验 |
| 回答风格 | 严谨、专业、清晰、简洁 | 可能娱乐化 |
| 创造性 | **克制**，避免过度创造性 | 鼓励创造性 |
| 用户规模 | **几十人甚至几百人同时使用** | 单人或少量用户 |
| 数据安全 | **敏感信息加密存储**，严格权限控制 | 简单存储 |

**设计原则**：
- ✅ 稳定生产：所有对话和操作结果可预期、可追溯
- ✅ 规范工作流：标准化流程，减少随意性
- ✅ 快捷响应：优化响应时间，提升效率
- ✅ 诚实回答：在面对没有把握的问题时，应明确告知；当执行任务失败时，应明确告知、并提供详细的错误信息，以便用户介入
- ❌ 避免娱乐搞笑、惊喜体验
- ❌ 避免不懂装懂，无法回答时应明确告知
- ❌ 避免学术讨论、前沿探索
- ❌ 避免过度创造性

---

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
```

### 启动后端服务（Web 模式）
```bash
python -m src.main
# 服务运行在 http://localhost:8000
```

### 启动后端服务（CLI 聊天模式）
```bash
CLI_MODE=true python -m src.main
```

### 启动 Gradio UI（快速调试）
```bash
python gradio_app.py
```

### 运行测试
```bash
# 运行全部测试
pytest tests/

# 运行单个测试文件
pytest tests/test_agent.py -v

# 运行包含异步的测试
pytest tests/ --asyncio-mode=auto

# 运行综合集成测试
python final_comprehensive_test_all.py
```

### 前端开发
```bash
cd frontend
npm install
npm run dev       # 开发模式
npm run build     # 构建生产版本
```

### Docker 部署
```bash
# 开发环境
docker-compose up -d

# 生产环境
docker-compose -f docker-compose.prod.yml up -d --build

# 检查部署状态
./deploy/check_deployment.sh
```

### API 健康检查
```bash
curl http://localhost:8000/health
```

---

## 架构概述

### 整体分层

```
用户/渠道入口
    ↓
FastAPI / Gradio UI (src/main.py, gradio_app.py)
    ↓
Master Agent (src/core/agent.py)
    ↓
LLM Gateway (src/llm/gateway.py)  +  Tool Registry (src/tools/)
    ↓
SubAgent Executor / Skill Executor
    ↓
SubAgents (subagents/)  /  Skills (src/skills/)
```

### 核心入口

- **`src/main.py`**：FastAPI 应用入口。定义所有 HTTP 路由，包括 `/api/chat`（同步）、`/api/chat/stream`（SSE 流式）、文件上传，以及企业微信/钉钉/飞书回调路由（通过 `src/channels/` 注册）。应用启动时根据 `configs/config.yaml` 初始化渠道适配器和数据库。
- **`gradio_app.py`**：独立的 Gradio 界面，绕过 FastAPI，直接调用 `master_agent`，用于本地快速调试。

### Agent 核心（`src/core/agent.py`）

整个系统的大脑。`Agent` 类同时承担主智能体（Master）和子智能体（SubAgent）角色，通过 `is_master` 参数区分：

- **主智能体**：拥有 `delegate_to_subagent` 工具，可将专业任务委派给子智能体；持有 `SubagentRegistry` 和 `SubagentExecutor`。
- **子智能体**：没有委派能力，只能使用 `AGENT_TOOLS` 中定义的工具和注册的 Skill。

**Agent 循环（`process_message`）**：
1. 为消息添加时间戳上下文，保存到 `ShortTermMemory`
2. 若有文件附件，自动匹配并注入对应 Skill 的上下文
3. 进入最多 20 轮的 LLM 迭代：
   - 调用 `llm_gateway.chat_with_tools()`，传入系统提示词、历史消息和工具列表
   - 若 LLM 返回工具调用，逐个执行（`create_plan`、`use_skill`、`skill_execute`、`delegate_to_subagent` 有专用处理逻辑，其余走 `ToolExecutor`）
   - 工具结果追加到消息列表，进入下一轮
   - 无工具调用时 yield 最终回复并退出循环
4. 通过 `yield make_event(...)` 实时 yield 结构化事件（`tool_start`、`tool_result`、`progress`、`thinking` 等），SSE 端点通过 `async for` 迭代直接序列化为 SSE 帧推送给前端

**系统提示词**：`_build_base_system_prompt()` 动态生成，根据 `is_master` 决定是否包含委派规则、可用子智能体列表。系统提示词对 LLM 行为有决定性影响，修改需谨慎。

### LLM 网关（`src/llm/`）

`LLMGateway`（`gateway.py`）是统一入口，通过 `configs/config.yaml` 中的 `llm.provider` 配置切换底层提供商：
- `qwen`：阿里云通义千问（DashScope SDK）
- `zhipu`：智谱 GLM（ZhipuAI SDK）

所有 LLM 调用都经过 `chat_with_tools()` 接口，屏蔽了不同 SDK 的差异。切换模型只需修改 `config.yaml` 的 `llm.provider` 字段（或设置环境变量）。

### 工具系统（`src/tools/`）

- **`base.py`**：`BaseTool` 抽象基类，所有工具继承它并实现 `execute(args)` 方法
- **`registry.py`**：`ToolRegistry` 管理已注册工具的查找和调用
- **`executor.py`**：`ToolExecutor` 负责实际执行，处理异常
- 具体工具按功能分目录：`email/`、`browser/`（Playwright）、`search/`（Tavily）、`document/`（摘要/翻译）、`ocr/`（PaddleOCR）、`file/`（文件读取）、`llm/`（content_generate）

主智能体在启动时通过 `_register_builtin_tools()` 注册全部内置工具。**工具定义（schema）与工具实现是分离的**：`AGENT_TOOLS` 列表（在 `agent.py` 顶部）定义 LLM 可调用的工具 schema，`ToolRegistry` 中是实际的 Python 实现。添加新工具需同时更新这两处。

### Skill 系统（`src/core/skill_*.py` + `src/skills/`）

Skill 是领域知识扩展包，以目录形式存放在 `src/skills/<skill-name>-<version>/`，每个 Skill 包含：
- `SKILL.md`：技能描述、触发条件、使用说明
- `scripts/`（可选）：可执行脚本

**工作流程**：LLM 调用 `use_skill` 工具加载指定 Skill 的 `SKILL.md` 内容，注入对话上下文；随后可调用 `skill_execute` 执行 Skill 目录下的脚本命令。

`SkillRegistry` 负责从目录加载和索引所有 Skill，支持按文件扩展名自动匹配（如上传 `.pdf` 文件时自动加载 PDF Skill）。配置文件中的 `skills.master_agent.allowed` 可限制主智能体可访问的 Skill 白名单。

### SubAgent 系统（`subagents/` + `src/subagents/`）

子智能体定义在 `subagents/<name>/SUBAGENT.md`，格式为 YAML Front Matter + Markdown 说明：
- **YAML 头部**：name、description、capabilities、triggers（关键词）、tools（inherit 或具体列表）、skills.allowed、context 限制、system_prompt（专业约束）
- **Markdown 主体**：详细的领域知识和操作指南

目前内置三个子智能体：`hr-expert`（HR管理）、`code-reviewer`（代码审查）、`trade-specialist`（国际贸易）。

`SubagentRegistry`（`src/subagents/registry.py`）解析并索引所有子智能体配置。`SubagentExecutor` 将每次委派任务实例化为独立的子智能体 `Agent`（`is_master=False`），在独立线程中异步执行，结果通过 `wait_for_result()` 返回给主智能体。

### 渠道适配器（`src/channels/`）

每个渠道（WeCom、Dingtalk、Feishu）继承 `ChannelAdapter` 基类，实现消息解析（inbound）和发送（outbound）。`ChannelManager` 统一管理已注册的渠道。各渠道路由通过 `src/channels/callback.py` 注册到 FastAPI。是否启用某渠道通过 `configs/config.yaml` 的 `channels.<name>.enabled` 控制。

### 记忆系统（`src/memory/`）

`ShortTermMemory`：基于 `deque` 的滑动窗口，按 session_id 维护对话历史，默认保留最近 10 条消息，TTL 1小时。消息格式兼容 OpenAI 的 `role`/`content`/`tool_calls` 结构，工具调用结果以 `role: tool` 消息存储。

### 计划管理（`src/core/plan_manager.py`）

当 LLM 调用 `create_plan` 工具时，`PlanManager` 创建结构化的 `ExecutionPlan` 并持久化为 Markdown 文件（`plans/<session_id>.md`）。每个 step 的状态（pending → running → completed/failed）实时更新到该文件，方便调试和审计。

### 数据库（`src/db/`）

使用 PostgreSQL + pgvector，通过 `DATABASE_URL` 环境变量配置。主要用于会话持久化和认证管理（`src/api/auth.py`、`src/api/session.py`）。

### 多租户与用户隔离（`src/multi_tenant/`）

**核心特性**：
- **多用户支持**：支持企业内数百甚至数千用户同时使用
- **会话隔离**：用户之间的对话历史完全隔离，互不可见
- **知识隔离**：用户只能访问被授权的 Agent 和知识库
- **租户隔离**：支持多企业（租户）部署，数据完全隔离

**实现要点**：
- 每个用户属于一个租户（企业）
- 会话（Session）关联用户 ID，确保会话隔离
- Agent 知识库可配置访问权限，实现知识隔离
- 所有数据查询必须包含租户/用户上下文

### 数据安全（`src/security/`）

**敏感信息加密存储**：
- 用户在对话中提供的敏感信息（如邮箱账户密码、API Key 等）**必须加密存储**
- 加密后的敏感信息只能用于执行对应操作，**不能明文返回给用户**
- 例如：用户提供了邮箱密码让 Agent 代发邮件，该密码加密存储后：
  - ✅ 可用于发送邮件
  - ❌ 用户询问"我的邮箱密码是什么"时，**不能给出**

**敏感字段过滤规则**：

| 敏感关键词 | 处理方式 |
|-----------|---------|
| `password`、`passwd` | 加密存储，返回时替换为 `***` |
| `secret`、`token`、`api_key` | 加密存储，返回时替换为 `***` |
| `private_key`、`access_key` | 加密存储，返回时替换为 `***` |

**实现参考**：后端接口错误处理规范中的 `sanitize_error_info` 函数。

### 管理员后台（`src/admin/`）

管理员后台提供企业级管理能力，包含以下核心功能：

| 功能模块 | 说明 |
|---------|------|
| **用户管理** | 增删改查用户、设置用户状态、分配部门 |
| **数字员工管理** | 管理企业内的 Agent（如差旅小助手、HR Agent 等） |
| **权限管理** | 分配用户与 Agent 的访问权限（哪些用户可以访问哪些 Agent） |
| **日志记录** | 记录所有对话和操作日志，支持追溯 |
| **统计数据** | 查看 Agent 使用情况、用户活跃度等统计 |

**权限模型**：
- 管理员 -> 租户管理员（可选）-> 普通用户
- 用户与 Agent 之间是多对多关系（通过权限表控制）
- Agent 执行的所有任务（不论成败）都需要记录日志

### 审计与日志（`src/audit/`）

**对话记录**：
- 用户与 Agent 的所有对话长期存储
- 支持按用户、时间、Agent 等维度查询

**任务执行日志**：
- Agent 执行的每项任务，不论成败，都记录详细日志
- 日志内容：任务类型、输入参数、执行结果、错误信息、执行时间
- 用于问题追溯和系统监控

### 前端（`frontend/`）

Vue 3 + TypeScript + Vite + TailwindCSS 构建的单页应用。通过 SSE（EventSource）连接后端 `/api/chat/stream` 接口，实时接收流式响应和工具执行进度。支持文件上传（先 POST 到 `/api/upload` 获取 `file_id`，再将 `file_id` 附带到聊天请求）。

---

## 关键扩展点

### 添加新工具
1. 在 `src/tools/<category>/` 下创建工具类，继承 `BaseTool`，实现 `async execute(args)`
2. 在 `src/core/agent.py` 顶部的 `AGENT_TOOLS` 列表中添加工具的 JSON Schema 定义
3. 在 `Agent._register_builtin_tools()` 中注册工具实例

### 添加新 Skill
在 `src/skills/<skill-name>-<version>/` 下创建目录，包含 `SKILL.md`（参照现有 Skill 格式），重启服务后自动加载。

### 添加新子智能体
在 `subagents/<name>/SUBAGENT.md` 中按 YAML Front Matter 格式定义，重启后自动加载。`triggers.keywords` 用于提示主智能体何时委派。

### 添加新渠道
继承 `src/channels/base.py` 的 `ChannelAdapter`，实现消息解析和发送，在 `src/main.py` 的 `lifespan` 函数中条件初始化并注册到 `channel_manager`。

### 添加多 Agent 类型（如差旅小助手、HR Agent）
1. 在 `src/agents/` 下创建新 Agent 类，继承 `BaseAgent`
2. 定义 Agent 的专业能力和触发关键词
3. 在 `src/multi_tenant/permission.py` 中注册 Agent 与用户的权限关系
4. 在管理后台的"数字员工管理"中添加新 Agent 配置

### 实现敏感信息加密存储
1. 在 `src/security/encryption.py` 中实现加密解密逻辑（建议使用 AES-256 或类似算法）
2. 敏感信息（如用户提供的邮箱密码）存储时加密，使用时解密
3. 返回给用户时必须过滤敏感字段，替换为 `***`
4. 加密密钥通过环境变量或密钥管理服务保管

---

## 环境变量

关键环境变量（参照 `deploy/.env.production.example`）：

| 变量名 | 说明 |
|--------|------|
| `LLM_PROVIDER` | LLM 提供商（qwen/zhipu/...） |
| `API_KEYS` | LLM API Key（逗号分隔多 Key 池） |
| `MODEL_CODE` | 模型编码 |
| `BASE_URL` | API Base URL |
| `DATABASE_URL` | 数据库连接字符串 |
| `WECOM_CORP_ID` / `WECOM_SECRET` 等 | 企业微信配置 |
| `DINGTALK_APP_KEY` 等 | 钉钉配置 |
| `FEISHU_APP_ID` 等 | 飞书配置 |
| `SMTP_SERVER` / `IMAP_SERVER` 等 | 邮件服务配置 |
