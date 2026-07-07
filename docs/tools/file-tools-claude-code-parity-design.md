# 文件工具对齐 Claude Code 水准设计

> 配套：[`large-content-retrieval-design.md`](./large-content-retrieval-design.md)（grep 工具在该文档）、[`tool-overall-optimization-design.md`](./tool-overall-optimization-design.md)
>
> 目标：将 read/write/edit/cp 提升到 Claude Code harness 同类工具水准，修正 offset 行号语义、补齐 edit 批量替换能力。grep 工具设计见配套文档。

## 1. 现状 vs Claude Code 对照

| 能力 | Claude Code | 本项目现状 | 差距 |
|------|------------|-----------|------|
| read offset 语义 | **1-based**（offset=1 读第1行，与显示行号一致） | **0-based**（offset=0 读第1行）`read_tool.py:34` | **不一致**，LLM 要心算转换，易错 |
| read 行号显示 | cat -n 1-based | 已一致 `read_tool.py:432-456` | — |
| read 分页 | offset+limit | 已支持 | — |
| read 上限 | 2000 行 | 2000 行 `read_tool.py:23` | — |
| read 编码检测 | 基础 | **更强**（BOM+chardet+多编码遍历） | 本项目优势 |
| read 图像 | 支持（多模态） | **不支持** | 重大差距（暂不在本设计范围，单独立项） |
| edit 唯一性校验 | string_replace 强制唯一 | 已对齐 `edit_tool.py:162-171` | — |
| edit 原子写入 | 是 | 已对齐 `edit_tool.py:308-365` | — |
| edit replace_all | **有**（批量替换全部匹配） | **无**（强制唯一，无法批量） | **差距** |
| edit 多模式 | 仅 string_replace | 多 2 模式（section/lines） | 本项目增强 |
| cp（文件交付） | 无 | 独创 | 本项目特有，保留 |

## 2. 设计目标

### 2.1 offset 改 1-based（breaking change，重点）

**改动**：read/edit 的 `offset` 参数语义从「0-based 偏移量」改为「1-based 行号」。

| 场景 | 改造前（0-based） | 改造后（1-based） |
|------|------|------|
| 读第 1 行 | `offset=0` | `offset=1` |
| 读第 50 行起 100 行 | `offset=49, limit=100` | `offset=50, limit=100` |
| next_hint 提示 | "继续: offset=2000" | "继续: offset=2001" |

**动机**：agent 从 read 返回的 cat -n 看到的是 1-based 行号（行 1、行 2...），但填 offset 要做 -1 心算。国内 LLM 这一步算错率高，导致读到错误段落。改 1-based 后，**所见即所填**。

**breaking change 影响范围**：
- read_tool.py：`_read_range` 的 offset 语义（`read_tool.py:308-352`）
- edit_tool.py：`replace_lines` 的 offset 语义（`edit_tool.py:67,238-271`）
- read_tool.py 的 next_hint 文案（`read_tool.py:346-350`）
- description / Field description 文案（`read_tool.py:34`、`edit_tool.py`）
- **现有 skill 中调用 read/edit 的地方**（需全局搜索适配）

**兼容策略**：
- 不做双模兼容（0-based+1-based 并存会让 LLM 更困惑，违背「schema 自描述」原则）
- offset 参数保留原名，仅改语义。内部统一 `_offset_to_index(offset) = offset - 1` 转换
- 改造时全局搜索 skill 中的 `offset=` 调用点逐一核对（skill 是文档非代码，主要是 SKILL.md 里的示例）

### 2.2 edit 新增 replace_all

**改动**：`replace_string` 模式新增 `replace_all: bool` 参数。

| 参数 | 默认 | 说明 |
|------|------|------|
| `replace_all` | False | True 时替换 old_string 的全部匹配（不校验唯一）；False 时保持现有「强制唯一」行为 |

**动机**：批量替换场景（如全文统一术语、批量改字段名），当前 replace_string 强制唯一，agent 只能逐个替换，低效。Claude Code 标配此能力。

**安全**：
- `replace_all=True` 时跳过唯一性校验，但仍报告替换次数
- `replace_all=False`（默认）行为完全不变，向后兼容

### 2.3 read/edit description 瘦身 + 文案更新

按 token 优化规范（description ≤80 字符），把 read 当前 ~19 行 description（含 3 种模式教程、SKILL_ROOT 注释）瘦身，教程迁 usage_guide。同时更新 offset 语义说明为 1-based。

## 3. 影响范围

### 修改
| 文件 | 改动 |
|------|------|
| `src/tools/file/read_tool.py` | offset 1-based、next_hint 文案、description 瘦身 |
| `src/tools/file/edit_tool.py` | replace_lines offset 1-based、replace_string 新增 replace_all、description 瘦身 |
| 相关 skill 的 SKILL.md | offset 调用示例从 0-based 改 1-based（全局搜索适配） |

### 不改
- write/cp：已达 Claude Code 水准（write 是增强版，cp 是独创），仅做 description token 优化（归入总体优化 Phase 3）
- read 图像支持：重大差距，单独立项，不在本设计范围

## 4. 验收标准

- [ ] read `offset=1` 读到第 1 行（原 `offset=0` 行为）
- [ ] next_hint 的 offset 值为 1-based 行号
- [ ] edit `replace_lines offset=1` 操作第 1 行
- [ ] edit `replace_string replace_all=True` 能批量替换，返回替换次数
- [ ] edit `replace_all=False`（默认）行为不变（唯一性校验仍生效）
- [ ] 全局 skill 中无残留 0-based offset 示例
- [ ] description ≤80 字符

## 5. 风险

- **breaking change**：现有 skill 的 offset 调用若漏适配会读到错误行。缓解：改造时全局搜索 `offset=` 在 `src/skills/` 和 `.agents/skills/`，逐一核对；skill 是 Markdown 文档非运行代码，主要影响 LLM 读到的示例，修复成本低。
