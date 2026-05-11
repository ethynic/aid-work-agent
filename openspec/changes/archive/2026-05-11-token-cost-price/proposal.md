## Why

平台Token消耗报表目前只展示各租户的输入/输出Token数量，无法直观了解实际成本。平台管理员需要知道每个租户消耗了多少费用，以便进行成本核算和资源规划。

## What Changes

1. **新增 `token_cost_prices` 表**
   - 存储各模型的输入/输出百万Token单价
   - 初始数据：qwen-plus, 输入 0.8 元/百万Token, 输出 2.0 元/百万Token
   - 其他模型数据由管理员手动补充，无需前端管理页面

2. **平台Token消耗报表增加"Token成本(元)"列**
   - 通过 SQL LEFT JOIN `token_cost_prices` 表，按模型匹配单价计算成本
   - 成本 = SUM(prompt_tokens × input_price / 1000000) + SUM(completion_tokens × output_price / 1000000)
   - 未配置单价的模型，该部分成本不计入（NULL 参与运算结果为 NULL，COALESCE 归零）
   - 每个租户行显示成本，汇总行显示总成本
   - 汇总卡片区域新增"总成本(元)"卡片
   - 成本显示保留2位小数，无单价数据时显示"—"

3. **不改动"我的Token消耗报表"**

## Capabilities

### Modified Capabilities
- `token-usage-reporting`: 平台报表增加成本计算维度，基于模型单价表计算各租户Token成本

## Impact

1. **数据库影响**
   - 新增 `token_cost_prices` 表（系统表，无 `bs_` 前缀）
   - 初始数据插入

2. **后端影响**
   - `ChatRecordDB.get_platform_token_usage` SQL 改为 LEFT JOIN `token_cost_prices`
   - `admin_reports.py` 响应增加 `input_cost`、`output_cost` 字段

3. **前端影响**
   - `PlatformTokenUsage.vue` 新增"Token成本(元)"列、汇总行成本、总成本卡片
