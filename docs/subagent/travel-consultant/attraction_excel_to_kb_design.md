# 景点门票 Excel 导入知识库方案

> 版本: v1.0 | 创建: 2026-05-13 | 状态: 待审核

## 现状分析

### 当前 Excel 导入流程

当前 AttractionManager.vue 有两套独立路径：

1. **数据库管理 Tab**：上传 Excel 走 `POST /api/v1/travel-quote/import/excel`（关系型数据库），存入 `bs_travel_quote_attractions` + `bs_travel_quote_tickets` 表
2. **知识库搜索 Tab**：只能搜索 `documents + chunks + chunks_vec` 中 `source_type='attraction_resource'` 的数据

两套路径完全不互通。实际业务中，景点门票数据来源是研学项目报价总表 Excel，结构极其非标准化，无法直接映射到关系表，必须走 LLM 解析 + 向量知识库路径。

### 目标

1. 移除"数据库管理"Tab，只保留知识库视图
2. 默认显示所有景点知识库数据（无搜索时自动 list_all）
3. 每个景点卡片显示来源文件信息
4. 新增景点 Excel 导入知识库 API（`POST /import/attraction-excel-kb`）
5. AttractionRetriever 补齐缺失的 `list_all()`、`source_file`、详情返回字段

---

## Excel 数据特征

### 数据源

`研学项目报价总表.xlsx`，约 50 个 Sheet。

### Sheet 分类

经过实际分析，该 Excel 的 Sheet 可分为三类：

| 类型 | 示例 Sheet 名 | 结构特征 |
|------|--------------|----------|
| **汇总 Sheet** | 贵州景点或地区资源汇总、贵阳景点汇总 | 每行一个景点，列含：名称、挂牌价、团队结算价、建议报价、备注 |
| **单体景点 Sheet** | 荔波小七孔、西江千户苗寨、坝陵河大桥、十二背后、黄果树 | 一个景点占整个 Sheet，内含多个子供应商/子项目的价格表 |
| **方向 Sheet** | 遵义方向、安顺方向、黔南方向 | 按出行方向分组，每行一个景点，列结构类似于汇总 Sheet |

### 列结构特征

#### 汇总/方向类 Sheet 典型列

| 列名 | 说明 | 示例 |
|------|------|------|
| 名称 / 景点名称 | 景点名 | 黄果树瀑布 |
| 挂牌价（元） | 官方价格 | 160 |
| 团队结算价（元） | 旅行社结算价 | 80 |
| 建议报价 | 对外报价 | 150 |
| 备注 | 补充说明 | 含环保车50元 |

**注意**：不同 Sheet 的列名不完全一致，需要模糊匹配。

#### 单体景点 Sheet 特征

- 数据量更大，一个景点可能有多个子项目
- 价格按季节/票种分层（成人票、学生票、老人票、团队票等）
- 部分有嵌套结构（如荔波小七孔下有多个服务提供商）
- 列结构高度不统一，无法硬编码解析

### 核心难点（比酒店 Excel 更复杂）

1. **Sheet 类型多样**：酒店 Excel 每个 Sheet 结构一致（都有"酒店名称"列），景点 Excel 有三类完全不同的 Sheet 结构
2. **列名不统一**：同一个概念在不同 Sheet 中列名不同
3. **嵌套结构**：单体景点 Sheet 中有子供应商层级
4. **合并单元格**：方向 Sheet 中同一方向可能合并区域列
5. **数据稀疏**：大量单元格为空或只有备注文字

---

## 整体方案

### 架构图

```
用户上传 Excel（研学项目报价总表.xlsx）
    │
    ▼
前端 AttractionManager.vue
    │  POST /api/v1/travel-quote/import/attraction-excel-kb
    │  FormData: { file: Excel文件 }
    ▼
后端 import_attraction_excel_to_kb()
    │
    ├─ 1. 保存 Excel 到临时文件
    │
    ├─ 2. read_all_sheets(file_path) → 获取所有 Sheet 的结构化数据
    │
    ├─ 3. Sheet 分类
    │      ├─ 汇总类 Sheet（含"名称"或"景点名称"列 + "挂牌价"列）
    │      ├─ 单体景点 Sheet（只有价格数据，无标准列名）
    │      └─ 跳过无关 Sheet（无景点数据）
    │
    ├─ 4. 按分类提取景点数据
    │      ├─ 汇总/方向类：逐行提取，每行 = 1个景点
    │      └─ 单体类：将整个 Sheet 内容作为一个景点
    │
    ├─ 5. 分批 LLM 解析（每批 5 个景点，比酒店批次更小）
    │      Prompt 要求 LLM 为每个景点输出：
    │        - 景点信息摘要（Chunk 0）
    │        - 门票价格表（Chunk 1）
    │
    ├─ 6. 逐条写入知识库
    │      AttractionRetriever.import_attraction()
    │
    └─ 7. 返回导入结果
           { total_attractions, imported, skipped, errors, details }
```

### 分批 LLM 解析设计

**为什么每批 5 条而非酒店的 10 条**：
- 景点数据更复杂，单体景点 Sheet 原始数据量大（可能上千字）
- 需要提取更多结构化信息（票种、季节、适用条件）
- 减小批次可提高 LLM 解析质量

**Sheet 分类策略**：

```python
def classify_sheet(sheet_name: str, headers: List[str]) -> str:
    """
    分类 Sheet 类型。

    汇总类：headers 同时含 名称/景点名称 + 挂牌价/团队结算价
    单体类：sheet_name 是已知景点名，或只有一个景点的价格表
    跳过：无有效数据
    """
    name_cols = [h for h in headers if any(kw in h for kw in ["名称", "景点"])]
    price_cols = [h for h in headers if any(kw in h for kw in ["挂牌价", "结算价", "报价", "门市价"])]

    if name_cols and price_cols:
        return "summary"
    elif price_cols:
        return "single"
    return "skip"
```

**LLM Prompt（景点专用）**：

```
你是一个景点门票价格数据解析助手。请将以下景点原始数据解析为结构化格式。

## 输入数据

以下是从 Excel 中读取的 {count} 个景点的原始数据。

{attraction_data}

## 输出要求

请对每个景点输出以下两个部分：

### 第1部分：景点信息摘要

格式如下（一行一字段）：
景点名称：xxx
所在区域：贵州省 xx市/xx方向
景区等级：xA级/省级/其他
景点类型：自然风光/人文历史/主题乐园/研学基地/其他
主要票种：成人票、学生票、老人票等（从价格数据中提取）
价格区间：xxx-xxx元/人（从所有票种中提取最低和最高价格）
景区交通：环保车xx元/人、索道xx元/人（如有）
特殊说明：免票政策、优惠政策等

### 第2部分：门票价格明细表

格式为表格，每行一个价格：
票种 | 客户类型 | 挂牌价 | 结算价 | 建议报价 | 适用条件 | 备注

规则：
1. 所有模糊价格信息转换为具体数字
2. 如果有季节差价，分多行列出
3. "含环保车"等附加费用单独标注
4. 团体优惠条件（如"16免1"）附在价格表末尾
5. 如果原始数据不完整，尽量推断，无法推断的标注"未知"

## 输出格式

请严格按以下 JSON 格式输出：

```json
[
  {
    "attraction_name": "景点名称",
    "region": "区域/方向",
    "category": "景点类型英文编码（natural/cultural/theme_park/museum/research/other）",
    "info_text": "景点信息摘要文本（多行，一行一字段）",
    "ticket_table_text": "门票价格明细表文本",
    "metadata": {
      "region": "区域",
      "category_cn": "中文景点类型",
      "source_sheet": "来源 Sheet 名"
    }
  },
  ...
]
```

注意：只输出 JSON 数组，不要输出其他内容。如果某个景点数据不足（如无景点名称），则跳过不输出。
```

---

## 详细实施计划

### 任务 1: AttractionRetriever 增强

对标 HotelRetriever 已有的能力，补齐 AttractionRetriever 缺失的功能。

**修改文件**: `src/skills/travel-quote/scripts/attraction_retriever.py`

#### 1.1 新增 `list_all()` 方法

```python
def list_all(self, tenant_id: str, limit: int = 200, offset: int = 0) -> Dict:
    """列出所有景点知识库文档（分页）"""
    with self._get_conn() as conn:
        conn.execute(
            "SELECT COUNT(*) AS cnt FROM documents WHERE source_type = %s AND tenant_id = %s",
            (self.SOURCE_TYPE, tenant_id),
        )
        total = conn.fetchone()["cnt"]

        conn.execute("""
            SELECT d.id AS doc_id, d.title, d.file_path, d.metadata, d.created_at,
                   c.text AS info
            FROM documents d
            LEFT JOIN chunks c ON c.doc_id = d.id AND c.chunk_index = 0
            WHERE d.source_type = %s AND d.tenant_id = %s
            ORDER BY d.id
            LIMIT %s OFFSET %s
        """, (self.SOURCE_TYPE, tenant_id, limit, offset))
        rows = conn.fetchall()

    items = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        items.append({
            "doc_id": row["doc_id"],
            "title": row["title"],
            "info": row["info"] or "",
            "metadata": meta or {},
            "source_file": row["file_path"] or "",
            "created_at": str(row["created_at"]) if row["created_at"] else "",
        })
    return {"total": total, "items": items}
```

#### 1.2 `search_by_name` 和 `search_by_vector` 返回 `source_file` 和 `created_at`

与 HotelRetriever 保持一致，在 SQL JOIN 中加入 `d.file_path, d.created_at`，结果中加入这两个字段。

#### 1.3 `import_attraction()` 增加 `source_file` 参数

```python
def import_attraction(self, tenant_id: str, attraction_name: str, region: str,
                      info_text: str, ticket_table_text: str,
                      metadata: Optional[Dict] = None,
                      source_file: str = "") -> int:
    # ...
    # file_path 传入 source_file 而非空字符串
```

---

### 任务 2: 后端新增 API

#### 2.1 新增导入端点

**新增接口**: `POST /api/v1/travel-quote/import/attraction-excel-kb`

**请求**: `FormData { file: Excel文件 }`

**响应**:
```json
{
  "success": true,
  "data": {
    "total_attractions": 50,
    "imported": 45,
    "skipped": 3,
    "errors": ["Sheet 'xxx': 原因"],
    "details": [
      { "sheet": "贵州景点或地区资源汇总", "total": 10, "imported": 10, "skipped": 0 }
    ]
  }
}
```

**修改文件**: `src/api/travel_quote.py` — 新增 `import_attraction_excel_to_kb()` 端点

#### 2.2 新增列表端点

**新增接口**: `GET /api/v1/travel-quote/kb/attractions`

**参数**: `limit` (默认 200), `offset` (默认 0)

**响应**:
```json
{
  "success": true,
  "data": {
    "total": 45,
    "items": [
      { "doc_id": 1, "title": "景点：黄果树瀑布", "info": "...", "metadata": {}, "source_file": "研学项目报价总表.xlsx", "created_at": "..." }
    ]
  }
}
```

#### 2.3 更新详情端点

现有 `GET /kb/attractions/{doc_id}` 需要返回 `title`、`source_file`、`metadata`（与酒店详情端点保持一致）。

**修改文件**: `src/api/travel_quote.py` — 更新 `get_attraction_kb()` 返回字段

---

### 任务 3: 景点 Excel LLM 解析器

**新增文件**: `src/skills/travel-quote/scripts/attraction_excel_parser.py`

```python
class AttractionExcelParser:
    """景点 Excel 非结构化价格文本解析器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    def classify_sheet(self, sheet_name: str, headers: List[str]) -> str:
        """分类 Sheet 类型"""

    def extract_from_summary_sheet(self, sheet_data: Dict) -> List[Dict]:
        """从汇总/方向 Sheet 提取景点数据"""

    def extract_from_single_sheet(self, sheet_name: str, sheet_data: Dict) -> List[Dict]:
        """从单体景点 Sheet 提取数据"""

    def parse_batch(self, attractions: List[Dict]) -> List[Dict]:
        """批量解析景点数据（每批 5 个）"""

    def parse_excel_sheets(self, sheets_data: Dict) -> List[Dict]:
        """处理所有 Sheet，分类提取 + 分批解析"""
```

---

### 任务 4: 前端 API 适配

**修改文件**: `frontend/src/api/travelQuote.ts`

#### 4.1 新增景点 KB 导入 API

```typescript
export interface AttractionKBImportResult {
  total_attractions: number
  imported: number
  skipped: number
  errors: string[]
  details: Array<{
    sheet: string
    total: number
    imported: number
    skipped: number
  }>
}

export async function importAttractionExcelKB(file: File): Promise<{ success: boolean; data: AttractionKBImportResult }> {
  // 同 importHotelExcelKB 模式
}
```

#### 4.2 新增景点列表 API

```typescript
export async function listAttractionsKB(params?: { limit?: number; offset?: number }): Promise<{ total: number; items: any[] }> {
  // 同 listHotelsKB 模式
}
```

---

### 任务 5: 前端 AttractionManager.vue 重写

**修改文件**: `frontend/src/components/travel/AttractionManager.vue`

**改动内容**（与 HotelManager.vue 重写模式一致）：

1. **删除**：整个"数据库管理"Tab、景点 CRUD 弹窗、门票 CRUD 弹窗、Tab 切换栏
2. **保留**：知识库搜索功能、景点详情弹窗、导入结果弹窗
3. **新增**：
   - 默认显示所有景点（调用 `listAttractionsKB()`）
   - "显示全部"按钮（搜索后可切回全部列表）
   - 每个景点卡片显示：title、region tag、category tag、来源文件、info 摘要
   - 导入按钮改调 `importAttractionExcelKB`

**参考 HotelManager.vue 的最终结构**，适配景点字段名即可。

---

### 任务 6: 前端 composable 适配

**修改文件**: `frontend/src/composables/useImport.ts`

新增 `useAttractionKBImport(onSuccess)` composable（或修改现有 `useImport` 支持景点 KB 导入），模式与 `useHotelKBImport` 一致。

---

## 涉及文件汇总

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/skills/travel-quote/scripts/attraction_excel_parser.py` | 景点 Excel LLM 解析器 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/skills/travel-quote/scripts/attraction_retriever.py` | 新增 `list_all()`、搜索返回 `source_file/created_at`、`import_attraction()` 增加 `source_file` |
| `src/api/travel_quote.py` | 新增导入/列表端点、更新详情端点返回字段 |
| `frontend/src/api/travelQuote.ts` | 新增 `importAttractionExcelKB()`、`listAttractionsKB()` |
| `frontend/src/composables/useImport.ts` | 新增 `useAttractionKBImport` |
| `frontend/src/components/travel/AttractionManager.vue` | 重写为知识库模式（参考 HotelManager.vue） |

---

## 酒店开发经验与注意事项

以下是酒店 Excel → KB 开发过程中遇到的实际问题，景点开发需引以为鉴：

### 1. dotenv 必须在导入 src 模块之前加载

**问题**：`src/db/database.py` 在模块导入时读取 `DATABASE_URL` 环境变量。如果 `.env` 尚未加载，会使用默认的 `localhost` URL，导致连接到错误的数据库。

**解决**：独立脚本（测试脚本、迁移脚本）中，必须在 `import src.*` 之前调用：
```python
from dotenv import load_dotenv
load_dotenv()
# 然后才能 import src.xxx
```

### 2. psycopg2 context manager 用法

**问题**：`get_db_connection()` 返回的是 context manager，不是 raw connection。直接调用 `conn.cursor()` 会报 TypeError。

**解决**：使用 `with get_db_connection() as conn: conn.execute(...)` 模式。

### 3. LLM JSON 输出的容错解析

**问题**：LLM 输出的 JSON 可能被 markdown 代码块包裹（如 ` ```json ... ``` `），直接 `json.loads()` 会失败。

**解决**：解析前先 strip markdown 代码块标记：
```python
def parse_llm_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]  # 去掉第一行 ```json
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())
```

### 4. 方法调用时注意参数顺序

**问题**：调用 `search_by_name(name, tenant_id=TENANT_ID)` 传了错误的 keyword 参数，方法签名实际是 `search_by_name(tenant_id, name_query)`，导致所有搜索静默返回空结果（88条全部跳过）。

**解决**：调用 Retriever 方法时，严格使用 keyword args 并核对方法签名。

### 5. source_file 追踪

**问题**：初始版本的 `import_hotel()` 没有记录来源文件名，导致导入后无法知道数据来自哪个 Excel。

**解决**：在 `import_hotel/import_attraction` 中增加 `source_file` 参数，存入 `documents.file_path` 字段。API 层从 `file.filename` 获取。

### 6. tenant_id 一致性

**问题**：初始导入使用了 `default` 作为 tenant_id，而实际租户 ID 是 `tenant_9eb3e45cab83`，导致前端按租户过滤时看不到数据。

**解决**：导入 API 中统一从 `request.state.tenant_id` 获取，不再硬编码。如果历史数据需要修复，用 SQL UPDATE 批量修正。

### 7. Windows 控制台中文编码

**问题**：Windows 控制台默认编码不支持 UTF-8，中文输出乱码。

**解决**：测试脚本开头加 `sys.stdout.reconfigure(encoding='utf-8')`，或重定向 stderr（`2>nul`）。

---

## 风险与注意事项

### 1. 景点数据比酒店更非结构化

酒店的月份列价格文本虽然复杂，但每行 = 一家酒店的模式是统一的。景点 Excel 则：
- 汇总 Sheet 每行 = 一个景点，但列名不统一
- 单体景点 Sheet 整个 Sheet = 一个景点，内部结构千变万化
- 方向 Sheet 每行 = 一个景点，但可能有合并单元格

**缓解措施**：
- Sheet 分类器做模糊匹配而非精确匹配
- 单体景点 Sheet 将整个内容传给 LLM，不做预处理
- 对于无法分类的 Sheet，尝试作为单体景点处理

### 2. LLM 解析质量

景点门票数据中"团队结算价"和"建议报价"等字段可能有空值或非数字内容（如"电询"、"面议"）。

**缓解措施**：
- Prompt 中明确说明如何处理缺失值（标注"未知"）
- 解析后对 JSON 做后处理验证
- 失败的景点记录原始数据到 errors 中

### 3. 景点名称去重

同一个景点可能出现在多个 Sheet 中（如"黄果树瀑布"同时出现在汇总 Sheet 和安顺方向 Sheet）。

**缓解措施**：
- 导入前按 `attraction_name + tenant_id` 查重
- 已存在则跳过（第一版），后续支持更新
- 返回结果中区分 imported 和 skipped

### 4. 导入耗时

假设 50 个景点，每批 5 个，共 10 次 LLM 调用，加上 embedding 调用，总耗时约 2-3 分钟。

**缓解措施**：
- 前端显示"导入中..."状态
- 第一版用同步等待，如用户反馈过长再改为 SSE 或后台任务

### 5. Excel 格式变更

实际 Excel 的 Sheet 列名可能随版本变化。

**缓解措施**：
- 列名识别用关键词模糊匹配，不硬编码
- 解析器输出包含 `source_sheet` 便于追溯
- LLM Prompt 足够通用，不依赖特定列名

---

## 验收标准

- [ ] 用户在 AttractionManager 页面上传景点 Excel（研学项目报价总表.xlsx）
- [ ] Excel 被正确分类（汇总 Sheet、单体景点 Sheet、方向 Sheet）
- [ ] 每个景点的非结构化价格数据被 LLM 解析为景点信息摘要 + 门票价格表
- [ ] 解析结果通过 AttractionRetriever.import_attraction() 写入向量知识库
- [ ] 导入后通过搜索 API 能搜到导入的景点
- [ ] 默认视图显示所有景点知识库数据（含来源文件信息）
- [ ] 前端展示导入结果（成功/跳过/错误数量，按 Sheet 分组）
- [ ] 重复导入同一景点时跳过（不产生重复数据）
- [ ] "数据库管理"Tab 已移除，页面只显示知识库视图
