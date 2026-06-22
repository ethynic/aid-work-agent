# 开发计划：旅游报价技能 - 酒店局部替换

> **关联设计文档**：[design-travel-quote-hotel-swap.md](../docs/system/design-travel-quote-hotel-swap.md)
> **登记**：[docs/ideas.md](../docs/ideas.md) 第 19 条「旅游报价酒店局部替换」

## 任务清单

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 1 | 编写设计文档 | ✅ 已完成 | `docs/system/design-travel-quote-hotel-swap.md` |
| 2 | 编写开发计划 | ✅ 已完成 | 本文件 |
| 3 | 登记到 ideas.md | ✅ 已完成 | docs/ideas.md「数字员工 / 子智能体」分区第 19 条 |
| 4 | 改造 generate.py 输出 internal_data | ✅ 已完成 | 在 return 中增加 `internal_data` 字段：含 items（原始结构）、hotel_stays、couples、season_type、region_name。`file_path` 保持顶层独立字段，不放入 internal_data。位置：`scripts/generate.py:184-242` |
| 5 | 新增 update_hotel.py 脚本 | ✅ 已完成 | 入口 main() 接收 stdin/命令行 JSON；校验 internal_data 与 hotel_overrides；调用 calculate_hotel_stays 重算住宿行；原位置替换 items 中的住宿行，保持原顺序；调 export_with_template 输出新 Excel；返回与 generate.py 同 schema（file_path 顶层独立） |
| 6 | update_hotel.py 输入校验 | ✅ 已完成 | (a) internal_data 必含 items 数组和 hotel_stays 数组；(b) 每个 override 的 city 必须能在 hotel_stays 找到；(c) hotel_doc_id 必须能查到价格表；(d) 新酒店无有效价格时报错而非生成 0 元报价；(e) 旧住宿行数量必须等于新住宿行数量，否则报错 |
| 7 | update_hotel.py 替换 items 住宿行 | ✅ 已完成 | 原位置替换：遍历 items 找出所有 category=="住宿" 的索引，按顺序用 calculate_hotel_stays 返回的新住宿行替换（不删除不追加）；保留其他 items 顺序；重新汇总 cost_per_person / quote_total / quote_per_person / teacher_total |
| 8 | 单元测试 - 正常单城市替换 | ✅ 已完成 | `test_only_target_hotel_changed` 通过：贵阳酒店被换、其他 items 不变、住宿行原位置保留 |
| 9 | 单元测试 - 多城市一次性替换 | ✅ 已完成 | `test_multi_city_overrides` 通过：hotel_overrides 一次传 2 个城市，两个住宿行同时更新 |
| 10 | 单元测试 - city 未匹配 | ✅ 已完成 | `test_city_not_in_stays_raises` 通过 |
| 11 | 单元测试 - 新酒店无价格表 | ✅ 已完成 | `test_no_price_table_raises` + `test_zero_price_raises` 通过（覆盖"查不到"和"价格表无效"两种） |
| 12 | 单元测试 - 单房差与 teacher_subtotal | ✅ 已完成 | `test_totals_recalculated` 通过：teacher_subtotal 按新酒店价格 × 夜数 × teacher_count 重算 |
| 13 | 单元测试 - 其他边界 | ✅ 已完成 | 新增 `test_hotel_count_mismatch_raises` / `test_missing_internal_data_raises` / `test_empty_overrides_raises` / `test_missing_doc_id_raises` / `test_filepath_top_level` / `test_rows_match_items` / `test_generate_returns_internal_data`。共 13 个用例全部通过 |
| 14 | 更新 SKILL.md | ✅ 已完成 | (a) 输出格式补充 internal_data 字段说明，强调"LLM 不得修改或解读"；(b) 新增「酒店局部更新」章节描述 update_hotel.py 调用方式、hotel_overrides 参数、行为说明 |
| 15 | 更新 SUBAGENT.md | ✅ 已完成 | travel-consultant 阶段三新增「客户想换酒店」小节（4 步流程 + 5 条铁律）；行为约束增加第 12 条"换酒店走 update_hotel.py" |
| 16 | 改造 hotel.py 支持 name_overrides | ✅ 已完成 | `calculate_hotel_stays` 新增 `name_overrides` 参数，允许客户指定酒店名时优先于 retriever 解析 |
| 17 | 端到端实测 | ✅ 已完成 | 用户在真实环境实测通过：generate → 客户通过 knowledge_base_search 选定新酒店 → update_hotel 成功生成新报价单 |
| 18 | 修复 hotel_doc_id 设计缺陷 | ✅ 已完成 | 实测发现 LLM 无法获得 hotel_doc_id（knowledge_base_search 不返回该字段）。改造 update_hotel.py 改用 hotel_name 匹配，内部反查 doc_id。14 个单元测试全通过 |

## 实施顺序

1. **任务 4** 先做（改造 generate.py 输出），这是后续工作的基础
2. **任务 5-7** update_hotel.py 主体实现（含校验和替换逻辑）
3. **任务 8-12** 单元测试覆盖核心场景和边界
4. **任务 13-14** 同步更新 SKILL.md 和 SUBAGENT.md
5. **任务 15** 端到端实测验证整个链路

## 关键技术点

### generate.py 输出 internal_data 字段

`generate.py:184-198` 已经构造了 `internal_data` 字典，目前只用于 `export_with_template`。本次改造：

```python
# 现有（generate.py:184-198）
internal_data = {
    "course_name": course_name,
    "company_name": company_name,
    "region_name": region_name,
    "start_date": start_date,
    "trip_days": trip_days,
    "total_people": total_people,
    "teacher_count": teacher_count,
    "items": items,
    "cost_per_person": cost_per_person,
    "teacher_total": teacher_total,
    "single_supplement": single_supplement,
    "quote_per_person": quote_per_person,
    "quote_total": quote_total,
}

# 改造后：增加 update_hotel 需要的字段
internal_data = {
    ...现有字段...,
    "couples": couples,                  # 单房差计算依赖
    "season_type": season_type,          # 季节类型（虽不重算但保留）
    "hotel_stays": hotel_stays or [],    # 酒店住宿清单（含 city/area/nights/hotel_doc_id）
}

# return 中追加
return {
    ...现有 rows/合计/file_path...,
    "internal_data": internal_data,
}
```

### update_hotel.py 核心逻辑

```python
def update_hotel(params):
    internal = params["internal_data"]
    overrides = params["hotel_overrides"]  # [{city, hotel_doc_id, hotel_name?}]

    items = internal["items"]
    hotel_stays = internal["hotel_stays"]
    couples = internal["couples"]
    total_people = params["total_people"] or internal.get("total_people")
    teacher_count = params["teacher_count"] or internal.get("teacher_count")

    # 1. 校验：每个 override 的 city 必须在 hotel_stays 中
    stay_by_city = {s["city"]: s for s in hotel_stays}
    for ov in overrides:
        if ov["city"] not in stay_by_city:
            raise ValueError(f"未找到城市 {ov['city']}，无法替换酒店")

    # 2. 用 overrides 更新 hotel_stays
    for ov in overrides:
        stay_by_city[ov["city"]]["hotel_doc_id"] = ov["hotel_doc_id"]
        if ov.get("hotel_name"):
            stay_by_city[ov["city"]]["hotel_name"] = ov["hotel_name"]

    # 3. 用 calculate_hotel_stays 重算住宿行（复用 hotel.py，需支持 name override）
    hotel_items, single_supplement = calculate_hotel_stays(
        [], tenant_id, hotel_stays, total_people, teacher_count, couples,
        name_overrides={ov["city"]: ov["hotel_name"] for ov in overrides if ov.get("hotel_name")}
    )
    # hotel_items 顺序与 hotel_stays 一致

    # 4. 原地替换 items 中的住宿行（保持原顺序）
    old_hotel_indices = [i for i, it in enumerate(items) if it.get("category") == "住宿"]
    if len(old_hotel_indices) != len(hotel_items):
        raise ValueError(f"住宿行数量不一致：旧 {len(old_hotel_indices)} 行，新 {len(hotel_items)} 行")

    new_items = list(items)  # 浅拷贝保持顺序
    for idx, new_hotel_item in zip(old_hotel_indices, hotel_items):
        new_items[idx] = new_hotel_item  # 原位置替换

    # 5. 重新汇总
    cost_per_person = round(sum(it.get("subtotal", 0) for it in new_items), 2)
    quote_total = round(cost_per_person * total_people, 2)
    quote_per_person = round(quote_total / total_people, 2) if total_people > 0 else 0
    teacher_total = round(sum(it.get("teacher_subtotal", 0) for it in new_items), 2)

    # 6. 导出新 Excel（internal_data 不含 file_path，file_path 是顶层字段）
    new_internal_data = {
        **{k: v for k, v in internal.items() if k != "file_path"},
        "items": new_items,
        "cost_per_person": cost_per_person,
        "quote_per_person": quote_per_person,
        "quote_total": quote_total,
        "teacher_total": teacher_total,
        "single_supplement": single_supplement,
        "hotel_stays": hotel_stays,  # 已含新 doc_id
    }
    file_path = export_with_template(new_internal_data, params.get("template_path"))

    # 7. 返回与 generate.py 同 schema：file_path 顶层独立，internal_data 平级
    return build_response(new_internal_data, file_path)
```

### calculate_hotel_stays 支持 override

`hotel.py:calculate_hotel_stays` 当前从 `hotel_retriever.get_hotel_info` 取酒店名。需小改：

```python
def calculate_hotel_stays(items, tenant_id, hotel_stays, total_people, teacher_count, couples, name_overrides=None):
    """
    name_overrides: {city: hotel_name} 可选，客户指定酒店名时使用
    """
    ...
    for stay in hotel_stays:
        city = stay.get("city", "")
        ...
        # 取酒店名：优先 name_overrides，否则从 retriever 取
        if name_overrides and city in name_overrides:
            hotel_name = name_overrides[city]
        else:
            attraction_info = retriever.get_hotel_info(doc_id)
            hotel_name = f"{city}酒店"
            ...现有解析逻辑...
```

## 验证标准

| 验证项 | 通过标准 |
|--------|---------|
| generate.py 输出含 internal_data | 返回 JSON 中有 internal_data 字段，含 items 原始结构、hotel_stays、couples、season_type、region_name |
| file_path 在顶层 | data.file_path 是顶层独立字段，不在 internal_data 内 |
| internal_data 与 rows 不重复字段 | rows 是中文 key 视图，items 是英文 key 内部结构，无字段重复 |
| update_hotel 单城市替换 | 住宿行仅目标城市那行金额变化，其他 items 不变，新 file_path 生成 |
| update_hotel 保持原顺序 | 住宿行原位置不变（不挪到末尾），便于客户对比新旧报价 |
| update_hotel 多城市替换 | hotel_overrides 含 N 个城市，N 行住宿行同时更新 |
| city 未匹配报错 | override 中 city 不在 hotel_stays，抛错并退出（不生成 0 元报价单） |
| 新酒店无价格报错 | hotel_doc_id 查不到价格表，抛错退出 |
| 单房差正确 | couples>0 且剩余奇数时，单房差按新酒店价格 × 夜数 ÷ 人数 |
| teacher_subtotal 正确 | 住宿行 teacher_subtotal = 新酒店价格 × 夜数 × teacher_count |
| SUBAGENT.md 引导清晰 | 含"先确认城市 → 列酒店 → 调 update_hotel"流程，铁律明确 |
| 端到端实测 | 真实行程 generate → 换酒店 → update_hotel 全链路跑通 |
