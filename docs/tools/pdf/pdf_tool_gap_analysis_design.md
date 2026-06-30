# PDF 工具能力差距分析与增强设计方案

> 日期：2026-06-30  
> 状态：设计完成，待开发  
> 关联现有文档：[PDF 工具设计文档](pdf_tool_design.md)

## 1. 背景

当前项目已实现 `pdf_process` 工具，定位是面向 Agent 的 PDF 综合处理入口。该工具通过 `context + file_paths` 接收用户需求，由内部 LLM 路由到具体操作，并执行读取、转换、生成、合并、拆分等 pipeline。

本设计方案基于现有代码与 Codex PDF 插件能力进行对比，目标不是替换当前工具，而是在保留项目稳定性、可预测性和多租户文件交付机制的前提下，补齐 PDF 生成后的渲染验证、结构化检查、质量报告和更可靠的运行时能力探测。

## 2. 当前项目 PDF 工具现状

### 2.1 已有功能

入口：`src/tools/pdf/pdf_process_tool.py`

| 能力 | 操作 | 实现方式 |
|------|------|----------|
| 文本读取 | `read` | `PyMuPDF` 提取页面文本和元数据 |
| 表格提取 | `read_tables` | `pdfplumber.extract_tables()` |
| OCR | `ocr` | 调用已有 `paddleocr_doc_parsing(file_type=0)` |
| PDF 转 Markdown | `pdf_to_md` | `PyMuPDF4LLM`，文本过少时降级 OCR |
| Markdown 转 PDF | `md_to_pdf` | `markdown` 转 HTML，再用 `fpdf2` 生成 |
| HTML 转 PDF | `html_to_pdf` | 简化 HTML 解析 + `fpdf2` 生成 |
| Word 转 PDF | `docx_to_pdf` | 优先 LibreOffice，回退 Pandoc |
| PDF 合并 | `merge` | `PyMuPDF.insert_pdf()` |
| PDF 拆分 | `split` | `PyMuPDF.insert_pdf()` 按范围复制页面 |
| 页面提取 | `extract_pages` | `PyMuPDF.insert_pdf()` 指定页面复制 |

### 2.2 架构特点

- 单一工具入口：Agent 不需要理解具体 PDF 子能力，只调用 `pdf_process`。
- 内部 LLM 路由：`PdfRouter` 负责把自然语言需求转为 `task + params`。
- Pipeline 串联：支持 `read,pdf_to_md` 等多步骤操作。
- 多租户文件落点：生成文件通过 `PdfFileHandler.save_temp()` 保存到当前租户上传目录。
- 测试覆盖较多：`tests/unit/tools/test_pdf_tool.py` 覆盖路由解析、核心 handler、读取、转换、合并拆分等。

### 2.3 当前实现中的主要问题

| 问题 | 影响 |
|------|------|
| 缺少渲染验收 | PDF 生成成功只代表文件存在，不能证明版式正常、中文不乱码、表格不溢出、内容未重叠 |
| 缺少 PDF 结构检查 | 未统一检查页数、尺寸、加密、损坏、页面为空、文字覆盖率等 |
| 生成路径能力有限 | `fpdf2` 对复杂 HTML/CSS、长表格、图片、分页控制、页眉页脚支持有限 |
| 文档与实现不完全一致 | 旧设计文档仍描述 Pandoc/WeasyPrint 主路径，实际 `md_to_pdf/html_to_pdf` 已改为 `fpdf2` |
| 运行时依赖探测不足 | 只有局部 `which` 或 import 尝试，缺少统一 capability report |
| 页码约定不统一 | `read/pdf_to_md` 内部 pages 为 0-based，`extract_pages/split` 对用户参数为 1-based，路由 prompt 容易产生歧义 |
| 文件交付依赖 Agent 再调 cp | 这是当前智能体交付链路的必要约束：`pdf_process` 返回文件路径，Agent 必须再调用 `cp`，前端用户才能看到下载卡片 |
| 缺少安全增强能力 | 尚未实现加密、解密、脱敏、元数据清理、水印、压缩等企业文档常见操作 |

### 2.4 现有实现质量问题

本次优化必须同时处理已有实现质量问题，不能只叠加新能力。

| 类型 | 位置 | 问题 | 处理要求 |
|------|------|------|----------|
| 明确缺陷 | `pdf_merger.split_pdf()` | `new_doc.close()` 在 `new_doc.save()` 前执行，真实运行可能导致拆分失败 | P0 修复并补真实/半真实单测 |
| 鲁棒性不足 | `pdf_merger.split_pdf()` | 无效 range 被静默跳过，全部无效时仍可能返回 `success=true,count=0` | P0 改为返回明确错误或 warnings |
| 鲁棒性不足 | `pdf_merger.extract_pages()` | 全部页码无效时仍可能生成空 PDF 或返回成功 | P0 增加有效页校验 |
| 语义不一致 | `read/read_tables/pdf_to_md` | 模块说明使用 0-based pages，但路由 prompt 面向用户混用 1-based 语义 | P0 统一外部 1-based，内部转换 |
| 接口参数失效 | `pdf_writer.md_to_pdf/html_to_pdf` | `css` 参数保留但实际不生效，容易误导调用方 | P0 明确废弃或实现受支持样式参数 |
| 版式风险 | `pdf_writer._HtmlTableParser` | 表格提取后可能改变原 HTML 中正文与表格的顺序 | P0 修复解析策略或限制说明 |
| 版式风险 | `pdf_writer._render_html_content` | 复杂标签、嵌套列表、代码块、图片等只能部分兜底 | P0 在 validator 中暴露 warnings；P2 再升级生成器 |
| 文件名风险 | `PdfFileHandler.save_temp()` | `output_name` 未集中做文件名清洗和冲突处理策略说明 | P0 加安全文件名规范，避免路径穿越和覆盖歧义 |
| 交付约束 | `pdf_process` 工具描述 | 生成文件后必须由 Agent 再调 `cp`，这是前端展示下载卡片的固定链路 | 不改为工具内自动登记；只强化提示词、返回字段和测试约束 |
| 测试漂移 | `tests/unit/tools/test_pdf_tool.py` | 部分测试仍 mock `_find_pandoc/_html_to_pdf_via_pandoc/_html_to_pdf_via_weasyprint` 等旧实现符号 | P0 重写测试以匹配当前 fpdf2/LibreOffice/Pandoc 回退实现 |

### 2.5 文档一致性问题

旧文档 [PDF 工具设计文档](pdf_tool_design.md) 仍保留第一版设计假设，需要作为本次优化的一部分修订。

| 文档位置 | 当前描述 | 实际实现 | 修订要求 |
|----------|----------|----------|----------|
| 技术选型 | `md_to_pdf/html_to_pdf` 依赖 Pandoc + WeasyPrint | 当前主路径是 `markdown + fpdf2` | 更新为 fpdf2 主路径，Pandoc 仅保留 DOCX 回退或未来可选引擎 |
| 依赖安装 | 要求安装 `weasyprint` | `requirements.txt` 未包含 `weasyprint`，仅 DOCX 回退路径尝试 import | 明确可选依赖，不作为 P0 必需 |
| `pdf_writer.py` 设计 | 描述 `_find_pandoc()`、默认 CSS、WeasyPrint 回退 | 相关函数已不存在 | 删除过时代码片段，改为真实模块说明 |
| 实施记录 | 标记 Pandoc + WeasyPrint 路径已完成 | 与代码不符 | 修正为“已切换为 fpdf2 纯 Python 生成路径” |
| 页码说明 | `extract_pages` 部分示例曾写 0-indexed，路由又写 1-based | 代码对用户参数按 1-based 处理 | 统一文档口径：Agent/用户参数 1-based，内部私有函数可 0-based |
| 测试说明 | 声称 94 个测试全部通过 | 当前测试文件存在旧实现 mock，需重新验证 | 优化完成后重新记录真实测试结果 |

## 3. Codex PDF 插件能力摘要

Codex PDF 插件是一个面向“读、生成、检查、渲染、验证 PDF”的工作流能力，核心关注点是视觉质量和交付前验收。

| 能力 | 说明 |
|------|------|
| PDF 读取/检查 | 使用 `pdfplumber`、`pypdf` 做文本抽取和快速检查 |
| PDF 创建 | 推荐使用 `reportlab` 程序化生成，强调稳定排版 |
| 页面渲染 | 使用 Poppler 的 `pdftoppm` 把 PDF 页面渲染为 PNG |
| 元信息检查 | 使用 `pdfinfo` 获取页数、页面尺寸、加密状态等 |
| 视觉 QA | 交付前必须渲染并检查对齐、间距、字体、重叠、截断、页眉页脚等 |
| 文件约定 | 中间文件放 `tmp/pdfs/`，最终文件放 `output/pdf/` |

插件强调的不是更多 PDF 操作种类，而是“生成后必须验证视觉结果”。这是当前项目工具最明显缺口。

## 4. 差距矩阵

| 维度 | 当前项目 PDF 工具 | Codex PDF 插件 | 差距 |
|------|------------------|----------------|------|
| Agent 集成 | 已有单入口工具、LLM 路由 | 无项目级 Agent 工具封装 | 项目更强 |
| 多租户文件管理 | 已接入租户上传目录 | 仅本地文件约定 | 项目更强 |
| 文本/表格提取 | PyMuPDF + pdfplumber | pdfplumber + pypdf | 基本相当 |
| OCR | 已集成 PaddleOCR | 不内置 OCR | 项目更强 |
| PDF 转 Markdown | PyMuPDF4LLM + OCR 降级 | 无专门策略 | 项目更强 |
| PDF 生成 | fpdf2，偏简单内容 | reportlab，偏精确排版 | 插件在稳定排版上更强 |
| HTML/CSS 支持 | 简化解析，复杂 CSS 不可靠 | 不主张复杂 HTML，建议程序化生成 | 都不适合复杂 Web 还原 |
| 渲染验证 | 缺失 | Poppler 渲染 PNG 并人工/程序检查 | 插件更强 |
| 结构化检查 | 零散 | pdfinfo/pypdf 快速检查 | 插件更强 |
| 质量报告 | 缺失 | 工作流要求记录检查结果 | 插件更强 |
| 安全能力 | 未实现 protect/compress/metadata clean | 可基于 pypdf 扩展 | 都需项目化实现 |

结论：项目工具的“业务集成和操作广度”更好；Codex PDF 插件的“生成质量控制和交付验收”更成熟。弥合差距应优先补质量保障链路，而不是重写现有工具。

## 5. 目标设计

### 5.1 设计目标

1. 保留 `pdf_process` 作为唯一 Agent 入口。
2. 增加可选的 `inspect`、`render`、`validate` 能力，形成“生成/改造后自动验收”的闭环。
3. 对生成型操作默认执行轻量校验，重要文档或用户要求“正式/可交付/排版好”时执行完整渲染验收。
4. 统一页码输入语义：外部参数一律使用 1-based，内部模块转换为 0-based。
5. 统一依赖探测：启动或首次调用时报告 `fitz/pdfplumber/pypdf/fpdf2/reportlab/poppler/libreoffice/pandoc/pymupdf4llm` 可用性。
6. 输出结构保持兼容，新增字段只做增强，不破坏现有调用方。
7. 修复现有实现缺陷、测试漂移和文档漂移，确保代码、测试、设计文档三者一致。
8. 保持文件交付链路不变：生成文件后仍由 Agent 调用 `cp` 给前端用户提供下载，不在 PDF 工具内部绕过或替代 `cp`。

### 5.2 新增模块

```
src/tools/pdf/
├── pdf_inspector.py       # PDF 结构检查：页数、尺寸、加密、元数据、文本覆盖率
├── pdf_renderer.py        # Poppler/PyMuPDF 渲染页面为 PNG
├── pdf_validator.py       # 验证规则与质量报告
├── pdf_capabilities.py    # 运行时依赖探测
└── pdf_reportlab_writer.py # 可选：复杂报告类 PDF 的 reportlab 生成器
```

### 5.3 新增操作

| 操作 | 功能 | 阶段 |
|------|------|------|
| `inspect` | 读取 PDF 结构信息、元数据、页数、页面尺寸、是否加密、文本覆盖率 | P0 |
| `render_pages` | 将指定页面渲染为 PNG，返回图片路径列表 | P0 |
| `validate` | 基于结构检查 + 渲染结果输出质量报告 | P0 |
| `clean_metadata` | 清理标题、作者、创建工具等元数据 | P1 |
| `add_watermark` | 添加文字水印或图片水印 | P1 |
| `protect` | 添加密码保护 | P1 |
| `compress` | 压缩 PDF，降低体积 | P2 |
| `extract_images` | 提取嵌入图片 | P2 |

## 6. 核心方案

### 6.1 结构检查 `pdf_inspector.py`

使用 `pypdf` 和 `PyMuPDF` 组合实现：

- 文件是否存在、是否为 PDF。
- 是否加密，是否可解密。
- 页数、每页尺寸、旋转角度。
- 元数据：title、author、producer、creator、creation_date、mod_date。
- 每页文本字符数，用于判断空白页、扫描件比例。
- 文件大小、线性化信息可选。

返回示例：

```json
{
  "success": true,
  "page_count": 3,
  "encrypted": false,
  "file_size": 128420,
  "pages": [
    {"page": 1, "width": 595.28, "height": 841.89, "rotation": 0, "text_chars": 1200}
  ],
  "metadata": {"title": "..."},
  "warnings": []
}
```

### 6.2 渲染 `pdf_renderer.py`

优先使用 Poppler：

```bash
pdftoppm -png -r 150 input.pdf output_prefix
```

回退方案使用 PyMuPDF：

- `page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)`
- 保存为 PNG。

渲染结果保存到当前租户目录下的隐藏检查子目录，或临时目录：

```
storage/uploads/tenant_xxx/pdf_validation/<file_stem>/<run_id>/page-001.png
```

默认渲染策略：

- 小于等于 5 页：全量渲染。
- 大于 5 页：渲染首页、末页、前 3 页、含表格页；用户指定 `pages` 时按指定页。
- 最大渲染页数默认 10，可配置。

### 6.3 质量验证 `pdf_validator.py`

验证分为三层：

| 层级 | 规则 | 是否默认 |
|------|------|----------|
| basic | 文件存在、可打开、页数 > 0、无加密阻塞、文件大小正常 | 默认启用 |
| structural | 页面尺寸一致、无空白页、文本覆盖率不异常、元数据无敏感字段 | 生成后默认启用 |
| visual | 渲染 PNG 成功、页面非空、无明显黑页/白页、可选人工/LLM 视觉检查 | 按配置或重要任务启用 |

程序化视觉检查先做低风险规则：

- PNG 文件存在且大小大于阈值。
- 图像不是全白/全黑。
- 页面边缘是否存在大量截断色块。
- 渲染页数量与预期一致。

不在第一阶段做复杂视觉智能判断，避免引入不可预测性。后续可接入视觉模型，但必须仅作为 warnings，不直接修改文件。

### 6.4 生成后自动验收

对以下操作追加验证：

- `md_to_pdf`
- `html_to_pdf`
- `docx_to_pdf`
- `merge`
- `split`
- `extract_pages`

默认执行 basic + structural。用户明确要求“正式文档、排版好、交付、合同、报价单、报告”等场景，或配置 `tools.pdf.validate_visual_by_default=true` 时，再执行 visual。

返回结果新增：

```json
{
  "file_path": "...",
  "validation": {
    "success": true,
    "level": "structural",
    "warnings": [],
    "rendered_pages": []
  }
}
```

如验证失败：

- 文件生成失败：返回 `success=false`。
- 文件生成成功但质量警告：返回 `success=true`，附带 `validation.warnings`，由 Agent 诚实告知用户。
- 严重问题（打不开、0 页、加密无法读取）：返回 `success=false`。

### 6.5 生成器策略

保留现有 `fpdf2` 路径作为默认，新增 `reportlab` 专项生成器用于结构化报告类 PDF：

| 场景 | 推荐生成器 |
|------|------------|
| 简单 Markdown、短报告 | 现有 fpdf2 |
| 表格较多、固定版式报告、页眉页脚要求明确 | reportlab |
| Word 原文转 PDF | LibreOffice |
| 复杂 HTML/CSS 网页还原 | 不建议承诺高保真；可回退 WeasyPrint/Playwright，但需单独设计 |

第一阶段不强制替换 `fpdf2`，只增加验证；第二阶段再基于真实失败样本决定是否将报告类 PDF 切到 reportlab。

### 6.6 页码语义统一

外部接口和路由 prompt 统一声明：用户页码一律从 1 开始。

内部规则：

- `PdfProcessTool` 入口处规范化参数。
- `read`、`read_tables`、`pdf_to_md` handler 接收用户页码后转为 0-based。
- `extract_pages`、`split` 保持当前 1-based 用户语义。

新增辅助函数：

```python
def normalize_user_pages(pages: list[int], page_count: int) -> list[int]:
    """用户页码 1-based -> 内部页码 0-based，过滤越界并保留顺序。"""
```

### 6.7 现有实现修复策略

P0 阶段必须先完成以下修复，再叠加质量验证能力：

1. 修复 `split_pdf()` 的 `new_doc` 生命周期：只在保存完成后关闭文档。
2. 为 `split_pdf()` 和 `extract_pages()` 增加有效页校验：没有任何有效页时返回 `success=false`。
3. 增加 `sanitize_pdf_filename()`：仅保留安全文件名，禁止路径分隔符，自动补 `.pdf`，冲突时追加短 UUID。
4. 明确 `css` 参数策略：P0 暂不支持任意 CSS 时，应在返回 warnings 中说明；P2 若引入 WeasyPrint/Playwright 再恢复 CSS 高保真能力。
5. 修复 HTML 片段顺序：表格解析必须保持原始文档中的正文/表格顺序；无法可靠支持的 HTML 元素进入 validation warnings。
6. 更新 `tests/unit/tools/test_pdf_tool.py`：删除对旧 Pandoc/WeasyPrint 私有函数的 mock，改测当前公开行为。
7. 更新旧设计文档 `pdf_tool_design.md`：标注 v1.1 为历史方案，补充 v1.2 当前实现与本次增强链接。

## 7. 配置方案

新增配置：

```yaml
tools:
  pdf:
    validate_after_generate: true
    validate_level: "structural"  # basic | structural | visual
    visual_validate_max_pages: 10
    render_dpi: 150
    renderer: "auto"  # auto | poppler | pymupdf
    fail_on_validation_warning: false
    output_engine: "fpdf2"  # fpdf2 | reportlab
```

## 8. 实施计划

### P0：质量保障闭环

1. 修复现有缺陷：`split_pdf` 生命周期、无效页码返回、文件名清洗、HTML 顺序、测试漂移。
2. 修订旧设计文档，使技术选型、依赖和实现状态与代码一致。
3. 新增 `pdf_capabilities.py`，统一探测依赖可用性。
4. 新增 `pdf_inspector.py`，实现结构检查。
5. 新增 `pdf_renderer.py`，Poppler 优先、PyMuPDF 回退。
6. 新增 `pdf_validator.py`，实现 basic/structural/visual 三层校验。
7. `PdfProcessTool` 增加 `inspect/render_pages/validate` 操作。
8. 生成型 handler 调用 `_validate_output_pdf()`，返回 `validation` 字段。
9. 更新 `pdf_router.py` prompt，支持新操作并明确页码 1-based。
10. 补单元测试：依赖缺失、结构检查、渲染回退、验证失败/警告、生成后校验。

### P1：企业常用增强

1. `clean_metadata`：清理 PDF 元数据，降低敏感信息泄露风险。
2. `add_watermark`：支持文本水印。
3. `protect`：支持密码保护，注意不得在回复中明文返回密码。
4. 强化文件交付约束：生成型操作返回稳定的 `file_path/files` 和建议 `display_name`，但仍必须由 Agent 调用 `cp` 完成交付。

### P2：生成质量升级

1. 引入 `reportlab` 报告生成器，覆盖正式报告、报价单、带页眉页脚的固定版式。
2. 建立 PDF 视觉回归样本集，保存小型样例 PDF 和预期检查结果。
3. 评估复杂 HTML 转 PDF 的专用路径，如 WeasyPrint 或 Playwright print-to-pdf。

## 9. 测试与验收

### 9.1 单元测试

- `test_pdf_capabilities.py`
- `test_pdf_inspector.py`
- `test_pdf_renderer.py`
- `test_pdf_validator.py`
- 更新 `test_pdf_tool.py` 覆盖新增操作与生成后验证字段。

### 9.2 集成测试

使用小型真实 PDF 样本验证：

- 文字型 PDF：读取、inspect、validate。
- 扫描型 PDF：inspect 文本覆盖率低，提示 OCR。
- 生成型 PDF：md_to_pdf 后 structural 验证通过。
- 损坏 PDF：inspect/validate 返回失败。
- 加密 PDF：返回加密状态，不泄露敏感内容。

### 9.3 验收标准

- 生成 PDF 后至少能证明“文件可打开、页数正确、页面尺寸合理”。
- visual 模式下能输出渲染 PNG，且检测非空白/非黑页。
- 依赖缺失时返回明确错误和降级路径，不抛未捕获异常。
- 不破坏当前 `pdf_process` 既有 10 个操作的返回兼容性。
- 敏感信息不以明文返回给用户。

## 10. 风险与约束

| 风险 | 应对 |
|------|------|
| Poppler 在部署环境缺失 | PyMuPDF 渲染回退；capabilities 中明确标记 |
| visual 检查误判 | 第一阶段只做保守规则，复杂判断只给 warnings |
| fpdf2 复杂版式能力不足 | 先用验证暴露问题，再按场景引入 reportlab |
| 生成后验证增加耗时 | 默认 structural，visual 只在重要场景或配置启用 |
| cp 交付漏调 | 不改变交付契约；通过工具描述、返回字段和测试强化 Agent 必须调用 `cp` |

## 11. 推荐结论

推荐按“先验收、后增强生成器”的顺序推进：

1. P0 先补 `inspect/render/validate` 和生成后自动校验，这是和 Codex PDF 插件最大的差距。
2. P1 再补 metadata 清理、水印、加密等企业文档能力。
3. P2 基于实际失败样本决定是否引入 reportlab 或复杂 HTML 转 PDF 引擎。

这样可以最大程度复用现有 `pdf_process` 架构，同时把 PDF 工具从“能生成文件”提升到“能证明文件可交付”。
