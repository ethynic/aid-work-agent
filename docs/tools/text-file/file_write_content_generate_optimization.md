# file_write 工具集成内容生成 — 优化方案

> 版本: v1.0 | 创建: 2026-05-19 | 状态: 已实现

## 一、问题

当前 Agent 生成 HTML 报告需要**两步调用**：

```
步骤 1: content_generate(prompt="生成封面页 HTML...", content_type="report")
         → 返回 {success: True, content: "<!DOCTYPE html>...(完整 HTML，数千 token)..."}
         → 完整 HTML 进入 Agent 上下文

步骤 2: file_write(content="<!DOCTYPE html>...(同样的 HTML)...", file_path="cover.html")
         → 同一份 HTML 再次进入 Agent 上下文
         → 返回 {success: True, download_url: "/api/files/xxx/download", ...}
```

**问题**：一份 HTML 内容在 Agent 上下文中出现两次，每次可能数千 token。生成 8 页报告就是 16 次大体积内容注入，严重浪费上下文窗口。

**根因**：`content_generate` 和 `file_write` 是两个独立工具，Agent 必须先把生成内容拿到手（进入上下文），再传给文件工具（再次进入上下文）。生成内容对 Agent 来说是**透明的中间产物**，它只需要知道文件在哪。

---

## 二、方案

在 `file_write` 工具中集成内容生成能力。当 Agent 需要生成文件时，**只调一次 `file_write`**，工具内部自动完成"生成 → 校验 → 写入 → 注册"，只返回文件元信息，不返回文件内容。

### 2.1 改造前后对比

**改造前（两次调用，内容进上下文）**：

```
Agent → content_generate(prompt="生成 HTML...", ...) → 返回 HTML 内容（进上下文）
Agent → file_write(content="<完整 HTML>", ...)       → HTML 再次进上下文
```

**改造后（一次调用，内容不进上下文）**：

```
Agent → file_write(
          file_path="cover.html",
          generate_prompt="根据以下材料生成封面页 HTML...",
          content_type="report"
        )
        → 内部调 LLM 生成 HTML → 校验格式 → 写入文件 → 注册下载
        → 只返回 {success, file_path, download_url}（不返回 HTML 内容）
```

---

## 三、接口设计

### 3.1 新增参数

在 `FileWriteInput` 中新增以下**可选**参数：

```python
class FileWriteInput(BaseModel):
    # === 现有参数（不变） ===
    content: Optional[str] = Field(None, description="文件内容文本（直接提供）")
    file_path: Optional[str] = Field(None, description="目标文件路径")
    file_extension: Optional[str] = Field(None, description="文件后缀名")
    encoding: Optional[str] = Field(None, description="文件编码，默认 UTF-8")
    overwrite: Optional[bool] = Field(False, description="是否覆盖已存在文件")
    register_download: Optional[bool] = Field(True, description="是否注册下载")
    display_name: Optional[str] = Field(None, description="显示文件名")

    # === 新增参数 ===
    generate_prompt: Optional[str] = Field(None, description=(
        "内容生成指令。提供此参数时，工具内部调用 LLM 生成文件内容，"
        "无需再提供 content 参数。"
        "应包含：角色定义、任务描述、输入素材、输出格式要求、质量标准。"
        "适用于生成 HTML 报告、Markdown 文档等长文本场景。"
    ))
    content_type: Optional[str] = Field(None, description=(
        "内容类型标签，辅助 LLM 生成。已知预设："
        "report、article、outline、email、market_report。"
        "仅在使用 generate_prompt 时生效。"
    ))
    language: Optional[str] = Field("zh", description="生成内容的语言，默认中文")
```

### 3.2 参数互斥规则

| 场景 | 参数组合 | 行为 |
|------|----------|------|
| **直接写入** | `content` 有值，`generate_prompt` 为空 | 保持现有逻辑，直接写入 |
| **内部生成** | `generate_prompt` 有值，`content` 为空 | 内部调 LLM 生成内容 → 校验 → 写入 |
| **冲突** | 两者都有值 | 忽略 `generate_prompt`，使用 `content`（直接写入优先） |
| **都为空** | 两者都无值 | 返回错误（与现有行为一致） |

### 3.3 工具描述更新

```python
description = """生成文本文件并注册到下载系统。支持 Markdown、HTML、TXT、CSV、JSON 等格式。

两种使用方式：
1. 直接提供内容：file_write(content="完整内容", file_path="report.md")
2. 内部生成内容：file_write(file_path="report.html", generate_prompt="生成指令...")
   → 工具内部调用 LLM 生成内容，直接写入文件，不暴露生成内容到对话上下文

方式 2 适合生成长文本（HTML 报告、长文档等），避免生成内容占用过多上下文窗口。
生成后自动创建下载链接，用户可在前端下载/预览。"""
```

---

## 四、执行流程

### 4.1 完整流程

```
file_write.execute() 入口
    │
    ├─ content 非空？
    │   └─ YES → 走现有逻辑（直接写入）      ← 不改变现有行为
    │
    ├─ generate_prompt 非空？
    │   └─ YES → 走内部生成逻辑 ↓
    │
    └─ 都为空 → 返回错误

内部生成逻辑：
    │
    ├─ 1. 调用 content_generate 内部方法
    │      LLM 生成内容（在独立 LLM 调用中完成，不影响 Agent 上下文）
    │
    ├─ 2. 格式校验（根据文件后缀）
    │      .html → 检查是否包含 <html 或 <!DOCTYPE
    │      .json → 检查 JSON 有效性
    │      .md   → 检查非空
    │      校验失败 → 重试一次（附加提示词要求修正格式）
    │
    ├─ 3. 写入文件（复用现有写入逻辑）
    │
    ├─ 4. 注册下载（复用现有注册逻辑）
    │
    └─ 5. 构建返回结果（只返回元信息，不含生成内容）
           {
             "success": True,
             "file_path": "/path/to/file.html",
             "file_name": "cover.html",
             "file_size": 12345,
             "download_url": "/api/files/xxx/download",
             "message": "文件已生成: cover.html"
           }
           注意：不返回 "content" 字段
```

### 4.2 格式校验规则

```python
def _validate_content(self, content: str, suffix: str) -> tuple[bool, str]:
    """校验生成内容是否符合文件后缀要求的格式"""
    if suffix in (".html", ".htm"):
        # HTML 必须包含基本的 HTML 标记
        content_lower = content.lower().strip()
        if not ("<html" in content_lower or "<!doctype" in content_lower or "<body" in content_lower):
            return False, "生成内容不是有效的 HTML（缺少 <html> 或 <!DOCTYPE> 标签）"
    elif suffix == ".json":
        try:
            import json
            json.loads(content)
        except json.JSONDecodeError:
            return False, "生成内容不是有效的 JSON"
    elif suffix == ".csv":
        # CSV 基本检查：至少有一行，包含逗号或制表符
        if "," not in content and "\t" not in content:
            return False, "生成内容不像是 CSV 格式（未检测到分隔符）"
    return True, ""
```

### 4.3 重试机制

格式校验失败时，自动重试一次，在 prompt 末尾附加修正指令：

```python
max_retries = 1
for attempt in range(max_retries + 1):
    generated = await self._generate_content(prompt, content_type, language)

    valid, error_msg = self._validate_content(generated, suffix)
    if valid:
        break

    if attempt < max_retries:
        # 重试时附加修正提示
        prompt = f"{prompt}\n\n【注意】上次生成的内容格式不正确：{error_msg}。请确保输出符合 {suffix} 格式要求。"
        logger.warning(f"内容格式校验失败，正在重试: {error_msg}")
    else:
        # 重试仍失败，返回错误
        return {"success": False, "error": f"生成内容格式校验失败: {error_msg}"}
```

---

## 五、内部生成实现

### 5.1 依赖注入方式

`file_write` 内部需要调用 LLM，有两种方式：

| 方式 | 实现 | 优缺点 |
|------|------|--------|
| **A. 直接调用 LLM Gateway** | `from src.llm.gateway import llm_gateway` | 简单直接，但与 `content_generate` 工具有重复代码 |
| **B. 内部实例化 ContentGenerateTool** | 在 FileWriteTool 内部创建 ContentGenerateTool 实例并调用 | 复用现有工具的系统提示词和预设逻辑 |

**推荐方案 A**：直接调用 `llm_gateway.chat()`。原因：
- `content_generate` 的核心逻辑就是 `llm_gateway.chat(messages, temperature=0.7, max_tokens=4096)`
- 系统提示词的预设逻辑（`_get_system_prompt`）与文件生成场景不完全匹配
- 文件生成场景需要更灵活的系统提示词控制（如强调 HTML 结构规范）
- 避免工具间的循环依赖

### 5.2 内部生成方法

```python
async def _generate_content(self, prompt: str, content_type: str, language: str) -> str:
    """内部调用 LLM 生成内容"""
    from src.llm.gateway import llm_gateway

    system_prompt = (
        "你是一个专业的内容生成助手。请严格遵循用户指令生成高质量内容。\n"
        "规则：\n"
        "1. 直接输出最终内容，不要添加说明性文字\n"
        "2. 确保输出符合指定的文件格式要求\n"
        "3. 内容必须完整，不要截断或使用省略号\n"
    )

    if content_type == "report":
        system_prompt += "\n你是一个专业的报告撰写专家。请生成结构清晰的专业报告。"
    elif content_type == "article":
        system_prompt += "\n你是一个资深的内容创作者。请撰写高质量的内容。"

    language_map = {"zh": "请使用简体中文", "en": "Please respond in English"}
    if language in language_map:
        system_prompt += f"。{language_map[language]}。"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    response = await llm_gateway.chat(messages, temperature=0.7, max_tokens=4096)

    if isinstance(response, dict):
        return response.get("content", "")
    return str(response)
```

---

## 六、返回值设计

### 6.1 内部生成模式的返回值

```python
{
    "success": True,
    "file_path": "/abs/path/to/cover.html",
    "file_name": "cover.html",
    "file_size": 12345,
    "encoding": "utf-8",
    "download_url": "/api/files/file_abc123/download",
    "file_id": "file_abc123",
    "download_file_name": "cover.html",
    "message": "文件已生成: cover.html (内部生成)"
}
```

**关键区别**：不包含 `content` 字段。生成内容只存在于工具内部，不泄露到 Agent 上下文。

### 6.2 直接写入模式的返回值

保持现有行为不变（本来就不返回 `content`）。

---

## 七、上下文节省效果

以竞品研究 8 页 HTML 报告为例：

| 方案 | Agent 上下文中的 HTML 内容 | 节省 |
|------|---------------------------|------|
| **改造前** | 每页出现 2 次（content_generate 返回 + file_write 参数）= 16 次 HTML 注入 | — |
| **改造后** | 0 次（file_write 只返回元信息） | 100% |

假设每页 HTML 平均 3000 token：
- 改造前：16 × 3000 = **48000 token** 被 HTML 占用
- 改造后：**0 token** 被 HTML 占用

这对 Agent 循环的可持续性至关重要，尤其是需要连续生成多页报告的场景。

---

## 八、Agent 调用示例

### 8.1 改造前的两步调用

```
# 步骤 1：生成内容（HTML 进入 Agent 上下文）
Agent → content_generate(
  prompt="根据以下材料生成封面页 HTML...",
  content_type="report"
)
← {success: True, content: "<!DOCTYPE html>...(5000字)..."}  ← HTML 进入上下文

# 步骤 2：写入文件（HTML 再次进入上下文）
Agent → file_write(
  content="<!DOCTYPE html>...(同样的 5000 字)...",
  file_path="cover.html"
)
← {success: True, download_url: "..."}  ← HTML 再次进入上下文
```

### 8.2 改造后的一步调用

```
# 一步完成（HTML 不进入 Agent 上下文）
Agent → file_write(
  file_path="cover.html",
  generate_prompt="根据以下材料生成封面页 HTML：\n{材料内容}",
  content_type="report"
)
← {success: True, download_url: "...", file_size: 12345}  ← 只有元信息
```

---

## 九、HTML 报告合并机制

### 9.1 问题

逐页生成的 HTML 文件（cover.html、company_overview.html 等）通过 `file_write(generate_prompt=...)` 生成，返回值只有元信息。Agent 没有这些 HTML 的内容，无法自己拼接合并。

如果让 Agent 用 `file_read` 逐个读取再拼接，HTML 内容又回到上下文中，违背了优化目标。

### 9.2 方案：新增 `merge_files` 参数

在 `file_write` 中新增 `merge_files` 参数，工具内部完成"读取子页面 → 提取 body → 生成带导航的汇总 HTML → 写入文件"。

```python
class FileWriteInput(BaseModel):
    # ... 现有参数 ...

    # === 合并参数 ===
    merge_files: Optional[list[str]] = Field(None, description=(
        "要合并的文件路径列表。提供此参数时，工具读取每个文件的 <body> 内容，"
        "生成带导航框架的汇总 HTML 文件。"
        "与 generate_prompt / content 互斥，三选一。"
    ))
    merge_titles: Optional[list[str]] = Field(None, description=(
        "每页的标题列表，与 merge_files 一一对应。"
        "用于生成汇总页的左侧目录导航。"
        "不提供时使用文件名作为标题。"
    ))
    report_title: Optional[str] = Field(None, description=(
        "汇总报告的标题，显示在导航栏。"
        "如「竞品分析报告 · 飞书」。"
    ))
```

### 9.3 参数优先级

| 参数组合 | 行为 |
|----------|------|
| `content` 非空 | 直接写入（最高优先级） |
| `generate_prompt` 非空 | 内部生成后写入 |
| `merge_files` 非空 | 合并多个文件后写入 |
| 都为空 | 返回错误 |

### 9.4 合并流程

```
file_write(merge_files=["cover.html", "overview.html", ...],
           merge_titles=["封面", "公司概况", ...],
           report_title="竞品分析报告 · 飞书",
           file_path="full_report.html")
    │
    ├─ 1. 逐个读取 merge_files 中的文件
    │      for path in merge_files:
    │          html = Path(path).read_text()
    │          body = 提取 <body>...</body> 之间的内容
    │          pages.append({"title": title, "body": body})
    │
    ├─ 2. 生成汇总 HTML 框架
    │      - 导航栏（上一页/下一页/页码）
    │      - 左侧目录（根据 merge_titles 生成）
    │      - CSS 样式（导航 + 内容区域）
    │      - JS 翻页逻辑
    │
    ├─ 3. 将每页 body 放入 <div class="page-section"> 容器
    │      <div class="page-section active" id="page-0">封面 body</div>
    │      <div class="page-section" id="page-1">概况 body</div>
    │      ...
    │
    ├─ 4. 提取各子页面的 <style> 内容去重合并到 <head>
    │
    ├─ 5. 写入 file_path，注册下载
    │
    └─ 6. 返回元信息（不含 HTML 内容）
```

### 9.5 合并方法实现要点

```python
async def _merge_html_files(
    self,
    file_paths: list[str],
    titles: list[str],
    report_title: str,
) -> str:
    """读取多个 HTML 文件，合并为带导航的汇总 HTML"""
    import re

    pages = []
    collected_styles = set()

    for i, path in enumerate(file_paths):
        p = Path(path)
        if not p.exists():
            logger.warning(f"合并时文件不存在，跳过: {path}")
            continue

        html = p.read_text(encoding="utf-8")

        # 提取 <style> 内容并去重
        for match in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.DOTALL):
            style_text = match.group(1).strip()
            if style_text not in collected_styles:
                collected_styles.add(style_text)

        # 提取 <body> 内容
        body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL)
        body_content = body_match.group(1).strip() if body_match else html

        title = titles[i] if i < len(titles) else p.stem
        pages.append({"title": title, "body": body_content})

    # 构建汇总 HTML
    nav_items = "\n".join(
        f'<a class="toc-item{" active" if i == 0 else ""}" '
        f'onclick="goToPage({i})">{p["title"]}</a>'
        for i, p in enumerate(pages)
    )

    page_sections = "\n".join(
        f'<div class="page-section{" active" if i == 0 else ""}" id="page-{i}">\n'
        f'{p["body"]}\n</div>'
        for i, p in enumerate(pages)
    )

    merged_styles = "\n".join(collected_styles)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{report_title}</title>
  <style>
    /* 导航样式 */
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }}
    .report-nav {{ ... }}
    .toc-panel {{ ... }}
    .toc-item {{ ... }}
    .page-section {{ display: none; padding: 48px; max-width: 900px; margin: 0 auto; }}
    .page-section.active {{ display: block; }}
    /* 子页面合并样式 */
    {merged_styles}
  </style>
</head>
<body>
  <div class="report-nav">
    <button onclick="prevPage()">◀ 上一页</button>
    <span>{report_title}</span>
    <button onclick="nextPage()">下一页 ▶</button>
    <span id="pageInfo">第 1 页 / 共 {len(pages)} 页</span>
  </div>
  <div class="toc-panel">{nav_items}</div>
  <div class="main-content">{page_sections}</div>
  <script>
    var total = {len(pages)}, current = 0;
    function goToPage(i) {{ ... }}
    function prevPage() {{ if (current > 0) goToPage(current - 1); }}
    function nextPage() {{ if (current < total - 1) goToPage(current + 1); }}
  </script>
</body>
</html>"""
```

### 9.6 Agent 调用示例

```
# 1. 逐页生成（每页返回 file_path，HTML 不进上下文）
file_write(file_path="cover.html", generate_prompt="生成封面...")
← {file_path: "/abs/path/cover.html", download_url: "..."}

file_write(file_path="overview.html", generate_prompt="生成公司概况...")
← {file_path: "/abs/path/overview.html", download_url: "..."}

... 继续生成其他页面 ...

# 2. 合并为完整报告（Agent 只传文件路径列表，合并在工具内部完成）
file_write(
  file_path="full_report.html",
  merge_files=["/abs/path/cover.html", "/abs/path/overview.html", ...],
  merge_titles=["封面", "公司概况", "产品分析", ...],
  report_title="竞品分析报告 · 飞书"
)
← {file_path: "/abs/path/full_report.html", download_url: "...", file_size: 150000}
```

**关键**：整个过程中，HTML 内容只在子页面文件和合并后的文件中存在，Agent 上下文里只有文件路径字符串。

---

## 十、对竞品研究设计的影响

`file_write` 完成改造后，竞品研究子智能体的报告生成流程简化为：

```
逐页生成（8 次调用）：
  file_write(file_path="cover.html", generate_prompt="生成封面...")
  file_write(file_path="company_overview.html", generate_prompt="生成公司概况...")
  ...
  file_write(file_path="summary.html", generate_prompt="生成总结建议...")

合并报告（1 次调用）：
  file_write(
    file_path="full_report.html",
    merge_files=["cover.html", "company_overview.html", ...],
    merge_titles=["封面", "公司概况", ...],
    report_title="竞品分析报告 · 飞书"
  )
```

**变化**：
- 不再需要 `content_generate` 工具（报告生成场景下）
- 总共 9 次工具调用（8 次逐页 + 1 次合并），每次只返回元信息
- Agent 上下文中**零 HTML 内容**，只有指令文本和文件路径
- 合并过程纯字符串操作，无需 LLM 参与

---

## 十一、涉及文件

| 文件 | 改动范围 | 说明 |
|------|----------|------|
| `src/tools/file/text_file_writer.py` | 新增 `generate_prompt`/`content_type`/`language`/`merge_files`/`merge_titles`/`report_title` 参数；新增 `_generate_content()`、`_validate_content()`、`_merge_html_files()` 方法；`execute()` 中新增内部生成和合并分支 | 核心改动 |
| `src/tools/file/text_file_writer.py` | 更新 `description` 和 `usage_guide` | 工具描述 |
| `docs/subagent/competitor-research/competitor_research_subagent_design.md` | 报告生成流程描述更新 | 设计文档同步 |

---

## 十二、向后兼容性

- **现有调用方式完全兼容**：只传 `content` 不传 `generate_prompt` 时，行为与改造前完全一致
- **`content_generate` 工具不受影响**：继续独立存在，用于需要 Agent 直接处理生成内容的场景（如生成邮件正文后直接发送）
- **无数据库变更、无前端变更**
