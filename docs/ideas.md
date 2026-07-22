# 项目开发目录

> 本文件是进行中和待开发内容的索引，链接设计文档和开发计划文档，跟踪开发状态。
> 已完成的功能归档在 [ideas_finished.md](ideas_finished.md)。

---

## 状态说明

| 状态 | 含义 |
|------|------|
| 🔧 部分完成 | 已开始开发，部分阶段完成 |
| 📋 待开发 | 设计完成或进行中，尚未开始编码 |
| 💡 灵感 | 早期想法，尚未正式设计 |

---

## 基础设施

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 1 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。Phase 1 全部完成（含渠道追踪方案 C：TraceCollector 下沉到 `Agent.process_message`，从 record_service 自动读 source_type，渠道零改造）。1.6（单测/e2e）和 1.7（JSONL 双写迁移）已取消：obs 系统每日真实流量运行已事实验证；JSONL 与 obs 永久并行。Phase 2-4 未开始。2026-07-07 | [设计](infrastructure/observability-design.md) / [延伸设计](infrastructure/observability-channel-sessions-design.md) | [计划](infrastructure/observability-dev-plan.md) / [延伸计划](infrastructure/observability-channel-sessions-dev-plan.md) |

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 🔧 部分完成 | Phase 0 知识库分类管理已完成。Phase 1-2 已重排（2026-07-06）：**Phase 1（合规阻塞，2 周）= 文档级权限 + 检索日志**；**Phase 2（销售精度 + 体验，3-4 周）= LLM Rerank + 查询改写 + 质量评估（与 Rerank 配套）+ Pipeline 协调器**。重排理由：原 Phase 1 按实现依赖排序无商业化优先级，现按销售推动力重排——权限是中大型企业上线卡点，质量评估需与 Rerank 配套（否则评估的是基线无意义）。 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) |
| 42 | 租户积分充值与计费 | 🔧 部分完成 | 预付费积分（credit）充值 + 对话消耗积分 + 余额报警 + 账单查询。Phase 1 数据基础+扣费链路（`tenants.credit_balance` / `chat_records.credit_cost` / 新建 `tenant_recharges` 表 / `billing.usage_factor` 配置）✅；Phase 2 平台管理员手动充值（`/api/saas/billing/recharges` + `TenantRecharge.vue`，不可改仅可删除回扣）✅；Phase 3 租户余额/用量明细/充值记录（改造 `TenantTokenUsage.vue` 为「积分用量」、`PlatformTokenUsage.vue` 新增「消耗积分」列、新建 `TenantRechargeRecords.vue`）✅；Phase 4 余额报警三入口（`useCreditCheck.ts` composable + 前端 sendMessage 阻断 + 登录提醒 + 后端 `/api/chat`、`/api/chat/stream`、`channels/session.process_and_persist` 硬阻断）✅；Phase 5 在线支付对接（预留 `payment_orders` 复用）待开发。计费算法：`credit_cost = ceil((prompt_tokens × input_price_per_m + completion_tokens × output_price_per_m) × usage_factor)`，`usage_factor` 默认 100。扣费挂载在 `src/services/session_record.py` 写 `chat_records` 时同步原子 UPDATE。2026-07-20 | [设计+计划](system/saas/tenant-credit-billing-design.md) | - |

## 数字员工 / 子智能体

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 17 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | [设计](subagent/crm/crm_subagent_design.md) | — |
| 45 | 旅游报价数据补全（消费 Excel 模板工具） | 📋 待开发 | 解决旅游报价"内容项不全无法支撑任意版式"问题。模板填充已下沉为通用无状态能力（见 #44，样例每次必传、调用方持有），本条只管 travel-quote 领域职责。Phase1 数据模型补全（item 加 row_total/audience_split/group，QuoteData 加分类与分组小计、多人群人均）；Phase2 消费集成（QuoteData→通用 data 适配器、generate.py 带 sample_file_path 调 #44 fill_with_sample、退役自有 excel_export.py）；Phase3 travel-consultant 聊天接入（客户发样例→agent 记住样例路径→后续报价带上，全程无入库）；Phase4 领域补全（大交通机票/高铁、签证/小费/装备/税费/赠送/加床、餐饮住宿儿童差异化）。2026-07-20 | [设计](system/design-travel-quote-template-engine.md) | [计划](plans/plan-travel-quote-template-engine.md) |
| 46 | 旅游顾问行程 HTML 长图导出（图片独立行 + x-to-image 图片内联） | 📋 待开发 | 客户要求行程图片嵌在表格内，现有 Word 路径图片在表格外独立章节、且 Word 对图片布局控制弱、多客户样式难定制。改为：详细行程默认以 **HTML 长图(PNG)** 交付，CSS 精确控图；**图片独立成行**(紧跟景点信息行下方跨列)+分档布局(1张大图/2-3张等宽横排/≥4张每行最多3张换行)；**图片 base64 内联由 x-to-image 内部完成**(file_id/远程URL→base64)，智能体只写 `<img src="file_id:xxx">`；Word 降为可选备份(客户要可编辑/打印时)。本期一套标准模板，多样式延后。**关键验证(2026-07-20)**：Pandoc pipe_tables 支持单元格内嵌图(Word 备份可行)；headless Chromium set_content 禁加载本地文件→HTML 图必须 base64 内联；x-to-image 已注册(agent.py:431)、旅游顾问 inherit 已可用；agent 主循环 hasattr 钩子自动注入 tenant_id(agent.py 不改)。改动：`_image_inliner` 加 `inline_images_as_data_uri` / x_to_image models+html_renderer+tool 接入 / SUBAGENT.md 改 HTML 模板。原型已验证(1/2/3/4 张四种布局)。2026-07-20 | [设计](subagent/travel-consultant/itinerary-html-export-design.md) | [计划](plans/plan-itinerary-html-export.md) |
| 20 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；②广告管理（腾讯广告Marketing API朋友圈广告：投放诊断+策略建议+人在环受控执行+留资转化回传，不自建竞价）；③巡检商机（知乎/小红书等第三方平台，agent经browser工具以真实账号正常登录后做需求雷达/内容播种/留言接洽，商机汇总分配销售——公司第一优先级）。**已实现**：13张数据表全建、连接器协议骨架(PlatformCapability含内容/广告ADS_*/webWEB_*三类枚举/SocialPlatformConnector/ConnectorRegistry/CapabilityResolver resolve/supports/require)、SocialMediaService(账号/计划/素材/内容/审核/发布任务CRUD+幂等,已接入CapabilityResolver)、连接器契约测试(参数化覆盖全部连接器+capability_resolver单测)、21个API、1个聚合工作台前端(~20%)。**stub/未实现**：公众号/视频号连接器无真实HTTP(wechat_official声明矛盾已于S0修复,现仅声明ACCOUNT_CREDENTIALS)、AI内容生成、发布调度执行(Dispatcher/Executor/APScheduler未集成,QUEUED后不可达)、运营数据分析(仅计数)、子智能体(.disabled)、广告管理与巡检商机两模块完全未做。当前对生产无影响（页面planned、子智能体disabled）。**S0共享底座已完成(2026-07-21)**。2026-07-21 三大模块设计合并收敛为单一智能体（删除原分散的内容/广告/巡检独立设计文件，去RPA化，巡检改用agent原生web操作表述）。开发计划分S共享底座/B巡检/A广告/C内容补齐四系列+I整合，约125-155人日，推荐顺序：底座→巡检(第一优先级)→广告→内容补齐。 | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-marketing-agent-design.md) / [S1发布调度设计](system/digital-employee/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](system/digital-employee/social-media-marketing-agent-dev-plan.md) |

## 工具

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。2026-07-20 Phase 3 代码已收口：完成原 `tool_call_id` exactly-once continuation、原 Agent 后台 LLM 续跑、持久 resume worker、自动完成后台监测、人工超时 reaper、iframe 挑战识别、断线事件补取及前端轮询回收；三智能体流程和主控终检通过（browser 177、Phase 3 定向 32、Agent 相邻 12、前端组件 3，production build 与主入口 import 通过）。真实 Redis opt-in、PostgreSQL、双 Gunicorn worker、真实验证码页同 context E2E 和进程崩溃补偿仍待部署验收，故保持部分完成。架构已收口为 Agent Desktop 内置 browser runtime；Phase 4 浏览器侧只负责 `browser/1.0`、DesktopRuntimeExecutor、路由安全和联合验收。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 29 | PDF 工具质量验证增强 | 🔧 部分完成 | P0+企业文档增强+Playwright HTML 转 PDF 已完成：新增 inspect/render_pages/validate、结构化检查、生成后自动校验、页码语义统一和依赖探测；修复 split 保存顺序、无效页码静默成功、文件名安全、HTML 表格顺序、测试漂移和旧设计文档不一致问题；新增 clean_metadata/add_watermark/protect/compress/extract_images/rotate；复杂 HTML/CSS 默认优先 Playwright print-to-pdf，失败回退 fpdf2；Docker 增加 LibreOffice Writer 及构建期校验，DOCX 转 PDF 统一使用 LibreOffice 隔离配置目录并保留失败诊断。PDF 全量单测通过，127 passed；真实 Playwright 生成和结构校验通过。2026-07-01。**2026-07-15 新增图片支持**：md_to_pdf + html_to_pdf 复用 `_image_inliner`（扩展 HTML `<img>` 解析）解析 `file_id:`/远程 URL 为本地路径；Playwright 路径本地 src 转 base64 data URI、fpdf2 兜底路径用 `pdf.image` 双引擎渲染；`PdfProcessTool` 接入 tenant/user 注入（镜像 Word）。同步完成 **#34 Phase 3b 落盘闭环**（`_merge_results` 超长 content/markdown 走 `spill_large_content` 落盘，返回 file_path 供 read/grep 回读）+ **全规范核对**（错误返回接 `sanitize_error`，不再泄漏 `str(e)`）。21 新增 + 135 PDF 回归单测全绿。| [设计](tools/pdf/pdf_tool_gap_analysis_design.md)、[图片支持设计](tools/pdf/pdf-image-support-design.md) | [开发计划](tools/pdf/pdf_tool_quality_validation_dev_plan.md)、[图片支持计划](../plans/plan-pdf-image-support.md) |
| 30 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。适用场景：结构化业务数据直接生成正式报价单、报告、对账单、审批单等，要求页眉页脚、页码、签章区、复杂跨页表格和版式位置稳定。当前复杂 HTML/CSS 已由 Playwright print-to-pdf 覆盖，不优先投入 reportlab。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 32 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。当前已有结构化校验和 Playwright HTML 转 PDF，暂不投入完整样本集建设。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 34 | 工具总体优化梳理 | 🔧 部分完成 | 跨工具层面的优化梳理登记文档（区别于单工具设计）。进度：#34a `upload_to_remote` 工具直接删除 ✅（已提交）；#34b 全部 27 个业务工具 token 审查 + 规范 ✅；Phase 0/1（删工具+建规范+helper）✅；Phase 2 试点 http_api/pdf_process/paddleocr token 优化 ✅（token 对比达标，服务器验证通过）；#34c **大内容落盘检索闭环 + grep 工具** 🔧（pdf 侧已回填）——Phase 2 截断后完整内容丢弃形成信息黑洞，新增「截断→落盘临时文件→read/grep 回读」闭环（对齐 Claude Code harness），含 `_spill.py` 落盘管理器 + 新增 grep 工具（调 ripgrep）+ Dockerfile 装 rg；**2026-07-15：Phase 3b 的 pdf_process 回填已完成**——`_merge_results` 的 read/ocr/pdf_to_md 超长字段接入 `spill_large_content` 落盘，返回 file_path 供 agent read/grep 回读（http_api 落盘在先），见 [PDF 图片支持设计](tools/pdf/pdf-image-support-design.md)；#34d **文件工具对齐 Claude Code 水准** 📋——read/edit 的 offset 从 0-based 改 1-based（breaking，需适配 skill）+ edit 新增 replace_all 批量替换。开发计划已调整：Phase 3 升级为「文件工具对齐+grep+落盘闭环」优先实施。2026-07-06 | [总体设计](tools/tool-overall-optimization-design.md) / [落盘闭环+grep](tools/large-content-retrieval-design.md) / [文件工具对齐](tools/file-tools-claude-code-parity-design.md) | [开发计划](tools/tool-overall-optimization-dev-plan.md) / [落盘闭环计划](tools/large-content-retrieval-dev-plan.md) |
| 44 | Excel 智能模板填充工具（样例 + 数据 → 按版式生成） | 🔧 部分完成 | 通用**无状态**能力：任何"样例表格 + 结构化数据 → 按样例版式生成 Excel"的场景（旅游报价 / CRM 对账单 / 财务报表 / 贸易报价单）。**无状态单 action**：调用方每次必传样例（样例存储是调用方职责），工具数据感知分析样例结构 + 填充，**不建注册中心/DB/template_id**（原计划的 bs_excel_templates 取消）。扩展现有 `src/tools/excel/excel_template.py`（fill_template/结构感知填充/行样式保留）和 `ExcelProcessTool` 多 action 管线，不重写。**行数不匹配处理（核心正确性）**：数据行 vs 样例行 多/少/相等——多则按行样式续填、少则删除剩余样例行，**输出不得残留任何样例数据**（行级+单元格级强校验）。**样式完整保留**：以克隆样例为基底只改值、不重绘样式，保留颜色/字体/合并单元格/行高/列宽/数字格式/条件格式等；行增删后显式重锚定合并范围（补 openpyxl 不自动平移的缺陷）+ 补插入行行高复制。通用 data 契约（meta/rows/group_subtotals/totals，columns 可选）领域无关；`fill_with_sample` 库可被 skill 脚本直接 import。租户注入照搬 word_process_tool 双轨模式。Phase A 数据感知分析+行数不匹配渲染+不变式校验 / Phase B 分组合计人均+样例克隆渲染 / Phase C action 接线+租户+测试。消费者：旅游报价（#43）。2026-07-20。**Phase A+B+C 已完成（2026-07-20，经独立 CodeReview）**：`src/tools/excel/excel_template_ai.py` 实现 fill_with_sample 无状态填充 + 数据感知结构分析 + 行数不匹配渲染 + 多分组（自下而上增删+行号重映射）+ 分组小计/合计/人均 + 样式/合并/行高保留 + 残留不变式校验；`ExcelProcessTool` 接通 data 入参 + 确定性路由 + 双轨租户注入（agent 主循环 hasattr 钩子自动注入）；excel 全量 36 单测全绿（template_ai 18 + process_tool_template 6 + routing 12）。B.4 占位符兜底延后。 | [设计](tools/excel/excel-template-ai-design.md) | [计划](plans/plan-excel-template-ai.md) |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用 / 个人账号 RPA）、钉钉、飞书渠道接入。
> 企业微信个人账号 RPA（#29 系列）采用「服务端拉取会话存档 + C# 客户端 PowerShell 自动化」双链路，**第一阶段真机验收已于 2026-07-16 通过**（agent2 消息入库 / 6.2 漏抓率 / outbox 端到端 / 附件与多动作发送 / 生产 Secret 配置）；持续集成与各子功能进度见 #29 及 #29a~#29d 子条目。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 29 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | **服务端**（已完成）：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/secret_crypto 七模块按 protocol.md §B 签名实现，68 单元测试通过；callback/config/files/ws 路由接入 main.py；6 条集成测试全部通过；管理前端 P1.1 已交付；指标+告警 P1.4 部分交付（83 测试通过）；平台后台绑定管理 Tab + 「+ 新增绑定」按钮已交付。**服务端拉取会话存档模式**（2026-07-03 完成 Phase 1-11，**2026-07-08 真机验证修复 2 个关键 bug**）：放弃客户端轮询为主方案，改为服务端直接拉取企微会话存档（回调 + 拉取双模式）。复用 `tenant_channel_configs` 表 + `ChannelConfig.vue` 前端，新增 `listen_mode` 字段（第一期强制 'server'，client 模式前端禁用、代码保留）。archive 模块含：credential_codec（5 字段 Fernet 加密 + 强制 server 防绕过）/ callback_crypto（企微签名 + AES 解密，复用 wecom.crypto.WeComCrypto + hmac.compare_digest）/ chat_crypto（**RSA-PKCS1v15** + AES-256-CBC，commit 5791d36 修正：早期"OAEP-SHA1 与 C# 对齐"是错的，企微官方文档要求 PKCS1，真机密文验证 3 条全 PKCS1 通过、OAEP 全失败）/ http_client（access_token Redis 缓存 + 45009 异常 + 3 次重试）/ fetcher（拉取密文 + 解密 + 构造 envelope + 复用 _process_inbound_message，Redis 分布式锁防多 worker 并发）/ poller（60s 兜底轮询，main.py lifespan 启停）/ callback_handler（GET echostr + POST 事件双验签兼容，按 body 格式 XML/JSON 天然分流）/ verifier（5 步链路自测 + 5 类错误诊断）/ audit（6 类事件接入 fetcher/callback_handler）。**SDK 调用子进程隔离**（commit c5ee158）：gunicorn worker 加载 cryptography/psycopg 后加载 .so 破坏 C 堆导致 worker 静默退出，ctypes 实现整体移到 `_sdk_inner.py`（子进程内 import），主进程走 multiprocessing.Pool(spawn, size=1)。`_process_inbound_message` 加 source 参数区分来源。**测试**：214+ 单元/集成测试通过，archive 模块覆盖率 91%。**真机验证进度**（详见 memory project_wecom-personal-rpa-server-archive-verification）：verify 接口 ✅；fetcher 拉密文 ✅；PKCS1v15 解密 ✅；消息入库 + 6.2 漏抓率已于 2026-07-16 真机验收通过 ✅。**客户端方向已重定（2026-06-26）**：放弃 Qwen3-VL 视觉定位 + FlaUI/UIA3 + Windows OCR 三条路径（均已真机验证失败）；放弃把 PS 重写到 C#。新方向：① C# 客户端通过 Process+JSON 调用 PowerShell 脚本作为自动化后端（复用 `debug-navigate.ps1` 真机验证能力）；② 服务端拉取会话存档（已上线，见上）；③ Fallback 监听方案当前所有候选（Windows 通知/视觉/OCR）都不够稳，**先占位不开发**；④ 未登录二维码 30 秒一次截取 + base64 直推服务端 Redis（30s TTL，不入审计/DB）；⑤ 绑定级监控白名单（DB `monitor_user_names`/`monitor_user_ids` + 协议下发 + 客户端缓存 + 服务端二次校验）。**已删过时文档**：vision-design / vision-breakthrough / vision-plan / enter-fix-design / enter-fix-plan / ps-automation-design / ps-automation-plan（均已永久废弃，不再保留误导后人）。**客户端入站消息功能已完全删除**（commit `0c74769`，2026-07-08 用户决策落地）：客户端无公网域名收不到企微回调，拉取+解密全部归服务端负责。已删 `Client.App/MessageArchive/` + `Client.App/Inbound/` 整目录（9 个 `.cs`）、对应测试及 DI 注册；C# 端 OAEP-SHA1 bug 随删除自动消失。**遗留（第一阶段已清零）**：原"立即做 agent2 部署 + 6.2 漏抓率测试"已于 2026-07-16 真机验收通过；"客户端入站功能全删"已由 commit `0c74769` 完成。#29a~#29d 子条目均已真机验收通过并归档至 ideas_finished.md；剩余推进项为客户端增强方向 ④⑤（未登录二维码推送、绑定级监控白名单），属后续规划。 | [协议](system/wecom-personal-rpa-protocol.md) / [架构设计](system/wecom-personal-rpa-design.md) / [客户端设计](system/wecom-personal-rpa-client-design.md) / [绑定管理 Tab 设计](system/wecom-personal-rpa-portal-binding-design.md) / [服务端监听存档设计](system/wecom-personal-rpa-server-archive-listener-design.md) / [SDK 部署](system/wecom-personal-rpa-sdk-deploy.md) | [服务端+部署计划](../plans/plan-wecom-personal-rpa.md) / [客户端计划](../plans/plan-wecom-personal-rpa-client.md) / [绑定管理计划](../plans/plan-wecom-personal-rpa-portal-binding.md) / [服务端监听存档计划](../plans/plan-wecom-personal-rpa-server-archive-listener.md) |

## 前端

> 2026-07-15：Agent Desktop 增加可交付 API 配置。构建必须显式传入 API 基址并写入受控包内资源；首次启动原子初始化 `%APPDATA%\aid-agent-desktop\desktop-config.json`，升级保留且可编辑；`AID_AGENT_API_BASE_URL` 仅作为最高优先级运维覆盖，非法或缺失配置 fail-loud。详见 [Windows 编译与打包手册](system/desktop-agent-client-build-manual.md)。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 33 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend/frontend-office-preview-design.md) | — |
| 36 | Agent 跨平台桌面客户端 | 🔧 部分完成 | 2026-07-14 Phase 0～3 Windows 完成。2026-07-20 因 Desktop renderer 已包含新的侧栏、对话和多会话提交，完成 Windows NSIS 自动更新本地实现：`electron-updater`、包内只读 HTTPS 更新源、左下角按需下载/确认重启、窄 IPC、状态竞态保护，以及 `latest.yml`/`.blockmap`/`app-update.yml`/publisherName/签名一致性门禁；Web 与 Python 服务不受影响，development-unsigned 固定禁用真实更新。三智能体和主控终检通过，最终 `0.0.2` 开发包 100,415,947 bytes，SHA-256 `36e017...0ab70`，NotSigned。正式图标、证书签名、真实更新源和旧版→新版/坏签名/回滚真机矩阵仍待完成，因此生产自动更新未标完成。Phase 4 统一承接 browser runtime：代码位于 `clients/agent-desktop/browser-runtime/`，复用 Desktop 认证、installation identity、签名安装包、更新器、托盘和设置页，不产生第二个客户端或发布链路；macOS 延后到 Mac 设备逐 Phase 验证。 | [设计](system/desktop-agent-client-design.md) | [开发计划](system/desktop-agent-client-dev-plan.md) |
| 43 | 多会话后台流式 | 🔧 部分完成 | 2026-07-20 代码与单测完成，待真实环境 E2E 验收。useAgent 从全局单份流式状态重构为 per-session 状态池（`shallowReactive` Map + computed 视图代理，消费组件零改动）：切换历史会话/新建会话/切换数字员工不再弹「终止当前会话」confirm，旧会话后台继续流式；切回进行中的会话显示实时累积内容；会话列表进行中显示 spinner、后台完成未查看显示小点（查看后消失）。后端零改动（`/api/chat/stream` 按 session_id 独立管理）。7 个新单测全过，build 通过。 | [设计](system/multi-session-background-streaming-design.md) | [开发计划](plans/plan-multi-session-background-streaming.md) |

---

## 技术栈优化

> 在"功能不变、推倒重来"前提下，对前后端技术栈的系统性优化建议。当前状态均为 💡 灵感阶段。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 38 | 后台定时/轮询任务外置 | ✅ 已完成开发（待线上验证） | 原「留在 worker 内 APScheduler」只解决多 worker 重复执行；**2026-07-21 升级为独立后台运行时**--把 APScheduler（含 4 循环迁移 + 用户 cron + S1 发布调度）整体搬到独立于 HTTP worker 的进程，不占用 worker、独立扩缩。零新依赖（复用现有 APScheduler+Redis 锁，否决 arq）。**2026-07-21 v3 计划一次性完成不分期**：调度器 + 全部后台任务（4 循环）只在 background 容器跑，HTTP worker 纯 HTTP；main.py 删 start() 与 4 循环、无开关；background 主线程 asyncio loop 承载 archive poller + APScheduler（含迁入的 memory/dedup/wecom_kf 循环）；每 30s reconcile 对账 `scheduled_tasks`（配置同步+手动触发，新增 `manual_trigger_at` 列，API/工具去掉 manager 跨进程调用）；poller 不改内部只换宿主。代码已落地（commit 88902fe），用户在测试环境验证后台任务执行情况。原 #38 调研文档已合并到设计/计划并删除。 | [独立后台运行时设计](infrastructure/background-runner-design.md) | [计划](plans/plan-background-runner.md) |
| 40 | 后台管理界面引入 Element Plus | 💡 灵感 | 后台管理 16 个页面（表格/表单密集型）从手写 TailwindCSS 迁到 Element Plus 组件库（`el-table`、`el-form`、`el-dialog` 等），聊天主界面保持不变。搭配按需导入，约 8-9 人天（含 toast 替换）。 | [设计](tech-stack-optimization/admin-element-plus-migration.md) | — |
| 41 | toast 迁至 Element Plus ElMessage | 💡 灵感 | 替换停更的 `vue-toastification@rc`，使用 Element Plus 的 `ElMessage.success/error/info/warning`，零额外依赖增量。涉及 21 个文件约 182 处调用 + 1 处测试 mock。约 1 人天。 | [设计](tech-stack-optimization/toast-migration.md) | — |

---

## 调研报告索引

以下调研报告为多项功能设计的前期研究，不单独对应开发任务：

| 调研主题 | 文档 | 关联功能 |
|---------|------|---------|
| 企业级智能体平台调研 | [enterprise-agent-platform-research.md](research/enterprise-agent-platform-research.md) | 可观测性、Prompt 管理、知识库 |
| 基础设施差距分析 | [enterprise-agent-infrastructure-gap-analysis.md](research/enterprise-agent-infrastructure-gap-analysis.md) | 整体规划 |
| Prompt 版本管理调研 | [prompt-version-management-research.md](research/prompt-version-management-research.md) | Prompt 全生命周期管理 |
| AI Agent 可观测性调研 | [observability-design-research.md](research/observability-design-research.md) | 可观测性与质量保障 |
| 知识库 RAG 技术调研 | [enterprise-knowledge-base-rag-research.md](research/enterprise-knowledge-base-rag-research.md) | 知识库能力增强 |
| 知识库行业产品调研与低改动快速增强建议 | [enterprise-knowledge-base-quick-wins.md](research/enterprise-knowledge-base-quick-wins.md) | 知识库能力增强（补充 Phase 1-2 之外的快速增强点） |
| Text-to-SQL 调研 | [text-to-sql-data-analysis-agent-research.md](research/text-to-sql-data-analysis-agent-research.md) | 数据分析智能体 |
| 企微客服 AI 绑定调研 | [wecom-kf-ai-chatbot-binding-research.md](research/wecom-kf-ai-chatbot-binding-research.md) | 企业微信客服 AI 绑定 |
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](research/context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| 前端样式调研 | [frontend-style-research.md](research/frontend/frontend-style-research.md) | 前端样式统一 |
| 前端 Office 预览调研 | [frontend-office-preview-research.md](research/frontend/frontend-office-preview-research.md) | 前端 Office 预览 |
| HTML 转 PPTX 技术调研 | [html-to-pptx-conversion-research.md](research/html-to-pptx-conversion-research.md) | PPT 技能 PPTX 导出 |
| 企业微信个人账号 RPA 生产级客户端技术方案调研 | [wecom-personal-rpa-client-implementation-research.md](research/wecom-personal-rpa-client-implementation-research.md) | 企业微信个人账号 RPA 接入 |
| 会话内上下文压缩业界方案调研 | [context_compression_research.md](research/context_compression_research.md) | 会话内上下文压缩（中期记忆） |
| 社媒内容运营智能体与聚合平台可行性调研 | [social-media-operations-agent-platform-research.md](research/social-media-operations-agent-platform-research.md) | 社媒内容运营智能体与聚合平台 |
| 懂车帝与汽车之家客户留资统一接入可行性调研 | [automotive-platform-lead-integration-research.md](research/automotive-platform-lead-integration-research.md) | CRM 智能体、汽车平台渠道集成 |
| AI 智能体行业产品体验提升调研与「工作日报」方案设计 | [ai-agent-experience-daily-report-research.md](research/ai-agent-experience-daily-report-research.md) | 工作日报（个人日报 + 团队日报）、AI 价值证明、续费驱动 |
| 从个人经验到组织能力：AI 智能体组织知识沉淀调研与方案设计 | [org-knowledge-sedimentation-research.md](research/org-knowledge-sedimentation-research.md) | 组织知识沉淀（三层知识架构 + 专家识别 + 自动抽取 + 主动推荐）、个人经验转组织资产、续费护城河 |
