# 假异步问题分析与修复指南

> **Review 修订**：初版错误地将同步工具函数（`def`）中的 `subprocess.run` 误判为假异步。本文已修正——假异步的准确定义是 **`async def` 中直调同步阻塞函数未用 `to_thread` 包裹**，问题出在调用层（`*_process_tool.py` 的 async handler），底层同步工具函数本身没有问题。

## 概述

**假异步**指在 `async def` 函数中直接调用同步阻塞函数（如 `subprocess.run`），而没有通过 `asyncio.to_thread()` 将其包裹到独立线程执行。这会导致 UvicornWorker 的 asyncio 事件循环被阻塞，该 worker 上的所有其他并发请求在此期间都无法得到调度。

**关键判断标准**：只看阻塞调用是否发生在 `async def` 上下文中。`def` 同步函数内部使用 `subprocess.run` 是正常行为，不阻塞事件循环——只有当这个同步函数被 `async def` 调用方直接唤用（而非通过 `to_thread` 包裹）时，才构成假异步。

**影响范围**：本项目 Gunicorn 使用 `UvicornWorker`（异步 ASGI worker），每个 worker 通过 asyncio 事件循环并发处理大量请求。假异步代码会短暂"冻结"事件循环，使该 worker 的吞吐能力在阻塞期间退化为同步模型。

## 架构分层说明

```
async 入口层（*_process_tool.py 的 async handler）   ← 修复点在这里
    │
    │ 调用（缺少 to_thread 包裹）  ← 假异步发生处
    ▼
同步工具层（*_renderer.py / *_capabilities.py 等）   ← 这里用 subprocess.run 没问题
    │
    └── subprocess.run / 同步 I/O  ← 正常同步代码
```

**原则**：底层同步工具函数保持不动。修复在 async 入口层加 `asyncio.to_thread()` 包裹。

## 已确认的假异步代码（async 入口层）

### 1. `src/tools/pdf/pdf_process_tool.py` — `_handle_render_pages`

```python
# 行 763: async def _handle_render_pages
# 行 770: 直接调用同步 render_pages()，未用 to_thread 包裹
async def _handle_render_pages(self, ctx: PipelineContext, params: Dict) -> Dict:
    from src.tools.pdf.pdf_renderer import render_pages
    ...
    return render_pages(       # ← 同步阻塞，触发 poppler/libreoffice 子进程
        file_path,
        pages=params.get("pages"),
        dpi=params.get("dpi", 150),
        max_pages=params.get("max_pages", 10),
        renderer=params.get("renderer", "auto"),
    )
```

阻塞时长：1~60 秒。**P0**。

### 2. `src/tools/pdf/pdf_process_tool.py` — `_handle_validate`

```python
# 行 778: async def _handle_validate
# 行 785: 直接调用同步 validate_pdf()，未用 to_thread 包裹
async def _handle_validate(self, ctx: PipelineContext, params: Dict) -> Dict:
    from src.tools.pdf.pdf_validator import validate_pdf
    ...
    result = validate_pdf(     # ← 同步阻塞，内部会调用 render_pages
        file_path,
        level=params.get("level", "structural"),
        pages=params.get("pages"),
        render_dpi=params.get("dpi", 150),
        max_pages=params.get("max_pages", 10),
    )
```

阻塞时长：1~60 秒（同渲染）。**P0**。

### 3. `src/tools/word/md_to_word.py` — `convert_async`

```python
# 行 86: async def convert_async
# 行 114: 直接 return 同步 _convert_sync()，未用 to_thread 包裹
async def convert_async(md_text, template=None, title="", author="",
                        tenant_id=None, user_id=None):
    ...
    if tenant_id:
        md_text, _refs = await inline_images(...)  # 图片内联是真异步
    return _convert_sync(md_text, template=template, title=title, author=author)  # ← 同步阻塞
```

> 注意：`word_process_tool.py:554` 中的 `await convert_async(...)` 本身用了 `await`，真异步。但 `convert_async` 内部直接调了同步的 `_convert_sync()`（内部含 `subprocess.run` / pandoc），这才是阻塞点。修复位置在 `convert_async` 内部。

阻塞时长：1~5 秒。**P0**。

### 4. `src/tools/ppt/ppt_process_tool.py` — `_handle_spec` / `_generate_ppt`

```python
# 行 372: async def _handle_spec
# 行 385: 直接调用同步 self._render_node_spec(spec)
async def _handle_spec(self, normalized):
    ...
    return self._render_node_spec(spec)       # ← 同步阻塞（Node 渲染子进程）

# 行 387: async def _generate_ppt
# 行 397: 直接调用同步 self._check_node_renderer_ready()
async def _generate_ppt(self, plan):
    ...
    if not self._check_node_renderer_ready():  # ← 同步阻塞（子进程检测）
        raise RuntimeError("node renderer unavailable")
```

> `_handle_html`（行 180）、`_handle_auto`（行 274）、`_handle_template`（行 315）也都在 `async def` 中直接调用同步函数，`_handle_html` 和 `_generate_ppt` 中调用 `_check_node_renderer_ready()` 会触发子进程。

阻塞时长：`_render_node_spec` 1~10 秒；`_check_node_renderer_ready` 数百毫秒。**P1**。

### 5. `src/tools/file/grep_tool.py` — `execute`

```python
# 行 111: async def execute
# 行 161: subprocess.run() 直接在 async 函数中同步调用
async def execute(self, **kwargs) -> Dict[str, Any]:
    ...
    proc = subprocess.run(                    # ← 同步阻塞事件循环
        cmd, capture_output=True, text=True,
        timeout=RG_TIMEOUT, shell=False
    )
```

阻塞时长：数百毫秒到数秒。**P1**（高频工具）。

## 非问题代码（误判说明）

以下文件中的 `subprocess.run` 均在 `def` 同步函数中，**不阻塞事件循环**，不属于假异步：

| 文件 | 所在函数 | 函数类型 | 说明 |
|------|----------|----------|------|
| `src/tools/pdf/pdf_renderer.py:101` | `_render_with_poppler` | `def` | 同步工具函数，行为正常 |
| `src/tools/pdf/pdf_capabilities.py:67` | `command_version` | `def` | 同步工具函数，且调用者不在 async 上下文（启动时调用） |
| `src/tools/word/md_to_word.py:368` | `_pandoc_convert` | `def` | 同步工具函数，行为正常 |
| `src/tools/ppt/renderer.py:49` | `NodePptRenderer.render` | `def` | 同步工具函数，行为正常 |
| `src/tools/ppt/quality_validator.py:239/272` | `_render_preview` | `def` | 同步工具函数，行为正常 |
| `src/tools/ppt/ppt_capabilities.py:115` | `command_version` | `def` | 同步工具函数，版本检测，调用者不在 async 上下文 |

这些是正常的同步模块，**不需要修改**。问题在于调用它们的 async 入口层（见上节）。

## 代码已经正确使用 `asyncio.to_thread` 的正面案例

以下模块的 async handler 已经正确包裹了同步调用：

| 文件 | 处理方式 |
|------|----------|
| `src/tools/data_analysis/` (全部) | 所有 DB/文件操作均用 `await asyncio.to_thread(...)` |
| `src/tools/browser/` (全部) | Redis/DB 操作均用 `await asyncio.to_thread(...)` |
| `src/tools/pdf/pdf_process_tool.py:597` | `await asyncio.to_thread(paddleocr_doc_parsing, ...)` |
| `src/tools/pdf/pdf_process_tool.py:618` | `await asyncio.to_thread(convert_smart, ...)` |
| `src/tools/ppt/ppt_process_tool.py:224` | `await asyncio.to_thread(...)` |
| `src/main.py` (路由层) | `await asyncio.to_thread(MessageDB.create_batch_transactional, records)` |

## 误报（实际安全）的案例

以下 `time.sleep` 虽在项目中出现，但不会阻塞 UvicornWorker 事件循环：

| 位置 | 原因 |
|------|------|
| `src/db/database.py` 的 `time.sleep(120)` | 跑在 `_start_pool_health_check_thread()` 守护线程中 |
| `src/scheduler/executor.py` 的 `time.sleep(60)` | 跑在 APScheduler worker 线程中（非 asyncio 主循环） |
| `src/tools/browser/resume_store.py` 的 `time.sleep(0.01)` | 在 `asyncio.to_thread(op)` 内部，已脱离事件循环 |

## 修复方案

**原则**：底层同步工具函数**保持不动**，在 async 入口层用 `asyncio.to_thread()` 包裹对同步函数的调用。

### 修复 `pdf_process_tool.py:_handle_render_pages`

```python
# 修复前（阻塞事件循环）
async def _handle_render_pages(self, ctx, params):
    ...
    return render_pages(file_path, pages=params.get("pages"), ...)

# 修复后
async def _handle_render_pages(self, ctx, params):
    ...
    return await asyncio.to_thread(
        render_pages, file_path,
        pages=params.get("pages"),
        dpi=params.get("dpi", 150),
        max_pages=params.get("max_pages", 10),
        renderer=params.get("renderer", "auto"),
    )
```

### 修复 `md_to_word.py:convert_async`

```python
# 修复前
async def convert_async(md_text, template=None, ...):
    ...
    return _convert_sync(md_text, template=template, title=title, author=author)

# 修复后
async def convert_async(md_text, template=None, ...):
    ...
    return await asyncio.to_thread(
        _convert_sync, md_text, template=template, title=title, author=author
    )
```

### 修复 `ppt_process_tool.py:_handle_spec`

```python
# 修复前
async def _handle_spec(self, normalized):
    ...
    return self._render_node_spec(spec)

# 修复后
async def _handle_spec(self, normalized):
    ...
    return await asyncio.to_thread(self._render_node_spec, spec)
```

### 修复 `grep_tool.py:execute`

```python
# 修复前（阻塞事件循环）
proc = subprocess.run(cmd, capture_output=True, text=True, timeout=RG_TIMEOUT, shell=False)

# 修复后
proc = await asyncio.to_thread(
    subprocess.run, cmd, capture_output=True, text=True, timeout=RG_TIMEOUT, shell=False
)
```

### 修复注意事项

1. **不要在 `def` 同步函数中加 `await`**——会导致 `SyntaxError`。同步工具函数（如 `pdf_renderer._render_with_poppler`）保持不变。
2. `asyncio.to_thread(func, *args, **kwargs)` 传的是函数对象和参数，**不要传调用结果**（写成 `to_thread(func())` 会先同步执行再传返回值，异步化失效）。
3. 如果同步工具函数已有关键字参数，用 `to_thread(func, arg1, kw1=val1, kw2=val2)` 即可。

## 检测方法

```bash
# 精准检测：找出 async def 函数中直接调用同步阻塞函数的行
grep -rn "async def" src/tools/ -A 20 | grep -B 20 "subprocess\.run\|\.execute(" | grep "async def\|subprocess"

# 检测 *_process_tool.py 中 async def 直调 def 方法但未用 to_thread 的地方
# 思路：找 async def 函数体内调用的同步方法，然后 grep 确认该方法名附近没有 to_thread
```

## 优先级建议

| 优先级 | 位置 | 原因 |
|--------|------|------|
| P0 | `pdf_process_tool.py:_handle_render_pages` | 阻塞最长（最多 60s），PDF 是高频功能 |
| P0 | `pdf_process_tool.py:_handle_validate` | 同上，内部也会触发渲染 |
| P0 | `md_to_word.py:convert_async` | 阻塞 1~5s，Word 生成常用 |
| P1 | `ppt_process_tool.py:_handle_spec / _generate_ppt` | PPT 渲染阻塞 1~10s |
| P1 | `grep_tool.py:execute` | 阻塞数百毫秒到数秒，高频工具 |
| P2 | `ppt_process_tool.py:_handle_html / _handle_auto / _handle_template` | `_check_node_renderer_ready` 阻塞数百毫秒，影响较小 |

## 修订记录

| 日期 | 修订内容 |
|------|----------|
| 2026-07-22 | **初版**：错误地将同步工具函数中的 `subprocess.run` 标记为假异步 |
| 2026-07-22 | **修订**：修正问题定位层级，假异步位置从底层同步工具函数改为 `*_process_tool.py` 的 async handler；移除 `pdf_capabilities.py` 和 `ppt_capabilities.py` 的误报；修复方案改为在 async 入口层包裹 |

## 相关文档

- [异步/同步开发规范](../../../.codebuddy/rules/async-sync-guidelines.md) — 项目规范要求使用 `asyncio.to_thread`
- Gunicorn 配置：`deploy/gunicorn.conf.py` — `worker_class = "uvicorn.workers.UvicornWorker"`，workers=3
