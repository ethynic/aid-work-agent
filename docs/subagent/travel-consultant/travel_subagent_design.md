# 旅游子智能体设计文档

> 版本: v7.0 | 创建: 2026-05-09 | 最后更新: 2026-05-18 | 状态: 已完成
>
> v7.0 变更：重写第 5-6 节对齐实际实现（行程文本驱动模式、景点 3 chunk 结构、teacher_subtotal）

## 1. 设计目标

设计一套**通用的旅游行业 AI 顾问系统**，核心思路：

1. **能在 SUBAGENT.md 里说清楚的，就不新建 skill** — 行程规划、对话风格、路线策略都由 LLM 在子智能体说明中直接执行
2. **只有需要精确程序化操作的才用 skill** — 报价生成（查库+计算+导出 Excel）封装为 `quote-generate` skill，仅注册给旅游子智能体
3. **租户定制统一用 extra.md** — 每个子智能体每个租户一个 extra.md 文件，包含该租户的个性化配置（对话风格、路线策略、模板路径等），作为 system prompt 的一部分注入，优先级高于 SUBAGENT.md
4. **定价数据分类存储** — 酒店/景点用向量知识库（语义匹配），车辆用关系型表（算法推荐），详见 [旅游资源定价数据方案](../../subagent/travel-consultant/travel_pricing_resource_design.md)

可配置模块：

| 模块 | 数据存储 | 运行方式 |
|------|---------|---------|
| 定价数据（酒店/景点） | 向量知识库（documents + chunks + chunks_vec） | 向量搜索 + LLM 取价；景点 3 个 chunk（信息/门票/项目），酒店 2 个 chunk（信息/价格） |
| 定价数据（车辆） | `bs_travel_quote_vehicles` 关系型表 | 算法推荐 + 按天/按公里计费（支持多段导航距离） |
| 定价数据（餐标/导游/其他费用） | `bs_travel_quote_*` 关系型表 | `quote-generate` skill 内部查询 |
| 报价单模板路径 | extra.md 中配置 | skill 读取路径对应的模板文件 |
| 路线规划策略 | extra.md 中用 Markdown 描述 | LLM 按 prompt 中的策略规则规划 |
| 拟人化对话风格 | extra.md 中用 Markdown 描述 | LLM 按 prompt 中的人设和风格回复 |

---

## 2. 费用构成总览

```
旅游团总报价
├── 1. 交通费用（包车/大交通）
├── 2. 景点门票
├── 3. 住宿费用
├── 4. 餐饮费用
├── 5. 导游/领队费用
├── 6. 保险费用
├── 7. 其他费用（景区交通/索道、综合服务费、司机餐宿等）
└── 8. 利润/加价
```

---

## 3. 定价数据模型

> **本节已迁移至独立设计文档**：[旅游资源定价数据方案](../../subagent/travel-consultant/travel_pricing_resource_design.md)
>
> 该文档涵盖以下内容：
>
> | 内容 | 说明 |
> |------|------|
> | 第一部分：酒店资源 | 向量知识库方案（替代原 `bs_travel_quote_hotels` + `bs_travel_quote_rooms`） |
> | 第二部分：景点门票资源 | 向量知识库方案（替代原 `bs_travel_quote_attractions` + `bs_travel_quote_tickets`） |
> | 第三部分：车辆资源 | 关系型表扩展（保留 `bs_travel_quote_vehicles`，新增按公里计费模式） |
> | 导航距离计算 Skill | 基于高德地图 API 的导航距离计算，详见 [route_distance_skill_design.md](../../subagent/travel-consultant/route_distance_skill_design.md) |
>
> **仍在关系型表中的数据**（区域匹配逻辑继续适用）：
> - `bs_travel_quote_vehicles` — 车辆（扩展，新增按公里计费）
> - `bs_travel_quote_meals` — 餐标
> - `bs_travel_quote_guides` — 导游
> - `bs_travel_quote_fees` — 其他费用
> - `bs_travel_quote_seasons` — 淡旺季
> - `bs_travel_quote_regions` — 区域分类

### 3.1 区域匹配逻辑（仍适用）

关系型表的区域匹配逻辑不变：

```python
def query_by_region(table, region_name=None):
    """查询定价数据，优先匹配区域，无匹配则取全国通用"""
    if region_name:
        rows = db.query(f"SELECT * FROM {table} WHERE tenant_id=? AND is_active=true "
                        f"AND (region_name=? OR region_name IS NULL)", tenant_id, region_name)
    else:
        rows = db.query(f"SELECT * FROM {table} WHERE tenant_id=? AND is_active=true "
                        f"AND region_name IS NULL", tenant_id)
    return rows
```

### 3.2 总体数据架构（已更新）

```
bs_travel_quote_regions (区域/城市 — 独立分类表)

【向量知识库】                               【关系型表】
documents + chunks + chunks_vec              bs_travel_quote_vehicles (车辆，扩展)
  ├── source_type='hotel_resource'           bs_travel_quote_meals (餐标)
  └── source_type='attraction_resource'      bs_travel_quote_guides (导游)
                                             bs_travel_quote_fees (其他费用)
                                             bs_travel_quote_seasons (淡旺季)
```

### 3.3 利润/加价配置

利润/加价配置写入 extra.md（见第 7 节），不需要数据库表。
## 4. 报价单模板系统

### 4.1 设计思路

报价单模板就是一个 Excel 文件，模板文件路径写在 extra.md 中。没有配置模板路径时使用系统内置默认模板。

- **不需要数据库建模板表**
- **不需要字段映射配置** — 变量名与 `QuoteResult`/`QuoteItem` 字段名固定绑定
- **只支持 Excel 模板** — `.xlsx` 格式，用 `{{变量名}}` 作为占位符

### 4.2 模板变量名约定（用户配置指南）

模板中的变量名与 `QuoteResult` / `QuoteItem`（第 5.3 节）的字段名一一对应，用户按此表在 Excel 中填写占位符：

**汇总信息区**（替换文本，可出现在任意单元格）：

| 变量名 | 含义 | 示例值 |
|--------|------|--------|
| `{{course_name}}` | 行程/课程名称 | 超级贵州研学 |
| `{{company_name}}` | 公司名称 | 贵州天悦旅行社 |
| `{{region_name}}` | 目的地区域 | 贵阳 |
| `{{start_date}}` | 出发日期 | 2026-07-01 |
| `{{trip_days}}` | 行程天数 | 6 |
| `{{total_people}}` | 总人数 | 30 |
| `{{total_cost}}` | 整团成本 | 58000.00 |
| `{{cost_per_person}}` | 人均成本 | 1933.33 |
| `{{quote_per_person}}` | 人均报价 | 2223.33 |
| `{{quote_total}}` | 整团报价 | 66700.00 |
| `{{single_supplement}}` | 单房差 | 320.00 |

**数据行区**（每条报价项循环输出）：

| 变量名 | 含义 | 示例值 |
|--------|------|--------|
| `{{category}}` | 成本类别 | 门票 |
| `{{name}}` | 项目名称 | 天眼观景台(成人) |
| `{{unit_price}}` | 单价 | 88.00 |
| `{{quantity}}` | 数量 | 25 |
| `{{unit}}` | 数量单位 | 人 |
| `{{frequency}}` | 次数 | 1 |
| `{{freq_unit}}` | 次数单位 | 次 |
| `{{subtotal}}` | 费用小计（人均） | 73.33 |
| `{{remark}}` | 备注 | 协议价 |

**数据行标记**：

| 标记 | 含义 |
|------|------|
| `{{#items}}` | 数据行起始标记（写在数据行第一行的任意单元格） |
| `{{/items}}` | 数据行结束标记（写在数据行最后一行的任意单元格） |

脚本遇到 `{{#items}}` 到 `{{/items}}` 之间的行时，按报价项数量复制该行并填充变量。标记行本身不输出。

**模板示例**：

```
┌────────────────────────────────────────────────────────────────────────────┐
│  {{company_name}}研学报价表                                                 │
│  课程名称：{{course_name}}    日期：{{start_date}}    人数：{{total_people}}  │
│                                                                            │
│  成本类别 │ 项目 │ 单价 │ 数量 │ 单位 │ 次数 │ 单位 │ 费用小计 │ 备注     │
│ ────────────────────────────────────────────────────────────────────────── │
│  {{#items}}                                                                │
│  {{category}} │ {{name}} │ {{unit_price}} │ {{quantity}} │ {{unit}} │      │
│               {{frequency}} │ {{freq_unit}} │ {{subtotal}} │ {{remark}}    │
│  {{/items}}                                                                │
│ ────────────────────────────────────────────────────────────────────────── │
│  合计 │                                     │ {{quote_total}}              │
│                                                                            │
│  审核签字：________  成本审核员：________  研学负责人：________              │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 导出流程

```python
def export_with_template(quote_data: dict, template_path: str) -> str:
    dst = tempfile.mktemp(suffix='.xlsx')
    shutil.copy2(template_path, dst)
    wb = openpyxl.load_workbook(dst)
    ws = wb.active

    # 1. 扫描找到 {{#items}} 和 {{/items}} 标记行的位置
    items_start_row, items_end_row = find_items_markers(ws)

    # 2. 提取数据行模板（标记之间的行）
    template_rows = extract_template_rows(ws, items_start_row, items_end_row)

    # 3. 删除标记行，插入实际数据行
    insert_data_rows(ws, template_rows, quote_data['items'], items_start_row)

    # 4. 替换所有 {{变量名}}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and '{{' in str(cell.value):
                cell.value = replace_placeholders(cell.value, quote_data)

    wb.save(dst)
    return dst
```

系统内置默认模板文件 `src/skills/quote-generate/templates/default.xlsx`。

---

## 5. 报价计算流程

> **报价计算的详细设计（酒店/景点取价、车辆计价模式）已迁移至**：[旅游资源定价数据方案](../../subagent/travel-consultant/travel_pricing_resource_design.md)
>
> 以下保留报价输入参数和输出结构的概要定义，供 SUBAGENT.md 引用。

### 5.1 输入参数

> 重构后的 `quote-generate` 技能支持**行程文本驱动**模式，LLM 子智能体只需传入用户确认的行程方案全文，技能内部完成行程解析、资源检索、计价、导出全流程。

**新模式（推荐）**：传入 `itinerary_text`，技能内部自动解析：

```python
{
    "tenant_id": "租户ID（必填）",
    "itinerary_text": "用户确认的行程方案全文（必填，Markdown 表格格式）",
    "start_date": "2026-07-01",
    "profit_rate": 0.15,
    "course_name": "超级贵州研学",
    "company_name": "贵州天悦旅行社",
    "template_path": None  # 可选，自定义 Excel 模板路径
}
```

**旧模式（向后兼容）**：不传 `itinerary_text` 而传入结构化参数时走旧逻辑，参数包括 `region_name`、`total_people`、`adults`、`students`、`children_half`、`elders`、`couples`、`trip_days`、`attraction_ids`、`hotel_id`、`attraction_matches`、`hotel_stays`、`meal_tier`、`guide_type`、`vehicle_count`、`include_insurance`、`departure_city`、`destination` 等。

### 5.2 计算流程

**新模式完整流程**：

```
Step 0: 行程解析 → parse_itinerary(itinerary_text) → LLM 提取结构化数据
Step 0.5: 资源检索 → resolve_resources(parsed, tenant_id) → 景点/酒店向量搜索匹配
Step 1: 区域名称
Step 2: 确定季节 → bs_travel_quote_seasons
Step 3: 导航距离 → calculate_multi_leg_distance / calculate_single_leg_distance（高德地图 API）
Step 4: 交通 → bs_travel_quote_vehicles → 按天/按公里计费，支持多段距离
Step 5: 景点门票 + 游玩项目 → calculate_attraction_cost()（统一入口）
         ├─ 门票：LLM 从 chunk 1 提取 → _build_ticket_items（含去重和团队构成 fallback）
         └─ 项目：LLM 从 chunk 2 匹配行程活动 → _build_project_items（按人/按团计费）
Step 6: 住宿 → calculate_hotel_stays()（多城市）或 calculate_hotel_cost()（单酒店）
         → 向量搜索选酒店 → LLM/规则取价 → 排房 + 单房差
Step 7: 餐饮 → bs_travel_quote_meals → 餐标 × 人数 × 餐数
Step 8: 导游 → bs_travel_quote_guides → 日薪 × 天数 × 旺季倍率
Step 9: 其他费用 → bs_travel_quote_fees → 保险、综合服务费等
Step 10: 过滤价格为 0 的项目 → 汇总 → 人均成本 × (1 + 利润率) → 导出 Excel
```

### 5.3 输出结构

```python
class QuoteResult:
    course_name: str             # 行程名称
    company_name: str            # 公司名称
    region_name: str
    start_date: date
    trip_days: int
    total_people: int
    teacher_count: int           # 随队老师人数
    items: list[QuoteItem]
    price_per_person: decimal    # 人均报价（含利润）
    total_price: decimal         # 整团报价
    teacher_total: decimal       # 随队老师总费用

class QuoteItem:
    category: str                # 成本类别：用车/门票·项目/住宿/餐饮/导游/其他费用
    name: str                    # 项目名称
    unit_price: decimal          # 单价
    quantity: int                # 数量
    unit: str                    # 数量单位：人/辆/间/团
    frequency: int               # 次数
    freq_unit: str               # 次数单位：天/次/夜
    subtotal: decimal            # 费用小计（学生人均）
    teacher_subtotal: decimal    # 随队老师费用小计（0 表示老师不额外付费）
    remark: str
```

> `QuoteResult` 和 `QuoteItem` 的字段名即模板变量名（见第 4.3 节），一一对应，无需额外映射。
## 6. 子智能体功能与 Skill 架构

### 6.1 核心原则

| 功能 | 实现方式 | 原因 |
|------|---------|------|
| 需求咨询 | SUBAGENT.md 中的对话阶段和沟通技巧 | LLM 擅长自然对话 |
| 行程规划 | SUBAGENT.md 中的路线规划策略 + 知识库检索 | LLM 做规划和推荐 |
| 报价生成（查询+计算+导出） | **quote-generate skill**（全流程 skill） | 查库+计算+Excel 需要程序精确处理，作为整体封装 |
| 知识库检索 | knowledge_base_search 工具 | 已有工具 |

### 6.2 为什么用 Skill 而不是 Tool

Tool（工具）是全局通用的，注册在 `ToolRegistry` 中，所有智能体都能调用。但旅游报价查询和计算是**旅游子智能体专属的业务逻辑**——其他智能体（如外贸、合同审核）用不到 `bs_travel_quote_*` 这些表。

因此，报价的完整流程（查询定价数据 → 组装报价项 → 计算费用 → 生成 Excel）封装为一个 **skill**，仅注册给旅游子智能体。LLM 只需调用一次 `skill_execute`，传入行程参数，skill 内部的 Python 脚本完成所有工作。

### 6.3 Skill 清单

旅游子智能体只有一个专属 skill：

```
travel-consultant (子智能体)
  ├── quote-generate (报价生成 — 唯一专属 skill)
  │     ├── 接收行程参数（人数、天数、景点列表、酒店、餐标等）
  │     ├── 查询 bs_travel_quote_* 定价数据表
  │     ├── 组装报价项 + 计算各项费用
  │     ├── 读取 extra.md 中的模板路径 → 加载模板文件填充数据
  │     └── 输出 xlsx 文件 + 返回报价明细
  │
  └── 通用能力（不依赖 skill）
        ├── knowledge_base_search — 知识库检索
        └── LLM 直接执行 — 需求咨询、行程规划、对话
```

**移除的 skill**：`trip-planner`（规划策略写入 SUBAGENT.md）、`quote-generator`（合并进 quote-generate）、`quote-export`（合并进 quote-generate）

### 6.4 quote-generate Skill 内部流程

脚本拆分为 14 个独立模块，职责清晰：

```
src/skills/quote-generate/scripts/
├── generate.py           # 主流程编排（入口）
├── itinerary_parser.py   # LLM 行程文本解析
├── resource_resolver.py  # 景点/酒店向量检索
├── attraction.py         # 门票 + 游玩项目费用计算
├── attraction_retriever.py  # 景点向量检索器
├── hotel.py              # 住宿费用计算（支持多城市）
├── hotel_retriever.py    # 酒店向量检索器
├── vehicle.py            # 交通费用计算（按天/按公里 + 多段距离）
├── meal.py               # 餐饮费用计算
├── guide.py              # 导游费用计算
├── other_fees.py         # 其他费用（保险、综合服务费等）
├── season.py             # 淡旺季判定
├── db.py                 # 数据库表初始化 + 查询
├── excel_export.py       # Excel 模板导出
└── llm_client.py         # LLM 调用封装
```

**主流程**：

```
generate.py generate_quote(params)
    │
    ├─ if itinerary_text:
    │    parse_itinerary(text)          → LLM 解析行程文本 → 结构化数据
    │    resolve_resources(parsed)      → 向量检索匹配景点/酒店
    │    _validate_headcount()          → 人数校验与修正
    │    _calculate_route_distance()    → 多段导航距离计算（高德 API）
    │
    ├─ determine_season()              → 淡旺季判定
    ├─ calculate_vehicle_cost()        → 交通（按天/按公里）
    ├─ calculate_attraction_cost()     → 门票 + 游玩项目（LLM 提取 + 去重）
    ├─ calculate_hotel_stays/cost()    → 住宿（多城市/单酒店）
    ├─ calculate_meal_cost()           → 餐饮
    ├─ calculate_guide_cost()          → 导游
    ├─ calculate_other_fees()          → 其他费用
    │
    ├─ 过滤价格为 0 的项目
    ├─ 汇总（含 teacher_total）
    └─ export_with_template()          → Excel 导出
```

### 6.5 Skill 输入参数

LLM 通过 `skill_execute` 的 `content` 参数传入 JSON。推荐使用行程文本驱动模式：

```json
{
  "tenant_id": "tenant_xxx",
  "itinerary_text": "## 贵州天眼+荔波6天5晚研学行程\n\n**出发日期**：2026年7月1日\n\n**团队构成**：20名初中生 + 1位带队老师\n\n| 天数 | 时段 | 行程安排 | 游玩项目 | 备注 |\n|------|------|----------|----------|------|\n| D1 | 下午 | 贵阳接站 | — | |\n...",
  "start_date": "2026-07-01",
  "company_name": "贵州天悦旅行社有限公司",
  "course_name": "超级贵州研学",
  "profit_rate": 0.15
}
```

> 子智能体只需传入 `itinerary_text`（用户确认的行程方案全文，Markdown 表格格式），技能内部自动完成行程解析、资源检索、计价、导出。从 20+ 个参数简化到 7 个。

### 6.6 Skill 输出结构

```json
{
  "success": true,
  "data": {
    "course_name": "超级贵州研学",
    "company_name": "贵州天悦旅行社有限公司",
    "region_name": "贵州",
    "start_date": "2026-07-01",
    "trip_days": 6,
    "total_people": 21,
    "teacher_count": 1,
    "items": [
      {"category": "用车", "name": "38座大巴", "unit_price": 2255.85, "quantity": 1, "unit": "辆", "frequency": 1, "freq_unit": "趟", "subtotal": 107.42, "teacher_subtotal": 0, "remark": "38座大巴·38座含司机，含燃油过路费"},
      {"category": "门票/项目", "name": "天文体验馆+天眼摆渡车+南仁东事迹馆(学生票)", "unit_price": 140.0, "quantity": 21, "unit": "人", "frequency": 1, "freq_unit": "次", "subtotal": 140.0, "teacher_subtotal": 140.0, "remark": "挂牌价"},
      {"category": "住宿", "name": "平塘天悦酒店", "unit_price": 268.0, "quantity": 2, "unit": "人", "frequency": 1, "freq_unit": "夜", "subtotal": 134.0, "teacher_subtotal": 134.0, "remark": "平塘1晚"}
    ],
    "price_per_person": 2461.53,
    "total_price": 51692.13,
    "teacher_total": 1964.0,
    "file_path": "/tmp/xxx.xlsx"
  }
}
```

---

## 7. 租户定制系统：extra.md

### 7.1 设计思路

每个子智能体除了官方的 `SUBAGENT.md`，还有按租户隔离的 `extra.md`。extra.md 记录该租户的个性化配置，作为 system prompt 的一部分注入，**优先级高于 SUBAGENT.md**。

**替代方案对比**：

| 之前 | 现在 |
|------|------|
| 策略表 `bs_travel_route_strategies` (JSON → 渲染 Markdown → 缓存) | extra.md 直接写 Markdown |
| 人设表 `bs_travel_persona_configs` (JSON → 渲染 Markdown → 缓存) | extra.md 直接写 Markdown |
| 模板表 `bs_travel_quote_templates` | extra.md 中配一个路径 |
| 渲染发布 API + 发布流程 | 直接改文件，前端编辑器保存即生效 |

**核心优势**：
- 所有租户定制统一一个文件，运维直接看文件就知道定制了什么
- 不需要 JSON → Markdown 渲染逻辑，不需要渲染缓存，不需要发布流程
- 任何子智能体天然支持租户定制，不限旅游
- 前端一个 Markdown 编辑器搞定

### 7.2 文件存储路径

```
storage/subagents/<subagent_name>/extra_<tenant_id>.md
```

示例：
```
storage/subagents/
  travel-consultant/
    extra_tenant_a1b2c3d4.md      ← 租户A的定制
    extra_tenant_f7g8h9i0.md      ← 租户B的定制
  contract-archive-review/
    extra_tenant_a1b2c3d4.md      ← 同一租户，不同子智能体的定制
```

### 7.3 extra.md 内容结构

extra.md 是自由格式的 Markdown，租户可以写任何想定制的内容。以下为旅游子智能体的推荐结构：

```markdown
# 租户定制配置

> 本文件优先级高于 SUBAGENT.md。如果本文件的规则与 SUBAGENT.md 冲突，以本文件为准。

## 身份与对话风格

你是"小旅"——一位在旅行社工作了3年的旅游顾问。
你走过贵州大部分景点，对研学旅行特别有心得。

### 性格特点
- 热情但不夸张，专业但不生硬
- 善于倾听，会从客户的话里捕捉关键信息
- 有主见，会给出明确的建议，不会面面俱到让客户自己选

### 说话方式
- 用短句，不写长段落。每段最多3句话
- 偶尔用"其实"、"说实话"开头，像在思考
- 确认信息时说"了解了"，不说"收到"或"好的"
- 不用感叹号堆砌，想表达热情时用波浪号～

### 绝对禁止
- 不说"作为AI"、"我是人工智能"、"我的算法"
- 不说"很高兴为您服务"、"感谢您的咨询"
- 不用排比句（"我们这里有...有...还有..."）
- 不把对话变成参数收集问卷，每次最多追问2个问题

## 路线规划策略

### 路线形态
- 采用**环线设计**，不走回头路，进出安排在不同方向
- 每天行车时间不超过**3.5小时**
- 长途移动/换城市的当天，最多安排**2个**景点

### 节奏控制
- 每天安排**2个核心景点**
- 行程节奏**均衡**：上午紧凑，下午稍宽松
- 集合时间默认**07:30**，安排**1小时午休**

### 景点筛选
- **必须包含**的景点：天眼、溶洞探秘
- 景点类型要**多样化**，避免连续两天同类型
- **不安排购物点**

### 特殊注意
- 目标客群是**学生研学团**，注意安全性和教育性

## 报价配置

### 利润规则
- 利润率：15%
- 计算方式：人均成本 × (1 + 利润率)
- 取整方式：向上取整

### 报价单模板
- 模板路径：storage/templates/tenant_a1b2c3d4/报价单模板.xlsx
- 未配置模板路径时使用系统默认模板

### 公司名称
- 公司名称：贵州天悦旅行社有限公司
- 联系方式：0851-XXXXXXXX
```

### 7.4 System Prompt 拼接逻辑

```
最终 system_prompt = SUBAGENT.md 内容 + extra.md 内容
```

拼接规则：
1. **SUBAGENT.md 在前，extra.md 在后** — LLM 对后面的内容注意力权重更高
2. **extra.md 开头声明优先级** — 固定加一句"本文件优先级高于 SUBAGENT.md"
3. **extra.md 不存在时跳过** — 未定制的租户只使用 SUBAGENT.md
4. **extra.md 可以为空** — 只有标题行，表示未定制

**需要修改的代码**：

| 文件 | 修改内容 |
|------|---------|
| `src/subagents/loader.py` | 加载子智能体时，检查 `storage/subagents/<name>/extra_<tenant_id>.md` 是否存在 |
| `src/subagents/executor.py` | 拼接 system_prompt 时，追加 extra.md 内容 |
| `src/core/agent.py` | `process_message()` 中传递 tenant_id 到子智能体执行器 |

### 7.5 前端编辑页面

前端为每个子智能体提供一个 Markdown 编辑器，租户管理员可直接编辑 extra.md：

```
┌─────────────────────────────────────────┐
│  数字员工管理 > 旅游顾问 > 租户定制       │
├─────────────────────────────────────────┤
│                                         │
│  ⚠️ 此处配置优先级高于系统默认设置        │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │ # 租户定制配置                  │    │
│  │                                 │    │
│  │ ## 身份与对话风格               │    │
│  │                                 │    │
│  │ 你是"小旅"——一位在旅行社...     │    │
│  │ ...                             │    │
│  │                                 │    │
│  │ ## 路线规划策略                 │    │
│  │ ...                             │    │
│  │                                 │    │
│  │ ## 报价配置                     │    │
│  │ ...                             │    │
│  └─────────────────────────────────┘    │
│                                         │
│  [保存] [恢复默认] [预览效果]            │
└─────────────────────────────────────────┘
```

**API**：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/subagents/<name>/extra` | 获取当前租户的 extra.md 内容 |
| PUT | `/api/v1/subagents/<name>/extra` | 保存 extra.md 内容 |
| DELETE | `/api/v1/subagents/<name>/extra` | 删除 extra.md，恢复默认 |

### 7.6 路线规划策略参考

以下是路线规划的常用配置项，供租户在 extra.md 中参考编写。不需要数据库表，不需要 JSON 格式，直接用自然语言写在 Markdown 里。

**五类策略维度**：

| 维度 | 关注点 | 可配置内容 |
|------|--------|-----------|
| 路线优化 | 怎么走 | 环线/轮辐/线性、每日最大行车时间、不走重复路 |
| 节奏控制 | 走多快 | 每日景点数、集合时间、午休时长、缓冲比例 |
| 景点筛选 | 走哪些 | 必去景点、禁去景点、购物点限制、类型多样化 |
| 资源分配 | 住哪吃啥 | 酒店位置偏好、换酒店频率、排房规则、特色餐次数 |
| 特殊客群 | 照顾谁 | 学生/家庭/老人/VIP、儿童友好、晚间活动、预算敏感度 |

**预置策略预设**（供用户参考）：

| 预设 | 路线 | 节奏 | 客群 | 典型场景 |
|------|------|------|------|---------|
| 省域研学标准 | 环线 | 均衡 | 学生 | 贵州研学6天 |
| 省域研学紧凑 | 环线 | 紧凑 | 学生 | 天数有限的研学 |
| 跨省观光经典 | 环线 | 均衡 | 通用 | 多省联游 |
| 城市周边休闲 | 轮辐 | 悠闲 | 家庭 | 周末2日游 |
| 中老年康养 | 轮辐 | 悠闲 | 老年 | 康养团 |
| VIP深度定制 | 环线 | 悠闲 | VIP | 高端定制 |
| 企业团建活力 | 环线 | 均衡 | 企业 | 团建拓展 |

### 7.7 对话风格参考

以下是常见的对话风格配置，供租户在 extra.md 中参考编写。

**四种风格预设**：

| 风格 | 人设感 | 示例回复 |
|------|--------|---------|
| 亲切专业型 | 靠谱的朋友 | "了解了，30个初中生的话，我比较推荐天眼这条线。" |
| 活泼导游型 | 爱旅行的同龄人 | "嗨～贵州最近超推荐天眼那边，我上个月刚去过，真的震撼。" |
| 资深专家型 | 资深规划师 | "您好。根据团队情况，建议选择天文研学线路。" |
| 邻家朋友型 | 有趣的导游 | "天眼必须安排上！那个望远镜口径500米，走一圈要半小时" |

**风格配置要点**：
- **身份定义**：名字、角色、背景经历
- **性格特点**：3-5条核心性格描述
- **说话方式**：句式习惯、用词偏好、格式要求
- **绝对禁止**：不该说的话、不该用的表达方式

---

## 8. SUBAGENT.md 整体结构

### 8.1 System Prompt 组成

```
最终 system_prompt = SUBAGENT.md (官方) + extra.md (租户定制，优先级更高)
```

- **SUBAGENT.md**：官方定义，所有租户共用，包含通用的对话流程、技能调用说明、行为约束
- **extra.md**：租户定制，包含个性化的人设、风格、策略、报价配置。不存在时只使用 SUBAGENT.md

### 8.2 SUBAGENT.md 结构（官方固定）

```markdown
---
YAML Frontmatter（固定）
  name, version, capabilities, tools, skills: [quote-generate]
---

## 对话阶段管理
  ### 阶段一：了解团队
  ### 阶段二：深入了解需求
  ### 阶段三：方案推荐
  ### 阶段四：报价生成
  ### 阶段五：方案调整
  ### 阶段六：导出报价单

## 沟通技巧
## 报价功能说明
  ### 报价生成流程（什么时候调 quote-generate skill）
  ### quote-generate 参数说明（LLM 需要传入什么）
  ### 报价导出 — skill 返回结果后的处理
## 行为约束
```

> 注意：身份、风格、策略这些租户定制的内容**不写在 SUBAGENT.md 里**，而是写在 extra.md 中。SUBAGENT.md 只放通用的对话流程和功能说明。

### 8.3 extra.md 结构（租户定制）

见第 7.3 节的完整示例。

---

## 9. 数据库与知识库汇总

> 详细的表结构和向量知识库设计见 [旅游资源定价数据方案](../../subagent/travel-consultant/travel_pricing_resource_design.md)

**关系型数据表（6 张，由 quote-generate skill 内部查询）**：

| 表名 | 说明 |
|------|------|
| `bs_travel_quote_regions` | 区域/城市分类 |
| `bs_travel_quote_vehicles` | 车辆价格（扩展：按天/按公里计费，支持多段导航距离） |
| `bs_travel_quote_meals` | 餐标价格 |
| `bs_travel_quote_guides` | 导游费用 |
| `bs_travel_quote_fees` | 其他固定费用 |
| `bs_travel_quote_seasons` | 淡旺季配置 |

**向量知识库**：

| 知识库 source_type | Chunk 结构 | 说明 |
|-------------------|-----------|------|
| `hotel_resource` | Chunk 0（向量化）：酒店信息摘要；Chunk 1：价格明细表 | 替代原 hotels + rooms 表 |
| `attraction_resource` | Chunk 0（向量化）：景点信息摘要；Chunk 1：门票价格表；Chunk 2：游玩项目价格表 | 替代原 attractions + tickets 表 |

**租户定制（文件，不建表）**：

| 文件 | 说明 |
|------|------|
| `storage/subagents/travel-consultant/extra_<tenant_id>.md` | 租户定制配置（对话风格、路线策略、报价模板路径等） |

**专属 Skill**：

| Skill | 说明 |
|-------|------|
| `quote-generate` | 报价全流程：查库 + 向量搜索 → 计算 → 模板导出 Excel |
| `route-distance` | 导航距离计算（高德地图 API），详见 [设计文档](../../subagent/travel-consultant/route_distance_skill_design.md) |

**已删除的表**：
- ~~`bs_travel_quote_templates`~~ — 模板路径写在 extra.md 中
- ~~`bs_travel_quote_route_strategies`~~ — 策略用 Markdown 写在 extra.md 中
- ~~`bs_travel_quote_persona_configs`~~ — 人设用 Markdown 写在 extra.md 中
- ~~`bs_travel_quote_hotels`~~ + ~~`bs_travel_quote_rooms`~~ — 迁移至向量知识库
- ~~`bs_travel_quote_attractions`~~ + ~~`bs_travel_quote_tickets`~~ — 迁移至向量知识库
## 10. 接口设计：谁调用、为什么调用

### 10.1 两个完全不同的接口体系

本设计的接口分为两套，服务于完全不同的调用方：

```
┌─────────────────────────────────────────────────────────┐
│  体系 A：LLM 工具调用（Tool）                             │
│  调用方：大模型（通过 function calling）                    │
│  触发方式：LLM 在对话中根据需要自动调用                      │
│  参数来源：LLM 根据对话上下文自行构造                        │
│  目的：让 LLM 获取定价数据、导出报价单                      │
├─────────────────────────────────────────────────────────┤
│  体系 B：后台管理 REST API                                │
│  调用方：前端管理页面 / 外部系统                            │
│  触发方式：人工操作或外部系统对接                            │
│  参数来源：用户在界面上填写 / 外部系统推送                    │
│  目的：维护定价数据、编辑租户定制(extra.md)                 │
└─────────────────────────────────────────────────────────┘
```

### 10.2 体系 A：LLM 调用 Skill

旅游报价的完整流程（查库 → 计算 → 导出 Excel）封装在 `quote-generate` skill 中。LLM 只需调用一次 `skill_execute`，传入从对话中收集的行程参数，skill 内部的 Python 脚本完成所有工作。

**为什么不用 Tool 而用 Skill**：
- Tool 是全局工具，注册在 `ToolRegistry` 中，所有智能体都能调用
- 旅游报价涉及 `bs_travel_quote_*` 专属业务表，只有旅游子智能体需要
- 把查询+计算+导出封装为一个 skill，LLM 只需一次调用，不用多轮查不同的表再自己算

#### 10.2.1 调用方式

```
LLM 调用 skill_execute({
  "skill": "quote-generate",
  "command": "python scripts/generate.py",
  "content": '{"tenant_id":"xxx","region_name":"贵阳","total_people":30,...}'
})
```

**LLM 如何知道参数**：`quote-generate` 的 SKILL.md 中定义了完整的参数说明，LLM 加载 skill 后能看到所有参数的名称、类型、含义。LLM 从对话中逐步收集这些信息（人数、天数、景点、酒店偏好等），所有参数齐备后一次性传入。

#### 10.2.2 Skill 内部处理

skill 的 Python 脚本 `generate.py` 接收 JSON 参数后，内部完成：

1. **行程解析**（新模式）：调用 LLM 从 `itinerary_text` 提取结构化数据（人数、景点、酒店偏好、路线等）
2. **资源检索**（新模式）：用 AttractionRetriever / HotelRetriever 向量搜索匹配景点和酒店
3. **计价**：按第 5 节流程完成交通、门票、住宿、餐饮、导游、其他费用的计算
4. **导出**：加载报价模板，填充数据，生成 xlsx 文件
5. **返回**：返回报价明细 JSON + 文件路径

> 门票和住宿的价格取用会调用 LLM（从知识库 chunk 文本中提取价格），其余为纯 Python 逻辑。

#### 10.2.3 调用全景流程

```
用户消息: "帮我做个20个学生去贵州天眼+荔波6天的报价"
    │
    ▼
Agent.process_message()
    │
    ├─ 第1-5轮: LLM 通过对话确认行程方案
    │    必要时调用 knowledge_base_search 了解景点信息
    │
    ├─ 第6轮: 行程确认后，LLM 调用 skill_execute({skill: "quote-generate", ...})
    │    │
    │    ▼ generate.py 内部流程（14 个模块协作）
    │    ├─ itinerary_parser.py → LLM 解析行程文本
    │    ├─ resource_resolver.py → 向量检索匹配景点/酒店
    │    ├─ vehicle.py → 车型推荐 + 按公里计费（调用高德 API）
    │    ├─ attraction.py → LLM 提取门票 + 游玩项目
    │    ├─ hotel.py → 多城市酒店取价 + 排房
    │    ├─ meal.py → 餐标
    │    ├─ guide.py → 导游费用
    │    ├─ other_fees.py → 保险等
    │    ├─ 汇总 → 人均 + 整团 + 老师费用
    │    └─ excel_export.py → 生成 Excel
    │
    ├─ 第7轮: LLM 收到报价明细，组织文字回复给用户
    │
    └─ 第8轮: LLM 调用 register_download_file 注册 Excel 下载
         → 告知用户可以下载报价单
```

> 对比之前的设计：之前 LLM 需要多轮调用 `travel_quote_query` 查不同的表，然后自行计算费用，最后再调用 `quote-export` 导出。现在 LLM 只需调用一次 skill，所有查库和计算都由脚本完成，更准确、更高效。

### 10.3 体系 B：后台管理 REST API

这些接口给**前端管理页面**和**外部系统对接**使用，LLM 不调用这些接口。

#### 定价数据维护（前端管理页面使用）

旅行社管理员在前端页面维护定价数据，典型的 CRUD 操作：

```
# 区域管理
GET       /api/v1/travel-quote/regions                    # 区域列表
POST      /api/v1/travel-quote/regions                    # 新增区域
PUT       /api/v1/travel-quote/regions/{id}               # 编辑区域
DELETE    /api/v1/travel-quote/regions/{id}               # 删除区域

# 车辆价格管理
GET       /api/v1/travel-quote/vehicles?region_name=贵阳  # 车辆列表（按区域筛选）
POST      /api/v1/travel-quote/vehicles                   # 新增车辆价格
PUT       /api/v1/travel-quote/vehicles/{id}              # 编辑
DELETE    /api/v1/travel-quote/vehicles/{id}

# 景点 + 门票
GET       /api/v1/travel-quote/attractions?region_name=贵阳
POST      /api/v1/travel-quote/attractions
PUT       /api/v1/travel-quote/attractions/{id}
DELETE    /api/v1/travel-quote/attractions/{id}
GET       /api/v1/travel-quote/attractions/{id}/tickets   # 该景点的所有票种
POST      /api/v1/travel-quote/attractions/{id}/tickets   # 批量新增票种

# 酒店 + 房型（同理）
GET/POST  /api/v1/travel-quote/hotels
GET/POST  /api/v1/travel-quote/hotels/{id}/rooms

# 餐标 / 导游 / 其他费用 / 淡旺季（同理，标准 CRUD）
GET/POST  /api/v1/travel-quote/meals
GET/POST  /api/v1/travel-quote/guides
GET/POST  /api/v1/travel-quote/fees
GET/POST  /api/v1/travel-quote/seasons
```

**调用方**：前端管理页面（浏览器中的 Vue 组件）
**鉴权**：租户管理员登录后操作，自动带 `tenant_id`

#### 数据批量导入（外部系统对接 / Excel 导入）

```
POST      /api/v1/travel-quote/import/excel               # 上传 Excel 批量导入
  请求: multipart/form-data，上传 Excel 文件
  处理: 按 Sheet 名匹配表名，解析后批量写入对应 bs_ 表
  调用方: 前端上传页面
```

#### 租户定制配置（前端管理页面）

模板路径、对话风格、路线策略等所有租户定制内容统一在 extra.md 中配置，前端提供 Markdown 编辑器：

```
GET       /api/v1/subagents/<name>/extra                 # 获取 extra.md 内容
PUT       /api/v1/subagents/<name>/extra                 # 保存 extra.md 内容
DELETE    /api/v1/subagents/<name>/extra                 # 删除，恢复默认
```

**调用方**：前端管理页面
**鉴权**：租户管理员
**说明**：一个编辑器搞定所有定制需求，保存即生效。不需要策略表、人设表、渲染发布流程。

### 10.4 接口与调用方对照表

| 接口 | 调用方 | 触发方式 | LLM 是否参与 |
|------|--------|---------|-------------|
| `skill_execute(quote-generate)` | **LLM** | function calling 自动触发 | 是（LLM 传入行程参数） |
| `knowledge_base_search` | **LLM** | function calling 自动触发 | 是（LLM 构造查询） |
| 定价数据 CRUD API | **前端管理页面** | 人工操作 | 否 |
| Excel 批量导入 API | **前端管理页面** | 人工上传 | 否 |
| 租户定制 extra.md API | **前端管理页面** | 人工编辑 | 否 |

---

## 11. 本设计覆盖但用户未提及的费用项

| 费用项 | 归属 |
|--------|------|
| 景区内交通（环保车/索道/游船） | attractions.internal_transport |
| 淡旺季差价 | seasons + 各表 season_type |
| 旅行社协议价 vs 挂牌价 | tickets/rooms 的 agency_price |
| 团体优惠价 | tickets 的 group_price |
| 单房差 | 排房算法自动计算 |
| 司机餐补/住宿费 | vehicles 的 driver_meal/accommodation |
| 综合服务费 | fees 表 |
| 空驶费 | fees 表 |
| 加床费 | rooms 的 extra_bed_rate |
| 外语导游加价 | guides 的 language_premium |
| 研学导师费用 | guides 的 guide_type=research |
| 路餐/简餐 | meals 的 meal_type=pack_lunch |
| 活动物料费 | fees 表 |
| 超时费/超公里费 | vehicles 的 overtime/overkm_rate |

---

## 12. 实施优先级

| 优先级 | 模块 | 工作量 | 状态 | 原因 |
|--------|------|--------|------|------|
| P0 | extra.md 租户定制系统 | 小 | ✅ 完成 | 改 agent prompt 拼接逻辑 + 前端编辑器 |
| P1 | 定价数据表 + 数据导入 | 中 | ✅ 完成 | 核心数据层 |
| P2 | quote-generate skill | 大 | ✅ 完成 | 查库+计算+模板导出全流程 |
| P3 | 前端业务数据管理页面 | 中 | ✅ 完成 | 7 个 CRUD 管理页面 + Excel 批量导入 + 模板下载，见第 13 节 |

---

## 13. 前端业务数据管理页面

### 13.1 设计目标

为旅游子智能体提供可视化的定价数据管理页面。旅行社管理员通过侧边栏"业务数据"菜单进入，在新标签页中管理 10 张定价数据表。

复用项目已有的业务页面架构：
- `MenuSidebar.vue` — 自动渲染 SUBAGENT.md 中的 `business_pages` 配置
- `BaseBusinessLayout.vue` — 业务页面的通用布局（标题栏 + 返回按钮 + 内容区）
- `window.open(route, '_blank')` — 在新标签页打开

### 13.2 业务页面规划

10 张定价表按功能归类为 7 个管理页面：

| 页面 ID | 标题 | 图标 | 路由 | 管理的表 |
|---------|------|------|------|---------|
| regions | 区域管理 | 🌍 | /travel-consultant/regions | bs_travel_quote_regions |
| vehicles | 车辆价格 | 🚐 | /travel-consultant/vehicles | bs_travel_quote_vehicles |
| attractions | 景点门票 | 🏔️ | /travel-consultant/attractions | bs_travel_quote_attractions + bs_travel_quote_tickets |
| hotels | 酒店房型 | 🏨 | /travel-consultant/hotels | bs_travel_quote_hotels + bs_travel_quote_rooms |
| meals | 餐标价格 | 🍽️ | /travel-consultant/meals | bs_travel_quote_meals |
| guides | 导游费用 | 🧑‍🏫 | /travel-consultant/guides | bs_travel_quote_guides |
| fees | 其他费用 | 💰 | /travel-consultant/fees | bs_travel_quote_fees + bs_travel_quote_seasons |

**设计决策**：
- 景点+门票合并为一个页面（门票是景点的子资源，用弹窗/折叠面板管理）
- 酒店+房型合并为一个页面（房型是酒店的子资源）
- 其他费用+淡旺季合并为一个页面（淡旺季配置简单，作为费用页的一个 Tab）
- 区域、车辆、餐标、导游各独立一个页面

### 13.3 SUBAGENT.md business_pages 配置

在 `subagents/travel-consultant/SUBAGENT.md` 的 YAML frontmatter 中添加：

```yaml
business_pages:
  - id: regions
    title: 区域管理
    icon: 🌍
    route: /travel-consultant/regions
  - id: vehicles
    title: 车辆价格
    icon: 🚐
    route: /travel-consultant/vehicles
  - id: attractions
    title: 景点门票
    icon: 🏔️
    route: /travel-consultant/attractions
  - id: hotels
    title: 酒店房型
    icon: 🏨
    route: /travel-consultant/hotels
  - id: meals
    title: 餐标价格
    icon: 🍽️
    route: /travel-consultant/meals
  - id: guides
    title: 导游费用
    icon: 🧑‍🏫
    route: /travel-consultant/guides
  - id: fees
    title: 其他费用
    icon: 💰
    route: /travel-consultant/fees
```

### 13.4 前端路由配置

在 `frontend/src/main.ts` 中添加两组路由（参考外贸获客智能体的模式）：

**独立模式**：
```ts
{
  path: '/travel-consultant',
  name: 'travel-consultant',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    { path: 'regions', name: 'travel-consultant-regions', component: () => import('./components/travel/RegionManager.vue') },
    { path: 'vehicles', name: 'travel-consultant-vehicles', component: () => import('./components/travel/VehicleManager.vue') },
    { path: 'attractions', name: 'travel-consultant-attractions', component: () => import('./components/travel/AttractionManager.vue') },
    { path: 'hotels', name: 'travel-consultant-hotels', component: () => import('./components/travel/HotelManager.vue') },
    { path: 'meals', name: 'travel-consultant-meals', component: () => import('./components/travel/MealManager.vue') },
    { path: 'guides', name: 'travel-consultant-guides', component: () => import('./components/travel/GuideManager.vue') },
    { path: 'fees', name: 'travel-consultant-fees', component: () => import('./components/travel/FeeManager.vue') },
  ]
}
```

**租户模式**（嵌套在 `/t/:tenant_id` 路由组下）：
```ts
{
  path: 'travel-consultant',
  name: 'tenant-travel-consultant',
  component: () => import('./components/BaseBusinessLayout.vue'),
  children: [
    { path: 'regions', name: 'tenant-travel-consultant-regions', component: () => import('./components/travel/RegionManager.vue') },
    // ... 其余 6 个页面同上，name 加 tenant- 前缀
  ]
}
```

### 13.5 前端 API 客户端

新建 `frontend/src/api/travelQuote.ts`，封装所有 `/api/v1/travel-quote/*` 接口调用。

```typescript
// 基础模式（参考 customer.ts）
import { getAuthHeader } from './auth'

const API_BASE = `${import.meta.env.VITE_API_BASE_URL || '/api'}/travel-quote`

// 通用 CRUD 函数
async function crudList(resource: string, params?: Record<string, string>) { ... }
async function crudGet(resource: string, id: number) { ... }
async function crudCreate(resource: string, data: any) { ... }
async function crudUpdate(resource: string, id: number, data: any) { ... }
async function crudDelete(resource: string, id: number) { ... }

// 各资源的具体方法
export const travelQuoteAPI = {
  regions: { list, create, update, delete },
  vehicles: { list, create, update, delete },
  attractions: { list, create, update, delete },
  tickets: { list(attractionId), create(attractionId, data), update, delete },
  hotels: { list, create, update, delete },
  rooms: { list(hotelId), create(hotelId, data), update, delete },
  meals: { list, create, update, delete },
  guides: { list, create, update, delete },
  fees: { list, create, update, delete },
  seasons: { list, create, update, delete },
}
```

### 13.6 Vue 组件设计

所有管理页面组件放在 `frontend/src/components/travel/` 目录下。

#### 通用 CRUD 管理页面模式

每个管理页面遵循统一的交互模式：

```
┌─────────────────────────────────────────────────────────────┐
│  [页面标题]                              [+ 新增] [区域▼筛选] │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 数据表格（可编辑行 / 点击编辑按钮弹出表单）                ││
│  │  名称 │ 字段1 │ 字段2 │ ... │ 操作（编辑/删除）          ││
│  └─────────────────────────────────────────────────────────┘│
│                                                             │
│  [新增/编辑弹窗]                                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │ 表单字段（根据各表结构动态渲染）                           ││
│  │                              [取消] [保存]               ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

#### 各页面组件说明

**RegionManager.vue（区域管理）**
- 表格列：区域名称、别名、上级区域、层级、状态
- 筛选：层级（省/市/区县）
- 操作：新增/编辑/删除

**VehicleManager.vue（车辆价格）**
- 表格列：车型、显示名、座位范围、日租金、超时费、超公里费、司机餐补、司机住宿费、季节类型
- 筛选：区域名称、车型
- 操作：新增/编辑/删除

**AttractionManager.vue（景点门票）**
- 主表格列：景点名称、区域、分类、地址、游览时长、景区交通、状态
- 筛选：区域名称
- 操作：新增/编辑/删除景点 + 点击行展开门票管理
- 子表格（展开行）：票种、显示名、挂牌价、协议价、团体价、季节类型
- 子操作：新增/编辑/删除门票

**HotelManager.vue（酒店房型）**
- 主表格列：酒店名称、区域、星级、地址、联系电话、状态
- 筛选：区域名称
- 操作：新增/编辑/删除酒店 + 点击行展开房型管理
- 子表格（展开行）：房型、显示名、入住人数、床位数、门市价、协议价、含早、加床费、季节类型
- 子操作：新增/编辑/删除房型

**MealManager.vue（餐标价格）**
- 表格列：区域、餐标档次、餐类、每人价格、每桌人数、菜品标准、季节类型
- 筛选：区域名称、餐标档次
- 操作：新增/编辑/删除

**GuideManager.vue（导游费用）**
- 表格列：区域、导游类型、级别、计费方式、日薪/整团费、外语加价、旺季倍率
- 筛选：区域名称、导游类型
- 操作：新增/编辑/删除

**FeeManager.vue（其他费用 + 淡旺季）**
- Tab 1「其他费用」：费用名称、分类、计费方式、单价、是否必含
- Tab 2「淡旺季配置」：季节类型、显示名、开始日期、结束日期、价格倍率
- 操作：新增/编辑/删除

### 13.7 数据流

```
MenuSidebar 检测当前子智能体 = travel-consultant
  → 读取 SUBAGENT.md 的 business_pages
  → 渲染 7 个业务菜单项
  → 用户点击 → window.open(route, '_blank')

新标签页加载 Vue App
  → BaseBusinessLayout.vue 包裹
  → 匹配路由 → 加载对应 Manager 组件
  → 组件 onMounted → 调用 travelQuoteAPI.*.list()
  → API 请求 /api/v1/travel-quote/* （带 X-Tenant-Id）
  → 后端 travel_quote.py 处理 CRUD
  → 返回数据 → 渲染表格

用户新增/编辑/删除
  → 前端表单弹窗 → travelQuoteAPI.*.create/update/delete()
  → 后端处理 → 返回结果 → 刷新列表
```

### 13.8 文件变更清单

#### 后端变更

| 文件 | 操作 | 说明 | 状态 |
|------|------|------|------|
| `subagents/travel-consultant/SUBAGENT.md` | 修改 | YAML frontmatter 添加 `business_pages` 配置 | ✅ 已完成 |
| `src/api/travel_quote.py` | 修改 | 新增 Excel 导入 API + 模板下载 API（见 13.12 节） | ✅ 已完成 |

#### 前端新增文件

| 文件 | 说明 | 状态 |
|------|------|------|
| `frontend/src/api/travelQuote.ts` | API 客户端，封装 10 个资源的 CRUD + 导入/模板下载 | ✅ 已完成 |
| `frontend/src/composables/useImport.ts` | Excel 导入共享 composable（导入/模板下载/结果弹窗） | ✅ 已完成 |
| `frontend/src/components/travel/RegionManager.vue` | 区域管理页面 | ✅ 已完成 |
| `frontend/src/components/travel/VehicleManager.vue` | 车辆价格管理页面 | ✅ 已完成 |
| `frontend/src/components/travel/AttractionManager.vue` | 景点门票管理页面（含子表格门票管理） | ✅ 已完成 |
| `frontend/src/components/travel/HotelManager.vue` | 酒店房型管理页面（含子表格房型管理） | ✅ 已完成 |
| `frontend/src/components/travel/MealManager.vue` | 餐标价格管理页面 | ✅ 已完成 |
| `frontend/src/components/travel/GuideManager.vue` | 导游费用管理页面 | ✅ 已完成 |
| `frontend/src/components/travel/FeeManager.vue` | 其他费用 + 淡旺季管理页面（双 Tab） | ✅ 已完成 |

#### 前端修改文件

| 文件 | 说明 | 状态 |
|------|------|------|
| `frontend/src/main.ts` | 添加 travel-consultant 路由（独立模式 + 租户模式） | ✅ 已完成 |
| `frontend/src/components/BaseBusinessLayout.vue` | 修复 `<slot>` → `<router-view />` 空白页 bug | ✅ 已完成 |

### 13.9 实施顺序

```
Step 1: SUBAGENT.md 添加 business_pages → 侧边栏菜单立即可见          ✅ 完成
Step 2: travelQuote.ts API 客户端 → 前端可调用后端接口                  ✅ 完成
Step 3: main.ts 添加路由 → 菜单点击后能加载对应组件                     ✅ 完成
Step 4: 逐个实现 7 个 Manager 组件（按使用频率排序）                    ✅ 完成
        4.1 RegionManager → 其他页面依赖区域数据做筛选                  ✅ 完成
        4.2 AttractionManager → 景点+门票，核心数据                    ✅ 完成
        4.3 HotelManager → 酒店+房型                                    ✅ 完成
        4.4 VehicleManager → 车辆价格                                   ✅ 完成
        4.5 MealManager → 餐标                                          ✅ 完成
        4.6 GuideManager → 导游                                         ✅ 完成
        4.7 FeeManager → 其他费用 + 淡旺季                              ✅ 完成
Step 5: Excel 批量导入（后端 API + 前端上传入口 + 模板下载）            ✅ 完成
Step 6: BaseBusinessLayout 空白页修复（slot → router-view）            ✅ 完成
Step 7: 验证全流程（菜单渲染 → 页面打开 → CRUD 操作 → 数据持久化）     ✅ 完成
```

### 13.10 验证方式

1. **菜单渲染** ✅ 已验证：进入旅游顾问对话页面，左侧边栏出现"业务数据"分组，包含 7 个菜单项
2. **页面打开** ✅ 已验证：点击每个菜单项，在新标签页中打开对应管理页面，显示 BaseBusinessLayout 头部 + 完整表格和操作按钮
3. **CRUD 操作** ✅ 已验证：每个页面测试新增、编辑、删除，数据正确持久化到数据库
4. **Excel 导入** ✅ 已验证：下载模板 → 填入数据 → 上传导入 → 列表自动刷新
5. **租户隔离** — 待多租户环境验证：不同租户登录后只能看到自己租户的定价数据
6. **关联数据** ✅ 已验证：景点页面展开门票管理、酒店页面展开房型管理，父子数据联动正确

---

### 13.11 已知问题修复：BaseBusinessLayout 渲染空白 ✅ 已修复

**问题**：`BaseBusinessLayout.vue` 第 29 行使用 `<slot></slot>` 而非 `<router-view />`。Vue Router 的嵌套子路由通过 `<router-view />` 渲染，`<slot>` 只在组件被手动嵌套时生效。导致所有业务数据页面打开后只有标题栏，内容区域为空。

**修复**：将 `<slot></slot>` 改为 `<router-view />`。

**影响范围**：所有使用 BaseBusinessLayout 作为路由父组件的页面（外贸获客 + 旅游顾问的所有业务数据页面）。

---

### 13.12 Excel 批量导入功能 ✅ 已完成

#### 设计思路

旅行社已有大量定价数据存放在 Excel 中，需要批量导入到 10 张定价表。提供两个 API：
- **下载模板**：返回包含 10 个 Sheet 的空 Excel 文件，每个 Sheet 预置列头
- **上传导入**：读取上传的 Excel，按 Sheet 名匹配目标表，逐行插入

#### API 设计

**下载导入模板**：
```
GET /api/v1/travel-quote/import/template
响应：直接返回 .xlsx 文件流
```

**上传 Excel 导入**：
```
POST /api/v1/travel-quote/import/excel
请求：multipart/form-data，字段名 file
响应：{ success: true, data: { results: [{ sheet: "区域", imported: 10, skipped: 2, errors: [...] }] } }
```

#### Sheet 名与表的映射

| Sheet 名 | 目标表 | 关键列 |
|----------|--------|--------|
| 区域 | bs_travel_quote_regions | name, aliases, parent_name, level |
| 车辆 | bs_travel_quote_vehicles | vehicle_type, vehicle_type_label, seats_min, seats_max, daily_rate |
| 景点 | bs_travel_quote_attractions | name, region_name, category |
| 门票 | bs_travel_quote_tickets | attraction_id, ticket_type, retail_price |
| 酒店 | bs_travel_quote_hotels | name, region_name, star_rating |
| 房型 | bs_travel_quote_rooms | hotel_id, room_type, retail_price |
| 餐标 | bs_travel_quote_meals | meal_tier, meal_type, price_per_person |
| 导游 | bs_travel_quote_guides | guide_type, daily_rate |
| 费用 | bs_travel_quote_fees | fee_name, fee_category, unit_price |
| 淡旺季 | bs_travel_quote_seasons | season_type, start_date, end_date |

#### 前端交互

每个管理页面顶部操作区新增两个按钮：
- **"下载模板"** — 调用模板下载 API，保存为 travel_quote_template.xlsx
- **"导入 Excel"** — 打开文件选择器（只接受 .xlsx），上传后显示导入结果弹窗（成功 N 条，跳过 N 条，失败原因列表），成功后自动刷新列表

#### 文件变更

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/api/travel_quote.py` | 修改 | 新增 GET /import/template 和 POST /import/excel |
| `frontend/src/api/travelQuote.ts` | 修改 | 新增 downloadTemplate() 和 importExcel() |
| 7 个 Manager.vue | 修改 | 操作区添加"下载模板"和"导入 Excel"按钮 |
