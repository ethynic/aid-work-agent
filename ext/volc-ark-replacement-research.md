# 火山方舟模型平替调研摘要

> 调研日期：2026-08-16。目的：检索火山方舟（Volcengine Ark）提供的文本模型，评估有无可平替 qwen3.7-flash（百炼 0.2/0.8 元/百万 token）的选项。
> 数据来源：用户下载的火山方舟官方「模型价格」文档 `ext/volc-llm-price.md`；qwen3.7-flash 基准见 `ext/qwen-llm-price.md`。

## 结论

**火山方舟没有能全面平替 qwen3.7-flash 的文本模型。** 最接近的 doubao-seed-1.6-flash（0.15/1.5）输入端更便宜，但输出端贵 ~1.9 倍；典型 agent 场景（输入:输出≈3:1）混合单价约 1.95 元/M vs qwen3.7-flash 1.4 元/M，贵 ~40%；叠加显式缓存命中后差距扩大到 ~60%。**qwen3.7-flash 维持首选，无需迁移。**

## 火山方舟低价位文本模型（在线推理·常规，元/百万 token）

| 模型 | 输入（最低段） | 输出（最低段） | 缓存命中 | 相对 qwen3.7-flash（0.2/0.8） |
|------|------|------|------|------|
| doubao-seed-1.6-flash | 0.15 | 1.50 | 0.03 | 输入更便宜，输出贵 ~1.9 倍 |
| doubao-seed-2.0-mini | 0.20 | 2.00 | 0.04 | 输入持平，输出贵 2.5 倍 |
| doubao-seed-1.6-lite | 0.30 | 0.60 | 0.06 | 输出便宜 25%，输入贵 1.5 倍 |
| doubao-1.5-lite-32k | 0.30 | 0.60 | 0.06 | 同 1.6-lite，较老 |
| doubao-seed-1.6 / 1.8 | 0.80 | 2.00 | 0.16 | 全面更贵 |
| doubao-seed-2.0-lite | 0.60 | 3.60 | 0.12 | 全面更贵 |

其余豆包模型（seed-2.0-pro/code、2.1-turbo/pro 等）输入 ≥3 元/M，智谱 glm-5.2/4.7、deepseek-v4-pro 均更贵，不构成平替。

## 关键点

1. **缓存机制差异**：火山方舟为透明前缀缓存（自动命中，无需配置），命中价 0.03；百炼 qwen 为显式/隐式缓存，显式命中按标准输入 10%（0.02），另按输入 125% 计缓存创建费。qwen 缓存成本更低。
2. **火山方舟托管 deepseek-v4-flash**：现价 1.0/2.0，2026-08-21 起调价至 3.0/9.0，比 qwen3.7-flash 贵 5 倍以上。
3. **能力未实测**：doubao-seed 系列 agent 工具调用能力未经本项目实测。若因合规/已有火山云资源必须切换，doubao-seed-1.6-flash 是最接近的价格档，值得做一次工具调用实测。

## 关联

- 快照：[ext/volc-llm-price.md](volc-llm-price.md)（火山方舟模型价格全文）
- 入口：`ext/llm-doc-entrances.md`（火山方舟模型价格文档 URL）
- 上游调研：[docs/research/deepseek-v4-flash-replacement-research.md](../docs/research/deepseek-v4-flash-replacement-research.md)
