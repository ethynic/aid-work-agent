---
name: quote-generate
description: 研学旅游报价生成技能，传入用户确认的行程方案文本，自动解析行程、检索资源、计算费用、导出 Excel 报价单。
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
  skill="quote-generate",
  command="python scripts/generate.py",
  content='<JSON 参数>'
)
```

**必须通过 `content` 参数传递 JSON**，不要用命令行参数。

## 输入参数（JSON）

```json
{
  "tenant_id": "租户ID",
  "itinerary_text": "| 天数 | 时段 | 行程安排 | 游玩项目 | 备注 |\n|------|------|----------|----------|------|\n| D1 | 下午 | 贵阳接站，乘车前往平塘天文小镇 | — | 约2.5小时车程 |\n| D2 | 上午 | 天文体验馆、南仁东事迹馆 | 精品讲解导览、天象影院 | 21-30人团讲解 |\n...（完整5列表格原文）",
  "start_date": "2026-07-01",
  "profit_rate": null,
  "course_name": "超级贵州研学",
  "company_name": "贵州天悦旅行社有限公司",
  "template_path": null
}
```

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tenant_id` | string | 是 | 租户ID |
| `itinerary_text` | string | 是 | 用户确认的完整行程方案原文，**必须是5列Markdown表格格式**（天数、时段、行程安排、游玩项目、备注），不能压缩成自然语言段落 |
| `start_date` | string | 否 | 出发日期 YYYY-MM-DD，默认今天 |
| `profit_rate` | float | 否 | 利润率（null 则使用 extra.md 中的配置，默认 0.15） |
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

```json
{
  "success": true,
  "data": {
    "course_name": "超级贵州研学",
    "company_name": "贵州天悦旅行社有限公司",
    "region_name": "贵阳",
    "start_date": "2026-07-01",
    "trip_days": 6,
    "total_people": 30,
    "teacher_count": 3,
    "items": [
      {
        "category": "用车",
        "name": "大巴(45座)",
        "unit_price": 1800,
        "quantity": 1,
        "unit": "辆",
        "frequency": 6,
        "freq_unit": "天",
        "subtotal": 360.00,
        "teacher_subtotal": 0,
        "remark": "含司机餐补"
      },
      {
        "category": "住宿",
        "name": "贵阳酒店",
        "unit_price": 320,
        "quantity": 2,
        "unit": "人",
        "frequency": 2,
        "freq_unit": "夜",
        "subtotal": 320.00,
        "teacher_subtotal": 320.00,
        "remark": "贵阳2晚"
      }
    ],
    "price_per_person": 2223.33,
    "teacher_total": 1514.00,
    "total_price": 66700.00,
    "file_path": "/tmp/quote_xxx.xlsx"
  }
}
```

## 收到结果后的操作

1. 用自然语言向客户展示报价明细（类别、项目、单价、小计）
2. 调用 `register_download_file(file_path=结果中的file_path, display_name="报价单名称.xlsx")` 注册下载
3. 告知客户可以下载报价单

## 注意事项

- **只需传入行程文本**，技能内部自动完成解析、检索、计算全流程
- **行程文本应该是客户已确认的完整方案**，包含景点、天数、人数等关键信息
- **profit_rate 为 null 时使用默认 15%**，也可以在 extra.md 中配置
- **template_path 为 null 时使用系统默认模板**
