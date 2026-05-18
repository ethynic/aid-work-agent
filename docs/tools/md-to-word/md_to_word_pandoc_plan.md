# md_to_word 重构计划：改用 Pandoc 引擎

## Context

当前 `src/tools/word/md_to_word.py` 用手写的正则解析器将 Markdown 转 Word，存在两个核心问题：
1. **解析能力有限**：复杂 Markdown（嵌套列表、不规范的表格等）经常渲染异常
2. **LLM 输出不规范**：Agent 场景下 Markdown 来自 LLM，常有字面量 `\n`、缺少空行、表格缺分隔行等问题

实验证实 Pandoc 转换效果更好。本计划将转换引擎替换为 Pandoc，同时增加 Markdown 规范化处理和 Docker 兼容。

## 架构设计

```
转换流程:
  md_text → normalize_markdown() → 临时 .md 文件 → pandoc subprocess → 临时 .docx
          → python-docx 打开 → 设置 CJK 字体 → Document（内存中）
          → save_as() → WordFileHandler.save_temp() → _get_tenant_upload_dir()

注意：临时 .md/.docx 文件仅用于 Pandoc 中间转换，用 tempfile 自动清理。
最终文件保存位置由 save_as() → _get_tenant_upload_dir() 决定：
  - 有租户有用户：storage/uploads/{tenant_id}/{user_id}/
  - 有租户无用户：storage/uploads/{tenant_id}/
  - 无租户有用户：storage/uploads/{user_id}/
  - 无租户无用户：storage/uploads/conversation/
```

**设计决策**：
- 使用 **Pandoc 默认样式** + `--reference-doc` 自定义 .docx 模板。删除旧的 JSON 模板系统
- `template` 参数语义变更：接受 `.docx` 文件路径（传给 Pandoc `--reference-doc`），传入旧名称（如 `"default"`）时忽略
- 仅做一项后处理：**设置 CJK eastAsia 字体**（Pandoc 不设置 `w:rFonts w:eastAsia`）

**保持不变**的公共 API 签名：
- `convert(md_text, template, title, author) -> Document`
- `convert_file(md_path, template, title, author) -> Document`
- `save_as(doc, file_name, output_dir) -> Dict`

**需要小幅修改的调用方**：
- `src/api/word.py`：`MdToDocxRequest.template` 字段说明改为"可选 .docx 模板文件路径"
- `src/tools/word/word_process_tool.py`：无改动（透传 template 参数）
- `tests/unit/tools/test_word_tool.py`：`test_convert_invalid_template` 改为 `test_convert_without_template`

## 修改文件清单

| 文件 | 改动 |
|------|------|
| `src/tools/word/md_to_word.py` | 重写：Pandoc 引擎 + 规范化 + CJK 字体后处理 + `--reference-doc` 支持，删除旧代码 |
| `Dockerfile` | 运行阶段 `apt-get install` 增加 `pandoc` |
| `src/api/word.py` | `MdToDocxRequest.template` 字段说明更新 |
| `tests/unit/tools/test_word_tool.py` | 适配新实现，新增规范化测试 |

## 详细设计

### 1. Markdown 规范化 `normalize_markdown()`

LLM 输出的常见问题及修复策略，按流水线依次处理：

| 步骤 | 函数 | 处理内容 |
|------|------|----------|
| 1 | `_fix_literal_newlines` | 字面量 `\n`（反斜杠+n 两字符）→ 真正换行符（跳过代码块内） |
| 2 | `_ensure_block_spacing` | 标题/代码块/表格/分隔线前后确保有空行 |
| 3 | `_fix_tables` | 表格缺分隔行时自动补齐；列数不一致时补空单元格 |
| 4 | `_close_code_fences` | 未闭合的代码块自动补上 ``` |
| 5 | `_preprocess_for_pandoc` | `---/===` 分隔线 → `\newpage`（pandoc 分页语法）；移除控制字符 |

### 2. Pandoc 调用 `_pandoc_convert()`

```python
cmd = [
    "pandoc",
    "-f", "markdown+pipe_tables+raw_html+autolink_bare_uris",
    "-t", "docx",
    "--wrap=none",
]
# 支持 --reference-doc 自定义模板
if reference_doc and Path(reference_doc).exists():
    cmd.extend(["--reference-doc", str(reference_doc)])
cmd.extend(["-o", str(docx_path), str(md_path)])

subprocess.run(cmd, capture_output=True, text=True, timeout=30)
```

- 临时文件用 `tempfile.TemporaryDirectory`，自动清理
- 30 秒超时防止卡死
- Pandoc 失败时抛 `RuntimeError`
- `--reference-doc`：传入 .docx 文件路径时使用，不传则用 Pandoc 默认样式

### 3. CJK 字体后处理 `_ensure_cjk_fonts()`

Pandoc 生成的文档不设置 `w:rFonts w:eastAsia` 属性，导致中文字符可能渲染异常。后处理遍历所有 run，补上 eastAsia 字体：

```python
def _ensure_cjk_fonts(doc: Document) -> None:
    CJK_FONT = "SimSun"
    for para in doc.paragraphs:
        for run in para.runs:
            rPr = run._element.get_or_add_rPr()
            rFonts = rPr.find(qn('w:rFonts'))
            if rFonts is None:
                rFonts = OxmlElement('w:rFonts')
                rPr.insert(0, rFonts)
            if rFonts.get(qn('w:eastAsia')) is None:
                rFonts.set(qn('w:eastAsia'), CJK_FONT)
    # 表格内的 run 同样处理
    for table in doc.tables:
        ...
```

### 4. Dockerfile 改动

在运行阶段 `apt-get install` 列表中，`fonts-noto-cjk` 后面增加 `pandoc`。Debian trixie 的 pandoc 包版本为 3.x，约 50-80MB。

### 5. 分隔线语义

当前代码把 `---` 转为**分页符**（page break），Pandoc 默认把 `---` 渲染为水平线。在规范化步骤中将 `---/===` 替换为 `\newpage`（Pandoc 的分页语法），保持分页行为一致。

### 6. `template` 参数处理

`convert(md_text, template=...)` 中的 `template` 参数：

- **传入 `.docx` 文件路径**：通过 Pandoc `--reference-doc` 使用自定义样式模板
- **传入非路径字符串（如旧名称 `"default"`）**：忽略，使用 Pandoc 默认样式
- **传入 `None`**：使用 Pandoc 默认样式

判断逻辑：`template` 值以 `.docx` 结尾且文件存在时，视为 reference-doc；否则忽略。

### 7. 删除的代码

旧的 `md_to_word.py` 中以下内容将被移除（不再需要）：
- `markdown_to_doc()` 及所有内部函数（`_add_heading`、`_add_rich_paragraph`、`_add_list_item`、`_add_code_paragraph`、`_extract_table`、`_add_table`、`_auto_fit_columns`、`_fill_cell`、`_balance_formatting_around_br`、`_get_unclosed_markers`、`_add_rich_text_to_paragraph`）
- 对 `template_manager.get_template()` 的依赖
- 对 `word_lib.resolve_font_name`、`resolve_alignment`、`parse_color`、`PAGE_SIZES` 的依赖

## 文件结构

重写后的 `md_to_word.py` 结构（约 200 行，原 578 行）：

```
Section A: 公共 API（~30 行）
  - convert()
  - convert_file()
  - save_as()

Section B: Markdown 规范化（~120 行）
  - normalize_markdown()
  - _fix_literal_newlines()
  - _ensure_block_spacing()
  - _fix_tables()
  - _close_code_fences()
  - _preprocess_for_pandoc()

Section C: Pandoc 转换（~30 行）
  - _pandoc_convert()

Section D: CJK 字体后处理（~20 行）
  - _ensure_cjk_fonts()
```

## 测试计划

### 现有测试调整
- `test_convert_with_headings`、`test_convert_with_table`、`test_convert_with_code_block`、`test_convert_with_list`：**应通过**（公共 API 不变）
- `test_convert_invalid_template`：**需修改**，旧名称不再抛 ValueError，改为验证转换成功（template 被忽略）
- `TestWordToMd.test_convert_success`：**应通过**（往返测试）

### 新增测试
| 测试 | 验证点 |
|------|--------|
| `test_normalize_literal_newlines` | `\n` 字面量 → 真换行 |
| `test_normalize_unclosed_code_block` | 未闭合代码块自动补 ``` |
| `test_normalize_missing_table_separator` | 表格缺分隔行自动补齐 |
| `test_normalize_block_spacing` | 标题前无空行时自动补 |
| `test_pandoc_convert_basic` | 基本的 md→docx 转换验证 |
| `test_pandoc_with_reference_doc` | 传入 .docx reference-doc 时的样式继承 |
| `test_real_llm_output` | 用 test.md 的内容（含 `\n`）验证完整流程 |

## 风险与应对

| 风险 | 应对 |
|------|------|
| Docker 镜像增大 ~50MB | 可接受，pandoc 是成熟稳定的工具 |
| `<br>` 处理差异 | Pandoc 将 `<br>` 转为 Word 行内换行（w:br），比当前多段落方式更正确 |
| CJK 字体缺失 eastAsia 属性 | `_ensure_cjk_fonts` 显式设置 |
| Windows 开发环境 | 需手动安装 Pandoc（已安装）；部署环境通过 Dockerfile 确保 |
| 旧 JSON 模板不再生效 | template 参数传入旧名称时静默忽略；需要自定义样式时传入 .docx 文件路径 |
