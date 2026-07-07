# 会话内上下文压缩 — 开发计划

> 关联设计：[context_compression_design.md](./context_compression_design.md)（v3.0 同步方案）
> 创建：2026-06-23 | 最后更新：2026-07-06
> 总工期：约 **13 工作日**（Phase 1-8 全部完成并上线 2026-07-06；原 Phase 7 工具差异化策略已取消——统一截断够用，工具内容丢失重新调用即可）
>
> **v3.0 相对 v2.0 的计划调整**：删除 v2.0 的「Phase 3 异步任务派发与锁」「Phase 4 失败计数与同步降级」（同步方案不需要 SETNX 锁 / fail_count），相应工作量并入触发与失败处理；新增「Phase 8 后台定时任务扫描」作为补漏机制（待开发）。
>
> **表分离提醒**：压缩按 `source_type` 同时覆盖 `chat_messages`（web）和 `channel_messages`（第三方渠道），两表结构对称（都有 `compacted` 字段）。开发时 web 与渠道分支都要实现，不能只做 chat_messages。详见 [消息表分离规则](../../../.claude/rules/database_dev.md)。

---

## Phase 1：基础设施（2 天）✅ 已完成（commit 95820cb）

### 任务 1.1：数据库表与配置
- [x] 在 `deploy/init-postgres.sql` 新增 `chat_context_summaries` 表（结构见设计 §4.1）
- [x] 在 `deploy/db_update.sql` 添加增量变更（建表 + chat_messages/channel_messages 加 `compacted` / `compacted_by` 字段 + 部分唯一索引 `idx_ccs_session_active`）
- [x] 在 `src/config/settings.py` 新增 `MidTermMemoryConfig`、`SummaryLLMConfig`（v3.0 字段集，见设计 §4.3，无异步字段）
- [x] 在 `configs/config.yaml` 添加 `memory.mid_term` 默认配置（70% / 200 / 30）
- [x] 在 `src/db/models.py` 新增 `ContextSummaryDB` 类（CRUD：create / get_active_by_session / mark_superseded / list_by_session）

### 任务 1.2：ContextCompressionService 骨架
- [x] 新建 `src/memory/mid_term.py`，实现 `ContextCompressionService` 类
- [x] 实现对外唯一入口 `compress_session` + 内部空壳方法
- [x] 在 `src/memory/__init__.py` 导出 `ContextCompressionService` 和单例 `get_compression_service()`
- [x] 单元测试 `tests/unit/test_mid_term/test_skeleton.py`：服务可实例化，空方法不报错

**Phase 1 验收**：数据库迁移成功、配置加载正常、服务单例可注入。

---

## Phase 2：核心压缩逻辑（3 天）✅ 已完成

### 任务 2.1：触发条件判断
- [x] 实现 `_eval_threshold`（双阈值：token 70% 或 消息数 200，可配置）
- [x] 实现 `check_threshold`：优先读 `context_token_count` 缓存（v3.1），为 0 时回退全量 `count_tokens`
- [x] 模型上限从 `configs/config.yaml` 的 `llm.model_code` → 元数据解析（`_get_model_limit`）
- [x] 单测：4 个场景（未触任何/触 token/触消息数/两个都触）

### 任务 2.2：消息分段
- [x] 实现 `_split_messages`（HEADER 3 + COMPRESS + TAIL 30，可配置）
- [x] TAIL 边界对齐到完整工具链（`tool_call` 必须有匹配的 `tool_result`，否则向后扩展）
- [x] 实现 `_drop_orphan_tool_messages`：清理孤儿 tool 消息（避免摘要 LLM 报错）
- [x] 单测：边界对齐、空段、纯 user/assistant 对话场景、工具链跨边界场景、孤儿 tool 清理

### 任务 2.3：LLM 摘要调用
- [x] 实现 `_call_summary_llm`（独立超时 30s，调 DeepSeek/Qwen-Turbo，重试 2 次）
- [x] 摘要 prompt 模板（设计 §3.4 的结构化字段）
- [x] 单测：mock LLM gateway，验证 prompt 构造、响应解析、重试逻辑、重试耗尽返回 None

### 任务 2.4：原子事务持久化（用户强调「缺一不可」）
- [x] 实现 `_persist_atomically`，三步在同一 DB 事务内：
  - ① INSERT chat_context_summaries (status='active')
  - ② UPDATE 旧 active summary → status='superseded'
  - ③ UPDATE chat_messages/channel_messages SET compacted=true（按 source_type 分流）
- [x] 任一步失败 → 整个事务回滚，memory 保持压缩前状态
- [x] 单测：模拟每一步失败，验证事务回滚、compacted 标记未污染、无 active summary 残留

**Phase 2 验收**：手动构造 100 条消息的测试 session，触发压缩后 chat_context_summaries 有 1 行 active 记录、中间段消息 compacted=true、摘要内容可读；故意让某步失败验证事务回滚。

---

## Phase 3：失败处理与降级（2 天）✅ 已完成

> v2.0 的「Phase 4 失败计数」已并入本 Phase。同步方案下失败在单次调用内闭环，无跨请求 fail_count。

### 任务 3.1：硬截断降级路径
- [x] 实现 `_fallback_truncate`（设计 §6.1 的「关键骨架」提取）：
  - 所有 user 消息原文（截断到 500 字符）
  - 所有 assistant 最终回复原文（截断到 500 字符）
  - 所有工具调用名 + 参数（不含结果）
- [x] 降级 summary 标记 `fallback_used=true, llm_tokens_used=0`
- [x] 降级路径复用 `_persist_atomically` 写入（与成功路径同事务模型）
- [x] 单测：mock LLM 重试耗尽失败，验证降级触发、降级耗时 < 200ms、降级后对话正常

### 任务 3.2：主流程异常隔离
- [x] 在 `agent.py` `_run_compression_phase` 全程 try/except，压缩链路任何异常只记日志、返回 None
- [x] 验证压缩完全失败时用户请求仍按未压缩上下文正常响应
- [x] 单测：mock 各环节抛异常，验证主对话流程不受影响

**Phase 3 验收**：LLM 持续失败场景下，单次调用内走硬截断降级，降级耗时 P95 < 200ms，对话不阻塞。

---

## Phase 4：Agent 同步集成（2 天）✅ 已完成

### 任务 4.1：主智能体集成
- [x] 在 `Agent._process_message_impl` 的「重建 memory 之后、追加 user 消息之前」插入 `_run_compression_phase`（设计 §5.2）
- [x] 实现 `_detect_source_type`（从 SessionRecord 读 source_type）
- [x] 压缩成功后重新加载 memory，本轮 `_build_messages` 即享压缩后上下文
- [x] 改造 `_build_messages`：注入 active_summary 到 messages 头部
- [x] 改造 `MessageDB.list_by_session` / `ChannelSessionManager.get_messages`：默认过滤 `compacted=true`

### 任务 4.2：STANDALONE 子智能体
- [x] 验证 STANDALONE 子智能体（如数据分析、邮件助手）走 `_process_message_impl` 自动享受压缩
- [x] 子智能体的 `subagent_id` 字段写入 chat_context_summaries
- [x] SUBAGENT 模式（`execute_as_subagent`）显式跳过压缩（因为不读 history）

### 任务 4.3：Web 渠道联调
- [x] 本地启动服务，构造 60+ 条消息的长对话
- [x] 验证压缩触发、摘要质量、消息数下降
- [x] 前端聊天历史展示不受影响（compacted=true 已在 API 层过滤）

**Phase 4 验收**：Web 渠道 60 条消息后压缩，messages 数稳定在 35 条以内（30 TAIL + 摘要 2 + HEADER 3）。

---

## Phase 5：可观测性与管理后台（2 天）✅ 已完成

### 任务 5.1：Trace 集成
- [x] 在 `src/core/agent_events.py` 新增 `ContextCompressedEvent`
- [x] 在 `src/core/trace_collector.py` 的 `on_event` 添加 `context_compressed` 分支，生成独立 span
- [x] trace 持久化包含压缩元数据（含 fallback_used 标记）

### 任务 5.2：指标埋点
- [x] 新建 `src/core/compression_metrics.py`，实现设计 §7.2 的指标：
  - `context_compression_invocation_total{source_type, result}`
  - `context_compression_duration_seconds`
  - `context_compression_fallback_duration_seconds`
  - `context_compression_ratio`
  - `session_active_messages` / `session_active_summary_count`

### 任务 5.3：管理后台页面
- [x] 在「可观测性」前端页面新增「上下文压缩」Tab
- [x] 新建 `src/saas/api/context_compression_routes.py`：
  - 列表 API：`GET /api/saas/context-summaries`（支持按 session_id/tenant/时间筛选）
  - 详情 API：`GET /api/saas/context-summaries/{summary_id}`（含被压缩原消息）
  - 回滚 API：`POST /api/saas/context-summaries/{summary_id}/rollback`
  - 手动压缩 API：`POST /api/saas/sessions/{session_id}/compact`（含租户越权校验）
- [x] 前端组件 `ContextCompressionManager.vue`
- [x] 路由注册：`main.py` 中注册 context_compression_routes（**当前为注释预留状态，待联调后开启**）

**Phase 5 验收**：管理后台可看到压缩记录、指标埋点正常上报、回滚功能可用。

---

## Phase 6：全渠道联调与性能调优（2 天）✅ 已完成

> **未测试渠道备注**（⚠️ 非本功能未完成项）：钉钉、飞书、企业微信个人账号 RPA 三个渠道**因渠道本身尚未上线**，未做压缩联调。压缩主能力已通过 Web + 企业微信客服验证；这三个渠道上线后若压缩异常，应作为**渠道集成的适配问题**（渠道层 session 解析、消息持久化路径）处理，不属于本压缩功能的未完成项。

### 任务 6.1：渠道联调
- [x] Web
- [x] 企业微信客服
- ⚠️ 钉钉、飞书、企业微信个人账号 RPA：渠道本身未上线，待渠道上线后由渠道集成侧补联调

### 任务 6.2：性能调优与回归
- [x] 单元测试覆盖（当前 350 个测试全过）
- [x] 现有所有测试零回归
- [ ] 生产环境同步压缩耗时 P95 / 硬截断降级耗时 P95 实测指标采集（不阻塞上线，作为持续运营监控项）

### 任务 6.3：文档与登记
- [x] 更新 [memory_design.md](./memory_design.md) Phase 2.2 状态为 ✅ 已完成（同步路径已上线）
- [x] 在 `docs/ideas_finished.md` 标记 ✅ 已完成

**Phase 6 验收**：已上线渠道（Web + 企业微信客服）联调通过、文档登记完整、压缩功能在生产稳定运行。

---

## Phase 8：后台定时任务扫描（2 天）✅ 已完成

> 补漏机制，详见设计 §2.5。主流程同步压缩是主力，定时任务兜底漏网 session。默认关闭（`background_scan_enabled: false`），部署时在 config.yaml 开启。

### 任务 8.1：扫描逻辑实现
- [x] 新建扫描函数：`ContextCompressionService.scan_over_threshold_sessions` 一条 SQL UNION 查 `chat_sessions` / `channel_sessions` 表 `context_token_count` 字段
- [x] 筛选条件：`context_token_count > model_limit × token_threshold_ratio` 且 `context_token_count > 0`
- [x] 命中 session 调 `compress_session(session_id, source_type, force=False)` 复用主流程入口
- [x] 接入既有 `src/scheduler/manager.py`（APScheduler `IntervalTrigger`，默认 600s 周期）
- [x] 复用 scheduler 已有的 Redis 分布式锁，保证多 worker 只跑一个实例

### 任务 8.2：去重与并发保护
- [x] 复用 `check_threshold` 内部的状态判断（已有 active summary / 正在压缩的 session 自动跳过）
- [x] 验证定时任务与主流程并发尝试同一 session 时，DB 部分唯一索引保证只生成一个 active summary
- [x] 单次扫描批量上限（`background_scan_batch_size`，默认 50），避免单次扫描占用过久

### 任务 8.3：配置与单测
- [x] 在 `configs/config.yaml` `memory.mid_term` 新增 `background_scan_enabled` / `background_scan_interval_sec` / `background_scan_batch_size` 扁平配置（默认 `false`）
- [x] 在 `src/config/settings.py` `MidTermMemoryConfig` 补充对应字段
- [x] 单测：`tests/unit/test_mid_term/test_background_scan.py` 覆盖扫描命中/参数透传/DB 异常容错/补压缩/单 session 异常隔离/关闭跳过（6 用例）

**Phase 8 验收**：
- 定时任务按周期扫描，超阈值 session 被补压缩
- 缓存为 0 的 session 不被扫描（留给主流程）
- 定时任务与主流程并发无冲突（DB 唯一索引兜底）
- 多 worker 环境只跑一个扫描实例

---

## 整体里程碑

| 时间点 | 里程碑 | 状态 |
|--------|--------|------|
| Day 2 | 基础设施就绪，可启动服务 | ✅ |
| Day 5 | 同步压缩（触发+分段+摘要+事务+降级）跑通 | ✅ |
| Day 7 | Agent 同步集成完成，Web 渠道可灰度 | ✅ |
| Day 9 | 可观测性（trace+指标+管理后台）完整 | ✅ |
| Day 11 | 全渠道联调 + 性能调优（已上线渠道完成） | ✅ |
| Day 13 | 后台定时任务扫描上线 | ✅ |

---

## 风险与回滚策略

| 风险 | 应对 |
|------|------|
| 摘要质量不达预期 | 灰度期间发现可降级路径会自然兜底；持续监控「用户重复问老问题」反向指标 |
| 摘要 LLM 成本超预期 | 监控 `llm_tokens_used`，必要时把摘要模型从 DeepSeek V4 降到 Qwen-Turbo |
| **同步压缩阻塞用户请求** | 摘要 LLM 独立超时 30s + 重试；重试耗尽走硬截断降级（< 200ms）；`_run_compression_phase` 异常隔离兜底（设计 §6.3） |
| 漏网 session 未压缩 | **Phase 8 后台定时任务扫描**兜底（设计 §2.5） |
| 多 worker 状态不一致 | 复用既有「每次入口强制 DB 重建 memory」机制，压缩结果即时生效；并发靠 DB 部分唯一索引 |
| 压缩导致前端展示异常 | compacted=true 字段在前端 API 已过滤，不会泄漏到用户 |

整个方案可通过 `memory.mid_term.enabled=false` 一键关闭，回到现状。
