# 飞书、钉钉 AgentRunner 接入设计、计划与代码审核

## 第三轮复审（2026-10-09，最新结论）

**代码与接入设计审核通过：原 R1～R4 已关闭，本轮未发现新增 P0/P1/P2。** 有一项不阻断代码审核的文档边界说明建议；部署和两个渠道的真机验收仍未完成。下文前两轮结论均为历史记录。

本轮基线仍为 `master` / HEAD `96521334572b25c9b8f1c8064bef760aed17748f` 及当前未提交改动。重点审查更新后的两份实施计划、新增真实接线与 worker 执行测试，并复核接入实现。审查者仅更新本报告，未修改业务代码、测试或开发者计划，未提交或部署。

### 原意见关闭依据

| 原意见 | 本轮状态 | 依据 |
|---|---|---|
| R1 / 失败被误当成功交付 | ✅ 关闭 | 既有 error 分流修复保持；真实入口 + HTTP/ASGI 组合覆盖执行失败、身份拒绝、结果读取失败，断言非空失败提示、不写成功历史、不触发 recap、不重复提交 |
| R2 / 渠道用户上下文退化 | ✅ 关闭 | 原 helper 覆盖保留；新增每渠道实际 `RunnerWorker.execute` 用例，确认 Runtime 收到 nickname、tenant、phone、channel_type/channel_user_id |
| R3 / 零用量入口记录重复 | ✅ 关闭 | 两入口继续设置 skip_save；真实 SessionRecordManager 接线用例验证入口收尾及失败标记，没有重新引入入口空账单 |
| R4 / 测试和计划不一致 | ✅ 关闭 | 新增接线 10 项、worker 执行 2 项；两计划补齐基线/现状区分、error/用户/费用责任、Phase 4a/4b、测试命令与验收限制，旧证据保留为历史 |

接入仍遵循架构 §8.1 和已验收 KF 模式：保留原回调、用户注册、隐藏命令、会话队列及渠道渲染发送；由真实薄客户端调用共用 Runner 服务。渠道侧单独负责 channel_messages，Runner 负责执行费用；没有增加渠道专属 gate、inbox、消费者、容器或本地执行循环。

新增接线测试没有替换整个 ChannelRunnerAgent：真实后台入口、薄客户端、HTTP/ASGI API、RunnerManager/Authorizer、channel-result 脱敏和 process_and_persist 均执行。成功用例还验证工具消息、图片及钉钉单聊/群聊回复方向。worker 测试调用真实 execute，验证有效 verbose 配置传到 Runtime，verbose 事件经包装投递 observer，执行进入 stage/finalize 收尾；被测用户与配置 helper 本身未被 patch。

### 不阻断审核的文档建议（P3）

两计划「验证记录」的真实/假件边界段（当前第 69 行）写“桩仅落在平台 adapter、内存 DB 行、session_queue 契约镜像……与环境配置”，范围过窄。新增测试文件已诚实注明 MemoryRunners 预制终态，以及 worker 测试的记录型 Runtime、执行仓库、finalizer 和 trace IO 假件。建议将这些同步到计划，避免读者把 execute 的调用及收尾验证理解成真实引擎、真实 finalizer 或实库结算验证；不需要为此扩大实现或重写测试。

### 本轮独立验证

| 验证 | 实际结果 | 边界 |
|---|---|---|
| 计划所列七文件定向命令 | 84 passed、54 skipped，退出 0 | 接线 10 + worker 2 + bridge 59 + KF 交付 13 实际执行；入口 28 + 两回调 26 被 integration PostgreSQL autouse 跳过 |
| 上述 54 项隔离执行 | 54 passed、0 skipped，退出 0 | 同一未修改测试复制至临时目录，手动加载根 conftest 导入隔离，以 confcutdir 绕过不必要的实库 fixture；执行后删除副本 |
| 本任务 diff 空白检查 | 通过 | 定向 `git diff --check`；未对并行任务改动作结论 |

标准命令：

```bash
bash ./scripts/dev_test.sh tests/unit/channels/test_channel_agent_runner_wiring.py tests/unit/services/agent_runner/test_worker_execution.py tests/unit/channels/test_channel_runner_service_independent.py tests/integration/test_channel_agent_runner_entries.py tests/integration/test_feishu_routes.py tests/integration/test_dingtalk_routes.py tests/integration/test_wecom_kf_reply_delivery.py -p no:cacheprovider -q
```

本轮共实际执行并通过 **138 个不同用例**；不能将此写成本机标准命令一次得到 138 passed。审查环境为 Windows/Git Bash、Python 3.12、无 Docker；业务/日志 DB 强制指向本机不可连接测试地址，Redis 关闭，没有访问真实租户数据。标准命令的跳过数量取决于 PostgreSQL fixture；与开发者记载的环境结果分别记录，不覆盖其历史记录。

开发者计划另记载大范围回归 `1130 passed / 7 skipped / 2 failed` 及环境归因；本轮未独立重跑该套件或确认两个失败的归因，不将其表述为全量测试通过，也不把它用作本轮通过依据。前轮共用 session/verbose 回归记录保留，不与本轮重复计数。

未验证真实 Runner lifespan、真实 LLM/Runtime 引擎、真实 PostgreSQL finalizer/费用结算、Redis 跨进程竞争或飞书/钉钉平台投递。代码审核通过后仍需两个渠道各自完成部署验收：可信来源授权、对话和工具历史、追问、新消息取消/取代旧任务、钉钉单聊/群聊目标、失败提示及账单单一归属。KF 已验收不能替代这些验收；本轮没有部署授权，也没有发起部署。

## 第二轮复审（2026-10-09，历史）

**该轮结论：R1、R2、R3 的实现修复通过；该轮未发现新增 P0/P1。R4 部分完成，仍有测试与计划收尾项，不能将四项全部标为已关闭。** 下文第一轮结论为历史记录，不代表该轮仍有原 P1 阻断。

本轮基线仍为 `master` / HEAD `96521334572b25c9b8f1c8064bef760aed17748f`，包含当前未提交修复及新增 `tests/integration/test_channel_agent_runner_entries.py`。审查只新增本报告内容，未修改业务实现、测试或开发者计划，未提交或部署。

### 原意见复核

| 原意见 | 本轮状态 | 复核依据 |
|---|---|---|
| R1 / Runner 失败被误当成功交付 | ✅ 实现修复通过 | `session.py:1162` 显式分流 error，关闭 verbose、mark_error、发送非空提示、返回 error；不写空 assistant、不调用 complete、不触发成功 recap。真实 HTTP/ASGI 接线复现确认两个渠道执行失败、身份拒绝及历史读取失败均沿正确失败分支 |
| R2 / 渠道用户上下文退化 | ✅ 实现修复通过 | worker `_agent_user` 渠道分支优先 nickname，并恢复 source/channel_user_id；execute 从已授权 Runner 的 source 和持久输入传入，Web 构造行为保持；新增用户构造用例通过 |
| R3 / 零用量入口记录重复 | ✅ 实现修复通过 | 两后台入口 start_record 后设置 skip_save=True，end_record 仍收尾；无入口独立用量时不新增空账单，与 KF 账本归属一致；入口测试及实际组合调用确认 skip_save |
| R4 / 验证和计划与代码不一致 | 🔧 部分完成 | 取消检查、任务取代、协程取消及主/嵌套追问已扩展到三渠道；新增 28 个入口/用户/verbose 配置用例。但计划未更新，仓库内入口测试仍替换整个 Runner adapter，未固化本轮真实接线验证 |

架构边界仍符合既定第三方渠道模式。本轮没有要求更改原会话切分、飞书回复方向或媒体能力，也没有引入新的渠道执行/投递体系。

### R4 剩余收尾要求（P2）

1. **同步两份计划的设计及验证事实。** `implementation_plan.md:16` 仍是旧的“45+4”完成记录；第 21～27 行仍以“当前代码”描述进程内 Agent/llm；费用设计仍写“渠道 SessionRecord 保留入口记录”，未说明无独立用量时 skip_save；共享缺口清单也未登记本轮 error 分流和用户构造修复。请明确标为接入前基线，更新当前设计、修复记录、实际测试命令/环境/数量及剩余真机验收范围。保留原证据历史，不用本轮数字冒充旧轮执行结果。

2. **把组合验证固化，清楚区分 helper 测试与实际执行测试。** 新入口测试 `test_channel_agent_runner_entries.py:131～147` 仍以 FakeChannelRunnerAgent 替换真实适配器；第 349 行后的用户及第 413 行后的 verbose 测试直接调用 helper，没有驱动 worker.execute。本轮审查者已用真实后台入口 + 真实 ChannelRunnerAgent + HTTP/ASGI 授权独立跑通成功/失败组合，但这是临时复现，不是仓库持续回归。建议最小固化每渠道的成功链路（含工具历史/图片）、403、执行失败、结果读取失败；补一个实际 worker 执行用例，断言 Runtime 接收正确用户和有效 verbose 配置/事件。其他已有局部测试可以保留，无需重写全部测试或为每个错误码复制同一分支用例。

本轮没有观察到这些验证缺口对应的新增功能故障；它们是上一轮 R4 尚未完成的证据与交付问题，不升格为 P1。

### 本轮独立验证记录

| 验证 | 结果 | 证据边界 |
|---|---|---|
| Runner bridge + 共用 process_and_persist 回归 | 84 passed，0 skipped，退出 0 | 标准 dev_test.sh；含三源取代/取消/嵌套追问及共用渠道历史、发送和收尾回归；DB/worker 状态替换为隔离端口 |
| 新入口用例 + 原飞书/钉钉回调 | 54 passed，0 skipped，退出 0 | 新入口 28、原回调 26；未修改测试副本在临时目录执行，绕过 integration 不必要的实库 autouse；后台入口真实、Runner adapter 为测试假件 |
| verbose policy + dispatcher + KF 回复预算 | 95 passed，0 skipped，退出 0 | 标准 dev_test.sh；覆盖实际 Engine/policy 与旁路发送逻辑，不能代替新渠道 worker.execute 的组合验收 |
| 审查者补充真实接线复现 | 8 场景通过 | 每渠道分别验证 completed、failed、foreign_actor、history_unavailable；复用现有两个测试文件的隔离平台/存储端口，恢复真实 ChannelRunnerAgent，并连接实际 HTTP client、ASGI API、RunnerAuthorizer/Manager。worker 执行仍是 MemoryRunners，非实库 E2E |
| 定向 diff 空白检查 | 通过 | `git diff --check` 仅检查本任务修复文件 |

标准命令：

```bash
bash ./scripts/dev_test.sh tests/unit/channels/test_channel_runner_service_independent.py tests/unit/channels/test_session_manager_persist.py -p no:cacheprovider -q
bash ./scripts/dev_test.sh tests/unit/test_verbose_feedback.py tests/unit/channels/test_verbose_dispatcher.py tests/unit/channels/test_wecom_kf_reply_budget.py -p no:cacheprovider -q
```

临时副本组合包括 `tests/integration/test_channel_agent_runner_entries.py`、`test_feishu_routes.py`、`test_dingtalk_routes.py`；手动加载根 conftest 的导入隔离后，以临时目录为 confcutdir 执行相同测试，副本随后删除。业务/日志 DB 指向本机不可连接的测试地址，Redis 关闭，本机 Python 3.12，无 Docker。本轮 pytest 三组共 233 项通过；8 项临时组合复现单列，不冒充新增仓库用例。

未验证真实 Runner 进程 lifespan、飞书/钉钉平台投递、真实 PostgreSQL 费用结算或 Redis 跨进程竞争。部署与真机验收仍需用户明确指示；上述限制不影响本轮已确认的修复结论，也不代表可以跳过两个渠道分别验收。

## 第一轮审核记录（历史）

审查日期：2026-10-09。审查者：本会话开发助手，独立于实际开发者；未修改业务实现、未提交、未部署。

审查基线：`master`，HEAD `96521334572b25c9b8f1c8064bef760aed17748f` 及当前未提交接入改动。结论不自动适用于后续代码版本。

对应事项：`20261008-1603`。设计和计划见[飞书计划](../channel/feishu/implementation_plan.md)、[钉钉计划](../channel/dingtalk/implementation_plan.md)；约束见[架构 §8.1](../system/agent-application-architecture-design.md#81-后续接入的固定边界)、[KF 已验收接入基线](../plans/plan-wecom-kf-channel-restore.md)、[共同集成契约](../system/runner-desktop-runtime-integration-contract.md)。

## 审核结论

接入方向符合第三方渠道模式，但当前实现不通过开发验收：1 项 P1、3 项 P2 待处理。优先修复异常适配和用户上下文退化，纠正重复对话记录，再补充真实后台入口验证并更新计划。

已认可的边界：原回调、验签、去重、用户注册、隐藏命令、session_queue、渠道历史及渲染发送保持原实现；对话通过 ChannelRunnerAgent 的 HTTP client 交给共用 Runner API/worker。没有新增渠道专属 gate、inbox、消费者、容器或本地 Agent loop。channel-result、verbose、finalizer 历史责任及共用 peer sources 的小范围扩展符合现有模式。

`channel_chat_id=None` 对齐这两个渠道已有会话数据，不要求借本次接入重做群聊会话隔离；飞书原有回复方向、原媒体能力同样不在本次扩大改造。

## 必须处理的意见

### R1 / P1：Runner 失败被当成成功交付，用户收不到错误提示

位置：`src/saas/api/channel_routes.py:958、1123`；关联 `src/channels/session.py:1129、1151、1405、1422、1455`，`src/core/session_queue.py:841`。

Runner 不可用、拒绝授权或执行失败会抛 RunnerError。原队列捕获 processor 异常后返回 `EnqueueResult(status='error')`，但 process_and_persist 只分流 merged，没有分流 error，随后进入成功分支：写入 user/空 assistant、发送空内容、最终返回 success。两个后台入口仅处理 merged，也未接住 error。其外层 except 兜底不会触发，因为异常已被队列转换为结果。

已隔离复现：分别调用两个真实后台处理函数，仅替换外部端口和队列边界，注入 `RUNNER_SERVICE_UNAVAILABLE`；两者都写入 user/assistant，发送正文为空，兜底 send_text 调用数为 0。另用两个真实 adapter.send_message 方法确认：空正文、无图片/文件时，实际文本发送调用数为 0，却返回 True。因此还会让渠道误认为已送达，并进入 send_ok 条件下的 recap 收尾。

这是共用渠道层的既有 error 分流缺口，本次 HTTP 接入新增了必须覆盖的服务失败场景；不是将其表述为本次新写出来的队列缺陷。

修改要求：在既有结果适配边界显式处理 error，保留失败语义和必要的安全错误信息，发送非空的渠道失败提示；钉钉群聊继续沿原 reply_target/conversation_type，不能失败后发到错误的单聊目标。不把空响应当成功交付，不触发成功 recap。取消/合并 follower 仍保持原语义；修复不能盲目重交已受理 Runner 或重放未知工具副作用，也不新增投递管线。

验收：两个渠道分别覆盖 Runner 不可用、403、执行 failed、结果读取失败；断言最终状态为失败、失败提示非空且目标正确、无成功 recap、无重复模型执行，并回归 KF 共用处理。

### R2 / P2：计划中的“用户信息等价”不成立，真实姓名退化为技术账号

位置：两个计划第 25、38 行；接入点 `src/saas/api/channel_routes.py:912、1082`；关联 `src/services/agent_runner/worker.py:412、595`、`src/channels/agent_user_builder.py`。

原 build_agent_user_for_channel 使用 `nickname → username → unknown`，并构造含 channel_type/channel_user_id 的 User。ChannelRunnerAgent 不使用传入的 user 对象；worker 重建时只使用 `username → phone → user_id`，不取 nickname，也不恢复渠道字段。渠道侧补写 nickname 并不能令两者等价。

已隔离复现：同一合成 users 行的 nickname 为“测试员工”、username 为 `feishu_ou_fixture`，原 User.name 为“测试员工”，Runner User.name 为技术账号；channel_type/channel_user_id 从 feishu/actor 退化为 None/None。运行时 prompt_sources 直接将 user.name 写入“当前用户”姓名，实际影响称呼及基于姓名的业务输出。

修改要求：针对已授权渠道执行重建与原渠道一致的显示姓名、手机号和渠道身份。渠道身份应来自已授权会话/Runner 主体，不能信任自由请求字段；无需传整份 User 或引入渠道专属 runtime scope。Web 既有用户构造行为如需保持，应明确区分渠道分支。同步修订两份计划的等价性说明。

验收：飞书/钉钉分别覆盖 nickname 与 username 不同、nickname 缺失、手机号存在/缺失、绑定用户有效性；测试应走实际 worker 用户构造和提示词注入，不能仅断言请求 source。

### R3 / P2：渠道与 Runner 各保存一条对话记录，统计被重复计数

位置：`src/saas/api/channel_routes.py:917、972、1087、1137`；关联 `src/services/session_record.py:158、443、580`、`src/services/agent_runner/transaction_repository.py:90`、`src/db/models.py:1524`。

去掉 set_model/set_provider 只停止渠道累计模型用量，并未停止 end_record → save → ChatRecordDB.create。Runner finalizer 另按自己的 record_id 写入同一 source/session 的 chat_records。普通一次对话因此存在执行记录和零用量入口记录；现有统计直接对 chat_records COUNT(*)，会重复计算对话数、累加两份耗时，并产生 model=None 的统计分组。

已隔离执行真实 SessionRecordManager.start_record/complete/end_record，只 mock 持久化和定价：零 token、model=None、skip_save=False，仍调用 ChatRecordDB.create 一次。

没有发现因此重复扣模型费用的证据；问题是重复对话记录及统计失真，不将其夸大成已确认双扣费。KF 参考入口已经通过 skip_save 跳过无 ASR 的渠道空记录。

修改要求：复用 KF 的账本归属方式。没有入口独立用量时避免新增同 source 的 chat_records；保留必要的渠道上下文供既有收尾使用。若确实需要入口审计，先明确其与执行账单的区分和统计口径，不能以 token=0 作为不重复的证明。不要扩大为重写整套统计或计费系统。

验收：普通对话仅一条执行账单；合并 follower 不增空账单；失败与追问场景的记录数及费用有明确预期；模型费用仍只由 Runner receipts/finalizer 结算。

### R4 / P2：计划的完成结论超过当前测试证明的范围

位置：两个计划第 16、48～52 行；`tests/unit/channels/test_channel_runner_service_independent.py:341～386`；两个回调测试的 `_patch_background`。

新增两个渠道用例直接构造 ChannelRunnerAgent，假 repository 提交即返回预制终态，未经过真实后台入口或 worker 执行。取消、追问、owner/follower 等既有 bridge 测试仍使用固定 wecom_kf 的 bridge.agent，没有按计划参数化到 feishu/dingtalk。回调测试将各自 `_process_tenant_*_background` 整体 mock，只证明回调协议与调度，不证明后台执行接线。verbose 新用例只证明配置上传，不证明 worker 应用后实际反馈。两渠道用例的 session.user_id=None 也避开了用户重建差异。

修改要求：在保持外部平台、数据库、LLM 隔离的前提下，调用两个真实后台入口；覆盖主/指定 profile 的绑定、用户上下文、钉钉群聊发送目标、历史单一写入者、工具消息/产物、verbose、生效的取消确认及嵌套追问。至少将依赖 source 的既有通用用例参数化到两个新渠道。增加 R1～R3 的失败回归。

修复后重新完成独立测试与 CR，更新 Phase 4 的代码状态、命令、执行环境、通过/失败/跳过数量及证据边界。当前“45+4”摘要不能代替当前代码可复跑的实际统计。计划的“现状核对”仍描述改动前代码，建议明确标为接入前基线，避免与当前实现混淆。

## 本次验证与限制

| 检查 | 实际结果 | 证据边界 |
|---|---|---|
| 标准入口定向测试 | 47 passed，26 skipped，退出 0 | bridge 实际 HTTP/ASGI，但 DB/worker 状态为假件；26 项被 integration 的 PostgreSQL autouse 前置条件跳过 |
| 同一回调测试隔离执行 | 26 passed，0 skipped，退出 0 | 将两份未修改测试复制到临时目录，使用根 conftest 的导入隔离，避开不必要的 PostgreSQL autouse；真实回调路由，后台函数仍为 mock |
| 用户构造差异 | 两种构造结果不等价 | 合成 users 行，mock UserDB 读取；没有访问真实用户数据 |
| 零用量入口保存 | 仍调用 ChatRecordDB.create 一次 | 真实 SessionRecord 保存流程，mock 定价/写入；实库重复行由代码职责推导，不冒充实库验收 |
| HTTP 失败适配 | 两个真实后台入口均生成空交付且未触发兜底 | 外部平台/数据库/队列替换为隔离端口；真实 process_and_persist 与 ChannelRunnerAgent 错误传播 |
| 空交付 adapter 行为 | 两个真实 send_message 均返回 True，实际文本发送 0 次 | 空 UnifiedResponse，方法内部业务逻辑真实，未调用平台 API |

标准命令：`bash ./scripts/dev_test.sh tests/unit/channels/test_channel_runner_service_independent.py tests/integration/test_feishu_routes.py tests/integration/test_dingtalk_routes.py -p no:cacheprovider -q`。

本机 Python 3.12，无 Docker；业务 DB/日志 DB 强制指向本机不可连接测试地址，Redis 关闭。未触碰真实租户数据，没有修改测试基础设施；临时测试副本执行后删除。未运行真实 Runner lifespan、实库结算或飞书/钉钉平台 E2E，本次不宣称已经真机验收，也不否定计划中其他审查者已有、但此处无法复核的启动检查记录。

后续保持既定第三方渠道接入模式，仅修上述必要适配缺口。更新关联设计/计划详情后，再同步索引状态；真机部署仍需用户明确指示，不能用 KF 已验收或本次单测代替两个渠道各自验收。
