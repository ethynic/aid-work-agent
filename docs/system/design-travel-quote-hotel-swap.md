# 旅游报价技能 - 酒店局部替换设计

> **关联开发计划**：[plan-travel-quote-hotel-swap.md](../../plans/plan-travel-quote-hotel-swap.md)
> **登记**：[docs/ideas.md](../ideas.md) 第 19 条「旅游报价酒店局部替换」

## 背景

`travel-consultant` 子智能体调 `travel-quote` 技能生成报价单后，客户经常想换酒店（如"贵阳的酒店能不能换好一点的"、"荔波换成有泳池的"）。

当前问题：

1. **travel-quote 是一次性脚本**：`generate.py` 从行程文本重新解析 → 向量检索 → 计算全部费用 → 导出 Excel。换酒店只能重跑整个流程，结果不可控（向量检索每次可能匹配到不同酒店）。
2. **子智能体已有能力列酒店选项**：`hotel_retriever` 可基于城市/区域搜索酒店列表给客户看，但客户选定的 `doc_id` 没办法回传给报价单。
3. **缺少"只换酒店"的入口**：客户只想换酒店，景点、用车、餐饮、导游等都不该变。

## 目标

- 客户换酒店时，**只重算住宿相关费用**，其他 items 原样保留
- 支持**按城市定位**（行程通常会住多个城市的酒店，如贵阳/平塘/荔波各几晚）
- 客户选定新酒店后，**生成新的 `.xlsx` 报价单**
- 子智能体调用方式简单清晰，符合现有技能调用模式

## 非目标

- 不支持换景点、换用车等其他类别（本次只做酒店）
- 不自动更新 Word 详细行程文档（默认由子智能体在对话里告知客户新酒店名）
- 不引入数据库落库（无状态设计，数据走 LLM 上下文回传）

## 设计

### 总体方案

新增独立脚本 `scripts/update_hotel.py`，职责单一：**接收上次报价的完整 items 结构 + 客户指定的新酒店映射，只重算住宿行，输出新报价单**。

`generate.py` 输出做小幅改造：把现有重复的字段（rows、合计值、file_path）和内部计费所需的原始数据（items 原始结构、hotel_stays、计费参数）**合并到一个 JSON 返回**，不重复字段。

### 模块关系

```
┌─────────────────────┐
│  travel-consultant  │  子智能体
│  (SUBAGENT.md)      │
└──────────┬──────────┘
           │
           ├─ 首次报价：use_skill("travel-quote") + skill_execute(generate.py)
           │       ↓
           │  返回 JSON（含 items、计费参数、file_path、rows 等，合并输出）
           │
           ├─ 客户想换酒店：调 hotel_retriever 列酒店 → 客户选定
           │
           └─ 局部更新：use_skill("travel-quote") + skill_execute(update_hotel.py)
                   ↓
                   content = {prev_internal_data, hotel_overrides}
                   ↓
              返回新 JSON（同结构，新 file_path）
```

### 数据流：首次报价

`generate.py` 返回结构改造（合并去重）：

```json
{
  "success": true,
  "data": {
    // —— 表头信息 ——
    "course_name": "超级贵州研学",
    "company_name": "贵州天悦旅行社有限公司",
    "start_date": "2026-07-01",
    "total_people": 30,
    "teacher_count": 3,
    "trip_days": 6,

    // —— 报价明细（LLM 视图，对应 Excel 表头）——
    "rows": [...],          // 同现有，中文 key

    // —— 合计 ——
    "合计_费用小计": 1861.53,
    "合计_随队老师": 560.00,
    "人均报价": 2223.33,
    "总价": 66700.00,

    // —— 文件 ——
    "file_path": "/tmp/quote_xxx.xlsx",

    // —— 内部数据（供 update_hotel 使用，LLM 不得修改或解读；不含 file_path，file_path 是顶层独立字段）——
    "internal_data": {
      "region_name": "贵州",
      "couples": 0,
      "season_type": "peak",
      "items": [
        // 原始 items，含 category/name/unit_price/quantity/unit/frequency/freq_unit/
        // subtotal/teacher_subtotal/remark 等英文 key 字段
      ],
      "hotel_stays": [
        {"city": "贵阳", "area": "南明区", "nights": 2, "hotel_doc_id": 101},
        {"city": "平塘", "area": "", "nights": 1, "hotel_doc_id": 205},
        {"city": "荔波", "area": "", "nights": 2, "hotel_doc_id": 308}
      ]
    }
  }
}
```

**关键改动**：

1. 现有 `generate.py` 内部已经构造了 `internal_data` 字典（见 `generate.py:184-198`），目前只用于 `export_with_template`，**没有输出**。本次改造把它加入 return，并扩展字段（补充 `couples`、`season_type`、`hotel_stays`）。
2. **rows 与 items 的区别**：rows 是给 LLM 看的中文 key 视图（用于向客户复述报价明细），items 是内部计费结构（保留英文 key，update_hotel 直接用）。两者通过 `data.items` / `data.rows` 平级共存，不再重复字段。
3. **`file_path` 保持顶层独立字段**：不放入 `internal_data`。子智能体需要直接读取顶层 `file_path` 注册下载，不应嵌套取值。`internal_data` 只放计费相关的数据结构。

### 数据流：酒店局部更新

`update_hotel.py` 输入（通过 `content` 传 JSON）：

```json
{
  "tenant_id": "租户ID",
  "internal_data": {
    // 上次 generate.py 返回的 internal_data 原样回传
    "region_name": "贵州",
    "couples": 0,
    "season_type": "peak",
    "items": [...],
    "hotel_stays": [...]
  },
  "hotel_overrides": [
    // 客户要换的酒店，按 city 定位
    {
      "city": "贵阳",
      "hotel_doc_id": 150,
      "hotel_name": "贵阳凯宾斯基酒店"   // 可选，给客户看的名称
    }
  ],
  // 以下字段用于重新导出 Excel
  "course_name": "超级贵州研学",
  "company_name": "贵州天悦旅行社有限公司",
  "start_date": "2026-07-01",
  "total_people": 30,
  "teacher_count": 3,
  "trip_days": 6,
  "template_path": null
}
```

**hotel_overrides 语义**：

- `city` 必填，用于定位 `hotel_stays` 中要替换的那一项
- `hotel_doc_id` 必填，新酒店在知识库的 doc_id（子智能体通过 `hotel_retriever.search` 拿到）
- `hotel_name` 可选，会作为 items 住宿行的 `name` 字段，让客户看到具体酒店名
- **支持多次替换**：客户一次想换多个城市的酒店，`hotel_overrides` 是数组
- **未列出的 city 沿用旧 doc_id**：客户只说"换贵阳的酒店"，平塘/荔波保持不变

### update_hotel.py 处理流程

```
1. 加载 internal_data.items（旧 items 列表）
2. 加载 internal_data.hotel_stays（旧酒店住宿清单）
3. 用 hotel_overrides 覆盖 hotel_stays 中对应 city 的 hotel_doc_id
4. 用 hotel.py 的 calculate_hotel_stays() 重算住宿行
   - 传入更新后的 hotel_stays + 计费参数（total_people/teacher_count/couples）
   - 得到新的住宿 items 列表（按 hotel_stays 顺序）
5. 原地替换：遍历旧 items，遇到 category=="住宿" 的行按出现顺序替换为新住宿行
   - **保持原 items 顺序**，住宿行原来在第几位就还在第几位
   - 旧住宿行数量必须等于新住宿行数量，否则报错（数据不一致）
6. 重新汇总 cost_per_person / quote_total / quote_per_person / teacher_total
7. 调 export_with_template() 导出新 Excel
8. 返回与 generate.py 完全相同的 JSON 结构
   - 顶层含 rows、合计、新 file_path（顶层独立）
   - internal_data 字段含新 items（顺序保持）、新 hotel_stays
```

**保持顺序的关键**：不删除旧住宿行再追加，而是**按位置替换**。这样报价单的行序对客户来说是稳定的，便于客户对比新旧报价。

### 复用现有模块

- `hotel.py:calculate_hotel_stays` — 已支持 `hotel_stays` 列表模式，原样复用
- `hotel_retriever.get_price_table / get_hotel_info` — 已有，用于取新酒店价格和信息
- `excel_export.export_with_template` — 已有，传入新 items 直接出 Excel
- 不需要改 itinerary_parser、attraction、vehicle、meal、guide、other_fees 等模块

### 返回结构

与 `generate.py` 完全一致（同 schema），便于子智能体复用同一套"收到报价后怎么跟客户说"的话术规则。

## SUBAGENT.md 改造

在「阶段三：详细行程确认 → 引导报价」中新增小节：

### 换酒店流程

当客户对已生成的报价提出换酒店需求时：

1. **询问要换哪个城市的酒店**：行程通常会住多个城市（如"贵阳/平塘/荔波"），先确认客户要换哪个城市
2. **用 `hotel_retriever` 列酒店选项**：基于该城市 + 客户偏好（如"4钻"、"带泳池"）搜索，列 3-5 个候选给客户选
3. **客户选定后调用 update_hotel.py**：

```python
skill_execute(
  skill="travel-quote",
  command="python scripts/update_hotel.py",
  content='{"tenant_id": "...", "internal_data": (上次报价返回的 internal_data 原样传回), "hotel_overrides": [{"city": "贵阳", "hotel_doc_id": 150, "hotel_name": "贵阳凯宾斯基"}], "course_name": "...", ...}'
)
```

4. **收到新报价后**：按现有"收到报价后怎么跟客户说"规则复述新报价明细 + 新 file_path，告知"已把 XX 城市的酒店换成 YY"

**铁律**：

- `internal_data` 必须**原样传回**，不允许修改 items、计费参数
- 只传客户要换的 city，其他 city 由系统自动沿用旧酒店
- 一次只换酒店，不允许借机改景点/天数/人数

## 关键设计决策

| 决策点 | 方案 | 理由 |
|--------|------|------|
| 入口方式 | 新增 `update_hotel.py` 独立脚本 | 职责单一，不污染 `generate.py`；SUBAGENT.md 增加一种调用方式说明 |
| 中间数据保留 | `generate.py` 返回增加 `internal_data` 字段 | 无状态、不引入数据库；上下文增加约 2-3KB 可接受 |
| 酒店定位方式 | 按 `city` 匹配 `hotel_stays` | 行程通常多城市多酒店，必须精确指定；未列出的 city 沿用旧值 |
| Word 行程 | 默认不更新 | 行程文本通常只写"入住酒店"笼统描述，无需重生成；由 agent 在对话里告知 |
| 输出 | 新的 `.xlsx` 报价单 | 保留旧报价单便于客户对比；新 file_path 给到客户 |

## 输入校验

`update_hotel.py` 必须校验：

1. `internal_data` 必须包含 `items`（数组）和 `hotel_stays`（数组）
2. `hotel_overrides` 中每个 `city` 必须能在 `hotel_stays` 中找到（找不到报错，避免静默替换失败）
3. 每个 `hotel_doc_id` 必须能在知识库查到（`hotel_retriever.get_price_table` 返回非空）
4. 若 `hotel_overrides` 中某酒店价格表无有效价格，**整单报错**，不允许"住宿费为 0 的报价单"流出

## 改动清单

### 新增文件

- `src/skills/travel-quote/scripts/update_hotel.py` — 酒店局部更新主脚本

### 修改文件

- `src/skills/travel-quote/scripts/generate.py`
  - 在 `return` 字典中增加 `internal_data` 字段（含 `items`、`hotel_stays`、`couples`、`season_type`、`region_name`）
  - **`file_path` 保持顶层独立字段**，不放入 `internal_data`
  - 字段不与现有 `rows`/合计/`file_path` 重复

- `src/skills/travel-quote/SKILL.md`
  - 输出格式部分补充 `internal_data` 字段说明
  - 新增「酒店局部更新」章节，描述 update_hotel.py 调用方式
  - 明确：LLM **不得修改或解读 internal_data**，原样回传即可

- `subagents/travel-consultant/SUBAGENT.md`
  - 在「阶段三」中新增「客户换酒店」小节
  - 增加"先确认换哪个城市 → 列酒店选项 → 调 update_hotel"的流程
  - 增加铁律：internal_data 原样传回，不允许修改

## 风险与权衡

| 风险 | 缓解 |
|------|------|
| LLM 修改了 internal_data 导致报价错误 | SKILL.md/SUBAGENT.md 显式说明"原样传回"；update_hotel.py 校验 items 数量与 hotel_stays 一致 |
| LLM 忘记传 internal_data（新会话/上下文被压缩） | update_hotel.py 报错并提示"缺少上次报价数据，请重新生成完整报价" |
| 客户连续多次换酒店 | 每次返回新的 internal_data，LLM 用最新的回传；旧报价单不删 |
| 单房差计算依赖 couples，但客户可能在换酒店时同时调整人数 | update_hotel.py 不接受改人数参数，couples 强制沿用 internal_data 中的值，避免误用 |
| 上下文增加 2-3KB JSON | 单次会话可接受；如有问题后续可改为落库方案 |

## 测试要点

1. **正常流程**：generate → 客户选新酒店 → update_hotel → 新报价单的住宿行金额正确，其他行不变
2. **多城市酒店**：行程住 3 个城市，只换其中一个，其他两个住宿行金额不变
3. **行序保持**：新旧 items 中住宿行的位置一致（不挪到末尾），便于客户对比
4. **未匹配的 city**：hotel_overrides 中 city 不在 hotel_stays 中，应报错
5. **新酒店无价格表**：hotel_doc_id 查不到价格，应报错而非生成 0 元报价
6. **单房差**：couples > 0 且剩余人员为奇数时，单房差正确计算
7. **teacher_subtotal**：住宿行的老师费用正确（按新酒店价格 × 夜数 × teacher_count）
8. **file_path 在顶层**：返回 JSON 的 `data.file_path` 是顶层字段，子智能体可直接读取注册下载
