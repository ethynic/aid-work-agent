# Agent 应用层架构优化设计（统一入口 + 内核收敛）

> 日期：2026-10-01
>
> 状态：设计完成，尚未开始编码
>
> 替代：本文替代已删除的《统一 Agent Run 应用服务、持久执行与完成通知设计基线（P0）》及其开发执行计划。旧方案要求 Web、全部生产渠道、通知、后台进程组成一个完整候选版一次性上线，中间阶段不能进生产，实施全部失败。本文改为**每一步都能独立合并、独立上线、行为不变**的渐进式路线。
>
> 关联：[母体 Agent 收敛原则](agent-kernel-convergence-principles.md)（冻结增长 + 伴生绞杀，本文是它的具体落地路线）、[运行时安全加固设计](agent-runtime-safety-hardening-design.md)

## 1. 现状问题（2026-10-01 核实）

### 1.1 入口各自调用 Agent，生命周期分散

| 入口 | 位置 | 调用方式 | 自行处理的事 |
|------|------|----------|--------------|
| Web 流式 | `src/main.py` `/api/chat/stream` | `process_message_with_feedback(surface="web")` | SSE 包装、`session_queue.mark_responding`、取消（`sse_manager` + `session_queue`）、`SessionRecordManager` 收尾、消息落库（含取消轮次落库） |
| Web 同步 | `src/main.py` `/api/chat` | `process_message_sync` | 同步返回、落库 |
| CLI | `src/main.py` `cli_chat` | `master_agent.process_message` | 仅调试 |
| 第三方渠道 | `src/channels/session.py` `process_and_persist` → `session_queue.enqueue_and_process` | `process_message_sync` + `progress_callback` | 2 秒合并、串行锁、取消重跑、`channel_messages` 落库、图片经 `_last_response_images` 实例属性桥接 |
| 企微个人 RPA | `src/saas/api/wecom_personal_rpa_routes.py` | 经 `session_queue` 调 `process_message_sync` | 同渠道 |
| 定时任务 | `src/scheduler/executor.py` | `process_message_sync` | 自建 cron 会话、结果写回会话、重试 |
| 桌面 D1 | `src/desktop_agent/turn.py` | `process_message` / `continue_tool_call` | 自行收集 `tool_messages`、落库、远程工具挂起 |

同一件事（会话串行、取消、计费收尾、消息落库、事件转发）在 5 处以上各写一遍，修一个入口的 bug 常常漏掉其他入口。`process_message` / `process_message_sync` / `process_message_with_feedback` / `continue_tool_call` 四个方法签名各不相同，参数靠 `**kwargs` 和 ContextVar 透传。

### 1.2 `agent.py` 职责过载

`src/core/agent.py` 当前 4026 行。主循环之外还包含：系统提示词组装（`_build_system_prompt`、`_build_base_system_prompt`、`_resolve_db_subagent_prompt`、`_load_extra_md`、`_load_template_files`、`_load_knowledge_sources`）、长期记忆与回复风格（`_load_long_term_memory`、`_resolve_reply_style`、`_handle_remember_intent`）、历史重建（`_reload_memory_from_db`、`_load_channel_history`、`_build_messages`、`_reorder_messages_for_llm`）、上下文压缩（`_run_compression_phase`）、澄清状态（`_*_pending_clarification`）、子智能体委派（`_delegate_to_subagent_direct`、`execute_as_subagent`）。

**注意**：`tests/unit/test_agent_structure_guard.py` 的冻结基线是 3977 行，而当前文件为 4026 行，守卫测试理应失败。S0 需先核实该守卫是否在日常回归中被执行。

### 1.3 真正的问题排序

1. 入口分叉（影响每一次 bug 修复和新渠道接入）——**本文主线**
2. `agent.py` 不可单测、不可替换——**本文主线**
3. 发版 / 重启打断长任务、SSE 断开后任务丢失——**真实但次要，本文只预留接口，按需实施**（见 §5 S5）

## 2. 目标架构

```mermaid
flowchart TB
  subgraph Ingress[入口适配层]
    W[Web / API<br/>SSE 流式]
    G[渠道 Gateway<br/>验签·去重·合并·回发]
    C[自有客户端<br/>UI 身份]
    T[内部触发<br/>定时·任务·recap]
  end

  App[Agent 应用服务（唯一入口）<br/>run(request, sink)<br/>会话串行·租户身份·取消·消息落库·计费收尾]

  subgraph Host[执行宿主：现为 API 进程内，可整体平移到独立 runner]
    Ctx[上下文装配<br/>Prompt·记忆·历史·知识]
    L[Agent Loop 内核<br/>轮次·工具调用·澄清·压缩]
    Sink[事件出口 EventSink<br/>进度·澄清·结果]
    M[模型网关<br/>供应商·限流·计量]
    TE[工具执行层<br/>装配·策略·执行上下文]
    S[服务端工具]
    Sub[子智能体委派<br/>递归复用同一内核]
    LP[本地工具代理<br/>Invocation 队列]
  end

  D[受信设备 Runtime<br/>主动拉取·回传结果]
  Infra[(PostgreSQL · Redis · 文件与产物 · Trace/计费明细)]

  W <--> App
  G <--> App
  C <--> App
  T <--> App
  App --> L
  Ctx --> L
  L --> Sink
  Sink --> App
  L --> M
  L --> TE
  TE --> S
  TE --> Sub
  TE --> LP
  D -- 领取/回传 --> LP
```

### 2.1 各层职责

| 层 | 负责 | 不负责 |
|----|------|--------|
| 入口适配层 | 协议解析、验签、平台消息去重、合并窗口、把 `AgentRequest` 交给应用服务、实现自己的 `EventSink`（SSE 写出 / 渠道回发 / 同步收集） | 会话串行、取消状态、计费、消息落库、直接调用 `Agent` |
| Agent 应用服务 | 会话串行锁（复用 `session_queue`）、构造 `User` 与租户上下文、选择主/子智能体、取消令牌、`SessionRecordService` 生命周期、消息落库（按 web/channel 分表）、计费收尾、异常收敛 | Prompt 内容、工具调度、渠道协议 |
| 上下文装配 | 根据 `AgentProfile + 会话 + 用户` 产出 system prompt 与 messages，含压缩触发判断 | 调用模型执行业务回答 |
| Agent Loop 内核 | 模型轮次、工具调用解析与派发、澄清/挂起、在安全点检查取消、产出事件 | 读写会话表、计费收尾、渠道差异、领域特例（video/travel/BOSS/wecom） |
| 事件出口 | 统一事件类型（沿用 `src/core/agent_events.py`），把事件交给入口实现 | 决定事件如何呈现 |
| 模型网关 | 供应商、KeyPool、failover、usage 回传 | 业务逻辑 |
| 工具执行层 | 装配（`src/tools/assembly.py`）、`ToolExecutionContext`、策略判断、结果收敛 | 主循环控制 |
| 本地工具代理 + 设备 | 生成 Invocation，设备主动拉取并回传；沿用 `src/local_tools` | 推进对话 |

### 2.2 依赖方向规则

- 入口 → 应用服务 → 内核，单向。入口禁止 import `src.core.agent`。
- 内核不 import 入口、渠道、`src/main.py`、`src/channels/*`、具体领域模块。
- 上下文装配是纯函数层（输入数据 → 输出 messages），数据读取通过注入的仓储接口完成，便于用快照测试。
- 子智能体委派复用同一个内核，不另起循环；它是当前请求内的一个步骤，有独立 Trace span，不单独建顶层会话生命周期。

## 3. 关键契约

### 3.1 `AgentRequest`

```python
@dataclass(frozen=True)
class AgentRequest:
    session_id: str
    source: Literal["web", "web_sync", "channel", "scheduler", "desktop", "cli"]
    user: User                      # 已鉴权，含 tenant_id
    input_text: str
    attachments: tuple[dict, ...] = ()
    subagent_id: Optional[str] = None
    channel: Optional[str] = None   # wecom_kf / dingtalk / ...
    extra_system_prompt: Optional[str] = None
    request_context: Optional[AgentRequestContext] = None
    verbose: Optional[VerboseFeedbackConfig] = None
    continuation: Optional[ToolContinuation] = None  # 工具结果续接（替代 continue_tool_call）
```

替代当前散落在 `process_message*` 上的十几个位置参数与 `_` 前缀私有参数。

### 3.2 `EventSink`

```python
class EventSink(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...
```

内置实现：

| 实现 | 用途 | 替代 |
|------|------|------|
| `SseSink` | Web 流式，写入 SSE 队列 | `main.py` 中的事件循环转写 |
| `CollectingSink` | 同步返回（`/api/chat`、定时任务、渠道），收集文本与图片 | `process_message_sync` 的拼接逻辑与 `_last_response_images` 实例属性桥接 |
| `ChannelProgressSink` | 渠道过程提示，包装现有 verbose 限流 | `progress_callback` |

verbose 过滤（`iter_with_verbose_feedback`）作为 sink 装饰器实现，不再分成两个入口方法。

### 3.3 `AgentService`

```python
class AgentService:
    async def run(self, request: AgentRequest, sink: EventSink,
                  cancel: CancelToken) -> AgentResult: ...
```

- `AgentResult` 含最终文本、图片 `ImageRef` 列表、终态（completed / cancelled / failed / waiting_clarification）。
- 取消统一走 `CancelToken`，内部桥接现有 `sse_manager.is_cancelled` 与 `session_queue.check_cancel`，入口不再自己拼 lambda。
- 计费收尾、Trace `on_complete`、取消轮次落库全部在 `run` 的 `finally` 中完成，入口不再重复。

### 3.4 执行宿主的可替换性

`AgentService.run` 只依赖 `AgentRequest`、`EventSink`、`CancelToken` 三个可序列化或可远程化的对象。以后若需要独立 runner：入口把 `AgentRequest` 写入表，runner 进程调用同一个 `AgentService.run`，`EventSink` 换成「写事件表 + Redis 通知」的实现。上层入口与下层内核都不需要改动。**在 S5 触发条件满足前不做这一步。**

## 4. 约束

1. **行为不变**：S1–S4 的每一个提交都是纯结构调整，禁止顺手改行为；行为变更另起提交。
2. **一次只切一个入口**：每切换一个入口单独合并、单独上线、单独回归，出问题只回退这一个入口。
3. **保留兼容壳**：`process_message_sync` 等旧方法在全部调用方迁完前保留，内部转调新路径；全部迁完后再删除。
4. **新逻辑不进 `agent.py`**：沿用收敛原则 P1，新增能力落在应用服务、上下文装配或独立模块。
5. **不新增表**：S1–S4 不新增数据库表，复用 `chat_messages` / `channel_messages` / `chat_records` 和现有 Redis 键。

## 5. 演进步骤

| 步骤 | 内容 | 主要文件 | 验收 |
|------|------|----------|------|
| S0 | 核实 `test_agent_structure_guard.py` 是否在回归中运行，按当前行数校准基线；为 6 类入口各补一条行为快照测试（输入 → 事件序列 + 落库行 + 计费记录） | `tests/unit/`、`tests/integration/` | 快照测试在当前代码上全绿 |
| S1 | 新建 `AgentRequest` / `EventSink` / `CancelToken` / `AgentService`，内部先直接调用现有 `process_message`；`process_message_sync` 与 `process_message_with_feedback` 改为转调 | `src/services/agent_app/`（新） | S0 快照全绿 |
| S2 | 逐个切换入口：定时任务 → 渠道（`channels/session.py`）→ Web 同步 → Web 流式 → 桌面 D1（或直接删除，视桌面规划）。会话串行、取消、落库、计费收尾从入口搬进 `AgentService` | `src/scheduler/executor.py`、`src/channels/session.py`、`src/main.py`、`src/desktop_agent/turn.py` | 每切一个入口：该入口快照 + 相邻回归全绿，可独立上线 |
| S3 | 从 `agent.py` 拆出上下文装配为独立模块（system prompt、记忆、风格、历史重建、压缩判断） | `src/core/context/`（新） | 同输入的 messages 快照前后一致；`agent.py` 行数下降并下调守卫基线 |
| S4 | `agent.py` 剩余部分收敛为 Loop 内核：去掉对 `SessionRecordManager`、渠道、落库的直接依赖，改为由应用服务注入；子智能体委派改为复用内核 | `src/core/agent.py`、`src/subagents/executor.py` | 依赖方向守卫测试（内核不 import 渠道/入口）+ 全部入口快照全绿 |
| S5（按需） | 执行宿主平移到独立 runner + 持久事件 | 视需求另行设计 | 触发条件：发版/重启打断长任务成为实际投诉，或出现明确需要「离开页面后继续执行」的已批准需求 |

S1 是后续所有步骤的前提；S3 与 S2 可以并行，但不能同时修改同一文件。

## 6. 非目标

- 不做持久 Run 状态机、租约、outbox、通知中心（留给 S5 按需设计）。
- 不统一 `chat_messages` 与 `channel_messages`，分表规则不变（见 `database_dev.md`）。
- 不重写 `session_queue` 的合并与串行算法，只把调用点收进应用服务。
- 不改变任何对外 API、SSE 事件格式、渠道回复行为。

## 7. 风险与应对

| 风险 | 应对 |
|------|------|
| 入口里隐含的特殊处理在迁移时丢失（如取消轮次落库、渠道图片桥接、cron 会话创建） | S0 先用快照测试固化行为；迁移时逐条对照 §1.1 表格「自行处理的事」 |
| ContextVar（`SessionRecordManager`、工具执行上下文）在新路径中设置时机变化导致计费漏记 | `AgentService.run` 显式持有 record 并传入内核；按 `billing_audit.md` §3.5 核对 |
| 多个开发者/智能体同时改 `agent.py` 冲突 | S3、S4 期间 `agent.py` 只允许本路线的拆分提交 |
| 拆分中途停滞 | 每一步都可单独上线，停在任何一步系统都处于一致、可用状态 |
