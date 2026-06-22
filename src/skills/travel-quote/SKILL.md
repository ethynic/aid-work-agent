---
name: travel-quote
description: 旅游行程报价技能，专门用于研学/旅游场景，传入用户确认的行程方案文本，自动解析行程、检索资源、计算费用、导出 Excel 报价单。
metadata:
  version: "2.0.0"
  author: aid-work-agent
dependencies:
  - openpyxl>=3.1.0
---

# 研学旅游报价生成技能

## 适用场景

当用户确认了行程方案，需要生成详细报价时使用此技能。一次调用完成：解析行程 → 检索资源 → 计算费用 → 导出 Excel。

**触发词**："报价"、"费用多少"、"算一下价格"、"报价单"、"生成报价"、"导出报价单"

## 调用方式

```python
skill_execute(
  skill="travel-quote",
  command="python scripts/generate.py",
  content='<JSON 参数>'
)
```

**必须通过 `content` 参数传递 JSON**，不要用命令行参数。

## 输入参数（JSON）

```json
{
  "tenant_id": "租户ID",
  "itinerary_text": "## 贵州天眼+荔波5天4晚研学行程\n\n**出发日期**：2026-07-01\n**团队构成**：30名初三学生\n\n| 天数 | 时段 | 行程安排 | 游玩项目 | 备注 |\n|------|------|----------|----------|------|\n| D1 | 下午 | 贵阳接站，乘车前往平塘天文小镇 | — | 约2.5小时车程 |\n| D2 | 上午 | 天文体验馆、南仁东事迹馆 | 精品讲解导览、天象影院 | 21-30人团讲解 |\n...（完整原文，包括标题、人数、5列表格）",
  "start_date": "2026-07-01",
  "course_name": "超级贵州研学",
  "company_name": "贵州天悦旅行社有限公司",
  "template_path": null
}
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tenant_id` | string | 是 | 租户ID |
| `itinerary_text` | string | 是 | 用户确认的完整行程方案原文，**必须包含标题、出发日期、团队构成（人数等）和5列Markdown表格详细行程**（天数、时段、行程安排、游玩项目、备注），不能压缩成自然语言段落 |
| `start_date` | string | 否 | 出发日期 YYYY-MM-DD，默认今天 |
| `course_name` | string | 否 | 行程/课程名称 |
| `company_name` | string | 否 | 公司名称 |
| `template_path` | string | 否 | 报价单模板路径（null 使用默认模板） |

### 技能内部处理流程

```
itinerary_text（行程文本）
  → Step 1: 调用 LLM 解析行程 → 提取景点名称、人数、酒店偏好、天数等
  → Step 2: 用 Retriever 检索向量知识库 → 景点名称→doc_id，酒店偏好→doc_id
  → Step 3: 用 route-distance Skill 计算导航距离（按公里计费用）
  → Step 4: 查询车辆/餐饮/导游/其他费用定价
  → Step 5: 计算各项费用 → 汇总 → 导出 Excel
```

### 向后兼容

如果传入旧的结构化参数（`attraction_doc_ids`、`hotel_doc_id` 等）而不传 `itinerary_text`，走旧的直接计价模式。

## 输出格式

返回 JSON 的 `data.rows` 数组字段名**直接对应 Excel 报价单的表头列**（成本类别、项目、单价、数量、单位、次数、单位2、费用小计、随队老师、备注）。请严格按这些字段复述，**不要自行计算或换算**。

```json
{
  "success": true,
  "data": {
    "course_name": "超级贵州研学",
    "company_name": "贵州天悦旅行社有限公司",
    "start_date": "2026-07-01",
    "total_people": 30,
    "teacher_count": 3,
    "trip_days": 6,
    "rows": [
      {
        "成本类别": "用车",
        "项目": "大巴(45座)",
        "单价": 1800,
        "数量": 1,
        "单位": "辆",
        "次数": 6,
        "单位2": "天",
        "费用小计": 360.00,
        "随队老师": 0,
        "备注": "含司机餐补"
      },
      {
        "成本类别": "住宿",
        "项目": "贵阳酒店",
        "单价": 320,
        "数量": 2,
        "单位": "人",
        "次数": 2,
        "单位2": "夜",
        "费用小计": 320.00,
        "随队老师": 320.00,
        "备注": "贵阳2晚"
      }
    ],
    "合计_费用小计": 1861.53,
    "合计_随队老师": 560.00,
    "人均报价": 2223.33,
    "总价": 66700.00,
    "file_path": "/tmp/quote_xxx.xlsx",
    "internal_data": {
      "region_name": "贵州",
      "couples": 0,
      "season_type": "peak",
      "hotel_stays": [
        {"city": "贵阳", "area": "南明区", "nights": 2, "hotel_doc_id": 101},
        {"city": "平塘", "area": "", "nights": 1, "hotel_doc_id": 205}
      ],
      "items": [
        {
          "category": "用车",
          "name": "大巴(45座)",
          "unit_price": 1800,
          "quantity": 1,
          "unit": "辆",
          "frequency": 6,
          "freq_unit": "天",
          "subtotal": 360.0,
          "teacher_subtotal": 0,
          "remark": "含司机餐补"
        }
      ],
      "cost_per_person": 1861.53,
      "teacher_total": 560.0,
      "single_supplement": 0,
      "quote_per_person": 2223.33,
      "quote_total": 66700.0
    }
  }
}
```

### `internal_data` 字段说明

**⚠️ LLM 不得修改或解读 `internal_data` 的任何内容**，仅在客户要换酒店时**原样回传**给 `update_hotel.py` 使用。

`internal_data` 包含的内容：

| 字段 | 用途 |
|------|------|
| `items` | 原始计费结构（英文 key），`update_hotel.py` 直接复用 |
| `hotel_stays` | 酒店住宿清单（city / area / nights / hotel_doc_id），按城市定位要换的酒店 |
| `couples` | 夫妻对数，影响单房差计算 |
| `season_type` | 季节类型，保留供参考 |
| `region_name` | 区域名称，保留供参考 |
| 其他计费字段 | cost_per_person / quote_per_person / quote_total 等，仅作历史记录 |

`file_path` 是顶层独立字段，**不在** `internal_data` 内。

### 字段含义

`rows[]` 字段名直接对应 Excel 报价单的表头列，含义自明。其中：

- `费用小计`：本行的人均费用（**已是最终值**，不要再乘"单价×数量×次数"）
- `随队老师`：本行老师费用（**已是最终值**）
- `合计_费用小计`、`合计_随队老师`：表尾合计，**直接引用，不要自己累加 rows**

## 收到结果后的操作

1. **严格按 rows 数组复述报价明细**：字段含义与 Excel 报价单完全一致，**不要自行换算或重新计算**（如 `单价 × 数量 × 次数` 是错误的，应直接用"费用小计"列的值）
2. 汇总数据引用 `合计_费用小计`、`合计_随队老师`、`人均报价`、`总价`
3. 结果中的 `file_path` 是生成的 Excel 文件路径，**必须按系统提示词的「文件交付规则」用 cp 注册下载**，否则用户看不到文件；调用 cp 时必须传 `display_name`，使用面向客户的业务文件名，例如 `研学旅游报价单.xlsx` 或 `{目的地}研学旅游报价单.xlsx`，不要使用临时文件名
4. 告知客户可下载 Excel 查看完整明细

## 酒店局部更新（换酒店）

当客户对已生成的报价提出换酒店需求时，**不要重跑 `generate.py`**（会导致其他类别也重新匹配、结果不可控），而是调用 `update_hotel.py` 只重算住宿行。

### 调用方式

```python
skill_execute(
  skill="travel-quote",
  command="python scripts/update_hotel.py",
  content='<JSON 参数>'
)
```

### 输入参数（JSON）

```json
{
  "tenant_id": "租户ID",
  "internal_data": {
    // 上次 generate.py 返回的 internal_data 字段，原样回传，不要修改
  },
  "hotel_overrides": [
    {
      "city": "贵阳",
      "hotel_name": "贵阳凯宾斯基酒店"
    }
  ],
  "course_name": "超级贵州研学",
  "company_name": "贵州天悦旅行社有限公司",
  "start_date": "2026-07-01",
  "total_people": 30,
  "teacher_count": 3,
  "trip_days": 6,
  "template_path": null
}
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tenant_id` | string | 是 | 租户ID |
| `internal_data` | object | 是 | **上次 generate.py 返回的 `internal_data`，原样回传，不得修改** |
| `hotel_overrides` | array | 是 | 客户要换的酒店列表，按 city 定位 |
| `course_name` / `company_name` / `start_date` / `total_people` / `teacher_count` / `trip_days` | 各类型 | 否 | 表头信息，不传则沿用 internal_data 中的值 |
| `template_path` | string | 否 | 报价单模板路径（null 用默认） |

### `hotel_overrides` 元素说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `city` | string | 是 | 要换酒店的城市名（必须能在 `internal_data.hotel_stays` 中找到） |
| `hotel_name` | string | 是 | 客户选定的酒店**完整名称**（从 `knowledge_base_search` 返回的"酒店名称：XXX"字段取得）。脚本内部按名称反查 doc_id |

**`hotel_name` 取值规范**：

- 必须是 `knowledge_base_search` 返回结果中"酒店名称："字段后的完整名称，**不要带"酒店"前后缀也不要省略**。例如返回 `"酒店名称：荔波四季花园酒店"` 时，传 `"荔波四季花园酒店"`
- 不要传 `doc_id`——agent 拿不到 doc_id，也不需要传
- 酒店名歧义（同名多个）或查不到时，脚本会报错，让 agent 用更精确的酒店名重试

### 行为说明

- **只重算住宿行**：其他类别（用车、景点、餐饮、导游、其他费用）原样保留
- **保持原顺序**：住宿行原位置替换，不挪到末尾
- **按 city 定位**：客户一次只换某几个城市，未列出的 city 沿用旧酒店
- **生成新报价单**：返回新的 `file_path`，旧报价单保留便于对比
- **输入校验严格**：city 未匹配 / 新酒店无价格 / 住宿行数量不一致时立即报错，避免流出错误报价

### 输出格式

与 `generate.py` 完全一致（同 schema），包含新的 `rows`、合计、`file_path`、新的 `internal_data`。

## 注意事项

- **只需传入行程文本**，技能内部自动完成解析、检索、计算全流程
- **行程文本应该是客户已确认的完整方案**，包含景点、天数、人数等关键信息
- **template_path 为 null 时使用系统默认模板**
- **换酒店时不要重跑 generate.py**，用 `update_hotel.py` 局部更新

## ⚠️ 对客户的回复口径（必须严格遵守）

向客户总结报价时：

1. **只说报价**：使用"人均报价"、"总价"、"合计_费用小计"、"合计_随队老师"字段，**禁止**提及"成本"、"利润"、"利润率"、"加价后"、"加利润后"等字眼
2. **不要解释定价机制**：客户看到的 `人均报价`、`总价` 就是最终对外报价，**不要**拆解成"成本+利润"叙事
3. **不要拆分费用小计**：Excel 表头里的"成本类别"列名是模板固定字段（用于内部归类），向客户介绍时**不要**复述"成本类别"这个词，可以直接说"用车 / 门票 / 住宿"等具体类别名
