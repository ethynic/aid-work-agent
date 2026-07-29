# 知识库检索工具租户隔离设计

## 问题背景

`KnowledgeBaseTool`（工具名 `knowledge_base_search`）是智能体用来检索知识中心的核心工具。在多租户 SaaS 部署中，该工具**完全没有租户隔离**，向量搜索、全文搜索、文档标题查询都直接操作 `chunks`/`chunks_vec` 表，不带任何 `tenant_id` 过滤。

这意味着一个租户的知识库搜索可能返回其他租户的文档数据，属于**数据隔离违规**。

## 现状分析

### 数据模型

```
documents (有 tenant_id)
  └── chunks (无 tenant_id，通过 doc_id 关联 documents)
        └── chunks_vec (无 tenant_id，通过 chunk_id 关联 chunks)
        └── chunks_fts (无 tenant_id，通过 chunk_id 关联 chunks)
```

`chunks` 和 `chunks_vec` 表本身没有 `tenant_id` 列，租户隔离需要通过 JOIN `chunks → documents` 实现。

### 当前检索链路（无租户过滤）

```
KnowledgeBaseTool.execute()            # 不接收/不传递 tenant_id
  → HybridRetriever.retrieve()        # 不接收 tenant_id 参数
      → VectorDBPostgreSQL.search()   # 直接查 chunks_vec，无 JOIN
      → _postgres_fts_search()        # 直接查 chunks，无 JOIN
      → _build_results()              # 按 chunk_id 查 chunks，无过滤
  → 查询 documents 表获取标题         # 无 tenant_id WHERE 条件
```

### Agent 注入机制

Agent 在 `process_message()` 和 `execute_as_subagent()` 中会向工具注入 `tenant_id`，但当前注入列表只包含 `attraction_search`，**遗漏了 `knowledge_base_search`**。

### 参考实现：AttractionSearchTool

`AttractionSearchTool`（`src/tools/knowledge/attraction_search_tool.py`）正确实现了租户隔离：

1. 提供 `set_tenant_id()` 方法供 Agent 注入
2. 回退到 ContextVar `get_current_tenant_id()`
3. SQL 中 JOIN `chunks → documents` 并 `WHERE d.tenant_id = %s`

## 设计方案

### 方案选择：JOIN 过滤（不改表结构）

在 SQL 查询中 JOIN `documents` 表过滤 `tenant_id`，与 `AttractionSearchTool` 保持一致。

**原因**：
- `documents` 表数据量相对较小，JOIN 开销可接受
- 不需要改表结构，避免数据迁移
- 与已有模式一致

### 修改文件清单

#### 1. `src/knowledge/vector_db/vector_db.py` — 向量搜索加 tenant_id

`search()` 方法添加 `tenant_id: Optional[str] = None` 参数：

```python
async def search(
    self,
    query_embedding: List[float],
    top_k: int = 10,
    tenant_id: Optional[str] = None
) -> List[Tuple[int, float]]:
```

有 `tenant_id` 时 SQL 改为：

```sql
SELECT cv.chunk_id, cv.embedding <=> %s::vector as distance
FROM chunks_vec cv
JOIN chunks c ON cv.chunk_id = c.id
JOIN documents d ON c.doc_id = d.id
WHERE d.tenant_id = %s
ORDER BY distance
LIMIT %s
```

无 `tenant_id` 时保持原有 SQL 不变。

#### 2. `src/knowledge/retriever/hybrid_retriever.py` — 透传 tenant_id

三处改动：

- `retrieve()` 添加 `tenant_id: Optional[str] = None`，透传给 `vector_db.search()` 和 `_fts_search()`
- `_fts_search()` 添加 `tenant_id` 参数，透传给 `_postgres_fts_search()`
- `_postgres_fts_search()` 添加 `tenant_id` 参数，有值时 SQL 改为：

```sql
SELECT c.id, ts_rank(c.text_vec, plainto_tsquery(%s)) as score
FROM chunks c
JOIN documents d ON c.doc_id = d.id
WHERE c.text_vec @@ plainto_tsquery(%s)
  AND d.tenant_id = %s
ORDER BY score DESC
LIMIT %s
```

#### 3. `src/tools/knowledge/knowledge_base_tool.py` — 工具层租户隔离

- `__init__()` 添加 `self._tenant_id = None`
- 添加 `set_tenant_id()` 方法（供 Agent 注入）
- `execute()` 中解析 tenant_id：优先 `self._tenant_id`，回退 ContextVar `get_current_tenant_id()`
- `execute()` 将 `tenant_id` 传给 `retriever.retrieve(..., tenant_id=tenant_id)`
- 文档标题查询添加 `AND tenant_id = %s` 过滤

#### 4. `src/core/agent.py` — 注入 tenant_id

两处工具注入循环（`process_message()` 和 `execute_as_subagent()`）：

```python
# 从
for tool_name in ("attraction_search",):
# 改为
for tool_name in ("attraction_search", "knowledge_base_search"):
```

#### 5. `src/knowledge/service.py` — search_documents 透传

`search_documents()` 已接收 `tenant_id` 参数，将 `tenant_id` 透传给 `retriever.retrieve()` 即可。

### 向后兼容

所有新增参数均为 `Optional[str] = None`，默认 None 时 SQL 和行为与现有代码完全一致，不影响非 SaaS 模式。

## 实施顺序

1. `vector_db.py`（底层，无下游依赖）
2. `hybrid_retriever.py`（依赖步骤 1）
3. `knowledge_base_tool.py` + `agent.py`（依赖步骤 2）
4. `service.py`（依赖步骤 2）

## 验证

1. 不同租户账号调用 `knowledge_base_search`，确认只返回本租户文档
2. 非 SaaS 模式（无 tenant_id）行为不变
3. 确认 JOIN 不导致明显性能下降
