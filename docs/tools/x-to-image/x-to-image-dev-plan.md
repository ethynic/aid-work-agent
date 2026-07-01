# x-to-image 开发计划

> 创建日期：2026-07-01
> 关联设计：[x-to-image 服务设计文档](x-to-image-design.md)
> 状态：✅ v1 已完成开发（Phase 1-5 全部完成，2026-07-01）

---

## 1. 目标

按 [设计文档](x-to-image-design.md) 落地 `x-to-image` 服务 v1：将**文本 / Markdown / HTML** 转换为**一张尺寸可控的长图 PNG**，以 agent 工具形式暴露。**Playwright 强制 headless，输出写入临时目录并返回临时文件路径**（不走下载注册/Redis）。

**v1 范围**：文本 / Markdown / HTML 输入；单张长图输出；返回临时路径。PDF / Word 等留 v2。

## 2. 阶段划分

### Phase 1：搭建服务骨架（models / service / base / browser_pool）

状态：✅ 已完成（2026-07-01）

**目标**：建立 `src/services/x_to_image/` 包结构与核心服务壳，不含具体渲染逻辑。

待完成改动：
1. `src/services/x_to_image/models.py` —— `InputType`、`ImageFormat` 枚举，`XToImageInput`、`XToImageResult` dataclass（见设计 §5.3）。
2. `src/services/x_to_image/renderers/base.py` —— `ImageRendererBase`（async ABC，`name`、`render(inp, work_dir)`、`_shoot_full_page` 工具方法）。
3. `src/services/x_to_image/renderers/browser_pool.py` —— `BrowserPool` 单例：
   - 完全仿照 `WeComKfRenderer`（`src/channels/wecom_kf/renderer.py`）的懒加载 + `asyncio.Lock` + `is_available()` + `_ensure_browser()` + `_cleanup_browser()` + `close()`。
   - **`headless=True` 硬编码**（不暴露参数，不接收外部覆盖）。
   - 提供 `async shoot(html, width, out_dir) -> png_path`：`set_viewport_size` → `set_content(wait_until="networkidle", timeout=15000)` → `screenshot(full_page=True, type="png")`。
4. `src/services/x_to_image/service.py` —— `XToImageService`（`_renderers` 注册表 + `register_input_type` + `convert` 主流程：`tempfile.mkdtemp(prefix="x_to_image_")` 建工作目录 → 渲染 → `finalize_long_image`），底部 `x_to_image_service = XToImageService()`。
5. `src/services/x_to_image/__init__.py` 与 `renderers/__init__.py` 导出。
6. 在 `src/services/__init__.py` 追加导出 `XToImageService`、`x_to_image_service`、模型类。

测试（`tests/services/x_to_image/test_browser_pool.py`）：
- `is_available()` 返回 True/False 不抛异常。
- `shoot` 对简单 HTML 返回存在的 PNG（断言文件存在且非空）。
- 断言浏览器以 headless 启动（可通过日志或 mock `chromium.launch` 捕获 `headless=True` 参数）。

验收：
- 包结构建立，单例可 import；Playwright 不可用时 `convert` 优雅返回失败而非抛异常。

---

### Phase 2：实现三个渲染器（text / markdown / html）

状态：✅ 已完成（2026-07-01）

**目标**：实现文本、Markdown、HTML 三类输入的渲染（均走 headless Playwright）。

待完成改动：
1. `renderers/text_renderer.py` —— `TextRenderer`：文本转 `<pre style="white-space:pre-wrap;word-break:break-word;font-family:...">` + 基础 HTML 模板 → `browser_pool.shoot`。
2. `renderers/markdown_renderer.py` —— `MarkdownRenderer`：
   - `markdown.markdown(src, extensions=["tables", "fenced_code", "codehilite", "toc"])`。
   - 套样式模板（参考 `WeComKfRenderer.HTML_TEMPLATE`：表头底色、代码块底色、`body{margin:0;padding:16px}`、系统中文字体栈）。
   - 模板宽度由 `inp.width` 控制（viewport + CSS `body{width:{width}px}`）。
3. `renderers/html_renderer.py` —— `HtmlRenderer`：
   - `is_file_path=True` → `Path(source).read_text(encoding="utf-8")`（文件不存在返回失败）。
   - `is_file_path=False` → 直接用 `source`。
   - 不强制套模板（尊重用户自带样式）；仅保证 `body` 有最小 margin。
4. 在 `service._register_builtin` 注册三者。

测试（`tests/services/x_to_image/test_renderers.py`）：
- 纯文本含换行/缩进 → PNG 高度 > 0、宽度 ≈ `inp.width`、非空白。
- Markdown 含表格 + 代码块 → PNG 非空白、尺寸合理。
- HTML 字符串（自带 `<style>`）→ 正常渲染。
- HTML 文件路径（临时写一个 `.html`）→ 正常读取渲染；不存在路径 → 失败。

验收：
- 三类输入各产出有效（非空白、尺寸受控）PNG，均位于临时工作目录。

---

### Phase 3：实现 image_utils（拼接 / 空白检测 / 截断 / 体积控制）

状态：✅ 已完成（2026-07-01）

**目标**：实现 `finalize_long_image` 与辅助函数，保证输出「单张、可控」，写入临时工作目录。

待完成改动（`src/services/x_to_image/image_utils.py`）：
1. `_stitch_vertical(page_paths) -> Image` —— 纵向拼接（v1 通常单张，直接 `Image.open`；多张按最大宽度对齐 `paste`）。
2. `_is_blank(img) -> bool` —— `img.convert("L").getextrema()` 全白/全透明判定（参考 `HtmlExporter._validate_screenshots`）。
3. `_append_truncation_notice(img) -> Image` —— 底部 paste 一张高 40px、灰底白字「⚠ 内容已截断」提示条。
4. `finalize_long_image(page_paths, inp, work_dir, renderer_name) -> XToImageResult`：
   - 拼接 → 空白检测 → 高度截断（`> max_height` 裁剪 + 加提示，`truncated=True`）→ 存 PNG 到 `work_dir` → 超 `max_file_size_mb` 转 JPEG(quality=85)。
   - 文件名用 `inp.output_name` 或 uuid；`image_path` 返回 `out_path.absolute()`。

测试（`tests/services/x_to_image/test_image_utils.py`）：
- 单张图直接通过；多张图拼接后高度 = 各图高度之和、宽度 = 最大宽度。
- `_is_blank` 对纯白图返回 True、对正常图返回 False。
- 构造高度 25000px 的图，`max_height=20000` → 裁剪 + `truncated=True` + 底部有提示条。
- 构造大体积图，`max_file_size_mb=1` → 生成 `.jpg`。
- 输出路径位于传入的 `work_dir` 内。

验收：
- 拼接、截断、空白、体积四个分支均有用例覆盖；输出落临时目录。

---

### Phase 4：实现薄工具 + 注册

状态：✅ 已完成（2026-07-01）

**目标**：以 agent 工具形式暴露，返回临时文件路径。

待完成改动：
1. `src/tools/image/__init__.py` + `src/tools/image/x_to_image_tool.py`：
   - `XToImageInputModel`（Pydantic，见设计 §5.8；遵循 [入参契约](../../tool-input-contract-redesign.md) 的 `content/content_type/output_name` 约定）。
   - `XToImageTool(BaseTool)`：`name="x_to_image"`、`display_name="内容转图片"`、`category="image"`、`description`、`InputModel`。
   - `execute`：构造 `XToImageInput` → `await x_to_image_service.convert()` → 失败透传 → 成功返回临时路径 + 元信息 dict（`image_path`/`image_name`/`file_size`/`image_width`/`image_height`/`truncated`/`renderer`）。**不返回 download_url，不调 cp 注册。**
2. 在 `src/core/agent.py:_register_builtin_tools`（line 306 起）PDF 工具注册后追加：
   ```python
   from src.tools.image.x_to_image_tool import XToImageTool
   self.tool_registry.register(XToImageTool())
   ```

测试（`tests/tools/image/test_x_to_image_tool.py`）：
- mock `x_to_image_service.convert` 返回成功 → 断言 `execute` 返回 `success=True` 且含 `image_path`、**不含 `download_url`**。
- mock 返回失败 → 断言 `success=False` 且无 `image_path`。
- `to_tool_definition()` 生成合法 JSON schema。
- `validate_parameters` 对缺 `content` 报缺失。

验收：
- 工具可被 `tool_registry.get_tool("x_to_image")` 取到；schema 合法；端到端在 agent 中可被 LLM 调用并产出临时图片路径。

---

### Phase 5：端到端集成测试 + 文档登记

状态：✅ 已完成（2026-07-01）

待完成改动：
1. `tests/services/x_to_image/test_integration.py`：文本/MD/HTML 各一条真实样例，端到端调 `x_to_image_service.convert`，断言：
   - `success=True`
   - `image_path` 存在且非空，**位于临时目录**（`tempfile.gettempdir()` 之下）
   - `height <= max_height`、`file_size > 0`
2. 更新 `docs/ideas.md` 的 `## 工具` 条目 33 状态为 `🔧 部分完成`，说明补「v1 完成」。
3. 更新本开发计划各 Phase 状态为已完成，补「已完成改动」要点。

验收：
- 集成测试通过；ideas.md 状态更新；全量相关单测 `pytest tests/services/x_to_image/ tests/tools/image/ -v` 通过。

---

## 3. 里程碑与优先级

| Phase | 工作量估算 | 优先级 | 前置依赖 |
|-------|-----------|--------|----------|
| 1 服务骨架 | 1d | P0 | 无 |
| 2 渲染器 | 1.5d | P0 | 1 |
| 3 image_utils | 1d | P0 | 1 |
| 4 工具 + 注册 | 0.5d | P0 | 2、3 |
| 5 集成 + 登记 | 0.5d | P0 | 4 |

**合计约 4.5 人天**（相比 v1.0 移除了 Phase 0 的 cp 重构，工作量 -0.5d）。Phase 2 与 3 在 Phase 1 完成后可并行。

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| Playwright 在测试环境缺失 | 单测对 `browser_pool` 做 mock；集成测试标记 `@pytest.mark.integration`，CI 需装 Chromium |
| 临时文件无自动清理导致膨胀 | v1 不引入清理任务（与项目现状一致）；§7 已记录为已知限制，后续统一规划 temp 清理调度 |
| Markdown 样式在不同渠道预览差异 | v1 统一 PNG 位图，规避渠道 Markdown 渲染差异；样式以「白底、系统中文字体、清晰表格」为基调 |
| 长图在某些 IM 有尺寸上限 | `max_height`/`max_file_size_mb` 默认值保守；后续可按渠道调参 |
| 调用方拿到临时路径后未及时消费 | image_path 非持久承诺，调用方应及时使用（发送/转存）；文档中注明 |
