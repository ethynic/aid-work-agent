# 开发计划：Excel 智能模板填充工具（无状态）

> **关联设计**：[excel-template-ai-design.md](../tools/excel/excel-template-ai-design.md)
> **登记**：[docs/ideas.md](../ideas.md)「工具」分区 #44
> **核心**：无状态单 action `fill_template(样例, data)`；行数不匹配处理；输出不残留样例数据。
> **流程**：每个 Phase 走 [dev_workflow.md](../../.claude/rules/dev_workflow.md) 三智能体流程。pytest 走 `./scripts/dev_test.sh`。

## Phase A — 数据感知分析 + 行数不匹配渲染（主体）✅ 已完成（2026-07-20）

> 依赖：无。工具的核心能力。
> **完成状态**：`src/tools/excel/excel_template_ai.py` 已实现；`tests/unit/tools/test_excel_template_ai.py` 11 测试全绿（0 warning）；既有 excel 工具回归 12 测试不破。

- [x] A.1 数据契约与样例读取 `src/tools/excel/excel_template_ai.py`
  - [x] `data` 形状定义（FillData：meta/rows/columns?/group_subtotals?/totals?）Pydantic 校验
  - [x] openpyxl 读样例 → `read_sample_grid` 提取单元格网格（值/合并/加粗/背景/行高）
- [x] A.2 AI 结构分析（数据感知）`analyze_structure(sample_path, data, llm_callable) -> SheetStructure`
  - [x] LLM prompt：识别标题/meta 字段位置+绑定、明细列头+绑定、明细行区首末行+模板行、合计/人均
  - [x] 数据感知：用 data.rows / data.meta / data.totals 的键辅助绑定判定
  - [x] JSON 解析 + 结构校验；缺明细区/列时 fail-loud
- [x] A.3 行数不匹配渲染（span-based 清空，杜绝列残留）
  - [x] M==K：原位替换（每单元格只写 `.value`，不改样式）
  - [x] M>K：`insert_rows(M-K)`；`_copy_row_format` 复制单元格样式 + 行高；续填；下游下移
  - [x] M<K：填前 M 行后 `delete_rows(K-M)`；下游上移
- [x] A.3b 样式保留（核心要求）
  - [x] 值写入只动 `.value`，不重建 Font/Fill
  - [x] **合并单元格显式重锚定**：`_shift_merges` 按明细区增删 N 行 ±N 重写（openpyxl 不自动平移）
  - [ ] 条件格式 / 数据验证范围按偏移修正（**记为已知限制**，设计文档已说明；Phase A 聚焦合并+样式+行高）
  - [x] 列宽/冻结窗格/打印设置等不动（天然保留）
- [x] A.4 不变式校验 `_verify_render`
  - [x] 渲染后扫描输出明细区跨度，断言每格 == data 值或为空（无样例残留）
  - [x] 违例 → AssertionError（fail-loud）
- [x] A.5 库函数 `fill_with_sample(sample_file_path, data, output_name?, output_dir?, llm_callable?)`
  - [x] 串：copy_and_open → analyze_structure → 渲染 → 校验 → save；返回 file_path/rows_rendered/inferred_structure
- [x] A.6 单测 `tests/unit/tools/test_excel_template_ai.py`（11 passed）
  - [x] 行数不匹配三种情况（多/少/相等），每种断言无残留
  - [x] 列绑定：显式 columns 覆盖 / AI 推断（mock LLM）
  - [x] 结构分析（mock LLM）识别明细区/列/合计；坏 JSON/缺明细 fail-loud
  - [x] 不变式校验：故意构造残留，断言报错
  - [x] **样式保留回归**：表头粗体/填充、列宽、标题合并、数字格式、插入行行高/填充/边框继承、合并重锚定
- [x] **验证**：`./scripts/dev_test.sh tests/unit/tools/test_excel_template_ai.py -p no:cacheprovider -q` → 11 passed

## Phase B — 分组 / 合计 / 人均 + 样例克隆渲染增强 ✅ 已完成（2026-07-20）

> 依赖：A。覆盖复杂版式（如旅游报价的多分组 + 人均）。
> **完成状态**：多分组渲染（自下而上增删 + 行号重映射）、分组小计、合计/人均、列绑定增强已实现；新增 4 测试，累计 15 passed。

- [x] B.1 多明细区分组渲染
  - [x] `GroupRegion` 模型 + 结构分析识别多分组（每组 detail 区 + 小计行 + match）
  - [x] `_assign_rows` 按 match / row.group 归属；`_render` 分组自下而上增删 + `_final_row` 重映射
  - [x] 小计行填 `data.group_subtotals`（缺则按 subtotal_col 数值求和，`_resolve_subtotal`）
- [x] B.2 合计/人均绑定
  - [x] `totals` 识别合计行 + 人均/标量单元格
  - [x] 填 `grand_total` + `per_capita.*`（`_resolve_total_value`，行号按 `_final_row` 重映射）
- [x] B.3 列绑定增强
  - [x] 显式 `columns` 优先（fill_with_sample 内覆盖 LLM 推断）
  - [x] 跨度内未绑定列清空（`_column_span` + `_write_detail_row` span-based），杜绝列残留
- [ ] B.4 占位符兜底路径（样例含 `{{var}}` 时）— **延后**：中科悦行等样例无占位符，cell_map 路径已覆盖；`{{#group}}` 块等增强待真实占位符样例出现时再做
- [x] B.5 单测（4 新增，累计 15 passed）
  - [x] 多分组 + 混合 delta（>K/<K）+ 小计 + 合计 + 人均 + 无残留 + 样式保留
  - [x] 小计数值 fallback（不传 group_subtotals 时按列求和）
  - [x] 未匹配行并入首组（不静默丢数据）
  - [x] groups 为空退回单明细区（Phase A 兼容）
- [x] **验证**：`./scripts/dev_test.sh tests/unit/tools/test_excel_template_ai.py` → 15 passed；既有 excel 工具回归 27 passed 不破

## Phase C — action 接线 + 租户 + 路由 + 测试 ✅ 已完成（2026-07-20）

> 依赖：A + B。
> **完成状态**：`ExcelProcessTool` 接通智能填充（data 入参 + 确定性路由 + 租户注入）、路由 prompt 更新、6 集成测试全绿。

- [x] C.1 `ExcelProcessTool._handle_fill_template` 增强
  - [x] `ExcelProcessInput` 加 `data` 字段；`PipelineContext` 透传 data
  - [x] data 分支调 `fill_with_sample`；无 data 走旧 `variables` 占位符路径（向后兼容）
  - [x] `_resolve_task_deterministic`：data + file_paths → 确定性路由 fill_template（免 LLM 路由）
- [x] C.2 `excel_router.py` prompt：fill_template 两种模式（智能填充 data / 占位符 variables）
- [x] C.3 双轨租户注入（镜像 Word/Pdf）
  - [x] `ExcelProcessTool` 加 `_tenant_id`/`_user_id` + `set_tenant_id`/`set_user_id`（agent.py `hasattr` 钩子自动注入）
  - [x] `_resolve_tenant_user`（注入优先，ContextVar 兜底）+ `_resolve_output_dir`（注入时输出落租户目录，未注入走 ContextVar）
- [x] C.4 集成测试 `tests/unit/tools/test_excel_process_tool_template.py`（6 passed）
  - [x] execute(data+file_paths) 全链路（M==K / M>K）
  - [x] 租户注入 → 输出落 `tenants/{tid}/{uid}/`
  - [x] data 无样例附件 → 友好报错
  - [x] InputModel 含 data 字段
  - [x] 旧 variables 占位符路径回归
- [x] **验证**：excel 全量 36 passed（template_ai 18 + process_tool_template 6 + routing 12）；`ExcelProcessTool` 具 `set_tenant_id`；import 不触发 master_agent

> B.4 占位符兜底（`{{#group}}` 块等）仍延后：中科悦行等样例无占位符，cell_map 路径已覆盖；待真实占位符样例出现再做。

## 提交前终检（每个 Phase）

- [ ] 后端 import 终检：`python -c "from src.tools.excel.excel_template_ai import fill_with_sample"`
- [ ] pytest 全绿（`./scripts/dev_test.sh tests/unit/tools/test_excel_* -p no:cacheprovider -q`）
- [ ] **不变式重点回归**：M<K 场景输出无样例残留
- [ ] **样式保留回归**：带颜色/字体/合并/行高/数字格式的样例，非数据区渲染前后位级一致
- [ ] 既有 excel 工具回归（read/export/modify/format/既有 fill_template 不破）
- [ ] 设计文档与本计划同步；`docs/ideas.md` 状态更新
