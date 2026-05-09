---
name: quote-generate
description: 研学旅游报价生成技能，查询定价数据库、计算各项费用、导出 Excel 报价单。当需要为研学旅游行程生成报价、计算费用、导出报价单时使用此技能。
metadata:
  version: "1.0.0"
  author: aid-work-agent
dependencies:
  - openpyxl>=3.1.0
---

# 研学旅游报价生成技能

## 适用场景

当用户确认了行程方案，需要生成详细报价时使用此技能。一次调用完成：查库 → 计算 → 导出 Excel。

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
  "region_name": "贵阳",
  "total_people": 30,
  "adults": 25,
  "children": 0,
  "children_half": 5,
  "students": 0,
  "elders": 0,
  "couples": 2,
  "trip_days": 6,
  "start_date": "2026-07-01",
  "attraction_ids": [1, 3, 5, 7],
  "hotel_id": 2,
  "meal_tier": "standard",
  "guide_type": "research",
  "vehicle_count": null,
  "include_insurance": true,
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
| `region_name` | string | 是 | 目的地区域（如"贵州"、"贵阳"） |
| `total_people` | int | 是 | 总人数 |
| `adults` | int | 是 | 成人数 |
| `children` | int | 否 | 6岁以下儿童（免票） |
| `children_half` | int | 否 | 6-18岁（半票） |
| `students` | int | 否 | 学生数（半票） |
| `elders` | int | 否 | 65岁以上老人（免票） |
| `couples` | int | 否 | 夫妻对数（影响排房） |
| `trip_days` | int | 是 | 行程天数 |
| `start_date` | string | 是 | 出发日期 YYYY-MM-DD |
| `attraction_ids` | int[] | 否 | 景点ID列表 |
| `hotel_id` | int | 否 | 酒店ID |
| `meal_tier` | string | 否 | 餐标档次编码（如 standard） |
| `guide_type` | string | 否 | 导游类型编码（如 research） |
| `vehicle_count` | int | 否 | 车辆数量（null 则自动推荐） |
| `include_insurance` | bool | 否 | 是否包含保险，默认 true |
| `profit_rate` | float | 否 | 利润率（null 则使用 extra.md 中的配置，默认 0.15） |
| `course_name` | string | 否 | 行程/课程名称 |
| `company_name` | string | 否 | 公司名称 |
| `template_path` | string | 否 | 报价单模板路径（null 使用默认模板） |

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
        "remark": "含司机餐补"
      }
    ],
    "total_cost": 58000.00,
    "cost_per_person": 1933.33,
    "single_supplement": 320.00,
    "profit_rate": 0.15,
    "quote_per_person": 2223.33,
    "quote_total": 66700.00,
    "file_path": "/tmp/quote_xxx.xlsx"
  }
}
```

## 收到结果后的操作

1. 用自然语言向客户展示报价明细（类别、项目、单价、小计）
2. 调用 `register_download_file(file_path=结果中的file_path, display_name="报价单名称.xlsx")` 注册下载
3. 告知客户可以下载报价单

## 注意事项

- **所有参数从对话中收集齐备后一次性传入**，不需要分步调用
- **脚本内部直接查询数据库获取定价**，不需要 LLM 预先查 knowledge_base_search
- **profit_rate 为 null 时使用默认 15%**，也可以在 extra.md 中配置
- **template_path 为 null 时使用系统默认模板**
