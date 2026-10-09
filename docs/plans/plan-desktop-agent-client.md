# Agent 桌面客户端 v3 开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 源码调研与重新设计 | ✅ 完成（2026-10-08） | 三家源码调研完成；接受Runtime补充并以共同契约v1.3定稿；wire及实现未完成 |
| H3 首期公共壳 | 独立Runtime产品/Node监管/选包与公共入口 | ✅ 完成（2026-10-09） | UI竞态增量复核后新包68b855通过标准验包/独立复验及实际启动退出；待人工验收 |
| Phase 1 | 云端 Runner 桌面对话闭环 | 📋 待开发 | 复用现有用户会话与公开 Runner API，不依赖旧 D1 |
| Phase 2 | Windows Runtime 受管集成与任务绑定 | 📋 待开发 | 同机/异机选择、目标grant与审批先冻结；保留客户旧Runtime |
| Phase 3 | Windows 本地文本文件闭环 | 📋 待开发 | read/search/write/edit、冲突、许可、journal 与补传；完成后方称完整桌面 MVP |
| Phase 4 | 受控命令与应用 Provider | 📋 待开发 | Shell 权限/隔离需先定稿；既有应用能力逐项接入 |
| Phase 5 | macOS 本地能力与发行验收 | 📋 待开发 | 凭证、路径、进程和包资源适配及独立真机验证 |

> 设计：[Agent 桌面客户端 v3](../system/desktop-agent-client-design.md)

> 依据：[Codex、DeepSeek Harness、Hermes 源码调研](../research/desktop-agent-harness-architecture-research.md)；执行侧协作：[Runtime 插件宿主计划](plan-runtime-plugin-host.md)。
>
> 基线：[已完成 Runner 重构](plan-agent-runner-service.md)、[构建手册](../system/desktop-agent-client-build-manual.md)
>
> 本计划跟踪桌面后续工作；不重新打开已完成的 Runner 重构。现有 Shell 属于既有部分完成成果。

## 1. 实施规则与交付边界

> **Runtime接线范围硬限制：当前仅消费第一部分A1～A4。第三方skill的B1/B2暂不开发，仅保留契约/schema/fixture；第一部分完成后交付并停止，不自动进入第二部分。普通“继续开发”不授权B1/B2，须用户明确要求启动第二部分。不能以通用化、预留接口或候选字段存在为由提前开发第三方安装、环境/AI分析、动态登记/执行或UI入口。**

共同约束：[Runner / Desktop / Runtime集成契约v2.0](../system/runner-desktop-runtime-integration-contract.md)。公共Electron壳和任务binding由本工作流主导；Runtime工作流交付统一执行环境/core、插件宿主和独立Runtime管理模块。本计划Phase 2接入已有共同实现，不再复制一套Host；H1～H4定义及责任以共同契约为准。

每阶段开始前先核对最终源码与依赖，补齐本阶段契约细节并更新设计；完成后先更新本登记区，再同步索引。阶段间没有日历或人天承诺，按验收依赖推进。

本轮为纯设计文档，主控静态核对与文档检查，不执行完整三角色流程。后续业务实现涉及共享协议、鉴权、设备/进程生命周期、文件副作用和恢复，按高风险执行开发、自测、独立测试、独立 CodeReview、主控整合；只有确实边界清晰的独立 UI 工作可按常规级别处理。

不自动提交、推送或部署。服务器部署、更新脚本及容器重建/重启均等待用户当场明确部署指令；Windows/macOS 本机验证结果与服务端部署结果分别记录。

## 2. Phase 0：设计与基线

- [x] 核对兼容 Agent 壳、共用 Engine、独立 Runner 及 Web 网关。
- [x] 核对独立 Desktop Shell、旧 Coordinator/Host 空壳及旧 D1 的存在。
- [x] 核对 Runtime 的设备协议、Provider、部分 v2 outbox、Windows/DPAPI 依赖及全局 selected 规则。
- [x] 在原桌面设计路径重建方案，登记本计划与关联文档。
- [x] 阅读三个官方仓库固定 commit 的核心、工具/执行环境与用量实现，形成并登记调研报告。
- [x] 用户确认 AgentRunner 作为共用云端核心，客户机 Runtime 执行本地工具。
- [x] 核对共同契约，补齐Web/Desktop调度同机/异机Runtime、目标grant/产物及专用电脑独占语义。
- [ ] 确认具体首场景、Shell 首发范围与平台优先级；当前按设计建议分期。

验证结论：仅源码与文档检查；没有本轮 build、测试、真实模型或设备执行证据。

## 3. Phase 1：Runner 桌面对话闭环

### 开发内容

- 从 Web Runner Client 中提取可复用 DTO、事件解码和 transport；保留 Web 入口与既有断言，不搬 Web 页面壳。
- 接入已有会话创建/历史、数字员工权限及 Runner 提交/查询；采用 `source=chat` 与已有用户会话。
- 新建桌面对话工作区，展示状态、进度、结果、云端附件、澄清及控制结果。
- 请求键与不可变意图在发送前保存，未确认提交可找回；认证作用域隔离、缓存有界、退出登录停止观察。
- 认证 Fetch SSE + 查询降级；窗口隐藏/切会话不触发 cancel，重启先 discovery。
- 更新 Phase C 占位文案和旧 Coordinator 设计引用，停止依赖 `requestTurn`；依赖盘点后再删除空壳。

### 验收门槛

- 实际 Desktop 页面经过主 API → 独立 Runner HTTP → worker；不能仅用兼容 Agent 的返回替身证明服务接入。
- 模拟接单响应丢失：同键重试仅一个任务；服务不可达不切旧 D1。
- 关闭页面后云端任务继续，重启找回；事件断线、乱序/过期 revision、查询失败不丢原输入。
- 澄清答复续原 runner；普通输入排队；paused/interrupted/取消请求和终态准确展示。
- 切账号/租户不重发旧请求或读取旧缓存；云端历史与投影无重复写入。
- 定向 Client/renderer 回归、frontend build、Desktop typecheck/build 与真实窗口检查；受影响 Web Runner 行为回归。

本阶段完成只能声明桌面对话已接入，不能声明本地文件/Shell 可用。

## 4. Phase 2：Runtime 集成和固定任务环境

### 开工前定稿

- grant 创建/撤销、账户绑定、有效版本与安全存储；云端与 Host 的校验责任。
- `execution_binding` 公共字段、兼容摘要、持久输入/checkpoint/ToolExecutionContext/子执行传递。
- 固定设备路径与旧 selected 路径的 dispatch、claim、结果接纳规则。
- 文件 Provider 的工具 schema、写操作回执、结果 outbox 与核对语义。
- 仅既有企业策略要求任务审批时接原持久审批/等待适配，区别于澄清controls；许可绑定参数摘要与原invocation。不默认为安装插件增加逐次审批。
- 本阶段所需表与迁移若有新增，登记系统/业务表分类、tenant 隔离及数据库变更。
- 与 Runtime 插件宿主计划核对 shared core、配对、插件和监管的实现归属，复用其成果，不另建调度/安装链。
- Web/Desktop共用目标设备与可用grant的服务端DTO和校验；冻结远端产物获取与本机IPC拒绝异机引用的规则。
- 与H4定稿长GUI流程的资源占用范围、owner、续期、跨调用保持及释放；已有单调用锁不能替代证据。

### 开发内容

- 提供不加载无人值守会话任务的受管 Runtime 启动边界，Main 管理进程，renderer 仅调用窄 IPC。
- 复用设备配对，独立保存设备 token；处理旧外置 Runtime、重复实例与主体切换，不覆盖已有客户配置。
- 增加原生工作目录选择、grant 管理和脱敏状态；本机根路径不进入普通业务请求。
- 任务绑定持久化、派发与恢复重验；继承到子执行。
- 支持选择同机受管或异机独立Runtime；Web复用同一验证/绑定接线，不能依赖全局selected。异机目录首期使用目标Runtime预先建立的有效grant。
- 核对打包 Node 入口、Provider 依赖、路径与更新/退出 drain；不假定 process.execPath 在 Electron 内可直接复用。

### 验收门槛

- 在任务接受后更改全局 selected：新任务仍只执行原固定设备；旧工具链沿原路径正常工作。
- 跨租户/用户绑定、伪造 grant、撤权、旧授权版本和能力缺失均被可信边界拒绝。
- 第二实例、账号切换、Runtime 崩溃和应用退出不造成两个领取者，不将杀进程显示为操作取消成功。
- 更新前不丢 pending 结果；受控 Node/Provider 在安装包中实际启动且不泄露凭证。
- 设备协议、授权、公开投影与 Runtime 生命周期独立测试和审查；既有 BOSS/微信/skill-runner 按依赖回归。
- Desktop A未启用Runtime、Web浏览器无执行核心时，仍可调度B；A退出/注销不停止B，显式撤权/取消另验。
- A目录不能生成B的grant，A本机打开拒绝B文件引用；B特定网络/软件不可用时不静默换执行位置。

## 5. Phase 3：本地文本文件 MVP

### 开发内容

- 实现文件引用与双执行器路由：本机 DeviceFileRef 与云端 file_id 清晰区分。
- 实现有界 batch read/search，逐项结果、失败及 revision 明确；避免重复小请求和全部目录上传。
- 实现文本 list/read/search、受权 write/edit；读返回 revision，写前检查权限与内容版本。
- 目录边界与安全打开、大小/编码/输出限制、原子写、journal、回执及 ACK 前结果补传。
- 明确本地修改/产物位置、冲突、未知效果及确有策略要求的任务审批UI；云端模型数据传输告知。
- 原调用恢复按证据核对，不能直接重跑整个文件工具；如原策略要求审批，继续绑定原调用、参数/授权版本。

### 验收门槛

| 场景 | 业务意图 |
|---|---|
| 文件位于本机批准目录，服务器存在同名相对路径 | 必须读取/写入选定设备，不能误用云端文件 |
| 路径穿越、软链接/reparse point、目录替换及校验竞态 | 阻止访问批准范围外的对象 |
| 用户在 read 与 edit 之间改文件 | 返回冲突并保留用户修改 |
| 写前崩溃、写后 ACK 前断线、结果重复补传 | 核对原操作，副作用不重复 |
| 撤权、旧 claim、旧 attempt、批准过期/参数变更 | 失效执行权不能继续写入 |
| 结果无法证明、磁盘满/journal 失败 | 停止并显示核对或失败，不隐瞒未知效果 |
| 云端附件、服务器产物与本地结果同时存在 | 展示和路由正确，上传/保存由明确操作触发 |

使用隔离临时工作区与真实文件执行器验证，模型可用替身控制场景，但不能替换路径校验、写入与回执代码。至少一次Windows安装包执行“选定含写权限的目录 → 读文本 → 实际写入 → 重启核对”无额外弹窗闭环；另在已有企业策略确需审批的场景验证“待批准 → 批准后写入”。再分别验证真实模型联调，记录未验证边界。

本阶段通过后才可将 Windows 本地文本文件 MVP 标为已完成开发；发行及服务器部署验收按实际授权和结果另记。

## 6. Phase 4：命令与应用 Provider

- 先完成Shell权限与隔离设计，明确可验证的OS边界；未隔离的已安装脚本按当前用户权限运行，据实说明。策略要求强隔离却未实现时不启用该能力，不用逐次弹窗代替隔离；不默认增加插件调用审批。
- 精确 command/args、cwd、环境、网络/子进程权限、超时与取消；输出有界，环境按批准白名单传递。
- 受信 Provider 按能力登记、安装和启用；沿既有业务许可与计费，不能让 MCP 自报 schema 获得权限。
- 验证批准参数篡改、命令逃逸假设、进程树取消、未知副作用、Provider 崩溃与结果补传。
- BOSS/微信等共享桌面资源按原锁机制仲裁，保证旧自动化任务兼容。
- 专用电脑长流程覆盖跨调用独占、其他插件/后台观察竞争、续期/断网、崩溃核对与安全释放；兼容单调用锁，未实现流程锁前不宣称整段独占。
- 支持受权脚本一次完成多步确定性处理；模型判断仍回 Runner，云端费用不由设备自报。完整 PTC 在性能需求证实后独立细化。

通用 Shell 与具体应用可拆成独立交付；任何单项成功不表示其他能力已通过。

## 7. Phase 5：macOS 与发行

- 替换 Windows-only 凭证/平台假设，验证路径、符号链接、安全打开、进程/Provider、系统授权和签名包资源。
- macOS arm64/x64 按实际承诺平台分别验证；Windows 结果不能替代 Mac 证据。
- 安装、升级/退出时操作未完、回执待 ACK、最低版本阻断、协议不兼容恢复分别验证。
- 正式签名、更新源和灰度验收独立记录；构建成功不等于发布/部署成功。

## 8. 验证与登记要求

验证仅覆盖当前阶段和真实受影响依赖。后端按 `.claude/rules/testing.md` 与项目测试入口选择定向测试；前端使用现有 scripts，源码变化运行 frontend build；客户端使用各 package 的 typecheck/build/test，涉及进程与包资源增加真实启动检查。

独立测试与 CR 的交接包含：范围、最终文件、业务意图、风险、命令、退出码、通过/失败/跳过及未验证项。修复后重跑受影响检查；同一状态已有有效证据不机械重跑。

性能验收分别记录模型、排队/领取、Provider 启动/执行、回执接纳、输出字节、模型轮数和 token。用相同结果比较逐项读取与 batch；未测量前不承诺延迟或成本收益。设备快路径不作为现有协议首版替换要求。

各阶段在本计划记录实际结果、问题和遗留；索引只保留一句话状态。全部约定范围完成后再移动到 `ideas_finished.md`。如分期取消或另行立项，先调整设计和计划，再同步索引。

## 9. Desktop / Runtime 共同边界登记

2026-10-09用户逐条复核后修订：Runtime为本批共同契约/schema/fixture唯一写入者，文件范围与进度见[Runtime计划第6节](plan-runtime-plugin-host.md#6-desktop--runtime-共同边界登记)。Desktop暂停对这些共同文件的并行写入；公共入口/H2/H3仍归Desktop。v2.0/候选0.2按用户决定取消插件额外审批并统一code/error；此处仅登记交接，不代表Desktop已经重新消费验证。

Runtime接口产出通知（2026-10-08）：Runtime工作流在共享目录`contracts/runtime-host/v1/`编写首批schema与fixture，入口为该目录README。本批Runtime唯一写入共同产出及本节提示；Desktop可直接读取、消费fixture并提供adapter验证结果。H2/H3及公共壳不在本批改动范围；初版待验证，不将文件落盘视为共同wire已冻结。

**候选0.1已交付**：[共享README与运行命令](../../contracts/runtime-host/v1/README.md)、[管理fixture](../../contracts/runtime-host/v1/fixtures/management.json)、[输出fixture](../../contracts/runtime-host/v1/fixtures/outputs.json)、[测试专用消费fake](../../contracts/runtime-host/v1/fixtures/consumer-fake.mjs)。首批7份schema保持简约，Desktop可直接接管理请求/响应、状态刷新及设备产物展示；不要复制一套不同DTO。请在本计划H1/H4登记真实adapter兼容结果或缺少的具体场景；Runtime将据结果登记共同冻结。H2 binding/grant和H3公共壳仍由本工作流主导，不因该候选自动完成。

| 检查点 | 主导 | 状态 | 本批写入者/记录 |
|---|---|---|---|
| H1 管理port与wire schema | Runtime | [见Runtime计划](plan-runtime-plugin-host.md#6-desktop--runtime-共同边界登记) | 桌面提供consumer adapter/fake，接入同一管理语义 |
| H2 固定设备binding/grant/审批 | Desktop | 📋 待开发 | 本批唯一写入者与文件清单实施前登记，Runtime提供核验adapter |
| H3 公共壳与实例监管 | Desktop | 🔧 进行中 | [实施接口/文件归属](../system/desktop-agent-client-design.md#137-第一部分h3实施批接口与唯一写入者)已登记；A1接收通过，Node22/真实adapter/验收包待完成 |
| H4 插件登记与产物 | Runtime | [见Runtime计划](plan-runtime-plugin-host.md#6-desktop--runtime-共同边界登记) | 桌面展示与模型输入按共同artifact契约适配 |

2026-10-08：共同契约v1.0已登记到双方设计/计划和AGENTS；wire schema、代码及双向兼容验证未完成。双方阶段编号不决定底层实现归属。

2026-10-08：用户向Runtime工作流转交桌面定位，共同契约补充为v1.1，明确统一任务执行环境、本地批量计算和审批/计费边界；本计划原文件/程序分期保持，具体schema仍先经H1～H4冻结。

2026-10-08：本工作流核对契约并补充v1.2，用户确认Web/Desktop可调度同机或异机独立Runtime。补齐目标目录、网络、产物与长GUI资源独占。仅修改共同契约、双方设计/计划引用与交接、桌面索引；Runtime内部方案不代为开发或验收，H1～H4仍待实现前冻结。

2026-10-08：Runtime工作流审阅后补充共同契约v1.3：批准的资源需求、旧进程停止核对、断网许可与调用/占用/流程时限。Desktop在H2/H4冻结时接入这些约束与阻断投影；Runtime负责真实仲裁/进程停止证据。仅同步共同引用和交接，未改变本计划阶段状态，未代表Desktop会话完成接受或兼容验收。

2026-10-08：Desktop工作流实际审阅并接受v1.3，共同架构/行为契约定稿；无需新增架构条款，进入H1～H4分批接口冻结与各自实现。已核对skill的900秒缺省/1800秒上限及单调用桌面锁；无业务代码或schema变更，不改变Phase 1～5待开发状态，不代替双向兼容验收。

2026-10-08：Desktop已收到并读取Runtime候选0.1共享README、管理请求/响应、Host状态/通知、输出schema和测试专用consumer-fake。静态核对与v1.3的管理/业务分离、原结果协议和设备产物归属一致；后续真实adapter以该目录为格式来源，不复制生产模型或导入测试fake。Runtime独立测试/CR结果见其计划第7节，本工作流未重复运行或宣称独立验收；H2/H3及真实Host、main/preload和服务端消费仍待实施，候选未共同冻结。

### 9.1 候选0.1消费审阅（2026-10-09）

范围：7份schema、47个正反fixture、README、消费fake及校验代码，与共同契约v1.3及桌面设计对照。本次由Desktop工作流审查、运行定向检查并更新本设计/计划；不修改Runtime拥有的schema、fixture或代码，不代表完整独立测试/CR流程或真实端到端验收。

**当前结论：格式分层合理，未发现需要改动共同架构或现有schema字段的冲突；消费参考P2已修复并由Desktop复核关闭，候选继续保持未共同冻结。以下保留原发现及修复证据。**

| 项目 | 审阅结论及桌面影响 |
|---|---|
| H1管理面 | 管理与业务执行分离；request_id/request_key用途分开，operation可查询，错误与版本策略够用；Main/preload按该格式适配 |
| 状态投影 | Host state、connection和Runner任务分离，通知只触发snapshot查询；须补下述水位/换实例保护 |
| H4插件 | skill与MCP契约分开，代码路径不上云，安装ready/审批投影不代替授权；摘要规范化、嵌入schema及资源需求由真实接纳方验证 |
| 输出/产物 | complete与effect分别表达完整性和业务效果；设备/invocation归属可验证，仍须包装进原Device结果及ACK，不直接信任脚本effect |
| H2/H3及长占用 | 本批明确不覆盖；无须重做桌面对话设计，但固定设备、grant/审批、实例监管和跨调用资源占用仍是后续实现依赖 |

#### P2：消费fake未完整处理revision水位和新实例

位置：[consumer-fake.mjs](../../contracts/runtime-host/v1/fixtures/consumer-fake.mjs)的describe/getState分支（23～32行）。可复现两种情况：

1. describe返回实例A、revision=10，随后getState返回同实例revision=9，fake接受并保存9；已知水位未参与校验。
2. 已有A的快照revision=9，在同一逻辑管理连接内describe返回新实例B、revision=0，再接收B/0，fake忽略新快照并保留A。此场景没有先调用reconnect；真实Main管理通道在执行实例重启时未必断开，不能依赖断线清理才能换实例。

样例需记录同实例有效describe及已见事件水位；新实例清理旧快照/实例待查询状态，并隔离旧响应。低水位快照不能作为当前状态完成刷新；订阅/查询竞态由adapter补查或脏标记收敛。字段已足够，不要求新增连接代次wire或事件账本。Runtime负责修正共享fake/fixture，Desktop真实adapter增加对应回归后记录消费结果。

验证：现有Python校验5项通过（7schema/47fixture）；Node6项通过，0失败/跳过。另用内存构造消息调用真实fake，分别得到`accepted_revision=9`和`ignored=true,snapshot_instance=A`，证实现有测试未覆盖上述边界；追加检查只用于复现，无生产文件修改。未启动真实Host、Electron或服务端，未证明产物授权、模型图片输入或写动作恢复。共同契约维持v1.3。

#### Runtime修复交接（2026-10-09）

Runtime已按上述P2修复[消费fake](../../contracts/runtime-host/v1/fixtures/consumer-fake.mjs)，补齐同实例最高水位、新实例清旧快照/重置水位、旧实例在途回复隔离和通知/查询竞态保护；[Node回归](../../contracts/runtime-host/v1/tests/consumer.test.mjs)新增3项。Schema、47个静态fixture、候选0.1与共同契约v1.3不变。[共享README](../../contracts/runtime-host/v1/README.md)已说明真实adapter还需结束失效Promise并补查/保持dirty。

Runtime最终独立测试Node 9项、Python 5项及29项补充边界断言通过，独立CR通过；完整证据见[Runtime计划](plan-runtime-plugin-host.md#7-当前接口与依赖)。这条为Runtime交付记录，保留本节原审阅；Desktop复核和真实adapter联调仍待本工作流登记，不能把fake修复当共同wire已冻结。

#### Desktop修复复核（2026-10-09）

Desktop已读取修复代码及新增回归，运行Node9项全部通过、0失败/跳过。再次独立构造原两个消息序列：describe水位10后的snapshot9返回`ignored=true,snapshot=null`；同连接A/10切换describe到B/0后接受B/0，返回`ignored=false,snapshot_instance=B,snapshot_revision=0`。原消费fake P2关闭；schema与Python验证相关内容未变，本次未重复运行Python。真实adapter的失效Promise收尾、低水位后补查/dirty收敛及Host/服务端联调仍未实施，不登记wire冻结。本批Desktop仅更新自身设计/计划，不改Runtime代码或发消息到其他会话。

### 9.2 用户复核后候选0.2交接（2026-10-09）

Runtime按用户逐条确认更新共同契约v2.0及[共享候选0.2](../../contracts/runtime-host/v1/README.md)：安装即插件授权/默认启用，取消registration审批；管理与工具输出固定数字code/error；skill新增入口description/可选output_schema和最小code_map，AI先分析SKILL.md，必要时才补传源码。候选0.1未冻结，需Main/preload消费参考显式迁移并重新验证；不能以第9.1节对旧候选的复核代表本批接受。

Runtime验证结果以其[计划](plan-runtime-plugin-host.md#7-当前接口与依赖)为准。Desktop的重新消费、真实adapter/Host/服务端联调及H2/H3仍待本工作流登记；本条是Runtime交接记录，不改变桌面业务阶段状态。

#### Desktop候选0.2消费审阅（2026-10-09）

已读取v2.0、候选0.2 README及更新schema/fixture/fake，实际运行Python7项（7schema/56fixture）和Node11项，0失败/跳过。检查包含固定code/error成功/失败形状、旧0.1形状拒绝、operation内外结果区分、失败输出保留effect、手册型空入口，以及已修复的水位/实例/旧连接隔离。本轮未发现新增阻断或需要修改schema的消费缺口；接受0.2作为待接线格式输入，不能将本审阅记为真实adapter验收或共同wire冻结。

| 桌面迁移项 | 实施约束 |
|---|---|
| 管理回复 | 以严格数字code判定请求结果，成功error为空；查询成功再检查operation.status及自身code/error，不沿用旧error对象 |
| 工具内容 | 不再读取success；非零code也保留effect/complete及产物，不能映射为未执行或自动重试；通过adapter包装进现有Device结果，现有生产协议不改义 |
| 插件展示 | 移除registration/批准流程；安装成功默认enabled=true，依赖/配置独立决定ready；认证、配对、设备/workspace及确有既有策略的任务审批仍执行 |
| skill描述 | 消费entry.description及可选output_schema；空entries/code_map不展示执行按钮；code_map不是执行入口，原始SKILL.md与AI生成内容分开 |
| 安装分析 | 先手册，不足再传必要源码；分析请求、用量、源码发送及Host核验由后续真实分析器接线实现，不将新字段视为已上线功能 |
| 版本 | 0.1未冻结/上线，同目录0.2显式替代；api_major/schema_version=1不表示兼容旧候选，真实adapter按0.2建立验证，架构契约以v2.0为准 |

本批Desktop仅更新自身设计/计划的消费结论与迁移约束；未修改共享schema/fixture、未实现业务代码或启动桌面业务开发。H2/H3、真实Host producer、main/preload adapter、服务端接纳和资源/图片联调继续待实施。Runtime的完整独立测试与CR见其计划，不把本工作流运行的定向测试称为另一次完整开发流程。

### 9.3 Runtime两部分实施范围交接（2026-10-09）

用户限定当前第一部分为Runtime UI＋第一方BOSS/weixin/wecom CLI插件安装管理及必要core/既有执行兼容。第二部分第三方skill的安装、AI分析、动态登记/执行暂不开发，契约/schema/fixture保留，不作为公共Runtime壳或第一方插件接线的前置。Runtime交付管理feature/port，Desktop按H3负责公共main/preload、Node监管、两产品入口/构建；不重做完整聊天工作台或提前增加第三方skill页面。

共同契约v2.0与候选0.2格式不变，此条仅范围交接。以[Runtime计划第一部分](plan-runtime-plugin-host.md#第一部分runtime-ui与第一方cli插件)为实施清单，首期UI只展示支持的第一方安装入口；第三方包返回明确不支持，不把保留schema当已实现功能。本段由Runtime登记，不代为声明Desktop接受/实施完成。

Desktop范围核对（2026-10-09）：已读取重排的A1～A4及B1/B2，按本边界消费Runtime后续管理模块；第三方skill页面、安装、Python/sidecar、AI分析与动态登记不提前实施、不阻塞第一方交付。H3由本工作流负责必要公共壳/Node监管/两产品接线，Runtime交feature/port；该接线不要求完整Desktop聊天/文件/Shell先完成，H2新增语义另行冻结。v2.0/候选0.2/schema未变，复用前批验证，不重复跑同状态测试。本批仅更新Desktop自身设计/计划，未启动业务开发或宣称接线完成。

范围限制补强已同步至本计划实施规则：A1～A4完成后停止，B1/B2只有用户明确要求启动才实施；接口与业务代码不变。

### 9.4 H3首期设计输入交付（2026-10-09）

本工作流唯一更新Desktop设计第13节及本节，供Runtime A1/A2依赖注入和A3/A4公共壳接线使用；不修改正在由Runtime维护的共同schema/契约。H3仍待开发，A3/A4不因本文交付完成。候选0.3的plugins.list实例/revision包装及可选version由Runtime产出，接到正式通知后单独消费复核；不把此拟议形状写成已冻结格式。

| 输入 | 当前交付 | 后续验证/责任 |
|---|---|---|
| Node | 锁定22.23.3 Windows x64，官方固定归档/校验值，明确资源与Host入口目标 | H3维护构建清单/校验实物，Runtime注入路径；未下载/运行目标Node |
| 私有通道/选择引用 | Main直属child继承IPC；受信选择器引用绑定用途/实例/期限/请求键；旧外置不支持时显示冲突 | H3实现通道/IPC权限及引用，Runtime消费与核验第一方包；不开放网络管理或任意路径 |
| 去重secret | Host侧既有DPAPI adapter能力，独立home密文文件、首次原子创建、稳定恢复 | Runtime实现持久幂等及摘要；不扩大renderer账户凭证hydrate白名单 |
| 单实例/drain | Host锁覆盖普通领取，应用锁/桌面锁用途分开；退出门等停止核对/补传 | H3真实进程与产品退出接线，Runtime拆分领取/执行/回执控制；旧CLI兼容按实际能力拒绝/迁移 |
| 两产品/写入归属 | Desktop identity保留，Runtime独立identity/userData，共用home/core/Node；公共入口/package/构建由H3唯一维护 | Runtime交core/Host入口/feature资产；实际批次先登记精确文件，不并行改公共壳 |

已核对Electron43.1.0、原DPAPI文件与home、Provider的process.execPath/环境继承、sessionTasks单实例仅覆盖引擎、PollLoop停止含中止在途，以及构建Node>=22.12.0/两CLI>=22；据此形成最小接线设计。官方Node元数据只用于锁定公共资源，不等于二进制兼容/签名验收。未启动Host、安装或运行CLI、生成生产信任根、构建安装包或修改业务代码；本批无需重跑未改变的schema/fixture测试。

### 9.5 候选0.3消费复核（2026-10-09）

收到Runtime候选0.3落盘通知后，读取共同契约v2.0、README迁移说明、management-response/schema/fixture/消费参考，以及Runtime设计第12节、计划第8节和Provider10.2离线包格式。本批仅由Desktop更新自身设计第13.5节与本节，保留第9.2节0.2历史记录；共享目录与Runtime文档仍由Runtime唯一维护。

验证：实际运行Python7项（7schema/59fixture）及Node14项，全部通过，0失败/跳过；内存补充18项schema断言覆盖旧数组、缺实例/revision、非法类型、可选version及路径字段拒绝，12项消费断言覆盖通知期间低水位列表、旧连接复用请求ID、旧通知及同连接换实例。补充检查未写入共享测试，也未启动生产Host、Electron或服务端；不是实际adapter验收或Runtime独立测试/CR的替代。

结论：0.3与v2.0一致，未发现新增桌面消费阻断，接受为待接线格式输入。管理回复的列表对象是0.2到0.3的不兼容迁移，不能继续按数组解码，也不能因major仍为1自动兼容旧候选。真实接线遵守下表：

| 迁移项 | 实施约束 |
|---|---|
| 列表/版本 | 解码instance_id/revision/plugins；仅展示实际可选version，缺省“版本未知”，release_id不作版本推算 |
| 水位/竞态 | state/list/describe/通知共享最高水位，状态与列表分别dirty；换实例清两种快照，旧连接隔离。更高revision优先接纳，只在同revision时比较已接受列表的本地查询序号，不能按发送先后丢弃更新快照 |
| 查询收尾 | 结束失效Promise，明确超时/取消/失败并补查；丢弃旧结果不能标记刷新完成，fake不直接导入生产 |
| Runtime操作 | 先查原持久operation再核验新引用；重试不重装。安装就绪、升级/停用/卸载、旧入口优先级及重配对沿Runtime第12节，不另建管理状态机 |
| 包/H3边界 | 离线envelope/payload、预构建依赖与H3设计输入相容；正式信任配置/三实包/真实Node与进程监管仍待验证，不生成临时生产信任根 |

初次通知时Runtime独立测试/CR仍进行中，最终交付及Desktop复核见下节。候选0.3未共同冻结，H2/H4新授权/跨端版本语义与H3实物接线保持原待实施状态，A3/A4及Desktop业务阶段不升级完成。B1/B2继续暂不开发；未修改业务代码、共享schema/fake、提交或部署。

#### 候选0.3最终修复消费复核（2026-10-09）

Runtime最终交付登记：独立测试与CR发现同一P2，初版列表发送序号错误地屏蔽先发查询的更高revision；已改为实例/revision优先，仅同revision比较appliedListQuery，并增加第15项回归。其计划第8节记录独立Python7/59＋29形状断言、Node15＋72消费断言通过及最终CR无遗留P0/P1/P2。这些是Runtime工作流的证据，不冒称本工作流执行。

Desktop读取最新fake/回归和最终登记，实际重跑Python7项（7schema/59fixture）、Node15项，0失败/跳过；补充11项内存断言覆盖先发更高revision优先、后发尚未返回、同revision迟到拒绝、后发失败后的更高revision接纳，以及通知水位继续阻止旧列表。共享schema/fixture/fake未改；上表及设计第13.5节同步最终排序语义，初次Node14及补充30项记录保留为历史。

结论：此次P2已关闭，接受最新候选0.3作为待接线输入；真实Host/Main、Windows安装包、正式签名信任/实包和H2/H4输入仍按原验收门推进，不能将内部格式测试/CR通过写成wire已冻结或A1～A4已开发完成。

### 9.6 A1开发资产接收与兼容核验（2026-10-09）

收到Runtime A1内部实现交接，读取[core README](../../clients/shared/local-tool-host-core/README.md)、Runtime计划第9节、受管入口与管理实现；本批仅消费开发资产、更新Desktop设计第13.6节和本计划，未修改Runtime源文件/共享schema或公共壳。原Runtime全套/独立测试/CR证据以其计划为准，未重复执行或代为标记A1整体完成。

| 本工作流实际检查 | 结果与限制 |
|---|---|
| tgz身份/内容 | 0.2.14、3060079字节，SHA-256 b5db68c2677139c75455e8feea4844d4910385f3bfc06348b1d34d5cafc9bccc；完整入口/sessionTasks/core/SDK/zod，无BOSS强依赖/Runtime tests |
| 仓库外布局 | 安全解包到新建临时目录，dist/src完整复制为resources/runtime/host，带同目录package.json/node_modules；3564个文件逐项摘要一致，无npm pack/install/build |
| 真实IPC/状态 | Windows、开发Node24.13.0、隔离home、--supervisor=desktop；25项最终断言通过，11回复/4事件全部通过候选0.3 schema；空Host/unpaired，不连接云端/执行Provider |
| 生命周期/恢复 | 缺配对start明确失败，变更插件code3；stop重发同operation，同home第二实例拒绝并退出1；断连实际exit0，再启动换instance且原DPAPI secret/operation保持 |
| 探测纠正 | 首轮等待stdio close超时，追加exit记录确认Host已退出0；最终按实际exit核验并分开清理诊断流，不能把最初超时写成drain失败或忽略它 |

消费结论：A1开发资产可按约定布局供H3集成，不需要重新实现Host。实际Main/preload接线、可信环境注入、能力显示、Promise/连接代次、退出门及两产品打包仍由Desktop唯一维护，H3保持待开发。Runtime describe当前只有events，安装器未交付；UI不能把可查询列表当plugins管理可用。

待协调的具体缺口：启动阶段的同home冲突尚只返回通用诊断/exit1，没有H1结构化code4；本批证明第二领取者被拒绝，不证明Main能精确分类失败原因。H3先保留启动失败/需核对展示，精确原因传递需与Runtime统一入口适配，不自行解析日志、改共享格式或抢锁。历史journal/session未结案的身份替换继续code11且保留事实，不提供删除目录放行；A4身份切换验收仍待完成。

未验证固定Node22.23.3、真实配对/云端/GUI/在途长任务drain、正式信任根与三CLI自包含实包或Windows产品安装包；不能据空Host smoke冻结H1、升级A3/A4状态或声称完整桌面Agent可用。B1/B2仍暂不开发。仅改原有设计/计划，不新增功能索引，不提交/推送/部署。

### 9.7 第一部分H3实施登记（2026-10-09）

依据已核对的人类继续开发指示，按[设计13.7](../system/desktop-agent-client-design.md#137-第一部分h3实施批接口与唯一写入者)登记callback/bootstrap错误/构建profile与精确文件责任。本批高风险：可信IPC/目录暂存、凭证隔离、进程退出和启动/打包链路；主控开发与定向自测后，独立测试与独立CodeReview分别验证，主控整合。Runtime实现资产保持唯一写入者，缺共享类型或包只关闭相应能力，不单方修改schema。

| 子项 | 状态 | 完成证据/待项 |
|---|---|---|
| H3-1 Node/产品构建 | ✅ 完成（2026-10-09） | 固定22.23.3，两产品及acceptance身份隔离，NSIS/manifest实际核验通过 |
| H3-2 Main/preload/选包/监管 | ✅ 完成（2026-10-09） | 白名单/冻结快照/私有callback/退出监管及异常回归通过；原生选框待人工 |
| H3-3 全局入口/真实接线 | ✅ 完成（2026-10-09） | Desktop/Web构建、真实三包安装恢复22断言、包内启动12断言通过 |
| H3-4 独立验证/人工验收交付 | ✅ 完成（2026-10-09） | UI握手竞态独立52项/CR通过，新包重建/独立验包/实际启动退出通过 |

主控实现检查点：新增受信Native选包引用与冻结快照、直属Node私有callback、H1白名单/正常describe握手、同实例管理与stop确认后实际exit监管；Main退出/更新接安全门，Runtime关窗托盘，独立入口不创建Desktop云端会话。两项选包测试及原四项监督测试加入桌面默认套件后45/45通过，0失败/跳过；Electron TypeScript与Desktop类型检查通过，frontend Desktop/Web构建均exit0。随后补握手超时复用仍存活Host、reconciling保留进程且同stop operation重试两项，监督定向6/6通过；修复disconnect/exit重复失效通知，TypeScript重新通过。上述为主控自测，不代替独立测试/CR或Node22/实际Electron验收。

当前独立CR已启动，产品配置/固定Node资源和包脚本仍实施中；Runtime A2新版受管资产及签名测试包尚待正式交接。不得用旧A1 tgz宣称安装功能已接通，不生成生产信任根，不把当前接线登记为H3/A3/A4完成。

公共壳独立检查点：独立测试发现确认退出后重新开放接纳会让晚到start重建Host，已改为成功退出永久闭合、失败才恢复接纳。独立CR发现旧reconciling stop不会自行恢复、disabled更新先停Host、选包底层FS错误含绝对路径三项；分别改为历史terminal事实保持且下次明确退出新停止核验、更新先验证downloaded、选包所有FS错误脱敏。running超时仍查原operation，不强杀或重放业务动作。最终独立Desktop typecheck/tsc及48/48默认测试、frontend typecheck:desktop、14项选包/回调/环境断言及4项晚到启动断言全部通过，0失败/跳过。补充复验首轮使用缺instance_id的旧fake被安全检查拒绝，纠正测试输入后通过，保留该检查记录。独立CR复核无遗留P0/P1/P2，仅覆盖公共壳源码；包脚本/实物尚不包含在本结论。

UI主控检查：已用真实Chrome无头加载Desktop构建产物及受控Runtime port fixture，在1100×850及实际最小720×700窗口检查未配对节点禁止启动、配对字段可用、插件空状态、无横向溢出；8项断言通过、无pageerror，已查看截图。最初项目未安装Playwright测试包，内置Playwright默认版本浏览器亦未安装，改用已提供的Playwright及本机Chrome后成功；不是实际Electron/原生dialog/托盘验证。

主控补充启动检查：在Runtime资源尚未准备、缺product配置的原Desktop默认路径运行`node scripts/smoke.mjs`，实际Electron43 smoke exit0、AGENT_DESKTOP_SMOKE_PASS，原API/CORS/流式/上传下载检查通过；没有启动Runtime业务实例。此结果只证明原产品启动兼容。

待A2正式交接时核对：当前Runtime的`src/productTrust.ts`仍在production校验中按key_id名称正则推测测试根，与13.7指定的显式test_only标记语义不一致；H3 product/prepare不复制这种推测。该文件由Runtime唯一写入者修正或登记兼容理由，本工作流不抢写；当前源文件尚在实施，不能将该观察当最终资产审查通过。acceptance产品可先按实际test_only根继续验证，production的共同输入在此项关闭后再验收。

固定Node实物检查点：官方22.23.3 Windows x64 ZIP按清单SHA-256校验，实际version/platform/arch/ABI为22.23.3/win32/x64/127；在该Node下运行公共壳9项定向测试全部通过、0失败/跳过。另由真实Supervisor启动先前已核对的仓库外A1 Host与新隔离home，13项实际IPC/DPAPI初始化/空未配对状态/列表实例归属/stop与实际exit/一次disconnect及晚到start拒绝断言通过，收到2个事件、无云端调用。此为A1资产与Node22兼容证据，未证明新版插件安装器、产品信任配置或Electron托盘/安装包接线。

包装独立检查点（修复与最终复验中）：初版固定Node22定向23/23、默认56/56及缺输入CLI拒绝检查通过；独立test/CR均发现createPublicKey接受PKCS8私钥导致public_key字段可能携密钥入包的P1。包装唯一写入者已改为只接受SPKI公钥PEM，并扫描完整resources及解包asar，包括JSON转义私钥；CR原复现已被拒绝，正常公钥仍接受。另修inputs整体摘要/唯一路径与实际packagedInputs逐项字节绑定、Host/root已验Buffer冻结后解包，以及固定Node无业务import整棵Host依赖门；最终独立复验另行登记，不把初版结果套用到修复后状态。

旧Desktop目录兼容P1：独立CR根据setName与旧asar名称提出风险。主控实际Electron43两个隔离进程证明，在首次getPath(userData)前setName会将默认目录名从aid-agent-desktop改成AID Work Agent；现Main先捕获原路径、再设置显示名并显式固定product.userDataPath或原路径，Runtime/acceptance仍使用明确独立目录。最初探测先读取路径后改名，缓存使变化不可见，不能用于排除原风险；第二次按原实际顺序验证后确认修复意图。所有probe均在appready前切换Temp userData，未读取生产账户数据。

选包大小前置门已对齐Runtime第一方外层archive预算512MiB，避免哈希/暂存Host不能接受的超限字节；不是内层展开上限。新增native稀疏文件超限测试后选包4/4通过，TypeScript通过。构建手册第12节新增真实交接资产到验收包的命令和人工步骤，目前仍标待最终资产，不称可验收。

Runtime最终信任输入还需确认：其productTrust使用相同createPublicKey校验模式，若未限制公钥PEM也会有同类私钥接纳风险。H3解码/打包已拒绝该输入；Runtime由其唯一写入者处理，不能据现有文件完成宣称production输入门已验收。

第一方final-acceptance资产接收：收到Runtime设计者正式三包/root交接后，主控只读逐份核对字节数/摘要，全部与交接一致；根通过H3严格wrapped/SPKI解析，schemaVersion1、acceptance、1个显式test_only=true根、限定三第一方providers，root文件SHA-256为cdc5d4b0f5f27f62f23c06a43436217306525f3f1c16fbe16a6396eca28c3cec。实际源文件productTrust已移除key_id正则猜测，前述名称差异关闭；其私钥PEM校验观察仍待唯一写入者核对。新版Host tgz独立验证尚未正式交接，尚未生成完整资源或Windows安装器，不复制初版root/包混用。Runtime报告的包离库version/doctor/OCR及独立核心测试以其计划为准，未冒称本工作流执行。

| final-acceptance交接文件 | 字节数 | 主控核对SHA-256 |
|---|---|---|
| boss-0.3.0-dev.aidplugin.zip | 4442404 | 6c1f4578498ff23ab8138b5fb1c51994f4dbd209a7bf1754e43d8726bce73d11 |
| weixin-0.1.0-dev.aidplugin.zip | 117056592 | c3f17d0460aa596b273c76b24299d74b7cda0a96af3c07c86564f9af7c1285f3 |
| wecom-0.1.0-dev.aidplugin.zip | 117215512 | 35dfd1b23ae2dab194763dbb4dae5ea80a4f9e02c136d32887a5524b68f9a1dc |

资产目录为`clients/runtime-plugin-packaging/release/final-acceptance`。仅用于隔离验收，不替代production批准的信任根或Windows发行证书；B1/B2仍暂不开发。

包装最终源码检查点：独立Node22定向26/26、默认57/57和22项隔离断言通过，0失败/跳过，覆盖私钥/公私钥拼接拒绝、JSON转义秘密扫描、清单fingerprint/重复path/缺resource/packagedSHA篡改前置拒绝、实际无业务依赖import与缺依赖失败、独立Electron临时目录路径保持。另根据本地electron-builder26实际签名选择器发现production会重签固定Node导致批准摘要变化，已只排除固定资源路径后缀，保留Main/NSIS/其他EXE签名；独立受影响9/9测试通过、0失败/跳过，无生产证书不冒称签名构建成功。独立CR最终无遗留P0/P1/P2，范围为源码与隔离门，完整A2/NSIS实物仍另验。

A2候选消费记录：c48f390fee067a516f64189889858384f8e82e35440743532eb0f75c17a39c8a按固定资源布局准备3682文件成功；真实Electron隔离appData/Host home完成初始管理、隐藏窗口、原生选择器结果stub后的实际Main选包/私有callback/BOSS签名安装及同key查询。首轮将文件名-dev误作version，断言在实际0.3.0处失败；该测试输入已纠正，不把首轮记成功。Runtime压力测试三包refresh超过15秒，Runtime改顺序inflate及feature120秒，Main本地管理/初始握手同步120秒；业务许可/实际停止/drain不变。mock clock新增超过15秒但预算内describe/list、120秒耗尽/晚到旧相关ID拒绝/复用存活Host、断连立即结束三个回归后监督9/9通过，TypeScript通过；首轮mock Child的connected只读赋值编译失败，改getter状态后通过。

后续20a6263fe488d5b0916b883ff077080e722f3f293369eb29b7bf7b5defe0f665候选用于顺序inflate接线验证，仍待最终摘要；Runtime再次统一SPKI公钥helper后需重打包。三包/root字节未变。当前不构建声称最终的Windows安装器，待末次交接后重新prepare并锁输入；候选消费及格式测试不代替完整人工验收。

120秒预算delta独立验证/CR通过：固定Node22监督9/9、0失败/跳过，另外mock Date/timer确认超时不kill/disconnect。预算说明：30秒是stop已接受后的轮询观察deadline，在每次operations.get回复后检查；单次管理RPC仍可能等待120秒，故不是整个drain/退出耗时上限。实际child退出观察15秒亦只报未确认，不强杀。两位独立角色未发现新增P0/P1/P2，未把候选实包登记发行冻结。

最新SPKI候选f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f（3184959字节）替代20a/c48f，正按上述三包/root重新prepare；不使用旧资源生成最新产品。Runtime报告压力刷新86.6秒、普通5.9～7.2秒，支持120秒等待预算；此为来源侧证据。Desktop renderer已重建为feature120秒源码，Electron TypeScript/copy通过；原Browserslist数据旧提示仍存在，不扩本批修复。最新tgz来源侧最终独立交付尚待确认，真实产品联调后再登记。

最终资产冻结与真实窗口：Runtime声明f52b及final-acceptance三包/root为最终验收资产，来源独立CR/末次离库三包生命周期证据见Runtime计划10节；不等于共同wire/production冻结。主控f52b prepare exit0（3682资源文件）；真实Electron43在隔离appData/Host home中完成Runtime管理、三签名实包安装/default enabled、原键同operation、实际清单版本0.3.0/0.1.0/0.1.0、关窗隐藏、重开、两次安全退出及重启后3插件/原operation恢复，22项断言通过、无pageerror/残留Node。原生文件选择器结果由测试stub提供，之后Main IPC/可信文件handle/冻结快照/私有callback/Host验签均真实；OS对话框交互与实际业务登录/操作仍留人工。三个ready=false为真实运行条件诊断，不虚构登录/就绪。已查看真实窗口截图。

真实窗口第二轮首探测误把上一UI operation的“已完成”当新安装完成，得到2而非3插件；已改为等待新operation_id后再核terminal，最终22断言通过。该失败属于测试等待条件纠正，不改生产UI或Host，不将首轮计成功。

Windows包装首构建已生成NSIS，但package整体exit1：powershell.exe继承PowerShell7的PSModulePath导致Security模块装载失败，签名探测stdout为空；主控pwsh对相同installer实际Status=NotSigned，证书环境变量未设置。包装唯一写入者正在隔离5.1签名探测环境，保持原签名门，随后重新打包及独立复核。当前不登记安装器验收PASS或交付摘要。

#### H3最终验收交付（2026-10-09）

后续状态：Runtime提交前独立检查发现describe握手期间高revision通知丢失的controller竞态，由其feature唯一写入者修复并独立测试/CR。下列a8b0安装器与记录保留为上一批历史，不能代表修复后的最终源码；暂停该批验收交付，待稳定增量后按标准renderer/package/verify流程替换输出与摘要。本工作流不修改Runtime feature或提前重打包，共同wire仍不冻结。

上述签名探测问题已关闭：仅签名子进程按大小写清除继承PSModulePath，显式加载Windows PowerShell自身Security模块；错误直接失败，acceptance=NotSigned/production=Valid门未放宽。固定Node22包装10/10独立测试（含真实污染模块路径及带单引号文件名）通过、0失败/跳过，独立CR无遗留P0/P1/P2。helper及固定Node清单已纳入构建输入摘要，未手工补写manifest。

主控以同一f52b最终Host重新执行标准package，exit0、RUNTIME_PACKAGE_PASS及RUNTIME_VERIFY_PASS；独立测试再运行标准verify也exit0。3682项资源、实际asar/dist、Host依赖图import及产品身份/信任配置完整核验通过；inputs3713/packagedInputs3704（resources3683含manifest＋dist21）。当前输出：

| 项目 | 最终记录 |
|---|---|
| 安装器 | `clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe` |
| 字节数 / SHA-256 | 126240834 / a8b0e1df3ed4c95d7d281db3dc30dcd8067c0d1e6b800c99c61b082a69bc8bf1 |
| 发行记录 | `clients/agent-desktop/build/runtime-release/acceptance/runtime-release-manifest.json` |
| 构建输入 SHA-256 | 4ab5f39f61a7d33da89f24173941a44d7780e6836d6e8f629fd466fa93989a3d |
| Host / Node | f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f / 22.23.3、ABI127 |
| 产品 / 签名 | cn.aidingyi.agent.runtime.acceptance / NotSigned（仅验收） |

主控实际启动此次win-unpacked EXE（app.isPackaged=true），包内resources、Runtime产品名称/独立验收userData、未配对禁止启动、真实Node/Host管理features与getState、关窗隐藏/重开、无pageerror及实际exit0共12断言通过，CIM核对无该包或资源Node残留。启动前确认两验收目录不存在，测试只建立独立验收profile并保留未配对空Host，不读取生产home/账号；没有运行NSIS安装。已查看包内窗口截图。首次探测误用不存在的runtime.getState被白名单拒绝，修正为getState；第二次Playwright在close后取process对象失败，改为启动时保存child引用；最终检查通过。这两项为测试脚本纠正，不改产品代码或包输入。

H3开发交付完成，第一部分具备人工验收包；[构建手册12节](../system/desktop-agent-client-build-manual.md#12-独立runtime首期验收包)列出同批三包与验收步骤。人工仍需确认NSIS安装/原生选择器、软件实际登录及业务条件、真实服务配对与执行；ready=false来源于实际未满足条件。停止在第一部分，不进入B1/B2、完整Desktop对话、H2/H4新任务链路或生产签名发行。共同wire候选0.3未因此冻结；没有提交、推送或服务器部署。

#### UI握手竞态增量后的最终交付（2026-10-09，替代a8b0）

Runtime唯一写入者完成controller/controller.test最小修复：describe未回时按实例缓存本连接代最高revision，确认实例后合并水位；reset清缓存、旧连接代忽略，最多8实例且超界明确失败。来源侧独立feature52/52（24fixture＋18controller＋6mount＋4timeout）、desktop typecheck及最终增量CR通过、无遗留P0/P1/P2；这些是Runtime侧工作流证据，不冒称本工作流重跑。主控只读确认controller SHA-256为8f5b7a8c963da6f85d9033bf337250a5660a1687d676564bf5bddf76b435d419，test为c52b583c55cc4e0e029fe0d649c5647627e632bd26a88d1f4cad1525c4033f31，不修改feature。

正式增量CR通过后，主控执行标准`npm run runtime:package:acceptance`，完整renderer/typecheck/边界/产物检查、Electron tsc、asar及NSIS生成与标准验包均exit0，RUNTIME_PACKAGE_PASS/RUNTIME_VERIFY_PASS。新Renderer主bundle为desktop-BUYpAdAM.js；原sass_binary_site配置及Browserslist旧提示不影响exit0，不扩大此批修复。独立测试再次标准verify exit0并复算下列值，inputs3713/packaged3704及3682资源/asar-dist21完整绑定；f52b Host/root/三包未变。

| 当前交付 | 精确记录 |
|---|---|
| 安装器（原路径已替换） | `clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe` |
| 字节数 / SHA-256 | 126241075 / 68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd |
| buildInput SHA-256 | d12b14e1758bdba88ffaff6d79d4e17a68181e3f3d947e21e3f6616bb3e76aff |
| manifest / 签名 | 同目录runtime-release-manifest.json / NotSigned（acceptance） |

主控启动新win-unpacked EXE复用本工作流此前创建的未配对空验收profile，实际包身份/包内资源/独立目录、未配对禁止启动、真实Host管理、关窗隐藏/重开、无pageerror及实际exit0共12断言再次通过；CIM无该包进程残留，已查看新截图。未执行NSIS安装、原生选框或真实业务配对，也未重做与本次Renderer增量无关的三实包安装测试，前次22断言保留其原覆盖范围。

旧a8b0包的暂停状态已关闭，当前人工验收使用68b855及新manifest，旧摘要仅作历史。手册12与Desktop索引行同步新交付。Runtime计划10.4及用户条件提交授权、限定暂存/提交/推送由Runtime主控维护；本工作流未执行Git提交/推送或部署，且不会替其宣称提交成功。B1/B2、H2/H4新链路与共同wire冻结均不扩大。
