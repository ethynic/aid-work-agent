# Agent 网络搜索增强调研：ZCode 与 DeepSeek Harness

> 日期：2026-10-13。用户优先级：中文网站、企业资料和最新信息搜不全。
> 方法：官方仓库固定 commit 源码阅读、官方 API 文档核对、本项目代码审计；未运行两款上游产品。后续已完成智谱接入冒烟、百度/智谱两组 API 对照（第 9 节）及金牌家居联系人查询与详情核验（第 10 节），未进行规模评测、压测或实际账单费用对比。
> 关联：[设计](../system/agent-web-search-enhancement-design.md)、[开发计划](../plans/plan-agent-web-search-enhancement.md)。

## 1. 结论与证据边界

建议把现有 Tavily 单源包装升级为统一搜索服务，首批接入百度 AI Search、智谱 Web Search，保留 Tavily，补齐批量查询、时间过滤、跨源去重和官网正文核验。

事实：ZCode 使用模型原生联网搜索；DeepSeek Harness 通过提供商接口支持 DeepSeek、Exa、Perplexity，并有独立网页读取工具。事实：本项目已有百度搜索 skill，但没有并入 `web_search` 的统一接口。推断：引入国内搜索源和查询拆分，有望补足中文企业及长尾网站覆盖。第 9 节华为资料样本百度更好，第 10 节金牌家居董秘联系方式和最新报告样本智谱更好；尚未完成规模评测，不能推导全面排名或提升倍数。

研究范围是公共互联网信息。企业内部知识库、业务数据库和登录后信息继续使用既有受权工具；公网搜索增强不能替代企业内部检索，也不能保证召回未收录或受限平台的内容。

## 2. ZCode 实际实现

仓库：[zai-org/ZCode](https://github.com/zai-org/ZCode)，固定 commit `aac4755666d09fdcd70272fcf063c077a639015f`。

### 2.1 搜索后端

- 对模型暴露 `WebSearch`，handler 使用当前 `context.model` 发起独立的辅助模型调用，内部只提供一个原生 `web_search` 工具。
- 要求模型声明 `supportsNativeWebSearch`。本次读取的 adapter 将原生工具编码为 `anthropic.tools.webSearch_20260209`，不支持的 provider 路径明确报错。
- BigModel Anthropic 兼容端点有专门兼容处理：采用流式调用收集结果；不强制指定搜索工具，避免端点拒绝 named tool choice。
- 支持查询、域名允许/排除条件、内部搜索次数上限；输出结构化结果、来源、可选摘要、耗时和用量。

因此，准确说法是“封装模型提供商原生联网能力，适配 BigModel 兼容端点”，不是“已确认直接调用智谱独立 Web Search API”。源码也没有告诉我们该端点后台究竟使用百度、搜狗还是其他索引。

源码证据：[handler](https://github.com/zai-org/ZCode/blob/aac4755666d09fdcd70272fcf063c077a639015f/apps/zcode-cli/packages/core/src/tool/handlers/websearch.ts)、[工具契约](https://github.com/zai-org/ZCode/blob/aac4755666d09fdcd70272fcf063c077a639015f/apps/zcode-cli/packages/contracts/src/tools/websearch.ts)、[adapter](https://github.com/zai-org/ZCode/blob/aac4755666d09fdcd70272fcf063c077a639015f/apps/zcode-cli/packages/adapters/src/model/tool-transform.ts)。

### 2.2 网页读取与可借鉴点

`WebFetch` 独立抓取 URL，转换页面内容，再按调用者的 prompt 交给辅助模型处理；缓存抓取内容 15 分钟，跨主机重定向返回新地址，提供完整产物引用和截断信息。借鉴点是搜索与正文核验分工、结构化来源和可追踪用量，不能把摘要当作完整原文。

边界：搜索描述中残留 `US-only` 文案，不能据此判断 BigModel 的真实地域覆盖。该版本网页出网检查明确移除了普通域名 DNS 预检；不能将其当作服务端完整 SSRF 防护照搬。

源码：[WebFetch](https://github.com/zai-org/ZCode/blob/aac4755666d09fdcd70272fcf063c077a639015f/apps/zcode-cli/packages/core/src/tool/handlers/webfetch.ts)、[出网检查](https://github.com/zai-org/ZCode/blob/aac4755666d09fdcd70272fcf063c077a639015f/apps/zcode-cli/packages/core/src/tool/handlers/webfetch-egress-guard.ts)。

## 3. DeepSeek Harness 实际实现

仓库：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，固定 commit `d743267388641bc76f17c45ce8b4c231aed1d32c`。

| 能力 | 实际调用与行为 |
|---|---|
| DeepSeek 搜索 | Anthropic 兼容 `/messages` + `web_search_20250305`，默认辅助模型 `deepseek-v4-flash`；解析原生搜索结果块和引用摘录，没有结果块时报错 |
| Exa 搜索 | Exa `/search`，支持 auto/keyword/neural 配置，读取结果及 highlights |
| Perplexity 搜索 | `/chat/completions`，默认 `sonar`，处理回答与来源 |
| 工具层 | `web_search` 默认接受 1–4 个查询，并发执行；URL 去重、轮流合并各查询结果，避免单个查询占满结果上限 |
| 网页读取 | `web_fetch` 经独立 HTTP provider 获取页面，工具层转换 HTML 为 Markdown，返回截断标志 |

统一 `ctx.web` 接口将 provider 与模型工具 schema 分开。搜索 provider 是显式选择：未指定且有多个可用 provider 时报告歧义，不按注册顺序自动挑选。因此“支持多后端”不等于“自动多引擎融合”。

批量搜索中一个查询失败会取消同批其他查询，等待全部结束后抛错。本项目应保留可用结果并报告部分失败，这是面向资料采集的本项目设计选择，不是对该实现的直接复制。

源码：[服务选择](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/web/src/index.ts)、[批量工具](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/tool-web/src/search.ts)、[DeepSeek provider](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/web-search-deepseek/src/provider.ts)、[Exa provider](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/web-search-exa/src/provider.ts)、[Perplexity provider](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/web-search-perplexity/src/provider.ts)、[读取工具](https://github.com/deepseek-ai/deepseek-harness/blob/d743267388641bc76f17c45ce8b4c231aed1d32c/packages/web/tool-web/src/fetch.ts)。

## 4. 本项目现状与缺口

审计位置：`src/tools/search/search_tool.py`、`src/config/settings.py:SearchToolConfig`、`src/tools/executor.py`、`src/skills/baidu-search-1.1.3/`、`subagents/competitor-research/SUBAGENT.md`、`tests/unit/tools/test_search_tool.py`、`tests/e2e/test_search_real.py`。

| 已确认事实 | 影响 |
|---|---|
| `web_search` 仅调用 Tavily，默认 5 条、basic、带供应商摘要 | 没有国内源互补能力，检索覆盖受单源限制 |
| 每次只接受 `keyword`，且描述强制与用户语言一致 | 不利于一次检索全称、简称、品牌及中英文别名 |
| 已有 domain/topic/depth 参数，缺少时间范围参数 | 最新信息无法通过该工具显式限制时间 |
| 任意 advanced 异常都重试 basic，重试不带 include/exclude_domains | 重试会改变原本的检索范围；鉴权错误也会无意义重试 |
| schema 有 `limit`，execute 不读取；若显式传 None，默认配置不被采用 | 参数语义不一致；需要修复并验证历史调用兼容 |
| 使用同步 Tavily SDK + executor，工具内未传显式请求超时 | 工具内部缺少可控的 HTTP 生命周期；外层超时不能证明线程中请求已停止 |
| 结果保留 URL，但未保留发布日、来源引擎、查询归属和完整性说明 | 不利于时效判断、去重、引用追踪和评测 |
| 百度 skill 调用 AI Search API，有 freshness，但删除 snippet，requests.post 未设置 timeout | 已有国内源能力没有成为稳定公共工具，结果摘要被丢弃 |
| 竞品子智能体配置百度 skill，已有多轮检索与 browser_get_content 指南 | 可复用现有研究流程，不需要另建 Agent loop |
| 通用搜索测试主要验证定义和单次 mock 成功，真实测试验证返回结果 | 未衡量中文企业召回、信息新鲜度及来源准确性 |

以上是代码层原因候选，尚未复现用户具体失败查询。不能据此断言所有搜索问题都由 Tavily 引起；工具参数暴露和 Agent 检索策略同样需要改进。

## 5. 供应商候选与优先级

1. **百度 AI Search：首批中文召回候选。** 现有 skill 可提供接入经验；正式 adapter 使用官方 V2 结构化搜索，保留摘要、链接、来源及时间信息，支持站点与时间过滤。文档列出网页 top_k 上限 50；不同模态、接口不可混用。来源：[百度官方搜索 API](https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy)。
2. **智谱 Web Search：首批互补候选。** 独立 API 返回结构化结果，并支持自研及搜狗、夸克引擎；选择引擎时检查其实际支持的数量、域名和时间条件。使用搜索 API 不要求主 Agent 换成 GLM。来源：[官方能力说明](https://docs.bigmodel.cn/cn/guide/tools/web-search)、[API schema](https://docs.bigmodel.cn/api-reference/工具-api/网络搜索.md)。
3. **Tavily：保留，先充分利用现有能力。** 官方 API 已有 time_range/start_date/end_date 等条件；目前工具未暴露。其搜索 content 不能自动视作完整网页。来源：[Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)。
4. **Exa / Perplexity 独立 Search API：结构化检索候选；DeepSeek 原生搜索 / Perplexity Sonar：辅助模型搜索候选。** 两类产品分开评测与计费，独立 Search API 不要求模型生成回答。2026-10-13 补查确认 Perplexity 已提供独立 Search API，不能把 Harness 的 Sonar adapter 当作其唯一接入方式；更多候选与价格见第 8 节。

建议先比较百度与智谱独立 API，冻结查询集后选择中文主源及互补引擎。融合不能用任意跨供应商 score 直接排序，尤其不能把共享底层索引的两个 API 当成完全独立证据。

## 6. 如何判定增强有效

建立至少 40 条公共信息查询：中文企业官网/品牌别名、同名企业消歧、政府协会长尾站点、最近 7/30 天公开动态各 10 条。每条保存参考官方 URL、查询时间、时间条件、期望实体和证据摘录；至少部分样本来自实际失败查询。

同批对比现有 Tavily、暴露时间条件后的 Tavily、百度、智谱，以及各自融合。测量官网命中率@10、可回答率、实体误匹配率、时间条件满足率、来源支持率、p50/p95 时延和实际费用。供应商断言“更全”不能替代结果。

先采集基线，再冻结上线门槛。硬门槛包括旧参数兼容、域名约束不丢失、取消和部分失败可见、无来源不伪装搜索成功；效果门槛以中文命中和可回答率实测提升为准，不预填虚构分数。

## 7. 本轮交付

已完成源码核对、差距分析、设计与计划登记；后续智谱单源接入已完成开发及真实冒烟，百度已完成实验调用和两组同条件对照。多源产品适配、规模质量评测及正式部署未完成；具体开发记录在关联计划，本文保留调研事实与证据边界。

## 8. 独立搜索 API 候选与费用（2026-10-13 核对）

以下来自各供应商官方公开页面，按原币种展示；套餐单位成本按额度全部用完计算，非承诺效果排名。实际金额取决于购买套餐、免费额度、返回数量、重试及额外正文读取；主 Agent 的模型费另计。

| 候选 | 适合纳入评测的能力 | 搜索费用与计费边界 | 官方来源 |
|---|---|---|---|
| 智谱 Web Search | 国内检索，可选自研/搜狗/夸克；结构化摘要及日期 | Std ¥10/千次；Pro ¥30/千次；搜狗、夸克各 ¥50/千次；独立搜索 API 与对话生成收费分开 | [能力](https://docs.bigmodel.cn/cn/guide/tools/web-search)、[价格](https://docs.bigmodel.cn/cn/guide/start/pricing) |
| Serper | Google 网页、新闻等结果，可补海外及中英文公开资料；第三方 SERP 服务 | 入门包 $50/5万查询，折 $1/千次；大包最低 $0.30/千次，预购额度有效期 6 个月；提供 2500 次试用 | [官网与价格](https://serper.dev/) |
| Brave Search API | 明确使用独立索引，网页/新闻及 AI context，可检验与 Google 的互补性 | Search $5/千请求，每月 $5 赠送额度；Answers 是另一产品，额外按 token 收费 | [官方价格](https://brave.com/search/api/) |
| Perplexity Search API | 结构化结果、正文上下文、语言/地域/域名条件，适合批量检索 | 标准 $5/千成功请求；Fast $1/千成功请求；一个请求最多 5 个查询按一次请求计费，无额外 token 费；成功零结果仍计费 | [能力](https://docs.perplexity.ai/docs/search/quickstart)、[价格](https://docs.perplexity.ai/docs/getting-started/pricing) |
| Exa | 网页及公司/人物等索引、内容提取与深检索，适合研究任务候选 | 前 10 结果：Instant $4/千次，Fast/Auto $7，Deep $12，Deep-Reasoning $15；超过 10 个每多一个结果 +$1/千请求；独立 Contents $1/千页/内容类型，AI 摘要另计 | [官方价格](https://exa.ai/pricing) |
| Tavily | 现有接入，面向 Agent 的搜索与提取 | 按量 $0.008/credit；basic 1 credit、advanced 2，折 $8/$16 每千次；月包约 $5–7.5/千 credit；每月 1000 免费 credit | [官方计费](https://docs.tavily.com/documentation/api-credits) |
| SerpApi | 覆盖多个搜索引擎及垂直 SERP，适合需要 Google/Bing/百度等统一接入时评测 | $25/月1000次、$75/月5000次、$150/月15000次，折 $25/$15/$10 每千次；免费 250 次/月；套餐未用满时实际单位成本更高 | [官方价格与 API 列表](https://serpapi.com/pricing) |
| DataForSEO | 多引擎 SERP 与大批量采集，能区分同步和队列检索 | Google 首 10 结果：队列 $0.60/千次，优先队列 $1.20，Live $2；site 等高级操作符有 x5 基价乘数，翻页另计；不能将队列价当即时对话价 | [官方价格](https://dataforseo.com/pricing/google-serp/google-organic-serp-api) |
| 博查 Web Search | 国内网页、摘要、发布时间、时间范围，作为中文补充源候选 | 本轮未能读取官网链接的飞书价格页，当前单价未核实，不采用旧渠道目录价编制预算 | [能力](https://open.bochaai.com/)、[官网链接的价格页](https://aq6ky2b8nql.feishu.cn/wiki/JYSbwzdPIiFnz4kDYPXcHSDrnZb) |

### 8.1 针对本项目的组合建议

把“中文引擎 + Google 结果 + 明确独立索引”作为评测组合：中文主源可比较百度与智谱 Pro，Google 路径先试 Serper，第三路试 Brave。中文专项再比较智谱搜狗/夸克和博查；复杂多查询任务纳入 Perplexity Search，企业研究任务纳入 Exa。

Serper、SerpApi、DataForSEO 若都选择 Google，只是不同 API 供应商，不能计作三个独立搜索引擎。多个引擎命中同一转载内容，也不能算多份独立原始证据；需要 URL/实体去重并核验原始页面。上述推荐是按能力和费用的选型判断，中文召回优劣仍待同批样本实测。

### 8.2 调用费用示例

按每月 1 万个任务、每任务各调用指定源一次、无重试，未扣免费额度，费用仅含搜索消耗：

- 智谱 Pro + Serper：¥300 + $10；Serper 首次仍需购买至少 $50 入门包，消耗与充值额不同。
- 智谱 Pro + Serper + Brave：¥300 + $60。
- 同时每源搜索 3 个关键词：上例变为 ¥900 + $180；查询数和来源数相乘。
- Perplexity 标准搜索：1 万个最多 5 查询的批次为 $50；Fast 为 $10，质量与覆盖需另测。

正文提取、页面存储、供应商回答生成与主 Agent 模型用量另计。以“每个任务的总费用/有效证据数量”评价，而不是仅比较每千次最低价；默认路由不必每次调用全部候选源。

## 9. 用户要求的百度 / 智谱同条件对比（2026-10-13）

状态：已完成两组真实 API 对照。用户将百度搜索凭据配置到项目 `.env` 后，本轮于北京时间 2026-10-13 13:01 同条件调用百度和智谱；四次请求均成功、各返回3条，百度原始结果未发现域名越界。只读取本任务的项目凭据，日志与结果不包含密钥。此前网页端未取得结果，不纳入本次对比。

冻结首批条件：查询“智谱 联网搜索 API”，限定 `docs.bigmodel.cn`，前3条；查询“华为 公司简介”，限定 `huawei.com`，前3条；两者均不加时间条件。智谱使用已接入 `search_pro`，百度使用 `baidu_search_v2` / standard / web-only，将相同域名传到 `search_filter.match.site`，返回数使用 `resource_type_filter.top_k=3`。

初步已观察智谱：技术查询返回快速开始、平台介绍、智能体开发平台，没有直接命中独立搜索 API 文档；企业查询第一条命中华为中文公司简介，但另外两条为日文企业页和开源博客。摘要存在菜单/Cookie噪音及2022、2015等旧数据，企业三条发布时间为空。这是两条查询的样本观察，不能替代整体质量排名。

已同时重跑智谱，并保留原始标题、URL、content、date与实测耗时；百度保留 snippet，没有复用现有 skill 删除 snippet 的结果处理。HTTP在每组内并发发起，百度的两次调用串行以避免默认QPS干扰。没有改默认提供商，也未将百度适配接入产品工具。

### 9.1 返回链接与单次耗时

| 查询 | 提供商 | 排名1 | 排名2 | 排名3 | 实测耗时 |
|---|---|---|---|---|---|
| 智谱 联网搜索 API | 百度 V2 standard | [平台介绍](https://docs.bigmodel.cn/cn/guide/start/introduction) | [知识库检索](https://docs.bigmodel.cn/api-reference/知识库-api/知识库检索) | [Coding Plan 常见问题](https://docs.bigmodel.cn/cn/coding-plan/faq) | 2.097s |
| 智谱 联网搜索 API | 智谱 Pro | [快速开始](https://docs.bigmodel.cn/cn/api/introduction) | [平台介绍](https://docs.bigmodel.cn/) | [智能体开发平台](https://docs.bigmodel.cn/cn/guide/platform/intelligent-agent) | 1.943s |
| 华为 公司简介 | 百度 V2 standard | [公司简介](https://www.huawei.com/cn/corporate-information) | [华为中文首页](https://www.huawei.com/cn/) | [华为招聘官网](https://career.huawei.com/) | 2.070s |
| 华为 公司简介 | 智谱 Pro | [旧路径公司简介](https://www.huawei.com/cn/corporate-info) | [日文企業情報](https://www.huawei.com/jp/about-huawei) | [开源博客](https://www.huawei.com/cn/open-source/blogs?author=yupan) | 1.714s |

### 9.2 对比结论及限制

**本次企业公开资料样本，百度优于智谱；技术接口文档样本，两家均没有直接命中目标。**

- 企业查询：百度首条正文直接进入公司介绍，包含21.3万员工和2025年研发费用1,923亿元等信息；通过另行读取当前[华为公司简介](https://www.huawei.com/cn/corporate-information)核对一致。智谱首条片段包含20.7万员工和2022年研发数据，前半部混有菜单/Cookie；第二条日文片段含2015年信息，第三条偏题。
- 技术查询：两家前3条都没有独立 Web Search API 文档。百度后两条分别转到知识库和Coding套餐限制；智谱返回通用API说明和Agent平台联网设置。不能以返回数量相同称为同等准确，也不能在此样本宣称百度技术检索更强。
- 片段处理：百度企业首条返回1094字符、首页97、招聘949，主要是业务正文；智谱首条912、日文491、博客920，包含显著导航噪音。字符长度不等于有效证据量。
- 日期和评分：百度返回了日期字段，但其含义不能自动当成页面原始发布日期；六条rerank_score均为1，未区分本次明显偏题结果。智谱企业三条日期为空。日期完整不等于时效性已经核验，原始分数不跨引擎比较。
- 速度：本次智谱两次略快，只有单次样本，没有p50/p95或稳定性结论。

选型建议：下一阶段将百度纳入中文企业资料主源候选，保留智谱做补充；技术查询需要更明确的关键词/术语和正文核验。仅两组查询不能决定全面质量排名，默认路由切换仍需要更多样本。

## 10. 金牌家居负责人、邮箱与公司简介实测（2026-10-14）

用户要求验证“搜索 + 读取命中链接”能否取得企业负责人姓名、邮箱及公司简介。本轮百度 standard/web 与智谱 search_pro/high 各调用 6 次，均请求前 10 条，不加域名及时间过滤，12 次均成功；共返回 120 条记录，包含重复，不能算 120 个独立来源。两家每组使用相同查询；先用独立公开检索确认公司全名，不将该检索结果计入两家 API 命中率。未测试百度 turbo、智谱搜狗或夸克。

### 10.1 查询与观察

| 查询 | 百度 | 智谱 |
|---|---|---|
| 金牌橱柜 公司简介 官网 | 官网产品页第 3、官网首页第 6；前两条股价资讯中的片段把公司混为金牌厨电企业，不能采信 | 前 10 条没有直接返回公司官网；同一第三方域名占 6 条，且含厨电企业误匹配及旧资料 |
| 金牌厨柜家居科技股份有限公司 董事会秘书 电子邮箱 | 摘录中的公司邮箱出现在第 6、10 条；第 1 条公告摘录未带邮箱，读取正文后取得陈建波、李朝声和公司邮箱 | 第 1 条旧年报已经包含陈建波、李朝声及邮箱；第 3 条新浪公司资料可直接读到董秘及邮箱，第 9 条为 2026 半年报摘要 |
| 金牌厨柜家居科技股份有限公司 董事长 总裁 | 返回百科、企业资料，包含温建怀、潘孝贞 | 首条为 2021 年任职新闻，其他管理层资料和新闻混合；不能只凭旧新闻确认当前任职 |
| 金牌家居 招商 商务 负责人 邮箱 | 有公司邮箱，但混入另一家北京同名企业；未取得经核实的招商负责人专属邮箱 | 前 10 条全部为同一工商信息站的分支或经销商页面；多条邮箱是该站客服，不能归属目标公司 |
| 金牌厨柜 公司简介 官网（补查规范品牌名） | 命中官方招聘页、产品页第 2、官网首页第 5和全球布局页 | 结果比“橱柜”写法更集中于目标企业，但前 10 条仍未直接返回企业官网 |
| 金牌家居 2026 半年度报告 联系人 邮箱（补查年份与文档类型） | 前 10 条无直接命中的 2026 半年报摘要；第 7 条为报告列表，可继续读取导航 | 第 1 条直接命中 2026 半年报摘要，正文可核实姓名与邮箱；第 7 条为 2026 半年度业绩说明会公告 |

上述排名是本次 API 返回记录，不是常驻排序；没有把补查结果倒算为首轮结果。“厨柜”是法定名称和品牌规范写法，“橱柜”是用户原始输入，产品需要实体补全和查询改写。

### 10.2 详情核验和最终事实

- 全称：**金牌厨柜家居科技股份有限公司**，简称金牌家居，股票代码 603180。官网简介称成立于 1999 年，业务聚焦整家定制、柜门墙一体化方案；官网产品入口包含厨柜、衣柜、木门等。来源：[官网简介](https://www.goldenhome.cc/jinpai/guanyujinpai)、[官网首页](https://www.goldenhome.cc/)。
- 董事会秘书：**陈建波**；证券事务代表：**李朝声**。2026 年半年报摘要将两人的电子信箱均列为 **goldenhome@canc.com.cn**。这是公司公开联系邮箱，不是已证明的个人专属邮箱。来源：[2026 半年报摘要](https://guba.eastmoney.com/news,603180,1764061850.html)、[同报告新浪披露页](https://money.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12539625&stockid=603180)。
- 董事长：**温建怀**；总裁：**潘孝贞**。从百度命中官网继续导航到[官方领导寄语](https://www.goldenhome.cc/jinpai/message)核对两人职务；智谱补查命中的[2026 半年度业绩说明会公告](https://guba.eastmoney.com/news,603180,1773454925.html)也确认温建怀的董事长职务。本轮没有取得两人个人专属工作邮箱。
- 读取百度第 1 条[2024 公告正文](https://finance.sina.cn/2024-04-26/detail-inatepnq4993223.d.html)后能取得姓名和公司邮箱，说明摘要未出现不等于详情没有。旧公告仅作链路验证，联系人用 2026 报告复核。
- 页面读取有真实限制：企查查详情返回 HTTP 405；搜狐旧报告 HTTP 200 但只有页面壳，无可用公告正文。东方财富的逗号 URL 在开发助手 web 工具中失败，但普通 HTTP 读取成功并取得正文；这是本轮开发侧核验，不代表产品 web_fetch 已实现。

### 10.3 对项目的意义

本样本**官网发现百度更好，董秘联系方式和指定最新报告智谱更好**；两家均未取得经核实的个人专属工作邮箱。公司共用邮箱、分支联系人、第三方平台客服、同名公司邮箱必须分别归属，不能仅靠正则抽取后拼到高管姓名旁边。

建议在后续企业研究策略中执行：品牌别名及规范名称补全 → 拆分官网、管理层、具体职能及最新报告查询 → 多源去重与实体核验 → 读取正文及必要的 PDF → 将姓名/职务/邮箱类型/所属公司/证据 URL/文档日期关联保存。读取失败须可见，并优先切换同份官方披露的可读来源。上市公司董秘邮箱的可得性不能外推到未上市企业或其他部门。

### 10.4 法定代表人与企业邮箱补查（2026-10-14）

用户指出企查查、天眼查公开企业页包含法人和邮箱。本轮增加相同条件的两组查询，两家各增加 2 次成功调用、每次 10 条；本节累计每源 8 次查询，共 160 条返回记录（含重复）。

| 查询 | 百度 | 智谱 |
|---|---|---|
| 金牌厨柜家居科技股份有限公司 法定代表人 邮箱 | 第 2 条企查查、第 5 条天眼查片段均列出法定代表人潘孝贞和 goldenhome@canc.com.cn | 前列有分支企业混入；第 10 条上市保荐书列出潘孝贞与同一公司邮箱 |
| 金牌家居 潘孝贞 法人 邮箱 | 第 2 条企查查继续返回姓名与公司邮箱 | 第 1 条公司概况、第 2 条品牌介绍、第 4 条企业投融资资料包含姓名与同一公司邮箱；第 9 条工商变更公告可进一步核对法人 |

读取[2026 年半年度报告全文](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12539626&stockid=603180)独立复核：第二节公司信息明确“公司的法定代表人”为潘孝贞；同节联系人表和基本情况分别将 goldenhome@canc.com.cn 列为董秘/证券事务代表及公司电子信箱。企查查、天眼查企业页的搜索片段只标注“邮箱”，未标注为“潘孝贞个人邮箱”。因此可交付“法定代表人姓名 + 企业公开邮箱”，不能改写为“法人本人专属邮箱”。两站详情本轮开发助手 web 工具仍未读取成功，未绕过登录或访问限制。

本次法定代表人/工商联系方式查询，百度更直接命中用户指定的企查查、天眼查；智谱也能取得企业邮箱，但前一组靠第 10 条旧保荐书。与董秘查询的结论不同，继续支持按子任务拆分检索并融合，而非宣称某家在全部企业资料查询中更优。

## 11. 全筑股份企业邮箱实测（2026-10-14）

用户指定上海全筑控股集团股份有限公司。百度 standard/web 与智谱 search_pro/high 各调用两次，各请求前 10 条，不加时间或域名过滤，四次均成功，共 40 条记录（含重复）。查询为：

1. `上海全筑控股集团股份有限公司 法定代表人 邮箱`
2. `上海全筑控股集团股份有限公司 联系人 电子邮箱`

| 查询 | 百度 | 智谱 |
|---|---|---|
| 法定代表人 邮箱 | 第 2 条企查查片段同时列出朱斌及 **332069799@qq.com**；第 3 条天眼查仅显示邮箱标签，未显示邮箱值 | 第 1 条企业第三方页含另一个 QQ 邮箱（未核实）；第 2、5、6 条含 adm@trendzone.com.cn，第 4 条证券事务代表任职公告含 ir@trendzone.com.cn；另有 4 条天眼查新闻返回平台客服邮箱，按归属剔除 |
| 联系人 电子邮箱 | 第 2 条企查查同样含 **332069799@qq.com**；第 3 条官网、第 5 条投资者关系 | 第 1 条公司概况含 ir@trendzone.com.cn，第 4 条为 2026 半年报摘要且带同一邮箱；其余有不同年份报告和第三方公司资料 |

### 11.1 正文读取与复核

- 法定代表人：**朱斌**。独立补查并读取[2026 半年报全文](https://stock.stockstar.com/notice/SN2026082500045537.shtml)第二节公司信息确认，不将独立补查结果算作两家 API 命中。
- **ir@trendzone.com.cn**：2026 半年报摘要列为董事会秘书**孙海军**、证券事务代表**包嵘嵘**共用电子信箱；2026-09-17 业绩说明会公告明确列为公司邮箱和董事会办公室联系邮箱。这是可核实的近期共用联系渠道，不能替代用户指定的企业页 QQ 邮箱目标。来源：[2026 半年报摘要](https://vip.stock.finance.sina.com.cn/corp/view/vCB_AllBulletinDetail.php?id=12535606&stockid=603030)、[2026-09-17 公司公告](https://epaper.cs.com.cn/zgzqb/html/2026-09/17/nw.D110000zgzqb_20260917_1-B031.htm)。
- **adm@trendzone.com.cn**：读取智谱命中的[百度知道问答](https://zhidao.baidu.com/question/249505249846687284.html)，爱企查账号回答将其标为公司邮箱。该资料有旧注册资本，尚未用近期官方披露核实邮箱当前有效性；不推定它由朱斌本人使用。
- 读取智谱命中的[2024 证券事务代表聘任公告](https://guba.eastmoney.com/news,603030,1415781868.html)成功，正文确认包嵘嵘与 ir 邮箱；当前职务使用 2026 报告复核。
- 百度命中的[官网投资者关系](https://www.trendzone.com.cn/investor.html)和[联系我们](https://www.trendzone.com.cn/contact.html)可读，但可见正文未提供邮箱；企查查返回 405。百度命中的 2024 新浪会议公告正文可读，未发现目标邮箱。智谱第 4 条中财网及旧爱金股报告页面本轮普通 HTTP 读取未取得可用正文，改用同份公开报告的新浪披露页核验，不能伪称原链接读取成功。
- 智谱旧报告片段有两种格式噪音：两列邮箱粘连导致正则匹配出错误域名，另一个实名工作邮箱将域名句点写成逗号。没有自动猜测修正或作为有效邮箱交付；需保留上下文并回原文核验。

### 11.2 评测纠错：企业 QQ 邮箱被临时脚本误过滤

用户提供企查查截图，明确显示企业邮箱 **332069799@qq.com**。重新检查此前保存的两组原始 API 响应，百度第 2 条企查查结果均已含该邮箱，与截图一致；智谱两组前 10 条没有该目标邮箱。无需重新调用 API 即可复核这一事实。

先前临时评测脚本为排除个人邮箱，按邮箱域名统一删除 QQ/163/126/Gmail/Hotmail，导致企业公开联系方式被漏掉，随后错误报告“百度没有邮箱值”“智谱更直接”。这是评测输出与判断错误，不能归因于百度召回不足。已移除临时脚本中的域名过滤并更正本节结果，不涉及产品 adapter 改动。

**按用户本次验收目标，百度已命中企查查公开的目标 QQ 邮箱，智谱未命中该目标邮箱。** QQ 等邮箱服务域名不能用于判定是否属于企业公开联系方式；应按页面标签、所属实体、上下文和来源核验。该截图与片段证明它是企业页公开邮箱，仍未证明由朱斌本人使用。公司公共邮箱、企业登记联系邮箱、具名负责人工作邮箱应同时保留并标注归属，不能用投资者关系邮箱替代用户指定的联系方式目标。本轮没有进行投递或有效性探测。

### 11.3 用户查看前 20 条完整响应

2026-10-14 按用户要求重新用本节两组相同关键词调用百度 standard/web，均将 top_k 从 10 改为 20，不加站点或时间过滤。两次成功且各返回 20 条，耗时分别 0.791s、0.658s；单次记录不能推导稳定速度。两组第 2 条仍是企查查且 content 包含 332069799@qq.com；本次每组其余 19 条未匹配到邮箱值。人物、关联子公司、法院公告等结果混入，需要按目标实体与查询意图判断有效性。

向用户交付本机临时目录下 `baidu-quanzhu-top20-20261014.json` 和对应 HTML 查看版。JSON 保留两组请求正文、采集时间、耗时与完整成功响应，不包含请求头或密钥；HTML 保留每条结果的全部字段及完整片段，按 API 排名呈现，没有过滤邮箱、重排或去重。本次只有返回条数参数改变，不能将新调用假称为上一轮原始的 20 条。未把本次搜索调用计为智谱同批对照。
