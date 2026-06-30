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
| 1 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。Phase 1 完成，Phase 2-4 未开始。2026-05-29。**延伸：追踪页面整合 channel_sessions（含微信客服等所有渠道）— 方案 C：TraceCollector 下沉到 `Agent.process_message` 内部，从 record_service 自动读取 source_type，渠道真正零改造，[设计](infrastructure/observability-channel-sessions-design.md) / [开发计划](infrastructure/observability-channel-sessions-dev-plan.md)** | [设计](infrastructure/observability-design.md) | [计划](infrastructure/observability-dev-plan.md) |
| 2 | Prompt 全生命周期管理 | 🔧 部分完成 | Phase 0+1+2 代码完成。Phase 3 新增回复风格选择器+business_pages配置。2026-06-04 | [设计](infrastructure/prompt-lifecycle-design.md) | [计划](infrastructure/prompt-lifecycle-dev-plan.md) |
| 3 | 租户数据迁移 | 📋 待开发 | 跨数据库租户数据迁移（知识库 + 业务表），UUID 稳定标识符 + replace/merge 模式 + Excel 导出导入。2026-06-08 | [设计](infrastructure/tenant-data-migration.md) | — |
| 4 | 系统核心表文档 | ✅ 已完成 | 数据库核心表用途与关系文档，覆盖用户/对话/渠道/知识库/数字员工/SaaS/Prompt 管理等 30+ 张系统表。2026-06-18 | [文档](system/database_system_table.md) | — |
| 5 | 缓存使用情况文档 | ✅ 已完成 | 系统缓存使用全景文档，覆盖 Redis 缓存、内存缓存、数据库去重共 17 类缓存，含键模式、TTL、失效策略。2026-06-18 | [文档](system/cache_usage.md) | — |
| 6 | 文件存储使用情况文档 | ✅ 已完成 | 系统文件存储全景文档，覆盖新旧双轨路径、文件命名规范、目录结构、清理策略。2026-06-18 | [文档](system/file_usage.md) | — |
| 7 | 会话内上下文压缩（中期记忆） | 🔧 部分完成 | 解决单 session 长会话上下文爆 token 问题（web/微信客服/钉钉/飞书/RPA 全渠道通用）。**v3.0 独立模块化方案**：压缩服务作为业务无关、渠道无关的独立模块，**对外唯一入口 `compress_session(session_id, source_type, *, force=False)`**，内部自动从 DB 解析 tenant/user/subagent 元数据 + 自动读 model_limit。双阈值触发（token 70% 或 消息数 150，均可配置）→ **Agent 主流程同步 await 等待压缩完成** → 原子事务持久化（写 summary + 标记原消息 compacted=true，缺一不可）→ LLM 失败自动同步硬截断降级。保留首尾（HEADER 3 + TAIL 30，可配置）+ 中间段结构化摘要 + 工具结果差异化截断 + 可回滚归档。**Phase 1+2（基础设施+同步压缩服务）已完成**（commit 95820cb）：4 个 P0 修复 + 6 个 P1 修复，96 单测全通过。**v3.0 架构原则调整**：删除 v2.0 异步流程（fire-and-forget + SETNX 锁 + 失败计数 + 同步降级兜底），回归业界共识的同步模式；Phase 8「后台定时任务」作为预留节（本次不实现）。**v3.1 性能优化（session 级 token 缓存）**：chat_sessions / channel_sessions 表新增 `context_token_count` 字段，Agent 主循环每次 LLM 调用后写入 `prompt_tokens + completion_tokens`（最后一次为准，非累加），`_should_compress` 优先读缓存跳过 `count_tokens` 全量扫描（为 0 时回退全量计算兼容老 session）。收益：性能提升 5-20ms/请求 + 用 LLM API 精确 token 替代本地估算误差。待 Phase 3-7+9（服务 API 重构 + Agent 同步集成 + 工具策略 + 可观测性 + 全渠道联调）。2026-06-25 | [设计](infrastructure/memory/context_compression_design.md) / [调研](research/context_compression_research.md) | [开发计划](infrastructure/memory/context_compression_dev_plan.md) |
| 8 | 服务器部署现状文档 | ✅ 已完成 | 记录腾讯云服务器（124.222.3.254）当前部署架构：Nginx 反代 + 生产/测试双 Docker 容器 + PostgreSQL 单实例双库 + 腾讯云 Redis，含端口/目录速查、运维命令、故障排查与关联文档索引。2026-06-29 | [文档](../deploy/服务器部署现状.md) | — |
| 9 | Gunicorn 多 Worker 定时任务单 Worker 执行 | 📋 待开发 | Gunicorn 多 worker 部署下，scheduled_tasks 表内的定时任务会被每个 worker 重复触发。方案：`post_fork` hook 约束仅 worker-0 启动调度器，其他 worker 跳过；任务执行层用 Redis 锁兜底防滚动重启期间双跑。时间敏感性低（5-10 分钟延迟可接受）。2026-06-29 | [设计](infrastructure/scheduled-tasks-single-worker-design.md) | — |

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 🔧 部分完成 | Phase 0 知识库分类管理已完成（左侧分类导航 + 分类 CRUD + 文档过滤）。Phase 1-2 待开发：LLM Rerank、文档级权限、查询改写、质量评估。2026-06-04 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) |
| 19 | Skill 版本化触发重载 | ✅ 已完成开发 | 会话中已加载过的 skill，当 SKILL.md frontmatter `version` 提升后，强制 LLM 重新 `use_skill` 获取最新指南。**纯 prompt 驱动方案在 DeepSeek 上验证不可靠**（LLM 看到上下文有旧指南就跳过 use_skill），改为代码层兜底。改动：①`SkillRegistry.get_descriptions`/`SkillLoader.get_skill_descriptions` 在每条描述末尾追加 `(vx.y.z)`；②`UseSkillTool.execute` 返回新增独立 `skill_version` 字段；③`SkillLoader.parse_skill_md` 读取 version 时优先顶层 `version`，缺失则回退 `metadata.version`，兼容项目中 8 个 skill 把版本写在 `metadata.version` 的约定；④`Agent` 新增 `_get_last_use_skill_version` / `_check_skill_version_consistency` 两个辅助方法；⑤`skill_execute` 执行前强制版本校验：扫会话 memory 历史，取该 skill 最近一次 `use_skill` 返回的 `skill_version`，与 registry 当前版本比较，不一致/无记录/缺字段 → 拒绝执行脚本，返回错误"技能版本已更新 vX→vY，请先 use_skill 重新加载"，主循环 + 子智能体循环两处拦截；⑥prompt 模板移除版本校验规则段（已由代码兜底）。2026-06-23 | — | — |

## 数字员工 / 子智能体

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 9 | 数据分析智能体 | 🔧 部分完成 | 统一智能分析工具（SmartDataAnalysisTool）：内置 LLM 编排 + pandas/numpy 执行引擎。数据源导入已完成，分析工具待开发。2026-06-05 | [设计](system/digital-employee/data-analysis-subagent-design.md)、[工具设计](system/digital-employee/smart-data-analysis-tool-design.md) | [工具开发计划](system/digital-employee/smart-data-analysis-tool-dev-plan.md) |
| 9a | 数据源导入功能 | 🔧 部分完成 | Phase 1+2 代码完成（后端 API + 前端页面），Phase 3 集成测试待做。2026-06-03 | [设计§三~§五](system/digital-employee/data-analysis-subagent-design.md) | [计划](system/digital-employee/data-source-import-dev-plan.md) |
| 9c | 数据分析工具返回结构优化 | 🔧 部分完成 | P0 完成：工具返回改为 conclusion + artifacts + analysis_meta 三层结构，表格 artifact 带 preview 最多 10 行；AnalysisAgent system prompt 加收尾规范约束 conclusion 必须含产物指代。P1 主智能体 prompt 已由用户配置。2026-06-12 | [设计](system/digital-employee/data-analysis-tool-result-redesign.md) | — |
| 9d | 工具消息持久化（事务性） | 📋 待开发 | 修复工具结果跨 worker 丢失导致主智能体重跑分析的根因。tool 消息与最终回复作为事务一起写入 chat_messages，要么全成功要么全失败。2026-06-12 | [设计](system/digital-employee/tool-messages-persistence-design.md) | — |
| 9b | 聊天附件数据分析 | 🔧 部分完成 | 聊天中发送 Excel/CSV 附件自动注册到知识库并分析。共享 schema_saver 服务 + upload_data_file 工具。2026-06-09 | [设计](system/digital-employee/chat-attachment-data-analysis-design.md) | — |
| 17 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | [设计](subagent/crm/crm_subagent_design.md) | — |
| 18 | 业务页面元数据注册表 | 📋 待开发 | 页面元数据注册表 + AI 智能推荐，替代子智能体配置中的手动路由输入。2026-06-10 | [设计](system/digital-employee/page-metadata-registry-design.md) | [开发计划](system/digital-employee/page-metadata-registry-dev-plan.md) |
| 20 | 社媒内容运营智能体与聚合平台 | 🔧 部分完成 | 已完成首期可开发条件评估，并落地核心骨架：OpenSpec change、社媒核心表、连接器协议、账号/计划/内容版本/审核/发布任务/数据概览 API、聚合工作台页面和页面元数据。正式服影响已屏蔽：页面元数据状态为 planned，不进入业务菜单；社媒子智能体定义以 `.disabled` 草案保存，不会被加载器自动加载。真实微信公众号 HTTP 发布、视频号发布包下载/数据导入、调度器和真实账号验收仍待 Phase 5+。2026-06-30 | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-operations-agent-design.md) | [开发计划](system/digital-employee/social-media-operations-agent-dev-plan.md) |

## 工具

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20 | 浏览器操作可视化 | 📋 待开发 | Playwright Screencast API 实时推送浏览器操作画面到前端，支持操作标注。2026-05-21 | [设计](tools/browser/browser_visualization_design.md) | — |
| 21 | 文件工具四件套（read/write/edit/cp）v2 | 🔧 部分完成 | 拆分旧 text_file_writer/file_reader/file_list 为四个原子工具，支持 SKILL.md `<SKILL_ROOT>` 占位符。cp 工具新增 `visible` 参数（默认 False），区分中间过程文件与最终交付，避免前端展示多个中间副本下载卡片。2026-06-12 | [设计 v2](tools/text-file/file_tools_redesign_v2.md) | [开发计划 v2](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 22 | Pandoc 安装与部署 | ✅ 已完成 | word_process 工具的 md_to_word 操作依赖 Pandoc 命令行工具。Docker/Linux 部署已内置（`Dockerfile:106`）；Windows/macOS 本地开发需手动安装，缺失时报 `[WinError 2]`。2026-06-22 | [安装部署指南](tools/md-to-word/pandoc-install-guide.md) | — |
| 23 | 酒店知识库搜索工具（hotel_search） | ✅ 已完成 | 仿 attraction_search 新增酒店专项搜索工具，名称优先+向量兜底检索 source_type='hotel_resource'，返回酒店信息+价格明细表（chunk_index=1，通用 knowledge_base_search 拿不到）。客户直接问酒店或酒店价格时使用。通过 inherit:true 对旅游顾问子智能体可用。2026-06-22 | [设计](subagent/travel-consultant/hotel_search_tool_design.md) | [计划](../plans/plan-hotel-search-tool.md) |
| 24 | 景点知识库搜索工具（attraction_search） | ✅ 已完成 | 旅游顾问子智能体的景点专项搜索工具，纯向量检索 source_type='attraction_resource'。补登记（此前漏登）。2026-05-14 | [设计](subagent/travel-consultant/attraction_search_tool_design.md) | — |
| 25 | 旅游报价价格解析性能优化 | 🔧 部分完成 | 酒店、景点门票/项目、行程解析均已改为 DeepSeek V4 Pro 关闭推理；酒店用候选压缩短 prompt，景点保留 LLM 主路径并新增团队票优先后处理，行程解析补充无项目景点/活动名原样保留自检。规则优先仍待后续评估。2026-06-22 | [设计](system/design-travel-quote-price-parser-performance.md) | — |
| 27 | 酒店价格表六列格式升级 + 房型合并解析 | 🔧 部分完成 | hotel_excel_parser 由五列（房型\|客户类型\|价格\|含早\|适用日期）升级为六列（房型\|散客价\|团客价\|含早\|适用日期\|备注），第一行强制表头；房型列合并床型/面积/景观等属性便于房型匹配；只解析对外价格、丢弃内部结算价、单价格自动复制到散客/团客两列；hotel.py 的 _parse_hotel_price_rows/_select_team_price_by_llm/_extract_first_team_price 同步适配新结构，对外报价走团客价口径。2026-06-23 | [设计](subagent/travel-consultant/hotel_excel_to_kb_design.md) | — |
| 28 | 酒店报价房型自动解析 + 含早写入备注 | 🔧 部分完成 | ① itinerary_parser 从行程文本提取房型写入 hotel_stays[].room_type（默认 ""，hotel_overrides.room_type 优先级更高）；② _select_team_price_by_llm 改为让 LLM 输出"行序号"，函数据此返回 {price, breakfast, room_type} dict，含早/选中房型信息不再丢失；③ prompt 强化"价格-房型配对"原则：明确告诉 LLM 选中的房型会原样展示给客户，必须与团队构成匹配（学生/老师团默认标准间，不因价格高低改选）；④ calculate_hotel_stays/_calculate_hotel_cost_from_kb 的 remark 强制写入"房型=XX"（即使客户未指定房型，也用 LLM 选中行的房型；价格表行房型为空时兜底"标准间"）+ 含早原文（如"含双早"），杜绝"裸价格"；⑤ 新增 evaluate_hotel_breakfast_roomtype_stability.py 跑房型+含早 3 轮稳定性测试，旧评估脚本同步兼容 dict 返回值。代码改动完成，3 轮稳定性测试待在能连 DB 的环境运行。2026-06-29 | — | — |
| 29 | PDF 工具质量验证增强 | 🔧 部分完成 | P0+企业文档增强+Playwright HTML 转 PDF 已完成：新增 inspect/render_pages/validate、结构化检查、生成后自动校验、页码语义统一和依赖探测；修复 split 保存顺序、无效页码静默成功、文件名安全、HTML 表格顺序、测试漂移和旧设计文档不一致问题；新增 clean_metadata/add_watermark/protect/compress/extract_images/rotate；复杂 HTML/CSS 默认优先 Playwright print-to-pdf，失败回退 fpdf2。PDF 全量单测通过，127 passed；真实 Playwright 生成和结构校验通过。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | [开发计划](tools/pdf/pdf_tool_quality_validation_dev_plan.md) |
| 30 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。适用场景：结构化业务数据直接生成正式报价单、报告、对账单、审批单等，要求页眉页脚、页码、签章区、复杂跨页表格和版式位置稳定。当前复杂 HTML/CSS 已由 Playwright print-to-pdf 覆盖，不优先投入 reportlab。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 31 | 文件生成类工具入参语义拆分与路由健壮性 | 🔧 部分完成 | Word/PDF/Excel/PPT 当前普遍用 `context` 同时承载“用户目的 + 待处理正文 + 隐含参数”，导致内部 LLM 路由不稳定、转换正文被工具指令污染。目标：新增 `instruction/content/content_type/output_name` 结构化入参，`context` 仅作兼容；规则路由覆盖高确定性场景，内部 LLM 只处理复杂参数抽取。已完成 Word 短期兜底 + Phase 1 正式入参扩展：`instruction/content/content_type/output_name` 已进入 schema，新旧入参统一归一化，`md_to_word` 只消费清洗后的正文，显式 `output_name` 优先于标题推断，Word 单测 74 passed。待补 PDF/Excel/PPT 同步改造、Agent/旅游顾问提示更新。2026-06-30 | [设计](tools/tool-input-contract-redesign.md) | [开发计划](tools/tool-input-contract-redesign-dev-plan.md) |
| 32 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。当前已有结构化校验和 Playwright HTML 转 PDF，暂不投入完整样本集建设。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |

## 渠道集成

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 26 | 企业微信客服 AI 绑定 | 📋 待开发 | "扫码绑定 AI 机器人"模式，第三方授权与合规接入。2026-05-26 | [设计](channel/wecom_kf/wecom_kf_design.md) | — |
| 27 | wecom_kf 多媒体消息支持 | 🔧 部分完成 | 后端完成：download_media 方法 + channel_routes 消息处理（附件下载/持久化/传递给 agent）+ external_customers 附件下载代理接口。前端完成：ExternalCustomerService 语音播放器/图片预览/文件卡片 + AttachmentCard 组件。待集成测试。2026-06-14 | — | — |
| 28 | 渠道会话多条消息串行化 | 📋 待开发 | 渠道场景短时间多条消息串行处理，新消息取消旧请求 + 只保留最新意图，解决并发写入混乱和 DeepSeek 400 问题。2026-06-18 | [设计](channel/concurrent-message-serialization-plan.md) | — |
| 29 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | **服务端**（已完成）：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/secret_crypto 七模块按 protocol.md §B 签名实现，68 单元测试通过；callback/config/files/ws 路由接入 main.py；6 条集成测试全部通过；管理前端 P1.1 已交付；指标+告警 P1.4 部分交付（83 测试通过）；平台后台绑定管理 Tab + 「+ 新增绑定」按钮已交付。**客户端方向已重定（2026-06-26）**：放弃 Qwen3-VL 视觉定位 + FlaUI/UIA3 + Windows OCR 三条路径（均已真机验证失败）；放弃把 PS 重写到 C#。新方向：① C# 客户端通过 Process+JSON 调用 PowerShell 脚本作为自动化后端（复用 `debug-navigate.ps1` 真机验证能力）；② 消息监听主方案用企业微信官方「会话存档 API」（RSA 解密 + seq 持久化，需企业开通 + 凭证配置在 client.yaml）；③ Fallback 监听方案当前所有候选（Windows 通知/视觉/OCR）都不够稳，**先占位不开发**；④ 未登录二维码 30 秒一次截取 + base64 直推服务端 Redis（30s TTL，不入审计/DB）；⑤ 绑定级监控白名单（DB `monitor_user_names`/`monitor_user_ids` + 协议下发 + 客户端缓存 + 服务端二次校验）。**已删过时文档**：vision-design / vision-breakthrough / vision-plan / enter-fix-design / enter-fix-plan / ps-automation-design / ps-automation-plan（均已永久废弃，不再保留误导后人）。**遗留**：按客户端 5 阶段计划落地（清理 → PS 工程化+协议扩展 → 出站+存档+二维码 → 入站解析+基础设施 → 真机回归，约 54h/7 工作日） | [协议](system/wecom-personal-rpa-protocol.md) / [架构设计](system/wecom-personal-rpa-design.md) / [客户端设计](system/wecom-personal-rpa-client-design.md) / [绑定管理 Tab 设计](system/wecom-personal-rpa-portal-binding-design.md) | [服务端+部署计划](../plans/plan-wecom-personal-rpa.md) / [客户端计划](../plans/plan-wecom-personal-rpa-client.md) / [绑定管理计划](../plans/plan-wecom-personal-rpa-portal-binding.md) |
| 30 | 钉钉渠道接入 | 🔧 部分完成 | 钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。代码（adapter/crypto/media/message_builder/路由/前端配置）已完成，112 单元测试 + 23 集成测试全部通过；待真实钉钉环境联调。2026-06-19 | [设计](channel/dingtalk/integration_guide.md) / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 31 | 飞书渠道接入代码审核 | 🔧 部分完成 | 基于 [integration_guide.md](channel/feishu/integration_guide.md) 审核飞书渠道实现。发现 3 个 P0 问题（adapter 每请求新建导致 token 缓存/速率限制/HTTP 连接池失效 + httpx client 不关闭造成 fd 泄漏；encrypt_key 文档与代码必填规则不一致）、4 个 P1 安全问题（签名比较未用 hmac.compare_digest、时间戳未做偏移校验、GET 回调无鉴权、去重 DB 异常静默丢消息）、4 个 P2 问题（速率限制需迁 Redis、媒体存储违反租户隔离、asyncio.create_task 无引用、bot open_id 失败静默丢群聊消息）。2026-06-19 | — | [审核报告](../plans/feishu-channel-code-review.md) |
| 33 | 渠道上下文丢失修复（channel_messages 事务化 + 连续 user 兜底 + 合并写入时机 + send_response 统一封装） | ✅ 已完成 | 已通过用户手工测试。P0-1/P0-2/P0-3 全部落地，**一次改造全渠道复用**（wecom_kf/wecom/wecom_personal_rpa/dingtalk/feishu 共 6 个调用点统一走 `ChannelSessionManager.process_and_persist`）。改动：①`src/core/session_queue.py` `enqueue_and_process` 返回 `EnqueueResult`（含 status/merged_input/was_merged，新增 status="error"）；②`src/channels/session.py` 新增 `add_messages_batch_transactional`（事务批量写入）+ `process_and_persist`（user 写入推迟到合并决策后 + 异常兜底）+ `make_send_response`（消除 6 处 _send_response 重复，净减 70 行）；③`src/core/agent.py` `_reorder_messages_for_llm` 连续 user 兜底（保留最新一条）；④6 个渠道调用点全部替换。修复 P0 必修项 6 个（兜底方向写反 / record 时序 / 异常 fall-through 丢失 user / 批量写入失败返回 error / 测试更新 / wecom_kf NameError）。新增 40 个单元测试全通过，零回归。2026-06-24 | [调研](research/wecom-kf-context-loss-research.md) | — |
| 34 | chat_records 表 web/channel 隔离 | 📋 待开发 | ChatRecordDB.list_by_session / list_by_user 未按 source_type 过滤，channel 端（wecom_kf/wecom/dingtalk/feishu）的会话记录会被 web 端会话查询页面拉出来造成混淆。需补 source_type 过滤（web 端默认只查 source_type='chat' 或 NULL）。2026-06-23 | — | — |
| 35 | 渠道/web 上下文重建两大病根修复（来源分流 + 窗口裁剪方向） | 🔧 部分完成（待部署） | **#33 的 P0 修复未覆盖的两个真因，均为读取侧 bug**。**病根A（来源不分流）**：`agent.py` 上下文重建 `if db_messages:` 盲信 chat_messages，渠道会话若有迁移期残留就永不读 channel_messages → 反复问已确认信息、一旦错乱后续全错。修复：`ChannelSessionManager.is_channel_session` 判定，渠道读 channel_messages、web 读 chat_messages，严格分离。**病根B（裁剪方向反了，更严重）**：`get_messages` 与 `MessageDB.list_by_session` 都用 `ORDER BY id ASC LIMIT N`，返回**最老的 N 条**，长会话超过 N 后最近一轮 assistant 回复被整体切掉（trace 取证：连续两 trace prompt token 完全相同、messages[0..98] 逐条相同、615/616 被跳过）→ agent 看不到上一轮提问，用户"OK"失指代、重复执行。web+channel 都中招（web 仅因短会话没触发）。修复：子查询取最近 N 条再正序 + `_reorder_messages_for_llm` 末尾裁掉开头非 user 对齐到 user 边界（防 DeepSeek 400）。防御性收尾：`short_term._load_from_db` 渠道会话不读 chat_messages。全部编译+测试通过（54 passed），生产快照验证修复后 615/616/617 全部进窗口。**待部署**。2026-06-25 | [调研](research/wecom-kf-context-loss-research.md) §9~§10 | — |
| 36 | 渠道语音 ASR 补齐（wecom / feishu / dingtalk） | 📋 待开发 | 渠道场景下语音消息必须在渠道层完成 ASR 转文字后送入 agent，LLM 不再承担语音识别职责。已完成：①wecom_kf 渠道层调用阿里云 ASR 转文字（`src/saas/api/channel_routes.py` `_transcribe_voice_with_asr`）；②删除 `master_agent.md` / `subagent_base.md` 中"[语音消息] 无识别结果时回复语音识别失败"的旧规则；③ASR 成功后的前缀由 `[语音消息]` 改为 `[ASR识别结果]`，明确告知 LLM 文本来源。待补：wecom（企业微信应用消息）、feishu、dingtalk 三个渠道的语音消息同样需要接入阿里云 ASR，目前仅占位，未实现。2026-06-26 | — | — |
| 37 | wecom_kf 用户撤回消息处理（同批次剔除 + 跨批次标记 + 后台可见） | 📋 待开发 | 微信客服用户撤回消息（`user_recall_msg` 事件，origin=4）走 sync_msg 推送，现有代码因 `origin!=3` 过滤被完全忽略，导致被撤回消息仍参与合并与上下文重建。已通过 tlog 确认事件结构与推送路径。方案：①同批次剔除——撤回事件识别 + 事件去重，合并前从 valid_msgs 删除被撤回消息；②跨批次标记——channel_messages 加 is_recalled/recalled_at 字段，metadata 存 wecom_msgid/merged_from_msgids/merged_segments，撤回事件到达时反查标记；③合并消息部分撤回——按 merged_segments 重建 content；④上下文重建（_load_channel_history / short_term）过滤 is_recalled=FALSE；⑤后台"外部接待客户"与"会话追踪"视图不过滤，展示"已撤回"徽章。2026-06-29 | — | [开发计划](channel/wecom_kf/message_recall_plan.md) |

## 前端

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 33 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend/frontend-office-preview-design.md) | — |

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
| Text-to-SQL 调研 | [text-to-sql-data-analysis-agent-research.md](research/text-to-sql-data-analysis-agent-research.md) | 数据分析智能体 |
| 企微客服 AI 绑定调研 | [wecom-kf-ai-chatbot-binding-research.md](research/wecom-kf-ai-chatbot-binding-research.md) | 企业微信客服 AI 绑定 |
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](research/context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| 前端样式调研 | [frontend-style-research.md](research/frontend/frontend-style-research.md) | 前端样式统一 |
| 前端 Office 预览调研 | [frontend-office-preview-research.md](research/frontend/frontend-office-preview-research.md) | 前端 Office 预览 |
| HTML 转 PPTX 技术调研 | [html-to-pptx-conversion-research.md](research/html-to-pptx-conversion-research.md) | PPT 技能 PPTX 导出 |
| 企业微信个人账号 RPA 生产级客户端技术方案调研 | [wecom-personal-rpa-client-implementation-research.md](research/wecom-personal-rpa-client-implementation-research.md) | 企业微信个人账号 RPA 接入 |
| 会话内上下文压缩业界方案调研 | [context_compression_research.md](research/context_compression_research.md) | 会话内上下文压缩（中期记忆） |
| 社媒内容运营智能体与聚合平台可行性调研 | [social-media-operations-agent-platform-research.md](research/social-media-operations-agent-platform-research.md) | 社媒内容运营智能体与聚合平台 |
