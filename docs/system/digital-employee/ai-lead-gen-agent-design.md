# AI获客智能体设计（AI Lead-Gen Agent）

> 状态：设计定稿，待开发
> 来源：workbuddy 技能包 `b2b-lead-gen` v3.0.0（2026-10-26 移植决策）
> 关联：`.claude/rules/skill_integration.md`（移植流程）、`.claude/rules/knowledge_retrieval.md`（知识库检索）、`docs/system/agent-web-search-enhancement-design.md`（搜索服务）

## 1. 背景与定位

### 1.1 智能体定位

面向企业销售/外贸人员的 B2B 客户开发数字员工：从产品资料出发，通过网络搜索挖掘目标市场潜在客户与关键联系人邮箱，撰写个性化开发信并完成发送、回复监控与意向通知的完整闭环。

- **对用户名称**：AI获客
- **标识符**（subagent 目录名 / 表前缀 / 技能名）：`ai-lead-gen`
- **承载方式**：新建数字员工 `subagents/ai-lead-gen/SUBAGENT.md` + 移植技能 `src/skills/ai-lead-gen-1.0.0/`

### 1.2 与外贸获客智能体（trade-specialist）的关系：替换下线

trade-specialist 是 4 个月前的演示性质实现，与本智能体是**替换关系而非迭代关系**：

| 维度 | trade-specialist（旧） | AI获客（新） |
|---|---|---|
| 客户来源 | `content_generate` 虚拟生成（邮箱统一 `luwei@aidingyi.cn`） | web_search 真实搜索 + 官网联系人挖掘 |
| 搜索 | 提示词明确禁止 web_search | 核心能力就是搜索 |
| 公司介绍 | 硬编码在 SUBAGENT.md | 用户产品资料（临时上传 + 知识库） |
| 合规/送达率 | 无 | 合规铁律 + 域名体检 + 节流日限 |
| 半自动/全自动 | 无 | 同一管线 + review/auto 闸门 + 定时任务 |

两者提示词层面的约束根本对立（一个禁搜索、一个全靠搜索），无原地升级空间。新智能体验证稳定后 trade-specialist 整体下线，方案见第 11 节。

### 1.3 需求清单映射

| # | 需求 | 承载方案 |
|---|---|---|
| 1 | 产品资料（对话临时上传 / 知识库维护） | §6 双通道接入 |
| 2 | websearch 搜索客户名单（公司 + 联系人姓名/邮箱/职衔） | §7 委托协议对接 web_search / web_fetch |
| 3 | 客户名单入库 | §5 数据模型（bs_ai_lead_gen_* 五表） |
| 4 | 逐一撰写针对性邮件 | §8 邮件生成管线（读产品资料 + 客户情报） |
| 5 | 半自动 / 全自动发送 + 每日定时 | §8.3 同一管线 review/auto 闸门 + §9 定时任务 |
| 6 | 发送结果入库 | §5 `bs_ai_lead_gen_emails` |
| 7 | 回复自动接收、意图判定、意向通知 | §10 IMAP 轮询 + LLM 意图判定 + 通知 |

## 2. 整体架构

```
用户（web 对话 / 渠道）
  → 数字员工 ai-lead-gen（subagents/ai-lead-gen/SUBAGENT.md）
    → 技能 ai-lead-gen-1.0.0（编排：搜什么/爬谁/怎么判/怎么写/怎么发）
      → 宿主工具：web_search / web_fetch（检索层）、knowledge_base_search（产品资料）、
                  email_send / email_read（收发）、scheduler 定时任务工具
      → 技能脚本：lead_db.py（入库闸门）、email_rules.py / email_verify.py（邮箱判定校验）、
                  domain_check.py（域名体检）、send_emails.py（节流群发）
    → PostgreSQL bs_ai_lead_gen_* 五表（名单/联系人/邮件/回复/发送记录）
    → storage/tenants/{tid}/lead-gen/{campaign}/ （brief、情报、草稿等工作产物）

定时链路（全自动模式）
  → src/scheduler（ScheduledTaskDB + APScheduler，每日 cron）
    → ScheduledTaskExecutor 以用户身份在 cron 会话执行 task_prompt
      → 同一条获客管线（auto 闸门：跳过人工确认）
      → 完成后 notification_service 汇总通知（站内信 + 渠道）

回复监控链路
  → 定时任务轮询发件邮箱（IMAP，email_lib 两段式过滤）
    → 按 Reply-To/message_id 归属 campaign → LLM 意图判定 → 有意向 → 通知用户
```

### 2.1 复用与新增边界

| 类别 | 内容 |
|---|---|
| 复用 | `web_search` / `web_fetch`（65f69db）、`email_send` / `email_read`（src/tools/email/）、`knowledge_base_search`、`src/scheduler/`、`notification_service`、Runner 执行链路 |
| 新增（移植改造） | 技能 `src/skills/ai-lead-gen-1.0.0/`（SKILL.md + scripts + references）、`subagents/ai-lead-gen/SUBAGENT.md`、五张 `bs_ai_lead_gen_*` 表、回复监控定时任务、campaign 管理脚本 |
| 明确不做 | 不新建渠道接单闸门；不自建搜索/爬取通道（委托宿主工具，二期评估移植 web-search-pro 兜底）；不动 recap 机制（每日循环走 scheduler，不走 every_round recap） |

## 3. 技能包移植方案

### 3.1 源包评估

workbuddy `b2b-lead-gen` v3.0.0 结构成熟：11 步工作流（需求澄清 → 搜索源 → 找客户 → 筛选 → 定位官网 → 情报 → 联系人 → 社媒 → 写邮件 → 发送），10 条铁律（不猜测邮箱、一人一封、合规优先、域名体检、邮箱对口判定等），检索/爬取已剥离为 SEARCH_TASK / SCRAPE_TASK 委托协议——恰好对接我方「技能编排 + 宿主工具执行」架构。

### 3.2 口径改造点（移植时逐项落实）

| workbuddy 原口径 | 我方口径 |
|---|---|
| 委托「外层智能体 WebSearch / WebFetch / web-search 技能」 | 委托宿主工具 `web_search` / `web_fetch`；`SEARCH_TASK`/`SCRAPE_TASK` 协议保留为编排内部约定，不再要求用户贴回结果（同会话内技能直接驱动工具执行） |
| `workspace/campaigns/<name>/` CSV 落盘（leads.csv / contacts.csv） | 名单/联系人/邮件/回复全部落 `bs_ai_lead_gen_*` 表（§5）；brief、intel、emails 草稿等工作产物落 `storage/tenants/{tid}/lead-gen/{campaign}/`（走 `get_tenant_storage_path`，scene 新增 `lead-gen`） |
| `scripts/lead_db.py` CSV 读写 | 改造为 DB 读写 + 入库闸门（relevance/source/confidence 校验、去重逻辑保留），表结构见 §5 |
| `scripts/send_emails.py` 直连 SMTP | 对接我方 `email_send`（SMTP 凭据经租户邮箱配置，不走技能包 `smtp_config.json`）；节流/日限/每域 3 封逻辑保留在发送编排层 |
| `scripts/email_rules.py` / `email_verify.py` / `domain_check.py` | 原样移植（无宿主依赖），判定口径与 SKILL.md 铁律一致 |
| 发件人档案 `sender_profile.json` 落 workspace | 落租户目录 `lead-gen/sender_profile.json`（域名体检结果 + 合规判定跨 campaign 复用）；SMTP 密码仍只存环境变量引用，不落明文 |
| 需求澄清用 AskUserQuestion | 对话内直接多轮提问（我方无该交互协议，按第 1 步的多轮节奏在对话里问） |
| references/ 七个参考文档 | 原样移植（检索总纲、搜索引擎地图、搜索词模板、联系人发现、邮件模板、合规、送达率、域名预热） |

### 3.3 首次移植检查清单（按 skill_integration.md §6）

- [ ] 与 whatsapp-messaging / email 工具的边界划分（发信走 email_send，非 WhatsApp 通道）
- [ ] 剥离 workbuddy 专有路径（venv、AskUserQuestion、腾讯文档导出改为可选说明）
- [ ] SkillRegistry 加载、skill_execute 调用、白名单配置（subagent_definitions.skills.allowed）
- [ ] `tests/unit/test_ai_lead_gen_skill.py` 基础单测（入库闸门 / 邮箱判定 / DB 读写）
- [ ] requirements.txt 依赖核对（httpx / bs4 / lxml 容器内已具备性确认）

## 4. 数据模型（5 张 bs_ 表）

按 database_dev.md 规范：`bs_ai_lead_gen_*`（目录名连字符转下划线）、必含 `tenant_id` / `user_id` / `created_at DEFAULT CURRENT_TIMESTAMP`、外键完整性应用层保证、技能加载时 `init_tables()` 幂等建表、同步登记 `deploy/init-postgres.sql` 与 `deploy/db_update.yaml`。

```
bs_ai_lead_gen_campaigns        获客活动
  id, campaign_name, tenant_id, user_id,
  brief            TEXT        需求说明书（产品/ICP/地域/决策人/CTA/数量，含用户确认记录）
  target_region    TEXT        目标国家/地区
  send_mode        TEXT        review | auto（默认 review）
  status           TEXT        draft | searching | drafting | awaiting_review | sending | paused | completed | failed
  daily_limit      INT         日发送上限（默认 20，预热分档上限 200）
  scheduler_task_id TEXT       关联 scheduler 定时任务 id（auto 模式）
  reply_mailbox    TEXT        回信监控邮箱（reply-to 归属锚点，= 解析后的发件邮箱，见 §7.4）
  send_mailbox_level TEXT      实际使用的邮箱层级：user | tenant（审计用，见 §7.4）
  product_refs     TEXT        产品资料引用（知识库 doc_id 列表 / 会话附件 file_id 列表，JSON）
  updated_at

bs_ai_lead_gen_leads            客户公司清单
  id, campaign_id, tenant_id, user_id,
  company_name, country, industry, website,
  score            INT         筛选打分（地域/规模/相关性/痛点/可得性）
  status           TEXT        new | qualified | rejected | contacted
  reject_reason    TEXT        no_official_site | no_email | low_score | ...
  source_url, notes, updated_at

bs_ai_lead_gen_contacts         联系人（含邮箱闸门字段）
  id, campaign_id, lead_id, tenant_id, user_id,
  name, title, email,
  email_source     TEXT        official_site | directory | verified_pattern | guessed | manual
  email_type       TEXT        personal | department | generic | unknown
  email_function   TEXT        职能标签（sales/procurement/hr/...，email_rules.assess_email 判定）
  email_relevance  TEXT        relevant | generic | unknown
  confidence       TEXT        high | medium | low
  language, phone, linkedin, notes, updated_at
  （入库闸门：relevance=relevant 且 source!=guessed 且 confidence∈{high,medium}，由 lead_db.py 强制）

bs_ai_lead_gen_emails           邮件草稿与发送记录
  id, campaign_id, contact_id, tenant_id, user_id,
  subject_a, subject_b, ab_group, body,
  status           TEXT        draft | approved | sending | sent | failed | bounced
  message_id       TEXT        SMTP Message-ID（回复匹配锚点）
  sent_at, error, retry_count, updated_at
  （subject_a/subject_b 为 A/B 两版主题；发送时按 ab_group 择一，结果回写 status/message_id/sent_at/error）

bs_ai_lead_gen_replies          客户回复
  id, campaign_id, contact_id, email_id, tenant_id,
  from_addr, subject, body, received_at,
  intent           TEXT        interested | info_request | refused | irrelevant | unclear
  intent_summary   TEXT        LLM 意图摘要（一句话 + 建议动作）
  notified         BOOLEAN     是否已通知用户
```

索引：campaign_id、(tenant_id, status)、emails.message_id（回复匹配查询）。租户隔离：所有查询带 `tenant_id`；表级名单机制（knowledge_retrieval.md §4.1）不适用（本表非知识表），但 campaign 内数据按 user_id 可见范围由业务层约束。

另新增系统表（非 bs_ 业务表，对齐 `user_email_settings` 定位）：`tenant_email_settings`——租户级邮箱配置（§7.4 第 2 层），tenant_id 唯一，字段与加密方式对齐 `user_email_settings`，登记 `deploy/init-postgres.sql`、`deploy/db_update.yaml` 与 `database_system_table.md`。

## 5. 产品资料接入（需求 1）

两条通道，写入 campaign 的 `product_refs`：

1. **对话临时上传**：用户在对话界面传 docx/pdf/pptx/xlsx → 走现有附件链路（`storage/tenants/{tid}/conversation/`，file_id 登记）；技能第 1 步需求澄清时收集，`read` 工具读取内容进入 brief。
2. **知识库维护**：用户事先在知识库维护产品资料栏目 → `knowledge_base_search` 检索（走 knowledge_retrieval.md 全部规范：`resolve_category_scope` 栏目授权 + `load_shared_ranges` 共享范围 + 表级名单，`subagent_id` 从 ToolExecutionContext 读取）。撰写邮件时对每家客户按需检索产品卖点/报价/案例。

brief.md 生成时固化本次 campaign 使用的资料清单与摘要，保证同一 campaign 内口径一致，不随知识库后续变动漂移。

## 6. 客户搜索与联系人挖掘（需求 2、3）

### 6.1 检索层对接

技能发出搜索/抓取任务（协议沿用 SEARCH_TASK/SCRAPE_TASK 的字段结构作为编排约定），由本智能体在同会话内用宿主工具执行：

- `web_search`（DeepSeek 默认）：找客户清单、找官网、找公开引用。搜索词模板按 `references/lead-search-playbook.md`，引擎/名录地图按 `references/search-engine-map.md`
- `web_fetch`：读官网首页/About/Contact/Team 页正文（含文字型 PDF），原文经 evidence 机制落盘后 `read` 分页读取
- 结果质检铁律 7 保留：返回结果必须与 query 相关，无关结果整批丢弃绝不入库

### 6.2 已知能力缺口与降级策略

| 缺口 | 影响 | 首期策略 | 二期 |
|---|---|---|---|
| 无国别/语言引擎路由（Naver/Yandex/Yahoo!Japan 等） | 非英语市场检索质量下降 | DeepSeek 跨语言检索 + 目标语言本地词模板弥补；效果不足的市场如实上报 | 移植 web-search-pro 作为降级技能（走 skill_integration.md 流程） |
| 无 companies-only 硬过滤 | 词典/百科/黄页混入 | 提示词层过滤 + lead_db 入库时按域名黑名单剔除（web-search-pro 的过滤清单可借鉴为静态名单） | 同上 |
| web_fetch 不做 JS 渲染 | JS 渲染官网拿不到正文 | 该公司标 `reason=site_js_rendered`，不编造情报；占比过高时单独评估 | 评估引入 Playwright 渲染兜底（容器体积/稳定性代价另行立项） |
| 无邮箱验证工具（Hunter/Snov 类） | 模式推断邮箱 confidence 上限 low，不能发送 | 只发官网明示（high）与验证通过（medium）邮箱；推断邮箱不进发送队列 | 评估接入第三方验证 API（涉及计费，另行评审） |

## 7. 邮件生成与发送管线（需求 4、5、6）

### 7.1 生成

- 每家 qualified 客户先产出情报档案（intel，官网真实内容推断，痛点证据不足写"证据不足"）
- 邮件结构按 `references/email-copybook.md`：双主题 A/B、钩子（具体事实）、相关性（用客户官网语言）、证据、低承诺 CTA、签名、合规要素；80～150 词、目标语言本地化
- 产品卖点从产品资料（§5）检索注入，每个客户的引用可追溯

### 7.2 同一条管线，一个闸门（需求 5 核心）

```
搜索入库 → 联系人挖掘 → 写草稿（emails.status=draft）
        ↓
send_mode 闸门
  ├─ review（默认，首次使用）：会话内展示名单 + 草稿 → 用户确认/修改
  │    确认 → status=approved → 发送；修改意见回写草稿与联系人记录
  │    （用户逐条修改成为后续 auto 模式的先验，沉淀在 DB）
  └─ auto：跳过确认直接 approved → 发送（对应源包铁律 5 的"确认后不再逐次询问"）
```

两条模式共用同一套脚本、同一套表、同一套日限节流，只差闸门开关。campaign 级 `send_mode` 可随时切换（对话里说"改为全自动"即可）。

### 7.3 发送纪律（源包铁律保留）

- 发送前强制域名体检（domain_check.py，SPF/DKIM/DMARC，独立子域优先），结果存 sender_profile 跨 campaign 复用
- 合规检查按目标国（GDPR/CAN-SPAM/PIPL/CASL），退订说明进首封邮件
- 每联系人一封；同一域名 24h ≤3 封；日限预热分档 20→200；发送间隔 90～150s，`send_emails.py` 支持切段续跑（已发自动跳过，幂等靠 emails.status）
- 无关职能邮箱（hr@/jobs@/finance@）与公开窗口（info@/contact@）不进发送队列
- 发送结果逐封回写 `bs_ai_lead_gen_emails`（status/message_id/sent_at/error），退信标 bounced

### 7.4 发件/收件邮箱配置（三层解析，2026-10-26 定案）

本智能体的 SMTP 发送与 IMAP 回复监控使用**三层邮箱配置**，解析顺序：

| 层级 | 配置方 | 存储 | 生效范围 |
|---|---|---|---|
| 1. 用户个人邮箱 | 租户用户本人（对话界面/设置入口） | `user_email_settings` 表（**已有**，`EmailCredentialDB` 加密存储 + `/api/email-settings` 自助管理含测试发送） | 该用户发起的 campaign |
| 2. 租户邮箱 | 租户管理员（企业设置页 `/t/{tenant_id}/settings`） | `tenant_email_settings` 表（**新增**，字段对齐 user_email_settings + tenant_id 唯一，密码同款加密；登记 init-postgres.sql + db_update.yaml + database_system_table.md） | 本租户全部用户的兜底 |
| 3. 系统 .env（SMTP_*/IMAP_*） | 平台 | 环境变量 | **本智能体禁止使用**——仅用于平台级通知功能，冷邮件发送与回复监控不得回退到系统邮箱 |

解析规则：

```
解析发件邮箱(campaign)：
  用户个人邮箱已设置 → 用之
  否则租户邮箱已设置 → 用之（记录实际使用层级，写入 campaign.send_mailbox_level 便于审计）
  两者皆无 → 报错提示：请先在「邮箱设置」配置个人邮箱，或联系租户管理员配置租户邮箱
           campaign 状态保持 draft，不进入发送环节
```

- **发件与收件同源**：`reply_mailbox`（§9 回复监控锚点）= 解析后的发件邮箱，IMAP 凭据与 SMTP 同一套配置，发送后自动开始监控该邮箱的回复
- **与 sender_profile 衔接**：用户级/租户级邮箱配置变更（换邮箱/换服务器）时，对应层级的域名体检结果与合规判定作废，下次发送前重新体检；同一层级配置未变则复用
- **前端口径**：用户个人邮箱入口复用现有邮箱设置页；租户邮箱入口加在企业设置页 `/t/{tenant_id}/settings`（`TenantSettings.vue`，仅租户管理员可见，参照页内「通知渠道」分区的组织方式新增「租户邮箱」分区，支持深链锚点），均带测试发送验证
- **安全**：凭据只在发送/收信时经工具层解密使用，不进 LLM 上下文；`emails` 表和通知中不落 SMTP/IMAP 密码任何形态

## 8. 定时任务与全自动循环（需求 5）

复用 `src/scheduler/`（ScheduledTaskDB 持久化 + APScheduler + ScheduledTaskExecutor）：

- 用户切换 auto 模式时，通过现有 scheduler 工具创建每日 cron 任务，task_prompt 固定模板："执行获客活动 {campaign_name} 的每日循环：按 brief 搜索补充名单 → 挖掘联系人 → 撰写邮件 → 按 send_mode=auto 发送 → 汇总今日结果"，执行身份为创建用户，落 cron 专属会话
- campaign 的 `scheduler_task_id` 回写关联；切回 review 模式时暂停对应任务
- 执行结束由管线末段触发汇总通知（§9）：新增客户 N、有效联系人 M、发送 X、失败/退信 Y、新回复 K、有意向 Z
- 长耗时风险：一轮完整循环含大量搜索/抓取，受 Runner 轮次上限约束；首轮实测确定单日循环的名单批次上限（源包纪律：每轮 30～50 家候选、并发 ≤3），必要时拆分"上午搜索/下午发送"两个 cron 任务

## 9. 回复监控与意向通知（需求 7）

- **锚点**：发送时统一设置 `Reply-To` 为 campaign 监控邮箱（`reply_mailbox`），并落 `message_id`；回复按 In-Reply-To/References 优先匹配，退化为按发件人邮箱匹配 contact
- **轮询**：定时任务（建议每小时 1 次，随 campaign 存在而存在）用 `email_lib` IMAP 两段式过滤读取新邮件（凭据按 §7.4 解析后的邮箱配置），去重后写 `bs_ai_lead_gen_replies`
- **意图判定**：LLM 单次调用判 `intent`（interested / info_request / refused / irrelevant / unclear）+ 一句话摘要 + 建议动作；计费按 billing_audit.md 走 `record_background_llm_usage`（source=ai_lead_gen_reply_intent）
- **通知**：intent ∈ {interested, info_request} 时经 `notification_service` 发站内信 + 渠道推送（用户绑定渠道），内容含客户、联系人、摘要、建议动作；refused/irrelevant 只落库供名单优化，不打扰
- 监控任务幂等：按 message_id 去重，重复投递不重复通知

## 10. trade-specialist 下线方案（需求确认 1）

原则：一次性删干净，避免两个"获客"入口并存引发混淆。分两步走——**新智能体上线验证后再执行删除**（先共存观察，删除前在 docs/ideas.md 标注）。

| 类别 | 清单 |
|---|---|
| 子智能体 | `subagents/trade-specialist/`（整目录删除） |
| 技能 | `src/skills/trade-customer-1.0.0/`（整目录删除，含 init_tables） |
| 工具/注册 | `src/core/skill_executor.py`、`src/subagents/factory.py`、`src/mcp/tools.py` 中 trade 相关引用；`configs/config.yaml` 主智能体技能白名单中的 `trade-customer` |
| API | `src/api/customer.py`（/customer-info 数据接口，整文件删除并摘除 main.py 挂载） |
| 前端 | `frontend/web/api/customer.ts`、路由 `customer-info` 页面、`agentRoutes.ts` 条目、`PortalLayout.vue` / `sessionGreetings.ts` / `MenuSidebar` 等引用与测试用例 |
| 页面元数据 | `configs/page_metadata.yaml` 中 trade-specialist 相关 page 条目 |
| 数据库表 | `bs_trade_specialist_matched_customers`、`bs_trade_specialist_customer_emails` 及执行日志表：`deploy/db_update.yaml` 新增 `DROP TABLE IF EXISTS` 增量块（生产执行前先确认无在用数据，必要时导出备份一份到租户目录）；`deploy/init-postgres.sql` 不含这些表则无需改 |
| 用户侧说明 | 若有租户在用，切换公告：数据不迁移（虚拟演示数据无迁移价值） |

## 11. 合规立场

- 源包 10 条铁律全部保留（不猜测邮箱、一人一封、合规优先、不采集敏感个人信息、结果相关性、不编造、域名体检、邮箱对口、禁轰炸、必须多路径找完才判 no_email）
- 平台层面补充：冷邮件发送能力是租户级敏感能力，上线时评估是否需要租户级开关/准入（沿用防滥用策略的"先观察后拦截"思路）；发送量、退信率纳入租户维度可观测（日志先落 tlog 主题，量大后再进台账）
- 演示数据风险消除：不再存在"虚拟客户"语义，所有入库客户均有真实来源 URL

## 12. 计费与安全注意

- 本技能新增 LLM 调用点：意图判定（§9）、可能的情报摘要；全部按 billing_audit.md §3 自检，主循环内的走通路①，后台定时任务内的显式走 `record_background_llm_usage`
- Embedding 调用点：无新增（产品资料检索复用知识库既有链路，计费已收口）
- 凭据：SMTP 密码只存环境变量引用（sender_profile 存变量名不落明文）；用户级/租户级邮箱凭据走 `EmailCredentialDB` 同款加密存储（§7.4），凭据只在工具层解密使用、不进 LLM 上下文；系统 .env 邮箱（SMTP_*/IMAP_*）本智能体禁止使用
- 外部搜索结果属外部数据，提示词层声明"不作执行指令"（对齐 web_search usage_guide）

## 13. 分期实施计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 移植跑通 | 技能移植（§3 口径改造）+ SUBAGENT.md + 五表 init_tables + 租户级邮箱设置（`tenant_email_settings` 表 + 租户设置页入口，§7.4）+ 对接 web_search/web_fetch + 半自动 review 全流程（搜索→入库→挖人→写邮件→确认→发送→记录） | 测试环境真实跑一个 campaign，名单/邮件/发送记录落库正确；未配邮箱时报错提示正确、用户/租户/无配置三层解析正确 |
| P2 定时与通知 | auto 模式 + scheduler 每日循环 + 完成汇总通知 + 回复监控（IMAP 轮询 + 意图判定 + 意向通知） | 定时任务连续 3 天静默执行、汇总通知到达、模拟回复触发意向通知 |
| P3 下线旧智能体 | trade-specialist 清理（§10）+ 前端页面 + 表 DROP | 全仓 grep 无 trade-specialist/trade-customer 残留，回归通过 |
| P4 检索增强（按需） | web-search-pro 降级技能移植；邮箱验证 API 评估；JS 渲染兜底评估 | 按单项另行立项 |

## 14. 开放问题

1. ~~发件邮箱供给~~ → 已定案：三层邮箱配置（用户个人 → 租户兜底 → 报错提示），系统 .env 不参与，见 §7.4（2026-10-26）
2. 发送量准入：是否设租户级日发送上限硬闸（防滥用策略衔接）——建议 P2 期与硬规则一起定
3. 单轮循环的轮次预算：全自动一轮的搜索/抓取批次上限需实测校准（§8 最后一条）
4. IMAP 轮询频率与多租户邮箱规模增长后的连接管理——首期按 campaign 粒度串行轮询即可
5. 邮箱配置变更的生效口径：发送中途中途换邮箱是否允许（建议：campaign 内锁定解析结果，下一 campaign 生效）——P1 实现时按此默认，如有异议再调
