# 工具开发规范（Tool Development Spec）

> 配套设计文档：[`tool-overall-optimization-design.md`](./tool-overall-optimization-design.md) #2 统一规范。
>
> 本文档是**可执行的开发规范**，既是新工具开发的 checklist，也是旧工具重构的判定依据。后续所有工具改造和新工具开发**必须遵循**。
>
> 公共能力实现见 [`src/tools/_helpers.py`](../../src/tools/_helpers.py)（`truncate_text` / `sanitize_error`）与 [`src/tools/_spill.py`](../../src/tools/_spill.py)（`spill_large_content`）。
>
> **2026-10 修订**：对齐 commit `026ca30b`（提示词压缩重构）后的实际架构——工具说明统一走 tools 参数 `description` 通道，`usage_guide` 停用；原「description ≤ 80 字符 + 教程迁 usage_guide」条款废除。同时并入大内容落盘闭环契约（原落盘计划 Phase D 遗留项）。

## 0. 规范目标

LLM 每轮对话都消耗两类工具 token：① **工具 schema**（`description` + 参数 Field description，随 tools 参数注入，全对话常驻）；② **`execute()` 返回值**进入 tool_result，逐轮累积。本规范从这两条入口统一约束，最大化单位 token 的信息密度，并杜绝把敏感/调试信息灌入对话上下文。

核心心法：**schema 只描述「工具是什么、参数长什么样、怎么用才对」；返回值只带「调用方无法从调用本身得知的新信息」；诊断走日志、大内容走落盘**。

---

## 本地开发依赖

部分工具依赖系统二进制（非 Python 包，`pip install` 装不了）。本地开发前请按平台安装：

| 依赖 | 用途 | Linux/Docker | Windows（venv） | macOS |
|------|------|------|------|------|
| **ripgrep (`rg`)** | `grep` 工具（文件内搜索，grep_tool.py 调系统 rg 二进制） | `apt-get install -y ripgrep`（Dockerfile 已内置） | `winget install BurntSushi.ripgrep.MSVC` 或 `scoop install ripgrep` | `brew install ripgrep` |
| **pandoc** | `word_process` 的 md_to_word 操作 | `apt-get install -y pandoc`（Dockerfile 已内置，详见 [pandoc 安装指南](md-to-word/pandoc-install-guide.md)） | `winget install JohnMacFarlane.Pandoc` | `brew install pandoc` |

**验证**：`rg --version`、`pandoc --version` 能输出版本号即可。

> 说明：这些是**系统级 CLI**，与 Python 虚拟环境无关，venv 内无需 pip 安装。缺失时工具会优雅降级（grep 返回「ripgrep 未安装」，word 转 PDF 等功能不可用），不会导致服务启动失败。

---

## 1. 六条核心契约

| 维度 | 契约 | 反例（禁止） |
|------|------|------|
| `description` | 工具说明**唯一通道**：功能 + 触发场景 + 正确调用所必需的教程/约束/负面清单，能短则短 | 与 Field description 或系统提示词模板内容重复；塞与调用无关的业务背景 |
| Field description | 只描述字段本身（类型/含义/默认值/枚举值） | few-shot / 调参示例 / 业务背景 |
| 文本返回字段 | 统一字符上限（单字段 ≤ 2000、全文类 ≤ 5000）；全文类超限走**落盘闭环**（preview + file_path + `truncated`） | 无上限裸传全文 / 表格 / chunk |
| 错误返回 | 仅 `{"success": False, "error": "<脱敏一句话>"}` | `debug` 字段 / `str(e)` 原文 / traceback / `kwargs` 回显 |
| 成功返回 | 只带新信息（路径/行数/count/id/状态） | echo 输入参数 / 纯话术 `message` |
| 参数约束 | 条件必填用 `model_validator` + `Literal` 枚举，让 schema 自描述 | 全标 Optional + 运行时手动校验报错 |

下面逐条展开。

---

### 1.1 `description`：工具说明唯一通道

- **做什么**：承载功能一句话 + 触发场景 + LLM 正确使用所必需的内容——调用教程、参数配合方式、负面清单（不要怎么用）、cp 注册提醒。
- **为什么集中在这里**：description 随 tools 参数每轮注入，是唯一稳定到达 LLM 的工具说明通道；`usage_guide` 已停用（见 §1.1.1）。
- **长度原则**：**能短则短，不设硬性字符上限**——每轮常驻注入，多一字都是固定成本。简单工具一句话即可；确需教程的工具写紧凑教程，但与 Field description、系统提示词模板**零重复**。
- **禁止**：与调用无关的业务背景、system-prompt 级全局指令（如「必须先向用户确认」）、与 Field description 逐条重复的参数说明。

```python
# ✅ 好：简单工具——功能 + 触发场景一句话
description = (
    "将文本、Markdown 或 HTML 转换为一张长图(PNG)。"
    "适用于需要把富文本/表格/样式化内容以图片形式呈现给用户的场景。"
)

# ✅ 好：复杂工具——功能 + 场景 + 紧凑的操作约束（不与 Field description 重复）
description = "Word 文档处理工具。支持生成/读取/修改/格式化/对比/模板填充。只读操作（read/analyze/word_to_md/diff）不产生新文件，无需调用 cp。"

# ❌ 坏：塞与调用无关的业务背景、逐条复述参数
description = "本工具由 XX 业务线使用，调用前必须先向用户确认意图。参数 content 是字符串类型，表示待处理的正文内容，参数 file_name 是字符串类型……"
```

#### 1.1.1 `usage_guide`：已停用

`BaseTool.usage_guide` 字段与 `get_usage_guide()` 仍存在（历史兼容），但**新工具一律不写 usage_guide**，内容并入 `description`；存量非空 usage_guide 在触碰对应工具时顺手迁移。工具说明不得同时出现在系统提示词模板与 description 两处（026ca30b 已消除重复注入）。

### 1.2 Field description：只描述字段本身

- 只写：类型、含义、默认值、枚举值。
- 禁止：调参示例（「查询近期邮件时建议 limit=5」）、few-shot、业务背景。

```python
# ✅ 好
limit: int = Field(10, description="返回条数上限，默认 10")
content_type: Literal["text", "markdown", "html"] = Field(
    "markdown", description="内容类型：text/markdown/html"
)

# ❌ 坏：Field description 混入调参 few-shot
limit: int = Field(10, description="返回条数上限。查询近期邮件时建议设为 5；用户问未读时配合 unseen_only=True")
```

### 1.3 文本返回字段：截断 + 落盘闭环

分两档处理：

**① 普通文本字段（≤ 5000 字符）——截断 + 标记**

- 单字段（如 chunk 文本、单个 API 响应体）：≤ **2000** 字符。
- 全文类（如整篇 markdown、整页 markdown、文档全文）：≤ **5000** 字符。
- 超出时：返回截断后的文本 + `"truncated": true` 标记，让 LLM 知道需要按需续读。
- 统一用公共 helper `truncate_text(text, limit)` 实现截断（见 §3）。

```python
from src.tools._helpers import truncate_text

text, truncated = truncate_text(raw_text, limit=5000)
return {"success": True, "preview": text, "truncated": truncated, "total_len": len(raw_text)}
```

**② 全文类超限（> 5000 字符）——落盘闭环（必选）**

截断而不落盘会制造「信息黑洞」：LLM 永远拿不到被截掉的内容。全文类返回超限时**必须**调 `spill_large_content`（见 §3）把完整内容写入临时文件，返回 preview + file_path，LLM 按需用 `read`/`grep` 回读：

```python
from src.tools._spill import spill_large_content

if len(full_text) > 5000:
    spill = spill_large_content(full_text, prefix="word_to_md_", suffix=".md")
    return {
        "success": True,
        "preview": spill["preview"],          # 前 5000 字符
        "truncated": spill["truncated"],       # True
        "file_path": spill["file_path"],       # 完整内容落盘路径，可 read/grep 回读
        "full_size": spill["full_size"],
    }
```

- 现行样板：`http_api._parse_response`（JSON/text 大响应落盘）、`pdf_process._merge_results`（read/ocr/pdf_to_md 截断字段落盘）。
- 读取/转换类工具（word/excel/pdf 的 to_md、read）不得回塞全文——返回行数/页数等元信息 + preview，全文走落盘文件路径。
- 例外声明：个别工具如确需返回完整候选集（如知识库检索要让 LLM 看到全部命中），须像 `knowledge_base_tool` 一样在代码注释中写明 `_no_truncate` 的业务理由。

### 1.4 错误返回：脱敏 + 极简

- 统一格式：`{"success": False, "error": "<脱敏后的一句话用户提示>"}`。
- **全局禁止** `execute()` 返回值携带 `debug` 字段给 LLM。
- 需要诊断信息走结构化日志（`logger.error(..., exc_info=True)`），不进 tool_result。
- **禁止返回**：`str(e)` 原始异常字符串、`traceback` 字符串、`kwargs` 回显（最严重的是早期 `upload_to_remote` 的 `debug=kwargs` 会泄漏明文密码——随工具删除已解决）。
- 脱敏用公共 helper `sanitize_error(error, safe_messages=...)`，参照 `ppt_process._format_user_error` 白名单机制（见 §3）。

```python
# ✅ 好：脱敏一句话
except SomeInternalError as e:
    logger.error(f"[XxxTool] 失败: {e}", exc_info=True)
    return {"success": False, "error": sanitize_error(e, safe_messages=SAFE_MSGS)}

# ❌ 坏：回塞 kwargs / str(e) / traceback
return {"success": False, "error": str(e), "debug": kwargs, "traceback": tb}
```

### 1.5 成功返回：只带新信息

- 只返回 LLM 无法从调用本身得知的**新信息**：生成的路径、行数、count、id、状态、truncated 标记。
- **禁止 echo 输入参数**（如 `email_send` 回显 to/cc/subject、`ai_call` 回显 phone/lead_id）。
- 删除纯话术 `message`（如「操作成功」「已为您生成」），或仅保留真正承载新信息的 message。
- 内部 trace / 中间步骤走单独持久化，不进返回值（参照 `analyze_data`）。

### 1.6 参数约束：让 schema 自描述

- 枚举值用 `Literal[...]`，不要用 `str` + 运行时校验。
- 条件必填用 Pydantic `model_validator`（跨字段校验），不要全标 Optional 再在 execute 里手动报错。
- 目标：schema 自描述参数约束，减少 LLM 猜参 → 运行时报错 → 往返重试的 token 浪费。

```python
# ✅ 好（ppt_process 的做法）
content_type: Optional[Literal["auto","text","markdown","html","slide_deck_spec"]] = Field(...)
export_mode: Optional[Literal["editable","high_fidelity","both"]] = Field(...)

@model_validator(mode="after")
def require_content_or_file(self):
    if not self.content and not self.context and not self.file_paths:
        raise ValueError("请提供 content、context 或 file_paths")
    return self
```

---

## 2. 正面标杆

新工具与重构都应参照这些标杆，它们分别示范了规范的不同侧面。

### 标杆 A：`x_to_image`（描述短 + 返回纯元信息）
- `description` 简短（~80 字符）：功能 + 触发场景一句话说完——简单工具的 description 长度基准。
- 成功返回**只带元信息**：`image_path` / `image_name` / `file_size` / `width` / `height` / `truncated` / `renderer`，不回塞图片内容本身、不 echo 输入。
- 超长内容用 `truncated` 标记。**这是「成功返回」契约的范本。**

### 标杆 B：`ppt_process`（错误脱敏 + Literal 枚举）
- 错误处理用 `_format_user_error` 白名单脱敏：异常按类型映射到固定安全消息；个别类型（`HtmlExportSecurityError`/`TemplateAnalysisError`）再用 `set` 白名单放行**确定的少量消息字符串**，其余一律兜底脱敏。绝不返回 `str(e)` 原文。
- 参数约束用 `Literal` 枚举（`content_type` / `export_mode`）+ `field_validator`（trim 空串）+ `model_validator`（跨字段必填校验）。
- **这是「错误返回」+「参数约束」契约的范本。** 公共能力 `sanitize_error` 即从其脱敏逻辑提取（ppt_process 自身暂未切换调用，触碰时顺手迁移）。

### 标杆 C：`analyze_data`（trace 单独持久化）
- 内部 `_steps` / `SpanRecord` / `TraceRecord` **只走 `trace_persist` 单独持久化，不放进对外返回值**，避免把中间过程灌入 tool_result。
- table 输出只带 `preview`（前 3 行）+ 列名 + 行数等元信息，全表走 artifact/文件。
- `analysis_meta` 全是小字段（行数/列数/类型），不回塞大块数据。
- **这是「返回值不灌大块内容」+「trace 不进返回值」契约的范本。**

### 标杆 D：`http_api` / `pdf_process`（大内容落盘闭环）
- 大响应/大文档调 `spill_large_content` 落盘，返回 `preview + file_path + truncated + full_size`，LLM 用 `read`/`grep` 按需回读。
- **这是 §1.3「截断 + 落盘闭环」契约的范本。**

---

## 3. 公共能力

工具改造一律复用以下公共函数，不得各写一套截断/脱敏/落盘。

### `truncate_text(text, limit, suffix="...")`（`src/tools/_helpers.py`）
截断文本到 `limit` 字符。返回 `tuple[截断后文本, 是否截断]`。未超长原样返回 `(原文本, False)`；超长则截到 `limit` 并拼 `suffix`，返回 `(截断后, True)`。纯函数、无副作用，`None`/空串安全。

> 使用约定：返回字典时把 `truncated` 一并带出，方便 LLM 判断是否续读。

### `sanitize_error(error, safe_messages=None, fallback="操作失败，请稍后重试")`（`src/tools/_helpers.py`）
通用错误脱敏。
- `error`：异常对象或字符串。
- `safe_messages`：`{异常类型: 安全消息}` 映射，支持三种形态（调用方按工具自身白名单传入）：
  1. `{类型: str}` —— 命中该类型异常，一律返回此固定安全消息。
  2. `{类型: set[str]}` —— 命中类型后，仅当 `str(error)` 在集合内才透传该消息，否则继续匹配/fallback（对标 `ppt_process` 的 `safe_security_errors` 写法）。
  3. `{类型: (set[str], str)}` —— set 形态的增强版：命中集合透传消息，未命中走 tuple 第二项「分类兜底消息」（对标 `ppt_process` 的 `HtmlExportSecurityError` 处理：命中固定集合透传，否则返回「HTML 文件未通过安全检查」）。
- `fallback`：白名单都不命中时的兜底脱敏消息。
- **核心原则**：绝不返回 `str(e)` 原文、traceback、kwargs。只有命中白名单的消息才透传，其余一律 fallback。

### `spill_large_content(content, *, prefix, suffix, meta=None)`（`src/tools/_spill.py`）
大内容落盘（调用方判断 > 5000 字符后才调用）。把完整内容写入临时目录 `aid_agent_spill/` 下的文件，返回 `{"file_path": 绝对路径, "full_size": 字符数, "preview": 前 5000 字符, "truncated": bool}`。
- `prefix` 标识来源工具（如 `httpapi_response_`），`suffix` 带扩展名（如 `.json`、`.md`）。
- `meta` 可选元信息（如原 URL、页数），写入同名 `.meta.json`，不进返回值。
- 过期落盘文件由启动清理（`cleanup_stale_spill_files`，默认 24h 老化）。
- 落盘路径在 read/grep 工具的路径白名单内，LLM 可直接回读。

---

## 4. 新工具开发 checklist

- [ ] `description` 为工具说明唯一通道：功能 + 触发场景 + 必要教程/约束，能短则短，与 Field description 及系统提示词模板零重复
- [ ] 不写 `usage_guide`（已停用）
- [ ] 每个 Field description 只描述字段本身，无 few-shot/业务背景
- [ ] 枚举参数用 `Literal`，条件必填用 `model_validator`
- [ ] 文本返回字段有字符上限，超长用 `truncate_text` 并带 `truncated`
- [ ] 全文类返回 > 5000 字符走 `spill_large_content` 落盘闭环，带 file_path
- [ ] 错误返回仅 `{"success": False, "error": sanitize_error(...)}`，无 debug/traceback/kwargs
- [ ] 成功返回只带新信息，无 echo 输入、无纯话术 message
- [ ] 中间 trace / 大块内容走单独持久化或文件路径，不进返回值
- [ ] 单元测试覆盖截断边界、落盘闭环与脱敏白名单

## 5. 旧工具重构判定

按本规范 §1 六条契约逐条核对，凡违反即为待修项；剩余待修项的范围与优先级见[开发计划 2026-10 复核结论](./tool-overall-optimization-dev-plan.md)。重构必须复用 §3 公共 helper，不得各写一套。
