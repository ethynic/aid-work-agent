# 摘要生成模型性价比实测报告

> 实验日期：2026-09-11。用真实知识库文档实测 deepseek-v4-flash 与 qwen3.8-flash 的摘要生成 token 消耗与成本，验证"仅凭单价表无法对比"的前提（不同模型 tokenizer 不同，同一文档的 prompt tokens 差约 9%）。
>
> 复现脚本：[scripts/benchmark_summary_models.py](../../scripts/benchmark_summary_models.py)。GLM-5.3-Flash 因智谱 key 余额不足（code 1113）未能实测，待充值后补测。

## 1. 实验设计

- **文档样本**：生产库 `documents` 表取 5 篇（id 24/25/2224/2238/2271），覆盖 pdf/docx/xlsx 三种类型、1012~5424 字符正文，含政策文件、产品说明、行程、企业名单、操作手册。
- **提示词**：与 `KnowledgeBaseService.generate_summary` 完全一致（正文截断 5000 字符，要求不超过 300 字中文摘要），temperature=0.3。
- **思考开关**：关思考参数与 `chat_lite` 同源（deepseek `thinking.type=disabled`，qwen `enable_thinking=False`）；开思考为模型默认（deepseek `enable_thinking=True`、qwen `enable_thinking=True`）。
- **与生产 lite 通道的差异（有意为之）**：max_tokens 统一 2048（生产为 500，思考开启时会被 reasoning 烧穿导致 content 为空，无法公平对比）；直连 Provider 不走 gateway（不落计费、不触发 failover）。
- **计价公式**：`成本 = (prompt - cached) × input单价 + cached × cached单价 + completion × output单价`（元/百万 token）。

| 模型 | input | output | cached_input | 备注 |
|------|-------|--------|--------------|------|
| deepseek-v4-flash | 2.0 | 8.0 | 0.04 | |
| qwen3.8-flash | 0.8 | 2.7 | 0.16 | |
| GLM-5.3-Flash | 0.8 | 2.8 | 0.8 | **未实测**（key 欠费） |

## 2. 结果汇总（5 篇文档合计）

| 模型 | 思考 | prompt tokens | completion tokens | cached | 总成本（元） | 单次均成本（元） | 每千次成本（元） | 平均耗时 |
|------|------|--------------|-------------------|--------|-------------|-----------------|-----------------|---------|
| deepseek-v4-flash | 关 | 10,539 | 443 | 0 | 0.0246 | 0.00492 | 4.92 | 1.20s |
| deepseek-v4-flash | 开 | 10,669 | 3,655 | 2,688 | 0.0453 | 0.00906 | 9.06 | 3.93s |
| qwen3.8-flash | 关 | 11,245 | 530 | 0 | 0.0104 | 0.00209 | 2.09 | 2.62s |
| qwen3.8-flash | 开 | 11,425 | 2,466 | 0 | 0.0158 | 0.00316 | 3.16 | 9.31s |

要点：

- **关思考时 qwen3.8-flash 比 deepseek-v4-flash 便宜 58%**（单价差 2.5 倍，因 qwen prompt tokens 多约 9%、completion 略多，实际差距略小于单价差）。
- **开思考时 qwen3.8-flash 便宜 65%**：deepseek 思考 token 明显更多（3,655 vs 2,466），且输出单价是 qwen 的约 3 倍。
- **思考开启成本翻倍**：deepseek +84%，qwen +52%，全部来自 reasoning completion tokens。
- **速度上 deepseek 全面占优**：关思考 1.20s vs 2.62s（快 2.2 倍），开思考 3.93s vs 9.31s（快 2.4 倍）。
- **摘要长度相近**：各组合平均 151~186 字符，均在 300 字限制内，finish_reason 全部为 stop。

## 3. 明细数据

| 模型 | 思考 | doc_id | prompt | completion | cached | 耗时 | 成本（元） | 摘要字符 |
|------|------|--------|--------|------------|--------|------|-----------|---------|
| deepseek-v4-flash | 关 | 24 | 3,461 | 113 | 0 | 1.06s | 0.007826 | 214 |
| deepseek-v4-flash | 关 | 25 | 1,700 | 72 | 0 | 0.67s | 0.003976 | 115 |
| deepseek-v4-flash | 关 | 2224 | 674 | 73 | 0 | 1.29s | 0.001932 | 124 |
| deepseek-v4-flash | 关 | 2238 | 1,882 | 98 | 0 | 1.71s | 0.004548 | 142 |
| deepseek-v4-flash | 关 | 2271 | 2,822 | 87 | 0 | 1.25s | 0.006340 | 160 |
| deepseek-v4-flash | 开 | 24 | 3,487 | 1,050 | 0 | 5.70s | 0.015374 | 197 |
| deepseek-v4-flash | 开 | 25 | 1,726 | 531 | 0 | 2.85s | 0.007700 | 144 |
| deepseek-v4-flash | 开 | 2224 | 700 | 405 | 0 | 2.93s | 0.004640 | 146 |
| deepseek-v4-flash | 开 | 2238 | 1,908 | 462 | 0 | 3.01s | 0.007512 | 180 |
| deepseek-v4-flash | 开 | 2271 | 2,848 | 1,207 | 2,688 | 5.15s | 0.010084 | 186 |
| qwen3.8-flash | 关 | 24 | 3,795 | 113 | 0 | 3.28s | 0.003341 | 204 |
| qwen3.8-flash | 关 | 25 | 1,847 | 99 | 0 | 3.04s | 0.001745 | 172 |
| qwen3.8-flash | 关 | 2224 | 726 | 84 | 0 | 1.83s | 0.000808 | 156 |
| qwen3.8-flash | 关 | 2238 | 2,047 | 128 | 0 | 2.59s | 0.001983 | 205 |
| qwen3.8-flash | 关 | 2271 | 2,830 | 106 | 0 | 2.35s | 0.002550 | 191 |
| qwen3.8-flash | 开 | 24 | 3,831 | 683 | 0 | 18.05s | 0.004909 | 126 |
| qwen3.8-flash | 开 | 25 | 1,883 | 409 | 0 | 6.16s | 0.002611 | 144 |
| qwen3.8-flash | 开 | 2224 | 762 | 500 | 0 | 9.05s | 0.001960 | 148 |
| qwen3.8-flash | 开 | 2238 | 2,083 | 442 | 0 | 5.96s | 0.002860 | 178 |
| qwen3.8-flash | 开 | 2271 | 2,866 | 432 | 0 | 7.31s | 0.003459 | 173 |

注：deepseek 在 doc 2271（开思考）命中了 2,688 个隐式缓存 tokens（同前缀第二次调用），已按 0.04 元/百万计价——生产同前缀高频调用（如批量摘要回填）时 deepseek 的缓存优势会比本表更明显。

## 4. GLM-5.3-Flash 缺口与运维风险

- 本地 `.env`、生产容器（aid-agent-api）、测试容器（aid-agent-api2）三处的智谱 key 调用 GLM-5.3-Flash 均返回 `429 code=1113 余额不足或无可用资源包,请充值`，实验中断。
- **运维风险**：生产 failover 链路为 deepseek → qwen → zhipu，智谱 key 欠费意味着**最后一跳备用通道实际已失效**；若 deepseek 与 qwen 同时故障，系统将无可用 LLM。建议尽快充值或更换 key。
- GLM 单价与 qwen3.8-flash 接近（input 0.8 vs 0.8，output 2.8 vs 2.7），但 cached_input 单价 0.8 是 qwen（0.16）的 5 倍，且为唯一多模态通道；补测后按同表格式追加数据。

## 5. 结论

1. **现网 lite 配置（`LITE_MODEL_CODE=qwen/qwen3.8-flash`，关思考）即成本最优解**：摘要场景每千次 2.09 元，比 deepseek 关思考（4.92 元）省 58%，比开思考的任何组合省 34%~65%。
2. **deepseek 适合对延迟敏感、成本不敏感的主链路**（快 2.2~2.4 倍）；批量后台任务（摘要回填、 富集、分类）继续走 qwen lite 通道。
3. **摘要场景无必要开启思考**：成本 +52%~84%、耗时 +2~3 倍，摘要长度与完成率无可见收益（未做人工质量评估，如需可后续补质量盲评）。
4. GLM-5.3-Flash 待智谱 key 充值后按 §1 同口径补测 10 次调用，追加至 §2/§3 表格。

## 附：实验局限

- 样本仅 5 篇文档、单次调用无重复，token 数存在随机波动（temperature=0.3）。
- 未评估摘要质量差异，成本结论不构成质量结论。
- prompt tokens 因 tokenizer 不同天然存在约 9% 差异（qwen 偏多），对比以实测成本为准而非单价。
