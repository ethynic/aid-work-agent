# PDF 工具质量验证增强开发计划

> 日期：2026-06-30  
> 状态：待开发  
> 关联设计：[PDF 工具能力差距分析与增强设计方案](pdf_tool_gap_analysis_design.md)

## 1. 目标

本次开发同时解决三类问题：

1. 修复现有 PDF 工具实现质量问题。
2. 修复旧设计文档、测试与真实代码不一致的问题。
3. 补齐 Codex PDF 插件强调的 inspect/render/validate 质量验证链路。

非目标：

- 不重写 `pdf_process` 单入口架构。
- P0 不引入复杂视觉模型检查。
- P0 不承诺复杂 HTML/CSS 高保真转 PDF。
- 不改变文件交付链路：生成文件后仍必须由 Agent 调用 `cp`，由 `cp` 给前端用户提供下载卡片。

## 2. 改动范围

| 类型 | 文件 |
|------|------|
| 修改 | `src/tools/pdf/pdf_process_tool.py` |
| 修改 | `src/tools/pdf/pdf_router.py` |
| 修改 | `src/tools/pdf/pdf_reader.py` |
| 修改 | `src/tools/pdf/pdf_to_md.py` |
| 修改 | `src/tools/pdf/pdf_writer.py` |
| 修改 | `src/tools/pdf/pdf_merger.py` |
| 修改 | `src/tools/pdf/pdf_lib.py` |
| 新增 | `src/tools/pdf/pdf_capabilities.py` |
| 新增 | `src/tools/pdf/pdf_inspector.py` |
| 新增 | `src/tools/pdf/pdf_renderer.py` |
| 新增 | `src/tools/pdf/pdf_validator.py` |
| 修改 | `tests/unit/tools/test_pdf_tool.py` |
| 新增 | `tests/unit/tools/test_pdf_validation.py` |
| 修改 | `docs/tools/pdf/pdf_tool_design.md` |
| 修改 | `docs/tools/pdf/pdf_tool_gap_analysis_design.md` |

## 3. 实施步骤

### Phase 0：基线确认

1. 运行现有 PDF 单测，记录失败项：

```bash
pytest tests/unit/tools/test_pdf_tool.py -q
```

2. 用 `rg` 确认测试中引用的旧私有函数：

```bash
rg -n "_find_pandoc|_html_to_pdf_via_pandoc|_html_to_pdf_via_weasyprint|_get_default_css" tests/unit/tools/test_pdf_tool.py
```

验收：

- 明确当前失败项与测试漂移项清单。

### Phase 1：修复现有实现质量问题

1. `pdf_merger.py`
   - 修复 `split_pdf()` 在 `save()` 前关闭 `new_doc` 的问题。
   - `split_pdf()` 对无效 range 返回 warnings；全部无效时返回 `success=false`。
   - `extract_pages()` 对全部无效页码返回 `success=false`。
   - 确保异常路径关闭 `doc/new_doc`。

2. `pdf_lib.py`
   - 新增 `sanitize_pdf_filename(file_name)`。
   - `save_temp()` 使用安全文件名，禁止路径穿越。
   - 文件名冲突时追加短 UUID，避免覆盖旧文件。

3. `pdf_process_tool.py`
   - 新增页码参数规范化辅助方法。
   - `read/read_tables/pdf_to_md` 统一接收 1-based pages，并转为内部 0-based。
   - 保持生成型操作只返回 `file_path/files`，不在工具内部替代 `cp`。

4. `pdf_writer.py`
   - 明确 `css` 参数 P0 不做高保真支持，返回 warnings。
   - 修复 HTML 表格与正文顺序保持问题。
   - 对不支持的图片、复杂 CSS、复杂嵌套元素返回 warnings。

验收：

- 现有 10 个操作返回结构兼容。
- 无效页码不再静默成功。
- 安全文件名测试通过。

### Phase 2：补齐质量验证模块

1. 新增 `pdf_capabilities.py`
   - 探测 Python 包：`fitz`、`pdfplumber`、`pypdf`、`fpdf`、`pymupdf4llm`、`reportlab`。
   - 探测命令：`pdftoppm`、`pdfinfo`、`soffice/libreoffice`、`pandoc`。
   - 返回结构化 capability report。

2. 新增 `pdf_inspector.py`
   - 检查文件存在、扩展名、可打开性。
   - 返回页数、页面尺寸、旋转角、元数据、每页文本字符数、加密状态。
   - 加密或损坏 PDF 返回明确错误。

3. 新增 `pdf_renderer.py`
   - 优先 Poppler `pdftoppm`。
   - 缺失时回退 PyMuPDF `get_pixmap()`。
   - 支持 `pages`、`dpi`、`max_pages`。
   - 输出 PNG 到租户目录下的 `pdf_validation` 子目录或临时目录。

4. 新增 `pdf_validator.py`
   - basic：文件存在、可打开、页数 > 0。
   - structural：页面尺寸、空白页、文本覆盖率、敏感元数据 warnings。
   - visual：渲染 PNG 存在，非全白/全黑。

验收：

- `inspect` 可对正常、损坏、加密 PDF 返回可解释结果。
- `render_pages` 在无 Poppler 时可用 PyMuPDF 回退。
- `validate` 能输出 `success/level/errors/warnings/rendered_pages`。

### Phase 3：接入 `pdf_process`

1. `TaskType` 新增：
   - `inspect`
   - `render_pages`
   - `validate`

2. `PdfProcessTool` 新增 handler：
   - `_handle_inspect`
   - `_handle_render_pages`
   - `_handle_validate`

3. 生成型操作追加 `_validate_output_pdf()`：
   - `md_to_pdf`
   - `html_to_pdf`
   - `docx_to_pdf`
   - `merge`
   - `split`
   - `extract_pages`
   - 返回中保留 `file_path/files`，必要时新增建议 `display_name`；Agent 仍必须调用 `cp`。

4. `pdf_router.py` prompt 更新：
   - 增加新操作说明。
   - 明确外部页码 1-based。
   - 对“检查/验证/看看 PDF 是否正常/渲染预览”等请求路由到新操作。

验收：

- Agent 仍只需要调用 `pdf_process(context, file_paths)`。
- 生成 PDF 的返回中包含 `validation` 字段。
- 验证失败时错误信息简洁、可解释。

### Phase 4：测试修复与补充

1. 重写漂移测试：
   - 删除对不存在私有函数的 mock。
   - `md_to_pdf/html_to_pdf` 按 fpdf2 当前行为测试。
   - `docx_to_pdf` 分别测试 LibreOffice 成功、Pandoc 回退、全部失败。

2. 新增测试：
   - capabilities 探测。
   - inspect 正常/损坏/加密。
   - renderer Poppler 路径和 PyMuPDF 回退。
   - validator basic/structural/visual。
   - split/extract 无效页码。
   - save_temp 文件名安全。
   - 页码 1-based 到 0-based 转换。

3. 运行：

```bash
pytest tests/unit/tools/test_pdf_tool.py tests/unit/tools/test_pdf_validation.py -q
```

验收：

- PDF 工具相关单测全部通过。
- 不依赖本机必须安装 Poppler/LibreOffice/Pandoc；相关测试用 mock。

### Phase 5：文档同步

1. 更新 `docs/tools/pdf/pdf_tool_design.md`
   - 标注旧 Pandoc/WeasyPrint 方案为历史设计。
   - 写明当前 fpdf2 主路径。
   - 添加 v1.2 质量验证增强链接。

2. 更新本设计文档
   - 如果实施中发现新约束，补充到风险或实施记录。

3. 更新 `docs/ideas.md`
   - 开发开始时状态改为 🔧 部分完成。
   - 开发完成后移动到 `docs/ideas_finished.md`。

验收：

- 代码、测试、设计文档描述一致。
- `docs/ideas.md` 中设计和开发计划链接完整。

## 4. 回归范围

必须重点回归：

- 上传 PDF 后读取内容。
- PDF 转 Markdown，文字型和扫描件 OCR 降级。
- Markdown 转 PDF，并返回可下载文件。
- Word 转 PDF，LibreOffice/Pandoc 均缺失时错误明确。
- 合并、拆分、提取页面。
- 文件名包含中文、空格、路径分隔符时的保存行为。

## 5. 风险控制

| 风险 | 控制 |
|------|------|
| 渲染依赖缺失 | PyMuPDF 回退，capabilities 明确报告 |
| 视觉检查误判 | P0 只做非空白/非黑页等保守检查 |
| 文件交付行为变化 | 不改 cp 契约；PDF 工具不直接生成前端下载卡片 |
| 旧测试与新实现冲突 | 以公开行为为准重写测试，不测试不存在的私有函数 |
| 生成质量短期仍有限 | P0 用 validator 暴露问题，P2 再引入 reportlab |

## 6. 完成标准

- PDF 工具实现缺陷已修复。
- 旧设计文档与真实实现一致。
- 新增 inspect/render_pages/validate 能力。
- 生成型操作返回 validation。
- PDF 相关单测通过。
- 不新增明文敏感信息返回。

## 7. 实施记录

### P0 完成（2026-06-30）

- 修复现有质量问题：`split_pdf()` 保存前关闭文档、无效页码静默成功、`extract_pages()` 全部无效页仍成功、文件名未集中清洗、HTML 表格顺序失真。
- 新增质量验证模块：`pdf_capabilities.py`、`pdf_inspector.py`、`pdf_renderer.py`、`pdf_validator.py`。
- 接入 `pdf_process`：新增 `inspect`、`render_pages`、`validate` 操作；生成型操作追加 `validation` 字段。
- 保持交付链路不变：PDF 工具返回 `file_path/files`，前端下载仍必须由 Agent 调用 `cp` 完成。
- 修复测试漂移：移除 PDF writer 测试中对旧 Pandoc/WeasyPrint 私有函数的 mock。
- 文档同步：`pdf_tool_design.md` 更新为 v1.2，说明当前 fpdf2 主路径。
- 验证结果：`pytest tests/unit/tools/test_pdf_tool.py tests/unit/tools/test_pdf_validation.py -q` 通过，100 passed。
