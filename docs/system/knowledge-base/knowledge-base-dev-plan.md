# 知识库能力增强开发计划

> 关联设计：[知识库能力增强方案设计文档](./knowledge-base-enhancement-design.md)
> 文档索引：[ideas.md §系统功能 #5](../../ideas.md)
> 创建日期：2026-06-04
> 状态：Phase 0 已完成并上线（详见设计文档 §5.0）；Phase 1-2 待开发

---

## 总览

> Phase 0（知识库分类管理）已上线，**本开发计划仅包含待开发部分**。Phase 0 的设计与实现详情见设计文档 [§5.0](./knowledge-base-enhancement-design.md#50-知识库分类管理phase-0--最高优先级已上线)。

| 阶段 | 内容 | 优先级 | 预估周期 | 状态 |
|------|------|--------|---------|------|
| Phase 1 | 文档级权限 + 检索日志（合规阻塞） | **P0** | 2 周（10 工作日） | 📋 待开发 |
| Phase 2 | Rerank + 查询改写 + 质量评估 + Pipeline（精度 + 体验） | P1 | 3-4 周（15-20 工作日） | 📋 待开发 |

---


## Phase 1：文档级权限 + 检索日志（合规阻塞）

> 设计文档：§3.2 权限、§3.3 检索日志
> **目标**：解锁中大型企业客户上线卡点（文档级 ACL），并配套权限审计所需的检索日志。
> **预估工期**：10 工作日（2 周）

### 任务分解

#### P1-T1：数据库变更（Phase 1 所需）

- [ ] `deploy/init-postgres.sql` 新增：
  - `knowledge_bases` 表（含 `is_default` 字段）
  - `kb_permissions` 表（含 `subject_type` / `subject_id` / `permission` 三级 ACL）
  - `retrieval_logs` 表（检索日志，含 `query` / `rewritten_query` / `top_score` / `latency_ms` / `reranked` / `user_feedback`）
- [ ] `deploy/db_update.sql` 新增：
  - `documents` 表新增 `kb_id INTEGER` 字段（不强制 NOT NULL，向后兼容）
  - 上述 3 张新表的建表语句
- [ ] 迁移策略：为每个租户自动创建"默认知识库"（`is_default=true`，`subject_type='tenant'` 全租户可见），现有文档（`kb_id IS NULL`）回填到默认库

**交付文件**：`deploy/init-postgres.sql`、`deploy/db_update.sql`

**验收标准**：
- 新环境启动后所有表存在
- 已有环境增量迁移无报错
- 现有文档自动关联到租户默认知识库
- 默认知识库对所有租户成员可见（向后兼容）

**预估工期**：1.5 人天

---

#### P1-T2：权限服务（核心）

- [ ] `src/knowledge/permissions.py` — `KnowledgeBasePermission` 类
  - `get_accessible_kb_ids(user_id, tenant_id, departments)` — 返回用户可访问的 KB ID 集合
    - 查询逻辑：`subject_type='tenant' AND subject_id=tenant_id` OR `subject_type='department' AND subject_id IN departments` OR `subject_type='user' AND subject_id=user_id`
  - `get_accessible_doc_ids(user_id, tenant_id, departments, limit=5000)` — 返回用户可访问的文档 ID 集合
    - 通过 `kb_ids` JOIN `documents` 查询
  - **首期不缓存**（直接查库，避免多 Worker 不一致）
- [ ] `kb_permissions` 表的索引：`idx_kb_perm_kb(kb_id)` + `idx_kb_perm_subject(subject_type, subject_id)`（已在 DDL 中）

**交付文件**：`src/knowledge/permissions.py`

**验收标准**：
- 三级 ACL 查询正确：tenant 级、department 级、user 级各自命中
- 用户属于多个部门时取并集
- 单次查询延迟 < 5ms（走索引）
- 无权限配置时返回默认知识库 ID（向后兼容）

**预估工期**：2 人天

---

#### P1-T3：检索层权限过滤（Pre-retrieval Filtering）

- [ ] 修改 `src/knowledge/vector_db/vector_db.py` 的 `search()` 方法
  - 新增 `doc_ids` 参数（`Optional[set[int]]`）
  - SQL 改造：在现有 `WHERE d.tenant_id = %s` 后追加 `AND d.id = ANY(%s)`（PostgreSQL 数组）
  - `doc_ids=None` 时不追加过滤（保持现状）
  - `doc_ids=set()` 时直接返回空结果（用户无权限）
- [ ] 修改 `src/knowledge/retriever/hybrid_retriever.py` 的 `retrieve()` 方法
  - 新增参数：`user_id`、`departments`（替代原 `user_id` 占位参数）
  - 调用 `kb_permission.get_accessible_doc_ids()` 获取 doc_ids
  - 传给向量检索和 FTS 检索（两路都要过滤）
- [ ] 修改 FTS 检索（`_postgres_fts_search`）：同样新增 `doc_ids` 过滤
- [ ] 修改 `src/tools/knowledge/knowledge_base_tool.py`：从 session 上下文获取 `user_id`/`departments` 传入检索

**交付文件**：`src/knowledge/vector_db/vector_db.py`、`src/knowledge/retriever/hybrid_retriever.py`、`src/tools/knowledge/knowledge_base_tool.py`

**验收标准**：
- 用户 A 配置只能看 KB1，检索时返回 KB1 的 chunks，KB2 完全不可见
- `tenant` 级权限：所有租户成员可见
- `department` 级权限：该部门用户可见
- `user` 级权限：仅指定用户可见
- 权限过滤对向量检索和 FTS 检索**同时生效**
- 无权限配置时（`user_id=None`）不过滤，向后兼容
- 5000 文档过滤延迟 < 100ms（PostgreSQL `ANY` 数组走索引）

**预估工期**：2.5 人天

---

#### P1-T4：知识库 CRUD + 权限管理 API

- [ ] `src/knowledge/api.py` 新增端点（参考设计文档 §3.2.3）：
  - `POST /api/knowledge/bases` — 创建知识库
  - `GET /api/knowledge/bases` — 列出知识库（仅当前用户有权限的）
  - `PUT /api/knowledge/bases/{kb_id}` — 更新名称/描述
  - `DELETE /api/knowledge/bases/{kb_id}` — 删除知识库（关联文档移到默认库）
  - `POST /api/knowledge/bases/{kb_id}/permissions` — 设置权限（tenant/department/user）
  - `GET /api/knowledge/bases/{kb_id}/permissions` — 查看权限列表
  - `DELETE /api/knowledge/bases/{kb_id}/permissions/{perm_id}` — 删除单条权限
- [ ] `src/knowledge/service.py` 新增对应 service 方法
- [ ] 上传文档端点扩展：`POST /api/knowledge/upload?kb_id={kb_id}`，不传时归入默认库
- [ ] Pydantic 模型：`CreateKBRequest`、`UpdateKBRequest`、`SetPermissionRequest`、`PermissionResponse`

**交付文件**：`src/knowledge/api.py`、`src/knowledge/service.py`

**验收标准**：
- 端点全部做租户隔离
- 创建知识库后默认 `subject_type='user', subject_id=创建者`（管理员权限）
- 上传到指定 kb_id 的文档继承该 KB 权限
- 删除 KB 时关联文档 `kb_id` 重置到默认库

**预估工期**：2 人天

---

#### P1-T5：检索日志记录

- [ ] `src/knowledge/service.py` 新增 `log_retrieval()` 方法
  - 写入 `retrieval_logs`：`tenant_id`、`session_id`、`query`、`results_count`、`top_score`、`avg_score`、`latency_ms`、`reranked=false`（Phase 1 没有 Rerank）、`user_feedback=NULL`
- [ ] 在 `KnowledgeBaseTool.execute()` 中调用日志记录（**异步 fire-and-forget**，不阻塞返回）
- [ ] `configs/config.yaml` 新增 `knowledge.retrieval_logging` 配置段（`enabled`/`sample_rate`）
- [ ] 用户反馈端点：`POST /api/knowledge/retrieval_logs/{id}/feedback`（接收 `feedback: 'relevant' | 'irrelevant'`）
- [ ] Phase 2 上线 Rerank/查询改写后，再扩展 `rewritten_query` / `rerank_latency_ms` 字段（表结构 Phase 1 已建好）

**交付文件**：`src/knowledge/service.py`、`src/knowledge/api.py`、`configs/config.yaml`

**验收标准**：
- 每次检索写入一条日志（按 `sample_rate` 采样）
- `sample_rate=0.5` 时约一半请求记录日志
- 用户反馈可正确更新到对应日志记录
- 日志写入不影响主流程响应延迟（异步）

**预估工期**：1 人天

---

#### P1-T6：前端 — 知识库管理 + 权限设置

- [ ] `frontend/src/api/knowledge.ts` 新增知识库/权限相关接口
- [ ] `KnowledgeBase.vue` 扩展：
  - 顶部新增"知识库切换"下拉（默认显示"全部"，按 KB 分组）
  - 上传文档时选择目标知识库
- [ ] 权限设置弹窗：
  - 列出当前 KB 的权限条目（subject_type + subject_id + permission）
  - 新增权限（选择主体类型 → 输入主体 ID → 选择权限级别）
  - 删除单条权限
- [ ] 删除知识库时弹窗提示"将删除知识库及其权限配置，关联文档会移到默认库"

**交付文件**：`frontend/src/components/KnowledgeBase.vue`、`frontend/src/api/knowledge.ts`

**验收标准**：
- 知识库 CRUD + 权限设置功能正常
- 上传时正确关联 kb_id
- `npm run build` 通过

**预估工期**：1.5 人天

---

#### P1-T7：集成测试 + 性能测试

- [ ] 权限隔离测试：跨用户、跨部门、跨租户的检索结果隔离
- [ ] 权限过滤性能测试（5000 文档 < 100ms）
- [ ] 默认知识库迁移测试（现有文档归入默认库后仍可检索）
- [ ] 回归测试：现有检索功能不受影响

**交付文件**：测试报告

**验收标准**：
- 所有集成测试通过
- 无跨租户/跨用户数据泄露
- 无回归 bug

**预估工期**：0.5 人天

---

### Phase 1 工期汇总

| 任务 | 人天 | 依赖 |
|------|------|------|
| P1-T1 数据库变更 | 1.5 | 无 |
| P1-T2 权限服务 | 2 | T1 |
| P1-T3 检索层过滤 | 2.5 | T2 |
| P1-T4 KB CRUD + 权限 API | 2 | T2 |
| P1-T5 检索日志 | 1 | T1 |
| P1-T6 前端管理 | 1.5 | T4 |
| P1-T7 测试 | 0.5 | T3, T4, T5, T6 |
| **合计** | **11 人天**（约 2 周） | |

**可并行项**：T5（检索日志）只依赖 T1，可与 T2/T3/T4 并行；T6 前端可在 T4 完成后启动，与 T3/T5 并行。

---

## Phase 2：Rerank + 查询改写 + 质量评估 + Pipeline（销售精度 + 体验）

> 设计文档：§3.1 Rerank、§3.3 质量评估、§3.4 查询改写、§3.5 Pipeline
> **目标**：销售 demo 精度可信；多轮对话连贯；量化指标对比 Rerank 提升幅度。
> **依赖**：Phase 1 完成（权限过滤已在 HybridRetriever 内）
> **预估工期**：15-20 工作日（3-4 周）

### 任务分解

#### P2-T1：评估集建立（先于 Rerank）

- [ ] 内部运营手工标注 50~100 条 `(query, relevant_doc_ids)` 测试用例
  - 优先覆盖销售 demo 场景（公司制度、产品信息、客户案例）
  - 包含简单/中等/困难三类查询
- [ ] 评估集录入 `evaluation_sets` 表（`test_cases JSONB`）
- [ ] 评估集 CRUD API：`POST/GET/DELETE /api/knowledge/evaluation-sets`

**交付文件**：`evaluation_sets` 数据、`src/knowledge/api.py`

**验收标准**：
- 至少 50 条标注用例
- `relevant_doc_ids` 字段非空
- 评估集可被 API 加载

**预估工期**：2 人天（含标注）

---

#### P2-T2：基线评估服务（Rerank 前的对照）

- [ ] `src/knowledge/evaluation/evaluator.py` — `KnowledgeBaseEvaluator` 类
  - `evaluate_retrieval(eval_set_id)` — 跑评估集，对每个 query 调用 `HybridRetriever.retrieve()`，计算：
    - Precision@5
    - Recall@10
    - nDCG@10
    - Hit Rate
  - 评估结果写入 `evaluation_results` 表（`config_snapshot` 记录 `reranker='none'`）
- [ ] 后端 API：`POST /api/knowledge/evaluation-sets/{id}/evaluate` — 手动触发评估
- [ ] **首期不实现**：LLM-as-Judge（Faithfulness/Answer Relevancy）、定时调度

**交付文件**：`src/knowledge/evaluation/`、`src/knowledge/api.py`

**验收标准**：
- 跑评估集得到 4 项指标
- 结果保存到 `evaluation_results`
- 同一评估集重复跑得到一致结果（确定性）

**预估工期**：2 人天

---

#### P2-T3：Rerank 引擎

- [ ] `src/knowledge/reranker/base.py` — `BaseReranker` 抽象基类 + `RerankResult` 数据类
- [ ] `src/knowledge/reranker/llm_reranker.py` — LLM-based Reranker
  - 复用 `src/llm/gateway.py`（qwen/zhipu，按 `LLM_PROVIDER` 配置）
  - `RERANK_PROMPT`：输入 query + top-20 候选，输出 `[{index, score, reason}]` JSON
  - `temperature=0.1`、`max_tokens=2000`
  - JSON 解析失败时降级返回原始顺序前 top_k
- [ ] `src/knowledge/reranker/cross_encoder_reranker.py` — 预留接口（TODO，第三阶段）
- [ ] `src/knowledge/reranker/__init__.py` — 工厂函数 `get_reranker()`，按 `knowledge.rerank.provider` 配置返回实例
- [ ] `configs/config.yaml` 新增 `knowledge.rerank` 配置段（`enabled`/`provider`/`top_k=5`/`max_candidates=20`/`timeout_ms=3000`/`fallback_on_error=true`）

**交付文件**：`src/knowledge/reranker/`、`configs/config.yaml`

**验收标准**：
- 输入 20 条候选 → 输出 top-5 重排
- LLM 调用失败时降级返回原始排序
- 配置 `enabled=false` 时跳过 Rerank
- Rerank 耗时记录到日志

**预估工期**：2.5 人天

---

#### P2-T4：Query Rewriting 查询改写

- [ ] `src/knowledge/retriever/query_rewriter.py` — `QueryRewriter` 类
  - `REWRITE_PROMPT`：对话历史 + 当前查询 → 独立查询
  - `rewrite(current_query, chat_history, max_history_turns=5)` — 共指消解
  - **触发条件**：`chat_history >= 2 条消息` 时触发，否则直接返回原始查询
  - 超时（`max_latency_ms=1000`）/异常时返回原始查询
  - `temperature=0.1`
- [ ] `configs/config.yaml` 新增 `knowledge.query_rewriting` 配置段
- [ ] 集成到 `src/tools/knowledge/knowledge_base_tool.py`：从 session 获取对话历史，调用改写

**交付文件**：`src/knowledge/retriever/query_rewriter.py`、`src/tools/knowledge/knowledge_base_tool.py`

**验收标准**：
- "高铁呢？" + 历史"公司差旅报销标准是什么？" → 改写为"公司差旅报销 高铁标准"
- 无对话历史时直接返回原始查询
- 改写超时/失败时降级到原始查询
- 改写前后 query 记录到检索日志（扩展 `rewritten_query` 字段）

**预估工期**：1.5 人天

---

#### P2-T5：检索 Pipeline 协调器

- [ ] `src/knowledge/retriever/pipeline.py` — `RetrievalPipeline` 类
  - 串联：Query Rewriting → Permission Check → Hybrid Retrieval → Rerank
  - `search(query, user_id, tenant_id, departments, chat_history, top_k, use_rerank, use_rewrite)` 统一入口
  - 异步记录检索日志（含 `rewritten_query`、`reranked`、`rerank_latency_ms`）
- [ ] 替换调用点：`KnowledgeBaseTool`、API 搜索端点改为调用 `Pipeline.search()`
- [ ] 每个步骤可独立开关（`use_rerank`/`use_rewrite`）

**交付文件**：`src/knowledge/retriever/pipeline.py`

**验收标准**：
- Pipeline 串联 4 个步骤
- 单个步骤失败不影响后续（降级策略）
- 检索日志异步写入，不增加主流程延迟
- 现有检索功能不受影响

**预估工期**：2 人天

---

#### P2-T6：Rerank 后评估（对比提升）

- [ ] 用 P2-T1 同一评估集，开启 Rerank 重跑评估
- [ ] `evaluation_results` 表新增记录（`config_snapshot.reranker='llm'`）
- [ ] 对比基线（P2-T2）vs Rerank 后的 Precision@5 / nDCG@10 提升幅度
- [ ] 生成评估对比报告（Markdown，附在交付物中）

**交付文件**：评估对比报告

**验收标准**：
- Rerank 后 nDCG@10 较基线提升 ≥ 0.05（否则评估 Rerank 配置/prompt 是否需调整）
- 评估结果可查询、可对比

**预估工期**：0.5 人天

---

#### P2-T7：前端 — 检索调试工具

- [ ] 在 `KnowledgeBase.vue` 或新页面增加检索调试入口
- [ ] 调试页面：输入 query + 可选 chat_history → 调用调试 API → 展示：
  - 改写后的 query
  - 检索结果（RRF 排序）
  - Rerank 后的结果（含 score 和 reason）
  - 各步耗时
- [ ] 后端调试端点：`POST /api/knowledge/debug/search`（仅管理员可访问）

**交付文件**：调试页面、调试 API

**验收标准**：
- 调试工具可查看完整 Pipeline 各步骤输出
- 调试端点限制管理员访问

**预估工期**：1.5 人天

---

#### P2-T8：集成测试 + 性能测试

- [ ] Pipeline 全链路集成测试（含 Rerank + 改写 + 权限）
- [ ] Rerank 性能测试（p95 延迟 < 400ms，含降级场景）
- [ ] Query Rewriting 测试：覆盖代词消解、跨轮引用、首跳过等场景
- [ ] 回归测试：Phase 1 的权限过滤、检索日志功能不受影响

**交付文件**：测试报告

**验收标准**：
- 所有集成测试通过
- 检索 p95 延迟（含 Rerank）< 400ms
- 无回归 bug

**预估工期**：1 人天

---

### Phase 2 工期汇总

| 任务 | 人天 | 依赖 |
|------|------|------|
| P2-T1 评估集建立 | 2 | 无（可与 T3/T4 并行） |
| P2-T2 基线评估 | 2 | T1 |
| P2-T3 Rerank 引擎 | 2.5 | 无（可与 T1/T2 并行） |
| P2-T4 Query Rewriting | 1.5 | 无（可与 T1-T3 并行） |
| P2-T5 Pipeline 协调器 | 2 | T3, T4 |
| P2-T6 Rerank 后评估 | 0.5 | T2, T3, T5 |
| P2-T7 前端调试工具 | 1.5 | T5 |
| P2-T8 集成/性能测试 | 1 | T5, T6, T7 |
| **合计** | **13 人天**（约 3 周，含并行） | |

**可并行项**：T1（评估集）+ T3（Rerank）+ T4（改写）首周可并行；T2 基线评估等 T1 完成；T5 Pipeline 等 T3+T4 完成。

**关键路径**：T1 → T2 → T6（评估闭环，3-4 工作日）和 T3 → T5（Rerank 闭环，4.5 工作日），两条路径可并行。

---

## 风险与注意事项

| 风险 | 阶段 | 缓解措施 |
|------|------|---------|
| 已有 source_type 未注册到分类表 | P0（已完成） | 迁移脚本自动提取；未注册文档在"全部"中可见 |
| 删除分类后文档孤立 | P0（已完成） | 删除分类不删文档；可重建同名分类恢复 |
| 默认知识库迁移遗漏 | P1 | 迁移脚本扫描 `kb_id IS NULL` 文档归入默认库；幂等可重跑 |
| 多 Worker 权限缓存不一致 | P1 | 首期不缓存（直接查库），后续按需迁移 Redis |
| 权限过滤性能 | P1 | 限制 max 5000 doc；PostgreSQL `ANY` 数组走索引 |
| LLM Rerank 增加延迟 | P2 | `timeout_ms=3000` 超时降级；可选 Selective Rerank（高分跳过） |
| Query Rewriting 失败 | P2 | `fallback_on_error=true` 回退原始 query；记录改写日志 |
| 评估集质量影响 Rerank 提升幅度 | P2 | 标注 ≥ 50 条覆盖销售场景；nDCG 提升 < 0.05 时复盘 prompt/配置 |
| 检索日志写入压力 | P1-P2 | `sample_rate` 采样；定期归档 |
| documents 表 kb_id 迁移 | P1 | 自动创建租户默认知识库，迁移脚本归入默认库 |
