# x-to-image 服务设计文档

> 创建日期：2026-07-01
> 状态：设计完成，待审核（v1.1，按反馈调整：强制 headless + 输出走临时目录）
> 关联模块：`src/services/x_to_image/`、`src/tools/image/`
> 关联现有能力：`WeComKfRenderer`（Playwright 元素截图）、`pdf_renderer.render_pages`、`pdf_writer.docx_to_pdf`

---

## 1. 背景

企业智能体在多渠道（企业微信、钉钉、飞书、Web）交互中，常常需要把「文本 / Markdown / HTML / 文档」这类**非图片内容**转成**一张图片**交付给用户，典型诉求：

- 富文本/表格/样式化内容在 IM 气泡里排版丢失，转成长图更清晰；
- 报告、报价、内容卡需要可转发的视觉化形态；
- 部分 IM 渠道不支持 Markdown 渲染，图片是唯一稳定呈现方式。

目前项目里已有零散的「渲染为图片」能力（`WeComKfRenderer` 渲染表格 PNG、`HtmlExporter` 渲染 PPT 幻灯片、`pdf_renderer.render_pages` 渲染 PDF 页），但**没有一个统一、面向任意输入、输出可控单张长图**的入口。

本设计提出 `x-to-image` 服务：给定某个 `x`（文本 / Markdown / HTML，未来扩展 PDF / Word），智能选择渲染路径，输出**一张尺寸可控的长图 PNG**。

## 2. 设计目标

| 目标 | 说明 |
|------|------|
| **统一入口** | 一个服务 `convert(x, content_type) → 单张长图`，屏蔽底层渲染差异 |
| **始终单张长图** | 无论输入是文本、Markdown、HTML 还是（未来）多页文档，输出恒为**一张**纵向图片 |
| **尺寸可控** | 长图高度/体积有上限，超限自动截断并附加提示，杜绝不可控的超大图 |
| **强制 headless** | Playwright Chromium **必须** headless 模式运行（内容均为本系统自生成，无反爬/登录需求） |
| **输出临时文件** | 长图写入系统临时目录，返回临时文件绝对路径，不涉及下载注册/Redis |
| **复用现有依赖** | 零新增重型依赖（playwright / Pillow / markdown / PyMuPDF 均已在 `requirements.txt`） |
| **可扩展** | 新增输入类型只需实现一个 `ImageRendererBase` 子类并 `register`，不改服务主体 |
| **可复用** | 核心逻辑放在 `src/services/`，非 agent 路径（如渠道渲染器）也能直接调用 |

## 3. 约束与需求

### 3.1 功能性需求（v1）

| 编号 | 需求 | 优先级 |
|------|------|--------|
| F1 | 输入为**纯文本**，按可读样式（等宽/衬线、自动换行）渲染为长图 | P0 |
| F2 | 输入为 **Markdown**（含表格、代码块、列表），渲染为带样式的长图 | P0 |
| F3 | 输入为 **HTML 字符串或 .html 文件**，渲染为长图 | P0 |
| F4 | 输出恒为**单张纵向长图**（PNG），写入系统临时目录 | P0 |
| F5 | 长图尺寸/体积超限时截断，并在底部附加「⚠ 内容已截断」提示 | P0 |
| F6 | 返回临时文件绝对路径（及宽高/体积/是否截断等元信息） | P0 |
| F7 | 支持自定义宽度、背景色（轻量） | P1 |

### 3.2 非功能性需求

- **N1 性能**：单次渲染典型 ≤ 5s（Playwright 启动已有单例浏览器复用）。
- **N2 headless 强制**：浏览器**恒为** `headless=True`（代码层硬编码，不接受外部参数覆盖），与 `WeComKfRenderer` 一致。
- **N3 并发安全**：Playwright 单例浏览器在 `asyncio.Lock` 保护下串行使用页面（与 `WeComKfRenderer` 一致）；多 worker 下浏览器各自独立。
- **N4 稳定性**：Playwright 不可用时必须有**优雅降级**（明确报错，不崩溃进程）。
- **N5 可预测性**：渲染参数（宽度、DPI、上限）有合理默认值，避免 LLM 误传超大值。

### 3.3 不在本期范围（v2+）

- PDF / Word / Excel / PPT 输入（架构已预留扩展点，见 §8）。
- URL 网页截图（已由 `browser_screenshot` 工具覆盖）。
- 长图按宽度智能分列、瀑布流排版等高级排版。
- 临时文件的定时清理调度（见 §7）。

## 4. 技术选型

### 4.1 渲染引擎：Playwright（headless）

- **文本 / Markdown / HTML** → 统一先得到一段 HTML（文本用 `<pre>` 包裹、Markdown 用 `markdown` 库转 HTML），再由 Playwright `page.set_content` + `page.screenshot(full_page=True)` 得到**全页长截图**。
- 这是生成「一张长图」最自然、保真度最高的方式，且 `WeComKfRenderer` 已验证该路径稳定。
- **浏览器强制 headless**：内容均为本系统自生成，不存在反爬虫检测或需要可视交互的场景，headless 是唯一正确选择（也避免有头模式在容器/服务器环境无显示器的崩溃问题）。
- 浏览器实例采用**异步单例 + 懒加载 + Lock 串行**，与 `WeComKfRenderer` 完全一致（`src/channels/wecom_kf/renderer.py:81`）。

### 4.2 拼接 / 后处理：Pillow（已在依赖）

- 即便 v1 的文本/MD/HTML 通常只产生**一张**全页截图，服务仍统一经过「页面图列表 → 纵向拼接 → 单张长图」管线。
- 这样未来接入 PDF/Word（产生多张页图）时，拼接逻辑天然复用，服务主体不变。
- Pillow 同时承担：**空白页检测**（参考 `HtmlExporter._validate_screenshots` 的 `getextrema`）、**尺寸/体积控制**、**格式转换**。

### 4.3 临时目录：tempfile（标准库）

- 长图写入系统临时目录，每次渲染用 `tempfile.mkdtemp(prefix="x_to_image_")` 建一个独立工作目录，中间页图与最终长图都落在其中。
- 落在进程 `TMPDIR`：Docker 中为 `/home/appuser/tmp`（`Dockerfile:76`），本地开发为 OS 默认（Windows `%TEMP%` / Linux `/tmp`）。
- 仅返回最终长图的**绝对路径**，不注册到下载系统、不写 Redis。

### 4.4 依赖清单（零新增）

| 库 | 版本 | 用途 | 状态 |
|----|------|------|------|
| `playwright` | >=1.59.0 | HTML → 全页长截图（headless Chromium） | 已在 requirements |
| `Pillow` | >=10.0.0 | 纵向拼接、空白检测、尺寸控制 | 已在 requirements |
| `markdown` | >=3.5.0 | Markdown → HTML（含 tables/fenced_code 扩展） | 已在 requirements |
| `tempfile` | 标准库 | 临时工作目录 | 无需安装 |

> Dockerfile 已安装 Chromium 运行时依赖与字体（`fonts-noto-cjk`），无需改动。

## 5. 方案设计

### 5.1 总体架构

```
                     ┌─────────────────────────────────────────┐
   调用方             │  XToImageTool (BaseTool, 薄包装)         │
  (agent / channel) ─▶│  - 解析 InputModel                       │
                     │  - 调 service.convert()                  │
                     │  - 返回临时文件路径 + 元信息              │
                     └───────────────────┬─────────────────────┘
                                         │
                    ┌────────────────────▼────────────────────┐
                    │  XToImageService（src/services/）        │
                    │  - register_input_type(type, renderer)   │
                    │  - convert(input) → XToImageResult       │
                    │     1. 建 mkdtemp 临时工作目录            │
                    │     2. 按 content_type 选 renderer        │
                    │     3. renderer.render() → [页图路径]     │
                    │     4. image_utils.stitch_vertical()      │
                    │     5. 截断/尺寸控制 → 单张长图            │
                    └────────────────────┬────────────────────┘
                                         │ 注册
              ┌──────────────┬───────────┴───────────┬──────────────┐
              ▼              ▼                       ▼              ▼
        TextRenderer   MarkdownRenderer       HtmlRenderer    (future)
        (Playwright)   (md→html→Playwright)   (Playwright)   PdfRenderer ...
              └──────────────┴───────────┬───────────┘
                                         ▼
                              ImageRendererBase
                              - Playwright 单例浏览器（async, headless）
                              - page.set_content + full_page screenshot
```

### 5.2 目录结构

```
src/services/x_to_image/
├── __init__.py              # 导出 XToImageService, 单例 x_to_image_service, 模型类
├── models.py                # InputType / ImageFormat 枚举, XToImageInput, XToImageResult
├── service.py               # XToImageService（注册表 + convert 主流程）
├── image_utils.py           # stitch_vertical / is_blank / enforce_limits（Pillow）
└── renderers/
    ├── __init__.py
    ├── base.py              # ImageRendererBase（async ABC）
    ├── browser_pool.py      # Playwright 异步单例浏览器（headless, 懒加载 + Lock）
    ├── text_renderer.py     # 纯文本 → <pre> HTML → 截图
    ├── markdown_renderer.py # Markdown → HTML → 截图
    └── html_renderer.py     # HTML 字符串 / .html 文件 → 截图

src/tools/image/
├── __init__.py              # 导出 XToImageTool
└── x_to_image_tool.py       # BaseTool 薄包装
```

### 5.3 数据模型（`models.py`）

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class InputType(str, Enum):
    TEXT = "text"          # 纯文本
    MARKDOWN = "markdown"  # Markdown
    HTML = "html"          # HTML 字符串或 .html 文件路径


class ImageFormat(str, Enum):
    PNG = "png"
    JPEG = "jpeg"


@dataclass
class XToImageInput:
    source: str                       # 文本/MD/HTML 内容，或 .html 文件绝对路径
    content_type: InputType
    width: int = 800                  # 渲染视窗宽度（px），默认 800
    output_name: Optional[str] = None # 输出文件名（不含扩展名），默认用 uuid
    image_format: ImageFormat = ImageFormat.PNG
    max_height: int = 20_000          # 长图最大高度（px），超限截断
    max_file_size_mb: int = 10        # 长图最大体积（MB），超限转 JPEG/降质
    is_file_path: bool = False        # source 是否为文件路径（HTML 文件场景）
    extra: dict = field(default_factory=dict)  # 保留扩展（背景色/水印等）


@dataclass
class XToImageResult:
    success: bool
    image_path: Optional[str] = None      # 最终单张长图的临时文件绝对路径
    width: int = 0
    height: int = 0
    file_size: int = 0
    truncated: bool = False               # 是否因超限被截断
    renderer: str = ""                    # 实际使用的渲染器名
    error: Optional[str] = None
```

### 5.4 核心服务（`service.py`）

镜像 `NotificationService` 的「注册表 + register_xxx 扩展」模式：

```python
class XToImageService:
    """x-to-image 核心服务：将文本/Markdown/HTML 等渲染为一张长图（写入临时目录）。"""

    def __init__(self):
        self._renderers: dict[InputType, ImageRendererBase] = {}
        self._register_builtin()

    def _register_builtin(self):
        from .renderers.text_renderer import TextRenderer
        from .renderers.markdown_renderer import MarkdownRenderer
        from .renderers.html_renderer import HtmlRenderer
        self.register_input_type(InputType.TEXT, TextRenderer())
        self.register_input_type(InputType.MARKDOWN, MarkdownRenderer())
        self.register_input_type(InputType.HTML, HtmlRenderer())

    def register_input_type(self, content_type: InputType, renderer):
        """注册输入类型渲染器（扩展点）。"""
        self._renderers[content_type] = renderer

    async def convert(self, inp: XToImageInput) -> XToImageResult:
        renderer = self._renderers.get(inp.content_type)
        if not renderer:
            return XToImageResult(success=False, error=f"不支持的输入类型: {inp.content_type}")
        try:
            # 1. 建临时工作目录（mkdtemp，落在进程 TMPDIR）
            import tempfile
            work_dir = Path(tempfile.mkdtemp(prefix="x_to_image_"))
            # 2. 渲染 → 一组页图路径（v1 文本/MD/HTML 通常为 1 张）
            page_paths = await renderer.render(inp, work_dir)
            if not page_paths:
                return XToImageResult(success=False, error="渲染未产生图片")
            # 3. 纵向拼接为单张长图 + 尺寸/体积控制（写入 work_dir）
            from .image_utils import finalize_long_image
            return await finalize_long_image(page_paths, inp, work_dir, renderer_name=renderer.name)
        except Exception as e:
            logger.error(f"[XToImage] 转换失败: {e}", exc_info=True)
            return XToImageResult(success=False, error=str(e))


x_to_image_service = XToImageService()   # 模块级单例
```

### 5.5 渲染器基类与浏览器池

`renderers/base.py`：

```python
class ImageRendererBase:
    name: str = "base"

    async def render(self, inp: XToImageInput, work_dir: Path) -> list[str]:
        """返回渲染得到的页图 PNG 路径列表（落在 work_dir）。"""
        raise NotImplementedError

    async def _shoot_full_page(self, html: str, inp: XToImageInput, work_dir: Path) -> str:
        """通用：把 HTML 设置进页面并做全页长截图。"""
        from .browser_pool import browser_pool
        return await browser_pool.shoot(html, width=inp.width, out_dir=work_dir)
```

`renderers/browser_pool.py` —— **完全复刻 `WeComKfRenderer` 的单例浏览器管理**（懒加载、`asyncio.Lock` 串行、`is_available()` 探测、`close()`），抽取为通用池，支持任意宽度与 `full_page=True`。**关键：headless 硬编码为 True。**

```python
class BrowserPool:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self._available: Optional[bool] = None

    async def is_available(self) -> bool: ...
    async def _ensure_browser(self):
        # headless=True 硬编码：内容均为本系统自生成，无反爬/登录需求
        self._browser = await self._pw.chromium.launch(
            headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        self._page = await self._browser.new_page()

    async def shoot(self, html: str, width: int, out_dir: Path) -> str:
        """set_content → full_page screenshot → 返回 PNG 路径。"""
        async with self._lock:
            await self._ensure_browser()
            await self._page.set_viewport_size({"width": width, "height": 800})
            await self._page.set_content(html, wait_until="networkidle", timeout=15000)
            path = out_dir / f"{uuid.uuid4().hex[:12]}.png"
            await self._page.screenshot(path=str(path), full_page=True, type="png")
            return str(path)

    async def close(self): ...

browser_pool = BrowserPool()
```

### 5.6 各渲染器职责

| 渲染器 | 输入处理 | 关键点 |
|--------|----------|--------|
| `TextRenderer` | 文本 → `<pre style="white-space:pre-wrap;word-break:break-word">...</pre>` 套基础模板 | 保留换行/缩进；等宽或系统中文栈 |
| `MarkdownRenderer` | `markdown.markdown(src, extensions=["tables","fenced_code","codehilite","toc"])` → 套带样式模板（表头底色、代码块底色、`max-width`） | 复用 `WeComKfRenderer.HTML_TEMPLATE` 的样式思路，放宽 `max-width` 以适配长图 |
| `HtmlRenderer` | 若 `is_file_path`：`Path.read_text(encoding="utf-8")`；否则直接用 `source` | 不强制套模板（尊重用户自带样式）；仅注入一个确保 `body` 无默认 margin 的轻量 wrapper |

三者最终都产出一段完整 HTML，交给 `browser_pool.shoot()` 做 headless 全页截图。

### 5.7 长图后处理（`image_utils.py`）

```python
async def finalize_long_image(page_paths, inp, work_dir, renderer_name) -> XToImageResult:
    from PIL import Image
    # 1. 纵向拼接（v1 通常单张，直接复用；多张走 paste）
    long_img = _stitch_vertical(page_paths)
    # 2. 空白检测（getextrema 全白/全透明 → 判失败）
    if _is_blank(long_img):
        return XToImageResult(success=False, error="渲染结果为空白图片", renderer=renderer_name)
    # 3. 高度截断
    truncated = False
    if long_img.height > inp.max_height:
        long_img = long_img.crop((0, 0, long_img.width, inp.max_height))
        long_img = _append_truncation_notice(long_img)  # 底部加「⚠ 内容已截断」横条
        truncated = True
    # 4. 体积控制：存 PNG 超过 max_file_size_mb 则转 JPEG(quality=85)
    name = inp.output_name or uuid.uuid4().hex[:12]
    out_path = work_dir / f"{name}.png"
    long_img.save(out_path, format="PNG")
    if os.path.getsize(out_path) > inp.max_file_size_mb * 1024 * 1024:
        out_path = work_dir / f"{name}.jpg"
        long_img.save(out_path, format="JPEG", quality=85)
    return XToImageResult(success=True, image_path=str(out_path.absolute()),
                          width=long_img.width, height=long_img.height,
                          file_size=os.path.getsize(out_path),
                          truncated=truncated, renderer=renderer_name)
```

> **关于「页数范围」控制**：v1 输入（文本/MD/HTML）本身无页码概念，因此用 `max_height`（默认 20000px）+ `max_file_size_mb`（默认 10MB）作为「长图大小可控」的等价约束。未来接入 PDF/Word 时，渲染器产出多张页图，此时引入 `max_pages`（默认 5）限制参与拼接的页数——`image_utils.stitch_vertical` 与截断逻辑无需改动，自然复用。这也正是统一走「页图列表 → 拼接」管线的收益。

### 5.8 薄工具（`src/tools/image/x_to_image_tool.py`）

```python
class XToImageInputModel(BaseModel):
    content: str = Field(..., description="待转换的内容：纯文本、Markdown 或 HTML")
    content_type: str = Field("markdown", description="内容类型：text / markdown / html")
    is_file_path: bool = Field(False, description="content 是否为本地 .html 文件路径")
    width: int = Field(800, description="图片宽度(px)，默认800，建议400-1200")
    output_name: Optional[str] = Field(None, description="输出文件名(不含扩展名)")


class XToImageTool(BaseTool):
    name = "x_to_image"
    display_name = "内容转图片"
    category = "image"
    description = (
        "将文本、Markdown 或 HTML 转换为一张长图(PNG)。"
        "适用于需要把富文本/表格/样式化内容以图片形式呈现的场景。"
        "返回临时图片文件路径。"
    )
    InputModel = XToImageInputModel

    async def execute(self, **kwargs) -> Dict[str, Any]:
        # 1. 构造 XToImageInput
        # 2. result = await x_to_image_service.convert(inp)
        # 3. 失败：返回 {"success": False, "error": ...}
        # 4. 成功：返回临时文件路径 + 元信息（见下）
```

**成功返回结果**（仅返回临时路径与元信息，**不**注册下载/不返回 download_url）：

```python
return {
    "success": True,
    "image_path": result.image_path,       # 临时文件绝对路径
    "image_name": Path(result.image_path).name,
    "file_size": result.file_size,
    "image_width": result.width,
    "image_height": result.height,
    "truncated": result.truncated,
    "renderer": result.renderer,
}
```

> 调用方（agent 或渠道）拿到 `image_path` 后，自行决定如何交付给用户（如渠道以 image 消息发送、或经 cp 工具注册为可下载文件）。本服务/工具**不**承担文件交付职责，保持单一职责（「转换」）。

### 5.9 工具注册

在 `src/core/agent.py` 的 `_register_builtin_tools`（line 306 起）中，于 PDF 工具注册之后追加：

```python
# 注册 x-to-image 内容转图片工具
from src.tools.image.x_to_image_tool import XToImageTool
self.tool_registry.register(XToImageTool())
```

### 5.10 服务包 `__init__.py` 导出

镜像 `src/services/__init__.py` 既有约定：

```python
# src/services/x_to_image/__init__.py
from .service import XToImageService, x_to_image_service
from .models import XToImageInput, XToImageResult, InputType, ImageFormat
__all__ = ["XToImageService", "x_to_image_service",
           "XToImageInput", "XToImageResult", "InputType", "ImageFormat"]
```

## 6. 容错与兜底

| 场景 | 现象 | 影响 | 兜底方案 |
|------|------|------|----------|
| Playwright/Chromium 不可用 | `is_available()` 返回 False | 无法渲染 | 服务返回 `success=False, error="图片渲染引擎不可用(Playwright/Chromium 未安装)"`；工具层把错误透传给 agent。**不崩溃进程** |
| 浏览器页面断开 | `page.evaluate` 抛异常 | 后续截图失败 | `browser_pool._ensure_browser` 检测后自动 `_cleanup_browser` 重启（同 `WeComKfRenderer`） |
| 渲染出空白图 | 内容为空 / CSS 把内容隐藏 | 用户拿到空白图 | `image_utils._is_blank`（`getextrema`）检测，返回失败 |
| 长图超 `max_height` | 图片过高，IM 无法预览/发送 | 渲染成功但不可用 | 截断 + 底部附加「⚠ 内容已截断，原文 N 字」提示，`truncated=True` 透传 |
| PNG 体积超 `max_file_size_mb` | 文件过大，下载/发送慢 | 体验差 | 自动转 JPEG(quality=85)；仍超限则返回失败并提示调小 width |
| HTML 含外部资源加载慢 | `networkidle` 长时间不触发 | 渲染卡住 | `set_content` 设 timeout（默认 15s），超时按已加载内容截图并记 warning |
| Markdown 解析异常 | 非法语法 | 渲染中断 | `markdown` 库默认容错；异常被 `convert` try/except 捕获返回失败 |

## 7. 临时文件生命周期（已知限制）

- 每次 `convert` 用 `tempfile.mkdtemp(prefix="x_to_image_")` 建独立工作目录，**返回其中的最终长图路径**；中间页图也保留在同目录。
- 落在进程 `TMPDIR`（Docker: `/home/appuser/tmp`；本地: OS 默认）。
- **本项目当前无任何临时文件清理调度**（仅 `log_retention.py` 清理日志）。因此本服务产出的临时图片**不会**被自动删除，依赖调用方在消费后自行清理，或后续统一引入临时文件清理任务。
- **v1 决策**：不为本服务单独引入清理任务，与项目现状（PDF/PPT 渲染产物同样无清理）保持一致，避免增加运维复杂度。若后续临时目录膨胀成为问题，再统一规划一个 `temp/` 清理调度（覆盖所有工具的临时产物），而非各自为政。
- 中间页图与最终长图同目录的设计，便于排查（出问题时可一并查看中间产物）。

## 8. 兼容性

- **零侵入**：不改动任何现有工具行为，不触碰 `CpTool`。
- **新增依赖**：无。
- **Dockerfile**：无需改动（Chromium + 中文字体 + TMPDIR 已就绪）。
- **配置**：默认值内置于代码（宽度 800、max_height 20000、max_file_size 10MB）；如需运行时可配，未来接入 `configs/config.yaml`，v1 不引入。

## 9. 扩展性（v2+ 预留）

- **新增输入类型**：实现 `ImageRendererBase` 子类，在 `XToImageService._register_builtin`（或外部 `register_input_type`）注册即可，`convert` 主流程与 `image_utils` 无需改动。
- **PDF / Word 接入路径**（已规划，不在 v1）：
  - `PdfRenderer`：直接复用 `pdf_renderer.render_pages`（PyMuPDF/Poppler）产出每页 PNG（受 `max_pages` 限制）→ 走 `stitch_vertical` 拼接。
  - `DocxRenderer`：先 `pdf_writer.docx_to_pdf`（LibreOffice 隔离配置）转 PDF → 复用 PdfRenderer。
  - 两者都天然产出「多页图」，与统一拼接管线无缝衔接。
- **渠道复用**：`WeComKfRenderer` 未来可改为调用 `x_to_image_service`（Markdown→长图），消除重复的浏览器管理代码（不在 v1，避免回归风险）。

## 10. 验证方法

- **单元测试**：`renderers` 各类型输入 → 产出非空 PNG；`image_utils` 拼接/截断/空白检测分支；`browser_pool` headless 启动。
- **集成测试**：`x_to_image_service.convert` 端到端，文本/MD/HTML 各一条样例，断言 `success=True`、`image_path` 存在于临时目录、`height <= max_height`、文件非空。
- **工具测试**：`XToImageTool.execute` 断言返回含 `image_path`、无 `download_url`。
- **手测命令**：
  ```bash
  pytest tests/tools/image/ tests/services/x_to_image/ -v
  ```

## 11. 落地步骤（概要，详见开发计划）

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1 | 搭建 `src/services/x_to_image/` 骨架（models / service / base / browser_pool） | 无 |
| 2 | 实现 3 个渲染器（text / markdown / html） | 1 |
| 3 | 实现 `image_utils`（拼接/空白/截断/体积） | 1 |
| 4 | 实现 `XToImageTool` + 注册 | 1,2,3 |
| 5 | 单测 + 集成测试 | 2,3,4 |

## 12. 关联文档

- [PDF 工具设计文档](../pdf/pdf_tool_design.md)（`docx_to_pdf` / `render_pages` 来源）
- [文件工具入参契约重设计](../tool-input-contract-redesign.md)（`instruction/content/content_type/output_name` 结构化入参约定，本工具的 InputModel 遵循之）
- 开发计划：[x-to-image 开发计划](x-to-image-dev-plan.md)
