# 企业微信个人 RPA 消息合并与 Trace 治理开发计划

## 1. 依据与范围

- 需求依据：[消息合并与无效 Trace 治理方案](wecom-personal-rpa-message-merge-and-trace-plan.md)。
- 复用公共链路 `ChannelSessionManager.process_and_persist()` → `SessionMessageQueue.enqueue_and_process()`，不新增 RPA 私有队列。
- 原始 Trace 保留；后端提供统一展示语义，前端只消费语义，不按渠道或 `user_message_id IS NULL` 自行推断。
- 未经 Phase 0 失败测试证明，不调整 archive inbox 的可靠消费、lease、retry 或 seq 推进逻辑。

## 2. 实施原则

1. 严格按 Phase 0 → 4 顺序推进；每个 Phase 达到退出条件后再进入下一 Phase。
2. 每个 Phase 都先补测试，再做最小实现；RPA、微信客服和通用 Trace 行为必须回归。
3. 会话隔离键至少包含 tenant、RPA account/config 和稳定会话标识；缺失稳定标识时拒绝跨事件合并。
4. `event_id` 负责消费幂等，session queue 负责业务合并，两者不能互相替代。
5. 不新增敏感信息日志；测试与日志只记录脱敏 ID、状态和计数。

## 3. Phase 0：基线验证与根因锁定

### 任务

- 对照 RPA 与微信客服调用参数，核对 `session_id`、`msgid`、附件 metadata、Agent user、取消检查和 `mark_responding` 时机。
- 增加自动化复现：同一 RPA 会话连续两/三条消息、不同租户/账号/会话隔离、event_id 重投。
- 验证 session queue 的锁、取消标记、merge buffer 和 pending 是否为 Redis 共享状态；明确进程内状态的边界。
- 记录失败发生在哪一层：路由键、跨 worker 共享、取消检测、持久化边界或出站边界。

### 预计文件

- `tests/unit/channels/wecom_personal_rpa/test_router.py`
- `tests/unit/channels/test_session_manager_persist.py`
- `tests/unit/test_session_queue.py`
- 必要时新增 RPA 合并集成测试文件

### 退出条件

- 至少一个测试能稳定暴露真实契约缺口，或测试证明现有合并链路正确并把剩余问题收敛到 Trace 展示。
- 输出根因结论和 Phase 1 的精确改动清单，不凭生产现象修改代码。

## 4. Phase 1：补齐 RPA 合并契约

### 任务

- 修正稳定 session key，确保同会话稳定且 tenant/account/config 间隔离。
- 补齐 `msgid=event_id`、文本/附件 metadata 和合并段 metadata 透传。
- 被合并调用不得写 `channel_messages` 或创建 outbox；owner 只持久化一次 user/assistant 消息并创建一次回复动作。
- 将不可取消边界放在真正准备创建首个出站动作时；已进入回复阶段的新消息走 pending 下一轮。
- archive inbox 的事件幂等、retry、lease 和 seq 行为保持独立。

### 预计文件

- `src/channels/wecom_personal_rpa/router.py`
- `src/saas/api/wecom_personal_rpa_routes.py`
- `src/channels/session.py`
- `src/core/session_queue.py`（仅公共契约确有缺口时）
- 对应单元与集成测试

### 退出条件

- 回复前连续消息只产生一次最终持久化与出站；回复后消息稳定进入下一轮。
- 文本/附件不丢失、不重复；跨租户、账号、会话不串线；event_id 重投无新增 Trace/outbox。

## 5. Phase 2：Trace 显式终止语义

### 任务

- 在现有 Trace metadata 中增加可选 `termination_reason`、`merge_role`，避免无必要数据库列变更。
- queue 的取消、follower、owner、pending 路径写入准确语义；失败状态不得降级为 interrupted。
- owner Trace 回填真实 `user_message_id`；follower 不伪造关联消息。
- 为历史记录实现集中式兼容判定：仅对承诺持久化的渠道来源，结合空输出、无错误和终态判断 interrupted。

### 预计文件

- `src/core/trace_collector.py`
- `src/core/trace_persist.py`
- `src/core/session_queue.py`
- `src/channels/session.py`
- 对应 Trace 与 session queue 测试

### 退出条件

- 新数据优先依赖显式语义；历史兼容规则不误隐藏失败、有输出、子智能体或非持久化来源 Trace。

## 6. Phase 3：监控 API 与前端展示

### 任务

- 后端集中返回 `display_state`、`is_persisted_message`、`is_intermediate`、终止原因和合并角色。
- 会话 Trace API 保留完整数据，通过显式参数控制是否包含处理过程；trace_id 详情始终可访问。
- 会话页有效消息计数排除中间 Trace，并提供“显示处理过程（N）”开关与中断说明。
- 全局 Trace 页保留故障排查语义，不默认丢弃失败或中断记录。

### 预计文件

- `src/api/monitor.py`
- `frontend/src/api/monitor.ts`
- `frontend/src/components/saas/SessionTraces.vue`
- `frontend/src/components/saas/TraceDetail.vue`
- 后端 API 测试与前端测试

### 退出条件

- 默认聊天视图只统计有效消息；展开后能查看中间过程；失败、有输出和历史详情均可见。
- 前端不包含 RPA 专属过滤条件。

## 7. Phase 4：回归、启动安全与验收准备

### 自动化验证

- 运行新增测试、`session_queue`、`ChannelSessionManager`、RPA archive/flow、monitor Trace 相邻回归。
- 后端关键模块执行语法与 import 检查。
- 前端改动后运行测试和 `npm run build`。
- 三智能体流程：开发完成后由测试智能体独立验证，再由 Code Review 智能体审查并修复 P0/P1。

### 真机验收清单

1. 回复前快速发送“去贵州”“两个人”“六天”：只产生一条合并回复。
2. 客户端开始回复后再补充：形成下一轮回复，不撤销当前轮。
3. 连续文本和附件：Agent 输入完整且只发送一次对应回复/附件。
4. 对账 archive inbox、`channel_messages`、`obs_traces`、RPA outbox 的事件数和关联 ID。

### 退出条件

- 自动化测试、启动安全检查和前端构建全绿。
- 真机验收步骤、查询口径和回滚开关明确；无法在本地完成的真机项标记为待部署验证，不伪报完成。

## 8. 状态维护与回滚

- 2026-07-13：Phase 0-3 代码完成，Phase 4 自动化验证完成；三智能体开发、独立测试和 Code Review 已通过。
- 待部署环境完成真实 Redis Lua 竞态验证及三组企微真机验收，因此整体状态仍为 🔧 部分完成。
- 已知 P2：合并窗口内撤回带附件消息时，现有附件数组缺少 per-segment ownership，暂不能按被撤回 segment 精确裁剪；不影响连续消息合并主路径，后续单独设计。
- 开始开发后 `docs/ideas.md` 保持 🔧 部分完成，并随 Phase 更新进度。
- 全部代码与可执行验证完成后移至 `docs/ideas_finished.md`，真机依赖若未完成则继续保留在 `docs/ideas.md`。
- 合并契约增强与监控默认折叠应可分别回滚；archive inbox 幂等和可靠消费不随展示功能回滚。
- 本任务不自动提交、不 push。
