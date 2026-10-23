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

### table（数据表格）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `head` | list[str] | ✓ | 表头（建议 ≤6 列） |
| `rows` | list[list[str]] | ✓ | 数据行，每行列数 = `len(head)` |
| `caption` | str | | 表下注释（如数据来源） |

原生 PPT 表格（文字可编辑）：表头用品牌色、 body 斑马纹；**纯数字列自动右对齐**，其余居中/首列左对齐；列宽按各列最长内容比例分配；单元格文字过长自动缩字号（最小约 7pt，即 14px）并留在格内。

### image_text（图文组合，单图）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `image` | str | | 图片路径（同上） |
| `side` | str | | `"left"`（默认）或 `"right"`，图在哪一侧 |
| `h` | str | | 文本区小标题 |
| `points` | list | | 文本要点，每项 `{"h":..,"d":..}` 或纯字符串 |
| `chips` | list[str] | | 底部小标签（按空间自适应裁剪） |

### image_grid（图文组合，多图 2~4 张）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `images` | list | ✓ | 2~4 项，每项 `{"src": 路径, "caption": 题注}`（也接受纯路径字符串） |
| `caption` | str | | 整页底部注释 |

4 张 = 2×2，3 张 = 1×3，2 张 = 1×2；每张图独立题注。图片缺失时该格降级为占位面板（并在 stderr 告警），不影响其他格。

### timeline（横向时间轴）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `items` | list | ✓ | 3~5 项，每项 `{"t": 时间, "h": 标题, "d": 描述(可选)}` |

### process（流程步骤）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | ✓ | 标题 |
| `lead` | str | | 导语 |
| `steps` | list | ✓ | 3~5 项，每项 `{"h": 步骤名, "d": 描述(可选)}`，步骤间有箭头连接 |

### quote（金句/大引言）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `quote` | str | ✓ | 引言正文 |
| `author` | str | | 署名 |
| `title` | str | | 署名下的职务/来源 |

### toc（目录）
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | | 缺省"目录" |
| `lead` | str | | 导语 |
| `items` | list | ✓ | 每项 `{"h": 章节名, "d": 一句说明(可选)}` 或纯字符串；超过 4 项自动分两栏 |

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

## 5. 溢出保护（防"字体太大撑出页面"）

程序化生成 PPT 最常见的翻车点：**字号写死 + 文本框固定 + 不开启自适应**，内容一长（如 KPI 大数字 `44.63万`）就溢出卡片甚至页面。本技能在两条路线都内置了防溢出：

- **native 路线**（native.py）：三层保护，缺一不可——
  1. **真实字体测量**：`measure()` 用 PIL 加载真实字体文件（msyh/msyhbd/simhei/Noto CJK…）逐字测宽，**区分加粗**（加粗雅黑数字比常规宽 ~8%，实测 `44.63万` 在 94px 加粗下 = 353px，而 em 估算只给 329px）。⚠️ 曾经 `_pil_font()` 漏导入 `ImageFont`，`NameError` 被 `except Exception` 静默吞掉导致**每次测量都静默退回 em 估算、验证假通过**——现在 except 已收窄为 `(OSError, ValueError)`，且验证脚本必须对比 `measure()` 与直接 `ImageFont.truetype().getlength()` 证明字体活性。
  2. **自动缩字号**：`text(..., fit=True, max_lines=1)` 按 `w×0.92`（单行）/`w×0.97`（多行）预算缩小字号——单行收缩到放得下为止（实测 `44.63万` 104→82px、`1,234.56万`→58px），长段落收缩到换行后高度不超框。
  3. **硬禁换行**：`max_lines=1` 的文本框直接设 `word_wrap=False`——**无论查看器替换成多宽的字体都永远不会换行**（配合自适应字号，溢出量也压在卡片内边距里）。这是针对"查看器字体比测量字体宽"的终极兜底。
- **模板填充路线**（content_filler.py）：`_safe_text()` 对整框替换的文本开启 `word_wrap=True` + `auto_size=TEXT_TO_FIT_SHAPE`（等价 PowerPoint 的"溢出时缩小文字"）；`editor.py` 的 `set_text` 操作同样生效。

新写版式时，凡是内容长度不可控的文本，一律加 `fit=True`（单行元素再加 `max_lines=1`）。

验证方式（缺一不可）：① 对比 `measure()` 与直接 `ImageFont` 测量值，证明真实字体生效；② 重开生成的 pptx，单行元素检查 `word_wrap is False` 且 `est_w(text, size_px) <= 文本框宽px×0.93`。

---

## 6. 扩展方法

版式系统是**注册表模式**，加新版式 = 写一个渲染函数 + 登记一行，主链路/编辑器/校验全部自动生效：

```python
# pptx_gen/native.py
def render_swot(slide, page, i, n, pal, img_path):
    bg(slide, pal)
    top = header(slide, pal, page["title"], page.get("lead"))
    # ... 用 rect/oval/text/shape_text/header/footer 原语布局四象限 ...
    # 内容长度不可控的文本一律 fit=True（单行元素加 max_lines=1）

NATIVE_RENDERERS["swot"] = render_swot          # native 生效
DARK_RENDERERS["swot"] = render_swot_preview    # dark_theme.py 里加同名预览（可选，默认主题）
```

- **编辑器同步可用**：`edit_deck` 的 `add_slide`/`replace_slide` 直接读 `NATIVE_RENDERERS`，新角色立刻能加页/换页。
- **未知 role 会明确报错**（列出全部可用角色），不会静默跳过。
- **预览主题架构**：`render.py` 末尾安装 `RENDERERS = DARK_RENDERERS`——**深色 14 角色为默认预览主题**，几何参数与 `native.py` 逐参数对齐（docstring 声明），保证 QA 预览与 deck 视觉一致；浅色 7 角色退为 `LIGHT_RENDERERS` legacy，切回方式：`import pptx_gen.render as R; R.RENDERERS = R.LIGHT_RENDERERS`。新增预览角色注册到 `dark_theme.py` 的 `DARK_RENDERERS`。
- **预览可选**：`render.py` 未登记的 role 会自动降级为"标题 + [preview N/A]"页，不阻塞 QA；补上预览函数即可肉眼校验。
- **接图表**：调用前用 matplotlib/自己的绘图生成 PNG，页用 `{"role":"image","image":"图.png"}` 或 `image_text`/`image_grid` 引用。示例见 `examples/run_demo2.py` 的 `make_chart/make_line_chart/make_panels`。
- **多图解析**：`image_grid` 读 `page["images"]`（相对 `base_dir` 解析，`base_dir` 默认 = 模板所在目录，可用参数覆盖）；缺失文件保留占位面板并在 stderr 告警。
- **QA 预览**：`preview_pngs(tpl, content, out_dir)` 用 Pillow 引擎出 1920×1080 预览图；它**不产出 deck**，deck 永远走原生可编辑路径。
- **路线一换图**：`pptx_gen/slide_copy.py` 已可靠深拷贝含图页面；若要"按内容换图"，在拷贝后替换 Picture 形状的 `image.blob` 并改写 `r:embed`（python-pptx 1.0.x 用 `get_or_add` 重建关系）。

---

## 7. 依赖与运行

```bash
pip install -r requirements.txt   # python-pptx>=1.0.0, Pillow>=10.0.0
```

- **所有产出均为原生可编辑 PPT**：deck 由 python-pptx 构建（文本框/圆角矩形/椭圆/图片），文字在 PowerPoint/WPS 里可直接改；Pillow 只用于 `preview_pngs` QA 预览。
- 原生渲染要点：中文需同时写 latin/ea/cs 三个 typeface；透明度靠往 `srgbClr` 插 `<a:alpha>`；渐变角度必须直接钉 `a:lin/@ang`（python-pptx 的 `gradient_angle` setter 会写出 270°）；PowerPoint 行高≈1.2em，`line_spacing` 传 `lh/1.2` 对齐设计稿。
- 示例：`python examples/run_demo2.py`（路线二）、`python examples/run_demo.py`（路线一）。

---

## 8. 修改已生成的 PPT（edit_deck）

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
