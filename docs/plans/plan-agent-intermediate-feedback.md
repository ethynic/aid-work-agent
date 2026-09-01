# Agent 用户可见中间消息（verbose）开发计划

> 状态：🔧 代码与自动化测试已全部完成（2026-08-31，Phase 0–4 各经 开发→独立测试→独立CR 三智能体流程；待用户按 [verbose-feedback-rollout-runbook.md](verbose-feedback-rollout-runbook.md) 完成真机验收与灰度后移入 ideas_finished.md）  
> 制定日期：2026-08-31  
> 审计状态：✅ 已通过三轮独立设计审查，全部阻断项已纳入计划  
> 产品决策：2026-08-31 删除 LLM 候选路径，verbose 全部由 Tool/Skill/System 确定性产生  
> 设计文档：[agent-intermediate-feedback-design.md](../system/agent-intermediate-feedback-design.md)  
> 开发要求：跨 Agent 内核、Web、渠道的非平凡改动；每个实施 Phase 均执行“开发 → 独立测试 → 独立 CodeReview”，不自动提交代码。

## 1. MVP 交付边界

本期只交付一个可靠能力：本轮被策略认定为长任务时，最多向最终用户发送一条简短中间消息；它不是技术 progress，也不是最终 response。（2026-09-01 产品决策变更：system watchdog 兜底已删除，运行超过 8 秒但无策略命中的 turn 不产生任何 verbose。）

必须满足：

- `verbose` 与 `progress/tool_*/llm_call/response` 在协议和展示上隔离。
- 每轮最多一条，1～60 字、单句、无换行、无数字 ETA；文案只来自 Skill/Tool/delegate 策略，不由 LLM 生成，也无系统兜底（2026-09-01 产品决策）。
- Skill/tool/delegate 提供确定性策略文案；无系统兜底（2026-09-01 产品决策：原「turn 8 秒后仍无用户事件时由非取消式 system watchdog 兜底」已删除）。
- Web 原地更新 assistant 占位；第三方渠道先发 verbose、最终结果仍按原链路发送。
- verbose 不产生独立 `chat_messages/channel_messages` 行，只合并进最终 assistant metadata。
- 全局默认启用（2026-09-01 产品决策，原"默认关闭"灰度策略废止），可按请求、渠道、旧微信客服配置逐级覆盖；最高优先级 `force_disabled` 可全局紧急关闭。
- 不改变现有 session queue 的 cancel/merge/pending 语义，尤其禁止因 verbose 调用 `mark_responding`。
- 企微个人 RPA 的 verbose 与 final 使用不同 delivery request id，不能互相幂等吞掉。

本期不做：多阶段多条播报、百分比/ETA、工具内部 `report_verbose`、子智能体内部事件实时透传、卡片原地更新、管理端指标页。这些均为后续增强，不得夹带进 MVP。

## 2. 成功标准

1. 短问答/短工具及任何无策略命中的 turn（无论多慢）不产生 verbose（无系统兜底，2026-09-01 产品决策）。
2. 长任务在有效 tool call 之后、`tool_start` 之前由编排层产生至多一个 policy 事件。
3. wrapper 只过滤/透传事件，不产生 verbose、不取消、不重启、不延长 Agent producer。
4. 渠道发送慢、返回 false、抛异常或超时均不阻塞最终回复。
5. 用户在 verbose 后追加消息时，行为与改造前完全一致，不丢 owner turn 的既有语义。
6. 微信客服每次咨询最多消耗一个额外回复配额，并为 final 保留配额。
7. RPA outbox 中 verbose 和 final 是两个可独立去重的投递。
8. verbose 文案、Skill feedback metadata 和事件均不进入同一轮后续或下一轮 LLM messages。
9. 关闭开关后，现有相关回归测试和可观察行为不变。

## 3. 实施顺序

```text
Phase 0 契约与基线
  → Phase 1 内核策略和事件（watchdog 已于 2026-09-01 决策删除）
    → Phase 2 Web 消费与持久化
    → Phase 3 渠道 dispatcher、RPA 幂等与旧配置迁移
      → Phase 4 首批长任务接入、全链路验收和灰度
```

Phase 2、3 在 Phase 1 契约冻结后可分别实施，但 Phase 4 必须等待两端完成。任何 Phase 若改变事件字段、队列语义或持久化位置，先更新设计文档再继续。

## 4. Phase 0：契约与基线（0.5 人天）

### 工作项

- 固化 `verbose` schema：`type/eventId/data/source/timestamp`；source 仅 `policy|system`。
- 固化反馈状态机：`PENDING → EMITTED → CLOSED`；状态作用域是整个 owner 生命周期，所有 `_processor` 重跑 attempt 复用。
- 记录 Agent 事件、sync callback、session queue、channel persistence、Web SSE、微信客服 waiting indicator、RPA outbox 的测试基线。
- 列出当前工作区无关改动，实施期间只触碰本功能文件。

### 门禁

- 契约测试先失败且能表达业务意图；生产行为尚未改变。
- 两个审计 P0 各有一条可复现的回归测试草案：追加消息不得导致 final 消失；RPA verbose/final 不得共用幂等键。

## 5. Phase 1：统一反馈内核（2～2.5 人天）

### 代码范围

新增 `src/core/verbose_feedback.py`：

- `LongRunningFeedback`：可信 start message 与匹配结果。
- `VerboseFeedbackConfig`：本轮冻结配置，默认 `enabled=true`（2026-09-01 产品决策）、max 1，并支持最高优先级 `force_disabled`（`initial_delay_seconds` 已随 watchdog 删除下线）。
- `VerboseFeedbackState`：本轮独立状态，不得放在共享 Agent 实例字段。
- `resolve_feedback_policy(tool_name, args, agent)`：唯一策略解析入口。
- `validate_feedback_text(text)`：内置 Skill、租户自定义 Skill 和 tool 模板统一校验，只接受 1～60 字单句、无换行、无数字 ETA/路径/命令/敏感键；失败返回空，不做局部“清洗”，改用 fallback 文案。
- `iter_with_verbose_feedback(...)`：事件过滤/观测包装器（原以绝对 deadline 实现 watchdog；2026-09-01 产品决策删除兜底后仅保留透传、未注册 verbose 拦截、终态置位与 producer 生命周期管理）。
- observer：记录是否触发、source、长度、time-to-first、失败原因，不记录正文。

修改：

- `src/core/agent_events.py`：增加最小 `make_verbose_event`。
- `src/core/agent.py`：有效 tool calls 规范化后、`tool_start` 前解析策略并直接产生 policy 事件；不增加 verbose 相关 system prompt，不读取或修改 tool-call assistant content。为 Web 暴露显式 feedback 包装入口；`process_message_sync` 仅在有用户 callback 时启用包装器。
- Skill loader/registry：`metadata.user_feedback` 单独保存在编排侧，不渲染进 Skill 正文或 Agent prompt。
- Tool schema 生成：不得暴露 `get_user_feedback` 或其返回文案。
- `src/config/settings.py`、`configs/config.yaml`：增加有类型的配置模型，同时保留现有 `agent.reply_style`。

策略解析顺序必须固定：

1. `skill_execute`：按 skill registry 的已审核 metadata 动态解析。
2. `delegate`：使用固定的父级开始文案；不透传子 Agent 内部事件。
3. 普通 tool：仅调用可选 `get_user_feedback(args)`；返回空即短任务。
4. 其他：不产生 tool policy 业务提示。

无系统兜底（2026-09-01 产品决策：watchdog 已删除）。渠道 owner 必须从 `process_and_persist` 显式把同一个 `VerboseFeedbackState` 传入每次 `process_message_sync` 重跑，禁止在 sync 方法内部偷偷新建状态。

### 必测场景

- 长任务 policy 事件在 `tool_start` 前出现；无策略命中的 turn（无论多慢）不产生任何 verbose。
- 空/无效 tool call 不触发。
- Skill/Tool 模板含换行、超长、路径、JSON、命令、敏感信息或数字 ETA 时整体降级为 system fallback。
- 并行 tool calls 仍最多一条。
- 8 秒是绝对 deadline；期间连续技术事件不推迟提示。
- 迟到的未注册 verbose 被 wrapper 拦截，每轮仍只发一条。
- 正常、异常、取消均无残留 producer task。
- 两个并发 session 的状态完全隔离。
- 原始 generator、Desktop/internal 调用方不被自动注入 verbose。
- 捕获同一轮后续及下一轮 provider 请求，断言 messages 中不存在 verbose 文案；Skill prompt 和 tool schema 中也不存在 feedback metadata。

### 门禁

- 新增和相邻后端单测全绿；Agent import 成功。
- 配置默认启用（2026-09-01 决策），YAML 与模型默认值一致。
- Trace/observer 能观测 policy verbose 事件。

## 6. Phase 2：Web 展示和 metadata（1～1.5 人天）

### 后端

- `/api/chat/stream` 使用 feedback 包装入口并透传 `verbose` SSE。
- `verbose_events` 与 `progress_events` 分开收集。
- final 前将 `verboseMessages` 合并进 assistant metadata，不覆盖 downloadable files 或调用方 metadata。
- browser continuation 只允许已有 `verbose` 事件原样通过；MVP 不在 continuation 内另开 wrapper。

### 前端

- `types/index.ts` 增加 `VerboseMessage` 和事件 union。
- `api/agent.ts` 增加 `onVerbose`。
- `useAgent.ts` 以 `eventId` 去重，把最新文案写入当前 session 的 assistant 占位；response/error/cancel 后关闭 live 状态。
- `MessageItem.vue` 在原占位区显示，`aria-live="polite"`，最多两行；不新增聊天气泡，不混入 debug progress。

### 必测与门禁

- SSE 解析、同 eventId 去重、response 后隐藏、历史 metadata 恢复。
- 多会话 A/B 隔离，切换会话不串文案。
- browser continuation 不重复启动 verbose wrapper。
- metadata 合并保留文件、图片及已有键。
- 前端单测与 build 通过；10 秒 mock 工具在约 8 秒提示，随后正常展示 final。

## 7. Phase 3：第三方渠道可靠投递（2～3 人天）

### dispatcher 与持久化

在 `src/channels/session.py` 增加 owner 生命周期共享的 `ChannelVerboseDispatcher`：

- dispatcher 惰性启动；merged follower 不进入 owner processor，不创建 task。cancel/merge 重跑复用已有 state/dispatcher，已 emitted/sent 状态不得重置。
- callback 只做 `put_nowait`，不得直接 await adapter；队列容量 1，重复事件丢弃。
- dispatcher 串行调用 adapter 的低优先级 `send_status_message(reserve_for_final=1)`，单次发送 5 秒超时，false/异常只记指标；不得直接复用普通 `send_message`。
- Redis 限流器用 Lua/事务原子完成“校验预留 + 扣减”；进程内 deque 在无 `await` 的临界区完成。禁止先读余额再普通发送。
- final 持久化前执行 `close_and_drain()`，冻结 verbose metadata；随后持久化，再走既有 final 发送链路。drain 超时必须 cancel 并 await 实际发送 task 后才返回，不能留下晚到发送。
- 取消/转人工路径显式关闭 dispatcher，不残留 task。
- `process_and_persist` 增加可选 sender/context，但不调用 `session_queue.mark_responding`。
- metadata 合并规则：系统字段优先；`downloadableFiles` 按 `file_id` 去重；`verboseMessages` 按 `eventId` 去重；其他调用方字段保留。

### 渠道与配置

- 企微、微信客服、钉钉、飞书使用各自 adapter 与 reply target，但必须新增原子预留 final 内部限流额度的 status 发送入口；额度只剩 1 时抑制 verbose，final 仍可发送。不支持安全预留的 adapter 默认抑制 status。
- 微信客服新增 owner 级 `WeComKfReplyBudget(total=5)` 并由 verbose/final 共享：verbose 成功扣 1；final 正文优先且至少保留 1 次；长正文优先合成长图、失败则单条截断纯文本；剩余额度再发图片/文件，超预算资产记录 `suppressed_reply_budget`。无法保证 final 正文时不发 verbose。
- 企微个人 RPA 每次投递都显式设置唯一 request id：`{event_id}:verbose:1` 与 `{event_id}:final`，再调用 `set_reply_context`；不得只依赖 `UnifiedResponse.message_id`。
- 配置优先级固定为：全局 `force_disabled=true` > 请求级覆盖 > 渠道 `verbose_feedback` > 旧 `wecom_kf.waiting_indicator` > 全局配置 > 代码默认。
- 通过 ChannelFactory/集中入口向 adapter 注入非敏感配置；保留旧 waiting indicator 读兼容，移除 route 外层旧 watchdog，避免双发。

### P0/P1 回归测试

1. verbose 后用户追加消息，不因状态切换导致 owner final 被 pending turn 覆盖。
2. 全流程断言未调用 `mark_responding`。
3. RPA verbose/final 产生不同 outbox/request id，重试各自幂等；同时验证 outbox 和直推路径均保持 verbose → final 顺序。
4. callback 返回速度不受慢 adapter 影响；dispatcher 超时/false/异常不影响 final。
5. `close_and_drain` 后 metadata 冻结，晚到事件不能与 DB batch 竞态。
6. merged follower 不创建 dispatcher、不发送 verbose。
7. `_processor` cancel/merge 重跑复用 owner 状态，累计仍最多提示一次。
8. 每轮、每渠道最多一条；内部限流只剩一个额度时 verbose 被抑制、final 成功。
9. 转人工、取消后不再发送；无独立 channel message 行。
10. 旧 waiting indicator 配置只触发新机制一次。
11. 群聊 reply target/conversation type 与 final 一致。
12. 微信客服 verbose/final 共享 5 次 budget，final 正文优先，覆盖长图失败、正文截断、多图片、多文件和预算耗尽组合；超预算资产可观测地抑制。
13. `force_disabled=true` 覆盖请求、渠道与旧 waiting indicator。

### 门禁

- session queue、channel persistence、RPA outbox、微信客服旧测试及各 adapter 定向测试全绿。
- 测试租户实测普通渠道、微信客服、企微个人 RPA 各一条长任务。

## 8. Phase 4：首批长任务、全链路验收与灰度（1.5～2 人天）

### 首批接入

- `travel-quote` 与 `excel-to-template` skill metadata 增加审核过的 start message。
- `BaseTool.get_user_feedback(args)` 默认返回空；Excel 仅模板填充/生成路径返回文案，list/read 等短操作返回空。
- delegate 只提供父层固定开始文案；子智能体内部 verbose 透传留到增强期。

### 全链路验证

- travel quote 与 Excel 模板分别验证：提示先到、文件/final 后到，最终 metadata 完整。
- 故障注入：adapter 慢 10 秒、false、异常；SSE 断开；Agent 在 8 秒边界完成；用户追加消息；转人工与取消。
- 并发测试至少覆盖 100 个长 turn，完成后 asyncio task 数恢复基线，final 失败率不因 verbose 上升。
- 验收矩阵覆盖 Web、企微、微信客服、钉钉、飞书、企微个人 RPA；关键 P0 在真实或集成环境复测。

### 灰度与回滚

1. 以 `enabled=false` 部署，验证 import、build、配置加载和旧逻辑无回归。（2026-09-01 产品决策后全局默认 `enabled=true`，此步骤改为"部署后回归验证"，见 runbook §1。）
2. 测试租户开启，观察 time-to-first、verbose 送达/失败、final 成功率、每轮消息数、dispatcher 超时。
3. Web 先灰度，再分渠道开启；微信客服重点观察回复配额。
4. 紧急回滚设置全局 `force_disabled=true`；它覆盖请求、渠道和旧 waiting indicator。该开关在 owner 创建时及 dispatcher 真正发送前各读取一次，因此也抑制尚未发送的在途提示；若部署环境不支持热加载，变更后必须重启服务。旧逻辑不可恢复成第二套 watchdog。

### 门禁

- 三智能体流程的开发、自测、独立测试、独立 CR 均通过且问题关闭。
- 自动化、构建、并发和真机矩阵有可追溯记录。
- #64 完成后从 `docs/ideas.md` 移至 `docs/ideas_finished.md`。

## 9. 预计工作量

| Phase | 估算 |
|---|---:|
| 0 契约与基线 | 0.5 人天 |
| 1 内核策略 | 2～2.5 人天 |
| 2 Web | 1～1.5 人天 |
| 3 渠道与 RPA | 2～3 人天 |
| 4 接入、验收、灰度 | 1.5～2 人天 |
| MVP 合计 | **7～9.5 人天** |

若同时加入多阶段播报、子智能体实时透传、in-process `report_verbose` 等增强项，完整范围预计再增加 4～7 人天；必须另立计划，不能扩大本期验收边界。

## 10. 进度维护

- 开始实施时将 `docs/ideas.md` #64 标为 🔧 部分完成。
- 每完成一个 Phase，在本文记录实际改动、验证命令、通过数和遗留项。
- 任何跳过的测试必须显式写明原因，不能以“已通过”概括。
- 全部完成且真机验收通过后，按项目规范更新 `docs/ideas_finished.md`。

## 11. 实施进度记录

### 11.0 Phase 1：统一反馈内核（2026-08-31 完成）

#### 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/core/verbose_feedback.py` | 新增（646 行） | 统一反馈内核：`LongRunningFeedback` / `VerboseFeedbackConfig`（frozen，`effective_enabled`）/ `VerboseFeedbackState`（PENDING→EMITTED→CLOSED）/ `validate_feedback_text`（白名单校验，失败整体拒绝）/ `resolve_feedback_policy`（唯一解析入口）/ `build_policy_verbose_event`（policy 注入逻辑，agent.py 只接线）/ `prepare_turn_feedback`（sync wrapper 门控）/ `iter_with_verbose_feedback`（非取消式 watchdog 包装器，绝对 deadline + producer 生命周期管理）/ `VerboseFeedbackObserver`（可观测钩子，不记录正文） |
| `src/core/agent_events.py` | 修改（+40 行） | 新增 `make_verbose_event(event_id, data, source)`：恰好五字段（type/eventId/data/source/timestamp），source 仅 policy\|system，文案校验最后一道防线；复用 `make_event` 毫秒时间戳 |
| `src/core/agent.py` | 修改（+59 行，净） | ① `process_message`/`_process_message_impl` 增加可选 `verbose_config/state/observer`（默认 None 行为不变）；② valid_tool_calls 规范化后、首个 tool_start 前 4 行接线 yield policy 事件（逻辑在 verbose_feedback 模块）；③ 新增显式包装入口 `process_message_with_feedback(surface/config/state/observer/**kwargs)`；④ `process_message_sync` 增加 `feedback_state`（外部传入必须复用）等参数，仅 progress_callback 非 None 且配置生效时启用 watchdog |
| `src/core/skill_registry.py` | 修改（+24 行） | 新增 `get_user_feedback(name)` 访问器：读 `Skill.metadata.user_feedback`（编排侧专用）；已确认 metadata 既有路径（get_descriptions/get_skill_content）均不进 prompt，loader 无需改动 |
| `src/tools/base.py` | 修改（+20 行） | `BaseTool.get_user_feedback(tool_args)` 默认返回 None（服务端钩子）；`to_tool_definition` 只生成 name/description/input_schema，钩子不进 tool schema |
| `src/config/settings.py` | 修改（+34 行） | 新增有类型 `AgentConfig`（`reply_style` + `AgentVerboseFeedbackConfig`）并声明 `settings.agent` 字段；默认全部关闭；与既有 `getattr(settings, 'agent', None).reply_style` 动态访问完全兼容（全仓唯一消费点 `_resolve_reply_style`） |
| `configs/config.yaml` | 修改（+10 行） | `agent.verbose_feedback` 块显式全 false，与模型默认值逐字段一致 |
| `tests/unit/test_verbose_feedback.py` | 新增（57 条用例） | Phase 1 必测场景全覆盖（见下） |
| `tests/unit/test_verbose_feedback_contract.py` | 修改 | 按计划激活 P0-1：移除 `@pytest.mark.skip`（CR 裁决其仅依赖 Phase 1 设施），同步修正模块 docstring 与 fixture docstring 中"Phase 3 激活"的过时表述（仅注释，未动任何断言/签名）；P0-2 保持 skip 至 Phase 3 |
| `docs/plans/plan-agent-intermediate-feedback.md` | 修改 | 本进度记录 |

#### 关键设计决策

1. **包装器组合方式**：`iter_with_verbose_feedback` 内部启动唯一 producer task 消费原始事件写 `asyncio.Queue`；watchdog 用从执行开始计的绝对 deadline（`asyncio.wait_for(queue.get(), timeout=剩余时间)`），技术事件不重置计时；timeout 只 try_emit 一条 `source="system"` 的 fallback 提示，绝不取消 producer；finally 统一 cancel+await producer（正常/异常/取消三路径均无遗留 task，有 task 计数断言）。额外防御：透传的 verbose 事件若非 `state.event` 本体（迟到重复/伪造）一律丢弃，"每轮最多一条"不依赖 producer 自觉。
2. **policy 注入的降级语义**：`resolve_feedback_policy` 对"long_running=true 但模板违规"返回 `LongRunningFeedback(start_message="")`（仍算 policy 命中），由 `build_policy_verbose_event` 整体替换为 `fallback_message`（source 保持 "policy"——设计 §4 产品决策中 "system" 来源专属于 watchdog）。理由：契约冻结 `resolve_feedback_policy(tool_name, tool_args, agent)` 三参签名无法传入 fallback 文案；且必测场景要求违规模板"整体降级 system fallback"（即替换为兜底文案）而非不发。
3. **eventId 生成**：`new_verbose_event_id()` = `verbose_` + uuid4.hex，policy（agent 编排层）与 watchdog（wrapper）各自调用，本轮唯一由 state 单点消费保证。
4. **配置模型接入**：`Settings` 声明 `agent: AgentConfig`（含 `reply_style` 与 `AgentVerboseFeedbackConfig` 子模型），YAML 中 `agent` 节由 extra="allow" 的 `_AttrDict` 动态挂载转为 Pydantic 强类型；`create_settings` 的动态挂载循环自动跳过已声明字段，`getattr(settings, 'agent', None)` / `getattr(agent_cfg, 'reply_style', None)` 两条既有访问路径行为不变（grep 全仓仅 `_resolve_reply_style` 一处消费）。
5. **agent.py 结构守卫**：`test_agent_py_line_count_frozen` 冻结 agent.py ≤3977 行（白名单外不可修改），因此 verbose 逻辑全部落在 `verbose_feedback.py`，agent.py 仅保留签名/参数透传、4 行注入接线、薄包装入口与 sync 门控调用（净增恰 59 行，3997→3977 达标）。
6. **observer 触发点**：按设计 §12，emitted 回调统一由 wrapper 触发（policy 事件经 queue 透传时记录 time-to-first；watchdog 直接记录）；policy 竞态失败由注入点记录 `policy_race_lost` 抑制原因。

#### 必测场景覆盖（tests/unit/test_verbose_feedback.py，57 条全绿）

- policy 事件在首个 tool_start 前（impl 级驱动）✅；短工具无 policy、慢 turn 有 system watchdog ✅
- 空/无效 tool call（空 name / 非法 JSON 参数）不触发 ✅
- Skill 模板 10 类违规（换行/超长/路径/JSON/命令/敏感键/数字 ETA/代码块/HTML/百分比）参数化：整体降级 fallback、原文不出现在事件流 ✅
- 并行 tool calls 取第一个长任务策略、仅一条、不拼接 ✅；同一 turn 第二轮再命中仍一条（状态机拦截）✅
- 绝对 deadline（连续 progress 不推迟，0.2s 阈值 0.15~0.4s 内触发）✅；快速 turn 无 watchdog ✅
- watchdog/policy 双向竞态只发一条 ✅；迟到的未注册 verbose 被 wrapper 丢弃 ✅
- 正常/异常/取消均无残留 task（asyncio.all_tasks 计数）✅；producer 异常原样传播 ✅
- 两个并发 session 状态完全隔离 ✅；配置关闭时 wrapper 纯透传 ✅；非法 surface 拒绝 ✅
- 原始 generator（不经包装入口）不被注入 verbose ✅；sync 无 callback（scheduler）不受影响 ✅；sync 传入 state 被复用（state.event is 事件本体）✅
- mock LLM 捕获两轮 provider 请求：system_prompt/messages/memory 均无 verbose 文案与 "user_feedback" ✅；Skill 正文/描述不渲染 metadata ✅；BaseTool 默认钩子 None 且 tool schema 无 get_user_feedback ✅
- 配置：settings.agent 强类型、默认关闭、reply_style 兼容、default_feedback_config 与 YAML 一致、force_disabled 压过 enabled ✅

#### 自测结果（2026-08-31）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/test_verbose_feedback_contract.py tests/unit/test_verbose_feedback.py -q` | **92 passed, 1 skipped**（契约 35 passed（含激活的 P0-1）+ 新增 57 passed；1 skipped = P0-2 待 Phase 3） |
| `python -m pytest tests/unit/test_agent_events.py tests/unit/test_session_queue.py tests/unit/test_agent_reorder_messages.py tests/unit/test_agent_record_isolation.py tests/unit/test_agent_image_event_loop.py -q` | **68 passed**（Phase 0 基线 42+26 全部保持绿） |
| `python -m pytest tests/unit/test_agent_events.py ... test_agent_structure_guard.py test_agent_tool_result_echo_guard.py test_tool_result_truncation.py test_build_messages.py -q`（agent 内核 10 文件） | **123 passed**（含结构守卫 3977 行达标） |
| `python -m pytest tests/unit/test_config.py tests/unit/test_channel_config_db.py tests/unit/test_channel_factory.py -q` | **34 passed**（配置回归） |
| `python -m pytest tests/unit -q` | **66 failed, 5150 passed, 15 skipped**；66 个失败经 pristine HEAD worktree（`git worktree add` + 同款 `.env`）全量基线对比**逐一相同（0 新增）**，均为环境性/在途既有失败（无 LLM key 的 tools/video_gen/skills LLM 用例、tenant storage 路径、message_recall、kf QR data_url、2 个 Postgres 等），不属本功能文件范围 |
| `python -c "import src.main"` | **OK** |

#### Phase 1 遗留项

1. P0-2（RPA verbose/final 幂等键）保持 skip，Phase 3 激活。
2. 渠道 `process_and_persist` 显式传递 owner 级 `VerboseFeedbackState`、`ChannelVerboseDispatcher` 与 `send_status_message` 原子预留均为 Phase 3 范围；Phase 1 渠道 sync 路径已具备 watchdog 能力（全局开关默认关闭）。
3. Web `/api/chat/stream` 切换到 `process_message_with_feedback` 及前端展示为 Phase 2 范围。
4. 具体业务工具/Skill 的文案接入（travel-quote / excel `get_user_feedback` 返回值与 SKILL.md metadata）按计划属 Phase 4。

### 11.1 Phase 0：契约与基线（2026-08-31 完成）

#### 测试基线（回归红线：Phase 1–4 不得使下列基线变红）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/test_agent_events.py tests/unit/test_session_queue.py -q` | **42 passed**（5 warnings，约 16s） |
| `python -m pytest tests/unit/channels/ -q` | **3 failed, 863 passed, 7 skipped**（约 16s） |
| `python -m pytest tests/unit/test_agent_events.py tests/unit/test_agent_image_event_loop.py tests/unit/test_agent_local_tool_registration.py tests/unit/test_agent_record_isolation.py tests/unit/test_agent_reorder_messages.py tests/unit/test_agent_request_context.py tests/unit/test_agent_structure_guard.py tests/unit/test_agent_tool_result_echo_guard.py -q` | **87 passed**（约 2s） |

说明：`tests/unit/core/` 目录不存在（已用 `ls` 探明 `tests/` 结构），第三条基线改用 `tests/unit/` 根目录下 8 个 agent 内核相关测试文件（`test_agent_*.py`，不含 desktop/d1 系列）。

channels 目录 3 个既有失败（环境性基线问题，与本功能无关，不在本功能修复范围，Phase 1–4 只需不新增失败）：

- `tests/unit/channels/test_kf_account_referral.py::TestKfAccountUtils::test_build_qr_data_url`
- `tests/unit/channels/wecom_personal_rpa/archive/test_fetcher.py::test_duplicate_event_id_enqueue_uses_database_unique_constraint`
- `tests/unit/channels/wecom_personal_rpa/test_db_outbox_results.py::test_postgres_init_and_incremental_migrations_keep_outbox_fields_in_sync`

另：channels 运行结束时有无害的 `multiprocessing Pool.__del__` 退出噪声（`AttributeError: 'NoneType' object has no attribute 'dumps'`），不影响结果统计。

#### 工作区无关改动清单（`git status --porcelain`，2026-08-31 快照）

快照时点：commit `0246e814`（recruiting 弹层自愈）与 `f730c531`（BOSS 租户交付）已由并行工作流提交落地；下列文件属其他在途工作，后续 Phase 一律不得触碰（其中本功能自己的两个文件已单独列于 11.1 改动清单）：

```text
 M docs/ideas.md
 M docs/system/agent-runtime-safety-hardening-design.md
?? .zcode/plans/plan-sess_1092253d-5ddd-45c2-891b-95a35e128a46.md
?? .zcode/plans/plan-sess_2731e274-ab25-442d-8e5f-d569b6e95f34.md
?? .zcode/plans/plan-sess_910c3dfa-cdac-4d66-b135-39558b99cd7a.md
?? clients/wecom-cli/
?? docs/plans/plan-agent-intermediate-feedback.md
?? docs/plans/plan-agent-runtime-security-debt-closure.md
?? docs/plans/recruiting/boss-wecom-recruiting-service-rollout-plan.md
?? docs/research/wecom-cli/
?? docs/solutions/
?? docs/system/agent-intermediate-feedback-design.md
?? docs/system/agent-runtime-security-debt-closure-design.md
?? tests/unit/test_verbose_feedback_contract.py
```

#### Phase 0 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `tests/unit/test_verbose_feedback_contract.py` | 新增 | 4 组契约（事件 schema / 反馈状态机 / 配置 / 文本校验）+ 2 个审计 P0 回归草案。两个 P0 草案选择放同一文件：Phase 0 白名单只允许一个新测试文件，契约与草案共用 fixture 与同一条门禁命令 |
| `docs/plans/plan-agent-intermediate-feedback.md` | 新增本章 | 实施进度记录 |

`git diff --stat -- src/ configs/` 为空，生产行为未变。

#### 自测结果（2026-08-31 CodeReview 修订后，覆盖原开发自测数字）

- `python -m pytest tests/unit/test_verbose_feedback_contract.py -q` → **34 failed, 2 skipped, 0 error**。
- 失败原因分布（`--tb=line` 逐条核对）：5 × `ImportError: cannot import name 'make_verbose_event' from 'src.core.agent_events'`（schema 契约组）+ 29 × `ModuleNotFoundError: No module named 'src.core.verbose_feedback'`（状态机 7 + 配置 4 + 文本校验 18，含参数化 16 例）。全部为”实现尚不存在”，无测试自身 bug。
- 2 个 skipped 即 P0-1 / P0-2 回归草案，`reason` 注明激活条件与缺失物，断言逻辑已写完整。
- 门禁达成：契约测试先失败且能表达业务意图；生产行为尚未改变。

#### CodeReview 修订记录（2026-08-31，CR 智能体）

CR 修复 6 项 P1，基线由 29 failed + 2 skipped 变更为 **34 failed + 2 skipped**（新增 5 条红测全部因实现未存在而失败）：

1. 重写 `test_event_id_unique_within_turn` → `test_event_id_echoed_verbatim`：原用例传入不同 id 断言不同 id，属恒真断言（该性质已被 `test_core_fields_and_types` 的 echo 断言覆盖）；现冻结真实契约——工厂原样透传 eventId，「本轮唯一」由调用方生成（模块 docstring 同步补记）。
2. schema 补「恰好五字段」断言，冻结设计 §4 最小事件结构，防实现夹带 phase/etaSeconds 等扩展字段。
3. 状态机新增 `test_pending_with_response_started_rejects_emit`：原 `test_emit_after_response_started_rejected` 的第二次 emit 本就会被 event 非空拦下，response_started 守卫从未被独立验证；补 PENDING + response_started 竞态窗口用例（设计 §14.5）。
4. 文本校验补齐设计 §6.1 已冻结但未断言的拒绝类别：多句、代码块、HTML 标签、百分比（参数化 12→16 例），并补 1 字下边界合法用例；工厂层同步补多句拒绝与 1 字边界。
5. 配置契约补 `delivery_timeout_seconds == 5` 与 `fallback_message` 默认文案（设计 §11/§6.3，watchdog 兜底文案属用户可见契约，docstring 字段清单同步补记）。
6. P0-2 草案补 `monkeypatch.setenv(“RPA_SECRET_KEY”, ...)`：`deliver_actions → _build_reply_digests → _hmac_key()` 在 env 缺失时抛 RuntimeError 且不被捕获，Phase 3 移除 skip 后将以 error 而非断言失败收场（对齐 `test_action_client.py` 既有做法）。

CR 报告未修项（P2）：P0-1 草案 skip reason 声称依赖 Phase 3「owner 级状态传递链路」，但该用例在测试内自建 owner 级 state 并以闭包传入 processor，实际仅依赖 Phase 1，可在 Phase 1 落地后提前激活；P0-1 时序余量（B 于 0.6s 到达 vs owner 最早 1.2s 结束）在极慢调度下有理论抖动空间，Phase 3 激活时可放宽。

#### Phase 0 遗留项

1. 契约测试当前为红（TDD 门禁），Phase 1 落地 `src/core/verbose_feedback.py` 与 `make_verbose_event` 后转绿；期间不得为使其通过添加生产 stub。
2. 契约 API 签名（`make_verbose_event(event_id, data, source)`、`VerboseFeedbackState.try_emit/mark_response_started/close`、`VerboseFeedbackConfig` 字段与 `effective_enabled`、`validate_feedback_text`）已在测试文件模块 docstring 中冻结；Phase 1 实现若签名有出入，必须先修改契约测试并评审，不得静默偏离。
3. P0-1 / P0-2 草案以 `@pytest.mark.skip` 挂起，分别由 Phase 1/3 落地后移除 skip 激活（skip reason 已写明缺失物）。
4. `docs/ideas.md` #64 已由主控者在 Phase 0 期间更新为 🔧 部分完成并补充实施状态（2026-08-31，主控者执行，先于本条原拟的 Phase 1 计划）。

### 11.2 Phase 2：Web 展示和 metadata（2026-08-31 完成）

#### 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/main.py` | 修改（净 +70 行） | ① 新增模块级 `WEB_VERBOSE_DELIVERY = "streamed"` 冻结常量、`_resolve_web_verbose_config(override=None)`（默认读全局配置；函数级 override 参数供测试注入，不新增 API 字段）与 `_build_verbose_metadata_entries()`（按 eventId 去重保留首条，只保留协议五字段 + delivery）；② `event_generator` 改用 `agent.process_message_with_feedback(surface="web", config=...)` 包装入口，`verbose_events` 与 `progress_events` 分开收集（verbose 不进 progressMessages / record_service / debug 详情）；③ final 前 `verboseMessages` 合并进 assistant metadata，不覆盖既有键，本轮无 verbose 时不写该键；④ `_continue_browser_agent` 的 continuation stream 白名单增加 `"verbose"`（只透传原生事件），路径仍走 `continue_tool_call`（裸 `process_message`），不新开 wrapper/watchdog |
| `frontend/web/types/index.ts` | 修改（+9 行） | 新增 `VerboseMessage` 接口（eventId/data/source/timestamp）与 `MessageStreamEvent` union 的 `type:"verbose"` 成员 |
| `frontend/web/api/agent.ts` | 修改（+20 行） | `connect`/`parseSSELine` 新增 `onVerbose?: (message: VerboseMessage) => void` 回调（位于 `onBrowserHumanRequired` 之后、`subagent` 之前），`case "verbose"` 解析为协议四字段回调 |
| `frontend/web/composables/useAgent.ts` | 修改（+27 行） | `SessionStreamState` 新增 per-session `liveVerbose: Ref<VerboseMessage | null>` + 模块级 computed 代理并导出；`onVerbose` 按 eventId 覆盖写当前 session（后端保证每轮一条，前端去重是防御）；`sendMessage` 开始时重置；`onResponse` / `onError` / `onComplete` / `onBrowserHumanRequired` / catch 五条终止路径清除 |
| `frontend/web/components/MessageItem.vue` | 修改（+19 行） | 占位区文案改为 `liveHintText`（当前会话 live verbose 有值时替换「对方正在输入中...」，同一占位区不新增气泡）；提示元素加 `aria-live="polite"`；`.verbose-hint` 两行 CSS 截断（`-webkit-line-clamp: 2`）；历史 metadata 的 verboseMessages 不在已完成消息下展示（前端完全不消费该字段，默认不展示自动满足） |
| `tests/unit/test_verbose_feedback_web_stream.py` | 新增（8 条用例） | 直接调用 `chat_stream` 协程 + stub sse_manager/session_queue/record/MessageDB/agent_router；fake agent 用真实 `iter_with_verbose_feedback` 包装 scripted 事件流。覆盖：慢 turn watchdog system 帧透传（0.2s 短 delay）、policy verbose 透传 + metadata 三键合并 + delivery="streamed"、快 turn 无 verboseMessages 键、enabled=false 零行为变化（帧序列逐一断言）、合并纯函数去重/形状/防御、continuation 透传 verbose 且 `process_message_with_feedback` 零调用 |
| `tests/unit/test_verbose_feedback_contract.py` | 修改（+3 条用例） | 新增 `TestWebVerboseMetadataDeliveryContract`：设计 §10「Web 端 delivery 命名由契约测试冻结」→ 冻结 `WEB_VERBOSE_DELIVERY == "streamed"`（而非 "displayed"）、条目恰好五字段 + delivery、同 eventId 去重保留首条、空输入返回空列表（无 verbose 时 metadata 无键） |
| `frontend/web/__tests__/api/agent.verbose.test.ts` | 新增（3 条） | SSEManager 解析 verbose 帧 → onVerbose 四字段；与 progress 隔离；跨 chunk 边界；未注册回调时安全忽略 |
| `frontend/web/__tests__/composables/useAgentVerbose.test.ts` | 新增（4 条） | 同 eventId 覆盖去重且不进执行详情；response/error/complete 后清除；A/B 会话隔离不串文案；fake timers 表达「约 8 秒提示 → 10 秒 final → 新一轮重置」时间线 |
| `frontend/web/__tests__/components/MessageItem.verbose.test.ts` | 新增（4 条） | 占位替换（同一 `.message-ai-content`、单 aria-live 元素）、无 verbose 回退默认文案、response 后占位隐藏、历史消息不展示 verbose |
| `docs/plans/plan-agent-intermediate-feedback.md` | 修改 | 本进度记录 |

#### 关键设计决策

1. **metadata 合并实现**：抽纯函数 `_build_verbose_metadata_entries(verbose_events)`（去重 + 字段裁剪 + delivery 注入），`event_generator` 在既有 `progressMessages` / `downloadableFiles` 组装完成后追加 `verboseMessages` 键，且仅在该键不存在时写入——「不覆盖已有键」由结构保证（该 metadata 为本函数新建 dict，无调用方预置 verboseMessages 的可能）；无 verbose 时整键缺席（选择「键不存在」而非空数组，避免历史消息结构变化）。
2. **delivery 冻结为 "streamed"**：Web 无法可靠确认 DOM 实际渲染，记录「已随 SSE 流式下发」事实；常量 `WEB_VERBOSE_DELIVERY` 由契约测试冻结（设计 §10 授权），后续若改 "displayed" 必须先改契约并评审。
3. **continuation 处理**：`_continue_browser_agent` 维持 `continue_tool_call`（裸 `process_message`）不变——天然无 wrapper/watchdog；仅把 `"verbose"` 加入 continuation stream 事件白名单，使「万一出现的原生 verbose」可透传到前端（今日该路径不可能产生 verbose，故为纯防御性透传，零行为变化）。测试以「`process_message_with_feedback` 零调用 + verbose 事件进 store」行为级断言。
4. **配置读取**：`_resolve_web_verbose_config()` 默认走 Phase 1 的 `default_feedback_config()`（读 `settings.agent.verbose_feedback`，读取失败安全回落关闭）；`force_disabled` 优先级由内核 `effective_enabled` 保证，main.py 不二次判定。请求体无承载字段 → 不新增 API 字段，保留函数级 `override` 参数供测试/内部调用注入。
5. **前端状态结构**：live verbose 是 per-session 单槽 `Ref<VerboseMessage | null>`（非数组）——「最多一条 + 后来者覆盖」使去重天然成立；由 `useAgent()` 导出 computed 代理，`MessageItem` 直接消费当前查看会话的槽位（ChatContainer 只渲染当前会话消息，语义吻合，且无需改白名单外的 ChatContainer/MessageList）。
6. **测试注入方式**：后端不走 TestClient，直接 await `chat_stream` 并消费 `StreamingResponse.body_iterator`；fake agent 的 `process_message_with_feedback` 委托真实 `iter_with_verbose_feedback`（state 缺省时新建，与真实入口一致），因此 watchdog 时序、state 竞态拦截等内核行为在集成路径上被真实执行。

#### 自测结果（2026-08-31）

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/test_verbose_feedback_contract.py tests/unit/test_verbose_feedback.py -q` | **97 passed, 1 skipped**（Phase 1 基线 94+1 无回退；+3 为新契约用例，理由见上表） |
| `python -m pytest tests/unit/test_verbose_feedback_web_stream.py -q` | **8 passed** |
| `python -m pytest tests/unit -k "chat or stream or main_api or metadata" -q` | **168 passed, 1 failed, 2 skipped**；唯一失败 `test_skill_loader.py::test_parse_metadata_triggers` 属既有 66 环境性失败集合（`Skill.triggers` 属性缺失，与本功能无关，文件无交集） |
| `python -m pytest tests/unit -k "structure or line_count or frozen" -q` | **47 passed, 2 failed**；2 个失败均为 `prompts/test_ppt_prompt_contract.py` 既有基线失败（66 集合内）；agent.py 行数守卫通过 |
| `cd frontend && npx vitest run web/__tests__ --project web` | **235 passed, 3 failed, 1 error**；3 个失败（RpaBindingPanel 1 + routes 2）已用「还原本功能 4 个前端文件至 HEAD 后复跑」证实为**既有失败**（HEAD 上同样 3 failed），与本功能无关；新增 11 条用例全绿；architecture 测试全绿。1 个 unhandled error 来自既有组件测试的无关 DOM 插入异常，不影响用例结果 |
| `cd frontend && npm run typecheck` | **通过**（vue-tsc 0 错误） |
| `cd frontend && node scripts/check-dependency-boundaries.mjs --scope=web` | **通过**（MessageItem → useAgent 为既有允许方向） |
| `cd frontend && npm run build` | **通过**（vue-tsc + boundaries + vite build + artifact verification，481 modules） |
| `python -c "import src.main"` | **OK** |
| `python -m pytest tests/unit -q` | **66 failed, 5163 passed, 15 skipped**；失败数与 Phase 1 基线（66）**持平**，失败文件分布逐项核对均属既有环境性集合（skills/tools 无 LLM key、tenant storage、message_recall、kf QR、Postgres 等），0 新增；passed 5150→5163（+13，含本 Phase 新增 11 条后端用例） |

#### Phase 2 必测项覆盖对照

- SSE 解析 / verbose 帧透传 ✅（agent.verbose.test.ts + web_stream 透传用例）
- 同 eventId 去重 ✅（契约 3 条 + useAgent 覆盖用例 + `_build_verbose_metadata_entries` 单测）
- response 后隐藏 ✅（useAgent 清除用例 + MessageItem 正文非空隐藏用例）
- 历史 metadata 恢复 ✅（按 Phase 2 裁决：前端默认不展示、不消费 verboseMessages，MessageItem 用例锁定不渲染）
- 多会话 A/B 隔离 ✅（useAgentVerbose A/B 用例）
- browser continuation 不重复启动 watchdog ✅（web_stream continuation 用例，wrapper 零调用断言）
- metadata 合并保留文件、图片及已有键 ✅（downloadableFiles + progressMessages 三键合并用例）
- 前端单测与 build 通过 ✅；「10 秒 mock 工具约 8 秒提示随后 final」✅（后端 0.2s 等比 watchdog 用例 + 前端 fake timers 时间线用例）

#### Phase 2 遗留项

1. 前端 3 条既有失败（RpaBindingPanel/routes）与后端 66 条既有失败不在本功能修复范围，已验证与本功能改动无因果。
2. 历史消息 `verboseMessages` 的「展开查看」UI（如已完成消息下的可折叠提示记录）按计划属后续增强，本期前端完全不消费该字段。
3. 首批业务文案接入（travel-quote / excel `get_user_feedback` / SKILL.md metadata）仍为 Phase 4；Phase 2 测试以 scripted policy/system 事件覆盖协议与展示链路。
4. Phase 3 渠道 dispatcher / RPA 幂等 / 旧配置迁移未动（P0-2 仍 skip）。

### 11.3 Phase 3：第三方渠道可靠投递（2026-08-31 完成）

#### 改动文件清单

| 文件 | 改动 | 说明 |
|---|---|---|
| `src/channels/verbose_dispatcher.py` | 新增（411 行） | ① `ChannelVerboseDispatcher`：owner 生命周期一个实例；`submit()` 只做校验 + `put_nowait`（容量 1 队列，满/重复/晚到/非本体事件丢弃），绝不 await adapter；消费 task 惰性启动、串行调用 `send_verbose(event, delivery_id)`，单次 5 秒超时；False/异常/超时只冻结结果不重试；`close_and_drain()` 超时必须 cancel 并 await 在途发送 task 后才返回；`cancel_and_await()` 供取消/merged 路径；outcome 一次性冻结（sent/failed/timeout/suppressed_*）。② `resolve_verbose_feedback_config` 纯函数：force_disabled > 请求级 > 渠道 verbose_feedback > 旧 waiting_indicator 映射 > 全局 > 代码默认。③ `build_channel_verbose_metadata_entries`：按 eventId 去重，恰好五字段 + delivery。④ `final_delivery_id`（`{event_id}:final`）/`verbose_delivery_id`（`{event_id}:verbose:1`） |
| `src/channels/base.py` | 修改（+80 行） | `StatusDeliveryResult`（sent/failed/timeout/suppressed_rate_limit/suppressed_unsupported/suppressed_reply_budget）与 `send_status_message(reserve_for_final=1)` 契约（基类默认 suppressed_unsupported） |
| `src/channels/session.py` | 修改（+218 行） | `process_and_persist` 增加 `send_verbose`/`verbose_feedback_config` 可选参数；owner 生命周期创建一次 `VerboseFeedbackState` 并传入每次 `process_message_sync` 重跑（cancel/merge 不重置已 emitted/sent 状态）；merged follower 不进 owner processor、不建 dispatcher；verbose callback 惰性创建 dispatcher 并 `submit`；**verbose 路径全程不调 `mark_responding`**；final 持久化前 `close_and_drain` 冻结 metadata 后写 `verboseMessages`（无 verbose 时无该键）；取消/异常/merged 路径 `cancel_and_await`；`make_send_verbose`（kill switch 第二次读取，异常收敛为 `StatusDeliveryResult`） |
| `src/channels/wecom/adapter.py`、`src/channels/dingtalk/adapter.py` | 修改 | `send_status_message` + `_reserve_rate_limit`：进程内 deque 在无 await 临界区完成「清窗口 → `len(window)+1+reserve <= max` 校验预留+扣减」，额度不足返回 `suppressed_rate_limit` |
| `src/channels/feishu/adapter.py` | 修改 | Redis Lua 事务原子「校验预留+扣减」（杜绝先查后发 TOCTOU）；进程内内存降级同语义 |
| `src/channels/wecom_kf/budget.py` | 新增（83 行） | `WeComKfReplyBudget(total=5)`：`can_reserve`/`consume`（同步无 await 原子）/`record_suppressed`（suppressed_reply_budget 可观测） |
| `src/channels/wecom_kf/adapter.py` | 修改 | `send_status_message` 走 budget 预留（无法为 final 预留时抑制 verbose）；final 正文优先（长正文合成长图失败→单条截断纯文本），剩余额度发图片/文件，超预算记录 `suppressed_reply_budget`；budget 经 `_kf_reply_budget` 私有键注入（不挂 adapter 实例） |
| `src/channels/wecom_personal_rpa/adapter.py` | 修改 | `send_status_message`：幂等键由 pre_send 注入 request_id（verbose `{eventId}:verbose:1` / final `{eventId}:final`）决定，不依赖 `UnifiedResponse.message_id` |
| `src/saas/api/channel_routes.py` | 修改（+102 行） | 四个渠道入口接 `resolve_verbose_feedback_config` + `make_send_verbose` + `process_and_persist(verbose_...)`；**移除 route 外层旧 watchdog `_process_with_waiting_indicator`（调用与方法本体一并删除，测试锁定 import 失败）**；旧 waiting_indicator 经 `legacy_waiting_indicator` 映射进新机制（只发一次，无双发）；wecom_kf 入口按 owner 注入 `WeComKfReplyBudget(total=5)`（verbose 关闭时不注入） |
| `src/saas/api/wecom_personal_rpa_routes.py` | 修改（+29 行） | `send_response` 的 pre_send 传 `{event_id}:final`，`make_send_verbose` 的 pre_send 传 delivery_id（`{eventId}:verbose:1`）→ `set_reply_context` → outbox dedup_key 分离 |
| `src/saas/services/channel_factory.py` | 修改（+15 行） | 向 adapter 注入非敏感 `verbose_feedback` 渠道配置 |
| `tests/unit/channels/test_verbose_dispatcher.py` | 新增（25 条） | delivery_id 公式、submit 立即返回、超时/false/异常不重试、重复/晚到丢弃、close_and_drain 超时 cancel+await、cancel_and_await 无遗留 task、metadata 冻结/去重/形状、配置优先级 9 条（含 #13 force_disabled，:363）、ChannelFactory 注入 verbose_feedback |
| `tests/unit/channels/test_channel_status_rate_limit.py` | 新增（11 条） | 企微/钉钉/飞书剩 1 额度抑制 verbose final 成功（#8）、钉钉群聊 conversation_type 与 final 一致（#11）、Redis Lua 原子预留、内存降级、Redis 故障降级 |
| `tests/unit/channels/test_verbose_channel_delivery.py` | 新增（8 条） | process_and_persist 接线集成：#2 全程未追加 mark_responding、#4 慢 adapter/超时/false/异常不影响 final、#5 drain 后 metadata 冻结且晚到拒绝、#6 merged 不跑 processor、#7 cancel/merge 重跑复用 owner state 累计一次、#9 无独立 channel message 行、disabled 零行为变化、metadata 既有键保留 |
| `tests/unit/channels/test_wecom_kf_reply_budget.py` | 新增（12 条） | #12：total=5、verbose 成功扣 1、无法预留抑制、失败不扣、缺预算 suppressed_unsupported、正文优先、长图失败截断、多图片/文件耗尽预算、超预算可观测、无注入保持旧行为 |
| `tests/unit/channels/wecom_personal_rpa/test_verbose_final_delivery.py` | 新增（3 条） | #3：verbose/final delivery_id 分离、各自重试幂等（outbox 去重）、send_status 用注入的 request_id |
| `tests/unit/channels/test_wecom_kf_waiting_indicator.py` | 修改（迁移） | 旧配置注入/解析语义保留 + 旧配置映射进 verbose 机制（enabled/disabled/非法 delay/空白 message）+ `_process_with_waiting_indicator` 不可再导入 + **旧配置只触发一次 status 发送**（#10），共 16 条 |
| `tests/unit/channels/wecom_personal_rpa/conftest.py` | 修改 | 修复：psycopg2/bcrypt stub 改为「真实包可导入时不注入」（沿用 `_ensure_stub`），否则 RPA 目录先于其他测试收集时会以缺 `IntegrityError` 的 stub 污染同进程的 `test_idempotency` |
| `tests/unit/test_verbose_feedback_contract.py` | 修改 | 激活 P0-2（RPA verbose/final 幂等键），移除 skip |
| `docs/plans/plan-agent-intermediate-feedback.md` | 修改 | 本进度记录 |

#### P0/P1 回归 → 测试映射表

| # | 回归 | 测试 |
|---|---|---|
| 1 | verbose 后追加消息，owner final 不被 pending turn 覆盖 | `test_verbose_channel_delivery.py::TestReprocessReusesOwnerState::test_two_attempts_emit_verbose_once`（merged 重跑后 final 用第二次 attempt 合并结果）+ `tests/unit/test_session_queue.py`（Phase 1 既有 pending turn 守卫全绿） |
| 2 | 全流程未调用 mark_responding | `test_verbose_channel_delivery.py::test_verbose_never_triggers_mark_responding`（mark_responding 恰好既有 final 的 1 次） |
| 3 | RPA verbose/final 不同 outbox/request id，重试各自幂等 | `wecom_personal_rpa/test_verbose_final_delivery.py::test_final_and_verbose_pre_send_receive_distinct_delivery_ids` / `::test_retry_each_delivery_idempotent` / `::test_adapter_send_status_uses_injected_request_id` + 契约 P0-2 |
| 4 | callback 不受慢 adapter 影响；超时/false/异常不影响 final | `test_verbose_dispatcher.py::test_submit_returns_immediately_with_slow_adapter` / `::test_timeout_recorded_and_final_unaffected` / `::test_false_and_exception_recorded_not_retried` + `test_verbose_channel_delivery.py::test_slow_adapter_timeout_does_not_block_or_break_final` / `::test_send_verbose_false_and_exception_do_not_break_final` |
| 5 | close_and_drain 后 metadata 冻结，晚到不与 DB batch 竞态 | `test_verbose_dispatcher.py::test_close_and_drain_cancels_inflight_send_and_awaits_it` + `test_verbose_channel_delivery.py::test_late_submit_after_drain_rejected_and_metadata_frozen` |
| 6 | merged follower 不建 dispatcher、不发 verbose | `test_verbose_channel_delivery.py::test_merged_returns_without_running_processor` |
| 7 | cancel/merge 重跑复用 owner 状态，累计最多一次 | `test_verbose_channel_delivery.py::test_two_attempts_emit_verbose_once`（verbose_sends==1 且 metadata 一条） |
| 8 | 每轮每渠道最多一条；剩 1 额度 verbose 抑制 final 成功 | `test_verbose_dispatcher.py::test_duplicate_and_late_events_dropped` + `test_channel_status_rate_limit.py::test_last_quota_suppresses_verbose_final_still_ok`（企微/钉钉/飞书 Lua/内存降级 4 处） |
| 9 | 转人工/取消后不再发送；无独立 channel message 行 | `test_verbose_dispatcher.py::test_cancel_and_await_leaves_no_task` + `test_verbose_channel_delivery.py::test_late_submit_after_drain_rejected_and_metadata_frozen`（batch 无 verbose 行、outcome 冻结后拒绝晚到）+ `test_disabled_config_keeps_legacy_behavior` |
| 10 | 旧 waiting indicator 配置只触发新机制一次 | `test_wecom_kf_waiting_indicator.py::test_legacy_config_sends_exactly_one_status_message` + `::test_process_with_waiting_indicator_no_longer_importable` |
| 11 | 群聊 reply target/conversation type 与 final 一致 | `test_channel_status_rate_limit.py::test_group_conversation_type_matches_final`（钉钉；企微/飞书 status 复用与 final 相同 reply 通道，见各 adapter `send_status_message`） |
| 12 | kf 共享 5 次 budget，final 正文优先，组合降级 | `test_wecom_kf_reply_budget.py` 全部 12 条 |
| 13 | force_disabled 覆盖请求/渠道/旧 waiting indicator | `test_verbose_dispatcher.py::test_force_disabled_beats_everything`（:363）+ make_send_verbose 第二次读取（`test_disabled_config_keeps_legacy_behavior`） |

#### 自测结果（2026-08-31）

| 命令 | 结果 |
|---|---|
| 契约+内核+Web+渠道新套件 9 文件合并跑 | **182 passed**（契约+内核+Web 106 全绿含已激活的 P0-2；渠道新增 60 全绿；waiting_indicator 迁移 16 条全绿） |
| `python -m pytest tests/unit/channels/ -q -p no:warnings` | **3 failed, 923 passed, 7 skipped**；3 个失败均为既有环境性（kf QR data_url、RPA archive fetcher Postgres、db_outbox Postgres），0 新增 |
| `python -m pytest tests/unit/test_session_queue.py tests/unit/channels/test_session.py tests/unit/channels/test_session_manager_persist.py tests/unit/channels/test_make_send_response.py tests/unit/channels/test_idempotency.py tests/unit/channels/wecom_personal_rpa/ -q -p no:warnings` | **2 failed, 397 passed, 7 skipped**；2 个失败为既有 Postgres 环境性；修复 conftest stub 污染后 `test_idempotency` 在任意收集顺序下全绿 |
| `python -m pytest tests/unit -k "structure or line_count or frozen" -q -p no:warnings` | **50 passed, 2 failed**；2 个失败为 `prompts/test_ppt_prompt_contract.py` 既有基线失败；agent.py 行数守卫通过 |
| `python -c "import src.main"`；`from src.channels.session import ChannelSessionManager` / `from src.channels.verbose_dispatcher import ChannelVerboseDispatcher` / `from src.channels.wecom_kf.budget import WeComKfReplyBudget` | **均 OK** |
| `python -m pytest tests/unit -q -p no:warnings` | **66 failed, 5224 passed, 14 skipped**（Phase 2 基线 66 failed / 5163 passed / 15 skipped：失败数持平 0 新增；skipped -1 为 P0-2 激活，passed +61） |

#### Phase 3 遗留项

1. 真机/测试租户验收（普通渠道、微信客服、企微个人 RPA 各一条长任务）与灰度为 Phase 4 范围，未在本 Phase 执行。
2. `tests/unit/channels/wecom_personal_rpa/conftest.py` 的 stub 污染修复属测试基础设施最小修复（真实包可导入时不注入 stub），不改任何断言。

### 11.4 Phase 4：首批长任务接入、全链路验收与灰度（2026-08-31 完成）

#### 首批接入

| 项 | 改动 |
|---|---|
| `src/skills/travel-quote/SKILL.md` | metadata 增加 `user_feedback: {long_running: true, start_message: "正在生成报价单，这可能需要一点时间，请耐心等待。"}`（设计 §5 审核文案，逐字） |
| `src/skills/excel-to-template-1.0.0/SKILL.md` | 同上，文案"正在生成 Excel 模板文件，请稍候。" |
| `src/tools/excel/excel_process_tool.py` | `ExcelProcessTool.get_user_feedback`：仅 `task=fill_template`（含逗号多任务）返回 `LongRunningFeedback`；read/to_md/list/export/convert/merge 返回 None（继承 BaseTool 默认） |
| `src/core/verbose_feedback.py` | ① watchdog 竞态让位的 observer reason 区分 `response_started` 与 `watchdog_race_lost`（纯日志命名）；② `MAX_FEEDBACK_CHARS` 处注释裁决：`config.max_text_chars` 不做双源接线——validate_feedback_text 是无 config 依赖的冻结契约，接线会改契约签名且 60 字上限无差异化诉求 |
| `docs/plans/verbose-feedback-rollout-runbook.md` | 新增灰度与回滚手册（含真机验收矩阵） |

#### 全链路验收测试

新文件 `tests/unit/test_verbose_feedback_phase4_e2e.py`（15 passed，全 mock、亚秒级）：

1. 真实 SKILL.md 经生产解析路径 `SkillLoader.parse_skill_md` 断言 user_feedback 精确内容、经 `resolve_feedback_policy` 命中、不渲染进正文/Layer-1 描述（4 条）；
2. Excel 工具 fill_template 有文案、短任务无、逗号多任务命中、经 policy 解析可达、不出现在 tool schema（5 条）；
3. travel-quote / fill_template 全链路：policy verbose 先于 tool_start 与最终 response、每轮恰 1 条、`_build_verbose_metadata_entries` 按 eventId 去重且 delivery 冻结为 `streamed`（4 条）；
4. SSE 断开补充：消费端中途 aclose 后 producer 清理、无遗留 task（1 条）；
5. 并发 100 长 turn：全部完成、final 成功率 100%、每轮 verbose 恰 1 条、asyncio task 数恢复基线、总时长 ~4 秒（1 条）。

#### 故障注入覆盖映射（既有 → 测试名）

| 注入项 | 覆盖测试 |
|---|---|
| adapter 慢 10 秒 | `test_verbose_dispatcher.py::test_submit_returns_immediately_with_slow_adapter`、`test_verbose_channel_delivery.py::test_slow_adapter_timeout_does_not_block_or_break_final` |
| adapter 返回 false / 异常 | `test_verbose_dispatcher.py::test_false_and_exception_recorded_not_retried`、`test_verbose_channel_delivery.py::test_send_verbose_false_and_exception_do_not_break_final` |
| dispatcher 超时 | `test_verbose_dispatcher.py::test_timeout_recorded_and_final_unaffected` |
| SSE 断开 | 新增 `TestSseDisconnectFaultInjection::test_consumer_disconnect_cleans_producer_and_stops_sending`（kernel 级 cancel 另有 `test_no_leftover_tasks_normal_error_and_cancel`） |
| 8 秒边界 / 技术事件不推迟 | `test_verbose_feedback.py::test_absolute_deadline_not_postponed_by_technical_events`、`test_fast_turn_no_watchdog` |
| 用户追加消息 | `test_verbose_feedback_contract.py::TestP0AppendAfterVerboseKeepsFinal`、`test_channel_status_rate_limit.py` 附加链路 |
| 转人工 / 取消 | `test_verbose_feedback.py::test_browser_human_required_suppresses_watchdog`、`test_no_leftover_tasks_normal_error_and_cancel` |
| merge follower / 重跑复用 | `test_verbose_channel_delivery.py::test_merged_returns_without_running_processor`、`test_two_attempts_emit_verbose_once` |
| metadata 冻结 / 迟到投递 | `test_verbose_channel_delivery.py::test_late_submit_after_drain_rejected_and_metadata_frozen`、`test_verbose_feedback_web_stream.py::TestBuildVerboseMetadataEntries` |
| RPA 双幂等键 | `test_verbose_final_delivery.py`（3 条）|
| kill switch | `test_verbose_dispatcher.py::test_force_disabled_beats_everything`、`test_verbose_feedback.py::test_kill_switch_beats_request_config` |
| 微信客服配额 / 旧配置迁移 | `test_wecom_kf_reply_budget.py`、`test_wecom_kf_waiting_indicator.py`（16 条） |

#### 自测记录

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/unit/test_verbose_feedback_phase4_e2e.py -q` | **15 passed**（~4s，含 100 并发） |
| 9 文件 verbose 套件合并跑 | **182 passed**（与 11.3 记录持平，0 回退） |
| `python -m pytest tests/unit -k "excel" -q` | **208 passed** |
| `python -m pytest tests/unit -k "skill" -q` | 17 failed / 235 passed——17 个失败经 `git stash` 对照确认为既有基线失败（3 个 skill_loader triggers + 14 个 travel_quote_hotel_price/pricing），与本 Phase 无关 |
| `python -m pytest tests/unit -q` | **66 failed / 5239 passed / 14 skipped**（失败数与 Phase 3 基线持平 0 新增；passed +15 = 新增 Phase 4 测试） |
| `python -c "import src.main"` | OK |
| `python -m pytest tests/unit -k "structure or line_count or frozen" -q` | 51 passed / 2 failed（2 个失败为 `prompts/test_ppt_prompt_contract.py` 既有基线；agent.py 行数守卫通过） |

#### Phase 4 遗留项

1. 真机验收矩阵与灰度执行属用户/运维动作，见 `docs/plans/verbose-feedback-rollout-runbook.md` §2/§3/§5。
2. `max_text_chars` 配置双源接线已裁决不做（理由见改动表），无遗留代码项。

### 11.5 产品决策变更：全局默认启用（2026-09-01，主控者执行）

用户在本地验证后决策：verbose 全局默认开启，原"首版默认关闭灰度"策略废止。同步改动：

- 代码默认：`VerboseFeedbackConfig.enabled` 与 `AgentVerboseFeedbackConfig.enabled` 默认 `true`（`src/core/verbose_feedback.py`、`src/config/settings.py`）；`configs/config.yaml` 显式 `enabled: true`。
- 契约测试 `test_defaults`/`test_effective_enabled_follows_enabled` 及配置集成断言同步更新（契约变更理由：产品决策，本节为记录）。
- **语义修正（测试暴露）**：`resolve_verbose_feedback_config` 中渠道/旧 waiting_indicator 显式关闭（`enabled=false` 或 `delay<=0`）由"穿透全局"改为"显式关闭生效"——否则默认改 true 后，显式关闭的存量租户会被全局穿透（违反"已配置租户行为不倒退"）。新增 `test_channel_disabled_stays_disabled`、`test_legacy_delay_zero_stays_disabled` 钉死。
- 文档同步：设计 §1/§11、本计划 §1/Phase 1/门禁、runbook §0/§1/§2.1（灰度第一阶段改为"部署后回归验证"）。
- 验证：verbose 全套件 199 passed；全量 unit 66 failed（既有环境性清单持平）/ 5241 passed / 14 skipped，0 新增；`import src.main` OK。紧急回滚手段不变：全局 `force_disabled=true`（修改后需重启）。

### 11.6 产品决策变更：删除 system watchdog（2026-09-01，开发智能体执行）

用户（产品负责人）拍板："system watchdog 直接删除不需要，不需要它兜底，所有文案都在工具或者 skill 中指定。"同步改动：

- 内核 `src/core/verbose_feedback.py`：删除 watchdog 全部逻辑（绝对 deadline 计时、超时等待、`source="system"` 兜底发射、`watchdog_race_lost` 等抑制原因）；保留 producer task、事件透传、未注册 verbose 拦截、终态置位（`_TERMINAL_EVENT_TYPES` 转为 policy 发射守卫，response_started 后 state/dispatcher 拒绝后续 verbose）、task 清理；`VerboseFeedbackConfig` 删除 `initial_delay_seconds`，保留 `fallback_message`（仅作 policy 文案违规降级模板）与 `force_disabled` kill switch。
- 配置：`settings.AgentVerboseFeedbackConfig` 与 `configs/config.yaml` 删除 `initial_delay_seconds`；`resolve_verbose_feedback_config` 中旧 waiting_indicator 的 delay 不再映射运行时字段——`enabled=false` 或 `delay<=0` 视为显式关闭（沿用旧 `_get_waiting_indicator_cfg` 语义），delay 非法/缺失视为启用；`_LEGACY_WAITING_DEFAULT_DELAY` 常量删除。
- 测试：watchdog 专项用例删除或改写为策略场景；100 并发长 turn 改为策略驱动（每轮恰好 1 条 policy verbose、final 100%、task 恢复基线）。
- 文档同步：设计 §1/§2.1/§5/§6.3（整节改写为决策记录）/§7/§11/§12/§13/§14、runbook §0/观察指标/验收矩阵。
- 验证新基线：verbose 10 文件套件 200 passed（原 199：watchdog 专项删除、策略/legacy 语义用例补充，净 +1）；全量 unit 66 failed（既有环境性清单持平）/ 5242 passed / 14 skipped，0 新增；`python -c "import src.main"` OK；结构守卫仅既有 2 个 ppt 失败；agent.py 行数守卫（≤3977）通过。
- 理由：兜底文案与业务策略文案并存造成语义冲突（先通用兜底后业务提示的重复等待语），且兜底无法表达业务语义；产品选择「无策略就不提示」的确定性体验。

### 11.7 修正：Excel 提示语只定义在工具层（2026-09-01，主控者执行）

用户裁决：excel-to-template 场景实际执行走 Excel 工具的 `fill_template`，提示语应定义在 Excel 工具的 `get_user_feedback`，Skill metadata 不重复定义（避免双层来源）。

- `src/skills/excel-to-template-1.0.0/SKILL.md`：删除 `metadata.user_feedback` 块。
- Excel 工具 `ExcelProcessTool.get_user_feedback`（仅 fill_template）保持不变，成为该场景唯一文案来源。
- 测试：`test_excel_to_template_skill_md_declares_approved_feedback` 改为 `test_excel_to_template_skill_md_has_no_user_feedback`（断言 metadata 无该键）+ `resolve_feedback_policy` 对该 skill 返回 None；设计 §5 首批策略同步。
- 验证：metadata 探针 4 passed；verbose 核心套件 120 passed；全量 unit 66 failed（既有清单持平）/ 5242 passed，0 新增。
