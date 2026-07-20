# 智能数据分析工具设计文档

> 关联设计：[数据分析子智能体设计](./data-analysis-subagent-design.md) §七
> 关联规范：[系统架构](../../.claude/rules/architecture.md)
> 创建日期：2026-06-03
> 状态：✅ 已实现（2026-06-09 初版，开发计划见 [smart-data-analysis-tool-dev-plan.md](./smart-data-analysis-tool-dev-plan.md)）

---

## 一、设计思路

### 1.1 核心原则：一个统一的智能分析工具，内置 LLM 编排

不再拆成多个独立小工具（query_table、aggregate_data、compare_data...），而是设计一个 `SmartDataAnalysisTool`，类似 Word 工具的设计模式：

1. **输入**：用户的原始分析需求 + 匹配到的所有相关表的 Metadata
2. **迷你 Agent 循环**：工具内部 LLM 通过 function calling 逐步调用 `DataAnalyzer` 方法，执行后将结果摘要反馈给 LLM，LLM 根据中间结果决定下一步，循环直到分析完成
3. **执行引擎**：`DataAnalyzer` 使用 pandas/numpy 执行每个方法调用，中间结果通过变量表传递
4. **输出**：结构化数据摘要 + 图表文件路径，供外层 LLM 生成自然语言回答和 HTML 报告

### 1.2 为什么用 pandas + numpy

- pandas 对 Excel 和数据库表的处理接口统一（`read_excel` / `read_sql` → 都是 DataFrame），加载不同数据源后逻辑完全一致
- DataFrame 的过滤、聚合、透视、合并等操作是数据分析的标准范式
- numpy 用于科学计算场景（如标准差、相关系数等），非必须但有则更完善

### 1.3 与多工具方案的对比

| 维度 | 多工具方案 | 统一工具方案（本设计） |
|------|----------|---------------------|
| LLM 调用次数 | 每个操作一次工具调用（多轮外层 Agent 循环） | 一次工具调用，内部 Agent 循环多步执行 |
| 编排者 | 外层 Agent 循环 | 工具内部迷你 Agent 循环 |
| 安全性 | 每个工具独立校验参数 | AnalysisAgent 方法白名单 + JSON Schema 参数校验 |
| 效率 | 多次 LLM 往返 | 内部 Agent 循环，LLM 可根据中间结果动态调整 |
| 多表关联 | 需要多轮工具调用 | 内部循环一次完成 |

---

## 二、整体架构

```
SmartDataAnalysisTool（对外工具接口）
  │
  ├── 输入：requirement（用户需求）+ tables_metadata（表 Schema）
  │
  ├── AnalysisAgent（迷你 Agent 循环）
  │     ├── analysis_tools_schema.py — 9 个分析方法的 JSON Schema 定义
  │     ├── Agent 循环：chat_with_tools → 执行 tool → 结果存临时文件 → 摘要反馈 LLM → 重复
  │     └── 直到 LLM 不再调用工具 → 输出最终分析结果
  │
  ├── DataAnalyzer（分析引擎，pandas/numpy）
  │     ├── query()         数据获取
  │     ├── aggregate()     分组聚合
  │     ├── merge()         多表关联
  │     ├── pivot()         透视表
  │     ├── calculate()     列间运算/条件表达式
  │     ├── compare()       维度对比
  │     ├── trend()         时间趋势
  │     ├── to_table()      结构化数据输出
  │     ├── to_chart()      图表图片文件
  │     └── load_related()  自动加载关联表（便捷方法）
  │
  └── 输出：tables（数据摘要）+ charts（图片路径）
```

---

## 三、对外工具接口

### 3.1 SmartDataAnalysisTool

```python
# src/tools/data_analysis/smart_analysis_tool.py

import uuid
from pydantic import BaseModel, Field
from typing import Optional
from src.tools.base import BaseTool


class AnalyzeDataInput(BaseModel):
    requirement: str = Field(
        description="用户的原始分析需求（自然语言）"
    )
    tables_metadata: list[dict] = Field(
        description="已匹配到的相关表的 Metadata 列表，每项包含 table_id、table_name、columns、relations、source 等完整信息"
    )
    session_id: Optional[str] = Field(
        None,
        description="当前会话 ID，用于 DataFrame 缓存"
    )


class SmartDataAnalysisTool(BaseTool):
    name = "analyze_data"
    description = (
        "智能数据分析工具。接收用户的分析需求和相关表的 Metadata，"
        "自动编排分析步骤（查询、聚合、关联、对比、趋势、透视、计算、可视化等），"
        "一次性完成分析并返回结果摘要和图表文件。"
    )
    display_name = "智能数据分析"
    category = "data_analysis"
    InputModel = AnalyzeDataInput

    async def execute(self, **kwargs) -> dict:
        requirement = kwargs["requirement"]
        tables_metadata = kwargs["tables_metadata"]
        session_id = kwargs.get("session_id")

        # 1. 初始化 DataAnalyzer（分析引擎）
        analyzer = DataAnalyzer(session_id=session_id)

        # 2. 加载相关表的 DataFrame 到 analyzer
        for meta in tables_metadata:
            await analyzer.load_table(meta)

        # 3. 运行迷你 Agent 循环
        from src.core import master_agent
        analysis_id = f"analysis_{uuid.uuid4().hex[:8]}"
        agent = AnalysisAgent(
            llm_gateway=master_agent.llm_gateway,
            analyzer=analyzer,
            analysis_id=analysis_id,
            tables_metadata=tables_metadata,
        )
        result = await agent.run(requirement)

        return result
```

---

## 四、DataAnalyzer — 分析引擎（pandas/numpy）

`DataAnalyzer` 是核心执行引擎，提供所有分析方法。每个方法接收参数、操作 DataFrame、返回新 DataFrame。

**设计原则**：
- 所有数据处理方法的 `source` 参数支持三种引用方式：
  1. `table_id` — 从数据源加载的原始表
  2. `output_var` — 之前步骤的中间结果变量名
  3. `file_path` — 磁盘上的 CSV 文件路径（LLM 通过文件路径传大数据）
- 每个步骤的 `output_var` 对应一个不可变的 DataFrame 快照 + 一个持久化的 CSV 文件
- 数据处理产出 DataFrame，输出方法（to_table/to_chart）产出结构化数据或文件路径
- **大数据安全**：DataFrame 始终持久化到文件，LLM 只拿到文件路径和摘要，不会因数据量大导致上下文爆炸

### 4.1 方法清单

| 方法 | 层级 | 职责 | pandas 核心操作 |
|------|------|------|----------------|
| `query` | 数据获取 | 过滤、列选择、排序、行限制 | `df[filter][columns].sort_values().head()` |
| `aggregate` | 数据处理 | 分组聚合 | `df.groupby().agg()` |
| `merge` | 数据处理 | 多表关联（left/inner/outer） | `pd.merge(left, right, on, how)` |
| `pivot` | 数据处理 | 透视表（长→宽） | `pd.pivot_table()` |
| `calculate` | 数据处理 | 列间运算、条件表达式、派生新列 | `df.assign()` + `np.where()` / `np.select()` |
| `compare` | 分析 | 维度对比 + 占比 + 基准对比 | `groupby().agg()` + 除法计算占比 |
| `trend` | 分析 | 时间趋势 + 环比 + 分组趋势 | `dt.to_period()` + `groupby()` + `pct_change()` |
| `to_table` | 输出 | 结构化数据摘要给 LLM | DataFrame → `{columns, rows}` 字典 |
| `to_chart` | 输出 | 生成图表图片文件 | matplotlib 保存 PNG |
| `load_related`（可选） | 便捷 | 根据 relations 自动加载关联表并合并 | `query` + `merge` 的语法糖 |

### 4.2 场景验证（10 个分析场景）

以下场景验证方法粒度和覆盖度，从简单到复杂：

#### 场景 1：单表简单查询
**需求**："查看华东区域最近的 10 条销售记录"
**步骤**：query(过滤华东, 排序desc, limit=10) → to_table

#### 场景 2：单表聚合
**需求**："按区域统计总销售额和总订单量"
**步骤**：aggregate(group_by=[区域], sum) → to_chart(bar)

#### 场景 3：多表关联
**需求**："销售额最高的前 10 个客户名称及其行业"
**步骤**：aggregate(按客户ID汇总) + query(客户表) → merge(客户ID) → to_table

#### 场景 4：对比分析
**需求**："对比各区域的销售额，并展示各区域占比"
**步骤**：compare(区域, 销售额) → to_chart(pie)

#### 场景 5：趋势分析
**需求**："分析销售额的月度趋势变化"
**步骤**：trend(月份, 销售额, freq=M) → to_chart(line)

#### 场景 6：目标达成率（需要 calculate）
**需求**："各区域的年度销售目标完成率是多少？"
**步骤**：aggregate(按区域汇总实际) + query(目标表) → merge → calculate(实际/目标) → to_chart(bar)

#### 场景 7：透视表
**需求**："生成交叉透视表，行是区域，列是产品线，值是销售额"
**步骤**：query(取3列) → pivot(行=区域, 列=产品线, 值=销售额) → to_table

#### 场景 8：多表关联 + 分维度趋势
**需求**："按客户行业分析月度销售趋势，看看哪个行业的增长最快"
**步骤**：query(销售表) + query(客户表) → merge → aggregate(行业+月份) → trend(group_by=行业) → to_chart

#### 场景 9：中间结果复用
**需求**："先看各区域的销售额排名，再看各产品线的排名，最后把两个结果合并成一个大表"
**步骤**：aggregate(区域) + aggregate(产品线) [并行] → to_chart(区域) + to_chart(产品线) [并行] → to_table(合并)

#### 场景 10：全链路复杂分析
**需求**："分析各行业客户的销售额月度趋势，找出增长最快的行业和衰退的行业，并给出各行业在各区域的分布透视表"
**步骤**：query(销售) + query(客户) [并行] → merge → aggregate(行业+月份) → trend(group_by=行业) → compare(行业, 环比) + pivot(行业×区域) [并行] → to_chart(multi)

#### 粒度验证结论

| 结论 | 说明 |
|------|------|
| `calculate` 是必须的 | 场景6暴露：列间运算（完成率、利润率）无法用其他方法替代 |
| `trend` 需要 `group_by` | 场景8、10：需要按行业分组看趋势 |
| 所有方法需支持 `data_ref` | 中间结果需要继续加工 |
| `to_chart` 需支持多数据源 | 场景10：一个报告引用多个中间结果 |
| `load_related` 非必须 | query + merge 已能覆盖，但作为便捷方法有价值 |

### 4.3 完整类定义

```python
# src/tools/data_analysis/data_analyzer.py

import os
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from loguru import logger


class DataAnalyzer:
    """数据分析引擎

    使用 pandas + numpy 实现所有分析操作。
    统一处理 Excel 和数据库数据源（都是 DataFrame）。
    中间结果通过变量表（_variables dict）传递。
    """

    CHART_OUTPUT_DIR = "storage/analysis_charts"

    def __init__(self, session_id: str = None):
        self.session_id = session_id
        # 变量表：存储每个步骤的输出 DataFrame
        # key: output_var 名, value: pd.DataFrame
        self._variables: dict[str, pd.DataFrame] = {}
        # 表注册表：table_id → DataFrame（从数据源加载的原始数据）
        self._tables: dict[str, pd.DataFrame] = {}
        # 表 Metadata：table_id → metadata dict
        self._table_meta: dict[str, dict] = {}

    # ===== 数据加载 =====

    async def load_table(self, metadata: dict) -> str:
        """根据 metadata 加载数据源为 DataFrame

        Args:
            metadata: 表的完整 metadata（包含 source、columns 等）

        Returns:
            table_id
        """
        table_id = str(metadata.get("table_id", metadata.get("doc_id", "")))
        source = metadata.get("source", {})
        source_type = source.get("type")

        df = None
        if source_type == "excel":
            df = self._load_excel(source)
        elif source_type == "database":
            df = await self._load_database(source)

        if df is not None:
            self._tables[table_id] = df
            self._table_meta[table_id] = metadata
            logger.info(f"[DataAnalyzer] 加载表 {metadata.get('table_name', table_id)}: {len(df)} 行, {len(df.columns)} 列")
        else:
            logger.warning(f"[DataAnalyzer] 表 {table_id} 加载失败")

        return table_id

    def _load_excel(self, source: dict) -> pd.DataFrame | None:
        """从 Excel/CSV 文件加载"""
        file_path = source.get("file_path")
        if not file_path or not os.path.exists(file_path):
            return None

        try:
            if file_path.endswith('.csv'):
                return pd.read_csv(file_path)
            else:
                sheet_name = source.get("sheet_name", 0)
                return pd.read_excel(file_path, sheet_name=sheet_name)
        except Exception as e:
            logger.error(f"[DataAnalyzer] Excel 加载失败: {e}")
            return None

    async def _load_database(self, source: dict) -> pd.DataFrame | None:
        """从外部数据库加载（通过 SQLAlchemy）"""
        connector_id = source.get("connector_id")
        db_table_name = source.get("db_table_name")
        if not connector_id or not db_table_name:
            return None

        try:
            from src.tools.data_analysis.db_connector import create_connector_from_record
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM data_connectors WHERE id = %s",
                    (connector_id,),
                )
                record = cursor.fetchone()

            if not record:
                return None

            connector = create_connector_from_record(dict(record))
            df = connector.get_sample_data(db_table_name, rows=10000)
            connector.dispose()
            return df
        except Exception as e:
            logger.error(f"[DataAnalyzer] 数据库加载失败: {e}")
            return None

    # ===== 变量管理与文件持久化 =====

    DATA_OUTPUT_DIR = "storage/analysis_data"

    def _resolve_source(self, source_ref: str) -> pd.DataFrame | None:
        """解析数据源引用，支持三种方式：
        1. 中间变量名（_variables）
        2. 原始表 table_id（_tables）
        3. 文件路径（以 / 或盘符开头或包含扩展名的路径）
        """
        # 优先查内存变量
        if source_ref in self._variables:
            return self._variables[source_ref].copy()
        if source_ref in self._tables:
            return self._tables[source_ref].copy()

        # 尝试作为文件路径加载
        if os.path.exists(source_ref):
            try:
                if source_ref.endswith('.csv'):
                    df = pd.read_csv(source_ref)
                elif source_ref.endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(source_ref)
                else:
                    return None
                logger.info(f"[DataAnalyzer] 从文件加载数据: {source_ref}, {len(df)} 行, {len(df.columns)} 列")
                return df
            except Exception as e:
                logger.error(f"[DataAnalyzer] 文件加载失败 {source_ref}: {e}")
                return None

        return None

    def _store(self, var_name: str, df: pd.DataFrame) -> str:
        """存储中间结果到内存 + 持久化到 CSV 文件

        Returns:
            文件路径
        """
        self._variables[var_name] = df.copy()

        # 持久化到文件
        os.makedirs(self.DATA_OUTPUT_DIR, exist_ok=True)
        file_path = os.path.join(self.DATA_OUTPUT_DIR, f"{var_name}.csv")
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        logger.info(f"[DataAnalyzer] 存储变量 {var_name}: {len(df)} 行 → {file_path}")

        return file_path

    # ===== 数据获取层 =====

    def query(self, source: str, columns: list[str] = None,
              filters: list[dict] = None, sort_by: str = None,
              sort_order: str = "asc", limit: int = 100) -> pd.DataFrame:
        """查询/过滤数据

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            columns: 要返回的列名列表，None 返回全部列
            filters: 过滤条件列表，每项 {"column": "...", "op": "...", "value": ...}
                支持操作符: eq, neq, gt, gte, lt, lte, in, not_in, contains, startswith
            sort_by: 排序字段
            sort_order: asc | desc
            limit: 返回行数上限

        Returns:
            过滤后的 DataFrame
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        # 过滤
        if filters:
            df = self._apply_filters(df, filters)

        # 列选择
        if columns:
            missing = set(columns) - set(df.columns)
            if missing:
                raise ValueError(f"列不存在: {missing}")
            df = df[columns]

        # 排序
        if sort_by:
            if sort_by not in df.columns:
                raise ValueError(f"排序列不存在: {sort_by}")
            ascending = sort_order == "asc"
            df = df.sort_values(by=sort_by, ascending=ascending)

        # 行数限制
        df = df.head(limit)

        return df.reset_index(drop=True)

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        """应用过滤条件（白名单操作符，防止注入）"""
        OP_MAP = {
            "eq": lambda col, val: df[col] == val,
            "neq": lambda col, val: df[col] != val,
            "gt": lambda col, val: df[col] > val,
            "gte": lambda col, val: df[col] >= val,
            "lt": lambda col, val: df[col] < val,
            "lte": lambda col, val: df[col] <= val,
            "in": lambda col, val: df[col].isin(val if isinstance(val, list) else [val]),
            "not_in": lambda col, val: ~df[col].isin(val if isinstance(val, list) else [val]),
            "contains": lambda col, val: df[col].astype(str).str.contains(str(val), na=False),
            "startswith": lambda col, val: df[col].astype(str).str.startswith(str(val), na=False),
        }
        mask = pd.Series([True] * len(df), index=df.index)
        for f in filters:
            col = f.get("column")
            op = f.get("op")
            val = f.get("value")
            if col not in df.columns:
                raise ValueError(f"过滤列不存在: {col}")
            op_func = OP_MAP.get(op)
            if not op_func:
                raise ValueError(f"不支持的操作符: {op}")
            try:
                mask &= op_func(col, val)
            except Exception as e:
                raise ValueError(f"过滤条件执行失败 [{col} {op} {val}]: {e}")
        return df[mask]

    # ===== 数据处理层 =====

    def aggregate(self, source: str, group_by: list[str],
                  aggregations: list[dict], sort_by: str = None,
                  sort_order: str = "desc", limit: int = 100) -> pd.DataFrame:
        """分组聚合

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            group_by: 分组字段列表
            aggregations: 聚合操作列表，每项:
                {"column": "列名", "function": "聚合函数", "alias": "结果列名(可选)"}
                聚合函数: sum, mean, count, min, max, median, std, var, nunique, first, last
            sort_by: 结果排序字段
            sort_order: asc | desc
            limit: 返回行数上限

        Returns:
            聚合后的 DataFrame
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        # 校验 group_by 列存在
        missing = set(group_by) - set(df.columns)
        if missing:
            raise ValueError(f"分组列不存在: {missing}")

        # 构建聚合映射
        SUPPORTED_FUNCS = {
            "sum", "mean", "count", "min", "max",
            "median", "std", "var", "nunique", "first", "last",
        }
        agg_dict = {}
        alias_map = {}
        for agg in aggregations:
            col = agg["column"]
            func = agg["function"]
            alias = agg.get("alias", f"{col}_{func}")

            if col not in df.columns:
                raise ValueError(f"聚合列不存在: {col}")
            if func not in SUPPORTED_FUNCS:
                raise ValueError(f"不支持的聚合函数: {func}，可用: {SUPPORTED_FUNCS}")

            if col not in agg_dict:
                agg_dict[col] = []
            agg_dict[col].append(func)
            alias_map[(col, func)] = alias

        # 执行聚合
        grouped = df.groupby(group_by, dropna=False).agg(agg_dict)
        # 展平 MultiIndex 列名 → 用 alias 替换
        grouped.columns = [
            alias_map.get((col, func), f"{col}_{func}")
            for col, func in grouped.columns
        ]
        result = grouped.reset_index()

        # 排序
        if sort_by:
            if sort_by in result.columns:
                ascending = sort_order == "asc"
                result = result.sort_values(by=sort_by, ascending=ascending)

        result = result.head(limit).reset_index(drop=True)
        return result

    def merge(self, left_ref: str, right_ref: str,
              left_on: str, right_on: str,
              how: str = "left") -> pd.DataFrame:
        """多表关联

        Args:
            left_ref: 左表引用 — table_id / output_var / 文件路径
            right_ref: 右表引用 — table_id / output_var / 文件路径
            left_on: 左表关联列
            right_on: 右表关联列
            how: 关联方式: left | inner | outer | right

        Returns:
            合并后的 DataFrame
        """
        left_df = self._resolve_source(left_ref)
        right_df = self._resolve_source(right_ref)
        if left_df is None:
            raise ValueError(f"左表不存在: {left_ref}")
        if right_df is None:
            raise ValueError(f"右表不存在: {right_ref}")

        if how not in ("left", "inner", "outer", "right"):
            raise ValueError(f"不支持的关联方式: {how}")

        result = pd.merge(left_df, right_df, left_on=left_on, right_on=right_on, how=how)
        return result.reset_index(drop=True)

    def pivot(self, source: str, index: str, columns: str,
              values: str, agg_func: str = "sum") -> pd.DataFrame:
        """透视表（长表 → 宽表）

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            index: 行维度字段
            columns: 列维度字段
            values: 值字段
            agg_func: 聚合函数

        Returns:
            透视后的宽表 DataFrame
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        for col_name, label in [(index, "行维度"), (columns, "列维度"), (values, "值")]:
            if col_name not in df.columns:
                raise ValueError(f"{label}列不存在: {col_name}")

        result = pd.pivot_table(df, index=index, columns=columns, values=values, aggfunc=agg_func)
        result = result.reset_index()
        # 展平列名
        result.columns = [
            str(col) if not isinstance(col, tuple) else col[-1]
            for col in result.columns
        ]
        return result

    def calculate(self, source: str, operations: list[dict]) -> pd.DataFrame:
        """列间运算 / 条件表达式 / 派生新列

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            operations: 运算列表，每项:
                - 简单运算: {"expr": "销售额 / 订单量", "alias": "客单价"}
                - 条件表达式: {"expr": "if(销售额 > 100, 'S', if(销售额 > 50, 'A', 'B'))", "alias": "客户等级"}
                - 数学函数: {"expr": "round(完成率 * 100, 1)", "alias": "完成率百分比"}

        Returns:
            添加了新列的 DataFrame（不修改原始数据）

        支持的表达式语法：
        - 四则运算: + - * / // %
        - 比较运算: > < >= <= == !=
        - 条件: if(条件, 真值, 假值)，支持嵌套
        - 数学函数: round, abs, log, sqrt, ceil, floor
        - 类型转换: to_float(列名), to_int(列名)
        - 列引用: 直接用列名
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        for op in operations:
            expr = op["expr"]
            alias = op["alias"]
            df[alias] = self._eval_expression(df, expr)

        return df

    def _eval_expression(self, df: pd.DataFrame, expr: str):
        """安全执行表达式（白名单函数，不允许任意代码执行）"""
        import math
        import re

        # 将列名替换为 Series 引用
        # 先按列名长度降序排列，避免短列名误替换长列名的一部分
        col_names = sorted(df.columns, key=len, reverse=True)
        eval_expr = expr
        for col in col_names:
            # 只替换独立的列名（前后不是字母数字下划线中文）
            eval_expr = re.sub(
                r'(?<![a-zA-Z0-9_一-鿿])' + re.escape(col) + r'(?![a-zA-Z0-9_一-鿿])',
                f'__df__["{col}"]',
                eval_expr
            )

        # 构建安全的执行命名空间
        safe_namespace = {
            "__df__": df,
            "np": np,
            "round": round,
            "abs": abs,
            "min": min,
            "max": max,
            "len": len,
            "math": math,
            "sqrt": np.sqrt,
            "log": np.log,
            "ceil": np.ceil,
            "floor": np.floor,
            "to_float": lambda s: pd.to_numeric(s, errors="coerce"),
            "to_int": lambda s: pd.to_numeric(s, errors="coerce").astype("Int64"),
            "if": np.where,  # if(condition, true_val, false_val) → np.where
        }

        # 处理嵌套 if: 将 if(...) 替换为 np.where(...)
        eval_expr = self._transform_if_expr(eval_expr)

        try:
            result = eval(eval_expr, {"__builtins__": {}}, safe_namespace)
            return result
        except Exception as e:
            raise ValueError(f"表达式执行失败 '{expr}': {e}")

    def _transform_if_expr(self, expr: str) -> str:
        """将 if(cond, true_val, false_val) 转换为 np.where(cond, true_val, false_val)

        支持嵌套 if，如: if(a>1, 'A', if(a>0, 'B', 'C'))
        """
        result = []
        i = 0
        while i < len(expr):
            # 检查是否匹配 "if("
            if expr[i:i+3] == "if(" and (i == 0 or not expr[i-1].isalnum()):
                result.append("np.where(")
                i += 3
            else:
                result.append(expr[i])
                i += 1
        return "".join(result)

    # ===== 分析层 =====

    def compare(self, source: str, compare_column: str, value_column: str,
                agg_func: str = "sum", top_n: int = 10,
                baseline_column: str = None) -> pd.DataFrame:
        """维度对比分析

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            compare_column: 对比维度列（如区域、产品线）
            value_column: 数值列
            agg_func: 聚合函数
            top_n: 取前 N 个分组
            baseline_column: 基准列（可选，用于计算完成率等比率）

        Returns:
            对比结果 DataFrame，包含占比列。
            若有 baseline_column，额外包含比率列。
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if compare_column not in df.columns:
            raise ValueError(f"对比列不存在: {compare_column}")
        if value_column not in df.columns:
            raise ValueError(f"数值列不存在: {value_column}")

        # 按对比维度聚合
        result = df.groupby(compare_column, dropna=False).agg({value_column: agg_func}).reset_index()
        result = result.sort_values(value_column, ascending=False).head(top_n)

        # 计算占比
        total = result[value_column].sum()
        if total != 0:
            result["占比(%)"] = (result[value_column] / total * 100).round(2)
        else:
            result["占比(%)"] = 0.0

        # 基准对比（如目标完成率）
        if baseline_column and baseline_column in df.columns:
            baseline_df = df.groupby(compare_column, dropna=False).agg({baseline_column: agg_func}).reset_index()
            result = pd.merge(result, baseline_df, on=compare_column, how="left")
            result["比率"] = np.where(
                result[baseline_column] != 0,
                (result[value_column] / result[baseline_column]).round(4),
                np.nan
            )

        return result.reset_index(drop=True)

    def trend(self, source: str, date_column: str, value_column: str,
              freq: str = "M", agg_func: str = "sum",
              group_by: str = None) -> pd.DataFrame:
        """时间趋势分析

        Args:
            source: 数据源引用 — table_id / output_var / 文件路径
            date_column: 日期列名
            value_column: 数值列名
            freq: 聚合频率: D(天)/W(周)/M(月)/Q(季)/Y(年)
            agg_func: 聚合函数: sum/mean/count
            group_by: 分组列名（可选，用于分组趋势，如按行业分别看趋势）

        Returns:
            趋势 DataFrame，包含 period、数值、环比变化率。
            若有 group_by，额外包含分组列。
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if date_column not in df.columns:
            raise ValueError(f"日期列不存在: {date_column}")
        if value_column not in df.columns:
            raise ValueError(f"数值列不存在: {value_column}")

        # 转日期类型
        df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
        df = df.dropna(subset=[date_column])
        df["period"] = df[date_column].dt.to_period(freq)

        # 分组聚合
        group_cols = [group_by, "period"] if group_by else ["period"]
        trend_df = df.groupby(group_cols, dropna=False).agg({value_column: agg_func}).reset_index()
        trend_df["period"] = trend_df["period"].astype(str)

        # 计算环比变化率
        if group_by:
            trend_df["环比变化率"] = trend_df.groupby(group_by)[value_column].pct_change()
        else:
            trend_df["环比变化率"] = trend_df[value_column].pct_change()

        return trend_df.reset_index(drop=True)

    # ===== 输出层 =====

    def to_table(self, source: str, columns: list[str] = None,
                 max_rows: int = 50) -> dict:
        """将 DataFrame 转为结构化数据（给 LLM 上下文用）

        Args:
            source: 数据源引用 — output_var / table_id / 文件路径
            columns: 指定列（可选）
            max_rows: 最大返回行数（控制 token 消耗）

        Returns:
            {"columns": [...], "rows": [[...], ...], "row_count": N, "total_count": M}
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if columns:
            df = df[[c for c in columns if c in df.columns]]

        total = len(df)
        df = df.head(max_rows)

        # 值转为原生 Python 类型（避免 numpy 类型序列化问题）
        rows = []
        for _, row in df.iterrows():
            rows.append([
                self._to_native(v) for v in row
            ])

        return {
            "columns": list(df.columns),
            "rows": rows,
            "row_count": len(rows),
            "total_count": total,
        }

    def to_chart(self, source: str, chart_type: str,
                 x_column: str, y_columns: list[str] = None,
                 title: str = "数据分析", group_by: str = None) -> dict:
        """生成图表图片文件

        使用 matplotlib 生成 PNG 图片，保存到 storage 目录。
        返回文件路径供外层 LLM 调用 HTML 生成工具。

        Args:
            source: 数据源引用 — output_var / table_id / 文件路径
            chart_type: 图表类型: bar | line | pie | scatter | stacked_bar | grouped_bar
            x_column: X 轴列名
            y_columns: Y 轴列名列表
            title: 图表标题
            group_by: 分组列（用于多系列图表）

        Returns:
            {"file_path": "...", "chart_type": "...", "title": "..."}
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        import matplotlib
        matplotlib.use('Agg')  # 非交互式后端
        import matplotlib.pyplot as plt
        # 中文字体
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
        plt.rcParams['axes.unicode_minus'] = False

        fig, ax = plt.subplots(figsize=(12, 6))

        if chart_type == "pie":
            values = df[y_columns[0]] if y_columns else df.iloc[:, 1]
            ax.pie(values, labels=df[x_column], autopct='%1.1f%%', startangle=90)
            ax.set_title(title)
        else:
            x = df[x_column]
            if not y_columns:
                y_columns = [c for c in df.columns if c != x_column]

            if chart_type in ("bar", "grouped_bar"):
                x_range = range(len(x))
                width = 0.8 / len(y_columns) if len(y_columns) > 1 else 0.6
                for i, y_col in enumerate(y_columns):
                    offset = (i - len(y_columns) / 2 + 0.5) * width
                    ax.bar([xi + offset for xi in x_range], df[y_col], width=width, label=y_col)
                ax.set_xticks(list(x_range))
                ax.set_xticklabels(x, rotation=45, ha='right')
            elif chart_type == "stacked_bar":
                x_range = range(len(x))
                bottom = np.zeros(len(x))
                for y_col in y_columns:
                    ax.bar(x_range, df[y_col], bottom=bottom, label=y_col)
                    bottom += df[y_col].values
                ax.set_xticks(list(x_range))
                ax.set_xticklabels(x, rotation=45, ha='right')
            elif chart_type == "line":
                for y_col in y_columns:
                    ax.plot(x, df[y_col], marker='o', label=y_col)
                ax.tick_params(axis='x', rotation=45)
            elif chart_type == "scatter":
                ax.scatter(df[x_column], df[y_columns[0]], alpha=0.6)
                if len(y_columns) > 1:
                    for y_col in y_columns[1:]:
                        ax.scatter(df[x_column], df[y_col], alpha=0.6, label=y_col)

            ax.set_title(title)
            ax.legend()
            ax.grid(axis='y', alpha=0.3)

        plt.tight_layout()

        # 保存文件
        os.makedirs(self.CHART_OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{title}_{timestamp}.png"
        file_path = os.path.join(self.CHART_OUTPUT_DIR, file_name)
        fig.savefig(file_path, dpi=150, bbox_inches='tight')
        plt.close(fig)

        return {
            "file_path": file_path,
            "chart_type": chart_type,
            "title": title,
            "data_columns": list(df.columns),
        }

    @staticmethod
    def _to_native(val):
        """将 numpy 类型转为 Python 原生类型"""
        if isinstance(val, (np.integer,)):
            return int(val)
        if isinstance(val, (np.floating,)):
            return float(val)
        if isinstance(val, (np.bool_,)):
            return bool(val)
        if isinstance(val, (np.datetime64,)):
            return str(val)
        if pd.isna(val):
            return None
        return val

    # ===== 便捷方法 =====

    def load_related(self, table_id: str, relation_name: str = None) -> pd.DataFrame:
        """根据 relations 配置自动加载关联表并合并（语法糖）

        Args:
            table_id: 主表 ID
            relation_name: 关联关系名称（可选，不传则加载第一个关联）

        Returns:
            合并后的 DataFrame
        """
        meta = self._table_meta.get(table_id)
        if not meta:
            raise ValueError(f"表 {table_id} 未加载")

        relations = meta.get("relations", [])
        if not relations:
            raise ValueError(f"表 {table_id} 没有关联关系配置")

        # 选择关联关系
        rel = None
        if relation_name:
            rel = next((r for r in relations if r.get("name") == relation_name), None)
        else:
            rel = relations[0]

        if not rel:
            raise ValueError(f"关联关系不存在: {relation_name}")

        # 找到关联表
        to_table = rel["to_table"]
        to_table_meta = next(
            (m for m in self._table_meta.values() if m.get("table_name") == to_table),
            None
        )
        if not to_table_meta:
            raise ValueError(f"关联表 {to_table} 未加载，请先加载关联表")

        to_table_id = str(to_table_meta.get("table_id", to_table_meta.get("doc_id", "")))
        left_df = self._resolve_source(table_id)
        right_df = self._resolve_source(to_table_id)

        return pd.merge(
            left_df, right_df,
            left_on=rel["from_column"],
            right_on=rel["to_column"],
            how="left"
        ).reset_index(drop=True)
```

---

## 五、AnalysisAgent — 迷你 Agent 循环

### 5.1 设计演进

**原方案（已废弃）**：先让 LLM 规划出完整步骤序列 JSON，再用 PlanExecutor 批量执行。问题在于：
- LLM 必须一次生成所有步骤，无法根据中间结果调整计划
- function calling 的 tool_calls 需要执行结果反馈才能继续，不能一次收集所有调用

**当前方案**：像主智能体一样，实现一个迷你 Agent 循环。LLM 每次调用一个分析方法工具，执行后将结果摘要反馈给 LLM，LLM 根据结果决定下一步。循环直到 LLM 不再调用工具。

```
while iteration < max_iterations:
    LLM + tools → tool_calls
    if 没有 tool_calls → 返回 LLM 的最终文本作为分析总结
    for each tool_call:
        执行 DataAnalyzer 方法
        结果存临时文件（防止上下文爆炸）
        文件路径 + 摘要反馈给 LLM
```

### 5.2 关键设计约束

| 约束 | 实现方式 |
|------|---------|
| **防止上下文爆炸** | DataAnalyzer 方法返回 DataFrame 后立即持久化到 CSV 文件，LLM 只收到文件路径 + 摘要（行数、列名、前几行预览）|
| **文件路径传递** | LLM 收到 `file_path` 后可在后续步骤的 `source` 参数中使用，DataAnalyzer 的 `_resolve_source` 自动识别文件路径并加载 |
| **token 用量记录** | 循环内每次 LLM 调用的 usage 累加，最终汇总到 `SmartDataAnalysisTool` 的返回结果中，供外层 `SessionRecordService` 记录 |
| **全链路跟踪** | 复用 `TraceCollector` 机制，每次 LLM 调用和工具执行都记录为 span，写入 trace |
| **最大迭代次数** | 默认 50 次，防止无限循环 |
| **与主系统一致** | system_prompt + messages + tools + chat_with_tools + tool result 反馈 — 和主智能体循环模式完全一致 |

### 5.3 方法 Tools Schema 定义

每个分析方法定义为一个 OpenAI function calling 格式的 tool：

```python
# src/tools/data_analysis/analysis_tools_schema.py

"""数据分析内部方法的 Tools Schema 定义"""

ANALYSIS_TOOLS = [
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
                        "description": "数据源引用：table_id（原始表）、output_var（之前步骤的变量名）、或文件路径（CSV）"
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要返回的列名列表，不传则返回全部列"
                    },
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "列名"},
                                "op": {
                                    "type": "string",
                                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "contains", "startswith"],
                                },
                                "value": {"description": "比较值"}
                            },
                            "required": ["column", "op", "value"]
                        },
                        "description": "过滤条件列表"
                    },
                    "sort_by": {"type": "string", "description": "排序字段"},
                    "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                    "limit": {"type": "integer", "description": "返回行数上限，默认 100"},
                    "output_var": {"type": "string", "description": "输出变量名，供后续步骤引用"}
                },
                "required": ["source", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate",
            "description": "分组聚合。按指定列分组，对数值列执行聚合运算。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "分组字段列表"
                    },
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "function": {
                                    "type": "string",
                                    "enum": ["sum", "mean", "count", "min", "max", "median", "std", "var", "nunique"],
                                },
                                "alias": {"type": "string", "description": "结果列名（可选）"}
                            },
                            "required": ["column", "function"]
                        },
                    },
                    "sort_by": {"type": "string"},
                    "sort_order": {"type": "string", "enum": ["asc", "desc"]},
                    "limit": {"type": "integer"},
                    "output_var": {"type": "string", "description": "输出变量名"}
                },
                "required": ["source", "group_by", "aggregations", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "merge",
            "description": "多表关联。将两个数据源通过指定列进行关联。",
            "parameters": {
                "type": "object",
                "properties": {
                    "left_ref": {"type": "string", "description": "左表引用：table_id / output_var / 文件路径"},
                    "right_ref": {"type": "string", "description": "右表引用：table_id / output_var / 文件路径"},
                    "left_on": {"type": "string", "description": "左表关联列"},
                    "right_on": {"type": "string", "description": "右表关联列"},
                    "how": {"type": "string", "enum": ["left", "inner", "outer", "right"]},
                    "output_var": {"type": "string", "description": "输出变量名"}
                },
                "required": ["left_ref", "right_ref", "left_on", "right_on", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "pivot",
            "description": "透视表（长表→宽表）。按行维度和列维度交叉汇总。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "index": {"type": "string", "description": "行维度字段"},
                    "columns": {"type": "string", "description": "列维度字段"},
                    "values": {"type": "string", "description": "值字段"},
                    "agg_func": {"type": "string", "description": "聚合函数，默认 sum"},
                    "output_var": {"type": "string"}
                },
                "required": ["source", "index", "columns", "values", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "列间运算/派生新列。支持四则运算和 if(条件, 真值, 假值)条件表达式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "operations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "expr": {"type": "string", "description": "表达式"},
                                "alias": {"type": "string", "description": "新列名"}
                            },
                            "required": ["expr", "alias"]
                        },
                    },
                    "output_var": {"type": "string"}
                },
                "required": ["source", "operations", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare",
            "description": "维度对比分析。按维度分组聚合，计算占比。可选基准列计算比率。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "compare_column": {"type": "string", "description": "对比维度列"},
                    "value_column": {"type": "string", "description": "数值列"},
                    "agg_func": {"type": "string"},
                    "top_n": {"type": "integer"},
                    "baseline_column": {"type": "string", "description": "基准列（可选）"},
                    "output_var": {"type": "string"}
                },
                "required": ["source", "compare_column", "value_column", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trend",
            "description": "时间趋势分析。按日期聚合为指定频率，计算环比变化率。可选 group_by。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "date_column": {"type": "string"},
                    "value_column": {"type": "string"},
                    "freq": {"type": "string", "enum": ["D", "W", "M", "Q", "Y"]},
                    "agg_func": {"type": "string"},
                    "group_by": {"type": "string", "description": "分组列（可选）"},
                    "output_var": {"type": "string"}
                },
                "required": ["source", "date_column", "value_column", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "to_table",
            "description": "将数据转为结构化表格输出（给 LLM 看的）。通常是分析链的最后一步。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "max_rows": {"type": "integer", "description": "默认 50"},
                    "output_var": {"type": "string"}
                },
                "required": ["source", "output_var"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "to_chart",
            "description": "生成图表图片文件。图表类型建议：趋势用 line，对比用 bar，占比用 pie，交叉分析用 stacked_bar。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "数据源引用：table_id / output_var / 文件路径"},
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "pie", "scatter", "stacked_bar", "grouped_bar"],
                    },
                    "x_column": {"type": "string", "description": "X 轴列名"},
                    "y_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "title": {"type": "string", "description": "图表标题"},
                    "output_var": {"type": "string"}
                },
                "required": ["source", "chart_type", "x_column", "output_var"]
            }
        }
    },
]
```

### 5.4 AnalysisAgent 实现

```python
# src/tools/data_analysis/analysis_agent.py

import json
import time
import uuid
from loguru import logger
from src.tools.data_analysis.analysis_tools_schema import ANALYSIS_TOOLS


ANALYSIS_SYSTEM_PROMPT = """你是一个数据分析专家。根据用户的分析需求和可用的数据表信息，调用合适的分析方法完成分析任务。

## 规则

1. 根据用户需求，逐步调用分析方法。每次调用后会看到执行结果（包含文件路径和摘要），据此决定下一步
2. source 参数支持三种引用：
   - table_id：原始数据表的 ID
   - output_var：之前步骤输出的变量名
   - file_path：CSV 文件路径（执行结果会返回文件路径，可以直接作为后续步骤的 source）
3. 数据处理方法（query/aggregate/merge/pivot/calculate/compare/trend）的结果会自动保存到文件，返回中包含 file_path
4. 输出方法（to_table/to_chart）生成最终结果：to_table 返回结构化表格数据，to_chart 生成图片文件
5. 最后应该调用 to_table 或 to_chart 将结果输出
6. 图表类型建议：趋势用 line，对比用 bar，占比用 pie，交叉分析用 stacked_bar
7. 完成所有分析步骤后，不要调用任何工具，直接用文字总结分析结论即可
"""

MAX_ITERATIONS = 50


class AnalysisAgent:
    """数据分析迷你 Agent

    实现和主智能体一致的 Agent 循环：
    LLM + tools → 执行 tool → 结果摘要反馈 LLM → 继续循环
    直到 LLM 不再调用工具，输出最终分析总结。

    不复用主 Agent 类的原因：
    - 分析工具是内部方法（DataAnalyzer），不是注册在 ToolRegistry 中的 BaseTool
    - 需要 DataFrame 变量传递机制（output_var）
    - 执行结果需要摘要化（防止大数据进入 LLM 上下文）
    - 有独立的 token 计数和 trace 记录
    """

    # DataAnalyzer 中可被 LLM 调用的方法白名单
    ALLOWED_METHODS = {
        "query", "aggregate", "merge", "pivot",
        "calculate", "compare", "trend",
        "to_table", "to_chart",
    }

    def __init__(self, llm_gateway, analyzer, analysis_id: str,
                 tables_metadata: list[dict]):
        self.llm = llm_gateway
        self.analyzer = analyzer
        self.analysis_id = analysis_id
        self.tables_metadata = tables_metadata

        # Token 用量累加
        self._total_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        # 收集最终输出
        self._tables_output = []
        self._charts_output = []
        # 步骤记录：每一步的方法、参数、结果摘要、文件路径
        self._steps = []
        # Trace spans
        self._spans = []

    async def run(self, requirement: str) -> dict:
        """运行分析 Agent 循环

        Returns:
            {
                "success": bool,
                "summary": str,                # LLM 生成的分析总结
                "tables": [...],               # to_table 的输出列表
                "charts": [...],               # to_chart 的输出列表
                "steps": [...],                # 每一步的完整记录（方法、参数摘要、结果、文件路径）
                "intermediate_files": [...],   # 所有中间文件汇总（供上层 Agent 引用）
                "total_usage": {...},           # token 用量汇总
                "iterations": int,              # 实际迭代次数
                "trace_id": str,                # 全链路跟踪 ID
            }
        """
        trace_id = f"tr_{self.analysis_id}_{uuid.uuid4().hex[:8]}"
        tables_info = self._build_tables_info(self.tables_metadata)
        user_message = f"## 用户需求\n{requirement}\n\n## 可用的数据表\n{tables_info}"

        messages = [{"role": "user", "content": user_message}]
        iteration = 0

        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info(f"[AnalysisAgent] iteration={iteration}, analysis_id={self.analysis_id}")

            # === LLM 调用 ===
            llm_start = time.time()
            try:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=ANALYSIS_TOOLS,
                    tool_choice="auto",
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_tokens=4000,
                )
            except Exception as e:
                logger.error(f"[AnalysisAgent] LLM 调用失败: {e}")
                return {
                    "success": False,
                    "error": f"LLM 调用失败: {e}",
                    "iterations": iteration,
                    "total_usage": self._total_usage,
                    "trace_id": trace_id,
                }

            llm_duration_ms = int((time.time() - llm_start) * 1000)

            # 累加 token 用量
            usage = response.get("usage", {})
            self._total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            self._total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            self._total_usage["total_tokens"] += usage.get("total_tokens", 0)

            # 记录 LLM span（用于 trace）
            self._spans.append({
                "span_id": f"sp_llm_{iteration}",
                "name": "llm_call",
                "start_time": llm_start,
                "duration_ms": llm_duration_ms,
                "usage": usage,
                "model": self.llm.get_model_name(),
                "provider": self.llm.get_provider_name(),
            })

            tool_calls = response.get("tool_calls", [])
            content = response.get("content", "")

            # === 没有工具调用 → LLM 认为分析完成 ===
            if not tool_calls:
                logger.info(f"[AnalysisAgent] 分析完成，共 {iteration} 次迭代")

                # 写入 trace
                self._persist_trace(trace_id, requirement, content, "completed", iteration)

                return {
                    "success": True,
                    "summary": content,
                    "tables": self._tables_output,
                    "charts": self._charts_output,
                    "steps": self._steps,
                    "intermediate_files": self._build_intermediate_files(),
                    "total_usage": self._total_usage,
                    "iterations": iteration,
                    "trace_id": trace_id,
                }

            # === 有工具调用 → 逐个执行并反馈 ===
            # 将 assistant 消息加入上下文
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", ""),
                        }
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                tool_id = tc.get("id", "")
                func = tc.get("function", {})
                method = func.get("name", "")
                args_str = func.get("arguments", "{}")

                try:
                    params = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    params = {}

                # 提取编排元数据
                output_var = params.pop("output_var", f"step_{iteration}")

                logger.info(f"[AnalysisAgent] 执行 {method} → {output_var}")

                # 执行并记录 tool span
                tool_start = time.time()
                result_feedback = await self._execute_tool(method, params, output_var)
                tool_duration_ms = int((time.time() - tool_start) * 1000)

                self._spans.append({
                    "span_id": f"sp_tool_{iteration}_{method}",
                    "name": f"tool:{method}",
                    "start_time": tool_start,
                    "duration_ms": tool_duration_ms,
                    "tool_args": args_str,
                    "output_var": output_var,
                    "success": result_feedback.get("success", True),
                })

                # 将 tool result 反馈给 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(result_feedback, ensure_ascii=False),
                })

        # 达到最大迭代次数
        logger.warning(f"[AnalysisAgent] 达到最大迭代次数 {MAX_ITERATIONS}")
        self._persist_trace(trace_id, requirement, content or "达到最大迭代次数", "max_iterations", iteration)

        return {
            "success": True,
            "summary": content or "分析完成（达到最大迭代次数）",
            "tables": self._tables_output,
            "charts": self._charts_output,
            "steps": self._steps,
            "intermediate_files": self._build_intermediate_files(),
            "total_usage": self._total_usage,
            "iterations": iteration,
            "trace_id": trace_id,
        }

    async def _execute_tool(self, method: str, params: dict, output_var: str) -> dict:
        """执行单个分析方法并返回结果摘要（给 LLM 看）

        关键设计：
        - 数据处理方法返回 DataFrame → 持久化到 CSV 文件 → 文件路径返回给 LLM
        - LLM 在后续步骤中可以把文件路径作为 source 参数传给其他方法
        - 只返回摘要信息（行数、列名、文件路径、前几行预览），不返回完整数据
        - 每次执行后记录步骤到 self._steps，供上层 Agent 了解分析过程
        """
        if method not in self.ALLOWED_METHODS:
            return {"success": False, "error": f"不允许的方法: {method}"}

        analyzer_method = getattr(self.analyzer, method, None)
        if not analyzer_method:
            return {"success": False, "error": f"方法不存在: {method}"}

        step_record = {
            "step": len(self._steps) + 1,
            "method": method,
            "output_var": output_var,
        }

        try:
            if method in ("to_table", "to_chart"):
                # 输出方法：返回 dict，直接收集
                result = analyzer_method(**params)
                result["success"] = True
                result["output_var"] = output_var

                if method == "to_table":
                    self._tables_output.append(result)
                    step_record.update({
                        "description": f"输出结构化表格: {result.get('row_count', 0)} 行",
                        "result_summary": {
                            "row_count": result.get("row_count", 0),
                            "total_count": result.get("total_count", 0),
                            "columns": result.get("columns", []),
                            "preview": result.get("rows", [])[:3],
                        },
                    })
                    self._steps.append(step_record)
                    return {
                        "success": True,
                        "type": "table",
                        "output_var": output_var,
                        "row_count": result.get("row_count", 0),
                        "total_count": result.get("total_count", 0),
                        "columns": result.get("columns", []),
                        "preview": result.get("rows", [])[:5],
                    }
                else:  # to_chart
                    self._charts_output.append(result)
                    step_record.update({
                        "description": f"生成{result.get('chart_type', '')}图表: {result.get('title', '')}",
                        "file_path": result.get("file_path", ""),
                        "chart_type": result.get("chart_type", ""),
                        "title": result.get("title", ""),
                    })
                    self._steps.append(step_record)
                    return {
                        "success": True,
                        "type": "chart",
                        "output_var": output_var,
                        "file_path": result.get("file_path", ""),
                        "chart_type": result.get("chart_type", ""),
                        "title": result.get("title", ""),
                    }
            else:
                # 数据处理方法：返回 DataFrame → 持久化到文件 → 返回路径 + 摘要
                df = analyzer_method(**params)
                file_path = self.analyzer._store(output_var, df)

                # 构建前 3 行预览（供上层 Agent 了解数据内容）
                preview_rows = []
                for _, row in df.head(3).iterrows():
                    preview_rows.append([DataAnalyzer._to_native(v) for v in row])

                step_record.update({
                    "description": self._describe_method(method, params),
                    "file_path": file_path,
                    "result_summary": {
                        "rows": len(df),
                        "columns": list(df.columns),
                        "preview_columns": list(df.columns[:5]),
                        "preview": preview_rows,
                    },
                })
                self._steps.append(step_record)

                # 给 LLM 的摘要：文件路径 + 元信息，不返回数据本身
                return {
                    "success": True,
                    "output_var": output_var,
                    "file_path": file_path,
                    "rows": len(df),
                    "columns": list(df.columns),
                    "preview_columns": list(df.columns[:5]),
                    "preview_rows": min(3, len(df)),
                    "hint": f"后续步骤可通过 output_var '{output_var}' 或文件路径 '{file_path}' 引用此数据",
                }

        except Exception as e:
            logger.error(f"[AnalysisAgent] 工具执行失败 {method}: {e}")
            step_record.update({
                "success": False,
                "error": str(e),
            })
            self._steps.append(step_record)
            return {"success": False, "error": str(e)}

    def _describe_method(self, method: str, params: dict) -> str:
        """生成方法调用的人类可读描述"""
        if method == "query":
            filters = params.get("filters")
            cols = params.get("columns")
            desc = "查询数据"
            if cols:
                desc += f"，选取列: {', '.join(cols[:3])}{'...' if len(cols) > 3 else ''}"
            if filters:
                desc += f"，{len(filters)}个过滤条件"
            return desc
        elif method == "aggregate":
            groups = params.get("group_by", [])
            aggs = params.get("aggregations", [])
            return f"按 {', '.join(groups)} 聚合，{len(aggs)}个聚合操作"
        elif method == "merge":
            how = params.get("how", "left")
            return f"{how} 关联 {params.get('left_ref', '?')} 和 {params.get('right_ref', '?')}"
        elif method == "pivot":
            return f"透视表: 行={params.get('index', '?')}, 列={params.get('columns', '?')}, 值={params.get('values', '?')}"
        elif method == "calculate":
            ops = params.get("operations", [])
            aliases = [op.get("alias", "?") for op in ops]
            return f"计算派生列: {', '.join(aliases)}"
        elif method == "compare":
            return f"对比分析: {params.get('compare_column', '?')} 按 {params.get('value_column', '?')}"
        elif method == "trend":
            freq_map = {"D": "日", "W": "周", "M": "月", "Q": "季", "Y": "年"}
            freq = freq_map.get(params.get("freq", "M"), "月")
            group = params.get("group_by")
            desc = f"{freq}度趋势: {params.get('value_column', '?')}"
            if group:
                desc += f"，按 {group} 分组"
            return desc
        return method

    def _build_intermediate_files(self) -> list[dict]:
        """从步骤记录中提取所有中间文件汇总"""
        files = []
        for step in self._steps:
            # 数据处理步骤有 file_path，输出步骤的 to_chart 也有 file_path
            file_path = step.get("file_path")
            if not file_path:
                continue
            # 去重（同一个 output_var 不重复记录）
            if any(f["output_var"] == step["output_var"] for f in files):
                continue
            entry = {
                "output_var": step["output_var"],
                "file_path": file_path,
                "description": step.get("description", ""),
                "method": step["method"],
            }
            # 补充行数和列信息（数据处理步骤有 result_summary）
            summary = step.get("result_summary", {})
            if "rows" in summary:
                entry["rows"] = summary["rows"]
                entry["columns"] = summary.get("columns", [])
            # 图表步骤补充图表类型和标题
            if "chart_type" in step:
                entry["chart_type"] = step["chart_type"]
                entry["title"] = step.get("title", "")
            files.append(entry)
        return files

    def _build_tables_info(self, tables_metadata: list[dict]) -> str:
        """构建给 LLM 的表信息摘要"""
        parts = []
        for meta in tables_metadata:
            table_id = str(meta.get("table_id", meta.get("doc_id", "")))
            table_name = meta.get("table_name", "")
            desc = meta.get("table_description", "")
            row_count = meta.get("row_count", "?")
            col_count = meta.get("column_count", "?")

            cols_desc = []
            for col in meta.get("columns", []):
                line = f"  {col['name']}({col.get('semantic_name', '')}): {col.get('data_type', 'text')}"
                if col.get("enum_values"):
                    line += f" 枚举:{col['enum_values'][:10]}"
                if col.get("is_id"):
                    line += " [ID]"
                if col.get("references"):
                    line += f" → {col['references']}"
                cols_desc.append(line)

            relations_desc = []
            for rel in meta.get("relations", []):
                relations_desc.append(
                    f"  {rel.get('from_column', '?')} → {rel.get('to_table', '?')}.{rel.get('to_column', '?')}"
                )

            source = meta.get("source", {})
            source_desc = f"来源:{source.get('type', '?')}"
            if source.get("type") == "database":
                source_desc += f" 表:{source.get('db_table_name', '?')}"

            part = (
                f"表ID: {table_id} (可用作 source 参数)\n"
                f"表名: {table_name}\n"
                f"描述: {desc}\n"
                f"数据: {row_count}行 × {col_count}列, {source_desc}\n"
                f"列:\n" + "\n".join(cols_desc)
            )
            if relations_desc:
                part += "\n关联:\n" + "\n".join(relations_desc)
            parts.append(part)

        return "\n\n".join(parts)

    def _persist_trace(self, trace_id: str, input_text: str,
                       output_text: str, status: str, iterations: int):
        """将分析过程的 trace 写入持久化（复用主系统的 trace_persist 机制）"""
        try:
            from src.core.trace_persist import schedule_persist
            from src.core.trace_collector import TraceRecord, SpanRecord
            import time as _time

            now = _time.time()
            trace = TraceRecord(
                trace_id=trace_id,
                session_id=self.analysis_id,
                tenant_id="",
                user_id="",
                subagent_id=None,
                input=input_text[:500],
                source_type="data_analysis",
                status=status,
                output=output_text[:500] if output_text else None,
                total_tokens=self._total_usage.get("total_tokens", 0),
                model=self.llm.get_model_name(),
                provider=self.llm.get_provider_name(),
                agent_iterations=iterations,
                duration_ms=sum(s.get("duration_ms", 0) for s in self._spans),
            )

            # 将收集的 spans 转为 SpanRecord
            for s in self._spans:
                span = SpanRecord(
                    span_id=s["span_id"],
                    name=s["name"],
                    start_time=s["start_time"],
                    duration_ms=s.get("duration_ms", 0),
                    success=s.get("success", True),
                    model=s.get("model"),
                    provider=s.get("provider"),
                    usage=s.get("usage"),
                    tool_args=s.get("tool_args"),
                )
                span.end_time = s["start_time"] + s.get("duration_ms", 0) / 1000.0
                trace.spans.append(span)

            schedule_persist(trace)
        except Exception as e:
            logger.warning(f"[AnalysisAgent] trace 持久化失败: {e}")
```

### 5.5 步骤记录与上下文透传

`SmartDataAnalysisTool` 作为内嵌 Agent 循环的工具，其执行过程中的中间文件和步骤对上层 Agent 不可见是一个问题。上层 Agent 需要：

1. **向用户解释分析过程**："我先按区域汇总了销售额，然后做了月度趋势分析..."
2. **引用中间结果**：用户追问"把刚才按区域的数据再按产品线拆一下"，上层 Agent 需要知道已有 `sales_by_region.csv`
3. **生成 HTML 报告**：报告可能引用多个中间图表和数据表，不仅仅是最终输出

**设计**：每次 `_execute_tool` 执行后，将步骤信息记录到 `self._steps`，返回结果中包含 `steps`（完整步骤记录）和 `intermediate_files`（去重的中间文件汇总）。

**步骤记录结构**：

```json
// steps[i] — 数据处理步骤
{
  "step": 1,
  "method": "aggregate",
  "output_var": "sales_by_region",
  "description": "按 区域 聚合，2个聚合操作",
  "file_path": "storage/analysis_data/sales_by_region.csv",
  "result_summary": {
    "rows": 6,
    "columns": ["区域", "总销售额", "订单量"],
    "preview_columns": ["区域", "总销售额", "订单量"],
    "preview": [["华东", 1200000, 350], ["华南", 980000, 280], ["华北", 850000, 210]]
  }
}

// steps[i] — 输出步骤（to_table）
{
  "step": 3,
  "method": "to_table",
  "output_var": "table_1",
  "description": "输出结构化表格: 6 行",
  "result_summary": {
    "row_count": 6,
    "total_count": 6,
    "columns": ["区域", "总销售额", "订单量"],
    "preview": [["华东", 1200000, 350], ...]
  }
}

// steps[i] — 输出步骤（to_chart）
{
  "step": 4,
  "method": "to_chart",
  "output_var": "chart_1",
  "description": "生成line图表: 各区域月度销售趋势",
  "file_path": "storage/analysis_charts/区域趋势_20260604_153000.png",
  "chart_type": "line",
  "title": "各区域月度销售趋势"
}
```

**中间文件汇总**（`intermediate_files`）：从 `_steps` 中提取，按 `output_var` 去重：

```json
[
  {
    "output_var": "sales_by_region",
    "file_path": "storage/analysis_data/sales_by_region.csv",
    "description": "按 区域 聚合，2个聚合操作",
    "method": "aggregate",
    "rows": 6,
    "columns": ["区域", "总销售额", "订单量"]
  },
  {
    "output_var": "chart_1",
    "file_path": "storage/analysis_charts/区域趋势_20260604_153000.png",
    "description": "生成line图表: 各区域月度销售趋势",
    "method": "to_chart",
    "chart_type": "line",
    "title": "各区域月度销售趋势"
  }
]
```

**上层 Agent 使用方式**：

- `steps` 数组拼入工具返回结果中，作为 `tool` 消息内容的一部分供上层 LLM 阅读
- `intermediate_files` 中的 `file_path` 可在用户追问时直接引用（如"基于刚才的 `sales_by_region` 数据再做..."
- `preview`（前 3 行）让上层 LLM 能判断数据质量，决定是否需要追问或重新分析

### 5.6 与多工具方案 / 原规划方案的对比

| 维度 | 多工具方案 | 原规划-执行方案（已废弃） | 迷你 Agent 循环（当前方案） |
|------|----------|----------------------|------------------------|
| LLM 调用次数 | 每个操作一次工具调用（多轮往返外层 Agent） | 1 次规划 + N 次执行 | N 次循环（内部闭环） |
| 执行策略 | 外层 Agent 循环驱动 | 先规划全量步骤，再批量执行 | LLM 边看结果边决策，可动态调整 |
| 中间结果 | 跨多轮工具调用，上下文不可控 | 全在内存，LLM 看不到 | 摘要反馈给 LLM，原始数据留在 DataAnalyzer |
| 参数可靠性 | 依赖外层 LLM 理解 | 依赖 LLM 一次规划正确 | JSON Schema + function calling |
| 与主系统关系 | 占用外层 Agent 循环 | 自成体系 | 复用 chat_with_tools + TraceCollector |

---

## 六、安全设计

### 6.1 威胁模型

| 威胁 | 防护层 | 措施 |
|------|--------|------|
| LLM 调用危险方法 | AnalysisAgent | `ALLOWED_METHODS` 白名单硬编码 |
| 恶意过滤条件注入 | DataAnalyzer.query | `_apply_filters` 操作符白名单 |
| calculate 表达式注入 | DataAnalyzer.calculate | `__builtins__: {}` 限制命名空间 + 函数白名单 |
| 大量数据泄露到 LLM | AnalysisAgent._execute_tool | DataFrame 只返回摘要给 LLM，原始数据留在变量表 |
| 大量数据导出 | query | `limit=100` 默认限制 |
| 无限循环 | AnalysisAgent | `MAX_ITERATIONS=50` 硬编码上限 |

### 6.2 calculate 表达式安全

`_eval_expression` 的安全措施：
- `__builtins__: {}` — 禁止 `import`、`exec`、`eval`、`open`、`__import__` 等
- 命名空间只暴露安全的数学函数（round、abs、sqrt、log 等）和 numpy
- 列名替换为 `__df__["列名"]` 的 Series 引用，不直接 eval 列名
- `if` 表达式转换为 `np.where`，不执行 Python if 语句

---

## 七、步骤间数据传递模式

Agent 循环模式下，步骤间数据传递通过 **文件持久化 + 变量名引用 + 步骤记录** 三通道实现：

**文件持久化通道**：
- 每个数据处理方法执行后，DataFrame 自动保存为 CSV 文件（`storage/analysis_data/{output_var}.csv`）
- LLM 在 tool result 中收到 `file_path`，后续步骤可直接将 `file_path` 作为 `source` 参数传入
- `DataAnalyzer._resolve_source()` 自动识别文件路径，通过 `pd.read_csv` 加载
- 适合大数据场景：文件路径只有几十个字符，不会撑爆 LLM 上下文

**变量名引用通道**：
- 每个 `output_var` 同时存入 `DataAnalyzer._variables` 内存变量表
- 后续方法通过 `output_var` 变量名作为 `source` 参数引用
- 内存中是 `df.copy()` 的不可变快照
- 适合小数据快速传递，不需要重新加载文件

**步骤记录通道**（§5.5 详述）：
- 每次工具执行后，步骤信息记录到 `AnalysisAgent._steps`
- 返回结果中包含 `steps`（完整步骤列表）和 `intermediate_files`（去重的中间文件汇总）
- 上层 Agent 可据此了解完整分析过程、引用中间文件、向用户解释分析步骤
- 步骤记录中的 `preview`（前 3 行数据）让上层 Agent 无需加载完整文件即可判断数据内容

**LLM 看到的信息**：
```json
{
  "success": true,
  "output_var": "sales_by_region",
  "file_path": "storage/analysis_data/sales_by_region.csv",
  "rows": 156,
  "columns": ["区域", "总销售额", "订单量"],
  "hint": "后续步骤可通过 output_var 'sales_by_region' 或文件路径 'storage/analysis_data/sales_by_region.csv' 引用此数据"
}
```

**上层 Agent 看到的信息**（工具返回结果）：
```json
{
  "success": true,
  "summary": "各区域销售额月度趋势分析...",
  "tables": [...],
  "charts": [...],
  "steps": [
    {"step": 1, "method": "aggregate", "output_var": "sales_by_region", "description": "按 区域 聚合，2个聚合操作", "file_path": "...", "result_summary": {...}},
    {"step": 2, "method": "trend", "output_var": "region_trend", "description": "月度趋势: 销售额，按 区域 分组", "file_path": "...", "result_summary": {...}},
    {"step": 3, "method": "to_chart", "output_var": "chart_1", "description": "生成line图表: 各区域月度销售趋势", "file_path": "...", "chart_type": "line", "title": "各区域月度销售趋势"}
  ],
  "intermediate_files": [
    {"output_var": "sales_by_region", "file_path": "storage/analysis_data/sales_by_region.csv", "rows": 6, "columns": [...]},
    {"output_var": "region_trend", "file_path": "storage/analysis_data/region_trend.csv", "rows": 36, "columns": [...]},
    {"output_var": "chart_1", "file_path": "storage/analysis_charts/区域趋势_20260604_153000.png", "chart_type": "line", "title": "各区域月度销售趋势"}
  ],
  "total_usage": {...},
  "iterations": 5,
  "trace_id": "..."
}
```

---

## 八、新增代码文件清单

| 文件路径 | 功能 | 估算行数 |
|---------|------|---------|
| `src/tools/data_analysis/smart_analysis_tool.py` | 统一智能分析工具（对外接口） | ~50 行 |
| `src/tools/data_analysis/data_analyzer.py` | 分析引擎（pandas/numpy，所有分析方法） | ~400 行 |
| `src/tools/data_analysis/analysis_tools_schema.py` | 9 个分析方法的 JSON Schema 定义 | ~200 行 |
| `src/tools/data_analysis/analysis_agent.py` | 迷你 Agent 循环（LLM 编排 + 工具执行 + 步骤记录 + trace + token 计数） | ~320 行 |

**总计约 ~970 行新增代码。**

### 依赖

```txt
# requirements.txt 新增
matplotlib>=3.7.0       # 图表生成（PNG 图片）
numpy>=1.24.0           # 科学计算（条件表达式、统计函数）
```
