# 景点知识库搜索工具设计文档

> 版本: v1.0 | 创建日期: 2026-05-14 | 状态: 待审核

## 1. 背景

### 1.1 当前问题

旅游顾问子智能体在给客户出行程概览时，存在以下不足：

1. **同城市景点补充不足**：客户说"想去黄果树"，子智能体只会围绕黄果树做行程，不会主动搜索安顺市还有什么其他景点可以安排
2. **顺路城市推荐缺失**：客户走贵州线，子智能体不会根据路线推荐贵阳、黔南等顺路城市及其景点
3. **搜索工具不适配**：现有 `knowledge_base_search` 工具搜索整个知识库（含所有文档类型），无景点专项搜索能力

### 1.2 现有搜索能力

| 工具/类 | 搜索范围 | 过滤能力 | 问题 |
|---------|----------|----------|------|
| `knowledge_base_search` | 全部文档 | 无类型/区域过滤 | 搜索结果混杂，可能返回非景点文档 |
| `AttractionRetriever.search()` | 仅景点 | 按名称(ILIKE)或向量搜索 | 属于报价 skill 内部组件，不对外暴露 |
| `GET /api/v1/travel-quote/search/attractions` | 仅景点 | 按名称或向量搜索 | 是 REST API，子智能体无法直接调用 |

### 1.3 设计目标

1. 提供一个**景点专项搜索工具**，只搜景点知识库，不搜其他文档
2. 支持**按城市/区域名搜索**，子智能体传入城市名即可获取该区域的景点
3. 搜索结果后续可直接用于报价（数据来源与报价 skill 一致）
4. 工具通过 `inherit: true` 自动对子智能体可用

---

## 2. 方案设计

### 2.1 新增 `attraction_search` 工具

**文件位置**: `src/tools/knowledge/attraction_search_tool.py`

**为什么不扩展现有 `knowledge_base_search`**：
- 子智能体需只搜景点库，不搜全库——通用工具加过滤不可靠
- 避免改动通用工具引入回归风险
- 新工具通过 `inherit: true` 自动对所有子智能体可用

**为什么不修改 `AttractionRetriever`**：
- `AttractionRetriever` 是报价 skill 的内部组件，职责是报价时查找景点价格
- 搜索补充景点是子智能体的对话阶段需求，不应与报价逻辑耦合
- 搜索逻辑自包含在工具内，减少跨组件依赖

### 2.2 搜索方式：纯向量语义搜索

参考现有 `AttractionRetriever.search_by_vector()` 的实现模式：

1. 将查询文本通过 Qwen `text-embedding-v3` 向量化（1024维）
2. 用 pgvector cosine distance 排序（`embedding <=> query_vector`）
3. 限制搜索范围：`source_type='attraction_resource'` + `chunk_index=0`（只搜景点信息摘要）
4. 按 distance 升序返回最近邻结果

**为什么用向量搜索而非字段匹配**：
- 景点知识库中的 `region` 字段值不统一（如"安顺方向"、"黔南"等），字段匹配容易漏结果
- 向量语义搜索能理解"安顺"与"安顺方向景区"的语义关联
- 用户传入"安顺景点"、"黄果树附近"等自然语言 query，向量搜索都能处理

### 2.3 SQL 查询

```sql
SELECT cv.embedding <=> %s::vector AS distance,
       c.doc_id, c.text, d.title, d.metadata, d.file_path
FROM chunks_vec cv
JOIN chunks c ON cv.chunk_id = c.id
JOIN documents d ON c.doc_id = d.id
WHERE d.source_type = 'attraction_resource'
  AND d.tenant_id = %s
  AND c.chunk_index = 0
ORDER BY distance
LIMIT %s
```

与现有 `AttractionRetriever.search_by_vector()` 的 SQL 完全一致，区别在于：
- 不从 travel-quote skill 目录导入，而是工具内独立实现
- `tenant_id` 从 ContextVar 获取而非参数传入
- 结果格式面向子智能体对话使用（截断长文本、突出区域信息）

---

## 3. 工具接口设计

### 3.1 输入参数

```python
class AttractionSearchInput(BaseModel):
    query: str = Field(..., description="搜索关键词（城市名、区域名或景点名）")
    top_k: Optional[int] = Field(20, description="返回结果数量，默认20")
```

**无 `search_type` 参数**：统一用向量语义搜索，简化接口。传入城市名（"安顺"）、区域名（"黔南"）、景点名（"黄果树"）均可，向量语义自动处理。

### 3.2 输出格式

```json
{
  "success": true,
  "results": [
    {
      "doc_id": 123,
      "title": "景点：黄果树瀑布",
      "info": "景点信息摘要（截断到500字符）...",
      "score": 0.8923,
      "region": "安顺方向",
      "category": "natural",
      "category_cn": "自然风光"
    }
  ],
  "count": 15
}
```

**字段说明**：
- `doc_id`: 知识库文档 ID，报价时用于关联门票价格
- `title`: 景点名称
- `info`: 景点信息摘要，截断到 500 字符避免上下文溢出
- `score`: 余弦相似度（1 - cosine distance），越高越相关
- `region`: 区域/方向（从 metadata 中提取）
- `category` / `category_cn`: 景点类型

### 3.3 工具描述

```python
name = "attraction_search"
description = (
    "从景点知识库中搜索景点信息。输入城市名、区域名或景点名称，"
    "返回匹配的景点列表（含名称、区域、类型、信息摘要等）。"
    "搜索结果来自景点知识库，后续报价时可直接使用。"
)
```

---

## 4. 实施步骤

### Step 1: 创建工具文件

**新增文件**: `src/tools/knowledge/attraction_search_tool.py`

工具类继承 `BaseTool`，实现：
- `_get_embedding_client()`: 延迟初始化 `TextEmbeddingV3Client`
- `_embed(query)`: 调用 Qwen text-embedding-v3 生成 1024 维向量
- `execute(query, top_k)`: 执行向量搜索 SQL，格式化返回

### Step 2: 注册工具

**修改文件**: `src/core/agent.py` 的 `_register_builtin_tools()` 方法

在 `KnowledgeBaseTool` 注册之后添加：
```python
from src.tools.knowledge.attraction_search_tool import AttractionSearchTool
self.tool_registry.register(AttractionSearchTool())
```

通过 `inherit: true` 自动对旅游顾问子智能体可用。

### Step 3: GIN 索引

**修改文件**: `deploy/db_update.sql`

```sql
-- 2026-5-14，景点区域搜索：为 documents.metadata 添加 GIN 索引
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin
ON documents USING GIN ((metadata::jsonb))
WHERE source_type = 'attraction_resource';
```

### Step 4: 更新 SUBAGENT.md

**修改文件**: `subagents/travel-consultant/SUBAGENT.md`

在"阶段二：出行程概览"部分之后、"阶段三"之前，插入景点补充搜索指引。

---

## 5. 子智能体使用流程

### 5.1 同城市景点补充

```
客户："30个初中生，7月初，5天，想去黄果树"
  ↓
子智能体推断：黄果树在安顺
  ↓
attraction_search(query="安顺景点", top_k=20)
  → 返回：龙宫、屯堡、旧州古镇等
  ↓
子智能体推荐："既然去安顺，龙宫也值得去，离黄果树不远"
```

### 5.2 顺路城市推荐

```
客户确认要去：黄果树（安顺）+ 天眼（平塘/黔南）
  ↓
子智能体推断顺路城市：贵阳（交通枢纽，安顺和黔南之间）
  ↓
attraction_search(query="贵阳景点", top_k=20)
  → 返回：青岩古镇、黔灵山、甲秀楼等
  ↓
子智能体推荐："路上经过贵阳，可以去青岩古镇逛逛"
```

### 5.3 景点名称确认

```
子智能体推荐了"小七孔"，客户问"小七孔有什么"
  ↓
attraction_search(query="小七孔", top_k=5)
  → 返回景点详细信息、门票价格区间等
  ↓
子智能体介绍景点特色
```

---

## 6. 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/tools/knowledge/attraction_search_tool.py` | **新增** | `attraction_search` 工具（纯向量搜索） |
| `src/core/agent.py` | 修改 | 注册新工具（1行） |
| `deploy/db_update.sql` | 修改 | 添加 GIN 索引 |
| `subagents/travel-consultant/SUBAGENT.md` | 修改 | 添加景点搜索指引 |

---

## 7. 验证方案

1. **工具单元测试**：`attraction_search(query="安顺", top_k=20)` 返回安顺区域景点，向量语义排序
2. **搜索精度测试**：`attraction_search(query="黄果树")` 返回黄果树瀑布为第一结果
3. **端到端测试**：发送"30个初中生，7月初，5天，想去黄果树"到旅游顾问，验证子智能体调用 `attraction_search` 并推荐同城市景点
