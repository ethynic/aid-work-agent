---
name: b2b-email-sender
description: >
  B2B 冷邮件发送技能：只负责把 b2b-lead-research 产出的邮件草稿批量发送出去，
  并完成发送后核验（退信分类、打开回拉、钩子战绩回写、回复监控）。
  本技能不挖掘、不写话术；数据全部来自数据库当前 campaign。
  适用于邮件批量发送、发送结果核验、回复监控、定时循环发送等场景。
version: 1.0.0
requires_tools: []
metadata:
  openclaw:
    emoji: "📨"
    requires:
      bins: ["python"]
---

# B2B 邮件发送（b2b-email-sender）

## 用途与边界

**只发不挖**。本技能消费 `b2b-lead-research` 技能产出的 DB 数据
（campaign / 联系人 / approved 状态邮件草稿），完成：

- 发送编排（dry-run -> 试发 -> 全量，限额与退订名单系统强制）
- 发送后核验（退信分类回写、打开记录回拉、钩子战绩回写）
- 回复监控（IMAP 轮询 -> 意图判定 -> 意向通知 / 退订）
- 定时循环（auto 模式，复用系统定时任务）

**不要做**（那是挖掘技能的职责，发现缺失时提示用户先跑 b2b-lead-research）：

- 不重抓官网、不补联系人、不检索新名单
- 不撰写 / 改写邮件话术（草稿 status 不是 approved 时如实上报，不代写）
- 不新建 campaign（init 是挖掘侧第 1 步的职责）
- 不采集任何新的联系方式

## 数据与身份（本系统改造点，必读）

1. **身份与数据库**：脚本由 `skill_execute` 在执行上下文中运行，
   `AID_TENANT_ID` / `AID_USER_ID` 环境变量由系统注入（不可伪造、不进提示词）。
   全部业务数据存 PostgreSQL `bs_ai_lead_gen_*` 八表
   （campaigns / leads / contacts / contact_channels / emails / messages /
   replies / opens）。表结构由 `b2b-lead-research` 的 `init_tables()` 建
   （本技能 frontmatter **无 init_script**；SkillLoader 对所有已加载技能执行建表，
   两技能同仓库必然同时存在）。
2. **lead_db.py 是薄壳转发**：本技能目录下的 `lead_db.py` /
   `hook_library.py` 只把兄弟技能 `b2b-lead-research-1.0.0/scripts/` 的权威副本
   加载进来（模块导入 + CLI 直跑双语义）。schema / 子命令变更只改权威副本。
3. **发件邮箱三层解析**（`mailbox_resolver.py`，发送脚本自动调用）：
   用户个人邮箱（邮箱设置）-> 租户邮箱兜底 -> 两者皆无时脚本退出并提示用户去配置。
   系统 .env 邮箱仅用于平台通知，**本技能禁止使用**。SMTP/IMAP 密码只在脚本内存中
   解密，不落盘、不打印、不进对话上下文。
4. **campaign 工作目录**：`/app/storage/tenants/{tenant}/lead-gen/campaign_{id}/`
   （logs/、send_queue.json）。退订黑名单在租户级
   `/app/storage/tenants/{tenant}/lead-gen/unsubscribed.csv`，跨 campaign 共享。
   路径由脚本自动解析为绝对路径，**不要传相对路径**（skill_execute 的 CWD 是
   /tmp 临时目录，相对路径会写错位置）。
5. **对话内 / 定时同一条管线**，只差 campaign 的 `send_mode` 编排方式：
   - `review`（对话内编排）：在当前对话中完成发送编排，邮箱就绪后直接批量发送；
   - `auto`（定时编排）：复用系统定时任务（scheduler）按 cron 以用户身份执行
     同样的发送编排。两种模式均**不做邮件预览、不做逐批确认**，发送前闸门
     （限额 / 黑名单 / 职能对口）完全一致，技术细节只写 logs 不进对话。

## 铁律（违反会导致任务失败）

1. **发送量由用户决定，技能不设数量下限、不做邮箱预热期分档**：campaign
   `daily_limit` 在 brief 确认时写入（不填 / 0 / 负数 = 不限量），发送脚本不因
   域名较新或累计发信量少而自动下调或拒发。**唯一硬红线**：同一域名 24h ≤ 3 封
   （防轰炸，不可关闭；`per_domain_daily_limit` 可调）。发送节奏
   （`delay_seconds`）与 `--max-minutes` 切段由用户在 config / 命令行决定。
2. **不判断邮箱/域名「可用性」来拦截发送**：域名校验失败、邮箱较新等都不构成
   拒发理由；闸门只有四个——限额、退订名单、职能对口（email_rules）、
   草稿状态。域名体检（domain_check.py）是**独立方法**，仅在用户明确下指令时
   调用（如「体检一下发件域名」「查一下 SPF/DKIM」），主流程不自动执行、
   不因「未体检」阻塞发送。
3. **每个联系人只发一封**，且首封必须已含「如何拒绝」（挖掘侧写入草稿，
   发送前消费校验时抽查确认）。群发同一文案给同一公司多个联系人是明确禁止的
   （每域 24h ≤ 3 封红线兜底）。
4. **合规要素齐备才可全量发送**：见 `references/email-compliance.md`——
   主题行不误导（禁 `Re:`/`Fwd:`）、退订机制可用、物理寄件地址已配置
   （美国/加拿大必需）、EU/EEA 追踪声明（发送脚本自动追加）。
5. **数据库读写必须走 `lead_db.py`，禁止 `python -c` 内联 SQL 直连数据库**。
   脚本执行报错时如实上报，由下一轮修正命令重试。
6. **禁止猜测任何数据库 ID**（campaign_id / email_id）。ID 只能来自本会话内
   命令的真实输出或 `list-contacts` / `show-campaign` 查询结果。拿不到就先查，
   报错信息里返回的有效 ID 清单直接取用，**不得编造连续编号去试**。
7. **禁止把自写脚本（write 工具产物）当程序执行**；确需临时脚本写到 `/tmp/`
   并用**绝对路径**执行，不要落租户目录。
8. **退信 / 限流分类按脚本既有口径，不人工改判**：硬退信（550 5.1.1 等）
   自动进退订名单；5.7.1 黑名单整批停发；`MI:STC` 限流**绝不**进退订名单。
   详见 `references/post-send-verification.md`。

## 脚本清单（经 skill_execute 调用；`lead_db.py` 与 `send_emails.py` 需要 --campaign-id）

| 脚本 | 用途 |
|------|------|
| `lead_db.py` | 业务数据读写（薄壳 -> 权威副本；发送侧常用 list-contacts / set-email-status / export-send-queue / bind-scheduler / unbind-scheduler / daily-summary / hook-stats） |
| `send_emails.py` | SMTP 发送引擎（限额/退订/退信分类/随机延迟/打开追踪像素） |
| `mailbox_resolver.py` | 发件邮箱三层解析（单独运行可自检配置） |
| `sender_profile.py` | 发件身份档案（from_name/退订说明/通信地址）；账号级，**不接 --campaign-id** |
| `tracking_key.py` | 打开追踪 key 生成（发送/回拉共用；一般不单独调用） |
| `fetch_opens.py` | 打开记录回拉（按 message_id 回写 opens_at/open_count，幂等） |
| `reply_monitor.py` | 回复监控：IMAP 轮询 -> 归属 -> 去重 -> LLM 意图判定 -> 意向通知/退订 |
| `domain_check.py` | SPF/DKIM/DMARC 体检（**仅用户指令时调用**；`--json` 恒 exit 0，看 `summary.fail`） |
| `email_rules.py` | 职能对口判定（发送闸门口径，与挖掘侧副本 sha256 一致） |
| `hook_library.py` | 钩子战绩回写（薄壳 -> 权威副本；发送后 `record` 用） |

## 执行流程（6 步）

### 第 1 步 · 消费校验

确认数据齐备才继续，缺什么提示用户先跑 b2b-lead-research：

```bash
python lead_db.py show-campaign --campaign-id ID
python lead_db.py list-contacts --campaign-id ID
```

核对：campaign 存在且 brief / 目标配置齐备；有 approved 状态的邮件草稿
（草稿不足 / 全是 draft 状态 -> 上报，不代写）；联系人可发状态正常。
**不核对新名单、不补联系人**。

### 第 2 步 · 发件配置

`mailbox_resolver.py` 自检三层邮箱配置。用户已提供发件配置时**不重复问**；
两者皆无时脚本 fail-fast，引导用户 / 管理员先去配置邮箱（这是刻意设计，
不要绕过）。发件身份（from_name / 退订说明 / 通信地址）经
`sender_profile.py status` 确认可复用，未配置时引导用户补齐后
`sender_profile.py save` 存档。

### 第 3 步 · 合规要素确认

按 `references/email-compliance.md` 第三节检查清单逐项确认（目标地区法规、
退订机制、物理地址、测试发送），检查结论写入 logs 不进对话。

### 第 4 步 · 队列

```bash
python lead_db.py export-send-queue --campaign-id ID
```

生成 `logs/send_queue.json`。队列等技术结果只写 logs，不向用户展开。

### 第 5 步 · 发送

```bash
python send_emails.py --campaign-id ID --dry-run     # 干跑复查，无 BLOCK 才可发
python send_emails.py --campaign-id ID --limit 3     # 先 3 封试发给自己确认送达
python send_emails.py --campaign-id ID               # 全量；可配 --max-minutes 切段
```

- 干跑有 BLOCK -> 按条目修正后重跑干跑，**不跳过闸门强行发送**。
- 对话内编排（review）与定时编排（auto）都直接推进，不逐批确认。
- 发送结果自动回写 emails 表（sent 含 message_id / failed / bounced），
  硬退信自动进退订名单；默认开启打开追踪像素（纯文本客户端不触发，已知损耗）。

### 第 6 步 · 发送后核验

完整口径见 `references/post-send-verification.md`，顺序固定：

```bash
# ① 打开记录回拉（幂等）
python fetch_opens.py --campaign-id ID
# ② 钩子战绩聚合
python lead_db.py hook-stats --campaign-id ID > /tmp/hook_stats.json
# ③ 回写钩子库（--business 用挖掘侧登记的分区 key，--market 用 brief 目标国家码）
python hook_library.py record --business <key> --market <码> --stats-file /tmp/hook_stats.json
```

逐封状态判定（bounced 硬退信 -> 退订名单；5.7.1 黑名单整批停发；
`MI:STC` 限流绝不退订；failed 会话污染重连）全部按脚本既有分类，
不人工改判。回复监控日常由定时任务跑（见下节）；对话中被问
「有人回复了吗」时可直接执行 `reply_monitor.py --campaign-id ID` 实时拉取。

## 定时任务与回复监控（auto 模式）

### 每日循环任务（auto 发送编排）

用户在系统「定时任务」中创建每日任务（建议上午执行一次），task_prompt 使用固定模板：

```
委派给 <子智能体名> 数字员工执行获客活动 campaign_id={ID} 的每日循环：
1. （如需补充名单，由 b2b-lead-research 技能负责检索与草稿撰写）
2. auto 模式自动推进：export-send-queue -> send_emails.py
   （先 --dry-run 无 BLOCK，再 --limit 3 试发，最后全量，限额系统强制）
3. 运行 fetch_opens.py --campaign-id {ID} 回拉打开记录（opens_at/open_count 自动回写）
4. 发送后核验：lead_db.py hook-stats --campaign-id {ID} 聚合，
   hook_library.py record --business <key> --market <码> 回写钩子战绩
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
委派给 <子智能体名> 数字员工执行获客活动 campaign_id={ID} 的回复监控：
运行 reply_monitor.py --campaign-id {ID}（默认 limit 20 / 回看 14 天），
按 stdout JSON 汇报：检查数、去重数、新回复数、意向分布、通知数、退订数。
```

脚本内部完成：IMAP 轮询发件邮箱（reply_mailbox，与发送同源）-> 按
In-Reply-To/References 中已发 message_id（退化为发件人邮箱）归属 campaign ->
按 Message-ID 幂等去重 -> LLM 意图判定（计费走后台用量）-> 有意向站内信+渠道通知、
负面回复进退订名单、线索状态推进 replied。历史遗留 intent 为空的行会在每轮自动补判。

## 用户沟通口径（发送汇报）

与用户的对话输出遵守以下口径，避免内部变量 dump 和技术细节外泄：

1. **不展示邮件预览**。邮件是批量发送（单批可达上百封），不做逐封预览、
   不展示正文、不解释 A/B 主题概念——A/B 两版主题仅内部交替分组，
   用于发送后效果统计。收件人入库、草稿撰写（挖掘侧）完成后直接推进发送，
   不请求用户逐批确认。发件配置检查、dry-run、发件分组等技术细节
   只写 `logs/` 供事后排查，不进对话。

2. **发送后大白话汇报**，固定顺序：
   - 发给谁：本次发送 N 封（覆盖哪些行业，代表公司举 2-3 例）
   - 结果：成功 / 失败 / 退信各多少，退信率高时给一句建议
   - 主题效果：两版主题各发一半，哪版暂回复更好（样本少时注明「暂不足定论」）
   - 下一步：回复监控自动运行中，有客户回复会立即通知你

3. **每轮结尾给下一步预期**：明确一句「你回复 X 之后我会做 Y」，
   动作词要短、可直接执行，不要让用户写自然语言长句。

## 不要使用本技能

- 还没有可发送的 approved 草稿、需要先挖名单 / 挖联系人 / 写话术
  -> 用 `b2b-lead-research` 技能（挖掘与发送是两个技能，先挖后发）
- 已有明确结构化线索来源的 CRM 录入 -> 用对应业务功能
- 营销群发（同一文案给大量既有订阅用户）-> 本技能是 1:1 个性化冷邮件外联
- 邮件代收代发个人事务 -> 平台邮箱通知体系
