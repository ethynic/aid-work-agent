# 结构化提问交互协议设计（ask_user / AskForm）

> 状态：v5 架构评审通过（2026-10-14），v6 为实施前口径修正稿
> 日期：2026-10-14（v1：2026-10-13 commit c93a00b9；v2～v6：2026-10-14）
> 关联：[Runner/Desktop/Runtime 共同契约](runner-desktop-runtime-integration-contract.md)、[AgentRunner 架构](agent-application-architecture-design.md)、[Web 任务推送通知](web-task-push-notification-design.md)
> 范围声明：本文只做协议与整体规划，未开发；文中"零改动 / 零新增 API"均为**待验证的复用目标**；应用层校验、历史投影、幂等兼容与投递保障均已列入实施范围（§9）。

## 修订记录

| 日期 | 版本 | 内容 |
|---|---|---|
| 2026-10-13 | v1 | 初稿：复用 clarify 等待管道；Web 富卡片 + 渠道统一文字；忽略/无限期等待/待答通知 |
| 2026-10-14 | v2 | 一轮评审：结构化 answers 提前一期 + 服务端权威校验与 canonical 生成；Q 编号协议；同源作答；幂等兼容；卡片状态机；渠道投递与预算生命周期；历史回显；等待资源边界；授权澄清 |
| 2026-10-14 | v3 | 二轮评审：入口模型放行 answers；摘要冻结为客户端原始意图；显式 skip 判定表；投递核算；错误契约；解析三分法；发布兼容 |
| 2026-10-14 | v4 | 三轮评审：blocked 出口收回原渠道；补发分流收窄；capabilities 能力发现；删除单题字节准入门；无害输入归一化 |
| 2026-10-14 | v5 | 四轮评审：①入站处理统一为单管线，**不按投递状态分流**——自然语言一律沿现有 reply 路径提交，补发仅在用户明确要求时发生；②wait 消费后停止发送旧表单剩余内容，不为旧表单增加续答状态；③**校验失败不创建 control**（同步 422 + errors、不消费 wait、不落库），拒绝历史与原拒绝回放收缩出范围；④删除"字符上限保证单题必进单段"的错误前提（UTF-8 有 4 字节字符），渠道按实际渲染字节分段；⑤文案长度上限区分为生成/展示建议与服务边界，删除用户自由文本独立字数拒绝门；⑥投递记录降为**发送事实**，不承担答案准入；历史展示复用既有问题、答案与控制事实（受理 control payload 附 form 快照），**删除 checkpoint ask_history 平行状态** |
| 2026-10-14 | v6 | 五轮评审（架构通过，实施前口径修正）：①"reply 受理即 wait 消费"修正为对齐异步控制机制——受理只登记 control，**应用成功才清除 wait**；受理后暂停旧表单补发，应用成功清理剩余发送记录，被拒且 wait 仍有效可修正/重发；②单逻辑行自身超限时在**合法字符边界**继续拆分跨消息发送（"不截断"指不丢内容，非禁止一行跨消息）；③载荷口径修正——`answer` 为 `max_length=1_000_000` **字符**、完整控制请求另有 UTF-8 **字节**闸门，两者不可统称 1MB；自由文本与结构化 answers 整体复用既有控制请求总量限制，不按题放大、不新增独立字数限制；④明确禁止扩张清单固化；校验失败重提不做专门前端键管理 |

## 1. 背景与目标

参考交互（ZCode 提问卡片）：智能体在需要用户澄清时渲染结构化提问卡片——分类标签 + 题干、单选/复选选项（label + description）、选项下方自定义输入行、"忽略 / 提交"按钮、多题分页（1/1）。

1. **Web 端**（含未来自由桌面客户端，共用 Runner 快照 wire）渲染富交互提问卡片；
2. **第三方渠道**（企业微信应用 / 飞书 / 钉钉 / 微信客服 / 企微个人号 RPA）**统一采用规范格式的文字问答**（范围决策，渠道不做富卡片；飞书等原生卡片为未来机会 §7.3）；
3. **数据格式协议先行**：渠道中立核心 schema 一次定稿；**严格表单校验覆盖结构化路径**；自由文本路径为兼容路径，不强制表单约束——明确的产品取舍；
4. **等待与找回**：提问在用户响应前无限期保持（不占 worker/租约，资源边界见 §5.5）；用户可跳过（loop 按已知信息继续）；同源入口提供待答标识与通知。

## 2. 现状盘点与相关事实（2026-10-13 核对）

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
- **旧服务端模型错误统一返回 `INVALID_REQUEST`**：兼容判定不能建立在旧服务端的新错误标志上（§8 走能力发现）。
- **授权按入口区分**：web/channel 会话分别校验 source、actor、session 归属；同一平台 user_id 不足以跨入口控制任务（§5.6）。
- **wecom_kf 预算**：`WeComKfReplyBudget(total=5)` 是**每条入站消息**（owner 级）对象；长正文会转图片（§7.2 投递保障以此为前提）。
- **supplemental_inputs 不是独立数据库表**：在 runner `checkpoint` JSONB 内维护列表，`public_snapshot` 维护投影——历史展示复用该既有载体（§7.1）。
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
4. **渠道统一文字模式**；按实际渲染字节分段、逻辑边界拆分、保留题号选项号；**内容完整是必要目标，单题单条不是必要限制**；投递记录只存发送事实。
5. **同源作答**：一期任务只能由产生它的同源入口作答（§5.6）。
6. **业务确认 ≠ 执行授权**（§8）。
7. **校验承诺如实声明**：结构化路径严格校验；自由文本兼容路径不强制表单约束（产品取舍）。
8. **只保留必要限制**：新增检查须说明其防止的具体错误及现有机制为何不足；无害格式差异归一化而非拒绝；普通文本作答不受投递状态限制，重发是可选操作不是前置步骤；不为普通校验失败维护新持久流程；复用既有受理事务、快照、授权与发送重试机制，不为同一规则增加多次网络往返。
9. **契约合规**：公开 controls 仍只有 pause/resume/reply；wire 可选字段按契约 §7.2 严格解析器验证，幂等兼容以 fixture 证明。

## 4. 核心数据协议

### 4.1 AskForm（下游：问题载荷）

```jsonc
// contracts/runtime-host/v1/schemas/ask-form.schema.json（实施时落盘）
{
  "form_id": "frm_<uuid>",              // 服务端（工具执行层）生成
  "wait_id": "wait_...",                // 关联的 runner wait（durable_control.py decorate_wait 补齐）
  "title": "归档范围",                   // 生成/展示建议 ≤20 字（非拒绝门，见下）
  "intro": "除成本治理外，以下开发已完成…", // 生成/展示建议 ≤200 字
  "questions": [                         // 1..5 题，打包进同一个 wait
    {
      "id": "q1",                        // 服务端按题序生成（^q[1-9]$），form 内唯一
      "tag": "归档范围",                  // 生成/展示建议 ≤12 字
      "prompt": "以下哪些任务移入 ideas_finished.md？",
      "type": "single | multi | text",   // 单选(radio，最多 1 项) / 多选(checkbox) / 纯自由文本
      "required": false,
      "options": [                       // single/multi 必填 2..8 项；text 禁止携带
        {"key": "q1o1", "label": "queue_order 序列自愈…", "description": "代码+测试+CR 完成…"}
        // key 服务端按序生成（^q[1-9]o[1-9]$），题内唯一；label/description 长度为生成/展示建议
      ],
      "allow_custom_text": true,         // 选项下方自由输入行；single/multi 默认 true，text 恒有输入框
      "custom_placeholder": "输入自定义内容…", // 生成/展示建议
      "placeholder": "输入你的回答…",      // 生成/展示建议
      "max_select": 5                    // multi 可选上限，2..选项数，缺省=选项数；single 恒 1
    }
  ],
  "allow_skip": true,                    // 是否允许整体跳过（仅约束显式 skip，见 §5.4）
  "expires_at": null                     // 预留字段，一期恒为 null（§5.5）
}
```

**服务边界与建议的区分（P2-5）**：

- **硬校验（工具层与受理层执行，违反即错误）**：题型与选项匹配（single/multi 缺 options、text 带 options）、`required`/`max_select` 业务规则、选项 key/question_id 结构完整性（服务端生成保证）、**总载荷 ≤8KB（UTF-8 字节）**。无其他长度拒绝门。
- **生成/展示建议（usage guide + schema 描述约束 LLM，不做拒绝门）**：title/tag/prompt/label/description/placeholder 的字符上限。超长文案由渠道渲染层按实际字节分段消化（§7.2），**不因此拒绝用户答案、不截断用户答案、不强制 LLM 重新生成**。
- 用户自由文本与结构化 `answers` 整体**复用既有控制请求总量限制**（v6 口径修正：`answer` 字段为 `max_length=1_000_000` **字符**，完整控制请求另有按 UTF-8 字节计的请求闸门，两者口径不同、各自沿用，不可统称"1MB"）；结构化答案纳入现有请求字节计算，不按题分别放大额度、不新增独立字数限制。
- 题型语义：`single` 最多选一项；`multi` 受 `max_select` 约束；`text` 无选项且答案不得携带选项。
- 选项与自定义输入并存：可只选、只输入、或两者同时；UI 不做互斥清空。
- **等待期间表单不可变**：AskForm 随 wait 写入 checkpoint，校验以 checkpoint 中该份为准；reply 不携带、不接受修改表单。

### 4.2 AskAnswer：入口模型与归一化

**传输模型**（`control_contracts.py`，`extra='forbid'`）：

```python
class AnswerItem(BaseModel):
    question_id: str            # ^q[1-9]$（大小写不敏感，受理时归一为小写）；须存在于当前 form
    selected_keys: List[str]    # ⊆ 该题 options.key；single ≤1；multi ≤ max_select；重复 key 服务端去重归一
    text: Optional[str]         # allow_custom_text=false 须为空；text 题不得携带 selected_keys；长度复用请求总量上限

class RunnerControl(...):       # 现有模型追加
    answers: Optional[List[AnswerItem]] = None   # 未提供=None；显式 [] 合法（=全部未答的普通提交）
    skip: bool = False                            # 显式整体跳过，仅 reply 可用
```

**入口校验修订**（`RunnerControl` 模型级，Web 网关与 Runner API 同一模型）：

| 规则 | 行为 |
|---|---|
| reply 合法条件 | `answer.strip() or attachments or answers is not None or skip` —— 满其一即过入口 |
| 互斥：`answers` 与非空 `answer` 同现 | 请求格式错误（422），不落库 |
| 互斥：`skip=true` 与 `answers` 同现 | 请求格式错误（422），不落库 |
| 非 reply（pause/resume/内部 browser_complete）携带 answers 或 skip=true | 请求格式错误（422），非 reply 行为完全不变 |
| 旧 clarify：空 answer、无附件、无 answers、skip=false | 仍报 `CONTROL_REPLY_REQUIRED`，**行为不变** |

**归一化优先**：answers 提交先归一化再校验——`question_id` 大小写归一、前后空白剥离、`selected_keys` 重复 key 去重（按一次计）。仅对无法确定解释的输入拒绝：未知 `question_id`、越界 key、同题重复且答案矛盾、text 题带选项、`allow_custom_text=false` 带 text、超 `max_select`。无累计错误次数状态。

**受理事务内业务校验（P2-3：失败不落库）**：

1. wait 无 form（旧 clarify）→ 原路径（answer 文本直纳），完全兼容；
2. wait 含 form → 按 §5.4 判定表处理（先归一化）；
3. 校验失败 → **同步 422 + `errors` 逐题明细，wait 不消费、不创建 control、不落库**；客户端修正后**可用原 `client_request_id` 重提**（无 control 落库即无意图冲突）；一旦受理成功（control 创建），后续变更按 §5.7 换键规则。

**canonical 文本**（服务端生成，写入执行载荷与落库）：

```
Q1【归档范围】queue_order 序列自愈、wecom_kf 限频修复；自定义：file_assets 建议观察一周再归档
Q2【后续独立事项】飞书钉钉接入 AgentRunner
Q3【补充说明】（未回答）
```

按 form 题序输出**全部题**（未答输出 `（未回答）`）；选项写可读 label，多选「、」；自定义以「；自定义：」前缀。**跳过文案**（`skip=true` 受理合法时）由服务端生成：`（用户跳过了本次提问，请按已知信息继续）`。

**自由文本兼容政策（产品取舍）**：`answers=None` 且 `skip=false` 的纯文本 reply 兼容放行，不经过表单校验，`required`/`max_select`/`allow_skip=false` 在该路径**不强制**，由 LLM 语义理解（clarify 现状）。严格校验仅承诺结构化路径。

### 4.3 幂等摘要：客户端原始意图冻结

- **digest 永远基于客户端原始请求**；canonical 是服务端生成的执行载荷，写入 control payload 与落库记录（存储结构与请求对象分离），不参与 digest。
- **序列化规则（显式条件，不用全局 exclude）**：`intent()` 构造 dict 时仅对新增字段做"未提供即不含该键"；其余字段序列化与现版逐字节一致。
- `answers` 与 `answer` 互斥使摘要歧义消失：结构化请求的 `answer` 必为空且重试时也为空。
- fixture 四例：①首次受理落库 canonical 后同键原请求重试命中原控制；②升级前纯文本请求升级后同键重试成功；③**已受理后**同键修改 answers → `IDEMPOTENCY_INPUT_MISMATCH`；④answers 缺省请求 digest 与升级前逐字节一致。
- 新客户端连旧服务端：经**能力发现**判定（§8），不做提交时探测。

### 4.4 传播链路（wait → snapshot → 各端）

| 环节 | 改动点 | 说明 |
|---|---|---|
| 工具 → wait | `runtime/controls.py` clarify/ask_user 分支 | `wait` 追加 `"form"`（引擎预期零改动，待验证） |
| wait_id 补齐 | `src/services/agent_runner/durable_control.py` | 复用 decorate_wait；`clarificationQuestions` 投影带表单元数据 |
| 快照白名单 | `public_view.py:48-57` | `form` 加白名单 |
| **入口契约** | `control_contracts.py` | 新增 `answers`/`skip` + §4.2 入口校验修订 |
| **控制受理** | `control_repository.py` | 归一化 + 表单业务校验（失败 422 不落库）；受理成功时 payload 附 canonical 与 form 快照（§7.1 历史复用） |
| **幂等** | `intent()/digest()` | §4.3 显式条件序列化 |
| **能力发现** | capabilities 端点 | 增加结构化支持字段（§8），前端缓存 |
| 恢复回注 | `recovery.py:_apply_reply` | canonical 作为 tool result 回注（复用目标，待验证） |
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

wait 含 form 时，受理事务内按**且只按**下表判定（先归一化，顺序即优先级）；**所有 rejected 结果均为同步 422 + errors，不消费 wait、不创建 control**：

| 提交形态 | 判定 | 结果 |
|---|---|---|
| `skip=true` | `form.allow_skip` 为 false | 拒绝 `FORM_SKIP_NOT_ALLOWED` |
| `skip=true` | `allow_skip=true` | **服务端权威跳过**：生成忽略文案为 answer，受理，wait 消费 |
| `answers` 提交（含 `[]`） | 归一化后仍有无法解释项 | 拒绝 `FORM_ANSWER_INVALID`（逐题明细） |
| `answers` 提交 | 遍历原 form 全部题（省略的题=未答），存在 `required=true` 题未满足 | 拒绝 `FORM_ANSWER_INVALID`（指明缺失题） |
| `answers` 提交 | 全部题未答（含 `[]`）且无 required 违反 | **合法普通提交**：canonical 全部 `（未回答）`，受理 |
| `answers` 提交 | 部分未答 | 合法：未答题输出 `（未回答）` |
| 纯文本（`answers=None, skip=false`） | 兼容路径 | answer 直纳，不做表单校验 |

- **跳过唯一入口 = 显式 `skip`**：Web 忽略按钮、渠道精确"跳过"文本都提交 `skip=true`。
- **诚实边界**：`allow_skip=false` 在结构化路径强制；自由文本路径不可强制——产品取舍。
- LLM 视角：跳过=明确表态不回答；全空提交=未表态。两者都促使 LLM 按已知信息继续。

### 5.5 等待与资源边界

- **默认无限期等待**（产品决策）：waiting 即 park，释放 worker 与执行租约；checkpoint 持久化，重启可恢复。
- 资源边界如实声明：释放 worker、执行租约、LLM 推理消耗；**保留逻辑会话位置（session claim）**——新消息走现有会话队列（合并/排队/取消）；开始新任务按现有方式取消旧任务。等待任务随 `agent_runners` 行持久化，不新增配额；会话删除/租户注销时 waiting runner 按现有生命周期取消（Phase 1 验证项）。
- 恢复前检查**复用既有执行边界**（status/积分/授权，受理与认领处现有检查）；不另造审批流程；恢复前对设备、插件、授权事实的重验覆盖面在 Phase 1 核对，缺口另补。ask_user 不持有桌面锁/外部事务。
- `expires_at` 预留（一期恒 null）；超时语义属 Phase 3。

### 5.6 作答入口与身份边界（一期收缩承诺）

- **一期 = 同源作答**：渠道任务由该渠道会话作答（现有 `ChannelRunnerAgent` reply 链路）；Web 任务由同一认证会话作答（浏览器与桌面客户端共用登录态）。
- **跨入口作答一期不做**；后置前置条件见 §7.3。已绑定/未绑定/解绑/跨租户一律按现有归属校验拒绝。
- §1 的"多端"= 同一 Web 登录态下的浏览器/桌面多端。

### 5.7 卡片状态机与错误契约

**错误两类契约（P2-3 收缩：校验失败不落库）**：

| 类别 | 发生点 | 返回行为 | 落库 |
|---|---|---|---|
| 请求/表单校验失败 | 入口模型或受理事务内（受理前） | 同步 422 + 机器可判定错误码；表单错误带 `errors` 逐题明细 DTO（`{errors:[{question_id,code,message}]}`，公开投影扩展）；**不创建 control、不消费 wait** | 否 |
| 受理后失败 | recovery/执行阶段 | 异步，经既有 control 状态（含 rejected）与快照呈现 | 是（既有机制） |

**重试与换键规则**：

- 校验失败：未落库即无意图冲突，前端修正后**直接重提即可**（复用或新键服务端都能正确处理），**不为该场景增加专门的前端键管理逻辑**；校验是确定性纯函数，重试得到相同结果。
- 网络结果不明（超时/5xx）：**不降级为文本路径**，沿用原意图、原键重试（幂等安全）。
- 用户修改答案后重新提交：生成新 `client_request_id` 即可；**已受理后**同键改答案 → `IDEMPOTENCY_INPUT_MISMATCH`（既有机制）。

**卡片状态机**（"已应用"权威判定 = control_id `consumed` 且快照中该 wait 已消失；单一条件不足为凭）：

| 状态 | 判定依据 | 展示 |
|---|---|---|
| 待作答 | 快照 `waiting.form` 存在且本端无在途提交 | 可编辑 |
| 提交中 | controls 请求已发出未收到响应 | 按钮禁用 |
| 已受理 | 收到受理响应 + control_id（accepted） | "已提交，等待应用" |
| 已应用 | control `consumed` 且快照无该 wait | 只读回显（§7.1） |
| 被拒绝 | 同步 422 + errors（逐题高亮）/ `RUNNER_RESUME_PENDING`（已有提交待应用）/ `CONTROL_WAIT_STALE`（刷新重读）/ `CONTROL_EXECUTION_CLOSED` | 按错误码区分，不统一显示"已答复" |
| 任务关闭 | runner 终态 | 只读 + 终态标注 |

- 双端同时提交（同源多端）：先到者受理，后到者按错误码处理并刷新；本端无提交上下文时，卡片状态以快照 + controls 查询重建，不依赖本地内存。
- 连续两轮 ask：第一轮"已应用"后，新 wait 的 form 作为新卡片出现。

## 6. Agent 侧改造

1. **新控制工具 `ask_user`**（`catalog=False`，`control_set.py` 装配）：InputModel 只收内容字段，`execute` 服务端生成 `form_id`/ids，执行 §4.1 硬校验（题型/选项匹配 + 总载荷 8KB + required/max_select）。
2. **root 执行也产生等待**：回归验证 root 等待与 cancel/pause/final、防滥用拦截点交互（Phase 1 首个验证项）。
3. **usage guide**：何时用 ask_user；开放式问题用普通文本；非阻塞建议用 present_options；单轮 ≤1 次提问；题型显式声明；选项题默认保留自定义输入；每选项带一句 description；**文案长度建议**（题干/选项/描述精炼，title ≤20 字、label ≤40 字等）——建议而非硬门，超长由渠道分段消化。
4. **存量工具**：`clarify` 保留兼容；`present_options` 保留非阻塞定位。
5. **实施约束**：复用既有受理事务、快照、授权与发送重试机制；前端本地预检只做即时反馈，服务端权威校验一次完成；不新增用户确认步骤、审批或每次提交的能力探测；投递记录只存发送事实；不为普通校验失败维护新持久流程。

## 7. 各端渲染

### 7.1 Web（含未来自由桌面客户端，一期）

- 新组件 `QuestionCard.vue`：题型渲染（single radio / multi checkbox）、`allow_custom_text` 自由输入行、text 独立输入框、required 前端预检（即时反馈）、忽略/提交（`allow_skip=false` 无忽略）、多题分页。
- **提交一律结构化**（`answers` 或 `skip=true`），canonical 由服务端生成；服务端不支持时经能力发现降级（§8）。
- 状态按 §5.7 状态机渲染；校验失败修正后原键重提；已受理后修改换新键；本端上下文丢失以快照 + controls 查询重建。
- **历史展示（P2-3：复用既有事实，无平行权威状态）**：
  - 问题：复用既有 `clarificationQuestions` 投影（问题文本以 assistant 消息进入历史）；
  - 答案与表单回显：**受理成功的 control payload 附服务端生成的执行记录（canonical + form 快照）**——control 是唯一权威事实源；`public_snapshot.supplementalInputs`（既有载体、既有写入点）扩展投影 `canonical/control_id/答案摘要/form 快照`，前端据此渲染只读表单卡片；
  - 状态：control 状态 + runner 终态（`已提交≠已应用`、被拒、未答已取消分别呈现）；
  - 未答即取消：问题文本经既有投影保留在历史（表单选项不回放，可接受）；
  - **草稿不持久化**：快照恢复的是已发布表单与已提交答案，不含未提交勾选/输入。
- legacy SSE form 透传为 Phase 3。

### 7.2 第三方渠道：统一文字问答（一期全渠道）

**渲染与分段（v6 修正：优先逻辑行，单行超限时按合法字符边界跨消息）**：

- `ask_render.py` 按渠道 adapter 配置渲染（§2.3），**按实际渲染字节计算**：优先在逻辑边界（intro 行、题头行、选项行）分段，续段保留题号与选项号（如 `Q1（续）`）；**单行自身仍超渠道单条上限时，在该行内部按合法字符边界继续拆分**（不破坏多字节序列），续段同样保留题号/选项号前缀。"不截断"指**不丢内容**，不是禁止一行跨消息。此为渲染分段兜底，不增加生成准入门、不要求 LLM 重写、不拒绝用户答案；优先省略 description 压缩篇幅。

**投递前核算（仅 wecom_kf 等有回复预算的渠道需要，不盲发）**：

- 计算段数 N；核算本周期可用预算 A（wecom_kf：5 次扣除 verbose 占用；**提问轮次不再为 final 预留**，实施时与 `WeComKfReplyBudget`/`reserve_for_final` 现状核对）；`N > A` → 省略 description 压缩重算；仍不足 → 发送能完整容纳的前段并如实告知剩余未发（事实提示，不承诺自动补发）。

**投递记录（P2 收敛：只存发送事实，不承担答案准入）**：

- 按 wait_id 记录：**已确认发送的段、最近失败原因、重投次数**——仅用于续传去重与提示事实；复用各渠道现有发送重试；**无状态机、无自动补发**。

**入站处理：单一管线（P1-1/P1-2 裁决：不分投递状态、自然语言永不吞）**：

所有入站消息走**同一条管线**，与投递状态无关：

1. 隐藏命令（"清空会话"/"新会话"等）→ 既有命令拦截，取消任务；
2. 精确"跳过" → `skip=true` 结构化提交；
3. Q 编号格式 → 结构化答案受理（协议错误 → 纠正提示，见三分法）；
4. 明确请求重发（精确"重发"/"简化"）→ 按投递记录**续传未发送段**（只发缺的，不重复已发），该条不作为答案；
5. **其余一切输入 → 沿现有 reply 路径提交**（自由文本兼容路径），**任何投递状态下都不拦截**——"按默认处理""不要继续"这类输入交给 LLM 理解（题目是 LLM 自己出的，语义可判）。

**受理与应用的边界（v6 修正：对齐异步控制机制，受理 ≠ 应用）**：受理（accepted）只表示 control 已登记 `resume_control_id`，**原 wait 在答案经恢复应用成功时才清除**，受理后仍可能失败。口径：

- 受理后即**暂停**该表单剩余内容的补发（保留发送记录，不删除）；受理未应用期间的"重发"请求不执行（此时答案可能即将应用）；
- 答案**应用成功**（control `consumed` 且快照无该 wait）→ 清理该表单剩余发送记录，此后重发不再针对旧表单；
- 控制被拒或应用失败且快照仍显示该 wait → wait 仍有效，用户可修正作答或明确要求重发。

全程直接复用既有 control 状态与 waiting 快照判断，**不新增状态机**；不得在仅收到 accepted 时宣称"已应用"或永久清除等待事实。用户后续输入沿普通对话处理；确需再次提问由 Runner 正常产生新 wait；不为旧表单增加重新打开、追加作答或续答状态。

**Q 编号回复协议（三分法 + 归一化优先）**：

| 输入特征 | 分类 | 处理 |
|---|---|---|
| 无编号协议特征的自然语言 | 自由文本 | 兼容路径透传 |
| 明确使用协议（出现题号）但含无法解释项：未知题号、越界选项号、text 题带选择、同题矛盾答案 | 协议错误 | 拒绝提交 + 纠正提示（指明非法项；一次，不计数）；无害差异归一化不纠正 |
| 混合（部分题可解析、部分无法解释） | 协议错误 | 整单拒绝 + 纠正提示（列出明细），不静默丢弃 |

- 按行解析：以题号开头的行开启新题答案，非题号开头的行视为上一题 text 续行；`required` 校验遍历整个原 form。
- 渠道发送形态：飞书 post / 企微 markdown / 钉钉 sampleMarkdown / wecom_kf text / RPA text，模板带显式题号 Q1/Q2，选项按题从 1 编号。

### 7.3 未来机会（不在一期范围，实施前单独立任务过契约检查点）

渠道原生卡片（飞书 interactive + `card.action.trigger` 优先）；跨入口作答（前置：可信账号绑定、任务发现入口、跨入口读取/控制授权）；提问通知跨任务频控；`expires_at` 超时语义（Phase 3 评估）。

## 8. 安全、限制与发布兼容

- **授权澄清**：ask_user 只收集业务选择，不改变工具权限/workspace grant/任务许可；"同意"不扩权；`allow_skip=false` 是结构化路径的业务门；跳过不构成批准；授权缺失的步骤恢复后仍被原有权限检查阻断。
- 普通澄清 reply 不转换为许可/授权（契约既有约束沿用）。
- 身份、租户与任务归属校验、wait 对应关系、重复消费防护与幂等**全部沿用现有机制**。
- 载荷限制：form 总载荷 8KB；`answer` 字段保持既有口径（`max_length=1_000_000` 字符 + 完整控制请求的 UTF-8 字节闸门，结构化 answers 纳入现有请求字节计算，不新增限制，§4.1）；渠道预算真实约束在投递层生效。
- **发布兼容（能力发现，不依赖旧服务端新错误码）**：
  - 既有 `GET /api/chat/runners/capabilities` 增加可选能力字段（如 `ask_structured_answers: true`）；**前端会话启动时读取一次并缓存**，不做每次提交探测；字段缺失（旧服务端）→ 前端保持客户端 canonical 文本路径（灰度/回滚窗口专用兼容，该窗口服务端权威校验不生效）。
  - **发布顺序**：后端兼容实例**全部就绪**后再启用前端结构化能力（feature flag）；滚动期以"未确认支持 = 文本路径"为判定基准；不用重复探测解决发布问题。
  - 网络结果不明：不降级，按原意图原键重试。
  - 回滚：回滚目标需保留 ask 受理能力处理在途 form wait，或回滚前取消在途 ask 任务——写入部署注意事项。

## 9. 分阶段实施

| 阶段 | 内容 | 验收要点（聚焦实际改动，不穷举格式组合） |
|---|---|---|
| Phase 1：协议 + 权威校验 + Web + 渠道文字全渠道 | schema/fixture（幂等四例）；`ask_user`（服务端 id + root 等待 + 硬校验）；入口模型修订；归一化 + 判定表 + 校验失败 422 不落库；canonical 服务端生成 + 受理 payload 附执行记录；wait/snapshot 白名单；supplementalInputs 投影扩展（历史只读回显）；Web QuestionCard（状态机/换键/上下文重建）；capabilities 能力字段 + 前端缓存降级；渠道渲染按实际字节分段（逻辑行优先 + 单行超限字符边界跨消息）+ wecom_kf 预算核算；入站单管线 | 正常提交、跳过、幂等四例；必要非法输入（未知题/越界/矛盾/text 带选项）422 逐题明细且不消费 wait；校验失败直接重提（无专门键管理）；已受理后同键改答案拒绝；pending 自然语言直接作为答案不吞；"重发"续传只发缺段；受理未应用期间重发暂停，应用成功后清理剩余记录，被拒且 wait 有效可修正重发；单行超限跨消息完整发送、续段保留题号/选项号、内容不丢失；历史回显（已答/被拒/未答取消）；双端提交与刷新重建；root 等待回归；旧快照严格解析器兼容 |
| Phase 2：同源待答可达性 | `runner_waiting_input` 通知生产者（挂 park 事务，复用统一通知基础设施）；站内信 + Web Push；会话列表「待回答」标识（Web 同源任务） | 通知到达与回跳；多次提问频控；渠道不受影响 |
| Phase 3：增强项（按需） | `expires_at` 语义冻结；capabilities 正式协商扩展；legacy SSE form 透传；埋点 | 过期不产生孤儿计费；严格解析器兼容门 |

未来机会（单独立任务）：渠道原生卡片、跨入口作答（§7.3）。

## 10. 评审意见落实对照（四/五轮）

| 轮次/意见 | 落实位置 |
|---|---|
| 四轮 1. pending 吞自然语言回答 | §7.2 入站单管线（不分投递状态；其余输入一律沿 reply 路径；补发仅显式请求） |
| 四轮 2. wait 消费后不应补发旧题 | §7.2（应用成功后清理剩余记录；后续走普通对话；无续答状态） |
| 四轮 3. 校验失败无需持久化 rejected control | §4.2、§5.7（422 + errors 不落库不消费 wait；直接重提；已受理走既有机制） |
| 四轮 4. 删除"字符上限保证单题单段"前提 | §4.1（删除算术保证）、§7.2（按实际渲染字节分段） |
| 四轮 5. 文案长度建议不成为硬门 | §4.1（建议与服务边界两分）、§6.3 |
| 五轮 1. "受理即消费"不符异步机制 | §7.2 受理与应用边界（受理暂停补发；应用成功清理；被拒且 wait 有效可修正/重发；复用既有 control 状态与快照，不新增状态机） |
| 五轮 2. 单行超限仍须完整发送 | §7.2 渲染分段（逻辑行优先 + 合法字符边界跨消息，续段保留题号/选项号） |
| 五轮 3. 载荷口径准确化 | §4.1、§8（`answer` 字符上限与请求字节闸门分列；answers 整体复用现有请求总量限制） |
| 实施收敛原则 | §3.8、§6.5、§8（禁扩张清单固化）、§9（验证聚焦） |

## 11. 开放问题

1. root 执行 ask_user 等待与 final_answer 流、`task_record` 语义交互（Phase 1 首个验证项）；
2. wecom_kf 预算核算与 `WeComKfReplyBudget`/`reserve_for_final` 现状核对（提问轮次 final 预留规则）；
3. 恢复链路对撤权/设备/插件事实的重验覆盖面核对（复用既有执行边界）；
4. 渠道用户长期只回自由文本时 `required` 无法强制的体验补偿（Phase 2 通知/badge 是否缓解）；
5. "重发"/"简化"/"跳过"渠道指令与现有 hidden_commands 机制的注册关系（是否统一收口）。
