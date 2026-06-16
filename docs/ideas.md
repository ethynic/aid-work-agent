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

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 🔧 部分完成 | Phase 0 知识库分类管理已完成（左侧分类导航 + 分类 CRUD + 文档过滤）。Phase 1-2 待开发：LLM Rerank、文档级权限、查询改写、质量评估。2026-06-04 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) |

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

## 渠道集成

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 26 | 企业微信客服 AI 绑定 | 📋 待开发 | "扫码绑定 AI 机器人"模式，第三方授权与合规接入。2026-05-26 | [设计](channel/wecom-kf/wecom_kf_design.md) | — |
| 27 | wecom_kf 多媒体消息支持 | 🔧 部分完成 | 后端完成：download_media 方法 + channel_routes 消息处理（附件下载/持久化/传递给 agent）+ external_customers 附件下载代理接口。前端完成：ExternalCustomerService 语音播放器/图片预览/文件卡片 + AttachmentCard 组件。待集成测试。2026-06-14 | — | — |
| 28 | 企业微信个人账号 RPA 接入 | 📋 待开发 | 双轨方案：员工个人账号走 PC RPA（云端工作机 + uiautomation/pyautogui），公司公用账号走安卓无障碍 RPA（WorkTool）。云端调度平台 + 多工作机集群，账号设备 1:1 绑定。Phase 1 不接入会话存档，Phase 2 接入做对账兜底。2026-06-16 | [设计](system/wecom-personal-rpa-design.md) | [计划](../plans/plan-wecom-personal-rpa.md) |

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
