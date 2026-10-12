# docs 文档清理计划（2026-10-12）

> 背景：docs/ 下现存 293 个 md 文档，其中 115 个创建于 3 个月前（早于 2026-07-12，按 git 创建日期含重命名追踪）。经 7 组并行审计，逐一核对文档内容与当前代码的一致性，得出本清理清单。
>
> **判定口径**：A=仍与代码一致（现役）；B=部分过时（核心机制在，细节有出入，保留但建议修正）；C=严重过时/与现有代码明显不符；D=纯历史记录（已完成计划/一次性调研，无现役参考价值）。删除候选 = C + D。
>
> **执行状态**：📋 待执行（本清单已评审落盘，删除动作由人工执行）。

## 1. 删除清单（41 个）

### 渠道（4）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/channel/concurrent-message-serialization-plan.md | 2026-06-18 | D | 已实施完成（src/core/session_queue.py），现行机制由 message-merge.md 承载 |
| docs/channel/wecom_kf/transfer_to_human_optimization.md | 2026-06-23 | D | 已完成，结论并入 wecom_kf_design.md §7.1；frontmatter「待开发」已失真 |
| docs/channel/wecom_kf/message_recall_plan.md | 2026-06-29 | D | 自述 ✅ 已完成，撤回机制现行说明在 message-merge.md §12 |
| docs/channel/wecom_kf/reply_quota_control_plan.md | 2026-07-01 | D | 自述「仅作历史方案记录」，Layer 1 提示词已废除（prompts.py 已核实） |

### 基础设施 / 记忆（4）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/memory/memory_design.md | 2026-05-22 | C | 旧快照，与代码及姊妹文档矛盾（仍称长期记忆未持久化）；保留 docs/infrastructure/memory/memory_design.md |
| docs/infrastructure/observability-dev-plan.md | 2026-06-02 | D | 纯进度记录（Phase 1 完成 / Phase 2-4 未开始，状态已在 design 中） |
| docs/infrastructure/prompt-lifecycle-dev-plan.md | 2026-06-02 | D | 纯进度记录，各 Phase 状态已在 design 中 |
| docs/infrastructure/memory/context_compression_dev_plan.md | 2026-06-24 | D | 自述 Phase 1-8 全部完成上线（2026-07-06） |

### 系统功能 / SaaS / 数字员工 / 知识库（7）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/system/saas/multi-tenant-saas-design.md | 2026-04-08 | C | agent_instances / instance_manager / 旧 billing 机制已不存在；saas.enabled 开关 2026-09-02 移除；计费已改 credit 体系 |
| docs/system/saas/plan_tenant_entry_adjustment.md | 2026-04-20 | D | 自注「历史方案仅供追溯」，/portal 路由已实现 |
| docs/system/saas/plan_business_data_menu.md | 2026-04-26 | D | business_pages 机制已实现（MenuSidebar.vue），计划完毕 |
| docs/system/digital-employee/data-source-import-dev-plan.md | 2026-06-04 | D | ✅ 已完成 |
| docs/system/digital-employee/chat-attachment-data-analysis-design.md | 2026-06-10 | D | 自标「2026-07-20 归档」 |
| docs/system/digital-employee/data-analysis-tool-result-redesign.md | 2026-06-12 | D | 自标「已实现并归档」，一次性诊断改造记录 |
| docs/system/digital-employee/page-metadata-registry-dev-plan.md | 2026-06-10 | D | ✅ 已完成（page_metadata.py / PageMetaSelector.vue 已实现）；design 文档保留 |

### 子智能体 / 旅游报价（5）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/subagent/travel-consultant/archive/route_distance_skill_design.md | 2026-05-12 | D | archive/ 内已归档历史资料，skill 早已实现 |
| docs/subagent/travel-consultant/attraction_search_tool_design.md | 2026-05-18 | C | 工具 2026-10-11 软下线（catalog=False），被统一知识库检索取代 |
| docs/subagent/travel-consultant/hotel_search_tool_design.md | 2026-06-22 | C | 同上 |
| docs/subagent/complaint-handling/complaint-agent-dev-plan.md | 2026-05-25 | D | 已完成开发计划，引用已废弃路径（deploy/db_update.sql、frontend/src） |
| docs/subagent/order-processing/dev_plan.md | 2026-05-26 | D | 计划 29 项未勾选、Phase 4 REST 未落地，引用旧迁移机制 |

### 工具（7）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/tools/browser/browser_agent_tools_research.md | 2026-05-26 | D | 一次性开源框架调研（browser-use/harness 数据），无代码对应物 |
| docs/tools/browser/browser_harness_research.md | 2026-05-26 | D | 一次性可行性调研，结论未落地（实际走 Playwright 路线） |
| docs/tools/text-file/file_tools_redesign_v2.md | 2026-06-12 | D | 重构已完成（read/write/edit/cp 现役），所引 file_reader_tool.py 等已不存在 |
| docs/tools/tool-overall-optimization-design.md | 2026-07-02 | D | 自注「审计时点快照，不要按本文判断代码现状」 |
| docs/tools/tool-overall-optimization-dev-plan.md | 2026-07-02 | D | 已收官（全部 Phase ✅，2026-10-10 P2 清理完成） |
| docs/tools/file-tools-claude-code-parity-design.md | 2026-07-07 | D | 已于 2026-10-10 实现，文中「现状 0-based/无 replace_all」已全部失真 |
| docs/tools/large-content-retrieval-dev-plan.md | 2026-07-07 | D | Phase A-D 全部 ✅；现行设计见 large-content-retrieval-design.md（保留） |

### 企微个人号 RPA（1）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/system/wecom-personal-rpa-client-phase5-test-guide.md | 2026-07-02 | D | 绑定特定 commit（4ac87cc）的一次性真机验证指引，Phase 1-12 已上线 |

### 调研报告（13）

| 文件 | 创建日期 | 判定 | 理由 |
|---|---|---|---|
| docs/research/frontend/frontend-style-research.md | 2026-05-26 | D | 业界样式调研，使命已完成（支撑 phase1 落地） |
| docs/research/frontend/phase1-unify-foundation-design.md | 2026-05-26 | D | 设计已全部实施（plan 自述 9/9 ✅），ideas_finished 已归档 |
| docs/research/frontend/phase1-unify-foundation-plan.md | 2026-05-26 | D | 自述「已完成」的执行记录 |
| docs/research/frontend/my-textarea-component-research.md | 2026-06-11 | D | MyTextarea.vue 已投用，选型记录 |
| docs/research/frontend-office-preview-research.md | 2026-05-28 | D | 选型结论已落地，以 frontend-office-preview-design.md 为准（保留） |
| docs/research/pilotdeck-agent-research.md | 2026-06-01 | D | 外部项目调研，index/ideas 均未登记，建议未被采纳 |
| docs/research/enterprise-agent-platform-research.md | 2026-06-02 | D | 竞品横评，时效数据过时，无落地映射 |
| docs/research/text-to-sql-data-analysis-agent-research.md | 2026-06-02 | D | 数据分析智能体已完成开发，调研使命完成 |
| docs/research/wecom-kf-ai-chatbot-binding-research.md | 2026-06-02 | D | 结论已落地（wecom_kf 渠道 + 个人号 RPA 均已实现） |
| docs/research/html-to-pptx-conversion-research.md | 2026-06-22 | D | 自述「结论已落地，以 ppt_tool_design.md 为准」 |
| docs/research/context_compression_research.md | 2026-06-24 | D | 关联设计+计划已落地（含 2026-09-30 改造），纯历史调研 |
| docs/research/wecom-personal-account-rpa-research.md | 2026-06-22 | D | 路径选型调研（首选安卓无障碍），实际落地走 PC 端 .NET RPA，结论以 wecom-personal-rpa-design.md 为准 |
| docs/research/wecom-personal-rpa-client-implementation-research.md | 2026-06-22 | D | 自注「历史调研（交付结论已废弃）」，WiX/MSIX 安装包方案 2026-07-13 废弃，现以 client-design + publish.ps1 为准 |

## 2. 待定（1 个）

| 文件 | 判定 | 说明 |
|---|---|---|
| docs/channel/wecom/wecom-integration.md | C | 回调路由（实际为 `/t/{tid}/wecom/callback/{config_id}`）与 §4.3 绑定接口描述均与代码不符；但加解密说明与故障排查章节仍有价值，**建议改写而非直接删除**。执行时二选一：改写校正 → 保留；或先摘取有效章节再删 |

## 3. 执行时同步动作

### 3.1 research_index.md 清理（7 行）

删除 `docs/research_index.md` 中以下 8 个条目行：企业级智能体平台调研、Text-to-SQL 调研、企微客服 AI 绑定调研、前端样式调研、前端 Office 预览调研、HTML 转 PPTX 技术调研、会话内上下文压缩业界方案调研、企业微信个人账号 RPA 生产级客户端技术方案调研。

### 3.2 活跃文件中的引用清理

删除文档后，以下**现役文件**中的引用会悬空，需同步清理（已排除待删文件之间的互引和 ideas.md / ideas_finished.md 历史归档链接——归档索引中的失效链接可接受，不必处理）：

| 引用所在文件（现役） | 指向的待删文档 |
|---|---|
| src/channels/wecom_kf/prompts.py | reply_quota_control_plan.md |
| src/tools/_helpers.py | tool-overall-optimization-design.md |
| docs/channel/wecom_kf/message-merge.md | concurrent-message-serialization-plan.md、message_recall_plan.md |
| docs/channel/wecom_kf/wecom_kf_design.md | transfer_to_human_optimization.md、reply_quota_control_plan.md |
| docs/subagent/pre-sales/lead-capture-design.md | transfer_to_human_optimization.md |
| docs/infrastructure/memory/context_compression_design.md | context_compression_dev_plan.md、memory/memory_design.md、context_compression_research.md |
| docs/infrastructure/memory/memory_design.md | context_compression_dev_plan.md、order-processing/dev_plan.md、context_compression_research.md |
| docs/infrastructure/observability-design.md、observability-channel-sessions-design.md | observability-dev-plan.md |
| docs/infrastructure/prompt-lifecycle-design.md | prompt-lifecycle-dev-plan.md |
| docs/system/skill-complete-removal-design.md | memory/memory_design.md |
| docs/system/saas/tenant-credit-billing-design.md | multi-tenant-saas-design.md |
| docs/system/digital-employee/smart-data-analysis-tool-dev-plan.md、tool-messages-persistence-design.md | data-analysis-tool-result-redesign.md、data-source-import-dev-plan.md |
| docs/system/digital-employee/data-analysis-subagent-design.md、knowledge-base/knowledge-base-enhancement-design.md | text-to-sql-data-analysis-agent-research.md |
| docs/system/knowledge-base/unified-kb-resource-table-metadata-design.md | tool-overall-optimization-dev-plan.md |
| docs/tools/large-content-retrieval-design.md | file-tools-claude-code-parity-design.md、tool-overall-optimization-design.md |
| docs/tools/tool-development-spec.md | tool-overall-optimization-design.md、tool-overall-optimization-dev-plan.md |
| docs/tools/browser/browser_visualization_design.md | order-processing/dev_plan.md |
| docs/subagent/travel-consultant/travel_pricing_resource_design.md、travel_subagent_design.md | archive/route_distance_skill_design.md |
| docs/research/org-knowledge-sedimentation-research.md | context_compression_research.md、enterprise-agent-platform-research.md |
| docs/research/ai-agent-experience-daily-report-research.md | enterprise-agent-platform-research.md |
| docs/research/ppt-generation-quality-research.md | html-to-pptx-conversion-research.md |

## 4. 保留但建议顺手修正（B 级，共 38 个）

不删，后续可小修。共性问题：① 路径写 `frontend/src/`（实际 `frontend/web/`）；② 状态头停在「设计中/待开发」实际已实现；③ 引用已废弃的 `deploy/db_update.sql`（现行 `deploy/db_update.yaml`）。

重点项：
- docs/channel/wecom/wecom_setup_verification.md、wecom_kf/wecom_kf_deployment_guide.md：**含明文 Secret/Token/appid 样例，无论删改都应先脱敏**
- docs/infrastructure/memory/context_compression_design.md：补记 2026-09-30 自适应预算 clamp(÷50,1500,4096)+续写+50:1 闸门改造
- docs/infrastructure/llm-failover-design.md：启用方式已改为 `LLM_PROVIDER` a/b/c 链式推导，非 `failover.enabled`
- docs/system/wecom-personal-rpa-protocol.md：PROTOCOL_VERSION 代码已 1.2.0，文档 1.0.0
- docs/system/image-asset-pipeline-design.md：被活规则引用，仅状态头「待开发」过时（已实现）
- docs/channel/dingtalk/onboarding_guide.md：回调路径误写 `{config_id}/{config_id}`、引用已删除的 integration_guide.md

## 5. 审计范围说明

- 115 个候选 = git 创建日期（含重命名追踪）早于 2026-07-12 且现存于 docs/ 的全部 md
- 判定分布：A 级（现役，不动）30 个、B 级（保留待小修）38 个、C 级 6 个、D 级 37 个；C 级中 `multi-tenant-saas-design.md`、`memory/memory_design.md`、`attraction_search_tool_design.md`、`hotel_search_tool_design.md` 4 个直接删除，`wecom-integration.md` 待定
- 删除合计 41 = D 级 37 + C 级 4
- 5 个免审保留：docs/ideas.md、docs/ideas_finished.md（活索引）、cache_usage.md、database_system_table.md、file_usage.md（被 .claude/rules 引用，要求持续维护）
- 保留项中被 .claude/rules/ 或代码注释引用的文档（image-asset-pipeline-design、context-reconstruction-pitfalls、wecom-kf-context-loss-research、observability-channel-sessions-design 等）一律不动
