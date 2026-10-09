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
| 20261008-runtime-plugin-host | Runtime 执行环境、插件宿主与可视化客户端 | 🔧 部分完成 | A1～A3完成；隔离验收包已交付，待人工及正式发行，第三方暂不开发。 | [唯一架构](system/runtime-plugin-host-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) / [接口](../contracts/runtime-host/v1/README.md) | [统一计划](plans/plan-runtime-plugin-host.md) |
| 20260908-2229 | 外部系统入口（SSO 打开第三方系统） | 🔧 部分完成（Phase 1 开发完成，待真机联调） | 连接中心「外部系统」入口 + SSO 通用契约（direct_url/ticket_redirect/token_param）打开第三方系统；Phase 1 完成待真机联调。 | [方案](system/external-system-entry-design.md) | — |
| 20260918-2045 | Redis 夜间巡检任务（生产专用） | 🔧 部分完成 | background_runner 调度器每日 00:30 巡检生产 Redis（容器 mem_limit 1g）：内存水位（600MB 警告/800MB 严重）、碎片率（仅 used>100MB 判）、AOF 写入/重写状态、键淘汰、连接数、无 TTL 键抽样（上限 1000，超 200 疑似泄漏）。结果以「[Redis巡检]」前缀进主日志（1 条 INFO 汇总 + 越界项 WARNING/ERROR）。REDIS_INSPECTION_ENABLED 门控默认关（测试环境为腾讯云托管无需巡检），生产 .env 已开启待重启生效。改动：新增 src/core/redis_inspection.py + RedisClient.info() + scheduler 注册；单测 11 用例通过，待部署。 | — | — |
| 20260930-1520 | 上下文压缩可观测性与计费改造 | 🔧 部分完成 | 触发：生产 trace tr_d6f82ad4ac6145ac 审计发现 context_compressed span token/cost 恒 0、obs_spans.cost 列全 0、口径不一致。改动：①mid_term 压缩 LLM 真实 usage 全链路透传（重试/续写累计，cache-hit token 规范化修复计费漏记）→ ContextCompressedEvent → span.usage；②obs_spans 两处 INSERT 补 cost 列（calculate_credit_cost 按价目计价，cached_tokens 参与）；③summary_max_tokens 改自适应 clamp(COMPRESS区tokens÷50, 1500, 4096) + finish_reason=length 续写一次兜底（仍截断标 summary_truncated）；④经济性闸门：COMPRESS 区 < 4 万 token 且未接近模型上限时跳过 LLM 摘要走 truncate（50:1 经济性）；⑤obs_traces.metadata 标注 total_tokens_scope=session_record_main，recap 摘要写 recap_tokens。480 单测全绿，待部署观察真实压缩。 | — | — |

## 数字员工 / 子智能体

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260914-1300 | SubagentRegistry 按 agent_id 为 key + 前端展示 agent_id | 🔧 部分完成 | 2026-09-14 修复生产事故：`subagent_definitions` 两条 active 定义（pre-sales / aidefine-sales-assistant）显示名相同，… | — | — |
| 20260908-1431 | 桌面 CLI 无人值守自动任务底座＋微信营销首场景 | 🔧 部分完成 | 桌面 CLI 无人值守任务底座（调度/账本/许可/journal/桌面锁）＋微信营销首场景，与端侧会话任务（20260912-2313）共用底座。 | [底座设计](design/desktop-automation/desktop-cli-automation-design.md) / [场景设计](design/weixin/weixin-marketing-automation-design.md) | [底座计划](plans/desktop-automation/plan-desktop-cli-automation.md) / [微信实施与BOSS衔接](plans/weixin/plan-weixin-marketing-automation.md) |
| 20260908-1432 | BOSS 直聘聊天自动化 | 🔧 部分完成 | B2 场景包（fake 端到端）十审通过入库；进入 B3 Runtime+Provider 真机接线。 | [场景设计 §11](design/weixin/weixin-marketing-automation-design.md#11-第二场景boss-直聘聊天自动化待独立立项) / [端侧接入设计](design/desktop-automation/boss-edge-session-design.md) / [底座设计](design/desktop-automation/desktop-cli-automation-design.md) | [BOSS 端侧接入计划](plans/desktop-automation/plan-boss-edge-session.md) / [BOSS 里程碑](plans/weixin/plan-weixin-marketing-automation.md#12-boss-聊天自动化实施衔接-待独立立项) / [底座计划](plans/desktop-automation/plan-desktop-cli-automation.md) / [VIP筛选+性能埋点](plans/desktop-automation/plan-boss-cli-vip-filter-perf.md) / [详情页打分即打招呼](plans/desktop-automation/plan-boss-detail-greet.md) |
| 20260905-1753 | 营销 App 智能外呼代理 | 🔧 部分完成 | 2026-09-05 完成调研、详细设计与实施规划。 | [设计](design/marketing-call-agent-design.md) | [验证与计划](plans/marketing-call-agent-plan.md) / [GLM 实验执行手册](plans/marketing-call-agent-experiment-runbook.md) |
| 20260817-1154 | 工程审计智能体（客户方案阶段） | ⏸️ 已搁置 | 面向工程管理咨询公司的「四库一平台三智能体」工程审计智能体商务方案（客户需求《工程审计智能体开发建设方案V1.0》）。 | 见 `docs/backups/engineering-audit-agent/`（git 忽略，仅本地） | — |
| 20260602-0955 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | — | — |
| 20260729-1434 | AI 批量视频内容生产系统 | 🔧 部分完成 | AI 批量视频内容生产系统：抽卡式短视频批量生成，方案多轮迭代（v0.4 回归本质）。 | [调研](research/ai-video-production-research.md) / [PRD](system/content-production/ai-video-production-prd.md) / [MVP技术设计](system/content-production/mvp-design.md) / [MVP开发计划](plans/plan-video-gen-mvp.md) / [产品定位重新审视](system/content-production/video-agent-enterprise-positioning-design.md) / [Phase 1 开发计划](plans/plan-video-agent-phase1.md) | — |
| 20260721-1145 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；… | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-marketing-agent-design.md) / [S1发布调度设计](system/digital-employee/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](system/digital-employee/social-media-marketing-agent-dev-plan.md) |

## 工具

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260920-1554 | 知识库文件搜索工具（knowledge_file_search） | 🔧 部分完成（开发+测试完成，待部署验证） | 按原始文件名（documents.title，模糊/精确）定位知识库文档，返回 file_path 供 LLM 用 read 分页读取，解决「提示词按文件名引用知识库文档 → LLM 拿标题当路径 read 连败」（2026-09-20 生产案例：tenant_923f70a485a1 数据分析助手 3 连 read 失败）。与 /api/knowledge/search_documents 语义检索互补不替代；领域逻辑下沉 KnowledgeService.search_documents_by_title，共享范围/可见性/owner 标注公共化复用，模式 A；互引 description + 0 命中引导防与 knowledge_base_search 误用。纯 DB 元数据查询，无计费点。 | — | [开发计划](plans/plan-knowledge-file-search.md) |
| 20260714-1911 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 20260630-1733 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260630-1734 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260702-1250 | 工具总体优化梳理 | 🔧 部分完成（2026-10 复核收口） | 跨工具 token 治理。规范已修订、3c 已完成；余 word/excel 落盘与小项清理。 | [总体设计](tools/tool-overall-optimization-design.md) / [落盘闭环+grep](tools/large-content-retrieval-design.md) / [文件工具对齐](tools/file-tools-claude-code-parity-design.md) | [开发计划](tools/tool-overall-optimization-dev-plan.md) / [落盘闭环计划](tools/large-content-retrieval-dev-plan.md) |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用）、钉钉、飞书渠道接入。

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261008-1603 | 飞书、钉钉对话服务接入 AgentRunner | 🔧 部分完成（开发完成，待部署验收） | 保留原渠道业务，仅接入独立 Runner 并传递可信渠道来源。 | [接入边界](system/agent-application-architecture-design.md#81-后续接入的固定边界) | [飞书](channel/feishu/implementation_plan.md#agentrunner-接入后续独立事项) / [钉钉](channel/dingtalk/implementation_plan.md#agentrunner-接入后续独立事项) / [审核](reviews/feishu-dingtalk-agentrunner-review-2026-10-09.md) |
| 20260910-1259 | 留资线索动态刷新（lead_refresh：意向度 + 需求分条 + 人工归属） | 🔧 部分完成 | **Phase 1 开发+单测完成（2026-09-16），待部署真机验证；Phase 2 §9.5 人工期对话推送（external_push_human：节流推送 + 转人工字段 + 跟进汇总摘要同步 + 不要求留资）开发+单测完成（2026-09-16）**。触发：2026-09-10 产品需求——留资后客户继续交流（智能体轮次 + 转人工期）仅落 `channel_messages`，线索行不再更新，运营页看不到最新客户状态。 | [设计](subagent/pre-sales/lead-capture-refresh-design.md) | — |

## 前端

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260922-1931 | 租户前台知识库「访问授权」矩阵 | 🔧 部分完成（开发完成，待部署验收） | 租户管理员在知识库页面右上角「访问授权」弹框中以矩阵（行=一级栏目、列=数字员工）自助查看/配置数字员工栏目授权，补齐管理后台按员工勾选视角下「未配置=全部允许」不可见的盲区。三态复选框（半选=未配置默认全允许）；首勾弹窗确认收窄；取消全部勾选恢复默认全允许；复用现有 /api/saas/tenant/subagent-knowledge 接口（后端零改动），保存时原样保留跨租户共享项。三态语义已同步到管理后台 TenantMgmt 知识库授权弹框（本租户栏目三态、共享栏目保持二态），前后台 UI 一致。 | — | — |
| 20260602-0956 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend-office-preview-design.md) | — |
| 20260714-1912 | Agent 跨平台桌面客户端 | 🔧 部分完成（H3待人工验收） | 共用 Runner 与 Runtime，UI竞态修复后的首期验收包已重建验证。 | [桌面设计 v3](system/desktop-agent-client-design.md) / [Runner 架构](system/agent-application-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) | [开发计划](plans/plan-desktop-agent-client.md) |
| 20260720-1459 | 多会话后台流式 | 🔧 部分完成 | 2026-07-20 代码与单测完成，待真实环境 E2E 验收。 | [设计](system/multi-session-background-streaming-design.md) | [开发计划](plans/plan-multi-session-background-streaming.md) |

---

## 故障复盘索引

已迁移至独立文件：[incidents/index.md](incidents/index.md)。

---

## 调研报告索引

已迁移至独立文件：[research_index.md](research_index.md)。
