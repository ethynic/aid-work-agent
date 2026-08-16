# 小米 MiMo-v2.5 平替 deepseek-v4-flash 可行性调研（扩展）

> 状态：✅ 调研 + 实测完成（2026-08-16）—— 官方 API 文档级评估 + 缓存命中率/能力实测 + tokenizer 成本修正
> 背景：承接 [deepseek-v4-flash-replacement-research.md](./deepseek-v4-flash-replacement-research.md)（已定案 qwen3.7-flash），补充评估小米 MiMo-v2.5 系列
> 结论先行：**小米官方 API 确认可用**（`https://api.xiaomimimo.com`，国内人民币计费），mimo-v2.5 **缓存确定性命中（99.8%）、关闭思考后能力达标**；**但实测 tokenizer 对中文膨胀 2.5~4.5x（典型~3x），实际成本 ≈ 0.0046~0.007 元/次 ≈ deepseek 无优势**，叠加限流承载不足，**mimo-v2.5 降级为容灾/多模态备选**；**mimo-v2.5-pro 成本过高排除**；**qwen3.7-flash 维持首选**（成本最低 0.00054 元/次 + 显式缓存确定性命中）。

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

> ⚠️ **2026-08-16 实测修正**：本表 token 数为 **qwen tokenizer** 口径。实测发现 **MiMo tokenizer 对中文 token 膨胀 2.5~4.5 倍**（同一文本 MiMo prompt_tokens 为 qwen 的倍数，见 §6.3），MiMo 行成本已按膨胀 3x（典型业务）折算，故与文档最初版本（按 token 直接比）差异显著。

| 模型 | 单次成本（元，按 qwen token 口径） | 较 deepseek | 较 qwen3.7-flash |
|------|---------------|-----------------|-----------------|
| deepseek-v4-flash（3.0/9.0/0.1） | ≈ 0.00620 | — | — |
| **qwen3.7-flash（首选）** | ≈ **0.00054** | **降 91%** | — |
| mimo-v2.5（1.0/2.0/0.02，膨胀 3x） | ≈ **0.0046** | 降 ≈ 25%（或相当） | **贵 ≈ 8.5 倍** |
| mimo-v2.5-pro（3.0/6.0/0.025，膨胀 3x） | ≈ 0.0136 | 贵 ≈ 2.2 倍 | 贵 ≈ 25 倍 |

**结论（实测修正后）**：qwen3.7-flash 成本仍最低（约 mimo-v2.5 的 1/8.5）。**MiMo 实际成本 ≈ 0.0046~0.007 元/次，与 deepseek-v4-flash 相当（甚至略低）**，相比"按 token 直接比"的 0.00154 已被膨胀放大 3 倍左右，**不再具备"成本显著低于 deepseek"的选型意义**（硬约束②成立但无选型价值）。mimo-v2.5 剩余价值仅为：**缓存写入免费**（qwen 显式缓存创建按 125% 计费，命中场景命中价相同 0.02/M，但新增/输出仍被膨胀放大）+ **官方直连、独立厂商故障域** + **原生全模态**（image/audio/video 输入）。

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

## 六、实测验证（2026-08-16，mimo-v2.5 vs qwen3.7-flash，均关思考）

> 环境：`api.xiaomimimo.com/v1/chat/completions` + `thinking:{"type":"disabled"}`；qwen3.7-flash + `enable_thinking:false`；temperature 0；脚本见 `/tmp/mimo_exp.py`（容器内）。MIMO_API_KEY 已配置于容器 `/app/.env`。

### 6.1 自动缓存命中率 ✅ 确定性命中

22K 前缀（23854 prompt tokens），重复 3 次：

| 调用 | 延迟 | `prompt_tokens_details.cached_tokens` |
|------|------|----------------------------------------|
| CALL1 | 3.5s | 192（写入） |
| CALL2 | 2.0s | **23808（99.8%）** |
| CALL3 | 1.7s | **23808（99.8%）** |

- MiMo 自动 Prompt Cache 与 qwen3.7-flash 显式缓存**同样确定性命中**（命中价均 0.02/M），且 MiMo 缓存写入免费。
- **注意字段位置**：命中量在 `usage.prompt_tokens_details.cached_tokens`，不在顶层 `usage.cached_tokens`（顶层恒为 0），计费解析需按此字段对齐（对应待实测项「计费字段对齐」）。

### 6.2 能力对比（3 个业务任务）✅ 全部达标

| 任务 | MiMo 延迟/输出 | qwen3.7-flash 延迟/输出 | 质量对比 |
|------|---------------|------------------------|---------|
| 改写 | 1.5s / 34 tok | 0.5s / 25 tok | 平手，均专业完整 |
| JSON 抽取 | 2.1s / 65 tok | 0.6s / 60 tok | **MiMo 略优**（purchase_intent 抽完整描述，qwen 仅"高"） |
| 多步推理 | 9.0s / 385 tok | 8.3s / 990 tok | 均正确（0.78 万=7800 元），MiMo 输出更精炼 |

- 关闭思考后能力保持，3 任务全部达标（≥ qwen-plus 的工程判断成立）。
- **延迟差异**：短任务 MiMo 慢 ~3 倍（1.5s vs 0.5s），长推理接近（9.0s vs 8.3s）。

### 6.3 ⚠️ tokenizer 膨胀（重大成本发现，2026-08-16 二次实测细化）

**方法**：对同一文本分别请求 MiMo 与 qwen3.7-flash（均关思考），对比两侧 `prompt_tokens`（脚本 `/tmp/tok_verify.py`）。并已用 MiMo 平台导出的消费记录 CSV（`/mnt/d/temp/usage_data_20260801_20260831_*.csv`，2026-08-16 一行 = 本实验 6 次请求）逐项核对：hit=48384/miss=24161/out=676/total=73221、费用 0.026481 元全部吻合，计费口径命中 0.02、未命中 1.0、输出 2.0 元/M 确认无误（CSV 本身无字数，无法直接算膨胀，膨胀须靠同文本两侧请求验证）。

**多文本实测倍数**：

| 文本特征 | MiMo prompt | qwen prompt | MiMo/Qwen |
|---------|------------|-------------|-----------|
| 业务任务（改写/JSON/推理，含字段、英文、数字，非重复） | 309/342/332 | 68/101/85 | **3.4~4.5x** |
| 自然中文 ~280 字（非重复） | 409 | 161 | **2.54x** |
| 自然中文 1000 字（重复拼接） | 812 | 535 | 1.52x |
| 英文 50 词 | 755 | 517 | 1.46x |
| para ×200（**极端重复**，不具业务代表性） | 23854 | 21817 | 1.09x |

- **膨胀非固定倍数**，随文本特征变化：**典型业务文本（自然中文 + 字段/英文/数字混合）≈ 2.5~4.5x（均值约 3x）**；纯自然中文约 2.5x；仅极端重复文本接近 1.1x（业务不存在，且疑似受平台对重复内容的计数优化影响）。
- 按 API 实际计费（按 token），MiMo 输入/输出/命中成本约放大 3 倍，**实际成本 ≈ 0.0046~0.007 元/次，与 deepseek-v4-flash 相当（甚至略低），不具成本优势**——此前「较 deepseek 降幅 75%」按 token 直接比较的结论**已修正失效**（见 §四）。
- 例外场景：**若业务输入恰好是高度重复的模板文本（如固定长 system prompt 重复拼接），膨胀接近 1x**，此时 MiMo 成本优势（命中 0.02 且写入免费）才可能体现。

**成因（非官方故意，技术差异）**：tokenizer 在模型预训练前按训练语料的词频训练后即固化，改动 tokenizer 意味着全部 token 序列变化、**必须重新预训练**，成本极高，一般厂商不会为此事后优化中文。qwen 对中文 tokenizer 的优化（中文 1 字 < 1 token）是阿里的专项投入，属业界领先水平；MiMo 的词表规模/训练语料对中文覆盖未达同等程度，导致中文被切得更碎。从商业动机看：若想多收钱直接涨价即可（同行都按 token 计价），且 MiMo 在缓存上明确让利（命中 0.02 元/M + 写入免费），与"靠 tokenizer 暗箱多收"矛盾——**更可能是训练语料以英文为主 + 词表/中文优化投入不足带来的无意劣势**。且实测膨胀随文本变化（极端重复仅 1.1x），非均匀膨胀，也佐证非刻意设计。

### 6.4 多模态对标：qwen-vl 系列（2026-08-16 补充）

MiMo-v2.5 是**原生全模态**（text+image+audio+video→text），合理对标对象是 **qwen-vl 系列（纯视觉 image→text）**，而非 qwen3.7-flash（纯文本）。本项目多模态使用点：

| 场景 | 模型 | 位置 |
|------|------|------|
| 视频提示词生成 | **qwen-vl-plus（默认）/ qwen-vl-max** | `src/core/agent.py:2207`、`src/api/video_gen.py:84-85`，`source_type=video_prompt` 独立计费 |
| 图片理解/子智能体图片输入 | 走主模型（`image_url` base64 透传 gateway） | `src/core/agent.py:3762 _build_multimodal_user_content` |

价格对标（元/M，国内）：

| 模型 | 输入 | 输出 | 模态 |
|------|------|------|------|
| qwen-vl-plus | 0.8 | 2 | 视觉 |
| qwen-vl-max | 1.6 | 4 | 视觉 |
| qwen3-vl-plus | 1 | 10 | 视觉 |
| **MiMo-v2.5** | **1.0** | **2.0**（命中 0.02） | **全模态** |

- 名义价格 MiMo 处于 vl-plus 与 vl-max 之间（输入高于 vl-plus 0.8、低于 vl-max 1.6；输出等于 vl-plus、低于 vl-max 4.0），初看比 vl-max 便宜 50%。
- **但 tokenizer 膨胀（§6.3）后，文本部分等效输入 ~3 元/M、输出 ~6 元/M，实际比 qwen-vl-plus 贵 ~3 倍、比 qwen-vl-max 贵 ~1.5~2 倍**。注意多模态请求的 token 主要为图片 visual tokens（两侧 patch/分辨率切分策略不同，膨胀系数不直接套用），但 system prompt、提示词上下文等文本部分确实膨胀。
- 叠加限流 RPM 100 账号级，无法承载视频提示词/图片理解这类高频小请求。
- **唯一差异化**：原生 audio/video 输入（qwen-vl 仅 image；音频需 qwen-audio、视频需抽帧走 qwen-vl 或 qwen-omni）。但本项目当前无 audio/video 输入调用点。
- **结论**：MiMo-v2.5 作为「qwen-vl 系列多模态备选」，在成本（膨胀后）、限流、现有调用点三个维度均无法替代 qwen-vl-plus/vl-max；仅在未来出现**音频/视频原生输入**需求时才有差异化价值。

## 七、排除项/降级项汇总

| 项 | 结论 | 原因 |
|----|------|------|
| 百炼 mimo-v2.5-pro（7/21） | 排除 | 官方直连 3/6 即可，百炼加价 2.3 倍且仅隐式缓存 |
| OpenRouter mimo 接入 | 排除 | 美元结算/国内可达性/合规问题；官方国内直连 `api.xiaomimimo.com` 更优 |
| mimo-v2.5-pro 做主模型 | 排除 | 成本 0.00455 元/次，为 qwen3.7-flash 的 8.4 倍；仅在需旗舰/长任务能力时按需调用 |
| MiMo 作主链路 | 排除 | 限流承载不足：RPM 100（账号级全 Key 合并）只能支撑数十并发，无法承载数百并发主链路（见 §五） |
| **mimo-v2.5 做成本优势备选** | ❌ 降级 | **2026-08-16 实测 tokenizer 膨胀 2.5~4.5x，实际成本 ≈ deepseek 无优势**（见 §6.3） |
| MiMo 做 qwen-vl 多模态平替 | ❌ 不采用 | 名义价虽低于 vl-max，但膨胀后实际贵于 vl-plus/vl-max，且限流承载不足；仅 audio/video 原生输入场景有差异化价值（见 §6.4） |
| **mimo-v2.5 做容灾/多模态备选** | ✅ 保留 | 缓存确定性命中 + 关闭思考后能力达标 + 官方直连独立故障域 + 全模态；仅限容灾/低并发/音频视频输入场景 |

## 八、最终结论与建议

1. **首选不变：qwen3.7-flash**（0.2/0.8 + `enable_thinking:false` + 显式缓存）——成本最低（0.00054 元/次，较 deepseek 降幅 91%）、显式缓存确定性命中，已开发完成。
2. **mimo-v2.5 降级为容灾/多模态备选**（官方直连 1.0/2.0/命中 0.02）——**2026-08-16 实测**：缓存确定性命中（99.8%）、关闭思考后能力达标、但 **tokenizer 膨胀 2.5~4.5x 致实际成本 ≈ deepseek 无优势**；叠加限流承载不足（RPM 100，见 §五）；**多模态维度对标 qwen-vl 系列（视频提示词模型）同样无优势**（膨胀后实际贵于 vl-plus/vl-max，见 §6.4）。仅在 qwen3.7-flash 不可用（百炼故障/显式缓存失效）时容灾降级，或未来**音频/视频原生输入**的低并发场景。
3. **mimo-v2.5-pro 不接入主链路**，成本高，仅作为按需旗舰模型。
4. **实测/待实测项**：
   - [x] 自动缓存命中率（2026-08-16 实测 **99.8% 确定性命中**，见 §6.1）
   - [x] `thinking:{type:"disabled"}` 后 3 个业务任务能力（2026-08-16 实测**全部达标**，见 §6.2）
   - [x] ~~限流承载评估~~（已文档级判定承载不足：RPM 100 账号级 vs qwen 30000，仅能支撑数十并发，见 §五；若仍要接入需排队 + 429 指数退避实测）
   - [ ] 计费字段对齐（命中量在 `prompt_tokens_details.cached_tokens` 不在顶层 `usage.cached_tokens`；无 125% 创建费，需改 `_parse_response` 与 tiered 逻辑）——**若决定接入则必改**

## 九、数据来源与可信度

| 数据 | 来源 | 可信度 |
|------|------|--------|
| 官方价格/缓存/端点/thinking/限流/错误码 | 小米官方文档 `mimo.mi.com/docs`（2026-08-16 抓取，摘要见 ext/mimo-mi-api-docs.md） | 高 |
| 百炼中转价（mimo-v2.5-pro 7/21） | 百炼官方价格文档（ext/qwen-llm-price.md） | 高 |
| OpenRouter 价格/规格（= 官方海外价） | OpenRouter 模型 API | 高 |
| 上下文 1.05M / 最大输出 131072 | OpenRouter 规格（官方文档未单列） | 中 |
| 缓存命中率 / 关闭思考后能力 / tokenizer 膨胀 | **2026-08-16 实测**（`api.xiaomimimo.com`，MIMO_API_KEY，脚本 `/tmp/mimo_exp.py`，见 §六） | 高 |

## 关联文档

- [deepseek-v4-flash 平替模型调研](./deepseek-v4-flash-replacement-research.md)（主调研，已定案 qwen3.7-flash）
- [小米 MiMo API 官方文档摘要](../../ext/mimo-mi-api-docs.md)（价格/缓存/API/限流/错误码速查）
- [qwen3.7-flash 平替实施计划](../plans/plan-qwen3-7-flash-replacement.md)
- [qwen3.7-flash 分段计价开发计划](../plans/plan-qwen3-7-flash-tiered-pricing.md)
