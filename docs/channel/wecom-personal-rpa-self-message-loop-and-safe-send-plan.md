# 企业微信个人 RPA 自消息循环与错发防护优化方案

## 一、问题结论

本次真实链路确认有两类风险：

1. **服务端自消息循环**：会话存档同时包含外部联系人入站和企业成员自身出站。当前 fetcher 统一把明文 `from` 当作 `sender_stable_id`，没有按当前 RPA 账号身份判断方向。Agent 发出的回复因此可能再次进入 Agent，生成发往账号自身（如 `waynelu`）的 outbox。
2. **客户端任务串行粒度不足**：客户端虽然已有 `SingleReader` 单 Worker，但会先把一个 `ActionEnvelope` 拆成多个独立 action 入队。当前互斥粒度是 action，不是完整消息；并发入队、进程恢复或客户端双开时，理论上可能在一条消息的文本、图片、文件动作之间插入另一条消息的 RPA 操作。

本次事故的首要根因仍是服务端把 `waynelu` 自身出站消息再次送入 Agent，产生了本不应存在的客户端任务。把 `waynelu` binding 设为 pending 是正确的临时止损，但不能替代方向过滤；客户端还需要保证完整消息级串行，防止合法并发消息之间争抢企微窗口。

## 二、优化目标

- 只有“外部联系人 → 当前企业微信成员”的消息进入 Agent。
- “当前企业微信成员 → 外部联系人”的存档消息只审计，不写 archive inbox，不产生 Trace、channel_messages 或 outbox。
- 方向不明时失败关闭，不猜测发送人或会话。
- 同一时刻只能有一个客户端进程操作当前 Windows 桌面上的企微。
- 一个消息 envelope 的搜索、文本、图片、文件和回执必须连续执行完成，才能开始下一条消息。
- 所有服务端下发入口统一进入同一持久化 FIFO，禁止绕过队列直接调用 PowerShell。

## 三、服务端方向过滤

### 3.1 建立权威账号身份

每个 RPA account 配置企业微信成员 userid：`wecom_user_id`（本账号示例为 `waynelu`），与 `tenant_id + account_id` 绑定。可增加只用于历史兼容的 `wecom_user_aliases`，但禁止使用展示名或模糊匹配判断账号身份。

配置缺失时不得启用 server archive 自动回复；存量配置在管理端提示补全。

### 3.2 单聊判定规则

| 原始存档字段 | 方向 | 处理 |
|---|---|---|
| `from == wecom_user_id` | `outbound_self` | 过滤并审计，推进 seq |
| `wecom_user_id in tolist` 且 `from != wecom_user_id` | `inbound_external` | 允许写 inbox 并进入 Agent |
| 其他或关键字段缺失 | `direction_unknown` | 失败关闭、告警、推进 seq |

入站联系人的稳定 ID取 `from`；peer 应从 `from/tolist` 中排除当前 `wecom_user_id` 后计算，不能继续固定使用 `tolist[0]`。这样同一联系人不会因收发方向不同生成两个会话键。

### 3.3 群聊规则

- `from == wecom_user_id` 时过滤本账号自己在群内发送的消息。
- 其他成员消息只有在群聊已授权、发送人满足监控策略且符合群触发规则时才进入 Agent。
- `roomid` 始终作为会话稳定键，发送人只写 metadata。

### 3.4 过滤位置与双保险

主过滤放在 archive 解密后、写 `wecom_rpa_archive_inbox` 前：

```text
解密消息 → 读取账号 wecom_user_id → 判断方向/peer
  ├─ inbound_external：构造 envelope → 写 inbox
  ├─ outbound_self：写 filtered audit → 推进 seq
  └─ direction_unknown：写诊断 audit/告警 → 推进 seq
```

在 `_process_inbound_message` 增加第二道防线：历史 inbox、人工重放或旧客户端消息若 sender 命中当前账号 userid，同样直接过滤。第二道防线不能替代 fetcher 源头过滤。

## 四、循环熔断

- outbox 保存目标 peer、request_id、发送完成时间及回复摘要。
- 短时间收到 sender 为当前账号自身、且对应近期出站的存档消息时标记 `self_echo_detected`。
- 单账号连续出现自消息回流达到阈值时自动暂停该 account 的 Agent 出站并告警，避免 token 消耗和持续误发。
- 文本相等只能作为异常检测辅助，不能作为主要方向判断，避免误伤用户引用 Agent 回复的场景。

## 五、客户端消息级串行发送

### 5.1 串行单位

```text
领取一个 ActionEnvelope(request_id)
  → 依次执行 actions[0..n]
      → 每个 action 内部完成搜索、输入/粘贴、发送和结果记录
  → 当前 envelope 全部动作进入终态
  → 上报逐 action 回执并释放执行权
  → 领取下一条 envelope
```

串行锁的持有范围必须覆盖整个 envelope，而不是单个 action。一个 action 失败时，先按既定重试策略完成该 action 的终态处理；若最终失败，则把当前 envelope 未执行动作标记为 `aborted_by_previous_action` 并回报，随后才允许处理下一条消息。

### 5.2 单实例和统一队列

- 复用 C# dispatcher 的单 Worker；本地 SQLite 增加 envelope 主表，并让 action 明细显式保存 `request_id + action_index`，按完整 envelope 领取后严格顺序执行。
- `EnvelopeEnqueueAsync` 必须在一个 SQLite 事务内写入完整 envelope，成功后只向工作通道投递一次 `request_id`，避免不同 envelope 的 action 交错写入。
- outbox 轮询、WebSocket 通知和启动恢复只负责投递到同一队列，任何入口都不能直接执行 PowerShell。
- 增加 Windows 命名 Mutex，约束同一登录会话只允许一个桌面自动化 dispatcher 工作；第二个客户端进程只能保持待命/报错，不能操作企微。
- 进程重启时以 envelope 为单位把悬挂的 running 状态恢复为 pending，并从该 envelope 开始处理，不能跳到后续消息。

### 5.3 回执语义

- 保持现有逐 action 回执协议，`request_id + action_index` 继续作为服务端结果关联键。
- 成功 action 正常上报 `succeeded`；最终失败 action 上报实际错误码。
- 同 envelope 中尚未执行的后续动作统一上报 `aborted_by_previous_action`，不得静默丢弃，否则服务端 outbox 无法进入确定终态。
- 只有当前 envelope 的每个 action 都有明确终态后，客户端才释放执行权并处理下一条消息。

## 六、管理与可观测性

- RPA 账号配置页维护并校验 `wecom_user_id`，缺失时禁止启用自动回复。
- archive 审计记录 `message_direction`、`direction_reason`、event_id 及脱敏 sender/recipient。
- 增加指标：`self_outbound_filtered_total`、`direction_unknown_total`、`self_echo_detected_total`、`envelope_queue_depth`、`envelope_execution_seconds`、`envelope_aborted_total`。
- pending 保留为人工暂停手段，但不承担方向识别职责。
- 原始错误 Trace/outbox 不物理删除；把本次 `waynelu` 自循环会话标记为 `self_echo/paused`，禁止污染“陆伟”上下文。

## 七、开发步骤

### Phase 0：固化真实样本

用本次数据制作脱敏 fixture：外部联系人入站、账号自身出站回流、方向缺失、群聊。先写失败测试，证明当前逻辑会把自身消息送入 Agent。

### Phase 1：服务端过滤

1. 增加账号 `wecom_user_id` 配置、租户隔离和启用校验。
2. fetcher 增加纯函数方向判定与稳定 peer 推断。
3. inbox 前过滤并补齐审计、指标、seq 推进。
4. `_process_inbound_message` 增加第二道自消息防线。
5. 增加自循环熔断和账号自动暂停告警。

### Phase 2：客户端消息级串行

1. 本地 SQLite 增加 envelope 主表及 action 的 `request_id/action_index`，提供事务化 `EnqueueEnvelope/ClaimNextEnvelope/CompleteEnvelope`。
2. dispatcher 从 action 级调度改为 envelope 级调度，完整执行当前消息后才领取下一条。
3. outbox poller、WebSocket 和启动恢复统一走 envelope 队列，补齐并发入队和恢复顺序。
4. 增加 Windows 命名 Mutex，阻止同一桌面双客户端并发操作企微。
5. 补齐后续 action 中止回执、队列深度和执行耗时；日志只记录 request_id、action_index 和状态，不记录正文。

### Phase 3：管理端与历史治理

1. 配置页增加企业成员 userid 和状态校验。
2. 监控页展示过滤、熔断、消息队列积压和 envelope 中止原因。
3. 标记本次错误会话，不自动重放已经产生回复的用户消息。

### Phase 4：测试与发布

按三智能体流程串行完成开发、独立测试和 CodeReview。先发布服务端方向过滤，观察指标正常后再发布客户端消息级串行。最后使用两个联系人同时来消息、单条消息包含文本+附件、客户端重启和客户端双开等场景做真机验收。

## 八、验收标准

- 陆伟发一条消息，只产生一条 inbox、一个有效 Trace 和一组发往陆伟的 outbox 动作。
- Agent 回复进入存档后标记为 `outbound_self`，不再产生 inbox/Trace/outbox。
- 两个联系人同时产生回复任务时，客户端严格按 FIFO 完整处理第一条消息后才开始第二条。
- 同一 envelope 的文本和附件连续执行，中间不能插入其他消息的搜索或发送操作。
- 同一 Windows 登录会话双开客户端时，只有一个进程能获得 RPA 执行权。
- `waynelu` 自循环会话不再增长，也不进入陆伟的上下文。
- 观察稳定后，再由管理员决定是否解除 `waynelu` pending。
