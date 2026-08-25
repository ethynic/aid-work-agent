# Task Plane Phase 0+1 落地设计（企业任务执行操作系统最小切片）

> 版本：v1.0
> 日期：2026-08-19
> 状态：设计定稿，待开发
> 上位设计：[Task/Session/Execution/Artifact 模型设计](enterprise-task-model-design.md) / [总体集成架构](enterprise-agent-platform-integration-design.md) / [Policy Engine](enterprise-policy-engine-design.md)
> 关联原则：[母体 Agent 收敛原则](../agent-kernel-convergence-principles.md)（本文交付 P1/P2/P4）
> 开发计划：[plan-task-plane-phase0-1.md](../../plans/plan-task-plane-phase0-1.md)

本文档是可直接开工的实现设计：所有表结构、模块落点、接入点、SQL、状态映射均已定稿，开发者不需要再做方案决策。文中引用的行号以 2026-08-19 工作区为基线（`src/core/agent.py` 4284 行），开发时以函数名定位为准。

---

## 1. 目标与非目标

### 1.1 目标（本切片交付什么）

1. **旁路 Task/Execution 索引**：为每一次 Agent 对话轮次建立 `enterprise_tasks` / `task_session_links` / `agent_executions` 记录，并把 `chat_records` 关联到 `execution_id`——为多智能体协作体系、Evidence Ledger、Evaluation 提供稳定主键（`task_id` / `execution_id` / `session_link_id`）。
2. **AgentRelease 最小快照**：每次执行冻结数字员工定义摘要（prompt hash + 工具清单 + 模型），解决协作体系 Phase 1 的「冻结 AgentRelease」前置。
3. **Policy shadow 决策**：在现有两处权限检查点旁路记录 allow/deny（只记录，绝不改变判定结果），为 Policy Engine 落地积累真实决策数据。
4. **安全债清偿（P0）**：`scheduled_tasks`/`scheduled_task_logs` 补租户字段并回填；知识库删除/chunks/下载三处补对象级租户条件；`obs_traces.total_cost` 与计费闭环。
5. **契约冻结（Phase 0）**：事件信封与执行信封的 canonical schema 落 `contracts/task-plane/`；全量业务工具 effect/幂等清单落档。
6. **Kernel 原则交付**：agent.py 行数结构守卫（P2）、冻结增长接线规范（P1）、工具调用分发 seam 抽取（P4，可独立延后的 Phase K）。

### 1.2 非目标（明确裁剪，防止范围蔓延）

| 裁剪项 | 归属 |
|---|---|
| `task_participants` / `artifacts` / `artifact_versions` / `task_artifact_links` 表 | Artifact 映射属上位 Phase 2（work_outcomes 投影），参与者属 Phase 4 |
| Execution Step 级记录（每次工具调用一行） | 工具级明细继续由 `chat_records.execution_details` 与 obs span 承载；StepRef 属上位 Phase 2 |
| 子智能体委派的 Execution（`parent_execution_id` 使用） | 属多智能体协作体系 Phase 1；本切片只索引主智能体轮次 |
| 上位 §8.1 的 `/api/v1/enterprise-tasks` 系列 API | 属 Phase 4；本切片只加一个平台管理员只读统计端点 |
| outbox 发布器/消费者 | 本切片只写不读；消费者（协作体系/Event Relay）后续自带 |
| Task 完成裁决、负责人、SLA、Task 工作台 UI | 属 Phase 4 |
| `DEVICE_OWNED` 会话/设备绑定 | 属 Phase 3；本切片 `environment_binding_hash` 为常量 |
| 定时任务触发创建 Execution | scheduler 接入属上位 Phase 2（但 tenant_id 安全债本切片先修） |

### 1.3 验收总门禁（全部满足才算完成）

1. 现有全量回归通过：agent 单测、渠道（wecom/dingtalk/feishu/wecom_kf）、billing、scheduler 相邻测试零失败。
2. 开关关闭时零行为差异：`task_plane.enabled=false` 时不新增任何 DB 写入、日志只有启动期一行注册信息。
3. 开关打开时双写可见：跑一轮 Web 对话 + 一轮渠道对话，`agent_executions`/`enterprise_tasks`/`chat_records.execution_id` 均有值且租户正确。
4. 降级安全：人为断开 PG，对话仍正常完成，出现 `task_plane.degraded` 结构化告警日志，无未捕获异常。
5. 所有新表读写 SQL 均带 `tenant_id` 条件，跨租户访问测试拒绝。
6. `tests/unit/test_agent_structure_guard.py` 通过（agent.py 行数 ≤ 4284）。

---

## 2. 对象模型与映射决策（无歧义版）

### 2.1 核心映射规则

| 现实体 | 映射到 | 规则 |
|---|---|---|
| `chat_sessions.session_id`（Web） | 一个 Task + 一条 `task_session_links(session_type='WEB')` | **懒创建**：该会话首次处理消息时创建，此后复用 |
| `channel_sessions`（企微/钉钉/飞书/wecom_kf/wecom_rpa） | 一个 Task + 一条 link(`session_type='CHANNEL'`) | 同上懒创建；`source_type` 取渠道名 |
| scheduler 的 `cron_{user_id}` 会话 | 走 Web 映射（它最终也是 `chat_sessions` 行） | trigger 仍标记为用户消息（scheduler Phase 2 才区分） |
| 一次 `Agent.process_message` 调用（= 一个用户轮次） | 一个 `agent_executions` 行 | `trigger_type='user_message'`；从进入 `process_message` 到终态事件为止 |
| 一次 `SessionRecordService`（= 一条 `chat_records`） | 关联本轮 Execution | `chat_records.task_id/execution_id` 回填（§6.4） |

**不做的事**：不试图把"一个企业任务"从闲聊中识别出来（上位 §13 的噪音风险）。Phase 1 是影子索引、无用户可见 Task 列表，一个会话一个 Task 是确定性规则，零启发式。业务语义的 Task 提升（多会话挂同一 Task）留给 Phase 4 的人工/显式操作。

### 2.2 Task 状态映射（简化版状态机）

```text
创建时 → RUNNING                     # 首个 Execution 开始
每个 Execution 终态后 → WAITING_INPUT # 语义：等待用户下一轮输入
WAITING_INPUT → RUNNING              # 下一轮 Execution 开始
```

Phase 1 内 Task **永不**进入 COMPLETED/FAILED/CANCELLED（无完成定义，不自动裁决——上位 §13 第 3 条）。`version` 字段每次状态变更 +1（CAS 预留，Phase 1 无并发写竞争：同一 Task 的写只来自同会话串行队列 `session_queue`，天然单写者）。

### 2.3 Execution 状态映射

| process_message 事件 | Execution 动作 |
|---|---|
| （recorder 构造，进入轮次） | `CREATED → RUNNING`（一条 UPDATE 内完成），`started_at=NOW()` |
| `complete` | → `SUCCEEDED`，`finished_at`，`result_summary`=最终 response 前 200 字符 |
| `error` | → `FAILED`，`failure_code`=事件内错误摘要前 100 字符（无结构化错误码，Phase 1 接受） |
| `cancelled` | → `CANCELLED`，`finished_at` |
| 进程崩溃/无终态事件 | 对账任务 2 小时后置 `STATUS_UNKNOWN`，`failure_code='worker_orphaned'`（§8） |

### 2.4 ID 规则（沿用仓库前缀惯例，`src/task_plane/ids.py` 单一生成点）

```python
task_id      = f"task_{uuid.uuid4().hex[:12]}"
link_id      = f"tlink_{uuid.uuid4().hex[:12]}"
execution_id = f"exec_{uuid.uuid4().hex[:12]}"     # 与 subagents/executor.py 的 exec_ 前缀同族，长度不同不会碰撞
release_id   = f"rel_{uuid.uuid4().hex[:12]}"
event_id     = f"evt_{uuid.uuid4().hex[:16]}"      # 与 obs trace 的 tr_ 同风格
```

`idempotency_key`（Execution）：`f"{session_type}:{native_session_id}:{started_at_ms}"`——同会话同毫秒重入视为同一轮（幂等冲突时返回已存在行，不新建）；用户重发消息是新一轮、新 Execution（符合上位「显式重试新建 execution」）。

---

## 3. 总体架构：事件驱动录制器（零侵入旁路）

### 3.1 挂接方式——照抄 TraceCollector 方案 C

`TaskPlaneRecorder` 与 `TraceCollector` 同构：挂在 `Agent.process_message` 内部（agent.py:2151-2211 区域），消费同一份事件流。**所有入口**（Web `/api/chat`、`/api/chat/stream`、五条渠道 `process_and_persist`、scheduler `executor.py`、Desktop D1 `turn.py`、CLI、Gradio）最终都经过 `process_message`，因此一处挂接全量覆盖，渠道/Web/main.py 调用方零改造。

```text
process_message(user_input, session_id, user, ...)
  ├─ 构造 TraceCollector（现有，:2151-2177）
  ├─ 构造 TaskPlaneRecorder（新增，紧跟其后，~12 行，见 §6.2）
  └─ async for event in self._process_message_impl(...):
       ├─ trace_collector.on_event(event)   # 现有
       ├─ task_plane_recorder.on_event(event)  # 新增，同一 try/except 内
       └─ yield event
     finally: on_error / on_complete
```

### 3.2 数据流与失败语义

```text
事件流 ──> TaskPlaneRecorder（内存态：execution 行 + 待写事件）
              │ on_complete / on_error 时一次性投递
              ▼
        TaskPlanePersistQueue（后台单线程，照抄 trace_persist.py:30-52 模式）
              │ 同一事务内写 agent_executions + enterprise_tasks 状态 + outbox
              ▼
        PostgreSQL（deploy/init-postgres.sql 新表）
```

**降级规则（红线）**：Recorder、队列、DB 任何一环失败，只记 `logger.warning("task_plane.degraded", extra={...})` 并继续主对话流程；**禁止**向上抛异常、禁止重试阻塞、禁止在主流程同步等待 DB。与上位 §11 Phase 1「任一新链路失败时现有聊天仍可运行，明确记录统一索引降级」一致。

**写入时机权衡**（已定，不再议）：Execution 行不在轮次开始时同步落库，而是随 on_complete 批量异步落库。代价是进程崩溃丢当轮索引（对账任务无法看到，因为行不存在）——这是**可接受的**：影子索引丢一行不影响业务，比每轮两次同步 DB 往返（热路径 +15ms×2）更重要。崩溃丢行率预期 <0.1%，用 stats 端点的 `executions vs chat_records` 差值监控。

---

## 4. 数据模型与 DDL（最终版）

### 4.1 新增 6 张表

规范依据 `.claude/rules/database_dev.md`：**必须同时改 `deploy/init-postgres.sql` 与 `deploy/db_update.sql`**；db_update 全语句幂等（该文件按 file_hash 整体重跑，见 `src/db/database.py:617-900` 机制）；禁外键、禁触发器；tenant_id 前导索引。

```sql
-- ============ 2026-08-19 Task Plane Phase 0+1：企业任务执行操作系统最小切片 ============
-- 设计文档：docs/system/enterprise-agent-platform/task-plane-phase0-1-landing-design.md §4
-- 旁路索引表：不改现有读写链路，全部幂等。

-- 4.1.1 企业任务（影子索引，一个原生会话一个 Task）
CREATE TABLE IF NOT EXISTS enterprise_tasks (
    task_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL,                        -- 首条用户消息前 80 字符
    objective TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,                       -- running / waiting_input（Phase 1 仅此两态）
    priority TEXT NOT NULL DEFAULT 'normal',
    requester_user_id TEXT,
    owner_user_id TEXT,
    source_type TEXT NOT NULL,                  -- web / wecom / dingtalk / feishu / wecom_kf / wecom_rpa / cli / desktop
    source_ref JSONB NOT NULL DEFAULT '{}'::jsonb,
    current_execution_id TEXT,
    execution_count INTEGER NOT NULL DEFAULT 0,
    block_reason_code TEXT,
    version BIGINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_enterprise_tasks_tenant ON enterprise_tasks(tenant_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_enterprise_tasks_requester ON enterprise_tasks(tenant_id, requester_user_id);

-- 4.1.2 会话关联（原生会话 -> Task 的唯一映射）
CREATE TABLE IF NOT EXISTS task_session_links (
    link_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_type TEXT NOT NULL,                 -- WEB / CHANNEL（DEVICE 属 Phase 3）
    native_session_id TEXT NOT NULL,            -- chat_sessions.session_id 或渠道会话标识
    ownership TEXT NOT NULL DEFAULT 'CLOUD_OWNED',
    source_type TEXT NOT NULL,
    linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, session_type, native_session_id)
);
CREATE INDEX IF NOT EXISTS idx_task_session_links_task ON task_session_links(tenant_id, task_id);

-- 4.1.3 执行账本（一次 process_message = 一行）
CREATE TABLE IF NOT EXISTS agent_executions (
    execution_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_link_id TEXT NOT NULL,
    agent_release_id TEXT,                      -- 关联 agent_release_snapshots
    parent_execution_id TEXT,                   -- Phase 1 恒 NULL，协作体系 Phase 1 启用
    trigger_type TEXT NOT NULL DEFAULT 'user_message',
    trigger_ref TEXT,
    coordinator_type TEXT NOT NULL DEFAULT 'cloud_agent',
    coordinator_node_id TEXT NOT NULL DEFAULT 'server',
    environment_binding_hash TEXT NOT NULL,     -- Phase 1 恒为 sha256('coordinator=cloud_agent|node=server')
    status TEXT NOT NULL,                       -- running / succeeded / failed / cancelled / status_unknown
    attempt_no INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT NOT NULL,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    result_summary TEXT,
    failure_code TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, execution_id),
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_executions_task ON agent_executions(tenant_id, task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_executions_status ON agent_executions(tenant_id, status, updated_at DESC);

-- 4.1.4 AgentRelease 最小快照（数字员工定义冻结，协作体系 Phase 1 的前置）
CREATE TABLE IF NOT EXISTS agent_release_snapshots (
    release_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    agent_key TEXT NOT NULL,                    -- subagent_id 或 'master'
    definition_hash TEXT NOT NULL,              -- sha256(prompt_text + tools_json + model_code)
    prompt_version TEXT,                        -- 有 prompt 生命周期版本则填，否则 NULL
    tools_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_code TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (tenant_id, agent_key, definition_hash)
);

-- 4.1.5 策略影子决策（只记录，绝不影响判定）
CREATE TABLE IF NOT EXISTS policy_shadow_decisions (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT,                             -- 平台管理员无租户时为 NULL
    user_id TEXT,
    check_point TEXT NOT NULL,                  -- agent_access / credit_balance
    agent_id TEXT,
    decision TEXT NOT NULL,                     -- allow / deny
    reason_code TEXT NOT NULL,                  -- legacy_check_agent_access / tenant_admin / user_granted / NO_CREDIT / no_tenant / ok
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_policy_shadow_tenant_time ON policy_shadow_decisions(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_policy_shadow_point ON policy_shadow_decisions(check_point, decision, created_at DESC);

-- 4.1.6 事件 outbox（只写不读，消费者属协作体系/后续 Phase）
CREATE TABLE IF NOT EXISTS task_plane_outbox (
    id BIGSERIAL PRIMARY KEY,                   -- 全局单调插入序（非聚合序，见 §9 偏差 4）
    event_id TEXT UNIQUE NOT NULL,
    tenant_id TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,               -- execution / task
    aggregate_id TEXT NOT NULL,
    aggregate_seq INTEGER NOT NULL,             -- 单 Execution 内 0,1,2...（recorder 内存维护）
    event_type TEXT NOT NULL,                   -- execution.created / execution.status_changed / task.created / task.status_changed
    schema_version TEXT NOT NULL DEFAULT '1.0',
    payload JSONB NOT NULL,
    trace_id TEXT,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_plane_outbox_agg ON task_plane_outbox(tenant_id, aggregate_type, aggregate_id, id);
```

### 4.2 现有表增量（同一 db_update 块内）

```sql
-- chat_records 关联 Execution（§7.3 兼容映射），旧数据保持 NULL
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS task_id TEXT;
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS execution_id TEXT;
CREATE INDEX IF NOT EXISTS idx_chat_records_execution ON chat_records(execution_id);
```

### 4.3 安全债 DDL（P0，见 §10）

```sql
-- scheduled_tasks / scheduled_task_logs 补租户（''=遗留未解析，fail-closed：租户视图不可见）
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
UPDATE scheduled_tasks st SET tenant_id = u.tenant_id
  FROM users u WHERE st.user_id = u.user_id AND u.tenant_id IS NOT NULL AND st.tenant_id = '';
ALTER TABLE scheduled_task_logs ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';
UPDATE scheduled_task_logs sl SET tenant_id = st.tenant_id
  FROM scheduled_tasks st WHERE sl.task_id = st.task_id AND sl.tenant_id = '';
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_tenant ON scheduled_tasks(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_logs_tenant ON scheduled_task_logs(tenant_id, created_at DESC);
```

回填依据：`users.user_id` 为全局唯一 `user_` 前缀 ID（`src/db/models.py:38-50`），跨租户无碰撞。回填后仍为 `''` 的行属用户已删除的遗留数据，租户视图查不到（fail-closed），平台管理员可经 SQL 处理。

---

## 5. 模块落点（新增代码全部在 `src/task_plane/`，不进 agent.py）

```
src/task_plane/
  __init__.py       # 空或仅版本号
  ids.py            # §2.4 的 5 个 ID 生成函数 + ENV_BINDING_HASH 常量
  contracts.py      # SessionRef / ExecutionEnvelope / EventEnvelope dataclass + to_dict（单一事实源）
  repository.py     # 全部 SQL。psycopg2 同步 + 参数化；异步端点经 asyncio.to_thread 调用
                    # （照抄 src/local_tools/repository.py:1-5 模块约定：同步、必带 tenant_id）
  recorder.py       # TaskPlaneRecorder（§7）
  policy_shadow.py  # record_shadow_decision(...)（§8）
  persist.py        # TaskPlanePersistQueue：后台单线程写库（照抄 src/core/trace_persist.py:30-52）
  reconcile.py      # job_task_plane_reconcile 回调（§9）
  api.py            # GET /api/saas/task-plane/stats（§11）
contracts/task-plane/
  event-envelope.schema.json    # canonical JSON Schema（与 contracts.py 对齐，测试校验）
  execution-envelope.schema.json
  README.md                     # 契约变更规则：改字段必须升 schema_version 并同步双端
```

### 5.1 repository.py 函数签名（定稿）

```python
# 全部同步 psycopg2；除 stats_snapshot 外全部必带 tenant_id 参数；失败抛 TaskPlaneError（由调用方降级）
def ensure_task_and_link(tenant_id: str, session_type: str, native_session_id: str,
                         source_type: str, requester_user_id: Optional[str], title: str) -> Tuple[str, str]:
    """懒创建 Task + link。INSERT ... ON CONFLICT (tenant_id, session_type, native_session_id)
    DO NOTHING 后 SELECT 返回已有 (task_id, link_id)。Task 首建状态 running。"""

def upsert_agent_release(tenant_id: str, agent_key: str, definition_hash: str,
                         prompt_version: Optional[str], tools_json: list, model_code: Optional[str]) -> str:
    """ON CONFLICT (tenant_id, agent_key, definition_hash) DO NOTHING → SELECT release_id。"""

def finish_execution(tenant_id: str, execution_row: dict, task_row: dict, events: list) -> None:
    """一个事务内：INSERT agent_executions（终态）+ UPSERT enterprise_tasks 状态
    （execution_count+1、current_execution_id、version+1、updated_at）+ INSERT outbox events。
    idempotency_key 冲突（ON CONFLICT DO NOTHING 且已存在行）→ 跳过整批，记 debug 日志。"""

def record_policy_shadow(decision_row: dict) -> None: ...

def orphan_reconcile(older_than_hours: int = 2) -> int:
    """UPDATE agent_executions SET status='status_unknown', failure_code='worker_orphaned',
    finished_at=NOW(), updated_at=NOW() WHERE status='running' AND updated_at < NOW()-interval。返回行数。"""

def stats_snapshot() -> dict:
    """今日计数：tasks / executions(按状态) / shadow_decisions(按 decision) /
    degraded 无 DB 记录（进程内存计数器，经 persist 模块 get_counters() 合并返回）。"""
```

### 5.2 recorder / policy_shadow 接口

```python
class TaskPlaneRecorder:
    def __init__(self, tenant_id: Optional[str], user_id: Optional[str], session_type: str,
                 native_session_id: str, source_type: str, agent_key: str,
                 record_service=None, trace_id: Optional[str] = None,
                 prompt_text: str = "", tools_json: list = None, model_code: Optional[str] = None): ...
    def on_event(self, event: dict) -> None:   # 吞一切异常（降级规则 §3.2）
    def on_error(self, error) -> None:          # → FAILED
    def on_complete(self) -> None:              # → 终态落库（经 persist 队列）

def record_shadow_decision(check_point: str, decision: str, reason_code: str,
                           tenant_id: Optional[str], user_id: Optional[str],
                           agent_id: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """供 main.py 两处检查点调用；内部判 settings.task_plane.policy_shadow 开关 + 入队。吞异常。"""
```

---

## 6. 接入点改造清单（精确到函数）

### 6.1 session 类型判定（recorder 构造前）

在 agent.py 挂接处（process_message 内，trace 构造之后）判定：

```python
session_type, source_type = _resolve_session_ref(record)   # 新增私有 helper，≤15 行
# 规则：record_service.source_type 在 ('chat','wecom','dingtalk','feishu','wecom_kf','wecom_rpa') 中
#   -> 'chat' → session_type='WEB'，其余 → 'CHANNEL'
# native_session_id：WEB 取 process_message 的 session_id 参数；CHANNEL 取渠道会话标识
#   （从 record_service 属性读；拿不到则降级用 session_id 参数并在 source_ref 记 actual）
```

拿不到 `source_type`（如 CLI/Gradio 无 record_service）→ `session_type='WEB'`、`source_type='cli'`。**拿不到 tenant_id → 不录制**（记 degraded 日志，跳过——与上位「tenant 缺失不入库」一致；`src/saas/context.get_current_tenant_id()` 与 `agent._init_tenant_id` 双源尝试）。

### 6.2 agent.py 改动（唯一改动点，合计 ≤20 行，P1 允许的"接线行"）

位置：`process_message`（agent.py:2122-2214）内 trace_collector 构造块（:2151-2177）之后：

```python
task_plane_recorder = None
if _task_plane_enabled():
    task_plane_recorder = TaskPlaneRecorder(...)   # §6.1 参数
# 事件循环内（:2181-2198，trace_collector.on_event 同一 try/except 后追加）：
    task_plane_recorder.on_event(event)
# finally 块（:2206-2211 区域）：
    task_plane_recorder and task_plane_recorder.on_complete()
# 异常分支（:2199-2205 区域）：
    task_plane_recorder and task_plane_recorder.on_error(err)
```

**不动** `_process_message_impl`、工具循环、`process_message_sync`、渠道层。agent_release 快照所需的 `prompt_text/tools_json/model_code` 从 `self`（subagent_config、tool_registry 名称清单、当前模型码）在构造 recorder 时一次性读取。

### 6.3 main.py 改动（policy shadow，4 处调用点）

| 位置 | 检查点 | 接线 |
|---|---|---|
| main.py:824-832（`/api/chat`） | `check_agent_access` | 判定后调 `record_shadow_decision('agent_access', 'allow'/'deny', 'legacy_check_agent_access', tenant_id, user_id, subagent_id)` |
| main.py:834-838（`/api/chat`） | `_check_tenant_credit_blocked` | 返回 403 → `('credit_balance','deny','NO_CREDIT')`；None → `('credit_balance','allow','ok')` |
| main.py:1191-1200（`/api/chat/stream`） | 同上两行接线 | 同 |
| main.py:1208-1211（`/api/chat/stream`） | 同 | 同 |

reason_code 细分：`check_agent_access` 内部四分支（平台管理员 `platform_admin` / 无租户 `no_tenant` / 租户管理员 `tenant_admin` / 用户授权 `user_granted`）——实现方式：不重构 checker（P5 禁止顺手改行为），在 main.py 按 `is_platform_admin(user)/is_tenant_admin(user)` 先判一次得出 reason_code，deny 且非管理员时记 `user_granted`。精确度足够，零 checker 改动。

### 6.4 session_record.py 改动（2 处）

1. `save()`（src/services/session_record.py:320-471）的 INSERT（:428-451）：列清单加 `task_id, execution_id`，值取 `getattr(self, "task_plane_ref", None) or (None, None)`。recorder 构造后立即 `record_service.task_plane_ref = {"task_id":..., "execution_id":...}`（同 trace_collector 回注模式 agent.py:2174）。
2. `save()` 尾部（credit_cost 已算出、`self.trace_collector` 存在时）：best-effort `UPDATE obs_traces SET total_cost=%s WHERE trace_id=%s`（try/except 吞异常）——闭环 §10.3 的成本字段。

### 6.5 scheduler/manager.py 改动（1 处）

`_register_system_jobs()`（:84-256）末尾照抄 `job_system_skill_ws_cleanup` 模式（:242-256）注册：

```python
self._scheduler.add_job(
    self._run_task_plane_reconcile,               # 实现：asyncio 无关，直接调 reconcile.orphan_reconcile()
    CronTrigger(hour=3, minute=40, timezone="Asia/Shanghai"),
    id="job_task_plane_reconcile", name="Task Plane Orphan Reconcile",
    max_instances=1, coalesce=True,
)
```

### 6.6 配置（configs/config.yaml + src/config/settings.py）

照抄 DesktopAgentConfig 模式（settings.py:377-383 + :547-549 + config.yaml:297-301）：

```python
class TaskPlaneConfig(BaseModel):
    enabled: bool = False            # env TASK_PLANE_ENABLED，yaml: ${TASK_PLANE_ENABLED:-false}
    policy_shadow: bool = False      # env TASK_PLANE_POLICY_SHADOW，yaml 同款
```

yaml 新增顶层节 `task_plane:`（置于 `desktop_agent:` 之后）。两个开关独立：可以先开 policy_shadow 积累决策数据，再开双写。

---

## 7. TaskPlaneRecorder 内部规格

### 7.1 事件映射表（on_event 的完整行为）

| 事件 type | 动作 |
|---|---|
| `tool_start` | `tool_call_count += 1`（仅计数，不落行） |
| `tool_result` / `llm_call` / `thinking` / `progress` / `images` / 其他 | 忽略 |
| `response` | 记录最后一条 response 文本（截 200 字符，作 result_summary） |
| `complete` | 置终态 SUCCEEDED，触发落库 |
| `error` | 置终态 FAILED（failure_code=事件 error 字段前 100 字符），触发落库 |
| `cancelled` | 置终态 CANCELLED，触发落库 |
| `clarification` | 忽略（Execution 保持 running；澄清回复是新一轮 Execution——与现有 pending clarification 语义一致） |

### 7.2 outbox 事件生成（on_complete 时一次性生成）

| event_type | payload |
|---|---|
| `task.created`（仅首建轮） | `{title, source_type, requester_user_id}` |
| `execution.created` | `{trigger_type, idempotency_key, agent_release_id, started_at}` |
| `execution.status_changed` | `{from:'running', to:<终态>, failure_code, result_summary}` |
| `task.status_changed` | `{from, to:'waiting_input', execution_count}` |

信封字段：`event_id / schema_version='1.0' / tenant_id / task_id / execution_id / aggregate_seq（内存 0..n）/ occurred_at / actor={type:'agent',id:agent_key} / trace_id`——与 `contracts/task-plane/event-envelope.schema.json` 一致，`aggregate_seq` 为单 Execution 内单调序（偏差声明见 §12.4）。

### 7.3 落库流程（persist 队列 worker）

```python
# persist.py：线程安全 Queue + daemon 线程，启动即注册（照抄 trace_persist.py:30-52）
# on_complete 投递 (execution_row, task_row, events, release_row) 元组：
#   worker: ensure_task_and_link 已在构造时同步做？
```

**修正（定稿）**：`ensure_task_and_link` 与 `upsert_agent_release` 在 **recorder 构造时同步执行**（两次幂等 UPSERT，各 <5ms，热路径可接受——它们必须同步，因为 `record_service.task_plane_ref` 要在 save() 前可用）。终态与 outbox 才走异步队列。即：

```text
构造时（同步，≤10ms）：ensure_task_and_link + upsert_agent_release + task_plane_ref 回注
on_complete（异步队列）：finish_execution（终态 + task 状态 + outbox，单事务）
```

---

## 8. Policy Shadow 规格

- 数据见 §4.1.5 表；写入经 persist 队列（策略检查在 API 层、在 process_message 之前，与 recorder 无耦合，共用同一队列线程）。
- **只记录不拦截**：决策结果仍由现有代码路径返回，shadow 调用永远在判定之后、且 try/except 包裹。
- 渠道链路（channel_routes.py）当前**没有**数字员工授权检查——Phase 1 不补检查（那是行为变更，违反影子原则），只在设计上登记该盲区：shadow 数据只代表 Web 两个入口的决策分布。
- 观测口径：`stats` 端点按 `check_point × decision` 出当日计数；上线一周后人工 review deny 率，作为 Policy Engine P0 的输入。

---

## 9. 对账任务

`job_task_plane_reconcile`（每日 03:40）：

1. `orphan_reconcile(older_than_hours=2)`：`agent_executions.status='running' AND updated_at < NOW()-2h` → `status_unknown` + `worker_orphaned`。语义依据上位 §10：无法确认副作用是否发生，保留未知态，禁止自动改判 succeeded/failed。
2. 顺带输出计数日志（当日 executions、orphan 数、（进程内）degraded 计数）供运维 grep。

注意：由于 §3.2 的写入时机（终态才落库），崩溃丢行**不会**留下 running 残留——orphan 只覆盖「落库成功但终态 UPDATE 丢失」的窄窗口。两机制互补，均已定稿。

---

## 10. Phase 0 安全债修复（三项，可独立先行发布）

### 10.1 scheduled_tasks 租户隔离

DDL 见 §4.3。代码改动（全部 fail-closed，不改变现有用户可见行为的前提 = 回填覆盖全部现存行）：

| 文件 | 改动 |
|---|---|
| `src/scheduler/db.py` | `list_by_user` 等查询加 `AND tenant_id = %s`（参数从 `get_current_tenant_id()` 取；为空 → 返回空列表，不再返回全量） |
| `src/api/scheduled_task.py` | 详情/更新/删除校验（:44-64、:112 附近）加 `task["tenant_id"] != tenant_id → 404`；创建时写入 `tenant_id` |
| `src/scheduler/executor.py` | 创建 `cron_{user_id}` 会话与执行时携带租户上下文（`set_tenant_context` 或显式传参，跟现有 user 对象的 tenant_id） |

### 10.2 知识库对象级租户条件

| 位置 | 改动 |
|---|---|
| `src/knowledge/service.py:481-504`（delete_document） | 三条 SQL 全部加 `AND tenant_id = %s`；函数签名加 `tenant_id: Optional[str]` 参数，为 None 时抛 403 |
| `src/knowledge/api.py:414-423`（DELETE 路由） | 传 `tenant_id = get_current_tenant_id()`，None → 403 |
| `src/knowledge/service.py:614-629`（get_document_chunks） | SQL 改 `JOIN documents d ON d.id = c.doc_id` 带 `AND d.tenant_id = %s`；签名同上加参 |
| `src/knowledge/api.py:438-443` | 同上传参 |
| `src/knowledge/api.py:456-489`（download，API 层裸 SQL :466） | SQL 加 `AND tenant_id = %s`；None → 403 |

### 10.3 obs_traces.total_cost 闭环

- `src/core/trace_persist.py:62-72`：INSERT 中硬编码 `0` 改为 `%s` 参数（值取 `trace.total_cost`，`TraceRecord` 增 `total_cost: float = 0` 字段）；`ON CONFLICT DO UPDATE` 子句同步加 `total_cost = EXCLUDED.total_cost`。
- 真实成本回填走 §6.4 第 2 点（save() 时 UPDATE），实现 evaluation 设计要求的「通过稳定关联而非重复计算」。

---

## 11. 统计端点（唯一新增 API）

```python
# src/task_plane/api.py，挂载进 main.py（照抄 admin 路由注册方式），仅 platform_admin
GET /api/saas/task-plane/stats
→ {
  "enabled": bool, "policy_shadow": bool,
  "today": {"tasks": n, "executions": {"running": n, "succeeded": n, "failed": n,
              "cancelled": n, "status_unknown": n},
            "shadow_decisions": {"agent_access": {"allow": n, "deny": n},
                                  "credit_balance": {"allow": n, "deny": n}}},
  "counters": {"degraded_writes": n, "persist_queue_depth": n},   # 进程内存，重启清零
  "coverage": {"chat_records_with_execution_today": n, "chat_records_today": n}  # 差值=丢行监控
}
```

---

## 12. 与上位设计的偏差声明（评审已裁定的 7 项）

1. **无 participants/artifacts/Step 表**——按上位分期，属 Phase 2/4。
2. **Execution 只索引主智能体轮次**——子智能体委派、定时触发、审批恢复的 Execution 分别属协作体系 Phase 1 与上位 Phase 2；`parent_execution_id/trigger_type/retry` 字段已预留。
3. **Task 状态机简化为 running/waiting_input 两态**——无完成裁决，永不自动 COMPLETED（上位 §13.3）。
4. **outbox 的 `id` 是全局 BIGSERIAL 而非聚合序**；聚合内顺序用 `aggregate_seq`（recorder 内存维护，单 Execution 内可靠）。协作体系引入真实消费者时若需严格聚合序，再加聚合序号表，不提前建。
5. **environment_binding_hash 为常量**——Phase 1 全部为 cloud_agent/server 执行；Phase 3 引入 DEVICE 时改为真实绑定哈希。
6. **API 仅 1 个只读统计端点**——上位 §8.1 全套 v1 API 属 Phase 4，且必须等租户条件与权限模型评审后再做。
7. **`agent_release_snapshots` 是快照不是 Registry**——无生命周期/灰度/canary（属 Evaluation 面）；只保证「同一 definition_hash 的执行可归到同一 release_id」。

这些偏差均为「推迟」而非「永久替代」；上位设计原文仍是完整目标态。

---

## 13. 测试计划（文件级清单）

| 文件 | 层 | 覆盖 |
|---|---|---|
| `tests/unit/test_task_plane_recorder.py` | unit | 事件映射表全行（喂合成事件流：complete/error/cancelled/clarification/崩溃无终态）；task_plane_ref 回注；tenant 缺失降级；构造期 DB 异常吞掉 |
| `tests/unit/test_task_plane_repository.py` | unit（mock conn） | SQL 均带 tenant_id（断言 SQL 文本含条件）；幂等冲突路径；orphan_reconcile 的 WHERE 条件 |
| `tests/unit/test_task_plane_policy_shadow.py` | unit | 开关关闭零调用；决策行字段完整；异常吞掉 |
| `tests/unit/test_agent_structure_guard.py` | unit | agent.py 行数 ≤ 4284（P2）；`src/task_plane/` 不 import `src.tools.*` 具体工具类（P6 方向检查） |
| `tests/unit/test_contracts_task_plane.py` | unit | contracts.py to_dict 与 JSON Schema 文件字段一致（不引入 jsonschema 依赖，手工键集合比对）；schema_version 常量 |
| `tests/integration/test_task_plane_dual_write.py` | integration（真实 PG） | 建临时租户 → 模拟两轮对话事件 → 断言 enterprise_tasks/task_session_links/agent_executions/chat_records 关联/outbox 全部落库且租户正确；跨租户读被拒；idempotency_key 重放不重复建行 |
| `tests/integration/test_scheduled_tasks_tenant.py` | integration | 回填后租户过滤生效；'' 遗留行租户视图不可见 |
| `tests/integration/test_knowledge_tenant_guard.py` | integration | 三处对象级操作跨租户 404/403 |
| 既有回归 | — | agent 29+、渠道各套、billing 86、scheduler 相邻——全部保持绿（CI 门禁） |

验收附加（手动/脚本）：断开 PG 跑一轮对话（降级门禁 §1.3.4）；开关关闭对比开启前后的 DB 写入 diff（零差异门禁 §1.3.2）。

---

## 14. 上线与回滚

1. **顺序**：Phase 0-A（安全债）独立先发 → 测试环境开 `TASK_PLANE_POLICY_SHADOW` → 开 `TASK_PLANE_ENABLED` → 观察 stats 端点 3 天（coverage 差值 <1%、degraded=0）→ 生产开双写。
2. **回滚**：关开关即回滚（新表留存无害）；安全债三项不可回滚但属纯收紧（fail-closed），上线前用集成测试证明存量用户可见行为不变。
3. **协作体系衔接**：本切片上线后，协作体系 Phase 0 的必填字段 `task_id/execution_id` 有了真实来源（本文 §2 映射 + §4.2 关联列），AgentRelease 冻结有了 `release_id`——其两个开工前置即告解决。

---

## 15. Kernel 原则交付物对照

| 原则 | 本文交付 |
|---|---|
| P1 冻结增长 | §6.2/6.3/6.4 的改动全部是"接线行"（agent.py ≤20 行、main.py 4 个调用点、session_record 2 处），新逻辑全在 `src/task_plane/` |
| P2 结构守卫 | `tests/unit/test_agent_structure_guard.py`（基线 4284） |
| P4 首批 seam | 工具调用分发抽取独立为 Phase K（见开发计划），不阻塞双写主链；抽取范围 = agent.py 工具循环的通用执行分支（当前 :3325-3530 的 LOCAL_REQUIRED/通用/挂起/tool_result 收口）移入 `src/core/tool_call_dispatcher.py`，特殊分支（scheduled/clarify/use_skill/delegate）留在 agent.py 委托调用；验收 = 行为等价（P5：现有 agent 全量回归绿 + Desktop D1 挂起用例绿） |
