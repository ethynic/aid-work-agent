# Agent 网络搜索增强开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Agent逐目标执行 | 内部目标表、原文证据与缺项复核 | ⛔ 已取消 | 用户要求撤回未提交实验实现；Read/PDF已提交修复与Flash配置保留 |
| 清理与通用研究修复 | 删除废弃原型、取证指南及PDF分页 | ✅ 完成（2026-10-26） | 清570实验文件；独立352项+8探针/CR通过；两企业3份官方PDF取得原生页段正文 |
| 缺项补搜策略对照 | 仅增加通用约束，复用双企业与预算 | ✅ 完成（2026-10-23） | 13轮/3搜索/13Fetch/18Read；触发1次改词及数量10→8，四关键缺项新增补齐0，见调研§19 |
| 小范围自主验证 | 双企业选词、参数及Search→Fetch→Read | ✅ 完成（2026-10-23） | 8轮/2搜索/9Fetch/15Read；官网与部分人数/联系人可得，未触发改词调参，PDF部分未完，见调研§18 |
| Read编码修复 | 严格UTF-8优先、回归与两份来源复验 | ✅ 完成（2026-10-23） | 独立79通过/0跳过，P2兼容回归关闭；两来源292行一致、SHA不变，未提交 |
| 七查询诊断 | 关键词、参数、结果利用与实现边界 | ✅ 完成（2026-10-23） | 离线30份正文SHA复核；发现官网线索及打码邮箱指标问题，未改代码、未新调用 |
| 已有线索跟进 | 保存搜索结果→Fetch→Read定向实测 | ✅ 完成（2026-10-23） | 10条完成记录9成功/1 PDF页数拒绝；29页Read/SHA通过，含1种子对照，见调研§14 |
| 关键词对照 | 原七查询与固定变体同批比较 | ✅ 完成（2026-10-23） | 14调用/55来源，发现候选7→10；site变体未改善，2份Read编码差异，见调研§15 |
| 组件参数验证 | 结果数量、深度及域名参数诊断 | ✅ 完成（2026-10-23） | 9调用/35来源；数量候选3→7，深度同源，域名对齐无差异；2份Read编码差异 |
| 首批提交 | 通用Search/Fetch、Read、模型完整性、50轮及Pro容量 | ✅ 完成（2026-10-23） | 隔离候选独立533通过/0跳过，CR及主控import通过；研究交付层排除，未部署 |
| Phase 0 | 上游源码核对、现状审计与设计草案 | ✅ 完成（2026-10-13） | 源码及价格已核对；百度/智谱实测补充见调研 §9–10；博查单价及规模质量待核实 |
| Phase 1A | 智谱独立搜索接入与兼容修复 | ✅ 完成（2026-10-13） | 80相关用例通过，修复后14例独立复测通过；CR已收口，同key真实Pro返回3条；未部署 |
| Phase 1B | 统一provider注册及百度接入，默认保留Tavily | ✅ 完成（2026-10-14） | 独立121项通过/0跳过，CR通过；同步远程后121项复验及启动检查通过，国内源默认关闭，用户授权提交，未部署 |
| Phase 1C | DeepSeek 独立搜索接入并设为默认源 | ✅ 完成（2026-10-22） | 独立测试/CR通过；评分溢出修复复测通过；5组真实搜索成功；隔离装配检查通过，未部署 |
| Phase 1D | 来源完整回读、web_fetch与研究交付校验 | ✅ 完成（2026-10-22） | 316相关测试/独立221通过，CR通过；主体同题0浏览器、两文件/CSV通过，20轮耗尽/严格合格0，业务未通过 |
| Phase 1E | 完成/轮内交付、官网身份及大PDF | ✅ 完成（2026-10-22） | 独立239通过/CR收口；50轮上限下19轮交付，合格0，业务未通过 |
| Phase 1F | 原生出网诊断与有界服务端Fetch | ✅ 完成（2026-10-23） | 双路径各11/11；独立341项及CR通过；主体24轮失败，业务未通过 |
| Phase 1G | Pro生成上限缺表修复与同题复验 | ✅ 完成（2026-10-23） | 独立40项及CR通过；35轮两文件验收通过，discovery1/3，业务未达标 |
| Phase 1 | 兼容修复、统一服务、批量和时间条件 | 🔧 进行中 | 兼容修复和统一provider接口已完成；批量/有界并发待开发 |
| Phase 2 | 国内搜索 adapter 与中文检索融合 | 🔧 进行中 | 四家独立provider适配已实现，默认DeepSeek；多源融合和规模召回评测待开发 |
| Phase 3 | web_fetch 与企业资料核验策略 | 🔧 进行中 | HTML/PDF读取已实现；实验逐目标控制与结束复核已撤回，Skills串联另行负责 |
| Phase 4 | 同批质量评测与上线配置选择 | 📋 待开发 | 金牌8、全筑2查询/源；企业QQ漏报及结论已纠正（调研 §11.2）；规模质量及p95待做；未部署 |

> 日期：2026-10-13。关联：[调研](../research/agent-web-search-harness-research.md)、[设计](../system/agent-web-search-enhancement-design.md)。

## 当前下一批顺序（2026-10-22，前三项已授权）

用户明确排除当前浏览器工具，后续研究装配/探针不调用browser_automation或其旁路；浏览器重构单列，不在本批顺手修复。现有生产装配尚未因本次文档修改而停用。实施清单见[设计§12](../system/agent-web-search-enhancement-design.md#12-当前补齐清单与实施顺序用户排除现有浏览器)。

1. 修web_search结果保留与正文落盘/回读：每条来源元数据完整，不再任意掐断序列化JSON。
2. 加无浏览器依赖的web_fetch：普通HTML与文字型公开PDF正文、来源元数据及明确错误。
3. 接证据/交付验证：工具类型真实、CSV可解析、缺证据不算合格；通用校验与skill业务规则各自归属。
4. 修skill_execute持久workdir、脚本路径与文件工具失败回执，使原skill分步执行可复用同一工作目录。
5. 前述通过后再评测定向查询与互补provider；DeepSeek保留默认，暂不做全源轮询/大规模融合。

前三项作为本轮最小研究闭环，用原海外样本重新验收，并断言实际浏览器调用数为0。完整skill随后加第4项；不能以增加max_iterations/max_results或移除所有长度限制代替以上修复。Phase 1D按涉及的工具装配/租户/出网风险执行高风险流程，独立测试与CR收口后进行主体复跑。

## Phase 1D 来源、公开文档与交付校验（2026-10-22）

范围对应设计§13及用户授权的前三项。主控实现Search来源投影、可信会话正文、Read接线、规则/CSV验证及可选完成检查；单独开发者实现无浏览器fetch，独立测试与独立CR分别验证。没有修改Device API、binding、Runtime wire协议或浏览器生命周期。

1. 来源列表按条目压缩预览，保留全部URL/类型/时间/来源编号与正文路径，标记免除通用JSON截断。正文在可信tenant/user/session作用域落盘，原始工具事实保存哈希；Read支持分页和外部可信storage挂载，其他会话不获读取权限。无会话直连调用保留原返回兼容。
2. web_fetch限公开HTTP/HTTPS标准端口，逐跳校验全部DNS答案并固定公网IP，原Host及TLS域名校验保留。5MiB下载、100页PDF、100万字符、40秒总时限；HTML/PDF解析使用可终止子进程并发上限2。登录挑战、动态页面、扫描/加密/坏PDF、压缩或非支持媒体等返回明确类型，无浏览器/OCR/第三方回退。并发/存活时间有界，不宣称具备跨平台硬内存沙箱。
3. research_validate实际解析UTF-8 CSV，检查列、来源ID/URL/真实类型、非空原文摘录及SHA；email/qualification等规则由skill传JSON。拒绝打码/模式邮箱、无姓名证据的个人归属、未知规模冒充合格；数字须在资格摘录中。它核对文本一致性，不能替代商业语义判断或证明人员现任性。研究会话内write产生的CSV需当前字节校验通过才能正常结束，文件/规则改动后旧通过失效；普通无研究事实会话不受影响。默认临时CSV仅凭当前执行真实write事实授权校验，使用既有edit修正，不扩展任意临时文件权限。

开发自测已证明真实kernel/dispatcher/executor及Search HTTP接线、HTML子进程、Read、Write/Edit、Validate形成修复闭环。首轮主体诊断发现Read长正文仍受通用截断影响，新增研究正文整行6000字符分页及准确next_hint；独立新增6例验证100KB/400行无遗漏，普通Read不变。最终独立回归221 passed、0失败/跳过，主控最终相关316 passed、0失败/跳过；两组有重叠，不相加，既有弃用/依赖告警保留。独立CR最终无未解决P0/P1/P2，源码import/工具发现及定向diff检查通过。未提交、未部署。

最终主控命令为宿主venv/Python3.12、UTF8模式，经`scripts/dev_test.sh`执行：`tests/integration/test_research_harness_tools.py tests/unit/test_agent_engine_acceptance.py tests/unit/test_agent_engine_resume_input.py tests/unit/test_tool_result_truncation.py tests/unit/test_engine_observation_timing.py tests/unit/tools/test_web_fetch_tool.py tests/unit/tools/test_research_evidence.py tests/unit/tools/test_research_validation.py tests/unit/tools/test_read_tool.py tests/unit/tools/test_deepseek_search_tool.py tests/unit/tools/test_search_tool.py tests/unit/test_tool_registry.py tests/unit/tools/test_tool_assembly.py tests/integration/test_tool_execution.py -p no:cacheprovider -q --tb=short --disable-warnings`，316 passed/0 skipped，16.25秒。DB/Redis/日志库均localhost:1隔离，网络仅HTTP/DNS边界替身，HTML/PDF子进程及文件/校验真实执行。独立测试选择research/read/search/deepseek/fetch/新integration七文件，221 passed/0 skipped，15.46秒。

真实同题由`scripts/probes/b2b_harness_probe.py --case gcc_search --entry main`经公开master入口执行，570.30秒、20轮、8搜索、15Fetch（7成功8失败）、0浏览器/0委派。Search/Fetch/15次Read均完整进入模型上下文，73份来源SHA与17个原skill文件及12个最终代码SHA一致。生成17列6行CSV与evidence.md，实际research_validate和独立重验均通过；全部pending、严格合格discovery 0/3、个人邮箱0。20轮耗尽、无最终答复，业务未完成，不把文件格式通过当业务通过。

开发诊断轮还暴露finish_reason=length空回复却completed、无产物的问题；本批完成检查只覆盖已经write的CSV，未覆盖必需文件声明/缺失、通用finish_reason处理及业务字段的全部语义。Ideal错误域名被报告成正确官网失效、Salumco第三方报道被混称官网，均登记为未解决语义差距。真实官方PDF因5MiB限制失败，动态页面/403不回退浏览器。下一阶段以[调研§9](../research/b2b-lead-gen-workbuddy-compatibility-research.md#9-前三项增强与主体同题复验2026-10-22)及[实验P7](plan-b2b-harness-probe.md#p7-已授权的前三项与复跑)为准；不扩张本批为Runtime重做、全源融合或自动部署。

## Phase 1E 完成、身份和有界PDF增量（2026-10-22）

按设计§14及用户授权实施，不涉及设备/公共wire变更。高风险流程保持开发、独立测试、独立CR三角色；浏览器、部署和提交均不执行。

1. 主体缺省50轮，保留显式profile限额；模型请求显示进度，半程保存、末段校验交付。
2. length/空回复不能完成或执行残缺工具；连续两次不完整明确失败，取消优先，轮数耗尽返回可见未完成说明。三家adapter必须保留length。
3. research_delivery v1或research_plan声明实际必需文件并冻结规则，checkpoint持久；零产物/过期CSV或MD/弱化规则不能过完成门。
4. 官网host、公司原文和品牌域名一致；人数完整语境、制造原文、合格国家枚举由规则控制；确定性MD只写真实已派发查询和精确失败URL。
5. PDF25MiB/可配50MiB，HTML5MiB、总时限90秒及现有出网/解析边界；验证超5MiB真实文本PDF解析与超限流式拒绝。
6. 独立测试/CR收口后冻结代码哈希，经主体公开入口执行原gcc_search（原ZIP、15搜索、50轮），独立检查实际交付和合格客户，分别登记。

开发、独立测试及CR已完成。主控定向103项通过；独立首批178项、装配/恢复114项、CR修复后最新239项（含52个新交付用例、provider解析→引擎6组合与取消竞态）通过，均0失败/跳过，集合有重叠不能相加。PDF开发自测66项通过，独测首批已含Fetch；HTML/PDF解析进程真实运行，网络/DB边界替身。均用宿主venv与正式scripts/dev_test.sh，未减生成参数。

独立测试最新命令为相关delivery/completion、provider_structural_validation、deepseek_provider、qwen_thinking_cache、provider_http_failover六文件，239 passed/0 skipped，4.05秒。CR修复并复核：adapter覆盖length、模型返回期间取消、第三方域名重贴官网、country未知冒充合格、staffing前缀误计人数、prepared查询冒充真实调用。最终无未解决P0/P1/P2；定向diff检查及隔离源码import/四研究工具发现通过（DeepSeek默认、PDF25MiB/90秒），告警为既有Pydantic/requests依赖问题。

独立最终命令（venv PATH、UTF8、DB/Redis/日志localhost:1隔离）：`scripts/dev_test.sh tests/unit/tools/test_research_delivery_v2.py tests/unit/test_research_completion_status.py tests/unit/llm/test_provider_structural_validation.py tests/unit/llm/test_deepseek_provider.py tests/unit/test_qwen_provider_thinking_cache.py tests/unit/llm/test_provider_http_failover.py -p no:cacheprovider -q`。PDF和跨工具回归的首批178、装配/恢复114验证不与239相加。

正式复跑目录为本机临时aid-b2b-gcc-closure-50-20261022/gcc_search，经Agent(is_master=True).process_message，原ZIP/brief、15搜索/50轮、3600秒保护时限、0浏览器/0委派。768.42秒、19轮、11搜索（51 full_text）、9Fetch全网络失败。第13轮write、第14轮校验拦错、第15轮修复、第16轮通过并生成MD、第19轮非空答复，正常stop。规则/CSV/MD、51来源、17原包文件与21源码SHA独立核对一致，真实context/ToolFact重验通过，completion_errors为空。

业务严格合格仍0/3 discovery、0/6整体。3 discovery均未尝试官网直读，9次读取实为7次fixed官网+2次第三方；有剩余资源仍提前结束，属于尚未接入声明式业务目标/逐企业取证覆盖的研究策略缺口。最终自由文本仍把未尝试混称失败；普通evidence.md Read存在12944→8035字符通用截断，实际文件完整。以上列为后续范围，没有通过改产物、放宽门槛或追加未授权功能掩盖。大PDF真实目标本轮未下载，仅6MiB解析与受控网络fixture验证通过。详见[调研§10](../research/b2b-lead-gen-workbuddy-compatibility-research.md#10-四项补缺与50轮主体复验2026-10-22)和[探针P8](plan-b2b-harness-probe.md#p8-完成身份pdf与50轮复验)。技术开发与实验执行完成，业务未通过；未提交、未部署。

## 本轮增量范围（2026-10-22）

用户明确要求接入 DeepSeek 并作为默认 search provider。按高风险流程：主控开发/自测 → 独立测试与只读 CodeReview → 主控整合。接入独立 Search API，沿用 SearchRequest/注册表，不新增工具名、自动回退或模型调用；Fetch 留在 Phase 3。

同步 Python 配置缺省与 YAML 默认，保留 SEARCH_PROVIDER 显式覆盖；复用 DEEPSEEK_API_KEYS / llm.deepseek 的首个有效密钥，不使用自定义 LLM base_url。basic 返回长摘要，advanced 请求正文并明确正文缺失后的摘要类型。域名转换操作符并严格后过滤；相对时间使用 UTC 滚动 1/7/30/365 天截止时间，after 日期扩大候选后逐条核验日期，未知/无时区/过期结果不冒充满足时效。拼接后的 query 超过 128 字符明确拒绝，不截断或丢约束。

验收：默认路由 DeepSeek、其他三源显式选择可用；缺 key 不自动回退且不妨碍启动；请求 JSON/鉴权/端点正确；结果映射、域名边界、日期边界、零匹配、非法响应和 401/402/429/5xx 脱敏；总超时和取消关闭 client；ToolExecutor 真实组件组合及配置/import/schema 隔离检查。仅做定向回归，不部署或提交，不把 mock 验证等同于真实召回质量提高。

### Phase 1C 验证记录

- 环境：本项目 venv / Python 3.12.6 / httpx 0.28.1，Git Bash 运行 scripts/dev_test.sh；测试数据库、日志库及 Redis 指向 localhost:1 隔离，HTTP 使用 MockTransport。
- 主控首轮：test_deepseek_search_tool、test_baidu_search_tool、test_search_tool、test_zhipu_search_tool、test_search_provider_independent、test_config 及 integration/test_tool_execution，共 154 passed / 0 skipped。随后新增 DeepSeek ToolExecutor basic/advanced 组合检查，集成文件 8 passed / 0 skipped。
- 独立测试：不含既有 test_search_provider_independent 的六文件范围 140 passed / 0 skipped，另有默认 YAML、四源显式选择、无密钥构造和域名绕过探针。
- 独立反例发现 P2：超大整数 rerank_score 令 math.isfinite 溢出。已改为先校验 0–3 范围，再检验有限值；新增六个无效评分反例。主控 DeepSeek 单元 + 搜索集成 53 passed / 0 skipped；独立相关用例 10 passed / 35 deselected / 0 skipped，原溢出探针通过；CR 复核闭环，无遗留 P0/P1/P2。未受影响测试结果复用。
- 主控 compileall 与隔离配置/import/schema检查通过：Python/YAML 均默认 deepseek，注册四源，无密钥构造不联网，Agent schema 无 provider 参数；未启动业务调度或全服务。既有 Pydantic 弃用和 RequestsDependencyWarning 未扩大范围处理。
- 真实调用共五组：华为公司简介、全筑邮箱、金牌董秘邮箱、全筑全称限定 qcc.com、全筑+朱斌+邮箱。均成功，前三组各 5 条、后两组各 10 条；单次约 0.80–1.02 秒，样本时延不代表 p95。金牌 advanced 返回正文；全筑通用查询前五条未命中旧验收 QQ 邮箱，补人名后第 3 条投资界公司页摘要命中，但仅证明公司公开邮箱，未证明负责人本人使用。费用按现价估算 0.025 元，未核对实际账单。
- API 快照（不含请求头/密钥）留在本机临时目录 aid-deepseek-search-smoke-20261022.json；附件 B2B skill 的兼容性与完整任务效果另见[评估](../research/b2b-lead-gen-workbuddy-compatibility-research.md)。未安装附件、提交或部署；Fetch、批量、融合及整批业务验收仍待开发/执行。

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

首期增强公共互联网中文网站、企业公开资料和时效信息。开发前阅读后端、测试规则及关联设计。涉及通用工具、异步网络请求、取消和身份边界，代码阶段按高风险流程：主控开发和自测，独立测试智能体与独立 CodeReview 智能体验证，主控整合。设计初稿阶段仅文档；已开发增量的实际范围和验证记录以本计划进度及各 Phase 记录为准。

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

## Phase 1F 真实Fetch能力与服务端正文回退（2026-10-22开始，2026-10-23继续）

按设计§15执行高风险流程。实现、最终独立测试及独立CR已完成；主体业务实跑24轮失败，业务效果不以单元测试代替。

1. 原生诊断：原域名HTTPX、固定IP/SNI、curl/Schannel与stdlib TLS均复现目标失败，Baidu控制可通；未关闭证书验证，未将错误归咎HTML/PDF解析。
2. 远端选型：官方DeepSeek Fetch实测4目标仅2成功，且Industrial正文遗漏人数；现有Tavily Extract advanced同4目标全部返回正文，保留155/Manpower与PDF。默认fetch.provider=auto、remote_provider=tavily；search仍deepseek。native成功不调用收费远端，只在网络类错误时尝试选定远端一次；显式native/remote和env覆盖可用，不按密钥暗中换服务。
3. 安全/完整性：固定API、初始URL和全部公网DNS准入、单请求/总90秒、远端JSON6MiB及正文100万字符、压缩体读前拒绝；API错误不含密钥或原始错误正文，取消释放。导航链接有界写入来源正文，URL仅规范化path尾斜杠而不丢query/fragment。Tavily PDF的null标题转换为空，不丢实际正文。
4. 证据语义：provider_fetch、最终URL/源站时间未知及回退尝试保留进真实ToolFact和证据清单，不升格为direct_html/pdf或官网verified。原生25MiB PDF和SSRF/TLS/解析边界不变，远端原文件大小/页数不作伪保证。
5. 验证：最终独立官方入口9文件341 passed、0 failed、0 skipped，16.76秒，4条既有Pydantic警告；覆盖native/remote/routing/evidence/validation/delivery/integration/assembly/config。独立CR最终复核无剩余P0/P1/P2；压缩、导航链接、URL query、PDF null标题及Markdown人数正反例均收口。网络边界为mock，真实提供商由双路径实跑另验。
6. 正式ToolExecutor双路径：auto模式原9URL 9/9，加Industrial/PDF为11/11，15.41秒且均原生；显式remote也是11/11、5.67秒，155/Manpower与PDF正文保留、逐页Read无遗漏、11行来源校验通过。两个独立进程使用独立合成身份，没有代理业务Agent作决定。原生也恢复可达，不能把昨日0到今日11全归功代码；远端成功独立验证了新增路径。原始远端首跑10/11暴露null标题已修，不删除失败证据。
7. 主体同题复跑24轮失败：仍公开master、50轮/15搜索/原ZIP和业务brief。操作提示新增provider_fetch区分与逐家公司取证，人数词表补manpower（不降低50门槛、不注入企业答案）。manifest记录22代码SHA；更早诊断跑在PDF修复后中止重启，保留中止记录，不当成正式成绩。

独立代码测试及CR已通过；未提交、未部署。正式业务结果见调研§12.3：12/14 Fetch成功，但CSV10错未修、连续16K截断后明确failed，无MD与合格交付；Phase1G另修已定位的Pro能力表缺项。不把读取成功直接叫合格客户。

最终源状态补记：真实来源中的**155** Total Manpower曾被人数语境检查误拒。语义匹配现忽略成对Markdown强调，来源摘录/哈希不改；新增5个正反例及受影响validation/delivery/routing三文件151 passed/0 skipped，5.21秒，与293集合重叠不相加。两条真实工具正文均验证155/manpower匹配成功。主体最终目录调整为aid-b2b-fetch-recovery-verified-20261023，先前两诊断目录保留中止原因，避免混用代码SHA或成绩；最终独立测试及CR已通过。

最终独立测试命令（宿主venv/Python3.12、UTF8、DB/Redis/日志localhost:1隔离）：`scripts/dev_test.sh tests/unit/tools/test_fetch_service_routing.py tests/unit/tools/test_remote_fetch_tool.py tests/unit/tools/test_web_fetch_tool.py tests/unit/tools/test_research_evidence.py tests/unit/tools/test_research_validation.py tests/unit/tools/test_research_delivery_v2.py tests/integration/test_research_harness_tools.py tests/unit/tools/test_tool_assembly.py tests/unit/test_config.py -p no:cacheprovider -q`。独立测试未调用外部网络或部署；独立CR只读，不重复跑测试。

## Phase 1G 主体模型生成上限缺表

设计§16。已定位Pro未配表导致16K截断，先补配置与真实YAML→网关→provider回归，再独立测试/CR。保留Phase1F正式失败样本，下一轮只改变能力表与审计元数据，仍50轮/15搜索、无浏览器、不注入答案。开发与业务验收分别记录。

Phase1G增量开发/独立测试同官方2文件命令：`scripts/dev_test.sh tests/unit/test_llm_gateway_max_tokens.py tests/unit/test_config.py -p no:cacheprovider -q`，分别40 passed/0 skipped（2.77/2.74秒），独立CR无P0/P1/P2。测试真实YAML经Gateway到provider边界；原显式值/未知模型回退不变。冻结后已启动aid-b2b-fetch-pro-limit-20261023同题复跑；原24轮失败成果不修补、不覆盖。

Phase1G运行中检查点：第11/13/17轮分别生成31588/28230/28793 completion tokens，均返回工具调用，实际超过旧16K；第11轮首CSV、第12轮8错，第17/18轮主体自行重写CSV/MD，第19轮降至4错。模型能力表、Gateway及22源码哈希仍一致。此时尚无合格交付，不能把纠错进展写成完成。

Phase1G正式结果：1686.08秒/35轮/8搜索/12Fetch8成功（7html+1pdf），模型已实际超过16K后完成工具调用；六次Validate错误8→4→2→2→1→0，主体自行修CSV/生成MD/交付，无主控补写。最终3qualified/3pending，其中discovery仅Alusystems1家/沙特，三家及两国目标未达且剩15轮/7搜索就停止；技术completed不能冒业务达标。source/manifest/两文件哈希、原包及模型投影见调研§12.4，生成MD普通Read截断遗留明确记录。独立最终业务审计待收口；未提交、未部署。

## 当前范围收敛（2026-10-23用户明确纠偏）

先只验收Web Search与Web Fetch。Search以固定查询的官网/规模/联系人/官方PDF覆盖、原文等级准确和完整返回为准；Fetch以固定URL的HTML/PDF正文、人数/邮箱/导航保留、错误与来源可解释为准。已有Fetch auto和remote各11/11只代表该批目标读取通过，不自动代表Search已达标或总体成功率。其他Skill加载、结构化交付、语义资格、业务目标门禁问题保留调研记录，暂不扩展实施；停止整Skill复跑。

组件验收检查点：Search固定7查询/4.92秒，7/7调用成功、30条正文保存和Read通过；但站内人数0、官方PDF查询为无关同名噪声、Ideal官网联系人未命中，检索内容覆盖未达标。真实参数及URL/SHA：[元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)。后续仅围绕这些Search召回缺口和已有Fetch边界开展组件增强，不启动整Skill测试。

## 改动清单与提交建议（2026-10-23，只读审查）

用户要求梳理Search/Fetch以外改动的原因、通用性与提交边界。本节仅登记当前工作区审查结论，不进行代码拆分、暂存、提交或部署；此前独立测试通过不等于整批改动都适合默认启用。

| 分组 | 文件/工具 | 修改原因 | 通用性与提交建议 |
|---|---|---|---|
| 搜索 | search_tool.py、providers/deepseek.py、registry.py、settings.py、config.yaml | 接DeepSeek并默认启用；保留来源类型、完整URL与可回读正文 | 通用，纳入Search/Fetch基础能力包；search_tool的强制研究CSV提示需与暂缓的交付工具拆开 |
| 抓取 | fetch_tool.py、fetch.py、remote_fetch.py及Fetch配置 | 读取HTML/文字PDF；有界原生与选定远端；记录失败与读取方法 | 通用，纳入基础能力包；11URL双路径通过是样本结果，不是全网保证 |
| 证据与Read | evidence.py、read_tool.py | 防止搜索结果/长正文被通用截断；按用户/会话隔离、SHA及分页回读 | 通用且是Search/Fetch依赖，应一并保留；Read仅研究正文走特殊分页，普通大MD截断仍未解决 |
| 模型正确性 | deepseek/qwen/zhipu三个LLM adapter；engine.py不完整输出处理 | 保留finish_reason=length；不执行残缺工具；空回复/截断不误报完成；耗尽有可见说明 | 通用正确性修复，建议独立提交，须从同文件的研究交付完成检查中拆开 |
| 参数与进度 | context_assembler.py的MASTER默认50；config.yaml的Pro393216；executor.py进度注入 | 用户明确要求50轮；Pro缺能力表回落16K；模型获得剩余轮数 | 前两项可独立保留；进度概念通用，但当前提示含CSV/文件交付策略，不宜整段当通用策略默认提交 |
| 研究交付 | research_plan、delivery.py、context_assembler绑定、tools.py上下文及engine.py完成检查 | 声明必需文件、冻结规则、拒绝缺失/改动后未复验的产物 | 部分通用但触发范围过宽，暂缓默认合入；应显式选择研究交付才触发 |
| CSV与业务语义 | research_validate、validation.py、identity.py、report.py | 解析真实CSV、比对原文/规则；官网、人数、邮箱资格；生成确定性MD | CSV/来源核对可复用；人数/官网规则已有保守误拒，MD列名和措辞固定为获客业务，不是通用完成验证，暂缓整体提交 |
| 实验资产 | scripts/probes三脚本、probe审计单测、docs/research实验JSON与研究/计划 | 重现对照并审计真实来源/代码/产物 | 可选实验包，和生产功能分别提交；整Skill复跑已按用户范围调整停止，不作为生产依赖 |

已确认没有修改write/edit/cp/skill_execute本体、浏览器工具、原附件Skill或新增桌面Runtime；这里修改的是现有Agent core与Runtime适配层，影响范围不能仅按工具个数估算。WeCom本地数据库及相关调研等其他工作区改动不属于本任务提交范围。

关键提交阻点：ToolDispatcher.validate_completion在有搜索来源且research_validate已装配时即启用，即使没有research_plan或入口research_delivery声明；completion_errors会将本次write产生的全部CSV要求research_validate。用户只想搜索后导出普通表格，也可能被强迫补研究来源列。这是对适用范围的审查结论，不是Search/Fetch接口必需条件；应收敛为显式启用，不能因为定向测试通过就整批默认合入。

建议提交边界：①Search/Fetch＋证据与Read依赖；②通用模型截断/空输出正确性修复；③用户要求的50轮与Pro能力表配置；④实验资产（可选）。研究交付/业务资格层暂缓；①②③存在同文件混合改动，提交前需按代码与测试依赖实际拆分，并验证拆分后的状态，不能简单按文件名一次git add。此次没有执行拆分或提交。

## 待用户审核的首批提交范围（2026-10-23）

此为按修改点划定的候选清单，不代表工作区整文件可直接git add；暂未拆分、暂存或提交。用户先审核范围，之后实际拆分并验证拆分后的源码与测试。费用使用实际用量/准入管理，不能通过压低单次max_tokens控制预算；较大上限是容量，不是目标输出长度。

1. Search/Fetch基础能力与正文回读：src/services/web_search/providers/deepseek.py、registry.py、fetch.py、remote_fetch.py；src/tools/search/search_tool.py、fetch_tool.py；src/config/settings.py与configs/config.yaml中Search/Fetch配置；evidence.py的来源保存/会话隔离/哈希/回读部分，以及src/tools/file/read_tool.py的研究正文分页。排除搜索/抓取提示中强制CSV/research_validate的要求，不依赖业务交付工具。
2. 通用不完整模型输出修复：src/llm/providers/deepseek.py、qwen.py、zhipu.py保留length；src/core/agent_engine/engine.py只取空/截断响应恢复、禁止执行残缺工具、耗尽时可见说明。排除该文件中的research_validate完成检查与相应输出时序变化。
3. 主体默认50轮：src/services/agent_runner/runtime/context_assembler.py仅取MASTER无显式profile时20→50这一处；显式profile限额及其他模式保持原值。排除research_delivery绑定。
4. Pro生成容量：configs/config.yaml仅补llm.model_max_tokens.deepseek-v4-pro:393216（384K）；原本缺表回退16384。tests/unit/test_llm_gateway_max_tokens.py新增3个参数化场景，实际YAML经Gateway到provider验证393216/显式4000/未知模型16384。主链路真实运行最大单次31588 tokens且正常返回工具调用，验证不再卡旧16K。

测试范围与上面功能对应：test_deepseek_search_tool.py、test_web_fetch_tool.py、test_remote_fetch_tool.py；test_fetch_service_routing.py与test_research_evidence.py仅保留Search/Fetch/Read相关案例；test_config.py、test_baidu_search_tool.py及integration/test_tool_execution.py必要兼容改动。通用模型测试从test_research_completion_status.py排除进度提示案例；50轮断言从研究交付测试拆为独立配置测试。不能直接带入依赖暂缓模块的整文件测试。

本批排除：research_plan/validate_tool.py、delivery.py、validation.py、identity.py、report.py；runtime/executor.py进度提示、runtime/tools.py研究上下文和完成检查；整Skill探针、审计脚本、大批实验数据（可另行归档）；WeCom等非本任务改动。必要设计/计划与测试随相应生产改动提交，文档不能把未提交的研究交付层描述为本批已经启用。

输出上限现状如实记录：这次未改gateway解析规则，仍为显式max_tokens优先→命中模型配置→未知模型16384；chat_lite旧默认16384也未改。故本次是Pro主模型缺表修复，不是全系统小上限清理；这些遗留路径不能冒称已按新原则统一整改。用户后续要求统一时再按模型支持容量审计，不能为费用目的继续缩小上限。

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


## 跨会话工作指南（2026-10-23）

首批65f69db9已推送Codeup/GitHub master。新会话从[后续工作指南](../guides/agent-web-search-continuation-20261023.md)接手，先隔离已提交基线与未提交研究原型，继续组件级定向召回诊断；不恢复完整Skill流程。本次仅新增交接文档及临时清理，原进度登记区继续作为唯一开发进度记录。清理详情与证据保留边界见指南第6节。

## 七查询诊断后的验证顺序（2026-10-23）

用户要求先诊断关键词、其他参数和已有结果利用，不修改代码。完整分析和可执行对照表见[调研§13](../research/agent-web-search-harness-research.md#13-七查询离线诊断与验证方案2026-10-23)，继续维护本计划，不另建进度文档。

离线复核30份历史正文SHA全部一致；Ideal公司页已含候选官网域名，Q7已返回Al Abbar制造能力页，发现查询还能从正文后段提取更多域名。旧邮箱布尔指标会命中打码样式，必须区分线索与完整证据。当前未证明adapter有丢项bug，也没有评价Agent自主关键词或后续决策的实验依据。

下一步先验证已有来源→候选入口→Fetch→Read的利用路径；再做原七查询与七个候选变体的同批对照，必要时增加最多三对参数诊断。首轮不超过20次Search、首批5次Fetch，尚未执行；同一HTTP响应逐层对照，已知URL/人工选路与Agent自主能力分开记录。供应商覆盖、查询策略和结果使用问题不自动转成代码开发任务。

基线核对：本地master/HEAD=65f69db9；fetch后Codeup origin/master=0442891e，领先一个PPT Skill提交，未涉及Search/Fetch关键链路；GitHub master仍65f69db9。本轮不合并远程。主工作区原18个已跟踪修改和49个未跟踪文件保留；新隔离目录C:/Users/PC/.codex/worktrees/search-recall-baseline/aid-work-agent固定65f69db9，已核验工作区干净且六个研究交付原型文件不存在。隔离目录没有本轮未提交诊断文档，评测时以主工作区本计划和调研为操作依据，源码仍从隔离目录加载。

### A. 已有线索利用实测完成（2026-10-23）

用户仅授权Fetch/Read跟进，未授权关键词对照或代码修改。隔离65f69db9使用真实ToolExecutor、仅Fetch/Read，合成身份及DB/Redis/日志localhost:1；未启动Agent/Skill/浏览器/Validate。首批5入口、再沿本次实际导航跟进5个目标，10条完成记录9成功（8HTML/1PDF）1拒绝；排除Industrial种子对照后，线索链9目标8成功1拒绝，成功最终URL去重8个。9份来源共29页Read，逐行完整性和SHA全部通过；全部完成尝试原生，无完成的远端调用。

取得Ideal官网具名联系人/职位与邮箱文本，集团官网实际链接的16页历史PDF，以及集团→Industrial子公司页→独立官网的自然链和155/Total Manpower原文；ALUSYSTEMS/Al Abbar取得制造和规模自述。联系人时效、人数日期/集团口径未核实；Ideal PDF明确pdf_too_many_pages，不绕过100页边界。首轮临时校验器中断与已完成Fetch复用、未完成目标重跑均保留，不将中断计HTTP失败或伪报整体时延。

详见[调研§14](../research/agent-web-search-harness-research.md#14-已有搜索线索fetchread实测2026-10-23)及[逐条元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)。仅更新文档/实验元数据，不改生产代码、配置或仓库脚本。B关键词和C参数对照未执行；不把本次人工跟进结果冒称Agent已自主解决或完整Skill达标。

### B. 七查询关键词同批对照完成（2026-10-23）

用户授权仅关键词调整，保持DeepSeek/max_results=5/advanced及其他参数；隔离65f69db9只注册Search/Read，无Fetch、Agent、Skill或浏览器。七个原查询与§13预定七变体交错同批执行，14次调用成功，原查询30条、变体25条，共55条full_text。14份同次供应商URL列表与工具结果一致；55份供应商正文经既有换行规则后与落盘SHA一致。最终有效67页Read，53份文本一致，2份ALUSYSTEMS记录因UTF-7误判改变增长百分比，不能宣称全链路通过。

发现查询删除employees后，独立候选沙特3→5、UAE4→5，合计7→10（约43%，仅候选）；MNAF3全名变体新增独立域名及带年份人数线索；Ideal全名小幅消歧。PDF题正确实体相关页0→4/5，仍无目标官方PDF；site-only Industrial继续0，Ideal site变体5→0。详细增删URL、丢失证据和判定见[调研§15](../research/agent-web-search-harness-research.md#15-七查询关键词同批对照2026-10-23)及[同批元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)，不合并成单一成功率。

Read问题独立定位：已保存UTF-8正文被chardet7.4.3以0.95置信度判为UTF-7，六行百分比损坏；Python3.12.6本地同源码复现。Q6变体首次Read断言中断，复用Search结果后记录差异继续，未额外Search。13调用完整计时、1调用计时缺失，供应商无usage返回，不写整体时延或费用成绩。此问题不影响本批企业计数/域名结论，但需另行修复授权；本轮未改代码、配置或仓库脚本。C参数验证、Read修复及Agent自主选词仍待开展。

### C. 参数对照冻结方案（2026-10-23）

用户授权继续参数对照，固定检索词，不改代码，不处理Read编码问题。复用干净65f69db9隔离源码及Search/Read真实链，合成身份和DB/Redis/日志localhost:1。三组关键词分别固定，不在本轮追加语义变体；无Fetch、LLM、浏览器或自动换源。

| 组 | 固定词 | 对照 | 目的 |
|---|---|---|---|
| 数量 | Saudi Arabia aluminum windows curtain wall manufacturer employees | max_results=5/10；advanced | 比较新增实体、重复与噪声、原前五条保留情况 |
| 深度 | "MNAF3 Industrial" Saudi Arabia | basic/advanced；max_results=5 | 比较同源URL排序与摘要/正文中域名、人数线索的可得性 |
| 域名 | "Ideal Aluminium" Dubai contact manager email | 手写后缀site:idealagdubai.com，max_results=5/50；相同词以include_domains传域名，max_results=5 | 记录实际query/count；手写50与include的上游50对齐，再区分供应商候选与工具后过滤 |

核心7次Search。若目标域名三请求仍为空，再使用此前已返回多条公司页的signalhire.com进行手写site/max_results=50与include_domains/max_results=5两次对照（关键词不变），总计最多9次。后者是已命中来源域名对照，不承诺该site查询必有结果；按预先条件运行，不择优报告。实际出站JSON在请求前落盘，原始响应保存在本机Temp，仓库仅登记脱敏元数据；Read逐字差异记录后继续，不修代码或把损坏文本用于事实判定。

### C. 参数对照完成记录（2026-10-23）

按冻结方案完成9次Search，所有实际query/count/need_content请求前落盘，条件参考组按目标官网三次上游空结果触发。固定词数量5→10保留原前五条，候选主体名3→7，新增Alugate/Uaac Sa/Facade Technology/AFAL；其中Uaac Sa正文混有Internal Server Error，需复核，候选不等于合格客户。basic/advanced五条来源顺序相同，正文3,298→8,398字符，域名/155/2024关键线索两者均有。Ideal手写site的count5/50及include count50均上游0；SignalHire手写50/include上游50实际请求相同、返回同样5条，但仅3条目标实体相关。

35来源（30 full_text、5 summary）供应商→保存SHA全部一致，9请求URL列表无丢项；有效Read35页，33份逐行一致、2份复现既有UTF-7误判，未修代码。来源/实际请求/差异见[调研§16](../research/agent-web-search-harness-research.md#16-固定关键词的参数对照2026-10-23)及[参数元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)。所有源码来自干净65f69db9隔离目录；只更新文档/元数据，无Fetch、LLM、浏览器、provider切换、提交或部署。多域名/排除/时间过滤、其他provider、Read修复和Agent自主选择均未开展，不将本次有限参数样本外推整体达标。

### Read编码最小修复计划（2026-10-23，用户授权）

范围见设计§17，只修改Read编码和相关回归，不处理搜索参数或其他未提交原型。主工作区read_tool.py与test_read_tool.py修改前均与隔离65f69db9一致；实现同步到既有隔离目录运行相关测试，避免研究门禁原型混入。宿主venv/Python3.12.6、chardet7.4.3，Docker命令不可用，使用Git Bash执行scripts/dev_test.sh的宿主路径；DB/Redis/日志localhost:1。

1. 增加确定性UTF-7误猜回归，覆盖ASCII百分比、多语言UTF-8、8192字节边界；验证真实保存→ToolExecutor/Read分页和GBK/UTF-16 BOM兼容，先确认缺陷用例失败。
2. 完整文件严格UTF-8读取优先；含NUL时保留既有UTF-16/32识别，UTF-7猜测不覆盖有效UTF-8，非法UTF-8沿原兼容流程；运行Read与相关来源分页回归。
3. 一个独立验证者在隔离源码上选择测试并只读审查本次diff，主控收口；不提交或部署。
4. 使用关键词对照元数据中Q6-original/Q6-variant原始source_id/context，经真实ToolExecutor→Read重读两份保存文件，不新Search/Fetch；逐行比对、检查六处增长率和原SHA，输出独立复验元数据。先前失败记录保持，不覆盖历史成绩。

### Read编码修复完成记录（2026-10-23）

实际代码仅read_tool.py的读取优先级，相关测试仅test_read_tool.py新增9个案例；未更改Search/Fetch、依赖、会话权限或原型文件。两文件修改前与隔离65f69db9相同，最终主仓/隔离SHA一致。测试先红后绿：旧实现4个UTF-8场景失败、2个UTF-16 BOM场景通过；首版完整UTF-8直接返回后主控76/独立142项通过，但独立兼容探针发现无BOM UTF-16英文回归P2。新增LE/BE真实字节测试在首版均失败，再保留含NUL的既有UTF-16/32识别并增加含NUL的UTF-7误猜UTF-8用例，最终关闭P2。首版成绩不作为最终代码全绿证据。

最终主控及独立官方入口均为：`scripts/dev_test.sh tests/unit/tools/test_read_tool.py tests/unit/tools/test_research_evidence.py -p no:cacheprovider -q --tb=short`，分别79 passed/0 failed/0 skipped、3.99/3.94秒，集合完全重叠不累计。独立另14个临时编码探针通过，覆盖GBK、UTF8/BOM、UTF16有/无BOM、UTF32无BOM、CRLF、UTF7误猜、采样边界和后段非法UTF8；审查无剩余P0/P1/P2。既有Pydantic弃用告警未改。早期一次同步测试文件路径错误导致50项全部deselected，已纠正后执行红灯测试，不计作有效测试结果。

主控真实ToolExecutor/Read复验原Q6-original与Q6-variant两份保存来源，各146行，原每份六处差异全部消失，全部292行与保存正文一致、原SHA未变；无新Search/Fetch/LLM/浏览器。首版复验记录保留作中间证据，最终使用[最终复验元数据](../research/experiments/b2b-workbuddy-gcc-20261022/read-utf8-fix-final-replay-20261023.json)。最终Read SHA为690e780b4b27545a8b73ac5ebec5a3b005db6182927673195ec7921a98b32e27，测试SHA为033b123acc6f215fb78f8c91f7a38b6878100d9550cc3141c4a8ec2c3a766b86。结果已同步设计§17、调研§17和索引，整体搜索任务仍部分完成；未暂存、未提交、未推送、未部署。

### 小范围自主验证完成记录（2026-10-23）

按用户新授权，在65f69db9加已验证Read修复的隔离worktree，经公开主Agent入口运行两家指定企业四项目标；空历史/空Skill/仅Search、Fetch、Read，未注入域名或事实答案，未加载主仓研究原型。10搜索/15Fetch/40Read、1200秒保护，保留50轮及Pro正常生成配置；真实177.89秒/8轮/2搜索/9Fetch/15Read，正常结束但业务仅部分完成。28来源SHA与1030行Read核对通过，26工具回执均进入后续模型输入，10关键源码SHA前后不变；本轮不改代码，不新增运行时测试。

两家官网直读、MNAF3官网155人及Ideal两名具名联系人取得；Ideal125仅第三方搜索摘要估算，非官方人数。MNAF3 PDF原生TLS失败后Tavily provider_fetch返回相关正文，未直接解析PDF；Ideal PDF因100页限制失败。初始自主选数量10，但只做2条英文官网查询，没有追加/改写或深度/域名参数调整；缺项与剩余预算并存，不能称自主策略整体通过。原始答复的证据等级、链接和“中英文查询”表述差异另登记，不改写原答复。

详见[调研§18](../research/agent-web-search-harness-research.md#18-小范围agent自主取证2026-10-23)及[元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)。下一步建议固定工具/模型/企业，仅以通用缺项补搜约束作对照，不提供答案或示例词；尚未执行。未恢复完整Skill、未暂存/提交/部署，整体状态仍部分完成。

### 通用缺项补搜对照完成记录（2026-10-23）

用户随后授权仅追加通用缺项补搜约束。本轮与上一轮的10关键源码、系统prompt、完整工具schema、模型/provider/生成容量、原brief前缀、企业/预算逐项核对一致，输入只有新user消息，无已知答案/示例词/旧结果，未中途引导。保留50轮与10搜索/15Fetch/40Read/1200秒，经同一公开Agent入口执行；实际311.05秒/13轮/3搜索/13Fetch（10成功3失败）/18Read，正常结束。38来源SHA、1150行Read及34回执进入模型全部核验通过，无新产品代码或测试改动。

第12轮确实追加Ideal人数查询，max_results由10改8；深度/域名/时间参数没有改变。补搜8URL中5个重复、3个未建立目标身份，没有后续Fetch/Read，四项关键缺口新增补齐0。SignalHire200–500与GraphIQ125冲突被明确报告，但200–500在本轮首批摘要已存在，不能归功补搜；上一轮同URL摘要没有该数字，实时波动已记录。无来源猜测idealaluminium.com消耗3Fetch，工业公司人员页线索未跟进，官方人数年份跨来源拼接及PDF完成勾仍有语义问题。

详见[调研§19](../research/agent-web-search-harness-research.md#19-通用缺项补搜约束对照2026-10-23)与[对照元数据（已归档）](../research/agent-web-search-harness-research.md#22-提交范围与实验元数据清理2026-10-26)。约束触发局部补搜，不等于整体业务通过；下一步仅建议验证逐项目标完成标准和证据归属，尚未执行。无Skill、浏览器、提交/推送或部署，整体仍部分完成。

### 清理与通用研究修复完成（2026-10-26）

用户要求直接清理并修复，本批撤销未合入的CSV/research交付原型，37个主仓文件和18个Temp目录485文件删除，12个tracked文件只恢复本任务实验差异至当前HEAD；Read修复和其他任务改动保留。恢复包在仓库外且ZIP CRC通过，主仓保留10份最终历史元数据和1份本次验收。旧中间链接改为归档说明，原型探针计划标撤销。

生产改动只在通用Search/Fetch指南及Fetch PDF页段能力：参数可选、1起、每次1..100页/显式分页默认20；原整篇100页、下载/超时/公网/TLS/子进程/取消上限保持。原页码/总页数/起止/pdf_complete经来源保存；HTML不能伪装PDF，显式页段禁远端回退。没有增加CSV门禁、业务Runtime或企业答案。主控220通过，独立352通过/0失败/0跳过及8项额外真实解析/发现探针通过，CR无未解决P0/P1/P2；集合有重叠不累计。

真实复验使用与原始中性brief逐字一致的输入、空历史/Skill、仅三工具、同模型和预算；14轮/257.98秒、3Search/8Fetch/21Read。Ideal117页PDF从超限切换为1–20与100–117原生提取；官方联系页与组织结构/联系人页得到交叉证据，MNAF3联系人触发专门查询、具名岗位线索被Read。未猜域名，但整轮仍遗漏MNAF3 PDF目标，不能冒称单轮全目标完成。

主控改用目标拆分：另以“只找MNAF3官方公司/产品PDF”中性任务从空历史独立查找，不提供已知URL；13轮/191.72秒、4Search/9Fetch/13Read，通过工业官网产品导航找到自动门64页、卷帘门25页资质册，两份各原生提取1–20页并Read相关正文。两次合计51来源SHA和2557行Read准确，58工具回执均进入模型，主仓/验收10关键源码SHA一致。明确页段提取和模型实际Read范围，不宣称整份已读；已定位范围中没有可信Ideal官方人数，第三方125与200–500继续分源记录，不伪造官方结论。

详见[调研§20](../research/agent-web-search-harness-research.md#20-清理与搜索闭环修复2026-10-26)、[验收元数据](../research/experiments/b2b-workbuddy-gcc-20261022/search-repair-acceptance-20261026.json)。最后清理本轮一次性原始快照/脚本时仅保留仓库外恢复包，不继续堆叠主仓实验资产。代码、回归及本批实际修复已完成；搜索整体质量持续按具体任务评估，不提交、不部署、不跑完整Skill。


### Agent内部逐目标执行完成（2026-10-26）

按设计§19实现research_progress内部控制、私有checkpoint账本/owner、真实Read行号与SHA绑定、首次全字段覆盖及有界缺项复核、最新24条未读线索索引与研究Fetch域名出处检查。无普通对话全局门禁、CSV/资格原型、Device/wire改变或新业务Runtime。独立发现的空引文、假尝试、同批检查时序、耗尽提示、defer暴露、未读重复行、历史回执别名、空Read、反馈洗白域名与大写HTTPS/IDNA回归已修复并收口。

最终12相关文件383通过/0失败/0跳过（16.00秒，4项既有提示）；独立最新边界8探针与64相关回归通过，其他增量集合重叠不累计；CR无未解决P0/P1/P2。最后整题以相同模型、原业务brief、空历史/Skill与10 Search/15 Fetch/40 Read/1200秒保护执行，28轮875.16秒，4/15/23检索工具调用、7内部控制调用；一次结束复核后自行读取企业与人物目录、修正记录，没有外部补漏任务。

两官网、MNA官方155、Ideal官网两具名联系人、集团16页PDF（含工业章节）及Ideal117页PDF的1–20/80–117页取得真实Read证据；MNA人物仅按集团负责人/股权关联资料标注，Ideal人数仅第三方125与200–500冲突，不升格。53来源SHA、2179行及49回执投影全部核对，14关键生产源码SHA与主仓一致。原答复对全PDF未披露/扫描页面因果的过宽表述留作语义质量记录，最终结论限定真实Read范围，不以8个evidenced声称8项官方事实。

详见[调研§21](../research/agent-web-search-harness-research.md#21-agent自身逐目标执行2026-10-26)与[最终元数据](../research/experiments/b2b-workbuddy-gcc-20261022/agent-goal-workflow-acceptance-20261026.json)。开发期失败轨迹及废弃来源已归档清理，主仓只新增一份最终元数据；不跑完整Skill、不提交、不部署。

最终归档清理完成：有效的53份来源正文保留，原始运行快照/脚本9文件已归档后删除，本轮共删除294个文本文件；恢复包`C:/Users/PC/.codex/task-archives/agent-goal-workflow-20261026-125418.zip`（313条目，CRC/SHA校验通过），SHA256 `4f97c0b88eb9487c9f399bd789fb9b64135c2aa0ae10bc09544855a52c405bac`。唯一保留中间件是Temp的1个编译缓存，删除被自动策略拒绝，不在主仓；源码、测试与一份最终元数据保留，未提交。


### 提交候选拆分与确认清单（2026-10-26）

本次仅准备和验证候选，不暂存、不提交。基于当前HEAD `f3410aa1976cf3b8542170025f39d98c0a28907e`，复用附属隔离worktree，在保存其旧研究代码快照后切到同一HEAD，仅叠加下列7个代码/测试文件；主仓现有未提交改动不回滚。

| 分组 | 候选文件 | 边界 |
|---|---|---|
| A1 建议先提交 | src/tools/file/read_tool.py；tests/unit/tools/test_read_tool.py | 修UTF-8误判，不改Agent主循环/工具装配 |
| A2 可一并确认 | src/services/web_search/fetch.py；src/services/web_search/evidence.py；src/tools/search/fetch_tool.py；tests/unit/tools/test_web_fetch_tool.py；tests/unit/tools/test_fetch_service_routing.py | PDF可选页段与元数据，不传参数保持原默认读取路径 |
| B 暂不提交 | src/core/agent_engine/engine.py；runtime/controls.py、executor.py、tools.py、research.py；src/tools/control_set.py；src/tools/search/research_progress_tool.py；tests/unit/test_agent_research_workflow.py | 新控制工具、完成复核、提示注入、域名出处约束会改变Agent执行行为 |
| B 提示词暂缓 | src/tools/search/search_tool.py的全部新增指南；fetch_tool.py中的通用研究指南3行 | 提示词同样会影响Agent策略，不因只是文字而当作零影响改动 |

A2中的fetch_tool.py混有两类改动，不能整文件暂存：候选仅含可选参数、服务传参和两行PDF页段指南，排除了URL/补搜/人物口径等通用研究指南。候选中6个Agent执行/装配/搜索策略文件与HEAD一致，两个研究新模块不存在；相关9测试文件323通过、0失败/跳过（17.52秒）。这是独立于研究流程的组合验证，不复用全工作区383项成绩代替。

候选另带设计、原计划、ideas索引3份最小文档变更，内容仅对应Read/PDF；不将主仓尚未提交的完整研究过程和B阶段说明一起提交。完整候选补丁：`C:/Users/PC/.codex/task-archives/search-read-pdf-ready-20261026.patch`，SHA256 `1008a6132667fd6bfddd66291ee8aa2d97353d2436600496a3491a05292c6bb6`。已在主仓执行git apply --cached --check验证能独立应用，未改变index。旧隔离worktree的13个研究文件已做CRC校验恢复包，不丢弃实验代码。

9份历史实验JSON已归档后删除，共532816字节，文档链接更新；保留3份最终验收依据，不作为A包运行时依赖或必须提交的代码。其他任务的授权修复、企微localdb/版本矩阵、生图部署与文档不属于本次候选，保持原样。待用户明确确认A1/A2后，再按Git规范fetch、复核分支与upstream，只暂存确认切片并提交推送；当前无提交、推送或部署。


### 模型基线更正（2026-10-26）

用户确认项目使用deepseek-flash。历史自主Agent实测实际沿本地.env覆盖使用deepseek-v4-pro，不能作为Flash自主能力/性能/费用的验收。本地DEEPSEEK_MODEL_CODE已纠正，历史model字段保留真实值；详见调研§23和元数据model_baseline_review。efef1652的Read/PDF工具修复及323项确定性回归不依赖Pro，提交未改模型选择配置。Skills串联由同事负责，本次不重跑、不部署、不新提交。

### 撤回实验研究流程（2026-10-26）

用户要求撤销B组执行流程及通用研究指南：恢复7个已跟踪文件、删除3个新增模块/测试，研究接线引用已清除。Read/PDF已提交修复和本地Flash配置保留，其他任务代码未动；前述B组候选及“实现完成”段落仅保留历史，不再接续开发或提交。恢复包及完整范围见[调研§24](../research/agent-web-search-harness-research.md#24-撤回未提交的agent研究流程2026-10-26)。

Read/Fetch回归187通过；独立引擎/工具装配回归141通过、1项因Windows符号链接权限失败、0跳过，独立审查通过。未暂存、提交或部署。
