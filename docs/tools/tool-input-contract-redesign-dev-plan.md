# 文件生成类工具入参语义拆分开发计划

> 创建日期：2026-06-30  
> 关联设计：[tool-input-contract-redesign.md](tool-input-contract-redesign.md)  
> 状态：待开发，Word 短期兜底已部分完成

---

## 1. 目标

将 Word/PDF/Excel/PPT 工具从“单 `context` 混合承载指令和正文”升级为“`instruction` + `content` + `content_type` + `output_name` + `file_paths`”的结构化入参，同时保持旧调用兼容。

## 2. 阶段划分

### Phase 0：现状补齐与回归基线

状态：部分完成

已完成：

- Word 工具增加纯 Markdown 确定性路由兜底。
- Word `md_to_word` 执行前剥离指令前缀。
- Word 从 H1-H6 标题推断标题和文件名。
- Word 单测完整通过：`70 passed`。

待补：

- 将当前 Word 兜底逻辑整理为可复用 helper，避免 PDF/Excel 重复实现。
- 增加事故样例回归 fixture，覆盖安顺坝陵河 3 天 2 晚行程。

验收：

- Word 纯 Markdown 首次调用一次成功。
- 指令前缀不进入 Word 正文。

### Phase 1：Word 正式入参扩展

状态：已完成

已完成改动：

1. `WordProcessInput` 增加字段：
   - `instruction`
   - `content`
   - `content_type`
   - `output_name`
2. 新增输入归一化方法：
   - 新字段优先。
   - 旧 `context` 自动拆分为 `instruction/content`。
3. `_resolve_task` 优先使用归一化后的结构化信息。
4. `_handle_md_to_word` 只消费 `content`。
5. 工具描述更新为推荐新调用方式，保留 `context` 兼容说明。
6. 显式 `output_name` 优先于路由器或标题推断出的文件名。

测试：

- `content` 为 Markdown，`instruction=生成Word`。
- 只有旧 `context`。
- `instruction` 与 `content` 同时存在时，不污染正文。
- `output_name` 显式传入时优先生效。

验收：

- 旧调用不破坏。
- 新调用不需要内部 LLM 即可完成确定性 `md_to_word`。
- Word 单测通过：`74 passed`。

### Phase 2：PDF 入参拆分与正文提取

状态：已完成

已完成改动：

1. `PdfProcessInput` 增加字段：
   - `instruction`
   - `content`
   - `content_type`
   - `output_name`
2. 新增输入归一化方法：
   - 新字段优先。
   - 旧 `context` 自动拆分为 `instruction/content`。
3. `md_to_pdf`、`html_to_pdf` 改为优先消费 `content`。
4. 对旧 `context` 做 Markdown/HTML 正文提取，避免指令前缀进入 PDF 正文。
5. 增加确定性规则路由：
   - Markdown + PDF 生成 → `md_to_pdf`
   - HTML + PDF 生成 → `html_to_pdf`
   - `.docx` 附件 + PDF 生成 → `docx_to_pdf`
6. 显式 `output_name` 优先于路由器或标题推断出的文件名。

测试：

- 指令 + Markdown 生成 PDF，指令不进入 PDF。
- 指令 + HTML 生成 PDF，HTML 结构不被破坏。
- 纯 HTML content + instruction 生成 PDF。
- 旧 `context` 兼容。
- DOCX 附件 + PDF 生成意图确定性路由。

验收：

- PDF 转换类生成不再直接消费混合 `context`。
- PDF 单测通过：`pytest tests/unit/tools/test_pdf_tool.py tests/unit/tools/test_pdf_validation.py tests/unit/tools/test_pdf_p0_contracts.py tests/unit/tools/test_pdf_p1_contracts.py -q`，132 passed。

### Phase 3：Excel export 正文隔离

状态：已完成

已完成改动：

1. `ExcelProcessInput` 增加：
   - `instruction`
   - `content`
   - `content_type`
   - `output_name`
2. `export` 数据来源改为：
   - `params.data`
   - `content`
   - 从旧 `context` 提取出的数据正文
3. Markdown/CSV/JSON 数据提取与校验独立封装。
4. 对 `context` 或 `instruction` 只有意图、无数据的场景继续返回 `needs_data=True`。
5. 显式 `output_name` 优先于路由器推断的文件名。

测试：

- 指令 + Markdown 表格导出 Excel。
- 指令 + CSV 导出 Excel。
- 指令 + JSON 数组导出 Excel。
- 旧 `context` 混合指令 + Markdown 表格时，导出数据不包含指令前缀。
- CSV 附件 + Excel 转换意图继续走附件转换，不误报缺少数据。
- 只有“帮我导出 Excel”但无数据时返回 `needs_data=True`。

验收：

- Excel 导出数据不包含自然语言指令。
- Excel 单测通过：`pytest tests/unit/tools/test_excel_tool_routing.py -q`，12 passed。

### Phase 4：PPT 标题与大纲拆分

状态：待开发

改动：

1. `PptProcessInput` 增加：
   - `instruction`
   - `content`
   - `content_type`
   - `output_name`
2. `plan_from_content` 优先使用 `content`。
3. 标题提取优先级：
   - `output_name` 去扩展名
   - Markdown H1/H2
   - planner 返回 title
   - fallback 默认标题
4. 避免使用混合 `context[:30]` 作为标题。

测试：

- “帮我生成 PPT” + Markdown 大纲，标题来自大纲。
- 纯主题生成 PPT 行为不变。
- 模板 PPT + content 大纲。

验收：

- PPT 标题不再包含“帮我生成”等指令前缀。

### Phase 5：Agent 与子智能体提示更新

状态：待开发

改动：

1. 更新主 Agent 工具使用指南：
   - 文件生成优先传 `instruction + content`。
   - 不再推荐把指令和正文拼成一个 `context`。
2. 更新旅游顾问子智能体：
   - 生成 Word 时使用 `instruction` 描述目的。
   - 使用 `content` 传完整 Markdown 行程。
3. 检查其它子智能体或技能中直接调用 Word/PDF/Excel/PPT 的提示。

测试：

- 旅游顾问确认行程后首次调用 Word 一次成功。
- 工具失败时不得声称已生成。

验收：

- Agent 新调用优先走结构化入参。
- 旧调用仍可兼容。

## 3. 兼容与发布策略

1. 第一版只新增字段，不删除 `context`。
2. 所有工具保留 `context` 兼容至少一个大版本。
3. 日志记录旧调用拆分结果，便于观察迁移效果。
4. 若正文提取不确定，不静默猜测，返回明确错误提示。

## 4. 风险与回滚

风险：

- 正文提取规则误删用户正文开头。
- 规则路由过度命中，绕过内部 LLM 导致复杂任务参数不足。
- Agent 仍按旧方式调用，迁移效果有限。

缓解：

- 规则路由只覆盖高置信度场景。
- 对旧 `context` 拆分保留 fallback。
- 单测覆盖事故样例和边界样例。

回滚：

- 新字段为兼容扩展，回滚时可只禁用规则路由和正文提取。
- 旧 `context` 路径保留，不影响现有调用。

## 5. 测试命令

```bash
pytest tests/unit/tools/test_word_tool.py -q
pytest tests/unit/tools/test_pdf_tool.py -q
pytest tests/unit/tools/test_excel_tool.py -q
pytest tests/unit/tools/test_ppt_tool.py -q
```

本地 Windows venv：

```powershell
.\venv\Scripts\python.exe -m pytest tests/unit/tools/test_word_tool.py -q
```
