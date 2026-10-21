# pptx_gen 详细使用文档

本文件是 `SKILL.md` 的展开：内容 schema 全字段、路线一的 `deck_spec` 结构、LLM 衔接提示词、与框架集成方式、扩展方法。

---

## 0. 快速开始（路线二，推荐）

```python
import os, glob, sys, json
SKILL_DIR = glob.glob(os.path.expanduser("~/.workbuddy/skills/pptx_gen"))[0]
sys.path.insert(0, SKILL_DIR)
from pptx_gen import extract_vi, generate_from_vi

tpl = "assets/demo_template.pptx"          # 客户模板（只需含 VI/Logo）
content = json.load(open("examples/demo_content.json", encoding="utf-8"))  # LLM 产出的内容
res = generate_from_vi(tpl, content, "out.pptx",
                       vi_override={"brand": "#14395F", "accent": "#FFB545"})
print(res)  # {'out': 'out.pptx', 'slides': 7}  —— 原生可编辑
```

---

## 1. 路线二 · 内容 schema（content.json）

顶层结构：

```json
{ "slides": [ { "role": "cover", ... }, ... ] }
```

每页是一个对象，必有 `role`，其余字段按角色不同。所有文本默认中文；`org` 是页脚公司名（可选）。

### cover（封面）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 主标题 |
| `subtitle` | str | | 副标题 |
| `date` | str | | 日期，与 `org` 用 `·` 拼接成页脚 meta |
| `org` | str | | 机构/公司名 |

### section（章节页）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `no` | str | | 大编号，缺省用页码 `01` |
| `title` | str | ✓ | 章节标题 |
| `lead` | str | | 一句引导语 |
| `org` | str | | 页脚公司名 |

### bullets（要点页）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `items` | list | ✓ | 每项是 `{ "h": 小标题, "d": 一句描述 }` |
| `org` | str | | 页脚 |

### stats（数据卡，最多 4 列）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `stats` | list | ✓ | 每项 `{ "num": "大数字", "label": "标签", "desc": "说明(可选)" }` |
| `org` | str | | 页脚 |

### twocol（双栏对照）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `left` | obj | ✓ | `{ "h": 左栏标题, "items": ["条1","条2",...] }` |
| `right` | obj | ✓ | 同上，右栏 |
| `org` | str | | 页脚 |

### image（图文页）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `image` | str | | 图片路径（绝对，或相对 `base_dir`；`base_dir` 默认 = 模板所在目录） |
| `caption` | str | | 图注 |
| `org` | str | | 页脚 |

> 调用 `generate_from_vi(tpl, content, out, base_dir=...)` 可自定义图片基准目录。

### end（结尾）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | | 结尾语，缺省"谢谢观看" |
| `subtitle` | str | | 副标题 |
| `contact` | str | | 联系方式 |

完整样例见 `examples/demo_content.json`。

---

## 2. VI 提取（extract_vi）

```python
vi = extract_vi("客户模板.pptx")
# -> {
#   "brand","accent","dark","light","ink","muted":  hex 配色,
#   "font_title","font_body":  中文字体路径(如 msyh.ttc),
#   "font_latin_title","font_latin_body":  拉丁字体名,
#   "logo_img":  PIL.Image (客户 Logo)
# }
```

- 想锁死品牌色：`generate_from_vi(..., vi_override={"brand":"#1F4E79","accent":"#2E9CCA","dark":"#10263D"})`。
- 自动从系统找中文字体（Windows 默认微软雅黑）；Linux/mac 请保证装有中文字体，否则回退可能缺字。

---

## 3. 路线一 · deck_spec 结构（generate）

```python
from pptx_gen import analyze, generate

spec = {
  "slides": [
    # 模式 A：复用母版占位符（保留母版背景/Logo，文字可编辑）
    {"mode":"placeholder","layout_index":1,
     "fields":{"0":"标题","1":["要点1","要点2","要点3"]}},
    # 模式 B：整页拷贝后 run 级文本替换（像素级复刻含图片）
    {"mode":"copy","template_slide_index":0,
     "replacements":{"原文片段A":"新文本A","原文片段B":"新文本B"}},
  ],
  "keep_template_slides": False   # 默认删原模板页，只留生成页
}
generate("客户模板.pptx", spec, "out.pptx")
```

- `analyze("客户模板.pptx")` 返回版式/占位符清单，先跑它决定 `layout_index` 与占位符 key。
- 模式 B 的 `replacements` key 命中顺序：**形状名 → 整段原文 → 原文子串**，逐 run 替换，保留字体/项目符号。
- 两模式可在同一 `slides` 混用。

---

## 4. 与你的 LLM / 智能体框架衔接

**路线二（推荐）的闭环：**
1. `vi = extract_vi(tpl)` → 把 VI 摘要（配色 hex、字体名、有无 Logo）喂给 LLM，让它"按这套 VI 创作"。
2. 让 LLM 输出 `content.json`（严格按第 1 节 schema）。提示词要点：
   - 先给每页定 `role`，再填字段；内容要"有信息量"。
   - `bullets` 的 `items` 写成 `{"h":小标题, "d":一句描述}`；`stats` 写成 `{"num","label","desc"}`。
   - 每页写一句 `lead` 统领，避免空泛大标题配两三行字。
   - 章节用 `section`、首尾用 `cover`/`end`、对比用 `twocol`、关键指标用 `stats`、配图用 `image`。
3. `generate_from_vi(tpl, content, out, vi_override=...)` 出片。

**路线一的闭环：** `analyze(tpl)` → JSON → LLM 产出 `deck_spec` → `generate(tpl, spec, out)`。

---

## 5. 扩展方法

- **新增页面版式**：在 `pptx_gen/native.py` 写一个 `render_xxx(slide, page, i, n, pal, img_path)`（用 `rect/oval/text/shape_text/header/footer` 原语，见源码），再登记 `NATIVE_RENDERERS["xxx"] = render_xxx`。内容 schema 加对应字段即可。
- **接图表**：在调用前用 matplotlib/你自己的绘图生成 PNG，页用 `{"role":"image","image":"图.png"}` 引用。示例见 `examples/run_demo2.py` 的 `make_chart`。
- **QA 预览**：`preview_pngs(tpl, content, out_dir)` 用 Pillow 引擎出 1920×1080 预览图，用于肉眼检查版式；它**不产出 deck**，deck 永远走原生可编辑路径。
- **路线一换图**：`pptx_gen/slide_copy.py` 已可靠深拷贝含图页面；若要"按内容换图"，在拷贝后替换 Picture 形状的 `image.blob` 并改写 `r:embed`（python-pptx 1.0.x 用 `get_or_add` 重建关系）。

---

## 6. 依赖与运行

```bash
pip install -r requirements.txt   # python-pptx>=1.0.0, Pillow>=10.0.0
```

- **所有产出均为原生可编辑 PPT**：deck 由 python-pptx 构建（文本框/圆角矩形/椭圆/图片），文字在 PowerPoint/WPS 里可直接改；Pillow 只用于 `preview_pngs` QA 预览。
- 原生渲染要点：中文需同时写 latin/ea/cs 三个 typeface；透明度靠往 `srgbClr` 插 `<a:alpha>`；渐变角度必须直接钉 `a:lin/@ang`（python-pptx 的 `gradient_angle` setter 会写出 270°）；PowerPoint 行高≈1.2em，`line_spacing` 传 `lh/1.2` 对齐设计稿。
- 示例：`python examples/run_demo2.py`（路线二）、`python examples/run_demo.py`（路线一）。

---

## 7. 修改已生成的 PPT（edit_deck）

`edit_deck(pptx_path, edit_spec, out_path=None)` 对已生成的 deck 做手术式修改：默认原地写入（同级留 `.bak`），只动 op 涉及的内容。适合"deck 已经手工改过"或"只动一两页"的场景；整deck重排请回到 content.json 重生成。

### edit_spec 结构

```json
{
  "palette": {"gold": "#FFB545", "top": "#14395F", "bottom": "#0A1E33"},
  "ops": [ {...}, {...} ]
}
```

- `palette` 可选：覆盖 add_slide/replace_slide 新建页的配色（缺省从 deck 现有背景渐变 + 强调色嗅探自动推导）。
- `ops` 按顺序执行；单个 op 失败不中断，记入返回的 `warnings`。

### op 一览（slide 均为 1 基页码）

| op | 字段 | 行为 |
|---|---|---|
| `replace_text` | `find`, `replace`, `scope`(页码 \| `"deck"`) | 在范围内所有含 `find` 的文本框里替换（保留 run 格式；跨 run 合并进首个 run） |
| `set_text` | `slide`, `match`(子串) 或 `shape`(序号), `text` | 整框替换，`\n` 分段，保留首个 run 格式 |
| `add_slide` | `role`, `page`{角色字段}, `after`(可选) | 原生渲染加页；`after: 5` 表示插在第 5 页后 |
| `replace_slide` | `slide`, `role`, `page` | 整页换内容 |
| `delete_slide` | `slide` | 删页 |
| `move_slide` | `slide`, `to` | 移到新位置 |
| `recolor` | `accent`/`brand`/`dark` 或 `map` | 全局换色（含文本与渐变停止点） |

`page` 的字段与 content.json 对应 role 完全一致（如 bullets: `title`/`lead`/`items[{h,d}]`；stats: `stats[{num,label,desc}]`；image: `image`(相对 deck 目录或绝对路径)/`caption`）。

### 示例

```json
{
  "ops": [
    {"op": "replace_text", "scope": 6, "find": "1,897", "replace": "1,999"},
    {"op": "set_text", "slide": 1, "match": "成交额分析", "text": "成交额复盘"},
    {"op": "add_slide", "after": 9, "role": "bullets",
     "page": {"title": "加拿大与韩国简析", "lead": "其余两国走势一览。",
              "items": [{"h": "加拿大整体平稳", "d": "5 月微增 1.0%，6 月回落 11.1%。"},
                        {"h": "韩国逐月走低", "d": "6 月较 4 月累计下滑约 19.5%。"}]}},
    {"op": "replace_slide", "slide": 11, "role": "twocol",
     "page": {"title": "结论与行动", "lead": "一增一险，双线并进。",
              "left": {"h": "加码 · 俄罗斯", "items": ["跟踪 7 月放量持续性", "评估渠道库存与追加资源"]},
              "right": {"h": "排查 · 德国", "items": ["定位 5 月回落原因", "防范风险蔓延至 7 月"]}}},
    {"op": "move_slide", "slide": 12, "to": 2},
    {"op": "recolor", "accent": "#E4572E"}
  ]
}
```

### 返回与校验

```python
res = edit_deck("deck.pptx", spec)
# {'out': ..., 'slides': 12, 'applied': [...], 'warnings': [...]}
```

改完用 `preview_pngs(tpl, content, out_dir)`（需要 content.json）或直接 python-pptx 结构 dump 验证；页码在结构变动后已自动刷新。
