# 联系话术素材与分渠道渲染规范

**核心思想：话术只写一次，按渠道渲染。** `b2b-lead-research` 产出「渠道中立素材」
与「分渠道成品」；后续发送 / 短信 / 电话 / 社媒触达**直接消费成品，不重新写话术**。

我方落点（DB 管线口径）：

- **email 渠道成品** -> `lead_db.py add-email-draft`（`bs_ai_lead_gen_emails` 表，
  带 `--hook-id`，状态 draft；发送侧直接消费）
- **其他渠道成品**（sms/call/whatsapp/telegram/wechat/line/kakao/linkedin/x/
  facebook/instagram）-> `lead_db.py add-message`（`bs_ai_lead_gen_messages` 表；
  channel='email' 会被拒收——email 话术唯一落点是 emails 表）
- 渠道中立素材（八段结构，见 §1）随企业综述写入
  `set-lead-profile` 的 `profile_md`（痛点/证据/钩子段），供人工与后续轮次复用

## 1. 渠道中立素材（每家公司一份，八段固定结构）

**每条事实必须能回溯到 probe 落盘证据与 URL**，证据不足就写「证据不足」，不许脑补。

```markdown
# <公司名> · 联系话术素材
- 目标联系人 / 职位 / 语言：
- 我方一句话价值主张：
- 对方业务综述（1-2 句，来自 lead profile）：

## 痛点（最多 3 条，每条附官网原文证据 + URL）
1. ...

## 我方证据（1 个可信凭据：案例 / 认证 / 数据 / 产能）
- ...

## 钩子（具体事实 + 代价，1 句；无社媒/官网事实就写「证据不足，不写钩子」）
- hook_text:
- hook_id: cost | peer | number | question | pain（自定义模式也行，战绩按它累积）

## 异议预判（3 条 + 简短应答）
- 已有供应商 → ...
- 没预算 / 没时间 → ...
- 需要认证 / 样品 → ...

## CTA（低承诺动作，按 brief 的第一目标）
- 主 CTA:
- 备选 CTA:

## 禁语与本地化注意
- ...
```

**钩子优先级**（先用 `hook_library.py recommend --business <key> --market <码>`）：
1. 该市场实测 · 已是目标语言 -> 直接用，只替换收件人事实
2. 种子实例 · 已是目标语言 -> 参考或改写
3. 跨语言机制参考 -> **只看心理机制，按渲染规则重写**，不逐字翻译

主题行用 `assets/subject_templates.json` 的语言中立结构骨架现渲染：
≤35 字符、不含公司简称、必须有具体数字、无全大写感叹号、无垃圾词。

## 2. 渲染成各渠道成品

| 渠道 | `subject` | `content` 长度 | 结构 |
|---|---|---|---|
| `email`（add-email-draft） | 必填 | 80-150 词 | 钩子 → 相关性 → 证据 → CTA → 签名 → 合规要素 |
| `sms` | 留空 | ≤160 字符（含退订提示） | 自报家门 + 一句具体价值 + 低承诺 CTA + `STOP` 退订 |
| `call` | 留空 | 30 秒开场白 ≈ 70-90 词 + 3 条要点 | 自报家门 → 一句相关性 → 请求 30 秒 → 要点 → 异议应答 → 结束动作 |
| `whatsapp` / `wechat` / `line` / `kakao` / `telegram` | 留空 | ≤300 字符，可分 2 条 | 一句价值 + 一个可回复的问题；不连发、不带附件轰炸 |
| `linkedin` / `x` / `facebook` / `instagram` | 留空 | 连接请求 ≤200 字符；公开互动 ≤280 字符 | 不推销，先给具体共同点；**连接请求里不放链接** |

**所有渠道共用的四条硬规则**：

1. **一封/一条只讲一件事**，一个 CTA，不叠加多个诉求。
2. **公司简称只出现在签名/自报家门里**，不进邮件主题、不进短信开头、不进连接请求标题。
3. **本地化不是翻译**：德语 Sie、日语敬体、韩语敬语、越南语 kính 语体、阿拉伯语 RTL；
   用当地同行会用的表达，避免翻译腔。语言码写进 `add-message --language`。
4. **钩子必须可回溯**：email 草稿带 `--hook-id`（emails.hook_id，战绩回写锚点），
   钩子原句写进正文；无事实就不写钩子，绝不靠搜索 snippet 脑补。

## 3. 邮件专属合规要素

- 正文要有明确「如何拒绝」（回复 unsubscribe 或退订链接）
- 真实寄件地址由 `b2b-email-sender` 从发件配置追加
- EU/EEA 收件人的追踪声明由发送脚本自动追加

挖掘技能只标**需要具备什么**，不负责拼接退订链接与寄件地址（那是发送技能的配置项）。

## 4. 电话 / 社媒渠道的额外素材

`add-message` 的 `content` 之外，可在 `notes` 里结构化补充（后续技能/人工直接读）：

```
talking_points: 要点1；要点2；要点3
objection_answers: 已有供应商->...；没时间->...
follow_up: day2 email ...；day5 call ...
call_window: Europe/Berlin 09:00-11:30,14:00-16:30
social_step: connect_request no_link=true after_accept=...
```

后续跨渠道序列（先邮件 -> 后电话 -> 再社媒）按这些标注执行，不用回挖数据。

## 5. 禁语

- 空泛词：「商业合作」「互利共赢」「优质服务」「best price」「Dear Sir/Madam」批量套用
- 虚假紧迫：「今日截止」「仅限今天」，除非确有事实
- 未经证实的「我注意到贵司最近…」（没有落盘证据就不写）
- 全大写、连续感叹号、多个链接、附件、伪装回复 `Re:` / `Fwd:`
- 采集或引用个人生活信息、敏感个人信息

## 6. 示例（同一份素材渲染四种渠道）

素材：`hook_id=cost`，`hook_text=「贵司 X 系列依赖进口，交期一波动单月空运吃掉约 5% 毛利」`，
证据 `https://vendor.example/products`，CTA = 回复索取一页交期对比表。

| 渠道 | 成品要点 |
|---|---|
| `email` | 主题 `交期波动下的 5% 空运成本`（≤35 字符）；正文：钩子 1 句 → 我方做什么 → 一个证据 → 「需要我把一页对比表发您吗？」→ 签名 → 退订；`add-email-draft --hook-id cost` |
| `sms` | `您好，我是<公司><姓名>。看到 X 系列交期波动会吃掉约 5% 毛利，我们做同规格本地备货。需要一页对比表吗？回复 STOP 不再联系`；`add-message --channel sms` |
| `call` | 开场 30 秒自报家门 + 同一句具体事实 + 「方便占用 30 秒吗」；要点 3 条；异议「已有供应商」→ 只做备选，不要求换供应商；`add-message --channel call` |
| `linkedin` | 连接请求：`您好 <名>，看到贵司 X 系列的进口交期讨论，我们在做同规格本地备货，想请教下贵司的备货节奏。（不放链接、不推销）`；`add-message --channel linkedin` |

四条都指向**同一个 CTA**、同一个 `hook_id`、同一份证据 —— 这就是「一次挖掘、多渠道复用」。

## 7. 登记到钩子库（战绩闭环）

`add-email-draft --hook-id <pattern>` 是战绩积累的**原料**：发送侧发送 +
核验后，由 `lead_db.py hook-stats` 按 hook_id 聚合
drafted/sent/opens/replies，再经 `hook_library.py record --business <key>
--market <码>` 回写租户级钩子库。下一轮挖掘先用
`hook_library.py recommend` 取实测结论，再渲染新渠道文案，形成闭环。
