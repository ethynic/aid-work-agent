# 多渠道联系方式采集口径

配合 `lead_db.py add-channel` 使用。**所有坐标都写进 `bs_ai_lead_gen_contact_channels`
表，每个坐标必须带 `source` / `source_url` / `confidence` / `evidence`，可回溯。**
email 渠道不进本表（落点 `bs_ai_lead_gen_contacts.email`）。

## 0. 总原则

1. **只采集公开商业联系方式**：官网 Contact / Impressum / Team、行业名录企业详情页、
   公开 PDF/展会名录、公司公开社媒主页、公开新闻稿。不访问需登录页面、不绕过权限。
2. **不编造、不拼装成明示信息**：按姓名+域名推出的邮箱可以存在，但必须标
   `source=verified_pattern, confidence=medium` 或 `source=guessed, confidence=low`；
   `guessed` 坐标默认不进发送队列。手机号一律不许推断。
3. **同一官网只抓一次**：正文（写综述用）、邮箱（反混淆）、电话、社媒外链在同一次
   web-search-pro 巡检里一起拿，禁止拆成多次巡检（⚡效率纪律）。
4. **联系方式属于公司/职位，不是个人生活信息**：不采集私人手机、私人社媒、家庭地址、
   种族/宗教/健康/政治倾向等敏感个人信息。
5. **优先对口联系人**：采购/运营/供应链/技术负责人 > 老板 > 通用窗口 `info@`。
   通用窗口保留但 `confidence` 不高于 `medium`，邮件主题与称呼按「您好」处理。
6. **本轮只记录不触达**：sms/call/whatsapp/telegram/wechat/line/kakao/linkedin/x/
   facebook/instagram 坐标仅入库供后续人工或后续技能消费，本轮不做任何触达动作。

## 1. 邮箱 `email`

来源优先级：`官网明示` > `行业名录详情页` > `公开文档/PDF` > `邮箱模式+验证` > `推断`。

**反混淆是必做步骤，不是可选增强**（德 / DACH 制造业尤其普遍）：

- 用 `scripts/email_deobfuscate.py`（纯标准库、`--selftest` 自检）：ROT13 /
  `data-enc-email` / Cloudflare `data-cfemail` / `[at] (at) &#64;` / `[dot]` /
  HTML 实体 / JSON-LD `"email"` / mailto / 明文。
- 深层路径：德语站常在 `/de/impressum`、`/footer/imprint`、`/kontakt`；法语 `/mentions-legales`；
  日本 `/company/contact`、特定商取引法页面；不能只爬首页。
- **品牌域名闸门**：反混淆会顺带抓出经销商 / 海外分部邮箱，只保留与目标官网
  同域 / 子域，或「品牌前缀 + 欧洲通用 TLD」的地址；其余记在 probe 证据里但不入库。
- 判定纪律：反混淆 + 深层路径 + 变体重试都做了仍无结果，才写「no_email」。
  未经反混淆就判 `no_email` 属于误判。

清理：去掉 `mailto:`、`u003e`、`&gt;`、空格；剔除 `lists.*` / `noreply@` / `no-reply@`
及招聘、财务、法务、公关、IT、自动应答等明显无关职能（email_rules.py 统一口径）。

## 2. 手机 `sms` 与电话 `call`

**统一 E.164**：`+<国家码><号码>`，无空格、无括号、无连字符，如 `+493012345678`。
`add-channel` 有 E.164 校验；不合格直接拒绝入库。

采集路径（按优先级）：

1. 官网 Contact / Impressum / 页脚（德国 Impressum 通常必列电话；常同时给 `Tel` / `Fax` / `Mobil`）
2. Google Business / 当地黄页 / 行业协会名录的企业详情页
3. 展会参展商名录、产品手册 PDF、报价单模板、公开新闻稿署名
4. 公司 WhatsApp Business 按钮里的号码（常是手机号，归 `whatsapp` 渠道，也可作为 `sms` 候选）

标注要求（写进 `evidence` 与 `notes`）：

- `landline` / `mobile` / `fax` / `switchboard`（总机）；总机要在 `evidence` 写明分机或转接部门
- 是否带 WhatsApp / WeChat 图标 → 同时生成对应 messaging 渠道坐标
- 传真号不入 `call` 渠道

**红线**：不许把官网总机伪装成负责人直线；不许把传真号写进 `call`；
不许从社媒资料里翻私人手机号。手机号默认不采集，租户扩展提示词明确要求时才搜索存储。

## 3. 社媒与即时通讯账号

统一 `value` = **主页完整 URL**（`https://www.linkedin.com/company/...`
或 `https://www.linkedin.com/in/...`）；只有平台不提供 URL 时才写公开账号 ID
（`@handle` 形态，`add-channel` 校验），并在 `evidence` 说明平台与来源页。

| 渠道 | 常见来源 | 备注 |
|---|---|---|
| `linkedin` | 官网页脚社交图标、公司页 / 决策人公开页 | 欧美 B2B 首选；只记公开页 URL，不读需登录内容 |
| `x` | 官网、新闻稿署名、行业报道 | 适合做公开互动线索，不做私信轰炸 |
| `facebook` / `instagram` | 官网社交图标、本地黄页 | 中小批发/零售/建材商常用；Instagram 常是产品展示 |
| `whatsapp` | 官网浮动按钮、`wa.me/<number>` 链接 | 拉美 / 中东 / 印度 / 东南亚高覆盖 |
| `wechat` | 官网二维码、名片、阿里巴巴店铺 | 中国及华人供应链；记公众号 / 企业微信 ID |
| `line` | 日本 / 中国台湾地区官网、名片 | 日本 B2B 常见 |
| `kakao` | 韩国官网、黄页 | 韩国 B2B 常见 |
| `telegram` | 官网、公开频道 | 东欧 / 中亚常见 |

采集纪律：

- 社交图标链接常是短链或跳转链，`evidence` 记**最终落地 URL**；抓不到落地页就标 `confidence=medium`。
- 区分**公司页**与**个人页**：公司页归到 lead 维度，个人页必须有姓名对应关系才挂到联系人。
- 社媒只分析公开商业内容（职业履历、行业观点、公开发言），用于钩子；不分析个人生活。
- 找不到就写「社媒信息不足」，**不写钩子、不编造账号**。

## 4. 各国渠道偏好（决定优先采哪一类，不是排除其它渠道）

| 市场 | 优先渠道 | 说明 |
|---|---|---|
| DE / AT / CH | `email` + `call`（Impressum） + `linkedin` | Impressum 法定披露，电话与邮箱可得性最高 |
| US / CA | `email` + `linkedin` | 电话可用但冷呼合规更严格 |
| UK / FR / IT / ES / NL / 北欧 | `email` + `linkedin` | GDPR 口径下邮件走合法利益，手机类需同意记录 |
| 中东 / 拉美 / 印度 / 东南亚 | `whatsapp` + `email` + `call` | WhatsApp 常是实际主渠道 |
| 日本 | `email` + `call` + `line` | 本地语言敬体，电话前先邮件更稳妥 |
| 韩国 | `email` + `kakao` + `call` | 敬语与职位称呼要准确 |
| 中国及华人供应链 | `wechat` + `call` + `email` | 企业微信 / 公众号 ID 记入坐标 |

**合规口径（我方定案）**：EU/EEA/UK/CA 市场的 `sms` / `call` / messaging 类坐标
**本轮只记录不触达**（无论是否取得同意）；触达属后续人工决策或后续技能的合规审查范围。
法规以最新公告为准。

## 5. 置信度判定表

| 情况 | `source` | `confidence` |
|---|---|---|
| 官网明示负责人邮箱 / 电话 | `official_site` | `high` |
| 官网明示通用窗口 `info@`、总机 | `official_site` | `medium` |
| 反混淆还原出的地址（有原始页面） | `official_site` | `high` |
| 行业名录 / 黄页企业详情页字段 | `directory` | `medium` |
| 公开 PDF / 展会名录 | `public_document` | `medium` |
| 公司公开社媒主页 URL | `social_profile` | `high` |
| 个人公开社媒页（有姓名对应） | `social_profile` | `medium` |
| 邮箱模式 + 第三方验证通过 | `verified_pattern` | `medium` |
| 仅凭姓名 + 域名推断 | `guessed` | `low` |

`verified` 字段只在真正做过投递层核验后置 true；挖掘阶段一律 false。

## 6. 质检清单（执行 `add-channel` 前逐项过）

- [ ] 每个坐标有 `source_url`（`guessed` 除外）与 `evidence`
- [ ] 邮箱已反混淆、已过品牌域名闸门、已剔无关职能
- [ ] 电话是 E.164，标了 landline/mobile/switchboard
- [ ] 社媒是最终落地 URL，区分公司页/个人页
- [ ] 没有把通用邮箱伪装成负责人邮箱
- [ ] 没有编造手机 / 社媒账号 / 私人信息
- [ ] 退订名单（`lead-gen/unsubscribed.csv`）中的地址不再采集成新坐标
- [ ] 每公司 ≤2 个对口联系人且职位不同
