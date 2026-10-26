# Agent 网络搜索增强开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Read UTF-8 | 已验证UTF-8优先，保留旧编码兼容 | ✅ 完成（2026-10-26） | 隔离工具候选323回归通过，用户已确认，仅工具切片 |
| PDF页段 | 可选页段参数、原页码及来源元数据 | ✅ 完成（2026-10-26） | 默认路径兼容；隔离工具候选323回归通过，未部署 |
| 首批提交 | 通用Search/Fetch、Read、模型完整性、50轮及Pro容量 | ✅ 完成（2026-10-23） | 隔离候选独立533通过/0跳过，CR及主控import通过；研究交付层排除，未部署 |
| Phase 0 | 上游源码核对、现状审计与设计草案 | ✅ 完成（2026-10-13） | 源码及价格已核对；百度/智谱实测补充见调研 §9–10；博查单价及规模质量待核实 |
| Phase 1A | 智谱独立搜索接入与兼容修复 | ✅ 完成（2026-10-13） | 80相关用例通过，修复后14例独立复测通过；CR已收口，同key真实Pro返回3条；未部署 |
| Phase 1B | 统一provider注册及百度接入，默认保留Tavily | ✅ 完成（2026-10-14） | 独立121项通过/0跳过，CR通过；同步远程后121项复验及启动检查通过，国内源默认关闭，用户授权提交，未部署 |
| Phase 1 | 兼容修复、统一服务、批量和时间条件 | 🔧 进行中 | 兼容修复和统一provider接口已完成；批量/有界并发待开发 |
| Phase 2 | 百度/智谱 adapter 与中文检索融合 | 🔧 进行中 | 三家独立provider适配已实现；多源融合和规模召回评测待开发 |
| Phase 3 | web_fetch 与企业资料核验策略 | 📋 待开发 | 普通 HTTP 正文读取，复用已有浏览器与研究子智能体 |
| Phase 4 | 同批质量评测与上线配置选择 | 📋 待开发 | 金牌8、全筑2查询/源；企业QQ漏报及结论已纠正（调研 §11.2）；规模质量及p95待做；未部署 |

> 日期：2026-10-13。关联：[调研](../research/agent-web-search-harness-research.md)、[设计](../system/agent-web-search-enhancement-design.md)。

## 本轮增量范围（2026-10-14）

百度V2结构化搜索接入同一web_search工具，由tools.search.provider或SEARCH_PROVIDER显式选择；配置默认改回tavily，百度与智谱均保留但默认不启用。配置凭据不会自动启用供应商，Tavily缺密钥/失败也不会回退百度或智谱。BAIDU_API_KEY与既有百度搜索skill约定一致，独立于BAIDU_OCR_API_KEY；不新增工具名或Agent可传的provider参数。

用户追加未来会接很多provider，因此统一为models.SearchRequest/SearchProvider接口、registry.PROVIDER_FACTORIES注册表及providers/{tavily,baidu,zhipu}.py独立实现。工具只规范通用参数、创建当前配置选中的request级provider并调用search；未来加适配器/注册项/配置不需修改工具分支。保留旧services.web_search.zhipu导入入口；不共享provider实例/请求凭据，不做自动探测、多源融合或路由兜底。Tavily继续使用既有同步SDK加executor，取消只能终止异步等待，线程请求仍靠SDK超时退出，不宣称本轮解决同步SDK生命周期限制。

使用固定官方HTTPS端点/v2/ai_search/web_search、standard/web-only，不调用生成式chat/completions。站点限定至多20项，排除站点原生block_websites并严格后过滤；week/month/year原生时效字段。百度官方page_time只支持固定date-math套餐，不能表达精确最近24小时；day请求前明确拒绝，不扩大范围或调用其他provider。保留摘要、链接、供应商来源、原始日期及评分（不跨引擎比较），摘要不当作全文；news专用主题不支持时明确提示。HTTP超时/取消释放client，不重试鉴权错误，不输出原始错误或凭据。单元HTTP MockTransport、路由、配置、既有智谱/Tavily回归与独立测试/CR；百度仍未启用，不以mock结果宣称真实效果提升。

### Phase 1B 验证记录

- 环境为本项目venv/Python3.12宿主，经scripts/dev_test.sh执行；数据库、日志库及Redis全部localhost:1隔离，供应商调用均替身。主控初轮83 passed/5 integration skipped；跳过来自集成目录公共数据库fixture，与本文件搜索工具组件链无关。
- 独立测试75项（旧搜索/智谱/配置/tool_registry）、百度24项和新增独立反例15项通过；两条真实ToolExecutor组合移入integration目录，搜索工具集成文件独立覆盖无关DB初始化fixture，不改公共conftest、不访问数据库。整理后两文件22 passed/0 skipped；复用相同最终业务状态有效结果，共121 passed/0 skipped，既有requests依赖告警1条。
- 覆盖默认关闭与有密钥不自动启用、仅实例化指定provider、未知provider拒绝、Agent不能传参切源、旧智谱import兼容、Tavily超时同源降级保留全部约束、鉴权不重试、取消不重试、百度请求/响应、严格域名/IDNA/去重/上限、缺密钥、非2xx/业务错误/非JSON/网络故障脱敏及HTTP client关闭；真实组件链只mock供应商边界。
- 独立CR发现百度day日期表达式不在官方固定套餐内，已修复为输入前unsupported_time_range/零HTTP，不扩大到自然日或周、不换源；测试与设计同步并复核，最终无P0/P1/P2。
- 主控compileall及实际settings/import/schema检查通过：选中tavily，注册baidu/tavily/zhipu，工具名仍web_search，参数没有provider，未知注册项拒绝，旧智谱入口同一函数，网络调用0。此为隔离启动检查，不启动业务调度或全服务。
- 默认继续Tavily，百度与智谱仅显式配置选择时调用，不自动回退；本轮没有真实供应商请求或质量/费用测评。未提交或部署。后续批量、有界并发、多源融合及web_fetch仍待开发，不将Phase1B完成标记为整体计划完成。
- 用户随后明确授权提交代码。提交前fetch发现远程领先9个提交，快进到a59a43df；唯一重叠ideas索引已保留远程新版提问设计行及本任务新增行，不stash/覆盖其他工作区改动。同步后主控重新运行七个相关文件，121 passed/0 skipped，1条既有requests告警；实际配置/import/schema检查再次通过，默认tavily、网络调用0。提交关联本计划及设计，立即推送当前确认的upstream，不部署。

## 本轮实施范围（2026-10-13）

用户指定先接智谱，复用智谱 LLM 的同一份密钥配置。本轮仅实现 provider 配置、独立 HTTP adapter、时间范围、旧参数及域名过滤兼容；修复相关 Tavily 降级条件丢失和错误泄漏。批量、多源融合、百度、web_fetch 和质量评测继续待开发，不能把智谱单源接入标为整体完成。

当时`configs/config.yaml` 默认 `provider: zhipu` / `zhipu_search_engine: search_pro`；未带 provider 的历史配置仍默认 Tavily。Phase1B遵照用户新要求恢复默认Tavily，智谱仅显式选择启用。`ZHIPU_SEARCH_ENGINE` 可切换引擎。凭据读取 `settings.llm.zhipu.get_effective_keys()` 的首个有效 key，不新增搜索 key，也不复用 LLM base_url。本轮不重构 LLM KeyPool，不声称搜索与模型共享并发配额。

智谱独立 API 不生成 answer；advanced 映射 high 摘要并明确不是完整正文。news 专用主题仅 Tavily 支持，智谱提示改用新闻关键词和时间范围。单个域名在支持的引擎走原生过滤；多域名、排除域名及夸克域名限制在候选中严格后过滤，可能返回不足数量。搜狗 count 按10取整再裁剪，夸克不传不支持的 count 字段。

### Phase 1A 验证记录

- 环境：Windows 宿主 `C:\Users\ethyn\AppData\Local\Programs\Python\Python312\python.exe`；通过 Git Bash 执行统一测试入口，无运行 Docker 容器；httpx 0.28.1、tavily-python 0.7.23。
- 主控自测：`scripts/dev_test.sh tests/unit/tools/test_search_tool.py tests/unit/tools/test_zhipu_search_tool.py tests/unit/test_config.py -p no:cacheprovider -q`，59 passed；新增 ToolExecutor 集成文件 5 passed。
- 独立测试：上述单元范围加 `tests/unit/test_tool_registry.py`，75 passed；`tests/integration/test_tool_execution.py`，5 passed。覆盖 HTTP 序列化、真实客户端关闭、取消、异常脱敏、参数规范化、引擎字段差异和域名严格过滤；网络边界使用 MockTransport。
- 独立 CR：发现 P2，真实 SDK `tavily.errors.TimeoutError` 不继承 builtin TimeoutError，已修复为同时识别两种类型，并将测试改为实际 SDK 异常；审查者复核后无遗留可操作缺陷。
- 修复后主控和独立测试均执行 `scripts/dev_test.sh tests/unit/tools/test_search_tool.py tests/integration/test_tool_execution.py -p no:cacheprovider -q`，各 14 passed。其他不受影响结果按流程复用，没有机械重跑全量。
- 主控真实可用性冒烟：现有智谱 LLM 密钥调用独立 Pro 搜索，查询“智谱开放平台官方文档”，成功返回 3 条 URL；只验证密钥/接口/结果映射，不作为官网命中或召回质量结论。没有运行付费多引擎质量对比。
- 用户追问后真实复测：限定 `docs.bigmodel.cn` 搜“智谱 联网搜索 API”、限定 `huawei.com` 搜“华为 公司简介”，两组各返回 3 条来源；第二组第一条命中华为中文公司简介。观察到技术查询含泛介绍页、企业摘要含 Cookie/导航和非中文页面，确认接入及域名约束可用，相关性与摘要清洗仍需后续增强。
- 主控配置/import/schema 与定向 diff/新增文件格式检查通过；未提交或部署。既有 Pydantic 弃用及 requests 依赖告警不影响本轮测试，未扩大范围修复。

## 1. 范围和流程

首期增强公共互联网中文网站、企业公开资料和时效信息。开发前阅读后端、测试规则及关联设计。预计涉及通用工具、异步网络请求、取消和身份边界，代码阶段按高风险流程：主控开发和自测，独立测试智能体与独立 CodeReview 智能体验证，主控整合。当前仅文档，无业务代码改动，不运行完整三角色流程。

不自动提交或部署。后续若触及 Desktop/Runtime 公共接口，先按共同契约登记唯一写入者、版本兼容和检查点。

## 2. Phase 1：兼容性与公共服务

拟涉及 `src/tools/search/search_tool.py`、`src/services/web_search/`、`src/config/settings.py`、`configs/config.yaml` 和定向工具测试。

交付：清晰的请求/结果类型、Tavily adapter、queries 参数及有界并发、时间条件、明确取消和超时、旧字段兼容。只对可重试错误降级，保留域名及时间条件；limit 与 max_results 归一化，None 使用配置。

验收：旧 keyword 输入、工具定义及 role/白名单有效；无搜索凭据可安全启动；空白查询、非法枚举、参数冲突和结果上限校验；advanced 失败时所有约束保留；401 不无意义重试；批次部分失败有实结果与 complete 标记；取消后没有新请求。

## 3. Phase 2：国内提供商和融合

交付：百度 V2 与智谱独立 Web Search adapter，结果时间/摘要/查询归属保留，能力矩阵，配置决定主源与互补源，URL 去重和排名融合。已有百度 skill 保留其产品入口；是否改为复用公共服务按实际 skill 执行环境决定，不能删掉运行时资源。

验收：用官方响应 fixture 检验正常、零匹配、业务错误和字段缺失；不同引擎不支持的域名/日期条件明确拒绝或走满足约束的配置源；并发限额和批次请求上限真实生效；取消后释放连接；shared 工具实例不保留请求身份；缓存和诊断符合租户及凭据规范。

## 4. Phase 3：读取与策略

交付：普通 web_fetch 工具及 HTTP 读取服务；企业全称/简称/地域消歧、官网优先、最近时间条件与证据引用指南，首先调整已有研究子智能体。

验收：HTML/文本、重定向、无发布时间、编码、截断、404/反爬/动态页；私网地址、跳转到私网、DNS 重绑定和意外凭据转发拒绝；正文 URL 与来源一致；不新增本地 loop 或设备协议。

## 5. Phase 4：同批评测

至少 40 条公共查询：企业官网/别名、同名企业、长尾政府协会站点、最近公开动态各 10 条。补实际失败样本，保存期望实体、官方链接、参考摘录和日期条件；时间敏感数据记录采集时刻，不依赖永久固定的新闻排名。

分开评测引擎检索质量与 Agent 最终回答质量。比较旧 Tavily、参数完善的 Tavily、百度、智谱及融合；记录命中率@10、可回答率、误匹配率、时间满足率、来源支持率、p50/p95 和实际费用。先跑基线再冻结门槛，不在开发报告里填推测分数。正式默认源必须有同批质量证据。

只在确认可用凭据及费用归属后执行真实 API 评测；尚缺凭据或运行环境时记录缺口，不用 mock 通过声称真实召回提高。供应商调用次数和模型用量纳入既有计费/准入机制，不减少生成 token 上限充当预算。

## 6. 验证交接与状态更新

定向测试通过项目 `scripts/dev_test.sh` 执行，先确认容器或宿主环境运行当前源码。涉及配置、import 与装配时补隔离启动检查，避免真实业务任务启动；独立验证报告真实命令、通过/失败/跳过、环境及未验证项。修复后重跑受影响检查，不机械跑全量测试。

先更新本表，再同步 ideas 索引状态。全部开发和必要验证完成后移动到 ideas_finished，部署状态单独如实记录，不把代码完成写成线上效果已验收。

## 首批通用能力提交（2026-10-23，用户明确授权）

范围按设计“首批通用组件提交边界”：DeepSeek默认搜索；HTML/PDF有界原生/远端Fetch；会话来源SHA及Read分页；LLM length/空输出正确处理及耗尽可见说明；主默认50和Pro393216。

实际按修改点拆分，并以临时Git index导出隔离提交树验证，避免未提交模块造成假绿。独立测试和只读CR检查候选目录。提交前主控亲自验证配置/import/工具装配。只暂存批准组件与对应测试/设计计划，提交后推送已确认upstream；不部署、不调用浏览器、不再跑整Skill。

排除research_plan/validate、delivery/validation/identity/report、Runtime研究交付上下文/完成门禁/进度提示、整Skill脚本与批量实验，以及WeCom等其他任务。API接入和组件回归通过不能等同搜索召回质量或全业务达标。已有真实11URL原生与远端各11成功，但网络也已恢复；固定7条Search查询仍暴露站内人数、官网联系人和官方PDF召回缺口，后续优先只补组件召回。

输出容量：仅补deepseek-v4-pro:393216（384K），原缺表回退16384；真实调用已超过16K并执行完整工具。显式max_tokens优先及未知模型/chat_lite旧16K默认尚未统一清理，未冒称全局放大；费用不能靠压低生成上限控制。


### 首批提交验证记录

- 当前远程快进基线为6a915b4982，独立验证运行隔离候选源码，不导入原工作区未提交的研究模块。宿主项目venv/Python3.12，经scripts/dev_test.sh执行；数据库/日志库/Redis为localhost:1，供应商边界为替身，无浏览器或部署。
- 独立19文件回归最终533 passed / 0 failed / 0 skipped。首轮5例resume_vl组合因隔离候选没有.env中的智谱key而失败；使用明确synthetic测试key后19文件完整重跑通过，未使用真实供应商网络。受影响Search/来源投影2文件独立29通过，与533重叠不累计。
- CR发现会话来源投影丢失既有Tavily answer和search_engine/response_time；已保留元数据，将answer标记为provider_generated_summary，预览上限700字符，长摘要另存会话文件/SHA供Read分页，不混作原始来源。新增短/长/关闭摘要及跨会话拒读回归。CR复核无剩余P0/P1/P2，真实候选diff --check通过。
- 主控最终import及配置检查通过，关键模块均来自候选，默认deepseek，未批准研究工具物理不存在；未启动业务服务。普通Search→Write组件交付无需研究门禁，独立烟测completed。
- 首批提交不表示完整Skill或搜索质量验收通过。下一步优先用固定查询逐项补官网、联系人、官方PDF的搜索召回；Fetch继续按具体失败样本增强。


独立回归可复现命令（隔离候选目录；PATH使用本项目venv，PYTHONPATH指向候选；ZHIPU_API_KEYS=unit-isolated-zhipu-key，DB/日志库/Redis均为localhost:1）：

```bash
scripts/dev_test.sh tests/unit/tools/test_deepseek_search_tool.py tests/unit/tools/test_baidu_search_tool.py tests/unit/tools/test_search_tool.py tests/unit/tools/test_search_provider_independent.py tests/unit/tools/test_zhipu_search_tool.py tests/unit/tools/test_web_fetch_tool.py tests/unit/tools/test_remote_fetch_tool.py tests/unit/tools/test_fetch_service_routing.py tests/unit/tools/test_research_evidence.py tests/unit/tools/test_read_tool.py tests/unit/tools/test_tool_assembly.py tests/unit/test_research_completion_status.py tests/unit/test_agent_default_iterations.py tests/unit/test_llm_gateway_max_tokens.py tests/unit/test_config.py tests/unit/llm/test_provider_structural_validation.py tests/unit/llm/test_deepseek_provider.py tests/unit/test_qwen_provider_thinking_cache.py tests/integration/test_tool_execution.py -p no:cacheprovider -q
```


## Read/PDF独立提交切片（2026-10-26）

本批仅4个工具/服务生产文件与3个测试文件：read_tool.py/test_read_tool.py；web_search/fetch.py、web_search/evidence.py、search/fetch_tool.py与test_web_fetch_tool.py、test_fetch_service_routing.py。PDF工具通用研究提示不随分页提交，Agent主循环、Runtime、control_set及Search提示保持HEAD原样。

基于1e7261c29e1f0f616f9000698463e9c87b65c193的独立候选中，两个新研究模块不存在，6个Agent执行/装配/策略文件与HEAD逐字一致。9相关测试文件323通过/0失败/0跳过（17.52秒），涵盖Read编码、原生/远端Fetch、来源投影、工具装配、Agent引擎/恢复/清理；设计见原设计新增的Read/PDF两节。用户已确认提交此工具切片；Agent执行流程和通用提示不提交，未部署。
