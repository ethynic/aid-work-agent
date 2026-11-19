# AgentRunner 独立服务开发计划与完成记录

## 开发进度

**✅ 已完成开发并归档（2026-10-08）。** Agent/AgentRunner 架构重构与 Web 接入已完成，微信客服原渠道恢复及服务接入也已完成。本文保留实施和验收证据，重构事项登记于 `ideas_finished.md`，不再作为 `ideas.md` 中的进行中任务。

下文阶段记录保留当时的结论及验证限制；当前状态以本登记区和对应渠道计划为准，历史记录中的“待实施”不重新打开已完成重构。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| M0 | 契约、责任归属与行为基线 | ✅ 完成（2026-10-01） | 核心契约审查通过；旧基线后端434/前端28；原9条测试顺序失败已修复重验 |
| M1 | 核心职责拆分、执行隔离与恢复边界 | ✅ 完成（2026-10-01） | 源码独立CR通过；53核心/装配、58关联及最终244项均有通过证据；不等于服务已交付 |
| M2 | 独立服务：持久接单、执行与查询 | ✅ 完成（2026-10-01） | API37、仓储/计费38、执行/owner43项分别独立验收；最新源码CR通过，后续恢复/四入口待迁移 |
| M3 | Web 接入：提交、轮询、刷新找回 | ✅ 完成（2026-10-02） | 普通Web/opt-in同步：后端32、前端44、原页面5步骤通过；24源CR/SHA/build验收，完整等待续接及默认切换依赖M4 |
| M4 | 暂停、继续与进程中断恢复 | ✅ 完成（2026-10-02） | Web/Local 及原页面续接验收；卡片正式15＋开发1；v2 CR0，425源/102辅助封存；默认迁移及外部真机仍M7 |
| M5 | 实时事件订阅增强 | ✅ 完成（2026-10-03） | 事件账/代理已验收；前端26＋真实页面1、关联6、CR0/build0；552源/32辅助末核一致 |
| M6a | 微信客服原渠道接通用 Runner HTTP | ✅ 完成（2026-10-08） | 原渠道恢复及独立 Runner 接入完成；agent2 发布与文本/语音/合并/取代真机验收见恢复计划 Phase 5 |

### 后续独立事项（不属于重构待办）

| 原阶段 | 事项 | 跟踪位置 |
|--------|------|----------|
| M6b | 飞书原渠道接入 Runner，待开发 | [飞书渠道计划](../channel/feishu/implementation_plan.md#agentrunner-接入后续独立事项) |
| M6c | 钉钉原渠道接入 Runner，待开发 | [钉钉渠道计划](../channel/dingtalk/implementation_plan.md#agentrunner-接入后续独立事项) |
| M7 | 已有整改和验收记录归档；尚未验证的容量、真实外部工具及其他环境不宣称通过 | 本文 §10 保留历史证据；后续接入按渠道计划验收，环境发布按[部署清单](../guides/deployment/agent-runner-deploy-checklist.md)核对 |

> 制定日期：2026-10-01
>
> 设计：[AgentRunner 服务架构设计](../system/agent-application-architecture-design.md)
>
> M0–M5 重构已分阶段验收完成，M6a 微信客服恢复及接入已完成。后续渠道只接入独立 Runner，不包含渠道重构；M7 的历史整改记录不作为继续启动重构的依据。后续代码开发按项目流程独立验证，提交和部署遵循用户授权。

## 1. 交付路线与范围

**核心重构已完成，后续渠道只接入既有服务。** 下列 M0–M5 顺序保留为已完成交付路线；M6/M7 的后续工作独立跟踪。

- M3 是第一个可体验的 Web 闭环：提交被持久接受后返回 runner_id，页面关闭不影响任务，重新进入能查询进度和结果。
- M4 完成后，Web 的持久执行与中断续接达到本期要求。M3 不能提前宣称已支持故障恢复。
- M5 只增强实时体验。查询始终是准确状态与最终结果的依据，关闭订阅后整套功能仍可运行。
- M6a/M6b/M6c 保留各自原渠道实现，只替换对话执行调用，分别切换、验证和回退。提交到独立 API/worker 并携带已授权 source/会话身份，渠道消费结果并沿原流程回发，不要求最终用户主动查询。
- Runtime+BOSS CLI 从 M1 起就是兼容约束，M3/M4 做针对性链路验证，不能等最后联调才发现客户工具不可用。
- 不将定时任务、CLI 调试、桌面 D1 或 Runtime 自动会话决策接入新服务；其功能仍须兼容。（企微个人 RPA 已清退删除）核心上下文、主/子共用 Loop、工具派发和生命周期必须真正拆分，不做通知中心或多执行节点自动扩容。
- 核心内部接口调整属于已完成的 Agent 重构范围，不能作为后续重写渠道的授权。渠道接入仅做必要的调用、身份及结果适配，沿用原队列、媒体、历史和发送；不新增渠道专用 gate、管线、消费者或容器，也不新增暂停/继续按钮。

实施时使用入口级开关逐个切换；同一请求只能走一条执行路径。原有调用方继续使用旧方法，禁止先将所有旧方法重定向到新服务。

## 2. 最小接口与持久模型

### 2.1 接单和查询优先

内部接口采用以下资源操作；M0 冻结职责与操作边界，M2 细化并验收持久 schema。Web 专用网关映射到 `/api/chat/runners`，不直接暴露受信身份字段。

| 接口 | 最早阶段 | 约定 |
|------|----------|------|
| `POST /v1/runners` | M2 | 输入持久保存后返回 202、runner_id、当前状态；表示已接受，不表示任务完成 |
| `GET /v1/runners/{id}` | M2 | 状态、公开进度/累计输出、结果、revision、等待原因及允许操作；不返回私有 checkpoint |
| `GET /v1/sessions/{kind}/{id}/runners` | M2 | 按已授权现有会话分页查询，找回活跃及历史 runner |
| `POST /v1/runners/{id}/cancel` | M2 | 独立授权，重复取消幂等；不能撤销已发生的外部动作 |
| `POST /v1/runners/{id}/controls` | M4 | 持久接收 pause/resume/reply 与稳定请求键；暂停接收不等于已停稳，回复匹配原等待标识 |
| `GET /v1/runners/{id}/controls/{control_id}` | M4 | 查询本人命令的接受、消费或拒绝事实；继续仍推进同一 runner |
| `GET /v1/runners/{id}/events?after_seq=...` | M5 | SSE 补读及实时读取；只是通知增强，不启动执行 |

错误使用现有项目脱敏规则及 debug 字段；禁止把内网错误、凭据、工具原始参数或系统提示词返回前端。所有操作都校验当前可信主体、tenant 与会话归属，不能仅凭 runner_id 访问。创建、执行与续接另校验当前智能体订阅/执行权限；查询已有结果与取消本人任务沿用既有历史归属权限，订阅过期不等于丢失历史。渠道查询/控制必须提供本次可信平台主体与路由证明，不能从目标记录复制身份后自证授权。

创建请求携带稳定 client_request_id。同一提交重试返回原 runner，同键不同输入拒绝。去重范围为服务端导出的 tenant/global scope、主体及 source，输入摘要包含 session/kind；同键换会话仍拒绝，不能生成第二个 runner。页面网络错误不能用新编号自动重发；先按原请求键/会话查询已接受的任务。去重键与原请求在同一事务保存。

### 2.2 初期数据落点

M2 建立 `agent_runners` 系统表：身份与会话引用、输入/配置引用、创建去重键与输入摘要、状态、attempt、revision、lease/控制请求、最新版本化 checkpoint、公开输出/结果、现有 record 关联与时间。

- 真实 tenant_id 可空，非空内部 scope_key 由认证主体导出 tenant/global 命名空间，客户端不能指定，不伪造业务 tenant。仅当前数据库确认的平台管理员可访问自己的 NULL tenant Web 会话，渠道仍要求真实 tenant；NULL 比较采用 IS NOT DISTINCT FROM，不解释为全租户。
- 创建去重唯一约束按 scope_key、主体、source、client_request_id 设置；runner_id 稳定，attempt 恢复后递增。
- M2 同时建立 `agent_runner_session_claims`：通用仓储按 scope_key/kind/session 唯一保存逻辑 owner，与执行 lease 分开；paused/waiting/interrupted 保留位置，queued 不占有，resume 续原 owner，终态释放。KF 沿用原渠道队列，共用通用执行 claim 与 attempt/lease，finalizer 终态释放 claim；不再有 KF 专用接单或投递 gate。原队列取消确认终态后交接。其他渠道在其接入阶段单独核定。
- M2 建立 `agent_runner_usage_receipts`：调用/用量事实与结算水位单独保存，迟到事实在原 record 补差，不借用公开事件的顺序或保留策略。
- M4 建立 `agent_runner_controls`：持久保存控制请求、输入摘要与单次消费事实，和公开事件、费用账本分开。实际接口统一为 controls 资源，不再分别建立 pause/resume 路由。
- checkpoint 只保存 JSON 可序列化数据及稳定资源引用，不保存协程、连接或密钥；版本不兼容明确拒绝恢复。
- Web/渠道会话及消息表保持分离。Runner 上下文不是新的公共聊天历史，不提供跨表 fallback。
- M5 建立 `agent_runner_events`：tenant、runner、attempt、seq、类型、脱敏 payload、时间；runner/seq 唯一。序号分配、事件与相关状态/恢复点变化在同一事务提交。
- M2–M4 先以状态/结果快照为权威；M5 启用事件后，最终结果、必要聊天写入与结束事件一起提交。发送平台消息不属于这个数据库事务。
- 活跃/可恢复记录不能自动清掉恢复点；事件、结束任务和产物分别设置有限保留期/容量。清理不得影响普通聊天记录。M0 明确保留配置，M7 验证容量与清理。

迁移脚本按项目现有机制提供，只增量新增系统表，不改造客户 Runtime 的表和协议。实际建表时同步 `docs/system/database_system_table.md` 及数据库变更记录。

## 3. M0：冻结契约和行为基线

工作：

1. 逐项列出 Web 与三个渠道的鉴权、消息写入、record/usage/Trace 收尾、取消、锁和发送责任；特别记录 Web 断线取消将被明确改为只断开观察。
2. 确认新 Web 功能使用的会话入口和数据读取链；同步 `/api/chat` 的旧返回格式保留，不能顺手把它当成已落库入口。
3. 固化 runner 状态转移、会话排队、等待输入、取消/暂停区别、checkpoint 版本及工具阶段。
4. 核对模型/工具的实际调用和副作用：已有 invocation 可查询，其他不可安全恢复的写工具必须停下核对，不承诺全部工具自动续跑。
5. 明确内部受信认证、用户身份映射、版本/产物引用、轮询策略、容量上限与配置位置；不足之处在本计划更新，不另造一个全平台设计。

验收：有可核验的成功/失败/取消/并发/合并/费用基线；每个迁移责任有唯一归属。使用现有相关测试，不写镜像实现的形式化测试；发现旧缺陷记录并与正确性修复区分。

### M0 架构审查决议（2026-10-01）

独立架构审查与旧功能基线已完成，实施者与主控确认以下开工契约。源码由实施角色单一写入，新增测试由独立测试角色负责，主控协调与验收。M0 冻结核心职责与资源操作，具体 API 字段和数据库 schema 在 M2 实施前细化复核。

- 真正拆出 `src/core/agent_engine/` 的 State、ports、纯上下文格式化和唯一 Engine；主/standalone/child 均进入该 Engine。具体上下文 IO、工具适配和生命周期由应用层装配，旧 Agent 最终仅为兼容壳。
- Engine 不 import HTTP、渠道、具体仓储、计费或领域 SDK；不能借“通用 adapter”继续调用旧巨类执行循环。具体上下文装配进一步分 Profile、History、Prompt、Compression、SkillSession；工具派发分普通、本地、控制/委派适配，避免形成另一巨类。
- 本地 invocation 的 tenant/user/session 必须来自当前 ExecutionState，不能从共享 `self.session_id` 获取。Worker 持有生成器，订阅断开不能触发其 finally 取消本地操作。
- 每次真实模型调用都记录可核验用量事实，失败后的重试不能只累计最终响应。恢复使用原 tool/call/invocation 事实，不能让历史清洗删除 pending calls 后从头运行。
- M1 验收真实共享 Engine、依赖方向、执行隔离与功能 owner；数据库占有/领取、跨进程运行和崩溃恢复随后分别按 M2–M4 验收。

| 现有能力 | 新架构责任归属 |
|----------|----------------|
| 主/standalone/child 配置、DB prompt、extra/template/knowledge/env、租户技能 | ProfileResolver + ContextAssembler + 执行内 SkillSession |
| 时间/附件/多模态、文件 workspace、长期记忆/记住意图、回复风格 | ContextAssembler 及相应仓储/资源接口 |
| Web/渠道历史、摘要/compacted/recalled、人工客服/接管、tool 配对清洗 | 明确 session kind 的 HistoryRepository + 纯消息规范化 |
| 压缩阈值、摘要写入、token 缓存与压缩指标 | CompressionCoordinator；状态/指标经应用观察者保存 |
| 多轮模型、角色轮数、调用配对、等待/取消/轮数耗尽 | 唯一 Engine + RolePolicy + ExecutionOutcome |
| use_skill/version/hook/full 指南、skill_execute/complete、plan | 工具控制适配 + SkillSession；Engine 不按工具名处理 |
| 子任务并发/超时/上下文/图片/澄清与用量 | Child 适配复用 Engine，独立子 State、等待事实及父汇总 |
| 兄弟调用结果配对、原调用续接、D1 defer | 等待/控制适配及持久 tool ledger；旧接口适配保持 |
| 本地 invocation、设备认证、写许可、进度/结果/取消/费用 | 既有 local-tools 协议 + 当前执行上下文的本地工具适配 |
| 图片去重、下载产物、tool_messages、verbose | 执行产物/消息收集 + 公开投影 + 入口既有呈现策略 |
| Trace、模型/技能/子任务真实用量、记录和取消轮次落库 | 应用 Lifecycle 的单一责任方及稳定调用水位 |
| 渠道合并/重跑、消息送达、锁释放及 recap | 渠道协调/发送适配；不进入 Engine |

门槛不能仅以目录、行数或初始化测试证明。既有能力缺失、仅包装旧循环、身份/权限回退、恢复重发未知写操作均属阻断项。

### M0 独立测试记录（2026-10-01）

起点 HEAD 为 `7d83c07d20f72bcf401cd6493ad4cf6ab9bac05c`。测试使用已有 `aid-agent-api` 的 Python 3.11，已确认仓库 bind mount `/app`。所有 pytest 使用随机隔离 PostgreSQL 测试库且自动删除，无容器重启、部署或真实 BOSS 操作。脚本没有执行位，实际使用 `bash ./scripts/dev_test.sh --isolated-db`，未修改权限。

| 批次 | 独立运行结果 | 范围与限制 |
|------|--------------|------------|
| A | exit 0，114 passed | 旧 Agent 初始化/提示词、图片/事件、请求/工具上下文、租户技能、record、消息规范化；不证明完整新 Loop |
| B | exit 0，192 passed | queue、渠道持久/发送/去重、verbose、微信客服/飞书/钉钉；Redis和渠道网络边界为替身 |
| C | exit 1，119 passed / 9 failed | BOSS费用/本地代理、设备PG协议、Web/D1；9条失败在Web测试helper的默认event loop，未进入API断言 |
| D/E | Web单独9 passed；修helper后record+Web组合14 passed，均exit 0 | `test_web_sessions.py`改用asyncio.run；保留原失败事实，未改业务代码 |
| F | exit 0，5文件28 passed | 前端agent/verbose/multi-session/localTools；MSW cancel和clone警告意味着不能当真实取消链路证据 |

去重通过数为后端434、前端28；重复重验不重复计数，无 skip。批次运行时旧业务实现尚未切换，新核心文件未被旧测试加载，不能将其作为 M1 新装配的回归证据。真实客户 Windows/BOSS、跨进程独立执行、故障恢复和真实模型工具启动仍待后续验收。

实际后端基线命令使用上述 bash 前缀，并追加 `-p no:cacheprovider -q --tb=short`：

```text
A: tests/integration/test_agent_loop.py tests/unit/test_agent_image_event_loop.py tests/unit/test_agent_events.py tests/unit/test_agent_request_context.py tests/unit/tools/test_tool_context.py tests/unit/test_tenant_skill_cache.py tests/unit/test_agent_record_isolation.py tests/unit/test_session_record_manager.py tests/unit/test_agent_reorder_messages.py tests/unit/test_agent_tool_result_echo_guard.py tests/unit/test_agent_local_tool_registration.py
B: tests/unit/test_session_queue.py tests/unit/channels/test_session_manager_persist.py tests/unit/channels/test_verbose_channel_delivery.py tests/unit/channels/test_make_send_response.py tests/unit/channels/test_idempotency.py tests/unit/channels/test_agent_user_builder.py tests/unit/channels/test_wecom_kf_reply_budget.py tests/unit/channels/test_feishu_image_send.py tests/unit/channels/test_dingtalk_image_send.py tests/unit/channels/test_channel_context_servicer_role.py tests/unit/channels/test_wecom_kf_adapter.py tests/unit/channels/test_feishu_adapter.py tests/unit/channels/test_dingtalk_adapter.py
C: tests/unit/local_tools/test_boss_tool_billing.py tests/unit/local_tools/test_write_result_billing.py tests/unit/local_tools/test_repository_state.py tests/unit/local_tools/test_proxy_tool.py tests/unit/local_tools/test_security.py tests/unit/tools/test_context_channel_derive.py tests/unit/session_tasks/test_device_protocol.py tests/integration/test_web_sessions.py tests/unit/test_desktop_agent_d1_agent_seam.py tests/unit/test_desktop_agent_d1_security.py
D: tests/integration/test_web_sessions.py
E: tests/unit/test_agent_record_isolation.py tests/integration/test_web_sessions.py
```

前端在 `frontend/` 实际运行：`npm test -- web/__tests__/api/agent.test.ts web/__tests__/api/agent.verbose.test.ts web/__tests__/composables/useAgentMultiSession.test.ts web/__tests__/composables/useAgentVerbose.test.ts web/__tests__/api/localTools.test.ts`。

M1 局部独立测试：`bash ./scripts/dev_test.sh --isolated-db tests/unit/test_agent_engine_acceptance.py -p no:cacheprovider -q --tb=short`，扩展至23 passed、exit 0。仅新 Engine + Model/Tool I/O 替身，不证明完整运行时/兼容壳、数据库持久化或进程恢复；M1 尚未验收完成。

M1 实施者另外两批自测遗漏 `--isolated-db`（44 passed；56 passed / 13 failed），不作为隔离验收证据。已停止默认 pytest，稳定代码须经隔离入口复验。只读核查确认目标为既有共享测试库 `aid_work_agent2`；根 conftest 在 sessionstart/sessionfinish 按测试租户公司名/前缀及 `^T[0-9A-F]{6}$` 编号匹配，再遍历 public 的 tenant_id 表清理。两批输出无正计数清理日志，但存在静默返回和 rollback，不能由无日志证明未写入，实际删除量无法从现有证据确认。13 条失败初步属于旧私有 helper 的测试入口，独立测试者迁移到新职责组件并保留功能断言，不据此把业务逻辑塞回兼容壳。

## 4. M1：核心职责拆分、执行隔离和恢复边界

主要位置：`src/core/agent.py`、`agent_router.py`、`request_context.py`、`src/tools/context.py`、`src/services/session_record.py`；新增通用运行上下文模块。

工作：

- 清理请求身份、图片、工具消息和压缩临时事件对共享 Agent 的写入；结果通过单次上下文返回。
- 模型循环能够接收初始上下文及已保存的 checkpoint，在模型完成、工具派发前、工具结果保存后、压缩/等待处报告安全边界。
- 将 ExecutionState、ContextAssembler/PromptBuilder、单一 Loop engine、ToolDispatcher、Lifecycle 分开，按设计 §3.1 的契约验收。领域逻辑进入工具/上下文适配器，不把旧巨类换目录继续调用。
- 主/子共用一个 engine，角色策略保持现有权限、轮数、澄清和上下文差异；子状态/Trace 独立，usage 汇总正确。
- 保留非目标入口行为，旧 Agent 方法最终仅作适配壳，映射到新 engine。恢复/仓储/控制依赖注入，不在 Loop import HTTP、渠道、具体 DB 或计费模块。
- ContextVar 嵌套恢复、本地工具 session/principal、技能作用域正确；不从共享实例取身份。

验收：两个 tenant/会话交错执行不串身份或图片；已完成工具结果不再调用工具；恢复上下文与 messages 对齐；现有 record、图片、租户技能和本地工具相关回归通过。审查真实主/子调用链及依赖方向，不能只让新目录守卫通过；旧巨类不是新服务运行核心。

### M1 中间审查与验收门槛（2026-10-01）

两套旧 Loop 已删除，主/standalone/child 已进入同一 Engine；组件之间的双向动态代理已移除，改为显式依赖。以上是当前调用图事实，不代表整个阶段通过。

| 问题 | 本阶段要求 |
|------|------------|
| 澄清只写等待、不读取下一句补充 | 保持原澄清后续派行为，独立测试实际装配 |
| D1 静默只取首个 call | 保留旧配对和等待语义；D1 不支持的并行调用明确失败，已收到的模型用量仍记录 |
| 正式回归发现 D1 续接缺原 tool 结果、有效 call 的 ID/过滤配对变化 | 保留旧公开协议与完整持久消息序列；原断言不变，修复后定向重验 |
| strict 历史查询把越权等同于空历史；准备先压缩后校验 | 新路径在任何压缩、remember、模型或工具 IO 前授权会话；兼容容错策略单独明示 |
| 续接的 runtime identity/profile 与 checkpoint 未核对 | 在工具上下文装配前拒绝身份、角色及配置不匹配 |
| 附件 workspace 在准备失败时未清理 | 准备失败清理，成功后转移至执行状态管理；等待恢复按策略保留 |
| 子计划沿用父 session 的 Redis 键（原有核心缺陷） | PlanManager 显式执行 scope 隔离目录及所有权威键；父/双子创建和推进互不覆盖 |
| SkillLoader 将租户 `.env`/默认值写入进程环境（原有核心缺陷） | 显式 tenant 的请求级映射传脚本、hook、动态上下文；指南加载不改 os.environ，可信身份最后覆盖，环境映射不入 checkpoint |
| 严格历史的摘要读取仍走无 tenant 过滤的全局接口（原有核心缺陷） | 绑定 HistoryRepository 读取摘要；NULL tenant 也明确匹配，不解释为全租户 |
| 图片空文本结果被队列的真假值回退吞掉（迁移回归） | 保留本次结果的图片事实，实际队列、落库及发送均验证 |
| 默认压缩仓储遗漏渠道 TEXT JSON 元数据解析（迁移回归） | 保持原 JSON 转换语义，后台渠道压缩与严格路径分别验证 |
| 主循环上下文 token 缓存更新遗漏 | 由观察者/仓储恢复原更新行为，明确 tenant/kind/session 范围 |
| 压缩间接引入渠道管理模块 | 抽取会话/历史仓储接口，复用算法；M2 独立启动验证实际 import 图 |

独立测试已将旧私有 helper 的 13 条测试入口迁至新 owner，保留功能断言；定向运行 14 passed、exit 0，另核查相邻结构性测试。局部测试不能替代完整兼容装配回归。

正式验收已启动。公开主/standalone 实际装配、共享 Agent 的跨租户技能/提示词/图片/工具上下文/用量隔离、真实 child 多模态/澄清以及隔离 PG 历史作用域已覆盖；外部模型/工具 IO 使用替身，实际 Engine 和 Runtime owner 未替换。首次完整批次发现 D1 实质回归，旧测试 seam/fixture 的原失败与迁移原因保留在独立证据中。

后续关联隔离批 58 passed、exit 0，D1 持久配对/image-only/压缩 TEXT JSON/旧澄清安全迁移等实质问题已修复重验。新 Engine 27 + Runtime 26 的独立组合批 53 passed、exit 0，包括父/双子计划创建、推进和删除互不影响，以及真实 BOSSProxy/LocalAdapter 使用本次请求 session 与 invocation 进度。这里只替换外部 IO，不替换新 Engine 执行；不等于真实客户设备或持久服务恢复验收。正式 CR 已确认职责边界与调用图，最后放行仍待技能全局环境和严格摘要 scope 修复验证。

正式独立源码 CR 已通过：最后技能环境、可信用户/会话占位及严格摘要 tenant/NULL scope 已逐项复核，实际 runner→tool 路径无剩余 P1/P2。源码审查之后的最终测试结论见下文验收记录。直接 `SkillExecutor.execute_skill_command(context=A, user_id/session_id=B)` 的非当前运行路径参数冲突作为后续接口收敛项；M2 组合根不得使用矛盾身份，须统一 effective context 或拒绝冲突。

**M1 最终验收通过**：独立测试末批 243 passed / 1 failed（exit 1）的唯一失败是测试自身 execution_id 遮蔽 profile 断言，修正测试资源后相关 8 passed（exit 0）；最终 244 项都有通过证据，不将重验 8 项再次累计。最终 43 个生产源码文件两次 SHA 校验无漂移。真实双租户技能指南/动态上下文/hook/执行、显式 A 与 ambient B、可信用户/会话，以及隔离 PG 的外租户/NULL/Web/渠道摘要均通过。源码审查与独立测试都允许进入 M2，主控批准阶段验收。

完整独立记录：[M1 证据](/private/tmp/aid-agent-runner-m1-independent-test-evidence.txt)、[最终源码 SHA](/private/tmp/aid-agent-runner-m1-source-final.json)。批次计数重叠，保留原失败和测试 seam/fixture 迁移原因，不拼接虚构唯一总数。此验收不证明独立服务、关页执行、跨进程 lease/fencing、数据库恢复、真实 Windows/BOSS 或渠道送达；这些按 M2–M7 分阶段完成。未新增容器/卷或操作 master/Yohar，临时测试库与新 fixture 产物已按归属清理，证据保留供审阅。

后续门槛另行明确：子执行必须注入持久控制/观察接口，不能永远使用 no-op CompatibilityControl；worker 关闭和租约丢失须区别于用户取消；最终结果/消息/计费由应用事务收尾，不能把 Engine 的 completed 安全点直接当作已收尾终态。客户本地工具继续按原 PG invocation/events 协议工作，恢复查询原 invocation，结果未知时不得再次创建。

## 5. M2：独立服务接单、执行和查询

新模块建议放在 `src/services/agent_runner/`，包括 contracts、repository、manager、executor、api、bootstrap、client；按需要拆文件，不为凑分层建立空模块。

M2a 持久 API 切片已冻结交接：16 个文件 SHA 记录见 [/private/tmp/aid-agent-runner-m2a-source-sha.json](/private/tmp/aid-agent-runner-m2a-source-sha.json)。开发隔离配置/schema 定向检查 38 passed、exit 0；独立启动 import/compile 检查 exit 0，未启动旧 Agent、渠道管理或调度。独立测试与正式源码 CR 正在进行，此记录不是 M2a 放行，M2b 真实 worker 与事务收尾也尚未验收。

首轮正式隔离验证 exit 1：迁移 1 passed、API 31 setup errors；真实 fresh enabled API 因缺少显式 PostgreSQL pool 初始化而启动失败，disabled import 检查未覆盖该路径。独立 CR 同时确认这一启动缺陷及渠道幂等返回遗漏当前 owner 检查；实施修复后按新 SHA 重验，不用测试预初始化掩盖问题。

修复后的 M2a 正式源码 CR 通过，无未关闭 P1/P2：独立 lifespan 初始化/关闭自身 pool、幂等和列表当前 owner、独立活跃结果与历史游标、direct 渠道 NULL/空串兼容均已复核。16 文件中 6 个变化，原配置/DDL 38 项证据保留；实际双 API/隔离 PG 完整独立重验 34 passed、exit 0。测试退出清理、foreign cursor 与 direct 等价边界末批仍在收尾，M2b 未完成。

**M2a 最终验收通过（2026-10-01）**：末批 7 passed / 29 deselected、exit 0（3 项新增边界、4 项受影响重验），最终 37 个唯一用例均有通过证据，不能把 34+7 宣称 41 项。真实 CLI 双 API、独立 PG、actual migration、真实缓存观测与 lifespan 关闭均通过；缓存为实际 MemoryStore fallback，未宣称外部 Redis 实链。独立 CR 已确认观测 wrapper 不改变认证结果。16 个冻结文件最终 SHA 无漂移，专属 API/cache 子进程与临时目录剩余 0，临时数据库按归属清理，未新增容器/卷或重启 master/Yohar。主控批准持久 API 切片，不认证执行 worker、跨进程恢复、Web 或渠道投递。完整记录：[M2a 独立验收证据](/private/tmp/aid-agent-runner-m2a-independent-test-evidence.txt)。

M2b 仓储/计费五源子片已交独立验证，SHA 记录见 [冻结清单](/private/tmp/aid-agent-runner-m2b-storage-sha.json)。正式源码 CR 修复两处存储门槛：已持久完成意图不可被后续 checkpoint/park/不同结果覆盖；用量按收费 owner 验证完整字段，不将未知用量当已知零。首批独立 PG 验证 exit 1（14 failed、20 setup errors），均被测试 Principal 错用 `web_user` 而非实际 `user` 的 fixture 挡住，未到业务断言；仅修测试数据后重验，生产约束未放宽。资源安全门槛另有真实隔离 PG/log 连接与 SDK 本地路由 1 passed、exit 0，不等于 worker 功能验收。

**M2b 仓储/计费子片验收通过（2026-10-01）**：第二批 33 passed、1 failed（93.84s），失败为分层价格 fixture 的 `max_input=None` 不符合既有整数契约；只改为 200，原 token 和 0.38 费用断言不变。定向复验及新增边界分别 3 passed（8.65s）、2 passed（6.65s），exit 均为 0，最终 38 个唯一通过用例（16 执行存储、22 用量存储），资源安全检查另计。真实 PG 行锁验证派发、checkpoint、heartbeat、receipt-start 四类操作锁后重新判断过期，覆盖 FIFO、CAS、claim 保留、取消收尾意图与完成意图不可覆盖；真实价格表及既有算法验证收费 owner、未知用量、价格快照、分组取整和迟到事实。五文件最终 SHA 无漂移，源码 CR 无未关闭 P1/P2，主控批准该子片。用量为合成提供者事实，尚不证明实际 worker/模型、共享子执行控制、原子终态消息/record/余额/receipt/claim、BOSS 或关页执行；M2 整体继续开发。各批隔离库及专属资源已清理，未操作 master/Yohar。完整记录：[M2b 独立证据](/private/tmp/aid-agent-runner-m2b-independent-test-evidence.txt)、[最终源码清单](/private/tmp/aid-agent-runner-m2b-storage-independent-source-final.json)。

M2b 实际 worker 已按 [67 源冻结清单](/private/tmp/aid-agent-runner-m2b-worker-source-sha.json) 交正式独立测试与 CR。共享 DAL 事务收尾、中性提供者观察接口、技能 Python 子进程组合根、父子执行权/权限、会话业务计划、旧附件、Trace 和持久终态后资源清理均进入这批验收，未改已验仓储五源或 M2a 接单接口。开发兼容检查 `bash scripts/dev_test.sh --isolated-db tests/integration/test_agent_runtime_acceptance.py tests/unit/tools/test_excel_template_ai_llm.py tests/unit/test_skill_executor_env.py -p no:cacheprovider -q --tb=short --disable-warnings` 为 39 passed、exit 0（82.11s）；此前一批 26 个 runtime 用例被测试临时工厂未接收新 plan_store 参数挡住，另 12 个 skill 用例通过，独立测试者仅迁 fixture 接口后复验。fresh worker `--help`、AST 与 diff 检查通过，67 源 SHA 无漂移；这些仅是开发兼容/启动证据，不能当实际跨进程执行、终态事务或子进程失败传播已验收。

实际 worker 首批独立验证：首条默认 CLI 完整执行 1 passed、exit 0（16.71s）；第二批 `test_worker.py -k 'not commits_one_message'` 为 10 passed、1 deselected、exit 0（136.62s），合计 11 个唯一通过。使用真实双 API、CLI worker、Engine/Gateway、隔离 PG 与本地模型 IO；关闭观察请求/专属接单 API 后执行继续，真实双 worker 的会话 FIFO/异会话并行、终态更新故障整体事务回滚且重领仅 finalize、提供者缺报用量保持财务 pending、完整零用量、SIGTERM 保留附件 workspace、实际 ReadTool 多轮计价均有证据。资源补批 3 failed/5 passed、exit 1（101.66s）：5 项覆盖越界附件拒绝、PG 业务计划连续性和原 record 晚到用量差额；3 项为 fileId-only 的默认名称 unknown 与测试名称预期不一致，未证明文件读取失败。既有 Web 带 name 的输入形状经实际副本 bytes/ReadTool 读取复验 3 passed、exit 0（39.82s），不冒充 fileId-only 已通过。该批测试与来源版本分别保留，技能/child/guard/取消等仍待验收。

67 源正式 CR 主体架构符合边界，发现并修复 1 个 P1（record 工具参数未复用既有脱敏）、2 个 P2（数据库 cancel 标志错误豁免子进程权威失败；成功调用未报告 usage 时缺原 receipt 引用使 helper 错判执行失败），已通过源码复核，受影响独立测试继续。另明确 5 个实际文档工具叶函数仍 import `src.main` 获取目录，需复用可信 ToolContext 的独立存储入口并验证租户产物/实际调用图；不能用仅 CLI 启动不 import main 作为全部工具隔离证明。上述关联片与测试未关闭前不批准完整 M2。

末批修复扩展为 78 个冻结源文件，正式源码 CR 已通过且 SHA 全匹配，无剩余 P1/P2。文档工具叶函数已使用共享存储入口，Excel/PDF/Word 以可信 ToolContext 为准，显式 NULL tenant 和空技能环境均不会拾取旧身份或父配置。fileId-only 三种存储 scope 的真实读取复验 3 passed（44.29s）；技能/控制批 8 passed / 1 failed（144.40s），原失败是测试误禁主模型解释子权限错误，修正后 1 passed（24.30s），子执行后续模型与写动作仍被拒绝。真实 `/dev/full` 零尺寸标记写失败及并发取消案例证明子进程权威失败不能被 shell 或取消标志掩盖；公开 record 脱敏、实际子 checkpoint/权限、停止清理和 SIGKILL 中断保留也已有通过证据。

真实摘要与同步 SDK embedding 生产者集成 2 passed：实际 claimed Attempt、UsageScope、本地 HTTP/SDK、PG receipt 与 Finalizer，完整用量各结算一次，缺报用量保留原收据和财务 pending，不能当完整知识工具流程证明。SDK 子进程文档 tenant/global 同序复验 2 passed；保留首次 global CLI exit 1 尚未解释的事实，不将同源码复验称为已修复。当前新增执行/owner 关联 41 个唯一通过，其中 4 个为明确狭窄 owner 集成；local 原计费 owner、活跃子任务异常分支及最终清理证据仍在收尾，M2 整体尚未放行。

M3 开工只读审查提前发现 M2 公共 DTO 仍直接透传 result.messages 和附件 content；虽然未暴露 checkpoint，原模型 tool_calls 参数仍可能随结果返回。此项按原公开边界门槛退回 M2 最小修复：私有结果保持恢复/历史用途，GET/list 的结果和输入展示采用明确白名单。受影响定向验证及 CR 完成前不放行 M2。

**M2 最终验收通过（2026-10-01）**：公开 DTO 最小修复后正式受影响 CR 通过；最后实际 HTTP 安全投影和活跃 child 异常 drain 两项 2 passed / 7 deselected（29.43s），私有参数/inline 附件原值保持，实际 ReadTool 执行不受影响。父提供者失败时真实 Runtime/SubagentExecutor 的 owned child 停止，aggregate interrupted，保留已提交 child facts/workspace，不虚报终态或生成 record；此项采用正常 DI，属于窄 owner 证明。实际本地 Proxy 下发与桌面 IO 替身关联 1 passed（21.24s），重复 write_result 只保留原本地一笔 0.50，Runner 模型费用 0.01，无第二笔本地扣费。

执行/owner 最终 43 个唯一通过：38 个真实默认 CLI 案例及 5 个窄真实 owner 集成（共享控制1、空 child 环境1、summary/embedding2、活跃 child 异常 DI1）；API37、仓储/计费38另计，不合并重复重验数。API16、仓储5、worker78、文档叶13的最终清单去重87源码 SHA 全匹配，最新源码 CR 无剩余 P1/P2。专属测试进程、资源目录、租户 scaffold 均为0。集群另有一个无 agent_runners、无活跃连接且本轮账本不含的旧 aid_test 库，归属不明，排除不删，不宣称整个集群临时库为0。完整末批命令、测试源版本、原失败及清理记录见 [M2b 证据](/private/tmp/aid-agent-runner-m2b-independent-test-evidence.txt)、[最终87源清单](/private/tmp/aid-agent-runner-m2-independent-source-final.json)、[最终17测试源清单](/private/tmp/aid-agent-runner-m2-independent-tests-final.json)。主控批准进入 M3；真实外部提供者、客户 Windows/BOSS、生产启动部署、M4恢复、M5订阅及渠道送达仍按后续门槛验收，不将本地 IO 替身冒充真实外部验证。

### M2 接口与持久化细化（已审查，实施中）

以下职责、身份、事务与计费边界已通过独立设计审查。实际 schema、接口及启动链路随源码交接逐项复核；设计通过不能当作服务已经实现或验收。

- 独立 FastAPI API 与独立 CLI worker，只通过 PostgreSQL 交换请求、状态及控制。API 不创建执行 task，组合根不导入 main、渠道管理/注册、调度或 legacy 仓储。
- 私有服务调用先验证专用服务凭据，再验证最终主体：Web 复用既有 `tokens` 表 + Redis 的 opaque token 校验和数据库主体/会话权限，不新增 JWT 链；渠道按现有 channel_sessions 的平台主体、来源和路由绑定映射内部用户。凭据只用于当前认证，绝不保存进 input、checkpoint、事件或 Trace；worker 重验数据库权限，不能要求原登录 token 在整个任务期间不过期。
- runner 保存稳定 ID、真实 nullable tenant 与内部 scope_key、session/actor/source、请求键与规范化输入摘要、私有 input/版本化 checkpoint、profile 引用和指纹、执行及结算两轴状态、attempt/worker/lease/revision、公开快照/结果、record 与费用水位、控制请求及时间。创建键按 scope/actor/source 唯一，同键同输入返回原 runner，同键异输入拒绝。global scope 只给数据库确认的 platform_admin 自己的全局 Web 会话；不能作为跨租户读权限。
- Web 选择完成时写聊天历史：接单事务只持久保存 runner 输入及可见输入投影。M3 刷新从历史与待执行 runner 重建消息，按稳定标识去重；当前执行只读先前已收尾历史，加本次输入，不加载未来排队任务的消息。KF 历史保留原渠道合并后唯一写入边界，不由 Runner 终态重复写入。
- 通用执行 claim 与执行 lease 分开；领取 claim 与 queued→running 同事务。Web 持久 queue_order 保证会话最早 queued 优先，SKIP LOCKED 不能跳过被锁首条让次条取得 claim。每个 runner 共用一把控制写锁，在锁内捕获 JSON 深拷贝并按 attempt/worker/lease/expected revision 写入，避免并发 child 的旧快照后写覆盖新快照。租约失效先 interrupted，不从头重发未知工具。
- 同 cursor 完成结果、消息、稳定 record/扣费与 Web claim 释放；失败全部 rollback。Engine completed 只是执行安全点，应用事务未提交时不能公开为已收尾。缓存、Trace 回填和 Redis 清理在 commit 后。
- 普通/child/压缩/background/embedding/skill 的真实用量需稳定 receipt 和实际 model/provider，每项只有一个收费 owner；local invocation 保持原计费。旧 worker 的迟到事实不得授予新的执行权，终态后迟到事实如何结算也必须明确。

**已确认的职责边界**：M2 建立专用 `agent_runner_usage_receipts`，不与公开 events 混用；M5 再建立订阅事件表。用量事实保存稳定 call/receipt、真实 provider/model、用途、原始 usage 与计价快照，独立表达观测和结算状态。终态后迟到用量通过同一稳定 record 的补记事务处理，不修改执行结果、重开 claim 或赋予旧 worker 执行权。计费保留原累计取整口径：不能把每个调用单独 ceil 后再相加；按同计价组重算累计金额，余额只扣新旧金额差，receipt 在同事务标记结算。原价格计算抽取为可注入快照的纯入口，复用算法而不复制。

表数量不是架构约束；计费台账与订阅事件的保留、去重、排序及补偿责任不同。

### M2 开工审查决议（2026-10-01）

独立设计 CR 与主控批准进入实现。M2 内部先交持久 API/仓储切片，再交真实 worker/事务与用量切片；两块均完成独立测试和源码审查后才能标 M2 完成。不提前实现 M4 resume 或 M5 SSE，不借切片减少本阶段真实执行门槛。

- 用量状态与执行状态分开。已停止且有确定结果的执行，在消息、当时已知费用和结果事务提交后公开终态；未知费用保留 settlement_pending，不虚构费用或无限续执行 lease。迟到原始调用事实仅补记原 record，不能重启执行或释放别人的 claim。
- 保留原 billing_boundary：主模型与 inline background 同价多轮按原 record 累计取整，embedding/ASR 按各自原累计类别，技能原独立脚本调用按稳定 skill-call 单独取整。不同模型子任务按真实模型价分组属于修复旧错价，不能宣称所有异模型旧费用完全不变。价格快照计算从原函数抽纯入口，原 public 调用保留。
- 父响应可先投影，公开终态和 claim 释放前应用须确认所有拥有的 child（包括 semaphore 排队者）已停止，等待/暂停事实已保存；不能父收尾后还有未来派发者。现有公开 delegate 无 fire-and-forget 参数，不引入新 detached 产品模型。
- 统一 runner→receipt/record→tenant 锁序；首次收尾与 late settlement 都锁相同稳定 record，扣累计差额并在同事务更新 receipt 水位。最终存储失败只重试 finalize，不能重进 Engine 重发工具。
- Worker 关闭/lease 丢失属于中断，用户 cancel 才请求取消本地 invocation；服务拥有执行迭代器，观察连接不取得所有权。M2 先收口这些接口，M4 验证真正从库恢复和接续。
- durable 执行的临时 workspace 和子任务资源由 Runner 应用管理，确定终态事务提交且拥有的子进程/任务停止后才清理；内存中的 completed/cancelled 不能触发提前删除。waiting、paused、interrupted 和 finalizing 重试保留 checkpoint 引用所需资源，兼容执行路径保持原生命周期。
- checkpoint `revision` 只用于执行快照 CAS；`control_revision` 标识已提交控制请求，`view_revision` 标识公开投影变化。运行中取消递增后两者，不用观察版本使 worker 的 checkpoint CAS 失效。Web 按 view_revision 判断更新，M5 事件 seq 独立。
- 独立启动及跨进程测试使用实际 bootstrap、API 与 worker；只替换外部模型/工具 IO，不替换 Engine/manager/repository，不添加生产鉴权 bypass。子进程明确校验隔离 DB，dotenv 不能覆盖回共享库。
- API 早期 CR 收口读写权限：查询/取消核对本次可信主体及现有会话 owner，执行另查 tenant 与 profile 许可。已有 opaque token Redis 缓存可能滞后于数据库撤销/缩短到期；Runner 关键请求复用现有解析后 fresh 核对 tokens 行与期限，不把缓存命中作为唯一有效性依据，不保存原 token。渠道查询不得从待查询记录复制 actor 作为调用证明。
- 通用 Gateway/Failover 与直接模型入口只依赖 `src/llm` 的中性调用观察接口，由 Runner 组合根安装真实收据实现；非 Runner 默认保持原调用。该接口不能反向导入 AgentRunner、Engine 或数据库，不能只建立未使用的抽象。父/子持久控制共享权威失败保护：计费存储、执行权或恢复点失败即使被业务工具异常处理吞掉，也必须阻止后续派发和正常终态；关键压缩重试边界显式透传。真实 summary/embedding/skill 入口同样逐物理调用记录，helper 只引用已保存事实。
- 顶层业务计划按可信会话与当前 owner 延续，执行上下文仍按 runner 隔离；从先前已提交的私有 checkpoint 只提取业务计划快照，精确核对 scope、kind、session、actor、user、source。每 attempt 使用独立 PlanFacts，PG 快照为权威，Redis/文件仅作投影，旧 attempt 的迟到投影不能成为后续执行的输入；子任务计划继续隔离。旧未分租户键仅可在唯一会话归属证明后迁移，不作通用 fallback。

工作：

- 独立可启动服务，不导入 `main.py` 获得全局对象，不启动生产调度或渠道注册。
- 一个执行 worker，async 并发不同会话；数据库领取 queued，执行协程由服务持有。API 与 worker 跨进程通过持久状态和控制请求协作。
- 持久接单、短请求返回、状态/输出/结果查询、会话列表与取消；重复提交及领取具备数据库约束。
- 先连接真实模型及现有工具装配。进度/累计输出从公开快照读取，私有上下文不可被查询接口返回。
- 明确创建失败、运行失败、存储失败与中断；异常退出不能留下永久 running，更不能自动重发写工具。
- 执行记录和计费只由新路径的单一 owner 收尾，正常/失败/取消落库正确；最后聊天写入与结果状态提交可重入、不重复。
- M0 事务审查确认，现有 MessageDB/渠道 batch/ChatRecordDB 会自行开连接提交，SessionRecord.save 还会吞错。新增接收显式 cursor/transaction 的仓储入口，旧 public 方法保留自建事务兼容壳；Runner finalize 校验 attempt 后同事务写结果、消息、record/余额及 Web claim 释放。事件启用后结束事件也纳入；缓存失效、Trace 回填和 Redis 清理在 commit 后。
- 计费采用稳定 record/调用水位，仅真正新增费用事实时扣费；本地 invocation 仍由原台账 owner 计费。ASR 合并 follower、child、embedding、skill 用量归属不得漏记或重复收尾。

验收：提交返回时记录已存在；不同 API 进程可查询；提交连接断开后仍完成；重复提交只有一个 runner/一次工具调用；越权拒绝；一个会话不双执行；服务关闭和租约失效有明确状态；真实只读工具启动检查通过。

## 6. M3：Web 提交与轮询闭环

后端首切片已按 [17源清单](/private/tmp/aid-agent-runner-m3-backend-source-sha.json) 交独立测试与正式 CR：短 HTTP 网关/client、已认证 capabilities、原键只读查找与新提交开关、requested/actual profile 分离、独立 accepted execution_context、安全展示投影和双时间历史元数据。实施兼容自验 16 项通过，import/hash/domain 探针通过；原错误测试路径 exit4、探针误解 FastAPI IncludedRouter 均已保留为自验工具错误，不能当业务通过证据。正式 CR 要求补上游 2xx 契约异常的明确502处理，受影响 client 重冻后验证；本切片尚未验收。原 `/api/chat` 同步及旧 SSE 未迁，不冒认同步等待已可用；M3 后续兼容子片采用显式 Runner 来源标记的薄委派，保持原 JSON，未标记旧调用方沿用原路径。前端文件与后端首切片分开写入/验收。

后端修复扩展至19源后正式 CR 通过；真实独立首片目前23个唯一通过（原Web实链12、fresh历史4、HTTP契约5、视频2），最后实例/默认摘要及启动边界仍在补验。三轮历史两次原失败为测试遗漏既有 system prompt/当前时间注入，修正测试后保留完整问答顺序和未来输入不混入断言；首批另有 PG setup 连接关闭，保留环境诊断不足事实。视频原失败为测试不可变 request_data 的 JSON 报告与 Popen 归属登记，两项定向复验通过，生产未改；外部视频服务 IO 为替身，不宣称实际生成完成。前端4源已交独立验证，实施 build 通过；正式 CR 发现 discovery 失败不可重试、subject/状态代未贯穿异步与提交保护被旧快照覆盖三项关联问题，solewriter 修复后再冻结，不能用原冻结清单冒认通过。同步 opt-in 兼容小片继续实施，M3尚未整体验收。

**M3 最终验证收尾（2026-10-02）**：20个后端源与4个前端源的最新正式 CR 通过。三处前端异步问题已修复：discovery 单独成功标记、跨 await 的当前主体/状态代核对、独立提交选择保护；原 SSE 兼容及早停期间不误调用 legacy cancel 均有独立证据。当前后端31个唯一通过（普通Web25、真实同步HTTP3、实际成功DTO缺必需字段3），前端44个唯一通过（client8、展示4、composable13、原SSE19），重复复验不累加。上游2xx缺 client_request_id/实际profile_id/正整数queue_order，或 capabilities 非明确版本/布尔契约，均502而非转旧 Loop。

同步子片现采用显式 `X-AgentRunner-Transport: runner` 的原 `/api/chat` 薄委派及 `/api/chat/runners/sync`，使用同一持久接单/查询链，保持旧 JSON 字段；未标记调用方保持兼容路径。实际独立 worker 完成、重复请求原任务、同步连接 ReadTimeout 后继续执行、排队取消均通过。首同步批2 passed/1 failed的失败为测试误读数据库 snapshot 列，改为真实 GET 公开投影后通过，未放宽生产约束。原 main 路由的狭窄 opt-in 接线仍在补验，不将独立同步路由证据冒充 master 全体启动。

前端最终 `npm run build` exit0，类型、243源依赖边界和522模块产物检查通过；最终4源 SHA 与构建记录一致，复用有效结果不重复构建。实际原页面首批通过关页不取消及新页面找回终态/输入各一次，切会话后测试在接单前读取不存在的 fixture row 失败，继续修测试时序并保留停止断言；尚不记页面全部通过。页面使用专属 Chrome/Vite 且所有 API 拦截，无真实账户/业务库或客户设备。完整命令、原失败及待收尾项见 [M3 独立证据](/private/tmp/aid-agent-runner-m3-independent-test-evidence.txt)、[构建证据](/private/tmp/aid-agent-runner-m3-development-evidence.txt)。阶段待最终页面、源清单及资源清理验收，不默认启用 Web。

**M3 子片最终验收通过（2026-10-02）**：原 `main.chat` 的显式 opt-in 狭窄真实接线补验1 passed、exit0（17.09s），真实 Request、隔离 PG、独立 HTTP 与已完成 CLI 任务，无第二次模型调用；没有启动 main lifespan/渠道/调度器，不当作 master 整体启动。后端最终32个唯一通过（Web12、fresh历史4、HTTP契约8、视频2、意图2、同步4），前端44个唯一通过。原页面1个场景5个步骤最终exit0：关页不取消、重新打开终态/输入各一次、原侧栏切独立会话、原停止按钮/确认框发明确取消并显示既有徽标、无余额提示且无新增接单。原页面依赖缺失、路由/fixture时序、徽标文案和 native confirm 默认被 dismiss 的初次失败均保留；修测试后生产未改。所有页面 API 拦截、外部 origin 阻断，最终 unknownAPI/external 均为空，不冒充页面到真实 worker 的全链路。

主控核对独立命令/退出状态、有效最终构建、[24源码最终清单](/private/tmp/aid-agent-runner-m3-independent-source-final.json) 零漂移、[27测试源清单](/private/tmp/aid-agent-runner-m3-independent-tests-final.json)、原页面截图及清理，正式批准普通 Web 和 opt-in 同步子片，并解除共享源码冻结进入 M4。专属 Node/Chrome/Vite/API/worker、临时目录、tenant scaffold 和本批隔离库均清理；原不明旧库仍排除不删，master/Yohar 未动。外部模型/视频与客户设备未实测；澄清、浏览器人工协助续接、暂停/恢复与默认 Web 切换继续 M4，SSE/渠道按 M5/M6，不将子片完成宣称整套交付完成。

主要位置：Web 专用薄网关及服务 client、`src/main.py` 既有聊天兼容入口与 `/api/sessions/{id}/messages` 历史接口、`frontend/web/api/agent.ts`、`frontend/web/composables/useAgent.ts`，以及对应类型/测试。`src/saas/api/web_sessions.py` 为租户管理员审阅入口，不能复用其管理员权限作为普通用户聊天授权。

工作：

- 增加 runner API 客户端与 Web 网关，新路径按入口开关接入，旧 SSE 路径保留兼容。已认证的 `/api/chat/runners/capabilities` 返回明确配置能力；服务不可达或契约错误必须报错，不能静默降级成旧 Loop。开关只决定新提交路由，关闭后仍可查询、取消和找回已接受的 runner；服务地址与专用凭据只放服务端。
- capabilities 的 observe_existing 仅表达服务端配置是否具备观察地址/调用凭据，不按 health 推断，不证明会话无任务。默认旧安装无 Runner 配置可跳过查询；已有 runner_id、历史关联或本地 Runner pending intent 必须继续观察并明确报告配置错误。入口回退必须保留已接受任务的观察配置，不删除凭据后假称无任务。
- Runner 刷新使用的历史观察增加共享 fresh subject 与 own-session tenant scope 校验，基于当前 opaque token、用户及会话数据库事实，不能只依赖旧登录缓存和 user_id 相等。旧历史弱校验属于既有事实，新接入不据此声称所有读取都已满足当前授权；改动不重写 TokenDB 或管理员审阅权限。
- 能力开关独立于服务全局 enabled。网关保留原 default-single-subagent 自动选择与订阅策略，通过中性配置/权限解析实现，不 import main 或构造旧 Agent；instance_id 继续表达现有会话绑定，不另造实例注册系统。原 NO_CREDIT 的状态码/提示由薄适配保留，其余权限及幂等冲突保留明确错误码。
- default-single 的请求意图与实际执行 profile 分开：Web 未显式指定时，持久 requested profile=main 与 routing_policy=default_single；默认 explicit 字段不进入旧摘要。先按原请求去重，再解析实际 profile 并校验执行权限，row.profile_id/配置摘要/worker 使用实际 profile。订阅或配置在响应丢失后变化，同键同原请求仍返回原 runner；派生领域 request_data/prompt 单独版本化保存，不改已接受 input/digest。此自动路由仅允许受信 Web chat 路径。
- Web 领域输入保持原行为：实例先解析为实际 profile；视频面板的 `video_params` 经既有领域校验后保存为通用版本化 request_data，worker 重建 ToolContext 所需数据，不能只拼进 prompt 或让 Engine 识别视频业务。身份、权限和凭据来自可信边界，不能由 request_data 覆盖。新增可选字段为空/未提供时保持既有 intent 规范化形状，避免旧已接受任务的同键重试因默认字段变化被误判 409；非空数据纳入不可变输入摘要。
- 提交后显示已接受/排队，不显示已完成；保存 runner_id 与会话关系。刷新从服务查询，不只依赖前端内存或 localStorage。
- 选择过 Runner 的待确认请求绑定原 transport、请求键与不可变 payload。接单响应丢失后即使关闭新提交开关，同原键重试仍只查找已接受 runner，不能转旧 SSE；未命中则明确拒绝创建。配置变化不重新读取视频面板或改变原请求意图。
- 接单输入在终结事务才写历史，刷新须查询授权的 submitted_input 白名单（原始文本、附件展示引用、稳定 `runner_id:user` 标识），并按 runner_id/role 与已提交历史去重；不暴露 prompt_augmentations、request_data、私有 checkpoint 或工具凭据，不把后续排队输入加载进当前执行。
- 接受时间与历史顺序分开：用户展示时间通过 metadata.accepted_at/sent_at 保留，数据库 created_at 继续表达收尾时的规范回合顺序。不能将第二条排队输入的接受时间当作历史排序时间，造成 user1、user2、assistant1、assistant2；实测第三个 runner 的模型上下文仍为 user1、assistant1、user2、assistant2。
- 同一 runner 最多一个在途查询；建议活跃页面约 1–2 秒查询，错误指数退避并设上限，进入终态停止。页面隐藏可降频，浏览器整页关闭停止观察，不调用服务取消。切会话或路由组件卸载可由登录期 session 池继续观察以保留后台完成未读；clear/logout/换主体必须 detach，不由组件生命周期取消服务任务。
- 用 revision 更新公开快照；累计文本以替换/对齐方式展示，不将每次查询的全文再次追加。消息按 runner 关联去重，避免最终聊天记录和运行占位重复。
- 恢复会话页面时加载聊天历史和该会话的活跃 runner，校验路由变化后旧请求不能污染新会话。
- 刷新合并历史、近期 runner 列表与独立 active_runners，按稳定 message_id/runner_id 去重；不能仅取 active，否则 history 读取后、list 读取前终结的 runner 会消失。排队即取消的任务未写历史时，仍从持久输入与终态投影恢复原停止整轮展示，保持不收费规则。
- 明确停止按钮是取消任务，断开连接只是停止观察。两个页面可以同时观察，同一任务只执行一次。
- 不沿用旧 SSE 的 AbortError→onComplete 或 abort 后立即 idle 逻辑：观察 abort 只 detach，明确取消请求也须等待持久状态后展示停止，网络失败不能假报取消成功。
- 保留每 session 状态池、后台完成未读标记、附件草稿清理与目标会话 processing guard；toolCallId、displayName、quickOptions、图片位置、downloadableFiles、verbose eventId 和人工协助卡片由明确的用户展示投影提供，不直接公开私有事件原文。历史取消标记及无输出停止整轮记录与原行为对齐。
- Web 同步旧接口作为兼容调用方等待结果，保持原 JSON 结构；等待连接结束不取消已接单任务，不修改其他旧调用方。

M3 可先验证普通完成、轮询和刷新，完整 Web 无缝切换还依赖 M4 原 runner 等待输入/人工协助续接：waiting 保留 claim，补充消息不能只排一个新 runner 而永久堵在后面。M3/M4 交叉门槛未闭合前不默认启用新 Web 入口，也不宣称澄清和人工协助已迁移。CLI、desktop D1、`client_routes` 与尚未切换渠道保持其既有职责，不能按共用 URL 粗略切换所有调用方。

验收：浏览器关闭/刷新/切换会话后后台任务继续；重新进入能找回；提交响应丢失后不会重复任务；输出、图片、附件和最终消息不重复；计费和取消可核验；Runtime+BOSS CLI 原操作链兼容。运行前端定向测试和 build，并实际检查相关交互。

## 7. M4：暂停与中断续接

工作：

- 从 M1 边界持久保存最新 checkpoint；paused 只有保存成功才能成立，释放执行资源但保留会话逻辑位置。
- resume 重新授权、核对配置/schema/产物，再通过数据库条件更新领取；同 runner_id、新 attempt。重复继续只产生一个推进者。
- 中断后默认显式继续，不静默重跑写操作。旧 worker 的迟到结果/新派发被 fencing 拒绝；外部写许可仍沿用原工具机制。
- 本地工具按原 invocation 查询进度/结果；无法判定副作用时等待核对。子任务尚不可恢复的状态也必须明确拒绝，不能仅恢复父 messages 并丢子任务。
- 模型响应未完整保存时可重新调用，但标明输出重建和真实新用量；不保证每个 token 精确续写，不重复已记录的调用费用。
- 内部提供暂停/继续控制，Web 沿用现有停止、澄清、人机协助及消息反馈交互；不新增按钮或用户流程。恢复经受信调用和现有交互适配；暂停时的新消息排队或明确续接，不覆盖恢复点。

验收：在模型返回后、工具派发前、工具生效后/保存结果前、完成收尾时分别故障注入；从数据库恢复而非同进程内存；暂停/继续竞争不双执行；BOSS 写操作结果未知不重发；消息、结果和用量不重复。进程重启测试只在隔离测试服务中执行。

### M4 只读设计预审边界（2026-10-01）

- 原 runner 的 claim 通过专门恢复领取推进，不能简单改成 queued 而被现有 claim 永久挡住。持久恢复意图、wait_id、回复去重键/摘要与单次消费事实，新 attempt 重新授权及 fencing；同键不同回复409，同 wait 不消费两次。
- 恢复完整父/child 树及各自资源、真实配置 fingerprint、技能和 request_data，不重新做前序会话装配或取别的 runner 业务计划。父已完成但 owned child 未终结时须恢复 child/drain 或明确拒绝未知 leaf，不能直接完成父；缺可信 child 配置摘要的活跃旧状态不能猜配置继续。
- 澄清回复进入原 child 的原 tool_call，先完成模型调用配对，再注入补充用户消息并持久写历史。不能借旧澄清协调器创建新 delegate；Redis 只做投影，先保存数据库消费事实再清理。
- 本地工具已有 create→progress→checkpoint 断电窗口，需为每个物理子步骤使用现有 dedupe/business_ref 唯一路径，并在派发前核执行权。恢复只查询原可信 tenant/user/session/tool/参数/device 的 invocation；queued/running 继续观察，terminal 按原结果/费用规则处理。OverlayHeal 等多 invocation、VL、入库、专项费各阶段须有恢复事实；未支持或效果未知时等待核对，不能笼统重放整个工具。

### M4 开工审查（2026-10-02）

独立架构审查批准先实施控制账本、原 claim 的恢复领取和完整执行树装配。新增 `agent_runner_controls` 私有命令表与 usage/events 分开：采用更严格的 runner/client_request_id 唯一键，摘要包括 action、wait、目标 execution、回答及附件；先验证当前主体，再处理幂等，同键换动作或异摘要409。同一 wait 只消费一次，消费事实、checkpoint CAS 和 control revision 同事务，原接单 input/hash 不变。恢复领取核对原 claim owner，与新 attempt/lease/fence 同事务，锁后用数据库时钟；旧 attempt 不得继续写执行事实。

用户请求暂停与 worker 内部停止分离。父执行及全部 owned child 到达可恢复安全点后才确认 paused 并释放 lease；取消优先，尚未确认暂停不能提前继续。RecoveryCoordinator 只编排仓储、授权、恢复装配和工具恢复端口，不重新收纳所有工具分支。已完成结果复用，未知派发默认等待核对，不开放调用方自称完成或强制重发写工具的通用开关。




本地子片同样通过接口细审：派发 fence、phase 意图与原 invocation 插入/关联使用同 PostgreSQL cursor 事务，不能仅做锁外 precheck 后插入设备可领取任务。逐阶段核对全部可信 owner、参数、设备、分支及 retry key；专项入库和费用仍由原 owner 收尾，缺事实则等待核对。M3 最终测试期间其24源保持冻结，先新增 M4 模块及 schema，不污染有效验证版本。

Local 实际源码预审补充：原 invocation.credit_cost 仅表示设备结果终态的工具费，不证明识别、遮挡处理等专项费已结算。简历 VL 两轮调用原规则按份收费覆盖模型成本，Runner 仍保存物理调用事实与统计，但不能又按普通 token 加收费；原 overlay 模型费与 heal 专项费规则分别保留。现专项费用、简历入库和通信记录无原 invocation/item 唯一恢复事实，不能整工具 execute 重放。采用私有 checkpoint 内稳定 branch/ordinal/item phase，与原领域 DAL cursor 接口将费用/余额或业务记录 ID 和 applied fact 同事务保存；复用原领域逻辑，不增通用账本，RecoveryCoordinator 不承载领域后处理。文件引用须稳定且归属可信，旧未知写入无证据继续 verification；孤立 Local 仓储2源 CR 通过仍不等于已接通整个恢复链。

后续完整 Local 接线覆盖五组：普通 BOSS 遮挡处理的 inspect/模型选择/dismiss/retry/heal费，单份/批量简历的逐份评价/姓名门槛/识别费/稳定图片/入库/匹配字段，SendTo/Current 的 script查询与真实发送后处理，云端只读 JobsList，以及 InterviewNotify。费用复用 ClientUsageLogDB cursor，简历与沟通复用已抽 cursor；匹配仅更新已有评价字段，不新增模型，通知日志另抽原 DAL cursor。识别费已成功、后续入库失败不退款；batch 同 invocation 按 item ordinal 各自保存，不能整批重放。VL 的覆盖定价仅可信领域 scope 能设置，普通 overlay 的真实 token费保持；模型返回到解析事实之间须保存原响应/receipt或明确核对，不能随机换调用 ID。

通知实际发送不是固定两次：原 markdown 按2040 UTF-8字节分块，另有 @ 提醒，旧网络重试可能重复未知发送。Runner 路径按实际 chunk/retry/@保存 started 与可信 ACK，未知网络效果等待核对而非自动重发，单次业务日志不随块数重复。沿原配置/模板/HTTP错误解析，不把 webhook/密钥持久化；非 Runner 保旧重试。恢复端口组合各领域 adapter，不在 Engine/Worker 加 BOSS 分支或新增通用 workflow。以上为源码预审/实施范围，普通调用子片与完整领域恢复分别验收。

完整领域取消门槛还包括通知等非设备 HTTP：不能仅凭 local invocation 都已终态就放 claim，而原 HTTP 仍在途/效果未知。按原领域 started/ACK事实组合核对，取消后禁新 chunk/@/业务动作，但仍有效的原 owner 可记录已发生结果及用量；启动的派发许可与原结果的完成保存分别校验，不为避开取消把未知写入视为只读。独验使用 localhost 真实单请求/ACK屏障和原phase仓储证明，不能只替换 sender boolean。领域接线尚未验收。

M3 最终放行后解除共享源码冻结。M4 首片现将 `control_contracts`、`control_repository`、`recovery_repository`、双 DDL 与系统表文档按 [6源清单](/private/tmp/aid-agent-runner-m4-controls-source-sha.json) 正式冻结，交独立 PG 测试与源码 CR 并行。变更号为 `2026-10-02 00:33:38`；静态 AST/pycompile 自验通过。迁移首自验1失败为旧 fixture 先 DROP agent_runners 被新 controls 外键阻止，fresh DDL 初始化实际成功；独立测试按正确表顺序调整 fixture 后复验，不移除生产约束。此片仅控制接收/原 claim 恢复事务，尚不证明实际 worker 安全暂停、执行树恢复或 Browser/Local 续接；实施继续其他模块，不改冻结6源。

6源正式独立 CR 已通过，SHA 零漂移、双 DDL 内容等价，无未关闭 P1/P2；核对当前 owner 先幂等、wait 消费串行、单 resume pointer、锁后数据库时钟、原 claim gate、新 attempt/消费/checkpoint 同事务、取消/终态/新暂停竞争和私有字段白名单。外键级联符合私有表生命周期，fixture 按子表先删除，不降生产约束。独立真实 PG 测试继续；应用 ackpause 仍须验证全部 owned leaf 安全，仓储只认根 paused/unstarted 标记不能替代 worker 验收。

首批独立真实 PG 验证31 passed、exit0、0 skip（70.79s），命令 `bash ./scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_control_storage.py tests/integration/agent_runner_service/test_recovery_storage.py tests/integration/agent_runner_service/test_migrations.py -p no:cacheprovider -q --tb=short --disable-warnings`，[原始日志](/private/tmp/aid-agent-runner-m4-controls-b1.log)。真实双连接验证锁后过期拒旧 ack、控制/checkpoint 写失败整个事务回滚再重试、原 claim+新 attempt、wait 单消费、FIFO 和双 worker 竞争；开始6源 SHA 匹配，独立测试未改生产。reject_resume 边界与最终清理仍补验，不重复完整31项。当前不证明 fresh HTTP 执行授权、全树安全停稳、queued worker 零 IO 或实际进程恢复。

**M4 控制/恢复仓储子片验收通过（2026-10-02）**：补验 reject_resume 两分支2 passed、exit0（6.03s），核对原 checkpoint/claim 保全及迟到拒绝不能改已消费/已收尾意图，最终33个唯一通过（controls18、恢复领取14、迁移1）。6源前后 SHA 全匹配，源码 CR 无剩余 P1/P2；原 fresh/incremental/replay 四表约束/default/index 门槛保留。主控已核对 [独立完整证据](/private/tmp/aid-agent-runner-m4-controls-independent-test-evidence.txt)、[最终6源](/private/tmp/aid-agent-runner-m4-controls-independent-source-final.json)、[最终7测试/运行源](/private/tmp/aid-agent-runner-m4-controls-independent-tests-final.json)，批准该子片并解除冻结供后续接线。两批专属隔离库/SQL fault trigger/fixture 已清理，无新增服务/worker/浏览器/资源目录，原不明库排除未删，master/Yohar 未动。仅直接 Principal 与真实 PG 仓储证明，fresh token/执行授权、全部 child 安全停止、queued 零 IO 和实际 tree/Local/Browser 恢复仍是后续独立门槛，完整 M4 继续实施。

应用/tree 接线现正在真实续接自验，未冻结或验收。首批实链3失败：queued 场景已经实际执行完成，但测试遗漏显式 augmentation 独立 user 的原行为；测试保留 augmentation 恰一次并仅过滤其 fixture 标记后核规范业务顺序。两项 child 续接首先处理原 taskrecord.clarifying 与恢复调度竞争，但将 record.start 提前仍不足，第二批两项实际超时失败继续保留，不能以 CLI exit0 或仓储33项冒认完整恢复通过。通过原 root/child/task/控制的状态、phase、计数、稳定错误码及栈位置诊断定位，不输出正文/参数/凭据；实施者串行修生产，测试保留原 ID、单消费、调用/费用和历史断言。完整 child 恢复闭合前不交成功冻结，不默认切换 Web。

单轮 B3 实际诊断已确认更深原因：child checkpoint 和原澄清 tool 已 completed，但父又出现子澄清 tool；`ChildObserver` 为用量聚合把 child model fact 追加到父 `model_calls`，M4 保存 response/applied 后，父恢复按相同 iteration 误读子响应。独立架构复核与主控批准先拆实际 owner，而非仅增加 filter：EngineState.model_calls 只保存本 execution 的步骤；子 CP 已由 ChildControl 单独持久保存，费用由实际 PG receipts 权威负责；保带子标识的 observer 回调以兼容旧计费/context/iteration。需要子/孙总 token 的结果与展示由应用层纯树 helper 按稳定 execution/call 去重聚合，不回填父恢复列表。旧 CP 按明确归属归一化，filter 仅留兼容防御，缺失活跃子树拒绝猜恢复；不新增会计框架或表。该拆分作为本应用片冻结门槛，现代/旧归属与真实单/双澄清、子孙统计/费用继续定向复验，尚未宣称修复通过。

执行/用量归属拆分后的 B6 实际自验3 passed、exit0（111.39s），覆盖 queued 零 IO 与单/双澄清；最终工作区归属验证随后调整，不能把 B6 作为最终全源码证明，仍待独立验收。主控读代码又确认普通会话历史 GET 直返 `MessageDB.list_by_session`，而 Runner 收尾保存的内部工具消息、参数和推理未经过公共展示投影。已要求在公共历史读取边界隐藏内部工具行、对 Runner 可见元数据白名单投影，保留私有数据库与模型恢复事实，验证既有进度、附件、图片和补充用户稳定 ID；此项纳入本应用片冻结与实际 HTTP 检查门槛。

M4 应用/执行树首片现按 [36源码清单](/private/tmp/aid-agent-runner-m4-application-source-sha.json) 正式冻结，主控核对无漂移，独立测试与只读 CR 并行启动。[开发证据](/private/tmp/aid-agent-runner-m4-application-developer-evidence.txt) 记录 B7 最终资源自验1 passed、exit0（27.86s）：queued 无准备 IO、原 inline 附件、可信工作区命名空间、终态清理及默认 CLI 续原 runner；之后仅公共历史 projector 调整，全36源 AST 检查通过，但实际 HTTP 展示仍待独立测试。普通 Web 历史统一隐藏内部 role/tool_call 行，与既有前端过滤一致；Runner 可见行 metadata 白名单，legacy 可见 metadata 保留原语义且深复制，不改私有 DAL/缓存对象。当前未知 Local/Browser/skill 已派发状态仍拒绝猜测恢复，待后续适配；不把本片冻结或自验称为完整 M4 完成。

首轮只读 CR 确认须在本应用片修复的边界：异步 child task 已停止不等于 child 执行终态，父完成但子仍 waiting/paused 时须保原 claim 和父完成事实；真实触发沿既有 delegate timeout/恢复，不存在公开 `wait_for_result=false` 参数，不新增该能力。完成父的补充用户事实仍须进入最终历史，不能为写历史再跑父模型。子/孙独立 PlanFacts 必须从各自 checkpoint 保存和恢复，不借根计划，旧活跃状态有 plan_ref 无完整事实则拒绝猜测。Trace 的原 span 保留已有保障，但普通 resume 会覆写摘要，须保累计实际执行耗时及所有已存 span 统计，不把暂停时长计为执行。终态工作区清理须逐执行核可信 namespace，非法路径跳过并脱敏记录，不能依据 checkpoint 任意目录直接删除。实施先保36源冻结，当前真实测试批保留完成后再定向解冻；本片仍未放行。

独立首批 B1 实际应用15项：10 passed、5 failed（342.19s），失败涉及外属工作区、二次澄清、child 派发前两个暂停竞争及普通历史公开投影。B2 核心相关33 passed（0.29s），含现代/旧模型归属和纯树用量/展示检查，不能替代真实子孙 CLI 恢复。B3 控制/恢复仓储回归31 passed、1 failed（68.46s），取消覆盖已接受恢复的分支需诊断。主控核对首批结束36源零漂移；保留原失败日志，等待实现/fixture 分类后统一修复并定向重验，不计为已完成验收。

[首批完整独立证据](/private/tmp/aid-agent-runner-m4-application-independent-test-evidence.txt) 已分类：foreign 工作区检查被真实 SIGTERM 异常退出提前阻断，仍保 graceful shutdown 实现门槛，工作区权限另用真实 kill/lease reaper 隔离验证；二次澄清 fresh CLI 收尾退出原因尚不明，须最终源码定向复验并取脱敏异常类别。两个暂停竞争的 WriteTool 路径超出其合法租户目录，fixture 改合法路径并保结果/字节断言；Qwen 不产出 fixture 假定的 reasoning 字段，改实际已收尾 PG 行的私有 metadata 种子，仅证明读端策略，不声称 Qwen 产出；取消关闭待恢复控制为 rejected/CONTROL_EXECUTION_CLOSED 且未消费，新状态正确，仓储断言相应更新。专属进程/资源目录已清理，原不明旧测试库仍排除未删。主控定向解冻已确认的执行树/计划/统计/资源清理源码修复，控制 DTO/API/schema 保持原契约；未因测试错误改生产行为。

前端子片先保守阻止无澄清标识的 paused/interrupted 输入新建 Runner 堵在原 claim 后，使用既有输入框/错误提示保正文与附件，不新增界面；这是暂态保护，不能据此宣称 Web 已能继续。完整 M4 另交最小接口片：复用 resume 的 answer/attachments 保存显式补充输入，空 resume 原摘要不变，稳定 control:user、原 input/hash/runner/queue 不变；用户仍按原发送流程，澄清保持 reply，未知工具/浏览器不得由文本自报完成。控制202只表示已接受，前端必须沿原 control ID 查询 consumed/rejected 并明确反馈，网络重试不换键或随当前状态改动作。最终实现和实链验收后才能关闭该门槛。

修补片按 [38源码清单](/private/tmp/aid-agent-runner-m4-application-fixed-source-sha.json) 重新冻结（原36中9源变更，另纳入 aggregate_tree/trace_persist）。[开发证据](/private/tmp/aid-agent-runner-m4-application-fixed-developer-evidence.txt) 保留计划预期、任务 done 观测与 logs schema fixture 原失败；实际 queued/SIGTERM/单双澄清4项、child计划/累计Trace与已完成父/停稳等待子2项及首次装配1项自验通过。后续只读审查补闭合法计划删除/替换、原call→计划步骤持久绑定、递归恢复专属owner、真实执行后清 unstarted 标志，以及已停稳四终态原样复用；非completed失败容器仍有活后代或未知派发明确 verification，不猜 retry、不消费控制、不放原 claim。最终 Recovery 单源重冻后，独立主链14项已开验，queued 增强连续两次恢复，不提前宣称全绿。

独立窄批11 passed（9.80s）：真实 PG 私有两层控制/许可2、取消未消费1、安全暂停 phase8；这是私有 owner/边界检查，不是公开孙级 CLI。受影响兼容32 passed（89.79s）：真实 Runtime26、D1 seam5、统计1，外部 IO fixture 没替换 Engine；不调用途中修订的 Recovery.prepare。旧 Engine31/公开投影1源码不变沿用有效结果，统计与首批重复按唯一案例计算。前端旧6源独立 client13/composable18 共31 passed（0.882s），发送发现竞态仍须补：决定回复或新建前完成当前会话可信 discovery，失败保输入不 fallback；原构建与35自验不能替代修后检查。两片继续独立验收，不把暂态输入保护或私有树检查称为完整 Web 恢复。

**当前应用片验收边界（2026-10-02）**：最终38源 CR 无剩余 P1/P2，Recovery 最新 SHA 为 `e6a169d57fd4dd9a44d05e71b16ec78231e27a179ff9e9b7ae669792cf664322`。独立实际主链14项为11 passed、3 failed（435.38s），源码零漂移；已通过两次 queued 恢复、单双澄清、原 child 计划/累计 Trace、已完成父等待原子任务、SIGTERM 与附件链。二次澄清本轮通过，但首轮异常原因仍未定位，不能倒推根因。两项暂停恢复在绝对共享产物根下仍无目标文件，已定位 WriteTool 使用进程 cwd 目录的真实独立服务缺陷；第三项普通历史检查被测试对 TEXT metadata 使用 jsonb 运算阻断。修 SQL cast 后，实际普通 HTTP 历史私有字段隐藏、可见消息/附件保留独立通过；同批相对路径的两项仅诊断旧 cwd 写入语义，不顶替绝对共享根门槛。

共享产物修复限中性 storage helper、write/cp 及必要 main 文件读取薄壳：可信 ToolCtx 的 tenant=None 是明确匿名 owner，无上下文不伪造该身份；写入、注册、所属目录缓存恢复共用配置根。不同 cwd 的真实 Worker write→cp→main 下载、可信匿名与外租户/符号链接边界待独立验收。旧无主体 opaque fileId 公共交付 URL 保既有语义，只调整既有目录基准，不增加登录要求或扩大扫描范围；Runner scoped lookup 不回落全租户扫描。旧自验134 passed、18 failed保留，失败 fixture 需设置隔离共享根；曾落入默认根的具名 fixture 仅以文件名、内容和运行时间三证归属后精确清理。

共享4源首次 CR 确认 P1：直接对 owner 根 resolve 再作为允许边界，会认可 tenant/conversation 指向另一租户或外目录的符号链接，读取与生成均受影响。修复必须以可信 configured mount 下未跳转的 owner 路径为基准，mkdir 前核父链/scene，读取拒绝跳转根而非重新定义所属范围；保留实际复现及修后断言。当前片尚未放行。

canonical anchor 修后4源 CR 无 P1/P2、SHA 无漂移；旧源两个真实符号链接复现先失败保留。共享独立11项为10 passed、1 failed（111.03s）：两项绝对根暂停续接、可信 NULL 的实际 write/cp→不同 cwd 的 main 下载、外租户 cp 拒绝及6 anchor 边界通过；tenantA fresh CLI 在 await finalizer 处 CancelledError。该批私有库/fixture 已清，无法补造旧失败时的数据库终态，原失败不归因猜测或简单重试盖掉。源码审查另确认 heartbeat 竞态：本 attempt 已合法提交终态/停稳并清 lease，但 finalizer 的提交后投影或 park 返回仍未结束，心跳将其当失租取消 owner。单独实际 PG 提交后屏障复现、精确释放证明修复与反向失租验证进行中；不据此倒推首轮二次澄清未知异常。应用/tree 验收继续 hold。

共享根相关 path/write/cp 全文件独立兼容152 passed、exit0（1.81s），不是旧开发134或其18项子集累加。只清理6个明确归属的开发 fixture 文件，保完整目录及其他文件。心跳旧源实际 PG 因果复现2 failed、3 passed（10.11s）：真实终态提交后与暂停 ack 返回前误取消；实际过期、换 attempt、SQL 故障仍正确中断。修复4源以原事务保存私有 released_attempt 的 worker/attempt/status/reason，合法结果或安全 CP 才 closed，Worker 不再取消该 owner，真正失租路径不变。修后8个唯一生命周期案例有效：初批7 passed、1个 rowgone fixture 误望 None，改核实际 RunnerError404 后定向1 passed；完整事务回滚补验及正式 CR 待收尾。此片不调用 Engine/Runtime，不替代 tenantA 默认 CLI 与新输入恢复门槛。[聚合独立证据](/private/tmp/aid-agent-runner-m4-fixed-independent-test-evidence.txt) 保留所有首失败及逐片 source/test SHA。

**心跳生命周期子片验收通过（2026-10-02）**：4源正式 CR 无剩余 P1/P2，独立10个唯一通过（8个提交返回窗/真实失效与伪关闭反向、2个终态/暂停 SQL 晚失败全事务回滚后原意图重试）。[冻结4源](/private/tmp/aid-agent-runner-m4-heartbeat-source-sha.json) 与独立开始/结束 SHA 匹配。费用/历史/receipt-applied/claim/control-consume及释放证明一起回滚，去 fault 后一次提交；不是只改状态让测试过。未调用 Runtime/Engine，原失败日志和 rowgone fixture 修正保留。Worker 同源还包含新输入首次装配 hook，此结论仅生命周期分支，不能提前认证完整新输入片。

resume+input 现按 [9源清单](/private/tmp/aid-agent-runner-m4-resume-input-source-sha.json) 与更新前端6源冻结：同 Runner resume 正文/附件、根专属 queue→appended 水位、unstarted 原历史/原输入后注入、旧响应/工具先配对、已完成父恢复原子树后根新轮次、原预算预检及原控制 ID 查询。开发 AST8/build exit0（5.82s，522模块）；真实跨进程输入接续、旧行为回归、正式 CR 及原页面检查继续独立执行，不以静态/构建宣布 Web 完整恢复。

新输入9源及关联前端6源最终正式 CR 无 P1/P2、SHA 全匹配。末两源修复已保存 FAILED 根恢复时被 Engine 重新 RUNNING 的实际路径：owned child 恢复失败后根 FAILED 被保存，stage 前失租保其 CP；durable restored 全停稳终态直接保存/返回收尾，不再启动模型。非空输入不复活 failed/cancelled/limit，消费前拒绝并保输入；只有合法可继续或明确 completed 根新要求推进新轮。提前保存后标资源已移交，避免清理已持久的回复工作区。旧兼容 final_output 调用窗口记录为原路径边界，目前唯一 producer 由 durable 分支禁用，不扩作本片假设性修复。实际9个跨进程形态及 core34 独验已开始，尚未宣布新输入交付。

新输入独验已得到9个实际形态的通过证据：首批5 passed/4 failed，失败为附件应由真实 ReadTool 读取的断言接线、补充正文放错案例，以及 FAILED 探针过期 SQL 未 commit；只修 fixture 后相关3通过、FAILED1定向通过（20.93s）。FAILED 来自真实外部 HTTP400，额外 owner checkpoint 保存由 DI 提供，验证既有 Failed 状态恢复收尾，不称为默认 Engine 自动保存 failed 的整链证明。财务补验2 passed（52.99s），逐次实际 receipt 唯一、原 record 精准 token/0.01 与租户一次扣费均成立；最终受影响 Runtime26/D1 seam5/统计1共32 passed（89.59s），替代旧版本兼容记录，不重复累计。tenantA 默认 CLI 的共享文件写入、复制及不同 cwd 的 main HTTP 下载本轮通过，原个别失败根因仍不倒推。

原界面检查已通过同 Runner 补充正文/原上传附件以及关闭刷新找回2步骤，但拒绝反馈步骤失败：controls 已返回 rejected，useAgent 只更新 error ref，ChatContainer 未显示它，用户无可见提示。已列为迁移阻断项，由实现角色按现有错误反馈方式修复，随后定向重验页面、composable 与 build；不通过修改为内存断言掩盖问题。该页面全部 API 为拦截替身，真实 Worker/PG 链由前述独验分别证明；完整 M4 仍有 Local/Browser owner 接续待完成。

Local 未接线6源已冻结：[源清单](/private/tmp/aid-agent-runner-m4-local-six-source-sha.json)、[开发证据](/private/tmp/aid-agent-runner-m4-local-six-developer-evidence.txt)。原 invocation cursor DAL、原 owner 阶段关联仓储、中性 lifecycle Protocol、领域阶段共事务仓储及招聘简历/沟通记录 cursor 入口不改变客户协议。旧招聘兼容63 passed（49.41s）及静态检查通过，只证明旧 wrapper 兼容；必须另经独立真实 PG 验证 invocation 与 checkpoint 原子关联、实际领域 writer 与阶段事实同事务、锁后过期全回滚，再接 Runtime/Proxy/Worker。当前不宣称多阶段恢复、专项费用或客户设备实链已完成。

**后端补充输入及共享产物分片验收通过（2026-10-02）**：主控核对 [结束源码清单](/private/tmp/aid-agent-runner-m4-resume-input-independent-source-final-before-ui.json) 中后端15源与 [结束测试清单](/private/tmp/aid-agent-runner-m4-resume-input-independent-tests-final-before-ui.json) 中后端4源均匹配；源码正式 CR 与实际9形态、财务2和受影响兼容32证据对应，共享 anchor/路径152与实际CLI下载门槛也已闭合。阶段数不跨批累加，DI边界及原失败保留。允许后端继续 Local 接线；今后修改 Runtime/Worker 等共用源须按新批次验受影响行为，不能沿用本次 SHA 当最终证明。前端反馈仍有三项 P2：旧错误刷新误提示、同文案压掉后续新操作提示、跨会话恢复旧附件；可选 wait_id 类型也需与 generic resume 公共契约一致。页面修后独验另行放行，完整 M4 未完成。

独立测试者另归档 [后端15源最终清单](/private/tmp/aid-agent-runner-m4-resume-input-backend-source-final.json) 与 [后端10测试源最终清单](/private/tmp/aid-agent-runner-m4-resume-input-backend-tests-final.json)，将原聚合清单的历史 useAgent 与后续页面叶修改区分。各隔离 wrapper 已退出并回收自己的数据库，本片后端子进程剩余0，页面资源 cleanup=true；旧不明测试库排除未删除，未操作 master/Yohar，未提交或部署。

**Local 仓储/领域事务接口分片验收通过（2026-10-02）**：独立真实 PG 最终17 passed、exit0（62.69s），6源正式 CR 与开始/结束/最终 SHA 一致，主控核对 [独立证据](/private/tmp/aid-agent-runner-m4-local-six-independent-evidence.txt)、[源码清单](/private/tmp/aid-agent-runner-m4-local-six-independent-source-final.json)、[测试清单](/private/tmp/aid-agent-runner-m4-local-six-independent-tests-final.json)。原 root/child invocation关联、完整不可变意图、外属/撤销设备拒绝、设备真实行锁跨租约后的 invocation+CP 全回滚、简历及沟通记录真实 cursor 写入与 CP 同事务、失败重试一次均通过；未用 fake complete_phase 替代领域写入。首批15 passed/2 failed/17 teardown errors为隔离 fixture 未调用现有招聘领域建表函数，修 fixture 后原断言保留；失败和成功临时库均已从 catalog 消失，旧不明库保留。允许接普通原 invocation 实际恢复，专项费用、VL、批量及 heal 分阶段续接、Browser 仍待实现/验证，本片不代表客户设备或完整 Local 已交付。

**Web 补充输入与错误反馈分片验收通过（2026-10-02）**：三项提示/草稿 P2、响应丢失后首次 rejected 被误当历史的新窗口及可选 wait_id 类型均修复。当前原请求键在本次用户操作保存 live proof，服务快照找回后首个拒绝仍提示；历史首次装配不弹旧错，同轮轮询去重，新用户操作重新反馈。独立41个唯一有效用例（composable24、client13、未变 mapper4），首批23 passed/1个新增测试错误期待跨会话 canRestore=false，保真实 auth/generation 回调与页面独立 SID 守卫后定向1通过；新会话正文/附件及提示不污染断言保留。最终 build52069 exit0（5.82s，522模块），正式 CR 无 P1/P2；[前端最终6源](/private/tmp/aid-agent-runner-m4-feedback-independent-source-final.json) 与 [6测试源](/private/tmp/aid-agent-runner-m4-feedback-independent-tests-final.json) 经主控核对匹配。

原页面4步骤通过：同 Runner 续正文/附件、关闭刷新找回一次、另一会话拒绝时正文和附件恢复并见安全中文提示、回原会话不串消息；主控实际查看截图。页面全部业务 API 拦截，newRunnerPOST/unknownAPI/外网逃逸为0，不称为页面直连真实 Worker 的 E2E；后端实际链由前述分片独验分别证明。[页面证据](/private/tmp/aid-agent-runner-m4-page-evidence.json)、[拒绝截图](/private/tmp/aid-agent-runner-m4-page-rejected-feedback.png)、[资源账本](/private/tmp/aid-agent-runner-m4-input-feedback-resource-ledger.json) 已归档，专属浏览器/Vite/临时目录已清；仅截图重复不加案例。保留原生产反馈缺陷、类型构建失败与 fixture 修正记录。完整 M4 继续 Local/Browser，不默认切换 Web，不提交或部署。

普通 Local 原 invocation 实际接线按 [16源清单](/private/tmp/aid-agent-runner-m4-local-ordinary-source-sha.json) 冻结：[开发证据](/private/tmp/aid-agent-runner-m4-local-ordinary-developer-evidence.txt) 为兼容95 passed（2.96s）及既有实际默认 CLI/Proxy/PG、桌面 IO 替身1 passed（23.78s），仅开发自验。首兼容9 passed/KeyboardInterrupt（103.23s）为新设备 helper 时区回归，独占批中断后修正：legacy naive 使用原本地时刻语义，锁定数据库 TIMESTAMP 的 last_seen 与同会话 clock_timestamp()::timestamp 比较。原日志保留，不称首批成功。新派发锁后复核 selected/active/在线/catalog，旧已完成 invocation 读取不重新选设备；原请求、期限及参数保持。仅普通继承 execute 的工具恢复，特殊工具初跑保旧后处理，恢复尚须 verification；专项多阶段另片推进。

正式 CR 完整16源起止 SHA 无漂移，发现两项取消执行权阻断：Worker 从 fresh row 重建 attempt，旧执行可能借新 owner 元组；特殊工具无普通 Local owner 时，durable livecancel 走 legacy 接口绕过归属及 fence。已交单一实现者，仅定向修 Worker/LocalAdapter 两源：执行权必须来自本次执行最初捕获的不可变 attempt；所有 durable 取消沿共享 root lock 和严格原 invocation 证明。当前独验先保持冻结、留本批证据，批结束再修和重验，未批准该实际接线片。无领域恢复/客户协议改动；旧 Runtime/D1 等非 durable 调用保原取消路径。

普通 Local 首次独验为10 passed / 4 failed / 1 deselected（291.25s），16源零漂移。通过项覆盖已完成原调用复用、撤销设备后的原结果读取、费用只扣一次、在途恢复、取消及真实 PG 反向证明；仍不作为最终接线验收。三项恢复拒绝实际正确，失败是新测试误把 `consumed_at` 当作消费凭据：现有契约在拒绝关闭时也记录该时间。保原仓储语义，改以 rejected、consumed_attempt=NULL、原 attempt/checkpoint/input/claim 和外部动作未变证明拒绝，保留首失败。另一项 SIGTERM 后状态未及时中断，旧临时库已回收，原因未定。

另一次独立真实 PG 时序探针复现停机缺口（1 failed，19.44s）：原 bind 事务提交返回与统计 checkpoint 保存竞争，统计 CAS 失败后同一 try 内的 interrupt 被跳过，CLI exit0 而 Runner 仍 running。仅控制真实线程/提交时序，未替换执行、SQL 或状态结果；不能倒推上述旧 SIGTERM 失败原因。要求同一原 attempt 的中断不依赖耗时统计保存成功，失去执行权时仍不得改变新 owner。取消审查还发现原设备 invocation 为 cancel_requested 时尚未停稳：必须等可信原调用终态才能宣布 cancelled、释放 claim 和资源；未知效果或超期无确认保留占有并要求核对。三项修复与该收尾门槛待定向独验/CR，普通 Local 尚未放行。

统一修复范围扩为 Worker、Runtime LocalAdapter、local_recovery、ExecutionRepository 四源，不新增 DDL 或 Engine 领域分支。原调用有限查询期间保心跳；无法确认则保存版本/归属明确的 cancel_completion_blocked、公开等待核对和精确 released_attempt，释放执行 lease 但保逻辑 claim/资源，取消领取排除该事实。低频有界检查仅核原完整 refs，全部原调用晚到可信终态后，按仍无有效 lease 的原状态/attempt/revision 清 blocked，使原取消收尾继续；不能重复请求设备取消、启动模型或重派工具。已有 pending_finalization 仍只重试结算。当前为实施决议，不代表源码或完整状态矩阵已验收。

首修复版按 [17源清单](/private/tmp/aid-agent-runner-m4-local-ordinary-fixed-first-source-sha.json) 冻结，原13源不变；开发原实际普通链1 passed（22.83s）。独立首短批7 passed（57.44s）：真实 Worker/特殊 Adapter 的执行权、设备锁后权限变化及停机提交竞争正反验证；关联心跳5 passed（15.46s）：实际取消核对提交返回窗、伪造释放证明拒绝及旧合法暂停/新epoch保护，均开始/结束17源匹配。CLI 后续7 passed / 1 failed（188.26s），唯一失败在旧测试未计入合法 blocked→late-known 的新 attempt，设备未停稳不放 claim、原结果后收尾、拒绝及原期限的断言已通过；保失败并定向增强执行次序/费用一次证明。定向 CR 再确认只读 sweep 误用 invocation 写锁，会串行阻塞其他会话领取并可能因30秒查询超时退出 Worker；独立真实持锁单例1 failed（6.31s）复现，释放锁并等待原 SQL 线程收尾后回收临时库。随后仅改只读分支为普通 SELECT，真正请求取消保原锁/fence；[当前17源](/private/tmp/aid-agent-runner-m4-local-ordinary-fixed-current-source-sha.json) 单源复核通过，无剩余 P0/P1/P2，最终持锁/晚到结果独验尚待收尾，普通 Local 仍未验收。

**普通 Local 原调用恢复与取消子片验收通过（2026-10-02）**：最终独验26个唯一集成场景（12默认CLI、14窄owner/真实PG时序/契约，含原Proxy短期限配置DI），另3项既有心跳关联复验和95项既有Local兼容通过；逐批计数不相加。最终持锁场景通过，晚到结果取消单例1 passed（30.46s），95兼容exit0（1.91s）。主控核对 [完整证据](/private/tmp/aid-agent-runner-m4-local-ordinary-independent-evidence.txt)、[逐项范围与复用依据](/private/tmp/aid-agent-runner-m4-local-ordinary-independent-inventory.json)、[最终17源](/private/tmp/aid-agent-runner-m4-local-ordinary-independent-source-final.json)、[18测试/运行源](/private/tmp/aid-agent-runner-m4-local-ordinary-independent-tests-final.json) 无漂移，正式CR无剩余P0/P1/P2。

设备原ACK前保原claim/资源并阻止同会话后任务；原可信ACK后由同Runner新attempt只收尾，不重发工具/模型。未知效果、缺绑定和未实现heal续接明确核对、不消费继续命令；只读观察不阻其他会话领取。正常原调用结果复用、原工具费和取消时的实际费用按各自旧规则核验，不将取消费用伪填为成功工具费。SIGTERM提交竞争与原queued首失败分开记录，不倒推未知原因；首错误、夹具时点/attempt修正及相应loaded hash均保留。外部模型/桌面动作替身，真实PG/API/Worker/Runtime/Engine/原write_result及费用DAL；非全部真实客户Windows验证。所有专属DB连接/进程/临时资源树为0，[清理证据](/private/tmp/aid-agent-runner-m4-local-ordinary-independent-cleanup.json) 保旧不明库、master/Yohar不动。批准解除普通17源冻结，进入完整领域多阶段接线；特殊工具目前仅首调用取消权限通过，不宣称后处理恢复已完成。完整M4/默认Web切换、Browser、订阅及三个渠道继续开发，不提交或部署。

前端6源最终发送发现保护 CR 无 P1/P2、SHA 无漂移，独立 composable20项通过（840ms），复用未变 client13/mapper4，合计37个唯一案例；最终 build exit0，类型/依赖边界及522模块检查通过。发现未结束或失败时保输入与附件，不能先创建新 Runner 或回退旧路径；控制按原 ID 查询消费/拒绝。此片暂态拒绝普通 paused/interrupted 新输入，不等于完整继续交付，下一最小片必须把既有发送交互接到同一 Runner 的 resume+answer/attachments，并补原页面行为验证。

### M4 完整 Local A 接线边界（2026-10-02）

A 片收口普通 BOSS 遮挡处理，不提前宣称五组专项工具全部接通。领域模型的 started/response 事实由中立 lifecycle 接口和应用 owner 保存，不能写入 Engine 的主模型恢复步骤；模型调用实际收据及统计保留，可信领域价格权限与普通 purpose 提示分开。已收到结果时允许原有效 attempt 在取消后保存原观测事实，但禁止新动作，不能借新 owner 保存旧结果。

设备失败不证明动作没有效果：只有原结果明确 `effect=none` 才自动 inspect/dismiss/retry；partial/applied/缺失效果须核对，恢复前同谓词拒绝消费继续命令。模型阶段已 started 而实际 receipt.start 被暂停阻止时，只有原阶段全部对应收据为零，才允许锁内按原 phase/intent 和当前不可变 attempt 重新授权；任何 started/unknown/observed 收据均阻止新调用。首次执行、恢复预检及事务重核共用该事实，不能按当前 attempt 或已完成收据过滤来误判未派发。上述实现处于开发自验/冻结前，正式独验及源码验收仍待完成。

A v1 现按 [39源码清单](/private/tmp/aid-agent-runner-m4-domain-a-v1-source-sha.json) 正式冻结（17本片变源及22真实装配/Gateway/provider/Trace未变依赖），主控核对零漂移，交独立测试与源码 CR。开发真实默认 CLI 的正常链最终1 passed、exit0（31.38s）：原四个设备调用、模型选择、重试、原工具费0.50及 heal费2各一次、三笔物理模型收据54tokens、Runner模型费0.01、Engine仅自己两步骤，领域响应另存。首次1 failed（30.09s）为新测试错查原费用表字段，修为实际 model 列后保全部断言及原失败，不改生产来追测试。早期80/62开发兼容窗口早于末版权限/效果/rearm调整，只作基线，不能充当末版独验；[交接契约](/private/tmp/aid-agent-runner-m4-domain-a-v1-handoff.txt)、[开发证据](/private/tmp/aid-agent-runner-m4-domain-a-developer-evidence.txt) 保留。首独验计划11真实形态（正常1、效果拒绝6、中断恢复4），零收据/原观测/可信价格窄 owner 另批；尚未有独验结论。B 只准备不冲突的未装配新模块，共用39源保持冻结；完整Local、Browser和M4未完成。

A v1 正式只读 CR 完成：39源起止零漂移，无剩余 P0/P1/P2；主/子实际归属、原设备/参数/期限、效果门槛、模型 receipt/rearm、观测与新派发分离、费用同事务及兼容路径通过源码审查。源码 gate 放行，独立真实链测试仍运行中，共用源继续冻结；不将 CR 通过记为 A/B 或完整 M4 验收。

A 首批独立真实链 B1 已11 passed、exit0（328.50s）：默认 CLI 正常链1、协议合法但不可安全 heal 的六种效果/错误组合6、中断恢复4；恢复4中两项原在途 inspect/retry 使用自然 SIGTERM/SIGKILL，两项 choice/fee 使用真实事务提交返回时序探针，续接均为未装饰默认 CLI。原编号/设备/事实/费用复用与 Engine 模型步骤归属断言通过，39源码和10加载测试/辅助源起止全匹配，无生产失败。[独立证据](/private/tmp/aid-agent-runner-m4-full-local-independent-evidence.txt) 保准确范围；有限可信价格、零收据 rearm、取消后原模型观测另批待验，专属资源清理待实际核对，不提前标 A 验收或清理为零。

B 目前仅准备未装配领域流程及有限 DAL/callback 壳，保持 A 共用39源冻结。源码预审核对旧 wrapper 别名/岗位关联、两轮 VL 及姓名门、识别费与入库分事务、既有匹配字段、稳定归属/hash图片及原通知 chunk；首次模型配置错误保每 item 失败，配置变化或未知通知不能掩盖已有原阶段。开发旧 VL 兼容24 passed、exit0（0.49s），9准备源起止匹配，详见 [B准备证据](/private/tmp/aid-agent-runner-m4-domain-b-prewire-developer-evidence.txt)。这只证明旧接口局部兼容，不证明新 producer、恢复、通知取消 gate 或完整 B 已接线；A 最后 owner 门槛后再定版装配及正式验收。

A 有限 owner 首 B2 为13 failed、exit1（118.37s），尚无已确认 A 生产阻断：新 probe 漏给真实 Authorizer 必需配置，另外误用未接 observer 的 `Gateway.chat_direct` 而非 A overlay 实际 `chat_lite`，未走到预期原收据断言。保首测试加载版本与日志，修测试装配及实际调用路径后定向重验13，不重跑未变的 B1。该测试装配错误同时暴露 B 实接必修：简历 VL 确实调用 `chat_direct`，其当前直接 `provider.chat` 无持久收据/派发校验；B 须在真实物理边界接中性 observer，无 Runner 时保持旧规则，不额外按 token 重复收费。实际 Detail/Batch→evaluate_resume→chat_direct 是 B 门槛，A chat_lite 的可信价格窄接口证明不能替代真实 VL producer。主控及独立 CR 均源读确认，当前 A39不改，B尚未装配，独验仍未结束。

A B2 修测试后定向13 passed、exit0（108.34s），生产39及加载7源起止零漂移：有限 covered 正向2、提示正反3、真实 foreign/sibling 响应2、真实 HTTP 取消后原结果保存1、零收据原阶段 rearm1、四种已有收据/旧 attempt 拒重发4。初始 ToolFact、零 IO 安全 park 及旧 epoch 替换为明示窄契约 fixture，真实 lifecycle/授权/PG/Gateway/外部 HTTP 保留；不称完整 Engine pause 或实际 Detail/Batch producer 已通过。原首13失败留档，未变 B1不重跑；最后34项受影响旧 overlay/领域 cursor 兼容及实际清理/最终清单仍收尾，共同39尚未解除冻结。

**Local 遮挡领域 A v1 分片正式验收通过（2026-10-02）**：独立24个新形态（9默认 CLI、2真实事务提交返回探针后默认 CLI、13明示窄 owner/真实 PG/Gateway HTTP），另34受影响旧兼容（overlay17、领域 cursor/仓储17），各批结果不重复累加。源码 CR 无剩余 P0/P1/P2；主控核对 [独立证据](/private/tmp/aid-agent-runner-m4-full-local-independent-evidence.txt)、[范围与关联源复用依据](/private/tmp/aid-agent-runner-m4-domain-a-independent-inventory.json)、[最终39源码](/private/tmp/aid-agent-runner-m4-domain-a-independent-source-final.json) 与 [15测试/辅助源](/private/tmp/aid-agent-runner-m4-domain-a-independent-tests-final.json) 零漂移。关联 DAL 三叶未捕 B3开始快照，采用运行前已存在源清单/原 HEAD、结束 hash/mtime及唯一写入者窗口事实核对有效复用，明确不补造开始记录。

外部模型和桌面执行仍为替身，真实 API/CLI/Engine/Runtime/PG/原调用结果/计费 owner；实际客户设备及 B 的真实 Detail/Batch VL未认证。首开发 SQL列错误和独验 Authorizer/调用链装配错误留档，修测试后原断言通过，没有本片生产修复或版本漂移。全部本批专属库、进程及临时根为0，[清理证据](/private/tmp/aid-agent-runner-m4-domain-a-independent-cleanup.json) 保留原不明旧库不删，master/Yohar未动。批准解除 A39冻结，进入专项 B实际接线；Gateway直连物理观察、特殊工具恢复、通知外写取消、Browser和最终 Web核对页面门槛继续，不默认切 Web、不提交或部署。共用源后续变更按新片受影响范围重验，A既有清单不冒充未来B最终状态。

B 后续按两片实接：B1 为 Detail/Batch、JobsList、SendTo/Current（含 script 只读分支），B2 为 InterviewNotify 及外部 HTTP 取消证明。B1 用有限 exact 注册类型选择领域适配器，复用原设备执行与中立 lifecycle，Engine/Worker 不加入业务名称。特殊设备调用依然约束原 refs/参数/设备/期限；script/Jobs仅各自可信云只读分支可恢复，不能将整发送工具声明为只读。SendTo 已知 `sent && !dry_run` 原 ACK 后的沟通审计可按精确原事实提交，取消禁止新设备动作而不抹掉已发送事实；SendCurrent不新增沟通记录。B1 首开发门槛是默认 CLI 真实 Detail→VL直连收据→姓名门/原识别费→稳定图片→入库/匹配及实际原文件薄 API 字节读取；外属 Runner 附件引用拒绝另证，保持旧 opaque 公开下载 URL 的原兼容政策，不冒称旧 public URL 做租户拒绝。当前为装配及测试准备，未冻结或验收。

B1 v1 现按 [56源码清单](/private/tmp/aid-agent-runner-m4-domain-b1-v1-source-sha.json) 正式冻结，主控核对零漂移，交独立16有界实际形态与正式源码 CR。此前正常8开发形态均通过（Detail1/33.08s，其他7/153.00s），随后有限 covered 原调用/item/policy/真实物理目标同 cursor 谓词补强；末版只补受影响实际 Detail/Batch2 passed、exit0（64.13s），51源起止匹配。前版8不冒充末版全部证据，开发首 Detail50源为运行中/结束见证，未补造开始加载快照。[完整契约](/private/tmp/aid-agent-runner-m4-domain-b1-v1-handoff.txt)、[开发证据](/private/tmp/aid-agent-runner-m4-domain-b1-developer-evidence.txt) 保这些范围。原 A 无设备 origin 的有限 covered 正向接口仅属历史契约，B实际 producer 取代其正向授权证据；未上生产的B无旧任务凭空兼容问题。Notify B2 只在冻结外准备，共用56保持稳定；B1及完整M4尚未验收。

B1 v1 完整正式 CR 结束：56源码起止零漂移，无 P0/P1，唯一 P2 是图片目录/open/write/fsync 的真实 `OSError` 没转为每 item 业务失败，导致整个 Batch停而跳过后续可处理简历。旧规则要求只报告当前入库失败、保已识别费、继续下份；Checkpoint/lease/authority 异常仍须整树停止。单一实施者已确认最小单源修复，待首独验8批结束及真实 OS 旧版单例复现后再改 `resume_flow`，新 v2 清单与原 v1证据分开；未受影响的 Jobs/script/Send 不机械重跑，正式独验及该 P2修复尚未完成。

B1 首独验8 passed、exit0（187.56s），56生产源及18测试/辅助源起止零漂移：真实 Detail/Batch/姓名门、Jobs、两种 script、两种已发送 ACK 均检查原费用/数据库/物理收据；图片薄 API 字节可读及外属 Runner 附件引用拒绝按各自范围验证。随后旧 v1 的真实文件系统单例1 failed、exit1（38.75s）：普通文件阻断图片目录创建，第一份 VL/识别费各1、第二份 VL0，父 Runner仍 completed且 claim已释放，确认普通存储错误吞掉后续项目，并非权限或 lease 中断。该单例自己的启动前快照未生成，复用此前冻结清单及唯一写入者事实并保运行中/结束见证，未补造启动记录。

已将 `resume_flow` 单源修成 `OSError`→当前项 `RESUME_STORE_FAILED`，其余55源码保持原 SHA；[B1 v2清单](/private/tmp/aid-agent-runner-m4-domain-b1-v2-source-sha.json) 经主控核对56零漂移。[v2交接](/private/tmp/aid-agent-runner-m4-domain-b1-v2-handoff.txt) 保旧版本及失败证据，独验续跑受影响恢复/边界与原真实 OS断言，未受影响查询/发送按范围复用首8；定向 CR和正式验收尚未结束。通知 B2仍仅在冻结外准备。

B1 v2 定向正式 CR通过，唯一图片存储 P2关闭，无剩余 P0/P1/P2，56源起止零漂移；其余55复用 v1完整审查。捕获位置只在图片准备阶段，既有识别费在此前独立提交，cursor入库尚未发生；CheckpointFailure及租约/停止异常不会被 `OSError`捕获。功能结论仍等待独立最终相关测试。

B1 v2后批独验9 passed、exit0（272.60s），56生产源及20测试/辅助源完整启动前/结束见证零漂移：真实 OS失败只影响当前 item，三种领域提交返回后进程退出由原 Runner默认 CLI续接，SendTo原 ACK后恢复/取消均保审计1，姓名失败后续 item继续，真实 PG收据写失败停树，VL缺 usage记 unknown审计而不伪造零用量。两批合计17个不同新形态（首8+后9），旧 OS失败复现不重复计；关联 owner11及 phase仓储17、最终清单/清理尚未结束，B1共同源仍冻结。

**Local专项 B1 v2分片正式验收通过（2026-10-02）**：17个不同新形态（12默认CLI、5实际领域提交返回时机探针及原CLI续接/取消），另28受影响关联（owner11、真实PG阶段仓储17），关联批28 passed、2 deselected、exit0（155.31s）；两项历史 A covered正向 fixture缺原截图/policy故明确排除，B真实producer代替其正向证明。主控核 [完整独立证据](/private/tmp/aid-agent-runner-m4-domain-b1-independent-evidence.txt)、[范围清单](/private/tmp/aid-agent-runner-m4-domain-b1-independent-inventory.json)、[最终56生产源](/private/tmp/aid-agent-runner-m4-domain-b1-independent-source-final.json) 与 [27测试/辅助源](/private/tmp/aid-agent-runner-m4-domain-b1-independent-tests-final.json) 零漂移，生产源完全匹配v2冻结，独立CR无剩余 P0/P1/P2。

旧图片错误复现与两个启动快照捕获瑕疵如实保留，关联批按运行前既有见证/运行中和结束hash及唯一写入者事实核对，不补造启动记录，不机械重跑掩盖记录缺口。外部模型/桌面为localhost协议替身，内部API/CLI/Engine/Runtime/PG/费用owner实际运行；原opaque公开下载政策保持，外属拒绝证明限已鉴权Runner附件守卫。[实际清理](/private/tmp/aid-agent-runner-m4-domain-b1-independent-cleanup.json) 本片专属进程/fixture根0，不明旧库0连接明确排除，master/Yohar未动。批准解除共同56源冻结进入B2通知实接；B2/Browser/客户真机尚未认证，完整M4未完成，无默认Web切换、提交或部署。后续共用源改动按受影响范围重新核验，B1清单不冒充未来版本。

B2通知只读预审完成，尚未接线或验收：原 HTTP的 started/unknown事实须在root及全children取消证明中保claim/resources，不能凭设备调用列表为空或后来禁用配置宣布安全；低频扫尾只读原事实，不发新 POST。已知原 ACK可由有效原 attempt观察并在有限原事实证明下提交审计，取消阻断下一段、@及拒绝后重试；跨epoch不得借新owner权限。全部必需 markdown与@确认后才标 sent，缺段但已有 ACK只能记录部分/failed。冻结原 policy版本、内容/段数/@摘要、配置版本与简历绑定，凭据继续仅在现配置中。纯原已知 ACK审计不能被后来配置变更抹掉，新派发则必须校当前配置；Engine/Worker不增加业务名称，不引入通用通知编排。

B2实际接线已启动，首正常 done多UTF8段/@默认CLI开发自验1 passed、exit0（27.26s），12受影响源起止一致；尚未正式冻结或独验。接口预核发现并发上下文风险：运行中的LocalAdapter取消也触发全树notify审计，但LocalLifecycle只水合当前leaf/祖先，其他活sibling随后保存旧CP可能覆盖已提交log事实，导致日后重复入库。实施认可最小收口：活Adapter只取消/读取原可信事实，全树audit仅Worker已drain或cancel-only无Runtime收尾显式执行，日志+phase+CP同cursor并返回全树投影。当前设置兼容开发批闭批后再改单点并跑受影响取消自验，增加独立真实PG有限契约及原Local/Send取消关联；不用新live对象注册框架，旧正常发送证据按未变范围保留。

B2 v1现按 [61源清单](/private/tmp/aid-agent-runner-m4-domain-b2-v1-source-sha.json) 正式冻结，主控核对零漂移；13相对B1新增/改动、48未变依赖见证分列，不将61全部算改动。[交接契约](/private/tmp/aid-agent-runner-m4-domain-b2-v1-handoff.txt) 与 [开发证据](/private/tmp/aid-agent-runner-m4-domain-b2-developer-evidence.txt) 保旧窗口：正常1及设置3 passed（27.26s/63.67s），有限取消接口收口后的末版只补受影响原首ACK取消且后来配置禁用1 passed、exit0（22.92s），完整61启动前/结束零漂移，13源py_compile通过。首正常仅12变源完整快照，未变依赖没有独立启动捕获，早窗不充当末版完整证据。

活Adapter的 `complete_audit`默认False，仅请求/读取；Worker全部owned任务drain后或cancel-only无Runtime显式True，固定原通知writer提交log+phase+CP并返回新row，统一投影同步control与执行树local事实，不盖模型消息/步骤。未知HTTP保claim，取消后原ACK观察与新HTTP派发仍分开。现交正式独验9实际形态、有限child/oldEpoch/日志cursor契约及受影响原Local/Send取消关联，正式CR并行；Browser只在冻结外准备，B2尚未验收。

B2 v1完整正式CR核13改源及48必要依赖，61起止零漂移；发现1个P1未关：未知通知事实给出 `LOCAL_NOTIFY_DELIVERY_VERIFICATION_REQUIRED`，但现 `block_cancel`与released_by仅接受3个中性取消码，会抛ValueError，运行中路径可能错误收尾释放claim，cancel-only路径可能中断重领。原9独验已经启动且源码保持稳定，另加真实unknown ACK→原waiting→实际cancel→cancel-only CLI的有界隔离库复现，不能以最终收到known ACK的在飞取消代替未知证明。两批闭批后单源将取消组合中 `kind=domain && !known`归一到现 `LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED`，原业务原因保留私有phase/facts，不扩heartbeat白名单或吞通用异常；新v2清单、定向CR及取消复验另记。

旧B2 v1真实unknown ACK取消单例1 failed、exit1（25.04s），61源/32测试辅助源统一启动前/结束一致。实际cancel-only第二attempt CLI退出0，但Runner仍finalizing/revision19、rootwaiting、claim1，原HTTP phase completed/unknown、原主模型收据1，未持久保存合法blocked等待；清理前诊断及旧日志保留。本次并未观察claim释放，不能将源码中运行中路径的风险倒说成已发生。原9独验仍在跑，全部61继续冻结待闭批。

B2 v1原9独验已闭批，exit0、9 passed（218.64s），统一61源/31测试辅助源完整启动前及实际结束捕获一致，测试相关进程候选0。六个默认CLI形态及三种真实提交返回时机探针的正常/已知ACK路径通过；unknown取消缺陷单例另记，不能用原9通过掩盖P1。[B2 v2清单](/private/tmp/aid-agent-runner-m4-domain-b2-v2-source-sha.json) 仅local_recovery一源变化，主控核61同paths、其余60一致及最终零漂移；中性未知效果码修改已py_compile通过。[v2交接](/private/tmp/aid-agent-runner-m4-domain-b2-v2-handoff.txt) 保原v1及失败证据，交unknown取消原断言、有限真实PG双child/epoch/日志回滚三契约和必要原Local/Send取消关联定向独验，定向CR并行。原9未受影响路径按scope复用，不机械重跑，B2仍未验收。

B2 v2定向正式CR通过，P1关闭，无剩余 P0/P1/P2，61源起止零漂移；中性码闭合现等待保存/心跳释放证明，原HTTP私有事实未改，其他60复用v1完整CR。独验后批实际unknown取消1通过（合法blocked等待/claim/无热领/无新IO），三项native双child/epoch/cursor契约在setup因父pytest缺CLI fixture随机servicepeer得到 `SERVICE_SOURCE_FORBIDDEN`，尚未进入业务断言；61源/32测试辅助源首末一致。保失败版本/日志，按同fixture正常AgentRunnerConfig及原bcrypt hash修DI，真实Authorizer/PG权限继续执行，定向只重三项，不改生产或重复unknown已过证据。

**通知 B2 v2分片正式验收通过（2026-10-02）**：13个不同新形态（7实际默认CLI、3实际提交返回时机探针、3明示native root/双child/epoch契约），另2原Local/Send取消关联；native修正常fixture配置后3 passed、exit0（48.06s），最后关联2 passed、exit0（54.82s）。原9正常/known路径只按v2未变范围复用，旧unknown失败及native setup错误保加载版本和日志，不重复计为新形态，不把native树称为公开fanout。主控核 [独立完整证据](/private/tmp/aid-agent-runner-m4-domain-b2-independent-evidence.txt)、[范围清单](/private/tmp/aid-agent-runner-m4-domain-b2-independent-inventory.json)、[最终61生产源](/private/tmp/aid-agent-runner-m4-domain-b2-independent-source-final.json) 与 [32测试/辅助源](/private/tmp/aid-agent-runner-m4-domain-b2-independent-tests-final.json) 零漂移且源完全匹配v2；所有正式窗口统一捕获成功才启动，源码CR无剩余 P0/P1/P2。

实际未知取消保持合法blocked等待/原claim/无热领、无新HTTP，已知原ACK恢复及取消审计一次，全树日志+CP真实PG故障同回滚及旧epoch拒绝均到末。外部模型/机器人/桌面为localhost协议替身，内部owner/Core/PG/费用/finalizer真实运行；非客户真机或外部平台验收。[实际清理](/private/tmp/aid-agent-runner-m4-domain-b2-independent-cleanup.json) 本片专属进程/fixture根0，隔离库只排除未归属旧库0连接，master/Yohar未动。批准解除61共同源冻结进入Browser四有限片，完整M4及最终Web页面仍待完成，不切默认Web、不提交或部署。

首 binding前的已派发 Notify无domain事实仍按verification保守拒恢复/释放：现StopRequested的内存异常不能提供跨进程no-commit/no-HTTP证明，也无法与旧无phase活动CP区分；prepared及已完成binding/policy零HTTP已有安全路径。该限制已源读核对，不在本片添加通用admission框架，不将无证据窗口宣称可自动续接。

主控正式释放M5①既有写口：原公开DTO/纯row codec单向迁移、事件账/全部公开commit尾点、短一致fresh授权读取、bounded retention/双DDL；SSE/Web stream/frontend/channels/默认开关仍不在首片，不提交或部署。合法新源码变更不以旧M4 sealed证据判断漂移；历史清单/已捕maps保留。

### M4 最终 Web 核对状态门槛（2026-10-02）

只读复核确认两项待修 P2，安排完整 Local 接线后的独立页面小片，不扰动当前领域源码冻结。`useAgent.isProcessing` 不包含 waiting/paused/interrupted，而 ChatInput 仅处理中显示原 Stop；verification waiting 的 Send 又被拒绝，普通用户因此不能到达已有 nonterminal cancel 接口。复用现有停止操作，用独立停止可用性区分不可回复的等待与澄清回复，不能把所有 waiting 都设为 processing 禁掉既有 Send。中文核对原因目前只进入 debug 执行详情，普通正文为空或保留旧输出；将公开 `waiting.question` 以稳定 Runner/wait 标识映到现有可见消息说明，保留原输出与澄清，不展示私有 checkpoint。

验收覆盖刷新找回后的核对说明及原停止操作、澄清仍可发送、切换会话不串状态、重复轮询不重复说明。停止接受后若原动作仍未知，页面仍须明确等待核对，不能虚报已取消或已释放会话。

## 8. M5：实时事件增强

工作：

- 保存公开事件及持久 seq，增加 SSE 按游标补读；采用已有事件 payload，额外信封带 runner/attempt/seq/version。
- 开始订阅前产生的事件也能补读；重连允许重复，客户端按序号去重；恢复时序号不归零。
- 连接有界、超时关闭，慢消费者不阻塞执行。事件过期查询快照重建，不重跑任务。
- Web 优先实时更新，订阅不可用时自动回到查询；查询仍可独立使用。token/事件太密时合并公开更新并保留正确文本次序，不逐 token 无界写表。
- 健康 SSE 期间也保留有界低频权威 GET 对账，和事件/reset触发共用单次在途读取及dirty合并。现250ms是输出通知频率抑制，没有补发最后被抑制输出的定时器，不能将其称为最大展示延迟；只等事件会遗漏暂时没有后续通知的末段变化。

验收：关闭全部订阅不影响任务；晚订阅/断线/多订阅不漏终态、不重复执行；漏通知仍能补读；订阅权限与查询一致；慢连接与过期游标行为正确。

M5 按实时体验需要排期，可以在 M4 之后先用查询适配渠道；它不改变接单、查询与恢复接口，也不阻塞验证四入口共用执行服务。订阅能力仍是本方案交付项，不因阶段顺序调整被取消。

### M5 有界只读预审（2026-10-02）

复用现有安全展示投影生成小型 typed payload、累计输出替换或 revision 通知，不将 Engine 私有 `llm_call`/原始工具结果/child response 直接公开，也不在每条进度中复制完整累计快照。单 runner 的序号从持久递增水位分配，不能从会被清理的 events 最大值重算；事件与对应公开状态同 cursor 提交，结束事件由 Finalizer 随结果/历史/费用/claim 原子提交，Engine 自称 completed 尚不等于公开终态。接单、排队取消、领取、park/interrupted、控制和终态均有真实 DAL 接线；迟到费用仅发布结算变化，不重复结束或执行。

SSE 使用查询同一 fresh 主体/当前渠道 actor 的授权链，并周期复验。每连接只持有有界分页及游标，不在网络发送时占 worker/control 锁或数据库连接；断开不取消、慢连接不拖执行。过期游标明确返回一致已提交 snapshot/revision/last_seq 的重置事实，再从 last_seq+1 补读，客户端按 seq 去重，查询保持权威。四入口共用公开流；KF 发送与发送后的原队列释放仍归原渠道 adapter，不要求新增发送 offset、投递账本或 KF 专用投递 gate；通用执行 claim 由 finalizer 在终态释放。当前为提前审查建议，未实施或验收，不阻塞 M4。

实施准备采用三个有限切片：事件账及一致读取→服务SSE/原Web薄代理→现RunnerClient header-auth fetch订阅与查询fallback。Runner持久head/floor为递增序号/最后已删除序号，`head>=floor>=0`；既有行基线0不回放执行，保留清理即使删除全部事件也不重置head。明确仓储commit前尾点，领域投影只返回最终row/changed，外层一次写本事务最终安全公开通知，避免内外hook重复；暂不引入DB内部事务标识列或异步flush框架。created只新接受、terminal只原Finalizer一次，late usage仅实际结算变化；lease/私有receipt/模型消息/画面不发事件。分页按短一致读事务取得row/head/floor/page后释放连接再发送，读取水位缺口/过期/未来游标明确reset；超限事件只invalidate引导权威查询，不截断结果或逐token复制累计全文。此时只读准备，待A片验收才开放生产写口，各片仍须独立测试/CR。

M5①开工预审认可上述有限接口；实际漏口须包含usage.observe真实settled→pending公开水位/事件及local_recovery.release_ready_cancellations就绪CAS，私有LLM事件的save/view变更不能自动发event。主控当前只放两个尚未被A片import的新event_contracts/event_repository模块，268及真实UI425加载源/DDL/config/既有接线继续冻结；新增2仅编译/最小导入/global diffcheck0，未安装schema/查events表/运行业务，无首片验收。[未接线交接](/private/tmp/aid-agent-runner-m5-events-unwired-v1-handoff.md)保有限cursor口与output-only原DBclock250ms合并，其他重要状态立即通知。接线前须将既有公开DTO/纯row解码原实现迁到单向中性模块、旧repository重导出兼容，事件口不能反依赖将调用它的主DAL形成import环，也不能复制另一套公开字段规则；此时该迁移只读准备，未创建第三模块或改既有源。

M4末验收后已解除上述既有源冻结，事件①接线实施中。批准必要的public_view原纯函数单向迁移、event_read短一致应用读口、event_maintenance可执行有界维护及manager薄读入口；原授权_web/_channel同cursor检查复用，不复制SQL或改普通请求语义。service bcrypt/原opaque verifier可能Redis调用在读事务外，RR内以真实token行/current actor/role/owned会话/source再核；不持pool连接等待外网。预审新2草稿P2为完整短页尾部缺洞应立即reset（区分字节budget提前break），stored event需exact int/`invalidate is True`而非字典1/True松相等，已交唯一writer收口。公开envelope固定version1及actual root attempt非负计数，不泄workerID/boot/epoch；local cancel-ready例外只能核原cancel/waiting/attempt/revision及实际false→true证明，不以任意caller flag或view++造通知。7组独立真实PG风险矩阵及唯一开发smoke准备中，实际源码/schema冻结后才运行；SSE/HTTPstream/前端/渠道与默认开关仍后片。

M5① v1已交接冻结：[交接及逐写口表](/private/tmp/aid-agent-runner-m5-events-v1-handoff.md)20源＝17Python＋双DDL＋DBdoc，有限205见证＝20＋171依赖＋14原profile资源；combined SHA `7ab615de1e183943668fc3e7ae709175d8404187e42c913d5656c526b52de560`。实施者实际compile/import17、原迁移split单DO/双DDL等同/日期递增及global diffcheck均0，尚无PG业务测试。主控实际核当前及loaded205全部匹配后，已派唯一开发PG smoke与完整只读CR；阶段仍进行中，不将编译或存储草稿当成事件功能验收。

唯一开发PG smoke79165实际exit0、1 passed（2.84s），[退出证据](/private/tmp/aid-agent-runner-m5-events-v1-development-1-run-evidence.json)205源/11实际辅助首末0；接受赢家、领取、私有CP不发事件、取消及短RR页、claim/无额外历史费用均到末，自有测试库/进程清理0。它不替代正式独立风险矩阵或Manager鉴权、实际执行及SSE验收。正式测试准备中发现满LIMIT两行被误当短SQL页，已在运行前按冻结契约修正oracle，不声称源码故障：满页继续后下一页确认洞，完整短页当页reset。

外键门槛收口：遵循[数据库规则](../../.claude/rules/database_dev.md)“外键引用完整性在Python代码中检查，不要在数据库层面强制约束”，系统root同样适用，撤销早期预审的SQL FK口径。应用写口先锁原root、保存原scope并同事务写event，读取须当前授权root；本片不自动删除root。M7终态保留清理必须检查无active claim及恢复需要，在原root锁下先删event再删root、同事务完成，不以级联替代生命周期责任。

正式独立B1实际74562 exit0、10 passed（37.59s），[证据](/private/tmp/aid-agent-runner-m5-events-v1-independent-B1-run-evidence.json)205源/16实际辅助首末0。真实PG分页/保留/strict type3、Manager当前身份及同RR一致4、事件SQL整事务回滚/并发CAS2、Finalizer终态回滚与迟到费用1均到末；用量事实是明确PG fixture，不冒实际模型采样。开发smoke1另列不加正式数；自有库/进程清理0，Redis未使用仍按原账保留。DDL、250ms输出合并、usage pending及local cancel-ready和全源CR仍待完成，不提前标①验收。

正式①完整源码CR已通过：[报告](/private/tmp/aid-agent-runner-m5-events-v1-architecture-cr-report.md)剩余P0/P1/P2均0，真实首09:56:59至末10:03:31 UTC的当前/loaded205 maps均匹配且当时保存；原9个纯codec函数AST对归档一致。两个草稿P2已关闭，无并行源变动或测试代跑；仍等剩余独立PG门，不提前认可SSE或整个M5。

剩余B2首窗98770实际exit1、4 fail/6 setupError（14.78s），[原始证据](/private/tmp/aid-agent-runner-m5-events-v1-independent-B2-run-evidence.json)205源/21辅助首末0。两个迁移node已到实际apply/equality或rollback/watermark断言，后续reference计数SQL的LIKE单`%`遇原DBAPI空参数触发IndexError；拒绝node恢复在该断言之后未到，缺head列使后续接单/setup级联失败，notice/auth尚未进入，不冒源码故障或通过。唯一tester仅修SQL `%%`及finally恢复，原失败窗保全，待原B2复验；生产205继续冻结，自有库/进程已清0。

B2r67947实际exit1、1 pass/3 fail/6 setupError（14.76s），[证据](/private/tmp/aid-agent-runner-m5-events-v1-independent-B2r-run-evidence.json)205/21首末0。fresh/incremental/旧行零基线/强制重放node完整通过；catalog负向的原schema/rows/watermark/reference0断言通过，但测试finally删损坏CHECK后没有恢复原定义，真实catalog正确拒绝缺失CHECK，缺head列继续使后续未进入。仅tester改为捕损坏前实际pg_get_constraintdef并finally回装原CHECK后再原迁移，不改生产或猜造schema。下一窗只复验拒绝1＋notice4＋原关联4，已过fresh1不机械重跑；本失败字节及范围保留，自有资源0。

独立CR补阅实际新增7事件测试，当前/本rr加载字节与该窗21 pre匹配；不代跑。触发器原事务失败、原CAS、真实DB时间、原Local绑定/ACK/sweep、Finalizer/身份断言有效。catalog负向实际是一组payload TEXT＋弱CHECK＋root缺head列，首先触发类型检查；即使该node到末通过，也只证明该具体组合同块回滚，不将各wrongNULL/default/index/CHECK源码谓词都冒独立运行实证。

B2rr58213实际exit1、7 pass/2 fail（31.12s），[证据](/private/tmp/aid-agent-runner-m5-events-v1-independent-B2rr-run-evidence.json)205/21首末0，catalog拒绝/维护实际子进程/Local ready三项及原API关联四项到末通过。输出测试在时钟前提验证之前遇head3而非2，需核实际DB时间与公开语义；usage测试假设原ledger初值settled，但真实schema默认pending，尚未observe。暂不裁为生产缺陷；两个准备前提也说明静态测试断言目的核对不能代实际运行，已让CR联合原fixtures补阅。仅这两项待复验，其余有效通过不重复，生产205不动，自有库/进程0。

B2rrr1730实际exit1、1 pass/1 fail（6.51s），205/21首末0：usage明确PG storage-state的settled起点经原root锁/事件尾准备，原observe＋触发器回滚＋幂等到末通过，不冒自然Runtime/Gateway产生该起点。原输出测试改supported factory单真实连接、每DAL仍独立commit并抓语义/DB时间；only_output_changed成立，但操作后实际0.25375秒超过其250ms前提，未认证合并，也不据此前失败回溯猜根因。下一窗只验证此一项，允许有限同事务原EventRepo/default250ms真实时钟仓储契约形态，或观测原append精确时点；不改生产时钟、区间或放宽head，不冒原Execution吞吐。其余通过保留，源205冻结。

单项62613实际exit1（3.08s），205/21首末0；有限同TX原append的精确decision时点已测到0.314967秒，公开语义仅output，但两次累计变更的窗口前提仍不足，不算生产合并失败。下一窗收窄为一条实际窗口内变更合并、真实DBsleep过原250ms后下一变更通知及原cancel立即；这是仓储时钟契约，不再称多次burst/Execution吞吐。原失败保存；数据库/DAL开销留M7按实际部署测量，不由该fixture反推通用生产容量。

**M5①事件账/一致读取验收通过（2026-10-02）**：最后48091实际exit0、1 passed（2.61s），[证据](/private/tmp/aid-agent-runner-m5-events-v1-independent-B2clock-run-evidence.json)验证原default250ms精确decision时钟的一条窗口内output合并、实际DBsleep后的新通知和原cancel即时尾；仅有限游标仓储契约，不称Execution burst吞吐。正式16 unique＋原API关联4已全有效，开发1另列；8实际运行窗及所有失败范围保留。完整源CR剩余P0/P1/P2均0，最终测试静读无新增问题；[末封存元数据](/private/tmp/aid-agent-runner-m5-events-v1-final-meta-end.json)于10:35:44 UTC记录205源/21实际辅助union，主控亲核当前全部匹配；[清理](/private/tmp/aid-agent-runner-m5-events-v1-final-cleanup.json)自有库/进程/临时目录/namespace全0，未知旧库排除，exact Redis按账意图保留。此时未接SSE/前端/渠道，已解除①源冻结，授权②上述7源实施；新合法源码改动不冒此前封存漂移。

主控已读[①最终清单](/private/tmp/aid-agent-runner-m5-events-v1-final-inventory.json)及[范围证据](/private/tmp/aid-agent-runner-m5-events-v1-final-evidence.txt)：8实际运行窗、5失败版本、各角色/真实命令/加载字节/未到范围均分列，21辅助为各窗有限union，不冒一次全导入。局部存储状态/SQL故障/采样DI的角色及输出单项仓储时钟边界均明确；①后没有重复旧矩阵，②实施中。

M5②只读准备采用独立有限ASGI发送监督与原Web流代理，先取得fresh首page/上游成功SSE握手再返回200，独立权限复验不受idle或slow-send阻塞；每页短RR结束后再发网络，finally只关订阅任务及HTTP资源。大reset使用同快照的小query_required提示，完整正文仍GET；terminal须补完head，pending费用可继续观察。原capabilities的contract_version1/runner_poll保留以兼容旧缓存客户端，后新增可选events_supported。当前①源仍冻结，②未编辑或运行。

M5②实施中的预查补充（尚未冻结验收）：真实应用的 `BaseHTTPMiddleware` 可能将受监督响应改包为外层流，慢发送验收须覆盖完整应用的外侧 ASGI send，不能只测裸响应。权限与断线监督须覆盖响应头发送阶段；观察许可也须覆盖首读，失败与取消释放。`wait_for(to_thread(...))` 不会停止已执行的数据库线程，正常短事务返回才归还连接；HTTP 结束不等于真实连接立即清零，清理须等待实际读结束并核验。实施者正在收口有限边界，不修改整库线程池或扩大成通知平台。

实际测试容器 httpx 0.28.1/httpcore 1.0.9 的 HTTP/1.1 原读链以最多 65536 字节网络块推进；显式 `aiter_raw(chunk_size=...)` 会聚合小帧，可能延迟心跳。当前选择核定原读链、显式禁 HTTP/2、单块限额与有界发送，不新增自定义 transport；这是当前依赖事实，尚不是 SSE 链路通过或生产容量证明。

主控批准②从7扩为9个 Python 源，新增边界仅既有 `src/saas/middleware.py` 与 `event_read.py`：两个精确 GET events 路径复用原 dispatch 的身份/租户/context 与安全错误规则，直接 ASGI 传递，不经内部流重包装；其它请求保持原中间件路径。事件短事务设置局部 SQL/锁超时，强持有实际读任务，首读即取得许可，取消后的许可释放以真实读结束为准。提前拒绝只发一次响应、成功与 post200 不重复 headers、完整应用慢发送及真实线程清理均须正式验收。

②v1已实施并固定：[自包含交接](/private/tmp/aid-agent-runner-m5-sse-v1-handoff.md)，9 Python＋184依赖＋14既有资源共207，组合SHA `af7278ea8c4b3877b68df797dd4079d65ba769721fcf375b10194f781cb9286c`；相对①恰7既有变化＋2新，其它198不变。实际容器末9模块compile/import与diff检查exit0，11:07:50 UTC元数据保留；主控亲核当前/loaded207全部一致。已放行唯一开发HTTP/PG链路和独立只读CR，不冒行为通过。Web仅依赖上游fresh timer及自身真实send5秒，不宣称本地DAL每秒复验；异步read5秒超时后清理仍等待实际同步读结束，非5秒内pool必为0。整个取消/gather/真实读/HTTPclose/release由强持有shield保护，末body以HTTPstart实际成功为前提，待完整应用验证。

②v1正式源码[独立CR](/private/tmp/aid-agent-runner-m5-sse-v1-architecture-cr-report.md)剩余P0/P1/P2均0，实际11:11:40–11:15:17 UTC当前/loaded207首末一致。唯一开发实测9665仍失败：exit1、1 failed（5.32秒），207/15首末一致，POST202成立，但订阅status为500，created/acquire/terminal断言未到；首次证据未区分服务或Web，私有日志已由fixture清掉，不能倒推根因。[原失败记录](/private/tmp/aid-agent-runner-m5-sse-v1-development-1-run-evidence.json)及[清理](/private/tmp/aid-agent-runner-m5-sse-v1-development-1-cleanup.json)已封，自有资源0、未知旧库排除、原Redis有意保留。已授权仅测试补事前脱敏阶段诊断、同节点新窗复验；207生产暂不改，源码CR通过不替代行为通过。

诊断复验47721 exit1、1 failed（5.40秒），207/15首末0，实际事前捕获两端500/稳定 `RUNNER_STORAGE_UNAVAILABLE`；异常 `ActiveSqlTransaction` 最后原位置为 `event_read.py:23`。主控与实施者核实 `get_pooled_connection` 的 checkout `SELECT 1` 先开启事务但直接return，原 `get_db_connection` 到作用域退出才rollback；事件随后 SET RR失败。①原PG factory有限验证未覆盖此真实默认checkout链，v1源码CR亦漏识此P1。原两次失败完整封存，自有清理0；已批准v2仅事件读取自有独立connection scope先rollback健康检查事务再开RR，不改全库pool或借用业务cursor，修复后复验原开发节点、CR定向复核。尚未关闭P1或放行正式风险组。

②v2已固定且开发正常链路通过：[交接](/private/tmp/aid-agent-runner-m5-sse-v2-handoff.md)，仅`event_read.py`变化，其它206与v1相同，组合207 SHA `b94274ae12b1a9456e78b9790c9af5f08889d1b4962662d8b6a52a7820ea2af9`，actualcompile1/import3/diff0。[定向CR](/private/tmp/aid-agent-runner-m5-sse-v2-architecture-cr-report.md)11:30:55–11:31:31 UTC首末current/loaded207一致，P1源码关闭、余0。第三实际开发窗37427 exit0、1 passed（8.97秒），[证据](/private/tmp/aid-agent-runner-m5-sse-v2-development-1-run-evidence.json)207/15首末及主控当前一致；真实原pool默认工厂→独立service/Web HTTP200→created/acquire/stage/terminal原seq→settled补完EOF及原GET/history2/claim0/receipt0/fee0全部到末。nativePG状态转移fixture，非实际Engine/model执行；开发unique仍1，前两失败保留。[清理](/private/tmp/aid-agent-runner-m5-sse-v2-development-1-cleanup.json)自有0、未知旧库排除、原Redis意图保留；已放有限正式7风险节点和必要middleware关联，源码继续固定，尚未接前端或认整个②通过。

**M5②服务SSE/Web代理有限验收通过（2026-10-02）**：36736实际exit1，10 passed/1 failed（77.48秒），207/21首末0，其中7正式＋3原mock middleware关联到末；idle节点读取超时，历史未记录关闭时点，不倒填根因。仅测试将reader idle15→20秒、总close25秒并记录实际时点，保原服务AUTH1/SEND5/HEARTBEAT15及撤权后EOF5秒断言；5245同节点exit0、1 passed（20.85秒），207/15首末0，真实client detach15.060406秒、token提交后另一订阅cleanEOF0.054918秒/noerror、root不变/后续HTTP401到末。该detach耗时是Python测试客户端事实，不称服务立即关闭或容量证明。

[最终清单](/private/tmp/aid-agent-runner-m5-sse-v2-final-inventory.json)与[证据](/private/tmp/aid-agent-runner-m5-sse-v2-final-evidence.txt)保5实际行为窗/3失败版本、2仅collect；正式8 unique＋关联3＋开发1分别计，未重跑已过10。含真实PG表锁55P03/解锁后正常入口，完整app外侧发送与RR-held/doublecancel明确时序DI，WirePeer仅外部HTTP协议fixture；非物理socket压力、Engine/provider、128实连接容量或完整前端验收。11:48:42 UTC最终source207/test21路径union封存，主控当前亲核0；各窗实际加载版本分别保留，不称单次最新版21全过，首次collect rawlog覆盖瑕疵也登记。自有资源0、未知旧库排除、exactRedis继续保留。②已接受，③仅放3前端生产源`api/runnerEventStream.ts`、`api/runner.ts`、`composables/useAgent.ts`；旧207仍保持。cap兼容/水位GET/健康低频对账/terminal pending继续观察但执行UI不再忙、EOF非终态及detach均待③验收。

### M5③ 恢复记录（2026-10-03）

恢复时实际核查发现，前述 `/private/tmp/aid-agent-*` 验证附件已被环境清理，仓库未找到副本。上文保留已发生的验收结论、命令退出状态及已登记限制；旧附件链接当前不可访问，不能据此复核历史加载字节，也不重建历史日志或冒填时间。前端三个生产源及测试准备仍在，③尚未验收。因原末次构建附件失去，针对当前前端状态重新捕获有限依赖清单并实际构建；本次起将脱敏验证清单与小型日志保存在仓库忽略目录 `tmp/agent-runner-evidence/m5-web-events/`。新证据只证明本次代码与环境，不倒填旧阶段的207源清单或历史验证。

本次三生产源与27静态依赖共30固定，主控亲核当前30/保存三源字节一致。[交接](/Users/ethynic/repos/aid-work-agent/tmp/agent-runner-evidence/m5-web-events/handoff.md)记录恢复后实际build68717退出0（Vite6.68秒、523构建模块），30及65类型检查输入首末不变；65不是通过测试数。唯一开发正常节点42251退出0、1通过、9项定向过滤。独立正式41381退出0、24通过，60750退出0、2新生命周期及6原轮询/主体/控制关联通过；因此正式新行为26、关联6、开发1分别计。替身仅外部HTTP/ReadableStream及原鉴权/历史边界，使用原解析器、Client、useAgent，不能冒真实服务、容量或页面证明。[源码CR](/Users/ethynic/repos/aid-work-agent/tmp/agent-runner-evidence/m5-web-events/cr-report.md)当前30/保存三源首末一致，P0/P1/P2均0。真实built页面订阅、查询、刷新找回与关闭后持续执行尚待验收；Docker Desktop已恢复，现有API容器健康且挂载当前仓库，原临时Redis不在，本片普通页面不依赖它。

**M5③验收通过（2026-10-03）**：真实页面84518退出0、1通过（19.71秒），使用本次built页面、原Web鉴权/API、独立AgentRunner和原Worker/Engine/PG；模型仅为虚构loopback外部响应关口，没有拦截页面fetch/auth/SSE。三次真实订阅200，刷新、关闭全部页面、重新打开均找回原Runner，关闭时原Worker仍running且未cancel；再次观察后放行模型，页面显示唯一终态，提交/模型调用各一次，18 tokens/.01及原历史各一次、claim释放。目视原页面终态截图通过，输入区可用、Stop消失。实际页面证明订阅及权威查询，不将所有GET归因于通知；通知触发查询由原Client独立外部wire测试证明。

末次[真实UI证据](/Users/ethynic/repos/aid-work-agent/tmp/agent-runner-evidence/m5-web-events/real-ui1-run-evidence.json)及[终态截图](/Users/ethynic/repos/aid-work-agent/tests/.artifacts/runner-events-real-ui-terminal.png)已核。最终552当前静态source/resource见证（含134assets）、32实际测试/辅助路径union首末一致，主控当前亲核0；不是一次加载全部552或一次跑32的声明。本片正式新行为27（前端26＋真实页面1）、关联6、开发1。清理只读核自有service/Web/Worker/Chrome/pytest及新测试数据库0；已登记旧库0连接明确排除，旧临时Redis已不在且未重建。本阶段完成不等于默认迁移、三渠道、容量或部署完成，后续源修改作为新阶段，不倒算此前证据漂移。

前端补充类型门15538退出0：只运行`vue-tsc --noEmit -p tsconfig.web.json`，当前30前端源/依赖及66类型测试输入首末不变，覆盖构建后新增的两项生命周期测试文件；未重跑Vite或行为。本附证不比较M6已获授权变更后的旧后端552为漂移。

## 9. M6a–M6c：逐个渠道接入

M6a 已完成，M6b/M6c 从本重构任务拆出，后续开发与进度更新分别登记到渠道计划；本节保留接入边界。

### M6a：微信客服原渠道接通用 Runner HTTP

本阶段保留 AgentRunner/AgentEngine 重构，恢复微信客服原渠道实现和用户行为。开发详情、进度与验证记录统一在[原渠道恢复计划](plan-wecom-kf-channel-restore.md)维护。接入独立 API/worker 是要求；仅调用兼容 Agent 壳中的新 Engine 不能代替服务接入。

| 边界 | 唯一责任方 | 接入要求 |
|---|---|---|
| 回调验签、拉取、游标、平台去重 | 原 KF 渠道 | 保持原入口与业务，不建立新接收管线 |
| 合并、follower、取消重跑、发送期 pending | 原 session_queue | 在原 processor 位置提交最终合并输入；取消映射到通用 Runner cancel，不假定请求受理即已停稳 |
| 模型与工具执行、恢复点、任务状态 | Runner API/worker | `source=wecom_kf`、channel session、原 profile；使用通用 create/read/cancel/controls/events |
| 用户、工具及回复历史 | 原 ChannelSessionManager | 原 msgid、语音附件、merged_segments、撤回、人工历史与图片元数据继续保留；Runner 不重复写 KF 历史 |
| 模型与工具计费 | Runner usage receipts/finalizer | 原记录不再次累加模型用量或扣费 |
| 入口语音识别与 ASR 计费 | 原渠道 ASR/SessionRecordService | 每次真实成功识别沿原记录计费，模型费用不写入该记录 |
| 转人工、留资、客户资料 | 原工具与 KF ContextVar | Worker 按可信配置及 session 重建工具上下文，真实转人工成功后原发送闭包抑制追加回复 |
| 渲染、发送、回复预算和 recap | 原 adapter/渠道收尾 | 保持现有媒体与文本路径；发送成功后按原规则触发 recap，无 Runner delivery gate |

薄桥使用主 API 已有 service peer，其授权来源包含 `wecom_kf`。提交与读取携带当前平台用户、客服账号以及来源；Runner 从现有 `channel_sessions` 验证绑定并推导 tenant/user/profile，保留当前租户订阅与余额检查。Worker 的 KF 配置引用必须按同租户读取，凭据只在服务端装配，不进入 input、checkpoint、事件或 Trace。

创建使用稳定请求键，网络重试复用原键；合并输入改变时按实际输入构造新请求。events 仅通知公开状态失效，薄桥再读 runner 快照并转换原渠道所需的进度、文件、图片、追问和结果；原历史收尾需要的脱敏工具消息通过同一 Runner 身份授权的 `GET /v1/runners/{id}/channel-result` 只读结果接口取得，不另建渠道投递管线。真实子澄清通过通用 controls 将回复关联原 runner/wait；当前 KF 薄桥遇到其它工具等待时请求取消、等待取消确认并明确报错，不保留无法续接的渠道等待状态；取消未确认时说明仍在核对，不虚报已停止。普通业务追问不扩大成新渠道状态机。paused/interrupted 或取消尚待外部操作核对时明确说明状态，不冒充完成，也不盲重演已派发操作。

附件沿用通用 Runner 契约：语音/视频按文件附件传递；较大媒体复用既有同租户 conversation `file_id` 引用，避免 base64 请求超过通用 POST 上限。不得为适配新增 KF 媒体持久管线。

KF 共用通用 `agent_runner_session_claims` 执行 claim 及每个任务的 attempt/lease，领取、恢复、取消与终态使用通用仓储，finalizer 终态释放 claim。原队列取消重跑须确认原 Runner 已达终态、claim 已释放后再交接；不再有 KF 专用接单或投递 gate，发送与 recap 不延长 Runner claim。

验收覆盖真实调用链“原回调 → 原合并队列 → 通用 Runner API → 独立 worker → 原渠道历史与发送”，以及稳定重试键、来源鉴权、跨租户拒绝、语音、合并取消、追问、转人工、图片/文件、预算、撤回与发送成功后 recap。历史不得双写，模型和 ASR 不得重复扣费；取消确认终态后通用执行 claim 必须释放，原队列才能交接；不得因旧 KF 投递 gate 遗留而阻塞新输入。独立测试、独立 CR 与启动检查按高风险流程执行，真实微信/付费模型与部署仍按当场授权范围。

### M6b / M6c：飞书与钉钉

分别接入通用 Runner API/worker，Runner 必须知道任务来自飞书或钉钉，并校验可信渠道身份及绑定。只替换对话执行调用，沿用各自原回调、消息解析、媒体、平台去重、队列、历史与发送业务；不重构渠道本身。开发前核定各渠道的历史/费用责任及原控制行为，逐个验收，不以 KF 接入替代其他渠道验证，也不预设专用 inbox、投递账本、锁闸门、消费者或容器。具体边界遵循[架构设计 §8.1](../system/agent-application-architecture-design.md#81-后续接入的固定边界)。

## 10. M7：历史综合验收、整改与回退记录

本节保留 2026-10-04/05 的实施时点记录，不是当前“重构进行中”的待办清单。图片引用/字节限制、adapter 契约版本、认证依赖及部署配置已有后续整改记录；“全部未提交”“compose 未接 Runner”等描述已被后续代码和发布取代。当前 Web 已接入，旧流式接口已有 `legacy_stream_bridge`；KF agent2 发布与验收见恢复计划 Phase 5。容量数字仍只有原测量边界，其他环境、真实工具及原记录中的未验证项不据此宣称已验收。

### M7 性能整改①：消除每执行全量资源装配（2026-10-04）

**根因**（本地容器实测，全新会话单条消息 22.0s，其中模型仅 2.1s）：worker 每次执行全量重建资源——`profiles.resolve` 与 `RuntimeResources` 各建一个 SubagentRegistry（磁盘 14+DB 6 各一遍）、SkillRegistry 每次加载 25 个 skill 并逐 skill 执行建表 DDL（打在远程库）、5 个工具路由各自懒建 LLMGateway/KeyPool、Redis 降级客户端每执行新建数十个。

**改动**（7 文件，生产代码）：
- 新增 `src/services/agent_runner/runtime/resource_cache.py`：进程级缓存——SkillRegistry 按 allowed 集合缓存一次（建表 DDL 随之只跑一次）；内置 SubagentRegistry 按目录缓存；DB overlay 注册表用 `SELECT COUNT(*), MAX(updated_at) FROM subagent_definitions WHERE status='active'` 一条廉价签名查询决定是否刷新（替代全量 load_from_db，保证 API/worker 两进程指纹不与 DB 分叉）。
- `runtime/profile.py`、`profiles.py` 接入缓存；excel/pdf/word/ppt 路由与 knowledge service 懒建网关改用共享 `llm_gateway` 单例。
- `src/channels/__init__.py` 保持 PEP 562 惰性导入，独立 API/worker 导入通用模块时不启动渠道回调或旧执行服务；KF 工具所需适配器在执行范围内装配。

**隔离边界**：只缓存配置形态资源（定义/连接）；MemoryManager、PlanManager、执行身份等单次 runner 状态不进缓存。RuntimeResources 的子代理注册表保持仅内置定义（不含 DB overlay）的既有行为——与旧共享 Agent 可委派 DB 定义子代理存在功能差异，登记为 M7 待裁决项（本次不悄悄改变委派面）。

**效果**（本地容器真实消息实测）：新会话 22.0s → 6.9s（含重启后首次缓存构建）/ 4.1s（暖态）；装配段 15.5s → ~1-2s。

**验证**：test_api+test_worker+test_agent_loop+test_session_queue 66 通过；test_worker_resources/test_worker_skills/test_resources/test_agent_engine_acceptance/test_tenant_skill_cache 54 通过。开发中自查修复一处自有缺陷（签名查询按 RealDictCursor 列名取值）。独立测试与独立 CR 另行记录。

**遗留**：每执行的租户技能合并扫描与内存 Redis 降级客户端开销，以及大会话压缩阈值策略，按通用性能与容量验证继续核定。

**独立验证（2026-10-04）**：独立测试最终版全量 37/37 通过，缓存正确性三项（同实例/单次加载/签名刷新恰好一次）全过，无 mock 语义变化；批跑偶发失败均属共享测试实例时序抖动（单跑/重跑即过）。独立 CR 报 P0×0、P1×2、P2×4：P1（注册表原地刷新读写竞争、DB 签名未覆盖 prompt 表致指纹可能持久分叉）已修复——改为 copy-on-write（锁外新建实例原子换入）+ 签名扩展 prompt_versions/prompt_labels（修正：prompt_registries 不存在、prompt_versions 无 updated_at 改用 count+max(version)）；P2 死导入已清理、其余登记。修复后 test_api+test_worker 47 项全绿。

### M7 整改③：trace 每调用 span + UI 执行中过程反馈（2026-10-04）

**① trace 补全**（`src/core/trace_collector.py` + `tests/unit/test_trace_llm_spans.py`）：原实现每次 llm_call 事件构建 span 后仅覆盖保留最后一个（`_last_llm_span`，旧"只看最后一次上下文"设计与体积约束）。改为每次调用恰好一个 span、按事件顺序落列：非最后一次在下一次调用到来时原地精简为摘要（message_count/has_tools/字符量，不含全量 messages），最后一次保留全量输入。单测 3 项 + 相邻 trace 测试 56 项通过；真实行程消息验证：3 span（精简/工具/全量）与用量收据一致，原先不可见的 12.8s 决策调用现可诊断。

**② UI 执行中反馈**（断点经浏览器实测定位）：服务端实时进度链路本已通（执行中公开快照携带 progressMessages，实测 14 条工具进度可查），前端投影/合并/SSE 链路也存在——断点在 `MessageItem.vue` 的进度块被 `isDebugEnabled` 门控，普通用户执行中只见"对方正在输入中..."。修复：`ChatMessage.runnerActive`（runnerMessages 投影按 runner 终态置位）+ MessageItem 新增执行中实时块（最近 4 条工具活动，复用既有样式；debug 块保持且执行中去重）；顺手消除双 emoji 遗留。浏览器实测：搜索类消息执行 16s 时逐条渲染 `🔧 需要调用工具【网络搜索…】`/`✅ 执行完成`。前端 7 测试 + vue-tsc + build 通过。

**顺带发现的缺陷（待办）**：
1. **提交失败静默**：POST /api/chat/runners 403（NO_CREDIT）时前端仅留"🚀 正在发送请求..."乐观消息，无错误提示——本地验证中积分耗尽期间全部表现为"永远没回复"，与用户报告的体感吻合，需补提交失败态展示与明确报错；
2. 验证租户积分消耗远超预期（单轮大上下文 20+ credits），M7 容量测算需真实口径。

### M7 调查②：慢回复根因（2026-10-04）

用户报告行程规划类消息 1 分钟+无回复，已复现并拆解为两类独立现象：

1. **合法慢（可复现，同文案新会话 108s）**：行程规划触发多阶段执行——3 次模型调用（25k-40k context/次，receipts 证实）+8 次工具（景点/网页搜索、选项呈现）；大空档即工具决策模型调用。用户原会话叠加 111k 未压缩历史时更慢。体验问题：长执行期间 UI 过程反馈未确证有效（前端 runnerMessages 具备渲染 progressMessages 能力，公开快照执行中是否携带待查）。
2. **真挂死（罕见，py-spy 证实过一次）**：两线程无限阻塞于 psycopg2 execute——连接 keepalive 实测有效（30/5/3）、statement_timeout=30s 在池连接实测生效、`SET LOCAL` 无永久覆盖、健康检查竞态假设复现失败（psycopg2 连接级串行化使该交错无害）。机制未最终定案；已加 tcp_user_timeout=60s 兜底无 ACK 场景；自动复现监控脚本模式已验证（同文案复现抓到全现场），下次出现可当场取服务端锁证据。生产 agent3 独立库可消除共享实例负载诱因。
3. **trace 完整性缺陷（本轮确证，M7 整改项）**：3 次模型调用只落 1 个 generation span，工具决策调用缺失致耗时分析必须靠 receipts 反推。

### M7 整改④：派发时持久的 adapter 契约版本与恢复拒绝（2026-10-04）

落实本节既有门槛「工具及恢复适配器没有独立兼容契约版本」：新增 `src/services/agent_runner/adapter_contract.py` 声明有界整数契约版本——`ADAPTER_CONTRACT_BASELINE=1`（缺字段遗留行的声明式基线）、`ADAPTER_CONTRACT_VERSION=1`（当前写入值）、`SUPPORTED_ADAPTER_CONTRACT_VERSIONS=frozenset({1})`（兼容策略显式声明）。人工 bump、非源码 hash，文案/prompt 改动不触碰。

**机制**：派发时在 executor.py 既有 durable_owner 分支（configuration_fingerprint 同点）把版本写入每个 `ExecutionState.resources['adapter_contract_version']`，随 'prepared' 的 control.save 持久化，子状态经 ChildControl.save 嵌入父 checkpoint 自动覆盖；恢复两道门——①RecoveryCoordinator.validate 的 active 分支在 configuration_fingerprint 比对前校验，未知/被淘汰/被篡改非整数值抛 `RunnerError('RECOVERY_ADAPTER_VERSION_UNSUPPORTED',409)`，经既有 reject_resume 路径拒绝 control、runner 原状保留（可等新 worker）；②executor restore 门镜像 RECOVERY_CONFIGURATION_CHANGED 的 ValueError 语义，兜住不经 coordinator 的 interrupted+cancel_requested 直接 acquire 旁路。两道门失败语义不同是有意设计：executor 门 ValueError→EXECUTION_FAILED 终态，避免不相容行在 cancel-acquire 路径无限 churn，与 CheckpointFailure 的 preserve+interrupt 语义不同。已完成只读子树不校验（与 configuration_fingerprint 同边界）；内核 agent_engine 零改动（resources 为自由 dict）。

**兼容窗口决定（首次 bump 时执行）**：缺字段=基线 1 是声明式假设——当前所有 in-flight checkpoint 确由现行（唯一）v1 语义适配器写入，非猜测。首次 bump 且淘汰 1 时，全部遗留缺字段行将被明确拒绝（不会默默用新语义执行旧阶段），这是有意策略；届时需先排空或显式迁移在飞任务再收窄 SUPPORTED 集合。单版本整数覆盖整个工具+恢复适配器契约族（tools phase/wait kind/resources 键形/children 形状/证明要求）；per-fact 微版本先例（model_phase_version）已存在于 local/domain recovery，不重复建面，某适配器独立演进时再拆分。`agent_runners.checkpoint_version` 列经 grep 确认为死列（src/tests 零引用），不启用不删改；envelope 'version' 与内核 schema_version 粗粒度门保持不变。

**验证**：新增单测 `tests/unit/test_runner_adapter_contract.py`（常量不变式+非法值矩阵+coordinator 未知拒绝/缺字段放行/当前版本放行/活跃子拒绝/已完成子树不校验，20 项）；`test_worker_recovery.py` 增端到端用例——delegate+child clarify 挂起后 jsonb_set 篡改 root/child 版本为 99→resume 被 rejected（RECOVERY_ADAPTER_VERSION_UNSUPPORTED）且零模型 IO、attempt/revision/checkpoint 原状；剥字段遗留行→续跑成功且 root/child checkpoint 补 stamp（3 项）。定向回归 `dev_test.sh --isolated-db tests/unit/test_runner_adapter_contract.py tests/integration/agent_runner_service/test_worker_recovery.py`：40 通过/1 既有 skip。executor 门的拒绝分支当前无运行时路径可直达（所有 restore 必先过 coordinator），与既有 configuration_fingerprint executor 门同属纵深防御，由单测函数矩阵+集成放行侧覆盖其共享判定逻辑。

### M7 通用整改与容量测量登记（2026-10-05）

下列保留通用 Runner 与 Web 整改的验证记录；KF 原渠道接入以恢复计划中的新验证为准。

- **compression-cap（压缩阈值绝对上限）**：改——settings.py:268 新增 `token_threshold_absolute=60000`（0/负禁用）+config.yaml:169 同步；mid_term.py:516-531 抽 `_effective_token_threshold=min(比例,绝对)`，三消费点统一（:601 阈值、:1769 后台扫描、:1498-1499 经济闸门 near_limit 改 0.8×有效阈值防 60k 封顶被误降级硬截断），新增 8 用例；验——grep 比例公式仅存 helper（:527）无分叉，实跑 test_check_threshold.py 19 passed，create_settings 实测 absolute=60000 生效、旧配置缺键可启动；遗——diff 四文件混有他人未提交 hunk，本轮仅评审阈值相关改动。
- **submit-failure-ui（前端提交失败明确提示）**：改——POST /api/chat/runners 失败给乐观消息挂内存态 submitFailure（definitive 403/4xx=错误态+重试钮、网络/5xx=自动重试），🚀 progress 行原地替换文案，重试复用原 client_request_id 幂等重放，本轮补 retryRunnerSubmit 互斥 guard（useAgent.ts:352-354）+竞态回归测试；restoreInput 复用 ChatContainer.vue:538-541 既有消费零改动；后端零改动；验——3 测试文件 47/47+vue-tsc 0 错+build 通过（前端构建退出码 0）；遗——state.submitSelecting=false（useAgent.ts:356）恒 false 冗余赋值，防御性无害。
- **fk-migration（controls FK 与库规则收口）**：改——init-postgres.sql:3070 撤 REFERENCES；db_update.yaml:3410 增量块 2026-10-04 02:00:00（to_regclass 守卫+contype='f' 全删，幂等）；4 处 fixture controls 先删；迁移测试断言按 datetime 定位（改动系接手前已落盘，本轮逐点核验+复跑）；验——应用层无 FK 级联依赖（src/ 无 DELETE FROM agent_runners，lock_owned_runner→ownership.py:26-29 FOR UPDATE，INSERT 均在同事务其后）；三迁移/存储测试实跑 20 passed；遗——无（历史 2026-10-02 块 append-only 保留正确，范围外 FK 未触碰）。
- **adapter-version（adapter 契约版本）**：改/验——机制详见本节「M7 整改④」，独立复核逐点核实（常量/写点唯一 executor.py:294/coordinator 门先于 fingerprint/篡改矩阵），复核实跑 40 passed 1 skip（438.49s，skip 为既有）；遗——无 P0/P1，checkpoint_version 死列不启用。
- **checkpoint-refs（图片可信引用+持久化字节上限）**：改——durable 装配点只存 runner_image 引用（登记 resources.image_artifacts、sha256 幂等落 owner workspace），ModelAdapter._resolve_image_references 按包含性+登记+尺寸/哈希复查深拷贝装配 wire、不回写 state；AgentRunnerLimitsConfig（request 2M/checkpoint 8M/snapshot 2M+env 覆盖）入口 413 闸（幂等重放不受限）+capped dumps 超限走既有失败终态；验——复核 grep 写点 checkpoint 17/snapshot 12 处全覆盖，定向实跑 44+4+31 passed（开发者全量 9 文件 136 passed）；遗——usage_repository.py:60 计费列不在范围未改；cleanup/resolve symlink 检查不对称由哈希复查兜底。
- **deploy-config（分进程部署配置准备）**：通用 Runner API/worker 使用同一制品和共享产物路径；主 API 的 KF 原入口调用通用服务，不配置独立 KF 拉取、接单或投递容器。当前部署说明以 deploy 目录现行配置为准；未启动或部署任何服务。
- **容量测量（落实本节测量门槛）**：本地容器真实 API/worker+假 LLM（延迟≈0）+一次性隔离库（脚本在 /tmp 未入仓）：吞吐 1.30 runner/s（concurrency=4 打满）、每执行占槽≈3.1s 纯机器开销、≈60 次远程 DB 事务/执行（瓶颈在事务数而非并发上限，真实承载≈4/(模型时长+3.1s)）；空闲轮询 3.8 语句/s；checkpoint 逻辑 15.6KB/落盘 7.4KB（上限 8MB 余量充足）；真实 cost12 bcrypt 173ms/验（fixture cost4 相差 258×，verify_service authorization.py:24 无缓存，单进程串行≈5.8 验/s）；结论——模型延迟 0+远程测试库+单轮尺寸，不可外推生产承载，建议 service token 校验加进程内缓存后真机复测。

**2026-10-05 当时待用户授权事项（历史记录，不代表当前状态）**：① 提交代码（当时全部改动仍在 master 工作区未提交）；② agent3 部署（overlay 与清单已备好，含 db_update.yaml 迁移前置与旧主 API 容器补 env 并 recreate）；③ 默认入口切换（当时默认 Web 未切 Runner，须先过旧页面 /api/chat/stream 并行执行协调门槛）；④ 真实渠道外网验收（当时微信客服仅有本地回环假平台基准）。后续提交、Web 接入与 KF 真机验收不以此旧列表重新判为未完成。

### 历史验收门槛（保留原发现时点）

验收覆盖四入口并发、跨 tenant/会话隔离、慢/断线观察、任务排队、暂停/继续竞争、存储不可用、旧 worker 迟到、真实模型/工具、Runtime/BOSS 兼容及产物访问。

- 测量执行并发、队列上限、轮询读取量、事件写入量、checkpoint/输出尺寸及保留容量，按结果调整配置，不宣称未经压测的承载数量。
- 实际源码已确认图片 data URL 被放入 state.messages 后进入 asdict checkpoint，旧“仅内存”注释不符；M7 应将持久消息改为可信产物引用，ModelAdapter 按 owner 读取并装配 provider wire。单请求/控制输入、checkpoint/公开快照总字节及队列拒绝背压须有配置与实测，不能只凭图片单张上限或执行并发认为持久体量已受控。
- 当前配置 fingerprint 覆盖 profile/settings/prompts，工具及恢复适配器没有独立兼容契约版本。M7 核定派发时持久的有界 adapter 契约版本及兼容策略，未知版本拒恢复；不能用全源码 hash 让文案/格式修改导致所有任务不可继续，也不能默默用改变语义的新适配器执行旧阶段。
- 内部服务凭据与登录校验的 CPU/数据库成本按实际部署参数测量。M2a 的 101 请求功能 fixture 为提速使用随机凭据的 bcrypt cost 4，不是生产鉴权开销或容量证据；M7 不沿用该低成本参数作承载结论。
- 配置与停止清理有界：服务停止接单后按策略安全停在恢复点；异常退出明确中断；测试产生的容器、卷和文件用命名清单清理，不动 master/Yohar 资源。
- 独立 API/worker 已有各自 `bootstrap`/`worker` 可运行入口，但当前部署 compose 未接 AgentRunner。M7补同一代码制品下分进程的部署配置、共享数据库/产物路径；只准备配置与隔离启动验证，不自行启动或更新用户环境。
- 清理 schema 时核对已有 `agent_runner_controls.runner_id` 的 SQL FK/ON DELETE CASCADE：它与当前“引用完整性由 Python 检查”的数据库规则不一致。以新增量迁移同步 fresh DDL 收口，先验原应用写口完整性与同事务有序清理；不能让终态 root 删除隐式清掉仍需核对的控制或投递事实。
- 回退只影响新提交的路由，已经被接受的 runner 仍由 AgentRunner 排空、暂停或显式取消；不能关闭服务后把未完成任务直接交旧 Agent 从头重跑。
- 新表和已接单任务回退时保留，不做破坏性降级。入口开关不能让同一请求同时进入两条路径。
- 默认Web切换还须处理旧已打开页面：现旧前端仍直接POST `/api/chat/stream`，该接口仍执行legacy反馈流程，仅同步JSON入口显式Runner header转接。新页面走Runner时不能让旧页面在同一现有会话绕过持久claim并行执行；按实际调用者和可信认证边界提供原协议兼容转接或等价占有协调，不能依靠客户端自报header或强制用户刷新作为完整迁移证明。当前默认未切，属于源码发现的M7门槛，非已验证线上故障；未迁移的D1/Runtime/其他入口须保其授权范围。
- 旧Stop仅取消旧SSE manager，服务端转接stream后也须按fresh主体/原Web会话取消其activeRunner，不能返回假成功。默认切换前协调已在运行的legacy owner，原SSE断线同样detach；兼容stream只转换安全公开投影，不再执行/写历史/结费用。旧payload无稳定请求键，不能按正文hash合并两次同文案发送；明确每HTTP提交键，现代稳定键保原。验收旧已打开页与新页同SID并发Send/Stop/断线、单执行/历史/费用及未迁移CLI/D1保持。仓库未发现CLI/Runtime直接调用stream只属有限检索，不据此推断仓库外客户不存在；按fresh登录、实际ownedWebSession及服务端迁移策略分流，不按User-Agent猜入口。
- 未迁移入口保留旧方法；Runner 跨进程可启动不是生产部署完成，真机部署和真实外部写操作按用户授权执行。

## 11. 验证方法与后续接入的记录要求

本重构已完成。以下方法保留供后续渠道接入或相关修复引用，实际命令按变更范围选择；新增测试路径在创建后列入对应完成记录，不将计划命令标为通过，不重启 master 容器：

- 后端定向测试默认使用 `bash ./scripts/dev_test.sh --isolated-db <本阶段测试路径> -p no:cacheprovider -q`。M0 独立测试发现全局 fixtures 即使 unit 也会清理测试命名租户，不能直接运行在共享业务库。
- 真实数据库竞争/迁移：使用 `bash ./scripts/dev_test.sh --isolated-db <测试路径> -p no:cacheprovider -q` 或经核对的隔离测试环境；不在共享业务库故障注入。
- 前端：`cd frontend` 后运行相应 `npm test -- <定向路径>` 与 `npm run build`。
- Runtime/Provider 未修改源码时优先相关协议/代理回归；若修改对应客户端，按其现有 npm 脚本验证，不自动重新打包客户版本。
- 每个高风险代码阶段完成后安排独立测试和独立 CR，记录真实命令/退出状态、环境、问题与未验证项；修复后只重跑受影响检查。

首次验证确认现有容器挂载当前工作区、解释器和依赖。import/启动检查在隔离服务中执行，不触发生产调度。相同代码与环境的有效结果复用，不机械全量回归。

后续事项只有在对应验收完成后标为完成；环境受限的真实链路保持待验收。新增详情与证据写入对应渠道或修复计划，ideas 只登记独立待办；本重构已归档至 ideas_finished。没有“提交代码”授权不提交/推送，没有明确部署指令不部署。
