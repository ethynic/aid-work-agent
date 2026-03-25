# Code Review Report — AID Work Agent

> **审核日期**：2026-03-25  
> **审核范围**：全项目（`src/`、`frontend/`、`configs/`、`subagents/`）  
> **审核原则**：只记录问题，不修改代码  
> **问题分级**：🔴 严重（Critical）｜🟡 中等（Warning）｜🟢 可清理（Cleanup）

---

## 目录

1. [🔴 严重问题（Critical）](#严重问题)
   - [1.1 auth.py — 函数名遮蔽（Name Shadowing）](#11-authpy--函数名遮蔽name-shadowing)
   - [1.2 auth.py — 注释块破坏语法结构（Orphan Code）](#12-authpy--注释块破坏语法结构orphan-code)
   - [1.3 core/executor.py vs tools/executor.py — 同名类双重定义](#13-coreexecutorpy-vs-toolsexecutorpy--同名类双重定义)
   - [1.4 models.py — SessionDB.delete() 级联删除不完整](#14-modelspy--sessiondbdelete-级联删除不完整)
2. [🟡 中等问题（Warning）](#中等问题)
   - [2.1 前端类型命名冲突（ChatMessage 双定义）](#21-前端类型命名冲突chatmessage-双定义)
   - [2.2 auth.py — verify_token 过期清理逻辑位置不当](#22-authpy--verify_token-过期清理逻辑位置不当)
   - [2.3 agent.py — SubAgent 线程池未设上限](#23-agentpy--subagent-线程池未设上限)
   - [2.4 main.py — /api/chat 同步接口阻塞事件循环](#24-mainpy--apichat-同步接口阻塞事件循环)
3. [🟢 可清理冗余代码（Dead Code / Unused Modules）](#可清理冗余代码)
   - [3.1 src/core/dialog_manager.py — 零引用，344 行死代码](#31-srccoredialog_managerpy--零引用344-行死代码)
   - [3.2 src/core/intent_engine.py — 链式冗余，456 行死代码](#32-srccoreintent_enginepy--链式冗余456-行死代码)
   - [3.3 src/core/planner.py — 整体过时，421 行死代码](#33-srccoreplannerpy--整体过时421-行死代码)
   - [3.4 src/memory/manager.py — 未完成的封装层，312 行死代码](#34-srcmemorymanagerpy--未完成的封装层312-行死代码)
   - [3.5 src/core/executor.py — 与 tools/executor.py 功能重叠](#35-srccoreexecutorpy--与-toolsexecutorpy-功能重叠)
4. [🖥️ 前端专项审核（Frontend Deep Review）](#前端专项审核frontend-deep-review)
   - [F-1 🔴 customer.ts — API_BASE 拼写错误，所有客户接口 404](#f-1--customerts--api_base-拼写错误导致所有请求-404)
   - [F-2 🔴 credentials.ts — new URL() 不兼容相对路径，崩溃](#f-2--credentialsts--listcredentials-构造-url-方式不兼容相对路径)
   - [F-3 🟡 useAgent.ts — sessionId 双轨制竞态，消息丢失风险](#f-3--useagentts--session_id-双轨制管理存在数据丢失风险)
   - [F-4 🟡 ChatContainer.vue — AI 回复从未持久化，历史消息消失](#f-4--chatcontainervue--历史消息恢复不保存-ai-回复助手消息丢失)
   - [F-5 🟡 useAuth.ts — User 接口重复定义](#f-5--useauthts--user-接口与-typesindexts-重复定义)
   - [F-6 🟡 MessageItem.vue — formatProgressContent 参数类型错误](#f-6--messageitemvue--formatprogresscontent-参数类型签名与实际调用不符)
   - [F-7 🟡 credentials.ts — 所有接口无 Authorization Header](#f-7--credentialsts--凭据列表请求无鉴权-header)
   - [F-9 🟢 types/index.ts — SendMessageRequest.files 类型错误](#f-9--sendmessagerequest-类型定义中-files-字段类型错误)
   - [F-10 🟢 useAgent.ts — switch-case 中 const 缺少块级作用域](#f-10--useagentts--gettooldiplayname-中-const-声明在-switch-case-中无块级作用域)
5. [附录：审核方法说明](#附录审核方法说明)

---

## 严重问题

### 1.1 core/executor.py vs tools/executor.py — 同名类双重定义

- **文件**：`src/core/executor.py`（324 行）和 `src/tools/executor.py`（实际在用）
- **位置**：两个文件均定义了 `ToolRegistry`、`BaseTool`、`ToolExecutor`

**问题描述**：

`src/core/executor.py` 和 `src/tools/executor.py` 中存在**完全同名的类定义**，分别被各自的 `__init__.py` 导出：

```python
# src/core/__init__.py 第 7 行
from .executor import ToolExecutor   # ← 来自 core/executor.py

# src/tools/__init__.py 第 5 行
from .executor import ToolExecutor   # ← 来自 tools/executor.py
```

而 `agent.py` 明确使用的是 `tools` 版本：
```python
# src/core/agent.py 第 23 行
from src.tools.executor import ToolExecutor   # ← 真正在使用的版本
```

两个 `ToolExecutor` 实现不同：
- `core/executor.py` 版本：内置了 `RespondTool` / `ClarifyTool` 两个特殊工具，有独立的 `ToolRegistry`
- `tools/executor.py` 版本：无内置特殊工具，使用外部传入的 `ToolRegistry`

**影响**：
- `core/executor.py` 中的 `ToolExecutor`、`ToolRegistry`、`BaseTool` **从未被业务代码实际使用**（agent.py 用的是 tools 版本）
- 任何开发者看到 `from src.core import ToolExecutor` 的调用，会误以为与 `agent.py` 是同一实现
- `RespondTool` / `ClarifyTool` 在 `core/executor.py` 中有实现但**无任何调用方**，是功能性死代码

**修复建议**：
- 删除 `src/core/executor.py` 整个文件（324 行）
- 从 `src/core/__init__.py` 中移除对应导出
- 若 `RespondTool` / `ClarifyTool` 有需要，迁移到 `src/tools/` 目录下

---

### 1.2 models.py — `SessionDB.delete()` 级联删除不完整

- **文件**：`src/db/models.py`
- **位置**：第 241-254 行（`SessionDB.delete()`）和第 466-472 行（`ChatRecordDB.delete_by_session()`）

**问题描述**：

`SessionDB.delete()` 方法删除会话时只清理了两张表：

```python
# 第 241-254 行
@staticmethod
def delete(session_id: str) -> bool:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 删除消息
        cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        # 删除会话
        cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        return True
```

但 `chat_records` 表（由 `ChatRecordDB` 管理）中存储的记录**也关联了 `session_id`**，且 `ChatRecordDB` 专门提供了 `delete_by_session()` 方法（第 466-472 行）：

```python
# 第 466-472 行
@staticmethod
def delete_by_session(session_id: str) -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_records WHERE session_id = ?", (session_id,))
        conn.commit()
        return cursor.rowcount
```

`SessionDB.delete()` **没有调用** `ChatRecordDB.delete_by_session()`。

**影响**：
- 每次删除会话后，`chat_records` 表中会遗留**孤立记录（orphaned records）**
- 长期运行后 `chat_records` 表数据量持续膨胀，且这些记录无法再通过会话 ID 关联到任何有效会话
- 如果后续有"审计记录"查询，这些孤立记录会干扰统计数据

**修复建议**：
在 `SessionDB.delete()` 中追加调用：
```python
ChatRecordDB.delete_by_session(session_id)
```
或在数据库层面对 `chat_records.session_id` 加外键约束 `ON DELETE CASCADE`。

---

## 中等问题

### 2.1 前端类型命名冲突（ChatMessage 双定义）

- **文件 A**：`frontend/src/types/index.ts`
- **文件 B**：`frontend/src/api/session.ts`，第 18-25 行

**问题描述**：

两个文件均导出 `ChatMessage` 接口，但含义和字段完全不同：

| 特性 | `types/index.ts` 的 `ChatMessage` | `api/session.ts` 的 `ChatMessage` |
|------|----------------------------------|-----------------------------------|
| **用途** | UI 渲染层，前端内部使用 | API 响应层，对应后端返回的数据结构 |
| **关键字段** | `id`（number）、`role`、`content`、`timestamp`（Date）、`progressMessages`（UI专属） | `id`（string）、`role`、`content`、`metadata`（object）、`created_at`（string） |
| **timestamp 类型** | `Date`（JS对象） | `created_at: string`（ISO字符串） |

当组件同时 `import` 两个文件时，后导入的会遮蔽先导入的，导致类型检查静默失效。

**影响**：
- TypeScript 编译器无法静默发现实际使用了哪个版本的 `ChatMessage`
- 将 API 层 `ChatMessage` 直接传给期望 UI 层 `ChatMessage` 的组件时，`timestamp` vs `created_at` 字段名差异会导致**运行时 undefined**
- 组件代码中可能已经混用两套字段名，造成隐性 Bug

**修复建议**：
- 将 `api/session.ts` 中的 `ChatMessage` 重命名为 `ChatMessageRecord` 或 `APIChatMessage`
- 或者将两个类型合并，通过可选字段（`?`）兼容 UI 和 API 场景

---

### 2.2 agent.py — SubAgent 线程池未设上限

- **文件**：`src/core/agent.py`
- **位置**：`SubagentExecutor.submit()` 相关逻辑

**问题描述**：

主智能体调用 `delegate_to_subagent` 工具时，`SubagentExecutor` 通过 `ThreadPoolExecutor` 在独立线程中运行子智能体。代码中**未对线程池大小设置显式上限**（默认 `max_workers=None`，Python 3.8+ 会设为 `min(32, os.cpu_count() + 4)`）。

在企业级多用户并发场景下（设计目标：数十至数百用户同时使用），如果每个用户会话的主智能体都委派了子智能体任务：
- 100 个并发用户 × 最多 3 个子智能体 = 可能同时创建 300 个线程
- 加上已有的 Gunicorn Workers 和 LLM 请求线程，系统资源压力极大

**影响**：
- 线程数量无上限控制，高并发下可能导致服务器 OOM 或响应超时
- 无排队机制，所有子智能体请求同时竞争资源

**修复建议**：
- 为 `ThreadPoolExecutor` 设置合理的 `max_workers`（建议 `CPU核数 × 2`）
- 或使用 `asyncio` 原生协程替代线程池，以更低开销支持更高并发

---

### 2.3 main.py — `/api/chat` 同步接口阻塞事件循环

- **文件**：`src/main.py`
- **位置**：`/api/chat` 路由处理函数

**问题描述**：

`/api/chat`（同步版本）路由中，`agent.process_message()` 是一个**异步生成器**，调用时通过 `asyncio.run_in_executor` 或直接 `await` 消费。但观察到代码中在某些分支直接在同步上下文中调用异步函数，未使用 `run_in_executor`，可能在高并发下阻塞 FastAPI 的 uvicorn 事件循环。

**影响**：
- 同步阻塞 LLM 调用（通常耗时 2-30 秒）会导致其他请求排队等待
- 企业场景下多用户并发时，部分用户请求延迟剧增

**修复建议**：
- 所有 LLM 调用统一走 `/api/chat/stream` SSE 接口（已实现异步流式）
- 如保留 `/api/chat` 同步接口，确保 LLM 调用通过 `asyncio.get_event_loop().run_in_executor()` 包装

---

## 可清理冗余代码

> 以下模块均经过引用搜索确认为**零业务引用**或**链式冗余**，可安全删除。删除前建议备份。

### 3.1 src/core/dialog_manager.py — 零引用，344 行死代码

- **文件**：`src/core/dialog_manager.py`（344 行）
- **类名**：`DialogManager`

**验证**：在整个 `src/` 目录下搜索 `DialogManager` 的引用，仅出现在：
1. `src/core/__init__.py` — 导出声明（非业务使用）
2. `src/core/dialog_manager.py` 自身

**无任何业务代码**（`agent.py`、`main.py`、API 层等）实例化或调用 `DialogManager`。

**背景**：该类提供的会话管理功能（多轮对话追踪、上下文拼接）已被 `agent.py` 直接使用 `ShortTermMemory` 替代。

**建议**：
- 删除 `src/core/dialog_manager.py`
- 从 `src/core/__init__.py` 中移除 `from .dialog_manager import DialogManager`

---

### 3.2 src/core/intent_engine.py — 链式冗余，456 行死代码

- **文件**：`src/core/intent_engine.py`（456 行）
- **类名**：`IntentEngine`、`IntentResult`

**验证**：`IntentEngine` / `IntentResult` 仅被 `src/core/planner.py` 引用（第 13 行）。而 `planner.py` 本身也是零业务引用（见 3.3 节）。

**背景**：该模块实现了"规则匹配 + LLM 分类"双路意图识别，设计意图是在 LLM function calling 普及前作为意图路由层。现在 LLM 通过 function calling 直接调用工具，意图识别完全由模型负责，该模块已完全过时。

**建议**：
- 删除 `src/core/intent_engine.py`
- 从 `src/core/__init__.py` 中移除对应导出

---

### 3.3 src/core/planner.py — 整体过时，421 行死代码

- **文件**：`src/core/planner.py`（421 行）
- **类名**：`Planner`

**验证**：`Planner` 仅在 `src/core/__init__.py` 中被导出，无任何业务代码使用。

**内容**：文件中包含一个大型的 `INTENT_TOOL_MAPPING` 字典（意图→工具名的硬编码映射），以及基于该字典构建执行计划的 `Planner` 类。这套机制在 LLM 直接输出 tool_calls 之前有价值，现已完全被 LLM function calling 替代。

> 注意：`src/core/plan_manager.py` 是**另一个文件**（593 行，活跃使用），负责将 LLM 生成的计划持久化为 Markdown 文件，**不应删除**。

**建议**：
- 删除 `src/core/planner.py`（注意区分 `planner.py` 和 `plan_manager.py`）
- 从 `src/core/__init__.py` 中移除 `from .planner import Planner`

---

### 3.4 src/memory/manager.py — 未完成的封装层，312 行死代码

- **文件**：`src/memory/manager.py`（312 行）
- **类名**：`MemoryManager`

**验证**：`MemoryManager` 仅在 `src/memory/__init__.py` 中被导出，无任何业务代码实例化。

**内容**：
- 短期记忆：封装了 `ShortTermMemory`（实际可用）
- 中期记忆：代码注释标注 `# TODO: V1.5 实现`，**未完成**
- 长期记忆：代码注释标注 `# TODO: V2.0 实现`，**未完成**

这是一个**未完成的上层封装**，其中实际可用的短期记忆功能已被 `agent.py` 直接使用 `ShortTermMemory` 替代，`MemoryManager` 从未被实例化。

**建议**：
- 若无 V1.5/V2.0 记忆扩展计划，删除此文件
- 若有扩展计划，保留但在 `README` 或此文档中标注"待实现"状态

---

### 3.5 src/core/executor.py — 与 tools/executor.py 功能重叠

- **文件**：`src/core/executor.py`（324 行）
- **内容**：`BaseTool`、`ToolRegistry`、`ToolExecutor`（含 `RespondTool`、`ClarifyTool`）

（详见 [1.3 节](#13-coreexecutorpy-vs-toolsexecutorpy--同名类双重定义) 的完整分析。）

**此处补充**：`RespondTool` 和 `ClarifyTool` 是 `core/executor.py` 独有的两个内置工具类，功能为：
- `RespondTool`：LLM 调用此工具表示"我有最终回答了"，结束工具循环
- `ClarifyTool`：LLM 调用此工具请求用户澄清信息

这两个工具的**设计思路有价值**，但当前 `agent.py` 的实现通过判断 LLM 返回是否包含 `tool_calls` 来决定是否退出循环，不需要显式的 `RespondTool`。若未来需要结构化退出机制，可将这两个工具迁移到 `src/tools/` 目录下独立维护。

**建议**：
- 将 `RespondTool` / `ClarifyTool` 的设计记录归档（或迁移到 `tools/` 下按需启用）
- 删除 `src/core/executor.py` 剩余部分

---

---

## 前端专项审核（Frontend Deep Review）

> 本节为补充审核，覆盖 `frontend/src/` 下全部文件：`api/`、`composables/`、`components/`、`types/`。

---

### F-1 🔴 customer.ts — API_BASE 拼写错误导致所有请求 404

- **文件**：`frontend/src/api/customer.ts`，第 7 行
- **后端**：`src/api/customer.py`，第 41 行

**问题描述**：

前端 `customer.ts` 中的 `API_BASE` 定义如下：

```typescript
// 第 7 行
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api/customer'}`
```

后端路由前缀：
```python
router = APIRouter(prefix="/api/customer", ...)
```

乍看一致，但注意前端的 fallback 是 `'/api/customer'`（包含路径），而其他 API 文件（如 `session.ts`、`auth.ts`、`credentials.ts`）的写法是 `import.meta.env.VITE_API_BASE_URL || '/api'`，然后再拼接子路径：

```typescript
// session.ts（正确写法）
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/sessions`

// auth.ts（正确写法）
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/auth`

// customer.ts（问题写法）
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api/customer'}`
//     ↑ 当 VITE_API_BASE_URL 有值（如 'http://localhost:8000/api'）时，
//       API_BASE = 'http://localhost:8000/api'，后面又不拼接子路径
//       导致 listCustomers → 'http://localhost:8000/api/customers' ← 路径正确
//     ↑ 当 VITE_API_BASE_URL 为空时
//       API_BASE = '/api/customer'，后面拼接得到 '/api/customer/customers' ← 多了 'customer'
```

由于构建时 `VITE_API_BASE_URL` 通常为空（走默认值），实际请求路径变成：
- `listCustomers` → `POST /api/customer/customers?user_id=...`（**多了一层 `/customer`**）
- `getCustomer` → `GET /api/customer/customers/{id}`
- `getStats` → `GET /api/customer/stats?user_id=...`

**影响**：
- 所有外贸客户相关功能（我的客户页面）在默认开发/部署环境下**全部 404**
- 仅当显式设置了 `VITE_API_BASE_URL=http://xxx/api` 时路径才正确，而这种情况下 URL 拼接变成 `http://xxx/api/customers`（缺少 `/customer` 中间段），**同样 404**

**修复建议**：
```typescript
// 与其他 API 文件保持一致
const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/customer`
```

---

### F-2 🔴 credentials.ts — listCredentials 构造 URL 方式不兼容相对路径

- **文件**：`frontend/src/api/credentials.ts`，第 53 行

**问题描述**：

```typescript
export async function listCredentials(connectionType?: string): Promise<{...}> {
  const url = new URL(`${apiBase}/credentials`)  // ← 第 53 行
  if (connectionType) {
    url.searchParams.append('connection_type', connectionType)
  }
  const response = await fetch(url.toString())
  ...
}
```

`new URL(relativeString)` **要求第一个参数是绝对 URL**（带协议和域名）。当 `apiBase` 的值是 `/api`（相对路径，默认情况）时，`new URL('/api/credentials')` 会抛出 `TypeError: Failed to construct 'URL': Invalid URL`，导致函数直接崩溃。

同文件中其他函数（`getCredential`、`createCredential`、`updateCredential`、`deleteCredential`）使用模板字符串拼接，**不受此影响**，只有 `listCredentials` 有问题。

**影响**：
- 凭据管理页面（`CredentialManager.vue`）的 `onMounted` 会调用 `listCredentials()`，导致页面打开时**直接报错**，凭据列表无法加载
- 报错仅在 `VITE_API_BASE_URL` 未设置（相对路径场景）时出现；若设置了完整 URL 则不报错

**修复建议**：
```typescript
// 改用 URLSearchParams 手动拼接
const params = new URLSearchParams()
if (connectionType) {
  params.append('connection_type', connectionType)
}
const query = params.toString()
const response = await fetch(`${apiBase}/credentials${query ? '?' + query : ''}`)
```

---

### F-3 🟡 useAgent.ts — session_id 双轨制管理，存在数据丢失风险

- **文件**：`frontend/src/composables/useAgent.ts` + `frontend/src/components/ChatContainer.vue`

**问题描述**：

`useAgent.ts` 中的 `sessionId` 通过以下方式生成（第 13 行）：
```typescript
const sessionId = ref<string>(generateSessionId())
// 生成格式：'session_' + Date.now() + '_' + 随机串（纯前端生成）
```

同时，`ChatContainer.vue` 通过后端 `createSession()` API 创建了真正的 DB session，将 `session_id` 同步到 `agentSessionId.value`（第 278、300 行）。

**问题在于**：两套 session ID 存在以下竞争场景：

1. **用户刷新页面**：`useAgent.ts` 重新生成新的随机 `sessionId`（前端本地），而 `loadLatestSession()` 恢复的是后端 DB 中最近的 `session_id`。恢复流程依赖 `ChatContainer.vue` 中的 `watch(currentSessionId, ...)` 正确同步。但若组件挂载顺序导致 `watch` 在同步前触发，发出的第一条消息会使用旧的前端随机 ID 而非 DB session_id。

2. **新建会话后立即发送消息**：`handleNewSession()` 中 `clearSession()` 会重置 `sessionId`（重新生成随机串），再通过 `agentSessionId.value = newSession.session_id` 覆盖。但 `clearSession()` 与赋值之间存在响应式触发时序问题。

3. **SSE 连接中的 session_id**：`sendMessage()` 发出 SSE 请求时使用的是 `sessionId.value`（第 86 行），而后端 `/api/chat/stream` 以此 session_id 存储对话历史。若 `sessionId` 未正确同步为后端 DB 的 id，对话历史会存储到一个不存在的 session 中，**导致历史消息永久丢失**。

**影响**：
- 高概率场景（刷新后立即发消息）导致消息存入孤立 session，历史记录面板无法显示
- 难以复现的竞态条件，用户感知为"历史消息丢失"

**修复建议**：
- 废弃 `useAgent.ts` 中的本地 `generateSessionId()`，改为强制要求调用方传入 `sessionId`（prop/参数形式）
- 或在 `sendMessage` 中优先读取 `currentSessionId`，而非内部的 `sessionId.value`

---

### F-4 🟡 ChatContainer.vue — 历史消息恢复不保存 AI 回复，助手消息丢失

- **文件**：`frontend/src/components/ChatContainer.vue`，第 247-254 行

**问题描述**：

`handleSend()` 中，用户发送消息后会调用 `saveMessage()` 保存用户消息到后端：

```typescript
async function handleSend(content: string) {
  if (currentSessionId.value) {
    await saveMessage(currentSessionId.value, 'user', content)  // ← 保存用户消息
  }
  await sendMessage(content)
  // ← AI 回复通过 SSE 流式写入 messages.value，但从未调用 saveMessage(..., 'assistant', ...)
}
```

**AI 的回复消息从未被保存到后端**。当用户刷新页面后，`watch(currentSessionId, ...)` 通过 `getSessionMessages()` 从后端加载历史，只能看到用户发的消息，**AI 的所有回复全部消失**。

**影响**：
- 会话历史恢复时，对话变成只有用户消息的单方面记录
- 用户切换会话再切回来，AI 回复全部不见

**修复建议**：
在 SSE `onComplete` 回调中，将最终的 AI 回复内容保存到后端：
```typescript
() => {
  addProgress('✅ 任务完成', 'complete')
  isProcessing.value = false
  // 保存 AI 回复
  if (currentSessionId.value && currentResponse.value) {
    saveMessage(currentSessionId.value, 'assistant', currentResponse.value)
  }
}
```

---

### F-5 🟡 useAuth.ts — User 接口与 types/index.ts 重复定义

- **文件 A**：`frontend/src/composables/useAuth.ts`，第 8-13 行
- **文件 B**：`frontend/src/types/index.ts`，第 41-46 行

**问题描述**：

两个文件都定义了完全相同的 `User` 接口：

```typescript
// useAuth.ts 第 8-13 行
export interface User {
  user_id: string
  username: string
  phone?: string
  avatar_url?: string
}

// types/index.ts 第 41-46 行
export interface User {
  user_id: string
  username: string
  phone?: string
  avatar_url?: string
}
```

两个定义字段完全一致，但分散在两个文件中独立维护。

**影响**：
- 若未来需要在 `User` 中加字段，需要同时修改两处，容易漏改
- `useAuth.ts` 没有从 `types/index.ts` 导入，而是重新声明，违反 DRY 原则

**修复建议**：
`useAuth.ts` 中删除本地 `User` 接口定义，改为：
```typescript
import type { User } from '@/types'
```

---

### F-6 🟡 MessageItem.vue — formatProgressContent 参数类型签名与实际调用不符

- **文件**：`frontend/src/components/MessageItem.vue`，第 156、83 行

**问题描述**：

函数签名声明接受 `string | Record<string, any>` 类型：

```typescript
// 第 156 行
function formatProgressContent(msg: string | Record<string, any>): string {
```

但模板中调用时传入的是 `ProgressMessage` 对象（第 83 行）：

```html
<span class="opacity-80">{{ formatProgressContent(msg) }}</span>
```

其中 `msg` 的类型是 `ProgressMessage`（来自 `types/index.ts`）：
```typescript
export interface ProgressMessage {
  type: 'progress' | 'complete' | 'error' | 'thinking' | 'tool_start' | 'tool_result'
  content: string    // ← content 是 string，而非函数内期望的 object
  timestamp: number
  toolName?: string
  ...
}
```

函数内部访问 `msg.content`（第 168 行），但函数签名对 `string` 类型的 `msg` 会直接走 `String(msg)` 分支（第 165 行），导致输出 `"[object Object]"`。

实际上 `msg` 永远是 `ProgressMessage` 对象，函数的 `string` 分支永远不会被触发，函数签名与实际用途存在**类型语义错误**。

**影响**：
- TypeScript 类型检查无法有效保护此函数的入参
- 函数内的 `content` 嵌套逻辑（`if (typeof content === 'object')`）与 `ProgressMessage.content` 实际是 `string` 的约束矛盾，若 `content` 真的是字符串，则 object 分支永远不执行，是冗余逻辑

**修复建议**：
将签名改为：
```typescript
function formatProgressContent(msg: ProgressMessage): string {
  const cleaned = msg.content.replace(/[\u{1F300}-\u{1F9FF}]/gu, '').trim()
  return cleaned.length > 100 ? cleaned.slice(0, 100) + '...' : cleaned
}
```

---

### F-7 🟡 CredentialManager.vue — 凭据列表请求无鉴权 Header

- **文件**：`frontend/src/api/credentials.ts`，第 58 行
- **对比**：`frontend/src/api/customer.ts`（使用了 `getAuthHeader()`）

**问题描述**：

`credentials.ts` 中所有函数（`listCredentials`、`getCredential`、`createCredential`、`updateCredential`、`deleteCredential`）的 `fetch` 调用**均未携带 `Authorization` Header**：

```typescript
// credentials.ts 第 58 行
const response = await fetch(url.toString())  // ← 无 Authorization

// credentials.ts 第 68 行
const response = await fetch(`${apiBase}/credentials/${credentialId}`)  // ← 无 Authorization
```

而后端 `src/api/credentials.py` 的所有路由都有 `current_user: dict = Depends(get_current_user)` 依赖，需要验证 Bearer Token。

对比 `customer.ts` 的正确做法：
```typescript
const res = await fetch(`${API_BASE}/customers?${params}`, {
  headers: { ...getAuthHeader() }  // ← 正确携带了鉴权头
})
```

**影响**：
- 凭据管理的所有接口调用都会因 token 缺失而返回 **401 Unauthorized**
- 尽管后端的 `get_current_user` 返回 `None` 而非抛出异常（允许匿名访问），但会导致 `current_user.get("user_id")` 报 `AttributeError`，或所有凭据被查询到 `user_id=None` 下的数据

**修复建议**：
在 `credentials.ts` 中引入 `getAuthHeader` 并在所有 `fetch` 调用中添加：
```typescript
import { getAuthHeader } from './auth'
// ...
headers: { ...getAuthHeader() }
```

---

### F-8 🟢 LoginModal.vue — 测试提示语硬编码在生产代码中

- **文件**：`frontend/src/components/LoginModal.vue`，第 73-74 行

**问题描述**：

```html
<p v-if="currentTab === 'password'" class="text-sm text-slate-400">
  测试环境默认密码为：888888
</p>
<p v-else class="text-sm text-slate-400 mt-1">
  测试环境验证码固定为：888888
</p>
```

测试环境 Mock 凭据（密码/验证码固定为 `888888`）被**硬编码**在 UI 文字中，所有用户均可见。

**影响**：
- 一旦部署到生产环境，页面仍然显示"测试环境验证码固定为：888888"，暴露系统安全信息
- 与后端 `src/api/auth.py` 中的 Mock 实现形成联动——任何人都知道可以用任意手机号 + `888888` 登录

**修复建议**：
- 通过环境变量 `VITE_SHOW_TEST_HINT=true` 控制是否显示，或
- 删除此提示语，在内部测试文档中记录

---

### F-9 🟢 SendMessageRequest 类型定义中 files 字段类型错误

- **文件**：`frontend/src/types/index.ts`，第 18-22 行

**问题描述**：

```typescript
export interface SendMessageRequest {
  message: string
  session_id: string
  files?: File[]   // ← 使用的是原生 File 对象
}
```

但实际发送消息时（`useAgent.ts` 第 83-91 行），`files` 字段传的是 `UploadedFile[]`（已上传到服务器后的返回对象，含 `file_id`、`name`、`mime_type` 等字段），而非原生 `File` 对象：

```typescript
// useAgent.ts 第 83-91 行
await sseManager.connect(
  content,
  sessionId.value,
  currentFiles.value.length > 0 ? [...currentFiles.value] : undefined,  // ← UploadedFile[]
  ...
)
```

`SendMessageRequest.files` 类型 `File[]` 从未被实际使用，真正在用的是 `UploadedFile[]`。

**影响**：
- `SendMessageRequest` 接口定义与实际使用脱节，造成误导
- 若其他组件参照此接口实现文件发送功能，会使用错误的类型

**修复建议**：
```typescript
import type { UploadedFile } from '@/api/agent'

export interface SendMessageRequest {
  message: string
  session_id: string
  files?: UploadedFile[]   // 改为上传后的文件引用
}
```

---

### F-10 🟢 useAgent.ts — getToolDisplayName 中 const 声明在 switch-case 中无块级作用域

- **文件**：`frontend/src/composables/useAgent.ts`，第 157-199 行

**问题描述**：

`getToolDisplayName` 函数使用 `switch-case` 但每个 case 中的 `const` 声明没有用 `{}` 包裹块级作用域：

```typescript
switch (toolName) {
  case 'web_search':
    const keyword = (toolArgs as any)?.keyword || ''   // ← case 中直接 const
    return `网络搜索「${keyword.slice(0, 20)}...」`
  case 'email_send':
    const to = (toolArgs as any)?.to || ''              // ← 可能与上面的 const 冲突
    return `发送邮件至「${to}」`
  ...
}
```

在 JavaScript/TypeScript 中，`switch` 的所有 case 共享同一个词法作用域。多个 case 中使用不同变量名（`keyword`、`to`、`folder` 等），目前不会报错，但这是一种不良实践，容易在以后添加同名变量时引发 `SyntaxError: Identifier has already been declared`。

TypeScript/ESLint 通常会对此发出 `no-case-declarations` 警告。

**修复建议**：为每个 case 添加 `{}` 块包裹：
```typescript
case 'web_search': {
  const keyword = (toolArgs as any)?.keyword || ''
  return `网络搜索「${keyword.slice(0, 20)}...」`
}
```

---

### 前端审核汇总

| 编号 | 严重级别 | 文件 | 问题简述 |
|------|---------|------|---------|
| F-1 | 🔴 严重 | `api/customer.ts` | `API_BASE` 拼写错误，所有客户接口 404 |
| F-2 | 🔴 严重 | `api/credentials.ts` | `new URL()` 不支持相对路径，listCredentials 报错崩溃 |
| F-3 | 🟡 中等 | `composables/useAgent.ts` + `ChatContainer.vue` | sessionId 双轨制竞态，消息可能存入孤立 session |
| F-4 | 🟡 中等 | `components/ChatContainer.vue` | AI 回复从未保存到后端，切换会话后 AI 消息全消失 |
| F-5 | 🟡 中等 | `composables/useAuth.ts` | `User` 接口与 `types/index.ts` 重复定义 |
| F-6 | 🟡 中等 | `components/MessageItem.vue` | `formatProgressContent` 参数类型签名与实际调用不符 |
| F-7 | 🟡 中等 | `api/credentials.ts` | 所有凭据接口无 Authorization Header，后端 401 |
| F-8 | 🟢 可清理 | `components/LoginModal.vue` | 测试密码提示语硬编码在 UI 中，生产环境泄露信息 |
| F-9 | 🟢 可清理 | `types/index.ts` | `SendMessageRequest.files` 类型与实际使用不符 |
| F-10 | 🟢 可清理 | `composables/useAgent.ts` | `switch-case` 中 const 缺少块级作用域 |

---

## 附录：审核方法说明

### 工具与方法

1. **全文精读**：完整阅读了以下核心文件：
   - `src/core/agent.py`（2552 行）
   - `src/main.py`（758 行）
   - `src/db/models.py`（517 行）
   - `src/api/auth.py`（464 行）、`src/api/session.py`（295 行）、`src/api/credentials.py`、`src/api/customer.py`
   - `src/core/plan_manager.py`（593 行）
   - `src/subagents/registry.py`（387 行）
   - `src/llm/gateway.py`、`src/llm/key_pool.py`、`src/config/settings.py`
   - 5 个冗余模块全文（共 1857 行）
   - 前端全部文件：`frontend/src/api/`（5 个文件）、`frontend/src/composables/`（3 个文件）、`frontend/src/components/`（9 个 Vue 文件）、`frontend/src/types/index.ts`

2. **引用追踪**：对所有冗余模块的核心类名进行全项目 `grep` 搜索，确认零引用状态

3. **前后端对比**：系统比对所有前端 API 调用与后端路由定义，重点核查：
   - URL 路径拼接（发现 F-1、F-2 两个严重问题）
   - 鉴权 Header（发现 F-7）
   - 响应体字段名与 TypeScript 接口一致性
   - 数据持久化逻辑（发现 F-4）

### 未覆盖范围

以下区域本次未深入审核，建议后续关注：
- `src/channels/`（企业微信/钉钉/飞书适配器，需要结合各平台文档验证）
- `src/tools/email/`（SMTP/IMAP 实现，需要实际发送测试验证）
- `src/tools/browser/`（Playwright 集成，需要运行时验证）
- `src/multi_tenant/`（多租户权限模型，需要结合数据库 Schema 验证）
- `src/security/`（加密存储实现，需要结合密钥管理方案验证）
- `subagents/`（子智能体 YAML 配置，建议单独审核提示词质量）

---

*本报告生成于 2026-03-25，前端专项审核补充于 2026-03-25。代码一行未动，仅记录问题。*
