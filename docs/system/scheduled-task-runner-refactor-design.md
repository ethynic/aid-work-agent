# 定时任务重构设计（Runner 化）

> 状态：v5（定稿评审修正），定稿 → 开发
> 前置：[AgentRunner 服务架构设计](agent-application-architecture-design.md)、[统一通知基础设施设计](notification-infrastructure-design.md)、[租户积分计费设计](saas/tenant-credit-billing-design.md)
> 日期：2026-10-28（v5 定稿评审修正）

## 1. 背景与目标

现有定时任务（`src/scheduler/`）是 agent runner 架构落地前的遗留实现：APScheduler 进程内注册 + 30s 签名对账 + Redis 锁，到点后在 background 进程内同步跑 `master_agent.process_message_sync`（无 checkpoint、无租约、无恢复），结果混写每用户共用的 `cron_{user_id}` 会话，计费走 `background_llm` 兜底台账。

**本功能从未对线上用户开放，无任何存量数据与兼容负担**。本次重构整体删除旧实现，重建为：

1. **执行引擎 = AgentRunner 服务**：每次触发向 Runner 提交持久任务，复用中断恢复、防双执行、可观测快照、trace；
2. **计费 = Runner 结算链**：usage receipts → `chat_records(source_type='scheduled')` → 租户积分原子扣减；
3. **任务视图**：独立"任务计划"分组 + 任务执行视图，历次执行的时间、触发原因、状态、结果、用量可追溯；
4. **通知 = 统一通知基础设施**：完成/失败/自动暂停/需要处理接入统一通知，任务可选择具体通知目标。

**v3 关键修正**（相对 v2，二次评审）：

- 非交互要求注入根/子执行运行上下文（prompt augmentation），不宣称仅靠工具过滤保证；
- pending 增加不可变提交快照：幂等键与提交内容同时固定，编辑不影响已认领执行；
- 首次接单/幂等重查/已受理重校验三种鉴权语义分离，接单与暂停/删除在事务边界协调；
- manual 执行不消费 once 额度（仅 scheduled 触发终态收尾 once），状态优先级明确；
- 提交拒绝全部映射为用户可见的任务状态与通知（无"清 pending 记日志"静默路径）；
- 恢复接线路由 dispatcher 提交 resume 控制实现（现有恢复候选要求 resume_control_id），删除固定 30 分钟自动取消；
- scheduled 非提问类等待新增 `task.needs_action` 通知（现有 waiting producer 只认 clarification 表单）；通知目标在事件生成时快照，资源→任务路由统一；
- 任务会话对普通 chat 提交关闭，旧聊天链接路由到任务视图；任务视图读取契约（触发原因/费用/投递状态）明确字段来源；
- 创建限制收敛：删除 prompt 2000 字上限（服从请求容量闸），默认行为约定按任务相关性而非模板。

**v4 修正**（三次评审，局部问题）：

- 提交拒绝映射闭合：409 IDEMPOTENCY_* 与其余未列举的确定性 4xx 同样进入用户可见的暂停+通知，无任何静默路径；
- 恢复驱动幂等化：控制请求用稳定 client_request_id，尝试次数从控制账本持久事实统计（网络未知/待消费不计数，重启不归零）；
- 用户对 scheduled runner 的操作范围明确为矩阵（查询/订阅/取消），reply 与带补充内容的 resume 不对 scheduled 开放；
- `task.needs_action` 收窄到确需用户动作的等待（设备确认/人工辅助/登录），远程结果与子执行等待只更新进度；
- `pending_snapshot` 补入 prompt_augmentations，快照覆盖完整提交意图；
- 注入位置表述修正：prompt_augmentations 经 ContextAssembler 装配为**受信补充 user 消息**（非系统提示词），子执行一致继承；全部事件（terminal/paused/needs_action）参数统一快照 notify_target_id。

**v5 修正**（定稿评审，恢复键算法）：

- 恢复控制键加入**中断回合 attempt 标识**：`sched-resume:{runner_id}:{attempt}:{n}`——修复"恢复成功后再次中断时同键命中已 consumed 旧控制、无法二次恢复"的确定性缺陷；受理前 402/403（不产生 rejected 记录）直接按原因暂停任务并通知，不等待不存在的 rejected 记录。

**明确不做**：渠道场景定时任务（工具仅 web 对话可见）；通用能力评分器/评审 Agent/任务编译系统；信息齐全的创建不增加审批；不做执行失败自动重试（Runner 自身处理基础设施级恢复）；不为"简单"删除持久交接、归属检查、原子删除协调与取消核对等正确性机制。

## 2. 总体架构

```mermaid
flowchart LR
    subgraph 创建（普通对话，交互式）
        A[用户提出定时需求] --> B[智能体结合对话整理自包含任务定义<br/>缺必要信息→对话中询问]
        B --> T[(scheduled_tasks)]
    end
    subgraph background_runner 进程
        D[dispatch.py 到期循环<br/>认领:推进调度+pending快照<br/>提交:幂等键=task:occurrence] -->|RunnerSubmit| RS[Runner 服务]
        R[恢复驱动:为 interrupted 提交<br/>resume 控制,有界尝试] --> RS
    end
    RS -->|终态事务| S[(chat_messages 任务会话)]
    RS -->|终态事务| B2[(chat_records + 积分)]
    RS -->|终态事务尾钩子| H[任务统计回写/once收尾]
    RS -->|终态/停车钩子| N[统一通知<br/>站内信 + 快照目标路由]
    TV[任务计划分组 + 任务执行视图] --> T
    TV -->|runners/消息/事件/投递状态| RS
    M[任务操作:暂停/恢复/改期/编辑/立即执行/取消当前执行/删除] --> T
```

## 3. 任务定义与创建

### 3.1 创建即完备（无试跑门禁）

创建发生在普通智能体对话中（交互式），完备性在创建阶段解决：

1. 用户提出定时需求，智能体**结合当前对话**整理：任务目标、执行时间、必要参数、资料来源、预期输出、必要的异常处理约定（如"无新增数据时输出'今日无新增数据'"）；
2. 缺少影响执行的必要信息时，智能体**在创建对话中向用户询问**；
3. 信息足够后调用创建工具，保存自包含的任务定义。

任务定义要求（写入工具描述，**无关键词规则机械校验**）：

- 不得依赖隐含上下文（"上面那个文件""按刚才说的办"）：稳定说明写入 task_prompt，文件等资料以受权资源引用（file_id）固化到任务 `attachments` 随每次执行提交，实时数据约定执行时获取；
- **与任务相关**的可预见缺数据/空结果场景需有默认行为约定——按相关性判断，不是所有任务的必填模板；
- 任务 prompt 长度服从请求容量限制（Runner 入口字节闸），不设额外产品上限；
- `max_tasks_per_user`（现行产品配置，默认 5）与 once 过期 10 分钟宽限（20261026-1530 修复语义：过期不执行）为**现行产品规则保留**，非 Runner 化新增限制。

"立即执行一次"保留为用户主动选择的操作（§4.5），与创建解耦；创建工具只做 DB 写入，不等待任何 Runner。

### 3.2 智能体上下文继承

- 创建时从工具执行上下文取当前会话的 `profile_id` 与 `instance_id` 固化到任务行；每次提交携带——执行使用该智能体自己的系统提示词、工具集、技能与知识库；
- 继承智能体使任务定义天然写在该智能体的能力域内（**创建视角的相关性，不构成能力保证**：执行失败如实记录与通知）；
- 主智能体会话创建的任务由主智能体执行（prompt 内指示委派）；不支持跨智能体指派（本期范围取舍）；
- 授权链每次提交重查 profile 订阅与用户授权，失效即 PROFILE_FORBIDDEN → 暂停（§4.1）。

### 3.3 通知目标选择

- 任务创建/编辑时可指定具体通知目标：**个人 Web Push 或租户级飞书/企微/钉钉群机器人、邮件目标**。任务行只保存 `notification_target_id` 引用，不复制 webhook/密钥；
- **目标选项读取**：新增符合既有使用范围的非管理员读取接口（返回名称、provider、目标引用等展示信息，不暴露凭据）——供工具选项与前端选择器；
- 默认不选外发目标 = 站内信 + 用户个人偏好渠道；**不默认预选租户群机器人**；
- **目标在事件生成时快照**（写入事件参数，随参数加密存储不可变）：投递展开按快照路由并校验目标当前仍有效（失效记投递异常，不影响执行与结算）；事件生成后修改任务目标不影响已生成事件；
- 群机器人外发只发任务名、状态与**可访问的任务视图入口链接**；完整结果留在任务视图。

## 4. 调度模型：到期认领 + 持久交接

APScheduler 注册/签名对账/Redis 锁删除（系统级 job 保留不动）。用户任务为**数据库真源 + 原子认领 + 待提交快照**的单循环（`dispatch.py`，间隔 `scheduler.poll_seconds` 默认 15s）。

### 4.1 触发、认领与提交

认领为**单短事务**：条件 `SELECT ... FOR UPDATE` 锁行（active + 到期 + 无 pending，DB 时钟校验）→ 从锁定行派生参数 → UPDATE 推进调度、写入本次触发的**不可变提交快照**与稳定标识：

```sql
UPDATE scheduled_tasks
SET next_due_at = <推进值>,
    pending_occurrence_key = <本次触发标识>,   -- scheduled=认领的 due 值；manual=uuid
    pending_trigger = 'scheduled' | 'manual',
    pending_retry_at = now(),
    pending_snapshot = jsonb_build_object(     -- 本次执行不可变的完整提交意图
        'text', task_prompt, 'attachments', attachments,
        'profile_id', profile_id, 'instance_id', instance_id,
        'prompt_augmentations', <非交互指令>)   -- 实际提交内容一并固化，重试原样重建
WHERE task_id = %s
RETURNING *
```

- **occurrence、推进值、快照全部派生自锁定的当前任务行**：occurrence = 锁定行的 next_due_at（本次消费的 due），推进值 = 按锁定行的**当前调度定义**计算（`compute_next_due`，cron 从 now 合并 / interval 锚定相位 / once 为 NULL）——扫描后发生改期的旧候选不可能覆盖新调度，多实例持有过期候选时 occurrence 不可能重用；once 已过执行宽限（10 分钟）则置 `expired` 不认领；

- **幂等键与完整提交意图同时固定**：快照含任务主体字段与本次实际提交的非交互 augmentation，重试原样重建同一请求（请求摘要含 prompt_augmentations，重试时不得重新生成）——彻底避免"同键不同内容 → IDEMPOTENCY_INPUT_MISMATCH 找不回原执行"；用户编辑只影响之后认领的执行；
- **崩溃不丢**：认领与快照同事务；重启后 pending 非空者重新提交；
- **响应丢失不重**：`client_request_id = '{task_id}:{occurrence_key}'`，接单成功但响应丢失时重试同键同内容返回原 runner；
- **pending 变更全部带 occurrence_key 条件**（clear/backoff 均为 `WHERE pending_occurrence_key = %s`），迟到响应不得清掉他人刚写入的新 pending；**提交拒绝触发的自动暂停同样带 occurrence+active 条件**（`pause_for_occurrence`）：迟到的拒绝响应既不得覆盖"用户暂停→恢复"后的新 pending，也不得再发布新的暂停通知；
- **快照在认领事务内从当前任务行固化**：claim_due/submit_manual 以条件 SELECT FOR UPDATE 锁行校验后在同一事务内构建快照——候选读取后、认领前发生的编辑必然生效于本次快照（"编辑只影响之后认领的执行"由行锁串行化保证，调用方不传快照）；
- **提交结果映射**（闭合集合，全部用户可见，无静默路径）：

| 提交结果 | 处理 |
|---|---|
| 202 成功（新建或幂等命中） | 清 pending（带 key 条件） |
| 402 CREDIT_BLOCKED | `paused(credit_blocked)` + `task.paused` 通知 + 清 pending |
| 403 PROFILE_FORBIDDEN / TENANT_* | `paused(profile_forbidden / tenant_unavailable)` + 通知 + 清 pending |
| 422 定义错误（INVALID_REQUEST 等） | `paused(definition_invalid)` + 通知（reason 文案）+ 清 pending |
| 409 IDEMPOTENCY_*（幂等键命中不同内容，提交异常） | `paused(submit_conflict)` + 通知（说明冲突原因与处理建议）+ ERROR 日志 + 清 pending——不得只记后台日志静默丢弃 |
| 其余确定性 4xx（未列举） | `paused(submit_rejected)` + 通知 + ERROR 日志 + 清 pending |
| 5xx / 网络未知（含超时） | **不当作确定未接单**：`pending_retry_at = now()+retry_seconds` 退避重试 |

三个时间概念分离：`next_due_at`（周期依据）、`pending_trigger + pending_occurrence_key`（本次触发原因与标识）、`pending_retry_at`（提交重试时间）。

### 4.2 推进规则（停机合并）

- **cron**：推进值 = now 之后的下一次触发（停机错过合并为一次，按 now 后节奏继续，不逐 tick 追赶）；
- **interval**：推进值 = 原锚点 + k×interval 中大于 now 的最小值（相位不漂移，手动执行不改节奏）；
- **once**：推进值 = NULL；认领时 `now() > run_at + 10min` 直接置 `expired`。

### 4.3 暂停/恢复/删除与接单的事务协调

三种鉴权语义分离（§5.2），生命周期操作与首次接单在数据库事务边界协调：

- **暂停**：单条 `UPDATE status='paused', 清 pending`。已受理的 runner 继续执行到终态（persisted 重校验不要求 active）；
- **首次接单守卫**：Runner 提交事务内对任务行 `SELECT ... FOR UPDATE` 复核 `status='active' AND pending_occurrence_key = <key>`——与暂停/删除/认领在行锁上串行化：暂停先提交则接单被拒；接单先提交则暂停清 pending 属于"已接单后清理"，语义正确；
- **删除 = 硬删除**（任务+会话+消息）：单事务内先锁任务行，再查该会话**非终态 runner**（含已接单未终态），有则 409；无则删除——检查与删除同事务，无"检查时无 runner、删除后才接单"窗口（接单事务持同一行锁，串行化）；
- **恢复**：重算 `next_due_at`（cron 取 now 之后下一次；interval 以 now 为新锚点；once 若 run_at 已过宽限置 expired），不补跑停用期间次数；
- **普通会话删除入口**与任务会话识别（§6）同样受守卫约束。

### 4.4 手动执行（"立即执行一次"）

条件 UPDATE 写入 manual pending 槽（`pending_occurrence_key=uuid, pending_trigger='manual'`，**不触碰 next_due_at**，快照同规则固化），≤ poll 间隔由循环统一提交。是额外的一次真实执行：recurring 不改节奏，**once 不消费 run_at 额度**。

### 4.5 once 消费与状态优先级

- **仅 `trigger='scheduled'` 的 runner 终态消费 once 机会**：终态（completed/failed/cancelled）→ 任务置 `completed`，`last_status` 记录成败；manual 终态只计入统计不改任务状态；
- 执行侧自动暂停（CREDIT_BLOCKED / PROFILE_FORBIDDEN）**仅对 recurring 任务生效**（once 额度已消费，completed 即终态，失败经 last_status 与通知可见）；
- 提交侧拒绝（§4.1 表）对 once 与 recurring 一致：paused + 原因（once 恢复后 run_at 已过则按宽限规则 expired，用户已收到带原因的暂停通知）。

### 4.6 恢复驱动（接通既有恢复入口，幂等且以控制账本为事实源）

既有恢复候选**要求 `resume_control_id` 非空**（`recovery_repository.py` next_candidates），worker 崩溃产生的 plain interrupted 无控制记录，不会被自动恢复——scheduled 任务无人回访，必须由调度侧接通：

- **稳定控制请求键（含中断回合标识）**：`client_request_id = 'sched-resume:{runner_id}:{attempt}:{n}'`，其中：
  - `attempt` = 该 runner **本次中断回合**的执行 attempt 号（`agent_runners.attempt`，每次领取/恢复 +1）——恢复成功后再次中断必是新 attempt，天然区分"本次中断"与"恢复后的下一次中断"，不会与已 consumed 的旧控制撞键；attempt 补入 runner 公开投影（§5.5）；
  - `n` = **本回合**已被拒绝（rejected）的 resume 控制数量，从 `agent_runner_controls` 按 `client_request_id` 前缀 `sched-resume:{runner_id}:{attempt}:` 统计（持久事实，重启不归零）；
  - 同回合网络重试永远同键（控制接口幂等：重复提交返回既有控制），多 dispatcher 并发无副作用，响应丢失重试同键不产生第二个控制；
- **不消耗失败次数**：网络未知/超时（同键重试）；`RUNNER_RESUME_PENDING`（已有受理待消费的控制，等待 worker 领取，不是失败）；
- **受理前确定性拒绝直接按原因暂停**：控制入口在创建记录**之前**做权限/余额校验，402/403 不会产生 rejected 记录——402 → `paused(credit_blocked)`、403 → `paused(profile_forbidden / tenant_unavailable)` + `task.paused` 通知（与 §4.1 提交映射同因同果）；其余未列举确定性拒绝 → `paused(interrupted)` + 通知。不等待不会生成的 rejected 记录，不无限重复提交；
- **消耗次数**：控制被 worker 拒绝（rejected 记录落账）——n 递增产生新键再试；n 达 `recovery_max_attempts`（默认 3）→ 任务 `paused(interrupted)` + `task.paused` 通知（reason="执行中断且自动恢复失败"）——暂停阻止后续 occurrence 在被占用的会话 claim 后无限排队，是任务级状态呈现而非操纵 runner 生命周期；
- **退避**：网络类暂时故障按 `submit_retry_seconds` 退避；确定性结果（暂停或被拒换键）按有界次数推进，不对同一确定性错误机械重试；
- 调度侧自动恢复使用**无补充输入的 resume**（不携带 answer/attachments，见 §5.2）；
- 任务视图提供**"取消当前执行"**操作（用户经网关调 runner cancel 专用端点）：取消按既有取消核对语义收尾（本地操作结果未知时保留等待核实，**不承诺立即释放 claim**），任务视图如实展示该执行状态；
- **删除固定时间自动取消策略**——不以时间取消替代恢复接线，调度器不维护第二套执行生命周期。

### 4.7 时区与生效延迟

时区 `scheduler.timezone`（Asia/Shanghai）；手动执行与恢复驱动 ≤ poll 间隔生效。

## 5. 执行模型：`scheduled` 来源接入 Runner

### 5.1 提交契约

```
RunnerSubmit(
  source = 'scheduled'
  session = {kind: 'web', session_id: 任务专属会话}
  client_request_id = '{task_id}:{occurrence_key}'
  text / attachments / profile_id / instance_id / prompt_augmentations = pending_snapshot（不可变快照，含非交互指令，重试原样重建）
  request_data = {'scheduled': {'task_id', 'trigger'}}
)
```

会话串行：`agent_runner_session_claims` 保证同任务同时只有一个活跃 runner；执行超过调度间隔时后续触发排队。

### 5.2 鉴权：三种语义分离（`_scheduled` 分支）

| 语义 | 条件 | 用途 |
|---|---|---|
| **首次接单**（提交事务内，execute=True） | 任务行存在且 `status='active' AND pending_occurrence_key == key(client_request_id)`（FOR UPDATE 行锁，§4.3） | dispatcher 提交新执行；暂停/删除后不再首次接纳 |
| **幂等重查**（execute=False） | 任务行存在 + 会话/属主匹配（不要求 active） | 同键重试找回原 runner（含暂停后） |
| **已受理重校验**（authorize_persisted，worker 执行前） | 任务行存在 + 租户/用户 active + profile 订阅授权（**不要求任务 active**） | 已受理执行在任务暂停后继续完成 |

- 提交方：background_runner 进程以 scheduler 服务身份（peers sources=`['scheduled']`，token 复用 `AGENT_RUNNER_WEB_SERVICE_TOKEN`）；Principal `actor_kind='system'`；
- **普通 chat 提交对任务会话关闭**：`source='chat'` 提交到带任务标记的会话 → 拒绝（`TASK_SESSION_CHAT_FORBIDDEN`）——任务容器不接收自由聊天，任务操作走任务接口。

**用户与调度侧对 scheduled runner 的操作范围**（控制入口按来源+动作校验，无新审批体系）：

| 主体 | 操作 | scheduled runner |
|---|---|---|
| 用户（Bearer token） | 查询、快照、事件订阅 | ✅ |
| 用户（Bearer token） | **取消当前执行**（任务视图操作，runner cancel 专用端点） | ✅ |
| 用户（Bearer token） | reply、resume（含 answer/attachments 的补充输入）、pause、browser_complete 等控制动作 | ❌ `SCHEDULED_CONTROL_FORBIDDEN`——scheduled 不开放自由文本作答，也不经 resume 携带聊天补充内容（非交互要求，§5.3） |
| scheduler 服务 | resume（**无补充输入**，恢复驱动 §4.6） | ✅ |
| 设备核对/登录等外部条件 | 既有受权入口（verify-local 等本地工具流程） | 不变 |

**任务暂停/恢复与 Runner 恢复是两个操作，入口与语义分离**：任务暂停/恢复 = 任务接口（改任务行状态与调度）；Runner 恢复 = 调度侧自动恢复驱动（resume 控制）。用户不直接对 scheduled runner 发起恢复。

### 5.3 非交互执行模式（根 + 子执行统一）

- **运行上下文注入**：提交携带非交互 prompt_augmentation，**经 ContextAssembler 装配为受信补充 user 消息**（既有机制，渠道 extra_system_prompt 同通道——不是系统提示词）；child_factory 将同一 augmentation 传入子执行上下文装配，根、子执行一致继承。指令内容："本次为定时自动执行，无法向用户提问；缺少信息时按任务定义中约定的取数方式与默认行为自主完成；不得请求用户补充"；
- **提问能力不可见**：ask_user / clarify 对 scheduled 根与子执行在装配层过滤（`interactive_only` 标记，definitions 与 dispatch 校验同一口径）；
- **语义与验收**：任务输出为"等待用户补充才能继续"的内容属于任务定义缺陷的执行表现，**不定义为正常完成**——通过创建完备性（§3.1）与上下文注入压制；验收用例明确"到点执行输出请求补充信息"不通过验收（§14）；
- **非提问类等待保留既有生命周期**：设备验证（verification）、人工辅助（human_assistance）、远程工具（remote_tool）、子执行等待按既有机制推进或停车——能自主解决的自行恢复；需要用户的经 `task.needs_action` 通知（§8.2）触达，用户在设备/任务视图处理；
- 保留设备核对、登录等外部条件处理，与"任务定义缺上下文"区分：前者按既有等待生命周期，后者由注入指令压制的默认行为兜底。

### 5.4 终态钩子

`event_repository.append_in_tx` terminal 分支按 `source=='scheduled'` 懒加载 `src/scheduler/hooks.record_scheduled_run_in_tx(cursor, runner)`（与通知生产者同一挂点，失败整体回滚）：

- `last_run_at / last_status` 与计数回写（manual 与 scheduled 终态都计入）；
- **once 消费**：仅 `trigger='scheduled'` 终态置 `completed`（§4.5）；
- **recurring 自动暂停**：CREDIT_BLOCKED → `paused(credit_blocked)`；PROFILE_FORBIDDEN → `paused(profile_forbidden)`。

### 5.5 零历史与公开投影契约

- `source='scheduled'` 执行不读会话历史：任务定义自包含，历次结果是展示账本；
- `write_history` 对 scheduled 来源把 `request_data.scheduled`（task_id/trigger）投影进消息 metadata；
- **public_runner 对 scheduled 来源增补公开字段**（显式白名单扩展，运行中即可读，不依赖终态消息 metadata）：
  - `scheduled: {task_id, trigger}`（来自 input.request_data，非敏感）——任务视图展示每次执行的触发原因；
  - `result.credit_cost`（结算后补充）——执行费用直接可读（权威仍在 chat_records）；
  - `attempt`（通用补充，执行事实非敏感）——恢复回合标识（§4.6）与任务视图展示。

## 6. 任务视图与存储

**存储复用，前端独立**：底层继续 `chat_sessions/chat_messages`（任务会话带 subagent/instance 绑定与 `metadata.scheduled_task` 标记）+ `agent_runners`（执行事实），不新建执行引擎。

**任务会话统一识别**（单一标记来源 `metadata.scheduled_task`）：

- 会话侧栏两个分组：普通会话 / 任务计划（任务计划来自 `/api/scheduled-tasks`）；任务会话从普通会话列表排除；
- **旧聊天链接直接打开任务会话** → 前端识别标记后路由到任务视图（不出现聊天形态）；
- 普通 chat 提交拒绝（§5.2）；普通会话删除入口拒绝删除任务会话；
- 任务操作全部走任务接口/任务视图操作区。

**任务执行视图**（一个任务一个长期视图）：

- 头部：任务说明、调度规则、状态（pause_reason 映射文案）、下次执行时间；
- 操作区：暂停、恢复、改期、编辑（任务内容/附件/通知目标）、立即执行一次、**取消当前执行**（对非终态 runner 经网关调 runner cancel）、删除（409 引导重试）；
- 执行记录列表：以 agent_runners 为执行事实——`scheduled.trigger`（触发原因，运行中来自公开投影）、accepted_at/finished_at（时间）、status、result.output 或 error_code 映射文案（**执行前失败、空输出同样成记录**）；费用 `result.credit_cost`；
- 当前执行进度：经网关订阅该 runner 事件流（source='scheduled' 用户 token 只读）；
- 通知投递状态：按任务资源查询通知投递（§8.4 轻量查询接口）；
- 无自由聊天输入框；定时执行只更新任务数据，不切换用户当前页面。

**编辑与调度解耦**：仅调度规则字段（schedule_type/cron/interval/run_at）变更才重算 `next_due_at`（从 now 起算）；任务内容/附件/通知目标编辑**不触碰调度**。

## 7. 计费与配额

复用 Runner 既有链路：usage receipts → `chat_records(source_type='scheduled')` → 租户积分原子扣减与缓存失效。准入：提交与执行前 `assert_credit` 双层；提交侧 402 → 暂停 + 通知（§4.1）；执行侧失败 → 终态钩子暂停（recurring）。手动执行同样计费。

## 8. 通知

复用 `src/notifications/` 四表账本、适配器、投递重试与偏好模型。

### 8.1 事件与接收人

| 事件 | 触发 | 接收人 |
|---|---|---|
| `task.completed` / `task.failed` | scheduled runner 终态 | 任务属主 |
| `task.needs_action`（新增） | scheduled runner 停车在**确需用户动作**的等待上：设备确认（verification）、人工辅助/登录（human_assistance）等——按下钻至叶子的实际等待原因判断 | 任务属主 |
| `task.paused` | 提交侧确定性拒绝（§4.1）或恢复尝试耗尽（§4.6） | 任务属主 |

- **不触发 needs_action 的等待**：remote_tool（远程结果等待）、子执行等待（child_wait）等系统自行推进的正常状态**仅更新任务视图进度**，不外发通知——"存在子执行等待"本身不构成人工处理要求，避免后台自主执行被通知噪声打扰；
- scheduled 提问等待不存在（工具过滤 + 上下文注入）；普通聊天的 `task.waiting_input`（提问语义）保持不变；
- **全部事件参数统一快照 `notify_target_id`**（terminal / paused / needs_action 一致）：`task.needs_action` = `{task_name, action_label, task_id, notify_target_id?}`，`task.paused` = `{task_name, reason_label, task_id, notify_target_id?}`——否则完成通知能到所选机器人而暂停/需处理通知退回个人偏好；
- `task.needs_action` 的 action_label 按等待类型给出**要做什么**（"需要在设备上确认操作""需要人工处理登录"等）；event_key 含 runner_id 与本次停车标识，按停车事件幂等（ON CONFLICT 去重）；文案说明动作指引并提供实际可处理入口（设备侧入口/任务视图状态）；
- scheduled 完成/失败/暂停/需要处理全部纳入站内信查询、偏好默认事件集合与全渠道渲染（站内文案、Web Push 模板、外发文本）。

### 8.2 资源契约与目标路由（统一）

- scheduled 相关事件统一资源：terminal 与 needs_action 为 `{'type':'runner_result'|'runner_waiting','id':runner_id}`（沿用既有注册），task.paused 为 `{'type':'scheduled_task','id':task_id}`；
- **目标路由统一经"资源→任务行"解析**：runner_result/runner_waiting 经 `agent_runners.session_id → scheduled_tasks`，scheduled_task 直接命中；命中任务行且事件参数含快照 `notify_target_id` → 向该目标投递外发（校验目标当前有效，失效记投递异常）；无快照目标 → 用户个人偏好路由；站内信恒开；
- 参数中的 `notify_target_id` 在**事件生成时快照**（加密存储、不可变），任务后续改目标不影响已生成事件；
- 外发机器人文案携带任务视图入口链接（复用通知系统链接基准配置）。

### 8.3 目标选项读取

新增非管理员可用的可选目标读取接口：个人 Web Push + 租户级目标（按既有使用范围），返回名称/provider/目标引用，不暴露凭据；供创建工具选项与前端选择器。

### 8.4 投递状态查询

按任务资源（scheduled_task:task_id，及其 runner 资源）查询通知消息与投递状态（state/attempts/异常原因）的轻量接口，供任务视图展示。

## 9. 数据模型

```sql
CREATE TABLE scheduled_tasks (
    id SERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,              -- sched_ 前缀
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    task_prompt TEXT NOT NULL,                 -- 自包含任务定义
    attachments JSONB DEFAULT '[]',            -- 受权资源引用，创建时固化
    profile_id TEXT NOT NULL DEFAULT 'main',
    instance_id TEXT,
    schedule_type TEXT NOT NULL,               -- daily|weekly|monthly|interval|once
    cron_expression TEXT,
    interval_seconds INTEGER,
    run_at TIMESTAMP,                          -- once 专用
    session_id TEXT UNIQUE NOT NULL,           -- 任务专属会话（存储容器）
    notification_target_id TEXT,               -- 可选：notification_targets 引用
    status TEXT NOT NULL DEFAULT 'active',     -- active|paused|completed|expired
    pause_reason TEXT,                         -- credit_blocked|profile_forbidden|tenant_unavailable|definition_invalid|submit_conflict|submit_rejected|interrupted
    next_due_at TIMESTAMP,                     -- 周期依据
    pending_occurrence_key TEXT,               -- 待提交触发标识（稳定）
    pending_trigger TEXT,                      -- scheduled|manual
    pending_retry_at TIMESTAMP,                -- 提交重试时间
    pending_snapshot JSONB,                    -- 本次执行不可变提交快照
    last_run_at TIMESTAMP,
    last_status TEXT,
    total_runs INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_scheduled_tasks_due ON scheduled_tasks(status, next_due_at);
CREATE INDEX idx_scheduled_tasks_pending ON scheduled_tasks(pending_retry_at) WHERE pending_occurrence_key IS NOT NULL;
CREATE INDEX idx_scheduled_tasks_owner ON scheduled_tasks(tenant_id, user_id, status);
```

删除 `scheduled_task_logs`、`scheduled_task_deliveries`。DDL 同步 `deploy/init-postgres.sql` / `db_update.sql`，更新 `database_system_table.md` §8。

## 10. API 与前端

### REST（`/api/scheduled-tasks`）

列表（+session_id/pause_reason/last_status/next_due_at/notification_target_id）、详情、pause（清 pending）、resume（重算 due）、delete（§4.3 事务守卫）、trigger（manual pending 槽）、编辑（任务内容/附件/通知目标不动调度；调度规则变更重算 due）。删除 logs/get_user_logs。读写维持 tenant+user 条件进 SQL。

### 工具（`src/tools/scheduler/scheduled_task_tool.py`）

- 两工具 `chat_only`；
- create：保留产品校验（时间必填不猜测 / once 未来时间 / 每用户上限 / R56 引导）；**无长度上限**（服从请求容量闸）；描述要求自包含 + 按相关性的默认行为约定；profile/instance 继承；可选 `notify_target_id`（经 §8.3 读取接口供选项）；创建即 active + 计算首 due；
- manage：list/pause/resume/cancel 适配新状态机；view 改为任务统计 + 最近执行摘要 + 指引任务视图；支持修改通知目标。

### 前端

任务计划分组 + 任务执行视图（§6）；通知目标选择器（§8.3）；通知点击 → 任务视图；旧聊天链接路由（§6）；`ScheduledTasks.vue` 改造为操作区基础。

## 11. 代码变更清单

**删除**：`src/scheduler/executor.py`、`delivery.py`、`error_sanitizer.py`；`manager.py` 用户任务部分；旧工具/API；两张旧表及渠道补投挂点；旧测试。

**重写/新增**：
- `src/scheduler/db.py`：新表 DAL（claim_due 原子认领+快照、manual pending、occurrence_key 条件化的 clear/backoff、resume/改期重算、删除守卫事务、统计回写）
- `src/scheduler/dispatch.py`：到期循环、推进计算（停机合并）、快照提交（拒绝映射 §4.1）、恢复驱动（resume 控制 + 有界尝试 + paused(interrupted)）
- `src/scheduler/hooks.py`：`record_scheduled_run_in_tx`（统计/once 消费/recurring 暂停）

**Runner 服务（增量）**：contracts source 枚举；authorization `_scheduled` 三语义 + 提交事务内接单守卫（行锁复核 status+pending）；chat 提交任务会话拒绝；零历史；非交互：prompt_augmentations 注入（根+子）+ `interactive_only` 工具过滤；`chat_only` 过滤；write_history 投影；public_runner 增 `scheduled`/`credit_cost`；event_repository terminal 挂 hooks；waiting 停车挂 needs_action 生产；settings peers。

**通知（增量）**：producer scheduled terminal/needs_action 分支；注册 `task.needs_action`（参数/文案/偏好默认集合/全渠道渲染）；参数快照 `notify_target_id`；expand 资源→任务统一路由 + 快照目标投递；目标选项读取接口（非管理员）；投递状态查询接口；外发带任务入口链接。

**其他**：`background_runner.py` 启动循环与恢复驱动；session 删除守卫；config scheduler 节；`database_system_table.md` §8。

## 12. 配置

```yaml
scheduler:
  timezone: "Asia/Shanghai"
  poll_seconds: 15             # 到期扫描/手动执行/恢复驱动生效上限
  max_tasks_per_user: 5        # 现行产品配置保留
  submit_retry_seconds: 60     # Runner 不可达/未知响应的退避间隔
  recovery_max_attempts: 3     # interrupted 恢复控制有界尝试次数
```

## 13. 设计决策记录

| 决策 | 理由 |
|------|------|
| 到期认领 + 原子认领 + 待提交快照 | 幂等键与**完整提交意图**（任务主体 + 非交互 augmentation）同时固定；崩溃不丢、响应丢失不重、编辑不污染已认领执行 |
| 提交拒绝映射为闭合集合 | 409 与未列举 4xx 同样进入暂停+通知（复用 paused 状态与通知，不建失败账本）；"按设计不应发生"不作为静默丢弃依据 |
| 恢复驱动 = resume 控制 + 回合键 + 账本计数 | 恢复候选要求 resume_control_id，plain interrupted 无人回访；键含中断回合 attempt（防"再中断撞已消费旧控制"）+ 回合内 rejected 计数（持久事实，重启不归零）；受理前 402/403 无 rejected 记录，直接按原因暂停；不以时间取消掩盖恢复缺口，不承诺 cancel 立即释放 |
| 控制操作范围矩阵（来源+动作校验） | 控制会改状态不得称"只读"；scheduled 不开放 reply/带补充输入的 resume，非交互要求覆盖所有控制入口；任务暂停/恢复（任务接口）与 Runner 恢复（调度自动）分离 |
| needs_action 只覆盖需用户动作的等待 | 远程结果与子执行等待是自主推进的正常状态，仅更新进度；通知说明要做什么并提供实际入口，避免噪声 |
| 创建对话补全定义，无试跑门禁 | 试跑在创建时产生真实副作用；未来数据会误判；创建对话天然可提问 |
| 非交互 = 上下文注入 + 提问能力不可见 + 默认行为约定 | 三层递进：注入指令引导行为、工具不可见消除结构化提问、创建约定兜底空结果；不宣称单层保证 |
| 接单/暂停/删除事务边界协调（行锁） | 消灭"暂停后首次接单""检查后接单再删除"竞态；三种鉴权语义分离使幂等重查与已受理执行不受暂停影响 |
| manual 不消费 once；仅 scheduled 终态收尾 | 手动是诊断/即时执行；消费规则一句话可解释 |
| needs_action 独立事件 | 现有 waiting 通知只认 clarification 表单（提问语义）；设备核对等待需要"需要处理"语义与文案 |
| 目标生成时快照进参数 | 参数本就加密不可变；避免"事件已生成、展开前改目标"发错群 |
| 任务会话对 chat 关闭 + 链接路由 | 独立视图不能只靠列表排除；旧链接与自由聊天写入都要堵住 |
| 公开投影补 scheduled/credit_cost | 任务视图运行中即需触发原因与费用，不能依赖终态消息 metadata |
| 删除 prompt 2000 上限 | 与"保存完整任务定义"冲突；服从既有请求容量闸 |
| 编辑与调度解耦 | 仅时间规则变更重算 due，避免编辑文案改变 interval 节奏 |
| 保留 max_tasks_per_user / once 宽限 | 现行产品规则（注明依据），非 Runner 化新增 |

## 14. 验收标准与必须验证的失败边界

验收标准（产品级）：

1. 创建时缺必要信息在创建对话补齐；上下文完整即可创建，无强制试跑；
2. 到点执行及子执行不向用户发起提问；**任务输出为"请求补充信息"的用例不通过验收**；无数据等场景按约定输出；
3. 任务位于独立分组，执行视图无聊天输入框；旧聊天链接打开任务会话路由到任务视图；普通 chat 提交被拒；定时执行不切换当前页面；
4. 每次执行的时间、触发原因、状态、结果、计费可追溯（含执行前失败与空输出，运行中可见触发原因）；
5. 所选通知目标收到通知（含任务入口链接），点击进入任务视图；未选择时不外发租户群；事件生成后改目标不影响已生成事件；
6. 认领后崩溃、提交响应丢失、worker 中断均不静默丢执行、不重复计费；编辑任务不改变已认领执行的提交内容；
7. 暂停只停未来调度（已受理执行跑完）；手动执行不改 interval 节奏、不消费 once；删除遵守守卫；
8. interrupted 执行被恢复驱动接续（resume 控制被消费）；无法恢复时任务暂停并通知，用户可取消该执行后恢复任务。

必须验证的失败边界：

1. 认领后提交前崩溃：pending+快照存活，重启重提同键同内容幂等；
2. 接单成功但响应丢失：重试返回原 runner，不重复执行/计费；
3. 提交期间用户编辑任务：已认领执行按快照提交原内容，下次认领用新定义；
4. 暂停与首次接单竞态：行锁串行化——暂停后不出现新接单；已受理执行完成；
5. 删除与接单竞态：同事务行锁 + 非终态 runner 检查——不出现"删除后接单"与"收尾撞已删会话"；
6. 迟到提交响应：occurrence_key 条件化 clear/backoff 不破坏新 pending；
7. 提交确定性拒绝（402/403/422/409 及未列举 4xx）：任务进入对应 paused+reason（credit_blocked/profile_forbidden/tenant_unavailable/definition_invalid/submit_conflict/submit_rejected）并收到通知——含 once 任务，无静默丢执行路径；once 拒绝后恢复 → 按宽限 expired；
8. once 手动执行成功：任务仍 active，run_at 到点正常执行后才 completed；once 正式执行失败：completed + last_status=failed + 通知；
9. worker 崩溃（interrupted）：dispatcher 提交 resume 控制（键含中断回合 attempt 与回合内拒绝数：并发/响应丢失不产生重复控制；RUNNER_RESUME_PENDING 与网络未知不计失败；重启不归零）→ worker 领取续跑；**同一 runner"中断→恢复成功→再次中断"两次均能创建并消费恢复控制（新 attempt 键）**；**resume 受理前被 402/403 拒绝 → 任务按原因暂停+通知，不无限重复提交**；被拒达上限 → 任务 paused(interrupted) + 通知；
10. 需用户动作的等待（设备确认/人工辅助）：按既有生命周期停车 + `task.needs_action` 通知（含动作指引与入口）；远程结果/子执行等待：仅任务视图进度更新，**不产生通知**；恢复后继续；
11. 停机错过多周期：cron 从 now 合并一次；interval 相位不漂移；
12. terminal 钩子、needs_action 生产与通知同事务：任一失败整体回滚；
13. 非交互过滤一致性：提问工具对 scheduled 根/子执行 definitions 与 dispatch 同时不可见；prompt_augmentation 到达根与子执行上下文。
