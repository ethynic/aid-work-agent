# 企业 Agent Evidence Ledger 设计

> 版本：v1.0
>
> 日期：2026-08-12
>
> 状态：架构设计基线，待分阶段实现
>
> 适用范围：Web、Agent Desktop、企业微信、钉钉、飞书、Cloud Agent、Local Agent Coordinator、Server ToolExecutor、`agent-tool-runtime`
>
> 关联设计：[企业 Task 模型](./enterprise-task-model-design.md)、[工作成果记录](../work-outcome-record-design.md)、[社媒发布调度](../digital-employee/publish-dispatcher-design.md)

## 1. 决策摘要

企业 Agent 不能以“工具返回 success”作为业务完成依据。平台需要一套服务器权威、追加式、可校验的 Evidence Ledger，将每个有业务副作用的动作完整表达为：

```text
Intent 快照
  -> Policy 决策
  -> Approval（需要时）
  -> Attempt
  -> Receipt / Observation
  -> Verification
  -> Outcome / Artifact
  -> Compensation（需要时）
```

核心原则：

1. 证据是事实及其来源，不是 Agent 的自然语言结论。
2. 审批必须绑定不可变 Intent hash；输入、目标或版本改变后旧审批自动失效。
3. 每个副作用动作必须有幂等键、前置条件和后置验证；无法确认是否生效时进入 `STATUS_UNKNOWN`，禁止盲重试。
4. Ledger 元数据和完整性链以服务器为权威；Desktop/Runtime 执行事实先写本地 durable journal，再以设备身份签名同步。服务器按来源标注可信度，不把设备自报等同于外部系统回执。
5. `chat_records`、observability trace、`work_outcomes` 和业务表继续保留各自用途，通过 adapter 关联到 Ledger，不直接冒充不可变证据。
6. 首期优先覆盖发布、发送、审批、修改业务数据、花钱、删除和 GUI 自动化等高风险动作；普通只读工具按采样/摘要记录，避免成本失控。

## 2. 目标与非目标

### 2.1 目标

- 回答“谁在何时、基于什么输入、经谁批准、在哪个节点、调用哪个工具版本、对哪个业务对象做了什么、结果如何证明”。
- 为 Web、Desktop、渠道和远端 Runtime 提供同一套动作、审批、幂等、验证和补偿语义。
- 支持租户审计、争议处理、合规导出、业务成果、质量评估和异常恢复。
- 对敏感内容最小化持久化，保留 hash、分类、来源和必要回执；原文按策略加密或仅驻留执行节点。
- 在不破坏现有聊天和工具链的前提下逐步从“观测”升级为“强制门禁”。

### 2.2 非目标

- 不记录或展示 Agent 隐藏思维过程。
- 不把所有日志永久保存，不做区块链，也不承诺单数据库能抵御拥有数据库超级权限的攻击者。
- 不用 Ledger 替代业务系统本身；订单、合同、发布内容等仍以对应业务系统为权威。
- 不把 LLM 生成的“看起来成功”文本当作外部动作回执。
- 不尝试用统一补偿逻辑自动逆转所有不可逆动作。

## 3. 现状审计

### 3.1 已存在的可复用能力

| 能力 | 当前实现 | 评价与复用方式 |
|---|---|---|
| 对话计费记录 | `chat_records` + `SessionRecordService` | 有 tenant、模型、token、积分、时延、状态；作为计费关联，不作为业务证据 |
| Tool 摘要 | `execution_details.tool_executions` | 有参数、结果、成功和耗时；结果截断 2000 字符，且一轮聚合 JSON，不足以做可靠动作账本 |
| Observability Trace | `obs_traces` / `obs_spans`、`TraceCollector` | 有 trace/span 和完整工具输入输出；异步 best-effort、trace 可 UPSERT、包含大量敏感原文，适合诊断而非长期证据 |
| 工作成果 | `work_outcomes` | 已有文件/动作/决策及租户列表；可删除、部分来自次日 LLM 推断、无版本/验证，适合作成果投影 |
| 内容审核 | `social_review_records` | 审批绑定 `variant_revision + content_hash`，是 Intent hash 绑定的正确样板 |
| 发布任务 | `social_publish_jobs` | 有冻结快照、全局唯一幂等键、外部 task ID、retry、lease、`status_unknown` 字段 |
| 发布尝试/结果 | `social_publish_attempts`、`social_published_contents` | 表结构已存在，可作为 Attempt/Receipt adapter；当前 Dispatcher/Executor 尚未落地，不能视为闭环完成 |
| 发布状态机 | `publishing/state_machine.py` | 定义了合法转换；现阶段只存在 `can_transition`，文档也明确强制校验仍是后续 S1 工作 |
| 浏览器执行审计 | `bs_browser_runs`、assistance、resume jobs | 有 tenant、run、执行目标、状态、lease、错误码、人工接管；刻意不存页面正文/凭证，适合作节点执行证据样板 |
| 渠道消息 | channel message_id、撤回字段和事务批量写 | 可作为用户指令来源证据和去重键，不等同于动作完成证据 |
| Desktop 规划 | 本地 event store、Coordinator、Session Relay | 可作为本地动作的 durable journal 和设备 attestation 来源 |

### 3.2 关键缺口

1. 缺少统一 `action_id`。一次工具调用、社媒发布、浏览器 run、合同审批和 Runtime invocation 无法跨系统串联。
2. 没有统一 Effect 分类、资源范围、风险、可逆性、前置条件与后置条件声明。
3. 通用工具审批主要停留在 Prompt/Skill 指引或客户端规划，服务器尚无统一审批对象、职责分离和 Intent hash 失效机制。
4. `chat_records` 的工具结果被截断，`obs_spans` 又保存完整敏感内容，两者分别存在证据不足和过度采集问题。
5. Trace 异步队列只在进程内，进程退出可能丢记录；UPSERT 也不是追加式审计语义。
6. `work_outcomes` 支持物理删除且可由小模型事后推断；不能证明动作真的发生。
7. 社媒模块已有最成熟的快照/审批/幂等结构，但 `idempotency_key` 当前是表级唯一而非明确 `(tenant, scope, key)`，通用化时必须定义租户和动作范围。
8. 定时任务失败后线程重试、子智能体 Redis 状态和浏览器 PostgreSQL lease 的可靠性不一致。
9. 本地/远端工具结果尚无设备签名、schema/tool digest、策略版本和可信度等级。
10. 缺少证据完整度判定：平台无法区分“仅 Agent 自报”“工具返回”“外部回执”“独立回查确认”。

## 4. 概念模型

### 4.1 Action Intent

Action Intent 是执行前冻结的业务动作：

```json
{
  "action_id": "act_...",
  "effect": "send",
  "tool": {"namespace": "weixin", "name": "send_message", "schema_digest": "sha256:..."},
  "resource": {"type": "contact", "opaque_id": "contact_..."},
  "target_node_id": "device_...",
  "input_snapshot_ref": "blob_...",
  "input_digest": "sha256:...",
  "preconditions": [{"type": "window_focused", "expected": true}],
  "postconditions": [{"type": "message_visible", "expected": true}],
  "risk": {"level": "high", "reversible": false},
  "idempotency_key": "..."
}
```

Intent 中敏感值可存加密 blob 或仅存 digest；审批 UI 通过受控解密/本地展示获得必要预览。

### 4.2 Evidence Entry

Evidence Entry 是追加式事实，最小字段：

- 归属：tenant/task/session/execution/action/attempt。
- 类型：intent、policy、approval、dispatch、receipt、observation、verification、artifact、compensation、attestation。
- 来源：用户、Cloud Agent、Desktop、Runtime、服务器 ToolExecutor、外部系统、审核人。
- 内容：结构化摘要、payload digest、可选加密 blob 引用。
- 完整性：前序 hash、当前 hash、设备签名/服务器签名、时间戳。
- 可信度：来源类型与 verification level，不能由 Agent 自行提高。

### 4.3 Evidence Bundle

Bundle 是面向某一业务主张的证据集合，例如“报价邮件已发送”：

```text
用户指令 -> 发送预览 -> 审批 -> SMTP/API request digest
-> provider message_id -> 独立状态查询/回执 -> 最终报价文件 hash
```

Bundle 不复制 Entry，只保存成员关系、主张、完整度和导出快照。

### 4.4 证据等级

| 等级 | 含义 | 示例 |
|---|---|---|
| L0 `ASSERTED` | Agent/工具自报，未经确认 | RPA 返回“点击成功” |
| L1 `OBSERVED` | 受控执行器观察到确定性后置条件 | 页面出现已发送记录 |
| L2 `RECEIPTED` | 外部系统给出稳定回执 | API object ID、邮件 message ID |
| L3 `VERIFIED` | 独立回查或第二数据源确认 | 用查询 API 查到目标状态和内容 hash |
| L4 `ATTESTED` | 授权人工对冻结快照作确认 | 审核人签署、人工发布确认 |

等级不是线性“越高越真”；Bundle 应同时展示来源。例如人工确认不能替代外部 API 回执，外部回执也不能证明业务内容合规。

## 5. Action 状态机

```text
PROPOSED
  -> POLICY_CHECKED
  -> APPROVAL_REQUIRED -> APPROVED | REJECTED | EXPIRED
  -> READY
  -> EXECUTING
  -> SUBMITTED
  -> VERIFYING
  -> SUCCEEDED | FAILED | STATUS_UNKNOWN

FAILED | STATUS_UNKNOWN
  -> COMPENSATION_REQUIRED
  -> COMPENSATING
  -> COMPENSATED | COMPENSATION_FAILED | MANUAL_REVIEW_REQUIRED
```

关键约束：

- Policy 拒绝是终止，不得让 LLM 改写参数绕过同一规则。
- Approval 只批准冻结 Intent；任何 `input_digest`、resource、tool/schema digest 或目标节点改变都回到 `PROPOSED`。
- `SUBMITTED` 只表示外部系统受理，不能映射为 Task 完成。
- `STATUS_UNKNOWN` 不允许自动重新 dispatch 非幂等动作。
- Compensation 是新的 Action，引用 `compensates_action_id`，不修改原事实。

## 6. Effect、风险与执行要求

统一 Effect 枚举：

| Effect | 示例 | 默认要求 |
|---|---|---|
| `read` | 查询订单、读取文件 | ACL、数据最小化、读取摘要 |
| `create` | 新建草稿、生成文件 | 幂等键、结果对象 ID/hash |
| `update` | 改地址、改客户状态 | 旧值/新值摘要、版本前置条件、回查 |
| `delete` | 删除文件/业务对象 | 明确目标、强审批、tombstone、可恢复性 |
| `send` | 邮件、微信、私信 | 收件人和内容快照、审批、回执、防重 |
| `publish` | 发布文章/视频 | revision/hash 审批、冻结快照、平台回查 |
| `approve` | 合同/OA 审批 | 职责分离、单据版本、审批人确认、外部流水号 |
| `pay` | 充值、广告花费、付款 | 金额/币种/收款方、双人审批、强幂等、对账 |
| `gui_control` | 鼠标键盘/RPA | 固定设备、窗口/账号上下文、确定性后置条件 |

Tool/Provider 必须声明 Effect contract；未知工具默认按高风险处理，不允许只凭名字推断低风险。

## 7. 数据模型建议

### 7.1 动作与尝试

```sql
CREATE TABLE agent_action_intents (
    action_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT,
    session_ref JSONB,
    execution_id TEXT NOT NULL,
    parent_action_id TEXT,
    compensates_action_id TEXT,
    effect TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_ref_hash TEXT NOT NULL,
    tool_namespace TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_schema_digest TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_node_id TEXT,
    input_digest TEXT NOT NULL,
    input_blob_id TEXT,
    risk_snapshot JSONB NOT NULL,
    preconditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    postconditions JSONB NOT NULL DEFAULT '[]'::jsonb,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, idempotency_scope, idempotency_key),
    UNIQUE (tenant_id, action_id)
);

CREATE TABLE agent_action_attempts (
    attempt_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    executor_kind TEXT NOT NULL,
    executor_node_id TEXT,
    executor_installation_id TEXT,
    provider_request_id TEXT,
    provider_object_id TEXT,
    fencing_token TEXT,
    request_digest TEXT NOT NULL,
    response_digest TEXT,
    status TEXT NOT NULL,
    error_category TEXT,
    error_code TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    UNIQUE (tenant_id, action_id, attempt_no)
);
```

### 7.2 审批、验证和补偿

```sql
CREATE TABLE agent_approval_requests (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    approval_type TEXT NOT NULL,
    required_roles JSONB NOT NULL,
    required_count INTEGER NOT NULL DEFAULT 1,
    segregation_rule JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, approval_id)
);

CREATE TABLE agent_approval_decisions (
    decision_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    approval_id TEXT NOT NULL,
    intent_digest TEXT NOT NULL,
    decision TEXT NOT NULL,
    approver_user_id TEXT NOT NULL,
    approver_role_snapshot JSONB NOT NULL,
    comment_blob_id TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, approval_id, approver_user_id)
);

CREATE TABLE agent_action_verifications (
    verification_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    attempt_id TEXT,
    verifier_kind TEXT NOT NULL,
    method TEXT NOT NULL,
    expected_digest TEXT,
    observed_digest TEXT,
    evidence_level TEXT NOT NULL,
    result TEXT NOT NULL,
    observation_blob_id TEXT,
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 7.3 追加式 Ledger

```sql
CREATE TABLE evidence_streams (
    evidence_stream_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    stream_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    last_sequence BIGINT NOT NULL DEFAULT 0,
    last_entry_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, stream_type, subject_id)
);

CREATE TABLE evidence_entries (
    entry_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    evidence_stream_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    entry_type TEXT NOT NULL,
    task_id TEXT,
    execution_id TEXT,
    action_id TEXT,
    attempt_id TEXT,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT,
    payload_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_digest TEXT NOT NULL,
    payload_blob_id TEXT,
    previous_entry_hash TEXT,
    entry_hash TEXT NOT NULL,
    signature_alg TEXT,
    signature TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, evidence_stream_id, sequence),
    UNIQUE (tenant_id, entry_id)
);

CREATE TABLE evidence_blobs (
    blob_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    encryption_key_version TEXT,
    storage_ref_encrypted TEXT,
    media_type TEXT,
    size_bytes BIGINT,
    retention_class TEXT NOT NULL,
    redaction_profile TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    destroyed_at TIMESTAMPTZ
);

CREATE TABLE evidence_bundles (
    bundle_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    task_id TEXT,
    action_id TEXT,
    claim_type TEXT NOT NULL,
    claim_summary TEXT NOT NULL,
    completeness_status TEXT NOT NULL,
    highest_evidence_level TEXT NOT NULL,
    exported_digest TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

另建 `evidence_bundle_entries(tenant_id, bundle_id, entry_id, role)`。Ledger 表禁止业务服务 UPDATE/DELETE；纠错通过新 Entry 指向被纠正 Entry。敏感 blob 到期可 crypto-shred，但保留 digest 和 `destroyed` 事实。

### 7.4 完整性实现

- hash chain 以 `evidence_stream_id` 为单位，避免租户全局串行热点。
- 追加时锁定 stream 行，分配 sequence 并在同事务写 Entry/outbox。
- `entry_hash = H(canonical_json(entry_without_signature) + previous_entry_hash)`。
- Desktop/Runtime 用 installation key 签名设备事件；服务器验证后再追加 server receipt Entry。
- 高风险租户可每日生成 stream roots 清单并写入独立对象存储/WORM；首期不强制外部时间戳服务。

## 8. 幂等、验证与补偿

### 8.1 幂等契约

- key scope 至少包含 tenant、Provider/业务动作和业务对象。
- 同 key + 同 `input_digest`：返回原 Action/结果，不重复执行。
- 同 key + 不同 digest：`409 IDEMPOTENCY_CONFLICT`。
- Executor 每次 dispatch 把同一业务 key 传给支持幂等的外部 API。
- GUI/RPA 无外部幂等能力时，执行前查询目标状态，执行后验证；断线后默认 unknown，而不是重做鼠标动作。

### 8.2 前置与后置验证

前置条件用于阻止陈旧操作，例如订单版本、内容 revision/hash、目标账号、窗口标题和登录身份。后置条件必须是确定性检查：

- API 查询目标字段是否变更。
- 外部对象 ID 是否存在且内容 hash 匹配。
- 页面结构中是否出现目标记录，而不是仅检查“按钮点击过”。
- 文件 hash、大小、格式和存储可读性是否符合要求。

验证失败分为：`NOT_APPLIED`、`APPLIED_DIFFERENTLY`、`INCONCLUSIVE`。只有第一类在策略允许时可安全重试。

### 8.3 补偿

- 可逆动作声明 compensation template，例如恢复旧地址、撤回草稿、删除新建草稿。
- 补偿也要 Policy、Approval、幂等和 Verification，不是异常处理里的随手调用。
- 邮件发送、外部审批、支付、公开发布等通常不可真正逆转，只能执行后续修正动作，并保留原事实。
- 自动补偿仅适用于明确、低风险、验证充分的动作；其余进入人工处置。

## 9. 跨端执行与证据同步

### 9.1 Cloud Agent / Server ToolExecutor

服务器在 dispatch 前原子写 Intent/Policy/Approval 状态，ToolExecutor 领取带 fencing token 的 Attempt；回执和验证结果直接追加 Ledger。

### 9.2 Agent Desktop

Local Coordinator 在动作前向服务器申请策略决策和 action envelope；本地执行过程中：

1. 先把 intent/attempt 写加密本地 journal。
2. 执行固定工具和 schema digest。
3. 本地记录脱敏结果、payload digest、node identity、单调序号和签名。
4. 在线时实时上传；中途断网时保留 journal，重连按序补传。
5. 服务器验证设备绑定、策略版本、Intent digest 和签名后追加 Ledger。

断网发生在不可逆动作之后时，Desktop 必须显示“结果待确认”，不能仅凭本地 UI 观察宣称企业系统已完成。若租户策略要求在线审批/记账，本地 Host 在 dispatch 前失联应 fail-closed。

### 9.3 `agent-tool-runtime`

Runtime 使用同一 action envelope 和本地 journal，但没有会话/Task 权威。它只能为已固定 `target_node_id` 的 Attempt 提供执行证据；服务器 claim 时复核 provider/tool/schema digest、节点策略和租户绑定。

### 9.4 Web 与消息渠道

Web、企业微信、钉钉和飞书主要产生用户指令、补充信息和审批决定证据。渠道消息事件必须保留原 channel message ID、发送者身份映射和撤回状态。渠道“回复了同意”只有在审批协议明确匹配审批 ID、Intent digest 和授权身份时，才能转为 ApprovalDecision。

## 10. API 与事件契约

### 10.1 内部 API

```text
POST /api/v1/actions/intents
POST /api/v1/actions/{action_id}/policy-evaluate
POST /api/v1/actions/{action_id}/approval-request
POST /api/v1/approvals/{approval_id}/decisions
POST /api/v1/actions/{action_id}/attempts/claim
POST /api/v1/actions/{action_id}/attempts/{attempt_id}/events
POST /api/v1/actions/{action_id}/verify
POST /api/v1/actions/{action_id}/compensations
GET  /api/v1/actions/{action_id}
GET  /api/v1/actions/{action_id}/evidence-bundle
POST /api/v1/evidence-bundles/{bundle_id}/export
```

Claim 返回短期 action token，绑定 tenant/action/attempt/node/tool digest/fencing token。结果上传超过大小上限时只接受 digest + blob upload ticket；不得把任意路径或 URL作为证据引用。

### 10.2 事件

关键事件：

- `action.intent_created`
- `action.policy_decided`
- `approval.requested/decided/expired/invalidated`
- `action.attempt_started/submitted/failed`
- `action.status_unknown`
- `action.verification_completed`
- `artifact.version_evidenced`
- `action.compensation_started/completed`
- `evidence.bundle_completed`

事件信封沿用 Task 设计的 tenant/task/session/execution/sequence/trace 字段。事件总线用于集成，Ledger Entry 才是审计事实；消费者不能通过重放普通事件改写 Ledger。

## 11. 权限、多租户与隐私

- Policy Decision Point 在服务器；Desktop、Server ToolExecutor 和 Runtime 都是 Enforcement Point。
- 每次 Action 查询、审批、claim、验证和导出都必须同时过滤 tenant。
- `created_by` 与 approver 必须来自认证上下文，不能信任请求体。
- 支持职责分离：建议人不得审批自己的高风险动作；金额/敏感度达到阈值要求两个不同授权人。
- 审批权限在决定时快照，后续角色变化不改写历史；执行前仍要复核账号未禁用、策略未撤销。
- Evidence payload 使用分类、字段级脱敏、租户密钥和保留策略；密钥、Cookie、完整环境变量、输入框值禁止进入 Ledger。
- Trace 中现有完整工具参数/结果需要单独制定缩短保留期和脱敏迁移，不能直接批量复制到 Ledger。
- Evidence Bundle 导出是受审计的高权限动作，导出文件带 tenant、生成时间、hash 和字段脱敏说明。

## 12. 故障语义

| 故障点 | 状态与处理 |
|---|---|
| Intent 已写、未 dispatch | `READY`，可安全按同 key 重试 |
| Approval 完成但策略已更新 | 执行前重评；不满足则 invalidated，不执行 |
| Dispatch 请求未到执行器 | Attempt 可超时并重新 claim；fencing 阻止旧 claim |
| 请求发出、响应丢失 | 若外部幂等/查询可确认则回查；否则 `STATUS_UNKNOWN` |
| Runtime 执行后离线 | 本地 journal 保留；服务器显示 unknown，不换节点重做 |
| Ledger 写入失败 | 对强审计动作不得开始 dispatch；执行后回执写失败则进入 reconciliation 且不能宣称完整成功 |
| Verification 服务故障 | 保持 `SUBMITTED/VERIFYING`，不自动映射成功 |
| 设备签名无效/序号回退 | 拒绝 Entry，吊销或隔离节点，动作进入人工复核 |
| Blob 丢失但 digest 在 | 标记 payload unavailable，完整性事实仍在；Bundle 降级 |
| 补偿失败 | 保留原动作和失败补偿，转 `MANUAL_REVIEW_REQUIRED` |

## 13. 与现有体系的迁移

### Phase 0：契约与风险目录

- 盘点全部工具，声明 effect、resource、risk、idempotency、pre/postcondition 和敏感字段。
- 先锁定 Action/Evidence JSON Schema、错误码和 canonical hash 规则。
- 给发布、发送、审批、删除、更新、支付、GUI control 建立 P0 高风险清单。

### Phase 1：Shadow Ledger

- 新增表、append service、outbox、hash 校验和 Evidence Bundle 查询。
- 只旁路记录，不阻断现有工具；选择社媒审核/发布、浏览器 run、合同审批做 adapter。
- 对比现有业务表和 Ledger，监控漏记、错序、重复和敏感字段泄漏。

### Phase 2：高风险强制门禁

- 发布/发送/审批/改业务数据接入冻结 Intent、服务器 Policy、Approval hash、幂等和验证。
- 社媒现有 `review_records`/publish snapshot/attempt 表保持业务权威，事务 outbox 生成 Evidence Entry。
- `work_outcomes` 增加 evidence bundle 关联；没有验证的 action outcome 明确标“推断/未验证”。

### Phase 3：统一 Tool Middleware

- Cloud Agent 所有 server tools 通过 Action Gateway。
- Desktop Local Tool Host 和 `agent-tool-runtime` 支持 action envelope、设备签名、journal、fencing 和结果补传。
- 本地文件读取等低风险动作按策略只记摘要/采样；写、删、发、GUI 操作强制记录。

### Phase 4：治理与合规

- Evidence 工作台、争议处置、补偿任务、Bundle 导出、保留策略和 WORM root export。
- 业务级完整度指标、Golden Tasks 回放和高风险动作审计告警。

### 13.1 兼容规则

- 不删除 `chat_records`、`obs_*`、`work_outcomes`、`social_*` 或 `bs_browser_*`。
- 旧调用没有 `action_id` 时 adapter 生成 legacy action，并标记 `coverage=partial`，不伪造缺失审批和验证。
- 新字段先可空、双写、对账，再开启非空和门禁；回滚只关闭新 middleware，不删除 Ledger 数据。
- 业务表和 Ledger 同库时用事务 outbox；跨库时先写业务事实/outbox，再由幂等消费者追加 Ledger。

## 14. 测试与验收

### 14.1 单元/契约

- canonical JSON 与 hash chain golden fixtures（Python/TypeScript 一致）。
- Effect contract、审批 hash 失效、职责分离、过期和策略重评。
- 同幂等 key 同 digest 重放、不同 digest 冲突。
- 所有 Action 状态合法/非法转换。
- Entry 只追加、纠错事件、blob crypto-shred 后 hash 可验证。
- tenant 条件与跨租户 ID 猜测拒绝。

### 14.2 集成与故障注入

- 发布内容 revision 改变后旧审批失效。
- API 返回 provider object ID 后独立回查成功，Bundle 达到 RECEIPTED/VERIFIED。
- 外部请求执行成功但响应断开，进入 unknown；确认状态前不重发。
- GUI 点击后 Runtime 掉线，不转其他节点、不重复点击。
- Desktop journal 重连补传：重复、乱序、签名错误和序号回退均正确处理。
- Worker claim 过期被回收，旧 worker fencing token 提交被拒。
- Ledger/Blob/Outbox 任一故障时满足 fail-closed 或明确降级策略。
- 渠道审批只有授权用户、正确 approval ID 和 intent digest 才生效。

### 14.3 验收门禁

- 每个 P0 高风险动作均能导出包含 Intent、Policy、Approval、Attempt、Receipt/Observation、Verification 的 Bundle；缺项明确标记。
- 不可逆动作在未知状态下零自动重试。
- 日志和 Ledger 中无明文密钥、Cookie、完整环境变量或敏感输入框值。
- 100% 新增 SQL 查询携带 tenant；设备 claim 同时校验 tenant/user/node/provider/tool/digest。
- 新 middleware 关闭时现有 Web/渠道链路可回退；开启后不能通过旧直连路径绕过。

## 15. 风险与待决策

| 项目 | 风险/待决策 |
|---|---|
| Ledger 存储量 | 需按 effect 分级采集、payload 外置、冷热分层和租户保留期 |
| Observability 原文 | 当前 Trace 保存完整输入输出，需单独隐私整改；不能直接当 Evidence 数据源 |
| 服务端不可用时的本地动作 | 建议高风险 fail-closed；低风险是否允许离线执行由租户策略决定 |
| 设备密钥 | 需确定 TPM/Keychain/DPAPI 策略、轮换、吊销和重装后的 installation identity |
| 外部系统无查询 API | 只能使用受控 UI observation 或人工 attestation，证据等级必须如实降低 |
| 通用审批 UI | 要支持 Web/移动渠道与 Desktop，但必须绑定同一 approval/intent hash |
| Compensation 目录 | 需要逐 Provider 定义，不能由 LLM 动态编造逆操作 |
| 法务保留 | 审批、支付、招聘、客户沟通的保留期和可删除要求不同，需要租户/地区策略 |
| hash chain 运维 | 需设计并发 append、分区、备份恢复及链断裂告警；不应声称等同电子签章 |
| `work_outcomes` 删除 | 现有删除 API与审计保留冲突；建议后续改为用户视图隐藏 + Ledger tombstone |

## 16. 架构红线

1. Agent 回复、工具 `success=true`、按钮点击和“外部已受理”都不等于业务完成。
2. 输入、目标、版本、工具 digest 或节点改变后不得复用旧审批。
3. 不可逆动作未知时不得盲重试、换节点或让另一个 Agent 重做。
4. 不允许 Desktop/Runtime 自报租户权限、风险等级、计费金额或提高证据等级。
5. Ledger 纠错只能追加，不物理修改历史；敏感内容删除使用 crypto-shred + tombstone。
6. Evidence Ledger 不记录隐藏思维过程，不以合规名义无限保存敏感原文。
7. 现有局部状态机可以复用，但必须通过 adapter 关联，不能各自宣称构成全局业务闭环。
