# 协会官网优先资料补全设计

## 2026-07-29：批量无界面 CLI 补充设计

入口为 `clients/association-enrichment-cli/association_enrichment_cli.py`。文字输入可重复传
`--association`，也可传包含“协会名称/association_name/association/单位名称”列的 CSV 或
XLSX。程序先规范空白、按名称去重并保留首次顺序，再逐协会串行执行，单个协会失败不得阻断后续。

编排顺序：

1. 项目 `WebSearchTool` 搜索官网候选，项目 LLM 判断唯一官网；
2. 先访问 HTTPS，失败后将同一 URL 降级为 HTTP；浏览器始终显式 `headless=false`；
3. 站内 collector 自动发现协会简介、领导、机构和联系方式页面，再交给项目 LLM 提取；
4. 官网不存在或两种协议均不可达时，使用普通网络检索结果补基础字段，同时手机字段强制留空；
5. 已知会长/秘书长但官网没有手机时，逐人调用微信搜一搜 RPA；
6. 写一个 XLSX，包含协会名称、14 个业务字段、处理状态、来源摘要、错误摘要和处理时间。

外部能力均通过 `AssociationBatchEnricher` 的可注入异步端口接入，单测不访问真实网络和微信。
完整手机号只写入用户指定的本地 Excel；CLI stdout 只输出数量、状态和文件路径，微信原始证据继续
使用当前 Windows 用户 DPAPI 加密。错误摘要会脱敏手机号。

姓名字段的业务归属由 LLM 判断。程序允许姓名 value 与逐字 quote 仅存在网页排版空白差异，例如
“潘  华”与“潘华”；source URL 同域、quote 逐字存在、手机号/邮箱格式、计数 token 和号码就近绑定
仍属于确定性安全边界，不能放宽。

> 日期：2026-07-28
> 状态：🔧 部分完成（本机批量 CLI MVP 已完成，生产安全加固待开发）
> 关联：[微信搜一搜 RPA](wechat-souyisou-rpa-design.md)

## 1. 目标与结论

输入协会名称，优先从协会当前官网补全以下字段：

- 主管单位、单位等级；
- 会长姓名/手机、秘书长姓名/手机；
- 单位地址、单位邮箱；
- 分支机构数量、单位会员数量、个人会员数量；
- 品牌会议连续次数；
- 单位官网、单位公众号。

**搜索结果只用于发现候选官网，不作为字段事实来源。** 字段值必须来自已验证官网页面、
官网附件或官网指向的公众号；官网未提供手机时，才使用微信搜一搜 RPA 补充，并标记为
`wechat_souyisou` 来源，不能覆盖更新的官网事实。

现有项目已具备搜索、浏览器、PDF、OCR、LLM Gateway 和文件落盘能力，但缺少官网身份
验证、站内遍历、字段级证据、冲突/时效规则和批处理状态机。因此应新增一个项目内
`association_profile_enrichment` 业务工具，而不是让通用 Agent 临时拼接多个工具。

## 2. 现有基础设施盘点

| 能力 | 现有实现 | 结论 |
|---|---|---|
| 候选官网发现 | `WebSearchTool` / Tavily | 可复用；只取 URL、标题和候选分数 |
| 普通 HTTP | `HttpApiTool` | 仅作轻量页面/API兜底；CAAPA 官网实测 403 |
| 动态官网访问 | `BrowserAutomationTool` | 可复用；venv匹配浏览器安装后已通，CAAPA 官网自身返回异常访问/403 |
| HTML/页面交互 | Browser orchestrator/page ops | 可复用，但需增加确定性站内采集接口 |
| PDF文本 | `PdfProcessTool`、`pdf_to_md` | 可复用，文本不足自动降级 OCR |
| 图片/扫描PDF | `PaddleOCRDocParsingTool` | 已在微信图片型 PDF 真机验证 |
| 结构化判断 | `src.llm.gateway.llm_gateway` | 可复用；外层必须做严格 Schema 与证据原文校验 |
| 大内容落盘 | `_spill.py`、read/grep | 可复用 |
| 微信联系人补充 | `clients/wechat-souyisou-rpa` | 已通；仅在官网缺失手机时调用 |
| 协会档案/证据库 | 无 | 需要新增 |

2026-07-28 项目内探针：

- `WebSearchTool("中国游艺设备游乐园协会 官网")` 成功发现 `https://www.caapa.org`；
- `HttpApiTool(GET https://www.caapa.org)` 返回 403；
- venv 中 Playwright 1.60.0 最初缺少匹配 Chromium revision 1223；安装后最小 Playwright
  与完整 `BrowserAutomationTool` 均可正常启动；
- CAAPA 官网对无头 Chromium 返回“异常访问/403”；Browser 工具现已在页面快照层
  确定性返回 `success=false/status=blocked/ACCESS_BLOCKED`，不再接受 LLM `done` 覆盖；
- 可信本地 `headless=false` 可见模式已真机通过：AI访问CAAPA首页、读取页面、
  点击“关于协会”，进入 `/About/1.html` 并获取协会简介正文，共4个编排步骤；
  任务结束后 Chromium 与worker进程均已回收。该模式未触发无头访问风控；
- 本地 Redis/数据库审计不可用时会降级并告警，但不再阻塞浏览器执行；
- Windows PowerShell 通过管道传中文源码会损坏查询，正式调用必须使用 UTF-8 JSON。

## 3. 数据来源与优先级

优先级从高到低：

1. 官网当前页面；
2. 官网同域 PDF/Word/图片附件；
3. 官网明确链接的官方公众号；
4. 民政部/全国社会组织等权威政府页面，仅用于官网身份和登记信息交叉验证；
5. 微信搜一搜详情，仅补官网缺失的手机或公众号文章线索；
6. 其他搜索结果只作为候选线索，不写入最终字段。

同字段冲突时不“平均”：

- 官网发布日期较新者优先；
- 现任/本届/最新组织架构页优先于历史新闻；
- 无法确认时字段置空并记录 `conflict`，进入人工复核；
- 搜索摘要、百科、自媒体不得覆盖官网值。

## 4. 字段证据契约

每个字段不是单纯字符串，而是：

```json
{
  "field": "secretary_general_name",
  "value": "某某",
  "status": "verified",
  "source_type": "official_site",
  "source_url": "https://example.org/about/leadership",
  "page_title": "协会领导",
  "evidence_quote": "秘书长：某某",
  "published_at": "2026-01-01",
  "retrieved_at": "2026-07-28T09:00:00+08:00",
  "confidence": 1.0,
  "content_hash": "sha256:..."
}
```

手机、邮箱属于敏感数据：API按权限返回；普通日志不记录明文；数据库使用项目统一加密
组件加密保存，证据正文按租户隔离。

## 5. 官网身份验证

`OfficialSiteResolver` 使用搜索发现 3～5 个候选，但必须满足至少两项：

- 首页/关于页出现协会完整名称或已知合法简称；
- ICP/页脚/版权主体或联系方式与协会一致；
- 政府登记页、官方公众号或权威页面反向链接该域名；
- 同域存在协会介绍、组织机构、会员服务等稳定栏目。

域名相似、搜索排名第一或页面标题包含协会名均不足以单独确认为官网。

输出 `verified / ambiguous / not_found`。`ambiguous` 必须人工确认，不能继续自动填表。

## 6. 站内采集流程

```text
association_name
  → WebSearchTool 发现候选域名
  → OfficialSiteResolver 验证官网
  → SiteMapCollector 读取首页、导航、sitemap
  → PagePrioritizer 排序重点页面
  → Browser/HTTP 采集 HTML
  → 官网附件下载
  → PdfProcess/PaddleOCR
  → FieldExtractor 严格 JSON
  → EvidenceValidator 原文与字段格式校验
  → ConflictResolver
  → 官网缺失手机时调用微信 RPA
  → 加密落库与人工复核队列
```

重点栏目关键词：协会介绍、组织机构、领导、负责人、秘书处、联系我们、会员名录、分支
机构、章程、年度报告、品牌活动、公众号。

站内采集必须限制：

- 仅已验证域名及其子域；
- 默认最多 50 页、10 个附件、单任务 5 分钟；
- robots/频率限制、URL 去重、内容哈希缓存；
- 登录、验证码、下载异常返回 `inconclusive`。

第一版高价值内页发现采用确定性语义筛选，不硬编码具体协会 URL。输入为一个或多个
已验证官网页面快照（URL、标题、正文、页面链接），根据链接文字和 URL 中的协会简介、
组织领导、组织架构、分支机构、联系我们、会员等中英文语义排序；只采集已验证域名及
其子域，去除 fragment 后做 URL 去重。抓取器以异步回调注入，因此桌面 Browser、CLI
和后续业务编排可以共用发现策略，不与某一种浏览器实现绑定。

默认最多纳入 12 个高价值页面，交给提取器的正文总量不超过 20,000 字符。页面只有在
完整正文可放入剩余预算时才纳入，不对短页面逐页截断，避免组织领导和联系方式页面
因固定长度裁剪丢失尾部字段。超预算 seed 正文不纳入结果，但其已取得的导航链接仍可
用于发现预算内的短内页。默认抓取尝试次数最多为页面预算的 3 倍，跨域、重复跳转
或超预算页面不能造成无界抓取。未显式声明端口的 verified domain 只允许 HTTP/HTTPS
默认端口；自定义端口必须由 verified domain 明确声明。抓取后发生跨域跳转的页面不得
进入可信页面集合。

collector 只负责抓取前目标 URL 和抓取后最终 URL 校验；Browser/HTTP `page_fetcher`
适配器必须在网络请求发生前独立执行每个 redirect hop、DNS 重绑定和私网/保留地址
SSRF 门禁。仅检查最终 URL 不能证明中间跳转未访问不可信地址，该门禁属于后续适配器
验收的阻塞条件。

## 7. 字段提取规则

| 字段 | 首选页面 | 校验 |
|---|---|---|
| 主管单位、单位等级 | 协会简介、章程、登记/年报 | 原文明确，不从常识推断 |
| 会长/秘书长 | 组织机构、领导、联系我们、最新通知 | 姓名与职务必须同一证据块 |
| 会长/秘书长手机 | 联系我们、通知、附件 | 姓名和号码需同段/同表格/明确字段绑定 |
| 地址、邮箱 | 联系我们、页脚 | 地址/邮箱格式校验 |
| 分支机构数量 | 分支机构列表 | 优先结构化计数，同时保存列表证据 |
| 单位/个人会员数量 | 协会简介、年报、会员页 | 保存“截至日期”；无日期降低置信度 |
| 品牌会议连续次数 | 官网活动专题/历届页面 | 以明确“第N届”为主，不用新闻条数代替 |
| 官网 | 官网验证器 | 域名级证据 |
| 公众号 | 官网二维码/链接/明确文字 | OCR二维码旁文字；后续可增加二维码解析 |

## 8. 需要新增的项目内基础设施

### 8.1 `official_site_resolver`

确定性服务，编排 `web_search + browser`，输出候选、验证信号和最终域名。

### 8.2 `website_content_collector`

在 Browser 工具之上提供机器可测的站内采集接口：

- 获取最终 URL、标题、正文 Markdown、链接和附件；
- 支持 sitemap、同域队列、内容哈希；
- 不依赖自然语言 Agent 自由发挥；
- 保留浏览器审计和租户上下文。

已新增 `official_site_page_collector` 基础服务及
`collect_high_value_official_pages(...)`。调用方负责通过 Browser/HTTP 生成
`OfficialPageSnapshot`，服务负责同域校验、语义发现、排序、去重和预算控制，最终只
输出现有提取器可接受的 `VerifiedOfficialPage[]`。

对于没有普通 `href`、依赖 JavaScript click 切换路由或正文的官网，新增
`official_site_browser_collector`。它从入口页自动扫描可见的 `a`、`button`、
`[role=button]`、`[onclick]` 和导航/菜单项，只对协会简介、协会领导、机构设置、
联系我们等高价值栏目执行点击。每条导航保存为从入口可重放的点击路径；采集其他栏目
前重新打开入口并重放路径，避免依赖人工留下的浏览器状态。点击后只有 URL、正文或
可见子导航发生变化才继续，跨域、无变化、重复状态和超出页数/正文/点击预算的结果均
拒绝。普通 `href` 与 JS click 共用同一发现和安全边界，最终同样输出
`VerifiedOfficialPage[]`。单个页面最多保留前 200 个可见导航候选，结合总点击预算
限制异常 DOM 的枚举成本和路径组合规模。Playwright wrapper 在 browser context 层
于请求发出前拦截当前页及 popup 的跨域顶层 frame 导航；第三方图片、脚本和 iframe
等子资源不按顶层导航处理。即使文本同时含
“会员”等高价值词，登录、注册、退出、删除、提交、报名、支付和下载类动作也禁止点击。

Playwright 入口 `collect_official_pages_with_playwright(...)` 的 `headless` 必须由
调用方显式传入严格布尔值，`None`、数字和字符串均拒绝；CAAPA/CRRA 等真机风控验收
使用 `headless=false`。Codex 或人工手动
点击栏目后再把正文交给提取器，只能用于调查，不能计为自动采集验收通过。
`navigation_timeout_ms` 在 wrapper 和 driver 两层均要求非布尔正整数。入口页在
`domcontentloaded` 后按不超过 250ms 的固定间隔等待 SPA 正文和导航出现；不足 250ms
的预算不额外 sleep，点击后仍先即时读取一次状态。BrowserContext route 使用 Playwright
Python 的单参数 handler，并从 `route.request` 取得请求。任何异常路径最终关闭 browser。

### 8.3 `association_profile_enrichment`

Agent 可见的唯一业务工具：

```json
{
  "association_name": "中国游艺设备游乐园协会",
  "refresh": true,
  "use_wechat_fallback": true
}
```

返回档案 ID、字段状态汇总和人工复核项；敏感字段按权限返回，不在工具日志中明文展示。

### 8.4 数据模型

- `association_profiles`：协会当前档案、官网、采集状态、版本；
- `association_profile_fields`：字段值、状态、来源、置信度、有效期；
- `association_evidence`：URL、标题、引用、时间、哈希、加密正文引用；
- `association_collection_runs`：状态机、重试、错误码、费用、耗时；
- `association_review_tasks`：官网歧义、字段冲突、低置信度人工复核。

所有表必须含 `tenant_id` 并遵守项目租户隔离规范。

## 9. 状态与完成语义

- `complete`：官网已验证，所有可找到字段已处理，缺失项有明确 `not_published`；
- `partial`：官网已验证，但附件/OCR/微信等部分来源失败；
- `inconclusive`：官网身份或关键证据冲突；
- `not_found`：完成限定范围采集后官网确实未发布该字段；
- `blocked`：验证码、登录或人工确认。

字段空值必须区分 `not_published / source_failed / conflict / not_applicable`，不能统一写空字符串。

## 10. 当前判断

官网优先路径在架构上可行，约 70% 能力可直接复用；尚未形成可上线的业务闭环。
最大阻塞不是 LLM，而是：

1. CAAPA 等官网存在无头浏览器风控，需要合规的桌面浏览器/人工接管回退；
2. 缺少官网身份验证；
3. 缺少字段级证据与冲突模型；
4. 缺少确定性站内采集与批处理状态机。

先完成这些能力，再接微信 RPA，会比让 Agent 直接依赖搜索摘要稳定得多。

## 11. 官网 14 字段结构化提取器（2026-07-28）

新增 `src/services/association_profile_extractor.py`，输入仅接受
`VerifiedOfficialPage[]`（`verified_official=true`）和已验证域名，不接受搜索摘要、
任意网页或未验证 URL。

14 个字段统一使用 `FieldEvidence(value, evidence_quote, source_url)`，Pydantic
`extra=forbid`。未匹配时三个值均为空。非空证据必须逐字存在于对应官网正文，value
必须存在于引用；会长、秘书长电话引用必须同时包含对应姓名和电话。单位官网由
verified domain 确定性生成。

单次提取最多 50 页、正文合计 20,000 字符，避免中文正文超过 Provider 上下文并限制
成本。官网标题和正文按不可信 JSON 数据传给模型，system/user 指令均明确禁止执行正文
内的提示。官网表述没有统一词表，字段业务语义和人员身份由 LLM 结合完整原文判断；
程序不再用固定中文关键词二次否决。程序只负责可确定验证的安全边界：严格字段结构、
同域 source URL、quote 逐字存在、value 位于 quote、计数为独立 ASCII 数字 token，
以及邮箱和手机号格式。同一 quote 含多个号码时，号码必须是距离对应姓名最近且唯一
的候选；会长、秘书长手机仍必须与对应姓名位于同一 quote。
四个数量字段允许 Provider 返回非布尔、非负 JSON integer，并在严格 schema 校验前
归一化为十进制 ASCII 字符串；布尔值、负数、浮点数、科学计数法和非数量字段数值均
拒绝。归一化不跳过原文 quote、精确数字 token 或 source URL 校验。

Provider 异常、坏 JSON、额外字段、非法 schema、虚构证据或姓名电话错绑均使整个结果
成为 `inconclusive`，不静默保留部分字段。本阶段只提供服务接口，不建数据库。

模型只有在原文整体语义能明确支持字段归属时才填写；无法确定单位会员/个人会员、
人员身份或其他字段含义时必须返回 null。该语义判断不再固化为关键词列表，以兼容
不同官网的自然语言、表格标题和栏目上下文。
