# 数据分析智能体设计文档

> 前序调研：[Text-to-SQL 与数据分析智能体技术调研](../../research/text-to-sql-data-analysis-agent-research.md)
> 关联设计：[知识库能力增强方案](../knowledge-base/knowledge-base-enhancement-design.md) §3.6
> 关联规范：[系统架构](.claude/rules/architecture.md)、[数据库开发规范](.claude/rules/database_dev.md)
> 创建日期：2026-05-29
> 最近更新：2026-06-02
> 状态：设计中

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
| Excel 智能解析 | 解析上传的 Excel/CSV，识别表格，提取 Schema | Excel/CSV 文件 | Table Metadata → 知识库 |
| 数据连接器 | 连接第三方数据库，发现表结构 | 数据库连接信息 | Table Metadata → 知识库 |
| 表 Metadata 知识库 | 存储和检索表结构信息 | 表描述（向量）+ 列信息（metadata） | 匹配到的表 Schema |
| 数据分析工具 | 基于预定义 pandas 工具分析数据 | 用户问题 → LLM 选工具+传参 | 分析结果/图表 |

### 2.2 核心数据流

```
用户问题："按区域统计销售额"
    ↓
[1. 表匹配] 用问题检索 Metadata 知识库 → 命中"销售报表"表
    ↓
[2. 意图理解] LLM 读取表 Schema，判断需要 groupby + sum
    ↓
[3. 工具调用] LLM 调用 aggregate_data(
        source="销售报表",
        group_by="区域",
        aggregations=[{"column": "销售额", "function": "sum"}]
    )
    ↓
[4. 执行] 工具从数据源加载 DataFrame → 执行 pandas groupby → 返回结果
    ↓
[5. 格式化] LLM 解读结果，返回自然语言回答
```

---

## 三、模块一：Excel 智能解析

### 3.1 核心流程

```
Excel/CSV 文件上传
  ↓
[1. Sheet 解析] 遍历每个 Sheet，识别表格区域
  ↓
[2. 数据提取] 将识别到的表格转为 DataFrame
  ↓
[3. LLM Schema 推理] 前 50 行数据 + 列名 → LLM 推理语义信息
  ↓
[4. Metadata 入库] 表描述做向量，列信息做 metadata，存入知识库
```

### 3.2 Sheet 内表格区域识别

一个 Sheet 中可能包含多个表格（如顶部是汇总表、下面是明细表），需要自动识别。

```python
# src/tools/data_analysis/table_detector.py

class TableDetector:
    """识别 Excel Sheet 中的表格区域"""

    def detect_tables(self, sheet, sheet_name: str) -> list[dict]:
        """
        识别 Sheet 中的所有表格区域。

        策略：
        1. 扫描所有行，根据连续非空行识别数据块
        2. 数据块之间有空行分隔 → 视为不同表格
        3. 每个表格的首行作为表头候选
        4. 过滤掉行数 < 2 的碎片块

        Returns:
            [{"name": "区域", "start_row": 0, "end_row": 20,
              "headers": ["区域", "销售额", ...],
              "data_rows": 20, "col_count": 5}, ...]
        """
        blocks = []
        current_block = []

        for row_idx, row in enumerate(sheet.iter_rows(values_only=True)):
            values = [v for v in row if v is not None and str(v).strip()]
            if values:
                current_block.append((row_idx, row))
            else:
                if len(current_block) >= 2:
                    blocks.append(self._finalize_block(current_block, sheet_name))
                current_block = []

        if len(current_block) >= 2:
            blocks.append(self._finalize_block(current_block, sheet_name))

        return blocks

    def _finalize_block(self, block: list, sheet_name: str) -> dict:
        """将数据块转为表格描述"""
        start_row = block[0][0]
        end_row = block[-1][0]

        # 首行作为表头
        header_row = block[0][1]
        headers = [str(v).strip() if v is not None else f"col_{i}" for i, v in enumerate(header_row)]

        return {
            "sheet_name": sheet_name,
            "name": f"{sheet_name}",
            "start_row": start_row,
            "end_row": end_row,
            "headers": headers,
            "data_rows": len(block) - 1,
            "col_count": len(headers),
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
  "table_name": "给这个表起一个简洁的中文业务名称",
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
            table_info: 表格位置信息（来自 TableDetector）

        Returns:
            {
                "table_name": "...",
                "table_description": "...",
                "columns": [{...}]
            }
        """
        # 取前 50 行数据作为样本
        sample = df.head(50)
        sample_data = sample.to_string(index=False, max_colwidth=30)

        response = await self.llm.chat(
            messages=[{"role": "user", "content": SCHEMA_INFERENCE_PROMPT.format(
                sheet_name=table_info["name"],
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
        return json.loads(content.strip())
```

### 3.4 数据加载为内部 DataFrame

识别到表格区域后，将其加载为 DataFrame 并缓存到数据存储中：

```python
# src/tools/data_analysis/data_store.py

class DataFrameStore:
    """管理已解析的 DataFrame 实例

    进程内缓存，按 session + file_path 索引。
    多 Worker 场景下可能需重新加载（可接受，< 1s）。
    """

    def __init__(self):
        self._store: dict[str, dict] = {}

    def put(self, session_id: str, table_id: str, df: pd.DataFrame, meta: dict):
        key = f"{session_id}:{table_id}"
        self._store[key] = {"df": df, "meta": meta, "loaded_at": datetime.now()}

    def get(self, session_id: str, table_id: str) -> pd.DataFrame | None:
        key = f"{session_id}:{table_id}"
        entry = self._store.get(key)
        return entry["df"] if entry else None

    def list_tables(self, session_id: str) -> list[str]:
        """列出当前会话可用的表 ID"""
        prefix = f"{session_id}:"
        return [k[len(prefix):] for k in self._store if k.startswith(prefix)]
```

---

## 四、模块二：数据连接器

### 4.1 核心功能

连接第三方数据库（MySQL、PostgreSQL、SQL Server 等），获取表列表和 DDL，用与 Excel 相同的 LLM Schema 推理流程提取 Metadata。

```
数据库连接信息（host/port/db/user/password）
  ↓
[1. 连接测试] 验证连接可用性
  ↓
[2. 表发现] 获取数据库中的表列表（用户选择要导入的表）
  ↓
[3. DDL 提取] 获取表的 CREATE TABLE 语句
  ↓
[4. 样本数据] 读取前 30 行数据
  ↓
[5. LLM Schema 推理] DDL + 样本数据 → LLM 推理语义（复用 SchemaExtractor）
  ↓
[6. Metadata 入库] 存入知识库（来源标记为 database）
```

### 4.2 连接器注册表

```sql
-- deploy/init-postgres.sql 新增

-- 数据连接器：记录第三方数据库连接
CREATE TABLE IF NOT EXISTS data_connectors (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,                        -- 连接名称（如"ERP生产库"）
    db_type TEXT NOT NULL,                     -- mysql | postgresql | sqlserver | sqlite
    host TEXT NOT NULL,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,          -- AES 加密存储
    options JSONB,                             -- 额外连接参数
    is_active BOOLEAN DEFAULT TRUE,
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

### 4.3 数据库适配器

```python
# src/tools/data_analysis/db_connector.py

from abc import ABC, abstractmethod

class DatabaseConnector(ABC):
    """数据库连接器基类"""

    def __init__(self, config: dict):
        self.config = config
        self._conn = None

    @abstractmethod
    def test_connection(self) -> bool: ...

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def get_ddl(self, table_name: str) -> str: ...

    @abstractmethod
    def get_sample_data(self, table_name: str, rows: int = 30) -> pd.DataFrame: ...

    def close(self):
        if self._conn:
            self._conn.close()


class MySQLConnector(DatabaseConnector):
    """MySQL 连接器"""

    def test_connection(self) -> bool:
        import pymysql
        try:
            self._conn = pymysql.connect(
                host=self.config["host"],
                port=self.config.get("port", 3306),
                user=self.config["username"],
                password=self._decrypt(self.config["password_encrypted"]),
                database=self.config["database_name"],
                connect_timeout=10
            )
            return True
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        cursor = self._conn.cursor()
        cursor.execute("SHOW TABLES")
        return [row[0] for row in cursor.fetchall()]

    def get_ddl(self, table_name: str) -> str:
        cursor = self._conn.cursor()
        cursor.execute(f"SHOW CREATE TABLE `{table_name}`")
        return cursor.fetchone()[1]

    def get_sample_data(self, table_name: str, rows: int = 30) -> pd.DataFrame:
        return pd.read_sql(f"SELECT * FROM `{table_name}` LIMIT {rows}", self._conn)


class PostgreSQLConnector(DatabaseConnector):
    """PostgreSQL 连接器（用于连接外部 PostgreSQL，区别于系统内置库）"""

    def test_connection(self) -> bool:
        import psycopg2
        try:
            self._conn = psycopg2.connect(
                host=self.config["host"],
                port=self.config.get("port", 5432),
                user=self.config["username"],
                password=self._decrypt(self.config["password_encrypted"]),
                dbname=self.config["database_name"],
                connect_timeout=10
            )
            return True
        except Exception:
            return False

    def list_tables(self) -> list[str]:
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """)
        return [row[0] for row in cursor.fetchall()]

    def get_ddl(self, table_name: str) -> str:
        cursor = self._conn.cursor()
        cursor.execute(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = %s ORDER BY ordinal_position
        """, (table_name,))
        cols = cursor.fetchall()
        ddl = f"CREATE TABLE {table_name} (\n"
        ddl += ",\n".join(f"  {c[0]} {c[1]} {'NOT NULL' if c[2] == 'NO' else ''}" for c in cols)
        ddl += "\n)"
        return ddl

    def get_sample_data(self, table_name: str, rows: int = 30) -> pd.DataFrame:
        return pd.read_sql(f"SELECT * FROM {table_name} LIMIT {rows}", self._conn)


CONNECTOR_MAP = {
    "mysql": MySQLConnector,
    "postgresql": PostgreSQLConnector,
}

def get_connector(db_type: str, config: dict) -> DatabaseConnector:
    cls = CONNECTOR_MAP.get(db_type)
    if not cls:
        raise ValueError(f"不支持的数据库类型: {db_type}")
    return cls(config)
```

### 4.4 连接器管理 API

```python
# src/api/data_analysis.py

@router.post("/api/data-analysis/connectors")
async def create_connector(request: ConnectorCreateRequest):
    """创建数据连接器（密码 AES 加密存储）"""
    ...

@router.get("/api/data-analysis/connectors")
async def list_connectors(tenant_id: str = None):
    """列出连接器"""
    ...

@router.post("/api/data-analysis/connectors/{connector_id}/test")
async def test_connector(connector_id: int):
    """测试连接"""
    ...

@router.get("/api/data-analysis/connectors/{connector_id}/tables")
async def list_remote_tables(connector_id: int):
    """获取远程数据库的表列表"""
    ...

@router.post("/api/data-analysis/connectors/{connector_id}/import")
async def import_tables(connector_id: int, request: ImportTablesRequest):
    """导入选中的表（提取 DDL + 样本数据 → LLM Schema 推理 → 存入知识库）"""
    ...
```

---

## 五、模块三（共享）：表 Metadata 知识库

### 5.1 设计思路

复用现有知识库的向量检索能力，但不存文本 chunks，而是存表级别的 Metadata：

| 知识库字段 | 用途 | 内容 |
|-----------|------|------|
| `documents.title` | 表名 | 如"销售明细表" |
| `documents.summary` | 表描述（做向量） | LLM 生成的表业务描述 |
| `documents.source_type` | 数据来源 | `"excel_table"` 或 `"database_table"` |
| `documents.metadata` (JSON) | 列信息 + 关联信息 | 完整的 columns schema + 数据源信息 |

**为什么复用知识库而不是新建独立表？**
- 向量检索、混合检索、租户隔离全部复用，无需重复实现
- 表描述天然适合向量化检索（用户说"销售数据" → 命中"销售明细表"）
- metadata JSON 字段已存在，足以存列信息

### 5.2 Metadata 文档结构

```python
# 注册到知识库的文档结构

{
    # --- documents 表字段 ---
    "title": "销售明细表",                    # 表名（LLM 推理的中文业务名）
    "source_type": "excel_table",             # excel_table | database_table
    "file_type": "structured",                # 标记为结构化数据（区别于普通文档）
    "summary": "该表记录了各区域、各产品线在每月的销售额和订单量...",  # 表描述（做向量）
    "metadata": {
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
        ]
    }
}
```

### 5.3 入库流程

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
            source_type="excel_table",
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
            source_type="database_table",
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
        """直接写入 documents + chunks + chunks_vec 表"""
        from src.db.database import get_db_connection
        from src.knowledge.embedding.embedding_client import TextEmbeddingV3Client
        from src.config.settings import get_embedding_api_key

        embedding_client = TextEmbeddingV3Client(api_key=get_embedding_api_key())
        embedding = await embedding_client.embed(description)

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 插入文档
            import json
            cursor.execute("""
                INSERT INTO documents (
                    user_id, tenant_id, title, source_type, file_type,
                    total_chunks, embedding_model, metadata, summary
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                user_id, tenant_id, title, source_type, "structured",
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

    async def search_tables(self, query: str, tenant_id: str, top_k: int = 3) -> list[dict]:
        """检索与用户问题匹配的表 Metadata"""
        results = await self.kb.search_documents(
            query=query,
            tenant_id=tenant_id,
            top_k=top_k
        )

        if not results.get("success"):
            return []

        tables = []
        for r in results.get("results", []):
            # 只返回结构化数据类型的文档
            doc_id = r["doc_id"]
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT metadata, summary FROM documents WHERE id = %s",
                    (doc_id,)
                )
                row = cursor.fetchone()
                if row and row.get("metadata"):
                    meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
                    tables.append({
                        "doc_id": doc_id,
                        "score": r["score"],
                        "summary": row["summary"],
                        **meta
                    })

        return tables
```

---

## 六、模块四：数据分析工具集

### 6.1 设计思路

**不使用 LLM 直接生成 SQL/代码**。而是提供一组预定义的 pandas 数据处理工具，LLM 只负责：
1. 根据用户问题，从 Metadata 知识库匹配到相关表
2. 理解用户意图，选择合适的工具
3. 根据表的列信息，为工具填充参数

工具一览：

| 工具 | name | 功能 | 核心参数 |
|------|------|------|---------|
| `QueryTableTool` | `query_table` | 查询/过滤表数据 | table_id, filters, columns, sort_by, limit |
| `AggregateDataTool` | `aggregate_data` | 分组聚合 | table_id, group_by, aggregations, filters |
| `CompareDataTool` | `compare_data` | 对比分析 | table_id, compare_column, group_by, metric |
| `TrendAnalysisTool` | `trend_analysis` | 趋势分析 | table_id, date_column, value_column, freq |
| `VisualizeDataTool` | `visualize_data` | 生成 HTML 交互式图表报告 | chart_type, data, x_column, y_columns, title |

### 6.2 QueryTableTool — 查询过滤

```python
# src/tools/data_analysis/query_table_tool.py

from pydantic import BaseModel, Field
from typing import Optional
from src.tools.base import BaseTool


class QueryTableInput(BaseModel):
    table_id: str = Field(description="表标识（从 Metadata 中获取的 doc_id 或 session 内的表名）")
    columns: Optional[list[str]] = Field(
        None,
        description="要返回的列名列表，空则返回全部列"
    )
    filters: Optional[list[dict]] = Field(
        None,
        description='''过滤条件列表，每个条件格式: {"column": "列名", "op": "操作符", "value": "值"}
        支持的操作符: eq(等于), neq(不等于), gt(大于), gte(大于等于), lt(小于), lte(小于等于),
        in(在列表中), not_in(不在列表中), contains(包含), startswith(开头是)'''
    )
    sort_by: Optional[str] = Field(None, description="排序字段")
    sort_order: Optional[str] = Field("asc", description="排序方向: asc | desc")
    limit: Optional[int] = Field(100, description="返回行数上限")


class QueryTableTool(BaseTool):
    name = "query_table"
    description = "查询和过滤表数据，支持条件筛选、列选择、排序"
    display_name = "数据查询"
    category = "data_analysis"
    InputModel = QueryTableInput

    async def execute(self, **kwargs) -> dict:
        table_id = kwargs["table_id"]
        df = self._get_dataframe(table_id)
        if df is None:
            return {"success": False, "error": f"未找到表: {table_id}"}

        # 应用过滤条件
        if kwargs.get("filters"):
            df = self._apply_filters(df, kwargs["filters"])

        # 列选择
        if kwargs.get("columns"):
            missing = set(kwargs["columns"]) - set(df.columns)
            if missing:
                return {"success": False, "error": f"列不存在: {missing}"}
            df = df[kwargs["columns"]]

        # 排序
        if kwargs.get("sort_by"):
            ascending = kwargs.get("sort_order", "asc") == "asc"
            df = df.sort_values(by=kwargs["sort_by"], ascending=ascending)

        # 行数限制
        limit = kwargs.get("limit", 100)
        total = len(df)
        truncated = total > limit
        df = df.head(limit)

        return {
            "success": True,
            "columns": list(df.columns),
            "rows": df.values.tolist(),
            "row_count": len(df),
            "total_count": total,
            "truncated": truncated,
        }

    def _apply_filters(self, df: pd.DataFrame, filters: list[dict]) -> pd.DataFrame:
        OP_MAP = {
            "eq": lambda col, val: df[col] == val,
            "neq": lambda col, val: df[col] != val,
            "gt": lambda col, val: df[col] > val,
            "gte": lambda col, val: df[col] >= val,
            "lt": lambda col, val: df[col] < val,
            "lte": lambda col, val: df[col] <= val,
            "in": lambda col, val: df[col].isin(val),
            "not_in": lambda col, val: ~df[col].isin(val),
            "contains": lambda col, val: df[col].astype(str).str.contains(val),
            "startswith": lambda col, val: df[col].astype(str).str.startswith(val),
        }
        mask = pd.Series([True] * len(df), index=df.index)
        for f in filters:
            op_func = OP_MAP.get(f["op"])
            if op_func:
                mask &= op_func(f["column"], f["value"])
        return df[mask]
```

### 6.3 AggregateDataTool — 分组聚合

```python
# src/tools/data_analysis/aggregate_data_tool.py

class AggregateDataInput(BaseModel):
    table_id: str = Field(description="表标识")
    group_by: list[str] = Field(description="分组字段列表")
    aggregations: list[dict] = Field(
        description='''聚合操作列表，每项格式: {"column": "列名", "function": "聚合函数", "alias": "结果列名(可选)"}
        聚合函数: sum, mean, count, min, max, median, std, nunique(去重计数), first(取第一个值)'''
    )
    filters: Optional[list[dict]] = Field(None, description="过滤条件（同 query_table）")
    sort_by: Optional[str] = Field(None, description="结果排序字段（聚合结果的列名）")
    limit: Optional[int] = Field(100, description="返回行数上限")


class AggregateDataTool(BaseTool):
    name = "aggregate_data"
    description = "对表数据进行分组聚合统计，如按区域汇总销售额、按月份统计订单数"
    display_name = "数据聚合"
    category = "data_analysis"
    InputModel = AggregateDataInput

    async def execute(self, **kwargs) -> dict:
        table_id = kwargs["table_id"]
        df = self._get_dataframe(table_id)
        if df is None:
            return {"success": False, "error": f"未找到表: {table_id}"}

        # 应用过滤
        if kwargs.get("filters"):
            df = self._apply_filters(df, kwargs["filters"])

        # 构建聚合字典
        agg_dict = {}
        alias_map = {}
        for agg in kwargs["aggregations"]:
            col = agg["column"]
            func = agg["function"]
            alias = agg.get("alias", f"{col}_{func}")
            agg_dict[col] = agg_dict.get(col, [])
            if isinstance(agg_dict[col], list):
                agg_dict[col].append(func)
            alias_map[(col, func)] = alias

        # 执行分组聚合
        grouped = df.groupby(kwargs["group_by"], dropna=False).agg(agg_dict)
        grouped.columns = [alias_map.get((col, func), f"{col}_{func}")
                           for col, func in grouped.columns]
        result_df = grouped.reset_index()

        # 排序
        if kwargs.get("sort_by"):
            result_df = result_df.sort_values(by=kwargs["sort_by"], ascending=False)

        # 行数限制
        limit = kwargs.get("limit", 100)
        result_df = result_df.head(limit)

        return {
            "success": True,
            "columns": list(result_df.columns),
            "rows": result_df.values.tolist(),
            "row_count": len(result_df),
            "group_by": kwargs["group_by"],
        }
```

### 6.4 TrendAnalysisTool — 趋势分析

```python
# src/tools/data_analysis/trend_analysis_tool.py

class TrendAnalysisInput(BaseModel):
    table_id: str = Field(description="表标识")
    date_column: str = Field(description="日期列名")
    value_column: str = Field(description="数值列名")
    freq: Optional[str] = Field("M", description="聚合频率: D(天)/W(周)/M(月)/Q(季)/Y(年)")
    agg_func: Optional[str] = Field("sum", description="聚合函数: sum/mean/count")
    filters: Optional[list[dict]] = Field(None, description="过滤条件")


class TrendAnalysisTool(BaseTool):
    name = "trend_analysis"
    description = "分析数值随时间的变化趋势，自动按时间粒度聚合"
    display_name = "趋势分析"
    category = "data_analysis"
    InputModel = TrendAnalysisInput

    async def execute(self, **kwargs) -> dict:
        table_id = kwargs["table_id"]
        df = self._get_dataframe(table_id)
        if df is None:
            return {"success": False, "error": f"未找到表: {table_id}"}

        date_col = kwargs["date_column"]
        value_col = kwargs["value_column"]

        # 确保日期列是 datetime 类型
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])

        # 过滤
        if kwargs.get("filters"):
            df = self._apply_filters(df, kwargs["filters"])

        # 按时间粒度聚合
        freq = kwargs.get("freq", "M")
        agg_func = kwargs.get("agg_func", "sum")
        df["period"] = df[date_col].dt.to_period(freq)
        trend = df.groupby("period").agg({value_col: agg_func}).reset_index()
        trend["period"] = trend["period"].astype(str)

        # 计算环比变化率
        trend["change_rate"] = trend[value_col].pct_change()

        return {
            "success": True,
            "columns": ["period", value_col, "change_rate"],
            "rows": trend.values.tolist(),
            "row_count": len(trend),
            "freq": freq,
        }
```

### 6.5 CompareDataTool — 对比分析

```python
# src/tools/data_analysis/compare_data_tool.py

class CompareDataInput(BaseModel):
    table_id: str = Field(description="表标识")
    compare_column: str = Field(description="用于对比的维度列（如区域、产品线）")
    value_column: str = Field(description="要对比的数值列")
    agg_func: Optional[str] = Field("sum", description="聚合函数")
    top_n: Optional[int] = Field(10, description="取前 N 个分组对比")
    filters: Optional[list[dict]] = Field(None, description="过滤条件")


class CompareDataTool(BaseTool):
    name = "compare_data"
    description = "对比不同分组的数据差异，如各区域销售额对比、各产品线利润率对比"
    display_name = "对比分析"
    category = "data_analysis"
    InputModel = CompareDataInput

    async def execute(self, **kwargs) -> dict:
        table_id = kwargs["table_id"]
        df = self._get_dataframe(table_id)
        if df is None:
            return {"success": False, "error": f"未找到表: {table_id}"}

        if kwargs.get("filters"):
            df = self._apply_filters(df, kwargs["filters"])

        compare_col = kwargs["compare_column"]
        value_col = kwargs["value_column"]
        agg_func = kwargs.get("agg_func", "sum")
        top_n = kwargs.get("top_n", 10)

        # 按对比维度聚合
        result = df.groupby(compare_col).agg({value_col: agg_func}).reset_index()
        result = result.sort_values(value_col, ascending=False).head(top_n)

        # 计算占比
        total = result[value_col].sum()
        result["占比"] = (result[value_col] / total * 100).round(2)

        return {
            "success": True,
            "columns": list(result.columns),
            "rows": result.values.tolist(),
            "row_count": len(result),
            "total": float(total),
        }
```

### 6.6 VisualizeDataTool — 数据可视化（HTML 报告）

**设计思路**：不使用 matplotlib 生成静态图片，而是生成 HTML 交互式报告。复用现有 `FileWriteTool` 的 HTML 生成能力 + `file_write` 工具写文件 + `/api/files/{file_id}` 内联预览。

**方案对比**：

| 维度 | matplotlib 图片 | HTML 报告 |
|------|----------------|-----------|
| 交互性 | 无（静态图片） | 可交互（排序、hover 提示、缩放） |
| 中文支持 | 需配置字体，Linux 常出问题 | 原生支持 |
| 预览方式 | 下载图片查看 | 浏览器内联预览 |
| 复用现有能力 | 新增依赖 | 复用 `FileWriteTool` + 下载注册 |
| 多图表组合 | 每个图单独文件 | 一个 HTML 包含多个图表 |

**实现方式**：使用 ECharts（通过 CDN）生成可交互图表，纯 HTML 输出，无需额外 npm 依赖。

```python
# src/tools/data_analysis/visualize_data_tool.py

class VisualizeDataInput(BaseModel):
    chart_type: str = Field(description="图表类型: bar | line | pie | scatter | table | multi_chart")
    data: list = Field(description="图表数据，格式: [[行1值1, 行1值2], ...]")
    columns: list[str] = Field(description="列名列表，与 data 中的列对应")
    x_column: Optional[str] = Field(None, description="X 轴列名")
    y_columns: Optional[list[str]] = Field(None, description="Y 轴列名列表")
    title: Optional[str] = Field("数据分析报告", description="报告标题")
    charts: Optional[list[dict]] = Field(
        None,
        description='''多个图表配置（multi_chart 模式），每项格式:
        {"chart_type": "bar|line|pie", "title": "子标题", "x_column": "...", "y_columns": ["..."]}'''
    )


class VisualizeDataTool(BaseTool):
    name = "visualize_data"
    description = "生成 HTML 交互式数据可视化报告，支持柱状图、折线图、饼图、数据表格"
    display_name = "数据可视化"
    category = "data_analysis"
    InputModel = VisualizeDataInput

    async def execute(self, **kwargs) -> dict:
        chart_type = kwargs["chart_type"]
        data = kwargs["data"]
        columns = kwargs["columns"]
        title = kwargs.get("title", "数据分析报告")

        if chart_type == "multi_chart" and kwargs.get("charts"):
            html = self._build_multi_chart_report(data, columns, title, kwargs["charts"])
        elif chart_type == "table":
            html = self._build_table_report(data, columns, title)
        else:
            x_col = kwargs.get("x_column") or columns[0]
            y_cols = kwargs.get("y_columns") or [c for c in columns if c != x_col]
            html = self._build_single_chart(data, columns, chart_type, title, x_col, y_cols)

        # 调用 FileWriteTool 写入 HTML 文件
        from src.tools.file.text_file_writer import FileWriteTool
        file_tool = FileWriteTool()
        result = await file_tool.execute(
            content=html,
            file_path=f"{title}.html"
        )

        if result.get("success"):
            return {
                "success": True,
                "file_id": result.get("file_id"),
                "download_url": result.get("download_url"),
                "preview_url": result.get("download_url"),  # 同一 URL，浏览器内联预览
                "report_title": title,
            }
        return {"success": False, "error": result.get("error", "HTML 生成失败")}

    def _build_single_chart(self, data, columns, chart_type, title, x_col, y_cols) -> str:
        """生成单图表 HTML 报告"""
        chart_option = self._build_echarts_option(data, columns, chart_type, x_col, y_cols)
        return self._wrap_html(title, [({"title": title, "option": chart_option})])

    def _build_multi_chart_report(self, data, columns, title, chart_configs) -> str:
        """生成多图表 HTML 报告"""
        charts = []
        for config in chart_configs:
            ct = config.get("chart_type", "bar")
            x_col = config.get("x_column", columns[0])
            y_cols = config.get("y_columns", [c for c in columns if c != x_col])
            option = self._build_echarts_option(data, columns, ct, x_col, y_cols)
            charts.append({"title": config.get("title", ""), "option": option})
        return self._wrap_html(title, charts)

    def _build_table_report(self, data, columns, title) -> str:
        """生成纯表格 HTML 报告"""
        rows_html = ""
        for row in data:
            cells = "".join(f"<td>{v}</td>" for v in row)
            rows_html += f"<tr>{cells}</tr>"
        headers_html = "".join(f"<th>{c}</th>" for c in columns)

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
.container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 24px; border-radius: 8px; }}
h1 {{ color: #333; border-bottom: 2px solid #4472C4; padding-bottom: 8px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
th {{ background: #4472C4; color: white; padding: 10px; text-align: left; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #e0e0e0; }}
tr:hover {{ background: #f0f4ff; }}
</style></head><body><div class="container">
<h1>{title}</h1>
<table><thead><tr>{headers_html}</tr></thead>
<tbody>{rows_html}</tbody></table>
</div></body></html>"""

    def _build_echarts_option(self, data, columns, chart_type, x_col, y_cols) -> dict:
        """构建 ECharts option 配置"""
        x_values = [str(row[columns.index(x_col)]) for row in data]
        series = []
        for y_col in y_cols:
            y_values = [row[columns.index(y_col)] for row in data]
            series.append({
                "name": y_col,
                "type": "bar" if chart_type == "bar" else ("line" if chart_type == "line" else "scatter"),
                "data": y_values,
            })

        if chart_type == "pie":
            series = [{
                "type": "pie",
                "data": [{"name": str(row[columns.index(x_col)]),
                           "value": row[columns.index(y_cols[0])]} for row in data]
            }]
            return {"series": series}

        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": x_values},
            "yAxis": {"type": "value"},
            "series": series,
        }

    def _wrap_html(self, title: str, charts: list[dict]) -> str:
        """生成完整的 HTML 报告壳"""
        charts_html = ""
        for i, chart in enumerate(charts):
            charts_html += f"""
            <div class="chart-section">
                <h2>{chart['title']}</h2>
                <div id="chart{i}" style="width:100%;height:400px;"></div>
            </div>"""

        options_js = ""
        for i, chart in enumerate(charts):
            import json
            options_js += f"""
            var chart{i} = echarts.init(document.getElementById('chart{i}'));
            chart{i}.setOption({json.dumps(chart['option'], ensure_ascii=False)});
            """

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 0; background: #f5f5f5; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
h1 {{ color: #333; border-bottom: 2px solid #4472C4; padding-bottom: 8px; }}
.chart-section {{ background: white; border-radius: 8px; padding: 20px; margin: 16px 0; }}
h2 {{ color: #4472C4; margin-top: 0; }}
</style></head><body><div class="container">
<h1>{title}</h1>
{charts_html}
</div>
<script>{options_js}
window.addEventListener('resize', function() {{
    {"".join(f"chart{i}.resize();" for i in range(len(charts)))}
}});
</script></body></html>"""
```

---

## 七、子智能体定义

### 7.1 SUBAGENT.md

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
  - 数据查询过滤与分组聚合
  - 趋势分析与对比分析
  - 数据可视化（HTML 交互式图表报告）
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
tools:
  inherit: false
  allowed:
    - query_table
    - aggregate_data
    - compare_data
    - trend_analysis
    - visualize_data
    - excel_process
skills:
  allowed: []
context:
  max_input_tokens: 8000
  max_output_tokens: 4000
---
```

Body（system_prompt）将包含数据分析规范、工具使用指引、安全规则等。

### 7.2 工具注册

在 `Agent._register_builtin_tools()` 中注册数据分析工具：

```python
# 数据分析工具（仅在 data-analysis 子智能体中可用）
from src.tools.data_analysis.query_table_tool import QueryTableTool
from src.tools.data_analysis.aggregate_data_tool import AggregateDataTool
from src.tools.data_analysis.compare_data_tool import CompareDataTool
from src.tools.data_analysis.trend_analysis_tool import TrendAnalysisTool
from src.tools.data_analysis.visualize_data_tool import VisualizeDataTool

self.tool_registry.register(QueryTableTool())
self.tool_registry.register(AggregateDataTool())
self.tool_registry.register(CompareDataTool())
self.tool_registry.register(TrendAnalysisTool())
self.tool_registry.register(VisualizeDataTool())
```

---

## 八、完整工作流

### 8.1 Excel 上传分析流程

```
Turn 1: 用户上传 "销售报表.xlsx"
  → [ExcelProcessTool.read] 读取文件
  → [TableDetector] 识别 Sheet 中的表格区域
  → [SchemaExtractor] LLM 推理每个表的 Schema
  → [TableMetadataManager] 将表 Metadata 存入知识库
  → [DataFrameStore] 缓存 DataFrame 到会话
  → 回复："我已解析了销售报表，发现 3 个表格：
     1. 销售明细（200 行，含区域/产品/销售额/月份）
     2. 目标对比（12 行，各区域年度目标）
     3. 汇总统计（5 行，各产品线汇总）"

Turn 2: 用户："按区域统计销售额"
  → [知识库检索] "按区域统计销售额" → 命中"销售明细表"
  → [LLM] 读取 Schema，判断需要 aggregate_data
  → [LLM 调用] aggregate_data(
        table_id="销售明细",
        group_by=["区域"],
        aggregations=[{"column": "销售额", "function": "sum", "alias": "销售总额"}],
        sort_by="销售总额"
    )
  → [AggregateDataTool] 加载 DataFrame → groupby → 返回结果
  → [LLM] 格式化结果为自然语言回答

Turn 3: 用户："画个柱状图"
  → [LLM 调用] visualize_data(
        chart_type="bar",
        data=上一步的结果数据,
        columns=["区域", "销售总额"],
        x_column="区域",
        y_columns=["销售总额"],
        title="各区域销售总额"
    )
  → [VisualizeDataTool] 生成 HTML（ECharts 柱状图）→ FileWriteTool 写文件 → 注册下载
  → 返回 HTML 报告链接（浏览器内联预览，可交互）
```

### 8.2 数据库连接分析流程

```
管理后台: 用户注册 MySQL 连接器 → 选择表 → 导入
  → [DatabaseConnector] 连接数据库
  → [获取 DDL + 前 30 行]
  → [SchemaExtractor] LLM 推理 Schema
  → [TableMetadataManager] 存入知识库

对话中: 用户："ERP 里最近新增了哪些客户？"
  → [知识库检索] 命中 ERP 导入的客户表
  → [LLM] 从 Metadata 中读取 connector_id 和 db_table_name
  → [LLM 调用] query_table(
        table_id="客户表",
        filters=[{"column": "创建时间", "op": "gte", "value": "2026-05-01"}],
        sort_by="创建时间",
        sort_order="desc"
    )
  → [QueryTableTool] 通过 DataConnector 从远程数据库加载 DataFrame → 过滤 → 返回
```

---

## 九、数据库变更

### 9.1 新增表

```sql
-- deploy/init-postgres.sql 新增

-- 数据连接器
CREATE TABLE IF NOT EXISTS data_connectors (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

### 9.2 增量变更

```sql
-- deploy/db_update.sql 新增

-- 2026-06-02，新增数据分析智能体相关表
CREATE TABLE IF NOT EXISTS data_connectors (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dc_tenant ON data_connectors(tenant_id, is_active);
```

> **注意**：不使用 `data_table_registry` 表（原设计），改为复用 `documents` 表 + `metadata` JSON 字段存储表 Schema。

---

## 十、新增代码文件清单

| 文件路径 | 功能 | 估算行数 |
|---------|------|---------|
| `subagents/data-analysis/SUBAGENT.md` | 子智能体定义 | ~80 行 |
| `src/tools/data_analysis/__init__.py` | 模块入口 | ~10 行 |
| `src/tools/data_analysis/table_detector.py` | Sheet 内表格区域识别 | ~80 行 |
| `src/tools/data_analysis/schema_extractor.py` | LLM Schema 推理 | ~100 行 |
| `src/tools/data_analysis/metadata_manager.py` | Metadata 知识库存取 | ~200 行 |
| `src/tools/data_analysis/data_store.py` | DataFrame 进程内缓存 | ~60 行 |
| `src/tools/data_analysis/db_connector.py` | 数据库连接器（MySQL/PG） | ~150 行 |
| `src/tools/data_analysis/query_table_tool.py` | 查询过滤工具 | ~120 行 |
| `src/tools/data_analysis/aggregate_data_tool.py` | 分组聚合工具 | ~100 行 |
| `src/tools/data_analysis/compare_data_tool.py` | 对比分析工具 | ~80 行 |
| `src/tools/data_analysis/trend_analysis_tool.py` | 趋势分析工具 | ~80 行 |
| `src/tools/data_analysis/visualize_data_tool.py` | 数据可视化工具（HTML 报告） | ~180 行 |
| `src/tools/data_analysis/excel_import.py` | Excel 文件导入 → Metadata 注册 | ~120 行 |
| `src/api/data_analysis.py` | 管理后台 API | ~150 行 |

**总计约 ~1510 行新增代码。**

---

## 十一、与现有系统的集成

### 11.1 依赖安装

```txt
# requirements.txt 新增
pymysql>=1.1.0          # MySQL 连接器（可选依赖）

# 不需要 matplotlib — 可视化使用 HTML + ECharts（CDN），复用 FileWriteTool
# 不需要 sqlglot — 不使用 Text-to-SQL 路径
```

### 11.2 配置变更

```yaml
# configs/config.yaml 新增
data_analysis:
  file_analysis:
    max_file_size_mb: 50
    max_sample_rows: 50       # LLM Schema 推理的样本行数

  db_connector:
    enabled: true
    query_timeout_seconds: 30
    max_result_rows: 1000
    password_encryption_key: "${DATA_ANALYSIS_ENCRYPTION_KEY}"
```

---

## 十二、安全设计

### 12.1 威胁模型与防护

| 威胁 | 防护层 | 措施 |
|------|--------|------|
| 跨租户数据泄露 | 知识库检索 | 复用知识库租户隔离（tenant_id 过滤） |
| 数据库密码泄露 | 数据连接器 | AES 加密存储，日志脱敏 |
| 恶意过滤条件注入 | 工具参数 | Pydantic 校验，白名单操作符 |
| 资源耗尽（慢查询） | 数据库连接器 | statement_timeout + LIMIT |
| 文件炸弹（超大文件） | 文件加载 | max_file_size_mb 限制 |
| 数据批量导出 | 结果限制 | max_result_rows = 1000 |

### 12.2 与原设计的安全差异

原设计使用 sqlglot 做 SQL AST 验证（因为 LLM 直接生成 SQL）+ 进程内代码执行器（matplotlib 生成图片）。新设计中 LLM 只传参数给预定义工具，不生成代码，**从根本上消除了代码注入风险**。可视化走 HTML 文件生成（复用 `FileWriteTool`），无需进程内代码执行。

---

## 十三、实施计划

### 第一阶段：Excel 解析 + 基础分析（3-4 周）

| 周次 | 任务 | 交付物 |
|------|------|--------|
| W1 | 子智能体定义 + TableDetector + SchemaExtractor | `SUBAGENT.md` + `table_detector.py` + `schema_extractor.py` |
| W1 | MetadataManager + DataFrameStore | `metadata_manager.py` + `data_store.py` |
| W2 | ExcelImportTool（上传 → 解析 → 入库） | `excel_import.py` |
| W2 | QueryTableTool + AggregateDataTool | `query_table_tool.py` + `aggregate_data_tool.py` |
| W3 | CompareDataTool + TrendAnalysisTool + VisualizeDataTool | 3 个工具 |
| W3 | 结果格式化 + 多轮对话支持 | 会话缓存 |
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

## 十四、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| LLM Schema 推理不准确 | 表名/列语义错误 | 人工审核入口 + 可手动修正 metadata |
| Sheet 内表格区域识别不准 | 多表格被合并或遗漏 | 人工可调整表格范围后重新解析 |
| 数据库连接器兼容性 | 特定数据库方言不支持 | 首期仅 MySQL/PG，按需扩展 |
| 大文件内存占用 | 影响服务稳定性 | 文件大小限制 + 行数限制 |
| Metadata 知识库与文档知识库混搜 | 表文档和普通文档混淆 | 检索时用 source_type 过滤 |
| 外部数据库表结构变更 | Metadata 过时 | 管理后台提示 + 手动刷新 |
| ECharts CDN 不可达 | 图表无法渲染 | 可部署内网 CDN 或 fallback 到纯表格 |

---

## 十五、与原设计的关键差异

| 维度 | 原设计（2026-05-29） | 新设计（2026-06-02） |
|------|---------------------|---------------------|
| 查询方式 | Text-to-SQL（LLM 生成 SQL） | LLM 传参给预定义 pandas 工具 |
| Schema 来源 | 手动注册到 `data_table_registry` | Excel 智能解析 + 数据库连接器自动发现 |
| Metadata 存储 | 独立表 + 关键词检索 | 复用知识库向量系统 |
| 安全模型 | sqlglot AST 验证 + 进程内代码执行器 | 参数白名单 + Pydantic 校验（无代码生成） |
| 数据连接器 | 无 | 核心模块（MySQL/PG） |
| LLM 职责 | 生成 SQL/代码 | 理解意图 + 选工具 + 传参数 |
