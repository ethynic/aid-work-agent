# Agent 网络搜索增强设计

> 日期：2026-10-13；状态：整体设计草案；智谱单源接入已完成开发，见第 8 节。
> 用户优先级：中文网站、企业资料、最新信息覆盖。
> 依据：[源码调研](../research/agent-web-search-harness-research.md)；执行记录：[开发计划](../plans/plan-agent-web-search-enhancement.md)。

## 1. 目标与实施边界

提升公共互联网中文检索的召回、时效性和证据质量，同时保留既有 `web_search` 调用兼容。

第一批建设通用搜索服务与结构化 API adapter，中文候选为百度、智谱，保留 Tavily；用基线评测选择正式默认源。第二批补正文读取和企业检索策略。内部知识库检索、受限平台采集和客户机执行继续走既有授权路径。

首期全部使用服务器侧公共网络工具，不增加 Desktop / Runtime 设备协议或新的 Agent loop。已核对[共同契约](runner-desktop-runtime-integration-contract.md)：若后续需要客户机网络或浏览器读取，复用既有固定设备、binding 和执行工具，并在共同接口变化前登记检查点。

## 2. 结构与职责

以下为包含批量、融合和正文读取的整体目标结构；已实现的单源provider注册表与适配接口见第9节，其余规划不等于当前已交付。

```text
AgentEngine / 既有子智能体
  → web_search / web_fetch（BaseTool，沿用 Catalog 与角色白名单）
    → src/services/web_search/（拟新增）
      → 参数规范化、能力检查、请求级计划
      → 有界并发、超时/取消、部分结果与失败分类
      → providers/{tavily,baidu,zhipu}.py（拟新增）
      → 去重、排序、来源与完整性投影
```

共享服务与工具实例不持有租户/用户身份；从 `ToolExecutionContext` 读取请求上下文。网络连接池可以复用，请求计划、凭据引用、取消状态和结果只属于本次调用。

优先复用项目现有 httpx/aiohttp 能力。确需 SDK 时核对已安装版本与 API 参数，不把动态最新 SDK 文档当成当前依赖事实。配置改动同时维护 `SearchToolConfig`、`configs/config.yaml` 和环境变量加载；配置、结果、日志只保留凭据引用与脱敏诊断。

## 3. 输入兼容与能力差异

保留工具名及既有字段；拟新增以下可选参数：

| 字段 | 语义 |
|---|---|
| `queries` | 1–4 个非空查询作为首期默认上限；与 keyword 二选一，入口校验后并发执行 |
| `language` | 检索意图语言，支持中文优先及必要的别名/英文补充，不再强制所有查询同语言 |
| `time_range` | day/week/month/year，按供应商能力映射 |
| `start_date` / `end_date` | 明确日期区间，与 time_range 互斥；入口统一为起始包含、结束不包含，并按供应商日期/时区规则转换 |
| `mode` | quick/comprehensive；决定一个主源或主源+一个互补源，供应商选择由服务配置控制 |

`keyword` 的旧调用维持有效。`max_results` 为合并后的模型可见结果上限，显式有效值优先于旧 `limit`，两者未提供或为 None 时使用配置。检索候选数量与最终展示数量分开，允许适量扩大候选池后去重。

各 provider 声明支持的域名过滤、排除域名、精确日期、相对时间、主题和搜索深度；调用前检查。不得静默丢失用户约束或把不支持的精确日期降级为不限时间。相对时间无法直接映射时，仅在语义等价且可验证时转换；否则返回能力错误或选择满足约束的已配置源。

`search_depth` / `topic` 对旧 Tavily 调用保留；其他引擎不能将“更多结果”冒充 advanced 抓取，返回实际生效的能力信息。明确日期查询遇到缺少发布时间的结果时标记未知，不声称已满足条件。

## 4. 检索与融合策略

- **quick**：使用配置中选定的主源；零结果或可重试失败时，按明确的后备列表调用一个源。鉴权/参数错误不重复请求相同端点；取消后不发新请求。
- **comprehensive**：主源和一个互补源并发；查询批次有请求级总上限和进程级并发限制，避免 4 查询 × 全部引擎无限放大。
- **企业资料**：优先按全称、品牌/简称、所在地或业务领域拆分查询；找到候选官网后做限定域名补查并读取关键页面。同名候选无法消歧时保留不确定性，不自动认定官网。
- **最新信息**：显式时间过滤和日期核验；区分发布日期、事件日期和抓取时间，重复转载不能作为多份独立佐证。
- **排序**：先按各引擎内部排名融合，再加实体匹配、已核实官网及日期匹配条件；首期可使用 RRF。原始 score 保留为 provider 私有诊断，不作跨源绝对比较。
- **去重**：去 fragment 和已确认的追踪参数；保留会改变页面身份的 query 参数。合并相同 URL 的命中归属，内容相似与转载去重后续以样本验证。

查询扩展由原 Agent/既有研究子智能体提出，不为每次普通搜索额外调用模型。首期不新增 deep_research 控制工具；复杂研究复用当前多轮工具调用。

## 5. 返回结果与完整性

保留顶层 `success/results/count/message`，`answer` 仍可选。每条结果保留 `title/url/content/score` 兼容字段；没有供应商 score 时采用兼容缺省并明确其无跨源排序含义。拟新增 `source_id`、`provider`、`matched_queries`、`published_at`、`date_kind`、`retrieved_at`、`content_kind`。

顶层拟新增 `complete`、`partial_failures`、`truncated`、`queries_executed`、`providers_used`、`applied_filters`、`usage`。一部分请求成功时保留结果且 `complete=false`；全部失败时 `success=false`；无匹配且请求完成与 provider 异常分别表示。缺少原生来源的辅助模型回答不能冒充搜索结果。

批次内使用确定的调用局部 source_id；跨调用引用采用调用标识与 URL 共同定位，不能只用同名序号。answer 明确是供应商摘要，事实陈述以 URL/摘录支撑。工具使用指南要求来源引用和外部文本作为数据处理，不能将网页指令写回系统策略。

结果长度限制用于模型上下文管理，截断明确标注。任务费用通过实际 API/模型用量和调用准入控制；不以压低 `max_tokens` 控制费用。

## 6. 正文读取

新增普通 `web_fetch` 工具，输入 URL，首期返回可引用 Markdown 正文、最终 URL、标题、读取时间、内容类型及截断标志；不默认再用模型摘要，保留可核查原文。

优先 HTTP 读取和既有 HTML 解析能力；动态页面仍交给既有浏览器工具，返回明确原因及后续建议。搜索摘要、已读正文、读取失败必须可区分。

服务器抓取需要连接前及每次重定向的公网目标校验，并以连接地址绑定或既有出网层阻断 DNS 重绑定；只检查 URL 字面量不能宣称完成 SSRF 防护。限制大小、时限及内容类型；不携带用户登录 Cookie 或 Authorization 到公网抓取。正文缓存按租户和策略分区、有 TTL 与容量上限；时效任务缩短 TTL，不能跨租户复用私人查询与数据。

## 7. 验证与发布门槛

以[计划](../plans/plan-agent-web-search-enhancement.md)的阶段验收执行。涉及工具装配、请求取消、凭据和租户边界的代码开发按高风险流程安排独立测试和 CodeReview；本文是文档草案，不代表已完成这些验证。

默认保持旧 Tavily 路径，新增源未配置时应可启动。完成能力及质量评测后，再启用选定的中文主源和融合策略；通过配置可退回旧路径。部署须由用户当场明确指定环境。

## 8. 本轮用户指定：先接智谱（2026-10-13）

当时用户明确指定先接智谱并复用 LLM 密钥，本仓库 YAML 曾改为智谱 Pro，历史缺省 provider 的配置仍走 Tavily；2026-10-14 用户要求两家国内源都暂不启用，当前默认已恢复 Tavily，见第9节。不表示中文质量评测已经完成。

本轮只开发智谱独立 API、请求级读取现有 LLM key、时间筛选与旧工具调用兼容。官方 endpoint 固定，不把自定义 LLM/Coding Plan base_url 拼成搜索地址。首个有效 key 为本轮最小接入，不新增共享并发池或自动轮换方案。

该接入已完成独立测试和 CR，并使用现有 key 成功进行 Pro 真实 API 冒烟；未部署，尚未进行规模中文召回质量评测。后续百度/智谱两组小样本对照见调研第 9 节；详细开发验证边界见计划 Phase 1A。

多域名与排除域名采用明确的候选后过滤，不静默丢失限制。智谱原生独立检索没有模型生成摘要，include_answer 不增加模型调用，search_depth 仅控制其摘要详尽程度并在工具说明中区分；news 专用主题不能假装受支持。后续完整服务与融合按第 2–7 节继续实施，具体完成记录见计划。

## 9. 多 provider 结构与默认关闭（2026-10-14）

当前实现统一为models.SearchRequest和SearchProvider.search，显式registry.PROVIDER_FACTORIES注册各适配器，providers/tavily.py、providers/zhipu.py、providers/baidu.py各自处理凭据、能力约束、供应商请求与结果解析。web_search不维护供应商条件分支；每次仅实例化选中的provider，凭据和状态不跨请求共享。未来新增provider增加适配器、注册项、配置枚举及定向测试即可。

默认provider:tavily；百度与智谱不自动启用、不因主源缺密钥或失败而调用。只有操作者显式改配置/SEARCH_PROVIDER才选对应源，Agent参数不暴露provider。已接入不代表已部署或已启用，不实现第4节拟议的多源融合/后备链。

百度使用[官方V2独立搜索](https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy)，固定HTTPS端点、standard版本、web-only，解析references中的snippet/content、url、date、website和rerank_score。域名限定原生match.site（上限20），排除使用block_websites，并按域名边界严格后过滤。week/month/year使用search_recency_filter；官方page_time限定固定date-math套餐，无法表达精确最近24小时，day请求前明确拒绝，不扩大范围、不另调其他源。日期保留供应商原值，不自动当作已核实原始发布日期；摘要不表示完整正文或模型答案。接线和HTTP契约以隔离MockTransport验证，不实际启用百度进行效果评测。

## 首批通用组件提交边界（2026-10-23）

用户批准DeepSeek独立搜索并设默认、HTML/文字PDF读取、有界显式远端回退、会话来源保存与Read分页，以及通用模型截断修复、主体默认50轮和Pro输出容量配置。不改Device/wire或浏览器工具。

Search basic返回摘要，advanced请求正文，实际类型不升格；返回完整来源元数据及有限预览，正文以会话隔离文件和SHA回读。Read仅研究正文整行分页，普通文件行为保留。来源是外部数据，不是执行指令。

Fetch提供auto/native/remote，缺省auto：原生成功立即返回，仅网络/TLS/连接/响应超时类错误尝试一次配置选定的远端（缺省Tavily Extract，可选DeepSeek Fetch）；不按Key自动换源。公网DNS/逐跳固定IP与TLS验证、HTML5MiB、PDF缺省25MiB/可配1..50MiB、100页、100万字符、总90秒/可配至180秒、可终止解析子进程保持有界。远端固定API、响应6MiB、压缩读前拒绝，保留导航及读取尝试；provider_fetch不冒充本机直读，最终URL/源站时间未知时明确标记。

三个LLM adapter保留length；引擎不执行不完整输出里的工具，一次恢复仍连续失败则明确失败，取消优先；耗尽时给出未完成说明。MASTER无profile时默认50，其他模式及显式profile沿原限额。Pro补模型能力表393216，避免缺表16K；最大输出是生成容量，不替代实际用量及准入预算。

本批不包含research_plan/research_validate、CSV/客户资格/官网身份规则、研究交付绑定、全局完成门禁或业务进度提示；不得因用户搜索并导出普通CSV而强制研究校验。相关原型与完整Skill实验保留本地，不作为本批生产能力。普通生成MD仍可能受通用回读截断，Search召回质量也需组件层继续增强。


兼容契约：会话投影保留既有search_engine/response_time。Tavily answer保留为供应商生成摘要，明确answer_kind，预览最多700字符；超出部分完整保存到同一会话文件，附SHA并由Read分页，不能作为新增source或原始全文证据。


## Read UTF-8兼容修复（2026-10-26）

完整文件先严格UTF-8读取，成功且无NUL时保留原文，不让chardet的UTF-7猜测改写已验证UTF-8；不能只验证8192字节样本。含NUL时保留既有无BOM UTF-16/32兼容识别，其余探测及Office读取保持。本批不修改Agent主循环、控制工具或提示词装配。

## 公开PDF显式页段读取（2026-10-26）

web_fetch新增可选pdf_start_page（1起）/pdf_page_count（1..100，显式分页默认20）。不传参数仍沿原默认整篇读取与100页上限，超过时给出总页数及分页提示；保留原页码，返回实际起止/总页数/pdf_complete，元数据随会话来源保存。显式页段只走原生解析，HTML拒绝页段，远端不伪装指定PDF页。下载大小、SSRF/DNS/TLS、超时、解析隔离与取消边界保持。工具指南仅补分页说明，不包含新研究流程的通用行为提示。

只含Read/PDF工具变更的隔离候选在现有HEAD上通过323项相关回归（0失败/跳过，17.52秒），包含工具装配、原Agent引擎/恢复及清理。research_progress、结束复核、域名出处门禁和通用搜索策略不在本批。
