# AgentRunner 独立服务开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| M0 | 契约、责任归属与行为基线 | ✅ 完成（2026-10-01） | 核心契约审查通过；旧基线后端434/前端28；原9条测试顺序失败已修复重验 |
| M1 | 核心职责拆分、执行隔离与恢复边界 | ✅ 完成（2026-10-01） | 源码独立CR通过；53核心/装配、58关联及最终244项均有通过证据；不等于服务已交付 |
| M2 | 独立服务：持久接单、执行与查询 | ✅ 完成（2026-10-01） | API37、仓储/计费38、执行/owner43项分别独立验收；最新源码CR通过，后续恢复/四入口待迁移 |
| M3 | Web 接入：提交、轮询、刷新找回 | ✅ 完成（2026-10-02） | 普通Web/opt-in同步：后端32、前端44、原页面5步骤通过；24源CR/SHA/build验收，完整等待续接及默认切换依赖M4 |
| M4 | 暂停、继续与进程中断恢复 | ✅ 完成（2026-10-02） | Web/Local/Browser服务端及原页面续接验收；卡片正式15＋开发1；v2 CR0，425源/102辅助封存；默认迁移及外部真机仍M7 |
| M5 | 实时事件订阅增强 | ✅ 完成（2026-10-03） | 事件账/代理已验收；前端26＋真实页面1、关联6、CR0/build0；552源/32辅助末核一致 |
| M6a | 微信客服接入 | ✅ 完成（2026-10-04） | 前任交接后接手收尾：2条失败均为测试自身错误（证据见M6a接手收尾记录），修复后尾批18用例两轮全量各17/18、失败均为隔离库连接抖动且单跑通过；真机/外部环境未验证，开关默认关闭 |
| M6b | 飞书接入 | 📋 待开发 | — |
| M6c | 钉钉接入 | 📋 待开发 | — |
| M7 | 四入口与 Runtime+BOSS CLI 综合验收 | 🔧 进行中 | 性能整改①完成：每执行全量资源装配 15.5s→亚秒级，新会话端到端 22.0s→4.1s(暖)/6.9s(冷)；66+54 定向测试通过；controls SQL FK/级联已按库规则收口（2026-10-04）：增量迁移撤 FK+fresh DDL 同步+4 处 fixture 有序清理，定向 20+10+1 通过；adapter 契约版本机制落地（2026-10-04）：派发 stamp+两道恢复门，单测20+集成3 通过；图片改可信产物引用+持久化字节上限落地（2026-10-04）：durable 装配点只存 runner_image 引用（登记 resources.image_artifacts、落 owner workspace），ModelAdapter 按 owner 校验深拷贝装配 wire data URL 不回写 state；limits（request/checkpoint/snapshot 字节）入口 413 与全部 checkpoint/快照写点 capped dumps；定向 134+回归通过；KF 拉取 SDK client 有界复用收口（2026-10-05）：同键页外调 3.0→1.03 次/页，池按 (account_id,config_version)+corp_id/secret 硬校验，进程内有界（容量16/空闲300s/页失败/关闭即弃），定向 13+关联 5 通过；十项整改全部完成并经独立复核（2026-10-05，每项一行登记见 M7 段增量登记，完整报告 out/m7-report.md）：legacy 测试分类/压缩阈值绝对上限 60k/前端提交失败提示（build 退出码 0）/FK 收口核验/认证收口核验/adapter 契约版本复核/图片引用+字节上限复核/KF 池化复核/real_browser sidecar 供给/分进程部署配置就绪；容量测量完成：吞吐 1.30 runner/s、瓶颈为每执行≈60 次远程 DB 事务、cost12 鉴权 173ms/验（建议 token 校验进程内缓存后真机复测），不外推生产承载；待用户授权：提交代码/agent3 部署/默认入口切换/真实渠道外网验收 |

> **2026-10-04 停止交接：** 用户取消剩余 M6a 开发和验收；最后独立回归实际16通过、2失败。[正式交接](agent-runner-m6a-handoff-2026-10-04.md)。
>
> **2026-10-04 接手收尾完成：** 接手者核验交接证据属实后，用探针实证将2条失败定位为测试自身错误并修复（生产代码零改动）；尾批18用例全量回归两轮各17/18，两轮中先后失败的转人工/子任务澄清用例均为远程隔离库（124.222.3.254:5433）高负载连接拒绝，单独重跑通过。细节见下文「M6a 接手收尾记录」。真机/外部环境验收、M6b/M6c/M7 未开始。

> 制定日期：2026-10-01
>
> 设计：[AgentRunner 服务架构设计](../system/agent-application-architecture-design.md)
>
> 实施已获授权，M0–M5 分阶段验收完成，M6 渠道迁移实施中；默认迁移、综合验收和真机部署按后续门槛。主控担任总工程师，负责协调、需求/架构/代码质量验收，不写生产代码或测试；实施、独立测试和独立 CodeReview 分工进行。实现涉及启动、并发、鉴权、计费和数据库，属于高风险；每个代码阶段均须独立验证。

## 1. 交付路线与范围

**先完成提交和查询，再完善中断恢复，最后增加实时订阅与渠道。** 不要求四个入口及全部内部重构一起完成才验收。

- M3 是第一个可体验的 Web 闭环：提交被持久接受后返回 runner_id，页面关闭不影响任务，重新进入能查询进度和结果。
- M4 完成后，Web 的持久执行与中断续接达到本期要求。M3 不能提前宣称已支持故障恢复。
- M5 只增强实时体验。查询始终是准确状态与最终结果的依据，关闭订阅后整套功能仍可运行。
- M6a/M6b/M6c 各自独立切换、验证和回退。渠道由后台适配器消费结果并回发，不要求最终用户主动查询。
- Runtime+BOSS CLI 从 M1 起就是兼容约束，M3/M4 做针对性链路验证，不能等最后联调才发现客户工具不可用。
- 不将定时任务、CLI 调试、企微个人 RPA、桌面 D1 或 Runtime 自动会话决策接入新服务；其功能仍须兼容。核心上下文、主/子共用 Loop、工具派发和生命周期必须真正拆分，不做通知中心或多执行节点自动扩容。
- 内部接口与代码均可调整，迁移保留原 Web 界面和用户操作。查询/订阅变化在调用层完成，不要求用户学习新流程，不新增暂停/继续按钮。

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
- M2 同时建立 `agent_runner_session_claims`：按 scope_key/kind/session 唯一、owner_runner_id 与 revision 表达逻辑占有，与执行 lease 分开。领取与取得 claim 同事务；paused/waiting/interrupted 保留位置，queued 不占有，resume 只能续原 owner。Web 结果提交后释放，渠道执行终态不提前释放，要等发送处理按原规则收尾。只按 runner 状态建 partial unique 无法表达这个发送边界，因此建立独立 claim 表。
- M2 建立 `agent_runner_usage_receipts`：调用/用量事实与结算水位单独保存，迟到事实在原 record 补差，不借用公开事件的顺序或保留策略。
- M4 建立 `agent_runner_controls`：持久保存控制请求、输入摘要与单次消费事实，和公开事件、费用账本分开。实际接口统一为 controls 资源，不再分别建立 pause/resume 路由；browser_complete 只允许受信完成端口，公共接口不接受自报工具完成。
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
| Browser ToolSuspension、兄弟调用结果配对、原调用续接、D1 defer | 等待/控制适配及持久 tool ledger；旧接口适配保持 |
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
| Browser 等待未处理同轮 siblings、D1 静默只取首个 call | 保留旧配对和等待语义；D1 不支持的并行调用明确失败，已收到的模型用量仍记录 |
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
- Web 选择完成时写聊天历史：接单事务只持久保存 runner 输入及可见输入投影。M3 刷新从历史与待执行 runner 重建消息，按稳定标识去重；当前执行只读先前已收尾历史，加本次输入，不加载未来排队任务的消息。渠道保留原合并后写入边界，并明确持久 cutoff。
- claim 仍与执行 lease 分开；领取 claim 与 queued→running 同事务。持久 queue_order 保证会话最早 queued 优先，SKIP LOCKED 不能跳过被锁首条让次条取得 claim。每个 runner 共用一把控制写锁，在锁内捕获 JSON 深拷贝并按 attempt/worker/lease/expected revision 写入，避免并发 child 的旧快照后写覆盖新快照。租约失效先 interrupted，不从头重发未知工具。
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

M3 可先验证普通完成、轮询和刷新，完整 Web 无缝切换还依赖 M4 原 runner 等待输入/人工协助续接：waiting 保留 claim，补充消息不能只排一个新 runner 而永久堵在后面；既有 browser continuation callback 不能另启旧 Loop 或重复写历史。M3/M4 交叉门槛未闭合前不默认启用新 Web 入口，也不宣称澄清和人工协助已迁移。CLI、desktop D1、`client_routes` 与尚未切换渠道保持其既有职责，不能按共用 URL 粗略切换所有调用方。

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
- Browser 通过应用恢复适配器绑定 owner_kind/runner_id、child execution、原 call/run/assistance；旧记录沿 legacy callback，新 Runner 只消费原 wait 的完成事实，不另启旧 Agent 或自行写历史。API 控制/截图/完成人工协助须路由到真实 browser runtime owner，复用现有 run/job lease；owner 丢失且资源不能安全重建时明确 BROWSER_RUNTIME_LOST/verification。Web 默认启用前必须验证活 owner 的人工接管→完成→同 runner 原调用继续及唯一历史/record，不承诺浏览器进程灾难后无损恢复。

### M4 开工审查（2026-10-02）

独立架构审查批准先实施控制账本、原 claim 的恢复领取和完整执行树装配。新增 `agent_runner_controls` 私有命令表与 usage/events 分开：采用更严格的 runner/client_request_id 唯一键，摘要包括 action、wait、目标 execution、回答及附件；先验证当前主体，再处理幂等，同键换动作或异摘要409。同一 wait 只消费一次，消费事实、checkpoint CAS 和 control revision 同事务，原接单 input/hash 不变。恢复领取核对原 claim owner，与新 attempt/lease/fence 同事务，锁后用数据库时钟；旧 attempt 不得继续写执行事实。

用户请求暂停与 worker 内部停止分离。父执行及全部 owned child 到达可恢复安全点后才确认 paused 并释放 lease；取消优先，尚未确认暂停不能提前继续。RecoveryCoordinator 只编排仓储、授权、恢复装配和工具恢复端口，不重新收纳所有工具分支。已完成结果复用，未知派发默认等待核对，不开放调用方自称完成或强制重发写工具的通用开关。

浏览器画面与输入目前归属具体执行进程，不能仅保存 complete 命令便宣称跨进程协助已可用。接口细审批准复用现有浏览器 IPC/frame，增加同事件循环、随 worker 停止的轻量内控端口，帧不入数据库或 Redis。API 依据受信配置 origin 核对 worker advertise URL，禁止客户端/数据库任意 URL 和 redirect；API 原子消费票据后重新核对 fresh 主体及 run/owner，内控端口再次核对原 runner/run/assistance/execution/call/owner epoch、真实 runtime 和 job lease。新 owner 不沿任意旧 BrowserResumeWorker 领取后永久失败的路径；观察 socket 断开不取消执行。

Browser 后续只读接线盘点确认：复用 `BrowserAutomationTool.execute`、HumanControl 的 suspend/get_owned_runtime/complete、`LocalPlaywrightExecutor` 的帧循环与 ViewHub 的有界最新帧、`browser_runs.py` 的原接口及前端 BrowserView。现 HumanControl/BrowserResumeJob 的另一个 HTTP/job 任务没有已停驻 Runner 的 usage/fence 上下文，不能直接调用 orchestrator.resume_from_human：人工完成接口只存可信 run/assistance/原wait完成事实和内部 control，原 Runner 新 attempt 消费后才通过 Browser adapter 继续同 runtime，绑定本次 usage/fence。人工观察的 Browser owner epoch 与 Agent 执行 attempt 分开核验；Runner-owned 记录不能走旧 `_continue_browser_agent` 的新 Agent/历史路径。二次人工协助生成新 wait，owner 丢失明确核对，不自动新建浏览器重演动作。本段为源码预审与后续门槛，未实施/验收。

一次精确源码盘点进一步确认：ToolSuspension 返回后原 Browser manager 仍续自己的 lease，不随 Runner park 自动失效；原 Browser 审计仅 tenant/user/session/child execution/call，缺少 root Runner/worker epoch 绑定，assistance 审计还可 best-effort。Runner 分支必须强制 PG 证明原 root→完整执行树 execution/call/wait 与 run/assistance，不以同 session 或 Redis 对象自证；legacy 审计策略不作为 Runner 授权。普通 WS 断开只关观察；用户取消和 worker 停止则回收对应 manager/runtime/monitor，关闭确认参与取消收尾。当前 Worker 退出并未回收 parked Browser，需随 sidecar 生命周期补齐；`--once` 进程退出后原 runtime 明确丢失，不承诺跨进程重建浏览器并重演动作。daemon 驻留、原帧/键鼠链、fresh 授权、取消回收与二次 wait 均是实际 Browser 分片门槛。

本地子片同样通过接口细审：派发 fence、phase 意图与原 invocation 插入/关联使用同 PostgreSQL cursor 事务，不能仅做锁外 precheck 后插入设备可领取任务。逐阶段核对全部可信 owner、参数、设备、分支及 retry key；专项入库和费用仍由原 owner 收尾，缺事实则等待核对。M3 最终测试期间其24源保持冻结，先新增 M4 模块及 schema，不污染有效验证版本。

Local 实际源码预审补充：原 invocation.credit_cost 仅表示设备结果终态的工具费，不证明识别、遮挡处理等专项费已结算。简历 VL 两轮调用原规则按份收费覆盖模型成本，Runner 仍保存物理调用事实与统计，但不能又按普通 token 加收费；原 overlay 模型费与 heal 专项费规则分别保留。现专项费用、简历入库和通信记录无原 invocation/item 唯一恢复事实，不能整工具 execute 重放。采用私有 checkpoint 内稳定 branch/ordinal/item phase，与原领域 DAL cursor 接口将费用/余额或业务记录 ID 和 applied fact 同事务保存；复用原领域逻辑，不增通用账本，RecoveryCoordinator 不承载领域后处理。文件引用须稳定且归属可信，旧未知写入无证据继续 verification；孤立 Local 仓储2源 CR 通过仍不等于已接通整个恢复链。

后续完整 Local 接线覆盖五组：普通 BOSS 遮挡处理的 inspect/模型选择/dismiss/retry/heal费，单份/批量简历的逐份评价/姓名门槛/识别费/稳定图片/入库/匹配字段，SendTo/Current 的 script查询与真实发送后处理，云端只读 JobsList，以及 InterviewNotify。费用复用 ClientUsageLogDB cursor，简历与沟通复用已抽 cursor；匹配仅更新已有评价字段，不新增模型，通知日志另抽原 DAL cursor。识别费已成功、后续入库失败不退款；batch 同 invocation 按 item ordinal 各自保存，不能整批重放。VL 的覆盖定价仅可信领域 scope 能设置，普通 overlay 的真实 token费保持；模型返回到解析事实之间须保存原响应/receipt或明确核对，不能随机换调用 ID。

通知实际发送不是固定两次：原 markdown 按2040 UTF-8字节分块，另有 @ 提醒，旧网络重试可能重复未知发送。Runner 路径按实际 chunk/retry/@保存 started 与可信 ACK，未知网络效果等待核对而非自动重发，单次业务日志不随块数重复。沿原配置/模板/HTTP错误解析，不把 webhook/密钥持久化；非 Runner 保旧重试。恢复端口组合各领域 adapter，不在 Engine/Worker 加 BOSS 分支或新增通用 workflow。以上为源码预审/实施范围，普通调用子片与完整领域恢复分别验收。

完整领域取消门槛还包括通知等非设备 HTTP：不能仅凭 local invocation 都已终态就放 claim，而原 HTTP 仍在途/效果未知。按原领域 started/ACK事实组合核对，取消后禁新 chunk/@/业务动作，但仍有效的原 owner 可记录已发生结果及用量；启动的派发许可与原结果的完成保存分别校验，不为避开取消把未知写入视为只读。独验使用 localhost 真实单请求/ACK屏障和原phase仓储证明，不能只替换 sender boolean。领域接线尚未验收。

M3 最终放行后解除共享源码冻结。M4 首片现将 `control_contracts`、`control_repository`、`recovery_repository`、双 DDL 与系统表文档按 [6源清单](/private/tmp/aid-agent-runner-m4-controls-source-sha.json) 正式冻结，交独立 PG 测试与源码 CR 并行。变更号为 `2026-10-02 00:33:38`；静态 AST/pycompile 自验通过。迁移首自验1失败为旧 fixture 先 DROP agent_runners 被新 controls 外键阻止，fresh DDL 初始化实际成功；独立测试按正确表顺序调整 fixture 后复验，不移除生产约束。此片仅控制接收/原 claim 恢复事务，尚不证明实际 worker 安全暂停、执行树恢复或 Browser/Local 续接；实施继续其他模块，不改冻结6源。

6源正式独立 CR 已通过，SHA 零漂移、双 DDL 内容等价，无未关闭 P1/P2；核对当前 owner 先幂等、wait 消费串行、单 resume pointer、默认拒绝自报 Browser 完成、锁后数据库时钟、原 claim gate、新 attempt/消费/checkpoint 同事务、取消/终态/新暂停竞争和私有字段白名单。外键级联符合私有表生命周期，fixture 按子表先删除，不降生产约束。独立真实 PG 测试继续；应用 ackpause 仍须验证全部 owned leaf 安全，仓储只认根 paused/unstarted 标记不能替代 worker 验收。

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

Browser最小落地只读预审按四片推进：可信归属→跨进程观察→原调用续接→关闭确认。原 `run_db` 缺root Runner/worker/wait，create/suspend审计允许best-effort；Runner分支必须改为可信执行树/原call/wait/run/assistance/browser owner epoch的PG绑定。原API票据后直接订阅本进程、缓存身份不足，票据须带可由服务器fresh复验的登录授权参考，不能仅持subject字符串自证；消费票据后及sidecar实际owner均重核权限/归属。复用 `Controls.assert_completion_in_tx` 与 Recovery有限completion口，人工完成不入旧enqueue_resume→新Agent/重复history链；原Runner新attempt续同orchestrator。页面断开仅detach，显式取消须实际关闭证明；远端request_cancel成功不等于已关闭。Worker停服回收parked runtime/manager/monitor，owner丢失或 `--once`进程退出明确核对而不重建未知动作。具体最小DDL/配置/API开工另审，当前仅预审，未部署或运行浏览器验收。

Browser公开卡片沿用现 `presentation.browserAssistance`→`runnerMessages`→HumanAssistanceCard。当前投影只设置 human_required，`useAgent.hasBlockingBrowserRequest`仍将pending/controlling/resume_queued视为阻塞；新分支在完成、取消、owner丢失及二次wait必须提交正确公开状态/ID，不能Runner终态而卡片仍pending。M4查询本身需足以刷新卡片，不依赖M5订阅；现MessageItem的旧continuation流会追加response，新Runner不得与累计轮询并行重复追加。安排原页面窄适配和对应刷新/二次wait/唯一输出验收，不换界面。

页面接点已只读核实：HumanAssistanceCard挂载及完成成功都会查询旧continuation，MessageItem将其中response追加正文；Runner-owned消息按可信runnerID分流，只观察原Runner，legacy续接保原。取消接口接受后也不能直接沿旧卡片的本地cancelled状态宣称浏览器已关闭，须由实际关闭证明与公开查询状态更新。旧assistance/wait的迟到结果不得改写二次协助的新卡片，本段属于后续接线及页面验收门槛。

Browser第一片精确盘点：run_db实际使用的 bs_browser_runs、bs_browser_assistance_requests、bs_browser_resume_jobs未在现双DDL定义；不假定线上已有或结构相同。补实际基础列及existing增量nullable Runner字段，legacyNULL保原，不凭CREATE IF NOT EXISTS认为旧表已补列。原root/execution/call→run唯一，mandatory启动行starting、原RunStore租约与executor启动后才live；二次wait保同run新assistance。原jobs只初始化兼容legacy，Runner不往旧恢复队列写。实际DDL/绑定/端口第一片稳定后正式审查和独验，后续sidecar/亲和/原调用完成/关闭/公开card分别接线。

首片预审补充两个竞态门槛：原等待ID复用 `DurableControl.decorate_wait` 的root/execution/call/kind/assistance语义，PG绑定与Engine不能各造一套ID。人工完成或自动监测可能在Runner尚running、尚未park时到达；须先保留可重试的可信完成事实，再与原wait幂等桥接内部控制，或提供等价停驻协调证明。不得先将Redis标completed，再因现控制仓储要求parked而丢失完成机会；不能为此放宽所有普通控制的状态守卫。实际续接片独验覆盖完成早到、重复完成、park后消费及二次wait，当前仅预审，尚未验证。

首片8生产源已实施至仓储/协议层，尚未装配Runtime/Manager：双DDL、系统表文档、Browser绑定仓储/受信endpoint/中性owner端口、共享wait身份和DurableControl复用。独验准备17个有界真实PG形态，涵盖fresh/增量/replay、legacyNULL原行、部分绑定拒绝、root/child原call与二次wait、跨身份拒绝、真实CAS回滚、锁等待跨租约到期及Browser lease独立于Runner park；native执行树fixture不冒真实Browser启动证明。开发首迁移1 failed、exit1（0.81s）在测试提前访问尚未由原migration runner升级的watermark列，未进入Browser增量断言；仅修测试tracker初始化并保失败版本，生产不为此改变，正式冻结/独验待完成。

Browser storage v1正式按 [8源清单](/private/tmp/aid-agent-runner-m4-browser-storage-v1-source-sha.json) 和 [7依赖见证](/private/tmp/aid-agent-runner-m4-browser-storage-v1-dependency-witness-sha.json) 冻结，主控核对15源零漂移；开发迁移同1重验 passed、exit0（7.29s），8源起止一致，首失败保留。[交接边界](/private/tmp/aid-agent-runner-m4-browser-storage-v1-handoff.txt) 明示仍未装配实际Browser/Redis/runtime。正式CR与独立17 PG形态并行，类型及同名约束/索引旧结构兼容也纳入审查；编译/仓储activate状态转换不冒真实executor启动或Browser整体通过。

独验首17实际PG形态 passed、exit0（56.92s），15生产/依赖及8测试辅助源统一首末零漂移；基础仓储正确性已有证据，尚未正式放行。CR发现P2旧结构缺口：错误fencing类型、同名非唯一/错误predicate索引或弱/未验证CHECK可被IF NOT EXISTS静默保留，安排旧版最小真实复现与有限修复。主控核原migration runner逐语句savepoint容错、失败只阻止水位而不回滚整个块；本Browser DDL及catalog检查须自具原子语句边界并在真实runner下验证不兼容时无部分应用，不为本片改造通用migration框架。

v1正式CR起止8源及7依赖全匹配，P0/P1=0，剩余P2共3：上述旧结构及迁移原子性为一项；`bind_wait`在Browser lease检查后还可能等assistance锁/唯一插入，提交前须独立fresh复核原live epoch租约；currentAttempt.worker须等于原Browser owner，activate/wait还须核实际run完整tenant/user/session归属，不能借原CP引用换执行进程。安排旧版真实PG最小复现后只修该Browser仓储和本块DDL，首17通过不替代这三项门槛，不提前放行或接通生产入口。

旧版DDL最小实际复现1 failed、exit1（7.13s），15源/9测试辅助源统一首末零漂移：将原owner租约列改成TIMESTAMP后调用实际migration runner，其报告82条成功并将水位从00:33:38推进到07:56:08，错误类型被静默认可；测试原拒绝断言失败，finally恢复原类型并实际replay至fresh结构。另3个native仓储旧版负向复现尚在运行；未改变受验生产源，不把旧缺陷复现计作通过形态。

旧native负向3 failed、exit1（9.18s），15源/10测试辅助源首末零漂移：真实唯一INSERT锁等待跨Browser到期、有效current worker B借live A、实际run用户错归属的activate均未按原断言拒绝；foreignwait后续断言因activate先失败尚未执行，不补称已复现。原等待事务已rollback、线程join及fixture清理完成，旧日志/测试版本保留。主控仅释放双DDL与Browser绑定仓储供v2定向修复，必填当前受信boot/epoch并做原worker/完整run归属与提交前Browser租约检查，其余共有依赖不扩改；修复后的实际独验/CR待完成。

v2有限DDL采用本块单一原子DO及实际catalog校验，两个事务内临时LIKE参考表显式解析预期CHECK后按各自relation规范化对比，结束DROP，不存业务数据；不比较含物理列序的raw conbin，也不改通用更新器。首开发fresh setup exit2为PL/pgSQL CASE条件括号SyntaxError，尚未进入pytest，原版本/日志保留；修正后实际fresh成功、同迁移1 passed（3.59s），随后原fact身份核对收口后的末版定向开发自验与25形态正式独验待完成。

Browser storage v2 [8源码](/private/tmp/aid-agent-runner-m4-browser-storage-v2-source-sha.json)/[7依赖](/private/tmp/aid-agent-runner-m4-browser-storage-v2-dependency-witness-sha.json) 正式冻结，主控核15零漂移；[版本变化](/private/tmp/aid-agent-runner-m4-browser-storage-v2-delta.json) 限双DDL、绑定仓储及必要表文档，其余4源/7依赖与v1相同。末版定向开发5 passed、exit0（17.35s），8源真实首末一致；开发依赖7末版对比先前v1真实见证相同，不伪造新的developer依赖起始捕获。[v2交接](/private/tmp/aid-agent-runner-m4-browser-storage-v2-handoff.txt) 明示仍仅仓储/协议基础层。

v2正式delta CR通过，旧3P2关闭，剩余 P0/P1/P2=0，8源及7依赖起止零漂移；单DO实际split一条、原子catalog验证/临时表回收、当前boot/epoch/worker与原完整归属及提交前独立Browser租约核对均经源码复审。独立25实际PG形态仍运行，15源/10测试辅助源统一capture成功才launch；正式放行须两道门共同完成，不以CR代替实际数据库证据。

**Browser仓储v2基础片正式验收通过（2026-10-02）**：25个不同实际PG形态（8迁移/约束、17明示native原调用仓储契约），完整末批 passed、exit0（77.49s），不是旧17加新25累计42；旧DDL1及native3失败保原加载版本和日志，修后拒绝/回滚断言全部到末。主控核 [独立完整证据](/private/tmp/aid-agent-runner-m4-browser-storage-independent-evidence.txt)、[25范围清单](/private/tmp/aid-agent-runner-m4-browser-storage-independent-inventory.json)、[最终15生产/依赖](/private/tmp/aid-agent-runner-m4-browser-storage-independent-source-final.json) 与 [10测试/辅助源](/private/tmp/aid-agent-runner-m4-browser-storage-independent-tests-final.json) 零漂移且源完全匹配v2；正式CR无剩余 P0/P1/P2。

实际fresh/增量/replay/legacyNULL及兼容旧ID/时间类型保持；不兼容catalog在真实更新器下水位不进、整schema及三表原行无部分改变，锁等待过期与错worker/boot/epoch/实际run归属拒绝均成立。[实际清理](/private/tmp/aid-agent-runner-m4-browser-storage-independent-cleanup.json) 自有进程/fixture根0、自有隔离库均不存在，session-local参考表随已删除测试库不存在，不补称曾查询保留会话tempcatalog；不明旧库0连接排除不删。批准释放15源进入真实producer接线，仍未启动Browser/Redis/helper、未证明实际executor启动/人工接管/跨进程续跑，完整M4与默认切换待后片，不提交/部署/master或Yohar重启。

下一producer片保持中性工具owner端口由应用层实现；原Browser Manager强制PG starting先于公开注册，实际executor及原Redis fence成功才live，park保原runtime与独立Browser lease。当前旧Automation/Orchestrator的广泛catch Exception会把权限/CP失败转换普通工具失败，Runner owner权威异常必须透传到Runtime/Worker中断处理，不能由此释放未知效果claim。共享root锁内只接受原调用资源事实和必要child→祖先CP，不能用旧全树覆盖其他live子任务消息/模型/阶段；实际关闭证明、完成早到桥接、fresh票据/sidecar及原卡片传输继续逐片验收。

producer预核补实际关闭基线缺口：原Manager先移除executor引用后吞close失败仍标终态，LocalExecutor force_reap二次wait超时仅log也可能返回closed=True；新Runner必须依据实际回收/CloseResult，未确认保原引用/claim并核对，不能以取消请求或乐观close返回当关闭。只回收本次确属资源，不全局清浏览器。权威异常穿透覆盖内部重试、fill Enter忽略异常及PageOps将fence错误转普通结果的路径，不只外层catch。临时producer能力未开启的durableRunner须任何Browser IO前明确不可用，不降到legacy人工恢复链；待完整Browser能力闭合，标准Runner自动使用原owner，不要求客户额外配置。

后续真实续接还须核Agent动作scope与原FencedExecutor owner对象的一致性：有Agent scope却不匹配不能静默当human/frame跳过当前Attempt核验。新attempt有限重绑定原manager/原Browser epoch的control/state/UsageScope/ToolCtx，不新造Browser epoch；人工输入和帧继续独立Browser租约及fresh授权，不能被旧已park的Runner lease绑死。届时实际第二attempt命令与用量/fence共同验证，本段未称已接通续接。

后片只读复核还确认：原人工确认sampler借用orchestrator.page_ops，而其executor已由Agent owner包装，停驻后旧Runner lease会拒绝快照。人工确认须通过原Browser租约的观察通道，保持同一个底层executor、命令序号及IO锁，不另造PageOps计数器或放宽Agent动作fence。原恢复候选全局LIMIT 1且Worker的RunnerError会reject_resume；Browser亲和必须在prepare/reject/消费前先筛，并在短领取事务中重核当前worker/boot/epoch/DB clock，非owner跳过仍活owner的控制，有界扫描不能阻塞其他会话。完成事实只标原leaf待恢复，实际原orchestrator续原call后配对结果，不把未经续跑的raw fact伪作工具completed。本段为源码确认及后片门槛，观察片仅复用Hub，不承担人工确认或恢复。

跨进程鉴权提案已审：私有一次票据引用实际tokens.id，不保存bearer；消费后和sidecar分别fresh核原token行、当前用户/角色/tenant/会话及Runner/run/epoch，每input/complete/cancel都核，帧周期复验。完整内控endpoint路径由服务配置固定，不只核origin；非owner跳过候选而不消费/拒绝清掉活owner控制，领取事务前重复worker+boot亲和，无队头饥饿。原continuation task安装新UsageScope/ToolCtx/fence。

本机仅找到缓存redis:7-alpine镜像、未有Redis服务/二进制，memory降级不能证明跨进程票据/lease。实际Browser验证阶段已启动task专属临时依赖helper，共享现测试容器网络、仅127.0.0.1独立端口，无publish/持久卷；测试角色管理准确资源清单并结束清除，不改现master/Yohar配置或重启。实际PING成立，缓存镜像无拉取、只读容器及tmpfs无持久数据卷，资源归属见[依赖账本](/private/tmp/aid-agent-runner-m4-browser-producer-redis-ledger.json)。此为本机隔离验证依赖，不是项目代码部署；真实Browser与票据/lease尚未认证。

真实producer首次开发窗90671 exit 1（18.58s），18生产源首末一致；再次74973 exit 1（19.23s），19生产源及3测试辅助源首末一致。实际测试页面GET成立，但Browser行已FAILED/closed、原工具已completed，等待到的下一模型请求是主模型而非Browser决策，因此不把等待信号当实际Browser成功。保留各失败原源码与测试版本；原PageOps返回稳定错误码的只读观测继续定位，不放松live绑定、实际资源关闭与计费断言。单独实际Playwright/Chromium about:blank启动及关闭成立，仅证明依赖可启动，不代替原worker协议或producer验收。

关闭预核再发现原worker_main先置closed=True再吞子资源关闭异常，会向父进程返回不真实ACK。有限修复要求实际page/context/browser/playwright关闭成功或原生可证已关闭后才清引用；失败返回CLOSE_UNCONFIRMED、保留待核资源，不能仅凭父进程退出推断子资源关闭。Worker只调用通用factory生命周期hook，具体Browser回收由应用组合根在任务drain后、PG pool关闭前按当前boot完成。本段为开发与审查门槛，实际严格关闭及legacy兼容仍待独立验证。

第三诊断窗85637 exit 1（17.82s），原PageOps观测确认PG starting先于Redis公开、原executor启动成功、导航成功，但实际snapshot返回WORKER_COMMAND_FAILED；实际close ACK及resource_close_confirmed成立。故当前故障收窄到原BrowserWorker snapshot内部，不猜Browser权限或模型原因。独立只读审查另确认Linux dead worker leader的提前return会遗漏原owned PGID里仍存活的Chromium：须沿启动时实际确认的原进程组尽力有界回收，不能猜组或全局kill；即便强收成功也不代替原关闭ACK，资源回收与业务关闭证明分别判定。失败page/context在祖先实际成功关闭后只能据真实关闭事实清除，不能把任意TargetClosed异常文字当证明。

进一步真实分层证明静态set_content后的body读取在semantic调用前已Target crashed，不能由单独launch成功或Semantic.generate的success/空元素推断页面可用。只读容器资源观测暂无OOM或pids/CPU限制证据，运行架构与renderer约束继续定位；不改master配置、重启或用简化快照隐藏失败。另冻结前CR发现P1：Runtime接ToolSuspension后的旧Redis get_assistance/bind_agent失败被转为普通工具结果，在mandatoryPG等待已绑定且Browser仍live时可错误释放claim；新durable路径须权威穿透或由原PG绑定明确承担归属，legacy政策保原。正式验证纳等待绑定后故障、无普通终态与claim保留。

producer candidate v1有限19源冻结，主控与独立CR核首末SHA无漂移。关闭ACK/祖先实际关闭、ToolSuspension权威marker、通用factory生命周期hook、Agent scope匹配、内部catch穿透及禁用0IO失败源读闭合；但正式CR仍有1项P1：原组消失后保留的历史PGID可被其他组复用，延迟shutdown再signal不得凭数字认原归属。按原Local内存资源handle核当前leader或原成员PID/出生身份，观察组空即永久撤销signal权；缺实际证明只保核对，不杀未知组，不新增全局进程registry。当前candidate不放行，真实producer仍未通过，关闭关联独验待v2稳定。

运行环境只读核实际aarch64容器/image/Chromium一致，未证架构混用；Playwright 1.62.0 / Chrome 151.0.7922.34，原stderr仅有限关键词分类见共享内存tmp目录EACCES，未输出原页面/日志。单变量实际诊断移除有效disable-dev-shm-usage（显式和Playwright默认）后静态页面读取与关闭成立；随后更有区分力的单变量保原flag、只改TMPDIR=/tmp，同样页面读取及关闭成功。实际ambient临时目录父非当前UID所有、祖先非全可遍历，service_processes另把TMPDIR设到pytest fixture.root。优先纠正测试专属Linux临时目录与renderer权限，主控撤回尚未实施的生产启动参数/compose修改指示，实施者确认两者未改。不因测试环境私有目录修改生产共享内存策略；默认CLI实际producer正向仍须通过。[Playwright官方容器说明](https://playwright.dev/python/docs/docker)仍作为容量门槛参考，现64MiB与一张测试页不构成复杂页面或并发容量证据，当前容器不重启/部署。

candidate v2正式delta CR通过，P0/P1/P2=0；19源首末SHA一致，仅Local原生leader/member出生身份及signal撤权修复，其余18沿用原审查。测试Browser-only私有Linux/tmp目录0700保原启动flag实际页面读取/关闭/目录回收成立，通用Processes仅该fixture作用期换TMPDIR，其他既有测试行为保原。首次默认CLI正向45893 exit 0，1 passed（21.09s），实际Browser决策请求/PG live绑定/独立Redis lease与Chromium后代成立，末端实际关闭、PG closed/lease清除、claim释放、3物理receipt合计54 tokens与唯一0.01 record均通过。实际预捕及结束936路径为源码/测试依赖witness，不是936测试；当前只是开发验证，不代替独立producer验收。原authority两形态、park与新增关闭/等待故障边界继续有限验证，不开启sidecar/续接、不标完整M4。

开发关联窗66454 exit 0，3 passed（57.21s），真实bind/activate PG故障穿透、wait先于suspension及park后独立Browser租约续期、SIGTERM实际关闭但原Runner claim保留到末，19生产及加载依赖首末一致。关闭窗61904 exit 1，5 passed / 1 failed（2.49s），失败在deadleader仍有活后代的前置，实际后代已自然退出、尚未调用close，不能冒生产reap失败。保原版本/结果；必要真实PID信号timing DI仅用于构造原组存活窗口，不替close/ACK/归属。独验准备14个唯一形态（原4、close6、等待后IO故障2、禁用/子执行2），实际CLI与原资源、真实PG触发器及有限IO/proc/timing DI口径分别登记，尚未称正式通过。

deadleader定向开发64099 exit 1（7.25s），原close已按预期返回unknown verification，后置因fixture STOP了非原PGID的Chrome而超范围要求全部退出；保失败，不扩大生产kill权。只STOP实际原组成员的有界真实timing DI改正后78551 exit 0，1 passed（2.21s），close6开发至末，原19及加载witness无漂移。有效开发10形态与后续独立14分开，不累加重验充数。正式窗口尚未启动前又确认WeakSet生命周期缺口：native非Human runtime、renew失败结束后Manager↔Owner循环可被GC，未确认关闭的原句柄从Factory可见集合消失。按原process资源容器强持实际manager，confirmed close与PGclosed均成功才仅释放自身；不新业务registry、不在CP保存对象，legacy弱引用保原。单BrowserOwner v3后追加GC/重试/释放隔离窄验证，再冻结独验。

下一观察sidecar片只读预审可开工但当前不写共享源：原hub帧/sendlock/latest可复用，原view_ws含输入不可整路拷贝；native只观察、明确拒input/complete/cancel，完整后片仍需恢复原交互。固定完整endpoint及专用gateway peer，单次票据持原tokens.id私有参考，消费/实际sidecar及无帧periodic均fresh校验，PG/native证明缺失不能回legacy。Factory通用open真实监听ready后才acquire，静态endpoint冲突failclosed；多worker按实际不同endpoint部署配置，不凭同URL猜owner。停服按tasks drain→停止新观察/关闭自有WS及资源→sidecar→PGpool，Browser独立租约支撑parked观察，不借已park的旧Runner Attempt授权。此为下一有限片门槛，非已接通sidecar或人工完成/续接。

candidate v3强生命周期delta正式CR通过，P0/P1/P2=0；19起止零漂移，仅BrowserOwner变化，其余18复用v2。实际native一call一manager及PG原call唯一run约束成立，确认资源且PGclosed提交后仅discard自身，未知/PG异常保原句柄，boot cleanup可发现。最终生产19＋共用依赖145去重164冻结。独验B1 native6 exit 0，6 passed（3.37s），实际164生产及5加载测试辅助源预捕/结束无漂移；原组timing、原生close IO DI及proc观测DI按真实口径记录。GC开发首2在不存在from_checkpoint的fixture调用前置失败（3.73s），尚未进入Owner/Browser IO，仅修新测试使用真实restore。后段实际ProfileCatalog初始化读取14个既有SUBAGENT资源，正式启动前另捕14成为178；先前只算所选profile的165准备见证被更正，不回填B1及关联批原164窗口，也不把路径数作新增代码或用例。

正式B2首批21214 exit 1，8 passed / 2 failed（169.69s），178源及27测试辅助源首末零漂移。禁用案例末断言错误地要求整个Redis命名空间为空，包含正常用户缓存；子任务在25秒期限前未达到末端断言，旧私有日志已随fixture清理，历史超时原因不补猜。定向B2r 49561 exit 1，1 passed / 1 failed（50.90s），原25秒诊断实际见5物理请求、两个child已完成、Browser已关闭、无owned Browser PID，随后CLI及Runner正常完成；末SQL误用不存在的agent_execution_id字段，尚未走完收据/claim断言。仅修测试使用真实runner_execution_id/runner_tool_call_id，保所有末端断言；原失败与加载字节保留，不改生产或加入兼容假列。

**Browser实际启动与归属小片正式验收通过（2026-10-02）**：末定向61992 exit 0，2 passed（46.50s），178源/27测试首末零漂移，实际child绑定、root及已完成sibling保全、6物理收据applied/正确execution归属、原claim释放全部到末；禁用时Browser及原工具等待/续接键为0，正常用户缓存允许。最终16个唯一新形态=实际Worker8＋原生关闭6＋Manager GC2，另10项旧协议/存储/实际Local关联通过（83927 exit 0，1.68s），重验不重复计数。真实默认CLI、驻留原方法probe、实际PG触发器、Redis方法IO故障、原组时机/proc观察DI及原仓储契约GC fixture分别标明，外部模型/页面为loopback测试服务；不声称真实Redis网络故障、OS PID数字复用或客户站点通过。

主控已读[完整独验](/private/tmp/aid-agent-runner-m4-browser-producer-independent-evidence.txt)、[唯一清单](/private/tmp/aid-agent-runner-m4-browser-producer-independent-inventory.json)，并实际核[最终178源/资源](/private/tmp/aid-agent-runner-m4-browser-producer-independent-final-source.json)和[35测试辅助源](/private/tmp/aid-agent-runner-m4-browser-producer-independent-final-tests.json)当前SHA全部匹配，复用正式v3 CR无P0/P1/P2。[实际清理](/private/tmp/aid-agent-runner-m4-browser-producer-independent-cleanup.json)自有Browser/Chrome/pytest进程、四类实际临时目录、fixture Redis键及自有隔离库/连接均0；不明旧库排除未删。唯一精确Redis helper按授权保留供下一片复用，不称所有资源归零，测试角色台账负责最终清除。批准解除源码冻结，唯一实施者进入跨进程只观察sidecar，独立测试/CR分工继续；人工接管、完成桥、同Runner原Browser续接及卡片仍未验收，完整M4/default Web切换不提前完成，不提交或部署。

观察片已进入实施：应用侧BrowserWebAuth/ViewTickets/Sidecar/ViewGateway与薄API分流，Factory中性open/close及实际ready检查；无新增DDL，不接人工输入或完成桥。固定内部端点本片仅支持直连HTTP，实际bind端口必须相符，HTTPS/TLS代理不冒充已实现；握手整体5秒有界，撤权先有界关闭外侧观察，再回收上游WS。单独真实监听/端口冲突/关闭自检通过只证明生命周期，不作为Browser画面证据。首实际park→独立API→sidecar→原Hub双观察开发窗95071已结束，189源/资源及39测试辅助源预捕；独立风险矩阵与测试仅准备，尚未正式验收。producer与view临时guard在完整Browser接管/续接/取消/card闭合时一起收口，不给客户遗留额外功能开关，服务端endpoint/内部凭据配置按实际部署保留。

首开发95071 exit 0，1 passed（24.99s），189源/资源及39测试辅助源首末零漂移，原Browser等待后的真实JPEG跨进程、双观察者单方断开与另一方继续、Browser lease/Runner claim保留及停Worker实际PGclosed均到末。本窗只证明正常查看，不算授权负向或正式独验。随后只读CR确认HTTP票据接口P2：原草稿先PG判别，再认证，匿名可凭404/503/401区分记录和配置；仅修薄API先fresh登录、native先完整归属授权再禁用状态/出票。前版通过保原source，后版定向happy与真实HTTP反向须重验。正常legacy PG行runnerNULL保原路径，旧best-effort审计缺行现在明确404 failclosed，不声称Redis-only故障行为全不变。

后版82540 exit 0，2 passed（47.70s），189源/资源及40测试辅助源首末零漂移；匿名及warm缓存后实际删除token的native/legacy/missing三路径均401，foreign不能先取得disabled状态，原owner才可见disabled503。主控已核[观察v1交接](/private/tmp/aid-agent-runner-m4-browser-observation-v1-handoff.txt)、[10变源](/private/tmp/aid-agent-runner-m4-browser-observation-v1-source-sha.json)及[189组合清单](/private/tmp/aid-agent-runner-m4-browser-observation-v1-combined-source-sha.json)当前SHA全匹配，10变源=9 Python＋YAML，共用code去重175＋实际SUBAGENT资源14。生产正式冻结，独立源码CR与真实授权/票据/无帧/传输/监听/关联独验已分派并行；未把两项开发通过或准备AST作为正式通过，不提前放行观察片或完整M4。

观察v1正式CR完成，189源/资源首末零漂移，P0/P1=0、P2=1：observe在await授权后才登记views，停服关闭快照可能漏掉在途握手，其后仍accept/subscribe。最小修法在首await前纳入原views，并在accept前再核accepting，沿原集合cancel/gather，不新增registry。当前独验3433实际8项窗口仍运行（189源/43测试预捕），先保持v1不改；原sandbox wrapper exit127未进pytest的启动环境失败单列保留。等真实退出与结束捕获，再单sidecar更新及delta CR，并补真实socket/原授权时机探针，不能以正常关闭案例代替在途停止竞态。

独验B1 3433已真实退出，exit 1，6 passed / 2 failed（165.40s），189源/43测试首末一致；正常双观察/HTTP先鉴权、warm-cache后真实token撤销/改绑/过期3、实际删除PG行而原Redis/Hub仍在时拒绝1分别到末。两个失败源核为测试期望/调度：平台管理员NULLtenant正向已收到真帧，降为普通角色后WS也及时断开，最后HTTP按既有TenantMiddleware确为400而非fixture预期403；无帧case在订阅前等待capture报告，但原frame_loop仅有observer才capture，尚未进入WS/撤权末断言。只修测试400与真实订阅后等capture次序，保无JPEG/撤权/lease要求及旧版本。主控授权先在v1做仅在途授权停止的真实socket时机反例，原Browser仍live以免资源关闭掩盖lateaccept；该窗口退出后才释放单sidecar修v2，正式观察仍未验收。

旧v1停服时机单例36324 exit 1（21.60s），189源/42测试首末零漂移：真实stop_observations已返回，释放原授权后仍WS accepted并收到原JPEG，而断言要求denied。原Browser保持live，故不是资源关闭、超时或假Hub结果掩盖，P2因果实际成立。原反例/源码/加载测试及finally清理保留，真实结束后主控只释放browser_sidecar.py最小v2修，其余188源/资源冻结；修后沿同一个反例和准确的两项fixture末断言验证，不扩大Worker/Engine或新增registry。

观察v2已单源修并冻结，主控实际比较[189 v2清单](/private/tmp/aid-agent-runner-m4-browser-observation-v2-combined-source-sha.json)与v1，只有browser_sidecar.py变化，其余188完全相同，当前所有SHA匹配；原生单源编译exit 0。连接登记前移至首await前，accept前再检查停止状态，原finally discard及原views关闭集合沿用。[单源交接](/private/tmp/aid-agent-runner-m4-browser-observation-v2-handoff.txt)交定向CR，正式独验原停服反例及两修正fixture和剩余风险边界继续；旧v1任何通过/失败按实际加载版本保留，不冒充统一末版通过，当前尚未验收观察片。

观察v2关闭竞态定向CR通过，189首末SHA一致。独验B2 46864 exit 0，3 passed（74.31s），189源/45测试首末一致：同停服反例如今真实拒绝但原Browser仍live/claim保留；NULLtenant平台管理员正向画面及降权后WS断开、原HTTP400到末；无帧case真实订阅后原capture调度被时机gate暂停，尚无JPEG时删除真实token仍5秒内断开，Browser租约继续。

独验B3 99430 exit 1，7 passed / 2 failed（139.01s），189源/48测试首末一致：实际一次性票据竞争、专用peer/完整proof、Redis出票IO失败、默认CLI监听冲突/HTTPS/端口不符、JPEG发送等待时撤权均到末。两传输失败是测试包裹路由缺WebSocket类型注解，框架在原探针前拒绝；保加载测试与失败证据，只修注解。B3r 55625 exit 0，2 passed（44.90s）；补完整末断言后B3r2 95980 exit 0，2 passed（44.71s），原5秒整体握手实际4至8秒内失败、重定向目标0请求，原claim/live/未来lease保全。相同两唯一案例不重复计数。

B4 86042 exit 0，7 passed（149.81s），189源/47测试首末一致：真实用户停用/改租户/会话改绑三种撤权，以及原同步授权方法在独立线程等待时PG浏览器租约继续，新增4项；实际旧Browser工具→原API/Hub/JPEG及原默认CLI完成/驻留park三项关联到末。模型/页面为loopback，capture/send/授权/握手时机或IO DI逐项登记，不声称实际数据库锁或客户站点验收。[v1/v2清单](/private/tmp/aid-agent-runner-m4-browser-observation-independent-inventory.json)共22个新唯一＋3个关联，6个v1有效案例按单sidecar差异复用，未称全量加载v2。最终189源/资源及58测试见证捕获，实际Chrome/driver/API与自有Worker/pytest/临时目录/Redis键/隔离库均0；未知旧库未删，唯一Redis helper继续按精确台账保留。

最终范围复核新增selected-tenant P2：NULL租户平台管理员拥有A/B真实会话时，HTTP选A仍能为B native run出票或获知disabled状态。主控仅放开browser_runs.py/browser_web_auth.py两源，v3在原fresh subject及完整实际binding后要求显式非空选定租户与actual tenant一致，先于disabled/Redis NX；无header原管理员正向及内部WS真实身份不变。主控实际核[189 v3源](/private/tmp/aid-agent-runner-m4-browser-observation-v3-combined-source-sha.json)零漂移，187项与v2相同；两源编译exit 0，独立delta CR新P2闭合且P0/P1/P2=0。受影响实际租户边界/原正向/HTTP顺序独验已分派，结果未返回前不验收观察片，不冒称先前22项加载v3。

**跨进程只读观察片正式验收通过（2026-10-02）**：v3定向10445实际exit 0，3 passed（79.92s），新增所选租户边界1及既有管理员/HTTP顺序2重验；无header/匹配header收到原JPEG，错header在enabled与disabled均403、Redis无票据新增，原waiting/claim/live/lease续租保全。主控已读[版本汇总与唯一清单](/private/tmp/aid-agent-runner-m4-browser-observation-independent-v3-inventory.json)、[独验记录](/private/tmp/aid-agent-runner-m4-browser-observation-independent-v3-evidence.txt)，并实际核最终189源/资源及59测试见证SHA全部匹配。共23个新唯一＋3个关联，重验不累加、历史v1/v2加载版本和全部失败未覆盖，187项未受v3两源影响按明确边界复用；独立CR P0/P1/P2=0。实际自有进程/隔离库/键/临时目录0，未知旧库排除，唯一Redis按精确台账保留。仅验收查看，不包含人工输入、完成桥、原Browser续接或完整M4。

下一段范围经只读讨论收窄并批准实施：先服务端原call续接（原PG completion事实、park后幂等control桥、亲和先于prepare/reject及consume事务重核、新Attempt重绑定原owner、原sampler/原orchestrator真实恢复），再单独native人工HTTP/WS动作与现卡片/页面接线。前段受信内部完成端口验证真实Browser/PG/新Attempt/用量/兄弟保全，不拿仓储fixture冒actual resume，也不声称用户操作已贯通；原Epoch、页面、IO锁及命令序号保持，无新Agent/恢复队列/DDL。观察验收清单封存，解除下一片相关生产源冻结，继续唯一实施者、独立测试、只读CR三角色；默认Web切换、提交和部署未授权。

服务端续接预审确认五项实现门槛：Browser与既有Local恢复证明组合，核心Engine不分工具业务；活owner的worker/boot亲和筛选在authorize/prepare/reject之前，有界候选页继续寻找其他可执行任务，claim消费事务再核原run/wait/epoch/独立lease；完成事实允许早于ToolSuspension真正park，只有park提交后才同cursor生成稳定幂等control并更新事实水位，回调丢失由本boot强保留manager的有限扫描补桥；新Attempt在原root锁内重绑同owner/manager/orchestrator，并安装当前ToolCtx/UsageScope，保原PageOps序号、页面及已完成兄弟；human快照仅复用已核原raw Browserlease能力，不放开任意executor或输入。原resume_from_human会追加确认步骤并行动，调用前须持久标记continuation-started；重复recover及结果未知不再调用，owner丢失也不清原control/claim/resources，公开核对态。内部完成口独立于legacy的Redis completed→旧job队列及其finalize/clear失败分支，仅共享纯采样与predicate逻辑。以上为下片验收条件，尚未声称实现。

续接首开发窗已启动31210，191源/资源＋61测试family见证03:52:26UTC预捕成功，运行中生产及已加载辅助源冻结，尚未有退出结果。12个Python源（2新私有completion/recovery＋10现有接线）逐项编译及现有容器相关模块import实际exit 0；源码组合为观察175实际路径重新核SHA＋2新模块＝177 code，加原14 profile资源＝191，主控实际核当前12/191全部匹配。准备7组独立风险矩阵及首真实resident原call fixture，准备AST不算运行通过。新树预flight类型错误安全化、单资源错误不退出Worker、相同verification投影幂等、终态/取消桥接竞态及已保存结果不重执行均待实际验证。主控要求本窗结束后将Browser＋Local恢复安装收口到RuntimeFactory应用装配，Worker只取得中性端口，保显式注入/customFactory；该有限装配delta另记版本，不把本窗结果冒后版认证。原12源范围无DDL、用户HTTP人控、卡片或默认切换。

首开发31210实际exit 1，1 failed（70.80s），191源/61辅助源首末零漂移。真实末诊断为原Runner waiting/attempt1、Browser live、2个observed收据，没有completion fact/control/trigger report，未进入完成方法；测试源码定位import worker后runpy以__main__重新定义RuntimeFactory，使原patch不作用于实际类。原加载probe字节、失败日志与首末捕获保留，仅修fixture直接调用原worker.main，所有续接末断言保留，不能把未触发路径称生产通过。窗口结束后仅worker文件收口默认恢复组合到Factory中性端口，编译及development-2新版清单后再跑同一实际happy；本时点续接仍未验证。

后开发82640实际exit 0，1 passed（29.05s），191源/61测试辅助源03:56:42→03:57:27UTC首末零漂移，主控实际读加载测试与捕获并核当前SHA全匹配。原owner真实完成采样后进入Attempt2，保同manager/orchestrator/PageOps/raw executor、业务seq增长且确认步骤只增1；原run/boot/epoch、起始页面仅1次GET、browser_complete consumed_attempt2、4个物理收据applied/72token、原费用0.01与1记录、claim释放、原Browser实际closed/自有PID0全部到末。单Worker装配delta将默认Browser＋Local组合移RuntimeFactory中性recovery_port，显式注入优先/customFactory无端口保既有Local；不声称前版本加载此收口。12源再次compile及相关import exit 0，正式191生产冻结，独立7组有限风险与相关回归、12源只读CR开始。仅开发正常链路通过，早完成/重复/第二wait/亲和竞争/已开始未知/结果缓存故障及正式首段验收仍待。

续接v1交接[范围与契约](/private/tmp/aid-agent-runner-m4-browser-continuation-v1-handoff.md)及12源字节已封存。正式B1 52313实际exit 0，1 passed（28.54s），191源/61测试首末零漂移，原正常续接全部末断言到末，不复述开发结果充独验。完整CR首末191一致，P0=0、P1=1组、P2=2，尚未通过：已有合法resume/reply竞争或单owner坏CP可从桥接hook退出整个Worker；wait有效性使用FOR UPDATE前求值的时钟；pause撤销未消费内部control后，observed fact永久挂旧ID不能续接。

旧v1真实过期锁反例52913实际exit 1，1 failed（4.77s），191源/62测试首末零漂移。原native PG声明/绑定仓储契约fixture，不冒真实Browser或采样；真实assistance锁等待在到期前已由pg_stat_activity观察，跨数据库时钟原expiry后释放，record_fact仍成功写completion_ref/observed且Browserlease仍future，期望拒绝未发生。主控实际读原断言、首末捕获及安全日志事实；未走到期望异常后的CP/control末断言，不称其通过。锁rollback/线程join、自有库/进程/键/目录清理实际0，Redis精确helper继续保留，原反例日志与测试版本保存。窗口全结束后批准有限v2：桥接正常竞争保fact返回、坏CP有限形状安全化，锁后及提交前fresh wait/Browser lease核；旧observed未消费fact因pause被拒时，只有后续合法显式resume实际消费且重新park、pause/cancel已解除才稳定重新关联，保旧控制历史，不借pause前resume或另一child reply自动继续。started/效果未知不重放，无新DDL/队列/框架；源冻结后定向CR与原反例、真实控制竞争/pause回归继续。control已消费但尚未start的暂停窗另列待核，不冒称本修覆盖完整Browser暂停。

续接v2已封存[定向交接](/private/tmp/aid-agent-runner-m4-browser-continuation-v2-handoff.md)：191项仅completion仓储1源改变，12源编译exit 0，尚无v2运行验证，不能把v1正常通过换称v2通过。追加只读CR确认第三个P2：原browser_complete已在Attempt2消费，但原动作的started尚未写入时发生合法暂停，后续Attempt3会被旧消费编号挡住；Engine恢复入口已清waiting，暂停保存后原completion/wait资源仍在，claim证明只取node.waiting也会在start之前挡住恢复。该窗口目前是源码确认，尚无实际复现结果。

独立测试明确当前实际pytest 0、无v2源码加载窗口，主控批准v3只修completion/recovery两源。保原browser_complete唯一消费历史；仅真实PG仍observed、原root/leaf/call/wait/ref全吻合且当前checkpoint.applied_control_id为同Runner当前Attempt实际consumed的显式resume，才许可首次started。claim定位仅凭合法observed completion及精确原browser_waits资源找回原assistance；现有wait冲突拒绝，无completion仍强要求原wait，started未知不重放，completed第二wait不得借旧assistance通行。无新control、二次消费、Engine业务分支或DDL。独立测试准备真实Engine已清waiting、原adapter start前暂停再resume3的窗口，与未消费control被pause撤销的场景分开；冻结后与原过期锁反例、控制竞争、坏CP及有界既定矩阵一并独验，避免明知缺门槛仍重复v2批次。完整Browser服务及M4仍未验收。

续接v3已正式冻结[交接](/private/tmp/aid-agent-runner-m4-browser-continuation-v3-handoff.md)，12源编译exit 0；主控实际读v1→v3完整diff并核当前191项零漂移，同路径仅completion/recovery两源改变、其余189不变，组合清单SHA为`7a59d298f6157ffa791252ca9eba4dd13b87f490891ade1ca6a7aa5472b95978`。已批准独立测试原过期锁反例及真实控制/暂停/第二wait风险组，与只读CR并行；尚未得到v3运行结果或CR通过。实施者仅准备下一人工动作接口方案，不提前改公共入口或解除生产冻结。

续接v3定向独立CR完成，P0/P1/P2=0，191源首末均与冻结组合清单一致。原v1 P1组、过期锁/未消费pause两个P2及追加已消费但start前pause P2在真实Engine→claim→start源码路径闭合；只复用其余189未变源既有审查，不冒已运行v3竞态。原等待行锁后及提交前fresh DBclock、当前合法resume与原唯一消费历史分离、清waiting后精确资源定位、started未知拒重派、第二wait不借旧assistance均已只读核验。独立运行证据仍待，不验收完整服务片或M4。

续接v3独验B2 67435实际exit 0，3 passed（15.36s），191源/64辅助源04:31:49→04:33:15UTC首末零漂移，主控已实际核清单、退出见证、日志通过行及原过期锁完整断言。原52913失败同强反例现拒绝`BROWSER_COMPLETION_WAIT_EXPIRED`，fact/ref仍NULL、checkpoint/revision原样、control0；坏CP类型守卫及claim事务锁等待跨Browser lease到期也拒绝并保原控制/claim/attempt。均为真实PG声明仓储契约，不冒真实Browser采样或恢复。后续真实resident控制竞争、两种pause窗口、第二wait与started未知组仍待，整片未验收。

续接v3独验B3 41920实际exit 1，3 passed/2 failed（153.08s），191源/64辅助源04:33:43→04:38:08UTC首末零漂移，主控实际核捕获/退出记录及加载测试。未消费桥被pause撤销后显式resume/repark、真实Engine清waiting后已消费但start前pause再resume3、started未知关原Worker后不重派三项全部末断言通过。pending竞争在pytest查询新PG连接时遇OperationalError，失败安全事实为Attempt3/running、resume2和browser_complete3已消费、fact仍observed、Browser live、原resume0、收据2；不能判生产已成功或自动归为暂态，原私日志清理后不重构历史原因。第二wait到Attempt3、两completed事实/两确认、5物理收据/90token、Browser closed/PID0，固定费用断言期望0.01实际0.02失败，真实定价核查及定向重验待，不删除收费断言或改生产压平费用。原失败加载字节封存，三个有效通过不机械重跑，整片未验收。

续接v3定向B3r 73358实际exit 0，2 passed（67.26s），191源/64辅助源04:42:11→04:45:16UTC首末零漂移，主控实际核清单/通过日志及加载helper。pending同源单次重验原竞争/延后桥接到末通过，原OperationalError未归因仍保失败；未加自动吞错重试。secondwait实际每收据冻结价格input1/output2、factor100、原model/main boundary符合fixture，独立Decimal计算每次`100*(11*1+7*2)/1e6`，5次0.0125按原向上取整到分应0.02；记录1/扣费一次、两确认、原页面/对象、Browser关闭、claim释放到末通过。仅测试费用期望按真实规则校正，生产191未变；新价格断言只这两条实际执行，不冒原B3三个旧加载用例也执行了新增断言。剩早完成重复、owner丢失/缓存故障、公平分页/真实child+sibling及关联组待。

续接v3独验B4 35212实际exit 0，3 passed（82.56s），191源/64辅助源04:46:20→04:49:52UTC首末零漂移，主控实际读退出见证/定向命令/通过日志及完整三条测试。早完成并重复实际采样两次在原park前保同fact、无control，park后唯一consume/确认到末；原owner真实shutdown后新CLI不消费原accepted control/不增Attempt/不重开Browser，保claim核对；原finish已提交后原Redis清理方法IO注入故障，原Workerinterrupted，显式resume的新CLI复用原knownresult，不重复原确认/Browser动作，实际结果/4物理收据/费用/关闭/claim末断言通过。已明确缓存故障为外部方法IO注入，不称真实Redis宕机。公平分页、真实child+sibling及坏CP实际Worker隔离/必要关联仍待，源码继续冻结。

续接v3独验B5 63535实际exit 0，2 passed（122.92s），191源/67辅助源04:50:26→04:54:50UTC首末零漂移，主控实际核退出见证/清单及完整两测试。16个foreign boot仅真实PG native仓储契约，不冒16个Chromium；后面的1个真实resident Browser由原Worker keyset跨poll越过页界处理，foreign原checkpoint/控制/claim不变、收据0。真实Browser child完成保同child/task/call绑定，已完成兄弟model/messages/tools/output/resources完全保留、父工具结果正确配对，7物理收据归root2/兄弟1/Browser child4，126token、单记录0.02、原页面仅1GET、原对象/确认、Browser关闭/PID0/claim释放到末。坏CP真实Worker隔离及Local/legacy Browser必要关联待，新增唯一暂14（含明确v1正常1及v3不同组13），失败与开发重复不累加。

下一人工动作片取消预审进一步沿完整调用链纠正主控初步判断：Worker虽调用Local命名的取消组合口，`local_recovery`实际已合入`browser_facts.cancellation_facts`，原Browser live不会因Local列表空被判ready；现缺口是仅观察PG关闭证明，没有主动关闭原Browser的动作能力。服务端取消须经应用层中性能力请求原Browser实际关闭，由既有证明核资源关闭与PGclosed后才释放claim；原boot丢失或关闭效果未知仍保claim核对，不能仅发cancel或写closed冒关闭成功。本时点只登记待实现边界，不扩当前v3冻结。

下一片只读方案已收窄：现take/complete/extend/cancel HTTP格式及同view WS输入格式保持，当前主体/所选租户/原PG绑定与每条action权限重新核验；复用原PageOps序号、raw executor及IO锁，不建第二Runtime/队列。延长采用原assistance单nullable `extended_at`与`expires_at`同事务水位，Redis后提交投影，不把期限镜像散放祖先/叶CP。人工权限拒绝独立普通错误，只拒绝输入/断观察，不Runner故障或强制回收；IO锁内写IPC前再核，已写frame后的页面断开由原owner有界收ACK，不取消原命令。真正Browser lease/协议/派发后超时保既有安全处理。自动完成只原owner `complete_owned`/双采样/PG事实桥，不走旧resume job；后续前端独立片按Runner快照更新现卡片，native旧continuation轮询/append不得与快照重复，取消接受不冒实际closed。本时点仅方案，未批准191源解冻实施。

人工动作权限方案另明确：take/input/extend/complete复用当前Runner执行许可及实际leaf许可，动作开始沿原恢复口径核余额，逐IPC写前至少复验当前执行许可、pause/cancel与原资源权限；不把观察权代替执行权、不另创main许可或逐IO余额政策。自动监测由原owner基于当前持久主体执行，不依赖旧页面票据/Bearer有效性，登出/关闭页面不取消Runner。原已派发ACK/result/费用/完成事实仍可保存；cancel按当前本人归属/read许可，不因订阅或余额阻止停任务。

坏CP真实隔离测试的只读契约进一步明确：owner桥接hook须有限typed失败隔离，不退出整个Worker；后续候选preflight发现`CHECKPOINT_TREE_INVALID`可按既有规则拒绝本次resume。此路径会改变control状态/清待恢复指针，不能称控制完全未变；须不消费、不增Attempt、不改原CP/wait/completionFact、不释放claim、不派发或收费，并继续处理其他健康任务。已确认具体Browser owner/resource丢失的preserve_control分支仍保原accepted控制核对，不能与损坏CP拒绝混同。本时点为只读复核，实际B6待。

续接v3 B6 64048实际exit 1，legacy关联1通过/坏CP隔离1失败（50.64s），191源/69辅助源首末零漂移。失败未观察到scope typed守卫，实际末Attempt2、Browser closed、typed计数0及AttributeError类；未到control末断言，不将其解释为accepted/rejected预期问题，也不从已清私日志猜历史AttributeError根因。源码核当前fixture的after_park等待期间调度器仍可能提前consume，测试时序没有限定在预期Attempt1窗口。保原69加载字节后，仅测试延迟原_acquire_next并加真实park/Attempt1/无lease/控制未consume前置断言，不造领取结果或Browser异常。

续接v3 B6r 60659实际exit 0，1 passed（29.01s），191源/69辅助源05:07:39→05:08:56UTC首末零漂移，主控实际核捕获/通过日志及前置和完整末断言。真实原scope typed错误被原owner扫描隔离，后续恢复合法reject `CHECKPOINT_TREE_INVALID`、consumed_attempt NULL，坏CP/原wait/fact不变、Attempt1/claim保留、无新Browser动作/费用；原Worker仍alive并完成另一合法会话。损坏CP未洗回健康以放行后续动作，生产191无改。Local原终态freshCLI兼容关联B7运行中，明确补捕实际device协议叶`src/local_tools/security.py`，源码见证192不冒原191已涵盖该叶。当前新唯一15及已过legacy关联1，最后Local关联/汇总/清理待。

**Browser服务端原call续接正式验收通过（2026-10-02）**：最后Local关联B7 36372实际exit 0，1 passed（31.98s），192源/69辅助源首末零漂移。主控已实际读[唯一清单与版本边界](/private/tmp/aid-agent-runner-m4-browser-continuation-final-inventory.json)、[完整独验](/private/tmp/aid-agent-runner-m4-browser-continuation-independent-evidence.txt)，并核10正式窗口各自源/测试首末一致、真实退出记录及最终192源/69测试当前SHA全匹配。冻结v3的191均包含且一致，额外Local实际security叶1独立注明。新增15唯一＝历史v1正常1（改动正常路径由v3真实链路直接覆盖，不冒B1加载v3）＋v3仓储3/真实resident8/公平1/child1/Worker隔离1；legacy及Local关联2，失败/开发/重验不累加。复用v3只读CR P0/P1/P2=0。所有历史失败、费用断言校正、时序/缓存IO注入和未知历史错误仍保留。

[实际清理](/private/tmp/aid-agent-runner-m4-browser-continuation-final-cleanup.json)自有隔离DB/UUID键/Runner与Browser/API/pytest/Chrome/driver/临时目录均0，未知旧DB0连接排除未删；精确Redis helper继续按测试台账保留，未新增helper或称所有资源归零。批准解除下一片有限生产源冻结，唯一实施者接native人工take/input/complete/extend/cancel及同owner自动完成监测，独立测试与CR继续。复用上述有限动作/权限/关闭证明方案，延长单PG水位，IPC写前拒绝与可能已写分开，断观察不取消命令；无第二Agent/Browser/队列、核心Engine工具业务分支或客户Runtime协议变更。前端现卡片/停止/核对状态独立后片，完整M4、M5订阅、渠道、默认切换仍待，不提交或部署。

native人控片实施范围已明确：纯PG action/once-extension仓储与同owner命令/权限应用编排分模块，工具只中性Protocol/错误/scope；API、sidecar及gateway薄接线，Factory安装中性资源取消能力，Worker不识别Browser业务。root追加实际序列门：原worker `last_seq+1`严格连续，原PageOps `_fields`先递增；正常拒绝若在DTO构造后不写IPC会烧号。优先原Local IO锁内先Browserlease与fresh human guard，再同步deferred原PageOps字段/命令构造、write_started、完整frame/ACK；输入及手工/自动sampler共用原序列/cache，无scope旧路径保持，不放宽wire或加counter。动作参数须预验证，builder不能await，确定未write的取消/到期不耗号不reap，部分write后不rollback。已派发task由原owner强持/shield收ACK，bootshutdown有界排水；强门验证拒绝一次后合法input、completion采样、原resume仍连续成功。本段为已授权实施/准备门槛，尚无新版源码冻结或行为通过结论。

人工动作片当前实际进度（2026-10-02）：实施者报告15个Python文件编译及隔离容器内有限Actions/Repository/Sidecar/原路由导入均exit 0；生产仍在开发态，未冻结、未启动真实动作验收，不将编译视为行为通过。已收口native action以PG期限授权、Redis同原assistance CAS投影不将queued/resumed倒退controlling、complete保原bac响应标识。独立测试准备7组矩阵及真实HTTP/WS人工正常链路，旧续接69辅助源和历史证据封存。

取消关闭新增有限门槛：Factory捕获本次immutable Attempt及原boot/fullbinding，通过中性scope在Local原IO锁内首次Close写入与原序号分配前复核；普通拒绝不强reap、不清handle/token、不释放claim、不永久锁住关闭状态，不退出整个Worker，保未知核对证明。boot清理仍按原owned handle证明。PG已commit延长而Redis投影失败时，重复native extend可重投同一已存期限，只延一次；已完成不得重新取得输入或延长权限。上述是实现及验收要求，尚无真实竞争通过结论。

开发态只读审查又确认两处需本片修复：旧API按run挑当前pending/controlling wait、sidecar及Actions先做live观察授权，导致原wait已resume_queued或进入第二wait后重复complete读不到原PG完成事实。幂等响应须以URL原assistance精确读取持久fullbinding/fact，fresh身份/所选租户/原owned会话核验独立于新动作许可，不重采样或操作第二wait，也不弱化view/input授权。native request_cancel提前写旧Redis取消flag后，原renew任务可能绕过IO锁内拒绝另行close；native取消须仅沿当前Runner取消授权和实际Close证明收口，legacy及真实lease丢失/boot清理保原证明。

Redis投影修复有实际边界：原raw assistance与session gate已完全TTL过期时，PG单期限仍不足以可信重建bac/predicates，当前片不猜造事实，只支持原raw/gate存续窗口修复。M7稳定原bac/挂起投影事实后补自然过期恢复验收；native人工采样不能以Redis旧期限否定PG合法延长，普通拒绝不能升级owner故障。投影需核实际读回，不能将底层吞掉写错误当成功。本时点仅开发态发现，尚未修复验收。

人工片有限修复接线报告：关闭旁路涉及原run_manager，实际范围增为16 Python＋双DDL＋表文档；native request_cancel不预写旧Redis取消flag，原Close scope取得真实证明后走原FINALIZING。重复complete加入精确原assistance完成事实的独立只读授权；native采样去除Redis旧期限裁权，投影同CAS并核读回。原raw/gate/bac遗失仍明确projection unavailable，待M7持久事实收口。当前等待新compile/import及源码开发窗冻结，未运行行为测试。独立测试正常链路已准备：真实WebAPI/WS接管、原IPC键鼠/页面事件、原采样、原call Attempt2、唯一费用与实际关闭；不替换Owner/DAL/结果。

人控v1开发窗已冻结并交sole测试角色：[自包含交接](/private/tmp/aid-agent-runner-m4-browser-human-v1-handoff.md)，19实际delta＝16 Python＋双DDL＋表文档；combined195＝原最终192＋新仓储/动作2模块＋表文档，含原14运行时资源。实施者16编译/有限容器导入实际exit 0，主控实际核195当前SHA零漂移，combined清单SHA为`ca4966ceedcb733ec84c102c8d60e85550e6977adecbfdec25817dde1a74b55a`。正常真实HTTP/WS人工链路单项开发测试已调度，源码及加载辅助冻结到实际exit/end；尚无行为结果，完整7组独验与正式CR待。

人控v1正常开发链路48591实际exit 0，1 passed（38.77s）；195源/72辅助06:01:50→06:03:24UTC首末及主控当前SHA均零漂移。主控实际读[退出证据](/private/tmp/aid-agent-runner-m4-browser-human-development-1-run-evidence.json)、[链路证据](/private/tmp/aid-agent-runner-m4-browser-human-development-1-evidence.txt)、[清理核验](/private/tmp/aid-agent-runner-m4-browser-human-development-1-cleanup.json)及pytest末通过行，并读原测试末断言：viewonly拒绝不消耗fields，同WS真实take后3合法键鼠触发虚拟本地页面实际输入/ready事件；原manual sampler与同orchestrator/PageOps/rawexecutor/原call续接Attempt2，confirmation/control各1，4实际applied receipt72 tokens/.01 record1/claim释放/原PG closed/PID0。虚拟模型和页面IO，不冒真实客户系统验收。自有隔离资源实际核0，未知旧DB排除，原精确Redis有意保留。仅开发协助正常1，不计已完成正式7组；批准复用此事实并开展余6风险组/必要关联及同195正式CR，前端/M5/M6仍待。

人控v1正式源码CR P0/P1/P2=0，主控实际读[报告](/private/tmp/aid-agent-runner-m4-browser-human-v1-architecture-cr-report.md)、meta及历史工具结果转录，并核当前195/归档字节；报告诚实区分先前首末工具核验与06:10:53UTC后补交文件，未伪称历史逐文件预捕。仅源码门通过，独验未完成。正式B1 41454实际exit 0，2 passed（7.23s），195/73首末及主控当前SHA零漂移；主控实际读新增迁移测试/退出证据，真实fresh nullable TIMESTAMPTZ、原legacy NULL行增量/跳过/强制重放全等、水位推进，以及错误TIMESTAMP真实updater回滚/不推进水位/不改行均到末。测试字节保存发生在launch后、观测exit poll前，匹配真实pre哈希，保存时点另录，不冒预捕。其余人控风险组与页面仍待。

下一M4页面片安全状态先决已由主控/实施者实际读源确认：当前`project_user_event`仅初始required写browserAssistance，take/extend/complete/hold/finish/cancel未持续投影；native Card不能只停旧pending或靠HTTP ACK猜终态。下片须以原PG wait/fact/run与Runner状态作有限安全展示投影：原查询一致读取派生，或原root锁同cursor materialize并推进view_revision，具体小方案先只读核定，不能散落写JSON或被parked DurableControl旧快照覆盖。M5事件/公开状态同cursor留接点，不现在扩events/schema。现卡片只传可信message.runnerId后读原Runner快照，native不旧bac轮询/append，legacy保持；当前195正式窗不改，待人控风险验收后实施。

下一片只读版本门补充：仅query derive而不推进公开水位时，两次相同view_revision的迟到get/list会把controlling退回pending，不能凭前端接受equal认为安全。实施者/独立审查有限比较：统一safe projector在原root锁同cursor物化并变化时view_revision++，或保一致derive但全部相关PG领域writer同cursor推进公开版本、共享有限projector；两者须覆盖take/extend/fact/start/finish/closed/hold/newwait/terminal、防旧snapshot/aid回退，M5挂同cursor事件。最终只选一种，不新增framework/DDL/核心Engine工具分支；当前仍只读方案，195正式测试冻结。

下一页面片最终选择A（方案通过，未编码）：统一纯安全card projector＋cursor helper，复用public_snapshot.browserAssistance与view_revision，不新增DDL/events/执行状态。原root锁同cursor按将保存的实际CP/当前PG完整原wait/fact/run证明投影；take/extend/fact/start/finish/hold/newwait/closed/terminal，以及generic save/park/stage/ack/finalizer共用有限口，旧aid不盖合法第二wait，display确变化才推进独立view水位，generic已有view++保留，不改执行revision。record_runtime_state先root再run/assist，禁止末helper补root形成逆锁。Engine仍不识别Browser业务，公开只原allowlist；M5后续同cursor挂事件。当前195正式风险窗不解冻，不再并行实现两套方案。

人控B2 34408实际exit 1，1 failed（38.50s），195/75首末零漂移。主控实际读退出证据及旧loaded测试/原probe：resume_calls在内存入口增加，但原报告仅finally发布；真实provider决定已held，重复complete后断言读取旧报告resume_calls=0，末释放后观察到原resume1/success/confirmation1。终态末断言未到，不冒通过或已确认生产缺陷。保原loaded75/日志，只tests修入口发布并等实际入口；B2r 81942同node已启动，新195/75真实pre首字节冻结。正式其它风险组继续准备，当前CR与生产195不变。

人控B2r 81942实际exit 0，1 passed（42.93s），195/75首末及主控当前SHA零漂移，主控实际核退出证据及pytest末行。旧aid在原control已消费、真实provider决定held的窗口重复complete返回原bac，采样/complete/resume各原次数、原control1、confirmation1、4实际receipt72tokens/.01 record1/claim释放/PID回收末断言到末；首B2失败照存，不补写成当时已观察入口。角色NULL→非admin的400仅身份/middleware证据，不冒执行许可。正式B3单真实组合已启动：tenant执行状态撤销/余额0时原ticket/query/WS仍读，新动作0IO拒绝，最后本人read取消必须沿真实关闭证明收尾；195/76加载冻结。独验仍部分进行。

人控B3首40315 exit 1（29.30s），195/76零漂移；仅测试误把原Browser HTTPException的detail.error_code当顶层KeyError，保旧bytes，只修错误读取。B3r 6000真实exit 1（59.04s），观察ticket/Runner GET/JPEG仍读、take/complete/input真实TENANT_UNAVAILABLE零fields/sample/resume及原claim/live均到，但read cancel接受后30s仍finalizing Attempt2/revision18，PG Browser仍live/未closed，实际收尾未通过。不得继续归因于错误形状、租户停用或延长等待冒通过。

主控暂停其它新批与下一片编码，唯一因果B3d 24888真实exit 1（59.77s），195/77首末零漂移；实际读[安全阶段](/private/tmp/aid-agent-runner-m4-browser-human-independent-B3d-safe-cancel-phases.json)/退出记录：原Factory进入、PGguard3次成功、真实Close ACK closed/OK/write_started/frame_written全真、原reap进入返回process_live=false，但Local.close未返回/抛出，Manager.finalize/Owner.close未进入。观察器仅原方法透传、threading.Lock只自身报告IO，不改结果。无历史私日志或等待栈证明时不猜根因；CR确认取消链无execute/credit，finalizing也可能来自错误保留，单末行不足证hang。当前原Task get_stack/cr_await函数/行号＋done/cancelling有限诊断B3s 35863已启动，195/77冻结，无locals/参数/正文；重点证伪或证实画面task取消被stderr cleanup吞掉、死进程画面循环与close gather的等待关系。原源码CR0只代表之前源审门，不代取消运行验收。

人控B3s 35863实际exit 1（62.28s），195/77首末零漂移；主控实际读[原Task等待链](/private/tmp/aid-agent-runner-m4-browser-human-independent-B3s-safe-cancel-phases.json)/退出记录，10s时关闭调用在Local.close:332等待frame_task gather，原frameTask未完成/cancelling_count=1、仍在_frame_loop:358→sleep；process已死，stderrTask已done、ACK闭合true，finishstderr/reap已返回。源码对应_finish_stderr吞当前取消，frameLoop仅引用非NULL而不看process退出，形成实际收尾循环等待。此为实证运行缺口，不将历史源码CR0当解除。

已准唯一实施者只释放Local两个叶做v2：死进程画面循环退出、stderr清理正确传播调用方取消并区分子stderr自身已取消；其他194冻结，保v1原字节。不得提前closed、跳ACK/reap/PG关闭/claim门或扩kill范围。新v2编译/manifest/归档与CR后，同原权限撤销＋read cancel实际节点优先复验，原30s不放宽，再恢复其它风险批。自有隔离资源06:55:30实际核0，未知旧库排除、精确Redis仍有意保留；新IO/自动监测/Close拒绝测试只准备未运行，不冒通过。

人控v2仅Local两个叶，195项manifest SHA `f624c39a07ffb9bd9c4a3a542ca1b083d0ee7d394af84e91fff7695c2b505e91`；主控实际读交接/精确v1→v2 diff并核当前195零漂移，其余194字节不变。单源编译/import/diffcheck实际0；独立定向CR P0/P1/P2=0，两次195/current与loaded校验07:00:17及07:00:32 UTC均0，报告登记时间与历史工具校验时间分开。关闭证明与PGclosed/claim门未弱化。

v2正式B1 71549实际exit 0，1 passed（31.71s），195/77首末0、pre实际测试bytes封存；主控实际读run-evidence及当前测试末断言。原tenant停用/余额0时读仍可用、take/input/首次complete零IO拒绝，本人read cancel沿原关闭链完成；原30s不放宽，真实Browser PGclosed/lease清除/PID0/claim释放、2receipt36tokens/.01 record1且无新模型全部到末。v1 B3r/d/s失败原证据保留，不据此宣称整个接管片验收；v2 B2已启动接管延期＋IO排队/断开风险，195/81冻结，自动监测、部分写入未知及关闭拒绝等剩余边界待验。

v2 B2 69033实际exit 1，1 passed/1 failed（82.16s），195/81首末0。主控实际读退出证据与IO测试定义：无效DTO/原IOlock排队中执行权限撤销零fields/序号拒绝，恢复后真实pointer/keyboard；原完整帧写入后hold ACK、断开WS，原强任务接收ACK，同PageOps采样/原续接及计费/claim/PID末完整通过。该完整帧hold不冒部分写入证明。延期测试原PG只延期一次、真实Redis CAS投影失败及相同expiry重试已到；测试先等后续模型再释放投影gate导致自身超时，最终投影未覆盖，未证生产缺陷。保失败bytes，独验只修gate观察释放顺序单跑B2r，不延长gate或改源。自动任务当前权限的负向、真实2字节部分帧未知及关闭拒绝跨原续租仍待实跑。

延期B2r 79018实际exit 1，1 failed（37.33s），195/81首末0，gate仍自身超时，末状态未验。主控读原complete_owned/owner.record_completion与ResumeStore确认native仅提交PG完成事实/bridge，不像legacy切Redis resume_queued；因此等待该Redis状态并非有效native观察点，不将测试超时归生产挂起。要求测试改等真实PG/原resume入口后释放原9s gate，再核单完成事实/控制消费、原expiry与计费资源末；若单验Redis已queued投影不回退则须明示实际Redis状态契约DI，不能冒native自然状态推进。A页面继续从PG导出唯一公开快照，不为通过测试新增缓存状态机。

延期B2rr 36571实际exit 1（34.85s），195/81首末0，原9s gate释放、延迟HTTP200/同expiry/完成事实保留已到；测试把consumed Attempt2错误等同已started，而实际合法phase仍observed，终态未到。仅测试改phase单调及原ref后B2rrr 5752实际exit 0，1 passed（39.33s），195/81首末0；主控实际读退出证据、末行与测试断言：真实PG一次延期/Redis CAS失败后的同expiry重试、原完成ref/phase不回退，真正completed/4receipts72tokens/.01 record/claim0/PGclosed/PID0到末。正常推进不被误判回退，旧失败保持原scope。

v2 B3 69608实际exit 1，3 passed/2 failed（102.58s），195/89首末0。旧Attempt在原IOlock内拒绝关闭、零seq/原handle-token保留、10.5s真实续租推进/无旧Redis旁路取消/另一SID健康到末通过；原stderr子task单独取消与调用者取消两契约通过。自动已核真实PG停用时多次guard拒绝且无新增采样/完成/control，但测试错误读取尚未发布的base报告，token失效及终态未到；仅修独立观察器，单auto B3r已启动。

部分帧真实向原stdin写2bytes+drain后故障，write_started=true/frame_written=false，原cancel持久blocked/waiting、PGlive/未closed、ownerPID0已到，但bootcleanup汇总BROWSER_CLOSE_VERIFICATION_REQUIRED使CLI exit1，测试原clean_exit断言失败，FIFO/费用末未到。主控、实施者及独立CR实际读关闭链：Manager逐key、worker逐同bootManager捕获继续最后汇总抛错；Factory finally仍关sidecar，run_worker finally仍关logs/PG pool。不存在此case跳过其余关闭的源码证据，也不把nonzero当执行期崩溃。保持原未知关闭故障汇总契约与195源；批准测试明确核exact typed退出1及实际listener/pool清理，并验原PG/claim/FIFO/费用/无重放末，不能仅放宽退出码冒通过，尚待实跑。

自动B3r 63032实际exit 0，1 passed（45.71s），195/89首末0；主控实际读退出证据/观察器：当前PG执行权限撤销时多次原guard拒绝，零新增fields/sample/fact/control，恢复后真实DOM操作；页面token失效401，原持久任务仍按双采样继续，同原call/PageOps、automatic confirmation0与真正终态/4receipts72tokens/.01/claim0/PGclosed/PID0全到末。

部分帧B4 60257实际pytest exit 0，1 passed（34.65s），195/90首末0；原被测Worker仍明确expected exit1，新增透传观察器实际核exact BrowserOwnerFailure/BROWSER_CLOSE_VERIFICATION_REQUIRED、sidecar socket/task关闭及logs/PG pool原关闭返回。PG保持live/未closed、原blocked/claim保留，PIDs0，原会话下一任务被默认新CLI阻塞且无模型IO/重放，2observed receipt36tokens/无chatrecord/余额不变全部到末。生产195未改变；首B3未知收尾失败范围照存。

正式新风险11 unique均到末（v1 DDL2/权限重复完成1；v2读取消/IO/延期/自动/Close拒绝/部分帧各1＋stderr2），开发辅助原正常1另记角色，不将重试重复计数。关联B5 68599实际collect exit2/0.41s，198/91首末0：旧phase3顶部import已移除的Agent私helper，尚未执行业务；关联额外Agent/User/SaaScontext三源码仅新窗捕获，不回填此前195。批准仅测试将旧源码/私helper断言迁为新Engine/LegacyRuntime实际暂停/已执行siblings不重复及pending延后行为，公共continue_tool_call兼容节点保留，旧有效HumanControl/ResumeStore/API关联待验，不为collect加生产假helper或skip。

现并行放行A片前端8源及尚未import的新增projector准备，均不属于活关联Python198；既有后端/导入口/配置/DDL仍冻结，关联实际退出后才释放后端写口。人控完整兼容验收与A片独立测试/CR均未提前完成，前端继续原界面及用法，无默认开关/部署/提交。

人控最终关联B5r 59388实际exit 0，41 passed/0 skipped（4.31s），198/92首末0；主控实际读run-evidence并现场核198源码及92测试当前字节均零漂移，无前端路径。close6、executor3及legacy人控32（含原公共continue_tool_call）通过；两旧Agent源码/私helper测试仅迁为现真实Engine暂停/siblings/等待恢复行为，没有伪造生产helper或skip。正式新11＋关联41及v1全源/v2唯一Local独立CR0构成人控服务端验收，原开发辅助正常1不混正式计数，v1运行失败和所有测试观察点失败保持原范围。源码198为195人控＋关联新捕三既有依赖，不冒此前窗已含三叶；最后清理汇总待独验整理，精确Redis继续留给后续片。

主控已读[人控最终清单](/private/tmp/aid-agent-runner-m4-browser-human-final-inventory.json)/证据与清理汇总：实际07:45:49 UTC封198/92，07:45:55自有DB/API/Runner/Browser/Chrome/driver/pytest/namespace/tmp均0，未知旧DB排除0连接，精确既有Redis意留；源CR、各历史版本和18运行窗的通过/失败范围及role独立登记。global diffcheck实际2仅本任务models.py EOF空行，交唯一实施者在A片删除该空行并登记纯空白delta，不据此重跑已验业务。

主控已释放A片后端写口，实施者完成已开始的前端8与有限后端projector/写口；旧198验收只对应封存历史，不用新合法A片修改误称漂移。A片新快照/版本/终态metadata/第二wait及页面交互需要独立新冻结测试和CR，无默认切换或部署。

A片实际职责门补充：通用Runner仓储/执行/恢复/控制/Finalizer写口仅调用同cursor中性公开状态投影口，不能各自import Browser projector、查询Browser表或按Browser phase分支。有限application_public_projection组合已安装应用能力，唯一Browser projector负责完整原绑定/PG事实/白名单；新增工具只扩应用投影组合，不逐个修改核心写口，无注册表或新框架。实际边界预计20源（原18＋中性组合1＋models纯EOF空白1），以正式冻结清单为准，尚未测试/CR。

A片v1正式冻结（2026-10-02）：[交接](/private/tmp/aid-agent-runner-m4-card-v1-handoff.md)及20修改源、268有限源码/依赖/资源见证已核；组合清单SHA为`53706509a542d0b29565f8aa7eb40186da88b4db10a96be414d2feeae57ddb5d`，主控现场核当前字节和封存loaded-source均零漂移。20源为12 Python（含中性组合及Browser projector两个新增模块、models EOF纯空白）和原前端8源；268不是一次测试实际导入全部项的声明，也不是全仓库递归冻结。人控198基线之外的前端/新增模块不虚构历史pre。12编译/最小容器导入及global diffcheck实际exit0；最终前端build会话96917实际exit0，日志[build4](/private/tmp/aid-agent-runner-m4-card-development-build-4.log)。早期build1/2为测试fixture类型问题，唯一测试者修复；build3早于末次mapper/reactivity修改，不作最终依据。没有业务/交互通过声明。

主控已调度同268的独立源码CR及唯一测试者真实resident Browser卡开发协助用例，先核domain take/extend/complete、原Worker终态、Web get/list与PG历史同一权威投影；实际运行结果待登记。随后独立前端11节点、后端3存储形态及原页面交互验证，不重复已验人控11＋41。继续复用唯一精确Redis helper；无新容器/重启、DDL、事件框架、默认开关、提交或部署。完整M4待此片和其余门槛闭合。

A片开发综合用例实际会话10769已启动，实际pre在08:28:26 UTC捕268源/资源及26有限测试/辅助见证，[pre元数据](/private/tmp/aid-agent-runner-m4-card-v1-development-1-meta-start.json)。该角色仍是唯一开发协助，正式独立节点另计；结果与清理待实际退出登记，不将启动称为通过。

开发10769实际exit1，1 failed（22.54s），268/26首末零漂移，[退出证据](/private/tmp/aid-agent-runner-m4-card-v1-development-1-run-evidence.json)。首次Web query/list与PG snapshot/view三项一致已到；随后fixture误将既有公开revision列为禁项失败，接管/延长/完成/终态未到。主控实际核public_runner既有allowlist，批准唯一测试者仅修期待；未到的result.assistant_metadata期待同样不存在于既有四字段public_result，不扩生产契约来迎合测试。末验保真实history metadata与terminal snapshot一致、private pending_finalization意图原样。该次[清理](/private/tmp/aid-agent-runner-m4-card-v1-development-1-cleanup.json)自有进程/DB/namespace/tmp均0，未知旧DB仍排除，唯一Redis按账保留；修正后重抓tests/pre再验。

开发2会话92433已实际启动，同268源、27测试/辅助pre，prefix `/private/tmp/aid-agent-runner-m4-card-v1-development-2`；新增有限hash-only原Finalizer前后观察器核pending_finalization意图不变，明确观察DI，不替事务/结果或输出私有正文。修正只在测试，业务运行/终态与清理待实际退出登记。

开发2会话92433实际exit0，1 passed（45.14s），[退出证据](/private/tmp/aid-agent-runner-m4-card-v1-development-2-run-evidence.json)及[清理](/private/tmp/aid-agent-runner-m4-card-v1-development-2-cleanup.json)已实际核；主控现场268源/27辅助首末及当前字节均零漂移。真实原resident Worker/Browser/PG/Redis下take、单次PG延长及重复原expiry、completion原Attempt2继续、finish/PG closed，Web get/list/PG snapshot及终态history metadata一致到末；原Finalizer hash-only透传观察DI证明pending intent未变，4原applied receipt72 tokens/.01 record1/claim0/owned PID0通过。虚构loopback模型/页面，非客户外部系统或UI端到端验收。仅developer正常1，原10769失败范围不改；批准正式前端11＋PG存储3与原页面交互、同268独立CR，完整A片/M4待验。该次自有DB/进程/namespace/tmp0，未知旧DB排除，唯一Redis有意复用。

正式B1存储3实际会话22126（268源/30辅助pre）及B2前端4文件11节点实际会话7857（268源/15辅助pre）已启动，prefix分别`/private/tmp/aid-agent-runner-m4-card-v1-independent-B1`与`-B2`；源码与已加载辅助冻结到各自实际退出，结果待登记。两窗资源独立，不重跑旧人控或默认启用。

B1 22126实际exit0，3 passed（11.61s），268/30首末0：实际PG声明native root/call绑定核领域显示同事务回滚、合法第二wait旧save/park不覆盖且sibling保全、坏证明安全核对状态；不冒真实Browser激活。B2 7857实际exit1，10 passed/1 failed（1.11s），268/15首末0：原Vue/API/composable/mapper受控网络；aid/auth flushsync产生多票据，fixture错把中间票据当最新并取未创建socket失败，后续旧binary callback断言未到。批准唯一测试者修fixture选最新并核中间票据不建WS，原10已到末事实保留，不称11全过；无生产生命周期修复或UI/Worker端到端声明。

独立源码20与268 current/loaded首末零漂移，初报告后[补审](/private/tmp/aid-agent-runner-m4-card-v1-architecture-cr-addendum.md)确认P0/P1=0、P2=1：通用Finalizer仍直接pop/替换browserAssistance专用元数据，未来工具展示规则会继续修改核心收尾口，未完全兑现中性组合边界。初CR0被补审结论替代，原报告/核验时点保留；08:27首工具结果事后转录但未预存全maps，元数据诚实注明，不伪造pre。B1/B2退出、无活pytest后，主控仅释放Browser projector/application_public_projection/Finalizer三源，唯一实施者将原纯deepcopy领域元数据规则移回Browser，经统一中性派生终态入口返回snapshot/result；保意图/DTO/锁/费用/历史同事务，余265不改，无注册表/框架。新v2须封同268、三源实际delta、定向CR及真实终态链复验；不重复不变存储/前端矩阵，页面验收仍待。

A片v2 08:43:54 UTC实际封同268源/资源，SHA`bfefcff662cfd595fdfd5f1ca5ca9ef0140ade1871c457839884ebe5d71a22e2`；主控实际读[三源delta](/private/tmp/aid-agent-runner-m4-card-v2-delta.diff)并现场核current/loaded均0、对v1恰上述三处，其余265不变。编译3/原容器最小导入3/global diffcheck实际exit0；Finalizer已只调用中性project_terminal_public_state，领域project_terminal_result保原pure deepcopy与专用元数据规则。v1前端源码/依赖没变可复用build4，但新测试fixture最终类型检查另核。已调独立三源CR及真实终态链定向回归，未称P2已最终验收；无业务矩阵重跑、DDL/事件/默认切换/提交或部署。

v2定向独立CR已关闭该P2，剩余P0/P1/P2=0；[审查报告](/private/tmp/aid-agent-runner-m4-card-v2-architecture-cr-report.md)实际08:45:10→08:46:06 UTC首末268 current/loaded maps零漂移，其他265同v1。源gate通过，运行/页面待；原v1补审保历史。独立v2 B1终态链28681（268/27pre）和B2失败前端单项54040（268/15pre）已实际启动，结果待。下一M5首片仅只读审查准备，不提前修改当前加载源码。

v2 B1 28681实际exit0，1 passed（44.96s），268/27首末0，改后真实终态历史/snapshot及原pendingintent透传观察一致到末；是正常开发形态的定向关联回归，不重复计new unique。B2 54040实际exit0，失败票据单项1 passed，268/15首末0；同文件另2只被`-t`未选，不冒业务skip。fixture现释放全部中间票据确认0 socket，再最新票据核原旧binary callback/对象URL释放/取消0。正式合计前端11 unique（v1有效10＋v2补1）、存储3 unique（v1）及正常开发1形态/v2末回归，重试不叠加。两个实际退出证据分别[v2 B1](/private/tmp/aid-agent-runner-m4-card-v2-independent-B1-run-evidence.json)/[v2 B2](/private/tmp/aid-agent-runner-m4-card-v2-independent-B2-run-evidence.json)，自有资源只读核0，唯一Redis按账保留。原页面/builtassets真实入口、键盘/窄屏及末版build继续，A片整体仍待。

末版build84040实际exit0（6.35s/522 bundled modules），268源＋69测试类型见证首末0，新增票据fixture类型有效，[构建退出证据](/private/tmp/aid-agent-runner-m4-card-v2-build-run-evidence.json)。真实built UI尝试63059 actualexit1（24.56s），425源/依赖/资产见证＝268＋23原bootstrap静态依赖＋134实际built assets，测试29，pre/end0；[退出证据](/private/tmp/aid-agent-runner-m4-card-v2-real-ui-run-evidence.json)。未拦HTTP/WS或stub身份，原真实PG用户/token/订阅经原tenant_auth/permissions/billing/content_sync与WebRunner/browser路由，没启动src.main/scheduler。实际页面Enter→POST202接受一条queued/Attempt0已到，fixture按预建SID找Runner失败，Worker未启动，后链未到。仅测试改为取实际accepted Runner SID并核PG owned session，原身份/生产不改，重抓测试pre再验；该次资源cleanup0、唯一Redis保持，未知旧DB排除。

真实UI第二窗60500 actualexit1（65.94s），425/29首末0及自有清理0，[证据](/private/tmp/aid-agent-runner-m4-card-v2-real-ui-r-run-evidence.json)。正确owned SID后Enter202→原Worker park→原WS/JPEG→take→真实页面F输入/ready事件已到，窄屏无横向溢出；缩至窄屏时原开启sidebar及backdrop挡住完成按钮，测试未走现有收起操作即点击失败，complete请求/终态未到。主控实际看[截图](/private/tmp/aid-agent-runner-m4-card-v2-real-ui-r-sidebar.png)确认正常侧栏遮挡，批准只fixture用原收起按钮，不force或改布局。第三原node29651已pre425/29启动，prefix `/private/tmp/aid-agent-runner-m4-card-v2-real-ui-final`，补同confirm_only原wait真实PG余额0否决complete且无fact/保claim、恢复原1000后原完整末验；source/资产不改。结果/键盘拒绝反馈/窄屏完成/终态待实际退出。

第三UI 29651实际exit1（39.64s），425/29首末0、自有清理0；正常窄屏收起sidebar到末，余额0 complete实际HTTP402，测试误期待403失败。原RunnerAuthorizer CREDIT_BLOCKED明确402，主控读源码及[退出证据](/private/tmp/aid-agent-runner-m4-card-v2-real-ui-final-run-evidence.json)，只批准fixture精确改402，不放宽任意4xx，不改生产。可见反馈/无fact/保claim/恢复本金后终态此窗尚未到，不因观察了402提前算通过；原窗字节/日志保留，新pre后末复验。

UI第四窗33901实际exit1（50.33s），425/29首末0、自有清理0；真实402可见安全反馈/无completion/claim保、恢复本金manual complete后原Worker completed/Attempt2、原tool fact/PGclosed/control一次/claim0/PID0/page1至末。新value fixture漏原helper必需marker，provider/receipt/fee及最终UI断言未到；仅补原marker，不改生产或费用期待，原失败范围保留。

**A片及M4阶段验收通过（2026-10-02）**：第五同UI node29136 actualexit0，1 passed（53.45s），[完整退出证据](/private/tmp/aid-agent-runner-m4-card-v2-real-ui-r3-run-evidence.json)425/29首末0。原真实PG主体/token/订阅许可、built UI键盘Enter→WebRunner202→原Worker→parked Browser→WS/JPEG→take及真实键鼠F/ready→余额0 complete402可见拒绝/no fact/claim保→恢复本金manual complete→同call/Attempt2→原tool结果/PGclosed/claim0/ownedPID0，全链到末。4原observed/applied receipt72 tokens/.01 record1；最终UI输出一次/无帧/textarea可用/0 BAC请求。主控实际看[窄屏拒绝](/private/tmp/aid-agent-runner-m4-card-real-ui-rejected.png)/[终态](/private/tmp/aid-agent-runner-m4-card-real-ui-terminal.png)及现场核425/29首末/current0；模型/业务网页仅虚构localhost，没拦HTTP/WS或stub身份，没启动main/scheduler，不冒客户外部操作验收。

主控已读[最终清单](/private/tmp/aid-agent-runner-m4-card-final-inventory.json)/[证据](/private/tmp/aid-agent-runner-m4-card-final-evidence.txt)：11实际测试运行窗＝2开发＋v1存储/前端2＋v2终态/单项2＋UI5；build84040另列。正式15 unique（前端11/真实PG3/真实built UI1）＋开发正常1独立角色；v2同node定向回归不重复加数，所有失败窗保原时点/字节/未到范围。v1全20源审查＋v2三源收口CR剩余0，末build实际0。最终09:22:51 UTC封425源/资产与102实际测试/类型见证有限union，主控当前核全部0；268生产子集单列，其余23bootstrap依赖/134资产不是全仓库冻结或一次全导入声明。[清理](/private/tmp/aid-agent-runner-m4-card-final-cleanup.json)自有DB/namespace/API/Runner/Browser/Chrome/driver/临时目录均0，未知旧DB0连接仍排除，exact Redis无持久mount/发布端口并按账有意保留后续使用。M4完成指本地代码/接口及相关风险验收，默认旧缓存协议迁移、事件/三渠道、容量/真实外部/部署继续M5–M7，不提前声称生产切换。

主控正式释放M5①既有写口：原公开DTO/纯row codec单向迁移、事件账/全部公开commit尾点、短一致fresh授权读取、bounded retention/双DDL；SSE/Web stream/frontend/channels/默认开关仍不在首片，不提交或部署。合法新源码变更不以旧M4 sealed证据判断漂移；历史清单/已捕maps保留。

### M4 最终 Web 核对状态门槛（2026-10-02）

只读复核确认两项待修 P2，安排完整 Local/Browser 接线后的独立页面小片，不扰动当前领域源码冻结。`useAgent.isProcessing` 不包含 waiting/paused/interrupted，而 ChatInput 仅处理中显示原 Stop；verification waiting 的 Send 又被拒绝，普通用户因此不能到达已有 nonterminal cancel 接口。复用现有停止操作，用独立停止可用性区分不可回复的等待与澄清回复，不能把所有 waiting 都设为 processing 禁掉既有 Send。中文核对原因目前只进入 debug 执行详情，普通正文为空或保留旧输出；将公开 `waiting.question` 以稳定 Runner/wait 标识映到现有可见消息说明，保留原输出与澄清，不展示私有 checkpoint。

验收覆盖刷新找回后的核对说明及原停止操作、澄清仍可发送、切换会话不串状态、重复轮询不重复说明。停止接受后若原动作仍未知，页面仍须明确等待核对，不能虚报已取消或已释放会话。Browser第二wait沿用同run但换assistance/wait，native观察凭据绑定原wait须重新建立现组件连接，不能只更新controlling状态而让旧已撤连接永久停在“正在连接”；沿现组件key/生命周期处理，无新用户操作。票据请求尚未返回时关闭页面/切会话，返回后不得创建已销毁组件的孤立WS，需验证观察资源清理且Runner不中断。当前为源码确认和后续门槛，尚未修改或通过页面验收。

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

SSE 使用查询同一 fresh 主体/当前渠道 actor 的授权链，并周期复验。每连接只持有有界分页及游标，不在网络发送时占 worker/control 锁或数据库连接；断开不取消、慢连接不拖执行。过期游标明确返回一致已提交 snapshot/revision/last_seq 的重置事实，再从 last_seq+1 补读，客户端按 seq 去重，查询保持权威。四入口共用公开流，渠道发送 offset、投递幂等与发送后 claim 释放仍归渠道 adapter。当前为提前审查建议，未实施或验收，不阻塞 M4。

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

### M6a 最终收尾契约（2026-10-03）

用户本次授权仅完成 M6a，完成后停止并交接，不进入 M6b、M6c 或 M7；不提交、推送或部署。以下契约替代本节历史记录中的冲突验收要求；历史通过、失败与未运行证据保留，不能作为扩大需求的依据。

| 验收范围 | 固定预期 |
|----------|----------|
| 普通收发 | 原可信回调及拉取、注册/归因/欢迎、连续聊天、正文/附件回发、原历史和发送后 recap/收费；最终正常流程经过实际 Admission 消费器 |
| 消息规则 | 保留原两秒合并、段级去重/撤回、客户与员工历史顺序、隐藏命令、旧消息和服务状态过滤；不得用换会话规避 |
| 追问 | 普通 master/standalone clarify 交回模型组织回复，不一律进入 WAITING；真实子任务的问题正确展示，聊天答案绑定原唯一等待继续，不要求问题已获平台 ACK |
| 语音与媒体 | 识别成功继续聊天；原识别失败/请求异常保占位继续，不因识别结果不确定无限阻塞普通聊天；不重复识别/收费。保原媒体支持及大小边界 |
| 客服账号限制 | 原客服账号 expire_at/credit_limit 检查与固定微信提示/历史等价迁移；命中不执行模型、不收本轮 AI 费用，租户授权拒绝不能替代此功能 |
| 转人工 | 成功后不追加机器人答复，后续客户消息只按人工期原规则处理；明确拒绝后继续原 AI 流程；复用未受影响独验 |
| 服务责任 | 持久接受后独立运行/可查询，单会话执行归属、当前可信身份与租户隔离、费用唯一归属、已发生外部操作不盲重放；发送 unknown 保事实，但执行终态且真实网络收尾后释放会话，后续聊天不永久锁住 |
| 交付 | 最终源码独立 CR、受影响定向回归及隔离启动/import 检查通过；列明有效结果、未验证外部环境、开关与启动方式，清理自有测试资源后交接停止 |

撤销以下 M6a 门槛：微信暂停/继续按钮或手工控制流程；强制余额归零叠加人工恢复/数据库故障；多层人工构造子任务恢复点；微信 Browser 人工卡片/门户闭环；普通追问必须匹配同 Runner 的精确等待编号；固定 Attempt/内部 phase/只读 SDK GET 次数；将手工重投可信 ASR/发送结果声称为生产自动找回。未知外部操作不冒成功、不盲重发仍须保留，但不据此要求普通语音聊天永久等待。

测试作者先给出原实现或明确需求依据，审查确认预期后固定断言；失败先定位生产、环境或测试前提，不能直接修改断言来过关。生产代码由实施者单一写入，测试与 CR 独立；复用与当前变更无关的有效证据，不机械重复全部历史窗口。

此前已核实两处语义差异（普通 clarify 强制等待、ASR unknown 阻塞本轮）及一处功能遗漏（客服账号限制固定提示）；当前没有丢失 ASR 结果的自动回调/查询恢复，手工可信结果重投只能证明结果保存能力。本次只恢复原使用行为，不开发新结果核对系统。账号阻断语音保原识别文本历史、固定提示且不建 AI record/收费，正常语音不迁移既有识别/费用 owner。旧路由残留 should_transfer_to_human 调用的底层方法已废弃且恒为 False，不复活自动关键词转人工；实际转人工仍是原 Agent 工具。共享暂停防御仅核对实际依赖，不作为微信验收门或继续扩展；不整文件回滚原通用控制。

### M6a 接手收尾记录（2026-10-04）

接手者核验交接文档证据全部属实（run-evidence/command/log/清理记录，运行期间源码未变）。两条失败用探针 dump 真实 wire/落库行定案，均为**测试自身错误，生产代码零改动**：

1. **test_kf_completion_commands（隐藏命令/过滤）**，两层错误：
   - 断言 `wecom_kf_context_task_intents` 全空与原行为矛盾——原代码 `channel_routes.py:2047`「人工期/已结束期需落库并推送第三方（external_push_human）」+ `services/recap/runner.py` 的 `lead_refresh`；新代码 `context_repository.py` 对 state 3/4 消息各建 2 条 pending_adapter 意图，测试注入 2 条消息恰为 4 条，数字吻合。改为断言恰好 2×2 有界意图。
   - 修后暴露下一层：`not c.platform.errors` 门禁下的 4 个错误实为原行为调用——注册流程 `customer/batchget`（`completion_business.py` 注册分支，契约「注册/归因/欢迎」要求保留）×2、素材只读 `media/get` ×2。测试补 batchget 脚本；假平台对 media/get 提供确定性「素材不可用」响应（调用方按原有界降级，不计错误）。
2. **test_kf_completion_sends（平台拒发）**：原 adapter 发任何可下载文件前先上传默认缩略图（`adapter.py:358-361`），真实环境该 media_id 有 1 小时缓存（228-231 行缓存优先）；前任假平台不提供 `media/upload` 端点，上传被拒抛异常致 unknown 中断。测试预热缩略图缓存一行后，wire 序列恢复 [body ack, file-link reject] → `closed_failed_known`。

期间一个错误假设（"≤2MB 文件转图片路径导致 upload"，实为默认缩略图上传；该路径仅对 `image/*` mime 生效）被探针证伪后，基于它的夹具改动已全部撤回。

**尾批回归**：18 用例（同交接 command.txt 窗口）两轮全量各 17 通过/1 失败；两轮先后失败的转人工、子任务澄清用例单独重跑均通过，失败原因均为远程隔离库（124.222.3.254:5433）在约 11 分钟整批负载下的连接拒绝，非代码问题。DROP DATABASE 清理抖动与交接记录一致。**遗留基建项**：隔离测试库为远程共享实例，整批高负载下有连接拒绝抖动，建议 M7 前核实其容量/连接池配置，避免环境噪声混入验收判断。

18 用例逐项对照收尾契约行核对（追问/语音媒体/账号限制/普通收发/消息规则/边界/发送未知与已知失败/转人工/迁移回放），全部有契约依据，无「为测试而测试」项，全部保留。

### M6a 最后修复记录（待最终验收）

首个兼容开发窗 81327 为 4 通过、2 失败，未计整组通过。普通追问的业务断言已到末，失败来自测试遗漏原注册流程的 customer/batchget 响应脚本；只补该前提，保留错误列表与全部业务断言。子追问接单被拒，源码确认顶层展示与内层 question ACK 门冲突；取消没有原功能依据的 ACK 门，保留可信来源、唯一真实澄清与当前版本绑定。原日志没有捕获内层错误码，不倒填实际错误码。

另已核实旧发送异常仍释放会话；新投递 unknown 永久保留 claim 会使同一会话后续聊天停住。最后修复仅在原 transport 实际结束、服务确认执行已终态且发送台账没有 started/未决定操作时，诚实关闭为 closed_unknown；原操作仍 unknown、不重发、不触发成功 recap，由原 Manager 在同一事务释放 claim。不以租约过期推断网络请求完成，不新增结果核对服务。

最后增量为 Delivery 两源、双 DDL 及数据库表说明，另补 SourceClient 对 closed_unknown 的单一允许枚举遗漏；与此前八个兼容源一起冻结后，完成定向开发验证、独立业务回归和最终源码审查。测试的同会话继续预期来自原实现，不再用不同客户的新会话绕过卡住的会话。自有旧窗六份数据库/连接/进程已审计清理，不扫描或删除未登记资源。 新版运行前独立审查发现，测试强制抛出 NativeWriteUnknown 只是原内部异常形态；域内实际排水并诚实收尾后可以返回。因此仅撤该异常形态断言，真实 dropped HTTP、原操作 unknown/没有 ACK、不重发、不成功 recap、原费用与同会话下一轮继续的业务断言全部保留，归档修改前后字节和理由。

最后投递 v1 开发窗 10962 实际为 1 通过、2 失败、1 setup error（122.02 秒）。普通追问通过；子追问已完成客户回复接单和执行结果，但共用测试辅助错误要求所有 presentation 记录只能有一份；等待追问与最终答复本可分别保存。未知发送用例因未导入原 prices fixture 未进入 body。迁移用例 rewind 早于原更新器对旧元数据表的初始化，未进入增量升级验证。四项结果保持原记录，不将前提失败当产品验证。

独立源码审查另拦住新增 CP 检查：Finalizer 在正常终态保留 pending_finalization 供迟到费用补结，并不代表执行未结束；微信投递域不应重复解释核心执行树。v2 仅删除这块判断，保留服务终态/当前绑定/无恢复命令及本域发送操作实际排水证明，未修改业务测试断言。v1 两项审查问题与失败证据保留；v2 定向独立审查 P0/P1/P2 均为 0，470 当前源与单叶归档/首末一致，其余 469 同 v1，待原相关节点复验。

10962 后的前提修正已先审查再冻结：仅导入原 prices fixture；迁移先用自有临时文件内的 canonical 最后一块调用原 updater 初始化旧元数据表，再用原完整路径验证 fresh/upgrade/skip/replay；投递辅助只严格选取当前真实服务终态 presentation，核对它自己的原初始来源绑定、结果正文和平台 ACK，不将客户答案的 locator 当初始 anchor，不强制累计投递记录只有一份。原业务与来源断言保留，未为测试调整生产逻辑。复验只跑此前未完成的三项，普通追问已通过开发证据保留。

投递 v2 开发窗 72674 为 1 通过、2 失败（119.45 秒）：DDL 完整新建、原精确 CHECK 升级、重复执行与原 known 行守恒通过；子追问的接单、执行终态和来源/投递收尾已到末，测试仍错误把所有 wire 限成一条正文；未知发送已真实排水并经服务关闭，旧 SourceClient 不接受新的 closed_unknown 结果值。生产 v3 只补这一允许枚举；单叶编译/import 与独立审查通过，其余 469 源与 v2 相同。测试只将正文校验限定 purpose=body，保唯一真实平台 ACK，合法原进度消息不再被这条正文断言禁止；原失败日志未保存额外 wire 的具体类型，不倒填本窗实测类型。DDL 与普通追问已通过开发结果复用，最后开发只复验子追问及同会话继续两项。

最后开发窗 82071 实际 exit 0、2 通过（164.38 秒），源码 470/辅助 25 首末零差异：一层子任务真实追问、客户聊天答案、原执行完成与正文 ACK/历史/费用到末；发送响应丢失保原 unknown、不重旧 POST、不成功 recap，同客户同 SID 下一批真实接单、模型执行及正常回发到末。v3 SourceClient 单枚举独立 CR0；主控亲核当前 470/归档单源和最终辅助 43 均一致。最终固定 18 条独立回归已自然结束：16 通过、2 失败，实际 exit 1；用户随后取消剩余工作，阶段不关闭为完成，详见正式交接。

### M6a 交接边界与运行入口

本次只交付到 M6a；M6b 飞书、M6c 钉钉、M7 默认迁移与四入口/Runtime+BOSS CLI 综合验收由接手者继续。现有 Runtime+BOSS CLI 仍是兼容对象，不新增为本次服务入口。代码保留在当前 master 工作区，未获提交、推送或部署授权；不因阶段验收自动打开开关。

`agent_runner.enabled` 与 `agent_runner.wecom_kf.enabled` 默认关闭。接手者在明确获得部署授权后，按既有增量数据库机制应用 `deploy/db_update.yaml`，新库使用 `deploy/init-postgres.sql`；配置服务地址、可信 peer 及微信 service_id/原渠道配置，不把令牌或密钥写入文档/日志。HTTP 入口本身不执行迁移，也不替代 Worker。

运行组件（入口说明，不是本次部署记录）：

- HTTP API：`python -m src.services.agent_runner.bootstrap`，接受 `--host`、`--port`。
- 执行器：`python -m src.services.agent_runner.worker`，接受 `--worker-id`、`--once`、`--max-tasks`。
- 微信拉取消费器：`python -m src.channels.wecom_kf.ingress_worker`，接受 `--max-pages`。
- 微信接单/投递消费器：`python -m src.channels.wecom_kf.admission_worker`，接受 `--once`、`--max-inputs`。
- recap 交接消费器装配在现有 `src/background_runner.py`，没有独立 recap_handoff CLI；继续使用原后台组件启动方式。

最终验证使用现有容器内隔离 PostgreSQL、真实服务/Worker/渠道代码以及 localhost 的微信和模型响应夹具，不宣称已验证真实微信账号、大模型付费链路、生产容量或正式部署。已撤销测试保留原字节和撤销理由，不计通过；原 workspace 授权测试仅需接手者换合法等待前提，其授权边界并未取消。ASR 未知结果没有新增自动查询/回调找回服务，运行期丢失的未知外部结果仍诚实保存，不能手工重投冒充自动恢复。

### M6 历史实施与验证记录

顺序：微信客服 → 飞书 → 钉钉。每次只修改目标渠道路由/适配器；`channels/session.py` 的共享修改必须验证尚未切换渠道仍走原路径。

微信客服内部按三片交接，不一次改完共享渠道流程：第一片是完整路由绑定、耐久拉取意图/入站页与账号游标仓储、当前配置及可信路由授权；第二片是耐久接单、接单前合并与接单后安全点补充、ASR事实和原执行上下文；第三片是固定回发路由、耐久发送预算/投递状态、发送后claim释放及一次recap。每片实施前明确写入范围，分别开发自测、独立测试和审查；前两片不能单独宣称微信客服已迁移完成。飞书、钉钉复用已验收的中性边界，平台协议仍在各自适配器内。

第一片已授权实施（2026-10-03）：新增KF来源验证、纯仓储、拉取worker及同cursor会话DAL，必要修改仅KF callback分流、配置和双DDL/表文档。`agent_runner.wecom_kf.enabled` 默认关闭，先不接Runner/ASR/回发；开启来源为服务器配置，不信请求布尔证明。回调用当前明确配置、真实密文签名和corp解密绑定，配置中的账号/profile精确匹配后短事务保存拉取意图再ACK；ACK前不增加账号列表联网依赖。worker在网络前后复核当前配置，以原owner/epoch/cursor和领取generation一次提交整页inbox及新游标，期间新通知不被覆盖。page事务不得调用旧会话方法的Redis/network/self-commit，新DAL保原SID及NULL用户、拒绝模糊匹配。新入口在原query/XML调试日志之前分流，台账只保存有界业务事实，不保存鉴权参数或原XML。plain认证及callback Token官方语义仍有明确迁移门；未知条款不扩建凭据平台，首片默认不切换，也不冒整渠道已兼容。

实施预查补充兼容门：不支持的表情/消息类型保存有界事实，不能卡住整页后续文字；原直接欢迎/会话状态回调与同步页进入会话的scene、欢迎能力须耐久保全，欢迎Code使用既有secret_crypto私有加密存储，不放公开payload、日志或Runner上下文。同步消息保原provider msgid，直接回调使用独立事件命名空间，不伪造用户消息编号。默认profile的旧空值与main保持原SID；缺失绑定的事实读不能创建会话。配置版本只约束本次在途IO，不能使同账号/profile下旧事实因文案更新失效；真正归属或执行profile变化不静默重新指向旧消息。已随首片冻结进入验证，不作为整渠道验收结论。

### M6a 第一片冻结与开发正常窗口（2026-10-03）

[交接](../../tmp/agent-runner-evidence/m6-kf-foundation/v1-handoff.md)精确11生产/schema/文档源（7Python），[源码](../../tmp/agent-runner-evidence/m6-kf-foundation/v1-source-sha.json)及有限86源/入口见证已由主控实读、当前与归档字节亲核一致。原SDK新增仅native有界私密模式，仍保持两参数factory与原默认模式；SQL期限仅本仓储LOCAL statement5s/lock3s，不改共享pool策略。双DDL为同形单DO，变更号`2026-10-03 12:00:00`，暂无行为迁移结论。真实编译/import/配置/原DDL split检查exit0不代替业务测试。

[开发正常99279](../../tmp/agent-runner-evidence/m6-kf-foundation/development-1-run-evidence.json)实际exit0，1 passed（2.89s）；86源/12实际测试辅助首末一致。独立作者用例由实施者执行，真实隔离PG、原Crypto、原SDK与loopback平台HTTP、原Worker/DAL保存页、游标、route及既有NULL用户/旧空profile SID；不建Runner、不调用ASR、不发送、不计费，也不是官方平台认证。独立11风险节点/7有限组及必要SDK原默认关联、独立CR已调度，当前未正式验收。仅测试自有数据库/peer收尾，未知旧库、Yohar及master容器保留。

独立首窗[34597](../../tmp/agent-runner-evidence/m6-kf-foundation/formal-b1-run-evidence.json)实际exit0，6 passed（21.14s），86源/14测试辅助首末一致；涵盖原callback ASGI、currentcfg/crypto、在途新generation、真实晚期SQL失败整页回滚、private欢迎/unsupported/recall/servicer，以及原SDK真实HTTP超限/安全错误/在途配置撤销。ASGI不代真实socket压力，原SDK错误已实源证转安全失败而非直接退出worker。独立[v1 CR](../../tmp/agent-runner-evidence/m6-kf-foundation/v1-cr-report.md)P0/P1=0、P2=1：新增无依据±300s期限会拒绝合法延迟通知，不能等默认切换时才处理。CR实际00:08:27→00:15:56 UTC，86当前/11归档首末匹配。主控在运行窗结束后仅授权auth去该期限、保原签名/currentcfg及严格timestamp形状；独立作者在原HTTP节点加入延迟签名通知，后续v2实际补验与剩余5风险/关联尚待，v1结论不倒填v2。

[v2单源交接](../../tmp/agent-runner-evidence/m6-kf-foundation/v2-handoff.md)仅`ingress_auth.py`去未证期限及unused time，其他10源/85有限闭包路径同v1。主控已亲核当前86与归档11一致；实际单源compile、既有API容器affected import及diffcheck退出0，首次Docker sandbox拒绝未执行Python，如实保留。独立[v2定向CR](../../tmp/agent-runner-evidence/m6-kf-foundation/v2-cr-report.md)实际00:19:48→00:20:23 UTC，86/11首末零漂移，P0/P1/P2=0。延迟真实签名及剩余风险运行仍待；不重复开发正常或已通过且不受时间策略影响的其他5项。

主控基础片正式验收：[63429](../../tmp/agent-runner-evidence/m6-kf-foundation/formal-b2-run-evidence.json)实际exit0，10 passed（27.29s）为剩余5风险、同一authority的v2补验1及SDK/原Crypto关联4；延迟900s合法签名首通知、原双DDL fresh/增量/replay/错索引原子拒绝、真实锁等待跨lease到期、cosmetic配置与旧fact/新epoch、空/main兼容、真实profile改向拒绝和缺route纯读不自建均到末。合计[清单](../../tmp/agent-runner-evidence/m6-kf-foundation/final-inventory.json)正式11唯一＋关联4＋开发1，3个实际行为窗；未把v1五项复用声称实际加载v2。主控实读日志/首末/CR并亲核最终86源/23实际辅助union当前一致，复用范围限唯一timeguard改动的无影响行为。末[清理](../../tmp/agent-runner-evidence/m6-kf-foundation/cleanup-final.json)确认pytest/KF CLI及自有HTTP线程已收尾、新测试库0；已知旧库1且0连接明确排除。随机数据库ID原wrapper未输出，只记录实际finally DROP返回及catalog无新增，不补造ID。本段只证明耐久接收、游标及原会话绑定，Runner接单、ASR、投递和真实平台认证仍未完成；释放源码冻结仅供后续获授权的具体片修改，不倒算本次证据漂移。

下一段先文本可信来源＋接单＋同Runner安全补充，之后独立增加voice/ASR；通过受信内部API提交有界receipt定位而非渠道worker直接运行第二套Manager。中性来源端口由应用装配平台DAL，提交/运行/读都以当前配置及原inbox/route证明授权；public DTO不接受布尔proof。有序输入以原root/claim锁与CP CAS确认attach/apply，最终cutoff同事务复核。预算耗尽时仅未实际进入原模型/历史的pending或精确queued引用可原子defer到下一queuedRunner，不重置旧预算、重演旧事实或丢追加输入；保原SID/顺序及旧deliveryclaim。文本小门仅origin3/text且原SDK当前service_state严格为1可派发，网络在SQL锁外且回事务fresh复核；unknown/人工/voice/lifecycle等保received，0/4转换及欢迎发送后门处理，默认不开启完整迁移。

文本小门原21源（17Python＋配置/双DDL/表文档）实施中，新增6分别是中性`source_receipts`、唯一应用装配`application_sources`、固定内部HTTP`source_client`、纯cursor`input_repository`、KF`admission_repository`与薄`admission_worker`；原修改限定API/Manager/Authorizer/Runner DAL、Worker应用装配、DurableControl/ExecutionRepository/TransactionRepository、settings、KF入站仓储/SDK和canonical双DDL。已批准必要第22源`runtime/context_assembler.py`：仅实际新增root用户消息附服务器可信initial input_ref，默认None保兼容，旧历史/child不附、恢复不重装；不能以历史中存在user推断本次输入已应用。内部`POST /v1/source-inputs`只接有界原receipt定位及服务端重算稳定键，不收tenant/user/target/proof布尔；返回immutable首接归属、当前Runner及耐久disposition。原source/actor/chat查询头与私有input定位仍需当前原归属验证，定位不是权限。应用服务同TX提交来源link和Runner/input，薄consumer只读平台inbox并HTTP重试原键，不直接读Runner私有输入表、接单或运行Agent。新增源范围外有实际必要时先说明因果，不将白名单冒当最终manifest。

迁移兼容门：native新接单/执行必须原receipt，不能随入口开关回退自证；旧无receipt已接受KF Runner不伪造升级，当前可信actor及原ownedSession下按服务端明确legacy read/cancel政策保持可查可取消，不能因新来源门锁死旧任务。原2秒接单前合并如不在本text小门完成，必须在完整M6默认切换前补齐，raw单receipt API不代原batch语义；每成员来源/order/历史稳定ID仍须保存。实施不碰Engine、前端、voice/usage、发送及其他渠道；尚未运行或验收文本链路。

实施前审追加两处收口：平台inbox仅存自身nullable接受链接，由来源provider在原Runner接单事务写入，consumer不依赖服务私有表；同SID但完整执行来源不同必须新建queued Runner，保旧来源与delivery claim。补充输入触发同Runtime继续时复用原统一执行/终结判定，保verbose观察、停机、暂停安全树、取消及未完成child保护，不复制第二条弱化流程。上述为待实施/待验证约束，不算行为通过。

终结收口仅允许明确completed cutoff或实际iteration_limit且预算已耗尽转接未用后续输入；runtime未装配/普通失败仍有pending时保原事实与claim核对，不将初始输入自动重排。平台状态观察时点固定在真实成功响应时，关闭客户端不刷新观察年龄。后续voice有限接口提案已只读核原Speech真实POST、AI与人工ASR记录边界：先接原voice intent，AI准备和费用归原Runner，人工/员工仍独立记录；已发生识别的defer复用文字并保原费用owner，started未知不得重发。voice尚未授权具体源、实施或测试。

文本v1已冻结：[交接](../../tmp/agent-runner-evidence/m6-kf-admission/v1-handoff.md)与[元数据](../../tmp/agent-runner-evidence/m6-kf-admission/v1-freeze-meta.json)实际01:11:39 UTC记录22源/18Python、374有限依赖见证（最小实际import97、保守静态Python329、原profile资源14去重，不宣称374均实际加载）。主控亲核owned当前/22归档及374当前全一致。第22源before字节为精确逆转见证，另与可用M5 final-end及真实UI首末封存hash一致，不冒实施前独立捕获。[检查](../../tmp/agent-runner-evidence/m6-kf-admission/v1-check-evidence.json)末18compile/import、globaldiffcheck实际exit0，原updater13:00块split1且双DDL一致/defaultfalse；两次检查脚本选错入口/键失败发生在业务前，未运行SQL/Worker。已放唯一开发normal等待tester最终helper核定，独立只读CR并行；本时点尚无文本业务通过或独立风险结果。

文本开发[85858](../../tmp/agent-runner-evidence/m6-kf-admission/v1-development-1-run-evidence.json)实际exit0、1 passed（17.69s），374源/23测试辅助首末/current一致；主控实读日志/捕获及末断言，原Crypto/SDK→平台inbox薄consumer→独立socket API→原resident Runtime/Engine→18tokens/唯一0.01 record/稳定source:user历史到末，terminal保delivery claim；非真实平台或独立风险验收。[清理](../../tmp/agent-runner-evidence/m6-kf-admission/v1-development-1-cleanup.json)只读确认新增库/连接/pytest/probe/KF CLI0，旧未知库排除；原finally DROP、HTTP线程收尾已返回，不补造随机库ID。完整[v1 CR](../../tmp/agent-runner-evidence/m6-kf-admission/v1-cr-report.md)实际01:14:53→01:20:51 UTC首末374/归档22一致，P0=0/P1=3/P2=1：unstarted暂停后取消无法收尾、来源锁等待后缺最终Attempt派发复核、已完成父恢复期新source补充被旧child mirror吞掉，以及明确外属read ref错误映成500；均为源码可达判断，未冒实际复现。已闭开发窗后只授权Input/DurableControl/Execution/Transaction仓储与KF provider五源v2收口，其余369保持；独验准备对应真实取消、真实锁等待及真实child producer反例。取消仅明确unstarted零IO事实，source新输入复用原root continuation意图并同步live root字段，无新输入旧mirror保原；此片尚未验收。

文本[v2交接](../../tmp/agent-runner-evidence/m6-kf-admission/v2-handoff.md)实际01:36:13 UTC冻结，主控亲核374同路径且只有上述5变化、其余369对v1不变、22当前/归档一致；combination SHA `31bcc66c5c3b134687f6982ca33ad97e461ec52524514e9901eed664f535eee2`。[检查](../../tmp/agent-runner-evidence/m6-kf-admission/v2-check-evidence.json)末五源实际containercompile/import及globaldiff0，保中间缩进及host python不可用失败记录，不冒业务失败/通过。unstarted取消用原精确pause release proof及零receipt两处验证；原child.before_model/tool的rootCAS也增加最终派发复核，known-result保存只复核Attempt；初始取消历史保持cancelled未执行事实。独立角色已准备13正式风险＋开发normal1，34为有限静态测试/helper union而非全加载。已调v2定向CR与首批3实际反例＋read authority1，等各自actual pre/exit/end；无v2业务通过证据，后续9风险及有限原关联未启动，不重跑旧阶段矩阵。

[v2正式B1](../../tmp/agent-runner-evidence/m6-kf-admission/v2-formal-b1-run-evidence.json) session49443实际exit4：测试相对import错误，0业务节点；374源/28辅助首末一致，不是生产失败或通过。独立作者修正child及尚未运行的storage/filter测试import；实际清理自有新库/连接/测试进程为0，未知旧库保留。[v2 CR](../../tmp/agent-runner-evidence/m6-kf-admission/v2-cr-report.md)实际01:40:18→01:43:23 UTC，首末零漂移；既有四项源码门关闭，新增P1：来源continuation标记处理后残留，可使已知FAILED的空恢复误进新模型。主控关闭该窗口后，仅授权InputRepository/DurableControl修复标记生命周期。

[v3交接](../../tmp/agent-runner-evidence/m6-kf-admission/v3-handoff.md)实际01:51:41 UTC冻结；主控亲核374当前/22归档一致，仅上述两源变化、其余372对v2不变，SHA `696440191da8d6886e5034e4656d978d36297dc3bcae87dc5ab3c5a3f034a0ec`。普通补充输入不安装标记；只有completed父镜像的尚未处理新来源意图才安装，并仅清自己所有的意图。控制ID变化本身不转交所有权，真实独立父续接还须原root continuation映射和同控制user消息，child回复/空resume不能冒父续接。末版两源compile/import及diffcheck实际0，尚无v3业务通过。已调独立v3定向CR及五节点实际isolated collect-only；收集通过后同五节点正式新窗，含真实FAILED后明确CP/interruption时序注入、默认空resume不重放。静态import核验不代实际加载，注入不冒自然崩溃。

[v3定向CR](../../tmp/agent-runner-evidence/m6-kf-admission/v3-cr-report.md)实际01:57:55→02:02:01 UTC，374当前/22归档首末零漂移，P0/P1/P2=0；读取两源delta及原控制/child恢复/Engine关联，未变372复用既有审查并核字节，不冒全量重读。[实际collect33942](../../tmp/agent-runner-evidence/m6-kf-admission/v3-collect-b1r-run-evidence.json)exit0，5 collected（0.23s）、0业务通过；主控亲算374源/30辅助首末及当前一致。正式B1r session67171已启动同五节点，待真实退出与收尾；源码CR和成功加载均不替代业务结论。

[B1r67171](../../tmp/agent-runner-evidence/m6-kf-admission/v3-formal-b1r-run-evidence.json)实际exit1，5 setup errors（0.87s）：风险模块未注册既有`kf_scope`，未到receipt/API/Worker业务，374源/30辅助首末一致。成功collect未证明fixture依赖闭合，不能记业务通过或生产失败。作者仅显式注册原fixture、未改fixture行为或生产；[原窗清理](../../tmp/agent-runner-evidence/m6-kf-admission/v3-formal-b1r-cleanup.json)实际新库/连接/匹配进程0、wrapper finally返回。B1r2 session93806以新首捕启动同五节点，待退出/末捕，不重复collect。

[B1r293806](../../tmp/agent-runner-evidence/m6-kf-admission/v3-formal-b1r2-run-evidence.json)实际exit1，4 failed/1 passed（80.89s），374源/30辅助首末一致；native read authority及legacy receiptless read/cancel末验通过。cancel/dispatch两节点在空provider script前置失败；FAILED节点实际第二模型400、1observed＋1unknown及两input appended，但故障注入位置在Worker已interrupt后，未到空resume断言，须仅改测试时序，不能记生命周期通过。child节点实际原父completed＋子waiting、3observed后合法reply返回500，未到Attempt2；源码候选为原ControlApplication执行授权未提供native PreparedSource，主控已调独立定位及有限修复提案，不倒填已丢私日志的确切异常。原窗自有数据库/进程/HTTP线程收尾为0，来源版本继续冻结，未开下一批或Voice生产。

[v4交接](../../tmp/agent-runner-evidence/m6-kf-admission/v4-handoff.md)精确API/ControlApplication两源修HTTP控制：首次fresh读/幂等→锁外来源准备→二次fresh读/同key优先→prepared执行授权/credit→原控制仓储；native追加原expected_revision，sync/Web/receiptless/pause保持兼容。主控亲核374当前/23归档零差异、仅两delta其余372同v3，SHA `0f09aa9511df9d4286867f25127e3b69bff627d7662bc83978b351b194d45315`；新增ControlApplication原字节实际预捕匹配v3，非回填旧22。末compile/import/diff0。[开发原Web65375](../../tmp/agent-runner-evidence/m6-kf-admission/v4-development-1-run-evidence.json)exit0、1 passed（10.53s），374源/20辅助首末一致：原API pause/resume/currentcredit/已接受duplicate、执行前撤信用均验证，model/receipt/record0；不冒native恢复通过。真实自有库/连接/进程清理0，原provider peer teardown返回而非不存在。

[v4定向CR](../../tmp/agent-runner-evidence/m6-kf-admission/v4-cr-report.md)实际02:22:59→02:26:04 UTC，374当前/23归档首末一致，P0=0/P1=1/P2=1。HTTP缺prepared原P1源码关闭，但关联Worker领取恢复→Recovery.prepare仍缺prepared且SourceUnavailable未按候选隔离，可中止Worker；另明确state3拒绝被Worker包装语义映500。两项为可达源码判断，不冒实际复现。开发窗结束后主控仅授权Worker/RecoveryCoordinator/RecoveryRepository/ControlApplication四源修：中性锁外prepare、短claim事务末来源/新Attempt复核、精确CAS核对保留原CP/control/claim/wait、HTTP只恢复已知安全RunnerError cause，未知SQL/异常不泛化。待v5冻结及真实反例，未启动后批。

[v5交接](../../tmp/agent-runner-evidence/m6-kf-admission/v5-handoff.md)精确上述四源、25自有文件（新纳Recovery两叶）、374有限见证；主控实读末四源compile/import/diff0并亲核当前/归档零差异、其余370对v4不变，SHA `0f20841483eb99b40815aa629182cde084cffdd7843e3801621e71ecba18af06`。恢复claim在消费/CP/公开投影/event SQL后重核来源及实际新Attempt许可，仅这个未commit末fence的LeaseLost转候选hold，整笔回滚；全局SQL/commit/其他marker不吞。公开hold保原waiting字段及CP/control/claim，只真变化通知；HTTPtyped安全拒绝保持同keywinner优先。已调独立四源CR；新增nativecontrol正常节点明确API+Worker均gateoff、accepted来源临时不可用hold后原Attempt2执行到记录/费用/历史末验，作为v5唯一开发业务窗，再由独立角色必要重验，不混角色结果。独立另准备真实event表锁跨新lease反例：明确只lease实参1s时序DI、真实DBclock/Attempt/SQL/原notify，不能冒默认lease自然过期；当前尚未运行该反例。

[v5 CR](../../tmp/agent-runner-evidence/m6-kf-admission/v5-cr-report.md)实际02:37:38→02:39:49 UTC，374当前/25归档首末一致，主控实读并亲算当前/首末0，P0/P1/P2=0；原关联恢复及HTTP拒绝两项源码关闭，不冒业务通过。[v5开发22712](../../tmp/agent-runner-evidence/m6-kf-admission/v5-development-1-run-evidence.json)实际exit0、1 passed（27.20s），374源/24辅助首末及当前/归档亲核0：真实SDK state3→HTTP409，credit402，当前actor及同key幂等，API与默认Worker gateoff下原控制hold→恢复同Runner Attempt2，原model/receipt1、18tokens/.01、source历史一次/delivery claim。真实自有库/连接/进程0、原HTTP peer teardown返回，非单独线程普查。开发通过不代独验，已放行五正式新窗（前三反例＋FAILED时序节点＋native控制），不重复收集，另新claim租约回滚及剩余风险/原关联分批。

[v5正式B151627](../../tmp/agent-runner-evidence/m6-kf-admission/v5-formal-b1-run-evidence.json)实际exit1、4 passed/1 failed（125.93s），374源/31辅助首末及归档由主控亲核0，真实自有资源清理0。取消、真实parent/child恢复期间新来源、真实第二模型FAILED＋明确原CP/interruption时序注入后的默认空resume、native控制/gateoff/drain全部末验通过。dispatch在本地端口config未安装API实际native peer处失败，尚未acquire/SQL锁/lease断言；仅该测试局部typed配置修同真实peer/hash/exactsource，来源/时钟/PreparedSource均不伪造。四pass不重验；第二窗已放行dispatch修后、新claim lease回滚、ordered input/cancel、budget defer、real CAS/SQL回滚共五节点；其他六风险及必要原关联另批，当前不记未运行结果。

[v5正式B251905](../../tmp/agent-runner-evidence/m6-kf-admission/v5-formal-b2-run-evidence.json)实际exit1、4 passed/1 failed（71.88s），374源/29辅助首末一致、自有资源清理0。已commit dispatch租约、未commit新Recovery租约（明确lease实参1s DI＋真实event表锁/DBclock）、ordered/cancel、真实CAS/SQL回滚都到末通过；hold可新增合法Attempt1公开核对事件，不冒新Attempt2成功事件。budget实测max/iteration1、第二输入接受及模型释放后公开failed，后续断言未到；主控与独测实读原Engine/Worker，原followup耗尽为ITERATION_LIMIT→failed，本例completed预期错误。只改测试严格failed/ITERATION_LIMIT/实际CP预算及原output，保所有defer/claim/usage末断言，不改生产或伪造此次未观测到的具体error。第三窗放行修后budget＋剩余六风险；旧通过八项与未受影响read1不重验，原Web关联末批另验。

本text小门对已parked clarification/paused/interrupted/Browser wait明确保received，不接受为新Runner、不猜普通文本为子回复/物理工具完成，不改原等待/兄弟执行树；不另提取或复制控制仓储。对应独验只证明这个hold边界，不能宣已实现渠道续接。完整M6切换前必须补真实问题投递/原wait绑定、exact leaf reply与安全resume桥；来源未知或副作用未知仍拒盲重放。已accepted native任务在入口开关关闭后仍用原receipt/currentpeer证明收尾，开关仅挡新接单，不降legacy。平台state读取返回的内部PreparedSource在原派发事务中核IO配置版本及新鲜度，不能只再核相同语义身份而漏在途配置变更。

本次查齐persisted授权调用另核Browser边界：BrowserWebAuth两个入口有web/chat约束；BrowserHumanActions.automatic_guard本身无Web-only约束且未安装native来源能力，不能宣渠道Browser自动完成可用。此text片不扩该叶，完整渠道Browser及M7切换前必须补中性来源proof与accepted flagoff一致性、原自动/等待恢复实测；沿BrowserOwner原物理动作dispatch fence，不降低WebOwned权限或让普通渠道文字证明物理完成。

[v5正式B335568](../../tmp/agent-runner-evidence/m6-kf-admission/v5-formal-b3-run-evidence.json)实际exit1、6 passed/1 failed（73.16s）；主控亲读原日志、运行与清理记录，复算374源/29辅助首末及辅助封存均0漂移，当前源相同，自有资源0。严格预算ITERATION_LIMIT及defer/claim/history/费用末验、消费者过滤、双DDL正负迁移、完整route隔离、有限PG unknown/parked契约六项通过。authority三种SDK拒绝、在飞配置变更拒绝及无落库断言通过，但最后正常接受失败；只读定位为测试finally将原TEXT配置再次JSON编码，恢复形状错误。批准仅恢复原TEXT字节并核原config/updated_at、保最后202/created/事实/SDK次数断言；下一窗唯一authority复验加原Web revoke-401关联，不重跑已有十五项有效风险结果，不修改374生产源。该失败窗口不计完整authority通过。

**文本接单片验收通过（2026-10-03）**：[末窗5855](../../tmp/agent-runner-evidence/m6-kf-admission/v5-formal-b4-run-evidence.json)实际exit0、2 passed（13.49s），唯一authority完整复验与原Web revoke-401关联到末；374源/27辅助首末及实际加载归档主控亲核一致。累计正式16 unique（未受影响v3 read1＋v5其余15）、原关联1，开发三窗分别保原版本/角色；旧失败不改为通过。v5独立CR P0/P1/P2均0，最终4源编译/import exit0有效。主控亲核[最终记录](../../tmp/agent-runner-evidence/m6-kf-admission/v5-final-meta.json)的374当前源、25原生产归档、43实际辅助路径union及原末窗，当前hash均一致；[实际清理](../../tmp/agent-runner-evidence/m6-kf-admission/v5-final-cleanup.json)自有数据库/进程0，未知旧库0连接排除保留，HTTP finally关闭/join返回不冒独立线程普查。解除该片冻结，仅供后续明确范围修改；文本通过不代表平台发送、人工/语音、原2秒batch、Browser渠道续接或整M6已完成。

**AI语音片实施范围（2026-10-03）**：采用[最终20源提案](../../tmp/agent-runner-evidence/m6-kf-foundation/voice-first-slice-handoff.md)的AI业务闭环，17Python＋双DDL＋数据库文档；10源准备原语不是业务验收结论。先接受可信voice intent，再在原Runner/Attempt下媒体准备及真实ASR；稳定输入/物理调用身份、原费用owner、结果与usage同事务，未知不重识别。可信平台Recognition零ASR；模型只识别文字，历史与record用户文字共用中性投影，原intent/hash不改。网络/价格在SQL锁外，ASR后刷新来源证明；取消须等待自己实际HTTP/SQL/codec收尾。保旧默认Speech补计，仅可信native observer停其旧补计；媒体大小/租户路径/敏感日志有界。人工与员工ASR、平台发送、batch、其他渠道、默认开关仍留各自后片，不修改Engine/Finalizer/UI/API DTO。实施后独立测试及CR，尚无语音运行通过结论。

语音片范围补充（2026-10-03，实施中）：主控实读旧入站仓储的digest比较，发现新reader加入Recognition后，原仅media_id的旧voice回放会冲突并阻整页游标。批准必要第21叶`src/channels/wecom_kf/ingress_repository.py`，修改前单独封原字节；只兼容相同完整来源及原字段、自洽旧digest、旧voice缺Recognition时的新字段差异，不改旧payload/digest/acceptedref、不反补识别结果，真实媒体/来源冲突和已存Recognition改变仍拒。生产编排同时收口为中性准备描述及注入能力，平台表/SDK结构留KF adapter，唯一`application_sources`有限装配，不将平台判断藏进通用helper。

**AI语音v1冻结交接（2026-10-03）**：[交接](../../tmp/agent-runner-evidence/m6-kf-voice/v1-handoff.md)封存21自有文件及449有限依赖路径，107为实际import见证，不冒449均动态加载。末版18源编译/import、默认开关关闭、双DDL14:00单DO解析及diff检查实际exit0；DDL尚未执行。检查没有持久全闭包pre-compile map，已在[真实记录](../../tmp/agent-runner-evidence/m6-kf-voice/v1-check-evidence.json)明确，不复造历史首末。主控亲核当前449、21归档及[正常辅助27](../../tmp/agent-runner-evidence/m6-kf-voice/normal-ready.json)均零漂移，放行唯一开发正常节点及只读独立CR；正式12风险节点、Text关联2及旧Speech关联3准备中，尚无语音业务通过结论。媒体/token/model采用有限本地IO替身，原Speech物理POST、Usage/费用、Worker与原SQL路径保留；不宣称付费平台真机验证。

语音开发正常窗[44066](../../tmp/agent-runner-evidence/m6-kf-voice/v1-development-1-run-evidence.json)实际exit0、1 passed（20.33s），449源/27辅助首末及辅助归档主控亲核一致。原HTTP接受后才媒体/ASR，原Worker、Speech POST、模型、历史与单record到末：2 observed/applied receipts、18 tokens、费用.11；终态claim为delivery，待发送owner确认后释放，不能记为已投递。自有进程0、原隔离wrapper退出完成drop finally，未知旧库0连接保留；媒体作为私有可信产物保留。独立CR已定位需修P2：未started的pause被当preflight失败固化为knownFalse，resume不再识别；冻结未改，正式风险测试尚未放行，等待完整CR统一修复，不将开发正常通过记为语音片验收。

语音v1独立[CR](../../tmp/agent-runner-evidence/m6-kf-voice/v1-cr-report.md)实际首末449及归档21一致，P0=0/P1=1/P2=2：已有execution时取消绕过late started/unknown preparation核对并推进终态/delivery；prePOST暂停误固化失败且新准备调用在原StopRequested归一化边界外；voice缺必填media_id仍被接收。独立[暂停复现19225](../../tmp/agent-runner-evidence/m6-kf-voice/v1-pause-before-run-evidence.json)实际exit1、1 failed（12.15s），449源/29辅助首末及归档主控亲核一致；确实到paused、0POST/0receipt，指定retained断言显示media_ready变known/preflight，未到resume，不冒后半段通过。自有库/进程收尾0、旧库排除保留。主控批准原21范围内5叶最小v2修复，不动Engine/Worker/Finalizer/接口/界面；正式风险准备追加初始/late暂停与lateunknown取消因果节点至15，字段拒绝并入原节点，不扩参数矩阵，业务运行等待修后冻结。

语音[v2](../../tmp/agent-runner-evidence/m6-kf-voice/v2-handoff.md)已冻结：主控实读精确5叶差异，449当前源、21归档与修改前5叶真实归档一致，实际编译/import检查首末449相同、18源检查exit0，配置仍默认关闭；未修改DDL/Engine/Worker/Finalizer。取消统一先经原中性装配口核未知preparation，平台查询留VoiceRepository；有execution时不生成无模型取消证书。暂停保media_ready，原before_model停止归一化覆盖准备/授权；可信voice必填media_id。放行唯一正常开发复验及5叶独立delta CR，正式15风险与关联5尚待运行，不提前标语音验收完成。

语音v2开发正常复验[30484](../../tmp/agent-runner-evidence/m6-kf-voice/v2-development-1-run-evidence.json)实际exit0、1 passed（20.12s），449源/27辅助首末、当前及辅助归档主控亲核一致；原完整ASR/模型/历史/record、2 receipts、18 tokens、费用.11及delivery claim到末，独立测试尚待运行。自有进程0、原wrapper drop finally返回、未知旧库0连接排除保留。lateunknown取消的有限契约是阻止终态/delivery推进并保输入/费用/claim；原cancel_only仍finalizing、没有pending_finalization，原lease过期后允许再次尝试收尾，不宣称已自动变成verification waiting或费用已确定。

语音v2独立[5叶delta CR](../../tmp/agent-runner-evidence/m6-kf-voice/v2-cr-report.md)P0/P1/P2均0，原三组源码问题关闭；449源/21归档及审查首末一致。主控已核[正式准备15](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-ready.json)的42有限辅助union与首控制组30路径当前一致，放行七组串行独验（控制4、Recognition2、结果2、SQL回滚1、late/预算2、媒体codec2、DDL2）及原Text2＋Speech3关联；首次真实失败立即停后组，运行前后封当窗原版本，不将准备或CR当测试通过。

语音v2首正式[控制窗13173](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g4-run-evidence.json)实际exit1、3 passed/1 failed（82.92s），449源/30辅助首末及归档主控亲核一致；已知ASR迟到取消、原初始pause反例恢复、已有Runtime lateunknown取消保留事实三项到末通过。latepause节点期望末model not_started，实际completed；原首模型仅普通final内容，cutoff继续后Worker先准备追加输入，尚未进入下一before_model，主控与审查者静读确认是测试入口错位，不能据此称生产归一化失败。旧失败及未到resume保原版本；后组已停，源冻结，准备真正before_model时序，仅失败节点重验，不复跑原3通过。自有库/进程0、未知旧库0连接排除保留。

语音v2唯一latepause定向[复验70305](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g4r-run-evidence.json)实际exit0、1 passed（36.98s），453源/29辅助首末、当前及辅助归档主控亲核一致。453只追加未修改的原Read/三个parser依赖见证，原449窗口不倒填；未执行Word/Excel/PPT操作。原Read实际读自有文本后仅延迟原结果返回，通过真实晚到语音接单后放行，透明ContextVar确认原root before_model；CP工具配对/结果只一次、第二model not_started、媒体保留及resume到末，2 LLM/36 tokens＋1 ASR/费用.11，claim为delivery。原失败不改通过、原三通过复用，控制组正式累计4 unique；自有资源清零（新增probe单独实际末普查），继续剩余组。

语音v2 Recognition[26113](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g1-run-evidence.json)实际exit0、2 passed（43.73s），449源/28辅助首末/当前/归档主控亲核一致：缺media_id真实页拒绝且cursor无写、可信Recognition零ASR、旧media-only回放不改旧事实并走原ASR到末。随后结果[14252](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g2-run-evidence.json)实际exit1、1 passed/1 failed（34.26s），449源/27辅助首末/归档一致；badJSON unknown及resume不再次POST到末通过，正式累计7 unique。known reject已发生1 ASR POST/1 observed receipt，模型请求处HTTP异常，未到calls0、完成及.01末验。主控实读原Assembler会pop末旧未配对user及测试provider仅匹配user，确认本node把随机marker塞旧孤立history不可靠；批准仅外部模型脚本用原占位文字作为选择标记、删除旧history UPDATE，保持所有业务oracle，唯一失败复验后继续，不复跑坏JSON通过项。失败原版及自有收尾0保留，源未改。

语音v2 known reject唯一[复验42092](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g2r-run-evidence.json)实际exit0、1 passed（19.86s），449源/27辅助首末与当前主控亲核一致；固定占位选择标记后原业务oracle全部到末，整数明确失败calls0、原时间/占位历史及LLM18 tokens/费用.01。坏JSON原通过项复用，正式累计8 unique，非开发窗计数；自有库/进程收尾0，未知旧库保留。

语音v2 SQL结果[2885](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g3-run-evidence.json)实际exit1、1 failed（40.16s），449源/28辅助首末与当前主控亲核一致。真实ASR POST1与PG result rollback→unknown、原typed observer显式exact result重投/重复/冲突断言及HTTP resume202均已到；terminal收敛失败，原Attempt1/interrupted、resume rejected、receipt observed、input accepted。原失败未打印control error_code或CP标记且私资源已清，不补造历史拒因。独立审查发现初始prep在Runtime创建前失败、原CP未标unstarted的恢复契约缺口，尚须新诊断窗确认；仅批准本node安全诊断增强及唯一复验，源冻结、后组暂停、末费用/history/新Attempt未验。自有库/进程收尾0，未知旧库保留。

语音v2安全诊断[54980](../../tmp/agent-runner-evidence/m6-kf-voice/v2-formal-g3d-run-evidence.json)实际exit1、1 failed（40.98s），主控亲读原log及449源/28辅助首末与当前一致；新窗真实拒因CHECKPOINT_TREE_INVALID、execution不存在且unstarted=false，prep known/receipt observed、ASR1/model0。仅本node安全诊断增强、业务oracle未变，旧2885缺失历史不倒填。独立[只读源码复核](../../tmp/agent-runner-evidence/m6-kf-voice/v2-g3-readonly-review.json)确认P1，最小方案仅Worker原CAS在可信初始source准备前存unstarted、成功与fresh授权后Factory前清；窗口外missing execution仍拒，避免Assembler压缩已付费故障借标记重演，原业务计划首次装配一并保留。原窗已封存、自有库/进程0；唯一writer获授权实施Worker1叶修复，开发自验与独立CR/相关独验尚待，未把提案标为完成。

语音v3冻结生产delta仅原Worker1叶；主控亲读actual before/diff、453见证/21源码归档当前一致（新增Read4为未改依赖见证，旧449窗不倒填）。两次原dispatch CAS把unstarted限定于首来源准备窗口，成功后Factory前清，包括明确resume；Factory以真实execution区分已有计划与首次装配。最小[compile/import检查](../../tmp/agent-runner-evidence/m6-kf-voice/v3-check-evidence.json)实际exit0（0.269166875s）、1编译/1导入、真实loaded50、开关False，453首末一致，无SQL/调度/付费；开发原g3定向及独立delta CR进行中，仍未验收。未知resume可consume并新Attempt再hold，但不重ASR/模型、不推进终态/delivery；未另扩preconsume端口。

语音v3原g3开发[63987](../../tmp/agent-runner-evidence/m6-kf-voice/v3-development-1-run-evidence.json)实际exit0、1 passed（26.74s），453源/28辅助首末及当前主控亲核一致。真实ASR SQL结果rollback、原typed观察exact重投后resume到原Attempt2 completed/settled，ASR POST1/原Attempt1费用owner保留，LLM1/Attempt2、单record18 tokens/.11、input applied到末；开发通过不替代正式复验。原资源wrapper/peer/child收尾返回，自有进程0、未知旧库1/0连接保留。独立[单叶CR](../../tmp/agent-runner-evidence/m6-kf-voice/v3-cr-report.md)P0/P1/P2=0，453源/21归档真正首末一致，主控实读报告；原20叶复用既有未变审查。放行原g3独验先跑，新首次计划继承/付费摘要storage故障2项按真实组件另准备；v3总17 unique门，旧v2八通过保历史，不冒同版通过，关联仍Text2＋Speech3。

语音v3原SQL结果恢复独立[13397](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g3-run-evidence.json)实际exit0、1 passed（26.54s），453源/28辅助首末/当前主控亲核一致；原known结果显式观察重投后Attempt2到completed/settled、ASR1/原owner/历史费用.11全部到末，正式v3累计1 unique。报告archive字段初写v2后仅元数据纠正为真实v3归档21并记录原因/时点，原log/loaded/start/end不改。自有资源收尾0，旧库保留。主控核原剩余14＋关联5准备map，42有限helperunion当前一致、无冲突、AST节点全在，放行六组串行；新计划/摘要2另准备，首失败即停。

语音v3控制[7357](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g4-controls-run-evidence.json)实际exit0、4 passed（105.27s），453源/31辅助首末与当前主控親核一致；初始pause/resume、真实Read到before_model的latepause、known ASR迟到取消、已有Runtime lateunknown取消保原事实全到末。随后Recognition[25431](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g1-recognition-run-evidence.json)实际exit0、2 passed（47.74s），453源/28辅助首末与当前一致，缺media整页拒、Recognition0ASR、旧media-only回放/原ASR到末；v3正式累计7 unique，自有资源收尾0。新增计划与付费摘要存储故障2独验ready已交，静态准备不算通过；真实默认summary retry保留，门槛仅配置参数DI，原40k经济闸不改。开发清理[补充事实](../../tmp/agent-runner-evidence/m6-kf-voice/v3-development-1-cleanup-addendum.json)澄清原“media retained”文案无依据；原finally删除自有root返回，随机路径未预存，不倒造历史路径不存在。

语音v3结果[83392](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g2-results-run-evidence.json)实际exit0、2 passed（34.99s），453源/27辅助首末/当前主控亲核一致；整数明确失败calls0/占位模型与badJSON未知拒重POST都到末，v3正式累计9 unique、自有收尾0。新2独验全3文件主控实读、28/29静态map当前一致，批准原14及关联通过后依序末验；计划使用明示PG前序状态fixture、不假发送/释放claim，摘要用真原provider/usage SQL故障与fatalguard、保持默认retry2，不造storage异常。当前late/预算2进行中，首失败即停。

语音v3 late/预算[77256](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g5-late-budget-run-evidence.json)实际exit1、1 passed/1 failed（74.94s），453源/30辅助首末与当前主控亲核一致，自有收尾0、后组暂停。原completed-parent child回复后准备lateVoice/单次parent模型/no redelegate全到末，v3累计10 unique。budget首终态预期failed/ITERATION_LIMIT，实际completed/settled、iteration=max1、ASR0；主控与独立审查实读原Engine与cutoff，确认未prepared Voice不进入followup，原final回复应completed/error_code None，跟Text已queued followup才iteration_limit不同。批准仅首node两个状态严格oracle修正、唯一复验，保输出/max1/defer新Root/claim阻挡/零媒体ASR/费用/重复接单全门；旧失败的末defer/claim/fee未到不倒填，旧child通过保原helper版本，不重复运行。源码未改。

语音v3预算唯一[复验16216](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g5r-run-evidence.json)实际exit0、1 passed（22.20s），453源/30辅助首末/当前主控亲核一致；仅本node状态oracle按原completed契约修，AST及源码片段核第二child函数/全局imports未改，原child通过复用旧加载版本不重复计数。精确defer新Root/不可变accepted及current refs、原delivery claim阻挡下一执行、0 media/prep/ASR、18 tokens/.01和重复locator全到末，v3累计11 unique；旧失败不改通过，自有收尾0，继续媒体/codec及DDL。

语音v3媒体codec[59231](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g6-media-codec-run-evidence.json)实际exit0、2 passed（4.42s），453源/27辅助首末/当前主控亲核一致；原SDK实际下载上限/租户hash与symlink、原codec坏输入实际退出及exact返回PID时序取消后的kill/wait/temp收尾到末，非有效SILK质量/吞吐证据。随后DDL[75931](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g7-ddl-run-evidence.json)实际exit0、2 passed（7.79s），453源/27辅助同版一致；原fresh/upgrade/force/replay/catalog、legacyNULL/media-only保留、真实错误TZ整DO rollback且水位不进都到末。原15 unique已通过，自有收尾0；必要关联5及新增恢复计划/摘要2仍待独验，不先验收完整Voice或默认切换。

语音v3有限关联[46322](../../tmp/agent-runner-evidence/m6-kf-voice/v3-association-run-evidence.json)实际exit0、5 passed（44.24s），453源/31辅助首末/当前主控亲核一致，自有收尾0。Text2原ordered/cancel与真实ITERATION_LIMIT预算defer门保持，旧Text43未改；原Speech3无observer默认路径回归使用外部response mocks，不冒native物理付费链证据。当前正式15 unique＋关联5，新增计划/摘要2尚待，未复跑无关基线或构建。

语音v3新增首次计划恢复[99291](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g8-first-plan-run-evidence.json)实际exit0、1 passed（29.52s），453源/28辅助首末/当前主控亲核一致，自有收尾0。原ExecutionPlan前序PG状态为明示历史fixture，不冒已执行/投递；本次真实ASR unknown、typed结果观察已知DI后原Attempt2/Factory/BusinessPlan/Read推进首task、第二pending、旧root不变，ASR1/LLM2/36 tokens/.11到末。未称可自然重建未知HTTP内容，未声称不存在的当前delivery gate末assert。当前正式16 unique＋关联5；真实摘要付费receipt SQL故障最后1独验进行中。

语音v3摘要保护[11889](../../tmp/agent-runner-evidence/m6-kf-voice/v3-formal-g9-compression-run-evidence.json)实际exit0、1 passed（23.07s），453源/29辅助首末/当前一致。原长历史、摘要HTTP与真实receipt UPDATE故障触发fatal保护；摘要已发生付费后空execution恢复被拒，原ASR/摘要不重复调用，原输入、收据、claim保持待核对。这是阻止未知副作用重演的证据，不是摘要自动恢复证明。默认摘要retry2保持，自有资源收尾0。

**AI语音首片主控验收通过（2026-10-03）**：[最终清单](../../tmp/agent-runner-evidence/m6-kf-voice/final-inventory.md)及[证据](../../tmp/agent-runner-evidence/m6-kf-voice/final-evidence.json)记录正式17 unique＋关联5；开发3窗口独列，22实际业务窗口和6历史失败保留。主控实际重算当前453源、47辅助union及21生产归档均无漂移，独立源码CR P0/P1/P2=0、最终import/compile有效。历史4处辅助文件混合版本按实际各窗口保留，不冒全部47文件均以最后版本运行；预算oracle唯一复验与旧child通过按原加载版本计数。最终自有DB/连接/列明进程0，原fixture媒体目录清理返回；未知旧库排除并保留。批准释放本片生产冻结，仍不代表整渠道迁移、人工语音、平台发送、codec质量/吞吐或默认切换已完成。

**下一片实施授权：人工语境文本与分类（Context13，原十叶加必要服务只读接口）**。按[十叶边界](../../tmp/agent-runner-evidence/m6-kf-foundation/context-ten-leaf-readonly.md)仅新增微信客服lifecycle/context仓储及context worker，修改该域ingress_auth、admission仓储/worker、channel_session仓储，双DDL与数据库说明原10叶；增加服务manager、api、source_client三叶的可信来源只读GET，准确13叶。由唯一现有admission worker装配有界consumer，Runner/Engine不加入渠道判断。已有accepted Text/Voice链接优先，原provenance/hash不变，仍核完整可信来源及当前执行许可；未接受消息可靠分类和接受共用inbox锁winner。暂时SDK网络/格式失败保持未观测可重试，可靠人工/结束或已证冲突不被后来state1改属。历史写入、精确路由撤回及稳定recap意图同事务；recap仅pending_adapter，不能调用旧void执行器或宣称业务效果完成。原NULL用户、空/main profile及legacy SID保持，不猜路由、不创建替代会话。锁顺序cfg→route/session→inbox，SDK在锁外，末重核DB时钟/配置版本；新接单仍默认关。开发自测后冻结，独立测试与源码CR分别验收；媒体/人工ASR、发送、注册/归因、欢迎/结束通知、活跃AI撤回控制与真实两秒合并仍属后片。

Context实施预审发现后到客服历史可能先被较早accepted Runner首次装配读入。为避免以聊天历史代理任务状态、清空历史后永久等待，撤销临时history存在判定方案，批准上述3叶必要契约扩展：`GET /v1/source-inputs` 只读严格locator；同短RR只读事务核可信receipt/provenance、current_runner绑定与原read权限，返回有限公开状态及原服务DB观测时点，无正文/checkpoint/SDK/余额/分类或链接写入。渠道锁外有界查询，短TX末重核原cfg/fullroute/inbox/link及较早已接受候选集合；非terminal、绑定/候选改变、失败或原观测超过10s均保pending_history，不写历史或recap意图。不能收到HTTP响应后重造fresh时间，不能读Runner/claim私表，也不冒送达或完整交错保证。查询与扫描须有界，不每条重扫全部终生消息。按原控制/恢复拒绝终态、defer仅终态前改current绑定的实际契约，可在本域既有表存独立source_terminal观察；首次观测须fresh，后来仅完整绑定不变才复用，不能冒历史消费、送达或结算完成。最终门还须保守检查更早已入站但尚未accepted、仍可被AI接单的消息，避免另一个worker锁外准备时后到历史抢先进来；可靠固定non-AI与原不可接单事实不按潜在AI乱挡。开发期草稿未冻结，旧历史代理门不得进入最终代码；具体公开读取/时序/clear与queuedcancel兼容均纳本片独验，完整跨AI/context合并时序仍属后片。

Context开发态阶段收口：首次可靠SDK分类先经原inbox winner短事务独立保存，再锁外查询更早任务、逐单短事务保存fresh公开终态观察，最后同事务写历史/消费/recap意图。此前草稿把分类与历史绑在同一事务，后续SQL失败会丢已确认人工归属，多个HTTP也会耗尽首次10s时效；开发期修正后需验证可靠3/4不因投影失败转AI、临时SDK无观测仍可重试、既有accepted无新class不被阻。历史失败不回滚已提交分类或其他已提交终态观察，后续历史投影不要求过去SDK时点持续新鲜；当前完整绑定、配置与本次公开观察的提交时效仍分别核验。本段是实施约束，尚无Context运行/CR通过证据。

**Context13 v1 冻结与开发自测（2026-10-03）**：[冻结交接](../../tmp/agent-runner-evidence/m6-kf-context/v1-handoff.md)固定13自有文件、456有限源码见证，10源编译/10模块导入与diff检查实际退出0；75为实际加载源码，不称456均导入。首次开发窗3660为fixture注册缺失，实际exit1、1 setup error（0.17s），业务未执行；只补测试模块导入，生产代码与业务断言不变，原失败证据保留。复验[43355](../../tmp/agent-runner-evidence/m6-kf-context/v1-development-2-run-evidence.json)实际exit0、1 passed（8.17s）。主控亲核456源码/15辅助首末及当前一致、13归档字节及日志hash一致。原加密回调、SDK HTTP、隔离PG、NULL用户/空profile旧SID、可靠人工及员工分类、两条历史与四条pending_adapter意图、原两种历史读者角色转换和重复幂等均到断言末；未验证来源GET、付费执行或真实recap。开发unique1独列，尚非独立验收。[独立末审计](../../tmp/agent-runner-evidence/m6-kf-context/v1-development-2-readonly-audit.json)06:46:37.868 UTC晚于运行末06:45:12.968，排除未知旧库后自有测试库/连接及列明进程为0；随机历史库名未预留，不追造归属。正式独立CR及分组风险测试已启动准备，源码保持冻结，M6a仍进行中。

Context v1 独立首组[33124](../../tmp/agent-runner-evidence/m6-kf-context/v1-formal-b1-run-evidence.json)实际exit0、4 passed（33.87s）；主控亲核456源码/19辅助首末及当前、日志hash一致。正式正常1、临时SDK重试1、可靠分类/真实事件冲突1、实际SQL故障原子性1均到断言末。末只读审计自有库/连接及列明进程0，未知旧库排除。独立CR已指出待收口：GET额外/重复参数未拒绝、撤回对已存在历史缺完整scope/未命中区分、员工普通历史不应依赖SDK状态成功；冻结期间未修改源码，其余正式风险组暂不运行，待统一修复复核。本首组不覆盖来源GET、旧accepted执行、撤回、锁等待或完整Context验收。

Context v1 [完整独立CR](../../tmp/agent-runner-evidence/m6-kf-context/v1-cr.md)P0/P1=0、P2=4；已核四项具体源证据并授权唯一实施者在原13内7叶统一v2修复：严格Query、撤回完整历史命中证明、员工身份与可选SDK状态分离、双DDL按列名核主/唯一键。现有recap已要求真实状态3/4，不作为无条件意图缺陷；无状态员工不得伪造观察。旧schema反例需核所有键而非只换PK，其真实SQL拒绝由独验补证。生产修改期暂停其余正式组，v1原字节与首组通过保留；最终受影响正常/风险和定向CR待v2冻结后验收。下一人工语音/外围效果[只读候选](../../tmp/agent-runner-evidence/m6-kf-context/next-context-effects-handoff.md)仅作后片准备，未批准其中新owned recap子系统或扩大当前范围。

Context v2 四P2候选于07:13:48 UTC真实冻结，7叶修改、4模块编译/导入实际exit0，尚无业务测试；[交接/检查](../../tmp/agent-runner-evidence/m6-kf-context/v2-handoff.md)原样保留。主控预读发现员工可选状态后到的并发窗：另一consumer先写history后，首次SDK3补观察会因prior persisted早返而漏意图。已解除v2冻结，仅授权ContextRepository一叶形成v3；已有历史须完整投影证明后按真实3/4补稳定意图，合法清空历史不重插/不裸造意图，错行明确拒绝。独验补实际两个ContextWorkers＋原SDK响应时序节点；v2不机械追加业务运行，也不将其事后改称未冻结草稿。其余风险运行继续等v3固定。

Context [v3最终交接](../../tmp/agent-runner-evidence/m6-kf-context/v3-handoff.md)实际07:19:24 UTC冻结，仅ContextRepo相对v2变化；末单源编译/导入exit0，主控亲核456当前/13归档0。[v3定向CR](../../tmp/agent-runner-evidence/m6-kf-context/v3-cr.md)原四P2关闭，余一P2：真实员工3/4状态提交后、业务意图投影前退出，已persisted历史不再候选。已授权v4仅Lifecycle有界扫描补选真实known3/4、既有历史仍存在、指定两意图缺失的员工记录；最终复用v3完整历史证明及幂等意图，clear不重插，不回滚可靠事实或重新SDK。独验扩现双consumer节点覆盖观察提交后退出及新Worker恢复，正常辅助15保持原字节；v2/v3均无业务窗，不虚构通过。

Context [v4交接](../../tmp/agent-runner-evidence/m6-kf-context/v4-handoff.md)实际07:32:17 UTC冻结，仅Lifecycle新增已known员工缺意图的有界修复候选，其他455不变；末单源编译/import exit0，主控亲核456当前/13归档0。[最终定向CR](../../tmp/agent-runner-evidence/m6-kf-context/v4-cr.md)P0/P1/P2=0。开发[18983](../../tmp/agent-runner-evidence/m6-kf-context/v4-development-1-run-evidence.json)actualexit0、1 passed（8.34s），456/15首末/当前及日志hash主控亲核一致；末自有库/连接及列明进程0，未知旧库保留。独立[B2 46651](../../tmp/agent-runner-evidence/m6-kf-context/v4-formal-b2-run-evidence.json)actualexit1、5 passed/1 failed（65.96s），456/24首末不变；员工2（含真正已提交观察后的退出DI/新消费者恢复）、全scope撤回、真实时钟/配置锁、坏消息后继续均到末。winner node在测试查未注册模型marker处KeyError，正向接受winner已到、反向未到，不计通过；已授权仅该测试注册标识修正及单项复验，业务断言不放宽、5有效结果保留。旧accepted/真实排序与双DDL组仍待正式运行，不称本片已验收。

Context 后续正式窗口已完成：[winner 单项26976](../../tmp/agent-runner-evidence/m6-kf-context/v4-formal-b2r-run-evidence.json) actualexit0、1 passed（10.20s），456/19首末不变；仅测试注册原模型标识，严格零模型调用及反向 human winner 断言完整通过，原失败字节保留。首次辅助资源审计因输出前缀白名单未纳 b2r 失败，前缀修正后的实际审计0，区别于业务失败。[B3 76961](../../tmp/agent-runner-evidence/m6-kf-context/v4-formal-b3-run-evidence.json) actualexit0、3 passed（74.95s），456/29首末不变：真实新接受后仅移除新分类的旧数据形状，原文本读取/取消/计费及已知语音唯一识别/原费用 owner 均到末；未接受→queued→原模型无未来员工→公开终态→清历史后复用终态证明到末。清历史使用隔离PG fixture，未冒公共clear或实际平台发送；新增资源记录仅核真实分配的3个DB/进程root，原容器fixture teardown后均0。[DDL 34146](../../tmp/agent-runner-evidence/m6-kf-context/v4-formal-ddl-run-evidence.json) actualexit0、2 passed（15.66s），456/15首末不变；原升级器fresh/force/replay、NULL用户旧SID与入站事实保留、同物理attnums但错误语义主键/唯一键整体拒绝和水印不推进到末，原CHECK完整恢复；未执行旧v1反例。以上日志hash及456当前/13归档由主控亲核一致，自有资源审计0，未知旧库0连接保留。当前版独立正常回归另行补验，不把开发正常结果混为正式结果；最终清单未封前不标整个Context片完成。

**人工语境文本片验收通过（2026-10-03）**：[末正常80032](../../tmp/agent-runner-evidence/m6-kf-context/v4-formal-normal-run-evidence.json) actualexit0、1 passed（8.27s），456/15首末不变；当前v4原客户/员工历史、NULL用户旧SID、两个原历史读者员工映射assistant、四个稳定pending_adapter意图及重复处理原ID不变到末。正式13 unique（v1未受影响分类/SQL三项复用、v4其余十项，正常版本复验不加数）＋双DDL关联2；开发正常独立角色、setup失败及winner测试失败窗口均保留。v4单叶修复及此前审查继承后P0/P1/P2=0，末实际编译/import0。[最终hash记录](../../tmp/agent-runner-evidence/m6-kf-context/final-meta.json)456有限源码、13原字节归档、37实际测试辅助路径union由主控现场亲核一致；9实际测试窗＝开发3＋正式6，不声称一次跑全部union。末资源审计新库/连接/列明进程0，旧未知库0连接排除。解除该片源码冻结只允许后续明确授权变化；人工语音、媒体、真实recap、batch及投递继续后片，pending_adapter不等于真实效果已完成，M6a仍进行中。

**下一片实施授权：人工/员工语音（Voice10）**。按[原功能校准与具体口](../../tmp/agent-runner-evidence/m6-kf-context/next-voice-contract-addendum.md)新增context_voice仓储及编排两叶，修改原context仓储/worker、lifecycle、voice_media四叶，双DDL与表说明三叶，再增加既有存储调用方登记file_usage一叶；精确十叶不新造Runner/Attempt/计费平台。原人工识别没有AI执行/余额门，保余额0或负值可识别与原独立费用记录；新的可靠归属、物理started/known/unknown及同事务stable record/debit排重复。unknown显示原语音占位但pending_asr不消费完成、不发recap、不再POST；可信迟到known沿原operation核对并更新同一history ID，尊重clear/recall。全部历史投影复用公共SourceGET及末事务排序，已发生识别/费用不被新credit或historygate抹去。nativeflagoff只停新分类，原fixed分类的已接受本域工作继续准备与未started首次识别；worker stopping不新POST、未知不重发、真实资源排水。证据新目录m6-kf-context-voice，开发正常、独立测试和独立CR分别进行；当前是授权实施，尚无该片实际运行通过证据。人工媒体、真实recap和整渠道默认迁移仍后续门。

人工Voice [v1交接](../../tmp/agent-runner-evidence/m6-kf-context-voice/v1-handoff.md)准确10叶、459有限source见证，末6源编译/import actual0、实际loaded80都在闭包、defaultoff/pairedDO/diff0；原15:00 CHECK只接受精确旧/新两形以兼容完整force/replay，真实迁移待独验。开发[1027](../../tmp/agent-runner-evidence/m6-kf-context-voice/v1-development-1-run-evidence.json) actualexit0、1 passed（13.12s），459/17首末0，原Crypto/SDK/SpeechPOST客户员工两次、原读者/费用各一次/余额0扣负及重试不二次调用到末，主控亲核loghash。exact分配的库、进程/存储根和两音频在原容器审计0；首辅助审计pool未初始化失败另记，纠正后只读实际0，不改业务通过事实。[完整v1 CR](../../tmp/agent-runner-evidence/m6-kf-context-voice/v1-cr.md)P0/P1=0、P2=2：原CLI stop未及时传内层owner；双worker占位后零POST unwritten回media_ready可能漏复选。已在正常窗口结束后统一授权VoiceRepo/Lifecycle及必要第11叶AdmissionWorker修复，保v1字节，不新扩平台；修后只用真实CLI停止+原selector恢复的定向开发/独立验证，正常有效结果复用。人工Voice仍未验收通过。

人工Voice [v2交接](../../tmp/agent-runner-evidence/m6-kf-context-voice/v2-handoff.md)已统一三叶修复，owned11（新增必要原AdmissionWorker），有限source仍459、其余456同v1；主控亲核当前/归档0，末三源实际compile/import0。[定向CR](../../tmp/agent-runner-evidence/m6-kf-context-voice/v2-cr.md)P0/P1/P2=0：CLI与close统一stop同步传播，Context及候选SQL后不新接受；零POST撤回保原epoch/history并复选media_ready+pending_asr，真正started/unknown不重派，clear/recall保留。真实CLI停止及双consumer恢复开发验证已通过（88826，1 passed），正式批次中该节点亦通过；实际停止后零POST，新进程沿原selector恢复，同一历史/费用仅一次。正常v1不机械重跑，剩余风险合并相关批进行。

人工Voice首轮独立批量验收：[B1r实际结果](../../tmp/agent-runner-evidence/m6-kf-context-voice/v2-formal-b1r-run-evidence.json)5 passed、1 failed（81.22s）。已通过正常历史/费用、可信识别与明确失败、事务回滚、真实CLI恢复、双consumer并发。失败是G4测试提前接收下一消息，worker正确选中它，原unknown不可重派；只修测试接收时序，撤回/清空后迟到真实结果仍待复验。原失败日志及23辅助快照保留，459源码首末不变、CR无待修项；主控核对原日志hash与[六份自有资源末审计](../../tmp/agent-runner-evidence/m6-kf-context-voice/v2-formal-b1r-cleanup.json)，库/连接/根目录/音频及列明子进程均已闭合。余下顺序、媒体与双DDL验收待运行，人工Voice尚未整体验收，不将5项通过计作M6a完成。

**人工Voice11主控验收通过（2026-10-03）**：[最终独立报告](../../tmp/agent-runner-evidence/m6-kf-context-voice/final-inventory.md)正式业务8 unique＋关联4（双DDL2、旧Context及AIvoice各1）全部通过，开发2独列。最后G7r实际87578退出0（5.42s）；先前G4接收顺序/异步fixture及G7目录断言三次失败原字节、日志保留，仅测试修正，不改业务。停止途中已完成落盘的合法音频保留可复用，codec两次结束后原目录baseline不变、真实子进程已退出，fixture末全清；不冒生产零artifact。主控亲核当前459有限源、11生产归档、40当前辅助union及末实际日志hash一致，union非一次全跑；编译/import及v2独立CR无待修项结果复用。各实际窗口自有库/连接/目录/音频/列明子进程审计闭合，未知资源不动。释放本片冻结，进入已审查的KF完整收发整合；人工Voice通过不代表整渠道、真实平台发送/后台效果、容量或默认迁移完成。

**M6a 收口调整（2026-10-03）**：此前旧功能拆片过细、验证记录过重，整体微信客服仍未交付。人工Voice完成后，剩余功能集中到一个微信客服收发整合阶段，按完整用户流程验收，不再为每项旧业务另建执行子系统。复用原欢迎/场景注册/附件/命令/后台adapter；原2秒合并及客户/员工穿插、耐久Runner接单、真实平台结果发送与预算、转人工与服务中断恢复、已知送达或明确终止后会话释放是主闭环。微信客服只有聊天消息，不提供 Web 的暂停按钮；不将用户点击暂停列为本渠道功能或验收门槛。新增阻断项须说明真实消息或后台执行的可达路径，不以构造渠道不存在的操作扩大范围。正式检查只覆盖实际影响与真实回归，复用有效旧结果；不减少独立测试/CR或放宽断言。

Recap采用原常驻consumer/adapter薄交接，持久保存原intent/受信归属、实际领取/派发/返回或未知；void正常返回只表示dispatch_returned，不宣称线索已更新或外推已成功，派发未知不自动整轮重做。保原业务窗口、收费、冷却与配置，不重建Lead模型/HTTP结果owner平台。[此前Lead效果迁移候选](../../tmp/agent-runner-evidence/m6-kf-context/next-lead-refresh-handoff.md)只读保留供参考，本期不实施该扩展。人工Context当前pending_adapter仍待整合交接，不能提前称旧后台功能已接通。四入口安装能力最终集中于中性composition，Engine/Worker不散布平台分支。

**KF整合契约与转人工收缩（2026-10-03）**：[完整收发交接](../../tmp/agent-runner-evidence/m6-kf-completion/integration-handoff.md)及[独立方案审查](../../tmp/agent-runner-evidence/m6-kf-completion/integration-review.md)中的普通收发责任保持；此前新增的转人工专用自动恢复方案已由用户明确要求收缩并授权实施，本段取代相关旧完成许可、余额豁免与多层恢复约定。转人工是渠道接待状态切换：微信明确成功后保原会话信息和标记，本轮停止AI处理，原费用与记录收尾；渠道只在可信智能助手状态下提交消息，人工接待期间后续客户消息不得新接单、调用模型或回复机器人消息。明确转接失败保原失败行为，不授予停止指令或标记转接成功。

删除转人工专用 completion 执行许可、余额豁免与自动恢复分支，删除未证明真实生产入口的多层子任务递归重装；保留真实一层已完成子任务的原结果配对及中性 STOP_EXECUTION，核心不加入微信判断。域内精确同Runner/执行/调用的真实ACK只用于发送抑制、原业务收尾及claim证明，不作为新执行许可。微信成功而工具恢复点保存失败时，沿通用 interrupted/verification 保留原事实待核对，不重复转接，不自动恢复AI或宣称本轮已完成。Voice消息回复准备、晚到结果、Web/Browser/local与原通用控制的真实共用职责按实际依赖保留，禁止整文件回滚。

验收优先两条真实渠道流程：成功转人工后同路由再次收到真实客户消息，确认0新agent接单/模型调用/机器人回复；微信明确拒绝转接，确认不标人工并按原规则处理。不将手工调用恢复API、强制余额归零、精确Attempt或多层人工CP的组合测试反推为产品需求。其他合并/历史/发送未知/费用/租户边界保既有责任及受影响验证，复用有效结果，不再扩大执行子系统。真实部署仍另需当场授权。

**整合实施与验证分工（2026-10-03）**：批接单、完整结果查询、发送事实与原业务接线已落地；实际Admission普通双轮开发窗口63483退出0（1 passed，87.97s），470源码/25辅助首末一致，自有库/连接/目录/列明进程清理已核。该结果不代替最终独立验收。此前复杂转人工组合用例与多层Kernel准备仅AST检查，未实际运行、没有业务失败记录，不以其作为扩展实现依据。用户已授权执行转人工收缩；[精简差异](../../tmp/agent-runner-evidence/m6-kf-completion/transfer-simplification/delta.patch)已落地，11源编译/import通过，[独立CR](../../tmp/agent-runner-evidence/m6-kf-completion/transfer-simplification/cr.md)无待修项，470有限源中仅11变化、其余459及SDK未改。[最终独立验收](../../tmp/agent-runner-evidence/m6-kf-completion/transfer-simplification/formal-run-evidence.json)89784退出0（94.75s）：真实渠道成功/拒绝2项及必要核心关联3项全部通过；470源码/26辅助首末、当前及归档一致，日志hash核对一致，自有库/连接/目录/列明进程实际审计为零。开发28535两项通过（97.53s）另列。外部微信/模型为localhost替身，不冒真实客户或提供者实测。实施者先定向自测，测试者独立运行固定版本，主控只协调、检查证据与更新文档。SDK v4三叶未改时复用原作者与审查者分离的CR。暂停按钮不属于微信客服入口，不作本期验收项。两次开发失败原记录保留：6587为prices fixture缺注册、未进入业务；24297核心流程到末但无关客户只读GET计数错误，整体仍为失败。测试修正包括fixture注册、真实转接响应后即切人工的外部peer时点及移除无业务依据的只读次数断言，核心0接单/0模型/0回复、失败行为与费用断言未删，最终测试已由非作者独立审查。主控承认前提核对不足及频繁调整测试的验收纪律问题；后续需求断言须先核业务依据与独立审查，失败先定位报告，测试修改先提供依据及差异，禁止边跑边调整预期以求通过。本次只验收转人工收缩，M6a整体仍未完成。

**普通完整收发开发运行通过（2026-10-03）**：实际窗口54204，严格正常用例1项通过（67.81s）；原加密回调/拉取、注册及归因、欢迎一次、连续两轮2秒文本批，各一个原Runtime/模型及费用、SDK正文ACK、服务核发送事实后释放claim并接受下一轮、原LeadRefresh适配器两次返回及intent的dispatch_returned均实际到达。外部平台/模型为localhost替身，不冒真实客户发送或后台效果完成。主控亲核470源码及24辅助的首末/当前hash、日志hash一致。前一10530窗口欢迎断言失败来自测试将原账号欢迎配置误放顶层，仅该fixture修正，生产未迁就测试；原日志/版本及末资源审计保留。本次为逐段调用真实组件的开发自测，尚未覆盖实际Admission消费器的候选/持续编排；最终正常及风险流程须走原消费器，不以组件直调代替。第二窗自有DB/连接/进程目录及两个列明PID已独立末审闭合，释放生产冻结补原兼容口。本次不代替独立测试与最终CR，M6a仍进行中。

每个渠道相同的工作清单：

1. 将经过验证、去重与现有合并策略处理的输入提交 runner，记录平台消息与 runner 的对应关系。
2. 后台适配器查询或订阅完成结果并负责发送；后台消费本身可从已保存的 owner/游标/投递状态恢复，不能只依赖 HTTP 回调里的临时 task。
3. 保留输入合并、图片/文件、verbose 配额/限频及发送后锁释放边界；接单后的补充输入在原 Runner 安全点处理，不取消后从头重演已发生的操作。暂停不长期占物理 lease，但会话占有仍受控。
4. 区分执行完成与发送完成，沿用现有平台去重和失败规则；无法确认送达时不盲重发；只在原条件满足时触发 recap。
5. 消息和费用不能由旧入口与新 runner 双重写入；新的发送 owner 只负责协议回发与投递事实。

验收：目标渠道真实相关回归、并发合并/重跑、发送失败、媒体和计费通过；已接入与尚未接入渠道不串路径。各自具备单独开关和回退，不能一次切三个渠道。

### M6 有界只读盘点（2026-10-02）

目标入站分支在 `src/saas/api/channel_routes.py`，共享 `ChannelSessionManager` 当前将 Queue/Agent 执行、batch 历史、record.complete、发送、Redis lease 释放及发送成功后 recap 串在一起。迁移应从目标三个分支接 Runner 接单及耐久发送 consumer，复用现有 `make_send_response`/平台 adapter；不能在新终态提交后继续旧 `process_and_persist`、历史或 record 收尾，否则双写/双费。Runner 的 channel 终态只将原 claim 转到 delivery gate，发送 owner 另行按真实结果释放。

已有入站 `MessageDedup` 是 PostgreSQL 布尔去重，微信客服 cursor 是 Redis 30 天拉取游标，共有 `send_response` 仅有稳定 ID/bool；这些均不等于已有耐久 outbox 或发送 exactly-once。最小迁移必须持久保存入站 event→accepted Runner 绑定，以及发送状态/offset；平台 ACK 后不能仅依赖临时 background task。按平台来源/account/event 构造稳定 Runner key，服务短暂失败仍重试原 key，持久接受后才认已处理。必要 inbox/delivery 事实留在渠道 adapter 的现有 PG 消息/会话扩展或小表，不扩成通用通知框架；无法判定送达则等待核对，不盲重发。当前为盘点事实和后续门槛，渠道尚未迁移或验收。

| 当前责任/事实 | M6 迁移门槛 |
|---------------|-------------|
| SessionQueue 前置2秒合并，processing 中取消追加重跑，finalizing/responding 后切下一轮 | 明确接单前 cutoff、接单后持久补充意图和发送 cutoff；不能 cancel旧Runner 后新建merged Runner 重演已发生操作。在原Runner安全点纳入新意图并复用事实，发送cutoff后排下一轮；具体最小控制接口在M6开工另审，不改Web默认排队语义 |
| merged_segments/msgids 与附件历史 metadata，follower 不写 owner 历史 | 保留段级归属/去重，入站来源及消息ID由可信adapter导出，原接单input不覆盖 |
| 原客户/员工消息30分钟旧消息过滤、AI前隐藏命令、C1→S1→C2→S2穿插历史 | 默认迁移前分别验证：cursor重放不产生过期新回复；原命令仍在应用边界处理；员工角色与跨AI/context顺序沿原业务时序，不由独立轮询完成先后猜测 |
| 微信客服同批连续消息合并、同批 recall 剔除、跨批 mark_recalled_message/merged_segments、人工客服切分 | 回撤仅作用对应消息段，保留原历史/人工消息边界，不用新会话规避 |
| 微信客服先ASR，再建record/add_asr_usage；成功语音不再作为voice附件送Agent，历史仍存voice metadata，follower可有独立ASR费 | 每次真实ASR有稳定事实及唯一费用owner，不能合并只计一次或旧record与Runner双扣 |
| WeComKfReplyBudget=5 贯穿 verbose/final，目前为进程内预算 | 投递账本保存已消耗预算，恢复/重试不重置；transfer_to_human成功的final压制由可信delivery disposition表达，不解析公开rawtoolresult |
| make_send_response 平台render/send、final_delivery_id、images/files；send_ok后才recap | 原适配器继续呈现媒体；确定 delivered/suppressed 后收尾claim，unknown等待核对；recap保stable round_message_id/原队列、配置及trace，不随Engine完成提前触发 |
| 钉钉group路由已提取但旧get_or_create_session未传chat_id，飞书同样存在NULL chat会话；自动SaaS注册失败可合法user_id=NULL | 按可信provider/account/user/chat证明兼容绑定旧session，不能伪填route或新建丢历史；保当前平台actor授权，不伪造SaaS用户 |

本次有限入口准备：[飞书只读提案](../../tmp/agent-runner-evidence/m6-feishu/readonly-ingress-handoff.md)与[钉钉只读提案](../../tmp/agent-runner-evidence/m6-dingtalk/readonly-ingress-handoff.md)列出源码落点、身份/旧SID、ACK耐久及有限组合门，未实施或验收。钉钉现已强制timestamp签名但不覆盖body，不能发明平台不支持的body HMAC；已支持群回conversationId，与飞书当前actor回发不同。当前均未发现项目Stream接线，具体官方协议核验仍有限，不将SDK能力冒已接入功能。

可信来源补充预审：旧飞书普通事件只在提供 signature 且已配置 crypto 时校签，事件 token/app 归属未完整校验，challenge 成功不证明后续每条消息。M6 入站写 binding/inbox 前须沿配置的真实安全模式验证原 body、时间与账号归属，未校验 JSON 不能成为平台证明；长连接来源与实际 HTTP 回调分别核信任。旧自动注册按平台 ID 尾4匹配用户名，不能用其结果作为完整平台身份到 SaaS 用户的授权证明，NULL 用户仍合法。

完整路由采用渠道 adapter 内最小持久绑定，必要新增 `channel_session_routes`：tenant/source/config/provider-account/full actor/chat-kind/chat/profile 对原 session，唯一完整 route；保存非敏感事件/配置版本证明，不存密钥或原鉴权头。已有多群或多配置共用的 legacy session 可有多个显式 legacy_shared route 保原历史与 claim，不能回填当前 callback 成单群自证；新会话才按完整 route 唯一。Runner 由仓储读取当前配置归属/可用与绑定，接单键纳配置/账号完整作用域，Engine 不处理渠道。具体 DDL 和已接受旧行版本兼容在 M6 开工审查，不在 M4 先扩表。

微信客服后续有限只读核对：现callback只有encrypt且crypto存在才校签/解密，明文分支原XML直通；M6新inbox/route须采用该配置真实认证模式，URL tenant及payload不能自证可信。原KF会话按full external_userid/tenant/profile/open_kfid保持绑定，open_kfid为客服账号，route chat_kind明确kf_direct，另纳config/corp账号；旧不同配置重合session显式legacy_shared，不换会话或伪造SaaSuser。当前cursor仅open_kfid/Redis30天，最小PG保存原同步页及account cursor水位后才推进，Redis只投影；先布尔dedup再临时后台执行不等于耐久接受。

发送侧实源补充：adapter.current_open_kfid为共享可变字段，delivery必须捕获可信完整route；api_client.send_msg body未透传上层stable message_id，不能靠内部ID宣称平台幂等，响应缺失不能默认errcode0当ACK。每正文/资产ordinal保存原intent/started/显式ACK或reject/unknown及平台ref，未知不自动重发；原进程内预算改持久尝试/ACK/未知消耗。ASR每原voice/follower有唯一事实/收费owner；转人工抑制记suppressed_transfer，保原人工期消息落库与语音费；仅确认投递或明确抑制后释放delivery claim并按原roundid一次recap。渠道所有规则仍归adapter，Engine不处理。

官方接口当前核验未完成：审查者只读尝试微信客服官方发送/状态/读取页面均读取失败，未用第三方镜像替代。上述代码事实已核，不将源码中的窗口/数量/保留期注释冒当前官方条款；M6实际实施前用可访问官方入口核定接口幂等、错误/ACK语义、回复限额及回调认证。当前仅后续准备，无渠道源码改动、平台发送或部署。

主控本次补查官方developer.work.weixin.qq.com的94677/94670页面，web读取仍失败；CUA打开94677被浏览器site-safety明确拒绝，未经过auto-review。已停止该浏览器动作，不用其他浏览器、代理或原始下载绕过。官方条款保留待核验，当前M5不依赖该页面。

渠道Browser边界已只读核实：原工具装配不按渠道禁Browser，自主执行取决于实际profile允许及真实tenant/SaaSuser身份；旧automation_tool同样拒绝NULL执行身份，不将此称为Runner新增回归。旧HumanControl固定server_web，渠道callback未发送required卡片或可信URL，旧续接仅写Web历史/SSE，未找到微信客服/飞书/钉钉人工Browser完整闭环。保留原自主能力及现有授权边界，不为未有闭环新建OAuth/门户；共享Browser有效结果按受影响范围复用。普通主任务追问沿原模型回复/下一轮聊天语义，只有原真实子澄清或已有工具等待才关联等待事实；普通平台文本不能冒可信Browser completion。此项不是微信新增人工交互验收门。

## 10. M7：综合验收与回退

### M7 性能整改①：消除每执行全量资源装配（2026-10-04）

**根因**（本地容器实测，全新会话单条消息 22.0s，其中模型仅 2.1s）：worker 每次执行全量重建资源——`profiles.resolve` 与 `RuntimeResources` 各建一个 SubagentRegistry（磁盘 14+DB 6 各一遍）、SkillRegistry 每次加载 25 个 skill 并逐 skill 执行建表 DDL（打在远程库）、5 个工具路由各自懒建 LLMGateway/KeyPool、Redis 降级客户端每执行新建数十个。

**改动**（7 文件，生产代码）：
- 新增 `src/services/agent_runner/runtime/resource_cache.py`：进程级缓存——SkillRegistry 按 allowed 集合缓存一次（建表 DDL 随之只跑一次）；内置 SubagentRegistry 按目录缓存；DB overlay 注册表用 `SELECT COUNT(*), MAX(updated_at) FROM subagent_definitions WHERE status='active'` 一条廉价签名查询决定是否刷新（替代全量 load_from_db，保证 API/worker 两进程指纹不与 DB 分叉）。
- `runtime/profile.py`、`profiles.py` 接入缓存；excel/pdf/word/ppt 路由与 knowledge service 懒建网关改用共享 `llm_gateway` 单例。
- `src/channels/__init__.py` 改 PEP 562 惰性导入（manager/session/callback 按需加载）：修复 M6a 遗留的 bootstrap 边界违规——`application_sources` 导入 wecom_kf 子包时经包 `__init__` 拉起整个遗留渠道栈，`test_actual_bootstrap_and_nonmain_profile_import_start_no_legacy_execution_services` 因此从失败转绿。

**隔离边界**：只缓存配置形态资源（定义/连接）；MemoryManager、PlanManager、执行身份等单次 runner 状态不进缓存。RuntimeResources 的子代理注册表保持仅内置定义（不含 DB overlay）的既有行为——与旧共享 Agent 可委派 DB 定义子代理存在功能差异，登记为 M7 待裁决项（本次不悄悄改变委派面）。

**效果**（本地容器真实消息实测）：新会话 22.0s → 6.9s（含重启后首次缓存构建）/ 4.1s（暖态）；装配段 15.5s → ~1-2s。

**验证**：test_api+test_worker+test_agent_loop+test_session_queue 66 通过；test_worker_resources/test_worker_skills/test_resources/test_agent_engine_acceptance/test_tenant_skill_cache 54 通过。开发中自查修复一处自有缺陷（签名查询按 RealDictCursor 列名取值）。独立测试与独立 CR 另行记录。

**遗留**：① `tests/unit/channels/test_wecom_kf_adapter.py` 31 个既有失败（M6a 适配器 native owner 语义 vs 旧单测预期，对照实验证明与本次改动无关），待按测试完整性协议分类处理；② 每执行残留一次 0-skill 租户技能合并扫描与 ~14 个内存 Redis 降级客户端（亚秒级，暂不动）；③ 大会话压缩阈值策略（0.7×256k=179k 过高）待产品决策，见接手收尾记录前的性能分析。

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

### M7 整改⑤：KF 拉取路径 SDK client 有界复用与外调收口（2026-10-05）

落实本节门槛「微信客服首片采用每页独立SDK client……M7须测量首gettoken、账号核对与sync实际外调/吞吐及恢复成本，收口有界复用和配置失效策略」。

**测量结论（回环假平台基准，真实 WeComKfApiClient，60 页，脚本未入仓库）**：现状每页固定 3 次外调（gettoken+account/list+sync_msg），60 页共 180 次、60 个 TCP 连接（ingress_worker 每页 `_pull` 内新建 client、页末 finally 关闭，token 缓存仅页内）。按 (account_id, config_version) 键控复用 client+token 后同键页降至 1.03 次外调/页（62 次、1 连接），复用页跳过重复账号核对后页均往返 -66%；RTT=30ms 时吞吐 5.0→12.6 pps（2.5×）。绝对吞吐含回环 ~40ms delayed-ACK 伪影且无 TLS/真实 RTT，不可外推真机承载量；外调数与连接数比值为精确值。token 被外部轮换（200+42001）时现有 api_client 40014/42001 失效重取路径自动恢复：+1 gettoken+1 拒绝重试，额外 143ms（RTT=0）/222ms（RTT=30ms），无需新恢复逻辑。官方约束（91039/90312）：access_token 有效期 7200s、不能频繁 gettoken（频率拦截）、企业可能使其提前失效；基础频率每企业单 cgi/api ≤1万次/分。

**实现**：新增渠道层 `src/channels/wecom_kf/client_pool.py`（KfClientPool）；`ingress_worker._pull` 改为 acquire/release——条目键=(account_id, proof.config_version)，acquire 时硬校验 corp_id+secret 一致才复用（兜住「secret 变了 updated_at 未变」窗口；claim 侧 `_account_matches(require_version=False)` 不拦这种窗口），新条目首用仍做完整 `_verify_account`，成功后随键缓存账号核对结论。失效策略：键不符/凭据不一致、页失败（含 errcode=-1 HTTP 级失败——api_client 该分支不失效 token，由池整体弃条目补上）、容量上限（`client_pool_capacity` 默认 16，超出先逐空闲 LRU、全忙时仅从池表剥离由其页持有者释放时关闭，池表大小即边界）、空闲 TTL（`client_pool_idle_seconds` 默认 300s，acquire/release 时清扫）、worker.close 全量弃并 close。声明口径为**进程内有界缓存**（非跨进程、非持久，不做跨进程 token 共享以避免 token 明文入 Redis；多副本部署各进程独立持池各自 gettoken，40014/42001 恢复路径已实测覆盖，部署形态说明留 M7 综合验收）。Engine 零触碰，client_factory seam 保持兼容。

**验证**：新增 `tests/integration/agent_runner_service/test_kf_ingress_client_pool.py`（池机制 7 项：同键复用/首用核验一次、页失败弃条目、同键 secret 轮换硬校验拒绝复用、容量与空闲 TTL 有界、全忙溢出剥离不关在用、close 全弃+secret 不入 repr+关闭后 acquire 拒绝、配置默认值与非法值；真实回环 2 项：同 worker 跨页复用后外调序列 [gettoken,account/list,sync,sync,sync,gettoken,account/list,sync] 与失败后冷重启、平台侧删号首错移至 sync_msg 且冷启动重验）。`test_kf_ingress_http_bounds.py:63-64` 断言按池语义改写（原断言即首片「每页新 client」行为，证据为改动前 ingress_worker.py:74/:88-91；本流三个页均失败，失败页弃条目后数值不变、语义更新为「失败页各持有唯一已关闭 client 且池零残留」）；`test_kf_ingress_foundation.py` 单页冷启动序列实测不变未改。定向 `dev_test.sh --isolated-db`（client_pool+foundation+http_bounds+lease_and_routes）13 通过；关联（transactions+config+sdk_legacy_association，transactions 覆盖同 worker 双成功页复用路径）5 通过。

### M7 十项整改与容量测量增量登记（2026-10-05）

十项整改每项一行（改——/验——/遗——），各项均含独立复核记录；完整明细、验收发现与待授权事项见 `out/m7-report.md`。

- **legacy-tests（31 个微信客服旧单测分类）**：改——31 个失败全归类测试过时（fixture 缺陷）：裸 MagicMock 使 `native_write_enabled` 恒真，30 例误入 `adapter.py:131` native owner 守卫、1 例经 `:590-591` 真值分支重抛；仅在 fixture 补 `a.api_client.native_write_enabled = False`（真实语义=api_client.py:105-107 property，未绑定 enable_native_writes 恒 False），断言与生产代码零改动；验——复核 diff 仅 fixture 一个 hunk（test_wecom_kf_adapter.py:34-38），实跑该文件 38 passed；遗——无阻塞（mock 真值陷阱归因成立，测试修复与生产语义一致）。
- **compression-cap（压缩阈值绝对上限）**：改——settings.py:268 新增 `token_threshold_absolute=60000`（0/负禁用）+config.yaml:169 同步；mid_term.py:516-531 抽 `_effective_token_threshold=min(比例,绝对)`，三消费点统一（:601 阈值、:1769 后台扫描、:1498-1499 经济闸门 near_limit 改 0.8×有效阈值防 60k 封顶被误降级硬截断），新增 8 用例；验——grep 比例公式仅存 helper（:527）无分叉，实跑 test_check_threshold.py 19 passed，create_settings 实测 absolute=60000 生效、旧配置缺键可启动；遗——diff 四文件混有他人未提交 hunk，本轮仅评审阈值相关改动。
- **submit-failure-ui（前端提交失败明确提示）**：改——POST /api/chat/runners 失败给乐观消息挂内存态 submitFailure（definitive 403/4xx=错误态+重试钮、网络/5xx=自动重试），🚀 progress 行原地替换文案，重试复用原 client_request_id 幂等重放，本轮补 retryRunnerSubmit 互斥 guard（useAgent.ts:352-354）+竞态回归测试；restoreInput 复用 ChatContainer.vue:538-541 既有消费零改动；后端零改动；验——3 测试文件 47/47+vue-tsc 0 错+build 通过（前端构建退出码 0）；遗——state.submitSelecting=false（useAgent.ts:356）恒 false 冗余赋值，防御性无害。
- **fk-migration（controls FK 与库规则收口）**：改——init-postgres.sql:3070 撤 REFERENCES；db_update.yaml:3410 增量块 2026-10-04 02:00:00（to_regclass 守卫+contype='f' 全删，幂等）；4 处 fixture controls 先删；迁移测试断言按 datetime 定位（改动系接手前已落盘，本轮逐点核验+复跑）；验——应用层无 FK 级联依赖（src/ 无 DELETE FROM agent_runners，lock_owned_runner→ownership.py:26-29 FOR UPDATE，INSERT 均在同事务其后）；三迁移/存储测试实跑 20 passed；遗——无（历史 2026-10-02 块 append-only 保留正确，范围外 FK 未触碰）。
- **auth-boundary（共享登录校验收口中性认证服务）**：改——verify_token/_verify_qb_token/fresh_web_subject 三原语落 src/services/auth_service.py（AST 对比与原 api/auth.py 逐行一致，缓存/auto_refresh/租期语义不动）；api/auth.py 再导出保可 patch；web_subject 薄适配同状态码；RunnerAuthorizer 懒导入换源；BrowserWebAuth 直连转 USER_UNAUTHORIZED(401)；agent_runner 包认证链路零 src.api 引用（web_sync.py:16 惰性导入为应用边缘刻意保留）（改动系前序会话落盘，本轮核对+自测）；验——AST verbatim identical=True，7 定向测试实跑 137 passed（253.12s），缓存命中后仍权威行二验 fail-closed；遗——原 patch src.api.auth.get_db_connection/get_cached 拦截内部的测试会失效（唯一实例已登记 P2-1，不阻塞）。
- **adapter-version（adapter 契约版本）**：改/验——机制详见本节「M7 整改④」，独立复核逐点核实（常量/写点唯一 executor.py:294/coordinator 门先于 fingerprint/篡改矩阵），复核实跑 40 passed 1 skip（438.49s，skip 为既有）；遗——无 P0/P1，checkpoint_version 死列不启用。
- **checkpoint-refs（图片可信引用+持久化字节上限）**：改——durable 装配点只存 runner_image 引用（登记 resources.image_artifacts、sha256 幂等落 owner workspace），ModelAdapter._resolve_image_references 按包含性+登记+尺寸/哈希复查深拷贝装配 wire、不回写 state；AgentRunnerLimitsConfig（request 2M/checkpoint 8M/snapshot 2M+env 覆盖）入口 413 闸（幂等重放不受限）+capped dumps 超限走既有失败终态；验——复核 grep 写点 checkpoint 17/snapshot 12 处全覆盖，定向实跑 44+4+31 passed（开发者全量 9 文件 136 passed）；遗——usage_repository.py:60 计费列不在范围未改；cleanup/resolve symlink 检查不对称由哈希复查兜底。
- **kf-sdk-reuse（KF SDK client 有界复用）**：改/验——详见本节「M7 整改⑤」，复核逐项成立（硬校验兜 secret 轮换窗口、容量溢出 detach 闭环、secret 不入 repr、_pull finally release 无泄漏），定向实跑 13 passed+关联 4 passed；遗——admission_service 1 例稳定失败系前片既有环境依赖（ContextWorker 真实 BASE_URL 外调必败 40013），本片未触碰未修，待环境侧收口。
- **legacy-web-compat（旧页面迁移桥测试基建）**：改——dev_test.sh 仅容器模式+显式 -m real_browser 时自动起一次性 sidecar aid-test-browser-redis（参数与 browser_io.py:91-95 断言一致，ping 探测复用外部 helper 不接管清理，退出码保真），普通路径不变，生产代码与断言零改动，不重启任何既有容器；验——两条门禁路径端到端实测（-m real_browser → exit=4 透传+无容器残留；无 -m → 零供给日志），主容器全程 Up 未动；遗——shellcheck 不可用未跑，完整套件以门禁路径验证替代复跑。
- **deploy-config（分进程部署配置准备）**：改——新增 docker-compose.agent-runner.yml（同镜像 runner-api/runner-worker/kf-ingress/kf-admission，KF 由 wecom-kf profile 门控+enabled 注入防重启风暴，healthcheck+depends_on，四服务无 ports）与 deploy/agent-runner-deploy-checklist.md（迁移前置/旧主容器补 env 必检/共库警告/peer 令牌/启动顺序与回退红线/Browser owner 拓扑），DEPLOYMENT.md 纯增量 16 行；验——compose config 四形态+渲染抽查+yaml 解析+定向 57 passed（244.33s），引用逐条核实无虚引；遗——未部署未启动任何容器（仅客户端解析），pgrep 判活局限已标注，部署待授权。
- **容量测量（落实本节测量门槛）**：本地容器真实 API/worker+假 LLM（延迟≈0）+一次性隔离库（脚本在 /tmp 未入仓）：吞吐 1.30 runner/s（concurrency=4 打满）、每执行占槽≈3.1s 纯机器开销、≈60 次远程 DB 事务/执行（瓶颈在事务数而非并发上限，真实承载≈4/(模型时长+3.1s)）；空闲轮询 3.8 语句/s；checkpoint 逻辑 15.6KB/落盘 7.4KB（上限 8MB 余量充足）；真实 cost12 bcrypt 173ms/验（fixture cost4 相差 258×，verify_service authorization.py:24 无缓存，单进程串行≈5.8 验/s）；结论——模型延迟 0+远程测试库+单轮尺寸，不可外推生产承载，建议 service token 校验加进程内缓存后真机复测。
- **验收发现 1 条（low/unconfirmed）**：微信客服尾批 1 例失败（test_actual_crypto_business_batch...[pre-sales]）为共享测试库 124.222.3.254:5433 批次中途瞬时 TCP connection refused，单跑同命令 1 passed（89.43s）复验通过，非代码回归、未修改文件；与 M6a 已登记隔离库高负载抖动遗留基建项同源，建议核实实例容量/连接池。

**待用户授权事项**（未授权不执行）：① 提交代码（全部改动仍在 master 工作区未提交）；② agent3 部署（overlay 与清单已备好，含 db_update.yaml 迁移前置与旧主 API 容器补 env 并 recreate）；③ 默认入口切换（默认 Web 切 Runner，须先过本节旧页面 /api/chat/stream 并行执行协调门槛，当前默认未切）；④ 真实渠道外网验收（微信客服真实 corp 凭据/公网回调/token 轮换，本地均为回环假平台基准）。

验收覆盖四入口并发、跨 tenant/会话隔离、慢/断线观察、任务排队、暂停/继续竞争、存储不可用、旧 worker 迟到、真实模型/工具、Runtime/BOSS 兼容及产物访问。

- 测量执行并发、队列上限、轮询读取量、事件写入量、checkpoint/输出尺寸及保留容量，按结果调整配置，不宣称未经压测的承载数量。
- 实际源码已确认图片 data URL 被放入 state.messages 后进入 asdict checkpoint，旧“仅内存”注释不符；M7 应将持久消息改为可信产物引用，ModelAdapter 按 owner 读取并装配 provider wire。单请求/控制输入、checkpoint/公开快照总字节及队列拒绝背压须有配置与实测，不能只凭图片单张上限或执行并发认为持久体量已受控。
- 当前配置 fingerprint 覆盖 profile/settings/prompts，工具及恢复适配器没有独立兼容契约版本。M7 核定派发时持久的有界 adapter 契约版本及兼容策略，未知版本拒恢复；不能用全源码 hash 让文案/格式修改导致所有任务不可继续，也不能默默用改变语义的新适配器执行旧阶段。
- 内部服务凭据与登录校验的 CPU/数据库成本按实际部署参数测量。M2a 的 101 请求功能 fixture 为提速使用随机凭据的 bcrypt cost 4，不是生产鉴权开销或容量证据；M7 不沿用该低成本参数作承载结论。
- 微信客服首片采用每页独立SDK client，仅页内复用token/连接，并在同步前核账号。M7须测量首gettoken、账号核对与sync实际外调/吞吐及恢复成本，收口有界复用和配置失效策略；不能将短生命周期页内缓存称为已保原跨页长期缓存，也不以低成本fixture宣称平台承载量。
- 最终依赖复核须收口共享登录校验的层次：当前RunnerAuthorizer默认懒加载api.auth.verify_token，BrowserWebAuth复用api.web_subject，又加载登录路由模块；现`src/services/auth_service.py`自身亦顶层反向导入api.auth的管理员校验函数，不能简单迁入该文件就称服务边界中性。现api包已惰性加载且独立进程启动成立，不称启动故障；共享token/current-subject校验应移至现有认证服务边界，由Web薄适配转HTTP错误，Runner及Browser应用只依赖中性身份结果/错误。保持原缓存、auto_refresh=False、真实token行二验及角色/owned-session规则，不借此重写登录/SMS或加另一套认证；正式调整后重验受影响身份与启动边界。
- 配置与停止清理有界：服务停止接单后按策略安全停在恢复点；异常退出明确中断；测试产生的容器、卷和文件用命名清单清理，不动 master/Yohar 资源。
- 独立 API/worker 已有各自 `bootstrap`/`worker` 可运行入口，但当前部署 compose 未接 AgentRunner。M7补同一代码制品下分进程的部署配置、共享数据库/产物路径及 Browser owner 拓扑说明；只准备配置与隔离启动验证，不自行启动或更新用户环境。
- 清理 schema 时核对已有 `agent_runner_controls.runner_id` 的 SQL FK/ON DELETE CASCADE：它与当前“引用完整性由 Python 检查”的数据库规则不一致。以新增量迁移同步 fresh DDL 收口，先验原应用写口完整性与同事务有序清理；不能让终态 root 删除隐式清掉仍需核对的控制或投递事实。
- 回退只影响新提交的路由，已经被接受的 runner 仍由 AgentRunner 排空、暂停或显式取消；不能关闭服务后把未完成任务直接交旧 Agent 从头重跑。
- 新表和已接单任务回退时保留，不做破坏性降级。入口开关不能让同一请求同时进入两条路径。
- 默认Web切换还须处理旧已打开页面：现旧前端仍直接POST `/api/chat/stream`，该接口仍执行legacy反馈流程，仅同步JSON入口显式Runner header转接。新页面走Runner时不能让旧页面在同一现有会话绕过持久claim并行执行；按实际调用者和可信认证边界提供原协议兼容转接或等价占有协调，不能依靠客户端自报header或强制用户刷新作为完整迁移证明。当前默认未切，属于源码发现的M7门槛，非已验证线上故障；未迁移的D1/Runtime/其他入口须保其授权范围。
- 旧缓存Browser卡片也属于上述透明迁移：HumanAssistanceCard成功complete及初次挂载会轮询原continuation events，MessageItem把response直接append。原随机bac目前仅Redis/事件展示，native PG原wait未持稳定映射；终态清Redis后不能依赖Redis或最后一个presentation event辨认归属。M7将原bac→Runner/execution/call/wait/assistance同native mandatory wait持久关联（原私有JSON或稳定资源事实可用，独立于completion相位且每新wait保原映射），原events薄读桥复验当前owner/selectedtenant并返回稳定未投递文本增量及停止事件，不入旧jobs/newAgent。不得把每次累计全文给旧append消费者，或新页replace同时又append。验收包括旧缓存卡片真实接管/完成/poll、终态clear后补读、断线游标/重复完成/第二wait及Stop；当前人控服务片不提前扩大旧events实现。
- 旧Stop仅取消旧SSE manager，服务端转接stream后也须按fresh主体/原Web会话取消其activeRunner，不能返回假成功。默认切换前协调已在运行的legacy owner，原SSE断线同样detach；兼容stream只转换安全公开投影，不再执行/写历史/结费用。旧payload无稳定请求键，不能按正文hash合并两次同文案发送；明确每HTTP提交键，现代稳定键保原。验收旧已打开页与新页同SID并发Send/Stop/断线、单执行/历史/费用及未迁移CLI/D1保持。仓库未发现CLI/Runtime直接调用stream只属有限检索，不据此推断仓库外客户不存在；按fresh登录、实际ownedWebSession及服务端迁移策略分流，不按User-Agent猜入口。
- 未迁移入口保留旧方法；Runner 跨进程可启动不是生产部署完成，真机部署和真实外部写操作按用户授权执行。

## 11. 验证、交付与进度记录

实施已启动。以下命令由独立测试/实施角色按阶段执行，主控审查证据；新增测试路径在创建后列入完成记录，不将计划命令标为通过，不重启 master 容器：

- 后端定向测试默认使用 `bash ./scripts/dev_test.sh --isolated-db <本阶段测试路径> -p no:cacheprovider -q`。M0 独立测试发现全局 fixtures 即使 unit 也会清理测试命名租户，不能直接运行在共享业务库。
- 真实数据库竞争/迁移：使用 `bash ./scripts/dev_test.sh --isolated-db <测试路径> -p no:cacheprovider -q` 或经核对的隔离测试环境；不在共享业务库故障注入。
- 前端：`cd frontend` 后运行相应 `npm test -- <定向路径>` 与 `npm run build`。
- Runtime/Provider 未修改源码时优先相关协议/代理回归；若修改对应客户端，按其现有 npm 脚本验证，不自动重新打包客户版本。
- 每个高风险代码阶段完成后安排独立测试和独立 CR，记录真实命令/退出状态、环境、问题与未验证项；修复后只重跑受影响检查。

首次验证确认现有容器挂载当前工作区、解释器和依赖。import/启动检查在隔离服务中执行，不触发生产调度。相同代码与环境的有效结果复用，不机械全量回归。

阶段只有在对应验收完成后标为完成；环境受限的真实链路保持待验收。详情与证据写在本计划阶段记录，ideas 保持一行索引。没有“提交代码”授权不提交/推送，没有明确部署指令不部署。
