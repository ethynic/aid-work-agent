# 企业微信个人 RPA 自消息循环与错发防护技术实现方案及开发计划

## 1. 依据、结论与范围

- 需求依据：[自消息循环与错发防护优化方案](wecom-personal-rpa-self-message-loop-and-safe-send-plan.md)。
- 当前服务端 `archive/fetcher.py::_build_envelope()` 使用明文 `from` 作为 `sender_stable_id`，单聊会话键使用 `from + tolist[0]`，未识别当前企业成员身份和消息方向。
- 当前客户端虽使用 `SingleReader` 单 Worker，但 `EnvelopeEnqueueAsync()` 会把一个 `ActionEnvelope` 拆成多个 `OutboxItem`。现有互斥粒度是 action，不是完整消息；并发入队、恢复或客户端双开时，缺少“当前消息完整处理后再开始下一条”的强约束。
- 本任务只治理企业微信个人 RPA 的 archive 入站、Agent 出站与桌面发送链路，不改企微客服渠道，不恢复已废弃的客户端入站监听。
- 原始 Trace、outbox 和审计数据保留；历史错误会话只做状态标记，不物理删除。

## 2. 总体技术方案

```text
会话存档密文
  → 解密
  → DirectionClassifier(wecom_user_id, from, tolist, roomid)
      ├─ inbound_external / inbound_group
      │    → 构造稳定 peer/room envelope → archive inbox → 二次身份防线 → Agent
      ├─ outbound_self
      │    → filtered audit + echo detection → 推进 seq，不写 inbox
      └─ direction_unknown
           → diagnostic audit + alert → 推进 seq，不写 inbox

Agent reply → outbox(target_peer_id, request_id, reply_digest)
  → 完整 ActionEnvelope 事务入本地 SQLite FIFO
  → Windows 命名 Mutex 获取当前桌面唯一 RPA 执行权
  → C# 单 Worker 领取最早 request_id
  → 连续执行 actions[0..n]，期间不领取其他消息
  → 所有 action 进入 succeeded/failed/aborted 终态
  → 释放当前 envelope → 领取下一条
```

核心安全边界：方向不明不进入 Agent；同一 Windows 桌面只能有一个 RPA 执行器，一个消息 envelope 未完整结束前禁止开始下一条消息。客户端不引入 UI Automation、OCR 或会话标题识别。

## 3. 数据模型与配置

### 3.1 `wecom_rpa_accounts` 账号身份

在 `deploy/init-postgres.sql` 和 `deploy/db_update.sql` 同步增加：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `wecom_user_id` | `TEXT` | `NULL` | 企业微信会话存档中的成员 userid，方向判断权威值 |
| `wecom_user_aliases` | `TEXT[]` | `'{}'` | 仅兼容已确认的历史 userid，不保存展示名 |
| `identity_verified_at` | `TIMESTAMP` | `NULL` | 管理员最近确认时间 |
| `identity_verified_by` | `TEXT` | `NULL` | 管理员用户 ID |

约束与规则：

- 所有读写 SQL 必须同时带 `tenant_id + account_id`；补充租户内 `wecom_user_id` 非空唯一索引，避免一个 userid 绑定到多个 RPA account。
- userid 仅做首尾空白清理和精确比较，不转小写、不按包含关系匹配；aliases 不允许包含主 userid、重复值或与同租户其他账号冲突。
- `upsert_account()` 不得用客户端状态事件覆盖管理员维护的身份字段。
- 缺少 `wecom_user_id` 时账号标记为“身份未配置”，archive 仍可拉取并推进 seq，但所有消息按 `direction_unknown` 过滤，不能进入自动回复。
- 第一版不单独增加 `auto_reply_enabled` 字段，自动回复准入条件为：账号未暂停、身份已配置、渠道配置已验证。现有 `pending/paused` 继续作为人工止损开关。

### 3.2 outbox 诊断字段

`wecom_rpa_action_outbox` 增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| `target_peer_id` | `TEXT` | 单聊稳定 peer 或群 `roomid`，用于防止目标为本账号及 echo 关联 |
| `reply_digest` | `TEXT` | 对规范化动作类型、正文和附件标识计算 HMAC-SHA256，不保存额外正文副本 |
| `send_started_at` | `TIMESTAMP` | 客户端首次执行动作时间 |

`request_id`、`completed_at` 和已有 `actions/reply_context` 继续保留。日志和审计只输出 ID 的 HMAC 短摘要、动作类型、长度及状态，不新增正文、搜索名、绝对路径或 userid 明文日志。

### 3.3 协议兼容

- 服务端 `ActionEnvelope` 和逐 action 回执协议保持不变，继续使用 `request_id + action_index` 关联结果。
- 客户端本地队列补充 `request_id`、`action_index` 和 envelope 状态，这些是本地持久化字段，不需要新增服务端 wire 字段。
- `RpaActionResultPayload.error_code` 已支持字符串扩展，可直接增加 `aborted_by_previous_action`。
- 发布时通过现有 `min_version` 阻止不具备消息级串行能力的旧客户端继续消费 outbox。

## 4. 服务端方向识别

### 4.1 新增纯函数模块

新增 `src/channels/wecom_personal_rpa/archive/direction.py`：

```python
class MessageDirection(str, Enum):
    INBOUND_EXTERNAL = "inbound_external"
    INBOUND_GROUP = "inbound_group"
    OUTBOUND_SELF = "outbound_self"
    DIRECTION_UNKNOWN = "direction_unknown"

@dataclass(frozen=True)
class DirectionDecision:
    direction: MessageDirection
    peer_id: str | None
    conversation_id: str | None
    reason: str
```

`classify_archive_message()` 只接收规范化后的 `self_ids/from_user/tolist/roomid`，不访问数据库，便于用真实脱敏 fixture 覆盖全部分支。

判定顺序：

1. `wecom_user_id` 缺失、`from` 缺失、字段类型错误：`direction_unknown`。
2. `roomid` 非空：
   - `from` 命中 self ids：`outbound_self`，`conversation_id=roomid`；
   - 否则：`inbound_group`，`conversation_id=roomid`，`peer_id=from`。后续仍由既有 binding、监控白名单和群触发规则决定是否进入 Agent。
3. 单聊 `from` 命中 self ids：`outbound_self`；peer 从 `tolist` 排除 self ids 后求唯一值，多值或空值不猜测，peer 留空并记录原因。
4. 单聊 `from` 不命中 self ids，且 `tolist` 精确包含 self id：`inbound_external`，`peer_id=from`，`conversation_id=dm:{from}`。
5. 其他情况：`direction_unknown`。

aliases 只参与 `from/tolist` 的 self 精确匹配。所有决定都带稳定 `reason` 代码，例如 `sender_is_self`、`self_in_recipient_list`、`self_identity_missing`、`recipient_not_self`、`multiple_peers`。

### 4.2 fetcher 主过滤

调整 `ServerArchiveFetcher._fetch_unlocked()` 和 `_build_envelope()`：

1. 在拉取批次前读取账号并构造 self ids；身份缺失不调用 Agent 路径。
2. 每条解密后先解析并规范化方向字段，再调用 `classify_archive_message()`。
3. 只有 `inbound_external/inbound_group` 才调用 `_build_envelope()` 和 `enqueue_inbound_archive_message()`。
4. `outbound_self/direction_unknown` 写 archive 审计并更新计数，然后推进当前 item 的 seq；不得写 archive inbox。
5. inbound 的单聊 `sender_stable_id` 和稳定会话 peer 均使用 `decision.peer_id`；群聊始终使用 `roomid`，真实发送人只进入 payload metadata。
6. 审计写失败不能造成同一条永久重拉；记录 `last_error` 后仍推进 seq。inbound inbox 写失败仍保持现有语义：停止本批且不得推进该条 seq。

把“解密失败”“安全过滤”“可靠 inbox 投递失败”分开计数，避免现有 `processed_count` 混淆。建议返回批次统计：`inbound_enqueued`、`self_filtered`、`unknown_filtered`、`decrypt_failed`。

### 4.3 `_process_inbound_message` 第二道防线

在创建 adapter、binding、用户、Trace 之前执行：

- 按 `tenant_id + env.account_id` 读取账号权威身份。
- 身份缺失：写 `direction_guard_filtered`，立即返回。
- `payload.sender_stable_id` 命中 self ids：写 `self_message_guard_filtered`，立即返回并触发危险 echo 计数。
- `server_fetcher` 消息必须携带 `message_direction`、`direction_reason`、`archive_peer_id`；字段缺失或方向不是 inbound 时失败关闭。
- 旧 inbox、人工重放、旧客户端 callback 同样受 sender=self 防线约束，不允许依赖 binding pending 才止损。

审计内的 sender/recipient 使用服务端 HMAC 脱敏函数，禁止写 userid 明文。第二道防线返回前不得调用 `ensure_user_registered`、`start_record`、session queue 或 action client。

## 5. 群聊授权

- `conversation_id` 固定为 `roomid`，不因发送成员变化产生新 session。
- self 消息始终在 fetcher 过滤。
- 非 self 消息继续走现有 `check_conversation_authorization()` 和 binding 级 `monitor_user_ids/monitor_user_names`；第一版不新增一套重复白名单。
- 在 payload metadata 增加 `archive_sender_id_hash` 和 `message_direction=inbound_group`，用于审计，不把发送人当作群会话稳定键。
- 未授权群、发送人不满足监控策略或群触发条件时不进入 Agent，并沿用 needs_review/monitor filtered 审计。

## 6. 自循环检测与熔断

正确过滤的企微出站存档本身是正常现象，不能仅因连续出现 `outbound_self` 就暂停账号，否则正常连续回复会误触发熔断。实现分为观察信号与危险信号：

- 观察信号 `self_echo_detected`：self 出站存档在 120 秒窗口内按 `account_id + target_peer_id + reply_digest` 命中已完成 outbox，只增加指标和审计，不累计暂停阈值。
- 危险信号 `self_echo_escaped`：第二道防线从 archive inbox/重放中截获 sender=self，或发现新 outbox 的 `target_peer_id` 命中 self ids。
- 使用 Redis 滑动窗口键 `wecom_rpa:self_echo_escape:{tenant_id}:{account_id}`；5 分钟内达到 3 次时，以条件更新把账号置为 `paused`，`paused_reason=self_echo_circuit_breaker`，写审计并告警。
- 暂停操作必须幂等；Redis 不可用时仍过滤当前消息，但不自动恢复、不绕过安全检查，并产生降级告警。
- 恢复只能由管理员执行。恢复前管理端要求确认 `wecom_user_id`、最近危险事件及客户端安全版本，恢复动作清空熔断窗口但保留审计。

## 7. 客户端消息级串行执行

### 7.1 当前缺口

`OutboundActionDispatcher` 已有 `Channel.CreateUnbounded(... SingleReader=true)`，因此单进程内同一时刻只执行一个 action。但当前实现存在三个边界缺口：

- `EnvelopeEnqueueAsync()` 循环拆分并逐 action 入队，多个调用方并发进入时可能在 await 边界交错。
- SQLite 主键是拼接后的 `action_id`，没有独立的 `request_id/action_index/envelope_status`，启动恢复只能按 action 恢复，不能证明一个 envelope 连续完成。
- 单 Worker 只在当前进程生效；同一 Windows 用户双开客户端时，每个进程都有自己的 Worker 和 SQLite gate，仍可能同时操作企微。

### 7.2 本地队列模型

本地 SQLite 使用 envelope 主表 + action 明细表。新增 `outbox_envelopes`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | `TEXT PRIMARY KEY` | 原始 `ActionEnvelope.request_id` |
| `status` | `TEXT` | `pending/running/done/failed` |
| `action_count` | `INTEGER` | 应进入终态的 action 总数 |
| `created_at/updated_at/completed_at` | `TEXT` | FIFO、恢复和清理时间 |

既有 `outbox_local` action 表增加 `request_id`、`action_index`，建立 `(request_id, action_index)` 唯一索引和外键语义；envelope 主表建立 `(status, created_at)` 领取索引。旧行按既有 action id 规则回填；无法可靠解析的旧行按单 action envelope 处理，不删除、不重复创建。

队列 API 调整为：

- `EnqueueEnvelopeAsync(env)`：一个 SQLite 事务内写入全部 actions；全部成功才提交。
- `ClaimNextEnvelopeAsync()`：只领取最早 pending `request_id`，同事务把其全部 action 标为 running，并按 `action_index` 返回。
- `CompleteActionAsync()`：写逐 action 终态和回执信息。
- `CompleteEnvelopeAsync()`：确认全部 action 已有终态后写 envelope 终态。
- `RecoverRunningEnvelopesAsync()`：启动时按 envelope 将悬挂 running 恢复为 pending，优先处理最早 envelope。

工作通道只投递一次 `request_id`，不再为每个 action 投递独立工作项。outbox poller、WebSocket 触发和启动恢复都只能调用 `EnqueueEnvelopeAsync()`，禁止直接调用 PowerShell。

### 7.3 完整消息执行边界

新增 `DispatchEnvelopeAsync(request_id)`，由唯一 Worker 执行：

1. 领取并固定当前 envelope 的有序 action 列表。
2. 对 action 0 执行既有 PowerShell 搜索和发送；完成既定重试并写终态。
3. 成功后立即执行 action 1，直至全部完成；循环期间不读取工作通道中的下一 request_id。
4. 某 action 最终失败时，把尚未执行的 action 标为 `aborted_by_previous_action` 并逐项回报，确保服务端 outbox 能进入确定终态。
5. 当前 envelope 全部 action 完成回执尝试后，标记 envelope 终态并领取下一条。

下载附件、PowerShell 调用、重试退避、临时文件清理和回执上报都属于当前 envelope 的执行周期。等待这些异步操作不会释放消息级执行权。

### 7.4 单桌面唯一执行器

- 客户端启动 dispatcher 前获取当前 Windows 登录会话的命名 Mutex，例如 `Local\\AidWeComPersonalRpaDesktopAutomation_{SessionId}`，避免跨 Session 误互斥和 `Global` 命名空间权限问题。
- Mutex 范围覆盖 dispatcher 生命周期，不在单个 action 完成后释放。
- 同一登录会话中的第二个客户端进程获取失败时不得启动 RPA Worker，需上报 `desktop_automation_already_running` 并保持非执行状态。
- 进程异常退出后由操作系统释放 Mutex；新进程恢复时先领取本地最早未完成 envelope。
- 保留 SQLite `SemaphoreSlim` 保护数据库写入，但它不再被视为桌面自动化互斥锁。

本阶段不引入 UI Automation、OCR、会话标题识别或 conversation token；企微 DirectX UI 无法可靠暴露 UIA 节点，继续投入该方向不作为本问题的解决条件。

## 8. 管理端与可观测性

### 8.1 管理 API/UI

- 新增 `PATCH /accounts/{account_id}/identity`，请求包含 `wecom_user_id`、`wecom_user_aliases`；后端校验租户归属、唯一性和操作者权限，写 `identity_verified_*` 审计。
- 账号列表返回 `wecom_user_id_configured`、脱敏 userid、最近验证时间和 `auto_reply_ready`，不向普通页面返回完整 userid。
- `WecomPersonalRpaManager.vue` 的账号弹窗增加身份配置、缺失阻断提示、熔断原因和恢复前确认；保存后刷新账号状态。
- 监控页展示方向过滤、未知方向、危险 echo、envelope 队列积压和中止分布，支持按 account 和时间窗口筛选。

### 8.2 审计与指标

新增审计 category：

- `archive_direction_filtered`
- `direction_guard_filtered`
- `self_echo_detected`
- `self_echo_escaped`
- `self_echo_circuit_opened`
- `envelope_execution_aborted`
- `account_identity_updated`

新增计数指标：`self_outbound_filtered_total`、`direction_unknown_total`、`self_echo_detected_total`、`self_echo_escaped_total`、`envelope_aborted_total`，并增加 `envelope_queue_depth`、`envelope_execution_seconds`。维度只允许低基数字段：结果、reason、action_type、客户端版本；tenant/account 通过管理查询过滤，不作为 Prometheus 无界 label。

## 9. 分阶段开发计划

### Phase 0：真实样本与失败测试

任务：

- 从本次事故制作脱敏 archive fixture：外部联系人入站、账号自身单聊出站、字段缺失、单聊多 recipient、群聊 self/非 self。
- 增加失败测试，证明当前 `_build_envelope()` 会把 self sender 写入 inbox，并证明当前客户端只保证 action 级串行，缺少 envelope 原子入队、连续执行和跨进程互斥。
- 用数据库查询固化基线：每个 event 对应的 inbox、Trace、channel message 和 outbox 数量。

预计文件：

- `tests/unit/channels/wecom_personal_rpa/archive/fixtures/`
- `tests/unit/channels/wecom_personal_rpa/archive/test_direction.py`
- `tests/unit/channels/wecom_personal_rpa/archive/test_fetcher.py`
- `clients/wecom-personal-rpa/src/Client.Tests/Outbound/OutboundQueueTests.cs`（新增或并入现有测试）
- `clients/wecom-personal-rpa/src/Client.Tests/Outbound/OutboundActionDispatcherTests.cs`

退出条件：至少一个服务端测试稳定复现 self sender 缺口；客户端测试能稳定证明并发 envelope 入队、启动恢复或双实例场景缺少完整消息级互斥。

### Phase 1：账号身份与服务端过滤

任务：

- 完成数据库兼容迁移、账号 identity CRUD、管理 API 校验。
- 实现 `direction.py`、fetcher inbox 前过滤、稳定 peer/room envelope、审计与指标。
- 增加 `_process_inbound_message` 二次防线。
- 补充 outbox target/digest 字段和危险目标检查。

预计文件：

- `deploy/init-postgres.sql`、`deploy/db_update.sql`
- `src/channels/wecom_personal_rpa/db.py`
- `src/channels/wecom_personal_rpa/archive/direction.py`
- `src/channels/wecom_personal_rpa/archive/fetcher.py`
- `src/channels/wecom_personal_rpa/archive/audit.py`
- `src/saas/api/wecom_personal_rpa_routes.py`
- `src/saas/api/wecom_personal_rpa_admin.py`
- 对应 unit/integration tests

退出条件：self/unknown 只审计并推进 seq；inbound 每个 event 只产生一个 inbox；第二道防线命中时无用户注册、Trace、channel message 和 outbox。

### Phase 1B：echo 关联与熔断

任务：

- 实现 recent outbox 关联、观察/危险信号分离、Redis 滑动窗口和幂等暂停。
- 管理端恢复前置校验，补充 Redis 故障降级测试。

退出条件：正常 self echo 不暂停账号；危险 echo 达阈值自动暂停，暂停后不再产生新 outbox；人工恢复有完整审计。

### Phase 2：客户端消息级串行

任务：

- 本地 SQLite 增加 request/action/envelope 字段和幂等迁移，实现完整 envelope 事务入队及领取。
- dispatcher 改为 `DispatchEnvelopeAsync()`，保证当前消息全部 action 终态后才读下一条。
- 统一 poller、WebSocket、启动恢复入口，补齐并发入队、失败中止和逐 action 回执。
- 增加 Windows 命名 Mutex，阻止同一登录会话双客户端同时操作企微。
- 扩充 C# 队列、调度、取消、恢复、回执和临时文件清理测试；PowerShell 的既有搜索发送逻辑不引入 UIA/OCR 重构。

预计文件：

- `clients/wecom-personal-rpa/src/Client.App/Outbound/OutboundQueue.cs`
- `clients/wecom-personal-rpa/src/Client.App/Outbound/OutboundActionDispatcher.cs`
- `clients/wecom-personal-rpa/src/Client.App/Outbound/OutboxPoller.cs`
- 客户端启动/DI 文件（注册命名 Mutex 生命周期）
- `clients/wecom-personal-rpa/src/Client.Tests/`

退出条件：并发到达的两条消息严格 FIFO；第一条的文本和附件全部完成后才开始第二条搜索；失败/重试期间不穿插下一条；重启后先恢复最早 envelope；双开时只有一个进程能获得执行权。

### Phase 3：管理端、历史治理与观测

任务：

- 完成账号身份配置 UI、指标和失败原因展示。
- 将 `waynelu` 事故相关 binding/session 标记为 `self_echo/paused`，保留原始数据，禁止自动重放已产生回复的消息。
- 编写只读核对 SQL 和一次性治理脚本；脚本必须先 dry-run，按 tenant/account/event 白名单执行且可重复运行。

退出条件：管理员可识别缺配置和熔断账号；历史治理不删除数据、不改其他租户/会话，重复执行无副作用。

### Phase 4：独立测试、Code Review 与发布

- 按项目三智能体流程串行执行开发、独立测试和 Code Review；不自动提交、不 push。
- 后端运行新增测试、archive 全量、RPA flow/whitelist/router/action result/admin/observability 相邻回归和关键 import 检查。
- 客户端运行 `dotnet test`、PowerShell 静态测试、Release build；前端运行测试与 `npm run build`。
- 先发布数据库兼容迁移和服务端过滤，观察至少一个完整业务周期；再发布强制安全发送客户端，最后启用管理 UI 和熔断自动暂停。

## 10. 测试矩阵

| 场景 | 期望 inbox/Agent/outbox | 期望客户端行为 |
|---|---|---|
| 外部联系人 → 当前成员 | 1 / 1 / 1 组，目标为外部联系人 | 唯一验证后发送 |
| 当前成员 → 外部联系人存档回流 | 0 / 0 / 0，seq 推进 | 不涉及发送 |
| 缺少 userid/from/tolist 不匹配 | 0 / 0 / 0，unknown 告警 | 不涉及发送 |
| 群内当前成员发言 | 0 / 0 / 0 | 不涉及发送 |
| 已授权群内其他成员发言 | 1 / 按触发规则 / room 目标 | 按 FIFO 完整执行 |
| 陆伟、张三同时产生回复 | 两组独立 outbox | 完整执行较早 envelope 后再执行下一条 |
| 单 envelope 含文本+图片+文件 | 一组 outbox、逐 action 回执 | 三个动作连续执行，中间不读取下一消息 |
| 中间 action 重试 | 当前 envelope 保持 running | 重试完成前不执行下一消息 |
| 中间 action 最终失败 | 后续 action 明确 aborted | 全部回执终态后再执行下一消息 |
| 执行中客户端重启 | 服务端不新增业务回复 | 恢复最早未完成 envelope，不越过它 |
| 同一桌面双开客户端 | outbox 不重复生成 | 只有一个进程获得命名 Mutex 并执行 |
| 正常连续三次 self echo | 仅观察指标，不暂停 | 无影响 |
| 三次危险 self echo escape | 账号自动 paused | 后续 action 不执行 |

## 11. 发布、回滚与验收

发布顺序：

1. 先执行只增列/索引的兼容迁移；确认旧服务和旧客户端仍可运行。
2. 给 `waynelu` 配置精确 `wecom_user_id`，保持 pending/paused；服务端方向过滤先以审计指标验证，不解除暂停。
3. 服务端过滤正式生效后观察 `direction_unknown`、inbox、Trace 和 outbox 对账。
4. 发布消息级串行客户端，提升 `min_version`，观察队列深度、执行耗时和 aborted 指标。
5. 使用两个联系人同时来消息、文本+图片+文件、执行中重启和客户端双开完成真机验收后，再由管理员决定是否恢复账号。

回滚原则：

- 数据库增列不回滚；代码回滚时旧版本忽略新字段。
- 服务端过滤保留独立开关，但关闭强制过滤仅用于紧急诊断，账号必须同时保持 paused，禁止恢复旧的不安全自动回复。
- 客户端消息级串行异常时通过 `min_version`/账号暂停阻止旧客户端消费，不回滚到 action 级可交错调度。
- 熔断自动暂停可单独关闭，self/unknown 方向过滤和客户端单实例、消息级串行不可随之关闭。

最终验收：

- 陆伟发送一条消息只产生一个 archive inbox、一个有效处理 Trace 和一组发往陆伟的 outbox；回复存档回流只增加 filtered audit。
- `waynelu` 自循环会话不再新增 Trace、channel message 或 outbox，也不进入陆伟上下文。
- 两个联系人同时产生任务时，客户端完整执行第一条 envelope 后才开始第二条；同一 envelope 的文本、图片、文件中间无其他消息操作。
- 客户端重启不会越过最早未完成 envelope；同一桌面双开时只有一个执行器；敏感 userid、正文和本地路径不出现在新增日志和审计中。
- 自动化测试、关键 import、客户端 Release build、前端 build 和真机测试全部通过后，才将 `docs/ideas.md` 条目移至 `docs/ideas_finished.md`。

## 12. 当前状态

- 2026-07-13：技术实现方案与开发计划完成，代码尚未开发。
- 开发开始后按 Phase 更新 `docs/ideas.md` 为实际进度；并发、恢复和双实例真机验收未通过时不得标记客户端消息级串行完成。
- 本次仅新增/更新文档，不提交、不 push。
