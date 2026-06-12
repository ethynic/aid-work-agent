# 文件操作工具集重新设计 v2 — 开发文档与开发计划

> **版本**: v2.0
> **创建**: 2026-06-11
> **状态**: 待开发
> **设计文档**: [file_tools_redesign_v2.md](file_tools_redesign_v2.md)
> **ideas.md 索引**: [docs/ideas.md](../../ideas.md) 条目 21「文件操作工具集重新设计 v2」
> **目标场景**: 让 `guizang-ppt-skill` 在 Agent 循环中端到端跑通；同时清理旧工具职责膨胀问题

---

## 一、开发目标回顾

把现有臃肿的 `file_read` / `file_write` / `file_list` 三件套拆为命名对齐 Claude Code 的四个工具：`read` / `write` / `edit` / `cp`。详细设计见 [设计文档](file_tools_redesign_v2.md)。本开发文档负责把设计落地为可执行的开发计划。

### 1.1 范围内

- 新建 `read` / `write` / `edit` / `cp` 四个工具类
- 删除 `FileListTool`，从 `agent.py`、`__init__.py` 移除注册
- 同步迁移所有外部引用点（agent.py 进度事件、前端 useAgent.ts、channel_routes.py、main.py、tests、scripts、competitor-research SKILL/SUBAGENT、trade-specialist SUBAGENT）
- 跑通 guizang-ppt-skill 端到端
- `use_skill_tool.py` 引导语已包含新映射（无需改动，只需复核）

### 1.2 范围外（设计文档已明确不做）

- 不做 grep / 搜索工具
- 不做流式写入
- 不保留旧工具名别名
- 不动 SKILL.md 正文中的 `cp` / `Read` / `Edit` 命令式语言（用 use_skill 引导语翻译）
- 不修改 `skill_substitutions.py` 的 `<SKILL_ROOT>` 替换机制（已可用）
- 不动 PPT/Word/Excel reader 路由逻辑（仅做工具名迁移）

---

## 二、当前代码现状核查（开发前基线）

> 这是动手前对仓库的真实快照。开发完成后必须保证下列每一项都达成"目标状态"。

### 2.1 工具层

| 文件 | 当前状态 | 目标状态 |
|---|---|---|
| `src/tools/file/file_reader_tool.py` | 含 `FileReaderTool`(name=`file_read`) + `FileListTool`(name=`file_list`) + `create_file_tools()` | 重构为 `ReadTool`(name=`read`)，删除 `FileListTool` 和 `create_file_tools` |
| `src/tools/file/text_file_writer.py` | `FileWriteTool`(name=`file_write`)，单类承载 overwrite/append/replace_section/copy 四种语义，max_tokens=65536 | 重构为 `WriteTool`(name=`write`)，仅保留 overwrite/append；删除 `source_file_path` / `section_start` / `section_end` 字段；max_tokens 维持 65536（设计文档"提升到 8192"针对更早版本，现状已更高，不再下调） |
| `src/tools/file/edit_tool.py` | 不存在 | **新建** `EditTool`(name=`edit`)，三种 mode：`replace_string` / `replace_section` / `replace_lines` |
| `src/tools/file/cp_tool.py` | 不存在 | **新建** `CpTool`(name=`cp`)，源路径限制在项目根目录内 |
| `src/tools/file/__init__.py` | 导出 `FileReaderTool` / `FileListTool` / `create_file_tools` / `FileWriteTool` | 改为导出 `ReadTool` / `WriteTool` / `EditTool` / `CpTool` |

### 2.2 Agent 注册（`src/core/agent.py`）

| 行号 | 当前代码 | 目标代码 |
|---|---|---|
| 308 | `from src.tools.file.file_reader_tool import FileReaderTool, FileListTool` | `from src.tools.file.read_tool import ReadTool` |
| 311 | `from src.tools.file.text_file_writer import FileWriteTool` | `from src.tools.file.write_tool import WriteTool`<br>`from src.tools.file.edit_tool import EditTool`<br>`from src.tools.file.cp_tool import CpTool` |
| 328-329 | `register(FileReaderTool())` / `register(FileListTool())` | `register(ReadTool())`（删除 FileListTool） |
| 332 | `register(FileWriteTool())` | `register(WriteTool())` / `register(EditTool())` / `register(CpTool())` |
| 1553/1566/1587 | `file_write_tool = registry.get_tool("file_write")` 注入 user_id/tenant_id | `file_write_tool = registry.get_tool("write")`（变量名可保留，仅改字符串） |
| 2265 | `elif tool_name == "file_read":` 进度预览分支 | `elif tool_name == "read":` |

**user_id / tenant_id 注入**：`WriteTool` 和 `CpTool` 都会注册下载、需要 `_resolve_upload_dir`，所以两者都需要注入 user_id/tenant_id。开发时把现有 `file_write_tool` 单变量改为遍历 `("write", "cp")` 都注入。

### 2.3 前端 `frontend/src/composables/useAgent.ts`

| 行号 | 当前 | 目标 |
|---|---|---|
| 218 | `const downloadToolNames = ['register_download_file', 'file_write']` | `['register_download_file', 'write', 'cp']` |
| 247 | `else if (toolName === 'file_read')` 预览分支 | `else if (toolName === 'read')` |
| 328 | `case 'file_read':` display name 分支 | `case 'read':`（建议同时为 `write` / `edit` / `cp` 增加 display name 分支） |

### 2.4 渠道 / 主入口

| 文件 | 行号 | 当前 | 目标 |
|---|---|---|---|
| `src/saas/api/channel_routes.py` | 188 / 333 / 1088 | `toolName in ("register_download_file", "file_write")` | `("register_download_file", "write", "cp")` |
| `src/main.py` | 1627 | `download_tool_names = {"register_download_file", "file_write"}` | `{"register_download_file", "write", "cp"}` |

### 2.5 Skill / Subagent 文档

| 文件 | 现状 | 目标 |
|---|---|---|
| `src/skills/competitor-research-1.0.0/SKILL.md` | 第 446/625/948 行 `file_write(generate_prompt=...)` | 全文 `file_write(` → `write(` 替换（3 处 + 关联示例） |
| `subagents/competitor-research/SUBAGENT.md` | 第 92/126/134/137/156/159/285/308 行多处 `file_write` | 全文 `file_write` → `write` 替换 |
| `tests/unit/test_competitor_research.py` | 第 123 / 148 行断言 `"file_write" in body` | 改为 `"write"` |
| `subagents/trade-specialist/SUBAGENT.md` | 第 210 行 `禁止调用 file_read 等文件读取工具` | 改为 `禁止调用 read 等文件读取工具` |

### 2.6 Skill 加载与 `<SKILL_ROOT>` 替换（**无需改动**）

- `src/core/skill_substitutions.py:55-57` 已实现 `<SKILL_ROOT>` → 绝对路径替换
- `src/skills/guizang-ppt-skill/SKILL.md` 中 `cp "<SKILL_ROOT>/assets/template.html" ...` 替换后为绝对路径，直接传给 `cp` 工具即可
- `src/skills/guizang-ppt-skill/assets/template-swiss.html` 第 1226 / 1332 行已有 `<!-- SLIDES_HERE · ... -->` 和 `<!-- END_SLIDES -->` 标记，`edit` 工具的 `replace_section` 模式直接命中

### 2.7 `use_skill_tool.py` 引导语（**已就绪，仅需复核**）

`src/tools/skill/use_skill_tool.py:85-109` 已经包含 `cp` / `read` / `edit` / `write` 的映射表，开发期间不需要改动，但开发完成后复核：链接是否还提到 `file_write`、措辞是否准确。

### 2.8 测试脚本

| 文件 | 现状 | 目标 |
|---|---|---|
| `scripts/test_ppt_skill_agent.py` | 第 190-238 行硬编码 `file_read` / `file_write` 工具名做统计 | 改为统计 `read` / `write` / `cp` / `edit`；新增 `cp` 和 `edit` 调用计数 |
| `scripts/test_guizang_ppt_skill_compat.py` | 第 377-388 行打印文案 `file_write` / 检查引导语 `file_write in content` | 改为 `write` |

### 2.9 已删除测试文件（git status 中的 `D`）

| 文件 | 处理 |
|---|---|
| `tests/unit/tools/test_file_read.py`（已删除） | 用新 `test_read_tool.py` 替代 |
| `tests/unit/tools/test_file_write.py`（已删除） | 用新 `test_write_tool.py` / `test_edit_tool.py` / `test_cp_tool.py` 替代 |

### 2.10 其他

| 文件 | 处理 |
|---|---|
| `docs/tools/text-file/file_write_content_generate_optimization.md` | 历史设计文档，仅在头部加"已被 v2 取代"标注，不改正文 |
| `docs/tools/text-file/text_file_generator_design.md` | 同上 |
| `docs/tools/FILE_TOOLS_GUIDE.md` | 用户文档，统一更新为新工具名 |
| `docs/tools/excel/*` | 仅在 README/索引里出现 `file_write`/`file_read` 文字，按上下文判断是否需要改名 |
| `tests/unit/test_skill_substitutions.py:57` | 测试用例字面量 `"Use file_read to read the template"` —— 测的是 `<SKILL_ROOT>` 替换，与工具名无关，**保留** |

---

## 三、开发阶段划分

> 顺序经过评审：先在隔离的新文件里建好工具，再做注册切换，再扫外部引用，最后跑端到端。每阶段都有可独立验收的产物。

### Phase 0：开发准备（≈ 0.5h）

**产出**：开发分支、CLAUDE.md 已读、设计文档与开发计划对齐。

**步骤**：
1. 切开发分支 `feature/file-tools-v2`
2. 重读设计文档第二章（工具职责）与第十一章（关键决策）
3. 在本文档第二章"现状核查"中标记任何与设计文档不一致之处，与维护者对齐

**验收**：分支已建；本文档为最新权威依据。

---

### Phase 1：新建 `read` 工具（≈ 2h）

**产出**：`src/tools/file/read_tool.py`，类名 `ReadTool`，name=`read`。

**改动**：
1. 从 `file_reader_tool.py` 复制 `FileReaderTool` 整体逻辑（路径解析、Word/Excel/PPT 路由、`_read_range`、`_read_section`、`_format_with_line_numbers`、编码检测全套），改：
   - 类名 `FileReaderTool` → `ReadTool`
   - `name = "file_read"` → `name = "read"`
   - `InputModel = FileReaderInput` → `InputModel = ReadInput`
   - 删除 `ReadInput.encoding` 字段（`execute` 内部继续支持编码自动检测，但不接收 LLM 传入的 encoding）
   - 更新 `description` 为设计文档 §3.1.1 的版本（≤ 20 行）
2. **删除** `FileListTool` 类和 `FileListInput`（不再迁移到新文件）
3. **删除** `create_file_tools()` 工厂（不再需要）
4. 旧文件 `file_reader_tool.py` **保留为薄壳**：仅 `from .read_tool import ReadTool`，并保留 `FileReaderTool = ReadTool` 别名——**仅在 Phase 1~4 过渡期使用**，Phase 5 一并删除

**验收**：
- 新建 `tests/unit/tools/test_read_tool.py`，覆盖：
  - 整文件读取（小文件）
  - 行号范围（offset+limit）
  - 标记定位（section_start + section_end）
  - section_end 不传时读到末尾
  - Word/Excel/PPT 路由命中
  - 不存在文件返回字符串错误
  - 项目根目录外的绝对路径返回错误
- `pytest tests/unit/tools/test_read_tool.py` 全绿
- `file_list` 工具类已不存在于 `src/tools/file/`

---

### Phase 2：新建 `write` 工具（≈ 2.5h）

**产出**：`src/tools/file/write_tool.py`，类名 `WriteTool`，name=`write`。

**改动**：
1. 从 `text_file_writer.py` 复制 `FileWriteTool`，改：
   - 类名 → `WriteTool`，name → `write`
   - `InputModel` 删除 `source_file_path`、`section_start`、`section_end`、`encoding` 字段
   - `mode` 仅保留 `overwrite` / `append` 两值（删除 `replace_section`）
   - `execute()` 删除复制分支和 replace_section 分支
   - 更新 `description` 为设计文档 §3.2.1 版本
2. `_generate_content` 维持现有 `max_tokens=65536`（设计文档中"提到 8192"是对更早的 4096 版本，现状已远超，不回调）
3. 旧文件 `text_file_writer.py` **保留薄壳** `FileWriteTool = WriteTool` 别名，Phase 5 删除
4. `__init__.py` 同时导出新旧名字（过渡期）

**验收**：
- 新建 `tests/unit/tools/test_write_tool.py`，覆盖：
  - overwrite 新文件
  - overwrite 已存在 + `overwrite=False` 报错
  - append 追加 / append 文件不存在自动创建
  - `generate_prompt` 内部生成（mock llm_gateway）
  - JSON 格式校验
  - 后缀白名单 / FORBIDDEN_EXTENSIONS 拒绝
  - 自动注册下载（mock redis_client）
  - `mode="replace_section"` 报错（已删除该模式）
  - 同时传 `content` 和 `source_file_path` 报错（已删除 source 字段）
- `pytest tests/unit/tools/test_write_tool.py` 全绿

---

### Phase 3：新建 `edit` 工具（≈ 3h，最复杂）

**产出**：`src/tools/file/edit_tool.py`，类名 `EditTool`，name=`edit`。

**改动**：
1. 新建文件，实现三种 mode：
   - `replace_string`：精确字符串替换，**强制 old_string 唯一**（多个匹配报错，对齐 Claude Code Edit）
   - `replace_section`：标记行保留，中间内容替换（沿用旧 `_replace_section` 同行/跨行两种处理）
   - `replace_lines`：按 0-based 行号范围替换
2. **关键不变量：先校验再写**。所有计算在内存中完成（`_do_replace_*` 返回 `(new_content, matched_info)`），全部通过后才写临时文件 + `tmp.replace(path)` 原子替换；任何 `EditError` 异常都返回字符串错误，原文件不动
3. 路径解析：沿用 read 的 `_resolve_path`（项目根目录或临时目录），但**目标必须已存在**
4. 自定义异常 `EditError`（仅用于内部信号，不向上抛）

**验收**：
- 新建 `tests/unit/tools/test_edit_tool.py`，覆盖：
  - replace_string 唯一匹配成功
  - replace_string 多匹配报错 + 原文件字节级不变（用 `hashlib.sha256` 验证）
  - replace_string 0 匹配报错
  - replace_string 多行 old_string（Python `str.replace` 天然支持）
  - replace_section 跨行：标记行保留，中间替换
  - replace_section 同行：标记同行时整段（含标记本身）替换
  - replace_section 未找到 section_start 报错
  - replace_lines 正常范围替换
  - replace_lines offset 超出文件行数报错
  - 目标文件不存在报错
  - 失败时临时文件已清理（`path.with_suffix(suffix + ".tmp")` 不残留）
- `pytest tests/unit/tools/test_edit_tool.py` 全绿

---

### Phase 4：新建 `cp` 工具（≈ 1.5h）

**产出**：`src/tools/file/cp_tool.py`，类名 `CpTool`，name=`cp`。

**改动**：
1. 新建文件，从旧 `_execute_copy` + `_resolve_source_path` 提取逻辑
2. `_resolve_source`：源必须在项目根目录内（防穿越，`resolve()` + `relative_to(project_root)`）
3. `_resolve_target`：复用 write 的 `_resolve_and_validate_path`（必须在 `storage/` 输出目录内）
4. 默认 `register_download=True`；不传 `file_path` 时自动分配下载目录路径
5. user_id / tenant_id 注入：和 write 一样支持 `set_user_id` / `set_tenant_id`

**验收**：
- 新建 `tests/unit/tools/test_cp_tool.py`，覆盖：
  - 复制 skill 模板到指定 file_path
  - 复制时不传 file_path（自动分配下载路径）
  - 复制 + register_download=False（保留中间文件到 file_path）
  - 源文件不存在报错
  - 源含 `..` 路径穿越报错
  - 源在项目根目录外（绝对路径）报错
  - 目标已存在 + `overwrite=False` 报错
  - FORBIDDEN_EXTENSIONS 源后缀报错
  - 注册下载 mock redis_client
- `pytest tests/unit/tools/test_cp_tool.py` 全绿

---

### Phase 5：切换 Agent 注册 + 移除旧文件（≈ 1h）

**这是切换点**，前 4 个 Phase 完成后才能执行。建议作为单独 commit。

**改动**：
1. `src/core/agent.py`：
   - 第 308/311 行 import 改名（删除 `FileReaderTool`/`FileListTool`/`FileWriteTool`，新增 `ReadTool`/`WriteTool`/`EditTool`/`CpTool`）
   - 第 328-332 行 register 列表更新（删除 FileListTool，加 EditTool + CpTool）
   - 第 1553-1588 行 user_id/tenant_id 注入：把单变量 `file_write_tool` 改为遍历 `("write", "cp")` 都注入
   - 第 2265 行 `elif tool_name == "file_read":` → `elif tool_name == "read":`
2. `src/tools/file/__init__.py`：删除过渡期别名，仅导出新四个工具
3. **删除** `src/tools/file/file_reader_tool.py` 和 `src/tools/file/text_file_writer.py`（薄壳不再需要）
4. **删除** `tests/unit/test_skill_loader.py` 中可能引用旧类名的部分（按运行报错修正）

**验收**：
- `python -c "from src.core.agent import master_agent; print('ok')"` 不报 ImportError
- `master_agent.tool_registry.get_tool("read")` / `write` / `edit` / `cp` 都能取到
- `master_agent.tool_registry.get_tool("file_read")` 返回 None
- `master_agent.tool_registry.get_tool("file_list")` 返回 None

---

### Phase 6：外部引用同步改名（≈ 1.5h）

**改动清单**：

#### 6.1 后端
| 文件 | 改动 |
|---|---|
| `src/saas/api/channel_routes.py` | 第 188/333/1088 行：`("register_download_file", "file_write")` → `("register_download_file", "write", "cp")` |
| `src/main.py` | 第 1627 行：`{"register_download_file", "file_write"}` → `{"register_download_file", "write", "cp"}` |

#### 6.2 前端
| 文件 | 改动 |
|---|---|
| `frontend/src/composables/useAgent.ts` | 第 218 行 downloadToolNames 增加 `'write'`、`'cp'`；第 247 行 `file_read` → `read`；第 328 行 `case 'file_read':` → `case 'read':`；建议补 `case 'write':` / `case 'edit':` / `case 'cp':` 的 display name 分支 |

#### 6.3 Skill / Subagent 文档
| 文件 | 改动 |
|---|---|
| `src/skills/competitor-research-1.0.0/SKILL.md` | 全文 `file_write(` → `write(`（3 处显式调用 + 关联示例） |
| `subagents/competitor-research/SUBAGENT.md` | 全文 `file_write` → `write`（约 8 处） |
| `subagents/trade-specialist/SUBAGENT.md` | 第 210 行 `file_read` → `read` |

#### 6.4 测试与脚本
| 文件 | 改动 |
|---|---|
| `tests/unit/test_competitor_research.py` | 第 123 / 148 行 `"file_write"` → `"write"` |
| `scripts/test_ppt_skill_agent.py` | 第 190-238 行工具名统计：`file_read`→`read`、`file_write`→`write`；新增 `cp` 和 `edit` 计数分支 |
| `scripts/test_guizang_ppt_skill_compat.py` | 第 377-388 行 `file_write` 文案 → `write`；引导语检查 `file_write in content` → `write in content` |

#### 6.5 用户文档
| 文件 | 改动 |
|---|---|
| `docs/tools/FILE_TOOLS_GUIDE.md` | 重写：read/write/edit/cp 四件套用法 |
| `docs/tools/text-file/file_write_content_generate_optimization.md` | 头部加注「⚠️ 已被 [file_tools_redesign_v2](file_tools_redesign_v2.md) 取代，仅作历史参考」 |
| `docs/tools/text-file/text_file_generator_design.md` | 同上 |

#### 6.6 不需要改的
- `tests/unit/test_skill_substitutions.py:57` 的字面量 `"Use file_read to read the template"` —— 测试的是 `<SKILL_ROOT>` 替换，与工具名无关
- `src/skills/competitor-research-1.0.0/scripts/html_report_merger.py:7` 的注释文字 —— 是说明性文本

**验收**：
- `cd frontend && npm run build` 成功
- `pytest tests/unit/test_competitor_research.py` 通过
- 全仓库 `grep -r "file_write\|file_read\|file_list" src/ frontend/src/ subagents/ scripts/` 应只剩历史 docs（已加取代标注）和 test_skill_substitutions.py 字面量

---

### Phase 7：端到端验证（≈ 2h）

**目标**：guizang-ppt-skill 从"做一个 AI 产品发布 PPT"到 `skill_complete` 全流程跑通。

**步骤**：
1. 启动 `gradio_app.py` 或前端开发服务器
2. 输入测试 prompt："用瑞士风做一个 5 页的 AI 产品发布 PPT"
3. 观察 Agent 循环各轮工具调用，**预期序列**（参考设计文档 §4.1）：
   - `use_skill(skill="guizang-ppt-skill")` → 返回 SKILL.md（已含 `<SKILL_ROOT>` 绝对路径）
   - `cp(source_file_path=".../template-swiss.html", file_path="...")` → 复制模板
   - `edit(mode="replace_string", old_string="<title>...", new_string="...")` → 改 title
   - `edit(mode="replace_string", old_string=":root{...}")` → 改主题色（必要时先 `read(section_start=":root")` 读出来）
   - `read(file_path="...", section_start="<style", section_end="</style>")` → 读样式块确认类名
   - `read(file_path=".../references/layouts-swiss.md", limit=200)` → 选布局
   - `edit(mode="replace_section", section_start="<!-- SLIDES_HERE", section_end="<!-- END_SLIDES", content="...")` → 填充 slides
   - `skill_complete(summary="...")`
4. **关键检查点**：
   - LLM 上下文中**没有完整 100KB HTML**（检查每一轮 tool_result 大小）
   - `cp` 调用至少 1 次
   - `edit` 调用至少 3 次（title + 主题色 + slides）
   - 最终交付 `download_url` 可访问

**验收**：
- PPT 文件生成，前端可下载预览
- 终端运行 `scripts/test_ppt_skill_agent.py` 自动化版本通过
- 终端运行 `scripts/test_guizang_ppt_skill_compat.py` 通过

---

### Phase 8：回归测试 + 文档登记（≈ 1h）

**步骤**：
1. 全量回归 `pytest tests/unit/` 全绿
2. 全量回归 `pytest tests/integration/ -m "not e2e"` 全绿
3. 在 `docs/ideas.md` 条目 21 状态从 `📋 待开发` → `🔧 部分完成`（开发中） → 完成后移到 `ideas_finished.md`
4. 在 `docs/ideas.md` 条目 21 补充「开发计划」链接 → 指向本文档
5. 检查 `.claude/rules/architecture.md`「添加工具」章节是否需要补充新工具

**验收**：
- 所有 pytest 通过
- ideas.md 索引完整
- CLAUDE.md 中工具章节已反映新工具（如有）

---

## 四、端到端测试矩阵

| 用例 | 工具 | 验证点 |
|---|---|---|
| read 整文件 | `read` | 小文件返回 cat -n 格式 |
| read 行号范围 | `read` offset+limit | 0-based offset，返回 1-based 行号 |
| read 标记定位 | `read` section_start+section_end | 含标记行 |
| read 不传 section_end | `read` | 从 start 读到末尾或 limit |
| read Word/Excel/PPT | `read` | 命中专用 reader |
| read 不存在 | `read` | 返回字符串错误 |
| read 项目外绝对路径 | `read` | 返回字符串错误 |
| read `<SKILL_ROOT>` 字面量（未替换） | `read` | 按字面解析，找不到文件返回错误（由 LLM 自行重试） |
| write overwrite 新文件 | `write` | 写入成功，返回 download_url |
| write overwrite 已存在 + overwrite=False | `write` | 报错 |
| write append | `write` | 文件末尾追加 |
| write generate_prompt | `write` mock gateway | 内部 LLM 生成，内容不进入上下文 |
| write JSON 格式错误 | `write` | 报错 |
| write 后缀禁止 | `write` `.exe 等 | 报错 |
| write mode=replace_section（已删） | `write` | 报错 |
| edit replace_string 唯一 | `edit` | 替换成功 |
| edit replace_string 多匹配 | `edit` | 报错 + 原文件 sha256 不变 |
| edit replace_string 0 匹配 | `edit` | 报错 |
| edit replace_section 跨行 | `edit` | 标记保留，中间替换 |
| edit replace_section 同行 | `edit` | 整段替换 |
| edit replace_lines | `edit` offset+limit | 行号范围替换 |
| edit 目标不存在 | `edit` | 报错 |
| edit 失败后无 .tmp 残留 | `edit` | tmp 文件已清理 |
| cp skill 模板 | `cp` | 复制成功，注册下载 |
| cp 不传 file_path | `cp` | 自动分配下载目录 |
| cp 路径穿越 | `cp` `..` | 报错 |
| cp 项目根外绝对路径 | `cp` | 报错 |
| **端到端 guizang-ppt-skill** | 全部 | use_skill → cp → edit(title) → edit(主题色) → read(style) → edit(slides) → skill_complete |

---

## 五、风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 工具名变更影响所有引用点 | 中 | Phase 6 改名清单已穷举；过渡期 Phase 1-4 保留薄壳别名降低风险 |
| LLM 不知道 `cp` 工具对应 skill 文档的 `cp` 命令 | 中 | use_skill 引导语已含映射表（§2.7）；工具 description 明确写"相当于 shell cp" |
| edit replace_string 多匹配时 LLM 困惑 | 中 | 错误信息明确"出现 N 次"，引导加更多上下文 |
| edit 失败破坏原文件 | 高 | 先内存校验后写 tmp + rename（原子） |
| competitor-research skill 改名遗漏导致功能退化 | 中 | Phase 6.3 改完后跑一次竞品研究 e2e（如有 fixture） |
| 前端 useAgent.ts 漏改导致下载卡片不显示 | 中 | npm run build 后手动验证 |
| `<SKILL_ROOT>` 字面量未替换直接传给工具 | 低 | 工具按普通路径解析，找不到文件返回错误；LLM 看到 use_skill 返回的绝对路径后会自行修正 |
| 旧文件薄壳别名长期残留 | 低 | Phase 5 强制删除，CI grep 兜底 |

### 5.1 回滚策略

- Phase 1-4 都是新文件 + 别名，回滚直接 `git revert`
- Phase 5 是切换点，回滚需要 revert 注册 commit；旧文件薄壳仍在，可立即恢复
- Phase 6（外部改名）回滚较繁琐但不会破坏功能（旧 skill 文档在新工具下会报错，但 LLM 会自行重试）
- Phase 7 端到端失败时，先检查 Phase 5/6 是否漏改，而不是回滚工具实现

---

## 六、不做的事（与设计文档对齐）

- 不做 grep / 搜索工具
- 不做流式写入
- 不保留旧工具名别名（Phase 5 删除薄壳）
- 不动 SKILL.md 命令式语言（`cp` / `Read` / `Edit` 由引导语翻译）
- 不修改 `skill_substitutions.py`
- 不动 PPT/Word/Excel reader 实现
- 不引入 `set_active_skill` 状态机制

---

## 七、开发进度跟踪

| Phase | 内容 | 状态 | 完成时间 | 备注 |
|---|---|---|---|---|
| 0 | 开发准备 | ✅ 完成 | 2026-06-11 | 设计与开发计划对齐 |
| 1 | 新建 `read` 工具 | ✅ 完成 | 2026-06-11 | 37 个测试通过；并行智能体产出 |
| 2 | 新建 `write` 工具 | ✅ 完成 | 2026-06-11 | 38 个测试通过；并行智能体产出 |
| 3 | 新建 `edit` 工具 | ✅ 完成 | 2026-06-11 | 26 个测试通过；并行智能体产出 |
| 4 | 新建 `cp` 工具 | ✅ 完成 | 2026-06-11 | 21 个测试通过；并行智能体产出 |
| 5 | 切换 Agent 注册 + 删除旧文件 | ✅ 完成 | 2026-06-11 | master_agent.tool_registry 验证新旧工具切换 |
| 6 | 外部引用同步改名 | ✅ 完成 | 2026-06-11 | 前端 npm build 通过；competitor-research 测试通过 |
| 7 | 端到端验证 | ✅ 完成 | 2026-06-12 | 用户实际运行 guizang-ppt-skill，PPT 成功生成 |
| 8 | 回归测试 + 文档登记 | ✅ 完成 | 2026-06-12 | 相关测试 163 个全绿；归档到 ideas_finished.md |

> 每完成一个 Phase，把对应行的 `⬜` 改为 `✅`，补「完成时间」和「备注」，并在 `docs/ideas.md` 条目 21 同步状态。

---

## 八、关联文档

- **设计文档**：[file_tools_redesign_v2.md](file_tools_redesign_v2.md)
- **ideas.md 索引**：[docs/ideas.md](../../ideas.md) 条目 21
- **guizang-ppt-skill**：`src/skills/guizang-ppt-skill/SKILL.md`
- **测试脚本**：`scripts/test_ppt_skill_agent.py`、`scripts/test_guizang_ppt_skill_compat.py`
- **历史设计文档**（仅作参考）：
  - [text_file_generator_design.md](text_file_generator_design.md)
  - [file_write_content_generate_optimization.md](file_write_content_generate_optimization.md)
