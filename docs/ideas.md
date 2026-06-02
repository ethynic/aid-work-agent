# 项目开发目录

> 本文件是所有开发内容的索引，链接设计文档和开发计划文档，跟踪开发状态。

---

## 状态说明

| 状态 | 含义 |
|------|------|
| ✅ 已完成开发 | 设计、开发、测试全部完成 |
| 🔧 部分完成 | 已开始开发，部分阶段完成 |
| 📋 待开发 | 设计完成或进行中，尚未开始编码 |
| 💡 灵感 | 早期想法，尚未正式设计 |

---

## 基础设施

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 1 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。Phase 1 完成，Phase 2-4 未开始。2026-05-29 | [设计](infrastructure/observability-design.md) | [计划](infrastructure/observability-dev-plan.md) |
| 2 | Prompt 全生命周期管理 | 📋 待开发 | 版本控制、Jinja2 模板引擎、A/B 测试、效果评估。草案阶段，预估 12 周。2026-05-28 | [设计](infrastructure/prompt-lifecycle-design.md) | — |
| 3 | LLM 故障转移 | ✅ 已完成开发 | 提供商故障自动切换，多 Key 轮换与降级策略 | [设计](infrastructure/llm-failover-design.md) | [计划](infrastructure/llm-failover-dev-plan.md) |
| 4 | MCP Server | ✅ 已完成开发 | Model Context Protocol 服务器，支持外部工具集成 | [设计](infrastructure/mcp_server.md) | [计划](infrastructure/mcp_server_dev_plan.md) |

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 📋 待开发 | LLM Rerank 重排序、文档级权限、多轮查询改写、RAGAS 质量评估、统一检索流水线。2026-05-28 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | — |
| 6 | 记忆系统 | ✅ 已完成开发 | 短期记忆（滑动窗口）+ 长期记忆（摘要压缩），会话上下文管理 | [设计](memory/memory_design.md) | [计划](memory/memory_phase1_plan.md) |
| 7 | 回复风格系统 | ✅ 已完成开发 | 可配置回复风格，不同场景的语气和格式控制 | [设计](system/design-reply-style.md) | — |
| 8 | 对话体验优化 | ✅ 已完成开发 | SSE 流式输出优化、消息渲染改进、交互体验提升 | [设计](system/design-chat-experience-optimization.md) | — |

## 数字员工 / 子智能体

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 9 | 数据分析智能体 | 📋 待开发 | 双路径架构（Text-to-SQL + pandas 代码生成），Excel/CSV/数据库多源分析。2026-05-29 | [设计](system/digital-employee/data-analysis-subagent-design.md) | — |
| 10 | 数字员工管理 | ✅ 已完成开发 | 实例管理、选择策略、并发控制 | [设计](system/digital-employee/digital-employee-management.md) | — |
| 11 | 旅行顾问智能体 | ✅ 已完成开发 | 旅游报价、路线规划、酒店景点知识库集成 | [设计](subagent/travel-consultant/travel_subagent_design.md) | — |
| 12 | 竞品调研智能体 | ✅ 已完成开发 | 竞品信息收集、HTML 预览、数据结构化输出 | [设计](subagent/competitor-research/competitor_research_subagent_design.md) | — |
| 13 | 售后处理智能体 | ✅ 已完成开发 | 售后工单处理、退款退货流程自动化 | [设计](subagent/after-sales/after_sales_subagent_design.md) | — |
| 14 | 投诉处理智能体 | ✅ 已完成开发 | 投诉分类、处理建议、升级流程 | [设计](subagent/complaint-handling/complaint-agent-design.md) | [计划](subagent/complaint-handling/complaint-agent-dev-plan.md) |
| 15 | 客户跟进智能体 | ✅ 已完成开发 | 客户跟进任务管理、提醒、执行 | [设计](subagent/customer-followup/customer_followup_design.md) | — |
| 16 | 订单处理智能体 | ✅ 已完成开发 | 订单自动化处理流程 | [设计](subagent/order-processing/design.md) | [计划](subagent/order-processing/dev_plan.md) |
| 17 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | [设计](subagent/crm/crm_subagent_design.md) | — |
| 18 | 内容生成通用设计 | ✅ 已完成开发 | 通用内容生成子智能体框架 | [设计](subagent/content_generate_universal_design.md) | — |

## 工具

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 19 | 浏览器自动化工具 | ✅ 已完成开发 | 网页自动化操作、数据采集、Markdown 转换 | [设计](tools/browser/browser_automation_design.md) | — |
| 19.1 | 知识库检索租户隔离 | 🔧 部分完成 | 知识库检索工具添加 tenant_id 过滤，修复跨租户数据泄露。代码已完成，待验证。2026-06-02 | [设计](tools/knowledge-base-search-tenant-isolation-design.md) | [计划](tools/knowledge-base-search-tenant-isolation-dev-plan.md) |
| 20 | 浏览器操作可视化 | 📋 待开发 | Playwright Screencast API 实时推送浏览器操作画面到前端，支持操作标注。2026-05-21 | [设计](tools/browser/browser_visualization_design.md) | — |
| 21 | PPT 生成工具 | ✅ 已完成开发 | AI 驱动的 PPT 内容生成与模板渲染 | [设计](tools/ppt/ppt_tool_design.md) | — |
| 21 | Word 工具 | ✅ 已完成开发 | Word 文档读取与生成 | [设计](tools/word/word_tool_design.md) | — |
| 22 | PDF 工具 | ✅ 已完成开发 | PDF 文档解析与处理 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 23 | Excel 工具 | ✅ 已完成开发 | Excel 文件读取与数据提取 | [设计](tools/excel/excel_tool_design.md) | — |
| 24 | HTTP API 适配器 | ✅ 已完成开发 | 通用 HTTP API 调用适配器 | [设计](tools/http_api_adapter_design.md) | [指南](tools/http_api_skill_developer_guide.md) |
| 25 | 文本文件生成工具 | ✅ 已完成开发 | 文本/Markdown 文件生成与内容写入优化 | [设计](tools/text-file/text_file_generator_design.md) | — |

## 渠道集成

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 26 | 企业微信客服 AI 绑定 | 📋 待开发 | "扫码绑定 AI 机器人"模式，第三方授权与合规接入。2026-05-26 | [设计](channel/wecom-kf/wecom_kf_design.md) | — |
| 27 | 企业微信集成 | ✅ 已完成开发 | 应用消息收发、回调处理 | [设计](channel/wecom/wecom-integration.md) | — |
| 28 | 飞书 / 钉钉集成 | ✅ 已完成开发 | 飞书和钉钉渠道适配器实现 | [设计](channel/feishu-dingtalk/channel_integration.md) | — |

## SaaS 多租户

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 29 | 多租户 SaaS 架构 | ✅ 已完成开发 | 租户隔离、订阅计费、权限管理、管理后台 | [设计](system/saas/multi-tenant-saas-design.md) | — |
| 30 | 租户级 Skills | ✅ 已完成开发 | 每个租户维护自己的 Skills 文件夹，按需加载 | [设计](system/saas/tenant_skills_design.md) | — |
| 31 | 订阅权限合并 | ✅ 已完成开发 | 订阅计划与功能权限的统一管理 | [设计](system/saas/subscription_permission_merge_plan.md) | — |

## 前端

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 32 | 前端样式统一 | ✅ 已完成开发 | 统一 UI 组件库、语义化 Token、变体系统 | [设计](research/frontend/phase1-unify-foundation-design.md) | [计划](research/frontend/phase1-unify-foundation-plan.md) |
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
