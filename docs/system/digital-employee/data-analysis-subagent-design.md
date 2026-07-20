# 数据分析智能体设计文档

> 前序调研：[Text-to-SQL 与数据分析智能体技术调研](../../research/text-to-sql-data-analysis-agent-research.md)
> 关联设计：[知识库能力增强方案](../knowledge-base/knowledge-base-enhancement-design.md) §3.6
> 关联规范：[系统架构](.claude/rules/architecture.md)、[数据库开发规范](.claude/rules/database_dev.md)
> 创建日期：2026-05-29
> 最近更新：2026-07-20
> 状态：✅ 已实现（模块一/二/五/六均已上线；§八子智能体定义实际改由主智能体注册 `analyze_data` 工具实现，未单独创建 subagent 目录）

---

## 一、背景与目标

### 1.1 业务场景

企业用户需要对结构化数据进行分析和查询，数据来源多样：

| 场景 | 数据来源 | 典型问题 |
|------|---------|---------|
| 销售数据分析 | 上传的 Excel 销售报表 | "按区域统计本季度销售额" |
| 财务报表分析 | CSV 导出的流水数据 | "哪些支出类别超过预算？" |
| 多表关联分析 | 多个上传文件 | "对比这两个月的订单趋势" |
| 外部系统数据查询 | 连接的业务数据库 | "ERP 里最近 30 天新增了多少客户？" |
| 趋势预测 | 历史数据文件 | "预测下季度的销售额" |

### 1.2 现有能力盘点

| 能力 | 实现位置 | 局限 |
|------|---------|------|
| Excel 读取/解析 | `ExcelProcessTool` + openpyxl | 按行读取，不理解表格语义 |
| Excel 简单分析 | `ExcelAnalyzer` (pandas) | 仅 5 种预设分析（summary/correlation/distribution/anomaly/pivot） |
| Excel 图表 | `ExcelChart` (openpyxl.chart) | 仅嵌入 Excel 的静态图表，不支持交互 |
| 知识库向量检索 | `KnowledgeBaseService` + `HybridRetriever` | 仅文本 RAG，不感知结构化数据 Schema |
| 文件下载注册 | `_register_download()` | 只能下载 Excel 文件 |

### 1.3 建设目标

构建一个**数据分析子智能体**（Data Analyst Subagent），核心思路：

**不采用 LLM 直接生成 SQL/代码**，而是：
1. **数据接入层**：将 Excel 文件和外部数据库的表结构自动解析为标准化的 Table Metadata
2. **Metadata 知识库**：将表描述向量化存入知识库，列信息作为 metadata，利用现有混合检索能力匹配用户意图
3. **工具化分析**：提供预定义的 pandas 数据处理工具，LLM 只负责理解意图、选择工具、传参数

| 指标 | 目标 |
|------|------|
| 支持数据源 | 上传的 Excel/CSV 文件 + 第三方数据库连接 |
| Schema 提取 | LLM 自动推理表名、列语义、枚举值、关联关系 |
| 查询方式 | LLM 选工具+传参，工具执行 pandas 操作 |
| 安全性 | 只读、行数限制、租户隔离、代码 AST 白名单 |

---

## 二、核心架构

### 2.1 四个业务模块

```
                    ┌─────────────────────────────────┐
                    │      数据分析子智能体              │
                    └──────────┬──────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     ┌────────▼──────┐ ┌──────▼──────┐ ┌───────▼──────┐
     │  模块一        │ │  模块二      │ │  模块三       │
     │  Excel 智能    │ │  数据连接器  │ │  数据分析工具  │
     │  解析          │ │             │ │              │
     └───────┬───────┘ └──────┬──────┘ └───────┬──────┘
             │                │                 │
             │   提取 Table   │   提取 Table    │  读取 Table
             │   Metadata     │   Metadata      │  Metadata
             │                │                 │
             └────────┬───────┘                 │
                      │                         │
              ┌───────▼──────────┐              │
              │  模块四（共享）    │              │
              │  表 Metadata      │◄─────────────┘
              │  知识库           │   查询匹配到的表
              │  (复用现有向量    │
              │   检索能力)       │
              └──────────────────┘
```

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| Excel 智能解析 | 解析上传的 Excel/CSV（校验标准表格格式），提取 Schema | Excel/CSV 文件 | Table Metadata → 知识库 |
| 数据连接器 | 连接第三方数据库，发现表结构 | 数据库连接信息 | Table Metadata → 知识库 |
| 表 Metadata 知识库 | 存储和检索表结构信息 | 表描述（向量）+ 列信息（metadata） | 匹配到的表 Schema |
| 数据分析工具 | 基于预定义 pandas 工具分析数据 | 用户问题 → LLM 选工具+传参 | 分析结果/图表 |

### 2.2 核心数据流

系统包含两个完整流程，缺一不可：**数据源导入**是基础，**数据分析查询**是目标。

#### 流程一：数据源导入（基础 — 让系统"认识"你的数据）

数据必须先导入并注册到 Metadata 知识库，后续才能被查询分析。Excel/CSV 通过对话中上传触发，数据库表通过管理后台手动导入。

```
路径 A：Excel/CSV 文件（对话中上传）       路径 B：数据库表（管理后台操作）
─────────────────────────────            ──────────────────────────
用户上传 "销售报表.xlsx"                   用户创建数据连接器 → 选择要导入的表
  ↓                                         ↓
[SheetParser] 遍历 Sheet，校验格式          [DatabaseConnector] 连接远程数据库
  ↓ 不符合格式的 Sheet 跳过                  ↓
[DataFrame] 直接读取为 DataFrame             获取 DDL + 前 30 行样本数据
  ↓                                         ↓
[SchemaExtractor] LLM 推理列语义             [SchemaExtractor] LLM 推理列语义
  ↓                                         ↓
[前端 Schema 审核] 用户确认/修正表描述、      [前端 Schema 审核] 用户确认/修正
  列语义名、枚举值、关联关系                    ↓
  ↓                                    用户确认后保存
[用户确认] → 保存到知识库                     ↓
  ↓                                    保存到知识库
  └──────────────────┬──────────────────────┘
                     ↓
          ┌─────────────────────┐
          │  Metadata 知识库     │
          │  documents 表        │
          │  source_type =       │
          │  'data-analysis-     │
          │   metadata'          │
          │                     │
          │  存储内容：           │
          │  · 表名 + 表描述(向量) │
          │  · 列 Schema(metadata)│
          │  · 数据源定位信息     │
          │  · 表间关联关系       │
          └─────────────────────┘
```

#### 流程二：数据分析查询（目标 — 回答用户的数据问题）

用户在对话中提出数据问题，系统检索 Metadata 知识库找到相关表，然后用预定义 pandas 工具执行分析。

```
用户问题："按区域统计销售额"
    ↓
[1. 表匹配] 调用 knowledge_base_search(query="区域销售统计",
                  source_type="data-analysis-metadata")
    → 命中"销售明细表"，返回完整 metadata（含列信息、数据源定位）
    ↓
[2. 兜底（可选）] 如果向量检索未命中
    → 调用 list_data_tables 获取全量表名列表
    → 从中找到匹配的表 → 用表描述再检索一次
    ↓
[3. 智能分析] 调用 analyze_data(
        requirement="按区域统计销售额",
        tables_metadata=[{销售明细表的完整 metadata}]
    )
    ↓
    [SmartDataAnalysisTool 内部]
      → [AnalysisPlanner] LLM 读取表 Schema，编排分析步骤：
          Step 1: aggregate(group_by="区域", sum("销售额"))
          Step 2: to_table(结构化结果)
          Step 3: to_chart(柱状图)
      → [PlanExecutor] 按步骤执行：
          · 从 DataFrameStore 或原始文件加载 DataFrame
          · 执行 pandas groupby + sum
          · 生成表格摘要 + 图表
    ↓
[4. 格式化] 外层 LLM 解读分析结果，返回自然语言回答 + 图表
```

---

## 三、模块一：Excel 智能解析

### 3.1 核心流程

```
Excel/CSV 文件上传
  ↓
[1. Sheet 遍历] 遍历每个 Sheet，校验格式（第一行表头 + 后续数据行）
  ↓
  不符合格式 → 跳过该 Sheet，记录跳过原因
  ↓
[2. 数据提取] 将符合条件的 Sheet 直接转为 DataFrame（第一行作为列名）
  ↓
[3. LLM Schema 推理] 前 50 行数据 + 列名 + Sheet 名称 → LLM 推理语义信息
  ↓
[4. 用户审核] 前端展示 LLM 推理结果，用户确认或修正：
  - 表描述（表名默认取 Sheet 名称，用户可修改）
  - 列的语义名、数据类型、枚举值
  - 列间关联关系
  ↓
[5. Metadata 入库] 用户确认后的 Schema → 表描述做向量，列信息做 metadata，存入知识库
```

> **格式要求**：上传的 Excel 文件，每个 Sheet 必须是标准表格格式——第一行是表头，下面是数据行。不符合格式的 Sheet 直接跳过不解析。Sheet 名称作为默认表名。

> **关键原则**：LLM 推理的 Schema 必须经过用户审核确认后才入库。LLM 对业务专有名词、枚举值覆盖、表关联关系的推断不一定准确，用户需要有机会修正后再保存。已入库的 Schema 也支持后续修改更新。

### 3.2 Sheet 格式校验

每个 Sheet 按标准表格格式校验：第一行必须是非空表头行，后面至少有一行数据行。不符合格式的 Sheet 跳过。

```python
# src/tools/data_analysis/sheet_parser.py

import pandas as pd
from loguru import logger


class SheetParser:
    """解析 Excel/CSV 文件中的 Sheet，校验格式并提取 DataFrame"""

    def parse_workbook(self, file_path: str) -> list[dict]:
        """解析 Excel 文件的所有 Sheet（或 CSV 文件）

        Returns:
            [{"sheet_name": str, "table_name": str, "df": DataFrame,
              "row_count": int, "col_count": int}, ...]

            只返回通过格式校验的 Sheet。
        """
        if file_path.endswith('.csv'):
            return self._parse_csv(file_path)

        results = []
        xl = pd.ExcelFile(file_path)
        for sheet_name in xl.sheet_names:
            result = self._parse_sheet(file_path, sheet_name)
            if result:
                results.append(result)
        return results

    def _parse_csv(self, file_path: str) -> list[dict]:
        """解析 CSV 文件"""
        try:
            df = pd.read_csv(file_path)
            if len(df) == 0 or len(df.columns) == 0:
                logger.warning(f"[SheetParser] CSV 文件为空: {file_path}")
                return []
            import os
            table_name = os.path.splitext(os.path.basename(file_path))[0]
            return [{
                "sheet_name": None,
                "table_name": table_name,
                "df": df,
                "row_count": len(df),
                "col_count": len(df.columns),
            }]
        except Exception as e:
            logger.error(f"[SheetParser] CSV 解析失败: {e}")
            return []

    def _parse_sheet(self, file_path: str, sheet_name: str) -> dict | None:
        """解析单个 Sheet，校验格式

        格式要求：第一行是非空表头，后面至少有一行数据。
        不符合格式返回 None。
        """
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        except Exception as e:
            logger.warning(f"[SheetParser] Sheet '{sheet_name}' 读取失败: {e}")
            return None

        # 校验：不能为空
        if df.empty:
            logger.info(f"[SheetParser] Sheet '{sheet_name}' 为空，跳过")
            return None

        # 校验：列名不能有缺失（第一行必须完整）
        unnamed_count = sum(1 for c in df.columns if str(c).startswith("Unnamed"))
        if unnamed_count == len(df.columns):
            logger.info(f"[SheetParser] Sheet '{sheet_name}' 第一行全为空，跳过")
            return None

        # 校验：至少需要 2 列（单列数据没有分析价值）
        if len(df.columns) < 2:
            logger.info(f"[SheetParser] Sheet '{sheet_name}' 只有 {len(df.columns)} 列，跳过")
            return None

        return {
            "sheet_name": sheet_name,
            "table_name": sheet_name,  # Sheet 名称即默认表名
            "df": df,
            "row_count": len(df),
            "col_count": len(df.columns),
        }
```

### 3.3 LLM Schema 推理

将表格的前 50 行数据 + 列名发给 LLM，推理出语义信息：

```python
# src/tools/data_analysis/schema_extractor.py

SCHEMA_INFERENCE_PROMPT = """你是一个数据分析师。请分析以下表格数据，推理出每个列的语义信息。

表名（来自 Sheet）：{sheet_name}
前几行数据：
{sample_data}

请返回 JSON（只返回 JSON，不要其他内容）：
{{
  "table_description": "用 2-3 句话描述这个表记录了什么业务数据，包含哪些维度",
  "columns": [
    {{
      "name": "列名（原文）",
      "semantic_name": "语义化中文名（如：客户名称、订单金额）",
      "data_type": "数据类型：text | integer | decimal | date | datetime | boolean",
      "description": "这个字段记录什么，1 句话",
      "value_description": "值的含义说明。例如：'省份名称，如广东、浙江' 或 'Y/N，Y表示已完成' 或 '关联客户表的 customer_id'",
      "enum_values": ["如果值是有限的几个，列出所有可能值，否则空数组"],
      "is_id": false,
      "references": "如果这个字段关联其他表的 ID，填写 '表名.字段名'，否则为 null"
    }}
  ]
}}

分析要点：
1. 枚举值：如果某列只有有限的几个重复值（如状态、类型），全部列出
2. ID 关联：如果某列值是 xxx_id 形式或看起来是外键，标记 is_id=true 并推测关联的表
3. 数据类型：不要简单写 string/number，要区分 integer/decimal/date/datetime
4. 值说明：对于非显而易见的值（如编码、缩写），解释其含义

注意：不要返回 table_name 字段，表名直接使用 Sheet 名称。
"""

# 跨表关联关系推断 Prompt（在多个表已解析后调用）
RELATION_INFERENCE_PROMPT = """你是一个数据分析师。根据以下多个表的 Schema 信息，推断表之间的关联关系。

已注册的表：
{tables_schema}

请分析表之间的关联关系，返回 JSON 数组（只返回 JSON，不要其他内容）：
[
  {{
    "from_table": "来源表名",
    "from_column": "来源字段",
    "to_table": "目标表名",
    "to_column": "目标字段",
    "type": "关联类型：one_to_one | one_to_many | many_to_one | many_to_many",
    "description": "一句话说明这个关联的业务含义"
  }}
]

推断规则：
1. 如果字段名是 xxx_id 形式，且另一个表有同名字段或 id 字段，大概率是多对一关联
2. 如果两表有同名字段且该字段在某表中 is_id=true，通常是多对一关联
3. 如果某字段值看起来是唯一的（如订单号），且在另一表中也出现，可能是多对多（通过中间表）
4. 只返回有把握的关联，不确定的不要返回
5. 如果没有发现关联关系，返回空数组 []
"""


class SchemaExtractor:
    """用 LLM 推理表格的语义 Schema"""

    def __init__(self, llm_gateway):
        self.llm = llm_gateway

    async def extract(self, df: pd.DataFrame, table_info: dict) -> dict:
        """
        提取表的语义 Schema。

        Args:
            df: 表格数据（DataFrame）
            table_info: 来自 SheetParser 的解析结果，包含 table_name（Sheet 名称）

        Returns:
            {
                "table_name": "...",  # 来自 table_info，不依赖 LLM 返回
                "table_description": "...",
                "columns": [{...}]
            }
        """
        # 取前 50 行数据作为样本
        sample = df.head(50)
        sample_data = sample.to_string(index=False, max_colwidth=30)

        response = await self.llm.chat(
            messages=[{"role": "user", "content": SCHEMA_INFERENCE_PROMPT.format(
                sheet_name=table_info["table_name"],
                sample_data=sample_data
            )}],
            temperature=0.1,
            max_tokens=2000
        )

        content = response.strip()
        # 清理 Markdown 格式
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]

        import json
        result = json.loads(content.strip())
        # 表名直接使用 Sheet 名称，不依赖 LLM 推理
        result["table_name"] = table_info["table_name"]
        return result

    async def infer_relations(self, tables_meta: list[dict]) -> list[dict]:
        """推断多个已注册表之间的关联关系

        在用户审核 Schema 后调用，自动推断跨表关联关系（包括关联类型）。
        推断结果作为建议展示给用户确认/修正。

        Args:
            tables_meta: 已注册表的 metadata 列表，每项包含 table_name、columns 等

        Returns:
            关联关系列表，每项包含 from_table, from_column, to_table, to_column, type
        """
        # 构建 Schema 摘要（每个表只保留关键信息，控制 token）
        schema_parts = []
        for t in tables_meta:
            cols_desc = []
            for c in t.get("columns", []):
                col_line = f"    {c['name']}({c.get('semantic_name', '')}): {c.get('data_type', 'text')}"
                if c.get("is_id"):
                    col_line += " [ID]"
                if c.get("references"):
                    col_line += f" → {c['references']}"
                if c.get("enum_values"):
                    col_line += f" 枚举:{c['enum_values'][:5]}"
                cols_desc.append(col_line)
            schema_parts.append(
                f"表: {t.get('table_name', '')}\n  列:\n" + "\n".join(cols_desc)
            )

        tables_schema = "\n\n".join(schema_parts)

        response = await self.llm.chat(
            messages=[{"role": "user", "content": RELATION_INFERENCE_PROMPT.format(
                tables_schema=tables_schema
            )}],
            temperature=0.1,
            max_tokens=1500
        )

        content = response.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]

        import json
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            return []
```

### 3.4 数据加载与 DataFrame 缓存

**设计决策：不将 Excel 数据导入数据库，保留原始文件，通过 pandas 直接处理。**

理由：
- 数据分析场景中 Excel 是分析素材，不是持久化数据源，无需入库
- 动态建表的维护成本（Schema 演进、数据清理、租户隔离）远超收益
- 文件是真相来源（source of truth），不存在数据同步问题

**DataFrame 缓存策略**：Redis 优先 + 文件系统降级。

```
请求 DataFrame
  → [1] Redis GET（命中直接返回，pickle 反序列化）
  → [2] Redis 不可用 or 未命中 → 文件系统读取 pickle 缓存
  → [3] 文件也不存在 → 从原始 Excel 重新加载 + 写入 Redis 和文件缓存
```

| 缓存层 | 介质 | Key / 路径 | TTL | 适用条件 |
|--------|------|-----------|-----|---------|
| L1 | Redis | `df_cache:{session_id}:{table_id}` | 会话 TTL（默认 2h） | Redis 可用 |
| L2 | 文件系统 | `storage/df_cache/{session_id}/{table_id}.pkl` | 会话结束时清理 | Redis 不可用时的降级 |
| L3 | 原始文件 | 用户上传的 Excel 路径（记录在 documents.metadata） | 永久 | 兜底，< 1s 重加载 |

> **注意**：现有的 `RedisClient`（`src/core/redis_client.py`）使用 JSON 序列化，不支持二进制数据。
> DataFrame 缓存需要直接操作 Redis 实例（pickle bytes），绕过 RedisClient 的 JSON 层。
> 降级链路中 Redis 不可用时走文件系统，复用 RedisClient 已有的连接检测机制判断可用性。

```python
# src/tools/data_analysis/data_store.py

import os
import pickle
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger


class DataFrameStore:
    """管理已解析的 DataFrame 实例

    缓存策略：Redis（pickle bytes）→ 文件系统（pickle 文件）→ 原始文件重加载。
    按 session + table_id 索引，会话结束后自动过期。
    """

    REDIS_KEY_PREFIX = "df_cache"
    FILE_CACHE_DIR = "storage/df_cache"
    DEFAULT_TTL = 7200  # 2 小时

    def __init__(self):
        self._redis_client = None  # 延迟初始化，避免循环导入
        self._redis_available = None

    def _get_redis(self):
        """获取 Redis 原生客户端（非 RedisClient 的 JSON 封装），用于二进制存取"""
        if self._redis_available is False:
            return None
        try:
            from src.core.redis_client import redis_client
            # RedisClient 内部维护连接，_get_backend() 返回原生 redis 实例或内存降级
            if redis_client._connected and redis_client._client:
                return redis_client._client
            return None
        except Exception:
            self._redis_available = False
            return None

    def put(self, session_id: str, table_id: str, df: pd.DataFrame, meta: dict = None):
        """缓存 DataFrame 到 Redis + 文件系统"""
        data = pickle.dumps(df)

        # L1: Redis
        redis = self._get_redis()
        if redis:
            try:
                key = f"{self.REDIS_KEY_PREFIX}:{session_id}:{table_id}"
                redis.set(key, data, ex=self.DEFAULT_TTL)
            except Exception as e:
                logger.warning(f"[DataFrameStore] Redis 写入失败，降级到文件: {e}")

        # L2: 文件系统
        try:
            cache_dir = Path(self.FILE_CACHE_DIR) / session_id
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = cache_dir / f"{table_id}.pkl"
            cache_file.write_bytes(data)
        except Exception as e:
            logger.warning(f"[DataFrameStore] 文件缓存写入失败: {e}")

    def get(self, session_id: str, table_id: str) -> pd.DataFrame | None:
        """获取 DataFrame，按 L1 → L2 → 原始文件 的优先级查找"""
        # L1: Redis
        redis = self._get_redis()
        if redis:
            try:
                key = f"{self.REDIS_KEY_PREFIX}:{session_id}:{table_id}"
                data = redis.get(key)
                if data:
                    return pickle.loads(data)
            except Exception as e:
                logger.warning(f"[DataFrameStore] Redis 读取失败，尝试文件缓存: {e}")

        # L2: 文件系统
        cache_file = Path(self.FILE_CACHE_DIR) / session_id / f"{table_id}.pkl"
        if cache_file.exists():
            try:
                return pickle.loads(cache_file.read_bytes())
            except Exception as e:
                logger.warning(f"[DataFrameStore] 文件缓存读取失败: {e}")

        return None

    def get_or_load(self, session_id: str, table_id: str, file_path: str,
                    sheet_name: str = None, header_row: int = 0) -> pd.DataFrame | None:
        """获取或从原始文件加载 DataFrame"""
        df = self.get(session_id, table_id)
        if df is not None:
            return df

        # L3: 从原始文件加载
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
            self.put(session_id, table_id, df)
            return df
        except Exception as e:
            logger.error(f"[DataFrameStore] 原始文件加载失败: {e}")
            return None

    def list_tables(self, session_id: str) -> list[str]:
        """列出当前会话可用的表 ID"""
        tables = set()

        # 从 Redis 查找
        redis = self._get_redis()
        if redis:
            try:
                pattern = f"{self.REDIS_KEY_PREFIX}:{session_id}:*"
                keys = redis.keys(pattern)
                prefix = f"{self.REDIS_KEY_PREFIX}:{session_id}:"
                for k in keys:
                    key = k.decode() if isinstance(k, bytes) else k
                    tables.add(key[len(prefix):])
            except Exception:
                pass

        # 从文件系统查找
        cache_dir = Path(self.FILE_CACHE_DIR) / session_id
        if cache_dir.exists():
            for f in cache_dir.glob("*.pkl"):
                tables.add(f.stem)

        return list(tables)

    def cleanup_session(self, session_id: str):
        """清理会话的所有缓存"""
        # 清理 Redis
        redis = self._get_redis()
        if redis:
            try:
                pattern = f"{self.REDIS_KEY_PREFIX}:{session_id}:*"
                keys = redis.keys(pattern)
                if keys:
                    redis.delete(*keys)
            except Exception:
                pass

        # 清理文件
        cache_dir = Path(self.FILE_CACHE_DIR) / session_id
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir, ignore_errors=True)
```

---

## 四、模块二：数据连接器

### 4.1 核心功能

连接第三方数据库（MySQL、PostgreSQL、SQL Server 等），获取表列表和 DDL，用与 Excel 相同的 LLM Schema 推理流程提取 Metadata。

**技术选型：SQLAlchemy 2.0（统一数据库方言）**

| 维度 | 原方案（多适配器） | 新方案（SQLAlchemy） |
|------|------------------|---------------------|
| 数据库支持 | 每种 DB 写一个 Connector 类 | SQLAlchemy dialect 统一处理 |
| 异步支持 | 同步 pymysql/psycopg2 | `create_async_engine` + async 驱动 |
| 连接管理 | 每次 `connect()` 手动管理 | SQLAlchemy 连接池自动管理 |
| 方言差异 | 每个类里手写 SQL | `inspect()` + `text()` 统一 API |
| 扩展新 DB | 新增一个 Connector 子类 | 安装对应 dialect 驱动即可 |

```
数据库连接信息（host/port/db/user/password）
  ↓
[1. 连接测试] SQLAlchemy create_engine → engine.connect() 验证
  ↓
[2. 表发现] inspect(engine) → get_table_names()
  ↓
[3. DDL 提取] inspector.get_columns() + get_pk_constraint() → 拼装 DDL
  ↓
[4. 样本数据] pd.read_sql(text("SELECT * FROM :table LIMIT :n"), engine)
  ↓
[5. LLM Schema 推理] DDL + 样本数据 → LLM 推理语义（复用 SchemaExtractor）
  ↓
[6. 用户审核] 前端展示推理结果，用户确认或修正列语义、枚举值、关联关系
  ↓
[7. Metadata 入库] 用户确认后的 Schema → 存入知识库（来源标记为 database）
```

### 4.2 连接器注册表

```sql
-- deploy/init-postgres.sql 新增

-- 数据连接器：记录第三方数据库连接
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,                        -- 连接名称（如"ERP生产库"）
    db_type TEXT NOT NULL,                     -- mysql | postgresql | sqlserver | sqlite
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,          -- AES 加密存储
    options JSONB,                             -- 额外连接参数（SSL、charset 等）
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',        -- 已导入的表列表 [{table_name, doc_id, imported_at}]
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

### 4.3 SQLAlchemy 统一数据库连接器

```python
# src/tools/data_analysis/db_connector.py

from typing import Optional

import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine


# SQLAlchemy 连接字符串模板
DIALECT_URL_TEMPLATES = {
    "mysql": "mysql+pymysql://{username}:{password}@{host}:{port}/{database}",
    "postgresql": "postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}",
    "sqlserver": "mssql+pymssql://{username}:{password}@{host}:{port}/{database}",
    "sqlite": "sqlite:///{database}",
}

# 默认端口
DEFAULT_PORTS = {
    "mysql": 3306,
    "postgresql": 5432,
    "sqlserver": 1433,
    "sqlite": None,
}


class DatabaseConnector:
    """基于 SQLAlchemy 的统一数据库连接器

    通过 SQLAlchemy dialect 统一处理不同数据库方言，
    不再需要为每种数据库写单独的适配器类。
    """

    def __init__(self, db_type: str, config: dict):
        self.db_type = db_type
        self.config = config
        self._engine: Optional[Engine] = None

    def _build_url(self) -> str:
        """构建 SQLAlchemy 连接 URL"""
        template = DIALECT_URL_TEMPLATES.get(self.db_type)
        if not template:
            raise ValueError(f"不支持的数据库类型: {self.db_type}，"
                             f"支持的类型: {list(DIALECT_URL_TEMPLATES.keys())}")

        config = {
            "username": self.config["username"],
            "password": self.config.get("password", ""),  # 已解密的明文密码
            "host": self.config.get("host", "localhost"),
            "port": self.config.get("port", DEFAULT_PORTS.get(self.db_type, 3306)),
            "database": self.config["database_name"],
        }
        url = template.format(**config)

        # 附加额外参数（如 charset、ssl 等）
        options = self.config.get("options", {})
        if options:
            params = "&".join(f"{k}={v}" for k, v in options.items() if v is not None)
            if params:
                url += f"?{params}"

        return url

    def get_engine(self) -> Engine:
        """获取 SQLAlchemy Engine（懒创建，带连接池）"""
        if self._engine is None:
            url = self._build_url()
            self._engine = create_engine(
                url,
                pool_size=3,
                max_overflow=5,
                pool_pre_ping=True,       # 自动检测断开连接
                pool_recycle=1800,        # 30 分钟回收
                connect_args={"connect_timeout": 10},
            )
        return self._engine

    def test_connection(self) -> dict:
        """测试连接是否可用

        Returns:
            {"success": bool, "message": str, "db_version": str}
        """
        try:
            engine = self.get_engine()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                result.fetchone()

                # 获取数据库版本信息
                version = self._get_db_version(conn)

            return {"success": True, "message": "连接成功", "db_version": version}
        except Exception as e:
            logger.warning(f"[DBConnector] 连接测试失败: {e}")
            return {"success": False, "message": f"连接失败: {e}"}

    def list_tables(self) -> list[dict]:
        """获取数据库中的表列表

        Returns:
            [{"table_name": str, "schema": str, "comment": str | None}]
        """
        engine = self.get_engine()
        inspector = inspect(engine)
        tables = []

        for table_name in inspector.get_table_names():
            comment = None
            try:
                # 获取表注释（MySQL/PG 支持）
                table_info = inspector.get_table_comment(table_name)
                comment = table_info.get("text")
            except Exception:
                pass

            tables.append({
                "table_name": table_name,
                "schema": inspector.default_schema_name or "public",
                "comment": comment,
            })

        return tables

    def get_ddl(self, table_name: str) -> str:
        """获取表的 DDL（从 inspector 信息拼装）"""
        engine = self.get_engine()
        inspector = inspect(engine)

        columns = inspector.get_columns(table_name)
        pk = inspector.get_pk_constraint(table_name)

        ddl_lines = [f"CREATE TABLE {table_name} ("]
        for col in columns:
            line = f"  {col['name']} {col.get('type', 'TEXT')}"
            if not col.get("nullable", True):
                line += " NOT NULL"
            if col.get("default") is not None:
                line += f" DEFAULT {col['default']}"
            ddl_lines.append(line + ",")

        if pk and pk.get("constrained_columns"):
            cols = ", ".join(pk["constrained_columns"])
            ddl_lines[-1] = ddl_lines[-1].rstrip(",")
            ddl_lines.append(f"  PRIMARY KEY ({cols}),")

        # 去掉最后一个逗号
        ddl_lines[-1] = ddl_lines[-1].rstrip(",")
        ddl_lines.append(")")

        return "\n".join(ddl_lines)

    def get_sample_data(self, table_name: str, rows: int = 30) -> pd.DataFrame:
        """获取表的样本数据（前 N 行）"""
        engine = self.get_engine()
        # 使用 text() + 参数化查询防止 SQL 注入
        # table_name 无法参数化，用白名单校验
        safe_tables = {t["table_name"] for t in self.list_tables()}
        if table_name not in safe_tables:
            raise ValueError(f"表不存在: {table_name}")

        query = text(f"SELECT * FROM {table_name} LIMIT :limit")
        return pd.read_sql(query, engine, params={"limit": rows})

    def _get_db_version(self, conn) -> str:
        """获取数据库版本信息（按方言区分）"""
        version_queries = {
            "mysql": "SELECT VERSION()",
            "postgresql": "SELECT version()",
            "sqlserver": "SELECT @@VERSION",
        }
        query = version_queries.get(self.db_type)
        if query:
            try:
                result = conn.execute(text(query))
                row = result.fetchone()
                return str(row[0]) if row else "unknown"
            except Exception:
                pass
        return "unknown"

    def dispose(self):
        """关闭连接池"""
        if self._engine:
            self._engine.dispose()
            self._engine = None


def create_connector_from_record(record: dict) -> DatabaseConnector:
    """从数据库记录创建连接器实例（自动解密密码）"""
    from src.tools.data_analysis.crypto import decrypt_password

    config = {
        "username": record["username"],
        "password": decrypt_password(record["password_encrypted"]),
        "host": record.get("host"),
        "port": record.get("port"),
        "database_name": record["database_name"],
        "options": record.get("options", {}),
    }
    return DatabaseConnector(record["db_type"], config)
```

### 4.4 密码加密

```python
# src/tools/data_analysis/crypto.py

import base64
import os

from loguru import logger


def encrypt_password(plaintext: str, key: str = None) -> str:
    """AES-GCM 加密密码

    使用环境变量 DATA_ANALYSIS_ENCRYPTION_KEY 作为密钥。
    返回 base64 编码的 nonce + ciphertext。
    """
    if not key:
        from src.config.settings import settings
        key = getattr(settings, 'data_analysis', None)
        if key:
            key = getattr(key, 'db_connector', None)
            if key:
                key = getattr(key, 'password_encryption_key', None)
        if not key:
            raise ValueError("未配置 DATA_ANALYSIS_ENCRYPTION_KEY")

    from cryptography.fernet import Fernet
    import hashlib

    # 确保密钥是 32 字节（Fernet 要求）
    derived_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    f = Fernet(derived_key)
    return f.encrypt(plaintext.encode()).decode()


def decrypt_password(ciphertext: str, key: str = None) -> str:
    """解密密码"""
    if not key:
        from src.config.settings import settings
        key = getattr(settings, 'data_analysis', None)
        if key:
            key = getattr(key, 'db_connector', None)
            if key:
                key = getattr(key, 'password_encryption_key', None)
        if not key:
            raise ValueError("未配置 DATA_ANALYSIS_ENCRYPTION_KEY")

    from cryptography.fernet import Fernet
    import hashlib

    derived_key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    f = Fernet(derived_key)
    return f.decrypt(ciphertext.encode()).decode()
```

### 4.5 连接器管理 API

```python
# src/api/data_analysis.py

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.db.database import get_db_connection
from src.tools.data_analysis.db_connector import (
    DatabaseConnector, create_connector_from_record
)
from src.tools.data_analysis.crypto import encrypt_password

router = APIRouter(prefix="/api/data-analysis", tags=["数据分析"])


# ===== Request Models =====

class ConnectorCreateRequest(BaseModel):
    name: str = Field(..., description="连接名称", min_length=1, max_length=100)
    db_type: str = Field(..., description="数据库类型: mysql | postgresql | sqlserver | sqlite")
    host: Optional[str] = Field(None, description="主机地址")
    port: Optional[int] = Field(None, description="端口号")
    database_name: str = Field(..., description="数据库名")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码（明文，服务端加密存储）")
    options: Optional[dict] = Field(None, description="额外连接参数")

class ConnectorUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, description="连接名称")
    host: Optional[str] = Field(None, description="主机地址")
    port: Optional[int] = Field(None, description="端口号")
    database_name: Optional[str] = Field(None, description="数据库名")
    username: Optional[str] = Field(None, description="用户名")
    password: Optional[str] = Field(None, description="密码（不修改则不传）")
    options: Optional[dict] = Field(None, description="额外连接参数")
    is_active: Optional[bool] = Field(None, description="是否启用")

class ConnectorTestRequest(BaseModel):
    db_type: str = Field(..., description="数据库类型")
    host: Optional[str] = Field(None)
    port: Optional[int] = Field(None)
    database_name: str = Field(..., description="数据库名")
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")
    options: Optional[dict] = None

class ImportTablesRequest(BaseModel):
    table_names: list[str] = Field(..., description="要导入的表名列表")


# ===== API 路由 =====

@router.post("/connectors")
async def create_connector(request: Request, body: ConnectorCreateRequest):
    """创建数据连接器"""
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)

    # 先测试连接是否可用
    connector = DatabaseConnector(body.db_type, {
        "username": body.username,
        "password": body.password,
        "host": body.host,
        "port": body.port,
        "database_name": body.database_name,
        "options": body.options or {},
    })
    test_result = connector.test_connection()
    if not test_result["success"]:
        return {"success": False, "error": f"连接测试失败: {test_result['message']}"}
    connector.dispose()

    # 加密密码后存储
    encrypted_pwd = encrypt_password(body.password)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        import json
        cursor.execute("""
            INSERT INTO data_connectors (
                tenant_id, name, db_type, host, port, database_name,
                username, password_encrypted, options, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            tenant_id, body.name, body.db_type, body.host, body.port,
            body.database_name, body.username, encrypted_pwd,
            json.dumps(body.options or {}, ensure_ascii=False), user_id,
        ))
        connector_id = cursor.fetchone()["id"]
        conn.commit()

    return {"success": True, "id": str(connector_id), "message": "连接器创建成功"}


@router.get("/connectors")
async def list_connectors(request: Request):
    """列出当前租户的连接器"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, db_type, host, port, database_name, username,
                   is_active, imported_tables, last_sync_at, created_at
            FROM data_connectors
            WHERE tenant_id = %s
            ORDER BY created_at DESC
        """, (tenant_id,))
        rows = cursor.fetchall()

    # 不返回密码
    result = []
    for row in rows:
        result.append({
            "id": str(row["id"]),
            "name": row["name"],
            "db_type": row["db_type"],
            "host": row["host"],
            "port": row["port"],
            "database_name": row["database_name"],
            "username": row["username"],
            "is_active": row["is_active"],
            "imported_tables": row.get("imported_tables", []),
            "last_sync_at": str(row["last_sync_at"]) if row.get("last_sync_at") else None,
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
        })
    return {"success": True, "data": result}


@router.put("/connectors/{connector_id}")
async def update_connector(request: Request, connector_id: str, body: ConnectorUpdateRequest):
    """更新连接器"""
    tenant_id = getattr(request.state, "tenant_id", None)

    updates = []
    params = []
    for field in ["name", "host", "port", "database_name", "username", "is_active"]:
        val = getattr(body, field, None)
        if val is not None:
            updates.append(f"{field} = %s")
            params.append(val)

    if body.password:
        updates.append("password_encrypted = %s")
        params.append(encrypt_password(body.password))

    if body.options is not None:
        import json
        updates.append("options = %s")
        params.append(json.dumps(body.options, ensure_ascii=False))

    if not updates:
        return {"success": False, "error": "没有需要更新的字段"}

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.extend([connector_id, tenant_id])

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE data_connectors SET {', '.join(updates)}
            WHERE id = %s AND tenant_id = %s
        """, params)
        conn.commit()

    return {"success": True, "message": "更新成功"}


@router.delete("/connectors/{connector_id}")
async def delete_connector(request: Request, connector_id: str):
    """删除连接器"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM data_connectors WHERE id = %s AND tenant_id = %s",
            (connector_id, tenant_id),
        )
        conn.commit()

    return {"success": True, "message": "删除成功"}


@router.post("/connectors/test")
async def test_connection(body: ConnectorTestRequest):
    """测试连接（创建前预览，不存储）"""
    connector = DatabaseConnector(body.db_type, {
        "username": body.username,
        "password": body.password,
        "host": body.host,
        "port": body.port,
        "database_name": body.database_name,
        "options": body.options or {},
    })
    result = connector.test_connection()
    connector.dispose()
    return result


@router.post("/connectors/{connector_id}/test")
async def test_existing_connector(request: Request, connector_id: str):
    """测试已保存的连接器"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM data_connectors WHERE id = %s AND tenant_id = %s",
            (connector_id, tenant_id),
        )
        record = cursor.fetchone()

    if not record:
        raise HTTPException(status_code=404, detail="连接器不存在")

    connector = create_connector_from_record(dict(record))
    result = connector.test_connection()
    connector.dispose()
    return result


@router.get("/connectors/{connector_id}/tables")
async def list_remote_tables(request: Request, connector_id: str):
    """获取远程数据库的表列表"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM data_connectors WHERE id = %s AND tenant_id = %s",
            (connector_id, tenant_id),
        )
        record = cursor.fetchone()

    if not record:
        raise HTTPException(status_code=404, detail="连接器不存在")

    connector = create_connector_from_record(dict(record))
    tables = connector.list_tables()
    connector.dispose()

    # 标记已导入的表
    imported = set()
    imported_tables = record.get("imported_tables") or []
    for t in imported_tables:
        if isinstance(t, dict):
            imported.add(t.get("table_name"))

    for table in tables:
        table["imported"] = table["table_name"] in imported

    return {"success": True, "data": tables}


@router.post("/connectors/{connector_id}/import")
async def import_tables(request: Request, connector_id: str, body: ImportTablesRequest):
    """导入选中的表（DDL + 样本数据 → LLM Schema 推理 → 存入知识库）"""
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM data_connectors WHERE id = %s AND tenant_id = %s",
            (connector_id, tenant_id),
        )
        record = cursor.fetchone()

    if not record:
        raise HTTPException(status_code=404, detail="连接器不存在")

    connector = create_connector_from_record(dict(record))

    # 用 SchemaExtractor + MetadataManager 批量处理
    from src.tools.data_analysis.schema_extractor import SchemaExtractor
    from src.tools.data_analysis.metadata_manager import TableMetadataManager
    from src.core import master_agent

    llm = master_agent.llm_gateway
    schema_extractor = SchemaExtractor(llm)
    metadata_manager = TableMetadataManager(llm_gateway=llm)

    results = []
    for table_name in body.table_names:
        try:
            ddl = connector.get_ddl(table_name)
            df = connector.get_sample_data(table_name, rows=30)

            # LLM Schema 推理
            schema = await schema_extractor.extract(df, {"name": table_name})

            # 注册到知识库
            import_result = await metadata_manager.register_database_table(
                connector_id=connector_id,
                db_table_name=table_name,
                df=df,
                ddl=ddl,
                tenant_id=tenant_id,
                user_id=user_id,
            )
            results.append({
                "table_name": table_name,
                "success": True,
                "doc_id": import_result.get("doc_id"),
            })
        except Exception as e:
            logger.error(f"[数据导入] 表 {table_name} 导入失败: {e}")
            results.append({
                "table_name": table_name,
                "success": False,
                "error": str(e),
            })

    # 更新已导入表列表
    existing_imported = record.get("imported_tables") or []
    if isinstance(existing_imported, str):
        import json
        existing_imported = json.loads(existing_imported)

    for r in results:
        if r["success"] and not any(
            t.get("table_name") == r["table_name"] for t in existing_imported
        ):
            existing_imported.append({
                "table_name": r["table_name"],
                "doc_id": r.get("doc_id"),
                "imported_at": str(datetime.now()),
            })

    import json
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE data_connectors
            SET imported_tables = %s, last_sync_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(existing_imported, ensure_ascii=False), connector_id))
        conn.commit()

    connector.dispose()

    return {
        "success": True,
        "results": results,
        "total": len(results),
        "succeeded": sum(1 for r in results if r["success"]),
        "failed": sum(1 for r in results if not r["success"]),
    }
```

### 4.6 前端管理界面

**前端 API 层**：

```typescript
// frontend/src/api/dataConnector.ts

import { getAuthHeader } from './auth'

const API_BASE = import.meta.env.VITE_API_BASE || ''

export interface DataConnector {
  id: string
  name: string
  db_type: string
  host: string | null
  port: number | null
  database_name: string
  username: string
  is_active: boolean
  imported_tables: ImportedTable[]
  last_sync_at: string | null
  created_at: string
}

export interface ImportedTable {
  table_name: string
  doc_id: string
  imported_at: string
}

export interface RemoteTable {
  table_name: string
  schema: string
  comment: string | null
  imported: boolean
}

export interface ConnectorFormData {
  name: string
  db_type: string
  host?: string
  port?: number
  database_name: string
  username: string
  password: string
  options?: Record<string, string>
}

export const dataConnectorAPI = {
  list: async (): Promise<{ success: boolean; data: DataConnector[] }> => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors`, {
      headers: { ...getAuthHeader() },
    })
    return res.json()
  },

  create: async (data: ConnectorFormData) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },

  update: async (id: string, data: Partial<ConnectorFormData & { is_active: boolean }>) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },

  delete: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/${id}`, {
      method: 'DELETE',
      headers: { ...getAuthHeader() },
    })
    return res.json()
  },

  test: async (data: ConnectorFormData) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/test`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },

  testExisting: async (id: string) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/${id}/test`, {
      method: 'POST',
      headers: { ...getAuthHeader() },
    })
    return res.json()
  },

  listRemoteTables: async (id: string): Promise<{ success: boolean; data: RemoteTable[] }> => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/${id}/tables`, {
      headers: { ...getAuthHeader() },
    })
    return res.json()
  },

  importTables: async (id: string, tableNames: string[]) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/connectors/${id}/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ table_names: tableNames }),
    })
    return res.json()
  },

  // ===== Schema 管理 =====
  // 注意：LLM 推理的 Schema 在前端内存中，用户确认后调 saveSchema 写入知识库
  saveSchema: async (data: { table_name: string; table_description: string; columns: any[]; relations?: any[]; source: any; row_count: number; column_count: number }) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/schemas`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },
  listSchemas: async () => {
    const res = await fetch(`${API_BASE}/api/data-analysis/schemas`, { headers: { ...getAuthHeader() } })
    return res.json()
  },
  updateSchema: async (docId: string, data: { table_name: string; table_description: string; columns: any[] }) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/schemas/${docId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },

  // 关联关系
  listRelations: async () => {
    const res = await fetch(`${API_BASE}/api/data-analysis/relations`, { headers: { ...getAuthHeader() } })
    return res.json()
  },
  inferRelations: async () => {
    const res = await fetch(`${API_BASE}/api/data-analysis/relations/infer`, {
      method: 'POST',
      headers: { ...getAuthHeader() },
    })
    return res.json()
  },
  batchSaveRelations: async (relations: any[]) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/relations/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ relations }),
    })
    return res.json()
  },
  createRelation: async (data: any) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/relations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },
  deleteRelation: async (data: any) => {
    const res = await fetch(`${API_BASE}/api/data-analysis/relations`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify(data),
    })
    return res.json()
  },
}
```

**前端页面组件** `DataConnectorManager.vue`：

页面布局遵循 `PortalLayout` 子页面模式（AppHeader + 内容区），包含：
1. **连接器列表**（BaseTable）— 名称、类型、主机、状态、已导入表数、操作
2. **新建/编辑连接器弹窗**（BaseModal）— db_type 下拉选择后动态显示 host/port 等字段，带"测试连接"按钮
3. **导入表弹窗**（BaseModal）— 展示远程表列表（BaseTable + checkbox），选择后 LLM 推理 Schema → 进入审核流程（§5）

```
┌─────────────────────────────────────────────────┐
│  AppHeader: 数据连接器管理                        │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌─ 连接器列表 ──────────────────────────────┐  │
│  │ [搜索框]              [+ 新建连接器]       │  │
│  │                                            │  │
│  │ 序号 | 名称 | 类型 | 主机 | 状态 | 表数 | 操作 │
│  │  1  | ERP库 | mysql | 10.0.0.1 | ● | 5 | 编辑 删除 导入 测试 │
│  │  2  | CRM库 | postgres | 10.0.0.2 | ● | 3 | 编辑 删除 导入 测试 │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ┌─ 新建连接器弹窗 ────────────────────────┐    │
│  │  连接名称: [________]                     │    │
│  │  数据库类型: [MySQL ▾]                    │    │
│  │  主机地址: [________]  端口: [3306]       │    │
│  │  数据库名: [________]                     │    │
│  │  用户名:   [________]                     │    │
│  │  密码:     [________]                     │    │
│  │                                            │    │
│  │  [测试连接]  ✓ 连接成功 MySQL 8.0.32      │    │
│  │                                            │    │
│  │          [取消]  [保存]                    │    │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  ┌─ 导入表弹窗 ────────────────────────────┐    │
│  │  ERP库 - 选择要导入的表                    │    │
│  │                                            │    │
│  │  ☑ | 表名 | Schema | 注释 | 状态          │    │
│  │  ☑ | orders | public | 订单表 | 未导入     │    │
│  │  ☐ | users | public | 用户表 | 已导入     │    │
│  │  ☑ | products | public | 产品表 | 未导入  │    │
│  │                                            │    │
│  │  已选 2 个表         [取消]  [导入]        │    │
│  └────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

> **注意**：前端页面遵循 [page_patterns.md](../../.claude/rules/page_patterns.md) 规范，使用 BaseTable / BaseModal / BaseButton / BaseInput / BaseSelect 组件，颜色使用语义 token。

---

## 五、Schema 审核与管理

### 5.1 设计思路

**LLM Schema 推理结果不能直接入知识库，必须经过用户审核确认后才写入。** 理由：

- LLM 对业务专有名词推断可能出错（如"SKU"是商品编码还是库存单位）
- 样本数据可能未覆盖全部枚举值，导致枚举列表不完整
- 表间关联关系的推断不一定准确
- 用户最理解自己的数据，应该有最终确认权

**核心原则：知识库中只有用户已确认的数据。**

**审核流程是纯前端的**：LLM 推理 Schema → 结果直接返回给前端展示（内存中）→ 用户在前端修改确认 → 调保存接口 → 写入知识库。用户不保存（刷新、关闭页面），数据自然消失，无需临时表。

### 5.2 Schema 生命周期

```
[新数据源接入]
  Excel 上传 / 数据库表导入
      ↓
  LLM Schema 推理 → 结果直接返回给前端（内存中）
      ↓
  前端展示 Schema（可编辑表单）
      ↓
  用户确认/修正 → 调用保存接口 → 写入知识库（documents + chunks + chunks_vec）

[用户不保存]
  刷新/关闭页面 → Schema 数据消失 → 不影响知识库

[已有 Schema 管理]
  用户浏览已导入的表（从 documents 表读） → 点击"编辑 Schema"
      ↓
  前端加载当前 Schema（可编辑表单）
      ↓
  用户修改 → 更新知识库（重新向量化 + 更新 metadata）
```

### 5.3 不需要临时表

待审核的 Schema 只存在于前端内存中，**不需要 `data_table_schemas` 等临时表**。理由：

- 审核是即时的：LLM 推理完 → 用户当场确认 → 保存。不需要持久化中间状态
- 前端丢失无影响：用户重新触发导入流程即可，LLM 推理成本可接受
- 减少复杂度：无需管理 pending/confirmed/rejected 状态流转

### 5.4 Schema 保存 API

```python
# src/api/data_analysis.py 新增路由

class SchemaSaveRequest(BaseModel):
    table_name: str = Field(..., description="表名（LLM 推理，用户可修改）")
    table_description: str = Field(..., description="表描述（LLM 推理，用户可修改）")
    columns: list[dict] = Field(..., description="列信息列表（LLM 推理，用户可修改）")
    relations: list[dict] = Field(default=[], description="关联关系（用户确认）")
    source: dict = Field(..., description="数据源定位信息（file_path/sheet_name 或 connector_id/db_table_name）")
    row_count: int = Field(..., description="行数")
    column_count: int = Field(..., description="列数")


@router.post("/schemas")
async def save_schema(request: Request, body: SchemaSaveRequest):
    """保存 Schema → 写入知识库

    前端将用户确认后的 Schema 提交到此接口，一次性写入
    documents + chunks + chunks_vec。
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)

    # 构建 metadata
    metadata = {
        "source": body.source,
        "table_name": body.table_name,
        "table_description": body.table_description,
        "row_count": body.row_count,
        "column_count": body.column_count,
        "columns": body.columns,
        "relations": body.relations,
    }

    # 构建向量化描述文本
    description_parts = [
        f"表名：{body.table_name}",
        f"描述：{body.table_description}",
        f"行数：{body.row_count}，列数：{body.column_count}",
        "列信息：",
    ]
    for col in body.columns:
        line = f"  - {col['name']}（{col.get('semantic_name', '')}）: {col.get('description', '')}"
        if col.get("enum_values"):
            line += f"，可选值：{', '.join(col['enum_values'])}"
        if col.get("references"):
            line += f"，关联：{col['references']}"
        description_parts.append(line)
    description = "\n".join(description_parts)

    from src.db.database import get_db_connection
    from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
    from src.config.settings import get_embedding_api_key
    import json

    embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
    embedding = await embedding_client.embed(description)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO documents (
                user_id, tenant_id, title, source_type,
                total_chunks, embedding_model, metadata, summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            user_id, tenant_id, body.table_name, "data-analysis-metadata",
            1, "text-embedding-v3",
            json.dumps(metadata, ensure_ascii=False),
            description,
        ))
        doc_id = cursor.fetchone()["id"]

        cursor.execute("""
            INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (doc_id, 0, description, len(description), json.dumps({"char_count": len(description)})))
        chunk_id = cursor.fetchone()["id"]

        from src.knowledge.vector_db.vector_db import get_vector_db
        vector_db = get_vector_db(dimension=1024, conn=conn)
        await vector_db.insert([chunk_id], [embedding])

        conn.commit()

    return {"success": True, "doc_id": str(doc_id), "message": "Schema 已保存到知识库"}


@router.get("/schemas")
async def list_schemas(request: Request):
    """列出所有已保存的 Schema"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, summary, metadata, created_at
            FROM documents
            WHERE tenant_id = %s
              AND source_type = 'data-analysis-metadata'
            ORDER BY title
        """, (tenant_id,))
        rows = cursor.fetchall()

    results = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        results.append({
            "doc_id": str(row["id"]),
            "table_name": row["title"],
            "table_description": row["summary"],
            "columns": meta.get("columns", []),
            "relations": meta.get("relations", []),
            "row_count": meta.get("row_count"),
            "column_count": meta.get("column_count"),
            "source": meta.get("source", {}),
            "created_at": str(row["created_at"]) if row.get("created_at") else None,
        })
    return {"success": True, "data": results}


class SchemaUpdateRequest(BaseModel):
    table_name: str = Field(..., description="表名")
    table_description: str = Field(..., description="表描述")
    columns: list[dict] = Field(..., description="列信息列表")


@router.put("/schemas/{doc_id}")
async def update_schema(request: Request, doc_id: str, body: SchemaUpdateRequest):
    """更新已确认的 Schema（修正列语义、枚举值等）

    更新后自动重新生成向量，保持知识库检索准确性。
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT metadata FROM documents WHERE id = %s AND tenant_id = %s",
            (doc_id, tenant_id),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Schema 不存在")

        meta = row["metadata"]
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)

        meta["table_name"] = body.table_name
        meta["columns"] = body.columns

        # 重建描述文本
        description_parts = [
            f"表名：{body.table_name}",
            f"描述：{body.table_description}",
            f"行数：{meta.get('row_count', '?')}，列数：{len(body.columns)}",
            "列信息：",
        ]
        for col in body.columns:
            line = f"  - {col['name']}（{col.get('semantic_name', '')}）: {col.get('description', '')}"
            if col.get("enum_values"):
                line += f"，可选值：{', '.join(col['enum_values'])}"
            if col.get("references"):
                line += f"，关联：{col['references']}"
            description_parts.append(line)
        new_description = "\n".join(description_parts)

        import json
        cursor.execute("""
            UPDATE documents
            SET title = %s, summary = %s, metadata = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            body.table_name, new_description,
            json.dumps(meta, ensure_ascii=False), doc_id,
        ))

        # 重新向量化
        from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
        from src.config.settings import get_embedding_api_key
        embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        embedding = await embedding_client.embed(new_description)

        cursor.execute("SELECT id FROM chunks WHERE doc_id = %s LIMIT 1", (doc_id,))
        chunk_row = cursor.fetchone()
        if chunk_row:
            chunk_id = chunk_row["id"]
            cursor.execute("UPDATE chunks SET text = %s WHERE id = %s", (new_description, chunk_id))
            from src.knowledge.vector_db.vector_db import get_vector_db
            vector_db = get_vector_db(dimension=1024, conn=conn)
            await vector_db.update([chunk_id], [embedding])

        conn.commit()

    return {"success": True, "message": "Schema 更新成功"}
```

### 5.5 关联关系管理 API

```python
# src/api/data_analysis.py 新增路由

@router.post("/relations/infer")
async def infer_relations(request: Request):
    """LLM 自动推断已注册表之间的关联关系

    返回推断结果供用户确认，不会直接保存。
    用户确认后调用 POST /relations/batch 保存。
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT metadata FROM documents
            WHERE tenant_id = %s
              AND source_type = 'data-analysis-metadata'
        """, (tenant_id,))
        rows = cursor.fetchall()

    if len(rows) < 2:
        return {"success": True, "data": [], "message": "已注册表不足 2 个，无法推断关联"}

    tables_meta = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        tables_meta.append(meta)

    from src.tools.data_analysis.schema_extractor import SchemaExtractor
    from src.core import master_agent

    extractor = SchemaExtractor(master_agent.llm_gateway)
    relations = await extractor.infer_relations(tables_meta)

    return {"success": True, "data": relations}


class BatchRelationRequest(BaseModel):
    relations: list[RelationRequest] = Field(..., description="要保存的关联关系列表")


@router.post("/relations/batch")
async def batch_create_relations(request: Request, body: BatchRelationRequest):
    """批量保存关联关系（用于确认 LLM 推断结果后一次性保存）"""
    results = []
    for rel in body.relations:
        # 复用单个创建逻辑
        try:
            result = await _save_single_relation(request, rel)
            results.append({"relation": f"{rel.from_table}.{rel.from_column}→{rel.to_table}.{rel.to_column}", "success": True})
        except Exception as e:
            results.append({"relation": f"{rel.from_table}.{rel.from_column}→{rel.to_table}.{rel.to_column}", "success": False, "error": str(e)})

    return {"success": True, "results": results}


@router.get("/relations")
async def list_relations(request: Request):
    """列出当前租户所有已确认表之间的关联关系"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, metadata
            FROM documents
            WHERE tenant_id = %s
              AND source_type = 'data-analysis-metadata'
        """, (tenant_id,))
        rows = cursor.fetchall()

    relations = []
    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        table_relations = meta.get("relations", [])
        for rel in table_relations:
            relations.append({
                "from_table": meta["table_name"],
                "from_column": rel["from_column"],
                "to_table": rel["to_table"],
                "to_column": rel["to_column"],
                "type": rel["type"],
                "description": rel.get("description", ""),
                "name": rel.get("name", ""),
            })

    return {"success": True, "data": relations}


class RelationRequest(BaseModel):
    from_table: str = Field(..., description="来源表名（doc title）")
    from_column: str = Field(..., description="来源字段")
    to_table: str = Field(..., description="目标表名（doc title）")
    to_column: str = Field(..., description="目标字段")
    type: str = Field(..., description="关联类型: one_to_one | one_to_many | many_to_one | many_to_many")
    description: Optional[str] = Field("", description="关联说明")


@router.post("/relations")
async def create_relation(request: Request, body: RelationRequest):
    """添加表间关联关系（写入来源表的 metadata.relations）"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 查找来源表
        cursor.execute("""
            SELECT id, metadata FROM documents
            WHERE tenant_id = %s AND title = %s AND source_type = 'data-analysis-metadata'
            LIMIT 1
        """, (tenant_id, body.from_table))
        from_row = cursor.fetchone()
        if not from_row:
            raise HTTPException(status_code=404, detail=f"来源表不存在: {body.from_table}")

        # 校验目标表存在
        cursor.execute("""
            SELECT id FROM documents
            WHERE tenant_id = %s AND title = %s AND source_type = 'data-analysis-metadata'
            LIMIT 1
        """, (tenant_id, body.to_table))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail=f"目标表不存在: {body.to_table}")

        import json
        meta = from_row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        if "relations" not in meta:
            meta["relations"] = []

        # 检查是否已存在
        for r in meta["relations"]:
            if (r["from_column"] == body.from_column
                    and r["to_table"] == body.to_table
                    and r["to_column"] == body.to_column):
                return {"success": False, "error": "关联关系已存在"}

        relation = {
            "name": f"{body.from_table}-{body.to_table}关联",
            "from_column": body.from_column,
            "to_table": body.to_table,
            "to_column": body.to_column,
            "type": body.type,
            "description": body.description or "",
        }
        meta["relations"].append(relation)

        cursor.execute("""
            UPDATE documents SET metadata = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(meta, ensure_ascii=False), from_row["id"]))
        conn.commit()

    return {"success": True, "message": "关联关系添加成功"}


@router.delete("/relations")
async def delete_relation(request: Request, body: RelationRequest):
    """删除表间关联关系"""
    tenant_id = getattr(request.state, "tenant_id", None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, metadata FROM documents
            WHERE tenant_id = %s AND title = %s AND source_type = 'data-analysis-metadata'
            LIMIT 1
        """, (tenant_id, body.from_table))
        from_row = cursor.fetchone()
        if not from_row:
            raise HTTPException(status_code=404, detail=f"来源表不存在: {body.from_table}")

        import json
        meta = from_row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)

        relations = meta.get("relations", [])
        original_len = len(relations)
        meta["relations"] = [
            r for r in relations
            if not (r["from_column"] == body.from_column
                    and r["to_table"] == body.to_table
                    and r["to_column"] == body.to_column)
        ]

        if len(meta["relations"]) == original_len:
            return {"success": False, "error": "关联关系不存在"}

        cursor.execute("""
            UPDATE documents SET metadata = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (json.dumps(meta, ensure_ascii=False), from_row["id"]))
        conn.commit()

    return {"success": True, "message": "关联关系已删除"}
```

### 5.6 前端 Schema 审核界面

**审核流程交互**：

```
┌─ Schema 审核弹窗 ──────────────────────────────────────┐
│                                                         │
│  📊 销售明细表（来源：销售报表.xlsx / Sheet1）           │
│  共 200 行，6 列                                        │
│                                                         │
│  表名: [销售明细表          ] ← 可编辑                   │
│  描述: [记录各区域各产品线在每月的销售额和订单量...]     │
│                                                         │
│  [列信息]  [关联关系]              ← 标签页切换          │
│  ─────────────────────────────────────────────────────  │
│                                                         │
│  ┌─ 列信息标签页 ────────────────────────────────────┐  │
│  │ 列名   | 语义名   | 类型    | 描述    | 枚举值     │  │
│  │ 区域   | [销售区域] | text ▾ | [销售所属] | 华东,... │  │
│  │ 销售额 | [销售金额] | decimal | [含税销售额] |      │  │
│  │ 客户ID | [客户标识] | text    | [关联客户表] | ✓ID  │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  ┌─ 关联关系标签页 ──────────────────────────────────┐  │
│  │ 本表字段 | 关联类型 | 目标表 | 目标字段 | 说明      │  │
│  │ 客户ID ▾ | 多对一 ▾ | 客户表 ▾ | id ▾ | [每条...]  │  │
│  │                                              [+ 添加] │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  [拒绝（删除）]                   [确认入库]            │
└─────────────────────────────────────────────────────────┘
```

**Schema 管理列表**（已确认的表）：

```
┌─ 已注册数据表 ─────────────────────────────────────────┐
│ [搜索表名]                          [筛选: 全部/Excel/数据库] │
│                                                         │
│ 序号 | 表名 | 来源 | 行数 | 列数 | 关联 | 状态 | 操作   │
│  1  | 销售明细 | Excel | 200 | 6 | 1条 | ●已确认 | 编辑 删除 │
│  2  | 客户表   | MySQL | 1.2k | 15 | 0条 | ●已确认 | 编辑 删除 │
│  3  | 订单明细 | Excel | 500 | 8 | 2条 | ○待审核 | 审核 删除 │
│                                                         │
│                              [管理关联关系]  ← 独立页面  │
└─────────────────────────────────────────────────────────┘
```

**独立的关联关系管理弹窗**（从 Schema 管理列表的"管理关联关系"按钮打开）：

```
┌─ 关联关系管理 ─────────────────────────────────────────┐
│                                                         │
│  所有已注册表之间的关联关系：                            │
│  [自动推断关联]  [+ 手动添加]                           │
│                                                         │
│  ┌─ 自动推断结果（点击"自动推断"后展示）───────────┐    │
│  │ ☑ | 销售明细.客户ID → 客户表.id | 多对一 | ✓   │    │
│  │ ☑ | 订单明细.产品ID → 产品表.id | 多对一 | ✓   │    │
│  │ ☐ | 订单明细.区域   → 区域表.code  | 多对一 | ✗ │    │
│  │                                [确认选中的关联] │    │
│  └────────────────────────────────────────────────┘    │
│                                                         │
│  已确认的关联：                                         │
│  序号 | 来源表.字段 | 类型 | 目标表.字段 | 操作          │
│  1   | 销售明细.客户ID | 多对一 | 客户表.id | 编辑 删除  │
│  2   | 订单明细.产品ID | 多对一 | 产品表.id | 编辑 删除  │
│  3   | 订单明细.客户ID | 多对一 | 客户表.id | 编辑 删除  │
│                                                         │
│  ┌─ 添加/编辑关联 ──────────────────────────────────┐  │
│  │ 来源表: [销售明细 ▾]  字段: [客户ID ▾]            │  │
│  │ 关联类型: [多对一 ▾]  ← LLM 自动推断，用户可修改   │  │
│  │ 目标表: [客户表 ▾]  字段: [id ▾]                   │  │
│  │ 说明: [每条销售记录关联一个客户]                    │  │
│  │                                                    │  │
│  │ [取消]  [保存]                                     │  │
│  └────────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

> 关联关系中"来源表"、"目标表"从已确认的 Schema 列表中选择（下拉框），"字段"从选中表的列中选择。
> 编辑已确认的 Schema 时，打开同样的编辑弹窗，保存后自动重新向量化。

---

## 六、模块五（共享）：表 Metadata 知识库

### 6.1 设计思路

复用现有知识库的向量检索能力，但不存文本 chunks，而是存表级别的 Metadata：

| 知识库字段 | 用途 | 内容 |
|-----------|------|------|
| `documents.title` | 表名 | 如"销售明细表" |
| `documents.summary` | 表描述（做向量） | LLM 生成的表业务描述 |
| `documents.source_type` | 知识库类型 | `"data-analysis-metadata"`（统一标识，区别于景点 `"attraction_resource"` 等其他类型） |
| `documents.metadata` (JSON) | 列信息 + 关联信息 + 数据源 | 完整的 columns schema + 数据源定位 + 关联关系 |

**为什么复用知识库而不是新建独立表？**
- 向量检索、混合检索、租户隔离全部复用，无需重复实现
- 表描述天然适合向量化检索（用户说"销售数据" → 命中"销售明细表"）
- metadata JSON 字段已存在，足以存列信息
- **source_type 过滤统一**：与景点知识库（`source_type='attraction_resource'`）使用同一字段区分类型，检索链路只需加一个参数即可支持所有子智能体的知识库隔离

**source_type 命名规则**：
- `data-analysis-metadata`：数据分析子智能体的表 Schema
- `attraction_resource`：旅游智能体的景点知识库（已有）
- 其他子智能体的专属知识库按同样规则命名
- 未配置 `knowledge_base` 的子智能体检索租户全部知识库

### 6.2 Metadata 文档结构

```python
# 注册到知识库的文档结构

{
    # --- documents 表字段 ---
    "title": "销售明细表",                    # 表名（LLM 推理的中文业务名）
    "source_type": "data-analysis-metadata",  # 统一标识，区别于 attraction_resource 等其他知识库类型
    "summary": "该表记录了各区域、各产品线在每月的销售额和订单量...",  # 表描述（做向量）
    "metadata": {
        # --- 状态信息 ---
        "status": "confirmed",                    # 知识库中只有 confirmed 状态（见 §5 Schema 审核）

        # --- 数据源信息 ---
        "source": {
            "type": "excel",                  # excel | database
            "file_path": "storage/uploads/xxx/销售报表.xlsx",
            "sheet_name": "Sheet1",
            "table_range": "A1:F200",
            "connector_id": null,             # 数据库连接器 ID（database 类型时有值）
            "db_table_name": null,            # 数据库表名（database 类型时有值）
        },

        # --- 表级信息 ---
        "table_name": "销售明细表",
        "row_count": 200,
        "column_count": 6,

        # --- 列信息 ---
        "columns": [
            {
                "name": "区域",               # 原始列名
                "semantic_name": "销售区域",
                "data_type": "text",
                "description": "销售所属区域",
                "value_description": "华东/华南/华北/华西/华中",
                "enum_values": ["华东", "华南", "华北", "华西", "华中"],
                "is_id": False,
                "references": None
            },
            {
                "name": "销售额",
                "semantic_name": "销售金额",
                "data_type": "decimal",
                "description": "该笔交易的含税销售额",
                "value_description": "正数，单位：元",
                "enum_values": [],
                "is_id": False,
                "references": None
            },
            {
                "name": "客户ID",
                "semantic_name": "客户唯一标识",
                "data_type": "text",
                "description": "关联客户表的客户编号",
                "value_description": "格式：CUST-XXXX，关联客户表的 id 字段",
                "enum_values": [],
                "is_id": True,
                "references": "客户表.id"
            }
        ],

        # --- 表间关联关系 ---
        # columns[].references 是文本描述（LLM 推断），辅助理解
        # relations 是结构化定义（用户配置），可执行 JOIN
        "relations": [
            {
                "name": "销售-客户关联",
                "from_column": "客户ID",           # 本表的关联字段
                "to_table": "客户表",               # 目标表的名称（doc title）
                "to_column": "id",                  # 目标表的关联字段
                "type": "many_to_one",              # one_to_one | one_to_many | many_to_one | many_to_many
                "description": "每条销售记录关联一个客户"
            }
        ]
    }
}
```

### 6.3 入库流程

```python
# src/tools/data_analysis/metadata_manager.py

class TableMetadataManager:
    """管理表 Metadata 到知识库的存取"""

    def __init__(self, knowledge_service: KnowledgeBaseService, llm_gateway):
        self.kb = knowledge_service
        self.llm = llm_gateway
        self.schema_extractor = SchemaExtractor(llm_gateway)

    async def register_excel_table(
        self,
        df: pd.DataFrame,
        table_info: dict,
        file_path: str,
        tenant_id: str,
        user_id: str = None
    ) -> dict:
        """将 Excel 中识别到的表格注册到知识库"""

        # 1. LLM 推理 Schema
        schema = await self.schema_extractor.extract(df, table_info)

        # 2. 构建表描述文本（用于向量化）
        table_description = self._build_description(schema, df)

        # 3. 构建完整 metadata
        metadata = {
            "source": {
                "type": "excel",
                "file_path": file_path,
                "sheet_name": table_info["sheet_name"],
                "table_range": f"A{table_info['start_row']}:{table_info.get('end_col', '')}{table_info['end_row']}",
                "connector_id": None,
                "db_table_name": None,
            },
            "table_name": schema["table_name"],
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": schema["columns"],
        }

        # 4. 通过知识库服务入库
        result = await self._save_to_knowledge_base(
            title=schema["table_name"],
            description=table_description,
            metadata=metadata,
            source_type="data-analysis-metadata",
            tenant_id=tenant_id,
            user_id=user_id
        )

        return result

    async def register_database_table(
        self,
        connector_id: int,
        db_table_name: str,
        df: pd.DataFrame,
        ddl: str,
        tenant_id: str,
        user_id: str = None
    ) -> dict:
        """将外部数据库表注册到知识库"""

        table_info = {"name": db_table_name}
        schema = await self.schema_extractor.extract(df, table_info)

        # 附加 DDL 信息到列描述中（DDL 可能包含 LLM 样本数据看不到的约束信息）
        table_description = self._build_description(schema, df, ddl=ddl)

        metadata = {
            "source": {
                "type": "database",
                "file_path": None,
                "sheet_name": None,
                "table_range": None,
                "connector_id": connector_id,
                "db_table_name": db_table_name,
            },
            "table_name": schema["table_name"],
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": schema["columns"],
            "ddl": ddl,
        }

        return await self._save_to_knowledge_base(
            title=schema["table_name"],
            description=table_description,
            metadata=metadata,
            source_type="data-analysis-metadata",
            tenant_id=tenant_id,
            user_id=user_id
        )

    def _build_description(self, schema: dict, df: pd.DataFrame, ddl: str = None) -> str:
        """构建用于向量化的表描述文本"""
        parts = [
            f"表名：{schema['table_name']}",
            f"描述：{schema['table_description']}",
            f"行数：{len(df)}，列数：{len(df.columns)}",
            "列信息："
        ]
        for col in schema["columns"]:
            line = f"  - {col['name']}（{col['semantic_name']}）: {col['description']}"
            if col.get("enum_values"):
                line += f"，可选值：{', '.join(col['enum_values'])}"
            if col.get("references"):
                line += f"，关联：{col['references']}"
            parts.append(line)

        if ddl:
            parts.append(f"\nDDL:\n{ddl}")

        return "\n".join(parts)

    async def _save_to_knowledge_base(
        self, title, description, metadata, source_type, tenant_id, user_id
    ) -> dict:
        """直接写入 documents + chunks + chunks_vec 表

        此方法仅在用户审核确认 Schema 后调用，写入知识库的数据即为最终状态。
        未确认的 Schema 不会写入知识库，不存在 draft 状态的文档。
        """
        from src.db.database import get_db_connection
        from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
        from src.config.settings import get_embedding_api_key

        embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        embedding = await embedding_client.embed(description)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 插入文档（source_type 统一为 data-analysis-metadata，不再使用 file_type 区分）
            import json
            cursor.execute("""
                INSERT INTO documents (
                    user_id, tenant_id, title, source_type,
                    total_chunks, embedding_model, metadata, summary
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id, tenant_id, title, source_type,
                1, "text-embedding-v3",
                json.dumps(metadata, ensure_ascii=False),
                description
            ))
            doc_id = cursor.fetchone()["id"]

            # 插入 chunk（用表描述作为 chunk 文本，用于 FTS）
            cursor.execute("""
                INSERT INTO chunks (doc_id, chunk_index, text, tokens, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (doc_id, 0, description, len(description), json.dumps({"char_count": len(description)})))
            chunk_id = cursor.fetchone()["id"]

            # 插入向量
            from src.knowledge.vector_db.vector_db import get_vector_db
            vector_db = get_vector_db(dimension=1024, conn=conn)
            await vector_db.insert([chunk_id], [embedding])

            conn.commit()

        return {"success": True, "doc_id": doc_id, "table_name": title}
```

---

## 七、模块六：数据分析工具集

> **详细设计见独立文档**：[智能数据分析工具设计](./smart-data-analysis-tool-design.md)

### 7.1 设计概要

采用**统一智能分析工具**架构，一个 `SmartDataAnalysisTool` 内置 LLM 编排，类似 Word 工具的设计模式：

```
用户需求 + 表 Metadata → SmartDataAnalysisTool
  → AnalysisPlanner（LLM 编排）→ 步骤序列 JSON
  → PlanExecutor（DAG 执行）→ 并行/顺序执行步骤
  → DataAnalyzer（pandas/numpy）→ query/aggregate/merge/pivot/calculate/compare/trend
  → to_table（结构化数据给 LLM）+ to_chart（matplotlib PNG 图片）
```

### 7.2 对外工具接口

数据分析子智能体对外只暴露 3 个工具：

| 工具 | name | 功能 |
|------|------|------|
| `KnowledgeBaseTool`（通用，已有） | `knowledge_base_search` | 检索匹配相关表的 Metadata |
| `ListDataTablesTool` | `list_data_tables` | 兜底：列出所有已注册数据表 |
| `SmartDataAnalysisTool` | `analyze_data` | 统一智能分析：接收需求 + Metadata → 内部 LLM 编排 → 执行分析 → 返回结果 |

### 7.3 DataAnalyzer 方法清单

| 方法 | 层级 | 职责 |
|------|------|------|
| `query` | 数据获取 | 过滤、列选择、排序、行限制 |
| `aggregate` | 数据处理 | 分组聚合（sum/mean/count/median/std 等） |
| `merge` | 数据处理 | 多表关联（left/inner/outer） |
| `pivot` | 数据处理 | 透视表（长→宽） |
| `calculate` | 数据处理 | 列间运算、条件表达式（if）、派生新列 |
| `compare` | 分析 | 维度对比 + 占比 + 基准对比 |
| `trend` | 分析 | 时间趋势 + 环比 + 分组趋势 |
| `to_table` | 输出 | 结构化数据摘要给 LLM |
| `to_chart` | 输出 | matplotlib PNG 图表文件 |
| `load_related`（可选） | 便捷 | 根据 relations 自动加载关联表并合并 |

> 完整的类定义、LLM 编排 Prompt、执行引擎代码、安全设计、10 个场景验证均见 [智能数据分析工具设计](./smart-data-analysis-tool-design.md)。

---

## 八、子智能体定义

### 8.1 SUBAGENT.md

位置：`subagents/data-analysis/SUBAGENT.md`

```yaml
---
name: 数据分析师
description: 分析 Excel/CSV 数据文件，连接外部数据库，支持统计分析和数据可视化
version: 1.0.0
author: system
capabilities:
  - Excel/CSV 文件智能解析与表格识别
  - 第三方数据库连接与表结构导入
  - 智能数据分析（查询、聚合、对比、趋势、透视、可视化）
triggers:
  keywords:
    - 数据分析
    - 统计
    - 查询数据
    - 报表
    - 图表
    - 趋势
    - 对比
    - 分析一下
    - 帮我查
    - 多少
    - 排名
    - 增长
    - 占比
    - 汇总
    - 合计
  file_patterns:
    - "*.xlsx"
    - "*.xls"
    - "*.csv"
knowledge_base:
  source_types:
    - data-analysis-metadata
tools:
  inherit: false
  allowed:
    - knowledge_base_search
    - list_data_tables
    - analyze_data
skills:
  allowed: []
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---
```

**`knowledge_base` 配置说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `knowledge_base.source_types` | `list[str]` | 该子智能体可检索的知识库 source_type 列表。检索工具只搜这些 source_type 的文档，不搜租户全部知识库。不配置则搜全部 |

**与现有系统的关系**：
- 旅游智能体的景点知识库使用 `source_type='attraction_resource'`，同样通过 `source_type` 字段区分
- 未配置 `knowledge_base` 的子智能体（如售后服务、订单处理等）保持现有行为，检索租户全部知识库
- `SubagentConfig` 模型需扩展 `knowledge_base` 字段（见开发计划）

Body（system_prompt）将包含数据分析规范、工具使用指引、安全规则等。

**设计原则**：Excel 文件只是数据源，不使用旧的 `ExcelProcessTool` 处理。所有数据（Excel/CSV/数据库表）统一加载为 DataFrame 后，通过 `SmartDataAnalysisTool` 处理。数据分析子智能体不继承任何父级工具（`inherit: false`），完全自包含。

**工作流编排原则**（system prompt 中引导外层 LLM 遵循）：
1. 分析用户问题，拆解出多个检索关键词
2. 用不同关键词多次调用 `knowledge_base_search`（多轮检索），每次传入 `source_type='data-analysis-metadata'`（返回结果自带 metadata，包含完整列信息和关联关系）
3. 合并去重检索结果
4. 根据用户意图重排序，选出最佳匹配的表
5. 将用户需求 + 匹配到的表 Metadata 一次性传给 `analyze_data`，由工具内部 LLM 编排具体分析步骤
6. **兜底机制**：如果多轮向量检索均未命中（2-3 次仍无结果），调用 `list_data_tables` 获取全量表名+描述列表，从中判断是否有匹配的表。选中表后，用该表的**描述文本**作为 query 再调一次 `knowledge_base_search(query="表的描述文本", source_type="data-analysis-metadata")`，FTS 全文检索会精确匹配回完整列 Schema（metadata 中自带）。如果全量列表中也没有匹配的表，告知用户缺少什么数据，无法完成分析

### 8.2 工具注册

在 `Agent._register_builtin_tools()` 中注册数据分析工具：

```python
# 数据分析工具（仅在 data-analysis 子智能体中可用）
from src.tools.knowledge.knowledge_base_tool import KnowledgeBaseTool
from src.tools.data_analysis.list_data_tables_tool import ListDataTablesTool
from src.tools.data_analysis.smart_analysis_tool import SmartDataAnalysisTool

self.tool_registry.register(KnowledgeBaseTool())       # source_type 由 LLM 调用时传入
self.tool_registry.register(ListDataTablesTool())       # 兜底：列出所有表名+描述
self.tool_registry.register(SmartDataAnalysisTool())    # 统一智能分析工具
```

---

## 九、完整工作流

### 9.1 Excel 上传分析流程

```
Turn 1: 用户上传 "销售报表.xlsx"
  → [ExcelProcessTool.read] 读取文件
  → [SheetParser] 遍历 Sheet，校验格式（第一行表头 + 数据行）
  → [SchemaExtractor] LLM 推理每个表的 Schema
  → [TableMetadataManager] 将表 Metadata 存入知识库（source_type='data-analysis-metadata'）
  → [DataFrameStore] 缓存 DataFrame 到会话
  → 回复："我已解析了销售报表，发现 3 个表格：
     1. 销售明细（200 行，含区域/产品/销售额/月份）
     2. 目标对比（12 行，各区域年度目标）
     3. 汇总统计（5 行，各产品线汇总）"

Turn 2: 用户："按区域统计销售额，画个柱状图"
  → [外层 LLM] 检索匹配表
     → 调用 knowledge_base_search(query="区域销售统计", source_type="data-analysis-metadata")  → 命中销售明细表
     → 调用 knowledge_base_search(query="销售额汇总", source_type="data-analysis-metadata")    → 命中销售明细表、汇总统计表
     → 合并去重 + 重排序：最佳匹配是销售明细表（有区域和销售额列）
  → [外层 LLM] 一次调用 analyze_data(
        requirement="按区域统计销售额，画个柱状图",
        tables_metadata=[{销售明细表的完整 metadata}]
    )
  → [SmartDataAnalysisTool 内部]
     → [AnalysisPlanner] LLM 编排生成步骤序列：
       Step 1: aggregate(source="销售明细表", group_by=["区域"], aggregations=[{column:"销售额", function:"sum", alias:"销售总额"}], sort_by="销售总额")
       Step 2: to_table(source="step1_result")
       Step 3: to_chart(source="step1_result", chart_type="bar", x_column="区域", y_columns=["销售总额"], title="各区域销售总额")
     → [PlanExecutor] 按步骤执行（Step 1 → Step 2 + Step 3 并行）
     → 返回：{tables: [{区域销售额汇总}], charts: [{柱状图PNG路径}]}
  → [外层 LLM] 解读表格数据，生成自然语言回答 + 引用图表路径调用 HTML 生成工具
```

### 9.2 数据库连接分析流程

```
管理后台: 用户注册 MySQL 连接器 → 选择表 → 导入
  → [DatabaseConnector] 连接数据库
  → [获取 DDL + 前 30 行]
  → [SchemaExtractor] LLM 推理 Schema
  → [TableMetadataManager] 存入知识库（source_type='data-analysis-metadata'）

对话中: 用户："ERP 里最近新增了哪些客户？"
  → [外层 LLM] 检索匹配表
     → 调用 knowledge_base_search(query="客户信息", source_type="data-analysis-metadata")  → 命中客户表
  → [外层 LLM] 调用 analyze_data(
        requirement="ERP里最近新增的客户，按创建时间倒序",
        tables_metadata=[{客户表的完整 metadata}]
    )
  → [SmartDataAnalysisTool 内部]
     → LLM 编排：query(过滤创建时间>=30天前, 排序desc) → to_table(展示结果)
     → 执行：通过 DataConnector 从远程数据库加载 DataFrame → 过滤 → 返回
  → [外层 LLM] 解读结果生成回答
```

### 9.3 多表关联分析流程

```
用户："对比各区域的销售额，并按客户行业细分"
  → [外层 LLM] 检索匹配表
     → 调用 knowledge_base_search(query="区域销售额", source_type="data-analysis-metadata")    → 命中销售明细表
     → 调用 knowledge_base_search(query="客户行业信息", source_type="data-analysis-metadata")   → 命中客户表
     → 合并去重：销售明细表、客户表
  → [外层 LLM] 调用 analyze_data(
        requirement="对比各区域的销售额，并按客户行业细分，需要关联客户表获取行业信息",
        tables_metadata=[{销售明细表}, {客户表}]
    )
  → [SmartDataAnalysisTool 内部]
     → LLM 编排（一次调用，内部多步）：
       Step 1: query(source="销售明细表", columns=[客户ID, 区域, 销售额])                    → sales_raw
       Step 2: query(source="客户表", columns=[客户ID, 行业])                                 → customer_industry
       Step 3: merge(left=sales_raw, right=customer_industry, on=客户ID)                      → enriched
       Step 4: aggregate(source=enriched, group_by=[区域, 行业], agg=销售额sum)                → result
       Step 5: to_table(source=result)
       Step 6: to_chart(source=result, chart_type=stacked_bar, x=区域, y=各行业)               → chart
     → [PlanExecutor] Step 1 和 Step 2 并行 → Step 3 → Step 4 → Step 5 和 Step 6 并行
     → 返回：{tables: [{区域×行业销售额}], charts: [{堆叠柱状图}]}
  → [外层 LLM] 解读结果生成回答 + 生成 HTML 报告
```

### 9.4 兜底检索流程（向量检索未命中时）

```
用户："分析一下员工满意度调查的结果"
  → [LLM 多轮检索编排]
     → 调用 knowledge_base_search(query="员工满意度调查", source_type="data-analysis-metadata")   → 无结果
     → 调用 knowledge_base_search(query="满意度评分", source_type="data-analysis-metadata")        → 无结果
     → 调用 knowledge_base_search(query="员工调查问卷", source_type="data-analysis-metadata")       → 无结果
  → [LLM 判断] 连续 3 次检索均未命中，触发兜底机制
  → [LLM 调用] list_data_tables()
     → 返回：
       1. 销售明细表 - 记录各区域各产品线在每月的销售额和订单量
       2. 目标对比表 - 各区域年度销售目标
       3. 客户表 - ERP 导入的客户基本信息
       4. 产品表 - 产品目录信息
  → [LLM 判断] 列表中没有满意度调查相关的表
  → [LLM 回复] "抱歉，当前可用的数据表中没有员工满意度调查相关的数据。
     目前可用的数据表有：销售明细表、目标对比表、客户表、产品表。
     如需分析满意度数据，请先上传相关文件或通过数据连接器导入对应的表。"

--- 兜底命中的场景 ---

用户："帮我看看上个月的订单数据"
  → [LLM 多轮检索编排]
     → 调用 knowledge_base_search(query="订单数据", source_type="data-analysis-metadata")   → 无结果（表名不叫"订单"，向量没匹配上）
     → 调用 knowledge_base_search(query="上个月订单", source_type="data-analysis-metadata")  → 无结果
  → [LLM 判断] 2 次检索未命中，触发兜底
  → [LLM 调用] list_data_tables()
     → 返回：
       1. 销售明细表 - 记录各区域各产品线在每月的销售额和订单量
       2. 客户表 - ERP 导入的客户基本信息
  → [LLM 判断] "销售明细表"的描述提到"订单量"，可能包含订单相关数据
  → [LLM 回查] 调用 knowledge_base_search(query="记录各区域各产品线在每月的销售额和订单量", source_type="data-analysis-metadata")
     → 用表的描述文本作为 query，FTS 全文检索 100% 精确匹配
     → 返回完整 Schema：table_id、columns（含"订单量"列）、relations
  → [LLM] 确认表中有订单量数据，继续分析流程
```

---

## 十、数据库变更

### 10.1 新增表

```sql
-- deploy/init-postgres.sql 新增

-- 数据连接器：记录第三方数据库连接
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

### 10.2 增量变更

```sql
-- deploy/db_update.sql 新增

-- 2026-06-03，新增数据分析智能体数据连接器表
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

> **注意**：不使用 `data_table_registry` 表（原设计），改为复用 `documents` 表 + `metadata` JSON 字段存储表 Schema。

---

## 十一、新增代码文件清单

| 文件路径 | 功能 | 估算行数 |
|---------|------|---------|
| `subagents/data-analysis/SUBAGENT.md` | 子智能体定义（含 knowledge_base 配置） | ~80 行 |
| `src/tools/data_analysis/__init__.py` | 模块入口 | ~10 行 |
| `src/tools/data_analysis/sheet_parser.py` | Sheet 格式校验与 DataFrame 提取 | ~80 行 |
| `src/tools/data_analysis/schema_extractor.py` | LLM Schema 推理 | ~100 行 |
| `src/tools/data_analysis/metadata_manager.py` | Metadata 知识库存取 | ~200 行 |
| `src/tools/data_analysis/data_store.py` | DataFrame 缓存（Redis + 文件系统降级） | ~150 行 |
| `src/tools/data_analysis/db_connector.py` | SQLAlchemy 统一数据库连接器 | ~180 行 |
| `src/tools/data_analysis/crypto.py` | 密码 AES-Fernet 加密/解密 | ~50 行 |
| `src/tools/data_analysis/list_data_tables_tool.py` | 列出所有数据表（仅名称+描述，兜底检索） | ~60 行 |
| `src/tools/data_analysis/smart_analysis_tool.py` | 统一智能分析工具（对外接口） | ~80 行 |
| `src/tools/data_analysis/data_analyzer.py` | 分析引擎（pandas/numpy，所有分析方法） | ~400 行 |
| `src/tools/data_analysis/analysis_planner.py` | LLM 编排器（需求 → 步骤序列） | ~120 行 |
| `src/tools/data_analysis/plan_executor.py` | 步骤执行引擎（DAG + 并行） | ~150 行 |
| `src/tools/data_analysis/excel_import.py` | Excel 文件导入 → Metadata 注册 | ~120 行 |
| `src/api/data_analysis.py` | 连接器管理 API（CRUD + 测试 + 导入） | ~280 行 |
| `frontend/src/api/dataConnector.ts` | 前端连接器 API 层 | ~80 行 |
| `frontend/src/components/DataConnectorManager.vue` | 前端管理页面 | ~350 行 |

**总计约 ~2490 行新增代码。**

> **注意**：以下改动不在上述新增文件范围内，需要修改现有代码：
> - ~~检索链路加 `source_type` 过滤参数~~（已完成）
> - ~~`KnowledgeBaseTool` 改造~~（无需改造，返回结果已包含 metadata 字段，columns 和 relations 在其中）
> - `SubagentConfig` 模型加 `knowledge_base` 字段
> - `SubagentLoader` 解析 `knowledge_base` 配置
> - `Agent` 初始化时根据配置约束知识库工具的检索范围

---

## 十二、与现有系统的集成

### 12.1 依赖安装

```txt
# requirements.txt 新增
sqlalchemy>=2.0.0       # 统一数据库连接层
pymysql>=1.1.0          # MySQL 驱动（SQLAlchemy mysql+pymysql dialect）
psycopg2-binary>=2.9.0  # PostgreSQL 驱动（SQLAlchemy postgresql+psycopg2 dialect）
cryptography>=41.0.0    # 密码加密（Fernet）
matplotlib>=3.7.0       # 图表生成（PNG 图片，由 SmartDataAnalysisTool 内部使用）
numpy>=1.24.0           # 科学计算（条件表达式、统计函数）

# 按需安装（连接 SQL Server 时）：
# pymssql>=2.2.0        # SQL Server 驱动

# 不需要 sqlglot — 不使用 Text-to-SQL 路径
# 不需要 ECharts — 图表由 matplotlib 生成 PNG，HTML 报告由外层 LLM 调用 HTML 生成工具完成
```

### 12.2 配置变更

```yaml
# configs/config.yaml 新增
data_analysis:
  file_analysis:
    max_file_size_mb: 50
    max_sample_rows: 50       # LLM Schema 推理的样本行数

  df_cache:
    ttl_seconds: 7200         # DataFrame 缓存 TTL（默认 2 小时）
    file_cache_dir: "storage/df_cache"  # 文件系统降级缓存目录

  db_connector:
    enabled: true
    query_timeout_seconds: 30
    max_result_rows: 1000
    password_encryption_key: "${DATA_ANALYSIS_ENCRYPTION_KEY}"
```

---

## 十三、安全设计

### 13.1 威胁模型与防护

| 威胁 | 防护层 | 措施 |
|------|--------|------|
| 跨租户数据泄露 | 知识库检索 | 复用知识库租户隔离（tenant_id 过滤） |
| 跨租户缓存泄露 | DataFrame 缓存 | Redis key 和文件目录均按 session_id 隔离，session_id 天然绑定租户 |
| 数据库密码泄露 | 数据连接器 | AES 加密存储，日志脱敏 |
| 恶意过滤条件注入 | 工具参数 | Pydantic 校验，白名单操作符 |
| 资源耗尽（慢查询） | 数据库连接器 | statement_timeout + LIMIT |
| 文件炸弹（超大文件） | 文件加载 | max_file_size_mb 限制 |
| 数据批量导出 | 结果限制 | max_result_rows = 1000 |
| 缓存磁盘占用 | DataFrame 缓存 | 会话结束时自动清理 + 目录配额 |

### 13.2 安全设计要点

**SmartDataAnalysisTool 的安全模型**：

- **方法白名单**：`PlanExecutor.ALLOWED_METHODS` 硬编码允许的方法名，LLM 无法调用不在白名单中的方法
- **操作符白名单**：`query` 方法的 `_apply_filters` 只允许预定义操作符，防止注入
- **calculate 表达式安全**：`_eval_expression` 使用 `__builtins__: {}` 限制命名空间，只暴露安全的数学函数，不允许 `import`、`exec`、`eval`、`open` 等
- **行数限制**：`to_table` 默认 max_rows=50，`query` 默认 limit=100，防止大量数据泄露到 LLM 上下文
- **临时变量隔离**：中间结果存在 `DataAnalyzer._variables` 中，会话结束自动清理

与原设计相比，新设计的 LLM 不直接生成代码/SQL，只通过结构化 JSON 指定方法+参数，从根本上消除了代码注入风险。

---

## 十四、实施计划

### 第一阶段：Excel 解析 + 智能分析工具（3-4 周）

| 周次 | 任务 | 交付物 |
|------|------|--------|
| W1 | 子智能体定义 + SheetParser + SchemaExtractor | `SUBAGENT.md` + `sheet_parser.py` + `schema_extractor.py` |
| W1 | MetadataManager + DataFrameStore | `metadata_manager.py` + `data_store.py` |
| W2 | DataAnalyzer 分析引擎（pandas/numpy 方法集） | `data_analyzer.py` |
| W2 | AnalysisPlanner（LLM 编排）+ PlanExecutor（DAG 执行） | `analysis_planner.py` + `plan_executor.py` |
| W3 | SmartDataAnalysisTool（对外接口）+ ListDataTablesTool | `smart_analysis_tool.py` + `list_data_tables_tool.py` |
| W3 | ExcelImportTool（上传 → 解析 → 入库） | `excel_import.py` |
| W4 | 集成测试 + 端到端测试 | 测试用例 |

### 第二阶段：数据连接器 + 管理后台（2-3 周）

| 周次 | 任务 | 交付物 |
|------|------|--------|
| W5 | DatabaseConnector（MySQL/PG）+ 加密存储 | `db_connector.py` |
| W6 | 管理后台 API + 前端页面 | `data_analysis.py` API + 前端 |
| W7 | 数据库表导入流程 + 测试 | 端到端测试 |

### 第三阶段：增强能力（按需启动）

| 任务 | 依赖 | 预估周期 |
|------|------|---------|
| SQL Server 连接器 | DatabaseConnector | 1 周 |
| 多文件关联分析（多表 JOIN） | DataFrameStore 增强 | 2 周 |
| 图表样式优化 | VisualizeDataTool | 1 周 |
| 自然语言建表 | Schema 管理 | 2 周 |

---

## 十五、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM Schema 推理不准确 | 表名/列语义错误 | 人工审核入口 + 可手动修正 metadata |
| Sheet 格式不符合预期 | 非标准表格格式被跳过 | 要求上传标准表格格式文件（第一行表头 + 数据行） |
| 数据库连接器兼容性 | 特定数据库方言不支持 | 首期仅 MySQL/PG，按需扩展 |
| 大文件内存占用 | 影响服务稳定性 | 文件大小限制 + 行数限制 |
| Metadata 知识库与文档知识库混搜 | 表文档和普通文档混淆 | 检索时用 source_type 过滤 |
| 外部数据库表结构变更 | Metadata 过时 | 管理后台提示 + 手动刷新 |
| Redis 不可用 | DataFrame 缓存降级 | 自动降级到文件系统缓存，透明切换 |
| 缓存磁盘空间耗尽 | 文件缓存写入失败 | 会话结束时清理 + 定期清理过期目录 |
| pickle 反序列化安全 | 恶意 pickle 数据 | 缓存仅由本系统写入，不接收外部 pickle 数据 |
| 关联关系推断错误 | 跨表 JOIN 结果不正确 | LLM 自动推断（含关联类型），用户确认/修正后保存 |
| Schema 审核遗漏 | 错误 Schema 导致分析偏差 | 未确认 Schema 存临时表，不入知识库；必须用户确认后才写入 |
| LLM 编排步骤序列不合理 | 分析结果错误或遗漏 | DataAnalyzer 方法白名单 + 参数校验 + 步骤描述供人工审查 |
| calculate 表达式注入 | 执行危险代码 | `__builtins__: {}` 限制 + 函数白名单 + 无 import/exec/eval |
| matplotlib 中文字体缺失 | 图表中文乱码 | 配置 fallback 字体链（Microsoft YaHei → SimHei → DejaVu Sans） |

---

## 十六、数据源接入总结

### 16.1 两条接入路径

```
路径 A：Excel/CSV 文件                路径 B：数据库连接器
─────────────────────               ──────────────────
用户上传文件                         用户创建连接器（前端管理界面）
  ↓                                   ↓
SheetParser 校验格式并提取 DataFrame     选择要导入的表
  ↓                                   ↓
pd.read_excel → DataFrame            SQLAlchemy 连接远程库
  ↓                                   ↓
SchemaExtractor（LLM 推理）          get_ddl + get_sample_data → DataFrame
  ↓                                   ↓
SchemaExtractor（LLM 推理）          SchemaExtractor（LLM 推理）
  ↓                                   ↓
─────── 汇合 ─────────────────────────────────────────
  ↓
Metadata 入库（用户确认后直接写入知识库）
  ↓
前端展示 Schema 审核弹窗（列信息 + 关联关系）
  ↓
用户确认/修正 → status=confirmed
  ↓
SchemaExtractor.infer_relations（LLM 推断跨表关联）
  ↓
用户确认关联关系 → 写入各表的 metadata.relations
  ↓
DataFrame 缓存（Redis → 文件系统 → 原始文件）
```

### 16.2 完整数据结构定义

每个注册的数据表最终存储为 `documents` 表中的一条记录，核心信息在 `metadata` JSON 字段中。

**documents 表字段映射**：

| documents 字段 | 值 | 说明 |
|---------------|-----|------|
| `id` | 自增 ID | Schema 的唯一标识（也是 table_id） |
| `user_id` | 上传者/创建者 | |
| `tenant_id` | 租户 ID | 租户隔离 |
| `title` | 表名（如"销售明细表"） | LLM 推理 / 用户修改 |
| `source_type` | `"data-analysis-metadata"` | 统一标识数据分析表 Schema，区别于景点（`attraction_resource`）等其他知识库类型 |
| `file_path` | Excel 文件路径（数据库来源为 null） | |
| `total_chunks` | `1` | 结构化数据只有 1 个 chunk |
| `metadata` | 以下完整 JSON | 所有 Schema 信息 |
| `summary` | 表描述文本（用于向量化） | 详见 §16.3 |

**metadata JSON 完整结构**：

```jsonc
{
  // ===== 状态 =====
  "status": "confirmed",              // 知识库中只有 confirmed（未确认的 Schema 在前端内存中，不入库）

  // ===== 数据源信息 =====
  "source": {
    "type": "excel",                  // "excel" | "database"

    // Excel 类型专有字段
    "file_path": "storage/uploads/xxx/销售报表.xlsx",
    "sheet_name": "Sheet1",
    "table_range": "A1:F200",

    // Database 类型专有字段
    "connector_id": null,             // data_connectors 表的 UUID
    "db_table_name": null,            // 远程数据库中的表名
    "db_schema": null                 // 数据库 schema（如 "public"）
  },

  // ===== 表级信息 =====
  "table_name": "销售明细表",          // 中文业务名（LLM 推理 / 用户修改）
  "table_description": "记录各区域各产品线在每月的销售额和订单量",
  "row_count": 200,
  "column_count": 6,

  // ===== 列信息 =====
  "columns": [
    {
      "name": "区域",                 // 原始列名（不可修改，数据加载时用这个）
      "semantic_name": "销售区域",     // 语义化中文名（LLM 推理 / 用户修改）
      "data_type": "text",            // text | integer | decimal | date | datetime | boolean
      "description": "销售所属区域",   // 列描述（LLM 推理 / 用户修改）
      "value_description": "华东/华南/华北/华西/华中",  // 值的含义说明
      "enum_values": ["华东", "华南", "华北", "华西", "华中"],  // 枚举值列表（LLM 采样 / 用户补充）
      "is_id": false,                 // 是否为 ID 字段
      "references": null              // 文本描述关联："表名.字段名"（LLM 推断，辅助理解）
    },
    {
      "name": "客户ID",
      "semantic_name": "客户唯一标识",
      "data_type": "text",
      "description": "关联客户表的客户编号",
      "value_description": "格式：CUST-XXXX",
      "enum_values": [],
      "is_id": true,
      "references": "客户表.id"       // LLM 推断的文本关联（不可执行，仅供理解）
    }
  ],

  // ===== 表间关联关系（结构化，可执行 JOIN）=====
  "relations": [
    {
      "name": "销售-客户关联",         // 关联名称（LLM 生成 / 用户修改）
      "from_column": "客户ID",         // 本表的关联字段（columns[].name）
      "to_table": "客户表",            // 目标表名（documents.title）
      "to_column": "id",              // 目标表的关联字段
      "type": "many_to_one",          // LLM 推断：one_to_one | one_to_many | many_to_one | many_to_many
      "description": "每条销售记录关联一个客户"
    }
  ],

  // ===== 数据库来源专有 =====
  "ddl": null                         // DDL 语句（仅 database 类型有值）
}
```

**数据结构层级关系**：

```
documents 表
  ├── id (table_id)
  ├── title ← table_name
  ├── source_type = "data-analysis-metadata"     # 统一标识，知识库类型过滤依据
  ├── summary ← 向量化文本（§16.3）
  ├── metadata (JSON) ←─────────────────────────────
  │     ├── status: confirmed（仅此状态）             │
  │     ├── source: { 定位信息 }                      │
  │     ├── table_name, table_description            │
  │     ├── row_count, column_count                  │
  │     ├── columns: [ 列信息数组 ]                   │
  │     │     ├── name (原始列名，数据加载用)          │
  │     │     ├── semantic_name (语义名，展示用)       │
  │     │     ├── data_type, description              │
  │     │     ├── enum_values, is_id                  │
  │     │     └── references (文本，辅助理解)          │
  │     ├── relations: [ 关联关系数组 ]               │
  │     │     ├── from_column, to_table, to_column   │
  │     │     ├── type (LLM 推断，用户可改)            │
  │     │     └── description                         │
  │     └── ddl (数据库来源专有)                       │
  │                                                   │
  ├── chunks 表 (1条)                                 │
  │     └── text = summary (FTS 全文检索用)            │
  └── chunks_vec 表 (1条)                             │
        └── embedding = 向量(summary) (向量检索用)     │
```

### 16.3 向量化文本（summary）生成规则

`documents.summary` 和 `chunks.text` 存储同一段文本，这段文本同时用于：
1. **向量检索** — 用户说"销售数据" → 向量匹配到"销售明细表"
2. **FTS 全文检索** — 关键词匹配表名、列名、枚举值

生成规则：

```python
def build_table_description(table_name, table_description, row_count, columns, ddl=None):
    parts = [
        f"表名：{table_name}",
        f"描述：{table_description}",
        f"行数：{row_count}，列数：{len(columns)}",
        "列信息：",
    ]
    for col in columns:
        line = f"  - {col['name']}（{col.get('semantic_name', '')}）: {col.get('description', '')}"
        if col.get("enum_values"):
            line += f"，可选值：{', '.join(col['enum_values'])}"
        if col.get("references"):
            line += f"，关联：{col['references']}"
        parts.append(line)
    if ddl:
        parts.append(f"\nDDL:\n{ddl}")
    return "\n".join(parts)
```

生成的文本示例：

```
表名：销售明细表
描述：记录各区域各产品线在每月的销售额和订单量，包含客户信息
行数：200，列数：6
列信息：
  - 区域（销售区域）: 销售所属区域，可选值：华东, 华南, 华北, 华西, 华中
  - 销售额（销售金额）: 该笔交易的含税销售额
  - 月份（销售月份）: 销售所属年月
  - 客户ID（客户唯一标识）: 关联客户表的客户编号，关联：客户表.id
  - 产品线（产品类别）: 产品所属类别
  - 订单量（订单数量）: 该笔交易的订单数
```

### 16.4 知识库检索流程（如何应用）

数据分析子智能体在对话中使用知识库的流程（LLM 多轮检索编排）：

```
用户提问："按区域统计本季度销售额"
  ↓
[1] LLM 多轮检索编排（通过 knowledge_base_search 工具）
    LLM 分析意图，拆解检索关键词：
    → 调用 knowledge_base_search(query="区域销售额统计", source_type="data-analysis-metadata")  → 命中 A, B, C 表
    → 调用 knowledge_base_search(query="季度销售数据", source_type="data-analysis-metadata")     → 命中 B, D 表
    → 过滤条件（自动生效）：source_type='data-analysis-metadata' AND metadata.status='confirmed' AND tenant_id=xxx
  ↓
[2] 合并去重 + LLM 重排序
    合并结果：A, B, C, D（按 doc_id 去重）
    LLM 根据用户意图重排序：最佳匹配是 B（销售明细表，有区域和销售额列）
  ↓
[3] 检查关联表
    选中表的 relations 中有：客户ID → 客户表.id
    LLM 判断：用户只问"按区域统计销售额"，不需要客户维度 → 不加载关联表
  ↓
[4] 读取 Schema → 工具执行
    LLM 根据选中表的列信息调用工具：
    → aggregate_data(table_id=B, group_by=["区域"], aggregations=[...])
  ↓
[5] 格式化回答
    LLM 将结果转为自然语言 + 可视化
```

**关键过滤条件**：
- `source_type = 'data-analysis-metadata'` — 由检索链路自动过滤（HybridRetriever 加 source_type 参数），只搜数据分析表 Schema，不搜景点等其他知识库
- 知识库中只有 confirmed 的数据（未确认的 Schema 在前端内存中，不保存到数据库）
- `tenant_id` — 租户隔离（原有机制）

### 16.5 数据加载路径（工具如何获取 DataFrame）

工具拿到 doc_id 后，根据 `source.type` 走不同路径：

```
source.type = "excel"
  → 从 source.file_path 读取 Excel
  → 从 source.sheet_name 定位 Sheet
  → pd.read_excel() → DataFrame
  → 缓存到 DataFrameStore

source.type = "database"
  → 从 source.connector_id 读取连接器配置
  → create_connector_from_record() 创建 SQLAlchemy Engine
  → 从 source.db_table_name 获取远程表名
  → pd.read_sql() → DataFrame
  → 缓存到 DataFrameStore
```

两条路径最终都产出 `pd.DataFrame`，后续工具逻辑完全一致。

---

## 十七、与原设计的关键差异

| 维度 | 原设计（2026-05-29） | 当前设计（2026-06-04） |
|------|---------------------|---------------------|
| 分析工具架构 | 多个独立小工具（query_table, aggregate_data 等） | 统一智能分析工具（SmartDataAnalysisTool），内置 LLM 编排 |
| 查询方式 | Text-to-SQL（LLM 生成 SQL） | LLM 生成步骤序列（JSON），pandas/numpy 执行 |
| LLM 调用模式 | 每个操作一次工具调用（外层 Agent 多轮） | 一次 analyze_data 调用，内部 LLM 编排 + 多步执行 |
| 执行引擎 | 独立工具各自实现 | DataAnalyzer 统一引擎（pandas + numpy） |
| 并行支持 | 无（顺序工具调用） | DAG 依赖分析 + asyncio.gather 并行执行 |
| 列间运算 | 不支持 | calculate 方法（四则运算 + if 条件表达式） |
| 图表输出 | HTML 报告（ECharts CDN） | matplotlib PNG 图片 → 外层 LLM 调用 HTML 工具生成报告 |
| Schema 来源 | 手动注册到 `data_table_registry` | Excel 智能解析 + 数据库连接器自动发现 |
| Metadata 存储 | 独立表 + 关键词检索 | 复用知识库向量系统（`source_type='data-analysis-metadata'`） |
| 安全模型 | sqlglot AST 验证 + 进程内代码执行器 | 方法白名单 + 操作符白名单 + 表达式命名空间限制 |
| 数据连接器 | 无 | SQLAlchemy 统一连接器 + 前端管理界面 |
| DataFrame 缓存 | 进程内 dict | Redis → 文件系统 → 原始文件三级缓存 |
