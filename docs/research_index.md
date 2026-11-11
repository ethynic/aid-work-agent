# 调研报告索引

> 多项功能设计的前期研究索引，不单独对应开发任务。新增调研报告须登记到本文件（见 AGENTS.md 文档登记规范）。

以下调研报告为多项功能设计的前期研究，不单独对应开发任务：

| 调研主题 | 文档 | 关联功能 |
|---------|------|---------|
| Runtime 1.0.0 与业务插件解耦 | [独立评审](research/runtime-plugin-decoupling-review-20261111.md) | [Runtime 1.0.0](plans/plan-runtime-plugin-host.md#1610-runtime-与插件-100-实施计划待开发)，第 1～3 项已确认，其余待逐条确认。 |
| agent3 Runner 端到端混合负载实验 | [实测报告](research/agent3-runner-end-to-end-load-test-20261106.md) | Runner 执行槽隔离 Phase 4：三档 8/8 任务完成结算，双槽并行已验；同步阻塞、告警与旧客户端仍待验。 |
| WeCom CLI OCR 使用点清单 | [调研](research/wecom-cli-ocr-inventory-20261106.md) | 2026-11-06：9 组原使用场景，区分仍运行的入口、退役脚本与发行包 OCR 依赖。 |
| 「社会治理智能体」技术要求评估 | [评估](research/social-governance-agent-requirement-assessment.md) | 2026-11-03：高校采购标书 vs 本项目能力对照；约 60% 吻合可投，缺口为工作流编排与知识空间，投标前需定应对策略。 |
| Runtime 与 CLI 安装升级简化评估 | [评估](research/runtime-cli-install-upgrade-simplicity-review.md) | 2026-10-30：审查安装、任务恢复、验包与信任耦合，建议覆盖升级优先、重装白名单保留配置。 |
| 企业微信群接入自研 Agent 自动问答 | [调研](research/wecom-group-agent-bot-research.md) | 2026-10-22：内部群 @智能机器人可走官方 API；外部客户群不能按同一路线承诺，现有应用渠道需另做适配。 |
| 连接器架构与飞书连接器实现路径调研（WorkBuddy/豆包工作/灵光） | [调研](research/connector-architecture-feishu-research.md) | 连接中心 Phase 2 飞书连接器输入（2026-10-27）：WorkBuddy=官方确认 MCP+CLI 连接器框架+代建飞书自建应用授权；飞书官方 2026-03 开源 lark-cli（17.9k★，Agent 集成文档齐全），官方 MCP 已停更；建议一期 lark-oapi SDK 直连+每租户自建应用（路径 B）。 |
| BOSS CLI 视觉识别与恢复真机实验 | [实验记录](research/boss-visual-recovery-machine-experiments.md) | 累计126次实调；霍接收预览关闭故障链通过，视觉20倍。 |
| ZCode / DeepSeek Harness 搜索工具与中文检索增强 | [调研](research/agent-web-search-harness-research.md) | Read/PDF修复保留，实验研究流程已撤回；旧Pro实测不作Flash验收。 |
| WorkBuddy B2B 获客 skill 兼容性与能力评估 | [调研](research/b2b-lead-gen-workbuddy-compatibility-research.md) / [实验计划](plans/plan-b2b-harness-probe.md) | 完整Skill复跑已停止；当前收敛到Search/Fetch组件验收。 |
| Codex、DeepSeek Harness、Hermes 桌面执行架构对比 | [调研](research/desktop-agent-harness-architecture-research.md) | 桌面 Agent：共用云端 Runner、本机文件/程序与批处理执行边界。 |
| Desktop 本地与服务端工具执行分工调研 | [调研](research/desktop-agent-local-vs-server-tool-execution-research.md) | 桌面客户端 v3 / Runtime 插件宿主：本地/服务端双文件执行器、DeviceFileRef 与路由边界；结论已被桌面 v3 设计（§8）与 Phase 2/3 计划吸收，旧 Coordinator 路线作废。 |
| 基础设施差距分析 | [enterprise-agent-infrastructure-gap-analysis.md](research/enterprise-agent-infrastructure-gap-analysis.md) | 整体规划 |
| Prompt 版本管理调研 | [prompt-version-management-research.md](research/prompt-version-management-research.md) | Prompt 全生命周期管理 |
| AI Agent 可观测性调研 | [observability-design-research.md](research/observability-design-research.md) | 可观测性与质量保障 |
| 知识库 RAG 技术调研 | [enterprise-knowledge-base-rag-research.md](research/enterprise-knowledge-base-rag-research.md) | 知识库能力增强 |
| 知识库行业产品调研与低改动快速增强建议 | [enterprise-knowledge-base-quick-wins.md](research/enterprise-knowledge-base-quick-wins.md) | 知识库能力增强（补充 Phase 1-2 之外的快速增强点） |
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
| BOSS CLI 接入 Web Agent MVP（覆盖上行旧方案） | [设计](design/recruiting/recruiting-cli-agent-integration-design.md) | ✅ 已交付（BOSS CLI 0.2.14 客户发货，VL 识别/VIP 筛选/详情页打分相继上线；MVP 过程计划已删，交付记录见设计文档头部） |
| 网页操作实时视图（Boss CDP 首期，原 login assist / page stream） | [设计](design/recruiting/login-assist-page-stream-design.md) / [开发计划](plans/recruiting/web-operation-live-view-dev-plan.md) / [实验纪要](research/cdp-page-stream-experiment-notes.md) | 📋 设计完成待开发（2026-09-01，v3.2/v2.2 已吸收开发前全仓代码核查与独立复审）：通用 `page-stream/1.0` 首期接入 `aid-runtime + boss-cli… |
| 招聘 CLI 接入 Agent：MVP 后总体架构 | [总体架构基线](design/recruiting/recruiting-cli-agent-post-mvp-architecture.md) | 🧭 总体方向已保留、待 MVP 后分阶段深化：MVP 的用户当前 PC 就是第一台 Windows 执行节点；… |
| BOSS CLI 沟通发消息能力（send-to / send-current） | [原生 CDP 设计 §10.6](design/recruiting/boss-resume-assistant-native-cdp-design.md) | 🔧 部分完成（2026-08-13）：把已真机验证的「搜索找人 + 输入 + 发送」链路封装为两个 CLI 子命令与同名 MCP tool——`send-to <姓名> --message <消息>… |
| BOSS CLI 职位切换能力（list-jobs / select-job） | [原生 CDP 设计 §10.7](design/recruiting/boss-resume-assistant-native-cdp-design.md) | ✅ 已完成开发（2026-08-13 封装 + 真机验收 + 增强，待提交）：封装为 `list-jobs`（只读 `boss_list_jobs`）… |
| 招聘「职位管理/简历库」UI 重构（列表/详情路由化 + job_id 强关联 + 删 demo 预置） | — | ✅ 已完成开发（2026-09-01 全部子项完成，含④期/A组/回写；开发过程：后端招聘模块 169 集成测试绿（真实 PG）+ 前端 typecheck/build/23 单测绿；未提交）：… |
| 招聘客户端部署封装（npm install 一键装机） | [设计](design/recruiting/recruiting-client-deployment-design.md) / [部署手册 clients/README.md](../clients/README.md) / [同事装机手册](../clients/release/安装手册.md) | 🔧 部分完成（2026-08-24 分发包 v0.2.3，send-to 沟通搜索链路全面通用化重写）：**send-to 通用化**（另一台机器分辨率不同点错搜索按钮暴露：… |
| 招聘面试邀约企微通知（两点式） | [设计](design/recruiting/recruiting-interview-notify-design.md) | 🔧 部分完成（Phase 1 核心闭环已开发，2026-08-19 三智能体全过——测试修 2 真 bug + CR 修 1 个 P1（executor 强转 Pydantic 模型实例致生产路径通知… |
| 浏览器 RPA（纯键鼠路线）深度调查报告 | [browser-rpa-vs-anti-bot-deep-research.md](research/browser-rpa-vs-anti-bot-deep-research.md) | BOSS 简历筛选助手、微信搜一搜 RPA、通用 RPA 底座。 |
| 上传文件存储合规审计 | [审计报告](plans/upload-storage-compliance-audit.md) | ✅ 已修复并发生产、已验证（2026-08-14 审计 + 当日修复 + 当日发生产验证）：主体合规（Phase 1~8 改造有效，持久化入口均走 storage.py 工具函数）。 |
| deepseek-v4-flash 平替模型调研 | [deepseek-v4-flash-replacement-research.md](research/deepseek-v4-flash-replacement-research.md) | LLM 模型选型、计费降本（2026-08-16 调研完成：选定 qwen3.7-flash 0.2/0.8 + enable_thinking:false + 显式缓存，… |
| 小米 MiMo-v2.5 平替可行性调研（扩展） | [mimo-v2.5-replacement-research.md](research/mimo-v2.5-replacement-research.md) | 评估小米 MiMo-v2.5 平替 deepseek-v4-flash（2026-08-16 调研+实测完成）。 |
| LLM 提供商文档入口速查 | [ext/llm-doc-entrances.md](../ext/llm-doc-entrances.md) | 记录百炼/DeepSeek/小米 MiMo/火山方舟官方文档入口 URL（2026-08-16 均验证 HTTP 200 可达），供 curl 兜底抓取资料用。 |
| 火山方舟模型平替调研摘要 | [ext/volc-ark-replacement-research.md](../ext/volc-ark-replacement-research.md) | 评估火山方舟文本模型平替 qwen3.7-flash（2026-08-16）。 |
| BOSS CLI 部署方案调研：员工电脑本地虚拟机 vs 云桌面 | [boss-cli-vm-vs-cloud-desktop-deployment.md](research/boss-cli-vm-vs-cloud-desktop-deployment.md) | 100 账号/100 台员工电脑场景（2026-09）。 |
| BOSS 矩阵账号—企业微信招聘协同客户方案 | [客户解决方案](solutions/recruiting/boss-wecom-recruiting-service-solution.md) / [实施与验证计划](plans/recruiting/boss-wecom-recruiting-service-rollout-plan.md) | 🔧 部分完成（2026-08-31）：形成约 100 个 BOSS 账号、约 10 台在线 VM 的客户方案，云端虚拟机报价 1,000 元/台/年，按 10 台初算约 10,000 元/年，… |
| 竣工图纸差异对比（转图片 + 多模态）测试报告 | [cad-multimodal-drawing-comparison-test-report.md](research/cad-multimodal-drawing-comparison-test-report.md) | 图纸差异对比功能（2026-09-04 实验）。 |
| CAD 处理与多模态图纸对比技术报告 | [cad-processing-and-multimodal-drawing-comparison-tech-report.md](research/cad-processing-and-multimodal-drawing-comparison-tech-report.md) | 图纸差异对比功能（2026-09-04）。 |
| 微信公众号历史文章清单获取原理调研（wechat-download-api 源码级分析） | [调研](research/wechat-mp/wechat-download-api-principle-research.md) | 公众号内容入知识库 WPS 托底清单源：机制=管理员扫码会话调公众平台后台 searchbiz/appmsgpublish；结论=方法可自研借鉴（非独门秘籍），Docker 降级为实验对照与应急选项（2026-09-16）。 |
| 邮箱服务器自动发现（Autoconfig）调研与配置体验优化方案 | [调研](research/email-server-autoconfig-research.md) | 邮箱设置体验优化：内置表+MX/ISPDB/Autoconfig 探测链，用户只填地址+授权码；含前端两步式表单与分期建议。 |
| 文生图/图生图能力缺失核查与生图模型选型调研 | [调研](research/image-generation-model-research.md) | 生图工具选型（2026-10-26）：确认项目无 AI 生图能力；建议一期智谱 CogView-4（文生图）+ 百炼 qwen-image-edit（图生图）复用现有账号，二期可选火山 Seedream。 |
| PPT 生成质量课题调研：从「能生成」到「能交付」 | [调研](research/ppt-generation-quality-research.md) | PPT 工具重构方向（2026-09-29）：三轮 trace 实证+业界调研（Codex/Anthropic skill/Presenton/Gamma 系）；结论=LLM 只选布局填槽、样式由主题库+规则排版保底、模板改一次性转译、QA 变门禁、python-pptx 回退废弃；分 P0-P3 落地路线。 |
| 企业微信本地库解密读取（local-db 通道）技术调研 | [调研](research/wecom-local-db-decrypt-research.md) | wecom-cli 本地库主数据通道（读历史/待回复检测/发送校验三能力主通道，UI/OCR 转兜底）：本机 5.0.11.6029 全链路验证通过（wxSQLite3 aes128cbc + 只读内存提取密钥 + 副本解密）；8 步流水线按版本矩阵分派，失效显式化不静默降级（2026-10-14）；集成设计见 design/wecom/wecom-cli-localdb-design.md。 |
| 无源码交付私有云——代码保护方案调研 | [调研](research/private-cloud-no-source-code-delivery.md) | 私有云交付不含源码（2026-10-22 调研完成，待实施）：核心逻辑 Cython 编译 + 其余 PyArmor + 技能/提示词加密；废弃 git pull 改离线镜像交付；Docker 无加密机制、载体更换不解决保密。 |
| AI 获客「联系人手机号交付」合规调研 | [调研](research/lead-gen-contact-phone-compliance-research.md) | AI获客线索交付边界（2026-10-28 调研完成）：个人手机号属 PIPL 个人信息，聚合交付租户=向第三方提供，5000 条即刑事入罪门槛（扬州 9 人爬企业注册信息获刑案同场景）；建议分层交付——企业信息/公开商务联系方式（座机、企业邮箱）可给，个人手机号不抓不存不交付，确需走持牌数据商采购通道；判例原文待外部律师复核。 |
