# Runtime 执行环境、插件宿主与可视化客户端开发计划

## 开发进度

> **开发范围硬限制：当前只允许实施第一部分A1～A4。第二部分B1/B2暂不开发，仅保留设计、契约、schema和fixture。第一部分完成后必须交付并停止，不得自动进入第二部分；只有用户明确要求启动第二部分，才可更新状态并实施。**

> **“暂不开发”不是“待开发”：B1/B2不得进入当前任务队列，也不得因收到“按计划开发”“继续开发”而启动。**

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 设计/契约 | 共同契约、七项决议、schema与fixture | ✅ 完成（2026-10-09） | v2.0/候选0.3；Python7/59、Node15及独立测试/CR通过，wire未冻结 |
| 第一部分 A1 | 第一方CLI所需的共用核心与管理接口 | 🔧 进行中 | core/CLI及H3实际接线通过；共同wire未冻结，旧数据无结案证据限制保留 |
| 第一部分 A2 | BOSS/weixin/wecom独立插件包及安装管理 | 🔧 进行中 | 三包/安装管理及独立测试CR通过，隔离验收可用；正式发行输入仍缺 |
| 第一部分 A3 | Runtime UI与公共Electron壳接线 | ✅ 完成（2026-10-09） | UI52独立通过，握手水位竞态已修；H3真实窗口及包内EXE证据见10.3/10.4 |
| 第一部分 A4 | Windows发行包与第一方CLI闭环验收 | 🔧 进行中 | Windows验收安装器已交付；NSIS/原生选包/真实配对与业务待人工验收 |
| 第二部分 B1 | 第三方skill导入与环境/AI分析 | **暂不开发** | 未开放实施；仅保留手册优先、必要源码、入口/代码地图契约 |
| 第二部分 B2 | 第三方skill云端登记、执行及样本验收 | **暂不开发** | 未开放实施；仅保留schema/fixture及版本、资源、结果契约 |

> 日期：2026-10-08
>
> 设计：[runtime-plugin-host-architecture-design.md](../system/runtime-plugin-host-architecture-design.md)
>
> 前置：[外部 Skill M1](plan-external-skill-plugin-m1.md)、[M2](plan-external-skill-plugin-m2.md)；保留既有路径，M2 真机项仍未验收。
>
> 第一部分隔离Windows验收包已交付，A1/A2代码与实包及A3公共壳已验证；共同wire/正式发行与A4人工业务闭环继续待完成。2026-10-09用户另行给出条件提交授权并确认推送不自动部署，影响核验见10.4；部署仍须另获明确授权。

> 当前：共同契约v2.0、H1候选0.3。真实Host/adapter、固定Node22.23.3、三实包及H3公共壳接线已验证；隔离验收交付见第10节。共同wire与production仍未冻结，七项决议见设计第12节，第9节为A1历史交接。

> 历史2026-10-08：共同契约v1.3已由双方审阅并定稿。实现细化见[设计第11节](../system/runtime-plugin-host-architecture-design.md#11-v13-基线下的-runtime-实现设计)，首批[共享schema/fixture候选0.1](../../contracts/runtime-host/v1/README.md)已产出；真实双向兼容证据仍待交付，不能将候选当作已冻结wire。

## 1. 范围和交付边界

2026-10-09 A2～A4实现批：Runtime工作流唯一写入共用core及Runtime Host范围（沿A1登记，含`scripts/smoke-first-party.mjs`真实离库验收脚本），新增`clients/runtime-plugin-packaging/**`、三第一方CLI必要发行依赖/路径修正、`frontend/desktop/features/runtime/**`，以及Runtime设计/计划、Provider规范10.2和ideas Runtime行。内部开发子任务按以上目录分工；公共main/preload、固定Node、两产品身份/构建/全局依赖与Windows包由Desktop H3唯一写入。私有选择callback类型由core提供，不增加H1 wire；生产trust root不自行生成，明确隔离测试发行与正式发行。用户要求可人工验收后再交付，B1/B2仍暂不开发。

共同契约：[Runner / Desktop / Runtime v2.0](../system/runner-desktop-runtime-integration-contract.md)。共享入口的唯一写入者和H1～H4结果登记在本文第6节；共享语义相同，不能因为双方Phase编号不同而重复实现。

2026-10-09用户调整：开发拆成两部分，**当前只实施第一部分：Runtime UI＋我们自己的CLI插件安装管理**。BOSS、weixin、wecom按需安装，复用既有云端调用与Device执行链。第二部分为第三方skill，保留已确认的契约/schema/fixture，安装、环境、AI分析、动态登记及执行接线全部暂不开发，不作为第一部分交付依赖。

第一部分提供安装/启用/停用/升级/卸载及就绪诊断，成功安装默认启用。首期采用第一方离线插件包，不顺带建设在线市场或通用远程下载服务。管理请求仍用既定plugins.import，但实现只接纳支持的第一方包；第三方skill在执行任何解包安装/依赖构建/源码上传前明确返回FEATURE_UNSUPPORTED，无插件业务动作。UI只显示第一方安装入口，不显示可用的第三方skill入口或无效占位按钮。

完整Agent Desktop的会话、目录、文件能力按桌面计划推进，不并入Runtime首期。通用Shell/PTC、第三方MCP分发与官方在线分发也不作为本次两部分的交付要求。

Windows 优先。复用现有 Electron 工程，不新增本地 Agent 或本地模型调用；不建第三方云端市场，不开放任意 shell/远程下载 URL；不删除服务端自身使用的内置 skill。

UI 产品形态已于 2026-10-08 获用户确认：同一工程、独立 Runtime 客户端；完整 Desktop 可复用管理模块。

## 2. 成功标准

第一部分：

1. 用户只安装Runtime客户端，通过UI完成服务地址、设备名和一次性码配对；没有业务CLI也能启动、联网和诊断。
2. 用户通过UI分别安装BOSS/weixin/wecom第一方离线包，成功默认启用，运行条件独立决定ready；支持停用、升级及卸载，无额外插件批准。
3. 安装就绪后复用已有云端Agent到客户机的工具执行链；原工具名、schema、业务语义和外部MCP Host兼容保持。不新增Agent循环或要求先实现第三方skill动态登记。
4. 升级固定在途版本；失败保留旧可用版本，卸载不丢待确认结果；缺失/停用插件不误报可用能力。
5. 同一Runtime core用于CLI与独立UI；公共Electron壳/Node打包/监管由Desktop主导，管理接口不开放任意业务执行。
6. 同一Windows发行包验证空核心、三CLI单独及组合安装、既有受影响执行/桌面锁/取消/unknown/outbox，以及重启和升级；真实设备部署与业务操作按现有授权规则执行。

第二部分：未来启用时再验证第三方ZIP、AI分析/描述生成、服务器无样本脚本目录的登记执行、截图模型输入及真实样本。当前保留规则和样例，不开发或对其作完成承诺。

## 3. 实施阶段

### 第一部分：Runtime UI与第一方CLI插件

#### A1：共用核心与管理接口

- 对齐H1候选0.3，用真实producer/consumer验证describe/getState/配对/启停、插件清单和operation；保留code/error、共享实例/revision水位、同revision查询顺序及持久请求去重；原请求重发先查operation，再检查新选择引用。
- core交付真实JS/types、exports/build、CLI包依赖及受管bootstrap；现有noEmit接口骨架不是可运行核心，须用编译后资产验证空Host/CLI/管理/停止。平台测试adapter足以启动A1，不要求全部H2/H4前置。
- 从现有Runtime提取第一方CLI所需的配对、配置、心跳、claim/result、Provider管理及生命周期；CLI保持薄入口。移除BOSS必装假设，空Host正常运行。
- 固定Node executable通过平台适配注入；设备凭证留Host，同home单实例。公共main/preload/监管与产品构建由H3唯一主导接线，不复制第二套Electron入口。
- 保留旧v1/v2、锁名、DPAPI身份、sessionTasks与原结果/ACK。普通领取、sessionTasks/nameBridge共用准入/停止门；30秒收尾观察预算不代表自动kill。替换配对先排空在途/未知占用/旧ACK，失败保留原身份；恢复先核对实际事实，unknown不重放。
- 新任务binding与新资源状态只消费H2/H4已冻结输入，缺失时明确能力未就绪；不为首期新建通用授权/调度体系。已有安全执行门槛不能以缩小范围为由跳过。
- 只实现第一方路径所需接口，不实现skill执行sidecar/AI生成器/Python环境管理、第三方工具动态加载或通用PTC。

#### A2：第一方离线包与安装管理

- BOSS/weixin/wecom交付独立离线插件包（envelope.json＋payload.zip），Runtime主包去掉BOSS强制依赖、bundledDependencies及强制构建/默认检查；按Provider规范10.2检查正式来源、内层摘要、完整manifest、平台和Node/ABI。客户机不运行npm/pip/Git/hook；正式trust root/签名实包未交付前仅隔离测试，不登记可发行。
- 交付受支持operation实际所需的内部Python/OCR/模型/脚本，修正weixin的venv/experiments和wecom的仓库venv路径。移走源码仓库且无系统Python/npm/Git实测；此项是第一方包必需依赖，不开发第三方venv管理。
- 可信本机选择器→受控导入→环境/应用就绪检查→原子安装；致命包/发行依赖错误阻止提交，可恢复应用/登录缺项允许安装成功且ready=false。新装默认enabled=true，升级保留enabled值。
- 只接纳首期支持的第一方插件，不把任意MCP tools/list直接注入云端Agent；沿实际官方契约及已有proxy能力接线，保留云端计费/通知等特殊方法。
- 原工具名与外部Host兼容保持，CLI单独运行仍可用。一个包损坏/缺失/卸载不阻断其他插件或空Host。
- plugins.import兼作升级：同release/摘要不重复安装，同release不同内容拒绝；首期拒绝降级及同版本异release。关闭准入并排空旧执行后切active，不新增多版本并行管理；受管理停用/卸载记录优先于legacy入口。失败回滚不删除原结果，重启核对未完成安装，不重跑有执行风险的步骤。

#### A3：Runtime UI

- 同一Electron工程提供独立Runtime产品形态；消费Desktop设计第13节H3输入：Node22.23.3/Windows x64、直属child私有IPC、选择引用、DPAPI、Host单实例/drain、两产品identity/home。Runtime交管理feature/bootstrap，Desktop唯一接公共main/preload、package/lock/构建及全局入口；无已验证通道的外置实例先明确冲突。
- 连接页提供服务地址、设备名、一次性配对码及连接状态；插件页提供第一方包安装、版本、enabled/ready、启停/升级/卸载和失败原因。
- 运行状态/必要诊断复用现有设备执行信息，不新增完整聊天工作台或第三方skill配置页面。
- Runtime窗口关闭与显式退出/停止分别处理，保持独立执行节点语义；不改变完整Desktop的默认关闭行为。
- renderer没有任意文件/进程接口；UI不通过管理port调用插件业务。

#### A4：Windows发行与闭环验收

- 最终包包含受控Node与可实际运行Runtime core，不预装业务CLI；客户机无需另装npm/Git/Python，第一方包自带其实际内部依赖。
- 验证无需终端的配对、三CLI单独/组合安装、依赖或应用未就绪提示、停用/升级/卸载及既有云端任务执行。
- 检查旧CLI/新UI并存、账号切换、取消、断网、重启、升级在途执行、结果补传和桌面资源冲突；各协议不支持的能力据实拒绝。
- 复用已有产物/图片能力并回归受影响路径，不把新第三方截图上传协议、通用产物服务或jingpian样本作为首期验收前置。
- 同一源码、依赖和发行包对应独立测试与CR证据，验证正式trust root/实包/H3进程与DPAPI/旧ACK。既有云端调用仅按明确legacy模式验收；新Desktop路由/binding/跨调用许可仍依赖H2/H4，未做真机验收时不登记已发行。

### 第二部分：第三方skill（暂不开发）

**以下B1/B2是保留的后续设计，不属于当前开发清单。未经用户明确要求启动第二部分，不得编写其实现、安装器、AI分析器、云端登记/执行链或UI入口，也不得以通用化、预留接口或第一部分依赖为由提前实现。**

保留既有设计第4～6节及5.3.1的规则、候选0.2中skill形状和fixture，未来独立启动，不要求第一部分实现空壳接口/生成器。

#### B1：导入、环境与AI描述生成（暂不开发，仅保留设计）

- 第三方skill ZIP受限解包/唯一技能根、原包和执行sidecar、依赖环境、mutable/状态兼容与实际入口校验。
- SKILL.md优先，信息不足才向云端模型补传必要源码；保留原始手册，生成入口说明/参数/可选输出和最小code_map，不虚构能力。
- 安装即使用授权，成功默认启用，无额外插件批准；未知能力/未确认入口明确缺口。

#### B2：登记、执行与样本验收（暂不开发，仅保留设计）

- 第三方设备契约接纳/请求级SkillRegistry视图、版本固定、资源仲裁、执行适配、产物上传与图片真实进入模型。
- 保留原Device结果/ACK、unknown不重放、在途版本、目录/设备归属和实际进程停止核对。
- 拿到jingpian等真实包后再验证脚本、依赖、mutable、截图及所需软件；不以现有schema/fixture通过代表样本已安装执行。

官方在线分发与第三方云端市场仍是后续独立范围，不为这次两部分预先实现。

## 4. 开发与验证流程

执行本计划或收到“继续开发”时，仅推进第一部分A1～A4；完成后报告交付结果，第二部分保持暂不开发。启动第二部分必须有用户明确指令，不能自行将其改为进行中。

开发智能体选择下一项工作时必须先检查上述范围限制；A1～A4全部完成后，无当前获准的下一开发阶段。

本次范围调整是文档工作，由主控核对，不触发完整三角色流程。后续涉及启动、鉴权、租户、协议与执行，按高风险流程执行：开发与定向自测 → 独立测试智能体 + 独立 CodeReview 智能体 → 主控整合。每阶段冻结交接范围；文档记录真实证据，不累加不同代码状态的结果。

验证重点：

| 边界 | 意图 |
|---|---|
| 身份/设备/版本/撤销 | 安装授权本设备插件，不扩大账号的设备/workspace使用关系 |
| revision/升级 | 同一任务始终使用同一手册和执行代码 |
| 单实例/桌面锁 | UI、CLI、后台观察不会同时控制桌面 |
| unknown/取消/outbox | 异常不造成重复业务动作或丢失终态 |
| ZIP/环境/回滚 | 安装失败不会破坏旧可用版本 |
| 产物与模型输入 | 真正验证模型看到了图片，而非路径字符串 |
| 最终发行包 | 交付物与已验源码一致；旧包不得冒充新能力 |

相关代码验证复用项目既有 `scripts/dev_test.sh`、Runtime 的 typecheck/定向 Node tests、Desktop 的 build/test/smoke 与 frontend build。不在计划阶段运行不会验证本次文档的业务测试。

## 5. 文档、索引与发布约束

- 每阶段更新本计划顶部进度，再同步 `docs/ideas.md` 一行状态。
- 旧 M2 的现状和未验收项保留，不把新设计写成旧功能已完成。
- 变更系统表、Provider 包契约、UI 构建或客户安装流程时更新关联文档。
- 仅用户明确要求提交才提交；仅用户当场明确要求部署目标环境才部署。

## 6. Desktop / Runtime 共同边界登记

2026-10-09 A1实现批：本会话为唯一写入者。精确范围为`clients/shared/local-tool-host-core/{package.json,package-lock.json,tsconfig.json,README.md,src/**,tests/**}`、`clients/agent-tool-runtime/{package.json,package-lock.json,src/**,tests/**,scripts/run-tests.mjs}`与本Runtime设计/计划、ideas的Runtime索引行。测试入口追加Host生命周期suite串行分组，隔离旧CLI模拟对同用户实例核对的影响。稳定执行模块移入普通Node核心，Runtime旧路径保留兼容导出；新增受管bootstrap、候选0.3真实管理producer、单实例与分离drain。共享schema/fixture、H2、公共Electron入口/全局package/lock及第一方业务实现不修改。H1仍为未冻结候选0.3；真实管理端通过后提供H3消费交接，不代替Desktop接线或A4验收。

2026-10-09开发前澄清批：Runtime设计者为唯一写入者，开发准备会话“梳理 Runtime 插件计划”保持只读。范围为原Runtime设计/计划、Provider规范离线实施补充、共同契约/README候选登记、H1管理响应/fixture/消费参考及定向测试，Desktop只消费新格式并维护自身H3输入。候选0.3增加plugins.list实例/revision包装及可选真实version；不修改生产业务、全局构建或H2类型，B1/B2仍暂不开发。七项决议见设计第12节，最终交付/外部门与验证见本文第8节。

2026-10-09用户逐条复核后修订：本批唯一写入者为Runtime工作流，范围为共同契约、双方设计/计划的当前引用及交接、`contracts/runtime-host/v1/` schema/fixture/消费参考/定向测试、ideas的Runtime索引。落实安装即授权、手册优先AI分析、入口说明/代码地图和固定数字code/error回复；不修改公共Electron入口、H2/H3类型或生产业务代码。架构语义升级v2.0，未冻结wire候选升级0.2；旧候选必须显式适配，不把变更当成可兼容新增字段。

2026-10-09消费P2修复批：Runtime为唯一写入者，范围为测试专用`consumer-fake.mjs`、消费回归、共享README和Runtime设计/计划；Desktop计划第9.1节仅追加修复交接，保留原审阅。schema、共同契约版本、生产业务、H2/H3和公共壳不变。目标是describe/事件水位不倒退、新实例清理旧快照、旧实例在途响应不污染当前状态，完成后独立测试/CR。

本批接口产出（2026-10-08）：唯一写入者为Runtime工作流；文件为`contracts/runtime-host/v1/`内schema、fixture、校验脚本/消费fake，以及共同契约的交付登记、Runtime设计/计划、Desktop计划的接收提示和ideas的Runtime行。不修改公共main/preload、H2/H3类型、全局package/lockfile或业务调用。首批只交最小管理面和插件描述/清单/输出内容，不定义设备binding、审批、许可或长占用wire；需Desktop/服务端真实消费验证后再登记冻结。

| 检查点 | 主导 | 状态 | 本批写入者/记录 |
|---|---|---|---|
| H1 管理port与wire schema | Runtime | 🔧 进行中 | 候选0.3真实Host/直属child producer与schema验证通过；H3真实consumer/共同冻结待完成 |
| H2 固定设备binding/grant/审批 | Desktop | [见桌面计划](plan-desktop-agent-client.md#9-desktop--runtime-共同边界登记) | Runtime提供核验adapter；不独立再建一套绑定 |
| H3 公共壳与实例监管 | Desktop | [见桌面计划](plan-desktop-agent-client.md#9-desktop--runtime-共同边界登记) | Runtime提交core/管理模块；公共main/preload/构建由桌面接线 |
| H4 插件登记与产物 | Runtime | 🔧 进行中 | 首批描述/清单/输出候选已交付；H2接入、真实接纳及资源/产物证据待完成 |

2026-10-08：共同契约文档已建立，双方设计/计划与AGENTS均已关联；具体schema和实现未完成，不冒称另一会话已确认或验收。

2026-10-08：用户转交桌面执行环境定位，已按共同契约v1.1补充环境上下文、本地批量计算与审批/计费边界；代码阶段均仍待开发，不扩大首轮为完整PTC或通用Shell。

2026-10-08：Desktop工作流核对并同步共同契约v1.2，补充同机/异机Runtime、目标grant/网络/产物及长GUI独占。H2由Desktop主导服务端选择/绑定与资源语义，Runtime在H1/H4提供目标管理、能力/资源声明及实际仲裁；内部计划细化由本工作流完成，尚未声称接线或验收通过。

2026-10-08：Runtime工作流审阅v1.2并兼容补充v1.3，保留部署与分工，细化资源需求、旧进程停止核对、断网许可及调用/占用/流程时限。本批唯一写入者为Runtime工作流，文件为共同契约、Runtime设计/计划、Desktop设计/计划（仅共同引用与交接）；无wire/业务代码修改，未向桌面会话发送消息或代为验收。5份文档的35个本地链接、共同计划锚点、当前版本引用与git diff --check通过；仅文档变更，未运行业务测试，实现仍待H1～H4冻结。

2026-10-08：Desktop工作流已审阅并接受Runtime的v1.3补充，共同架构/行为契约定稿，无新增架构要求。此条由Desktop同步接受结果，Runtime按既定分工继续细化H1/H4及内部实现；wire冻结和真实兼容验收仍待完成，阶段开发状态不变。

2026-10-08：依据用户转交的定稿结论，Runtime工作流细化实现设计第11节与本计划；本批文件仅为Runtime设计/计划和ideas的Runtime索引行。共同契约及Desktop文件不再改写，所有代码阶段仍待开发，未向另一个会话发送消息。H1/H4仅完成文档草案，schema/fixture、生产/消费验证和冻结均未完成。5份关联文档的36个本地链接/锚点、v1.3基线、索引唯一性及待开发状态检查通过，git diff --check通过；未运行与纯文档无关的业务测试。

## 7. Runtime接口交付与依赖清单

下列清单用于下一批实施交接，不新增共同架构条款或第二份进度账本。唯一实施进度仍在第6节和顶部阶段表；接口草案的位置均在原设计第11节。

| 交付项 | Runtime产出 | 消费方与前置 | 当前状态 |
|---|---|---|---|
| H1管理面 | describe/state/operation/plugin/error、请求去重和observe规则；schema/fixture/fake | Desktop main/preload adapter验证 | 🔧 当前候选0.3，未共同冻结 |
| H3集成需求 | 固定Node启动、同home单实例、DPAPI兼容、受管/外置通道及drain | Desktop主导公共壳、发行和监管接线 | 📋 需求已细化，接线未实施 |
| H2输入 | Runtime提交核验与资源监管需求，直接消费binding/grant/permit/owner语义 | Desktop主导schema；冻结前仅本地管理/导入或测试adapter | 📋 待主导交付共同类型 |
| H4契约登记 | release/sidecar/inventory及身份/版本/格式检查，生产消费fixture | Device API服务端验证，接既有Agent装配/SkillRegistry | 🔧 首批描述/清单候选已产出，接纳未实现 |
| H4结果/资源 | 产物归属、输出完整性、真实停止核对及长占用兼容证据 | H2授权/恢复、Runner图片输入与Desktop展示 | 🔧 输出内容候选已产出，资源wire/实测未完成 |

接口冻结条件：版本化schema与错误/事件示例落盘 → Runtime producer验证 → Desktop/服务端真实consumer验证 → 记录兼容限制与旧协议adapter → 第6节登记冻结版本。仅文档审阅或typecheck不满足冻结条件；授权依赖缺失时不启用新远程执行。

### 候选0.1交付与验证（2026-10-08）

- 共享产物：[contracts/runtime-host/v1/README.md](../../contracts/runtime-host/v1/README.md)，7份schema、47个正反fixture、Python校验脚本、测试专用消费fake与Node测试。不改Device API业务链、Electron入口、H2/H3或现有Provider协议；无新增运行时依赖与生成框架。
- 主控初测：Python 5测试/45fixture及Node 5测试通过。独立测试与CR指出mutation响应种类不匹配、失败operation缺原因及旧连接回调隔离缺口；已最小修复，describe补齐既有revision语义，连接代次仅在消费adapter内部。
- 最终独立测试：`.\venv\Scripts\python.exe contracts/runtime-host/v1/scripts/check.py`，7schema/47fixture、5测试通过；`node --test contracts/runtime-host/v1/tests/consumer.test.mjs`，6测试通过。0失败/跳过；另做82项Python边界断言和4项Node连接断言通过。独立CR复核通过，无遗留阻断问题。
- 主控最终核对：5份交接文档的本地链接/锚点、v1.3基线和索引说明长度通过，git diff --check通过。桌面计划第9节和共同契约第8.1节均已指向共享入口；没有代替Desktop真实消费验收。
- 交付通知：按用户“让桌面客户端知道更新”的指示，已向同仓库会话“重新设计桌面版 Agent”发送共享目录、范围、校验结果与候选状态通知；发送成功不代表其已经消费验证或接受wire冻结。
- 未完成：管理producer/main-preload adapter、选择引用/幂等持久化、登记审批/摘要规范化、H2接线、原Device结果包装/ACK、进程/资源和真实图片闭环。候选0.1未共同冻结；Phase 1只完成此批格式与样例，不标记core提取完成，未提交或部署。

### Desktop状态消费P2修复（2026-10-09）

收到[Desktop计划第9.1节](plan-desktop-agent-client.md#91-候选01消费审阅2026-10-09)的实际复现。Runtime修复测试消费参考：水位取有效describe/通知/已接受snapshot最大值；低于水位的结果不完成刷新；同连接换实例清旧快照并重置水位，旧实例注册的在途回复失效。schema及47个静态fixture不变，新增3项Node回归，不增加wire字段、事件账本或Promise调度框架。

验证记录：先补回归，旧代码6通过/3失败；修复后Node 9测试通过、0失败/跳过。独立测试重跑Node 9项及Python 5项（7schema/47fixture）均通过，并追加29项边界断言通过；独立CR通过，无新增P0/P1/P2。README和实现设计同步了真实adapter仍须结束失效查询、补查/dirty收敛的职责；git diff --check通过。未启动Host/Electron、未做真实接线、未提交或部署；候选0.1及架构v1.3不变。Desktop计划仅追加交接，不改写其原审阅或代为宣布消费验收。

交付：关联文档链接/锚点检查通过，已向“重新设计桌面版 Agent”发送本批修复范围、验证结果及真实adapter遗留职责通知。消息发送成功，Desktop后续复核结果由其工作流登记。

### 用户复核修订候选0.2（2026-10-09）

用户已逐条确认C01～C12及全部schema说明。v2.0改变插件授权语义，移除额外审批/默认逐次弹窗；保留身份/配对/设备/workspace和已有任务策略。候选0.2落实固定数字code/error、skill入口说明/可选输出schema与最小code_map；无脚本的手册skill可空入口，不虚构能力。安装AI流程手册优先，仅缺信息时补传必要源码，不开发第二套模型loop。

迁移：候选0.1未冻结未上线，0.2在同目录替代；api_major/schema_version仍为首个待冻结wire版本1，架构版本与wire分开。旧回复/registration/success形状拒绝，消费方需显式迁移；不改变生产Device结果/ACK及legacy审批实现，本阶段只更新目标设计与格式，真实执行链仍待开发。

验证：新增格式回归在旧schema上2失败，修订后主控Python7项（7schema/56fixture）和Node10项通过。独立测试复跑Python7/56与Node10，并完成129项schema及37项消费边界断言；发现消费fake遗漏effect的P2。主控补返回effect及错误输出仍保留applied/partial的回归后Node11通过，独立复验Node11与36项补充断言通过，P2关闭。schema/fixture未变，最终复用同状态的Python证据，0失败/跳过。

独立CR指出桌面计划仍无条件要求逐次审批及契约默认enabled依赖ready的措辞；已改为仅既有企业策略要求时审批，安装成功默认enabled=true，运行条件独立决定ready。最终独立CR通过，无遗留P0/P1/P2。77个本地链接/锚点及git diff --check通过。未实现生产Host/桌面/云端接线或AI分析器，未安装真实第三方包，未提交或部署。

交接：已按用户此前明确指示，向同仓库“重新设计桌面版 Agent”会话发送v2.0/候选0.2共享入口、迁移要点及最终验证结果，要求消费复核结果记入其计划第9.2节。发送成功；Desktop本批重新接受或真实联调尚未登记，不据通知完成声明共同wire冻结。

### 两部分开发范围调整（2026-10-09）

用户要求当前仅开发Runtime UI与我们自己的CLI插件安装，第三方skill只留契约。计划由原Phase1～7重排为第一部分A1～A4与第二部分B1～B2，原Phase编号仅属于历史记录；未把待开发工作标为完成。

唯一文档写入者为Runtime：本计划、Runtime设计、共同契约的实施范围说明、共享README范围说明、Desktop计划的范围交接及ideas Runtime行。共同架构v2.0、候选0.2、7schema/56fixture不变；仅缩小当前实施范围，不改skill契约/旧执行路径，不删除已交付格式。69个本地链接/锚点、两部分进度结构、索引唯一性及git diff --check通过；纯文档调整未重复运行不受影响代码测试，未修改业务代码、提交或部署。

已向“重新设计桌面版 Agent”会话发送两部分范围调整、计划入口和第9.3节交接，提醒公共壳接线不以第三方skill为前置；发送成功，不代替其实际读取/实施验收。

## 8. 开发前七项决议与候选0.3交付（2026-10-09）

详细依据：[Runtime设计第12节](../system/runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)、[Provider离线格式10.2](../system/first-party-cli-mcp-provider-standard.md#102-runtime首期离线包实施格式)、[候选0.3迁移](../../contracts/runtime-host/v1/README.md#候选02迁移到03)、[Desktop H3输入](../system/desktop-agent-client-design.md#13-h3-首期设计输入runtime-ui与第一方cli)。无新的功能文档，沿原索引与计划登记。

| 澄清项 | 首期决议 | 开发验收门 |
|---|---|---|
| 1 离线包/信任 | 外层envelope＋内层payload，签名含内层摘要；依赖构建在发行端 | 正式trust root/签名实包；OCR/Python和脚本脱离仓库可运行 |
| 2 安装/就绪 | 致命包检查阻止提交；业务应用缺项允许enabled=true/ready=false | 错包不损坏旧版，未登录等可恢复后就绪 |
| 3 升级/停用/卸载 | import升级、相同release去重、拒绝降级，排空旧执行再切换 | sessionTasks/nameBridge同准入门，旧事实/结果/恢复引用不丢 |
| 4 legacy | 受管理记录优先，明确旧入口保留，新空Host不强制BOSS | 停用/卸载不能fallback，缺单插件不退出Host；能力真实 |
| 5 去重/列表 | 先查operation再验新引用；敏感意图HMAC；列表含实例/revision/真实可选version | 过期引用重发、同键冲突、旧列表/实例/连接和刷新竞态 |
| 6 stop/重配对 | 关新准入后收尾，30秒不是kill，替换身份先清旧执行与ACK | 真实进程停止核对/旧token原回执、失败保留旧身份 |
| 7 分期/core/H3 | A1真实可构建core及CLI依赖，A3/A4消费H3公共壳 | 编译资产运行及H3实包；新H2路由缺失不冒充完成 |

外部输入状态：Desktop已提供第13节H3设计（Node22.23.3/Windows x64、继承IPC、选择引用/DPAPI/同home单实例/drain及文件责任），但接线与实物验证未实现。生产trust root/签名流程、三CLI自包含实包和OCR可移植性尚未交付；H2新路由/资源协议未冻结。A1/A2内部开发与隔离测试可推进，正式发行及A3/A4完成仍须相应证据；不生成临时生产密钥，不扩展B1/B2。

候选0.3仅修改management-response的plugins.list.result与可选version，配套正反fixture/消费参考/定向测试同步；旧数组拒绝。api_major/schema_version=1与架构v2.0不变，H2/H4内容schema及生产Device协议不变，不新增事件/类型生成/授权框架。

验证记录：先补列表水位/实例/同revision刷新回归，旧fake为11通过/3失败；修复后主控与独立测试Python7项（7schema/59fixture）、Node14项通过。独立测试另跑29项schema、27项消费断言；测试与CR均复现同一P2：查询发送序号错误地屏蔽先发查询返回的更高revision。主控最小修正为实例/revision优先、仅同revision比较已接受列表的本地查询序号，追加第15项回归，Node15通过。

最终独立复验：Node15项及72项额外消费断言通过，0失败/跳过；schema/fixture未变，复用本轮独立Python7/59及29项形状检查证据。独立CR定向复现确认P2关闭，最终无遗留P0/P1/P2。命令为`.\venv\Scripts\python.exe -X utf8 contracts/runtime-host/v1/scripts/check.py`与`node --test contracts/runtime-host/v1/tests/consumer.test.mjs`，额外探测均通过stdin内存执行，无测试临时文件。

主控文档核对：7份关联文档97个本地链接/锚点、索引单行长度/唯一性、B1/B2硬限制及临时脚本不存在检查通过，git diff --check通过。仅设计/共享格式/测试参考修改，未实现生产Host/安装器/配对持久化/退出门或业务接线，未构建真实第一方包、未提交部署，不把样例验证记为wire冻结或A1～A4开发完成。

交付通知：已向“梳理 Runtime 插件计划”发送本批文件、七项决议、OCR/core要求、验证与外部门的自包含最终说明；已向“重新设计桌面版 Agent”发送候选0.3迁移、P2最终修复及证据，并要求其在自己设计/计划登记重新消费。两条消息发送成功，Desktop本批接受/真实联调结果由其工作流登记，不代为宣称验收。

## 9. A1本批实现与H3交接（2026-10-09）

本批只交A1内部实现与消费资产，整体仍部分完成。共用核心新增真实build、JS/types/exports及本地依赖；16个稳定模块移入core/src/legacy，Runtime旧路径兼容导出，避免复制两条执行链。CLI薄入口与managed-entry共用RuntimeHost/LegacyExecution。主包移除BOSS依赖/bundled/强制构建，显式legacy Provider与原skill-runner配置仍保留，不作为签名安装记录；空或缺入口不误报能力、不claim。

真实H1管理producer支持describe/getState/pair/start/stop/plugins.list/operations.get与事件；持久request_key去重及受DPAPI保护的稳定HMAC secret，不保存pair参数/配对码/device token明文。安装管理未实现，plugins变更明确code3。home/user OS lease覆盖新CLI/受管进程，标准旧CLI额外做CIM实际SID核对，受管入口仅直属Node私有IPC，不开TCP管理。父进程断开进入同一drain；继续accepted调用progress/原结果，等待session接受动作、最终日志/ACK与Provider退出，30秒不强杀。claim撤销和意外执行循环结束分别据实报告revoked/reconciling。

配对/解绑采用DPAPI密文pending事务，在取得lease后恢复，并验证密文后才替换身份文件。历史journal/session无可信结案证据时拒绝身份替换；设计者已确认降级边界，详情见设计12.6。没有证据时不能靠目录删除放行。A4身份切换验收必须补足可信结案/实际停止核对，不能记为已完成。

| 验证 | 结果与适用范围 |
|---|---|
| 构建 | core build、Runtime build/typecheck通过；真实exports与编译入口可运行 |
| Runtime全套 | 最终默认npm test 246/246（Host串行21＋其他225），0失败/跳过；已包含SID修正与collector分组 |
| core | 最终10/10，0失败/跳过；去重重启、lease、停止、持久接受失败与无敏感参数落盘 |
| 独立测试 | 最终12/12，0失败/跳过；真实Windows IPC/DPAPI/父断连退出、停止准入/ACK、历史结案阻断、旧CLI模拟；删除/伪造USERNAME仍无法绕过SID检查 |
| 最终受影响回归 | 主控26/26，0失败/跳过；pair/pairingStore/hostIndependent，末次SID代码 |
| 实际H1消息 | 独立测试采集真实child的20 responses/7 events，全部通过候选0.3 Draft202012Validator；临时trace删除 |
| 独立CR | 通过，无遗留P1/P2；停止/身份/状态/打包问题已定向复验，Host测试串行隔离后默认npm test全通过 |
| 打包 | 最终tgz包含core JS/types、bootstrap及SDK/zod依赖，无BOSS/Runtime tests；源码仓库外解包实际IPC/DPAPI/断连退出通过，无npm安装或业务GUI |

测试环境为本机Windows、开发Node24.13.0及FakeCloud/FakeProvider；没有使用正式Node22.23.3、生产签名trust root/实包或H3最终Windows安装包，不能将这些结果记为A4发行验收。正式输入仍按第8节外部门登记。B1/B2保持暂不开发，未提交或部署。

H3资产交接：在clients/agent-tool-runtime目录运行npm pack，dist/src/**与bundled node_modules完整随包。Main需按core README复制到resources/runtime/host/、保留ESM package元数据/相对sessionTasks结构和模块依赖；固定Node启动managed-entry.js，可信home经原AIDWORK_RUNTIME_HOME注入，完整Desktop传--supervisor=desktop，独立Runtime缺省runtime_app。实际管理请求/响应/事件按候选0.3发送；stop完成后按H3退出门断开IPC并等实际child退出。本批包为隔离开发验证资产，不是Windows产品安装器，H3仍为唯一公共壳写入者。

最终开发验证tgz SHA-256：b5db68c2677139c75455e8feea4844d4910385f3bfc06348b1d34d5cafc9bccc。构建脚本/文档后续改变时须重建并核对，不据这个临时tgz摘要冻结产品wire或版本。

交接通知（2026-10-09）：Runtime设计会话“梳理 Runtime 第三方 Skill 安装机制”确认已通过其获授权路径向“重新设计桌面版 Agent”（01a11a92-3600-7953-891f-07da5ed038f9）发送A1资产、启动/停止约定、验证证据及限制，发送成功。设计者只读核对tgz为3060079字节，摘要与上文一致，未修改实现/文档或重跑同状态测试。Desktop实际消费、H3接线及验收由其工作流登记，当前尚待反馈；本通知不构成共同wire冻结或A4发行验收。Runtime资产后续修复仍由本批唯一写入者处理。

主控最终核对：本批4份详情/索引/资产文档的142个本地文件链接有效，Runtime索引唯一且仍为部分完成；定向git diff --check通过。最终tgz的全部JS/JSON/types与已做仓库外实际smoke的包逐项SHA-256一致，仅文档/打包元数据不同。工作区其他会话改动保留，未暂存/提交/推送/部署。A2签名实包与依赖可移植性、A3 H3接线、A4真实Windows产品及身份切换结案验收继续未完成。

## 10. A2～A4实际实现与验收交接（2026-10-09）

本节为当前批；第9节为A1历史交接，旧tgz不可用于新安装管理。已实现固定信任源的第一方离线验签/受限解包、完整发行文件清单、安装/升级/启停/卸载与legacy优先级、Host接单前冻结副本、共享revision，以及Runtime页面。新v2原ACK与session可信终态/fullACK结案证据允许已结案历史保留；旧证据缺失与unknown仍阻断版本/身份切换，不删除历史放行。

### 10.1 第一方最终隔离资产

资产目录为`clients/runtime-plugin-packaging/release/final-acceptance`，只消费该目录的三包及同目录`ISOLATED-TEST-TRUST-ROOT.json`；此前调试目录的signer不同，不能混用。私钥不保存、不打包。H3使用唯一wrapped格式，acceptance显式`test_only=true`，production依据固定配置和显式标识拒绝测试根。

| 包 | 字节数 | SHA256 |
|---|---:|---|
| boss-0.3.0-dev.aidplugin.zip | 4442404 | `6c1f4578498ff23ab8138b5fb1c51994f4dbd209a7bf1754e43d8726bce73d11` |
| weixin-0.1.0-dev.aidplugin.zip | 117056592 | `c3f17d0460aa596b273c76b24299d74b7cda0a96af3c07c86564f9af7c1285f3` |
| wecom-0.1.0-dev.aidplugin.zip | 117215512 | `35dfd1b23ae2dab194763dbb4dae5ea80a4f9e02c136d32887a5524b68f9a1dc` |

包内工具完整数21/8/10；Host受信子集不扩大。微信历史/Win32依赖迁入drivers，微信/企微含固定内部Python/OCR/模型/CRT及许可证，调用使用`-I -B`避免环境污染和签名目录缓存写入。`verify-offline.py`已在仓库外、仅Windows系统PATH完成三包签名/全文件/version/doctor，微信与企微实际合成图片OCR成功；执行前后文件集合/字节不变。此证据未发送消息或点击业务GUI。

### 10.2 已有证据与剩余验收门

Runtime默认全套251/251（串行21＋其他230），0失败/跳过；修复了动态能力检查错误地把不存在legacy入口当可领取的问题。core新增ZIP/安装器/Host意图测试，当前30/30通过。UI同实例断连保持原key/operation，不同实例拒绝自动重放，最新48项独立回归及Desktop类型检查通过（新增45秒合法回复/120秒超时/取消与原键重试）；实际Electron窗口/原生选择与最终产品由H3检验。

新增独立session测试已验证真实claim→terminal/fullACK→drain→DPAPI证明及gate，篡改events立即失效；retired任务只有部分ACK仍阻断，重启recovery收到原terminal/fullACK并drain后才结案；active/旧epoch/unknown及伪造.acked均不结案。实际managed-entry早describe收到code11且退出，保留坏pending原字节。独立新增与既有proof共9项通过，受影响session-engine/retention34项独立回归通过，无失败/跳过。固定Node22主控复验productTrust/proof/session共10项通过。

主控固定Node22.23.3/ABI127真实Host三包安装、接单后原选中文件改变、原key/过期ref重发、停用/启用、两次重启及卸载墓碑已通过。初版离库tgz同生命周期也通过，但并行三包完整inflate在压力下发生44.7～90.9秒刷新、只读探测超时；旧独立性能脚本第二次刷新失败，不能称其全通过。现改顺序检查，每次仅持有一份完整包，最终同步发布列表/revision；UI与H3管理查询有界等待120秒。

顺序版离库主控完整三包Host生命周期通过；列表通常5.9～7.2秒，另一次压力刷新86.6秒但未误改就绪，仍在新预算内。独立测试只从候选tgz导入core/Runtime：32断言通过，覆盖真实三包及probe、坏包回滚、持久operation重启去重/冲突与无可信session证明阻断；真实刷新7.52秒，core Host＋实际Store/probe初始化5.82秒，安装2.27/6.02/7.56秒。该独立启动用实际DPAPI adapter，但未跑全部LegacyExecution.initialize，主控真实Host另覆盖此路径。峰值约975MiB，未做低内存/冷磁盘认证。原选中文件变更的冻结/新准备拒绝及旧journal阻断另10项独立断言通过，相关实现未变。

最终来源门补严格SPKI PUBLIC KEY PEM：core包验签与固定资源消费共用校验，createPublicKey能派生私钥不能作为接纳依据。独立固定Node22复现PKCS8及伪PUBLIC私有DER均拒绝，固定资源报code9，有效最终根与真实BOSS包仍验签通过。当前唯一tgz SHA256为`f52b056be3b729ebfef1cc9786079b34d16eb9092312d19076269bed527fc13f`（3184959字节）；旧b5/c48/20a仅留历史证据不可最终消费。独立CR最终核对76个Runtime/core编译文件与当前build一致、无缺失/差异/本任务测试文件，无遗留P0/P1/P2；结论不代替H3实物或业务验收。末次f52b实际LegacyExecution/DPAPI完整Host三包smoke退出0，安装2.04/4.79/4.57秒，所有清单刷新5.43～5.57秒，包含冻结副本/原key重试/停用启用/两次重启/卸载墓碑。已向设计会话发送最终冻结交接，由其获授权通道转H3；Windows产品正在收口。

真实原MCP链独立补验：固定Node22实际三份已安装payload，SDK connect/initialize＋tools/list，BOSS21/微信8/企微10项工具定义逐字段与签名manifest一致；握手189/175/903毫秒。SDK关stdin后子进程退出0且无signal，7/6/6毫秒、无kill调用或残留PID。无生产token、不调用业务工具/GUI/外网；企微注册握手不要求token，不据此声称服务代理已配置。首次独立脚本将微信/企微数量反写，按实际签名manifest修正预期后退出0，不是生产回归修复。

三CLI定向验证：BOSS46、微信相关27、企微相关19、OCR取消5、打包3通过。全套CLI未全部通过：HEAD隔离基线可复现微信2项（name-search缺mock overlay helper与OCR alignment断言）、企微2项（SESSIONNAME环境假设）；微信全collector另有send参数测试偶发竞争，当前与HEAD定向12项send均通过。未擅自修改旁业务，不宣称全业务回归全绿。

人工验收准备完成的门：最终Windows acceptance安装器及摘要、实际Electron页面/受信选包后半链路/IPC/DPAPI/重启/退出证据、同一候选独立测试与CR齐备。用户届时只需提供现有服务地址与一次性配对码、登录对应业务App，检查空核心联网、三包安装与提示、停用/卸载/重启以及已有云端低风险任务；无开发依赖安装要求。真实部署必须另获明确授权。正式签名根、发行签名、Python安全发行评审/CRT许可资格、干净Windows与真实业务闭环仍单列，隔离验收版不冒称正式生产发行。新H2/H4与B1/B2不因本批测试通过而开放。

### 10.3 Windows人工验收交付

H3最终交付证据见[Desktop计划9.7](plan-desktop-agent-client.md#97-第一部分h3实施登记2026-10-09)：实际Electron三包安装/原键operation/关窗重开/重启恢复/安全退出22断言通过，无pageerror或残留Node。原生对话框返回值在自动测试中stub，Main文件读取/冻结/IPC/验签是真实执行；OS选包交互留人工。随后实际启动最终`win-unpacked` EXE（isPackaged=true），包内资源/产品名/独立目录、真实Host与未配对门、关窗重开/实际退出12断言通过，无残留；未执行NSIS安装或真实服务配对/业务操作。主控已查看两份实际窗口截图。

签名探测首轮因继承PowerShell7模块路径导致5.1子进程失败；H3局部隔离模块环境并显式加载系统Security模块，保持验收NotSigned/生产Valid门，独立包装10项测试与CR通过。标准重打包及验包exit0；Root另亲自运行`node scripts/verify-runtime-package.mjs build/runtime-release/acceptance`，RUNTIME_VERIFY_PASS，核对3682资源、asar/构建输入、固定Node22.23.3/ABI127和最终f52b Host一致。

| 交付项 | 记录 |
|---|---|
| 安装器 | [AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe](../../clients/agent-desktop/build/runtime-release/acceptance/AID-Work-Runtime-0.0.2-win-x64-acceptance-unsigned.exe) |
| 字节数 / SHA256 | 126241075 / `68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd`（10.4握手修复重建，替代a8b0旧包） |
| 发行记录 | [runtime-release-manifest.json](../../clients/agent-desktop/build/runtime-release/acceptance/runtime-release-manifest.json) |
| 产品 / 签名 | `cn.aidingyi.agent.runtime.acceptance` / `NotSigned`，仅内部隔离验收 |
| 三插件 | 本文10.1同一`final-acceptance`目录，安装器不预装业务插件 |
| 人工步骤 | [构建手册12节](../system/desktop-agent-client-build-manual.md#12-独立runtime首期验收包) |

用户人工验收顺序：安装打开 → 现有服务地址/设备名/一次性码配对 → 空核心启动并观察连接 → 原生选包逐一或组合安装三插件 → 登录对应业务软件后刷新基础就绪 → 停用/启用/重启保留/卸载 → 通过已有云端链执行授权只读测试任务 → 关窗托盘恢复和显式退出。BOSS调试Chrome仅引用较新已真机核验的[M07指南中Chrome启动说明](recruiting/m07-acceptance-test-guide.md)，Runtime安装以本节安装器为准。其Chrome说明与CLI旧AGENTS默认profile启动指引冲突，本批采用较新指南，不重写旁业务规范。

当前已具备人工验收条件；A3交付完成，A1/A2保留共同wire和正式发行输入门，A4保留人工真实业务/干净机器验收。共同wire候选0.3未因隔离产物冻结自动冻结，不开启H2/H4新链路或B1/B2。工作区其他会话改动保留，没有暂存、提交、推送或部署。

### 10.4 条件提交影响核验（2026-10-09）

用户授权本批在不影响已运行Agent服务、Web前端、微信KF渠道时先提交，人工业务验收随后进行；另明确确认master推送不会自动部署，部署需手动执行。提交范围仅本机Runtime/core、第一方CLI及插件构建、Desktop H3/独立renderer、共享格式与关联测试/文档。没有修改或暂存服务端src、Web业务源码/构建配置、微信KF实现或部署配置；工作区其他会话的KF文档、研究及索引整理保留不提交。BOSS既有0.3.0版本沿用为已验插件的源输入，不称本批原创升级。

线上影响结论：生产Python启动链不加载本批客户端入口，微信KF路径不变；Web仍使用独立index/tsconfig.web与web依赖边界。主控实际npm run build退出0，244源文件边界及523打包模块验证通过，无Desktop/native依赖。现有运行中的线上服务和已安装客户机不会因提交自行切换版本；后续主动安装/更新本机Runtime才消费本批功能。本地没有活动Git hook或仓库CI，Codeup只读页面需要登录，仓库外触发配置不能从本地证明；关于推送不自动部署的结论采用用户直接确认，不以本地无CI代替外部事实。

提交前追加独立验证发现Python一致性测试仍读迁移后的Runtime重新导出入口，已改读core唯一清单来源，并补既有README漏记的weixin_name_resolve。统一入口两文件独立结果28通过、1失败、0跳过；剩余BOSS catalog21/Host18差异在隔离HEAD原清单重复相同失败，三Provider受信集合迁移前后完全一致，不为通过测试扩大权限。旧clients/pack.sh仅新增前置fail-closed门：独立Host布局或损坏JSON在版本/清理前明确拒绝并指向新打包路径；真实仓库193文件摘要不变，两种Temp异常fixture亦未修改。相关增量独立CR通过。

UI独立追加发现describe未返回时丢弃同实例通知，迟到旧快照错误清dirty。sole-writer先复现红灯，修复为本代有界实例水位缓存，describe仅合并匹配实例，断连清空并忽略旧代，超过8实例明确失败；新增4项意图回归。最终独立feature52/52、Desktop类型检查通过，增量独立CR无遗留P0/P1/P2。Host/core/三插件/root输入未变，原f52b Runtime与生命周期证据继续有效；Desktop renderer/安装器须以本次标准重建后的新摘要为准，不复用旧a8b0安装器作最终源码验收。

Git提交准备：已fetch并保留所有工作区改动，以fast-forward同步到bb31f1e6；远端3个新增提交只涉及既有部署配置，本批不再次纳入。当前目标master→origin/master，索引仅暂存本任务文件及ideas的Runtime/Desktop两行，未执行部署脚本。提交仍保留A1/A2正式输入与共同wire门、A4人工业务验收门及B1/B2暂不开发限制。

最终重建交付：H3标准build/package/verify、独立验包及实际win-unpacked启动/退出12断言通过，无残留进程；主控亲自标准verify退出0，核对3682资源、Node22.23.3/ABI127及编译后的握手修复。安装器126241075字节，SHA-256为68b855010c8f39798a8822df83191e3044bdbb7860d46123345fe1dfbee9d3bd；buildInputSha256为d12b14e1758bdba88ffaff6d79d4e17a68181e3f3d947e21e3f6616bb3e76aff，原路径替换旧a8b0版本。Host f52b、公钥根及三包字节不变；NSIS安装、原生对话框、真实配对/业务仍留人工，不把提交记为生产发行或业务验收。
