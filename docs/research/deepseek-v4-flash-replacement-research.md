# deepseek-v4-flash 平替模型调研

> 状态：✅ 调研完成（2026-08-16）
> 背景：deepseek-v4-flash 官方 API 价格上调至 3.0/9.0 元/百万 token（cached 0.1），需在百炼（DashScope）寻找平替模型
> 关联开发计划：[plan-qwen3-7-flash-replacement.md](../plans/plan-qwen3-7-flash-replacement.md)

## 一、背景与需求约束

deepseek-v4-flash 官方 API（api.deepseek.com）2026-08-17 起价格上调至 **3.0/9.0 元/百万 token（命中缓存 0.1）**，用户已确认并写入 `token_cost_prices`。在百炼（DashScope）平台寻找平替模型，需同时满足 3 条硬约束：

1. **支持缓存** —— 系统提示词长 + 多轮对话场景下成本敏感，无缓存则平替意义不大
2. **价格明显低于 deepseek-v4-flash（3.0/9.0）**
3. **能力 ≥ qwen-plus** —— qwen-plus 实测能力不达预期，作为能力下限

候选范围：百炼上的 qwen 系列 + 第三方（Kimi / GLM / MiniMax）。

## 二、缓存能力实测矩阵

实验方法：22K token 固定前缀重复请求 3 次。显式缓存 = 消息 content 数组 + `cache_control: {"type": "ephemeral"}`；观察 `usage.prompt_tokens_details.cached_tokens`（命中）/ `cache_creation_input_tokens`（创建）。

| 模型 | 输入/输出价（元/百万） | 显式缓存 | 隐式缓存 | 结论 |
|------|----------------------|---------|---------|------|
| deepseek-v4-flash（官方） | 3.0 / 9.0（cached 0.1） | — | 有 | 基准 |
| **qwen3.7-flash** | **0.2 / 0.8**（分段） | ✅ 命中 | ❌ 无 | **首选** |
| qwen3.6-flash | 1.2 / 7.2 | ✅ 命中 | ❌ 无 | 排除（贵） |
| qwen3.5-flash | 0.2 / 2.0 | ✅ 命中 | ❌ 无 | 备选 |
| qwen3.5-plus | 0.8 / 4.8 | ✅ 命中 | ❌ 无 | 备选 |
| qwen-plus | 0.8 / 2.0 | ✅ | ✅ | 排除（能力不达） |
| glm-4.5-air | 0.8 / 6 | 未验证 | 未验证 | 低于 deepseek，但能力/缓存不确定 |
| MiniMax-M2.5 / M2.7 | 2.1 / 8.4 | 支持 | — | 仅思考模式，延迟高 |
| MiniMax-M3 | 4.2 / 16.8 | 支持 | — | 高于 deepseek |
| Kimi 全线（k2.5 起） | 4 / 21 起 | 支持 | — | 输入输出均高于 deepseek |

**实测命中证据（qwen3.7-flash）**：

```
EXP-CALL1: cache_creation_input_tokens=21808（创建缓存，按 125% 计费）
EXP-CALL2: cached_tokens=21808（命中，按 10% 计费）
EXP-CALL3: cached_tokens=21808（命中）
```

**重要修正**：早前判定「qwen3.7-flash 无缓存」是误判 —— 当时只测了**隐式**缓存（纯字符串 content、无 `cache_control`），而 qwen3.7-flash 只支持**显式**缓存。用 `cache_control` 严格重测后命中完美，与官方文档（qwen3.7-flash 在显式缓存支持列表）一致。

**隐式缓存补充验证（2026-08-17）**：legacy 与 MaaS（`ws-hjp2aklyr0ramcne.cn-beijing.maas`）两条端点复测 25K 前缀，结论——隐式缓存**仅在默认思考模式下偶发命中（~30%）**，`enable_thinking=false` 下完全无命中（生产强制 false → 隐式对生产不可用）。显式末尾标记（cache_control 放消息数组最后一条）覆盖全数组、13/27 块均完整命中，为生产唯一可行全量缓存路径。详见[上下文缓存优化计划](../plans/plan-qwen3-7-flash-context-cache-optimization.md)。

**缓存计费口径**（官方文档）：显式缓存 **创建按输入单价 125%、命中按 10%**；隐式缓存命中按 20%（不可关闭）。显式缓存有效期 5 分钟（命中后重置）。

## 三、思考模式问题（关键发现）

qwen3.5-flash / qwen3.6-flash / qwen3.5-plus / qwen3.7-flash 默认开启**思考模式**，且 `reasoning_tokens` 不受 `max_tokens` 限制：

| 模型（默认思考） | 改写任务耗时 | 输出 tokens |
|-----------------|-------------|------------|
| qwen3.5-flash | 33s | 3533 |
| qwen3.5-plus | 191s | 11173 |
| qwen3.7-flash | 12s | 1300 |

对话式 Agent 场景完全不可接受。传 `enable_thinking: false` 关闭思考后实测：

| 任务 | 3.7-flash 关闭思考 | 3.5-flash 关闭思考 |
|------|-------------------|-------------------|
| 通知改写 | 1s / 23 tok | 1s / 21 tok |
| JSON 结构化抽取 | 1s / 79 tok（字段全对） | 1s / 77 tok |
| 多步推理（答案 6 单） | 2s / 264 tok | 3s / 287 tok |

**关闭思考后能力保持**：3 个业务任务（正式通知改写 / 客户信息 JSON 抽取 / 多步订单推理）全部正确，输出质量与思考模式一致。

## 四、能力对比

4 个模型（qwen3.5-flash / qwen3.6-flash / qwen3.5-plus / qwen-plus）在同一批业务场景题并排对比，**全部正确**，flash 档能力不弱于 qwen-plus（改写更规范、抽取字段更全、推理过程完整）。因此「能力 ≥ qwen-plus」约束满足。

## 五、排除项汇总及原因

| 排除项 | 原因 |
|--------|------|
| 百炼部署 deepseek-v4-flash（1/2 元） | 2026-08-17 与官方同步涨价，价格优势消失（aliyun notice 118555） |
| qwen3.6-flash（1.2/7.2） | 输入价是 qwen3.7-flash 的 6 倍，无任何优势 |
| qwen3.5-plus（0.8/4.8） | 输出价是 qwen3.7-flash 的 6 倍；思考模式延迟 191s 需关闭 |
| qwen-plus（0.8/2.0） | 用户实测能力不达预期（即能力下限的来源） |
| Kimi 全线 | k2.5 起 4/21，输入输出均高于 deepseek，不满足价格约束 |
| GLM（glm-5/5.1/5.2/4.6/4.7） | 3/14 起，输出高于 deepseek |
| MiniMax-M3（4.2/16.8） | 高于 deepseek |
| MiniMax-M2.5/M2.7（2.1/8.4） | 虽略低于 deepseek，但仅思考模式（延迟高），且价格不如 qwen3.7-flash |

## 六、最终推荐

**qwen3.7-flash（0.2/0.8 元/百万，分段计价）+ `enable_thinking: false` + 显式缓存（`cache_control: ephemeral`）**

三项核心均实测通过：

1. **显式缓存命中**：22K 前缀第 2 次起 `cached_tokens=21808`（命中按 10% 计费）
2. **关闭思考**：1-2s 响应、输出 23~264 tokens、3 个业务任务能力达标
3. **价格**：典型场景（2000 tok 缓存前缀 + 500 新增 + 500 输出）：

| 模型 | 单次成本 | 降幅 |
|------|---------|------|
| deepseek-v4-flash（3.0/9.0/0.1） | ≈ 0.0062 元 | — |
| **qwen3.7-flash**（0.2/0.8/cached 0.02） | ≈ **0.0005 元** | **≈ 91%** |

备选方案：qwen3.5-flash（0.2/2.0）—— 输入同价、显式缓存同样命中，但输出价为 qwen3.7-flash 的 2.5 倍，仅在 qwen3.7-flash 不可用时作为回退。

## 七、实验复现方法（附录）

- **环境**：本地脚本经 stdin 管道进容器执行 `docker exec -i aid-agent-api /opt/venv/bin/python3 - < /tmp/xxx.py`；脚本从 `/app/.env` 读取 `QWEN_API_KEYS`
- **端点**：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`（OpenAI 兼容）
- **显式缓存**：消息 content 数组 + `cache_control`；观察 `prompt_tokens_details.cache_creation_input_tokens`（创建）/ `cached_tokens`（命中）
- **关闭思考**：request_body 加 `"enable_thinking": false`，验证 `completion_tokens_details.reasoning_tokens` 为 None
- **注意**：给脚本传 CLI 参数时不能用 `<(cat file args)` 进程替换（会把参数当文件名），需用 sed 改脚本内默认值或环境变量

## 关联文档

- [小米 MiMo-v2.5 平替可行性调研（扩展）](./mimo-v2.5-replacement-research.md)（2026-08-16：mimo-v2.5 满足三条硬约束升格为第二备选，qwen3.7-flash 维持首选）
- [qwen3.7-flash 平替实施计划](../plans/plan-qwen3-7-flash-replacement.md)
- [qwen3.7-flash 分段计价开发计划](../plans/plan-qwen3-7-flash-tiered-pricing.md)
- [LLM 计费接入改造设计](../system/saas/llm-billing-integration-design.md)
