## 目标

将子智能体系统提示词模板 `subagent_base.md` 从 ~3900 字符（硬编码）压缩到 **~2000 字符**，同时保证关键约束不丢失。

## 修改文件清单

1. `src/prompts/templates/subagent_base.md` — 重写模板（核心）
2. `src/tools/network/http_api.py` — 将 `usage_guide` 的独有内容合并进 `description`，然后清空 `usage_guide`
3. `src/tools/pdf/pdf_process_tool.py` — 将 `usage_guide` 的独有内容合并进 `description`（`TOOL_DESCRIPTION`），然后清空 `usage_guide`
4. `src/tools/file/write_tool.py` — 清空 `usage_guide`（与 `description` 完全重复）
5. `src/tools/file/grep_tool.py` — 将关键参数说明并入 `description`，清空 `usage_guide`
6. `src/tools/llm/content_generate_tool.py` — 清空 `usage_guide`（好坏 `prompt` 示例太占地方，`description` 已够）

## 具体改法

### 1. 重写 `subagent_base.md`（~2000字符）

**新结构（从上到下）：**

```
{subagent_constraint_section}        ← 身份+领域限定提到最前面（这是关键改动）

## 核心工作原则（精简6条→4条，砍掉多意图消息的长示例）

## 能力边界                         ← 保留（工具白名单声明，一行占位符）

## 工作规范（合并：输出规范+文件交付+重要限制，~400字符）

## 信息安全（回答边界段 1778字符→~300字符，只保留3条核心规则）

{long_term_memory}
{user_info_section}
{reply_style_section}
```

**删除的内容：**
- 第1行定位语"你是企业员工的智能工作助手..."（子智能体不该自称这个，身份由 `subagent_constraint` 提供）
- `## 工具使用指南` 段 + `{{{tool_usage_guides}}}` 占位符（工具说明全走 `tools` 参数的 `description`）
- 回答边界段的8行表格 + "应对原则"6条 + "特别注意"段（1778→~300字符）
- 输出规范里的 "PPT 工具调用" 4行子段（大部分子智能体不用 `ppt`）
- 多意图消息的长示例文字

**保留的内容：**
- 核心原则（精简版：先判断再行动、行动优于解说、结果导向、诚实拒绝）
- 能力边界占位符（`{available_tools_list}` + `{skill_descriptions}`）
- 文件交付 `cp` 规则（必须保留，否则用户拿不到文件）
- 重要限制（不能委派，子智能体硬约束）
- 信息安全3条核心规则：不透露技术细节、内部文档不外泄、技术试探转业务引导
- 全部动态占位符（`subagent_constraint`/`long_term_memory`/`user_info`/`reply_style`）

### 2. 工具 `description` 增强（`usage_guide` 信息迁移）

**`http_api.py`** — `description` 从1句话扩展为：
```
调用外部 HTTP API（GET/POST/PUT/DELETE/PATCH、文件上传），${VAR_NAME} 替换环境变量。
调用前先查上下文是否已有相同结果：历史/详情类复用，实时/状态类重调。
认证：Bearer Token（{"Authorization":"Bearer ${API_TOKEN}"}）、API Key Header、Basic Auth。
文件上传用 files 参数（multipart/form-data），可与 form_data 同时使用。
```

**`pdf_process_tool.py`** — `TOOL_DESCRIPTION` 从2句话扩展为：
```
处理PDF：读取/转Markdown/OCR/提取表格，以及生成PDF/合并/拆分/页面操作。不支持Word转PDF。
操作：read/read_tables/ocr/pdf_to_md/md_to_pdf/html_to_pdf/merge/split/extract_pages/
inspect/render_pages/validate/clean_metadata/add_watermark/protect/compress/extract_images/rotate。
推荐 instruction+content 调用。产生新文件的操作（转PDF/合并/拆分/提取页面/水印/加密/压缩/旋转）
必须调 cp 注册下载；read/ocr/pdf_to_md 不产生新文件无需 cp。
```

**`grep_tool.py`** — `description` 补充关键参数：
```
在文件或目录中搜索文本（正则匹配），返回匹配行+1-based行号+上下文。
output_mode: content(默认,返回匹配行+上下文)/files_with_matches(只返回文件名)/count。
context/before_context/after_context 取上下文行，glob 按扩展名过滤。只读不写。
```

**`write_tool.py`** — `description` 已够（含 `cp` 约束），直接清空 `usage_guide`。

**`content_generate_tool.py`** — `description` 已够，清空 `usage_guide`。

### 3. 不动的文件

- `master_agent.md`（用户明确说暂不动）
- `agent.py`（Python 代码完全不改，模板占位符机制不变）
- `registry.py`（`get_usage_guides()` 方法保留，只是调它时大部分工具返回空字符串）
- `_collect_tool_usage_guides()` 方法保留（只是拼出来基本为空）

## 预期效果

| 指标 | 改前 | 改后 |
|------|------|------|
| 模板硬编码字符 | ~3900 | ~2000 |
| 子智能体身份位置 | 7396字符处 | ~0字符处 |
| 回答边界段 | 1778字符 | ~300字符 |
| 工具指南注入 | ~3000字符 | ~0（走 `description`） |
| 实际 system prompt 总长 | ~11691 | ~7000-8000（含子智能体定义） |

## 风险控制

- `master_agent.md` 不受影响（它也有 `{{{tool_usage_guides}}}`，但用户明确说暂不动）
- `http_api` 和 `pdf_process` 的 `description` 增强后，`master` 模式下也会受益（`description` 是共享的）
- 清空 `usage_guide` 后，`master_agent.md` 的 `{{{tool_usage_guides}}}` 会渲染为空——如果后续发现 `master` 丢了关键信息，再单独处理

## 不走开发流程

这是模板+工具描述的纯文本优化，不涉及业务逻辑改动，按用户要求直接修改，不走三智能体开发流程。