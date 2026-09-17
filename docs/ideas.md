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
| 20260917-1201 | 生产 243 仿真环境（staging） | 🔧 部分完成 | 代码层完成（a7394c1a）：SIMULATION_MODE 门控（SMTP/通知 dry-run、附件删除跳过、启动横幅）+ 同步脚本 + sim compose + nginx conf；243 服务器侧已上线（2026-09-17）：建库建账号 + schema 初始化（属主移交 aid_sim_user）+ 代码 rsync + cherry-pick + nginx 白名单上线（白名单放 snippets/ 避免 http 层污染生产的踩坑已回写文档）+ aid-agent-api1 healthy、启动横幅生效、表 149 张自动补齐；待 P1 验收（办公 IP 访问 + 同步真实租户联调）。 | [设计](system/simulation-env-design.md) | — |
| 20260916-1534 | 阿里云 ASR AccessKey 更换指南 | ✅ 已完成 | AK 到期重新申请流程（RAM 用户创建、Secret 一次性保存、AliyunNLSFullAccess 授权）。 | [运维文档](ops/aliyun-asr-accesskey-renewal.md) | — |
| 20260912-2313 | 端侧会话任务执行器（P5 后续） | 🔧 部分完成 | 发送合并后五条实验通过，平均18秒；联系人搜索不再依赖聊天输入框布局。 | [设计](design/desktop-automation/edge-session-task-design.md) | [C0–C5 计划](plans/desktop-automation/plan-edge-session-task.md) / [2026-09-15联测交接](plans/desktop-automation/edge-session-handoff-2026-09-15.md) |
| 20260819-1126 | 母体 Agent 收敛（agent.py Kernel 化） | 📋 待开发 | **不设专项重构、不阻塞其他工作**，继续采用“冻结增长 + 有真实需求时伴生拆分”。 | [原则](system/agent-kernel-convergence-principles.md) | — |

## 系统功能

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260810-2104 | 第一方 CLI / MCP Provider 架构规范 | 📋 待开发 | 所有第一方 CLI 必须成为独立标准 MCP Provider，同时支持 aid-work-agent Web Local Tool Runtime、未来 Agent Desktop、Codex、… | [规范](system/first-party-cli-mcp-provider-standard.md) | 首次落地并入 [BOSS MVP 计划](plans/recruiting/plan-recruiting-cli-agent-integration.md) |
| 20260831-1508 | Agent 用户可见中间消息（verbose） | 🔧 部分完成 | Agent 面向用户的中间进度提示（verbose 事件，确定性文案 + owner 级限流 + 渠道适配）；代码完成，待灰度真机验收。 | [设计](system/agent-intermediate-feedback-design.md) | [开发计划](plans/plan-agent-intermediate-feedback.md) / [灰度回滚手册](plans/verbose-feedback-rollout-runbook.md) |
| 20260828-1421 | zhipu 默认模型切 GLM-5.3-Flash + 价目表多模态标识 | 🔧 部分完成 | ①zhipu 缺省模型 glm-4 → GLM-5.3-Flash（GLM-5 系列首个原生多模态，输入 0.8 / 输出 2.8 元/M tokens，… | [设计](design/weixin/weixin-cli-billing.md) | — |
| 20260908-2229 | 外部系统入口（SSO 打开第三方系统） | 🔧 部分完成（Phase 1 开发完成，待真机联调） | 连接中心「外部系统」入口 + SSO 通用契约（direct_url/ticket_redirect/token_param）打开第三方系统；Phase 1 完成待真机联调。 | [方案](system/external-system-entry-design.md) | — |
| 20260914-1901 | 微信公众号内容入知识库 | 🔧 部分完成 | 公众号文章多通道采集入知识库（回调+URL 直采+手动粘贴+接口对账，图片 VL 解析、500 字总结、按张计费）；P1/P2+接口通道（WP9）完成，待部署验收。 | [设计](system/wechat-mp/wechat-mp-knowledge-ingestion-design.md) | [计划](plans/plan-wechat-mp-knowledge-ingestion.md) |
| 20260916-1830 | 公众号自有号清单源（历史文章导入，**主通道**） | 🔧 部分完成 | 租户扫码自有号，定期拉全量「发表记录」清单（含群发历史），走既有 URL 直采入库；通道优先级：清单源>回调+手动URL>freepublish；开发完成待部署验收（宏陶瓷砖管理员扫码实测+会话 TTL 观察）。 | [设计](system/wechat-mp/wechat-mp-list-source-design.md) | — |
| 20260916-2300 | 租户 API 接口文档通用模板（${APP_ID} 环境变量渲染） | 🔧 部分完成 | 新租户开通免上传接口文档：通用模板 `configs/api_doc_templates/pre-sales-api.md`（按技能名命名，具备 pre-sales-api 技能的智能体均适用），应用号用 ${APP_ID} 占位符、加载期由租户环境变量渲染（凭证类占位符仍由 http_api 运行时替换防泄漏）；租户上传文档优先级最高，特殊表/字段租户仍可单独上传；推送/对话 skill/SSO 三入口已接入；存量 3 租户迁移（配 APP_ID + 删旧文档）待部署后执行。 | — | — |


## 数字员工 / 子智能体

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260917-1200 | spec-to-quotation-list 大项目扩容（轮数/分批/体积） | ✅ 已完成开发 | 解决真实规范书几百条目场景的三大瓶颈：智能体轮数硬编码 20 -> 子智能体可配置（context.max_iterations，缺省 20 上限 100）、items.json 一次性编写 -> 分批编条目 + merge_items.py 合并、xlsx 超 20MB -> --size-budget-mb 图片体积预算自动降质（floor 60）。单测 17 例 + 容器端到端回归通过。 | [设计](subagent/building-supply-chain/spec-to-quotation-scaling-design.md) | — |
| 20260917-1130 | 建筑供应链智能体（spec book 转报价清单） | ✅ 已完成开发 | 引入第三方技能 spec-to-quotation-list（`src/skills/spec-to-quotation-list-1.0.0/`），把设计手册/FF&E 规范/spec book PDF 转成中英双语「室内材料报价清单」Excel（多文件拆分 + 图片嵌入 + 参数表驱动数量公式）。新建子智能体 `subagents/building-supply-chain/`（仅文件系统注册），已用样例 PDF 在容器内完成 analyze/probe/generate/validate 端到端验证。 | — | — |
| 20260917-1000 | 技能依赖自动补全机制（工具 + recap 任务） | ✅ 已完成开发 | 自定义数字员工勾选技能时自动补全其依赖的工具与 recap 任务，根治漏勾配置 bug。技能在 SKILL.md frontmatter 声明 `requires_tools` / `requires_recap`，后端保存兜底（`apply_skill_requirements`）+ 前端勾选联动；已登记 pre-sales-api（完整）、after-sales-api / order-api（http_api）。 | — | — |
| 20260914-1300 | SubagentRegistry 按 agent_id 为 key + 前端展示 agent_id | 🔧 部分完成 | 2026-09-14 修复生产事故：`subagent_definitions` 两条 active 定义（pre-sales / aidefine-sales-assistant）显示名相同，… | — | — |
| 20260908-1431 | 桌面 CLI 无人值守自动任务底座＋微信营销首场景 | 🔧 部分完成 | 桌面 CLI 无人值守任务底座（调度/账本/许可/journal/桌面锁）＋微信营销首场景，与端侧会话任务（20260912-2313）共用底座。 | [底座设计](design/desktop-automation/desktop-cli-automation-design.md) / [场景设计](design/weixin/weixin-marketing-automation-design.md) | [底座计划](plans/desktop-automation/plan-desktop-cli-automation.md) / [微信实施与BOSS衔接](plans/weixin/plan-weixin-marketing-automation.md) |
| 20260908-1432 | BOSS 直聘聊天自动化 | 🔧 部分完成 | 跟踪已打招呼候选人，未读 observer＋候选人绑定＋版本话术/受限决策＋预授权＋人工接管及员工群通知。 | [场景设计 §11](design/weixin/weixin-marketing-automation-design.md#11-第二场景boss-直聘聊天自动化待独立立项) / [底座设计](design/desktop-automation/desktop-cli-automation-design.md) | [BOSS 里程碑](plans/weixin/plan-weixin-marketing-automation.md#12-boss-聊天自动化实施衔接-待独立立项) / [底座计划](plans/desktop-automation/plan-desktop-cli-automation.md) |
| 20260905-1753 | 营销 App 智能外呼代理 | 🔧 部分完成 | 2026-09-05 完成调研、详细设计与实施规划。 | [设计](design/marketing-call-agent-design.md) | [验证与计划](plans/marketing-call-agent-plan.md) / [GLM 实验执行手册](plans/marketing-call-agent-experiment-runbook.md) |
| 20260817-1154 | 工程审计智能体（客户方案阶段） | ⏸️ 已搁置 | 面向工程管理咨询公司的「四库一平台三智能体」工程审计智能体商务方案（客户需求《工程审计智能体开发建设方案V1.0》）。 | 见 `docs/backups/engineering-audit-agent/`（git 忽略，仅本地） | — |
| 20260602-0955 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | — | — |
| 20260720-2103 | 旅游报价数据补全（消费 Excel 模板工具） | 📋 待开发 | 解决旅游报价"内容项不全无法支撑任意版式"问题。 | [设计](system/design-travel-quote-template-engine.md) | [计划](plans/plan-travel-quote-template-engine.md) |
| 20260721-0847 | 旅游顾问行程 HTML 长图导出（图片独立行 + x-to-image 图片内联） | 📋 待开发 | 客户要求行程图片嵌在表格内，现有 Word 路径图片在表格外独立章节、且 Word 对图片布局控制弱、多客户样式难定制。 | [设计](subagent/travel-consultant/itinerary-html-export-design.md) | [计划](plans/plan-itinerary-html-export.md) |
| 20260729-1434 | AI 批量视频内容生产系统 | 🔧 部分完成 | AI 批量视频内容生产系统：抽卡式短视频批量生成，方案多轮迭代（v0.4 回归本质）。 | [调研](research/ai-video-production-research.md) / [PRD](system/content-production/ai-video-production-prd.md) / [MVP技术设计](system/content-production/mvp-design.md) / [MVP开发计划](plans/plan-video-gen-mvp.md) / [产品定位重新审视](system/content-production/video-agent-enterprise-positioning-design.md) / [Phase 1 开发计划](plans/plan-video-agent-phase1.md) | — |
| 20260721-1145 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；… | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-marketing-agent-design.md) / [S1发布调度设计](system/digital-employee/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](system/digital-employee/social-media-marketing-agent-dev-plan.md) |

## 工具

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260806-1318 | 协会信息收集客户端（交付产品） | 🔧 部分完成 | 「协会信息收集」四步流水线（搜官网→Playwright 采集→网络兜底→微信 RPA 取证）改造为交付客户的独立客户端。 | [设计](tools/association-client-design.md) | [开发计划](tools/association-client-dev-plan.md) |
| 20260811-1013 | weixin-cli 第一方微信操作 CLI / MCP Provider | 🔧 部分完成 | 第二个第一方 CLI，面向未来其他项目提供独立 Windows 微信操作能力。 | [设计](design/weixin/weixin-cli-design.md)、[计费关联设计](design/weixin/weixin-cli-billing.md) | [计划](plans/weixin/plan-weixin-cli.md) |
| 20260831-1509 | wecom-cli 第一方企业微信操作 CLI / MCP Provider | 🔧 部分完成 | **M1+M2+M3 已完成（2026-08-31，三轮三智能体流程 + 全部真机端到端复验通过）**：`clients/wecom-cli`（aid-wecom，… | [探测与设计](research/wecom-cli/probe-and-design-20260829.md) | — |
| 20260714-1911 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 20260630-1555 | PDF 工具质量验证增强 | 🔧 部分完成 | P0+企业文档增强+Playwright HTML 转 PDF 已完成：新增 inspect/render_pages/validate、结构化检查、生成后自动校验、页码语义统一和依赖探测；… | [设计](tools/pdf/pdf_tool_gap_analysis_design.md)、[图片支持设计](tools/pdf/pdf-image-support-design.md) | [开发计划](tools/pdf/pdf_tool_quality_validation_dev_plan.md)、[图片支持计划](plans/plan-pdf-image-support.md) |
| 20260630-1733 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 20260630-1734 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 20260702-1250 | 工具总体优化梳理 | 🔧 部分完成 | 跨工具层面的优化梳理登记文档（区别于单工具设计）。 | [总体设计](tools/tool-overall-optimization-design.md) / [落盘闭环+grep](tools/large-content-retrieval-design.md) / [文件工具对齐](tools/file-tools-claude-code-parity-design.md) | [开发计划](tools/tool-overall-optimization-dev-plan.md) / [落盘闭环计划](tools/large-content-retrieval-dev-plan.md) |
| 20260720-2104 | Excel 智能模板填充工具（样例 + 数据 → 按版式生成） | 🔧 部分完成 | 通用**无状态**能力：任何"样例表格 + 结构化数据 → 按样例版式生成 Excel"的场景（旅游报价 / CRM 对账单 / 财务报表 / 贸易报价单）。 | [设计](tools/excel/excel-template-ai-design.md) | [计划](plans/plan-excel-template-ai.md) |
| 20260819-1127 | 多源脏 Excel → 标准模板 LLM 抽取填充 | 🔧 部分完成 | 场景：用户一次上传多家供应商人员增减报表 + 一个标准模板，统一汇总填入模板（样本 `ExcelAI测试.zip`，5 来源 + 1 模板）。 | [调研+决议](tools/excel/excel-etl-gap-analysis.md) / [点数测算](tools/excel/excel-etl-points-estimation.md) | [开发计划](plans/plan-excel-etl-and-email.md) |
| 20260819-1724 | 邮件工具整体审查与整改 | 🔧 部分完成 | 邮件工具（项目最早实现，3 工具单文件 687 行）整体审查与整改：凭据加密、附件场景、开发规范对齐。 | — | [开发计划](plans/plan-excel-etl-and-email.md) |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用 / 个人账号 RPA）、钉钉、飞书渠道接入。
> 企业微信个人账号 RPA（20260616-1526）采用「服务端拉取会话存档 + C# 客户端 PowerShell 自动化」双链路，**第一阶段真机验收已于 2026-07-16 通过**（agent2 消息入库 / 6.2 漏抓率 / outbox 端到端 / 附件与多动作发送 / 生产 Secret 配置）；持续集成与各子功能进度见 20260616-1526 及其已归档子条目（旧编号 #29a~#29d，见 ideas_finished.md）。

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260616-1526 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | **服务端**（已完成）：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/sec… | [协议](system/wecom-personal-rpa-protocol.md) / [架构设计](system/wecom-personal-rpa-design.md) / [客户端设计](system/wecom-personal-rpa-client-design.md) / [绑定管理 Tab 设计](system/wecom-personal-rpa-portal-binding-design.md) / [服务端监听存档设计](system/wecom-personal-rpa-server-archive-listener-design.md) / [SDK 部署](system/wecom-personal-rpa-sdk-deploy.md) | [服务端+部署计划](plans/plan-wecom-personal-rpa.md) / [客户端计划](plans/plan-wecom-personal-rpa-client.md) / [绑定管理计划](plans/plan-wecom-personal-rpa-portal-binding.md) / [服务端监听存档计划](plans/plan-wecom-personal-rpa-server-archive-listener.md) |
| 20260819-1322 | 微信客服回复长图化 + 废除渠道约束提示词 | 🔧 部分完成 | **开发+单测完成（2026-08-19），待部署真机验证**。 | [配额方案（含 2026-08 变更）](channel/wecom_kf/reply_quota_control_plan.md) | — |
| 20260820-2131 | 微信客服处理超时等待提示 | 🔧 部分完成 | **开发+单测完成（2026-08-20），待部署真机验证**。 | [计划](plans/plan-wecom-kf-waiting-indicator.md) | — |
| 20260910-1259 | 留资线索动态刷新（lead_refresh：意向度 + 需求分条 + 人工归属） | 🔧 部分完成 | **Phase 1 开发+单测完成（2026-09-16），待部署真机验证；Phase 2 §9.5 人工期对话推送（external_push_human：节流推送 + 转人工字段 + 跟进汇总摘要同步 + 不要求留资）开发+单测完成（2026-09-16）**。触发：2026-09-10 产品需求——留资后客户继续交流（智能体轮次 + 转人工期）仅落 `channel_messages`，线索行不再更新，运营页看不到最新客户状态。 | [设计](subagent/pre-sales/lead-capture-refresh-design.md) | — |

## 前端

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260602-0956 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend/frontend-office-preview-design.md) | — |
| 20260714-1912 | Agent 跨平台桌面客户端 | 🔧 部分完成 | 2026-08-12 完成前端目录分层第一阶段：现有 `frontend/src/` 原样迁移到 `frontend/web/`，… | [设计](system/desktop-agent-client-design.md) | [开发计划](system/desktop-agent-client-dev-plan.md) |
| 20260720-1459 | 多会话后台流式 | 🔧 部分完成 | 2026-07-20 代码与单测完成，待真实环境 E2E 验收。 | [设计](system/multi-session-background-streaming-design.md) | [开发计划](plans/plan-multi-session-background-streaming.md) |

---

## 故障复盘索引

线上故障与生产事故的复盘文档（存放于 `docs/incidents/`）：

| 故障主题 | 文档 | 关联功能 |
|---------|------|---------|
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](incidents/context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| qwen3.7-flash 工具结果缓存数组化回显故障复盘 | [qwen-tool-message-cache-echo-incident.md](incidents/qwen-tool-message-cache-echo-incident.md) | 2026-08-19 线上故障：显式缓存"末尾标记"把 tool 消息 content 数组化（违反 OpenAI 兼容规范），qwen3.7-flash 概率性(~7%)… |
| 数据分析智能体 max_tokens 截断致空结论误判"无数据" | [analysis-agent-empty-conclusion-incident.md](incidents/analysis-agent-empty-conclusion-incident.md) | 2026-08-26 生产故障：AnalysisAgent 硬编码 max_tokens=4000，deepseek-v4-pro 推理模型烧穿预算返回空结论（completion 恰达上限 + co… |
| BOSS 批量读简历 0 份入库且日志无线索 | [boss-resume-batch-empty-incident.md](incidents/boss-resume-batch-empty-incident.md) | 2026-09-10 客户现场：boss_resume_batch 大部分卡片点击无反应打不开详情、打开的详情与卡片姓名不符（0.2.9 曾把王亦菲简历存到任玮鹤名下）、… |

## 调研报告索引

已迁移至独立文件：[research_index.md](research_index.md)。
