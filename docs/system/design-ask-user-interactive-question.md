# 结构化提问交互协议设计（ask_user / AskForm）

> 状态：v3 评审修订稿（二轮"修改后通过"意见已落实，待三审）
> 日期：2026-10-14（v1：2026-10-13 commit c93a00b9；v2：2026-10-14）
> 关联：[Runner/Desktop/Runtime 共同契约](runner-desktop-runtime-integration-contract.md)、[AgentRunner 架构](agent-application-architecture-design.md)、[Web 任务推送通知](web-task-push-notification-design.md)
> 范围声明：本文只做协议与整体规划，未开发；文中"零改动 / 零新增 API"均为**待验证的复用目标**；应用层校验、历史投影、幂等兼容与投递保障均已列入实施范围（§9）。

## 修订记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-13 | v1 | 初稿：复用 clarify 等待管道；Web 富卡片 + 渠道统一文字；忽略/无限期等待/待答通知 |
| 2026-10-14 | v2 | 一轮评审修订：结构化 answers 提前一期 + 服务端权威校验与 canonical 生成；Q 编号协议；同源作答；幂等兼容；卡片状态机；渠道投递与预算生命周期；历史回显；等待资源边界；授权澄清；schema 边界 |
| 2026-10-14 | v3 | 二轮评审修订：①入口模型显式放行 answers（与非空 answer 互斥、非 reply 禁带、旧 clarify 空回复规则不变）；②幂等摘要冻结为**客户端原始意图**（answers 仅提供时出现、显式条件序列化、不全局 exclude；canonical 只进执行载荷，永不回写请求对象）；③新增显式 `skip` 字段，冻结跳过/required/空提交判定表；④渠道投递改为段预算核算 + 四态投递状态 + 收敛路径，单题渲染上限前移为 form 生成准入校验；⑤错误契约三分（入口格式 / 表单业务 / 执行拒绝）与 errors DTO、换键规则；⑥历史持久化修正为 checkpoint 内 `ask_history`（事实修正）并关联 control 状态、未答取消也有历史；⑦渠道解析三分法（明确使用协议即不降级）与严格解析规则；⑧发布兼容最小行为（UNSUPPORTED_FIELD 可判定错误 + 前端临时 canonical 降级）、渠道上限区分平台硬上限与 adapter 配置、durable_control.py 路径修正 |

## 1. 背景与目标

参考交互（ZCode 提问卡片）：智能体在需要用户澄清时渲染结构化提问卡片——分类标签 + 题干、单选/复选选项（label + description）、选项下方自定义输入行、"忽略 / 提交"按钮、多题分页（1/1）。

1. **Web 端**（含未来自由桌面客户端，共用 Runner 快照 wire）渲染富交互提问卡片；
2. **第三方渠道**（企业微信应用 / 飞书 / 钉钉 / 微信客服 / 企微个人号 RPA）**统一采用规范格式的文字问答**（范围决策，渠道不做富卡片；飞书等原生卡片为未来机会 §7.3）；
3. **数据格式协议先行**：渠道中立核心 schema 一次定稿；**严格表单校验覆盖结构化路径**（Web 与渠道解析成功的提交）；自由文本路径为兼容路径，不强制表单约束——这是明确的产品取舍，不宣传为"所有入口均严格校验"；
4. **等待与找回**：提问在用户响应前无限期保持（不占 worker/租约，资源边界见 §5.5）；用户可跳过（loop 按已知信息继续）；同源入口提供待答标识与通知。

## 2. 现状盘点与相关事实（2026-10-13 核对，含评审修正）

### 2.1 两个既有先例

| 机制 | 文件 | 等待语义 | 渠道表现 | 缺口 |
|---|---|---|---|---|
| `clarify` | `src/tools/agent/clarify_tool.py` + `src/services/agent_runner/runtime/controls.py:75-84` | ✅ runner waiting → 用户 reply 回注为 tool result | 纯文本问题 + 纯文本回答 | 只有单个 question 字符串；仅子执行（SUBAGENT role）产生等待，root 执行调用不等待 |
| `present_options` | `src/tools/agent/present_options_tool.py` | ❌ 纯展示增强（不阻塞） | 靠 usage guide 要求 LLM 正文给编号清单兜底 | 非阻塞、无提交/忽略语义 |

**阻塞等待 + 应答回注管道已存在**（wait → snapshot → reply control → `recovery._apply_reply` 回注 tool result），本设计复用该管道并扩展载荷、校验与渲染；复用点均为待实现验证。

### 2.2 与本设计相关的既有事实（评审两轮确认）

- **controls 是异步受理**：control 有 `accepted → claimed → consumed / rejected` 生命周期；409 不只一种；waiting 消失也可能来自取消/失败。
- **入口模型校验先于受理事务**：Web 网关与 Runner API 用 `RunnerControl`（`extra='forbid'`）解析请求，reply 在 `answer` 为空且无附件时即报 `CONTROL_REPLY_REQUIRED`——**发生在控制受理事务之前**。结构化提交必须先改入口校验（§4.2/§4.3），否则合法 answers 根本进不了受理逻辑。
- **现有受理校验薄弱**：`assert_wait` 只校验等待身份与类型，`_apply_reply` 直接收纳 answer——表单校验为新增。
- **摘要与 payload 同源**：`request.digest()` 与 control payload 都来自 `request.intent()`——服务端改写请求对象（回填 canonical）会破坏幂等重试（§4.3 冻结规则以此为前提）。
- **授权按入口区分**：web/channel 会话分别校验 source、actor、session 归属；同一平台 user_id 不足以跨入口控制任务（§5.6）。
- **wecom_kf 预算**：`WeComKfReplyBudget(total=5)` 是**每条入站消息**（owner 级）对象，非跨等待持久的 runner 预算；长正文会转图片（§7.2 投递保障以此为前提）。
- **supplemental_inputs 不是独立数据库表**：当前实现在 runner `checkpoint` JSONB 内维护列表，`public_snapshot` 维护 `supplementalInputs` 投影——历史持久化方案据此修正（§7.1）。
- **`decorate_wait` 实际位于** `src/services/agent_runner/durable_control.py`（v2 误写 runtime/，已修正）。

### 2.3 渠道文字能力（区分平台硬上限与 adapter 当前配置）

| 渠道 | 发送类型 | adapter 当前分段配置 | 平台口径 |
|---|---|---|---|
| 飞书 | post | 4000 字节（**配置默认值，非官方上限实证**） | 官方上限实施时另行核对 |
| 企微应用 | markdown | 2048 字节 | 平台硬上限（cgi-bin/message/send） |
| 钉钉 | sampleMarkdown | 4000 字节（配置默认值） | 官方上限实施时核对 |
| 微信客服（wecom_kf） | text | 2048 字节（平台硬上限）；**每条入站 5 次回复预算** | 平台硬上限 |
| 企微个人号 RPA | SendTextAction | 2000 字符 | RPA 协议约定 |

渲染预算按 **adapter 当前配置**为准并留余量（§7.2）；实施时对"配置默认值"渠道补官方口径核对。

## 3. 设计原则

1. **一份中立 schema，各端投影**：AskForm / AskAnswer 在 `contracts/runtime-host/v1/` 定稿；各端为投影，不允许渠道私定问答格式。
2. **意图与执行载荷分离**：幂等摘要只基于**客户端原始意图**；canonical 文本由服务端生成、只进执行载荷（control payload 与落库），永不回写请求对象（§4.3）。
3. **双轨分离**：阻塞式提问（`ask_user`）与非阻塞式建议（`present_options`/quickOptions）语义分开、UI 可区分。
4. **渠道统一文字模式**：同一份文字问答规范；投递前做段预算核算，必答内容与选项 label 永不静默截断，失败路径必须收敛（§7.2）。
5. **同源作答**：一期任务只能由产生它的同源入口作答（§5.6）；跨入口后置。
6. **业务确认 ≠ 执行授权**：ask_user 只收集业务选择，不改变工具权限、workspace grant 或任务许可（§8）。
7. **校验承诺如实声明**：结构化路径严格校验；自由文本兼容路径不强制表单约束（产品取舍，§4.2）。
8. **契约合规**：公开 controls 仍只有 pause/resume/reply；wire 新增可选字段按契约 §7.2 做严格解析器验证，幂等摘要兼容以 fixture 证明（§4.3）。

## 4. 核心数据协议

### 4.1 AskForm（下游：问题载荷）

```jsonc
// contracts/runtime-host/v1/schemas/ask-form.schema.json（实施时落盘）
{
  "form_id": "frm_<uuid>",              // 服务端（工具执行层）生成
  "wait_id": "wait_...",                // 关联的 runner wait（durable_control.py decorate_wait 补齐）
  "title": "归档范围",                   // ≤20 字，可缺省
  "intro": "除成本治理外，以下开发已完成…", // ≤200 字，可缺省
  "questions": [                         // 1..5 题，打包进同一个 wait
    {
      "id": "q1",                        // 服务端按题序生成（^q[1-9]$），form 内唯一
      "tag": "归档范围",                  // ≤12 字，可缺省
      "prompt": "以下哪些任务移入 ideas_finished.md？",   // ≤200 字
      "type": "single | multi | text",   // 单选(radio，最多 1 项) / 多选(checkbox) / 纯自由文本
      "required": false,
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
  "allow_skip": true,                    // 是否允许整体跳过（仅约束显式 skip，见 §5.4）
  "expires_at": null                     // 预留字段，一期恒为 null（§5.5）
}
```

**标识符、准入与口径**：

- `question_id` / `key` 由工具执行层服务端生成，LLM 入参只含内容字段（顺序即编号）；渠道选项序号 = key 尾号。
- **form 生成准入校验**（渠道投递保障的前移，§7.2）：每题按"题干 + 选项 label（不含 description）"紧凑渲染后 ≤1800 UTF-8 字节（按最严格渠道单条上限留余量），超限工具层拒绝（`FORM_QUESTION_TOO_LARGE`），LLM 收错误后自行精简/拆题——从根上排除"单题永远发不出、重复补发无法收敛"的场景。总载荷 ≤8KB（UTF-8 字节）。
- 长度上限：schema 字段按**字符**计；渠道投递相关按渲染后 **UTF-8 字节**计。
- 题型语义：`single` **最多选一项**；`multi` 受 `max_select` 约束；`text` 无选项且答案不得携带选项。工具入参校验题型与选项匹配。
- 选项与自定义输入并存：可只选、只输入、或两者同时；UI 不做互斥清空。
- **等待期间表单不可变**：AskForm 随 wait 写入 checkpoint，校验以 checkpoint 中该份为准；reply 不携带、不接受修改表单。

### 4.2 AskAnswer：入口模型放行（P1-1 裁决）

**传输模型**（`control_contracts.py`，`extra='forbid'`）：

```python
class AnswerItem(BaseModel):
    question_id: str            # ^q[1-9]$，须存在于当前 form；禁止重复
    selected_keys: List[str]    # ⊆ 该题 options.key；single ≤1；multi ≤ max_select；服务端按选项序去重排序前先校验无重复（重复=错误）
    text: Optional[str]         # ≤1000 字；allow_custom_text=false 须为空；text 题不得携带 selected_keys

class RunnerControl(...):       # 现有模型追加
    answers: Optional[List[AnswerItem]] = None   # 未提供=None；显式 [] 合法（=全部未答的普通提交）
    skip: bool = False                            # 显式整体跳过，仅 reply 可用
```

**入口校验修订**（`RunnerControl` 模型级，Web 网关与 Runner API 同一模型，改一处生效）：

| 规则 | 行为 |
|---|---|
| reply 合法条件 | `answer.strip() or attachments or answers is not None or skip` —— 满其一即可过入口（原 `CONTROL_REPLY_REQUIRED` 条件扩展） |
| 互斥：`answers` 与非空 `answer` 同现 | 请求格式错误 `CONTROL_ANSWER_CONFLICT`（422），不落库 |
| 互斥：`skip=true` 与 `answers` 同现 | 请求格式错误 `CONTROL_SKIP_CONFLICT`（422） |
| 非 reply（pause/resume/内部 browser_complete）携带 answers 或 skip=true | 请求格式错误（422），非 reply 行为完全不变 |
| 旧 clarify：空 answer、无附件、无 answers、skip=false | 仍报 `CONTROL_REPLY_REQUIRED`，**行为不变** |

**受理事务内业务校验**（`control_repository` 扩展，按目标 wait 是否含 form 分流）：

1. wait 无 form（旧 clarify）→ 走原路径（answer 文本直纳），完全兼容；
2. wait 含 form → 按 §5.4 判定表处理；
3. 非法 → control 置 `rejected` + `FORM_ANSWER_INVALID`（detail 含逐题错误，§5.7 契约），wait 不消费。

**canonical 文本**（服务端生成，写入执行载荷与落库，LLM tool result 与审计用）：

```
Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档
Q2【后续独立事项】飞书钉钉接入 AgentRunner
Q3【补充说明】（未回答）
```

按 form 题序输出**全部题**（未答输出 `（未回答）`）；选项写可读 label，多选「、」；自定义以「；自定义：」前缀。**跳过文案**（`skip=true` 受理合法时）由服务端生成：`（用户跳过了本次提问，请按已知信息继续）`。

**自由文本兼容政策（产品取舍，如实声明）**：`answers=None` 且 `skip=false` 的纯文本 reply 兼容放行，不经过表单校验，`required`/`max_select`/`allow_skip=false` 在该路径**不强制**，由 LLM 语义理解（clarify 现状）。严格校验仅承诺结构化路径（Web 全部提交、渠道解析成功时）。

### 4.3 幂等摘要：客户端原始意图冻结（P1-2 裁决）

- **digest 永远基于客户端原始请求**：服务端不得在摘要计算前改写请求对象。canonical 是**服务端生成的执行载荷**，写入 control payload 与落库记录（存储结构与请求对象分离），不参与 digest。
- **序列化规则（显式条件，不用全局 exclude）**：`intent()` 构造 dict 时仅对新增字段做"未提供即不含该键"（`answers is not None` 才加入、`skip is True` 才加入）；**其余字段（含既有的 null、空数组）序列化行为与现版完全一致**——禁止对整个模型应用 `exclude_none`/`exclude_defaults`，避免移除旧字段破坏历史摘要。
- **answers 与 answer 互斥**（§4.2）使"结构化请求中 answer 是否计入摘要"不再存在歧义：结构化请求的 `answer` 必为空且恒为空，重试时也为空。
- fixture（随 schema 登记）：①首次受理（answers + 空 answer）落库 canonical 后，同键原请求重试命中原控制；②升级前请求（纯文本）升级后同键重试成功；③同键修改 answers → `IDEMPOTENCY_INPUT_MISMATCH` 拒绝；④answers 缺省请求的 digest 与升级前逐字节一致。
- 新客户端连旧服务端：旧模型 `extra='forbid'` 显式拒绝（§8 最小兼容行为）；正式能力协商列 Phase 3。

### 4.4 传播链路（wait → snapshot → 各端）

| 环节 | 改动点 | 说明 |
|---|---|---|
| 工具 → wait | `runtime/controls.py` clarify/ask_user 分支 | `wait` 追加 `"form"`（wait 是不透明 dict，引擎预期零改动，待验证） |
| wait_id 补齐 | `src/services/agent_runner/durable_control.py` | 复用 decorate_wait；`clarificationQuestions` 投影带表单元数据 |
| 快照白名单 | `public_view.py:48-57` | `form` 加白名单 |
| **入口契约** | `control_contracts.py` | 新增 `answers`/`skip` + §4.2 入口校验修订（**先于受理事务生效**） |
| **控制受理** | `control_repository.py` | 表单业务校验（§5.4 判定表）；rejected 落库 + errors 明细（§5.7） |
| **幂等** | `intent()/digest()` | §4.3 显式条件序列化 |
| 恢复回注 | `recovery.py:_apply_reply` | canonical 作为 tool result 回注（复用目标，待验证） |
| 历史 | checkpoint `ask_history`（§7.1） | park 登记、reply/终态更新，非独立表 |
| 渠道 | `src/channels/runner_agent.py:172-183` | `wait['form']` 文字渲染（无 form 退回 question） |
| 前端类型 | `runner.ts`、`useAgent.ts` | RunnerWait.form；RunnerControlIntent.answers/skip |

### 4.5 schema 登记与兼容

AskForm / AnswerItem / errors DTO schema + 正反 fixture 落 `contracts/runtime-host/v1/`，按契约第 8 节登记（字段与单位、必填缺省、错误码、幂等摘要、生产/消费样例）。旧快照（无 `form`）按纯文本渲染；带 `form` 快照对旧客户端以严格解析器验证"忽略未知字段不报错"。

## 5. 状态机与生命周期

### 5.1–5.3 wait 模型（不变项）

1. **一 ask 一 wait**：多题打包一个 AskForm；"1/1"分页是 Web 展示形态。
2. **复用 clarification 状态机**：`waiting --reply--> running`（复用目标，待验证）。
3. **多轮串行**：每轮新 tool_call_id → 新 wait_id；重复提交按 §5.7 错误契约处理。

### 5.4 提交判定表（P1-3 裁决：整体跳过、required、空提交唯一判定）

wait 含 form 时，受理事务内按**且只按**下表判定（顺序即优先级）：

| 提交形态 | 判定 | 结果 |
|---|---|---|
| `skip=true` | `form.allow_skip` 为 false | rejected `FORM_SKIP_NOT_ALLOWED`，wait 不消费 |
| `skip=true` | `allow_skip=true` | **服务端权威跳过**：生成忽略文案为 answer，wait 消费，LLM 按已知信息继续 |
| `answers` 提交（含 `[]`） | 任一题 `question_id` 不存在 / 重复 / key 非法 / 超上限 / text 题带选项 / `allow_custom_text=false` 带 text | rejected `FORM_ANSWER_INVALID`（逐题明细） |
| `answers` 提交 | **遍历原 form 全部题**（省略的题=未答），存在 `required=true` 题未满足"选中或非空自定义" | rejected `FORM_ANSWER_INVALID`（指明缺失题） |
| `answers` 提交 | 全部题未答（含 `answers=[]`）且无 required 题被违反 | **合法普通提交**：canonical 全部输出 `（未回答）`，wait 消费 |
| `answers` 提交 | 部分未答 | 合法：未答题输出 `（未回答）` |
| 纯文本（`answers=None, skip=false`） | 兼容路径 | answer 直纳，不做表单校验 |

- **跳过语义唯一入口 = 显式 `skip`**：Web 忽略按钮、渠道精确文本"跳过"（§7.2）都提交 `skip=true`；判定顺序保证 `required × allow_skip` 组合唯一结果（先 skip 后 required）。
- v2 的"忽略 = reply + 固定文案"作废：忽略文案由服务端在 skip 路径生成，Web/渠道不自行拼装。
- **诚实边界**：`allow_skip=false` 在结构化路径（含渠道精确"跳过"映射）强制；自由文本路径不可强制（用户写"我不想选了"会作为文本透传，LLM 按 usage guide 处理）——产品取舍，不宣传为所有入口不可绕过。
- LLM 视角：跳过=忽略文案；全空提交=全 `（未回答）`——两者都促使 LLM 按已知信息继续，语义可区分（跳过是明确表态，空提交是未表态）。

### 5.5 等待与资源边界

- **默认无限期等待**（产品决策）：waiting 即 park，释放 worker 与执行租约；checkpoint 持久化，重启可恢复。
- 资源边界如实声明：释放 worker、执行租约、LLM 推理消耗；**保留逻辑会话位置（session claim）**——等待 runner 仍占会话执行位，新消息走现有会话队列（合并/排队/取消）；开始新任务需按现有方式取消旧任务。等待任务随 `agent_runners` 行持久化，不新增配额；会话删除/租户注销时 waiting runner 按现有生命周期取消（Phase 1 验证项）。
- 恢复前（reply 受理与认领）沿用现有 status/积分/授权检查；**恢复前对设备、插件、授权事实的重验覆盖面在 Phase 1 核对**，缺口另补。ask_user 不持有桌面锁/外部事务；任务经其他机制持有的资源不属本协议释放范围。
- `expires_at` 预留（一期恒 null）；超时语义属 Phase 3，先冻结监管、取消与计费口径。

### 5.6 作答入口与身份边界（一期收缩承诺）

- **一期 = 同源作答**：渠道任务由该渠道会话作答（现有 `ChannelRunnerAgent` reply 链路）；Web 任务由同一认证会话作答（浏览器与桌面客户端共用登录态）。
- **跨入口作答一期不做**（授权按入口校验，user_id 不足以授权）；后置前置条件见 §7.3。已绑定/未绑定/解绑/跨租户一律按现有归属校验拒绝。
- §1 的"多端"= 同一 Web 登录态下的浏览器/桌面多端。

### 5.7 卡片状态机与错误契约（P1-5 / P2-5 裁决）

**错误三分契约**：

| 类别 | 发生点 | 返回行为 | 落库 |
|---|---|---|---|
| 请求格式错误 | 入口模型（网关/API 解析） | 同步 422 + 机器可判定错误码（`CONTROL_REPLY_REQUIRED` / `CONTROL_ANSWER_CONFLICT` / `CONTROL_SKIP_CONFLICT` / `UNSUPPORTED_FIELD: answers` 等），**无 control_id** | 否 |
| 表单业务错误 | 受理事务内 | 同步 422 + `errors` 明细 DTO；**创建 rejected control 落库并返回 control_id**；wait 不消费 | 是 |
| 受理后执行拒绝 | recovery/执行阶段（罕见） | 异步，经快照/control 状态呈现 | 是 |

**errors DTO**：`{errors: [{question_id, code, message}]}`（支持多错误）；公开投影（public_control/快照白名单）扩展该字段，前端据此逐题高亮。

**重试与换键规则**：

- 网络不确定 / 拒绝响应丢失：**复用原 `client_request_id` 重试**——rejected control 已落库时，同键重放**返回原拒绝结果**（幂等，不覆盖原意图、不产生第二个 control）；
- 用户**修改答案后重新提交：必须换新 `client_request_id`**（原意图不可变）；换键后正常受理。

**卡片状态机**（"已答复/已应用"的权威判定 = 该次提交的 control_id `consumed` 且快照中该 wait 已消失；单一条件不足为凭）：

| 状态 | 判定依据 | 展示 |
|---|---|---|
| 待作答 | 快照 `waiting.form` 存在且本端无在途提交 | 可编辑 |
| 提交中 | controls 请求已发出未收到响应 | 按钮禁用 |
| 已受理 | 收到响应 + control_id（accepted） | "已提交，等待应用" |
| 已应用 | control `consumed` 且快照无该 wait | 只读回显（§7.1） |
| 被拒绝 | 422 errors（逐题高亮）/ `RUNNER_RESUME_PENDING`（已有提交待应用）/ `CONTROL_WAIT_STALE`（刷新重读）/ `CONTROL_EXECUTION_CLOSED`（任务关闭） | 按错误码区分，不统一显示"已答复" |
| 任务关闭 | runner 终态 | 只读 + 终态标注 |

- 双端同时提交（同源多端）：先到者受理，后到者按 `RUNNER_RESUME_PENDING` / `CONTROL_WAIT_STALE` 处理并刷新；**本端无提交上下文时（另一标签页作答、刷新后），卡片状态一律以快照 + controls 查询重建**，不依赖本地内存。
- 连续两轮 ask：第一轮"已应用"后，新 wait 的 form 作为新卡片出现。

## 6. Agent 侧改造

1. **新控制工具 `ask_user`**（`catalog=False`，`control_set.py` 装配）：InputModel 只收内容字段，`execute` 服务端生成 `form_id`/ids，执行 §4.1 全量校验（含单题渲染准入 `FORM_QUESTION_TOO_LARGE`）。
2. **root 执行也产生等待**：现 `controls.py:75-84` 仅 SUBAGENT 产生 wait，须回归验证 root 等待与 cancel/pause/final、防滥用拦截点交互（Phase 1 首个验证项）。
3. **usage guide**：何时用 ask_user；开放式问题用普通文本；非阻塞建议用 present_options；单轮 ≤1 次提问；题型显式声明；选项题默认保留自定义输入；每选项带一句 description；**注意渲染预算（单题紧凑 ≤1800B）**，选项文本精炼。
4. **存量工具**：`clarify` 保留兼容（旧 runner 在途 + 自由文本路径）；`present_options` 保留非阻塞定位。
5. 实施范围明确含：入口校验修订、受理校验、幂等 fixture、历史投影、投递保障——非"只加一个工具"。

## 7. 各端渲染

### 7.1 Web（含未来自由桌面客户端，一期）

- 新组件 `QuestionCard.vue`：题型渲染（single radio / multi checkbox）、`allow_custom_text` 自由输入行、text 独立输入框、required 前端预检（"至少选一项或填自定义"）、忽略/提交（`allow_skip=false` 无忽略）、多题分页。
- **提交一律结构化**（`answers` 或 `skip=true`），不自行拼 canonical；canonical 由服务端生成。
- 状态按 §5.7 状态机渲染；重试复用原键，修改后重提交换新键；本端上下文丢失以快照 + controls 查询重建。
- **历史持久化（事实修正后的方案）**：
  - 位置：**runner `checkpoint` JSONB 内新增 `ask_history[]`**（非独立数据库表；`supplemental_inputs` 现状即 checkpoint 列表，沿用同一载体）。park 时即登记条目（form 快照、`status=待作答`）——**不只在 reply 时保存**；reply 受理更新为 `submitted(control_id, accepted_at)`，恢复消费更新为 `applied`；rejected/cancelled/终态各自更新。`public_snapshot` 投影只读摘要（白名单扩展）。
  - 历史渲染关联 **control 状态与 applied 事实**：`已提交 ≠ 已应用`；"已提交（待应用）/已应用/被拒/未答已取消"分别呈现，与 §5.7 状态机一致。
  - 未答即取消的任务保留问题历史（`待作答 → 已取消`）。
  - **草稿不持久化**：快照恢复的是已发布表单与已提交答案，不含未提交勾选/输入；刷新丢草稿属预期。
- legacy SSE form 透传为 Phase 3。

### 7.2 第三方渠道：统一文字问答（一期全渠道，P1-4 裁决：投递必须收敛）

**form 生成准入（根除"永远发不出"）**：单题紧凑渲染 ≤1800B 由工具层准入保证（§4.1），渠道侧不存在单题超上限场景；剩余风险只有"多题分段 × 渠道预算"。

**投递前核算（发送前，不盲发）**：

1. `ask_render.py` 按渠道渲染并计算段数 N（按题拆段：题干 + 该题选项 label 完整成段，选项永不跨段截断；description 首轮即按预算压缩/省略）；
2. 核算当前可用预算 A（wecom_kf：本周期 5 次扣除 verbose 等占用；**提问轮次不再为 final 预留——本周期响应即提问文本**，实施时与 `WeComKfReplyBudget`/`reserve_for_final` 现状核对，如必须预留则在 A 中扣除）；
3. 决策：`N ≤ A` → 投递；`N > A` → **紧凑模式重渲染**（去 description、并题压缩提示行）再核算；仍 `N > A` → **不投递**，登记 `delivery=pending`，等用户下次入站补发（新周期预算通常足够）。

**投递状态（替代 v2 的布尔标记，按 wait_id 登记，存渠道会话元数据，随 wait 消费清理）**：

| 状态 | 含义 | 后续 |
|---|---|---|
| `pending` | 核算未通过，未发送 | 用户下次入站时补发 |
| `partial` | 部分段已确认发送 | **按已登记段续传剩余段**（不整包重发、不重复已发内容）；登记含已确认段号、失败原因 |
| `delivered` | 全部段确认 | 无 |
| `blocked` | 确定性无法完成 | 终止自动补发，转引导（见下） |

**收敛规则（防永久补发循环）**：

- 补发时同样先核算再续传；**同一 form 连续 2 次入站仍无法完整投递 → `blocked`**，终止自动补发，发送引导文本："问题较长无法在此完整发送，请打开网页版查看并回答"；
- 用户回复"简化"（精确匹配）→ 服务端生成极简版（仅题干 + 编号选项，去一切提示行）重投一次；仍失败 → 维持 `blocked` + 引导 web；
- `blocked` 期间用户任何 Q 编号格式的答复照常受理（Web 端表单始终可用，不受渠道投递影响）。
- **答案受理不被投递状态阻断**：投递不完整时用户仍可凭已见部分作答；未见 required 题 → `FORM_ANSWER_INVALID` 明细指出缺失题，渠道侧把错误说明**并入该轮响应**（不单独发纠正消息耗预算）。

**补发与入站消息的关系（不吞用户意图）**：

- 入站消息命中隐藏命令（"清空会话"/"新会话"等）→ 走既有命令拦截，取消任务，ask_history 记"已取消"；
- 入站可解析为答案（Q 编号格式，或唯一选项题的编号）→ 直接受理，不补发；
- 其余情形 → 先补发/续传（该条消息**不**作为答案），提示"请回复 Q 编号或文字"。

**Q 编号回复协议（P2-7 裁决：三分法，协议使用即不降级）**：

| 输入特征 | 分类 | 处理 |
|---|---|---|
| 无编号协议特征的自然语言 | 自由文本 | 兼容路径透传（不强制表单约束） |
| 明确使用协议（出现题号）但含非法项：选项号越界（`Q1: 9`）、重复题号、重复选项号、text 题带选择、未知题号 | **协议错误** | **不降级**：拒绝提交 + 纠正提示（指明非法项）；纠正提示是对该条入站的响应（占当次预算 1 次，wecom_kf 也发）；连续第 2 次协议错误起降级为自由文本透传（防纠错循环） |
| 混合（部分题可解析、部分非法） | 协议错误 | **整单拒绝** + 纠正提示（列出明细），不静默丢掉可解析部分 |

- 严格解析规则：重复 `question_id` 禁止；`selected_keys` 重复即错误（不去重吞掉）；服务端受理前按选项序规范化排序；**按行解析**——以题号开头的行开启新题答案，非题号开头的行视为上一题 text 续行（正文中的 "Q2:" 片段不误判）；`required` 校验遍历整个原 form（§5.4）。
- 解析成功 → 结构化提交；协议错误 → 纠正；自然语言 → 兼容路径。
- 渠道发送形态：飞书 post / 企微 markdown / 钉钉 sampleMarkdown / wecom_kf text / RPA text，模板带显式题号 Q1/Q2，选项按题从 1 编号。

### 7.3 未来机会（不在一期范围，实施前单独立任务过契约检查点）

渠道原生卡片（飞书 interactive + `card.action.trigger` 优先）；跨入口作答（前置：可信账号绑定、任务发现入口、跨入口读取/控制授权）；提问通知跨任务频控；`expires_at` 超时语义（Phase 3 评估）。

## 8. 安全、限制与发布兼容

- **授权澄清**：ask_user 只收集业务选择，不改变工具权限/workspace grant/任务许可；"同意"不扩权；`allow_skip=false` 是结构化路径的业务门（§5.4 已声明边界）；跳过不构成批准；授权缺失的步骤恢复后仍被原有权限检查阻断（恢复链路重验覆盖面 Phase 1 核对）。
- 普通澄清 reply 不转换为许可/授权（契约既有约束沿用）。
- 载荷上限 + `answer` 沿用 1MB；入口与受理双层校验，非法输入不消费 wait。
- 回调鉴权与去重复用现有可信机制；回复仅等价一次 reply；渠道限流与预算沿用（§7.2 生命周期）。
- **发布兼容（P2-8 裁决：不依赖同批上线）**：
  - 新前端连旧服务端：answers 被 `extra='forbid'` 拒绝，错误含可判定标志（`UNSUPPORTED_FIELD: answers`）——前端据此显示"服务端版本较旧，请稍后重试"，**不得与表单业务错误混淆**（错误类别不同：格式 vs 业务）；
  - 滚动/回滚窗口的降级：检测到服务端不支持结构化时，前端**临时回退为客户端生成 canonical 文本提交**（纯文本兼容路径，服务端权威校验在该窗口不生效）——明确标注为回滚窗口专用兼容，非长期路径；已存在的 form wait 在回滚后仍可经文本路径答复（旧服务端把 answer 当文本）；
  - 渠道上限按 §2.3 口径（adapter 配置为准，配置默认值渠道实施时核对官方上限）。

## 9. 分阶段实施

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道 | schema/fixture（含幂等四例 §4.3）；`ask_user`（服务端 id + root 等待 + 生成准入 FORM_QUESTION_TOO_LARGE）；入口模型修订（answers/skip 放行与互斥、非 reply 禁带、旧 clarify 不变）；受理判定表（§5.4）+ errors DTO + rejected 落库与原键重放；canonical 服务端生成；wait/snapshot 白名单；checkpoint `ask_history` + 历史只读投影（含未答取消）；Web QuestionCard（状态机/换键规则/上下文重建）；渠道渲染与投递核算（段数×预算、紧凑模式、四态状态、续传、blocked 收敛、简化指令、补发不吞命令）；Q 编号三分法解析 | 入口：answers-only 成功、answers=[] 进入判定、旧 clarify 空回复仍拒、非 reply 带 answers 拒；幂等四例；判定表全组合（required×allow_skip、部分空、全空、skip）；协议错误不降级（Q1: 9、重复、混合）；多错误明细与逐题高亮；拒绝响应丢失原键重放返回原拒绝；换键修改后受理；五题多段/预算不足/部分成功续传/连续失败 blocked/简化重投；未答取消历史；双端提交与刷新重建；root 等待回归；旧快照严格解析器兼容 |
| Phase 2：同源待答可达性 | `runner_waiting_input` 通知生产者（挂 park 事务）；站内信 + Web Push；会话列表「待回答」标识（Web 同源任务） | 通知到达与回跳；多次提问频控；渠道不受影响 |
| Phase 3：增强项（按需） | `expires_at` 语义冻结；capabilities 能力协商；legacy SSE form 透传；埋点 | 过期不产生孤儿计费；严格解析器兼容门 |

未来机会（单独立任务）：渠道原生卡片、跨入口作答（§7.3）。

## 10. 评审意见落实对照（二轮）

| 复审通过条件 | 落实位置 |
|---|---|
| 1. answers-only 请求可通过入口模型 | §4.2（reply 合法条件扩展、互斥规则、非 reply 禁带、旧 clarify 不变） |
| 2. 原始意图与服务端 canonical 的摘要关系 | §4.3（digest=客户端原始意图；canonical 只进执行载荷；显式条件序列化禁全局 exclude；fixture 四例） |
| 3. 整体跳过、required、空提交唯一判定 | §5.4（显式 skip 字段 + 判定表 + 优先级顺序；Web/渠道 skip 均结构化；自由文本边界如实声明） |
| 4. 渠道投递失败收敛 | §7.2（生成准入根除单题超限；段×预算核算；四态状态；续传；blocked 终止 + 简化指令；补发不吞命令） |
| 5. 错误明细与 rejected 返回契约 | §5.7（错误三分、errors DTO、落库规则、原键重放返回原拒绝、修改换键） |
| 6. 历史持久化事实修正与"已应用"事实 | §7.1（checkpoint `ask_history`、park 即登记、control 状态关联、submitted≠applied、未答取消） |
| 7. 解析"非法输入不降级"边界 | §7.2（三分法、协议错误纠正、重复/混合规则、按行解析、required 遍历全 form） |
| 8. 发布兼容与渠道上限口径 | §8（UNSUPPORTED_FIELD 可判定、回滚窗口临时 canonical 降级）、§2.3（平台硬上限 vs adapter 配置）、§2.2（durable_control.py 路径修正） |

## 11. 开放问题

1. root 执行 ask_user 等待与 final_answer 流、`task_record` 语义交互（Phase 1 首个验证项）；
2. wecom_kf "补发不作为答案"与 `ChannelRunnerAgent` 现有 waiting 分支的实现对齐；
3. 恢复链路对撤权/设备/插件事实的重验覆盖面核对；
4. 渠道用户长期只回自由文本时 `required` 无法强制的体验补偿（Phase 2 通知/badge 是否缓解）；
5. "简化"指令是否扩展为通用渠道指令（与现有 hidden_commands 机制的关系）。
