# Runtime 1.0.0 与业务插件解耦评审

2026-11-11，基于 `codex/runtime-recovery` 的 `2a582ddd` 做设计和源码静态评审。评审覆盖第一方受信插件，以及安装、能力发布、派发、执行控制和回执进入 Agent 的链路。第 1～5 项全部确认，决议已并入共同契约和实施计划。[共同契约 3.0](../system/runner-desktop-runtime-integration-contract.md#12-契约-30-与首轮实施) · [实施计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与插件-100-实施计划待开发)

契约已明确业务归插件，Runtime 负责插件生命周期、通用调度、进程监督、状态和结果传输，Agent 按开放能力调用。已有授权、版本和发布信任是通用承载职责。当前代码仍有插件专用路径，计划需要落实各路径在首轮新版中的归属及验收方式。

## 1. 受信插件从安装到 Agent 调用仍依赖固定名单

- 现状。离线包校验和安装清单读取依赖 `FIRST_PARTY_PROVIDERS`，已安装入口经 `LEGACY_KEYS` 转换。产品读取的发布信任配置已经包含发布者可签发的插件标识。Agent 工具装配还从静态目录和逐工具代理类注册本机能力。[离线验包](../../clients/shared/local-tool-host-core/src/plugins/package.ts) · [安装存储](../../clients/shared/local-tool-host-core/src/plugins/store.ts) · [发布信任配置](../../clients/agent-desktop/electron/productConfiguration.ts) · [Agent 工具装配](../../src/tools/assembly.py)
- 影响。只替换心跳能力清单，安装和 Agent 装配仍可能拒绝一个已有发行授权且遵循新契约的插件。新增业务命令也可能仍需增加服务端代理类。
- 已确认。用户明确要求 Runtime 禁止写死具体插件名称、标识、工具清单等业务事实，一律从插件自身的受信声明获取。已获发行授权的第一方插件按统一协议安装、发现并执行。安装身份沿原签名和发布信任核定，运行入口使用已验证的插件标识和声明。首轮用一个不同标识、已有测试发行授权的插件，经过真实导入、Agent 工具发现、派发和回执，验证两端通用接入。决议已并入共同契约和实施计划。

## 2. 企微与 BOSS 同轮迁移至新版契约

- 现状。Runtime 启动、能力上报和领任务前直接判断 `wecom`、`boss-recruiting`、恢复探测工具以及少数企微数据库工具。服务端的设备准入也按旧桌面资源范围和工具名筛选。旧调用缺省插件身份时还有 BOSS 回退。[Runtime 启动](../../clients/agent-tool-runtime/src/runtimeHost.ts) · [旧调用执行](../../clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts) · [服务端准入](../../src/local_tools/catalog.py)
- 影响。即使入口加了企微适配，若新版接单仍经过这些分支，老插件特例会继续决定新版作业能否领取、排队和停止。
- 已确认。用户决定将 WeCom CLI 纳入首轮，与 Runtime 和 BOSS CLI 一起重构至 1.0.0。企微直接上报自身的工具及并行标记，按统一协议执行和回传。上线后统一升级，原拟议的企微兼容入口和旧作业协议随切换退役。决议已并入共同契约与实施计划。[WeCom CLI 设计](../design/wecom/wecom-cli-design.md#100-重构范围2026-11-11待开发)

## 3. BOSS 的云端业务与调用授权需要明确归属

- 现状。`BossResumeBatchTool` 在本机回执后逐份安排识别、计费、入库及汇总。已有简历评估接口按设备身份鉴权。Runtime 则按插件名向 BOSS 和企微子进程注入配对地址和设备凭据。[BOSS 批量工具](../../src/local_tools/proxy_tool.py) · [简历评估接口](../../src/local_tools/api.py) · [子进程环境](../../clients/agent-tool-runtime/src/runtimeHost.ts)
- 影响。新业务命令若仍依赖通用 Agent 工具适配层编排简历流程，会形成第二个业务控制者。若把现有凭据注入推广到所有插件，则会扩大原有凭据分发范围。
- 已确认。插件控制业务顺序、逐项失败处理和最终汇总，按业务步骤调用获准使用的云端服务。BOSS 自行发起识别、评分、入库等调用，企微自行发起标定等调用。云端业务接口核验授权、处理数据和计费。Runtime 按通用授权提供所需运行条件、调度作业和传输消息。各插件具体接口和费用边界进入各自的独立设计。

## 4. 并行执行与停止要落实到对应作业

- 现状。现有领取循环等待本次调用执行完再领下一项。`ProviderSet` 每个插件复用一个管理器，管理器忙时拒绝下一次调用。现有受管资源停止方法的证明范围是整个插件进程池。[领取循环](../../clients/shared/local-tool-host-core/src/legacy/pollLoop.ts) · [插件进程管理](../../clients/shared/local-tool-host-core/src/legacy/providerManager.ts) · [停稳证据](../../clients/agent-tool-runtime/src/processSupervision.ts)
- 影响。只加一个串行队列仍可能阻住独立后台作业。同一插件的串行和可并行命令也可能因共用进程而互相阻塞。用整个进程池处理一次作业取消，会停止其他用户的作业。固定调用超时可能误结束筛选大量简历等正常长作业。
- 已确认。Agent 和后台提交的串行作业共用一个队列，当前作业完成或确认停止后再开始下一项。可并行作业直接执行。取消只处理原作业。业务步骤是否卡住及如何处理超时由插件负责，Runtime 按可确认的异常退出等事实报告。仍在运行且异常无法确认的作业继续执行，后续串行作业等待。验收覆盖正常长作业、同一插件的独立串行和并行作业，以及仅取消目标作业。此决议替代此前按静默时限自动终止作业的规则。

## 5. 接单、结果和诊断的写入职责需要在计划中对齐

- 评审发现。契约规定服务端固定作业编号，插件记录接单并处理重复派发，Runtime 保存并补传原回执，服务端按结果编号去重。原计划曾把“接单和结果去重”合写在 Runtime 实施项中，职责表述有歧义。[共同契约](../system/runner-desktop-runtime-integration-contract.md#123-作业派发接单与关联n03n09n12) · [实施计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与插件-100-实施计划待开发)
- 影响。若据此让 Runtime 再持有一套业务完成记录，遇到断网、崩溃或重复请求时，插件进度、Runtime 推断与服务端结果可能不一致。
- 已核实 Agent 调用编号。模型接入层读取 `tool_calls[].id`。有效工具调用进入 Agent 时，`normalize_calls` 沿用模型编号或补生成编号。启用新版调用标识的 Runner 按执行 ID、模型调用 ID 与该轮工具序号生成可重复得到的 `tool_call_id`，并在派发前持久保存。该编号用于关联原工具调用及结果。[工具调用归一化](../../src/core/agent_engine/engine.py)
- 已核实 Runtime 调用编号。Runner 按 `runner_id`、执行 ID、`tool_call_id` 及调用阶段关联或复用已持久的 `invocation_id`。后台直调绕过 Runner，经统一入口入队，由数据库生成 UUID 主键。已有幂等键的同一后台请求重投时复用原记录。Runtime 领取的调用编号是 `invocation_id`。[Runner 调用绑定](../../src/services/agent_runner/local_invocations.py) · [后台直调实例](../../src/desktop_automation/executor.py) · [数据库编号](../../deploy/init-postgres.sql) · [设备领取接口](../../src/local_tools/api.py)
- 已确认。复用 `invocation_id` 作为发给 Runtime 的作业编号。Agent 调用保留服务端与 `tool_call_id` 的关联，后台直调使用自身受理的 `invocation_id`。同一次派发重送沿原编号。插件负责接单记录、进度及业务结果。Runtime 校验通用消息、管理进程、保存与补传原回执，按实际执行事实生成通用异常及问题编号。服务端按结果标识去重并归档。用户要求继续时由插件判断保存的业务进度是否可续。
