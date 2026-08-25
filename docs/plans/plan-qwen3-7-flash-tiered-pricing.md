# qwen3.7-flash 分段计价开发计划

> 状态：📋 待开发（2026-08-15 设计定稿）
> 关联：[系统功能 #42 租户积分充值与计费](../ideas.md)
> 计费审计：本次改动涉及计费函数（`calculate_credit_cost_with_breakdown` 扩展 + 新增合并计费函数），按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 执行全量回归核对。

## 背景

百炼平台 `qwen3.7-flash` 按**单次请求的输入 token 数**分段计价：

| 单次请求输入 Token 数 | 输入单价（元/百万） | 输出单价（元/百万） |
|---|---|---|
| 0 < T ≤ 32K | 0.2 | 0.8 |
| 32K < T ≤ 256K | 0.6 | 2.4 |
| 256K < T ≤ 1M | 1.2 | 4.8 |

**问题**：本系统 `chat_records` 把 agent 多轮循环的 usage 累加成一个 `prompt_tokens` 后统一计费。多轮对话中每轮 LLM 调用（= 一次 API 请求）的输入长度不同、单价可能落在不同档位，累加后按单一单价计费没有意义；但 **token 数合计、成本价合计是有意义的**。

## 设计决策

1. **计费口径：逐轮计费、累加成本、合并反向单价**。
   - 每轮 LLM 调用按该轮的 `prompt_tokens` 查档，分别算「未命中缓存输入 / 命中缓存输入 / 输出」三分项成本，累加成本合计。
   - 合并后的单价由 `{成本价合计} / {token数合计}` 反向算出，记入 `usage_breakdown` 的 chat 分项，**保持现有 `unit_prices` / `credits` 键结构不变 → 前端积分用量明细统计零修改**。
2. **缓存命中输入单价**：按输入价 20%（0.04 / 0.12 / 0.24）。
3. **表结构：JSONB 分段字段**，沿用 `price_per_second_by_resolution` 先例，不新增明细表、不改现有列、不影响其他模型。

## 一、表结构修改

`token_cost_prices` 新增 `tiered_pricing JSONB` 列（仅分段计价模型填写，其他模型为 NULL 走原逻辑）：

```sql
-- 2026-08-15 qwen3.7-flash 分段计价：token_cost_prices 增加 tiered_pricing
ALTER TABLE token_cost_prices ADD COLUMN IF NOT EXISTS tiered_pricing JSONB;

INSERT INTO token_cost_prices (model_name, tiered_pricing)
VALUES ('qwen3.7-flash', '[
  {"max_input": 32768,   "input_per_m": 0.2, "cached_input_per_m": 0.04, "output_per_m": 0.8},
  {"max_input": 262144,  "input_per_m": 0.6, "cached_input_per_m": 0.12, "output_per_m": 2.4},
  {"max_input": 1048576, "input_per_m": 1.2, "cached_input_per_m": 0.24, "output_per_m": 4.8}
]'::jsonb)
ON CONFLICT (model_name) DO UPDATE SET tiered_pricing = EXCLUDED.tiered_pricing, updated_at = NOW();
```

- 档位语义：`max_input` 递增即隐含区间（等价 `0<T≤32K` / `32K<T≤256K` / `256K<T≤1M`），查档取第一个 `max_input >= 输入token` 的档，超过 1M 用最后一档兜底。
- 两个 SQL 文件同步：`deploy/init-postgres.sql`（新环境）+ `deploy/db_update.sql`（增量，带 2026-08-15 注释）。

## 二、计费逻辑扩展

### 核心算法（逐轮 → 合并反向单价）

```
对每轮调用 i（用该轮 prompt_tokens 查档得 tier_i）：
    non_cached_cost += (prompt_i - cached_i) * tier_i.input_per_m
    cached_cost     += cached_i              * tier_i.cached_input_per_m
    output_cost     += completion_i          * tier_i.output_per_m
token_cost = (non_cached_cost + cached_cost + output_cost) / 1e6
credit_cost = ceil(token_cost * usage_factor * 100) / 100

合并反向单价（元/百万 token，分项 token 数为 0 时置 None）：
    input_per_m     = non_cached_cost / Σ(prompt - cached)
    cached_input_per_m = cached_cost / Σ(cached)
    output_per_m    = output_cost / Σ(completion)
```

**对账自洽**：`分项累计 token × 合并反向单价 ÷ 1M × usage_factor = 分项积分`，Σ 三分项 = 总成本，与现有 6 分项明细弹框的 `{数量} × {单价} × {系数} /1M = {积分}` 公式完全兼容。

### 改动点

| 文件 | 改动 |
|------|------|
| `src/db/models.py` | `TokenCostPriceDB.get_by_model_name` SELECT 增加 `tiered_pricing` 字段 |
| `src/services/billing.py` | ① 原 `calculate_credit_cost_with_breakdown` 内加 **tiered 单次查档分支**（当 `tcp.tiered_pricing` 存在时，按本次 `prompt_tokens` 取档计算）——所有单次调用点自动正确；② 新增 `calculate_llm_credit_cost_with_breakdown(usage_calls, model, ...)`——多轮逐档累加成本 + 返回合并反向单价，非 tiered 模型回退原逻辑 |
| `src/services/session_record.py` | ① `add_llm_usage` 追加每轮 usage 快照 `self._llm_usages`（纯内存，保留现有累计字段用于展示）；② `save()` 改调新函数传入快照列表 |
| `src/core/agent.py` | **不改**（`add_llm_usage` 内部记录快照） |
| 前端 | **不改**（`unit_prices` / `credits` 键结构不变） |

### 调用点覆盖核对

- **会话累计点**：`session_record.py save()` → 新合并函数（逐轮）。
- **单次调用点**（自动走 tiered 单次查档分支，零改动）：`_persist_background_llm_record`（background_runner 兜底）、`knowledge/service.py`（文档向量化）、`video_agent/service.py` 提示词 LLM、管理后台 LLM 路由等。
- `usage_breakdown` chat 分项建议加 `"tiered": true` 标记便于审计追溯（`_parse_breakdown_items` 不读该键，无害）。

## 三、Phase 划分

### Phase 1：表结构 + 数据访问 ✅ 预计 0.5 天

- [ ] 1.1 `deploy/init-postgres.sql` + `deploy/db_update.sql`：加 `tiered_pricing` 列 + INSERT qwen3.7-flash
- [ ] 1.2 `TokenCostPriceDB.get_by_model_name` SELECT 加 `tiered_pricing`

### Phase 2：计费函数 ✅ 预计 1 天

- [ ] 2.1 `calculate_credit_cost_with_breakdown` 加 tiered 单次查档分支
- [ ] 2.2 新增 `calculate_llm_credit_cost_with_breakdown`（多轮合并 + 反向单价）
- [ ] 2.3 单测覆盖（见测试方案）

### Phase 3：SessionRecordService 接入 ✅ 预计 0.5 天

- [ ] 3.1 `add_llm_usage` 记录每轮 usage 快照
- [ ] 3.2 `save()` 改调新合并函数 + `usage_breakdown` 加 `tiered` 标记
- [ ] 3.3 回归测试（非 tiered 模型结果与改动前一致）

### Phase 4：文档与登记 ✅ 预计 0.5 天

- [ ] 4.1 `docs/ideas.md` 系统功能区 #42 条目补充 + 开发计划列加本文件链接
- [ ] 4.2 更新 `docs/system/saas/tenant-credit-billing-design.md` 关联引用（可选）

## 测试方案

### 单元测试（`tests/unit/test_credit_billing.py` 扩展）

| 用例 | 场景 |
|------|------|
| 三档边界 | 输入 `32768`（档1）`32769`（档2）`262144`（档2）`262145`（档3）>1M 兜底档3 |
| 单次 tiered 查档 | `calculate_credit_cost_with_breakdown` 对 tiered 模型按输入取档，与手算一致 |
| 多轮合并 | `[10K, 40K]` 两轮各自查档，反向单价 = 加权平均，Σ 分项成本 = 总成本 |
| 反向单价除零 | 某分项 token 为 0（如无缓存命中）时该单价置 None，不 crash |
| 非 tiered 回归 | qwen-plus 等走原逻辑，结果与改动前一致 |

### 集成测试

- `session_record.save()`：tiered 模型多轮合并计费 + `usage_breakdown.chat` 结构断言（含 `tiered` 标记、反向单价、分项积分对账）

### 启动安全检查

- 后端：`docker exec aid-agent-api python -c "from src.services.billing import calculate_llm_credit_cost_with_breakdown"`（容器环境）/ 宿主机去掉前缀
- 前端：无改动，跳过 build

## 验收标准

1. `.env` 配 `QWEN_MODEL_CODE=qwen3.7-flash` 时，对话多轮调用逐轮按输入长度查档计费，`credit_cost` 与百炼账单口径一致（不因累计档位高估）。
2. `chat_records.usage_breakdown.chat` 含合并反向单价 + `tiered: true`，`TenantTokenUsage.vue` 每日用量明细 6 分项对账公式仍成立（前端零修改）。
3. 非 tiered 模型（qwen-plus / deepseek 等）计费结果与改动前完全一致。
4. `billing_audit.md` §3 checklist 全量核对通过（无计费缺口、无双计）。
