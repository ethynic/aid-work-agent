# Runtime 执行环境、插件宿主与可视化客户端开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 现状审计、架构设计与登记 | ✅ 完成（2026-10-08） | 核对源码、旧发布物与既有规范；设计和计划已落盘；未开发/部署 |
| Phase 1 | 契约冻结与共用 Runtime core | 📋 待开发 | 零插件启动、Node 路径注入、CLI/UI 共用配对和生命周期 |
| Phase 2 | 本地插件导入、版本与环境 | 📋 待开发 | skill ZIP/官方离线包、侧清单、依赖检查、原子安装 |
| Phase 3 | 设备契约登记、授权与云端执行 | 📋 待开发 | 云端无需安装设备脚本；会话内可见性、版本固定、受控执行 |
| Phase 4 | Runtime UI 与客户端发行 | 📋 待开发 | 连接、插件、任务、诊断；同工程独立 Runtime 产品形态 |
| Phase 5 | 三 CLI 按需交付与兼容迁移 | 📋 待开发 | 去 BOSS 捆绑、独立插件、旧设备协议兼容 |
| Phase 6 | 产物回传、jingpian 适配与 Windows 验收 | 📋 待开发 | 图片进入模型上下文、取消/unknown/重启、真实安装包闭环 |
| Phase 7 | 官方在线分发与受控更新 | 📋 待开发 | 后续阶段；受控 catalog、签名发布、撤回、灰度与回滚 |

> 日期：2026-10-08
>
> 设计：[runtime-plugin-host-architecture-design.md](../system/runtime-plugin-host-architecture-design.md)
>
> 前置：[外部 Skill M1](plan-external-skill-plugin-m1.md)、[M2](plan-external-skill-plugin-m2.md)；保留既有路径，M2 真机项仍未验收。
>
> 本计划是新目标的分期建议，所有代码阶段均未开始；不构成提交或部署授权。

## 1. 范围和交付边界

共同契约：[Runner / Desktop / Runtime v1.1](../system/runner-desktop-runtime-integration-contract.md)。共享入口的唯一写入者和H1～H4结果登记在本文第6节；共享语义相同，不能因为双方Phase编号不同而重复实现。

首轮目标为 Phase 1～6：独立可安装的 Runtime UI、零业务插件核心、可选官方 CLI、本机导入第三方 skill、仅契约的云端登记、文本及图片闭环。Phase 7 不是首轮阻塞项。

Windows 优先。复用现有 Electron 工程，不新增本地 Agent 或本地模型调用；不建第三方云端市场，不开放任意 shell/远程下载 URL；不删除服务端自身使用的内置 skill。

UI 产品形态已于 2026-10-08 获用户确认：同一工程、独立 Runtime 客户端；完整 Desktop 可复用管理模块。

## 2. 成功标准

1. 客户机只安装 Runtime UI 即可配对，BOSS/weixin/wecom 均可不装。
2. 云端无目标 skill 代码目录时，客户机导入 ZIP 后仍能登记、授权、读取手册和执行脚本。
3. 三 CLI 的既有业务调用和 MCP 外部 Host 兼容保持；改为可选独立安装。
4. screenshot artifact 在对应云端会话中进入模型图片输入，并支持基于截图继续执行下一步。
5. 授权、设备、版本和在途调用被固定；升级、撤销、断网不导致错误设备执行或写动作重放。
6. 最终包 Windows 真机验收通过；源码测试或 typecheck 不替代安装包实测。

## 3. 实施阶段

### Phase 1：冻结跨端契约，提取共用核心

- 对齐现有 first-party Provider 规范、AgentRunner 和 Device API；冻结 plugin/release/contract 身份、登记 schema、invocation 版本及授权模型。
- 新私有契约、设备绑定、grant 的 DDL 在此阶段独立评审，全部 tenant scoped；同步系统表文档及迁移记录。
- 从 `clients/agent-tool-runtime` 提取配对、配置、心跳、claim、执行和生命周期到 `clients/shared/local-tool-host-core`；CLI 变为薄入口。
- 保留 v1/v2 区别，不把现有 skill-runner 声称为 v2；冻结新脚本路径对 write-authorize/journal/outbox 的适配。
- 进程/凭证/Node executable 作为平台适配依赖；清除新核心对 BOSS 必装及 Electron process.execPath 的隐含依赖。
- 单实例覆盖旧 CLI 与新 UI；readiness 依据实际启动/依赖检查，不能只看入口文件存在。
- 与H2工作流统一任务执行上下文，文件/进程/skill adapter使用同一设备与grant语义；区分package/workspace/state/output，不把相同cwd或venv当作安全隔离。

验收：零 Provider 能配对和心跳；既有受影响 claim/result/锁/取消回归通过；无跨层反向依赖和第二套任务状态机。

### Phase 2：本地安装管理

- 实现 staging ZIP 导入、技能根选择、受限解包、侧清单生成、官方包验签与兼容性检查。
- 提供安装/启用/停用/卸载/诊断的无 UI API；安装与依赖执行必须接收可信的本机授权结果。
- 原始 skill 不改写；支持旧 metadata 作为提示，执行入口缺失时显示需配置，未知能力显示不兼容。
- 隔离环境、状态与输出；对旧 mutable 路径提供可验证的工作副本适配，保留原始包。
- 原子版本切换、在途版本保留、失败回滚；卸载不删除未确认业务结果。

验收：路径穿越/链接/超限 ZIP 被拒；依赖失败不启用；升级不影响旧在途调用；普通用户不需源码或开发工具。

### Phase 3：云端登记与执行

- Device API 增版本化登记接口；完整契约与简短心跳清单分开传输；身份来自设备 token。
- 官方 schema 与批准 catalog 比对；导入 skill 进入租户/用户/设备范围审批，不复用平台级 JSON 全局审批作为新私有授权。
- 新增只读设备契约加载源及请求级 SkillRegistry 视图；缓存键含身份、grant revision 与设备 inventory revision。
- 保留 `use_skill`、`skill_execute` 入口；新路径用入口标识和 args，下发字段由 registry 生成；legacy command 仅作为解析兼容。
- 入队和设备真正执行前复查授权、取消和版本；固定 release/digest，不自动换设备或新版本。
- 配套运行协议能力、写动作链和 unknown 恢复；旧路径按旧协议工作，不降低已有门禁。
- 受信脚本可在一次invocation内循环、过滤、聚合；与桌面batch read/search接入同一环境。部分失败、截断和写后中断必须有明确结果，不增加本地LLM或任意execute_code。

验收：服务器无目标脚本目录的端到端模拟通过；两个租户同名 skill 不相互可见；已加载旧手册/撤销/缓存过期均拒绝误执行；写动作不可因客户端自报 effect=none 被自动重试。

### Phase 4：UI 和自包含 Runtime 产品

- 在 `clients/agent-desktop` 和 `frontend/desktop` 增 Runtime 产品入口/构建配置；复用现有安全、token 和组件体系。
- Desktop main 监管固定 Node Runtime child；版本化窄 IPC 提供状态、配对、插件管理。
- 配对表单包含服务地址、设备名、一次性码；沿用五分钟票据与独立 device token。
- 连接、插件、任务、诊断四项；失败、待审批、缺依赖和停用显示明确状态。
- Runtime 形态关窗口收至托盘；明确退出/停止语义；不改变完整 Desktop 的默认关闭行为。
- 用户交互会话自启、桌面可交互提示和同 home 单实例；签名安装包、更新及卸载边界沿原规范。

验收：无需终端完成配对和 ZIP 导入；renderer 无任意进程/文件 IPC；没有 npm/Python 的客户机可启动空核心，skill 环境缺失通过 UI 可见。

### Phase 5：官方 CLI 可选安装

- 更新 `clients/pack.sh` 与 Runtime package，去除 BOSS 的 dependency/bundledDependencies/prepack 强制构建。
- BOSS、weixin、wecom 发布为独立离线插件包；代码签名/包签名按既有规范和 release 工程执行。
- 保留旧 Node 配置入口与 legacy BOSS-only 适配；新设备缺失插件不上报对应能力。
- 逐项核对云端 proxy 中真正本地操作与云端计费/通知等特殊方法，避免机械全部搬到 MCP。
- 官方 MCP 工具发现只比对受信契约；不直接将客户端任意 tools/list 注入 Agent。

验收：三 CLI 单独安装与组合安装均正常；一个卸载/损坏不影响其他插件；新的 Runtime 包不存在业务 CLI 捆绑；外部 MCP Host 能继续调用独立 CLI。

### Phase 6：截图产物及真实样本闭环

- 基于现有产物存储，增加设备 token 可用的受控输入/输出授权适配；优先输出截图，不扩大到完整本地文件系统工具。
- 输出路径圈定、类型/大小/张数限制；artifact_ref 绑定 tenant/device/invocation/session 授权。
- 工具结果图片进入 AgentRunner 的模型输入，适配 Web/渠道展示，禁止仅输出客户机路径。
- 获取 jingpian 原 ZIP，核实脚本入口、依赖、mutable 与输出；生成真实兼容清单，按样本实际需要适配。
- 在用户明确授权的设备，验证 UI 配对、导入、审批、status、桌面动作、截图分析与下一步；涉及真实业务写入时使用受控账号/场景。
- 最终同一发行包验证断网、取消、锁屏、崩溃、重启、升级在途调用和结果重投；记录通过、失败、跳过。
- 与桌面文件adapter做共同环境兼容验收：本地/云端同名路径不误路由，skill资源目录不误作用户workspace；batch实际处理/失败数量可核查。按实测记录领取、执行、回传、输出量和模型轮次，不预先承诺设备快路径收益。

验收：真实 Windows 样本完成一条需要看截图的任务；未知效果不重复操作；服务端没有安装样本代码；所有权限负向例通过。环境不可用则保留阶段待验收，不能记为完成。

### Phase 7：后续官方在线分发

- 沿第一方 Provider 规范冻结不可变 release/catalog/envelope、发布审核、签名、撤回与审计。
- Runtime 从受控 catalog 按需下载，短期下载授权绑定设备/release；验证后 staging+原子切换。
- 支持灰度、旧版回退和紧急停用；不允许模型提供 URL 或执行入口。
- 第三方云端市场另立项，不能借官方渠道提前开放任意代码分发。

验收：缺失、验签失败、下载中断、撤回、跨设备票据和回滚均可复现；发布记录不可覆盖，结果日志不随包回滚丢失。

## 4. 开发与验证流程

Phase 0 是文档工作，由主控完成相关核对。后续涉及启动、鉴权、租户、协议与执行，按高风险流程执行：开发与定向自测 → 独立测试智能体 + 独立 CodeReview 智能体 → 主控整合。每阶段冻结交接范围；文档记录真实证据，不累加不同代码状态的结果。

验证重点：

| 边界 | 意图 |
|---|---|
| 双端身份/授权/撤销 | 安装或客户端自报不能扩大 Agent 权限 |
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

| 检查点 | 主导 | 状态 | 本批写入者/记录 |
|---|---|---|---|
| H1 管理port与wire schema | Runtime | 📋 待开发 | 实施前登记唯一写入者与文件，交付Desktop消费fixture |
| H2 固定设备binding/grant/审批 | Desktop | [见桌面计划](plan-desktop-agent-client.md#9-desktop--runtime-共同边界登记) | Runtime提供核验adapter；不独立再建一套绑定 |
| H3 公共壳与实例监管 | Desktop | [见桌面计划](plan-desktop-agent-client.md#9-desktop--runtime-共同边界登记) | Runtime提交core/管理模块；公共main/preload/构建由桌面接线 |
| H4 插件登记与产物 | Runtime | 📋 待开发 | 与Desktop共享artifact展示与模型输入契约 |

2026-10-08：共同契约文档已建立，双方设计/计划与AGENTS均已关联；具体schema和实现未完成，不冒称另一会话已确认或验收。

2026-10-08：用户转交桌面执行环境定位，已按共同契约v1.1补充环境上下文、本地批量计算与审批/计费边界；代码阶段均仍待开发，不扩大首轮为完整PTC或通用Shell。
