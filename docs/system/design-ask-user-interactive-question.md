# 结构化提问交互协议设计（ask_user / AskForm）

> 状态：v4 评审修订稿（三轮"修正后可进入实施"意见已落实）
> 日期：2026-10-14（v1：2026-10-13 commit c93a00b9；v2/v3：2026-10-14）
> 关联：[Runner/Desktop/Runtime 共同契约](runner-desktop-runtime-integration-contract.md)、[AgentRunner 架构](agent-application-architecture-design.md)、[Web 任务推送通知](web-task-push-notification-design.md)
> 范围声明：本文只做协议与整体规划，未开发；文中"零改动 / 零新增 API"均为**待验证的复用目标**；应用层校验、历史投影、幂等兼容与投递保障均已列入实施范围（§9）。

## 修订记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-13 | v1 | 初稿：复用 clarify 等待管道；Web 富卡片 + 渠道统一文字；忽略/无限期等待/待答通知 |
| 2026-10-14 | v2 | 一轮评审修订：结构化 answers 提前一期 + 服务端权威校验与 canonical 生成；Q 编号协议；同源作答；幂等兼容；卡片状态机；渠道投递与预算生命周期；历史回显；等待资源边界；授权澄清；schema 边界 |
| 2026-10-14 | v3 | 二轮评审修订：入口模型放行 answers；摘要冻结为客户端原始意图；显式 skip 判定表；渠道投递核算与四态状态；错误契约三分；checkpoint ask_history；解析三分法；发布兼容 |
| 2026-10-14 | v4 | 三轮评审修订：①blocked 出口全部收回原渠道，删除"引导 Web 作答"与跨入口承诺冲突；②补发分支按投递状态收窄，自然语言答案永不吞（delivered 无任何拦截，pending 才可判定"非答案"，partial 走自由文本兼容）；③发布兼容改走既有 capabilities 能力发现（前端缓存、不逐次探测），发布顺序约束 + 网络不明不降级只重试，删除对旧服务端新错误码的依赖；④删除全局单题字节准入门，渠道预算只在投递渲染时生效（schema 字符上限已隐含单题必进单段）；⑤无害输入归一化（重复选项/大小写/空白/分隔符），删除累计错误次数状态；⑥新增"实施与验证边界"节，只保留必要限制 |

## 1. 背景与目标

参考交互（ZCode 提问卡片）：智能体在需要用户澄清时渲染结构化提问卡片——分类标签 + 题干、单选/复选选项（label + description）、选项下方自定义输入行、"忽略 / 提交"按钮、多题分页（1/1）。

1. **Web 端**（含未来自由桌面客户端，共用 Runner 快照 wire）渲染富交互提问卡片；
2. **第三方渠道**（企业微信应用 / 飞书 / 钉钉 / 微信客服 / 企微个人号 RPA）**统一采用规范格式的文字问答**（范围决策，渠道不做富卡片；飞书等原生卡片为未来机会 §7.3）；
3. **数据格式协议先行**：渠道中立核心 schema 一次定稿；**严格表单校验覆盖结构化路径**（Web 与渠道解析成功的提交）；自由文本路径为兼容路径，不强制表单约束——明确的产品取舍，不宣传为"所有入口均严格校验"；
4. **等待与找回**：提问在用户响应前无限期保持（不占 worker/租约，资源边界见 §5.5）；用户可跳过（loop 按已知信息继续）；同源入口提供待答标识与通知。

## 2. 现状盘点与相关事实（2026-10-13 核对，含评审修正）

### 2.1 两个既有先例

| 机制 | 文件 | 等待语义 | 渠道表现 | 缺口 |
|---|---|---|---|---|
| `clarify` | `src/tools/agent/clarify_tool.py` + `src/services/agent_runner/runtime/controls.py:75-84` | ✅ runner waiting → 用户 reply 回注为 tool result | 纯文本问题 + 纯文本回答 | 只有单个 question 字符串；仅子执行（SUBAGENT role）产生等待，root 执行调用不等待 |
| `present_options` | `src/tools/agent/present_options_tool.py` | ❌ 纯展示增强（不阻塞） | 靠 usage guide 要求 LLM 正文给编号清单兜底 | 非阻塞、无提交/忽略语义 |

**阻塞等待 + 应答回注管道已存在**（wait → snapshot → reply control → `recovery._apply_reply` 回注 tool result），本设计复用该管道并扩展载荷、校验与渲染；复用点均为待实现验证。

### 2.2 与本设计相关的既有事实

- **controls 是异步受理**：control 有 `accepted → claimed → consumed / rejected` 生命周期；waiting 消失也可能来自取消/失败。
- **入口模型校验先于受理事务**：Web 网关与 Runner API 用 `RunnerControl`（`extra='forbid'`）解析请求，reply 在 `answer` 为空且无附件时即报 `CONTROL_REPLY_REQUIRED`。结构化提交必须先改入口校验（§4.2）。
- **摘要与 payload 同源**：`request.digest()` 与 control payload 都来自 `request.intent()`——服务端改写请求对象会破坏幂等重试（§4.3）。
- **旧服务端模型错误统一返回 `INVALID_REQUEST`**：新错误码无法依赖旧部署返回，兼容判定不能建立在旧服务端的新错误标志上（§8 据此改走能力发现）。
- **授权按入口区分**：web/channel 会话分别校验 source、actor、session 归属；同一平台 user_id 不足以跨入口控制任务（§5.6）。
- **wecom_kf 预算**：`WeComKfReplyBudget(total=5)` 是**每条入站消息**（owner 级）对象；长正文会转图片（§7.2 投递保障以此为前提）。
- **supplemental_inputs 不是独立数据库表**：在 runner `checkpoint` JSONB 内维护列表，`public_snapshot` 维护投影——历史持久化方案据此设计（§7.1）。
- **`decorate_wait` 位于** `src/services/agent_runner/durable_control.py`。

### 2.3 渠道文字能力（区分平台硬上限与 adapter 当前配置）

| 渠道 | 发送类型 | adapter 当前分段配置 | 平台口径 |
|---|---|---|---|
| 飞书 | post | 4000 字节（**配置默认值，非官方上限实证**） | 实施时核对 |
| 企微应用 | markdown | 2048 字节 | 平台硬上限 |
| 钉钉 | sampleMarkdown | 4000 字节（配置默认值） | 实施时核对 |
| 微信客服（wecom_kf） | text | 2048 字节（平台硬上限）；**每条入站 5 次回复预算** | 平台硬上限 |
| 企微个人号 RPA | SendTextAction | 2000 字符 | RPA 协议约定 |

渲染预算按 **adapter 当前配置**为准并留余量；"配置默认值"渠道实施时核对官方口径。

## 3. 设计原则

1. **一份中立 schema，各端投影**；不允许渠道私定问答格式。
2. **意图与执行载荷分离**：幂等摘要只基于客户端原始意图；canonical 由服务端生成、只进执行载荷（§4.3）。
3. **双轨分离**：阻塞式 `ask_user` 与非阻塞式 `present_options`/quickOptions 语义分开、UI 可区分。
4. **渠道统一文字模式**；投递前核算预算，选项 label 与题干永不静默截断，失败路径必须收敛且**出口全部在原渠道**（§7.2）。
5. **同源作答**：一期任务只能由产生它的同源入口作答（§5.6）。
6. **业务确认 ≠ 执行授权**（§8）。
7. **校验承诺如实声明**：结构化路径严格校验；自由文本兼容路径不强制表单约束（产品取舍）。
8. **只保留必要限制**（三轮评审实施原则）：新增检查须说明其防止的具体错误及现有机制为何不足；无害格式差异归一化而非拒绝；与入口无关的渠道限制不前移为生成门槛；复用既有受理事务、快照与授权机制，不为同一规则增加多次网络往返（§6、§9）。
9. **契约合规**：公开 controls 仍只有 pause/resume/reply；wire 可选字段按契约 §7.2 严格解析器验证，幂等兼容以 fixture 证明。

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

**标识符与载荷口径（P2-4 修订：渠道限制不前移为生成门槛）**：

- `question_id` / `key` 由工具执行层服务端生成，LLM 入参只含内容字段（顺序即编号）；渠道选项序号 = key 尾号。
- 工具层准入校验仅一条：**总载荷 ≤8KB（UTF-8 字节）**。**不设单题渲染字节准入门**——schema 字符上限已隐含单题紧凑渲染必然满足最严格渠道单条预算（题干 ≤200 字 ≈ ≤600B + 选项 label ≤40 字 × ≤8 项 ≈ ≤1.1KB，合计 <1.8KB < wecom_kf/企微的 2048B 单条），Web/Desktop 表单不因最弱渠道受限；渠道字节预算只在**渠道投递渲染时**生效（§7.2）。
- 长度上限：schema 字段按**字符**计；渠道投递相关按渲染后 **UTF-8 字节**计。
- 题型语义：`single` **最多选一项**；`multi` 受 `max_select` 约束；`text` 无选项且答案不得携带选项。工具入参校验题型与选项匹配。
- 选项与自定义输入并存：可只选、只输入、或两者同时；UI 不做互斥清空。
- **等待期间表单不可变**：AskForm 随 wait 写入 checkpoint，校验以 checkpoint 中该份为准；reply 不携带、不接受修改表单。

### 4.2 AskAnswer：入口模型与归一化

**传输模型**（`control_contracts.py`，`extra='forbid'`）：

```python
class AnswerItem(BaseModel):
    question_id: str            # ^q[1-9]$（大小写不敏感，受理时归一为小写）；须存在于当前 form
    selected_keys: List[str]    # ⊆ 该题 options.key；single ≤1；multi ≤ max_select；**重复 key 服务端去重归一（按一次计），不拒绝**
    text: Optional[str]         # ≤1000 字；allow_custom_text=false 须为空；text 题不得携带 selected_keys

class RunnerControl(...):       # 现有模型追加
    answers: Optional[List[AnswerItem]] = None   # 未提供=None；显式 [] 合法（=全部未答的普通提交）
    skip: bool = False                            # 显式整体跳过，仅 reply 可用
```

**入口校验修订**（`RunnerControl` 模型级，Web 网关与 Runner API 同一模型）：

| 规则 | 行为 |
|---|---|
| reply 合法条件 | `answer.strip() or attachments or answers is not None or skip` —— 满其一即过入口（原 `CONTROL_REPLY_REQUIRED` 条件扩展） |
| 互斥：`answers` 与非空 `answer` 同现 | 请求格式错误 `CONTROL_ANSWER_CONFLICT`（422），不落库 |
| 互斥：`skip=true` 与 `answers` 同现 | 请求格式错误 `CONTROL_SKIP_CONFLICT`（422） |
| 非 reply（pause/resume/内部 browser_complete）携带 answers 或 skip=true | 请求格式错误（422），非 reply 行为完全不变 |
| 旧 clarify：空 answer、无附件、无 answers、skip=false | 仍报 `CONTROL_REPLY_REQUIRED`，**行为不变** |

**归一化优先（P2-5：无害差异不产生纠错轮次）**：answers 提交先归一化再校验——`question_id` 大小写归一、前后空白剥离、`selected_keys` 重复 key 去重（按一次计）；同题重复提交且答案一致亦合并。**仅对无法确定解释的输入拒绝**：未知 `question_id`、越界 key、同题重复且答案矛盾、text 题带选项、`allow_custom_text=false` 带 text、超 `max_select`。**不维护"累计错误次数"状态**，输入行为只由输入本身决定。

**受理事务内业务校验**（`control_repository` 扩展，按目标 wait 是否含 form 分流）：

1. wait 无 form（旧 clarify）→ 原路径（answer 文本直纳），完全兼容；
2. wait 含 form → 按 §5.4 判定表处理（先归一化）；
3. 非法 → control 置 `rejected` + `FORM_ANSWER_INVALID`（detail 含逐题错误，§5.7 契约），wait 不消费。

**canonical 文本**（服务端生成，写入执行载荷与落库）：

```
Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档
Q2【后续独立事项】飞书钉钉接入 AgentRunner
Q3【补充说明】（未回答）
```

按 form 题序输出**全部题**（未答输出 `（未回答）`）；选项写可读 label，多选「、」；自定义以「；自定义：」前缀。**跳过文案**（`skip=true` 受理合法时）由服务端生成：`（用户跳过了本次提问，请按已知信息继续）`。

**自由文本兼容政策（产品取舍，如实声明）**：`answers=None` 且 `skip=false` 的纯文本 reply 兼容放行，不经过表单校验，`required`/`max_select`/`allow_skip=false` 在该路径**不强制**，由 LLM 语义理解（clarify 现状）。严格校验仅承诺结构化路径。

### 4.3 幂等摘要：客户端原始意图冻结

- **digest 永远基于客户端原始请求**；canonical 是服务端生成的执行载荷，写入 control payload 与落库记录（存储结构与请求对象分离），不参与 digest。
- **序列化规则（显式条件，不用全局 exclude）**：`intent()` 构造 dict 时仅对新增字段做"未提供即不含该键"；其余字段（含既有 null、空数组）序列化与现版逐字节一致——禁止对整个模型应用 `exclude_none`/`exclude_defaults`。
- `answers` 与 `answer` 互斥使摘要歧义消失：结构化请求的 `answer` 必为空且重试时也为空。
- fixture 四例：①首次受理落库 canonical 后同键原请求重试命中原控制；②升级前纯文本请求升级后同键重试成功；③同键修改 answers → `IDEMPOTENCY_INPUT_MISMATCH`；④answers 缺省请求 digest 与升级前逐字节一致。
- 新客户端连旧服务端：经**能力发现**判定（§8），不做提交时探测。

### 4.4 传播链路（wait → snapshot → 各端）

| 环节 | 改动点 | 说明 |
|---|---|---|
| 工具 → wait | `runtime/controls.py` clarify/ask_user 分支 | `wait` 追加 `"form"`（引擎预期零改动，待验证） |
| wait_id 补齐 | `src/services/agent_runner/durable_control.py` | 复用 decorate_wait；`clarificationQuestions` 投影带表单元数据 |
| 快照白名单 | `public_view.py:48-57` | `form` 加白名单 |
| **入口契约** | `control_contracts.py` | 新增 `answers`/`skip` + §4.2 入口校验修订 |
| **控制受理** | `control_repository.py` | 归一化 + 表单业务校验（§5.4）；rejected 落库 + errors 明细 |
| **幂等** | `intent()/digest()` | §4.3 显式条件序列化 |
| **能力发现** | capabilities 端点 | 增加结构化支持字段（§8），前端缓存 |
| 恢复回注 | `recovery.py:_apply_reply` | canonical 作为 tool result 回注（复用目标，待验证） |
| 历史 | checkpoint `ask_history`（§7.1） | park 登记、reply/终态更新 |
| 渠道 | `src/channels/runner_agent.py:172-183` | `wait['form']` 文字渲染（无 form 退回 question） |
| 前端类型 | `runner.ts`、`useAgent.ts` | RunnerWait.form；RunnerControlIntent.answers/skip |

### 4.5 schema 登记与兼容

AskForm / AnswerItem / errors DTO schema + 正反 fixture 落 `contracts/runtime-host/v1/`，按契约第 8 节登记。旧快照（无 `form`）按纯文本渲染；带 `form` 快照对旧客户端以严格解析器验证"忽略未知字段不报错"。

## 5. 状态机与生命周期

### 5.1–5.3 wait 模型（不变项）

1. **一 ask 一 wait**：多题打包一个 AskForm；"1/1"分页是 Web 展示形态。
2. **复用 clarification 状态机**：`waiting --reply--> running`（复用目标，待验证）。
3. **多轮串行**：每轮新 tool_call_id → 新 wait_id。

### 5.4 提交判定表（整体跳过、required、空提交唯一判定）

wait 含 form 时，受理事务内按**且只按**下表判定（先归一化，顺序即优先级）：

| 提交形态 | 判定 | 结果 |
|---|---|---|
| `skip=true` | `form.allow_skip` 为 false | rejected `FORM_SKIP_NOT_ALLOWED`，wait 不消费 |
| `skip=true` | `allow_skip=true` | **服务端权威跳过**：生成忽略文案为 answer，wait 消费 |
| `answers` 提交（含 `[]`） | 归一化后仍有无法解释项（未知题/越界/矛盾/text 带选项/超上限） | rejected `FORM_ANSWER_INVALID`（逐题明细） |
| `answers` 提交 | **遍历原 form 全部题**（省略的题=未答），存在 `required=true` 题未满足 | rejected `FORM_ANSWER_INVALID`（指明缺失题） |
| `answers` 提交 | 全部题未答（含 `[]`）且无 required 违反 | **合法普通提交**：canonical 全部 `（未回答）`，wait 消费 |
| `answers` 提交 | 部分未答 | 合法：未答题输出 `（未回答）` |
| 纯文本（`answers=None, skip=false`） | 兼容路径 | answer 直纳，不做表单校验 |

- **跳过唯一入口 = 显式 `skip`**：Web 忽略按钮、渠道精确"跳过"文本都提交 `skip=true`。
- **诚实边界**：`allow_skip=false` 在结构化路径强制；自由文本路径不可强制（用户写"我不想选了"作为文本透传，LLM 按 usage guide 处理）——产品取舍。
- LLM 视角：跳过=明确表态不回答；全空提交=未表态。两者都促使 LLM 按已知信息继续，语义可区分。

### 5.5 等待与资源边界

- **默认无限期等待**（产品决策）：waiting 即 park，释放 worker 与执行租约；checkpoint 持久化，重启可恢复。
- 资源边界如实声明：释放 worker、执行租约、LLM 推理消耗；**保留逻辑会话位置（session claim）**——新消息走现有会话队列（合并/排队/取消）；开始新任务按现有方式取消旧任务。等待任务随 `agent_runners` 行持久化，不新增配额；会话删除/租户注销时 waiting runner 按现有生命周期取消（Phase 1 验证项）。
- 恢复前检查**复用既有执行边界**（status/积分/授权，受理与认领处现有检查）；不另造审批流程；**恢复前对设备、插件、授权事实的重验覆盖面在 Phase 1 核对**，缺口另补。ask_user 不持有桌面锁/外部事务。
- `expires_at` 预留（一期恒 null）；超时语义属 Phase 3。

### 5.6 作答入口与身份边界（一期收缩承诺）

- **一期 = 同源作答**：渠道任务由该渠道会话作答（现有 `ChannelRunnerAgent` reply 链路）；Web 任务由同一认证会话作答（浏览器与桌面客户端共用登录态）。
- **跨入口作答一期不做**；后置前置条件见 §7.3。已绑定/未绑定/解绑/跨租户一律按现有归属校验拒绝。
- §1 的"多端"= 同一 Web 登录态下的浏览器/桌面多端。

### 5.7 卡片状态机与错误契约

**错误三分契约**：

| 类别 | 发生点 | 返回行为 | 落库 |
|---|---|---|---|
| 请求格式错误 | 入口模型解析 | 同步 422 + 机器可判定错误码（`CONTROL_REPLY_REQUIRED` / `CONTROL_ANSWER_CONFLICT` / `CONTROL_SKIP_CONFLICT` 等），无 control_id | 否 |
| 表单业务错误 | 受理事务内 | 同步 422 + `errors` 明细 DTO；创建 rejected control 落库并返回 control_id；wait 不消费 | 是 |
| 受理后执行拒绝 | recovery/执行阶段（罕见） | 异步，经快照/control 状态呈现 | 是 |

**errors DTO**：`{errors: [{question_id, code, message}]}`（多错误）；公开投影扩展该字段，前端逐题高亮。

**重试与换键规则**：

- 网络不确定 / 拒绝响应丢失：**复用原 `client_request_id` 重试**——rejected control 已落库时同键重放**返回原拒绝结果**（幂等，不覆盖原意图）；网络结果不明**不降级为文本路径**，只按原意图原键重试；
- 用户**修改答案后重新提交：必须换新 `client_request_id`**。

**卡片状态机**（"已应用"权威判定 = control_id `consumed` 且快照中该 wait 已消失；单一条件不足为凭）：

| 状态 | 判定依据 | 展示 |
|---|---|---|
| 待作答 | 快照 `waiting.form` 存在且本端无在途提交 | 可编辑 |
| 提交中 | controls 请求已发出未收到响应 | 按钮禁用 |
| 已受理 | 收到响应 + control_id（accepted） | "已提交，等待应用" |
| 已应用 | control `consumed` 且快照无该 wait | 只读回显（§7.1） |
| 被拒绝 | 422 errors（逐题高亮）/ `RUNNER_RESUME_PENDING` / `CONTROL_WAIT_STALE`（刷新重读）/ `CONTROL_EXECUTION_CLOSED` | 按错误码区分，不统一显示"已答复" |
| 任务关闭 | runner 终态 | 只读 + 终态标注 |

- 双端同时提交（同源多端）：先到者受理，后到者按错误码处理并刷新；**本端无提交上下文时，卡片状态一律以快照 + controls 查询重建**，不依赖本地内存。
- 连续两轮 ask：第一轮"已应用"后，新 wait 的 form 作为新卡片出现。

## 6. Agent 侧改造

1. **新控制工具 `ask_user`**（`catalog=False`，`control_set.py` 装配）：InputModel 只收内容字段，`execute` 服务端生成 `form_id`/ids，执行 §4.1 校验（仅总载荷 8KB 一条硬门 + 题型/选项匹配）。
2. **root 执行也产生等待**：回归验证 root 等待与 cancel/pause/final、防滥用拦截点交互（Phase 1 首个验证项）。
3. **usage guide**：何时用 ask_user；开放式问题用普通文本；非阻塞建议用 present_options；单轮 ≤1 次提问；题型显式声明；选项题默认保留自定义输入；每选项带一句 description；选项文本尽量精炼（软建议——渠道适配由渠道层处理，不做生成端硬门）。
4. **存量工具**：`clarify` 保留兼容；`present_options` 保留非阻塞定位。
5. **实施约束（三轮评审实施原则）**：复用既有受理事务、快照与授权机制，不另造审批/校验流程；前端本地预检只做即时反馈，服务端权威校验一次完成，**不为同一规则增加多次网络往返**；渠道投递与归一化的全部新增状态以"防止的具体错误"为准入理由（投递状态防重复发送与盲发；归一化防无效纠错轮次），无其他新增检查。

## 7. 各端渲染

### 7.1 Web（含未来自由桌面客户端，一期）

- 新组件 `QuestionCard.vue`：题型渲染（single radio / multi checkbox）、`allow_custom_text` 自由输入行、text 独立输入框、required 前端预检（即时反馈）、忽略/提交（`allow_skip=false` 无忽略）、多题分页。
- **提交一律结构化**（`answers` 或 `skip=true`），canonical 由服务端生成；服务端不支持时经能力发现降级（§8）。
- 状态按 §5.7 状态机渲染；重试复用原键，修改后换新键；本端上下文丢失以快照 + controls 查询重建。
- **历史持久化**：
  - 位置：**runner `checkpoint` JSONB 内新增 `ask_history[]`**（非独立数据库表）。park 时即登记（form 快照、`status=待作答`）；reply 受理更新为 `submitted(control_id, accepted_at)`，恢复消费更新为 `applied`；rejected/cancelled/终态各自更新。`public_snapshot` 投影只读摘要（白名单扩展）。
  - 历史渲染关联 **control 状态与 applied 事实**：`已提交 ≠ 已应用`；"已提交（待应用）/已应用/被拒/未答已取消"分别呈现。
  - 未答即取消的任务保留问题历史。
  - **草稿不持久化**：快照恢复的是已发布表单与已提交答案，不含未提交勾选/输入；刷新丢草稿属预期。
- legacy SSE form 透传为 Phase 3。

### 7.2 第三方渠道：统一文字问答（一期全渠道）

**渲染与分段（P2-4：渠道预算只在投递时生效）**：

- `ask_render.py` 按渠道 adapter 配置渲染（§2.3）；紧凑模式（省 description、压缩提示行）与按题分段**按需启用**；由 §4.1 算术保证单题紧凑渲染必进最严格渠道单条，分段维度 = 多题 × 渠道单条预算；**题干与选项 label 永不跨段截断**。

**投递前核算（不盲发）**：

1. 计算段数 N；核算当前可用预算 A（wecom_kf：本周期 5 次扣除 verbose 占用；**提问轮次不再为 final 预留——本周期响应即提问文本**，实施时与 `WeComKfReplyBudget`/`reserve_for_final` 现状核对，如必须预留则在 A 中扣除）；
2. `N ≤ A` → 投递；`N > A` → 紧凑模式重渲染再核算；仍不足 → **不投递**，登记 `delivery=pending`。

**投递状态（按 wait_id 登记，存渠道会话元数据，随 wait 消费清理）**：

| 状态 | 含义 |
|---|---|
| `pending` | 核算未通过，未发送 |
| `partial` | 部分段已确认发送（登记已确认段号与失败原因） |
| `delivered` | 全部段确认 |
| `blocked` | 极简重投后仍无法完整投递（同一 form 连续 2 次补发不完整），**终止自动补发** |

**补发与入站消息的关系（P1-2 裁决：自然语言答案永不吞）**：

特殊分支**只适用于 pending / partial / blocked 三态**；**delivered 无任何拦截**，一切消息走正常答案路径。每个入站消息按以下优先级处理：

1. 隐藏命令（"清空会话"/"新会话"等）→ 既有命令拦截，取消任务，ask_history 记"已取消"；
2. 精确"跳过" → `skip=true` 结构化提交；
3. Q 编号格式 → 结构化答案受理（协议错误 → 纠正提示，见三分法）；
4. 精确"重发"（pending/partial/blocked）或"简化"（blocked）→ 触发补发/极简重投，该条不作为答案；
5. **其余自然语言**：
   - **`pending`**（问题从未送达，可确定该输入不是对问题的回答）→ 补发问题 + 提示"您上一条消息未作为回答；请直接回答、回复'跳过'或'取消'"，该条不作为答案；
   - **`partial` / `blocked`**（用户可能已见到部分题目，自然语言可能是有效回答）→ **按现有 reply 语义直接作为自由文本答案提交**，不吞、不强迫编号；partial 时剩余未送达段随该轮响应附发，提示可继续作答或跳过；未送达题目可能未获回答，由 LLM 按已知信息处理（符合自由文本兼容承诺）。

**blocked 的出口全部在原渠道（P1-1 裁决：不做跨入口引导）**：blocked 时发送事实说明 + 可用出口——直接用文字回答（自由文本路径）/ 回复"简化"触发极简重投一次 / 回复"取消"结束任务。**删除 v3 的"打开网页版查看并回答"引导**（跨入口一期无链路）；Web 表单可用性仅对 Web 同源任务成立。补发失败时提示事实与出口，**不要求用户反复输入来触发恢复**。

**Q 编号回复协议（三分法 + 归一化优先）**：

| 输入特征 | 分类 | 处理 |
|---|---|---|
| 无编号协议特征的自然语言 | 自由文本 | 兼容路径透传（不强制表单约束） |
| 明确使用协议（出现题号）但含无法解释项：未知题号、越界选项号、text 题带选择、同题矛盾答案 | 协议错误 | 拒绝提交 + 纠正提示（指明非法项；一次，不计数）；无害差异（大小写/空白/分隔符/重复选项）**归一化不纠正** |
| 混合（部分题可解析、部分无法解释） | 协议错误 | 整单拒绝 + 纠正提示（列出明细），不静默丢掉可解析部分 |

- 严格解析规则：按行解析——以题号开头的行开启新题答案，非题号开头的行视为上一题 text 续行；`required` 校验遍历整个原 form（§5.4）。
- 解析成功 → 结构化提交；协议错误 → 纠正；自然语言 → 兼容路径。**行为只由输入性质决定，无累计状态**。
- 渠道发送形态：飞书 post / 企微 markdown / 钉钉 sampleMarkdown / wecom_kf text / RPA text，模板带显式题号 Q1/Q2，选项按题从 1 编号。

### 7.3 未来机会（不在一期范围，实施前单独立任务过契约检查点）

渠道原生卡片（飞书 interactive + `card.action.trigger` 优先）；跨入口作答（前置：可信账号绑定、任务发现入口、跨入口读取/控制授权）；提问通知跨任务频控；`expires_at` 超时语义（Phase 3 评估）。

## 8. 安全、限制与发布兼容

- **授权澄清**：ask_user 只收集业务选择，不改变工具权限/workspace grant/任务许可；"同意"不扩权；`allow_skip=false` 是结构化路径的业务门；跳过不构成批准；授权缺失的步骤恢复后仍被原有权限检查阻断（复用既有执行边界，Phase 1 核对覆盖面）。
- 普通澄清 reply 不转换为许可/授权（契约既有约束沿用）。
- 载荷上限（§4.1 总量 8KB）+ `answer` 沿用 1MB；入口与受理双层校验，非法输入不消费 wait。
- 身份、租户与任务归属校验、wait 对应关系、重复消费防护与幂等**全部沿用现有机制**，不新增权限面。
- **发布兼容（P1-3 裁决：能力发现，不依赖旧服务端新错误码）**：
  - **判定机制**：既有 `GET /api/chat/runners/capabilities` 增加可选能力字段（如 `ask_structured_answers: true`）；**前端会话启动时读取一次并缓存**，不在每次提交时探测。字段缺失（旧服务端）→ 前端保持**客户端 canonical 文本路径**（标注：该窗口服务端权威校验不生效，为灰度/回滚窗口专用兼容）。
  - **发布顺序约束**：先发布后端（Runner API 与 Web 网关同部署单元）并确认健康，再放开前端结构化提交（feature flag）；滚动期间以"未确认支持 = 文本路径"为判定基准（请求可能命中任一实例）。
  - **网络结果不明（超时/5xx）：不降级**，按原意图原键重试（幂等安全）；文本降级仅用于"已确定服务端不支持"（capabilities 判定）。
  - **回滚**：回滚目标需保留 ask 受理能力以处理在途 form wait，或回滚前取消在途 ask 任务——写入部署注意事项。
  - 旧服务端对未知字段的统一 `INVALID_REQUEST` 不作为判定依据；`UNSUPPORTED_FIELD` 仅保留为新服务端诊断码。

## 9. 分阶段实施

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道 | schema/fixture（幂等四例）；`ask_user`（服务端 id + root 等待 + 总载荷校验）；入口模型修订（answers/skip 放行与互斥、旧 clarify 不变）；归一化 + 受理判定表（§5.4）+ errors DTO + rejected 落库与原键重放；canonical 服务端生成；wait/snapshot 白名单；checkpoint `ask_history` + 历史只读投影（含未答取消）；Web QuestionCard（状态机/换键/上下文重建）；capabilities 能力字段 + 前端缓存降级；渠道渲染与投递核算（N×A、紧凑模式、四态状态、按状态收窄的补发分流、blocked 原渠道出口）；Q 编号三分法（归一化优先） | 入口四例（answers-only 成功 / `[]` 进判定 / 旧 clarify 空回复仍拒 / 非 reply 带 answers 拒）；幂等四例；判定表全组合（required×allow_skip、部分空、全空、skip）；归一化用例（重复选项合并、大小写、空白、分隔符）与不可解释输入拒绝；多错误明细与逐题高亮；拒绝响应丢失原键重放；网络不明不降级只重试；能力发现降级与缓存；pending/partial/delivered/blocked 各态入站分流（自然语言不吞、delivered 无拦截）；blocked 原渠道三出口与"简化"重投；未答取消历史；双端提交与刷新重建；root 等待回归；旧快照严格解析器兼容 |
| Phase 2：同源待答可达性 | `runner_waiting_input` 通知生产者（挂 park 事务，复用统一通知基础设施）；站内信 + Web Push；会话列表「待回答」标识（Web 同源任务） | 通知到达与回跳；多次提问频控；渠道不受影响 |
| Phase 3：增强项（按需） | `expires_at` 语义冻结；capabilities 正式协商扩展；legacy SSE form 透传；埋点（提问率/跳过率/响应时长） | 过期不产生孤儿计费；严格解析器兼容门 |

未来机会（单独立任务）：渠道原生卡片、跨入口作答（§7.3）。

## 10. 评审意见落实对照（三轮）

| 三轮意见 | 落实位置 |
|---|---|
| 1. blocked 引导 Web 作答与一期范围冲突 | §7.2（出口全部收回原渠道：文字回答/简化/取消；删除 Web 引导与"始终可用"承诺） |
| 2. 补发分支吞答案 | §7.2（分支只适用 pending/partial/blocked；delivered 无拦截；自然语言默认按答案处理，仅 pending 可判定"非答案"；显式跳过/重发/简化指令优先；补发失败提示事实不反复触发） |
| 3. 旧服务端不返回新错误标志 | §8（capabilities 能力发现 + 前端缓存；发布顺序约束；网络不明不降级只重试；回滚保留受理能力） |
| 4. 最严格渠道限制 Web 表单 | §4.1（删除单题字节准入门，仅保留总载荷 8KB；算术说明单题必进单段）、§7.2（渠道预算只在投递渲染时生效） |
| 5. 无害输入应归一化 | §4.2（重复 key 去重、大小写/空白归一；仅不可解释输入拒绝）、§7.2（删除累计错误次数状态，行为由输入性质决定） |
| 实施与验证边界提醒 | §3.8、§6.5（只保留必要限制、复用既有机制、单次往返）、§9（验证聚焦实际改动，不机械重复） |

## 11. 开放问题

1. root 执行 ask_user 等待与 final_answer 流、`task_record` 语义交互（Phase 1 首个验证项）；
2. wecom_kf 补发分流与 `ChannelRunnerAgent` 现有 waiting 分支的实现对齐（含"提问轮次 final 预留"核对）；
3. 恢复链路对撤权/设备/插件事实的重验覆盖面核对（复用既有执行边界）；
4. 渠道用户长期只回自由文本时 `required` 无法强制的体验补偿（Phase 2 通知/badge 是否缓解）；
5. "简化"/"重发"/"取消"渠道指令与现有 hidden_commands 机制的注册关系（是否统一收口）。
