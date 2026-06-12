# 文件操作工具集重新设计文档

> **版本**: v2.0
> **创建**: 2026-06-11
> **状态**: 设计完成，待开发
> **关联代码**: `src/tools/file/file_reader_tool.py`、`src/tools/file/text_file_writer.py`、`src/tools/skill/use_skill_tool.py`、`src/core/agent.py`
> **目标场景**: 让 `guizang-ppt-skill` 在 Agent 循环中端到端跑通（拷贝模板 → 改 title / 主题色 / 填充 slide → 注册下载交付）
> **文档登记**: `docs/ideas.md`

---

## 一、设计目标与原则

### 1.1 设计目标

1. **与 Claude Code 工具命名对齐**：`read` / `write` / `edit` / `cp` 四个工具名，让 skill 文档中频繁出现的 `Read file`、`Edit`、`cp` 等指令对 LLM 而言是"零映射"的，降低 LLM 理解成本。
2. **职责单一**：每个工具只承担一类原子能力（读 / 新写 / 改 / 复制），不再用 mode 参数把多个语义塞进同一个工具。
3. **guizang-ppt-skill 必须完整可执行**：从用户"做一个 PPT"到最终交付 download_url，每一轮 LLM 调用都能命中工具能力，不需要把 100KB+ HTML 塞进 LLM 单次 output。
4. **schema description 控制在 20 行内**：国内模型（Qwen / ZhipuAI）function calling 的 description 越长注意力越分散，每个工具的 description 都要密集、有触发条件、有 bash 映射。

### 1.2 设计原则

| 原则 | 含义 |
|---|---|
| **schema 自描述** | description 明确写出工具能力、触发场景和 bash 命令映射（"相当于 cp 命令"等）。read/write/edit/cp 是**通用文件工具**，不限定 skill 场景，任何需要读写文件的任务都用它们。skill 文档只是其中一个使用场景 |
| **失败直接返回字符串** | 不返回 `{"success": False, "error": ...}`，直接返回错误原因字符串，减少 LLM 解析压力 |
| **成功精简返回** | 砍掉 `encoding`、`message`、`success` 等冗余字段，每个字段都必须是 LLM 后续决策需要的 |
| **不动 SKILL.md** | SKILL.md 继续用 `cp` / `Read` 等命令式语言，由工具 schema 承担翻译职责 |
| **路径白名单** | cp 的 source 必须在项目根目录内，read/write/edit 的目标必须在 `storage/` 输出目录内（write 复用现有约束） |

### 1.3 不做的事

- **不做别名**：旧 `file_read` / `file_write` 工具名不保留。新设计要干净，避免维护两套命名混淆。所有引用点一次性改名。
- **不做搜索工具**：`grep` / `file_search` 独立设计，本次不涉及。
- **不做流式写入**：HTML 大文件场景通过 cp 模板 + edit 分批 replace_section 解决，不需要流式 API。
- **不删除 PPT/Word/Excel 读取**：`read` 工具继续通过 `is_word_document` 等分支路由到现有 reader 实现，不在本次重构范围内。

---

## 二、工具拆分总览

| 工具 name | 职责 | Claude Code 对应 | bash 对应 | 主要场景 |
|---|---|---|---|---|
| **read** | 读取文本文件，支持整文件 / 行号 / 标记三种定位，返回 cat -n 格式 | Read | `cat -n` | 读模板、读 references、读当前生成的 PPT |
| **write** | 新写入文件（覆盖）、追加文件、内部调 LLM 生成 | Write | `echo >` / `echo >>` | 短内容直接写入；HTML 报告类中等文件生成 |
| **edit** | 局部编辑已存在文件：精确字符串替换、行号范围替换、标记之间替换 | Edit | `sed -i` | 修改 title、修改 `:root` 主题色、填充 SLIDES_HERE 区域 |
| **cp** | 文件复制 | 无（CC 没有此工具） | `cp` | 拷贝模板到 output 目标路径 |

### 2.1 为什么从 text_file_writer 拆分

当前 `FileWriteTool` 已经塞进了三种语义：

1. `mode="overwrite"` / `mode="append"` —— 写入
2. `mode="replace_section"` —— 编辑
3. `source_file_path` —— 复制

LLM 在面对一个工具有 8+ 个参数 + 3 种 mode 时，选择错误的概率显著上升。拆分后：
- LLM 看到 skill 文档"cp"指令 → 直接调 cp
- LLM 看到"Edit file" → 直接调 edit
- LLM 看到要生成新文件 → 调 write

每个工具的 schema 都更短、更精确。

### 2.2 删除 FileListTool 的理由

- skill 文档中**没有**任何 `ls` / `file_list` 指令
- 文件读取场景下，LLM 已经知道目标文件路径（skill 文档会明示）
- 探索式目录浏览对 PPT 生成无用，反而让 LLM 误调
- 用户已明确要求删除

---

## 三、每个工具的详细设计

### 3.1 read 工具

#### 3.1.1 name 与 description

```python
name = "read"
description = """读取文本文件内容，返回 cat -n 格式（每行带行号）。三种读取模式：

1. 整文件：read(file_path="...")
   → 读全部（上限 2000 行），适合小文件
2. 行号范围：read(file_path="...", offset=100, limit=50)
   → offset 是 0-based（第 0 行 = 文件第 1 行），返回行号 1-based
   → 配合 limit 分页读取
3. 标记定位：read(file_path="...", section_start="<style>", section_end="</style>")
   → 读两个标记文本之间的行（含标记行），适合读 HTML 区域、配置段
   → section_end 不传时从 section_start 读到文件末尾（受 limit 约束）

任何需要查看文件内容的场景都用本工具：查看用户上传的文档、读取已生成的输出文件、
查看模板内容、查看配置等。大文件（>500 行）先用 offset/limit 或 section_start/section_end
缩小范围，避免一次性读取过多内容。

Word/Excel/PPT 文档自动走专用解析器，不需要单独的工具。

注：skill 加载时，SKILL.md 中的 `<SKILL_ROOT>` 占位符已被替换为 skill 目录绝对路径，
因此 LLM 调用本工具时直接传 use_skill 返回的绝对路径即可，工具本身不需要识别占位符。"""
```

**为什么这样设计 description**：
- 三个读取模式编号清晰，LLM 看一眼就知道匹配哪种
- offset 0-based 与返回 1-based 的差异明确点出（避免 LLM 算错行号）
- 明确写"当 skill 文档要求 Read 文件时调用本工具"建立映射
- 控制在 18 行以内

#### 3.1.2 InputModel

```python
class ReadInput(BaseModel):
    file_path: str = Field(
        ...,
        description="文件路径，绝对路径或相对于项目根目录的相对路径。"
    )
    offset: Optional[int] = Field(
        None,
        description="起始行偏移（0-based）。第 0 行 = 文件第 1 行。"
        "配合 limit 实现分页读取。",
    )
    limit: Optional[int] = Field(
        None,
        description="最多读取多少行。不指定时读到文件末尾或 2000 行上限。",
    )
    section_start: Optional[str] = Field(
        None,
        description="按标记定位：起始标记文本。"
        "工具在文件中查找包含此文本的行，从该行开始读取（含该行）。"
        "标记文本应尽量具体以避免歧义，如 '<style type=\"text/css\">' 而非 '<style'。",
    )
    section_end: Optional[str] = Field(
        None,
        description="按标记定位：结束标记文本。"
        "工具在文件中查找包含此文本的行（必须在 section_start 之后），读到该行为止（含该行）。"
        "不指定时从 section_start 读到文件末尾或 limit 行。",
    )
```

**对比旧 FileReaderInput 的删除项**：
- 删除 `encoding` —— 国内 LLM 几乎不会主动传编码，自动检测即可（保留内部逻辑，不暴露参数）
- 其余字段语义沿用，不动

#### 3.1.3 返回值结构

成功时返回 dict：

```python
{
    "content": "     1\t<!doctype html>\n     2\t<html>...",
    "total_lines": 2419,
    "read_lines": 50,
    "next_hint": "文件共 2419 行，已读 1-50 行。继续: offset=50"
    # 仅在截断时附 next_hint；section 模式额外附 section_start_line / section_end_line
}
```

失败时直接返回字符串：`"读取文件失败: 文件不存在: xxx"` 或 `"未找到起始标记: <style>"`。

**为什么砍掉冗余字段**：
- 旧版返回 `success` / `message` / `encoding` / `file_size` —— LLM 不需要这些做决策
- 新版只保留 content（核心）、total_lines（判断是否截断）、read_lines（校验读取量）、next_hint（继续读的下一步）
- LLM 看到 next_hint 直接知道下一轮 offset 该填多少

#### 3.1.4 核心实现伪代码

```python
async def execute(self, **kwargs):
    file_path = kwargs.get("file_path")
    offset = kwargs.get("offset") or 0
    limit = kwargs.get("limit")
    section_start = kwargs.get("section_start")
    section_end = kwargs.get("section_end")

    if not file_path:
        return "文件路径不能为空"

    try:
        path = self._resolve_path(file_path)

        # Word/Excel/PPT 路由（沿用现有 _read_word_document 等）
        if is_word_document(str(path)):
            return self._read_word_document(path)
        if is_excel_document(str(path)):
            return self._read_excel_document(path)
        if is_ppt_document(str(path)):
            return self._read_ppt_document(path)

        # 文本文件
        content = self._read_file_content(path, encoding=None)
        lines = content.splitlines()
        total = len(lines)

        if section_start:
            return self._read_section(lines, section_start, section_end, limit, total)
        return self._read_range(lines, offset, limit, total)

    except Exception as e:
        return f"读取文件失败: {e}"
```

`_read_range` / `_read_section` / `_format_with_line_numbers` 沿用现有 FileReaderTool 实现，零改动。

---

### 3.2 write 工具

#### 3.2.1 name 与 description

```python
name = "write"
description = """写入文本文件并注册到下载系统（Markdown、HTML、TXT、CSV、JSON 等）。两种模式：

1. 直接写入（短文件 ≤ 4000 字，相当于 echo > file）：
   write(content="完整内容", file_path="report.md")
   mode 默认 overwrite（覆盖）。传 mode="append" 追加（相当于 >>）。

2. 内部生成（中等文件，工具内部调 LLM）：
   write(file_path="report.html", generate_prompt="生成指令...", content_type="report")
   适合报告、文章等结构化长文，内容不进入对话上下文节省 token。

大文件（HTML PPT 100KB+）不要塞 content，改用：先 cp(source_file_path=模板) 复制，再用 edit 替换占位区。

参数优先级：content > generate_prompt。互斥关系明确，避免歧义。"""
```

#### 3.2.2 InputModel

```python
class WriteInput(BaseModel):
    content: Optional[str] = Field(
        None,
        description="文件内容文本。不超过 4000 字时直接传。"
        "超过 4000 字不要用本参数，改用 cp 模板 + edit 替换区域的策略。",
    )
    file_path: Optional[str] = Field(
        None,
        description="目标文件路径（含文件名和后缀），如 'output/report.md'。"
        "支持绝对/相对路径。不提供时自动写入系统临时目录。",
    )
    file_extension: Optional[str] = Field(
        None,
        description="文件后缀名（不含点号），如 'md'、'html'。"
        "仅在 file_path 未提供时使用，默认 'txt'。",
    )
    mode: Optional[str] = Field(
        "overwrite",
        description="写入模式。overwrite（默认）：覆盖整个文件（相当于 >）。"
        "append：追加到文件末尾，文件不存在时自动创建（相当于 >>）。",
    )
    generate_prompt: Optional[str] = Field(
        None,
        description="内容生成指令。工具内部调 LLM 生成，不暴露到对话上下文。"
        "适合 HTML 报告、Markdown 长文等中等长度文件。",
    )
    content_type: Optional[str] = Field(
        None,
        description="内容类型标签，辅助 LLM 生成。预设：report、article、outline、email、market_report。",
    )
    language: Optional[str] = Field("zh", description="生成内容的语言，默认中文。"),
    overwrite: Optional[bool] = Field(
        False, description="overwrite 模式下文件已存在时是否覆盖。默认 False（已存在报错）。"
    ),
    register_download: Optional[bool] = Field(
        True, description="是否自动注册到下载系统，默认 True（用户可下载）。",
    ),
    display_name: Optional[str] = Field(
        None,
        description="注册下载时的显示文件名（可选）。默认用 file_path 中的文件名。",
    ),
```

**对比旧 FileWriteInput 的删除项**：
- 删除 `source_file_path` → 移到 cp 工具
- 删除 `section_start` / `section_end` → 移到 edit 工具
- 删除 `encoding`（默认 UTF-8，不暴露）
- `mode` 仅保留 overwrite / append 两值，删除 replace_section

#### 3.2.3 返回值结构

```python
{
    "file_path": "/abs/path/to/file.md",
    "file_name": "file.md",
    "file_size": 2048,
    "is_temp": False,
    "download_url": "/api/files/xxx/download",
    "file_id": "file_xxxxxxxxxxxx"
}
```

失败时返回字符串。

**为什么砍掉 `success` / `encoding` / `message`**：
- LLM 只需要知道写到了哪里（file_path）、用户能否下载（download_url）
- encoding LLM 用不到
- message 是给人看的，LLM 看到 file_path 就知道成功

#### 3.2.4 核心实现伪代码

```python
async def execute(self, **kwargs):
    content = kwargs.get("content")
    generate_prompt = kwargs.get("generate_prompt")
    mode = kwargs.get("mode") or "overwrite"
    file_path = kwargs.get("file_path")

    if mode not in {"overwrite", "append"}:
        return f"不支持的写入模式: {mode}（仅支持 overwrite / append）"

    if content is not None:
        use_generation = False
    elif generate_prompt:
        use_generation = True
    else:
        return "未提供内容：content 或 generate_prompt 至少提供一个"

    try:
        path = self._resolve_path_or_temp(file_path, kwargs.get("file_extension"))
    except ValueError as e:
        return str(e)

    # 后缀白名单检查、覆盖检查（沿用现有逻辑）
    ...

    if use_generation:
        content = await self._generate_with_retry(
            generate_prompt, kwargs.get("content_type") or "",
            kwargs.get("language") or "zh", path.suffix,
            max_tokens=8192,  # 新增：上限 8192，旧 4096
        )
        if content is None:
            return "内容生成失败，请检查 generate_prompt 或重试"
        content = _strip_code_fences(content)

    if mode == "overwrite":
        path.write_text(content, encoding="utf-8")
    elif mode == "append":
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            content = existing + "\n" + content
        path.write_text(content, encoding="utf-8")

    result = {
        "file_path": str(path),
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "is_temp": file_path is None,
    }
    if kwargs.get("register_download", True):
        dl = self._register_download(path, kwargs.get("display_name"))
        result["download_url"] = dl["download_url"]
        result["file_id"] = dl["file_id"]
    return result
```

**`generate_prompt` 内部 max_tokens 调整**：从现有 4096 提升到 **8192**，保证中等文件生成不被截断。

---

### 3.3 edit 工具（新建）

#### 3.3.1 name 与 description

```python
name = "edit"
description = """对已存在文件做局部编辑。支持三种模式：

1. replace_string（精确字符串替换，相当于 sed 's/old/new/'）：
   edit(file_path="...", mode="replace_string", old_string="<title>旧标题</title>", new_string="<title>新标题</title>")
   → old_string 必须在文件中唯一（多个匹配会报错），防止误改
   → skill 要求改 title / 改占位符时用此模式

2. replace_section（标记之间内容替换）：
   edit(file_path="...", mode="replace_section",
        section_start="<!-- SLIDES_HERE -->", section_end="<!-- END_SLIDES -->",
        content="<section>实际页面...</section>")
   → 标记行保留，只替换两标记之间的内容
   → skill 要求填充占位区域、改 :root 主题色块时用此模式

3. replace_lines（按行号范围替换）：
   edit(file_path="...", mode="replace_lines", offset=120, limit=10, content="新内容")
   → 替换从 offset+1 行开始的 limit 行

目标文件必须已存在；先校验再写，失败不影响原文件。
任何需要修改已存在文件内容的场景都用本工具：修改配置项、替换占位符、
填充模板区域、改 HTML 元素内容等。"""
```

**description 控制在 20 行内**，每种模式一行示例。

#### 3.3.2 InputModel

```python
class EditInput(BaseModel):
    file_path: str = Field(
        ...,
        description="目标文件路径（必须已存在），相对项目根目录或绝对路径。",
    )
    mode: str = Field(
        "replace_string",
        description="编辑模式：replace_string（精确字符串替换，默认）/ "
        "replace_section（标记之间内容替换）/ replace_lines（按行号替换）。",
    )

    # === replace_string 专用 ===
    old_string: Optional[str] = Field(
        None,
        description="[replace_string 专用] 文件中要被替换的精确字符串。"
        "必须在文件中唯一出现，否则报错（防止误改多处）。"
        "建议包含足够上下文（如整行或多行）以确保唯一。",
    )
    new_string: Optional[str] = Field(
        None,
        description="[replace_string 专用] 替换为的新内容。",
    )

    # === replace_section 专用 ===
    section_start: Optional[str] = Field(
        None,
        description="[replace_section 专用] 起始标记文本。"
        "工具在文件中查找包含此文本的行，从该行下一行开始替换，标记行本身保留。",
    )
    section_end: Optional[str] = Field(
        None,
        description="[replace_section 专用] 结束标记文本。"
        "工具在文件中查找包含此文本的行（在 section_start 之后），替换到该行前一行，标记行本身保留。",
    )

    # === replace_lines 专用 ===
    offset: Optional[int] = Field(
        None,
        description="[replace_lines 专用] 起始行偏移（0-based），第 0 行 = 文件第 1 行。",
    )
    limit: Optional[int] = Field(
        None,
        description="[replace_lines 专用] 要替换的行数。",
    )

    # === 共用 ===
    content: Optional[str] = Field(
        None,
        description="新内容。replace_section 和 replace_lines 模式必填。"
        "replace_string 模式不使用此参数（用 new_string）。",
    )
```

#### 3.3.3 返回值结构

```python
{
    "file_path": "/abs/path/to/file.html",
    "file_size": 105267,
    "matched_lines": 2  # replace_section 时返回标记所在行号；replace_string 时返回匹配行号；replace_lines 时返回替换范围
}
```

失败时返回字符串：`"未找到起始标记: <!-- SLIDES_HERE -->"` / `"old_string 在文件中出现 3 次，无法唯一匹配"` / `"目标文件不存在: xxx"`。

#### 3.3.4 核心实现伪代码

```python
async def execute(self, **kwargs):
    file_path = kwargs["file_path"]
    mode = kwargs.get("mode", "replace_string")

    try:
        path = self._resolve_path(file_path)
    except (ValueError, FileNotFoundError) as e:
        return str(e)

    if not path.exists():
        return f"目标文件不存在: {file_path}"

    original = path.read_text(encoding="utf-8")

    # 关键：先在内存中计算新内容，校验通过才写入（失败不影响原文件）
    try:
        if mode == "replace_string":
            new_content, matched_line = self._do_replace_string(
                original, kwargs["old_string"], kwargs["new_string"],
            )
        elif mode == "replace_section":
            new_content, start_line, end_line = self._do_replace_section(
                original, kwargs["section_start"], kwargs["section_end"], kwargs["content"],
            )
        elif mode == "replace_lines":
            new_content, start_line, end_line = self._do_replace_lines(
                original, kwargs["offset"], kwargs["limit"], kwargs["content"],
            )
        else:
            return f"不支持的编辑模式: {mode}"
    except EditError as e:
        return str(e)

    # 原子写入：临时文件 + rename
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(new_content, encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return f"写入文件失败: {e}"

    return {
        "file_path": str(path),
        "file_size": path.stat().st_size,
        "matched_lines": ...,  # 按模式填充
    }


def _do_replace_string(self, original, old, new):
    count = original.count(old)
    if count == 0:
        raise EditError(f"未在文件中找到 old_string: {old[:60]}...")
    if count > 1:
        raise EditError(f"old_string 在文件中出现 {count} 次，无法唯一匹配。请加入更多上下文使其唯一。")
    new_content = original.replace(old, new, 1)
    matched_line = original[:original.index(old)].count("\n") + 1
    return new_content, matched_line


def _do_replace_section(self, original, section_start, section_end, content):
    # 标记行保留，替换两行之间内容（沿用现有 _replace_section 逻辑）
    # 返回 (new_content, start_line, end_line)
    ...


def _do_replace_lines(self, original, offset, limit, content):
    lines = original.splitlines(keepends=True)
    start = offset
    end = offset + limit
    if start >= len(lines):
        raise EditError(f"offset={offset} 超出文件行数 {len(lines)}")
    new_lines = lines[:start] + [content + "\n"] + lines[end:]
    return "".join(new_lines), start + 1, end
```

**关键设计：先校验再写**。所有替换逻辑在内存中完成并校验（old_string 唯一性、标记存在性、行号范围），全部通过后才写临时文件 + rename，保证原文件不被半截修改破坏。

**为什么 replace_string 必须唯一**：对齐 Claude Code Edit 工具的行为。多个匹配时报错，强制 LLM 提供更精确的上下文，避免误改其他相似片段（如 PPT 模板里有多个 `<section class="slide">` 时，old_string 必须包含唯一上下文）。

---

### 3.4 cp 工具（新建）

#### 3.4.1 name 与 description

```python
name = "cp"
description = """复制文件，相当于 shell 的 cp 命令。

用法：
cp(source_file_path="/abs/src/skills/xxx/assets/template.html", file_path="output/ppt/index.html")
→ 将源文件复制到输出目录，自动注册下载

参数：
- source_file_path：源文件绝对路径（或项目根目录相对路径），必须在项目根目录内（防穿越）。
- file_path：目标路径。不传时自动分配下载目录路径。
  · 必须在 storage/ 输出目录内
- register_download：默认 True，复制后自动注册下载链接

任何需要复制文件内容的场景都用本工具：拷贝模板生成新文件、复制用户上传文件、
基于现有文件创建副本等。零 token 消耗（不读源文件内容到上下文）。"""
```

**`<SKILL_ROOT>` 不在 cp 工具层处理**：skill 加载时（`skill_substitutions.py` 第 55-57 行），SKILL.md body 中的所有 `<SKILL_ROOT>` 已被替换为 skill 目录绝对路径。因此 LLM 在 use_skill 返回的 SKILL.md 中看到的命令是：

```
cp "/abs/src/skills/guizang-ppt-skill/assets/template-swiss.html" "output/ppt/index.html"
```

LLM 把这个绝对路径直接传给 cp 工具即可，cp 工具不需要识别占位符，也不需要持有"当前激活 skill"状态。这与现有 `skill_substitutions` 机制保持一致，避免重复造轮子。

#### 3.4.2 InputModel

```python
class CpInput(BaseModel):
    source_file_path: str = Field(
        ...,
        description="源文件路径，绝对路径或项目根目录相对路径。源文件必须在项目根目录内。",
    )
    file_path: Optional[str] = Field(
        None,
        description="目标文件路径。不传时自动分配下载目录路径（推荐，因为 cp 后通常要交付下载）。"
        "目标必须在 storage/ 输出目录内。",
    )
    overwrite: Optional[bool] = Field(
        False, description="目标文件已存在时是否覆盖，默认 False。",
    ),
    register_download: Optional[bool] = Field(
        True, description="复制后是否自动注册到下载系统，默认 True。",
    ),
    display_name: Optional[str] = Field(
        None,
        description="注册下载时的显示文件名（可选）。默认使用源文件名。",
    ),
    visible: Optional[bool] = Field(
        False,
        description="该 cp 注册的下载文件是否在前端对话中展示下载卡片。"
        "默认 False（视为中间过程文件，仅记录 file_id 但不在前端展示）。"
        "如果 cp 本身就是最终交付（如复制用户上传的图片供下载），可设为 True。",
    ),
```

**`visible` 字段的设计意图**：cp 的典型用法是"先复制模板，再 edit 改造"，中间产物不应污染前端的下载卡片列表。前端 `useAgent.ts` 在收到 tool_result 时按 `result.visible !== false` 判断是否追加到 `downloadableFiles`：
- `visible` 缺省（register_download_file / write 注册）→ 视为可见，正常展示
- `visible=false`（cp 默认）→ 隐藏下载卡片，仅在后端记录 file_id

#### 3.4.3 返回值结构

```python
{
    "file_path": "/abs/storage/.../index.html",     # 复制后的目标路径
    "file_name": "index.html",
    "file_size": 104312,
    "download_url": "/api/files/xxx/download",      # 仅当 register_download=True
    "file_id": "file_xxxxxxxxxxxx",
    "resolved_source": "/abs/src/skills/guizang-ppt-skill/assets/template-swiss.html",
    "visible": False                                # 仅当 register_download=True，控制前端展示
}
```

#### 3.4.4 核心实现伪代码

```python
class CpTool(BaseTool):
    name = "cp"

    async def execute(self, **kwargs):
        source_file_path = kwargs["source_file_path"]
        file_path = kwargs.get("file_path")
        overwrite = kwargs.get("overwrite", False)
        register_download = kwargs.get("register_download", True)
        display_name = kwargs.get("display_name")
        visible = kwargs.get("visible", False)

        try:
            src = self._resolve_source(source_file_path)
            dst = self._resolve_target(file_path, src.suffix)

            if dst.exists() and not overwrite:
                return f"目标文件已存在: {dst}，设置 overwrite=True 覆盖"

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

            result = {
                "file_path": str(dst),
                "file_name": dst.name,
                "file_size": dst.stat().st_size,
                "resolved_source": str(src),
            }

            if register_download:
                dl = self._register_download(dst, display_name or src.name, visible=visible)
                result["download_url"] = dl["download_url"]
                result["file_id"] = dl["file_id"]
                result["visible"] = visible

            return result
        except Exception as e:
            return f"复制文件失败: {e}"

    def _resolve_source(self, source_file_path: str) -> Path:
        """解析源路径，必须位于项目根目录内"""
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        src = Path(source_file_path)
        if not src.is_absolute():
            src = project_root / src
        src = src.resolve()
        try:
            src.relative_to(project_root)
        except ValueError:
            raise ValueError(f"源文件超出允许范围（必须在项目根目录内）: {source_file_path}")
        if not src.exists():
            raise FileNotFoundError(f"源文件不存在: {src}")
        if not src.is_file():
            raise ValueError(f"源路径不是文件: {src}")
        return src
```

**安全策略**：`_resolve_source` 通过 `resolve()` + `relative_to(project_root)` 防止路径穿越攻击（如 `../../etc/passwd`）。

---

## 四、guizang-ppt-skill 完整执行序列

下面是用户说"做一个 AI 产品发布 PPT"后，Agent 循环各轮次的预期行为。每轮 LLM 看到的工具结果都很小，避免 100KB HTML 堆积在上下文里。

### 4.1 端到端流程图

```
轮次 1: 用户消息 "做一个 AI 产品发布 PPT"
   ↓ LLM 决策: 需要加载 skill
   ← 工具调用: use_skill(skill="guizang-ppt-skill")
   ↓ UseSkillTool 通过 skill_substitutions 将 SKILL.md body 中的 <SKILL_ROOT>
     替换为绝对路径 "c:/repos/aid-work-agent/src/skills/guizang-ppt-skill"
   ↓ 返回完整 SKILL.md（约 50KB） + 引导语
   （注意：LLM 在 SKILL.md 中看到的 cp 命令已经是绝对路径形式）

轮次 2: LLM 看完 SKILL.md，需要 Step 1（规划）
   ← LLM 输出: 规划主题节奏、页数（在 content 中，不调工具）
   ↓ LLM 决策: 进入 Step 2，拷贝模板（选风格 B 瑞士风）

轮次 3: 拷贝模板
   ← 工具调用: cp(
        source_file_path="c:/repos/aid-work-agent/src/skills/guizang-ppt-skill/assets/template-swiss.html",
        file_path="output/ppt/index.html",
        display_name="AI产品发布.html"
      )
   ↓ cp 工具:
     · 直接复制 template-swiss.html (104KB) → storage/.../index.html
     · 注册下载
     · 注册下载
   → 返回: {file_path, file_size, download_url, file_id}

轮次 4: Step 2.1 改 title（必改占位符）
   ← 工具调用: edit(
        file_path="output/ppt/index.html",
        mode="replace_string",
        old_string="<title>[必填] 替换为 PPT 标题 · Deck Title</title>",
        new_string="<title>AI 重塑工作方式 · 2026 春季发布</title>"
      )
   ↓ edit 工具: old_string 唯一匹配 → 替换 → 原子写入
   → 返回: {file_path, file_size, matched_lines: 8}

轮次 5: Step 2.2 改主题色（替换 :root 块中的颜色变量）
   ← 工具调用: edit(
        file_path="output/ppt/index.html",
        mode="replace_string",
        old_string="--ink: #1a1a1a;\n  --ink-rgb: 26,26,26;\n  ...主题色块原文...",
        new_string="--ink: #0a2540;\n  --ink-rgb: 10,37,64;\n  ...靛蓝瓷主题..."
      )
   ↓ edit 工具: 多行精确匹配 → 替换
   → 返回: {file_path, matched_lines: 56}
   注: 如 LLM 不确定主题色原文，先调 read(section_start=":root", section_end="}") 读出来

轮次 6: Step 3.0 预检：读模板的 <style> 块，确认类名
   ← 工具调用: read(
        file_path="output/ppt/index.html",
        section_start="<style",
        section_end="</style>"
      )
   ↓ read 工具: 返回带行号的 style 块（约 800 行，受 limit 约束可分页）
   → 返回: {content: "1215\t<style>...", total_lines: 2419, read_lines: 800, next_hint: ...}

轮次 7: Step 3.1 读 references/layouts-swiss.md 选布局
   ← 工具调用: read(
        file_path="c:/repos/aid-work-agent/src/skills/guizang-ppt-skill/references/layouts-swiss.md",
        limit=200
      )
   → 返回: layouts 前 200 行，LLM 看到布局骨架选项

轮次 8: Step 3.x 填充 SLIDES_HERE 区域
   ← 工具调用: edit(
        file_path="output/ppt/index.html",
        mode="replace_section",
        section_start="<!-- SLIDES_HERE",
        section_end="<!-- END_SLIDES",
        content="<section class='slide hero dark'>第1页</section>\n<section class='slide light'>第2页</section>\n..."
      )
   ↓ edit 工具: 标记行保留，中间内容替换为 LLM 生成的 slide HTML
   → 返回: {file_path, matched_lines: [start, end]}

轮次 9: Step 5 生成图片（调图片生成工具，不在本设计范围）
轮次 10: Step 6 验证（skill_execute 运行 validate-swiss-deck.mjs）
轮次 11: skill_complete(summary="PPT 已生成，download_url=...")
```

### 4.2 关键策略：避免 100KB HTML 进入 LLM 上下文

| 阶段 | 工具 | 进入 LLM 上下文的内容大小 |
|---|---|---|
| 拷贝模板 | `cp` | 仅返回 file_path + download_url（< 200 字节） |
| 改 title | `edit` replace_string | 仅返回 file_size（< 100 字节） |
| 改主题色 | `edit` replace_string | 仅返回 matched_lines（< 100 字节） |
| 读 style 块 | `read` section_start/end | style 块内容约 800 行（≈ 20KB），必须读但只读一次 |
| 读 layouts 参考 | `read` limit=200 | 约 8KB |
| 填充 slide | `edit` replace_section | 仅返回 matched_lines（< 100 字节） |
| LLM 生成 slide 内容 | LLM 直接输出 | 单次 ≤ 30KB（10 页），可单轮完成 |

**对比旧设计**：旧设计 LLM 试图把完整 100KB HTML 塞进 `file_write(content=...)`，单次 max_tokens 不够，连续 5 次失败。新设计 LLM 永远不需要在 content 参数里持完整 HTML，只在 edit replace_section 时传"要写入的新 slide 段"（≤ 30KB）。

### 4.3 单轮放不下时分批 replace_section 的策略

当 PPT 超过 15 页（slide HTML 超 30KB），LLM 单轮 max_tokens 装不下：

**方案 A：replace_string 锚点追加**（推荐）
1. 第一次 `edit replace_section` 把占位区替换为"前 5 页 + `<!-- APPEND_HERE -->`"
2. 后续每次 `edit replace_string`，old_string=`<!-- APPEND_HERE -->`，new_string=`新 2 页内容\n<!-- APPEND_HERE -->`
3. 最后一次 old_string=`<!-- APPEND_HERE -->`，new_string=`` （清空锚点）

**方案 B：write append 到临时文件，最后 replace_section**
1. `write(mode="append", file_path="output/slides_part.html", content="前 3 页")`
2. `write(mode="append", file_path="output/slides_part.html", content="第 4-6 页")` ...
3. 最后 `read` 临时文件，拼好后 `edit replace_section` 到 index.html

方案 A 更优：不需要 LLM 读取临时文件再写入，每轮只需追加少量内容。

---

## 五、`<SKILL_ROOT>` 占位符处理机制

### 5.1 关键事实：现有机制已覆盖

`<SKILL_ROOT>` 占位符**不在 read/write/edit/cp 四个工具层处理**，因为现有 `skill_substitutions` 机制已经完整覆盖。

`src/core/skill_substitutions.py` 第 55-57 行的实现：

```python
# 1. 替换 <SKILL_ROOT>（Claude Code / Codex / OpenClaw 兼容）
if skill_dir and "<SKILL_ROOT>" in body:
    body = body.replace("<SKILL_ROOT>", skill_dir)
```

当 Agent 调用 `use_skill(skill="guizang-ppt-skill")` 时，`UseSkillTool` 通过 `SkillRegistry.get_content(skill_name, substitutions={...})` 加载 SKILL.md body，**在 body 文本层面**所有 `<SKILL_ROOT>` 已被替换为 skill 目录绝对路径（如 `c:/repos/aid-work-agent/src/skills/guizang-ppt-skill`）。

LLM 在 use_skill 返回的工具结果中看到的命令是这样的：

```
Step 2:
cp "c:/repos/aid-work-agent/src/skills/guizang-ppt-skill/assets/template-swiss.html" "output/ppt/index.html"

Step 3.1:
Read c:/repos/aid-work-agent/src/skills/guizang-ppt-skill/references/layouts-swiss.md
```

LLM 直接把这些绝对路径传给 cp / read 工具即可，工具层不需要任何额外处理。

### 5.2 为什么不在工具层重复实现

| 方案 | 评估 |
|---|---|
| 工具层实现 `<SKILL_ROOT>` 替换 + `set_active_skill` 状态注入 | ❌ 重复造轮子；cp 工具需要持有 skill_registry 引用、维护 active skill 状态、做安全校验，复杂度大 |
| 沿用 `skill_substitutions`，工具只接收绝对路径 | ✅ 单一职责；工具纯粹处理文件操作，不耦合 skill 概念；与 Claude Code 兼容写法在 use_skill 阶段统一处理 |

### 5.3 工具层的路径解析职责

虽然工具不识别 `<SKILL_ROOT>`，但 read/cp/edit 仍需做路径解析：

| 工具 | 接收路径 | 解析规则 |
|---|---|---|
| **read** | 绝对路径（来自 skill 替换后）或项目根目录相对路径 | 沿用现有 `_resolve_path` |
| **cp** source_file_path | 绝对路径（来自 skill 替换后）或项目根目录相对路径 | 新增 `_resolve_source`，必须在项目根目录内（防穿越） |
| **cp** file_path | 项目根目录相对路径 | 复用 write 的 `_resolve_and_validate_path`（限制在 storage/output） |
| **write** file_path | 同 cp file_path | 不变 |
| **edit** file_path | 已存在文件的绝对路径或相对路径 | 沿用现有解析 + 文件必须已存在校验 |

### 5.4 风险与边界

| 风险 | 应对 |
|---|---|
| LLM 误传字面量 `<SKILL_ROOT>/...`（未经 use_skill 替换） | 工具按字面解析路径，找不到文件返回错误，LLM 看到错误后会重试（实际场景中 use_skill 几乎总会先调用） |
| 非 skill 上下文调用 read（用户直接发文件路径） | 完全可行——read 是通用工具，路径解析逻辑不区分来源 |
| 跨平台路径分隔符（Windows `\` vs Unix `/`） | `Path.resolve()` 自动处理，无需特殊代码 |

---

## 六、use_skill_tool 引导语更新

### 6.1 当前引导语（第 85-94 行）

```
- 如果指南中有命令/脚本要执行 → 调用 `skill_execute`
- 如果指南中要求生成内容 → 调用 `content_generate`
- 如果指南中要求搜索信息 → 调用 `web_search`
- 如果指南中有多个步骤 → 逐步执行，不要跳过
- 所有步骤完成后，调用 `skill_complete(...)` 标记完成
```

### 6.2 新引导语

```python
guidance_suffix = f"""

---
**⚠️ 以上是技能「{skill_name}」的完整操作指南。请严格按照指南中的步骤执行：**

**文件操作映射表**（指南中的命令 → 调用的工具）：
- 指南中 `cp <SKILL_ROOT>/... target` 或 "复制模板" → 调用 `cp`
- 指南中 `Read 文件` / "读模板" / "看 references" → 调用 `read`
- 指南中 `Edit 文件` / "修改 title" / "替换占位符" / "填充内容" → 调用 `edit`
- 指南中 `mkdir` / `touch` / `ls` 等其他 shell 命令 → 当前无对应工具，跳过或用其他方式
- 指南中要求生成纯文本内容（非基于模板） → 调用 `write`

**其他工具**：
- 指南中有命令/脚本要执行 → 调用 `skill_execute`
- 指南中要求搜索信息 → 调用 `web_search`
- 指南中要求生成图片 → 调用图片生成工具

**执行规则**：
- 多个步骤 → 逐步执行，不要跳过
- 所有步骤完成后，调用 `skill_complete(skill="{skill_name}", summary="结果摘要")` 标记完成
- 不要直接回复用户"正在执行"，而是立即开始执行第一步"""
```

**新增内容**：
- 文件操作映射表（5 行）—— 让 LLM 看到 skill 里的 `cp` / `Read` / `Edit` 立刻知道用哪个工具
- 明确 `mkdir` / `touch` / `ls` 无对应工具（避免 LLM 反复尝试）

---

## 七、SKILL.md 改造（最小化）

### 7.1 SKILL.md 是否需要改造？

**决策：不改造 SKILL.md 正文**。理由：
- 工具 schema description 已经承担了"翻译 cp/Read/Edit 命令"的职责
- SKILL.md 的命令式语言（`cp`、`Read`、`Edit`）对人类读者更直观，对 LLM 也建立了和 bash 的直觉映射
- 改造 SKILL.md 工作量大且容易引入错误

### 7.2 模板文件改造（已基本就绪）

经查证：
- `src/skills/guizang-ppt-skill/assets/template.html` 第 487-488 行已有 `<!-- SLIDES_HERE -->` 和 `<!-- END_SLIDES -->`
- `src/skills/guizang-ppt-skill/assets/template-swiss.html` 第 1226 行和第 1332 行已有 `<!-- SLIDES_HERE · ... -->` 和 `<!-- END_SLIDES -->`

**结论**：模板的 `END_SLIDES` 标记**已经存在**，无需新增。本次重构可以直接用 edit replace_section 处理两个标记之间的内容。

唯一需要注意的点：template-swiss.html 的 SLIDES_HERE 标记行包含中文注释（`<!-- SLIDES_HERE · 在此处粘贴 <section class="slide ..."> 页面块 -->`），LLM 调 edit 时 `section_start` 只需要传 `"<!-- SLIDES_HERE"` 这种前缀子串就能命中（in 匹配）。description 中已说明"标记文本应尽量具体"，但工具实现用 `in` 匹配，前缀子串即可。

---

## 八、改动清单（影响面）

| 文件路径 | 操作 | 改动要点 |
|---|---|---|
| `src/tools/file/file_reader_tool.py` | 重构 + 改名 | name="read"；删除 FileListTool 类；删除 encoding 参数；InputModel 改为 ReadInput；保留所有读取逻辑 |
| `src/tools/file/text_file_writer.py` | 重构 + 改名 | name="write"；删除 source_file_path / section_start / section_end / encoding 字段；mode 仅保留 overwrite / append；内部 LLM max_tokens 提到 8192 |
| `src/tools/file/edit_tool.py` | **新建** | EditTool，name="edit"；三种 mode：replace_string / replace_section / replace_lines；replace_string 强制 old_string 唯一性；先校验再写 |
| `src/tools/file/cp_tool.py` | **新建** | CpTool，name="cp"；source_file_path 解析必须在项目根目录内（防穿越）；register_download 默认 True |
| `src/tools/file/__init__.py` | 改导出 | 删除 FileListTool；新增 ReadTool / WriteTool / EditTool / CpTool 类名导出；调整 create_file_tools 返回 |
| `src/core/agent.py` | 改注册 | 删除 `FileListTool()` 注册；`FileReaderTool()` → `ReadTool()`；`FileWriteTool()` → `WriteTool()`；新增 `EditTool()` 和 `CpTool()` 注册 |
| `src/tools/skill/use_skill_tool.py` | 改引导语 | 第 85-94 行：新增文件操作映射表（cp/Read/Edit/write 四行映射） |
| `src/skills/guizang-ppt-skill/assets/template.html` | **无需改动** | 第 487-488 行已有 SLIDES_HERE / END_SLIDES 标记 |
| `src/skills/guizang-ppt-skill/assets/template-swiss.html` | **无需改动** | 第 1226 / 1332 行已有 SLIDES_HERE / END_SLIDES 标记 |

### 8.1 旧工具名引用同步改名

经 Grep 确认，以下文件包含 `file_read` / `file_write` / `FileReaderTool` / `FileWriteTool` / `FileListTool` 引用，需逐一处理：

| 文件 | 处理方式 |
|---|---|
| `src/saas/api/channel_routes.py` | 查看具体上下文，若是工具名映射则改名 |
| `src/main.py` | 同上 |
| `scripts/test_ppt_skill_agent.py` | 测试脚本，按新工具名更新 |
| `scripts/test_guizang_ppt_skill_compat.py` | 同上 |
| `tests/unit/test_skill_substitutions.py` | 同上 |
| `tests/unit/test_competitor_research.py` | 同上 |
| `src/skills/competitor-research-1.0.0/scripts/html_report_merger.py` | 检查是否硬编码工具名 |

---

## 九、迁移与回归策略

### 9.1 工具名别名

**决策：不保留别名**。旧 `file_read` / `file_write` 工具名彻底废弃。理由：
- 别名会让 LLM 在新旧工具之间犹豫，影响 function calling 准确率
- 工具改名是一次性投入，所有引用点改完后无回头路
- 国内 LLM 对工具名敏感度高于 Claude，干净的工具列表反而帮助决策

### 9.2 Agent 注册同步

`src/core/agent.py` 的 `tool_registry.register(...)` 列表和顶部 `from ... import` 必须同步更新，否则会 ImportError。

### 9.3 use_skill 后路径替换由现有机制承担

`<SKILL_ROOT>` 占位符替换在 `skill_substitutions.py` 第 55-57 行已实现，**不需要在 Agent 工具调用分发中做任何额外处理**。

use_skill 工具返回的 SKILL.md body 中，`<SKILL_ROOT>` 已经被替换为绝对路径，LLM 直接把替换后的路径传给 cp / read 即可，工具按普通路径解析。

### 9.4 回归测试

| 测试项 | 验证点 |
|---|---|
| read 整文件读取 | read(file_path="small.md") 返回 cat -n 格式 |
| read 行号范围 | offset=10, limit=5 返回 11-15 行 |
| read 标记定位 | section_start="<style>", section_end="</style>" 返回 style 块 |
| read 接收 use_skill 替换后的绝对路径 | read(file_path="/abs/skills/xxx/SKILL.md") 能解析 |
| read 不存在文件 | 返回字符串错误，不抛异常 |
| write overwrite | 新文件写入成功，注册下载返回 download_url |
| write append | 追加模式文件末尾添加内容 |
| write generate_prompt | 内部 LLM 生成内容，max_tokens=8192 |
| edit replace_string 唯一 | old_string 唯一时替换成功 |
| edit replace_string 多匹配 | old_string 多次出现时报错，原文件不变 |
| edit replace_section | 标记行保留，中间内容替换 |
| edit replace_lines | offset+limit 范围替换 |
| edit 失败不破坏原文件 | 校验失败时原文件字节级不变 |
| cp `<SKILL_ROOT>` | 复制 skill 模板到 output 路径 |
| cp 普通路径 | 复制项目内文件 |
| cp 路径穿越 | source 含 `..` 时拒绝 |
| **端到端**：guizang-ppt-skill 完整流程 | use_skill → cp → edit(title) → edit(:root) → read(style) → edit(slides) → skill_complete，最终交付 download_url |

### 9.5 灰度策略

由于 LLM 直接看到新工具名，无法灰度。建议：
1. 开发分支完成所有改动
2. 跑完整回归测试（含 guizang-ppt-skill 端到端）
3. 一次性合入主干，删除旧工具

---

## 十、风险与不做的事

### 10.1 风险

| 风险 | 严重度 | 缓解措施 |
|---|---|---|
| 工具名变更影响所有引用点 | 中 | 已 Grep 列出全部文件，迁移时逐一处理 |
| LLM 不知道 `cp` 工具名映射到 skill 文档的 cp 命令 | 中 | use_skill 引导语新增映射表（4 行）；cp 工具 description 明确写"相当于 shell 的 cp 命令" |
| `<SKILL_ROOT>` 路径解析（由现有 skill_substitutions 承担） | 低 | 第 55-57 行已实现替换；工具层只需做项目根目录范围校验（防穿越），不重复实现占位符逻辑 |
| edit replace_string 多匹配时 LLM 困惑 | 中 | 错误信息明确指出"出现 N 次"，引导 LLM 加入更多上下文 |
| edit 失败时破坏原文件 | 高 | 先在内存中校验，全部通过后才写临时文件 + rename（原子操作） |
| set_active_skill 状态在多轮对话中失效 | 低 | 不适用——本设计未引入 set_active_skill 机制；`<SKILL_ROOT>` 由 skill_substitutions 在 use_skill 阶段一次性替换为绝对路径，后续工具调用直接使用绝对路径 |

### 10.2 不做的事

- **不做 grep / 搜索工具**：本次范围之外，未来独立设计 `search` 工具
- **不做流式写入**：HTML 大文件场景已通过 cp + edit replace_section 解决
- **不做工具名别名**：干净命名优先
- **不动 SKILL.md 正文**：工具 schema 承担翻译职责
- **不动 layouts.md / themes.md 等 references**：这些是 skill 的内容资产，不在工具重构范围
- **不删除 PPT/Word/Excel reader 逻辑**：read 工具继续路由到现有专用 reader
- **不修改 generate_prompt 内部 LLM 调用的 system_prompt 模板**：仅提升 max_tokens 到 8192

---

## 十一、关键设计岔路口（已确认决策）

1. **`<SKILL_ROOT>` 在 read/cp 工具中是否需要单独支持？**
   - 决策：**不需要**。现有 `skill_substitutions.py` 第 55-57 行已在 use_skill 阶段将 SKILL.md body 中所有 `<SKILL_ROOT>` 替换为绝对路径，LLM 拿到的命令已经包含绝对路径，直接传给 read/cp 即可。工具层不重复实现占位符替换。

2. **edit replace_lines 模式是否需要？**
   - 当前 skill 工作流中用不到（replace_string 改 title、replace_section 填 slides 已覆盖全部需求）。
   - 决策：**保留**。对齐 Claude Code Edit 的行号编辑能力，未来其他 skill 可能用到。

3. **cp 工具是否在 file_path 不传时自动注册下载？**
   - 决策：**默认 register_download=True，自动分配下载目录路径**。理由：cp 后通常要交付给用户。

4. **edit replace_string 是否支持 old_string 跨多行？**
   - 决策：**支持**（Python `str.replace` 天然支持多行）。LLM 替换 `:root { ... }` 多行块时必须用多行。

5. **旧工具名别名是否保留？**
   - 决策：**不保留**。干净命名优先，所有引用点一次性改名。
