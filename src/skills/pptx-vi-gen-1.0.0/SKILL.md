---
name: pptx-vi-gen
description: >
  基于客户 PPT 模板生成美观演示文稿的技能。从用户上传的 .pptx 模板提取
  品牌视觉（主题色/字体/Logo），内容由 LLM 按版式 schema 重新创作，
  逐页渲染成美观成品。使用场景：按模板生成 PPT、模板风格 PPT、
  品牌VI PPT、美观演示文稿、套用公司模板做 PPT。
metadata:
  version: "1.0.0"
  author: aid-work-agent
  trigger_keywords:
    - 按模板生成PPT
    - 模板风格PPT
    - 品牌VI
    - 美观PPT
    - 套用模板
---

# 模板 VI → 美观 PPT 生成技能

## 何时使用此技能

**使用本技能用于**：
- 用户上传了一份 .pptx 模板（公司模板/品牌模板），要求按模板风格生成 PPT
- 用户强调美观、专业排版、品牌视觉（VI/主题色/Logo）
- 用户没有模板但希望生成排版美观的演示文稿（不传 --template，用默认风格）

**不要使用本技能**（改用 ppt_process 工具）：
- 用户需要生成后**继续编辑文字**的 PPT —— 本技能产出的是整页图片版 PPT，文字不可编辑
- 用户只是要一个普通可编辑 PPT，没有模板和美观要求

## ⚠️ 严禁事项

**不要自己设计版式或逐页堆文字**——7 种版式的视觉排版（配色、间距、字体层级）
已由渲染引擎内置，自行拼凑必然效果差且浪费大量 token。所有生成必须通过
`skill_execute` 调用本技能的管线脚本：

```
✅ 正确：skill_execute(skill="pptx-vi-gen", command='python scripts/generate.py --template "模板.pptx" --content content.json --output "输出.pptx"')
❌ 错误：用 ppt_process 工具套模板 / 自己想版式逐页描述
```

## 内容 schema（content.json，由你编写）

每页一个对象，按内容特点选 role。**版式分工**：cover（封面）和 end（结尾）各用
一次；section（章节页）用于划分篇章；正文页用 stats（大数字卡）/ bullets（要点列表）/
twocol（双栏对比）/ image（图片展示）组合，避免连续多页同一 role。

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

## 操作指南（步骤化）

### 第 1 步：确认输入

- 用户上传了 .pptx 模板：从会话附件取其**完整路径**
- 无模板：省略 --template，使用默认品牌风格
- 图片页素材：用户上传的图片同样取完整路径填入 `image` 字段

### 第 2 步：编写 content.json 并调用管线（一条命令）

把上表 schema 填好，**先写 content.json 到工作目录**，再执行：

```bash
python scripts/generate.py \
  --template "/path/客户模板.pptx" \
  --content content.json \
  --output "输出文件名.pptx" \
  --preview
```

- `--content -` 可从 stdin 读 JSON
- `--brand "#1F4E79"` / `--accent "#2E9CCA"`：客户只要 Logo 不要模板主题色时强制品牌色
- `--preview`：同时输出每页 PNG 到 `<输出名>_preview/`，便于向用户展示效果

### 第 3 步：解析 stdout JSON 结果

```json
{
  "success": true,
  "pptx": "/path/输出文件名.pptx",
  "slides": 8,
  "vi": {"brand": "#4F81BD", "accent": "#C0504D", "has_logo": true},
  "preview_dir": "/path/输出文件名_preview"
}
```

失败时 `success=false` + `error` 字段（如 role 非法、缺 title、文件不存在），修正 content.json 后重试。

### 第 4 步：用 cp 交付

```python
cp(src="<pptx 路径>", register_download=True)
```

### 第 5 步：向用户汇报

- 页数、采用的 VI（品牌色/是否有 Logo，来自模板还是默认风格）
- **明确说明**：成品为整页图片版式，文字不可再编辑；如需可编辑版本可改用 ppt_process 工具

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--content` | 是 | content.json 路径，或 `-` 表示 stdin |
| `--template` | 否 | 客户 .pptx 模板路径；省略则用默认 VI |
| `--output` | 否 | 输出文件名（默认 `vi_ppt_<时间戳>.pptx`） |
| `--brand` / `--accent` | 否 | 强制品牌色/强调色（十六进制，如 `#1F4E79`） |
| `--preview` | 否 | 输出每页 PNG 预览目录 |

## 边界与已知限制

- 输出 PPT 为 16:9 整页图片，**文字不可编辑**（一次性汇报/展示场景适用）
- 内置 7 种版式（cover/section/stats/bullets/twocol/image/end），没有表格、SmartArt、动画
- `image` role 的图片按等比缩放居中放入卡片，建议用户上传清晰的大图
- 模板中提取不到主题色时使用默认深蓝品牌色
