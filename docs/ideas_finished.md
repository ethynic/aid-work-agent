# 项目开发目录 — 已完成

> 已完成开发的功能归档在此文件。

---

## 基础设施

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 3 | LLM 故障转移 | 提供商故障自动切换，多 Key 轮换与降级策略 | [设计](infrastructure/llm-failover-design.md) | [计划](infrastructure/llm-failover-dev-plan.md) |
| 4 | MCP Server | Model Context Protocol 服务器，支持外部工具集成 | [设计](infrastructure/mcp_server.md) | [计划](infrastructure/mcp_server_dev_plan.md) |
| 5 | ✅ 主智能体系统提示词优化 | 重写 master_agent.md / subagent_base.md 为原则化结构，新增「文件交付规则」段统一约束"工具生成文件后必须用 cp 注册"。移除与 cp 功能重复的 register_download_file 工具（cp 默认 visible=True、放宽源路径限制）。清理 travel-consultant / competitor-research 的 SUBAGENT.md 与 SKILL.md 冗余注册说明。2026-06-18 完成开发并已测试 | [设计](system/prompt/agent-system-prompt-optimization-design.md) | [计划](../plans/agent-system-prompt-optimization-dev-plan.md) |
| 4 | 系统核心表文档 | 数据库核心表用途与关系文档，覆盖用户/对话/渠道/知识库/数字员工/SaaS/Prompt 管理等 30+ 张系统表。2026-06-18 | [文档](system/database_system_table.md) | — |
| 5 | 缓存使用情况文档 | 系统缓存使用全景文档，覆盖 Redis 缓存、内存缓存、数据库去重共 17 类缓存，含键模式、TTL、失效策略。2026-06-18 | [文档](system/cache_usage.md) | — |
| 6 | 文件存储使用情况文档 | 系统文件存储全景文档，覆盖新旧双轨路径、文件命名规范、目录结构、清理策略。2026-06-18 | [文档](system/file_usage.md) | — |
| 8 | 服务器部署现状文档 | 记录腾讯云服务器（124.222.3.254）当前部署架构：Nginx 反代 + 生产/测试双 Docker 容器 + PostgreSQL 单实例双库 + 腾讯云 Redis，含端口/目录速查、运维命令、故障排查与关联文档索引。2026-06-29 | [文档](../deploy/服务器部署现状.md) | — |
| 10 | 三智能体开发流程规范 | 非平凡开发任务（新功能/Phase/多文件改动）的标准流程：开发智能体→测试智能体（独立测试+回归+启动安全检查）→CodeReview智能体（独立审查+修复必要问题）→主控者提交前终检（import/build）→fetch+commit+push。三智能体串行、独立判断，避免"自己写自己测"盲区，确保提交即上线不挂服务器。ZCode/Claude Code/Codex 三工具共同遵循。2026-07-01 | [规范](../.claude/rules/dev_workflow.md) | — |
| 7 | 会话内上下文压缩（中期记忆） | 解决单 session 长会话上下文爆 token 问题（web/微信客服/钉钉/飞书/RPA 全渠道通用）。v3.0 独立模块化方案：对外唯一入口 `compress_session(session_id, source_type, *, force=False)`，内部自动从 DB 解析 tenant/user/subagent 元数据。双阈值触发（token 70% 或 消息数 200）→ Agent 主流程同步 await → 原子事务持久化（写 summary + 标记原消息 compacted=true）→ LLM 失败自动同步硬截断降级。Phase 1-8 全部完成并上线 2026-07-06（同步压缩主链路 + Trace + 指标 + 管理后台 + 补漏扫描 + 已上线渠道联调）。**⚠️ 备注**：钉钉/飞书/企微 RPA 三个未上线渠道的压缩联调由渠道集成侧在该渠道上线时负责，不属于本功能未完成项。355 单测通过。 | [设计](infrastructure/memory/context_compression_design.md) / [调研](research/context_compression_research.md) | [开发计划](infrastructure/memory/context_compression_dev_plan.md) |
| 11 | 租户数据迁移 | 跨数据库租户数据迁移（知识库 + 业务表），UUID 稳定标识符 + replace/merge 模式 + Excel 导出导入。2026-06-08 | [设计](infrastructure/tenant-data-migration.md) | — |
| 12 | Gunicorn 多 Worker 定时任务单 Worker 执行 | Gunicorn 多 worker 部署下，scheduled_tasks 表内的定时任务会被每个 worker 重复触发。方案：`post_fork` hook 约束仅 worker-0 启动调度器，其他 worker 跳过；任务执行层用 Redis 锁兜底防滚动重启期间双跑。时间敏感性低（5-10 分钟延迟可接受）。2026-06-29 | [设计](infrastructure/scheduled-tasks-single-worker-design.md) | — |

## 系统功能

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 6 | 记忆系统 | 短期记忆（滑动窗口）+ 长期记忆（摘要压缩），会话上下文管理 | [设计](memory/memory_design.md) | [计划](memory/memory_phase1_plan.md) |
| 7 | 回复风格系统 | 可配置回复风格，不同场景的语气和格式控制 | [设计](system/design-reply-style.md) | — |
| 8 | 对话体验优化 | SSE 流式输出优化、消息渲染改进、交互体验提升 | [设计](system/design-chat-experience-optimization.md) | — |
| 19 | Skill 版本化触发重载 | 会话中已加载过的 skill，当 SKILL.md frontmatter `version` 提升后，强制 LLM 重新 `use_skill` 获取最新指南。**纯 prompt 驱动方案在 DeepSeek 上验证不可靠**（LLM 看到上下文有旧指南就跳过 use_skill），改为代码层兜底。改动：①`SkillRegistry.get_descriptions`/`SkillLoader.get_skill_descriptions` 在每条描述末尾追加 `(vx.y.z)`；②`UseSkillTool.execute` 返回新增独立 `skill_version` 字段；③`SkillLoader.parse_skill_md` 读取 version 时优先顶层 `version`，缺失则回退 `metadata.version`，兼容项目中 8 个 skill 把版本写在 `metadata.version` 的约定；④`Agent` 新增 `_get_last_use_skill_version` / `_check_skill_version_consistency` 两个辅助方法；⑤`skill_execute` 执行前强制版本校验：扫会话 memory 历史，取该 skill 最近一次 `use_skill` 返回的 `skill_version`，与 registry 当前版本比较，不一致/无记录/缺字段 → 拒绝执行脚本，返回错误"技能版本已更新 vX→vY，请先 use_skill 重新加载"，主循环 + 子智能体循环两处拦截；⑥prompt 模板移除版本校验规则段（已由代码兜底）。2026-06-23 | — | — |
| 20 | Redis 缓存管理页 | 平台管理后台新增「Redis 缓存」页面，供平台管理员枚举、查看、搜索、删除 Redis 键值。SCAN 游标式枚举（禁用 KEYS），按 `CacheKeys` 26 个前缀分组 + 裸键告警，单键详情按 Redis 类型分支渲染，敏感值脱敏，删除操作二次确认 + `tlog` 审计。Phase 1 MVP（列表/查看/单键删除），Phase 2 增强批量删除 + overview 缓存。与 [cache_usage.md](system/cache_usage.md) 互补：规范约束写入，工具支撑排查。2026-07-06 | [设计](system/design-redis-cache-admin.md) | — |

## 数字员工 / 子智能体

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 10 | 数字员工管理 | 实例管理、选择策略、并发控制 | [设计](system/digital-employee/digital-employee-management.md) | — |
| 11 | 旅行顾问智能体 | 旅游报价、路线规划、酒店景点知识库集成 | [设计](subagent/travel-consultant/travel_subagent_design.md) | — |
| 12 | 竞品调研智能体 | 竞品信息收集、HTML 预览、数据结构化输出 | [设计](subagent/competitor-research/competitor_research_subagent_design.md) | — |
| 13 | 售后处理智能体 | 售后工单处理、退款退货流程自动化 | [设计](subagent/after-sales/after_sales_subagent_design.md) | — |
| 14 | 投诉处理智能体 | 投诉分类、处理建议、升级流程 | [设计](subagent/complaint-handling/complaint-agent-design.md) | [计划](subagent/complaint-handling/complaint-agent-dev-plan.md) |
| 15 | 客户跟进智能体 | 客户跟进任务管理、提醒、执行 | [设计](subagent/customer-followup/customer_followup_design.md) | — |
| 16 | 订单处理智能体 | 订单自动化处理流程 | [设计](subagent/order-processing/design.md) | [计划](subagent/order-processing/dev_plan.md) |
| 18 | 内容生成通用设计 | 通用内容生成子智能体框架 | [设计](subagent/content_generate_universal_design.md) | — |
| 19 | 旅游报价酒店局部替换 | 客户换酒店时只重算住宿费用，其他 items 不变；按城市定位、生成新报价单。update_hotel.py 按酒店名匹配（不依赖 LLM 传 doc_id）、generate.py 输出加 internal_data、hotel.py 支持 name_overrides、14 个单元测试全通过、真实环境端到端实测通过。2026-06-22 | [设计](system/design-travel-quote-hotel-swap.md) | [计划](../plans/plan-travel-quote-hotel-swap.md) |
| 26 | 报价单生成支持指定酒店 | ✅ 已完成开发 | generate.py 新增可选参数 hotel_overrides，客户明确指定的酒店一开始就生效。2026-06-29 优化首次生成的地点定位：优先精确匹配，景区名与行政区名不一致时按剩余住宿顺序应用，由 Agent 在调用前判断酒店与行程是否合理；update_hotel.py 对已有报价仍严格按城市定位。新增场景测试通过。 | [设计](subagent/travel-consultant/generate-quote-hotel-override-design.md) | [开发计划](plans/plan-generate-quote-hotel-override.md) |
| 35 | 旅游报价多人团动态车辆组合 | ✅ 已完成开发 | 车型推荐改为动态规划，按“最少车辆数 → 对应计价模式总单价最低 → 空座最少”选择组合；公里计价使用 per_km_rate 比较并按车型分别计价汇总，支持 100 人以上混合车型报价，避免 daily_rate 为空导致异常。新增 150 人团队回归测试。2026-07-01 | — | — |
| 36 | Markdown 中文 PDF 渲染修复 | ✅ 已完成开发 | md_to_pdf 改为 Markdown→HTML 后优先使用 Playwright/Chromium 打印，fpdf2 仅作带告警降级；使用 session_64735a204d5f 的真实中文旅游行程重放，Playwright 输出 2 页 PDF，中文、表格和分页渲染正常，视觉校验通过，PDF 相关测试 127 项通过。2026-07-01 | — | — |
| 37 | HTML 生成清洗与长文档预览修复 | ✅ 已完成开发 | write 工具支持从“模型说明文字 + ```html 围栏 + 尾部说明”中准确提取完整 HTML，同时保留 Markdown 内普通代码块；HTML 预览 iframe 增加 min-height:0 高度约束，并在同源文档加载后覆盖常见的 100vh/overflow:hidden 打印样式，恢复长文档纵向滚动。write 单测 37 项通过，前端生产构建通过。2026-07-01 | — | — |
| 38 | PDF.js 最小化前端预览 | ✅ 已完成开发 | PDF 预览由浏览器原生 iframe 改为前端动态加载 PDF.js 5.4.624，以 Canvas 逐页渲染并纵向排列；无工具栏、分页、搜索或缩放控件，仅保留自然滚动查看，下载仍使用原始 PDF。PDF.js 与 worker 独立异步加载，不增加首页主包；版本要求 Node 20.16+ 或 Node 22.3+，匹配服务器 node:22-alpine，前端类型检查及生产构建通过。2026-07-01 | — | — |

## 工具

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 19 | 浏览器自动化工具 | 网页自动化操作、数据采集、Markdown 转换 | [设计](tools/browser/browser_automation_design.md) | — |
| 19.1 | 知识库检索租户隔离 | 知识库检索工具添加 tenant_id 过滤，修复跨租户数据泄露 + 分块 overlap 修复。2026-06-02 | [设计](tools/knowledge-base-search-tenant-isolation-design.md) | [计划](tools/knowledge-base-search-tenant-isolation-dev-plan.md) |
| 21 | PPT 生成工具（基础版） | ✅ 已完成开发，历史基础架构已由 #34 增强方案接续；保留设计文档用于追溯。 | [设计](tools/ppt/ppt_tool_design.md) | — |
| 34 | PPT 工具增强与 HTML 转 PPTX 导出 | ✅ 已完成开发。Phase 0-8 全部完成：结构化 `instruction/content/content_type/output_name/export_mode` 入参、SlideDeckSpec、Node/PptxGenJS 渲染与回退、11 类布局、HTML 高保真/可编辑/both 导出、DOM 原生对象重建与局部栅格化、可审计模板跟随、统一 PPTQualityValidator、layout/QA 报告、strict 交付门禁，以及主/子智能体和网页 PPT skill 的直接调用提示。真实 Playwright、模板、损坏 PPTX、双输出、PPTX 重开、prompt/Python/Node 测试通过。2026-07-01 | [设计](tools/ppt/ppt_tool_enhancement_design.md) | [开发计划](tools/ppt/ppt_tool_enhancement_dev_plan.md) |
| 21 | Word 工具 | Word 文档读取与生成 | [设计](tools/word/word_tool_design.md) | — |
| 22 | PDF 工具 | PDF 文档解析与处理 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 23 | Excel 工具 | Excel 文件读取与数据提取 | [设计](tools/excel/excel_tool_design.md) | — |
| 21 | Excel 工具重构 | ✅ 已完成开发 | 移除 analyze 和 chart 操作（由数据分析工具替代），增强 read 操作（复制 FileReaderTool 的文档级读取能力）。2026-06-09 | [设计](tools/excel/excel-tool-refactor-design.md) | [计划](tools/excel/excel-tool-refactor-dev-plan.md) |
| 24 | HTTP API 适配器 | 通用 HTTP API 调用适配器 | [设计](tools/http_api_adapter_design.md) | [指南](tools/http_api_skill_developer_guide.md) |
| 25 | 文本文件生成工具 | 文本/Markdown 文件生成与内容写入优化 | [设计](tools/text-file/text_file_generator_design.md) | — |
| 22 | Pandoc 安装与部署 | word_process 工具的 md_to_word 操作依赖 Pandoc 命令行工具。Docker/Linux 部署已内置（`Dockerfile:106`）；Windows/macOS 本地开发需手动安装，缺失时报 `[WinError 2]`。2026-06-22 | [安装部署指南](tools/md-to-word/pandoc-install-guide.md) | — |
| 23 | 酒店知识库搜索工具（hotel_search） | 仿 attraction_search 新增酒店专项搜索工具，名称优先+向量兜底检索 source_type='hotel_resource'，返回酒店信息+价格明细表（chunk_index=1，通用 knowledge_base_search 拿不到）。客户直接问酒店或酒店价格时使用。通过 inherit:true 对旅游顾问子智能体可用。2026-06-22 | [设计](subagent/travel-consultant/hotel_search_tool_design.md) | [计划](../plans/plan-hotel-search-tool.md) |
| 24 | 景点知识库搜索工具（attraction_search） | 旅游顾问子智能体的景点专项搜索工具，纯向量检索 source_type='attraction_resource'。补登记（此前漏登）。2026-05-14 | [设计](subagent/travel-consultant/attraction_search_tool_design.md) | — |
| 33 | x-to-image 内容转图片服务 | 将文本/Markdown/HTML 渲染为一张尺寸可控的长图(PNG)，Playwright **headless** 全页截图 + Pillow 拼接/截断/体积控制；核心逻辑在 src/services/x_to_image/，薄工具 src/tools/image/ 暴露给 agent。输出写入临时目录并返回临时文件路径（不走下载注册）。v1（文本/MD/HTML）已完成开发，Phase 1-5 全部通过（单元 90+集成 7 测试），PDF/Word 预留扩展点待 v2。2026-07-01 | [设计](tools/x-to-image/x-to-image-design.md) | [开发计划](tools/x-to-image/x-to-image-dev-plan.md) |
| 22.1 | 研学报价技能 Prompt 稳定性优化（方案 A） | ① 同一行程多次报价金额漂移，通过固化 itinerary_parser 的 prompt 规则收敛方差，实测 5 次连续调用 hash 完全一致；② 重构 generate.py 返回结构：items→rows 字段名对齐 Excel 中文表头；③ 修复人均报价与总价÷人数不恒等的取整误差；④ SKILL.md 加"对客户回复口径"硬约束，避免 AI 对客户提及"成本/利润"；⑤ attraction.py prompt 新增禁脑补规则，禁止按比例推算儿童票/老人票；⑥ SUBAGENT.md 加景点/项目白名单硬约束（搜索原则+详细行程格式+输出自检三处）；⑦ itinerary_parser.py 第7条 prompt 强化跨城用车段提取；⑧ subtotal 字段统一为团队人均口径（门票÷人数、酒店按"间"计费、导游÷人数等）；⑨ 彻底移除 profit_rate（单价本身已含利润），不再二次加价。2026-06-14~16 | [设计](system/design-travel-quote-prompt-stability.md) | [计划](../plans/plan-travel-quote-prompt-stability.md) |
| 21 | 文件操作工具集重新设计 v2 | ✅ 已完成开发。拆分为 read/write/edit/cp 四个工具（对齐 Claude Code 命名），删除 file_list 工具，edit 三种编辑模式（replace_string/replace_section/replace_lines）。Phase 0-8 全部完成，4 个工具 122 单元测试 + 端到端 guizang-ppt-skill 实测通过。2026-06-12 | [设计](tools/text-file/file_tools_redesign_v2.md) | [计划](tools/text-file/file_tools_redesign_v2_dev_plan.md) |

## 渠道集成

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 27 | 企业微信集成 | 应用消息收发、回调处理 | [设计](channel/wecom/wecom-integration.md) | — |
| 28 | 飞书 / 钉钉集成 | 飞书和钉钉渠道适配器实现 | [设计](channel/feishu-dingtalk/channel_integration.md) | — |
| 29 | 飞书渠道对接（完整实施） | 修复 FeishuAdapter 错误实现（AES 密钥、签名验证），补齐 crypto/media 子模块、连接池复用、长消息拆分、速率限制、欢迎消息，完善路由层 challenge-response 验证与事件解密。60 项自动化测试全通过。2026-06-19 | [方案](channel/feishu/implementation_plan.md) / [实施](channel/feishu/integration_guide.md) | — |
| 32 | 微信客服转人工工具优化（schema + 渠道隔离） | ✅ 已完成开发 | 优化 transfer_to_human：①reason 改为必填；②渠道隔离完全由工具 execute 段的 get_kf_context 判断，非微信渠道返回「当前渠道未提供人工服务」友好提示（usage_guide 清空，因 LLM 看不到当前渠道，提示词约束无效）；③移除 LLM 路径上的关键词校验，新增 allow_agent_transfer 开关；④会话 metadata 记录 transfer_source=agent。代码 + 10 单元测试已落地，设计文档与 wecom_kf_design.md §7.1 已同步。2026-06-23 | [设计](channel/wecom_kf/transfer_to_human_optimization.md) | [计划](channel/wecom_kf/transfer_to_human_optimization_plan.md) |
| 33 | 渠道上下文丢失修复（channel_messages 事务化 + 连续 user 兜底 + 合并写入时机 + send_response 统一封装） | 已通过用户手工测试。P0-1/P0-2/P0-3 全部落地，**一次改造全渠道复用**（wecom_kf/wecom/wecom_personal_rpa/dingtalk/feishu 共 6 个调用点统一走 `ChannelSessionManager.process_and_persist`）。改动：①`src/core/session_queue.py` `enqueue_and_process` 返回 `EnqueueResult`（含 status/merged_input/was_merged，新增 status="error"）；②`src/channels/session.py` 新增 `add_messages_batch_transactional`（事务批量写入）+ `process_and_persist`（user 写入推迟到合并决策后 + 异常兜底）+ `make_send_response`（消除 6 处 _send_response 重复，净减 70 行）；③`src/core/agent.py` `_reorder_messages_for_llm` 连续 user 兜底（保留最新一条）；④6 个渠道调用点全部替换。修复 P0 必修项 6 个（兜底方向写反 / record 时序 / 异常 fall-through 丢失 user / 批量写入失败返回 error / 测试更新 / wecom_kf NameError）。新增 40 个单元测试全通过，零回归。2026-06-24 | [调研](research/wecom-kf-context-loss-research.md) | — |
| 37 | wecom_kf 用户撤回消息处理（同批次剔除 + 跨批次标记 + 后台可见） | 已完成：①撤回事件识别（origin=4，event_type=user_recall_msg，读 event.recall_msgid 而非 event.msgid）+ 事件去重；②同批次剔除（recalled_msgids_in_batch）；③channel_messages 加 is_recalled/recalled_at 字段；④合并消息 metadata 写入 merged_from_msgids/merged_segments（同批次 _build_merged_message + 跨批次 session_queue 缓冲区 segments 升级）；⑤跨批次标记 mark_recalled_message（单条/合并部分撤回重建 content）；⑥合并窗口内撤回兜底 remove_merge_segment 清理缓冲区；⑦上下文重建剔除（_load_channel_history 显式 include_recalled=False，get_messages 默认过滤）；⑧外部接待客户后台视图撤回徽章（get_messages_paginated 返回 is_recalled/recalled_at，前端 BaseBadge 整条撤回/部分撤回 + 删除线）；⑨trace 视图撤回标记精确关联（2026-07-02 新增）：obs_traces 新增 user_message_id 字段，process_and_persist 写入 channel_messages 后回填（双轨覆盖：set_user_message_id 覆盖 worker 未处理 + update_user_message_id UPDATE 覆盖 worker 已处理），monitor.py 改为按 message_id 精确匹配，彻底消除"内容相同/前缀模糊"导致的误标；历史 trace user_message_id 为 NULL 时不显示撤回标记（可接受降级）。2026-07-02 | — | [开发计划](channel/wecom_kf/message_recall_plan.md) |
| 35 | 渠道/web 上下文重建两大病根修复（来源分流 + 窗口裁剪方向） | #33 的 P0 修复未覆盖的两个真因，均为读取侧 bug。**病根A（来源不分流）**：`agent.py` 上下文重建 `if db_messages:` 盲信 chat_messages，渠道会话若有迁移期残留就永不读 channel_messages → 反复问已确认信息、一旦错乱后续全错。修复：`ChannelSessionManager.is_channel_session` 判定，渠道读 channel_messages、web 读 chat_messages，严格分离。**病根B（裁剪方向反了，更严重）**：`get_messages` 与 `MessageDB.list_by_session` 都用 `ORDER BY id ASC LIMIT N`，返回**最老的 N 条**，长会话超过 N 后最近一轮 assistant 回复被整体切掉（trace 取证：连续两 trace prompt token 完全相同、messages[0..98] 逐条相同、615/616 被跳过）→ agent 看不到上一轮提问，用户"OK"失指代、重复执行。web+channel 都中招（web 仅因短会话没触发）。修复：子查询取最近 N 条再正序 + `_reorder_messages_for_llm` 末尾裁掉开头非 user 对齐到 user 边界（防 DeepSeek 400）。防御性收尾：`short_term._load_from_db` 渠道会话不读 chat_messages。全部编译+测试通过（54 passed），生产快照验证修复后 615/616/617 全部进窗口。2026-07-06 上线。 | [调研](research/wecom-kf-context-loss-research.md) §9~§10 | — |

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
| 34 | 前端 MyTextarea 通用组件 | 📋 待开发 | 通用大文本框组件：全屏编辑、MD 预览、字数统计，预留 AI 优化/占位符识别 slot。复用现有 marked+highlight.js，自研不引第三方编辑器。2026-06-11 | [选型+设计](research/frontend/my-textarea-component-research.md) | — |
