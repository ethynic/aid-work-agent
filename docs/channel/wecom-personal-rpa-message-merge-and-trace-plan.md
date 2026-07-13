# 企业微信个人 RPA 消息合并与无效 Trace 治理开发计划

## 1. 背景与现状

本计划处理两个相互关联的问题：

1. RPA 会话存档消息曾因旧消费链路阻塞、重复取数而多次启动 Agent，监控页出现同一用户输入的重复空 Trace。
2. 用户连续发送多条补充消息时，RPA 应复用微信客服已经使用的会话级消息合并能力：Agent 尚未开始向渠道发送回复时，取消旧请求并合并输入，只生成一次最终回复。

代码核对结果：

- 微信客服并非使用渠道私有合并器，而是调用公共链路 `ChannelSessionManager.process_and_persist()` → `SessionMessageQueue.enqueue_and_process()`。
- RPA 的 `_process_inbound_message()` 当前也已进入该公共链路，基础接入已经存在。
- 因此本次不复制微信客服实现，也不新增 RPA 私有队列；开发重点是验证 RPA 的 `session_id` 稳定性、archive inbox 并发投递、取消检测和出站状态标记是否满足公共合并器的契约。
- `user_message_id` 是通用 Trace 字段，不是 RPA 专属字段。不能仅凭该字段为空就隐藏 Trace。

## 2. 目标

### 2.1 RPA 连续消息合并

- 同一租户、RPA 账号和企微会话的连续消息必须映射到同一个稳定 `session_id`。
- Agent 尚未开始发送回复时，新消息触发取消并与已有输入合并；旧调用不落 user/assistant 消息、不生成出站动作。
- Agent 已进入渠道回复阶段时，新消息进入 pending，等待当前轮完成后作为下一轮处理，避免撤销已经开始发送的内容。
- 文本与附件元数据都参与合并；不同会话、不同账号和不同租户绝不串线。
- archive inbox 保持可靠消费与事件幂等，消息合并不能替代 event_id 去重，也不能阻塞 seq 推进。

### 2.2 Trace 展示治理

- 保留底层原始 Trace，不能物理删除，以便排查取消、失败、重放和历史问题。
- 后端输出明确的展示语义，例如 `display_state=message|interrupted|failed|internal`、`is_persisted_message`，前端不自行拼渠道特例。
- 渠道会话跟踪页默认折叠“已取消/被合并且未持久化”的中间 Trace，不把它们计为有效聊天消息；提供“显示处理过程”入口查看。
- 失败 Trace、存在有效输出的 Trace、子智能体 Trace仍可见；不能用 `user_message_id IS NULL` 单条件过滤。

## 3. 判定规则设计

### 3.1 有效消息 Trace

满足以下任一条件时按有效消息展示：

- `user_message_id` 已关联 `channel_messages.message_id`；
- Trace 有非空输出，且不是已确认取消的中间调用；
- Trace 状态为 failed/error，需要向运维暴露；
- Trace 属于非渠道直接调用，当前链路本身不承诺写入 `channel_messages`。

### 3.2 中断/合并 Trace

优先使用显式终止原因，而不是从空字段猜测：

- SessionMessageQueue 取消旧请求时，为 Trace 写入结构化原因，如 `termination_reason=message_merged`；
- 被合并方记录 `merge_role=merged_follower`，最终处理方记录 `merge_role=merged_owner`；
- 兼容历史数据时，只有在“属于会持久化渠道消息的 source_type + `user_message_id` 为空 + output 为空 + completed/cancelled + 无 error”的组合条件下，才降级标记为 `interrupted`。

渠道能力判断应集中维护，至少覆盖 `wecom`、`wecom_kf`、`dingtalk`、`feishu`、`wecom_personal_rpa`，避免前端硬编码 RPA。

## 4. 开发阶段

### Phase 0：基线验证与根因复现

1. 为同一 RPA 会话构造短时间连续两条/三条 archive inbox 消息，记录 event_id、session_id、worker、Trace ID 和 outbox action_id。
2. 验证 RPA 路由生成的 session_id 在 `stable_id`、`conversation_id`、搜索名变化和历史绑定数据下是否稳定。
3. 验证多个 archive inbox worker/多个 Gunicorn worker 下，公共 SessionMessageQueue 的 Redis 锁、取消标记、merge buffer 和 pending 队列是否共享。
4. 对比微信客服调用参数与 RPA 参数，列出差异：msgid/event_id、附件元数据、Agent user、send_response 的 `mark_responding` 时机。

交付标准：形成可自动化复现的失败测试，确认是会话键不一致、并发时序、取消检查未生效还是出站状态过早标记中的哪一种，不凭现象直接改代码。

### Phase 1：补齐 RPA 消息合并契约

1. 修正 RPA session 路由键，确保同一真实会话稳定、跨租户/账号隔离。
2. 复用 `process_and_persist`，补齐 RPA 的 `msgid=event_id`、附件元数据和合并段 metadata 透传。
3. 确保被合并调用返回后不写 `channel_messages`、不创建 outbox；最终合并方只写一次合并 user 消息和一次 assistant 消息。
4. 校准 `mark_responding`：以真正准备创建/发送首个出站动作作为不可取消边界，不能在 Agent 尚未产生回复时提前设置。
5. 校验 pending 路径：已经开始回复后到达的新消息，在当前回复结束后继续处理且不会丢失。
6. 保持 archive inbox 的事件幂等、lease/retry 与 seq 推进逻辑独立，不把业务合并状态写成消费成功的替代条件。

### Phase 2：Trace 显式终止语义

1. 在 Agent/Trace 收尾链路增加可选的结构化终止原因和 merge role，兼容现有数据库 JSON metadata，优先避免新增数据库列。
2. 公共 SessionMessageQueue 在取消、被合并、pending 等路径准确写入语义；RPA 与微信客服复用同一规则。
3. 确保最终合并 Trace 回填最终持久化 user_message_id；中间 Trace 不伪造 message_id。
4. 对真正异常保留 error/status，不把失败误标为普通 interrupted。

### Phase 3：监控 API 与前端展示

1. `src/api/monitor.py` 统一计算 `display_state`、`is_persisted_message` 和 `is_intermediate`。
2. 会话 Trace API 返回原始条目及展示字段；是否默认折叠由显式查询参数控制，默认面向聊天视图隐藏中间处理过程。
3. `SessionTraces.vue` 的“共 N 条消息”只统计有效消息，并增加“显示处理过程（N）”；展开后给中断项标注“已被后续消息合并/旧请求已取消”。
4. 全局 Trace 列表仍以排障为主，默认不删除失败或中断记录；如增加过滤器，应允许选择全部、有效消息、中断、失败。
5. Trace 详情页始终可以通过 trace_id 打开，并展示终止原因、合并角色和关联 user_message_id。

### Phase 4：测试与真机验收

自动化测试至少覆盖：

- 同 session 两条文本在回复前到达：旧请求取消，Agent 最终收到合并文本，只创建一个出站回复。
- 同 session 三条消息跨 worker 到达：顺序稳定、无丢失、无重复落库。
- 已开始回复后新消息：进入 pending，产生下一轮回复。
- 不同联系人、不同 RPA 账号、不同租户并发：不合并。
- 文本+附件、附件+文本连续到达：附件元数据不丢失、不重复发送。
- archive event_id 重投：inbox 幂等拦截，不创建新 Trace/新 outbox。
- 合并产生的中间 Trace 被标为 interrupted，最终 Trace 关联 user_message_id。
- `user_message_id` 为空但有输出、失败、子智能体、非持久化来源：不得误隐藏。
- 历史 RPA 空输出 Trace：默认折叠但可展开、详情仍可访问。
- 前端构建、后端 import、相关单测和相邻渠道回归全部通过。

真机验收发送三组消息：

1. 快速连续补充信息，例如“去贵州”“两个人”“六天”，预期一条合并回复。
2. 等客户端开始回复后再补充，预期下一轮单独回复。
3. 连续发送文本和附件，预期 Agent 正确理解且客户端只发送一次对应附件/回复。

同时核对 archive inbox、channel_messages、obs_traces 和 RPA outbox 四条链路，确保事件数、有效消息数、Trace 展示数和出站动作数符合预期。

## 5. 预计改动范围

- `src/saas/api/wecom_personal_rpa_routes.py`
- `src/channels/wecom_personal_rpa/router.py`
- `src/channels/wecom_personal_rpa/archive/`（仅在复现证明消费并发需要调整时）
- `src/core/session_queue.py`
- `src/channels/session.py`
- `src/core/trace_collector.py` / `src/core/trace_persist.py`
- `src/api/monitor.py`
- `frontend/src/api/monitor.ts`
- `frontend/src/components/saas/SessionTraces.vue`
- `frontend/src/components/saas/TraceDetail.vue`
- 对应单元测试、集成测试与前端测试

实际开发以 Phase 0 的失败测试为准，未证明需要修改的文件不做无关改动。

## 6. 风险与回滚

- **误合并不同会话**：session key 必须包含租户、RPA 账号和稳定会话标识；缺少稳定标识时宁可不合并并记录告警。
- **回复已开始仍取消**：不可取消边界必须绑定真实出站动作创建/发送时机。
- **附件重复或丢失**：合并缓冲区按事件 ID 保存附件并去重，最终动作生成前统一读取。
- **监控误隐藏故障**：原始 Trace 不删除，失败始终显示，折叠可由用户展开。
- **Redis 故障**：维持现有降级策略并记录指标；多 worker 下无法保证合并时必须可观测告警，不能静默宣称成功。

回滚时可分别关闭“RPA 合并契约增强”和“监控默认折叠”，archive inbox 的幂等与可靠消费不回滚。

## 7. 开发流程

计划确认后按项目三智能体流程串行执行：

1. 开发智能体：先完成 Phase 0 失败测试，再做最小实现和自测。
2. 测试智能体：独立执行新测试、相邻渠道回归、后端启动检查和前端 build。
3. CodeReview 智能体：重点审查跨 worker 并发、租户隔离、取消竞态、附件幂等及监控误过滤。
4. 主控最终复核；未经用户明确要求，不提交、不推送。
