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
| 7 | 会话内上下文压缩（中期记忆） | 🔧 部分完成 | 解决单 session 长会话上下文爆 token 问题（web/微信客服/钉钉/飞书/RPA 全渠道通用）。**v2.0 异步方案**：双阈值触发（token 70% 或 消息数 150，均可配置）→ 主流程 fire-and-forget 派发异步压缩任务（Redis SETNX 锁防并发）→ 异步任务执行 LLM 增量摘要 + 原子事务持久化（写 summary + 标记原消息 compacted=true，缺一不可）→ 连续失败 3 次自动转同步降级兜底。保留首尾（HEADER 3 + TAIL 30，可配置）+ 中间段结构化摘要 + 工具结果差异化截断 + 可回滚归档。复用 `MemoryManager.save_conversation_summary` 占位接口落地。**Phase 1+2（基础设施+同步压缩服务）已完成**：4 个 P0 修复（UNIQUE 索引/tenant_id/deepseek 直连/orphan tool 丢弃）+ 6 个 P1 修复，96 单测全通过；待 Phase 3+6（异步派发+Agent 集成）。2026-06-24 | [设计](infrastructure/memory/context_compression_design.md) / [调研](research/context_compression_research.md) | [开发计划](infrastructure/memory/context_compression_dev_plan.md) |

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

## 渠道集成

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 26 | 企业微信客服 AI 绑定 | 📋 待开发 | "扫码绑定 AI 机器人"模式，第三方授权与合规接入。2026-05-26 | [设计](channel/wecom-kf/wecom_kf_design.md) | — |
| 27 | wecom_kf 多媒体消息支持 | 🔧 部分完成 | 后端完成：download_media 方法 + channel_routes 消息处理（附件下载/持久化/传递给 agent）+ external_customers 附件下载代理接口。前端完成：ExternalCustomerService 语音播放器/图片预览/文件卡片 + AttachmentCard 组件。待集成测试。2026-06-14 | — | — |
| 28 | 渠道会话多条消息串行化 | 📋 待开发 | 渠道场景短时间多条消息串行处理，新消息取消旧请求 + 只保留最新意图，解决并发写入混乱和 DeepSeek 400 问题。2026-06-18 | [设计](channel/concurrent-message-serialization-plan.md) | — |
| 29 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | 服务端契约层 + 实现层 + Wire 路由已落地：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/secret_crypto 七模块按 protocol.md §B 签名实现，68 单元测试通过；callback/config/files/ws 路由接入 main.py，channel_factory/channel_config/ChannelType 枚举注册齐全；新增 6 条集成测试全部通过。管理前端 P1.1 已交付；指标+告警 P1.4 部分交付（83 测试通过）。**客户端视觉定位方案已生产代码级落地（2026-06-24）**：① UIA3/MSAA 双失效后改走 Qwen3-VL 多模态视觉定位；② 12 个视觉层核心模块全部实现 + 单测覆盖（QwenVisionLocator / VisionCache SQLite / ScreenCapturer PowerShell 委托 / InputExecutor / OcrVisionLocator 降级 / WeComAutomation 重构 / MessageWatcher / LoginStateDetector 等）；③ Client.App 删除 AutomationStubs + DI 注册真实实现 + 配置完整 vision 段；④ 119 单测全绿（原 36 + 视觉层新增 83）；⑤ 真机回归 3/3 核心场景通过（5 元素定位 / 缓存命中 7-29ms / 指纹隔离）。下一步：Client.App 作为常驻 GUI 进程的真实端到端联调（需服务端部署 + 真实企微账号 + 真实 SendInput 发送），不在自动化测试范围内。2026-06-24 | [协议](system/wecom-personal-rpa-protocol.md) / [设计](system/wecom-personal-rpa-design.md) / [视觉定位设计](system/wecom-personal-rpa-vision-design.md) / [技术突破总结](system/wecom-personal-rpa-vision-breakthrough.md) | [计划](../plans/plan-wecom-personal-rpa.md) / [视觉定位计划](../plans/plan-wecom-personal-rpa-vision.md) |
| 30 | 钉钉渠道接入 | 🔧 部分完成 | 钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。代码（adapter/crypto/media/message_builder/路由/前端配置）已完成，112 单元测试 + 23 集成测试全部通过；待真实钉钉环境联调。2026-06-19 | [设计](channel/dingtalk/integration_guide.md) / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 31 | 飞书渠道接入代码审核 | 🔧 部分完成 | 基于 [integration_guide.md](channel/feishu/integration_guide.md) 审核飞书渠道实现。发现 3 个 P0 问题（adapter 每请求新建导致 token 缓存/速率限制/HTTP 连接池失效 + httpx client 不关闭造成 fd 泄漏；encrypt_key 文档与代码必填规则不一致）、4 个 P1 安全问题（签名比较未用 hmac.compare_digest、时间戳未做偏移校验、GET 回调无鉴权、去重 DB 异常静默丢消息）、4 个 P2 问题（速率限制需迁 Redis、媒体存储违反租户隔离、asyncio.create_task 无引用、bot open_id 失败静默丢群聊消息）。2026-06-19 | — | [审核报告](../plans/feishu-channel-code-review.md) |
| 33 | 渠道上下文丢失修复（channel_messages 事务化 + 连续 user 兜底 + 合并写入时机） | 🔧 部分完成 | 代码完成待联调。P0-1/P0-2/P0-3 全部落地，**一次改造全渠道复用**（wecom_kf/wecom/wecom_personal_rpa/dingtalk/feishu 共 6 个调用点统一走 `ChannelSessionManager.process_and_persist`）。改动：①`src/core/session_queue.py` `enqueue_and_process` 返回 `EnqueueResult`（含 status/merged_input/was_merged，新增 status="error"）；②`src/channels/session.py` 新增 `add_messages_batch_transactional`（事务批量写入）+ `process_and_persist`（user 写入推迟到合并决策后 + 异常兜底）；③`src/core/agent.py` `_reorder_messages_for_llm` 连续 user 兜底（保留最新一条）；④5 个渠道调用点全部替换。修复 P0 必修项 5 个（兜底方向写反 / record 时序 / 异常 fall-through 丢失 user / 批量写入失败返回 error / 测试更新）。新增 31 个单元测试全通过，零回归。**待用户手工测试**。2026-06-23 | [调研](research/wecom-kf-context-loss-research.md) | — |
| 34 | chat_records 表 web/channel 隔离 | 📋 待开发 | ChatRecordDB.list_by_session / list_by_user 未按 source_type 过滤，channel 端（wecom_kf/wecom/dingtalk/feishu）的会话记录会被 web 端会话查询页面拉出来造成混淆。需补 source_type 过滤（web 端默认只查 source_type='chat' 或 NULL）。2026-06-23 | — | — |

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
| 前端样式调研 | [frontend-style-research.md](research/frontend/frontend-style-research.md) | 前端样式统一 |
| 前端 Office 预览调研 | [frontend-office-preview-research.md](research/frontend/frontend-office-preview-research.md) | 前端 Office 预览 |
| HTML 转 PPTX 技术调研 | [html-to-pptx-conversion-research.md](research/html-to-pptx-conversion-research.md) | PPT 技能 PPTX 导出 |
| 企业微信个人账号 RPA 生产级客户端技术方案调研 | [wecom-personal-rpa-client-implementation-research.md](research/wecom-personal-rpa-client-implementation-research.md) | 企业微信个人账号 RPA 接入 |
| 会话内上下文压缩业界方案调研 | [context_compression_research.md](research/context_compression_research.md) | 会话内上下文压缩（中期记忆） |
