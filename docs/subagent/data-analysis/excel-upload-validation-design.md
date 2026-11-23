# 数据源 Excel 上传格式校验与规范化设计

> 状态：设计定稿，待开发
> 日期：2026-11-23（含同日二次增补：数据值级清洗）
> 关联：[数据分析智能体设计](data-analysis-subagent-design.md)

## 1. 背景与问题

数据源管理页面（`/t/{tenant_id}/data-sources`）上传 Excel 时，用户会带入各式各样的"非标准"表格。当前管线对结构零校验：

- 上传时 `sheet_parser`（`src/services/data_analysis/sheet_parser.py`）隐式按 `header=0` 读取，前两行说明文字被当成表头/数据喂给 LLM 做 schema 推断；
- 分析时 `data_analyzer._load_from_path`（`src/tools/data_analysis/data_analyzer.py:261`）同样 `pd.read_excel` 原文件，`load_table` 出来的列就是说明行文字，聚合/计算全部落空。

真实事故（2026-11-23，agent2 会话）：用户上传带两行说明的 Excel，数据分析智能体 `load_table` 读不出「单价」列数值，Agent 空转一轮后只能让用户自己重导标准 CSV——体验差且侵蚀信任。

**根因判断**：这不是提示词能兜住的——靠"让 LLM 覆盖前 N 行识别表头"是打地鼠（今天第 3 行，明天第 5 行）。数据表场景对**结构正确性**的要求与文档 RAG 不同：结构错了不是回答质量下降，而是算出错误的数字，且静默无感知。因此在上传入口设确定性边界是正确取舍；同时拒绝不应是唯一动作，可用自动化手段消解大部分学习成本。

**同日二次增补的问题**：值级脏数据（`'2021 -1-20'`、`'2023年 10月'`、`'2023-2-30'`、`'10万'`、`'-'`）造成 LLM 识别困难。现有 `_infer_type` 对这类值整列退化判定为 text，分析 LLM 拿到 object dtype 列只能现场猜着清洗——同一次空转的另一半根源。值问题与结构问题**定性不同、处理位置不同**（见 §7）。

## 2. 目标与非目标

**目标**：
1. 上传时对每个 sheet 做确定性结构校验（纯 openpyxl/pandas，无 LLM）；
2. 三档处理：合法直接过 / 可自动修复的给"检测 + 预览 + 确认" / 真歧义的拒绝并给出具体引导；
3. 规范化结果**持久化为派生文件**，分析与上传解析共用同一规范化数据源（分析侧加载逻辑零改动）；
4. 错误信息可操作：指出具体行/格、说明"为什么"、给出正确格式示例；
5. **值级确定性清洗层**：加载后、给 LLM 前，对可确定性转换的脏值统一转换并出列质量报告，不再依赖分析 LLM 现场自由发挥。

**非目标**：
- 不改造 schema_extractor 的 LLM 推断逻辑（规范化后它自然拿到正确表头）；
- 不处理存量已入库畸形表（二期可选：load_table 时返回结构化提示）；
- 不引入新的数据库表或 db_update 变更；
- 不做语义级清洗（"约30"、"30左右"这类模糊值保持原样交 LLM 说明，清洗层只碰确定性可转换的形态）。

## 3. 设计原则

| 原则 | 说明 |
|------|------|
| **校验确定性** | 格式判定全部用 openpyxl/pandas 确定性代码，禁止用 LLM 判格式——LLM 判定会把"校验"变成另一个不确定源 |
| **不静默自动修** | 静默错检 = 脏数据进库，每次查询都错且用户无感知，比报错更糟。凡涉及"猜测"（表头行位置）必须用户确认；语义明确的操作（合并展开）自动执行但明确告知 |
| **原始文件不动** | 原始上传文件保留原样（下载/审计口径不变），规范化写入派生文件 |
| **失败要具体** | 拒绝时给出 sheet 名、问题行号/单元格、一句人话解释和正确示例，不返回笼统"格式错误" |
| **结构拦在入口，值修在加载** | 结构问题该拦（不修全表错），值问题不该拦（脏数据是企业数据常态，全拦会把用户拦死），确定性清洗收编到加载层 |
| **转换必报告** | 值级清洗的每一次类型转换、置空、粒度降级都写进列质量报告，绝不静默 |

## 4. 格式契约与三档判定

### 4.1 格式契约（对外口径）

> 一张规范的数据表：**第一行是表头（仅一行，不含合并单元格），第二行起是数据，每个 sheet 一张表，sheet 名即表名。**

对用户引导文案统一使用这句话；系统侧能力比契约宽（见 4.2），但对外宣传口径保持简单。**值级数据不要求"干净"**——清洗层兜底（§7），不向用户提格式要求。

### 4.2 三档判定规则

对每个 sheet 独立判定，verdict ∈ `ok` / `fixable` / `error`：

| 检查项 | 判定 | 说明 |
|--------|------|------|
| 表头行位置 | 第 1 行 → `ok`；第 2~10 行可识别 → `fixable`（确认后跳过前 N 行）；不可识别 → `error` | 检测算法见 §5 |
| 表头行合并单元格 | 存在任何合并 → `error` | 列归属歧义，无法安全自动展开，必须用户取消合并 |
| 数据区合并单元格（垂直/水平） | → `fixable` | 语义明确（值跨行/跨列 = 填充），自动展开，无需用户操作 |
| sheet 全空 / 无数据行 | → `error` | "无数据行" = 表头行之后 0 行非空数据 |
| 表头行重复列名 | → `fixable`（warning 级提示） | 交给 schema 阶段列名处理，不阻断 |

**多 sheet 组合规则**：逐 sheet 判定，互不连坐。任一 sheet `fixable` → 进入确认流程，确认弹框中每个 sheet 可勾选是否导入（`error` sheet 默认禁选并展示原因；`ok`/`fixable` 默认勾选）；**全部 sheet 均 `error`** → 整文件拒绝（SSE error）。用户可只导部分 sheet，不必为一张坏表改整个文件。

### 4.3 拒绝引导文案

- 指出位置：如「工作表"物流运费" 第 2 行（表头行）存在合并单元格 B2:D2」；
- 说明后果：如「合并单元格会让 AI 无法判断该列归属，数据会算错」；
- 给出修法：如「请取消该行合并，每列填写独立的表头名称」；
- 附正确格式示例（前端静态示例图，复用确认弹框内嵌预览渲染）。

## 5. 表头检测算法（确定性）

对每个 sheet（openpyxl `read_only=True` 只取前 12 行原始值，含合并区 anchor 值）：

1. 逐行打分（第 1~10 行），得分特征：
   - 非空单元格占比（高加分）；
   - 文本值占比（表头多为文本，高加分）；
   - 值唯一性（重复值少加分）；
   - 与下一行的对比：下一行数值型占比显著更高 → 该行表头特征强；
   - 否决项：行内大部分为空、只有 1~2 个散落文字（典型标题/说明行）→ 直接排除。
2. 取得分最高且超过阈值的行作为候选 `header_row`：
   - `header_row == 1` 且第 1 行无合并 → `ok`；
   - `header_row > 1` → `fixable`，携带候选行号与前 5 行预览；
   - 无候选 → `error`。
3. CSV：无合并概念，仅跑表头行检测（候选 > 1 → fixable，规范化为跳行重写 CSV）。

**阈值与打分权重实现期用真实事故样本（2026-11-23 高精管材基准表等）回归校准**，样例入库 `tests/fixtures/data_sources/`。

## 6. 合并单元格展开规则

数据区合并区（起止均在 `header_row` 之后）自动展开：

- 取合并区左上角 anchor 值，填充到合并区覆盖的**每一个单元格**；
- 展开在规范化文件生成时一次性落盘（§8），不是加载时动态行为——保证上传预览、schema 推断、分析加载三者看到完全相同的数据。

## 7. 数据值级清洗与列质量报告（2026-11-23 二次增补）

### 7.1 定性：与结构问题分治

| | 结构问题（表头错位、合并） | 值问题（`'10万'`、`'2023-2-30'`） |
|---|---|---|
| 出现频率 | 少数用户的奇葩文件 | 企业真实数据常态，几乎每张表都有 |
| 拦截后果 | 拦了合理（改一次永久受益） | 拦了把用户拦死，要求值级干净不现实 |
| 修复语义 | 修复后表结构唯一确定 | 多数值可确定性转换，少数只能置空并报告 |

结论：**结构拦在上传入口（§4~§6）；值不拦，收编到"加载后、给 LLM 前"的确定性清洗层**。现状 `_infer_type` 遇脏值整列退化 text，分析 LLM 拿 object dtype 现场猜清洗，结果不可控——清洗层就是把这件事从 LLM 手里收回来。

### 7.2 值形态处理表

清洗仅作用于**整格匹配**（全格 = 该形态）的值；`预算10万`、`约30` 等嵌入文本保持原样，交 LLM 说明。

| 值形态 | 例子 | 处理 | 依据/口径 |
|--------|------|------|----------|
| 日期含杂散空格 | `2021 -1-20` | 去空白后 `pd.to_datetime` 解析 | 无损 |
| 中文年月 | `2023年 10月` | → 2023-10-01，报告"月粒度" | 语义明确，粒度降级须报告 |
| 非法日期 | `2023-2-30` | → NaT + 计数报告 | 不可修复，置空比留错值安全 |
| 单位数 | `10万`、`3.5亿`、`120元` | 去单位 ×倍率（万=1e4、亿=1e8，元=×1）→ 数值 | 换算语义明确 |
| 缺失标记 | `-`、`—`、`/`、`无`、`N/A`、空串 | → NaN | 中文业务表惯例 |
| 千分位/货币符号 | `1,234.50`、`¥88` | 去符号 → 数值 | 无损 |
| 全角数字 | `１２３` | 半角化后解析 | 无损 |
| 百分比 | `50%` | → 0.5，报告口径"百分比已换算为小数" | 50/0.5 口径歧义，默认小数并声明 |
| 无法归类 | —— | 保持原样（text），报告该列非标准值占比 | 不猜 |

日期解析格式候选清单（确定性）：`%Y-%m-%d`、`%Y/%m/%d`、`%Y年%m月%d日`、`%Y年%m月`（月粒度）、`%d/%m/%Y` 等，解析失败一律 NaT + 计数，绝不猜。

### 7.3 列质量报告（交给分析 LLM）

清洗后重推断列类型（覆盖 `_infer_type` 的 text 兜底），报告挂在表元信息随 `load_table` 返回：

```jsonc
"column_quality": {
  "单价": {"dtype": "decimal", "converted": 118, "to_null": 3,
           "examples_failed": ["2023-2-30"], "granularity": null},
  "月份": {"dtype": "date", "converted": 24, "to_null": 0, "granularity": "month"}
}
```

价值：
- 分析 LLM 拿到即知每列真实 dtype 与异常口径，写 pandas 代码不踩 `'10万'` 的坑；
- 回答用户时能如实交代"3 条非法日期已按空值处理"，符合项目"不确定时诚实承认"的设计原则；
- 报告随表元信息可被 SSE/前端透出（可选，二期）。

### 7.4 落点与口径统一

- 新增 `src/services/data_analysis/value_cleaner.py`：`clean_dataframe(df, dtypes_hint) -> (df_cleaned, column_quality)`，纯 pandas 确定性实现；
- **分析侧**：`data_analyzer.load_table` 加载后调用（在 `_load_from_path` 返回 df 之后、落盘状态之前），清洗报告并入 `_table_meta`；
- **上传侧**：`sheet_parser._dataframe_to_sheet_info` 采样时调用同一 cleaner，schema_extractor 拿到的 sample_data 与分析侧同口径；validation 事件附带列级 warning（`fixable`/`ok` sheet 也可有值级提示），**不阻断、不要求确认**；
- 常量（缺失标记表、单位倍率表、日期格式清单）与 excel_validator 共享模块级常量，避免两处口径漂移。

## 8. 规范化派生文件与加载链路

**核心决策：规范化结果持久化为派生文件，`schema.source.file_path` 指向派生文件。**

```
上传原始文件                     派生规范化文件（仅 fixable 时生成）
storage/tenants/{tid}/          storage/tenants/{tid}/
  data_sources/{file_id}.xlsx     data_sources/{file_id}_n.xlsx
       │                              │
       ├─ 前端下载/审计（保持原样）      ├─ sheet_parser 解析 → schema_extractor
       └─ 确认后不再使用               └─ data_analyzer._load_from_path（零改动）
```

- **生成时机**：用户确认修复后（confirm 请求）才生成，非法/合法文件不产生额外开销；
- **生成方式**：openpyxl 加载原文件 → 删除表头行之前的行 → 展开全部数据区合并 → 仅保留被勾选的 sheet → 另存 `_n` 后缀文件（.xlsx；CSV 同规则重写 `_n.csv`）；
- **收益**：`data_analyzer._load_from_path` 无需感知 header_row/合并元数据，结构规范化对加载链路**零改动**；上传解析与运行时加载天然一致，不存在"两套规范化语义漂移"问题；
- **值级清洗是加载时行为、不落盘**（§7.4）——原始值保留在派生文件中，清洗报告逐次生成，口径升级（如新增单位）对存量数据源自然生效；
- `.xls` 的合并信息 openpyxl 不支持：尝试 xlrd 读取 `merged_cells`，不可用或读取失败时合并检查降级为 warning（提示"建议转存为 .xlsx 后上传"），表头检测照常。

## 9. 流程与接口设计

### 9.1 上传 SSE 流（改造 `POST /api/data-analysis/upload`）

现有事件：`connected` / `progress` / `sheet_progress` / `sheet_done` / `complete` / `error`。新增：

```jsonc
// 解析完成后、schema 推断前，推送逐 sheet 校验结果
{"type": "validation", "sheets": [
  {"sheet_name": "成本基准", "verdict": "ok",
   "column_warnings": ["「单价」列 12% 的值非标准数字（如 '10万'、'-'），导入后 AI 将自动换算/置空并在分析时说明"]},
  {"sheet_name": "物流运费", "verdict": "fixable", "header_row": 3,
   "issues": ["第 1-2 行将被视为说明行并丢弃"],
   "preview": {"columns": ["成本项目", "单价", "..."], "rows": [["xx", 12.5, "..."]]}},
  {"sheet_name": "汇总", "verdict": "error",
   "issues": ["表头行（第 2 行）存在合并单元格 B2:D2，请取消合并"]}
]}
```

分支：
- 全部 `error` → 推 `error` 事件（携带 validation 明细 + 引导文案），流结束；
- 存在 `fixable` → 推 `validation` + `needs_confirmation`（含 `file_id`），流结束，**不继续** schema 推断；
- 全部 `ok` → 按现有管线继续（推 validation 后直接走 sheet_progress，前端可静默）。

### 9.2 确认端点（新增 `POST /api/data-analysis/upload/confirm`）

```jsonc
// 请求体
{"file_id": "a1b2c3d4e5f6", "sheets": [
  {"sheet_name": "物流运费", "header_row": 3, "selected": true},
  {"sheet_name": "汇总", "selected": false}
]}
// 响应：与 /upload 相同的 SSE 流（persist 阶段跳过，直接规范化 → 解析 → schema 推断 → complete）
```

- 后端按决策生成规范化派生文件（确定性执行，**不重新检测**——表头行以用户确认为准），之后管线与普通上传完全一致；
- **租户归属校验**：`file_id` 解析出的路径必须位于 `storage/tenants/{当前租户}/data_sources/` 下，否则 404（防跨租户路径枚举，与 cp_tool 跨租户防护同口径）；
- 幂等：重复 confirm 直接覆盖重建 `_n` 文件，无副作用。

### 9.3 前端（`DataSourceManager.vue` + `api/dataSource.ts`）

- `uploadExcelStream` 增加 `validation` / `needs_confirmation` 事件处理；
- 新增**校验结果弹框**：
  - 逐 sheet 列表：`ok`（绿，可展开列级 warning）/ `fixable`（黄，含"将丢弃前 2 行说明"说明）/ `error`（红，含具体问题与修法）；
  - `fixable` sheet 展开显示前 5 行预览表格（规范化后形态），用户确认"AI 看到的样子"；
  - 每 sheet 勾选是否导入（error 禁选）；
  - 底部固定引导：格式契约一句话 + 「查看正确示例」展开示例图；
  - 确认后调 `confirmUpload` 复用既有上传进度弹框继续。
- 全 error 场景：直接在弹框展示引导，不发起 confirm。

## 10. 改动清单

| 层 | 文件 | 改动 |
|----|------|------|
| 后端新增 | `src/services/data_analysis/excel_validator.py` | 结构校验 + 表头检测 + 合并展开 + 规范化文件生成（纯函数，无 LLM） |
| 后端新增 | `src/services/data_analysis/value_cleaner.py` | 值级清洗 + 列质量报告（纯 pandas，无 LLM）；与 validator 共享常量 |
| 后端修改 | `src/api/data_analysis.py` | `/upload` 增加校验阶段与 SSE 事件；新增 `/upload/confirm` |
| 后端修改 | `src/services/data_analysis/sheet_parser.py` | 采样时接入 value_cleaner（统一口径） |
| 后端修改 | `src/tools/data_analysis/data_analyzer.py` | `load_table` 加载后调用 value_cleaner，报告并入 `_table_meta`（结构规范化不改此文件） |
| 前端 | `frontend/web/api/dataSource.ts` | 新事件类型 + `confirmUpload()` |
| 前端 | `frontend/web/pages/DataSourceManager.vue` | 校验结果弹框（判定列表/预览/勾选/列级 warning/引导） |
| 测试 | `tests/unit/services/test_excel_validator.py`（新增） | 检测算法、合并展开、三档判定、`.xls` 降级 |
| 测试 | `tests/unit/services/test_value_cleaner.py`（新增） | §7.2 逐形态用例（含 `预算10万` 不动、`2023-2-30` 置空报告、`50%` 口径） |
| 测试 | `tests/fixtures/data_sources/`（新增） | 事故样本回归文件（说明行表头、表头合并、数据区合并、脏值列、正常表） |
| 测试 | upload/confirm 路由测试 | SSE 事件序列、租户归属校验、幂等 |
| 文档 | `docs/system/file_usage.md` | 登记 `data_sources/{file_id}_n.xlsx` 派生文件 |
| 文档 | `docs/ideas.md` | 本设计登记 |

无 DB 变更、无 `deploy/db_update.yaml` 条目。

## 11. 边界与风险

| 项 | 处理 |
|----|------|
| 大文件规范化内存 | openpyxl 全量加载 50MB 上限文件有内存峰值；规范化**仅在用户确认后**触发（非法文件零开销），沿用现有 `asyncio.to_thread` 模式。若真机出现内存压力，按架构规范登记 `agent_runner.heavy_tools` 接入清单（判定依据：openpyxl 全量加载大 Excel、分钟级、内存逼近容器上限）——本期先观察，不预设 |
| 表头检测误判 | 只发生在 `fixable` 提议阶段，用户在预览中确认/否决，误判不会静默进库 |
| 用户确认了错误的 header_row | 用户自担（预览所见即所得），且可随时删除数据源重传 |
| 值清洗改变分析结果口径 | 一切转换进 column_quality 报告随表元信息可见；报告口径升级（新增单位/格式）对存量数据源自然生效（清洗不落盘） |
| `50%` 换算口径争议 | 默认小数（0.5）并在报告中显式声明口径；如真机反馈不符预期，可改为配置项 |
| 存量畸形表 | 不在本期；二期候选：`load_table` 加载时对首行做同样检测，异常时向 LLM 返回结构化提示（"表头疑似在第 3 行，建议用户重新上传"） |
| 多语言/特殊列名 | 不涉及，schema 阶段既有逻辑处理 |

## 12. 计费与负载判定（规范自检）

- **计费**：excel_validator / value_cleaner 均为纯本地 openpyxl/pandas 逻辑，**零 LLM/Embedding 调用点**，按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 核对无计费需求。schema_extractor 既有 LLM 调用不在本次改动范围。
- **负载等级**：上传校验只读前 12 行采样（`read_only=True`），轻量；value_cleaner 对已加载 DataFrame 做fillna/astype 级操作，轻量；规范化为全量操作但有 50MB 门禁 + 确认后触发 + API 容器内 to_thread 执行，判 **light**，不接入 heavy_tools（风险观察项见 §11）。

## 13. 分期

- **P1（本期）**：excel_validator + value_cleaner + /upload 校验阶段 + /confirm 端点 + 前端校验弹框 + 单测与事故样本回归。
- **P2（可选）**：存量畸形表 load_table 结构化提示；上传校验统计（error/fixable 占比、值级脏值占比）——若 fixable/脏值占比高，说明应做"上传智能转换向导"再进一步降门槛。
