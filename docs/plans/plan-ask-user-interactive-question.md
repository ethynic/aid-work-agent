# 结构化提问交互（ask_user 提问卡片）实施计划

> 创建：2026-10-14；修订：2026-10-14（v2 接线责任与验证分工；v3 执行口径澄清：幂等用例按层分配、WP0 结果不复用、Phase 2 验证角色按风险定）
> 状态：Phase 1/2/3 已完成开发；Phase 4 按 2026-10-23 用户最新要求完成旧版快捷选项整体退役，独立测试与复审通过；既有真机验收及 expires_at 口径不变。
> 设计文档：[design-ask-user-interactive-question.md](../system/design-ask-user-interactive-question.md)
> ideas 条目：20261013-1434

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 协议 + 权威校验 + Web 提问卡片 + 渠道文字全渠道 | ✅ 开发完成（2026-10-14） | WP0-WP5 全部完成、三角色两轮流程走完（commit 56105660/d36f9ea3 + 本轮提交）。WP0 root 等待 spike 通过（§11.1）；WP1 契约/入口校验/摘要兼容；WP2 判定表受理/canonical 权威生成/422 错误链路/BFF 条件透传；WP3 ask_user 正式工具 + spike 收敛（root/SUBAGENT/legacy 行为矩阵内核级用例锁定）；WP4 Web QuestionCard（§5.7 状态机/绑定原 wait/capabilities 降级）；WP5 渠道文字五端（字节分段/预算核算/入站单管线/发送事实）。独立测试修复 3 缺陷：claim_resume 重建阻塞、kf 截断后重发承诺矛盾、多位数题号误解析；CR 修复 2 个 P1：无 form 等待结构化提交静默受理、多位数题号消费 wait。单元 3401 过（7 先存环境）+ 集成 33 过 + 契约 85 fixture + 前端 ask 37/全量 639 过（8 先存）。后续修正已完成代码复审；真机渠道验收待授权 |
| Phase 2 | 同源待答可达性（提问通知 + 会话待答标识） | ✅ 开发完成（2026-10-14） | `task.waiting_input` 通知生产者挂 park 事务尾（chat/web 用户自有 runner 且 waiting(form) 才通知；event_key=runner:wait 去重，同 wait 重复 park 不重发）；`resource_in_tx` 增 `runner_waiting` 分支（点击解析回原会话带 `_load_session`）；站内信 INBOX 纳入该事件（渲染+跳转链接）；SW 白名单/文案更新；会话列表 `waiting_input` 徽标（查询异常降级无徽标，列表缓存 TTL 30s 内）。验证：notifications 单元 138 过（含 9 例 waiting 生产者新用例；web_push 8 例为先存环境）、后端 3417+集成 33、前端 642 与基线一致。主控单角色自验（子智能体额度冻结期）；后续修正已完成代码复审；真机验收待授权。埋点以最新 Phase 3 记录为准 |
| Phase 3 | 增强项（按需） | ✅ 开发完成（2026-10-14） | legacy SSE form 透传（clarification 帧携带 form，旧页面作答方式不变）；生命周期埋点 MVP（[ask] produced 与事务提交后的 consumed；结构化及文字作答均计入，skipped 来自 payload.skip；recover_ms 表示控制受理至消费完成的时长，不是用户作答时长）；capabilities 已由 Phase 1 ask_structured_answers 覆盖（无多维度协商需求，无遗留）；expires_at 超时语义**暂缓**——需先冻结监管/取消/计费产品口径（设计 §5.5），未实现任何自动超时 |
| Phase 4 | 旧版快捷选项整体退役，统一使用 ask_user | ✅ 完成（2026-10-23） | 自测后端58+静态8/前端73；独立后端84/前端82通过；Web构建与CR通过，待部署及真机验收，见§8 |

合并验证（2026-10-14）：功能分支 `89f60ccb` 与 master `a805eb24` 整合；保留双方数据库迁移与网络搜索任务索引。Web 构建通过，独立前端定向回归 35 项通过，契约检查 7 项及 85 个 fixture 通过，42 个 Python 文件语法与 4 个关键模块导入通过；当前环境缺少 pytest，后端完整测试未重跑，未执行部署或真实渠道验收。

## 1. 范围边界（评审固化，实施中不得扩张）

**做**：设计文档 §4–§8 定义的协议、校验、渲染与历史展示；§9 Phase 1/2 验收清单。

**坚持**（各轮评审固化）：普通文字兼容、不新增用户确认步骤、不恢复校验失败落库与拒绝回放、不新增自动补发/纠错次数/旧表单续答状态、不把文案建议变成硬门、不为展示维护第二套权威生命周期；每个新增状态、查询或校验必须说明具体必要性；跨入口作答与渠道原生卡片为未来机会（另立任务）。

## 2. 现状锚点（实施涉及的关键代码）

| 区域 | 文件 | 涉及改动 |
|---|---|---|
| 控制契约 | `src/services/agent_runner/control_contracts.py` | 新增 `AnswerItem`/`answers`/`skip`；入口校验修订；`intent()` 显式条件序列化 |
| 控制受理 | `src/services/agent_runner/control_repository.py` | 归一化 + 判定表校验（422 不落库）；受理 payload（canonical 入既有 `answer` 键 + form 快照）；supplementalInputs 投影扩展 |
| **Runner API 错误处理** | `src/services/agent_runner/api.py` | 422 `errors`（question_id/code/message）**原样返回**，不并入通用错误报文 |
| **内部客户端** | `src/services/agent_runner/web_client.py`（RunnerServiceClient） | 错误结构透传，不降级为字符串 |
| **Web BFF** | `src/api/agent_runner_web.py` | `errors` 透传前端 |
| **前端错误模型** | `frontend/web/api/runner.ts`（RunnerRequestError） | 保留 `errors` 结构供逐题高亮 |
| 恢复 | `src/services/agent_runner/recovery.py` | `_apply_reply` 读 payload['answer'] 回注（canonical 落既有键，验证接线） |
| wait 装配 | `src/services/agent_runner/durable_control.py` | decorate_wait 复用；clarificationQuestions 带表单元数据 |
| 公共视图 | `src/services/agent_runner/public_view.py` | `form` 白名单、errors DTO（HTTP 响应）、supplementalInputs 扩展投影 |
| 控制分支 | `src/services/agent_runner/runtime/controls.py:75-84` | ask_user 分支 + root 执行等待（现仅 SUBAGENT） |
| 工具 | `src/tools/control_set.py` + 新建 `src/tools/agent/ask_user_tool.py` | 控制工具装配、服务端 id 生成、硬校验、usage guide |
| 能力发现 | `src/services/agent_runner/api.py` capabilities + `src/api/agent_runner_web.py` | `ask_structured_answers` 字段 |
| **渠道入站** | `src/channels/runner_agent.py:86-191` | waiting.form 渲染（返回分段结构，不预判送达）；入站单管线 |
| **会话发送封装** | `src/channels/session.py`（`make_send_response`/`send_response`/`process_and_persist`） | **分段信息经 UnifiedResponse 传到 adapter 逐段发送；发送事实在实际发送确认处记录** |
| 渠道共享渲染 | 新建 `src/channels/ask_render.py` | 按字节分段（逻辑行优先、单行字符边界跨消息、续段题号）、wecom_kf 预算核算 |
| wecom_kf | `src/channels/wecom_kf/renderer.py`、`budget.py` | **提问分段绕过长图合成**；预算用当前入站 owner 对象 |
| 五渠道 | `src/channels/{wecom,dingtalk,feishu,wecom_kf,wecom_personal_rpa}/` | 各自发送类型接入 |
| 契约登记 | `contracts/runtime-host/v1/` | ask-form / errors DTO schema + fixture（幂等四例），可并入既有 schema 文件结构 |
| Web 前端 | `frontend/web/api/runner.ts`、`composables/useAgent.ts`、`utils/runnerMessages.ts`、`components/MessageItem.vue`、新建 `components/chat/QuestionCard.vue` | 类型、提交/重试/换键、**卡片绑定原 wait**、历史只读投影、提问卡片、状态机、capabilities 缓存降级 |
| 通知（Phase 2） | `src/services/agent_runner/event_repository.py`（事务尾登记模式）、`src/background_runner.py` | `runner_waiting_input` 生产者，以原 wait 标识去重 |

## 3. Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道

> 风险评级：高。**三角色（开发/独立测试/独立 CR）一轮贯穿 Phase 1**，不为每个 WP 重建完整流程；CR 重点为 WP2/WP3 的受理与恢复回归。

**验证分层总则**（各 WP 只验本层，WP6 只补组合项）：

- WP0/WP3：等待生命周期——真实受理、恢复、幂等命中（数据库层验证，schema fixture 不能替代）；
- WP1：模型层——schema、入口模型、旧摘要兼容（fixture）；
- WP2：受理/恢复层——判定表、幂等、canonical 回注、旧 clarify 回归；
- WP4/WP5：用户交互与实际发送链路；
- WP6：跨模块组合与未覆盖项；**复用同一最终代码状态的有效结果，不机械重跑各 WP 用例**。

### WP0 root 等待最小验证（先行 spike，最先执行；WP1 可并行启动）

- 目的：验证设计前提——root 执行产生 clarification wait 后与 final_answer 流、`task_record`、cancel/pause、防滥用拦截点的交互是否成立。
- 最小实现：`controls.py` 临时放开 root 分支 + 现有 `clarify` 工具即可，**不依赖 WP1/WP2**；用例与结论直接复用于 WP3，不另建临时测试框架。
- 产出：结论回写设计 §11.1；若前提不成立，先回设计评审再继续。
- 注：WP0 的临时改动由 WP3 的正式实现替代收敛，不直接遗留；**WP0 的通过结果不直接复用到正式实现**——正式 ask_user 替换临时 root clarify 分支后，**重跑受影响的等待/恢复用例**（只重跑受影响部分，不扩大为全量回归）。

### WP1 契约与 schema（共享契约唯一写入者）

1. 契约清单：`AnswerItem`/`answers`/`skip`（`control_contracts.py` 入口模型修订）；`contracts/runtime-host/v1/` 下 ask-form 与 errors DTO schema（**可并入既有 schema 文件结构，不强制分别建文件**）+ 正反 fixture + **摘要兼容 fixture**（升级前后 digest 逐字节一致、缺省序列化不变）。**幂等用例按层分配**：WP1 只验请求形状/序列化/摘要兼容；"同键命中已受理控制""已受理后同键改答案拒绝"由 WP2 真实数据库链路验证，不重复实现。
2. 入口校验修订：reply 合法条件扩展、`answers`×非空 answer 互斥、`skip`×`answers` 互斥、非 reply 禁带、旧 clarify 空回复仍 `CONTROL_REPLY_REQUIRED`；`intent()` 对新字段"未提供即不含键"（禁全局 exclude）。
3. **错误映射**：入口模型校验错误**不透传含原始输入的通用报文**，统一映射为定义错误码（422）。
4. capabilities 增加 `ask_structured_answers`。

验收（模型层）：严格解析器过全部 fixture；摘要兼容 fixture 通过；旧形状请求 digest 逐字节不变（受理类幂等用例归 WP2）。

**唯一写入者**：`contracts/runtime-host/v1/*` 与 `control_contracts.py` 由本 WP 唯一写入，后续 WP 只消费不修改。

### WP2 Runner 受理、错误链路与恢复

1. 归一化（qid 小写、去空白、selected_keys 去重）→ 判定表校验（设计 §5.4）→ 失败同步 422 + `errors`（不创建 control、不消费 wait）。
2. **错误传输链路（P1-1）**：422 的 `errors`（question_id/code/message）从 Runner API → RunnerServiceClient → Web BFF → 前端 RunnerRequestError **完整保留**，任何一层不得降级为通用错误字符串；校验错误**只在 HTTP 响应中返回**，不写快照、不写 control。
3. **执行载荷接线（P1-1）**：服务端生成的 canonical 必须写入**既有 `payload['answer']`**（`_apply_reply` 读取该键），同一文本用于 `supplementalInputs.text` 与 tool result；canonical/form 快照附加字段**不替代**既有 `answer` 键，避免漏掉恢复接线。
4. 受理 payload 附 form 快照；supplementalInputs 投影扩展（canonical/control_id/答案摘要/form 快照）；`public_view.py` 白名单（`form`、supplementalInputs 扩展）；`durable_control.py` clarificationQuestions 带表单元数据。
5. `recovery.py`：验证 canonical 经既有 `answer` 键回注为 tool result 的完整链路（预期小改或不改；差异记录并最小修改）。

验收（受理/恢复层，数据库真实链路）：**一条合法结构化答案实际恢复并进入工具结果**；**一条错误题的 `errors` 贯穿 API→client→BFF→前端**；判定表全组合；**同键重试命中已受理控制、已受理后同键改答案 `IDEMPOTENCY_INPUT_MISMATCH`**（自 WP1 分配至本层的两个受理幂等用例）；旧 clarify 全路径回归。

### WP3 ask_user 工具与 root 等待完整实现

1. 基于 WP0 结论实现完整 root 等待（`controls.py` 正式分支，wait 载荷附 `form`），**复用 WP0 用例**，不重建。
2. 新建 `ask_user_tool.py`（catalog=False）：InputModel 仅内容字段；`execute` 生成 form_id/question_id/key + 硬校验（题型/选项匹配、required/max_select、总载荷 8KB）；`control_set.py` 装配；usage guide（何时提问、题型声明、文案长度建议口径）。
3. `clarify_tool.py` / `present_options_tool.py`：本阶段保留，usage guide 增加三工具分工；2026-10-23 后者按 Phase 4 退役，仅保留已固定旧调用执行兼容。

验收（与 WP2 合并）：等待生命周期全链路——受理→恢复→工具结果→续跑；连续两轮 ask；等待中 cancel/pause；子执行既有 clarify 路径不回归。

**唯一写入者**：`runtime/controls.py`、`ask_user_tool.py`、`control_set.py` 归 WP3（WP0 临时改动一并收敛）。

### WP4 Web 前端（含桌面兼容）

1. **卡片绑定原 wait（P1-3）**：QuestionCard 渲染时记录**自己的** `runner_id`/`wait_id`/`target_execution_id`；提交与重试始终携带这组值与原始答案不变；**不使用"当前任务视图最新 wait"的选择逻辑**；过期提交由服务端既有校验拒绝（`CONTROL_WAIT_STALE` 等）后刷新，不自动改投最新问题。
2. 提交链路：结构化提交（`answers`/`skip`）；网络不明原键重试；已受理后修改换新键；校验失败直接重提（无专门键管理）；422 `errors` 逐题高亮（依赖 WP2 错误链路）。
3. `QuestionCard.vue`：题型渲染、`allow_custom_text` 自由输入行（并存不互斥）、required 预检、忽略/提交（`allow_skip=false` 无忽略）、多题分页、§5.7 状态机。
4. 历史投影（`runnerMessages.ts`）：已答只读卡片来自 supplementalInputs 扩展投影；**未答取消态来自既有问题历史（clarificationQuestions 投影的问题文本）+ runner 终态标注**——未回答任务没有 supplementalInputs，不从答案投影生成。
5. capabilities 会话启动读取一次并缓存；字段缺失 → 客户端 canonical 文本路径（灰度/回滚窗口专用）。

验收（交互层）：**另一标签页先答完、Runner 已产生下一轮问题时，旧卡片提交不消费新 wait**；提交/跳过/双端冲突（409 细分）/刷新与上下文重建/历史回显；旧快照（无 form）渲染不回归。

**唯一写入者**：`runner.ts`、`useAgent.ts`、`runnerMessages.ts`、`QuestionCard.vue` 归 WP4。

### WP5 渠道层（五渠道文字问答）

1. **渲染到发送的接线责任（P1-2，实施后口径）**：`runner_agent.py` 渲染提问结构后**不自行承诺送达**——经**既有会话回复封装**（`session.py` 的 `make_send_response` 构造 UnifiedResponse）传到 adapter 发送；**发送事实在实际发送确认处记录**。wecom/dingtalk/feishu 按字节分段逐段发送；kf/RPA 一表单合并为**单条消息**（分段渠道的逐段事实仅适用前者），kf 精简后仍超单条硬限时生成 Markdown 文件（提问确认单.md，临时文件入租户 temp 目录即删）以 file 消息完整发送——**不截断正文冒充完整发送**。
2. **预算接线**：wecom_kf 核算使用**当前入站 owner 的 `WeComKfReplyBudget` 对象**（单条方案 N=1，提问轮次不为 final 预留——已核实 `reserve_for_final` 仅存在于 verbose 路径，结论回写设计 §11.2）。
3. `ask_render.py` 渲染规则（**仅适用于分段渠道 wecom/dingtalk/feishu**；kf/RPA 走 `render_single_message` 单条合并）：按实际字节、逻辑行优先、单行字符边界跨消息、续段保留题号/选项号、description 优先省略。kf 精简后仍超单条硬限时由 session 侧生成 Markdown 文件（提问确认单.md，临时文件入租户 temp 目录即删，已登记 file_usage.md）以 file 消息完整发送。
4. 入站单管线（所有渠道一致，不分投递状态）：隐藏命令 → 精确"跳过"（skip=true）→ Q 编号解析（三分法 + 归一化，协议错误纠正一次）→ 精确"重发/简化"（按发送事实记录续传缺段）→ **其余一律沿现有 reply 路径**。
5. 受理/应用边界（设计 v6 口径）：受理后暂停旧表单补发；control `consumed` 且快照无 wait → 清理剩余发送记录；被拒且 wait 有效 → 可修正重发。读既有 control 状态 + waiting 快照，无新状态机。
6. **不新增**渠道队列、独立投递服务或第二套 outbox。

验收（实际发送链路）：**多段提问实际按文本逐段发送（wecom/dingtalk/feishu）**；**kf/RPA 一表单单条到达，kf 超硬限走 Markdown 文件完整发送**；**中途失败只记录已确认段（分段渠道）/单条失败事实（kf/RPA），重发分别续传缺段或整条重发**；wecom_kf 预算核算端到端；五渠道各一例端到端。

**唯一写入者**：`ask_render.py`、`runner_agent.py` 改动、`session.py` 发送封装接线、wecom_kf renderer/budget 接线归 WP5。

### WP6 集成、发布与验收

1. **跨模块组合与未覆盖项**：前后端发布顺序（后端全实例就绪 → 前端 feature flag）、回滚注意（保留 ask 受理能力或先取消在途 ask）写入部署备忘；按设计 §9 Phase 1 验收清单逐项确认。**复用同一最终代码状态的有效结果，不机械重跑各 WP 用例**。
2. 文档收尾：更新设计 §11 开放问题结论、本计划进度区、ideas 索引。

### WP6 部署注意（开发完成时登记）

- **发布顺序**：后端（Runner API + Web BFF 同部署单元）全实例健康确认后，再放开前端结构化提交（feature flag）；滚动期以"capabilities 未确认支持 = 文本路径"为基准，不做逐次探测。
- **回滚**：回滚目标版本需保留 ask 受理能力（处理在途 form wait），或回滚前取消在途 ask 任务。
- **遗留 P2（登记不阻断，后续顺手收敛）**：capabilities 静态标志在前后端混合版本窗口不实；channel_sessions.metadata 读改写无跨进程锁（单会话串行兜底，模块已声明取舍）；"Q1…" 开头的自然语言会被当作 q1 文字答案（协议模式的显式取舍）。二轮复审已收敛：重发计数双记、结构化提交丢附件、旧 form 发送事实残留、text 题数字误拒、kf/RPA 合并为单条发送。
- **测试装置已知问题**：`test_channel_config_update_wecom_kf` 导入时 pop `DATABASE_URL`，与其他集成文件同批混跑互相干扰——集成测试必须逐文件跑。

## 4. Phase 2：同源待答可达性（验证角色按实际风险确定）

1. `runner_waiting_input` 通知生产者：**沿用现有通知生产机制**（runner park 事务尾登记），**以原 wait 标识去重**——同一 wait 的重复 park 不重复生产通知；**不新增频控子系统**（跨任务频控如后续确有需要，按设计 §7.3 另立）。
2. 站内信兜底 + Web Push（已开启浏览器通知的用户）；点击回跳原会话（复用既有点击解析）。
3. 会话列表「待回答」标识：Web 同源任务的 runner waiting 查询（轻量端点或并入会话列表接口，实施时定）。

**验证安排**：若实现仅为复用通知机制与列表展示，按仓库常规流程由**一名独立验证者**承担测试与审查；若涉及高风险事务、授权或恢复行为，再保留独立测试与独立 CR。

## 5. Phase 3：增强项（开发完成 2026-10-14，含两项按计划暂缓）

- **legacy SSE form 透传（已完成）**：clarification 帧/child_clarification wait 携带 form（旧页面无卡片渲染能力，仅数据透传，作答方式不变）；桥帧有单测锁定向后兼容（无 form 无键）。
- **埋点 MVP（已完成）**：`[ask] produced`（runtime/controls.py 生产点）与 `[ask] consumed wait_id/skipped/recover_ms`（recovery_repository.py 消费事务提交后）结构化日志；`recover_ms` 表示控制受理至消费完成的时长。当前不直接输出用户作答时长，未新增表或上报通道。
- **capabilities 协商（无遗留）**：Phase 1 已交付 `ask_structured_answers` 能力字段 + 前端缓存降级；无多维度协商需求。
- **expires_at 超时语义（暂缓）**：需先冻结监管/取消/计费产品口径（如超时自动跳过或转取消、时限默认值），属产品决策——设计 §5.5 明确不引入未冻结口径的自动超时；字段与解析已预留，口径定后实施。


## 6. 先行验证与风险清单

| # | 事项 | 时点 | 回写位置 |
|---|---|---|---|
| 1 | root 等待语义 spike | **WP0（最先执行）** | 设计 §11.1 |
| 2 | wecom_kf 预算与 `reserve_for_final` 现状核对 | WP5 开工前 | 设计 §11.2 |
| 3 | 恢复链路对撤权/设备/插件事实的重验覆盖面核对 | WP2 期间 | 设计 §11.3 |
| 4 | 飞书/钉钉分段配置的官方字节上限核对 | WP5 期间 | 设计 §2.3 |
| 5 | 旧快照（无 form）严格解析器兼容 fixture | WP1 | fixture 随 schema 登记 |

## 7. 验证与流程约定

- **三角色一轮贯穿**：Phase 1 开发、独立测试、独立 CR 各一轮（CR 重点 WP2/WP3 受理与恢复回归），不为每个 WP 重建流程——指**角色与交接不重复搭建**，代码修复后仍须重跑受影响用例；Phase 2 验证角色按实际风险确定（§4）。
- **验证分层**：模型层（WP1）/ 受理恢复层（WP0、WP2、WP3）/ 交互与发送链路（WP4、WP5）/ 跨模块组合（WP6）；schema fixture 不替代数据库幂等受理验证；WP6 复用同一最终代码状态的有效结果，不机械重跑。
- 聚焦实际改动（评审口径）：正常提交、跳过、幂等、必要非法输入、长行分段、发送失败、历史恢复；无实际故障或业务依据不增加检查项。
- 每阶段完成：先更新本计划「开发进度」区，再同步 `docs/ideas.md` 索引行（规范见 AGENTS.md）。
- 部署由用户当场指示，助手只准备命令与检查清单。

## 8. 2026-10-23：旧版快捷选项整体退役

关联索引：20261023-1624；设计见[设计§12](../system/design-ask-user-interactive-question.md#12-旧版快捷选项整体退役2026-10-23)。用户已授权提交代码；新范围完成验证后提交并推送，不部署。

### 8.1 已核实事实

- agent2 trace：`tr_runner_fad11fadee904df98a5851c0a5bc4880`；会话：`session_3743bc53e26f`；时间：2026-10-23 15:29:49。`boss_jobs_list` 成功后调用 `ask_user` 进入 waiting；模型没有生成正文，三个岗位标签来自旧 `quickOptions` 展示链路。
- 旧链路从任意工具的 `data.options` 自动生成按钮，经 Runner 快照、历史消息投影、Web 映射及 legacy SSE 重建，最终由 `MessageItem` 渲染。只改一个来源或隐藏单条消息不能完成退役。
- 初版按工具来源过滤并保留 `present_options` 的实现已被用户否定；初版自测与独立验证结果仅适用于旧范围，不能作为本次新范围的通过证据。

### 8.2 最新范围（2026-10-23 用户明确修订）

**整套旧版快捷选项功能停用，统一使用 `ask_user`；不按工具名特判，不再保留任何工具的旧按钮。** 此要求覆盖此前“保留 present_options 显式按钮”的范围。

1. 删除 `presentation.py` 从工具结果提升 `quickOptions` 的分支；Runner 公共视图与历史消息投影忽略该旧字段，不清理数据库或修改模型使用的原始工具结果。
2. 删除 Web 的 `quickOptions` 类型、工具结果提取、Runner/历史映射、legacy SSE 挂载、消息组件渲染及数字发送事件链。旧服务端、旧快照、内存缓存及来源缺失的历史记录均不再恢复按钮；`data.options` 仍可作为普通工具进度事实存在。
3. `present_options` 不再出现在新模型调用的定义/指南中；`ask_user`、`clarify` 的指南不再推荐旧按钮，无需作答的建议写正文。旧工具实现仅保留已固定 checkpoint 调用的执行兼容，不自动把非阻塞调用改写成阻塞 wait。
4. 运行时 `spec-to-quotation-list` 的两个业务确认门改用 `ask_user`，保留自定义输入与等待实际作答的语义。
5. `ask_user` 卡片、分页、自定义输入、提交/忽略及历史已答回显继续沿用，不改变控制、权限、计费或共享 Device/wire 契约。输入框和空态快捷提示不属于本次旧编号选项，不删除。

### 8.3 验收

| 场景 | 通过标准 |
|---|---|
| 任意工具结果 | 数据与进度保留，不创建旧快捷按钮，不依赖工具名称 |
| 实时、历史、刷新和缓存 | 即使存在旧 quickOptions 或工具 options，映射和实际组件也不再展示旧标签或发送数字 |
| ask_user | 当前提问卡片可提交/忽略；已答与未答历史继续正常投影 |
| 工具装配与引用 | 新调用只推荐 ask_user/clarify；旧 present_options 不暴露给模型，已固定旧调用仍可返回原结果 |

### 8.4 实施与验证

- 风险：高（涉及核心工具装配）。主控开发与自测，独立测试及独立 CodeReview 分别验证，复用不受新范围影响的环境信息。
- 已删除旧投影/按钮链路与按工具名过滤方案，完成旧工具曝光下线及运行时 skill/子智能体两个确认门迁移。`present_options` 类仅保留已有 checkpoint 的执行返回兼容。
- 主控自测：Git Bash 宿主 Python 3.12/pytest 9 经 `scripts/dev_test.sh` 运行 `test_retired_quick_options`、`test_ask_form_projection_wp2`、`test_root_clarify_wait_wp0`、`test_runner_message_projection`、`test_tool_control_set`、`test_ask_user_tool`、`test_present_options_tool`，58 passed；另 `test_tool_execution_cleanup.py` 8 passed。前端 `npm test --` 对 Runner/ask 映射、QuestionCard、退役按钮/中断组件、useAgentAsk、多会话 SSE、通知详情共8文件，73 passed。
- 独立测试：后端上述相关模块加 `test_legacy_stream_bridge`、`test_subagent_protocol`、`test_subagent_assets` 共9文件76 passed，相邻 `test_tool_assembly.py` 8 passed，合计84 passed；前端10文件80 passed，另实际 `MessageItem + QuestionCard` 组合探针2 passed（提交包含原 runner/wait/execution 及结构化答案；忽略仍传 skip:true；缓存旧字段无按钮/数字发送），合计82 passed，无失败或跳过，临时探针已清理。
- `cd frontend; npm run build` 最终通过（类型检查、依赖边界273源文件、Vite、Web产物1135模块）。首次构建发现旧快照测试直接传已删除类型字段，改用旧数据扩展fixture后通过；现有 Pydantic 弃用、Browserslist 和大 chunk 警告不阻断。
- 独立CR已复核无阻断。发现的旧工具静态测试断言、运行时 skill Changelog 和建筑供应链子智能体旧提示均已修正；全仓 `subagents`/`src/skills` 无旧工具推荐，前端无旧消费链。`git diff --check` 通过。
- 提交前已 fetch 并快进同步远程 3 个提交；仅对重叠的 ideas.md 单文件临时保存并恢复，保留其他任务工作区改动。本任务提交时只暂存自身文件及共享索引的自身条目。
- 未部署，未改现场任务；真实浏览器及 agent2 同形 trace 验收待执行。
