# 小米 MiMo-v2.5 平替 deepseek-v4-flash 可行性调研（扩展）

> 状态：📋 调研完成（2026-08-16）—— 官方 API 文档级评估 + 价格/缓存/兼容性已确认；能力与缓存命中率待实测
> 背景：承接 [deepseek-v4-flash-replacement-research.md](./deepseek-v4-flash-replacement-research.md)（已定案 qwen3.7-flash），补充评估小米 MiMo-v2.5 系列
> 结论先行：**小米官方 API 确认可用**（`https://api.xiaomimimo.com`，国内人民币计费）。**mimo-v2.5 满足三条硬约束，升格为第二备选**；**mimo-v2.5-pro 成本过高排除**；**qwen3.7-flash 维持首选**（成本最低 + 显式缓存确定性命中）。

---

## 一、背景与需求约束（沿用原调研）

deepseek-v4-flash 官方 API 涨价至 **3.0/9.0 元/百万 token（命中缓存 0.1）**。已在百炼定案 qwen3.7-flash（0.2/0.8 + `enable_thinking:false` + 显式缓存）为平替。本次扩展评估小米 MiMo-v2.5 系列，沿用 3 条硬约束：

1. **支持缓存** —— 系统提示词长 + 多轮对话成本敏感
2. **价格明显低于 deepseek-v4-flash（3.0/9.0）**
3. **能力 ≥ qwen-plus**

> 首轮评估（2026-08-16 上午）因拿不到小米官方 API 信息，仅据百炼（7/21 元、仅隐式缓存）判定"不采用"。下午拿到官方文档（`mimo.mi.com/docs`）后**结论修正**：官方直连价远低于百炼中转价，且支持 Prompt Cache，mimo-v2.5 满足硬约束。官方文档摘要见 [ext/mimo-mi-api-docs.md](../../ext/mimo-mi-api-docs.md)。

## 二、小米官方 API 定价（本次核心新增，权威来源）

按量计费，国内**元/百万 tokens**、海外**美元/百万 tokens**（与 OpenRouter 一致）。官方文档明确支持 **Prompt Cache**：命中前缀自动按"输入（命中缓存）"档计费，**缓存写入限时免费**。

| 模型 | 输入（命中缓存） | 输入（未命中） | 输出 | 定价区域 |
|------|-----------------|---------------|------|---------|
| mimo-v2.5 | **¥0.02** | **¥1.00** | **¥2.00** | 国内 |
| mimo-v2.5-pro | ¥0.025 | ¥3.00 | ¥6.00 | 国内 |
| mimo-v2.5 | $0.0028 | $0.14 | $0.28 | 海外 |
| mimo-v2.5-pro | $0.0036 | $0.435 | $0.87 | 海外 |

- 附带：ASR `mimo-v2.5-asr` ¥0.5/小时；TTS 系列限时免费；联网插件国内 ¥16/1000 次
- **对比百炼中转价**（7/21 元）：官方 mimo-v2.5-pro 3/6 元仅为百炼的约 43%，百炼对第三方模型加价明显

**三条硬约束逐一核对**：

| 约束 | 判断 | 依据 |
|------|------|------|
| ① 支持缓存 | ✅（命中价极低） | 官方价格表含"输入（命中缓存）"档；缓存写入限时免费 |
| ② 价格明显低于 3.0/9.0 | ✅ | mimo-v2.5 1.0/2.0（输入 3 倍低、输出 4.5 倍低、命中 5 倍低）；pro 3.0/6.0（输出 1.5 倍低、命中 4 倍低） |
| ③ 能力 ≥ qwen-plus | ✅（文档级，待实测） | Pro 级 agentic 定位；官方称 mimo-v2.5 "Pro-level agentic 性能、一半成本" |

**约束①细节（关键）**：MiMo 的缓存是**自动 Prompt Cache（隐式）**——OpenAI 兼容（`/v1/chat/completions`、`/v1/responses`）与 Anthropic 兼容（`/anthropic/v1/messages`）端点**均无 cache_control 请求参数**，命中通过 `usage.cached_tokens` / `cache_read_input_tokens` 观察。与 qwen3.7-flash 的**显式缓存**（`cache_control: ephemeral` 确定性命中）不同，MiMo 命中率不确定，**需实测验证**（官方给到命中价 0.02/M 且写入免费，若前缀命中稳定则成本极低）。

## 三、API 兼容性与接入（对现有代码友好）

| 项 | 值 |
|----|-----|
| OpenAI Chat Completions | `https://api.xiaomimimo.com/v1/chat/completions` |
| OpenAI Responses | `https://api.xiaomimimo.com/v1/responses` |
| Anthropic Messages | `https://api.xiaomimimo.com/anthropic/v1/messages` |
| 认证 | `Authorization: Bearer $MIMO_API_KEY` 或 `api-key: $MIMO_API_KEY` |
| 模型 ID | `mimo-v2.5-pro` / `mimo-v2.5` |
| 上下文 / 最大输出 | 1.05M / 131072（OpenRouter 规格） |
| 工具调用 | ✅ `tools`/`tool_choice`（非 auto 忽略，等同 auto） |
| JSON 输出 | ✅ `response_format: {type:"json_object"}` |
| 流式 | ✅ `stream: true` SSE |
| 思考模式 | `thinking: {"type":"enabled"|"disabled"}`，**默认 enabled**；关闭后支持自定义 temperature/top_p |
| 限流 | 每账号 RPM 100 / TPM 10M（所有 Key 合并） |

**与现有 qwen.py 的适配点**：已实现的 `enable_thinking`/`cache_control`/tiered 计费逻辑需按 MiMo 语义调整——思考关闭参数为 `thinking:{type:"disabled"}`；缓存无显式创建（自动命中，无 125% 创建费）；`usage` 字段名为 `cached_tokens`。

## 四、成本对比（典型场景：2000 tok 缓存前缀 + 500 新增 + 500 输出）

| 模型 | 单次成本（元） | 较 deepseek 降幅 | 较 qwen3.7-flash |
|------|---------------|-----------------|-----------------|
| deepseek-v4-flash（3.0/9.0/0.1） | ≈ 0.00620 | — | — |
| **qwen3.7-flash（首选）** | ≈ **0.00054** | **≈ 91%** | — |
| mimo-v2.5（1.0/2.0/0.02） | ≈ 0.00154 | ≈ 75% | 贵 ≈ 2.85 倍 |
| mimo-v2.5-pro（3.0/6.0/0.025） | ≈ 0.00455 | ≈ 27% | 贵 ≈ 8.4 倍 |

**结论**：qwen3.7-flash 成本仍最低（约 mimo-v2.5 的 1/3）。mimo-v2.5 相对优势是**缓存写入免费**（qwen 显式缓存创建按 125% 计费）+ **官方直连、独立厂商故障域** + **原生全模态**（image/audio/video 输入）。

## 五、限流对比：qwen3.7-flash vs MiMo（2026-08-16 补充）

> 来源：qwen3.7-flash 限流数据来自百炼控制台（RPM 30000 / TPM 5M）；MiMo 来自官方文档（见 [ext/mimo-mi-api-docs.md](../../ext/mimo-mi-api-docs.md) §6）。

| 指标 | qwen3.7-flash（百炼） | MiMo（官方直连） | 结论 |
|------|---------------------|------------------|------|
| RPM | **30000** | **100** | qwen 胜，**300 倍** |
| TPM | 5M | **10M** | MiMo 胜，2 倍 |
| 限流粒度 | KeyPool 多 Key 可线性放大 | **账号级，所有 Key 合并** | qwen 可扩容，MiMo 结构性上限 |

**分析**：
- **RPM 是决定性约束**：本项目是 agent 循环（每条用户消息触发多次 LLM 调用，最长 20 轮）+ 目标数百并发用户。以 300 并发 × 5 次调用/消息 ≈ 1500 次/分钟计，MiMo 100 RPM 只能满足 **7%**，其余全部 429；qwen 30000 RPM 轻松容纳。
- **TPM 2 倍优势无法补偿**：qwen 5M TPM 按典型负载（每条调用输入 3000~5000 token）≈ 1250 次/分钟 ≈ 250~400 活跃用户消息/分钟，正好覆盖"数百并发"量级；而 MiMo 10M ÷ 100 RPM = 每条请求上限 10 万 token，是"大请求少次数"形态，与本项目短请求密集场景不匹配。
- **限流粒度差异**：MiMo 限流为账号级（所有 API Key 合并计算），加 Key 无法扩容；qwen 走 KeyPool 多 Key 池，限流可随 Key 数量线性放大。差距是结构性、不可通过堆 Key 弥补的。

**结论**：限流维度 **qwen3.7-flash 结构性更优**（高 RPM + 中 TPM 精确匹配 agent 多轮短请求负载）。MiMo 文档级即可判定**主链路承载不足**（只能支撑数十并发，且需排队 + 429 指数退避），进一步强化其"仅备选/容灾/多模态"定位。

## 六、能力评估（待实测）

- mimo-v2.5-pro：旗舰，agentic/软件工程/长任务基准（ClawEval、GDPVal、SWE-bench Pro）排名靠前，能力等级高于 qwen-plus 无疑
- mimo-v2.5：官方称 "Pro-level agentic 性能、约一半成本"，能力大概率 ≥ qwen-plus
- **未在本项目 3 个业务任务（改写/JSON 抽取/多步推理）实测**；且默认开思考模式（延迟高），需验证 `thinking:{type:"disabled"}` 关闭后能力保持

## 七、排除项/降级项汇总

| 项 | 结论 | 原因 |
|----|------|------|
| 百炼 mimo-v2.5-pro（7/21） | 排除 | 官方直连 3/6 即可，百炼加价 2.3 倍且仅隐式缓存 |
| OpenRouter mimo 接入 | 排除 | 美元结算/国内可达性/合规问题；官方国内直连 `api.xiaomimimo.com` 更优 |
| mimo-v2.5-pro 做主模型 | 排除 | 成本 0.00455 元/次，为 qwen3.7-flash 的 8.4 倍；仅在需旗舰/长任务能力时按需调用 |
| MiMo 作主链路 | 排除 | 限流承载不足：RPM 100（账号级全 Key 合并）只能支撑数十并发，无法承载数百并发主链路（见 §五） |
| **mimo-v2.5 做第二备选** | ✅ 升格 | 三条硬约束满足 + 官方直连 + 缓存写入免费 + 全模态（**仅限容灾/低并发场景**） |

## 八、最终结论与建议

1. **首选不变：qwen3.7-flash**（0.2/0.8 + `enable_thinking:false` + 显式缓存）——成本最低（0.00054 元/次，较 deepseek 降幅 91%）、显式缓存确定性命中，已开发完成。
2. **第二备选：mimo-v2.5（官方直连 1.0/2.0/命中 0.02）**——三条硬约束全部满足，较 deepseek 降幅 75%。价值：独立厂商故障域（不依赖百炼）+ 缓存写入免费 + 原生全模态。**但限流承载不足（RPM 100 只能支撑数十并发，见 §五），不可作主链路**。仅在 qwen3.7-flash 不可用（如百炼故障/显式缓存失效）时的容灾降级，或未来需要多模态输入的低并发场景。
3. **mimo-v2.5-pro 不接入主链路**，成本高，仅作为按需旗舰模型。
4. **待实测项（决定是否正式接入）**：
   - [ ] 自动缓存命中率实测（22K 前缀重复请求，观察 `cached_tokens`）
   - [ ] `thinking:{type:"disabled"}` 关闭后 3 个业务任务能力 + 延迟/输出量
   - [x] ~~限流承载评估~~（2026-08-16 **已文档级判定承载不足**：RPM 100 账号级 vs qwen 30000，仅能支撑数十并发，见 §五；若仍要接入需排队 + 429 指数退避实测）
   - [ ] 计费字段对齐（`cached_tokens` 无 125% 创建费，需改 `_parse_response` 与 tiered 逻辑）

## 九、数据来源与可信度

| 数据 | 来源 | 可信度 |
|------|------|--------|
| 官方价格/缓存/端点/thinking/限流/错误码 | 小米官方文档 `mimo.mi.com/docs`（2026-08-16 抓取，摘要见 ext/mimo-mi-api-docs.md） | 高 |
| 百炼中转价（mimo-v2.5-pro 7/21） | 百炼官方价格文档（ext/qwen-llm-price.md） | 高 |
| OpenRouter 价格/规格（= 官方海外价） | OpenRouter 模型 API | 高 |
| 上下文 1.05M / 最大输出 131072 | OpenRouter 规格（官方文档未单列） | 中 |
| 能力 ≥ qwen-plus / 缓存命中率 / 关闭思考后能力 | 未实测 | 待验证 |

## 关联文档

- [deepseek-v4-flash 平替模型调研](./deepseek-v4-flash-replacement-research.md)（主调研，已定案 qwen3.7-flash）
- [小米 MiMo API 官方文档摘要](../../ext/mimo-mi-api-docs.md)（价格/缓存/API/限流/错误码速查）
- [qwen3.7-flash 平替实施计划](../plans/plan-qwen3-7-flash-replacement.md)
- [qwen3.7-flash 分段计价开发计划](../plans/plan-qwen3-7-flash-tiered-pricing.md)
