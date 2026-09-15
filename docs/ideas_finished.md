# 项目开发目录 — 已完成

> 已完成开发的功能归档在此文件。本文件同样是**纯索引**：`说明` 只写一句话，详情见链接的设计/计划文档；开发记录不写进表格。

## 基础设施

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 72 | ✅ 用户行为审计日志 | 登录/登出/改密/管理后台增删改/普通用户关键动作全量留痕（user_behavior_logs 系统表，IP/UA/设备快照/token 指纹），满足安全审计与追责定位。 | [设计](system/user-behavior-audit-log-design.md) | — |
| 3 | LLM 故障转移 | 提供商故障自动切换，多 Key 轮换与降级策略 | [设计](infrastructure/llm-failover-design.md) | — |
| 4 | MCP Server | Model Context Protocol 服务器，支持外部工具集成 | [设计](infrastructure/mcp_server.md) | — |
| 5 | ✅ 主智能体系统提示词优化 | 重写 master_agent.md / subagent_base.md 为原则化结构，新增「文件交付规则」段统一约束"工具生成文件后必须用 cp 注册"。 | [设计](system/prompt/agent-system-prompt-optimization-design.md) | [计划](plans/agent-system-prompt-optimization-dev-plan.md) |
| 4 | 系统核心表文档 | 数据库核心表用途与关系文档，覆盖用户/对话/渠道/知识库/数字员工/SaaS/Prompt 管理等 30+ 张系统表。2026-06-18 | [文档](system/database_system_table.md) | — |
| 5 | 缓存使用情况文档 | 系统缓存使用全景文档，覆盖 Redis 缓存、内存缓存、数据库去重共 17 类缓存，含键模式、TTL、失效策略。2026-06-18 | [文档](system/cache_usage.md) | — |
| 6 | 文件存储使用情况文档 | 系统文件存储全景文档，覆盖新旧双轨路径、文件命名规范、目录结构、清理策略。2026-06-18 | [文档](system/file_usage.md) | — |
| 8 | 服务器部署现状文档 | 记录腾讯云服务器（124.222.3.254）当前部署架构：Nginx 反代 + 生产/测试双 Docker 容器 + PostgreSQL 单实例双库 + 腾讯云 Redis，含端口/目录速查、… | [文档](../deploy/服务器部署现状.md) | — |
| 10 | 三智能体开发流程规范 | 非平凡开发任务（新功能/Phase/多文件改动）的标准流程：开发智能体→测试智能体（独立测试+回归+启动安全检查）→CodeReview智能体（独立审查+修复必要问题）… | [规范](../.claude/rules/dev_workflow.md) | — |
| 7 | 会话内上下文压缩（中期记忆） | 解决单 session 长会话上下文爆 token 问题（web/微信客服/钉钉/飞书/RPA 全渠道通用）。 | [设计](infrastructure/memory/context_compression_design.md) / [调研](research/context_compression_research.md) | [开发计划](infrastructure/memory/context_compression_dev_plan.md) |
| 11 | 租户数据迁移 | 跨数据库租户数据迁移（知识库 + 业务表），UUID 稳定标识符 + replace/merge 模式 + Excel 导出导入。2026-06-08 | — | — |
| 12 | Gunicorn 多 Worker 定时任务单 Worker 执行 | Gunicorn 多 worker 部署下，scheduled_tasks 表内的定时任务会被每个 worker 重复触发。 | [设计](infrastructure/scheduled-tasks-single-worker-design.md) | — |
| 2 | ✅ Prompt 全生命周期管理 | 子智能体 Prompt 的版本化管理（编辑→提交版本→对比→回滚）+ 独立智能体管理页面 + 租户定制 Prompt（extra_md）DB 化。 | [设计](infrastructure/prompt-lifecycle-design.md) | [计划](infrastructure/prompt-lifecycle-dev-plan.md) |
| 60 | 租户附件存储路径规范改造 | 把全项目 `storage/uploads/{tenant}/{user}/` 旧路径统一改造为 `storage/tenants/{tenant_id}/{scene}/` 新规范（依据 `.cla… | [规范](../.claude/rules/backend_dev.md) | [计划](plans/plan-tenant-storage-migration.md) |
| 69 | ✅ Agent 运行时安全加固 | 已落地定时调度租户隔离、请求上下文并发隔离与恢复、敏感错误脱敏、知识库对象级租户保护、观测成本闭环、核心结构守卫及工具副作用审计。 | [设计](system/agent-runtime-safety-hardening-design.md) / [工具审计](system/enterprise-agent-platform/tool-effect-inventory.md) | — |
| 69a | ✅ Agent 运行时安全债收尾 | 2026-08-31 复核确认 `X-Tenant-Id` 验权（`d517e859`/`ec177152`）… | [核对与修复设计](system/agent-runtime-security-debt-closure-design.md) | [开发计划](plans/plan-agent-runtime-security-debt-closure.md) |
| 61 | skill_ws 临时工作目录清理机制 | ✅ 已完成开发。修复技能执行工作目录 `skill_ws_*` 创建后永不清理的临时文件泄漏（每月堆积，2026-08-13 迁移核对时发现）。 | — | — |
| 38 | 后台定时/轮询任务外置 | ✅ 已完成开发（待线上验证）。 | [独立后台运行时设计](infrastructure/background-runner-design.md) | [计划](plans/plan-background-runner.md) |

## 系统功能

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 68 | 客户端计费统一接入（boss cli / 协会采集 / 未来客户端三模式） | ✅ 已完成开发。 | [设计](design/billing/client-billing-integration-design.md) | — |
| 53 | 租户间知识库共享 | ✅ 已完成开发。平台管理员两步配置实现知识库跨租户共享（A 租户知识库共享给 B 租户，B 数字员工检索时本租户+共享库合并检索）。 | [设计](system/knowledge-base/tenant-knowledge-sharing-design.md) | — |
| 37 | 图片资产全链路承载能力（Phase 0+1+2） | **系统级横切能力**：定义图片资产（Image Asset）从来源/注册/寻址/嵌入/渲染的统一规范。 | [设计](system/image-asset-pipeline-design.md) · [主计划](plans/plan-image-asset-pipeline.md) · [Phase 3 计划](plans/plan-image-asset-pipeline-phase3.md) | — |
| 6 | 记忆系统 | 短期记忆（滑动窗口）+ 长期记忆（摘要压缩），会话上下文管理 | [设计](memory/memory_design.md) | — |
| 7 | 回复风格系统 | 可配置回复风格，不同场景的语气和格式控制 | [设计](system/design-reply-style.md) | — |
| 8 | 对话体验优化 | SSE 流式输出优化、消息渲染改进、交互体验提升 | [设计](system/design-chat-experience-optimization.md) | — |
| 19 | Skill 版本化触发重载 | 会话中已加载过的 skill，当 SKILL.md frontmatter `version` 提升后，强制 LLM 重新 `use_skill` 获取最新指南。 | — | — |
| 20 | Redis 缓存管理页 | 平台管理后台新增「Redis 缓存」页面，供平台管理员枚举、查看、搜索、删除 Redis 键值。 | [设计](system/design-redis-cache-admin.md) | — |
| 35 | 短信验证码 skill | ✅ 已完成开发。新增 `src/skills/sms-verification-1.0.0/` 供智能体调用，复用 `src/sms/` 通道和 `send_sms_code`/`verify_sms_code` 底层逻辑。 | — | — |
| 36 | 技能白名单简化（三层->两层） | ✅ 已完成开发。 | — | — |
| 47 | 工作成果记录 | ✅ 已完成开发。沉淀子智能体产生的重要工作成果（生成文件、完成业务操作、给出决策建议）到 `work_outcomes` 表，租户前台新增"工作成果"菜单。 | [设计](system/work-outcome-record-design.md) | — |

## 数字员工 / 子智能体

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 10 | 数字员工管理 | 实例管理、选择策略、并发控制 | [设计](system/digital-employee/digital-employee-management.md) | — |
| 11 | 旅行顾问智能体 | 旅游报价、路线规划、酒店景点知识库集成 | [设计](subagent/travel-consultant/travel_subagent_design.md) | — |
| 12 | 竞品调研智能体 | 竞品信息收集、HTML 预览、数据结构化输出 | [设计](subagent/competitor-research/competitor_research_subagent_design.md) | — |
| 13 | 售后处理智能体 | 售后工单处理、退款退货流程自动化 | [设计](subagent/after-sales/after_sales_subagent_design.md) | — |
| 14 | 投诉处理智能体 | 投诉分类、处理建议、升级流程 | [设计](subagent/complaint-handling/complaint-agent-design.md) | [计划](subagent/complaint-handling/complaint-agent-dev-plan.md) |
| 15 | 客户跟进智能体 | 客户跟进任务管理、提醒、执行 | [设计](subagent/customer-followup/customer_followup_design.md) | — |
| 18 | 业务页面元数据注册表 | ✅ 已完成开发。 | [设计](system/digital-employee/page-metadata-registry-design.md) | [开发计划](system/digital-employee/page-metadata-registry-dev-plan.md) |
| 9 | 数据分析智能体 | ✅ 已完成开发。 | [设计](system/digital-employee/data-analysis-subagent-design.md) / [工具设计](system/digital-employee/smart-data-analysis-tool-design.md) | [工具开发计划](system/digital-employee/smart-data-analysis-tool-dev-plan.md) |
| 9a | 数据源导入功能 | ✅ 已完成开发。通用知识库扩展：上传 Excel/CSV → 解析 Sheet → LLM 推理 Schema → 用户审核 → 入库；连接外部数据库导入；管理已导入表 Schema 与关联。 | [设计§三~§五](system/digital-employee/data-analysis-subagent-design.md) | [计划](system/digital-employee/data-source-import-dev-plan.md) |
| 9b | 聊天附件数据分析 | ✅ 已完成开发。聊天中发送 Excel/CSV 附件自动注册到知识库并分析，共享 `schema_saver` 服务 + `upload_data_file` 工具（commit cfb0a78），`test_upload_shared.py` 覆盖。 | [设计](system/digital-employee/chat-attachment-data-analysis-design.md) | — |
| 9c | 数据分析工具返回结构优化 | ✅ 已完成开发。 | [设计](system/digital-employee/data-analysis-tool-result-redesign.md) | — |
| 9d | 工具消息持久化（事务性） | ✅ 已完成开发。修复工具结果跨 worker 丢失导致主智能体重跑分析的根因。 | [设计](system/digital-employee/tool-messages-persistence-design.md) | — |
| 16 | 订单处理智能体 | 订单自动化处理流程 | [设计](subagent/order-processing/design.md) | [计划](subagent/order-processing/dev_plan.md) |
| 18 | 内容生成通用设计 | 通用内容生成子智能体框架 | [设计](subagent/content_generate_universal_design.md) | — |
| 25 | 旅游报价价格解析性能优化 | ✅ 已完成开发。酒店、景点门票/项目、行程解析均已改为 DeepSeek V4 Pro 关闭推理；酒店用候选压缩短 prompt，景点保留 LLM 主路径并新增团队票优先后处理，行程解析补充无项目景点/活动名原样保留自检。 | [设计](system/design-travel-quote-price-parser-performance.md) | — |
| 27 | 酒店价格表六列格式升级 + 房型合并解析 | ✅ 已完成开发。 | [设计](subagent/travel-consultant/hotel_excel_to_kb_design.md) | — |
| 28 | 酒店报价房型自动解析 + 含早写入备注 | ✅ 已完成开发。 | — | — |
| 19 | 旅游报价酒店局部替换 | 客户换酒店时只重算住宿费用，其他 items 不变；按城市定位、生成新报价单。 | [设计](system/design-travel-quote-hotel-swap.md) | [计划](plans/plan-travel-quote-hotel-swap.md) |
| 26 | 报价单生成支持指定酒店 | ✅ 已完成开发 generate.py 新增可选参数 hotel_overrides，客户明确指定的酒店一开始就生效。 | [设计](subagent/travel-consultant/generate-quote-hotel-override-design.md) | [开发计划](plans/plan-generate-quote-hotel-override.md) |
| 35 | 旅游报价多人团动态车辆组合 | ✅ 已完成开发 车型推荐改为动态规划，按“最少车辆数 → 对应计价模式总单价最低 → 空座最少”选择组合；公里计价使用 per_km_rate 比较并按车型分别计价汇总，… | — | — |
| 36 | Markdown 中文 PDF 渲染修复 | ✅ 已完成开发 md_to_pdf 改为 Markdown→HTML 后优先使用 Playwright/Chromium 打印，fpdf2 仅作带告警降级；… | — | — |
| 37 | HTML 生成清洗与长文档预览修复 | ✅ 已完成开发 write 工具支持从“模型说明文字 + ```html 围栏 + 尾部说明”中准确提取完整 HTML，同时保留 Markdown 内普通代码块；… | — | — |
| 38 | PDF.js 最小化前端预览 | ✅ 已完成开发 PDF 预览由浏览器原生 iframe 改为前端动态加载 PDF.js 5.4.624，以 Canvas 逐页渲染并纵向排列；无工具栏、分页、搜索或缩放控件，仅保留自然滚动查看，… | — | — |
| 39 | 子智能体 LLM 配置扩展（按 Provider 覆盖 MODEL_CODE） | ✅ 已完成开发 子智能体 `SUBAGENT.md` 和 DB `subagent_definitions` 表支持指定主 provider + 各 provider 的 model_code 覆盖，… | [设计](subagent/subagent-llm-config-override-design.md) / [Failover 扩展](infrastructure/llm-failover-design.md#7-子智能体-model_codes-覆盖) | — |
| 40 | 简历-职位匹配体系（招聘操作智能体） | ✅ 已完成开发（真机演示验收通过 2026-09-01）。 | [设计](design/recruiting/resume-job-matching-design.md) | — |
| 41 | 招聘演示闭环（一键「筛选简历」快捷按钮 + 智能体链路） | ✅ 已完成开发（真机演示验收通过 2026-09-01）。 | 无独立设计（复用 boss_* 工具；utils/quickPrompts.ts + SUBAGENT.md） | — |

## 工具

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 65 | 工具注册与 Agent 解耦 | ✅ 已完成开发。 | [设计](tools/tool-auto-discovery-design.md) / [总体设计](plans/plan-agent-registration-decoupling.md) | [开发计划](plans/plan-agent-registration-decoupling.md) |
| 66 | 普通工具特殊分支删除与统一执行链 | ✅ 已完成开发。 | [设计与开发计划](plans/plan-tool-outcome-presentation-decoupling.md) | [计划](plans/plan-tool-outcome-presentation-decoupling.md) |
| 31 | 文件生成类工具入参语义拆分与路由健壮性 | ✅ 已完成开发。Word/PDF/Excel/PPT 通用 `context` 同时承载"用户目的 + 待处理正文 + 隐含参数"导致内部 LLM 路由不稳定、转换正文被工具指令污染。 | [设计](tools/tool-input-contract-redesign.md) | [开发计划](tools/tool-input-contract-redesign-dev-plan.md) |
| 35 | skill_complete 工具彻底删除 | ✅ 已完成开发。 | [设计](system/skill-complete-removal-design.md) | — |
| 21 | 文件工具四件套（read/write/edit/cp）v2 | ✅ 已完成开发。拆分旧 text_file_writer/file_reader/file_list 为四个原子工具，支持 SKILL.md `<SKILL_ROOT>` 占位符。 | [设计 v2](tools/text-file/file_tools_redesign_v2.md) | [开发计划 v2](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 19 | 浏览器自动化工具 | 网页自动化操作、数据采集、Markdown 转换 | [设计](tools/browser/browser_automation_design.md) | — |
| 19.1 | 知识库检索租户隔离 | 知识库检索工具添加 tenant_id 过滤，修复跨租户数据泄露 + 分块 overlap 修复。2026-06-02 | [设计](tools/knowledge-base-search-tenant-isolation-design.md) | [计划](tools/knowledge-base-search-tenant-isolation-dev-plan.md) |
| 21 | PPT 生成工具（基础版） | ✅ 已完成开发，历史基础架构已由 #34 增强方案接续；保留设计文档用于追溯。 | [设计](tools/ppt/ppt_tool_design.md) | — |
| 34 | PPT 工具增强与 HTML 转 PPTX 导出 | ✅ 已完成开发。 | [设计](tools/ppt/ppt_tool_enhancement_design.md) | [开发计划](tools/ppt/ppt_tool_enhancement_dev_plan.md) |
| 21 | Word 工具 | Word 文档读取与生成 | [设计](tools/word/word_tool_design.md) | — |
| 22 | PDF 工具 | PDF 文档解析与处理 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 23 | Excel 工具 | Excel 文件读取与数据提取 | [设计](tools/excel/excel_tool_design.md) | — |
| 21 | Excel 工具重构 | ✅ 已完成开发 移除 analyze 和 chart 操作（由数据分析工具替代），增强 read 操作（复制 FileReaderTool 的文档级读取能力）。 | [设计](tools/excel/excel-tool-refactor-design.md) | [计划](tools/excel/excel-tool-refactor-dev-plan.md) |
| 24 | HTTP API 适配器 | 通用 HTTP API 调用适配器 | [设计](tools/http_api_adapter_design.md) | [指南](tools/http_api_skill_developer_guide.md) |
| 25 | 文本文件生成工具 | 文本/Markdown 文件生成与内容写入优化 | [设计](tools/text-file/text_file_generator_design.md) | — |
| 22 | Pandoc 安装与部署 | word_process 工具的 md_to_word 操作依赖 Pandoc 命令行工具。 | [安装部署指南](tools/md-to-word/pandoc-install-guide.md) | — |
| 23 | 酒店知识库搜索工具（hotel_search） | 仿 attraction_search 新增酒店专项搜索工具，名称优先+向量兜底检索 source_type='hotel_resource'，返回酒店信息+价格明细表（chunk_index=1，… | [设计](subagent/travel-consultant/hotel_search_tool_design.md) | [计划](plans/plan-hotel-search-tool.md) |
| 24 | 景点知识库搜索工具（attraction_search） | 旅游顾问子智能体的景点专项搜索工具，纯向量检索 source_type='attraction_resource'。补登记（此前漏登）。2026-05-14 | [设计](subagent/travel-consultant/attraction_search_tool_design.md) | — |
| 33 | x-to-image 内容转图片服务 | 将文本/Markdown/HTML 渲染为一张尺寸可控的长图(PNG)，Playwright **headless** 全页截图 + Pillow 拼接/截断/体积控制；… | [设计](tools/x-to-image/x-to-image-design.md) | [开发计划](tools/x-to-image/x-to-image-dev-plan.md) |
| 22.1 | 研学报价技能 Prompt 稳定性优化（方案 A） | ① 同一行程多次报价金额漂移，通过固化 itinerary_parser 的 prompt 规则收敛方差，实测 5 次连续调用 hash 完全一致；② 重构 generate.py 返回结构：… | [设计](system/design-travel-quote-prompt-stability.md) | [计划](plans/plan-travel-quote-prompt-stability.md) |
| 21 | 文件操作工具集重新设计 v2 | ✅ 已完成开发。拆分为 read/write/edit/cp 四个工具（对齐 Claude Code 命名），删除 file_list 工具，edit 三种编辑模式（replace_string/replace_section/replace_lines）。 | [设计](tools/text-file/file_tools_redesign_v2.md) | [计划](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 62 | Word 客户模板格式参考生成 | ✅ 已完成开发（三智能体流程）。 | [设计](tools/word/word_tool_design.md) | — |
| 63 | Word 模板占位符填充增强（场景一） | ✅ 已完成开发（三智能体流程）。 | [设计](tools/word/word_tool_design.md) | — |

## 渠道集成

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 29a | RPA 连续消息合并与无效 Trace 治理 | ✅ 已完成开发。稳定 session key 与 legacy 原地迁移、Redis 原子 finalizing/ownership lease、文本及附件 merge/pending、Trace 显式终止语义、监控页中间过程折叠。 | [方案](channel/wecom-personal-rpa-message-merge-and-trace-plan.md) / [关联设计](channel/concurrent-message-serialization-plan.md) | [开发计划](channel/wecom-personal-rpa-message-merge-and-trace-dev-plan.md) |
| 29b | RPA 自消息循环与错发防护 | ✅ 已完成开发。 | [优化方案](channel/wecom-personal-rpa-self-message-loop-and-safe-send-plan.md) | [技术实现与开发计划](channel/wecom-personal-rpa-self-message-loop-and-safe-send-dev-plan.md) |
| 29c | RPA 客户端 EXE 单一交付 | ✅ 已完成开发。按运维决策永久下线安装包交付：删除安装包工程、构建/安装脚本及专属指南，只保留 `dotnet build -c Release` 本机编译和 `scripts/publish.ps1` 自包含 EXE 目录发布；同步 README、状态、操作手册、设计与计划，并新增静态防回归检查。 | — | — |
| 29d | RPA 外部联系人绑定识别与清理 | ✅ 已完成开发。RPA 与 `wecom_kf` 完全隔离；仅外部联系人进入 binding/Agent。 | [姓名解析调研](research/wecom-rpa-external-user-name-resolution-research.md) | — |
| 27 | wecom_kf 多媒体消息支持 | ✅ 已完成开发。 | — | — |
| 30 | 钉钉渠道接入 | ✅ 已完成开发。钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。 | [设计](channel/dingtalk/integration_guide.md) / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 31 | 飞书渠道接入代码审核 | ✅ 已完成开发。 | — | [审核报告](plans/feishu-channel-code-review.md) |
| 27 | 企业微信集成 | 应用消息收发、回调处理 | [设计](channel/wecom/wecom-integration.md) | — |
| 28 | 飞书 / 钉钉集成 | 飞书和钉钉渠道适配器实现 | [设计](channel/feishu-dingtalk/channel_integration.md) | — |
| 29 | 飞书渠道对接（完整实施） | 修复 FeishuAdapter 错误实现（AES 密钥、签名验证），补齐 crypto/media 子模块、连接池复用、长消息拆分、速率限制、欢迎消息，… | [方案](channel/feishu/implementation_plan.md) / [实施](channel/feishu/integration_guide.md) | — |
| 32 | 微信客服转人工工具优化（schema + 渠道隔离） | ✅ 已完成开发 优化 transfer_to_human：①reason 改为必填；②渠道隔离完全由工具 execute 段的 get_kf_context 判断，… | [设计](channel/wecom_kf/transfer_to_human_optimization.md) | [计划](channel/wecom_kf/transfer_to_human_optimization_plan.md) |
| 33 | 渠道上下文丢失修复（channel_messages 事务化 + 连续 user 兜底 + 合并写入时机 + send_response 统一封装） | 已通过用户手工测试。P0-1/P0-2/P0-3 全部落地，**一次改造全渠道复用**（wecom_kf/wecom/wecom_personal_rpa/dingtalk/feishu 共 6 个调用点统一走 `ChannelSessionManager.process_and_persist`）。 | [调研](incidents/wecom-kf-context-loss-research.md) | — |
| 37 | wecom_kf 用户撤回消息处理（同批次剔除 + 跨批次标记 + 后台可见） | 已完成：①撤回事件识别（origin=4，event_type=user_recall_msg，读 event.recall_msgid 而非 event.msgid）+ 事件去重；… | — | [开发计划](channel/wecom_kf/message_recall_plan.md) |
| 35 | 渠道/web 上下文重建两大病根修复（来源分流 + 窗口裁剪方向） | #33 的 P0 修复未覆盖的两个真因，均为读取侧 bug。 | [调研](incidents/wecom-kf-context-loss-research.md) §9~§10 | — |
| 34 | chat_records 表 web/channel 隔离 | ✅ 已完成开发。 | — | — |
| 36 | 渠道语音 ASR 补齐（wecom / feishu / dingtalk） | ✅ 已完成开发。渠道场景下语音消息在渠道层完成阿里云 ASR 转文字后送入 agent，LLM 不再承担语音识别。 | — | — |
| 38 | wecom_kf 单轮回复配额管控（提示词注入 + 渲染层兜底） | ✅ 已完成开发。**背景**：客户一句「发我英文版和日语版」触发智能体单轮产出 7 个发送单元（文字+表格图+Word ×2 语言+中文总结），微信客服 send_msg 5 条/48h 上限被击穿，最后 2 个 Word 文档丢失。 | — | [开发计划](channel/wecom_kf/reply_quota_control_plan.md) |
| 60 | 微信客服 95013 (conversation end) 错误修复 | ✅ 已完成开发 **2026-08-28 生产报错调查（2026-08-29 完成）**：13:24 前后 44 次 `errcode=95013` 刷屏 + 25 条客户消息被丢弃。 | — | — |
| 61 | 微信客服员工-客户对话可见性 | ✅ 已完成开发 **2026-08-29 开发完成**。 | — | — |

## SaaS 多租户

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 29 | 多租户 SaaS 架构 | 租户隔离、订阅计费、权限管理、管理后台 | [设计](system/saas/multi-tenant-saas-design.md) | — |
| 30 | 租户级 Skills | 每个租户维护自己的 Skills 文件夹，按需加载 | [设计](system/saas/tenant_skills_design.md) | — |
| 31 | 订阅权限合并 | 订阅计划与功能权限的统一管理 | [设计](system/saas/subscription_permission_merge_plan.md) | — |

## 前端

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 32 | 前端样式统一 | 统一 UI 组件库、语义化 Token、变体系统 | [设计](research/frontend/phase1-unify-foundation-design.md) | [计划](research/frontend/phase1-unify-foundation-plan.md) |
| 53 | 租户定制提示词前端入口（恢复 + 上线侧栏菜单） | ✅ 已完成开发。 | [设计](infrastructure/prompt-lifecycle-design.md) | — |
| 54 | 定制提示词页模板文件上传 | ✅ 已完成开发。 | [设计](infrastructure/prompt-lifecycle-design.md) | — |
| 34 | 前端 MyTextarea 通用组件 | ✅ 已完成开发。通用大文本框组件：全屏编辑、MD 预览、字数统计，预留 AI 优化/占位符识别 slot。 | [选型+设计](research/frontend/my-textarea-component-research.md) | — |
