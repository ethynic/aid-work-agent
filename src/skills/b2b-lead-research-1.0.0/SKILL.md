---
name: b2b-lead-research
description: >
  B2B 客户挖掘技能：目标客户检索、官网巡检、企业业务综述、联系人多渠道
  联系方式采集、话术素材与分渠道成品产出。只挖不发——不碰 SMTP、不做投递、
  不写 emails 发送状态。产出终点是数据库内的企业 + 联系人 + 多渠道坐标 +
  综述 + 话术成品，即使没有找到邮箱也可独立交付。
  适用于外贸开发、客户线索挖掘、lead generation、市场摸底等场景。
version: 1.0.0
init_script: lead_db.py
requires_tools: [web_search, web_fetch]
metadata:
  openclaw:
    emoji: "🎯"
    requires:
      bins: ["python"]
---

# B2B 客户挖掘（b2b-lead-research）

## 用途与边界

把一段公司介绍 / 产品介绍 / 需求描述，转化为数据库内的目标客户名单、
联系人清单（含邮箱 / 电话 / 社媒 / IM 多渠道坐标）、企业业务综述和
分渠道成品话术。

**只挖不发**：本技能不碰 SMTP、不调用任何发送脚本、不写 `emails` 表发送状态、
不做发送后核验。发送由 `b2b-email-sender` 技能承担——**无邮箱不阻塞交付**：
即使一家企业没有可用邮箱，综述与多渠道坐标本身已是可交付产物。

本技能是**编排层**：决定搜什么、爬哪个 URL、按什么口径判、写什么话术。
实际检索与爬取按**降级链**委托执行：**默认直接用 `web-search-pro` 技能的
`pipeline.py --extract` 管线**（检索+抓页+邮箱/电话抽取一站式，零 LLM 消耗）；
平台 `web_search` / `web_fetch` 工具仅在管线全通道失败时兜底（见下文委托协议）。

## 数据与身份（与上游包差异必读）

1. **身份与数据库**：脚本由 `skill_execute` 在执行上下文中运行，
   `AID_TENANT_ID` / `AID_USER_ID` 环境变量由系统注入（不可伪造、不进提示词）。
   所有业务数据存 PostgreSQL `bs_ai_lead_gen_*` 八表，启动时自动建表：

   | 表 | 内容 | 本技能重点字段 |
   |---|---|---|
   | campaigns | 获客活动 | brief、send_mode（发送侧消费） |
   | leads | 企业 | `summary`（一句话业务综述）、`profile_md`（详细综述） |
   | contacts | 联系人 | email / phone / linkedin |
   | contact_channels | 多渠道坐标 | channel / value / source / source_url / confidence / evidence |
   | messages | 非 email 渠道成品话术 | channel / content / language / cta |
   | emails | 邮件草稿与发送状态 | `hook_id`（钩子战绩回写锚点）、status=draft |
   | replies / opens | 回复与打开（发送侧写入） | 本技能只读汇报 |

   **不要**使用上游包的 workspace CSV / Outreach Pack 文件落盘存业务数据。
   钩子库例外：`hook_library.py` 存租户级 JSON
   `storage/tenants/{tenant}/lead-gen/hook_library/`（跨 campaign 复用）。
2. **campaign 工作目录**：`/app/storage/tenants/{tenant}/lead-gen/campaign_{id}/`
   （brief.md、intel/、probe/、emails/、logs/、contact_targets.json）。
   路径由脚本自动解析为绝对路径，**不要传相对路径**（skill_execute 的 CWD 是
   /tmp 临时目录，相对路径会写错位置）。
3. **对 `b2b-email-sender` 的交付契约**：发送侧直接消费本库当前 campaign 的
   草稿（emails.status=draft）与发件配置；email 话术唯一落点是 `emails` 表
   （带 hook_id），`messages` 表拒绝 channel='email'。

## 铁律（违反会导致任务失败）

1. **不猜测邮箱**。邮箱必须来自官网页面、邮箱验证工具或公司公开文档；
   靠名字+域名拼出的邮箱一律标 `guessed`（confidence=low），入库闸门会直接拒收。
2. **检索结果必须相关才可用**。搜索结果与查询无关（代理劫持特征）一律丢弃，
   宁可失败上报，也不污染名单。见 `references/retrieval-layer.md`。
3. **不采集敏感个人信息**（种族、宗教、政治倾向、健康、金融账户、工会成员身份）。
   社媒只分析公开的商业相关内容。个人手机号默认不采集；租户扩展提示词明确要求时
   可搜索并仅存联系人表 phone 字段，不得批量导出或在对外内容中输出。
4. **不编造邮箱**。官网抓不到就在 leads 里标 `reject_reason=no_email`，
   把通用邮箱伪装成负责人邮箱属于严重错误。但「抓不到」必须先排除
   **反混淆形态**（ROT13 / `data-enc-email` / Cloudflare / `[at]`）再判定：
   未经 `scripts/email_deobfuscate.py` 反混淆就标 `no_contact_page` / `no_email`
   属于误判（见「官网巡检必须支持邮箱反混淆」）。
5. **邮箱必须对口，不能只是"能发"**。招聘（hr@/jobs@）、财务、法务、公关邮箱与
   公开窗口（info@/contact@）**都不得进入发送队列**。判定由 `scripts/email_rules.py`
   统一负责，采集/入库/发送三道闸门强制执行。官网只有这些地址时不算"没有邮箱"，
   按 `references/contact-discovery.md` 路径 2→5 继续找，全部无果才判 `no_email`。
6. **数据库读写必须走 `lead_db.py`，禁止用 `python -c` 内联 SQL 直连数据库**。
   内联 SQL 绕过去重、闸门与配额校验（每公司联系人上限、职能判定），且不受
   可信身份校验保护。脚本执行报错时如实上报，由下一轮修正命令重试。
7. **禁止猜测任何数据库 ID**（campaign_id / contact_id / email_id / lead_id）。
   ID 只能来自本会话内命令的真实输出（init / add-contact / add-email-draft 的
   stdout）或 `list-contacts` / `list-leads` 的查询结果。拿不到 ID 就先跑
   `list-contacts` 查，报错信息里返回的有效 ID 清单直接取用，**不得编造连续
   编号去试**——历史实测盲猜 ID 连续 10 次 not found 空转烧掉大量迭代。
8. **禁止把自写脚本（write 工具产物）当程序执行**。批量数据操作（改状态、
   批量入库、导出）一律用 `lead_db.py` 既有子命令完成；确需临时脚本时写到
   `/tmp/` 下并用**绝对路径**执行，不要落租户 conversation 目录，也不要用
   相对路径引用存储文件。
9. **多渠道坐标只记录公开商务信息**。社媒/IM 坐标必须是官网、名录、公开主页上
   明示的企业商务入口；不推断、不拼装、不把私人账号当商务坐标。采集口径与
   置信度判定见 `references/multi-channel-discovery.md`。
10. **社媒 / IM / 手机类坐标本轮只记录不触达**。sms/call/whatsapp/telegram/
    wechat/line/kakao/linkedin/x/facebook/instagram 仅入库供后续人工或后续
    技能消费；本轮不做任何私信、连接请求、电话等触达动作（EU/EEA/UK/CA 市场
    一律如此，其他市场同样默认不触达）。

## 脚本清单（经 skill_execute 调用）

| 脚本 | 用途 |
|------|------|
| `lead_db.py` | 业务数据读写（权威副本；子命令见下表） |
| `hook_library.py` | 强钩子库：按业务+市场分区沉淀钩子实例与战绩（register/add/add-market/record/recommend/list/seed）；存储在租户级 JSON |
| `email_deobfuscate.py` | 邮箱反混淆（ROT13 `data-enc-email` / Cloudflare / `[at]` / HTML 实体），纯标准库，`--selftest` 自检；官网巡检抽取邮箱时必须启用 |
| `facts_builder.py` | 把批量官网报告压成每家 ≤300 字的紧凑事实表并按 25 家切块（纯规则，不调模型），200 家规模防上下文爆窗 |
| `email_verify.py` | 邮箱格式/MX 校验 |
| `email_rules.py` | 职能相关性判定（三道闸门共用口径；与 b2b-email-sender 副本 sha256 一致，由单测保证） |

`lead_db.py` 挖掘侧常用子命令：

```bash
python lead_db.py init --name N --brief-file F --target-region R [--send-mode review|auto]
python lead_db.py set-target-config --campaign-id ID --json-file F   # contact_targets.json
python lead_db.py add-lead --campaign-id ID --json-file F              # 检索名单入库（自动去重，支持 summary）
python lead_db.py set-lead-profile --campaign-id ID --company C --summary S --profile-file F
python lead_db.py set-status --campaign-id ID --company C --status S [reason]
python lead_db.py set-status-batch --campaign-id ID --data F           # 批量改状态：JSON 数组（--data 不传从 stdin 读），单事务
python lead_db.py add-contact --campaign-id ID --json-file F           # 联系人入库（闸门强制）
python lead_db.py add-channel --campaign-id ID --json-file F           # 多渠道坐标入库（枚举/E.164/@handle 校验 + 去重）
python lead_db.py list-channels --campaign-id ID [--contact-id CID]
python lead_db.py add-email-draft --campaign-id ID --contact-id CID \
    --subject-a A --subject-b B --body-file F [--hook-id PATTERN]      # email 成品（带钩子标注）
python lead_db.py add-message --campaign-id ID --json-file F           # 非 email 渠道成品（channel='email' 拒收）
python lead_db.py list-messages --campaign-id ID
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
> 铁律 1（不猜邮箱）、3（合规敏感信息）、6（结果相关性）、7（不编造，含反混淆前提）
> 以及入库闸门**一条都不能省**。
> 「结果只落盘」不等于「不质检」：落盘后仍须逐份核对相关性，不合格的合并成
> **一个**重查批次（最多 2 轮，见「⛔ 批量护栏」）；`facts_builder.py` 的
> ≤300 字是**有损压缩**，写综述/痛点时证据不足就按落盘文件定点回读原文，
> **严禁凭压缩摘要硬编情报**。

**七条硬规则**：

1. **一次委托覆盖整批**：检索/抓取 query 一律合并成一次 `pipeline.py` 调用
   （或一次委托多个并行 skill_execute），禁止逐家客户发任务块。
2. **结果只落盘、不回灌上下文**：批量报告留在 campaign 目录，对话里只保留
   汇总统计（total/ok/emails 数）；需要细节时定点读取单个文件。
3. **同一 URL 只抓一次**：官网正文与邮箱抽取来自同一份抓取结果（pipeline
   `--extract` 一次产出），禁止对同一官网重复发起抓取。
4. **检查点续跑**：每步结束把状态写进数据库（lead 状态 / contact 入库 /
   channel 入库 / profile 写入 / email 草稿）；中断后续跑只查库取当前状态，
   不重放全部历史。
5. **能规则判定就不调模型**：URL 一致性校验、邮箱形态分类、反混淆、打分、
   坐标校验一律交给脚本（`email_deobfuscate.py` / `email_rules.py` /
   `lead_db.py`）；模型只用于确有语义判断的环节（综述归纳、话术撰写）。
6. **上下文预算按块算**：大规模 campaign 严禁把全量 `site_text`、全量话术
   读进上下文；每个 turn 只加载「本块 facts + 本块已有产物 + 一份 style-card.md
   速查卡」。速查卡是**索引不是替身**：对模板/渲染规则有疑问时，定点打开
   对应 reference 的一小节（一次），不全文重读。
7. **静态资料只读一次、产物只生成一次**：`brief.md`、`email-copybook.md`、
   合规要点等与具体客户无关的资料，在**分块前一次性读完**并压成
   `style-card.md`（2-4KB，**必须完整保留**七段式结构、主题行硬规则
   ≤35 字符/无公司名/有具体数字、本地化要点、80-150 词长度、合规与退订要素；
   **不得为压体积而删规则**）落盘；巡检产出的公司情报同理——后续步骤只读、
   **不重写、不重新生成**；`facts_builder.py` 也只跑一次。

### 200 家企业专项（规模放大后新暴露的瓶颈）

> 批量纪律解决「停等次数」，但 200 家会暴露**上下文与输出容量**瓶颈：

| 新瓶颈 | 200 家时的量 | 后果 | 处理 |
|---|---|---|---|
| 官网正文 | 200 份 × 最多 6000 字 ≈ 120 万字符 | 读进上下文必爆窗 | `facts_builder.py` 压成每家 ≤300 字，按 25 家切块 |
| 综述 + 话术输出 | 200 家 × (综述+成品) ≈ 3-6 万输出 token | 单次 turn 写不完 | 综述、话术各按 25 家一块，各 8-10 turn |
| 状态改写 | 200 条 `set-status` = 200 次调用 | 200 次模型 turn | `lead_db.py set-status-batch` 一次改完全表 |
| 补查页面 | 几十家 × 3-5 页 | 数百次额外模型摘要 | URL 合并成一个列表一次性委托，不逐页 `web_fetch` |

**200 家的 turn 预算**：问答 3 + 定源 1 + 批量检索 2 + 批量抓取 1 + 事实表 0（脚本）
+ 综述 8-10 块 + 社媒 2-3 + 话术 8-10 块 + 批量状态 1 ≈ **25-30 次模型 turn**；
逐家停等写法约 **800-1200 次**。综述与话术是**两类不同产物、各 8-10 块**，
不是同一批档案生成两遍。

**分块生成流程（大规模撰写必须按此执行）**：

```bash
# ① 从 brief.md 提取产品词/品类词写进 keywords.txt（1 个 turn）
# ② 纯脚本压缩 + 切块（不调模型）
python scripts/facts_builder.py --probe <campaign>/probe \
    --out <campaign>/facts.jsonl --keywords-file <campaign>/keywords.txt --chunk 25
# ③ 按 chunks.json 逐块，每块只读该块 facts 文件：
#    综述 → set-lead-profile 写入该块综述（8-10 turn）
#    话术 → 生成该块分渠道成品并 add-email-draft / add-message（8-10 turn），复用已有综述
#    同一份档案不生成两遍；块之间不重复加载同一批静态资料
```

`--probe` 指向的目录由批量抓取落盘产生：web-search-pro `pipeline.py --extract`
的单页结果汇总为 `{task:{company,url}, result:{site_text,emails}}` 或
顶层 `{company, site_text, emails}` 两种形态均可被 `facts_builder.py` 读取
（每份一个 JSON 文件 + 可选 `index.json`）。

**非模型的墙钟延迟（如实说明）**：抓取耗时取决于 pipeline 并发与礼貌间隔
（**不建议调小间隔**，并发下压低礼貌间隔会触发 429/风控）。检索层能优化的只有
「模型 turn 数」与「上下文体积」。

## 全流程 8 步

1. **需求澄清（brief）**：问卷只**必问两项**——目标国家/地区、目标行业；
   企业规模、决策人职位等其余字段默认「不限」并按上下文合理默认填写，
   不发起连环追问（用户主动描述了规模/决策人/CTA 等信息时照常落 brief）。
   回显格式与确认方式见「用户沟通口径」。产出 brief.md 后
   `lead_db.py init` 建 campaign 并存 brief。
   `init` 会**前置校验发件邮箱**（个人邮箱或租户邮箱任一已配置）：未配置时拒绝创建
   并提示先去配置——这是刻意的 fail-fast，不要绕过，先引导用户/管理员配好邮箱再重跑
   （发送侧 `b2b-email-sender` 依赖同一配置）。
   同时 `hook_library.py register --key <business_key> ...` 登记业务分区
   （同一业务的所有 campaign 必须用同一个 key，战绩才会累积；见
   `references/hook-library.md`）。
2. **目标配置**：`set-target-config` 写入 contact_targets.json（目标职能/排除职能/产品一句话），
   三道闸门共用此口径。
3. **客户名单检索**：用 web-search-pro `pipeline.py search "<关键词>" --extract --json`
   （按目标国家/地区选当地引擎与行业名录关键词，挖名单加 `--companies-only`；
   战术模板见 `references/lead-search-playbook.md`，引擎选择见
   `references/search-engine-map.md`），
   结果过相关性判定后 `add-lead` 入库（自动按公司名/官网去重，可带 summary）。
4. **官网定位与筛选**：用 `pipeline.py search "公司名"` 或 `urls` 模式核验官网有效性，
   打分（score）并 `set-status` 标 qualified/rejected（带 reject_reason）。
5. **官网巡检 + 企业业务综述**：对 qualified 名单用
   `pipeline.py urls "官网首页,contact页,about页" --extract --json` 巡检，
   同一次抓取同时产出：正文（写综述用）、邮箱（反混淆，见下）、电话、社媒外链。
   综述分两级写入：一句话业务综述 + 详细综述（主营业务 / 痛点+证据 URL /
   钩子线索 / 决策链线索），落 `set-lead-profile`
   （`--summary` 一句话 + `--profile-file` 详细 Markdown）。

   > **⛔ 官网巡检必须支持邮箱反混淆（本步必做）**：德国 / DACH 制造业官网
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
6. **联系人 + 多渠道坐标**：从巡检结果抽联系人，`add-contact` 入库
   （闸门自动拒收：无关职能/公开窗口/guessed 低置信）；同一份抓取结果里的
   电话（E.164）/ 社媒 / IM 坐标用 `add-channel` 入库（渠道枚举校验 + 去重；
   source/source_url/confidence/evidence 必填可回溯）。采集口径、渠道偏好与
   置信度判定见 `references/multi-channel-discovery.md`。**没有可用邮箱的企业
   照常交付坐标与综述，不阻塞**。
7. **话术素材 + 分渠道成品**：先 `hook_library.py recommend --business <key>
   --market <码>` 查钩子库（战绩排名 + 可复用实例 + 结构骨架，见
   `references/hook-library.md`），再按 `references/talk-track-playbook.md`
   写分渠道成品：email 渠道走 `add-email-draft --hook-id <pattern>`（主题两版
   ≤35 字符、正文 80-150 词、合规要素齐备）；其他渠道走 `add-message`
   （channel 枚举内；channel='email' 会被拒收）。钩子无事实支撑就不写。
8. **交付（独立终点）**：大白话汇报——企业数 / 联系人数 / 各渠道 ready 数 /
   综述覆盖数，无邮箱企业的处置（坐标仍可用）。提示一句：
   「需要发送邮件时说一声，由发送技能（b2b-email-sender）接着做」。
   **挖掘到此结束**：不做发送准备、不碰 SMTP、不等用户确认发送。

## 用户沟通口径（问卷回显 / 交付汇报）

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

2. **交付大白话汇报**，固定顺序：
   - 找到多少家： qualified / rejected / 待核各多少，代表企业举 2-3 例
   - 联系人与渠道：联系人总数，邮箱就绪数 / 电话数 / 社媒数（一句话带过）
   - 业务综述：多少家已写综述，2-3 家举一句钩子亮点
   - 下一步：「需要发送邮件时说一声，我交给你——由发送技能接着做」
3. **每轮结尾给下一步预期**：明确一句「你回复 X 之后我会做 Y」，
   动作词要短、可直接执行，不要让用户写自然语言长句。

## 不要使用本技能

- 已有明确结构化线索来源的 CRM 录入 → 用对应业务功能
- 纯发送请求（名单已就绪、只要发邮件）→ 用 `b2b-email-sender` 技能
- 营销群发（同一文案给大量既有订阅用户）→ 本技能产出的是 1:1 个性化外联素材
- 邮件代收代发个人事务 → 平台邮箱通知体系
