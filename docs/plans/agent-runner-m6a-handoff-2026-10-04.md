# AgentRunner M6a 交接（2026-10-04）

> **状态：M6a 部分完成，未通过整阶段验收。用户要求立即停止剩余工作并交接。**
> 当前代码与测试原样保留，不继续修复、改断言、复验或开展 M6b/M6c/M7。

## 1. 当前状态

- 当前分支为 `master`，本轮重构保留在未提交工作区；没有提交、推送或部署。
- M0–M5 的历史分阶段验收记录见[开发计划](plan-agent-runner-service.md)。这些是当时版本的结论，不代表当前全仓重新验收通过。
- M6a 微信客服已经接入独立服务的可信入站、接单、执行、语音准备、历史、回发及转人工路径，但最后两条独立用例失败，不能宣称整阶段完成。
- `agent_runner.enabled`、`agent_runner.wecom_kf.enabled` 默认关闭；没有默认切换或真实微信账号/付费模型/生产环境验收。
- 飞书、钉钉、四入口与 Runtime+BOSS CLI 综合验收尚未开展。Runtime+BOSS CLI 是既有客户功能的兼容对象，本轮不新增为服务入口。

## 2. 必须遵循的需求

架构重构保持原用户功能和操作方式。Web 页面刷新或关闭不停止已接受任务；Runner 绑定现有会话，独立存储执行上下文并支持中断续接。查询是任务状态和结果的依据，事件订阅只增强通知。

微信客服用户只有聊天消息，没有 Web 的暂停/继续按钮。真实转人工成功后，后续消息不交给 Agent；明确转接拒绝后，保留原 AI 流程。不要新增微信人工操作入口、手工恢复步骤或自动关键词转人工。

已撤销的错误验收要求包括：微信暂停按钮、普通 master clarify 必须进入 WAITING、强制固定 Attempt/内部 phase/只读 GET 次数、无原功能依据的余额归零与人工恢复/数据库故障组合、多层手工构造恢复点及微信 Browser 人工门户闭环。未知外部调用不冒成功、不盲重发的约束保留；不要把取消错误场景理解为取消租户、费用或工作区授权保护。

## 3. 已落下的最后修复

| 内容 | 当前行为 |
|---|---|
| 普通追问 | master/standalone clarify 交回模型继续组织回复；真实子任务澄清仍可通过聊天答案续接 |
| 子任务追问 | 展示当前嵌套等待的真实问题；回复保可信来源、唯一澄清与 revision，不要求问题先获得平台 ACK |
| 语音结果未知 | 保原占位继续聊天；原未知调用/费用事实保留，不重复识别、不伪造零用量；未新增自动结果查询/回调找回服务 |
| 客服账号限制 | 原到期日末/额度限制、固定提示和历史；命中不执行模型或创建本轮 AI 收费记录；阻断语音仍保原识别历史 |
| 媒体 | 原始语音下载 20 MiB，转换后识别边界 10 MiB；普通语音费用 owner 不迁移 |
| 发送结果未知 | 实际 HTTP/adapter 排水、服务确认终态后关闭为 `closed_unknown`；原操作仍 unknown，不旧 POST2、不成功 recap，由原 Manager 同事务释放会话 claim，让同 SID 下一轮继续 |
| 架构收口 | 删除微信投递仓储新增的核心 checkpoint 树解释；终态由服务确认。SourceClient 接受诚实的 `closed_unknown` 域结果 |

最后兼容改动的 14 个文件归档：8 个兼容源见 [final-compatibility](../../tmp/agent-runner-evidence/m6-kf-completion/final-compatibility/handoff.md)，6 个投递/消费者/schema 文件见 [生产交接](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/v3/production-handoff.md)。这不是整个 M0–M6 的完整改动清单。当前有限源码见证为 `final-delivery/v3/combined-sha.json` 的 470 路径；独立回归辅助见证为 43 路径，均不是测试通过数。

新增数据库增量 `2026-10-04 00:03:10` 仅扩展 delivery 的 `closed_outcome`；双 DDL 与表说明已同步，新建、原精确 CHECK 升级及回放有通过结果。HTTP 启动本身不执行迁移。

## 4. 最后验收的真实结果

最后独立回归在用户取消前已自然结束，**实际退出 1，16 通过、2 失败，733.91 秒**。源码 470/辅助 43 首末没有变化；运行期间没有修改代码或断言。[运行证据](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/formal/run-evidence.json)、[原日志](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/formal/formal.log)、[实际命令](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/formal/command.txt)。

通过范围包括：普通追问、真实子任务聊天答案续接、两种 ASR 未知占位与下一轮不重识别、到期/额度限制、正常连续收发与后台交接、受限语音历史、员工顺序、媒体边界、未知发送后同客户同 SID 继续、原语音关联两项、转人工成功/拒绝两项及迁移一项。不把两条失败内部已经到达的部分断言计为完整通过。

### 未完成事项 A：隐藏命令/消息过滤用例

`test_kf_completion_commands.py:106` 要求该账号的 `wecom_kf_context_task_intents` 全空；实际查询返回 4 条。它之前已经验证清空会话、旧消息/媒体过滤、人工与结束状态历史，以及没有 Runner/input/claim。后面的余额和 peer.errors 断言未到。

静态审查指出：该测试自身包含人工/结束状态消息，原业务存在人工期后台意图；“没有 AI 执行”不等于“没有任何后台意图”。测试也未配置原注册流程的 customer/batchget 响应。这些是**尚未结合本窗详细台账确认的候选原因**，不能声称 4 条已全部归因或已修好。当前测试未改；接手者先核真实业务与台账，不为清空断言删除正常后台功能。

### 未完成事项 B：平台明确拒绝发送用例

`test_kf_completion_sends.py:129` 预期 `closed_failed_known`，实际返回 `success=True, outcome=closed_unknown`。后续重复 observe、具体 wire、无成功 recap 与费用/历史守恒断言未到。`success=True` 只表示投递作业收尾，不表示客户收到了消息。

原夹具只有两条 send_msg 响应，并对完整 wire 列表有固定预期；原流程允许进度消息，因此夹具/断言与真实表示序列可能不匹配。但本窗没有保存完整 wire 明细，**根因未确定**，不能直接把 unknown 改成 known、补假 ACK 或放宽断言。用户已停止剩余工作，当前不修复或复验。

### 源码审查与此前失败

最终源码独立审查未发现剩余 P0/P1/P2（8 兼容源、5 投递/schema 源、v2 单叶删除及 v3 单枚举分别审查；不是重新审查整个仓库）。必要编译/import 已通过，独立用例启动过真实隔离 API/Worker；这些不替代整阶段业务验收或生产部署。

此前开发窗 81327、10962、72674 的失败均保原记录；82071 的子追问/同 SID 续聊两项开发通过保留。测试前提修正的原字节、差分与理由在 `final-compatibility/approved-test-correction/`，撤销用例的原字节在 `withdrawn-test-bytes/`；撤销不是通过。原工作区授权用例仅需换合法等待前提，授权要求没有撤销。

## 5. 资源清理

此前开发窗自有数据库、连接、登记进程及目录已按精确分配清理核对；82071 证据见 `final-delivery/v3/development-3-cleanup.json`。

最后独立回归的隔离 wrapper 在 DROP DATABASE 时报告连接意外断开，原错误日志保留。随后只核对本窗登记的 15 份业务 allocation 和 1 份 DDL allocation：登记进程、目录均已消失，连接为零，但原 wrapper 留下同一份自有隔离库。该库已精确删除，实际清理 exit 0，2026-10-04 00:54:35 最终数据库/连接均为零。[末审](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/formal/cleanup.json)与[清理结果](../../tmp/agent-runner-evidence/m6-kf-completion/final-delivery/formal/owned-database-cleanup.json)分别保留先残留、后清理的事实。没有扫描或删除未知资源，没有重启现有服务；master 容器和 Yohar 数据库保留。

## 6. 接手位置与运行组件

先读本交接，再读[设计](../system/agent-application-architecture-design.md)及[计划](plan-agent-runner-service.md)的「M6a 最终收尾契约」；本交接记录停止时的真实截面，不要求继续采用旧错误验收门槛。

主要入口（说明，不是部署指令）：

- API：`python -m src.services.agent_runner.bootstrap`（`--host`、`--port`）。
- 执行：`python -m src.services.agent_runner.worker`（`--worker-id`、`--once`、`--max-tasks`）。
- 微信拉取：`python -m src.channels.wecom_kf.ingress_worker`（`--max-pages`）。
- 微信接单/投递：`python -m src.channels.wecom_kf.admission_worker`（`--once`、`--max-inputs`）。
- recap 消费器装配在现有 `src/background_runner.py`；没有独立 recap_handoff CLI。

配置需要服务地址、可信 peer、微信 service_id 与原渠道配置；凭据不写入交接或日志。任何部署、重启和默认开关切换仍需用户当场明确授权。不要自动提交、推送、重置工作区或删除本轮改动。

证据目录 `tmp/agent-runner-evidence/` 被 Git 忽略；换机器或工作树接手时须另行保留本轮小型证据，不能只推文档后假设证据已随仓库转移。早期 `/private/tmp` 附件部分已被环境清理，计划中已说明，不补造历史证据。
