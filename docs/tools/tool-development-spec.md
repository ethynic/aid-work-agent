# 工具开发规范（Tool Development Spec）

> 配套设计文档：[`tool-overall-optimization-design.md`](./tool-overall-optimization-design.md) #2 统一规范。
>
> 本文档是**可执行的开发规范**，既是新工具开发的 checklist，也是旧工具（Phase 2+）重构的判定依据。后续所有工具改造和新工具开发**必须遵循**。
>
> 公共能力实现见 [`src/tools/_helpers.py`](../../src/tools/_helpers.py)（`truncate_text` / `sanitize_error`）。

## 0. 规范目标

LLM 每轮对话都消耗两类工具 token：① **工具 schema**（`description` + 参数 Field description + `usage_guide`）注入 system prompt，全对话常驻；② **`execute()` 返回值**进入 tool_result，逐轮累积。本规范从这两条入口统一约束，最大化单位 token 的信息密度，并杜绝把敏感/调试信息灌入对话上下文。

核心心法：**schema 只描述「工具是什么、参数长什么样」；返回值只带「调用方无法从调用本身得知的新信息」；教程和诊断走各自的归属地**。

---

## 1. 七条核心契约

| 维度 | 契约 | 反例（禁止） |
|------|------|------|
| `description` | 一句话功能 + 触发场景，**≤ 80 字符** | 教程 / 操作菜单 / 调用示例 / 强制指令 |
| Field description | 只描述字段本身（类型/含义/默认值/枚举值） | few-shot / 调参示例 / 业务背景 |
| `usage_guide` | 集中承载调用教程、cp 注册提醒、负面清单；与 schema 不重复 | 与 description / Field description 内容重叠 |
| 文本返回字段 | 统一字符上限（单字段 ≤ 2000、全文类 ≤ 5000），超出返回**截断 + `truncated: true`** | 无上限裸传全文 / 表格 / chunk |
| 错误返回 | 仅 `{"success": False, "error": "<脱敏一句话>"}` | `debug` 字段 / `str(e)` 原文 / traceback / `kwargs` 回显 |
| 成功返回 | 只带新信息（路径/行数/count/id/状态） | echo 输入参数 / 纯话术 `message` |
| 参数约束 | 条件必填用 `model_validator` + `Literal` 枚举，让 schema 自描述 | 全标 Optional + 运行时手动校验报错 |

下面逐条展开。

---

### 1.1 `description`：一句话工具卡片

- **做什么**：一句话说明工具功能 + 触发场景（何时该被调用）。
- **长度**：≤ 80 字符。
- **禁止**：操作菜单罗列、调用示例、参数说明、cp 注册提醒、「必须先向用户确认」这类 system-prompt 级强制指令。

```python
# ✅ 好：功能 + 触发场景，简短
description = (
    "将文本、Markdown 或 HTML 转换为一张长图(PNG)。"
    "适用于需要把富文本/表格/样式化内容以图片形式呈现给用户的场景。"
)

# ❌ 坏：把 9 种操作菜单 + cp 注册说明塞进 description（pdf_process 现状）
description = "PPT生成工具...操作类型：generate/ocr/insert_page...(1100 字符)"
```

> 教程类内容一律收归 `usage_guide`。description 每轮注入，是固定 token 成本，必须压到最短。

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

### 1.3 `usage_guide`：教程集中地

- 集中放：如何调用、参数怎么填、不要怎么用（负面清单）、cp 注册提醒。
- 仍常驻注入 system prompt，但**集中管理、避免与 description / Field description 内容重复**。
- 空字符串表示无指南；不要刻意留空又把内容塞进 description。

### 1.4 文本返回字段：统一截断 + 标记

- 单字段（如 chunk 文本、单个 API 响应体）：≤ **2000** 字符。
- 全文类（如整篇 markdown、整页 markdown、文档全文）：≤ **5000** 字符。
- 超出时：返回截断后的文本 + `"truncated": true` 标记，让 LLM 知道需要按需续读。
- 统一用公共 helper `truncate_text(text, limit)` 实现截断（见 §3）。

```python
from src.tools._helpers import truncate_text

text, truncated = truncate_text(raw_text, limit=5000)
return {"success": True, "preview": text, "truncated": truncated, "total_len": len(raw_text)}
```

> 读取类工具（word/excel/pdf 的 to_md、read）回塞全文的设计应改为「返回行数/页数等元信息 + 可选 preview（前 N 行），全文走文件路径让 LLM 按需 `read`」。

### 1.5 错误返回：脱敏 + 极简

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

### 1.6 成功返回：只带新信息

- 只返回 LLM 无法从调用本身得知的**新信息**：生成的路径、行数、count、id、状态、truncated 标记。
- **禁止 echo 输入参数**（如 `email_send` 回显 to/cc/subject、`ai_call` 回显 phone/lead_id）。
- 删除纯话术 `message`（如「操作成功」「已为您生成」），或仅保留真正承载新信息的 message。
- 内部 trace / 中间步骤走单独持久化，不进返回值（参照 `analyze_data`）。

### 1.7 参数约束：让 schema 自描述

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

新工具与重构都应参照这三个标杆，它们分别示范了规范的不同侧面。

### 标杆 A：`x_to_image`（描述短 + 返回纯元信息）
- `description` ~80 字符，功能 + 触发场景一句话说完。
- 成功返回**只带元信息**：`image_path` / `image_name` / `file_size` / `width` / `height` / `truncated` / `renderer`，不回塞图片内容本身、不 echo 输入。
- 超长内容用 `truncated` 标记。**这是「成功返回」契约的范本。**

### 标杆 B：`ppt_process`（错误脱敏 + Literal 枚举）
- 错误处理用 `_format_user_error` 白名单脱敏：异常按类型映射到固定安全消息；个别类型（`HtmlExportSecurityError`/`TemplateAnalysisError`）再用 `set` 白名单放行**确定的少量消息字符串**，其余一律兜底脱敏。绝不返回 `str(e)` 原文。
- 参数约束用 `Literal` 枚举（`content_type` / `export_mode`）+ `field_validator`（trim 空串）+ `model_validator`（跨字段必填校验）。
- **这是「错误返回」+「参数约束」契约的范本。** 其脱敏逻辑已在 Phase 1 提取为公共能力 `sanitize_error`（Phase 16 改造时切换调用）。

### 标杆 C：`analyze_data`（trace 单独持久化）
- 内部 `_steps` / `SpanRecord` / `TraceRecord` **只走 `trace_persist` 单独持久化，不放进对外返回值**，避免把中间过程灌入 tool_result。
- table 输出只带 `preview`（前 3 行）+ 列名 + 行数等元信息，全表走 artifact/文件。
- `analysis_meta` 全是小字段（行数/列数/类型），不回塞大块数据。
- **这是「返回值不灌大块内容」+「trace 不进返回值」契约的范本。**

---

## 3. 公共能力（`src/tools/_helpers.py`）

后续所有 Phase 改造复用这两个函数，避免每个工具各写一套截断/脱敏。

### `truncate_text(text, limit, suffix="...")`
截断文本到 `limit` 字符。返回 `tuple[截断后文本, 是否截断]`。未超长原样返回 `(原文本, False)`；超长则截到 `limit` 并拼 `suffix`，返回 `(截断后, True)`。纯函数、无副作用，`None`/空串安全。

> 使用约定：返回字典时把 `truncated` 一并带出，方便 LLM 判断是否续读。

### `sanitize_error(error, safe_messages=None, fallback="操作失败，请稍后重试")`
通用错误脱敏。
- `error`：异常对象或字符串。
- `safe_messages`：`{异常类型: 安全消息}` 映射，支持三种形态（调用方按工具自身白名单传入）：
  1. `{类型: str}` —— 命中该类型异常，一律返回此固定安全消息。
  2. `{类型: set[str]}` —— 命中类型后，仅当 `str(error)` 在集合内才透传该消息，否则继续匹配/fallback（对标 `ppt_process` 的 `safe_security_errors` 写法）。
  3. `{类型: (set[str], str)}` —— set 形态的增强版：命中集合透传消息，未命中走 tuple 第二项「分类兜底消息」（对标 `ppt_process` 的 `HtmlExportSecurityError` 处理：命中固定集合透传，否则返回「HTML 文件未通过安全检查」）。
- `fallback`：白名单都不命中时的兜底脱敏消息。
- **核心原则**：绝不返回 `str(e)` 原文、traceback、kwargs。只有命中白名单的消息才透传，其余一律 fallback。

> `ppt_process._format_user_error` 计划在 Phase 16 改造为调用本函数；Phase 1 阶段保持原样，只读不改。

---

## 4. 新工具开发 checklist

- [ ] `description` ≤ 80 字符，无教程/示例/指令
- [ ] 每个 Field description 只描述字段本身，无 few-shot/业务背景
- [ ] 教程/负面清单/cp 注册提醒放进 `usage_guide`，不进 description
- [ ] 枚举参数用 `Literal`，条件必填用 `model_validator`
- [ ] 文本返回字段有字符上限，超长用 `truncate_text` 并带 `truncated`
- [ ] 错误返回仅 `{"success": False, "error": sanitize_error(...)}`，无 debug/traceback/kwargs
- [ ] 成功返回只带新信息，无 echo 输入、无纯话术 message
- [ ] 中间 trace / 大块内容走单独持久化或文件路径，不进返回值
- [ ] 单元测试覆盖截断边界与脱敏白名单

## 5. 旧工具重构判定（Phase 2+）

按本规范 §1 七条逐条核对，凡违反即为该 Phase 待修项；优先级见设计文档 #2 的「实施建议」（P0 安全/高频黑洞 → P3 低风险清理）。重构必须复用 §3 公共 helper，不得各写一套。
