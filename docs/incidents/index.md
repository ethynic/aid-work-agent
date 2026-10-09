# 故障复盘索引

> 线上故障与生产事故的复盘文档独立索引（2026-10-09 由 `docs/ideas.md`「故障复盘索引」分区迁出）。本文件是纯索引：`摘要` 只写一句话要点，过程与结论见各复盘文档。
> 新增故障复盘 → 先在本索引登记一行，再写复盘文档；复盘不进入 `ideas.md`/`ideas_finished.md` 开发条目（由修复任务条目另行关联）。

## 事故复盘

| 故障主题 | 文档 | 摘要 |
|---------|------|------|
| qwen3.7-flash 工具结果缓存数组化回显故障复盘 | [qwen-tool-message-cache-echo-incident.md](qwen-tool-message-cache-echo-incident.md) | 2026-08-19 线上故障：显式缓存"末尾标记"把 tool 消息 content 数组化（违反 OpenAI 兼容规范），qwen3.7-flash 概率性(~7%)… |
| 数据分析智能体 max_tokens 截断致空结论误判"无数据" | [analysis-agent-empty-conclusion-incident.md](analysis-agent-empty-conclusion-incident.md) | 2026-08-26 生产故障：AnalysisAgent 硬编码 max_tokens=4000，deepseek-v4-pro 推理模型烧穿预算返回空结论（completion 恰达上限 + co… |
| BOSS 批量读简历 0 份入库且日志无线索 | [boss-resume-batch-empty-incident.md](boss-resume-batch-empty-incident.md) | 2026-09-10 客户现场：boss_resume_batch 大部分卡片点击无反应打不开详情、打开的详情与卡片姓名不符（0.2.9 曾把王亦菲简历存到任玮鹤名下）、… |
| 数据分析智能体单次 392 积分消耗复盘 | [analysis-agent-cost-392-credits-incident.md](analysis-agent-cost-392-credits-incident.md) | 2026-09-20 生产：重分析任务 6 次 analyze_data 内层 200+ 调用致 1319 万 prompt token（95% 在内层循环）；压缩只摘 preview 压不住、tool_calls 参数不压缩；含 create_plan TypeError 崩溃（已修 fc24dfa9 待部署）与内层成本优化细化方案 |
| 用户取消会话计费缺失复盘 | [user-cancel-billing-gap-incident.md](user-cancel-billing-gap-incident.md) | 2026-09-22：web 手动取消的轮次 chat_records 无落账（392 积分事故第 1 次尝试同因未落账）；根因 = 前端先断连后 POST /cancel + main.py CancelledError except 内先 yield 后落账（anyio cancel scope 内 send 再抛 CancelledError 致落账不可达）；修复 = 后端先落账再 yield + 前端先 cancel 后 disconnect；含「菜单切换取消」历史行为澄清（2026-07-20 多会话后台流式后已不存在） |

## 经验速查与关联调研

| 主题 | 文档 | 摘要 |
|------|------|------|
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| 企业微信客服上下文丢失调研报告 | [wecom-kf-context-loss-research.md](wecom-kf-context-loss-research.md) | wecom_kf 连续提问间上次 assistant 回复未进上下文的问题调研；§9~§10 提炼出读取侧两大病根，修复见 ideas_finished 20260820-1623/1624 |
