# 项目开发目录 — 已完成

> 已完成开发的功能归档在此文件。本文件同样是**纯索引**：`说明` 只写一句话，详情见链接的设计/计划文档；开发记录不写进表格。
> 编号与 [ideas.md](ideas.md) 一致，统一为 `YYYYMMDD-HHMM`（2026-09-18 由旧 `#N` 编号迁移，时间取 git 提交时间，个别无据可查的为近似值）。

## 基础设施

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260828-1421 | zhipu 默认模型切 GLM-5.3-Flash + 价目表多模态标识 | ✅ 已完成开发：zhipu 缺省切 GLM-5.3-Flash，token_cost_prices 增 is_multimodal 并标记存量多模态模型（预留图片路由）；commit 7f7e3235，缓存价修正 c1cd3915。 | [设计（已并入 weixin-cli 设计 §5）](design/weixin/weixin-cli-design.md) | — |
| 20261008-1633 | 过时架构文档与入口引用清理 | ✅ 已完成开发：删除失效执行/协作方案，修正架构规则并清理旧入口链接。 | — | [清理记录](plans/plan-obsolete-architecture-doc-cleanup.md) |
| 20260916-1534 | 阿里云 ASR AccessKey 更换指南 | ✅ 已完成：AK 到期重新申请流程（RAM 用户创建、Secret 一次性保存、AliyunNLSFullAccess 授权）。 | [运维文档](ops/aliyun-asr-accesskey-renewal.md) | — |
| 20260912-2313 | 端侧会话任务执行器（P5 后续） | ✅ 已完成开发：Runtime 常驻任务执行器 + Provider 观察器 + 云端决策服务（weixin.conversation.v1）；C0–C5 及发送性能/搜索清理/Demo 手册全过（微信 37/37、Runtime 31/31），NL 建任务向导 4 阶段完成（2026-09-28，回归 402 passed）；待部署真机验收，灰度仍禁止（见回滚手册）。 | [设计](design/desktop-automation/edge-session-task-design.md) / [Demo 手册](ops/weixin-auto-chat-demo-agent2.md) / [回滚手册](ops/edge-session-rollout.md) | — |
| 20260819-1126 | 母体 Agent 收敛（并入 AgentRunner 重构） | ✅ 已完成开发：agent.py 保留兼容壳，执行内核与运行时职责已拆分，后续遵守治理原则。 | [原则](system/agent-kernel-convergence-principles.md) | [完成记录](plans/plan-agent-runner-service.md) |
| 20260906-1101 | ✅ 用户行为审计日志 | 登录/登出/改密/管理后台增删改/普通用户关键动作全量留痕（user_behavior_logs 系统表，IP/UA/设备快照/token 指纹），满足安全审计与追责定位。 | [设计](system/user-behavior-audit-log-design.md) | — |
| 20260528-1533 | LLM 故障转移 | 提供商故障自动切换，多 Key 轮换与降级策略 | [设计](infrastructure/llm-failover-design.md) | — |
| 20260526-1053 | MCP Server | Model Context Protocol 服务器，支持外部工具集成 | [设计](infrastructure/mcp_server.md) | — |
| 20260618-1140 | ✅ 主智能体系统提示词优化 | 重写 master_agent.md / subagent_base.md 为原则化结构，新增「文件交付规则」段统一约束"工具生成文件后必须用 cp 注册"。 | [设计](system/prompt/agent-system-prompt-optimization-design.md) | [计划](plans/agent-system-prompt-optimization-dev-plan.md) |
| 20260618-1052 | 系统核心表文档 | 数据库核心表用途与关系文档，覆盖用户/对话/渠道/知识库/数字员工/SaaS/Prompt 管理等 30+ 张系统表。2026-06-18 | [文档](system/database_system_table.md) | — |
| 20260618-1705 | 缓存使用情况文档 | 系统缓存使用全景文档，覆盖 Redis 缓存、内存缓存、数据库去重共 17 类缓存，含键模式、TTL、失效策略。2026-06-18 | [文档](system/cache_usage.md) | — |
| 20260618-1518 | 文件存储使用情况文档 | 系统文件存储全景文档，覆盖新旧双轨路径、文件命名规范、目录结构、清理策略。2026-06-18 | [文档](system/file_usage.md) | — |
| 20260629-1329 | 服务器部署现状文档 | 记录腾讯云服务器（124.222.3.254）当前部署架构：Nginx 反代 + 生产/测试双 Docker 容器 + PostgreSQL 单实例双库 + 腾讯云 Redis，含端口/目录速查、… | [文档](../deploy/服务器部署现状.md) | — |
| 20260701-2009 | 三智能体开发流程规范 | 非平凡开发任务（新功能/Phase/多文件改动）的标准流程：开发智能体→测试智能体（独立测试+回归+启动安全检查）→CodeReview智能体（独立审查+修复必要问题）… | [规范](../.claude/rules/dev_workflow.md) | — |
| 20260624-2005 | 会话内上下文压缩（中期记忆） | 解决单 session 长会话上下文爆 token 问题（web/微信客服/钉钉/飞书/RPA 全渠道通用）。 | [设计](infrastructure/memory/context_compression_design.md) / [调研](research/context_compression_research.md) | [开发计划](infrastructure/memory/context_compression_dev_plan.md) |
| 20260608-1610 | 租户数据迁移 | 跨数据库租户数据迁移（知识库 + 业务表），UUID 稳定标识符 + replace/merge 模式 + Excel 导出导入。2026-06-08 | — | — |
| 20260629-1354 | Gunicorn 多 Worker 定时任务单 Worker 执行 | Gunicorn 多 worker 部署下，scheduled_tasks 表内的定时任务会被每个 worker 重复触发。 | [设计](infrastructure/scheduled-tasks-single-worker-design.md) | — |
| 20260602-1415 | ✅ Prompt 全生命周期管理 | 子智能体 Prompt 的版本化管理（编辑→提交版本→对比→回滚）+ 独立智能体管理页面 + 租户定制 Prompt（extra_md）DB 化。 | [设计](infrastructure/prompt-lifecycle-design.md) | [计划](infrastructure/prompt-lifecycle-dev-plan.md) |
| 20260428-1639 | 租户附件存储路径规范改造 | 把全项目 `storage/uploads/{tenant}/{user}/` 旧路径统一改造为 `storage/tenants/{tenant_id}/{scene}/` 新规范（依据 `.cla… | [规范](../.claude/rules/backend_dev.md) | [计划](plans/plan-tenant-storage-migration.md) |
| 20260829-0831 | ✅ Agent 运行时安全加固 | 已落地定时调度租户隔离、请求上下文并发隔离与恢复、敏感错误脱敏、知识库对象级租户保护、观测成本闭环、核心结构守卫及工具副作用审计。 | [设计](system/agent-runtime-safety-hardening-design.md) / [工具审计](system/enterprise-agent-platform/tool-effect-inventory.md) | — |
| 20260831-1515 | ✅ Agent 运行时安全债收尾 | 2026-08-31 复核确认 `X-Tenant-Id` 验权（`d517e859`/`ec177152`）… | [核对与修复设计](system/agent-runtime-security-debt-closure-design.md) | [开发计划](plans/plan-agent-runtime-security-debt-closure.md) |
| 20260813-1328 | skill_ws 临时工作目录清理机制 | ✅ 已完成开发。修复技能执行工作目录 `skill_ws_*` 创建后永不清理的临时文件泄漏（每月堆积，2026-08-13 迁移核对时发现）。 | — | — |
| 20260721-1521 | 后台定时/轮询任务外置 | ✅ 已完成开发（待线上验证）。 | [独立后台运行时设计](infrastructure/background-runner-design.md) | [计划](plans/plan-background-runner.md) |
| 20260908-0945 | 本地开发调试环境说明（venv / WSL 容器 / Mac 容器） | ✅ 已完成开发。明确三种本地开发调试方式与网络要求：①本地 venv；②WSL2 内容器（mirrored 镜像网络 + host 网络覆盖）；③macOS Docker Desktop 容器（端口发布到 localhost 或 host networking）。 | [说明](infrastructure/local-dev-environments.md) | — |
| 20260602-1416 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。 | [设计](infrastructure/observability-design.md) / [延伸设计](infrastructure/observability-channel-sessions-design.md) | [计划](infrastructure/observability-dev-plan.md) |
| 20260901-1430 | 数据库增量升级脚本 YAML 化 | 🔧 部分完成 | 将 deploy/db_update.sql 全量哈希重跑机制改为 deploy/db_update.yaml + datetime 增量执行（last_datetime），免手动清理、… | [设计](system/database-db-update-incremental-design.md) | — |
| 20260905-1907 | 租户附件存储规范整改（第二阶段） | 🔧 部分完成 | 主上传链路整改后仍有 7 处违规写入；存量迁移已完成（测试 09-05 / 生产 09-07），遗留归档保留 30 天。 | — | [迁移方案](plans/plan-storage-legacy-migration.md) |


## 系统功能

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260921-1100 | 宏陶商城产品知识库同步（hongtao_shop 专用模块） | ✅ 已完成开发：宏陶专用模块拉取产品/帖子 + VL 描述 + 结构化正文入知识库（raw_payload 存原始记录），2026-10-09 验收通过。 | [设计](system/hongtao-shop/hongtao-shop-kb-design.md) | [开发计划](plans/plan-hongtao-shop-kb.md) |
| 20261001-1849 | Agent/AgentRunner 重构及独立服务 | ✅ 已完成开发：内核与独立服务、Web 接入已完成；后续渠道接入另行跟踪。 | [设计](system/agent-application-architecture-design.md) | [计划](plans/plan-agent-runner-service.md) |
| 20260918-2010 | 用户取消请求的 trace 标记与历史可见性 | ✅ 已完成开发（待部署）。触发：线上 tr_30005ad7dd284b3e 排查发现用户取消后 trace 落库 completed 且 output 为空、取消轮次消息不落库（历史页整轮消失）。改动：①agent 两条取消路径（cancel_check 命中 yield cancelled 事件 / CancelledError 穿透包装层）均标记 trace status=cancelled + termination_reason=user_cancelled；②Web SSE 取消轮次落库 user+assistant 消息并打 metadata.cancelled=true（不落 tool 序列防悬空 tool_calls）；③前端停止按钮加 confirm；④历史消息渲染"用户已取消本轮回复"徽章；⑤追踪页（TraceBrowser/TraceDetail/SessionTraces）状态文案改"用户取消"+warning 色。开发+单测（5 用例）+独立验证完成，待部署。 | — | — |
| 20260901-1358 | 客户端计费统一接入（boss cli / 协会采集 / 未来客户端三模式） | ✅ 已完成开发。 | [设计](design/billing/client-billing-integration-design.md) | — |
| 20260922-0920 | 知识库文档元数据查看 | ✅ 已完成开发（开发+独立测试+审查，待部署）。文档操作列新增「详情」弹窗查看 documents.metadata：溯源/易变字段键值展示、raw_payload 折叠 JSON；新增 GET /documents/{id} 详情接口（按需拉取，租户隔离与 chunks 同口径）。 | [设计](system/knowledge-base/doc-metadata-view-design.md) | — |
| 20260918-2031 | 生产主日志 WARNING 审计（09-16~09-18） | ✅ 全部完成。3 天 119 条 WARNING 聚合 8 类全部收口：#1 deepseek-flash 部署时差噪音、#3 丢弃预览加长 500 字符（含完整 ASR 文本）、#4 sanitizer 降级 INFO + source 来源标签（8 入口）、#6 Redis DNS 降级三层防御（compose 健康依赖 + 启动重试 + 恢复清残留）、#7 ASR 400 根因为免费试用过期（渠道侧 WARNING 降级 INFO）、#2/#5/#8 人工核对/观察；另 agent_update.sh 发版不再连带重启 redis、Redis 夜间巡检任务（每日 00:30）上线。 | — | [审计报告](ops/log-warning-audit-20260918.md) |
| 20260922-1855 | 生产与测试环境 WARNING 审计（09-20~09-22） | ✅ 整体完成。生产 232 条 / 测试 79 条聚合收口：P2 Reply style not found 根因查明——生产 reply_styles 表被直连 SQL 两次清空（来源未归因），已从 09-15 备份恢复 4 个系统风格 + reply_styles_audit 审计触发器观察点上线（应用侧数量下降告警待部署）；其余类别经用户评估不重要 ⏸ 暂不处理（复发再查）；0918 一揽子开发项仍未部署，部署后剩余噪音将消失。 | — | [审计报告](ops/log-warning-audit-20260922.md) |
| 20260918-2106 | 测试环境主日志 WARNING 审计（09-16~09-18） | ✅ 全部完成。测试机 254 三天 4840 条 WARNING 聚合 15 类全部收口：#1 显示名重复告警删除（属正常设计，根除 95.6% 刷屏，63248ed6）、#9 deepseek-flash 未配单价复核为配置时差零复发、#12 max iterations 升级 ERROR；余类不重要不处理或属正常业务日志。 | — | [审计报告](ops/log-warning-audit-20260918-testenv.md) |
| 20260918-2200 | 租户类型（真实/测试）与平台统计口径改造 | ✅ 全部完成（开发+独立测试+CodeReview，待部署）。tenants 加 tenant_type（real/test，存量默认 test）+ 前后端 TenantType 枚举；/portal/token-usage 汇总只算真实租户（明细保留全部+类型徽章）；/portal/recharge 头部新增总充值金额汇总（真实租户、排除赠送）+「赠送金额」勾选（is_gift，积分照常入余额但不计入汇总，租户前台显示赠送徽章）；租户管理列表/表单支持类型字段。口径实时判断不影响审计数据；PLATFORM_USAGE 缓存 key 升 v2；租户类型变更主动失效月度缓存。上线后需管理员把生产真实租户标为 real（运营动作）。 | — | — |
| 20260916-2300 | 租户 API 接口文档通用模板（${APP_ID} 环境变量渲染） | ✅ 已完成并上生产：新租户开通免上传接口文档——通用模板 `configs/api_doc_templates/{skill}.md`（按技能基础名命名，具备对应技能的智能体适用），${APP_ID} 占位符加载期按租户环境变量（subagent 精确 → 租户级兜底）渲染，凭证类占位符由 http_api 运行时替换防泄漏；租户上传文档优先级最高；推送/对话 skill/SSO 三入口接入。设计点已并入总体架构文档核心组件节。 | [架构文档](../.claude/rules/architecture.md) | — |
| 20260810-2104 | 第一方 CLI / MCP Provider 架构规范 | ✅ 已完成：规范文档确定并被三个落地实例验证（BOSS CLI 首个参考实现、weixin-cli、wecom-cli），Runtime 插件宿主复用其 Provider 契约。 | [规范](system/first-party-cli-mcp-provider-standard.md) | — |
| 20260823-2057 | 租户间知识库共享 | ✅ 已完成开发。平台管理员两步配置实现知识库跨租户共享（A 租户知识库共享给 B 租户，B 数字员工检索时本租户+共享库合并检索）。 | [设计](system/knowledge-base/tenant-knowledge-sharing-design.md) | — |
| 20260710-2043 | 图片资产全链路承载能力（Phase 0+1+2） | **系统级横切能力**：定义图片资产（Image Asset）从来源/注册/寻址/嵌入/渲染的统一规范。 | [设计](system/image-asset-pipeline-design.md) · [主计划](plans/plan-image-asset-pipeline.md) · [Phase 3 计划](plans/plan-image-asset-pipeline-phase3.md) | — |
| 20260522-2147 | 记忆系统 | 短期记忆（滑动窗口）+ 长期记忆（摘要压缩），会话上下文管理 | [设计](memory/memory_design.md) | — |
| 20260526-1904 | 回复风格系统 | 可配置回复风格，不同场景的语气和格式控制 | [设计](system/design-reply-style.md) | — |
| 20260526-1709 | 对话体验优化 | SSE 流式输出优化、消息渲染改进、交互体验提升 | [设计](system/design-chat-experience-optimization.md) | — |
| 20260709-1230 | Skill 版本化触发重载 | 会话中已加载过的 skill，当 SKILL.md frontmatter `version` 提升后，强制 LLM 重新 `use_skill` 获取最新指南。 | — | — |
| 20260706-1205 | Redis 缓存管理页 | 平台管理后台新增「Redis 缓存」页面，供平台管理员枚举、查看、搜索、删除 Redis 键值。 | [设计](system/design-redis-cache-admin.md) | — |
| 20260710-1632 | 短信验证码 skill | ✅ 已完成开发。新增 `src/skills/sms-verification-1.0.0/` 供智能体调用，复用 `src/sms/` 通道和 `send_sms_code`/`verify_sms_code` 底层逻辑。 | — | — |
| 20260717-0916 | 技能白名单简化（三层->两层） | ✅ 已完成开发。 | — | — |
| 20260729-1613 | 工作成果记录 | ✅ 已完成开发。沉淀子智能体产生的重要工作成果（生成文件、完成业务操作、给出决策建议）到 `work_outcomes` 表，租户前台新增"工作成果"菜单。 | [设计](system/work-outcome-record-design.md) | — |
| 20260911-2042 | chat_lite 计费模型错配 | ✅ 已完成开发。11 处小任务调用点 chat_lite 与计费模型错配（多收租户）：交互型改走 chat_no_thinking 对齐实际消耗模型；已完成待部署。 | [已知问题记录](plans/chat-lite-billing-model-mismatch.md) | — |
| 20260902-2228 | 彻底移除 demo 模式与 SAAS_ENABLED 开关 | ✅ 已完成开发。demo 模式（`DEMO_ENABLED`/`VITE_DEMO_ENABLED` 控制）已无人使用且与租户模式并存造成大量死分支；系统定位即 SaaS 平台。 | — | — |
| 20260602-1417 | 知识库能力增强 | 🔧 已完成开发 | 知识库能力增强分期：文档级权限/检索日志（P1）、Rerank/改写/质量评估（P2）；分类树形化、批量移动、搜索跟随分类已上线。 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) / [子级文档包含统一](plans/plan-knowledge-category-subtree-filter.md) |
| 20260908-1606 | 知识库 Excel 行级分块（表头注入 + 格式前置判定） | 🔧 已完成开发 | 上传 Excel 时按「一行数据 = 一个 chunk」分块，表头字段名注入每个数据行 chunk（键值对形式），使单个商品/记录可独立命中检索。 | [设计](system/knowledge-base/excel-row-chunking-design.md) | — |
| 20260716-2107 | 租户积分充值与计费 | 🔧 已完成开发 | 预付费积分（credit）充值 + 对话消耗积分 + 余额报警 + 账单查询。 | [设计+计划](system/saas/tenant-credit-billing-design.md) / [LLM 计费接入设计](system/saas/llm-billing-integration-design.md) / [开发计划](plans/plan-llm-billing-integration.md) / [qwen3.7-flash 分段计价计划](plans/plan-qwen3-7-flash-tiered-pricing.md) / [qwen3.7-flash 平替计划](plans/plan-qwen3-7-flash-replacement.md) / [上下文缓存优化计划](plans/plan-qwen3-7-flash-context-cache-optimization.md) / [工具结果截断计划](plans/plan-tool-result-truncation.md) | — |
| 20260730-1649 | 连接中心 | 🔧 已完成开发 | 租户前台新增「连接中心」一级菜单（`/t/:tenant_id/connections`），单页面 4 Tab：API 配置 / 环境变量 / 内置连接器 / 自定义连接器。 | [设计](system/connection-center-design.md) | [计划](plans/plan-connection-center.md) |

## 数字员工 / 子智能体

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260929-1530 | spec-to-quotation-list 升级 2.0.0：分阶段交互确认门（合并 workbuddy v3.1.0） | ✅ 已完成开发。依第三方 workbuddy v3.1.0 技能包升级：新增两个确认门（生成前先与用户确认「空间」与「大类」清单，确认结果落盘 confirmed_structure.json，生成器 --structure 约束）、items.json 数据模型改为 items[] + space/theme/category 三维度数据驱动、global_themes 跨空间合并大类、非产品条目（供应商名录）不进主表；新增 discover_structure.py 扫描提案脚本。保留我方增强：--size-budget-mb（移植到数据驱动生成器）、merge_items.py（适配新格式 + code/维度/参数引用硬校验）、validate 跳过非报价工作簿。修复第三方 bug：global_themes 大类被 themes 白名单误过滤。新增通用控制工具 present_options（选项卡片：web 端点按钮、渠道端打字回数字，复用前端 quickOptions 机制零前端改动）。技能目录升为 spec-to-quotation-list-2.0.0，SUBAGENT.md v2.0.0。容器合成 PDF 全链路冒烟 4 文件 4/4 已算量全绿；单测 30 例通过。 | — | — |
| 20260917-1600 | spec-to-quotation-list WorkBuddy 对标改进 | ✅ 已完成开发。同输入下 WorkBuddy 18 分钟产出 8 文件 289 条，agent 20 轮中断。已修：process_message 硬编码 20 轮 -> 子智能体 get_max_iterations（agent.py）、probe 批量探查/禁读脚本源码（SKILL.md）、兜底图每文件去重（generate）、validate.py 数字表头崩溃、merge_items.py 参数引用自检、交付前强制产出任务台账 summary.md（跨会话续作手柄）。P2 后续：全文优先模式、Python 分片模式。WorkBuddy 289 条/43 组/92% 已算量为重测 benchmark。**2026-09-17 实测（scripts/e2e_spec2quote_test.py）**：39 轮完成（上限 60 余量 35%）、12 分钟、62 次工具调用、input 518 万 tokens 99% 缓存命中、9 文件+台账 182 条/75%；实测揪出真瓶颈并已修——模型改名后 config.yaml model_max_tokens 缺旧名 key 回退 16384 截断长输出（补 deepseek-v4-flash 别名）；遗留 P2：截断静默退出无提示。 | [对标文档](subagent/building-supply-chain/workbuddy-benchmark-improvement.md) | — |
| 20260917-1200 | spec-to-quotation-list 大项目扩容（轮数/分批/体积） | ✅ 已完成开发。解决真实规范书几百条目场景的三大瓶颈：智能体轮数硬编码 20 -> 子智能体可配置（context.max_iterations，缺省 20 上限 100）、items.json 一次性编写 -> 分批编条目 + merge_items.py 合并、xlsx 超 20MB -> --size-budget-mb 图片体积预算自动降质（floor 60）。单测 17 例 + 容器端到端回归通过。 | [设计](subagent/building-supply-chain/spec-to-quotation-scaling-design.md) | — |
| 20260917-1130 | 建筑行业方案清单生成器（spec book 转报价清单） | ✅ 已完成开发。引入第三方技能 spec-to-quotation-list（`src/skills/spec-to-quotation-list-1.0.0/`），把设计手册/FF&E 规范/spec book PDF 转成中英双语「室内材料报价清单」Excel（多文件拆分 + 图片嵌入 + 参数表驱动数量公式）。新建子智能体 `subagents/building-supply-chain/`（仅文件系统注册），已用样例 PDF 在容器内完成 analyze/probe/generate/validate 端到端验证。 | — | — |
| 20260917-1000 | 技能依赖自动补全机制（工具 + recap 任务） | ✅ 已完成开发。自定义数字员工勾选技能时自动补全其依赖的工具与 recap 任务，根治漏勾配置 bug。技能在 SKILL.md frontmatter 声明 `requires_tools` / `requires_recap`，后端保存兜底（`apply_skill_requirements`）+ 前端勾选联动；已登记 pre-sales-api（完整）、after-sales-api / order-api（http_api）。 | — | — |
| 20260518-2215 | 数字员工管理 | 实例管理、选择策略、并发控制 | 设计（旧文档已移除） | — |
| 20260512-2159 | 旅行顾问智能体 | 旅游报价、路线规划、酒店景点知识库集成 | [设计](subagent/travel-consultant/travel_subagent_design.md) | — |
| 20260518-2222 | 竞品调研智能体 | 竞品信息收集、HTML 预览、数据结构化输出 | [设计](subagent/competitor-research/competitor_research_subagent_design.md) | — |
| 20260518-2223 | 售后处理智能体 | 售后工单处理、退款退货流程自动化 | [设计](subagent/after-sales/after_sales_subagent_design.md) | — |
| 20260525-1709 | 投诉处理智能体 | 投诉分类、处理建议、升级流程 | [设计](subagent/complaint-handling/complaint-agent-design.md) | [计划](subagent/complaint-handling/complaint-agent-dev-plan.md) |
| 20260525-1713 | 客户跟进智能体 | 客户跟进任务管理、提醒、执行 | [设计](subagent/customer-followup/customer_followup_design.md) | — |
| 20260610-1314 | 业务页面元数据注册表 | ✅ 已完成开发。 | [设计](system/digital-employee/page-metadata-registry-design.md) | [开发计划](system/digital-employee/page-metadata-registry-dev-plan.md) |
| 20260602-1418 | 数据分析智能体 | ✅ 已完成开发。 | [设计](system/digital-employee/data-analysis-subagent-design.md) / [工具设计](system/digital-employee/smart-data-analysis-tool-design.md) | [工具开发计划](system/digital-employee/smart-data-analysis-tool-dev-plan.md) |
| 20260602-1419 | 数据源导入功能 | ✅ 已完成开发。通用知识库扩展：上传 Excel/CSV → 解析 Sheet → LLM 推理 Schema → 用户审核 → 入库；连接外部数据库导入；管理已导入表 Schema 与关联。 | [设计§三~§五](system/digital-employee/data-analysis-subagent-design.md) | [计划](system/digital-employee/data-source-import-dev-plan.md) |
| 20260610-1017 | 聊天附件数据分析 | ✅ 已完成开发。聊天中发送 Excel/CSV 附件自动注册到知识库并分析，共享 `schema_saver` 服务 + `upload_data_file` 工具（commit cfb0a78），`test_upload_shared.py` 覆盖。 | [设计](system/digital-employee/chat-attachment-data-analysis-design.md) | — |
| 20260612-1648 | 数据分析工具返回结构优化 | ✅ 已完成开发。 | [设计](system/digital-employee/data-analysis-tool-result-redesign.md) | — |
| 20260612-1649 | 工具消息持久化（事务性） | ✅ 已完成开发。修复工具结果跨 worker 丢失导致主智能体重跑分析的根因。 | [设计](system/digital-employee/tool-messages-persistence-design.md) | — |
| 20260526-1411 | 订单处理智能体 | 订单自动化处理流程 | [设计](subagent/order-processing/design.md) | [计划](subagent/order-processing/dev_plan.md) |
| 20260518-2224 | 内容生成通用设计 | 通用内容生成子智能体框架 | [设计](subagent/content_generate_universal_design.md) | — |
| 20260623-0807 | 旅游报价价格解析性能优化 | ✅ 已完成开发。酒店、景点门票/项目、行程解析均已改为 DeepSeek V4 Pro 关闭推理；酒店用候选压缩短 prompt，景点保留 LLM 主路径并新增团队票优先后处理，行程解析补充无项目景点/活动名原样保留自检。 | [设计](system/design-travel-quote-price-parser-performance.md) | — |
| 20260518-1544 | 酒店价格表六列格式升级 + 房型合并解析 | ✅ 已完成开发。 | [设计](subagent/travel-consultant/hotel_excel_to_kb_design.md) | — |
| 20260827-1221 | 酒店报价房型自动解析 + 含早写入备注 | ✅ 已完成开发。 | — | — |
| 20260622-0926 | 旅游报价酒店局部替换 | 客户换酒店时只重算住宿费用，其他 items 不变；按城市定位、生成新报价单。 | [设计](system/design-travel-quote-hotel-swap.md) | [计划](plans/plan-travel-quote-hotel-swap.md) |
| 20260623-1323 | 报价单生成支持指定酒店 | ✅ 已完成开发 generate.py 新增可选参数 hotel_overrides，客户明确指定的酒店一开始就生效。 | [设计](subagent/travel-consultant/generate-quote-hotel-override-design.md) | [开发计划](plans/plan-generate-quote-hotel-override.md) |
| 20260830-1400 | 旅游报价多人团动态车辆组合 | ✅ 已完成开发 车型推荐改为动态规划，按“最少车辆数 → 对应计价模式总单价最低 → 空座最少”选择组合；公里计价使用 per_km_rate 比较并按车型分别计价汇总，… | — | — |
| 20260715-1816 | Markdown 中文 PDF 渲染修复 | ✅ 已完成开发 md_to_pdf 改为 Markdown→HTML 后优先使用 Playwright/Chromium 打印，fpdf2 仅作带告警降级；… | — | — |
| 20260812-1500 | HTML 生成清洗与长文档预览修复 | ✅ 已完成开发 write 工具支持从“模型说明文字 + ```html 围栏 + 尾部说明”中准确提取完整 HTML，同时保留 Markdown 内普通代码块；… | — | — |
| 20260701-1513 | PDF.js 最小化前端预览 | ✅ 已完成开发 PDF 预览由浏览器原生 iframe 改为前端动态加载 PDF.js 5.4.624，以 Canvas 逐页渲染并纵向排列；无工具栏、分页、搜索或缩放控件，仅保留自然滚动查看，… | — | — |
| 20260727-1650 | 子智能体 LLM 配置扩展（按 Provider 覆盖 MODEL_CODE） | ✅ 已完成开发 子智能体 `SUBAGENT.md` 和 DB `subagent_definitions` 表支持指定主 provider + 各 provider 的 model_code 覆盖，… | [设计](subagent/subagent-llm-config-override-design.md) / [Failover 扩展](infrastructure/llm-failover-design.md#7-子智能体-model_codes-覆盖) | — |
| 20260901-1405 | 简历-职位匹配体系（招聘操作智能体） | ✅ 已完成开发（真机演示验收通过 2026-09-01）。 | [设计](design/recruiting/resume-job-matching-design.md) | — |
| 20260901-1218 | 招聘演示闭环（一键「筛选简历」快捷按钮 + 智能体链路） | ✅ 已完成开发（真机演示验收通过 2026-09-01）。 | 无独立设计（复用 boss_* 工具；utils/quickPrompts.ts + SUBAGENT.md） | — |
| 20260721-0847 | 旅游顾问行程 HTML 长图导出（图片独立行 + x-to-image 图片内联） | ✅ 基础能力已交付（按用户确认归档）：x-to-image 工具/服务的 HTML 图片 base64 内联（file_id/远程 URL→data URI + 双轨租户注入）完成并被企微客服渠道长图复用；行程子智能体 prompt 仍走 Word 备路径（HTML 导出未接线、img-row 布局未实现），后续需求另行立项。 | [x-to-image 设计 §13](tools/x-to-image/x-to-image-design.md) | — |
| 20260720-2103 | 旅游报价数据补全（消费 Excel 模板工具） | ✅ 已完成开发：交付物为通用 excel_process 工具的 fill_template 模板填充能力（excel_template_ai：按用户提供的样例模板智能填充、样式保留、行数不匹配处理），其他智能体已在用；travel-quote skill 保持自研引擎不变，如需消费仅配置级接入。row_total/人群拆分/大交通等领域扩展未实现（登记于设计 §4，非核心）。 | [设计](system/design-travel-quote-template-engine.md) | — |

## 工具

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260923-2340 | PPT 图片资产接入（分析图表嵌入） | ✅ 已完成开发：ppt_process 新增 images 结构化入参（path+title+caption，≤20 张）+ 租户根域校验 + planner 资源注入与确定性 reconcile + python-pptx 兜底图片页渲染；开发+测试+CR 完成（2026-09-23，ppt 套件 98 passed）。 | [设计（整合版）](tools/ppt/ppt_tool_design.md) | — |
| 20260923-1142 | 数据分析产物复用（analyze_data 跨调用） | ✅ 已完成开发：会话级产物注册表 + load_output 工具 + 子代理清单注入 + 主代理 reusable_outputs 引导；P0 session_id 路径穿越已修（PoC 闭合）、254 定向测试通过（2026-09-23）。 | [设计](tools/data_analysis/analysis-artifact-reuse-design.md) / [事故复盘](incidents/analysis-agent-cost-392-credits-incident.md) | — |
| 20260831-1509 | wecom-cli 第一方企业微信操作 CLI / MCP Provider | ✅ 已完成开发：M1–M12 + E5 全落地，13 命令真机验证通过（M12 网络查找直达路线）；read_session 双通道（服务端 /session-history ×100 积分 + 本地 OCR 兜底 + 直连静态 token）、target_name 智能分发、Runtime productTrust 注册；209 用例。遗留：常驻 OCR 迁移、协议壳对接（RPA C# 壳清退）、side 判定启发式。 | [设计](design/wecom/wecom-cli-design.md) | — |
| 20260811-1013 | weixin-cli 第一方微信操作 CLI / MCP Provider | ✅ 已完成开发：8 个 MCP 工具（probe/chat_search/name_resolve/message_send(+v2 Runtime 签名许可)/history_read/unread_list/session_observer）+ 服务端代理计费（moonshot/kimi-k3 白名单 + DPAPI 激活）+ C5 便携打包与插件 Host 交付；205 用例。原路线图文章/公众号域未产品化（转向会话观察族）。遗留：kimi-k3 价目部署、泄漏 key 轮换。 | [设计](design/weixin/weixin-cli-design.md) | — |
| 20260806-1318 | 协会信息收集客户端（交付产品） | ✅ 已交付客户（2026-08-10）：Electron GUI（NSIS 安装包）+ 内嵌 PyInstaller CLI + 服务端代理计费/激活码（/api/client/v1/*），四步流水线（文心联网采集→官网 Playwright 采集→微信搜一搜搜姓名→手机号取证）产出 Excel；约 290 用例。交付后修复激活页地址与停止按钮；部署手册保留，前身内部工具（enrichment-cli/ui）过时文档已删。 | [设计](tools/association-client-design.md) / [部署手册](tools/association-client-deployment.md) | — |
| 20260917-1422 | BOSS 简历识别去 OCR 化（GLM-5.3-Flash 多模态） | ✅ 已完成开发：客户端只交图，云端 VL 一次评估姓名/总结/评分/key_info（11s/份），姓名门防点错人、文本不可信防计费造假，识别费 1 积分/份成功即扣；commit 5d14e64d。 | [设计](design/desktop-automation/boss-resume-vl-recognition-design.md) | — |
| 20260819-1724 | 邮件工具整体审查与整改 | ✅ 已完成开发：email_process 三合一（send/read/download_attachments 确定性分发）+ email_lib 拆分 + 异步包裹/超时/错误脱敏等规范修复；2026-10-09 验收通过。 | [审查](tools/email/email-tool-audit.md) | [开发计划](plans/plan-excel-etl-and-email.md) |
| 20260630-1555 | PDF 工具质量验证增强 | ✅ 已完成开发：inspect/render_pages/validate 操作、结构化检查、生成后自动校验、页码语义统一、依赖探测、Playwright HTML 转 PDF、图片 inline 双入口；2026-10-09 验收通过。 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260819-1127 | 多源脏 Excel → 标准模板 LLM 抽取填充 | ✅ 已完成开发：excel-to-template skill 管线（render_llm_view 语义渲染+脱敏往返、LLM 抽取+确定性校验回喂修复、子进程 LLM 计量、文件/邮件双入口）；2026-10-09 验收通过。 | [调研+决议](tools/excel/excel-etl-gap-analysis.md) | [开发计划](plans/plan-excel-etl-and-email.md) |
| 20260720-2104 | Excel 智能模板填充工具（样例 + 数据 → 按版式生成） | ✅ 已完成开发：无状态 fill_template（AI 数据感知结构分析+行数不匹配处理+样式位级保留），2026-09-23 补缺 data 首拍引导兜底；2026-10-09 验收通过。 | [设计](tools/excel/excel-template-ai-design.md) | [开发计划](plans/plan-excel-template-ai.md) |
| 20260819-1724 | 工具注册与 Agent 解耦 | ✅ 已完成开发。 | [设计](tools/tool-auto-discovery-design.md) / [总体设计](plans/plan-agent-registration-decoupling.md) | [开发计划](plans/plan-agent-registration-decoupling.md) |
| 20260821-0835 | 普通工具特殊分支删除与统一执行链 | ✅ 已完成开发。 | [设计与开发计划](plans/plan-tool-outcome-presentation-decoupling.md) | [计划](plans/plan-tool-outcome-presentation-decoupling.md) |
| 20260630-1713 | 文件生成类工具入参语义拆分与路由健壮性 | ✅ 已完成开发。Word/PDF/Excel/PPT 通用 `context` 同时承载"用户目的 + 待处理正文 + 隐含参数"导致内部 LLM 路由不稳定、转换正文被工具指令污染。 | [设计](tools/tool-input-contract-redesign.md) | [开发计划](tools/tool-input-contract-redesign-dev-plan.md) |
| 20260714-1911 | skill_complete 工具彻底删除 | ✅ 已完成开发。 | [设计](system/skill-complete-removal-design.md) | — |
| 20260612-0940 | 文件工具四件套（read/write/edit/cp）v2 | ✅ 已完成开发。拆分旧 text_file_writer/file_reader/file_list 为四个原子工具，支持 SKILL.md `<SKILL_ROOT>` 占位符。 | [设计 v2](tools/text-file/file_tools_redesign_v2.md) | [开发计划 v2](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 20260518-2225 | 浏览器自动化工具 | 网页自动化操作、数据采集、Markdown 转换 | [设计](tools/browser/browser_automation_design.md) | — |
| 20260602-1649 | 知识库检索租户隔离 | 知识库检索工具添加 tenant_id 过滤，修复跨租户数据泄露 + 分块 overlap 修复。2026-06-02 | [设计](tools/knowledge-base-search-tenant-isolation-design.md) | [计划](tools/knowledge-base-search-tenant-isolation-dev-plan.md) |
| 20260512-1711 | PPT 生成工具（基础版） | ✅ 已完成开发；初始架构决策已并入现行整合版设计文档（2026-10-09 文档整合）。 | [设计（整合版）](tools/ppt/ppt_tool_design.md) | — |
| 20260630-2158 | PPT 工具增强与 HTML 转 PPTX 导出 | ✅ 已完成开发（Phase 0-8，2026-07-01）；设计与开发计划已并入整合版设计文档（2026-10-09 文档整合）。 | [设计（整合版）](tools/ppt/ppt_tool_design.md) | — |
| 20260518-2226 | Word 工具 | Word 文档读取与生成 | [设计](tools/word/word_tool_design.md) | — |
| 20260511-1713 | PDF 工具 | PDF 文档解析与处理 | [设计](tools/pdf/pdf_tool_design.md) | — |
| 20260509-1748 | Excel 工具 | Excel 文件读取与数据提取 | [设计](tools/excel/excel_tool_design.md) | — |
| 20260609-1552 | Excel 工具重构 | ✅ 已完成开发 移除 analyze 和 chart 操作（由数据分析工具替代），增强 read 操作（复制 FileReaderTool 的文档级读取能力）。重构结果已并入总设计（重构专项文档 2026-10-09 清理）。 | [设计](tools/excel/excel_tool_design.md) | — |
| 20260518-2227 | HTTP API 适配器 | 通用 HTTP API 调用适配器 | [设计](tools/http_api_adapter_design.md) | [指南](tools/http_api_skill_developer_guide.md) |
| 20260519-1036 | 文本文件生成工具 | 文本/Markdown 文件生成与内容写入优化 | [设计](tools/text-file/text_file_generator_design.md) | — |
| 20260622-1311 | Pandoc 安装与部署 | word_process 工具的 md_to_word 操作依赖 Pandoc 命令行工具。 | [安装部署指南](tools/md-to-word/pandoc-install-guide.md) | — |
| 20260622-1416 | 酒店知识库搜索工具（hotel_search） | 仿 attraction_search 新增酒店专项搜索工具，名称优先+向量兜底检索 source_type='hotel_resource'，返回酒店信息+价格明细表（chunk_index=1，… | [设计](subagent/travel-consultant/hotel_search_tool_design.md) | [计划](plans/plan-hotel-search-tool.md) |
| 20260514-1212 | 景点知识库搜索工具（attraction_search） | 旅游顾问子智能体的景点专项搜索工具，纯向量检索 source_type='attraction_resource'。补登记（此前漏登）。2026-05-14 | [设计](subagent/travel-consultant/attraction_search_tool_design.md) | — |
| 20260701-2001 | x-to-image 内容转图片服务 | 将文本/Markdown/HTML 渲染为一张尺寸可控的长图(PNG)，Playwright **headless** 全页截图 + Pillow 拼接/截断/体积控制；后续演进 HTML 图片 base64 内联（租户身份 + ImageRegistry）并被企微客服渠道长图复用（演进记录见设计 §13）。 | [设计](tools/x-to-image/x-to-image-design.md) | — |
| 20260614-2003 | 研学报价技能 Prompt 稳定性优化（方案 A） | ① 同一行程多次报价金额漂移，通过固化 itinerary_parser 的 prompt 规则收敛方差，实测 5 次连续调用 hash 完全一致；② 重构 generate.py 返回结构：… | [设计](system/design-travel-quote-prompt-stability.md) | [计划](plans/plan-travel-quote-prompt-stability.md) |
| 20260612-0941 | 文件操作工具集重新设计 v2 | ✅ 已完成开发。拆分为 read/write/edit/cp 四个工具（对齐 Claude Code 命名），删除 file_list 工具，edit 三种编辑模式（replace_string/replace_section/replace_lines）。 | [设计](tools/text-file/file_tools_redesign_v2.md) | [计划](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 20260518-2228 | Word 客户模板格式参考生成 | ✅ 已完成开发（三智能体流程）。 | [设计](tools/word/word_tool_design.md) | — |
| 20260518-2229 | Word 模板占位符填充增强（场景一） | ✅ 已完成开发（三智能体流程）。 | [设计](tools/word/word_tool_design.md) | — |

| 20260920-2100 | 子智能体知识库栏目授权硬隔离 + 数据表移动兜底 | ✅ 已完成开发（2026-09-21，待部署） | 2026-09-20 双案例驱动：①数据表 `[数据表]` 文档被知识库移动后 source_type 被覆盖，数据分析智能体 search/list/load 三处按 `data-analysis-metadata` 过滤搜到 0 张表（tenant_c148f4efb4dc 生产案例）；②数字员工授权栏目仅 system prompt 软引导，LLM 不传 source_type 即读本租户全部分类（tenant_e9b2fab93a2f 测试反馈）。方案：授权语义确认为「自有栏目为空=允许全部（默认），勾选≥1=仅允许勾选栏目」；tenant_range 新增 load_authorized_source_types，knowledge_base_search / knowledge_file_search 收口（未传收窄为授权集合、传未授权拒绝返回可用清单）；analysis_agent 表发现/加载按 `[数据表]` 前缀 + metadata 判定放宽（豁免栏目授权，共享侧维持精确对）；schema_saver 去重放宽；system prompt 与授权弹框文案同步。零 schema 变更。 | [设计](system/knowledge-base/subagent-kb-category-authorization-design.md) | — |
## 渠道集成

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20261009-1452 | 微信客服完整 Markdown 交付 | ✅ 已完成开发：默认原文前缀与原生完整 MD，仅正文图片考虑长图；待真机验收。 | [设计](channel/wecom_kf/wecom_kf_design.md#十四2026-10-09-长答复交付调整) | — |
| 20261008-1340 | 微信客服原渠道恢复与 AgentRunner 接入 | ✅ 已完成开发：原渠道接入独立 Runner，误重构已移除，迁移历史保留；agent2 已发布并真机验收通过（文本/语音/合并/取代）。 | [设计](system/agent-application-architecture-design.md) | [计划](plans/plan-wecom-kf-channel-restore.md) |
| 20260916-1830 | 公众号自有号清单源（历史文章导入，主通道） | ✅ 已完成开发：租户扫码绑定自有号，定期拉「发表记录」清单走既有 URL 直采入库（首次回填上限+增量重叠即停，r3 修生产超量回填事故）；2026-10-09 验收通过。 | [设计](system/wechat-mp/wechat-mp-list-source-design.md) | — |
| 20260914-1901 | 微信公众号内容入知识库 | ✅ 已完成开发：回调/URL 直采/手动粘贴/freepublish 接口对账多通道入库，图片 VL 解析（门禁放开+r2 白描指令/200 张护栏）、content_md 组装、500 字总结、按 token 计费；WP0–WP13 全交付，2026-10-09 验收通过。 | [设计](system/wechat-mp/wechat-mp-knowledge-ingestion-design.md) | [计划](plans/plan-wechat-mp-knowledge-ingestion.md) |
| 20260820-2131 | 微信客服处理超时等待提示 | ✅ 已完成开发（2026-08-20 开发+单测完成，已部署真机验证）。 | [计划](plans/plan-wecom-kf-waiting-indicator.md) | — |
| 20260819-1322 | 微信客服回复长图化 + 废除渠道约束提示词 | ✅ 已完成开发（2026-08-19 开发+单测完成，已部署真机验证）。 | [配额方案（含 2026-08 变更）](channel/wecom_kf/reply_quota_control_plan.md) | — |
| 20260822-1022 | wecom_kf 多媒体消息支持 | ✅ 已完成开发。 | — | — |
| 20260619-2043 | 钉钉渠道接入 | ✅ 已完成开发。钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。 | 设计（旧文档已移除） / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 20260903-1501 | 飞书渠道接入代码审核 | ✅ 已完成开发。 | — | [审核报告](plans/feishu-channel-code-review.md) |
| 20260518-2230 | 企业微信集成 | 应用消息收发、回调处理 | [设计](channel/wecom/wecom-integration.md) | — |
| 20260518-2216 | 飞书 / 钉钉集成 | 飞书和钉钉渠道适配器实现 | 设计（旧文档已移除） | — |
| 20260619-1950 | 飞书渠道对接（完整实施） | 修复 FeishuAdapter 错误实现（AES 密钥、签名验证），补齐 crypto/media 子模块、连接池复用、长消息拆分、速率限制、欢迎消息，… | [方案](channel/feishu/implementation_plan.md) / [实施](channel/feishu/integration_guide.md) | — |
| 20260629-2159 | 微信客服转人工工具优化（schema + 渠道隔离） | ✅ 已完成开发 优化 transfer_to_human：①reason 改为必填；②渠道隔离完全由工具 execute 段的 get_kf_context 判断，… | [设计](channel/wecom_kf/transfer_to_human_optimization.md) | [计划](channel/wecom_kf/transfer_to_human_optimization_plan.md) |
| 20260820-1623 | 渠道上下文丢失修复（channel_messages 事务化 + 连续 user 兜底 + 合并写入时机 + send_response 统一封装） | 已通过用户手工测试。P0-1/P0-2/P0-3 全部落地，**一次改造全渠道复用**（wecom_kf/wecom/wecom_personal_rpa/dingtalk/feishu 共 6 个调用点统一走 `ChannelSessionManager.process_and_persist`）。 | [调研](incidents/wecom-kf-context-loss-research.md) | — |
| 20260629-2200 | wecom_kf 用户撤回消息处理（同批次剔除 + 跨批次标记 + 后台可见） | 已完成：①撤回事件识别（origin=4，event_type=user_recall_msg，读 event.recall_msgid 而非 event.msgid）+ 事件去重；… | — | [开发计划](channel/wecom_kf/message_recall_plan.md) |
| 20260820-1624 | 渠道/web 上下文重建两大病根修复（来源分流 + 窗口裁剪方向） | #33 的 P0 修复未覆盖的两个真因，均为读取侧 bug。 | [调研](incidents/wecom-kf-context-loss-research.md) §9~§10 | — |
| 20260708-1135 | chat_records 表 web/channel 隔离 | ✅ 已完成开发。 | — | — |
| 20260708-1136 | 渠道语音 ASR 补齐（wecom / feishu / dingtalk） | ✅ 已完成开发。渠道场景下语音消息在渠道层完成阿里云 ASR 转文字后送入 agent，LLM 不再承担语音识别。 | — | — |
| 20260701-2142 | wecom_kf 单轮回复配额管控（提示词注入 + 渲染层兜底） | ✅ 已完成开发。**背景**：客户一句「发我英文版和日语版」触发智能体单轮产出 7 个发送单元（文字+表格图+Word ×2 语言+中文总结），微信客服 send_msg 5 条/48h 上限被击穿，最后 2 个 Word 文档丢失。 | — | [开发计划](channel/wecom_kf/reply_quota_control_plan.md) |
| 20260828-1111 | 微信客服 95013 (conversation end) 错误修复 | ✅ 已完成开发 **2026-08-28 生产报错调查（2026-08-29 完成）**：13:24 前后 44 次 `errcode=95013` 刷屏 + 25 条客户消息被丢弃。 | — | — |
| 20260818-0924 | 企微客服账号引流归因（客服账号管理 + 二维码 + C端客户引流统计） | 🔧 已完成开发 | **Phase 1 后端 + Phase 2 前端已开发完成**（2026-08-18）：企微 API 5 方法（account_add/del/update/list + add_contact_w… | [方案](channel/wecom_kf/kf-account-referral-plan.md)（含开发计划） | — |
| 20260821-2010 | 微信客服售前咨询客户留资（手机号 / 顾问微信二维码） | 🔧 已完成开发 | **适用边界**：仅售前咨询场景需改造；售后客服支持场景无需改造，现有功能即满足。 | [设计](subagent/pre-sales/lead-capture-design.md) | [开发计划](subagent/pre-sales/lead-capture-dev-plan.md) |
| 20260829-1450 | 微信客服员工-客户对话可见性 | ✅ 已完成开发 **2026-08-29 开发完成**。 | — | — |
| 20260904-1308 | 售前推送代码级兜底（recap 任务 external_push） | 🔧 已完成开发 | 售前推送代码级兜底：recap 任务收尾时 external_push 直推第三方，替代实测不生效的提示词驱动方案。 | [方案](subagent/pre-sales/external-push-code-hook-design.md) | — |
| 20260904-1309 | 子智能体 Recap 机制（轮后异步沉淀任务） | 🔧 已完成开发 | **定位**：SUBAGENT.md 目前只有对话期配置（tools/skills/context），缺「每轮问答结束后沉淀类动作」的表达位。 | [机制设计](subagent/recap-mechanism-design.md) | — |
| 20260908-1729 | 售前推送链路修复（头像/性别注入 + http_api 审计 + 隐藏命令清留资） | 🔧 已完成开发 | 售前推送链路修复：头像/性别注入 + http_api 通道问题（2026-09-08 agent2 排查确认三问题）。 | — | — |

## SaaS 多租户

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260518-2231 | 多租户 SaaS 架构 | 租户隔离、订阅计费、权限管理、管理后台 | [设计](system/saas/multi-tenant-saas-design.md) | — |
| 20260518-2232 | 租户级 Skills | 每个租户维护自己的 Skills 文件夹，按需加载 | [设计](system/saas/tenant_skills_design.md) | — |
| 20260518-2233 | 订阅权限合并 | 订阅计划与功能权限的统一管理 | [设计](system/saas/subscription_permission_merge_plan.md) | — |
| 20260924-2047 | ✅ 网页端会话页面 | 管理员查看 web 端聊天记录：三栏布局（用户列表+用户名搜索 / 会话列表+智能体名称搜索 / 消息记录），菜单入口在「办公软件会话」下，仅管理员可见。后端 /api/saas/web-sessions/*（chat_sessions/chat_messages，租户隔离）。 | — | — |

## 前端

| 编号 | 功能 | 说明 | 设计文档 | 开发计划 |
|---|------|------|---------|---------|
| 20260526-1120 | 前端样式统一 | 统一 UI 组件库、语义化 Token、变体系统 | [设计](research/frontend/phase1-unify-foundation-design.md) | [计划](research/frontend/phase1-unify-foundation-plan.md) |
| 20260602-1420 | 租户定制提示词前端入口（恢复 + 上线侧栏菜单） | ✅ 已完成开发。 | [设计](infrastructure/prompt-lifecycle-design.md) | — |
| 20260602-1421 | 定制提示词页模板文件上传 | ✅ 已完成开发。 | [设计](infrastructure/prompt-lifecycle-design.md) | — |
| 20260611-1625 | 前端 MyTextarea 通用组件 | ✅ 已完成开发。通用大文本框组件：全屏编辑、MD 预览、字数统计，预留 AI 优化/占位符识别 slot。 | [选型+设计](research/frontend/my-textarea-component-research.md) | — |
