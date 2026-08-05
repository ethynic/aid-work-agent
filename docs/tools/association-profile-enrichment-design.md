# 协会官网优先资料补全设计

## 2026-07-30：本地调查工作台

CLI 核心保持不变，新增独立本机 FastAPI UI 复用批处理与 provider，不通过 shell
调用 CLI。文字和 CSV/XLSX 均先由项目 LLM 做有原文证据约束的清单解析与语义去重，
经用户确认后串行执行。任务展示当前协会、步骤和总体进度，完成后下载 Excel。

官网 collector/provider 增加可选审计回调，记录打开 URL 及采集页面；微信既有 DPAPI
artifact 经当前用户解密后重新写入任务级 DPAPI 详细日志。普通元数据只含脱敏摘要，
页面正文、列表、详情与 OCR 仅通过环回接口按需解密。完整设计与开发状态见
[本地调查工作台 UI 设计](association-enrichment-ui-design.md) 和
[UI 开发计划](association-enrichment-ui-dev-plan.md)。

## 2026-08-03：微信会话故障熔断契约

批量补号把微信执行结果分成两类：普通业务终态 `not_found/inconclusive` 可继续；
`cleanup` 阶段失败、RPA 外层超时及前台状态丢失属于会话级致命错误，必须停止当前批次，
禁止复用可能已污染的微信窗口。PowerShell JSON 仅返回稳定错误码、阶段和结果状态；
stderr 不直接进入审计或 Excel，仅记录长度与 SHA-256。

### 2026-08-05：正式连续查询会话交接

正式批次中，同一 `ProjectAssociationProviders` 实例串行承载微信查询序号。仅当上一条 RPA 已实际
启动并返回 `ok=true/session_closed=true`，下一条启动前发送一次 Alt+Tab、等待 1000ms，再进入原有
PowerShell `collect` 启动逻辑；首条不发送。非零退出、超时、响应无效或清理不确定均不授予交接资格。
资格在发送前消费，交接失败按 `WECHAT_HANDOFF_FAILED:handoff` 会话级熔断，禁止补发或启动下一条。
单条内部逻辑不变。完整真机证据、风险边界和待执行验收见
[微信 RPA 连续查询会话交接记录](wechat-rpa-session-handoff.md)。

独立详情顶层窗口采用更窄的生产交接规则：仅在受原结果 HWND guard 保护的点击刚完成后，完整核验
当前前台插件身份并绑定详情 HWND；详情返回只允许本会话已见 HWND，且必须重新通过原结果页证据。
终态清理跟随最后一个已证明的会话 HWND。此前 9→10 成功是 `flow_probe` 证据，正式 `collect` 的
真机验收仍待执行。

若 LLM 已生成 `found` 的加密证据 artifact，清理失败不能撤销业务证据。Python provider
先通过受限 artifact 路径和 `extract-mobile.ps1` 重新验证手机号，再抛出携带
`recovered_mobile` 的致命异常。编排器先保存该手机号和来源，完成当前行，再熔断批次。
这不是忽略清理错误：当前行仍记录具体错误码和 `cleanup` 阶段，后续负责人及协会均不执行。

## 2026-08-03：字段级证据隔离与有限定位恢复

官网 14 字段仍分别执行原有来源、quote、格式和姓名—手机号绑定校验，但校验终态改为字段级：
无关计数、邮箱或地址字段不合格时仅清空该字段，已经验证的会长、秘书长及其他字段继续保留；
若所有候选字段均被拒绝，整体仍以 `FIELD_EVIDENCE_REJECTED` 失败。严格 JSON 无法解析和顶层
Schema 不合法分别使用 `STRICT_JSON_INVALID`、`PROFILE_SCHEMA_INVALID`，不会伪装成成功。

领导姓名使用独立的官网领导页提取，即使主 14 字段提取失败也会运行；会长和秘书长之间同样按字段
隔离。网络搜索回退采用相同的逐字段拒绝方式，并继续禁止填写手机字段。

微信详情定位仅允许两种有限恢复：无卡片定位点时等待并重截一次；点击前短暂失焦时只激活原受信
插件 HWND 一次并重新校验。点击后的未知页面状态、清理失败和外层超时不重试，继续执行批次熔断。

### 2026-08-03：搜一搜可见状态门禁

每次人员检索先从可信微信主窗口打开搜一搜，将首次打开视为可能复用旧标签的脏会话。清理前必须
同时验证插件 HWND 与微信主窗口 HWND 不同，并核对 `WeChatAppEx.exe` 进程路径、窗口类名和标题；
验证通过后只向该插件顶层 HWND 发送一次带超时的 `WM_CLOSE`。必须确认插件窗口已销毁或隐藏，
再恢复并复验可信微信主窗口（当前真机版本为 `Weixin.exe`、`Qt51514QWindowIcon`、标题“微信”）；
拒绝关闭或等待超时均按会话级致命错误熔断。下一次搜索必须重新枚举
并取得当前可信插件 HWND，不复用上一次句柄。打开失败只允许重新激活可信主窗口并重开一次。

正常终态与 `finally` 异常终态使用同一顶层关闭函数。`detailMayBeOpen` 只写入脱敏诊断，终态清理
不得发送详情层 `Ctrl+W`，也不得先复制复验结果页；同一 HWND 内部面板或后台标签不构成额外清理
步骤。failure artifact 额外记录无敏感信息的 `cleanup_error_code`，RPA JSON、批处理错误列和 CLI
摘要保留 `PLUGIN_CLOSE_REJECTED`、`PLUGIN_CLOSE_TIMEOUT`、`MAIN_*` 等具体稳定码。若业务结果已为
`found`，仍按上文 DPAPI 证据恢复契约保存已验证手机号，然后以具体清理码熔断批次。

### 2026-08-03：搜索输入焦点与提交门禁

真机确认可信微信主窗口执行 `Ctrl+F → Down → Enter` 后，焦点确定进入搜一搜输入框。新会话导航
完成并验证可信插件前台后，必须直接粘贴并回读；导航到回读完成之间禁止截图、鼠标点击、窗口激活
或其他可能抢焦点的操作。

粘贴本次 query 后从当前控件执行 `Ctrl+A/C` 回读；规范普通空白和全角空白后必须与
本次 query 精确相等，且提交前可信插件仍在前台，才允许按 Enter。空值、旧 query、部分 query、
聊天框文本或前台丢失返回稳定输入子码、阶段 `input_verify`，
不得进入 ready/list/card。结果就绪还必须同时证明 `input_verified=true`、页面含完整 query、协会名、
人员名及结果页固定栏目。普通审计只写 `input_verified` 布尔值和 locator 类型，不新增 query 明文字段。
首次回读不匹配且插件仍可信前台时，只允许再执行一次三键重聚焦并立即重复粘贴/回读；总计两次
仍不匹配则失败，前台不可信则立即 `INPUT_FOCUS_LOST`。

若输入失败后完整插件顶层关闭及主窗口恢复成功，该错误是当前人员的明确技术失败，可继续下一人员；
若关闭或恢复不确定，则由具体 cleanup 错误码覆盖并按会话致命错误熔断。此前 20 家真机结果没有
建立输入框回读证据，所有 `not_found`、卡片定位结果和成功率统计均作废，必须在本门禁下重新验证。
`-VerifyInputOnly` 提供最小真机诊断：仅限已执行的 `search/collect`，复用同一键盘聚焦和回读
门禁但禁止 Enter，随后按统一顶层关闭契约恢复主窗口，不进入 ready/list/card。
输入失败保留 `SEARCH_INPUT_LOCATOR_FAILED`、`SEARCH_INPUT_CLICK_STRUCTURE_INVALID`、
`SEARCH_INPUT_READBACK_MISMATCH`、`SEARCH_INPUT_FINAL_STRUCTURE_INVALID` 或 `INPUT_FOCUS_LOST`；
failure artifact 只保存 locator/post-click/readback/final 四个布尔状态，不保存输入内容。

详情遍历内部使用一次受前台可信 HWND guard 保护的 `Alt+Left` 返回结果列表，非终止路径禁止
`Ctrl+W`。若后退后失焦，只允许恢复原可信插件一次；随后在有界截止时间内复制并复验原结果页，
截止仍无法证明恢复则 `RECOVERY_FAILED`，最终清理只执行统一顶层 `WM_CLOSE`。

列表 `visual_verify` 截图临时文件必须使用 OCR adapter 白名单内的 `wechat-ocr-` 前缀；调用端统一为
`wechat-ocr-list-verify-*`。外部 OCR/判定进程非零退出只返回无内容的 `JUDGE_PROCESS_FAILED`，
不得把 stderr、临时路径或页面正文写入错误结果。

默认卡片 band locator 依赖卡片与背景形成连续像素亮度带，不适用于 693px 窄微信 Qt 窗口中的浅色
文字流列表。进入卡片定位前必须始终运行整页证据 Judge：配置外部 Judge 时执行严格模型契约，未配置
时仍执行内置的同一行姓名—手机号精确绑定。整页已有有效绑定则直接返回 `found`，禁止为了打开详情
而猜测卡片坐标；只有整页没有绑定证据时才进入有限的视觉卡片定位。

### 2026-08-03：纯 RPA 连续焦点流程探针

`flow_probe` 是与业务采集隔离的真机诊断模式，读取 1～20 条严格 JSON（仅
`association_name/person_name`，负责人可为空）。同一 PowerShell 进程逐轮执行：主窗口三键打开搜一搜、
粘贴并精确回读、提交、复制完整列表到内存但不解析和落盘、在受限内容区相对点点击、以页面哈希变化
及同一可信插件 HWND 证明详情打开、连续两次 `Ctrl+W` 关闭详情和结果页，保留搜索主页作为下一轮
复用基准。

首结果点限制在 `x=0.28..0.68`、`y=0.18..0.70`，默认 `0.30/0.30`；页面哈希不变即
`FLOW_DETAIL_NOT_OPENED`，禁止继续。两次 `Ctrl+W` 关键区间只允许等待，不截图、不激活窗口、
不读剪贴板且不注入其他键鼠。输出只含轮次、可信插件 HWND、步骤布尔值、阶段和稳定错误码；不含
query、列表正文或手机号。失败时若插件仍存在，仍以可信 HWND `WM_CLOSE` 兜底并恢复主窗口。
搜索提交后的列表复制采用最多 12 次、间隔 500ms 的有界轮询（约 7 秒，含剪贴板读取等待）；每次只
校验同一可信插件 HWND、清空剪贴板、发送 `Ctrl+A/C`、读取长度并等待。非空且达到合理最小长度即
通过；预算耗尽返回 `FLOW_LIST_COPY_FAILED`，正文仍只存在内存且不解析、不落盘。

`flow_probe` 只诊断焦点流程，不做生产级页面归属判定。打开、输入、列表复制、点击、详情截图和关闭
各阶段都重新读取当前前台窗口：完整核验为微信主窗口时说明焦点仍在微信应用，但需要插件输入的阶段
返回 `FLOW_PLUGIN_NOT_FOREGROUND`；完整核验为可信路径下的 `WeChatAppEx.exe / Chrome_WidgetWin_0`
时，直接以当前前台插件 HWND 作为该阶段目标，并重新绑定 guard、窗口矩形和截图。外部、伪造或无法
识别的前台窗口才返回 `INPUT_FOCUS_LOST`。该模式不要求 HWND 新建，不做 query、协会、人员、栏目或
正文语义解析，也不保存复制正文。

每轮从完整可信微信主窗口或当前前台可信搜索插件执行 `Ctrl+F → Down → Enter`。三键只绑定动作前
的当前可信基准 HWND，不激活窗口。第一次等待后若前台完整身份仍是可信主窗口，只进行该只读判断和
短暂等待，再执行一次相同三键；最多两次，不点击、不激活。
任一次出现可信插件即继续；第二次仍为主窗口返回 `FLOW_PLUGIN_NOT_FOREGROUND`，外部或伪造窗口
立即返回 `INPUT_FOCUS_LOST`。输出以安全整数 `open_attempts` 记录实际尝试次数。

所有键盘、剪贴板和鼠标输入都只发送给动作当下的前台可信插件；不调用窗口激活，不操作后台或外部
窗口。两次 `Ctrl+W` 发送前只完整验证一次当前前台可信插件；进入关闭序列后不再使用旧详情 HWND
guard，也不在两次之间读取前台。在无人干扰的诊断前提下直接发送两次关闭键，中间只有计数、阶段
赋值和等待；第二次完成后才读取前台完整身份。严格可信搜索插件或微信主窗口均标记
`base_ready=true`，并以安全枚举 `base_kind=plugin/main` 输出；外部窗口返回 `INPUT_FOCUS_LOST`。
失败清理也只允许向
当前前台、且本轮已经见过的可信插件发送定向关闭消息，不激活主窗口或后台插件。
输出保留 `process_id`、安全整数 `close_count=2`，并以 `input/list/click/detail_hwnd_changed` 布尔值记录阶段间 HWND 是否变化；
不包含完整路径、标题、query、列表正文或手机号。

搜索完成后的点击门禁同时要求：剪贴板仍是本次 query 的结果页；结果内容区域截图 OCR 同时出现协会名
和人员名；卡片坐标位于固定比例内容区。定位算法使用相对比例和背景差异，支持深浅主题及 DPI 缩放。
任何一项不能证明时禁止点击。点击之后失焦仍禁止恢复或重复点击。

列表不含目标人员时，定义为“无可操作候选”，形成 `inconclusive` 业务结果，不计为
`CARD_LOCATE_FAILED`。CF_HTML 没有详情链接时仍继续视觉卡片定位，避免微信版本差异造成假阴性。
详情 OCR 固定裁剪中央内容区，最多捕获两次（最多滚动一次），且 OCR 汇总必须同时含协会名与目标
人员名。真正视觉定位失败的加密 artifact 仅保存窗口宽高、内容区比例及截图 SHA-256，不保存截图
或明文手机号。

批处理结果显式携带 `aborted` 和 `abort_error_code`，CLI 不再仅靠请求数与处理行数推断熔断。
因此单个协会熔断或最后一个协会熔断，即使没有“缺失行”，也必须导出包含当前行的 Excel，同时返回
`ok=false`、`business_status=failed`、退出码 2；`processed_count` 保留已导出行数，`aborted_count`
包含当前熔断行及其后未处理项，不能把当前行的 `partial` 误报为整批成功。

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
`VerifiedOfficialPage[]`。路径队列按末级目标的业务价值做全局排序，因此从“协会介绍”
二级菜单中新发现的领导或组织路径，可以抢占首页尚未处理的会员等低价值路径；同等
价值时优先较短路径。collector 完成同域验证后，普通 HTTP(S) href 直接在当前采集页
导航，避免 `target=_blank` 把内容打开到未被 driver 跟踪的 popup；JavaScript、锚点
和无普通 href 的目标仍走点击路径。单个页面最多保留前 200 个可见导航候选，
Playwright 在页面内先全量映射并过滤可见项，再截取前200项，同时保留原 locator
下标供点击使用，避免隐藏菜单占满预算或过滤后 `nth` 漂移。批量读取可见性、文本和
href，避免逐元素多次跨进程调用；再结合总点击
预算、90 秒总采集截止时间和20秒单路径截止时间限制异常 DOM、失效页面及路径重放的
成本。Playwright wrapper 在 browser context 层
于请求发出前拦截当前页及 popup 的跨域顶层 frame 导航；第三方图片、脚本和 iframe
等子资源不按顶层导航处理。即使文本同时含
“会员”等高价值词，登录、注册、退出、删除、提交、报名、支付和下载类动作也禁止点击。

Playwright 入口 `collect_official_pages_with_playwright(...)` 的 `headless` 必须由
调用方显式传入严格布尔值，`None`、数字和字符串均拒绝；CAAPA/CRRA 等真机风控验收
使用 `headless=false`。Codex 或人工手动
点击栏目后再把正文交给提取器，只能用于调查，不能计为自动采集验收通过。
`navigation_timeout_ms` 在 wrapper 和 driver 两层均要求非布尔正整数。入口页在
`domcontentloaded` 后按不超过 250ms 的固定间隔等待 SPA 正文和导航出现；不足 250ms
的预算不额外 sleep。老站若等待 `domcontentloaded` 超时，但当前 URL 仍是请求页且
正文已经可读，则保留该状态并继续现有轮询；正文为空、状态不可读或 DNS、证书、
连接拒绝等非超时错误仍按原异常失败。点击后仍先即时读取一次状态。BrowserContext route 使用 Playwright
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

## 12. 2026-08-05 微信窗口编排重构

本阶段只重构微信搜一搜 RPA；官网采集和模型解析代码保持不动，解析契约调整留待用户确认本阶段结果后再实施。

微信 RPA 不再让 `flow_probe` 与正式 `collect` 各自维护 HWND 规则。窗口会话统一表达为：启动前插件集合、主窗口、当前列表 HWND、本会话已接纳 HWND。只有完整通过微信插件身份校验、且不属于启动前集合的前台窗口，才能在“打开搜索”或“受保护点击详情”这两个预期转换点被接纳。同 HWND 详情通过后退返回；独立详情 HWND 使用已经由探针验证的关闭详情动作，随后只允许返回本会话列表 HWND。外部窗口、启动前旧插件和非预期转换均立即失败。终态清理只处理本会话接纳的窗口，逐个记录关闭结果；任一窗口关闭失败时 `session_closed` 不得为 true。

### 12.1 正式 collect 超时与清理预算

Python Provider 对单次正式微信 RPA 提供 600 秒外层预算。PowerShell `search/collect` 从执行开始建立 9 分钟业务截止时间，最后 1 分钟只作为统一 `finally`、窗口清理和结果回传余量。每次列表/详情循环都检查截止时间；启动最长 60 秒的 OCR 或 LLM Judge 子进程前必须确认至少还剩完整 60 秒，避免单次阻塞越过业务截止。业务预算耗尽使用稳定错误码 `WECHAT_WORK_TIMEOUT`，仍进入既有统一清理；非零退出不得授予下一条 handoff。`flow_probe` 不使用该正式业务预算。

### 12.2 独立详情返回证据的终态降级

独立详情关闭后，窗口状态机若已精确回到本会话登记的列表 HWND，则“详情窗口关闭和 HWND 恢复”已经成立。列表正文复制仍在有界时间内复验；若正文因页面瞬态、焦点随后切换到主窗口等原因暂不可验证，不得继续点击或滚动，但也不得把已完成的独立详情关闭误报为 `RECOVERY_FAILED`。此时以 `inconclusive` 安全结束当前采集并进入统一搜一搜窗口清理。只有未回到登记列表 HWND、落到外部/其他插件，或同 HWND 详情后退却无法重新证明列表正文时，继续返回真正的 `RECOVERY_FAILED`。

若独立详情 `Ctrl+W` 后前台直接落到身份完整匹配的 Qt 微信主窗口，不把主窗口当作列表，也不接纳任何新 HWND。程序只允许检查本会话已经登记的 ListHwnd 是否仍存在且身份仍严格匹配；通过后主动激活该精确 ListHwnd，并再次复验前台句柄相等。ListHwnd 消失、身份变化、激活失败、落到外部或其他插件时仍为 `RECOVERY_FAILED`。该恢复仅适用于独立详情；同 HWND 后退路径不得使用主窗口激活兜底。

### 12.3 Phase 1E3：详情内容稳定等待

正式 `collect` 仅在点击后截图哈希已经变化、且详情插件窗口身份确认完成后，固定等待 5000ms，再开始剪贴板复制、OCR 与模型判断。每个成功打开的详情只等待一次；点击前不增加等待。等待前后都复验当前详情前台守卫，焦点丢失继续失败并停止输入。`flow_probe` 只验证窗口和焦点转换，不读取详情内容，因此保持原时序不变。

### 12.4 Phase 1E4：内容结果与窗口安全解耦

详情复制为空、过短、仍是结果列表或其他不可用内容时，该候选记为不可读，不调用 OCR 或模型内容判断；先按窗口状态机关闭/返回，再继续下一个候选。点击后页面截图未变化表示该候选未打开，同样只计入候选级失败并继续，不得中止当前查询或阻断后续批次。全部候选都不可读时返回 `ok=true,status=inconclusive,session_closed=true`。

详情恢复只使用窗口安全证据，不再复制结果页正文作为恢复门禁。独立详情 `Ctrl+W` 后若原 `ListHwnd` 仍存在，只重新校验并激活该 owned 列表；若直接落到可信微信主窗口且原列表已消失，则逐一确认本会话所有 owned 插件 HWND 都已不存在，确认后视为窗口栈自然关闭，以 `inconclusive` 结束当前查询且不再点击。外部前台、owned 窗口残留、身份不可信、无法关闭或 cleanup 失败仍是会话级失败。正文内容的正确与否不参与窗口安全判断，也不增加额外真实性门禁。

### 12.5 Phase 1E5：搜索结果只走文本模型

正式 `search/collect` 的搜索结果相关性判断以剪贴板复制的列表文字为唯一模型输入，payload 只包含 `association_name/person_name/text`，不得包含截图、base64、图片路径或图片对象。删除原 `visual_verify` 路径：不再截取列表区域、不再生成 `wechat-ocr-list-verify-*` 临时图片、不再调用 OCR adapter 判断当前搜索结果，因此也不会出现 `stage=visual_verify` 的 `JUDGE_TIMEOUT`。

截图仍保留两种纯本地用途，均不发送模型：一是点击前后 bitmap 哈希用于确定页面是否发生变化；二是 `Find-DarkThemeCardBands` 的本地像素分析用于卡片坐标定位。详情图片/PDF OCR 属于详情正文补充链路，不是搜索结果相关性判断，并继续受 Phase 1E4 的可读复制门禁约束。

列表文字持续复制不到时，或外部文本 Judge 超时、进程异常、返回协议异常时，当前 query 返回 `ok=true,status=inconclusive`，完成严格 cleanup 后携带 `session_closed=true`，允许批处理继续交接下一 query。只有前台身份、owned HWND、关闭或 cleanup 无法证明安全时才保持会话级失败。

### 12.6 Phase 1E8：原生快捷键进入“文章”分类

真实微信搜一搜验证表明，初始搜索结果稳定后发送一次 `Ctrl+Tab` 会直接进入“文章”分类。正式 `search/collect` 因此只采用这一原生快捷键，不做 UIA、像素文字带、固定坐标、OCR 或模型分类定位，也不通过截图脑补选中态。`flow_probe` 暂不接入该行为。

首次列表文字通过 ready 门禁后，程序检查业务预算至少保留 62 秒，并在严格 plugin foreground guard 下发送且仅发送一次 `Ctrl+Tab`；固定等待 2 秒，等待后再次验证同一可信插件前台。快捷键发送异常或前台丢失继续按窗口安全错误处理，不重试 `Ctrl+Tab`。

切换后立即丢弃切换前的列表文字和 HTML，重新复制文章分类列表。只有重新复制且通过结果 ready 门禁的文字才能写入列表 artifact、交给 Phase 1E5 纯文本 Judge，并作为详情判断的列表基准；`search` 返回的同样是该文章分类文本。viewport、卡片 band、locator 使用和页面 hash 均在切换后重新建立，不复用切换前状态。切换后文字为空或不可用、文本 Judge 异常时，当前 query 返回 `inconclusive + cleanup`，清理成功后允许下一 query；窗口身份或清理失败仍为会话级错误。
