# 旅游子智能体设计文档

> 版本: v5.1 | 创建: 2026-05-09 | 状态: 待审核

## 1. 设计目标

设计一套**通用的旅游行业 AI 顾问系统**，核心思路：

1. **能在 SUBAGENT.md 里说清楚的，就不新建 skill** — 行程规划、对话风格、路线策略都由 LLM 在子智能体说明中直接执行
2. **只有需要精确程序化操作的才用 skill** — 报价生成（查库+计算+导出 Excel）封装为 `quote-generate` skill，仅注册给旅游子智能体
3. **租户定制统一用 extra.md** — 每个子智能体每个租户一个 extra.md 文件，包含该租户的个性化配置（对话风格、路线策略、模板路径等），作为 system prompt 的一部分注入，优先级高于 SUBAGENT.md
4. **定价数据存数据库** — 结构化的价格数据需要查询和计算，不能放 prompt

可配置模块：

| 模块 | 数据存储 | 运行方式 |
|------|---------|---------|
| 定价数据（车辆/门票/酒店等） | 10 张 `bs_travel_quote_*` 表 | `quote-generate` skill 内部查询 |
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

### 3.1 核心设计决策：不使用 region_id 外键

**问题**：用户说"贵州"、"苏州"、"重庆"，这些是省名、城市名、直辖市名，简称、别名、省略都很常见。如果定价数据用 `region_id` 关联，就需要 LLM 或程序把自然语言精确映射到某个 ID——这不可靠。

**方案**：不用 `region_id` 做外键关联。

- **区域表 `bs_travel_quote_regions` 独立存在**，但只作为分类标签和前端展示，不作为其他表的外键
- **定价数据通过"区域名称"文本字段做宽松匹配**。各表中用 `region_name TEXT` 存区域名（如"贵阳"、"黔南"），可以为空（空值表示全国通用）
- **quote-generate 脚本查库时**：先从行程参数中获取 region_name，用 SQL `region_name IS NULL OR region_name = ?` 查询，优先取有区域匹配的记录，没有则取全国通用的
- **省份/城市层级问题自然消解**：贵阳的景点 region_name 写"贵阳"，天眼景区 region_name 写"黔南"，不需要统一层级。用户说"贵州研学"，脚本查 attractions 时按 `region_name IN ('贵阳', '黔南', ...)` 或直接不按区域过滤，按景点名称匹配

**区域匹配逻辑在 skill 脚本中处理**，不需要 LLM 做 ID 映射：

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

### 3.2 总体 ER 关系

```
bs_travel_quote_regions (区域/城市 — 独立分类表，不作为外键)

bs_travel_quote_attractions (景点)       → bs_travel_quote_tickets (门票)
bs_travel_quote_hotels (酒店)            → bs_travel_quote_rooms (房型)
bs_travel_quote_vehicles (车辆)
bs_travel_quote_meals (餐标)
bs_travel_quote_guides (导游)
bs_travel_quote_fees (其他费用)
bs_travel_quote_seasons (淡旺季)
```

各表通过 `region_name TEXT` 标记所属区域（可为空），不通过外键关联。

### 3.3 区域表 `bs_travel_quote_regions`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| name | TEXT NOT NULL | 区域名称，如"贵阳"、"黔南"、"苏州" |
| aliases | TEXT | 别名/简称，逗号分隔，如"筑"、"贵阳市" |
| parent_name | TEXT | 上级区域名称，如贵阳的 parent_name 是"贵州" |
| level | TEXT | `province`省 / `city`市 / `district`区县 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**区域表的作用**：
1. **前端管理页面的分组筛选** — 按省/市组织定价数据
2. **skill 脚本的辅助查询** — 当用户说"贵州"时，脚本可以从区域表查到所有 `parent_name='贵州'` 的城市名，然后用这些城市名去查定价数据
3. **LLM 的目的地列表** — 告诉 LLM 该旅行社覆盖哪些区域

**区域匹配流程**：

```
用户说 "贵州研学"
  → LLM 从对话中提取目的地："贵州"
  → quote-generate 脚本：
      1. 查 regions 表: SELECT name FROM ... WHERE name='贵州' OR aliases LIKE '%贵州%'
         → 找到 level='province', name='贵州'
      2. 查子区域: SELECT name FROM ... WHERE parent_name='贵州'
         → 得到 ['贵阳', '黔南', '遵义', '安顺', '凯里', ...]
      3. 用这些名称查景点、酒店、餐标等
      → attractions: WHERE region_name IN ('贵阳','黔南',...) OR region_name IS NULL
```

### 3.3 车辆与交通费用 `bs_travel_quote_vehicles`

**设计思路**：根据团队人数，自动选择最经济的车型组合。车型按座位数分层，每种车型在特定区域有对应的日租金。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| region_name | TEXT | 所属区域名称（为空表示全国通用），如"贵阳" |
| vehicle_type | TEXT NOT NULL | 车型分类：`business`商务车 / `coaster`考斯特 / `minibus`中巴 / `bus`大巴 / `large_bus`大型大巴 |
| vehicle_type_label | TEXT | 车型显示名，如"别克GL8商务车" |
| seats_min | INT NOT NULL | 最小座位数 |
| seats_max | INT NOT NULL | 最大座位数 |
| daily_rate | DECIMAL(10,2) NOT NULL | 日租金（含基础8小时100公里） |
| overtime_rate | DECIMAL(10,2) | 超时费（元/小时） |
| overkm_rate | DECIMAL(10,2) | 超公里费（元/公里） |
| driver_meal_allowance | DECIMAL(10,2) | 司机餐补（元/天） |
| driver_accommodation | DECIMAL(10,2) | 司机住宿费（元/晚） |
| season_type | TEXT DEFAULT 'default' | 季节类型：`default`默认 / `peak`旺季 / `shoulder`平季 / `off`淡季 |
| effective_from | DATE | 价格生效日期 |
| effective_to | DATE | 价格失效日期 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| sort_order | INT DEFAULT 0 | 排序 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置车型数据**：

| vehicle_type | vehicle_type_label | seats_min | seats_max | 日租金参考 |
|-------------|-------------------|-----------|-----------|-----------|
| business | 商务车（别克GL8） | 5 | 7 | 600-1000 |
| business | 商务车（奔驰威霆） | 7 | 9 | 800-1500 |
| coaster | 考斯特 | 18 | 23 | 1000-2000 |
| minibus | 中巴 | 24 | 35 | 1200-2000 |
| bus | 大巴 | 37 | 45 | 1500-2500 |
| large_bus | 大型大巴 | 49 | 61 | 2000-3500 |

**车型推荐算法**：

```python
def recommend_vehicle(people_count: int, vehicles: list) -> list:
    """
    根据团队人数推荐最优车型组合。
    优先选择单辆能装下的车型；人数超过最大车型时，计算多辆组合。
    """
    single_options = [v for v in vehicles if v.seats_max >= people_count]
    if single_options:
        best = min(single_options, key=lambda v: v.seats_max)
        return [{"vehicle": best, "count": 1}]

    # 多辆组合：尽量用大车减少车辆数，同时考虑混合组合是否更便宜
    largest = max(vehicles, key=lambda v: v.seats_max)
    best_combo, best_cost = None, float('inf')

    for v in sorted(vehicles, key=lambda x: x.seats_max, reverse=True):
        full_count = people_count // v.seats_max
        remainder = people_count % v.seats_max

        if remainder == 0:
            cost = full_count * v.daily_rate
            if cost < best_cost:
                best_cost, best_combo = cost, [{"vehicle": v, "count": full_count}]
        else:
            for v2 in vehicles:
                if v2.seats_max >= remainder:
                    cost = full_count * v.daily_rate + v2.daily_rate
                    if cost < best_cost:
                        best_cost, best_combo = cost, [
                            {"vehicle": v, "count": full_count},
                            {"vehicle": v2, "count": 1},
                        ]
                    break
            cost = (full_count + 1) * v.daily_rate
            if cost < best_cost:
                best_cost, best_combo = cost, [{"vehicle": v, "count": full_count + 1}]

    return best_combo or [{"vehicle": largest, "count": math.ceil(people_count / largest.seats_max)}]
```

### 3.4 景点与门票 `bs_travel_quote_attractions` + `bs_travel_quote_tickets`

门票的核心复杂性在于**票种多样**（成人/儿童/学生/老人/团体/军人等），采用景点主表 + 门票价格明细表的二级结构。

**景点主表 `bs_travel_quote_attractions`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| region_name | TEXT | 所属区域名称，如"黔南" |
| name | TEXT NOT NULL | 景点名称 |
| category | TEXT | 景点分类：`natural`自然风光 / `cultural`人文历史 / `theme_park`主题乐园 / `museum`博物馆 / `research`研学基地 |
| address | TEXT | 地址 |
| open_time | TEXT | 开放时间描述 |
| visit_duration_hours | DECIMAL(4,1) | 建议游览时长（小时） |
| internal_transport_name | TEXT | 景区内交通名称（如"环保车"、"索道"） |
| internal_transport_price | DECIMAL(10,2) | 景区内交通费用（元/人） |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| sort_order | INT DEFAULT 0 | 排序 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**门票价格表 `bs_travel_quote_tickets`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| attraction_id | INT NOT NULL | 关联景点 |
| ticket_type | TEXT NOT NULL | 票种编码 |
| ticket_type_label | TEXT NOT NULL | 票种显示名 |
| retail_price | DECIMAL(10,2) NOT NULL | 挂牌价（元/人） |
| agency_price | DECIMAL(10,2) | 旅行社协议价（元/人） |
| group_price | DECIMAL(10,2) | 团体价（元/人） |
| group_min_people | INT | 团体价最低人数 |
| season_type | TEXT DEFAULT 'default' | 季节类型 |
| effective_from | DATE | 生效日期 |
| effective_to | DATE | 失效日期 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置票种**：

| ticket_type | ticket_type_label | 价格规则 |
|-------------|-----------------|---------|
| adult | 成人票 | 全价 |
| child_free | 儿童免票 | 身高1.2m以下或6周岁以下 |
| child_half | 儿童优惠票 | 身高1.2-1.4m或6-18周岁，通常半价 |
| student | 学生票 | 全日制本科及以下，通常半价 |
| elder_half | 老人半价票 | 60-64周岁 |
| elder_free | 老人免票 | 65周岁以上 |
| military | 军人/优抚票 | 通常免票 |
| group | 团体票 | 达到团体最低人数后适用 |

### 3.5 酒店与房型 `bs_travel_quote_hotels` + `bs_travel_quote_rooms`

住宿的核心问题是**排房**：根据团队人数和人员构成，自动分配房型并计算费用。

**酒店主表 `bs_travel_quote_hotels`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| region_name | TEXT | 所属区域名称，如"贵阳" |
| name | TEXT NOT NULL | 酒店名称 |
| star_rating | TEXT | 星级：`economy`经济型 / `comfort`舒适型(3星) / `premium`高档型(4星) / `luxury`豪华型(5星) |
| star_rating_label | TEXT | 星级显示名 |
| address | TEXT | 地址 |
| contact_phone | TEXT | 联系电话 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| sort_order | INT DEFAULT 0 | 排序 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**房型价格表 `bs_travel_quote_rooms`**：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| hotel_id | INT NOT NULL | 关联酒店 |
| room_type | TEXT NOT NULL | 房型编码 |
| room_type_label | TEXT NOT NULL | 房型显示名 |
| max_occupancy | INT NOT NULL | 最大入住人数 |
| bed_count | INT | 床位数 |
| retail_price | DECIMAL(10,2) NOT NULL | 门市价（元/间/晚） |
| agency_price | DECIMAL(10,2) | 旅行社协议价（元/间/晚） |
| includes_breakfast | BOOLEAN DEFAULT FALSE | 是否含早 |
| breakfast_count | INT DEFAULT 0 | 含早餐份数 |
| extra_bed_rate | DECIMAL(10,2) | 加床费用（元/晚） |
| season_type | TEXT DEFAULT 'default' | 季节类型 |
| effective_from | DATE | 生效日期 |
| effective_to | DATE | 失效日期 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置房型**：

| room_type | room_type_label | max_occupancy | 说明 |
|-----------|----------------|---------------|------|
| standard | 标准间/双床房 | 2 | 团队默认房型 |
| double | 大床房/双人间 | 2 | 情侣/夫妻 |
| triple | 三人间 | 3 | 奇数团队 |
| family | 家庭房 | 3-4 | 亲子出行 |
| suite | 套房 | 2 | 高端团 |
| single | 单人间 | 1 | 单人 |

### 3.6 餐饮/餐标 `bs_travel_quote_meals`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| region_name | TEXT | 所属区域名称（为空表示全国通用） |
| meal_tier | TEXT NOT NULL | 餐标档次编码 |
| meal_tier_label | TEXT NOT NULL | 餐标档次显示名 |
| meal_type | TEXT NOT NULL | 餐类：`breakfast`早餐 / `lunch`午餐 / `dinner`晚餐 / `pack_lunch`路餐 |
| meal_type_label | TEXT NOT NULL | 餐类显示名 |
| price_per_person | DECIMAL(10,2) NOT NULL | 每人每餐价格 |
| pax_per_table | INT DEFAULT 10 | 每桌人数 |
| dishes_standard | TEXT | 菜品标准描述（如"八菜一汤"） |
| season_type | TEXT DEFAULT 'default' | 季节类型 |
| effective_from | DATE | 生效日期 |
| effective_to | DATE | 失效日期 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置餐标**：

| meal_tier | meal_tier_label | 价格参考 |
|-----------|----------------|---------|
| economy | 经济餐 | 15-20 |
| standard | 标准餐 | 25-35 |
| quality | 品质餐 | 40-50 |
| premium | 高餐标 | 70-100 |
| luxury | 豪华餐标 | 120+ |

### 3.7 导游/领队费用 `bs_travel_quote_guides`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| region_name | TEXT | 所属区域名称（为空表示全国通用） |
| guide_type | TEXT NOT NULL | 导游类型编码 |
| guide_type_label | TEXT NOT NULL | 导游类型显示名 |
| guide_level | TEXT DEFAULT 'standard' | `junior`初级 / `standard`标准 / `senior`高级 / `premium`十佳 |
| guide_level_label | TEXT | 级别显示名 |
| billing_method | TEXT DEFAULT 'daily' | `daily`按天 / `per_trip`按团 |
| daily_rate | DECIMAL(10,2) | 日薪 |
| trip_rate | DECIMAL(10,2) | 整团费用 |
| language_premium | DECIMAL(10,2) DEFAULT 0 | 外语加价（元/天） |
| peak_season_multiplier | DECIMAL(3,2) DEFAULT 1.00 | 旺季上浮倍率 |
| season_type | TEXT DEFAULT 'default' | 季节类型 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置导游类型**：

| guide_type | guide_type_label | 日薪参考 |
|-----------|-----------------|---------|
| local | 地接导游 | 200-500 |
| national | 全陪导游 | 100-300 |
| research | 研学导师 | 400-800 |
| driver_guide | 司兼导 | 300-600 |

### 3.8 保险与其他固定费用 `bs_travel_quote_fees`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| fee_name | TEXT NOT NULL | 费用名称 |
| fee_category | TEXT NOT NULL | 费用分类 |
| billing_method | TEXT NOT NULL | `per_person`按人 / `per_person_per_day`按人天 / `per_trip`按团 / `per_vehicle_per_day`按车天 |
| unit_price | DECIMAL(10,2) NOT NULL | 单价 |
| is_mandatory | BOOLEAN DEFAULT FALSE | 是否必含 |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| sort_order | INT DEFAULT 0 | 排序 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置费用项**：

| fee_category | fee_name | billing_method | 单价参考 |
|-------------|----------|---------------|---------|
| insurance | 旅行社责任险 | per_trip | 含在团费中 |
| insurance | 旅游意外险 | per_person | 5-30 |
| insurance | 景点意外险 | per_person | 1-10 |
| service | 综合服务费 | per_person_per_day | 10-20 |
| service | 每日饮用水 | per_person_per_day | 3-5 |
| transport | 司机餐补 | per_vehicle_per_day | 50-100 |
| transport | 司机住宿费 | per_vehicle_per_day | 150-200 |
| transport | 空驶费 | per_trip | 视距离 |
| other | 活动物料费 | per_person | 10-30 |

### 3.9 季节/淡旺季配置 `bs_travel_quote_seasons`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| tenant_id | TEXT | 租户ID |
| season_type | TEXT NOT NULL | 季节类型编码 |
| season_type_label | TEXT NOT NULL | 季节类型显示名 |
| start_date | DATE NOT NULL | 开始日期 |
| end_date | DATE NOT NULL | 结束日期 |
| price_multiplier | DECIMAL(3,2) DEFAULT 1.00 | 价格倍率（相对默认价格） |
| is_active | BOOLEAN DEFAULT TRUE | 是否启用 |
| remark | TEXT | 备注 |
| created_at | TIMESTAMP DEFAULT NOW() | 创建时间 |

**预置季节类型**：

| season_type | season_type_label | 时间范围（参考） | 倍率 |
|-------------|-----------------|---------------|------|
| default | 默认/全年 | 1月1日-12月31日 | 1.00 |
| peak | 旺季 | 7月1日-8月31日, 10月1日-7日 | 1.20-1.50 |
| shoulder | 平季 | 4月-6月, 9月 | 1.00 |
| off | 淡季 | 11月-次年3月 | 0.80-0.90 |
| holiday | 节假日 | 春节/国庆 | 1.30-1.80 |

### 3.10 利润/加价配置

利润/加价配置写入 extra.md（见第 7 节），不需要数据库表。

---

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

### 5.1 输入参数

```python
class QuoteRequest:
    tenant_id: str
    region_name: str               # 区域名称，如"贵州"、"贵阳"
    total_people: int
    adults: int
    children: int               # 6岁以下免票
    children_half: int           # 6-18岁半票
    students: int
    elders: int                  # 65岁以上
    couples: int = 0
    families: int = 0
    trip_days: int
    start_date: date
    attractions: list[int]
    hotel_id: int
    meal_tier: str
    guide_type: str
    vehicle_count: int = None
    include_insurance: bool = True
    profit_rate: float = None
```

### 5.2 计算流程

```
Step 1: 确定季节 → bs_travel_quote_seasons
Step 2: 交通 → bs_travel_quote_vehicles → 车型推荐 → 日租金 × 天数 ÷ 人数
Step 3: 门票 → bs_travel_quote_tickets → 按票种人数累加
Step 4: 住宿 → bs_travel_quote_rooms → 排房 → 房价 × 间数 × 晚数 + 单房差
Step 5: 餐饮 → bs_travel_quote_meals → 餐标 × 人数 × 餐数
Step 6: 导游 → bs_travel_quote_guides → 日薪 × 天数 × 旺季倍率
Step 7: 其他 → bs_travel_quote_fees → 按计费方式
Step 8: 汇总 → 人均成本 × (1 + 利润率)
```

### 5.3 输出结构

```python
class QuoteResult:
    course_name: str             # 行程名称（如"超级贵州研学"）
    company_name: str            # 公司名称
    region_name: str
    start_date: date
    trip_days: int
    total_people: int
    items: list[QuoteItem]
    total_cost: decimal          # 整团成本
    cost_per_person: decimal     # 人均成本
    single_supplement: decimal   # 单房差
    profit_rate: float
    quote_per_person: decimal    # 人均报价
    quote_total: decimal         # 整团报价

class QuoteItem:
    category: str
    name: str
    unit_price: decimal
    quantity: int
    unit: str
    frequency: int
    freq_unit: str
    subtotal: decimal
    remark: str
```

> `QuoteResult` 和 `QuoteItem` 的字段名即模板变量名（见第 4.3 节），一一对应，无需额外映射。

---

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

```
LLM 调用 skill_execute({
  skill: "quote-generate",
  command: "python scripts/generate.py",
  content: JSON 行程参数
})
    │
    ▼
┌──────────────────────────────────────────────────┐
│  generate.py 脚本内部流程（纯 Python，不再调用 LLM）│
│                                                    │
│  1. 解析行程参数                                    │
│     - 人数、天数、出发日期、景点ID列表               │
│     - 酒店ID、餐标、导游类型、利润率                 │
│                                                    │
│  2. 确定季节 → 查 bs_travel_quote_seasons           │
│                                                    │
│  3. 查询各项定价数据                                │
│     - vehicles → 车型推荐 + 日租金                  │
│     - tickets  → 按票种统计人数                     │
│     - rooms    → 排房 + 房价                        │
│     - meals    → 餐标 × 人数 × 餐数                 │
│     - guides   → 日薪 × 天数                        │
│     - fees     → 按计费方式计算                      │
│                                                    │
│  4. 计算各项费用小计 + 人均 + 整团                   │
│                                                    │
│  5. 从 extra.md 读取模板路径 → 加载 Excel 模板文件    │
│     → 填充数据 → 输出 xlsx                          │
│                                                    │
│  6. 返回 JSON: { items, total, file_path }         │
└──────────────────────────────────────────────────┘
    │
    ▼
LLM 收到报价明细 + 文件路径
  → 组织文字回复给用户
  → 调用 register_download_file 注册下载
```

### 6.5 Skill 输入参数

LLM 通过 `skill_execute` 的 `content` 参数传入 JSON：

```json
{
  "tenant_id": "tenant_xxx",
  "region_name": "贵阳",
  "total_people": 30,
  "adults": 25,
  "children": 0,
  "children_half": 5,
  "students": 0,
  "elders": 0,
  "couples": 2,
  "trip_days": 6,
  "start_date": "2026-07-01",
  "attraction_ids": [1, 3, 5, 7],
  "hotel_id": 2,
  "meal_tier": "standard",
  "guide_type": "research",
  "vehicle_count": null,
  "include_insurance": true,
  "profit_rate": null,
  "course_name": "超级贵州研学"
}
```

> LLM 从对话中收集这些参数，所有信息齐备后才调用 skill。参数含义在 SUBAGENT.md 中说明。

### 6.6 Skill 输出结构

```json
{
  "success": true,
  "data": {
    "items": [
      {"category": "用车", "name": "大巴(45座)", "unit_price": 1800, "quantity": 1, "unit": "辆", "frequency": 6, "freq_unit": "天", "subtotal": 360.00, "remark": "含司机餐补"},
      {"category": "门票", "name": "天眼观景台(成人)", "unit_price": 88, "quantity": 25, "unit": "人", "frequency": 1, "freq_unit": "次", "subtotal": 73.33, "remark": "协议价"},
      {"category": "住宿", "name": "贵阳4钻酒店(标间)", "unit_price": 320, "quantity": 15, "unit": "间", "frequency": 5, "freq_unit": "晚", "subtotal": 266.67, "remark": "两人一间"}
    ],
    "total_cost": 58000.00,
    "cost_per_person": 1933.33,
    "profit_rate": 0.15,
    "quote_per_person": 2223.33,
    "quote_total": 66700.00,
    "file_path": "/tmp/quote_20260709_xxx.xlsx"
  }
}
```

> LLM 收到后，用自然语言组织回复给用户，展示报价明细，并注册下载文件。

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

## 9. 数据库表汇总

共 10 张业务表 + 1 个专属 skill + 1 套租户定制文件：

**定价数据（10张，由 quote-generate skill 内部查询）**：

| 表名 | 说明 |
|------|------|
| `bs_travel_quote_regions` | 区域/城市 |
| `bs_travel_quote_vehicles` | 车型与包车价格 |
| `bs_travel_quote_attractions` | 景点主表 |
| `bs_travel_quote_tickets` | 门票价格明细 |
| `bs_travel_quote_hotels` | 酒店主表 |
| `bs_travel_quote_rooms` | 房型与价格 |
| `bs_travel_quote_meals` | 餐标价格 |
| `bs_travel_quote_guides` | 导游费用 |
| `bs_travel_quote_fees` | 其他固定费用 |
| `bs_travel_quote_seasons` | 淡旺季配置 |

**租户定制（文件，不建表）**：

| 文件 | 说明 |
|------|------|
| `storage/subagents/travel-consultant/extra_<tenant_id>.md` | 租户定制配置（对话风格、路线策略、报价模板路径等） |

**专属 Skill**：

| Skill | 说明 |
|-------|------|
| `quote-generate` | 报价全流程：查库 → 计算 → 模板导出 Excel |

**已删除的表**：
- ~~`bs_travel_quote_templates`~~ — 模板路径写在 extra.md 中
- ~~`bs_travel_route_strategies`~~ — 策略用 Markdown 写在 extra.md 中
- ~~`bs_travel_persona_configs`~~ — 人设用 Markdown 写在 extra.md 中

---

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

1. **查库**：根据 `region_name`、`attraction_ids`、`hotel_id` 等参数，查询对应的 `bs_travel_quote_*` 表获取定价数据
2. **计算**：按照第 5 节的计算流程，自动完成车型推荐、排房、费用计算
3. **导出**：加载报价模板，填充数据，生成 xlsx 文件
4. **返回**：返回报价明细 JSON + 文件路径

> 整个过程是纯 Python 逻辑，不再调用 LLM。定价数据查询、费用计算、Excel 生成都由脚本完成，保证数据准确性。

#### 10.2.3 调用全景流程

```
用户消息: "帮我做个30人贵州6天的报价"
    │
    ▼
Agent.process_message()
    │
    ├─ 第1-5轮: LLM 通过对话收集报价所需信息
    │    （人数、天数、景点偏好、住宿要求等）
    │    必要时调用 knowledge_base_search 了解景点信息
    │
    ├─ 第6轮: 信息齐备后，LLM 调用 skill_execute({skill: "quote-generate", ...})
    │    │
    │    ▼ generate.py 内部流程
    │    ├─ 查 bs_travel_quote_seasons → 确定季节
    │    ├─ 查 bs_travel_quote_vehicles → 车型推荐
    │    ├─ 查 bs_travel_quote_tickets → 门票价格
    │    ├─ 查 bs_travel_quote_rooms → 排房计算
    │    ├─ 查 bs_travel_quote_meals → 餐标
    │    ├─ 查 bs_travel_quote_guides → 导游费用
    │    ├─ 查 bs_travel_quote_fees → 其他费用
    │    ├─ 计算汇总 → 人均 + 整团
    │    └─ 加载模板 → 生成 Excel → 返回结果
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

| 优先级 | 模块 | 工作量 | 原因 |
|--------|------|--------|------|
| P0 | extra.md 租户定制系统 | 小 | 改 agent prompt 拼接逻辑 + 前端编辑器，所有租户定制立即可用 |
| P1 | 定价数据表 + 数据导入 | 中 | 核心数据层 |
| P2 | quote-generate skill | 大 | 查库+计算+模板导出全流程，核心 skill |
