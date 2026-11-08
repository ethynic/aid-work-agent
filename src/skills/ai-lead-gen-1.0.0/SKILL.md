---
name: ai-lead-gen
description: >
  AI 获客技能：B2B 目标客户开发与冷邮件外联全流程。从需求澄清（brief）开始，
  检索目标客户名单（公司+联系人+邮箱），入库筛选后逐一撰写针对性邮件，
  邮箱就绪后自动批量发送或经定时任务发送（SMTP），发送结果回写数据库，
  邮件正文内置打开追踪像素（收件方打开即产生打开记录，可回拉统计打开率），
  客户回复自动接收并判定意向、有意向时通知用户。
  适用于外贸开发、客户线索挖掘、cold outreach、lead generation、
  邮件营销获客等场景。
version: 1.2.0
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
   所有名单/联系人/邮件/回复数据存 PostgreSQL `bs_ai_lead_gen_*` 六表
   （campaigns / leads / contacts / emails / replies / opens，启动时自动建表），
   **不要**使用原包的 workspace CSV 落盘存业务数据。
2. **发件邮箱三层解析**（`mailbox_resolver.py`，发送脚本自动调用）：
   用户个人邮箱（邮箱设置）-> 租户邮箱兜底 -> 两者皆无时脚本退出并提示用户去配置。
   系统 .env 邮箱仅用于平台通知，**本技能禁止使用**。SMTP/IMAP 密码只在脚本内存中
   解密，不落盘、不打印、不进对话上下文。
3. **campaign 工作目录**：`/app/storage/tenants/{tenant}/lead-gen/campaign_{id}/`
   （brief.md、intel/、emails/、logs/、contact_targets.json）。退订黑名单在
   租户级 `/app/storage/tenants/{tenant}/lead-gen/unsubscribed.csv`，跨 campaign 共享。
   路径由脚本自动解析为绝对路径，**不要传相对路径**（skill_execute 的 CWD 是
   /tmp 临时目录，相对路径会写错位置）；`sender_profile.py` 不带 `--profile`
   即读写租户级 `lead-gen/sender_profile.json`。
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
5. **发送量由用户决定，技能不设数量下限、不做邮箱预热期分档**：campaign
   `daily_limit` 在 brief 确认时写入（不填 / 0 / 负数 = 不限量），发送脚本不因
   域名较新或累计发信量少而自动下调或拒发。**唯一硬红线**：同一域名 24h ≤ 3 封
   （防轰炸，不可关闭）。发送节奏（`delay_seconds`）与 `--max-minutes` 切段
   由用户在 config / 命令行决定。
6. **检索结果必须相关才可用**。搜索结果与查询无关（代理劫持特征）一律丢弃，
   宁可失败上报，也不污染名单。见 `references/retrieval-layer.md`。
7. **不编造邮箱**。官网抓不到就在 leads 里标 `reject_reason=no_email`，
   把通用邮箱伪装成负责人邮箱属于严重错误。但「抓不到」必须先排除
   **反混淆形态**（ROT13 / `data-enc-email` / Cloudflare / `[at]`）再判定：
   未经 `scripts/email_deobfuscate.py` 反混淆就标 `no_contact_page` / `no_email`
   属于误判（第 5/7 步必做，见「官网巡检必须支持邮箱反混淆」）。
8. **域名体检是独立方法，不是发送前置步骤**。`scripts/domain_check.py` 仅在
   用户明确下指令时调用（如「体检一下发件域名」「查一下 SPF/DKIM」）；
   主流程不自动执行、不因「未体检」阻塞发送，`domain_check.passed` 也不参与
   发件人档案（`sender_profile.py status`）的复用判定。体检报告可经
   `sender_profile.py record-domain-check` 按需存档，仅供回看。
   用独立子域发冷邮件仍是配置建议，见 `references/domain-warming.md`。
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
12. **禁止把自写脚本（write 工具产物）当程序执行**。批量数据操作（改状态、
   批量入库、导出）一律用 `lead_db.py` 既有子命令完成；确需临时脚本时写到
   `/tmp/` 下并用**绝对路径**执行，不要落租户 conversation 目录，也不要用
   相对路径引用存储文件（skill_execute 的 CWD 是临时目录，相对路径会读错
   位置且报错难排查——2026-11-06 实测该模式直接烧光 50 轮迭代上限）。

## 脚本清单（经 skill_execute 调用；仅 `lead_db.py` 与 `send_emails.py` 需要 --campaign-id，其余为账号级或单目标脚本）

| 脚本 | 用途 |
|------|------|
| `lead_db.py` | 业务数据读写（子命令见下表） |
| `send_emails.py` | SMTP 发送引擎（限额/退订/退信分类/随机延迟） |
| `mailbox_resolver.py` | 发件邮箱三层解析（单独运行可自检配置） |
| `sender_profile.py` | 发件身份档案（from_name/退订说明/通信地址）；账号级，子命令 status/save/reset/record-domain-check，**不接 --campaign-id**；域名体检结果仅为可选存档，不参与复用判定 |
| `domain_check.py` | SPF/DKIM/DMARC 域名体检（**独立方法，仅用户指令时调用**）；`--json` 模式正常完成恒 exit 0，是否达标看 `summary.fail` |
| `email_deobfuscate.py` | 邮箱反混淆（ROT13 `data-enc-email` / Cloudflare / `[at]` / HTML 实体），纯标准库，`--selftest` 自检；官网巡检抽取邮箱时必须启用（见下文反混淆硬约束） |
| `facts_builder.py` | 把批量官网报告压成每家 ≤300 字的紧凑事实表并按 25 家切块（纯规则，不调模型），200 家规模防上下文爆窗 |
| `email_verify.py` | 邮箱格式/MX 校验 |
| `email_rules.py` | 职能相关性判定（三道闸门共用口径） |
| `reply_monitor.py` | 回复监控：IMAP 轮询 -> 归属 -> 去重 -> LLM 意图判定 -> 意向通知/退订 |
| `tracking_key.py` | 打开追踪 key 生成（按发件邮箱自动生成 track_tenant.secret，持久化到租户目录 lead-gen/tracking_keys.json，发送/回拉共用；一般不单独调用） |
| `fetch_opens.py` | 打开记录回拉：从追踪端点按游标分页拉取 -> 按 message_id 匹配回写 emails 表 opens_at/open_count（幂等可重复跑） |

`lead_db.py` 子命令：

```bash
python lead_db.py init --name N --brief-file F --target-region R [--send-mode review|auto]
python lead_db.py set-target-config --campaign-id ID --json-file F   # contact_targets.json
python lead_db.py set-send-mode --campaign-id ID review|auto
python lead_db.py add-lead --campaign-id ID --json-file F              # 检索名单入库（自动去重）
python lead_db.py set-status --campaign-id ID --company C --status S [reason]
python lead_db.py set-status-batch --campaign-id ID --data F           # 批量改状态：JSON 数组 [{company,status,reason?}]（--data 不传从 stdin 读），单事务
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

### ⛔ 批量护栏（强制，优先级高于下文任何「重查 / 重发 / 降级」措辞）

> 我方的委托（web-search-pro `pipeline.py`）是**同步一次性命令**，stdout JSON
> 直接返回，不存在上游 `batch_delegate.py` 的跨轮停等问题；但重查与降级的
> **轮次上限照旧强制**，防止在低质结果上无限空转烧迭代：

1. **重查最多 2 轮**：结果质检不合格的 query 换词/换语言后合并成**一个**重查批次；
   连续 2 轮整批仍不合格（或 hits=0）即停止，把该批标 `search_failed` 并如实上报，
   继续下游或中止 campaign。**禁止发起第 3 个重查批次。**
2. **失败站只降级 1 次**：抓取失败 / 空壳站合并成**一个**降级批次（换引擎/补深层
   路径重抓一次），该批次仍失败即标 `blocked` / `needs_manual` 并如实上报，
   **不再发起新的降级批次**。
3. **重跑不算重查轮次**：pipeline 对已成功结果的去重由落盘文件与入库去重承担，
   同一批失败记录的机械重跑（未换判据）不算新轮次。
4. **每轮委托都要等结果回来再推进**：skill_execute 返回前不写结论、不推进下游；
   返回后只读落盘/返回的摘要字段，不把整份 JSON 灌进对话。

### ⚡ 效率纪律（优先级高于下文各步骤里的停等说明）

> **质量优先于效率（本节不可越过的边界）**：以下所有批量 / 压缩手段只改变
> **「怎么拿、怎么组织上下文」**，**不改变任何判据、产物字段与校验环节**。
> 铁律 1（不猜邮箱）、3（合规）、6（结果相关性）、7（不编造，含反混淆前提）
> 以及发送闸门（限额/黑名单/职能对口）、退订与限速**一条都不能省**。
> 域名体检（铁律 8）是独立方法，仅人工指令触发，不在此列、也不作为发送阻塞项。
> 「结果只落盘」不等于「不质检」：落盘后仍须逐份核对相关性，不合格的合并成
> **一个**重查批次（最多 2 轮，见「⛔ 批量护栏」）；`facts_builder.py` 的
> ≤300 字是**有损压缩**，写痛点时证据不足就按落盘文件定点回读原文，
> **严禁凭压缩摘要硬编情报**。

**七条硬规则**：

1. **一次委托覆盖整批**：检索/抓取 query 一律合并成一次 `pipeline.py` 调用
   （或一次委托多个并行 skill_execute），禁止逐家客户发任务块。
2. **结果只落盘、不回灌上下文**：批量报告留在 campaign 目录，对话里只保留
   汇总统计（total/ok/emails 数）；需要细节时定点读取单个文件。
3. **同一 URL 只抓一次**：官网正文与邮箱抽取来自同一份抓取结果（pipeline
   `--extract` 一次产出），禁止对同一官网重复发起抓取。
4. **检查点续跑**：每步结束把状态写进数据库（lead 状态 / contact 入库 /
   email 草稿）；中断后续跑只查库取当前状态，不重放全部历史。
5. **能规则判定就不调模型**：URL 一致性校验、邮箱形态分类、反混淆、打分、
   退订与限速一律交给脚本（`email_deobfuscate.py` / `email_rules.py` /
   `lead_db.py`）；模型只用于确有语义判断的环节（情报归纳、邮件撰写、意图判定）。
6. **上下文预算按块算**：大规模 campaign 严禁把全量 `site_text`、全量邮件草稿
   读进上下文；每个 turn 只加载「本块 facts + 本块已有产物 + 一份 style-card.md
   速查卡」。速查卡是**索引不是替身**：对模板/渲染规则有疑问时，定点打开
   对应 reference 的一小节（一次），不全文重读。
7. **静态资料只读一次、产物只生成一次**：`brief.md`、`email-copybook.md`、
   合规要点等与具体客户无关的资料，在**分块前一次性读完**并压成
   `style-card.md`（2-4KB，**必须完整保留**七段式结构、主题行硬规则
   ≤35 字符/无公司名/有具体数字、本地化要点、80-150 词长度、合规与退订要素；
   **不得为压体积而删规则**）落盘；第 7 步产出的公司情报同理——后续步骤只读、
   **不重写、不重新生成**；`facts_builder.py` 也只跑一次。

### 200 家企业专项（规模放大后新暴露的瓶颈）

> 批量纪律解决「停等次数」，但 200 家会暴露**上下文与输出容量**瓶颈：

| 新瓶颈 | 200 家时的量 | 后果 | 处理 |
|---|---|---|---|
| 官网正文 | 200 份 × 最多 6000 字 ≈ 120 万字符 | 读进上下文必爆窗 | `facts_builder.py` 压成每家 ≤300 字，按 25 家切块 |
| 情报 + 邮件输出 | 200 封 × 80-150 词 ≈ 3-6 万输出 token | 单次 turn 写不完 | 情报、邮件各按 25 家一块，各 8-10 turn |
| 状态改写 | 200 条 `set-status` = 200 次调用 | 200 次模型 turn | `lead_db.py set-status-batch` 一次改完全表 |
| 补查页面 | 几十家 × 3-5 页 | 数百次额外模型摘要 | URL 合并成一个列表一次性委托，不逐页 `web_fetch` |

**200 家的 turn 预算**：问答 3 + 定源 1 + 批量检索 2 + 批量抓取 1 + 事实表 0（脚本）
+ 情报 8-10 块 + 社媒 2-3 + 邮件 8-10 块 + 批量状态/发送 2 ≈ **25-30 次模型 turn**；
逐家停等写法约 **800-1200 次**。情报与邮件是**两类不同产物、各 8-10 块**，
不是同一批档案生成两遍。

**分块生成流程（大规模撰写必须按此执行）**：

```bash
# ① 从 brief.md 提取产品词/品类词写进 keywords.txt（1 个 turn）
# ② 纯脚本压缩 + 切块（不调模型）
python scripts/facts_builder.py --probe <campaign>/probe \
    --out <campaign>/facts.jsonl --keywords-file <campaign>/keywords.txt --chunk 25
# ③ 按 chunks.json 逐块，每块只读该块 facts 文件：
#    情报 → 生成该块公司情报（8-10 turn）
#    邮件 → 生成该块邮件草稿并 add-email-draft（8-10 turn），复用已有情报
#    同一份档案不生成两遍；块之间不重复加载同一批静态资料
```

`--probe` 指向的目录由批量抓取落盘产生：web-search-pro `pipeline.py --extract`
的单页结果汇总为 `{task:{company,url}, result:{site_text,emails}}` 或
顶层 `{company, site_text, emails}` 两种形态均可被 `facts_builder.py` 读取
（每份一个 JSON 文件 + 可选 `index.json`）。

**非模型的墙钟延迟（如实说明）**：抓取耗时取决于 pipeline 并发与礼貌间隔
（**不建议调小间隔**，并发下压低礼貌间隔会触发 429/风控）；发送墙钟时间只
取决于用户设定的 `delay_seconds` 与 `--max-minutes` 切段（默认 90-150 秒/封，
200 封 ≈ 5-8 小时），另有「每域 24h ≤ 3 封」防轰炸红线；数量、间隔均由用户决定
（铁律 5），技能不按预热期分档。

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
   不请求用户逐批确认。发件配置检查、dry-run、发件分组等技术细节
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

   > **⛔ 官网巡检必须支持邮箱反混淆（第 5/7 步必做）**：德国 / DACH 制造业官网
   > 普遍用 WordPress 插件把邮箱做 ROT13 后塞进 `data-enc-email`
   > （`vasb[ng]nsg-tebhc.qr` -> `info@aft-group.de`，连 `[at]` / `[dot]` 都可能被
   > 编码成 `[ng]` / `[qbg]`），页面里既无 `mailto:` 也无明文。只跑 mailto +
   > 纯文本正则会**整批判成「无邮箱」**（实测 200 家德国制造企业有 43 家如此）。
   > - **标准实现**：`scripts/email_deobfuscate.py`（纯标准库，自带 `--selftest`），
   >   批量抓取抽取邮箱时接入其 `extract_emails`；`web-search-pro` 管线仅做
   >   mailto + 明文抽取，**未接反混淆前不得把「管线没抽到」直接判为无邮箱**，
   >   需对命中页面跑一次 `email_deobfuscate.py` 复核。
   > - **覆盖**：ROT13 属性、Cloudflare `data-cfemail` / `email-protection#hex`、
   >   `[at] (at) &#64;` / `[dot]`、HTML 实体、JSON-LD `"email"`、常规 mailto 与明文；
   >   每个地址带 method + 来源页可回溯。反混淆是**还原**页面已给的信息，不是猜测。
   > - **深层路径**：德语站常在 `/footer/imprint`、`/de/impressum`、`/contact/...`
   >   等非标准路径放邮箱，路径发现不能只靠首页链接。
   > - **品牌域名闸门**：反混淆会顺带抓出经销商 / 海外分部邮箱，入库前只保留
   >   与目标官网同域 / 子域或「品牌前缀 + 通用 TLD」的地址。
   > - **判定纪律**：先反混淆 + 深层路径 + 变体重试，仍无结果才标 `no_email`，
   >   不要把可还原的地址提前判死。
6. **社媒兴趣分析**（可选）：用 `pipeline.py urls` 读公开商业内容，写入联系人 notes。
7. **撰写邮件**：先 `list-contacts` 取本 campaign 真实 contact ID 清单，再
   `add-email-draft` 逐联系人写针对性草稿（必写 subject_a/subject_b
   两版，各 ≤35 字符只差一个变量；公司简称放签名不进主题行），
   写完直接 `set-email-status` 置 approved——**不做邮件预览、不等用户确认**。
   contact_id 无效时报错会返回有效 ID 清单，直接取用，不要继续猜。
8. **发送准备**：`sender_profile.py status` 确认发件身份可复用（不自动跑域名
   体检——那是独立方法，见铁律 8）+ `mailbox_resolver.py` 确认邮箱配置就绪 +
   `export-send-queue` 生成队列。队列等技术结果只写 logs，不向用户展开。
9. **发送**：先 `send_emails.py --campaign-id ID --dry-run` 干跑复查
   （无 BLOCK 才可发），再 `--limit 3` 小批量试发，最后全量
   （可配 `--max-minutes` 分段）。对话内编排（review）与定时编排（auto）
   都直接推进，不逐批确认。发送结果自动回写 emails 表
   （sent 含 message_id / failed / bounced），硬退信自动进退订名单。
   发送默认开启打开追踪：正文尾部内嵌 1x1 像素（收件方客户端加载图片即记录
   一次打开；纯文本客户端不触发，属已知损耗）。发送完成或次日运行
   `fetch_opens.py --campaign-id ID` 回拉打开记录（自动回写 opens_at/open_count，
   幂等可重复跑），结合回复数据向用户汇报打开率。
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
4. auto 模式自动推进：export-send-queue -> send_emails.py
   （先 --dry-run 无 BLOCK，再 --limit 3 试发，最后全量，限额系统强制）
5. 运行 fetch_opens.py --campaign-id {ID} 回拉打开记录（opens_at/open_count 自动回写）
6. 收尾执行 daily-summary --campaign-id {ID} --notify 发送当日汇总通知
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
