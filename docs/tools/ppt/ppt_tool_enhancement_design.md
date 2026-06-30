# PPT 工具增强设计文档

> 日期：2026-06-30  
> 状态：待人工评审，评审通过后开发  
> 关联现状：`src/tools/ppt/` 已有 `ppt_process`、planner、generator、template_analyzer 和基础布局渲染器  
> 关联文档：  
> - [PPT 工具设计文档](ppt_tool_design.md)  
> - [PPT 工具深度调研报告](ppt_tool_research.md)  
> - [HTML 转 PPTX 技术可行性调研](../../research/html-to-pptx-conversion-research.md)  
> - [文件生成类工具入参语义拆分设计](../tool-input-contract-redesign.md)

## 1. 背景与目标

当前 PPT 工具已经具备基础生成能力：

- `ppt_process` 是 Agent 唯一入口，支持 `auto` 和 `template` 两类模式。
- `PPTPlanner` 用 LLM 将主题或 Markdown 内容规划为统一 JSON 大纲。
- `PPTGenerator` 当前使用 `python-pptx` 输出原生 `.pptx`。
- `TemplateAnalyzer` 能读取 `.pptx` 模板的 slide layout 和 placeholder，并做基础匹配。
- 已有封面、目录、章节、内容、图表、总结等布局渲染器。

但现有能力与 Codex Presentations 插件仍有明显差距，尤其在以下方面：

1. 入参仍以 `context` 混合承载“用户目的 + 正文 + 隐含参数”，标题和正文边界不稳定。
2. 模板模式只是“删除原幻灯片 + 使用 layout + 填 placeholder”，没有完整模板审计、frame map、编辑目标约束和偏差记录。
3. 从零生成依赖少量手写布局，缺少可复用布局库、布局评分和设计 QA 闭环。
4. HTML PPT 当前核心交付是单文件 HTML，缺少高保真 PPTX 导出路径。
5. 导出后没有渲染预览、重叠/越界/文本截断检测，工具只保证文件生成，不保证可交付质量。

增强目标：

- 补齐 `ppt_process` 结构化入参契约，降低路由和标题污染。
- 直接引入 Node.js + PptxGenJS 作为新增 PPTX 主渲染器；Python 继续承担 Agent 工具入口、LLM 编排、文件管理、日志和 QA 调度。
- 建立“可编辑原生 PPTX”和“高保真截图 PPTX”双导出能力。
- 学习 Codex Presentations 插件的模板继承、布局库、渲染 QA 和对象检查机制。
- 删除 Google Slides 范围；该能力对本项目 Agent 当前无实际价值。
- 将 HTML PPT 转换升级为“高保真版 + 尽量可编辑版”两条可明确选择和降级的路径。
- 输出设计文档供人工审核，通过后再开发。

## 2. Codex Presentations 插件能力差距

调研对象：本地 `@presentations` 插件，核心说明来自 `Presentations` skill 与 `@oai/artifact-tool` 文档。

### 2.1 能力对比

| 能力 | 当前 PPT 工具 | Codex Presentations 插件 | 差距与增强方向 |
|------|---------------|--------------------------|----------------|
| 生成引擎 | `python-pptx` 手写坐标和布局 | `@oai/artifact-tool`，支持 compose rows/columns/grid/layers 与原生对象 | 直接新增 Node.js + PptxGenJS 主渲染器，`python-pptx` 保留为读取、模板初步分析和旧流程兼容 |
| 坐标体系 | inch + python-pptx EMU | CSS px，默认 1280×720，HTML/PPTX 更易统一 | `SlideDeckSpec` 统一使用 1280×720 px；Node 渲染器内部转换为 PptxGenJS 使用的 inch |
| 布局库 | 少量固定布局 | Codex Grid 布局库，按用途/密度/槽位筛选 | 新增内置 layout registry，覆盖 cover/toc/section/content/summary/chart/table/image |
| 模板跟随 | 基础读取 layout 和 placeholder | inspect 模板、frame map、starter deck、deviation log、只编辑继承对象 | 建立 template audit、frame map、edit targets 和偏差日志 |
| 导入编辑 | 只能基于模板新增页并填 placeholder | importPptx 后 inspect/resolve/edit/re-inspect | 短期仍用 `python-pptx` 做模板读取/分析；生成新页和 HTML 转换走 PptxGenJS |
| QA | 无系统渲染检查 | 每页 PNG、layout JSON、montage、重叠/裁剪/换行检查 | 增加 LibreOffice/Playwright/Poppler 可用路径下的渲染与结构化验证 |
| 图片策略 | 可插入图片，缺少资产治理 | 图片 byte 嵌入、fit/crop/alt、素材比例先规划 | 增加图片资产规格、裁剪策略和缺失检测 |
| 图表/表格 | 基础图表和表格 | 原生 charts/tables，导出后检查对象树 | 补 chart/table schema 与测试样例 |

### 2.2 可直接借鉴的工程机制

1. **先选输出路径**：新建 PPT、编辑现有 PPT、基于模板、HTML 转换要在入口确定，不让 planner 自由猜。
2. **模板跟随模式**：模板是视觉来源时，不再混入内置主题；输出页必须映射到源模板 slide/layout。
3. **frame map**：每页明确继承哪个模板页或 layout、允许编辑哪些对象、允许新增哪些对象。
4. **QA 产物**：导出后生成每页预览图、layout JSON、总览图，并记录检查结果。
5. **不可编辑区域显式化**：WebGL、复杂滤镜、毛玻璃、复杂 SVG 可以作为图片层，但必须在结果元数据中说明。
6. **文件交付只返回最终 PPTX**：预览图、layout JSON、审计日志用于内部 QA，不默认暴露给用户。

### 2.3 PptxGenJS 引入方式

Python Agent 主体可以直接引入 JS/PptxGenJS，但边界必须清晰：

```text
Python ppt_process
  -> InputNormalizer / Router / Planner
  -> SlideDeckSpec JSON
  -> 调用 Node.js renderer CLI
  -> PptxGenJS 生成 .pptx
  -> Python QA Validator
  -> 返回 file_path
```

落地要求：

- Node 渲染器作为工具内部子进程调用，不改变 Agent 主循环语言栈。
- Node 脚本只接收 JSON 文件路径、输出目录和渲染参数，不直接访问会话、租户、数据库或 LLM。
- Python 侧负责超时、错误捕获、日志、临时目录清理、文件命名和下载交付。
- Docker/部署环境必须安装 Node.js，并在项目内锁定 `package.json` / lockfile。
- PptxGenJS 是新增 PPTX 主渲染器；`python-pptx` 保留用于读 PPTX、模板分析、简单兼容和必要的后处理。

## 3. 统一入参契约

将 `tool-input-contract-redesign.md` 中 PPT 相关修改合并到本设计。

### 3.1 新增输入字段

`PptProcessInput` 增加：

```python
instruction: Optional[str]  # 用户目的，如“生成PPT”“基于模板生成”“将HTML转PPTX”
content: Optional[str]      # 待处理正文，如 Markdown 大纲、HTML、SlideDeckSpec JSON
content_type: Optional[str] # markdown/html/slide_deck_spec/text/auto
output_name: Optional[str]  # 业务文件名，可选，优先级最高
export_mode: Optional[str]  # editable/high_fidelity/both，HTML 转 PPTX 时使用
context: Optional[str]      # 兼容旧调用
file_paths: Optional[List[str]]
```

兼容规则：

- 新字段优先，`context` 仅作为旧调用兼容入口。
- 旧 `context` 自动拆分为 `instruction/content`，高置信度识别 Markdown/HTML 正文边界。
- 执行阶段只消费归一化后的 `content`，不直接使用混合 `context`。
- 无法可靠拆分时保留旧逻辑，但返回 warning 便于日志观察。

### 3.2 模式路由

| 路由 | 条件 | 处理 |
|------|------|------|
| `template` | `file_paths` 含 `.pptx` 且 instruction 提到基于模板/套模板 | 模板跟随模式 |
| `html_to_pptx` | `content_type=html` 或 content 明显 HTML，且 instruction 提到转 PPTX | HTML 转 PPTX |
| `spec_to_pptx` | `content_type=slide_deck_spec` 或 JSON 符合 schema | 语义 AST 原生生成 |
| `outline_to_pptx` | Markdown 大纲或长正文 | planner 生成大纲后生成 |
| `topic_to_pptx` | 简短主题 | planner 从主题生成 |

### 3.3 标题和文件名优先级

标题提取优先级：

1. `output_name` 去扩展名。
2. `content` 中 Markdown H1/H2。
3. `SlideDeckSpec.title`。
4. planner 返回的 `title`。
5. “演示文稿”。

禁止继续使用混合 `context[:30]` 作为默认标题，避免出现“帮我生成一份...”这类文件名。

## 4. 目标架构

增强后的 PPT 工具采用 Python 编排 + Node 渲染的双运行时架构：

```text
PptProcessInput
  -> InputNormalizer
  -> Router
  -> Planner / HTML Extractor / Template Analyzer / Spec Loader
  -> SlideDeckSpec
  -> Node PptxGenJS Renderer
  -> .pptx
  -> QA Validator
  -> file_path + qa_summary + warnings
```

核心设计调整：

- `SlideDeckSpec` 是本项目自研的轻量 JSON 协议，不是 Codex 或 PptxGenJS 的现成标准。
- `SlideDeckSpec` 借鉴 Codex 的对象模型、px 坐标、可检查对象树和 QA 思路，但字段设计要优先贴合 PptxGenJS。
- Markdown/主题/模板/HTML 都先转为可验证的语义规格，再渲染。
- HTML 不是长期唯一源格式，而是预览产物或兼容输入。
- PPTX 导出优先原生对象，必要时局部或整页截图降级。

## 5. SlideDeckSpec 语义模型

`SlideDeckSpec` 是本项目自研的中间 JSON 协议，目标不是复刻 Codex 私有实现，而是吸收成熟方案的共同设计：

- 借鉴 Codex：px 坐标、稳定 `id/name`、对象树、layout JSON、导出后 QA。
- 借鉴 PptxGenJS：节点字段尽量贴近 `slide.addText`、`slide.addShape`、`slide.addImage`、`slide.addTable`、`slide.addChart` 的参数。
- 借鉴 DOM 转换方案：HTML 抽取结果使用浏览器计算后的 `position` 和 `computedStyle`，不自研 CSS 布局引擎。

建议新增 Python schema `src/tools/ppt/spec.py`，同时新增 TypeScript 类型 `src/tools/ppt/renderer-node/src/spec.ts`：

```python
class SlideDeckSpec(BaseModel):
    version: str = "1.0"
    title: str
    slide_size: SlideSize = SlideSize(width=1280, height=720)
    layout: str = "LAYOUT_WIDE"
    theme: ThemeTokens
    slides: list[SlideSpec]

class SlideSpec(BaseModel):
    id: str
    name: Optional[str] = None
    layout: str  # cover/content/chart/table/summary/custom
    title: Optional[str] = None
    background: Optional[BackgroundSpec] = None
    nodes: list[SlideNode]
    notes: Optional[str] = None

SlideNode = (
    TextNode | ShapeNode | ImageNode | TableNode | ChartNode |
    GroupNode | RasterLayerNode
)
```

### 5.1 与 PptxGenJS 的字段对齐

PptxGenJS 原生 API 使用 inch 坐标；`SlideDeckSpec` 对外统一 px，Node renderer 内部做 `px / 96` 转换。

| Spec 字段 | PptxGenJS 映射 |
|-----------|----------------|
| `slide_size.width/height` | `ppt.layout = "LAYOUT_WIDE"` 或自定义 layout |
| `node.position.left/top/width/height` | `x/y/w/h = px / 96` |
| `TextNode.text` | `slide.addText(text, options)` |
| `TextNode.runs` | `slide.addText([{ text, options }], options)` |
| `ShapeNode.shape` | `slide.addShape(pptx.ShapeType.xxx, options)` |
| `ImageNode.path/data/fit/crop` | `slide.addImage(...)` |
| `TableNode.rows` | `slide.addTable(rows, options)` |
| `ChartNode.chart_type/categories/series` | `slide.addChart(type, data, options)` |
| `RasterLayerNode.image_path` | `slide.addImage(... full/partial frame ...)` |

节点示例：

```json
{
  "type": "text",
  "id": "cover-title",
  "name": "cover-title",
  "position": {"left": 80, "top": 180, "width": 860, "height": 110},
  "text": "季度经营分析",
  "options": {
    "fontFace": "Microsoft YaHei",
    "fontSize": 48,
    "bold": true,
    "color": "0F172A",
    "margin": 0,
    "breakLine": false,
    "fit": "shrink"
  }
}
```

说明：

- `options` 尽量采用 PptxGenJS 同名字段，减少 renderer 转译成本。
- `position`、`id`、`type`、`name` 是本项目统一字段。
- `metadata` 用于记录来源、可编辑性、HTML selector、模板 placeholder 等，不传给 PptxGenJS。

### 5.2 节点原则

- 文本、形状、表格、图表默认转为 PPT 原生对象。
- WebGL、复杂滤镜、无法稳定映射的视觉效果使用 `RasterLayerNode`。
- 每个节点带 `bounds`、`z_index`、`editable`、`source`，便于统计可编辑率和排查。
- 不让 LLM 直接生成 PptxGenJS 代码；LLM 只生成或修正 `SlideDeckSpec`，Node renderer 负责确定性渲染。
- Spec 版本化，后续新增字段必须向后兼容。

## 6. HTML 转 PPTX 设计

用户明确要求：PPT 工具需要将生成的 HTML 页面 PPT 转成：

1. 高保真的 PPTX 文件。
2. 尽量保真的可编辑 PPTX 文件。

因此 HTML 导出必须支持双路径。

### 6.1 高保真版：整页截图 PPTX

适用场景：

- 用户优先要求视觉一致。
- HTML 包含 WebGL/canvas、复杂动画、backdrop-filter、复杂 SVG/滤镜。
- 可编辑导出 QA 不通过。

流程：

```text
HTML -> Playwright 打开 -> 固定 viewport 1920x1080 或 2560x1440
     -> 等待字体/图片/WebGL/动画最终态
     -> 每个 section.slide 截图 PNG
     -> PptxGenJS 每页铺满图片
     -> 渲染校验页数和非空
```

特点：

- 视觉还原优先，预期 90-95 分。
- 文字不可编辑，文件较大。
- 作为所有 HTML 转换任务的可靠降级结果。

### 6.2 尽量可编辑版：DOM 抽取 + 原生重建 + 局部截图

适用场景：

- 用户需要在 PowerPoint/WPS 中继续编辑文字、图表、卡片。
- HTML 来自受控模板，如 `guizang-ppt-skill` 的 `section.slide` 结构。

流程：

```text
HTML -> Playwright 打开
     -> 注入 extractor
     -> 读取 DOM rect / computedStyle / text / z-index
     -> 生成 SlideDeckSpec
     -> canvas/backdrop-filter/复杂区域截图为 RasterLayerNode
     -> PPTX Renderer 生成原生对象
     -> QA Validator 检查可编辑率和视觉偏差
```

转换策略：

| HTML/CSS | PPTX 策略 |
|----------|-----------|
| `section.slide` | 一页 `SlideSpec` |
| `h1/h2/p/li` | 原生文本框 |
| 卡片、分割线、标签 | 原生 shape |
| 表格 | 原生 table，复杂表格可降级图片 |
| 图表 | 有结构化数据时原生 chart，否则局部截图 |
| `img` | image，保留裁剪/适配 |
| SVG icon | 优先作为矢量或图片 |
| CSS Grid/Flex | 使用浏览器计算后的绝对坐标，不手写 CSS 布局引擎 |
| WebGL/canvas | 背景或局部图片层 |
| `backdrop-filter/mix-blend-mode` | 局部截图层 |
| 动画 | 捕获最终态，不导出动画 |

### 6.3 `export_mode`

| 值 | 输出 |
|----|------|
| `high_fidelity` | 只输出整页截图 PPTX |
| `editable` | 输出尽量可编辑 PPTX；失败时返回错误和建议 |
| `both` | 同时输出高保真版和可编辑版；默认推荐用于人工审核 |

工具返回示例：

```json
{
  "success": true,
  "file_path": ".../deck_editable.pptx",
  "alternate_file_path": ".../deck_high_fidelity.pptx",
  "qa_summary": {
    "slide_count": 8,
    "editable_ratio": 0.82,
    "raster_layer_count": 9,
    "warnings": ["第3页 WebGL 背景为图片层"]
  }
}
```

## 7. 模板跟随增强

现状模板模式只分析 slide layouts，增强为 Codex-like 模板跟随流程。

### 7.1 新增内部产物

| 文件 | 用途 |
|------|------|
| `template-audit.json` | 模板尺寸、母版、layout、placeholder、字体、颜色、已有幻灯片摘要 |
| `template-frame-map.json` | 输出页到模板页/layout 的映射与 editTargets |
| `deviation-log.json` | 不可继承、需要新增、发生降级的记录 |
| `qa-report.json` | 渲染检查、对象检查、文本截断和重叠检查结果 |

这些产物默认保存在临时目录，不作为用户交付文件。

### 7.2 匹配策略

优先级：

1. 用户明确指定模板页或 layout。
2. slide type/layout 与模板 layout 类型匹配。
3. placeholder 能容纳内容密度。
4. 字体、颜色和版式复杂度适合当前内容。
5. 无匹配时选择最接近 layout 并记录 deviation。

### 7.3 编辑边界

- 模板作为视觉来源时，不混用内置主题。
- 尽量编辑已有 placeholder 或继承对象。
- 新增对象必须在 frame map 中声明原因。
- 不删除母版、页脚、页码、品牌 chrome，除非用户明确要求。

## 8. QA 与验收

新增 `PPTQualityValidator`：

```text
PPTX -> 渲染 PNG/或 LibreOffice PDF -> 每页检查
     -> 结构检查
     -> 质量报告
```

检查项：

- 文件存在、页数正确、非空页。
- 文本框/图片/表格/图表对象数量符合预期。
- 文本不明显越界或被裁剪。
- 形状没有非预期重叠。
- 图片路径有效，图片不为 0 字节。
- 模板模式下页脚/页码/品牌元素未丢失。
- HTML 转换时统计 `editable_ratio` 和 `raster_layer_count`。

验收标准：

- `outline_to_pptx` 标题不含指令前缀。
- `template` 模式生成前必须有 template audit 和 frame map。
- `html_to_pptx high_fidelity` 至少达到整页截图可交付，不出现空白页。
- `html_to_pptx editable` 文本和基础形状尽量原生化，复杂背景允许图片层。
- 所有生成模式返回 `file_path`，仍由 Agent 后续调用 `cp` 交付。

## 9. 开发计划

### Phase 0：现状补齐和回归基线

- 为当前 `ppt_process` 增加回归测试基线。
- 覆盖主题生成、Markdown 大纲生成、模板生成。
- 记录当前输出质量和已知缺陷。

### Phase 1：PPT 入参语义拆分

- `PptProcessInput` 增加 `instruction/content/content_type/output_name/export_mode`。
- 新增 input normalizer。
- 标题提取改为结构化优先。
- 工具描述更新为推荐新字段，保留 `context` 兼容。

测试：

- “帮我生成 PPT” + Markdown 大纲，标题来自 H1/H2。
- `output_name` 显式优先。
- 模板 PPT + `content` 大纲。
- 纯主题生成行为不变。

### Phase 2：SlideDeckSpec 和逻辑坐标层

- 新增 `SlideDeckSpec` schema。
- 新增 Node renderer 工程：`src/tools/ppt/renderer-node/`。
- 引入 `pptxgenjs`，建立 CLI：输入 spec JSON，输出 PPTX。
- 建立 Markdown/Planner JSON 到 spec 的转换。
- 建立 1280×720 px 到 PptxGenJS inch 的转换工具。
- 先覆盖 text/shape/image/table/chart/rasterLayer 六类节点。
- 将新增生成路径默认切到 PptxGenJS renderer；旧 `python-pptx` 生成器保留回滚开关。

### Phase 3：模板跟随增强

- 实现 template audit。
- 实现 frame map。
- 改造 `TemplateAnalyzer.generate_from_template`，按 editTargets 填充。
- 输出 deviation log 和 qa report。

### Phase 4：HTML 高保真 PPTX 导出

状态：✅ 已完成开发（2026-06-30）

- Playwright 渲染 HTML。
- 按 `section.slide` 或横向 deck 状态逐页截图。
- 生成整页图片版 PPTX。
- 加入非空页和页数校验。

### Phase 5：HTML 尽量可编辑 PPTX 导出

- Playwright DOM extractor。
- DOM/computedStyle 到 `SlideDeckSpec`。
- 文本、shape、image、table 基础映射。
- canvas/复杂滤镜局部截图。
- 输出可编辑率和降级说明。

### Phase 6：QA 自动化

- 增加 PPTX 渲染预览能力。
- 增加对象结构检查。
- 增加重叠、越界、文本截断启发式检测。
- 单测 + 小型视觉样本集。

## 10. 非目标

- 不承诺把任意网页 100% 转为原生可编辑 PPTX。
- 不导出动画和转场。
- 不在本阶段接入第三方商业 PPT API。
- 不开发 Google Slides 导入或原生 Google Slides 输出。
- 不改变文件交付规则：工具返回 `file_path`，Agent 仍需调用 `cp` 注册下载。
- 不自动替换现有 `guizang-ppt-skill` 的 HTML 主流程，先做导出增强。

## 11. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| HTML 可编辑转换还原度不稳定 | 用户看到错位 PPTX | 默认提供高保真截图版；可编辑版附 QA warning |
| 模板分析误判 layout | 内容放错位置 | frame map 可审计；复杂模板先返回需要人工确认 |
| Node/PptxGenJS 子进程失败 | PPT 生成失败 | Python 捕获 stderr/stdout，保留旧 `python-pptx` 回滚路径，设置超时和临时目录清理 |
| PptxGenJS 与 PowerPoint/WPS 渲染差异 | 字体、图表或阴影略有偏差 | 渲染 QA + 样例回归；字体和主题在 spec 中显式声明 |
| 渲染依赖环境差异 | Windows/Linux 输出不一致 | 明确依赖探测，缺失时降级结构检查 |
| 文件体积过大 | 高保真版下载慢 | 截图尺寸和 PNG/JPEG 策略可配置 |

## 12. 人工审核关注点

请重点审核：

1. 是否接受 Node.js + PptxGenJS 作为新增 PPTX 主渲染器。
2. HTML 转 PPTX 是否默认输出 `both`，还是默认只给高保真版。
3. 模板跟随是否要先支持“基于 layout 新建页”，还是直接支持“克隆源幻灯片后编辑对象”。
4. 是否接受 `SlideDeckSpec` 作为本项目自研中间层，并要求字段优先贴合 PptxGenJS。
5. QA 阈值：可编辑率、截图层数量、文本截断 warning 是否作为失败条件。
