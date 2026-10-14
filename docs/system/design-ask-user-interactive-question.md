# 结构化提问交互协议设计（ask_user / AskForm）

> 状态：设计完成，待评审排期
> 日期：2026-10-13
> 关联：[Runner/Desktop/Runtime 共同契约](runner-desktop-runtime-integration-contract.md)、[AgentRunner 架构](agent-application-architecture-design.md)、[chat 体验优化设计](design-chat-experience-optimization.md)
> 范围声明：本文只做协议与整体规划，未开发；涉及 Runner 共享 wire 的字段（4.3、4.4）在实施前按契约第 8 节完成 schema/fixture 登记。

## 1. 背景与目标

参考交互（ZCode 提问卡片）：智能体在需要用户澄清时，不输出纯文本问题，而是渲染一张结构化提问卡片——分类标签 + 题干、复选/单选选项（label + description）、自由文本输入框、键盘操作提示、"忽略 / 提交"按钮，多题时支持分页（1/1）。

目标：我们自己的 agent 获得同等的提问交互能力，并解决跨端一致性问题：

1. **Web 端**（含桌面客户端，共用 ChatContainer）渲染富交互提问卡片；
2. **第三方渠道**（企业微信应用 / 飞书 / 钉钉 / 微信客服 / 企微个人号 RPA）**统一采用规范格式的编号文本问答**。这是范围决策而非临时降级：渠道端不做富卡片，未来有机会再针对飞书等提供特殊接口的渠道单独优化（§7.3）；
3. **数据格式协议先行**：渠道中立的核心 schema 一次性定稿，各端按能力接入渲染，不出现"各端各自发明一套问答格式"；
4. **等待与找回**：提问在用户响应前无限期保持（不占执行资源）；用户可跳过（loop 按已知信息继续）；Web 端提供待答标识与通知（站内信 / Web Push），随时回到提问卡片作答。

## 2. 现状盘点（2026-10-13）

### 2.1 两个既有先例

| 机制 | 文件 | 等待语义 | 渠道表现 | 缺口 |
|---|---|---|---|---|
| `clarify` | `src/tools/agent/clarify_tool.py` + `src/services/agent_runner/runtime/controls.py:75-84` | ✅ runner waiting → 用户 reply 回注为 tool result | 纯文本问题 + 纯文本回答 | 只有单个 question 字符串，无选项/表单；**仅子执行（SUBAGENT role）产生等待，root 执行调用不等待** |
| `present_options` | `src/tools/agent/present_options_tool.py` | ❌ 纯展示增强（不阻塞） | 渠道端仅靠 usage guide 要求 LLM 正文给编号清单兜底 | 前端按钮点击只发序号文本；非阻塞，无提交/忽略语义 |

关键结论：**"阻塞等待 + 用户应答回注"的完整管道已经存在**（wait → snapshot → reply control → `recovery._apply_reply` 回注 tool result），本设计不新建等待机制，只扩展载荷与渲染。

### 2.2 渠道能力矩阵

| 渠道 | 现有出站 | 交互回调现状 | 富卡片就绪度 |
|---|---|---|---|
| 飞书 | text / post / **interactive（builder 已存在但全仓零调用）** | 回调入口只认 `url_verification` 和 `im.message.receive_v1`，卡片回调 `card.action.trigger` 直接 ignore（`channel_routes.py:1329-1355`） | 高：补发送方法 + 回调分支即可 |
| 企微应用 | text / markdown / textcard / image / file / news | 所有 `event` 类型在 `channel_routes.py:794` 直接丢弃；无 template_card builder | 中：需新增 template_card builder + event 分支 |
| 钉钉 | sampleText / sampleMarkdown / sampleImageMsg / sampleFile / sampleLink | 机器人消息无原生点选回调（sessionWebhook 未使用，回复走 REST） | 低：原生点选需引入 AI 卡片，成本高 |
| 微信客服（wecom_kf） | text / image / file / link（无 markdown，长内容渲染长图） | 无交互消息类型；**平台限制单次咨询 5 次回复** | 无：维持文本编号 |
| 企微个人号 RPA | SendText / SendImage / SendFile | 无交互能力 | 无：维持文本 |

### 2.3 应答回注管道（复用，不改造）

- 渠道：`ChannelRunnerAgent.process_message_sync`（`src/channels/runner_agent.py:86-191`）发现 waiting runner → 用户下一条消息自动转 `POST controls {action:'reply', answer, wait_id, target_execution_id}`；
- Web：`useAgent.ts:601-625` 检测 parked runner 的 clarification wait，把下一次发送转 reply control；
- 恢复：`recovery.py:_apply_reply` 校验 wait_id → 答案写为原工具调用的 tool result（`{"success":true,"answer":...}`）→ runner 续跑。
- 约束：同一执行单 `state.waiting` 槽位（多题必须打包进一个 wait）；重复 reply 报 `CONTROL_WAIT_CONSUMED`；answer 为纯字符串（`control_contracts.py:16`，上限 1MB）。

## 3. 设计原则

1. **一份中立 schema，各端投影**：AskForm / AskAnswer 在 `contracts/runtime-host/v1/` 定稿；Web、渠道、LLM 上下文都是该 schema 的投影，不允许渠道私定问答格式。
2. **双轨分离**：阻塞式提问（`ask_user`，等待应答，runner 挂起）与非阻塞式建议（`present_options`/quickOptions，不等待）语义分开、UI 可区分，不互相替代。
3. **canonical 文本是统一兜底**：结构化答案的权威回流形态是一段确定性的、人类可读的 canonical 文本（§4.2），它同时是 reply.answer、渠道文本格式和 LLM tool result；富客户端的结构化字段只是增强传输。任何渠道能力缺失都不阻塞任务闭环。
4. **契约合规**：公开 controls 仍只有 pause/resume/reply；不因提问卡片新增控制动作；wire 可选字段按契约 §7.2 做严格解析器兼容验证（C12）。
5. **Web 是唯一富交互端**：Web 与未来自由桌面客户端（共用 Runner 快照 wire）渲染提问卡片；全部第三方渠道采用同一份文字问答模式（2026-10-13 范围决策），渠道端文案明确告知操作方式（回复编号/文字/跳过），不假装有点击按钮。

## 4. 核心数据协议

### 4.1 AskForm（下游：问题载荷）

```jsonc
// contracts/runtime-host/v1/schemas/ask-form.schema.json（实施时落盘）
{
  "form_id": "frm_<uuid>",              // 服务端生成
  "wait_id": "wait_...",                // 关联的 runner wait（decorate_wait 补齐）
  "title": "归档范围",                   // 分类标签，≤20 字，可缺省
  "intro": "除成本治理外，以下开发已完成…", // 可缺省引导语
  "questions": [                         // 1..5 题，打包进同一个 wait
    {
      "id": "q1",                        // 问题稳定标识（答案按 id 对应）
      "tag": "归档范围",                  // ≤12 字，可缺省
      "prompt": "以下哪些任务移入 ideas_finished.md？",
      "type": "single | multi | text",   // 单选(radio) / 多选(checkbox) / 纯自由文本
      "required": false,                 // 默认 false；为 true 时"至少选一项或填自定义内容"
      "options": [                       // single/multi 必填，2..8 项
        {"key": "q1o1", "label": "queue_order 序列自愈…", "description": "代码+测试+CR 完成…"}
      ],
      "allow_custom_text": true,         // 选项下方附自由输入行；single/multi 默认 true，text 题恒有输入框
      "custom_placeholder": "输入自定义内容…", // 自由输入行占位提示，可缺省
      "placeholder": "输入你的回答…",      // text 类型输入框提示语，可缺省
      "max_select": 5                    // multi 可选上限，可缺省；single 恒为 1
    }
  ],
  "allow_skip": true,                    // 是否允许"忽略"；false 时 UI 不出忽略按钮
  "expires_at": null                     // 预留字段，MVP 恒为 null（§5.4）
}
```

载荷约束（服务端与前端共同校验）：单 ask ≤5 题；single/multi 选项 2..8；`label` ≤40 字、`description` ≤100 字（渠道文本降级行宽）；`prompt` ≤200 字；序列化后 ≤8KB。超限由工具层返回失败（与 present_options 的 2~6 校验同风格），不静默截断。

**题型语义（2026-10-13 补充定稿）**：

- `single` 单选（radio，一次恰选一项）；`multi` 多选（checkbox，可配 `max_select` 上限）；`text` 纯自由输入（无选项）。工具入参校验题型与选项匹配（single/multi 无 options、text 带 options 均报错）。
- **选项与自定义输入并存**：`single/multi` 默认在选项下方保留一行自由输入（`allow_custom_text`，默认 `true`；严格确认门可关）。用户可以只选、只输入、或两者同时（"选 2，但要改名成 X"）；UI 不做互斥清空，由用户自主控制。
- **required 校验口径**："选中的选项与非空自定义内容至少有一项"，不是强制必须点选项。
- 单选/多选的选择必须在 `type` 中显式声明，由 LLM 按语义决定（互斥方案用 single、可并集用 multi），前端按类型渲染 radio/checkbox，不得混用。

### 4.2 AskAnswer（上游：答案回流）

**canonical 文本格式**（权威形态，即 reply.answer 与 LLM tool result 内容）：

```
【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档
【后续独立事项】飞书钉钉接入 AgentRunner
【补充说明】queue_order 先观察一周
```

- 每题一行 `【{tag 或题干截断}】{答案}`；多选用「、」连接；text 题直接写内容；空答案题省略该行。
- 选项答案写 **key + 可读 label**（如 `q1o1（queue_order 序列自愈）`），LLM 无需反查选项表即可理解；key 保留供程序解析。
- **自定义内容与选项并存**：选了选项又填了自定义时，自定义以「；自定义：」前缀附在同题行尾；只填自定义没选项时只写内容。selection 与 text 是一题答案的两个独立部分（对应结构化 `selected_keys` + `text`），LLM 自行综合理解（如"选 2 但要求改名"）。
- **忽略固定文案**：`（用户跳过了本次提问，请按已知信息继续）`。

**结构化传输（Phase 2 可选 wire 扩展）**：`RunnerControl`（`control_contracts.py`，`extra='forbid'`）新增可选字段：

```jsonc
"answers": [ {"question_id": "q1", "selected_keys": ["q1o1","q1o3"], "text": null} ]  // ≤5 项
```

与 `answer` 并存且一致（服务端以 canonical 文本为准，结构化字段供富客户端类型化提交与审计）；`intent`/`digest` 自动覆盖新字段（契约合规：仅增加可选且缺省兼容字段，仍需严格解析器 fixture 验证 + 生产/消费双端样例）。Web BFF 原样透传该模型，需同步 `frontend/web/api/runner.ts` 的 `RunnerControlIntent`。

### 4.3 传播链路（wait → snapshot → 各端）

| 环节 | 改动点 | 说明 |
|---|---|---|
| 工具 → wait | `runtime/controls.py` clarify/ask_user 分支 | `wait` 追加 `"form": {...AskForm}`（wait 是不透明 dict，引擎零改动） |
| wait_id 补齐 | `runtime/durable_control.py:95-110` | 复用现有 decorate_wait；`clarificationQuestions` 投影同步带上表单元数据 |
| 快照白名单 | `public_view.py:48-57 _public_waiting` | **新增 `form` 字段必须加白名单，否则 API 不出**（最易漏点） |
| 事件 | `event_contracts.py` 4 种 kind 不变 | 事件仍是失效通知，前端凭 view_revision 拉 GET 快照取 form，零事件协议改动 |
| 渠道 | `src/channels/runner_agent.py:172-183` | 现在直返 `wait['question']`；改为 canonical 渲染 `wait['form']`（无 form 时退回 question，兼容旧在途 runner） |
| legacy SSE | `legacy_stream_bridge.py:195-205` clarification 帧 | 追加可选 `form` 字段（非 runner 路径的兼容增强，Phase 1 可后置） |
| 前端类型 | `frontend/web/api/runner.ts` RunnerWait、`useAgent.ts` clarificationLeaf | 加 form 解析 |

### 4.4 schema 登记与兼容

- AskForm/AskAnswer schema + 正反 fixture 落 `contracts/runtime-host/v1/`，随实现按契约第 8 节登记（请求/响应字段、必填缺省、错误码、幂等摘要、样例）。
- 旧快照（无 `form`）：前端与渠道按纯文本 question 渲染（现状行为）；旧客户端收到带 `form` 的快照忽略未知字段即可，但须用严格解析器验证不报错。

## 5. 状态机与生命周期

1. **一 ask 一 wait**：引擎单 waiting 槽位决定多题必须打包成一个 AskForm；截图中的"1/1"分页是 Web 对多题包的展示形态，不是多个 wait。
2. **复用 clarification 状态机**：`waiting --reply--> running`，恢复管道（`_apply_reply`）零改动；答案经 canonical 文本回注为 ask_user 的 tool result。
3. **多轮串行**：每轮 ask 新 tool_call_id → 新 wait_id；重复提交已有 `CONTROL_WAIT_STALE(409)` / `CONTROL_WAIT_CONSUMED` 防护。
4. **忽略（跳过）语义**：
   - 由 `form.allow_skip` 控制（默认 `true`）；确认门类提问（必须二选一确认）LLM 应置 `false`，UI 不渲染忽略按钮，用户必须作答或取消整个任务。
   - 忽略的实现 = reply + 忽略固定文案（§4.2），不动 recovery 的 resume 空输入分支（该分支对 clarification wait 的 tool fact 语义未经验证，留作后续确认项）。
   - **忽略后的 loop**：固定文案作为 ask_user 的 tool result 回注（`{"success":true,"answer":"（用户跳过了本次提问，请按已知信息继续）"}`），LLM 收到明确的"跳过"事实后继续执行，按已知信息推进并在后续输出中说明基于的假设——语义诚实，不伪造用户选择。忽略 ≠ 取消：runner 继续跑，用户仍可随时 cancel 整个任务。
   - **防追问循环**：usage guide 约束"用户跳过后，同一问题不得在无新信息的情况下重复追问；连续被跳过时应直接按合理默认值执行"。
   - **部分忽略**：多题包中某题空答按该题跳过处理（canonical 文本省略该行）；`required=true` 的题由前端拦截空提交，服务端校验兜底。
5. **等待与超时**：
   - **默认无限期等待**（2026-10-13 决策）：runner 进入 waiting 即 park——不占 worker、不占租约、不产生 LLM 费用；checkpoint 持久化在 Postgres，服务重启可恢复，直到用户 reply 或 cancel。等一天再答是合法状态，渠道端也不因等待超时而失效。
   - `expires_at` 字段已预留（MVP 恒 null）。若产品后续要超时（如 24h 未响应自动按跳过继续或转取消），属 Phase 3：需先冻结监管、取消与计费口径，不因采用自动续跑而隐性扩大单次时限。
6. **待答可达性与恢复（仅 Web，桌面客户端跟随）**：
   - **恢复到选择状态天然成立**：快照即权威且持久化（`agent_runners.public_snapshot` + checkpoint），用户重开页面/换设备登录，会话里的提问卡片按快照 `waiting.form` 原样重现并可作答，无需任何"恢复"API。渠道用户无需此机制——问题文本已在对话流中，回复即答。
   - 缺的是**找回入口**，Phase 2 补齐：① 会话列表「待回答」标识（基于该用户会话的 runner `status=waiting` 且含 form 的轻量查询）；② 提问通知：runner 进入 waiting(form) 时经统一通知基础设施生产新通知事件（如 `runner_waiting_input`，生产者挂在 park 事务上，模式同现有 terminal 通知生产者），站内信恒开兜底 + 已开启浏览器通知的用户收 Web Push，点击回到会话即见提问卡片。复用 [Web 任务推送通知](web-task-push-notification-design.md) 的管道与点击回跳机制，不新建推送通道。
7. **跨端一致**：同一用户可 Web + 渠道多端在线，任一端提交即消费 wait；其余端下次拉快照发现 waiting 消失自动置灰。

## 6. Agent 侧改造

1. **新控制工具 `ask_user`**（`catalog=False`，`control_set.py` 显式装配）：
   - InputModel 即 AskForm 的 questions 结构（Pydantic 嵌套，中文 Field 描述）；
   - execute 仅做校验与回显（同 clarify 风格），等待语义由 controls.py 赋予；
   - **root 执行也产生等待**：现 `controls.py:75-84` 仅 `AgentMode.SUBAGENT` 产生 wait，ask_user 需对 MASTER/STANDALONE 同样 yield wait。这是功能性缺口，须回归验证 root 等待与 cancel/pause/final 流、防滥用拦截点的交互。
2. **usage guide（写入 system prompt）**：何时用 ask_user（信息缺失且候选明确 / 确认门 / 多项需澄清打包一次问完）；开放式问题仍用普通文本回复（不为问而问）；非阻塞建议用 present_options；单轮任务尽量 ≤1 次提问。题型指导：互斥方案用 `single`、可并集用 `multi` 并在 `type` 显式声明；选项题默认保留自定义输入行（`allow_custom_text` 默认 true），严格确认门才关闭；每个选项尽量带一句话 `description` 帮用户判断。
3. **存量工具处置**：`clarify` 保留兼容（旧 runner 在途），渐进收敛到 ask_user；`present_options` 保留其非阻塞定位，其 usage guide 增加与 ask_user 的分工说明。

## 7. 各端渲染

### 7.1 Web（含未来自由桌面客户端，Phase 1）

- 新组件 `frontend/web/components/chat/QuestionCard.vue`：题 tag chip + 题干、single 渲染 radio（圆点）/multi 渲染 checkbox（方框，严格按 `type` 区分不混用）、`allow_custom_text` 时选项下方渲染一行自由输入（占位用 `custom_placeholder`，对齐截图"输入你的回答…"）、text 题渲染独立输入框、必填校验（"至少选一项或填自定义内容"）、底部"忽略 / 提交"（`allow_skip=false` 时无忽略按钮）、多题分页（‹ 1/N ›）、键盘提示行。
- 选择与自定义输入**并存不互斥**：输入自定义不清空已选项，提交时 `selected_keys` 与 `text` 一起走 canonical 序列化（§4.2）。
- 渲染挂载点：`MessageItem.vue` 现有 quickOptions / HumanAssistanceCard 同区；数据通路 = runner 快照 `waiting.form` → `utils/runnerMessages.ts` presentation 投影（与 quickOptions 提取器同处扩展）→ 快照轮询/SSE 失效通知驱动刷新。
- 提交链路零新增 API：结构化答案由前端序列化为 canonical 文本 → 走 `useAgent.ts` 已有的 clarification reply（`POST /api/chat/runners/:id/controls {action:'reply', answer, wait_id, target_execution_id}`）；提交成功（快照 waiting 消失）后卡片置灰为只读回显。
- 忽略按钮 = 提交忽略固定文案（§5.4）；已消费的 wait 提交报 409，前端提示"已答复"并置灰。
- **未来自由桌面客户端**：消费同一 Runner 快照 wire（`waiting.form` 为共享契约字段），渲染同一张卡片；系统级"待答"通知走 Electron 通知通道，另行在桌面计划登记，本设计只保证 wire 就绪。

### 7.2 第三方渠道：统一文字问答模式（范围决策，Phase 1 全渠道）

全部第三方渠道不做富卡片，采用同一份文字问答规范，按各自支持的 msgtype 发送：

| 渠道 | 发送类型 | 备注 |
|---|---|---|
| 飞书 | post | 走现有 markdown 自动转换 |
| 企微应用 | markdown | 2048 字节分段逻辑沿用 |
| 钉钉 | sampleMarkdown | 4000 字节分段沿用 |
| 微信客服（wecom_kf） | text | **占单次咨询 5 次回复预算中 1 次**，需走 `WeComKfReplyBudget` 为 final 预留 |
| 企微个人号 RPA | SendTextAction | 2000 字符单段上限 |

规范格式（各渠道统一，渲染函数放渠道共享层 `src/channels/ask_render.py`，各 adapter 调用，杜绝四处复制）：

```
❓【归档范围】请确认以下任务的归档处理
除成本治理外，以下开发已完成、仅剩部署/验收…

【归档范围】哪些任务移入 ideas_finished.md？（多选，回复编号如"1、3"；也可直接回复文字说明）
1. queue_order 序列自愈 — 代码+测试+CR 完成，仅剩 agent1 部署与验收
2. wecom_kf 并发限频修复 — 开发+单测完成待部署
3. 飞书钉钉接入 AgentRunner — 待部署真机验收
也可直接输入自定义要求（如"选 1、2，file_assets 先等等"）

【补充说明】其他要求请直接回复文字

可回复编号或文字；回复"跳过"则按已知信息继续
```

- 题型提示语随 `type` 区分：多选`（多选，回复编号如"1、3"…）`、单选`（单选，回复一个编号；也可直接回复文字）`、text 题直接提示输入；`allow_custom_text=true` 的选项题都带"也可直接回复文字"提示。
- `allow_skip=false` 时末行改为"本问题需明确答复后任务才能继续"。
- 用户文本回复的**解析归一化是渠道侧 convenience，不承担正确性**：`"1、3"`/`"1,3"`/`"1和3"` → selected_keys，编号后的其余文字归为该题自定义 text（如"1、3，另外 file_assets 先等等"），按该会话最近一次 AskForm 映射（form 缓存于 session 元数据，随 wait 消费失效）；无法解析时原文透传给 LLM，由模型语义理解（clarify 现状即如此）。混杂回复（部分编号部分自由文本）→ 组合为 canonical 多行。
- 渠道端无"已答复"回执：用户在其他端答完后，渠道侧仅在下一条消息的回复中体现结果，不主动撤回已发的问题文本（保持渠道只读历史，避免额外消息消耗 wecom_kf 预算）。

### 7.3 未来机会：渠道原生卡片（不在本期范围）

以下仅登记方向，不承诺；实施前须单独立任务并过共同契约检查点：

- **飞书**（优先级最高）：`interactive` 卡片 builder 已存在（`message_builder.py:86`，零调用点）；需补发送方法 + 回调入口 `card.action.trigger` 分支（现直接 ignore，`channel_routes.py:1329-1355`）→ `get_or_create_session` 路由 → reply control。
- **企微应用**：新增 `template_card`（button_selection/button_list）builder；`channel_routes.py:794` 的 event 丢弃分支前识别 `template_card_event`，EventKey → session 路由。
- **钉钉**：原生点选需引入 AI 卡片及独立回调订阅，投入产出比最低，最后评估。
- 届时 AskForm→卡片渲染与卡片回调→canonical 的映射仍以本文 §4 schema 为唯一协议源，不另定渠道私有格式。

## 8. 安全与限制

- 载荷上限（§4.1）+ `answer` 沿用 1MB 上限；工具层超限返回明确错误，不截断。
- 渠道卡片回调鉴权：open_id/user_id → session 复用 `get_or_create_session` 可信映射与事件去重（`MessageDeduplicator`）；回调仅等价于一次 reply，不转换为任何许可/授权（契约 L156）。
- 渠道限流与预算沿用现有机制（wecom_kf 5 次预算、各渠道 10 条/60s）；ask 卡片走正常发送路径，不新增外发通道。
- 卡片文案与普通回复同源（LLM 生成），无新增敏感数据面；`form` 进快照前与 question 同等对待（不落凭证，凭证红线不变）。

## 9. 分阶段实施

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| Phase 1：协议 + Web + 渠道文字全渠道 | ask-form schema/fixture 登记；`ask_user` 工具 + root 等待；wait/snapshot `form` 白名单；Web QuestionCard（单选 radio/多选 checkbox/自定义输入并存/忽略/多题分页，`allow_skip`/`allow_custom_text` 生效）；渠道文字问答（`ask_render.py` + runner_agent 直返改造）+ 回复归一化 | root 等待回归（cancel/pause/final/防滥用）；渠道五端文本问答端到端；旧快照严格解析器兼容 fixture；Web 提交/忽略/部分忽略/重复提交 409；单选与多选、选项+自定义组合提交的 canonical 序列化正确；跳过后 LLM 不重复追问 |
| Phase 2：待答可达性 | `runner_waiting_input` 通知生产者（挂 park 事务，复用统一通知基础设施）；站内信兜底 + Web Push（点击回跳会话即见卡片）；会话列表「待回答」标识 | 通知到达与点击回跳端到端（浏览器关闭/后台场景沿用 Web 推送验收口径）；同一 runner 多次提问的频控；渠道用户不受影响 |
| Phase 3：增强项（按需） | `expires_at` 超时语义冻结（可选）；`RunnerControl.answers` 结构化字段（fixture + 严格解析验证）；legacy SSE 路径 form 透传；埋点（提问率/跳过率/响应时长） | 过期不产生孤儿计费；wire 可选字段过严格解析器兼容门 |

未来机会（单独立任务，不在上述排期）：渠道原生卡片，见 §7.3。

## 10. 开放问题

1. root 执行 clarify/ask_user 等待与主智能体 final_answer 流、`task_record` 语义的交互（Phase 1 首个验证项）；
2. 同一用户多端并发的"先到先得"体验（后提交端 409 提示文案）；
3. wecom_kf 预算紧张时是否允许 LLM 声明"低预算模式"（问题与选项合并为更短文本）；
4. 提问通知的频控策略：同一 runner 短时间内多次 ask、多个会话同时 waiting 时的推送合并与静默时段（Phase 2 前定稿）。
