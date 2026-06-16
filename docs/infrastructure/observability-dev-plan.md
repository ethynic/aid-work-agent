# 可观测性与质量保障 — 开发计划

> 对应设计文档：[observability-design.md](./observability-design.md)
> 对应调研报告：[observability-design-research.md](../research/observability-design-research.md)
> 对应差距分析：[enterprise-agent-infrastructure-gap-analysis.md](../research/enterprise-agent-infrastructure-gap-analysis.md) §2.1
> 前置重构：[async-generator-migration-dev-plan.md](./async-generator-migration-dev-plan.md)（已完成 — 事件流已结构化）
> 创建日期：2026-05-29
> 更新日期：2026-06-01（Phase 1 调整为"采集+查看事实数据"，含前端追踪查看页面）
> 状态：待开发

---

## 前置条件

1. AsyncGenerator 迁移（commit `51c41f4`）已完成，`process_message()` 返回 `AsyncGenerator[dict, None]`，事件流已结构化。
2. **追踪库独立部署**：使用 `LOGS_DATABASE_URL` 环境变量连接独立的 PG + TimescaleDB 实例（数据库名 `aid-work-logs2`），与业务库 `DATABASE_URL` 分离。
3. **TimescaleDB 扩展**：追踪库需启用 `timescaledb` 扩展，支持 Hypertable 自动分区、压缩、过期。

---

## Phase 1：追踪采集 + 事实数据查看（预计 2 周）

> 设计文档参考：§二（数据模型）、§三（分布式链路追踪）、§六（追踪查看前端）
> 目标：采集每次请求的追踪数据，通过浏览器可查看（替代 SSH 翻 JSONL 日志）
> 核心改动：后端 2 个新文件 + main.py 约 5 行 + 后端 API + 前端 3 个页面

### 阶段 1.1：追踪库基础设施

> 前置依赖：无

- [x] **1.1.1 创建追踪数据库并启用 TimescaleDB**
  - 在测试服务器（`124.222.3.254:5433`）创建数据库 `aid_work_logs2`
  - 执行 `CREATE EXTENSION IF NOT EXISTS timescaledb;`
  - 在 `.env` 和 `.env.example` 中添加 `LOGS_DATABASE_URL` 环境变量
  - 开发环境：同一 PG 实例不同数据库；生产环境：独立 CVM 实例
  - [x] 已完成 — `deploy/init-postgres-logs.sql` 含 TimescaleDB 容错初始化

- [x] **1.1.2 新增追踪库连接池**
  - 在 `src/db/database.py` 中新增：
    - `LOGS_DATABASE_URL` 环境变量解析
    - `init_logs_pool()` — 初始化追踪库连接池
    - `get_logs_connection()` — 获取追踪库连接（context manager）
  - 复用现有 `ThreadedConnectionPool` 机制，独立连接池参数（`LOGS_DB_POOL_MIN` / `LOGS_DB_POOL_MAX`）
  - 在 `src/main.py` 的 `lifespan` 中调用 `init_logs_pool()` + `init_logs_tables()`
  - 追踪库连接失败时 warning 但不阻止服务启动（降级为不记录追踪）
  - [x] 已完成

- [x] **1.1.3 创建 obs_traces 表（TimescaleDB Hypertable）**
  - 在 `deploy/init-postgres-logs.sql`（新文件，追踪库专用初始化脚本）中添加建表语句
  - [x] 已完成

- [x] **1.1.4 创建 obs_spans 表（TimescaleDB Hypertable）**
  - 在 `deploy/init-postgres-logs.sql` 中添加建表语句
  - [x] 已完成

- [x] **1.1.5 创建 obs_scores 表（TimescaleDB Hypertable）**
  - 在 `deploy/init-postgres-logs.sql` 中添加建表语句
  - [x] 已完成

### 阶段 1.2：TraceCollector + TracePersist

> 前置依赖：1.1

- [x] **1.2.1 实现 TraceCollector（事件流旁路收集器）**
  - 新建 `src/core/trace_collector.py`
  - [x] 已完成

- [x] **1.2.2 实现 trace_persist（异步持久化到追踪库）**
  - 新建 `src/core/trace_persist.py`
  - [x] 已完成

### 阶段 1.3：SSE handler 集成

> 前置依赖：1.2
> 关键文件：`src/main.py` event_generator()
> 改动量：约 5-8 行新增代码，零行删除

- [x] **1.3.1 在 event_generator() 中集成 TraceCollector**
  - 在 `event_generator()` 开头创建 `TraceCollector` 实例
  - 在 `async for event` 循环内，转发 SSE 后调用 `trace_collector.on_event(event)`
  - 在 `CancelledError` 处理中调用 `trace_collector.on_event({"type": "cancelled"})`
  - 在 `Exception` 处理中调用 `trace_collector.on_error(str(e))`
  - 在 finally 区域调用 `trace_collector.on_complete(record_service)`
  - [x] 已完成

- [ ] **1.3.2 在非 SSE 路径中集成追踪（process_message_sync 路径）**
  - ⚠️ **本项已由方案 C 接管**：改为「在 `Agent.process_message()` 内部接入 TraceCollector，从 record_service 自动读取上下文，所有渠道零改造覆盖」
  - 详见：[observability-channel-sessions-dev-plan.md](./observability-channel-sessions-dev-plan.md) 阶段 A
  - 原方案（调用方旁路收集）已废弃，理由见 [observability-channel-sessions-design.md](./observability-channel-sessions-design.md) §五

- [x] **1.3.3 在 agent.py 中 yield `llm_call` 事件**
  - 在 `process_message()` 主循环和 `execute_as_subagent()` 两个 LLM 调用点后，各 yield 一个 `llm_call` 事件
  - 事件携带：messages（完整输入上下文）、tools schema、response content、usage、duration、request_id
  - [x] 已完成

- [x] **1.3.4 TraceCollector 支持 `llm_call` 事件收集**
  - 在 `on_event()` 中增加 `llm_call` 事件处理
  - 创建 `span_type=generation` 的 SpanRecord
  - **只保留最后一次** LLM 调用的完整 messages（覆盖策略）
  - [x] 已完成

- [x] **1.3.5 trace_persist 支持 generation span 写入**
  - `_do_persist()` 中将 generation span 写入 `obs_spans` 表，含 model、prompt_tokens、completion_tokens
  - [x] 已完成

- [x] **1.3.6 前端 TraceDetail 展示最后一次 LLM 上下文**
  - 在 Trace 详情页增加"最后一次 LLM 调用上下文"区域
  - 用 JsonViewer 展示完整的 messages 数组
  - [x] 已完成

### 阶段 1.4：后端追踪查看 API

> 前置依赖：1.1

- [x] **1.4.1 新建 src/api/monitor.py — 追踪查看 API**
  - `GET /api/monitor/sessions` — 有追踪数据的会话列表（分页，支持 status/time_range/tenant_id 筛选）
  - `GET /api/monitor/sessions/{session_id}/traces` — 某会话下所有 trace（按 created_at 正序）
  - `GET /api/monitor/traces` — 全局 trace 列表（分页，支持 status/tags/time_range 筛选）
  - `GET /api/monitor/traces/{trace_id}` — 追踪详情（含完整 span 列表，按 start_time 排序）
  - 使用 `get_logs_connection()` 连接追踪库
  - 需要 tenant_id 隔离（复用 `_resolve_admin_tenant`）
  - [x] 已完成

- [x] **1.4.2 注册 monitor 路由到 main.py**
  - 在 `src/main.py` 中 include `monitor_router`
  - 路由前缀 `/api/monitor`
  - [x] 已完成

### 阶段 1.5：前端追踪查看页面

> 前置依赖：1.4
> 前端规范参考：`.claude/rules/frontend_dev.md`、`.claude/rules/page_patterns.md`
> 设计文档参考：§六（追踪查看前端）

- [x] **1.5.1 新建 frontend/src/api/monitor.ts — API 层**
  - `getTracedSessions(params)` — 获取有追踪数据的会话列表
  - `getSessionTraces(sessionId)` — 获取某会话下的所有 trace
  - `getTraces(params)` — 全局 trace 列表（分页+筛选）
  - `getTraceDetail(traceId)` — 获取 trace 详情（含 spans）
  - [x] 已完成

- [x] **1.5.2 新建 JsonViewer.vue — JSON 格式化查看器组件**
  - 路径：`frontend/src/components/ui/JsonViewer.vue`（通用 UI 组件）
  - 递归组件 `JsonNode.vue`，默认折叠，逐级展开，语法高亮（语义 token 颜色）
  - [x] 已完成

- [x] **1.5.3 新建 TraceBrowser.vue — 会话追踪列表页**
  - 路径：`frontend/src/components/saas/TraceBrowser.vue`
  - 路由：`/portal/monitoring`
  - [x] 已完成

- [x] **1.5.4 新建 SessionTraces.vue — 会话内 Trace 列表页**
  - 路径：`frontend/src/components/saas/SessionTraces.vue`
  - 路由：`/portal/monitoring/:session_id`
  - [x] 已完成

- [x] **1.5.5 新建 TraceDetail.vue — Trace 详情页**
  - 路径：`frontend/src/components/saas/TraceDetail.vue`
  - 路由：`/portal/monitoring/trace/:trace_id`
  - [x] 已完成

- [x] **1.5.6 路由和菜单注册**
  - 在 `frontend/src/main.ts` 的 `/portal` children 中添加 3 个路由
  - 在 `PortalLayout.vue` 的 `portalMenuItems` 中添加 `{ path: '/portal/monitoring', label: '追踪查看', icon: '🔍' }`
  - [x] 已完成

### 阶段 1.6：验证与测试

> 前置依赖：1.2 ~ 1.5

- [ ] **1.6.1 编写 trace_collector 单元测试**
  - 测试完整的 `on_event` 事件序列：tool_start → tool_result → response → on_complete
  - 测试同名工具多次调用（_tool_name_counter）
  - 测试错误和取消路径
  - 测试 output 完整拼接
  - 位置：`tests/unit/test_trace_collector.py`
  - [ ] 未开始

- [ ] **1.6.2 编写 trace_persist 单元测试**
  - Mock `get_connection()`，验证 SQL 写入正确性
  - 测试队列满时的行为
  - 位置：`tests/unit/test_trace_persist.py`
  - [ ] 未开始

- [ ] **1.6.3 端到端验证**
  - 启动服务，通过 SSE 发送聊天请求
  - 检查 `obs_traces` 和 `obs_spans` 表是否有数据写入
  - 通过 `/api/monitor/sessions` 和 `/api/monitor/traces` API 查询验证数据正确性
  - 验证 span 数据（工具调用、耗时、成功/失败）是否正确
  - [ ] 未开始

- [ ] **1.6.4 前端构建验证**
  - `cd frontend && npm run build` 确保无编译错误
  - 打开追踪查看页面，验证会话列表、Trace 列表、Trace 详情三级浏览正常
  - 验证 Span 展开查看完整 input/output（替代 JSONL 日志的关键能力）
  - [ ] 未开始

### 阶段 1.7：JSONL 日志迁移（双写验证期）

> 前置依赖：1.3
> 设计文档参考：§十（JSONL 日志迁移计划）
> 目标：验证全链路追踪数据可以完整替代 JSONL 文件日志

- [ ] **1.7.1 确保 obs_spans 中 LLM 调用数据完整保存**
  - 在 `trace_collector.py` 的 `on_complete()` 中，除了从 `SessionRecordService` 获取汇总 token 数据外，还需要为每次 LLM 调用创建 `span_type=generation` 的 span
  - LLM 调用的 input/output 必须**不截断**完整保存（对齐现有 `llm_invoke_logs` 的行为）
  - 验证方法：对比同一请求的 `llm_invoke_logs` 和 `obs_spans` 数据是否一致
  - [ ] 未开始

- [ ] **1.7.2 双写期间数据对比验证**
  - 对比 `obs_traces.total_tokens` vs `agent_session_logs` 的 `usage` 汇总
  - 对比 `obs_spans` 工具调用数 vs `agent_session_logs` 的 `tool_calls_count`
  - 对比 `obs_spans(name=generation)` 的 LLM span vs `llm_invoke_logs` 的调用记录数
  - 编写对比验证脚本（`scripts/verify_obs_vs_jsonl.py`）
  - [ ] 未开始

- [ ] **1.7.3 添加 JSONL 日志开关**
  - 新增环境变量 `OBS_DISABLE_JSONL_LOGGING`（默认 `false`）
  - 在 `llm_call_logger.py` 和 `agent_logger.py` 入口处检查此开关
  - 为 `true` 时跳过 JSONL 文件写入，仅保留 obs 追踪
  - [ ] 未开始

### Phase 1 完成标准

- [ ] SSE 对话后 `obs_traces` 有记录（含完整的 input、output、status、duration_ms）
- [ ] 工具调用后 `obs_spans` 有记录（含 name、duration_ms、success）
- [ ] LLM 调用后 `obs_spans` 有 generation span（含完整的 request/response，不截断）
- [ ] `/api/monitor/sessions` API 可查询有追踪数据的会话列表
- [ ] `/api/monitor/sessions/{id}/traces` API 可查询某会话的所有 trace
- [ ] `/api/monitor/traces/{id}` API 可查询 trace 详情（含完整 span 树）
- [ ] 前端 `/portal/monitoring` 可浏览会话列表 → Trace 列表 → Trace 详情
- [ ] 前端 Trace 详情中可展开查看 LLM 完整 request/response 和工具调用参数/结果
- [ ] `JsonViewer` 组件支持默认折叠、逐级展开、语法高亮、字符串截断
- [ ] agent.py 未做任何修改
- [ ] 主流程延迟增加 < 1ms（on_event 为同步方法）
- [ ] 双写期间 obs 数据与 JSONL 数据对比一致
- [ ] `OBS_DISABLE_JSONL_LOGGING` 开关可用

---

## Phase 2：质量评估 + 幻觉检测（预计 2 周）

> 设计文档参考：§四（LLM 响应质量评估）、§五（幻觉检测）
> 目标：自动评估每次回复的质量，检测幻觉

### 阶段 2.1：LLM-as-Judge 质量评估

> 前置依赖：Phase 1（需要 obs_scores 表）

- [ ] **2.1.1 实现评分 Prompt 模板**
  - 新建 `src/core/quality_prompts.py`
  - 定义 4 个评估维度的 Prompt 模板常量：`FAITHFULNESS_PROMPT`, `ANSWER_RELEVANCE_PROMPT`, `COMPLETENESS_PROMPT`, `CONCISENESS_PROMPT`
  - 每个 Prompt 包含：评估维度、1-5 分 rubric 评分规则、输出格式要求（JSON）
  - [ ] 未开始

- [ ] **2.1.2 实现 QualityEvaluator**
  - 新建 `src/core/quality_evaluator.py`
  - `__init__(llm_gateway, sample_rate=0.1, judge_model="qwen-turbo")`
  - `evaluate_if_needed(question, context, answer, trace_id)` — 根据采样率决定是否评估
  - `_run_judge(metric, question, context, answer)` — 调用 LLM 执行评估
  - `_parse_judge_response(response)` — 解析 JSON 评分（处理 markdown code block 包裹）
  - `_persist_score(trace_id, metric, result)` — 写入 `obs_scores` 表
  - [ ] 未开始

- [ ] **2.1.3 实现 SSE handler 中的评估调用**
  - 在 `main.py` 的 `event_generator()` 中，trace 完成后触发评估
  - 仅在有知识库检索结果（tool_result 中包含知识库工具调用）时执行
  - 使用 `asyncio.create_task()` 异步执行，不阻塞 SSE 流
  - 评估失败仅 warning 日志，不影响回复
  - [ ] 未开始

- [ ] **2.1.4 实现新智能体上线全量评估**
  - 在 Redis 中维护新智能体的请求计数器（key: `eval:full:{subagent_id}`）
  - 新智能体前 50 条请求：采样率强制 100%，评估全部 4 个维度
  - 超过 50 条后降级为正常采样率
  - [ ] 未开始

### 阶段 2.2：幻觉检测

> 前置依赖：Phase 1（需要 obs_scores 表）

- [ ] **2.2.1 实现 HallucinationGuard**
  - 新建 `src/core/hallucination_guard.py`
  - 实现 4 层检测：信心评分、忠实度评分、矛盾检测、实体幻觉检测
  - `inspect(question, context_chunks, answer) -> dict`
  - 返回：confidence, faithfulness, contradictions, hallucinated_entities, is_hallucinating
  - 纯同步方法（无 async），不依赖外部 API，< 50ms
  - [ ] 未开始

- [ ] **2.2.2 集成到追踪持久化旁路**
  - 在 `trace_persist._do_persist()` 完成后，异步触发幻觉检测
  - 从 `obs_spans` 中提取 `tool_result` 的知识库检索结果作为 context
  - 检测结果写入 `obs_scores`（source="auto"）
  - 检测到幻觉时添加 tag 到 trace（通过 UPDATE obs_traces SET tags = ...）
  - 不修改 agent.py，不修改 SSE handler
  - [ ] 未开始

- [ ] **2.2.3 编写单元测试**
  - 测试各层检测器的独立行为
  - 测试综合判定逻辑（faithfulness < 0.50 / contradictions > 0 / entities > 0）
  - 测试无上下文时的安全降级
  - 位置：`tests/unit/test_hallucination_guard.py`
  - [ ] 未开始

- [ ] **2.2.4 编写 QualityEvaluator 单元测试**
  - Mock LLM Gateway，测试 Prompt 构造和响应解析
  - 测试采样率逻辑
  - 测试 JSON 解析的各种边界情况（markdown 包裹、无效 JSON、score 越界）
  - 位置：`tests/unit/test_quality_evaluator.py`
  - [ ] 未开始

### Phase 2 完成标准

- [ ] `obs_scores` 表中有 LLM Judge 评分数据
- [ ] 幻觉检测结果写入 `obs_scores`
- [ ] 新智能体前 50 条请求全量评估
- [ ] 采样评估不影响主流程延迟

---

## Phase 3：实时监控仪表盘（预计 2 周）

> 设计文档参考：§六.4（后续扩展）
> 目标：运维实时监控系统运行状态（概览卡片 + 趋势图表 + WebSocket 实时推送）
> 注意：追踪查看页面已在 Phase 1 完成，本 Phase 新增仪表盘功能

### 阶段 3.1：后端 WebSocket 推送 + 指标 API

> 前置依赖：Phase 1 + Phase 2

- [ ] **3.1.1 实现概览指标 API**
  - `GET /api/monitor/overview` — 概览指标（最近 1h 请求总数、成功率、P95 延迟、Token 总量）
  - `GET /api/monitor/tokens` — Token 消耗（按模型分布、今日消耗、成本估算）
  - `GET /api/monitor/tools` — 工具调用统计（各工具调用次数、成功率、平均耗时）
  - `GET /api/monitor/quality` — 质量趋势（评分趋势、低分回复比例）
  - 在 `src/api/monitor.py` 中添加
  - [ ] 未开始

- [ ] **3.1.2 实现 WebSocket 监控端点**
  - 在 `src/api/monitor.py` 中添加 `@router.websocket("/ws/monitor")`
  - 5 秒轮询模式：循环采集指标 → `websocket.send_json()` → `asyncio.sleep(5)`
  - `WebSocketDisconnect` 时正常退出
  - 需要认证（复用现有 token 校验）
  - [ ] 未开始

### 阶段 3.2：前端监控仪表盘页

> 前置依赖：3.1

- [ ] **3.2.1 新建 MonitoringDashboard.vue**
  - 路径：`frontend/src/components/saas/MonitoringDashboard.vue`
  - 路由：`/portal/monitoring/dashboard`（在已有 monitoring 子路由下新增）
  - 使用 `AppHeader` 标题 "系统监控"，右上角显示实时连接状态指示灯
  - 四个 BaseCard 概览卡片：请求/分钟、平均延迟、成功率、Token 消耗
  - 使用 BaseTable 组件展示工具调用统计
  - 遵循 `page_patterns.md` 列表页规范
  - [ ] 未开始

- [ ] **3.2.2 实现 WebSocket 连接和实时数据绑定**
  - 建立 WebSocket 连接到 `/ws/monitor`
  - 连接状态管理（连接中/已连接/断开重连）
  - 定时更新概览卡片数据
  - 断线自动重连（指数退避）
  - [ ] 未开始

- [ ] **3.2.3 实现趋势图表**
  - 请求延迟趋势折线图（P50/P95/P99）
  - Token 消耗趋势柱状图（输入/输出/缓存）
  - 质量评分趋势折线图（忠实度/相关性/完整性）
  - 使用 Chart.js 或 ECharts（与前端现有依赖一致）
  - [ ] 未开始

### 阶段 3.3：验证

> 前置依赖：3.2

- [ ] **3.3.1 前端构建验证**
  - `cd frontend && npm run build` 确保无编译错误
  - [ ] 未开始

- [ ] **3.3.2 功能验证**
  - 打开监控仪表盘，验证实时数据更新
  - 验证趋势图表渲染
  - [ ] 未开始

---

## Phase 4：结构化告警（预计 1 周）

> 设计文档参考：§七（结构化告警）
> 目标：异常自动发现和通知

### 阶段 4.1：告警引擎

> 前置依赖：Phase 1 + Phase 2（需要 obs_traces + obs_scores 数据）

- [ ] **4.1.1 实现 AlertRule 和 AlertManager**
  - 新建 `src/core/alert_manager.py`
  - `AlertRule` 数据类：name, metric, operator, threshold, window_minutes, severity, channels, message_template, cooldown_minutes
  - `AlertManager` 类：evaluate(), _check_condition(), _send_alert(), _send_recovery()
  - 冷却机制：同一规则在 cooldown 期内不重复发送
  - 自动恢复：指标恢复正常后发送恢复通知
  - [ ] 未开始

- [ ] **4.1.2 实现默认告警规则集**
  - 6 条默认规则：llm_error_rate_high, request_success_rate_low, llm_latency_high, quality_score_low, hallucination_rate_high, token_usage_spike
  - 规则定义在代码中（`DEFAULT_RULES` 类变量）
  - [ ] 未开始

- [ ] **4.1.3 实现告警评估定时任务**
  - 复用已有的定时任务调度机制
  - 每分钟执行一次：遍历规则 → 查询指标值 → evaluate()
  - 指标值从 `obs_traces` 和 `obs_scores` 聚合查询
  - [ ] 未开始

- [ ] **4.1.4 与已有通知服务集成**
  - 告警发送通过 `src/services/notification_service.py`
  - critical → 企业微信 + 邮件
  - warning → 企业微信
  - [ ] 未开始

### 阶段 4.2：告警管理 API 和 UI

> 前置依赖：4.1

- [ ] **4.2.1 实现告警 REST API**
  - `GET /api/monitor/alerts` — 告警历史列表（分页）
  - `GET /api/monitor/alerts/config` — 获取当前告警规则配置
  - `PUT /api/monitor/alerts/config` — 更新告警规则（阈值、启用/禁用）
  - 在 `src/api/monitor.py` 中添加
  - [ ] 未开始

- [ ] **4.2.2 前端告警列表**
  - 在 MonitoringDashboard 中增加告警标签页或区域
  - 使用 BaseTable 展示告警历史
  - 列：时间、规则名、级别、当前值、阈值、状态
  - 使用 BaseBadge 标记级别（critical=danger, warning=warning）
  - [ ] 未开始

### 阶段 4.3：验证

- [ ] **4.3.1 告警端到端验证**
  - 模拟高错误率场景，验证告警触发和通知发送
  - 验证冷却机制（同一规则在冷却期内不重复发送）
  - 验证恢复通知（指标恢复后发送 [RESOLVED] 通知）
  - [ ] 未开始

- [ ] **4.3.2 前端构建验证**
  - `cd frontend && npm run build` 确保无编译错误
  - [ ] 未开始

---

## 数据生命周期管理

> TimescaleDB 自动管理，不需要额外的清理定时任务。
> - obs_traces / obs_spans：7 天后压缩，90 天后自动过期（`add_retention_policy`）
> - obs_scores：30 天后压缩，180 天后自动过期

---

## 总体进度追踪

| Phase | 内容 | 预计工期 | 状态 |
|-------|------|---------|------|
| Phase 1 | 追踪采集 + 事实数据查看 | 2 周 | 🟡 开发完成，待端到端验证 |
| Phase 2 | 质量评估 + 幻觉检测 | 2 周 | ⬜ 未开始 |
| Phase 3 | 实时监控仪表盘 | 2 周 | ⬜ 未开始 |
| Phase 4 | 结构化告警 | 1 周 | ⬜ 未开始 |

**总工期：6 周**

### 关键里程碑

| 里程碑 | 完成标志 | 对应任务 |
|--------|---------|---------|
| M1: 追踪可用 | 通过 API 查询到完整的 trace + span 数据，agent.py 零修改 | Phase 1 阶段 1.1~1.5 |
| M1.5: JSONL 可替代 | obs 数据与 JSONL 数据对比一致，开关可用 | Phase 1 阶段 1.6 |
| M2: 质量可度量 | obs_scores 表中有 LLM Judge 评分 + 幻觉检测数据 | Phase 2 全部完成 |
| M3: 监控可视化 | 前端仪表盘实时展示指标和追踪详情 | Phase 3 全部完成 |
| M4: 告警自动化 | 异常场景自动触发通知 | Phase 4 全部完成 |

### JSONL 日志淘汰计划

| 阶段 | 时间点 | 动作 |
|------|--------|------|
| 双写期 | Phase 1 上线后 ~2 周 | obs 追踪和 JSONL 同时写入，对比验证数据一致性 |
| 开关期 | M1.5 验证通过后 | `OBS_DISABLE_JSONL_LOGGING=true` 关闭 JSONL，保留代码可回退 |
| 移除期 | 开关期稳定 2 周后 | 移除 Provider 层和 Agent 层的 JSONL 写入调用，保留 `skill_execute` 日志 |

### 风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| SessionRecordService 字段访问方式不确定 | TraceCollector 获取 LLM 数据可能需要适配 | 验证 record_service 的实际属性名，必要时增加公开方法 |
| 持久化线程崩溃 | 追踪数据丢失 | 队列积压时 warning 告警，线程自动重启 |
| 追踪库连接失败 | 无法写入追踪数据 | warning 但不阻止服务启动，追踪库独立不影响业务 |
| 开发环境 TimescaleDB 不可用 | 无法创建 Hypertable | 开发环境可退化为普通 PG 表，跳过 Hypertable/compression/retention |
