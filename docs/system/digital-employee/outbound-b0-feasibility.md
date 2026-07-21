# B0 巡检可行性验证报告

> 日期：2026-07-21
> 阶段：巡检商机 B0（M-巡检1 门槛任务）
> 上级设计：[social-media-marketing-agent-design.md](social-media-marketing-agent-design.md) §7 / §14
> 开发计划：[social-media-marketing-agent-dev-plan.md](social-media-marketing-agent-dev-plan.md) §5 B0

## 0. 结论速览

| 子项 | 状态 | 结论 |
|------|------|------|
| browser 工具登录态持久化能力（代码核查） | ✅ 已核查 | **当前不持久化**——临时上下文，每次运行全新未登录。需扩展才能支撑巡检 |
| 知乎/小红书真实账号可用性 | ⏳ 待人工确认 | 需公司提供非新号 |
| 知乎/小红书 web 操作可用性 + 反风控 | ⏳ 待人工实测 | 见 §4 清单 |
| 小红书 web 端发布/私信是否仅 APP | ⏳ 待人工实测 | 见 §4 清单 |

**关键结论**：巡检 web 连接器（B2/B3/B6）**在 browser 工具未扩展登录态持久化前不可行**——每次运行重新登录既不可持续又会触发风控。**B0 衍生出一个前置开发任务：browser 登录态持久化扩展**（见 §3），它是 B2/B3 的硬依赖。

## 1. 代码核查：browser 工具登录态持久化

### 1.1 现状（两条执行路径都一样）

browser 工具（#20）有两种执行路径，**都使用临时 Playwright 上下文**：

**A. 本地内联执行**（`src/tools/browser/session.py`）：
```python
# session.py:51-65
self.browser = await self.playwright.chromium.launch(headless=..., args=[...])   # 全新浏览器
self.context = await self.browser.new_context(viewport=..., user_agent=...)      # 临时上下文，无 storage_state / user_data_dir
self.page = await self.context.new_page()
```

**B. fenced 子进程执行**（`src/tools/browser/worker_main.py`）：
```python
# worker_main.py:181-188
self.browser = await self.playwright.chromium.launch(headless=..., args=[...])
self.context = await self.browser.new_context(viewport=...)                       # 同样临时
self.page = await self.context.new_page()
```

### 1.2 证据

- 全 `src/tools/browser/` grep `storage_state|cookies|user_data_dir|launch_persistent_context` → **零命中**（仅 README 有示例，无实现）。
- `session.py:141-142` 注释明说：「当前 worker 所拥有的会话；**不用于跨请求恢复**」。
- `_browser_sessions` 是 worker 进程内 dict，worker 重启即丢；run 之间不共享上下文。

### 1.3 含义

- 单个 run 内：登录态在该 run 的 context 生命周期内保持（跨页面导航 OK）。
- **跨 run / 跨 worker 重启：登录态全部丢失**，每次都从未登录开始。
- browser 工具有人工接管机制（`human_control.py` / `human_completion_monitor.py` / `agent_resume_coordinator.py`），登录可经人工接管完成，但**接管产生的登录态同样不被捕获持久化**。

## 2. 对巡检的影响

巡检的 web 连接器（B2 协议、B3 知乎、B6 小红书）核心动作都依赖「以已登录真实账号操作」。需求雷达（B4）是周期性巡视，需跨多次 run 维持登录。当前 browser 工具：

- 每次雷达 run 都要重新登录 → 运营不可持续；
- 高频登录 → 极易触发平台风控（被判定自动化），与设计 §3「真人节奏、异常即停」冲突；
- 设计 §7.3「登录态托管：Cookie/Session 加密存储（`bs_outbound_account_sessions`）」**无 browser 工具侧的支持**，表建了也注入不回去。

**结论：browser 工具必须先扩展登录态持久化，巡检才可行。**

## 3. 衍生前置任务：browser 登录态持久化扩展（B0 → 新任务）

> 建议在开发计划里新增此项（标为 B2/B3 硬依赖），暂称 **B0.5**。

### 3.1 方案选型

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A. storage_state 注入**（推荐） | 登录后 `context.storage_state()` 导出 cookies+localStorage → 加密存 `bs_outbound_account_sessions`；下次 run `new_context(storage_state=...)` 注入 | 标准 Playwright 做法、轻量、按账号隔离天然适配多租户 | 仅覆盖 cookies+localStorage，少数用 IndexedDB/更复杂会话机制的平台可能不够 |
| B. persistent context | `launch_persistent_context(user_data_dir=按账号目录)` | 完整浏览器 profile，最稳 | 重：每账号一个 profile 目录、并发/清理复杂、多租户隔离麻烦 |

**推荐 A**，覆盖知乎/小红书这类 cookie 会话足够；若实测某平台 storage_state 持不住，再针对性升级 B。

### 3.2 改动范围

- `session.py` + `worker_main.py`：`start()` 接受 `storage_state`（从加密存储读出）注入 `new_context`；新增「登录完成」回调触发 `storage_state()` 导出 → 加密回写。
- 持久化层：`bs_outbound_account_sessions`（B1 表，Cookie 加密）的读写 helper。
- 失效检测：注入后若仍判未登录 → 标记失效、通知人工重新登录（不破解、不绕过，符合 §3 红线）。
- 安全：storage_state 含敏感会话，必须 `SecretCrypto` 加密 + 密钥版本，不出现在日志/审计/Agent 上下文（对齐 §10）。

### 3.3 与 B1/B2 的关系

- B1 建 `bs_outbound_account_sessions` 表（存加密 Cookie）——B0.5 的存储后端。
- B0.5 给 browser 工具加注入/导出能力——B2 的 `ensure_logged_in` 依赖它。
- 顺序：**B1（表）→ B0.5（browser 扩展）→ B2（协议/ensure_logged_in）→ B3（知乎连接器）**。

## 4. 待人工实测清单（需公司真实账号配合）

> 以下需提供**非新号**的知乎、小红书真实账号，由运营/测试人员用 browser 工具（人工接管模式）实际走一遍。结论回写本文档 §1.3 之后与设计 §14。

### 4.1 知乎
- [ ] 登录：人工接管登录后，能否在当前 run 内保持登录态跨页导航。
- [ ] 搜索：关键词搜索结果页可正常加载、可读。
- [ ] 读页：问题页/回答页 DOM 可被 snapshot 工具结构化解析。
- [ ] 发（想法/文章/回答）：web 端是否可用、是否有强风控。
- [ ] 评论：可发评论、可读他人评论。
- [ ] 私信：web 端私信是否可用。
- [ ] 反风控：连续操作多少次/多快开始出现验证码或限流。

### 4.2 小红书
- [ ] 登录：同上。
- [ ] 搜索 / 读页：笔记页 DOM 可结构化。
- [ ] **发布**：web 端发布笔记是否被引导到 APP（关键，决定 B6 发布是否可行）。
- [ ] **私信**：web 端私信是否可用（可能仅 APP）。
- [ ] 评论：可发可读。
- [ ] 反风控：同上。

### 4.3 storage_state 有效性实测（B0.5 实现后补测）
- [ ] 登录后导出 storage_state → 重启 run 注入 → 是否仍处登录态。
- [ ] storage_state 有效时长（cookie 过期节奏）。

## 5. 设计 §14 待确认项回写

§14 第 5 项「browser 工具登录态持久化能力」**部分确认**：

> **已确认（2026-07-21，B0 代码核查）**：browser 工具（#20）当前**不持久化登录态**——`session.py` 与 `worker_main.py` 均用临时 `new_context()`，无 `storage_state`/`user_data_dir`，注释明示「不用于跨请求恢复」。巡检需先做 **B0.5 登录态持久化扩展**（storage_state 加密存 `bs_outbound_account_sessions`，下次注入）才能可行。
>
> 仍待确认：公司知乎/小红书真实账号；小红书 web 发布/私信是否仅 APP（§4 实测）。

## 6. 后续

1. **B0.5** 立项：browser 登录态持久化扩展（B2/B3 硬依赖），方案 A（storage_state）。
2. **B1**：商机数据模型 4 表（含 `bs_outbound_account_sessions`，B0.5 的存储后端）。
3. 人工实测 §4 清单（公司提供真实账号后）。
4. 实测结论回写本报告 + 设计 §14。
