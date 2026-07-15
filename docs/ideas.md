# 项目开发目录

> 本文件是进行中和待开发内容的索引，链接设计文档和开发计划文档，跟踪开发状态。
> 已完成的功能归档在 [ideas_finished.md](ideas_finished.md)。

---

## 状态说明

| 状态 | 含义 |
|------|------|
| 🔧 部分完成 | 已开始开发，部分阶段完成 |
| 📋 待开发 | 设计完成或进行中，尚未开始编码 |
| 💡 灵感 | 早期想法，尚未正式设计 |

---

## 基础设施

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 1 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。Phase 1 全部完成（含渠道追踪方案 C：TraceCollector 下沉到 `Agent.process_message`，从 record_service 自动读 source_type，渠道零改造）。1.6（单测/e2e）和 1.7（JSONL 双写迁移）已取消：obs 系统每日真实流量运行已事实验证；JSONL 与 obs 永久并行。Phase 2-4 未开始。2026-07-07 | [设计](infrastructure/observability-design.md) / [延伸设计](infrastructure/observability-channel-sessions-design.md) | [计划](infrastructure/observability-dev-plan.md) / [延伸计划](infrastructure/observability-channel-sessions-dev-plan.md) |

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 🔧 部分完成 | Phase 0 知识库分类管理已完成。Phase 1-2 已重排（2026-07-06）：**Phase 1（合规阻塞，2 周）= 文档级权限 + 检索日志**；**Phase 2（销售精度 + 体验，3-4 周）= LLM Rerank + 查询改写 + 质量评估（与 Rerank 配套）+ Pipeline 协调器**。重排理由：原 Phase 1 按实现依赖排序无商业化优先级，现按销售推动力重排——权限是中大型企业上线卡点，质量评估需与 Rerank 配套（否则评估的是基线无意义）。 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) |
| 35 | 短信验证码 skill | 🔧 部分完成 | 新增 `src/skills/sms-verification-1.0.0/` 供智能体调用，复用 `src/sms/` 通道和 `send_sms_code`/`verify_sms_code` 底层逻辑。`send` + `verify` 两个 CLI 子命令，频控 60s 同号锁 + 24h 上限 10（Redis 降级内存），输出 JSON 不含 code、日志脱敏 `***`。三智能体流程通过（27/27 单测 + 相邻 skill 回归 22/22 + 启动安全 + CR 无 P0/P1），待提交。2026-07-10 | - | - |
| 36 | 技能白名单简化（三层->两层） | 🔧 部分完成 | 管理后台 3 个技能列表 API（`/api/admin/agent-definitions/meta/skills`、`/api/admin/subagents/skills`、`/api/subagents/skills`）改读 `SkillRegistry.list_all_loaded_skills()`（未过滤全集），不再被 `master_agent.allowed` 卡。新增 `_all_skills` 字段 + 两个公开方法。顺手修复 `/api/subagents/skills` 路由被 `/{agent_id}` 抢占的预先存在问题。每加一个 skill 只需改 1 处（子智能体用->DB / 主智能体用->config.yaml）。三智能体流程通过（11/11 单测 + 启动安全 + CR 修复 1 个 P1 路由抢占），待提交。2026-07-10 | - | - |

## 数字员工 / 子智能体

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 9 | 数据分析智能体 | 🔧 部分完成 | 统一智能分析工具（SmartDataAnalysisTool）：内置 LLM 编排 + pandas/numpy 执行引擎。数据源导入已完成，分析工具待开发。2026-06-05 | [设计](system/digital-employee/data-analysis-subagent-design.md)、[工具设计](system/digital-employee/smart-data-analysis-tool-design.md) | [工具开发计划](system/digital-employee/smart-data-analysis-tool-dev-plan.md) |
| 9a | 数据源导入功能 | 🔧 部分完成 | Phase 1+2 代码完成（后端 API + 前端页面），Phase 3 集成测试待做。2026-06-03 | [设计§三~§五](system/digital-employee/data-analysis-subagent-design.md) | [计划](system/digital-employee/data-source-import-dev-plan.md) |
| 9c | 数据分析工具返回结构优化 | 🔧 部分完成 | P0 完成：工具返回改为 conclusion + artifacts + analysis_meta 三层结构，表格 artifact 带 preview 最多 10 行；AnalysisAgent system prompt 加收尾规范约束 conclusion 必须含产物指代。P1 主智能体 prompt 已由用户配置。2026-06-12 | [设计](system/digital-employee/data-analysis-tool-result-redesign.md) | — |
| 9b | 聊天附件数据分析 | 🔧 部分完成 | 聊天中发送 Excel/CSV 附件自动注册到知识库并分析。共享 schema_saver 服务 + upload_data_file 工具。2026-06-09 | [设计](system/digital-employee/chat-attachment-data-analysis-design.md) | — |
| 17 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | [设计](subagent/crm/crm_subagent_design.md) | — |
| 20 | 社媒内容运营智能体与聚合平台 | 🔧 部分完成 | 已完成首期可开发条件评估，并落地核心骨架：OpenSpec change、社媒核心表、连接器协议、账号/计划/内容版本/审核/发布任务/数据概览 API、聚合工作台页面和页面元数据。正式服影响已屏蔽：页面元数据状态为 planned，不进入业务菜单；社媒子智能体定义以 `.disabled` 草案保存，不会被加载器自动加载。真实微信公众号 HTTP 发布、视频号发布包下载/数据导入、调度器和真实账号验收仍待 Phase 5+。2026-06-30 | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-operations-agent-design.md) | [开发计划](system/digital-employee/social-media-operations-agent-dev-plan.md) |

## 工具

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20 | 浏览器混合执行、可视化与人工接管 | 📋 待开发 | 2026-07-14 完成 v2.6 设计：`auto` 强制服务端 headless 优先，仅确定的本地能力预检失败或 HeadlessFailureDetector 高置信失败可经 EscalationGate 转客户端；普通 HTTP/网络故障不升级。人工参与采用持久化 ToolSuspension，从同一 run/page/context 和原 tool_call_id 自动续跑。采用 Agent-first 原则：本地执行是 Agent Desktop 的可选 browser runtime，客户端技术栈、认证、发布和生命周期跟随 Agent 主应用；runtime 关闭或故障不得影响 Agent 主链路，不再交付独立 Browser Companion。 | [设计](tools/browser/browser_visualization_design.md) | [开发计划](tools/browser/browser_execution_dev_plan.md) |
| 29 | PDF 工具质量验证增强 | 🔧 部分完成 | P0+企业文档增强+Playwright HTML 转 PDF 已完成：新增 inspect/render_pages/validate、结构化检查、生成后自动校验、页码语义统一和依赖探测；修复 split 保存顺序、无效页码静默成功、文件名安全、HTML 表格顺序、测试漂移和旧设计文档不一致问题；新增 clean_metadata/add_watermark/protect/compress/extract_images/rotate；复杂 HTML/CSS 默认优先 Playwright print-to-pdf，失败回退 fpdf2；Docker 增加 LibreOffice Writer 及构建期校验，DOCX 转 PDF 统一使用 LibreOffice 隔离配置目录并保留失败诊断。PDF 全量单测通过，127 passed；真实 Playwright 生成和结构校验通过。2026-07-01 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | [开发计划](tools/pdf/pdf_tool_quality_validation_dev_plan.md) |
| 30 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。适用场景：结构化业务数据直接生成正式报价单、报告、对账单、审批单等，要求页眉页脚、页码、签章区、复杂跨页表格和版式位置稳定。当前复杂 HTML/CSS 已由 Playwright print-to-pdf 覆盖，不优先投入 reportlab。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 32 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。当前已有结构化校验和 Playwright HTML 转 PDF，暂不投入完整样本集建设。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 34 | 工具总体优化梳理 | 🔧 部分完成 | 跨工具层面的优化梳理登记文档（区别于单工具设计）。进度：#34a `upload_to_remote` 工具直接删除 ✅（已提交）；#34b 全部 27 个业务工具 token 审查 + 规范 ✅；Phase 0/1（删工具+建规范+helper）✅；Phase 2 试点 http_api/pdf_process/paddleocr token 优化 ✅（token 对比达标，服务器验证通过）；#34c **大内容落盘检索闭环 + grep 工具** 📋——Phase 2 截断后完整内容丢弃形成信息黑洞，新增「截断→落盘临时文件→read/grep 回读」闭环（对齐 Claude Code harness），含 `_spill.py` 落盘管理器 + 新增 grep 工具（调 ripgrep）+ Dockerfile 装 rg；#34d **文件工具对齐 Claude Code 水准** 📋——read/edit 的 offset 从 0-based 改 1-based（breaking，需适配 skill）+ edit 新增 replace_all 批量替换。开发计划已调整：Phase 3 升级为「文件工具对齐+grep+落盘闭环」优先实施。2026-07-06 | [总体设计](tools/tool-overall-optimization-design.md) / [落盘闭环+grep](tools/large-content-retrieval-design.md) / [文件工具对齐](tools/file-tools-claude-code-parity-design.md) | [开发计划](tools/tool-overall-optimization-dev-plan.md) / [落盘闭环计划](tools/large-content-retrieval-dev-plan.md) |
| 35 | skill_complete 工具彻底删除 | 🔧 部分完成 | `skill_complete` 原始设计目标（标记 skill 完成 + 压缩纯文本 skill 中间上下文）实际未达成：压缩只发生在进程内存未触达 DB，下次会话从 DB 重建即恢复原状；压缩实现本身还引入上下文污染（system role summary、序列打乱、use_skill 的 assistant+tool_result 被错误归入「skill 前」永久保留）。决策彻底删除工具及全部依赖（SkillSession 数据类、_active_skill_sessions 字典、_compress_skill_context、has_active_skill_session、_LIFECYCLE_TOOLS allowed_tools 围栏），并在主循环 + 子智能体执行器两处加静默兜底（应对 DB 历史残留和模型记忆触发的 LLM 调用）。远程 7ebc3b9 已先做了 prompt 层临时止血（去掉「调用 skill_complete 标记完成」指示），本次是彻底清理。**开发完成（2026-07-14）**：T1-T7 全部代码/测试/文档清理完成，进入测试 + CR 流程。2026-07-14 | [设计](system/skill-complete-removal-design.md) | [开发计划](plans/plan-skill-complete-removal.md) |

## 渠道集成

> 2026-07-09 补充：企业微信个人账号 RPA 服务端会话存档已修复 SDK 解密挂起、`DecryptData` 明文字段映射和本地 Windows poller 污染线上状态问题。agent2 真机验证已能成功拉取/解密到授权层；当前未入库原因是首次会话绑定 `pending`，需人工确认后再验证 agent 入库链路。
> 2026-07-10 补充：RPA 出站协议升级至 v1.2，`ActionEnvelope.reply_context` 明确携带发送人、原始入站文本、Agent 回复和桌面端会话搜索名；客户端使用去除末尾 `@微信` 的搜索名定位窗口，缺少可靠搜索名时拒绝盲发。服务端补齐已鉴权 config→client→租户命名空间 account 映射、outbox 上下文持久化和真实 `send_ok`，前端支持维护会话搜索名。WS 配置标识回归已修复：新客户端统一使用 `chan_*` 业务 ID，服务端兼容历史数字 ID，并严格按 tenant/channel/client 归属解析。v1.2 将数据库 outbox 设为唯一权威源，新增 HMAC 鉴权 `GET /api/v1/channels/wecom-personal-rpa/outbox`，连接建立仅发送无正文的 `outbox_available` 提醒；滚动升级期间，新动作先入库后仍可向当前 worker 的在线连接兼容直推完整信封，跨 worker/失败由轮询兜底。状态仍为 🔧 部分完成，待客户端轮询实现及 agent2 真机验证。
> 2026-07-10 补充（客户端 outbox 轮询已实现）：客户端侧可靠轮询落地——新增 `IAgentApiClient.GetOutboxAsync`（镜像 `/config`，静态渠道路径 + HMAC + limit clamp 1-100）；新增 `OutboxPoller`（IHostedService，启动即拉 + 周期拉 + WS 重连/`outbox_available` 即拉，SemaphoreSlim 重入保护，失败指数退避 1/2/4/8/16/30s、成功采纳服务端 `poll_interval_seconds` clamp [2,60]，每 ~50 次成功清理 7 天终态行）；`OutboundQueue.MarkDoneAsync` 由 DELETE 改为保留 `status='done'+completed_at`（at-least-once 下避免重拉重发，并支持终态重报），新增 `GetByActionIdAsync`/`PruneTerminalAsync`（旧库幂等 `ALTER ADD COLUMN completed_at` 迁移）；`OutboundActionDispatcher.EnvelopeEnqueueAsync` 对重复信封按本地终态重报回执（done→success / failed→带 stored code）；`ServerMessageDispatcher` 加 `case outbox_available`；抽 `IReconnectSource` 便于测试。日志只出 request_id/数量/状态（不打印 inbound_text/agent_reply_text）。Client.Tests 130 例全绿（新增 ~30 例）。真机：客户端已启动并每周期拉 `/outbox`（当前服务端端点未部署，404 退避中），WS 已连。待 agent2 部署服务端 `/outbox` 后做完整端到端联调。开发计划见 `~/.claude/plans/`（会话内）。
> 2026-07-12 补充：客户端发送动作的搜索框定位已从窗口相对坐标点击改为前台激活企微主窗口后连续按两次 Alt 聚焦；激活失败时中止键盘输入，避免误发到其他应用。状态仍为 🔧 部分完成。
> 2026-07-12 补充：修复客户端构建产物缺失 PowerShell 自动化脚本的问题，Debug/Release build 与 publish 均复制完整 `scripts` 目录；运行时相对脚本路径固定基于 `AppContext.BaseDirectory` 解析，不再受启动工作目录影响。状态仍为 🔧 部分完成。
> 2026-07-12 补充：增强后台 RPA 的企微窗口激活：PowerShell 临时关联当前、前台及企微窗口输入线程，执行置顶/激活/前台切换，并在 `finally` 中可靠解绑；保留三次重试、前台句柄校验和双 Alt 期间的失败安全中止。状态仍为 🔧 部分完成，待真机端到端验证。
> 2026-07-13 补充：增加 Agent 出站附件到 `send_image/send_file` 的映射，客户端综合协议文件名、Content-Disposition、URL 与 Content-Type 安全下载并限制大小，最终清理临时文件；图片使用 `SetImage`，普通文件使用 `SetFileDropList` 粘贴发送。入站会话存档的 `sdkfileid` 下载仍为独立链路，不与 Agent 出站 URL 混用。状态仍为 🔧 部分完成，待真机附件发送验证。
> 2026-07-13 补充：客户端同一 `ActionEnvelope` 的多个发送动作复用当前企微会话，仅首个实际成功的 `send_text/send_image/send_file` 搜索联系人；后续动作通过默认关闭的内部 PowerShell 参数跳过搜索，但发送前仍校验企微主窗口处于前台。复用状态不持久化、不跨 envelope，崩溃恢复后的首个待执行动作仍重新搜索。状态仍为 🔧 部分完成，待真机文本+附件连续发送验证。
> 2026-07-12 补充：修复服务端会话存档慢 Agent 阻塞游标。fetcher 解密后先写 PostgreSQL `wecom_rpa_archive_inbox`（租户+event 唯一去重），可靠入队后立即推进 seq；Agent 由独立 worker 处理，失败进入 retryable，进程重启后可回收超时 running；inbox 投递失败不推进游标，坏密文保持审计后推进。状态仍为 🔧 部分完成，待独立测试/CodeReview及服务器部署验证。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 27 | wecom_kf 多媒体消息支持 | 🔧 部分完成 | 后端完成：download_media 方法 + channel_routes 消息处理（附件下载/持久化/传递给 agent）+ external_customers 附件下载代理接口。前端完成：ExternalCustomerService 语音播放器/图片预览/文件卡片 + AttachmentCard 组件。待集成测试。2026-06-14 | — | — |
| 29 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | **服务端**（已完成）：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/secret_crypto 七模块按 protocol.md §B 签名实现，68 单元测试通过；callback/config/files/ws 路由接入 main.py；6 条集成测试全部通过；管理前端 P1.1 已交付；指标+告警 P1.4 部分交付（83 测试通过）；平台后台绑定管理 Tab + 「+ 新增绑定」按钮已交付。**服务端拉取会话存档模式**（2026-07-03 完成 Phase 1-11，**2026-07-08 真机验证修复 2 个关键 bug**）：放弃客户端轮询为主方案，改为服务端直接拉取企微会话存档（回调 + 拉取双模式）。复用 `tenant_channel_configs` 表 + `ChannelConfig.vue` 前端，新增 `listen_mode` 字段（第一期强制 'server'，client 模式前端禁用、代码保留）。archive 模块含：credential_codec（5 字段 Fernet 加密 + 强制 server 防绕过）/ callback_crypto（企微签名 + AES 解密，复用 wecom.crypto.WeComCrypto + hmac.compare_digest）/ chat_crypto（**RSA-PKCS1v15** + AES-256-CBC，commit 5791d36 修正：早期"OAEP-SHA1 与 C# 对齐"是错的，企微官方文档要求 PKCS1，真机密文验证 3 条全 PKCS1 通过、OAEP 全失败）/ http_client（access_token Redis 缓存 + 45009 异常 + 3 次重试）/ fetcher（拉取密文 + 解密 + 构造 envelope + 复用 _process_inbound_message，Redis 分布式锁防多 worker 并发）/ poller（60s 兜底轮询，main.py lifespan 启停）/ callback_handler（GET echostr + POST 事件双验签兼容，按 body 格式 XML/JSON 天然分流）/ verifier（5 步链路自测 + 5 类错误诊断）/ audit（6 类事件接入 fetcher/callback_handler）。**SDK 调用子进程隔离**（commit c5ee158）：gunicorn worker 加载 cryptography/psycopg 后加载 .so 破坏 C 堆导致 worker 静默退出，ctypes 实现整体移到 `_sdk_inner.py`（子进程内 import），主进程走 multiprocessing.Pool(spawn, size=1)。`_process_inbound_message` 加 source 参数区分来源。**测试**：214+ 单元/集成测试通过，archive 模块覆盖率 91%。**真机验证进度**（详见 memory project_wecom-personal-rpa-server-archive-verification）：verify 接口 ✅；fetcher 拉密文 ✅；PKCS1v15 解密 ✅（脚本验证）；**待 agent2 部署 2026-07-09 SDK 超时修复后验证消息入库 + 6.2 漏抓率测试**。**客户端方向已重定（2026-06-26）**：放弃 Qwen3-VL 视觉定位 + FlaUI/UIA3 + Windows OCR 三条路径（均已真机验证失败）；放弃把 PS 重写到 C#。新方向：① C# 客户端通过 Process+JSON 调用 PowerShell 脚本作为自动化后端（复用 `debug-navigate.ps1` 真机验证能力）；② 服务端拉取会话存档（已上线，见上）；③ Fallback 监听方案当前所有候选（Windows 通知/视觉/OCR）都不够稳，**先占位不开发**；④ 未登录二维码 30 秒一次截取 + base64 直推服务端 Redis（30s TTL，不入审计/DB）；⑤ 绑定级监控白名单（DB `monitor_user_names`/`monitor_user_ids` + 协议下发 + 客户端缓存 + 服务端二次校验）。**已删过时文档**：vision-design / vision-breakthrough / vision-plan / enter-fix-design / enter-fix-plan / ps-automation-design / ps-automation-plan（均已永久废弃，不再保留误导后人）。**新待办（2026-07-08 用户决策）**：**客户端入站消息功能完全删除**——企微回调要监听域名，客户端没公网域名收不到回调，没有回调就不知道何时拉消息，所以客户端不该做拉取+解密，全部由服务端负责。删除范围：① 9 个 `.cs` 文件（`Client.App/MessageArchive/` 整目录 + `Client.App/Inbound/` 整目录：ChatArchiveListener/ArchiveHttpClient/ArchiveCryptoService/ArchiveMediaDownloader/ArchiveSeqStore/InboundEventBuilder/InboundEventReporter/WeComRateLimitException/MonitorUsersCache）；② 6 个测试（对应 Client.Tests 下两个目录）；③ 7 个文件需修改（App.xaml.cs DI 注册移除、IMessageWatcher.cs 删、RpaConfigResponse 移除 listen_mode 字段、AgentApiClient/IAgentApiClient 移除 PostInboundEvent、PauseState 移除引用、Realtime 评估影响）；④ 服务端 `/api/rpa/inbound` 路由评估是否标 deprecated；⑤ 文档同步。涉及 22 个文件，走完整三智能体流程。**C# 端 OAEP-SHA1 bug 随删除自动消失**（commit 5791d36 修 Python 时发现，C# 端同错但即将被删）。**遗留**：① 立即做：agent2 部署 + 6.2 漏抓率测试；② 后续做：客户端入站功能全删 | [协议](system/wecom-personal-rpa-protocol.md) / [架构设计](system/wecom-personal-rpa-design.md) / [客户端设计](system/wecom-personal-rpa-client-design.md) / [绑定管理 Tab 设计](system/wecom-personal-rpa-portal-binding-design.md) / [服务端监听存档设计](system/wecom-personal-rpa-server-archive-listener-design.md) / [SDK 部署](system/wecom-personal-rpa-sdk-deploy.md) | [服务端+部署计划](../plans/plan-wecom-personal-rpa.md) / [客户端计划](../plans/plan-wecom-personal-rpa-client.md) / [绑定管理计划](../plans/plan-wecom-personal-rpa-portal-binding.md) / [服务端监听存档计划](../plans/plan-wecom-personal-rpa-server-archive-listener.md) |
| 29a | RPA 连续消息合并与无效 Trace 治理 | 🔧 部分完成 | 2026-07-13 Phase 0-3 代码与 Phase 4 自动化验证完成，三智能体开发/测试/Code Review 通过。已落地稳定 session key 与 legacy 原地迁移、Redis 原子 finalizing/ownership lease、文本及附件 merge/pending、Trace 显式终止语义、监控页中间过程折叠。主控相关测试 69 passed，前端 build 与关键 import 通过。待部署环境真实 Redis Lua 验证及三组企微真机验收；已知 P2 为撤回带附件的 merge segment 暂不能精准裁剪附件。 | [方案](channel/wecom-personal-rpa-message-merge-and-trace-plan.md) / [关联设计](channel/concurrent-message-serialization-plan.md) | [开发计划](channel/wecom-personal-rpa-message-merge-and-trace-dev-plan.md) |
| 29b | RPA 自消息循环与错发防护 | 🔧 部分完成 | 2026-07-13 已完成安全关键自动化代码：Phase 0 增加方向、过滤、危险目标、客户端原子队列/中止测试；Phase 1 完成账号权威 `wecom_user_id/aliases` 兼容迁移、租户隔离 identity API、archive self/unknown 入 inbox 前过滤、稳定 peer/room envelope、Agent 前二次防线及出站 target=self/身份缺失拒绝；Phase 1B 完成正常 self echo 与最近 completed outbox 的 target+120 秒+逐 action HMAC 组件摘要关联，支持拆分文本、单/多附件和缺少 `agent_reply_text`，并完成危险 self echo 五分钟滑动窗口、三次幂等暂停及恢复清窗；Phase 2 完成 SQLite envelope 主表+action 明细幂等迁移、事务入队、FIFO 连续执行、失败中止回执、真实执行时间 `send_started_at` 上报落库、Windows Session 命名 Mutex，以及共享 SQLite 的 Session 所有权/可续租租约和过期恢复保护。自动化测试通过，尚需真实 PostgreSQL 迁移及真机双开、跨 Session、重启、文本+多附件验收。按当前开发安排 Phase 3 未动：不开发管理 UI/专用监控展示/历史治理脚本，不修改真实事故数据；`waynelu` pending 临时止损保持。 | [优化方案](channel/wecom-personal-rpa-self-message-loop-and-safe-send-plan.md) | [技术实现与开发计划](channel/wecom-personal-rpa-self-message-loop-and-safe-send-dev-plan.md) |
| 29c | RPA 客户端 EXE 单一交付 | ✅ 已完成开发 | 2026-07-13 按运维决策永久下线安装包交付：删除安装包工程、构建/安装脚本及专属指南，只保留 `dotnet build -c Release` 本机编译和 `scripts/publish.ps1` 自包含 EXE 目录发布；同步 README、状态、操作手册、设计与计划，并新增静态防回归检查。独立测试 154 passed，Release build/publish 通过，Code Review 通过。 | — | — |
| 30 | 钉钉渠道接入 | 🔧 部分完成 | 钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。代码（adapter/crypto/media/message_builder/路由/前端配置）已完成，112 单元测试 + 23 集成测试全部通过；待真实钉钉环境联调。2026-06-19 | [设计](channel/dingtalk/integration_guide.md) / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 31 | 飞书渠道接入代码审核 | 🔧 部分完成 | 基于 [integration_guide.md](channel/feishu/integration_guide.md) 审核飞书渠道实现。发现 3 个 P0 问题（adapter 每请求新建导致 token 缓存/速率限制/HTTP 连接池失效 + httpx client 不关闭造成 fd 泄漏；encrypt_key 文档与代码必填规则不一致）、4 个 P1 安全问题（签名比较未用 hmac.compare_digest、时间戳未做偏移校验、GET 回调无鉴权、去重 DB 异常静默丢消息）、4 个 P2 问题（速率限制需迁 Redis、媒体存储违反租户隔离、asyncio.create_task 无引用、bot open_id 失败静默丢群聊消息）。2026-06-19 | — | [审核报告](../plans/feishu-channel-code-review.md) |

## 前端

> 2026-07-15：Agent Desktop 增加可交付 API 配置。构建必须显式传入 API 基址并写入受控包内资源；首次启动原子初始化 `%APPDATA%\aid-agent-desktop\desktop-config.json`，升级保留且可编辑；`AID_AGENT_API_BASE_URL` 仅作为最高优先级运维覆盖，非法或缺失配置 fail-loud。详见 [Windows 编译与打包手册](system/desktop-agent-client-build-manual.md)。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 33 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend/frontend-office-preview-design.md) | — |
| 36 | Agent 跨平台桌面客户端 | 🔧 部分完成 | 2026-07-14 Phase 0～3 Windows 完成；Phase 5 Windows unsigned 开发安装包也已通过三智能体与主控终检。标准链路生成 x64 user-scope NSIS、ASAR/Portal/凭证门禁、CycloneDX/SHA-256/audit/license/manifest；packaged exe smoke PASS 且零残留。当前包为 0.0.1 development-unsigned（100,215,084 bytes，SHA-256 `20555b...37979`），不可对外正式分发；正式图标、证书签名、真实更新源、安装/协议/升级回滚仍待完成。浏览器 runtime Phase 4 继续等待 Executor 契约，不能阻塞 Agent 主应用；macOS 延后到 Mac 设备逐 Phase 验证。新增 [Windows 编译与打包手册](system/desktop-agent-client-build-manual.md)，明确 agent2 API 运行期配置、服务端 CORS、开发包及正式签名包流程；2026-07-15 补充 Node.js 22、Windows 测试枚举说明和 `scripts/build-win-dev.ps1` 一键开发包构建入口。 | [设计](system/desktop-agent-client-design.md) | [开发计划](system/desktop-agent-client-dev-plan.md) |

---

## 技术栈优化

> 在"功能不变、推倒重来"前提下，对前后端技术栈的系统性优化建议。当前状态均为 💡 灵感阶段。

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 38 | 后台定时/轮询任务外置 | 💡 灵感 | 用 `arq`（基于 Redis 的异步任务队列）替代 `main.py` lifespan 中 `asyncio.create_task` 启动的 6 个后台循环，解决 Gunicorn 多 worker 重复执行问题。约 7-8 人天。 | [设计](tech-stack-optimization/background-tasks-externalization.md) | — |
| 40 | 后台管理界面引入 Element Plus | 💡 灵感 | 后台管理 16 个页面（表格/表单密集型）从手写 TailwindCSS 迁到 Element Plus 组件库（`el-table`、`el-form`、`el-dialog` 等），聊天主界面保持不变。搭配按需导入，约 8-9 人天（含 toast 替换）。 | [设计](tech-stack-optimization/admin-element-plus-migration.md) | — |
| 41 | toast 迁至 Element Plus ElMessage | 💡 灵感 | 替换停更的 `vue-toastification@rc`，使用 Element Plus 的 `ElMessage.success/error/info/warning`，零额外依赖增量。涉及 21 个文件约 182 处调用 + 1 处测试 mock。约 1 人天。 | [设计](tech-stack-optimization/toast-migration.md) | — |

---

## 调研报告索引

以下调研报告为多项功能设计的前期研究，不单独对应开发任务：

| 调研主题 | 文档 | 关联功能 |
|---------|------|---------|
| 企业级智能体平台调研 | [enterprise-agent-platform-research.md](research/enterprise-agent-platform-research.md) | 可观测性、Prompt 管理、知识库 |
| 基础设施差距分析 | [enterprise-agent-infrastructure-gap-analysis.md](research/enterprise-agent-infrastructure-gap-analysis.md) | 整体规划 |
| Prompt 版本管理调研 | [prompt-version-management-research.md](research/prompt-version-management-research.md) | Prompt 全生命周期管理 |
| AI Agent 可观测性调研 | [observability-design-research.md](research/observability-design-research.md) | 可观测性与质量保障 |
| 知识库 RAG 技术调研 | [enterprise-knowledge-base-rag-research.md](research/enterprise-knowledge-base-rag-research.md) | 知识库能力增强 |
| Text-to-SQL 调研 | [text-to-sql-data-analysis-agent-research.md](research/text-to-sql-data-analysis-agent-research.md) | 数据分析智能体 |
| 企微客服 AI 绑定调研 | [wecom-kf-ai-chatbot-binding-research.md](research/wecom-kf-ai-chatbot-binding-research.md) | 企业微信客服 AI 绑定 |
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](research/context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| 前端样式调研 | [frontend-style-research.md](research/frontend/frontend-style-research.md) | 前端样式统一 |
| 前端 Office 预览调研 | [frontend-office-preview-research.md](research/frontend/frontend-office-preview-research.md) | 前端 Office 预览 |
| HTML 转 PPTX 技术调研 | [html-to-pptx-conversion-research.md](research/html-to-pptx-conversion-research.md) | PPT 技能 PPTX 导出 |
| 企业微信个人账号 RPA 生产级客户端技术方案调研 | [wecom-personal-rpa-client-implementation-research.md](research/wecom-personal-rpa-client-implementation-research.md) | 企业微信个人账号 RPA 接入 |
| 会话内上下文压缩业界方案调研 | [context_compression_research.md](research/context_compression_research.md) | 会话内上下文压缩（中期记忆） |
| 社媒内容运营智能体与聚合平台可行性调研 | [social-media-operations-agent-platform-research.md](research/social-media-operations-agent-platform-research.md) | 社媒内容运营智能体与聚合平台 |
| 懂车帝与汽车之家客户留资统一接入可行性调研 | [automotive-platform-lead-integration-research.md](research/automotive-platform-lead-integration-research.md) | CRM 智能体、汽车平台渠道集成 |
