# Xiaomi MiMo API 官方文档摘要

> 来源：https://mimo.mi.com/docs/zh-CN/ 官方文档（价格/速率限制/模型超参/错误码/OpenAI API/Responses API/Anthropic API 共 7 页）
> 抓取日期：2026-08-16；最后更新时间以各页标注为准（价格 2026-08-06，速率 2026-06-29，超参 2026-07-17，错误码 2026-07-15）
> 背景：MiMo-V2 系列 2026-06-30 已下线，现为 MiMo-V2.5 系列。

## 1. 按量计费价格（pay-as-you-go）

- 计费单位：国内 **元/百万 tokens**；海外 **美元/百万 tokens**
- **缓存命中**：请求前缀命中 Prompt Cache 时按命中缓存价计费
- **缓存写入：限时免费**
- 联网搜索按调用次数独立计费（不含在 Token 价格中）；ASR 按输入音频时长计费

### 国内定价（元/百万 tokens）

| 模型 | 输入（命中缓存） | 输入（未命中缓存） | 输出 |
|------|-----------------|-------------------|------|
| mimo-v2.5-pro | ¥0.025 | ¥3.00 | ¥6.00 |
| mimo-v2.5 | ¥0.02 | ¥1.00 | ¥2.00 |
| mimo-v2.5-asr（ASR） | — | ¥0.5/小时（按音频时长） | — |
| mimo-v2.5-tts / -voiceclone / -voicedesign（TTS） | 限时免费 | 限时免费 | — |

### 海外定价（美元/百万 tokens，与 OpenRouter 一致）

| 模型 | 输入（命中缓存） | 输入（未命中） | 输出 |
|------|-----------------|--------------|------|
| mimo-v2.5-pro | $0.0036 | $0.435 | $0.87 |
| mimo-v2.5 | $0.0028 | $0.14 | $0.28 |

### 联网服务插件

| 服务 | 价格 | 说明 |
|------|------|------|
| 国内联网服务 | ¥16/1000 次 | 网页搜索 + 网页解析 |
| 海外联网服务 | $5/1000 次 | 网页搜索 + 网页解析 |

## 2. 缓存机制（重要）

- **自动 Prompt Cache（隐式缓存）**：命中自动按"输入（命中缓存）"档计价，**不支持显式 cache_control**（OpenAI 兼容与 Anthropic 兼容端点均无 cache_control 请求参数；响应中通过 `usage.cached_tokens` / `cache_read_input_tokens` 查看命中量）
- **缓存写入限时免费**（对比百炼 qwen 显式缓存创建按输入价 125% 计费）
- 命中价极低：mimo-v2.5 ¥0.02/M、mimo-v2.5-pro ¥0.025/M（约为未命中价的 2% / 0.8%）

## 3. API 端点与认证

三套兼容接口，均支持 `api-key: $MIMO_API_KEY` 或 `Authorization: Bearer $MIMO_API_KEY` 认证：

| 接口 | 端点 |
|------|------|
| OpenAI Chat Completions | `https://api.xiaomimimo.com/v1/chat/completions` |
| OpenAI Responses | `https://api.xiaomimimo.com/v1/responses` |
| Anthropic Messages | `https://api.xiaomimimo.com/anthropic/v1/messages` |

模型 ID：`mimo-v2.5-pro`、`mimo-v2.5`（另 ASR `mimo-v2.5-asr`、TTS 系列）。
上下文长度 1.05M、最大输出 131072（OpenRouter 规格，官方文档未单列）。

## 4. 思考模式（thinking）

- 请求体参数：`thinking: {"type": "enabled" | "disabled"}`，**默认 enabled**（默认开思考）
- 思考模式下：
  - 返回 `reasoning_content`（OpenAI）/ `thinking` 内容块（Anthropic）
  - **多轮工具调用必须把历史 `reasoning_content` 完整回传**，否则 400（错误码表明确）
  - **不支持自定义 temperature/top_p**（强制默认 1.0 / 0.95）
- 关闭思考（`type: "disabled"`）后应支持自定义 temperature（范围 [0,1.5]）、top_p（[0.01,1.0]）

## 5. 工具/输出能力

- **Function calling**：`tools`/`tool_choice` 支持；`tool_choice` 非 auto 会被后端忽略（等同 auto）
- **JSON 输出**：`response_format: {type: "json_object"}`（需消息中指示生成 JSON）
- **流式**：`stream: true` SSE
- **其他**：`stop`（最多 4 序列）、`max_tokens`、`seed` 等标准参数；多模态（mimo-v2.5 原生 text+image+audio+video 输入，404 表示不支持图像输入的接口/模型）

## 6. 限流（每账号，所有 API Key 合并计算）

| 模型 | RPM | TPM |
|------|-----|-----|
| mimo-v2.5-pro / mimo-v2.5 | 100 | 10M |
| mimo-v2.5-asr | 100 | 10K |
| TTS 系列 | 100 | 10M |

负载高时可能 429，需指数退避重试。

## 7. 错误码速查

| 错误码 | 原因 | 解决 |
|--------|------|------|
| 400 | 请求体格式错 / 多模态文件不符 / **思考模式未回传 reasoning_content** | 按提示检查；思考模式完整回传 reasoning_content |
| 401 | API Key 无效 / **混用 Token Plan 与按量付费 Key** | 检查 key 与请求头；Token Plan 用专属 Base URL+Key |
| 402 | 余额不足 | 充值 |
| 403 | 地区不支持 / Key 被风控 | 新建 Key |
| 404 | 接口/模型不支持图像输入 | 换多模态接口 |
| 421 | 内容审核拦截 | 避免不安全内容 |
| 429 | 请求超限 / Token Plan 额度耗尽 | 指数退避重试 / 升套餐 |
| 500/503 | 服务器故障/负载高 | 稍后重试 |

## 8. 模型超参

temperature 默认 1.0（范围 [0,1.5]）；top_p 默认 0.95（范围 [0.01,1.0]）。思考模式下两者均不可自定义。

## 关键陷阱（接入时注意）

1. **思考模式默认开启**，若要快速响应需显式传 `thinking:{type:"disabled"}`；开启时多轮必须回传 reasoning_content 否则 400
2. **缓存是自动的**，无显式 cache_control——命中率需实测（与 qwen3.7-flash 显式确定性命中不同）
3. Token Plan 与按量付费是两套 Key，混用报 401
4. 限流 RPM 100 为账号级（所有 Key 合并），高并发需评估
5. 官方直连价格远低于百炼中转价（百炼 mimo-v2.5-pro 7/21 元 vs 官方 3/6 元）
