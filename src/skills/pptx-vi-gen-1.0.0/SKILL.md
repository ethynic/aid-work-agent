---
name: pptx-vi-gen
description: >
  基于客户 PPT 模板生成美观、原生可编辑演示文稿的技能，并支持对已生成
  PPT 做手术式修改。从用户上传的 .pptx 模板提取品牌视觉（主题色/字体/Logo），
  内容由 LLM 按版式 schema 重新创作，逐页用 python-pptx 原生形状渲染——
  所有文字在 PowerPoint/WPS 中可直接编辑。使用场景：按模板生成 PPT、
  模板风格 PPT、品牌VI PPT、美观演示文稿、套用公司模板做 PPT、
  修改已生成的 PPT（改字/加页/删页/移页/换页/换色）。
metadata:
  version: "2.2.0"
  author: aid-work-agent
  trigger_keywords:
    - 按模板生成PPT
    - 模板风格PPT
    - 品牌VI
    - 美观PPT
    - 套用模板
    - 修改PPT
    - 改PPT
    - PPT加一页
    - PPT换色
---

# 模板 VI → 原生可编辑 PPT 生成与修改技能

## 何时使用此技能

**使用本技能用于**：
- 用户上传了一份 .pptx 模板（公司模板/品牌模板），要求按模板风格生成 PPT
- 用户强调美观、专业排版、品牌视觉（VI/主题色/Logo）
- 用户没有模板但希望生成排版美观的演示文稿（不传 --template，用默认风格）
- 用户对**本技能生成的 PPT** 提修改意见（改字/加页/删页/移页/整页换内容/换色）

**不要使用本技能**（改用 ppt_process 工具）：
- 没有模板 VI 诉求的通用 PPT 处理（通用转换、解析、非本技能生成 deck 的修改）

**核心保证**：本技能所有产出均为 python-pptx 原生形状构建的可编辑 PPT
（文本框/圆角矩形/椭圆/图片，非贴图），文字在 PowerPoint/WPS 里可直接改。

## ⚠️ 严禁事项

**不要自己设计版式或逐页堆文字**——14 种版式的视觉排版（配色、间距、字体层级）
已由渲染引擎内置，自行拼凑必然效果差且浪费大量 token。所有生成必须通过
`skill_execute` 调用本技能的管线脚本：

```
✅ 正确：skill_execute(skill="pptx-vi-gen", command='python scripts/generate.py --template "模板.pptx" --content content.json --output "输出.pptx"')
❌ 错误：用 ppt_process 工具套模板 / 自己想版式逐页描述
```

## 路线选择

| 场景 | 入口 | 说明 |
|------|------|------|
| 模板 VI + AI 重创内容（**默认推荐**） | `generate.py` | 只提取模板配色/字体/Logo，逐页原生形状渲染，视觉最美观 |
| 生成后要复用模板母版版式/整页拷贝 | `template_fill.py` | 路线一：占位符填充或整页拷贝替换，像素级保留模板页 |
| 修改已生成的 PPT | `edit.py` | 手术式修改，只动指定内容，其余原样保留 |

## 路线二 · VI 生成（默认）

### 内容 schema（content.json，由你编写）

每页一个对象，按内容特点选 role。**版式分工**：cover（封面）和 end（结尾）各用
一次；toc（目录）放正文前；section（章节页）划分篇章；正文页按内容特点组合：
stats（大数字卡）/ bullets（要点列表）/ twocol（双栏对比）/ image（图片展示）/
table（数据表格）/ image_text（图文组合）/ image_grid（多图画廊）/ timeline（时间轴）/
process（流程步骤）/ quote（金句），避免连续多页同一 role。全部字段见
`references/usage.md` 第 1 节。

```jsonc
{
  "slides": [
    {"role": "cover",   "title": "主标题", "subtitle": "副标题", "date": "2026 年 10 月", "org": "部门/公司名"},
    {"role": "section", "no": "01", "title": "章节标题", "lead": "本章导语，一两句话"},
    {"role": "stats",   "title": "页标题", "lead": "页导语",
     "stats": [{"num": "32%", "label": "指标名", "desc": "一句具体说明"}, ...]},
    {"role": "bullets", "title": "页标题", "lead": "页导语",
     "items": [{"h": "要点小标题", "d": "一句具体描述"}, ...]},
    {"role": "twocol",  "title": "页标题",
     "left": {"h": "左栏标题", "items": ["条目", ...]}, "right": {"h": "右栏标题", "items": ["条目", ...]}},
    {"role": "image",   "title": "页标题", "lead": "页导语", "image": "图表.png", "caption": "图片说明"},
    {"role": "end",     "title": "谢谢观看", "subtitle": "结束语", "contact": "联系方式"}
  ]
}
```

**内容丰富度要求**（直接决定成品质量）：
- 每个 bullet 必须有 `h`（小标题）+ `d`（具体描述），不要只给一列短词
- stats 的 `num` 用大数字/百分比，`label` 简短，`desc` 写具体事实
- 每页尽量写 `lead` 导语；stats 建议 3~4 张卡，bullets 建议 3~6 条
- 所有描述要具体、有信息量，不要"提升效率、赋能业务"之类的空话
- 一份 PPT 建议 7~12 页

### 操作指南（步骤化）

**第 1 步：确认输入**
- 用户上传了 .pptx 模板：从会话附件取其**完整路径**
- 无模板：省略 --template，使用默认品牌风格
- 图片页素材：用户上传的图片同样取完整路径填入 `image` 字段

**第 2 步：编写 content.json 并调用管线（一条命令）**

```bash
python scripts/generate.py \
  --template "/path/客户模板.pptx" \
  --content content.json \
  --output "输出文件名.pptx" \
  --preview
```

- `--content -` 可从 stdin 读 JSON
- `--brand "#1F4E79"` / `--accent "#2E9CCA"`：客户只要 Logo 不要模板主题色时强制品牌色
- `--preview`：同时输出每页 PNG 到 `<输出名>_preview/`（QA 预览，便于向用户展示效果）

**第 3 步：解析 stdout JSON 结果**

```json
{
  "success": true,
  "pptx": "/path/输出文件名.pptx",
  "slides": 8,
  "vi": {"brand": "#4F81BD", "accent": "#C0504D", "has_logo": true},
  "preview_dir": "/path/输出文件名_preview"
}
```

失败时 `success=false` + `error` 字段（如 role 非法、缺 title、文件不存在），修正 content.json 后重试。使用客户模板时输出可能带 `fonts_replaced`（模板商业字体被替换为免费字体的清单）。

**第 4 步：用 cp 交付**

```python
cp(src="<pptx 路径>", register_download=True)
```

**第 5 步：向用户汇报**
- 页数、采用的 VI（品牌色/是否有 Logo，来自模板还是默认风格）
- 说明成品为原生可编辑 PPT，文字可直接在 PowerPoint/WPS 中修改；
  如需调整（改字/加页/换色等）可直接提出，用 edit.py 迭代修改

## 修改已生成的 PPT（迭代修改）

用户对已生成的 deck 提修改意见时，把自然语言需求翻译成 `edit_spec` JSON 调用：

```bash
python scripts/edit.py --pptx "输出文件名.pptx" --spec edit_spec.json
```

- 缺省**原地修改**（自动留 `.bak` 备份）；传 `--output` 可写到新文件
- 只动 op 涉及的内容，其余原样保留（用户手工改过的部分也不受影响）
- `slide` 均为 1 基页码；结构变动（加/删/移）后页脚页码自动刷新
- 新增 section 页要在 `page` 里显式带 `no`；配色自动取自 deck 现有调色板

| op | 关键字段 | 说明 |
|----|---------|------|
| `replace_text` | `find`/`replace`/`scope`（页码或 `"deck"`） | 保留原格式的文本替换，跨 run 匹配自动合并 |
| `set_text` | `slide` + `match`（或 `shape` 序号）+ `text` | 整框替换（`\n` 分段），保留首个 run 的格式 |
| `add_slide` | `role` + `page`（角色字段）+ 可选 `after` | 用原生渲染器加一页，可插到指定页之后 |
| `replace_slide` | `slide` + `role` + `page` | 整页换内容（原位替换） |
| `delete_slide` | `slide` | 删页 |
| `move_slide` | `slide` + `to` | 移页到新位置 |
| `recolor` | `accent`/`brand`/`dark`，或 `map` {旧hex: 新hex} | 全局换色（文本/形状/渐变全覆盖） |

edit_spec 完整字段与示例见 `references/usage.md` 第 8 节。stdout 返回
`applied`（每个 op 的执行结果）与 `warnings`（失败项，需修正后重试）。

**验证改动**：用 python-pptx 结构 dump 检查（`python -c` 遍历 slides/shapes
打印文本），或用 `--output` 写新文件后向用户确认。`replace_text` 的 applied
计数为 0 时说明没找到目标文本，应换 `set_text` + `match` 精确定位。

## 路线一 · 模板填充（template_fill.py）

适合"要像素级复用模板页"或客户模板很规范的场景。两步走：

```bash
# 1. 解析模板版式/占位符/每页文本（JSON，供你决策）
python scripts/template_fill.py analyze --template "/path/客户模板.pptx"

# 2. 按 deck_spec 生成
python scripts/template_fill.py generate --template "/path/客户模板.pptx" \
  --spec deck_spec.json --output "输出文件名.pptx"
```

deck_spec 每页二选一（同一 spec 内可混用）：
- `{"mode": "placeholder", "layout_index": N, "fields": {"占位符idx": "文本 或 [多行列表]"}}` —— 复用母版占位符，保留母版背景/Logo
- `{"mode": "copy", "template_slide_index": N, "replacements": {"原文片段": "新文本"}}` —— 整页拷贝后 run 级文本替换，像素级复刻含图片

默认删除原模板页只留生成页；`--keep-template-slides` 保留。

## 参数说明

| 脚本 | 参数 | 必填 | 说明 |
|------|------|------|------|
| `generate.py` | `--content` | 是 | content.json 路径，或 `-` 表示 stdin |
| | `--template` | 否 | 客户 .pptx 模板路径；省略则用默认 VI |
| | `--output` | 否 | 输出文件名（默认 `vi_ppt_<时间戳>.pptx`） |
| | `--brand` / `--accent` | 否 | 强制品牌色/强调色（十六进制，如 `#1F4E79`） |
| | `--preview` | 否 | 输出每页 PNG 预览目录 |
| | `--embed-fonts` | 否 | 嵌入思源黑体/Noto Sans 进 pptx（约 +18MB，未装字体的机器也原样渲染） |
| `edit.py` | `--pptx` | 是 | 目标 deck 路径 |
| | `--spec` | 是 | edit_spec.json 路径，或 `-` 表示 stdin |
| | `--output` | 否 | 输出路径（缺省原地修改，自动留 `.bak`） |
| `template_fill.py` | `analyze --template` | 是 | 解析模板，输出版式/占位符 JSON |
| | `generate --template --spec --output` | 是 | 按 deck_spec 生成 |

## 商用免费字体策略（2.2.0 起强制）

交付客户的 PPT **不携带任何字体授权风险**，按字符所属脚本自动选字体（与 PowerPoint/WPS 原生行为一致）：

- **双轨规则**：ea（中文）→ **思源黑体 CN**（Source Han Sans CN），latin/cs（字母/数字）→ **Noto Sans**（两者均为 SIL OFL 1.1 商用免费，随包分发在 `scripts/pptx_gen/assets/fonts/`）。思源的拉丁字形偏宽，数字/字母单独用 Noto Sans 更干净。
- **路线二**：每个 run 显式写 latin=Noto Sans / ea=思源黑体；量测与 QA 预览按字符分流用同一份字体文件，保证「测得准 = 渲染一致」。
- **路线一与 edit.py**：保存前把模板里的商业字体（微软雅黑/宋体/Arial/Calibri 等）按标签分流替换为免费字体；免费白名单字体（思源/Noto/MiSans/普惠体/HarmonyOS Sans 等）原样保留。替换清单在返回 JSON 的 `fonts_replaced` 字段，可审计。
- **字体嵌入（opt-in）**：`--embed-fonts` 把两款字体嵌进 pptx（约 +18MB），未安装字体的电脑也能原样渲染；默认关闭（未嵌入时打开方按度量兼容字体替换，版式不散——单行元素禁换行 + 0.92 宽度预算兜底）。

## 边界与已知限制

- 内置 14 种版式（cover/toc/section/stats/bullets/twocol/image/table/image_text/
  image_grid/timeline/process/quote/end），支持原生表格；没有 SmartArt、动画
- `image` role 的图片按等比缩放放入卡片，建议用户上传清晰的大图
- 模板中提取不到主题色时使用默认深蓝品牌色
- 路线二的 QA 预览 PNG（Pillow 渲染）与原生 deck 排版近似但不逐像素一致，
  以 PowerPoint/WPS 打开效果为准
