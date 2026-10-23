# 故障复盘索引

> 线上故障与生产事故的复盘文档独立索引（2026-10-09 由 `docs/ideas.md`「故障复盘索引」分区迁出）。本文件是纯索引：`摘要` 只写一句话要点，过程与结论见各复盘文档。
> 新增故障复盘 → 先在本索引登记一行，再写复盘文档；复盘不进入 `ideas.md`/`ideas_finished.md` 开发条目（由修复任务条目另行关联）。

## 事故复盘

| 故障主题 | 文档 | 摘要 |
|---------|------|------|
| 2026-10-23 BOSS问题统一开发交接 | [boss-agent2-issues-and-minimal-repair-handoff-20261023.md](boss-agent2-issues-and-minimal-repair-handoff-20261023.md) | 最小修复467项及CR通过；0.0.26/0.3.24组合包已验，现场待验。 |
| 招聘筛选235秒慢操作与登录后恢复封锁 | [recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md](recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md) | 2026-10-20：慢输入待后续；存量未知执行已支持受管资源停稳解锁，独立验证通过。 |
| 数字员工定制弹框扩展提示词不回显：迁移块原地改写致错键数据残留 | [tenant-agent-instance-migration-drift-incident.md](tenant-agent-instance-migration-drift-incident.md) | 2026-10-16 测试环境：10-14 迁移块草稿版落库后原地改写但 datetime 未变，水位已过的库永久漏跑，直用实例行 agent_id 错用 tenant_id 致弹框/列表覆盖项命不中（运行时靠 registry 回退掩盖）；补偿块纠偏结构与数据 + 运行时收敛为实例行权威（空即清空） |
| Web 会话 subagent_id 为空显示「主智能体」 | [web-session-subagent-display-mislabel-incident.md](web-session-subagent-display-mislabel-incident.md) | 2026-09-07 生产：隐式建会话不写 subagent_id 致显示错标，实际执行/计费均正确（after-sales）；2026-10-16 复核问题已不存在，残余风险=隐式建会话路径仍不回写，新增聊天入口需检查 |
| 微信客服读取异常宽表导致 Runner 中断 | [wecom-kf-excel-read-runner-interrupted-20261015.md](wecom-kf-excel-read-runner-interrupted-20261015.md) | 2026-10-15生产：Excel重复维度扫描阻塞222秒，旧KB数据未迁metadata；补迁与修复见关联计划。 |
| Runtime协议3配对持续500：存量迁移水位跳过注册字段 | [runtime-registration-migration-watermark-incident.md](runtime-registration-migration-watermark-incident.md) | 2026-10-14：agent2水位18:00高于新增17:00迁移，字段未建；追加22:45幂等补偿。 |
| Runtime安装向导点击安装后直接退出 | [runtime-installer-missing-fixture-env-incident.md](runtime-installer-missing-fixture-env-incident.md) | 2026-10-14：可选测试变量缺失留下NSIS错误标志，正常安装误判失败；隔离测试环境掩盖问题。 |
| qwen3.7-flash 工具结果缓存数组化回显故障复盘 | [qwen-tool-message-cache-echo-incident.md](qwen-tool-message-cache-echo-incident.md) | 2026-08-19 线上故障：显式缓存"末尾标记"把 tool 消息 content 数组化（违反 OpenAI 兼容规范），qwen3.7-flash 概率性(~7%)… |
| 数据分析智能体 max_tokens 截断致空结论误判"无数据" | [analysis-agent-empty-conclusion-incident.md](analysis-agent-empty-conclusion-incident.md) | 2026-08-26 生产故障：AnalysisAgent 硬编码 max_tokens=4000，deepseek-v4-pro 推理模型烧穿预算返回空结论（completion 恰达上限 + co… |
| BOSS 批量读简历 0 份入库且日志无线索 | [boss-resume-batch-empty-incident.md](boss-resume-batch-empty-incident.md) | 2026-09-10 客户现场：boss_resume_batch 大部分卡片点击无反应打不开详情、打开的详情与卡片姓名不符（0.2.9 曾把王亦菲简历存到任玮鹤名下）、… |
| 数据分析智能体单次 392 积分消耗复盘 | [analysis-agent-cost-392-credits-incident.md](analysis-agent-cost-392-credits-incident.md) | 2026-09-20 生产：重分析任务 6 次 analyze_data 内层 200+ 调用致 1319 万 prompt token（95% 在内层循环）；压缩只摘 preview 压不住、tool_calls 参数不压缩；含 create_plan TypeError 崩溃（已修 fc24dfa9 待部署）与内层成本优化细化方案 |
| 用户取消会话计费缺失复盘 | [user-cancel-billing-gap-incident.md](user-cancel-billing-gap-incident.md) | 2026-09-22：web 手动取消的轮次 chat_records 无落账（392 积分事故第 1 次尝试同因未落账）；根因 = 前端先断连后 POST /cancel + main.py CancelledError except 内先 yield 后落账（anyio cancel scope 内 send 再抛 CancelledError 致落账不可达）；修复 = 后端先落账再 yield + 前端先 cancel 后 disconnect；含「菜单切换取消」历史行为澄清（2026-07-20 多会话后台流式后已不存在） |
| 企微客服 20 用户并发演示 sync_msg 限频（45009） | [wecom-kf-sync-msg-45009-incident.md](wecom-kf-sync-msg-45009-incident.md) | 2026-10-11 生产：约 20 用户同时进入致微信 sync_msg 限频 47 秒全失败，积压 31 条串行排空，首批用户端到端等 1-3 分钟；根因 = 回调并发拉取无去重 + 45009 无重试，非 DeepSeek/服务器；修复 = 指数退避重试 + per-open_kfid 拉取去重 |
| agent1 提交连续 500：queue_order 序列被 SQL 直插污染 | [analysis-agent1-runner-submit-500-uniqueviolation-incident.md](analysis-agent1-runner-submit-500-uniqueviolation-incident.md) | 2026-10-13 仿真：10-12 测试脚本直插 agent_runners 17 行显式写 queue_order=34~50 但 BIGSERIAL 序列停在 33，正常提交持续撞 agent_runners_queue_order_key 致 /api/chat/runners 连续 17 次 500 约 4.5 分钟，前端重试烧号到 51 偶然自愈；修复 = 提交入口捕获该约束冲突后 setval 对齐 GREATEST(max,last_value) 重试一次 |

## 经验速查与关联调研

| 主题 | 文档 | 摘要 |
|------|------|------|
| 对话上下文重建避坑速查 | [context-reconstruction-pitfalls.md](context-reconstruction-pitfalls.md) | 消息历史加载/窗口裁剪 5 大陷阱(来源分流、最近N条、对齐user、表分离、默认值漂移) |
| 企业微信客服上下文丢失调研报告 | [wecom-kf-context-loss-research.md](wecom-kf-context-loss-research.md) | wecom_kf 连续提问间上次 assistant 回复未进上下文的问题调研；§9~§10 提炼出读取侧两大病根，修复见 ideas_finished 20260820-1623/1624 |
