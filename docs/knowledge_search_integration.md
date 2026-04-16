# 企业知识库搜索集成指南

在智能体工作流中直接调用 `search_documents` 方法（非 HTTP 接口），实现知识库检索能力集成。

## 方法签名

```python
# 来源：src/knowledge/service.py - KnowledgeBaseService
async def search_documents(
    self,
    query: str,
    user_id: Optional[int] = None,
    top_k: int = 10
) -> Dict[str, Any]
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | str | 是 | 搜索关键词或用户问题 |
| `user_id` | int | 否 | 用户 ID（权限控制，暂未实现） |
| `top_k` | int | 否 | 返回结果数量，默认 10 |

### 返回值结构

```python
{
    "success": True,                        # 是否成功
    "results": [                            # 搜索结果列表
        {
            "doc_id": 1,                    # 文档 ID
            "chunk_id": 42,                 # 文本块 ID
            "text": "差旅报销标准：...",      # 文本块内容
            "title": "员工手册2024.pdf",     # 文档标题
            "file_type": "pdf",             # 文件类型
            "file_path": "./uploads/...",   # 文件路径
            "score": 0.8523                 # 相关度分数（0-1）
        },
        ...
    ],
    "count": 3                              # 结果总数
}

# 失败时：
{
    "success": False,
    "error": "搜索失败，请稍后重试",
    "debug": "详细错误信息",
    "results": [],
    "count": 0
}
```

## 调用方式

### 方式一：通过 KnowledgeBaseService 单例（推荐）

`knowledge_service` 是全局单例，已在 `src/knowledge/service.py` 底部实例化。

```python
from src.knowledge.service import knowledge_service

# 异步调用
result = await knowledge_service.search_documents(
    query="差旅报销标准是什么",
    user_id=None,  # 可传入用户 ID 做权限过滤
    top_k=5
)

if result["success"]:
    for item in result["results"]:
        print(f"[{item['title']}] (相关度: {item['score']})")
        print(f"  {item['text'][:200]}...")
else:
    print(f"搜索失败: {result['error']}")
```

### 方式二：通过 KnowledgeBaseTool（工具层）

如果需要和 Agent 的工具调用机制保持一致，可以通过已注册的 `KnowledgeBaseTool` 调用。

```python
from src.tools.knowledge.knowledge_base_tool import KnowledgeBaseTool

tool = KnowledgeBaseTool()
result = await tool.execute(query="公司年假制度", top_k=5)

# 返回结构略有不同（无 title/file_type/file_path，使用 doc_title）：
# {"success": True, "results": [{"text": ..., "doc_title": ..., "score": ...}], "count": N}
```

### 方式三：直接使用 HybridRetriever（底层）

需要自定义检索逻辑时，可直接使用混合检索器。

```python
import sqlite3
from src.knowledge.retriever.hybrid_retriever import HybridRetriever
from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
from src.knowledge.vector_db.vector_db import VectorDBSQLite
from src.config.settings import settings

# 初始化
db_path = "./aid_work_agent.db"
conn = sqlite3.connect(db_path, check_same_thread=False)
conn.enable_load_extension(True)
conn.execute("PRAGMA journal_mode=WAL")

qwen_keys = settings.llm.qwen.get_effective_keys()
vector_db = VectorDBSQLite(db_path=db_path, dimension=1024, conn=conn)
embedding_client = TextEmbeddingV3Client(api_key=qwen_keys[0])
retriever = HybridRetriever(vector_db=vector_db, embedding_client=embedding_client, conn=conn)

# 检索（返回原始结果，无文档标题）
results = await retriever.retrieve(query="绩效考核流程", top_k=10)
```

## 在智能体工作流中集成

### 场景一：在 Agent 的工具执行逻辑中加入知识库检索

在 Agent 循环中，可以在 LLM 调用工具前或工具执行后，自动检索知识库补充上下文：

```python
# src/core/agent.py - process_message 方法中

from src.knowledge.service import knowledge_service

async def _enrich_with_knowledge(self, query: str, user_id=None, top_k=3):
    """用知识库检索结果增强 Agent 上下文"""
    result = await knowledge_service.search_documents(
        query=query,
        user_id=user_id,
        top_k=top_k
    )

    if not result["success"] or not result["results"]:
        return None

    # 拼接检索结果为上下文文本
    context_parts = []
    for item in result["results"]:
        context_parts.append(
            f"【{item['title']}】(相关度: {item['score']})\n{item['text']}"
        )

    return "\n\n---\n\n".join(context_parts)
```

### 场景二：在子智能体（SubAgent）中调用

子智能体在执行专业任务时，先检索知识库获取企业规范或参考资料：

```python
# 在子智能体的任务执行逻辑中

async def execute_task(task_description: str, user_id=None):
    # 1. 先搜索相关知识库内容
    result = await knowledge_service.search_documents(
        query=task_description,
        user_id=user_id,
        top_k=5
    )

    knowledge_context = ""
    if result["success"] and result["results"]:
        knowledge_context = "\n\n## 企业知识库参考资料\n\n"
        for item in result["results"]:
            knowledge_context += f"- **{item['title']}**: {item['text'][:300]}\n"

    # 2. 将知识库内容注入 LLM prompt
    prompt = f"{task_description}\n{knowledge_context}"

    # 3. 调用 LLM 生成回复
    response = await llm_gateway.chat(prompt)
    return response
```

### 场景三：在 API 路由中组合调用

在业务接口中同时调用知识库搜索和其他服务：

```python
from src.knowledge.service import knowledge_service

@router.post("/api/v1/agent_with_knowledge")
async def chat_with_knowledge(request: ChatRequest, http_request=None):
    # 获取用户信息
    user_id = auth.get_current_user(http_request).get("user_id") if http_request else None

    # 并行执行：Agent 对话 + 知识库检索
    import asyncio
    agent_task = asyncio.create_task(
        master_agent.process_message(request.message, request.session_id)
    )
    search_task = asyncio.create_task(
        knowledge_service.search_documents(query=request.message, user_id=user_id, top_k=3)
    )

    agent_response = await agent_task
    search_result = await search_task

    return {
        "success": True,
        "agent_response": list(agent_response),
        "knowledge_results": search_result.get("results", [])
    }
```

### 场景四：在 Skill 脚本中调用

在 Skill 的可执行脚本中直接调用知识库检索：

```python
# src/skills/expense-checker/scripts/check_expense.py

import asyncio
from src.knowledge.service import knowledge_service

async def main(expense_type: str):
    result = await knowledge_service.search_documents(
        query=f"{expense_type} 报销标准",
        top_k=3
    )

    if result["success"]:
        for item in result["results"]:
            print(f"来源: {item['title']}")
            print(f"内容: {item['text']}")
    else:
        print(f"检索失败: {result['error']}")

if __name__ == "__main__":
    asyncio.run(main("差旅"))
```

## 注意事项

1. **异步调用**：`search_documents` 是 `async` 方法，必须用 `await` 调用；在同步上下文中使用 `asyncio.run()` 或 `asyncio.to_thread()`。

2. **服务已就绪**：`knowledge_service` 全局单例在模块导入时自动创建，无需手动初始化。但确保 `API_KEYS` 环境变量已配置（用于生成查询向量）。

3. **结果过滤**：返回的 `score` 是 0-1 的相关度分数，建议设置阈值（如 0.3）过滤低质量结果：

   ```python
   relevant_results = [r for r in result["results"] if r["score"] >= 0.3]
   ```

4. **性能考量**：每次搜索需要调用 Embedding API 生成查询向量，耗时约 200-500ms。避免在循环中高频调用。

5. **数据库连接**：`knowledge_service` 内部管理数据库连接，调用方无需关心连接管理。
