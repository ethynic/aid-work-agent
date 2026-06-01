# PilotDeck 智能体架构深度调研

> 调研日期：2026-05-29
> 仓库：https://github.com/OpenBMB/PilotDeck
> 定位：面向任务制的 AI Agent 生产力平台，以 WorkSpace 为单位，重新定义智能体的操作边界与记忆演化
> 技术栈：TypeScript，支持 CLI / Electron 桌面端 / Web UI
> 开源协议：AGPL 3.0（2026.05.28 刚开源）
> 开发方：清华大学 THUNLP + ModelBest + OpenBMB + AI9Stars

---

## 一、项目概览

PilotDeck 是一个自主编程智能体框架，核心定位类似 Claude Code / Cursor。它以 **WorkSpace（工作空间）** 为核心隔离单位，每个项目拥有独立的文件系统、记忆存储和技能集。

**核心卖点**：
- WorkSpace 级隔离——消除跨项目记忆污染
- 白盒可追溯记忆——全程透明，含"梦境模式"自动整理
- 智能路由与成本优化——实测社交媒体场景节省 ~70% 成本，复杂任务仅需 1/6 成本即可媲美顶级模型
- Always-on 执行——用户离开后智能体继续后台工作

---

## 二、架构全景

### 2.1 目录结构

```
src/
  adapters/       -- 渠道适配器、Web UI 桥接
  agent/          -- 核心智能体循环、Turn 执行、会话、子智能体
  always-on/      -- 常驻智能体（cron、workspace、config）
  cli/            -- CLI 入口、本地网关、代理
  context/        -- 上下文管理（预算、压缩、记忆、Prompt 组装）
  cron/           -- 定时任务运行时
  extension/      -- 插件系统（hooks、skills、contributions）
  gateway/        -- 网关、会话路由、权限、用户交互通道
  lifecycle/      -- 生命周期钩子分发
  mcp/            -- MCP（Model Context Protocol）客户端集成
  model/          -- 模型抽象层（提供商、流式、规范化协议）
  permission/     -- 多层权限/决策引擎
  router/         -- 智能路由（token saver、场景路由、降级链）
  session/        -- 会话存储、文件系统、转录、工作树、元数据
  task/           -- 任务协议和运行时
  tool/           -- 工具注册、执行、调度、内置工具、审计
  web/            -- Web 客户端/服务端
```

### 2.2 三大架构支柱

PilotDeck 没有传统的 `core` 层，而是以三个"支柱"构成核心：

1. **Context（上下文管理）**：Token 预算、自动压缩、记忆注入、Prompt 组装
2. **Router（智能路由）**：场景路由、复杂度分级、降级链、成本优化
3. **Gateway（网关）**：会话管理、权限控制、用户交互通道

### 2.3 请求流程

```
用户输入 → TurnRunner → AgentLoop
                         |
                         +-- tryAutoCompact（主动压缩）
                         +-- Router.decide() + Router.execute()（流式调用模型）
                         +-- 组装助手消息
                         +-- 如有 tool_calls: Scheduler.executeAll()
                         +-- ensureToolResultPairing + projectToolResults
                         +-- 追加到消息列表
                         +-- decideLoopContinuation（有工具调用则继续）
                         +-- 循环直到 stop_reason 或 max_turns
```

---

## 三、8 个亮点设计详解

### 亮点 1：AsyncGenerator 作为 Agent 循环的核心骨架

**设计**：`AgentLoop.run()` 返回 `AsyncGenerator<AgentEvent>`，每一步通过 `yield*` 组合。所有中间状态（turn_started、model_event、tool_calls_detected、tool_result、turn_completed）作为类型化事件 emit。

```typescript
// TurnRunner 包装 AgentLoop
async *run(options): AsyncGenerator<AgentEvent, TurnRunnerResult> {
  yield { type: "turn_started", ... };
  // 生命周期钩子可返回 effects: [{ type: "block" }] 阻止执行
  yield* AgentLoop.run();  // 组合委托，所有子事件自动上浮
}
```

**循环继续判断**是纯函数 `decideLoopContinuation`：如果助手消息包含 tool_calls 则继续，否则停止。决策逻辑与编排逻辑彻底分离。

**关键文件**：
- `src/agent/loop/AgentLoop.ts`（~800+ 行，整个项目最大的单文件）
- `src/agent/loop/decideLoopContinuation.ts`
- `src/agent/turn/TurnRunner.ts`

#### 深度对比：Callback 模式 vs AsyncGenerator 模式

> **注意（2026-06-01）**：以下对比中的"我们的模式"描述的是 AsyncGenerator 迁移**之前**的旧架构（Callback + 线程轮询）。迁移已完成，当前架构已采用 AsyncGenerator + yield 模式，与 PilotDeck 的模式一致。详见 [AsyncGenerator 迁移方案设计](../infrastructure/async-generator-migration-design.md)。本节保留作为历史参考。

**一句话概括（迁移前）**：我们的 agent 循环产出数据是"推"（callback 推给消费者），PilotDeck 是"拉"（消费者通过迭代器拉取）。前者耦合，后者解耦。**迁移后两者模式一致**。

##### 我们的模式：Callback + 线程轮询

```
Agent.process_message()
  │
  ├── 接收 progress_callback 参数
  ├── 在循环中调用 LLM → 执行工具 → 把事件通过 callback 推出去
  └── yield 最终文本

main.py (SSE 端)
  │
  ├── ThreadPoolExecutor 启动 agent
  ├── agent 在独立线程 + 独立 event loop 中运行
  ├── agent 通过 callback 把事件推入 results['progress'] 列表
  ├── SSE generator 每 50ms 轮询 results['progress']
  └── 发现新事件 → 格式化为 SSE data: 帧 → 发给浏览器
```

**具体代码链路**：
1. `src/core/agent.py:1354-1389` 定义了 `send_progress`、`send_tool_start`、`send_tool_result` 等 5 个闭包，每个闭包包装 `progress_callback`
2. agent 循环中，执行工具前调用 `send_tool_start()`，执行后调用 `send_tool_result()` — 直接"推"
3. `src/main.py:1302` 的 `sync_progress_callback` 把事件 append 到 `results['progress']` 列表
4. `src/main.py:1422-1489` 的 `event_generator()` 每 50ms 轮询这个列表，取出新事件发给 SSE

##### PilotDeck 的模式：AsyncGenerator + yield* 组合

```
TurnRunner.run()          ← AsyncGenerator<AgentEvent>
  │
  ├── yield { type: "turn_started" }
  ├── yield* AgentLoop.run()    ← 组合委托，所有子事件自动上浮
  │     │
  │     ├── yield { type: "model_event", ... }
  │     ├── yield { type: "tool_calls_detected", ... }
  │     │     └── ToolScheduler 内部也 yield 单个工具结果
  │     ├── yield { type: "tool_result", ... }
  │     └── yield { type: "turn_completed" }
  │
  └── return AgentLoopRunResult

消费者（WebSocket Gateway）
  │
  ├── for await (const event of turnRunner.run()) { ... }
  └── 每收到一个 event 就序列化发给前端，无需轮询
```

##### 逐维度对比

| 维度 | 我们（迁移前：Callback + 线程轮询） | PilotDeck（AsyncGenerator + yield*） |
|------|---------------------------|--------------------------------------|
| **数据流向** | 推模式：agent 决定何时推数据 | 拉模式：消费者按自己节奏消费 |
| **事件传递** | `progress_callback(dict)` → 线程共享列表 → 50ms 轮询 | `yield event` → `for await` 直接消费 |
| **线程模型** | agent 在独立线程+独立 event loop；SSE 在主 event loop | 全部在同一个 async 上下文中，无线程 |
| **中间件插入** | 要改 callback 闭包 | 在 yield 链中间插一个 `yield*` 转换即可 |
| **事件类型** | dict 自由格式，靠 `type` 字段区分 | TypeScript 类型化联合体，编译期检查 |
| **取消机制** | Redis flag + `cancel_check()` 回调 | `AbortController` + `yield` 点自然中断 |
| **子智能体** | 另起线程+event loop，progress 包装回调传递 | `yield* SubAgentSession.run()`，事件自动上浮 |
| **延迟** | 50ms 轮询间隔 + 线程间数据拷贝 | 事件即产即消，零延迟 |

##### 用代码具体说明差异

**场景：agent 执行一个工具调用**

我们的写法（`src/core/agent.py` 简化）：
```python
# 1. 通知前端：工具开始
send_tool_start(tool_name, tool_args)  # → callback → 线程列表

# 2. 执行工具
result = await self.tool_executor.execute(tool_name, tool_args)

# 3. 通知前端：工具结果
send_tool_result(tool_name, result, success)  # → callback → 线程列表

# 4. SSE 端下次轮询（最多 50ms 后）才能拿到这两个事件
```

PilotDeck 的等效写法：
```typescript
// 1+2+3. 全部通过 yield，消费者实时收到
yield { type: "tool_calls_detected", calls: [...] };
// ... 工具调度器执行 ...
yield { type: "tool_result", name: toolName, result, success };
// 消费者的 for await 循环立刻收到，零延迟
```

**关键差异**：我们的 agent 需要知道"谁在消费我的事件"（callback 是谁传入的），PilotDeck 的 agent 不知道也不关心——它只管 yield，消费者自己决定怎么处理。

##### 迁移价值与成本评估（已实施）

**迁移已完成**（2026-06-01），详见 [AsyncGenerator 迁移方案设计](../infrastructure/async-generator-migration-design.md)。

**已实现的好处**：
- 消除线程+轮询的复杂度（`main.py` 里 200+ 行的线程管理代码已简化为 ~80 行）
- 事件零延迟（50ms → 即时）
- 子智能体事件通过 yield 传递（`subagent_progress_wrapper` 已删除）
- 中间件插入容易（加压缩、加审计、加限流，都在 yield 链上）
- 每个 yield 点天然是可测试的断言点

**可借鉴价值**：★★★★☆

---

### 亮点 2：智能模型路由（RouterRuntime）

**设计**：三层智能路由，根据请求复杂度自动选择最优模型。

**场景路由**：`decideScenario()` 将请求分类为显式场景、默认场景、tokenSaver、子智能体等，然后选择对应模型。

**Token Saver**：一个"裁判模型"评估每个请求的复杂度分级（如"简单问题" vs "复杂多步骤任务"），简单请求路由到更便宜的模型。结果按会话缓存为"粘性"路由决策。

**三层重试**：
1. **零使用量重试**：API 返回空响应时，重试最多 5 次（指数退避）
2. **瞬态网络重试**：ECONNRESET、socket hang up 等，指数退避+抖动（`baseDelay * 2^attempt + random(500ms)`），最大 30s
3. **降级链**：按场景配置的备选模型列表，逐个尝试

**内容感知降级**：事件在输出前先缓冲。一旦有内容事件（text_delta、thinking_delta、tool_call_start）输出给消费者，降级/重试就被锁定——防止重复文本。

```typescript
// 内容感知降级的关键逻辑
function isContentEvent(event): boolean {
  return event.type === "text_delta" || event.type === "thinking_delta" ||
    event.type === "tool_call_start" || ...;
}
// 一旦 yield 过内容事件，锁定降级
```

**关键文件**：
- `src/router/RouterRuntime.ts`
- `src/model/streaming/StreamingCheckpoint.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 模型路由 | 复杂度分级 + 场景路由 + 粘性缓存 | 仅 LLM Failover（A/B 提供商切换） |
| 重试 | 三层（空响应、网络错误、降级链） | Failover 切换 |
| 成本优化 | Token Saver 省约 70% 成本 | 无 |
| 降级安全 | 内容感知锁定防重复输出 | 无保护 |

**可借鉴价值**：★★★★★
智能路由是成本优化的核心。我们的 Failover 已有基础，应优先补上复杂度分级和内容感知降级。

---

### 亮点 3：Token 预算管理与三层自动压缩

**设计**：`TokenBudgetManager` 用 tiktoken（o200k_base）精确计算 token 数，当上下文占比超过阈值（0.8）时触发自动压缩。

**三层压缩策略**：
1. **Tier 1 — Micro（微压缩）**：截断旧的 tool_result 内容
2. **Tier 2 — Snip（裁剪）**：修剪中间轮次，保留头部+尾部锚点
3. **Tier 3 — Full（完整压缩）**：通过模型调用总结整个对话

压缩结果保持严格顺序：边界标记 → 摘要 → 保留的尾部 → 附件 → Hook 结果。

**上下文溢出恢复**（`ContextOverflowRecovery`）：
- 第一次尝试：剥离图片重试
- 第二次尝试：截断消息头部到 50% 重试
- 第三次尝试：截断到 25%
- 进一步：放弃

**关键文件**：
- `src/context/budget/TokenBudgetManager.ts`
- `src/context/compaction/AutoCompactionPolicy.ts`
- `src/context/compaction/CompactionEngine.ts`
- `src/context/compaction/MicroCompactionEngine.ts`
- `src/context/compaction/SnipEngine.ts`
- `src/context/recovery/ContextOverflowRecovery.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 上下文管理 | Token 精确计算 + 三层渐进压缩 | ShortTermMemory 滑动窗口（按消息数截断） |
| 压缩策略 | Micro → Snip → Full 三级 | 无压缩，直接丢弃旧消息 |
| 溢出恢复 | 渐进式截断 + 重试 | 无 |

**可借鉴价值**：★★★★★
三层渐进压缩是最值得借鉴的设计。我们当前滑动窗口直接丢弃旧消息，丢失了上下文。Micro 压缩（截断长 tool_result）实现简单但效果显著，可作为第一步。

---

### 亮点 4：白盒可追溯记忆系统 + 梦境模式

**设计**：记忆的生成、提取、存储、使用全过程透明可见。

**WorkSpace 级隔离**：每个项目拥有独立的记忆存储，消除跨项目记忆污染。

**"梦境模式"（Dream Mode）**：智能体空闲时自动整理记忆，类比人睡眠时大脑整理记忆。系统在空闲期对记忆进行提取、归纳、重组织。

**长期记忆注入**：`MemoryAttachmentBuilder` 从长期记忆提供商（EdgeClaw）检索相关记忆，注入到系统 Prompt 中。

**关键文件**：
- `src/context/memory/EdgeClawMemoryProvider.ts`
- `src/context/memory/MemoryAttachmentBuilder.ts`
- `src/context/memory/MemoryResolver.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 短期记忆 | 精确 Token 管理 + 自动压缩 | Deque 滑动窗口 |
| 长期记忆 | EdgeClaw 提供商 + 自动整理 | 无 |
| 主动整理 | 梦境模式（空闲时自动整理） | 无 |

**可借鉴价值**：★★★☆☆
梦境模式创意好但实现复杂度高。对我们来说，先实现三层压缩（亮点 3）比增加长期记忆更实际。

---

### 亮点 5：子智能体的工具域隔离

**设计**：子智能体继承父智能体的工具，但通过 `buildScopedRegistry()` 过滤：

```typescript
buildScopedRegistry() {
  // 1. 遍历父工具列表
  // 2. 过滤 allowedTools
  // 3. 剥离 plan-mode 工具
  // 4. 移除 agent 工具（防止嵌套）
  // 5. 移除 always_on_* 工具
  // 6. 对只读子智能体过滤写操作工具
}
```

**子智能体报告契约**：必须产出结构化的 5 字段 Markdown 报告：
- Scope（范围）
- Result（结果）
- Key files（关键文件）
- Files changed（变更文件）
- Issues（问题）

**上下文继承**：父消息通过 `buildForkedMessages()` 分叉，系统 Prompt 过滤（`applySystemPromptFilters`），文件读写状态克隆。

**并发工具调度**：`ConcurrentToolScheduler` 将工具调用分为两组——并发安全的并行执行（`Promise.all`），其余顺序执行。结果按原始调用顺序返回。

**关键文件**：
- `src/agent/sub/SubAgentSession.ts`
- `src/tool/scheduler/ConcurrentToolScheduler.ts`
- `src/tool/protocol/types.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 子智能体工具隔离 | 精细（读/写/嵌套/plan-mode） | 无隔离（继承所有工具） |
| 子智能体输出契约 | 结构化 5 字段报告 | 无约束 |
| 工具并发调度 | 自动识别安全/不安全并行 | 顺序执行 |

**可借鉴价值**：★★★★☆
工具域隔离和并发调度对多租户安全性至关重要。结构化报告契约也值得引入。

---

### 亮点 6：流式中断恢复（StreamingCheckpoint）

**设计**：跟踪已输出的 `partialText` 和 `tokensReceived`。网络中断时：

- 如果已有足够内容（>50 token, >100 chars）→ 构建 continuation 请求，在中断点继续
- 否则从头重试

```typescript
// 构建续传请求
buildContinuationRequest(partialText, originalMessages) {
  // 1. 将已输出的 partialText 作为助手消息
  // 2. 添加 "Continue from where you left off." 作为用户消息
  // 3. 发送给模型继续生成
}
```

**关键文件**：
- `src/model/streaming/StreamingCheckpoint.ts`
- `src/model/streaming/streamModel.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 流式中断 | 检查点 + 续传 | 中断即丢失 |
| 用户体验 | 无感知恢复 | 需要重新提问 |

**可借鉴价值**：★★★☆☆
实现复杂度较高，但理念值得学习。对 SSE 流式输出来说，可以先实现简单的检查点（记录已输出内容），后续再考虑续传。

---

### 亮点 7：五种 Hook 执行器 + 插件系统

**设计**：Hooks 支持 5 种执行器类型：
1. **command** — Shell 命令执行（bash/powershell）
2. **prompt** — 二次 LLM 调用
3. **http** — HTTP webhook
4. **agent** — 完整 agent 循环调用
5. **callback** — 进程内 JavaScript 函数（用于网关集成 hooks）

每个 Hook 支持：
- 条件匹配（`if` 字段支持 glob 模式、正则、管道分隔）
- 异步执行（`async: true` + 可选 `asyncRewake`）
- 超时限制
- `once` 标记（一次性执行）

**插件清单**支持多种贡献类型：
```typescript
type PilotDeckPluginManifest = {
  name: string;
  commands?: string | string[];
  skills?: string | string[];
  hooks?: PilotDeckHooksSettings;
  mcpServers?: Record<string, unknown>;
  mcpInstructions?: Record<string, unknown>;
  outputStyles?: string | string[];
};
```

**技能系统安全校验**：
- Slug 正则校验：`[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}`（防路径穿越）
- 文件大小上限：50MB
- 文件数量上限：500
- 危险扩展名检测（`.exe`、`.sh` 等）

**关键文件**：
- `src/extension/hooks/protocol/settings.ts`
- `src/extension/plugins/`
- `src/extension/skills/SkillManager.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| Hook 类型 | 5 种（command/prompt/http/agent/callback） | 仅简单事件推送 |
| 插件系统 | 多贡献类型清单 + 热重载 | 无 |
| 技能安全 | Slug 校验 + 容量限制 + 扩展名检测 | 无 |

**可借鉴价值**：★★★☆☆
Hook 系统扩展性好但我们的场景暂时不需要如此复杂。技能安全校验值得立即引入。

---

### 亮点 8：Tool 执行管道与权限系统

**设计**：工具执行经过严格的 11 步管道：

```
1. Abort 检查
2. Registry 查找
3. JSON Schema 验证
4. PreToolUse 生命周期 Hook（可阻止/拒绝/修改输入）
5. 工具级自定义验证（validateInput）
6. Plan-todo 门控检查
7. 权限决策（allow/deny/ask/cancel）+ 可选 PermissionRequest Hook
8. 实际执行
9. 结果大小限制
10. PostToolUse 生命周期 Hook
11. 审计记录
```

**工具定义协议**包含丰富的元信息：
```typescript
type PilotDeckToolDefinition = {
  name: string;
  isReadOnly(input): boolean;        // 标记是否只读
  isConcurrencySafe(input): boolean; // 标记是否可并发执行
  isDestructive?(input): boolean;    // 标记是否破坏性操作
  requiresUserInteraction?(input): boolean; // 是否需要用户交互
  validateInput?(input, context): Promise<ValidationResult>;
  checkPermissions?(input, context): Promise<PermissionResult>;
};
```

**关键文件**：
- `src/tool/execution/ToolRuntime.ts`
- `src/tool/protocol/types.ts`
- `src/tool/audit/ToolAuditRecorder.ts`

**与我们的对比**：
| 维度 | PilotDeck | 我们的系统 |
|------|-----------|-----------|
| 工具管道 | 11 步（验证→Hook→权限→执行→审计） | 参数验证 + 执行 |
| 工具元信息 | readOnly/concurrencySafe/destructive | 仅 name + description |
| 审计 | 完整的调用审计记录 | 无 |

**可借鉴价值**：★★★★☆
工具元信息（readOnly、destructive 标记）对多租户安全性很有价值。审计记录也值得引入。

---

## 四、依赖注入架构

`AgentRuntimeDependencies` 是核心的依赖注入接口，agent 循环接收：

```typescript
{
  router: AgentRouterRuntime,       // 决策 + 流式 + 用量
  tools: { scheduler, registry },   // 工具调度和注册
  context: AgentContextRuntime,     // 上下文管理
  lifecycle: LifecycleRuntime,      // 生命周期钩子
  elicitation: ElicitationChannel,  // 用户交互通道
  fileHistory, fileUpdateNotifier,
  planFileManager, planTodoManager,
  eventEmitter, drainEvents,        // 事件传播
}
```

这使得每个组件都可以独立 mock 和测试。

---

## 五、WebSocket 网关协议

整个 UI 通过类型化 WebSocket 帧协议通信：

```typescript
type WsGatewayFrame =
  | WsHelloFrame        // 握手（协议版本 + auth token）
  | WsHelloOk           // 握手确认
  | WsRequestFrame      // RPC 请求（list_sessions, skill CRUD, cron 管理）
  | WsResponseFrame     // RPC 响应
  | WsEventFrame        // 实时 Turn 更新（带 seq 序号 + final 标志）
  | WsNotificationFrame // 服务端推送（config reload, task completion）
```

React 端的 `useSessionStore` 维护三个消息流：
- `serverMessages`：后端持久化的 JSONL
- `realtimeMessages`：WebSocket 实时流
- `merged`：两者合并 + 去重

**Subscribe 模式绕过 React 批处理**：`WebSocketContext.subscribe()` 在 `setLatestMessage` 之前同步触发所有订阅者，避免 React 18 自动批处理丢失高频事件。

---

## 六、对标的总结与优先级建议

按**实现价值 × 实现难度**排序的借鉴优先级：

| 优先级 | 亮点 | 预期收益 | 实现难度 |
|--------|------|---------|---------|
| P0 | 三层自动压缩（Micro→Snip→Full） | 大幅提升长对话能力 | 中 |
| P0 | 智能路由 + 内容感知降级 | 节省 50-70% API 成本 | 中 |
| P1 | 工具元信息（readOnly/destructive 标记） | 多租户安全增强 | 低 |
| P1 | 子智能体工具域隔离 | 安全性 | 中 |
| P1 | 技能安全校验（Slug + 容量限制） | 安全性 | 低 |
| P2 | AsyncGenerator 循环重构 | 架构可维护性 | 高 |
| P2 | 工具执行审计记录 | 运维可观测性 | 低 |
| P2 | 流式检查点与续传 | 用户体验 | 高 |
| P3 | 梦境模式记忆整理 | 长期记忆质量 | 高 |
| P3 | 五种 Hook 执行器 | 扩展性 | 高 |

### 建议实施路径

**第一阶段（P0，建议立即启动）**：
1. 在 `ShortTermMemory` 中引入三层压缩，先实现 Micro（截断长 tool_result）
2. 在 LLM Gateway 中增加复杂度分级路由，复用现有 Failover 框架

**第二阶段（P1）**：
3. 给 `BaseTool` 增加 `is_readonly`、`is_destructive` 属性
4. 在子智能体系统中实现工具过滤
5. 给 SKILL.md 加载增加安全校验

**第三阶段（P2-P3，按需推进）**：
6. 评估 AsyncGenerator 循环重构的可行性
7. 流式检查点作为 SSE 增强的长期目标
