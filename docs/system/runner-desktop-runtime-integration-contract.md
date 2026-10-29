# AgentRunner / Desktop / Runtime 共同集成契约

> 契约 ID：`AID-RUNNER-DESKTOP-RUNTIME`
>
> 版本：`2.11`；日期：2026-10-27
>
> 状态：v2.0按用户逐条复核确定，替代v1.3的插件额外审批语义；候选0.3已有真实Host与Desktop消费验证，Windows隔离验收版已交付，尚未共同登记wire冻结。现有公开API以代码为基线；共享格式见第8.1节，任务binding、授权及资源占用wire仍待主导交付。隔离验收不代表生产发行或真实业务验收通过。
>
> 用户目标：两个会话可分别设计 Desktop 与 Runtime，桌面改版通过适配层接入，不重复开发 Runtime 或 CLI/skill。
>
> Desktop：[设计 v3](desktop-agent-client-design.md)、[开发计划](../plans/plan-desktop-agent-client.md)
>
> Runtime：[插件宿主设计](runtime-plugin-host-architecture-design.md)、[开发计划](../plans/plan-runtime-plugin-host.md)
>
> 上位基线：[已完成 AgentRunner 架构](agent-application-architecture-design.md)、[Provider 规范](first-party-cli-mcp-provider-standard.md)

## 1. 适用范围与约束层级

2026-10-29 W14.5兼容纠正：现有显式allowed业务工具保留原模型可见性，设备capabilities只决定自动新增候选；旧provider_id继续兼容。Runtime/CLI/native工具名单、Device wire及执行权限不改，无需因此强制升级客户机。复用原registry/excluded/业务投影，详见[兼容计划](../plans/plan-wecom-cli-group-chat-experiment.md#w145-服务端单独升级的旧端兼容复核2026-10-29)；不改变2.11契约版本。

2026-10-29 W14内部装配登记：[计划](../plans/plan-wecom-cli-group-chat-experiment.md#w14-通用runtime业务工具自动装配2026-10-29)。主控唯一维护目录/Assembly/Runtime投影，独立TEST/CR只读。复用已上报capabilities和原actor设备查询，不变Device wire、权限、冻结恢复/unknown、既有域绑定或配置指纹，因此契约仍2.11，不另造协议。候选实现与模型可见性分离；安装即授权原则保持，无新审批/探测门禁。


2026-10-27 W10实施登记（2.11）：[计划](../plans/plan-wecom-cli-group-chat-experiment.md#w10-真机前综合收敛2026-10-27)。不改Device API claim/permit、OperationResult、原release、收费及授权wire；增量是三项Agent工具和内部恢复语义。唯一写入者：主控维护契约/云proxy/文档；Runner实现角色维护持续恢复，CLI维护native/TS/MCP结果，Runtime维护共用core/Host；独立TEST/CR只读验证。

2026-10-29 W13装配纠正（仍2.11，无wire变更）：内置main同样显式装配既有三项WeCom业务proxy，配置智能体仍遵守原allowed/excluded；不全量开放BOSS、Weixin或内部恢复。原Device授权/绑定/claim/permit与三工具schema均不改。唯一写入者为本会话主控（装配与文档），独立TEST/CR验证；仅本地服务器重载和用户请求的只读Web复验，不自动发送或监听。见[W13计划](../plans/plan-wecom-cli-group-chat-experiment.md#w13-main缺少企微三工具2026-10-29)。

W13实际执行接点：ToolExecutor的InputModel只校验/规范化公共业务字段，LocalToolAdapter注入的原可信上下文完整传给execute；无InputModel路径及纯normalize/冻结恢复证明不改。未增加LLM内部参数、放宽未知公共字段或修改Device wire，已用实际Web DB读取复验。

- Agent仅`wecom_read_session(session,cursor?,limit?)`、`wecom_message_send(session,text)`、`wecom_read_sessions(sessions?,cursor?,limit?)`；MCP保留原调用共15项，Runtime受信9项。CLI生产`ws1`会话、`wb1`分页及`sender_platform`，云端透传，不自建平台算法；缺字段按unknown。CLI0.2.0的schema_digest为`sha256:3cc508ef7b22b0daf2050e5843da644e56165c50bcfb3f479b1835141daa82db`。新Runtime默认兼容旧8项，真实MCP schema证据核实后更新9项；新参数缺能力返回明确升级错误，协商暂时故障可重试，旧入参正常执行。
- 私有`wecom_task_device` v2沿用原root/设备原子绑定，覆盖单聊/群聊现代调用；v1旧群调用继续兼容。`continuous_activation` v1是服务器可信接续来源，不替代权限或重放许可；任务依赖指纹v3仅将可验证匹配的旧v2迁移。暂停/取消优先，暂时源错误保留原wake并退避。
- 命名发送先持久化内部`wecom.resolve`只读phase（native read_session的resolve_only），main只使用该原设备有效结果的canonical；Agent schema不含该参数，稳定ID发送无额外查询。解析阶段不读正文或推进cursor，恢复保原模型意图并验证原解析/main，unknown不因改用稳定ID而重放。
- CLI读取消息保留不可变baseline/start_fence和未知身份的原消息恢复cursor。Runner仅在原invocation存私有`_wecom_inbound`待处理offset，当前结果与`_wecom_recovered`原子交接；checkpoint只存固定轮转位置，原调用/late_result/用量不改写。读unknown仅逻辑允许重读；外发unknown只读对账，不自动重放。
- 既有Runtime恢复观察可带`provider_key=wecom`，能力名`runtime_recovery_wecom_v1`；不伪造BOSS页面，WeCom page为not_applicable。可信resource_stopped可释放原物理hold，不将未知消息变成成功。outbox原payload/endpoint保留，仅本地可选next_attempt_at和有界补投轮次；AbortSignal为进程内参数，未新增wire。已ACK且效果已知、证据一致的journal才按7天清理。
- 生产/消费组合已覆盖旧调用、新schema、真实MCP只读协商及隔离Worker/SQL。签名发行包安装、真实业务UI、多天运行未验，不将源码组合通过当成正式部署验收。历史签名根不替换。


2026-10-26 W6实施检查点：新增`wecom_group_poll` schema v1（target_name/cursor）及文本发送可选`group_cursor`，CLI唯一生产、MCP/Runtime第8项受信工具及云proxy共同消费；旧CLI缺能力在实际listTools门禁零调用拒绝，旧7工具普通调用不变。首次context不作new，后续账号/群/generation/anchor及原群名独立游标；不消费共享watch水位。监控root文本回复必须带原group_cursor、不得ref替代，其他媒体发送不在本轮授权范围；CLI发送前验证原账号/群/库且不可OCR降级。Runner checkpoint私有`resources.wecom_task_device` v1（原root identity/runner_id/device_id）在首次poll与invocation同事务原子固定，后续root/child WeCom沿原设备；运行时配置指纹升级拒绝旧Worker静默忽略。Device/H1/H3 wire与release/unknown/outbox语义不改，新工具只用于新版release。唯一写入：主控CLI/MCP/core及文档，backend角色云端配置/代理/Runner及后端验证，独立TEST/CR只读。详见[W6计划](../plans/plan-wecom-cli-group-chat-experiment.md#w6-配置智能体完整链路2026-10-26)；不部署、不恢复监听，不宣称真实群及多天验收完成。

2026-10-22恢复增量实施检查点（[修复计划](../plans/plan-runner-local-tool-recovery-lockup-fix.md)，开发中）：Device wire、H1/H3及绑定身份不变。唯一写入者：主控维护后端恢复证明/公开投影与本契约；Runtime传输角色维护共享Core及Runtime消费；BOSS恢复角色维护CLI本机恢复。v2 `result-outbox/<id>.json`保持原格式；v1结果使用同一outbox设施的`legacy-v1/`子目录，显式`protocol_version=1`、`endpoint=result`，只补传原payload。旧消费者只扫描根目录，因此忽略并保留v1文件，不得误投operation-result或重执行；新版分别投递原endpoint，ACK前保留。CLI保持OperationResult/error枚举及operation_recovery schema2、visual_recovery schema1；现有诊断字典补充脱敏失败类别/子阶段。browserTargetBinding可增加本机可选业务URL及原profile信息；原profile可由已有可信本机配置或实际浏览器进程参数确认，不强制要求新增managed_profile_dir字段或“曾由本启动器拉起”的磁盘记录。旧记录缺字段但仍可确定原profile时支持自动重开，确实无法确定时准确说明，不猜测陌生profile。只读/可重设页面可重建tab，未知外发不重放；取消/失权禁止重开。当前CLI调用内确定性恢复、Runtime独立续租，不新增本地Agent或后台恢复调度协议；既有release绑定不改，需新版能力则在原会话合法创建新版接续执行。生产/消费与旧格式组合验证按本批影响范围登记，未验收前不宣称兼容通过。此前“只attach”的历史限制在用户明确授权的原profile重开范围内被本条替代；不得杀无关Chrome、清Cookie/profile或偷换账号。

2026-10-20共同检查点：用户授权BOSS视觉恢复直接复用Runtime配对身份，禁止额外URL/ClientBinding token配置。新增设备API`POST /api/local-tools/runtime/boss/visual-diagnose`（复用既有视觉body v1/v2及响应/20倍计费），不改H1/H3/Device配对/claim/permit格式。Host唯一从当前配对配置/DPAPI凭据为受信BOSS进程注入内存字段`AIDWORK_RUNTIME_SERVER`和`AIDWORK_RUNTIME_DEVICE_TOKEN`；不向其他Provider、renderer/IPC、日志或磁盘扩散，不接纳父env覆盖。CLI本地恢复职责保持。旧ClientBinding API不改，历史台账保留；新BOSS只消费新设备入口，旧插件不消费新字段且需升级。唯一写入：本任务主控维护Runtime Host/CLI/Main及版本，backend身份角色维护设备路由/视觉主体与台账；独立角色只读审查/测试。两端计划已同步登记，实施及实际producer/consumer组合测试记录见[BOSS计划23](../plans/desktop-automation/plan-boss-operation-verification-visual-recovery.md#23-runtime配对身份复用纠正2026-10-20)。本版本仅增加该设备API及受信BOSS调用边界，不声称其他共同wire已完成冻结。

涉及桌面重设计、Runtime、插件、设备/workspace 授权及相关 Runner 接线的会话，开始工作前必须读取本文与相应设计/计划。该要求同时登记在根 AGENTS.md，不能依赖某个会话的记忆或转述。

系统/会话指令及用户明确要求优先。本文定义两项工程的共同边界；各自设计只细化内部实现，不能单方面改写共同边界。发现当前代码与文档不符，先区分“代码已实现”和“计划扩展”，记录差异，不能用文档假装新 API 已存在，也不能把遗留耦合继续固化为新目标。

本文冻结架构职责和兼容规则，不冻结页面、组件、目录排版或全部技术实现。后续批准的架构调整可以演进，但须带版本、兼容和迁移记录；不承诺未来所有产品变化都零返工。

2026-10-08 定稿结论：Runtime 的 v1.3 补充与 Desktop 的同机/异机设计一致，无需继续增加架构条款。双方以本版本为实施基线，在职责范围内独立推进；内部实现细节和既定检查点的接口产出不要求再次讨论完整架构。后续只有实际改变共同语义或兼容规则时才升级本契约版本，普通审阅、进度登记不升版本。

2026-10-09用户复核决议：安装即本机使用授权，安装成功默认enabled=true，运行条件独立决定ready，不另设插件审批或默认逐次弹窗。账号/配对/设备使用关系、目标workspace及既有任务策略继续复用。允许安装分析调用云端模型：先读SKILL.md，信息不足时才补传必要源码；这不是本地任务Agent loop。日期标为v1.3的记录属于历史基线，冲突处以v2.0及第8.1节当前候选0.3为准。

当前实施范围：第一部分Runtime UI、BOSS/weixin/wecom第一方插件安装管理及必要核心/既有执行兼容已交付隔离验收版，人工与正式发行门见统一Runtime计划。第三方skill安装、AI分析、动态登记/执行为第二部分，契约/schema/fixture保留，暂不开发。当前以候选0.3为格式入口，不因文档收敛改变C01～C12、schema或版本；第一部分不能把保留的skill格式展示为已支持能力。公共壳/H2/H3职责保持。

恢复增量兼容细节（2.6）：公开 Runner 投影新增可选 `local_recovery_kind` / `local_recovery_actions`，只展示候选动作，不授予执行权；旧 Web 忽略新增字段，新 Web 遇旧服务端缺字段时沿原入口。后端继续在原受权事务核对恢复/结束能力。续筛领域阶段保存私有 `_resume_history_version=2`，旧缺字段沿 v1 原资格和排除名单证明，不改变已接受 invocation 参数；私有版本不发往 CLI/Device API。CLI 本机 binding 的可选 `restore_pending` 只记录已创建页面尚待登录/职位/筛选恢复，原 ref 和 target 索引同步，旧普通读取不依赖这些新增字段。CLI 内部持久记录不承诺在降级至旧插件后仍能自动恢复新 target；在途调用继续使用原 release，不热换代码。实际组合测试及未验收范围见修复计划第9节。

## 2. 必须稳定的十二项边界

| 编号 | 共同要求 |
|---|---|
| C01 | AgentRunner 是聊天任务、推理、等待/恢复及用量的唯一权威；Desktop/Runtime 不新增本地 Agent loop。 |
| C02 | Desktop 使用主 API 的公开 Runner 网关；Runtime 使用设备 API。客户端不持有内部 Runner service token。 |
| C03 | UI 发起/观察/控制任务；本地业务动作由 Runner 创建受权 invocation，经 Device API 到 Runtime。renderer 和 Host 管理接口不得直接发起插件业务调用。 |
| C04 | 同一 Runtime core 服务 CLI、独立 Runtime 客户端、完整 Agent Desktop；独立 Runtime 客户端复用同一 Electron 工程，是已确认产品形态。 |
| C05 | Runtime core 无 Electron/Vue/页面和具体 BOSS/微信业务依赖；Desktop 不导入 Provider domain 源码。 |
| C06 | 设备skill代码和依赖只在设备安装；云端保存原始手册/必要参考资料、能力入口/代码地图及摘要。安装分析先读手册，必要源码允许发给云端模型，不要求服务端部署代码。 |
| C07 | MCP CLI与skill共用安装、版本和执行生命周期，调用语义不同；用户安装即授权使用，安装成功默认enabled=true，运行条件独立决定ready，无额外插件审批。SKILL.md本身不等于脚本入口，手册型skill不虚构入口。 |
| C08 | tenant/user 来自可信认证；执行设备、workspace grant、插件版本来自验证后的绑定。LLM、prompt、自由 request_data 不创造权限。 |
| C09 | 新本地任务固定设备；调用固定 contract/release/digest。换全局 selected、页面、UI 产品或升级不能改变原任务执行位置和代码版本。 |
| C10 | Runner 状态与本机进程状态分别投影；隐藏页面、断开订阅不取消云端任务。退出本机执行进程也不等于副作用已取消。 |
| C11 | 已可能发生写动作的 unknown 不自动重放；ACK 前仅补传原结果。不同 Provider 的 v1/v2 能力按实测登记。 |
| C12 | 修改共同 schema 必须版本化并同时验证生产端/消费端；旧客户机、旧 Web/渠道和已接受任务不能因新版 UI 静默变义。 |

## 3. 三条接口与两个适配层

```mermaid
flowchart TD
  UI[Desktop / Runtime UI] -->|本机 Host Management Port| Main[Electron main adapter]
  Main --> Core[Runtime Core]
  Entry[Web / Agent Desktop] -->|公开 Runner API| Cloud[云端 AgentRunner]
  Cloud -->|受权 invocation| Device[Device API]
  Core -->|claim / progress / result| Device
  Core --> Plugins[MCP / skill / local-file adapter]
  Remote[其他电脑独立 Runtime] -->|claim / progress / result| Device
  Remote --> RemotePlugins[目标电脑文件 / 程序 / 软件]
```

- **Runner API**：任务提交、查询、观察和控制；不能经其普通 reply 提交伪造的设备结果。
- **Device API**：设备身份、能力、领取与执行事实接纳；只有受信 Host 使用 claim/permit/device token。
- **Host Management Port**：本机配对、安装、启停、状态、workspace grant 管理；不提供 `invokeTool`、任意 shell 或通用文件操作。

Desktop 的 Electron adapter 转换 IPC/凭证/选择器；Runtime 的 platform adapter 提供进程、安全存储、文件和桌面原语。调整 Electron 窗口、路由、IPC transport 或打包细节时，只改变 adapter 与产品组合，不要求 Provider/skill 重写。

现有 `LocalToolHostCore.invoke()` 是预留的内部执行接口，不是 renderer 管理 API；不能把该接口直接暴露来绕过云端 invocation。

### 3.1 已实现的 Runner API 基线

源码依据：`src/api/agent_runner_web.py`、`src/services/agent_runner/{contracts,control_contracts,event_contracts}.py` 和当前 Web client。

| 操作 | 客户端公开入口 |
|---|---|
| 能力 | `GET /api/chat/runners/capabilities` |
| 创建 | `POST /api/chat/runners` |
| 查询 | `GET /api/chat/runners/{runner_id}` |
| 找回会话任务 | `GET /api/chat/sessions/{session_id}/runners` |
| 观察 | `GET /api/chat/runners/{runner_id}/events?after_seq=...` |
| 取消 | `POST /api/chat/runners/{runner_id}/cancel` |
| 控制与结果 | `POST /api/chat/runners/{runner_id}/controls`、对应 GET |

桌面首期沿 `source=chat`、`session.kind=web`，不新造 desktop source/会话表。网关创建字段是 `message/session_id/files/subagent` 等，不是内部 RunnerSubmit 的 `text/session/attachments/profile_id`；两端之间的映射由网关负责。

当前能力响应 `contract_version=1` 是 Runner 公开 API 版本，与本文版本、Electron bridge version、Provider protocol_version 分开。公开 controls 只允许 pause/resume/reply；内部 browser_complete 不开放为普通 Desktop 控制。当前事件是 created/revision_changed/terminal/settlement_changed 等状态通知，不能当原始 token 流。

创建 202 只表示接单；相同请求键绑定不可变意图。事件游标用于补读，snapshot/result 是展示基础。新增 binding 字段尚不在 WebSubmission 中，不能自行塞进 request_data 充当已验证授权。

### 3.2 新 Host 管理接口的最小共同面

下面冻结操作语义，非已有 IPC 方法名；实际 DTO、schema、编码和 IPC channel 由第 8 节第一项产出统一冻结。

| 操作 | 输入与结果的稳定语义 |
|---|---|
| describe | API major、实现版本、支持特性、当前状态 revision；不能把 UI 版本作为 Runtime 协议版本 |
| getState / observe | 当前实例、谁监管它、设备绑定、在线/领取状态、插件就绪摘要；observe 提供取消订阅，断流后可重新查询 |
| pair | 可信 UI 提供服务地址、设备名、一次性码；Host 完成配对并保管 token；响应不含 token |
| start / stop | 管理执行实例；stop 先停止领取并 drain，返回实际停止/等待核对状态；不承诺杀进程等于业务取消 |
| plugins.list | 本机安装、启用、就绪及诊断；无插件也是合法状态 |
| plugins.import / enable / disable / uninstall | 本机选择器提供受控 selection_ref，返回 operation_id；执行入口与本地授权由 Host 校验 |
| operations.get / observe | 安装、环境检查、停止等管理操作进度；该 operation_id 不冒充 Runner runner_id 或 invocation_id |
| grants.create / revoke / describe | 受信选择器与策略形成 opaque grant_ref；云端授权接线共用同一模型，绝对根路径留本机 |

必须返回机器可判定错误码及可展示消息；凭证、配对码、claim/permit、环境变量和任意设备路径不进入状态投影。UI 展示类型与 wire schema可分离，但必须通过显式 adapter。

安装操作可以是本机管理操作，不必伪造聊天 Run；用户选择安装即本机授权；安装成功默认启用，依赖/配置决定ready。云端登记校验身份、格式和版本，不增加插件批准关口；上报失败应保留同步诊断，不能伪装已对云端可见。上述管理 port 不接纳模型直接发来的安装或执行参数。

## 4. 两个会话的责任和文件边界

| 唯一实现责任 | 负责工作流 | 另一侧如何使用 |
|---|---|---|
| Runtime core、CLI薄壳、Device API client、MCP/skill生命周期、插件导入/环境/状态/产物 | Runtime 工作流 | Desktop 经管理 port / Runtime child 使用 |
| 公共 Electron main/preload、安全 IPC 注册、全局 Desktop Shell、构建入口与共享 Node 打包策略 | Desktop 工作流 | Runtime 提交独立模块/port及 Runtime 产品需求，由公共壳接线 |
| 独立 Runtime 管理页面/模块及需求 | Runtime 工作流 | 放入独立 feature 模块；不在共享入口另写一套窗口/凭证/监管实现 |
| 公共 Runner client、桌面对话投影、提交/观察/控制 | Desktop 工作流 | Runtime UI 不复制聊天 client 或模型循环 |
| 固定设备 execution binding 的网关/Runner应用层接线 | Desktop 工作流主导共同边界变更 | Runtime 提供设备验证 adapter；不得另定义第二个 binding |
| 本地目录 grant、文件引用/Provider | Desktop 工作流的文件能力模块 | 接入同一 Runtime adapter registry，不建立第二个 Host；Runtime 提供平台原语 |
| 插件契约登记、安装状态、skill加载和执行适配 | Runtime 工作流 | 复用上述 binding/grant/Device API，不能另写任务归属和通用审批账本 |

源码建议模块：Runtime core 在 `clients/shared/local-tool-host-core`；公共壳在 `clients/agent-desktop/electron`；Runtime 专属模块在其 `runtime/` 子目录及 `frontend/desktop/features/runtime/`；路径是组织建议，port 才是稳定依赖边界。

开始修改共享入口、binding、Device API公共模型、schema、全局 package/lockfile 前，在两份计划的共同边界登记区指明本批唯一写入者和文件清单。同一批不得双方并行写这些文件；先交模块后接线。登记不是新的提交授权。

上述责任是工程分工，不意味着另一个会话可以向用户声称已协作验收。另一侧尚未读取、实现或验证时，按实际状态记录。

## 5. 任务绑定、资源和版本的共同语义

- **任务 binding**：网关验证设备归属、设备授权和必要 workspace grant后，持久化到原任务意图/ExecutionState，再经 ToolExecutionContext 派发；子执行继承。不能每个步骤重新查全局 selected。
- **旧 selected**：只保留在明确的 legacy路径；新契约拒绝静默回退。纯云端任务不必绑定设备。
- **workspace grant**：可撤销、有 revision 的受权目录引用，不是“知道 grant_id 就授权”。插件状态目录与用户文件 workspace 分开，安装 skill 不自动获得文件 grant。
- **插件选择**：每次 invocation 固定 release和 content/contract digest，不假设一个 runner 只能用一个插件。加载手册与对应调用版本必须一致。
- **资源结果**：本机文件引用、云端 file_id、device artifact_ref 保持有类型的归属；不能互用裸绝对路径。云端 Agent读取本机返回的内容会经过服务端/模型。
- **恢复**：分别检查 Runner attempt/owner、claim/permit、绑定/授权 revision 和本机操作事实。账号改变或旧 owner 失效不能借新身份重传业务意图或执行下一步。

execution_binding、grant与新增审批的精确 DTO 在共享 schema冻结前，双方可以完成内部设计、云端纯聊天、插件本地导入/检查和管理 UI mock。不得分别实现两套 wire协议后再拼接。授权与恢复的共同接口必须先于依赖它的业务代码定稿。

### 5.1 Runtime 作为 Runner 的统一任务执行环境

2026-10-08 用户转交的桌面设计定位与 C01～C12 一致；补充以下语义，不另建调度或授权体系。

Runtime 除插件宿主外，还提供任务执行上下文和文件、进程、应用、产物等能力适配。任务上下文由同一 H2 受信 binding 派生，至少关联原 runner/execution、固定设备、可选 workspace grant及版本、策略引用、取消/执行权和本次 invocation。具体字段由 H2 schema统一冻结，Runtime 不自行定义第二个任务绑定 DTO。

read/edit、受控 command、skill 和软件操作均使用该上下文及相应能力授权；不能 read在客户机而 edit写服务端同名目录，也不能每步重新选设备。云端附件仍有独立 file_id位置语义，不因任务有本地 workspace 而自动变成本机资源。

“同一执行环境”是相同的任务/设备/资源授权语义，不要求每任务启动 VM、每插件共用一个 Python解释器或所有进程使用相同 cwd。插件包目录、任务workspace、可写状态与产物目录用途不同；脚本相对用户文件路径必须通过已验证的 workspace/输入引用解析，不能按插件源码cwd猜测。Plugin adapter可拥有独立venv，但不能借此更换设备或授权主体。

普通本地程序可执行循环、分支、过滤、统计、格式转换和批准的确定性动作链。需要新的模型判断、扩大权限或新的业务决策时，由原 Runner继续；不在 Runtime新建模型循环或把任意代码包装成批准的 batch。

批量能力通过已登记的结构化工具或受信脚本入口提供，先实现 batch search/read和必要本地聚合，完整PTC/任意 execute_code后置。批量中的文件操作仍遵守同一grant和版本检查；部分失败、漏读、截断要返回实际数量和完整性说明。带副作用的批次中断后，不自动从头重跑；只有有证据的动作适配器才可按原操作事实核对。

本次新增语义不代表脚本已有OS隔离：cwd/venv/目录引用不是沙箱。未隔离第三方代码仍可能访问当前用户资源，其运行必须符合明确本机授权和企业策略；要求强隔离的策略不能用一次授权弹窗代替实际隔离。结构化文件能力可约束文件访问，不能据此声称任意Python脚本也已被限制。

### 5.2 审批和计费的边界

Runner/服务端策略保存审批与执行许可权威；Desktop或独立Runtime UI展示批准交互，Host复查执行边界。安装即插件使用授权，不新增插件审批或默认逐次调用弹窗；身份/设备/workspace和确有既有策略要求的任务许可继续共用原体系，不分别建业务批准账本，也不能把普通澄清reply转换为许可。

保留云端Runner是为复用企业任务、授权、恢复与账本，不以“本地loop无法计费”为理由。Runtime不另建模型用量账本；必要的执行耗时、资源使用和业务回执由既有服务端计费规则接纳。模型供应商usage仍来自实际模型调用。

性能按领取等待、执行、回传、输出量和模型轮次分别测量；本地batch与脚本优先。后续快路径仅替换通信transport，不改变invocation身份、授权、去重、取消或原结果核对；本版不冻结尚未测量的低延迟承诺。

### 5.3 操作入口与执行电脑分离

用户已确认两种部署形态：Runtime 与完整 Desktop 同机，作为该电脑的执行入口；Runtime 独立安装在另一台电脑，由 Web 或 Desktop 经云端 Runner 调度。后者适用于特定网络、特定软件以及 BOSS 等长期占用交互桌面的专用电脑。两种形态共用执行核心和 Device API，不因入口位置新增一套 Runner。

| 操作入口 | 固定执行设备 | 共同语义 |
|---|---|---|
| Desktop A | A 上受管 Runtime | Main 可管理本机实例及选择本机目录；业务调用仍经 Runner |
| Desktop A | B 上独立 Runtime | A 不必启用自己的 Runtime；任务的文件、命令、软件与网络访问发生在 B |
| Web | A 或 B 上已配对 Runtime | 浏览器不安装执行核心；通过可信网关选择有权使用的设备 |

目标设备选择、可用能力及忙碌状态通过用户认证的服务端设备接口查询；Host Management Port 仅管理同机实例，不直接暴露为网络远程管理接口，也不把 B 的设备 token 发给 A。具体列表/选择 DTO 与现有接口适配由 H2 冻结。Web 与 Desktop 使用同一 binding 校验，不能让 Web 继续依赖全局 selected 来实现新任务语义。

每个新设备任务在接单时固定目标设备；界面展示执行设备、连接/忙碌状态及资源归属。本轮不扩展为一个任务随意跨多台电脑，后续如需多设备编排另行版本化。目标离线、缺少软件或特定网络不可用时返回等待/失败事实，不迁移到入口电脑或云端。

设备列表、心跳和忙碌投影是选择提示，不是执行许可或预留成功。实际执行前仍由服务端核验授权、Runtime核验能力和资源占用；列表查询后状态改变时返回原设备的等待/拒绝事实，不借“自动选择可用设备”改变已固定任务。

### 5.4 目标设备目录、网络与产物

Desktop A 的本机目录选择器只能建立 A 的 grant，不能据同名路径生成 B 的授权。远端文件任务使用 B 的 Runtime 本机建立、经服务端验证且允许当前用户/Agent 使用的 grant；新建远端 grant 由 B 的受信管理交互完成，首期可预先在 B 配置。Web/远端 Desktop 选择可用 grant 的引用，不获得任意枚举 B 文件系统的权限。

文件引用和产物始终携带固定设备归属。A 的 Main 本机打开接口拒绝 B 的文件引用；查看 B 的产物需经受权内容/产物接口，下载到 A 是显式复制操作，不改变原文件的位置和授权。云端附件继续沿自身 file_id 语义，不能将 B 的裸路径交给 A 或服务端解析。

Runtime 执行的请求使用目标电脑的实际网络、软件安装和登录态；拥有特定网络并不自动授权所有网络资源。网络和凭证能力按策略校验，状态只投影可用性和必要诊断。此契约不增加通用代理、网络隧道或把 B 的网络/凭证复制到 A。

### 5.5 专用电脑、长时间执行与资源独占

独立 Runtime 在其配置的生命周期内持续领取/执行，Web 关页或 Desktop A 退出、注销不停止 B 的执行实例，也不取消已接受任务；显式撤权、取消、租约失效仍按原权威和核对链处理。目标电脑休眠、锁屏、断网和退出 Runtime 分别报告实际状态，无窗口运行不保证 GUI 在锁屏时可操作。

设备在线、Runtime 单实例和桌面资源独占是不同状态。同一电脑交互桌面的锁覆盖全部插件、CLI 与后台观察；纯文件/计算不机械占用桌面锁。Runtime 负责实际资源仲裁，服务端/入口展示等待、占用与阻断，不创建第二套业务调度器，也不凭远端 UI 状态强行解锁。

BOSS 等跨多次 invocation 的连续 GUI 流程若需保持桌面独占，不能假定已有单次 invocation 锁已经满足。H2 与 H4 实施前统一资源范围、原任务 owner、占用期限/续租、跨调用保留及释放/失联核对语义，并验证全部竞争入口。确定性程序可在一次已授权执行内持续持锁；模型思考或等待期间是否保留桌面，必须由流程契约明确，不默认永久锁整台电脑。未知副作用核对与资源占用分别处理，不能无限占锁或自动重放。

现状证据：`clients/agent-tool-runtime/src/desktopLock.ts` 的 `withDesktopLock(fn)` 在回调结束后释放锁，`invocationRunner.ts` 在单次Provider调用外持锁；其锁按本机用户/交互会话派生，不依赖云端device_id。这证明已有同桌面单调用仲裁，不证明跨调用独占已实现。

长任务按其实际支持的协议复用 progress、取消、claim/permit、journal/outbox 与续期机制，记录当前机制不能覆盖的超时/占用边界；不把旧skill v1视为具备完整v2恢复能力。新增字段、状态及超时规则先经 H2/H4 schema 冻结和兼容验证，再承诺长时间无人值守能力。

实施前还须冻结以下四项，均为上文边界的细化，不增加另一套业务调度器：

1. **资源需求来自Host核验登记的契约**。是否操作桌面、是否跨调用保留独占，由登记的工具/流程契约声明并在执行时核验，不能由LLM参数或脚本自报“只读/纯计算”解除锁。未知第三方脚本保留保守桌面占用；经明确核验的文件/计算能力才可免占桌面，不增加插件批准步骤。当前调用链统一持桌面锁，按资源分类并发属于待实现扩展。
2. **占用过期不等于进程停止**。跨调用占用记录关联原任务、执行owner和可核验的占用代次；旧owner不能续租、释放或借重启沿用新的占用。取消、超时、失联、Host崩溃后，确认原进程及相关后台动作停止才能将物理资源交给下一任务；不能仅凭云端租约到期或锁句柄消失认定桌面安全。无法确认时明确阻断冲突调用并进入核对，展示原因和受信本机处置入口。业务effect仍可为unknown，不能因释放资源就将其改成none或重放；反之，已确认动作停止后也不因业务结果unknown永久持锁。
3. **断网执行必须有可验证的许可边界**。只有协议与本机监管明确支持、且仍在有效许可内的已批准确定性执行才可推进；不得凭缓存授权新领任务、新建写动作许可或无限续租。要求在线复查的动作在断网时不得开始；本机撤权/停止及许可到期按预定停止策略处理，确认不了停止则按上一项阻断。云端撤权在离线设备上不能承诺即时生效，H2必须明确许可期限与失联策略，策略不允许离线继续时不得启用该能力。恢复联网仅核对和补传原事实。
4. **调用超时、占用期限与流程期限分开**。progress不自动延长执行许可或超时，资源续租也不自动延长脚本运行时间。当前skill默认900秒、硬上限1800秒，不因采用独立Runtime就获得无限时长。超长流程优先由原Runner按受权调用分段推进，跨调用占用不等于跨调用授权；确需扩大单次时限时先冻结监管、取消及兼容规则，不能用重复启动脚本规避限制。

### 5.6 通用持久化休眠增量（2026-10-22）

用户确认由 Agent 主动调用业务工具并自主休眠，到期由云端 Runner 恢复同一任务。候选[设计](agent-runner-durable-sleep-design.md)及[计划](../plans/plan-agent-runner-durable-sleep.md)扩展云端 timer 等待与持续任务，不要求 CLI 主动唤醒，也不在 Runtime 增加模型循环、长期业务调度器或 sleep Device/Host wire。

本条在契约2.7登记实施检查点；公开Runner API保持contract_version=1，候选0.3 Host格式不变。沿原会话、runner行wake_at与checkpoint特性扩展，不强制新会话、timer表或全局schema升级。云端timer不能延长原invocation的timeout/permit/资源独占；休眠与唤醒继续核验原主体、固定设备、任务claim和已有调用事实，unknown写不自动重放。单次调用版本固定规则保持。

实施前由本任务主控登记Runner接单/快照/能力协商/checkpoint的候选版本、唯一写入者和生产/消费fixture；Web/Desktop公共Runner消费与现有两项计划登记影响，Device/Host/Provider无变化则明确记录不受影响。旧worker当前没有新任务隔离领取保证，启用前须验证领取池版本一致；不以旧UI忽略可选字段代替严格解析及恢复兼容验证。此时再按实际差异登记契约版本，本次不宣称已共同验收。

2026-10-22用户反馈候选：普通请求不增加必填模式，仅可选continuous或经原ask_user/reply绑定确认启用；新普通任务默认50业务轮次且成功纯sleep不占数，实际模型调用/费用仍记账。会话显示累计积分，由用户决定任务寿命，不新增任务总预算。多个runner或单任务多目标的本机调用复用既有invocation队列/标准领取loop/桌面锁，CLI互斥不再复制成新队列；本条不扩张wecom受信工具面或宣称5群已验收。

实施共同检查点（生产/消费测试完成后才记为验收）：唯一实现写入方为本Runner功能工作流，休眠开发角色拥有park/恢复/worker，持续任务开发角色拥有模式确认/账本/公开投影，主控拥有Web消费者与契约；同一源码文件修改前交接所有权。

| 接口/格式 | 冻结增量 | 兼容及验证 |
|---|---|---|
| 创建与能力 | 可选`execution_mode="continuous"`，普通请求省略；能力`durable_sleep_v1`、`continuous_execution_v1` | 旧省略字段digest不变；缺能力即不支持；默认关闭，新API/worker组合隔离测试后才开放 |
| 公开视图 | 顶层可选`execution_mode`、脱敏`sleep_blocked.error_code`；`usage={task_credits,session_credits,pending,updated_at}`；snapshot保留实际iteration、新增business_iterations及可选history_version=1 | 白名单显式投影，无私有checkpoint泄露；Web真实producer/consumer fixture验证，旧消费者可查询/取消 |
| timer leaf | `kind=timer,version=1,seconds,reason,wake_at,wait_id,target_execution_id`，嵌套child_wait沿原树 | resume携带原wait/leaf，服务端消费再次核验；旧无定位客户端可取消，不能盲目继续timer |
| 分段执行保护 | `kind=continuous_guard`，assistance_id按实际iteration区分本段；同样定位原wait/leaf | 根或child均可显式继续，仅重置所选叶段起点，旧wait不得给新段额度；子执行正常委派回复仍可完成并返回父 |
| 持久格式 | 原runner行nullable wake_at、checkpoint.durable_sleep.version=1，continuous.version=1 | 同事务park/消费、当前owner fencing；未知版本拒绝，旧任务无字段沿原恢复规则 |
| Device/Host/Provider | 无新增字段或接口 | 无本地睡眠/模型loop；原claim/permit/release/digest/锁不改 |

回退必须先关闭新能力准入并保留支持版本处理存量，或安全结束新语义任务；旧worker不能与新timer同池，即使paused仍可能被旧cancel-only领取。不得以pause所有任务作为充分回退方案；配置/全局prompt严格指纹不关闭。实际producer/consumer测试与限制统一登记[休眠计划第13节](../plans/plan-agent-runner-durable-sleep.md#13-本轮实施基线与共同检查点)。

2026-10-22整体复审修复检查点（2.8，本地开发验证完成）：唯一修复角色维护云端恢复/工具身份/归档及schema，主控维护本契约和关联设计；独立测试与CR只读。Device/Host wire及API major 1保持。云端私有`tool_identity_version=1`按原持久模型调用区分新工具代次，旧pending及已接受invocation不重命名。历史产物复用chat_messages.metadata已有images/downloadableFiles；不在Runtime新增loop、队列或资源授权。累计BIGINT为数据库内部兼容扩大。公开snapshot新增可选`history_revision`（非负整数，单调历史投影水位，不是event_seq/权限），由原history_offset、artifact_history_offset及archived_child_count导出，同checkpoint重试不变；供客户端在窗口退休后拉取已提交历史。旧客户端忽略，新客户端遇旧服务缺字段保持原行为。Web消费保持每会话一个在途刷新，成功才推进已读取水位，失败允许后续事件重试；generation/认证变化丢弃旧结果，以messageId合并历史而不覆盖瞬态输入。需同时验证公开白名单/事件、TS、真实历史metadata消费者及旧端组合。实际实现/证据与未完成项统一见[休眠计划第17节](../plans/plan-agent-runner-durable-sleep.md#17-复审修复与验证2026-10-22)；本地Runner/Web生产消费、独立测试和CR已通过，未提交、部署或宣称真机/多天验收。

2.8的历史窗口读取面（本地Runner/Web验证完成，未部署）：复用`GET /api/sessions/{session_id}/messages`，无runner_id时沿原接口/缓存；有runner_id（≤128字符）时为原session授权下的归档窗口读取。可选before_id为默认"0"的十进制非负ID字符串（严格格式且≤INT64_MAX），0取最新页、正数取id更小的更早页，limit限定1～100。按session及runner共同过滤并直接PG读取，SQL先排不可展示tool声明，再倒序取limit+1、正序返回；涵盖control:user和child产物。响应沿原messages/failed_message_ui并加has_more:boolean（有更早展示行）、next_cursor:string（本页最早ID，空页沿输入before/0）。参数不授予执行或跨会话读取权。

Web自动刷新只更新最近100条归档展示窗口，不将全部历史回填浏览器；更早记录按页替换、返回最新重取。浏览旧页时live只更新当前Runner状态，不能冲走窗口；当前任务投影、乐观输入及停止控制保留，当前assistant按稳定投影ID定位。history_revision仅表示最新窗口更新水位，成功请求后才推进，失败保持当前窗口并可重试，不声称全历史已加载。旧客户端忽略新字段，新客户端遇旧服务缺字段沿原行为；默认接口/原历史缓存不改变。复用原MessageList/BaseButton及默认兼容followLatest，不新增历史中心、页面、模型loop或执行权限。已本地验证长期浏览器状态有界、当前输出/输入保留、旧页/最新切换、加载失败/并发与认证隔离、键盘/窄屏及真实历史metadata消费者，证据统一见休眠计划第17节；Desktop及真机Runtime组合不在本轮验收范围。

### 5.7 wecom发送对账接线（2026-10-26）

本增量版本2.9登记`wecom_send_status`业务schema v1；MCP/legacy工具集合及各自既有digest算法更新，Device v1/Host候选0.3/H3/Runner公开API major不变。唯一进度见[wecom计划](../plans/plan-wecom-cli-group-chat-experiment.md#2026-10-26-mcp与runtime发送对账接线)。主控唯一写MCP与共享core providers/providerManager及关联TS测试/本文；后端开发角色唯一写local_tools注册、代理、原发送scope及Python测试，另限域写Runner local_recovery.can_restore的status参数等价证明；既有owner/phase比较及_known不变。Runtime与Desktop计划均引用此记录，未实现/未测项不登记验收。

本地输入receipt_id仅为当前用户DPAPI证据目录标识；云端模型传send_invocation_id，由服务端从原可信wecom_message_send结果提取receipt，验证tenant/user/session/固定原device。detached hold只对服务端建立并在enqueue/claim重验的精确只读原发送scope开放，不据模型tool名/readonly/参数放行。对账结果保持独立，只读成功不等于发送confirmed；原unknown、hold、已接受release和invocation审计不修改，未知发送不重放，原Runner verification等待不自动解除。无receipt/失效来源保持原核验与权限门。初次对账仍核当前显式选机/固定binding；选B而原send在A时拒绝、需选回A，不自动换机。来源/phase在排队后失效仅收敛未started的status为failed/none并让队列继续，不改原send。该server拒绝可用result_json顶层私有version=1零派发证明恢复既有失败：status ID、Cloud send ID、business ref和完整native request/owner必须全部匹配，客户端data不可信；无标记/坏版本不享该窄恢复，旧严格source校验保留。不新增公共wire、结果outbox格式或恢复队列。

Runtime追加第7项wecom只读工具，写集合及锁/超时/许可/原结果outbox不变；旧CLI仍可用旧六项，新工具实际listTools/schema/readonly门禁不通过时明确none/升级，不静默调用未知工具或回退发送。能力缓存绑定子进程代次；signed旧release不原地改变，新版需后续显式升级。工具调用本身0积分，不替代模型实际用量计费。新增工具不是Device wire新增权限或资源停稳证明，不将本轮接线冒充长期群聊/多设备/锁屏验收。

2.9实施状态：本地MCP/Runtime/后端生产消费及独立TEST/CR已完成；PG63/TS32通过，单测173通过另2个HEAD旧BOSS基线失败保留。后端仅严格server no-dispatch证明可判该status自身物理完成，持久hold查询带齐必需字段，原send unknown/hold及全局完成码不变。真实企业微信、完整Worker受当前SDK阻断的旧场景、发行/安装及长期运行未验；没有提交或部署。全部命令、负例修复与范围统一见wecom计划最终结果，不以本条代替产品/线上验收。

## 6. 产品生命周期差异由壳配置处理

完整 Desktop 首期保持关闭最后窗口退出；独立 Runtime产品形态关窗口收至托盘。该区别是壳配置，不是两个 Runtime core。

实例明确记录由 Desktop、Runtime App 或 CLI 管理。完整 Desktop 退出/注销仅按其受管实例的策略停止领取、隔离其会话缓存与目录授权；不能自动停掉外置 Runtime App 的独立配对实例。对共享/外置实例的停止须有明确管理操作，不因观察窗口关闭而触发。

同一 runtime home/设备身份只有一个执行实例。遇到已有实例连接受控管理通道或显示冲突，不抢占、覆盖凭证或重新配对。两种产品 app identity/userData 分离，Runtime home及迁移规则共用。安装/更新一个 UI产品不得删除另一产品的设备记录或未 ACK结果。

Provider 的 Node executable、进程监管、凭证后端由平台 adapter注入。Provider 不根据 UI名称判断执行行为，普通 Node core 不 import Electron。windows交互会话是桌面操作前提；无窗口与 Session 0/锁屏操作不能混为一谈。

## 7. 兼容与变更规则

1. 本文小版本允许补充不改变旧语义的要求；移除/改义必须升 major，并在两项设计/计划登记影响与迁移。
2. wire schema采用自己的 api_version/feature协商。仅增加“可选且缺省兼容”的字段也须验证严格解析器是否接受；不能仅凭字段可选就认定兼容。
3. UI改布局、路由和 renderer实现可独立进行；如受控 Node启动或 IPC改变，由 Desktop adapter吸收，Provider和Runtime业务模块不追随改版。
4. wire破坏性变化提供新版本/显式 adapter，保留旧在途调用和恢复数据直到安全排空；不把旧 runner/release静默转换成新版。
5. 不支持的版本/特性返回明确状态；只禁用依赖该能力的功能，云端纯聊天或其他可用插件可继续。权限相关差异不得猜测兼容。
6. 每次共同接口变化必须同时更新 schema、生产/消费fixture及两项计划；未通过兼容门的实现只能留内部，不可宣布已集成。

不要以“另一个会话可能不同意”为理由阻止契约内可独立推进的工作。边界内决定由对应工作流负责；边界变更先形成可审阅差异与迁移方案，必要产品取舍再由用户决定。

## 8. 实现前共同检查点

两份计划共用同一登记项；仅完成本文不代表下列项已完成。

每项实施记录以主导工作流的计划为唯一进度来源，另一计划引用；共同契约记录语义和兼容决议，不复制双方开发日志。

| 检查点 | 唯一主导 | 必须交付的证据 |
|---|---|---|
| H1 管理 port/DTO/错误与事件 | Runtime | 中性 schema/type、producer fixture、consumer fake；Desktop adapter验证；不依赖Electron对象 |
| H2 固定设备 binding/grant/审批 | Desktop | Web/Desktop共用目标设备与grant选择、可信网关与Runner扩展、Runtime核验；身份进入不可变摘要与恢复链；与H4统一长流程资源占用语义 |
| H3 实例监管与两产品生命周期 | Desktop | 受管/外置实例、Node路径、home/凭证、drain/退出/更新；Runtime conformance验证 |
| H4 插件登记与结果/图片 | Runtime | 受权契约/revision/schema、调用版本、设备归属artifact；提供长流程资源需求与真实仲裁证据，Desktop展示兼容 |

新增中性 wire schema建议落 `contracts/runtime-host/v1/`；在实际实现时再生成/维护类型与fixture。现有 `contracts/desktop-agent/` 属旧 D1，不能仅因名字相似当成新契约继承。测试不只比较 typecheck：必须同时使用真实生产端和真实 adapter检验生命周期与副作用边界。

架构定稿与 wire 冻结分别登记。每批接口至少明确请求/响应字段及单位、必填/缺省规则、错误码与状态、版本/能力协商、可信身份与幂等摘要、许可/超时/撤权/恢复时序，并交付生产和消费样例。接口可以先冻结 schema 再实现，但不能以本文定稿声称上述字段已经确定；样例验证也不冒充真实端到端验收。按 H1～H4 的唯一主导分批交付，不要求所有后续能力同时完成才能开发已有公开 Runner 对话或不依赖新增协议的内部模块。

### 8.1 Runtime schema / fixture 交付（候选0.3）

2026-10-09，Runtime依据用户复核更新[共享入口与消费说明](../../contracts/runtime-host/v1/README.md)。Desktop后续接线前读取该入口，不再按设计草案自行另写同名DTO。

开发前细化候选0.3：plugins.list成功result改为{instance_id, revision, plugins}，插件项可选真实version仅用于展示；旧数组需显式迁移，列表与Host状态共用revision水位及实例/旧连接隔离。0.2尚未冻结上线，本批在同目录替代，api_major/schema_version仍为1，架构v2.0及H2/Device协议不变。七项内部实施决议见[Runtime设计第12节](runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)；H3设计输入由[Desktop第13节](desktop-agent-client-design.md#13-h3-首期设计输入runtime-ui与第一方cli)维护，设计交付不代表真实接线。候选0.2历史审阅保留，Desktop须重新消费0.3后登记结论。

- H1：[管理请求](../../contracts/runtime-host/v1/schemas/management-request.schema.json)、[响应](../../contracts/runtime-host/v1/schemas/management-response.schema.json)、[状态](../../contracts/runtime-host/v1/schemas/host-state.schema.json)、[通知](../../contracts/runtime-host/v1/schemas/host-event.schema.json)，配套[管理fixture](../../contracts/runtime-host/v1/fixtures/management.json)。
- H4首批内容：[插件契约](../../contracts/runtime-host/v1/schemas/plugin-contract.schema.json)、[安装清单](../../contracts/runtime-host/v1/schemas/plugin-inventory.schema.json)、[输出内容](../../contracts/runtime-host/v1/schemas/tool-output.schema.json)，配套插件、截图/unknown/截断及拒绝fixture。
- 可运行验证：目录内`scripts/check.py`和`tests/consumer.test.mjs`；消费fake仅供Desktop编写真实adapter参考，不是已接线的客户端。

这批有意保持最小格式，不定义H2 binding/grant/claim/许可/长占用，不替代现有Device结果与ACK，不引入另一套模型循环、事件账本或类型生成框架。沿用候选0.2的固定request_id/method/code/error/result；code为非负整数，0且error为空表示请求成功，失败code非零/error非空/result为null。管理operation也固定code/error，查询成功与operation失败分开。工具内容不带success，effect/complete独立保留。plugins.list无registration审批字段；skill有entry.description、可选output_schema及code_map（包内相对path/description），手册型允许空entries/code_map。

候选0.1/0.2未上线或冻结，当前由0.3替代；目录v1、api_major/schema_version=1保持，不能把架构2.0当成API major。旧候选回复需显式迁移/重新验证，严格schema拒绝旧形状；已有生产Device协议不改。首期签名规范化、真实Host/Desktop管理接线、进程监管及隔离验收包已有实现证据，但共同wire尚未登记冻结，H2/H4新登记与产物链仍未完成。当前实现及限制统一见[Runtime架构](runtime-plugin-host-architecture-design.md)和[Runtime计划](../plans/plan-runtime-plugin-host.md)，不沿用历史候选的待开发状态。

### 8.2 连接注册与设备替换增量（2026-10-13，实施中）

共同检查点由Runtime主控登记，保持架构2.0/H1候选0.3不变，新增注册能力修订1与Device pairing_protocol=2；不是已部署能力。唯一写入者：Runtime主控维护注册暂存/core/Main/preload及H3消费，服务端开发角色维护票据/替换持久关系和Web；独立测试与CR共同核对生产消费端。详见[恢复计划Phase13](../plans/plan-runtime-upgrade-recovery.md)。

| 接口/记录 | 本批冻结规则与兼容 |
|---|---|
| Web POST /api/local-tools/devices/{old_device_id}/replacement-ticket | 登录tenant/user校验旧设备归属，返回code/expires_at/replacement_device_id；普通票据接口不变。名称/机器指纹不创造撤销权限。 |
| POST /api/local-tools/runtime/pair | 可选pairing_protocol严格整数2；普通码旧客户端不变；替换码旧客户端明确拒绝且不消费。替换响应可选replacement_pending:true；只创建新设备/绑定，不撤销旧设备。 |
| POST /api/local-tools/runtime/replacement/complete | 新设备token鉴权，空请求/{}，只完成自身服务器绑定关系；success/replacement_completed，幂等且竞争替换不能多赢家。不得接纳请求指定oldID/租户/用户。保留旧任务事实，不自动改selected。 |
| H3可选registrationStatus() | ConnectionDiagnosis v1或null，仅投影受保护本机注册步骤，无凭据/路径，无HTTP/历史资源扫描；存在此端口才启用continue_registration。旧renderer忽略新端口，旧preload下新renderer不发新mode。 |
| H3 reconfigureConnection | v1新增register_new（地址/名称/配对码）和continue_registration+confirmed:true（不带码），仅registrationStatus能力存在时使用。旧renderer调用new_device时新Main在消费码/改状态前明确CLIENT_UPDATE_REQUIRED，不能静默改为stage；新renderer旧preload仍交旧Main处理原new_device。其余DTO字段不变，结果new_device只在活动home安装后为true。 |
| 注册修订1 | 固定home旁CurrentUser DPAPI事务/独占lease；requested无OTP，未知不自动重发；paired无claim，activation绑定同UUID/归档epoch。旧资源门用于安装/执行；active待注销失败仅重试complete，不再次pair或归档。 |

C09～C11不变：旧任务固定旧设备，不迁移/重放旧结果；停止证明与业务结案不同。pending注册阻止本产品自动/普通start和pair，stop/退出仍可用；真正旧CLI停止需资源证明。注册和凭据写入成功且资源交接后才撤销旧身份；服务不可用、身份无效、保护/权限/并发失败须明确可操作阶段，未知HTTP不能显示成功。旧服务器不支持替换接口时明确升级服务端，不降级误撤。

## 9. 兼容验收矩阵

### 2026-10-14 用户批准的干净安装增量（2.1）

本节替代第8.2节独立Runtime新版的历史资源核对激活门；旧客户端协议1/2及完整Desktop现有行为保持兼容。唯一进度来源：[干净安装计划](../plans/plan-runtime-clean-install.md)。H1候选0.3、Runner binding语义不变。安装角色唯一维护安装代际与旧进程处置，服务端角色唯一维护Device pairing_protocol=3/DDL，主控维护Main/H3自动注册及消费端。

用户明确授权安装/新配对自动停止本产品旧执行、移除旧自启、隔离/清理旧运行状态。新版只迁移有效身份配置及独立验证的兼容插件，不加载旧journal/outbox/session/管理记录决定新启动。旧业务副作用不承诺撤销，云端事实不标成功、不迁移、不重放；旧记录或删除失败不阻断新安装。旧进程须验证产品作用域和当前用户身份，不能批量终止Node或用户其他浏览器。

2026-10-14安装确认补充：安装器先只读检测；旧客户端运行中先提示“确定后自动退出并安装/取消则暂不安装”，任何停止或自启清理必须在确定之后。静默安装遇运行实例默认取消；检测后新启动实例也不能未经确认停止。卸载、新配对及普通运行时切换保持既有语义，H3/wire/Device协议不变；主控唯一维护NSIS/PowerShell，结果见干净安装计划Phase5。

协议3用受保护的新请求身份保证配对响应丢失/退出后的幂等恢复；同事务创建新设备、撤销凭原token或服务端替换票据验证的旧设备、选中新设备。名称/机器指纹不创造撤销权限，不撤销同账号其他电脑。不支持协议3时明确提示服务端升级，禁止静默退回两步pending注册。H3保留旧可选注册端口兼容，但新Runtime界面一次提交自动完成激活，不要求手动continue或历史重启恢复。

H3可选automaticConnection只由新版独立Runtime preload开启。Device `/runtime/activate {}`仅鉴权设备自身，兼容旧协议2暂存身份自动收尾。跨账号旧凭据不产生撤销权也不阻断可信新票据注册。旧授权事务门先锁device再锁任务，覆盖claim/许可/两类续租。用户本次明确批准验收包也接管当前Windows用户精确全局npm旧CLI（该旧CLI无发行profile），不扩展为终止其他用户、通用Node或另一完整Desktop；最终测试nonce另隔离真实进程/自启/安装登记。

| 情形 | 共同预期 |
|---|---|
| 新旧 UI适配同一管理 API major | 两边均可读状态，不改变业务 schema |
| UI断订阅/换页/改版 | 已接单云端 runner继续，重连可查询；无重复调用 |
| 新 UI连接外置/旧 CLI Runtime | 不抢占和重配对；明确协商能力或显示需升级 |
| 纯云端桌面会话，无插件 | 仍可正常工作 |
| 安装/升级/停用某插件 | 不替换在途 release；不影响其他插件 |
| selected改变、grant撤销、旧账号/owner | 原任务不得转移；失效执行权不能产生下一动作 |
| 写后崩溃或 ACK丢失 | 保存/核对原事实，只补传结果，不重复业务动作 |
| 未授权客户端伪造管理/设备消息 | 不获得代码执行、跨租户数据或工具结果接纳权限 |
| 新 schema超出支持范围 | 明确拒绝该能力，不静默回退旧 D1、云端执行或任意 shell |
| 本地read/edit/skill访问同名路径 | 使用同一任务环境及受权资源；不得误写服务端目录或按插件cwd猜测用户文件 |
| batch部分失败/输出截断/写后中断 | 返回实际完整性；不能静默跳过，也不能把整个批次从头重跑 |
| Desktop A / Web选择B，无A本机Runtime | 同一受权任务可在B执行；软件、文件和网络均以B为目标 |
| A选择本机目录，或打开B文件引用 | 不能建立B的grant或在A解析B路径；远端产物经受权接口显式查看/下载 |
| A退出/注销、Web关页，B仍在执行 | B不因观察入口生命周期而停止；显式撤权/取消仍按原任务规则处理 |
| BOSS长流程与其他插件/后台观察竞争 | 按声明的独占范围等待或拒绝；跨调用锁、续期及崩溃释放有实际证据 |
| 占用到期/Host崩溃，旧脚本或后台动作仍在运行 | 冲突调用保持阻断；旧owner不能操作新占用；确认停止后释放资源，业务unknown仍不重放 |
| 自报纯计算、心跳显示空闲或progress持续更新 | 不创造免锁/执行许可，不自动延长调用超时或占用期限 |
| 执行中断网、许可到期或本机撤权 | 按已冻结许可与停止策略处理；不新领或新批写动作，不能确认停止时阻断资源交接 |
| B锁屏/休眠/断网/特定网络失效 | 报告阻断或未知事实；不迁移设备，不将在线等同可交互或自动重放 |

后续代码开发按项目高风险流程执行，保留独立测试与CodeReview；协议fixture通过不替代最终Windows安装包的真实进程、Provider及结果验收。

## 10. 给新桌面会话的交接文本

> 基于当前已完成的 AgentRunner 重设计尚未开发的桌面能力。开始前读取根 AGENTS.md、`docs/system/runner-desktop-runtime-integration-contract.md`、桌面 v3设计/计划及Runtime插件宿主设计/计划。遵守共同契约v2.0（用户复核修订）：桌面负责任务工作台与公共Electron适配，Runtime负责统一任务执行环境、设备执行核心和插件生命周期；独立Runtime客户端复用同一工程。Web/Desktop可调度同机或另一电脑Runtime，目标设备固定，远端目录授权和产物保持设备归属，长GUI流程需明确资源独占；租约到期不代表旧动作已停止，断网许可和三类时限须先冻结。安装即授权/默认启用，先手册后必要源码生成能力说明与代码地图；读取候选0.3的code/error及列表水位/版本格式，旧候选需显式迁移。当前只实施A1～A4，第三方skill B1/B2暂不开发。先核对C01～C12和H1～H4，不恢复旧本地Agent/D1路线，不复制Runtime，也不单方面定义binding/管理wire协议。可以自由调整页面和交互；共同边界变化须给出版本和兼容方案。只设计未开发内容，已有成果、其他会话改动和旧设备身份保留；未获明确指令不提交或部署。

## 11. 登记记录

| 日期 | 版本 | 内容 | 状态 |
|---|---|---|---|
| 2026-10-27 | 2.11 | W10三业务工具、平台投影、continuous可信接续及WeCom原设备资源恢复；Device授权wire保持 | 开发、独测及CR通过；发行安装、真实发送和跨日运行未验 |
| 2026-10-08 | 1.0 | 建立职责、接口边界、文件责任与H1～H4共同检查点，登记到双方设计/计划和AGENTS | 文档约束建立；双方实现/契约测试未完成，未向其他会话发送消息 |
| 2026-10-08 | 1.1 | 根据用户转交的桌面定位，补充统一任务执行环境、本地批量计算、审批/计费与性能边界 | 兼容补充；新增wire schema及实现仍待H1～H4冻结，未向其他会话发送消息 |
| 2026-10-08 | 1.2 | 用户确认同机/异机Runtime，补充目标设备选择、远端grant/产物、特定网络及长流程独占 | 本批Desktop工作流唯一文档写入者：共同契约、双方设计/计划的引用与交接、ideas桌面索引；无wire/代码变更，Runtime实现未代为验收 |
| 2026-10-08 | 1.3 | Runtime侧审阅并保留v1.2方向，补充批准的资源需求、停止核对、断网许可及三类时限 | 本批Runtime工作流修改共同契约、双方设计/计划引用与交接及Runtime内部细化；仅文档核对，H1～H4实现与双向验收仍待完成 |
| 2026-10-08 | 1.3 定稿审阅 | Desktop工作流接受Runtime补充，无新增架构要求；明确架构定稿与wire冻结分开登记 | 本批Desktop仅修改共同契约、双方设计/计划的定稿状态与交接、桌面索引；版本不变，无schema/代码修改，接口及真实兼容验收待实施 |
| 2026-10-09 | 2.0 用户复核 | 用户逐条确认C01～C12及schema；取消插件额外审批，允许手册优先/必要源码的云端AI分析，统一数字code/error，候选0.2替代0.1 | Runtime唯一写入；用户产品规则确定，Desktop重新消费/真实接线与wire冻结待完成；不改变已上线Device协议 |
| 2026-10-09 | 2.0 / wire候选0.3 | 开发前七项实施决议，列表实例/revision包装与可选真实version；旧数组显式迁移 | Runtime唯一维护共享格式，独立测试/CR证据见Runtime计划第8节；Desktop须重新消费，真实接线与wire冻结未完成 |
| 2026-10-26 | 2.9 | wecom发送只读对账工具schema v1、原发送/设备scope及有限私有零派发证明 | 本地生产消费/独测/CR完成；真实群聊/发行/长期运行未验，未提交部署 |

### 2026-10-20 复审历史：恢复观察v1（2.3，未发行实现已纠正）

2.3曾将纯页面观察前置为scope登记后Windows重启，且Python/sessionTasks配置会禁用整个观察能力。复审确认这是过度门禁，相关新增见证和默认producer已删除；此前隔离测试只证明旧实现按当时规则运行，不能证明设计合理，也不能作为2.4修正验收。原已接受v1 invocation结果接纳保留兼容；后续请求统一按下方2.4。Phase9精确未决集合、缺指纹不连坐其他电脑、新意图不受历史登录错误限制等修正保留。

### 2026-10-20 复审纠正：观察与停止独立（2.4，四项修正已验证）

本记录优先于上方2.3未发行实现。唯一代码及文档写入者为恢复修复开发智能体，主控及独立测试/CR只读核对。Device claim/permit与H1/H3不变；Host观察请求/结果升级version=2、能力runtime_recovery_observe_v2，旧v1不能消费v2请求；旧回执没有代次时明确停稳未知，不推定已停止。

- 删除新增scope重启见证；纯CDP页面观察不前置Windows重启、Runtime重启或停稳证明，不因Python/sessionTasks配置禁用。
- resource_stopped与page_observed各自独立；纯观察成功不改变原业务unknown，不自动放行未知写动作。原认证迟到回执继续按已有完成判定解除自身hold。2.4未新增停稳producer，当时无回执历史hold未闭环；此阶段限制已由下方2.5设备受管边界停止路径替代。不能把新空home或页面可读当成旧执行已停。
- page_summary增加有限页面事实与match/mismatch/unknown，复用既有导航路径、可见筛选徽章及面板判定；不能核对完整原条件时保留unknown，不从计数相同推出选项相同。
- GET仅查询实际未决回执并投影入口，不修改冻结集合或审计历史；迟到明确回执与设备准入使用同一完成判定。
- 本轮四项修正已通过独立测试与CodeReview；实际范围、历史停止关联缺口及验证登记在BOSS恢复计划Phase10，不宣称资源恢复全部完成。旧v1测试不能作为本次修正通过证据。


### 2026-10-20 显式停止原设备受管资源（2.5，已完成开发）

本批唯一写入者为恢复开发智能体；主控、独立测试与CR只读。Web/内部verify-local请求可选stop_resources:boolean（默认false保留v2纯读），显式true派发version=3，独立能力runtime_recovery_stop_v1；Device claim/permit和H1/H3不变。原请求版本、模式、设备、scope及ID集合冻结于business_ref，结果仅核销本次集合，不改变原业务unknown或重放未知发送。

- 显式动作停止本Runtime/home启动的旧工具进程及旧孤儿Job，可能同时关闭由Runtime启动的浏览器；不停止其他设备或当前真实活动任务。服务端在同设备/同机实际claimed/running时拒绝；Host同桌面锁下暂停Provider新建，拒绝真实busy，关闭空闲Provider，复用原生terminate/probe逐一确认既有DPAPI Job为0，finally仅恢复临时fence，不重开永久停用的admission。Provider握手中的transport也纳入回收，原manager引用不得复活。
- 原设备配对身份、home独占lease与既有受保护Job账本构成设备级执行边界，不要求旧invocation逐一有generation；存量unknown/result_json=NULL可据此解除自身hold，原业务结果仍unknown。
- 既有私有DPAPI ledger添加可选business_started:boolean。新marker=false；受权普通BOSS Provider调度才置true，每代持久化一次，失败不保留内存成功标记；runtime_recovery_observe/boss_recovery_probe永不置true；旧无字段监督记录兼容。停止须有可信业务覆盖及非空受管Job集合，新空home先纯读产生的probe-only记录（包括重开Runtime后）不能自造历史停止证明。严格读取者同时兼容此可选字段，旧客户端需升级，不新增账本。
- 真正活跃的未受管旧实例与系统进程检查不可用分别报告。空Jobs的旧登录session不阻断；非空旧session以既有DPAPI原生停止回执核对，或核实该旧session已无进程后确认边界消失，不能仅凭当前session查不到Job放行。不凭空home、busy=false、截图或用户声明签发证明，不将重启Windows作为常规门。纯读v2始终独立可用，Python/sessionTasks配置不整体禁用。
- 版本3停止证明绑定scope/device/原ID集合/runtime_generation，证据为supervised_device_boundary、resource_scope=managed_provider_pool；消费仅限原BOSS调用，不用于未受管skill-runner/Python。页面是否观察/匹配单列，目标冲突或损坏标记page_target_unavailable，跳过页面观察，仍可提交真实stop proof；不静默换页。仅精确当前版本3显式scope可解自身资源hold。
- 版本3领取及started同时核验当前冻结scope与数据库时钟截止；过期或被新请求替代的queued stop永不执行物理回收，也不能阻塞新scope领取。迟到回执可保留历史，仅当前精确scope可改变hold。普通业务180秒与v2观察兼容语义不变。
- 冻结源码通过独立120项（native15、PG27、frontend78）及宽窄屏UI，CR无未解决P0/P1/P2。实际native Job dummy→Host→result API/PG→新意图create/claim/start已验证；跨端夹具的Runner终态不是新增真实Worker结算，不操作真实业务或重启用户系统。唯一进度与结果见BOSS恢复计划Phase10；未部署。
