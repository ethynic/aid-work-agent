# 知识库能力增强开发计划

> 关联设计：[知识库能力增强方案设计文档](./knowledge-base-enhancement-design.md)
> 文档索引：[ideas.md §系统功能 #5](../../ideas.md)
> 创建日期：2026-06-04
> 状态：Phase 0 已完成

---

## 总览

| 阶段 | 内容 | 优先级 | 预估周期 | 状态 |
|------|------|--------|---------|------|
| Phase 0 | 知识库分类管理（前端左侧分类导航 + 分类 CRUD） | **最高** | 4 天 | ✅ 已完成 |
| Phase 1 | 核心检索增强（Rerank + 权限 + 查询改写 + Pipeline + 日志） | 高 | 4-5 周 | 📋 待开发 |

---

## Phase 0：知识库分类管理

> 设计文档：§5.0

### 任务分解

#### P0-T1：数据库变更

- [x] `deploy/init-postgres.sql` 新增 `knowledge_categories` 表定义
- [x] `deploy/db_update.sql` 新增建表语句 + 已有 `source_type` 数据迁移脚本
- [x] 验证迁移脚本：确保已有租户的 `file`、`attraction_resource`、`hotel_resource` 等分类自动注册

**交付文件**：`deploy/init-postgres.sql`、`deploy/db_update.sql`

**验收标准**：
- 新环境启动后 `knowledge_categories` 表存在
- 已有租户数据迁移后，`knowledge_categories` 中包含该租户所有已有 `source_type` 的分类记录
- `UNIQUE(tenant_id, source_type)` 约束生效，重复插入不报错（`ON CONFLICT DO NOTHING`）

---

#### P0-T2：后端分类 CRUD API

- [x] `src/knowledge/api.py` 新增 4 个端点：
  - `GET /api/knowledge/categories` — 分类列表（LEFT JOIN 统计文档数）
  - `POST /api/knowledge/categories` — 创建分类（source_type 格式校验 + 唯一性校验）
  - `PUT /api/knowledge/categories/{category_id}` — 重命名 display_name
  - `DELETE /api/knowledge/categories/{category_id}` — 删除分类（仅删记录，不删文档）
- [x] 新增 Pydantic 模型：`CreateCategoryRequest`、`UpdateCategoryRequest`、`CategoryResponse`
- [x] `src/knowledge/service.py` 新增 4 个方法：
  - `list_categories(tenant_id)` — 查分类 + 统计文档数
  - `create_category(tenant_id, source_type, display_name)` — 校验 + 插入
  - `update_category(category_id, tenant_id, display_name)` — 更新名称
  - `delete_category(category_id, tenant_id)` — 删除记录

**交付文件**：`src/knowledge/api.py`、`src/knowledge/service.py`

**验收标准**：
- `GET /categories` 返回分类列表，每项含 `id`、`source_type`、`display_name`、`document_count`
- `POST /categories` 校验 `source_type` 格式 `^[a-z][a-z0-9_-]*$`，重复返回 409
- `PUT /categories/{id}` 只更新 `display_name`，不影响 `source_type`
- `DELETE /categories/{id}` 删除后关联文档不受影响
- 所有端点做租户隔离（`tenant_id` 过滤）

---

#### P0-T3：后端文档列表过滤 + 上传关联分类

- [x] `src/knowledge/api.py` 的 `GET /api/knowledge/documents` 新增 `source_type` 可选查询参数
- [x] `src/knowledge/service.py` 的 `list_documents()` 新增 `source_type` 参数，在 WHERE 中追加过滤条件
- [x] `src/knowledge/api.py` 的 `POST /api/knowledge/upload` 新增 `source_type` 可选表单字段
- [x] `src/knowledge/service.py` 的 `upload_document()` 新增 `source_type` 参数，覆盖默认的 `"file"`
- [x] 批量上传 `POST /api/knowledge/upload/batch` 同步支持 `source_type` 参数

**交付文件**：`src/knowledge/api.py`、`src/knowledge/service.py`

**验收标准**：
- `GET /documents?source_type=file` 只返回 `source_type=file` 的文档
- `GET /documents`（不传 `source_type`）返回全部文档，向后兼容
- `POST /upload` 带 `source_type` 参数时，文档记录使用指定值
- `POST /upload` 不带 `source_type` 参数时，默认 `"file"`，向后兼容

---

#### P0-T4：前端 API 层

- [x] `frontend/src/api/knowledge.ts` 新增分类相关类型和函数：
  - `CategoryResponse` 接口
  - `CreateCategoryRequest`、`UpdateCategoryRequest` 接口
  - `listCategories()`、`createCategory()`、`updateCategory()`、`deleteCategory()` 函数
- [x] `listDocuments()` 函数签名扩展，支持可选 `sourceType` 参数
- [x] `uploadDocument()` / `uploadDocumentsBatch()` 扩展，支持传入 `sourceType`

**交付文件**：`frontend/src/api/knowledge.ts`

**验收标准**：
- TypeScript 编译无报错
- API 函数签名与后端端点一一对应

---

#### P0-T5：前端页面改造（左侧分类导航 + 双栏布局）

- [x] `KnowledgeBase.vue` 改为双栏布局：
  - 左侧固定宽度（~200px）分类导航面板
  - 右侧弹性宽度文档列表区域（复用现有表格逻辑）
- [x] 左侧面板：
  - "全部" 项（显示文档总数）
  - 分类列表（从 `listCategories()` 加载），每项显示 `display_name` + 文档数
  - 选中态高亮（`bg-primary-50 text-primary-700`）
  - 底部"添加分类"按钮
  - 分类项 hover 时显示编辑/删除图标
- [x] 右侧文档列表：
  - 切换分类时调用 `listDocuments(limit, offset, sourceType)` 刷新
  - "全部"时不传 `sourceType`
  - 搜索仍按现有逻辑，搜索范围限定在当前分类（如有选中）
- [x] 添加分类弹窗：
  - 英文代号输入框（正则 `^[a-z][a-z0-9_-]*$` 前端校验）
  - 分类名称输入框
  - 调用 `createCategory()` → 成功后刷新分类列表
  - 后端 409 时前端提示"该代号已存在"
- [x] 重命名弹窗：
  - 显示当前 `source_type`（只读）
  - 编辑 `display_name`
  - 调用 `updateCategory()` → 成功后刷新分类列表
- [x] 删除分类确认：
  - `confirm('删除分类不会删除已上传的文档，确定删除？')`
  - 调用 `deleteCategory()` → 成功后刷新分类列表

**交付文件**：`frontend/src/components/KnowledgeBase.vue`

**验收标准**：
- 左侧分类列表正确显示 `display_name` 和文档计数
- 点击分类切换右侧文档列表
- 添加/重命名/删除分类功能正常
- 上传文档时自动使用当前选中的 `source_type`
- "全部"视图下上传默认 `source_type=file`
- 现有搜索、分页、删除功能不受影响

---

#### P0-T6：联调测试

- [x] 新租户场景：无 `knowledge_categories` 数据 → 创建默认分类 → 上传文档
- [x] 已有租户场景：迁移后分类正确 → 文档列表按分类过滤正确
- [x] 边界场景：
  - 空分类（`document_count=0`）显示正常
  - 删除分类后文档仍在"全部"中
  - `source_type` 含特殊字符被前端拦截
  - 重名 `source_type` 被后端 409 拒绝
- [x] `npm run build` 构建通过

**验收标准**：
- 全流程无阻塞性 bug
- 前端构建无报错

---

### Phase 0 工期排布

| 天 | 任务 | 依赖 |
|----|------|------|
| D1 上午 | P0-T1 数据库变更 | 无 |
| D1 下午 | P0-T2 后端分类 CRUD API | T1 |
| D2 上午 | P0-T3 文档列表过滤 + 上传关联 | T2 |
| D2 下午 | P0-T4 前端 API 层 | T3 |
| D2-D3 | P0-T5 前端页面改造 | T4 |
| D4 | P0-T6 联调测试 | T5 |

---

## Phase 1：核心检索增强

> 设计文档：§3.1 Rerank、§3.2 权限、§3.3 质量评估、§3.4 查询改写、§3.5 Pipeline 协调器

### 任务分解

#### P1-T1：数据库变更（Phase 1 所需）

- [ ] `deploy/init-postgres.sql` 新增：
  - `knowledge_bases` 表（知识库分组管理）
  - `kb_permissions` 表（知识库权限）
  - `retrieval_logs` 表（检索日志）
  - `evaluation_sets` 表（评估数据集）
  - `evaluation_results` 表（评估结果）
- [ ] `deploy/db_update.sql` 新增：
  - `documents` 表新增 `kb_id`、`content_hash`、`last_verified_at`、`valid_until` 字段
  - 上述 5 张新表的建表语句
- [ ] 迁移策略：为每个租户自动创建"默认知识库"，现有文档归入默认库

**交付文件**：`deploy/init-postgres.sql`、`deploy/db_update.sql`

**验收标准**：
- 新环境启动后所有表存在
- 已有环境增量迁移无报错
- 现有文档自动关联到租户默认知识库

---

#### P1-T2：Rerank 引擎

- [ ] `src/knowledge/reranker/base.py` — `BaseReranker` 抽象基类 + `RerankResult` 数据类
- [ ] `src/knowledge/reranker/llm_reranker.py` — LLM-based Reranker 实现
  - 复用现有 LLM Gateway
  - `RERANK_PROMPT` 模板：输入 query + top-20 候选文档，输出带分数的 JSON
  - 解析 LLM 返回的 JSON，降级处理解析失败
- [ ] `src/knowledge/reranker/cross_encoder_reranker.py` — 预留接口（TODO，第二阶段实现）
- [ ] `src/knowledge/reranker/__init__.py` — 工厂函数 `get_reranker()`，根据配置返回实例
- [ ] `configs/config.yaml` 新增 `knowledge.rerank` 配置段

**交付文件**：`src/knowledge/reranker/`、`configs/config.yaml`

**验收标准**：
- 输入 20 条候选文档 → 输出 top-5 重排结果
- LLM 调用失败时降级返回原始排序
- Rerank 耗时记录到日志
- 配置 `enabled=false` 时跳过 Rerank

---

#### P1-T3：Query Rewriting 查询改写

- [ ] `src/knowledge/retriever/query_rewriter.py` — `QueryRewriter` 类
  - `REWRITE_PROMPT` 模板：对话历史 + 当前查询 → 独立查询
  - `rewrite()` 方法：取最近 N 轮对话，调用 LLM 改写
  - 无历史或调用失败时返回原始查询
- [ ] `configs/config.yaml` 新增 `knowledge.query_rewriting` 配置段
- [ ] 集成到 `src/tools/knowledge/knowledge_base_tool.py`：从 session 获取对话历史，调用改写

**交付文件**：`src/knowledge/retriever/query_rewriter.py`、`src/tools/knowledge/knowledge_base_tool.py`

**验收标准**：
- "高铁呢？" + 历史"公司差旅报销标准是什么？" → 改写为"公司差旅报销 高铁标准"
- 无对话历史时直接返回原始查询
- 改写超时/失败时降级到原始查询
- 改写前后 query 记录到日志

---

#### P1-T4：文档级权限控制

- [ ] `src/knowledge/permissions.py` — `KnowledgeBasePermission` 类
  - `get_accessible_kb_ids()` — 查询用户可访问的知识库 ID
  - `get_accessible_doc_ids()` — 查询用户可访问的文档 ID
  - 权限规则：`tenant` > `department` > `user` 三级
- [ ] 修改 `HybridRetriever.retrieve()` — 新增 `user_id`、`departments` 参数，注入文档 ID 过滤
- [ ] 修改 `VectorDBPostgreSQL.search()` — 支持 `doc_ids` 参数（Pre-retrieval filtering）
- [ ] 修改 FTS 检索 — 同步支持 `doc_ids` 过滤
- [ ] 后端 API 新增知识库 CRUD + 权限管理端点（设计文档 §3.2.3）
- [ ] 向后兼容：无 `user_id` 时不过滤

**交付文件**：`src/knowledge/permissions.py`、`src/knowledge/retriever/hybrid_retriever.py`、`src/knowledge/vector_db/vector_db.py`、`src/knowledge/api.py`

**验收标准**：
- 用户只能检索到有权限的知识库文档
- `tenant` 级权限：所有租户成员可见
- `department` 级权限：该部门用户可见
- `user` 级权限：仅指定用户可见
- 权限过滤对向量检索和 FTS 检索同时生效
- 无权限配置时（现有数据）全部可见

---

#### P1-T5：检索 Pipeline 协调器

- [ ] `src/knowledge/retriever/pipeline.py` — `RetrievalPipeline` 类
  - 串联：Query Rewriting → Permission Check → Hybrid Retrieval → Rerank
  - `search()` 方法：统一入口，编排各步骤
  - 异步记录检索日志（不阻塞返回）
- [ ] 替换现有调用点：`KnowledgeBaseTool`、API 搜索端点改为调用 `Pipeline.search()`

**交付文件**：`src/knowledge/retriever/pipeline.py`

**验收标准**：
- Pipeline 串联 4 个步骤，每个步骤可独立开关
- 单个步骤失败不影响后续步骤（降级策略）
- 检索日志异步写入，不增加主流程延迟
- 现有检索功能不受影响

---

#### P1-T6：检索日志记录

- [ ] `src/knowledge/service.py` 新增 `log_retrieval()` 方法
- [ ] 写入 `retrieval_logs` 表：`tenant_id`、`query`、`rewritten_query`、`results_count`、`top_score`、`latency_ms`、`reranked` 等
- [ ] 采样率支持（`sample_rate` 配置）：高流量时按比例采样
- [ ] 用户反馈 API：`POST /api/knowledge/retrieval_logs/{id}/feedback`

**交付文件**：`src/knowledge/service.py`、`src/knowledge/api.py`

**验收标准**：
- 每次检索（含改写 + Rerank）写入一条日志
- `sample_rate=0.5` 时约一半请求记录日志
- 用户反馈可正确更新到对应日志记录

---

#### P1-T7：质量评估服务

- [ ] `src/knowledge/evaluation/evaluator.py` — `KnowledgeBaseEvaluator` 类
  - `evaluate_retrieval()` — 运行评估集，计算 Precision@5、Recall@10、nDCG@10、Hit Rate
  - `evaluate_generation()` — 完整 RAG 流程评估，计算 Faithfulness、Answer Relevancy
  - `collect_feedback()` — 收集用户单条反馈
- [ ] 后端 API：评估集 CRUD + 执行评估 + 查看结果
- [ ] `configs/config.yaml` 新增 `knowledge.evaluation` 配置段

**交付文件**：`src/knowledge/evaluation/`、`src/knowledge/api.py`

**验收标准**：
- 上传评估集（JSON 格式）→ 执行评估 → 返回各项指标
- 评估结果保存到 `evaluation_results` 表
- 指标计算结果与手动验证一致

---

#### P1-T8：前端 Phase 1 页面

- [ ] 知识库管理页面（复用 Phase 0 的双栏布局，增加知识库切换）
- [ ] 权限设置弹窗（设置知识库的租户/部门/用户级访问权限）
- [ ] 检索调试工具（输入 query → 查看改写 → 查看检索结果 → 查看 Rerank 排序）
- [ ] 质量仪表盘卡片（检索成功率趋势、平均分数趋势、用户反馈分布）

**交付文件**：`frontend/src/components/KnowledgeBase.vue`（扩展）、新增调试/仪表盘页面

**验收标准**：
- 知识库切换 + 权限设置功能正常
- 调试工具可查看完整 Pipeline 各步骤输出
- 仪表盘图表正确展示检索质量指标

---

#### P1-T9：集成测试 + 性能测试

- [ ] Pipeline 全链路集成测试
- [ ] Rerank 性能测试（p95 延迟 < 400ms）
- [ ] 权限过滤性能测试（5000 文档过滤延迟 < 100ms）
- [ ] 回归测试：确保现有功能不受影响

**交付文件**：测试报告

**验收标准**：
- 所有集成测试通过
- 检索 p95 延迟（含 Rerank）< 400ms
- 无回归 bug

---

### Phase 1 工期排布

| 周 | 任务 | 依赖 |
|----|------|------|
| W1 | P1-T1 数据库变更 + P1-T2 Rerank 引擎 | 无 |
| W2 | P1-T4 文档级权限 + P1-T6 检索日志 | T1 |
| W3 | P1-T3 查询改写 + P1-T5 Pipeline 协调器 | T2, T3, T4 |
| W4 | P1-T7 质量评估 + P1-T8 前端页面 | T5, T6 |
| W5 | P1-T9 集成测试 + 性能测试 | T8 |

---

## 风险与注意事项

| 风险 | 阶段 | 缓解措施 |
|------|------|---------|
| 已有 source_type 未注册到分类表 | P0 | 迁移脚本自动提取；未注册文档在"全部"中可见 |
| 删除分类后文档孤立 | P0 | 删除分类不删文档；可重建同名分类恢复 |
| LLM Rerank 增加延迟 | P1 | Selective Rerank + 超时降级到原始排序 |
| Query Rewriting 失败 | P1 | fallback 到原始查询；记录改写日志 |
| 权限过滤性能 | P1 | 限制 max 5000 doc；PostgreSQL ANY 数组 |
| 检索日志写入压力 | P1 | 采样率可调；定期归档 |
| documents 表 kb_id 迁移 | P1 | 自动创建租户默认知识库，迁移脚本归入默认库 |
