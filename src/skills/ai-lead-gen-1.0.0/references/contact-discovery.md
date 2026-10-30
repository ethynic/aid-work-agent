# 联系人与邮箱挖掘手册

联系人挖掘阶段（SKILL.md 第 5 步）使用。

## 零、核心原则：邮箱要「对口」，不只是「能发」

**官网上抓到一个邮箱，不等于拿到了有效目标邮箱。**

很多公司官网只有这几类地址：

| 官网上的地址 | 它是什么 | 能否作为目标邮箱 |
|---|---|---|
| `hr@` `jobs@` `careers@` | 招聘邮箱 | ❌ 与产品推销完全无关 |
| `legal@` `finance@` `press@` `marketing@` | 其他职能部门 | ❌ 与产品推销无关 |
| `info@` `contact@` `office@` | 公开联系窗口 | ❌ 没有具体负责人 |
| `purchasing@` `export@` `production@` | 对口的部门邮箱 | ✅ 有效目标 |
| `maria.schmidt@` | 个人企业邮箱 | ✅ 有效目标（需核验姓名） |

**只有「与当前产品/服务对应的负责人或部门邮箱」才��有效邮箱。**
公开窗口和无关职能邮箱不算 —— 发过去既浪费额度，又拉低回复率，还可能因
「骚扰无关部门」被举报。

> **判定由 `email_rules.py` 统一负责**。采集（`web-search-pro` 技能的抽取管线）、
> 入库（`lead_db.py`）、发送（`send_emails.py`）、验证（`email_verify.py`）
> 共用同一套口径，不各自为政。

判定分三个正交维度：

| 维度 | 取值 | 含义 |
|---|---|---|
| `type` 形态 | `personal` / `department` / `generic` / `unknown` | 邮箱长什么样 |
| `function` 职能 | `purchasing` / `sales` / `hr` / `legal` / ... | 属于哪个业务口 |
| `relevance` 相关性 | `relevant` / `generic` / `unknown` / `irrelevant` | 与本次推销是否对口 |

**只有 `relevance == "relevant"` 才是有效目标邮箱。**

### 声明本次产品对应的部门（重要）

不同产品要找的部门不同。卖工业阀门找采购/工程，卖包装材料找生产/采购，
卖人力资源服务才找 HR。**campaign 目录下的 `contact_targets.json` 声明这一点**：

```json
{
  "product_or_service": "工业阀门（面向设备制造商）",
  "target_functions": ["purchasing", "engineering", "production"],
  "exclude_functions": [],
  "notes": "目标客户是欧美设备制造商，采购/工程/生产是决策口"
}
```

| 字段 | 作用 |
|---|---|
| `target_functions` | 对口职能白名单。填了则只有这些职能算 `relevant`；填 `null` 表示不限制（全部对口职能均可） |
| `exclude_functions` | 显式排除的职能，即使在对口范围内也判 `irrelevant` |
| `product_or_service` | 记录本次推销的产品，供复核与写邮件时对齐口径 |

`management`（CEO/老板/总经理）与 `personal`（个人邮箱）**恒定相关**，不受白名单限制 ——
决策层与具体人天然是商业推介的对象。

命令行可临时覆盖：`--target-functions purchasing,sales`。

### 三道闸门

| 闸门 | 位置 | 行为 |
|---|---|---|
| 采集 | `web-search-pro` 技能（`pipeline.py --extract`） | 无关职能邮箱直接剔除并单独报告；公开窗口保留在报告里但**不写进联系人库** |
| 入库 | `lead_db.py add-contact` | `irrelevant` 与 `generic` 一律拒收，打印原因并提示继续找 |
| 发送 | `send_emails.py` | 干跑阶段标 `BLOCK`；正式发送阶段**硬跳过**，不占用发送配额 |

## 一、联系人优先级

按决策权与采购影响力排序。**每家公司最多取 2 个联系人，且职位必须不同。**

| 优先级 | 职位 | 英文常见称谓 | 本地语言常见称谓 | 适用场景 |
|---|---|---|---|---|
| 1 | 创始人/老板/总经理 | Founder, Owner, CEO, Managing Director, Geschäftsführer, PDG, Direttore | 法定代表人、董事长、总经理 | 中小企业、无采购部门时直接拍板人 |
| 2 | 采购总监/采购经理 | Purchasing Director, Procurement Manager, Head of Purchasing, Sourcing Manager | 采购总监、采购经理、供应链总监 | 你卖的是"要采购的东西" |
| 3 | 运营总监/工厂经理 | Operations Director, COO, Operations Manager, Plant Manager | 运营总监、生产厂长、运营经理 | 你卖生产设备/耗材/工艺服务 |
| 4 | 产品/技术负责人 | Head of Product, CTO, Technical Director, R&D Manager | 产品总监、技术总监、研发经理 | 卖技术方案、软件、定制开发 |
| 5 | 采购部门其他 | Procurement Specialist, Buyer, Sourcing | 采购专员、采购工程师 | 大公司采购分工细时的切入口 |
| 6 | 商务/市场负责人 | Business Development, Sales Director, Marketing Manager | 商务总监、市场经理 | 对方走渠道/代理路线 |

## 二、联系人查找路径（按可靠性排序）

### 总体导向：优先个人企业邮箱，官网缺失必兜底

- **优先个人企业邮箱**：在目标国法规允许的前提下，优先为目标联系人找到其**个人企业邮箱**——公司域名（@company.com）下、归属具体员工个人的邮箱，如 `j.doe@company.com`。它兼具"可个性化"与"可识别企业归属"两大优点，回复率显著高于职能/通用邮箱。私人邮箱（gmail/gmx/web.de/hotmail 等个人邮箱服务）合规风险高，仅在确无公司邮箱且其本人公开用于商务时慎用。
- **官网缺失必须兜底**：路径 1 官网无 contact 页、或抓不到负责人邮箱，**不代表没有邮箱**。须按路径 2→3→4→5 依次扩展，并配合邮箱验证工具（Hunter/Snov/Apollo）继续找，直到确认所有公开渠道都无果，才判 `no_email`。

### 什么算「官网没找到」

`web-search-pro` 技能的抽取管线会明确报出这三种情况，**都不是 no_email**：

| 情况 | 脚本信号 | 该怎么办 |
|---|---|---|
| 只抓到招聘/无关职能邮箱 | `emails_rejected` 非空，`valid_targets = 0` | 换路径找对口部门邮箱 |
| 只抓到公开窗口（info@/contact@） | `valid_targets = 0`，`emails` 全是 `generic` | 走路径 3 反推个人邮箱，或改用官网表单 |
| 页面是 JS 渲染抓不到 | `pages_fetched` 只有首页、正文极短 | 用浏览器技能打开 contact 页读 |

**看到 `valid_target_count = 0` 就必须继续找**，不能直接判 `no_email`。
脚本会在 stderr 打印 `[继续找] ...` 提示，报告 JSON 里 `needs_fallback_search: true`。

### 路径 1：官网（最可靠，confidence=high）
逐个检查这些页面：
```
/contact  /contact-us  /kontakt  /contacto  /contatti  /お問い合わせ /문의
/about  /about-us  /über-uns  /impressum  /a-propos  /chi-siamo  /会社概要
/team  /our-team  /leadership  /management  /mitarbeiter
/company  /profile  /organization
```
要点：
- 德语网站必看 **Impressum**（法定要求，含负责人姓名）
- `Impressum` / `Mentions légales` / `公司情報` 在日韩是法定公开页，几乎必含法人姓名
- 很多公司把总经理照片+名字放在首页团队区

### 路径 2：行业名录站（confidence=high）
展会名录、B2B 平台、协会会员页常直接给联系人字段。用 WebFetch 读企业详情页。

### 路径 3：邮箱模式推断 + 验证（官网无明示邮箱时找个人企业邮箱的关键手段）

先从官网已知邮箱（或同行业公司）反推公司邮箱模式，再套用到目标联系人姓名，生成候选个人企业邮箱。**这一步是"官网没给邮箱时仍能拿到个人企业邮箱"的核心手段**。

务必经过验证工具（Hunter/Snov/Apollo）或公开引用核验，核验通过则标 `verified_pattern` / `confidence=medium` 可发；**仅模式拼出、未经任何核验的才是 `guessed` / `low` / 不推荐**（对应铁律 1，不要直接发）。

常见模式：
```
first.last@        最常见
first@             小公司常见
flast@             
firstl@            
first_last@         
last.first@
firstlast@          
f.last@
first-last@
```

### 路径 4：社媒公司页成员列表（confidence=medium）
LinkedIn 公司页 → People → 按职位搜，或用 WebSearch `site:linkedin.com/in "<公司名>" purchasing`。

### 路径 5：新闻稿与公开报道（confidence=high）
公司官网 News/Press 页、行业媒体报道常引述负责人姓名+职位，且常附邮箱。

### 路径 6：招聘 JD（判断而非获取）
招聘网站上的职位描述能推断出：公司在做什么、在解决什么问题、找什么样的人。用于第 7 步痛点分析。

## 三、邮箱验证流程

**禁止直接发送猜测的邮箱。** 验证流程：

1. **格式校验**：用 `scripts/email_verify.py` 检查语法与常见域名拼写错误
2. **MX 记录检查**：脚本会做 DNS MX 查询，无 MX 说明域名无法收信
3. **验证工具核验**（有 API key 时）：Hunter.io / Snov.io / NeverBounce / ZeroBounce，输入邮箱查 deliverability
4. **无工具时**：降级为在 Google 搜 `"<完整邮箱>"` 看是否有公开引用；或用官网联系表单替代邮件触达

`email_source` 与 `confidence` 的对应关系：

| 来源 | email_source | confidence | 是否可发 |
|---|---|---|---|
| 官网明示 | `official_site` | high | 可发 |
| 名录站明示 | `directory` | high | 可发 |
| 模式推断 + 验证工具通过 | `verified_pattern` | medium | 可发 |
| 模式推断无验证 | `guessed` | low | **不推荐**，优先走官网表单 |
| 用户手工提供 | `manual` | high | 可发 |

⚠️ **注意：`confidence` 说的是「地址是否可靠」，不是「这个地址是否是目标」。**
一个 `hr@` 的 confidence 可以是 high（官网明示、地址真实），但它**不是有效目标邮箱**。
两个维度必须同时满足才算有效：

```
有效目标邮箱 = relevance == "relevant"  且  confidence ∈ (high, medium)  且  email_source != "guessed"
```

`lead_db.py score` 会直接打印 `VALID TARGET emails: N`，用这个数判断名单质量。

## 四、职能邮箱要分三类看

旧文档把角色邮箱一律归为「不要发」，但这过于粗糙 —— `purchasing@` 和 `hr@` 都是角色邮箱，
性质完全不同。按职能分三类：

| 类别 | 例子 | 处置 |
|---|---|---|
| **对口职能**（对本次产品/服务负责） | `purchasing@` `export@` `production@` `engineering@` `ceo@` | ✅ 有效目标，可以发 |
| **公开窗口**（无具体负责人） | `info@` `contact@` `office@` `hello@` `admin@` `support@` | ❌ 不入联系人表；可验证域名有效性、判断公司规模；正式触达改用官网表单 |
| **无关职能**（与推销无关） | `hr@` `jobs@` `careers@` `legal@` `finance@` `press@` `marketing@` `noreply@` | ❌ 直接剔除；看到就要换路径去找对口邮箱 |

**公开窗口不作为目标邮箱**的原因：它没有任何负责人，邮件到达后要么被自动回复、
要么在多人邮箱里丢失。个人化外联发到公开窗口，回复率极低且容易被判垃圾。

**无关职能不能发**的原因：把推销邮件发给招聘邮箱或法务邮箱，是明确的骚扰信号，
可能被直接举报，进而损害发件域名声誉。

## 五、contacts.csv 字段规范

`scripts/lead_db.py` 使用以下列，勿改列名：

```csv
company,name,title,email,email_source,email_type,email_function,email_relevance,phone,linkedin,confidence,language,notes
```

| 新增列 | 取值 | 含义 |
|---|---|---|
| `email_type` | `personal` / `department` / `generic` / `unknown` | 邮箱形态 |
| `email_function` | `purchasing` / `sales` / `hr` / `legal` / ... | 业务职能归属 |
| `email_relevance` | `relevant` / `generic` / `unknown` | 与本次推销是否对口 |

`email_relevance` 只可能是 `relevant` / `generic` / `unknown` ——
`irrelevant` 的地址在入库时就被拒收，不会出现在表里。

`notes` 字段记录社媒洞察（第 9 步），格式：
```
[社媒] 2024年在X上发布关于Y的文章；关注Z技术
[痛点] 官网招聘显示正在扩产
[偏好] 公开表示倾向本地供应商
```

## 六、决策链补充技巧

找到一个人后，顺藤摸瓜找第二个：
1. 搜索 `"<公司名>" "procurement"` / `"<公司名>" "operations manager"`
2. 查 LinkedIn 员工列表里同部门的人
3. 看官网新闻稿里被引述的多个人（通常涵盖不同职能）
4. 查公司是否有"某某事业部/某某分公司"负责人

## 七、隐私与合规红线

- 只采集公开可得的信息
- 不访问需登录/付费墙/明确禁止抓取的页面
- 不采集与业务无关的个人信息（家庭、爱好、健康、政治、宗教）
- 社媒分析仅限公开职业内容
- **区分"个人企业邮箱"与"私人邮箱"**：
  - 个人企业邮箱 = 公司域名下归属具体员工个人的邮箱（`j.doe@company.com`）。可识别企业归属、便于收件人管理与退订，是优先目标。
  - 私人邮箱 = 个人邮箱服务（`gmail.com` / `gmx.de` / `web.de` / `hotmail.com` / `outlook.com` / `yahoo.com` 等）。各国合规风险更高，德国对个人邮箱冷邮件尤为敏感，仅在确无公司邮箱且其本人公开用于商务时慎用。
- 联系人为个人企业邮箱时，在欧盟/英国仍需做合法利益评估（个人化触达提升了"利益平衡测试"要求），见 `email-compliance.md`；德国等地区优先改发公司职能邮箱。