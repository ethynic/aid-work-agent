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
| 20261012-1230 | file_assets 历史文件名补录 | ✅ agent2 已补录，生产待执行 | commit 8825d880 上线 file_assets 表持久化文件元数据（根治下载文件名退化）后，存量历史文件名需跑补录脚本恢复（磁盘 + 消息表原始名优先，幂等）。agent2 已执行（磁盘 1241 + 消息 769，共 1457 行）；生产 243 发布后需同样执行。 | — | —（执行步骤见 [生产发布备忘](../deploy/生产发布备忘.md)） |
| 20261012-2130 | docs 文档清理（3 个月前失效文档） | ✅ 已执行（2026-10-13） | 41 个删除项已 git rm；待定 wecom-integration.md 摘取有效章节至 wecom-crypto-troubleshooting.md 后删除；research_index 8 条目 + 全部悬空引用已清理；B 级重点项 6 处已修（含 2 个部署文档凭证脱敏），其余 32 个 B 级小修待后续顺手处理。 | [清理计划](plans/plan-docs-cleanup-20261012.md) | — |
| 20261021-2200 | 私有云生产环境部署指南 | ✅ 已完成（2026-10-21） | 新增 [deploy/生产环境部署指南.md](../deploy/生产环境部署指南.md)：面向客户私有云全新部署的通用 playbook，综合 243 独占迁移档案与济南诺度混部实例（摸底决策、Docker/codeup 三通道、sparse-checkout、PG 绑址、.env 必改项、宝塔 TLS、发布后事项、验收清单、两例踩坑速查）。 | [部署指南](../deploy/生产环境部署指南.md) | — |
| 20261028-1255 | 外发邮箱配置指南（域名 DNS 侧） | ✅ 已完成（2026-10-28） | 新增 [docs/ops/outbound-email-dns-setup-guide.md](ops/outbound-email-dns-setup-guide.md)：面向租户外发邮件域名配置的实操指南，沉淀 tulin.cn 体检实战——SPF/DKIM/DMARC/MX 四记录要点（163 免费企业邮 + 阿里云解析逐步操作）、MX 主机记录误填子域致主域收信中断、权威 DNS 与公共缓存验证口径、DMARC 报告解读与 p 策略升级路径、缺 DKIM 决策参考与验收清单。 | [指南](ops/outbound-email-dns-setup-guide.md) | — |

## 系统功能

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261110-2032 | 技能目录重复注入去重 | ✅ 已完成开发（已合并 master，模型选型待真机观察） | use_skill 工具 schema 去名单只留使用规则，名单唯一保留系统提示；独立测试回归+CR 通过。模型选型行为待真机观察。master 专属子智能体目录重复不在本轮。 | — | [计划](plans/plan-skill-catalog-injection-dedup.md) |
| 20261110-1524 | 管理后台系统健康监控页面 | 🔧 部分完成（四级状态徽标改版+网络 IO；重试占比口径 bug 已修待部署，待真机验收） | 平台后台新增 /portal/system-health 健康监控页（登录默认页）：四组指标+AI 研判，无曲线无历史，手机端一等公民 | [设计](system/system-health-monitor-design.md) | — |
| 20261110-2201 | 系统健康真实模型错误率 | 📋 待规划 | 模型网关侧 LLM 调用失败率统计（KeyPool/CircuitBreaker 补失败计数），发现「限流被重试兜住、对话未失败」隐患；替代已删除的同源重复行（2026-11-10） | — | — |
| 20261106-1010 | 定时任务结果免登录链接 | 📋 设计完成，待开发 | 定时任务终态通知链接免登录化（复用回复分享的公开路由/ticket/只读渲染）：只读结果页+登录入口、无密码链接即凭证、实时读跟随任务生命周期、web push/站内信/IM webhook 三处全换、产物文件免登录可下载。10 项决策已与用户逐条定稿（2026-11-06）。 | [设计](system/design-task-result-share-link.md) | [计划](plans/plan-task-result-share-link.md) |
| 20261111-1100 | Runner 容量与轻重负载分离 | 🔧 部分完成（Phase 7 已发布 agent2） | 11-12 agent2 压测通过轻重分离/槽内并发/重判/看门狗验收；to_md 大文件守门 P0 已整改并对话级回归通过；入队判型信号重构草案（travel-consultant 过判实证）定稿待实施，见设计 §10。 | [设计](system/runner-capacity-and-load-separation-design.md) | [开发计划](plans/plan-runner-capacity-load-separation.md) |
| 20261105-1902 | Runner 执行槽隔离与运行告警 | 🔧 部分完成（agent3 初验） | 混合负载与 Excel 重负载专项已验：隔离性与告警达成，新发现取消挂死/心跳锁超时/假异步 3 个 P0 待整改。 | [设计](system/agent-application-architecture-design.md#10-runner-执行槽隔离与运行告警2026-11-05) | [计划](plans/plan-runner-execution-slot-isolation-and-alerts.md) |
| 20261104-1639 | 知识库多轮检索新鲜度与结果去重 | 🔧 部分完成 | 工具说明对照无差异，待 Web 与正式微信 KF 复验。 | [设计](system/knowledge-base/knowledge-search-freshness-design.md) | [计划](plans/plan-knowledge-search-freshness.md) |
| 20261109-1440 | 知识库文档禁用/启用 | ✅ 已完成开发（待部署） | documents 加 disabled 列，检索全入口过滤（收口点+模式B/C+数据分析），页面状态列+禁用/启用按钮；11 单测绿+201 回归绿。 | — | — |
| 20261022-1606 | 本地工具失败与原会话恢复闭环 | 🔧 部分完成（Phase 0-4/7-12 完成） | 0.3.27 单调用归位接续与跨轮续读真机通过（10-26，累计 27 份）；遗留 Phase 5/6 真实发布切换与 A1～A6 业务验收待授权，持续隐藏/二次超屏/混合 DPI 未验。 | [设计增量](system/agent-application-architecture-design.md#本地工具阻断与原会话恢复2026-10-22增量待实现)、[最小修复方案](design/recruiting/boss-overlay-heal-design.md#w4-原始日志核验后的最小修复方案2026-10-23)、[今日统一交接](incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md) | [修复计划](plans/plan-runner-local-tool-recovery-lockup-fix.md)、[10-30事故取证](plans/plan-boss-widget-incident-evidence-collection.md) |
| 20261112-1235 | BOSS 筛选容错与本机操作恢复 | 📋 待开发 | 跳过无效筛选条件，旧执行停稳后在原会话继续。 | [筛选设计](design/recruiting/resume-job-matching-design.md#boss-筛选参数容错)、[恢复设计](system/agent-application-architecture-design.md#boss-本机操作恢复) | [开发计划](plans/plan-boss-filter-input-and-local-recovery.md) |
| 20261020-recruiting-error-perf | 招聘链路单点错误治理与 Win32 输入性能修复 | 🔧 部分完成 | 单份跳过+多轮CR+读取链路PS全迁Node完成（双通道对照验证），Phase 6 真机观察待做 | [复盘](incidents/recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md) | [计划](plans/plan-recruiting-error-containment-and-input-performance.md) |
| 20261020-1223 | 腾讯文档知识库导入 | 🔧 部分完成 | 客户需将既有文档系统内容导入知识库；按受众面排序腾讯文档>飞书>金山>钉钉>语雀>石墨，Phase 1 聚焦腾讯文档。已完成（0bcb03d3）：适配层 src/services/tencent_docs/（drive 列表客户端 + doc v3 属性树转 Markdown 转换器 + 400010 自动降级导出 DOCX 兜底与每 openID 每日 9 次配额闸门），36 个单测全绿、独立验证无 P0/P1。已完成（2026-10-20）：导入任务框架（任务 #5，DB 4 表 + 凭据加密存取 + 批次预检/配额闸门/无变化跳过 + Redis 单副本调度器 + 知识库入库覆盖更新）与前端入口（任务 #7，独立页面 /t/:tenant_id/knowledge/tdoc-imports：凭据绑定/目录浏览/文件选择/批次进度轮询），72 个单测全绿；浏览器真机联调与 OAuth 多租户授权（#2，被第三方应用入口缺失阻塞，待官方反馈）待做。范围决策（2026-10-20，产品确认）：现有租户腾讯文档账号均为个人账号，仅对接个人版 C 端；企业版 SaaS（OneID + drive/v3）与企业微信文档体系待实际需求出现再对接（调研快照 ext/腾讯文档接口/07）。遗留确认项：search 接口参数名待真机验证、400010 触发条件待定位。测试凭据明文登记计划 §7.1。 | — | [计划](plans/plan-tencent-docs-knowledge-import.md) |
| 20261013-1055 | Agent 网络搜索增强 | 🔧 部分完成 | Read/PDF工具已提交；实验研究流程已撤回，Skills串联另行负责。 | [设计](system/agent-web-search-enhancement-design.md) | [计划](plans/plan-agent-web-search-enhancement.md) / [接续指南](guides/agent-web-search-continuation-20261023.md) |
| 20261008-runtime-plugin-host | Runtime 执行环境、插件宿主与可视化客户端 | 🔧 部分完成 | Runtime0.0.37实包独测通过，已替换Downloads，未安装。 | [唯一架构](system/runtime-plugin-host-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) / [接口](../contracts/runtime-host/v1/README.md) | [统一计划](plans/plan-runtime-plugin-host.md) |
| 20261110-runtime-recovery | Runtime 异常退出后的启动恢复 | 🔧 部分完成（待真机验收） | 恢复依据只用进程退出证据，用户停止/退出取消自动启动，读不出的旧记录归档放行。 | [设计](system/runtime-abnormal-exit-recovery-design.md) / [复盘](incidents/guoteng-runtime-disconnect-20261110.md) | [计划](plans/plan-runtime-abnormal-exit-recovery.md) |
| 20261111-1910 | Runtime 插件解耦与并行接单 | 📋 方案待评审 | 服务端排队，插件标记为可并行的命令可直接领取执行。 | [设计](system/runtime-plugin-decoupling-dispatch-design.md) | [计划](plans/plan-runtime-plugin-decoupling-dispatch.md) |
| 20261031-1501 | Runtime内置诊断留存与一键导出 | 🔧 部分完成 | 查看导出随Runtime0.0.34出包，专用跨层取证待开发。 | [设计第14节](system/runtime-plugin-host-architecture-design.md#14-客户端内置诊断留存与一键导出2026-10-31待开发) | [计划第14节](plans/plan-runtime-plugin-host.md#14-内置诊断留存与一键导出开发计划2026-10-31) |
| 20261028-1430 | 连接器运行时（统一工具入口与三种转接器） | 📋 设计完成，待开发 | 四决策全部与用户定稿（2026-10-28）：统一工具入口+三种转接器（飞书 SDK 直连先行/文档驱动基于 http_api 增强/MCP 最后）；x-aid- 扩展命名；外部 MCP 反向能力默认关（单向供给定位）。与连接中心（管理平面）配套，飞书为第一连接器。 | [设计](system/connector-runtime-design.md) / [连接中心设计](system/connection-center-design.md) / [调研](research/connector-architecture-feishu-research.md) | — |

## 数字员工 / 子智能体

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261112-1500 | 子智能体思考模式排查与批量关闭（enable_thinking） | 🔧 部分完成（配置贯通+3 个内置关闭已提交，候选实测后再定） | 关思考实测省 35~40% 积分（标书 trace 根因）；配置贯通含自定义数字员工三态开关。已关：ai-bid-assistant/ai-contract-assistant/auto-form-filler/contract-archive-review；6 个候选待 trace 实测，统计时须含自定义数字员工实例三态占比。 | [排查清单](audit/enable-thinking-review-20261112.md) | — |
| 20261112-1620 | AI投标助手扩容（bid-opportunity-evaluator 技能移植 + 子智能体改造） | 🔧 部分完成（开发+单测完成，待提交部署真机验收） | 移植 workbuddy bid-opportunity-evaluator 技能 v1.0.0（四步法投标可行性评估，规则化 0 模型调用产出 Word 评估报告，负载等级判 light）；ai-bid-assistant 显示名改「AI投标助手」，挂载该技能并定两阶段分流（先评估决策后编标）；7 单测绿 + 容器端到端冒烟通过。 | — | — |
| 20261110-2030 | 采购比价助手（quote-price-comparison 技能移植 + 薄壳子智能体） | 🔧 部分完成（上游 v1.2.1 已合并，待提交部署） | 已合并 v1.2.0（金额守恒单位换算 + run_pipeline 流水线）与 v1.2.1（摘要改 Word summary.docx，python-docx 直出正式交付物）；脚本与上游逐字节一致零补丁，13 单测容器内全绿 + CLI 冒烟通过，待部署真机验收。 | — | — |
| 20261111-0945 | 项目进度管家（project-progress-tracker 技能移植 + 薄壳子智能体） | 🔧 部分完成（已合并 master 1608eda6，待部署真机验收） | 移植 workbuddy 同名技能 v1.2.0（多源归集→三态研判→离线单文件看板→增量闭环）+ 薄壳子智能体；9 单测绿，待真机验收。 | — | — |
| 20261110-1440 | AI标书助手（bid-doc-generator 技能移植 + 薄壳子智能体） | 🔧 部分完成（v1.0.2 加轮次纪律，待提交部署重测） | 移植 bid-doc-generator 技能 + 薄壳子智能体；11-10 兰香园测试包实测对齐上游；11-11 agent2 真机首验 ITERATION_LIMIT，SKILL.md 升 v1.0.2 增「轮次预算纪律」，10 单测绿。 | [测试基准与实测记录](tests/fixtures/bid_doc_lxy/README.md) | — |
| 20261110-1100 | 供应商资质审查（技能移植 + 薄壳子智能体） | 🔧 部分完成（开发+单测完成，待提交部署） | 移植 workbuddy supplier-qualification-review 技能 v1.1.0（四阶段：视觉抽取→真实性审查→外部核查→两份 Word 交付）并新建同名薄壳子智能体；USCC 校验脚本修上游 REG_DEPT「9=金融」错标为「市场监管（工商）」。8 单测绿，待部署真机对话验收。 | — | — |
| 20261110-1003 | BOSS 简历读取链路提速（一期：等待参数与校验精简） | 🔧 部分完成（0.3.30 试用包已交付，待真机验收） | 单份简历端到端 46.6s→目标 37-39s：等待参数区间随机化（jitter）、轮询间隔收紧、首段后快照校验精简；快照算法升级与评分并行化留二期。 | [设计](system/boss-resume-read-speedup-design.md) | [计划](plans/plan-boss-resume-read-speedup.md) |
| 20261109-1603 | 租户数字员工 LLM 提供者定制（移植管理后台配置+租户级开关） | 🔧 开发中（开发+单测完成，待提交部署） | 定制弹框支持指定 LLM 提供者与各 Provider Model 覆盖（与父定义同构 JSONB）；平台管理后台租户编辑弹框新增「允许修改LLM提供者」开关，默认关，关闭立即失效（fail-closed，行数据保留）。帮助文字已按操作指引规范内嵌弹框说明。 | — | — |
| 20261107-0930 | AI合同助手（contract-generator 技能移植） | 🔧 部分完成（开发+单测完成，待提交部署） | 移植 workbuddy contract-generator 技能（四步合同生成工作流）并新建 ai-contract-assistant 子智能体；模板四渠道、取价走 knowledge_base_search。6 单测绿 + 容器加载验证通过，待真机对话验收。 | [设计](subagent/ai-contract-assistant/ai-contract-assistant-design.md) | — |
| 20261027-2100 | 引擎 Loop 内同步压缩（长任务上下文膨胀根治） | 📋 待设计 | 背景：trace tr_runner_fa9f94ae 单次 run 50 迭代，prompt 25.7k→181k 全程无压缩（见 20261027-1800 条目 D 项核查结论）。现有同步压缩仅在 ContextAssembler.prepare（用户消息入口）触发，引擎 Loop 内无钩子；`context_token_count` 缓存与阈值配置均正常。方向：引擎 Loop 迭代间检查最后一次 model fact 的 prompt_tokens，超阈值（复用 min(0.7×模型上限,60000)）时同步执行压缩（DB 层 compress_now + memory reload + state.messages 重建），需保证 tool_call/tool_result 配对完整（reorder_history 已有此逻辑）与 checkpoint 协议一致性。属共享引擎核心高风险改动，须走独立设计 + 三智能体流程。 | — | — |
| 20261112-1230 | b2b-lead-gen 上游拆分同步（挖掘/发送双技能） | 🔧 部分完成（开发+单测+冒烟完成，待提交部署） | 上游拆分落地为 b2b-lead-research（挖掘：综述/多渠道坐标/钩子库话术）+ b2b-email-sender（发送与核验）双技能，共享 DB 八表，删旧 ai-lead-gen 技能；leads 加 summary/profile_md、emails 加 hook_id、新增 contact_channels/messages 两表（db_update 已记）；前端线索页综述弹框+联系人页渠道列；136 单测绿+注册冒烟过。部署注意：管理后台须把 ai-lead-gen 的 skills.allowed 改为两个新技能名。 | [设计](subagent/ai-lead-gen/ai-lead-gen-agent-design.md) | — |
| 20261108-1100 | AI获客上游 b2b-lead-gen v3.3.11 同步合并 | 🔧 部分完成（开发+单测完成，待提交部署） | 合并上游 11 版升级至技能 v1.1.0：发送引擎 SMTP 会话自愈/限流整批停发/邮箱反混淆清洗/去预热分档（daily_limit 不填=不限量，每域 24h≤3 封红线保留）；新增 email_deobfuscate.py（ROT13/Cloudflare 反混淆）与 facts_builder.py（≤300 字事实表+25 家切块，含我方报告格式兼容补丁）；sender_profile 域名体检解耦为独立方法（record-domain-check 存档，不阻塞发送不参与复用）；domain_check --json 恒 exit 0 补丁重打；lead_db 新增 set-status-batch；SKILL.md 移植批量护栏/效率纪律/200 家专项/反混淆硬约束（委托措辞适配 web-search-pro 管线）；98 单测绿。剩：提交部署 + 真机发送冒烟。 | [设计](subagent/ai-lead-gen/ai-lead-gen-agent-design.md) | — |
| 20261110-1200 | AI获客联系人手机号放开（扩展提示词开关 + 掩码显码审计） | 🔧 部分完成（开发+单测完成，待提交部署） | 放开「不抓不存不交付」：租户扩展提示词明确要求时可搜索存储手机号（提示词口径改 SUBAGENT.md/SKILL.md）；联系人页服务端掩码（138****5678，海外号也掩码）+ 点击显码落 user_behavior_logs 审计（phone_view），无导出入口。12 单测绿。 | — | — |
| 20261108-1110 | AI获客邮件打开追踪（D 项：/api/track 端点） | 🔧 部分完成（开发+单测完成，待提交部署+真机冒烟） | 服务端 /api/track/p.gif 像素打点 + /api/track/opens 游标回拉（限速/匿名放行/bs_ai_lead_gen_opens 表）；技能侧 tracking_key（租户级 store）/fetch_opens（回写 emails.opens_at/open_count）/send_emails 像素注入完成；前端邮件记录页新增打开列 + 服务端定时合并 lead_opens_sync（直读 opens 表聚合回写，10 分钟，替代手动 HTTP 回拉）；6 新单测，84 回归绿。剩：提交部署 + 真机冒烟（发信→打开→回拉）；campaign_metrics/validate_campaign/hook_library 三件套另行评估。 | [方案](docs/plans/plan-ai-lead-gen-open-tracking.md) | — |
| 20261020-1003 | 数据分析数据表按智能体配置 | 🔧 部分完成 | 六项复审修复通过，真实PostgreSQL验证待完成。 | [设计](subagent/data-analysis/data-analysis-subagent-design.md) | [计划](plans/plan-data-analysis-agent-scope.md) |
| 20261013-2102 | 自动填表智能体 | 🔧 部分完成（P0 核心链路 + 短信计费开发完成，待 wecom_kf 真机验证） | 内部员工在微信/企微/钉钉/飞书中用文字或语音，由智能体通过第三方系统 API 代填表单（首期 10605 客户信息+跟进记录）。手机号身份验证（渠道自带/users.phone/短信验证三档）+ 用户级委托登录 + 回显确认回执 + 表单 schema 接口通用化。已交付：form-filling-api 技能（load_api_config/get_user_phone/load_form_schema + delegate_login hold_token 托管扩展）、auto-form-filler 子智能体、通用接口文档模板、短信计费（每条 1 积分，AID_* env 计费身份 fail-closed）。待部署验证：wecom_kf 通道真机全流程（手机号验证、回显确认渲染）。配套：customer-followup 智能体已下线（git rm `subagents/customer-followup/`，业务页面/数据表/技能保留）。 | [设计](subagent/auto-form-filler/auto-form-filling-agent-design.md) | — |
| 20261013-1700 | BOSS操作验证与GLM视觉异常恢复 | 🔧 部分完成（原任务续做独立验证通过，待真机） | 页面CLI排障后按原目标续做已独立验证，待新版Runtime真机验收。 | [设计](design/recruiting/boss-overlay-heal-design.md) / [原需求](plans/desktop-automation/plan-boss-job-vip-entitlements.md#glm-obstacle-recovery) | [计划](plans/desktop-automation/plan-boss-operation-verification-visual-recovery.md) |
| 20261015-1726 | 招聘智能体、Runtime与BOSS待优化项 | 📋 待评估 | 保留原审查编号，登记当前视觉恢复计划之外的候选优化与用户反馈。 | — | [待优化清单](plans/desktop-automation/boss-recruiting-optimization-backlog.md) |
| 20260908-1432 | BOSS 直聘聊天自动化 | 🔧 部分完成 | B1/B2 完成入库（场景包默认关闭）；B3 起待开发，B0 测试账号授权阻塞中；旁支已交付（见 finished）。 | [BOSS 端侧接入设计](design/desktop-automation/boss-edge-session-design.md) / [底座设计](design/desktop-automation/desktop-cli-automation-design.md) | [BOSS 端侧接入计划](plans/desktop-automation/plan-boss-edge-session.md) |
| 20260905-1753 | 营销 App 智能外呼代理 | 🔧 部分完成 | 2026-09-05 完成调研、详细设计与实施规划。 | [设计](design/marketing-call-agent-design.md) | [验证与计划](plans/marketing-call-agent-plan.md) / [GLM 实验执行手册](plans/marketing-call-agent-experiment-runbook.md) |
| 20260817-1154 | 工程审计智能体（客户方案阶段） | ⏸️ 已搁置 | 面向工程管理咨询公司的「四库一平台三智能体」工程审计智能体商务方案（客户需求《工程审计智能体开发建设方案V1.0》）。 | 见 `docs/backups/engineering-audit-agent/`（git 忽略，仅本地） | — |
| 20260729-1434 | AI 批量视频内容生产系统 | 🔧 部分完成 | AI 批量视频内容生产系统：抽卡式短视频批量生成，方案多轮迭代（v0.4 回归本质）。 | [调研](research/ai-video-production-research.md) / [PRD](system/content-production/ai-video-production-prd.md) / [MVP技术设计](system/content-production/mvp-design.md) / [MVP开发计划](plans/plan-video-gen-mvp.md) / [产品定位重新审视](system/content-production/video-agent-enterprise-positioning-design.md) / [Phase 1 开发计划](plans/plan-video-agent-phase1.md) | — |
| 20260721-1145 | 社媒营销智能体 | 🔧 部分完成 | 企业社媒营销全链路单一子智能体，三大模块共享账号/凭证/调度/审核/数据归一化/文件存储核心与统一社媒平台连接器：①内容管理（公众号/视频号自有阵地发布）；… | [调研](research/social-media-operations-agent-platform-research.md) / [设计](subagent/social-media-marketing/social-media-marketing-agent-design.md) / [S1发布调度设计](system/publish-dispatcher-design.md) / [后台运行时设计](infrastructure/background-runner-design.md) | [开发计划](subagent/social-media-marketing/social-media-marketing-agent-dev-plan.md) |

## 工具

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261111-1130 | lead-search-proxy 双出口扩展（46 新增出口 + web_search.py 多代理轮换） | 🔧 部分完成（agent2 双出口已部署验证，243 待部署） | sci-dialog-46（博物馆宽带出口）部署同款 tinyproxy 容器 + 双隧道（254:18081/243:18081），151→243 隧道已补建，安全组已放行双落点验证通过（123.158.82.107）；web_search.py 多代理逗号分隔 round-robin 轮换 + 失败出口 10 分钟熔断回直连（daa80168 已合并 master）；agent2 容器已切双代理并 e2e 验证（轮换交替生效、零 errors）；243 代码与 env 部署暂缓（用户裁决）。 | — | — |
| 20261110-1130 | 跨轮 workspace 旧路径改写（防 image_render 等工具首调失败） | ✅ 已完成开发（待部署） | Runner 上一轮工作区清理后旧路径仍残留会话历史致下一轮首调失败：context_assembler 加载历史后改写（反查索引 ws_file_idx 命中转 file_id、未命中失效标记）；图片附件装配时注册 ImageRegistry（source=user_upload）；image_render 报错加自纠提示。25 单测通过。 | — | — |
| 20261107-1120 | web-search-pro 技能合并上游 v3 效率与风控更新 | 🔧 部分完成（开发+验证完成，待提交部署） | 合并 workbuddy 2026-11-06 新版（efficiency 节流/冷却/LLM 缓存等，version 1.1.0）；我方百度代理+cookie 预热补丁重移植，11 单测+CLI 冒烟通过。 | — | — |
| 20261106-1310 | 产品效果图助手（interior-render）：image_gen 服务 + 出图/读图工具 + 技能移植 + wecom_kf 图片链路 | ✅ 已完成开发（待部署） | 基于第三方 qwen-interior-render 技能包移植：image_gen 服务层（DashScope qwen-image-3.0-pro，wire 形态按官方文档适配）、image_render/image_read 通用工具（按张计费）、interior-render 技能与子智能体、wecom_kf 入站图片放行。首轮 trace 506s 调优：n=1、判读收紧、enable_thinking=false 实测提速一倍、wecom_kf 只发图、AI 角标加租户开关。11-08 合并上游更新：Phase 4 改用户驱动自检默认跳过、sample_color.py Pillow>=10 兼容（容器 Pillow 12 必挂）+ 极小图防 0 尺寸。11-09 SUBAGENT.md 瘦身为引用 SKILL.md，工作纪律与交付口径单一来源化（便于其他数字员工复用技能）。11-10 合并上游 1.4.0：VL 只定位取色坐标不估色、新增 Phase 1.5 风格适配、软装饱满化模板、毛坯房实景照片分支（version 1.2.0，单测 13 通过）。 | — | [计划](plans/plan-image-gen-interior-render.md) |
| 20261105-1550 | Runner Worker 看门狗与队列堆积飞书告警 | 📋 待决策（设计稿完成，用户考虑实现方式） | TPG80509 事故后的最后防线：事件循环阻塞看门狗线程 + 队列水位巡检 + 独立飞书告警通道，三个方案选项与落地组合建议待裁决。 | [设计](system/runner-worker-watchdog-design.md) | — |
| 20261105-1540 | Excel 工具假异步与 used range 膨胀防御修复（TPG80509） | ✅ 已完成开发（已部署生产，11-11 核对） | 生产 243 全智能体无响应事故修复：excel_process 8 个 handler 全量 to_thread 包裹；双 ExcelReader 归一到 file/excel_reader.py（excel/excel_reader.py read_excel_document 薄委托）；used range 膨胀走 read_only 流式探测真实边界 + 截断提示。270 个相关测试通过；11-11 核对 243 四容器（api/api1/worker/worker1）均含修复代码。 | — | — |
| 20261104-2038 | 企微会话每日总结工作台与专属定时配置 | 🔧 部分完成 | 手动日报已显式路由到 main，待 agent2 发布验收。 | [设计](design/wecom/wecom-conversation-operations-design.md) | [计划](plans/plan-wecom-daily-summary-workbench.md) |
| 20261108-1200 | 企业微信代理任务按新消息激活智能体 | 🔧 部分完成（P8 联测中） | 单设备统一监控会话，新消息才按会话启动智能体回答。 | [设计](design/wecom/wecom-conversation-operations-design.md#9-企业微信代理任务新消息触发回答2026-11-08设计待开发) | [计划](plans/plan-wecom-proxy-event-driven.md) |
| 20261110-1313 | 企业微信首次启动发送与定时消息发送 | 🔧 部分完成（P0 开发中） | 先验收 CLI 标题点击和首次启动发送，再开发定时发送。 | [首次启动](design/wecom/wecom-conversation-operations-design.md#921-监控配置首次启动发送p0) / [定时发送](design/wecom/wecom-scheduled-send-design.md) | [计划](plans/plan-wecom-scheduled-send.md) |
| 20261103-1508 | 企微附件原件与持续任务后续优化 | 📋 待开发（用户暂缓） | 附件原件、预览及Runner恢复交互另行实施，不阻塞日志日报。 | [设计边界](design/wecom/wecom-conversation-operations-design.md#8-后置范围与验收原则) | [后续计划](plans/plan-wecom-conversation-followups.md) |
| 20261105-1100 | 企微群聊长文本粘贴与监控等待修复 | 🔧 部分完成 | agent2 的 GLM 标定与标题复制已真机验证，完整发送待验收。 | [设计](design/wecom/wecom-cli-design.md) | [计划](plans/plan-wecom-cli-group-chat-experiment.md#w23-文本发送标题复制与-glm-标定2026-11-06) |
| 20261102-1430 | 企微旁路聊天日志、前端与按日日报 | 🔧 部分完成（工作台由新计划接续） | 旁路日志与日期读取已有初版实现；日报页面及计划按新方案重做。 | [设计](design/wecom/wecom-conversation-operations-design.md) | [历史计划](plans/plan-wecom-conversation-operations.md) / [新计划](plans/plan-wecom-daily-summary-workbench.md) |
| 20261018-1736 | wecom-cli 登录引导增强（自动拉起 + 二维码直返） | 💡 灵感 | 三态验收发现：need_login 态 probe 已返回登录二维码 base64，缺口是 WECOM_NOT_FOUND（进程未启动）时不自动拉起企微——增加自动启动后把二维码转交 agent 引导用户扫码。 | [验收记录](plans/wecom-cli-localdb-three-state-acceptance.md) | — |
| 20261014-2043 | wecom-cli 本地库数据通道（local-db） | 🔧 部分完成 | 三件同批出包与双插件隔离导入通过，成品默认进下载目录。 | [设计](design/wecom/wecom-cli-localdb-design.md) | [开发计划](plans/plan-wecom-cli-localdb.md) |
| 20261026-1110 | 本地生图推理容器（Qwen-Image-2.1 · stable-diffusion.cpp GPU） | 🔧 部分完成 | deploy/image-gen/ 独立 compose（手动启停，不随 agent）：sd-cli CUDA 镜像 + FastAPI t2i/edits 串行队列；agent 侧工具接入待做。 | [部署文档](../deploy/image-gen/README.md) | — |
| 20260714-1911 | 浏览器混合执行、可视化与人工接管 | 🔧 部分完成 | Phase 0～1 已完成；Phase 2 已实现、待真实 Redis/PostgreSQL 门禁。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 20260630-1733 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260630-1734 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。 | [设计](tools/pdf/pdf_tool_design.md) | — |

## 渠道集成

> 本区覆盖企业微信（客服 wecom_kf / 应用）、钉钉、飞书渠道接入。

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20261113-1126 | 长任务企业微信应用通知 | 📋 待开发 | web 渠道长任务（≥5 分钟可配）完成/失败时经企微独立通知应用一对一推送 textcard，点击回原会话；OAuth 为主+手机号确认兜底绑定身份；替换 web push 投递渠道（保留停用）。6 项决策已逐条确认。 | [设计](system/wecom-app-task-notification-design.md) | [计划](plans/plan-wecom-app-task-notification.md) |
| 20261106-1145 | wecom_kf 人工超时智能体接管 | 📋 待开发（设计待评审） | 转人工后人工超时限无发言且客户来新消息时，自动结束人工会话切回智能体接待（企业微信状态图 3 只能转 4，无热切换）；含 reason 白名单豁免、接管标记与审计字段、配置字段及前端表单。 | [设计](channel/wecom_kf/wecom_kf_design.md)（§十五） | — |
| 20261109-2203 | 三渠道入站文件消息支持（飞书/钉钉/企微应用） | 💡 灵感 | 非图片文件输出侧三渠道已具备（textcard/文件消息直发），输入侧缺文件消息分支；机制与图片分支同构（媒体模块已有下载接口），有实际需求时按图片模式补齐。 | — | — |
| 20261109-2159 | 多渠道图片收发支持（飞书/钉钉/企微应用，对齐 wecom_kf） | 🔧 部分完成（开发+单测完成，待真机验收） | 复刻 wecom_kf 图片链路到三渠道：入站图片下载落盘 -> attachment 入 Runner；出站公共 helper（ImageRef/files 解析+租户校验）+ 企微补 images 直发（>2MB 降级 textcard）+ 飞书/钉钉对齐口径。真机缺口：飞书 image_key、钉钉 downloadCode/photoURL、企微 media_id 收发待各渠道租户配置验证。 | — | — |
| 20261029-1200 | 飞书/钉钉/企业微信渠道语音输入（阿里云 ASR） | 🔧 部分完成（开发+单测完成，待部署真机验证） | 复刻 wecom_kf 语音链路到三渠道：下载音频 -> 规范化 -> ASR 转文字入对话，计费对齐 KF；飞书/钉钉下载接口待实测。 | — | — |
| 20261029-1140 | 留资口径调整（qr 不算留资 + 联系方式类型扩展） | 🔧 开发中 | qr 发码拆独立工具 send_advisor_qr；contact_method 扩展为手机号/固话/邮箱，other_contact JSONB 逐条加密存储。 | [设计](subagent/pre-sales/lead-capture-design.md)（§十六修订） | — |
| 20261018-0930 | 微信侧大文件交付 OSS 化（>=20MB iOS 下载卡死） | 📋 待开发（讨论稿待决策） | ≤20MB 已走 file 消息直发（d8c18929）；>=20MB 仍卡死，方案为交付文件迁腾讯云 COS + 标准->归档分层 + 按次转积分，含 cp 复制改引用、知识库原始文件是否同迁等待决策点。 | [讨论稿](plans/plan-wecom-large-file-oss-delivery.md) | — |

## 前端

| 编号 | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20260714-1912 | Agent 跨平台桌面客户端 | 🔧 部分完成（H3待人工验收） | 共用 Runner 与 Runtime，UI竞态修复后的首期验收包已重建验证。 | [桌面设计 v3](system/desktop-agent-client-design.md) / [Runner 架构](system/agent-application-architecture-design.md) / [共同契约](system/runner-desktop-runtime-integration-contract.md) | [开发计划](plans/plan-desktop-agent-client.md) |
| 20261105-1416 | UI 内嵌帮助文字（P0+P1 批次） | ✅ 已完成开发 | 将《智能体后台操作指引》中的规则性帮助文字内嵌到页面 UI：新建 HelpExpand 组件。P0：定制页扩展提示词（8 维度指引表）+模板文件、访问授权三态规则、客服账号表单逐字段说明、防滥用三层策略。P1：复制副本 4 场景、顶级栏目移动限制原因、回复风格预置说明、通知规则 5 事件含义+Webhook 入口（工作日报积分提示页面已有，无需新增；回复风格按产品澄清口径写入「自定义风格暂无法关联数字员工使用」）。同步规范与覆盖位置台账见 frontend_dev.md「UI 内嵌帮助文字同步规范」，功能改版须同步修改帮助文字。 | — | — |
| 20261112-1000 | Runner 中断后前端自动停止与播放按钮恢复 | 🔧 部分完成 | 发版中断（interrupted+verification）后停止按钮自动消失并变为播放按钮，点击或直接发送文本均以 resume control 接续原任务；任务卡扩展到非持续任务的暂停/中断行。 | — | [计划](plans/plan-runner-interrupt-frontend-auto-resume.md) |

---

## 故障复盘索引

已迁移至独立文件：[incidents/index.md](incidents/index.md)。

---

## 调研报告索引

已迁移至独立文件：[research_index.md](research_index.md)。
