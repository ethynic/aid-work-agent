# BOSS 简历筛选助手原生 CDP 开发计划

> 状态：🔧 Phase 1～4 代码完成，Phase 5/6 接口骨架完成待外部 provider 集成  
> 日期：2026-07-22  
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
| 7 | ⬜ 未开始 | 写动作需用户确认最小样本，且依赖 Phase 6 结论 |
| 8 | ⬜ 未开始 | Vue 业务页面(登录门禁/岗位配置/复核队列/审计日志)+打包验收 |

**真机环境**：用户 Chrome 已带 `--remote-debugging-port=9222` 启动并登录在 `https://www.zhipin.com/web/chat/recommend`，Phase 2 已实测可连。

**当前测试基线**：`cd clients/boss-resume-assistant && npm test`(node 18)→ **98/98 全绿**，typecheck 通过。

**环境**：用 nvm 切 node 24 可下 electron 二进制；测试/dev 用 node 18(better-sqlite3 有 node 18 prebuilt)。打包需 admin 装 VS VC++ workload。



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

## 12. 测试命令规划

具体命令在工程建立后固化，目标形态：

```powershell
npm run lint
npm run typecheck
npm run test:unit
npm run test:integration
npm run test:cdp-policy
npm run test:image-fixtures
npm run build
npm run package:win
```

真机测试必须单独执行并生成报告，不能混入普通 CI，也不能在无人值守环境执行页面写动作。

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 原生 Input 点击仍触发详情异常 | 项目核心阻塞 | Phase 0先验证；失败降级人工点击 |
| BOSS 改版 | 列表解析或按钮定位失败 | DOMSnapshot契约、几何+文本联合定位、fail-loud |
| Canvas 虚拟滚动 | 长图缺段 | 固定重叠、稳定检测、断层检测、保留原始分片 |
| OCR中文长文本错误 | 筛选误判 | DOM摘要优先、置信度、证据化、UNCERTAIN复核 |
| OCR 摘要字段缺失或低置信度 | 查看日志不完整 | 保留置信度和原图证据；列表摘要回退并暂停人工复核 |
| 动作结果无法确认 | 重复操作 | UNKNOWN不重试、动作唯一键、人工复核 |
| 误引入 Runtime | 页面刷新 | 网关硬拒绝、依赖扫描、方法审计、真机门禁 |
| 候选人隐私落盘 | 数据风险 | DPAPI、字段/附件加密、保留期、主动导出 |

## 14. 当前状态

- [x] 原始 CDP 安全域单变量实验完成。
- [x] 详情截图、滚轮和分段截图可行性完成。
- [x] Canvas/Network/WASM 数据形态完成定位。
- [ ] Phase 0 正式工程化和原生点击稳定性门禁。
- [ ] Phase 1～8 未开始。
