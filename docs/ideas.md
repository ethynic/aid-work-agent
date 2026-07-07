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
| 1 | 可观测性与质量保障 | 🔧 部分完成 | 分布式追踪 + LLM 质量评估 + 实时监控 + 结构化告警。Phase 1 完成，Phase 2-4 未开始。2026-05-29。**延伸：追踪页面整合 channel_sessions（含微信客服等所有渠道）— 方案 C：TraceCollector 下沉到 `Agent.process_message` 内部，从 record_service 自动读取 source_type，渠道真正零改造，[设计](infrastructure/observability-channel-sessions-design.md) / [开发计划](infrastructure/observability-channel-sessions-dev-plan.md)** | [设计](infrastructure/observability-design.md) | [计划](infrastructure/observability-dev-plan.md) |
| 2 | Prompt 全生命周期管理 | 🔧 部分完成 | Phase 0+1+2 代码完成。Phase 3 新增回复风格选择器+business_pages配置。2026-06-04 | [设计](infrastructure/prompt-lifecycle-design.md) | [计划](infrastructure/prompt-lifecycle-dev-plan.md) |
| 3 | 租户数据迁移 | 📋 待开发 | 跨数据库租户数据迁移（知识库 + 业务表），UUID 稳定标识符 + replace/merge 模式 + Excel 导出导入。2026-06-08 | [设计](infrastructure/tenant-data-migration.md) | — |
| 9 | Gunicorn 多 Worker 定时任务单 Worker 执行 | 📋 待开发 | Gunicorn 多 worker 部署下，scheduled_tasks 表内的定时任务会被每个 worker 重复触发。方案：`post_fork` hook 约束仅 worker-0 启动调度器，其他 worker 跳过；任务执行层用 Redis 锁兜底防滚动重启期间双跑。时间敏感性低（5-10 分钟延迟可接受）。2026-06-29 | [设计](infrastructure/scheduled-tasks-single-worker-design.md) | — |

## 系统功能

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 5 | 知识库能力增强 | 🔧 部分完成 | Phase 0 知识库分类管理已完成。Phase 1-2 已重排（2026-07-06）：**Phase 1（合规阻塞，2 周）= 文档级权限 + 检索日志**；**Phase 2（销售精度 + 体验，3-4 周）= LLM Rerank + 查询改写 + 质量评估（与 Rerank 配套）+ Pipeline 协调器**。重排理由：原 Phase 1 按实现依赖排序无商业化优先级，现按销售推动力重排——权限是中大型企业上线卡点，质量评估需与 Rerank 配套（否则评估的是基线无意义）。 | [设计](system/knowledge-base/knowledge-base-enhancement-design.md) | [计划](system/knowledge-base/knowledge-base-dev-plan.md) |

## 数字员工 / 子智能体

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 9 | 数据分析智能体 | 🔧 部分完成 | 统一智能分析工具（SmartDataAnalysisTool）：内置 LLM 编排 + pandas/numpy 执行引擎。数据源导入已完成，分析工具待开发。2026-06-05 | [设计](system/digital-employee/data-analysis-subagent-design.md)、[工具设计](system/digital-employee/smart-data-analysis-tool-design.md) | [工具开发计划](system/digital-employee/smart-data-analysis-tool-dev-plan.md) |
| 9a | 数据源导入功能 | 🔧 部分完成 | Phase 1+2 代码完成（后端 API + 前端页面），Phase 3 集成测试待做。2026-06-03 | [设计§三~§五](system/digital-employee/data-analysis-subagent-design.md) | [计划](system/digital-employee/data-source-import-dev-plan.md) |
| 9c | 数据分析工具返回结构优化 | 🔧 部分完成 | P0 完成：工具返回改为 conclusion + artifacts + analysis_meta 三层结构，表格 artifact 带 preview 最多 10 行；AnalysisAgent system prompt 加收尾规范约束 conclusion 必须含产物指代。P1 主智能体 prompt 已由用户配置。2026-06-12 | [设计](system/digital-employee/data-analysis-tool-result-redesign.md) | — |
| 9d | 工具消息持久化（事务性） | 📋 待开发 | 修复工具结果跨 worker 丢失导致主智能体重跑分析的根因。tool 消息与最终回复作为事务一起写入 chat_messages，要么全成功要么全失败。2026-06-12 | [设计](system/digital-employee/tool-messages-persistence-design.md) | — |
| 9b | 聊天附件数据分析 | 🔧 部分完成 | 聊天中发送 Excel/CSV 附件自动注册到知识库并分析。共享 schema_saver 服务 + upload_data_file 工具。2026-06-09 | [设计](system/digital-employee/chat-attachment-data-analysis-design.md) | — |
| 17 | CRM 智能体 | 📋 待开发 | 客户关系管理，客户数据整合与智能跟进建议 | [设计](subagent/crm/crm_subagent_design.md) | — |
| 18 | 业务页面元数据注册表 | 📋 待开发 | 页面元数据注册表 + AI 智能推荐，替代子智能体配置中的手动路由输入。2026-06-10 | [设计](system/digital-employee/page-metadata-registry-design.md) | [开发计划](system/digital-employee/page-metadata-registry-dev-plan.md) |
| 20 | 社媒内容运营智能体与聚合平台 | 🔧 部分完成 | 已完成首期可开发条件评估，并落地核心骨架：OpenSpec change、社媒核心表、连接器协议、账号/计划/内容版本/审核/发布任务/数据概览 API、聚合工作台页面和页面元数据。正式服影响已屏蔽：页面元数据状态为 planned，不进入业务菜单；社媒子智能体定义以 `.disabled` 草案保存，不会被加载器自动加载。真实微信公众号 HTTP 发布、视频号发布包下载/数据导入、调度器和真实账号验收仍待 Phase 5+。2026-06-30 | [调研](research/social-media-operations-agent-platform-research.md) / [设计](system/digital-employee/social-media-operations-agent-design.md) | [开发计划](system/digital-employee/social-media-operations-agent-dev-plan.md) |

## 工具

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 20 | 浏览器操作可视化 | 📋 待开发 | Playwright Screencast API 实时推送浏览器操作画面到前端，支持操作标注。2026-05-21 | [设计](tools/browser/browser_visualization_design.md) | — |
| 21 | 文件工具四件套（read/write/edit/cp）v2 | 🔧 部分完成 | 拆分旧 text_file_writer/file_reader/file_list 为四个原子工具，支持 SKILL.md `<SKILL_ROOT>` 占位符。cp 工具新增 `visible` 参数（默认 False），区分中间过程文件与最终交付，避免前端展示多个中间副本下载卡片。2026-06-12 | [设计 v2](tools/text-file/file_tools_redesign_v2.md) | [开发计划 v2](tools/text-file/file_tools_redesign_v2_dev_plan.md) |
| 25 | 旅游报价价格解析性能优化 | 🔧 部分完成 | 酒店、景点门票/项目、行程解析均已改为 DeepSeek V4 Pro 关闭推理；酒店用候选压缩短 prompt，景点保留 LLM 主路径并新增团队票优先后处理，行程解析补充无项目景点/活动名原样保留自检。规则优先仍待后续评估。2026-06-22 | [设计](system/design-travel-quote-price-parser-performance.md) | — |
| 27 | 酒店价格表六列格式升级 + 房型合并解析 | 🔧 部分完成 | hotel_excel_parser 由五列（房型\|客户类型\|价格\|含早\|适用日期）升级为六列（房型\|散客价\|团客价\|含早\|适用日期\|备注），第一行强制表头；房型列合并床型/面积/景观等属性便于房型匹配；只解析对外价格、丢弃内部结算价、单价格自动复制到散客/团客两列；hotel.py 的 _parse_hotel_price_rows/_select_team_price_by_llm/_extract_first_team_price 同步适配新结构，对外报价走团客价口径。2026-06-23 | [设计](subagent/travel-consultant/hotel_excel_to_kb_design.md) | — |
| 28 | 酒店报价房型自动解析 + 含早写入备注 | 🔧 部分完成 | ① itinerary_parser 从行程文本提取房型写入 hotel_stays[].room_type（默认 ""，hotel_overrides.room_type 优先级更高）；② _select_team_price_by_llm 改为让 LLM 输出"行序号"，函数据此返回 {price, breakfast, room_type} dict，含早/选中房型信息不再丢失；③ prompt 强化"价格-房型配对"原则：明确告诉 LLM 选中的房型会原样展示给客户，必须与团队构成匹配（学生/老师团默认标准间，不因价格高低改选）；④ calculate_hotel_stays/_calculate_hotel_cost_from_kb 的 remark 强制写入"房型=XX"（即使客户未指定房型，也用 LLM 选中行的房型；价格表行房型为空时兜底"标准间"）+ 含早原文（如"含双早"），杜绝"裸价格"；⑤ 新增 evaluate_hotel_breakfast_roomtype_stability.py 跑房型+含早 3 轮稳定性测试，旧评估脚本同步兼容 dict 返回值。代码改动完成，3 轮稳定性测试待在能连 DB 的环境运行。2026-06-29 | — | — |
| 29 | PDF 工具质量验证增强 | 🔧 部分完成 | P0+企业文档增强+Playwright HTML 转 PDF 已完成：新增 inspect/render_pages/validate、结构化检查、生成后自动校验、页码语义统一和依赖探测；修复 split 保存顺序、无效页码静默成功、文件名安全、HTML 表格顺序、测试漂移和旧设计文档不一致问题；新增 clean_metadata/add_watermark/protect/compress/extract_images/rotate；复杂 HTML/CSS 默认优先 Playwright print-to-pdf，失败回退 fpdf2；Docker 增加 LibreOffice Writer 及构建期校验，DOCX 转 PDF 统一使用 LibreOffice 隔离配置目录并保留失败诊断。PDF 全量单测通过，127 passed；真实 Playwright 生成和结构校验通过。2026-07-01 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | [开发计划](tools/pdf/pdf_tool_quality_validation_dev_plan.md) |
| 30 | PDF reportlab 固定版式生成器 | 📋 待开发 | 暂不开发，未来如出现强固定版式需求再评估。适用场景：结构化业务数据直接生成正式报价单、报告、对账单、审批单等，要求页眉页脚、页码、签章区、复杂跨页表格和版式位置稳定。当前复杂 HTML/CSS 已由 Playwright print-to-pdf 覆盖，不优先投入 reportlab。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 31 | 文件生成类工具入参语义拆分与路由健壮性 | 🔧 部分完成 | Word/PDF/Excel/PPT 当前普遍用 `context` 同时承载“用户目的 + 待处理正文 + 隐含参数”，导致内部 LLM 路由不稳定、转换正文被工具指令污染。目标：新增 `instruction/content/content_type/output_name` 结构化入参，`context` 仅作兼容；规则路由覆盖高确定性场景，内部 LLM 只处理复杂参数抽取。Word、PDF、Excel 的计划内改造已完成；PPT 已完成完整 Phase 0-8 增强，包括结构化入参、确定性路由、主/子 Agent 与 Skill 调用提示及回归测试。剩余工作仅为该跨工具计划中的旅游顾问提示更新。2026-07-01 | [设计](tools/tool-input-contract-redesign.md) | [开发计划](tools/tool-input-contract-redesign-dev-plan.md) |
| 32 | PDF 视觉回归样本集 | 💡 灵感 | 低优先级未来项。用于沉淀小型样例 PDF、渲染 PNG 或预期检查结果，后续在改动 PDF 生成器、渲染器、验证器时做回归校验，防止中文乱码、空白页、黑页、页数错误、表格溢出等质量退化。当前已有结构化校验和 Playwright HTML 转 PDF，暂不投入完整样本集建设。2026-06-30 | [设计](tools/pdf/pdf_tool_gap_analysis_design.md) | — |
| 34 | 工具总体优化梳理 | 🔧 部分完成 | 跨工具层面的优化梳理登记文档（区别于单工具设计）。进度：#34a `upload_to_remote` 工具直接删除 ✅（已提交）；#34b 全部 27 个业务工具 token 审查 + 规范 ✅；Phase 0/1（删工具+建规范+helper）✅；Phase 2 试点 http_api/pdf_process/paddleocr token 优化 ✅（token 对比达标，服务器验证通过）；#34c **大内容落盘检索闭环 + grep 工具** 📋——Phase 2 截断后完整内容丢弃形成信息黑洞，新增「截断→落盘临时文件→read/grep 回读」闭环（对齐 Claude Code harness），含 `_spill.py` 落盘管理器 + 新增 grep 工具（调 ripgrep）+ Dockerfile 装 rg；#34d **文件工具对齐 Claude Code 水准** 📋——read/edit 的 offset 从 0-based 改 1-based（breaking，需适配 skill）+ edit 新增 replace_all 批量替换。开发计划已调整：Phase 3 升级为「文件工具对齐+grep+落盘闭环」优先实施。2026-07-06 | [总体设计](tools/tool-overall-optimization-design.md) / [落盘闭环+grep](tools/large-content-retrieval-design.md) / [文件工具对齐](tools/file-tools-claude-code-parity-design.md) | [开发计划](tools/tool-overall-optimization-dev-plan.md) |

## 渠道集成

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 27 | wecom_kf 多媒体消息支持 | 🔧 部分完成 | 后端完成：download_media 方法 + channel_routes 消息处理（附件下载/持久化/传递给 agent）+ external_customers 附件下载代理接口。前端完成：ExternalCustomerService 语音播放器/图片预览/文件卡片 + AttachmentCard 组件。待集成测试。2026-06-14 | — | — |
| 29 | 企业微信个人账号 RPA 接入 | 🔧 部分完成 | **服务端**（已完成）：schemas/db/SQL 表结构锁定共享契约；auth(HMAC)/router/message/action_client/adapter/connection/secret_crypto 七模块按 protocol.md §B 签名实现，68 单元测试通过；callback/config/files/ws 路由接入 main.py；6 条集成测试全部通过；管理前端 P1.1 已交付；指标+告警 P1.4 部分交付（83 测试通过）；平台后台绑定管理 Tab + 「+ 新增绑定」按钮已交付。**服务端拉取会话存档模式**（2026-07-03 完成 Phase 1-11）：放弃客户端轮询为主方案，改为服务端直接拉取企微会话存档（回调 + 拉取双模式）。复用 `tenant_channel_configs` 表 + `ChannelConfig.vue` 前端，新增 `listen_mode` 字段（第一期强制 'server'，client 模式前端禁用、代码保留）。archive 模块含：credential_codec（5 字段 Fernet 加密 + 强制 server 防绕过）/ callback_crypto（企微签名 + AES 解密，复用 wecom.crypto.WeComCrypto + hmac.compare_digest）/ chat_crypto（RSA-OAEP-SHA1 + AES-256-CBC，与 C# 客户端算法对齐）/ http_client（access_token Redis 缓存 + 45009 异常 + 3 次重试）/ fetcher（拉取密文 + 解密 + 构造 envelope + 复用 _process_inbound_message，Redis 分布式锁防多 worker 并发）/ poller（60s 兜底轮询，main.py lifespan 启停）/ callback_handler（GET echostr + POST 事件双验签兼容，按 body 格式 XML/JSON 天然分流）/ verifier（5 步链路自测 + 5 类错误诊断）/ audit（6 类事件接入 fetcher/callback_handler）。`_process_inbound_message` 加 source 参数区分来源。214 个单元/集成测试通过，archive 模块覆盖率 91%。**客户端方向已重定（2026-06-26）**：放弃 Qwen3-VL 视觉定位 + FlaUI/UIA3 + Windows OCR 三条路径（均已真机验证失败）；放弃把 PS 重写到 C#。新方向：① C# 客户端通过 Process+JSON 调用 PowerShell 脚本作为自动化后端（复用 `debug-navigate.ps1` 真机验证能力）；② 服务端拉取会话存档（已上线，见上）；③ Fallback 监听方案当前所有候选（Windows 通知/视觉/OCR）都不够稳，**先占位不开发**；④ 未登录二维码 30 秒一次截取 + base64 直推服务端 Redis（30s TTL，不入审计/DB）；⑤ 绑定级监控白名单（DB `monitor_user_names`/`monitor_user_ids` + 协议下发 + 客户端缓存 + 服务端二次校验）。**已删过时文档**：vision-design / vision-breakthrough / vision-plan / enter-fix-design / enter-fix-plan / ps-automation-design / ps-automation-plan（均已永久废弃，不再保留误导后人）。**遗留**：按客户端 5 阶段计划落地（清理 → PS 工程化+协议扩展 → 出站+存档+二维码 → 入站解析+基础设施 → 真机回归，约 54h/7 工作日） | [协议](system/wecom-personal-rpa-protocol.md) / [架构设计](system/wecom-personal-rpa-design.md) / [客户端设计](system/wecom-personal-rpa-client-design.md) / [绑定管理 Tab 设计](system/wecom-personal-rpa-portal-binding-design.md) / [服务端监听存档设计](system/wecom-personal-rpa-server-archive-listener-design.md) | [服务端+部署计划](../plans/plan-wecom-personal-rpa.md) / [客户端计划](../plans/plan-wecom-personal-rpa-client.md) / [绑定管理计划](../plans/plan-wecom-personal-rpa-portal-binding.md) / [服务端监听存档计划](../plans/plan-wecom-personal-rpa-server-archive-listener.md) |
| 30 | 钉钉渠道接入 | 🔧 部分完成 | 钉钉开放平台企业机器人接入，支持单聊/群聊消息收发、签名验证（HmacSHA256）、媒体文件处理、长消息拆分。代码（adapter/crypto/media/message_builder/路由/前端配置）已完成，112 单元测试 + 23 集成测试全部通过；待真实钉钉环境联调。2026-06-19 | [设计](channel/dingtalk/integration_guide.md) / [接入手册](channel/dingtalk/onboarding_guide.md) | [计划](channel/dingtalk/implementation_plan.md) |
| 31 | 飞书渠道接入代码审核 | 🔧 部分完成 | 基于 [integration_guide.md](channel/feishu/integration_guide.md) 审核飞书渠道实现。发现 3 个 P0 问题（adapter 每请求新建导致 token 缓存/速率限制/HTTP 连接池失效 + httpx client 不关闭造成 fd 泄漏；encrypt_key 文档与代码必填规则不一致）、4 个 P1 安全问题（签名比较未用 hmac.compare_digest、时间戳未做偏移校验、GET 回调无鉴权、去重 DB 异常静默丢消息）、4 个 P2 问题（速率限制需迁 Redis、媒体存储违反租户隔离、asyncio.create_task 无引用、bot open_id 失败静默丢群聊消息）。2026-06-19 | — | [审核报告](../plans/feishu-channel-code-review.md) |
| 34 | chat_records 表 web/channel 隔离 | 📋 待开发 | ChatRecordDB.list_by_session / list_by_user 未按 source_type 过滤，channel 端（wecom_kf/wecom/dingtalk/feishu）的会话记录会被 web 端会话查询页面拉出来造成混淆。需补 source_type 过滤（web 端默认只查 source_type='chat' 或 NULL）。2026-06-23 | — | — |
| 36 | 渠道语音 ASR 补齐（wecom / feishu / dingtalk） | 📋 待开发 | 渠道场景下语音消息必须在渠道层完成 ASR 转文字后送入 agent，LLM 不再承担语音识别职责。已完成：①wecom_kf 渠道层调用阿里云 ASR 转文字（`src/saas/api/channel_routes.py` `_transcribe_voice_with_asr`）；②删除 `master_agent.md` / `subagent_base.md` 中"[语音消息] 无识别结果时回复语音识别失败"的旧规则；③ASR 成功后的前缀由 `[语音消息]` 改为 `[ASR识别结果]`，明确告知 LLM 文本来源。待补：wecom（企业微信应用消息）、feishu、dingtalk 三个渠道的语音消息同样需要接入阿里云 ASR，目前仅占位，未实现。2026-06-26 | — | — |
| 38 | wecom_kf 单轮回复配额管控（提示词注入 + 渲染层兜底） | 🔧 部分完成（Layer 1 实现中） | **背景**：旅游咨询顾问智能体实际使用中，客户一句「发我英文版和日语版」触发智能体单轮产出 7 个发送单元（文字+表格图+Word ×2 语言+中文总结），微信客服 send_msg 5 条/48h 上限被击穿，最后 2 个 Word 文档丢失。md 表格转图片功能受好评需保留，需从机制上限制单轮产出量（否则「翻译 10 国语言」会让 LLM+接口同时爆炸）。**双层防护**：Layer 1 提示词注入（软约束，主）— agent.process_message_sync 新增 extra_system_prompt 参数，wecom_kf 调用方传入渠道能力说明（5 条上限、2048 字节、单轮 ≤4 单元、多语言/多文档分批、拒绝过量请求）；Layer 2 渲染层兜底（硬约束，保底）— adapter.send_message 重构为「收集→截断→暂存 Redis→发送」，超 4 条暂存，末尾发「回复『继续』获取剩余」，入口识别「继续」flush。**进度**：2026-07-01 完成 Layer 1（提示词注入），先观察 LLM 遵守率再决定 Layer 2 阈值。 | — | [开发计划](channel/wecom_kf/reply_quota_control_plan.md) |

## 前端

| # | 功能 | 状态 | 说明 | 设计文档 | 开发计划 |
|---|------|------|------|---------|---------|
| 33 | 前端 Office 预览 | 📋 待开发 | 前端在线预览 Office 文档（Word/Excel/PPT） | [设计](research/frontend/frontend-office-preview-design.md) | — |

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
