# PPT 工具增强开发计划

> 创建日期：2026-06-30  
> 关联设计：[ppt_tool_enhancement_design.md](ppt_tool_enhancement_design.md)  
> 状态：🔧 部分完成（Phase 0-7 已完成，真实 HTML/模板/PPTX 与统一 QA 自动化测试通过）
> 范围：`ppt_process` 入参拆分、Node.js + PptxGenJS 主渲染器、`SlideDeckSpec`、HTML 转 PPTX、模板跟随增强、QA 验证

## 1. 目标

将现有 PPT 工具从“Python LLM 规划 + python-pptx 基础生成”升级为：

```text
Python ppt_process
  -> 结构化入参归一化
  -> 路由 / Planner / HTML Extractor / Template Analyzer
  -> SlideDeckSpec JSON
  -> Node.js + PptxGenJS 主渲染器
  -> PPTX QA
  -> file_path + qa_summary
```

核心交付：

1. `ppt_process` 支持 `instruction/content/content_type/output_name/export_mode`，保留 `context` 兼容。
2. 新增与 PptxGenJS 高度对齐的 `SlideDeckSpec`。
3. 新增 Node renderer CLI，使用 PptxGenJS 生成 `.pptx`。
4. 支持 HTML 页面 PPT 导出为高保真截图版 PPTX。
5. 支持 HTML 页面 PPT 导出为尽量可编辑的 PPTX。
6. 增强模板模式：template audit、frame map、deviation log、QA report。
7. 增加导出后 QA：页数、非空、对象数量、文本/图片/截图层统计。

## 2. 总体原则

1. Python 仍是 Agent 工具入口和主编排层。
2. Node.js 只做确定性 PPTX 渲染，不访问数据库、租户、会话或 LLM。
3. 新路径默认使用 PptxGenJS；旧 `python-pptx` 生成器保留回滚开关。
4. LLM 不直接生成 JavaScript 代码，只生成或修正 `SlideDeckSpec`。
5. HTML 转 PPTX 默认优先稳定交付：高保真截图版必须可靠，可编辑版允许带 warning。
6. Google Slides 不纳入范围。
7. 工具只返回 `file_path` 等元数据，最终交付仍由 Agent 调用 `cp`。

## 3. 阶段计划

### Phase 0：回归基线与依赖预检

状态：✅ 已完成（独立测试与 code review 通过，回归测试 11 passed）

目标：

- 固化当前 PPT 工具行为，避免后续迁移时破坏旧能力。
- 明确 Node.js / npm / Playwright / LibreOffice 等环境依赖的探测方式。

改动：

1. 增加当前 `ppt_process` 回归测试：
   - 纯主题生成 PPT。
   - Markdown 大纲生成 PPT。
   - `.pptx` 模板 + 内容生成 PPT。
   - 空输入错误。
2. 新增依赖探测 helper：
   - Node.js 是否可执行。
   - Node renderer 依赖是否安装。
   - Playwright 是否可用于 HTML 截图。
   - LibreOffice 或其它渲染验证工具是否可用。
3. 增加开关配置：
   - `PPT_RENDERER=pptxgenjs|python_pptx`
   - `PPT_ENABLE_HTML_EXPORT=true|false`
   - `PPT_QA_STRICT=true|false`

验收：

- 旧 `python-pptx` 路径测试通过。
- 缺少 Node.js 时工具能回退或返回明确错误，不出现异常堆栈泄漏给用户。

测试命令：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_tool.py -q
```

### Phase 1：PPT 入参语义拆分

状态：✅ 开发及 code review 完成（2026-06-30，相关单测 28 passed）

目标：

- 将 `ppt_process` 从单 `context` 入口升级为结构化入参。
- 避免标题、正文、大纲被用户指令污染。

改动：

1. `PptProcessInput` 增加：
   - `instruction`
   - `content`
   - `content_type`
   - `output_name`
   - `export_mode`
2. 新增 `PptInputNormalizer`：
   - 新字段优先。
   - 旧 `context` 自动拆分为 `instruction/content`。
   - Markdown H1/H2、HTML `<title>/<h1>`、JSON `title` 可用于标题提取。
3. 改造 `_detect_mode`：
   - `template`
   - `html_to_pptx`
   - `spec_to_pptx`
   - `outline_to_pptx`
   - `topic_to_pptx`
4. 标题优先级：
   - `output_name` 去扩展名。
   - Markdown H1/H2。
   - `SlideDeckSpec.title`。
   - planner 返回 title。
   - `演示文稿`。
5. 更新工具描述，推荐 Agent 传 `instruction + content`，保留 `context` 兼容说明。

验收：

- “帮我生成 PPT” + Markdown 大纲时，标题来自大纲，不含“帮我生成”。
- 显式 `output_name` 优先。
- 旧 `context` 调用不破坏。
- 无内容时返回明确错误。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_tool.py -q
```

### Phase 2：SlideDeckSpec 与 Node/PptxGenJS 渲染器

状态：✅ 已完成开发并通过独立测试与 code review（2026-06-30）

目标：

- 建立本项目自研的 PPT 中间协议。
- 让新增生成路径默认走 PptxGenJS。

改动：

1. 新增 Python schema：
   - `src/tools/ppt/spec.py`
   - `SlideDeckSpec`
   - `SlideSpec`
   - `TextNode`
   - `ShapeNode`
   - `ImageNode`
   - `TableNode`
   - `ChartNode`
   - `RasterLayerNode`
2. 新增 spec 转换器：
   - planner JSON -> `SlideDeckSpec`
   - Markdown 大纲 -> planner JSON -> `SlideDeckSpec`
   - 简短主题 -> planner JSON -> `SlideDeckSpec`
3. 新增 Node 工程：
   - `src/tools/ppt/renderer-node/package.json`
   - `src/tools/ppt/renderer-node/src/render.ts`
   - `src/tools/ppt/renderer-node/src/spec.ts`
   - `src/tools/ppt/renderer-node/src/units.ts`
   - `src/tools/ppt/renderer-node/src/nodes/*.ts`
4. Node CLI：

```bash
node dist/render.js --spec spec.json --out output.pptx --qa-json layout.json
```

5. 实现基础节点渲染：
   - text -> `slide.addText`
   - shape -> `slide.addShape`
   - image/rasterLayer -> `slide.addImage`
   - table -> `slide.addTable`
   - chart -> `slide.addChart`
6. Python 调用 Node renderer：
   - 写 spec 到临时目录。
   - 子进程调用 Node。
   - 捕获 stdout/stderr。
   - 超时取消。
   - 返回 PPTX 路径。

验收：

- 主题生成和 Markdown 大纲生成默认走 PptxGenJS。
- 输出 PPTX 可打开，含原生文本框、形状、表格或图表。
- Node 失败时返回清晰错误，并可通过配置回退旧 `python-pptx`。

测试：

```powershell
npm --prefix src/tools/ppt/renderer-node test
npm --prefix src/tools/ppt/renderer-node run build
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_tool.py -q
```

### Phase 3：布局库与 Planner 输出约束

状态：✅ 已完成（2026-06-30）

目标：

- 避免每页完全靠 LLM 自由排版。
- 建立可复用、可测试的 layout registry。

改动：

1. 新增 layout registry：
   - cover
   - toc
   - section
   - bullets
   - stat
   - comparison
   - timeline
   - chart
   - table
   - image
   - summary
2. 每个 layout 定义：
   - 适用场景。
   - 内容槽位。
   - 默认字号/间距/安全边距。
   - 最大文本密度。
   - 推荐图片比例。
3. Planner prompt 更新：
   - 输出 layout id。
   - 不输出绝对坐标。
   - 每页内容密度受限。
4. Spec builder 根据 layout registry 生成节点位置。

验收：

- 同类输入多次生成版式结构稳定。
- 内容过多时能截断、拆页或 warning，不强行塞入一页。
- 输出的 spec 可读、可测试。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_spec.py tests/unit/tools/test_ppt_tool.py -q
```

完成说明：

- 新增 11 类确定性 layout registry，集中定义场景、槽位、字号、间距、安全边距、文本密度和图片比例。
- Planner 仅输出 layout id，解析阶段清理绝对坐标并修正未知 layout。
- Spec builder 统一按 registry 生成节点；要点、目录、指标、表格和图表支持稳定拆页，超长文本和不适合拆页的内容确定性截断。
- `SlideDeckSpec.warnings` 记录回退、拆页和截断行为，`SlideSpec.layout` 保留实际布局，便于审计。
- PPT Python 测试 51 passed；Node 测试 5 passed；Node build 通过。

### Phase 4：HTML 高保真 PPTX 导出

状态：✅ 已完成开发（2026-06-30）

目标：

- 对现有 HTML 页面 PPT 提供稳定的高保真 PPTX 导出。

改动：

1. 新增 HTML exporter：
   - `src/tools/ppt/html_exporter.py`
   - Playwright 打开 HTML 文件或 HTML 字符串。
   - 固定 viewport：默认 1920x1080，支持配置。
   - 等待字体、图片、canvas、动画最终态。
2. 分页策略：
   - 优先 `section.slide`。
   - 兼容横向 deck transform 状态。
   - 无 section 时按整页截图输出单页。
3. 截图策略：
   - PNG 默认。
   - 可配置 JPEG 压缩。
   - 每页记录截图路径、尺寸、耗时。
4. 生成高保真 PPTX：
   - 将截图转为 `RasterLayerNode`。
   - 由 PptxGenJS 每页铺满图片。
5. QA：
   - 页数正确。
   - 图片非空。
   - PPTX 文件存在且大小合理。

验收：

- `guizang-ppt-skill` 生成的 HTML 能导出高保真 PPTX。
- WebGL/canvas 背景不丢失。
- 无空白页。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_html_export.py -q
```

完成说明：

- 新增 `html_exporter.py`，支持 HTML 字符串和本地 HTML 文件，默认
  1920x1080 viewport，可配置 PNG/JPEG、JPEG 质量和超时。
- 等待字体、图片和双帧绘制完成，结束 CSS/Web Animations；优先按
  `section.slide` 分页，并重置横向 deck transform 后逐页截图。
- 截图清单记录路径、像素尺寸、文件大小和耗时；空白页、尺寸不符、
  截图缺失、超时和不安全文件输入均返回明确错误契约。
- 每页截图转换为整页 `RasterLayerNode`，由 Node/PptxGenJS 输出 PPTX；
  QA 校验页数、截图非空及 PPTX 文件和合理大小。
- `ppt_process` 已接入 `html_to_pptx`。`high_fidelity` 正常输出；
  `both` 在 Phase 5 前仅返回高保真文件和明确 warning；`editable`
  明确拒绝，不伪造可编辑结果。功能受 `PPT_ENABLE_HTML_EXPORT` 控制。

### Phase 5：HTML 尽量可编辑 PPTX 导出

状态：✅ 已完成（2026-06-30）

目标：

- 在高保真截图版之外，提供可编辑率尽量高的 PPTX。

改动：

1. 新增 DOM extractor：
   - Playwright 注入 JS。
   - 读取 `getBoundingClientRect()`。
   - 读取 `getComputedStyle()`。
   - 提取文本、图片、基础形状、z-index、selector。
2. DOM -> `SlideDeckSpec`：
   - `h1/h2/p/li` -> `TextNode`
   - card/divider/tag -> `ShapeNode`
   - `img/svg` -> `ImageNode`
   - `table` -> `TableNode`
   - canvas/复杂滤镜 -> `RasterLayerNode`
3. 局部截图：
   - canvas。
   - `backdrop-filter`。
   - `mix-blend-mode`。
   - 复杂 SVG 或无法映射节点。
4. 可编辑率统计：
   - 原生文本数量。
   - 原生形状数量。
   - 图片层数量。
   - raster 层面积占比。
5. `export_mode` 行为：
   - `high_fidelity`：只生成截图版。
   - `editable`：只生成可编辑版，失败返回错误。
   - `both`：同时生成两版。

验收：

- 简单 HTML：文本、卡片、图片基本可编辑。
- 复杂 HTML：至少生成可打开 PPTX，并准确报告 raster 层。
- `both` 模式返回 `file_path` 和 `alternate_file_path`。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_html_editable_export.py -q
```

完成内容：

- 已实现 DOM rect/computed style/selector/z-index 抽取及 viewport 到英寸映射。
- 已实现文本、基础形状、图片/SVG、表格原生节点和复杂区域局部截图。
- 已实现可编辑率统计及 `high_fidelity/editable/both` 完整输出行为。
- 已通过真实 Playwright、双 PPTX 生成与 `python-pptx` 重开测试。

### Phase 6：模板跟随增强

状态：✅ 已完成（2026-07-01）

目标：

- 将模板模式从“简单填 placeholder”升级为可审计、可控的模板跟随。

改动：

1. `TemplateAnalyzer` 增强：
   - 提取 slide size、母版、layout、placeholder、字体、颜色。
   - 提取已有幻灯片摘要。
   - 输出 `template-audit.json`。
2. 新增 frame map：
   - 输出页 -> 模板 slide/layout。
   - editTargets。
   - 允许新增对象列表。
3. 新增 deviation log：
   - 无匹配 layout。
   - 内容过密。
   - 必须新增对象。
   - 发生降级。
4. 模板生成策略：
   - 第一阶段：基于 layout 新建页 + 严格 placeholder 填充。
   - 第二阶段：评估是否克隆源幻灯片后替换对象。
5. 模板模式不混用内置主题。

验收：

- 模板生成前必有 `template-audit.json` 和 `template-frame-map.json`。
- 模板页脚、页码、品牌元素尽量保留。
- 无法安全匹配时返回 warning，不静默乱套模板。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_template.py -q
```

完成内容：

- 已实现模板尺寸、master/layout、placeholder、主题字体/颜色和原始页面摘要审计。
- 已实现 `template-audit.json`、`template-frame-map.json`、`deviation-log.json` 临时产物及生成前门禁。
- 已实现 layout/placeholder 优先生成、显式源页面克隆、缺失占位符声明新增和安全降级 warning。
- 模板模式仅复用源模板 master/layout，不调用内置 theme；路径与错误信息不对外泄漏。
- 已通过真实 PPTX 审计、frame map、品牌对象保留、偏差记录和重新打开测试。

### Phase 7：PPTX QA 与质量报告

状态：✅ 已完成（2026-07-01）

目标：

- 生成后不仅确认有文件，还要确认可交付质量。

改动：

1. 新增 `PPTQualityValidator`：
   - 文件存在。
   - 页数正确。
   - 非空页。
   - 对象数量统计。
   - 图片文件有效。
   - raster 层统计。
   - warning 汇总。
2. 渲染预览：
   - 优先 LibreOffice headless 或可用转换器。
   - 环境缺失时降级为结构检查。
3. layout JSON：
   - Node renderer 输出简化对象树。
   - Python QA 读取对象树做统计。
4. QA 结果写入：
   - `qa-report.json`
   - 工具返回 `qa_summary`

验收：

- 空白页、0 字节图片、页数不一致会失败。
- 可编辑版能返回 `editable_ratio`。
- 高保真版能返回截图层数量和图片尺寸。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt_quality_validator.py -q
```

完成内容：

- 已实现统一 `PPTQualityValidator`，覆盖文件/可打开性、页数、空白页、对象越界、图片和 raster 可读性。
- 已实现 LibreOffice headless PDF、Poppler PNG preview；依赖缺失或转换失败时安全降级并记录 warning。
- Node renderer `layout.json` 已扩展为脱敏对象树，所有生成路径输出 `.layout.json` 和 `.qa-report.json`。
- 工具结果稳定返回结构、preview、raster、`editable_ratio`、warning/error 和 deliverable 状态。
- 已实现 `PPT_QA_STRICT`：严重 QA 错误在 strict 模式下阻止成功交付，非 strict 模式保留文件并明确告警。
- 已通过真实 PPTX、损坏包/图片、无 LibreOffice、strict/非 strict 和双输出独立 QA 测试。

### Phase 8：Agent 提示与文档更新

状态：待开发

目标：

- 让 Agent 正确使用新版 PPT 工具入参。

改动：

1. 更新主 Agent 工具使用指南：
   - PPT 生成优先传 `instruction + content`。
   - HTML 转 PPTX 使用 `content_type=html` 和 `export_mode`。
   - 生成后仍必须调用 `cp`。
2. 检查子智能体/技能直接调用 PPT 的提示。
3. 更新用户可见错误信息：
   - 缺少 Node renderer。
   - HTML 转换失败。
   - 模板无法安全匹配。

验收：

- Agent 不再把完整指令和正文混在 `context`。
- 工具失败时 Agent 不声称已经生成文件。

测试：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/prompts -q
```

## 4. 文件与目录规划

新增或重点改动：

```text
src/tools/ppt/
├── ppt_process_tool.py              # 入参、路由、调用主流程
├── input_normalizer.py              # 新增：结构化入参归一化
├── spec.py                          # 新增：SlideDeckSpec Python schema
├── spec_builder.py                  # 新增：planner/markdown/html 到 spec
├── renderer.py                      # 新增：Python 调 Node renderer
├── html_exporter.py                 # 新增：HTML 高保真/可编辑导出
├── quality_validator.py             # 新增：PPTX QA
├── template_analyzer.py             # 增强：template audit/frame map
├── generator.py                     # 保留：旧 python-pptx 回滚路径
└── renderer-node/
    ├── package.json
    ├── tsconfig.json
    ├── src/
    │   ├── render.ts
    │   ├── spec.ts
    │   ├── units.ts
    │   ├── nodes/
    │   └── qa.ts
    └── tests/
```

测试：

```text
tests/unit/tools/
├── test_ppt_tool.py
├── test_ppt_input_normalizer.py
├── test_ppt_spec.py
├── test_ppt_renderer.py
├── test_ppt_html_export.py
├── test_ppt_html_editable_export.py
├── test_ppt_template.py
└── test_ppt_quality_validator.py
```

## 5. 依赖与部署

新增 Node 依赖：

- `pptxgenjs`
- `typescript`（仅构建/测试）
- `@types/node`（仅构建/测试）

依赖版本由 `src/tools/ppt/renderer-node/package-lock.json` 锁定。部署构建必须使用
`npm ci && npm run build`，不提交 `node_modules/` 和 `dist/`。

部署要求：

- Docker 镜像安装 Node.js LTS。
- 构建阶段执行 `npm ci` 和 `npm run build`。
- 运行时可执行 `node src/tools/ppt/renderer-node/dist/render.js`。
- 默认 `PPT_RENDERER=pptxgenjs`；`PPT_RENDERER_FALLBACK=true` 时普通主题/大纲
  的 Node 渲染失败可回退 `python-pptx`，设为 `false` 可禁止回退。
- Playwright 浏览器依赖沿用项目已有安装策略；如环境缺失，HTML 导出返回明确错误。

## 6. 回滚策略

1. `PPT_RENDERER=python_pptx` 可强制回旧生成器。
2. `PPT_ENABLE_HTML_EXPORT=false` 可关闭 HTML 转 PPTX。
3. Node renderer 失败时：
   - 普通主题/大纲生成可回退 `python-pptx`。
   - HTML 高保真导出不可回退旧生成器，返回明确错误。
4. 新入参为兼容扩展，不删除 `context`。

## 7. 总体验收

完成后需满足：

- `ppt_process` 支持新旧入参。
- 普通 PPT 生成默认走 PptxGenJS。
- HTML 高保真 PPTX 可稳定生成。
- HTML 可编辑 PPTX 可生成并返回可编辑率和降级说明。
- 模板模式输出 audit/frame map/deviation/QA。
- 所有生成结果含 `file_path`，不改变 `cp` 交付规则。
- 单元测试覆盖新增模块。

建议总测试命令：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_ppt*.py -q
npm --prefix src/tools/ppt/renderer-node test
npm --prefix src/tools/ppt/renderer-node run build
```
