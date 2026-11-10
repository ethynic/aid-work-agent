# BOSS 简历读取链路提速（一期：等待参数与校验精简）— 设计文档

> 设计日期：2026-11-10
> 状态：设计定稿（10 项决策已逐条与陆伟确认），待开发
> 开发计划：[plan-boss-resume-read-speedup.md](../plans/plan-boss-resume-read-speedup.md)
> 需求来源：飞书任务清单 #10（国腾数字：boss直聘筛选简历慢）

---

## 一、背景与生产数据

简历筛选链路（`clients/boss-resume-assistant`）：点卡片开详情 → 轮询弹层 → 回顶 → 分段滚动截图（到底三重判定）→ 拼接长图 → Escape 关闭 → 下一位；文本识别与评分在云端。

生产实测（trace，近 30 天，37 批次 / 100 份尝试）：

| 指标 | 实测值 |
|---|---|
| 单份简历端到端（工具 span 内） | **均值 46.6s，P50 46.1s，P90 58.2s，最大 86.5s** |
| boss_resume_batch 单批（~2.7 份） | 均值 143s，P90 191s |

代码注释中的"单份约 30 秒"（`toolDefs.ts:11`）已过时，实测慢 55%。按代码常量推算，其中纯固定 sleep 约 12-14s/份，页面结构快照校验约 2-5s/份。分步 perf 埋点（`[boss-perf]` 行）落在客户机本地 runtime.log，服务端无汇聚——本设计按生产总耗时 + 代码常量推算定参（决策 1），真机验收验证。

## 二、设计决策（2026-11-10 逐条确认）

| # | 决策点 | 现值 | 定稿 |
|---|---|---|---|
| 1 | 定参依据 | — | 生产数据 + 常量推算，真机验收验证 |
| 2 | 段间重绘等待 `SEGMENT_REPAINT_DELAY` | 固定 900ms/段 | **随机 500-600ms**；复用现有重复检测兜底（字节差<200 → 像素确认 → 补滚补拍），不新增机制 |
| 3 | 回顶后等待 `SCROLL_TOP_SETTLE_DELAY` | 固定 1500ms | **随机 500-600ms**；回顶滚动保留（虚拟滚动读不到位置，防护必需） |
| 4 | 开详情轮询 `OPEN_POLL_INTERVAL` | 600ms | **400ms**（首次查询随间隔提前） |
| 5 | 关弹层轮询 `CLOSE_POLL_INTERVAL` | 500ms | **250ms** |
| 6 | 到底确认加长等待 `BOTTOM_CONFIRM_DELAY` | 1800ms | **1000ms**；双确认流程保留（防懒加载假到底→简历截断） |
| 7 | 相邻候选人拟人间隔 `CARD_PACE_DELAY` | 固定 2000ms | **随机 500-600ms**。理由：单份处理 40s+、滚动事件密集，间隔对拟人度影响小。风控权衡项：独立常量，真机见风控异常一键调回 |
| 8 | 每段截图前后双快照校验 | 2 次/段 | **首段保留前后双校验，后续段仅截图后一次**（滚动为自发行为，段间几何不会自变；弹窗中途出现由截后校验拦截） |
| 9 | 滚动步长 5→8 格（拼接算法升级为特征匹配） | 5 格 | **二期专项** |
| 10 | 评分并行化/流水线（服务端编排） | 串行 | **二期专项**；本期先补评分耗时埋点 |

预期收益：单份 46.6s → **约 37-39s（-17~20%）**；单批 3 份 143s → 约 115s。

## 三、详细设计

### 3.1 等待参数随机化（决策 2/3/6/7）

- 新增工具函数 `jitter(minMs, maxMs)`（boss-resume-assistant 内共用，如 `src/main/boss/timing.ts`），返回 `[min, max]` 内均匀随机整数；导出供单测。
- 常量改语义为区间：
  - `SEGMENT_REPAINT_DELAY = { min: 500, max: 600 }`（`ResumeReader.ts:123`）
  - `SCROLL_TOP_SETTLE_DELAY = { min: 500, max: 600 }`（`ResumeReader.ts:114`）
  - `BOTTOM_CONFIRM_DELAY = 1000`（`ResumeReader.ts:127`，保持定值）
  - `CARD_PACE_DELAY = { min: 500, max: 600 }`（`ResumeBatchReader.ts:158`）
- 调用点改为 `this.sleep(jitter(min, max))`；`deps.sleep` 注入机制不变，单测断言 sleep 入参落在区间（注入固定 random 或断言边界）。
- perf 埋点沿用：`sleep:{ms}` 名称随实际值变化，属预期（分步统计按前缀聚合即可，本期不改统计口径）。

### 3.2 轮询间隔（决策 4/5）

- `OPEN_POLL_INTERVAL = 400`（`ResumeBatchReader.ts:152`）：打开循环 `sleep(OPEN_POLL_INTERVAL)` → 首查即 400ms；`openAttempts = ceil(OPEN_POLL_TIMEOUT/OPEN_POLL_INTERVAL)` 自动变 20，重点逻辑（`OPEN_RECLICK_AFTER`/`OPEN_CLICK_ATTEMPTS`）不变。
- `CLOSE_POLL_INTERVAL = 250`（`ResumeBatchReader.ts:162`）。
- 列表滚动等待（`ResumeBatchReader.ts:421` 的 1200/1800ms、`:391` 的 1200ms）**本期不动**（每次滚动轮间才发生，占比小；避免与签名比对推进逻辑耦合）。

### 3.3 快照校验精简（决策 8）

- `ResumeReader.capturePart`（`ResumeReader.ts:441`）增加参数 `first: boolean`：
  - 首段：截图前后各一次快照 + `assertResumeUnobstructed`（现状）；
  - 后续段：仅截图后一次快照 + 校验（rect 与 `activeRect` 比对 + 遮挡断言）。
- 画布消失/几何变化/弹窗遮挡的中止语义不变（fail-loud），只是校验时机从"前后各一次"减为"截后一次"。
- `ResumeBatchReader` 内部透传段序号，无需感知。

### 3.4 明确不改的部分

- 到底判定三层逻辑（字节差 → 像素确认 → 连续两次相同）不动；`MAX_SEGMENTS`/`MAX_CAPTURES` 不动。
- 5 格滚动步长不动（二期随算法升级评估回 8 格）。
- Win32 真实滚轮/点击方式不动（CDP 合成事件被风控拦截，真机实证）。
- 列表滚动、续筛、恢复/检查点逻辑不动。

## 四、风险与回滚

| 风险 | 缓解 |
|---|---|
| 重绘等待过短拍到重复段 | 现有三层到底判定 + 补滚补拍天然兜底；`suspectSeams`/`bottomReached=false` 指标在验收中监控不升 |
| 到底确认 1000ms 不足致懒加载假到底 → 简历截断 | 保留双确认流程；验收对比截断告警数 |
| 卡片间隔缩短触发风控 | 独立常量 `CARD_PACE_DELAY`，异常时一键调回 2000ms；验收期观察列表刷新/账号状态 |
| 快照精简漏检中途弹窗 | 截后校验仍全量执行（几何 + 遮挡断言），首段仍有基准双校验 |

回滚：全部改动为常量与局部逻辑，单提交可整体 revert。

## 五、验证

- 单测（`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`，客户端为 Node 测试，按客户端既有测试命令执行）：
  - jitter 区间断言；各调用点 sleep 入参落在新区间
  - capturePart：首段双快照、后续段单快照；消失/遮挡中止语义回归
  - 重复段补滚补拍路径回归（现有用例迁移）
- 真机验收（依赖：客户提供 BOSS 账号客户机，用户安排）：
  - 同账号同筛选场景对比：单份 P50、单批总时长
  - 质量指标不升：suspectSeams 出现率、bottomReached=false 次数、失败率
  - 风控观察：连续筛选 30+ 份无列表刷新异常/账号受限
- 二期预登记（本期交付后评估）：① 拼接特征匹配对齐 + 8 格滚动；② 评分耗时埋点 → 评分并行化/流水线。
