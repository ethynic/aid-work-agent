# 企业微信应用渠道接入 AgentRunner 开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 现状核对与接入设计 | ✅ 完成（2026-10-10） | 已核对企业微信入口、三渠道参考实现及共享来源限制；仅完成规划 |
| Phase 2 | Runner 来源及历史归属适配 | ✅ 完成（2026-10-10） | wecom 来源、查询、channel-result、verbose、finalizer 和 peer 配置已接线 |
| Phase 3 | 企业微信对话入口替换 | ✅ 完成（2026-10-10） | 复用 ChannelRunnerAgent；原回调、队列、发送及共享会话保留，入口 skip_save |
| Phase 4 | 定向自测、独立测试与 CodeReview | ✅ 完成（2026-10-10） | 独立相关回归 261 项、企业微信实库 12 项、真实执行探针及启动通过；CR 无问题，2 项非本项失败见 §5.4 |
| Phase 5 | 部署与企业微信真机验收 | 📋 待开发 | 待用户明确指定部署环境；部署和真机结果独立登记 |

本项让企业微信自建应用渠道 `wecom` 的对话执行使用现有独立 Runner API/worker。采用已接入微信客服、飞书和钉钉的薄桥模式，保留原回调、会话队列、历史与发送流程。业务代码已按本计划接入并完成定向自测、独立测试与审查；最终审核已批准提交，提交记录通过本计划的 Git 关联查询，部署与真机验收尚未完成。

范围是 `/t/{tenant_id}/wecom/callback/{config_id}`；`wecom_kf`、`wecom_personal_rpa` 及 Runtime 的 `wecom-cli` 是不同入口，不因本项重新接入或调整。实施遵循 [AgentRunner 架构 §8.1](../system/agent-application-architecture-design.md#81-后续接入的固定边界)、[Runner / Desktop / Runtime 共同契约](../system/runner-desktop-runtime-integration-contract.md)和[开发流程](../../.claude/rules/dev_workflow.md)。

## 1. 接入前代码基线与文档差异

以下为 2026-10-10 本项接入实施前的工作区代码基线，不代表当前实现或已部署版本。接入后的实现与验证见顶部「开发进度」及 §5。

| 环节 | 当前实现 | 接入决定 |
|------|----------|----------|
| 回调接收 | `src/saas/api/channel_routes.py` 的 GET/POST 按 tenant/config 创建 adapter，处理签名、解密、事件过滤、消息去重，再创建后台协程并返回 success | 保留原逻辑；回调 ACK 不代表 Runner 已持久接单 |
| 对话执行 | `_process_tenant_wecom_background` 调用 `agent_router.get_agent`，经 `process_and_persist` 在主 API 进程执行兼容 Agent | 在此处替换为 ChannelRunnerAgent，实际执行移至独立服务 |
| 会话身份 | `get_or_create_session(channel_type='wecom', channel_user_id=message.user_id, tenant_id=..., subagent_id=subagent_type or '')`；未传 channel_chat_id/config_id | 沿用原会话；Runner 传 channel_chat_id=None、profile_id=subagent_type or 'main' |
| 用户上下文 | `ensure_user_registered` 建号，`build_agent_user_for_channel` 补全用户；worker 已能按任意非 chat 来源重建 nickname/phone/渠道身份 | 保留入口补全；worker 从授权行及 users 表重建，测试覆盖 wecom |
| 历史及发送 | 原 session manager、session_queue 和 adapter 负责合并、取消、user/tool/assistant 落库、verbose/final 与 recap | 继续由原渠道唯一写 channel_messages、唯一回发；Runner 仅读取渠道历史 |
| 费用 | 企业微信入口 start_record 后读取 `agent.llm` 的 model/provider；仍记录本地执行用量 | 改由 Runner 结算模型/工具费用；无入口独立用量时 skip_save=True，保留 end_record |
| Runner 来源限制 | SessionQuery、API 会话查询、peer 配置、channel-result、finalizer 排除及 verbose 两端仅支持现有来源集合 | 必须同时扩展 wecom；仅替换 Agent 构造会失败 |

参考实现及审核证据：

- [飞书计划](../channel/feishu/implementation_plan.md#agentrunner-接入后续独立事项)、[钉钉计划](../channel/dingtalk/implementation_plan.md#agentrunner-接入后续独立事项)：代码已接入，文档仍登记待部署真机验收。
- [微信客服恢复计划](plan-wecom-kf-channel-restore.md)：已有独立 Runner 接入和真机验收记录。
- [飞书、钉钉审核](../reviews/feishu-dingtalk-agentrunner-review-2026-10-09.md)：沿用其失败分流、用户重建、skip_save 与真实接线测试要求，避免重复引入已修问题。

现有文档有三处与代码不一致，规划以代码事实为准：

1. 架构 §8 原将飞书/钉钉写成待开发，本次同步为开发完成、待部署验收；`.claude/rules/architecture.md` 的旧流程描述也同步纠正。
2. 企业微信配置手册仍介绍 `/wecom/callback` 单租户端点，当前 `src` 只查到上述租户级端点。本项不新增旧端点，也不全面修订历史配置手册。
3. 已完成索引中“渠道语音 ASR 补齐”覆盖 wecom，但当前 WeComAdapter 只读取 XML `Recognition`，缺失时为“[语音消息]”；企业微信后台函数未调用独立 ASR，也未下载/传递图片、文件附件。接入保留该实际行为，不宣称具备未接线能力；媒体补齐如有需求另立任务。

## 2. 接入设计

### 2.1 请求与身份

企业微信回调继续执行自动注册、隐藏命令和用户信息补全。普通消息经以下链路处理：

```text
WeCom callback → 原后台入口 → ChannelSessionManager / session_queue
  → ChannelRunnerAgent → Runner API → worker / RuntimeExecution
  → 查询结果与脱敏工具消息 → 原渠道落库、渲染、发送及 recap
```

薄桥构造参数固定为 `source='wecom'`、原 session_id、`channel_user_id=message.user_id`、`channel_chat_id=None`、`profile_id=subagent_type or 'main'`、原 config_id。提交使用已有 `/v1/runners`，查询和控制沿用现有接口。tenant/user 从可信渠道会话派生，服务凭据只在服务间使用；不把身份、密钥或设备权限塞入自由请求字段。

`request_data.channel_config_id` 按飞书/钉钉模式仅作追踪，不新增 KF 专属 `channel_runtime_scope`，也不把该字段当作已验证的应用授权。wecom 复用通用 `_channel_in_tx` 校验渠道来源、会话用户/chat、profile、租户有效性、用户状态和租户订阅；用户自动注册失败时，沿用现有允许 user_id 为空的渠道规则。

多个企业微信应用若 tenant/user/profile 相同，当前会共享会话和队列，config_id 不参与会话隔离；首期保留这一语义。不同租户、用户和 profile 应隔离。若产品要求按应用拆分会话或验证应用与 profile 的独立绑定，须另行设计迁移，不能在接入时静默改 session_id。

### 2.2 执行、追问与失败

保留 `process_and_persist`、队列合并与取消规则。薄桥遇到真实主/嵌套子澄清时，用既有 controls.reply 关联原 runner、wait_id 和 target_execution_id；普通新消息取代旧执行时，先确认旧 Runner 取消终态，再交接新执行。合并 follower 不回发，不重复落库。

Runner 不可用、身份拒绝、执行失败或 channel-result 读取失败均走现有 error 分支：发非空失败提示、不写空 assistant、不触发成功 recap。不得自动回退进程内 Agent，不自动重交已可能执行副作用的业务任务。verbose 发送失败维持 best-effort，final 发送失败沿原渠道处理。

本次不新增持久 inbox、投递 outbox、渠道消费者、专用 gate 或容器。独立 Runner 能持久保存执行事实，但当前回调 ACK 到提交之间仍是主 API 后台协程，取得结果后的历史/投递也由原渠道收尾；主 API 崩溃不保证自动恢复这些环节。该边界须留在验收记录，不能把 Runner 持久执行等同于渠道端到端可靠投递。

### 2.3 历史、费用及反馈

- finalizer 把 wecom 加入渠道历史排除集合；channel-result 允许已授权 wecom 获取脱敏 tool_messages。原渠道事务唯一写 user/tool/assistant，避免双写。
- 入口删除 `agent.llm` 的 model/provider 访问；start_record 后设 `skip_save=True`，避免无独立用量的空记录重复统计。模型和工具费用来自 Runner receipts/finalizer。当前入口没有独立 ASR 费用；将来补 ASR 时须单独核对入口计费，不能沿用 skip_save 丢掉实际用量。
- 保留 `make_send_response`、`make_send_verbose` 和原收件人 message.user_id；薄桥与 worker 两端同时接纳 wecom 的 verbose 配置，保持全局 force_disabled 最高优先级、取消/合并 owner 的反馈状态及 final 额度预留。
- worker 继续从持久身份和 users 表重建用户，保留昵称、phone、channel_type='wecom' 和 channel_user_id；原用户补全结果须在提交前可读取。工具不通过自由 channel_user_info 字段获得身份。

## 3. 文件范围与兼容方案

本项按高风险代码开发执行：涉及跨模块契约、身份校验、计费及取消并发，实施时安排开发者、独立测试者和独立 CodeReview 者。当前纯文档阶段由主控者核对，不启动三角色开发流程。

| 文件 | 计划修改 |
|------|----------|
| `src/services/agent_runner/contracts.py` | SessionQuery.source 增加 wecom；RunnerSubmit 继承同一声明 |
| `src/services/agent_runner/api.py` | list_session 的 source 查询枚举增加 wecom |
| `configs/config.yaml` | 当前主 API peer.sources 增加 wecom；同步检查 settings 加载，AgentRunnerPeerConfig 已是 List[str]，无需新配置项或凭据 |
| `src/services/agent_runner/manager.py` | channel_result 来源门增加 wecom，保留 actor 授权和脱敏 |
| `src/services/agent_runner/finalizer.py` | wecom 不写 Runner 渠道历史，仍执行用量结算及 claim 释放 |
| `src/channels/runner_agent.py` | verbose 配置传递集合增加 wecom；沿用请求、取消、追问与结果逻辑 |
| `src/services/agent_runner/worker.py` | channel_verbose_config 增加 wecom；复用现有渠道用户重建 |
| `src/saas/api/channel_routes.py` | 仅改企业微信 Agent 构造、入口记录归属及移除未使用的 history 预读；其余渠道业务保持原流程 |
| 相关 tests | 扩展真实入口/薄桥/API/worker 接线、来源隔离、finalizer 与回调回归 |

`authorization.py`、`channel_context.py`、`runtime/history.py` 和 `session.py` 作为重点核对对象：当前通用授权、wecom 历史读取和 error 分流已有基础，预期无需为企业微信增加特殊实现；实际发现缺口先更新本计划再最小修复。

**接口变更登记**：复用内部 Runner HTTP v1，在原 source 枚举中新增 wecom；不改已有字段、默认值、旧意图摘要或旧请求语义。本批唯一写入者为本接入任务的开发者，生产端是 Runner API/worker，消费端是主 API 的 ChannelRunnerAgent。2026-10-10 实施前已确认上述业务文件无已有改动；本批唯一写入者为本实现会话，保留其他任务的工作区改动。新来源属于服务能力扩展，旧服务严格解析器不能接收它。

兼容门包括：旧四种 source 请求仍可解析/查询/控制/恢复；新 wecom 请求只有服务端和 worker 均支持后才启用入口。部署顺序为先升级全部 Runner API/worker 并验证 peer 配置，再切换主 API 企业微信入口；混合版本期间不提交 wecom Runner。回退须先暂停 wecom 新任务进入，核对并排空已接受任务及 waiting/unknown 副作用事实，再回退相关版本；不得让旧 worker 领取新来源任务。

本项不修改公共 Desktop/Device API、execution binding、grant 或 Runtime wire schema，不新增本地设备选择与授权；共同契约 H1～H4 不因此重新实施。如开发发现必须改变这些共同语义，应另登记版本、唯一写入者与两端兼容证据后再做依赖实现。

## 4. 验证与验收

### 4.1 定向自动化

优先扩展现有 `test_channel_runner_service_independent.py`、`test_channel_agent_runner_wiring.py`、`test_channel_agent_runner_entries.py` 及 `test_worker_execution.py`，避免只检查字符串或只 mock 整个 ChannelRunnerAgent。

| 场景 | 必须证明 |
|------|----------|
| 文本成功 | 企业微信真实后台入口 → 真实薄桥 → HTTP/ASGI API/授权 → 结果与工具历史 → 原渠道落库/回发；不调用 agent_router |
| 来源与身份 | 创建、会话查询、runner 查询、channel-result、cancel/reply 均拒绝 foreign actor/source；跨租户会话、profile 不匹配、禁用/过期主体与无订阅不能执行 |
| worker 接线 | 实际 execute 收到 wecom 用户昵称/phone/渠道身份及有效 verbose 配置；全局 kill switch 不能被渠道覆盖 |
| 历史与费用 | finalizer 排除 wecom 渠道历史但仍结算 receipts、释放 claim；渠道事务唯一写入；实际 record manager 不保存入口零用量记录 |
| 合并与取消 | 连续输入合并 follower 无回发；取代旧任务前确认取消；接受响应窗口/轮询中协程取消可清理；取消未确认时不推进替代任务 |
| 主/子追问 | controls.reply 对准原等待；不把旧问题重发为本次答案；无支持的工具等待按原规则取消并报错 |
| 失败与发送 | 服务不可用、Runner failed、授权拒绝、工具历史获取失败均发提示且不触发成功 recap；verbose 失败不影响 final，final 失败不记录成功投递 |
| 原回调与消息 | 签名/解密错误、事件过滤、重复消息、隐藏命令、Recognition/语音占位、原长消息与文件卡片发送行为保持；图文附件不因 source 扩展被宣称已支持 |
| 四渠道回归 | chat/wecom_kf/feishu/dingtalk 的解析、查询、工具历史、verbose 和历史责任不退化；多应用共享会话行为如实保留 |

计划起始命令（实施后按新增用例补充）：

```bash
bash ./scripts/dev_test.sh \
  tests/unit/channels/test_channel_runner_service_independent.py \
  tests/unit/channels/test_channel_agent_runner_wiring.py \
  tests/unit/services/agent_runner/test_worker_execution.py \
  tests/integration/test_channel_agent_runner_entries.py \
  tests/integration/test_wecom_callback.py \
  -p no:cacheprovider -q
```

独立测试者补选实际受影响的 session_queue、session、verbose、Runner 结算和来源授权用例；不用全量测试代替定向证据。新增/现有回调测试须确认调用租户级真实入口，旧路由测试不能替代该边界。入口、配置、worker 修改后，在隔离环境执行配置加载、import 和 Runner 启动检查，不启动生产调度或写真实业务数据。

记录运行环境、命令、passed/failed/skipped、原因及假件边界。MemoryRunners 预制终态、记录型 Runtime、内存仓储及 finalizer 假件只能证明相应接线；真实 PostgreSQL 结算、Redis 竞争、真实模型/平台发送须分别登记。integration fixture 跳过不能写成已通过，最终费用与历史验收不能只靠 mock finalizer。

### 4.2 真机验收及完成门

在用户明确授权部署的目标环境，使用企业微信自建应用独立验证：文本、绑定数字员工、隐藏命令、平台语音 Recognition/缺失占位、工具历史、主/子追问、连续消息合并与新消息取代、verbose/final、错误提示、渠道历史单写及账单单一归属。至少核对不同租户/用户/profile 的隔离，以及同用户同 profile 多应用的现有共享行为。微信客服及其他渠道验收不能替代本渠道验收。

代码完成门：定向自测、独立测试和独立 CR 的有效证据齐备，P0/P1 已修复复核，受影响启动检查通过，更新本计划并按项目规则移入完成索引；真机部署验收未完成时在完成记录明确保留。Phase 5 只有实际授权、部署及验收后才能标完成。提交和部署分别需要用户授权，本计划不构成授权。

## 5. 当前验证记录

2026-10-10：完成只读源码、参考计划与审核记录核对，形成上述设计并登记索引。计划本地链接、进度区、索引唯一登记、代码块及新文件空白检查通过；关联文档定向 `git diff --check` 通过。业务实现和测试未修改；未运行业务测试、提交或部署。


### 5.1 开发者实施与验证（2026-10-10）

- 设计者复核确认无额外产品决议：当前无独立 ASR 用量，入口 `skip_save=True`；Runner 保留 `_settle` 和 claim 释放；config_id 仅追踪；所有 API/worker 支持新来源后再切入口。确认通过读取设计会话的复核结果取得。
- 上述 8 个业务文件完成来源扩展和入口替换；`authorization.py`、`channel_context.py`、`runtime/history.py`、`session.py` 核对后复用现有通用实现，无额外特殊授权或设备协议修改。
- 扩展真实 HTTP/ASGI 薄桥、企业微信后台入口、worker.execute、取消/主子追问、来源隔离与 verbose 回归；租户 GET/POST 回调新增真实 crypto/adapter 解析验证，保留签名/解密拒绝、事件过滤、去重、平台 Recognition、原后台分派及隐藏命令。
- 在运行中的 `aid-agent-api` 测试容器内验证，Python 3.11；已核对 bind mount `/Users/ethynic/repos/aid-work-agent` → `/app`。宿主机缺少 pytest，使用项目统一测试脚本；Redis 测试配置关闭，内存降级，不宣称真实 Redis 竞争已验收。

| 验证 | 命令 / 范围 | 结果 |
|---|---|---|
| 定向自测 | §4.1 起始命令的 5 个测试文件 | 185 passed，0 failed / skipped；仅既有弃用警告 |
| 隔离实库首轮 | `bash ./scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_wecom_settlement.py -p no:cacheprovider -q` | 11 passed；新增共享会话第 12 项已由独立测试者重跑通过 |
| 隔离启动 | `bash ./scripts/dev_test.sh --isolated-db /tmp/wecom_runner_startup_probe.py -p no:cacheprovider -q` | 1 passed；配置/入口/worker import、真实 bootstrap lifespan+health 和 worker --once 退出 0 |
| 本批空白检查 | 定向 `git diff --check` | 通过 |

启动探针是测试容器临时文件（宿主 `/private/tmp/wecom_runner_startup_probe.py`），使用临时 `aid_test_*` PostgreSQL 库，worker 子进程固定该数据库及 logs 库、临时资源目录，不领取真实任务，不启动主 API 生产调度。没有模型/平台调用，未修改或重启既有服务。费用测试真实调用 SessionRecordManager、RunnerFinalizer、UsageRepository、扣费及渠道批量写入，只排除无关缓存/trace 的 `_after_commit`。HTTP 接线仍以 MemoryRunners 预制终态、worker.execute 以记录型 Runtime 为假件；不把这些证据当作真实模型、企业微信发送或端到端恢复验收。

### 5.2 发布执行边界（待明确部署授权）

已核对 `deploy/agent2_update.sh`、`deploy/agent3_update.sh` 的 Runner overlay 与主 API 使用同一条 `compose up`，且代码/配置有共享挂载；普通更新脚本本身不保证本计划的分批兼容门。因此不能直接运行整体更新脚本并假定入口最后切换。部署目标确定后，选择能确实暂停企业微信新消息进入的维护窗口，先核对在途任务和共享数据库内全部 worker，再升级全部 Runner API/worker、验证 `peer.sources` 与 schema/health/worker，就绪后重建主 API 并恢复入口；或准备可验证的分批服务更新命令。agent2/agent3 共库及固定 Runner 容器名互斥也须沿既有部署清单核对。

本批未运行部署脚本、重建或重启业务容器；没有提交、推送或真机验收。回调 ACK 至 Runner 受理之间、以及结果后渠道历史与发送收尾仍由主 API 后台协程负责，其崩溃恢复边界维持 §2.2，不承诺渠道端到端可靠投递。

### 5.3 独立 CodeReview（2026-10-10）

独立审查者 `/root/wecom_review` 只读审查本批业务 diff、新测试和必要上下文，未发现 P0/P1/P2：来源门完整；持久渠道身份/租户/profile 校验及 worker 重授权保持；config_id 仅追踪；历史唯一写、费用结算及 claim 释放正确；取消/取代/主子追问及失败分流沿既有实现。审查者定向 `git diff --check` 退出 0，未重复业务测试。真实 Redis/模型/平台发送和部署兼容门仍待验收。


### 5.4 独立测试中发现的非本项失败

- 并行通知任务在 `deploy/init-postgres.sql` 退役 gate 语句之后追加合法通知 DDL，触发既有 `test_fresh_runner_schema_and_original_channel_do_not_require_retired_native_pipeline` 的 `schema.rstrip().endswith(...)` 断言（`test_channel_runner_service_independent.py:542`）。原 gate 退役语句仍在 `init-postgres.sql:5295`，其前置退役/schema 校验均通过；仅末尾位置条件失效。本批不修改通知 schema 或无关断言。原 9 文件运行结果 261 passed / 1 failed，排除该项后 261 passed / 1 deselected、退出 0。
- 相邻真实 API 回归 `test_channel_current_actor_proof_is_required_for_read_and_cancel[wecom_kf]` 的旧请求夹具缺少 `request_data.channel_config_id`，现有 KF `kf_binding` 返回 `CHANNEL_CONFIG_REQUIRED`（400），与旧夹具预期 202 不符。KF 专属约束并非本项修改，本批不放宽授权或修正无关夹具；实库 3 文件结果 69 passed / 1 failed。新增企业微信实库 12 项全部通过，无跳过。
- 并行通知任务增加 `repository.assert_schema` 和 `event_repository` 的通知依赖后，主控重跑隔离启动探针：1 passed、退出 0；独立测试者已在新代码状态完成真实 Runtime/Engine/费用/历史闭环，见 §5.5。其余源码保持本批稳定状态。


### 5.5 独立真实执行闭环（2026-10-10）

`/root/wecom_test` 在临时 `/tmp/wecom_actual_worker_independent_probe.py` 验证两真实 Runner API 子进程、CLI worker、Runtime/Engine/Gateway 与真实 PostgreSQL receipt、finalizer、扣款、claim 收尾；唯一模型 I/O 边界使用已有 loopback `ProviderPeer`，不调用真实模型供应商或企业微信发送。

```bash
bash ./scripts/dev_test.sh --isolated-db /tmp/wecom_actual_worker_independent_probe.py \
  -c pytest.ini -p tests.conftest -p no:cacheprovider -q
```

结果：1 passed（12.85s）、退出 0、无跳过。API/worker 均设置 `NOTIFICATIONS_ENABLED=true`，证明 wecom 完成后仍不产生通知记录：SQL `notification_messages` 为 0，`channel_messages` 为 0，余额仅扣 0.01，receipt 单一且 applied、settlement settled，claim 已释放；原 actor 获取 channel-result 200，foreign actor 403。执行前后并行依赖 `event_repository.py`、`repository.py`、`producer.py` 的 SHA-256 保持一致。永久源码/测试未由测试者修改。

本项开发完成并移入完成索引，Phase 5 保留待授权部署及真机验收。当前验证不能替代真实 Redis 多进程竞争、供应商调用或企业微信平台发送验收；后台协程恢复边界不变。该独立测试阶段尚未获得提交、推送与部署授权；后续提交审核见 §5.7，部署仍须遵循 §5.2。


### 5.6 独立回归命令与最终结论

独立测试者 `/root/wecom_test` 的最终报告确认本项无阻断问题，未改共享源码；精确命令如下。临时真实执行探针源文件保留在宿主 `/private/tmp/wecom_actual_worker_independent_probe.py`，测试容器副本在 `/tmp`。

```bash
bash ./scripts/dev_test.sh \
  tests/unit/channels/test_channel_runner_service_independent.py \
  tests/unit/channels/test_channel_agent_runner_wiring.py \
  tests/unit/services/agent_runner/test_worker_execution.py \
  tests/integration/test_channel_agent_runner_entries.py \
  tests/integration/test_wecom_callback.py \
  tests/unit/channels/test_session.py \
  tests/unit/test_session_queue.py \
  tests/unit/channels/test_verbose_channel_delivery.py \
  tests/unit/channels/test_verbose_dispatcher.py \
  -k 'not test_fresh_runner_schema_and_original_channel_do_not_require_retired_native_pipeline' \
  -p no:cacheprovider -q

bash ./scripts/dev_test.sh --isolated-db \
  tests/integration/agent_runner_service/test_wecom_settlement.py \
  tests/integration/agent_runner_service/test_api.py \
  tests/integration/agent_runner_service/test_usage_storage.py \
  -p no:cacheprovider -q
```

第一条 261 passed / 1 deselected（27.41s）、退出 0，无 skip；未排除前为 261 passed / 1 failed。第二条 69 passed / 1 failed（133.82s）、退出 1，其中本项 12 项全部通过。两失败的精确原因及非本项归属见 §5.4，不宣称全量全绿。临时真实执行探针和主控最新启动检查均退出 0，复用了同一代码状态的有效证据，未机械重复全量测试。

独立审查者补充复核并行依赖后仍无 P0/P1/P2：通知 producer 在访问通知表前限定 chat/web，wecom 直接返回；通知启用状态新增通知 schema 启动前提，发布时需核对对应 schema。结合启用通知的真实闭环和最新启动检查，未发现新增依赖影响本次企业微信接入。最终本批 Python 语法、文档链接/代码块/空白及索引唯一登记检查通过。

### 5.7 设计者 / 最终审核复核

2026-10-10，设计/最终审核会话亲自运行 `bash ./scripts/dev_test.sh --isolated-db /tmp/wecom_runner_startup_probe.py -p no:cacheprovider -q`：1 passed（1.94s）、退出 0，覆盖配置、企业微信入口/worker import、bootstrap lifespan/health 及 worker --once。源码审查无新增阻断发现。审核要求的两处架构文档已同步为代码完成、待部署真机验收，§1 明确接入前基线；仅修文档并通过链接/代码块/空白检查，业务源码保持稳定。

设计者最终审核通过，依据人类用户“达标了的话让他提交代码”的明确授权，批准仅提交本项 18 文件及配置来源单一 hunk，并推送已确认的 `origin/master`。提交前执行 fetch 并快进同步远程 3 个提交；仅对重叠的 `docs/ideas.md` 定向保存后恢复，通知和知识库索引改动保留，本项不提交该文件。部署未授权。

快进同步后再次运行上述隔离启动探针：1 passed（1.77s）、退出 0。远程变更未涉及本项接入源码，复用此前独立业务验证与审查结果。
