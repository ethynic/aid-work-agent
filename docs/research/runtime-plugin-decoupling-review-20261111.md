# Runtime 1.0.0 与业务插件解耦评审

2026-11-11，基于 `codex/runtime-recovery` 的 `2a582ddd` 做设计和源码静态评审。依据为[共同契约 3.0](../system/runner-desktop-runtime-integration-contract.md#12-契约-30-与首轮实施)及[实施计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与-boss-cli-100-实施计划待开发)。评审覆盖第一方受信插件、企微兼容入口，以及安装、能力发布、派发、执行控制和回执进入 Agent 的链路。以下五项为待逐条确认的实施边界。

契约已明确业务归插件，Runtime 负责插件生命周期、通用调度、进程监督、状态和结果传输，Agent 按开放能力调用。已有授权、版本和发布信任是通用承载职责。当前代码仍有插件专用路径，计划需要落实各路径在首轮新版中的归属及验收方式。

## 1. 受信插件从安装到 Agent 调用仍依赖固定名单

- 现状。离线包校验和安装清单读取依赖 `FIRST_PARTY_PROVIDERS`，已安装入口经 `LEGACY_KEYS` 转换。产品读取的发布信任配置已经包含发布者可签发的插件标识。Agent 工具装配还从静态目录和逐工具代理类注册本机能力。[离线验包](../../clients/shared/local-tool-host-core/src/plugins/package.ts) · [安装存储](../../clients/shared/local-tool-host-core/src/plugins/store.ts) · [发布信任配置](../../clients/agent-desktop/electron/productConfiguration.ts) · [Agent 工具装配](../../src/tools/assembly.py)
- 影响。只替换心跳能力清单，安装和 Agent 装配仍可能拒绝一个已有发行授权且遵循新契约的插件。新增业务命令也可能仍需增加服务端代理类。
- 待确认。已获发行授权的第一方插件，按统一协议安装、发现并执行。安装身份沿原签名和发布信任核定，运行入口使用已验证的插件标识和声明。首轮用一个不同标识、已有测试发行授权的插件，经过真实导入、Agent 工具发现、派发和回执，验证两端通用代码无须增加插件名和专用工具类。

## 2. 企微兼容入口的隔离范围需要写清楚

- 现状。Runtime 启动、能力上报和领任务前直接判断 `wecom`、`boss-recruiting`、恢复探测工具以及少数企微数据库工具。服务端的设备准入也按旧桌面资源范围和工具名筛选。旧调用缺省插件身份时还有 BOSS 回退。[Runtime 启动](../../clients/agent-tool-runtime/src/runtimeHost.ts) · [旧调用执行](../../clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts) · [服务端准入](../../src/local_tools/catalog.py)
- 影响。即使入口加了企微适配，若新版接单仍经过这些分支，老插件特例会继续决定新版作业能否领取、排队和停止。
- 待确认。兼容入口承担旧工具映射、旧能力探测和旧回执转换。旧命令的并行标记在兼容入口确定后交给同一个调度器。新版路径从签名声明、作业绑定和实际进程状态取得调度信息。已交付旧客户端保留原服务端入口。

## 3. BOSS 的云端业务与调用授权需要明确归属

- 现状。`BossResumeBatchTool` 在本机回执后逐份安排识别、计费、入库及汇总。已有简历评估接口按设备身份鉴权。Runtime 则按插件名向 BOSS 和企微子进程注入配对地址和设备凭据。[BOSS 批量工具](../../src/local_tools/proxy_tool.py) · [简历评估接口](../../src/local_tools/api.py) · [子进程环境](../../clients/agent-tool-runtime/src/runtimeHost.ts)
- 影响。新业务命令若仍依赖通用 Agent 工具适配层编排简历流程，会形成第二个业务控制者。若把现有凭据注入推广到所有插件，则会扩大原有凭据分发范围。
- 待确认。BOSS 插件控制业务顺序、逐项失败处理和最终汇总，通过现有授权调用 BOSS 云端服务。BOSS 云端服务负责业务鉴权、数据写入及计费。Runtime 按可信授权提供所需运行条件，通用层根据身份和版本路由、验证通用消息及传输结果。BOSS 具体能力、云端接口调用和费用边界进入 BOSS CLI 独立设计。

## 4. 并行执行与停止要落实到对应作业

- 现状。现有领取循环等待本次调用执行完再领下一项。`ProviderSet` 每个插件复用一个管理器，管理器忙时拒绝下一次调用。现有受管资源停止方法的证明范围是整个插件进程池。[领取循环](../../clients/shared/local-tool-host-core/src/legacy/pollLoop.ts) · [插件进程管理](../../clients/shared/local-tool-host-core/src/legacy/providerManager.ts) · [停稳证据](../../clients/agent-tool-runtime/src/processSupervision.ts)
- 影响。只加一个串行队列仍可能阻住独立后台作业。同一插件的串行和可并行命令也可能因共用进程而互相阻塞。用整个进程池处理一个作业静默超时，还会停止其他用户的作业。
- 待确认。按作业声明决定领取与执行，在既有受管进程机制上落实单项取消、静默终止和停稳确认。用同一个测试插件的两个命令，验证两个用户的串行和并行作业同跑，以及停止其中一个时另一个仍能正常结束。具体进程安排由实现根据插件支持的独立停止能力确定。

## 5. 接单、结果和诊断的写入职责需要在计划中对齐

- 现状。契约规定服务端固定作业编号，插件记录接单并处理重复派发，Runtime 保存并补传原回执，服务端按结果编号去重。计划第 16.10 节第 2 项把“接单和结果去重”合写在 Runtime 实施项中，职责表述有歧义。[共同契约](../system/runner-desktop-runtime-integration-contract.md#123-作业派发接单与关联n03n09n12) · [实施计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与-boss-cli-100-实施计划待开发)
- 影响。若据此让 Runtime 再持有一套业务完成记录，遇到断网、崩溃或重复请求时，插件进度、Runtime 推断与服务端结果可能不一致。
- 待确认。插件负责业务接单记录、进度和结果。Runtime 校验通用消息、管理进程、保存与补传原回执，按实际进程与消息事实生成通用异常和问题编号。服务端固定作业身份、按结果标识去重、归档并向 Agent 展示。用户要求继续时由插件判断保存的业务进度是否可续。
