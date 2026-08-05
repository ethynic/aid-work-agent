# BOSS 简历筛选助手原生 CDP 开发计划

> 状态：🔧 Phase 1～9 代码全部完成（CLI 形态）；Phase 10 进行中——12.1/12.2 已完成（CLI `filter` 真机验证通过），剩 12.2 YAML 集成 / 12.3 付费误判修复 / 12.4 写动作验证  
> 日期：2026-07-22（Phase 7/8/9 完成于 2026-08-04，Phase 10 新增于 2026-08-05）  
> ⚠️ **2026-08-05 路线收敛**：Electron GUI（renderer/主进程壳/IPC/preload/打包链路）、spawn 模式 ChromeLauncher、探索期探针脚本、`spikes/boss-native-cdp/` 已全部删除，CLI 为唯一产品形态。本文 Phase 1/8 等章节中 GUI、IPC、Electron 打包相关描述仅作历史记录，不再有效；以设计文档 §16 决策 6/7/8 为准。
> 设计文档：[BOSS 简历筛选助手原生 CDP 设计](../../design/recruiting/boss-resume-assistant-native-cdp-design.md)  
> 工程位置：`clients/boss-resume-assistant/`

## 0. 实时进度（2026-07-22 夜间）

| Phase | 状态 | 说明 |
|---|---|---|
| 1 | 🔧 代码完成 / 打包门禁待 admin | 工程骨架+SQLite(8表)+IPC+安全+Vue 占位页+打包脚本全部完成。**测试/dev 全通**(node 18 + better-sqlite3 prebuilt)。**打包阻塞**：electron 二进制已用 node 24 下载成功，但 better-sqlite3 的 electron-43 ABI 无 prebuilt 需本地编译 VS C++，而 `--quiet` 安装 VC++ workload 需管理员权限(UAC 弹窗)，无人值守时 VS Installer 返回 Exit 87。待用户以 admin 装 VC++ workload 后即可 `npm run package:win:dev`。**加密降级**：明文 SQLite，DPAPI/字段加密移至 Phase 8 |
| 2 | ✅ 代码+单测+真机只读门禁通过 | CdpSocket/CdpGateway/methodPolicy/audit 产品化。真机已用 `http://127.0.0.1:9222` 实测：连接 browser CDP、attach 推荐页、Page.enable、确认 recommend frame、单次截图，全程无 Runtime、无写动作 |
| 3 | ✅ 代码+单测 | ListSnapshotParser(domSnapshot)+domSnapshot 工具：sparse/dense 双序列化兼容、嵌套 iframe owner 偏移累加(不减 scrollOffset)、UNLOCATABLE、视口安全区(顶部150/底部20)、HMAC 指纹 |
| 4 | ✅ 代码+单测 | DetailCapture(滚动分段截图+稳定/到底检测)+LongScreenshotStitcher(重叠搜索+MSE 评分+断层检测+宽度校验)。纯像素算法 6 单测验证拼回连续长图、断层失败、宽度不一致失败 |
| 5 | ✅ 代码+单测+真集成 | **TesseractJsProvider 已集成**(tesseract.js 纯 WASM 本地推理，输出 OcrBlock 带 box/confidence，chi_sim+eng)。OcrProvider 契约+fail-loud+ResumeNormalizer。单测 5 绿 |
| 6 | ✅ 代码+单测+真集成 | **DeepSeekLlmProvider 已集成**(OpenAI 兼容、`deepseek-v4-flash`、JSON mode、temperature 0.1、多 key 轮询、非法结论降级 UNCERTAIN)。ScreeningEngine 两阶段+三态。单测 8 绿 |
| 7 | ✅ 代码+单测+CR通过 / 真机写动作待用户确认 | ActionPlanner（三态→GREET/REJECT/NO_ACTION，6项前置校验）+ActionStore（幂等 unique_key+状态机）+PageActionExecutor（fresh snapshot 重定位、before/after截图、UNKNOWN零重试）+ButtonLocator/DetailCloser+reasonMapping。DB migration v2（actions 补 unique_key/session_id/sent_at/confirmed_at+唯一索引）。CR 修复 P1：mousePressed 送达后 released 失败归 UNKNOWN 防重复点击。真机最小样本步骤见 tests/manual-e2e-phase7.md，需用户在场确认 |
| 8 | ✅ 代码+单测+CR通过 / 真机与打包验收待办 | ChromeLauncher+ScreeningSession(14态状态机编排)+storage 四 store+exporter(CSV防公式注入)+runtime 装配；渲染层五页(LoginGate 交互登录门禁/JobConfig/RunConsole/ReviewQueue/AuditLog)+窄 IPC v2(18 新通道,全过 assertTrustedIpcSender)；migration v3。CR 修 3 个 P1：滚动改 mouseWheel(mouseMoved+delta 不滚动)、Launcher 启动失败杀进程防泄漏、登录等待期 Chrome 退出可复位。真机步骤 tests/manual-e2e-phase8.md；打包仍阻塞于 admin 装 VC++ workload |
| 9 | ✅ 代码+单测+CR通过 / 真机待办 | **CLI 形态**（2026-08-04 产品决策，Electron 壳太重）：`src/cli/` 五子命令 run/review/review override/export/audit；岗位 YAML 配置(examples/example-job.yaml)、回车登录门禁、stdin p/r/s/q 控制、静态 HTML 复核报告（证据着色+长图 file://+全量转义）、数据落 `data/`(gitignore)；migration v4(resume_views.ocr_markdown)。CR 修 2 个 P1：Ctrl+C 无清理(Chrome 孤儿)改为 SIGINT 统一走强制清扫、q 退出与在途 runLoop 竞态改为 quitNow 立即 shutdown+exit。Electron 工程保留不删，CLI 链路零 electron import(依赖闭包 30 文件实测) |
| 10 | 🔧 进行中 / demo 已实证 | **双通道点击固化**（2026-08-05 真机实证，设计文档 §16 决策 8）：BOSS 反作弊 SDK 选择性拦截 CDP 合成点击（筛选类控件），Win32 真实鼠标全通。08-05 demo 已手动跑通全流程：DOMSnapshot 定位「5-10年/本科/硕士/博士/10-20K」→ win-click.ps1 逐个点击 → 确定 → 列表刷新（筛选·5 生效）。待办见 §12 |

**真机环境**：用户 Chrome 已带 `--remote-debugging-port=9222` 启动并登录在 `https://www.zhipin.com/web/chat/recommend`，Phase 2 已实测可连。

**当前测试基线**：`cd clients/boss-resume-assistant && npm test`(node 22)→ **205/205 全绿**，typecheck 与 `npm run build` 通过。

**CLI 用法**（node 22）：`npm run cli -- run --job examples/example-job.yaml`（扫码登录后回车开始，运行中 p/r/s/q）；`npm run cli -- review --open`（HTML 复核报告）；`npm run cli -- review override <编号> <QUALIFIED|REJECTED> --reason "..."`；`export` / `audit`。

**Phase 9 真机记录（2026-08-04）**：首次真机 run 时，CLI spawn 全新临时 profile Chrome 供扫码登录，**BOSS 风控在登录环节封号**（CDP 未建立连接，与自动化操作无关，系全新设备环境+调试端口触发）。已完成 attach 模式改造（ChromeAttacher：用户日常 Chrome 手动加 `--remote-debugging-port=9222` 启动，CLI 只 attach，任何路径不 spawn/kill Chrome、不删 profile；测试+CR 三轮修复 3 个 P1/P2：正常完成进程挂死、重复 connect 泄漏旧 gateway、onDisconnect 身份守卫）。212/212 全绿。**真机重测前置：用户账号申诉解封**。

**Phase 9 真机待办**：重点覆盖 CR 指出的三条退出路径（运行中 q、运行中 Ctrl+C、登录等待中 Ctrl+C）及双终端同时 run（无单实例锁，P2 待加 `data/run.lock`）；attach 模式首跑确认任务正常完成后进程干净退出。

**环境（2026-08-04 更新）**：统一 node 22（better-sqlite3 升 v13 N-API 预编译，node 18 不再使用，electron-43 ABI 免本地编译，**原 VC++ workload 打包阻塞已解除**）；electron 二进制经 npmmirror 镜像下载。

**Phase 7 真机待办**（需用户在场确认，步骤见 `clients/boss-resume-assistant/tests/manual-e2e-phase7.md`）：重点观察 CR 报告的 P2-1（确认启发式整页作用域，详情背后列表的「打招呼」文案可能使确认永远 UNKNOWN）和 P2-2（候选人姓名精确匹配）。

**登录方式决策（2026-08-04 产品确认）**：BOSS 登录需 App 扫码，不自动化；客户端走「用户手动登录 → 点击按钮确认继续」的交互式门禁（Phase 8 LoginGate 已落实，CDP WebSocket 只在确认后建立）。

**Phase 8 真机待办**（步骤见 `clients/boss-resume-assistant/tests/manual-e2e-phase8.md`）：重点观察 CR 报告的 P2-2（详情滚轮锚点在 (0,0)，可能命中左侧导航，必要时改传视口中心）；并确认 P2-4（复核改判 QUALIFIED 后是否需补打招呼，设计未明确，待产品决策）。

**待用户配合的两件事**：① admin 装 VS VC++ workload 后跑 `npm run package:win:dev` 出安装包；② 真机端到端验证（Phase 7 写动作最小样本 + Phase 8 全流程，均需用户在场确认）。

**环境**：node 22 统一（见上）；旧记录：node 18 曾用于 better-sqlite3 v11 prebuilt 测试，已废弃。



## 1. 开发原则

1. Phase 0 真机门禁未通过前，不开始完整客户端开发。
2. 浏览器控制只允许自行实现的原生 CDP WebSocket 客户端。
3. 禁止 Playwright、Puppeteer、Selenium；禁止 `Runtime.*`。
4. 每个 Phase 都必须保存测试结果、实际 CDP method 集合和失败样本。
5. 任何无法确认的页面状态或动作结果都暂停，不自动重试。
6. 完成一个 Phase 后同步更新本文状态和 `docs/ideas.md`。

## 2. 总览

| Phase | 内容 | 预估 | 状态 | 完成门禁 |
|---|---|---:|---|---|
| 0 | 原生 CDP 真机可行性门禁 | 4～6 人日 | ✅ 已通过 | 打开详情、截图、Escape 和方法审计通过；WASM/Runtime 禁止 |
| 1 | 独立桌面工程与本地数据底座 | 3～5 人日 | ⬜ | 应用、IPC、SQLite、加密和打包骨架通过 |
| 2 | Chrome 启动与 Raw CDP Gateway | 4～6 人日 | ⬜ | 手动登录门禁、白名单、断线恢复通过 |
| 3 | 推荐列表 DOMSnapshot 适配 | 5～7 人日 | ⬜ | 多 fixture + 真机只读队列通过 |
| 4 | 查看摘要日志、详情分段截图与长图拼接 | 7～10 人日 | ⬜ | 每次查看有摘要记录，10份简历无断层长图 |
| 5 | OCR、Markdown与字段归一化 | 5～8 人日 | ⬜ | 正文覆盖率和字段冲突测试通过 |
| 6 | 硬规则、LLM筛选与人工复核 | 5～7 人日 | ⬜ | 三态结论、证据和Schema门禁通过 |
| 7 | 打招呼/不合适动作与幂等 | 5～8 人日 | ⬜ | 最小真实动作样本和UNKNOWN不重试通过 |
| 8 | 审计界面、恢复、打包与验收 | 5～7 人日 | ⬜ | Windows安装包和端到端验收通过 |
| 9 | CLI 形态（产品决策 2026-08-04） | 2～3 人日 | ✅ 代码完成 | 见 §0；Electron 壳保留不删 |
| 10 | 双通道点击固化与筛选设置自动化 | 2～3 人日 | 🔧 demo 已实证 | WinMouseClicker 落地 + 筛选全流程真机通过 + 付费误判修复 |

总预估：43～64 人日。Phase 0 失败时停止项目并回到人工点击或纯只读辅助方案，不通过增加 stealth、Runtime 注入或更换自动化框架继续绕行。

## 3. Phase 0：原生 CDP 真机可行性门禁

### 3.1 目标

把当前临时实验脚本收敛成最小、可重复、可审计的只读协议验证工具，确认原生 CDP 能完成“打开详情—截图—Escape 返回列表”的最小链路。真实写动作延后到 Phase 7。

### 3.2 任务

- [x] 建立 `spikes/boss-native-cdp/`，只依赖 Node 标准库和轻量 `ws`。
- [x] 实现 browser WebSocket 请求 ID、sessionId 路由、超时和断开。
- [x] 固化 CDP method 白名单和 `Runtime.*` 硬拒绝。
- [ ] 启动普通可见 Chrome，用户手动登录并关闭弹窗后再连接。
- [ ] 记录连接、Target、Page、Network、DOMSnapshot、Input 的单变量矩阵。
- [ ] 用 DOMSnapshot 布局定位首张候选卡片，使用 Input mousePressed/mouseReleased 打开详情。
- [ ] 连续打开/关闭详情 20 次，记录成功、误刷新、误点和超时。
- [ ] 验证 Input mouseWheel 从详情顶部滚动到底部，不滚动推荐列表。
- [ ] 验证 Page 分段截图和相邻截图重叠连续性。
- [ ] 验证 Input Escape 关闭详情 20 次。
- [ ] 验证分段截图 + 本地 OCR 能生成同结构查看摘要，DOMSnapshot 仅做定位与结构辅助。
- [ ] 在用户确认下做最小打招呼/不合适真实样本，验证结果确认和幂等。
- [x] 工具已用脱敏 JSONL 导出实际发送的 CDP method，并以单测断言 `Runtime.*` 在发送前被拒绝；真实门禁运行的审计仍待采集。
- [x] 修复 Chrome 150 真机 DOMSnapshot 坐标兼容：子文档 `scrollOffsetY=917` 时可见节点 bounds 仍为视口坐标，locator 不再重复扣减 scrollOffset；嵌套 iframe 只累加 owner 视口偏移，并以 dense fixture 锁定。
- [x] 产品现场最终验收：fresh 页面选择当前完整可见卡片，原生 Input 点击卡片左侧正文区域成功打开详情，截图成功，单次完整 Escape 成功返回列表，未触发招聘写动作。产品确认该最小链路即为 Phase 0 通过标准；20 次批量稳定性不再作为 Phase 1 前置阻塞。

### 3.2.1 当前检查点（2026-07-22）

- 已完成：原生 CDP 客户端、协议策略、脚本注入硬拒绝、脱敏审计、用户就绪确认门槛、只读 DOMSnapshot/视口截图基线命令。WASM 摘要探针及其安装/移除权限已按产品决策删除。
- 离线自动化测试：`npm test`，覆盖禁止方法及审计、请求/session/乱序/事件/异常清理、审计脱敏，以及 Chrome 150 dense array 与标准 sparse `{index,value}` DOMSnapshot、嵌套 iframe 与滚动偏移、`nodeValue`/`contentDocumentIndex` 未知形态 fail-loud、重复文本、无安全点击区和招聘写动作文案拒绝。
- 真机部分通过：打开详情 2/2；滚动 3 次且逐次截图成功；完整 Escape `rawKeyDown`/`keyUp` 2/2；两份详情 DOMSnapshot 正文可读。摘要来源已确定为分段截图 + 本地 OCR，不再验证 WASM 探针。
- 后续阶段：批量稳定性、截图重叠连续性、本地 OCR 摘要完整性和最小写动作分别在 Phase 3～7 验收，不再阻塞 Phase 1。Phase 0 未执行任何真实写动作。

### 3.2.2 Phase 0 交接总结

**结论**：Phase 0 已于 2026-07-22 经产品现场验证通过，后续智能体可以直接从 Phase 1 开始，不需要重新实现或重复验收 Phase 0。

**已交付代码**：

- `spikes/boss-native-cdp/src/client.mjs`：原生 CDP WebSocket 客户端，处理请求关联、session 路由、超时、事件和断线清理。
- `spikes/boss-native-cdp/src/policy.mjs`：协议白名单与硬拒绝策略；禁止 `Runtime.*`、脚本注入及招聘写动作。
- `spikes/boss-native-cdp/src/audit.mjs`：CDP 调用的脱敏 JSONL 审计。
- `spikes/boss-native-cdp/src/snapshot-locator.mjs`：兼容 sparse/dense DOMSnapshot 的只读定位基线，并对歧义、危险文案和未知结构 fail-loud。
- `spikes/boss-native-cdp/src/cli.mjs`、`live-readonly-gate.mjs`、`live-detail-scroll.mjs`、`live-escape.mjs`：连接、只读门禁、滚动截图和 Escape 验证命令。

**验证基线**：

- 在 `spikes/boss-native-cdp/` 执行 `npm test`，当前基线为 20/20 通过。
- 真机已验证：当前页面中选择一张完整可见卡片，点击卡片左侧正文区域可打开详情；`Page.captureScreenshot` 可截图；发送一次完整 Escape 可返回列表。
- 姓名文字子节点不一定触发打开，不能把“点击姓名”写成固定策略；后续应识别卡片正文安全区域。
- BOSS 页面及候选人列表会动态变化。每次操作前必须获取 fresh snapshot 并重新确认目标，禁止复用上一次的姓名、节点或坐标。
- 真机截图仅保存在本机临时目录，可能包含候选人信息，不进入仓库、不作为测试 fixture。

**已明确删除或停止的方案**：

- WASM/`abstractData` 摘要探针及其安装、移除权限已删除；摘要统一走“截图 + 本地 OCR”，在 Phase 4～5 实现。
- 不引入 Playwright、Puppeteer、Selenium，不开放 `Runtime.*`、isolated world 或任何页面脚本注入能力。
- Phase 0 中尝试过的复杂动态自动选卡、20 次批量门禁及额外报告层不作为交付基线；不要在 Phase 1 恢复这些代码。

**后续阶段边界**：

- Phase 1 只建设 Electron/Vue/TypeScript 桌面工程、本地数据库、IPC、安全存储和开发安装包，不重做页面适配。
- Phase 2 将本次 spike 中已验证的 CDP 客户端和协议策略产品化。
- Phase 3 负责动态卡片结构识别、fresh snapshot、正文安全点击点及批量只读稳定性。
- Phase 4～5 负责详情分段截图、长图拼接和本地 OCR；Phase 7 才允许在用户确认下验证最小真实写动作。

### 3.3 Phase 0 最终通过标准（产品确认版）

- [x] 用户在普通可见 Chrome 中手动登录并确认页面已准备好。
- [x] 原生 CDP 在 fresh 页面上可点击当前完整可见卡片的正文安全区域并打开详情。
- [x] `Page.captureScreenshot` 可保存详情截图。
- [x] 单次完整 Escape 可关闭详情并返回推荐列表。
- [x] 全程未执行招聘写动作，协议策略拒绝 `Runtime.*`、WASM 探针和脚本注入。

原计划中的 20 次批量稳定性、长简历完整截图、本地 OCR 摘要和真实写动作验证已经分别移至 Phase 3～7，不再是进入 Phase 1 的前置条件。

### 3.4 失败处理

Phase 0 已按产品确认的最小只读标准通过。后续阶段若批量定位、截图/OCR 或写动作连续三轮仍不稳定，应阻塞对应阶段并降级为人工确认，不回退或篡改 Phase 0 的验收结论。

## 4. Phase 1：独立桌面工程与本地数据底座

### 4.1 工程

- [ ] 创建 `clients/boss-resume-assistant/` Electron + Vue + TypeScript 工程。
- [ ] 配置 Vite、ESLint、Vitest 和 Windows打包。
- [ ] Renderer 禁用 Node integration，开启 context isolation。
- [ ] 建立窄 IPC：登录确认、任务控制、复核、导出和设置。
- [ ] 不复用现有 Agent Desktop 的认证、自动更新或服务端 API。

### 4.2 数据

- [ ] 建立 jobs、sessions、candidates、resume_views、captures、evaluations、actions、cdp_audit 表。
- [ ] `resume_views` 以岗位、候选人、查看时间为核心，保存摘要来源、完整度和加密 ViewedResumeSummary JSON。
- [ ] 实现迁移、事务、唯一约束和崩溃恢复检查点。
- [ ] 使用 Windows DPAPI 保护主密钥，敏感字段使用认证加密。
- [ ] 建立加密附件目录和30天默认清理策略。
- [ ] 测试数据库损坏、密钥不可用和附件缺失时 fail-loud。

### 4.3 门禁

- [ ] 单元测试通过。
- [ ] Renderer 无任意文件/CDP访问能力。
- [ ] 打出可安装的 development-unsigned Windows包。

## 5. Phase 2：Chrome 启动与 Raw CDP Gateway

### 5.1 ChromeLauncher

- [ ] 发现系统 Chrome 可执行文件并展示失败诊断。
- [ ] 创建本次会话独立 profile 和随机调试端口。
- [ ] 读取 `DevToolsActivePort`，不扫描固定端口。
- [ ] 启动登录 URL，保持可见窗口。
- [ ] 用户点击“登录和页面准备完成”前不建立 CDP WebSocket。
- [ ] Chrome退出后再清理临时 profile；清理失败保留路径供人工处理。

### 5.2 CdpGateway

- [ ] 实现请求关联、事件订阅、session路由、超时、取消和断线。
- [ ] 所有业务调用封装为类型化方法，不暴露通用 `send(method)` 给业务层。
- [ ] 实现 allowlist/denylist；未知 method 默认拒绝。
- [ ] 禁止 `Runtime.*`、Debugger、isolated world、RemoteObject相关方法。
- [x] 禁止 `Page.addScriptToEvaluateOnNewDocument`、`Page.removeScriptToEvaluateOnNewDocument` 和任意脚本注入。
- [ ] 记录脱敏协议审计。
- [ ] 增加依赖树测试，禁止 Playwright/Puppeteer/Selenium。

### 5.3 门禁

- [ ] mock CDP server 覆盖乱序响应、事件风暴、断线和超时。
- [ ] 真实登录页面连接保持10分钟无刷新。
- [ ] 审计导出只包含允许方法。

## 6. Phase 3：推荐列表 DOMSnapshot 适配

### 6.1 解析

- [ ] 识别顶层 recommend 页面和推荐 frame。
- [ ] 从 DOMSnapshot 字符串、节点关系和 layout bounds 构造候选卡片。
- [ ] 提取姓名、年龄、学历、年限、状态、期望、经历摘要和标签。
- [ ] 处理同名候选人、字段缺失、广告插入、卡片懒加载和顺序重排。
- [ ] 生成岗位内候选人 HMAC 指纹。

### 6.2 坐标

- [ ] 为每张卡片计算正文安全点击点，避开“打招呼”等按钮。
- [ ] 卡片不能唯一定位时返回 UNLOCATABLE。
- [ ] 点击前重新快照，确认指纹和坐标仍匹配。

### 6.3 测试

- [ ] 保存脱敏 DOMSnapshot fixtures。
- [ ] 编写契约测试，字段变化必须显式失败。
- [ ] 真机只读运行30分钟，不执行写动作。

## 7. Phase 4：查看摘要日志、详情分段截图与长图拼接

Phase 4 先交付截图、拼接和查看日志持久化骨架；`ViewedResumeSummary` 的 OCR 生成与完整性验收依赖 Phase 5 OCR Adapter，两阶段都通过前不允许候选人进入自动筛选或写动作。

### 7.1 ViewedResumeSummary

- [ ] 定义基础信息、工作经历摘要、项目经历摘要和教育摘要结构。
- [ ] 删除 userId、头像URL、securityId、encryptGeekId、encryptJobId、lid 和加密正文。
- [ ] 详情确认打开后立即创建 `resume_views` 待完成记录。
- [ ] 本地 OCR 成功时保存 `source=OCR_NORMALIZED` 和 `COMPLETE_SUMMARY`。
- [ ] OCR 失败时保存列表摘要并暂停人工复核，不重载详情、不重复点击。
- [ ] 只有 `resume_views` 摘要提交成功，候选人才允许进入筛选和动作阶段。

### 7.2 DetailCapture

- [ ] 通过 Input 点击打开详情并多信号确认。
- [ ] 使用 Page.captureScreenshot 获取基线图。
- [ ] 自动识别详情正文裁剪区域，排除固定导航和操作栏。
- [ ] 先向上滚动到顶，再按65%～75%视口高度向下滚动。
- [ ] 每次滚动等待 Canvas 稳定，保存原始分片和元数据。
- [ ] 连续截图相同或达到20片时停止。
- [ ] 使用 Input Escape 关闭详情并确认回到列表。

### 7.3 LongScreenshotStitcher

- [ ] 实现相邻图像重叠搜索和位移评分。
- [ ] 裁掉重复区域并纵向合成长图。
- [ ] 检测断层、错位、固定元素残留和异常空白。
- [ ] 拼接失败保留分片并进入人工复核。
- [ ] 输出 PNG、尺寸、分片数和完整性报告。

### 7.4 门禁

- [ ] 每次成功打开详情都生成 `resume_views` 记录，不允许无摘要的“已查看”。
- [ ] OCR 失败样本明确进入列表摘要回退流程，不丢失查看记录。
- [ ] 10份不同长度简历全部生成无断层长图。
- [ ] 不同Windows缩放和Chrome缩放组合至少覆盖100%、125%。
- [ ] 全过程协议审计无 Runtime。

## 8. Phase 5：OCR、Markdown与字段归一化

### 8.1 OCR Adapter

- [ ] 定义本地 OCR Provider 接口，Provider选择通过中文样本基准测试决定。
- [ ] 支持长图和分片回退两种输入。
- [ ] 保存文本块、坐标、置信度和来源分片。
- [ ] 低置信度内容显式标记，不自动修正事实。

### 8.2 Resume Normalizer

- [ ] 将列表摘要和OCR内容归一为统一 ResumeDocument。
- [ ] 将OCR恢复的基础/工作/项目/教育摘要回填为 `source=OCR_NORMALIZED` 的 ViewedResumeSummary。
- [ ] OCR失败时保存 `source=LIST_DOM_FALLBACK`、`PARTIAL_SUMMARY` 并暂停等待人工复核。
- [ ] 恢复个人优势、期望职位、工作、项目、教育和技能章节。
- [ ] 公司、职位、日期等字段优先使用 DOMSnapshot。
- [ ] 来源冲突进入 reviewIssues，不静默覆盖。
- [ ] 生成供筛选使用的 Markdown 和可审计 JSON。

### 8.3 门禁

- [ ] 建立至少20份脱敏中文简历图像样本。
- [ ] 关键字段准确率和正文覆盖率达到项目约定阈值。
- [ ] Markdown章节顺序与截图一致。

## 9. Phase 6：硬规则、LLM筛选与人工复核

### 9.1 岗位知识

- [ ] 定义岗位、硬条件、偏好、排除项和问候语模板。
- [ ] 每次运行锁定知识版本和规则版本。
- [ ] 提供规则预览和样例候选人试算。

### 9.2 Screening Engine

- [ ] 硬规则先执行并输出逐条证据。
- [ ] LLM只处理未决项，使用严格 JSON Schema。
- [ ] 输出 QUALIFIED、REJECTED、UNCERTAIN。
- [ ] 无证据、字段冲突或响应失败一律 UNCERTAIN。
- [ ] 记录模型、Prompt版本、输入哈希、输出和耗时。

### 9.3 人工复核

- [ ] 展示查看时间、ViewedResumeSummary来源、结构化摘要、长图、Markdown、结论和证据。
- [ ] 支持用户改判并记录原结论和改判理由。
- [ ] UNCERTAIN 默认进入复核队列，不执行页面动作。

## 10. Phase 7：打招呼/不合适动作与幂等

### 10.1 动作规划

- [ ] 将三态结论映射为 GREET、REJECT、NO_ACTION。
- [ ] 配置每会话和每日动作上限。
- [ ] 按本地候选人指纹和动作唯一键防重复。
- [ ] 写操作前再次确认当前详情和候选人一致。

### 10.2 原生 Input

- [ ] 用截图/DOMSnapshot定位唯一按钮区域。
- [ ] mousePressed/mouseReleased 后等待可确认结果。
- [ ] 不合适原因必须与评估理由显式映射。
- [ ] 动作状态实现 PLANNED、SENT、CONFIRMED、UNKNOWN、FAILED。
- [ ] UNKNOWN 永不自动重试。

### 10.3 门禁

- [ ] mock和视觉fixture覆盖按钮移位、弹窗、重复点击和网络超时。
- [ ] 用户确认的最小真实样本通过。
- [ ] 重启恢复不重复执行已SENT动作。

## 11. Phase 8：审计界面、恢复、打包与验收

### 11.1 界面

- [ ] 登录引导和页面准备确认。
- [ ] 岗位配置、任务进度、暂停/恢复/停止。
- [ ] 人工复核队列。
- [ ] 候选人详情、筛选原因、证据和动作结果。
- [ ] 按岗位、查看日期、候选人、摘要来源、结论和动作筛选日志。
- [ ] 导出 CSV/JSON/Markdown；截图单独授权导出。

### 11.2 稳定性

- [ ] Chrome断开、应用崩溃和Windows重启恢复。
- [ ] URL异常、页面改版、截图拼接失败和OCR失败暂停。
- [ ] profile和临时附件清理。
- [ ] 24小时本地稳定性测试。

### 11.3 最终验收

- [ ] 10份简历连续端到端处理，无 Runtime刷新。
- [ ] 每次详情打开都有候选人指纹、岗位、查看时间和 ViewedResumeSummary。
- [ ] 每份进入自动筛选的简历都有长图、Markdown、结论、证据和动作记录。
- [ ] 审计方法集合无禁止项。
- [ ] Windows安装、升级和卸载验证通过。
- [ ] 更新本文完成状态并将 `docs/ideas.md` 条目移动到 `docs/ideas_finished.md`。

## 12. Phase 10：双通道点击固化与筛选设置自动化

> 背景：2026-08-05 真机实证 CDP 合成点击被 BOSS 反作弊 SDK 选择性拦截（筛选类控件），Win32 真实鼠标全通。设计决策见设计文档 §16 决策 8、执行规范见 §10.3、踩坑清单见 §17。当日 demo 已用探针脚本手动跑通筛选全流程（经验 5-10年 + 学历 本科/硕士/博士 + 薪资 10-20K → 确定 → 列表刷新），本 Phase 将其产品化。

### 12.1 WinMouseClicker（Win32 真实鼠标通道）

- [x] 实现 `src/main/input/WinMouseClicker.ts`：输入 page 坐标（DOMSnapshot 给出），输出点击成功/失败。
- [x] 实时校准：`GetWindowRect(Chrome_RenderWidgetHostHWND)` 取渲染视口屏幕矩形，`scale = 视口宽 / 截图PNG宽` 换算（DOMSnapshot bounds 即 device px，与截图同口径）；每次点击前重新校准，不缓存窗口位置。
- [x] 落点守卫：点击前 `WindowFromPoint` 归属校验 = `Chrome_RenderWidgetHostHWND`；失败则 `SetWindowPos` 抬窗重试一次，仍失败 fail-loud 进入 PAUSED。
- [x] 动作：`SetCursorPos + mouse_event`（禁用 SendInput 绝对坐标），拟人分步移动（10 步/300ms）→ 悬停 500ms → down/up。
- [x] 落地形态：node 侧 child_process 调用 `scripts/win-click.ps1`（UTF-8 BOM，中文参数不经 bash 内联）；`win-screenshot.ps1` 保留为诊断工具。
- [x] CLI 在 Win32 点击期间提示用户手离鼠标。
- [x] 修复：`FindRenderWidget` 优先取可见 render widget（Chrome 每窗口多实例，后台标签留隐藏实例，踩坑 #14）；`defaultScriptPath` 兼容 dist 布局多一级目录。

### 12.2 筛选设置自动化（FilterSetter）

- [x] 产品化 `scripts/locate-text.mjs` 的定位逻辑 → `src/main/boss/FilterSetter.ts`（行锚定消歧：行带 = 最近其他行标签垂直距离的一半，x 严格在标签右缘右侧；面板已打开时跳过开面板；徽章计数校验兜底，全部 fail-loud）。
- [x] CLI `filter` 子命令（`--experience/--education/--salary`），真机端到端验证通过（2026-08-05：5-10年 + 本科/硕士/博士 + 10-20K → 筛选·5 徽章校验通过，列表刷新全员本科）。
- [x] CLI `filter --probe` 只读探针：一次快照输出三行全部选项及坐标（LCA 面板容器结构级排除卡片垃圾文本 + 行带几何规则），真机验证通过（2026-08-05）。
- [x] CLI `filter --clear` 清除筛选：开面板（已开跳过）→ 清除 → 确定 → 徽章无计数校验，真机验证通过（2026-08-05）。
- [ ] 自然语言筛选：用户自然语言描述 → LLM 对照 `--probe` 实时面板选项词表翻译成 `--experience/--education/--salary` 参数（probe 即为此提供合法选项词表，2026-08-05 确认设计意图）。
- [ ] 岗位 YAML 配置扩展筛选条件字段（经验/学历多选/薪资），run 启动前自动调用 FilterSetter 并验证列表变化。
- [ ] 「学历本科及以上」语义映射为多选 本科+硕士+博士（CLI 目前需显式传 `本科,硕士,博士`）。

### 12.3 已知 bug 修复

- [ ] 付费检测误判修复：`ScreeningSession.PAYWALL_MARKERS` 改区域限定（x≥400，排除左导航常驻「直豆/首充」文本）+ 弹层文案子串匹配（解锁/购买/商品价格），参考 `ButtonLocator.snapshotContainsText` 加区域变体。
- [ ] 清理 `data/boss-resume.db` 6 条假 `PAYWALL_LOCKED`（肖迪/高佩瑶/李亮/杨志杰/朱威/张哲铭），清理后候选人可重新评估。

### 12.4 收尾

- [x] CLI `greet [--limit N]` 子命令（`GreetExecutor`，独立于旧 ActionPlanner 体系）：DOMSnapshot 找视口内全部「打招呼」→ Win32 逐个点击，每击后校验按钮数减少否则 fail-loud 停止。真机验证通过（2026-08-05：点击直发无确认弹层，按钮变「继续沟通」）。
- [ ] 真机验证 REJECT 写动作通道（「不合适」原因弹层文案未校准，UNKNOWN→PAUSED 兜底）。
- [x] greet 滚动加载：默认上限 10、最大 100，当前屏点完 CDP mouseWheel 自动滚动，滚动前后列表文档 scrollOffsetY 不变判定到底（2026-08-05）。**真机回归修复**：iframe 文档 bounds 是文档绝对坐标，可见性/点击坐标必须减 scrollOffsetY（设计文档 §17 坑 16），否则「不停往下滚但一个都不点」；修复后 limit 1 真机验证通过（滚动后位置点击正确）。
- [x] greet 前置校验：当前页非推荐牛人列表页（无「筛选」按钮）直接报错，避免误报「已滚到底」；付费墙弹层识别（「该职位无开聊权益/商品价格/扫码支付」）fail-loud 提示换职位（2026-08-05 真机发现：打招呼按职位扣开聊权益，无权益职位弹购买层）。**已知不足**：沟通页 DOM 仍挂载推荐 iframe（含「筛选」「打招呼」文本），文本存在性校验会误通过（§17 坑 17），需增强为可见性/结构判定。
- [ ] 付费墙体验优化（用户要求记录）：付费弹层自动关闭（点 X）；greet 开始前预检当前职位开聊权益，避免点出去才发现；YAML 岗位配置记录各职位权益状态。
- [ ] 全部未提交改动过三智能体流程后提交（枚举修复/探针脚本/win 脚本/付费修复）。
- [ ] 同步本文状态 + `docs/ideas.md` 登记。

## 13. 测试命令规划

具体命令在工程建立后固化，目标形态：

```powershell
npm run typecheck
npm test
npm run build
```

真机测试必须单独执行并生成报告，不能混入普通 CI，也不能在无人值守环境执行页面写动作。（GUI 打包命令 `package:win` 等已随路线收敛删除。）

## 14. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| CDP 合成点击被风控选择性拦截 | 筛选类控件不可点 | ✅ 已实证闭环（2026-08-05）：双通道点击，筛选类走 Win32 真实鼠标（决策 8），Phase 10 产品化 |
| BOSS 改版 | 列表解析或按钮定位失败 | DOMSnapshot契约、几何+文本联合定位、fail-loud |
| Canvas 虚拟滚动 | 长图缺段 | 固定重叠、稳定检测、断层检测、保留原始分片 |
| OCR中文长文本错误 | 筛选误判 | DOM摘要优先、置信度、证据化、UNCERTAIN复核 |
| OCR 摘要字段缺失或低置信度 | 查看日志不完整 | 保留置信度和原图证据；列表摘要回退并暂停人工复核 |
| 动作结果无法确认 | 重复操作 | UNKNOWN不重试、动作唯一键、人工复核 |
| 误引入 Runtime | 页面刷新 | 网关硬拒绝、依赖扫描、方法审计、真机门禁 |
| 候选人隐私落盘 | 数据风险 | DPAPI、字段/附件加密、保留期、主动导出 |

## 15. 当前状态

- [x] 原始 CDP 安全域单变量实验完成。
- [x] 详情截图、滚轮和分段截图可行性完成。
- [x] Canvas/Network/WASM 数据形态完成定位。
- [x] Phase 1～9 代码全部完成（CLI 形态），214/214 测试绿。
- [x] 双通道点击真机实证 + 筛选设置 demo 手动跑通（2026-08-05）。
- [x] Phase 10.1/10.2：WinMouseClicker + FilterSetter 产品化，CLI `filter` 子命令真机端到端验证通过（208/208 测试绿，2026-08-05）。
- [ ] Phase 10 剩余：YAML 筛选集成（12.2）、付费误判修复 + 假数据清理（12.3）、写动作通道验证（12.4）。
- [ ] Phase 7/8 真机端到端验证（写动作通道归属、全流程）。
