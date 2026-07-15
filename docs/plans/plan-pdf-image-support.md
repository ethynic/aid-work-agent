# 开发计划：PDF 工具图片支持 + 工具优化（#34 Phase 3b）

> 配套设计：[`docs/tools/pdf/pdf-image-support-design.md`](../tools/pdf/pdf-image-support-design.md)

## 任务拆分

### Part A — 图片支持

- [x] **A3** `_image_inliner.py` 扩展 HTML `<img>` 解析（`_HTML_IMG_FILE_ID_PATTERN` / `_HTML_IMG_REMOTE_PATTERN` + `inline_images` syntax 分支）
- [x] **A4** `pdf_writer.py` 双引擎渲染本地图
  - [x] `_embed_local_images_as_data_uri`（Playwright 路径，本地 src→base64 data URI）
  - [x] `_render_image_fpdf` + `_render_html_content` `<img>` 处理（fpdf2 兜底）
  - [x] Playwright 路径在 `_prepare_print_html` 后接入 data URI 转换
- [x] **A1** `PdfProcessTool` 接入 tenant/user（`set_tenant_id/set_user_id` + `_resolve_tenant_user`，镜像 Word）
- [x] **A2** `_handle_md_to_pdf` / `_handle_html_to_pdf` 在生成前 `inline_images`（`if tenant_id:` 守卫，html 用 `syntax="html"`）

### Part B — 优化

- [x] **B1** 落盘闭环：`_merge_results` read/ocr（limit 2000）/ pdf_to_md（limit 5000）超长字段 `_spill_text_field` → `spill_large_content`，返回 `file_path + truncated + full_size`
- [x] **B2** 全规范核对：`execute()` / `_resolve_task` / `pdf_writer` 异常分支 `str(e)` → `sanitize_error`

### 测试

- [x] `test_image_inliner.py` 追加 HTML syntax 用例（file_id 命中/找不到、远程 URL、单引号 src）
- [x] `test_pdf_image_support.py` 新增：data URI 转换（本地/远程data保留/缺文件）、handler 集成（md/html/tenant=None 兼容/inline 失败回退）、落盘闭环（read/markdown/小内容不落盘）、错误脱敏

### 文档与登记

- [x] 新增 `docs/tools/pdf/pdf-image-support-design.md`
- [x] 新增 `docs/plans/plan-pdf-image-support.md`（本文件）
- [x] 更新 `docs/ideas.md` #29（图片支持 + Phase 3b + 全规范核对）/ #34（Phase 3b pdf 落盘闭环 ✅）
- [x] 更新 `docs/plans/plan-image-asset-pipeline.md` Phase 3「pdf_process 接入」标记为已兑现

### 三智能体流程

- [x] 开发自测（21 新增 + 135 PDF 回归全绿；word_to_md 单测因宿主机缺 `markitdown` 失败，属环境问题，非本次回归）
- [ ] 测试智能体：回归 + 启动安全检查
- [ ] CodeReview 智能体：P0/P1 必修
- [ ] 主控者 import 终检

## 验证记录

- `tests/unit/tools/test_pdf_image_support.py` + `test_image_inliner.py`：21 passed
- `tests/unit/tools/test_pdf_p0_contracts.py` + `test_pdf_tool.py` + `test_pdf_p1_contracts.py` + `test_pdf_validation.py`：135 passed, 2 skipped
- import 终检：`PdfProcessTool` 具 `set_tenant_id/set_user_id/_resolve_tenant_user`，`inline_images` 具 `syntax` 参数，`_embed_local_images_as_data_uri` 可导入 —— 通过
