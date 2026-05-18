# 旅游资源向量知识库设计方案

> 版本: v5.0 | 创建: 2026-05-12 | 最后更新: 2026-05-18 | 状态: 已完成
>
> v5.0 变更：对齐实际实现（景点 3 chunk 结构、门票/项目独立 LLM 提取、teacher_subtotal、多城市酒店）

## 资源类型概览

本方案覆盖两类旅游资源，统一使用向量知识库方案：

| 资源类型 | source_type | 旧表（已删除） | Chunk 0（向量化） | Chunk 1（不向量化） | Chunk 2（不向量化） |
|----------|-------------|-------------|-------------------|-------------------|-------------------|
| 酒店 | `hotel_resource` | bs_travel_quote_hotels + rooms | 酒店信息摘要 | 价格明细表 | — |
| 景点门票 | `attraction_resource` | bs_travel_quote_attractions + tickets | 景点信息摘要 | 门票价格明细表 | 游玩项目价格表 |

两者共享同一套基础设施（documents + chunks + chunks_vec），只是 source_type 不同。

---

# 第一部分：酒店资源

## 1. 方案概要

### 核心思路

用项目已有的**知识库系统**（documents + chunks + chunks_vec + HybridRetriever）存储酒店数据，替代关系型表。

每个酒店 = 一个 document，包含 **2 个 chunk（摘要）**：

| Chunk | 内容 | 向量化 | 用途 |
|-------|------|--------|------|
| **Chunk 0：酒店信息摘要** | 酒店名称、所在区域、星级、地址、主要房型 | **是**（embedding → chunks_vec） | 向量匹配选酒店 |
| **Chunk 1：价格明细表** | 一行一个价格：房型、团队价、团散价、适用日期 | **否**（仅纯文本存储在 chunks 表） | LLM 定位到酒店后取价格 |

### 数据流

```
用户上传 Excel
  → Agent 调用 Excel 工具读取
    → 逐行（每家酒店）调用 LLM 解析价格文本 → 生成两个摘要
      → 摘要1（酒店信息）→ embedding → 存入 chunks_vec（向量化）
      → 摘要2（价格明细）→ 仅存入 chunks 表（不向量化）
        → 搜索时：向量匹配摘要1选酒店 → 取摘要2给 LLM 解价格
```

### 为什么比关系型方案好

| 对比项 | 关系型宽表 | 向量知识库 |
|--------|-----------|-----------|
| 名称模糊匹配 | 需要 ILIKE/pg_trgm | 向量天然语义匹配 |
| "黄果树附近4钻酒店" | 需要区域映射表 | 向量直接理解 |
| 非结构化价格 | 存原始文本，每次调用 LLM | 预解析成表格，取价更精确 |
| 扩展性 | 加字段要改 DDL | 改摘要格式即可 |
| 复用基础设施 | 新建表+CRUD | 复用现有知识库系统 |

---

## 2. 数据结构设计

### 2.1 每个酒店 = 一个 Document

在 `documents` 表中，每家酒店存为一行：

```
documents:
  title: "酒店：贵阳大十字广场亚朵酒店"
  tenant_id: "tenant_xxx"
  source_type: "hotel_resource"
  metadata: {"type": "hotel", "region": "贵阳", "sub_region": "云岩区", "diamond_level": "4钻"}
```

**`source_type: "hotel_resource"`** 用于区分酒店数据和普通知识库文档，查询时可以按此过滤。

### 2.2 Chunk 1：酒店信息摘要

**格式**（纯文本，用于 embedding）：

```
酒店名称：贵阳大十字广场亚朵酒店
所在区域：贵州省 贵阳市 云岩区
星级等级：4钻
地址：贵阳云岩区中华中路1号峰会国际大厦B座1楼
主要房型：高级城景标间、单人间
房间总数：42间
价格区间：450-450元/间
是否含早：部分含早
特殊说明：团队团散同价
```

**设计原则**：
- 字段名用中文，embedding 模型对中文语义理解更好
- 包含所有需要用于筛选的信息（区域、星级、房型、价格区间）
- "是否含早"、"特殊说明"等从价格文本中提取

### 2.3 Chunk 1：价格明细表

**格式**（纯文本表格，用于 LLM 取价）：

```
贵阳大十字广场亚朵酒店 价格明细表

高级城景标间 | 团队 | 450 | 含早 | 适用日期：全年
高级城景标间 | 团散 | 450 | 含早 | 适用日期：全年
高级城景标间 | 团队 | 门市价8.5折 | 含早 | 适用日期：7月1日-8月31日

艺龙酒店(贵阳喷水池店) 价格明细表
心悦双床房 | 团队 | 288 | 不含早 | 适用日期：5月1日-5月31日（不含五一假期）
心悦双床房 | 团队 | 328 | 不含早 | 适用日期：5月1日-5月4日（五一假期）
心悦双床房 | 团队 | 308 | 不含早 | 适用日期：6月1日-6月30日
心悦双床房 | 团散 | 328 | 不含早 | 适用日期：6月1日-6月30日（团散=团队价+20）
心悦双床房 | 团队 | 328 | 不含早 | 适用日期：7月1日-7月11日
心悦双床房 | 团队 | 568 | 不含早 | 适用日期：7月12日-8月31日
心悦双床房 | 团散 | 428 | 不含早 | 适用日期：7月12日-7月31日
心悦双床房 | 团散 | 628 | 不含早 | 适用日期：8月1日-8月31日
心悦双床房 | 团队 | 288 | 不含早 | 适用日期：9月1日-9月30日
心悦双床房 | 团队 | 308 | 不含早 | 适用日期：10月1日-10月31日
心悦双床房 | 团散 | 328 | 不含早 | 适用日期：10月1日-10月31日（团散=团队价+20）

镇宁凌悦大酒店 价格明细表
山景高雅标间 | 团队 | 268 | 不含早 | 适用日期：全年淡季
山景高雅标间 | 团散 | 298 | 不含早 | 适用日期：全年淡季
山景高雅标间 | 团队 | 398 | 不含早 | 适用日期：7月11日-8月19日（旺季）
山景高雅标间 | 团散 | 428 | 不含早 | 适用日期：7月11日-8月19日（旺季）
山景高雅标间 | 团队 | 298 | 不含早 | 适用日期：7月1日-7月10日（旺季过渡）
山景高雅标间 | 团队 | 328 | 不含早 | 适用日期：8月20日-8月31日（旺季过渡）
特殊政策：陪同房180元/间，执行8免半，16免1

西江三春里·孟度假酒店 价格明细表
基础标间 | 团队 | 618 | 不含早 | 适用日期：淡季（5月-6月，9月）
基础标间 | 团散 | 668 | 不含早 | 适用日期：淡季（团散+50）
基础标间 | 团队 | 768 | 不含早 | 适用日期：7月1日-7月9日
基础标间 | 团队 | 1188 | 不含早 | 适用日期：7月10日-8月25日（暑期旺季）
基础标间 | 团散 | 1238 | 不含早 | 适用日期：7月10日-8月25日（团散+50）
中级单间 | 团队 | 668 | 不含早 | 适用日期：淡季
高级标间 | 团队 | 728 | 不含早 | 适用日期：淡季
```

**一行一个价格**，格式：`房型 | 客户类型 | 价格 | 含早 | 适用日期：具体日期范围`

**关键设计原则**：
- **适用日期必须是具体的日期范围**（如"7月10日-8月25日"），而不是笼统的"7-8月"或月份名
- 不同酒店的时间维度可能完全不同：有的按月、有的按具体日期段、有的按节假日
- LLM 解析时将所有模糊的日期描述转换为具体日期范围
- 节假日单独列出行（如"五一假期"、"国庆假期"）
- 特殊政策（免房、司陪房等）附在价格表末尾

### 2.4 为什么分两个 Chunk 且只向量化 Chunk 0

| | Chunk 0（酒店信息） | Chunk 1（价格明细） |
|---|---|---|
| **目的** | 选酒店 | 取价格 |
| **搜索方式** | 向量匹配（embedding） | 定位后直接取文本给 LLM |
| **向量化** | 是 | **否** |
| **文本长度** | 短（100-200字） | 较长（200-1000字） |
| **更新频率** | 低（酒店信息不变） | 高（价格经常变） |

只向量化 Chunk 0 的好处：
- 向量搜索纯粹匹配酒店属性，不被价格数据干扰
- 价格变化时只需更新 Chunk 1 文本，不需要重新生成 embedding
- 减少一半的 embedding API 调用（省成本）
- 价格取用是精确匹配任务（房型+日期→价格），不需要向量搜索

---

## 3. 酒店选择算法

### 3.1 三级匹配策略

```
输入：用户需求（酒店名/星级/景点位置/出行日期）
  │
  ▼
Level 1：精确名称匹配（如果用户指定了酒店名）
  → FTS/ILIKE 搜索 chunk 中的酒店名
  → 找到则直接返回，跳过后续
  │
  ▼
Level 2：向量语义搜索
  → 构造查询："贵阳 云岩区 4钻酒店 靠近大十字广场"
  → 在 hotel_resource 文档中搜索 chunk_index=0（酒店信息摘要）
  → 返回 top 5 候选
  │
  ▼
Level 3：LLM 精选 + 取价
  → 将 3-5 家候选酒店的价格明细交给 LLM
  → LLM 根据房型、日期、人数选出最优价格
  → 返回选定酒店 + 价格
```

### 3.2 精确名称匹配优先

用户说"我要住亚朵酒店"，需要确保这家酒店排第一：

```python
def search_hotel(tenant_id, query, top_k=5):
    # Step 1: 尝试精确名称匹配
    # 在 chunks 中搜索 chunk_index=0（酒店信息摘要）
    # WHERE doc_id IN (SELECT id FROM documents WHERE source_type='hotel_resource' AND tenant_id=?)
    # AND text ILIKE '%亚朵%'
    exact_matches = fts_search_hotel_name(tenant_id, query)

    if exact_matches:
        return exact_matches  # 精确匹配直接返回

    # Step 2: 向量语义搜索
    results = hybrid_search(tenant_id, query, source_type="hotel_resource", chunk_index=0)
    return results
```

### 3.3 景点就近匹配

向量搜索天然支持语义就近。构造查询时包含景点信息：

- 用户说"黄果树瀑布附近的酒店" → 查询 = "安顺 镇宁 黄果树 酒店住宿"
- 用户说"苗寨游玩，住哪好" → 查询 = "黔东南 苗寨 西江 酒店住宿"
- 用户说"贵阳的4钻酒店" → 查询 = "贵阳 4钻 酒店"

embedding 模型会理解"黄果树瀑布"和"镇宁/安顺"的地理关系，因为 Chunk 1 中包含了区域信息。

---

## 4. 价格取用流程

### 4.1 定位到酒店后取价

```
已确定酒店：贵阳大十字广场亚朵酒店（doc_id=123）
出行日期：2026-07-15
房型需求：标间
客户类型：团队

  │
  ▼
取出该酒店 chunk_index=1 的价格明细表
  │
  ▼
LLM 解析价格明细表：
  输入：价格明细表 + 出行日期 + 房型 + 客户类型
  输出：匹配的价格行 + 最终报价
  │
  ▼
结果：高级城景标间 | 团队 | 门市价8.5折 | 7-8月
  → Agent 换算：门市价约520 × 0.85 ≈ 442元/间
```

### 4.2 价格解析 Prompt

```
你是酒店报价助手。以下是某酒店的价格明细表，请根据出行信息找到匹配的价格行。

价格明细表：
{price_table}

出行信息：
- 入住日期：{date}
- 房型需求：{room_type}
- 客户类型：{customer_type}（团队/团散）
- 入住天数：{nights}晚

请从价格明细表中找到完全匹配的一行，返回JSON：
{
  "room_type": "房型",
  "customer_type": "团队/团散",
  "price_per_room": 450,
  "includes_breakfast": true,
  "applicable_date_range": "全年",
  "notes": "补充说明（如有）"
}
```

---

## 5. 数据导入流程

### 5.1 完整流程

```
用户上传 Excel（如 酒店资源表20260511.xlsx）
  │
  ▼
Agent 调用 Excel 工具读取（复用 src/tools/excel/）
  │
  ▼
逐 Sheet 逐行处理每家酒店：
  │
  ├─ 1. 提取基本信息（区域、钻级、名称、地址）
  │     → 直接从 Excel 列映射
  │
  ├─ 2. 调用 LLM 解析价格文本 → 生成价格明细表
  │     → 输入：该酒店所有价格相关的原始文本（不按月份拆分，整行价格列原样传入）
  │     → 输出：格式化的价格明细表（一行一价格，每行含适用日期范围）
  │
  ├─ 3. 生成 Chunk 1（酒店信息摘要）
  │     → 格式化基本信息
  │
  ├─ 4. 生成 Chunk 2（价格明细表）
  │     → 使用 LLM 解析结果
  │
  ├─ 5. 生成 Embedding（只有 Chunk 0 做向量化）
  │
  └─ 6. 存入知识库：
        → INSERT INTO documents (title, tenant_id, source_type='hotel_resource', metadata)
        → INSERT INTO chunks (doc_id, chunk_index=0, text=酒店信息)  ← 向量化
        → INSERT INTO chunks (doc_id, chunk_index=1, text=价格明细)  ← 不向量化
        → INSERT INTO chunks_vec (chunk_id_0, embedding)              ← 只有 Chunk 0
```

### 5.2 LLM 解析价格的 Prompt

```
你是一个酒店价格数据整理助手。请将以下酒店的非结构化价格信息整理为标准化的价格明细表。

酒店名称：{hotel_name}
区域：{region}

原始价格信息：
{raw_price_text}

请整理为以下格式，一行一个价格：
房型 | 客户类型（团队/团散） | 价格（元/间/夜） | 是否含早 | 适用日期：具体日期范围

要求：
1. 每个价格变体单独一行（团队价和团散价分开，含票和不含票分开）
2. 不同日期段的价格必须分开（如"7.1-7.9"和"7.10-8.25"是两行）
3. 不同房型分开
4. **适用日期必须是具体的日期范围**（如"适用日期：7月10日-8月25日"），不能用"旺季"、"淡季"等模糊描述
5. 节假日加价单独列出（如"适用日期：10月1日-10月7日（国庆）"）
6. 如果原文有"团散+X"的规则，直接计算出团散价格
7. 如果原文有"门市价X折"，保留原文形式（如"门市价8.5折"），不换算
8. 特殊政策（免房、司陪房、含票套餐等）附在末尾
9. 如果某个月份没有价格信息，跳过不写
10. 如果适用日期覆盖全年，写"适用日期：全年"

输出格式示例：
高级城景标间 | 团队 | 450 | 含早 | 适用日期：全年
高级城景标间 | 团队 | 门市价8.5折 | 含早 | 适用日期：7月1日-8月31日
心悦双床房 | 团队 | 288 | 不含早 | 适用日期：5月1日-5月31日（不含五一假期）
心悦双床房 | 团队 | 328 | 不含早 | 适用日期：5月1日-5月4日（五一假期）
```

**注意**：`{raw_price_text}` 是从 Excel 中提取的该酒店所有价格相关的原始文本，不按月份拆分。LLM 负责从中解析出所有价格行和对应的适用日期范围。不同酒店的价格维度可能完全不同（按月、按日期段、按节假日、按季节），LLM 统一转为"适用日期"格式。

---

## 6. 与现有知识库系统的集成

### 6.1 不改动现有组件

- **HybridRetriever 不改动**：现有知识库检索器保持不变
- **知识库服务不改动**：现有 upload/search 流程保持不变

### 6.2 新建 HotelRetriever

独立的酒店检索器，复用底层基础设施（embedding client、chunks_vec 表），但有自己的搜索逻辑：

```python
class HotelRetriever:
    """酒店专用检索器"""

    def __init__(self, embedding_client, conn):
        self.embedding_client = embedding_client
        self.conn = conn

    async def search_by_name(self, tenant_id: str, name_query: str, top_k: int = 5) -> List[Dict]:
        """精确/模糊名称匹配 — 优先使用"""
        # 在 chunks 中搜索 chunk_index=0（酒店信息摘要）
        # JOIN documents WHERE source_type='hotel_resource' AND tenant_id=?
        # AND text ILIKE '%name_query%'
        ...

    async def search_by_vector(self, tenant_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """向量语义搜索"""
        # 1. embed(query) → query_embedding
        # 2. 向量搜索 chunks_vec，JOIN chunks + documents 过滤：
        #    WHERE d.source_type='hotel_resource'
        #    AND d.tenant_id=?
        #    AND c.chunk_index=0  -- 只搜酒店信息摘要
        # 3. 返回 top_k 酒店文档
        ...

    async def search(self, tenant_id: str, query: str, top_k: int = 5) -> List[Dict]:
        """组合搜索：精确名称优先 → 向量搜索"""
        # Step 1: 尝试名称匹配
        name_results = await self.search_by_name(tenant_id, query, top_k)
        if name_results:
            return name_results

        # Step 2: 向量语义搜索
        return await self.search_by_vector(tenant_id, query, top_k)

    def get_price_table(self, doc_id: int) -> Optional[str]:
        """取出酒店的价格明细表（chunk_index=1）"""
        # SELECT text FROM chunks WHERE doc_id=? AND chunk_index=1
        ...

    def get_hotel_info(self, doc_id: int) -> Optional[str]:
        """取出酒店信息摘要（chunk_index=0）"""
        # SELECT text FROM chunks WHERE doc_id=? AND chunk_index=0
        ...
```

### 6.3 数据入库流程

导入脚本直接操作 `documents` + `chunks` + `chunks_vec` 表，不经过 `KnowledgeBaseService`：

```python
async def import_hotel(tenant_id, hotel_data):
    # 1. INSERT INTO documents (title, tenant_id, source_type='hotel_resource', metadata)
    #    RETURNING doc_id

    # 2. INSERT INTO chunks (doc_id, chunk_index=0, text=酒店信息摘要)
    #    RETURNING chunk_id_0

    # 3. INSERT INTO chunks (doc_id, chunk_index=1, text=价格明细表)
    #    RETURNING chunk_id_1
    #    注意：chunk_index=1 不存入 chunks_vec

    # 4. embedding = embed(酒店信息摘要)
    #    INSERT INTO chunks_vec (chunk_id_0, embedding)
```

---

## 7. 旧表处理

### 7.1 删除旧表

以下表及其相关代码将被完全删除：

| 删除项 | 说明 |
|--------|------|
| `bs_travel_quote_hotels` 表 | DDL 从 init-postgres.sql 和 db_update.sql 移除 |
| `bs_travel_quote_rooms` 表 | DDL 从 init-postgres.sql 和 db_update.sql 移除 |
| `generate.py` 中的 hotels/rooms DDL | `TABLE_DEFINITIONS` 中相关条目删除 |
| `generate.py` 中的 `calculate_hotel_cost()` | 替换为新的知识库版本 |
| `travel_quote.py` 中 hotels/rooms CRUD 端点 | 删除 |
| `HotelManager.vue` 中 rooms 相关代码 | 改造为知识库展示 |
| `travelQuote.ts` 中 rooms API | 删除 |

```python
def calculate_hotel_cost(items, tenant_id, hotel_doc_id, total_people,
                         couples, trip_days, start_date):
    """
    计算住宿费用（知识库方案）
    hotel_doc_id: 知识库中的文档 ID
    """
    # 1. 取出价格明细表（chunk_index=1）
    price_table = hotel_retriever.get_price_table(hotel_doc_id)

    # 2. 调用 LLM 从价格明细表中取价
    parsed = llm_parse_price(price_table, start_date, total_people, is_group=True)

    # 3. 后续排房逻辑不变
    room_price = parsed['price_per_room']
    ...
```

---

## 8. 前端交互

### 8.1 Excel 导入

前端上传 Excel → 调用导入 API → Agent 处理 → 入库 → 返回导入结果

### 8.2 酒店管理

前端可以：
- **搜索酒店**：输入关键词 → 调用向量搜索 → 展示匹配的酒店列表
- **查看详情**：点击酒店 → 展示 Chunk 1（酒店信息）+ Chunk 2（价格明细表）
- **重新导入**：上传新 Excel → 清除旧数据 → 重新入库

---

## 9. 实施步骤

### Phase 1：数据导入

1. 开发 `import_hotels.py` 导入脚本
   - 读取 Excel
   - 逐行调用 LLM 解析价格 → 生成两个 Chunk
   - 生成 Embedding
   - 存入 documents + chunks + chunks_vec
2. 用客户 Excel 测试导入

### Phase 2：酒店检索

1. 开发 `HotelRetriever`
2. 实现名称匹配 + 向量搜索 + 价格取用

### Phase 3：报价集成

1. 改造 `generate.py` 的 `calculate_hotel_cost()`
2. 更新 `SUBAGENT.md`

### Phase 4：前端

1. 酒店管理页面改造
2. Excel 导入功能

---

# 第二部分：景点门票资源

## A1. 方案概要

与酒店完全相同的向量知识库方案。

每个景点 = 一个 document（source_type='attraction_resource'），包含 **3 个 chunk**：

| Chunk | 内容 | 向量化 | 用途 |
|-------|------|--------|------|
| **Chunk 0：景点信息摘要** | 景点名称（含别名）、区域、类别、地址、游玩时长、景区交通 | **是** | 向量匹配选景点 |
| **Chunk 1：门票价格明细表** | 一行一个价格：票种、适用人群、价格、适用日期 | **否** | LLM 提取门票价格 |
| **Chunk 2：游玩项目价格表** | 一行一个项目：项目名、价格、计费方式 | **否** | LLM 匹配行程中的游玩活动 |

### 与酒店方案的区别

| | 酒店 | 景点门票 |
|---|---|---|
| source_type | `hotel_resource` | `attraction_resource` |
| 摘要中的关键信息 | 区域、星级、地址 | 区域、类别、别名、游玩时长 |
| 价格维度 | 房型 + 客户类型 + 日期 | 票种(成人/儿童/学生/老人/团体) + 日期 |
| 名称特殊性 | 名称相对固定 | **一个景点可能有多个名字**（如"黄果树大瀑布"="黄果树瀑布"="黄果树"） |

---

## A2. 数据结构设计

### A2.1 每个景点 = 一个 Document

```
documents:
  title: "景点：黄果树大瀑布"
  tenant_id: "tenant_xxx"
  source_type: "attraction_resource"
  metadata: {"type": "attraction", "region": "安顺", "category": "natural"}
```

### A2.2 Chunk 0：景点信息摘要

**格式**：

```
景点名称：黄果树大瀑布
别名：黄果树瀑布、黄果树、黄果树风景区
所在区域：贵州省 安顺市 镇宁县
景点类别：自然风光
地址：安顺市镇宁布依族苗族自治县黄果树镇
建议游玩时长：3-4小时
景区内交通：观光车 50元/人
开放时间：7:30-18:00（旺季），8:00-17:30（淡季）
```

**别名字段**：景点名称的多种叫法都列出来，向量搜索时能通过别名匹配。

### A2.3 Chunk 1：门票价格明细表

**格式**：

```
黄果树大瀑布 门票价格明细表

成人票 | 散客 | 160 | 含景区交通 | 适用日期：全年
成人票 | 团队(10人起) | 130 | 含景区交通 | 适用日期：全年
成人票 | 旅行社协议价 | 110 | 含景区交通 | 适用日期：全年
儿童票(1.2m-1.4m) | 散客 | 80 | 含景区交通 | 适用日期：全年
儿童票(1.2m以下) | 免费 | 0 | 含景区交通 | 适用日期：全年
学生票 | 散客 | 80 | 含景区交通 | 适用日期：全年（需学生证）
老人票(60-69岁) | 散客 | 免费 | 含景区交通 | 适用日期：全年
老人票(70岁以上) | 免费 | 0 | 含景区交通 | 适用日期：全年
军人票 | 免费 | 0 | 含景区交通 | 适用日期：全年（需军官证）
团体票(10人以上) | 团队 | 130 | 含景区交通 | 适用日期：全年
特殊政策：16免1，8免半

荔波小七孔 门票价格明细表
成人票+小七孔门票 | 散客 | 170 | 不含交通 | 适用日期：3月1日-11月30日（旺季）
成人票+小七孔门票 | 散客 | 120 | 不含交通 | 适用日期：12月1日-次年2月底（淡季）
成人票+景区交通 | 散客 | 170+40=210 | 含观光车 | 适用日期：3月1日-11月30日
儿童票(1.2m-1.5m) | 散客 | 85 | 不含交通 | 适用日期：旺季
儿童票(1.2m以下) | 免费 | 0 | 含交通 | 适用日期：全年
```

**一行一个价格**，格式：`票种 | 客户类型 | 价格 | 是否含交通 | 适用日期`

**关键设计原则**：
- 适用日期必须是具体日期范围
- 不同票种分开（成人/儿童/学生/老人/军人/团体）
- 散客价、团队价、协议价分开
- 特殊政策（免票规则、团体优惠）附在末尾
- 如果门票和景区交通是分开的，分别列出

---

## A3. 景点选择算法

与酒店类似的三级策略：

```
Level 1：精确名称/别名匹配
  → 搜索 Chunk 0 中的"别名"字段
  → "黄果树"能匹配到"黄果树大瀑布"（因为别名中有"黄果树"）

Level 2：向量语义搜索
  → 查询 "安顺 最大的瀑布 景点" → 向量匹配到黄果树瀑布的景点摘要
  → 查询 "苗寨 民族风情 景点" → 匹配到西江千户苗寨

Level 3：LLM 精选 + 取价
  → 取出匹配景点的门票价格明细表
  → LLM 根据人群构成（成人X人/儿童X人/学生X人）+ 出行日期取价
```

### AttractionRetriever

```python
class AttractionRetriever:
    """景点门票专用检索器"""

    def search_by_name(self, tenant_id, name_query, top_k=5):
        """精确名称/别名匹配（ILIKE）"""
        ...

    def search_by_vector(self, tenant_id, query, top_k=5):
        """向量语义搜索（chunk_index=0）"""
        ...

    def search(self, tenant_id, query, top_k=5):
        """组合搜索：名称匹配优先 → 向量搜索"""
        ...

    def get_attraction_info(self, doc_id):
        """取出景点信息摘要（chunk_index=0）"""
        ...

    def get_ticket_table(self, doc_id):
        """取出门票价格明细表（chunk_index=1）"""
        ...

    def get_project_table(self, doc_id):
        """取出游玩项目价格表（chunk_index=2）"""
        ...
```

---

## A4. 门票取用流程

```
已确定景点：黄果树大瀑布（doc_id=456）
出行日期：2026-07-20
人群：成人25人，儿童3人，学生2人，老人2人

  │
  ▼
取出该景点 chunk_index=1 的门票价格明细表
  │
  ▼
LLM 解析：
  输入：价格明细表 + 出行日期 + 人群构成
  输出：
    - 成人票 团队 130元 × 25人 = 3250元
    - 儿童票 散客 80元 × 3人 = 240元
    - 学生票 散客 80元 × 2人 = 160元（需学生证）
    - 老人票 免费 × 2人 = 0元
    - 特殊政策：16免1（团队25人免1张票）
```

### 门票取价 Prompt

```
你是景点门票报价助手。以下是某景点的门票价格明细表，请根据出行信息计算门票费用。

门票价格明细表：
{ticket_table}

出行信息：
- 出行日期：{date}
- 成人：{adults}人
- 儿童：{children}人
- 学生：{students}人
- 老人：{elders}人
- 总人数：{total}人
- 是否团队：{is_group}

请从价格明细表中找到每个人群类型匹配的价格，计算总费用。
注意特殊政策（如"16免1"等免票规则）。
返回JSON：
{
  "items": [
    {"ticket_type": "成人票", "customer_type": "团队", "price": 130, "quantity": 25, "subtotal": 3250},
    {"ticket_type": "儿童票", "customer_type": "散客", "price": 80, "quantity": 3, "subtotal": 240},
    ...
  ],
  "free_tickets": 1,
  "total_cost": 3650,
  "per_person": 121.67,
  "notes": "16免1已减免1张成人票"
}
```

---

## A5. 数据导入流程

与酒店完全相同的流程：

```
用户上传景点门票 Excel 或 文档
  → Agent 调用 Excel 工具读取
    → 逐行（每个景点）调用 LLM 解析门票价格 → 生成两个 Chunk
      → Chunk 0（景点信息摘要）→ embedding → 存入 chunks_vec
      → Chunk 1（门票价格明细表）→ 仅存入 chunks 表
```

### LLM 解析门票价格的 Prompt

```
你是一个景点门票数据整理助手。请将以下景点的非结构化门票信息整理为标准化的价格明细表。

景点名称：{attraction_name}
区域：{region}
类别：{category}

原始门票信息：
{raw_ticket_text}

请整理为以下格式，一行一个价格：
票种 | 客户类型 | 价格（元/人） | 是否含景区交通 | 适用日期：具体日期范围

要求：
1. 每种票种+客户类型组合单独一行
2. 适用日期必须是具体日期范围
3. 儿童票注明身高范围（如"1.2m-1.4m"）
4. 老人票注明年龄范围
5. 特殊政策附在末尾（免票规则、团体优惠等）
6. 如果门票含景区交通，注明
7. 如果有协议价/团队价，单独列出
```

---

## A6. 旧表删除

| 删除项 | 说明 |
|--------|------|
| `bs_travel_quote_attractions` 表 | DDL 从 init-postgres.sql 和 db_update.sql 移除 |
| `bs_travel_quote_tickets` 表 | DDL 从 init-postgres.sql 和 db_update.sql 移除 |
| `generate.py` 中的 attractions/tickets DDL | `TABLE_DEFINITIONS` 中相关条目删除 |
| `generate.py` 中的 `calculate_ticket_cost()` | 替换为新的知识库版本 |
| `travel_quote.py` 中 attractions/tickets CRUD 端点 | 删除 |
| `AttractionManager.vue` 中 tickets 子表代码 | 改造为知识库展示 |
| `travelQuote.ts` 中 tickets API | 删除 |

---

## A7. attraction.py 实现（已完成）

景点门票和游玩项目的费用计算封装在 `attraction.py` 中，统一入口 `calculate_attraction_cost()`。

### 核心设计

1. **按 doc_id 去重**：同一景点在不同天出现时合并，不重复计价
2. **门票和项目分别用独立 LLM prompt 提取**：门票走 `_llm_extract_tickets()`，项目走 `_llm_extract_projects()`
3. **价格取挂牌价**：价格表每行多个数字时取第一个（挂牌价），用于对外报价
4. **人数分配由代码决定**：LLM 只提取价格和票型（adult/student/child_half/elder），代码根据团队构成分配人数
5. **团队构成 fallback**：当 LLM 标记的票型（如 adult）对应人数为 0 时，自动 reassign 到团队主要客群
6. **同票型去重**：用 `seen_ticket_types` 集合确保每种票型只出一条，防止旺季/淡季重复

```python
def calculate_attraction_cost(items, tenant_id, attraction_matches,
                              adults, children_half, students, elders,
                              total_people, teacher_count=0,
                              attraction_ids=None):
    """统一入口：门票 + 游玩项目"""
    # 按 doc_id 去重
    unique_attractions = _merge_attractions(attraction_matches)

    for ua in unique_attractions:
        items = _process_single_attraction(items, ua, ...)

    return items


def _process_single_attraction(items, ua, ...):
    """处理单个景点"""
    # 获取 3 个 chunk
    attraction_info = retriever.get_attraction_info(ua.doc_id)
    ticket_table = retriever.get_ticket_table(ua.doc_id)
    project_table = retriever.get_project_table(ua.doc_id)

    # Step 1: 门票提取（LLM 独立 prompt）
    tickets = _llm_extract_tickets(ua.name, attraction_info, ticket_table, ...)
    items = _build_ticket_items(items, attraction_name, tickets, ..., teacher_count)

    # Step 2: 游玩项目匹配（仅当行程中有 activities 时）
    if ua.activities and project_table:
        projects = _llm_extract_projects(ua.name, attraction_info, project_table, ua.activities, ...)
        items = _build_project_items(items, attraction_name, projects, total_people, teacher_count)

    return items
```

---

## A8. 文件变更清单（景点门票部分）

### 新增/已实现文件

| 文件 | 说明 |
|------|------|
| `src/skills/quote-generate/scripts/attraction.py` | 门票 + 游玩项目费用计算（统一入口） |
| `src/skills/quote-generate/scripts/attraction_retriever.py` | 景点向量检索器（3 chunk 读取） |

### 已删除的旧代码

| 旧文件/代码 | 说明 |
|------------|------|
| `generate.py` 中的 attractions/tickets DDL | 不再需要本地建表 |
| `generate.py` 中的 `calculate_ticket_cost()` | 替换为 `attraction.py` 中的 `calculate_attraction_cost()` |
| `bs_travel_quote_attractions` + `bs_travel_quote_tickets` 表 | 迁移至向量知识库（3 chunk 结构） |

---

# 第三部分：车辆资源（关系型表 + 智能导入）

## B1. 方案概要

车辆数据**不适合向量知识库**，原因：

| 维度 | 酒店/景点 | 车辆 |
|------|-----------|------|
| 数据量 | 多（100+酒店，几十景点） | 少（5种车型，每区域5-10条） |
| 选择逻辑 | 语义匹配（名称/区域/星级） | **算法推荐**（按人数+座位算最优组合） |
| 价格复杂度 | 高（多房型/票种+日期段） | 中（日租金 + 按公里计费，按季节） |
| 名称匹配 | 需要（别名多） | 不需要（车型就5种） |

**方案**：保留 `bs_travel_quote_vehicles` 关系型表，改造 Excel 导入为智能解析。

---

## B2. 表结构（扩展）

保留现有表结构，新增 4 个字段支持按公里计费模式：

```sql
CREATE TABLE IF NOT EXISTS bs_travel_quote_vehicles (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    region_name TEXT,
    vehicle_type TEXT NOT NULL,          -- business/coaster/minibus/bus/large_bus
    vehicle_type_label TEXT,             -- "别克GL8商务车"
    seats_min INT NOT NULL,
    seats_max INT NOT NULL,
    daily_rate DECIMAL(10,2) NOT NULL,   -- 按天计费：日租金
    overtime_rate DECIMAL(10,2),
    overkm_rate DECIMAL(10,2),
    driver_meal_allowance DECIMAL(10,2),
    driver_accommodation DECIMAL(10,2),
    pricing_mode TEXT DEFAULT 'daily',   -- ★ 新增：计价方式 'daily' 或 'per_km'
    per_km_rate DECIMAL(10,2),           -- ★ 新增：每公里单价（per_km 模式）
    base_km DECIMAL(10,2),              -- ★ 新增：包含基础公里数（per_km 模式，超出部分按单价）
    base_fee DECIMAL(10,2),             -- ★ 新增：起步价（per_km 模式）
    season_type TEXT DEFAULT 'default',
    effective_from DATE,
    effective_to DATE,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INT DEFAULT 0,
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 新增字段说明

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `pricing_mode` | TEXT | 计价方式：`daily`（按天）或 `per_km`（按公里） | `'daily'` / `'per_km'` |
| `per_km_rate` | DECIMAL | 每公里单价，仅 `per_km` 模式使用 | `3.50`（元/公里） |
| `base_km` | DECIMAL | 包含基础公里数，超出部分按 per_km_rate 计算 | `200`（含200公里） |
| `base_fee` | DECIMAL | 起步价，仅 `per_km` 模式使用 | `500`（起步500元） |

### 按公里计费示例

| 场景 | base_fee | base_km | per_km_rate | 实际距离 | 计算公式 | 费用 |
|------|----------|---------|-------------|---------|---------|------|
| 贵阳→黄果树接送 | 500 | 200 | 3.50 | 128.5km | 128.5 ≤ 200，只收起步价 | 500元 |
| 贵阳→荔波包车 | 500 | 200 | 3.50 | 320km | 500 + (320-200) × 3.50 | 920元 |
| 机场接送 | 200 | 30 | 5.00 | 35km | 200 + (35-30) × 5.00 | 225元 |

**向后兼容**：`pricing_mode` 默认 `'daily'`，现有数据和逻辑完全不受影响。

---

## B3. 计价算法（扩展）

### B3.1 现有按天计费（保留不动）

`recommend_vehicle(people_count, vehicles)` 算法：

1. 人数能被一辆车装下 → 选最小能装下的（最省钱）
2. 装不下 → 遍历所有车型，计算最优组合（主车型+补充车型，成本最低）
3. 返回最优组合：`[{vehicle, count}, ...]`

费用计算：`daily_rate × trip_days × vehicle_count`

**按天计费逻辑完全保留，不改动。**

### B3.2 新增：按公里计费模式

当车辆 `pricing_mode = 'per_km'` 时，使用导航距离计费：

```
费用 = base_fee + max(0, distance_km - base_km) × per_km_rate

其中 distance_km 由 route-distance Skill 通过高德地图 API 计算的导航距离
```

**计费流程**：

```
用户: 25人从贵阳出发去黄果树瀑布玩3天
  │
  ├─ 1. 查询车辆数据，按 pricing_mode 分组
  │     daily_vehicles = [车型A, 车型B, ...]     ← pricing_mode='daily'
  │     per_km_vehicles = [车型C, 车型D, ...]    ← pricing_mode='per_km'
  │
  ├─ 2. 如果有 per_km 车辆:
  │     a. 调用 route-distance Skill 计算导航距离
  │        use_skill("route-distance", {"origin": "贵阳市", "destination": "黄果树瀑布"})
  │        → distance_km = 128.5
  │
  │     b. 推荐车型组合（按人数，复用 recommend_vehicle 算法）
  │
  │     c. 计算费用:
  │        车型C: base_fee=500, base_km=200, per_km_rate=3.50
  │        128.5 ≤ 200 → 费用 = 500 × 车辆数
  │
  ├─ 3. 如果导航距离计算失败:
  │     自动降级到 daily 模式计费（不阻塞报价）
  │
  └─ 4. 返回报价
```

### B3.3 generate.py 改造

```python
def calculate_vehicle_cost(items, tenant_id, region_names, total_people,
                           trip_days, season_type, vehicle_count,
                           route_distance_km=None):  # ★ 新增参数
    """计算交通费用（支持按天和按公里两种模式）"""
    vehicles = query_by_region("bs_travel_quote_vehicles", tenant_id, region_names, season_type)

    # 按计价模式分组
    daily_vehicles = [v for v in vehicles if v.get('pricing_mode', 'daily') == 'daily']
    per_km_vehicles = [v for v in vehicles if v.get('pricing_mode') == 'per_km']

    # 优先使用按公里计费（如果有距离数据）
    if per_km_vehicles and route_distance_km:
        return _calculate_per_km_cost(items, per_km_vehicles, total_people,
                                       route_distance_km, vehicle_count)

    # 默认按天计费（现有逻辑不变）
    if daily_vehicles:
        vehicles = daily_vehicles
    elif vehicles:
        # 如果只有 per_km 车辆但没有距离数据，仍用 daily_rate
        pass

    # ... 现有按天计费逻辑 ...
    combo = recommend_vehicle(total_people, vehicles)
    # ...


def _calculate_per_km_cost(items, vehicles, total_people, distance_km, vehicle_count):
    """按公里计费"""
    combo = recommend_vehicle(total_people, vehicles)
    total_cost = 0

    for c in combo:
        v = c["vehicle"]
        count = c["count"]
        base_km = float(v.get('base_km') or 0)
        base_fee = float(v.get('base_fee') or 0)
        per_km_rate = float(v['per_km_rate'])

        if distance_km <= base_km:
            # 未超出基础公里数
            cost = base_fee * count
        else:
            # 超出部分按公里计费
            extra_km = distance_km - base_km
            cost = (base_fee + extra_km * per_km_rate) * count

        total_cost += cost
        per_person = round(total_cost / total_people, 2)

        items.append({
            "category": "用车",
            "name": v['vehicle_type_label'],
            "specification": f"按公里计费 {distance_km}km",
            "unit_price": round(cost / count, 2),
            "unit": "趟",
            "frequency": 1,
            "quantity": count,
            "subtotal": cost,
            "per_person": per_person,
        })

    return items, total_cost
```

### B3.4 报价流程中调用 route-distance Skill

在报价生成主流程（`generate.py`）中，当检测到 `per_km` 车辆时获取导航距离：

```python
# 报价生成主流程中
vehicles = query_by_region("bs_travel_quote_vehicles", tenant_id, region_names, season_type)
has_per_km = any(v.get('pricing_mode') == 'per_km' for v in vehicles)

route_distance_km = None
if has_per_km and departure_city and destination:
    # 调用 route-distance Skill 获取导航距离
    route_result = use_skill("route-distance", {
        "origin": departure_city,
        "destination": destination,
    })
    if route_result and route_result.get("success"):
        route_distance_km = route_result["distance_km"]

vehicle_items, vehicle_cost = calculate_vehicle_cost(
    items, tenant_id, region_names, total_people,
    trip_days, season_type, vehicle_count,
    route_distance_km=route_distance_km,  # ★ 传入导航距离
)
```

---

## B4. 智能导入方案

### B4.1 当前问题

当前 Excel 导入使用固定模板（SHEET_TABLE_MAP），要求 Excel 的 sheet 名必须是"车辆"，列名必须精确匹配字段名。客户提供的 Excel 不一定符合这个格式。

### B4.2 智能导入流程

```
用户上传 Excel 文件（任意格式）
  │
  ▼
Agent 调用 Excel 工具读取文件
  │
  ▼
Agent 调用 LLM 分析 Excel 结构：
  - 输入：Excel 的所有 sheet 名 + 每个 sheet 的列名 + 前几行数据
  - 输出：每个 sheet 应该映射到哪个表 + 列名映射关系
  │
  ▼
LLM 返回映射关系（JSON）：
{
  "sheet_mapping": [
    {
      "sheet_name": "贵阳包车",
      "target_table": "bs_travel_quote_vehicles",
      "column_mapping": {
        "车型": "vehicle_type_label",
        "座位数": "seats_max",
        "日租金": "daily_rate",
        ...
      },
      "defaults": {
        "region_name": "贵阳",
        "vehicle_type": "bus",
        "seats_min": 37
      }
    }
  ]
}
  │
  ▼
Agent 按映射关系逐行提取数据 → 清洗转换 → 批量 INSERT
```

### B4.3 LLM 映射分析 Prompt

```
你是旅游报价数据整理助手。请分析以下 Excel 文件的结构，判断每个 sheet 应该映射到哪个数据表。

可用的数据表和字段：
- bs_travel_quote_vehicles（车辆价格）：region_name, vehicle_type(business/coaster/minibus/bus/large_bus), vehicle_type_label, seats_min, seats_max, daily_rate, overtime_rate, overkm_rate, driver_meal_allowance, driver_accommodation, pricing_mode(daily/per_km), per_km_rate, base_km, base_fee, season_type, remark

Excel 文件信息：
{excel_info}

请对每个 sheet 返回 JSON 格式的映射关系，包括：
1. target_table：映射到哪个表
2. column_mapping：Excel 列名 → 数据库字段名的映射
3. defaults：需要自动填充的默认值（如从 sheet 名推断 region_name）
4. value_transform：需要特殊转换的值（如"商务车"→ vehicle_type="business"）
```

### B4.4 值转换规则

LLM 映射时需要处理的常见转换：

| Excel 中的值 | 数据库字段 | 转换规则 |
|-------------|-----------|---------|
| "商务车"、"别克GL8" | vehicle_type | → `business` |
| "考斯特" | vehicle_type | → `coaster` |
| "中巴" | vehicle_type | → `minibus` |
| "大巴"、"37座" | vehicle_type | → `bus` |
| "大客车"、"55座" | vehicle_type | → `large_bus` |
| "5-7座" | seats_min/seats_max | → 拆分为两个字段 |
| "旺季"/"淡季" | season_type | → `peak`/`off` |
| "贵阳包车" | region_name | → `贵阳`（从 sheet 名推断） |
| "按天"/"按公里" | pricing_mode | → `daily`/`per_km` |
| "3.5元/公里" | per_km_rate | → `3.50` |
| "含200公里" | base_km | → `200` |
| "起步价500" | base_fee | → `500` |

这些转换规则由 LLM 动态判断，不需要硬编码。

---

## B5. 需要的改动

| 改动 | 说明 |
|------|------|
| 扩展表结构 | 新增 4 个字段（pricing_mode, per_km_rate, base_km, base_fee） |
| 改造计价逻辑 | `calculate_vehicle_cost()` 支持按公里计费分支 |
| 集成 route-distance Skill | 报价流程中调用 Skill 获取导航距离 |
| 删除固定 SHEET_TABLE_MAP | travel_quote.py 中的硬编码映射 |
| 新增智能导入端点 | `/api/v1/travel-quote/import/smart` |
| 导入流程：Agent 驱动 | Agent 读取 Excel → LLM 分析结构 → 按映射入库 |
| 前端导入按钮 | 不再要求固定模板，支持任意格式 Excel |
| 前端车辆管理 | 新增计价方式切换（按天/按公里），按公里时显示对应字段 |

---

## B6. 不需要改动的部分

- `recommend_vehicle()` 算法（按人数推荐车型组合）
- `VehicleManager.vue` 基本结构（只改导入功能 + 新增字段）
- 现有 `daily` 模式的计价逻辑

---

## 完整资源方案总结

| 资源 | 存储方案 | 选择方式 | 计价方式 | 导入方式 | 旧表处理 |
|------|---------|---------|---------|---------|---------|
| **酒店** | 向量知识库 | 向量搜索 + 名称匹配 | 按房型+日期 | Excel → LLM 解析 → 知识库 | 删除 hotels + rooms |
| **景点门票** | 向量知识库 | 向量搜索 + 别名匹配 | 按票种+人群 | Excel → LLM 解析 → 知识库 | 删除 attractions + tickets |
| **车辆** | 关系型表（扩展） | 算法推荐（按人数） | **按天 / 按公里**（★ 新增） | Excel → LLM 分析结构 → 智能映射入库 | 保留 vehicles |

需要删除的旧表：`bs_travel_quote_hotels`、`bs_travel_quote_rooms`、`bs_travel_quote_attractions`、`bs_travel_quote_tickets`

保留的关系型表：`bs_travel_quote_vehicles`、`bs_travel_quote_meals`、`bs_travel_quote_guides`、`bs_travel_quote_fees`、`bs_travel_quote_seasons`、`bs_travel_quote_regions`

新增的 Skill：`route-distance`（导航距离计算，详见 [Skill 设计文档](route_distance_skill_design.md)）

---

# 第四部分：quote-generate 技能重构 — 行程文本驱动报价（已完成）

## C1. 设计目标（已实现）

当前 `quote-generate` 技能要求调用者（LLM 子智能体）手动收集并传入 20+ 个结构化参数（包括 `attraction_ids`、`hotel_id` 等数据库 ID）。

问题：
1. **LLM 不知道数据库 ID**——景点和酒店在旧表用自增 ID，在知识库用 doc_id，LLM 无法直接获取
2. **参数收集过程冗长**——SUBAGENT.md 中参数清单占 20 行，调用示例复杂
3. **与向量知识库方案不匹配**——向量搜索天然支持自然语言查询，但当前设计要求传入精确 ID

## C2. 已实现：行程文本驱动

### 核心思路

**子智能体只需传入用户确认的行程方案全文，技能内部完成一切**：

```
用户确认的行程文本
  → Step A: 调用 LLM 解析行程文本 → 提取结构化数据（景点名称、人数、偏好等）
  → Step B: 用 Retriever 检索向量知识库 → 景点名称→doc_id，酒店偏好→doc_id
  → Step C: 用 route-distance Skill 计算导航距离
  → Step D: 调用现有的计价函数 → 计算各项费用
  → Step E: 汇总 + 导出 Excel
```

### 输入参数（新）

```json
{
  "tenant_id": "租户ID（必填）",
  "itinerary_text": "用户确认的行程方案全文（必填）",
  "start_date": "2026-07-01",
  "profit_rate": null,
  "course_name": "超级贵州研学",
  "company_name": "贵州天悦旅行社",
  "template_path": null
}
```

从 20+ 个参数简化到 **7 个**。`itinerary_text` 是子智能体与客户确认好的行程方案文本。

### 向后兼容

当不传 `itinerary_text` 而是传入旧的结构化参数（`attraction_doc_ids`、`hotel_doc_id` 等）时，走旧的计价逻辑，确保平滑过渡。

## C3. LLM 行程解析

### parse_itinerary() 函数（itinerary_parser.py）

输入行程文本，调用 LLM 提取结构化数据：

**返回字段**：

```json
{
    "region_name": "贵州",
    "total_people": 21,
    "adults": 0,
    "children_half": 0,
    "students": 20,
    "elders": 0,
    "couples": 0,
    "teacher_count": 1,
    "trip_days": 6,
    "departure_city": "贵阳",
    "destination": "平塘",
    "daily_attractions": [
        {"day": 1, "attractions": [{"name": "平塘天文小镇", "activities": ["开营仪式", "团队建设"]}]},
        {"day": 2, "attractions": [{"name": "中国天眼科普基地", "activities": ["专业讲解导览", "天文互动体验", "FAST观景台参观", "天文小课堂", "夜游望远镜观星"]}]}
    ],
    "hotel_preference": "4钻酒店",
    "hotel_stays": [
        {"city": "平塘", "area": "", "nights": 2},
        {"city": "荔波", "area": "", "nights": 1},
        {"city": "贵阳", "area": "", "nights": 2}
    ],
    "daily_routes": [
        {"day": 1, "legs": [{"from": "贵阳站", "to": "平塘天文小镇"}]},
        {"day": 3, "legs": [{"from": "平塘天文小镇", "to": "天空之桥研学基地"}, {"from": "天空之桥研学基地", "to": "荔波"}]}
    ],
    "meal_tier": "standard",
    "guide_type": "local"
}
```

### _call_llm() 实现

generate.py 运行在子进程中，直接调用 LLM SDK（与 embedding 相同的模式）：

- Qwen 提供商：`dashscope.Generation.call()`
- Zhipu 提供商：`zhipuai SDK`

## C4. 资源检索（resource_resolver.py，已实现）

将 LLM 解析出的名称/偏好转换为知识库 doc_id，并携带匹配的活动列表：

```python
def resolve_resources(parsed: dict, tenant_id: str) -> dict:
    """返回：
    {
        "attraction_matches": [
            {"name": "平塘天文小镇", "doc_id": 443, "activities": ["开营仪式", "团队建设"], "info": "..."},
            {"name": "中国天眼科普基地", "doc_id": 442, "activities": [...], "info": "..."},
            ...
        ],
        "hotel_doc_id": 538,
        "hotel_stays": [
            {"city": "平塘", "area": "", "nights": 2, "hotel_doc_id": 538},
            {"city": "荔波", "area": "", "nights": 1, "hotel_doc_id": 532},
            {"city": "贵阳", "area": "", "nights": 2, "hotel_doc_id": 489}
        ]
    }
    """
```

**景点匹配**：逐个 `daily_attractions` 条目在向量库中搜索（名称匹配优先 → 向量语义搜索），取 top-1，并聚合该景点在所有天的 activities。

**酒店匹配**：为每个 `hotel_stays` 条目按城市搜索酒店（`"{city} 酒店 {hotel_preference}"`），将 `hotel_doc_id` 回填到 hotel_stays 中。

## C5. generate_quote() 主流程（已实现）

```python
def generate_quote(params: dict) -> dict:
    init_tables()
    tenant_id = params.get('tenant_id', '')
    itinerary_text = params.get('itinerary_text', '')

    if itinerary_text:
        # 新模式：行程文本驱动
        parsed = parse_itinerary(itinerary_text)
        resources = resolve_resources(parsed, tenant_id)
        # 从 parsed + resources 提取所有参数
        region_name = parsed.get('region_name', '')
        total_people = parsed.get('total_people', 30)
        adults = parsed.get('adults', 0)
        students = parsed.get('students', 0)
        teacher_count = parsed.get('teacher_count', 0)
        attraction_matches = resources.get('attraction_matches', [])
        hotel_stays = resources.get('hotel_stays', [])
        # ... 其余参数 ...
    else:
        # 旧模式：向后兼容（直接从 params 读取结构化参数）
        pass

    # 人数校验
    adults, students = _validate_headcount(...)

    # 计价流程
    season_type, season_multiplier = determine_season(tenant_id, start_date)
    route_distance_km, leg_details = _calculate_route_distance(...)
    items, actual_vehicle_count = calculate_vehicle_cost(...)
    items = calculate_attraction_cost(items, tenant_id, attraction_matches, ...)
    if hotel_stays:
        items, single_supplement = calculate_hotel_stays(items, ...)
    else:
        items, single_supplement = calculate_hotel_cost(items, ...)
    items = calculate_meal_cost(...)
    items = calculate_guide_cost(...)
    items = calculate_other_fees(...)

    # 过滤 + 汇总
    items = [item for item in items if item.get('subtotal', 0) > 0 or item.get('unit_price', 0) > 0]
    cost_per_person = round(sum(item.get('subtotal', 0) for item in items), 2)
    quote_per_person = round(cost_per_person * (1 + profit_rate), 2)
    teacher_total = round(sum(item.get('teacher_subtotal', 0) for item in items), 2)

    # 导出 Excel
    file_path = export_with_template(internal_data, template_path)
    return { items, price_per_person, total_price, teacher_total, file_path, ... }
```

## C6. SUBAGENT.md 简化

报价阶段（阶段四）的参数清单简化为：

> **调用 quote-generate 技能时，只需传入**：
> - `tenant_id`：租户 ID（系统获取）
> - `itinerary_text`：客户确认的行程方案全文
> - `start_date`：出发日期
> - `company_name`：公司名称（从 extra.md 获取）
> - `course_name`：行程名称（可选）
>
> **技能内部自动完成**：解析行程 → 搜索景点酒店 → 计算距离 → 生成报价

## C7. 文件变更清单（已实现）

| 文件 | 改动 | 状态 |
|------|------|------|
| `src/skills/quote-generate/scripts/generate.py` | 主流程编排，新增 `_validate_headcount()`、`_calculate_route_distance()` | ✅ |
| `src/skills/quote-generate/scripts/itinerary_parser.py` | 新建：LLM 行程文本解析 | ✅ |
| `src/skills/quote-generate/scripts/resource_resolver.py` | 新建：景点/酒店向量检索 | ✅ |
| `src/skills/quote-generate/scripts/attraction.py` | 新建：门票 + 游玩项目费用计算 | ✅ |
| `src/skills/quote-generate/scripts/attraction_retriever.py` | 新建：景点向量检索器（3 chunk） | ✅ |
| `src/skills/quote-generate/scripts/hotel.py` | 新建：住宿费用计算（支持多城市 + teacher_subtotal） | ✅ |
| `src/skills/quote-generate/scripts/hotel_retriever.py` | 新建：酒店向量检索器（2 chunk） | ✅ |
| `src/skills/quote-generate/scripts/vehicle.py` | 新建：交通费用（按天/按公里 + 多段距离） | ✅ |
| `src/skills/quote-generate/scripts/meal.py` | 新建：餐饮费用 | ✅ |
| `src/skills/quote-generate/scripts/guide.py` | 新建：导游费用 | ✅ |
| `src/skills/quote-generate/scripts/other_fees.py` | 新建：其他费用 | ✅ |
| `src/skills/quote-generate/scripts/season.py` | 新建：淡旺季判定 | ✅ |
| `src/skills/quote-generate/scripts/db.py` | 新建：数据库表初始化 + 查询 | ✅ |
| `src/skills/quote-generate/scripts/excel_export.py` | 新建：Excel 模板导出 | ✅ |
| `src/skills/quote-generate/scripts/llm_client.py` | 新建：LLM 调用封装 | ✅ |
| `src/skills/quote-generate/SKILL.md` | 重写输入参数说明（7 个参数） | ✅ |
| `subagents/travel-consultant/SUBAGENT.md` | 简化阶段四的参数清单和调用示例 | ✅ |

---

# 第五部分：报价单模板差缺分析（已实现）

> 基于《超级贵州行程最终报价(30人).xls》实际模板，对比系统现有能力。
> 以下缺失项已在代码中全部实现。

## D1. 实际报价单结构

```
R0:  标题：贵州天悦旅行社有限公司研学报价表
R1:  信息行：研学 课程名称=超级贵州 | 日期=超级贵州 | 人数=30
R2:  表头：成本类别 | 项目 | 单价 | 数量 | 单位 | 次数 | 单位 | 费用小计 | 随队老师 | 备注
R3:  用车 | 旅游大巴 | 9800 | 1辆 | 1次 | 326.67 | 0 | 全程6天研学团队用车，贵阳起止
R4:  用餐 | 研学特色餐 | 40 | 1人 | 8餐 | 320 | 320 | 全程8个餐，不含第一天与最后一天晚餐
R5:  住宿 | 贵阳酒店【携程4钻】| 320 | 2人 | 2夜 | 320 | 320 | 贵阳两晚
R6:        | 安顺酒店【携程4钻】| 280 | 2人 | 1夜 | 140 | 140
R7:        | 罗甸酒店【携程4钻】| 180 | 2人 | 1夜 | 90  | 90  | 天眼主题日入住
R8:        | 西江酒店【携程4钻】| 428 | 2人 | 1夜 | 214 | 214
R9:  门票/活动 | 天眼景区 | 110 | 1人 | 1次 | 110 | 110 | 含观光车+天文体验馆+天象影院、证书、保险
R10:       | 天眼讲解费 | 400 | 1团 | 1次 | 13.33 | 0  | 南仁东纪念馆、天文科普馆
R11:       | 天眼耳机 | 10 | 1人 | 1次 | 10  | 10
R12:       | 天眼研学课程 | 30 | 1人 | 1次 | 30  | 0   | 发报机课程、观星、天眼模型任选一
R13:       | 关岭化石 | 30 | 1人 | 1次 | 30  | 30  | 化石挖掘
R14:       | 化石公园讲解费 | 200 | 1团 | 1次 | 6.67 | 0
R15:       | 夜游黄果树 | 120 | 1人 | 1次 | 120 | 120 | 旺季选择夜游黄果树
R16:       | 安顺屯堡/修房子 | 88 | 1人 | 1次 | 88  | 10  | 含门票，修缮房屋活动
R17:       | 西江门票 | 60 | 1人 | 1次 | 60  | 120 | 符合免票政策人群为30
R18:       | 西江研学课程 | 58 | 1人 | 1次 | 58  | 0   | 蜡染，苗歌，稻田捉鱼三选一
R19:       | 青岩古镇 | 0 | 1人 | 1次 | 0   | 10
R20:       | 坝陵河大桥 | 88 | 1人 | 1次 | 88  | 0   | 含上桥观光、桥梁博物馆、搭建桥梁
R21: 其他费用 | 每日用水/保险 | 20 | 1人 | 1次 | 20  | 20  | 研学手册、道具等
R22:       | 研学导师 | 400 | 2人 | 6天 | 160 | 0   | 研学老师2人1团
R23:       | 司陪房 | 200 | 2人 | 5夜 | 66.67 | 0  | 1名司机，2名导师
R24:       | 老师费用 | 1600 | 3人 | 1次 | 160 | -
R25:       | 操作费 | 20 | 1人 | 6天 | 120 | -
R26: 合计 | 人均成本 2551.33 | 随队老师 1514
R29: 审核 | 成本审核员： | 研学负责人： | 财务部审核：
R30:       | 日期： | 日期： | 日期：
```

## D2. 差缺要素分析

### 已具备的能力 ✅

| 报价单要素 | 系统现有能力 | 对应函数/模块 |
|-----------|-------------|-------------|
| 用车费用 | `calculate_vehicle_cost()` | 按天/按公里计费，推荐车型组合 |
| 用餐费用 | `calculate_meal_cost()` | 按餐标、区域、天数计算 |
| 景点门票 | `calculate_ticket_cost()` + `_calculate_ticket_cost_from_kb()` | 按票种、人群计价 |
| 酒店住宿 | `calculate_hotel_cost()` + `_calculate_hotel_cost_from_kb()` | 排房逻辑，单房差 |
| 其他费用（保险、操作费） | `calculate_other_fees()` | 多种计费方式 |
| 利润计算 | `generate_quote()` 中的汇总 | cost → quote |
| Excel 导出 | `export_with_template()` | 支持自定义模板 |

### 已实现的要素 ✅

> 以下要素在 v5.0 重构中已全部实现，原"缺失要素"状态更新如下：

| 要素 | 状态 | 实现方式 |
|------|------|----------|
| 随队老师独立计费列 | ✅ 已实现 | item 新增 `teacher_subtotal` 字段，各计价函数均支持 `teacher_count` 参数，Excel 输出两列 |
| 多城市不同酒店 | ✅ 已实现 | `hotel_stays` 列表 + `calculate_hotel_stays()` 函数，逐城市向量搜索酒店 |
| 门票/活动细项拆分 | ✅ 已实现 | 景点 chunk 2 存游玩项目价格表，`_llm_extract_projects()` 独立提取，`_build_project_items()` 按人/团计费 |
| 司陪房 | ⚠️ 部分实现 | 酒店排房已考虑 teacher_count，司陪房独立费用暂由 `calculate_other_fees()` 覆盖 |
| 报价表头信息 | ✅ 已实现 | `export_with_template()` 支持 company_name、course_name、total_people 等占位符 |
| 底部审核区域 | ✅ 已实现 | 通过自定义 Excel 模板预留，无需代码改动 |

## D3. 实施记录

> 以下所有项目已在 v5.0 重构中实现。

| 原优先级 | 要素 | 实现状态 | 实现模块 |
|----------|------|----------|----------|
| P0 | 多城市不同酒店 | ✅ 已实现 | `hotel.py: calculate_hotel_stays()` + `resource_resolver.py` 逐城市搜索 |
| P0 | 随队老师独立计费列 | ✅ 已实现 | 各计价函数新增 `teacher_count` 参数，item 含 `teacher_subtotal` 字段 |
| P1 | 门票/活动细项拆分 | ✅ 已实现 | `attraction.py: _llm_extract_projects()` + `_build_project_items()` 独立提取游玩项目 |
| P1 | 司陪房 | ⚠️ 部分实现 | 酒店排房考虑 teacher_count，独立司陪房费用由 `other_fees.py` 覆盖 |
| P2 | 自定义模板适配 | ✅ 已实现 | `excel_export.py: export_with_template()` 支持占位符 |
| P2 | 底部审核区域 | ✅ 已实现 | 通过自定义 Excel 模板预留 |

## D4. 当前 item 数据结构

> 已实现的目标结构（原"输出结构对比"已无对比意义，直接记录当前结构）。

```python
{
    "category": "门票",             # 成本类别：用车/门票/住宿/餐饮/导游/其他
    "name": "天眼景区联票",          # 项目名称
    "unit_price": 140.00,           # 单价
    "quantity": 21,                 # 数量
    "unit": "人",                   # 单位
    "subtotal": 98.00,              # 费用小计（学生人均）
    "teacher_subtotal": 140.00,     # 随队老师费用小计
    "remark": ""                    # 备注
}
```

### 汇总结构

```python
{
    "items": [...],
    "cost_per_person": 2551.33,       # 学生人均成本
    "teacher_total": 1514.00,         # 随队老师总费用
    "total_cost": cost_per_person * total_people,
    "single_supplement": 0,           # 单房差
    "profit_rate": 0.15,
    "quote_per_person": cost_per_person * (1 + profit_rate),
    "quote_total": quote_per_person * total_people,
}
```
