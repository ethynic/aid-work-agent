# 开发计划：AgentRunner 通用持久化休眠与自动唤醒

2026-10-27增量实施：continuous运行中失租可信接续、任务依赖指纹v3、timer源暂时错误退避及死Worker模型调用保留unknown后续做，统一在[W10实施记录](plan-wecom-cli-group-chat-experiment.md#w10-真机前综合收敛2026-10-27)开发与独立验证；保留既有park/timer、会话claim、计费事实与停止优先级，不另建调度服务。真实部署与多天持续运行尚未验收。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 设计 | 现有 Runner 核对、通用休眠设计与索引 | ✅ 完成（2026-10-22） | 明确主动轮询、原子 park/唤醒、持续激活额度、计费与兼容边界；仅文档，无代码/迁移/部署 |
| 复审 | 简化设计、恢复边界与现有机制冲突核对 | ✅ 完成（2026-10-22） | 主控对照源码自审，修订与证据见第10节；不作为独立CR或运行验证 |
| 用户反馈 | 持续确认、存储、多目标、积分展示与50轮语义 | ✅ 完成（2026-10-22） | 六项决议见第11节；第5点已澄清为显示累计积分，无任务积分上限；仅更新文档 |
| Phase 0 | 最小持久布局、恢复/控制契约与兼容冻结 | ✅ 完成（2026-10-22） | 已同步master，契约2.7及实施布局见第13～14节 |
| Phase 1 | 普通任务50业务轮次、安全休眠与根执行唤醒 | ✅ 完成（2026-10-22） | 真实PG/跨进程/新Worker恢复、纯sleep及新旧默认策略验证通过 |
| Phase 2 | 暂停/立即继续/取消与 Web 展示 | ✅ 完成（2026-10-22） | 前端111项、build及1280/390px浏览器状态/键盘检查通过 |
| Phase 3 | 受管子执行休眠与原树恢复 | ✅ 完成（2026-10-22） | 原child恢复、guard继续/旧wait拒绝、低余额安全暂停独测通过 |
| Phase 4 | 澄清启用持续、累计积分与长期正确性 | ✅ 完成（2026-10-22） | 原策略确认/账本/历史幂等及有界容量独测通过，无任务积分上限 |
| Phase 5 | 独立验证、CR、发布前检查与验收材料 | ✅ 完成（2026-10-22） | 六项及追加边界修复；最终独测/CR/主控检查通过，结果见第17节；未提交或部署 |
| 后续验收 | agent2/真实Runtime与wecom、持续时长观察 | 📋 待开发 | 尚未提交、推送或部署；需用户后续明确指示后进行，不代表多天已验 |

2026-10-26配置智能体持续群聊联动在[W6计划](plan-wecom-cli-group-chat-experiment.md#w6-配置智能体完整链路2026-10-26)完成隔离开发验收：复用现有sleep/continuous/history与用量，补独立群游标、配置入口和原设备固定；独测PG76通过，CR问题均闭环。隔离新Worker/Runtime/MCP验证不等于真实群或多天验收；旧pytest入口安全风险单独登记W9。此次不提交、部署或恢复监听。

> 日期：2026-10-22；设计：[通用持久化休眠与自动唤醒](../system/agent-runner-durable-sleep-design.md)；上位：[现行 Runner 架构](../system/agent-application-architecture-design.md)、[共同契约](../system/runner-desktop-runtime-integration-contract.md)。
>
> 2026-10-22最新授权：用户明确要求在wecom工作树按计划开发，先同步master，再走开发、独立测试与CodeReview；禁止提交和部署。此前仅设计的限制已被本轮授权替代，旧文档检查记录仍只代表当时状态。

> 工作归属：`C:/repos/aid-work-agent-wecom`，分支`feature/ask-user-question-card`。本轮已启动Runner开发；[agent2分支发布脚本](plan-agent2-branch-deploy.md)已完成本地验证，部署仍需另行授权。

## 1. 目标与范围

交付 Agent 可主动调用的通用 `agent_sleep(seconds, reason?)`：持久保存等待、释放执行资源、到期自动恢复同一任务，继续主动调用原有业务工具。continuous 任务无默认结束日期，用户可控制；本能力不依赖 wecom，也不要求业务工具推送事件。

按用户最新决议：普通创建不增加必填参数，仅保留可选continuous；不确定是否长期时复用ask_user澄清。普通新任务默认50业务轮次、纯sleep不计；长期任务不设总积分预算，会话展示实际累计积分供用户判断是否继续。支持多会话多任务共享设备和单任务多目标，设备冲突排队优先复用已有队列/锁。

首版 Web 直接沿接单时已有会话、现有 Runner/Device API 和父子内核；保留旧无模式参数请求、原会话claim与渠道行为。不新增独立任务会话创建协议、跨会话关联中心、timer表、全局checkpoint schema升级、通用归档平台；不开发wecom监控器、CLI watch接入、长期工具进程、子智能体消息邮箱或新调度服务。

## 2. 当前代码证据与风险分级

已只读核对 `contracts.py`、`engine.py`、`runtime/controls.py`/`tools.py`、`durable_control.py`、`execution_repository.py`、`worker.py`、`recovery*.py`、`runtime/child*.py`、`finalizer.py`、`transaction_repository.py` 和 Web 网关。

关键事实：park 可释放 lease 但保留 session claim；恢复当前依赖显式 control；Engine 按累计 iteration 限额；子树已保存但没有通用 timer；当前终态/迟到结算不足以支持长期不结束；checkpoint 已有字节上限，不能仅压缩消息就假设多年状态有界。wecom 相关代码的上轮核对来自 `C:/repos/aid-work-agent-wecom`，仅作能力示例，不作为本任务实现基线。

本轮纯文档，由主控修改和检查，无需三智能体。后续实现涉及调度/并发、持久迁移、计费及核心工具装配，按[开发流程规范](../../.claude/rules/dev_workflow.md)高风险流程：开发及自测 → 独立测试 + 独立 CodeReview → 主控整合检查；提交、部署分别等待用户明确授权。

## 3. Phase 0：实施前冻结

交付可核对的 schema/状态转移/生产消费 fixture 与变更清单，更新本计划后再写实现：

1. 固定 agent_sleep 参数、非 durable/未适配来源可见性，复用 pending prepared 尾部调用及整树停稳检查；区分 sleep 秒数、调用 timeout、claim/permit 与执行 lease。不新增“sleep 必须最后调用”的任意限制。
2. 冻结原 agent_runners 可空 wake_at 与部分索引、原行锁/claim顺序和 scope/tenant NULL 规则；复用 owned_wait_id 和 checkpoint waiting/resources/envelope，新增特性namespace版本，不先全局升 schema。
3. 冻结 Worker 受信恢复来源、原 sleep ToolFact 完成与 Engine 唯一消息配对、最近消费凭据、失租后未动作恢复证明；提取 RecoveryCoordinator 共用核验，sleep 不进入本地 invocation 证明，不伪造用户 controls。
4. 冻结timer resume的wait_id/叶、两个revision和已受理resume优先，覆盖ABA竞态；普通创建省略新字段，只接受可选continuous。冻结ask_user私有purpose/确认选项绑定，reply消费与策略启用同事务，不改原input/digest。
5. 冻结50业务轮次与实际iteration双计数、成功纯sleep豁免水位、当前执行段起点，含失败/混合sleep/恢复的计数；明确用户确认及执行保护暂停的显式继续政策，不复用积分预算概念。
6. 沿现有实际收据、record/差额扣费确定非终态结算状态及事务锁顺序；明确跨任务并发余额语义、迟到事实和领域计费，不另造计费体系。
7. 沿现有消息表冻结 continuous 已闭合历史的稳定ID/提交水位及终态补写，按已结算record与未应用收据确定增量计费，不预设新归档/聚合表；同步 attempt时长累计和Trace迟到投影规则。
8. 冻结capabilities/公开投影/旧Web组合、特性版本、旧worker退场与回退，含任务/会话累计积分与待结算标记；核对全局configuration_fingerprint及附件TTL。阶段实现只做隔离验证，完整能力未通过前不得提前公开。
9. 复用PostgreSQL同事务的wake_at/checkpoint/controls，首版不增加Redis调度依赖。核对已有invocation队列、Runtime标准串行loop、桌面锁与wecom互斥分工，确定5会话/1任务5目标的隔离与队列等待测试，不新增CLI内部队列。

验收：上述决策无影响正确性的未定项；共同契约登记拟实施版本、唯一写入者及兼容路径。涉及 Web/Desktop 公共 Runner 投影时，在双方关联计划登记消费者责任；不顺手修改 Host/Device/Provider wire。不将设计候选标为已部署能力。

## 4. Phase 1：普通任务50业务轮次与根休眠闭环

拟影响：`src/tools/control_set.py` 与 `src/tools/agent/` 控制工具定义、runtime 工具装配、Engine/ExecutionState、Runner durable ports、仓储/worker/recovery、数据库迁移与系统表文档、定向测试。

- 新增仅受支持 durable 环境可用的 agent_sleep；保留未开始的prepared尾部调用，不安全位置明确失败；park同事务保存wake_at、整树checkpoint、状态及事件，提交前不能公开虚假deadline。
- 现有worker领取到期原行，校验claim/主体/配置及两个revision；已受理resume/暂停/取消优先。加载明确的timer恢复来源，而不是绕过现有applied_control_id门后从头运行。
- 将sleep原结果交给Engine单一配对；提取共用恢复核验，不能让无invocation的sleep被LocalRecoveryProof拒绝或fallback成verification。
- 覆盖park前/后、领取未提交/已提交、原结果已配对/尚未新动作、进入before_model/tool后的不同故障；未armed的纯sleep意图显式恢复须有SLEEP_NOT_COMMITTED收敛出口。reap_expired后的安全timer交接须有扫描入口，一般interrupted/unknown不自动重放。
- 有界分批轮转queued/controls/timer，空睡眠释放任务并发与本地锁；保留原会话claim和普通中断恢复语义。
- 默认值从20统一到50，覆盖ExecutionState、ContextAssembler及SubagentConfig默认回退；显式业务配置和旧已接受快照按兼容策略保留。新增实际iteration/业务轮次口径，成功纯sleep的豁免与park原子提交，不改call_id、不删除真实收费收据。

验收：实库1～10秒单轮/重复休眠、关闭观察者、重启后过期唤醒；双worker唯一领取，park失败无虚假休眠。普通任务第21～50业务轮可执行、第51业务轮受保护；成功纯sleep不占50轮，混合业务+sleep计1轮，非法/未提交sleep不豁免；重试不重复豁免，睡眠也不重置已用业务轮次。

## 5. Phase 2：用户控制与 Web

拟影响：control_repository/recovery、公开投影/capabilities、Web Runner 类型/client/composable/任务展示、通知与相关测试；以现有组件规范为准。

- pause沿现有acknowledged_pause/pause_requested阻止到期领取，不引入独立timer状态机；resume绑定原wait_id/叶，立即完成剩余sleep，补充输入沿原controls幂等键。
- cancel 先使 timer 不再可领，再沿既有取消核验/finalizer；到期/手动继续/取消同一锁顺序、只消费一次。
- 当前会话内输入转为原任务控制；已受理的补充输入不能被自动到期抢占。原queued项仍按会话claim等待；停止按钮不依赖模型醒来，不新增跨会话入口为前置。
- 显示准确 deadline、等待原因、允许操作和阻断；刷新找回、查询和 SSE 补读有效；timer 不冒充 clarification、不每轮推送。
- 准备会话/任务累计积分、更新时间和待结算标记的公开白名单/事件消费；完整增量费用数据在Phase 4联调，不展示按token估算的伪精确积分，不新增预算填写/阈值暂停入口。

验收：Web当前会话提交→睡眠→立即继续/补充→暂停过期→继续→停止；旧wait控制不得误唤醒下一次sleep；网络重试不重复创建/消费。低积分、失效附件或profile不兼容仍可取消；其他等待类型、普通队列和旧前端取消保持可用。

## 6. Phase 3：受管子执行

- ChildControl 将叶 timer 上抛至原父树，保持真实叶 execution_id/tool_call_id/wait_id；根 timer 投影可验证。
- 整树停稳后 park；睡眠期间无父子协程等待，不消耗既有 7200 秒活跃结果等待 timeout。
- 接入Worker._drain_children：timer子树仅剩确定性收尾时正常drain后park，不能被现有active+unfinished分支interrupt/stop=True；真实兄弟运行仍明确拒绝/中断，不把未完成树伪装完成。
- 唤醒复用原 ChildRecoveryAdapter，完成原 sleep 工具结果并恢复原父委派调用；不重复建立子执行或合并重复用量。
- 首版单 timer，不安全兄弟/未知写/未完成调用明确拒绝；不加入父子主动消息功能。

验收：根/standalone/一层及嵌套 child 等待和唤醒；父取消/暂停、兄弟运行、子树版本不兼容及 child 等待超时边界。

## 7. Phase 4：澄清启用continuous、累计积分及长期正确性

- 普通请求省略新字段，明确持续请求可带continuous；复用ask_user固定用途表单及reply，用户肯定答复原子启用当前策略，否定/跳过不启用，原input/digest不改；普通reply不能误启用。无需增加模式选择页。
- Engine和RecoveryCoordinator共用50业务轮次/纯sleep豁免；continuous无任务总轮次上限，合法sleep后继续下一段。单段忙循环耗尽则可恢复地暂停，由用户继续/停止；普通暂停/重启不自动赠段。持续任务的一段普通回复不直接标整个任务完成。
- 增量结算与余额准入在下一次收费 dispatch 前、park 前完成；重用稳定 record 与现有费用事实，不依赖最后结束。账本失败阻止新动作，低余额保留任务等待处理。
- continuous裁剪前幂等写入已闭合消息，稳定ID/历史水位与finalizer协同；不能按裁剪后的数组位置逐轮调用原write_history。保存游标、未决调用、累计基数与owner；复用既有收据，不预设新归档系统。
- attempt_durations_ms滚入累计基数，Trace防止旧attempt的finally覆盖新激活；实际费用/增量统计必须基于持久事实，而不是每attempt重置的collector。
- 配置 defaults、能力开关和公开支持状态一致；最短 sleep/单次上限为资源准入，不是任务总寿命限制，不改 max_tokens 控预算。
- 将原归属费用投影为任务/会话累计积分；父子/工具只计一次，unknown待结算明确标注。用户不设置任务积分上限，继续/暂停/停止无需新增费用审批；原账户余额准入仍生效。
- 用不同任务及目标状态验证5会话各跟一目标、单任务5目标；已有设备队列/桌面锁按操作粒度释放，云端sleep不占锁，游标与取消不串任务。真实wecom读取接线未完成时用支持游标的工具隔离验收，不据此冒充wecom已可用。

验收：持续任务跨多个50业务轮段合法运行；纯sleep不计，实际模型轮次/积分不丢，执行保护暂停可由用户继续。澄清确认/否定/重复/取消竞态及旧无参数请求均覆盖。会话显示正确累计积分，不因任务累计消费达到任意阈值自动暂停；低账户余额仍能保存/停止。5会话/1任务5目标隔离、队列取消/等待和共享读取水位不漏消息。加速样本验证历史ID、快照/时长和费用聚合受控。

## 8. Phase 5：独立验证与发布前检查

| 类别 | 关键场景/业务意图 |
|---|---|
| 通用性 | 使用两个无业务副作用的不同测试工具，证明计时机制不依赖 wecom/本地 CLI |
| 实库原子性 | park前/后、领取前/后、原结果已配对但无新动作、首次dispatch前/后；验证Worker实际恢复入口和reap后的候选扫描 |
| 并发与控制 | 双进程、受理resume优先、带输入控制不丢、旧wait跨下一次sleep、暂停与到期、取消与派发、旧owner迟到写 |
| 权限与隔离 | 不同租户/用户、NULL 平台账号、切号/撤权/低积分/配置变更及父子 owner |
| 轮次/费用 | 普通默认50业务轮、成功纯sleep豁免、mixed/失败sleep；持续无总预算、会话积分与父子/工具去重；忙循环保护与原余额准入 |
| 容量与公平 | 覆盖未裁剪会超现有限额的加速样本；due/queued/control不饿死；历史ID、attempt时长、Trace及收据聚合受控 |
| 兼容 | 旧checkpoint/新增namespace、缺省digest、全局prompt/config指纹、原附件TTL、旧Web/服务组合、旧worker包括cancel-only的回退门 |
| Web 与通知 | 断线/刷新/重试找回、任务会话输入、停止语义、非澄清 timer、不逐轮推送 |
| 主子恢复 | ROOT/STANDALONE/CHILD、原子树恢复、无多余 Task/锁/重复委派 |
| 持续确认 | 绑定purpose的肯定答复、否定/跳过/重复/取消竞态；普通reply不改模式，原请求摘要不变 |
| 多任务/多目标 | 5会话5runner共设备、1runner5目标、各目标游标与部分失败、有限队列等待/取消；共享watch水位不得冒充每任务已消费 |

相关测试优先扩展现有 `tests/unit/services/agent_runner/`、Engine 单测、`tests/integration/agent_runner_service/` 与 Web Runner 测试；命令使用 `./scripts/dev_test.sh <相关路径> -p no:cacheprovider -q`，实库使用现有隔离数据库模式。需要多个真实 worker/可控进程终止，不以全 mock 代替领取竞争证据。前端改动运行现有测试和 build，并验证实际页面。

独立测试者记录通过/失败/跳过、数据库/解释器/进程形态、用量/消息次数与工作区版本。独立 CR 核查事务锁、执行树/许可/轮数/费用和兼容；主控核对最终 diff、关键 import/配置解析/worker 入口、迁移新库/升级库及重复执行。服务启动在隔离环境，不启动真实自动回复或服务器部署。

真实时间 smoke 与计划 soak 记录实际连续时长、故障点、唤醒延迟、内存/快照大小和扣费；可控时钟循环不能冒充多天验收。发布准备可完成，但部署目标/动作和提交均需用户另行明确指示。

## 9. 本轮文档验证记录

- 已按现有架构形成独立增量设计及本计划，原已完成 Runner 重构计划保持归档。
- 主架构和共同契约补充拟议增量入口，`docs/ideas.md` 登记为待开发；不迁移至已完成开发索引。
- 文档检查：5个相关文件共120个本地文件链接，119个目标存在；唯一缺失为 `ideas.md` 原有的 `design/wecom/wecom-cli-localdb-design.md`，本次未修改该条目。本次新增文档及新引用无缺失。
- 新文档无尾随空白、代码围栏闭合；索引编号唯一，计划顶部进度登记存在，6个实施阶段均为待开发。3个已跟踪修改文档的 `git diff --check` 通过；新文件另行检查，不借此宣称业务测试通过。
- 未运行业务测试、创建 timer、修改代码/SQL/配置、提交或部署；设计核对不构成实现或独立测试通过。

## 10. 设计复审（2026-10-22）

本节记录上一轮主控对照现有源码的文档自审；不是独立CodeReview，也未运行功能测试。结论：方向成立，原稿存在不必要前置和数个可导致从头执行、丢输入或无法恢复的遗漏，已按下表修订设计与本计划；实现阶段均仍待开发。涉及模式确认及轮数政策的最新用户决议以第11节为准，旧表不覆盖最新要求。

| 编号 | 优先级 | 原稿问题与代码证据 | 文档修订 |
|---|---|---|---|
| R1 | P1 | `worker.py:419`仅applied_control_id触发resume_state；timer只取得lease可能重跑原输入；`runtime/tools.py:52`和`recovery.py:175`不认识无invocation的sleep | 显式受信timer恢复来源，共用树验证并先完成sleep事实，Engine单一配对，不伪造control |
| R2 | P1 | `control_repository.py:219/241`主要推进control_revision；自动到期与已受理resume可能抢占；裸resume不绑定一次等待 | 锁内核对两种revision及resume_control_id，受理控制优先；timer resume冻结wait_id/叶，拒绝跨下一次sleep的旧控制 |
| R3 | P1 | `reap_expired`只变interrupted；只扫描到期waiting会遗漏已消费后崩溃；原稿另加开始标记却未落实扫描闭环 | 复用现有before_model/tool事实及最近消费基线，有界扫描已失租且尚未新动作的交接；进入新动作后维持原中断规则 |
| R4 | P2 | 原稿强制独立会话、timer表和schema2，超出单timer需要；已有session claim、waiting/resources及owned_wait_id可复用 | 使用原会话、原runner行wake_at与特性namespace；不新增会话创建协议或全局schema前置；明确同会话并行交谈限制 |
| R5 | P2 | “sleep必须最后一个工具”绕开了已有pending prepared恢复机制；每工具释放证明接口也可能扩面 | 允许仅未开始的尾部工具随checkpoint保存；复用现有停稳/未决事实，不增加通用释放协议 |
| R6 | P1 | `recovery.py:163`对带输入resume另检累计轮数；只改Engine无法完成continuous边界唤醒 | 同时更新恢复与Engine额度政策；合法sleep先建立激活起点，已完成祖先不复活，普通恢复不赠额度 |
| R7 | P1 | `worker.py:309`的drain会对active且未完成子树interrupt；timer子执行尚未结束任务记录时可被误取消 | 为可证明timer停稳/收尾补正常drain分支，保留真实运行/未知动作中断，不伪装child_tree_finished |
| R8 | P1 | 裁剪未覆盖`write_history()`按数组下标编号、plan/local引用及`attempt_durations_ms`；Trace每attempt重建且共用ID | 原消息表稳定ID/水位，终态补余；同步闭合引用、时长折叠、Trace按attempt保护，去掉新归档平台前置 |
| R9 | P1 | 活跃结算和每次收费准入未区分安全park/取消；上一笔耗尽积分可能让下一次sleep变失败 | 先保存已发生事实，余额不足阻止新收费并保留blocked等待；取消不依赖余额/附件可用，不扩张认证权限 |
| R10 | P1 | `runtime_manifest.py`散列全局Agent/LLM及全部prompts；`RuntimeFactory.attachments()`恢复时读短期上传信息；旧worker仍能取消领取paused任务 | 显式记录指纹与附件失效边界；只改必要扩展点；回退不能仅暂停任务，必须排除旧worker接触新语义任务 |

保留的必要复杂度：continuous的增量计费、有界checkpoint/消息历史和严格恢复事实。它们分别防止长期欠账、状态超限和重复外部动作，不能因“简化”删除。首版仍只支持一棵执行树一个有效timer，不增加监听服务或多个独立子timer。

下一步仅是未来获准编码前的Phase 0：冻结原字段/消息ID及消费凭据的精确形状、增量结算的现有分组计费兼容、公开投影fixture和发布退场流程。本次没有运行证据可以证明这些路径已实现或可发布。

复审文档检查通过：5个文件120个本地文件链接，除第9节已登记的1个存量wecom链接缺文件外无新增缺失；两份新文档的7个链接均有效。代码围栏、尾随空白、10条复审表格、索引唯一性和6个待开发阶段检查通过，已跟踪文档的git diff --check通过。上述为静态文档检查，未执行功能测试；没有代码、SQL、配置、实际任务或部署变更。

## 11. 用户六项反馈与确认（2026-10-22）

本节替代此前公开bounded/continuous二值、接单后不可经澄清变更模式，以及20轮普通默认值的候选政策。用户另行回复已明确：第5项是显示累计积分、由用户决定继续或停止，不是设置积分上限后自动暂停。本轮仍只改文档。

| 事项 | 决议 | 实施/验收落点 |
|---|---|---|
| U1 持续任务确认 | 普通创建省略新字段，仅可选continuous；明确持续意图不反复问，不确定时复用ask_user澄清 | purpose/原wait/固定确认键绑定；reply消费与当前策略启用原子提交；否定/跳过不启用，input/digest保持 |
| U2 PG与Redis | 首版PG保存wake_at/checkpoint/controls同事务；Redis可持久化，但本批不增加双写调度 | 核对索引/正常持久化配置及重启恢复；将来真有性能瓶颈才考虑可丢失的唤醒提示，不替代PG权威 |
| U3 多会话多个长期任务 | 5个会话5个runner可共享设备，操作经已有invocation队列、标准loop及桌面锁 | 不另建wecom CLI业务队列；验收公平/有限等待、取消和设备离线；CLI的BUSY互斥不等于队列 |
| U4 单任务多个目标 | 1个runner可以逐轮跟进5个群或其他目标，一棵树仍只需一个timer | 逐目标身份/游标/结果；部分失败不重做已完成目标；不同runner消费进度不共用一个全局watch水位 |
| U5 累计积分 | 不设任务总预算/积分阈值暂停；显示本任务及本会话累计已用积分、待结算状态，用户决定继续/停止 | 实际归属收费、父子/工具不双计，public_view/事件明确接线；原账户余额准入仍生效 |
| U6 50轮与sleep | 新普通任务缺省50业务轮，成功纯sleep豁免，真实模型轮次/费用照记；持续任务没有总50轮终止 | 50默认源一致；混合业务+sleep计1轮，非法/未armed不豁免，重试不重复豁免；原显式配置及旧快照兼容 |

进一步的恢复边界：确认持续的reply提交后崩溃不得重复询问或丢策略；取消先落库则不得启用；纯sleep已豁免但park未提交不得出现；已启用continuous的一段最终文本不能误终结“直到用户停止”的任务；单段忙循环防护暂停后的继续必须是用户明确选择，而非重启自动恢复赠段。

Runtime/CLI核对证据：主分支`src/local_tools/repository.py:claim_next`已有持久队列，`clients/shared/local-tool-host-core/src/legacy/pollLoop.ts:claimLoop`逐项await runInvocation；`invocationRunner.ts`调用共享desktopLock。用户指定wecom worktree的`clients/wecom-cli/src/mcp/server.ts:runExclusive`冲突返回BUSY，watch按轮释放锁并支持focus多目标；不把这些源码事实冒充真实5群业务或公平性已验收。

本轮文档验证：5个文件118个本地文件链接，117个目标存在，唯一缺失仍为原有wecom本地库设计索引，未改该条目；新增外部技术依据已核对Redis/PG官方说明。围栏/空白、复审表/6项反馈表、索引唯一性及6个待开发阶段检查通过，跟踪文档git diff --check通过。上述为静态检查，未运行业务测试；代码、20轮默认值、接口、队列行为和真实任务均尚未修改。

## 12. 工作树、master同步与agent2验收路线（2026-10-22）

- 唯一开发工作区：`C:/repos/aid-work-agent-wecom`，当前分支`feature/ask-user-question-card`。通用Runner休眠、Runtime与wecom CLI联调在同一分支交付；能力仍保持通用，不将计时器写进wecom插件。设计、计划与进行中索引均迁入该树，不保留master工作区的第二份可编辑副本。
- 保留该树原有wecom本地库未提交修改，关联[wecom本地库计划](plan-wecom-cli-localdb.md)。迁移文档不表示这些旧改动已完成提交或合并。
- Runner功能开工前及后续每个主要阶段交接/agent2验收前，检查分支、upstream与dirty状态，fetch更新远程引用，合入经确认的最新master已提交版本；不混入master工作区里他人的未提交改动，不默认stash全部或覆盖wecom修改。
- 同步前先在原计划登记master来源SHA和本批基线，处理本任务已变更文件的归属；必要时先在获授权的提交检查点保存改动。发生冲突由开发者逐项整合，完成后重跑受影响验证，再交独立测试/CR。本条不自动授权提交/推送，不能以“同步过”为名假报已包含master尚未提交代码。
- 本轮迁移与脚本优化不直接执行整个master合并；根工作区和wecom树均有已有改动，后续同步按上述检查点进行，避免无关改动被混合。
- 发布路线：特性分支完成代码、独立测试/CR → 按用户明确指示提交并推送该分支 → 用户当场指示部署到agent2后，用支持`--branch`的新脚本固定该远程分支SHA发布 → Runtime+wecom CLI真实多会话/多群验收 → 记录确切已验收SHA → 用户授权后整合master，并对最终整合结果重跑受影响验证。
- agent2测试未通过时继续在该树修复；不可为获得发布能力提前把未验收功能合到master。使用agent2更新脚本的具体说明与其隔离测试记录统一见[分支发布计划](plan-agent2-branch-deploy.md)。

## 13. 本轮实施基线与共同检查点

- 已执行`git fetch origin`，远程master及本地master均为`3b713e08251fe1abdfbb0158248138d225ef6cd7`；功能分支原HEAD为`ae31f2b1ee76852e4a15d279eca187f6f8d81b65`。
- 使用`git merge --no-commit --no-ff origin/master`整合，保留MERGE_HEAD，不创建merge提交；工作区/索引已经包含新master，HEAD尚未移动。两份索引冲突按最新master完成状态及wecom较新实现分别处理；恢复原有wecom本地库改动及本任务文档，无丢弃、默认stash或覆盖用户修改。
- 整合后审查基准tree为`a871bc5696cd57ef0f9df6371a09be0b40cb11f1`，用于区分大量master导入和本次Runner代码差异；原发布脚本/未跟踪设计等另外按本任务范围核对，不将master差异计入新功能规模。同步前工作区逐文件副本及binary patch仅保存在本机临时备份，不含提交。
- 高风险分工：休眠/引擎/恢复开发角色、continuous计费/历史开发角色；主控维护Web、契约和集成；开发稳定后由另外的独立测试和只读CodeReview角色验证最终状态。发布脚本既有验证不替代Runner验证。
- API保持`contract_version=1`，加性能力`durable_sleep_v1`/`continuous_execution_v1`；公开continuous及积分字段、timer leaf与原wait控制定位见共同契约2.7。Device/Host/Provider无sleep新wire，Runtime不新增loop；相关两端计划仅登记消费兼容影响。
- 默认能力开关关闭；隔离测试显式启用。不能仅因schema迁移存在就让旧worker同池消费新timer。普通无参数新任务50业务轮的默认值变更与continuous启用分开记录。


## 14. 已实施布局与设计收敛（2026-10-22）

- 工具与调度：`AgentSleepTool`只产生受管云端等待意图；参数seconds为严格整数1～604800、reason可选且≤200字符，单次上限不是任务寿命。`durable_sleep.py`共享版本化树证明，`TimerRepository`用数据库时间/原session claim/两种revision领取；没有Redis双写或进程内多天sleep。
- 恢复：原wait/叶/工具ID完成后由Engine唯一配对；timer_activation区分仅交接与已经开始新动作。物理receipt start同事务设置action_started，save/park/stage/pause ack保留单调标志；interrupted候选及锁内claim再次核验该attempt没有任何新物理receipt。一般interrupted和unknown写仍不自动重放。
- 控制及公平：cancel/finalizing保留优先，queued/controls/timer轮转；同runner已受理resume先于到期。暂停保持deadline，补充输入/继续绑定原timer wait及叶；continuous_guard以实际iteration区分代次，只能明确继续所选叶段，不给下一段赠送旧wait额度。
- 轮次：新submit私有business_round_policy.version=1冻结默认50；旧未装配任务缺标记沿20，旧ExecutionState及显式profile上限保留。实际iteration不回退；合法纯sleep在park事务最多豁免一次，同响应多个合法sleep不重复豁免，混合业务轮计一次。消费sleep才开启continuous下一段。
- 模式与child：普通接单省略execution_mode并维持旧digest/传输；continuous仅Web且需能力开关。固定用途ask_user绑定原表单/叶/wait，肯定结构化答复与reply消费原子启用，普通文本答复不改策略。子继承睡眠及分段策略，但普通委派回答仍可完成返回父；根continuous的一段无工具回答保持活跃。子达到guard、物理准入低余额保存not_started后，可恢复原child，不能变成隐式失败或新建委派。
- 计费：复用唯一record、原冻结价格和实际receipt；feature执行以usage_breakdown.runner_accumulator.version=1累计main分组原始值、保持原分组round，skill按原调用round后滚入基数，增量只读取未应用收据。unknown保持pending，已发生费用照结算；下一收费准入查原余额并在事务尾重新核验lease。累计积分不是任务预算。
- 历史与摘要：原方案“park前必须摘要/裁剪”已收敛为**下一次合法收费dispatch前或根恢复且尚未创建child协程时**压缩，安全park只结算并保存原文，不发起新收费。摘要前重验实际profile授权、原动作fence与余额；无完整摘要不裁剪。保留原文补充规则及按工具/目标隔离的命名cursor/watermark原值，模型每次接收摘要和原文规则；64KiB规则/游标侧存与64KiB摘要是容量边界，超限明确阻断。
- 有界状态：达到96条生成消息后保留最近48条及闭合工具对；model/tools近期窗口32，pending/未应用响应/unknown引用保留。原invocation正面完成证明后才退休本地引用，已闭合child移出并滚入token/工具统计基数；历史消息以runner:history:offset、control_id:user、runner:final稳定身份幂等写原表。前端保留已持久化归档消息，未受理输入不冒充历史。
- 时长与Trace：封存attempt时长滚入累计基数；feature trace使用独立attempt ID、局部spans/用量/时长，费用权威指向稳定record，不能向每个attempt回填任务累计费用造成观测双计；旧非feature trace口径保留。
- 能力与迁移：`AGENT_RUNNER_DURABLE_SLEEP_ENABLED`/agent_runner.durable_sleep_enabled默认false，API/worker同池全部支持后才显式启用；公开capability为durable_sleep_v1/continuous_execution_v1，API major仍1，Device/Host无新增wire。已同步标准与Nuodu初始化及增量DDL，旧schema→增量两次的幂等验证单独登记。
- 兼容边界：恢复使用checkpoint已消费输入及可信workspace/image artifacts，不重新解析已消费原上传ID而把临时TTL变任务TTL；仍需要且已失效的真实资源、配置严格指纹变化、撤权、未知效果保持阻断。没有关闭指纹校验或自动延长授权。

尚不以本轮结果宣称：多天真实soak、原样Linux CLI真机启动/agent2部署、真实企微5群自动回复及Runtime设备公平性业务验收。nested tree在公开child catalog不支持再次委派时仅验证受信导入原树恢复，不虚构模型可发起孙级委派。上述是验收范围边界，不能用加速样本或fixture替代。


## 15. 最终验证与交付记录（2026-10-22）

### 独立测试

| 验证 | 真实结果 | 证据及限制 |
|---|---|---|
| 最终实库回归 | 114 passed / 0 failed / 0 skipped，196.99s | 任务专属本机PG14.2+pgvector0.8.1；每批随机aid_test数据库，未访问agent2/业务库 |
| 最终单元回归 | 303 passed / 1既有环境失败 / 0 skipped | test_artifact_refs.py::test_symlinked_artifact_is_rejected在os.symlink因Windows WinError1314失败；未删除或跳过该检查，不能称全部单元全绿 |
| 追加旧guard回归 | 1 passed | 同一execution下一段拒绝旧wait身份，不额外赠送额度 |
| 升级与初始化 | 旧schema→新增量连续执行两次通过 | 原记录/input digest/旧20轮策略保持；nullable wake及部分index正确，标准/Nuodu DDL一致 |
| 配置与兼容 | 默认false、env显式true通过 | 新普通50/旧已接受20/显式profile保持；RunnerSubmit省略None传输及digest不引入新字段 |
| 真实Worker | 包含在114实库中的10项 | 真实HTTP API、RuntimeFactory、Engine、仓储、授权、计费及本机脚本化HTTP模型；到期后新Worker恢复，提前继续/输入优先、原child与guard、低余额准入、澄清肯定/否定/取消竞态 |
| 容量独测 | 160轮闭合父子树通过 | 未剪16,429,700B>8MiB，最大checkpoint3,612,169B，最终1,969,003B；5目标cursor与原文规则保留、432历史ID唯一、累计token2080/480及含summary分组费用0.31守恒 |

容量样本含320份模拟模型用量及9次fake summary，调用真实裁剪、用量聚合和时长逻辑；历史writer与外部模型是fixture，不冒充真实PG/RPC长时观察。另开发自测2000轮轻量对照超过原8MiB限制，压缩结果<512KiB；两者形态不同，不混用数字。真实PG另验archive/retry/final幂等、control:user稳定身份、unknown迟到补账和分组舍入。

Worker测试仅在测试启动器适配Windows缺少的signal注册及精确自有进程清理，未替换业务执行组件；子guard采用加速计数，子低余额race只在真实start前加确定性测试闸，然后继续真实准入。不能据此声称产品Linux CLI已在agent2启动、Windows CLI原生支持或已经完成50次实体模型/多天运行。

实库覆盖还包括：双独立进程争抢一次timer、领取后失租且未新动作的唯一重领；park前崩溃不自动arm、显式SLEEP_NOT_COMMITTED；未知写兄弟使park原子回滚；5会话timer/取消隔离；pause先于park仍公开/私有business=0且deadline不延期；summary receipt与原子action fence、旧内存false回写仍不能自动桥；两类账本锁等待超过lease时禁止新receipt；child物理准入失败保留not_started、安全暂停、补余额后同child继续。

最终实库命令（使用已隔离的DATABASE_URL，禁止指向业务库）：

```bash
bash scripts/dev_test.sh --isolated-db \
  tests/integration/agent_runner_service/test_durable_sleep_independent.py \
  tests/integration/agent_runner_service/test_durable_sleep_worker_independent.py \
  tests/integration/agent_runner_service/test_durable_sleep.py \
  tests/integration/agent_runner_service/test_continuous_settlement.py \
  tests/integration/agent_runner_service/test_storage.py \
  tests/integration/agent_runner_service/test_recovery_storage.py \
  tests/integration/agent_runner_service/test_control_storage.py \
  tests/integration/agent_runner_service/test_usage_storage.py \
  -p no:cacheprovider -q --tb=short
```

独立测试三文件为上述两个independent集成文件及`tests/unit/services/agent_runner/test_durable_sleep_independent.py`，聚合SHA256为`90bb6305fe006bf80be7d76ed7ba56cd2e740d12a67471276034e6fdf0027d72`；独测45源文件运行前后摘要相同：`209d82890ba6433c997a0d3b45070a46e0bc69040db8ca220dd248cc116d16ed`。测试者另有scope不同的单元/开发回归，不将重叠套件重复相加成唯一覆盖数量。

### 前端及主控检查

- 最终8个定向Vitest文件111项通过：runner API、useAgentRunner/Events、runnerMessages/AskMessages、DurableTaskStatus、MessageItem分享与中断回归。
- `npm run build`通过类型、依赖边界、Vite及Web产物检查；保留既有大chunk/Browserslist提示，不为本功能改打包策略。
- `node web/__tests__/e2e/durable-task-page.mjs`最终通过1280/390px真实Chrome组件检查：键盘继续、积分/pending、暂停不承诺自动deadline、blocked、停止；无业务API/模型/Runtime请求。主控查看截图无横向溢出；此组件fixture不冒充真实登录主页面/agent2验收。
- 关键worker/API/恢复/settlement模块导入、新默认配置、AST/编译及本次diff空白检查通过；无未解决merge冲突。全索引/关联文档439个本地链接检查中32个旧引用/未生成发行产物缺失，对照本轮基准确认新增缺失0，不扩修其他任务。
- 仅关闭本任务创建的WSL临时PG进程，临时随机测试库由入口清理；不操作服务器、Runtime真实会话或其他任务服务。

### 独立CodeReview与源码冻结

独立只读CR最终通过，无未解决P0/P1/P2阻断。已发现并修正guard叶恢复、子低余额保存、物理receipt最终lease fence、摘要前授权/持久动作标记、低余额safepark出口、归档补充输入重复与普通sleep历史显示问题；不把开发者自审替代独立CR。

主控与CR以统一Python算法独立复算57个源码/前端/配置/schema文件，摘要均为`9da714719051f154fd77ac9ac7ee5f668663cec471bd18d048a0d01895dcad0c`。范围为相对第13节基准tree的src/frontend/config及本次三份schema差异和新增src/frontend文件；排除master同步、原有wecom改动、发布脚本、文档和后端测试。路径Python排序、每行`path:lowercase SHA256(file bytes)`，LF连接且末尾无LF后再SHA256。文档状态更新不改变该冻结摘要。

交付为本地已完成开发、待后续agent2/Runtime/wecom业务验收；本轮未提交、推送、部署或合并回master。MERGE_HEAD仍指向第13节已整合master，HEAD仍为原功能分支；这是按用户禁止提交保留的未提交合并，不假报已产生merge commit。


## 16. 整体独立CodeReview复审（2026-10-22）

本轮按用户要求只审查，不修改产品代码，不提交、推送或部署。三个新的独立审查角色分别核对恢复与唤醒竞争、计费事务、长期状态与资源增长，主控交叉核对并复现游标提取及等待身份问题。源码范围为第15节相同57文件，SHA256仍为`9da714719051f154fd77ac9ac7ee5f668663cec471bd18d048a0d01895dcad0c`；未将其他wecom开发或master同步差异混入。第15节测试结果保留为历史验证，上一轮CR通过结论不代表本轮发现已修复。

### 原始发现与当前状态

| 编号 | 优先级 | 发现与触发条件 | 最小修复方向 | 状态 |
|---|---|---|---|---|
| R1 | P1 | `worker.py:454-458,506-518`：timer/control领取成功后，Runtime创建前再次授权/查余额；另一任务耗尽余额或租户条件变化时，普通RunnerError分支stage failed并finalize，长期任务被永久结束。 | 对已有恢复树的准入阻断保留原claim/树/已消费sleep事实；低余额暂停，其他可处理阻断明确核验；不要重新arm已消费timer。 | 已修复，独测及CR通过 |
| R2 | P1 | `transaction_repository.py:107-114`累计写同一record，但`deploy/init-postgres.sql:94-105`的tokens/duration_ms仍为INTEGER；累计达到2^31即SQLSTATE22003，后续active settle、付费准入、终态结算均失败。 | 累计计量字段扩大到BIGINT，同步标准/Nuodu初始化及幂等升级，测试边界和旧数据兼容。 | 已修复，独测及CR通过 |
| R3 | P2 | `continuous_history.py:116-124`裁剪旧ToolFact后，Engine不再识别旧call ID；timer身份无代次，再用同ID会生成旧wait_id，迟到resume可误唤醒新sleep。本地invocation的旧phase binding也会与新调用冲突并阻断。 | 用持久模型轮次区分工具调用和等待身份，保持同一次恢复身份稳定；不要无限保存所有历史ID。 | 已修复，独测及CR通过 |
| R4 | P2 | `continuous_context.py:44-48,69-74`只解析tool message。普通大JSON结果先被Engine截断成非JSON，提取静默跳过；摘要预处理再截前2000字符，可连尾部next_cursor一起丢失，原ToolFact.result虽有精确游标却未消费。 | 在原结果仍可证明归属时提取结构化进度，覆盖裁剪前后；不能依赖展示文本反解析或让有损摘要承担精确游标保留。 | 已修复，独测及CR通过 |
| R5 | P2 | `continuous_history.py:65-68`仅裁三类展示列表，遗漏`presentation.downloadableFiles`及`ExecutionState.images`；连续生成产物时公开快照和checkpoint仍线性增长并触发持久化上限。 | 先保留产物历史/必要业务引用，再缩小当前投影窗口；保持图片与文件可查，不能直接无证明丢弃。 | 已修复，独测及CR通过 |
| R6 | P2 | `continuous_history.py:111-115`删除已闭合child时，retired_subtree未处理其workspace/reply_workspaces/image_artifacts；`worker.py:656-666`终态清理只遍历剩余树，删除的child失去唯一资源登记，文件永久遗留。 | 删除child前完成原owner资源退休，或保留有界清理交接证据并可重试；涉及已归档仍需引用的产物必须先证明生命周期。 | 已修复，独测及CR通过 |

### 本轮新增验证与范围

- R1：真实RunnerWorker.execute、DurableControl.stage配内存仓储，注入CREDIT_BLOCKED及USER_UNAUTHORIZED/TENANT_EXPIRED/SUBSCRIPTION_REQUIRED；均得到failed、finalize_calls=1、runtime_creations=0。无业务数据库或模型调用。
- R2：任务专属本机PG14.2临时表复用现DDL，调用真实settle_record；tokens及duration分别传入2^31，均得到22003。事务已回滚、临时PG已关闭。每10秒16k输入tokens约15.5天达到token上限；duration只累计活跃attempt，阈值约24.86活跃天，不把sleep计入。
- R3：40个后续完成工具后，真实compact_facts删除旧sleep事实；owned_wait_id生成同一ID，ControlRepository.assert_wait接受迟到旧resume。本地工具真实_bind在mock原binding返回时明确抛核验阻断，未证明会静默返回旧业务结果。
- R4：20000字符工具结果含精确next_cursor，真实Engine截断辅助函数后retained_context返回空游标map，真实摘要预处理之后连游标文本也不存在。主控独立复现原ToolFact有游标而提取map为空。
- R5：真实archive_and_compact执行200次、每次追加50条图片与文件引用；最终各10000条仍未退休。snapshot从20轮380595B、80轮1535595B增至200轮3845595B，触发现行2MiB的SNAPSHOT_TOO_LARGE。
- R6：33个完成delegate子树，首child含workspace与image_artifacts；真实compact_facts删除首child及登记，现终态清理无法再遍历其路径。此为资源登记链路验证，不冒充实际磁盘长时soak。
- 计费额外舍入回归2项通过；immutable receipt digest、冻结价格、root锁串行化以及record/扣款/applied同事务未发现新的确定性双扣缺陷。没有重跑整套114项实库回归，不将前轮结果冒充本轮新增验证。

### 架构与下一步

PG权威状态、原Worker调度、原session claim、物理receipt账本及能力开关的方向合理。本轮未发现需要引入Redis双写、第二个调度服务或通用归档平台的理由。问题集中在裁剪与持久身份/资源退休的边界、累计字段容量和恢复准入异常分类，先在现机制内修复。R3应统一解决工具代次，而非各层各补一套历史去重集合；R5与R6分别是展示状态和磁盘资源生命周期，不能互相替代。

当时结论：**整体复审未通过，6项尚未修复**，仅更新本计划和索引，未升级契约。用户随后授权修复，现已完成六项及追加边界修复、独立测试与CR，最新证据见第17节；本节保留原始发现/行号/复现，不能据此推断当前仍有相同缺陷。


## 17. 复审修复与验证（2026-10-22）

用户已授权安排修复智能体处理第16节问题，再独立评审直到通过；仍禁止提交、推送和部署。本轮不混入新master变更或其他wecom任务，保留原HEAD与MERGE_HEAD。高风险流程为一个统一修复角色写代码和定向自测，稳定后另设独立测试和只读CR角色；新增发现继续交原修复角色，避免并行修改同一源码。

| 修复批次 | 内容 | 状态 | 验证记录 |
|---|---|---|---|
| R1/R2 | 恢复准入阻断保留原树、累计计量BIGINT迁移 | ✅ 完成（2026-10-22） | 原claim/消费事实及pause ack保留；实库升级/大累计/幂等结算通过 |
| R3/R4 | 新调用持久身份代次、原始结果游标保留 | ✅ 完成（2026-10-22） | 稳定代次/旧pending/已完成旧结果/截断与摘要边界通过，CR闭环 |
| R5/R6 | 产物历史归档与有界活动投影、child资源退休 | ✅ 完成（2026-10-22） | 全树产物归档、UI100条窗口、幂等与清理失败保owner通过 |
| 独立测试 | 六项回归及相邻恢复/计费/历史链路 | ✅ 完成（2026-10-22） | 最终单元237、实库受影响31通过；同链全批139、前端117及build/Chrome通过 |
| 独立CR | 复核六项、兼容边界与修复引入风险 | ✅ 完成（2026-10-22） | freeze4通过，无未解决P0/P1/P2；原六项及追加阻断全部闭环 |

实施前共同检查点（契约2.8）：公开Runner API major 1、timer leaf、Device/Host/Provider schema不改；snapshot加性可选history_revision为非负单调历史投影水位，新Web以单在途请求获取既有历史且认证/generation隔离、成功才推进水位，旧端忽略、旧服务缺字段不发新请求。另新增云端私有工具身份策略版本，按已持久化模型调用身份为新ToolCall生成稳定namespace，同一次恢复不重命名旧pending或既有invocation。归档产物沿原chat_messages metadata的images/downloadableFiles展示字段，不新增归档服务或资源授权入口；必须验证现有历史消费者。唯一源码写入者为boundary_fixer，主控维护本设计/计划、共同契约和数据库说明；测试/CR只读源码。若实际实现超出此边界，先登记实际变化再验证兼容。


### 第一轮修复冻结与独立验证

- 改前副本与manifest仅存本机任务临时目录；freeze1的14个本轮源码聚合SHA256为`4b9d3df115ff4e43871896d5295b387b0bb0ada19fd77e25cca31b85d30ed772`，独测运行前后一致。它是相对本轮修复前状态的范围，不与第15节57文件总功能hash混用。
- 开发自测：后端定向85通过，追加桥接17通过（与前组重叠14项，不相加）；真实隔离PG24通过/0跳过；前端82通过，Web build通过（保留原Browserslist/chunk提示）。
- 独立测试：真实隔离PG47通过/0失败/0跳过（26.54s，含新增8项）；单元10通过/2失败；前端新增5项通过。真实PG覆盖已消费根/child timer后余额或权限阻断的resume/cancel、旧INTEGER连续迁移两次、超过2^31累计、增量/终态/迟到收据幂等、200轮各10000产物引用归档。独立测试临时库已销毁，helper留供二轮；无业务数据库或部署。
- 两个独立单元失败是修复仍遗漏的确定缺陷：旧read fact因local引用保护留存、新read fact退休后，再裁剪将最新cursor回退；活跃WAITING/PAUSED child未退休，其images没有套用根的归档窗口，200轮仍10000条。
- 独立CR未通过：恢复阻断直接park时可能漏ack并发已受理pause，遗留accepted控制/客户端持续poll；history_revision刷新默认只读最近100原始行且可能读到未成功失效的旧缓存，不能证明已消费全部归档；同ID持久历史需提升runnerHistory标记，不能仅跳过。正常lifecycle后置settle会失效缓存，未把正常路径误报为必然旧缓存，问题是失效异常被吞及覆盖不足。
- 第二轮继续由原修复角色统一写入：R1原子暂停ack/cancel优先，R4有界来源顺序及旧map兼容，R5全树图片窗口/直读PG有界分页/持久标记合并，R6仅rmdir空owner根目录且失败保登记。仍被真实业务/摘要引用的资源继续保留，不能为压容量扩大删除权。


第二轮接口检查点：历史读取继续复用GET sessions/{session_id}/messages，新增可选runner_id、十进制字符串after_id/through_id，单页limit1～100；响应新增has_more/next_cursor/through_id。新模式按原session授权、runner过滤直接PG，固定首请求上界，覆盖control:user/child产物和显示过滤空页；旧无参数入口完全保持。完整版本/唯一写入者/消费兼容已登记共同契约2.8 §5.6，待freeze2实测分页、缓存预热、失败续页、并发新归档及租户/会话隔离。R4新增有界事实来源顺序，旧无来源事实不能覆盖原已保存target游标；活动child元数据套同一归档窗口。


### 第二轮冻结与第三轮收敛

freeze2的18个源码聚合SHA256为`491c70c8a2d32e84741c7cb0f2dc88c437681a0efa1983f1e14a0f06811b3654`，22文件manifest含本批开发测试。开发定向63项、PG17项、前端84项及完整build通过；独立单元220项、前端115项及包含最终fixture的独立build通过。实库完整批次137通过、2失败、4个teardown错误、0跳过，不能记为全绿：两失败为测试脚本runpy加载第二个Worker类导致真实入口gate未命中；四错误为旧Windows清理调用os.killpg缺失。独测角色已修正自有脚本/临时兼容插件，第三轮重跑整组，不修改生产源码或共享conftest。

CR freeze2仍未通过：新增自动历史拉取后浏览器messages永久保留所有归档，真实mapper模拟100/500/1000轮为102/502/1002条，属于新引入的无界增长；applyRunner选择首个assistant会读到归档行而非当前输出；旧pending/旧unapplied response保原ID且无来源顺序，其升级后新完成的结果又被保守legacy分支忽略，游标不能推进。此前已修的原子pause ack、活child图片归档和分页鉴权/固定horizon等未再发现阻断。

第三轮最小修复已登记契约2.8候选：沿原消息区/BaseButton保留最近100条归档窗口及当前任务/输入，更早记录按页替换，返回最新；messages新增before_id可选参数（0取最新，正数取更早；此时forward模式尚未移除，后经下述决议取消），直接PG同原scope过滤，不新增服务或页面。新UI水位表示窗口刷新而非全历史加载；浏览旧页不被live冲走。新完成的旧call在首次result配对时取得持久来源顺序且不重命名；当前assistant按稳定投影ID取。按frontend-design及前端规范实施，需独立行为/build/窄屏可视验证。


第三轮简化决议：前向after/through只是本轮未提交、未发布的临时实现，无外部兼容需求；经CR建议取消双模式，仅保留runner_id/before_id（0最新、正数更早）窗口读取及has_more/next_cursor，默认旧messages入口不变。新UI只刷新100条最新归档窗口，旧页按需替换且可返回最新，不读完所有历史、不累积DOM；完整当前协议已重写共同契约2.8 §5.6和设计10，前两轮描述仅是阶段记录。独立测试按最终业务意图验证，不为临时DTO保留冗余实现。


### 第三轮冻结与最终验证（进行中）

freeze3的24文件manifest（含开发测试）及副本存本机任务临时目录，按排序后的path:sha256聚合为`2b1737671c85c73c74a2d4bbbf5db69861b93486d3a44a9bc02cd77a4b63cc9c`。生产代码停止写入，独立测试和CR核对这一版本。开发自测91项、隔离PG17项、前端85项全部通过，无跳过；完整Web build与diff检查通过。相同结果有交叉覆盖，不跨批次累加为总用例数。

主控对最终状态完成配置加载及7个相关模块import，显式关闭durable sleep、未启动worker或数据库连接池；24文件内容与freeze3逐项匹配。schema核对标准/Nuodu初始化及已有表升级中的8个BIGINT累计字段、原会话ID分页索引一致。独立测试/CR和窄屏可视检查尚在进行，未提前记为验收通过。


freeze3独立验证进展：定向单元230通过/0失败/0跳过（7.55s），前端6套117通过（含8个独立consumer）；真实Chrome 1280/390px验证键盘翻页、loading/error、旧页scroll anchor及返回最新follow通过，主控已查看两张截图，未见窄屏截断。PG全批和最终build仍在进行。

freeze3 CR仍未通过：旧checkpoint中某read的result_recorded=True，且配对tool结果尚在当前未归档messages时，其无order结果仍被视为孤立legacy残fact；已保存cursor9而本段已完成cursor10时，压缩后可能永久只剩9。第三轮首次配对补order仅覆盖not result_recorded，未覆盖此升级保存点。已交原修复角色补最小迁移证明，并由独测追加“当前配对旧结果能推进、孤立受保护旧fact不能回退”回归；生产修改后冻结下一版本再审，不以第三轮先前通过项替代受影响复验。


第四轮实施前收敛：最小修复限于retained_context，使用当前generated消息中完整assistant→tool配对及摘要覆盖边界证明legacy结果尚未归档；值读原ToolFact.result，消息截断不影响。仅无可信continuous_cursor_orders的旧目标允许按本段消息顺序更新，已有可信顺序及孤立fact保护不放宽，不新增永久集合/表或猜测旧iteration。独测已在freeze3真实compact_facts复现红灯；等待实库批次释放后唯一修复角色写入，再冻结复验。


freeze3实库批次已结束：139通过、0失败、0错误、0跳过（273.06s），临时库销毁/自有子进程清理正常，24文件前后hash一致。已修正的真实worker.main入口gate两项及旧历史鉴权四项均通过；Windows仅用任务临时pytest插件精确清理测试拥有的Popen，不改业务清理逻辑或共享conftest。Web完整build及稳定Chrome 1280/390px截图检查通过，无窄屏截断；第四轮只有context修改时复用这些未受影响结果，不机械重复。R4新增独立红灯仍存在于freeze3，因此本批通过不等于整体验收。实库读取释放后，修复角色开始第四轮；后端受影响单元/压缩历史/真实Worker恢复再独立验证。


### 第四轮冻结与终审

freeze4仅相对freeze3修改`continuous_context.py`及开发自有单测，24文件聚合SHA256为`7c7bcf7723af1ad7319de06cbe48467498fb5933c224bd8536e7af11f5bba6c6`。开发最后46项通过/0跳过；主控重查小diff、相关import/配置加载及24项内容hash，未启动worker/数据库池。

独立CR已通过：复核当前assistant→tool完整配对、summary_through-history_offset边界、多目标最新消息顺序及新版positions保护，无新永久集合。真实复现加入initial_len=1/history_offset=12后，旧fact退休和checkpoint restore再压缩均保留新游标；其余R1/R2/R3/R5/R6及有限UI窗/最终before-only API沿freeze3完整审查。没有未解决P0/P1/P2，之前阻断发现已闭环。独立测试正在复验受影响后端链路，未提前将整体状态置完成。


### 本轮最终结果

- 独立测试通过：freeze4定向单元237通过/0失败/0错误/0跳过（7.52s），包括旧已配对9→10推进、孤立旧1不回退及Engine/控制/资源/桥接回归。最终受影响实库31通过/0失败/0错误/0跳过（66.88s），新增真实PG checkpoint往返、24KB原始结果与截断message、摘要/归档、fact退休、保留旧local proof后再压缩用例。准确命令与文件范围见下，未运行无关全项目测试。
- freeze3全实库139通过与freeze4受影响31通过分别记录，不相加；最后小修仅context函数和自测。前端117项、完整build及1280/390px真实Chrome结果对应未变的最终前端/API，已核对后复用；主控查看等待入场动画后的稳定截图，键盘焦点、窄屏及加载失败保留原内容均可用。
- 独立CR通过，无未解决P0/P1/P2；主控核对最终diff、迁移一致性/import/配置和24文件hash。原未提交merge的HEAD/MERGE_HEAD保持，未混入其他wecom改动。临时库和测试自有子进程已清理，55438任务专属PG由主控关闭，未操作业务库/服务器。
- 开发状态为已完成开发，索引移入ideas_finished。未提交、推送或部署；agent2、真实Runtime/wecom及真实多天连续运行尚未验收。数据库历史/计费事实按审计要求持久增长，本轮有界保证针对活动checkpoint/公开投影/浏览器窗口；受真实业务引用保护的资源不为压容量而强删。


### 最终独测命令及环境边界

宿主机Python3.12.6、Git Bash；临时PG14.2+vector端口55438，每批均经`--isolated-db`建立随机隔离库。Windows仅测试清理插件接管`Processes.stop`，验证Popen属于本fixture后terminate/kill并wait；不mock产品恢复、鉴权或数据库。插件SHA256为`783a0959599f633e698e21f8529fa0aae15ad230819411f5a4d59317502973de`，以任务临时PYTHONPATH/PYTEST_PLUGINS加载，未改共享conftest。复现新旧检查点的模型/摘要服务为本机脚本化HTTP/回调，不调用真实企微。以下参数是实际执行范围，139项全批执行后新增了最后legacy PG用例，因此现在重跑同文件集会多收集1项。

freeze3完整实库139项：

```bash
bash scripts/dev_test.sh --isolated-db \
  tests/integration/agent_runner_service/test_durable_sleep_independent.py \
  tests/integration/agent_runner_service/test_durable_sleep_worker_independent.py \
  tests/integration/agent_runner_service/test_durable_sleep.py \
  tests/integration/agent_runner_service/test_continuous_settlement.py \
  tests/integration/agent_runner_service/test_storage.py \
  tests/integration/agent_runner_service/test_recovery_storage.py \
  tests/integration/agent_runner_service/test_control_storage.py \
  tests/integration/agent_runner_service/test_usage_storage.py \
  tests/integration/agent_runner_service/test_durable_sleep_boundaries.py \
  tests/integration/agent_runner_service/test_durable_sleep_boundary_independent.py \
  tests/integration/agent_runner_service/test_web_history_authority.py \
  -p \
  no:cacheprovider \
  -q \
  --tb=short
```

freeze4定向单元237项：

```bash
bash scripts/dev_test.sh --isolated-db \
  tests/unit/test_agent_engine_acceptance.py \
  tests/unit/test_agent_engine_resume_input.py \
  tests/unit/test_execution_usage.py \
  tests/unit/services/agent_runner/test_durable_sleep.py \
  tests/unit/services/agent_runner/test_durable_sleep_independent.py \
  tests/unit/services/agent_runner/test_durable_sleep_boundary_independent.py \
  tests/unit/services/agent_runner/test_durable_sleep_boundaries.py \
  tests/unit/services/agent_runner/test_continuous_lifecycle.py \
  tests/unit/services/agent_runner/test_worker_execution.py \
  tests/unit/services/agent_runner/test_worker_task_isolation.py \
  tests/unit/services/agent_runner/test_control_contracts.py \
  tests/unit/services/agent_runner/test_model_phase_outcome.py \
  tests/unit/services/agent_runner/test_phase3_recovered_resume_consumption.py \
  tests/unit/services/agent_runner/test_phase8_failure_recovery_combination.py \
  tests/unit/services/agent_runner/test_legacy_stream_bridge.py \
  tests/unit/services/agent_runner/test_persistence_limits.py \
  tests/unit/services/agent_runner/test_artifact_refs.py::TestArtifactCleanup \
  -p \
  no:cacheprovider \
  -q \
  --tb=short
```

freeze4受影响实库31项：

```bash
bash scripts/dev_test.sh --isolated-db \
  tests/integration/agent_runner_service/test_continuous_settlement.py \
  tests/integration/agent_runner_service/test_durable_sleep_independent.py \
  tests/integration/agent_runner_service/test_durable_sleep_boundary_independent.py::test_legacy_completed_cursor_survives_database_restore_summary_archive_and_retained_old_local_proof \
  tests/integration/agent_runner_service/test_durable_sleep_boundary_independent.py::test_two_hundred_artifact_rounds_keep_bounded_projection_and_all_archived_references_after_retry \
  tests/integration/agent_runner_service/test_durable_sleep_boundary_independent.py::test_real_worker_postclaim_balance_race_preserves_then_continues_or_cancels_original_task \
  tests/integration/agent_runner_service/test_durable_sleep_worker_independent.py::test_actual_child_sleep_restores_original_subtree_in_fresh_worker \
  -p \
  no:cacheprovider \
  -q \
  --tb=short
```

前端在`frontend/`执行（源码与freeze4一致）：

```bash
npx vitest run web/__tests__/composables/useAgent.durableHistory.independent.test.ts web/__tests__/composables/useAgentRunner.test.ts web/__tests__/composables/useAgentRunnerEvents.test.ts web/__tests__/composables/useAgentMultiSession.test.ts web/__tests__/utils/runnerMessages.test.ts web/__tests__/api/runner.test.ts --silent
npm run build
node web/__tests__/e2e/durable-history.independent-page.mjs
```

Chrome仅本机Vite/headless Chrome，所有/api请求abort。未验证真实多天soak、真实Runtime/wecom联动或agent2部署；仅定向范围，不声称全仓测试均运行。临时PG已由主控`pg_ctl -D /tmp/aid-runner-sleep-pg -m fast stop`成功关闭，其他服务未操作。


### 收尾核验（2026-10-23）

主控最终检查通过：24个冻结文件内容与freeze4逐项一致；相关文档围栏和新增本地链接有效，索引仅将本功能移动至已完成；范围内git diff --check通过，无未解决merge路径，分支、HEAD和原MERGE_HEAD保持。关联Runtime计划中2个旧打包产物链接目标缺失，经修复前树对照确认原已存在，未改动或将其声称为可用。任务临时PG已成功关闭。独立测试与CR结论均为通过；未提交、推送、部署或运行真实企微自动回复。
