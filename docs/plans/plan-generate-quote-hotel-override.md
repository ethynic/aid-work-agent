# 开发计划：报价单生成支持指定酒店

> 反向关联：[设计文档](../subagent/travel-consultant/generate-quote-hotel-override-design.md) · [ideas.md 第 26 条](../ideas.md)

## 任务拆解

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 1 | 抽取共享函数 `resolve_hotel_overrides()` | `src/skills/travel-quote/scripts/hotel.py` | ⬜ 待开始 |
| 2 | `generate.py` 读取 `hotel_overrides` 并调用覆写 | `src/skills/travel-quote/scripts/generate.py` | ⬜ 待开始 |
| 3 | `update_hotel.py` 复用 `resolve_hotel_overrides()` | `src/skills/travel-quote/scripts/update_hotel.py` | ⬜ 待开始 |
| 4 | SKILL.md 文档新增 `hotel_overrides` 参数说明 | `src/skills/travel-quote/SKILL.md` | ⬜ 待开始 |
| 5 | 手动测试 7 个场景 | — | ⬜ 待开始 |
| 6 | 登记 ideas.md / 移动到 ideas_finished.md | `docs/ideas.md` | ⬜ 待开始 |

## 详细步骤

### 步骤 1：抽取 `resolve_hotel_overrides()`

**文件**：`src/skills/travel-quote/scripts/hotel.py`

**操作**：
- 在文件末尾新增 `resolve_hotel_overrides(tenant_id, hotel_stays, overrides) -> dict`
- 函数内容迁移自 `update_hotel.py` 第 67~120 行：
  - 校验每个 override 的 `city` 在 `hotel_stays` 中存在
  - 用 `HotelRetriever.search_by_name()` 查询，精确比对"酒店名称"字段（复用 `_parse_hotel_name()`）
  - 歧义/查不到/价格表无效时抛 `ValueError`（消息与现状一致）
  - 写回 `stay['hotel_doc_id']`
  - 返回 `{city: hotel_name}` 字典
- `_parse_hotel_name()` 改为模块级私有函数（原本只在 `update_hotel.py` 中）

**验收标准**：
- 函数签名清晰、参数有类型标注
- 报错消息与原 `update_hotel.py` 完全一致
- 单元测试可覆盖（mock HotelRetriever）

### 步骤 2：`generate.py` 接入

**文件**：`src/skills/travel-quote/scripts/generate.py`

**操作**：
- 在 `resolve_resources()` 调用之后（约第 87 行 `hotel_stays = resources.get('hotel_stays', [])` 之后）、Step 6 之前，插入：

```python
overrides = params.get('hotel_overrides') or []
name_overrides = {}
if overrides and hotel_stays:
    from hotel import resolve_hotel_overrides
    name_overrides = resolve_hotel_overrides(tenant_id, hotel_stays, overrides)
    logger.info(f"[travel-quote] 应用酒店指定: {name_overrides}")
elif overrides and not hotel_stays:
    logger.warning("[travel-quote] 传入 hotel_overrides 但 hotel_stays 为空，忽略")
```

- 修改 Step 6 的 `calculate_hotel_stays()` 调用，传入 `name_overrides=name_overrides`（原调用未传此参数，默认为 `{}`，行为不变）

**验收标准**：
- 不传 `hotel_overrides` 时，日志无变化、结果无变化
- 传 `hotel_overrides` 时，指定城市的住宿行项目名=客户酒店名、价格来自客户酒店
- 指定 city 不在 hotel_stays 时，报错消息列出当前包含的城市

### 步骤 3：`update_hotel.py` 复用

**文件**：`src/skills/travel-quote/scripts/update_hotel.py`

**操作**：
- 删除第 67~127 行那段手写反查/校验/覆写逻辑
- 替换为：

```python
from hotel import resolve_hotel_overrides
name_overrides = resolve_hotel_overrides(tenant_id, hotel_stays, overrides)
```

- 保留第 122~127 行构造 `name_overrides` 的部分（其实可以直接用返回值）

**验收标准**：
- 行为与原来完全一致（同一个 internal_data + 同一个 hotel_overrides，新旧版本产出相同的 new_items）
- 代码行数显著减少

### 步骤 4：SKILL.md 文档

**文件**：`src/skills/travel-quote/SKILL.md`

**操作**：
- 「输入参数（JSON）」示例中追加 `"hotel_overrides": [...]` 字段
- 「参数说明」表新增一行
- 「适用场景」部分新增一段「何时使用 hotel_overrides」说明
- 「注意事项」中追加一条：`hotel_overrides` 是可选参数，未指定城市走 LLM 默认推荐

**验收标准**：
- 文档示例可直接复制运行
- 与 `update_hotel.py` 的 `hotel_overrides` 结构说明完全一致（避免 LLM 混淆）

### 步骤 5：手动测试

**测试场景**（7 个，对应设计文档「边界与错误处理」表）：

1. 不传 `hotel_overrides`：与原行为一致（对照旧版本 Excel）
2. 传 1 个城市的 override（多城市行程）：该城市酒店正确，其他城市保留默认
3. 传全部城市的 override：所有住宿行都用指定酒店
4. override 的 city 不在 hotel_stays：报错消息包含当前城市列表
5. 酒店名歧义（构造同名数据，或用一个会匹配多个的模糊名）：报错列出候选
6. 酒店名查不到：报错提示匹配条数
7. 旧结构化模式（itinerary_text 为空 + 传 overrides）：warning 日志、不报错

**验收标准**：7 个场景全部符合预期

### 步骤 6：文档登记

**操作**：
- `docs/ideas.md` 在「旅游报价」分区（第 25 条之后）新增第 26 条，状态 `🔧 部分完成`
- 完成后移动到 `docs/ideas_finished.md`，状态 `✅ 已完成开发`

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 抽取共享函数时漏掉边界条件 | 步骤 3 改完后用同一份 internal_data + override 对比新旧输出，必须完全一致 |
| `generate.py` 中 import 时机不对 | 放在 `resolve_resources()` 之后，与 `update_hotel.py` 调用顺序一致 |
| LLM 误用 `hotel_overrides`（如行程没提到该城市也硬塞） | SKILL.md 中明确"city 必须在 hotel_stays 中"，且代码会报错兜底 |

**回滚**：所有改动集中在 4 个文件，回滚 git revert 即可，无数据库变更、无配置变更。

## 预估工作量

- 步骤 1~3：1~2 小时（核心代码改动）
- 步骤 4：30 分钟
- 步骤 5：1 小时（需要真实知识库数据测试）
- 步骤 6：10 分钟

总计：约半天。
