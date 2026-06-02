# Text-to-SQL 与数据分析智能体技术调研

> 调研时间：2026-05-29
> 调研范围：Text-to-SQL 框架、生产实践、数据分析智能体、Excel/CSV 分析、中文 LLM 适配

---

## 一、开源 Text-to-SQL 框架

### 1.1 框架总览

| 框架 | 类型 | 核心方法 | 适用场景 |
|------|------|---------|---------|
| **Vanna.ai** | 开源 Python | RAG + LLM | 快速接入，支持多数据库 |
| **Contextual-SQL** | 开源 | 推理时扩展 + 奖励模型 | BIRD benchmark 最优本地方案 |
| **Snowflake Arctic-Text2SQL-R1** | 开源 7B 模型 | RL + CoT 推理 | BIRD 排行榜榜首，企业级 |
| **MATS** | 开源 | 多智能体协作 | 使用小模型实现高精度 |
| **XiYan-SQL** | 开源 | 多生成器集成 | Qwen Coder 基座，中文友好 |
| **Chase-SQL** | 研究 | 多候选 + 自一致性 | BIRD 顶级方案 |
| **DeFog DataQnA** | 开源 | 模板 + LLM 混合 | 企业数据查询 |

### 1.2 Vanna.ai（最流行的开源方案）

**架构**：RAG 流水线
```
用户问题 → 向量检索 DDL/文档/示例 SQL → LLM 生成 SQL → 执行 → 返回结果/图表
```

**核心特性**：
- 基于 RAG 的 schema 检索：将数据库 DDL、文档、训练对（Question-SQL pairs）存储为向量嵌入
- **Function RAG**（2025 新特性）：将传统 Q-SQL 训练对转化为可调用的函数模板/工具，在生成时动态调用
- Vanna 2.0 增加了企业安全、用户级权限
- 支持多种数据库（PostgreSQL、MySQL、Snowflake、BigQuery 等）

**局限**：
- 语义层假设字典是静态的，多租户 B2B SaaS 场景下需要租户特定的 schema 定义
- 简单场景表现好，复杂多表 JOIN 准确率下降明显

**GitHub**: https://github.com/vanna-ai/vanna

### 1.3 Contextual-SQL（BIRD 最佳本地方案）

**架构**：两阶段候选生成 + 选择
```
1. 构建 mSchema 上下文 + 采样 few-shot 示例
2. 以 temperature=1 采样 n 个 SQL 候选，重复 m 次（不同 few-shot）→ 共 n*m 个候选
3. 执行过滤：只保留可执行成功的 SQL
4. 奖励模型排序：训练 Qwen-2.5-32B 作为评分模型
5. 综合 log-probs + reward score 选择最优候选
```

**关键洞察**：
- **上下文质量是关键**：DDL（54.68%）→ mSchema（60.94%）→ mSchema+Fewshot（62.5%）准确率递增
- **推理时扩展（Inference-time Scaling）**：通过大量采样 + 选择，本地模型可以匹敌 API 大模型
- 奖励模型 + log-probs 加权（alpha=0.4 效果最好）
- BIRD-dev 上达到约 73% 执行准确率

**开源地址**: https://github.com/ContextualAI/bird-sql

### 1.4 Snowflake Arctic-Text2SQL-R1/R2

**架构**：轻量级 RL + 强推理
- 7B 参数量模型，专门为 SQL 生成优化
- 使用简单的奖励信号 + 强化学习训练
- BIRD 排行榜第一（超越 GPT-4o 等大模型）
- 后续 R2 版本在企业 SQL 生成上超越前沿大模型

**开源**: https://huggingface.co/Snowflake/Arctic-Text2SQL-R1-7B

### 1.5 MATS（多智能体 Text-to-SQL）

**架构**：专业角色分工的多智能体系统
```
Schema Linking Agent → SQL Generation Agent → Refinement Agent → Execution Agent
```

**核心创新**：
- 使用**小型语言模型**（而非依赖 GPT-4 级别大模型）
- 通过**执行反馈**（execution feedback）迭代改进
- 各智能体分工明确，降低单个智能体的工作负载
- BIRD 上达到 87.1% 准确率

**开源**: https://github.com/thanhdath/mats-sql

---

## 二、生产级 Text-to-SQL 最佳实践

### 2.1 六大失败模式与解决方案

根据 Google Cloud 的总结（[The Six Failures of Text-to-SQL](https://medium.com/google-cloud/the-six-failures-of-text-to-sql-and-how-to-fix-them-with-agents-ef5fd2b74b68)）：

| 失败模式 | 根因 | 解决方案 |
|---------|------|---------|
| **1. 智能体顺序问题** | 单 LLM 自行决定操作顺序 | SequentialAgent 强制有序执行 |
| **2. Schema 幻觉** | LLM 不了解你的数据库 schema | 动态 schema 检索工具（不要一次性 dump 全部 schema） |
| **3. 查询逻辑错误** | 复杂 JOIN/聚合错误 | LoopAgent 迭代改进（Writer + Critic 模式） |
| **4. 性能与成本** | 多 LLM 调用慢且贵 | 用自定义 Agent 处理确定性步骤（如 SQL 解析） |
| **5. 危险查询执行** | 直接执行 LLM 生成的 SQL | sqlglot 静态验证 → dry run → 再执行 |
| **6. LLM 输出格式混乱** | LLM 带有对话性修饰语 | Callback 后处理清理输出 |

### 2.2 生产级架构模式（验证器循环）

```
用户问题
  ↓
[Schema 检索] ← 向量检索 + 元数据过滤
  ↓
[SQL 生成] ← LLM + Few-shot 示例
  ↓
[SQL 验证] ← sqlglot AST 解析 + 语法检查
  ↓ (失败则带错误信息重新生成)
[Dry Run] ← 在测试环境/限制行数执行
  ↓
[安全检查] ← 只读检查、行数限制、禁止 DDL/DML
  ↓
[正式执行]
  ↓
[结果格式化] → 返回给用户
```

### 2.3 Schema 检索/链接策略

**核心原则**：不要把整个数据库 schema 塞进 prompt。

**分层策略**：

1. **表级过滤**：
   - 基于表名/描述的向量检索（pgvector）
   - 关键词匹配（中文表名需要特殊处理）
   - 用户历史查询模式分析

2. **列级过滤**：
   - 基于用户问题中的实体与列名的语义相似度
   - 外键关系追踪（只保留关联表的列）
   - 高基数分类列采样（给 LLM 展示几条样本值）

3. **上下文构建**：
   - **DDL 方式**：CREATE TABLE 语句（最基础，准确率最低）
   - **mSchema 方式**：包含外键关系 + 列样本值（中等准确率）
   - **mSchema + Few-shot**：增加相似查询示例（最高准确率）

### 2.4 SQL 验证与安全

**多层防护**：

```python
# 第1层：语法验证（sqlglot，纯本地，快）
import sqlglot
try:
    ast = sqlglot.parse_one(sql, dialect="postgres")
except sqlglot.errors.ParseError:
    return "SQL 语法错误"

# 第2层：安全规则检查
FORBIDDEN_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE"]
for keyword in FORBIDDEN_KEYWORDS:
    if keyword in sql.upper():
        return f"禁止的 SQL 操作: {keyword}"

# 第3层：Dry Run（限制行数）
dry_sql = f"SELECT * FROM ({sql}) AS _sub LIMIT 10"
# 在只读副本或事务中执行

# 第4层：LLM-as-Judge（可选，验证结果合理性）
```

### 2.5 Few-shot 示例管理

**最佳实践**：
- 维护一个 Question-SQL 示例库（向量数据库存储）
- 根据用户问题语义相似度检索 1-3 个最相关的示例
- 示例应覆盖常见查询模式：单表查询、JOIN、聚合、子查询、窗口函数
- 定期人工审核示例质量，移除过时的示例
- Contextual-SQL 的做法：用不同 few-shot 示例生成多个候选，扩大覆盖面

### 2.6 多轮数据分析对话

**挑战**：
- 上下文指代消解（"刚才那个表" → 哪个表？）
- 查询递进（先看总量 → 再看分月 → 再看某个月）
- 中间结果传递

**方案**：
- 维护对话级 schema state（当前涉及的表、列、过滤条件）
- 每轮对话携带之前查询的 SQL + 结果摘要
- 使用 BIRD-INTERACT 基准评估多轮交互能力

---

## 三、LLM 数据分析智能体（超越 SQL）

### 3.1 SQL vs 代码生成（pandas）对比

根据 [BIRD-Python 论文](https://arxiv.org/html/2601.15728v2) 的系统性研究：

| 维度 | Text-to-SQL | Text-to-Python（pandas） |
|------|-------------|------------------------|
| **范式** | 声明式（描述要什么） | 过程式（一步步怎么做） |
| **LLM 生成难度** | 较低，SQL 简洁 | 较高，需要显式处理 NULL/类型等 |
| **灵活性** | 受限于 SQL 表达能力 | 完整编程能力（ML、可视化、复杂变换） |
| **可验证性** | 高，AST 解析 + dry run | 中，需要沙箱执行 |
| **大数据集性能** | 优，DBMS 优化器处理 | 差，内存限制 |
| **错误模式** | 列选择/JOIN 错误为主 | 逻辑错误为主（17.5% vs SQL 的 0.3%） |

**关键发现**：
- 小模型上 Python 准确率显著低于 SQL（Qwen2.5-Coder 77B：SQL 52.93% → Python 35.95%）
- 但大模型/推理模型差距缩小（Qwen3-Max：Python 63.43%，DeepSeek-R1：62.52%）
- 当提供充分上下文（LCF 框架消除歧义）后，Python 可以达到与 SQL 相当的准确率
- **瓶颈不在代码生成能力，而在领域知识缺失**

### 3.2 代码解释器（Code Interpreter）方案

**2026 年主要平台对比**：

| 平台 | 隔离方式 | GPU 支持 | 并发能力 | 特点 |
|------|---------|---------|---------|------|
| **Modal** | gVisor 容器 | T4 到 B200 | 50,000+ | AI 原生，生产级，Mistral AI/Ramp 使用 |
| **E2B** | Firecracker microVM | 无 GPU | 100（Pro） | 安全沙箱，Perplexity/Hugging Face 使用 |
| **Northflank** | Firecracker/Kata/gVisor | L4/H100/H200 | - | 无限会话时间，BYOC |
| **Daytona** | Linux namespace | - | - | 开源核心，Git 原生 |
| **Cloudflare** | Linux 容器 | 无 | - | TypeScript API，边缘部署 |

**推荐方案**：

对于本项目（企业智能体，不需要 GPU）：
1. **E2B**：最成熟的安全沙箱，Firecracker 硬件级隔离，适合执行不可信代码
2. **自建方案**：Docker + gVisor/Firecracker，控制成本，但运维负担大
3. **Modal**：如果需要 GPU 推理能力（如本地模型）

### 3.3 OpenAI Code Interpreter 模式

**架构**：
```
用户上传文件 → LLM 分析文件结构 → 生成 Python 代码 → 沙箱执行 → 
返回结果/图表 → 用户追问 → 迭代分析
```

**核心组件**：
1. 文件上传与解析（CSV、Excel、JSON）
2. 代码生成（pandas 数据处理 + matplotlib/plotly 可视化）
3. 安全沙箱执行（网络隔离、资源限制）
4. 结果返回（文本、图表、下载文件）

### 3.4 PandasAI（开源代码生成方案）

**架构**：LLM + pandas 代码生成
- 支持多种 LLM（OpenAI、Qwen、本地模型等）
- 支持多种数据源（CSV、SQL、Parquet）
- 自动生成 pandas 代码执行分析
- 支持对话式交互

**GitHub**: https://github.com/sinaptik-ai/pandas-ai

---

## 四、Excel/CSV 分析方案

### 4.1 大文件处理策略

**问题**：LLM 上下文窗口有限（128K tokens），一个包含数万行的 Excel 无法直接放入。

**分层策略**：

| 策略 | 方法 | 适用场景 |
|------|------|---------|
| **Schema 提取** | 只传列名、类型、样本值（前 5 行） | 所有场景（必须） |
| **统计摘要** | 数值列：min/max/mean/std；分类列：unique count/top-5 | 初步探索 |
| **分块 RAG** | 按行/列分块，向量化存储，按需检索 | 超大文件查询 |
| **SQL 中介** | 导入 SQLite/DuckDB，用 Text-to-SQL 查询 | 结构化数据查询 |
| **采样** | 随机采样 N 行进行分析 | 统计分析/趋势发现 |
| **代码生成** | LLM 生成 pandas 代码，沙箱执行 | 复杂分析 |

### 4.2 Schema 提取最佳实践

```python
def extract_schema_for_llm(df, max_sample_rows=5):
    """提取 DataFrame schema 供 LLM 使用"""
    schema = {
        "columns": [],
        "row_count": len(df),
        "sample_data": df.head(max_sample_rows).to_dict(orient="records")
    }
    for col in df.columns:
        col_info = {
            "name": col,
            "dtype": str(df[col].dtype),
            "non_null_count": int(df[col].notna().sum()),
            "null_count": int(df[col].isna().sum()),
        }
        if df[col].dtype in ['int64', 'float64']:
            col_info.update({
                "min": float(df[col].min()),
                "max": float(df[col].max()),
                "mean": float(df[col].mean()),
            })
        elif df[col].dtype == 'object':
            unique_vals = df[col].nunique()
            col_info["unique_count"] = unique_vals
            if unique_vals <= 20:
                col_info["sample_values"] = df[col].value_counts().head(10).to_dict()
        schema["columns"].append(col_info)
    return schema
```

### 4.3 分析能力矩阵

| 分析类型 | SQL 方案 | 代码生成方案 | 推荐选择 |
|---------|---------|------------|---------|
| 简单查询/过滤 | Text-to-SQL | pandas query | SQL（更可靠） |
| 聚合统计 | GROUP BY | df.groupby() | SQL（更高效） |
| 复杂变换 | 困难 | pandas pipeline | 代码生成 |
| 机器学习 | 不支持 | sklearn | 代码生成 |
| 可视化 | 不支持 | matplotlib/plotly | 代码生成 |
| 跨数据源关联 | 困难 | pandas merge | 代码生成 |
| 多表 JOIN | SQL 天然支持 | pd.merge 多步 | SQL |

---

## 五、中文 LLM 适配

### 5.1 中文 LLM SQL 生成能力

根据 [CallSphere 2026 对比](https://callsphere.ai/blog/llm-comparison-sql-query-generation-open-vs-open-may-2026) 和 [BIRD-Python 论文](https://arxiv.org/html/2601.15728v2)：

| 模型 | 参数量 | SQL 生成表现 | 特点 |
|------|--------|------------|------|
| **Qwen3-Max** | 闭源 | BIRD-Python 63.43%，SQL 71.60% | 中文最强，SQL 和 Python 均优秀 |
| **DeepSeek V4-Pro** | 1.6T/49B active | BIRD 表现优异 | 成本低，MIT 协议 |
| **Qwen3-32B** | 32B | SQL 72.75%（LCF 下） | 可本地部署 |
| **Qwen2.5-Coder-77B** | 77B | SQL 52.93%，Python 35.95% | 代码专精 |
| **DeepSeek-R1** | 671B/37B active | SQL/Python 表现接近 Qwen3-Max | 推理能力强 |
| **XiYanSQL-QwenCoder** | 基于 Qwen Coder | 专为 Text-to-SQL 优化 | 中文场景专项 |
| **Arctic-Text2SQL-R1** | 7B | BIRD 排行榜第一 | 模型小但精准，需英文 schema |

### 5.2 双语 Schema 处理挑战

**核心问题**：
1. **表名/列名语言不一致**：数据库用英文（`customer_order`），用户用中文（"客户订单"）
2. **中文列名歧义**：如"状态"在不同表中含义不同
3. **中文值匹配**：`WHERE status = '已发货'` 需要精确匹配中文枚举值

**解决方案**：

```python
# 方案1：双语 Schema 描述
schema_description = """
表: bs_trade_specialist_matched_customers (匹配客户)
  - customer_id: 客户ID (TEXT, 唯一)
  - tenant_id: 租户ID (TEXT, 必填)
  - company_name: 公司名称 (TEXT)
  - country: 国家 (TEXT, 示例: '美国', '英国', '德国')
  - contact_status: 联系状态 (TEXT, 枚举: '未联系', '已联系', '已回复')
"""

# 方案2：语义映射层
SEMANTIC_MAPPING = {
    "客户": "matched_customers",
    "订单": "orders",
    "联系状态": "contact_status",
    "公司名": "company_name",
}

# 方案3：Qwen/DeepSeek 的中文原生能力
# Qwen 系列天然支持中文 SQL 生成，可以：
# - 直接理解中文表名（如 `bs_客户表`）
# - 正确处理中文 WHERE 条件
# - 生成包含中文注释的 SQL
```

### 5.3 推荐策略

对于本项目（使用 Qwen + ZhipuAI）：

1. **Schema 设计**：
   - 数据库表名/列名保持英文（工程规范）
   - 在 schema 描述中提供中文注释和业务含义
   - 枚举值示例包含中文实际值

2. **Prompt 工程**：
   - System prompt 中明确表名/列名的中英文映射
   - Few-shot 示例使用中文问题 → 英文 SQL
   - 指定 SQL 方言（PostgreSQL）

3. **模型选择**：
   - **Qwen3-Max**：中文 SQL 生成最强（闭源 API）
   - **Qwen3-32B**：可本地部署，表现接近 Max
   - **DeepSeek V4-Pro**：成本最低，MIT 协议
   - 对于简单查询，Qwen2.5-Coder 也足够

---

## 六、推荐架构方案

结合本项目特点（企业智能体、多租户、PostgreSQL、Qwen/ZhipuAI），推荐架构：

### 6.1 Text-to-SQL 智能体架构

```
用户提问（中文）
  ↓
[意图识别] → 判断是数据查询还是其他任务
  ↓ (数据查询)
[Schema 检索] 
  → 基于 tenant_id 过滤可见表
  → 向量检索相关表/列
  → 构建双语 schema 描述
  ↓
[Few-shot 检索]
  → 从示例库检索相似查询的 Q-SQL 对
  ↓
[SQL 生成] → Qwen3/DeepSeek 生成 SQL
  ↓
[SQL 验证]
  → sqlglot 语法检查
  → 安全规则（禁止 DDL/DML，行数限制）
  → Dry run（LIMIT 10）
  ↓ (失败 → 带错误信息重新生成，最多3次)
[正式执行] → PostgreSQL（带 tenant_id 过滤）
  ↓
[结果格式化] → 表格/图表 + 自然语言解读
```

### 6.2 Excel/CSV 分析智能体架构

```
用户上传文件
  ↓
[文件解析] → openpyxl/pandas 读取
  ↓
[Schema 提取] → 列名、类型、统计摘要、样本数据
  ↓ (Schema 信息放入 LLM 上下文)
用户提问
  ↓
[路由决策] → 简单查询走 SQL，复杂分析走代码生成
  ↓
[SQL 路径] → 导入 DuckDB/SQLite → Text-to-SQL
[代码路径] → LLM 生成 pandas 代码 → 沙箱执行
  ↓
[结果返回] → 文本 + 图表
```

### 6.3 关键技术选型

| 组件 | 推荐方案 | 理由 |
|------|---------|------|
| SQL 生成 LLM | Qwen3-Max / DeepSeek V4-Pro | 中文 SQL 最强 |
| SQL 验证 | sqlglot | 纯 Python，快，支持多种方言 |
| Schema 检索 | pgvector + 元数据过滤 | 已有 PostgreSQL 基础设施 |
| Few-shot 存储 | pgvector | 与知识库共用向量库 |
| Excel 解析 | openpyxl + pandas | 成熟方案 |
| 代码沙箱 | E2B 或自建 gVisor | 安全隔离 |
| 可视化 | matplotlib/plotly 代码生成 | 灵活，LLM 可控 |

---

## 七、关键参考资源

- [Contextual AI Text-to-SQL 开源方案](https://contextual.ai/blog/open-sourcing-the-best-local-text-to-sql-system/)
- [Google Cloud Text-to-SQL 技术博客](https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql/)
- [Text-to-SQL 六大失败模式](https://medium.com/google-cloud/the-six-failures-of-text-to-sql-and-how-to-fix-them-with-agents-ef5fd2b74b68)
- [MATS 多智能体框架论文](https://arxiv.org/html/2512.18622v1)
- [BIRD-Python: Text-to-Python vs Text-to-SQL 对比](https://arxiv.org/html/2601.15728v2)
- [Snowflake Arctic-Text2SQL-R1](https://www.snowflake.com/en/blog/engineering/arctic-text2sql-r1-sql-generation-benchmark/)
- [Vanna.ai GitHub](https://github.com/vanna-ai/vanna)
- [XiYan-SQL 多生成器框架](https://github.com/XGenerationLab/XiYan-SQL)
- [DeepSeek V4 vs Qwen 3.5 SQL 对比](https://callsphere.ai/blog/llm-comparison-sql-query-generation-open-vs-open-may-2026)
- [代码解释器平台对比](https://modal.com/resources/best-code-interpreters-chatgpt-ai-apps)
- [AWS Text-to-SQL RAG 实践](https://aws.amazon.com/blogs/machine-learning/build-your-gen-ai-based-text-to-sql-application-using-rag-powered-by-amazon-bedrock-claude-3-sonnet-and-amazon-titan-for-embedding/)
- [中文 Text-to-SQL 检索增强论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12558495/)
- [SAFE-SQL 自增强上下文学习](https://arxiv.org/html/2502.11438v2)
