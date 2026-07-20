# 旅游报价 - 数据补全与模板工具消费设计

> **关联开发计划**：[plan-travel-quote-template-engine.md](../plans/plan-travel-quote-template-engine.md)
> **登记**：[docs/ideas.md](../ideas.md)「数字员工 / 子智能体」分区「旅游报价数据补全」(#43)
> **依赖的通用工具**：[Excel 智能模板填充工具](../tools/excel/excel-template-ai-design.md)（无状态 `fill_template(样例, data)`，由该工具提供）
> **关联既有设计**：[酒店局部替换](design-travel-quote-hotel-swap.md) / [价格解析性能](design-travel-quote-price-parser-performance.md) / [Prompt 稳定性](design-travel-quote-prompt-stability.md)

## 背景

`travel-consultant` 子智能体调 `travel-quote` 技能（`src/skills/travel-quote/`）生成 Excel 报价单。两个待解问题：

1. **每个客户/租户的报价单版式不同**：客户在聊天里发来样例，系统要能"按样例版式生成报价"。
2. **报价内容项不全，无法支撑任意版式**：当前只有单一 `人均报价`（无成人/儿童人均分列）、只能平铺明细行（无分区汇总行）。

**架构决策**：模板填充是**领域无关的通用能力**，已下沉为通用 Excel 工具的一个无状态 action（[excel-template-ai-design.md](../tools/excel/excel-template-ai-design.md)）。本设计只管 **travel-quote 的领域职责**：

- 算出**领域完整、结构化**的 `QuoteData`（车/住/餐/门票/导游/服务费/大交通 + 行总价 + 人群拆分 + 分组）
- 把 `QuoteData` **适配成通用工具的 data 契约**，连同客户样例一起调通用工具填充
- **不再自己实现模板渲染**（退役 `scripts/excel_export.py`）
- **持有客户样例的文件路径**（调用方职责，见 Phase 3）

## 非目标

- 不实现模板分析 / 渲染（那是通用 Excel 工具的职责，无状态、每次传样例）。
- 不建模板注册中心 / DB 表 / template_id 解析（通用工具无状态，样例由调用方传）。
- 不改既有计费模块的业务规则，只加字段 + 新增大交通模块。
- 不做 PDF/Word 报价单。

## 现状分析

### 计费与数据模型（`scripts/generate.py` + 各计费模块）

当前 item 的 `subtotal` 是**人均口径**（行总价 ÷ 总人数）。人群拆分只有**门票模块**做了真正的按票种拆分；住宿/餐饮/用车/导游/其他为人均均摊 → 算不出"成人人均 ≠ 儿童人均"。

### 模板渲染（`scripts/excel_export.py`）

当前自实现的 `{{#items}}...{{/items}}` 占位符引擎，不支持分区汇总行、多人群人均、行总价口径，也不处理行数不匹配。这套逻辑将被通用工具取代并退役。

## 总体架构（消费者视角）

```
┌─────────────────────────────────────────────────────────────────┐
│  travel-consultant 子智能体（聊天，持有样例路径）                    │
│   - 客户发样例 → 记住样例 file_id/路径（调用方职责）                 │
│   - 客户要报价 → 调 travel-quote，带上 sample_file_path             │
└──────────┬──────────────────────────────────┬───────────────────┘
           │                                  │ 报价 + sample_file_path
           ▼                                  ▼
┌─────────────────────┐               ┌──────────────────────────────┐
│  通用 Excel 工具      │◀──────────────│ travel-quote 技能（本设计）    │
│  (src/tools/excel/)  │  fill_with_    │  行程解析→检索→计费            │
│  无状态 fill:         │  sample(样例,  │  → QuoteData（领域完整）       │
│  样例 + data → Excel  │   data)        │  → 适配通用 data              │
└─────────────────────┘               └──────────────────────────────┘
```

travel-quote 与通用工具的集成：**generate.py 直接 import 通用工具的 `fill_with_sample`**（样例路径 + 适配后的 data），不走 agent 中转——QuoteData 不经 LLM 上下文，省 token 省延迟。

> 通用工具**无状态**：每次调用都传样例。客户样例的"持久化/下次复用"由 travel-consultant agent 持有样例 file_id（在对话上下文或会话存储里），每次报价带上——不是工具或 travel-quote 的注册中心职责。

## Phase 1：报价数据模型补全

**目标**：`generate.py` 产出领域完整的 `QuoteData`。纯加字段，不改既有 LLM 契约。

### 1.1 item 结构增强（加字段，`subtotal` 语义不变）

```python
item = {
  # 既有（不动）
  "category": "住宿", "name": "...", "unit_price": 680,
  "quantity": 2, "unit": "间", "frequency": 1, "freq_unit": "",
  "subtotal": 272.0,            # 人均（保持，向后兼容）
  "teacher_subtotal": 0, "remark": "...",

  # 新增
  "row_total": 1360.0,          # 行总价 = 单价 × 数量 × 次数
  "audience_split": {           # 行总价按人群分摊（Σ = row_total）
    "adult": 816.0, "child": 544.0, "student": 0, "elder": 0,
  },
  "group": "房餐车",            # 建议分组键（category 经默认映射，结构分析可覆盖）
}
```

> `subtotal` 保持人均口径：既有默认模板、`generate.py` 汇总、SKILL.md 对 LLM 的契约都依赖它。新版式的"费用小计"列绑 `row_total`。

### 1.2 人群分摊规则（`audience_split`）

| category | 规则 |
|---|---|
| 门票/项目 | **精确**（已按票种分行） |
| 住宿 / 餐饮 / 用车 / 导游 / 其他 / 大交通 | 按人头比例 `row_total × audience_count / total_people` |

### 1.3 QuoteData 聚合（新增顶层字段，放入 `internal_data`）

```python
{
  # 既有（不动）
  "items": [...], "quote_per_person": ..., "quote_total": ...,
  # 新增
  "grand_total": ..., "category_subtotals": {...}, "group_subtotals": {...},
  "audience_totals": {...}, "audience_per_capita": {...},
  "pax_summary": "2成人,3儿童", "customer_name": "",
}
```

### 1.4 不改 LLM 契约

SKILL.md 输出契约（`rows`/`合计_*`/`人均报价`/`总价`/`internal_data`）不变，新字段放 `internal_data`。

## Phase 2：消费通用工具填充（退役 excel_export.py）

**目标**：travel-quote 不再自己渲染，改为"适配 data + 带样例调通用工具"。

### 2.1 QuoteData → 通用 data 适配器 `scripts/template_adapter.py`

```python
def adapt_to_generic(quote_data) -> dict:
    return {
      "meta": {
        "company_name": quote_data["company_name"],
        "customer_name": quote_data.get("customer_name", ""),
        "date": quote_data["start_date"],
        "pax_summary": quote_data["pax_summary"],
      },
      "rows": [
        { "category": it["category"], "name": it["name"],
          "unit_price": it["unit_price"], "quantity": it["quantity"],
          "unit": it["unit"], "amount": it["row_total"],   # 费用小计绑行总价
          "remark": it["remark"] }
        for it in quote_data["items"]
      ],
      "group_subtotals": quote_data["group_subtotals"],
      "totals": {
        "grand_total": quote_data["grand_total"],
        "per_capita": quote_data["audience_per_capita"],
      },
    }
```

> 列绑定由通用工具的结构分析从样例表头 ↔ rows 键推断；旅游报价 schema 已知稳定，可按需在 data 里附 `columns` 显式钉死（见工具设计"数据契约"）。

### 2.2 generate.py 改调通用工具

```python
# 既有
# from excel_export import export_with_template
# file_path = export_with_template(internal_data, template_path)

# 改为
from src.tools.excel.excel_template_ai import fill_with_sample

sample_path = params.get("sample_file_path") or str(SKILL_DIR / "templates" / "default.xlsx")
data = adapt_to_generic(internal_data)
result = fill_with_sample(sample_path, data,
                          output_name=params.get("output_name"),
                          tenant_id=tenant_id)
file_path = result["file_path"]
```

**参数约定**：

| 参数 | 必填 | 说明 |
|---|---|---|
| `sample_file_path` | ❌（不传用自带默认样例） | 客户样例路径，由 travel-consultant agent 持有并传入；不传则用 travel-quote 自带的 `templates/default.xlsx` 作为默认样例 |

> 无 template_id、无 registry、无默认值解析。默认样例就是 travel-quote 自带的一份样例文件，走同一条 `fill_with_sample` 路径。

### 2.3 退役 `scripts/excel_export.py`

- 渲染逻辑被通用工具取代，删除该文件。
- `templates/default.xlsx` 保留，改作"自带默认样例"（不再是占位符模板），由通用工具分析填充。
- `create_default_template.py` 可保留（生成默认样例用）或简化。

### 2.4 SKILL.md 更新

- 输入参数增加 `sample_file_path`（客户样例；不传用默认样例）。
- 输出契约不变（仍返回 `rows`/合计/file_path），LLM 无感。

## Phase 3：travel-consultant 聊天接入（持有样例）

**目标**：客户发样例 → agent 记住 → 后续报价带上样例 → 通用工具填充。**全程无入库**。

- [ ] travel-consultant 子智能体识别用户消息带报价单样例附件 → 记住样例的 `file_id`/路径（存在对话上下文或会话存储里，**调用方职责**）。
- [ ] 后续要报价时，agent 调 travel-quote 并带上 `sample_file_path`（即记住的样例路径）。
- [ ] travel-quote 的 `generate.py` 把 `sample_file_path` 透传给通用工具 `fill_with_sample`。
- [ ] 子智能体确认话术："已记录您提供的报价单版式，本次报价将按此版式生成。"

> 没有独立"分析入库"步骤。通用工具在每次 fill 时做数据感知的结构分析（样例 + 本次 data）。agent 只负责"记住用哪个样例"。

### 默认样例兜底

客户没提供样例时，travel-quote 用自带 `templates/default.xlsx` 默认样例，保证开箱即用。

## Phase 4：领域价格元素补全

**目标**：覆盖旅游领域全部价格构成，确保"任何版式都能填满"。

### 4.1 大交通（机票/高铁/火车/轮渡）— 最大缺口

- 新增 `scripts/transport.py` 计费模块 + DB 表 `bs_travel_quote_transports`。
- `itinerary_parser.py` 增加"大交通段"提取。
- 计费按人群票种，产出 item `category="大交通"`、`group="大交通"`。
- LLM 从知识库交通价格表提取（复用 attraction.py 范式）。

### 4.2 其他零项（扩展 `other_fees.py` 配置，仅扩数据不改表结构）

| 元素 | fee_category | 计费方式 |
|---|---|---|
| 签证 | visa | per_person |
| 小费 | tips | per_person_per_day |
| 装备租赁 | equipment | per_person / per_trip |
| 税费 | tax | per_trip |
| 赠送项 | complimentary | per_trip（单价 0，仅展示） |
| 加床 | extra_bed | per_person_per_day |

> 赠送项单价 0 需展示，`generate.py` 的"过滤价格为 0"逻辑对 `complimentary` 豁免。

### 4.3 餐饮/住宿的人群差异化（精细化，可延后）

- 餐饮：`bs_travel_quote_meals` 增加 `child_price`，按实际餐标分摊。
- 住宿：引入"儿童不占床"策略，`calculate_hotel_stays` 调整 `audience_split`。

## 与通用 Excel 工具的边界

| 职责 | 归属 |
|---|---|
| 行程解析 / 资源检索 / 计费 / QuoteData | **travel-quote（本设计）** |
| QuoteData → 通用 data 适配 | **travel-quote（Phase 2）** |
| 样例结构分析 / 渲染 / 行数不匹配 / 不变式校验 | **通用 Excel 工具** |
| 持有客户样例路径（跨轮复用） | **travel-consultant agent（调用方）** |
| `generate.py` 透传 `sample_file_path` + 调 `fill_with_sample` | travel-quote 调用通用工具库 |

## 分阶段实施总览

| Phase | 目标 | 依赖 | 风险 |
|---|---|---|---|
| **P1 数据模型** | item 加 `row_total`/`audience_split`/`group`；QuoteData 加聚合 | 无 | 低（纯加字段） |
| **P2 消费集成** | 适配器 + generate.py 带 sample 调 `fill_with_sample` + 退役 excel_export.py | 通用工具 Phase A 完成 | 低（集成） |
| **P3 聊天接入** | agent 持有样例 → 报价带上 → 填充 | 通用工具 Phase A 完成 | 中（端到端） |
| **P4 领域补全** | 大交通 + 零项 + 人群差异化 | P1 | 中（知识库数据） |

> P1 可立即开始；P2/P3 等通用工具 Phase A 就绪后接入；P4 可插队。

## 风险与取舍

| 风险 | 缓解 |
|---|---|
| **退役 excel_export.py 破坏既有报价** | P2 用自带默认样例跑全量回归；通用工具保证输出不残留样例数据 |
| **`subtotal` 双口径混淆** | 文档明确；适配器 rows 绑 `row_total`（行总价） |
| **QuoteData 键与样例列对不齐** | 通用工具 `unmatched_columns` 警告回传；data 可附 `columns` 显式钉死 |
| **agent 忘记带样例路径** | 不传时用自带默认样例兜底；agent 提示词明确"客户给过样例就带上" |
| **既有 LLM 契约被破坏** | P1/P2 不改 `rows`/`合计_*`/`人均报价`/`总价`；新字段放 `internal_data` |
| **`update_hotel.py` 兼容** | 它只读 `items`/`hotel_stays`，新字段透明保留，不受影响 |
| **skill 脚本 import src.tools 副作用** | Phase 2 开工时验证 `from src.tools.excel.excel_template_ai import fill_with_sample` 不触发 master_agent 单例（符合 backend_dev 包初始化副作用规范）|
