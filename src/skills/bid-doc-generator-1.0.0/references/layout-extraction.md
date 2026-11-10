# 版式提取与推导（Layout Extraction）

> **铁律**：版式**不得固化在技能里**。渲染器只负责执行 `layout.json`；
> `layout.json` 的每一个字段都必须能追溯到「甲方招标要求」或「历史标书范式」二者之一。
> 若两者都未给出，则该字段留空由默认兜底，并记入 `pending_items` 提示人工确认。

---

## 一、两个来源，谁优先

| 来源 | 提供什么 | 权威性 |
|---|---|---|
| **甲方招标要求** | 强制性的格式条款（字体、字号、纸张、装订、份数、目录、签章、页数上限、密封） | **最高**，违反即可能否决投标或扣分 |
| **历史标书范式** | 风格类版式（字体搭配、封面形态、表格样式、页眉页脚、标题层级编号体系、语体） | 次之，甲方未规定处沿用 |

**冲突时以甲方为准**，并在 `layout.json` 的 `notes` 中记录冲突与取舍理由。

---

## 二、从甲方招标要求中要提取什么

按以下顺序通读招标书，逐项落到 `layout.json`：

### 1. 字体与字号（最易漏、后果最重）
常见表述位置：投标人须知前附表「需要补充的其他内容」、评标办法「技术方案评分」、
投标文件格式章节说明。

典型条款与映射：

| 招标书原文 | layout 字段 |
|---|---|
| 「技术方案部分应主要采用"宋体""小四"字体编写」 | `body.font="宋体"` `body.size_pt=12`（小四=12pt） |
| 「正文采用宋体四号」 | `body.size_pt=14`（四号=14pt） |
| 「标题使用黑体三号」 | `heading.*.font="黑体"` `heading.1.size_pt=16`（三号=16pt） |
| 「小五号」 | 9pt　「五号」10.5pt　「小四」12pt　「四号」14pt　「小三」15pt　「三号」16pt　「二号」22pt　「初号」42pt |

> 中文字号与磅值对照：小初36 / 初42 / 小初36 → 实际用
> 小二18 / 二22 / 小三15 / 三16 / 小四12 / 四14 / 小五9 / 五10.5 / 六7.5 / 小六6.5

**注意**：条款只约束「技术方案部分」时，只需保证 `body` 与技术标各文件符合；
若未区分正文与表格，须同时设定 `table`。

### 2. 纸张与页边距
- 「A4 规格」→ `page.size="A4"`；未提及则沿用历史标书；再无则默认 A4。
- 页边距通常招标书不规定 → 沿用历史标书；无历史则用行业常规（上下 2.8/2.5cm，左右 3cm），
  并在 `notes` 标注「沿用默认，无明确规定」。

### 3. 装订与份数
| 招标书原文 | layout 字段 |
|---|---|
| 「按 A4 规格胶装」 | `page.binding="胶装"` |
| 「正副本分开装订，不得活页装订」 | `page.binding="胶装"` `page.separate_copies=true` |
| 「不需要分册装订」 | `page.multi_volume=false` |
| 「需要分册装订」 | `page.multi_volume=true` |
| 「纸质文件伍份（正本壹份，副本肆份）」 | `copies={"original":1,"copy":4}` |
| 「封面右上角显著标明正本/副本」 | `cover.show_copy_mark=true` |

### 4. 目录与页码
- 「编制目录」→ `toc.enabled=true`
- 「页眉/页脚」要求 → `header.enabled` / `footer.enabled`，`footer.page_number=true`

### 5. 签章要求
- 「逐页加盖单位公章」或「加盖骑缝章」→ `signature.per_page_seal` / `signature.cross_page_seal`
- 「法定代表人或委托代理人签字并加盖单位公章」→ 每份文件末尾必须有签字区 → `signature.need_sign_area=true`

### 6. 页数上限
- 「控制在 400 页（单面）以内，否则扣分」→ `page.max_pages=400`
  → 由 `make_package.py` 统计实际页数并校验（超限则 FAIL）。

### 7. 其他格式条款
- 表格样式是否规定 → `table.header_shading` / `table.border`
- 是否要求密封、封套文字 → 属递交操作，不进 layout，但记入 `pending_items`

---

## 三、从历史标书中提取什么

甲方未规定处，沿用历史中标标书：

| 观察点 | layout 字段 |
|---|---|
| 正文字体与字号 | `body.font` `body.size_pt` |
| 行距（1.5 倍 / 固定值） | `body.line_spacing` |
| 首行缩进（2 字符 / 无） | `body.first_line_indent_chars` |
| 标题字体、字号、加粗、居中或左对齐 | `heading.1~3` |
| 封面形态（项目名+投标文件+投标人+日期，标题字号） | `cover.*` |
| 表格：表头是否加粗、是否灰底、边框粗细、字号 | `table.*` |
| 页眉内容与位置、页脚格式（第X页共Y页） | `header.*` `footer.*` |
| 层级编号体系（一、→（一）→ 1. → (1)） | `heading.numbering` |
| 章节起止写法、图表题注格式 | `caption.*` `note.*` |

**提取方式**：读历史标书的实际文件（docx/pdf），对照其样式与视觉特征，
不要凭印象。历史标书只在「风格」上参考，**不得复制其实质内容**。

---

## 四、`layout.json` Schema

```jsonc
{
  "_source": {
    "tender": "第二章 投标人须知 前附表 10.3 第6项、3.7.3A(3)",
    "history": "东鹏某 Top10 房企集采中标标书（历史范例）",
    "notes": "甲方明确要求技术方案用宋体小四→body 照此设定；页边距甲方未规定→沿用历史标书"
  },

  "page": {
    "size": "A4",                    // A4 | B5 | Letter | 自定义
    "orientation": "portrait",       // portrait | landscape
    "margin_cm": {"top": 2.8, "bottom": 2.5, "left": 3.0, "right": 3.0},
    "header_distance_cm": 1.5,
    "footer_distance_cm": 1.5,
    "binding": "胶装",                // 胶装 | 活页 | 骑马钉 | 无线胶装（按甲方原文填）
    "separate_copies": true,          // 正副本分开装订
    "multi_volume": false,            // 是否需要分册装订
    "max_pages": 400,                 // 页数上限；null 表示未规定
    "max_pages_scope": "技术方案部分"   // 上限适用范围说明
  },

  "body": {
    "font": "宋体",
    "size_pt": 12,
    "line_spacing": 1.5,              // 倍数或磅值
    "first_line_indent_chars": 2,
    "align": "justify",              // justify | left | center
    "space_after_pt": 6
  },

  "heading": {
    "1": {"font": "黑体", "size_pt": 16, "bold": true, "align": "center", "space_before_pt": 6, "space_after_pt": 10},
    "2": {"font": "黑体", "size_pt": 14, "bold": true, "align": "left",   "space_before_pt": 12, "space_after_pt": 8},
    "3": {"font": "楷体", "size_pt": 12, "bold": true, "align": "left",   "space_before_pt": 8,  "space_after_pt": 6}
  },

  "table": {
    "font": "宋体",
    "size_pt": 9,
    "header_bold": true,
    "header_shading": "D9D9D9",      // 表头底纹；null = 不加底纹
    "border": true,
    "border_sz": 4,                  // 1/8 磅
    "border_color": "000000",
    "header_repeat": true,           // 跨页重复表头
    "cell_v_align": "center",
    "header_align": "center",
    "align": "left",                 // 默认正文列对齐
    "numeric_align": "right"         // 数值列对齐
  },

  "caption": {"font": "黑体", "size_pt": 10.5, "bold": true, "align": "center", "space_before_pt": 8},
  "note":    {"font": "楷体", "size_pt": 9.5,  "align": "left",  "space_after_pt": 8},
  "sign":    {"font": "宋体", "size_pt": 12,    "align": "right", "space_after_pt": 2},
  "list":    {"font": "宋体", "size_pt": 12,    "space_after_pt": 3},

  "cover": {
    "enabled": true,
    "title_size_pt": 22,
    "subtitle_size_pt": 18,
    "volume_size_pt": 14,
    "info_size_pt": 14,
    "align": "center",
    "blank_lines_before": 3,
    "show_bidder": true,
    "show_copy_mark": true,          // 封面右上角标「正本/副本」
    "copy_mark_position": "top_right"
  },

  "toc": {"enabled": true, "title": "目　录", "title_font": "黑体", "title_size_pt": 16, "levels": "1-3"},

  "header": {"enabled": true, "text": "", "font": "宋体", "size_pt": 9, "align": "center"},
  "footer": {"enabled": true, "text": "", "font": "宋体", "size_pt": 9, "align": "center",
             "page_number": true, "page_format": "第 {page} 页 共 {total} 页", "separator": "    "},

  "signature": {
    "need_sign_area": true,
    "per_page_seal": false,          // 逐页盖章
    "cross_page_seal": true          // 骑缝章
  },

  "copies": {"original": 1, "copy": 4}
}
```

---

## 五、字段来源标注约定

每个非默认字段，在 `_source.notes` 中写明出处，格式：

```
正文宋体小四←招标书前附表10.3-6；页边距上下2.8/2.5左右3←历史标书；
封面标题22pt←历史标书；表头灰底D9D9D9←历史标书；表格边框←甲方未规定沿用历史
```

**自检**：渲染完成后，`scripts/check_layout.py` 会把 `requirements.json` 中
`category="format"` 的条款与 `layout.json` 逐条比对，不一致则 FAIL。

---

## 六、常见坑

1. **只改了 body 没改 table**：甲方说「技术方案用宋体小四」时，表格字号若仍是五号，
   评审可能认为未按要求执行 → 版式校验会同时检查 `body` 与 `table`。
2. **页数超限**：甲方限 400 页时，技术标册必须实测页数，不能估算。
3. **副本未标**：招标书要求封面右上角标「正本/副本」，`cover.show_copy_mark` 必须为 true，
   并在生成时提示需分别生成正本与副本版本。
4. **分册与不装订矛盾**：招标书说「不需要分册装订」但输出多册时，
   说明是编制单元而非装订单元，最终合订成一本；`page.multi_volume=false`。
5. **层级编号体系**：若历史标书用「一、/（一）/ 1. / (1)」，标题文字里应保留该编号，
   不要用渲染器自动生成，避免与既有文风冲突。
