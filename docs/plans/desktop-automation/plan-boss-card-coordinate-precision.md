# BOSS 坐标桥接与 Runtime MCP 大结果回传修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 原始几何口径修复与边界回归 | ✅ 完成（2026-10-11） | 3 项回归旧代码全失败；修复后相关 55 项通过 |
| Phase 2 | 独立测试、CodeReview 与构建 | ✅ 完成（2026-10-11） | typecheck、436 项测试及 85 组额外边界通过，独立 CR 无阻断 |
| Phase 3 | Runtime 调度真机续筛验证 | ✅ 完成（2026-10-11） | 首批3+两次续读各3，共9人无重复；实际滚动/完整回传/图像抽查通过 |
| Phase 4 | Win32 DPI 坐标桥接修复及独立验证 | ✅ 完成（2026-10-11） | 439 项全量及局部重跑 38 项通过，CR 两项 P1 闭环；Phase3验收通过 |
| Phase 5 | 共用 MCP stdio 有限接收预算与诊断 | ✅ 完成（2026-10-11） | core 33、Runtime/Host 79 项通过；CR 无阻断，预算有限且 unknown 不重放 |
| Phase 6 | 续筛列表 CDP CSS 坐标实测映射 | ✅ 完成（2026-10-11） | 独立相关74/74、CR闭环；真实两次next推进列表并读到首屏之外 |
| Phase 7 | 嵌套iframe祖先滚动偏移桥接 | ✅ 完成（2026-10-11） | 独立全量452/452、额外25组及CR通过；37完整画布样本/长图抽查通过 |

## 缺陷与修复方案

2026-10-11 来源会话通过真实 Runtime PollLoop/ApiClient/ProviderSet、Windows 桌面检测与机器作用域锁，使用隔离 FakeCloud Device API 调用真实 `boss-cli mcp --stdio`：首屏 4 张卡片全部匿名失败，attempted=0；同页 doctor 配对 4/4。已测得首卡姓名 Y=276，按钮 Y=291.5；批量读取先把按钮取整到 292，严格的 `|dy|<16` 因 dy=16 拒绝，doctor 保留按钮小数而通过。

共享 `cardName.ts` 还会将姓名文本坐标取整，同样可在小数边界产生误拒或误配。按钮与姓名均在几何筛选、排序、配对及输出中心点时保留原始精度；保持 `|dy|<16`、姓名模式、状态排除、相对姓名列和视口校验，不扩大阈值，不硬编码 DPI，不改变身份校验及调用权限。

回归覆盖真实 15.5px 边界、姓名自身小数边界、dy=16/超界拒绝、iframe owner 与滚动换算，以及小数坐标的跨批排除和重叠列表滚动。点击仍复用现有 Win32 适配器；最终屏幕像素换算由适配器负责。

现有文档差异：原生 CDP 设计 §7.1 的旧坐标说明称无需减滚动偏移，§10.3 的旧通道说明默认 CDP；当前子项目 AGENTS、`domSnapshot.ts`、批量执行器及后续真机记录采用 owner + bounds 中心 − scrollOffset，开详情走 Win32。本任务以当前实现及来源会话实测为准，保留滚动公式与通道，不借修复单方面改义。`WinMouseClicker` 在调用既有整数参数 ps1 前才进行 `Math.round`；几何配对不经过该输入层取整。

## 范围与责任

- 本会话唯一修改 BOSS 产品源码、测试、本计划及索引；不提交、推送或部署。
- 来源会话 `01a12934-c5ea-7f82-964f-78c574b10195` 唯一负责 `.tmp/boss-runtime-live.mjs`、真实桌面操作及[原续筛计划](plan-boss-screening-continuation.md)记录；双方已获用户双向沟通授权。
- 已读取[共同契约](../../system/runner-desktop-runtime-integration-contract.md)。Phase 1–4 仅修 Provider 内部几何/输入；Phase 5 按实际 Runtime 阻断扩展共享 core 的有限 stdio 预算与脱敏诊断，不修改 Device API、binding、manifest 或 wire schema。
- 原始计划与 `docs/ideas_finished.md` 已有改动须保留。筛选面板薪资行混入到岗文案属于旁路观察，不扩展本任务。

## 验证记录

- 旧代码构建 exit 0；`node --test --test-name-pattern='原始小数|姓名节点小数|小数配对跨批' dist/tests/resume-batch.test.js`：exit 1，3/3 失败，复现按钮及姓名取整缺陷。
- 修复后 `npm run build:main`：exit 0；`node --test dist/tests/resume-batch.test.js dist/tests/greet-executor.test.js dist/tests/doctor-readiness.test.js`：exit 0，55/55 通过。
- 独立测试角色：`npm run typecheck` exit 0；最终 `npm test`（含 build:main）exit 0，36 文件 / 436 项全部通过，fail/skip/cancel=0。初次 438 项中的 2 失败为 `dist/tests/ocr-engine.test.js`、`resolve-probe.test.js` 无源码的陈旧产物；核对不存在对应 TS 源码并限定路径清除后，标准命令通过，未为此改产品逻辑。
- 独立额外内存验证 exit 0：85 组几何/状态边界，涵盖姓名列相对阈值（按钮 X=600.3）、正负 15.49/15.5/15.99/16/16.01px 及状态词排除。
- 最终 dist 隔离启动：CLI `--help`、`resume-batch --help`、`version --json` exit 0；`resume-next --bogus` exit 2 正确拒绝。均不访问 9222 或操作真实桌面。
- 独立只读 CR 无阻断：严格姓名/状态/视口/相对列/16px 保护不变；共享 greet/doctor 行为及 WinMouseClicker 末端整数输入保持；回归能捕获原误拒与误接收。
- 主控最终 diff 检查通过；源码/dist 已稳定并通知来源会话开始真机。FakeCloud 验证不代表真实云端 Runner、模型识别、入库或计费验收。

## Runtime 真机反馈（2026-10-11）

来源会话执行 `node .tmp/boss-runtime-live.mjs batch batch-fixed`，保留原失败证据并将新报告写入 `.tmp/boss-live-20261011/batch-fixed/report.json`。真实 Runtime 调用链确认 4/4 姓名成功配对，按钮 Y 保留 291.5/659.5/1027.5/1395.5；attempted 从 0 恢复到 4，原取整缺陷已不再出现。

后续读取尚未通过：首屏 4 张卡片共 12 次既有 Win32 姓名点击后未出现 CANVAS，4 个详情打开超时，成功简历为 0。Provider 已退出、隔离监听已关闭；整页截图为 2504×1474，与 DOM viewport 一致，截图仍为推荐列表无弹层。当前卡片姓名点是整数，本次小数输出修复未改变这些实际输入点；点击落点/窗口几何与详情形态由来源会话继续只读排查，不能将此批次声称连续读取、累计排除去重或实际列表滚动验收通过。

### 后续阻断：Win32 测量与光标输入口径

来源会话排除多个同标题窗口后，在 `.tmp/boss-window-readonly.ps1 -MoveProbe` 中仅移动、不点击/键入/滚动，finally 恢复光标及线程上下文。默认 PowerShell 的 RenderWidget 为 (25,284)–(1277,1021)，映射首姓名为 (367,422)；PMv2 读出的真实矩形为 (49,568)–(2553,2042)，正确物理点为 (733,844)。原 `SetCursorPos(367,422)` 返回 true，但直接 `GetPhysicalCursorPos` 也是 (367,422)，反算 page=(318,-146)，PMv2 下落点不属该 widget；原默认上下文守卫却通过。此为实际错位证据，不是根据 2 倍比例猜测 DPI。

修复范围追加既有 `scripts/win-click.ps1`、`cv-wheel.ps1` 及相关测试。选用当前线程 PMv2 上下文，使矩形测量、归属守卫、光标移动与滚轮使用同一物理坐标口径；验证设置成功，不支持或设置失败即停止，不回退盲点、不硬编码倍率。finally 恢复原上下文；弃用 Forms.Cursor.Position，以直接 Win32 物理光标回读验证落点；不改 CDP/Win32 通道、身份门或 Runtime/Provider 契约。风险升级为真实输入桥接，继续保留独立测试与独立 CR。

实现共用 `scripts/BossPhysicalInput.cs`，两份既有 PS1 在同一 Add-Type 中编译共享 helper 与各自窗口接口，保留 UTF-8 BOM。helper 校验 PMv2 设置/恢复、物理矩形与光标 API 成功状态、移动后的物理回读和精确 HWND 归属；点击悬停后、每次滚轮前重新映射并检查落点。滚轮与点击一致选取最大可见 widget，默认限制 BOSS Chrome 标题。既有 Write-Error/exit 1、2 分支显式使用 ErrorAction Continue，避免全局 Stop 提前改变退出码。

新增可执行验证以 fake native 边界运行生产 C# helper 和两份原映射函数，覆盖设置拒绝/API 缺失/验证失效/恢复失败/线程改变/矩形失败/移动失败及回读偏离/错误 HWND；完整原点击脚本替换 OS 边界后验证遮挡仍 exit 2、恢复上下文且不发鼠标或键盘事件；真实 PS1 以唯一不存在标题启动，验证编译和缺失窗口 exit 1，不操作真实窗口。主控 `npm run build:main` 与新测试 2/2 通过，独立验证/CR 进行中。

独立 CR 发现并修复输入后异常回执 P1：真实点击后 finally 恢复失败原会映射 UI_CHANGED/effect=none。新增 Scope.InputStarted，在真实 DOWN/WHEEL 前才标记；仅已可能发出输入的异常输出可信 `[BOSS_INPUT_UNKNOWN]`，WinClickError 对此映射 EXECUTION_UNKNOWN，写动作 effect=unknown、不可重试；输入前拒绝仍 none。完整 fake 点击脚本验证输入后恢复失败非零退出及标记，实际 operation 回归验证前/后失败的 code/effect/retryable 与单次尝试；最终定向 40/40 通过，最终独立全量与 CR 复核中。

独立最终全量 `npm run typecheck` exit 0；`npm test`（含构建）exit 0，37 文件 / 439 项通过、0 跳过。54 组 100/150/200% 与负屏幕原点映射、真实 PMv2 只读进入/光标读取/恢复、npm 打包包含共享 C# 与两份 PS1 均通过。CR 再发现输入后标记与环境关键词的优先级冲突：仅可信 WinClickError 标记判断提前到消息提取后，避免包含 `timed out` / 登录关键词的输入后异常被降级为可重试 none；实际 greet 回归补齐两类冲突，普通无标记错误保持原行为。最终只读 CR 无阻断；该局部分类修改的独立受影响用例重跑中，其余证据复用。

最终独立受影响验证：`npm run build:main` exit 0，`node --test dist/tests/operations-result.test.js` 38/38 通过，0 失败/跳过/取消；额外 12 组可信标记与超时/登录/ECONNREFUSED cause 冲突、无标记及非 WinClickError 假标记验证通过。两个 CR P1 均闭环，当前无阻断；源码和 dist 稳定，明确放行来源会话执行 `batch-bridge-fixed`。尚未得到业务连续读取结果，不提前记 Phase 3 完成。

来源会话原生仅移动 `.tmp/boss-physical-input-move.ps1` 验证新 helper：Enter→ReadRect→MoveCursor→RequireCursorOnWidget→ReadCursor exit 0，真实矩形 (49,568)–(2553,2042)，请求与物理回读均 (733,844)，GuardPassed=true；finally 恢复物理光标和上下文。该结果证明本机桥接错位已纠正，尚不替代业务点击/简历读取验收。

真机范围：来源会话本机为 Microsoft Windows 11 专业版 Insider Preview，Version 10.0.26220 / Build 26220，窗口 DPI=192。平台信息只描述验收环境，不代替实际错位回读证据。后续复测写入 `batch-bridge-fixed`，保留 `batch-fixed` 失败现场。

`batch-bridge-fixed/report.json` 首个实际点击在 10 步移动与 500ms 悬停后的 `RequireCursorOnWidget` 被拒绝，Runtime 回执 UI_CHANGED / effect=none；0 简历，Provider 已退出，未自动重试。此前目标 HWND 前置归属检查与每一步即时物理回读均未失败，当前证据不能确定是悬停后光标偏移、窗口矩形变化还是归属变化。保留严格守卫，来源会话继续仅移动观测初始/移动末端/悬停后的物理点、矩形和归属；不提前将业务验收标完成。

完整前台化仅移动 `.tmp/boss-click-hover-probe.ps1` exit 0：Widget=1311058，Rect=(49,568)–(2553,2042)，初始/刷新期望点恒为 (733,844)，期望点归属始终正确；每步 MoveCursor 即时物理回读通过，但移动完成与 500ms 悬停后的实际点均回到 (3711,1494)，属另一个 Chrome_RenderWidgetHostHWND=198908。目标矩形及 HWND 未变，证据排除本次复映射或坐标公式导致拒绝；推断移动后存在其他输入来源改写光标，来源尚未确定，不能认定用户操作。生产脚本继续拒绝、不发点击，不通过反复拉回光标放宽保护；待输入环境澄清后再业务验收。

共享 helper 单独 10 步 / 25ms 探查复现：step 1–5、8–9 请求与延时回读一致；step 6、7、10 即时读回正确，25ms 后回到 (3711,1494)，500ms 后仍为原点，矩形不变。证据汇总于 `.tmp/boss-live-20261011/batch-bridge-fixed/pointer-interference-evidence.json`。该复现独立于完整前台化与 PS 变量刷新，现阶段无需继续修改产品代码或重复业务失败。

交付状态：开发、构建、独立测试及 CR 已完成，两处产品缺陷均修复；真机阶段登记为「输入环境干扰待解除」，连续读取、累计排除去重、实际滚动尚未验收。来源会话负责等待用户反馈并在干扰解除后继续原任务，两会话既有通信授权保留；本会话停止扩展修复，索引迁入已完成开发并明确保留验收限制。最终业务 run Provider/隔离监听已退出，原生仅移动观测均 finally 恢复光标/上下文；无提交、推送或部署。

## Phase 5 扩展：Runtime stdio 回传阻断

上述交付为 Phase 4 时点记录。来源会话收到用户 RDP 条件补充后，以窗口模式、客户端鼠标移出远程画面进行仅移动对照，10 步即时/25ms/500ms回读均通过。真实 `batch-rdp-windowed` 的 CLI 内部读取 3/3 成功、失败 0、35 次截图；随后 Runtime 收到 Connection closed，回执 EXECUTION_UNKNOWN / effect=unknown，无简历 data。CLI 内部成功不等于 Runtime 验收，不自动重放该 unknown 批次。

只读代码事实：两侧 SDK 1.30.0 `ReadBuffer` 缺省 10MiB，append 超限会调用 onerror 并关闭 transport；共享 ProviderManager 未配置预算且未绑定 client.onerror。BOSS MCP 结果将含图片 base64 的同一 payload 同时返回 text JSON 与 structuredContent；服务端单图解码上限 10MiB，MCP 单批最多 3 份。临时文件读取后清理，当前实批原始传输字节数缺失，不能仅由截图尺寸宣称该批超限确证。

隔离合成复现（无 GUI、无真实图片或姓名）：当前 Runtime ProviderManager 消费路径，2MiB base64 双份响应成功；6MiB base64 对应 MCP 12,583,181 字节，首个错误 `ReadBuffer exceeded maximum size of 10485760 bytes`，随后 ProviderCrashError / Connection closed，子进程 exit 0；只在该 stub 接收端改为 64MiB，同数据完整返回。临时目录限于本任务 OS temp 并清理，不改来源会话 `.tmp`。

实施选择：唯一写入者为本修复会话，修改共享 core `src/legacy/providerManager.ts`、Runtime 隔离 Provider 传输测试及其 fixture/helper。Runtime 默认接收预算设有限 128MiB，覆盖 3×10MiB 图片经 base64 与双份包装约 80MiB 加元数据；受信内部构造选项可选 1–128MiB，拒绝 NaN/Infinity/非整数/越界，不增加 Device/LLM 配置字段。绑定 client.onerror 只记录白名单传输错误类型与预算字节，保留首个超限原因，不打印 JSON/图片/名字/凭据。超限仍关闭，unknown 不自动重放。保留现有 text 与 structuredContent 兼容，不改图片编码、单批容量、wire schema、binding、许可或公共壳，不升级共同契约 v2.0/wire 候选0.3；共同 H4 新产物协议仍未实施。

预算与 Producer 事实须区分：10MiB 是 `src/services/recruiting_resume_service.py` 的云端单图解码接纳上限；当前 BOSS 客户端 `imageBuffer` 并无此硬上限。128MiB 是本机总 stdio 接收资源预算，只覆盖上述可接纳规模的估算，不保证任意实际三份都能回传；超出预算仍关闭并返回 unknown，不通过无限加大预算或自动重读规避。

SDK 限制准确口径：预算按每个 transport 的当前未消费 stdout 缓冲字节执行，不是调用累计流量、所有 Provider 合计预算或 Host RSS 上限。SDK `Buffer.concat`、字符串与 JSON 对象会带来额外内存放大；保留有限输入预算并观察合成大消息性能，不把 128MiB 解释为进程内存保障。

主控实现与自测：新增本地 `stdioMaxBufferBytes` 校验，默认128MiB；transportGeneration 防止旧实例迟到 onerror/onclose 污染新实例，respawn 重置脱敏首因。`npm run build:main`（Runtime，含共享 core 构建）exit0；`node --test dist/tests/providerTransportSize.test.js dist/tests/providerCrash.test.js` 6/6 通过，0跳过。覆盖默认 ProviderSet 完整回传 6MiB base64 双份结果、1MiB 正边界/超一字节拒绝、异常预算值、超限无重放、respawn恢复/首因清零、畸形JSON敏感哨兵不进入日志及 FakeCloud Runtime unknown无重放；既有只读/写崩溃行为保持。独立测试与 CR 进行中。

独立中间验证：core+Runtime typecheck/build exit0，core 5 suite / 33项全部通过；Runtime 10相关suite初次66项中64通过、2失败。两失败来自既有 `providers.test.ts` 仍按18工具/旧digest322acb断言，而拉取的注册已有 `boss_resume_next` / digest9bef64；本次未改产品 manifest，不能将失败称为全绿。来源主会话明确授权只更新旧测试：19工具、12保守写工具与当前固定digest，同时保留“剔除新增next后仍匹配旧digest”兼容断言，防止掩盖无关漂移。独立只重跑受影响注册suite，复用其余通过证据。

独立性能观测（隔离合成，不是真实batch字节测量）：默认ProviderSet回传约80MiB wire（40MiB base64在text/structured重复）exit0，耗时13,279ms，RSS初始57MiB、完成380MiB，OS峰420MiB。25ms采样漏过同步concat/JSON峰，以OS峰为准；该结果证明有限预算可用且有明显内存/时延放大，不是RSS保障，不进一步上调。预算端点/非法值、base64不进入日志通过。

Phase5独立只读CR无阻断：旧构造调用兼容，Runtime re-export/Desktop复用同一core；非致命onerror不改crashed，generation隔离旧实例；SDK onclose先标记crashed再拒绝调用，因此超限首因进入ProviderCrashError；既有unknown/retryfalse/safe_to_retryfalse保持。测试真实SDK/子进程，fixture无浏览器或真实业务数据；等待注册suite与真实Host消费检查最终结果。

Phase5最终独立验证通过：core+Runtime typecheck/build exit0；core全部5 suite /33项通过。Runtime相关10suite原66项中的旧2失败在授权修正后，`npx tsc -p tsconfig.main.json` exit0，providers+protocol 8/8通过；按相同受影响状态复用其余测试后，Runtime相关唯一67项通过；实际Host隔离消费 `hostIndependent` 12/12通过，合计Runtime/Host79，0跳过/取消。额外独立核验首因→respawn清零→旧client迟到error/onclose隔离、日志脱敏、预算端点均通过。注册测试修正独立CR通过，production manifest无diff，当前及历史digest锚点保留；不重新运行未改BOSS的439项测试。

最终core唯一源码 `clients/shared/local-tool-host-core/src/legacy/providerManager.ts`，构建入口 `npm --prefix clients/agent-tool-runtime run build:main` 统一构建core与Runtime薄壳；现有dist已稳定。源码/构建通过不等于业务真机通过，已明确放行来源会话的受控验证，须重新通知用户鼠标静止条件；不自动重放此前 unknown 批次，Phase3待真实连续读取/去重/滚动结果。

### 新版真实 Runtime 首批回传（2026-10-11）

来源会话在用户重新保持 RDP 窗口模式、客户端指针移出静止后，仅移动探查再次通过，并以新隔离 fixture/设备/claims 执行 `batch-runtime-large-fixed`；未重放旧 unknown invocation。首批 limit=3，attempted=3、resumes=3、failures=0，真实 Runtime 成功接纳完整 data；continuation.available=true、processed_count=3，filters_verified=false（不能声称筛选一致性验收）。data JSON=9,522,163 bytes，Runtime 日志 data_bytes=9,522,059；三图 PNG bytes=1,791,051 /1,606,275 /3,743,153，均 bottomReached=true、suspectSeams=[]。

新批三图合计原始字节7,140,479，base64双份包装的传输下界已超过旧10MiB接收预算，且本次真实回传成功；这是新批实际证据，不将其套成旧 `batch-rdp-windowed` 未测payload的精确值。来源会话已使用原 continuation.arguments 累计排除调用 boss_resume_next；续读去重及真实推荐列表滚动尚待结果。

后续 `boss_resume_next` 完整回传余下1人，5段到底；两批共4个不同候选人，累计排除count=4，无失败或重复，next data约1.38MB。next.stop_reason=no_progress、continuation.available=false，两次CDP wheel后卡片/偏移signature未推进。harness原仅用wheel调用数>0设置listScrollVerified=true是假阳性，来源会话负责修正；本计划不能据此记实际滚动通过。JOB_CHANGED/none与不受信boss_close_detail的TOOL_NOT_ALLOWED/none再次通过，Provider已退出。

只读源码分析：当前列表推进固定viewport中心CDP wheel，未定位推荐列表滚动容器；两次1200/1800ms延迟snapshot的卡片姓名/Y及各文档偏移signature都不变则no_progress，正确不宣称reached_end。来源会话继续仅截图/DOM核对中心点归属、可见iframe边界与尾部证据；当前不改源、不转通道，不因发出wheel就宣称已滚动或已穷尽全部列表。

来源会话追加只读截图与 DOM：推荐列表仍在顶部，右侧滚动条 thumb 位于顶部、约占1/4高度，第四卡只露上半部，明确不是列表末尾。doc0 首 bounds 为2504×1474、doc2 为2168×1394，两文档 offset 均0；固定中心(1252,737)在截图的推荐卡片区域内，不能仅归因为滚动点落在列表外。harness 已改为只有读到首屏之外的新候选人才判列表滚动通过。官方 CDP Input 协议定义坐标与 wheel delta 为主 frame viewport CSS px，项目既有文档将 Snapshot/PNG 描述为 device px；仅二者尺寸一致不证明 Input 同口径，后续须以容器几何和受控对照证实，不能猜测固定除2或改走 Win32。

高风险共用执行路径沿用独立测试与独立 CR；变更前同步 Runtime 与 Desktop 计划共同检查点。来源会话唯一负责桌面操作及后续实批验证，本会话不真实输入、不提交/推送/部署。

## Phase 6：续筛列表 CSS 输入坐标

来源会话以已有 CLI Gateway 与方法白名单采集 fresh DOMRects：根 HTML bounds=[0,0,2504,1474]、clientRect=[0,0,1252,737]，实际 sx=sy=2。仅把原中心与滚动步长按测得比例映射为 CSS (626,368.5)、deltaY=368.5，发送1次既有 wheel（无click/key），推荐 doc2 scrollOffsetY 从0→737；contentHeight5676不变，主文档/隐藏iframe偏移仍0。证据为来源独占的 `batch-runtime-large-fixed/css-wheel-probe.json`。这证明当前列表滚动的单位桥接缺失，不根据DPI硬编码倍率。

实施范围仅 `boss_resume_next` 列表 scroll：Gateway/session 允许可选 includeDOMRects（默认仍false），续筛 Reader 使用 fresh 主文档根 HTML 的同节点 bounds/clientRects 测量比例；缺失、无效、冲突几何即 fail-loud、不发滚轮。每次延迟重试使用新快照重新测量，保留两次 signature 确认、30屏、取消、1000历史、姓名身份门与 no_progress 语义。Win32 姓名点/视口、详情读取及其他 CDP 浏览动作不改；不增加方法、权限、wire或Runtime core接口。修复会话唯一写入源码/测试/本计划，来源会话独占 GUI/9222/`.tmp`。按子项目三角色要求执行独立测试与CR；最终放行后来源起新受控只读业务验收，不自动重放unknown调用。

主控构建exit0，初次相关3suite72/72通过；新增计时包装与operation接线测试1/1通过（续筛 true选项透传、普通batch仍默认false，坏几何UI_CHANGED/effect=none、不重试、无输入并close）。独立 typecheck与npm test（含构建）exit0，37文件444项全通过、0失败/跳过/取消；随后新增真实根结构永久回归后buildexit0、resume-batch29/29通过，覆盖HTML位于layout尾部、首#document空clientRect、dense/sparse对齐。额外独立32组比例/800步长上限、fresh几何失效、取消、身份与历史检查通过；隔离CLI help/resume-next help/version exit0、未知参数exit2。未GUI或连9222。

独立CR发现并已修复P2：根HTML位于尾部且自身几何有效时，首#document bounds的NaN/缺高可绕过差值比较。列表输入桥接现先验证原首bounds四元有限/正尺寸，再显式验证viewport有限/正尺寸，坏几何不进入Input。真实根结构测试补两轴NaN/Infinity/undefined/0/负值的零wheel回归；clientRects注释注明CSS客户端矩形/尺寸，不把任意节点x/y当全局坐标。首次P2重跑3suite为73通过/1失败：0/负尺寸已被既有viewportOf更早以FilterSetError拒绝，新增断言误要求ResumeBatchError；测试现按实际前置保护修正，仍验证拒绝及零wheel。核对源码确认viewportOf无默认尺寸兜底，已纠正此前注释及记录，未改公共视口逻辑。受影响定向重跑及CR复核中，未改core/Runtime与其他BOSS源不重复测试。

Phase6最终独立构建exit0、resume-batch29/29通过，复用同最终产品源的cdp-gateway+operations-result45项通过，相关3suite74/74、0失败/跳过/取消。此前全量444项与新增结构1项合计445个唯一项目测试有有效证据，不声称重跑过445全量；typecheck结果有效。独立另19组首bounds缺损/各轴非有限/0/负尺寸、HTML有效条件下均拒绝且zero click/wheel。最终只读CR无阻断，P2及断言问题全部闭环；最终dist已构建，主控diff检查通过。已放行来源会话通知用户再起新一轮真实Runtime续筛；Phase3须等实际偏移推进与首屏之外候选人读取，不把受控单wheel诊断替代完整实批验收。

## Phase 7：嵌套 iframe 祖先滚动偏移

Phase6后 `batch-css-fixed` 真机列表实际滚动成功，推荐doc2偏移达到2948；但前一诊断737偏移起点时，同一人的拼接高度14822→7890、4838→2279，不能将有效PNG或bottomReached作为完整简历证明。继续下滚后出现1460/1468×1394大CANVAS，Reader误报未打开。来源只读活动详情证据 `active-detail-geometry.json`：doc2 scrollY=2948、owner=(336,80)；doc3 scrollY=0，现有helper累计owner=(336,3028)，canvas bounds=(0,0,1468,1394)，locateResumeCanvas=null。差值2948与父文档滚动一致，`domSnapshot.accumulateOwnerOffset` 当前仅累加owner bounds，未扣owner所在文档偏移。

修复范围为共享BOSS helper：各祖先层加入owner bounds时减去该owner所在document的scrollOffsetX/Y；leaf自身偏移仍由现有调用者减一次，不重复扣。保留owner唯一性、循环和缺可见layout拒绝，不改通道、阈值、wire、Runtime或权限。增加多层/两轴/小数/dense/sparse及真实0/737/2948完整canvas回归，并覆盖原匿名/身份/滚动/图像解析链路，执行独立全量与CR。来源会话唯一负责真实Provider与桌面；在其明确通知业务Provider退出前，本会话只读代码/证据、登记文档，不改源码或构建dist。原计划及本轮图片不记Phase3完成。

来源随后确认Provider自然退出、harness exit1且监听/fixture已清理；探查时进程已不存在，实际没有执行Stop，不是unknown写重放。本轮next实际19尝试、1返回图/18画布定位失败、12次列表wheel、偏移737→7967、processed_count22，两次续读验收未完成。已有裁短图片全部保留为失败证据，不认可完整性；后续来源以Runtime→CLI重入页面及新fixture重新验收。

主控红绿验证：旧helper `npm run build:main` exit0；新 `dom-snapshot-owner-scroll` 的3项祖先滚动回归exit1、0通过/3失败，直接复现offset817而非80、canvas高度657而非1394、姓名点多737。产品仅两行offset分别扣owner文档X/Y滚动，未改任何caller公式；构建exit0，`node --test dist/tests/dom-snapshot-owner-scroll.test.js dist/tests/resume-reader.test.js dist/tests/resume-batch.test.js dist/tests/greet-executor.test.js dist/tests/doctor-readiness.test.js` exit0、86/86通过、0跳过/取消。新增7项涵盖0/737/2948/负小数、dense/sparse、多层两轴、leaf单扣、默认零滚动、隐藏/缺/多owner/cycle拒绝；执行实际Reader管线并明确断言wheel/sameView/stitch均得到完整336,80,1468,1394区域，非仅PNG字节有效或bottomReached。合成单段图片不代替真机首尾内容与拼接完整性验收。独立全量/typecheck/启动及只读CR中，core/Runtime未改复用有效结果。

Phase7最终独立 `npm run typecheck` exit0、`npm test`（含build）exit0，38文件452/452、0失败/跳过/取消。额外25组纯内存检查通过：祖先深1/2/3/5、正/零/负双轴小数及dense/sparse保持视口原点；leaf只扣一次，负原点+祖先/leaf滚动的canvas裁剪与15.5px姓名配对边界保持。隔离CLI --help/resume-next --help/version --json exit0，未知flag exit2。正式只读CR无阻断：各caller未重复扣祖先，main offset0及缺省滚动行为、owner唯一/缺layout/循环保护保持；原部分FilterSetter未扣leaf为已有事实，不扩大本次修复。文件头注释同步澄清祖先/leaf职责，不影响已验证逻辑。主控diff检查通过，源码/dist稳定，放行来源起新一轮Runtime实批；真实PNG完整性/首尾/接缝与内容遗漏仍未验收，Phase3保持进行中。无提交、推送或部署。

### Phase7新版真机反馈

首轮 `batch-owner-fixed` 在任何简历读取前安全退出：Runtime经菜单chat→recommend保留推荐iframe偏移8814，未满足harness起始offset0要求。保留缓存/滚动位置符合既有goto语义，不作为新增产品缺陷。来源随后以既有编译CLI Gateway `Page.navigate` 重载原推荐URL，只做验收起点准备；后续业务调用仍通过Runtime→MCP→CLI，未增加产品重置能力或修改DOM。

新轮 `batch-owner-fixed-final` 起始offset0通过，8次详情只读采样fullCanvas=true，实际rect=(336,80,1468/1460,1394)，支持祖先偏移修复及当前完整画布定位已生效。首份CLI内部7段到底成功；第2份 `cv-wheel.ps1:156 RequireCursorOnWidget` 因光标/归属变化拒绝，读取管线Escape关闭成功，fixture首失败协作取消生效，Runtime回执CANCELLED/effect=none/data0，Provider已退出；后续点击也被守卫拒绝。当前没有完整批次返回或续读，不以内部单份成功/8次几何采样替代完整长图、累计去重及连续业务验收。来源只读复核输入环境与原生守卫证据，不能据此确定操作者或RDP具体事件；产品源/scripts/dist保持已验证状态，Phase3继续进行中。

用户随后确认上一轮期间操作过远程桌面，现已移出指针并停止操作。来源起新 `batch-owner-stable-input`，HoverProbe、fresh起始offset0与首屏4/4配对均通过；首卡3次姓名点(684,276)原生点击守卫通过，但8秒内无CANVAS，fixture立即协作取消，Runtime CANCELLED/effect=none/data0，Provider退出。后续同CLI Gateway只读截图确认当前职位今日查看权益已用完，BOSS显示升级付费弹层；这解释该轮无CANVAS，不是新坐标失效证据。未操作付费，含候选信息/付费码的本机截图不公开展示。来源已向用户询问换另一已有职位验收或权益恢复后继续，等待回复；源码/scripts/dist保持稳定，Phase3继续待验收，不把不同输入条件/职位的结果混为同一批完整性证明。

## Phase 3 最终真机验收与收口（2026-10-11）

用户自行充值后，来源使用同职位的新fixture `batch-owner-quota-restored`，不复用旧continuation；原生悬停检查与现有CLI Gateway起点准备后，真实业务调用仍走Runtime→MCP→本机Boss CLI。起始推荐文档offset0，首屏4卡；首批3份、两次next各3份，共9个不同候选人，各批attempted=3/failures=0，累计processed_count3→6→9，无重复且排除历史校验通过。3次CDP列表wheel使doc2实际偏移0→737→2211，两次next均推进，读取9人超过首屏4人。

37次fresh详情采样均fullCanvas，rect=(336,80,1468或1460,1394)，祖先scroll覆盖0/737/1474/2211；未再出现上半区裁短。9份PNG签名/IHDR尺寸验证通过，bottomReached全部true、suspectSeams=[]。来源每批抽查1张原始长图，共3张（3-resume-1/4-resume-2/5-resume-3），均看到候选头部及末端内容，未出现此前737父偏移造成的姓名缺失/裁短；该抽查不是逐像素或OCR无遗漏保证，filters_verified仍false。

Runtime三批完整回传，均success/OK/effect=none、started/progress/resultAck正常；本机report的resultDataBytes分别6,181,113 /6,464,529 /7,390,870（与先前独立传输预算验证区分，不套用于旧未测失败payload）。418次心跳；故意职位不符的JOB_CHANGED/effect=none与不受信工具TOOL_NOT_ALLOWED/effect=none均拒绝成功。Provider及fixtureHTTP已退出，用户Chrome保留。

最终证据 `.tmp/boss-live-20261011/batch-owner-quota-restored/report.json`：主控只读核验completed/providerExited=true、resumesRead9、noDuplicateCandidates/cumulativeHistoryVerified/listScrollVerified/readBeyondInitialViewportVerified=true、continuationBatchesVerified2、完整画布样本37、PNG/bottomReached各9、suspectSeamCount0及三批回传字节/ACK；不输出候选信息、图片base64或付费码。Phase3完成，修复任务全部收口，仅更新本计划与修复索引，原续筛计划由来源会话维护。产品源/scripts/dist保持Phase7已验证状态，未重测/重构建、未提交/推送/部署。验收使用隔离FakeCloud Device API，未调用真实云端Runner、模型OCR、入库或计费，不能将本机Runtime链路通过表述为这些云端能力已验收。

参考：[GetWindowRect DPI 虚拟化](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowrect)、[SetThreadDpiAwarenessContext 返回/恢复](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setthreaddpiawarenesscontext)、[上下文比较](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-aredpiawarenesscontextsequal)。微软光标概述的逻辑坐标说明不能替代本机实际输入回读结果。
