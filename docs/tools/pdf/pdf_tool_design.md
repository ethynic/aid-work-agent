# PDF 工具设计文档

> 版本: v1.2 | 创建日期: 2026-05-09 | 最近更新: 2026-07-01 | 状态: 第一阶段已完成，质量验证增强开发中

> **实现校准（2026-07-01）**：本文档早期 v1.1 方案曾以 Pandoc + WeasyPrint 作为 Markdown/HTML 转 PDF 主路径。当前代码已调整为 `markdown` 解析 + `fpdf2` 纯 Python 生成；`docx_to_pdf` 统一使用 LibreOffice。PDF 质量验证增强、实现质量修复和差距分析见 [PDF 工具能力差距分析与增强设计方案](pdf_tool_gap_analysis_design.md)，开发计划见 [PDF 工具质量验证增强开发计划](pdf_tool_quality_validation_dev_plan.md)。

## 1. 概述

PDF 工具是一个综合性的 PDF 处理工具包，遵循 Word 工具的架构模式（单一入口 + LLM 路由 + Pipeline 执行）。支持 PDF 的生成、读取、转换、合并拆分等全生命周期操作。

## 2. 技术选型

### 已有依赖（零新增安装）

| 库 | 当前版本 | 用途 |
|---|---|---|
| `PyMuPDF` (fitz) | >=1.23.0 | PDF 文本提取、元数据读取、页面渲染为图片、PDF 合并/拆分 |
| `pdfplumber` | >=0.10.0 | PDF 表格提取 |
| `pypdf` | >=4.0.0 | PDF 合并/拆分/旋转/元数据写入（补充操作） |
| `markdown` | >=3.5.0 | Markdown → HTML 解析（缺失时有轻量 fallback） |
| `fpdf2` | >=2.8.0 | Markdown/HTML → PDF 纯 Python 生成 |
| `pandoc` | 系统级安装 | Word 工具的 Markdown → DOCX；PDF 工具不使用 |
| `PaddleOCR` | 已有工具 | 扫描件 PDF 的 OCR 识别 |

### 需新增依赖

| 库 | 用途 | 安装命令 |
|---|---|---|
| `PyMuPDF4LLM` | PDF → Markdown 结构化转换（基于已有 PyMuPDF） | `pip install PyMuPDF4LLM` |
| `weasyprint` | 可选，用于未来 HTML/CSS 高保真 PDF 方案 | `pip install weasyprint` |

> **当前实现说明**：`md_to_pdf` / `html_to_pdf` 不依赖 Pandoc 或 WeasyPrint；复杂 HTML/CSS 高保真转换不在第一阶段承诺范围内。

---

## 3. 功能清单

### 3.1 核心功能（第一阶段）✅ 已完成

| # | 操作名 | 功能 | 输入 | 输出 |
|---|--------|------|------|------|
| 1 | `read` | 读取 PDF 文本内容（文字型 PDF） | PDF 文件路径 | 文本内容 + 页数 + 元数据 |
| 2 | `read_tables` | 提取 PDF 中的表格 | PDF 文件路径 | 表格列表（每张表格为二维数组） |
| 3 | `ocr` | OCR 识别扫描件/图片型 PDF | PDF 文件路径 | Markdown 文本（调用已有 PaddleOCR 工具） |
| 4 | `pdf_to_md` | PDF 转 Markdown（结构化转换） | PDF 文件路径 | Markdown 文本 |
| 5 | `md_to_pdf` | Markdown 转 PDF | Markdown 文本或 .md 文件 | PDF 文件（下载链接） |
| 6 | `html_to_pdf` | HTML 转 PDF | HTML 文本或 .html 文件 | PDF 文件（下载链接） |
| 7 | `docx_to_pdf` | Word 转 PDF | .docx 文件路径 | PDF 文件（下载链接） |
| 8 | `merge` | 合并多个 PDF | 多个 PDF 文件路径 | 合并后的 PDF 文件 |
| 9 | `split` | 拆分 PDF（按页码范围） | PDF 文件路径 + 页码范围 | 拆分后的 PDF 文件 |
| 10 | `extract_pages` | 提取指定页面为独立 PDF | PDF 文件路径 + 页码列表 | 提取的 PDF 文件 |

### 3.2 增强功能（第二阶段，可选）⏳ 待开发

| # | 操作名 | 功能 |
|---|--------|------|
| 11 | `add_watermark` | 添加文字/图片水印 |
| 12 | `rotate` | 旋转 PDF 页面 |
| 13 | `extract_images` | 提取 PDF 中嵌入的图片 |
| 14 | `get_metadata` | 读取/修改 PDF 元数据（标题、作者、创建时间等） |
| 15 | `protect` | 添加 PDF 密码保护 |
| 16 | `compress` | 压缩 PDF 文件大小 |

---

## 4. 架构设计

### 4.1 文件结构

```
src/tools/pdf/
├── __init__.py                 # 导出 PdfProcessTool
├── pdf_process_tool.py         # 主工具入口（Agent 调用入口）
├── pdf_router.py               # LLM 路由器（判断操作类型和参数）
├── pdf_reader.py               # PDF 读取（文本提取、表格提取、元数据）
├── pdf_to_md.py                # PDF → Markdown 转换
├── pdf_writer.py               # 生成 PDF（Markdown/HTML/DOCX → PDF）
├── pdf_merger.py               # PDF 合并/拆分/页面提取
└── pdf_lib.py                  # 共享工具函数（路径解析、文件保存、CSS 模板等）
```

### 4.2 调用链路

```
Agent 调用 pdf_process(context, file_paths)
  │
  ▼
PdfProcessTool.execute()
  │
  ├─ PdfRouter.route(context, file_paths)  ← LLM 判断操作类型
  │   返回 {"task": "md_to_pdf", "params": {...}}
  │
  ▼
Pipeline 执行器
  │
  ├─ read → pdf_reader.read_text()
  ├─ read_tables → pdf_reader.extract_tables()
  ├─ ocr → 调用已有的 PaddleOCR 工具 (src/tools/ocr/ocr_tool.py)
  ├─ pdf_to_md → pdf_to_md.convert()
  ├─ md_to_pdf → pdf_writer.md_to_pdf()
  ├─ html_to_pdf → pdf_writer.html_to_pdf()
  ├─ docx_to_pdf → pdf_writer.docx_to_pdf()
  ├─ merge → pdf_merger.merge_pdfs()
  ├─ split → pdf_merger.split_pdf()
  └─ extract_pages → pdf_merger.extract_pages()
```

## 5. 各模块详细设计

### 5.1 pdf_process_tool.py — 主工具入口

完全遵循 `WordProcessTool` 的架构：

```python
class TaskType:
    """有效操作类型常量"""
    READ = "read"
    READ_TABLES = "read_tables"
    OCR = "ocr"
    PDF_TO_MD = "pdf_to_md"
    MD_TO_PDF = "md_to_pdf"
    HTML_TO_PDF = "html_to_pdf"
    DOCX_TO_PDF = "docx_to_pdf"
    MERGE = "merge"
    SPLIT = "split"
    EXTRACT_PAGES = "extract_pages"

    ALL = {READ, READ_TABLES, OCR, PDF_TO_MD, MD_TO_PDF,
           HTML_TO_PDF, DOCX_TO_PDF, MERGE, SPLIT, EXTRACT_PAGES}


class PdfProcessInput(BaseModel):
    context: Optional[str] = Field(None, description="...")
    file_paths: Optional[List[str]] = Field(None, description="...")


class PdfProcessTool(BaseTool):
    name = "pdf_process"
    category = "file"
    InputModel = PdfProcessInput
```

Pipeline 执行逻辑与 Word 工具一致：`_resolve_task → 拆分操作 → 按序执行 → _update_context → _merge_results`。

### 5.2 pdf_router.py — LLM 路由器

路由 prompt 示例（与 WordRouter 结构一致）：

```
你是 PDF 文档处理工具的内部路由器。

## 可用操作

1. read - 读取PDF文档的文本内容（适用于文字型PDF）
2. read_tables - 提取PDF中的表格数据
3. ocr - OCR识别扫描件/图片型PDF（适用于扫描件、图片PDF）
4. pdf_to_md - 将PDF转换为Markdown格式
5. md_to_pdf - 将Markdown文本转换为PDF文件
6. html_to_pdf - 将HTML内容转换为PDF文件
7. docx_to_pdf - 将Word文档(.docx)转换为PDF文件
8. merge - 合并多个PDF文件为一个
9. split - 将PDF按页码范围拆分
10. extract_pages - 提取PDF的指定页面为独立文件

## 判断规则

- context 要求读取/查看 PDF 文件内容 → read
- context 要求提取 PDF 中的表格 → read_tables
- context 要求 OCR / 识别扫描件 → ocr
- context 要求将 PDF 转为 Markdown → pdf_to_md
- context 中包含 Markdown 内容且要求生成 PDF → md_to_pdf
- context 中包含 HTML 内容且要求生成 PDF → html_to_pdf
- 有 .docx 文件且要求转为 PDF → docx_to_pdf
- context 要求合并多个 PDF → merge
- context 要求拆分 PDF 或按页提取 → split / extract_pages
- 操作可组合，如 "先读取再转为 Markdown" → read,pdf_to_md
```

### 5.3 pdf_reader.py — PDF 读取

#### `read_text(file_path, pages=None) -> Dict`

使用 **PyMuPDF** 提取文本内容：

```python
import fitz  # PyMuPDF

def read_text(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    doc = fitz.open(file_path)
    metadata = {
        "page_count": doc.page_count,
        "title": doc.metadata.get("title", ""),
        "author": doc.metadata.get("author", ""),
        "creation_date": doc.metadata.get("creationDate", ""),
    }

    page_texts = []
    target_pages = pages if pages else range(doc.page_count)
    for page_num in target_pages:
        page = doc[page_num]
        text = page.get_text("text")
        page_texts.append({"page": page_num + 1, "text": text})

    doc.close()
    return {
        "success": True,
        "content": "\n\n".join(p["text"] for p in page_texts),
        "pages": page_texts,
        "metadata": metadata,
    }
```

#### `extract_tables(file_path, pages=None) -> Dict`

使用 **pdfplumber** 提取表格：

```python
import pdfplumber

def extract_tables(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    tables_all = []
    with pdfplumber.open(file_path) as pdf:
        target_pages = pages if pages else range(len(pdf.pages))
        for page_num in target_pages:
            page = pdf.pages[page_num]
            tables = page.extract_tables()
            for i, table in enumerate(tables):
                tables_all.append({
                    "page": page_num + 1,
                    "table_index": i + 1,
                    "data": table,  # 二维列表
                })

    return {"success": True, "tables": tables_all, "count": len(tables_all)}
```

#### `get_metadata(file_path) -> Dict`

使用 PyMuPDF 读取 PDF 元数据。

### 5.4 pdf_to_md.py — PDF 转 Markdown

#### `convert(file_path, pages=None) -> Dict`

使用 **PyMuPDF4LLM**：

```python
import pymupdf4llm

def convert(file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
    # 按页转换，保留页面结构
    md_chunks = pymupdf4llm.to_markdown(file_path, page_chunks=True)

    if pages:
        selected = [md_chunks[p] for p in pages if p < len(md_chunks)]
    else:
        selected = md_chunks

    # 每个 chunk 是一个 dict，包含 text 和 metadata
    full_text = "\n\n---\n\n".join(
        chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
        for chunk in selected
    )

    return {
        "success": True,
        "markdown": full_text,
        "page_count": len(selected),
    }
```

#### 自动降级策略

```
pdf_to_md 调用时：
  1. 先尝试 PyMuPDF4LLM 提取（文字型 PDF，快速）
  2. 如果提取结果为空或几乎无文本 → 判断为扫描件
  3. 自动降级到 OCR 模式（调用 PaddleOCR）
  4. 返回 OCR 结果，并标注来源为 OCR
```

```python
def convert_smart(file_path: str) -> Dict[str, Any]:
    """智能转换：自动判断文字型或扫描件"""
    result = convert(file_path)
    md_text = result.get("markdown", "").strip()

    # 如果提取的文本很少（可能是扫描件），自动走 OCR
    if len(md_text) < 50:
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
        ocr_result = paddleocr_doc_parsing(file_path=file_path, file_type=0)
        if ocr_result.get("success"):
            return {
                "success": True,
                "markdown": ocr_result["full_text"],
                "page_count": len(ocr_result.get("texts", [])),
                "source": "ocr",  # 标注来源
            }

    result["source"] = "text_extract"
    return result
```

### 5.5 pdf_writer.py — 生成 PDF

#### `md_to_pdf(md_text, output_name=None, css=None) -> Dict`

当前实现使用 `markdown + fpdf2`。`css` 参数暂不做高保真支持，传入时返回 warning。

```python
def md_to_pdf(md_text: str, output_name: str = None,
              css: str = None, title: str = "") -> Dict[str, Any]:
    """Markdown → PDF，通过 markdown + fpdf2"""

    cleaned_md = _preprocess_markdown(md_text)
    body_html = _md_to_html(cleaned_md)
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "output.pdf")
        _create_pdf_with_html(body_html, output_path, title=title)
        if not Path(output_path).exists():
            return {"success": False, "error": "PDF 生成失败：fpdf2 未输出文件"}

        save_result = PdfFileHandler.save_temp(
            source_path=output_path,
            file_name=output_name or "document.pdf",
        )
        if css:
            save_result["warnings"] = ["当前 fpdf2 生成路径不支持自定义 CSS，已忽略 css 参数"]
        save_result["success"] = True
        return save_result
```

#### `html_to_pdf(html_text, output_name=None) -> Dict`

当前实现使用简化 HTML 解析 + `fpdf2` 渲染。支持常见标题、段落、表格；复杂 CSS、图片和深层嵌套结构会在质量验证阶段给出 warning，不承诺高保真。

```python
def html_to_pdf(html_text: str, output_name: str = None) -> Dict[str, Any]:
    """HTML → PDF，通过 fpdf2"""
```

#### `docx_to_pdf(file_path, output_name=None) -> Dict`

路径：仅使用 LibreOffice。

```python
def docx_to_pdf(file_path: str, output_name: str = None) -> Dict[str, Any]:
    """Word → PDF"""
    return _docx_to_pdf_via_libreoffice(file_path, output_name)
```

> **注意**：DOCX 转 PDF 不使用 Pandoc，避免额外 PDF 引擎依赖和版式差异。

### 5.6 pdf_merger.py — 合并/拆分

#### `merge_pdfs(file_paths, output_name=None) -> Dict`

使用 **PyMuPDF**：

```python
def merge_pdfs(file_paths: List[str], output_name: str = None) -> Dict[str, Any]:
    merged = fitz.open()
    for path in file_paths:
        doc = fitz.open(path)
        merged.insert_pdf(doc)
        doc.close()

    # 保存
    output_path = ...
    merged.save(output_path)
    merged.close()

    return {"success": True, "file_path": output_path, ...}
```

#### `split_pdf(file_path, ranges, output_name=None) -> Dict`

```python
def split_pdf(file_path: str, ranges: List[str]) -> Dict[str, Any]:
    """
    ranges: ["1-3", "5-7"] 表示提取第1-3页和第5-7页
    返回多个 PDF 文件
    """
    # PyMuPDF 实现
```

#### `extract_pages(file_path, pages, output_name=None) -> Dict`

```python
def extract_pages(file_path: str, pages: List[int]) -> Dict[str, Any]:
    """
    pages: [0, 2, 5] 提取第1、3、6页（0-indexed）
    """
    # PyMuPDF 实现
```

### 5.7 pdf_lib.py — 共享工具

```python
class PdfFileHandler:
    """PDF 文件操作工具"""

    @staticmethod
    def save_temp(source_path: str, file_name: str = None) -> Dict:
        """保存 PDF 到会话上传目录"""

    @staticmethod
    def resolve_path(file_path: str) -> str:
        """解析文件路径（相对路径 → 绝对路径）"""

    @staticmethod
    def get_page_count(file_path: str) -> int:
        """快速获取 PDF 页数"""

    @staticmethod
    def is_text_pdf(file_path: str, sample_pages: int = 3) -> bool:
        """判断 PDF 是文字型还是扫描件（采样前几页）"""
```

### 5.8 default.css — 内置样式

历史上用于 Pandoc + WeasyPrint 的默认 PDF 样式。当前 fpdf2 主路径不再依赖 CSS 文件，中文字体由 `pdf_writer._find_chinese_font()` 自动探测：

```css
@page {
    size: A4;
    margin: 2.5cm;
}

body {
    font-family: "SimSun", "Microsoft YaHei", "Noto Sans CJK SC", serif;
    font-size: 12pt;
    line-height: 1.6;
}

h1, h2, h3 {
    font-family: "SimHei", "Microsoft YaHei", sans-serif;
}

table {
    border-collapse: collapse;
    width: 100%;
}

th, td {
    border: 1px solid #333;
    padding: 6px 12px;
}

code {
    font-family: "Consolas", "Source Code Pro", monospace;
    background-color: #f5f5f5;
    padding: 2px 4px;
}
```

---

## 6. OCR 集成设计

PDF 工具的 OCR 功能**不重新实现**，直接调用已有的 `PaddleOCR` 工具：

```python
# pdf_process_tool.py 中的 _handle_ocr 方法
async def _handle_ocr(self, ctx: PipelineContext, params: Dict) -> Dict:
    if not ctx.file_paths:
        return {"success": False, "error": "ocr 操作需要 file_paths 参数"}

    file_path = ctx.file_paths[0]

    # 直接调用已有的 PaddleOCR 函数
    from src.tools.ocr.ocr_tool import paddleocr_doc_parsing
    result = paddleocr_doc_parsing(file_path=file_path, file_type=0)

    if result.get("success"):
        return {
            "success": True,
            "content": result["full_text"],
            "pages": result["texts"],
            "page_count": len(result["texts"]),
        }
    return result
```

---

## 7. 智能降级策略

```
用户请求 "读取这个 PDF"
  │
  ▼
pdf_reader.read_text()
  │
  ├─ 文本量充足 → 返回文本结果
  │
  └─ 文本量极少（< 50 字符）→ 可能是扫描件
      │
      ▼
    自动提示：该 PDF 可能是扫描件，建议使用 OCR 模式
    或自动降级到 OCR（取决于配置）
```

```
用户请求 "把这个 PDF 转成 Markdown"
  │
  ▼
pdf_to_md.convert_smart()
  │
  ├─ PyMuPDF4LLM 提取成功 → 返回 Markdown
  │
  └─ 提取为空 → 自动调用 PaddleOCR → 返回 OCR 结果
```

---

## 8. 路由 Prompt 完整设计

```
你是 PDF 文档处理工具的内部路由器。根据用户的请求和上下文，决定应该执行哪些操作。

## 可用操作

1. read - 读取PDF的文本内容（文字型PDF，使用文本提取）
2. read_tables - 提取PDF中的表格数据
3. ocr - OCR识别PDF内容（扫描件、图片型PDF）
4. pdf_to_md - 将PDF转换为Markdown格式
5. md_to_pdf - 将Markdown文本转换为PDF文件
6. html_to_pdf - 将HTML内容转换为PDF文件
7. docx_to_pdf - 将Word文档转换为PDF文件
8. merge - 合并多个PDF文件
9. split - 按页码范围拆分PDF
10. extract_pages - 提取PDF的指定页面

## 判断规则

- 有 PDF 文件且要求读取/查看/了解内容 → read
- 有 PDF 文件且要求提取表格 → read_tables
- 有 PDF 文件且明确要求 OCR 或识别扫描件 → ocr
- 有 PDF 文件且要求转为 Markdown → pdf_to_md
- context 中包含 Markdown 内容（# 标题、| 表格等）且要求生成 PDF → md_to_pdf
- context 中包含 HTML 内容且要求生成 PDF → html_to_pdf
- 有 .docx 文件且要求转为 PDF → docx_to_pdf
- 有多个 PDF 文件且要求合并 → merge
- 有 PDF 文件且要求拆分/按页提取 → split 或 extract_pages
- 不确定 PDF 是文字型还是扫描件 → 先 read，如果结果太少会自动提示用 ocr
- 操作可组合，如 "先读取内容再转 Markdown" → read,pdf_to_md

## 参数说明

- split 操作需要在 params 中提供 ranges，格式如 ["1-3", "5-7"]
- extract_pages 操作需要在 params 中提供 pages，格式如 [1, 3, 5]（页码，从1开始）
- merge 操作的文件来自 file_paths（多个文件路径）
- md_to_pdf / html_to_pdf 可在 params 中提供 title 和 output_name
- read 操作可在 params 中提供 pages，格式如 [1, 2, 3]（只读取指定页）

## 输出格式

严格输出 JSON：
{
  "task": "read",
  "params": {},
  "reason": "用户要求读取PDF内容"
}
```

---

## 9. Agent 注册

在 `src/core/agent.py` 的 `_register_builtin_tools()` 中注册：

```python
from src.tools.pdf.pdf_process_tool import PdfProcessTool
self.tool_registry.register(PdfProcessTool())
```

同时在 `AGENT_TOOLS` schema 列表中添加工具定义（与 Word 工具格式一致）。

---

## 10. 配置设计

```yaml
# configs/config.yaml
tools:
  pdf:
    # Pandoc 路径（复用 Word 工具的查找逻辑）
    pandoc_path: ""  # 空则自动查找
    # PDF 生成引擎：fpdf2（当前默认）| reportlab（计划增强）
    pdf_engine: "fpdf2"
    # 内置 CSS 样式路径（空则使用默认）
    default_css: ""
    # OCR 降级阈值（提取文本少于此字符数时自动提示 OCR）
    ocr_fallback_threshold: 50
    # PDF 转换超时（秒）
    convert_timeout: 60
```

---

## 11. 依赖安装

### 开发环境

```bash
pip install PyMuPDF4LLM
```

### Docker 环境

```dockerfile
# 在现有 Dockerfile 中添加
RUN pip install PyMuPDF4LLM

# 如果需要高质量排版（可选，增加约 1GB 镜像体积）
# RUN apt-get update && apt-get install -y texlive-xetex texlive-lang-chinese
```

---

## 12. 实施优先级

| 优先级 | 功能 | 依赖库 | 工作量 | 状态 |
|--------|------|--------|--------|------|
| P0 | 框架搭建（process_tool + router + lib） | 无 | 2天 | ✅ 已完成 |
| P0 | `read` — 读取 PDF 文本 | PyMuPDF（已有） | 0.5天 | ✅ 已完成 |
| P0 | `md_to_pdf` — Markdown 转 PDF | markdown + fpdf2 | 1天 | ✅ 已完成 |
| P1 | `pdf_to_md` — PDF 转 Markdown | PyMuPDF4LLM | 0.5天 | ✅ 已完成 |
| P1 | `ocr` — OCR 集成 | PaddleOCR（已有） | 0.5天 | ✅ 已完成 |
| P1 | `read_tables` — 表格提取 | pdfplumber（已有） | 0.5天 | ✅ 已完成 |
| P2 | `html_to_pdf` — HTML 转 PDF | 简化 HTML + fpdf2 | 0.5天 | ✅ 已完成 |
| P2 | `docx_to_pdf` — Word 转 PDF | LibreOffice | 0.5天 | ✅ 已完成 |
| P2 | `merge` / `split` / `extract_pages` | PyMuPDF（已有） | 1天 | ✅ 已完成 |
| P3 | 内置 CSS 样式调优 | 无 | 0.5天 | ✅ 已完成 |

**预计总工期：5-7 天**

---

## 13. 风险与注意事项

1. **复杂 HTML/CSS 高保真限制**：当前 `html_to_pdf` 使用 fpdf2 简化渲染，复杂 CSS、图片和深层嵌套结构不承诺还原。后续如需高保真需单独引入 WeasyPrint 或 Playwright print-to-pdf 路径。

2. **中文字体问题**：WeasyPrint 依赖系统字体。Docker 镜像需要安装中文字体包（`fonts-noto-cjk` 或 `fonts-wqy-zenhei`）。

3. **大文件处理**：PDF 转换可能耗时较长，需要设置合理的超时时间，并在 SSE 流中推送进度。

4. **Pandoc 使用边界**：PDF 工具不使用 Pandoc；Pandoc 仅供 Word 工具的 Markdown → DOCX 功能使用。

5. **OCR 工具的函数调用**：PDF 工具直接调用 `paddleocr_doc_parsing()` 函数（同步函数），在 async handler 中需使用 `asyncio.to_thread()` 包装。

6. **临时文件清理**：PDF 生成过程中会产生临时文件，需要在异常情况下确保清理。

---

## 14. 实施记录

### 第一阶段完成（2026-05-11）

已完成核心功能（P0-P2）的全部开发和单元测试：

- **框架搭建**：`pdf_process_tool.py`（主入口）、`pdf_router.py`（LLM 路由）、`pdf_lib.py`（共享工具）
- **10 个操作**：read、read_tables、ocr、pdf_to_md、md_to_pdf、html_to_pdf、docx_to_pdf、merge、split、extract_pages
- **智能降级**：pdf_to_md 自动判断文字型/扫描件，OCR 回退
- **内置 CSS**：`default.css` 中文字体配置
- **Agent 注册**：已在 `src/core/agent.py` 的 `_register_builtin_tools()` 中注册
- **单元测试**：94 个测试用例全部通过（`tests/unit/tools/test_pdf_tool.py`）

### v1.2 实现校准与质量验证增强（2026-06-30）

- **实现校准**：确认 `md_to_pdf/html_to_pdf` 当前主路径为 `fpdf2`，修正文档和测试中残留的 Pandoc/WeasyPrint 假设。
- **质量修复**：修复拆分 PDF 保存前关闭文档、无效页码静默成功、文件名安全清洗、HTML 表格顺序保持等问题。
- **质量验证增强**：新增 `inspect`、`render_pages`、`validate` 操作；生成型操作追加结构化校验结果。
- **测试更新**：PDF 工具相关单测扩展至 100 个并通过。

### 后续说明

- 当前 PDF 处理统一由 Agent 调用 `pdf_process` 工具完成。
