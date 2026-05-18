# 酒店 Excel 导入知识库方案

> 版本: v1.0 | 创建: 2026-05-13 | 状态: 待审核

## 现状分析

### 当前 Excel 导入流程

当前 HotelManager.vue 的 Excel 导入走的是**关系型数据库**路径：

```
用户上传 Excel → POST /api/v1/travel-quote/import/excel
  → openpyxl 读取 → 按 SHEET_TABLE_MAP 映射到关系表
    → "酒店" Sheet → bs_travel_quote_hotels
    → "房型" Sheet → bs_travel_quote_rooms
    → 其他 Sheet（车辆/景点/餐标/导游/费用/淡旺季）→ 各自业务表
```

**问题**：这个路径与向量知识库完全不互通。上传的酒店数据存到了 `bs_travel_quote_hotels` + `bs_travel_quote_rooms` 关系表中，而不是 `documents + chunks + chunks_vec` 知识库。

### 目标流程

用户上传酒店报价 Excel 后，应该：

1. **解析 Excel** → 按 `hotel_excel_analysis.json` 的格式输出结构化数据（按 Sheet 读取每个城市的酒店）
2. **每 10 条酒店数据调用一次 LLM** → 解析非结构化的价格文本，生成酒店信息摘要（Chunk 0）和价格明细表（Chunk 1）
3. **调用 `HotelRetriever.import_hotel()`** → 逐条写入知识库（documents + chunks + chunks_vec）

### Excel 数据特征

参考 `hotel_excel_analysis.json`，实际 Excel 的 Sheet 结构如下：

| Sheet 名 | 含义 | 每行 = 一家酒店 |
|-----------|------|----------------|
| 贵阳酒店 | 贵阳地区酒店 | 是 |
| 安顺酒店 | 安顺地区酒店 | 是 |
| 黔东南酒店 | 黔东南地区酒店 | 是 |
| ... | 其他城市 | 是 |

每个 Sheet 的列结构：

| 列名 | 说明 | 示例 |
|------|------|------|
| 市内区域/区域 | 子区域 | 云岩区、镇宁县 |
| 钻级 | 星级 | 4钻、5钻、3钻 |
| 酒店名称 | 酒店名 | 贵阳大十字广场亚朵酒店 |
| 房间数量 | 房型及数量 | 高级城景标：7、单间：35 |
| 地址 | 酒店地址 | 贵阳云岩区中华中路1号... |
| 五月~十二月 | **非结构化价格文本** | 团队团散同价\n高级城景房型：450 |

**核心难点**：月份列中的价格文本是**完全非结构化**的，包含：
- 团队价/团散价的混合描述
- 多种房型组合定价
- 日期段细分（7.1-7.9、7.10-8.25）
- 节假日特殊价格（五一、国庆、端午）
- 司陪房、免房政策
- "团散+20"、"门市价8.5折"等相对定价

这需要 LLM 来解析。

---

## 整体方案

### 架构图

```
用户上传 Excel
    │
    ▼
前端 HotelManager.vue
    │  POST /api/v1/travel-quote/import/hotel-excel-kb
    │  FormData: { file: Excel文件 }
    ▼
后端 import_hotel_excel_to_kb()
    │
    ├─ 1. 保存 Excel 到临时文件
    ├─ 2. 调用 Excel 工具读取（read_sheet）→ 获取结构化数据
    │      返回格式参照 hotel_excel_analysis.json:
    │      { sheet_names, 每个 sheet: { headers, rows, merged_cells } }
    │
    ├─ 3. 数据预处理：将 rows 从数组格式转为字典格式
    │      rows: [[val1, val2, ...], ...] → [{header1: val1, header2: val2, ...}, ...]
    │
    ├─ 4. 识别酒店列：找到"酒店名称"列，提取每行酒店
    │      过滤空行、无酒店名的行
    │      提取元信息：区域、钻级、地址、房间数量
    │      提取月份列：headers 中匹配月份关键词（五月~十二月）
    │
    ├─ 5. 分批 LLM 解析（每批 10 家酒店）
    │      每批发送：10 家酒店的原始数据
    │      Prompt 要求 LLM 为每家酒店输出：
    │        - 酒店信息摘要（Chunk 0 格式）
    │        - 价格明细表（Chunk 1 格式）
    │
    ├─ 6. 逐条写入知识库
    │      调用 HotelRetriever.import_hotel(tenant_id, hotel_name, region,
    │                                        info_text, price_table_text, metadata)
    │
    └─ 7. 返回导入结果
           { total, imported, skipped, errors }
```

### 分批 LLM 解析设计

**为什么每 10 条一批**：
- 酒店价格文本很长（每家可能有几百到上千字），一次传太多会超 token 限制
- 10 条约 3000-5000 字原始数据，LLM 输出约 2000-3000 字，总 token 在 8000 以内
- 太少则 API 调用次数多、速度慢；太多则 LLM 解析质量下降

**LLM Prompt（核心）**：

```
你是一个酒店价格数据解析助手。请将以下酒店原始数据解析为结构化格式。

## 输入数据

以下是从 Excel 中读取的 {count} 家酒店的原始数据。每家酒店包含基本信息和各月份的价格文本。

{hotel_data}

## 输出要求

请对每家酒店输出以下两个部分：

### 第1部分：酒店信息摘要

格式如下（一行一字段）：
酒店名称：xxx
所在区域：贵州省 xx市 xx区
星级等级：x钻
地址：xxx
主要房型：房型A、房型B（从价格文本和房间数量中提取）
房间总数：xx间（如无数据则不输出此行）
价格区间：xxx-xxx元/间（从所有月份中提取最低和最高价格）
是否含早：含双早/部分含早/不含早/未知
特殊说明：团队团散同价/执行16免1/司陪房xx元 等

### 第2部分：价格明细表

格式为表格，每行一个价格：
房型 | 客户类型 | 价格 | 含早 | 适用日期：具体日期范围

规则：
1. 所有模糊日期（如"五月"、"7-8月"）转换为具体日期范围（如"5月1日-5月31日"）
2. 节假日单独列出行（五一、国庆、端午等）
3. "团散+20"表示团散价 = 团队价 + 20
4. 司陪房、免房政策等特殊规则附在价格表末尾
5. 如果某月份列无数据，则跳过该月份
6. "门市价8.5折"转换为具体价格（如果无法计算则保留原文）

## 输出格式

请严格按以下 JSON 格式输出：

```json
[
  {
    "hotel_name": "酒店名称",
    "region": "区域",
    "info_text": "酒店信息摘要文本（多行，一行一字段）",
    "price_table_text": "价格明细表文本",
    "metadata": {
      "sub_region": "子区域",
      "diamond_level": "4钻",
      "address": "地址"
    }
  },
  ...
]
```

注意：只输出 JSON 数组，不要输出其他内容。如果某家酒店数据不足（如无酒店名称），则跳过不输出。
```

---

## 详细实现计划

### 任务 1: Excel 工具读取函数适配

**现状**：`src/tools/excel/excel_reader.py` 的 `read_sheet()` 一次只读一个 Sheet，返回格式为 `{ headers: [...], rows: [[val, ...], ...] }`。

**需要的格式**：参照 `hotel_excel_analysis.json`，需要一次读取所有 Sheet，rows 为字典列表格式。

**方案**：新增 `read_all_sheets()` 函数到 `excel_reader.py`。

```python
def read_all_sheets(file_path: str) -> Dict[str, Any]:
    """
    读取 Excel 文件的所有 Sheet，返回与 hotel_excel_analysis.json 一致的格式。

    Returns:
        {
            "sheet_names": ["贵阳酒店", "安顺酒店", ...],
            "贵阳酒店": {
                "max_row": 22,
                "max_col": 13,
                "merged_cells": [],
                "headers": ["市内区域", "钻级", ...],
                "rows": [{"市内区域": "云岩区", "钻级": "4钻", ...}, ...]
            },
            ...
        }
    """
```

**修改文件**: `src/tools/excel/excel_reader.py` — 新增 `read_all_sheets()` 函数

---

### 任务 2: 后端新增 Excel → 知识库导入 API

**新增接口**: `POST /api/v1/travel-quote/import/hotel-excel-kb`

**请求**: `FormData { file: Excel文件 }`

**响应**:
```json
{
  "success": true,
  "data": {
    "total_hotels": 100,
    "imported": 95,
    "skipped": 3,
    "errors": ["第X行: 原因"],
    "details": [
      { "sheet": "贵阳酒店", "total": 20, "imported": 19, "skipped": 1 }
    ]
  }
}
```

**实现步骤**:

1. 保存上传文件到临时路径
2. 调用 `read_all_sheets()` 读取全部 Sheet
3. 识别酒店 Sheet：检查 headers 中是否包含"酒店名称"列
4. 对每个酒店 Sheet，提取行数据，每行构造酒店原始信息
5. 按 10 条一批分组，调用 LLM 解析
6. 解析结果写入知识库（通过 `HotelRetriever.import_hotel()`）
7. 返回汇总结果

**修改文件**: `src/api/travel_quote.py` — 新增 `import_hotel_excel_to_kb()` 端点

---

### 任务 3: LLM 批量解析实现

**新增文件**: `src/skills/quote-generate/scripts/hotel_excel_parser.py`

```python
class HotelExcelParser:
    """酒店 Excel 非结构化价格文本解析器"""

    def __init__(self):
        self._gateway = None

    def _get_gateway(self):
        if self._gateway is None:
            from src.llm.gateway import LLMGateway
            self._gateway = LLMGateway()
        return self._gateway

    def parse_batch(self, hotels: List[Dict]) -> List[Dict]:
        """
        批量解析酒店数据。

        Args:
            hotels: 每个元素包含 hotel_name, region, raw_data 等

        Returns:
            解析后的列表，每个元素包含 hotel_name, region, info_text, price_table_text, metadata
        """
        # 构造 Prompt + 原始数据
        # 调用 LLM
        # 解析 JSON 输出
        # 返回结果

    def parse_excel_sheets(self, sheets_data: Dict) -> List[Dict]:
        """
        处理所有酒店 Sheet。

        1. 从 sheets_data 中识别酒店 Sheet（含"酒店名称"列）
        2. 提取每月列和基本信息列
        3. 分批（每 10 条）调用 parse_batch
        4. 汇总结果
        """
```

**关键设计决策**：

- 使用项目已有的 `LLMGateway` 调用 LLM，复用 KeyPool 和并发控制
- 解析结果为 JSON 格式，需做容错处理（LLM 输出可能包含 markdown 代码块包裹）
- 每批 10 条是建议值，可根据实际 token 消耗调整
- 解析失败的酒店单独记录错误，不影响其他酒店

---

### 任务 4: 前端适配

**现状**：`HotelManager.vue` 的 Excel 导入调用 `POST /api/v1/travel-quote/import/excel`（关系型导入），通过 `useImport` composable 处理。

**需要改为**：调用新的 `POST /api/v1/travel-quote/import/hotel-excel-kb`（知识库导入）。

**改动点**:

| 文件 | 改动 |
|------|------|
| `frontend/src/api/travelQuote.ts` | 新增 `importHotelExcelKB(file: File)` 函数 |
| `frontend/src/composables/useImport.ts` | 新增 `useHotelKBImport(onSuccess)` 或修改 `useImport` 支持不同 API |
| `frontend/src/components/travel/HotelManager.vue` | 导入按钮改调新 API；导入结果展示适配新格式（显示酒店数量而非行数） |

**导入结果展示调整**：

原 `ImportResult` 格式：
```json
{ "total_imported": 50, "total_skipped": 2, "results": [{sheet, imported, skipped}] }
```

新格式（酒店 KB 导入）：
```json
{
  "total_hotels": 100, "imported": 95, "skipped": 3,
  "details": [{ "sheet": "贵阳酒店", "total": 20, "imported": 19 }]
}
```

前端需适配新格式的结果展示。可以复用现有的 ImportResultModal 组件，只需调整数据映射。

---

### 任务 5: 月份列识别逻辑

Excel 中不同 Sheet 的月份列名称可能不同：

| Sheet | 月份列 |
|-------|--------|
| 贵阳酒店 | 五月、六月、七月、八月、九月、十月、十一月、十二月 |
| 安顺酒店 | 五月、六月、七月、八月 |
| 黔东南酒店 | 五月、六月、七月、八月、九月、十月 |

**识别规则**：
```python
MONTH_KEYWORDS = ["一月", "二月", "三月", "四月", "五月", "六月",
                  "七月", "八月", "九月", "十月", "十一月", "十二月",
                  "1月", "2月", "3月", "4月", "5月", "6月",
                  "7月", "8月", "9月", "10月", "11月", "12月"]

def is_month_column(header: str) -> bool:
    return any(kw in header for kw in MONTH_KEYWORDS)

def is_info_column(header: str) -> bool:
    info_keywords = ["区域", "市内区域", "钻级", "酒店名称", "房间数量", "地址"]
    return any(kw in header for kw in info_keywords)
```

非月份、非信息列忽略（如某些 Sheet 可能有备注列）。

---

## 涉及文件汇总

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/skills/quote-generate/scripts/hotel_excel_parser.py` | 酒店 Excel 非结构化价格文本 LLM 解析器 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/tools/excel/excel_reader.py` | 新增 `read_all_sheets()` 函数 |
| `src/api/travel_quote.py` | 新增 `POST /import/hotel-excel-kb` 端点 |
| `frontend/src/api/travelQuote.ts` | 新增 `importHotelExcelKB()` API 函数 |
| `frontend/src/composables/useImport.ts` | 适配新的导入 API |
| `frontend/src/components/travel/HotelManager.vue` | 导入按钮改调知识库导入 |

---

## 数据流详图

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端 HotelManager.vue                                           │
│                                                                  │
│  [上传 Excel] → importHotelExcelKB(file)                        │
│       │                                                          │
│       ▼                                                          │
│  POST /api/v1/travel-quote/import/hotel-excel-kb                │
│  Content-Type: multipart/form-data                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│ 后端 import_hotel_excel_to_kb()                                  │
│                                                                  │
│  1. 保存文件到 temp                                              │
│  2. read_all_sheets(file_path)                                   │
│     → { sheet_names, 贵阳酒店: {headers, rows}, 安顺酒店: ... } │
│                                                                  │
│  3. 识别酒店 Sheet（headers 含 "酒店名称"）                      │
│                                                                  │
│  4. 逐 Sheet 处理：                                              │
│     for each sheet in hotel_sheets:                              │
│       hotels = extract_hotels(sheet)  # 提取行，构造原始数据     │
│       batches = chunk(hotels, 10)                                │
│                                                                  │
│       for batch in batches:                                      │
│         ┌──────────────────────────────────────┐                 │
│         │ HotelExcelParser.parse_batch(batch)  │                 │
│         │                                      │                 │
│         │  构造 Prompt + 原始数据               │                 │
│         │       │                              │                 │
│         │       ▼                              │                 │
│         │  LLMGateway.chat(prompt)             │                 │
│         │       │                              │                 │
│         │       ▼                              │                 │
│         │  解析 JSON 输出                       │                 │
│         │  [                                    │                 │
│         │    {                                  │                 │
│         │      hotel_name,                      │                 │
│         │      region,                          │                 │
│         │      info_text,     ← Chunk 0        │                 │
│         │      price_table_text, ← Chunk 1     │                 │
│         │      metadata                         │                 │
│         │    },                                 │                 │
│         │    ...                                │                 │
│         │  ]                                    │                 │
│         └──────────────────────────────────────┘                 │
│              │                                                   │
│              ▼                                                   │
│         for hotel in parsed:                                     │
│           HotelRetriever.import_hotel(                           │
│             tenant_id, hotel_name, region,                       │
│             info_text, price_table_text, metadata                │
│           )                                                      │
│           → documents + chunks + chunks_vec                      │
│                                                                  │
│  5. 返回 { total, imported, skipped, errors, details }           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 风险与注意事项

### 1. LLM 解析质量

酒店价格文本格式极度不统一（有些只有"300"，有些有复杂的多房型多日期段描述）。LLM 可能：
- 漏解析某些隐性信息（如"团散+20"需要从上文推断团队价）
- 日期范围推断不准确（如"五月"是否包含五一假期）
- 价格数字提取错误

**缓解措施**：
- Prompt 中给出充分的示例（参照 `travel_pricing_resource_design.md` 中的示例格式）
- 解析后可增加后处理验证（价格是否为数字、日期范围是否合理）
- 对于 LLM 无法解析的酒店，记录原始文本到 error 中，允许后续手动补充

### 2. 导入耗时

假设 100 家酒店，每批 10 条，共 10 次 LLM 调用。每次 LLM 调用约 5-10 秒，总耗时约 1-2 分钟。

**缓解措施**：
- 前端显示进度（当前处理第 X/100 家）
- 使用 SSE 流式返回进度（可选，第一版可先做同步等待）
- 或者改用后台任务 + 轮询（如果用户反馈等待时间过长）

### 3. Excel 格式多样性

实际 Excel 可能：
- 列名不完全匹配（如"区域"vs"市内区域"）
- 存在合并单元格（如同一区域的多行酒店）
- 钻级字段混入其他信息（如"4钻\n开业：2016"）

**缓解措施**：
- 月份列识别用关键词模糊匹配
- 基本信息（区域、钻级）从行内读取，缺失则尝试从上一行继承（合并单元格场景）
- 非结构化信息原样传给 LLM 处理

### 4. 重复导入

用户多次上传同一个 Excel 会导致知识库中重复数据。

**缓解措施**：
- 导入前按 `hotel_name + region + tenant_id` 查重
- 如果已存在同名酒店，跳过或更新（第一版先跳过，后续支持更新）
- 返回结果中区分 `imported`（新增）和 `skipped`（已存在跳过）

---

## 验收标准

- [ ] 用户在 HotelManager 页面上传酒店 Excel
- [ ] Excel 被正确解析（参照 hotel_excel_analysis.json 格式）
- [ ] 每家酒店的非结构化价格文本被 LLM 解析为酒店信息摘要 + 价格明细表
- [ ] 解析结果通过 HotelRetriever.import_hotel() 写入向量知识库
- [ ] 导入后通过搜索 API 能搜到导入的酒店
- [ ] 前端展示导入结果（成功/跳过/错误数量）
- [ ] 重复导入同一家酒店时跳过（不产生重复数据）
