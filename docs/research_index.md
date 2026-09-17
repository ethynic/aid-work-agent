# 调研报告索引

> 多项功能设计的前期研究索引，不单独对应开发任务。新增调研报告须登记到本文件（见 AGENTS.md 文档登记规范）。

以下调研报告为多项功能设计的前期研究，不单独对应开发任务：

| 调研主题 | 文档 | 关联功能 |
|---------|------|---------|
| 企业级智能体平台调研 | [enterprise-agent-platform-research.md](research/enterprise-agent-platform-research.md) | 可观测性、Prompt 管理、知识库 |
| 基础设施差距分析 | [enterprise-agent-infrastructure-gap-analysis.md](research/enterprise-agent-infrastructure-gap-analysis.md) | 整体规划 |
| Prompt 版本管理调研 | [prompt-version-management-research.md](research/prompt-version-management-research.md) | Prompt 全生命周期管理 |
| AI Agent 可观测性调研 | [observability-design-research.md](research/observability-design-research.md) | 可观测性与质量保障 |
| 知识库 RAG 技术调研 | [enterprise-knowledge-base-rag-research.md](research/enterprise-knowledge-base-rag-research.md) | 知识库能力增强 |
| 知识库行业产品调研与低改动快速增强建议 | [enterprise-knowledge-base-quick-wins.md](research/enterprise-knowledge-base-quick-wins.md) | 知识库能力增强（补充 Phase 1-2 之外的快速增强点） |
| Text-to-SQL 调研 | [text-to-sql-data-analysis-agent-research.md](research/text-to-sql-data-analysis-agent-research.md) | 数据分析智能体 |
| 企微客服 AI 绑定调研 | [wecom-kf-ai-chatbot-binding-research.md](research/wecom-kf-ai-chatbot-binding-research.md) | 企业微信客服 AI 绑定 |
| 前端样式调研 | [frontend-style-research.md](research/frontend/frontend-style-research.md) | 前端样式统一 |
| 前端 Office 预览调研 | [frontend-office-preview-research.md](research/frontend/frontend-office-preview-research.md) | 前端 Office 预览 |
| HTML 转 PPTX 技术调研 | [html-to-pptx-conversion-research.md](research/html-to-pptx-conversion-research.md) | PPT 技能 PPTX 导出 |
| 企业微信个人账号 RPA 生产级客户端技术方案调研 | [wecom-personal-rpa-client-implementation-research.md](research/wecom-personal-rpa-client-implementation-research.md) | 企业微信个人账号 RPA 接入 |
| 会话内上下文压缩业界方案调研 | [context_compression_research.md](research/context_compression_research.md) | 会话内上下文压缩（中期记忆） |
| 社媒内容运营智能体与聚合平台可行性调研 | [social-media-operations-agent-platform-research.md](research/social-media-operations-agent-platform-research.md) | 社媒内容运营智能体与聚合平台 |
| 微信桌面版 RPA 自动化技术方案调研 | [wechat-desktop-rpa-technical-research.md](research/wechat-desktop-rpa-technical-research.md) | 微信搜一搜 RPA（协会采集）；影刀失败后改自研「图像+OCR+键鼠」Python 方案，4 轮 PowerShell 真机验证通过 |
| 微信搜一搜 RPA 命令行工具 | [设计](tools/wechat-souyisou-rpa-design.md) / [连续查询会话交接记录](tools/wechat-rpa-session-handoff.md) | 🔧 部分完成：正式 `collect` 已接入 HWND 会话状态机、10 分钟预算和 Per-Monitor V2；输入采用可信键盘导航与精确回读，… |
| 协会官网优先资料补全 | [设计](tools/association-profile-enrichment-design.md) | 🔧 部分完成：新增严格 14 字段提取、自动站内导航、批量无界面 CLI 和 Excel 输出。 |
| 懂车帝与汽车之家客户留资统一接入可行性调研 | [automotive-platform-lead-integration-research.md](research/automotive-platform-lead-integration-research.md) | CRM 智能体、汽车平台渠道集成 |
| 抖音电商飞鸽客服接入 Agent 可行性调研 | [douyin-shop-pigeon-agent-customer-service-research.md](research/douyin-shop-pigeon-agent-customer-service-research.md) | 抖店/飞鸽客服渠道、Agent 自动接待、转人工 |
| 摘要生成模型性价比实测 | [llm-summary-cost-benchmark.md](research/llm-summary-cost-benchmark.md) | 知识库摘要、lite 通道选型（deepseek/qwen/GLM 三家已实测，含缓存机制对比；2026-09-17 补充外部推送工具调用重放实测：GLM 全漏写鉴权头不可用于工具调用型任务，deepseek-flash 综合最优） |
| 抖音本地生活订单→门店微信群推送系统 私有化部署可行性评估 | [douyin-lifeservice-order-wechat-group-dispatch-research.md](research/douyin-lifeservice-order-wechat-group-dispatch-research.md) | 竞品（TkTok Sys）视频还原；抖音生活服务开放能力接入路径（商家自研 vs 服务商）、微信/企微推送通道选型、功能模块与工作量（一期约 8~10 人月）、预开通账号清单、… |
| 抖音来客订单同步与门店微信播报系统 解决方案（含开发计划与报价，面向客户） | [design/douyin-lifeservice-dispatch/solution-proposal.md](design/douyin-lifeservice-dispatch/solution-proposal.md) | 一期打包价 ¥29.8 万/203 人日/约 14 周交付；通道矩阵（企微内部群 webhook 全自动合规 / 企微客户群群发限频 / RPA 播报为可选项）；抖店发货提醒等为二期可选包 |
| 小红书客服接入 Agent 可行性调研 | [xiaohongshu-agent-customer-service-integration-research.md](research/xiaohongshu-agent-customer-service-integration-research.md) | 小红书电商客服、专业号私信、小程序客服、Agent 自动接待与转人工 |
| AI 智能体行业产品体验提升调研与「工作日报」方案设计 | [ai-agent-experience-daily-report-research.md](research/ai-agent-experience-daily-report-research.md) | 工作日报（个人日报 + 团队日报）、AI 价值证明、续费驱动 |
| 从个人经验到组织能力：AI 智能体组织知识沉淀调研与方案设计 | [org-knowledge-sedimentation-research.md](research/org-knowledge-sedimentation-research.md) | 组织知识沉淀（三层知识架构 + 专家识别 + 自动抽取 + 主动推荐）、个人经验转组织资产、续费护城河 |
| 微信公众号文章搜索「不依赖微信 App」可行性调研 | [wechat-article-search-without-app-feasibility.md](research/wechat-article-search-without-app-feasibility.md) | 搜一搜无 App 外通道；不依赖 App 全域关键词搜文章只能在「搜狗(免费不稳)/商业聚合API(付费稳)/回退App内搜一搜」间三角取舍，无完美解 |
| BOSS 直聘智能招聘 Agent 可行性调研 | [boss-recruiting-agent-research.md](research/boss-recruiting-agent-research.md) | BOSS 简历筛选助手（独立桌面应用）。 |
| BOSS CLI 接入 Web Agent MVP（覆盖上行旧方案） | [设计](design/recruiting/recruiting-cli-agent-integration-design.md) / [MVP 开发计划](plans/recruiting/plan-recruiting-cli-agent-integration.md) | 🔧 部分完成（2026-08-11：M0.0~M0.6 全部完成并过三智能体流程；M0.7 真机验收待进行）：… |
| 网页操作实时视图（Boss CDP 首期，原 login assist / page stream） | [设计](design/recruiting/login-assist-page-stream-design.md) / [开发计划](plans/recruiting/web-operation-live-view-dev-plan.md) / [实验纪要](research/cdp-page-stream-experiment-notes.md) | 📋 设计完成待开发（2026-09-01，v3.2/v2.2 已吸收开发前全仓代码核查与独立复审）：通用 `page-stream/1.0` 首期接入 `aid-runtime + boss-cli… |
| 招聘 CLI 接入 Agent：MVP 后总体架构 | [总体架构基线](design/recruiting/recruiting-cli-agent-post-mvp-architecture.md) | 🧭 总体方向已保留、待 MVP 后分阶段深化：MVP 的用户当前 PC 就是第一台 Windows 执行节点；… |
| BOSS CLI 沟通发消息能力（send-to / send-current） | [原生 CDP 设计 §10.6](design/recruiting/boss-resume-assistant-native-cdp-design.md) | 🔧 部分完成（2026-08-13）：把已真机验证的「搜索找人 + 输入 + 发送」链路封装为两个 CLI 子命令与同名 MCP tool——`send-to <姓名> --message <消息>… |
| BOSS CLI 职位切换能力（list-jobs / select-job） | [原生 CDP 设计 §10.7](design/recruiting/boss-resume-assistant-native-cdp-design.md) | ✅ 已完成开发（2026-08-13 封装 + 真机验收 + 增强，待提交）：封装为 `list-jobs`（只读 `boss_list_jobs`）… |
| 招聘操作智能体「简历库」业务页 | [resume-detail-cli-integration-handoff.md](plans/recruiting/resume-detail-cli-integration-handoff.md) | 🔧 部分完成（2026-08-16）：新表 `bs_recruiting_operator_resumes`（幂等建表 + deploy/db_update.sql 已登记）、… |
| 招聘「职位管理/简历库」UI 重构（列表/详情路由化 + job_id 强关联 + 删 demo 预置） | — | ✅ 已完成开发（2026-09-01 全部子项完成，含④期/A组/回写；开发过程：后端招聘模块 169 集成测试绿（真实 PG）+ 前端 typecheck/build/23 单测绿；未提交）：… |
| 招聘客户端部署封装（npm install 一键装机） | [设计](design/recruiting/recruiting-client-deployment-design.md) / [部署手册 clients/README.md](../clients/README.md) / [同事装机手册](../clients/release/安装手册.md) | 🔧 部分完成（2026-08-24 分发包 v0.2.3，send-to 沟通搜索链路全面通用化重写）：**send-to 通用化**（另一台机器分辨率不同点错搜索按钮暴露：… |
| 招聘面试邀约企微通知（两点式） | [设计](design/recruiting/recruiting-interview-notify-design.md) | 🔧 部分完成（Phase 1 核心闭环已开发，2026-08-19 三智能体全过——测试修 2 真 bug + CR 修 1 个 P1（executor 强转 Pydantic 模型实例致生产路径通知… |
| 浏览器 RPA（纯键鼠路线）深度调查报告 | [browser-rpa-vs-anti-bot-deep-research.md](research/browser-rpa-vs-anti-bot-deep-research.md) | BOSS 简历筛选助手、微信搜一搜 RPA、通用 RPA 底座。 |
| LLM 调用计费缺失审计与修复计划 | [开发计划](plans/llm-billing-gap-audit.md) | 📋 待开发（2026-08-14 审计 + 当日逐点复核 + 与上次计划交叉核对）：原 13 处缺口与 2 处风险全部核实真实，… |
| LLM 计费审计 2026-09-12（季度复审） | [审计报告](plans/llm-billing-audit-20260912.md) | ✅ 已修复（2026-09-12 按 billing_audit.md §3 全量复审：LLM 21+20 处 / Embedding 12 处 / ASR 3 处 / 视频 1 处逐点读上下文核对「… |
| 上传文件存储合规审计 | [审计报告](plans/upload-storage-compliance-audit.md) | ✅ 已修复并发生产、已验证（2026-08-14 审计 + 当日修复 + 当日发生产验证）：主体合规（Phase 1~8 改造有效，持久化入口均走 storage.py 工具函数）。 |
| deepseek-v4-flash 平替模型调研 | [deepseek-v4-flash-replacement-research.md](research/deepseek-v4-flash-replacement-research.md) | LLM 模型选型、计费降本（2026-08-16 调研完成：选定 qwen3.7-flash 0.2/0.8 + enable_thinking:false + 显式缓存，… |
| 小米 MiMo-v2.5 平替可行性调研（扩展） | [mimo-v2.5-replacement-research.md](research/mimo-v2.5-replacement-research.md) | 评估小米 MiMo-v2.5 平替 deepseek-v4-flash（2026-08-16 调研+实测完成）。 |
| LLM 提供商文档入口速查 | [ext/llm-doc-entrances.md](ext/llm-doc-entrances.md) | 记录百炼/DeepSeek/小米 MiMo/火山方舟官方文档入口 URL（2026-08-16 均验证 HTTP 200 可达），供 curl 兜底抓取资料用。 |
| 火山方舟模型平替调研摘要 | [ext/volc-ark-replacement-research.md](ext/volc-ark-replacement-research.md) | 评估火山方舟文本模型平替 qwen3.7-flash（2026-08-16）。 |
| BOSS CLI 部署方案调研：员工电脑本地虚拟机 vs 云桌面 | [boss-cli-vm-vs-cloud-desktop-deployment.md](research/boss-cli-vm-vs-cloud-desktop-deployment.md) | 100 账号/100 台员工电脑场景（2026-09）。 |
| BOSS 矩阵账号—企业微信招聘协同客户方案 | [客户解决方案](solutions/recruiting/boss-wecom-recruiting-service-solution.md) / [实施与验证计划](plans/recruiting/boss-wecom-recruiting-service-rollout-plan.md) | 🔧 部分完成（2026-08-31）：形成约 100 个 BOSS 账号、约 10 台在线 VM 的客户方案，云端虚拟机报价 1,000 元/台/年，按 10 台初算约 10,000 元/年，… |
| 桌面 CLI 无人值守底座与微信/BOSS 就绪度调研（2026-09-08） | [调研](research/weixin-cli/automation-readiness-2026-09-08.md) | 核实微信能力与 Windows/Mac 边界；补核 BOSS 未读/写后证据/候选人/话术/通知现状，通用能力归中立底座设计与计划；本轮无真实发送。 |
| 竣工图纸差异对比（转图片 + 多模态）测试报告 | [cad-multimodal-drawing-comparison-test-report.md](research/cad-multimodal-drawing-comparison-test-report.md) | 图纸差异对比功能（2026-09-04 实验）。 |
| CAD 处理与多模态图纸对比技术报告 | [cad-processing-and-multimodal-drawing-comparison-tech-report.md](research/cad-processing-and-multimodal-drawing-comparison-tech-report.md) | 图纸差异对比功能（2026-09-04）。 |
| 微信公众号历史文章清单获取原理调研（wechat-download-api 源码级分析） | [调研](research/wechat-mp/wechat-download-api-principle-research.md) | 公众号内容入知识库 WPS 托底清单源：机制=管理员扫码会话调公众平台后台 searchbiz/appmsgpublish；结论=方法可自研借鉴（非独门秘籍），Docker 降级为实验对照与应急选项（2026-09-16）。 |
