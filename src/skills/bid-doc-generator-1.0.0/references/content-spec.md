# 内容规格（Content Spec）· 大模型输出契约

> **红线**：大模型只做三件事 —— **判断**（识别要求性质、判定偏离）、**内容输出**（写正文、填表格）、**整合**（跨文件统一口径）。
> 所有**文件操作**（读招标书、建目录、渲染 Word、生成偏离表、打包、自检）一律由 `scripts/` 下的脚本完成。
> 大模型**不写 .docx、不手工拼 XML、不自己算价**。

---

## 一、两个 JSON 文件

| 文件 | 谁产出 | 谁消费 | 作用 |
|---|---|---|---|
| `tender_extracted.json` | `extract_tender.py` | 大模型 | 机械解析的招标书全文（章节树 + 表格），**只读** |
| `layout.json` | **大模型** | `check_layout.py` / `render_docs.py` | **本次版式配置**：由甲方招标要求 + 历史标书范式推导 |
| `price_input.json` | **大模型** | `price_quote.py` | 我方单价 + 甲方数量 + 税率 + 限价（单价来自我方价格输入） |
| `price_result.json` | `price_quote.py` | `render_docs.py` | 分项合价、含税合价、总价、大写金额、限价校验 |
| `matrix.json` | **大模型** | `build_matrix.py` / `render_docs.py` | 资格/技术/商务三张对照表 |
| `content.json` | **大模型** | `render_docs.py` + `make_package.py` | 投标文件全部内容与结构 |
| `checks.json` | **大模型** | `make_package.py` | 跨文件一致性自检项 |

---

## 二、执行流水线（固定顺序，不得跳步）

```bash
# 0) 建工作目录
mkdir -p work/{inputs,parsed,outputs}

# 1) 机械解析招标书（脚本）
python scripts/extract_tender.py <招标书.docx> <参数表.xlsx> <公告.pdf> --out work/parsed/

# 2) 【大模型】读 work/parsed/tender_extracted.json → 做判断 → 写
#    work/requirements.json（要求清单，含 category="format" 的格式条款）
#    work/matrix.json（偏离矩阵）
#    work/price_input.json（单价来自我方价格输入）
#    work/layout.json（★版式：甲方招标要求 + 历史标书范式，见 layout-extraction.md）
#    work/content.json（全部文件内容与结构）
#    work/checks.json（自检项）

# 3) 报价计算与限价校验（脚本）
python scripts/price_quote.py --input work/price_input.json --out work/price_result.json

# 4) 版式合规校验（脚本）—— FAIL 必须修
python scripts/check_layout.py --layout work/layout.json \
    --requirements work/requirements.json --report work/LAYOUT_CHECK.md

# 5) 偏离表（脚本）
python scripts/build_matrix.py work/matrix.json --out-dir work/outputs

# 6) 渲染 Word（脚本）—— 版式来自 layout.json
python scripts/render_docs.py work/content.json --out work/outputs \
    --layout work/layout.json [--copy-mark 正本|副本]

# 7) 打包 + 一致性自检（脚本）
python scripts/make_package.py --content work/content.json --out work/outputs \
    --expected "a.docx,b.docx" --checks work/checks.json
```

---

## 三、`content.json` 结构

```jsonc
{
  "meta": {
    "project_name": "…",       // 招标项目全称（页眉/封面/页脚统一用这一份）
    "bidder": "…",             // 投标人全称
    "tenderer": "…",           // 招标人
    "agency": "…",             // 招标代理
    "date_cn": "2026 年 6 月"
  },

  "docs": [
    {
      "file": "01-投标函.docx",   // 文件名（含册号前缀，便于排序与装订）
      "title": "投标函",
      "volume": "第一册",        // 可选，封面显示
      "cover": {"title": "…", "subtitle": "投标文件", "show_bidder": true},  // 可选
      "sections": [
        {
          "heading": "一、投标函",  // 可为 null
          "level": 1,
          "blocks": [ … ]
        }
      ]
    }
  ],

  "pending_items": [              // 需人工补齐项，进入 PACKAGE.md
    "营业执照复印件需加盖公章后扫描插入"
  ]
}
```

### block 类型

| type | 用途 | 关键字段 |
|---|---|---|
| `p` | 段落 | `text` / `segments`（富文本）/ `bold` `align` `first_line` `size` `font` |
| `heading` | 段内小标题 | `text` `level`(2/3) |
| `table` | 表格 | `header[]` `rows[][]` `widths[]`(cm) `aligns[]` `caption` `note` |
| `kv` | 两列信息表 | `rows[[k,v]]` `widths` |
| `price_table` | **报价表（读 price_result.json）** | `source` `style`("tender"=招标书12列 / "simple") `caption` |
| `matrix_table` | **偏差表（读 matrix.json）** | `source` `matrix`("technical"/"commercial"/"qualification") `caption` `note` |
| `list` | 列表 | `items[]`（字符串或 `{text,level,bold}`）`ordered` |
| `note` | 小字注释 | `text` |
| `sign` | 签字盖章区 | `lines[]` `align` |
| `toc` | 目录域（Word 内 F9 更新） | — |
| `pagebreak` | 分页 | — |

> **`price_table` / `matrix_table` 必须优先使用**：
> 报价数字与偏离结论由脚本从 `price_result.json` / `matrix.json` 直接渲染，
> **大模型不得手抄金额与偏离符号**，否则跨文件必然不一致。

### 表格宽度速查（A4 纵向，页边距 3cm → 可用宽 15cm）
- 2 列：`[5, 10]`
- 3 列：`[3, 9, 3]` / `[4, 6, 5]`
- 金额列右对齐：`aligns: ["center","right","right"]`

---

## 四、`requirements.json`（要求清单 / 考题）

```jsonc
{
  "project": "…",
  "requirements": [
    {
      "req_id": "Q1",
      "category": "qualification|technical|commercial|scoring|format|veto",
      "source": "第二章 投标人须知 前附表 1.4.1",
      "requirement": "原文摘录",
      "evidence_type": "营业执照/审计报告/检测报告/承诺函",
      "mandatory": true,
      "location": "资格审查资料（一）",
      "note": ""
    }
  ]
}
```

`category` 决定落点：
- `qualification` → 资格审查资料册
- `technical` → 技术标 + 技术性能详细描述 + 技术偏差表
- `commercial` → 商务标 + 商务偏差表
- `scoring` → 技术标（须针对性写到，否则扣分）
- `format` → 封面/目录/份数/密封/签章
- `veto` → 必须在 PASS 自检项中出现

---

## 五、`matrix.json`（偏离矩阵）

```jsonc
{
  "qualification": [
    {"req_id":"Q1","requirement":"…","our_evidence":"…","status":"满足|部分满足|不满足","note":""}
  ],
  "technical": [
    {"spec_id":"T1","requirement":"…","our_parameter":"…","deviation":"无偏离|正偏离|负偏离","evidence":"…","note":""}
  ],
  "commercial": [
    {"term_id":"B1","term":"…","requirement":"…","our_commitment":"…","deviation":"无偏离|正偏离|负偏离","note":""}
  ]
}
```

**负偏离 = 否决风险**（当招标文件 1.11.1 规定「不允许负偏离」时）。宁可留空标 `[待补充]`，
也不得虚标「满足」。

---

## 六、`checks.json`（一致性自检项）

```jsonc
{
  "checks": [
    {"name":"总价唯一","type":"consistent","expect":"12924264\\.42",
     "files":"all","detail":"所有出现处必须一致"},
    {"name":"大写金额必须出现","type":"consistent","expect":"壹仟贰佰…",
     "files":["01-投标函.docx","02-报价一览表.docx"],"required":true},
    {"name":"偏差表无负偏离","type":"table_values","files":["07-商务和技术偏差表.docx"],
     "header":"偏离情况","column":"偏离情况","forbid":["负偏离","不满足","部分满足"]},
    {"name":"不得出现占位符","type":"not_contains","expect":"XXXX","files":"all"},
    {"name":"投标函含大写金额","type":"contains","expect":"人民币","files":["01-投标函.docx"]}
  ]
}
```

### 检查类型语义

| type | 语义 |
|---|---|
| `contains` | 目标文件必须包含 `expect` |
| `not_contains` | 目标文件不得包含 `expect` |
| `regex` | 目标文件必须匹配 `expect` |
| `consistent` | **出现即唯一**：目标文件中若出现 `expect` 匹配项，该文件内匹配值必须唯一（多值 → FAIL）。`required:true` 时文件未出现即 FAIL |
| `table_values` | 定位含 `header` 关键词的表格，抽取 `column` 列全部取值，检查是否命中 `forbid` 中任一禁用值 |

> `consistent` 默认「未出现不判失败」，因为同一数字本就不必出现在每一册；
> 只有确实要求每个目标文件都必须出现时（如大写金额）才加 `required:true`。

---

## 七、大模型职责边界（务必遵守）

| 允许 | 禁止 |
|---|---|
| 判断要求的性质、重要性、落点 | 直接生成 .docx 或改 Word 二进制 |
| 撰写方案性正文、承诺函文字 | 编造资质证书编号、业绩、检测数据 |
| 填表：把已确认的资质/参数/价格填进表格 | 自行推算价格（单价必须来自我方价格输入） |
| 标注 `[待补充]` | 把 `[待补充]` 悄悄删掉而不记入 `pending_items` |
| 判定偏离（对比招标参数 vs 我方参数） | 隐瞒负偏离 |
| 统一跨文件口径（金额/数量/术语） | 在不同文件里给出不一致的同一数字 |
| **推导版式**：从甲方招标格式条款 + 历史标书范式得出 `layout.json` | **在技能脚本、references 或 content.json 中写死字体/字号/行距/页边距等版式参数** |
| 在 `_source.notes` 标注每个版式字段的出处 | 甲方已规定却沿用历史范式（须以甲方为准） |

**输出路径约定**：大模型写 `work/content.json`、`work/requirements.json`、
`work/matrix.json`、`work/price_input.json`、`work/layout.json`、`work/checks.json` 六个文件；
其余全部由脚本生成。
