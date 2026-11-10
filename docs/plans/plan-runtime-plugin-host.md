# Runtime 执行环境、插件宿主与可视化客户端开发计划

## 开发进度

> **2026-10-30 S1～S5简化开发、独测与CR已通过；S5最新Runtime/插件打包及自有fixture安装升级验收完成，现用客户端未替换。用户后续已授权提交与推送本任务及清理打包临时文件，仍禁止部署、替换当前客户端或恢复监听；第三方B1/B2与v2写路径泛化不随本轮启动。生产签名、真实业务及普通Main自启不在本轮通过范围。**
>
> 更新：2026-10-30。简化目标见设计第13节、计划第12节。唯一架构：[Runtime 设计](../system/runtime-plugin-host-architecture-design.md)；当前共同契约2.11；本轮不改wire，旧记录保留原版本。本计划整合旧 M1/M2 及 Runtime M0.4，不再维护独立阶段文档。
>
> 2026-11-02更新：第15节U1界面/日志源码独测与CR通过；用户取消App签名，保留显式unsigned发行。第14节通用查看导出已实现，BOSS专用跨层采样与实包验收仍待，不改变历史交付或安装授权。

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| U1 | Runtime静默刷新、简化界面、日志入口与未签名发行 | ✅ 完成（2026-11-02） | 0.0.36含截图三项优化，94独测/CR及实际EXE通过；已授权本次提交，未部署 |
| U1.9 | 连接概览与展开详情的紧凑排列 | ✅ 完成（2026-11-02） | 81回归、独立20项/CR及宽窄视觉通过，已进入037实际包 |
| U1.10 | 紧凑连接概览Runtime0.0.37交付 | ✅ 完成（2026-11-02） | 24打包测试、独立实际验包/CR及Downloads替换通过；未安装部署 |
| 恢复 1A | 自动启动阶段诊断与现有一键导出 | ✅ 完成（2026-11-10） | 独立测试及 CR 通过，真机停稳修复留待后续 |
| 恢复 1 | Windows 旧执行停稳与原身份自动恢复 | 📋 待开发 | 先验证真实电源状态与兼容证据 |
| 恢复 2 | 单次用户配对与原身份启动解耦 | ✅ 完成（2026-11-10） | 独立测试与 CR 通过，实际安装包待验收 |
| 恢复 2B | 协议 4 准备、客户端落盘与服务端确认 | ✅ 完成（2026-11-10） | 独立测试与 CR 通过，最终安装包待验收 |
| 恢复 3A | 一键诊断补齐目录来源、连接阶段和 Windows 电源摘要 | ✅ 完成（2026-11-10） | 独立测试与 CR 通过，现场和安装包待验 |
| 恢复 3 | 实际安装包、兼容与回退验收 | 📋 待开发 | 真机验证持续心跳、领取和旧结果保留 |
| 设计/共享格式 | 当前架构、契约、schema/fixture | ✅ 完成（2026-10-09） | 架构v2.0、格式候选0.3；共同wire冻结另列 |
| 第一部分 A1 | 共用核心、管理端、CLI与受管入口 | ✅ 完成（2026-10-09） | 实际Host/DPAPI/停止/兼容验证；H1共同冻结待确认 |
| 第一部分 A2 | 三第一方离线包与安装管理 | ✅ 完成（2026-10-09） | 隔离签名实包、真实安装/重启/MCP验证；正式发行输入另列 |
| 第一部分 A3 | Runtime UI与公共Electron壳 | ✅ 完成（2026-10-09） | 实际窗口/IPC/监管、握手竞态修复、独立52项验证 |
| 第一部分 A4 | Windows发行与真实业务验收 | 🔧 进行中 | 隔离安装器已交付；NSIS/原生选框/真实配对业务及正式发行未完成 |
| 旧 M1/M2 | 服务端目录知识层与legacy设备脚本路由 | ✅ 完成（2026-10-06） | 已有代码并入兼容基线，样本Windows业务验收仍未完成，见第9节 |
| 第二部分 B1 | 第三方Skill导入、环境与AI分析 | **暂不开发** | 未开放实现；不是当前待开发队列 |
| 第二部分 B2 | 第三方Skill动态登记、执行与样本验收 | **暂不开发** | 未开放实现；保留格式/资源/结果约束 |
| v2 写路径泛化 | v2 写门禁从 weixin 白名单（WEIXIN_V2_TOOLS）泛化为 provider manifest 声明式、claim/meta v2、send_deferred 事件协议、桥注册表/桥密钥注入机制、v2 manifest 验收 | 📋 待开发 | BOSS 端侧会话计划 B3 的外部依赖（2026-10-11 登记）；协议锚点以该领域设计为准 |
| 简化 S0 | 现有设计/计划收敛与独立评审 | ✅ 完成（2026-10-30） | 四项遗漏已修订，独立复审无设计阻断；仅文档，代码/安装未验证 |
| 简化 S1 | 插件中断恢复、安装与旧任务解耦 | ✅ 完成（2026-10-30） | 独测/CR通过；历史事实保留，secret真损坏仍只读；未提交安装 |
| 简化 S2 | 通用能力发现、列表查询减负、重装回退 | ✅ 完成（2026-10-30） | 通用发现/快照/修复回退通过；旧0.0.29＋BOSS0.3.27实包只读兼容通过 |
| 简化 S3 | 覆盖升级/离线配置与干净重装 | ✅ 完成（2026-10-30） | PS→TS真实隔离组合及旧注册续接通过；原home/事实保留，未实际安装 |
| 简化 S4 | 稳定签名、短路径与受管旧包清理 | ✅ 完成（2026-10-30） | 独立组件/稳定签名/受管清理通过；114场景独测及CR无遗留，未发行 |
| 简化 S5 | 最终安装包与客户机升级验收 | ✅ 完成（2026-10-30） | 最终R2隔离升级/新装/Clean与实际Host恢复通过；原0.0.29/0.3.27配置配对保留 |
| 本地环境统一 | 原8000/master接入Web、Runner与现有Runtime | ✅ 完成（2026-10-30） | 复用原本地PG/Redis/卷；master三应用与Web API只读链路通过，旧三应用已停 |
| 诊断 D0 | 内置留存与一键导出方案/计划 | ✅ 完成（2026-10-31） | 用户明确列为后续必做；设计第14节，尚未编码 |
| 诊断 D1 | 调用/目标/原生窗口关联与有界留存 | 🔧 进行中 | 通用Main留存及旧Host安全投影完成，BOSS专用窗口关联待开发 |
| 诊断 D2 | Runtime内查看/导出脱敏诊断包 | ✅ 完成（2026-11-02，0.0.34） | 包内collector/Main/yazl匿名ZIP通过；未执行安装或真实业务 |
| 诊断 D3 | 独测/CR、真实发行包与故障包验收 | 🔧 进行中 | U1源码与实际包内匿名验收通过；专用采样和真实安装/故障未验 |


2026-10-26完整master整合：wecom工作树已将1721707f（含历史信任根>16修复、共享窗口恢复及BOSS0.3.27）整合为未提交merge，W6未提交修改保留。Runtime/CLI/BOSS/Web构建及独立组合验证完成，CR无新增未决问题，3项旧Runtime清单/digest断言仍失败；升级水位幂等补齐另经真实存量PG验收。结果唯一见[WeCom整合计划](plan-wecom-cli-group-chat-experiment.md#最新master与runtime核查2026-10-26用户追问后补核)，未部署、安装或恢复监听，不等于已安装版本验收。

2026-10-26 W6检查点：按[共同契约2.10](../system/runner-desktop-runtime-integration-contract.md)新增只读wecom_group_poll及scoped文本发送group_cursor能力门禁，复用原Runtime调度/锁/outbox，不新增本地loop。设备私有标记由Runner唯一写入，旧CLI新工具零派发拒绝；实现/隔离验收唯一见[W6计划](plan-wecom-cli-group-chat-experiment.md#w6-配置智能体完整链路2026-10-26)，禁止部署或恢复监听。

2026-10-26 wecom对账检查点：按[共同契约2.9 §5.7](../system/runner-desktop-runtime-integration-contract.md#57-wecom发送对账接线2026-10-26)追加业务工具wecom_send_status schema v1，沿原只读operation与固定原发送/设备scope；MCP/core主控、local_tools后端角色各自限域写入，实际范围/结果唯一登记在[wecom计划](plan-wecom-cli-group-chat-experiment.md#2026-10-26-mcp与runtime发送对账接线)。Device/Host/H3/Runner公开wire、原unknown/hold及outbox不变，不自动解除verification等待；旧CLI明确拒绝新能力并保留旧六工具。本地接线及独立TEST/CR已完成（PG63、TS32、单测173通过另2旧BOSS基线失败）；无提交/部署，真实群聊、长期运行及新Desktop UI未验收。

2026-10-22休眠检查点：按[共同契约2.8 §5.6](../system/runner-desktop-runtime-integration-contract.md#56-通用持久化休眠增量2026-10-22)由Runner工作流实现云端持久化timer/continuous，唯一进度见[休眠计划](plan-agent-runner-durable-sleep.md#13-本轮实施基线与共同检查点)。Runtime/Device/Host/Provider无新sleep wire或本地loop；已有invocation队列/桌面锁和固定release规则保持，真实多任务接线验收单独记录，不能由云端单测代替。

2026-10-22复审修复兼容检查点：Runner私有工具代次/累计BIGINT及历史产物退休由同一修复角色负责；snapshot可选history_revision仅为历史更新水位，Web拉取既有历史，旧消费者可忽略，Device/Host不变。实现与独测/CR结论统一见[休眠计划第17节](plan-agent-runner-durable-sleep.md#17-复审修复与验证2026-10-22)，本地Runner/Web独测及CR已通过，未部署；本条不宣称Desktop或真机Runtime已验收。

2026-10-20恢复检查点：按共同契约2.5保持v2纯读独立，新增显式v3停止本Runtime受管旧工具进程的路径，可覆盖无generation且结果永久丢失的存量BOSS调用；不重放原业务，不要求Windows重启。源码与dist已冻结，独立120项及宽窄UI通过，CR无阻断；唯一实现责任、精确范围及结果见[BOSS恢复计划Phase10](plan-boss-resume-next-recovery-fix.md)。旧2.3/v1证据仅历史，未发行或部署。

2026-10-20恢复增量检查点：按[共同契约恢复增量](../system/runner-desktop-runtime-integration-contract.md#2026-10-20-本地未知调用恢复与web结束等待兼容增量)开发Web结束等待、迟到回执对账与设备冲突阻断；唯一代码写入者为恢复开发子智能体，无Runtime客户端或Device wire变更。进度与独立验证唯一记录在[BOSS恢复计划Phase 6～8](plan-boss-resume-next-recovery-fix.md)，本计划仅引用。

2026-10-20共同检查点：按共同契约2.2及[BOSS计划23](desktop-automation/plan-boss-operation-verification-visual-recovery.md#23-runtime配对身份复用纠正2026-10-20)消除额外视觉token配置。主控唯一维护runtimeHost.ts、Desktop Supervisor、BOSS视觉client/localRecovery和版本元数据；backend身份角色唯一维护src/local_tools/api.py、boss_visual_service及必要台账代码。新增设备视觉入口，原H1/H3及配对/claim/permit不改；设备凭据只给受信BOSS，其他Provider隔离。独立TEST/CR后重打包，不部署。

2026-10-14共同检查点：用户批准[干净安装计划](plan-runtime-clean-install.md)，共同契约2.1。安装角色负责安装代际与进程清理，服务端角色负责协议3及数据库，主控负责Main/H3自动激活与renderer。替代新版历史资源恢复门，旧协议1/2不变；验证以该计划为准。

同日追加安装确认：主控接管NSIS/PowerShell唯一写入，旧客户端运行时确定才退出安装，取消保持旧运行；不改H3/wire/Device。增量验证统一见干净安装计划Phase5。

2026-10-12 r7共同检查点：[恢复计划Phase10](plan-runtime-upgrade-recovery.md#phase10-最终交付与服务端状态复核2026-10-12)已交付自助诊断/管理修复/归档新配对；主控维护Core/Runtime/Main和UI，资源角色仅维护资源helper，独立角色只读验证。H1/Device/binding不变，Runtime-only H3可选端口与私有connection v1、续装控制保持单一写入者；实际包和独立验证通过，未部署服务器。

2026-10-12 r6共同检查点：[恢复计划Phase 8](plan-runtime-upgrade-recovery.md)主控维护core management/Runtime组合层，Windows平台角色维护legacy DPAPI调用，其他角色独立验证；仅Runtime_app启用失败时只读管理，沿用H1 blocked/code11和私有退出v1，不重置身份/密钥/历史。不再要求客户补日志。

2026-10-12 r5 共同检查点：[恢复计划Phase 6](plan-runtime-upgrade-recovery.md)当前主控仅维护Runtime NSIS与旧版续装PS，严格校验后归档旧控制材料再登记新版，保留原自启选择；core/Main/H1/Device/binding无变化，独立测试角色维护安装器回归。

2026-10-12 r4 共同检查点：[恢复计划Phase 4/5](plan-runtime-upgrade-recovery.md)由当前主控唯一维护NSIS、core.dispose、managed-entry和Supervisor/Main；补恢复步骤自足性、无pending免调用、最终目录退出核验及历史记录下空闲管理进程退出。直属Main/Host私有生命周期v1绑定实例与generation；H1、Device、binding和业务结果语义不变，UAC恢复保持原DPAPI/HKCU归属。

2026-10-12 升级退出检查点：[试用包Phase 5](plan-runtime-ui-boss-trial-bundle.md)当前主控唯一维护公共NSIS退出检查和只读诊断；X保留托盘常驻语义，不再允许安装器默认终止旧进程。旧运行marker恢复仍按原停止/未知事实边界，需要现场核验，不改Host/Device wire、许可或自动重放。

2026-10-12 BOSS 冷启动增量检查点：[试用包计划Phase 4](plan-runtime-ui-boss-trial-bundle.md)登记当前主控唯一写入共享store入口选择及Runtime组合层；可选bootstrapKeys默认兼容，仅BOSS已验签manifest保留冷启动入口，不把doctor未登录当未安装。Device/H1 wire、binding、许可和版本保持。

2026-10-12 共同检查点：[老用户试用组合包](plan-runtime-ui-boss-trial-bundle.md)由当前主控唯一写入公共 Electron main、自启适配/测试及打包白名单/NSIS钩子，复用既有 Host/BOSS。仅登录启动与正常管理 start；不改 wire/binding/许可/core，v2.0与候选0.3保持；未签名验收版独立home，不能冒充正式迁移。

2026-10-11 MCP 大结果回传检查点：唯一写入者为 [BOSS/Runtime 修复会话](desktop-automation/plan-boss-card-coordinate-precision.md)，仅修改共享 core `src/legacy/providerManager.ts` 及 Runtime 传输测试/fixture/helper。SDK 1.30.0 的 10MiB 默认接收预算由有限 128MiB 替代，受信内部选项仅可收紧到 1–128MiB；增加脱敏超限诊断，超限关闭与 unknown 禁重放不变。旧构造调用兼容；不改 Device/管理 wire、binding、许可、公共壳、依赖或 H4 新产物协议。契约 v2.0 / wire 候选0.3不变；进度与验证以链接计划 Phase 5 为唯一来源。

2026-10-11 共同检查点：BOSS 续筛业务契约 v1 见[续筛计划](desktop-automation/plan-boss-screening-continuation.md)。本任务主控独占 `clients/shared/local-tool-host-core/src/legacy/providers.ts`、`providerManager.ts` 的新工具注册、续筛能力前置门禁及对应测试；新增 `boss_resume_next`，原工具兼容，旧 Provider 明确拒绝新工具。不修改管理 wire、binding、许可、公共壳或本计划第二部分。

## 1. 范围与停止条件

第一部分为独立 Runtime UI、BOSS/weixin/wecom 按需离线安装管理及原云端执行兼容，成功安装默认启用，就绪由真实环境决定。安装器不预装业务插件，客户机无需 npm/Git/系统 Python。

不并入完整 Desktop 对话、通用文件/Shell/PTC、在线市场或第三方MCP分发。第三方包在安装/依赖构建/源码上传前拒绝，不显示无效占位入口。保留 legacy skill-runner 不等于实现新的第三方安装机制。

本轮完成代码和隔离验收交付，停止自动实施。A4 剩余人工项据实保留，不因提交推送登记已验收。后续部署须用户明确当场指定目标；提交不是部署授权。

## 2. 成功标准与剩余门

已验证空核心、配对管理协议、三包单独/组合安装、只读就绪诊断、启停/卸载、重启恢复、去重、实际child退出、旧执行/结果兼容及固定Node发行资源。

仍需人工确认 NSIS 安装、原生选择框、现有服务真实配对、业务软件登录、授权只读云端任务和干净客户机。正式发行还需正式trust root/发布签名、Windows证书、内部Python安全和CRT许可资格。

H1 producer/真实H3 consumer已有证据，整体wire仍候选0.3；H2新任务设备/workspace/许可与H4新登记/资源/产物未冻结。这些不是已上线接口，缺输入不启用对应能力。

## 3. 实施阶段

### 第一部分：Runtime UI与第一方CLI插件

| 阶段 | 已交付实现 | 保留限制 |
|---|---|---|
| A1 | Node core exports/build、管理producer、薄CLI/bootstrap、lease、配对事务、既有Device执行、drain、结案证明 | 历史无可信证明/unknown继续拒绝身份切换；无任意renderer业务调用 |
| A2 | envelope/payload签名校验、原子store、受控选包冻结、持久operation、升级/停用/卸载、三自包含包 | 仅批准第一方；旧受信工具子集不扩大；正式发行另需批准输入 |
| A3 | 配对/连接/插件页，preload/main/监管/私有IPC，产品身份与固定Node，托盘与退出门 | 完整Desktop对话不在本批；外置无受控通道只报冲突 |
| A4 | Windows隔离acceptance安装器、完整资源验包、实际包EXE启动/退出 | 人工与正式发行门见第2节；不承诺已完成业务真机验收 |

### 第二部分：第三方skill（暂不开发）

**B1/B2 不属于当前开发清单。不得以通用化、预留接口或第一部分依赖为由提前编写安装器、AI分析器、新云端登记/执行链或UI入口。**

B1 保留唯一技能根、安全解包、原包/sidecar、环境/可变状态、SKILL.md优先与必要源码云端分析、实际入口/code_map验证。

B2 保留设备私有契约视图、调用版本固定、资源/取消/unknown/原结果ACK、新产物/图片进入模型及WorkBuddy实包验收。现有fixture不能代替真实样本执行。只有用户明确启动第二部分才更新状态和实施。

## 4. 开发与验证流程

非平凡代码按项目开发流程独立测试和CR；纯文档整合核对代码、约束、索引与链接，不机械重复业务测试。只运行受影响检查，不扩大权限消除基线失败。

共同格式变更先登记唯一写入者、版本/兼容方案与共同检查点，再验证真实producer/consumer。不能把candidate通过或临时tgz构建成功当正式发行完成。

## 5. 文档与索引

只维护一个Runtime架构和本计划。共同契约、schema/fixture、Provider标准、Desktop设计及构建手册按各自职责保留。

旧M1/M2调研设计/计划与M0.4实施规格已整合删除，索引不再有独立M1/M2待办。领域sessionTasks、微信/BOSS自动化不删除，其Host依赖指向当前架构。进行中总条目保持“部分完成”，原因是A4及共同/正式发行门仍未关闭。

## 6. Desktop / Runtime 共同边界登记

| 检查点 | 主导/唯一写入范围 | 当前状态 |
|---|---|---|
| H1管理port/wire | Runtime：contracts/runtime-host/v1、core管理producer；Desktop消费验证 | 候选0.3真实两端已验证，未共同登记冻结 |
| H2设备/workspace/许可 | Desktop主导服务端与共享输入；Runtime消费核验adapter | 未冻结，不自行补协议 |
| H3公共壳/实例监管 | Desktop：main/preload、选择/监管、package/lock、Node/产品构建和全局入口 | 首期隔离Runtime产品已交付，人工项待验收 |
| H4插件描述/结果/资源 | Runtime主导，与Device/Runner/Desktop共同验证 | 描述/输出格式已交付；新登记、资源及产物链未完成 |
| Runtime实现 | core、Host/CLI/bootstrap、三包构建与必要CLI路径、runtime feature | 本批已提交；不由Desktop重建第二套core |

## 7. 当前接口与依赖

格式唯一入口为[contracts/runtime-host/v1/README.md](../../contracts/runtime-host/v1/README.md)，实际行为见[Runtime设计第11～12节](../system/runtime-plugin-host-architecture-design.md#11-共用核心与接口实现)。api_major/schema_version为1，与共同契约v2.0、候选0.3和产品版本分别管理。

列表返回实例/revision/plugins、可选真实version；请求固定code/error；operation与业务任务分开。消费方共享水位、处理换实例/旧连接/同revision乱序，并保留握手期间通知。

管理mutation依request_key持久去重，选包引用与外包快照走私有Main-child callback，不加H1字段。固定Node/信任资源来自H3产品输入。缺官方生产输入不能用测试根补位。

## 8. 首期实施约束

七项规则已落实于[设计第12节](../system/runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)：离线签名/依赖、安装与ready分开、升级排空、legacy优先级、持久去重与水位、停止/配对、唯一文件责任。

固定Node22.23.3/ABI127。包检查顺序进行；管理查询/初始化等待120秒，与30秒drain观察及业务许可期限分开，超时不kill、不续权。当前性能证据不覆盖低内存/冷磁盘认证，压力刷新曾达86.6秒，不能宣称所有客户机刷新都很快。

## 9. legacy兼容与未完成样本验收

旧M0.4已经成为core中的Device/MCP/DPAPI/桌面检查/结果链，原“必须捆绑BOSS、5秒强退”等规格不作为现行为。旧pack脚本对新布局先拒绝；新发行走Desktop标准构建链。

M1知识层代码完成，历史证据为257单测及26集成通过；M2云端/设备代码完成，收尾证据为239云端用例、60设备受影响用例及typecheck通过，旧进度“评审未收口”已被其终态复核记录替代。以上是历史记录，本次文档整理不重新宣称跑过。

现代码仍使用服务端目录、审批/hash/entries、device_execution开关与本机skills.dir/python；新v2.0路径不能照搬这些安装/审批要求。旧门槛暂按代码保留，不能删除文档就取消执行检查。

WorkBuddy jingpian样本Windows Python实际执行、可交互桌面/进程树收尾与截图/业务闭环未验收。该遗留随本计划保留，不单列待开发，不重新启动B1/B2，也不拿第一方包验收代替第三方样本。

## 10. 最终交付与验证记录

### 10.1 第一方最终隔离资产

仅使用clients/runtime-plugin-packaging/release/final-acceptance的三包与对应ISOLATED-TEST-TRUST-ROOT.json；旧调试根不能混用。

| 包 | 字节数 | SHA256 |
|---|---:|---|
| boss-0.3.0-dev.aidplugin.zip | 4442404 | `6c1f4578498ff23ab8138b5fb1c51994f4dbd209a7bf1754e43d8726bce73d11` |
| weixin-0.1.0-dev.aidplugin.zip | 117056592 | `c3f17d0460aa596b273c76b24299d74b7cda0a96af3c07c86564f9af7c1285f3` |
| wecom-0.1.0-dev.aidplugin.zip | 117215512 | `35dfd1b23ae2dab194763dbb4dae5ea80a4f9e02c136d32887a5524b68f9a1dc` |

manifest版本0.3.0/0.1.0/0.1.0；完整MCP工具21/8/10，Host子集不扩大。BOSS0.3.0沿用此前已有源输入，不称本批原创升级。

微信/企微内部Python/OCR/模型/CRT随包，使用-I -B避免缓存写入。离库、系统PATH下真实version/doctor及合成图片OCR验证通过，执行前后文件集合/摘要不变；未发消息或点击业务GUI。

### 10.2 独立验证与已知限制

| 范围 | 本批记录 |
|---|---|
| Runtime/core | Runtime251/251、core30/30；固定Node22 trust/proof/session10，无跳过 |
| 独立离库Host | 最终f52b真实Host/DPAPI、三包import、冻结副本/原key过期ref重试、停用启用、两次重启/卸载退出0；另seq32/negative10通过 |
| 真实MCP | 三实际payload SDK initialize/tools/list与签名manifest逐字段一致；关stdin自然exit0，无kill/残留PID |
| session恢复 | 9项proof/真实terminal-fullACK/drain、engine/retention34通过；旧/篡改/部分ACK/unknown拒绝结案 |
| UI | 最新feature52/52与desktop typecheck；握手高水位缓存、reset/旧代/溢出回归，独立CR无遗留P0/P1/P2 |
| 公共壳/最终包 | 实际Electron三包/重启/退出22断言；最终win-unpacked启动/退出12断言、无残留；标准验包3682资源一致 |
| 打包验签修复 | PS7→PS5.1模块路径隔离，独立10/10及CR；NotSigned/Valid策略不变 |

独立CR核对76编译文件与最后f52b Host一致。离库Host末次安装约2.04/4.79/4.57秒、清单5.43～5.57秒；压力及内存限制见第8节，不能当客户机性能保证。

三CLI定向BOSS46、微信相关27、企微相关19、OCR取消5、打包3通过。全量CLI不全绿：HEAD可复现微信2项、企微2项环境/mock基线失败；微信全collector有send竞争，定向12项当前与HEAD均过。提交前Python相关独立28通过/1失败/0跳过，失败为HEAD也有BOSS catalog21/Host18差异；保持权限不扩大。

### 10.3 Windows人工验收交付

| 项目 | 最终记录 |
|---|---|
| 安装器 | [AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe](../../clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe) |
| 字节数/SHA256 | 126241075 / `68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd` |
| 构建输入摘要 | `d12b14e1758bdba88ffaff6d79d4e17a68181e3f3d947e21e3f6616bb3e76aff` |
| Host | 0.2.14 / `f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f` |
| 发行记录 | [runtime-release-manifest.json](../../clients/agent-desktop/build/runtime-release/acceptance/runtime-release-manifest.json) |
| 人工步骤/三包 | [构建手册12节](../system/desktop-agent-client-build-manual.md#12-独立runtime首期验收包) |
| H3证据 | [Desktop计划9.7](plan-desktop-agent-client.md#97-第一部分h3实施登记2026-10-09) |

独立验收产品cn.aidingyi.agent.runtime.acceptance，Windows签名NotSigned；三插件自身仍验发布者签名。最新installer已包含握手修复，旧安装器/临时Host候选不作为交付。

自动化替代了原生dialog返回值，随后Main文件冻结/IPC/Host验签真实。NSIS安装、OS选框交互、真实服务配对和软件登录/业务仍人工：安装打开→现有服务地址/设备名/一次性码配对→空核心连接→原生选包→登录软件并刷新→停用/重启/卸载→已授权只读云端任务→托盘恢复/安全退出。

### 10.4 提交与线上影响边界

已提交并推送`f1087289`，156本任务文件；未部署。用户明确master推送不会自动部署，手动部署需另授权。服务端src、Web业务/构建配置、微信KF实现及部署配置未纳入本批；Web完整build通过，244源/523模块无Desktop/native依赖。旧pack布局前置拒绝通过独立验证，不删除legacy入口。

源码提交不使线上服务或已装客户机自动切换版本。首期本轮停止；整体索引仍部分完成，A4人工/正式发行及共同wire未冻项保留，B1/B2暂不开发。

## 11. 本次文档收敛

### 2026-10-21 打包操作手册整理

已完成纯文档整理：[Runtime 与 BOSS 插件构建及 ZIP 交付手册](../ops/runtime-boss-release-guide.md)作为当前出包入口，覆盖版本/原生依赖、固定 Node、BOSS 签名及历史根、正确 cwd 下的 Host pack、Runtime 安装器、实际包验证、安装说明和 clients/release 最终 ZIP。clients README、插件 README、旧构建手册第12节及历史安装说明均跳转至该入口。11段PowerShell在5.1/7均语法通过，62个本地链接及新增相关锚点有效，5个npm script名称、6个固定工具路径及pack/prepare参数与源码一致，diff检查通过；动态版本产物路径不要求本轮存在。未执行构建、安装、模型调用、签名登记、版本修改或出包，未提交/部署。没有改运行时策略、公共 wire 或共同契约版本，不另建开发功能。


2026-10-09按用户要求，将旧M1/M2任务与Runtime M0.4合入本计划/唯一架构，删除过时独立调研设计与实施计划，替换入链引用。将代码完成与未验收分开登记；原Server审批/hash事实保留，未实现第三方新机制不改状态。不改执行逻辑、共同契约语义或schema版本；用户已明确要求提交本次整理，不部署。

文档核对：43个Runtime相关本地链接/锚点、索引唯一性与B1/B2状态检查通过；删除文件的旧引用已清除。源码/测试/配置仅修注释指针，15个Python文件排除docstring后的AST与HEAD一致，TypeScript去注释和YAML非注释内容在替换前后相同；定向git diff --check通过。纯文档工作未重跑业务测试，工作区其他任务修改保留。
# 2026-10-12 升级恢复共同检查点

Phase9连接恢复审计及源码补漏以[恢复计划的状态矩阵](plan-runtime-upgrade-recovery.md#phase9连接失效与恢复路径的确定行为)为准：H1仍为候选0.3，reconciling原原因使用已有message字段；当前state/执行所有权/身份门保持，修复历史operation锁住明确新操作及BOM启动/ACK读取。主控唯一写入，独立测试/CR只读产品。原身份资料损坏、永久身份标记和旧未结案证明目前没有完整自助出口，不把r6管理/退出成功算作连接恢复；新身份reset及对应wire尚未实施。

已登记[覆盖升级与自动恢复](plan-runtime-upgrade-recovery.md)：core内可选适配钩子、本机v2 marker/DPAPI监督记录及受信Node→Main私有恢复IPC；H1 getState/schema保持不变，唯一写入者和兼容方案见该计划，Device/wire授权不变，实施前检查点完成。


### 2026-10-13 Runtime r8 只读故障入口检查点

主控唯一修改Runtime页面/Controller及测试、Desktop版本元数据和交付说明；H1/H3/Device及恢复事务语义不变。不支持插件时不误查询、不假刷新，恢复步骤首屏可见且不受检查忙碌禁用；详见[恢复计划Phase11](plan-runtime-upgrade-recovery.md#phase11-实施前共同检查点2026-10-13)。独立真实包故障/恢复交互与审查待完成。


2026-10-20恢复纠正（优先于上方历史Phase10完成记录）：按[共同契约2.5](../system/runner-desktop-runtime-integration-contract.md)保留独立纯读，并新增显式停止本Runtime受管旧工具进程的路径，覆盖存量无generation且回执永久丢失的BOSS调用；不重放原业务、不要求Windows重启。独立120项及宽窄UI通过，CR无阻断；唯一进度见[BOSS恢复计划](../plans/plan-boss-resume-next-recovery-fix.md)，未部署。

## 12. 安装升级简化实施计划（2026-10-30）

### 12.1 当前授权、依据与成功标准

本轮仅完善现有设计/计划及独立设计评审，**不写代码、提交、安装、部署或恢复监听**。S1～S5仅列为后续开发队列，不能以计划完成获得实施授权。依据[独立评估](../research/runtime-cli-install-upgrade-simplicity-review.md)、[设计第13节](../system/runtime-plugin-host-architecture-design.md#13-安装升级简化目标2026-10-30)。不另立同题计划，不启动第三方Skill或v2写路径泛化。

2026-10-30后续授权替代上段当前范围：仅S1/S2在master开发、自测、独立测试及CR，修复到通过；不提交、安装、部署或恢复监听。主控维护management.ts、runtimeHost.ts及对应管理/Runtime测试和文档；限定开发子智能体唯一维护store.ts及pluginStore.test.mjs，结束后主控接管；独立测试/CR不写产品源码。不实施S3覆盖升级或S4签名/GC。secret不可解密/operations真正损坏时不自动重置幂等身份，保留原只读故障与退出/修复出口，该收窄经独立设计审查确认。

完成实施后的成功标准：

- 服务端单独升级，**原包 Runtime 0.0.29＋BOSS 0.3.27 不升级、不重配对**，工具及原任务恢复、回执、计费仍可用。
- 普通覆盖升级复用有效home/身份/插件；断网保留配置，网络恢复正常认证，不每次生成新代际。
- 插件安装中断可回到已提交状态；无关历史unknown不阻断安装/连接；真实未停止执行仍不能被新版本抢占。
- 已安装未登录仍可发现工具，失败指向登录/重连/重装等具体动作；列表不触发全量验包和doctor。
- 明确重装/回退可用，当前有效版本失败不丢失，旧未知发送不重放，旧包清理不丢回执。

### 12.2 分批实施与文件责任

主控是产品代码与文档唯一写入者；后续代码开发按高风险流程安排独立测试与独立CodeReview，只读产品代码。每批修复到通过后再推进；检查已有证据与依赖，避免每批重复全量构建。以下为预计涉及文件，具体diff按实际问题收窄。

| 阶段 | 最小实施范围 | 主要位置 | 完成条件 |
|---|---|---|---|
| S0 | 更新既有设计、计划、契约交叉引用，独立审查兼容和简化边界 | 本计划、Runtime设计、共同契约、安装/恢复计划、Desktop设计/计划、ideas索引 | 无未解决设计阻断；代码仍未改 |
| S1 | pending依据已提交inventory收敛；解除可读历史journal/Host marker/旧管理operation的全局门禁，保留实际停止/旧调用关联 | core `plugins/store.ts`、`management.ts`；Runtime `runtimeHost.ts` | 无当前ownsExecution仍可复核旧marker；真正坏密钥/管理记录保留只读，不新增恢复数据库/服务 |
| S2 | installed/readiness通用分离；列表读取快照；同包损坏原子修复与明确回退；复用原错误 | 上述store/Host、`pluginProbe.ts`；Runtime controller/page仅改受影响提示 | BOSS/WeCom同规则，旧受信子集不扩大；旧H1调用仍可消费，无新增强制字段 |
| S3 | 覆盖安装保留兼容home；离线保留配置；坏状态走既有干净安装出口 | Desktop `runtimeInstallation.ts`、installer/clean-install脚本、必要Main/恢复adapter | 同一身份覆盖升级与显式干净重装分开；旧current指针可读，旧任务不因目录变更失去结果事实 |
| S4 | 稳定签名者与独立版本发行；仅新安装短路径；清理旧release/终结操作冻结ZIP与暂存 | `release-batch.ps1`、`prepare-runtime.mjs`、现有packaging signer、store/Host/installation及构建手册 | 不改已交付release内容或删除未清点根；保留轻量去重/事实，不新增共享依赖/信任分发/GC平台；清理失败不挡使用 |
| S5 | 集成后的独立测试、CR、隔离最终安装包验收；删已无调用的新增期冗余分支 | 各阶段定向测试、隔离Windows/包目录、文档完成记录 | 下面最小矩阵有真实证据；未测项如实标注；不自动提交部署 |

S1/S2先解决客户卡点，不依赖路径/签名改造。S3/S4沿用旧schema和已交付根，避免“大迁移完成才能修一个卡点”。S5复用各阶段有效证据，仅补集成与实际安装器验证；不把安装器验证分散到每个状态小修都重新发包。

不预计修改服务端API、数据库或Provider业务schema；若发现确需改共同字段，先单独登记兼容方案并重新评审受影响部分，不能借本计划顺便替换协议。错误信息优先使用现有字段，不先设计新错误类型系统。

### 12.3 实际进程、旧任务与中断边界

| 边界 | 目标行为 | 禁止的捷径 |
|---|---|---|
| 切换前自己的执行仍活着 | 原关闭/停止链核实并停止；确实不能停止只阻止相关替换，保留管理诊断 | 仅凭unknown认定活着；批量kill Node/Chrome；建另一套监督机制 |
| 旧任务unknown但已无活动进程 | 安装/重连可继续；原invocation保留unknown与证据、只读对账 | 删除journal当作成功；所有插件和连接永久封锁 |
| 插件复制后、inventory提交前崩溃 | 继续旧有效记录；未提交暂存可隔离 | 为“恢复”重新执行原业务动作 |
| inventory提交后、清标记/回响应前崩溃 | 验证已提交release，按原操作证据查询；不能证明成功则明确返回可重装 | 残留pending锁死全局；仅清标记就报成功 |
| 覆盖升级时有未ACK结果 | 保持原home/身份/回执；按原幂等链补传 | 当新任务再执行一次，或提前回收对应release |
| 重配对/干净重装变更身份 | 旧材料按原身份留存，可信旧身份替换走已有协议3；新连接不回放旧任务 | 新token代传旧结果；凭机器名注销别的设备 |
| 服务器发布、断网或短暂认证服务错误 | 云端原checkpoint/调用ID恢复，本机待连接；迟到结果只结算一次 | 强制升级客户端；重建业务invocation；超时就删凭据 |
| release清理或归档失败 | 保留事实、明确维护警告；新有效安装仍可用 | 清理失败升级为全局启动门禁 |

历史恢复分支只在确认没有实际交付依赖后删除；仍被原设备、未ACK结果或真实调用使用的路径继续支持。最初不要求把所有旧generation迁到短目录。

### 12.4 最小验收矩阵与证据复用

仅验证实际交付组合，不向更老版本或假想损坏组合展开。数据和运行目录独立，禁止触碰当前客户机进程、配对、插件目录、业务聊天或监听。

| 验收组 | 真实意图与范围 | 证据要求 |
|---|---|---|
| C1 服务端兼容旧端 | 归档原Runtime0.0.29＋BOSS0.3.27，实际MCP工具可列出、已有调用形状可执行；新功能缺失不隐藏旧工具 | 原包校验和、实际core/MCP响应；业务执行用隔离无副作用通道，不能称真实招聘动作已验收 |
| C2 旧任务跨版本恢复 | 旧服务端producer的checkpoint/旧BOSS配置经新服务端恢复，原invocation/设备不变；成功只结算一次、unknown不重发；普通服务端重启可继续 | 复用W15生产producer/真实PG夹具与W14.6/W16旧包证据，影响这些实现/依赖时重跑；字段写成旧版本不算原包验证 |
| C3 升级与重装 | 旧包0.0.29覆盖升级保留身份/插件/未ACK事实；离线保留配置；损坏控制材料能干净重装；旧账号事实不转移 | 隔离Windows用户/虚拟机与自有服务，最终NSIS真实执行；测试旧状态不触及当前安装 |
| C4 插件维护 | 提交前/后中断各一例；旧unknown/Host marker且ownsExecution=false不挡管理，真实活动不能被覆盖；健康同包幂等、损坏同包修复、明确回退、失败旧版可用 | store＋真实management/Host调用链，不能只测journal helper；最终包代表性一例，不穷举复制时点 |
| C5 能力与成本 | 未登录仍发现受信工具并获得可恢复错误；列表不重复解包/全目录校验/起doctor；坏包不执行 | 实际管理调用/调用次数与进程证据，不只mock一个ready布尔值；无需先登录业务或发送消息 |
| C6 发布与清理 | 同一发布者连续两个兼容插件包可由支持该发布者的原Runtime安装；旧包仍验签；release及终结操作冻结ZIP清理保留活动引用、去重摘要和原回执 | 自有隔离签名/安装根及包摘要；签名者兼容不等于旧Runtime能执行任何新协议 |

W15/W16证据源见[WeCom既有计划](plan-wecom-cli-group-chat-experiment.md#w16-master客户端打包改动复核2026-10-30)。复用时记录对应代码/依赖/配置和范围；新改动影响其语义则旧结果失效。正式客户交付还需原有签名/许可与实际Windows验收，本次设计评审不冒称完成。

### 12.5 设计评审记录

2026-10-30：主控完善现有设计/计划，独立审查者 `runtime_simplicity_design_review` 只读文档与必要源码。首轮发现P1一项、P2三项，主控修订后独立复审通过，无遗留设计阻断：

| 发现 | 原实现依据 | 本轮文档修订 |
|---|---|---|
| P1 管理层历史阻塞遗漏 | management marker/reconciling/ownsExecution=false分支会继续挡插件维护及stop | S1覆盖Host marker、旧管理operation/secret与无当前执行所有权的管理出口；C4用真实管理调用链 |
| P2 同包修复遗漏 | store同release同摘要只checkRelease后return，坏payload不修复 | S2区分健康幂等与损坏同包原子重装，纳入原同包验收 |
| P2 导入ZIP持续增长 | runtimeHost每次冻结management-imports完整ZIP，管理操作持有引用 | S4/C6覆盖终结操作不再需要的冻结ZIP/暂存清理，保留轻量摘要/去重事实 |
| P2 旧包无限保留风险 | 历史JournalEntry没有release_id，不存在历史release引用索引 | 只保留已有可定位的真实执行引用；历史unknown独立留事实，不倒建引用框架或锁住全部大包 |

复审确认旧Runtime0.0.29＋BOSS0.3.27的原工具/任务恢复/回执/计费兼容要求保留；没有新增强制升级、安装联网验收或全局结案门禁。S0仅设计通过，S1～S5仍待开发授权。未运行安装、重启、业务工具或代码测试，不宣称旧端重新实测或产品安装验收通过。主控新增/改动文档本地链接核查及定向diff空白检查通过，工作区其他任务改动保留。

### 12.6 S1/S2实施与验证（2026-10-30）

用户后续明确授权仅实施S1/S2，本节替代12.5中的历史“待开发授权”状态。master基线5fafa3d0，fetch后origin/master与HEAD为0/0；保留其他任务未提交文件。不提交、不安装、不部署、不恢复监听。

实际责任：主控修改core management、Runtime Host、管理/Runtime测试、Runtime页面两处说明及文档；限定开发者只修改store、package签名共用helper与pluginStore测试；独立测试与CR只读产品源码，修复由对应写入者完成。

- S1：pending以原子inventory为准，不重放旧安装；清标记失败保留可用快照及reason，后续写入重试。插件局部code11不再将Host永久变为reconciling；旧marker允许在明确维护/stop时再次复用原停止证明，ownsExecution=false不单独作为放行依据。历史journal/session/outbox不再是全局安装前提，实际活动仍先排空。
- S2：所有已验签manifest通用发现，ready只作诊断；list不再调用refresh。启动仍完整校验后构建ProviderSet。健康同包幂等、损坏同包原子修复；明确同版本更换/回退可用，当前及仍保留发行包同ID异摘要拒绝。新增helper仅读≤64KiB签名envelope，不解历史payload或新建台账。页面说明改为“最近检查”，未新增交互/错误协议。
- 必要边界：真正不可解密的secret、损坏operations或不可解析根inventory不猜测重建，保留既有故障/退出/后续S3干净重装出口。未修改服务端、Device/H1/H3/Provider schema、BOSS业务源码、依赖锁/版本、安装器或签名发行根。S3/S4及最终安装包验收均未执行。

主控初轮core39项、Runtime9项定向检查及Runtime build:main通过；开发者后续store15＋package7专项通过，重叠不累加为新增测试数。Web build及Desktop build通过（各自依赖边界/产物核验通过），只有原有npm配置/Browserslist及Web大chunk提示，无构建错误。

独立CR首轮发现P2两项：pending清除失败不应阻断已提交有效安装；新允许回退后须验证仍保留的同release身份。修复后增量复审无遗留P0/P1/P2；签名校验复用现有函数，不引入历史登记框架。独测另发现旧early-describe用例还要求初始化失败立即退出，与本轮未改变的r8只读模式矛盾；仅修测试为真实子进程握手可读、blocked原因可见、拒绝写及伪造空列表、原pending保留且正常退出，独立CR已复核，未改产品迎合旧断言。

独立测试最终结果：使用原交付Node22.23.3，core **57/57**、Runtime **16/16**、真实子进程＋store＋H1组合 **3/3**，共76个唯一node场景，0失败/取消/跳过；包含未登录可发现/具体恢复原因、清单提交前后中断、清标记失败不阻断有效快照、同包修复、回退发行身份、原完成证明与故障只读退出。Runtime build/tsc通过；Web及Desktop构建由主控验证，不重复计数。

原包兼容两批：Runtime **0.0.29/core0.2.20** 与本轮 **core0.2.21** 分别验签并实际加载原BOSS **0.3.27**，真实MCP tools/list均25项且名单相同，业务call为0；原归档7559文件前后hash一致。服务器、原恢复/计费源码未改：W15三项产品hash匹配，八项freeze中七项字节匹配，provenance唯一差异按已知CRLF→LF重建校验一致；按该范围复用原49unit/15PG证据，本轮未重跑数据库，不把版本字段或工具列表冒充业务发送/招聘动作验收。

证据：本机Temp `runtime-s1s2-independent-test-9mwcb94h/FINAL-INDEPENDENT-TEST.json/md`，JSON SHA256 `624c8a43ec15a28bfa0820c9454ce91ce4fbc7544ff0510d8a87a70f6eb17fef`；同目录`final-freeze.json`冻结9项产品/测试文件。独立CR角色`runtime_simplicity_design_review`两项P2修复复审通过，末项只读故障测试修订另经增量复核，无遗留P0/P1/P2。主控核对最终源码/报告摘要、文档链接和定向diff检查，不机械重跑相同状态已通过的测试。

交付范围仅S1/S2源码与文档，原密钥保护、认证、原结果/计费/未知不重放保持；必要的损坏根元数据或secret故障只读出口不冒称已修复。S3/S4、最终安装器/客户机实际升级、新版真实BOSS/WeCom业务及监听未验收；未提交、推送、安装、配对、部署或恢复监听，当前客户端未替换。

### 12.7 S3/S4实施登记（2026-10-30）

用户授权仅开发S3/S4、自测、独立测试及CR，禁止提交、安装、部署或恢复监听。master仍5fafa3d0，fetch后与origin/master为0/0；前轮S1/S2未提交修改继续保留。所有实际安装器执行和客户机升级验收留待S5另行授权，不用测试授权替代安装授权。

唯一写入责任：S3限定开发者维护runtimeInstallation、clean-install/installer脚本及现有定向测试；S4发布角色维护release-batch/验包脚本、相关测试和发布手册；主控维护core store/management、Runtime导入快照收尾与相关测试、设计/契约/计划。无重叠写入；公共Main如确有必要先统一登记，独立测试及CR只读产品源码。

范围：普通Finalize保留已有v2 pointer/home及身份，只有显式Clean/新安装分配短目录；旧generation读取继续兼容，原注册续接不改。S4采用现有外部signer/profile，组件分别构建/版本选择，不每批制造临时信任身份；旧根原样保留。插件安全停止后提交版本时保留当前与上一版；只清理受管UUID release及不再需要的management-imports冻结ZIP，原operation轻量摘要与未终结输入继续保留；不新增GC服务或持久历史索引。

实现核查纠正初评：原validIdentity已将断网/超时保留为可复用，仅401/403拒绝；S3对此补真实离线/拒绝回归，不新增三态认证框架或重复迁移逻辑。坏secret不自动轮换，用现有明确干净重装出口；旧事实留原home，新身份不代传原outbox。

实际落地：普通Finalize沿用有效v2 pointer、安装ID、home和initialized，二进制变化不迁配置/插件；每机器安装的全局stamp由另一账号改变时，同安装目录也保留本用户原pointer。新安装用短`userData/rt`；显式`/Clean`或更换连接使用短`connections/<UUID>`，继续读取旧generation布局。未完成注册不能选回已经替换的priorHome，Main沿原fallback在新目录安装原注册身份，原request/device不重建。单包迁移失败仅报具体白名单provider提示，不再清空已迁好BOSS。

发行脚本沿原入口增加Component Runtime/Boss/Both与独立版本、Signer/TrustRoots/Profile，取消默认双patch和批次随机根/历史快照写入；Plan仅只读解析，在外部工具、版本写入、私钥读取之前返回。默认验收根和所有历史根本轮均未改；稳定根首次登记沿原公开根入口，不另造分发协议。原Python验包省略参数仍兼容隔离测试根，显式公开根支持既有acceptance/production，production仍拒test_only及错误provider授权。完整真实signer/安装包发行未执行。

存储收尾：受管安全停止后的成功导入保留当前与上一版；卸载先提交轻量tombstone，再回收本provider所有大包，其他provider及未知业务facts不动，清理失败仅警告并可重试。management-imports仅保留仍running/reconciling的冻结ZIP；终态先持久化再回收，request_key/摘要/结果仍可去重。并发prepare尚未持久接单时跳过收集，避免删除另一个准备中的输入。没有新增定时GC、恢复服务、历史索引或必需wire字段。

独立测试使用原交付Node22：**114个唯一node场景，0失败/取消/跳过**。构成为core60、Runtime17、Desktop安装12＋恢复意图4、打包11、Plan2、NSIS仅编译1、自有真实组合7；增量重跑不重复计数。真实组合覆盖PS Finalize→TS prepare普通/干净安装、另一账号stamp变化、默认网络异常/503保留而401/403拒绝、实际BOSS签名包先迁好后另一包导入阶段失败、并发ZIP清理、原监督证明与启动意图、短home未完成注册经实际RuntimeCleanConnection/注册store/H1续接，以及实际卸载回收与原包重装。Desktop tsc与Runtime build/typecheck通过；本阶段没有前端源码变化，S1/S2对应构建结果按未变范围复用。

旧端兼容：原Runtime **0.0.29/core0.2.20** 与当前core均验签并实际加载BOSS **0.3.27**，tools/list同25项、业务call为0；原归档7559文件在测试前后对W16原摘要一致。服务端及恢复/计费相关产品文件未改，W15三项产品hash及已知provenance换行修正核对后，仅按原范围复用49unit/15PG证据，本轮未重跑PG，不把MCP发现当招聘动作/消息发送验收。

独立CR修复并复审通过四项：production多余默认acceptance验包路由、短home旧注册回选抵消Clean、跨账号全局stamp误迁本用户home、卸载仅打标未释放大包；最后移除测试驱动的多余认证catch，默认离线与认证拒绝语义保持原样，测试覆盖真实默认路径。无遗留P0/P1/P2。证据：本机Temp `runtime-s3s4-independent-test-ilimy0w9/FINAL-INDEPENDENT-TEST.json/md`，最终JSON SHA256 `c646ff7c9ee641aaf1b1b95c52ea832db5c1b7ea94f4b92d294c1b157b10b150`；19项产品/测试冻结摘要见同目录报告。主控核对报告/源码摘要、定向diff与文档链接。

范围限制：清理界限是当前home受管包；多次显式Clean遇journal/outbox/监督或未知事实会保留整个旧home，不能称所有历史磁盘占用全局有界。旧自定义`skills.python`未纳入完整Provider Job监督，其同boot停止证明限制保留，不假报所有自定义Skill都能无重启恢复；原包不带客户本机config，不能推断客户从未配置过Python。该限制不扩大成全局历史任务结案门禁，用户仍有现有明确Clean出口。本轮不运行实际安装EXE、Stop现用客户端、完整真实签名发行、真实BOSS/WeCom业务或监听；未提交、推送、安装、部署，当前客户端未替换。S5实装仍待用户另行授权。

### 12.8 S5打包与隔离安装验收（2026-10-30）

用户后续已明确授权S5，替代上节“实装未授权”的历史状态：打包最新Runtime及插件，真实隔离安装/升级，不影响现用客户端；仍禁止提交、部署、恢复监听。主控负责版本/锁及构建修复、公开发布根和文档，独立验收角色负责自有fixture真实安装/升级与报告，独立审查者只读核对隔离边界、源码/包输入和结果。

新验收版版本分别为UI0.0.32、Host0.2.25、Core0.2.22、BOSS0.3.28、WeCom0.2.2，不覆盖已有同号发行包。固定外部验收签名者`aidwork-acceptance-cli-v1`首登一次，范围仅BOSS/WeCom，旧23根原样保留；私钥在仓库外构建专用目录，按既有外部signer格式及Windows ACL保护，不进入源码/产物/日志。没有增加每批随机根或生产信任降级。

构建源由HEAD的独立本地clone覆盖仅本任务源码/版本/锁等变更；node_modules、编译输出、Node/OCR缓存及出包均在独立Temp构建目录，避免改动现用CLI工作内容。前轮OCR缓存与当前lock不一致，已按固定lock在独立目录重建并通过OCR/SQLite/AES自检，不冒用旧缓存。初次全新构建暴露Core先于native编译的TS2307，release-batch仅增加已有win32-native-input build再Core build，独立CR确认该真实依赖修复，无新门禁。

实际安装仅使用已有acceptance fixture：严格nonce Temp根的appData/install，子进程级`AIDWORK_RUNTIME_INSTALL_FIXTURE`和APPDATA、`/S /D=<隔离install>`；NSIS fixture重定向Builder/Run/快捷方式，不执行生成的普通卸载器。旧0.0.29原安装器的PS/NSIS输入摘要及原ASAR smoke路径均已独立核实。Electron若启动必须显式SMOKE_USER_DATA，因为APPDATA本身不足隔离Electron；SMOKE跳过普通Main自动启动/UpdateIntent，不能以GUI smoke冒充普通自动恢复。`/S`遇仍运行的旧fixture会取消，升级前仅显式停止自己的测试PID，不称交互自动停止已验收。

验收矩阵：原EXE真实安装→实际旧Host在自有云服务配对/导入原BOSS签名包→升级新EXE→核对原home/配置/受保护凭据/插件及原任务ID；真实已装core的PollLoop/runInvocation/outbox配合自有无副作用Provider验证原结果补传/unknown不重发。真实BOSS只只读version/doctor及MCP发现，不做招聘动作；WeCom只验包/导入/发现，不发送或监听。安装前后比较现用客户端PID/创建时间、相关配置/凭据摘要、普通产品/Run注册表指纹，日志不输出凭据或客户数据。最终产物、独测/CR与未测边界见本节完成记录。

实装发现原Host在310字符插件目录用普通cwd执行Node探测，会先报ENOENT再以read ENOTCONN退出；原Node22同样复现，不能归因新版Node。最小修复仅将pluginProbe子进程cwd转为Node既有toNamespacedPath，未增加版本门禁或恢复流程。原交付Node22的真实长目录子进程回归1/1及Runtime类型检查通过，独立CR无阻断。旧基线因此用原已安装PluginStore验签导入、配合namespace诊断构造，不能称旧H1导入通过；新版须用正常H1验证。修复前R1安装器只作失败候选，最终R2 SHA256为e987af15ae164ce2ff0e8d3cb1789227117ef7fecf0e8bf3061033fbf8e820fc。

最终R2独立输入审查通过：3839项packagedInputs与资源/ASAR逐项一致，包内含上述修复；34项源码/版本/锁/测试在master和隔离构建副本逐字节一致。BOSS0.3.28与WeCom0.2.2签名/摘要及离线验包通过，WeCom额外通过合成OCR、SQLite/AES检查。Windows EXE仍为acceptance未签名版，插件沿受信Ed25519签名，不称生产签名发行。一次升级验收脚本沿用旧ASAR索引缓存导致提取截断，已归为harness错误，需清缓存后重做；未将该次失败记为产品通过或产品缺陷。

真实实装分组已取证：

| 分组 | 实际结果与边界 |
|---|---|
| 原包升级 | 原0.0.29 EXE实装、原Host配对，旧PluginStore构造BOSS0.3.27及三类任务基线；最终R2覆盖后正常新H1保留原长home、配置/DPAPI身份、管理去重及旧插件状态，实际MCP25一致 |
| 原任务续接 | 已装新core补传旧outbox并ACK，fake effects不增加；原may_have_started/unknown journal保留，模拟重复投递不重复effect，新任务可继续。人为将fake unknown改queued的测试只证明本机防重发，不验证生产unknown终态转换；服务端未改，原恢复/PG证据按前轮范围复用 |
| 新插件 | 正常新H1在原长home成功导入BOSS0.3.28、WeCom0.2.2，无probe旁路；原BOSS disabled状态保持，插件升级不自动启用业务 |
| 显式Clean | 真实安装/Clean建立新短home，兼容配置/受保护身份可复用，旧unknown journal原hash保留且不复制回放 |
| 独立新装 | 最终R2真实/S安装与H1初始化使用短rt；正常导入两插件，实际MCP tools/list分别25/15，业务调用0；未就绪插件仍可发现工具 |

三项验收脚本错误均保留失败证据并纠正，仅最终有效轮次用于结论：ASAR旧索引缓存；并行运行相同产品安装器而触发互斥退出；把fresh目录误断言为仅Clean使用的connections，实际契约是rt。没有修改产品迁就这些错误。另发现仓库旧手工用例要求十次普通安装各建新代际，已改为fresh/ordinary/Clean三次代表性安装，检查普通保留id/home/initialized及配置、凭据、journal/outbox原字节，Clean新目录保留旧facts；该用例证明文件/指针行为，不替代真实Host任务验证。测试文件不在打包输入，不重打R2；最终源码冻结扩为35项。

独立验收最终PASS，补充手工代表例1/1通过、0失败/跳过；原Node22长cwd回归1/1通过，前轮S1/S2与S3/S4按未变范围复用，不重复累计测试数。报告`FINAL-S5-INDEPENDENT-ACCEPTANCE.json` SHA256为f4be85eb7de4f953c8e22528f26610ed3ca817236a8b42684d37420a1bd1d16f，Markdown SHA256为a3a627e8c93d66289288f5cb9e52ae682d384dbdbafb9429d3fcce1c8349429b；原证据目录为本机Temp/runtime-s5-independent-9mwr6ges，交付仅含这两个脱敏报告，不含旧ready/配对码/token。

现用客户端10个长期UI/Host/监督/MCP进程PID与创建时间、9项配置/凭据/指针摘要、普通产品及Run注册表指纹前后一致。初始另两个瞬时PID后续不在，未向其发信号且未取证退出原因，不能称所有初始PID完全未变。7个自建nonce目录/fixture键及测试认证材料已清理，清理时自有PID0；补充manual例另有1个fixture自行清理。未运行普通卸载器，未触碰稳定发行签名者。

最终交付目录最初为`clients/release/AID-Work-S5-Runtime-0.0.32-BOSS-0.3.28-WeCom-0.2.2-20261030-105809`，用户随后要求产物不留工作区，已整体移至本机仓库外`%LOCALAPPDATA%/AidWorkBuild/deliveries/`同名目录（整包ZIP及sha256 sidecar亦同），源目录已移除。包含最终R2 EXE、两个已签名插件ZIP、安装说明、公开根、构建/源码冻结清单、验包及独立验收报告；内容未改、摘要不变，无R1旧安装器。独立CR已核对最终代码/包输入、隔离边界和manual修订，无新增阻断，交付封存复核另记最终摘要。

最终同名整包ZIP为252508745字节，SHA256 `e73a5e942551f18917ab382da122bfa00aa40a079367cee7865c9879c46db57b`；15个entry的长度/摘要逐项匹配交付目录，文件SHA清单和ZIP的独立sha256 sidecar均已生成。build-info为`passed_scoped_isolated_acceptance`、冻结35项；报告只含脱敏结论/摘要，旧私有状态及失败R1不进入交付。

封存独立CR最终通过，无阻断：审查者另行核对整包hash/15条目、SHA256SUMS的14项、当前master源码冻结35项、独测报告摘要和最终R2/两个插件对应，均零差异；README与本节限定一致，无R1、私钥或fixture凭据。主控最终定向diff空白检查及master/隔离副本35项冻结核对通过，HEAD保持5fafa3d0且未暂存文件。

未测边界：普通Electron Main自动重启/登录自启、UAC交互、生产Windows签名、真实服务端计费及BOSS招聘/WeCom收发、多天监听。本轮仅自有FakeCloud/Provider，服务端与原wire未改；旧端继续使用不被强制升级。S5完成仅指上述授权隔离范围，不宣布A4全部生产/业务验收通过；未提交、推送、替换现用客户端、部署或恢复监听。

### 12.9 提交授权与工作区收尾（2026-10-30）

用户明确授权提交本任务源码并清理打包临时文件；提交按master已确认upstream推送，仍不部署或恢复监听。提交范围为S0～S5的Runtime/插件安装升级简化、最小构建/长cwd修复、版本/锁/公开根及设计/计划/规范；其他会话的搜索、研究、视频和AGENTS未提交修改不混入。

本轮与此前W11未跟踪交付目录、整包及sidecar已迁至仓库外`%LOCALAPPDATA%/AidWorkBuild/deliveries/`，验收JSON随交付保存，不纳入源码。新版整包SHA保持e73a5e942551f18917ab382da122bfa00aa40a079367cee7865c9879c46db57b；历史已跟踪手册/证据、运行配置及签名者不删除，不按扩展名批量清JSON。

仅只读核验本地容器：原8000挂载当前master且开启src热重载；18000独立验收项目仍挂旧wecom工作树。主工作区未跟踪Compose副本移至仓库外本机归档，不加入正式配置；既有运行容器/卷/配对和旧工作树的实际Compose未改。Vite差异只是可选DEV_API_TARGET开发代理，默认仍8000，生产构建不使用server.proxy、不需新增生产env；该既有本地开发改动不随本次Runtime提交。日常开发继续使用原master本地环境，未来停用/切换额外容器另按明确指示操作。

提交归属核对：本任务原暂存46个路径，在共享master工作区被另一并发提交`63bce640710f94e12e7b6289f3fb02d4de10bc0d`带入并已发布；其标题为scheduler任务，额外包含`frontend/web/components/scheduled/TaskPlanView.vue`。本任务46路径无遗漏，Runtime实际源码与S5已验收版本一致，但不能称这次形成了独立Runtime源码提交，也不把另一任务文件纳入本轮CR/验收结论。保留已发布历史，不重写或强推；随后仅对本计划补充提交归属说明，使用限定文件提交避免再次带入共享暂存内容。Vite、AGENTS及其他未提交任务仍保留原状，打包产物均不在提交中。

## 13. 本地8000/master统一验证（2026-10-30）

用户明确授权将本地Web/Runner统一至原8000/master开发环境，处理配对及测试数据后停止18000临时应用，验证Runtime＋WeCom；后续明确直接复用18000已有本地数据库，不搬库/新建库。保留PG/Redis作为本地依赖，仅停止旧API、Runner API/Worker三应用。禁止agent2部署和恢复监听，本轮不自动提交。产品wire/恢复协议不改，复用原Runner overlay及连接切换。

只读核对发现原8000的.env实际连接远端agent2库，先前统计的20租户/六个历史招聘记录/两项定时均属该远端库；因此立即作废“在原连接启动worker/清理任务”的方案，未写入、导出或部署远端数据库。原本机aid-postgres为呼叫中心且缺pgvector，不修改或复用该库。直接使用aid-wecom-local-postgres-1及原卷，保留本地1测试租户、2会话/32消息、8个已完成Runner和3个成功调用，无待执行/定时任务。只备份该本地PG及storage/uploads/log，原.env备份于仓库外，不复制远端数据。

原master本地Compose通过extends复用现有Runner两服务，使用同镜像、master源码和.env；加入已有aid-wecom-local_local网络，以postgres/redis原DNS直接连接本地依赖，三应用复用原storage/uploads/log external卷，不再搬数据。仅本地进程沿原开发模式用root直接Python入口，避免生产gosu与旧root数据卷权限差异，不全仓chown；生产overlay不改。.env同时切换主库及追踪库（均指向原本地postgres/aid_wecom_local，追踪库未禁用）、Redis及Runner内部地址/服务令牌，不留下远端数据库连接。先停旧worker再启master worker，避免同库混跑。配对沿现有UI分配新home、验签迁移插件，8000签发替换身份；新配对自动start，因此先验证本地任务均终态。Web切后端后使用保留的本地测试租户、新建验证会话。

验收：8000健康及Runner真实接单/完成，Web只读任务实际调用Runtime/WeCom并获得消息身份字段，检查无send及continuous任务；原历史状态不因新测试被重放。旧三个应用stop，不删除容器/卷，PG/Redis保留供master使用；不对旧项目执行down -v。回退材料保存在`%LOCALAPPDATA%/AidWorkBuild/local-environment-migration/20261030-114912/`，本地PG备份1139475字节、1459项TOC并已核对容器/宿主SHA；配套文件备份596917字节，凭据及原始数据库内容不输出或纳Git。主控实施，独立角色只读方案及最终结果核查；当前进展与未测边界在本节收尾登记。

实施结果：aid-agent-api（8000）、aid-runner-api-local、aid-runner-worker-local均健康且/app挂master；旧aid-wecom-local-api/runner-api/runner-worker已停止，18000关闭。原PG/Redis及卷保持运行，重启策略设为unless-stopped以作为持久本机依赖；未改共享呼叫中心PG或任何agent2服务。备份的原.env指远端，仅供人工对照，不自动恢复后启动worker。

一次真实schema缺口已仅本地修复：旧测试库水位2026-10-29 13:46:35越过后来合入的通知规则13:00批次，缺notification_rules及targets.rule_id，Runner首次启动报NOTIFICATION_SCHEMA_COLUMNS_REQUIRED。按原deploy/db_update.yaml补执行该13:00幂等批次，再复用原增量更新器，无自建迁移器/改水位跳过错误；严格通知五表版本、列类型及约束验证通过。初次补齐到10:00，最终独测时随当前master迁移已到2026-10-30 11:20:00。未据此宣称所有历史库升级组合已重测，跨分支漏过旧时间批次是后续升级需注意的事实。

当前master Web为http://127.0.0.1:13001/，API代理8000，实际从master/frontend启动；旧13000 Vite来自旧工作树，停止该进程的命令被自动审批以blocked by policy拒绝，没有绕过强杀，仍开放但不再作为测试入口。3000当时已占用，未碰占用者。13001的/health是SPA HTML，不能当后端健康证明；8000/health及实际/api请求另验。新浏览器入口通过open_in_codex返回queued，不称已自动完成登录或浏览器点击回归。

主控经13001的Web API用原本地测试用户创建新会话，main智能体实际接单runner_286f858a2b71494eb5f12843bdd325dc，attempt=1、completed/settled；唯一wecom_read_sessions调用succeeded、effect=none、code=OK，读回两条sender_platform=wecom。未输出姓名/聊天正文，也未发送、建立continuous或定时监听。样本只有wecom，不称本次覆盖了真人微信/混合群全部分类。现库9个completed、4个succeeded、3会话/36消息，与原8/3和2/32加本次验证一致，不冒称逐行比对全部历史备份。

Runtime沿原已安装客户端完成替换配对，旧设备revoked/unselected，新设备active/selected；没有安装S5新包。UI切换后管理连接显示陈旧，正常File/Exit再开恢复；Sky启动一度缺SESSIONNAME，query session核实当前进程SessionID1确属Console Active后，仅为启动的原Runtime进程设Console，不伪造交互或绕过锁屏。最终UI为运行中/在线、WeCom0.2.1已就绪，BOSS0.3.27保留。一次性配对/测试auth辅助文件及内存上下文已清除，原设备凭据沿DPAPI保持。

独立只读验收报告在上述仓库外目录INDEPENDENT-LOCAL-ENVIRONMENT-AUDIT.json/md；JSON SHA256为0a7f594848638bc1887d5c0f6822b2c6f35db4771fd05158e874ed8fe3757685，Markdown SHA256为94fc87eaac1c3d8d3a1f0ab104ec3f6ed2870332674d7c96c5a5edfbad6f85f6。独测未取得token、未重复执行任务，独立核对本地连接/实际卷、原三服务停止、精确Runner/读取结果与身份字段、通知schema、活跃设备心跳及全库send/continuous/活动Runner/wake均0，结果通过。schema差异与旧13000仍开放如实披露。独立CR已核对限定三文件diff、报告摘要、Compose继承/实际卷及外部依赖生命周期，无阻断；主控核对报告SHA、三个应用healthy和定向diff检查，一次性凭据助手文件已移除。未提交、部署agent2或恢复监听。

本机日常启动沿原入口docker compose -f docker-compose.local.yml up -d --no-build；源码直接挂master，API按src热重载，Runner Python模块修改后用同Compose重启对应Runner进程生效，不承诺worker自动热重载。保留的本地数据依赖及external卷不要按临时环境清除；以后无需再启动旧18000三应用。

2026-10-30用户后续授权提交与推送本次本地环境统一配置/文档，替代上文历史“本轮不自动提交”状态，仍禁止agent2部署和恢复监听。提交前fetch并快进同步master至5b8504aa，本任务三文件以外七个已有跟踪文件改动摘要保持，其他未跟踪任务不动；只提交docker-compose.local.yml、本计划及ideas对应索引行。私密.env、数据库/卷、备份、验收JSON及既有Vite开发差异均不提交。配置与前轮独测/CR状态一致，整合后只补Compose解析、关键Runner模块import和本地健康检查，不重复执行企微业务或监听。

## 14. 内置诊断留存与一键导出开发计划（2026-10-31）

**优先级：P1，用户明确要求补齐的必做项；2026-11-02已按第15节实施通用查看导出与Main留存，专用跨层取证及实包仍未完成。** 纳入后续 Runtime 稳定性迭代，具体版本/日期待排，不能因临时脚本已经交付而关闭本项。若延期，必须保留顶部D1～D3待开发状态与未交付原因，不能静默降为可选优化。

设计唯一来源：[Runtime架构第14节](../system/runtime-plugin-host-architecture-design.md#14-客户端内置诊断留存与一键导出2026-10-31待开发)。事故输入：[BOSS隐藏窗口复盘](../incidents/boss-agent2-widget-stuck-hidden-20261030.md)。[临时取证附件](plan-boss-widget-incident-evidence-collection.md)只服务当前事故，不是永久客户端架构。

### 14.1 交付目标与实施顺序

| 阶段 | 必须完成的内容 | 验收结果 |
|------|----------------|---------|
| D0 方案登记 | 在既有架构、统一计划与ideas登记必做项，区分现有文件日志和待开发产品能力 | 本轮仅文档完成，D1～D3仍待开发 |
| D1 自动留证 | 复用调用ID/版本及log-health；BOSS写attach/目标摘要/恢复阶段，共享native写拒绝时的窗口父链/可见性/实际op，Runtime关联并持久化有界元数据 | 受控故障能产生完整关联链；刷新换widget、跨调用重attach、后台与真正遮挡可区分；没有证据的时段保留未知 |
| D2 内置导出 | U1已实现安全投影＋Main保存ZIP＋Runtime查看入口；客户端实包验收待后续授权，完整D1专用采样仍待开发 | 客户只需客户端内导出一个文件，离线/未配对/Host故障/业务进行中均可读已有数据；不需外置PS脚本或管理员权限 |
| D3 验证交付 | 独立测试、独立CR、实际客户端包和隔离安装/升级；故障包字段核对、隐私哨兵与性能边界验证 | 按14.3矩阵提供证据；旧0.0.29＋BOSS0.3.27原功能保留；发行/部署授权与源码完成分别登记 |

D1和D2形成同一交付闭环：既不能只有按钮没有故障关联字段，也不能只有详细日志仍要求客户找目录。新增诊断不依赖网络或付费模型，不为取证取得输入权限、阻塞在途调用或改变业务重试预算。

### 14.2 代码责任与边界

- Runtime主控：`clients/shared/local-tool-host-core/src/legacy/log.ts`、`diagnostics.ts`及必要的诊断模块，活动home只读定位、结构化留存/轮转/脱敏投影、覆盖/错误manifest；先核实现有依赖，不另建后台诊断服务。
- 运行组合层：`clients/agent-tool-runtime/src/runtimeHost.ts`及Provider日志汇聚，关联原invocation与实际运行版本，不收集原始参数/回复正文，不改Device wire或执行授权。
- BOSS/原生角色：`clients/boss-resume-assistant/src/main/cdp/`、`boss/visualRecovery.ts`、Reader恢复节点及 `clients/shared/win32-native-input/src/` 的诊断点；只补事实，不在日志功能中偷改输入守卫或加入自动点击/抬窗/刷新。其他插件复用公共包结构，首期不强制每个插件提供同样GUI字段。
- 公共客户端角色：`clients/agent-desktop/electron/main.ts`、`preload.cts`及 `frontend/desktop/features/runtime/`，固定诊断入口、原生保存/归档与状态展示；复用现有组件和主题。新增IPC前完成共同契约检查点，字段仅候选，当前不冻结新H1/H3方法。
- 本期不新增自动上传、录屏或长期高频桌面监控；详细业务截图/正文不进入默认包。日志留存不清理业务恢复事实，不把当前元数据伪造为事故历史。

### 14.3 验证矩阵

| 类别 | 必测条件与断言 |
|------|----------------|
| 关联正确性 | 一次调用含多次attach/session变化、刷新替换widget、native click或wheel拒绝、CDP开卡后wheel仍失败；导出能正确串联且不把session变化判成target变化 |
| 历史真实性 | 新日志保存关联；旧日志缺字段、TMP被清理、日志已经轮转/截断、无CDP连接空窗分别可见；当前状态与历史采样分开 |
| 只读与不中断 | 导出前后home指针、凭据、journal/outbox/管理状态和源文件摘要不变；Host未启动/启动失败、未配对、离线、业务执行中仍可导出已有证据，不启动或停止任务 |
| 隐私与路径 | token/Cookie/配对码/姓名/消息/简历/自由文本诊断/URL query/绝对路径哨兵不入ZIP；根目录/文件重解析点、损坏记录与不受管路径不越界读取 |
| 留存与性能 | 候选7天/20MiB上限、持续故障、磁盘满/无权限、写入中尾行、并发导出、单源采集失败；磁盘与CPU有界，失败不阻断业务且manifest报告实际缺口 |
| 客户端与兼容 | 客户端按钮到保存ZIP的真实链路、取消保存、中文/含空格路径；新Runtime＋旧插件/旧日志可导出部分证据，旧客户端原工具/恢复/结果/计费不受服务端升级影响 |
| 交付有效性 | 从实际安装包制造一次可控隐藏/输入宿主异常并导出，无消息发送；支持人员仅凭包可识别调用/目标/窗口/失败op及证据不足处，不能只用fake或ZIP文件存在代替验收 |

该工作涉及公共壳、诊断数据和底层输入观察，实施按高风险流程保留开发、独立测试与独立CR，主控核对冻结源码及最终包。只运行相关模块、IPC/UI、日志与兼容检查，不机械跑无关业务全量；未实测项如实保留。包能诊断不等于BOSS故障已修复。

### 14.4 本轮登记与后续启动条件

2026-10-31只核对源码事实并更新设计/计划/索引：已有文件日志和home定位继续复用；自动关联留存、客户端一键导出及真实产品验收均未开发。未新增代码、共同接口、自动化日程或部署动作。

后续开始开发先重读共同契约和本节，登记接口唯一写入者及兼容消费，按D1→D2→D3落实。保存目标、实际日志保留量与Main/Host通信方式以实施前代码核对为准，不借诊断绕开既有安装、授权、unknown或签名规则；客户部署须用户当场明确指示。


## 15. Runtime界面简化与正式发行准备（2026-11-02）

设计唯一来源：[架构第15节](../system/runtime-plugin-host-architecture-design.md#15-runtime界面简化与正式发行准备2026-11-02)，日志复用第14节并明确分阶段范围。用户已选官网图标；了解Web TLS证书不可替代代码证书后，已明确取消应用签名，本轮无需申请证书。本轮不提交、安装、部署或恢复监听，不改客户机现用状态。

| 阶段 | 内容及验收 | 状态 |
|------|------------|------|
| U1.1 界面与刷新 | 首次配对/覆盖沿用/故障重连三态；常规刷新不插横幅/丢焦点；详细管理信息折叠 | ✅ 完成（2026-11-02） |
| U1.2 日志查看导出 | 独立Main入口、有界脱敏投影、离线/Host故障可查看和导出；原始业务文本不入包 | ✅ 完成（2026-11-02，源码） |
| U1.3 历史隔离复核 | 旧Runtime/BOSS能力与覆盖配对保持；旧历史不拦新连接，真实在途动作不重放 | ✅ 完成（2026-11-02） |
| U1.4 品牌与未签名发行 | 官网品牌图标与显式unsigned打包/验包；用户取消应用签名，不改变插件验签或产品身份 | ✅ 完成（2026-11-02，0.0.34实包） |
| U1.5 独立验收 | 定向UI/IPC/日志/启动兼容测试＋独立CR，模拟port视觉核验，不碰现用客户端 | ✅ 完成（2026-11-02，源码） |
| U1.6 Runtime安装器 | 用户追加授权仅打Runtime0.0.34，显式U1选源、实包验原两插件，不安装或重打插件 | ✅ 完成（2026-11-02） |
| U1.7 正常切换服务器 | 正常连接详情提供更换服务器入口，复用原配对表单/协议；显式提交才切换 | ✅ 完成（2026-11-02，0.0.35实包） |
| U1.8 截图优化 | 去除Runtime菜单、显示实际服务器地址、紧凑全宽插件列表；独立验证并打包036 | ✅ 完成（2026-11-02） |

主控唯一维护RuntimeManagementPage/controller/RuntimeApp、UI测试与图标及icon打包接线；日志开发角色唯一维护core collector/安全事件、Main/preload/bridge日志类型及对应测试；独测与CR只读。接口先完成共同契约登记；现有wire不变、logs新增可选端口，旧bridge缺口只影响日志新功能。不扩BOSS/native专用窗口采样，不把第14节完整D1～D3误记全部交付。旧端验证只覆盖实际交付0.0.29＋BOSS0.3.27及本次受影响组合，禁止逐代扩测试。


### 15.1 实施与验证记录

- 主控UI自测74项；日志开发Core4＋Desktop3＋既有诊断13共20项通过。独立首轮138项，另有真实匿名生产collector组合：rt、connections/UUID、旧generations三布局，Host执行/HTTP/HTTPS/net/fetch哨兵全部0，导出ZIP仅summary/manifest/events，个人信息/业务正文/凭据/路径哨兵均不入包，源文件与pointer不变。
- CR指出缺少安装版本，已补App/Host/Core与未知null；UI/summary明确当前安装版本不代表旧事件当时版本。连接状态徽章补role=status；日志入口由用户打开时滚动/聚焦，常规状态刷新不抢焦点。正常不存在轮转日志只在详情登记，不作为故障警告。
- 日志源每份tail上限512KiB，最多500安全事件；Main固定生命周期2×1MiB轮转，旧runtime.log沿既有5MiB规则，不宣称已保证7天完整留存。D1里的BOSS attach/HWND/loader采样未开发，第14节整体仍部分完成。无新恢复框架/业务门禁；查看导出不启动/停止任务、不联网。
- 静态历史复核：完整旧管理operation、journal/outbox不全局拦新连接；独立RuntimeCleanConnection重配对用新home。活动管理secret损坏、真进程未停止和本次注册未完成仍分别处理，不删除历史unknown或重放。覆盖升级保留原身份/home的现有回归保持。
- 视觉：仅工作区外mock port预览，headless Edge验证首次/在线/离线/blocked四态及720×600窗口，无JS错误/横溢出；定时刷新2200ms后输入及焦点保持、无刷新横幅，正常在线无配对/重连按钮；用户打开日志及模拟导出反馈正常。截图与visual-report在 `%TEMP%/aid-runtime-u1-preview/`。未把模拟状态说成真实客户机连接或保存框验收。
- Web生产build、Desktop最终build/依赖边界/产物验证通过；打包政策和日志7项、打包24项定向通过。ICO经实际builder converter识别256无fallback，原三帧保留；高DPI清晰度受原48像素来源限制。
- 用户取消签名后，新增显式Unsigned不改正式/验收身份或插件信任，兼容旧manifest默认；隔离overlay仅11份打包文件，不含U1业务源码，避免误交付HEAD旧UI。此轮未生成新Runtime安装器，既有0.0.33交付包不包含本轮U1源码；后续打包需准确选源及新版本。
- 最终独立补测31项：versions真实ZIP、未知值、unsigned匿名归档11overlay/原业务WIP排除、ICO原帧保留通过。CR末轮另发现首次配对promise失败时摘要卡片被隐藏，修正后错误及重试入口可见且只请求一次；root核出public favicon在真实aidagent协议不能直读，改为Vite assets并增加实际bundle/协议回归，不扩大协议白名单。受影响最终page17＋renderer4共21项独测通过，0失败/0跳过；CR复审无遗留。额外最终日志3项及产品/IPC/签名/日志18项主控核验通过；完整App build最终为3manifest/45模块。
- 独立证据 `%TEMP%/aid-runtime-u1-test-4b87123182fe4d1690cc5045bf87cace/` 包含 independent-test-report、anonymous-combination-report、tail-independent-report、final-two-regressions-report；视觉最终4态品牌图均加载成功，无JS错或横溢出，自己的预览server已停止。现场原Runtime四进程31892/31924/31932/33152仍保持09:41启动时间。
- 当前客户端进程/配置不动，不提交、重新打包、安装、部署或恢复监听。本轮完成源码功能和独立验证，不冒称新功能已经进入此前0.0.33安装包。


### 15.2 U1安装器打包（2026-11-02，用户追加授权）

仅打新Runtime0.0.34，已交付BOSS0.3.29和WeCom0.2.4包复用，不重新签署/构建插件，不提交、安装、部署或恢复监听。Core新增诊断实现从0.2.22升0.2.23；Host打包组合从0.2.25升0.2.26，对应package/lock同步，不覆盖旧交付版本。沿当前已安装acceptance的appId/home/公开根保持可覆盖升级，不把App不签名误改为生产插件根放行，也不切换客户机身份。

以master46636779独立Git归档为基线，仅将上轮已审查U1 Main/Core/UI/测试/图标及本轮版本/打包文件逐项冻结叠加；额外记录source-overlay.json与源文件SHA，工作区通知规则/数据库/其他文档/Vite开发设置等改动均排除。复用release-batch内部WorkspacePrepared入口构建Runtime-only；交付build-info如实注明U1业务源码叠加，不沿用packaging-only说明。已有固定Node官方ZIP仅作为cache复制，仍由原摘要校验；不复制旧dist/node_modules或已安装客户端。独测/CR只读核对实际EXE与配套插件，优先复用同源功能证据。


#### 15.2最终交付与独立验包

- 构建副本 `%TEMP%/aid-runtime-u1-package-abbdc67b/workspace`：源归档HEAD466367794fbcf6d6d4ebf70ac2e55c3cb25e9e6b，4284常规归档文件/36明确overlay独立逐项核对，无其他WIP，构建后overlay零漂移；四份lock与UI0.0.34/Host0.2.26/Core0.2.23一致。
- 复用现有release-batch Runtime-only内部入口，全部流程exit0；24项打包测试和隔离副本11项日志/renderer/签名政策测试通过、0跳过。原runtime verifier验证3815受管资源、实际NotSigned、固定Node22.23.3/ABI127、安装身份与source fingerprint通过。
- EXE：`%LOCALAPPDATA%/AidWorkBuild/deliveries/AID-Work-Runtime-0.0.34-20261102-112104/AID-Work-Runtime-0.0.34-win-x64-acceptance-unsigned.exe`，127125991字节；SHA256 `2be036770e3ec766883611b3f59ba80c1aee25e0828177c71313186421a4832c`。沿现用acceptance身份而非新生产目录；Windows签名明确NotSigned，不改插件签名或test_only。
- 独测用7za纯解档实际EXE，3818项resources及app.asar内29份dist摘要与冻结构建完全一致；EXE内Node/Core/24公开根验原BOSS0.3.29（3738文件）及WeCom0.2.4（7472文件）签名/清单通过，保留WeCom3业务工具和旧BOSS25工具；两原插件字节不改、不重新打包或捆绑。原插件SHA分别 `ab6126fb386c6ac8c64d50cf6ca3f7ef7828ebb4eb0e245326a995b6368c76cc`、`f16407dedaf1217301d2fcb85b2d112d4d6d20de24e33a893d297a8c2b95796d`。
- 实际ASAR runtimeLogs/yazl及EXE内Core在匿名home成功生成安全ZIP，版本34/26/23正确、隐私哨兵排除、网络0、Host未启动；renderer图标及允许协议路径通过。实际NSIS和主程序PE均有官方16/32/48/256图标，rcedit规范化部分mask尾部但像素一致；没有用仓库dist冒充包内功能。
- 交付目录含11文件/10项SHA：EXE、原release manifest、实际来源manifest、公开根、build-info、使用说明及4份核验/来源报告。最终完整ZIP为同级 `AID-Work-Runtime-0.0.34-20261102-112104-verified.zip`，127178752字节；SHA256 `e96bfce2a2c37a9f05d92b9bcd8a60e40915dd70724459b731fe180ef509e20f`。独测/CR最终目录/ZIP逐文件字节和旁校验和通过。原脚本先生成的无verified草稿ZIP保留且不交付；它不含后补独测报告，最终包以verified为准，EXE本体始终不变。
- 交付build-info准确记录U1业务source overlay，并把两插件标为复用验包而非本批构建，未选插件版本null。不写packaging-only虚假来源。最终独测证据 `%TEMP%/aid-runtime034-test-bcd3fe2a5fb34c87b0bfc11c13546d0a/` 的actual-exe-runtime、exe-integrity-branding、source-provenance、final-delivery-report；原始报告已放交付目录，最终ZIP核验报告留外部以免封包后改写。
- 未运行安装器、覆盖当前客户端、重配对、调用真实业务、部署或监听；未提交。本轮只交付可供用户安装的文件，未将NSIS执行/真实客户机升级和Web业务写成通过。后续提交必须只暂存本任务内容，工作区其它任务仍保留。


### 15.3 正常连接切换到测试服务器（2026-11-02）

按设计15.5补UI遗漏，不改配对后端/恢复流程。主控唯一维护RuntimeManagementPage及定向测试/后续版本，独立验证者只读做测试与CR。当前0.0.34已交付不能覆盖同版本重发；更新UI安装器使用0.0.35，Host0.2.26/Core0.2.23与原两插件复用。实际服务器切换需用户在新版客户端填写目标地址及该服务器生成的一次性码，本轮不替用户写客户端配置或切换真实身份。


#### 15.3验证与交付结果

- 普通风险UI修复，主控开发＋一个独立验证角色完成测试和CR：feature77项（page19）0失败/跳过。新回归证明健康状态详情入口可达、预填设备名并聚焦服务地址；打开与取消均无reconfigure/stop/pair，取消清code；提交仅一次register_new，使用目标server/code，不硬编码agent2。
- Headless Edge模拟port实际点击详情/设置/取消/再次提交：打开和取消请求0，取消码清空，目标server仅提交一次，无JS错；可视检查表单/焦点与用户操作相符。临时预览server已停止。测试服务器从仓库登记核对为https://agent2.aidingyi.cn，只读health返回HTTP200；没有请求配对或替用户切换真实服务器，用户需在该后台生成目标码。
- Runtime0.0.35沿034冻结源码，仅Page/test与UI package/lock四项改变，其余32份overlay原hash不变。Core/Host5份JSON在工作树仅CRLF变化，JSON与换行规范后内容相同，构建继续用034已验证原字节并在source-overlay.origin登记。构建副本 `%TEMP%/aid-runtime035-package-1642da5e/workspace`，HEAD46636779归档同034，未混入其它WIP。
- 原打包流程exit0，24项打包测试全过；Desktop编译/边界/3manifest/45模块验证通过。实际EXE `AID-Work-Runtime-0.0.35-win-x64-acceptance-unsigned.exe` 为127126187字节，SHA256 `3e82f8bfededd81dfd4497c6411c6f77f6a41245217ffc33c9c6162973b40443`，NotSigned，沿既有acceptance身份/home。无插件重打。
- 独立7za只解档EXE确认新详情/表单代码进入实际renderer，29份dist与冻结编译一致；实际Core54文件、Node、24根、Main/preload/Electron及app依赖与034逐字节相同，因此明确复用034的两插件验签/清单与日志ZIP组合证据。runtime仅重编native supervisor、其build-info、resource-manifest三项变化；源/script/compiler/policy与034不变且新EXE摘要符合build-info。没有把源码dist当实物或把复用说成重跑。
- 交付 `%LOCALAPPDATA%/AidWorkBuild/deliveries/AID-Work-Runtime-0.0.35-20261102-114037/`，10文件/9项SHA；同级最终`AID-Work-Runtime-0.0.35-20261102-114037-verified.zip`为127178103字节、SHA256 `cc78dd74fed04e4bb5e7cbb26889eed445b014107acd2211775f1bda2338080c`。独立目录/ZIP逐文件字节与摘要通过，旧脚本原始草稿ZIP不交付。
- 沿用户先前Downloads交付目标，035复制到 `C:/Users/PC/Downloads/AID-Work-Installers-20261102-111012/`，旧034保留于其previous-runtime-0.0.34备份目录（摘要仍2be036…），没有删除。原BOSS0.3.29/WeCom0.2.4字节不变，SHA256SUMS更新为当前三安装文件；独立复核通过。证据在 `%TEMP%/aid-runtime035-test-0ea188cccf0e4263926e70b22909bd58/` 的source-test-review、source-difference、actual-exe-difference、final-delivery-downloads-report，原前三份随包。
- 未执行安装或真实服务器切换，未提交、部署或恢复监听；当前已安装客户端由用户后续选择覆盖035并在页面输入目标服务器码。


### 15.4 截图优化与Runtime0.0.36（2026-11-02）

用户截图标注为三项：不显示Windows菜单栏、连接详情需可识别服务地址、插件列表减少竖向留白且利用右侧空间。设计15.6为本轮唯一边界，root维护UI/Main/read-only metadata/菜单与测试、版本和打包，独测/CR只读。新H3可选connectionInfo只显示origin，沿已有可信sender与活动home，不更改配对/执行行为；旧bridge保留。

成功标准：正常Desktop菜单不变、Runtime Windows无顶部菜单；实际配置地址可见并随切换更新，配置中凭据/其它字段不跨IPC，读取失败不拦执行；1156宽插件同一行、720窄窗无横溢出，启停/卸载确认语义保留；036实际包包含这些修改，现有BOSS/WeCom包复用，不安装/提交/部署/监听。


#### 15.4实施验证记录

- 只移除Runtime Windows应用菜单，普通Desktop和macOS菜单保持原状；新增可选connectionInfo通过原可信sender，读取实际活动home的config.json并只返回HTTP(S) origin。账号、令牌、密码及URL路径/query/hash不跨IPC，不启动Host或联网，失败返回null；前端读取独立于主状态刷新，不拖慢连接和插件操作。
- 插件列表改为名称/版本、状态、操作紧凑列，使用窗口完整宽度；原启停、卸载确认及状态含义保持。mock port + Headless Edge核对1156宽面板1092/右边距32/每行68px、720宽每行79px，均无横溢出或JS错误；连接详情显示实际port返回的地址，自己的预览服务已停止。
- 独立测试feature81项＋编译后菜单/metadata/security13项共94项通过，0失败/0跳过。匿名实际编译Main回调验证可信sender、固定home/无客户端路径输入、活动home变化及损坏/超限配置，网络和Host调用0、配置不变。Windows测试进程不能创建文件symlink（EPERM），没有将该项声称为真实覆盖；代码沿普通文件校验并拒绝symbolic link，不扩展修复框架。
- Web生产build（1143模块）、Desktop编译/边界和3manifest/45模块验证通过，原release-batch Runtime-only全部exit0、24项打包测试全通过。独立源码审查无阻断项；HEAD46636779归档4284文件与43明确overlay逐项一致，仅13项相对035变动/新增，排除其它未提交工作。Core0.2.23/Host0.2.26和两插件保持，UI独立升0.0.36，不覆盖旧版本重发。

- 实际EXE纯解档后3818份资源与冻结构建字节一致，29份ASAR dist对应编译产物；新Main/platform/preload/helper四模块、地址及紧凑CSS进入实际包。实际ASAR匿名回调再次验证可信frame、固定home、投影及菜单，未访问客户机配置、联网或启动Host。Core54文件/Node/24根/app依赖/logs均与035一致，明确复用034原两插件验签/日志证据；非伪称插件重新实测或构建。独立来源CR核对3845项manifest input和43overlay零漂移，无遗留项。
- Runtime EXE为127126631字节，SHA256 `652984f2cd29a1df91bf336fdee6e411b11f9fc9990fdbc7e143272b2f421aa2`，实际Authenticode为NotSigned；沿原acceptance身份/home，不改变插件信任与生产服务器配置。交付目录 `%LOCALAPPDATA%/AidWorkBuild/deliveries/AID-Work-Runtime-0.0.36-20261102-115859/`，完整归档以同级`-verified.zip`为准；附冻结源清单和独测/CR/实际ASAR/视觉报告，原无verified草稿不作交付。
- 新EXE已复制至 `C:/Users/PC/Downloads/AID-Work-Installers-20261102-111012/`，顶层三份安装文件SHA逐项复核；旧035核对摘要后移动至previous-runtime-0.0.35，原033/034备份和两个插件原包不动。当前客户端未被停止或安装覆盖，未真实配对/换服务器、提交、部署或恢复监听；NSIS实际执行与业务测试仍未运行。

- 最终完整归档`AID-Work-Runtime-0.0.36-20261102-115859-verified.zip`为127182708字节，SHA256 `0767de13931f3eb9cfe787f2a5dc10ad0c29ac8cdf9c280a428825ef580cb1b5`；交付目录14文件/13项SHA，文档如实区分mock视觉/实际ASAR匿名核验与未执行安装业务。来源及实际包报告见`%TEMP%/aid-runtime036-package-0c84221d/independent-review-report.json`与`%TEMP%/aid-runtime036-test-88a2e14a5d3c41999d12511098be9714/`，主要报告随完整归档交付。

- 最终独测只读复核通过：verified ZIP与目录14文件逐字节相同、13项摘要及旁SHA正确，随包报告引用可解析；Downloads三项摘要及033/034/035备份均保持预期。`final-delivery-report.json`留在036独测Temp目录，未封包后改写交付材料。

### 15.5 本会话提交前核对（2026-11-02）

用户授权提交并推送本会话及BOSS相关修改，agent2由用户自行发布；助手不部署、安装或恢复监听。master已fetch并与origin/master同步于94269b35。BOSS0.3.29业务修复及交付说明已分别在148f7945、46636779，不重复改动插件或版本。

当前43份Runtime选源与036冻结清单核对为38份字节相同、5份package/lock仅换行不同，无业务源码漂移，可复用上述独测与CR。主控对当前工作区重跑Desktop完整build（3 manifest/45模块）和发布脚本/配套验包/Runtime打包/插件打包36项测试，均通过、0跳过；Runner受影响import亦通过，修复证据见[休眠计划第18节](plan-agent-runner-durable-sleep.md#18-deepseek-空思考字段修复2026-11-02)。

只读运行中安装文件版本为0.0.36，本次提交不引入超出已交付036的客户端代码，也不重打BOSS0.3.29或WeCom0.2.4；本次服务端Runner修复不需要客户端更新。通知/定时任务、搜索及其它任务WIP保留，公共索引仅暂存本任务条目。

### 15.6 连接概览卡片布局优化（2026-11-02）

- 范围：按用户新截图优化卡片布局。标题/设备名/说明归组，连接标记及原操作靠右；详情改为两列标签值，底部提示与设置同行，窄窗自然折行。沿原Desktop组件/token和事件，不改Controller/IPC/Runtime执行语义。
- 成功标准：展开时服务器/状态/编号易扫读、减少空白；收起与展开均无横向溢出，键盘可达，原启动/停止/故障/更换服务器/取消仍正常，未提交设置不产生请求。
- 验证：主控现有Runtime页面交互测试及Desktop/Web构建；独立验证者按局部UI范围测试/审查；自有本机模拟port视觉检查，不访问现用客户端/真实服务器或业务数据。
- 验证结果：现有feature81项全通过；独立页面20项及CodeReview通过，0失败/0跳过（与81项重叠，不累加）。原事件、禁用条件和旧bridge保持，无新接口/执行限制；没有为CSS排列新增形式化业务测试。Web build及边界验证1143模块、Desktop/Main build及3manifest/45模块均通过，diff检查通过。
- 自有Headless预览：1108px窗口展开卡片由452px降至271.5px；720px窗口由473px降至334.5px。宽窄展开无横溢出，summary键盘空格展开/收起正常；打开设置聚焦地址、取消后关闭表单，未提交时写请求0。另检查720故障状态，保留全部原操作。视觉与JS无异常，模拟port数据，外部网络阻断，浏览器/server均在finally关闭；截图与报告在 `%TEMP%/aid-runtime-connection-layout-3cpjksst/`。
- 独立长内容视觉：1108/720下100字设备名称与长服务地址自然换行，操作区域无越界、JS错误0；独立验证者实际查看宽窄/故障/长内容截图，结论通过。证据 `%TEMP%/aid-runtime-layout-independent-hp6tr4yo/`，未操作现用客户端或业务。
- 状态：开发及独立验证完成。此前本会话代码已在5b06e607提交；本批为新修改，尚未提交、打包、安装、部署或恢复监听。现有0.0.36安装包不含本批新布局，后续交付须用新版本，不覆盖已交付同版本包。

### 15.7 Runtime0.0.37提交与安装包交付（2026-11-02）

用户追加授权提交本次连接卡片布局并打包，替换Downloads当前交付包；不运行安装器、部署或恢复监听。仅UI版本升0.0.37，Host0.2.26/Core0.2.23、BOSS0.3.29和WeCom0.2.4保持。先将本任务源码/版本/文档提交推送，再用该提交的隔离Git归档通过原release-batch Runtime-only打包，沿现有acceptance身份与unsigned政策，不叠加其他任务WIP。

源码沿用15.6的81项回归、独立20项/CR与视觉结果；master本次已fetch同步95af234f，新增通知任务提交未触及clients或frontend/desktop，已有客户端验证仍对应相同源码。本次隔离打包将重跑编译/原验包，独立核对实际EXE包含新布局与版本、旧两插件可用前提；交付后更新SHA与证据。旧036包经摘要核对后留在Downloads备份目录，只替换当前Runtime入口，插件不重打不改写。

#### 15.7 最终交付记录

- 源码已提交推送 `07e5d428e18fe7d764d5e6ac5158779937ea5002`。原release-batch按该提交归档到 `%TEMP%/aid-release-4153b361/workspace`；独立核对source.tar的Git标识及4301份常规源文件，字节零漂移，无其它WIP。仅UI0.0.37，Host/Core和插件版本保持。
- 首轮主控在下载await期间补入同一官方Node缓存，导致独占写入EEXIST，未产出安装器。核对锁定摘要后，在同一自有隔离目录顺序重跑原WorkspacePrepared入口成功，未改产品代码或添加恢复门禁。原始日志和重跑日志均留在 `%TEMP%/aid-runtime037-release-4f1e625d/`，未把首轮失败计为成功。
- 隔离构建、Desktop/Main编译/3manifest/45模块、安装器3815受管资源验包及24项打包测试全部通过，0跳过。实际EXE为127126852字节，SHA256 `071675fbb43234a9e1e16d35820a2cea4f0f4847a44a00ccb7059c88cf1fc654`，WinPS5.1实测NotSigned；NSIS版本0.0.37、内置主程序0.0.37.0。沿原acceptance appId/home和插件根，不改变当前安装。
- 独立NSIS只解档：3818份资源逐字节对应win-unpacked，ASAR中29份dist及3857项manifest input/总指纹重算通过，新header/facts/footer和720响应式样式进入真实包。Electron模块/app依赖、Node/24公开根/Host工具定义与036原字节相同；Core53/54文件字节同，另1份Core package.json及Host/package.json仅CRLF转LF且JSON语义相同；native监管EXE及其摘要/资源清单重新生成，源/编译器/策略不变。可复用034以来原两插件配套兼容证据，不声称所有资源字节一致或重新执行插件业务。
- 完整交付位于 `%LOCALAPPDATA%/AidWorkBuild/deliveries/AID-Work-Runtime-0.0.37-20261102-140649/`。原脚本ZIP保留原样、无追加重封：同级 `.zip` 为127197656字节，SHA256 `5694233929bb525ba1edf9093f4d4b95af1cbe912a1a909d960a30dc9ce416dd`；6份文件与目录逐项字节、SHA表及旁摘要复核通过。独立实际包报告在 `%TEMP%/aid-runtime037-independent-9uculmnk/`，不为了附报告另造第二套交付ZIP。
- 新EXE已复制到 `C:/Users/PC/Downloads/AID-Work-Installers-20261102-111012/`；原036核对SHA `652984f2cd29a1df91bf336fdee6e411b11f9fc9990fdbc7e143272b2f421aa2` 后移至该目录的previous-runtime-0.0.36。两个原插件摘要保持，顶层三份安装文件SHA256SUMS已更新；原033/034/035备份不动。当前Runtime四PID及UTC启动时间核对未变，没有安装覆盖、重配对、部署或恢复监听。
- 验收边界：独立测试和实际包CR通过，真实NSIS安装升级与企微/BOSS业务未执行。仅替换下载文件；用户需要运行新安装器后才会看到037布局。最终交付报告留在上述Temp证据目录。

## 16. 连接与旧执行恢复整改计划（2026-11-10）

[方案](../system/runtime-plugin-host-architecture-design.md#16-连接与旧执行恢复整改2026-11-10) · [0.0.37 复现证据](../incidents/guoteng-runtime-disconnect-20261110.md#交付版复现管理握手与执行待核对)。恢复 1A 已补诊断，按用户确认先开发恢复 2 的单次用户配对。真实重启、休眠与快速启动对照及 Windows 停稳修复留待隔离真机。11 月 5 日和 9 日的初次失败缺少 Host 回执，计划不以两天都命中同一分支为前提。

| 阶段 | 实施范围 | 放行依据 |
| --- | --- | --- |
| 恢复 1A | Main 自动恢复失败改为固定阶段事件，现有 Core 白名单和导出直接投影。 | 同一 ZIP 能区分 describe 失败、getState 失败、旧执行待核对与 start 提交或确认失败。历史通用事件可读，无原始错误或配对凭据，不调整执行门禁。 |
| 恢复 1 | 先在隔离真机用 0.0.37 验证开机实例和旧资源停稳证据，再改 native、Runtime Host 和必要的 Main 恢复入口。同步补握手、状态读取、停稳核验、执行启动四段脱敏诊断。 | 同会话睡眠唤醒、真实会话切换、休眠/快速启动、完整重启均有对照。BCD GUID 相同且旧资源已证停稳时，旧 v2 记录可用原身份恢复连续心跳及领取。旧资源仍活或证据缺失时，不启动可能冲突的执行。旧任务结果未知可继续核对，旧任务不被重放。 |
| 恢复 2 | 单次显式配对，收到并校验成功回执后才持久化及切换目录。旧 `requested` 只用于关联误切空目录，随后移出活动入口。 | 有效 HTTPS 身份与旧 HTTP `requested` 并存时，启动和普通重试均零 `/pair`。301、超时、异常或不匹配回执都不生成失败事务或空目录。超时提示服务端结果未知。后续仅恢复已确认成功的本地安装，Host 启动失败不重开配对。 |
| 恢复 3 | 用实际安装包复测国腾组合路径，覆盖旧包升级、覆盖安装、`/Clean`、混合本地记录及回退。 | 原身份恢复场景保持同一设备 ID 和有效凭据，连续至少三个正常心跳且成功领取一次隔离测试任务。无新配对请求、无重复任务或撤销。已有未 ACK 结果按原身份补传。临时网络失败仍自动恢复。界面及诊断区分连接可达与执行可用，报告无凭据。 |

### 16.1 真实 Windows 验证与放行条件

- **同会话睡眠唤醒。** 原 Host 存活时应由原循环恢复。若旧 Host 退出，用有效的同会话 Job 台账核对。记录恢复前后的会话、原 Host 是否存活，以及首次正常心跳和领取。另测网络暂未恢复的情况，复用原退避。
- **跨会话及不同电源路径。** 至少覆盖同一开机内注销再登录、休眠恢复、开启快速启动后的关机再开机、以及真正完整重启。记录启动项 GUID、开机实例证据及旧 Job/进程核验结论。完整重启前后保持同一启动项。休眠或快速启动后仅凭启动事件或会话变化不放行。补测系统时钟调整和事件日志缺失，确认缺证据仍保留保护。
- **分辨失败阶段。** 基线保留 `describe` 返回错误、`describe` 成功但状态待核对、已开始 start 后失败、仅诊断心跳四条对照。实际包必须能区分，模拟挂起或换 GUID 只计分支验证。若 0.0.37 真机复现出额外握手故障，先保存回执并定位，单独评估是否纳入本轮。
- **诊断包独立验收。** 给验证者实际包的一键导出，要求仅凭该 ZIP 区分握手失败、旧执行待核对、正常循环断网重试、待配对 301，以及诊断单次心跳。再用「活动目录为空，原身份日志在上一个目录」重测，确认仍能看到对应来源的时间线，或明确看到缺失及截断。国腾场景应能通过一个 ZIP 识别运行标记、停稳判断及相关电源事件，无需额外 PowerShell。验证原始 stderr、凭据、配对码和业务内容均不会随包导出。
- **兼容与回退。** 编码前按共同契约登记 native 本地输出和 DPAPI 记录的生产者、消费方、版本及回退路径。未验证旧版读取新记录时，不把覆盖旧包当成安全回退。回退不得删除执行标记、回执或凭据。已交付 Runtime/BOSS 旧组合继续通过现有 Device API 工作。

### 16.2 恢复 1A 开发记录

- Main 的现有自动启动流程按握手、状态读取、执行待核对、start 提交与确认分别记录事件。Core 按原白名单投影到现有 ZIP，保留旧版通用事件的可读性。用实际子进程 IPC 验证五种失败均能从同一 ZIP 区分，且原始 Host 错误不会进入归档。
- 自测覆盖 Desktop 全量 104 项、Electron 隔离冒烟和 Runtime 真实受管进程与启动相关用例 14 项。Core 诊断 5 项通过；Core 全量 112/113，唯一失败为未修改的 WeCom MCP instructions 前 512 字符断言，已从基线源码核对短语超出截断边界。现阶段只补诊断，旧执行停稳判断、待配对事务与旧连接日志目录采集仍按后续阶段验证与开发。
- 独立测试复核 Desktop 104/104、Core 诊断 5/5、Runtime 受管 Host 14/14，编译、实际模块导入和隔离 Electron 启动均通过。Core 全量 111/113，单独核对两项未通过分别为基线 WeCom instructions 断言与共享桌面锁波动，调用链均未经过本次诊断改动。测试阶段未修改源码。
- 独立 CR 核对实际启动及 ZIP 导出链路，通过本阶段审查。同步主分支至 `7472454f` 后七个源码与测试文件的差异一致，Desktop 104/104 和隔离 Electron 启动复验通过。

### 16.3 恢复 2 开发记录

- 用户确认配对一次显式提交仅发送一次。重启、普通检查、继续本地安装均不发送配对请求。旧 `requested` 只允许由同一安装中受信 prior 和当前空目录证据还原原身份，随后移出活动入口。覆盖和 `/Clean` 正常迁移仍保留原身份；若历史旧版 `/Clean` 已抹去可信 prior 链，无法从任意历史目录重建。
- 只保存服务端确认成功的本地事务，并先写配置和受保护凭据再切换。成功配对与 Host 启动分别处理。响应丢失时既有服务端仍可能已撤销旧设备，客户端明确提示结果未知并保留原本机身份，等待人工查看；应用层准备和确认属于后续方案。
- CR 回归：确认回执的首次 DPAPI 写入失败后，同一 Main 进程内仍可继续本地安装。解除文件故障前后都拒绝再次配对。真实 HTTP 验证全过程仅 1 次 POST。若首次写入成功前就退出进程，回执仍无法跨进程恢复。
- 开发自测：真实 HTTP/DPAPI 与受管 Host 集成 18 项、Runtime 注册存储 3 项、Core 心跳与诊断 13 项、Desktop 104 项、前端 Runtime 50 项及隔离 Electron 冒烟通过。Core 全量 111/113，既有 WeCom 两项真实 MCP 用例因跨进程互斥返回 BUSY，串行仍复现；受影响用例无失败。最终安装包驱动语法通过，3 项安装实物测试待提供新包。
- 独立测试：Desktop 104/104、HTTP/DPAPI/受管 Host 17/17、Runtime 注册存储 3/3、既有注册与 Host 15/15、Core 受影响 16/16、前端 Runtime 81/81。编译、实际导入和隔离 Electron 启动通过。真实超时探针仅收到一笔带码的配对，重开与检查未重发。复现并修正本地切换失败时旧身份心跳遮盖安装待完成的误报，相关测试重跑通过。3 项新版 EXE 实物用例缺少安装包而跳过。
- 独立 CR 发现并复验首次写入成功回执失败的恢复路径。修复后当前进程保留已确认回执，恢复写入后只完成本机安装，HTTP 真实夹具始终只有一次配对。独立复测 Desktop 104/104、HTTP/DPAPI/受管 Host 18/18、模块导入和隔离 Electron 启动通过。CR 通过，首次落盘前退出仍无法跨进程恢复。

### 16.4 服务端配对确认后续检查点

试用客户将统一升级，新配对要求协议 4，已经配对的旧客户端保留原鉴权与任务行为。[共同契约](../system/runner-desktop-runtime-integration-contract.md#8-实现前共同检查点)记录三端点时序、凭证绑定和唯一写入者。整套验收覆盖准备回包丢失、客户端保存失败、确认未到服务端、确认回包丢失、并发确认和已失效配对码。每个故障注入点核对原设备选定状态、设备数量、候选能否领取和请求次数。服务端数据库用任务专属 PostgreSQL 验证，客户端用真实 HTTP、DPAPI 和受管 Host，最终安装包与部署仍另行验收和授权。

- 新配对使用新版独立 Windows Runtime 界面。旧 CLI 与非独立桌面的单步配对入口固定返回 426，已配对的旧设备仍可用原 token。Web 保留生成和复制配对码，更新为新版界面与自动选定指引。
- 确认前候选写入或停稳失败保持原目录和原设备授权。确认发送前落盘 `confirming`。有歧义时保留候选并只读核对，服务端已经提交时恢复同一设备身份。旧的安装包集成驱动通过协议 3 构造历史回执，仅用于迁移证据。协议 4 最终 EXE 验收须从实际新版界面走新接口。
- 先应用可空字段迁移并完成服务端 worker 切换，再开放新版客户端配对。混用旧服务端 worker 会留下旧单步写入入口。待确认候选和原设备凭据在回退时保留，先完成或确认其状态，再决定回退。
- 开发自测：隔离 PG16 70 项通过，含真实迁移前后旧 token 的心跳、领取、结果上报与 Windows Node/DPAPI → Uvicorn → PostgreSQL 确认回执故障链。真实 HTTP/DPAPI 与受管 Host 22 项、Desktop 104 项、Runtime 注册回归 16 项、Core 13 项、前端 55 项通过，Web/Desktop 构建和隔离 Electron 冒烟通过。测试数据库按本机时区运行。最终 EXE 和 Windows 电源恢复待另行验收。
- 独立测试：同步主分支至 `5f171f29` 后，隔离 PG16 83/83、Runtime 注册 16/16、Desktop 104/104、HTTP/DPAPI/受管 Host 22/22、Core 13/13、前端 86/86。Web/Desktop 构建、真实模块导入、Python 编译、隔离 Electron 启动、PowerShell 语法和差异检查通过。未修改源码。最终安装器、Windows 电源恢复和窄屏视觉验证留待验收。
- CR 发现确认回包断开后，服务端事务仍可能继续提交，旧 Host 提前启动会再次领取任务。回炉先用真实 HTTP 复现旧 Host 启动一次，再增加 `confirming` 的启动限制，覆盖原进程、重开和手动启动。只读结果确认未提交后才允许恢复旧身份，确认已提交则安装并启动新身份。回炉自测 HTTP/DPAPI/受管 Host 24/24、Desktop 104/104、前端 Runtime 51/51，Desktop 构建和隔离 Electron 启动通过，待独立复验。
- 回炉独立复验：HTTP/DPAPI/受管 Host 24/24、Desktop 104/104、前端 86/86、隔离 PG16 配对确认 7/7。真实断回包并延迟提交时，旧 Host 启动次数为零。无效查询回执不解除门禁，明确拒绝或过期后才允许恢复。编译、模块导入、隔离 Electron 启动和差异检查通过。
- 独立 CR 复核确认中门禁、服务端单事务换绑、旧凭据兼容及迁移，真实 HTTP 断回包定向复测通过，无 P0/P1。实际 EXE、Windows 电源恢复和窄屏视觉仍待验收。

### 16.5 恢复 3A 一键诊断

- 维持 ZIP v1 和原入口。`events.jsonl` 记录握手、状态读取、执行待核对、启动、配对 `prepare`/`confirm`/`status`、运行心跳与领取转折及单次诊断心跳。配对记录只带协议类型、固定原因和可用的 HTTP 状态。运行心跳与领取分别按失败和恢复转折记录，原重试间隔保持原样。
- `manifest.json` 标明当前、可信上一连接及客户端来源的分文件覆盖、截断和过时状态。附带的 `evidence` 记录采样时刻、配对阶段及目录关系、身份可读性、执行标记时间、固定停稳原因、启动标识与会话匹配以及 Windows 电源时间线。证据采样只在导出时读取，不启动 Host 或发送心跳。`bootMatchesMarker` 只能对照当前实现的启动标识，不能认定开机实例或放行执行。
- 开发自测先复现当前目录空时导出投影收不到上一目录记录。修复后 Desktop 106 项、真实 HTTP/DPAPI/受管 Host 24 项、Runtime 58 项、Core 46 项、前端 24 项通过。通过真实 Windows ZIP、真实 PowerShell 5.1 系统事件查询、事件字段夹具、真实 native 标识与受保护错会话记录、Web/Desktop 构建及隔离 Electron 冒烟。Windows 电源状态切换、最终安装器和现场组合路径另行验收。
- 独立验证 Desktop 106/106、HTTP/DPAPI/受管 Host 24/24、Runtime 58/58、Core 67/67、前端 Runtime 82/82，实际 ZIP、PowerShell 无事件查询、Web/Desktop 构建、隔离启动均通过。合入 `e5193d03` 后仅三个 Web 文件变化，Web 构建和全范围依赖边界复验通过。
- 第一次开发自测的隔离路径解析失败，合成记录追加到本机默认旧 Runtime 日志，核查未发生轮转，原文件保留。自测重跑及全部独立验证显式使用 worktree 内的独立 Runtime 目录。
- 独立 CR 无 P0/P1，使用隔离 Runtime 目录复测真实 ZIP 和 PowerShell 查询 2/2。P2：文件新近写入但只有范围外事件可投影时，采集详情的「早于所选范围」容易被理解为整个文件已过时；可细化为「可识别记录早于所选范围」。

两处修复分别验证，停稳修复可独立验收。按项目高风险流程执行开发、独立测试和只读 CodeReview。真机安装仅使用隔离设备或已明确授权的客户机，发布与部署另行授权。
