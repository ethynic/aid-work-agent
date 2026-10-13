# 自动填表智能体设计

> 2026-10-13 立项调研。目标：让内部员工（销售员等）在微信 / 企微 / 钉钉 / 飞书中用文字或语音把客户信息、客户跟进记录等表单数据写入第三方系统（首期 10605 售前咨询系统），展示"通过接口代填第三方表单"的平台能力。该能力面向各行业客户均可理解，是后续承接客户 CRM 接入需求的样板。

## 1. 定位与边界

### 1.1 与现有智能体的关系

| 智能体 | 面向 | 数据方向 | 状态 |
|--------|------|---------|------|
| pre-sales（pre-sales-api 技能） | C 端客户（渠道外部用户） | 对话后经 recap 任务（external_push）把线索推给 10605 | 已上线 |
| **自动填表（本设计）** | **内部员工**（经 wecom_kf 等渠道接入，业务上是员工） | **对话主流程内同步**通过接口写入第三方系统表单 | 新建 |
| customer-followup（客户跟进智能体） | 内部 | 我方系统内管理线索/跟进 | **计划下线**（见 §9） |

关键差异：

- **不是 recap 任务**：写入在对话主流程内同步完成（工具/脚本同步执行 + 回执），不使用 external_push 异步推送。
- **面向内部员工**：渠道层面（wecom_kf）用户身份是外部客户，业务上是员工——因此写操作的准入完全依赖手机号身份验证（fail-closed，见 §4.5）。
- **写操作 + 回显确认 + 回执**：区别于 pre-sales 的"静默推送"，本智能体写前必须回显确认、写后必须回显单据编号。

### 1.2 典型场景

1. "帮我创建一个新客户，信息如下……" → 抽取字段 → 回显确认 → 查重 → 创建 → 回执客户编号
2. "XX 客户刚联系我，他们需要……" → 按名称/手机号查客户 → 新建客户或定位已有客户 → 创建跟进记录 → （按 10605 要求）同步客户状态与跟进汇总摘要 → 回执跟进记录编号
3. 后续扩展：采购单、入库单等任意租户上传了 API 文档 + 表单 schema 的第三方表单

## 2. 总体架构

复用既有机制，新增一个子智能体 + 一个技能：

```
渠道消息（微信客服/企微/钉钉/飞书）
  → 子智能体 subagents/auto-form-filler（系统提示词：表单填写流程编排）
    → 技能 src/skills/form-filling-api-1.0.0（仿 pre-sales-api）
        ├─ load_api_config.py   读取租户 API 文档（复用 tenant_api_doc.py）
        ├─ get_user_phone.py    手机号获取/短信验证/绑定落库（新增，见 §4）
        ├─ delegate_login.py    委托登录换 client_token（复用 pre-sales 脚本，Redis 缓存 23h）
        ├─ load_form_schema.py  拉取表单 schema（新增，见 §5）
        └─ http_api 工具        查重/创建/更新（复用 src/tools/network/http_api.py）
```

- **模块放置**：子智能体定义进 `subagents/auto-form-filler/`；技能进 `src/skills/form-filling-api-1.0.0/`；均为平台通用能力，不放 `tenant_custom/`（对接哪个第三方系统由租户 API 文档决定）。
- **工具白名单**：subagent `tools.additional` 引入 `http_api`；技能 `requires_tools` 声明同款（自动补全机制会兜底）。

## 3. 数据流（以"记录跟进"为例）

```
用户消息 → LLM 抽取意图与字段
  → 需要客户定位：module_listing_view 查客户（名称/手机号模糊匹配）
      ├─ 唯一命中 → 使用该客户
      ├─ 多条命中 → 列表回显让用户选
      └─ 未命中 → 询问"是否新建客户"，转创建客户流程
  → 字段补全：对照表单 schema 检查必填，缺什么问什么
  → 回显确认（md 表格或逐行文本，按渠道适配，见 §6）
      └─ 用户明确确认后才执行写操作；修改则回到字段抽取
  → module_data_create（module=lianxijilu）→ 得到 record_id
  → 跟进汇总摘要同步：读客户 genjinhuizongzhaiyao → 归纳 → module_data_update 更新
      zhuangtai + genjinhuizongzhaiyao（无重要信息可跳过，见 ext 文档 §9.1）
  → 回执："成功创建客户跟进记录 {record_id}（客户：XX）"
```

防重与失败续做：

- **客户查重**：创建前按客户名称 + 手机号查 `module_listing_view`，命中即提示"已存在，是否为其添加跟进"而非重复创建。
- **部分失败续做**：跟进记录创建成功但摘要同步失败时，回执中带 record_id 并说明"客户状态摘要未更新，可让我重试"；重试基于已生成 id，不重复创建。
- **错误码约定**（10605）：`Code=0` 成功；`Code=-1` 业务错误（原样转述给用户）；`Code=-99` client_token 失效 → 带 `force_refresh=true` 重跑 delegate_login 后重试一次。

## 4. 身份与鉴权设计

### 4.1 委托登录（复用已跑通机制）

`POST /api/v1/erp.delegate/login`，Header `Api-Authorize-Token: ${AGENT_TOKEN}`，参数 `mobile`；响应 `client_token`（有效期 1 天）+ `record_id`（10605 的 sys_user.id，即操作人）。delegate_login.py 以 Redis 缓存 23h，key 含 tenant_id / subagent_id / mobile / login_url。写操作时 10605 后端将 `create_user` 记为 `record_id`，操作人归属到员工本人——满足"用户级委托登录"要求。

与 pre-sales 的差异：pre-sales 的 mobile 来自 `record_lead_capture.assignee_phone`（人工配置的归属员工）；本智能体的 mobile 来自**对话用户本人**（§4.2），语义更直接，不存在"归属员工 ≠ 对话者"问题。

### 4.2 手机号获取三档（按优先级）

| 档 | 来源 | 说明 |
|----|------|------|
| A | 渠道自带 | 飞书/钉钉/企微应用经 `get_channel_user_info` 返回 mobile（agent_user_builder.py 已统一清洗入库） |
| B | users.phone 已绑定 | 此前短信验证通过落库过的手机号 |
| C | 对话内收集 + 短信验证 | 微信客服典型路径：智能体询问手机号 → 复用 `sms-verification` 技能发码 → 用户回复验证码 → 校验通过后**写回 users.phone**（落库后走 B 档，后续不再验证） |

C 档是唯一需要新增后端能力的点：sms_cli.py 仅负责发码/验码，**"验证通过 → users.phone 落库"由 get_user_phone.py 脚本调用一个新的内部 API 完成**（不经过 LLM 转述验证结果），脚本同时返回绑定结果供对话确认。

### 4.3 手机号归属策略

- 验证码只证明"此人拥有该手机号"。绑定前提是该手机号在 **10605 系统用户表中存在**（自动建号关闭时，delegate_login 对不存在/已禁用的手机号返回错误，智能体提示联系管理员建号）。
- **自动建号默认关闭**：内部员工场景应由管理员预先在 CRM 建号；`create_user` 开关保留为租户级可配置（api-meta 扩展字段 `delegate_auto_create: true|false`，缺省 false）。
- 同租户内 users.phone 冲突（手机号已是其他用户记录的手机号）：各自渠道用户记录独立绑定（10605 侧按手机号幂等），我方不做合并；若需限制单手机号单账号，后续按客户要求加。

### 4.4 凭证安全（沿用既有机制）

- `AGENT_TOKEN` 与 `client_token` 由脚本/http_api 经 ToolExecutionContext 的环境变量替换注入 header，**不进 LLM 上下文**。
- api-doc 文档中的凭证类占位符（`AGENT_TOKEN`、`user_token_name` 声明的 token）沿用 tenant_api_doc.py 的"不渲染"规则。

### 4.5 写操作准入（fail-closed）

无法确定手机号（三档均无）或验证未通过时，**拒绝一切写操作**，仅允许闲聊/引导。任何加到微信客服的陌生人在完成手机号验证前，不可能触碰租户的第三方系统数据。

## 5. 表单 schema（通用化的关键）

### 5.1 向 10605 提出的 schema 接口要求

10605 表单字段定义存于 `ide_attr` / `ide_table` 元数据表（含 `display_name` 中文名），但无对外 schema 接口。提出需求：

- 接口建议名：`POST /api/v1/erp.module/module_schema`，入参 `module`（如 `kehuxinxi`、`lianxijilu`）
- 响应：字段数组，每项含 `field`（英文字段名）、`display_name`（中文）、`type`、`required`（必填）、`enum_values`（枚举字典，如 zhuangtai 的五种取值）、`description`（填写说明）、`is_create_hidden`（新建时无需填写的字段，如 genjinhuizongzhaiyao）
- 鉴权：与业务接口一致（AGENT_TOKEN + client_token）

### 5.2 我方消费方式

- `load_form_schema.py` 运行时调用 schema 接口并做本地缓存（Redis，TTL 1h，key 含 tenant_id），避免每轮拉取。
- api-doc 模板（`configs/api_doc_templates/form-filling-api.md`）不再手写字段清单，只保留：Base URL、鉴权说明、module 清单、**业务规则**（如"填完跟进记录须同步客户摘要"）。字段细节一律以 schema 接口为准——租户接入新 CRM 时只需上传文档 + 第三方提供 schema 接口，无需我方逐字段梳理。
- 回显确认、必填补问、枚举校验均基于 schema 生成，这是"自动填表"能泛化到任意表单的前提。

## 6. 回显确认与渠道渲染

| 渠道 | 确认格式 |
|------|---------|
| web / 飞书 / 钉钉 | md 表格 |
| 微信客服 / 企微 | 待实测：机器人**文本消息**中 md 表格的渲染情况（用户已手动验证微信对 .md **文件附件**的预览效果良好、支持宽表滚动——但消息通道与文件预览路径不同，须在 wecom_kf 真机确认）。MVP 先用逐行"字段：值"文本 + 关键字段（手机号/金额/日期）显式强调；若文本渲染可用 md 表格再升级 |

确认交互约定：回显后必须等待用户明确"确认/正确/可以"类指令才执行写入；"不对/改一下"回到字段修正。语音输入（渠道 ASR）的转写错误由确认环节兜底，数字类字段在回显中加粗提示核对。

## 7. 安全与多租户

- **租户隔离**：AGENT_TOKEN 为租户级凭证（api-meta 按租户文档加载）；client_token 缓存 key 含 tenant_id + subagent_id + mobile；schema 缓存 key 含 tenant_id。
- **审计**：写入回执与失败均落 `chat_records`（主循环通路①自动覆盖）；绑定操作（users.phone 落库）在日志中记录 user_id + 脱敏手机号。
- **计费**：主循环 LLM 调用走通路①（agent.py 显式 add_llm_usage）；技能脚本无 LLM 调用、无新增 embedding/ASR 调用点；短信按条扣费见 §9.2。已按 billing_audit.md §3 自检，无新增缺口。
- **短信防滥用**：复用 sms-verification 的 60s 防秒刷 + 24h 配额，验证码不回显、日志脱敏规则不变。
- **短信计费**：所有 sms-verification 技能调用按条扣费，见 §9.2。

## 8. 实施计划

### P0（MVP：跑通典型场景）

1. 技能 `form-filling-api-1.0.0`：load_api_config / delegate_login（复用）/ get_user_phone（含绑定 API）/ load_form_schema
2. 手机号绑定内部 API + users.phone 落库
3. 子智能体 `auto-form-filler`：系统提示词按 §3 流程编排，覆盖客户信息（kehuxinxi）+ 跟进记录（lianxijilu）两个表单
4. 回显确认 + 回执 + 查重 + `-99` 刷新重试
5. wecom_kf 真机验证：手机号验证全流程、确认格式渲染
6. sms-verification 短信计费（§9.2，独立改动，可先行开发）

### P1

6. 表单 schema 接口接入（依赖 10605 开发）+ 缓存；未上线前短期以 api-doc 文档内字段清单兜底
7. 部分失败续做、客户多命中选择交互打磨
8. md 表格渲染实测后统一确认格式

### P2

9. 租户自上传 CRM 文档 + 第三方 schema 接口的通用接入；更多表单类型（采购单、入库单）
10. SSO 入口（sso_enabled api-meta 已支持）：用户从对话跳转浏览器查看已填单据

### 依赖 10605（qbweb3.1）侧的事项

| 事项 | 说明 |
|------|------|
| schema 接口 | §5.1 要求，通用化前提 |
| 文档补全 | 客户/跟进记录查询接口补入 10605 接口文档 |
| 自动建号关闭 | agent_token 配置 `create_user: false` |
| 委托写权限确认 | 确认 `_delegate_write_check` 对销售员身份写 kehuxinxi / lianxijilu（关联客户而非本人）的权限边界 |
| 确认渲染实测配合 | wecom_kf 真机环境 |

## 9. 配套变更：customer-followup 下线

实际客户需要的是"智能体代填自有 CRM"而非"在我方系统内管理 CRM"，customer-followup 从未实际跑通，保留会造成职责混淆。

- 管理后台将该数字员工**停用/下架**（subagent_definitions 状态变更，不删库）；业务数据 bs_ 表保留不动。
- `docs/ideas.md` 对应条目标记 ⏸️ 已搁置并注明由自动填表智能体取代。
- 已启用该智能体的租户（如有）提前通知。

## 9.2 配套变更：sms-verification 短信计费（独立于本智能体）

所有经 sms-verification 技能发送的短信验证码，**每成功发送一条扣对应租户 1 积分**（售价约 0.1 元/条），不限调用场景（自动填表、其他智能体、任何对话中的身份验证）。

- **扣费点**：`sms_cli.py` 的 `send` 成功路径（`send_sms_code` 返回 True、真实发信后，`_record_send` 同处），仅在真实发送时扣费；qb_sms_code bypass（未真实发信）不扣费。`verify` 不扣费。
- **落账方式**：`ChatRecordDB.create(source_type="sms_verification", credit_cost=1.0, tenant_id=..., user_id=..., user_message="[短信验证码] 发送至 138****0000")`——同事务原子扣减 `tenants.credit_balance`（src/db/models.py:1335 既有机制），审计与扣费一体落库。
- **tenant_id 来源**：从脚本执行上下文（ToolExecutionContext 注入的租户/用户标识）获取，**不信任 LLM 传参**；获取不到租户身份时拒绝发送（fail-closed），防止计费落空。
- **余额不足**：扣减失败（原子扣减 rollback）时发送失败，向用户返回"积分余额不足"错误，不提供绕行方案（沿用 SKILL.md"禁止回退方案"约定）。
- **范围边界（待产品确认）**：`src/api/auth.py:229,774` 两处 Web 登录验证码走的是底层 `send_sms_code` 直调、不经 sms-verification 技能，MVP 不纳入扣费（避免影响登录体验与平台自身成本归属）；如需全量按条计费，后续在底层 `send_sms_code` 收口并单独登记。

## 10. 开放问题

1. wecom_kf 文本消息中 md 表格渲染待真机实测（§6）。
2. 语音多轮长表单（字段 > 8 个）的补问节奏：一次性问全 vs 分批确认，MVP 取"一次回显全部缺失必填项"，实测后调。
3. 客户名称模糊匹配的阈值与去重规则（同音、简称），P0 用包含匹配 + 人工选择，不引入模糊匹配算法。
4. 命名：目录名 `auto-form-filler`、对外名称"自动填表"是否合适，待产品确认。
