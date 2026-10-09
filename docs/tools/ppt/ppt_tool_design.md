# PPT 工具设计（ppt_process）

> 版本：v3.0（整合版）｜日期：2026-10-09｜状态：✅ 已实现并持续维护
> 设计参考: [MiniMax pptx-generator](https://github.com/MiniMax-AI/skills/blob/main/skills/pptx-generator/SKILL.md) (MIT License，基础版设计参考)
>
> 本文档是 PPT 工具的唯一现行架构与设计文档，按当前代码库实际实现整合撰写，
> 并已并入以下历史文档的有效内容（原件已删除，过程细节见 git 历史）：
> - 基础版设计（2026-05-09，python-pptx 单渲染器时代，条目 20260512-1711）
> - 增强设计与开发计划（2026-06-30，Node/PptxGenJS 双运行时 + HTML 导出，条目 20260630-2158）
> - 图片资产接入设计与计划（2026-09-23，images 入参，条目 20260923-2340）
> - 深度调研报告（2026-05-09，结论均已落地，无需独立保留）

## 1. 工具定位与演进

`ppt_process` 是 Agent 生成/转换 PPT 的唯一工具入口（`PptProcessTool`，category="file"）。
演进脉络：

| 阶段 | 时间 | 交付 |
|------|------|------|
| 基础版 | 2026-05 | python-pptx 单渲染器、planner LLM 编排、模板基础分析 |
| 增强 | 2026-06/07 | Node + PptxGenJS 主渲染器、SlideDeckSpec 中间协议、HTML 双路径导出、模板跟随审计、QA 闭环 |
| 图片资产 | 2026-09 | `images` 结构化入参（分析图表嵌入），租户根域校验 + 确定性 reconcile |

## 2. 对外契约（入参）

`PptProcessInput`（`ppt_process_tool.py:47-106`）：

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| `instruction` / `content` / `output_name` / `context` | Optional[str]，strip 后空串归 None | instruction 用户目的；content 待处理正文；output_name 业务文件名（优先级最高）；context 仅兼容旧调用，由 normalizer 拆分 |
| `content_type` | `auto/text/markdown/html/slide_deck_spec/md/json/spec` | auto 由 normalizer 推断：HTML 起始标签→html；JSON 含 title+slides→slide_deck_spec；markdown 标题→markdown；否则 text |
| `export_mode` | `editable/high_fidelity/both` | HTML 转 PPTX 时使用；both 返回 `file_path` + `alternate_file_path` 双产物 |
| `file_paths` | Optional[List[str]]，剔空白项 | .pptx 模板 / .html 文件；外部文件须先 `cp` 进 workspace |
| `images` | Optional[List[ImageAssetInput]]，≤20 项 | 图片资产（见 §5） |

- 校验：content/context/file_paths 全空直接打回。
- `ImageAssetInput`（`ppt_process_tool.py:22-44`）：`path/title/caption` 三字段必填、strip 非空。
- 工具描述（TOOL_DESCRIPTION）契约要点：instruction+content 优先；HTML 转换须 content_type="html" + export_mode；外部文件先 cp；生成后必须调 `cp` 注册下载（both 模式两条路径分别 cp）。
- 标题优先级：`output_name` 去扩展名 → content 中 Markdown H1/H2 → `SlideDeckSpec.title` → planner 返回 title → "演示文稿"。

## 3. 模式体系与路由

`_detect_mode`（`ppt_process_tool.py:217-231`）优先级：

1. file_paths 含 .pptx → `template`
2. file_paths 含 .html/.htm → `html_to_pptx`
3. content_type=="html" → `html_to_pptx`
4. content_type=="slide_deck_spec" → `spec_to_pptx`
5. content 满足 `_looks_like_outline`（markdown 标题/编号列表/长度>200）→ `outline_to_pptx`
6. 否则 → `topic_to_pptx`

`images` 仅支持 `topic_to_pptx` / `outline_to_pptx`；其余模式传入即整体打回
（"images 仅支持主题/大纲生成模式；模板、HTML 与 spec 模式不支持传入图片资产"，
`ppt_process_tool.py:181-185`）。

## 4. 端到端架构

Python 编排 + Node 渲染的双运行时架构：

```text
PptProcessInput
  → PptInputNormalizer.normalize（context 拆分、content_type 推断、标题抽取）
  → _detect_mode 路由
  → 四路分发：
      auto(topic/outline)  planner → SlideDeckSpec → NodePptRenderer / python-pptx 兜底
      template             planner → TemplateAnalyzer → generate_from_template（python-pptx）
      html_to_pptx         HtmlExporter(Playwright) → spec → _render_node_spec
      spec_to_pptx         SlideDeckSpec 校验（不经 planner）→ _render_node_spec
  → _apply_quality_validation（strict 失败删产物并阻止交付）
  → file_path + qa_summary + warnings
```

### 4.1 auto 模式（topic/outline）

- `PPTPlanner.plan_from_topic / plan_from_content`（LLM 将主题或 Markdown 规划为统一 JSON 大纲；
  images 资源清单注入见 §5）→ `reconcile_image_slides` → `_generate_ppt`。
- `_generate_ppt`（`ppt_process_tool.py:472-510`）：
  - **主路径**：`PPT_RENDERER`（默认 pptxgenjs，`ppt_config.py:25`）且 Node 渲染器可用时，
    `SlideDeckSpecBuilder.from_planner` → `NodePptRenderer.render`（调
    `renderer-node/dist/render.js` 子进程）。
  - **兜底**：任何异常且 `PPT_RENDERER_FALLBACK`（默认 true）时，`get_theme` +
    `PPTGenerator.generate`（python-pptx，按 type/layout 分发 `layouts/*.py` 渲染器），
    结果附回退 warning。

双运行时边界（增强阶段确立，仍然有效）：Node 渲染器是工具内部子进程，只接收
spec JSON 文件路径、输出目录和渲染参数，不访问会话、租户、数据库或 LLM；Python 侧
负责超时、错误捕获、日志、临时目录清理、文件命名和交付。

### 4.2 SlideDeckSpec 语义模型

自研轻量 JSON 中间协议（`spec.py` + renderer-node 侧 `spec.ts`），统一 1280×720 px
坐标（Node 渲染器内部 `px/96` 转 inch）。设计原则：

- 字段尽量贴近 PptxGenJS API（`addText/addShape/addImage/addTable/addChart` 同名 options），减少转译成本。
- 节点类型：Text/Shape/Image/Table/Chart/Group/RasterLayer；每个节点带 `position/id/name`。
- LLM 不直接生成渲染代码，只生成或修正 spec；渲染确定性由 Node renderer 保证。
- 结构校验：节点越界（x+w>width 等）抛错；`extra=forbid`、`slides≥1`。
- WebGL、复杂滤镜等无法稳定映射的视觉效果用 `RasterLayerNode`（图片层），结果元数据中显式说明。

`spec_builder.py`：planner JSON → SlideDeckSpec；未知名 layout 回落 bullets、字段截断、
超限拆页（`_split_slide/_split_table/_split_chart`）；确定性坐标来自 `layout_registry.py`
的 Frame 注册表，共 11 种布局：cover/toc/section/bullets/stat/comparison/timeline/
chart/table/image/summary。

### 4.3 template 模式（模板跟随）

`_handle_template`（`ppt_process_tool.py:391-446`）：planner.plan_from_content →
`TemplateAnalyzer.analyze`（模板审计）→ match_content_to_layouts →
`generate_from_template`（python-pptx 直接克隆源幻灯片/套版式生成，不经 pptxgenjs）。

模板跟随哲学（增强阶段确立）：

- 内部产物 `template-audit.json`（尺寸/母版/layout/placeholder/字体/颜色摘要）、
  `template-frame-map.json`（输出页 → 模板页映射与 editTargets）、`deviation-log.json`
  （不可继承/需新增/降级记录），保存在临时目录随会话销毁，不作为用户交付文件；
  qa_summary 记录三个 created 标志与 deviation_count，deviation 进 warnings。
- 编辑边界：模板作为视觉来源时不混用内置主题；尽量编辑已有 placeholder/继承对象；
  不删除母版、页脚、页码、品牌 chrome。
- 模板大小上限 50MB（`template_analyzer.py:23`）。

### 4.4 html_to_pptx 模式（双路径导出）

`_handle_html`（`ppt_process_tool.py:233-331`）：需 `PPT_ENABLE_HTML_EXPORT`（默认
false，`ppt_config.py:32`）。`HtmlExporter.export`（Playwright）：

- **high_fidelity**：整页截图铺满 PPTX，视觉还原优先，作为可靠降级结果。
- **editable**：DOM/computedStyle 抽取 → SlideDeckSpec 原生重建（文本/shape/table
  原生对象），canvas/backdrop-filter 等复杂区域局部截图为 RasterLayer；统计
  editable_ratio 与 raster_layer_count。
- **both**：双产物（file_path + alternate_file_path），供人工审核。

QA：输出文件须存在且 ≥10KB（`:313`）；截图临时目录在 QA 通过后即删；
`screenshots` 返回 manifest 不含本地路径。

### 4.5 QA 质检闭环

`_apply_quality_validation`（`ppt_process_tool.py:536-613`，`PPTQualityValidator`）：
所有模式成功后统一检查——文件存在、页数正确、非空页、对象数量、文本越界/截断、
形状重叠、图片有效；strict 失败删除产物并返回"PPT 质量检查未通过，已阻止交付"。
pptxgenjs 路径另有渲染 QA（`renderer.py:104-115`）：qa.version=="1.0"、slide_count/
node_count 与 spec 一致；产物旁发布 `.layout.json` / `.qa-report.json` 副产物。

## 5. 图片资产链路（images）

场景：agent 自主调度 `analyze_data` → 整理结论 → `ppt_process`，把分析图表 PNG 作为
图片资产嵌入 PPT。设计原则（图片资产阶段确立）：**契约而非引导**（不加链式提示词，
只在入参契约声明"能吃什么"，何时调由 agent 自主判断）；**语义随图传递**（title+caption
必填，语义来源是刚读过 conclusion 的调用方 agent，不做 VL 图片理解）。

链路（`image_assets.py`）：

1. **租户根域解析** `resolve_tenant_root`（`:34-58`）：tool 执行上下文 tenant_id →
   saas `get_current_tenant_id()` → `_anonymous` 兜底；根域 =
   `storage/tenants/{tenant_id}`。
2. **校验** `validate_images`（`:68-110`）：>20 张整体打回；逐项 path/title/caption
   非空 → 根域包含检查（`normcase(realpath)` 前缀，`_within_root`，防穿越/跨租户）→
   扩展名 {.png,.jpg,.jpeg} → 文件存在 → 非空文件 → ≤20MB；通过者 realpath 绝对化；
   任何错误整体打回、逐条列出。
3. **planner 注入** `format_image_resources`（`planner.py:82-97`）：user prompt 末尾
   追加"可用图片资源"编号清单（《标题》——caption + 路径）；system prompt 硬性规则
   第 11 条：image 页的 image_path 必须原样取自清单路径，不得编造或修改。
4. **确定性回收** `reconcile_image_slides`（`:131-215`）：LLM 转抄不可全信，plan 返回后
   统一 reconcile——image 页路径匹配（normcase 全路径相等 → 退化 basename 相等且唯一）
   命中回写校验后绝对路径（自愈转抄误差）；未命中 image 页丢弃记 warning；重复引用去重；
   未被引用的图片在最后一个 summary 页前（无则末尾）强制补 image 页（"传了必进"）。
5. **渲染**：
   - 主路径 `spec_builder._image`（`:502-521`）：PIL 读实际像素等比 contain 居中，
     白色 roundRect 承托 + 图注卡片。
   - python-pptx 兜底 `layouts/image.py::render_image`：标题 + add_picture 等比适配
     （高 4.4" 框、超宽按宽 9.6" 缩放）+ caption；文件缺失降级为文字页不中断。
6. **spec 模式堵漏** `normalize_spec_image_paths`（`:218-245`）：spec 内 image/raster
   节点路径做同样校验与绝对化，堵住绕过 `images` 的路径；错误含 slide[id] 与 basename，
   不泄全路径。
7. **反馈闭环**：成功 message 追加"编排图片 N 页"；reconcile warnings 并入结果。

## 6. 安全设计

| 面 | 机制 |
|----|------|
| images/spec 路径 | 租户根域 `normcase(realpath)` 包含检查（模式同 `analysis_artifacts.py`），LLM 可控路径不能直通任意文件读取 |
| 跨租户 | 根域含 tenant_id，穿越与跨租户引用同被拦截 |
| HTML 导出 | 内容与文件双重 ≤10MB；仅本地 .html/.htm；`_restrict_requests` 仅放行 data/blob/about 与同目录 file 请求；`PPT_ENABLE_HTML_EXPORT` 默认关 |
| 模板 | ≤50MB |
| 错误脱敏 | `_format_user_error` 按异常类型映射固定文案；安全类异常用消息白名单（命中原文，未命中通用语），该模式被其他工具参照（`_helpers.py:10`） |

## 7. 输出与交付约定

- 输出目录：`get_current_conversation_dir()`，ImportError 回落 `storage/ppt`。
- 结果 JSON：`success/file_path/slide_count/renderer`（"pptxgenjs" 或
  "python_pptx"）/`message`（"已生成PPT，共 N 页"）/可选 `warnings`/`qa_summary`；
  html both 额外 `alternate_file_path` + `alternate_qa_summary`。
- 交付规则不变：工具只返回 `file_path`，Agent 后续调用 `cp` 注册下载。

## 8. 主题体系

两套并存：`themes.py` 4 个 pptxgenjs 主题（midnight_gold 默认/teal_tech/indigo_clean/
crimson_warm，供主路径）；`theme.py` 的 python-pptx 主题体系
（`get_theme(theme_id, style)`，供兜底路径）。

## 9. 测试与质量基线

`tests/unit/tools/` 合计 117 个用例：test_ppt_tool(26) / test_ppt_spec(19) /
test_ppt_image_assets(16) / test_ppt_quality_validator(12) / test_ppt_template(11) /
test_ppt_input_normalizer(11) / test_ppt_html_export(11) /
test_ppt_html_editable_export(6) / test_ppt_renderer(5)。

## 10. 非目标（现行边界）

- 不承诺任意网页 100% 转原生可编辑 PPTX；不导出动画/转场。
- 不接入第三方商业 PPT API；不做 Google Slides 导入/导出。
- 图片资产不做 VL 图片理解、不做裁剪/美化；不改 planner 布局体系与模式路由逻辑。
- 不替换 `guizang-ppt-skill` 的 HTML 主流程（HTML PPT 生成与 ppt_process 是两条并行能力）。
