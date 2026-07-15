# PDF 工具图片支持 + 工具优化（#34 Phase 3b）设计

> 配套开发计划：[`plan-pdf-image-support.md`](../../plans/plan-pdf-image-support.md)
>
> 关联：image-asset 管线（`docs/plans/plan-image-asset-pipeline.md`）Phase 3「pdf_process inliner 接入」；
> 工具总体优化（`docs/tools/tool-overall-optimization-dev-plan.md`）Phase 3b 落盘闭环。

## 1. 背景

Word 工具已通过 `image_inliner` 支持生成带图片的 docx（Pandoc 原生嵌图）。PDF 工具生成 PDF 时图片不显示，原因有二：

1. **从不解析图片引用**：`pdf_writer` 的 `md_to_pdf` / `html_to_pdf` 直接把 `![alt](file_id:file_xxx)`、`<img src="file_id:file_xxx">` 当普通文本，未解析为本地图。
2. **渲染引擎渲染不了本地图**：
   - Playwright 用 `page.set_content()` 以 `about:blank` 为基址，本地 `<img src="C:\...">` 加载不到。
   - fpdf2 的 `_render_html_content` 只处理 h1~h6 / p / 兜底 `<img>`，markdown 图片经 markdown 库变成 `<p><img/></p>` 后被 `_strip_html_tags` 丢弃。

image-asset 设计文档早已把「PDF inliner 接入」列为 **Phase 3** 待办（旅游顾问走 Markdown，Phase 1 只做 Word）。本次兑现，同时按 #34 计划完成 PDF 剩余的 Phase 3b 落盘闭环并核对全规范。

## 2. 目标与非目标

**目标**
- `md_to_pdf` + `html_to_pdf` 都支持图片：Markdown `![](file_id:…)` / HTML `<img src="file_id:…">`、远程 URL 均解析为本地图并在 PDF 中可见（Playwright 主路径 + fpdf2 兜底路径都要可见）。
- `_merge_results` 超长字段（read/ocr 的 content、pdf_to_md 的 markdown）走 `spill_large_content` 落盘，返回 `file_path + truncated`，agent 能 read/grep 回读。
- 错误返回不再泄漏 `str(e)`（接 `sanitize_error`）。

**非目标**
- 不改 PDF 工具的核心功能语义（其它 17 种操作不动）。
- 不引入新的图片来源（只复用已有 `image_inliner` + `ImageRegistry`）。
- fpdf2 图片渲染为「兜底可用」：宽度=内容宽、保持比例、单图失败不阻断；不追求版式精度。
- 不写依赖真实 Playwright/Chromium 的 e2e（容器内手工验证）。

## 3. 方案

### 3.1 复用既有机制（不重复造轮子）

| 能力 | 位置 |
|------|------|
| 图片引用解析 | `src/tools/_image_inliner.py::inline_images`（本次扩展 HTML） |
| 图片→本地路径 | `src/core/image_asset.py::ImageRegistry`（get_ref_by_file_id / fetch_to_local / resolve_local_path） |
| tenant/user 注入 | `src/core/agent.py` 主循环 `hasattr(tool,'set_tenant_id')` 通用注入 |
| ContextVar 兜底 | `src/saas/context.py::get_current_tenant_id / get_current_user_id` |
| 落盘闭环 | `src/tools/_spill.py::spill_large_content`（http_api 为参考实现） |
| 截断/脱敏 | `src/tools/_helpers.py::truncate_text / sanitize_error` |

标杆：Word 的图片链路 `word_process_tool.py::_handle_md_to_word`（双轨取 tenant_id → `convert_async` → `inline_images`）是 PDF 的直接模板。

### 3.2 图片支持（Part A）

**A1 `PdfProcessTool` 接入 tenant/user**（`pdf_process_tool.py`）
- `__init__` 加 `_tenant_id` / `_user_id`，加 `set_tenant_id` / `set_user_id`（与 `WordProcessTool` 一致）。Agent 主循环已通过 `hasattr` 通用注入，自动接通。
- 新增 `_resolve_tenant_user()`：注入优先，`get_current_tenant_id()` 兜底。

**A2 handler 先 inline 再生成**（`_handle_md_to_pdf` / `_handle_html_to_pdf`）
- 在拿到正文后、调生成函数前，`if tenant_id:` 守卫下调 `inline_images`（md 走默认 markdown 语法，html 走 `syntax="html"`）。
- `if tenant_id:` 守卫保证 tenant 缺省时行为不变（`test_html_to_pdf_pipeline_passes_css_for_explicit_warning` 等契约测试仍绿）。
- `inline_images` 抛异常时记 warning 回退原文，不阻断生成。

**A3 `_image_inliner.py` 扩展 HTML `<img>` 解析**
- 新增 `_HTML_IMG_FILE_ID_PATTERN` / `_HTML_IMG_REMOTE_PATTERN`（捕获 `<img … src=引号` + 引号 + src 值，`\2` 反向引用保证引号配对）。
- `inline_images` 按 `syntax` 分支：markdown 不变；html 仅替换 `<img>` 的 src 值，保留标签其余属性。复用 `_are_sub` 并发框架与 file_id/远程解析逻辑。单图失败保留原 `<img>`。

**A4 `pdf_writer.py` 渲染本地图（两引擎）**

到此步 HTML 中已是本地绝对路径（A2/A3 注入）。

- **Playwright**（主路径）：新增 `_embed_local_images_as_data_uri(html)`，把指向本地文件的 `<img src>` 读出 base64 编码替换为 `data:<mime>;base64,…`。src 已是 http(s)/data:/mailto: 的不动；文件不存在/读取失败保留原 src。在 `_html_to_pdf_via_playwright` 的 `_prepare_print_html` 之后、`set_content` 之前调用。data URI 绕开 `about:blank` 基址问题，最稳。
- **fpdf2**（兜底）：`_render_html_content` 顶部加「独立 `<img>` 直接渲染」分支；`<p>` 分支检测 inner 含 `<img>` 时，先渲染残余纯文本（若有），再对每张图调 `_render_image_fpdf(pdf, src)`（`pdf.image(path, w=pdf.epw, new_x="LMARGIN", new_y="NEXT")`，宽度=内容宽保持比例）。远程/data URI 在 fpdf2 路径跳过（无法读本地）。
- 共用 `_resolve_local_src(src)`（去 `file://` 前缀，远程/data: 返回 None）与 `_extract_img_srcs(html)`。

### 3.3 落盘闭环 + 全规范核对（Part B）

**B1 落盘闭环**（`_merge_results`，#34 Phase 3b）
- 新增 `_spill_text_field(value, limit, prefix, suffix)`：超长则全文落盘、in-result 预览按 `limit` 自行截断（`spill_large_content` 内部固定 PREVIEW_LIMIT=5000，而 read/ocr 预览上限是 2000，故预览单独截；落盘文件始终是全文）。
- read/ocr（limit 2000，prefix `pdf_read_`/`pdf_ocr_`，.txt）、pdf_to_md（limit 5000，prefix `pdf_to_md_`，.md）超长时返回 `{*_truncated, *_file_path, *_full_size}`，消除「截断即丢弃」的信息黑洞。

**B2 全规范核对**（按 `docs/tools/tool-development-spec.md`）
- description：已 ≤80 字符（Phase 2b 完成），不动。
- usage_guide：已集中、与 schema 不重复，不动。
- **错误脱敏**：`execute()` 通用异常分支、`_resolve_task` 路由异常、`pdf_writer` 的 md_to_pdf / Playwright / fpdf2 异常分支，原 `f"…: {e}"` 泄漏 `str(e)`，统一改 `sanitize_error(e, fallback=…)`。`FileNotFoundError`（`文件不存在:路径`，受控消息）保持透传。Playwright 回退 warning 去掉内嵌原始 error。
- echo 输入：`_merge_results` 各分支只返回新信息（file_path/size/count…），无 echo，不动。

## 4. 影响范围

| 文件 | 改动 |
|------|------|
| `src/tools/_image_inliner.py` | 新增 HTML 正则 + `inline_images` syntax 分支；模块/docstring 更新 |
| `src/tools/pdf/pdf_writer.py` | 新增 `_embed_local_images_as_data_uri` / `_render_image_fpdf` / `_resolve_local_src` / `_extract_img_srcs`；Playwright 路径接入；fpdf2 `_render_html_content` 接 `<img>`；错误分支接 sanitize_error |
| `src/tools/pdf/pdf_process_tool.py` | A1 tenant 注入 + `_resolve_tenant_user`；A2 两个 handler 接 inline_images；B1 `_spill_text_field` + read/ocr/pdf_to_md 落盘；B2 错误脱敏 |
| `tests/unit/tools/test_image_inliner.py` | 追加 HTML syntax 用例 |
| `tests/unit/tools/test_pdf_image_support.py` | 新增：data URI 转换、handler 集成、落盘闭环、错误脱敏 |

无前端改动、无配置改动、无 DB 变更。

## 5. 验证

- 单测：`tests/unit/tools/test_pdf_image_support.py`、`test_image_inliner.py`、`test_pdf_p0_contracts.py`、`test_pdf_tool.py` 全绿（共 156 passed）。
- import 终检：`from src.tools.pdf.pdf_process_tool import PdfProcessTool` 等关键符号无循环导入。
- 容器内手工端到端：register 一张本地图拿 file_id，构造含 `![图](file_id:xxx)` 的 Markdown 经 `PdfProcessTool().execute(...)`（注入 tenant_id）生成 PDF，fitz 渲染确认图片可见；Playwright 不可用时强制 fpdf2 再验一次。
- 按三智能体流程：开发自测 → 测试智能体回归 + 启动安全检查 → CodeReview。

## 6. 已知限制

- fpdf2 兜底路径图片渲染为「宽度=内容宽」的简化版，不保证版式精度（Playwright 主路径保真）。
- 混合「文字 + 图片」同一段落在 fpdf2 路径下，文字与图片按先文字后图片顺序排布。
- 远程图片在 fpdf2 路径下不渲染（fpdf2 不读 URL）；Playwright 路径下浏览器可加载远程。
