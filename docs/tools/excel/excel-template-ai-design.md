# Excel 智能模板填充工具设计（样例 + 数据 → 按版式生成）

> **关联开发计划**：[plan-excel-template-ai.md](../../plans/plan-excel-template-ai.md)
> **登记**：[docs/ideas.md](../../ideas.md)「工具」分区「Excel 智能模板填充」(#44)
> **消费者**：旅游报价（[design-travel-quote-template-engine.md](../system/design-travel-quote-template-engine.md)）、后续 CRM 对账单 / 财务报表 / 贸易报价单等

## 背景与动机

很多业务场景都需要"**给我一个样例表格 + 我的数据，照着样例版式生成一张填好数据的 Excel**"：旅游报价、CRM 对账单、财务报表、贸易报价单……版式千差万别，但"结构化数据 → 按样例版式填表"的机制完全相同。把它做成通用 Excel 工具的一个 action，任何子智能体都能调用。

## 核心设计决策（无状态）

本工具是**无状态**的，遵循三条原则：

1. **样例每次必传，调用方负责存储**。工具不保存任何模板。用户/调用方随时给一个样例附件，工具拿到就能用。"下次还用某个样例"是**调用方**（agent 记在对话上下文、skill 存文件路径）的职责，不是工具的。
2. **分析 = 样例 + 本次数据**。结构识别在每次填充时进行，且**数据感知**（用本次数据辅助判断哪些是明细行、绑哪些键）。因此没有"先分析入库、再取用"的两段式，分析折叠进 fill。
3. **行数不匹配是核心正确性需求**。给的数据行数和样例的明细行数会多/少/相等，工具必须正确处理（见下），且**输出不得残留任何样例数据**。

> 这三条直接决定了：**不做模板注册中心、不做 DB 表、不做 list/get/set_default/delete、不做 template_id 解析**。上一版设计里的 `bs_excel_templates`、`analyze_template` 独立 action、模板管理 actions 全部移除。工具收敛为**一个无状态 action**。

## 现状（已有基础，本设计是扩展）

`src/tools/excel/` 已具备（直接复用，不重写）：

| 既有能力 | 文件 | 复用点 |
|---|---|---|
| `fill_template(template_path, variables, output_name)` | `excel_template.py` | 克隆模板 + 填值；`{{var}}` 占位符模式 + 无占位符的**结构感知模式**（启发式找表头、按表头文本匹配填字典列表、行展开） |
| `_fill_structured` / `_detect_header_row` | `excel_template.py` | 结构感知填充的启发式基座，AI 分析升级它 |
| `_save_row_styles` / `_apply_row_styles` | `excel_template.py` | 行样式深拷贝（font/border/fill/number_format/alignment）—— 插入新行时复用 |
| `ExcelFileHandler.copy_and_open` / `ws.insert_rows` | `excel_lib.py` / openpyxl | 克隆样例 + 行插入/删除 |
| 多 action 管线 | `excel_process_tool.py` | `TaskType` + `_get_handler(op)→_handle_xxx` + `PipelineContext`；新增 action 按既有模式插入 |
| 租户目录 | `excel_lib.py` | `get_session_dir()` 走 ContextVar 拼租户路径（输出落租户目录） |

既有边界（本设计补的）：
- `detect_variables` 只认 `{{var}}`，无法从**纯样例**（无占位符）识别结构 → 用 AI 分析补。
- `_fill_structured` 启发式不准、且**不处理行数变少**（会残留样例数据）→ 用 AI + 删除逻辑补。
- 不支持分区汇总行、合计、多人群人均等绑定 → 用数据感知分析补。

> 既有 `list_templates()`（扫描 `storage/excel_templates/`）本设计**不依赖、不扩展**；它作为遗留函数可保留（harmless），新流程不使用。

## 唯一核心 action：`fill_template`

```
fill_template(sample_file_path, data, output_name?) → { file_path, file_name, rows_rendered, inferred_structure? }
```

| 入参 | 必填 | 说明 |
|---|---|---|
| `sample_file_path` | ✅ | 样例 Excel 文件路径（调用方提供并负责存储）。工具只读不写原件。 |
| `data` | ✅ | 本次要填入的结构化数据（见"数据契约"） |
| `output_name` | ❌ | 输出文件名（不传则自动命名，落租户会话目录） |

| 出参 | 说明 |
|---|---|
| `file_path` / `file_name` | 生成的 Excel |
| `rows_rendered` | 实际填充的明细行数 |
| `inferred_structure`（可选） | 工具本次识别出的结构摘要（标题/meta 字段/列绑定/明细区/分组/合计），供 agent 调试或向用户回显"我是这样理解您的样例的" |

**内部流程**：

```
1. copy_and_open(sample_file_path)            # 克隆样例，不动原件
2. AI 结构分析（数据感知）：
   a. openpyxl 读样例 → 单元格网格（值/合并/加粗/背景）
   b. 连同 data 一起喂 LLM → 识别：
      - 标题区、顶部 meta 字段（位置 + 绑定到 data.meta 的哪个键）
      - 明细列头（列号 + 表头 + 绑定到 data.rows 的哪个键）
      - 明细行区（首末行号）+ 行模板样式
      - 分区汇总行（"XX小计" + 覆盖范围）
      - 合计行 + 人均/其他标量字段（绑定 data.totals）
   c. 产出 inferred_structure（瞬态，不持久化）
3. 渲染（按下节"行数不匹配处理"）：
   - 填标题/meta（按结构位置 + data.meta）
   - 明细区：按 data.rows 填充，处理 多/少/相等
   - 分组汇总行：填 data.group_subtotals（或按 rows 现算）
   - 合计/人均：填 data.totals
4. 不变式校验：输出明细区不得残留样例数据（见下）
5. save → 返回
```

## 行数不匹配处理（核心正确性）

样例明细区有 **K** 行示例数据，本次 `data.rows` 有 **M** 行。三种情况：

| 情况 | 处理 |
|---|---|
| **M == K** | 原位替换：每行单元格按列绑定用 `data.rows[i]` 覆盖 |
| **M > K** | 填满 K 行后，按"行模板样式"（取自第 K 行，含单元格样式 + 行高）`insert_rows(M-K)` 续填 `data.rows[K..M-1]`；下游行（汇总/合计）随插入下移；下方合并范围同步 +（M-K）重锚定（见"样式保留"） |
| **M < K** | 填前 M 行后，**`delete_rows` 删除剩余 K-M 行样例数据**；下游行随删除上移；下方合并范围同步 −（K-M）重锚定 |

**不变式（必须强校验）**：

> 渲染后，输出 Excel 的明细区内**不得残留任何样例的示例数据**——行级（M<K 时删除多余行）和单元格级（每个原示例单元格都要被 data 值或空覆盖，不能保留样例文字/数字）。

这条是本工具最重要的正确性约束，单测必须覆盖：给一份带示例数据的样例 + 少于示例的 data，断言输出里找不到任何样例示例值。

**多分组样例**：若样例有多个明细区（如旅游报价的"房餐车区 / 门票区 / 服务费区"，每区自带小计行），对每个区独立判定 M vs K、独立插入/删除，`data.rows` 按分组归属（结构分析时识别每行属于哪个分组）填入对应区。

## 样式保留（核心要求）

用户样例的视觉格式——**颜色、字体、合并单元格、行高、列宽、数字格式、边框、条件格式**等——必须**尽可能完整保留**。这是"按样例版式生成"的根本价值：输出要长得和客户给的样例一模一样，只是数据换成本次的。

**总策略：以克隆样例为基底，只改值、不重绘样式。** 一切操作都建立在不破坏既有样式的前提下。

| 样式层面 | 保留方式 |
|---|---|
| 单元格样式（字体/填充色/边框/对齐/数字格式/保护） | 填充时**只写 `.value`，不动样式属性**；样式自动保留。既有 `_save_row_styles`/`_apply_row_styles` 已覆盖 font/border/fill/number_format/alignment，插入行时复用 |
| **行高 / 行隐藏** | **既有缺口，需补**：插入新行时 `row_dimensions[r].height = 模板行高度`、`hidden`/`customHeight` 同步 |
| 列宽 / 列隐藏 | 工具只增删**行**、不动列 → 列宽天然保留，无需处理 |
| **合并单元格** | 保留全部既有合并；行增删后**显式重锚定**下方合并区（见下，openpyxl 坑） |
| 条件格式 / 数据验证 | 不动；范围跨增删区时按偏移修正（openpyxl 限制，列为风险） |
| 冻结窗格 / 打印设置 / sheet 视图 / 网格线 | 不动，天然保留 |
| 图表 / 图片 / 主题色 / 表格样式 | 不动（图表引用区域若被行增删影响，列为已知限制） |

**三个关键实现点**：

1. **值写入不改样式**：填充 meta/明细/小计/合计单元格时只 `cell.value = ...`，**绝不**用 `Font(...)`/`PatternFill(...)` 重建——这是样式保留的第一原则。
2. **插入行的完整样式复制（补行高）**：M>K 插入新行时，每个单元格照搬模板行对应列的样式（`copy(cell.font/border/fill/alignment/number_format)`），**并补 `row_dimensions[new_row].height = row_dimensions[template_row].height`**。考虑用 `new_cell._style = copy(template_cell._style)` 整体复制样式索引（比逐属性更完整，一次性覆盖 font/fill/border/alignment/number_format/protection）。
3. **合并单元格显式重锚定（补 openpyxl 缺陷）**：openpyxl 的 `insert_rows`/`delete_rows` **不会自动平移** `merged_cells.ranges`、条件格式范围、数据验证范围。渲染前快照所有合并范围；明细区增删 N 行后，把**位于明细区下方**的合并范围的 `min_row/max_row` 同步 ±N 后重写；明细区内部的合并（如同类别的"成本类别"列纵向合并）按本次数据重新合并。

**不变式扩展（单测强校验）**：

- 既有：输出明细区不得残留样例数据。
- 新增：渲染前后，**未被填充的区域**（标题区样式、表头样式、列宽、合并范围、行高）**位级一致**。单测对比克隆样例与输出的非数据区样式，断言不退化。

## 数据契约（`data` 参数）

领域无关，最小必填为 `rows`：

```json
{
  "meta": {                          // 可选：顶部标量字段
    "company_name": "中科悦行旅行社",
    "customer_name": "张先生",
    "date": "2026-08-01",
    "pax_summary": "2成人,3儿童"
  },
  "rows": [                          // 必填：明细数据行
    { "category": "住宿", "name": "黄果树套房", "unit_price": 680, "quantity": 2, "unit": "间/夜", "amount": 1360, "remark": "亲子套房" },
    { "category": "门票/项目", "name": "黄果树瀑布(成人)", "unit_price": 160, "quantity": 2, "unit": "人", "amount": 320, "remark": "" }
  ],
  "columns": [                       // 可选：显式列绑定（不传则 AI 从样例表头 ↔ rows 键推断）
    { "key": "category", "header": "成本类别" },
    { "key": "amount", "header": "费用小计" }
  ],
  "group_subtotals": {               // 可选：分组汇总值（不传则按 rows 现算）
    "房餐车小计": 1360, "门票小计": 320, "服务费小计": 0
  },
  "totals": {                        // 可选：合计区
    "grand_total": 10230,
    "per_capita": { "成人人均": 2280, "儿童人均": 2076.67 }
  }
}
```

- `meta`：键由结构分析绑定到样例顶部位置；样例没出现对应位置则忽略。
- `rows`：键由 `columns`（若提供）或 AI 推断绑定到样例明细列；data 多余的键忽略，样例列缺数据则置空（不留样例值）。
- `columns`：调用方可显式提供以保证绑定可靠（旅游报价等已知 schema 场景推荐）；ad-hoc 场景不传，AI 推断。
- `group_subtotals` / `totals`：键由结构分析绑定到样例的"XX小计"/合计/人均单元格；不传则小计按 rows 现算、合计留空。

> 消费者负责把领域数据适配成这个形状（travel-quote 在 `generate.py` 做 QuoteData→data 映射）。工具完全不认识"成人人均/房餐车"等领域词。

## 渲染策略

两种实现路径，都基于"克隆样例 + 数据感知结构分析"，按样例特征自动选择：

| 策略 | 适用 | 实现 |
|---|---|---|
| **样例克隆**（主） | 样例是 xlsx（有精确坐标） | 克隆样例 → 按分析出的结构坐标填值 + 行插入/删除（cell_map 思路） |
| **占位符**（兜底） | 样例已含 `{{var}}` 占位符 | 增强既有 `fill_template` 占位符模式（支持 `{{#group}}` 块、中文变量、点号路径） |

两条路径**都执行行数不匹配处理和不变式校验**。策略选择对调用方透明（结构分析阶段判定）。

**主推样例克隆**：用户明确倾向"复制样例直接替换"。样例克隆保留客户原版式 100%，无需向样例注入占位符。复用既有 `copy_and_open` + `_save_row_styles`/`_apply_row_styles` + `insert_rows`/`delete_rows`。

## 可导入库（供 skill 脚本调用）

核心函数对 skill 脚本（如 travel-quote 的 `generate.py`，在子进程跑）可直接 import：

```python
# src/tools/excel/excel_template_ai.py
def fill_with_sample(sample_file_path, data, output_name=None, tenant_id=None) -> dict:
    """无状态：样例 + 数据 → 填好的 Excel。返回 {file_path, rows_rendered, inferred_structure}"""
```

`ExcelProcessTool._handle_fill_template` 是它的薄包装（处理 action 入参归一 + 返回格式 + 租户上下文）。两条调用路径：

- **BaseTool 路径**（agent 临时出表）：LLM 调 `excel_process`，action=fill_template，传 file_paths=[样例] + data。
- **skill 脚本路径**（集成出表，如 travel-quote）：`generate.py` import `fill_with_sample` 直接调，data 不经 LLM 中转。

## 租户上下文

照搬 `src/tools/word/word_process_tool.py` 双轨模式：`ExcelProcessTool` 加 `self._tenant_id`/`self._user_id` + setter，Agent 钩子调用；fallback `src.saas.context` ContextVar。输出落 `storage/tenants/{tid}/...`（遵循租户附件规范）。**样例输入文件路径由调用方给**，工具不负责存储样例。

## 校验（fail-loud，Rule 9）

- 结构分析结果完整性：至少识别出明细区 + 列绑定；识别失败 → 报错（不静默乱填）。
- **不变式校验**：渲染后扫描输出明细区，若残留任何样例示例值 → 报错重试（不应发生，发生即 bug）。
- 列绑定覆盖率：若样例明细列大多绑不上 data 键 → 警告回传（`unmatched_columns`），让调用方知道 data 可能缺字段。

## 分阶段实施

| Phase | 目标 | 交付 | 风险 |
|---|---|---|---|
| **A. 数据感知分析 + 行数不匹配渲染** | AI 结构分析（样例+data）+ 三种行数情况处理 + 不变式校验 | `excel_template_ai.py`（`fill_with_sample` + 分析 + 渲染 + 校验） | 高（LLM 识别准确率 + 行插入/删除正确性） |
| **B. 分组/合计/人均 + 样例克隆渲染** | 多明细区分组、合计/人均绑定、行模板样式克隆 | 增强渲染器 + 列绑定（显式 columns / AI 推断） | 中 |
| **C. action 接线 + 租户 + 测试** | `_handle_fill_template` 增强、路由 prompt、双轨租户、测试 | ExcelProcessTool handler + router + `test_excel_template_ai.py` | 低 |

> 去掉了上一版的"DB 表 + 注册中心"Phase——无状态设计不需要。A 是主体，B 增强，C 收口。

## 风险与取舍

| 风险 | 缓解 |
|---|---|
| **LLM 结构识别不准** | 数据感知 prompt + JSON Schema 校验 + 不变式校验兜底 + `inferred_structure` 回传供 agent/人确认 |
| **行数变少时残留样例数据**（最严重） | 显式 `delete_rows` + 不变式单测强校验（给少于示例的 data，断言输出无样例值） |
| **行增删后样式/合并/行高错乱**（openpyxl `insert_rows`/`delete_rows` 不自动平移合并范围、条件格式、数据验证范围） | 插入行用 `_style` 整体复制单元格样式 + **补行高**；合并范围**显式重锚定**；条件格式/数据验证范围按偏移修正或记为已知限制；新增"样式位级一致"单测对比渲染前后非数据区 |
| **样式属性复制不全**（漏行高/数字格式/保护） | 优先 `new_cell._style = copy(template_cell._style)` 整体复制；样式回归测试断言非数据区字体/填充/边框/数字格式/行高不变 |
| **列绑定歧义**（样例表头与 data 键对不上） | 调用方可传 `columns` 显式钉死；未传时 AI 推断 + `unmatched_columns` 警告 |
| **每次调用都跑 LLM 分析的成本** | 无状态换来的代价；样例通常不大、prompt 短（关闭推理）；高频场景后续可加调用方侧缓存（不是工具职责） |
| **既有 fill_template 回归** | 旧 `{{var}}` 单值/列表/结构感知模式保留；增强是加 data 模式 + 行数处理，不改旧模式 |

## 与既有/关联设计的关系

- **既有 `excel_template.py`**：本设计扩展它（AI 分析 + 行数处理 + data 模式），不重写既有函数。
- **既有 `ExcelProcessTool` 管线**：fill_template action 增强；不新增 list/get/set_default/delete（无状态）。
- **旅游报价消费者**：[design-travel-quote-template-engine.md](../system/design-travel-quote-template-engine.md) 负责领域计费 + QuoteData→data 适配 + 持有样例路径，调本工具 `fill_with_sample`。
- **PDF 固定版式生成器**（#30）：本设计是 Excel 侧"样例驱动生成"，与之互补。
