# 开发计划：旅游报价数据补全与模板工具消费

> **关联设计**：[design-travel-quote-template-engine.md](../system/design-travel-quote-template-engine.md)
> **依赖的通用工具计划**：[plan-excel-template-ai.md](plan-excel-template-ai.md)（无状态 `fill_with_sample(样例, data)`，Phase A 完成后可消费）
> **登记**：[docs/ideas.md](../ideas.md)「数字员工 / 子智能体」分区 #43
> **流程**：每个 Phase 走 [dev_workflow.md](../../.claude/rules/dev_workflow.md) 三智能体流程。pytest 走 `./scripts/dev_test.sh`。

## Phase 1 — 报价数据模型补全

> 依赖：无。纯加字段，不改既有 LLM 契约。

- [ ] 1.1 各计费模块为 item 增加 `row_total`（行总价 = 单价×数量×次数）
  - [ ] `vehicle.py` / `attraction.py` / `hotel.py` / `meal.py` / `guide.py` / `other_fees.py`
  - [ ] `generate.py` 计算 `grand_total = Σrow_total`，校验 `grand_total ≈ quote_total`（容差说明）
- [ ] 1.2 新增人群分摊工具 `scripts/audience_split.py`
  - [ ] `split_by_headcount(row_total, adults, children, students, elders)`
  - [ ] `split_ticket_item(ticket_type, row_total)`（门票精确归属）
  - [ ] 各计费模块填 `audience_split`
- [ ] 1.3 item 增加 `group` 字段（`scripts/category_groups.py` 默认 category→group 映射）
- [ ] 1.4 `generate.py` 聚合并输出新顶层字段到 `internal_data`
  - [ ] `category_subtotals` / `group_subtotals` / `audience_totals` / `audience_per_capita` / `pax_summary` / `customer_name` / `grand_total`
- [ ] 1.5 单测 `tests/unit/tools/test_travel_quote_audience_split.py`
  - [ ] 门票精确归属 / 人头比例守恒 / 多人群人均 / 边界（0 人群、单人团）
- [ ] 1.6 回归：既有默认样例完整报价，`subtotal`/`quote_per_person`/`quote_total` 不变
- [ ] **验证**：`./scripts/dev_test.sh tests/unit/tools/test_travel_quote_audience_split.py -p no:cacheprovider -q` 全绿 + 默认样例回归通过

## Phase 2 — 消费通用工具填充（退役 excel_export.py）

> 依赖：通用工具 [plan-excel-template-ai.md](plan-excel-template-ai.md) Phase A（`fill_with_sample`）完成。

- [ ] 2.1 QuoteData → 通用 data 适配器 `scripts/template_adapter.py`
  - [ ] `adapt_to_generic(quote_data)` → meta/rows/group_subtotals/totals
  - [ ] rows 绑 `row_total`（行总价口径）；可附 `columns` 显式钉列绑定
  - [ ] 单测：适配器输出键与通用工具 data 契约一致
- [ ] 2.2 `generate.py` 改调通用工具
  - [ ] 接 `sample_file_path` 参数（不传用自带 `templates/default.xlsx` 默认样例）
  - [ ] `fill_with_sample(sample_path, data, output_name, tenant_id)` → file_path
  - [ ] 验证 skill 脚本 import `src.tools.excel.excel_template_ai` 不触发 master_agent 单例
- [ ] 2.3 退役 `scripts/excel_export.py`
  - [ ] 删除该文件（渲染逻辑已被通用工具取代）
  - [ ] `templates/default.xlsx` 保留，改作"自带默认样例"（不再是占位符模板）
- [ ] 2.4 SKILL.md 更新
  - [ ] 输入参数加 `sample_file_path`（客户样例，不传用默认样例）
  - [ ] 输出契约不变（LLM 无感）
- [ ] 2.5 回归：用自带默认样例跑完整报价，关键字段与 P1 后一致；输出不含样例残留数据
- [ ] **验证**：`generate.py` 端到端产出 xlsx + 适配器单测 + 默认样例回归 + import 终检 `python -c "from src.skills.travel_quote.scripts.generate import generate_quote"`

## Phase 3 — travel-consultant 聊天接入（持有样例）

> 依赖：通用工具 Phase A 完成。**全程无入库**——agent 持有样例路径。

- [ ] 3.1 travel-consultant 子智能体识别用户消息带报价单样例附件
  - [ ] 记住样例 `file_id`/路径（对话上下文或会话存储，**调用方职责**）
  - [ ] 后续调 travel-quote 时带上 `sample_file_path`
- [ ] 3.2 travel-consultant 的 SUBAGENT.md / 提示词补充："客户给过样例就带上 sample_file_path；没给过用默认"
- [ ] 3.3 端到端：用户发样例 → agent 记住 → 要报价（带样例）→ 通用工具按样例版式填充输出
- [ ] **验证**：端到端流程通过 + 不带样例走默认样例回归

## Phase 4 — 领域价格元素补全

> 依赖：P1。各子项可独立插队。

### 4a. 大交通
- [ ] 4.1 DB 表 `bs_travel_quote_transports`（`deploy/init-postgres.sql` + `deploy/db_update.sql`）
- [ ] 4.2 `scripts/transport.py` 计费模块（复用 attraction.py 范式）
- [ ] 4.3 `itinerary_parser.py` 增加"大交通段"提取
- [ ] 4.4 `generate.py` 接入 transport，item `category="大交通"`、`group="大交通"`
- [ ] 4.5 单测 + 知识库交通价格表样例

### 4b. 其他零项（扩展 other_fees）
- [ ] 4.6 `bs_travel_quote_fees.fee_category` 扩展取值：visa / tips / equipment / tax / complimentary / extra_bed
- [ ] 4.7 `other_fees.py` 支持新计费方式（tips=per_person_per_day、tax=per_trip、complimentary 单价 0）
- [ ] 4.8 `generate.py` 价格为 0 过滤对 `complimentary` 豁免
- [ ] 4.9 单测覆盖每种 fee_category

### 4c. 人群差异化（可延后）
- [ ] 4.10 `bs_travel_quote_meals` 增加 `child_price`，`meal.py` 按实际餐标分摊
- [ ] 4.11 住宿"儿童不占床"策略，`calculate_hotel_stays` 调整 `audience_split`
- [ ] 4.12 单测：儿童不占床 / 儿童餐标差的人均结果

- [ ] **验证**：大交通端到端 + 零项各 category 单测全绿 + 既有报价回归

## 提交前终检（每个 Phase）

- [ ] 后端 import 终检：`python -c "from src.skills.travel_quote.scripts.<module> import <symbol>"`
- [ ] pytest 全绿（`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`）
- [ ] 默认样例回归：生成报价，关键字段与改造前一致（除新增字段）；输出无样例残留
- [ ] 设计文档与本计划同步；`docs/ideas.md` 状态更新（部分完成→🔧，全部完成→移 ideas_finished.md ✅）
