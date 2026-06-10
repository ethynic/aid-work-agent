# 报价生成模块重构设计文档

> 版本: v1.0 | 创建日期: 2026-05-18 | 状态: 待审核

## 1. 背景

`src/skills/travel-quote/scripts/generate.py` 当前约 2246 行，承担了报价生成的全部职责。代码可读性差、维护成本高，且**门票和游玩项目的价格提取存在严重缺陷**（缺项、匹配不准、重复计算），需要重构。

### 1.1 保留不动的部分

| 模块 | 原因 |
|------|------|
| `main()` 入口 | 入参解析逻辑无问题 |
| `_call_llm()` | LLM 调用逻辑无问题 |
| `_calculate_multi_leg_distance()` / `_calculate_per_km_cost()` / `calculate_vehicle_cost()` | 用车费用计算无问题 |
| `export_with_template()` / `_export_simple()` / `_replace_placeholders()` | Excel 导出和模板替换无问题 |
| `calculate_hotel_stays()` / `calculate_hotel_cost()` / `_calculate_hotel_cost_from_kb()` / `_parse_team_price()` | 酒店匹配和住宿费用计算无问题，仅拆分 |
| `calculate_meal_cost()` / `calculate_guide_cost()` / `calculate_other_fees()` | 其他费用计算无问题 |
| `init_tables()` / `TABLE_DEFINITIONS` / `get_db()` / `query_by_region()` | 基础设施无问题 |
| `recommend_vehicle()` | 车型推荐无问题 |
| `resolve_resources()` 中的酒店匹配部分 | 按城市逐个检索酒店逻辑无问题，仅拆分 |

### 1.2 需要重构的部分

| 问题 | 严重程度 | 说明 |
|------|----------|------|
| 门票/项目价格提取缺项 | **严重** | LLM 提取 prompt 设计不合理，经常遗漏门票票种或项目 |
| 门票/项目匹配不准 | **严重** | 游玩项目与知识库项目的模糊匹配效果差 |
| 景点门票未去重 | **严重** | 同一景点多天出现时，门票会被重复计算 |
| 价格取值逻辑不清 | **高** | 应统一取"挂牌价"（较高的价格），但实际经常取到渠道价 |
| 代码过长 | **中** | 单文件 2246 行，需按职责拆分 |

---

## 2. 重构目标

1. **拆分文件**：将 `generate.py` 按职责拆分为多个模块，主文件控制在 400 行以内
2. **修复门票提取**：重新设计 LLM prompt，确保不遗漏票种
3. **修复项目匹配**：重新设计游玩项目提取逻辑，确保匹配准确
4. **景点去重**：同一景点只收一次门票
5. **统一价格取值**：始终取挂牌价（较高价格）

---

## 3. 文件拆分方案

```
src/skills/travel-quote/scripts/
├── generate.py              # 主入口 + 主流程编排（约 350 行）
├── db.py                    # 数据库连接、表初始化、区域查询（约 100 行）
├── season.py                # 季节判断（约 30 行）
├── vehicle.py               # 车型推荐 + 用车费用计算（约 200 行）
├── attraction.py            # 🆕 景点门票 + 游玩项目计算（约 400 行，本次重构核心）
├── hotel.py                 # 住宿计算（约 200 行）
├── meal.py                  # 餐饮计算（约 60 行）
├── guide.py                 # 导游计算（约 60 行）
├── other_fees.py            # 其他费用计算（约 50 行）
├── excel_export.py          # Excel 模板导出（约 300 行）
├── itinerary_parser.py      # 🆕 行程解析（LLM 提取景点/团队/路线）（约 200 行）
├── resource_resolver.py     # 🆕 资源解析（景点/酒店向量匹配）（约 150 行）
├── attraction_retriever.py  # 不变
├── hotel_retriever.py       # 不变
├── attraction_excel_parser.py # 不变
├── hotel_excel_parser.py    # 不变
└── vehicle_excel_parser.py  # 不变
```

### 3.1 各模块职责

| 模块 | 职责 | 依赖 |
|------|------|------|
| `generate.py` | `main()`、`generate_quote()` 主流程编排 | 所有业务模块 |
| `db.py` | `get_db()`、`init_tables()`、`TABLE_DEFINITIONS`、`query_by_region()` | `src/db/database` |
| `season.py` | `determine_season()` | `db.py` |
| `vehicle.py` | `recommend_vehicle()`、`calculate_vehicle_cost()`、距离计算相关 | `db.py` |
| `attraction.py` | 门票+游玩项目计算（核心重构） | `attraction_retriever.py`, `_call_llm()` |
| `hotel.py` | 住宿费用计算（单酒店+多酒店） | `hotel_retriever.py`, `db.py` |
| `meal.py` | 餐饮费用计算 | `db.py` |
| `guide.py` | 导游费用计算 | `db.py` |
| `other_fees.py` | 保险、杂费等 | `db.py` |
| `excel_export.py` | `export_with_template()`、`_export_simple()` | `openpyxl` |
| `itinerary_parser.py` | `parse_itinerary()` — LLM 解析行程文本 | `_call_llm()` |
| `resource_resolver.py` | `resolve_resources()` — 向量匹配景点/酒店 | `attraction_retriever.py`, `hotel_retriever.py` |

### 3.2 模块间调用关系

```
generate.py::generate_quote(params)
  │
  ├── itinerary_parser.py::parse_itinerary(itinerary_text)
  │     └── _call_llm(prompt)          # LLM 解析行程
  │
  ├── resource_resolver.py::resolve_resources(parsed, tenant_id)
  │     ├── AttractionRetriever.search()  # 向量匹配景点
  │     └── HotelRetriever.search()       # 向量匹配酒店
  │
  ├── season.py::determine_season()
  │
  ├── vehicle.py::calculate_vehicle_cost()
  │
  ├── attraction.py::calculate_attraction_cost()   # 🆕 统一入口
  │     ├── _extract_unique_attractions()           # 去重景点
  │     ├── _llm_extract_ticket_and_project_prices() # LLM 提取（新 prompt）
  │     └── _fallback_parse_prices()                # 规则 fallback
  │
  ├── hotel.py::calculate_hotel_stays() / calculate_hotel_cost()
  │
  ├── meal.py::calculate_meal_cost()
  │
  ├── guide.py::calculate_guide_cost()
  │
  ├── other_fees.py::calculate_other_fees()
  │
  └── excel_export.py::export_with_template()
```

---

## 4. 核心重构：景点门票 + 游玩项目

### 4.1 当前问题根因分析

#### 问题 1：门票提取缺项

**现状**：`_llm_extract_attraction_prices()` 的 prompt 要求 LLM 从知识库的门票价格表中提取价格，但 prompt 过于复杂，LLM 经常遗漏票种（如只提取成人票遗漏学生票）。

**根因**：
- Prompt 同时要求做景点验证、门票提取、项目提取三件事，任务过多
- Prompt 中说"即使某票种人数为0也要提取"，但 LLM 实际行为是跳过 0 人的票种
- 门票和项目混在同一个 prompt 中，互相干扰

#### 问题 2：游玩项目匹配不准

**现状**：`mentioned_activities`（从行程中解析的活动名称）与知识库 `project_table` 中的项目名称做模糊匹配，匹配效果差。

**根因**：
- LLM prompt 中的项目匹配指令虽然有模糊匹配说明，但 LLM 经常不加区分地把项目表中所有项目都返回
- 行程文本中的活动描述与知识库中的项目名称差异大（如"登台俯瞰天眼全貌" vs "FAST观测体验"）

#### 问题 3：景点门票重复

**现状**：行程中同一景点多天出现（如 D4 上午和下午都在小七孔景区），门票被计算两次。

**根因**：
- `daily_attractions` 是按天存储的列表，同一景点出现在多天
- `resolve_resources()` 做了 `doc_id` 去重，但去重后 activities 的合并逻辑只合并了 activities，没有在后续门票计算时确保只收一次门票
- 门票计算 `_calculate_ticket_cost_from_kb()` 依赖 `doc_ids` 列表，去重后应该只算一次，但实际调用链中 `doc_ids` 可能包含重复

#### 问题 4：价格取值不准

**现状**：Prompt 说"优先取挂牌价"，但知识库价格表的列名不统一（有的叫"挂牌价"、有的叫"零售价"、有的叫"团队价"），LLM 经常取到较小的渠道价。

**根因**：
- 知识库价格表是用户上传的 Excel 解析文本，列名不统一
- "取较大的价格"这个规则在 prompt 中不够明确

### 4.2 重构方案

#### 4.2.1 景点去重策略

```
输入: daily_attractions = [
  {day:1, attractions: [{name:"小七孔景区", activities:["卧龙潭观光"]}]},
  {day:2, attractions: [{name:"小七孔景区", activities:["丛林探险"]}]}
]

Step 1: 按景点名称去重，合并 activities
  unique_attractions = {
    "小七孔景区": {activities: ["卧龙潭观光", "丛林探险"], days: [1, 2]}
  }

Step 2: 向量匹配 → doc_id
  "小七孔景区" → doc_id=42

Step 3: 门票只算一次，项目按合并后的 activities 算
```

**关键**：门票去重在数据结构层面保证，而非依赖 LLM 或后续逻辑。

#### 4.2.2 统一价格取值规则

知识库价格表是管道符分隔的文本（从 Excel 解析），格式示例：

```
票种|客户类型|价格
成人票|挂牌价|180
成人票|团队价|120
学生票|挂牌价|90
学生票|团队价|60
```

**规则**：对于每个票种，取该票种中**最大的价格**作为报价价。这是最简单且最可靠的规则。

**在 LLM prompt 中明确要求**：
- 同一票种如果有多个价格（如挂牌价、团队价），**取最大的那个**
- 不要在 JSON 中重复列出同一票种的多个价格

#### 4.2.3 新的 LLM Prompt 设计

将原来的一个大 prompt 拆分为两个独立 prompt：

**Prompt A：景点验证 + 门票提取**

```python
def _llm_extract_tickets(search_name, attraction_info, ticket_table,
                         adults, children_half, students, elders):
    """仅提取门票价格，不涉及项目"""
```

Prompt 核心指令：
1. 验证搜索名称与知识库景点是否匹配
2. 从门票价格表中提取每个票种的最高价格
3. **无论对应人数是否为 0，所有存在的票种都要提取**
4. 返回 `{confirmed, name, tickets: [{name, unit_price, ticket_type}]}`

**Prompt B：游玩项目匹配**

```python
def _llm_extract_projects(search_name, attraction_info, project_table,
                          activities):
    """仅提取游玩项目价格"""
```

Prompt 核心指令：
1. 行程中在该景点计划体验的具体项目为：`activities`
2. 从项目/服务价格表中用模糊语义匹配找到对应项目
3. **只返回能在价格表中找到匹配项的活动，不要添加价格表中没有的项目**
4. 返回 `{projects: [{name, unit_price, billing_method, remark}]}`

#### 4.2.4 新的数据流

```
itinerary_text
  │
  ▼ parse_itinerary()
  │  LLM 提取: daily_attractions, 团队构成, 路线等
  │
  ▼ resolve_resources()
  │  景点名称 → doc_id（向量匹配）
  │  酒店偏好 → doc_id
  │
  ▼ calculate_attraction_cost()     ← 新的统一入口
  │
  ├── Step 1: _merge_daily_attractions(daily_attractions)
  │     按景点名称去重，合并 activities，记录涉及的 days
  │     结果: [{name, doc_id, activities, days}]
  │
  ├── Step 2: 遍历每个唯一景点
  │     │
  │     ├── 获取知识库数据
  │     │   info = retriever.get_attraction_info(doc_id)
  │     │   ticket_table = retriever.get_ticket_table(doc_id)
  │     │   project_table = retriever.get_project_table(doc_id)
  │     │
  │     ├── Step 2a: _llm_extract_tickets()
  │     │   输入: search_name, info, ticket_table, 团队构成
  │     │   输出: tickets = [{name, unit_price, ticket_type}]
  │     │   每个景点只调用一次 → 保证门票不重复
  │     │
  │     ├── Step 2b: 如果有 activities → _llm_extract_projects()
  │     │   输入: search_name, info, project_table, activities
  │     │   输出: projects = [{name, unit_price, billing_method}]
  │     │
  │     ├── Step 2c: 如果 LLM 失败 → _fallback_parse_prices()
  │     │   规则解析门票表 + 项目表
  │     │
  │     └── Step 2d: 将门票和项目转为 items
  │
  └── 返回 items
```

---

## 5. 详细设计：attraction.py 模块

### 5.1 核心数据结构

```python
@dataclass
class UniqueAttraction:
    """去重后的景点信息"""
    name: str                    # 行程中的景点名称
    doc_id: int                  # 知识库 doc_id
    activities: List[str]        # 合并后的游玩项目列表
    days: List[int]              # 出现在哪几天
```

### 5.2 公开接口

```python
def calculate_attraction_cost(
    items: list,
    tenant_id: str,
    attraction_matches: List[Dict],    # resolve_resources() 的输出
    adults: int,
    children_half: int,
    students: int,
    elders: int,
    total_people: int,
    teacher_count: int = 0,
) -> list:
    """
    计算景点门票 + 游玩项目费用。

    核心逻辑：
    1. 按 doc_id 去重（同一景点只收一次门票）
    2. 合并同一景点在不同天提到的 activities
    3. 分别用 LLM 提取门票和项目价格
    4. LLM 失败时 fallback 到规则解析

    返回追加 items 后的列表。
    """
```

### 5.3 内部方法

```python
def _merge_attractions(attraction_matches: List[Dict]) -> List[UniqueAttraction]:
    """按 doc_id 去重，合并 activities"""


def _llm_extract_tickets(
    search_name: str,
    attraction_info: str,
    ticket_table: str,
    adults: int,
    children_half: int,
    students: int,
    elders: int,
) -> Optional[Dict]:
    """LLM 提取门票价格（独立 prompt，专注于门票）"""


def _llm_extract_projects(
    search_name: str,
    attraction_info: str,
    project_table: str,
    activities: List[str],
) -> Optional[List[Dict]]:
    """LLM 提取游玩项目价格（独立 prompt，专注于项目匹配）"""


def _build_ticket_items(
    items: list,
    attraction_name: str,
    tickets: List[Dict],
    adults: int,
    children_half: int,
    students: int,
    total_people: int,
    teacher_count: int,
) -> list:
    """将门票提取结果转为 items，根据团队构成分配人数"""


def _build_project_items(
    items: list,
    attraction_name: str,
    projects: List[Dict],
    total_people: int,
    teacher_count: int,
) -> list:
    """将项目提取结果转为 items，处理按人/按团计费"""


def _fallback_parse_prices(
    items: list,
    attraction_name: str,
    ticket_table: str,
    project_table: str,
    activities: List[str],
    adults: int,
    children_half: int,
    students: int,
    total_people: int,
    teacher_count: int,
) -> list:
    """规则 fallback：解析管道符分隔的价格表文本"""
```

---

## 6. LLM Prompt 详细设计

### 6.1 Prompt A：门票提取

```
你是一个旅游报价数据提取助手。请从以下门票价格表中提取价格。

## 景点信息
{attraction_info}

## 搜索关键词
"{search_name}"

## 门票价格表
{ticket_table}

## 团队构成
- 成人: {adults} 人
- 儿童（半票）: {children_half} 人
- 学生: {students} 人
- 老人: {elders} 人

## 任务

1. 判断搜索关键词"{search_name}"与知识库中的景点是否是同一个（考虑别名、简称）
2. 从门票价格表中提取**所有票种的价格**
3. 同一票种如果有多个价格（如挂牌价、团队价、渠道价），**取最大的那个**

## 返回 JSON

```json
{
    "confirmed": true,
    "name": "景点正式名称",
    "tickets": [
        {"name": "景点名(成人票)", "unit_price": 180, "ticket_type": "adult"},
        {"name": "景点名(学生票)", "unit_price": 90, "ticket_type": "student"},
        {"name": "景点名(儿童票)", "unit_price": 90, "ticket_type": "child_half"},
        {"name": "景点名(老人票)", "unit_price": 0, "ticket_type": "elder"}
    ]
}
```

## 关键规则

1. **取最大价格**：同一票种多行价格取最大的。如"成人票 挂牌价180"和"成人票 团队价120"，取 180
2. **提取所有票种**：价格表中存在的成人票、学生票、儿童票、老人票都要提取，不要遗漏
3. **ticket_type 取值**：adult / student / child_half / elder
4. **景点不匹配**时 confirmed=false，但仍提取价格（可能是相关景点）
5. 如果门票价格表为空，tickets 返回空数组
6. 只返回 JSON
```

### 6.2 Prompt B：游玩项目匹配

```
你是一个旅游项目价格匹配助手。请将行程中提到的活动与知识库价格表进行匹配。

## 景点信息
{attraction_info}

## 搜索关键词
"{search_name}"

## 项目/服务价格表
{project_table}

## 行程中提到的游玩项目
{activities_list}

## 任务

逐一检查每个游玩项目，在价格表中找到匹配的项目并提取价格。

## 匹配规则

1. **语义匹配**：行程中的活动描述可能与价格表名称不完全一致，需要理解语义
   - "登台俯瞰天眼全貌" ↔ "FAST观测体验"
   - "天文小课堂" ↔ "天文小课堂"
   - "夜游望远镜观星" ↔ "夜间望远镜观测"
2. **只返回能匹配到的项目**：找不到匹配的活动不要添加
3. **不要添加行程未提到的项目**：价格表中有但行程没提到的项目不要添加
4. **计费方式判断**：
   - 价格表中有"团""组"等字样 → 按团计费（per_group）
   - 否则 → 按人计费（per_person）
5. **同一票种/项目有多个价格时，取最大的那个**

## 返回 JSON

```json
{
    "projects": [
        {"name": "FAST观测体验", "unit_price": 50, "billing_method": "per_person", "matched_activity": "登台俯瞰天眼全貌"},
        {"name": "天文小课堂", "unit_price": 30, "billing_method": "per_person", "matched_activity": "天文小课堂"},
        {"name": "讲解费", "unit_price": 400, "billing_method": "per_group", "matched_activity": "精品讲解导览"}
    ]
}
```

如果没有任何项目能匹配，projects 返回空数组。
只返回 JSON。
```

### 6.3 与旧 Prompt 的对比

| 维度 | 旧 Prompt | 新 Prompt |
|------|-----------|-----------|
| 数量 | 1 个大 prompt | 2 个独立 prompt |
| 职责 | 验证+门票+项目三合一 | 门票和项目分离 |
| 价格取值 | "优先取挂牌价"（模糊） | "取最大价格"（明确） |
| 票种完整性 | "即使人数为0也要提取"（经常被忽略） | "提取所有票种，不要遗漏"（更直接） |
| 项目匹配 | 在同一个 prompt 中指令过长 | 独立 prompt，专注匹配 |
| LLM 调用次数 | 每景点 1 次 | 每景点最多 2 次（门票1次+项目1次） |

> LLM 调用次数增加是可接受的：每次调用更聚焦，准确率更高。且只有在景点有 activities 时才会调用第二次。

---

## 7. 行程解析（itinerary_parser.py）变更

### 7.1 现有 parse_itinerary() 的保留

`parse_itinerary()` 的整体逻辑和 prompt 无需大改，只需要微调：

1. **输出格式不变**：仍然返回 `daily_attractions`、团队构成、路线等
2. **微调点**：确保 `daily_attractions` 中同一景点在不同天出现时，每天的 `activities` 都被正确提取（现状已支持，无需修改）

### 7.2 提取到独立文件

将 `parse_itinerary()` 和 `resolve_resources()` 从 `generate.py` 提取到各自的模块文件中。逻辑不变，只是文件位置变化。

---

## 8. 门票 → items 的转换逻辑

### 8.1 现有问题

当前逻辑：LLM 返回的 tickets 中每个票种都有一个 `quantity`（人数），但这个 quantity 是从 LLM 提取时传入的团队构成推导的。问题是 LLM 经常混淆人数。

### 8.2 新方案

**不在 LLM prompt 中传入具体人数**，而是在代码中根据团队构成分配：

```python
def _build_ticket_items(items, attraction_name, tickets, adults, children_half,
                        students, elders, total_people, teacher_count):
    """将门票提取结果转为 items"""
    ticket_type_to_count = {
        "adult": adults,
        "child_half": children_half,
        "student": students,
        "elder": elders,
    }
    # teacher 按成人票计费
    teacher_ticket_type = "adult"

    for ticket in tickets:
        unit_price = ticket["unit_price"]
        if unit_price == 0:
            continue

        ticket_type = ticket["ticket_type"]
        count = ticket_type_to_count.get(ticket_type, 0)

        # 跳过该票种对应人数为 0 的情况
        if count == 0:
            continue

        # 随队老师按成人票计费
        teacher_subtotal = 0
        if teacher_count > 0 and ticket_type == teacher_ticket_type:
            teacher_subtotal = round(unit_price * teacher_count, 2)

        items.append({
            "category": "门票/项目",
            "name": ticket["name"],
            "unit_price": unit_price,
            "quantity": count,
            "unit": "人",
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": unit_price,      # 每人费用
            "teacher_subtotal": teacher_subtotal,
            "remark": ticket.get("remark", ""),
        })
```

**关键变化**：
- LLM 只负责提取价格，不负责分配人数
- 人数分配完全由代码根据团队构成决定
- `subtotal` 是每人费用（= unit_price），`quantity` 是对应人数

### 8.3 游玩项目 → items 转换

```python
def _build_project_items(items, attraction_name, projects, total_people, teacher_count):
    for proj in projects:
        unit_price = proj["unit_price"]
        if unit_price == 0:
            continue

        billing = proj.get("billing_method", "per_person")

        if billing == "per_group":
            quantity = 1
            unit = "团"
            subtotal = round(unit_price / total_people, 2)
            teacher_subtotal = 0
        else:
            quantity = total_people
            unit = "人"
            subtotal = unit_price
            teacher_subtotal = round(unit_price * teacher_count, 2) if teacher_count > 0 else 0

        items.append({
            "category": "门票/项目",
            "name": proj["name"],
            "unit_price": unit_price,
            "quantity": quantity,
            "unit": unit,
            "frequency": 1,
            "freq_unit": "次",
            "subtotal": subtotal,
            "teacher_subtotal": teacher_subtotal,
            "remark": proj.get("remark", ""),
        })
```

---

## 9. generate_quote() 主流程变更

重构后的主流程：

```python
def generate_quote(params: dict) -> dict:
    init_tables()

    # --- 解析参数 ---
    tenant_id = params.get('tenant_id', '')
    itinerary_text = params.get('itinerary_text', '')
    ...

    if itinerary_text:
        # 新模式
        parsed = parse_itinerary(itinerary_text)
        resources = resolve_resources(parsed, tenant_id)

        # ... 提取参数（同现有逻辑）...

        attraction_matches = resources.get('attraction_matches', [])
        hotel_stays = resources.get('hotel_stays', [])
        ...
    else:
        # 旧模式（向后兼容）
        ...

    items = []

    # Step 3: 交通（不变）
    items, actual_vehicle_count = calculate_vehicle_cost(...)

    # Step 4: 景点门票 + 游玩项目（重构后的统一入口）
    items = calculate_attraction_cost(
        items, tenant_id, attraction_matches,
        adults, children_half, students, elders,
        total_people, teacher_count=teacher_count
    )

    # Step 5: 住宿（不变）
    ...

    # Step 6-8: 餐饮、导游、其他（不变）
    ...

    # 汇总、导出（不变）
    ...
```

**关键变化**：
- `calculate_attraction_cost()` 替代了原来的 `calculate_ticket_cost()` + `_calculate_ticket_cost_from_kb()`
- 不再需要传入 `attraction_ids`（旧模式）和 `attraction_doc_ids`（新模式）分开处理
- 统一使用 `attraction_matches`（包含 name、doc_id、activities）

---

## 10. Fallback 规则解析

当 LLM 提取失败时的降级策略：

### 10.1 门票 Fallback

解析管道符分隔的门票表文本，按规则提取：

```python
def _fallback_parse_tickets(ticket_table, adults, children_half, students, elders):
    """规则解析门票价格表"""
    lines = [l.strip() for l in ticket_table.split('\n') if l.strip() and '|' in l]

    result = {}
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 3:
            continue

        ticket_keyword = parts[0]  # 如 "成人票"
        price_str = parts[-1]      # 取最后一列（通常是价格）

        # 识别票种
        ticket_type = None
        if '成人' in ticket_keyword:
            ticket_type = 'adult'
        elif '儿童' in ticket_keyword:
            ticket_type = 'child_half'
        elif '学生' in ticket_keyword:
            ticket_type = 'student'
        elif '老人' in ticket_keyword or '老年' in ticket_keyword:
            ticket_type = 'elder'

        if not ticket_type:
            continue

        try:
            price = float(price_str)
        except ValueError:
            continue

        # 取最大价格
        if ticket_type not in result or price > result[ticket_type]:
            result[ticket_type] = price

    return result  # {"adult": 180, "student": 90, ...}
```

### 10.2 项目 Fallback

保留现有的关键词字符重叠匹配，但增加最小重叠阈值：

```python
def _fallback_parse_projects(project_table, activities, total_people):
    """规则解析项目价格表"""
    # ... 类似现有 _fallback_match_projects，但：
    # 1. 最小重叠字符数从 2 提高到 3
    # 2. 增加价格列的智能定位（不只取第3列）
    ...
```

---

## 11. 向后兼容

### 11.1 旧模式（无 itinerary_text）

旧模式通过 `attraction_ids`（数据库模式）或 `attraction_doc_ids`（知识库模式）传入景点，仍然保留。在 `generate_quote()` 中通过 `if itinerary_text` 分支处理。

### 11.2 数据库模式门票计算

`calculate_ticket_cost()` 中通过 `attraction_ids` 查 `bs_travel_quote_attractions` 和 `bs_travel_quote_tickets` 表的逻辑保留在 `attraction.py` 中，作为 `calculate_attraction_cost_db()` 函数。

---

## 12. 实施计划

### Phase 1：文件拆分（低风险，不改逻辑）

| 步骤 | 内容 | 预计时间 |
|------|------|----------|
| 1.1 | 创建 `db.py`，迁移数据库相关代码 | 0.5h |
| 1.2 | 创建 `season.py`，迁移季节判断 | 0.1h |
| 1.3 | 创建 `vehicle.py`，迁移用车计算 | 0.5h |
| 1.4 | 创建 `hotel.py`，迁移住宿计算 | 0.5h |
| 1.5 | 创建 `meal.py`，迁移餐饮计算 | 0.3h |
| 1.6 | 创建 `guide.py`，迁移导游计算 | 0.3h |
| 1.7 | 创建 `other_fees.py`，迁移其他费用 | 0.2h |
| 1.8 | 创建 `excel_export.py`，迁移导出逻辑 | 0.3h |
| 1.9 | 创建 `itinerary_parser.py`，迁移行程解析 | 0.3h |
| 1.10 | 创建 `resource_resolver.py`，迁移资源匹配 | 0.3h |
| 1.11 | 精简 `generate.py`，只保留主流程编排 | 0.5h |
| 1.12 | 验证所有功能正常运行 | 1h |

**Phase 1 预计总时间：4.5h**

### Phase 2：门票+项目重构（核心改动）

| 步骤 | 内容 | 预计时间 |
|------|------|----------|
| 2.1 | 创建 `attraction.py`，实现 `_merge_attractions()` | 0.5h |
| 2.2 | 实现新的 `_llm_extract_tickets()` prompt | 1h |
| 2.3 | 实现新的 `_llm_extract_projects()` prompt | 1h |
| 2.4 | 实现 `_build_ticket_items()` 和 `_build_project_items()` | 0.5h |
| 2.5 | 实现 `_fallback_parse_prices()` | 0.5h |
| 2.6 | 实现 `calculate_attraction_cost()` 统一入口 | 0.5h |
| 2.7 | 修改 `generate_quote()` 调用新接口 | 0.5h |
| 2.8 | 用真实行程测试，调优 prompt | 2h |

**Phase 2 预计总时间：6.5h**

### Phase 3：验证与优化

| 步骤 | 内容 | 预计时间 |
|------|------|----------|
| 3.1 | 用多个不同行程模板测试报价生成 | 2h |
| 3.2 | 边界情况测试（无景点、无项目、单景点多天等） | 1h |
| 3.3 | 对比旧输出，确认无回归 | 1h |

**Phase 3 预计总时间：4h**

**总计：约 15h**

---

## 13. 验收标准

### 13.1 文件拆分

- [ ] `generate.py` 主文件不超过 400 行
- [ ] 每个模块文件不超过 400 行
- [ ] 所有模块可通过 `from scripts.xxx import yyy` 导入
- [ ] `main()` 入口行为不变

### 13.2 门票+项目提取

- [ ] 同一景点只收一次门票（去重）
- [ ] 所有存在的票种都被提取（不缺项）
- [ ] 价格取最大值（挂牌价）
- [ ] 游玩项目只匹配行程中提到的活动
- [ ] 游玩项目不遗漏（除非知识库中确实没有）
- [ ] LLM 失败时 fallback 正常工作
- [ ] 随队老师费用正确计算

### 13.3 向后兼容

- [ ] 旧模式（无 itinerary_text）正常工作
- [ ] 数据库模式门票正常工作
- [ ] Excel 导出格式不变
