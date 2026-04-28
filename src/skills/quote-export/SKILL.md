---
name: quote-export
description: 研学旅游报价Excel导出技能，将结构化报价数据导出为格式化的Excel报价单(.xlsx)。当需要导出报价单、下载报价Excel时使用此技能。
metadata:
  version: "1.0.0"
  author: aid-work-agent
dependencies:
  - openpyxl>=3.1.0
---

# 研学旅游报价Excel导出技能

## 适用场景

- 将结构化报价数据导出为格式化的Excel报价单(.xlsx)
- 用户要求导出报价单、下载报价Excel、生成报价文件

## 使用说明

### 导出报价Excel

**当报价项较多时，必须通过 `content` 参数传递 items JSON，避免命令行参数过长被截断：**

```bash
# 导出报价Excel（推荐：使用 content 参数通过 stdin 传递 items）
skill_execute(
  skill="quote-export",
  command='python scripts/export_xlsx.py --course-name "超级贵州研学" --date "2025-07-01" --people 30 --total 2551.33',
  content='[{"category":"用车","name":"旅游大巴","unit_price":9800,"quantity":1,"unit":"团","frequency":1,"freq_unit":"次","subtotal":326.67,"remark":"40座6天全程"},{"category":"用餐","name":"研学正餐","unit_price":40,"quantity":1,"unit":"人","frequency":8,"freq_unit":"餐","subtotal":320.00,"remark":"标准餐标"}]'
)

# 注册下载
register_download_file(file_path="<输出的file_path>", display_name="超级贵州报价单_30人.xlsx")
```

**重要规则：**
1. 使用 `content` 参数传 items JSON 数组，不要用命令行 --items 参数
2. **所有字段值（category、name、remark 等）必须使用中文**，不要翻译成英文
3. Excel 内部的标题、表头、数据都应显示中文内容

### 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--title` | string | 否 | 报价表标题，默认"贵州天悦旅行社有限公司研学报价表" |
| `--course-name` | string | 是 | 课程名称 |
| `--date` | string | 是 | 出发日期 |
| `--people` | int | 是 | 参团人数 |
| `--items` | string(JSON) | 是 | 报价项JSON数组，每项包含category/name/unit_price/quantity/unit/frequency/freq_unit/subtotal/remark |
| `--total` | float | 是 | 总费用 |
| `--teacher-total` | float | 否 | 随队老师费用合计，默认0 |
| `--output` | string | 否 | 输出文件路径，不指定时自动生成到临时目录 |

### items 数组中每项的字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `category` | string | 成本类别（如：用车、住宿、餐饮） |
| `name` | string | 项目名称 |
| `unit_price` | number | 单价 |
| `quantity` | number | 数量 |
| `unit` | string | 数量单位（如：团、间、份） |
| `frequency` | number | 次数 |
| `freq_unit` | string | 次数单位（如：次、天） |
| `subtotal` | number | 费用小计（人均） |
| `teacher_fee` | number | 随队老师费用（可选） |
| `remark` | string | 备注 |

## Excel格式说明

导出的Excel报价单遵循标准报价模板格式：

1. **标题行**：合并A-J列，加粗居中，14pt字体，显示报价表标题
2. **信息行**：显示课程名称、日期、人数，分三组各占3列
3. **空行**：间隔
4. **表头行**：加粗居中，浅蓝底色，列标题为：成本类别、项目、单价、数量、单位、次数、单位、费用小计、随队老师、备注
5. **数据行**：按报价项逐行填写，同类别单元格自动合并居中，金额列右对齐并保留两位小数
6. **合计行**：合并A-G列显示"合计"，浅黄底色，显示总费用和老师费用合计
7. **审核签字区**：包含审核、成本审核员、研学负责人、财务部审核四个签字位

## 注意事项

- **items参数**：必须是合法的JSON数组字符串，命令行中用单引号包裹，内部用双引号
- **JSON转义**：如果命令行参数中包含特殊字符，确保正确转义
- **输出文件**：脚本输出JSON包含`file_path`字段，必须调用`register_download_file`注册后用户才能下载
- **金额格式**：单价、小计、合计自动格式化为千分位并保留两位小数
- **同类别合并**：相同成本类别的行会自动合并第一列单元格
- **Sheet名称**：取课程名称前31个字符作为Sheet名（Excel限制）
