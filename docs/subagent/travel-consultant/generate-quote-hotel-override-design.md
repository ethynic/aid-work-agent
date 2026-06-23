# 报价单生成支持指定酒店设计

> 反向关联：[ideas.md 第 26 条](../../ideas.md) · [开发计划](../../plans/plan-generate-quote-hotel-override.md)

## 背景与动机

当前 `generate.py` 在生成报价单时，酒店是通过向量检索自动匹配的：

1. LLM 从行程文本解析出 `hotel_stays: [{city, area, nights}]` 和 `hotel_preference`（偏好描述，如"4钻"）
2. `resolve_resources()` 用 `"{city} {area} 酒店 {hotel_pref}"` 作为 query 向量检索，**top_k=1**，给每个 stay 唯一一个 `hotel_doc_id`
3. `calculate_hotel_stays()` 按 doc_id 查价、算费

问题：
- 匹配随机性较强，同一个行程多次生成报价，酒店可能不一致
- 客户已有酒店偏好（或上一版报价已修改过酒店）时，重新调整行程后酒店又被换掉了
- 当前 Agent 会先调 `generate.py`、发现酒店不对、再调一次 `update_hotel.py` 补救 —— 浪费 token、增加调用链路、降低响应速度
- **行程文本里明确写了酒店名（如"入住蝴蝶妈妈隐田乡舍美宿"）时，Agent 只把它塞进 `itinerary_text` 却不传 `hotel_overrides`，导致报价单上的酒店与行程不一致**

## 目标

在 `generate.py` 增加一个**可选**参数 `hotel_overrides`，复用 `update_hotel.py` 已有的酒店反查/校验逻辑，让"行程中明确的酒店"在一开始就生效。

**关键约束**：
- `hotel_overrides` 是**可选**参数，不传时行为完全不变（向后兼容）
- 客户可能只指定**部分**城市的酒店；未指定的城市走原有 LLM 推荐逻辑，**不能少**
- 复用 `update_hotel.py` 中"按酒店名反查 doc_id"的整套逻辑（包括歧义、查不到、无价格的报错口径），避免两套实现

## 数据结构

`hotel_overrides` 沿用 `update_hotel.py` 现有结构：

```json
[
  {"city": "贵阳", "hotel_name": "贵阳凯宾斯基酒店"},
  {"city": "荔波", "hotel_name": "荔波四季花园酒店", "room_type": "大床房"}
]
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `city` | string | 是 | 城市名，不带"县/市"后缀（与 `hotel_stays.city` 口径一致） |
| `hotel_name` | string | 是 | 客户选定的酒店**完整名称**，从 `knowledge_base_search` 返回的"酒店名称：XXX"字段取得；agent 不传 doc_id |
| `room_type` | string | 否 | 客户指定的房型（如"大床房"、"亲子房"）。传入时候选价格行先按房型模糊包含匹配过滤；该房型在价格表中无任何匹配行时**报错**（列出实际可用房型），不静默回退到标准间 |

## 调用示例

```json
{
  "tenant_id": "...",
  "itinerary_text": "## 贵州天眼+荔波5天4晚研学行程\n...",
  "hotel_overrides": [
    {"city": "贵阳", "hotel_name": "贵阳凯宾斯基酒店"}
  ]
}
```

上例中，贵阳的酒店用客户指定的"贵阳凯宾斯基酒店"，其他城市（如平塘、荔波）仍走 LLM 默认推荐。

## 实现方案

### 1. 抽取共享函数：`resolve_hotel_overrides()`

将 `update_hotel.py` 中第 67~120 行那段"反查 doc_id + 覆写 hotel_stays"的逻辑，抽到 `hotel.py` 中作为公共函数：

```python
# hotel.py
def resolve_hotel_overrides(
    tenant_id: str,
    hotel_stays: list,
    overrides: list,
) -> dict:
    """按酒店名反查 doc_id 并覆写到 hotel_stays 对应城市。

    Args:
        tenant_id: 租户ID
        hotel_stays: LLM 解析出的住宿清单，元素会被原地修改 hotel_doc_id 字段
        overrides: [{city, hotel_name}]，客户指定的酒店

    Returns:
        name_overrides: {city: hotel_name}，用于传给 calculate_hotel_stays

    Raises:
        ValueError: city 未匹配 / 酒店名歧义 / 查不到 / 价格表无效
    """
```

逻辑要点（与 `update_hotel.py` 现状一致）：
1. 校验每个 override 的 `city` 在 `hotel_stays` 中存在
2. 用 `HotelRetriever.search_by_name()` 查询，精确比对"酒店名称"字段避免模糊误命中
3. 歧义（多条）/查不到/价格表无效时立即报错
4. 写回 `stay['hotel_doc_id']`，收集 `name_overrides`

### 2. `generate.py` 接入

在 `resolve_resources()` 返回之后、`calculate_hotel_stays()` 之前插入 merge 步骤：

```python
# generate.py - 在 Step 6 之前插入
overrides = params.get('hotel_overrides') or []
name_overrides = {}
if overrides and hotel_stays:
    from hotel import resolve_hotel_overrides
    name_overrides = resolve_hotel_overrides(tenant_id, hotel_stays, overrides)
    logger.info(f"[travel-quote] 应用酒店指定: {name_overrides}")

# Step 6: 住宿
if hotel_stays:
    items, single_supplement = calculate_hotel_stays(
        items, tenant_id, hotel_stays, total_people, teacher_count, couples,
        name_overrides=name_overrides,
        start_date=start_date,
    )
```

**为什么放在 `resolve_resources()` 之后**：
- `resolve_resources()` 已经为所有城市匹配了一个默认 doc_id
- `resolve_hotel_overrides()` 只覆写 `hotel_overrides` 中出现的城市，其他城市的 doc_id 保持不变
- 这样无论客户指定几个城市的酒店，未指定的城市都保留 LLM 推荐结果

### 3. `update_hotel.py` 复用同一函数

把 `update_hotel.py` 中对应那段逻辑替换为调用 `resolve_hotel_overrides()`，保持行为完全一致（消除重复实现）。

### 4. SKILL.md 文档更新

`SKILL.md` 第 31~53 行的「输入参数（JSON）」和「参数说明」表新增 `hotel_overrides`：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `hotel_overrides` | array | 否 | 客户明确指定的酒店列表，结构同 `update_hotel.py`；只覆盖指定城市，其他城市走 LLM 默认推荐 |

同时在「适用场景」部分加一段：

> **何时使用 `hotel_overrides`**：
> - **`itinerary_text` 中明确写出某晚入住的具体酒店名（如"入住 XXX"、"住 XXX"、"XXX 美宿/民宿/客栈/度假村"等）**——必须传，否则报价单酒店与行程不一致
> - 客户在生成报价前明确指定了某城市的酒店
> - 客户上一版报价已修改过酒店，重新调整行程后想保留该酒店
> - 避免先生成报价再调 `update_hotel.py` 的两步走流程

## 边界与错误处理

| 场景 | 行为 |
|------|------|
| `hotel_overrides` 为空或未传 | 走原有逻辑，零变化 |
| `hotel_overrides` 中 `city` 不在 LLM 解析的 `hotel_stays` 中 | 报错（与 `update_hotel.py` 一致），提示本次报价包含的城市列表 |
| 酒店名歧义（多个精确匹配） | 报错，提示用更精确的酒店全名 |
| 酒店名查不到 | 报错，提示匹配条数 |
| 酒店无价格表 / 价格无效 | 报错 |
| **指定 `room_type` 但该酒店价格表无此房型** | **报错**，错误信息列出该酒店可用房型，让 Agent 改用列表中的房型重试 |
| **指定 `room_type` 且价格表有此房型但无团队价** | 报错（同"无团队价"） |
| LLM 解析的 `hotel_stays` 为空（旧结构化模式） | 忽略 `hotel_overrides`，记录 warning |

## 非目标

- **不修改 LLM 解析阶段**：`itinerary_parser.py` 不变，仍只产出 `hotel_stays` 结构
- **不修改向量检索默认行为**：`resolve_resources()` 的 top_k=1 仍保留，`hotel_overrides` 是"覆写"而非"替代检索"
- **不处理"客户想换酒店但还没生成过报价"的场景**：那种情况客户应先让 Agent 生成报价（可能带 `hotel_overrides`），不需要单独工具

## 测试要点

1. **不传 `hotel_overrides`**：行为与原来完全一致（回归测试）
2. **传部分城市的 override**：指定城市用客户酒店，其他城市保留 LLM 默认
3. **传所有城市的 override**：全部用客户指定
4. **override 的 city 不在 hotel_stays**：报错信息正确
5. **酒店名歧义**：报错信息列出候选
6. **酒店查不到价格**：报错
7. **旧结构化模式（itinerary_text 为空）**：override 被忽略，不报错

## 影响面

| 文件 | 改动 |
|------|------|
| `src/skills/travel-quote/scripts/hotel.py` | 新增 `resolve_hotel_overrides()` |
| `src/skills/travel-quote/scripts/generate.py` | 读取 `hotel_overrides` 并调用覆写 |
| `src/skills/travel-quote/scripts/update_hotel.py` | 复用 `resolve_hotel_overrides()`（消除重复） |
| `src/skills/travel-quote/SKILL.md` | 文档新增参数说明 |
