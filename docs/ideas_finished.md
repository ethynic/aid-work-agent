# 项目开发目录 — 已完成

> 已完成开发的功能归档在此文件。

---

## 基础设施

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 3 | LLM 故障转移 | 提供商故障自动切换，多 Key 轮换与降级策略 | [设计](infrastructure/llm-failover-design.md) | [计划](infrastructure/llm-failover-dev-plan.md) |
| 4 | MCP Server | Model Context Protocol 服务器，支持外部工具集成 | [设计](infrastructure/mcp_server.md) | [计划](infrastructure/mcp_server_dev_plan.md) |

## 系统功能

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 6 | 记忆系统 | 短期记忆（滑动窗口）+ 长期记忆（摘要压缩），会话上下文管理 | [设计](memory/memory_design.md) | [计划](memory/memory_phase1_plan.md) |
| 7 | 回复风格系统 | 可配置回复风格，不同场景的语气和格式控制 | [设计](system/design-reply-style.md) | — |
| 8 | 对话体验优化 | SSE 流式输出优化、消息渲染改进、交互体验提升 | [设计](system/design-chat-experience-optimization.md) | — |

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

## 工具

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 19 | 浏览器自动化工具 | 网页自动化操作、数据采集、Markdown 转换 | [设计](tools/browser/browser_automation_design.md) | — |
| 19.1 | 知识库检索租户隔离 | 知识库检索工具添加 tenant_id 过滤，修复跨租户数据泄露 + 分块 overlap 修复。2026-06-02 | [设计](tools/knowledge-base-search-tenant-isolation-design.md) | [计划](tools/knowledge-base-search-tenant-isolation-dev-plan.md) |
| 21 | PPT 生成工具 | AI 驱动的 PPT 内容生成与模板渲染 | [设计](tools/ppt/ppt_tool_design.md) | — |
| 21 | Word 工具 | Word 文档读取与生成 | [设计](tools/word/word_tool_design.md) | — |
| 22 | PDF 工具 | PDF 文档解析与处理 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 23 | Excel 工具 | Excel 文件读取与数据提取 | [设计](tools/excel/excel_tool_design.md) | — |
| 21 | Excel 工具重构 | ✅ 已完成开发 | 移除 analyze 和 chart 操作（由数据分析工具替代），增强 read 操作（复制 FileReaderTool 的文档级读取能力）。2026-06-09 | [设计](tools/excel/excel-tool-refactor-design.md) | [计划](tools/excel/excel-tool-refactor-dev-plan.md) |
| 24 | HTTP API 适配器 | 通用 HTTP API 调用适配器 | [设计](tools/http_api_adapter_design.md) | [指南](tools/http_api_skill_developer_guide.md) |
| 25 | 文本文件生成工具 | 文本/Markdown 文件生成与内容写入优化 | [设计](tools/text-file/text_file_generator_design.md) | — |
| 21 | 文件操作工具集重新设计 v2 | ✅ 已完成开发。拆分为 read/write/edit/cp 四个工具（对齐 Claude Code 命名），删除 file_list 工具，edit 三种编辑模式（replace_string/replace_section/replace_lines）。Phase 0-8 全部完成，4 个工具 122 单元测试 + 端到端 guizang-ppt-skill 实测通过。2026-06-12 | [设计](tools/text-file/file_tools_redesign_v2.md) | [计划](tools/text-file/file_tools_redesign_v2_dev_plan.md) |

## 渠道集成

| # | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 27 | 企业微信集成 | 应用消息收发、回调处理 | [设计](channel/wecom/wecom-integration.md) | — |
| 28 | 飞书 / 钉钉集成 | 飞书和钉钉渠道适配器实现 | [设计](channel/feishu-dingtalk/channel_integration.md) | — |

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
