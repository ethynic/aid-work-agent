# 会话内上下文压缩 — 开发计划

> 关联设计：[context_compression_design.md](./context_compression_design.md)（v2.0 异步方案）
> 创建：2026-06-23 | 最后更新：2026-06-24
> 总工期：约 **16 工作日**（v2.0 异步方案比 v1.0 同步方案多 3 天：异步任务派发 + 失败计数 + 同步降级）

---

## Phase 1：基础设施（2 天）

### 任务 1.1：数据库表与配置
- [ ] 在 `deploy/init-postgres.sql` 新增 `chat_context_summaries` 表（结构见设计 §4.1）
- [ ] 在 `deploy/db_update.sql` 添加增量变更（建表 + chat_messages/channel_messages 加 `compacted` / `compacted_by` 字段）
- [ ] 在 `src/config/settings.py` 新增 `MidTermMemoryConfig`、`SummaryLLMConfig`（v2.0 字段集，见设计 §4.3）
- [ ] 在 `configs/config.yaml` 添加 `memory.mid_term` 默认配置（70% / 150 / 30 / 3 次失败）
- [ ] 在 `src/db/models.py` 新增 `ContextSummaryDB` 类（CRUD：create / get_active_by_session / mark_superseded / list_by_session）

### 任务 1.2：ContextCompressionService 骨架
- [ ] 新建 `src/memory/mid_term.py`，实现 `ContextCompressionService` 类
- [ ] 实现空壳方法：`check_and_dispatch`、`check_fallback_needed`、`get_active_summary`、`_run_compression_task`
- [ ] 在 `src/memory/__init__.py` 导出 `ContextCompressionService` 和单例 `get_compression_service()`
- [ ] 写单元测试 `tests/unit/test_mid_term/test_skeleton.py`：服务可实例化，空方法不报错

**Phase 1 验收**：
- 数据库迁移成功
- 配置加载正常（启动时不报错）
- 服务单例可注入

---

## Phase 2：核心压缩逻辑（3 天）

### 任务 2.1：触发条件判断
- [ ] 实现 `_should_compress`（双阈值：token 70% 或 消息数 150，可配置）
- [ ] 接入 token 计数（复用 `src/llm/gateway.py` 的 tokenizer 或新增 utils）
- [ ] 模型上限从 `configs/config.yaml` 的 `llm.model_code` → 元数据解析
- [ ] 单测：4 个场景（未触任何/触 token/触消息数/两个都触）

### 任务 2.2：消息分段
- [ ] 实现 `_split_messages`（HEADER 3 + COMPRESS + TAIL 30，可配置）
- [ ] TAIL 边界对齐到完整工具链（`tool_call` 必须有匹配的 `tool_result`，否则向后扩展）
- [ ] 单测：边界对齐、空段、纯 user/assistant 对话场景、工具链跨边界场景

### 任务 2.3：LLM 摘要调用
- [ ] 实现 `_call_summary_llm_with_retry`（独立超时 30s，调 DeepSeek/Qwen-Turbo，重试 2 次）
- [ ] 摘要 prompt 模板（设计 §3.4 的结构化字段）
- [ ] 单测：mock LLM gateway，验证 prompt 构造、响应解析、重试逻辑、重试耗尽返回 None

### 任务 2.4：原子事务持久化（用户强调「缺一不可」）
- [ ] 实现 `_persist_atomically`，三步在同一 DB 事务内：
  - ① INSERT chat_context_summaries (status='active')
  - ② UPDATE 旧 active summary → status='superseded'
  - ③ UPDATE chat_messages SET compacted=true, compacted_by=summary_id WHERE id IN (...)
- [ ] 任一步失败 → 整个事务回滚，memory 保持压缩前状态
- [ ] 单测：模拟每一步失败，验证事务回滚、compacted 标记未污染、无 active summary 残留

**Phase 2 验收**：
- 手动构造 100 条消息的测试 session，触发压缩后：
  - chat_context_summaries 有 1 行 active 记录
  - 中间段消息 compacted=true
  - 摘要内容可读，包含关键事实
- 故意让某步失败，验证事务回滚

---

## Phase 3：异步任务派发与锁（3 天）

### 任务 3.1：Redis SETNX 锁
- [ ] 实现 `_try_acquire_task_lock`（SETNX + TTL 5min）
- [ ] 锁 key：`context_compression:running:{source_type}:{session_id}`
- [ ] 锁值：task_id（uuid，用于识别任务）
- [ ] 单测：抢锁成功/失败场景

### 任务 3.2：异步任务派发
- [ ] 实现 `check_and_dispatch`：主流程入口（非阻塞）
  - 检查阈值 → 未达 → return
  - 检查锁 → 已占 → 静默跳过（debug 日志，不报错）
  - 抢锁成功 → `asyncio.create_task(self._run_compression_task(...))` fire-and-forget
- [ ] 任务异常捕获：所有异常写入 fail_count，不让 asyncio 吞掉
- [ ] 单测：mock asyncio.create_task，验证派发逻辑

### 任务 3.3：异步任务主体
- [ ] 实现 `_run_compression_task`：读消息 → 分段 → 工具预处理 → 摘要 LLM → 原子事务 → 释放锁
- [ ] 任何异常都不能让锁泄漏（try/finally 释放）
- [ ] 单测：mock 整个链路，验证成功路径 + 各种异常路径

**Phase 3 验收**：
- 构造连续 3 次用户请求都触发阈值的场景
- 验证：第 1 次派发任务，第 2、3 次静默跳过
- 第 1 次任务完成后，下一轮请求才会再次派发

---

## Phase 4：失败计数与同步降级（2 天）

### 任务 4.1：失败计数器
- [ ] 用 Redis 实现 `fail_count`（key: `context_compression:fail_count:{source_type}:{session_id}`）
- [ ] TTL 1 小时窗口
- [ ] 异步任务失败时 `INCR`，成功时 `DELETE`
- [ ] 实现 `check_fallback_needed`：读取 fail_count，返回是否 ≥ 3

### 任务 4.2：同步降级路径
- [ ] 实现 `_fallback_truncate`（设计 §6.2 的「关键骨架」提取）：
  - 所有 user 消息原文（截断到 500 字符）
  - 所有 assistant 最终回复原文（截断到 500 字符）
  - 所有工具调用名 + 参数（不含结果）
- [ ] 实现 `_sync_fallback`：主流程触发的同步降级（含原子事务）
- [ ] 降级 summary 标记 `fallback_used=true, llm_tokens_used=0`
- [ ] 降级成功后清空 fail_count
- [ ] 单测：模拟 LLM 连续失败 3 次，第 4 次请求验证同步降级触发

**Phase 4 验收**：
- mock LLM 持续失败，连续 3 个异步任务都失败后，第 4 次主流程请求触发了同步降级
- 同步降级耗时 P95 < 200ms
- 降级后对话正常继续

---

## Phase 5：工具结果策略（2 天）

### 任务 5.1：工具类型注解
- [ ] 在 `src/tools/base.py` 的 `BaseTool` 新增 `compression_strategy` 属性（默认 `summarize`）
- [ ] 给大输出工具打标：`file_read` / `search_documents` / `export_excel` / `pandas_analyze` → `truncate`
- [ ] 给状态类工具打标：`email_list` / `customer_search` / `list_orders` → `summarize`（默认）
- [ ] 给指令类工具打标：`create_plan` / `use_skill` / `clarify` → `keep_args`
- [ ] 给写入类工具打标：`send_email` / `word_process` / `text_file_writer` → `drop_result`

### 任务 5.2：工具结果预处理
- [ ] 实现 `_preprocess_tool_results`（按 strategy 处理 COMPRESS 区内所有 tool 消息）
- [ ] truncate：> 2000 字符截断 + 「[原文已存档，可重新调用工具获取]」后缀
- [ ] keep_args：保留 tool_call 的 function+arguments，丢弃 result content
- [ ] drop_result：tool 消息整条从 COMPRESS 区移除（不进摘要 LLM）
- [ ] 单测：每种 strategy 一组场景

**Phase 5 验收**：
- 大输出工具结果（如 10000 行 Excel）能正常进摘要且 token 可控
- 不同工具类型的处理策略符合预期

---

## Phase 6：Agent 集成（2 天）

### 任务 6.1：主智能体集成
- [ ] 在 `Agent._process_message_impl` 的 `agent.py:1759` 与 `agent.py:1886` 之间插入压缩调用（设计 §5.2 代码）
- [ ] 实现 `_detect_source_type`（从 SessionRecord 读 source_type）
- [ ] 改造 `_build_messages`（`agent.py:1125`）：注入 active_summary 到 messages 头部
- [ ] 改造 `MessageDB.list_by_session` / `ChannelSessionManager.get_messages`：默认过滤 `compacted=true`

### 任务 6.2：STANDALONE 子智能体
- [ ] 验证 STANDALONE 子智能体（如数据分析、邮件助手）走 `_process_message_impl` 自动享受压缩
- [ ] 子智能体的 `subagent_id` 字段写入 chat_context_summaries
- [ ] SUBAGENT 模式（`execute_as_subagent`）显式跳过压缩（因为不读 history）

### 任务 6.3：Web 渠道联调
- [ ] 本地启动服务，构造 60+ 条消息的长对话
- [ ] 验证压缩触发、摘要质量、消息数下降
- [ ] 前端聊天历史展示不受影响（前端读 chat_messages，过滤 compacted=true 已在 MessageDB.list_by_session 实现）

### 任务 6.4：第三方渠道联调（至少 1 个）
- [ ] 选企业微信客服（wecom_kf）作为代表联调
- [ ] 验证 channel_messages 表也走同样压缩流程
- [ ] 验证 process_and_persist 事务与压缩时机不冲突

**Phase 6 验收**：
- Web 渠道：60 条消息后压缩，messages 数稳定在 35 条以内（30 TAIL + 摘要 2 + HEADER 3）
- 第三方渠道：同一用户多轮对话，session 不会无限膨胀

---

## Phase 7：可观测性与全渠道联调（2 天）

### 任务 7.1：Trace 集成
- [ ] 在 `src/core/agent_events.py` 新增 `ContextCompressedEvent`
- [ ] 在 `src/core/trace_collector.py:86` 的 `on_event` 添加 `context_compressed` 分支
- [ ] 创建紫色 span（区别于 LLM/Tool span）
- [ ] trace 持久化包含压缩元数据（含 fallback_used 标记）

### 任务 7.2：指标埋点
- [ ] 复用既有可观测性框架（参考 `infrastructure/observability-design.md`）
- [ ] 实现设计 §7.2 的 9 个指标（含异步任务指标）
- [ ] Grafana/Prometheus dashboard 配置（如适用）

### 任务 7.3：管理后台页面
- [ ] 在「可观测性」前端页面新增「上下文压缩」Tab
- [ ] 列表 API：`GET /api/observability/context-summaries`（支持按 session_id/tenant/时间筛选）
- [ ] 详情 API：`GET /api/observability/context-summaries/{summary_id}`（含被压缩原消息）
- [ ] 回滚 API：`POST /api/observability/context-summaries/{summary_id}/rollback`
- [ ] 手动压缩 API：`POST /api/observability/sessions/{session_id}/compact`（运维触发立即压缩）
- [ ] 前端组件：参考现有 trace 列表组件风格

### 任务 7.4：5 渠道联调
- [ ] Web ✅（Phase 6 已完成）
- [ ] 企业微信客服
- [ ] 钉钉
- [ ] 飞书
- [ ] 企业微信个人账号 RPA

### 任务 7.5：性能调优与回归
- [ ] 异步任务耗时 P95 < 30s
- [ ] 同步降级耗时 P95 < 200ms
- [ ] 同一 session 不会出现两次压缩叠加（active summary 唯一性 + SETNX 锁）
- [ ] 单元测试覆盖 ≥ 90%
- [ ] 现有所有测试零回归

### 任务 7.6：文档与登记
- [ ] 更新 [memory_design.md](./memory_design.md) Phase 2.2 状态为 ✅ 已完成
- [ ] 在 `docs/ideas.md` 更新状态为 ✅ 已完成开发
- [ ] 完成后移动到 `docs/ideas_finished.md`

**Phase 7 验收**：
- 5 渠道全部联调通过
- 性能指标达标
- 文档登记完整

---

## 整体里程碑

| 时间点 | 里程碑 |
|--------|--------|
| Day 2 | 基础设施就绪，可启动服务 |
| Day 5 | 同步路径（触发+分段+摘要+事务）跑通 |
| Day 8 | 异步任务派发 + 锁去重完整 |
| Day 10 | 失败兜底（fail_count + 同步降级）完整 |
| Day 12 | 工具策略 + Agent 集成完成，Web 渠道可灰度 |
| Day 14 | 可观测性完整，运维可见可控 |
| Day 16 | 全渠道上线 |

---

## 风险与回滚策略

| 风险 | 应对 |
|------|------|
| 摘要质量不达预期 | 灰度期间发现可降级路径会自然兜底；持续监控「用户重复问老问题」反向指标 |
| 摘要 LLM 成本超预期 | 监控 `llm_tokens_used`，必要时把摘要模型从 DeepSeek V4 降到 Qwen-Turbo |
| 异步任务撑爆 token | TAIL 30 条 + 阈值 70% 留 30% 缓冲；极端情况同步降级兜底 |
| 多 worker 状态不一致 | 复用既有「每次入口强制 DB 重建 memory」机制，压缩结果即时生效 |
| 压缩导致前端展示异常 | compacted=true 字段在前端 API 已过滤，不会泄漏到用户 |

整个方案可通过 `memory.mid_term.enabled=false` 一键关闭，回到现状。
