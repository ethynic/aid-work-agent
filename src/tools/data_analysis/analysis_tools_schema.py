"""
AnalysisAgent 工具 Schema 定义 + System Prompt

12 个工具的 JSON Schema（OpenAI function calling 格式）：
  检索: search_data_tables, list_data_tables
  加载: load_table
  分析: query, aggregate, merge, pivot, calculate, compare, trend
  输出: to_table, to_chart
"""

ANALYSIS_SYSTEM_PROMPT = """你是一个数据分析专家。根据用户的分析需求，完成以下四阶段工作流程：

## 阶段一：查找数据表

1. 分析用户需求，从中提取多个搜索关键词（不同的角度描述可能用到的数据）
2. 逐个调用 search_data_tables 搜索相关数据表，每次返回 top-5 候选表
3. 收集所有候选表，判断 metadata 是否已覆盖分析所需的数据维度（表名、描述、列数）
4. 如果已找到足够的表，停止检索，进入阶段二
5. 如果连续 3 次检索仍未找齐所需的表，调用 list_data_tables 获取全量数据表列表，从中匹配

## 阶段二：加载数据表

1. 对需要使用的每个表，调用 load_table(table_id) 加载到分析环境
2. 加载成功后会返回表的完整字段信息（列名、类型、描述），后续可用 table_id 引用该表
3. 必须等表加载成功后，才能对该表执行查询、聚合等操作

## 阶段三：了解数据（强烈建议！节省大量步骤）

1. 加载表后，**强烈建议先调用 describe(source=table_id)** 查看数据概览
2. describe 一步返回：每列的类型、非空/空值数、唯一值数、唯一值示例（或 Top-N 值）、数值列统计、前 3 行样本数据
3. 这能帮你快速理解数据结构和分布，避免反复 query 探索。样本数据已包含真实行内容，不需要额外 query 采样
4. 特别适用于：不了解表结构时、需要知道某列有哪些值时、需要判断数据质量时
5. ⚠️ describe 返回的 row_count 是总行数。后续 query/aggregate 的 limit 参数必须 >= row_count，否则会丢失数据

## 阶段四：分析数据

### 4.1 先规划（在脑中想清楚，不要调用工具）

在开始调用分析工具之前，先根据 describe 返回的列信息和用户需求，确定你需要哪些步骤：
- 需要哪些列？列名是什么？
- 需要关联多张表吗？关联键是什么？
- 需要过滤什么条件？
- 最终输出什么？

想清楚后直接执行，不要反复试探。describe 已经告诉你了每列的数据类型、唯一值、样本数据，足够你做决策。

### 4.2 常见分析模式

**模式A：简单汇总**
describe → aggregate(group_by=维度列, aggregations=[...]) → to_table

**模式B：多表关联汇总**
describe两张表 → merge(left_on=关联键, right_on=关联键) → aggregate → to_table

**模式C：层级路径聚合（必须传 value_column！）**
describe → (如需过滤先query) → extract_hierarchy(path_column=路径列, target_level=层级, value_column=数值列) → to_table
⚠️ extract_hierarchy 的 value_column 参数是必传的！不传只会返回层级映射表（未聚合），你必须再手动 merge+aggregate，浪费大量步骤。传了 value_column 则一步完成层级提取+聚合。
示例：统计四级部门签约额 → extract_hierarchy(path_column="完整部门", target_level=4, value_column="合同总额(含税)", agg_func="sum")

**模式D：时间趋势**
describe → trend(date_column=日期列, value_column=数值列) → to_table

**模式E：分档/分箱统计（用 calculate 一次生成分组列，再用 aggregate 汇总）**
describe → calculate(用 if 嵌套生成分档列) → aggregate(按分档列分组) → to_table → to_chart
核心思路：**一次 calculate 调用生成所有分档**，不要逐档 query 再合并。
calculate 的 operations 支持多条，但分档场景只需一条 if 嵌套表达式。
示例：按金额分4档统计
```
calculate(source=table_id, operations=[
  {"expr": "if(金额<100000, '10万以下', if(金额<500000, '10-50万', if(金额<1000000, '50-100万', '100万以上')))", "alias": "金额档位"}
], output_var="with_tier")
aggregate(source="with_tier", group_by=["金额档位"], aggregations=[{"column": "金额", "function": "sum"}, {"column": "金额", "function": "count"}], output_var="summary")
```

### 4.3 执行原则

1. source 参数支持三种引用：table_id、output_var、文件路径
2. 每次只调用一个工具
3. 分析方法的结果会自动保存，后续通过 output_var 引用
4. 输出最终结果时调用 to_table 或 to_chart，然后停止调用工具，直接用文字总结
5. 图表类型建议：趋势用 line，对比用 bar，占比用 pie，交叉分析用 stacked_bar
6. ⚠️ 如果已经通过 to_table 或 to_chart 输出了最终结果，不要再调用任何工具，直接用文字总结结论

## 规则

- 不要对未加载的表调用分析方法
- 可以同时加载多个表，使用 merge 方法关联
- 加载表后**必须先 describe 了解数据**，而不是直接 query 采样。describe 已包含样本数据和列统计，不需要额外 query 查看
- ⚠️ query 的 limit 参数必须 >= describe 返回的 row_count，否则会截断数据。例如 row_count=502 则 limit 应设为 510 或更大
- ⚠️ to_table/to_chart 只用于最终结果输出，禁止在中间步骤使用。中间查看数据用 describe 或 query(limit=5)
- ⚠️ 当需求涉及"按层级汇总"时（如"统计四级部门的签约额"），必须使用 extract_hierarchy 且必须传 value_column 参数，一步完成层级提取和聚合
- ⚠️ extract_hierarchy 内置 sort_by/sort_order 参数，聚合后可直接排序，不需要额外 query 排序
- ⚠️ 禁止对同一个数据重复调用 to_table。如果已经输出过一次，不要换变量名再输出一次
- ⚠️ 当需求涉及"按区间/档次/等级分组统计"时（如"按金额分档"、"按年龄段统计"），必须用 calculate 的 if 嵌套表达式一次性生成分组列，然后 aggregate 汇总。禁止对每个档位分别 query 再 merge，这会浪费大量步骤

## 收尾规范（重要！）

完成分析后，你的最终文字输出（即 conclusion）必须遵守：

1. 一句话说明本次分析产出了什么（"已生成XX图表"、"已查询出XX数据"）
2. 必须列出每个产物的标题和类型（不要列路径，路径系统自动管理）
3. 不要重复表格里的数据明细（用户能直接看到图表/表格，主智能体会从 artifacts.preview 读取简单答案）
4. 不要说"如需图表请告诉我"——你已经生成过了

错误示例（禁止）：写一大段 Markdown 表格列前10名明细
正确示例：「已生成「2026年签约金额Top10」柱状图，并输出对应的明细表格（10行）。可直接向用户展示。」"""

_SOURCE_DESC = "数据源引用：table_id（原始表）、output_var（之前步骤的变量名）、或文件路径（CSV）"
_REF_DESC = "数据源引用：table_id / output_var / 文件路径"

ANALYSIS_TOOLS = [
    # ==================== 检索工具 ====================
    {
        "type": "function",
        "function": {
            "name": "search_data_tables",
            "description": "根据关键词语义搜索数据表。用自然语言描述你要找的数据表（如'客户信息'、'销售订单'），返回最相关的表列表。支持多次调用不同关键词搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，用自然语言描述想找的数据表",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回结果数量，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_data_tables",
            "description": "列出所有可用的数据表（兜底工具）。返回表名、描述和 table_id。当 search_data_tables 多次搜索仍未找到所需表时，用此工具查看全量列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "可选的关键词过滤，只返回名称或描述中包含该关键词的表",
                    },
                },
                "required": [],
            },
        },
    },
    # ==================== 加载工具 ====================
    {
        "type": "function",
        "function": {
            "name": "load_table",
            "description": "加载指定数据表到分析环境。在使用查询、聚合等分析方法前，必须先加载表。加载成功后返回表的完整字段信息，后续可通过 table_id 引用该表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_id": {
                        "type": "string",
                        "description": "要加载的表 ID（从 search_data_tables 或 list_data_tables 结果中获取）",
                    },
                },
                "required": ["table_id"],
            },
        },
    },
    # ==================== 概览工具 ====================
    {
        "type": "function",
        "function": {
            "name": "describe",
            "description": "数据概览工具。一步返回指定列的统计摘要：数据类型、非空数、唯一值数、唯一值示例（或 top N 高频值）、数值列的 min/max/mean。在开始分析前或不确定数据结构时使用，避免反复 query 探索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要统计的列名列表，不传则返回全部列的统计",
                    },
                    "max_unique": {
                        "type": "integer",
                        "description": "每列最多展示的唯一值数量，默认 20",
                    },
                },
                "required": ["source"],
            },
        },
    },
    # ==================== 分析工具 ====================
    {
        "type": "function",
        "function": {
            "name": "query",
            "description": "查询/过滤数据。支持列选择、条件过滤、排序、行数限制。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": _SOURCE_DESC,
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要返回的列名列表，不传则返回全部列",
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "过滤字段名"},
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "eq", "neq", "gt", "gte", "lt", "lte",
                                        "in", "not_in", "contains", "not_contains",
                                        "startswith", "endswith", "is", "is_not",
                                    ],
                                    "description": "过滤操作符（is/is_not 用于空值判断）",
                                },
                                "value": {"description": "过滤值"},
                            },
                            "required": ["column", "op", "value"],
                        },
                        "description": "过滤条件列表",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "排序字段",
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "排序方向，默认 asc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回行数上限，默认 100",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate",
            "description": "分组聚合。按指定列分组，对数值列执行聚合运算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "分组字段列表",
                    },
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "聚合字段"},
                                "function": {
                                    "type": "string",
                                    "enum": [
                                        "sum", "mean", "count", "min", "max",
                                        "median", "std", "var", "nunique",
                                    ],
                                    "description": "聚合函数",
                                },
                                "alias": {
                                    "type": "string",
                                    "description": "结果列别名（可选）",
                                },
                            },
                            "required": ["column", "function"],
                        },
                        "description": "聚合操作列表",
                    },
                    "sort_by": {"type": "string", "description": "排序字段"},
                    "sort_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "排序方向，默认 desc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回行数上限，默认 100",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "group_by", "aggregations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "merge",
            "description": "多表关联。将两个数据源通过指定列进行关联。",
            "parameters": {
                "type": "object",
                "properties": {
                    "left_ref": {
                        "type": "string",
                        "description": f"左表引用：{_REF_DESC}",
                    },
                    "right_ref": {
                        "type": "string",
                        "description": f"右表引用：{_REF_DESC}",
                    },
                    "left_on": {"type": "string", "description": "左表关联列"},
                    "right_on": {"type": "string", "description": "右表关联列"},
                    "how": {
                        "type": "string",
                        "enum": ["left", "inner", "outer", "right"],
                        "description": "关联方式，默认 left",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["left_ref", "right_ref", "left_on", "right_on"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pivot",
            "description": "透视表（长表→宽表）。按行维度和列维度交叉汇总。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "index": {"type": "string", "description": "行维度字段"},
                    "columns": {"type": "string", "description": "列维度字段"},
                    "values": {"type": "string", "description": "值字段"},
                    "agg_func": {
                        "type": "string",
                        "description": "聚合函数，默认 sum",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "index", "columns", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "列间运算/派生新列。支持四则运算、if(条件, 真值, 假值)条件表达式。可用函数: round, abs, min, max, sqrt, log, ceil, floor, to_float, to_int, null(空值), length(字符串长度), substr(子串), contains(包含), startswith, endswith, lower, upper, trim, replace, year/month/day(日期提取), isnull, notnull, coalesce。注意：这是 Python 表达式，不要使用 SQL 函数。示例：(1) 四则运算 expr='单价*数量' alias='总价'; (2) 分档 expr='if(金额<100000,\"小\",if(金额<500000,\"中\",if(金额<1000000,\"大\",\"特大\")))' alias='档位'; (3) 日期提取 expr='year(日期)' alias='年份'; (4) 字符串 expr='contains(名称,\"北京\")' alias='是否北京'。分档时务必一次 if 嵌套生成所有档位，不要分多次调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expr": {"type": "string", "description": "表达式"},
                                "alias": {"type": "string", "description": "新列名"},
                            },
                            "required": ["expr", "alias"],
                        },
                        "description": "计算操作列表",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "operations"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare",
            "description": "维度对比分析。按维度分组聚合，计算占比。可选基准列计算比率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "compare_column": {"type": "string", "description": "对比维度列"},
                    "value_column": {"type": "string", "description": "数值列"},
                    "agg_func": {
                        "type": "string",
                        "description": "聚合函数，默认 sum",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回前 N 项，默认 10",
                    },
                    "baseline_column": {
                        "type": "string",
                        "description": "基准列（可选）",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "compare_column", "value_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trend",
            "description": "时间趋势分析。按日期聚合为指定频率，计算环比变化率。可选 group_by。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "date_column": {"type": "string", "description": "日期列名"},
                    "value_column": {"type": "string", "description": "数值列"},
                    "freq": {
                        "type": "string",
                        "enum": ["D", "W", "M", "Q", "Y"],
                        "description": "聚合频率，默认 M（月）",
                    },
                    "agg_func": {
                        "type": "string",
                        "description": "聚合函数，默认 sum",
                    },
                    "group_by": {
                        "type": "string",
                        "description": "分组列（可选）",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "date_column", "value_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_hierarchy",
            "description": "层级路径解析：从路径列（如 '公司/事业部/中心/部门/组'）中提取指定层级的数据。可一步完成：解析层级结构 + 将子层级数据聚合到目标层级。⚠️ 必须传 value_column 参数，否则只返回层级映射表（未聚合），需要额外步骤手动聚合。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "path_column": {
                        "type": "string",
                        "description": "层级路径列名（如 '完整部门'），路径中的层级由分隔符分隔",
                    },
                    "target_level": {
                        "type": "integer",
                        "description": "目标层级（1-based，路径根为第1级）。例如路径 A/B/C/D/E 中，第4级是 D",
                    },
                    "separator": {
                        "type": "string",
                        "description": "路径分隔符，默认 '/'",
                    },
                    "value_column": {
                        "type": "string",
                        "description": "⚠️ 必须传入！需要聚合到目标层级的数值列（如 '合同总额(含税)'）。不传只返回层级映射表，需要额外 merge+aggregate",
                    },
                    "agg_func": {
                        "type": "string",
                        "description": "聚合函数，默认 'sum'",
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "排序依据的列名（如聚合后的列名），不传则默认按聚合值降序排列",
                    },
                    "sort_order": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "排序方向，默认 'desc'（降序）",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名，供后续步骤引用",
                    },
                },
                "required": ["source", "path_column", "target_level", "value_column"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "to_table",
            "description": "将数据转为结构化表格输出（给用户看的）。通常是分析链的最后一步。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要输出的列名列表，不传则输出全部列",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "最大输出行数，默认 50",
                    },
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名",
                    },
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "to_chart",
            "description": "生成图表图片文件。图表类型建议：趋势用 line，对比用 bar，占比用 pie，交叉分析用 stacked_bar。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": _SOURCE_DESC},
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "scatter", "stacked_bar", "grouped_bar"],
                        "description": "图表类型",
                    },
                    "x_column": {"type": "string", "description": "X 轴列名"},
                    "y_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Y 轴列名列表",
                    },
                    "title": {"type": "string", "description": "图表标题"},
                    "output_var": {
                        "type": "string",
                        "description": "输出变量名",
                    },
                },
                "required": ["source", "chart_type", "x_column", "y_columns", "title"],
            },
        },
    },
]

    # 允许 AnalysisAgent 调用的方法白名单
ALLOWED_METHODS = {
    # 检索
    "search_data_tables", "list_data_tables",
    # 加载
    "load_table",
    # 概览
    "describe",
    # 分析
    "query", "aggregate", "merge", "pivot",
    "calculate", "compare", "trend", "extract_hierarchy",
    # 输出
    "to_table", "to_chart",
}
