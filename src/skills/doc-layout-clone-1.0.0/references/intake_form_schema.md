# 信息采集表 fields.json 格式（doc-layout-clone 技能）

## 用途

当第 3 步需要用户补充的信息**较多**（经验阈值：≥6 个字段，或需用户提供财务 / 人员 / 知识产权 / 市场等多类结构化数据），
不要在聊天里逐条追问，改为生成一份可填写的 Word 信息采集表：

- 每个字段给出 `sample`（示例值）时，该值在表里以**黄色高亮**呈现，表示"这是示例，请替换为真实值"。
- 用户可以直接在 Word 里改；也可以说"先用示例数据"——此时不改，技能把示例值当作本次答案回填。
- 用户填完后，用 `--extract` 把值读回，写进 `answers.json` 再走第 4 步。

**红线**：`fields.json` 里的字段与示例值只能由本次读样版分析得出，属于**本次任务产物**，放在工作目录，**不写进技能包**。
脚本 `make_intake.py` 不含任何业务常量。

## fields.json 结构

```json
{
  "title": "国瀛科技 · 专精特新诊断报告 信息采集表",
  "note": "黄色高亮=示例数据，请替换为真实值；如确认沿用示例，保留高亮即可。* 为必填。",
  "groups": [
    {
      "name": "一、主体信息",
      "fields": [
        {
          "key": "company_name",
          "label": "公司全称",
          "sample": "国瀛科技",
          "hint": "营业执照登记全称",
          "sample_from": "模板样版",
          "required": true
        },
        {
          "key": "revenue_2025",
          "label": "2025 营业收入（万元）",
          "sample": "4500",
          "hint": "与财报口径一致",
          "required": false
        }
      ]
    }
  ]
}
```

### 字段含义

| 键 | 必填 | 作用 |
|----|------|------|
| `title` | 否 | 文档主标题；命令行 `--title` 可覆盖 |
| `note` | 否 | 标题下的说明；命令行 `--note` 可覆盖。应写明黄色高亮含义 |
| `groups[].name` | 是 | 分组小标题，如"一、主体信息""二、经营财务" |
| `groups[].fields[].key` | 是 | 字段唯一标识；回收时作为 values.json 的键；应与 `answers.json` 里的键一致或可映射 |
| `groups[].fields[].label` | 是 | 表中"信息项"列显示的中文名 |
| `groups[].fields[].sample` | 否 | 示例 / 预填值。**非空即黄色高亮**；留空则留白待填 |
| `groups[].fields[].hint` | 否 | "口径 / 说明"列，写数据口径、佐证材料、注意事项 |
| `groups[].fields[].sample_from` | 否 | 示例来源标注（如"模板样版""公开信息"），拼进说明列 |
| `groups[].fields[].required` | 否 | true 时 label 加 `*`，提示必填 |

## 生成

```
python scripts/make_intake.py <工作目录>/fields.json <工作目录>/信息采集表.docx \
    [--title "..."] [--note "..."]
```

输出 JSON：`out` / `groups` / `fields` / `highlight`（恒为 `yellow=示例数据`）。
生成后用 `cp` 工具把采集表复制为用户可见的交付文件，告诉用户：黄色是示例、可直接改、或回一句"先用示例数据"。

## 回收

用户填写后：

```
python scripts/make_intake.py --extract <填好的信息采集表.docx> \
    <工作目录>/fields.json --out <工作目录>/intake_values.json
```

输出 JSON：`count` / `values`（key → 用户填写值）。
把 `values` 映射到 `answers.json` 对应字段（示例值未改即为沿用示例），再走第 4 步回填。
回收按 `fields.json` 的分组与行序定位到表格第 3 列，**不依赖用户是否保留高亮**。

## 判定何时启用采集表

| 情形 | 做法 |
|------|------|
| 待改字段 ≤5，且都是简单值 / 方向选择 | 按原第 3 步在对话里一次问全 |
| 待改字段 ≥6，或需用户提供财务 / 人员 / 知产 / 市场等结构化数据 | **生成信息采集表**，让用户离线填写 |
| 用户明确说"信息太多，给我个表填" | 生成信息采集表 |
| 用户说"先用示例数据" | 无需用户填写；把 sample 当答案直接回填，但在交付说明里提示这些是示例值 |
