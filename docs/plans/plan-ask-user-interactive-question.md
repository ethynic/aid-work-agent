# 结构化提问交互（ask_user 提问卡片）实施计划

> 创建：2026-10-14
> 状态：📋 待开发（设计已过架构评审，v6 口径修正稿）
> 设计文档：[design-ask-user-interactive-question.md](../system/design-ask-user-interactive-question.md)（v6，含六轮评审记录与落实对照）
> ideas 条目：20261013-1434

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 协议 + 权威校验 + Web 提问卡片 + 渠道文字全渠道 | 📋 待开发 | — |
| Phase 2 | 同源待答可达性（提问通知 + 会话待答标识） | 📋 待开发 | — |
| Phase 3 | 增强项（按需） | 📋 待开发 | — |

## 1. 范围边界（评审固化，实施中不得扩张）

**做**：设计文档 §4–§8 定义的协议、校验、渲染与历史展示；§9 Phase 1/2 验收清单。

**不做**（五轮评审明确的禁扩张清单）：不恢复校验失败落库与拒绝回放；不新增自动补发、纠错次数或旧表单续答状态；不新增逐次能力探测、重复授权查询或用户确认步骤；不把文案建议写成 schema 硬拒绝；不强迫渠道用户使用编号（普通文字持续兼容）；不为展示维护第二套权威生命周期；跨入口作答与渠道原生卡片为未来机会（另立任务）。

## 2. 现状锚点（实施涉及的关键代码）

| 区域 | 文件 | 涉及改动 |
|---|---|---|
| 控制契约 | `src/services/agent_runner/control_contracts.py` | 新增 `AnswerItem`/`answers`/`skip`；入口校验修订（§设计 4.2）；`intent()` 显式条件序列化 |
| 控制受理 | `src/services/agent_runner/control_repository.py` | 归一化 + 判定表校验（422 不落库）；受理 payload 附 canonical + form 快照；supplementalInputs 投影扩展 |
| 恢复 | `src/services/agent_runner/recovery.py` | `_apply_reply` canonical 回注验证（复用目标，预期零/小改） |
| wait 装配 | `src/services/agent_runner/durable_control.py` | decorate_wait 复用；clarificationQuestions 带表单元数据 |
| 公共视图 | `src/services/agent_runner/public_view.py` | `_public_waiting.form` 白名单、errors DTO、supplementalInputs 扩展投影 |
| 控制分支 | `src/services/agent_runner/runtime/controls.py:75-84` | ask_user 分支 + **root 执行等待**（现仅 SUBAGENT） |
| 工具 | `src/tools/control_set.py` + 新建 `src/tools/agent/ask_user_tool.py` | 控制工具装配、服务端 id 生成、硬校验、usage guide |
| 存量工具 | `src/tools/agent/clarify_tool.py`、`present_options_tool.py` | 保留兼容；usage guide 分工说明 |
| 能力发现 | Runner capabilities 端点（`agent_runner/api.py`）+ Web BFF（`src/api/agent_runner_web.py`） | `ask_structured_answers` 字段 |
| 渠道入口 | `src/channels/runner_agent.py:86-191` | waiting.form 渲染直返；入站单管线 |
| 渠道共享 | 新建 `src/channels/ask_render.py` | 分段渲染（字节、逻辑行、续段题号）、wecom_kf 预算核算 |
| 五渠道 | `src/channels/{wecom,dingtalk,feishu,wecom_kf,wecom_personal_rpa}/` | 各自发送类型接入 |
| 契约登记 | `contracts/runtime-host/v1/`（schemas/fixtures/tests） | ask-form / errors DTO schema + fixture（幂等四例） |
| Web 前端 | `frontend/web/api/runner.ts`、`composables/useAgent.ts`、`utils/runnerMessages.ts`、`components/MessageItem.vue`、新建 `components/chat/QuestionCard.vue` | 类型、提交/重试/换键、历史只读投影、提问卡片、状态机、capabilities 缓存降级 |
| 通知（Phase 2） | `src/services/agent_runner/event_repository.py`（事务尾登记模式）、`src/background_runner.py`、统一通知核心 | `runner_waiting_input` 生产者 |

## 3. Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道

> 风险评级：高（Runner 公开契约 + 受理/恢复链路 + 五渠道）。按开发流程规范走**完整三智能体流程**（开发自测 → 独立测试 → CodeReview），WP1–WP3 完成后先做一次阶段内集成验证再进 WP4/WP5。

### WP1 契约与 schema（先行，其余包依赖）

1. `contracts/runtime-host/v1/`：`ask-form.schema.json`（含题型/选项/必填/allow_skip/expires_at=null）+ `errors` DTO schema + 正反 fixture；**幂等四例 fixture**（设计 §4.3：升级前后 digest 逐字节一致、同键重试命中、已受理后同键改答案拒绝、缺省序列化不变）。
2. `control_contracts.py`：模型字段 + 入口校验修订——reply 合法条件扩展（answer/附件/answers/skip 满其一）、`answers`×非空 answer 互斥、`skip`×`answers` 互斥、非 reply 禁带、旧 clarify 空回复仍 `CONTROL_REPLY_REQUIRED`；`intent()` 对新字段"未提供即不含键"（禁全局 exclude）。
3. capabilities 增加 `ask_structured_answers`（仅后端全实例就绪后由前端启用消费）。

验收：schema 严格解析器过全部 fixture；幂等四例通过；旧形状请求 digest 不变。

### WP2 Runner 受理与恢复

1. `control_repository.py`：归一化（qid 小写、去空白、selected_keys 去重）→ 判定表（设计 §5.4，先归一化后判定）→ 失败同步 422 + `errors`（不创建 control、不消费 wait）；成功 → canonical 权威生成，payload 附 `{canonical, form 快照}`，supplementalInputs 投影扩展（canonical/control_id/答案摘要/form 快照）。
2. `public_view.py`：`form` 白名单（`_public_waiting`）、errors DTO 投影、supplementalInputs 扩展字段白名单。
3. `durable_control.py`：clarificationQuestions 投影带表单元数据（历史问题文本沿用既有投影）。
4. `recovery.py`：验证 canonical 回注为 tool result 的复用性（预期小改或不改；若发现 `fact.result` 结构需适配，记录差异并最小修改）。

验收：判定表全组合（skip×allow_skip、required×allow_skip、部分空、全空 `[]`、纯文本兼容）；校验失败不落库不消费 wait 且修正后可重提；已受理后同键改答案 `IDEMPOTENCY_INPUT_MISMATCH`；旧 clarify 全路径回归。

### WP3 ask_user 工具与 root 等待（首个技术验证点）

1. **先行 spike**：root 执行产生 clarification wait 的语义验证——`controls.py` 现仅 `AgentMode.SUBAGENT` 等待；验证 MASTER/STANDALONE 等待与 final_answer 流、`task_record`、cancel/pause、防滥用拦截点的交互，结论回写设计文档 §11.1。
2. 新建 `ask_user_tool.py`（catalog=False）：InputModel 仅内容字段；`execute` 生成 form_id/question_id/key + 硬校验（题型/选项匹配、required/max_select、总载荷 8KB）；`control_set.py` 装配；usage guide（何时提问、题型声明、文案长度建议口径）。
3. `controls.py` ask_user 分支：按 spike 结论实现 root 等待，wait 载荷附 `form`。
4. `clarify_tool.py` / `present_options_tool.py`：保留，usage guide 增加三工具分工。

验收：root 等待回归（cancel/pause/final/防滥用/连续两轮 ask）；子执行既有 clarify 路径不回归；超载荷拒绝（FORM 载荷错误）。

### WP4 Web 前端（含桌面兼容）

1. `runner.ts`：`RunnerWait.form`、`RunnerControlIntent.answers/skip`、errors DTO 类型；`useAgent.ts`：结构化提交、网络不明原键重试、已受理后修改换新键、校验失败直接重提（无专门键管理）。
2. `QuestionCard.vue`（`components/chat/`）：single radio / multi checkbox / text 输入、`allow_custom_text` 自由输入行（并存不互斥）、required 预检（即时反馈）、忽略/提交（`allow_skip=false` 无忽略）、多题分页 ‹1/N›；§5.7 状态机（提交中/已受理/已应用/按错误码被拒/任务关闭）；422 errors 逐题高亮。
3. `runnerMessages.ts`：快照 `waiting.form` 投影为可作答卡片；supplementalInputs 扩展投影为只读已答卡片（含被拒/未答取消态）；`MessageItem.vue` 挂载（quickOptions 同区，与 present_options 按钮视觉区分）。
4. capabilities 会话启动读取一次并缓存；字段缺失 → 客户端 canonical 文本路径（灰度/回滚窗口专用）。

验收：提交/跳过/双端冲突（409 细分）/刷新与跨标签页上下文重建/历史回显（已答/被拒/未答取消）；旧快照（无 form）渲染不回归。

### WP5 渠道层（五渠道文字问答）

1. `ask_render.py`：按 adapter 配置字节上限渲染；逻辑行优先分段、**单行超限按合法字符边界跨消息**（不破坏多字节序列）、续段保留题号/选项号（`Q1（续）`）、优先省略 description；题干/选项内容不丢失；wecom_kf 段数×预算核算（**提问轮次不为 final 预留**，实施时与 `WeComKfReplyBudget`/`reserve_for_final` 现状核对，结论回写 §11.2）。
2. `runner_agent.py`：waiting.form 渲染直返（无 form 退回 question，兼容旧在途 runner）；发送事实记录（已确认段/失败原因/重投次数，仅防重复发送）。
3. **入站单管线**（所有渠道一致，不分投递状态）：隐藏命令 → 精确"跳过"（skip=true）→ Q 编号解析（三分法 + 归一化；协议错误纠正提示一次）→ 精确"重发/简化"（续传只发缺段）→ **其余一律沿现有 reply 路径**。
4. 受理/应用边界：受理后暂停旧表单补发；control `consumed` 且快照无 wait → 清理剩余发送记录；被拒且 wait 有效 → 可修正重发。全程读既有 control 状态 + waiting 快照，无新状态机。
5. 五渠道接入：飞书 post / 企微 markdown / 钉钉 sampleMarkdown / wecom_kf text（预算内）/ RPA text。

验收：入站管线分流（含 pending 自然语言不吞、delivered 无拦截、协议错误纠正一次）；分段用例（多题多段、单行超限跨消息、内容不丢、续段题号）；wecom_kf 预算与 final 预留核对结论；五渠道端到端各一例。

### WP6 集成、发布与验收

1. 发布顺序：后端（Runner API + Web BFF 同部署单元）全实例就绪并确认健康 → 前端放开结构化提交 feature flag；回滚注意（回滚目标保留 ask 受理能力，或先取消在途 ask 任务）写入部署备忘。
2. 按设计 §9 Phase 1 验收清单逐项验收（正常提交、跳过、幂等、必要非法输入、长行分段、发送失败、历史恢复）。
3. 文档收尾：更新设计文档 §11 开放问题结论、本计划进度区、ideas 索引。

## 4. Phase 2：同源待答可达性（风险中，完整流程）

1. `runner_waiting_input` 通知生产者：runner park 事务尾登记（复用 Web 任务通知的既有事务尾登记模式），payload 最小化（session_title/event_kind/resource_ref），不新增推送通道。
2. 站内信兜底 + Web Push（已开启浏览器通知的用户）；点击回跳原会话（复用既有点击解析）。
3. 会话列表「待回答」标识：Web 同源任务的 runner waiting 查询（轻量端点或并入会话列表接口，实施时定）。
4. 频控：同一 runner 多次提问合并策略（设计 §11.4，Phase 2 前定稿）。

## 5. Phase 3：增强项（按需，另立排期）

`expires_at` 超时语义冻结（先冻结监管/取消/计费口径）；capabilities 正式协商扩展；legacy SSE form 透传；埋点（提问率/跳过率/响应时长）。

## 6. 先行验证与风险清单

| # | 事项 | 时点 | 回写位置 |
|---|---|---|---|
| 1 | root 执行等待语义 spike（final 流/task_record/cancel/pause/防滥用交互） | WP3 开工前 | 设计 §11.1 |
| 2 | wecom_kf 预算与 `reserve_for_final` 现状核对（提问轮次预留规则） | WP5 开工前 | 设计 §11.2 |
| 3 | 恢复链路对撤权/设备/插件事实的重验覆盖面核对 | WP2 期间 | 设计 §11.3 |
| 4 | 飞书/钉钉分段配置的官方字节上限核对 | WP5 期间 | 设计 §2.3 |
| 5 | 旧快照（无 form）严格解析器兼容 fixture | WP1 | fixture 随 schema 登记 |

## 7. 验证与流程约定

- Phase 1 高风险：完整三智能体流程（开发自测 → 独立测试 → CodeReview），WP2/WP3 的受理与恢复回归为 CR 重点；Phase 2 中风险：同样走独立测试与 CR。
- 验证聚焦实际改动（评审四轮口径）：正常提交、跳过、幂等、必要非法输入、长行分段、发送失败、历史恢复；同一代码状态已有有效结果不重复跑。
- 每阶段完成：先更新本计划「开发进度」区，再同步 `docs/ideas.md` 索引行（规范见 AGENTS.md）。
- 部署由用户当场指示，助手只准备命令与检查清单。
