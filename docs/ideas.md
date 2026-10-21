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
| 20261012-1230 | file_assets 历史文件名补录 | ✅ agent2 已补录，生产待执行 | commit 8825d880 上线 file_assets 表持久化文件元数据（根治下载文件名退化）后，存量历史文件名需跑补录脚本恢复（磁盘 + 消息表原始名优先，幂等）。agent2 已执行（磁盘 1241 + 消息 769，共 1457 行）；生产 243 发布后需同样执行。 | — | —（执行步骤见 [生产发布备忘](../deploy/生产发布备忘.md)） |
| 20261012-2130 | docs 文档清理（3 个月前失效文档） | ✅ 已执行（2026-10-13） | 41 个删除项已 git rm；待定 wecom-integration.md 摘取有效章节至 wecom-crypto-troubleshooting.md 后删除；research_index 8 条目 + 全部悬空引用已清理；B 级重点项 6 处已修（含 2 个部署文档凭证脱敏），其余 32 个 B 级小修待后续顺手处理。 | [清理计划](plans/plan-docs-cleanup-20261012.md) | — |

## 系统功能

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261020-recruiting-error-perf | 招聘链路单点错误治理与 Win32 输入性能修复 | 🔧 部分完成 | 单份跳过+多轮CR完成；native点击与cv-wheel滚动真机验证通过，余读取脚本待迁 | [复盘](incidents/recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md) | [计划](plans/plan-recruiting-error-containment-and-input-performance.md) |
| 20261020-1223 | 腾讯文档知识库导入 | 🔧 部分完成 | 客户需将既有文档系统内容导入知识库；按受众面排序腾讯文档>飞书>金山>钉钉>语雀>石墨，Phase 1 聚焦腾讯文档。已完成（0bcb03d3）：适配层 src/services/tencent_docs/（drive 列表客户端 + doc v3 属性树转 Markdown 转换器 + 400010 自动降级导出 DOCX 兜底与每 openID 每日 9 次配额闸门），36 个单测全绿、独立验证无 P0/P1。已完成（2026-10-20）：导入任务框架（任务 #5，DB 4 表 + 凭据加密存取 + 批次预检/配额闸门/无变化跳过 + Redis 单副本调度器 + 知识库入库覆盖更新）与前端入口（任务 #7，独立页面 /t/:tenant_id/knowledge/tdoc-imports：凭据绑定/目录浏览/文件选择/批次进度轮询），72 个单测全绿；浏览器真机联调与 OAuth 多租户授权（#2，被第三方应用入口缺失阻塞，待官方反馈）待做。范围决策（2026-10-20，产品确认）：现有租户腾讯文档账号均为个人账号，仅对接个人版 C 端；企业版 SaaS（OneID + drive/v3）与企业微信文档体系待实际需求出现再对接（调研快照 ext/腾讯文档接口/07）。遗留确认项：search 接口参数名待真机验证、400010 触发条件待定位。测试凭据明文登记计划 §7.1。 | — | [计划](plans/plan-tencent-docs-knowledge-import.md) |
| 20261013-1055 | Agent 网络搜索增强 | 🔧 部分完成 | 三家provider已接入，默认Tavily；融合、批量和正文核验待开发。 | [设计](system/agent-web-search-enhancement-design.md) | [计划](plans/plan-agent-web-search-enhancement.md) |
| 20261008-runtime-plugin-host | Runtime 执行环境、插件宿主与可视化客户端 | 🔧 部分完成 | A1～A3完成；隔离验收包已交付，待人工及正式发行，第三方暂不开发；新增 v2 写路径泛化阶段（BOSS B3 依赖，待开发）。 | [唯一架构](system/runtime-plugin-host-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) / [接口](../contracts/runtime-host/v1/README.md) | [统一计划](plans/plan-runtime-plugin-host.md) |
| 20261010-1642 | 系统级防滥用与限额 | 🔧 部分完成（Phase 1 开发完成，未部署） | Phase 1 观察闭环已完成：abuse_policies/abuse_events 两表、引擎 src/services/anti_abuse/（绑定匹配 user > channel（按 users.source 渠道类型） > tenant 兜底 > 平台默认）、定时扫描（300s）+ 事件 1h 去重 + 站内信通知（abuse.rule_triggered）、渠道入口 users.status 停用拦截（fail-open）、防滥用中心页（用量监控/禁用记录/触发记录 + 策略编辑）、用户停用/启用接口。Runner 链路 status 检查缺口登记 Phase 2 统一拦截点解决。 | [设计](system/saas/anti-abuse-policy-design.md) | — |
| 20261010-1930 | 租户管理员通知（计费提醒 / 反滥用告警） | 🔧 部分完成（Phase 1 开发完成，未部署） | 复用统一通知基础设施：站内信恒开兜底 + 外发渠道（飞书/企微/钉钉/邮件）四选一；三事件 billing.credit_low（7 天用量推算，月度幂等）/ billing.tenant_expiry（30/7/1 天档）/ abuse.rule_triggered（Phase 2 挂反滥用 warn_threshold）；管理员逐人展开 + 租户级外发目标（同事件多管理员仅投一次），不新建表。启用需 configs/config.yaml notifications.tenant_events_enabled=true。 | [设计](system/tenant-admin-notification-design.md) | — |
| 20260908-2229 | 外部系统入口（SSO 打开第三方系统） | 🔧 部分完成（Phase 1 开发完成，待真机联调） | 连接中心「外部系统」入口 + SSO 通用契约（direct_url/ticket_redirect/token_param）打开第三方系统；Phase 1 完成待真机联调。 | [方案](system/external-system-entry-design.md) | — |

## 数字员工 / 子智能体

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261020-1003 | 数据分析数据表按智能体配置 | 🔧 部分完成 | 六项复审修复通过，真实PostgreSQL验证待完成。 | [设计](system/digital-employee/data-analysis-subagent-design.md) | [计划](plans/plan-data-analysis-agent-scope.md) |
| 20261016-1202 | 数字员工配置收敛：recap/工具技能带入锁定 | 🔧 部分完成（开发完成，待提交部署） | 背景：2026-10-16 agent2 事故——管理员删租户 pre-sales-api.md 改用通用模板，但通用模板 fallback 按技能白名单检索，pre-sales 白名单未含 pre-sales-api，recap external_push 静默放弃。改动：管理后台 AgentDefinitionManager recap 区/工具区按「当前勾选技能的 requires_recap/requires_tools」实时推导来源并锁定（BaseBadge「技能带入/技能依赖」标注，enabled 可临时停用），手动添加入口保留（lead_refresh 等无技能来源任务）；取消勾选技能时 recap 条目自动回删（toast 提示）、工具不回删；后端 create/update 保存时校验 recap 任务名 ∈ RECAP_TASK_ADAPTERS、when ∈ VALID_WHEN（非法 400，替代运行期静默跳过）。 | — | — |
| 20261014-1645 | 租户自助复制/定制数字员工 | 🔧 部分完成（开发完成，待提交部署） | 租户管理员在 my-agents 页面对已订阅数字员工重命名、扩展提示词、指定知识库栏目、覆盖简介与快捷按钮，并可复制出租户专属副本实例（继承语义：父定义 system_prompt/工具/技能实时生效，仅覆盖 4 类字段；支持从内置/自定义/本租户实例复制，实例来源克隆覆盖项+根父一层继承）。2026-10-21 补修：副本实例对话正常但 recap/external_push 不触发——ChannelRunnerAgent.subagent_config 解析 profile 未传 tenant_id，副本实例不进全局 registry 抛 PROFILE_NOT_FOUND 被 trigger_recap 静默吞掉（渠道侧 4 个构造点已补传 tenant_id，新增透传回归单测）。同日二次补修：副本实例 recap/external_push 虽已派发但文档解析落空被放弃——resolve_doc_text 按副本完整 ID 找租户文档/技能白名单均查不到（副本不进 subagent_definitions、无副本级文档与环境变量配置入口），tenant_api_doc 新增 resolve_base_agent_id 按 tenant_agent_instances.base_agent_id 解析父定义名，文档/技能白名单/环境变量/AGENT_TOKEN 统一按父定义读取（external_push._get_agent_token 同步；对话侧 executor._tool_context 同按 base_agent_id 查环境变量，dir_name 已切实例 ID 仅供 extra.md/知识栏目隔离），新增副本解析单测。新建 tenant_agent_instances 表（直用实例行复合唯一、副本实例行部分唯一索引），tenant_extra 定制提示词迁移进新表（双写过渡）；渠道配置支持绑定副本实例；副本实例 ID 纯连字符（防 recap session_id 末段提取破坏）。已过独立测试与 CodeReview 复核（38 单测通过、前端 build 通过）。2026-10-19 完善：复制时一并克隆模板文件（磁盘副本 + 新 file_id + 永久元数据）与知识库栏目（共享资产模块 `subagent_assets`）；副本实例显式补入「新会话允许列表」「用户管理授权列表」并在设置授权时展开校验（此前副本实例不可见/被当越权过滤）；「我的数字员工」卡片新增删除按钮，删除前校验渠道配置占用，被引用则直接拒绝（需先解除渠道绑定）。 | [设计](system/digital-employee/tenant-agent-instance-design.md) | — |
| 20261013-2102 | 自动填表智能体 | 🔧 部分完成（P0 核心链路 + 短信计费开发完成，待 wecom_kf 真机验证） | 内部员工在微信/企微/钉钉/飞书中用文字或语音，由智能体通过第三方系统 API 代填表单（首期 10605 客户信息+跟进记录）。手机号身份验证（渠道自带/users.phone/短信验证三档）+ 用户级委托登录 + 回显确认回执 + 表单 schema 接口通用化。已交付：form-filling-api 技能（load_api_config/get_user_phone/load_form_schema + delegate_login hold_token 托管扩展）、auto-form-filler 子智能体、通用接口文档模板、短信计费（每条 1 积分，AID_* env 计费身份 fail-closed）。待部署验证：wecom_kf 通道真机全流程（手机号验证、回显确认渲染）。配套：customer-followup 智能体已下线（git rm `subagents/customer-followup/`，业务页面/数据表/技能保留）。 | [设计](system/digital-employee/auto-form-filling-agent-design.md) | — |
| 20261013-1700 | BOSS操作验证与GLM视觉异常恢复 | 🔧 部分完成（新版验收包） | 视觉已复用Runtime配对、无需额外token；新版包通过，部分外发与agent2真机待验。 | [设计](design/recruiting/boss-overlay-heal-design.md) / [原需求](plans/desktop-automation/plan-boss-job-vip-entitlements.md#glm-obstacle-recovery) | [计划](plans/desktop-automation/plan-boss-operation-verification-visual-recovery.md) |
| 20261015-1726 | 招聘智能体、Runtime与BOSS待优化项 | 📋 待评估 | 保留原审查编号，登记当前视觉恢复计划之外的候选优化与用户反馈。 | — | [待优化清单](plans/desktop-automation/boss-recruiting-optimization-backlog.md) |
| 20260908-1432 | BOSS 直聘聊天自动化 | 🔧 部分完成 | B1/B2 完成入库（场景包默认关闭）；B3 起待开发，B0 测试账号授权阻塞中；旁支已交付（见 finished）。 | [BOSS 端侧接入设计](design/desktop-automation/boss-edge-session-design.md) / [底座设计](design/desktop-automation/desktop-cli-automation-design.md) | [BOSS 端侧接入计划](plans/desktop-automation/plan-boss-edge-session.md) |
| 20260905-1753 | 营销 App 智能外呼代理 | 🔧 部分完成 | 2026-09-05 完成调研、详细设计与实施规划。 | [设计](design/marketing-call-agent-design.md) | [验证与计划](plans/marketing-call-agent-plan.md) / [GLM 实验执行手册](plans/marketing-call-agent-experiment-runbook.md) |
| 20260817-1154 | 工程审计智能体（客户方案阶段） | ⏸️ 已搁置 | 面向工程管理咨询公司的「四库一平台三智能体」工程审计智能体商务方案（客户需求《工程审计智能体开发建设方案V1.0》）。 | 见 `docs/backups/engineering-audit-agent/`（git 忽略，仅本地） | — |
| 20260729-1434 | AI 批量视频内容生产系统 | 🔧 部分完成 | AI 批量视频内容生产系统：抽卡式短视频批量生成，方案多轮迭代（v0.4 回归本质）。 | [调研](research/ai-video-production-research.md) / [PRD](system/content-production/ai-video-production-prd.md) / [MVP技术设计](system/content-production/mvp-design.md) / [MVP开发计划](plans/plan-video-gen-mvp.md) / [产品定位重新审视](system/content-production/video-agent-enterprise-positioning-design.md) / [Phase 1 开发计划](plans/plan-video-agent-phase1.md) | — |
| 20260721-1145 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；… | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-marketing-agent-design.md) / [S1发布调度设计](system/digital-employee/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](system/digital-employee/social-media-marketing-agent-dev-plan.md) |

## 工具

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261021-1625 | pptx-vi-gen 技能：模板 VI 提取 + 美观 PPT 生成 | 🔧 部分完成（开发+单测完成，待提交部署） | 从第三方系统（workbuddy）移植的 PPT 生成技能，仅整合其"路线二"（VI 生成，其"路线一"模板填充与 ppt_process 工具重叠不引入）。从客户上传的 .pptx 模板提取 VI（主题色/字体/Logo），LLM 按 7 种版式 schema（cover/section/stats/bullets/twocol/image/end）产出 content.json，Pillow 逐页渲染 1920×1080 后合成整页图片版 PPT（文字不可编辑）。新增 src/skills/pptx-vi-gen-1.0.0/（SKILL.md + scripts/generate.py CLI + pptx_gen 库），移植改造：vi.py 字体查找补 Linux Noto CJK/文泉驿路径（原硬编码 Windows 字体）、pipeline.py 支持无模板默认 VI 兜底、路径解析不做跨租户 glob（fail-fast）。SKILL.md 划清与 ppt_process 工具边界（本技能管"模板 VI + 美观图片版"，可编辑 PPT 走 ppt_process）。独立验证通过（6 个单测：注册发现/stdin/端到端/非法 role/缺 title/缺必需字段），容器内端到端验证渲染质量正常。 | — | — |
| 20261014-2043 | wecom-cli 本地库数据通道（local-db） | 📋 待开发 | 解密企微本地 SQLite 库作为读历史/待回复检测/发送校验三能力的主数据源（5.0.11.6029 本机已全链路验证），UI/OCR 链路转兜底；版本矩阵分派 + doctor 首跑深检定基调；v1 仅文本消息。 | [设计](design/wecom/wecom-cli-localdb-design.md) | — |
| 20260714-1911 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 20260630-1733 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260630-1734 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。 | [设计](tools/pdf/pdf_tool_design.md) | — |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用）、钉钉、飞书渠道接入。

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260910-1259 | 留资线索动态刷新（lead_refresh：意向度 + 需求分条 + 人工归属） | 🔧 部分完成 | **Phase 1 开发+单测完成（2026-09-16），待部署真机验证；Phase 2 §9.5 人工期对话推送（external_push_human：节流推送 + 转人工字段 + 跟进汇总摘要同步 + 不要求留资）开发+单测完成（2026-09-16）**。触发：2026-09-10 产品需求——留资后客户继续交流（智能体轮次 + 转人工期）仅落 `channel_messages`，线索行不再更新，运营页看不到最新客户状态。 | [设计](subagent/pre-sales/lead-capture-refresh-design.md) | — |
| 20261012-1200 | wecom_kf 并发限频修复（sync_msg 45009 重试 + 拉取去重） | 🔧 部分完成（开发+单测完成，待部署） | 2026-10-11 演示事故修复：约 20 用户同时进入触发微信 sync_msg 限频 47 秒全失败，积压 31 条串行排空，首批用户端到端等 1-3 分钟。修复：45009 指数退避重试 + 同一 open_kfid 拉取任务去重合并。 | [事故复盘](incidents/wecom-kf-sync-msg-45009-incident.md) | — |
| 20261018-0930 | 微信侧大文件交付 OSS 化（>=20MB iOS 下载卡死） | 📋 待开发（讨论稿待决策） | ≤20MB 已走 file 消息直发（d8c18929）；>=20MB 仍卡死，方案为交付文件迁腾讯云 COS + 标准->归档分层 + 按次转积分，含 cp 复制改引用、知识库原始文件是否同迁等待决策点。 | [讨论稿](plans/plan-wecom-large-file-oss-delivery.md) | — |

## 前端

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261021-1126 | Web Agent 单条回复分享 | 📋 待开发 | 独立网页完整展示所选回复及图片附件，由分享人决定是否需要密码。 | [设计](system/web-agent-reply-sharing-design.md) | [计划](plans/plan-web-agent-reply-sharing.md) |
| 20260714-1912 | Agent 跨平台桌面客户端 | 🔧 部分完成（H3待人工验收） | 共用 Runner 与 Runtime，UI竞态修复后的首期验收包已重建验证。 | [桌面设计 v3](system/desktop-agent-client-design.md) / [Runner 架构](system/agent-application-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) | [开发计划](plans/plan-desktop-agent-client.md) |

---

## 故障复盘索引

已迁移至独立文件：[incidents/index.md](incidents/index.md)。

---

## 调研报告索引

已迁移至独立文件：[research_index.md](research_index.md)。
