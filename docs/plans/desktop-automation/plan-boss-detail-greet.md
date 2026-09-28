# BOSS 详情页打分即打招呼（筛选主路径改造）

## 背景与目标

现有打招呼主路径在推荐牛人**列表页**（boss_greet 按姓名/全量点卡片按钮）。筛选人多时
（几百人、合格几十人）列表页找人要滚动翻页，很麻烦。目标：**详情页打分即打招呼**成为
筛选主路径——打开候选人详情 → 云端 VL 打分 → 分数合格直接点详情页「打招呼」按钮；
列表页 greet 命令保留（手动/AI 可调），但不进简历筛选流程。

## 真机探查结论（2026-09-28，attach 9222 推荐牛人页，两轮实测）

- 详情弹层：canvas（locateResumeCanvas 可命中，约 x=168,y=40,w≈740,h≈1208，宽度会漂移
  739→743），**canvas 右侧操作列为纯 DOM**：收藏 / 举报 / 不合适 一行（cy≈109），
  **「打招呼」按钮在其正下方**（center≈(1085,159)，42x16）；全在 document 与列表同源。
- 列表页卡片「打招呼」恒为 x≈1151 列（每 184px 一行）；与详情按钮横向区带交叉，
  **纯几何（x/y）区分不可靠**（第一行列表按钮 cy=146 与详情按钮 cy=159 仅差 13px）。
- **可靠区分 = DOM 结构**：操作列四节点（收藏/举报/不合适/打招呼）DOM 序连续（4376/
  4384/4388/4395），是紧凑子树。用 `LCA(收藏,举报,不合适)` 得操作列容器，再
  `isDescendantOf` 判定哪个 打招呼 在容器内（工具已有：domSnapshot.ts，FilterSetter
  同套范式）。要求容器内恰好 1 个。
- 详情页头部候选人为 DOM 文本（如「谢正意」@(318,129)）——可做强校验：canvas 顶部区
  （y∈[canvas.y, canvas.y+300]，x∈canvas 列）内精确匹配预期姓名，防打错人。
- Escape 关闭详情可靠（两轮 CLOSED_OK）；点击后按钮预期翻转「继续沟通」（CONTINUE_TEXT，
  与列表按钮同状态机，开发时真机复核）。
- 探查脚本：`.tmp/probe-detail-greet.mjs`、`.tmp/probe-detail-greet2.mjs`（gitignored）。

## 方案

### 云端编排（新增能力不改既有语义）

筛选流程由云端 AI 编排（打分在云端 VL，CLI 不做判断），每候选人四步：

1. `boss_open_detail --name X`（新）：GreetExecutor 同源卡配对（含滚动找人）→ Win32 点
   姓名节点开详情（ResumeBatchReader 同源）→ 校验 canvas + 详情姓名==X，详情保持打开。
2. `boss_resume_detail --name X`（既有）：读取打开中的详情 → 云端 VL 评估入库（读完不关，
   既有行为不变）。
3. 合格 → `boss_greet_detail --name X`（新）：校验详情仍开且姓名==X → 定位操作列
   「打招呼」（容器消歧）→ Win32 点击 → 校验翻转为「继续沟通」→ Escape 关闭并确认。
   已是「继续沟通」→ 报已打过、正常关闭（幂等）。`--dry-run`：只定位不上报点击。
4. 不合格 → `boss_close_detail`（新）：Escape 关闭并确认 canvas 消失（清理用，无业务副作用）。

列表页 `boss_greet` 保留；工具描述改标注「手动/兜底路径，简历筛选主流程请用
boss_open_detail → boss_resume_detail → boss_greet_detail」。

### 客户端改动（clients/boss-resume-assistant）

- 新 Executor `src/main/boss/DetailGreetExecutor.ts`：
  - `locateActionBar(snap)`：收藏/举报/不合适 ≥2 命中 → LCA 容器；缺 → DetailGreetError。
  - `locateGreetButton(snap)`：容器内 打招呼 恰 1 → 点；容器内 继续沟通 → already-greeted；
    容器外命中一律视为列表诱饵。
  - `verifyDetailName(snap, name)`：canvas 顶区精确匹配，fail-loud 防错人。
  - `greet()`：Win32 click → 轮询 snapshot 校验翻转（复用 GreetExecutor 校验思路）→ Escape 关。
- 新 operation ×3（operations/bossOpenDetail.ts / bossGreetDetail.ts / bossCloseDetail.ts）：
  挂 OPERATIONS 表 + CLI 命令 + validate 白名单两表 + MCP toolDefs + manifest（boss_open_detail
  / boss_greet_detail / boss_close_detail）。open/greet 为 write，close 为 readonly 语义
  （无业务副作用）但借真实鼠标，描述里保留「期间勿动鼠标」。
- open_detail 复用：cardName.ts 配对 + 列表滚动找名（GreetExecutor 既有滚动逻辑抽用）。

### 云端改动（src/）

- `local_tools/proxy_tool.py` + `manifest.py` + `catalog.py`：注册 3 个纯代理工具（无计费，
  模式抄 boss_greet / boss_reject_current）。
- `boss_greet` 工具描述加「手动/兜底，筛选主流程用详情页三步」。

## 测试计划

- DetailGreetExecutor 单测：容器消歧（列表诱饵 打招呼 在容器外不误点）、姓名校验失败
  fail-loud、翻转校验、already-greeted 幂等、Escape 关闭确认。
- operation/CLI validate/MCP 一致性测试（新命令两表同步）。
- 真机：`boss_greet_detail --dry-run` 在已打过招呼的候选人上验证定位与 already-greeted
  判定（零副作用）；再由用户确认后真发 1 次。

## 边界与不做

- 不改 boss_greet / GreetExecutor 既有行为（保留兜底路径）。
- 不动 VL 打分与计费（resume_detail 云端后处理不变）。
- 打招呼文案仍用 BOSS 默认问候语（不发自定义消息，那属于聊天链路）。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 探查 | 详情页按钮定位与消歧规则真机实证 | ✅ 完成（2026-09-28） | 两轮探查结论见上；.tmp/ 脚本留档 |
| Phase 1 | 客户端：DetailGreetExecutor + 3 operation + CLI/MCP 注册 | ✅ 完成（2026-09-28） | build:main/typecheck 0 错误；npm test 420 用例全绿（新增 detail-greet-executor 22 例 + boss-detail-greet-ops 10 例 + cli-validate/manifest/mcp-server 同步） |
| Phase 2 | 云端：proxy_tool/manifest/catalog 注册 + boss_greet 描述改标 | ✅ 完成（2026-09-28） | pytest tests/unit/local_tools + 注册/发现相关 155 通过（含新增 TestDetailGreetTools 4 例）；全仓 -k proxy 51 通过 |
| 验证 | 三智能体流程 + typecheck/test + 云端 Python 测试 | ✅ 完成（2026-09-28） | 测试智能体修复 MCP dry_run 键名缺陷（dry-run 静默变真点击）；CR 修复 2 个 P1（付费墙映射防云端自愈重试；前置失败误报 EXECUTION_UNKNOWN + 翻转轮询容错）；主控补齐 SUBAGENT.md 白名单/一键筛选链路改详情页主路径（18→21 工具断言）；最终 typecheck 0 错、客户端 423/423、云端 137 通过 |
| 真机 | --dry-run 验定位 → 真发（含完整筛选闭环） | ✅ 完成（2026-09-28） | 三连全通（open→dry-run 定位 92ms→close）；容器上爬修复后真发 ✅×2：李晓宝（当时误用构造 job_ctx 评 90，远程库真实要求重评实为 50——经验/薪资档位缺失所致，已打招呼无法撤回，教训：写动作前必须用真实职位上下文）；正确口径完整闭环 ✅：设职位筛选（1-3年/本科+大专/10-20K）→ 逐人 open→读→VL 评分（.env 远程库 job_ctx）→ 孙炜 88 分 → greet-detail 真实点击翻转校验通过（3.2s）→ 停止。遗留待修：① open-detail 姓名校验竞态（canvas 先现、头部姓名晚一拍，6 次中 3 次误报 WRONG_PAGE，应轮询重试；VL name_seen 门均确认简历归属无误）② goto URL 直跳留 SPA 半水化态（点击失灵，同 URL 重 Page.navigate 治愈）待加固 |
