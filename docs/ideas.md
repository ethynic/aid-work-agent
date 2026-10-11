# 项目开发目录

> 本文件是进行中和待开发内容的**纯索引**：每个任务一行，`编号` 为条目首次登记的时间戳（YYYYMMDD-HHMM，历史条目取 git 提交时间），唯一且随时间递增，新增条目取当前时刻。2026-09-16 之前的历史文档中 `#NN` 旧编号已废弃，检索以功能名或时间戳编号为准。每个任务一行，`说明` 只写一句话（≤60 字），`状态` 只写状态短语。开发进度按实际情况更新到任务链接的**开发计划文档**顶部「开发进度」登记区（规范见 AGENTS.md），索引行只同步状态与链接，不要把长文本写进表格。
> 已完成的功能归档在 [ideas_finished.md](ideas_finished.md)。

---

## 状态说明

| 状态 | 含义 |
|------|------|
| ⛔ 已停止 | 方案或前提已作废，不继续开发 |
| 🔧 部分完成 | 已开始开发，部分阶段完成 |
| 📋 待开发 | 设计完成或进行中，尚未开始编码 |
| 💡 灵感 | 早期想法，尚未正式设计 |

---

## 基础设施

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260917-1201 | 生产 243 仿真环境（staging） | 🔧 部分完成 | 代码层完成（a7394c1a）：SIMULATION_MODE 门控（SMTP/通知 dry-run、附件删除跳过、启动横幅）+ 同步脚本 + sim compose + nginx conf；243 服务器侧已上线（2026-09-17）：建库建账号 + schema 初始化（属主移交 aid_sim_user）+ 代码 rsync + cherry-pick + nginx 白名单上线（白名单放 snippets/ 避免 http 层污染生产的踩坑已回写文档）+ aid-agent-api1 healthy、启动横幅生效、表 149 张自动补齐；2026-09-17 更新脚本落地：sim.sh 默认附带代码同步（rsync 生产工作区 + sim-base 基准重放仿真增量，--skip-code 可跳过）+ 新增 agent1_update.sh 验证模式（git 拉取指定版本 + 前端构建，与复现模式互斥）；待 P1 验收（办公 IP 访问 + 同步真实租户联调）。 | [设计](system/simulation-env-design.md) | — |

## 系统功能

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261008-runtime-plugin-host | Runtime 执行环境、插件宿主与可视化客户端 | 🔧 部分完成 | A1～A3完成；隔离验收包已交付，待人工及正式发行，第三方暂不开发；新增 v2 写路径泛化阶段（BOSS B3 依赖，待开发）。 | [唯一架构](system/runtime-plugin-host-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) / [接口](../contracts/runtime-host/v1/README.md) | [统一计划](plans/plan-runtime-plugin-host.md) |
| 20261010-1642 | 系统级防滥用与限额 | 🔧 部分完成（Phase 1 开发完成，未部署） | Phase 1 观察闭环已完成：abuse_policies/abuse_events 两表、引擎 src/services/anti_abuse/（绑定匹配 user > channel（按 users.source 渠道类型） > tenant 兜底 > 平台默认）、定时扫描（300s）+ 事件 1h 去重 + 站内信通知（abuse.rule_triggered）、渠道入口 users.status 停用拦截（fail-open）、防滥用中心页（用量监控/禁用记录/触发记录 + 策略编辑）、用户停用/启用接口。Runner 链路 status 检查缺口登记 Phase 2 统一拦截点解决。 | [设计](system/saas/anti-abuse-policy-design.md) | — |
| 20261010-1930 | 租户管理员通知（计费提醒 / 反滥用告警） | 🔧 部分完成（Phase 1 开发完成，未部署） | 复用统一通知基础设施：站内信恒开兜底 + 外发渠道（飞书/企微/钉钉/邮件）四选一；三事件 billing.credit_low（7 天用量推算，月度幂等）/ billing.tenant_expiry（30/7/1 天档）/ abuse.rule_triggered（Phase 2 挂反滥用 warn_threshold）；管理员逐人展开 + 租户级外发目标（同事件多管理员仅投一次），不新建表。启用需 configs/config.yaml notifications.tenant_events_enabled=true。 | [设计](system/tenant-admin-notification-design.md) | — |
| 20260908-2229 | 外部系统入口（SSO 打开第三方系统） | 🔧 部分完成（Phase 1 开发完成，待真机联调） | 连接中心「外部系统」入口 + SSO 通用契约（direct_url/ticket_redirect/token_param）打开第三方系统；Phase 1 完成待真机联调。 | [方案](system/external-system-entry-design.md) | — |

## 数字员工 / 子智能体

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260908-1432 | BOSS 直聘聊天自动化 | 🔧 部分完成 | B1/B2 完成入库（场景包默认关闭）；B3 起待开发，B0 测试账号授权阻塞中；旁支已交付（见 finished）。 | [BOSS 端侧接入设计](design/desktop-automation/boss-edge-session-design.md) / [底座设计](design/desktop-automation/desktop-cli-automation-design.md) | [BOSS 端侧接入计划](plans/desktop-automation/plan-boss-edge-session.md) |
| 20260905-1753 | 营销 App 智能外呼代理 | 🔧 部分完成 | 2026-09-05 完成调研、详细设计与实施规划。 | [设计](design/marketing-call-agent-design.md) | [验证与计划](plans/marketing-call-agent-plan.md) / [GLM 实验执行手册](plans/marketing-call-agent-experiment-runbook.md) |
| 20260817-1154 | 工程审计智能体（客户方案阶段） | ⏸️ 已搁置 | 面向工程管理咨询公司的「四库一平台三智能体」工程审计智能体商务方案（客户需求《工程审计智能体开发建设方案V1.0》）。 | 见 `docs/backups/engineering-audit-agent/`（git 忽略，仅本地） | — |
| 20260729-1434 | AI 批量视频内容生产系统 | 🔧 部分完成 | AI 批量视频内容生产系统：抽卡式短视频批量生成，方案多轮迭代（v0.4 回归本质）。 | [调研](research/ai-video-production-research.md) / [PRD](system/content-production/ai-video-production-prd.md) / [MVP技术设计](system/content-production/mvp-design.md) / [MVP开发计划](plans/plan-video-gen-mvp.md) / [产品定位重新审视](system/content-production/video-agent-enterprise-positioning-design.md) / [Phase 1 开发计划](plans/plan-video-agent-phase1.md) | — |
| 20260721-1145 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；… | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-marketing-agent-design.md) / [S1发布调度设计](system/digital-employee/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](system/digital-employee/social-media-marketing-agent-dev-plan.md) |

## 工具

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261011-0948 | 数据分析与智能体成本治理 | 📋 待开发 | 310积分事件已归因，设计批量加工、可靠复用及共用上下文，完整任务目标≤90积分。 | [设计](tools/data_analysis/analysis-cost-governance-design.md) / [复盘](incidents/analysis-agent-cost-310-credits-runner-incident.md) | [开发计划](plans/plan-analysis-cost-governance.md) |
| 20260714-1911 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 20260630-1733 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260630-1734 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20261010-1053 | 统一知识库检索工具替代 attraction/hotel_search | 🔧 部分完成 | 两工具由统一知识库工具替代；价格/项目表迁入文档 metadata（raw_payload 契约），替代固定 chunk 位次取表。Phase 1-3 完成；Phase 4a 两工具已软下线（catalog=False，提示词切统一工具，待真机观察后 4b 删除）；Phase 5 真机迁移待逐次授权。 | [设计](system/knowledge-base/unified-kb-resource-table-metadata-design.md) | [开发计划](plans/plan-unified-kb-resource-metadata.md) |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用）、钉钉、飞书渠道接入。

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261008-1603 | 飞书、钉钉对话服务接入 AgentRunner | 🔧 部分完成（开发完成，待部署验收） | 保留原渠道业务，仅接入独立 Runner 并传递可信渠道来源。 | [接入边界](system/agent-application-architecture-design.md#81-后续接入的固定边界) | [飞书](channel/feishu/implementation_plan.md#agentrunner-接入后续独立事项) / [钉钉](channel/dingtalk/implementation_plan.md#agentrunner-接入后续独立事项) / [审核](reviews/feishu-dingtalk-agentrunner-review-2026-10-09.md) |
| 20260910-1259 | 留资线索动态刷新（lead_refresh：意向度 + 需求分条 + 人工归属） | 🔧 部分完成 | **Phase 1 开发+单测完成（2026-09-16），待部署真机验证；Phase 2 §9.5 人工期对话推送（external_push_human：节流推送 + 转人工字段 + 跟进汇总摘要同步 + 不要求留资）开发+单测完成（2026-09-16）**。触发：2026-09-10 产品需求——留资后客户继续交流（智能体轮次 + 转人工期）仅落 `channel_messages`，线索行不再更新，运营页看不到最新客户状态。 | [设计](subagent/pre-sales/lead-capture-refresh-design.md) | — |

## 前端

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260714-1912 | Agent 跨平台桌面客户端 | 🔧 部分完成（H3待人工验收） | 共用 Runner 与 Runtime，UI竞态修复后的首期验收包已重建验证。 | [桌面设计 v3](system/desktop-agent-client-design.md) / [Runner 架构](system/agent-application-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) | [开发计划](plans/plan-desktop-agent-client.md) |

---

## 故障复盘索引

已迁移至独立文件：[incidents/index.md](incidents/index.md)。

---

## 调研报告索引

已迁移至独立文件：[research_index.md](research_index.md)。
