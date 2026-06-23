# 旅游报价价格解析性能优化设计

> 日期：2026-06-22  
> 范围：`src/skills/travel-quote/scripts/` 中行程解析、酒店价格解析与景点门票/项目解析  
> 状态：酒店、景点门票/项目、行程解析均已先完成 DeepSeek 关闭推理提速；规则优先待后续评估

## 背景

`travel-quote` 技能在行程文本驱动模式下，会自动完成：

```text
行程解析 -> 资源检索 -> 路线距离 -> 车辆/景点/住宿/餐饮/导游/其他费用 -> Excel 导出
```

近期用同一行程与租户 `tenant_9eb3e45cab83` 反复测试，完整脚本耗时稳定在 200 秒以上：

| 测试条件 | 总耗时 | 观察 |
|---|---:|---|
| `deepseek-v4-pro` 原样脚本 | 约 217s / 220s | 低并发时仍稳定超过 3 分钟 |
| 运行时计时器 | 约 399s | 酒店选价出现单次 156s 尾延迟 |
| `deepseek-v4-flash` 环境变量覆盖 | 约 288s | 未提速，且行程解析质量下降 |

当前最主要瓶颈不是 Excel 导出、数据库普通查询或价格计算，而是酒店与景点价格解析阶段过度依赖串行 LLM 调用。

## 问题分析

### 1. 酒店价格解析

当前逻辑位于 `hotel.py:_parse_team_price()`：

```python
fallback = _extract_first_team_price(price_table)
if not check_in_date:
    return fallback
price = _select_team_price_by_llm(price_table, check_in_date)
```

问题是：只要有入住日期，就必定先调用 LLM。规则兜底已经存在，但放在 LLM 之后。

酒店价格表本质上是结构化数据：

```text
房型 | 客户类型 | 价格(元) | 含早 | 适用日期
```

这类数据应当优先由规则解析。LLM 只应处理规则无法识别的少数日期表达。

实测中 3 个酒店 stay 会触发 3 次 LLM 选价，耗时可达到：

```text
47s + 39s + 156s = 242s
```

这对“从结构化价格表选一个数字”来说不合理。

### 2. 景点门票与项目解析

当前逻辑位于 `attraction.py:_process_single_attraction()`：

```python
tickets = _llm_extract_tickets(...)
if tickets is None:
    tickets = _fallback_parse_tickets(...)

projects = _llm_extract_projects(...)
if projects is None:
    projects = _fallback_parse_projects(...)
```

同样是 LLM 优先、规则兜底。一次 5 个景点的行程可能触发：

- 门票 LLM：每个景点 1 次
- 项目 LLM：每个有 activities 的景点 1 次

本次样例触发 9 次景点相关 LLM，总耗时约 60-96 秒，单次尾延迟曾达到 53 秒。

景点门票表和项目表也通常是结构化文本，绝大多数可以规则解析。

### 3. LLM 调用缺少短超时与任务型限制

当前 `llm_client.py` 对 DeepSeek / Zhipu 使用：

```python
timeout=300.0
```

且没有按任务设置 `max_tokens`。这会导致结构化抽取任务遇到供应商侧尾延迟时，整个报价流程长时间阻塞。

## 设计目标

1. 酒店价格解析改为“候选压缩 + 非推理 LLM 短 prompt 主路径 + 保守 fallback”，不再由规则做最终选价。
2. 将景点门票/项目解析从“每景点多次 LLM”改为“规则优先，仅未匹配项 LLM 兜底”。
3. 将完整报价的常规耗时从 200s+ 降至 30s-60s 内。
4. 保持报价结果稳定、可解释、可回归测试。
5. LLM 失败或超时不得阻断报价主流程，应有保守 fallback。

## 非目标

- 不重构行程解析 prompt。
- 不改变报价 Excel 模板。
- 不改变 `internal_data` / `rows` 输出结构。
- 不改资源检索入库格式。
- 不在本次方案中解决 route-distance 子进程优化。

## 方案总览

```text
酒店价格表
  -> 规则解析 rows
  -> 压缩候选
  -> 非推理 LLM 根据入住日期和团队构成选择团队价
  -> 失败时回退首条团队价

景点门票表
  -> 规则解析票型
  -> 团队票/成人票/学生票等归一
  -> 构建 ticket items
  -> 失败时 LLM 兜底

景点项目表
  -> 规则解析候选项目
  -> 精确/包含/别名/字符重叠匹配
  -> 未匹配活动才 LLM 兜底
```

## 酒店价格解析设计

### 数据结构

新增内部结构，不改变外部返回：

```python
{
    "room_type": "标准间",
    "customer_type": "团队",
    "price": 308.0,
    "breakfast": "含双早",
    "date_text": "2026-07-01至2026-08-31",
    "row_index": 3,
}
```

### 解析步骤

1. 按行切分 `price_table`。
2. 仅处理包含 `|` 的数据行。
3. 拆列后识别：
   - 房型：第 1 列
   - 客户类型：第 2 列
   - 价格：第 3 列首个数字
   - 含早：第 4 列，可选
   - 适用日期：第 5 列及之后合并
4. 过滤团队价：
   - 包含 `团队`
   - 包含 `团散同价`
   - 包含 `团队/散客`

### 日期匹配规则

入住日期为 ISO 字符串，如 `2026-08-15`。

支持优先级：

| 类型 | 示例 | 处理 |
|---|---|---|
| 精确日期区间 | `2026-07-01至2026-08-31` | 解析 start/end，判断覆盖 |
| 月日区间 | `7月1日-8月31日` | 补全年份 |
| 月份范围 | `7-8月`、`7月-8月` | 转为当年月份范围 |
| 单月 | `8月` | 转为当月 1 日至月底 |
| 旺季/暑期 | `暑期`、`旺季` | 7-8 月优先匹配 |
| 节假日 | `五一`、`国庆`、`春节`、`中秋`、`端午` | 按内置节假日关键词规则匹配 |
| 平日/周末 | `平日`、`周末` | 按 weekday 判断 |
| 通用 | `全年`、`平季`、空日期 | 低优先级通用匹配 |

### 打分策略

同一入住日期匹配多行时，按分数选择：

| 条件 | 分数 |
|---|---:|
| 精确日期区间覆盖 | +100 |
| 节假日专用覆盖 | +120 |
| 月份/季节覆盖 | +70 |
| 周末/平日匹配 | +50 |
| 全年/通用 | +10 |
| 日期区间更短更精确 | +1 至 +20 |
| 行号更靠前 | 作为最终 tie-break |

当没有任何日期覆盖时，回退到第一条团队价，保持现有行为。

### LLM 兜底

仅在以下情况调用 LLM：

- 价格表列结构异常，规则无法解析任何有效团队价。
- 日期文本包含未知复杂表达，且多条候选无法可靠决策。
- 命中多条同分候选且价格差异较大。

LLM prompt 必须压缩：

```text
任务：从候选团队价中为入住日期选择价格，只输出数字。
入住日期：2026-08-15
候选：
1. 标准间 | 团队 | 308 | 2026-07-01至2026-08-31
2. 标准间 | 团队 | 228 | 2026-09-01至2026-12-31
```

不再发送完整酒店信息，不发送长规则说明。

超时建议：

```text
hotel_price_select: 8s-10s
max_tokens: 32
```

超时或失败时使用 `_extract_first_team_price()`。

## 景点门票解析设计

### 规则解析优先

解析 `ticket_table` 每一行：

```text
票种名 | 客户类型 | 挂牌价 | 团队价 | ...
```

规则：

1. 取第一个可解析数字作为报价价。
2. 识别票型：
   - `团队票`、`团队` -> `adult`，备注为团队票
   - `成人`、`全价`、`普通游客`、`全体游客`、`统一票价` -> `adult`
   - `学生` -> `student`
   - `儿童`、`半票` -> `child_half`
   - `老人`、`老年` -> `elder`
3. 如果同时存在团队票和成人票，优先团队票。
4. 同票型多行时，保留第一条或按季节字段扩展后选择。
5. 没有明确票型但有价格时，作为统一票价 adult 处理。

现有 `_build_ticket_items()` 可继续复用。

### LLM 兜底

仅在规则无法识别有效票价时调用 `_llm_extract_tickets()`。

兜底 prompt 压缩：

- 去掉长篇禁止脑补规则。
- 不发送完整 `attraction_info`，只发送景点名和价格表。
- 要求 JSON 输出。

超时建议：

```text
attraction_ticket_extract: 10s-15s
max_tokens: 512
```

## 景点项目匹配设计

### 候选解析

从 `project_table` 中解析候选：

```python
{
    "name": "卧龙潭漂游",
    "price": 50.0,
    "billing_method": "per_person",
    "people_range": (1, 20) | None,
    "raw": "...",
}
```

计费方式：

- 行内含 `团`、`组`、`每场`、`场` -> `per_group`
- 否则默认 `per_person`

人数分档：

- 支持 `1-20人`、`21-30人`、`30人以上`
- 根据 `total_people` 选择对应档位

### 匹配策略

对每个 activity 依次尝试：

1. 标准化精确匹配。
2. 标准化包含匹配。
3. 别名匹配。
4. 字符重叠/相似度匹配。

标准化：

- 去空格
- 去括号内容可选保留原词
- 去标点
- 全角转半角
- 小写化英文

别名表可先内置在代码中，后续可迁移配置：

```python
{
    "FAST观测体验": ["FAST观景", "天眼观测", "观测体验"],
    "天眼瞭望台直通车": ["瞭望台直通车", "观景台直通车"],
    "卧龙潭漂游": ["卧龙潭漂流", "卧龙潭漂游"],
    "鸳鸯湖游船": ["鸳鸯湖划船", "鸳鸯湖船"],
}
```

### LLM 兜底

仅将未匹配 activity 发给 LLM。

Prompt 只包含：

```text
未匹配活动：
- 夜游望远镜观星

候选项目：
1. FAST观测体验 | 30 | 按人
2. 天象影院 | 40 | 按人
...

返回能匹配的项目 JSON。找不到返回空数组。
```

不再发送完整景点信息和长规则。

超时建议：

```text
attraction_project_match: 10s-15s
max_tokens: 512
```

## LLM 客户端调整

`llm_client.call_llm()` 建议支持任务级参数：

```python
def call_llm(prompt: str, *, timeout: float = 30.0, max_tokens: int | None = None, task: str = "") -> str:
    ...
```

不同任务默认值：

| task | timeout | max_tokens |
|---|---:|---:|
| `hotel_price_select` | 10s | 32 |
| `attraction_ticket_extract` | 15s | 512 |
| `attraction_project_match` | 15s | 512 |
| `itinerary_parse` | 30s | 2048 |

同时记录结构化日志：

```text
[travel-quote][llm] task=hotel_price_select model=... prompt_chars=820 duration=2.31s success=true fallback=false
```

## 实施计划

### Phase 1：酒店候选压缩 + 非推理 LLM 选价

> 2026-06-22 实测发现规则选价命中不足，平塘/安顺等真实价格表会进入歧义或回退，不能满足“与原 LLM 精度基本一致”的要求。因此撤回规则决策，规则仅用于把价格表整理成候选行，最终仍由 LLM 按入住日期和团队构成选价。

- ✅ 保留 `_parse_hotel_price_rows()` 用于候选压缩
- ✅ 删除 `_match_hotel_price_by_date()` 主路径，不再用规则做最终决策
- ✅ 修改 `_parse_team_price()` 为 LLM 主路径，失败时回退首条团队价
- ✅ 酒店选价 prompt 加入入住日期、星期和团队构成
- ✅ 酒店选价继续使用当前配置模型（如 `deepseek-v4-pro`），但请求体显式传 `thinking: {"type": "disabled"}` 关闭推理
- ✅ 新增/调整单元测试覆盖：
  - 候选行解析
  - 有入住日期时 LLM 主路径
  - prompt 包含团队构成
  - 酒店任务短超时 / 小 token 输出
  - LLM 异常或非正价格回退首条团队价

### Phase 2：景点门票/项目关闭推理

> 2026-06-22 A/B 实测：样例 5 个景点 9 次景点 LLM，默认 thinking 总耗时约 64.2s；关闭 thinking 后约 18.8s。项目匹配结果一致；门票在“团队票 vs 成人套票”场景需后处理保护。

- ✅ `_llm_extract_tickets()` 继续使用当前长 prompt，但调用 DeepSeek 时显式传 `thinking: {"type": "disabled"}`
- ✅ `_llm_extract_projects()` 继续使用当前长 prompt，但调用 DeepSeek 时显式传 `thinking: {"type": "disabled"}`
- ✅ 增加 `task`、`timeout`、`max_tokens` 便于观测与控制
- ✅ 增加团队票后处理：同票型同时出现普通票和团队票时保留团队票
- ✅ 增加单元测试覆盖：
  - 景点门票关闭 thinking 调用参数
  - 天龙屯堡多成人票时团队票优先
  - 项目匹配关闭 thinking 调用参数

### Phase 3：行程解析关闭推理

> 2026-06-22 A/B 实测：同一 6 天样例行程，默认 thinking 约 42.17s；关闭 thinking 约 5.86s，但初版会遗漏 activities 为空的天龙屯堡，并将“扶梯（单程）”简化为“扶梯”。补充硬性自检后约 6.52s，关键字段恢复。

- ✅ `parse_itinerary()` 调用 DeepSeek 时显式传 `thinking: {"type": "disabled"}`
- ✅ 增加 `task=itinerary_parse`、`timeout=60s`、`max_tokens=4096`
- ✅ prompt 增加关闭推理模式硬性自检：
  - 实际游览景点即使 activities 为空也必须输出
  - 不因 activities 为空删除景点
  - activities 必须逐字复制游玩项目列，保留括号和标点
  - 景点名优先使用行程安排中的主景点名
- ✅ 增加单元测试覆盖调用参数和关键自检规则

### Phase 4：景点门票规则优先

- 新增 `_parse_ticket_rows_rule_based()`
- 扩展 `_fallback_parse_tickets()`，从 fallback 升级为主路径
- 保留 LLM 兜底
- 增加单元测试覆盖：
  - 成人票
  - 团队票优先
  - 统一票价
  - 学生/儿童/老人票
  - 多价格列取第一个数字

### Phase 5：项目匹配规则优先

- 新增 `_parse_project_rows()`
- 新增 `_match_project_rule_based()`
- 未匹配项目才调用 LLM
- 增加单元测试覆盖：
  - 精确匹配
  - 包含匹配
  - 别名匹配
  - 人数分档
  - per_group/per_person 判断

### Phase 6：性能回归与日志

- 同一租户同一行程连续跑 3 次
- 输出阶段耗时统计
- 验证价格稳定性与总耗时

## 验收标准

1. 样例行程 `tenant_9eb3e45cab83` 总耗时稳定低于 60 秒。
2. 酒店价格解析常规路径不调用 LLM。
3. 景点门票/项目 LLM 默认关闭 DeepSeek thinking，单次常规耗时约 1-3 秒。
4. 后续规则优先阶段完成后，景点门票常规路径不调用 LLM，项目匹配只有未匹配 activity 调用 LLM。
5. LLM 兜底超时不超过 15 秒。
6. 输出 rows 字段与当前 schema 完全兼容。
7. 现有 `update_hotel.py` 酒店局部替换流程不回归。

## 风险与应对

| 风险 | 应对 |
|---|---|
| 日期表达覆盖不全 | 规则无法判断时 LLM 兜底；记录 `fallback_reason` |
| 规则误选低价/高价 | 单元测试覆盖真实价格表；优先精确日期与节假日 |
| 项目别名维护成本 | 先内置高频别名，后续迁移到配置或知识库 metadata |
| LLM 兜底返回异常 | 短超时 + JSON 校验 + fallback |
| 价格结果变化 | 建立样例行程 golden result，允许明确规则导致的可解释差异 |

## 后续可选优化

- 行程 Markdown 表格规则解析，减少 `parse_itinerary()` 单次 40s+ LLM。
- 路线距离改为直接 import 调用或批量并发，减少子进程开销。
- 资源检索增加批量 embedding 或并发检索。
- 避免脚本启动时加载 Master Agent 与无关技能表。
