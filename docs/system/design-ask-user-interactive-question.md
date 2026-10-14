# 结构化提问交互协议设计（ask_user / AskForm）

> 状态：v2 评审修订稿，待复审
> 日期：2026-10-14（v1：2026-10-13，commit c93a00b9）
> 关联：[Runner/Desktop/Runtime 共同契约](runner-desktop-runtime-integration-contract.md)、[AgentRunner 架构](agent-application-architecture-design.md)、[Web 任务推送通知](web-task-push-notification-design.md)
> 范围声明：本文只做协议与整体规划，未开发；文中"零改动 / 零新增 API"均为**待验证的复用目标**，不是已验证结论，应用层校验、历史投影与幂等兼容改造已列入实施范围（§9）。

## 修订记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-13 | v1 | 初稿：复用 clarify 等待管道；Web 富卡片 + 渠道统一文字；忽略/无限期等待/待答通知 |
| 2026-10-14 | v2 | 按评审意见修订：①权威结构化答案（`answers`）提前到一期并由服务端校验、权威生成 canonical；②渠道回复 Q 编号协议与歧义政策；③作答入口收缩为同源，跨入口后置；④幂等摘要兼容（omit-null）与 answers/answer 一致性由服务端保证；⑤卡片状态机改为 control 回执驱动；⑥渠道渲染上限、分段与 wecom_kf 预算生命周期、未投递补发路径；⑦历史回显持久化与投影定义；⑧等待期资源边界（worker lease / 会话 claim / 撤权重验）；⑨确认门=业务选择非执行授权；⑩schema 边界补齐（id 生成、字节口径、空提交行为、阶段统一） |

## 1. 背景与目标

参考交互（ZCode 提问卡片）：智能体在需要用户澄清时渲染结构化提问卡片——分类标签 + 题干、单选/复选选项（label + description）、选项下方自定义输入行、"忽略 / 提交"按钮、多题分页（1/1）。

1. **Web 端**（含未来自由桌面客户端，共用 Runner 快照 wire）渲染富交互提问卡片；
2. **第三方渠道**（企业微信应用 / 飞书 / 钉钉 / 微信客服 / 企微个人号 RPA）**统一采用规范格式的文字问答**（范围决策，渠道不做富卡片；飞书等原生卡片为未来机会 §7.3）；
3. **数据格式协议先行**：渠道中立核心 schema 一次定稿，权威答案为结构化模型并由服务端校验（§4.2），各端为投影；
4. **等待与找回**：提问在用户响应前无限期保持（不占 worker/租约，资源边界见 §5.5）；用户可跳过（loop 按已知信息继续）；同源入口提供待答标识与通知。

## 2. 现状盘点（2026-10-13）

### 2.1 两个既有先例

| 机制 | 文件 | 等待语义 | 渠道表现 | 缺口 |
|---|---|---|---|---|
| `clarify` | `src/tools/agent/clarify_tool.py` + `src/services/agent_runner/runtime/controls.py:75-84` | ✅ runner waiting → 用户 reply 回注为 tool result | 纯文本问题 + 纯文本回答 | 只有单个 question 字符串；仅子执行（SUBAGENT role）产生等待，root 执行调用不等待 |
| `present_options` | `src/tools/agent/present_options_tool.py` | ❌ 纯展示增强（不阻塞） | 靠 usage guide 要求 LLM 正文给编号清单兜底 | 非阻塞、无提交/忽略语义 |

**阻塞等待 + 应答回注管道已存在**（wait → snapshot → reply control → `recovery._apply_reply` 回注 tool result），本设计复用该管道并扩展载荷、校验与渲染；管道复用点均标注为待实现验证。

### 2.2 与本设计相关的既有事实（评审确认）

- **controls 是异步受理**：control 有 `accepted → claimed → consumed / rejected` 生命周期；409 不只一种（`CONTROL_WAIT_STALE` / `RUNNER_RESUME_PENDING` / `CONTROL_EXECUTION_CLOSED` 等），waiting 消失也可能来自取消/失败（§5.7 状态机以此为前提）。
- **现有受理校验薄弱**：`ControlRepository.assert_wait` 只校验等待身份与类型，`_apply_reply` 直接收纳 answer——表单校验必须新增（§4.2），不能依赖前端。
- **授权按入口区分**：web/channel 会话分别校验 source、actor、session 归属；渠道任务读取要求可信 channel actor。**同一平台 user_id 不足以跨入口控制任务**（§5.6 以此收缩承诺）。
- **幂等摘要覆盖全模型**：`RunnerControl.intent()` 基于 model_dump、`digest()` 对整个意图 sha256——新增缺省字段也可能改变序列化结果（§4.3 兼容方案以此为前提）。
- **wecom_kf 预算**：`WeComKfReplyBudget(total=5)` 是**每条入站消息**（owner 级）对象，不是跨等待持久存在的 runner 预算；长正文会转图片，不能保证文本完整到达（§7.2 投递保障以此为前提）。

### 2.3 渠道文字能力矩阵

| 渠道 | 发送类型 | 单条上限（平台） |
|---|---|---|
| 飞书 | post | 4000 字节 |
| 企微应用 | markdown | 2048 字节 |
| 钉钉 | sampleMarkdown | 4000 字节 |
| 微信客服（wecom_kf） | text | 2048 字节；**每条入站 5 次回复预算** |
| 企微个人号 RPA | SendTextAction | 2000 字符 |

## 3. 设计原则

1. **一份中立 schema，各端投影**：AskForm / AskAnswer 在 `contracts/runtime-host/v1/` 定稿；Web、渠道、LLM 上下文都是投影，不允许渠道私定问答格式。
2. **服务端权威**：表单答案以结构化 `answers` 提交，由服务端按 checkpoint 中的原表单校验并**权威生成** canonical 文本；客户端不自带最终文本，执行依据与审计天然一致（§4.2）。
3. **双轨分离**：阻塞式提问（`ask_user`，等待应答）与非阻塞式建议（`present_options`/quickOptions，不等待）语义分开、UI 可区分。
4. **渠道统一文字模式**：全部第三方渠道同一份文字问答规范；渲染必须满足渠道单条上限，必答题与选项 label 永不静默截断（§7.2）。
5. **同源作答**：一期任务只能由产生它的同源入口作答（§5.6）；跨入口作答后置。
6. **业务确认 ≠ 执行授权**：ask_user 只收集业务选择，不改变工具权限、workspace grant 或任务许可（§8）。
7. **契约合规**：公开 controls 仍只有 pause/resume/reply；wire 新增可选字段按契约 §7.2 做严格解析器验证，幂等摘要兼容单独定义（§4.3）。

## 4. 核心数据协议

### 4.1 AskForm（下游：问题载荷）

```jsonc
// contracts/runtime-host/v1/schemas/ask-form.schema.json（实施时落盘）
{
  "form_id": "frm_<uuid>",              // 服务端（工具执行层）生成
  "wait_id": "wait_...",                // 关联的 runner wait（decorate_wait 补齐）
  "title": "归档范围",                   // ≤20 字，可缺省
  "intro": "除成本治理外，以下开发已完成…", // ≤200 字，可缺省
  "questions": [                         // 1..5 题，打包进同一个 wait
    {
      "id": "q1",                        // 服务端按题序生成（^q[1-9]$），form 内唯一
      "tag": "归档范围",                  // ≤12 字，可缺省
      "prompt": "以下哪些任务移入 ideas_finished.md？",   // ≤200 字
      "type": "single | multi | text",   // 单选(radio，最多 1 项) / 多选(checkbox) / 纯自由文本
      "required": false,                 // 为 true 时"至少选一项或填自定义内容"
      "options": [                       // single/multi 必填 2..8 项；text 禁止携带
        {"key": "q1o1", "label": "queue_order 序列自愈…", "description": "代码+测试+CR 完成…"}
        // key 服务端按序生成（^q[1-9]o[1-9]$），题内唯一；label ≤40 字；description ≤100 字
      ],
      "allow_custom_text": true,         // 选项下方自由输入行；single/multi 默认 true，text 恒有输入框
      "custom_placeholder": "输入自定义内容…", // ≤30 字，可缺省
      "placeholder": "输入你的回答…",      // text 类型输入框提示语，≤30 字，可缺省
      "max_select": 5                    // multi 可选上限，2..选项数，缺省=选项数；single 恒 1
    }
  ],
  "allow_skip": true,                    // 是否允许"忽略"；false 时 UI 不出忽略按钮
  "expires_at": null                     // 预留字段，一期恒为 null（§5.5）
}
```

**标识符与校验口径**：

- `question_id` / `key` 由工具执行层按题序/选项序**服务端生成**，LLM 入参只含内容字段（题与选项的顺序即编号），唯一性与格式由生成保证，不依赖 LLM 自觉；渠道选项序号 = key 尾号（`q1o3` ↔ 选项 3）。
- 长度上限：schema 字段按**字符**计；渠道投递相关按渲染后 **UTF-8 字节**计（§7.2）；总载荷 ≤8KB（UTF-8 字节），超限工具层返回失败，不静默截断。
- 题型语义：`single` **最多选一项**（是否必须作答由 `required` 决定）；`multi` 受 `max_select` 约束；`text` 无选项。工具入参校验题型与选项匹配（single/multi 缺 options、text 带 options 均报错）。
- **选项与自定义输入并存**：single/multi 默认在选项下方保留自由输入行；用户可只选、只输入、或两者同时（"选 2，但要改名成 X"）；UI 不做互斥清空。
- 等待期间表单**不可变**：AskForm 随 wait 写入 checkpoint，恢复与校验均以 checkpoint 中该份为准，reply 不携带、也不接受修改表单。

### 4.2 AskAnswer（上游：答案回流，一期即为权威结构化）

**传输模型**（`control_contracts.py`，`extra='forbid'`，一期新增）：

```python
class AnswerItem(BaseModel):
    question_id: str            # ^q[1-9]$，须存在于当前 form
    selected_keys: List[str]    # ⊆ 该题 options.key；single ≤1 项；multi ≤ max_select
    text: Optional[str]         # ≤1000 字；allow_custom_text=false 时须为空

class RunnerControl(...):       # 现有模型追加
    answers: Optional[List[AnswerItem]] = None   # ≤5 项（= form 题数上限）
```

**服务端权威处理**（控制受理事务内，`control_repository` 扩展，新增步骤）：

1. 读取 runner checkpoint 的 `waiting.form`（表单不可变，见 §4.1）；
2. 校验 answers：question_id 存在、key ∈ 该题 options、single ≤1 项、multi ≤ max_select、`allow_custom_text=false` 时 text 为空、`required` 题"选中的选项与非空自定义至少有一项"、长度上限；
3. **非法 → control 置 `rejected` + 机器可判定错误码**（`FORM_ANSWER_INVALID`，detail 指明 question_id 与原因；全空且 `allow_skip=false` → `FORM_ANSWER_REQUIRED`）；**wait 不消费**、`resume_control_id` 不写入，客户端可修正重提；
4. 合法 → **服务端忽略客户端自带的 `answer` 字段**，按 checkpoint 表单权威生成 canonical 文本，作为 `answer` 落库（control payload、`supplemental_inputs`、tool result 同源），执行依据与审计记录天然一致；
5. 全空且 `allow_skip=true` → 等价忽略（生成忽略固定文案，§5.4）。

**canonical 文本**（服务端生成，LLM tool result 与审计用，人可读）：

```
Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档
Q2【后续独立事项】飞书钉钉接入 AgentRunner
Q3【补充说明】（未回答）
```

- 按 form 题序输出**全部题**（含未答，输出 `（未回答）`，消除省略歧义）；选项写可读 label，多选「、」连接；自定义以「；自定义：」前缀附在同题行尾；只填自定义未选项时只写内容。
- **忽略固定文案**（整体跳过或全空且允许跳过）：`（用户跳过了本次提问，请按已知信息继续）`。

**自由文本兼容政策**（明确承诺边界）：

- `answers=null` 且 `answer` 非空的纯文本 reply **兼容放行**：不经过表单校验，直接作为 answer 交给 LLM（语义理解，clarify 现状）。此路径下 `required`/`max_select` 等约束**不强制**，属提示性约束。
- 因此严格表单校验**仅承诺结构化路径**：Web UI 一律提交结构化；渠道解析成功时提交结构化（§7.2）；解析失败回退纯文本兼容路径。

### 4.3 RunnerControl 扩展与幂等摘要兼容

- **缺省序列化兼容**：`intent()` 序列化对 `answers` 采取"未提供即不出现"（exclude_none / exclude_defaults），未携带 answers 的请求（含历史形状）序列化字节不变 → digest 不变。**"可选字段"不等于幂等兼容，必须以 fixture 证明**。
- fixture（随 schema 登记）：升级前受理的请求（无 answers）→ 升级后同 `client_request_id` 重试仍命中原控制；同键但 answers 不同的请求 → 拒绝（`IDEMPOTENCY_INPUT_MISMATCH`）。
- 新客户端连旧服务端：旧模型 `extra='forbid'` 会显式拒绝 → 必须表现为明确错误而非静默丢字段；一期以**同批发布约束**（前端与 Runner API 一起上线）+ 前端收到校验错误时给出版本不匹配提示兜底，正式能力协商（capabilities 暴露 ask 支持）列 Phase 3。
- 服务端不信任客户端 `answer`（§4.2 第 4 步），不存在"客户端自觉保持一致"的依赖。

### 4.4 传播链路（wait → snapshot → 各端）

| 环节 | 改动点 | 说明 |
|---|---|---|
| 工具 → wait | `runtime/controls.py` clarify/ask_user 分支 | `wait` 追加 `"form": {...AskForm}`（wait 是不透明 dict，引擎预期零改动，待验证） |
| wait_id 补齐 | `runtime/durable_control.py:95-110` | 复用现有 decorate_wait；`clarificationQuestions` 投影同步带表单元数据 |
| 快照白名单 | `public_view.py:48-57 _public_waiting` | 新增 `form` 必须加白名单，否则 API 不出 |
| **控制契约** | `control_contracts.py` | 新增 `answers` + `AnswerItem`；校验规则见 §4.2/§4.3 |
| **控制受理** | `control_repository.py` | 受理事务内新增表单校验（§4.2）；`supplementalInputs` 扩展持久化 form 摘要与结构化答案（§7.1 历史回显依赖） |
| 恢复回注 | `recovery.py:_apply_reply` | 答案写为 ask_user 的 tool result；结构化答案随 canonical 同源落库（改动待验证） |
| 渠道 | `src/channels/runner_agent.py:172-183` | 直返 `wait['form']` 的文字渲染（无 form 退回 question，兼容旧在途 runner） |
| legacy SSE | `legacy_stream_bridge.py:195-205` | 追加可选 `form` 字段（非 runner 路径兼容增强，Phase 3） |
| 前端类型 | `frontend/web/api/runner.ts`、`useAgent.ts` | RunnerWait 加 form；RunnerControlIntent 加 answers |

### 4.5 schema 登记与兼容

- AskForm / AnswerItem schema + 正反 fixture 落 `contracts/runtime-host/v1/`，随实现按契约第 8 节登记（字段与单位、必填缺省、错误码、幂等摘要、生产/消费样例）。
- 旧快照（无 `form`）：前端与渠道按纯文本 question 渲染（现状行为）；带 `form` 快照对旧客户端须以严格解析器验证"忽略未知字段不报错"。

## 5. 状态机与生命周期

### 5.1–5.3 wait 模型（不变项）

1. **一 ask 一 wait**：多题打包进一个 AskForm（引擎单 waiting 槽位）；"1/1"分页是 Web 对多题包的展示形态。
2. **复用 clarification 状态机**：`waiting --reply--> running`，canonical 文本回注为 tool result（复用目标，待验证）。
3. **多轮串行**：每轮 ask 新 tool_call_id → 新 wait_id；重复提交按 §5.7 错误码细分处理，不统一显示"已答复"。

### 5.4 忽略（跳过）与空提交语义

- `form.allow_skip` 控制（默认 `true`）；`false` 用于**业务确认门**：UI 不渲染忽略按钮，用户必须作答或取消整个任务。**确认门只关业务语义（如"继续/停止"），不构成执行授权**（§8）。
- 忽略实现 = reply + 忽略固定文案，作为 ask_user 的 tool result 回注；LLM 收到明确"跳过"事实后按已知信息继续并说明假设；忽略 ≠ 取消。
- 防追问循环：usage guide 约束"用户跳过后同一问题不得无新信息重复追问；连续被跳过应按合理默认值执行"。
- 部分忽略：多题包中未答的题在 canonical 中输出 `（未回答）`；`required=true` 的题空提交被服务端拒绝（§4.2 第 3 步）。
- **全空提交**：`allow_skip=true` → 等价忽略（服务端生成忽略文案）；`allow_skip=false` → `rejected`（`FORM_ANSWER_REQUIRED`），不消费 wait。避免产生空 answer 撞 `CONTROL_REPLY_REQUIRED` 的既有校验（服务端为合法空表单生成忽略文案后 answer 非空）。

### 5.5 等待与资源边界

- **默认无限期等待**（产品决策，保留）：waiting 即 park，释放 worker 与执行租约；checkpoint 持久化，服务重启可恢复，直到 reply 或 cancel。
- **资源边界如实声明**：
  - 释放：worker、执行租约、LLM 费用（不再产生推理消耗）；
  - **保留**：逻辑会话位置（session claim）——等待期间该 runner 仍占据其会话的执行位；用户新消息走现有会话队列机制（合并窗口 / 排队 / 取消标志），不会与等待任务并行执行两个循环；用户要开始新任务需按现有方式取消旧任务；
  - 等待任务数量与容量：随 `agent_runners` 行持久化，不新增配额；受会话队列与防滥用现有约束；会话删除 / 租户注销时按现有 runner 生命周期处理（waiting runner 随之取消，登记为 Phase 1 验证项）；
  - 撤权与恢复：恢复执行前（reply 受理与 `_acquire_next` 认领）沿用现有 status/积分/授权检查；**恢复前对设备、插件、授权事实的重验范围须在 Phase 1 核对现有 recovery 检查覆盖面**，缺口另行补；
  - `ask_user` 本身不持有桌面锁、外部事务等本地资源；若任务在等待前经其他机制持有了此类资源，不在本协议释放范围内（各机制自己的生命周期管理）。
- `expires_at` 字段预留（一期恒 null）；若产品后续要超时，属 Phase 3，须先冻结监管、取消与计费口径，不自动引入超时续跑。

### 5.6 作答入口与身份边界（一期收缩承诺）

- **一期 = 同源作答**：渠道产生的任务（source=wecom/dingtalk/feishu/wecom_kf）只能由**该渠道会话**作答（现有 `ChannelRunnerAgent` reply 链路）；Web 产生的任务（source=chat/web）由同一认证会话作答（Web 浏览器与桌面客户端共用登录态，现有认证已覆盖）。
- **跨入口作答（Web 答渠道任务、渠道答 Web 任务）一期不做**：现有授权按入口校验 source/actor/session 归属，同一平台 user_id 不足以授权跨入口控制；后置需先定义可信账号绑定、任务发现入口（渠道任务在 Web 会话列表的可见性）、跨入口读取/控制授权，见 §7.3。
- 已绑定、未绑定、解绑、跨租户场景一律按现有归属校验拒绝；不能仅凭 runner_id 或平台用户 ID 获得控制权限。
- §1 的"多端"承诺相应收缩为：同一 Web 登录态下的浏览器/桌面多端。

### 5.7 卡片提交状态机（control 回执驱动）

**"已答复"的权威判定 = 该次提交的 control_id 达到 `consumed` 且快照中该 wait 已消失**；waiting 消失或任何单一 409 都不得单独作为"已答复"依据。

| 状态 | 判定依据 | 展示 |
|---|---|---|
| 待作答 | 快照 `waiting.form` 存在且本端未提交 | 可编辑 |
| 提交中 | controls 请求已发出未收到响应 | 按钮禁用 |
| 已受理 | 收到受理响应 + `control_id`（accepted） | "已提交，等待应用"，继续轮询 |
| 已应用 | 该 control_id `consumed` 且快照无该 wait | 只读回显（§7.1） |
| 被拒绝 | control `rejected` / 4xx | 按错误码提示：`FORM_ANSWER_INVALID/REQUIRED` → 高亮错误题；`RUNNER_RESUME_PENDING` → "已有提交待应用，请稍候"；`CONTROL_WAIT_STALE` → 刷新快照重读；`CONTROL_EXECUTION_CLOSED` / 终态 → 任务关闭 |
| 任务关闭 | runner 终态（completed/failed/cancelled） | 只读 + 终态标注 |

- 网络不确定：**复用原 `client_request_id` 重试**（幂等命中同一 control，不产生重复提交）；不得换新键重试。
- 双端同时提交（同源多端，如两个浏览器标签页）：先到者受理，后到者按 `RUNNER_RESUME_PENDING` / `CONTROL_WAIT_STALE` 处理并刷新。
- 连续两轮 ask：第一轮卡片"已应用"后，新一轮 wait 的 form 作为新卡片出现。

## 6. Agent 侧改造

1. **新控制工具 `ask_user`**（`catalog=False`，`control_set.py` 显式装配）：
   - InputModel 只收内容字段（题/选项顺序即编号），`execute` 服务端生成 `form_id`/`question_id`/`key` 并做 §4.1 全量校验（同 present_options 校验风格，失败返回明确错误）；
   - **root 执行也产生等待**：现 `controls.py:75-84` 仅 `AgentMode.SUBAGENT` 产生 wait，ask_user 需对 MASTER/STANDALONE 同样 yield wait。功能性缺口，须回归验证 root 等待与 cancel/pause/final 流、防滥用拦截点的交互。
2. **usage guide（写入 system prompt）**：何时用 ask_user（信息缺失且候选明确 / 业务确认门 / 多项需澄清打包一次问完）；开放式问题仍用普通文本回复；非阻塞建议用 present_options；单轮任务尽量 ≤1 次提问。题型指导：互斥方案 `single`、可并集 `multi` 并显式声明；选项题默认保留自定义输入（`allow_custom_text` 默认 true），严格业务确认门才关闭；每选项带一句话 `description`；注意渠道渲染预算（§7.2），选项文本尽量精炼。
3. **存量工具处置**：`clarify` 保留兼容（旧 runner 在途 + 自由文本路径），渐进收敛；`present_options` 保留非阻塞定位，usage guide 增加分工说明。
4. **实施范围明确含**：控制受理校验（§4.2）、历史投影（§7.1）、幂等兼容 fixture（§4.3）、渠道投递失败处理（§7.2）——非"只加一个工具"。

## 7. 各端渲染

### 7.1 Web（含未来自由桌面客户端，一期）

- 新组件 `frontend/web/components/chat/QuestionCard.vue`：题 tag chip + 题干、single 渲染 radio / multi 渲染 checkbox（严格按 `type` 区分）、`allow_custom_text` 时选项下方自由输入行（占位 `custom_placeholder`）、text 题独立输入框、required 校验（"至少选一项或填自定义"）、底部"忽略 / 提交"（`allow_skip=false` 无忽略按钮）、多题分页（‹ 1/N ›）。
- 选择与自定义输入**并存不互斥**；提交一律走结构化 `answers`（`RunnerControlIntent` 扩展），不自行拼 canonical 文本。
- **提交状态按 §5.7 状态机渲染**：提交中 / 已受理 / 已应用 / 按错误码区分的被拒绝提示 / 任务关闭；网络重试复用原 `client_request_id`。
- **历史回显持久化与投影**（一期实施范围，非免费能力）：
  - 持久化：reply 受理时把 `form 摘要 + 结构化答案 + 提交事实（control_id、accepted_at）`写入 `supplemental_inputs`（既有表，扩展 JSON 结构）与 control payload——wait 消费后仍可回放；
  - 投影：`public_view` 的 `supplementalInputs` 白名单扩展对应字段；`runnerMessages.ts` 历史投影将"已答复的 AskForm"渲染为只读卡片（题目 + 用户答案 + 答复时间）；
  - 前端消息类型增加只读表单卡片类型，与待作答卡片区分；
  - **草稿不持久化**：快照恢复的是已发布的表单与已提交的答案，不含用户未提交的勾选/输入；刷新丢失草稿属预期，UI 不做本地草稿保存（如需再立项）。
- legacy SSE 路径的 form 透传为 Phase 3（一期仅 runner 主链路）。

### 7.2 第三方渠道：统一文字问答模式（一期全渠道）

**投递保障（渲染上限与分段）**：

- `ask_render.py`（渠道共享渲染层）按渠道渲染后 **UTF-8 字节**上限检查：wecom_kf / 企微应用 ≤2000B（平台 2048 留余量）、钉钉 / 飞书 ≤3800B（平台 4000 留余量）、RPA 按其 2000 字符限制。
- 超限处理（按序降级）：压缩/省略选项 `description` → 按题拆分为多条消息（**题干 + 该题选项 label 必须完整成段，选项永不跨段截断，必答题永不静默截断**）→ 单题选项文本仍超渠道上限（生成质量问题的兜底）→ 本次投递失败并标记（见下）。usage guide 同时约束 LLM 控制选项文本长度，从源头减少拆分。
- 分段发送复用各渠道现有分段与限流机制；部分段失败重试沿用 `_send_with_retry` 等；**最终失败 → 标记 `question_undelivered`（问题已持久化、runner 保持 waiting），找回路径见 wecom_kf 生命周期（各渠道同路径）**。

**wecom_kf 回复预算生命周期（明确口径，实现时与 `WeComKfReplyBudget` 现状核对）**：

- 问题投递消耗**触发本次任务的入站消息**的 5 次预算；不占用 final 预留（`reserve_for_final` 沿用）；不转图片（表单必须纯文本）。
- 用户答复后的 resume 执行属于**用户该条答复入站消息**开启的新预算周期，final 回复消耗该周期预算。
- 预算不足以投递问题（极端场景）→ 不硬撑发送，标记 `question_undelivered`。
- **未投递找回路径**：用户下次入站时，渠道处理器在把该条消息作为答案之前，先补发未投递的问题文本并提示"请回复 Q 编号或文字"（补发消耗新周期预算，该条入站**不**作为答案）；若该条入站已符合 Q 编号回复格式则直接作为答案提交，不重复补发。

**Q 编号回复协议（文本 → answers 的确定性解析）**：

- 规范格式：`Q1: 1、3；Q2: 先处理部署项`（题号必带，选项号为该题选项序号）；宽容归一化：全/半角、顿号/逗号、`Q1`/`q1`/`题1`。
- **归属规则（无歧义性）**：不带题号的编号，仅当 form **恰有一道选项题**时可解释为该题答案；否则**不得猜测**——按自由文本兼容路径透传（§4.2），不产生结构化提交。
- 否定/混合表达（"选 1，但不要执行"、"1、3，另外 file_assets 先等等"）：编号归 `selected_keys`，其余文字归该题 `text`，语义由 LLM 综合；纯否定句无法提取有效答案时走自由文本路径。
- 解析成功 → 结构化提交（享受服务端校验）；解析失败 → 纯文本兼容（`required` 等不强制，LLM 语义理解）。
- 同名 tag / 截断题干**不参与**程序匹配（仅人读），程序定位一律用题号与 `question_id`。

**各渠道发送形态**：飞书 post / 企微应用 markdown / 钉钉 sampleMarkdown / wecom_kf text / RPA SendTextAction，格式模板同 v1（题号 Q1/Q2 显式标注，多题各自编号，选项按题重新从 1 编号）。渠道端无"已答复"回执：用户在其他端（同源规则下不存在跨端，此条仅指渠道补发场景）答复后渠道侧不主动撤回已发问题文本。

### 7.3 未来机会（不在一期范围，实施前单独立任务过契约检查点）

- **渠道原生卡片**（飞书优先：interactive builder 已存在 + `card.action.trigger` 回调分支；企微 template_card 次之；钉钉 AI 卡片最后评估）：届时 AskForm→卡片渲染与卡片回调→`answers` 的映射仍以 §4 schema 为唯一协议源。
- **跨入口作答**（Web 答渠道任务等）：需先冻结可信账号绑定、任务发现入口与跨入口读取/控制授权（§5.6）。
- **提问通知的跨任务频控**、`expires_at` 超时语义（Phase 3 评估项）。

## 8. 安全与限制

- **授权澄清（按评审第 9 条）**：ask_user 只收集**业务选择**，不改变工具权限、workspace grant 或任务许可；选择"同意/继续"不扩大权限；`allow_skip=false` 的确认门是业务语义门，不是授权门；跳过不构成批准；跳过后的"按已知信息继续"仅适用于既有权限与已知事实允许的步骤，**授权缺失的步骤在恢复后仍被原有权限检查阻断**（恢复链路权限重验覆盖面列 Phase 1 核对项，见 §5.5）。
- 普通澄清 reply 不转换为许可/授权（共同契约既有约束，本设计沿用）。
- 载荷上限（§4.1）+ `answer` 沿用 1MB 上限；工具层与受理层双重校验，非法输入 rejected 不消费 wait。
- 渠道卡片/文本回调鉴权：session 映射与事件去重复用现有可信机制；回复仅等价一次 reply，不转换为任何许可。
- 渠道限流与预算沿用（wecom_kf 生命周期见 §7.2）；提问文本走正常发送路径，不新增外发通道；`form` 进快照前与 question 同等对待，不落凭证。

## 9. 分阶段实施

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道 | ask-form/answer schema + fixture（含幂等兼容 fixture）；`ask_user` 工具（服务端 id 生成 + root 等待）；`RunnerControl.answers`（omit-null 幂等）+ 受理事务内表单校验 + canonical 服务端权威生成；wait/snapshot `form` 白名单；`supplemental_inputs` 扩展 + 历史只读投影；Web QuestionCard（题型/自定义并存/忽略/分页/§5.7 状态机/结构化提交）；渠道文字渲染（渲染上限/分段/`question_undelivered` 补发路径）+ Q 编号解析 + wecom_kf 预算生命周期 | 绕过 UI 提交非法答案 rejected 不消费 wait；升级前受理/升级后同键重试幂等 fixture；合法纯文本回复兼容放行；同名 tag 多题、否定表达、多行文本、分隔符、非法编号用例；渲染超限与必答/选项不截断；预算不足与补发端到端；409 细分与 `client_request_id` 重试；root 等待回归（cancel/pause/final/防滥用）；连续两轮 ask 状态切换；刷新/重开/任务完成后历史回显；旧快照严格解析器兼容 |
| Phase 2：同源待答可达性 | `runner_waiting_input` 通知生产者（挂 park 事务，复用统一通知基础设施）；站内信兜底 + Web Push（点击回跳会话即见卡片）；会话列表「待回答」标识（范围 = Web 同源任务） | 通知到达与点击回跳端到端；同一 runner 多次提问频控；渠道任务不受影响（渠道内补发路径已覆盖） |
| Phase 3：增强项（按需） | `expires_at` 超时语义冻结（可选）；capabilities 能力协商暴露 ask 支持；legacy SSE form 透传；埋点（提问率/跳过率/响应时长） | 过期不产生孤儿计费；wire 可选字段过严格解析器兼容门 |

未来机会（单独立任务）：渠道原生卡片、跨入口作答，见 §7.3。

## 10. 评审意见落实对照

| 评审条件（三、复审通过条件） | 落实位置 |
|---|---|
| 1. 权威答案模型、服务端校验及自由文本兼容政策 | §4.2（结构化 answers 一期 + 受理校验 + canonical 服务端生成；纯文本兼容路径明确不强制表单约束） |
| 2. 无歧义的渠道多题回答规范 | §4.1（服务端 id）、§7.2（Q 编号协议、归属规则、歧义不猜测、用例清单入验收） |
| 3. 跨端身份边界及实际交付范围 | §5.6（同源作答一期承诺；跨入口后置并列明前置定义） |
| 4. 历史幂等摘要兼容方案 | §4.3（omit-null 序列化 + fixture + 新旧客户端行为） |
| 5. controls 回执驱动的卡片状态机 | §5.7（consumed+wait 消失双条件；错误码细分；幂等重试） |
| 6. 渠道完整投递、失败恢复与预算生命周期 | §7.2（渲染字节上限、分段降级、`question_undelivered` 补发、wecom_kf 三段预算口径） |

另落位：历史回显持久化 §7.1；等待资源边界 §5.5；授权澄清 §8；schema 边界与阶段统一 §4.1/§4.2/§9。

## 11. 开放问题

1. root 执行 ask_user 等待与主智能体 final_answer 流、`task_record` 语义的交互（Phase 1 首个验证项）；
2. wecom_kf "补发不作为答案"与现有"下一条消息即 reply"语义的切换，需与 `ChannelRunnerAgent` 现有 waiting 分支对齐实现（§7.2 设计已给口径）；
3. 恢复链路对撤权/设备/插件事实的重验覆盖面（§5.5，Phase 1 核对现有 recovery 检查）；
4. `required` 题在纯文本兼容路径不强制——渠道用户长期只回自由文本时是否升级为纠正提示（有消息成本，wecom_kf 尤甚）；
5. 同源多端（两个标签页）同时编辑的提示体验（§5.7 已有冲突处理，是否需要 presence 提示）。
