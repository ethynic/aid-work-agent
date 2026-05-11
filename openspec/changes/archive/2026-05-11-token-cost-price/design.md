## Context

平台Token消耗报表（`PlatformTokenUsage.vue`）当前展示各租户的输入/输出Token数量和对话次数，数据来源于 `chat_records` 表按 `tenant_id` 分组汇总。`chat_records` 每条记录包含 `model` 字段（如 `qwen-plus`），但当前 SQL 未利用此字段。

系统需要增加成本核算能力：根据各模型的输入/输出单价，计算每个租户的Token消耗成本。

## Goals / Non-Goals

**Goals:**
1. 新增模型单价配置表，支持手动维护各模型成本价
2. 平台报表每行增加成本列，汇总行和卡片区增加总成本
3. 未配置单价的模型不计入成本，前端显示"—"
4. 成本保留2位小数

**Non-Goals:**
1. 前端管理页面（单价数据由管理员直接在DB中维护）
2. 修改"我的Token消耗报表"
3. 成本预警或预算功能
4. 历史成本回算（单价变更后不影响已计算的报表）

## Decisions

### 1. 成本计算方式：SQL LEFT JOIN

在 `ChatRecordDB.get_platform_token_usage` 的 SQL 中 LEFT JOIN `token_cost_prices` 表：

```sql
SELECT cr.tenant_id,
  COUNT(*) as conversation_count,
  COALESCE(SUM(cr.prompt_tokens), 0) as input_tokens,
  COALESCE(SUM(cr.completion_tokens), 0) as output_tokens,
  COALESCE(SUM(cr.prompt_tokens * tcp.input_price_per_m / 1000000), 0) as input_cost,
  COALESCE(SUM(cr.completion_tokens * tcp.output_price_per_m / 1000000), 0) as output_cost
FROM chat_records cr
LEFT JOIN token_cost_prices tcp ON cr.model = tcp.model_name
WHERE cr.created_at >= %s AND cr.created_at <= %s
  AND NOT (cr.prompt_tokens = 0 AND cr.completion_tokens = 0)
GROUP BY cr.tenant_id
ORDER BY conversation_count DESC
```

**理由**：两表 JOIN 是简单操作，SQL 层计算比应用层循环更高效，且一次查询返回所有数据。

**未匹配模型处理**：LEFT JOIN 使未配置单价的记录 `tcp.input_price_per_m` 为 NULL，`NULL * token / 1000000` 为 NULL，`SUM` 跳过 NULL，COALESCE 归零。前端通过 `input_cost + output_cost` 是否为 0 且 token 数不为 0 来判断是否显示"—"。

### 2. 表设计：系统表 `token_cost_prices`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | |
| model_name | TEXT UNIQUE NOT NULL | 模型名称，对应 chat_records.model |
| input_price_per_m | NUMERIC(10,4) | 输入百万Token单价(元) |
| output_price_per_m | NUMERIC(10,4) | 输出百万Token单价(元) |
| created_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

**UNIQUE 约束**：`model_name` 唯一，确保每个模型只有一条单价记录。

### 3. 前端显示规则

| 场景 | 显示 |
|------|------|
| 成本 > 0 | 保留2位小数，如 "12.34" |
| 成本 = 0 且 token 数 > 0 | "—"（有消耗但无单价配置） |
| 成本 = 0 且 token 数 = 0 | "0.00" |

判断逻辑：`has_cost_config` 字段（后端返回），表示该租户是否有未匹配单价的模型。

### 4. 后端响应扩展

`PlatformTokenUsageResponse` 中每个租户数据增加：
```json
{
  "input_cost": 5.23,
  "output_cost": 12.50,
  "total_cost": 17.73,
  "has_unpriced_tokens": false
}
```

`summary` 增加：
```json
{
  "total_input_cost": 52.30,
  "total_output_cost": 125.00,
  "total_cost": 177.30,
  "has_unpriced_tokens": true
}
```

`has_unpriced_tokens`：是否存在未配单价的模型消耗。如果为 true，汇总行成本旁显示提示"含未计价模型"。

## Risks / Trade-offs

### 1. 单价变更影响历史报表
- **风险**：管理员修改模型单价后，历史月份报表成本会随之变化
- **缓解**：当前可接受，单价变更不频繁。如需固定历史成本，后续可考虑快照机制

### 2. LEFT JOIN 性能
- **风险**：`token_cost_prices` 表极小（几行），JOIN 对性能基本无影响
- **缓解**：`model_name` 上有 UNIQUE 约束，等值查找 O(1)

### 3. 多模型租户的成本归零判断
- **风险**：租户同时使用已配价和未配价模型时，成本只计已配价部分，但 `has_unpriced_tokens=true` 提示用户
- **缓解**：提示文字"含未计价模型"明确告知成本不完整
