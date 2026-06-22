# 酒店知识库搜索工具设计文档

> 版本: v1.0 | 创建日期: 2026-06-22 | 状态: 已实现

## 1. 背景

### 1.1 当前问题

旅游顾问子智能体在对话中无法回答客户关于酒店价格的问题：

- **酒店信息可检索**：通用 `knowledge_base_search` 能搜到酒店（`source_type='hotel_resource'`，chunk_index=0 的酒店信息摘要已向量化）
- **酒店价格检索不到**：价格明细表存于 chunk_index=1（**未向量化**），通用检索不会返回它
- **报价 skill 内部已有酒店检索**：`src/skills/travel-quote/scripts/hotel_retriever.py` 的 `HotelRetriever` 能查酒店 + 价格表，但它只服务于报价流程，子智能体对话阶段调不到

### 1.2 设计目标

1. 新增 `hotel_search` 工具，让子智能体在对话中回答"XX 酒店多少钱""贵阳有哪些酒店"等问题
2. 返回酒店信息摘要 + **价格明细表**（价格是通用检索拿不到的核心数据）
3. 通过 `inherit: true` 自动对旅游顾问子智能体可用
4. 形态仿照已有的 `attraction_search` 工具，降低维护成本

### 1.3 与景点搜索工具的关系

| 维度 | `attraction_search` | `hotel_search`（本工具） |
|------|---------------------|--------------------------|
| source_type | `attraction_resource` | `hotel_resource` |
| 附加表 | `project_table`（chunk_index=2） | `price_table`（chunk_index=1） |
| 检索算法 | 纯向量 | 名称优先 + 向量兜底 |
| 表单结构 | 仿 `BaseTool` | 仿 `BaseTool`（一致） |

## 2. 方案设计

### 2.1 新增 `hotel_search` 工具

**文件位置**: `src/tools/knowledge/hotel_search_tool.py`

**为什么不复用 `HotelRetriever`**：与景点工具的设计先例一致——`HotelRetriever` 是报价 skill 的内部组件，且 skill 脚本用相对导入（`from hotel_retriever import ...`），外部导入不可靠。工具内自包含实现，避免跨组件耦合。

### 2.2 检索算法：名称优先 + 向量兜底

复刻报价 skill `HotelRetriever.search` 的两段式策略：

1. **名称匹配优先**：`chunks.text ILIKE '%query%'`（限 `source_type='hotel_resource'` + `chunk_index=0` + tenant 过滤）
   - 精确命中具体酒店（如客户问"亚朵多少钱"，直接按名称命中）
2. **向量语义兜底**：名称无结果时，走 pgvector cosine distance（`embedding <=> query::vector`）
   - 处理城市名（"贵阳"）、区域名、特色词（"经济型""带泳池"）等语义查询

**为什么不用景点工具的纯向量**：酒店咨询以名称查询为主（"XX酒店多少钱"），纯向量可能把相似酒店排在目标酒店之前；名称优先更精准。报价 skill 已验证此策略对酒店有效。

### 2.3 价格明细表

命中酒店后，批量查 chunk_index=1 的 `price_table`（避免 N+1），作为 `price_table` 字段返回全文。价格表是 markdown 表（`房型 | 客户类型 | 价格 | 含早 | 适用日期`），体积小，无需截断。

## 3. 工具接口设计

### 3.1 输入参数

```python
class HotelSearchInput(BaseModel):
    query: str = Field(..., description="搜索关键词（酒店名、城市名或区域名）")
    top_k: Optional[int] = Field(20, description="返回结果数量，默认20")
```

### 3.2 输出格式

```json
{
  "success": true,
  "results": [
    {
      "doc_id": 123,
      "title": "酒店：贵阳大十字广场亚朵酒店",
      "info": "酒店信息摘要（截断到500字符）...",
      "price_table": "房型 | 客户类型 | 价格 | 含早 | 适用日期\n高级城景房型 | 团队 | 450 | 含双早 | 5月1日-6月30日\n...",
      "score": null,
      "region": "贵阳",
      "diamond_level": "4钻"
    }
  ],
  "count": 5
}
```

**字段说明**：
- `doc_id`：知识库文档 ID
- `title`：酒店名称（文档标题）
- `info`：酒店信息摘要（chunk_index=0，截断 500 字），含价格区间/星级/房型/含早等
- `price_table`：价格明细表（chunk_index=1，全文）——本工具的核心价值
- `score`：名称命中为 `null`，向量命中为 `1 - cosine_distance`
- `region` / `diamond_level`：从 `documents.metadata` 提取（`sub_region` / `diamond_level`）

### 3.3 tenant_id 解析

与景点工具一致：
- 优先 Agent 注入（`set_tenant_id`，子智能体线程场景，ContextVar 不可用）
- 其次 ContextVar（`get_current_tenant_id`，HTTP 请求场景）
- 都拿不到时返回 `{"success": False, "error": "无法确定租户ID"}`

## 4. 实施步骤

### Step 1: 创建工具文件

**新增**: `src/tools/knowledge/hotel_search_tool.py`（继承 `BaseTool`，实现两段式检索 + 价格表批量查询）

### Step 2: 注册工具 + 注入 tenant_id

**修改**: `src/core/agent.py`
- `_register_builtin_tools()` 中注册 `HotelSearchTool()`
- 主请求路径 + 子智能体路径的 tenant_id 注入元组加入 `"hotel_search"`

### Step 3: 更新 SUBAGENT.md

**修改**: `subagents/travel-consultant/SUBAGENT.md`
- 工具的用法（输入参数、返回字段、何时调用）由工具自身 `description` 自解释，不在 SUBAGENT.md 重复
- 仅在行为约束第 10 条「行程中不出现价格」中追加一个例外子句：客户直接问酒店价格时可用 `hotel_search` 的价格明细表据实回答，但仍不得写入行程（消解该约束与新工具的冲突）

### Step 4: 文档与测试

- 本设计文档 + 开发计划 `plans/plan-hotel-search-tool.md`
- 登记 `docs/ideas.md`
- 单元测试 `tests/integration/test_hotel_search_tool.py`（真实 DB 集成测试：连 `.env` 的 DATABASE_URL，动态发现有 hotel_resource 数据的租户，验证名称命中 + price_table + 向量兜底；仅 stub 外边界 `_embed`）

## 5. 涉及文件

| 文件 | 操作 |
|------|------|
| `src/tools/knowledge/hotel_search_tool.py` | **新增** |
| `src/core/agent.py` | 修改（注册 + 2 处 tenant_id 注入） |
| `subagents/travel-consultant/SUBAGENT.md` | 修改（约束第 10 条追加酒店价格例外子句） |
| `tests/integration/test_hotel_search_tool.py` | **新增**（真实 DB 集成测试） |

## 6. 验证方案

1. **集成测试**：`venv/Scripts/python.exe -m pytest tests/integration/test_hotel_search_tool.py`（真实 DB：名称命中 + price_table、向量兜底路径、租户隔离、错误分支）
2. **工具注册核对**：启动后 `hotel_search` 在工具列表中，且旅游顾问子智能体继承
3. **端到端**（需真实数据 + 凭证）：发送"贵阳有哪些酒店？大概多少钱"→ 验证调用 `hotel_search` 并据 `price_table` 回答；发送行程类请求 → 验证行程正文不出现酒店价格（约束 #10 仍成立）
