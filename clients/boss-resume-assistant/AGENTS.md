# BOSS 招聘操作 CLI — 智能体开发指南

本文件是给编程智能体（ZCode / Codex / Claude Code 等）的上手指南。**进入本项目开发前必读**，能避免重复踩坑。权威设计来源是 `docs/design/recruiting/boss-resume-assistant-native-cdp-design.md`（先读它的 §6/§10/§16/§17/§10.6）。

## 1. 这是什么

`clients/boss-resume-assistant` 是 BOSS 直聘招聘操作的**独立 CLI + 标准 MCP Provider**。attach 用户日常登录的 Chrome（带 `--remote-debugging-port=9222`），用原生 CDP + DOMSnapshot 驱动页面，做筛选/打招呼/接受简历/标记不合适/约面试/**搜索找人发消息**等操作。技术栈：Node 22 + TypeScript + ESM，零第三方运行时依赖（只有 `ws`/`zod`/`@modelcontextprotocol/sdk`）。测试用 `node:test`（零测试框架）。

## 2. 上手铁律（违反必出问题）

1. **只 attach，禁 spawn**：attach 用户自己启动的 debug Chrome，**绝不 spawn/kill Chrome、绝不删 profile**（全新 profile 扫码登录会触发 BOSS 风控封号，§16 决策7）。
2. **原生 CDP，禁 Playwright/Puppeteer/Selenium**：见 `src/main/cdp/methodPolicy.ts` 白名单。**禁 `Runtime.*`/`Debugger.*`/脚本注入**（`Runtime.enable` 是已确认的详情刷新触发条件，§16 决策1-2）。
3. **读页面只用 `DOMSnapshot.captureSnapshot`**：禁 `querySelector`/`evaluate`/`Runtime.evaluate`。定位靠**稳定文本 + strings 表 + 坐标几何**，禁猜 class/selector（BOSS class 是动态混淆 hash）。
4. **点击/输入第一优先 Win32**（2026-08-26 用户定调：防爬是关键）：**所有点击与键盘输入默认走 Win32 真实事件**（`WinMouseClicker.click` / `clickAndType`）；CDP `Input.*` 仅限纯浏览类（点卡片开详情、Escape、滚动——真机实证放行）。**教训（§17 坑22）**：2026-08-24 曾误判「Win32 DPI 换算偏差」把聊天链路点击改 CDP——实为用户移动窗口致输入框不可见，换算无偏差，已全部回退。
5. **fail-loud**：任何歧义（命中 0 个/多个/点击后状态未变）立即抛错请人工查看，**绝不盲点、绝不盲发**；写动作 `UNKNOWN` 不自动重试。
6. **永不关闭用户 Chrome**：`close()` 只断开 CDP 连接。
7. **CLI 参数校验 fail-loud（2026-08-26 事故教训）**：所有子命令未知 flag / 多余位置参数一律 exit 2 拒绝（`src/cli/validate.ts` 的 `validateCommandArgs`，白名单表 `COMMAND_FLAGS`/`COMMAND_POSITIONALS`）；`--help`/`-h` 任意位置显示用法 exit 0；**写动作命令必须显式意图**（greet 需 `--names 甲,乙` 定向或 `--all` 全量，二选一，禁默认全量——曾因 fail-open 解析让 `greet --help` 直接真实执行误打 6+1 人）。**新增命令必须同步两张表 + cli-validate 测试**，漏登记则新命令参数被当未知参数拒绝。

## 3. 坐标体系（用项目工具，禁自己造轮子）

```
屏幕坐标 = accumulateOwnerOffset(documentIndex) + boundsCenter(bounds) − scrollOffset
```
- `bounds` 是**文档绝对坐标、滚动后不变**；iframe 文档滚动只改 `scrollOffsetY`
- 隐藏 iframe（后台标签页）的 owner 无可见 bounds，`accumulateOwnerOffset` 会**抛错**，必须 `try/catch` 跳过
- 同一文案在 `strings` 表可能有**多个下标**（如「不合适」2 个），遍历全部下标，别只 `findIndex` 取第一个
- 区域分界常量：`LIST_MAX_X=850`（左会话列表 / 右聊天面板）、`SIDEBAR_MAX_X=200`（侧栏菜单 / 顶部同文案诱饵）

工具（`src/main/boss/domSnapshot.ts`）：`findNodesByString` / `accumulateOwnerOffset` / `boundsCenter` / `indexedValues`；视口尺寸 `FilterSetter.viewportOf(snap)`。

**坐标可缓存性（判断标准，非铁律）**：动态定位是默认，但并非所有坐标都禁缓存——满足**①固定布局（sticky/fixed，不随列表滚动而动）+ ②窗口几何不变（系统自动操作，无人为缩放/拖动）**的坐标可缓存（典型：推荐牛人页顶部职位框/筛选栏，固定顶部、窗口不变）。引入缓存时**必须加失效保护**：设 TTL，或点击缓存坐标后校验未达预期（下拉未展开/落点错位）则丢弃缓存、回退动态识别。随滚动/列表渲染变化的坐标（牛人卡片等）不可缓存。详见设计文档 §10.7。

## 4. 分层架构（新增能力照此分层，业务只实现一次）

| 层 | 职责 | 参照 |
|---|---|---|
| **Executor** `src/main/boss/` | 纯逻辑，依赖注入 `Deps`（snapshot/click/clickAndType/pressEscape/signal），可单测 | `GreetExecutor.ts`、`ChatRejectExecutor.ts`、`ResumeConsentExecutor.ts`、`ChatSearchExecutor.ts`、`ChatSendExecutor.ts` |
| **operation** `src/main/operations/` | `createXxxOperation` → `runBossOperation(kind,ctx,sessionFactory,validate,body)`，统一 `OperationResult`/`Effect` | `bossGreet.ts`、`bossSendTo.ts`；会话原语 `bossContext.ts`（`BossSession`/`ensureChatPage`） |
| **注册** `operations/index.ts` | `OPERATIONS` 表（CLI 与 MCP 共用，业务只实现一次） | — |
| **CLI** `src/cli/` | `cli/index.ts` switch 分发 → `cli/commands/*.ts` 薄 renderer | `commands/greet.ts` |
| **MCP tool** `src/mcp/toolDefs.ts` | tool 定义（与 operation 同名） | — |

## 5. 命令（编译 / 类型检查 / 测试 / 运行）

```bash
cd clients/boss-resume-assistant
npm run build:main      # 编译（tsc -p tsconfig.main.json）→ dist/
npm run typecheck       # 类型检查（0 错误才能提交）
bash scripts/test-bounded.sh   # 测试：逐文件 90s 超时、8 并行，默认排除挂死文件（可加文件名子串过滤）
node dist/src/cli/index.js <command> [--cdp-port 9222]   # 跑 CLI
```

**测试警告（多次踩坑，2026-11-10 沉淀，必读）：**

- **禁裸跑 `npm test` / `node scripts/run-tests.mjs` 全量收集**：`mcp-conformance.test.js` / `mcp-server.test.js` 会派生 MCP 子进程且子进程不退出，一次 `node --test` 收集全部测试文件时**整个进程挂死、零输出**，开发会话曾因此整段阻塞 30 分钟以上。用 `scripts/test-bounded.sh`（逐文件限时并行，默认排除挂死文件），或定向 `node --test dist/tests/<文件>`。
- **长命令输出不要接 `| tail` 等缓冲管道**：tail 把全部输出缓冲到命令结束，挂死时日志恒空、无从判断。长命令后台跑、直写日志文件，按固定间隔看日志增量；**日志 2-3 分钟零增长视为挂起**，去查进程树而不是继续等。
- **基线预存失败清单（master 上同样失败，勿计入新改动，改动前先看这里）**：
  - `manifest` 3 用例：期望 24 个 MCP 工具 vs toolDefs 实际 25（`boss_recovery_probe` 引入后未同步期望）
  - `dom-snapshot-owner-scroll` 2 用例
  - `readonly-recovery-continuation`：dist/ 孤儿陈旧产物（源码已删），build 后仍在
  - `page-click-experiment`：偶发 flake（`Date.now()-mtime` 时钟粒度），隔离重跑即过
  - 新改动的验收口径：**除这份清单外全绿**；出现清单外失败先查与本改动的关系
真机前提：用户先关掉所有 Chrome，再用 `chrome.exe --remote-debugging-port=9222` 启动日常 Chrome，登录 BOSS 打开目标页面。

## 6. 真机调试 / 写 probe（最容易犯错的地方）

**写探查脚本时，复用 `dist/` 里编译好的 `CdpGateway` + `domSnapshot` 工具，禁自己造坐标轮子、禁 Playwright。** 临时脚本放仓库根 `.tmp/`（已 gitignore）。

```js
import { pathToFileURL } from 'node:url'
const base = 'C:/repos/aid-work-agent/clients/boss-resume-assistant/dist/src/main'
const { CdpGateway } = await import(pathToFileURL(`${base}/cdp/CdpGateway.js`).href)
const ds = await import(pathToFileURL(`${base}/boss/domSnapshot.js`).href)
const { viewportOf } = await import(pathToFileURL(`${base}/boss/FilterSetter.js`).href)
const gw = new CdpGateway(); await gw.connect('http://127.0.0.1:9222')
// attach 找 zhipin.com 页 → gw.captureDomSnapshot() → 用 ds.findNodesByString 等解析
```

**已知坑（真机实证，别再踩）：**
- **会话列表可滚动**：用 `CHAT_LIST_SCROLL_POINT={x:550,y:900}`（真机校准，见 `bossAcceptResume.ts`）+ CDP `mouseWheel`；判到底**必须用 `ResumeConsentExecutor.listSignatureOf`**（cx<850 全文本 y 签名，接受简历场景真机验证有效），**别用局部人名序列判到底**（混入导航/推荐卡片标签会误判"到底"提前退出）。已知姓名精确找人用 `ChatSearchExecutor` 搜索更直接，与滚动互补——不是"滚动失效才改搜索"。
- **CSS 背景图标抓不到**：如搜索图标，DOMSnapshot 无节点无文本 → 让用户把鼠标移上去，用 Win32 `GetCursorPos` 读屏幕坐标反算 page 坐标校准。
- **虚拟列表/特殊渲染文本抓不到**：少数元素（如 §10.5 InterviewDemo 的约面试日期下拉）确实视口内 0 命中 → 靠相邻可见文本**锚点 + 固定偏移**定位。但**先排除"延时不够"**：搜索结果/职位下拉等异步渲染控件，等够延时（见上条）就有 bounds，不是真盲区——曾误判搜索结果卡片人名"抓不到"，实为 sleep 太早。
- **沟通页内嵌推荐 iframe**（§17 坑17）：DOM 里有推荐牛人全量节点（「打招呼」「筛选」都在），按文本存在性判页面会误判 → **用 URL 校验**（推荐=`/web/chat/recommend`，沟通=`/web/chat/index`）。
- **内容区定位（2026-10-31修正）**：普通输入不再读取/等待 `Chrome_RenderWidgetHostHWND`；共享native包用UIA唯一顶层Document获取内容区物理矩形，见设计§10.11。辅助窗口隐藏不等于页面不可交互。
- **PowerShell .ps1 必须 UTF-8 BOM**（中文标题/参数）；中文参数勿经 bash 内联传递。
- **WinMouseClicker 借真实光标**约 1 秒，调用前必须提示用户手离鼠标。
- **展开类控件（下拉/面板）点击后必须等渲染完再 snapshot**：下拉/面板异步渲染，项的 layout bounds 需时间出现。snapshot 太早 → 项无 bounds → **误判为"DOMSnapshot 盲区"**（职位下拉曾因此误判，实为等不够）。不同控件等待不同：筛选面板 `ensurePanelOpen` sleep 1200ms、职位下拉需 2500ms——**等不够就再多等/重 snapshot，别急着判盲区**。
- **解析列表项要遍历 `nodes.nodeValue`（所有节点）**，不只 `layout.nodeIndex`（仅有 bounds 的节点）：异步/未渲染的列表项文本节点有 nodeValue 但暂时无 bounds，只遍历 layout 会漏。典型：职位下拉项文本在 `nodes.nodeValue`，等渲染后才有 bounds。

## 7. 新增能力的流程

1. **真机实验**：写 probe（`.tmp/`，复用 dist 工具）验证坐标/链路；结论沉淀到设计文档对应 §（如 §10.6）。
2. **dev-workflow 三智能体**（`.agents/skills/dev-workflow`）：开发写代码+自测 → 测试独立回归+启动安全 → CodeReview。主控者最后亲自验证 `typecheck` + `npm test` 全绿。
3. **真机用编译后 CLI 验证**：`node dist/src/cli/index.js <新命令> ...`（写动作先 `--dry-run` 验链路，再真发）。
4. `docs/ideas.md` 登记/更新状态（开发中 🔧 / 完成 ✅）。

## 8. 必读文件（上手先读这几份）

- `docs/design/recruiting/boss-resume-assistant-native-cdp-design.md`：§6 CDP 白名单、§10 页面动作（含 §10.4 reject / §10.5 interview / §10.6 发消息找人）、§16 关键决策、§17 真机踩坑清单
- `src/main/cdp/methodPolicy.ts`：CDP method 白名单 + 硬拒绝列表
- `src/main/boss/GreetExecutor.ts` + `ChatRejectExecutor.ts`：Executor 范式（定位/坐标/双通道/fail-loud）
- `src/main/operations/bossContext.ts`：`BossSession` 原语 / `runBossOperation` 骨架 / `ensureChatPage`
- 一个完整 operation 样例：`operations/bossGreet.ts` + `tests/greet-executor.test.ts`

## 9. 部署

Runtime 导入的 `.aidplugin.zip` 与独立 npm `.tgz` 的命令、用途和验证都见[统一打包手册](../../docs/ops/runtime-boss-release-guide.md)。

## 10. 核心机制速查：CDP 定位 → 坐标换算 → Win32 点击 → 输入模拟（完整链路）

本节把四大核心机制串成一条链。**开发任何新动作前照此复用既有抽象，禁止自造轮子、禁止新写 ps1/坐标换算代码**：点击只传 page 坐标 + viewport（`WinMouseClicker.click`），输入只传文字（`typeChar` 逐字）。

### 10.1 CDP 获取元素坐标（只读，DOMSnapshot）

- 入口 `CdpGateway.captureDomSnapshot()` → `{strings, documents[]}`。每个 document 含：`nodes.nodeValue`（文本节点 → strings 下标）、`layout.nodeIndex/bounds`（bounds=[x,y,w,h]，**device px、文档绝对坐标、滚动后不变**）、`scrollOffsetX/Y`（文档滚动偏移）。
- 定位范式：稳定文本 → `strings` **全部**下标（同文案多下标，禁 `findIndex` 只取第一个）→ `findNodesByString(document, stringIndex)` 拿 bounds → `boundsCenter` 取中心。工具全在 `src/main/boss/domSnapshot.ts`。
- 视口尺寸 = `viewportOf(snap)`（`FilterSetter.ts`）：doc0 的 `layout.bounds[0]` 宽高，device px，与整页截图 PNG 同口径（§10.8：绝不做 DPI 换算）。
- 解析列表项要遍历 `nodes.nodeValue` 全部节点而非只遍历 `layout.nodeIndex`（异步渲染的文本节点先有 nodeValue 后有 bounds，只遍历 layout 会漏）。

### 10.2 坐标换算：page 坐标（device px）→ 屏幕坐标

```
page 坐标（Executor 计算）  = accumulateOwnerOffset(docIdx) + boundsCenter(bounds) − scrollOffset
屏幕坐标（native 输入内）   = UIA顶层Document物理矩形 Left/Top + page × (内容区物理宽高 ÷ 传入视口宽高)
```

- **DPI/缩放**：page与传入viewport保持同口径；UIA内容矩形已是物理屏幕坐标，按宽高比例映射，不额外乘/除固定DPI。每次input新观察，输入前复核CDP视口和原生内容/主窗几何，变化则重新观察，不能把旧点拉伸到新布局。
- **scrollOffset 坑（§17 坑16）**：iframe 文档内滚动后 bounds 不变、`scrollOffsetY` 变——不减滚动偏移，滚动后新露出的元素会被永远当成视口外（表现为"一直滚但一个都不点"）。
- **隐藏 iframe 坑**：后台标签页/隐藏 iframe 的 owner 无可见 bounds，`accumulateOwnerOffset` 抛错，必须 try/catch 跳过该 document。

### 10.3 Win32 真实鼠标点击（复用 `WinMouseClicker`，只传坐标 + viewport）

业务层唯一入口 `WinMouseClicker.click(point, viewport)`（`BossSession.click` 同款）——**调用方只传 page 坐标和视口尺寸**，其余全部内置（`@aid/win32-native-input` koffi FFI；2026-10-21 起 PowerShell 输入回退脚本已删，仅 native 单通道）：

1. 按原调试端口唯一定位Chrome进程/主窗，激活原CDP target；非绑定调用保留标题找窗。
2. 最小化先 `SW_RESTORE`；`AttachThreadInput` + `SetForegroundWindow` 处理前台锁，不等待legacy widget重建。
3. UIA读取唯一可见顶层Document内容矩形（不深入iframe），按传入page/viewport比例映射屏幕位置；不得猜工具栏高度。
4. `WindowFromPoint` 落点必须在内容矩形内且根窗为原主窗，拒绝其他窗口/同浏览器owned popup；前台、几何及光标回读保持。原有遮挡抬窗一次保留，不新增恢复层，也不要求命中某个指定辅助HWND。
5. 拟人动作：`SetCursorPos` 分 10 步移动（每步 25ms）→ 悬停 500ms → `mouse_event` LEFTDOWN/70ms/LEFTUP。

约束：统一 `SetCursorPos + mouse_event`，**禁 SendInput 绝对坐标**（多显示器需 VIRTUALDESK 标志有坑）；借真实光标约 1s，调用前必须提示用户手离鼠标；读取链路/实验残余 ps1 文件必须 UTF-8 BOM。

**通道选择（2026-08-26 用户定调：点击/输入第一优先 Win32，防爬是关键；§16 决策 8）**：

| 动作类型 | 通道 | 入口 |
|---|---|---|
| 写动作按钮（打招呼/不合适/发送/确定/接受简历/结果卡片） | Win32 真实鼠标 | `deps.click(point, viewport)` |
| 筛选类控件（筛选按钮/tab/城市职位下拉/面板选项） | Win32 真实鼠标 | `deps.click(point, viewport)` |
| 键盘输入（聚焦+逐字，含输入框聚焦点击） | Win32 原子 click+type | `deps.clickAndType(point, viewport, text)` |
| 纯浏览类（点卡片开详情、Escape、滚动） | CDP `Input.*`（真机实证放行） | `deps.clickBrowse(point)` / `pressEscape()` / `mouseWheel()` |

2026-08-24 曾把输入框聚焦/聊天链路点击改成 CDP 优先（当时以为 Win32 有 DPI 换算偏差）——实为**用户移动窗口致输入框不可见**，换算无偏差（§17 坑22），2026-08-26 已全部回退 Win32 并真机复验通过。

### 10.4 输入模拟（Win32 真实键盘，第一优先）

标准流程（参照 `ChatSendExecutor.sendMessage` / `ChatSearchExecutor.openContact`，2026-08-26 真机复验通过）：

1. 定位输入框（文本节点直接定位，或相邻锚点 + 固定偏移——input/textarea 的 value/placeholder 无 bounds 抓不到坐标）。
2. **`clickAndType(point, viewport, text)` 一次调用原子完成**：native 层内先拟人点击聚焦（500ms），再 SendInput `KEYEVENTF_UNICODE` 逐字真实键入（200ms/字，中文走 wScan 全 16 位）。不拆成「click + 单独输入」两次调用——间隙会被抢焦点。
3. 等 ~600ms 落地后校验：普通输入框 value 会进 strings；contenteditable（反爬碎片节点）用「输入前后 strings **差集**的字符覆盖率 ≥80%」判落地（防页面原有常见字假放行）。
4. 发送/提交按钮属写动作，重新 fresh snapshot 定位后走 Win32 `click`，发送后校验状态变化，UNKNOWN 不重试。
5. CDP `dispatchKey(type='char')` 通道已于 2026-08-26 移除（历史实证风控不拦键盘，但用户原则：真实事件优先）。

native 键盘实现注意（`@aid/win32-native-input`）：SendInput 的 INPUT 结构是联合体（cbSize 必须 40，x64），keybd_event 的 bScan 单字节装不下中文；§17 坑10 的「禁 SendInput」仅指鼠标绝对坐标。
