# BOSS 简历读取链路提速（一期）— 开发计划

> 关联设计：[boss-resume-read-speedup-design.md](../system/boss-resume-read-speedup-design.md)
> ideas 条目：20261110-1003（docs/ideas.md 数字员工 / 子智能体分区）
> 流程：高风险（风控敞口 + 简历读取正确性）→ 三智能体（开发 → 独立测试 → CR）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 等待参数随机化 + 轮询间隔收紧（决策 2-7） | 🔧 进行中（开发+测试完成，CR 审查中） | 新增 timing.ts jitter；定向测试 69/69 过；回归 90 文件仅 4 个基线预存失败（独立基线对照核实） |
| Phase 2 | 快照校验精简（决策 8） | 📋 待开发 | — |
| Phase 3 | 回归测试 + 真机验收 | 📋 待开发 | — |
| 二期 | 拼接算法升级（8 格）+ 评分埋点与并行化（决策 9/10） | 📋 待评估 | 本期交付后立项 |

## Phase 1 — 等待参数（对应设计 §3.1/§3.2）

1. 新增 `src/main/boss/timing.ts`：`jitter(minMs, maxMs)` 均匀随机整数，导出供单测
2. `src/main/boss/ResumeReader.ts`：
   - `SEGMENT_REPAINT_DELAY` 900 → 区间 {500,600}，`:406` 调用点改 `jitter`
   - `SCROLL_TOP_SETTLE_DELAY` 1500 → 区间 {500,600}，`:354` 同上
   - `BOTTOM_CONFIRM_DELAY` 1800 → 1000，`:383` 定值不变
3. `src/main/boss/ResumeBatchReader.ts`：
   - `CARD_PACE_DELAY` 2000 → 区间 {500,600}，`:341` 改 `jitter`（风控权衡项，独立常量可一键调回）
   - `OPEN_POLL_INTERVAL` 600 → 400（`:532` 循环不变）
   - `CLOSE_POLL_INTERVAL` 500 → 250（`:660` 循环不变）
4. perf 埋点 `sleep:{ms}` 名称随实际值变化，属预期，不改统计口径
5. 单测：jitter 边界；各调用点 sleep 入参区间；`openAttempts` 随新间隔的正确性

## Phase 2 — 快照校验精简（对应设计 §3.3）

1. `ResumeReader.capturePart` 增加 `first: boolean`：首段前后双快照+双遮挡断言；后续段仅截后一次（rect 比对 + `assertResumeUnobstructed`）
2. 中止语义不变：画布消失/几何变化/遮挡 → fail-loud 抛 `ResumeReadError`
3. 单测：首段 2 次/后续段 1 次快照计数；消失/遮挡中止回归；重复段补滚补拍路径回归

## Phase 3 — 回归与真机验收

1. 客户端全量相关回归（Node 测试，容器/宿主按客户端既有命令；不涉及 aid-agent-api pytest）
2. 真机验收（依赖用户提供 BOSS 账号客户机）：
   - 同场景对比单份 P50（基线 46.6s，目标 ~37-39s）与单批总时长
   - 质量不升：suspectSeams 出现率、bottomReached=false 次数、失败率
   - 风控观察：连续筛选 30+ 份无列表刷新异常/账号受限
3. 收尾：本计划进度表、ideas.md 状态同步、`toolDefs.ts:11` 的"约 30 秒"注释更新为实测口径
4. 部署由用户当场指示；提交关联 `文档: docs/plans/plan-boss-resume-read-speedup.md`

## 二期预登记（本期交付后评估）

1. 拼接对齐算法升级（特征匹配）+ 滚动步长回 8 格：预期再省 2-3s/份
2. 评分耗时埋点（服务端编排层）→ 数据出来后评估评分并行化/读评流水线
