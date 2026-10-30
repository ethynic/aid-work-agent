---
name: ai-lead-gen
description: >
  AI 获客技能：B2B 目标客户开发与冷邮件外联全流程。从需求澄清（brief）开始，
  检索目标客户名单（公司+联系人+邮箱），入库筛选后逐一撰写针对性邮件，
  邮箱就绪后自动批量发送或经定时任务发送（SMTP），发送结果回写数据库，
  客户回复自动接收并判定意向、有意向时通知用户。
  适用于外贸开发、客户线索挖掘、cold outreach、lead generation、
  邮件营销获客等场景。
version: 1.0.0
init_script: lead_db.py
requires_tools: [web_search, web_fetch]
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      bins: ["python"]
---

# AI 获客（B2B 冷邮件外联）

## 用途

把一段公司介绍 / 产品介绍 / 需求描述，转化为一份可发送的目标客户名单、
联系人清单和个性化外联邮件，并完成发送与回复跟进。

本技能是**编排层**：决定搜什么、爬哪个 URL、按什么口径判、怎么写邮件、怎么发。
实际检索与爬取按**降级链**委托执行：**默认直接用 `web-search-pro` 技能的
`pipeline.py --extract` 管线**（检索+抓页+邮箱/电话抽取一站式，零 LLM 消耗）；
平台 `web_search` / `web_fetch` 工具仅在管线全通道失败时兜底（见下文委托协议）。

## 数据与身份（本系统改造点，与原包差异必读）

1. **身份与数据库**：脚本由 `skill_execute` 在执行上下文中运行，
   `AID_TENANT_ID` / `AID_USER_ID` 环境变量由系统注入（不可伪造、不进提示词）。
   所有名单/联系人/邮件/回复数据存 PostgreSQL `bs_ai_lead_gen_*` 五表
   （campaigns / leads / contacts / emails / replies，启动时自动建表），
   **不要**使用原包的 workspace CSV 落盘存业务数据。
2. **发件邮箱三层解析**（`mailbox_resolver.py`，发送脚本自动调用）：
   用户个人邮箱（邮箱设置）-> 租户邮箱兜底 -> 两者皆无时脚本退出并提示用户去配置。
   系统 .env 邮箱仅用于平台通知，**本技能禁止使用**。SMTP/IMAP 密码只在脚本内存中
   解密，不落盘、不打印、不进对话上下文。
3. **campaign 工作目录**：`storage/tenants/{tenant}/lead-gen/campaign_{id}/`
   （brief.md、intel/、emails/、logs/、contact_targets.json）。退订黑名单在
   租户级 `lead-gen/unsubscribed.csv`，跨 campaign 共享。
4. **对话内 / 定时同一条管线**，只差 campaign 的 `send_mode` 编排方式：
   - `review`（对话内编排）：在当前对话中完成发送编排，邮箱就绪后直接批量发送；
   - `auto`（定时编排）：复用系统定时任务（scheduler）按 cron 以用户身份执行
     同样的发送编排。两种模式均**不做邮件预览、不做逐批确认**，发送前闸门
     （限额/体检/黑名单/职能对口）完全一致，技术细节只写 logs 不进对话。

## 铁律（违反会导致任务失败）

1. **不猜测邮箱**。邮箱必须来自官网页面、邮箱验证工具或公司公开文档；
   靠名字+域名拼出的邮箱一律标 `guessed`（confidence=low），入库闸门会直接拒收。
2. **每个联系人只发一封**，且必须在首封里说明"如何拒绝"。
   群发同一文案给同一公司多个联系人是明确禁止的。
3. **合规优先**。欧盟/英国 GDPR 合法利益评估；美国 CAN-SPAM（退订链接+真实寄件地址）；
   中国《个人信息保护法》；新加坡 PDPA；加拿大 CASL。见 `references/email-compliance.md`。
4. **不采集敏感个人信息**（种族、宗教、政治倾向、健康、金融账户、工会成员身份）。
   社媒只分析公开的商业相关内容。
5. **发送量纪律**：每域 24h ≤ 3 封；campaign `daily_limit` 默认 20
   （预热分档按累计已发：0-49→20/天，50-149→50，150-499→100，≥500→200）；
   `daily_limit < 20` 时发送脚本直接拒发。
6. **检索结果必须相关才可用**。搜索结果与查询无关（代理劫持特征）一律丢弃，
   宁可失败上报，也不污染名单。见 `references/retrieval-layer.md`。
7. **不编造邮箱**。官网抓不到就在 leads 里标 `reject_reason=no_email`，
   把通用邮箱伪装成负责人邮箱属于严重错误。
8. **发送前必须做域名体检**。SPF/DKIM/DMARC 未配好会静默进垃圾箱并损伤主域声誉。
   用 `scripts/domain_check.py` 确认无失败项，优先用独立子域。见 `references/domain-warming.md`。
9. **邮箱必须对口，不能只是"能发"**。招聘（hr@/jobs@）、财务、法务、公关邮箱与
   公开窗口（info@/contact@）**都不得进入发送队列**。判定由 `scripts/email_rules.py`
   统一负责，采集/入库/发送三道闸门强制执行。官网只有这些地址时不算"没有邮箱"，
   按 `references/contact-discovery.md` 路径 2→5 继续找，全部无果才判 `no_email`。
10. **数据库读写必须走 `lead_db.py`，禁止用 `python -c` 内联 SQL 直连数据库**。
   内联 SQL 绕过去重、闸门与配额校验（每公司联系人上限、职能判定），且不受
   可信身份校验保护。脚本执行报错时如实上报，由下一轮修正命令重试。
11. **禁止猜测任何数据库 ID**（campaign_id / contact_id / email_id / lead_id）。
   ID 只能来自本会话内命令的真实输出（init / add-contact / add-email-draft 的
   stdout）或 `list-contacts` / `list-leads` 的查询结果。拿不到 ID 就先跑
   `list-contacts` 查，报错信息里返回的有效 ID 清单直接取用，**不得编造连续
   编号去试**——历史实测盲猜 ID 连续 10 次 not found 空转烧掉大量迭代。

## 脚本清单（经 skill_execute 调用，均需 --campaign-id）

| 脚本 | 用途 |
|------|------|
| `lead_db.py` | 业务数据读写（子命令见下表） |
| `send_emails.py` | SMTP 发送引擎（限额/退订/退信分类/随机延迟） |
| `mailbox_resolver.py` | 发件邮箱三层解析（单独运行可自检配置） |
| `sender_profile.py` | 发件身份档案（from_name/退订说明/通信地址）与域名体检状态 |
| `domain_check.py` | SPF/DKIM/DMARC 域名体检 |
| `email_verify.py` | 邮箱格式/MX 校验 |
| `email_rules.py` | 职能相关性判定（三道闸门共用口径） |
| `reply_monitor.py` | 回复监控：IMAP 轮询 -> 归属 -> 去重 -> LLM 意图判定 -> 意向通知/退订 |

`lead_db.py` 子命令：

```bash
python lead_db.py init --name N --brief-file F --target-region R [--send-mode review|auto]
python lead_db.py set-target-config --campaign-id ID --json-file F   # contact_targets.json
python lead_db.py set-send-mode --campaign-id ID review|auto
python lead_db.py add-lead --campaign-id ID --json-file F              # 检索名单入库（自动去重）
python lead_db.py set-status --campaign-id ID --company C --status S [reason]
python lead_db.py add-contact --campaign-id ID --json-file F           # 联系人入库（闸门强制）
python lead_db.py add-email-draft --campaign-id ID --contact-id CID \
    --subject-a A --subject-b B --body-file F                          # 草稿（A/B 交替分组）
python lead_db.py set-email-status --campaign-id ID --email-id EID --status S
python lead_db.py export-send-queue --campaign-id ID                   # 生成 logs/send_queue.json
python lead_db.py bind-scheduler --campaign-id ID --task-id TID        # 绑定每日循环定时任务并切 auto
python lead_db.py unbind-scheduler --campaign-id ID [--cancel]         # 暂停(或取消)任务并切回 review
python lead_db.py daily-summary --campaign-id ID [--date D] [--notify] # 当日汇总 JSON（--notify 发站内信）
python lead_db.py score / export / list-leads / list-contacts / show-campaign ...
```

## 检索 / 爬取委托协议

流程走到需要检索/爬取的步骤（搜索名单、官网定位、联系人挖掘、社媒分析）时：

1. 用 `SEARCH_TASK` / `SCRAPE_TASK` 的 JSON 结构明确写出要搜什么 / 爬哪个 URL /
   按什么口径判（格式见 `references/retrieval-layer.md`）。
2. 按降级链委托执行：
   - **第一级（默认通道）**：`web-search-pro` 技能——**以 skill_name=web-search-pro
     发起 skill_execute**，跑 `python scripts/pipeline.py search "<query>" --extract --json`
     （名单检索/联系人挖掘一站式完成：检索→抓页→snippet 优先抽取邮箱/电话→掩码还原；
     纯 HTTP 零 LLM 消耗，且不再把大段搜索摘要灌进对话上下文）。
     不要在本技能的 skill_execute 里写这些命令——scripts/ 会被替换成本技能目录；
   - **第二级（兜底）**：平台 `web_search`（web-search-pro 全通道失败时换通道搜摘要）、
     `web_fetch`（直读已知 URL 补抓单页）；
   - 两级都失败：换关键词/引擎重试一次，仍失败则如实上报，不编造。
3. 多个独立搜索任务可并行委托；每轮委托都要带上相关性判定口径。
4. **禁止用平台 `web_search` 做常规名单检索/联系人挖掘**——它只返回摘要且每次调用
   计费，历史实测 23 次搜索仅换 4 个邮箱；仅在 web-search-pro 全通道失败时兜底使用。

## 用户沟通口径（问卷回显 / 发送汇报）

与用户的对话输出遵守以下口径，避免内部变量 dump 和技术细节外泄：

1. **问卷回显格式**：分行展示、不用 Q1/Q2 编号，默认值后给修改口子：

   ```
   已记录你的需求：
   · 目标地域：中国大陆
   · 目标行业：卫浴建材
   · 企业规模：不限（要限定直接说，如「50 人以上」）
   · 决策人：不限（默认优先创始人 / 采购 / 业务负责人）

   确认无误回复「开始」，我立即开始检索客户名单；要调整直接说要改哪项。
   ```

2. **不展示邮件预览**。邮件是批量发送（单批可达上百封），不做逐封预览、
   不展示正文、不解释 A/B 主题概念——A/B 两版主题仅内部交替分组，
   用于发送后效果统计。收件人入库、草稿撰写完成后直接置 approved 推进发送，
   不请求用户逐批确认。SPF/DKIM/DMARC 体检、dry-run、发件分组等技术细节
   只写 `logs/` 供事后排查，不进对话。

3. **发送后大白话汇报**，固定顺序：
   - 发给谁：本次发送 N 封（覆盖哪些行业，代表公司举 2-3 例）
   - 结果：成功 / 失败 / 退信各多少，退信率高时给一句建议
   - 主题效果：两版主题各发一半，哪版暂回复更好（样本少时注明「暂不足定论」）
   - 下一步：回复监控自动运行中，有客户回复会立即通知你

4. **每轮结尾给下一步预期**：明确一句「你回复 X 之后我会做 Y」
   （如「回复『开始』，我就先检索名单，完成后给你筛选结果」），
   动作词要短、可直接执行，不要让用户写自然语言长句。

## 全流程 10 步

1. **需求澄清（brief）**：问卷只**必问两项**——目标国家/地区、目标行业；
   企业规模、决策人职位等其余字段默认「不限」并按上下文合理默认填写，
   不发起连环追问（用户主动描述了规模/决策人/CTA 等信息时照常落 brief）。
   回显格式与确认方式见「用户沟通口径」。产出 brief.md 后
   `lead_db.py init` 建 campaign 并存 brief。
   `init` 会**前置校验发件邮箱**（个人邮箱或租户邮箱任一已配置）：未配置时拒绝创建
   并提示先去配置——这是刻意的 fail-fast，不要绕过，先引导用户/管理员配好邮箱再重跑。
2. **目标配置**：`set-target-config` 写入 contact_targets.json（目标职能/排除职能/产品一句话），
   三道闸门共用此口径。
3. **客户名单检索**：用 web-search-pro `pipeline.py search "<关键词>" --extract --json`
   （按目标国家/地区选当地引擎与行业名录关键词，挖名单加 `--companies-only`），
   结果过相关性判定后 `add-lead` 入库（自动按公司名/官网去重）。
4. **官网定位与筛选**：用 `pipeline.py search "公司名"` 或 `urls` 模式核验官网有效性，
   打分（score）并 `set-status` 标 qualified/rejected（带 reject_reason）。
5. **联系人挖掘**：用 `pipeline.py urls "官网首页,contact页,about页" --extract --json`
   抓官网 contact/about/team 页与名录（snippet 优先抽取 + 掩码还原），
   `add-contact` 入库。闸门自动拒收：无关职能/公开窗口/guessed 低置信。
6. **社媒兴趣分析**（可选）：用 `pipeline.py urls` 读公开商业内容，写入联系人 notes。
7. **撰写邮件**：先 `list-contacts` 取本 campaign 真实 contact ID 清单，再
   `add-email-draft` 逐联系人写针对性草稿（必写 subject_a/subject_b
   两版，各 ≤35 字符只差一个变量；公司简称放签名不进主题行），
   写完直接 `set-email-status` 置 approved——**不做邮件预览、不等用户确认**。
   contact_id 无效时报错会返回有效 ID 清单，直接取用，不要继续猜。
8. **发送准备**：`domain_check.py` 体检 + `sender_profile.py` 确认发件身份 +
   `mailbox_resolver.py` 确认邮箱配置就绪 + `export-send-queue` 生成队列。
   体检/队列等技术结果只写 logs，不向用户展开。
9. **发送**：先 `send_emails.py --campaign-id ID --dry-run` 干跑复查
   （无 BLOCK 才可发），再 `--limit 3` 小批量试发，最后全量
   （可配 `--max-minutes` 分段）。对话内编排（review）与定时编排（auto）
   都直接推进，不逐批确认。发送结果自动回写 emails 表
   （sent 含 message_id / failed / bounced），硬退信自动进退订名单。
   发送完成后按「用户沟通口径」大白话汇报。
10. **回复跟进**：回信按 Reply-To 锚点归因到 campaign，LLM 判定意向
    （interested/info_request/refused/irrelevant/unclear），有意向通知用户；
    负面回复自动进退订名单。日常由回复监控定时任务跑 `reply_monitor.py`
    完成（见下节）；对话中被问「有人回复了吗」时可直接执行一次
    `reply_monitor.py --campaign-id ID` 实时拉取。

## 定时任务与回复监控（auto 模式 / P2）

### 每日循环任务（auto 发送编排）

用户在系统「定时任务」中创建每日任务（建议上午执行一次），task_prompt 使用固定模板：

```
委派给 ai-lead-gen 数字员工执行获客活动 campaign_id={ID} 的每日循环：
1. 检索补充客户名单并入库（web-search-pro 管线，过相关性判定）
2. 对 qualified 名单挖掘联系人并入库（闸门自动拒收）
3. 为尚无草稿的联系人撰写针对性邮件草稿（subject_a/subject_b 两版）
4. auto 模式自动推进：域名体检 -> export-send-queue -> send_emails.py
   （先 --dry-run 无 BLOCK，再 --limit 3 试发，最后全量，限额系统强制）
5. 收尾执行 daily-summary --campaign-id {ID} --notify 发送当日汇总通知
```

执行成功后用 `bind-scheduler --campaign-id ID --task-id <定时任务ID>` 绑定并切 auto；
切回对话内编排用 `unbind-scheduler`（默认任务置 paused、send_mode 回 review），
campaign 彻底不再循环时加 `--cancel` 直接取消任务。
绑定/解绑只会操作当前租户当前用户名下的定时任务。

### 回复监控任务（每小时）

回复监控独立于每日循环，随 campaign 存在而存在（campaign 有在发或待回复状态即监控）。
用户在「定时任务」中创建每小时任务，task_prompt 使用固定模板：

```
委派给 ai-lead-gen 数字员工执行获客活动 campaign_id={ID} 的回复监控：
运行 reply_monitor.py --campaign-id {ID}（默认 limit 20 / 回看 14 天），
按 stdout JSON 汇报：检查数、去重数、新回复数、意向分布、通知数、退订数。
```

脚本内部完成：IMAP 轮询发件邮箱（reply_mailbox，与发送同源）-> 按
In-Reply-To/References 中已发 message_id（退化为发件人邮箱）归属 campaign ->
按 Message-ID 幂等去重 -> LLM 意图判定（计费走后台用量）-> 有意向站内信+渠道通知、
负面回复进退订名单、线索状态推进 replied。历史遗留 intent 为空的行会在每轮自动补判。

## 不要使用本技能

- 已有明确结构化线索来源的 CRM 录入 -> 用对应业务功能
- 营销群发（同一文案给大量既有订阅用户）-> 本技能是 1:1 个性化冷邮件外联
- 邮件代收代发个人事务 -> 平台邮箱通知体系
