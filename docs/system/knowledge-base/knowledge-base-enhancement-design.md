# 知识库能力增强方案设计文档

> 关联文档：[企业级 2B 智能体平台基础设施建设差距分析报告](../../research/enterprise-agent-infrastructure-gap-analysis.md) §2.3
> 前序设计：[企业知识库功能设计文档](./enterprise_knowledge_base.md)
> 技术调研：[企业知识库 RAG 系统前沿技术调研](../../research/enterprise-knowledge-base-rag-research.md)
> 创建日期：2026-05-28
> 状态：设计中

---

## 一、背景与目标

### 1.1 当前系统现状

知识库模块（`src/knowledge/`）已具备完整的基础 RAG 流程：

| 能力 | 实现 | 代码位置 | 评级 |
|------|------|---------|------|
| 文档解析 | PDF/Word/Excel/PPT/文本 5 种解析器 | `parsers/` | 良好 |
| 文本分块 | 句子边界分块，512 字/块，64 字重叠 | `chunker.py` (~252 行) | 良好 |
| 向量嵌入 | DashScope text-embedding-v3，1024 维 | `embedding/` (~153 行) | 良好 |
| 向量存储 | pgvector HNSW | `vector_db/` (~215 行) | 良好 |
| 混合检索 | 向量 0.7 + FTS 0.3，RRF 融合 | `retriever/hybrid_retriever.py` (~388 行) | 良好 |
| 租户隔离 | documents/chunks 表 tenant_id 字段 | 数据库层 | 良好 |

**核心短板**（与 Dify/FastGPT 等行业标杆对比）：

| 短板 | 影响 | 紧迫度 |
|------|------|--------|
| 无 Rerank 重排 | 检索精度不足，相关文档排序不佳 | **P0** |
| 无文档级权限 | 仅租户级隔离，无法控制部门/用户级访问 | **P0** |
| 无质量评估 | 无法量化检索效果，无法发现退化 | **P1** |
| 无多轮对话检索 | 长对话中指代消解失败，检索结果无关 | **P1** |
| 无结构化数据问答 | 无法查询 Excel/数据库等结构化数据 | **P2** |
| 无文档自动同步 | 文档变更需手动重新上传 | **P2** |
| 无健康监控 | 不知道知识库整体质量如何 | **P2** |

### 1.2 建设目标

将知识库从"基础可用"（B+ 级）提升到"企业信赖"（A 级）：

| 指标 | 当前 | 目标 |
|------|------|------|
| 检索 Precision@5 | 未度量 | > 0.75 |
| 检索 Hit Rate | 未度量 | > 0.90 |
| 检索失败率（无相关结果） | ~5.7%（行业基线） | < 2% |
| 检索 p95 延迟 | ~200ms | < 400ms（含 Rerank） |
| 多轮对话检索准确率 | N/A | > 0.80 |

---

## 二、总体架构

在现有混合检索 Pipeline 基础上，增加三层增强：

```
┌─────────────────────────────────────────────────────────────┐
│                      前置增强层                              │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │  Query Rewriting  │  │  权限过滤        │                │
│  │  (多轮对话查询改写) │  │  (Pre-retrieval) │                │
│  └────────┬─────────┘  └────────┬─────────┘                │
└───────────┼─────────────────────┼──────────────────────────┘
            │                     │
┌───────────┼─────────────────────┼──────────────────────────┐
│           ▼                     ▼     检索层（现有）         │
│  ┌──────────────────────────────────────┐                  │
│  │        HybridRetriever               │                  │
│  │  向量检索(top-30) + FTS检索(top-30)    │                  │
│  │  → RRF 融合 → top-20                 │                  │
│  └──────────────────┬───────────────────┘                  │
└─────────────────────┼────────────────────────────────────┘
                      │
┌─────────────────────┼────────────────────────────────────┐
│                     ▼        后置增强层                    │
│  ┌──────────────────────────────────────┐                │
│  │        Rerank 重排序                  │                │
│  │  Cross-Encoder → 精排 top-5          │                │
│  └──────────────────┬───────────────────┘                │
└─────────────────────┼────────────────────────────────────┘
                      │
                      ▼
                 LLM 上下文注入
```

新增模块清单：

| 模块 | 位置 | 新增代码量估算 |
|------|------|---------------|
| Rerank 引擎 | `src/knowledge/reranker/` | ~250 行 |
| 查询改写器 | `src/knowledge/retriever/query_rewriter.py` | ~200 行 |
| 文档权限服务 | `src/knowledge/permissions.py` | ~180 行 |
| 质量评估服务 | `src/knowledge/evaluation/` | ~300 行 |
| 检索 Pipeline 协调器 | `src/knowledge/retriever/pipeline.py` | ~150 行 |
| 数据库迁移 | `deploy/db_update.sql` + `deploy/init-postgres.sql` | 若干 DDL |

---

## 三、详细设计

### 3.1 Rerank 重排序（P0）

#### 3.1.1 方案选型

基于 [技术调研 §1](../../research/enterprise-knowledge-base-rag-research.md) 的对比分析：

| 方案 | 延迟 | 质量 | 成本 | 是否引入外部依赖 |
|------|------|------|------|----------------|
| **LLM-based Rerank** | +500-900ms | 中高 | 高（每次消耗 Token） | 否 |
| **BGE-reranker（本地部署）** | +100-150ms | 高（nDCG 0.715） | 低（需 GPU） | 是，需部署模型 |
| **Cohere Rerank API** | +200ms | 最高（nDCG 0.735） | 中（按量付费） | 是，外部 API |
| **LLM 轻量 Rerank（推荐首期）** | +300-500ms | 中 | 低（复用现有 LLM） | 否 |

**决策：分两阶段实施**

- **第一阶段（推荐）**：LLM-based Rerank，复用现有 LLM Gateway，零外部依赖
- **第二阶段（按需）**：接入 BGE-reranker 或 bce-reranker（中英双语场景优秀）

#### 3.1.2 LLM-based Rerank 实现

**调用时机**：在 HybridRetriever RRF 融合之后、送入 LLM 上下文之前。

```python
# src/knowledge/reranker/llm_reranker.py

RERANK_PROMPT = """你是一个文档相关性评估专家。请对以下文档片段与用户查询的相关性进行打分。

用户查询：{query}

文档片段：
{documents}

请严格按以下 JSON 格式返回结果（只返回 JSON，不要其他内容）：
[
  {{"index": 0, "score": 0.95, "reason": "直接回答了用户的问题"}},
  {{"index": 1, "score": 0.3, "reason": "仅部分相关"}},
  ...
]

评分标准：
- 1.0：完全回答了用户问题
- 0.7-0.9：大部分相关，包含关键信息
- 0.4-0.6：部分相关
- 0.0-0.3：几乎不相关
"""
```

**Reranker 抽象接口**（为后续切换到 Cross-Encoder 预留）：

```python
# src/knowledge/reranker/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class RerankResult:
    index: int           # 原始列表中的索引
    score: float         # 重排分数 [0, 1]
    reason: str = ""     # 相关性判断理由（LLM reranker 提供）

class BaseReranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, documents: list[str],
                     top_k: int = 5) -> list[RerankResult]:
        """对文档列表进行重排序"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass
```

**Pipeline 集成点**（修改 `HybridRetriever.retrieve()`）：

```python
# 现有流程（hybrid_retriever.py retrieve() 方法末尾）
# 1. 向量检索 top_k*3
# 2. FTS 检索
# 3. RRF 融合 → top-20
# 4. 归一化 → 截断

# 新增步骤：
# 5. Rerank 重排 → top_k（默认 5）
```

**降级策略**：

```python
async def retrieve_with_rerank(self, query: str, top_k: int = 5,
                                use_rerank: bool = True) -> list:
    candidates = await self.retrieve(query, top_k=top_k * 3)  # 取更多候选

    if not use_rerank or not candidates:
        return candidates[:top_k]

    try:
        reranker = get_reranker()  # 获取配置的 reranker 实例
        docs_text = [c["text"] for c in candidates]
        reranked = await reranker.rerank(query, docs_text, top_k=top_k)
        return [candidates[r.index] for r in reranked]
    except Exception as e:
        logger.warning(f"Rerank 失败，降级使用原始排序: {e}")
        return candidates[:top_k]  # 降级到原始 RRF 排序
```

**配置项**（`configs/config.yaml` 新增）：

```yaml
knowledge:
  rerank:
    enabled: true
    provider: "llm"           # "llm" | "bge" | "cohere"
    top_k: 5                  # rerank 后保留的结果数
    max_candidates: 20        # 参与 rerank 的最大候选数
    timeout_ms: 3000          # rerank 超时时间
    fallback_on_error: true   # 失败时降级到原始排序
```

#### 3.1.3 第二阶段：Cross-Encoder Reranker（预留）

```python
# src/knowledge/reranker/cross_encoder_reranker.py
# 预留接口，后续接入 bge-reranker 或 bce-reranker

class CrossEncoderReranker(BaseReranker):
    """基于 Cross-Encoder 模型的 Reranker
    
    部署方式：
    1. 本地部署：pip install sentence-transformers + bge-reranker-large
    2. API 调用：接入 Cohere Rerank API 或自建推理服务
    """
    @property
    def name(self) -> str:
        return "cross_encoder"
    
    async def rerank(self, query, documents, top_k=5):
        # TODO: 第二阶段实现
        ...
```

---

### 3.2 文档级权限控制（P0）

#### 3.2.1 权限模型

基于 [技术调研 §2](../../research/enterprise-knowledge-base-rag-research.md) 的分析，采用 **Pre-retrieval Filtering + In-Database Authorization** 模式。

**权限层级**：

```
租户级隔离（已有）
  └── 知识库级隔离（新增：按知识库分组管理文档）
        └── 文档级权限（新增：控制哪些部门/用户可见）
```

**新增数据模型**：

```sql
-- 知识库表：将文档按知识库分组管理
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_kb_tenant ON knowledge_bases(tenant_id);

-- 文档表扩展：关联到知识库
ALTER TABLE documents ADD COLUMN IF NOT EXISTS kb_id INTEGER REFERENCES knowledge_bases(id);

-- 知识库成员权限表
CREATE TABLE IF NOT EXISTS kb_permissions (
    id SERIAL PRIMARY KEY,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id),
    subject_type TEXT NOT NULL,       -- 'tenant' | 'department' | 'user'
    subject_id TEXT NOT NULL,         -- 租户ID / 部门ID / 用户ID
    permission TEXT NOT NULL,         -- 'read' | 'write' | 'admin'
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(kb_id, subject_type, subject_id)
);

CREATE INDEX idx_kb_perm_kb ON kb_permissions(kb_id);
CREATE INDEX idx_kb_perm_subject ON kb_permissions(subject_type, subject_id);
```

**权限规则**：

| subject_type | 含义 | 权限继承 |
|-------------|------|---------|
| `tenant` | 整个租户可见 | 所有租户成员可读 |
| `department` | 部门可见 | 该部门下所有用户可读 |
| `user` | 指定用户可见 | 仅该用户可读 |

**权限判断逻辑**：

```python
# src/knowledge/permissions.py

class KnowledgeBasePermission:
    def __init__(self, db_pool):
        self.db_pool = db_pool
        self._cache = {}  # 用户权限缓存（考虑多 Worker，后续迁移到 Redis）

    async def get_accessible_kb_ids(self, user_id: str, tenant_id: str,
                                     departments: list[str] = None) -> set[int]:
        """获取用户可访问的所有知识库 ID"""
        conditions = ["(subject_type = 'tenant' AND subject_id = %s)"]
        params = [tenant_id]

        if departments:
            placeholders = ",".join(["%s"] * len(departments))
            conditions.append(
                f"(subject_type = 'department' AND subject_id IN ({placeholders}))"
            )
            params.extend(departments)

        conditions.append("(subject_type = 'user' AND subject_id = %s)")
        params.append(user_id)

        query = f"""
            SELECT DISTINCT kb_id FROM kb_permissions
            WHERE ({" OR ".join(conditions)})
        """
        # ... 执行查询返回 kb_id 集合

    async def get_accessible_doc_ids(self, user_id: str, tenant_id: str,
                                      departments: list[str] = None,
                                      limit: int = 5000) -> set[int]:
        """获取用户可访问的所有文档 ID（用于 pre-retrieval filtering）"""
        kb_ids = await self.get_accessible_kb_ids(user_id, tenant_id, departments)
        if not kb_ids:
            return set()
        # 查询这些知识库下的所有文档 ID
        # SELECT id FROM documents WHERE kb_id IN (...)
```

#### 3.2.2 检索时权限过滤

修改 `HybridRetriever` 的检索方法，在向量检索和 FTS 检索时注入文档 ID 过滤：

```python
# hybrid_retriever.py 修改

async def retrieve(self, query: str, top_k: int = 10,
                   user_id: str = None, tenant_id: str = None,
                   departments: list[str] = None) -> Dict:
    """混合检索，支持权限过滤"""

    # 1. 权限过滤：获取用户可访问的文档 ID 集合
    accessible_doc_ids = None
    if user_id and tenant_id:
        from src.knowledge.permissions import kb_permission
        accessible_doc_ids = await kb_permission.get_accessible_doc_ids(
            user_id, tenant_id, departments
        )
        if not accessible_doc_ids:
            return {"success": True, "results": [], "count": 0}

    # 2. 向量检索（注入 doc_id 过滤）
    vector_results = await self._vector_search(query_embedding, top_k * 3,
                                                doc_ids=accessible_doc_ids)

    # 3. FTS 检索（注入 doc_id 过滤）
    fts_results = await self._fts_search(preprocessed_query, top_k * 3,
                                          doc_ids=accessible_doc_ids)

    # 4. RRF 融合 + Rerank（后续流程不变）
    ...
```

**向量检索的权限过滤**（修改 `vector_db.py`）：

```python
# 现有：纯向量相似度搜索
# SELECT chunk_id, 1 - (embedding <=> %s) AS similarity
# FROM chunks_vec WHERE ...

# 新增：带文档权限过滤的搜索
async def search_with_permission(self, query_embedding, top_k: int,
                                  doc_ids: set[int] = None):
    if doc_ids is not None:
        # Pre-retrieval filtering：通过 JOIN chunks 表过滤
        query = """
            SELECT cv.chunk_id, 1 - (cv.embedding <=> %s) AS similarity,
                   c.doc_id, c.text
            FROM chunks_vec cv
            JOIN chunks c ON cv.chunk_id = c.id
            WHERE c.doc_id = ANY(%s)
            ORDER BY cv.embedding <=> %s
            LIMIT %s
        """
        params = (str(query_embedding), list(doc_ids), str(query_embedding), top_k)
    else:
        # 无权限过滤，使用原始逻辑
        ...
```

#### 3.2.3 API 变更

```python
# 新增 API 端点（knowledge/api.py）

# 知识库 CRUD
POST   /api/knowledge/bases                     # 创建知识库
GET    /api/knowledge/bases                     # 列出知识库
PUT    /api/knowledge/bases/{kb_id}             # 更新知识库
DELETE /api/knowledge/bases/{kb_id}             # 删除知识库

# 知识库权限管理
POST   /api/knowledge/bases/{kb_id}/permissions  # 设置权限
GET    /api/knowledge/bases/{kb_id}/permissions  # 查看权限
DELETE /api/knowledge/bases/{kb_id}/permissions/{perm_id}  # 删除权限

# 文档上传（变更：需指定 kb_id）
POST   /api/knowledge/upload?kb_id={kb_id}      # 上传到指定知识库
POST   /api/knowledge/upload/batch?kb_id={kb_id} # 批量上传
```

**向后兼容**：如果请求不指定 `kb_id`，则自动使用租户的默认知识库（每个租户自动创建一个"默认知识库"）。

---

### 3.3 检索质量评估（P1）

#### 3.3.1 评估指标体系

基于 [技术调研 §3](../../research/enterprise-knowledge-base-rag-research.md) 的 RAGAS 框架，定义三层指标：

**第一层：检索指标（不依赖 LLM，可实时计算）**

| 指标 | 数据来源 | 计算方式 |
|------|---------|---------|
| 无结果率 | 每次检索记录 | 返回空结果的查询占比 |
| 平均检索分数 | 每次检索记录 | top-1 分数均值 |
| 低分结果占比 | 每次检索记录 | 分数 < 0.3 的结果占比 |
| 检索延迟 | 每次检索记录 | p50/p95 延迟 |

**第二层：评估集指标（依赖标注数据，定期评估）**

| 指标 | 数据来源 | 目标分数 |
|------|---------|---------|
| Precision@5 | 标注的 (query, relevant_docs) 对 | > 0.75 |
| Recall@10 | 标注的 (query, all_relevant_docs) 对 | > 0.80 |
| nDCG@10 | 标注的排序数据 | > 0.70 |
| Hit Rate | 标注数据 | > 0.90 |

**第三层：生成质量指标（依赖 LLM-as-Judge，定期评估）**

| 指标 | 计算方式 | 目标分数 |
|------|---------|---------|
| Faithfulness | LLM 判断回答是否基于检索上下文 | > 0.85 |
| Answer Relevancy | LLM 判断回答是否切题 | > 0.80 |

#### 3.3.2 数据采集

**新增数据库表**：

```sql
-- 检索日志表：记录每次检索的详细信息
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT,
    query TEXT NOT NULL,
    rewritten_query TEXT,               -- 改写后的查询（如有）
    results_count INTEGER,              -- 返回结果数
    top_score FLOAT,                    -- 最高分数
    avg_score FLOAT,                    -- 平均分数
    latency_ms INTEGER,                 -- 检索耗时（毫秒）
    reranked BOOLEAN DEFAULT FALSE,     -- 是否经过 rerank
    rerank_latency_ms INTEGER,          -- rerank 耗时
    user_feedback TEXT,                 -- 用户反馈：'relevant' | 'irrelevant' | NULL
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_retrieval_logs_tenant ON retrieval_logs(tenant_id, created_at DESC);

-- 评估数据集表：存储标注的测试用例
CREATE TABLE IF NOT EXISTS evaluation_sets (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    test_cases JSONB NOT NULL,           -- [{"query": "...", "relevant_doc_ids": [...], "expected_answer": "..."}]
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 评估结果表：记录每次评估的结果
CREATE TABLE IF NOT EXISTS evaluation_results (
    id SERIAL PRIMARY KEY,
    eval_set_id INTEGER REFERENCES evaluation_sets(id),
    precision_at_5 FLOAT,
    recall_at_10 FLOAT,
    ndcg_at_10 FLOAT,
    hit_rate FLOAT,
    faithfulness FLOAT,
    answer_relevancy FLOAT,
    total_queries INTEGER,
    config_snapshot JSONB,               -- 评估时的配置快照（chunk_size, reranker 等）
    created_at TIMESTAMP DEFAULT NOW()
);
```

#### 3.3.3 自动化评估服务

```python
# src/knowledge/evaluation/evaluator.py

class KnowledgeBaseEvaluator:
    """知识库检索质量评估器"""

    async def evaluate_retrieval(self, eval_set_id: int) -> dict:
        """运行检索评估，返回各项指标"""
        # 1. 加载评估集
        # 2. 对每个 test case 执行检索
        # 3. 计算 Precision@K, Recall@K, nDCG@K, Hit Rate
        # 4. 保存结果到 evaluation_results 表
        ...

    async def evaluate_generation(self, eval_set_id: int) -> dict:
        """运行生成质量评估（需要 LLM-as-Judge）"""
        # 1. 加载评估集
        # 2. 对每个 test case 执行完整 RAG 流程
        # 3. 使用 LLM 评估 Faithfulness 和 Answer Relevancy
        ...

    async def collect_feedback(self, retrieval_log_id: int,
                                feedback: str) -> None:
        """收集用户对检索结果的单条反馈"""
        # 更新 retrieval_logs.user_feedback
        ...
```

#### 3.3.4 前端展示

在现有监控仪表盘中增加知识库质量板块：

- 检索质量趋势图（按天/周）
- 无结果率趋势
- 用户反馈分布（相关/不相关）
- 检索延迟分布

---

### 3.4 多轮对话检索增强（P1）

#### 3.4.1 问题分析

生产环境中 60%+ 的 follow-up 消息包含未解析的指代。当前系统直接将用户原始消息作为检索 query，导致多轮对话场景下检索失败。

**典型失败案例**：

```
Turn 1: 用户："公司差旅报销标准是什么？"  → 检索成功
Turn 2: 用户："高铁呢？"                 → 检索"高铁"→ 返回无关结果
                                      → 应检索"公司差旅报销 高铁标准"
```

#### 3.4.2 Query Rewriting 实现

基于 [技术调研 §6](../../research/enterprise-knowledge-base-rag-research.md) 的四层 Contextualizer 架构，首期实现最核心的 **共指消解层**。

```python
# src/knowledge/retriever/query_rewriter.py

REWRITE_PROMPT = """你是一个查询改写助手。根据对话历史，将用户的最新消息改写为一个独立、完整、可用于文档检索的查询。

对话历史：
{chat_history}

用户最新消息：{current_query}

规则：
1. 将代词替换为实际指代的实体
2. 补充隐含的上下文信息
3. 保持用户原始意图不变
4. 只输出改写后的查询，不要任何解释

改写后的查询："""


class QueryRewriter:
    """查询改写器：将多轮对话中的依赖上下文的查询改写为独立查询"""

    def __init__(self, llm_gateway):
        self.llm = llm_gateway

    async def rewrite(self, current_query: str,
                       chat_history: list[dict] = None,
                       max_history_turns: int = 5) -> str:
        """
        将当前查询改写为独立查询。

        Args:
            current_query: 用户当前消息
            chat_history: 对话历史 [{"role": "user"|"assistant", "content": "..."}]
            max_history_turns: 使用最近 N 轮对话

        Returns:
            改写后的独立查询
        """
        if not chat_history or len(chat_history) < 2:
            return current_query  # 无历史或仅有 1 轮，无需改写

        # 取最近 N 轮
        recent = chat_history[-max_history_turns * 2:]  # user+assistant 各 1 条 = 1 轮
        history_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else '助手'}：{m['content']}"
            for m in recent
        )

        try:
            result = await self.llm.chat(
                messages=[{"role": "user", "content": REWRITE_PROMPT.format(
                    chat_history=history_text,
                    current_query=current_query
                )}],
                temperature=0.1,  # 低温度保证稳定性
                max_tokens=200
            )
            rewritten = result.strip()
            return rewritten if rewritten else current_query
        except Exception as e:
            logger.warning(f"Query rewriting 失败，使用原始查询: {e}")
            return current_query  # 降级到原始查询
```

#### 3.4.3 Pipeline 集成

修改 `KnowledgeBaseTool`（智能体调用的知识库工具），将对话历史传入检索流程：

```python
# src/tools/knowledge/knowledge_base_tool.py 修改

class KnowledgeBaseTool(BaseTool):
    async def execute(self, **kwargs):
        query = kwargs.get("query", "")

        # 从 session 上下文获取对话历史
        chat_history = self._get_chat_history()

        # 查询改写
        if chat_history:
            rewritten = await query_rewriter.rewrite(query, chat_history)
            logger.debug(f"查询改写：'{query}' → '{rewritten}'")
            query = rewritten

        # 执行检索（后续流程不变）
        result = await retriever.retrieve_with_rerank(query, top_k=top_k)
        ...
```

#### 3.4.4 配置项

```yaml
knowledge:
  query_rewriting:
    enabled: true
    max_history_turns: 5       # 使用最近 N 轮对话
    max_latency_ms: 1000       # 改写最大延迟
    fallback_on_error: true    # 失败时使用原始查询
```

---

### 3.5 检索 Pipeline 协调器（整合）

新增 Pipeline 协调器，统一编排各增强模块的调用顺序：

```python
# src/knowledge/retriever/pipeline.py

class RetrievalPipeline:
    """检索 Pipeline 协调器：串联 Query Rewriting → Permission → Retrieval → Rerank"""

    def __init__(self, retriever, reranker, query_rewriter, permission):
        self.retriever = retriever
        self.reranker = reranker
        self.rewriter = query_rewriter
        self.permission = permission

    async def search(self, query: str, *,
                     user_id: str = None,
                     tenant_id: str = None,
                     departments: list[str] = None,
                     chat_history: list[dict] = None,
                     top_k: int = 5,
                     use_rerank: bool = True,
                     use_rewrite: bool = True) -> dict:
        """
        完整的检索 Pipeline。

        流程：Query Rewriting → Permission Check → Hybrid Retrieval → Rerank
        """
        original_query = query
        rewritten_query = None

        # Step 1: Query Rewriting
        if use_rewrite and chat_history:
            rewritten_query = await self.rewriter.rewrite(query, chat_history)
            effective_query = rewritten_query
        else:
            effective_query = query

        # Step 2: Permission Check (pre-retrieval)
        doc_ids_filter = None
        if user_id and tenant_id:
            doc_ids_filter = await self.permission.get_accessible_doc_ids(
                user_id, tenant_id, departments
            )
            if doc_ids_filter is not None and len(doc_ids_filter) == 0:
                return {"success": True, "results": [], "count": 0}

        # Step 3: Hybrid Retrieval（带权限过滤）
        candidates = await self.retriever.retrieve(
            effective_query, top_k=top_k * 4,  # 多取候选用于 rerank
            doc_ids=doc_ids_filter
        )

        # Step 4: Rerank
        if use_rerank and candidates.get("results"):
            try:
                reranked = await self.reranker.rerank(
                    effective_query,
                    [r["text"] for r in candidates["results"]],
                    top_k=top_k
                )
                candidates["results"] = [candidates["results"][r.index] for r in reranked]
            except Exception as e:
                logger.warning(f"Rerank 降级: {e}")
                candidates["results"] = candidates["results"][:top_k]

        # Step 5: 记录检索日志（异步，不阻塞返回）
        asyncio.create_task(self._log_retrieval(
            original_query=original_query,
            rewritten_query=rewritten_query,
            tenant_id=tenant_id,
            results=candidates,
            ...
        ))

        return candidates
```

---

### 3.6 结构化数据问答 Text-to-SQL（P2）

> **已独立设计**：此模块复杂度高，已单独进行深度调研和详细设计。
> - 技术调研：[Text-to-SQL 与数据分析智能体技术调研](../../research/text-to-sql-data-analysis-agent-research.md)
> - 详细设计：[数据分析智能体设计文档](../digital-employee/data-analysis-subagent-design.md)

---

### 3.7 文档自动同步（P2）

> 当前仅做架构预留。

#### 3.7.1 支持的数据源

| 数据源 | 同步方式 | 优先级 |
|--------|---------|--------|
| 本地/共享文件夹 | 轮询 + SHA256 哈希对比 | P2 |
| Web 页面 | 定期爬取 + 内容哈希对比 | P3 |
| 企业网盘 | Webhook + API 拉取 | P3 |

#### 3.7.2 同步流程

```
数据源配置 → 定时/事件触发 → 拉取最新内容
  → 计算内容哈希 → 与存储的哈希对比
    → 无变更：跳过
    → 有变更：删除旧 chunks → 重新解析 → 分块 → 嵌入 → 更新向量
```

---

### 3.8 知识库健康监控（P2）

#### 3.8.1 健康度指标

| 指标 | 数据来源 | 告警阈值 |
|------|---------|---------|
| 文档覆盖率 | 评估集测试 | < 80% |
| 检索无结果率 | retrieval_logs 聚合 | > 5% |
| 文档过期率 | 文档 valid_until 字段 | > 10% 文档过期 |
| 用户负反馈率 | retrieval_logs.user_feedback | > 15% |

#### 3.8.2 健康检查 API

```python
# GET /api/knowledge/health?kb_id={kb_id}

{
    "status": "healthy" | "warning" | "critical",
    "metrics": {
        "total_documents": 150,
        "total_chunks": 4500,
        "stale_documents": 3,
        "no_result_rate_7d": 0.03,
        "avg_retrieval_score_7d": 0.72,
        "negative_feedback_rate_7d": 0.08
    },
    "alerts": [
        {"level": "warning", "message": "3 个文档超过 90 天未验证"}
    ]
}
```

---

## 四、数据库变更汇总

### 4.1 新增表

```sql
-- deploy/init-postgres.sql 新增

-- 知识库表
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    is_default BOOLEAN DEFAULT FALSE,   -- 是否为租户默认知识库
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_kb_tenant ON knowledge_bases(tenant_id);

-- 知识库成员权限表
CREATE TABLE IF NOT EXISTS kb_permissions (
    id SERIAL PRIMARY KEY,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id),
    subject_type TEXT NOT NULL,       -- 'tenant' | 'department' | 'user'
    subject_id TEXT NOT NULL,
    permission TEXT NOT NULL DEFAULT 'read',  -- 'read' | 'write' | 'admin'
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(kb_id, subject_type, subject_id)
);
CREATE INDEX idx_kb_perm_kb ON kb_permissions(kb_id);
CREATE INDEX idx_kb_perm_subject ON kb_permissions(subject_type, subject_id);

-- 检索日志表
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    session_id TEXT,
    query TEXT NOT NULL,
    rewritten_query TEXT,
    results_count INTEGER,
    top_score FLOAT,
    avg_score FLOAT,
    latency_ms INTEGER,
    reranked BOOLEAN DEFAULT FALSE,
    rerank_latency_ms INTEGER,
    user_feedback TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_retrieval_logs_tenant ON retrieval_logs(tenant_id, created_at DESC);

-- 评估数据集表
CREATE TABLE IF NOT EXISTS evaluation_sets (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    test_cases JSONB NOT NULL,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 评估结果表
CREATE TABLE IF NOT EXISTS evaluation_results (
    id SERIAL PRIMARY KEY,
    eval_set_id INTEGER REFERENCES evaluation_sets(id),
    precision_at_5 FLOAT,
    recall_at_10 FLOAT,
    ndcg_at_10 FLOAT,
    hit_rate FLOAT,
    faithfulness FLOAT,
    answer_relevancy FLOAT,
    total_queries INTEGER,
    config_snapshot JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 4.2 现有表变更

```sql
-- deploy/db_update.sql 新增

-- 2026-05-28，documents 表增加 kb_id 字段，关联知识库
ALTER TABLE documents ADD COLUMN IF NOT EXISTS kb_id INTEGER REFERENCES knowledge_bases(id);

-- 2026-05-28，documents 表增加内容哈希字段，用于文档同步变更检测
ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash TEXT;

-- 2026-05-28，documents 表增加过期检测字段
ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMP;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP;
```

---

## 五、前端变更

### 5.1 知识库管理页面

新增知识库管理界面（可复用现有 BaseCard/BaseTable 组件）：

- 知识库列表页：展示所有知识库，支持创建/编辑/删除
- 知识库详情页：展示知识库内的文档列表（复用现有文档列表逻辑）
- 权限设置弹窗：设置知识库的访问权限（租户/部门/用户）

### 5.2 检索调试工具

在管理后台增加检索调试功能：

- 输入查询 → 查看改写后的 query → 查看检索结果 → 查看 rerank 排序
- 用于运营人员调试知识库质量

### 5.3 质量仪表盘

在现有仪表盘页面增加知识库质量卡片：

- 检索成功率趋势
- 平均检索分数趋势
- 用户反馈分布

---

## 六、配置变更

`configs/config.yaml` 新增知识库增强配置：

```yaml
knowledge:
  # 现有配置保持不变...

  # Rerank 重排序
  rerank:
    enabled: true
    provider: "llm"             # "llm" | "cross_encoder" | "cohere"
    top_k: 5
    max_candidates: 20
    timeout_ms: 3000
    fallback_on_error: true

  # 查询改写
  query_rewriting:
    enabled: true
    max_history_turns: 5
    max_latency_ms: 1000
    fallback_on_error: true

  # 检索日志
  retrieval_logging:
    enabled: true
    sample_rate: 1.0             # 日志采样率（0.0-1.0），高流量时可降低

  # 质量评估
  evaluation:
    auto_schedule: false         # 是否自动定期评估
    schedule_cron: "0 2 * * 0"   # 每周日凌晨 2 点
```

---

## 七、实施计划

### 第一阶段：核心检索增强（4-5 周）

| 周次 | 任务 | 交付物 |
|------|------|--------|
| W1 | Rerank 引擎实现 + Pipeline 协调器 | `src/knowledge/reranker/` + `pipeline.py` |
| W1 | 数据库变更（新表 + 字段） | `init-postgres.sql` + `db_update.sql` |
| W2 | 文档级权限实现 | `src/knowledge/permissions.py` + API |
| W2 | 检索日志记录 | `retrieval_logs` 表写入逻辑 |
| W3 | Query Rewriting 实现 | `query_rewriter.py` + KnowledgeBaseTool 集成 |
| W3 | Pipeline 整合测试 | 集成测试用例 |
| W4 | 前端：知识库管理页面 | 知识库 CRUD + 权限设置 UI |
| W4 | 前端：检索调试工具 | 调试页面 |
| W5 | 质量评估服务 + 仪表盘 | `evaluation/` + 前端图表 |
| W5 | 全量回归测试 + 性能测试 | 测试报告 |

### 第二阶段：高级能力（按需启动）

| 任务 | 依赖 | 预估周期 |
|------|------|---------|
| Cross-Encoder Reranker 集成 | 第一阶段完成 | 2 周 |
| Text-to-SQL 基础版 | 意图分类器 | 4-6 周 |
| 文档自动同步 | 同步框架 | 3-4 周 |
| 知识库健康监控 | 检索日志积累 | 2 周 |

---

## 八、风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM Rerank 增加延迟 | 检索 p95 从 200ms 增至 500-700ms | Selective Rerank：高置信结果跳过 rerank；超时降级 |
| Query Rewriting 失败 | 改写错误导致检索不到结果 | fallback 到原始查询；记录改写日志便于调试 |
| 权限过滤性能 | 大量文档 ID 的 IN 查询可能慢 | 限制单次过滤 max 5000 doc；使用 PostgreSQL ANY 数组 |
| 检索日志写入压力 | 高频检索场景下日志表膨胀 | 采样率可调（sample_rate）；定期归档 |
| documents 表 kb_id 迁移 | 现有文档需要关联到知识库 | 自动创建租户默认知识库，迁移脚本将现有文档归入默认库 |
| 多 Worker 权限缓存不一致 | 进程内缓存可能过期 | 首期不缓存（直接查库），后续迁移到 Redis |

---

## 九、与差距分析报告的映射

本文档对应 [差距分析报告 §2.3 知识库能力增强](../../research/enterprise-agent-infrastructure-gap-analysis.md#23-知识库能力增强p0) 中列出的 5 项建设需求：

| 差距分析需求 | 本文档章节 | 实施阶段 |
|-------------|-----------|---------|
| Rerank 重排序 | §3.1 | 第一阶段 W1 |
| 文档级权限控制 | §3.2 | 第一阶段 W2 |
| 知识库健康度检测 | §3.8 | 第二阶段 |
| 结构化数据问答（Text-to-SQL） | §3.6 | 第二阶段 |
| 文档自动同步 | §3.7 | 第二阶段 |
