# 端侧会话任务开发计划 C0–C5

版本 V1.1 · 2026-09-12 · 待开发。权威契约：[端侧会话任务设计](../../design/desktop-automation/edge-session-task-design.md)，包括 §13 开发澄清。本文是该设计的执行拆分，不改变现有微信 P0–P5 编号。

## 1. 给开发智能体的执行指令

目标：实现 `weixin.conversation.v1`，云端发布有预算/期限/完成标准的任务，Runtime 独立进行观察、就绪调度、等待恢复及受许可单条发送，用户无需让开发助手逐轮操控。先核对本文及设计全文，再读取 AGENTS.md 与相关规则；不是先改 prompt 或复用固定内容任务假装会话 agent。

开发属于调度、权限、数据与计费跨模块高风险，按 [.claude/rules/dev_workflow.md](../../../.claude/rules/dev_workflow.md) 执行：开发自测→独立测试智能体→独立 CodeReview 智能体→主控修复并整合。不可用时明确缺口，不伪称完成。文档交付本身无需三智能体；本计划描述的是后续实现流程。

禁止：自动提交/推送；混入 BOSS CLI 未提交工作；真实发送未经授权消息；放开批量会话许可；绕过既有 v2/证据门禁；新增离线发送；让主 Agent 用 sleep 循环代替本地调度；只靠未读数推进水位；为赶进度将 unknown 当成功。

每阶段完成后在 [ideas.md](../../ideas.md) 更新本项状态/证据。代码开始才标“🔧 部分完成”；C5 门禁未全过不移完成索引。每阶段有明确停止点，不能开发完成就自动进入真机发布。

## 2. 依赖、包边界与顺序

**本次启动决定（A1–A3）**：先做 C0 fake 契约与样本，再走 C1–C4 fake；真机条件/授权未确认，登记 C0 真机 BLOCKED，真实观察和发送 capability 全关。当前开发机、已登录账号及此前“哈尼”群不自动纳入 conversation 测试授权。真机前补齐设备/微信版本/账号/目标会话/对端配合人/时段/内容及发送上限；fake ≥30样本不冒充真机。无需等待 BOSS 提交，保留其文件及索引变更，仅局部更新第78项；同文件实际并发写入时协调时段，不覆盖其他工作。具体边界见设计 §13.1。

现有 P1–P5 是复用底座；它们的“代码验收”不替代微信 P0 或本方案 C0 的 Observer/身份/消息证据验证。C0 测量及契约基线先完成，C1 开始；C2→C3→C4 串行集成；C5 最终验收。C0 真机条件欠缺时可以在登记缺口后继续 C1–C4 fake 开发，但所有真机执行 capability 保持关闭。

| 阶段 | 交付 | 依赖/放行条件 |
|---|---|---|
| C0 基线与观察证据 | 性能分解、身份/覆盖矩阵、冻结 Provider 契约、fake 样本 | 已授权测试环境；无环境仅可产 fake 契约，标真机阻塞 |
| C1 云端任务协议 | 数据/API/任务租约/状态/能力/预算占用 | C0 契约冻结，设计歧义已回填 |
| C2 端侧常驻执行器 | DPAPI日志/观察/公平队列/同步/恢复 | C1 设备协议和 fake server 可运行 |
| C3 决策与逐条执行 | 受限决策 worker/完成判定/底座映射/单条许可/计费 | C2 能独立等待和唤醒，观察批次可对账 |
| C4 工作台及工具 | 配置/发布/状态/接管/结果/工具接入 | C3 fake 多轮端到端通过 |
| C5 综合与灰度 | 故障矩阵、旧链路回归、真机/性能、安装/回滚 | C0 真机门禁+独立测试/CR+全部P0/P1闭环 |

不要提前承诺工期。C0 输出硬件/模型/Provider 现状与模块工作量清单，之后估算 C1–C5；模型延迟和客户等待不能以固定人日承诺消除。

## 3. C0：性能与 Provider 契约基线

读源码：`clients/weixin-cli/src/operations/unreadList.ts`、`drivers/ps1/unread-list.ps1`、`drivers/py/unread_list.py`、现有 read/send/identity 驱动、`clients/agent-tool-runtime/src/pollLoop.ts` 与 v2 执行路径。核对近次 P0 实测报告，不把转述耗时当原始数据。

产出 `docs/research/weixin-cli/edge-session-baseline.md`（实施时创建并登记），内容：

1. 设备/Windows/微信版本/窗口形态、OCR 冷/热启动、工具调用及模型/许可 p50/p95/max；每种路径至少30次，不足注明样本量。
2. 可重放 fake 消息流：相同文本、拆行、未知 sender、视口外、当前打开、人工已读/回复、突发、离线/重启、人工抢焦点。真机样本需匿名化并按受控证据存储。
3. 单聊/群聊分别出具账号/目标身份、连续消息对齐、写后新增气泡证据及 coverage_gap 处理结论；无证据的能力不开启。
4. 冻结设计 §6 的 `session_observer_v1` schema 与错误码；在 Provider 侧形成接口/contract test，不能向服务端承诺完整未读全量。
5. 以相同脚本/模型/fake对方响应重放基线，客户等待单列；后续 C5 用同条件对照。

停止条件：OCR 不可可靠判 sender/连续窗口或身份不唯一，不进入该目标类型的自动发送实测；可继续 fake 执行器开发。

## 4. C1：云端任务、协议与设备归属

**冻结补充（B4–B7、C8–C11）**：严格实现设计 §13，不再自行选择 schema 或部署默认值：

- completion_rule 三个互斥 schema；peer_confirmed 固定字段/枚举、require_all=true，提取放 reply 结果；只有 judged 使用 completion_review。rounds 的发布校验为 target 加可选开场白不超过 max_replies。
- batch_id 为文本，普通值 UUID；保留 opening 合成批次及 decision_kind=opening。decisions 增加 tenant+task 的 opening 部分唯一索引（跨 spec_revision 唯一），保留原五元唯一键和执行映射。opening 不调用模型、不占决策次数，不得在重新发布时重建。
- 新增 session_task_texts 与 session_task_confirmations，字段/约束/API 按 §13.3/13.5；加密调用既有 secret_crypto，缺密钥禁止发布，不能明文降级。截图仅端侧受控存储，本期不做云端截图上传/查看。
- bindings 从 pending、identity_version=0、验证证据/时间为 NULL 开始；生产不可发布 pending。独立测试库/fake Provider 可创建测试 verified fixture，绝不连接真机 Runtime。真机证据 schema 后续按版本扩展，新增字段走迁移。
- cost_units 固定为积分，建任务费用来源唯一关联及预留；任务自身预算耗尽 stopped，租户余额/可预留余额不足 blocked，充值不自动恢复。
- 云端每租户跨进程默认2个模型调用、每任务1个；DB槽位覆盖审核和重试，不能用进程内锁替代。
- publish 必须消费绑定冻结版本/摘要的一次性 confirmation_id；用户确认接口不开放给模型工具或设备身份。消费和发布同事务，重放幂等。

C1 额外验证：三个 schema 的非法组合/枚举、开场白跨版本冲突、pending 绑定拒绝生产发布、跨任务 text_ref 拒绝、密钥缺失/密文损坏、确认伪造/过期/改字段/跨用户/并发重放。完整 SQL/JSON 契约必须包含上述字段后再交接 C2。

文件所有权：新增 `src/session_tasks/`、`src/weixin_conversation/` 的模型/服务/API/注册；更新初始化/增量 DDL、路由入口、配置、表登记；此阶段不改 Provider 真机动作。

必须实现：

- 设计 §4–5、§9–11 的 schema、状态、表、ACL、唯一约束和 CAS；金额/额度沿现有精度，不用浮点货币。
- draft/publish/pause/resume/stop/handoff；发布只创建长期任务和 subject，不同步发送开场白。开场白作为 C3 的单独 execution decision，只允许一次。
- assignment/fence、60s租约/20s续租、绑定占用唯一约束；恢复与旧实例隔离；暂停/控制序号不能乱序恢复任务。
- events 连续前缀 ACK、同 ID 异 payload 409；observations/messages/batch/唤醒记录同事务。
- 设备身份认证与 capability 检查；未通过 observer/v2 等能力的设备不得分配。
- 建模 max_replies/max_decisions/max_cost_units/截止期；预算预留接口，C3 接真实账务。
- 配置默认关闭：`session_tasks.enabled=false`，`weixin_conversation.enabled=false`；enabled/allowlist 新许可门控复用已验证热读模式。调度节奏配置按进程快照，文档明确重启范围。

独立测试重点：同租户非属主/跨租户/跨设备、同会话双发布、两个实例争租约、租约过期、fence改变、同key异payload、乱序 ACK、暂停与续租竞争；迁移在空库/既有库重复运行。所有自动任务采用测试租户并清理。

交接：API schema、错误码、SQL锁序、并发证据、fake server fixture；缺少这些 C2 不自行猜测。

## 5. C2：Runtime 持久循环与常驻观察

文件所有权：新增 Runtime `src/sessionTasks/` 与 tests；Provider 常驻 OCR/observer；复用 credentials DPAPI、OS锁、配置、心跳/网络封装。不要升级最低 Node 版本或引入 native 数据库包作为顺手改造。

必须实现：

- 设计 §8 的单写入者 JSONL 事务记录、DPAPI payload、fsync、连续序号、同步 outbox、启动回放；既有写 journal 独立保留。
- 就绪队列、per-task状态、wait定时器、优先级/公平性、2个决策在飞上限（此时 fake）；一个任务等待不阻塞其他任务。
- 常驻 OCR 服务通过既有 Provider 进程管理生命周期，启动一次多次请求；故障显式 unavailable，可重启但必须重新对齐水位。
- 只读观察的资源锁边界；未验证无干扰前不放开并发。不会在模型/客户等待期间持锁。
- 当前/非当前/视口外会话复查与 observation age；超过覆盖能力限制激活数量并显示原因，不能循环越积越久。
- 连续窗口消息重建、稳定本地ID、批次静默窗口与版本；sender/对齐不明立即阻断。
- 本地磁盘上限、终态ACK后保留期清理、安全路径检查；离线待同步不丢数据，不新增发送。

验收：云端主 Agent 发布后退出，Runtime独立处理A等待→B就绪→A唤醒；kill进程在写日志前/中/后、同步前/后可恢复；一台机器启动第二实例被拒；不得记录明文消息/密钥到普通日志；10次连续相同文本正确重建或显式 gap，不静默丢消息。

C2 只读/假设备验证通过才接 C3 真实执行协议。没有真机授权不发送。

## 6. C3：专用决策、目标裁决及底座执行

补充验收：peer_confirmed 引用发布后 peer 原文、全部字段满足且无矛盾；缺字段不能完成；opening 在新入站/人工回复后取消，重启/重新发布不再生成。实际费用按现有来源只扣一次，已结算+未决预留共同占任务额度；模型调用前余额预检及跨任务原子预留，租户余额不足 blocked，任务预算不足 stopped。双 worker 同租户最多2个在飞（含审核/重试），超时未确认结束不能重叠重试。实现细节以设计 §13.2–13.5 为准。

文件所有权：`src/session_tasks/` 决策 worker/预算/映射、`src/weixin_conversation/` adapter/prompt/render/completion；最小扩展 `src/local_tools/` execution_lane与定向claim；Runtime调用既有v2 executor，不复制发送实现。

必须实现：

- §9 异步决策job：batch幂等、claim/租约、受限输出校验、超时最多一次重试、费用归属。等待decision只进行普通网络查询，不调用模型作轮询。
- rounds/peer_confirmed/judged 三种完成规则；judged 只有完成阶段才额外审核一次。预算/期限终止与达成目标严格分开。
- 新消息或人工回复使旧 decision superseded；消息版本复验分别在准备事务与端侧锁内执行。
- 每reply/opening只生成一组 occurrence/run/delivery/invocation，映射唯一、最终文字不可变；无第二套发送账本。
- execution_lane迁移默认standard；全局通用claim在SQL层排除session_task；新定向claim验证任务设备/fence；旧客户端不抢新lane。
- 单条write-authorize在获得共享桌面锁并复验之后执行；任务实时开关、epoch/fence、预算和目标在权限链中校验。
- 模型费用、完成审核、视觉/执行费用预留/结算、防重复扣费、unknown保留额度。复用模型调用账务，不额外虚构按轮收费。
- journal/outbox恢复、迟到回执、暂停/租约过期禁止新许可但仍接纳旧事实；失败原因映射到任务状态。

必测矩阵：同batch重复事件、两个模型完成竞争、网络重试、生成后客户新消息、排队期间人工回复、旧fence领取、暂停×授权、未知发送×恢复、开场白重启不重复、额度临界双决策、完成审核超预算、轮数不足误报完成、任务A文字不可用于任务B。

独立验证必须经真实测试DB和真实Runtime进程+fake Provider；仅mock service通过不能验收。相同正文新增气泡真机证据仍属于 C0/C5，不把 fake 结果标真机通过。

## 7. C4：主智能体与工作台接入

**2026-09-15：C4 代码及隔离 fake 验证完成**，独立测试与 CodeReview 问题已修复，浏览器桌面/窄屏/键盘检查通过。详见 [C4 验证记录](../../research/weixin-cli/edge-session-c4-validation.md)。C5 与真机门禁仍待完成，页面保持 developing、执行开关未开启。

挂载点固定为现有微信营销模块内的“会话任务”，路由 `weixin-marketing/session-tasks` 及 `session-tasks/:taskId`，完整租户前缀和路由名见设计 §13.5。prepare 返回草稿确认卡片/表单；用户点击一次签发 confirmation_id 并发布，不另问聊天确认。聊天文本及 confirmed=true 不构成发布凭据。测试确认过期、版本变更、工具伪造、用户双击及同版本恢复；真实门禁关闭时只能草稿/查看，fake 发布仅在隔离测试环境。

按 frontend-design 项目规范复用 Base*、语义 token、表单/详情布局；新增页面需要相应 skill，纯API逻辑不启动视觉重设计。

- 实现设计 §11 页面与专用 prepare/publish/manage 工具；普通工具走 Catalog/Assembly，身份取 ToolExecutionContext。
- 任务发布明确展示对象、目标/完成规则、开场白、轮数/决策/费用上限、截止时间；云端主智能体无需保持会话等待结果。
- 列表/详情明确“等客户”“等模型”“等桌面”“同步中”“观察缺口”“发送结果不明”，不一律显示“执行中”。
- 接管/恢复必须有控制版本和水位选择；历史消息不可静默补发。完成证据和实际发送账本可追溯。
- 通知仅变化/完成/失败/需人工，长期等待不刷屏；默认站内，外部渠道另需明确授权。
- 消息正文不落浏览器localStorage；请求取消与异步回填做代际检查，卸载后不重启轮询。

验收：完整配置与授权、完成三模式、预算耗尽/超时与成功不同、离线恢复、版本冲突、窄屏/键盘、控制按钮状态；前端构建/契约测试/界面实测。功能门禁未过时页面 developing，不能提前 published。

## 8. C5：交叉验证、性能与灰度

先跑受影响回归：session_tasks/weixin_conversation、底座permits/claim/results/取消/配额、Runtime旧standard lane及BOSS兼容、固定内容营销。不要每阶段机械跑整个tests/unit；最终入口/配置/DDL变更必须补空库与两进程隔离启动检查，禁止启动生产调度。

验收编号（报告逐项列 PASS/FAIL/BLOCKED，不可省略）：

| 编号 | 判据 |
|---|---|
| A1 任务独立性 | 主 Agent 会话结束/服务器用户SSE断开后 Runtime任务仍推进；云端API/决策暂不可达时安全等待 |
| A2 等待切换 | A等客户时B可推进；A新消息进入就绪队列；没有客户/模型等待占桌面锁 |
| A3 持久恢复 | 每个边界崩溃/重启；无已ACK消息丢失，无未知发送重发，无旧控制复活任务 |
| A4 消息正确性 | §6全部覆盖矩阵，缺口明确阻断；不凭徽章/正文hash保证全量 |
| A5 权限与并发 | 跨租户/设备/同会话多实例/旧fence/暂停授权竞争均拒绝；无死锁 |
| A6 目标与预算 | 三种完成模式、硬上限、超时/转人工/unknown原因正确；模型不能改目标或扩大授权 |
| A7 费用与去重 | 相同事件/决策/回执重放不重复扣费/发送；供应商实际重试费用照实、计入上限 |
| A8 兼容 | 固定内容场景不变；BOSS旧链、Runtime standard lane、v2失败不降级 |
| A9 性能 | 同C0条件≥30样本；编排开销下降≥30%，等待时间剔除；无LLM轮询、无逐次OCR冷启、正确性不退化；未达到不得靠取消授权校验达标 |
| A10 真机 | 授权单聊/群聊分别测；锁屏/断线/焦点抢占/身份漂移/相同文字新增气泡/人工接管；没验证的类型不开 |
| A11 发布/回滚 | 实际安装包在干净Windows用户环境运行；常驻OCR依赖随包可定位，不依赖仓库venv；关闭新场景免新许可、迟到回执照实、其他场景不受影响 |

灰度开关分通用执行器和微信场景，初始均false；限制单设备、单个授权会话，再扩到最多5个活跃任务，依据覆盖时效实测决定扩容。自动扩租户/设备不在V1。

报告产出（实施时创建并登记）：`docs/research/weixin-cli/edge-session-acceptance.md` 与 `docs/ops/edge-session-rollout.md`；包含命令/版本/样本数/分位耗时/账务/问题/独立角色结论/未验证范围，不含凭据与客户原文。发布运维文档操作均限制 tenant+scenario，不复用无场景过滤的批量SQL。

## 9. 完成定义与交接模板

每阶段交接包含：提交前工作区状态（不自动提交）、本任务文件清单、设计条款对应实现、DB/协议变更、测试命令与真实结果、独立测试/CR发现及修复、未通过门禁、下一阶段输入。不能把新增能力默认开启当作验收手段。

最终分开报告：①代码/fake验收；②指定设备/会话类型真机验收；③性能与预算测量；④允许灰度的范围。C5或P0仍阻塞时标“部分完成”，不声称无人值守全量可用。

后续BOSS接入另开工作包：复用本协议/Runtime循环，保留原BOSS场景的受限话术与P0′，更新其计划中的执行位置说明；不自动接管当前工作区BOSS探索，也不将本轮授权延伸到候选人。
