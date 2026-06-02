# 知识库检索租户隔离 — 开发计划

## 关联设计文档

[知识库检索工具租户隔离设计](knowledge-base-search-tenant-isolation-design.md)

## 任务清单

### Task 1: 向量搜索添加 tenant_id 过滤

**文件**: `src/knowledge/vector_db/vector_db.py`

- [x] `VectorDBPostgreSQL.search()` 方法添加 `tenant_id: Optional[str] = None` 参数
- [x] 有 `tenant_id` 时，SQL JOIN `chunks → documents` 并 `WHERE d.tenant_id = %s`
- [x] 无 `tenant_id` 时保持原有 SQL

### Task 2: 混合检索器透传 tenant_id

**文件**: `src/knowledge/retriever/hybrid_retriever.py`

- [x] `retrieve()` 添加 `tenant_id` 参数，透传给 `vector_db.search()` 和 `_fts_search()`
- [x] `_fts_search()` 添加 `tenant_id` 参数，透传给 `_postgres_fts_search()`
- [x] `_postgres_fts_search()` 添加 `tenant_id` 参数，有值时 JOIN `documents` 过滤

### Task 3: 知识库工具添加租户隔离

**文件**: `src/tools/knowledge/knowledge_base_tool.py`

- [x] `__init__()` 添加 `self._tenant_id = None`
- [x] 添加 `set_tenant_id()` 方法
- [x] `execute()` 中解析 tenant_id（优先注入值，回退 ContextVar）
- [x] `execute()` 传递 `tenant_id` 给 `retriever.retrieve()`
- [x] 文档标题查询添加 `AND tenant_id = %s` 过滤

### Task 4: Agent 注入 tenant_id

**文件**: `src/core/agent.py`

- [x] `process_message()` 中工具注入循环添加 `"knowledge_base_search"`
- [x] `execute_as_subagent()` 中工具注入循环添加 `"knowledge_base_search"`

### Task 5: service.py 透传 tenant_id

**文件**: `src/knowledge/service.py`

- [x] `search_documents()` 中 `retriever.retrieve()` 调用添加 `tenant_id=tenant_id`

### Task 6: 验证

- [ ] 启动应用，不同租户测试 `knowledge_base_search` 只返回本租户数据
- [ ] 非 SaaS 模式行为不变
