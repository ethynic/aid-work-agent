# qwen3.7-flash 平替 deepseek-v4-flash 实施计划

> 状态：🔧 开发完成待实测（2026-08-16 Phase 1/2/4 完成，Phase 3 真实对话实测待部署切换模型后执行）
> 关联调研：[deepseek-v4-flash 平替模型调研](../research/deepseek-v4-flash-replacement-research.md)
> 关联：[系统功能 #42 租户积分充值与计费](../ideas.md)
> 计费审计：本次改动涉及计费配置（token_cost_prices cached 单价修正）与 LLM 调用参数（enable_thinking / 显式缓存），按 [billing_audit.md](../../.claude/rules/billing_audit.md) §3 核对。

## 背景

deepseek-v4-flash 官方 API 价格上调至 **3.0/9.0/0.1 元/百万 token**。经调研（[deepseek-v4-flash-replacement-research.md](../research/deepseek-v4-flash-replacement-research.md)），选定百炼 **qwen3.7-flash**（0.2/0.8 分段计价）作为平替，配合 **`enable_thinking: false`**（关闭思考，1-2s 响应）与 **显式缓存**（`cache_control: ephemeral`，命中按 10% 计费），实测典型场景成本较 deepseek-v4-flash 降幅约 91%。

**前置状态**：qwen3.7-flash 分段计价（`token_cost_prices.tiered_pricing`）已在 [plan-qwen3-7-flash-tiered-pricing.md](./plan-qwen3-7-flash-tiered-pricing.md) 落地（2026-08-15）。本次只需：① provider 层加 `enable_thinking` / 显式缓存开关；② 切换模型配置；③ 修正 cached 单价（20% → 10%）。

## 设计决策

1. **`enable_thinking` 由配置控制**：`config.yaml llm.enable_thinking`（默认 `false`），qwen provider 发请求时写入 `request_body`。仅 qwen 推理模型（qwen3.x-flash/plus 系列）生效，不影响其他 provider 与模型。**必须显式关闭**：默认思考模式下 `reasoning_tokens` 不受 `max_tokens` 限制，延迟 12~191s、输出暴涨 1K~11K tokens，对话场景不可用。
2. **显式缓存由配置开关控制**：`config.yaml llm.context_cache`（默认 `true`），qwen provider 将**第一条 system 消息** content 转为数组形式 + `cache_control: {"type": "ephemeral"}`。**仅 qwen provider 的 qwen 系模型生效**（按 model 名 `qwen` 前缀判定）——qwen provider（百炼）也承载 deepseek/kimi/glm 等第三方模型，其缓存参数语义/计费与 qwen 系不同，不得误加 `cache_control`。
3. **缓存计费比例修正**：官方显式缓存命中按 **10%**（创建按 125%），而现有 `tiered_pricing.cached_input_per_m` 按 20% 填入（0.04/0.12/0.24），成本会高估一倍。修正为 **0.02/0.06/0.12**。
4. **缓存命中边界**：`cache_control` 标记向前回溯最多 **20 个 content 块**；同一会话内 system 提示词保持稳定前缀是命中前提。长会话（>20 条消息）system 前缀命中会失效——窗口裁剪后首条 system 仍带缓存标记即可恢复命中。
5. **切换范围**：仅影响 `LLM_PROVIDER=qwen`、model 为 qwen 系（`qwen` 前缀）且配置了 qwen3.7-flash 的环境；同 provider（百炼）下的 deepseek/kimi/glm、其他 provider（zhipu）及 qwen-plus 等均不受影响（开关按 model 名判定）。

## 一、改动点

| 文件 | 改动 |
|------|------|
| `configs/config.yaml` | `llm` 增加 `enable_thinking: false`、`context_cache: true`；`llm.model` 切 `qwen3.7-flash`（或部署环境用 `.env QWEN_MODEL_CODE`） |
| `src/config/settings.py` | `llm` 同步 `enable_thinking` / `context_cache` 字段（默认值与 config.yaml 一致） |
| `src/llm/providers/qwen.py` | `chat()` / `stream_chat()` 的 `request_body` 加 `enable_thinking`（读 `settings.llm.enable_thinking`，None 时不写）；`context_cache` 开启时调用 `_format_messages` 的缓存模式 |
| `src/llm/providers/base.py` | `_format_messages` 增加缓存分支：`context_cache` 开启且 model 为 qwen 系（`qwen` 前缀）时把第一条 system 消息转为数组 + `cache_control`；其余消息逻辑不变。由 qwen provider 传入开关（默认关闭，其他 provider / 百炼第三方模型不受影响） |
| `deploy/init-postgres.sql` + `deploy/db_update.sql` | qwen3.7-flash `cached_input_per_m` 修正 20% → 10%（0.04→0.02 / 0.12→0.06 / 0.24→0.12），`db_update.sql` 带 2026-08-16 注释 |
| `.env` | `QWEN_MODEL_CODE=qwen3.7-flash`（部署时切换） |

### 关键实现示意

`src/llm/providers/base.py` `_format_messages` 缓存分支：

```python
# 仅 qwen provider 且 settings.llm.context_cache=True 时启用
if use_cache and role == "system" and not formatted:
    formatted.append({"role": "system", "content": [
        {"type": "text", "text": str(content), "cache_control": {"type": "ephemeral"}}
    ]})
else:
    # 原有逻辑不变
    formatted.append({"role": role, "content": str(content)})
```

`src/llm/providers/qwen.py` `chat()`：

```python
request_body = {"model": self.model, "messages": self._format_messages(messages),
                "max_tokens": max_tokens, "temperature": temperature}
if settings.llm.enable_thinking is not None:
    request_body["enable_thinking"] = settings.llm.enable_thinking
```

## 二、Phase 划分

### Phase 1：provider 参数（enable_thinking + 显式缓存开关） 预计 0.5 天

- [ ] 1.1 `config.yaml` + `settings.py` 新增 `llm.enable_thinking` / `llm.context_cache`
- [ ] 1.2 `qwen.py` `chat()` / `stream_chat()` request_body 加 `enable_thinking`
- [ ] 1.3 `base.py` `_format_messages` 缓存分支 + `qwen.py` 接线开关
- [ ] 1.4 单测覆盖（见测试方案）

### Phase 2：配置切换 + 计费核对 预计 0.5 天

- [x] 2.1 `init-postgres.sql` + `db_update.sql` 修正 cached 单价 20% → 10%（0.04/0.12/0.24 → 0.02/0.06/0.12，db_update.sql 2026-08-16 追加 UPDATE 条目）
- [x] 2.2 `config.yaml` `llm.model` / `.env` `QWEN_MODEL_CODE` 切 `qwen3.7-flash`
- [x] 2.3 核对 `billing.py` tiered 逻辑对 cached 单价取值正确（读 `tiered_pricing.cached_input_per_m`）
- [x] 2.4 **显式缓存创建计费（125%）**：缓存创建 token 按输入价 125% 独立核算（官方口径），不再混入 non_cached 按 100% 计费
  - `qwen.py _parse_response` 读 `prompt_tokens_details.cache_creation_input_tokens` → 透出 usage.cache_creation_tokens
  - `billing.py` tiered（`calculate_llm_credit_cost_with_breakdown` 逐轮 `creation×input_price×1.25`）+ 非 tiered（`calculate_credit_cost_with_breakdown` 新增 `cache_creation_input_tokens` 参数，`max(prompt-cached-creation,0)` 剔除）→ breakdown 新增 `cache_creation_input_per_m`/`cache_creation_input` 分项
  - 计费入口透传：session_record 主 save/background/admin 三路径、client_binding_db、memory_summarizer、work_outcome_review、knowledge 摘要、reports 日报生成
  - 管理后台用量明细 6 分项扩为 7 分项（新增「缓存创建输入」列：后端 `_parse_breakdown_items` + 前端 `TenantTokenUsage.vue`/`billing.ts`）
  - 单测：tiered 单次/合并/非 tiered/无缓存单价兜底 4 用例 + qwen `_parse_response` 3 用例；86 单测 + 9 明细 API 集成 + 84 报告/客户端回归全绿

### Phase 3：实测验证 预计 0.5 天

- [ ] 3.1 真实对话一次：usage 中 `cached_tokens` > 0（同会话第二条起）且 `reasoning_tokens` 为 None
- [ ] 3.2 计费落库核对：`credit_cost` 按分段 + cached 10% 单价计算
- [ ] 3.3 回归：qwen-plus / deepseek / zhipu 等模型调用不受影响（开关默认值不影响非 qwen provider）

### Phase 4：文档登记 预计 0.5 天

- [ ] 4.1 `docs/ideas.md` 调研报告索引 + 系统功能 #42 条目补充开发计划链接
- [ ] 4.2 更新调研文档/计划文档的关联引用

## 三、测试方案

### 单元测试（`tests/unit/`）

| 用例 | 场景 |
|------|------|
| `_format_messages` 缓存开 | `context_cache=True` 时第一条 system 转数组 + `cache_control`，后续消息不变 |
| `_format_messages` 缓存关 | `context_cache=False` 时全部走原逻辑（字符串 content） |
| 非首条 system 不缓存 | 只有第一条 system 加 cache_control |
| `chat()` enable_thinking | 配置 `true` / `false` / `None` 三态，request_body 正确写/不写该键 |
| 非 qwen 系 model 不缓存 | 百炼第三方模型（如 `deepseek-*` / `kimi-*` / `glm-*`）即便 `context_cache=True` 也不加 `cache_control`，且 `enable_thinking` 不写入 |

### 集成测试

- 走 `TestClient` 发一次对话（mock LLM 响应），断言 `request_body` 含 `enable_thinking=false` + 首条 system content 数组 + `cache_control`

### 启动安全检查

```bash
docker exec aid-agent-api python -c "from src.config.settings import create_settings; s=create_settings(); print(s.llm.enable_thinking, s.llm.context_cache)"
docker exec aid-agent-api python -c "from src.llm.providers.qwen import QwenProvider"
```

## 四、验收标准

1. `.env` 配 `QWEN_MODEL_CODE=qwen3.7-flash` 时，真实对话响应 1-3s，`usage.prompt_tokens_details.cached_tokens` > 0（同会话第二条起），`reasoning_tokens` 为 None。
2. `chat_records` 计费按 qwen3.7-flash 分段计价 + cached 10% 单价，与百炼账单一致（不因 20% 高估成本）。
3. 关闭开关（`enable_thinking` / `context_cache`）或使用其他模型（qwen-plus / deepseek / zhipu）时，行为与改动前完全一致（回归）。
4. `billing_audit.md` §3 checklist 全量核对通过（无新增计费缺口、无双计）。
