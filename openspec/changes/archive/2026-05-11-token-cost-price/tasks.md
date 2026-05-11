## 1. 数据库变更

- [x] 1.1 创建 `token_cost_prices` 表
  - 在 `deploy/init-postgres.sql` 中添加 CREATE TABLE 语句
  - 在 `deploy/db_update.sql` 中添加增量变更（含注释和日期）
  - 插入初始数据：qwen-plus, 0.8, 2.0

## 2. 后端改造

- [x] 2.1 修改 `ChatRecordDB.get_platform_token_usage` SQL
  - LEFT JOIN `token_cost_prices` ON `chat_records.model = token_cost_prices.model_name`
  - 计算 `input_cost` 和 `output_cost`
  - 增加 `has_unpriced_tokens` 判断（使用 EXISTS 子查询）

- [x] 2.2 修改 `src/api/admin_reports.py` 响应结构
  - 每个租户数据增加 `input_cost`、`output_cost`、`total_cost`、`has_unpriced_tokens`
  - `summary` 增加 `total_input_cost`、`total_output_cost`、`total_cost`、`has_unpriced_tokens`

## 3. 前端改造

- [x] 3.1 修改 `PlatformTokenUsage.vue`
  - 表格新增"Token成本(元)"列（在"对话次数"列之前）
  - 成本显示：有值保留2位小数，未配价显示"—"
  - 汇总行新增总成本
  - 汇总卡片区域新增"总成本(元)"卡片（5列布局）
  - `has_unpriced_tokens` 为 true 时在卡片标题显示提示"含未计价模型"

- [x] 3.2 构建验证
  - 运行 `cd frontend && npm run build` 确认无语法错误 ✓
