# agent2：BOSS页面正常但续筛被隐藏渲染窗口守卫拒绝

日期：2026-10-23。本文仅记录只读排查事实及修复建议；未操作客户浏览器、重试任务、修改代码或部署。所有时间均为北京时间，不保存候选人姓名、简历正文或凭据。

本文记录最初只读排查；后续修复状态见[原计划Phase 8及第12节](../plans/plan-runner-local-tool-recovery-lockup-fix.md#12-2026-10-23现场增量隐藏渲染窗口未自动恢复开发验证中)，不代表重新采集了现场证据。

## 结论

本次直接失败点是**Win32原生输入的渲染子窗口可见性检查**，不是CDP读取超时，也不是180秒截止。两次工具回执均为 `UI_CHANGED / effect=none / input_status=not_started`，报 `STALE_OBSERVATION: render widget hidden; input refused`。

CLI确实成功取得网页截图，两个网页视觉诊断均返回 `obstruction=absent / goal_state=satisfied`。但本机隐藏窗口拒绝不在确定性恢复分类内；视觉恢复又只因stage=input就转去window分支，后者INTERNAL_ERROR后返回diagnosis_unavailable，最终让用户手工刷新。这是客户端恢复覆盖不完整，不能解释成用户网站异常。

“原页面观察已过期”是助手对STALE_OBSERVATION的概括；本次抛错位置没有观察时间校验，不能据此认定截图超龄。

## 1. 会话与版本

- 会话：`session_e23e102b8b92`。
- 用户11:38:59“继续筛选”，助手11:39:36回复“这轮没跑起来”。对应trace：`tr_runner_2afd4ac171aa4de4be4bd52945113618`。
- Runner为completed，attempt=1；可观测记录约36.38秒。两次tool span失败，分别约10.42秒、18.05秒。**不是昨日STALE_CONTINUATION导致Runner无法接单的同一故障。**
- agent2部署目录HEAD：`0442891e`。被审查的 `sequence.ts`、`localOperationRecovery.ts` Git内容与本地HEAD `65f69db9`相同。
- 用户确认昨晚更新Runtime/BOSS CLI/后台。设备上报的 `runtime_version=0.2.20` 对应Host Core版本，不能拿它与Runtime UI版本比较后断言未升级；当前仓库为Runtime UI 0.0.24、BOSS 0.3.22。设备manifest未提供BOSS显示版本，未独立核对客户机插件文件digest，不据此推测用户装了旧版。

## 2. 现场证据

| 时间 | 事实 |
|---|---|
| 11:21:43～11:22:20 | 同会话两次boss_filter_options返回同一render widget hidden错误 |
| 11:24:33～11:25:25 | 随后boss_filter_options、boss_filter成功，说明不是整场浏览器一直不可用 |
| 11:25:28～11:29:56 | boss_resume_batch及boss_resume_next两批成功 |
| 11:39:04.142～11:39:13.937 | invocation `f8a02297-ff44-47ca-9304-5d56deb8cf59` 执行boss_resume_next失败 |
| 11:39:16.164～11:39:33.460 | invocation `fc9e4be7-27c3-4001-85f6-04468c926491` 再次执行同工具，同样失败 |

两次续筛均沿父游标 `5bdfd67d-93a7-46f3-bd5f-2315ff88681c`，不是派发前游标拒绝。共同事实：

- `stage=input`、`input_status=not_started`、`attempts_used=1`、`ordinary_retries_used=0`。
- `resumes=[]`，attempted=1，stop_reason=page_operation_failed；本轮没有读取到新简历。
- processed_names却含1项，且为本次失败卡片；没有实际完成读取，不能将此字段当成功数量。是否进入后续排除取决于游标规则，不能仅凭此断言现场已经漏读。
- `step0.diagnose`：page模式、HTTP 200、state=completed、operation_code=OK、obstruction=absent、goal_state=satisfied、无关闭动作建议。
- `step0.diagnose.window`：operation_code=INTERNAL_ERROR、failure_stage=window_capture_or_diagnosis；没有有效diagnosis或HTTP状态，未保留原始错误原因。
- 最终 `visual_recovery.state=diagnosis_unavailable`、policy=readonly、steps=[]。无确定性transport退避步骤，也没有实际恢复点击。

事件记录多次出现connect/attach，但它们发生在网页/窗口诊断各自打开会话时；**不能把这些诊断连接当成已经重试了原读取步骤**。心跳持续续租，无租约过期证据。

## 3. 源码因果链

1. `bossResumeBatch.ts`将卡片点击接到`session.click`，即`WinMouseClicker`的原生输入；本次不是靠改变CDP超时参数就能解决。
2. `WinMouseClicker`先激活绑定CDP target，再进入共享 `@aid/win32-native-input`。`sequence.ts`抬起绑定BOSS主窗并短暂等待，随后`findRenderWidget`枚举Chrome_RenderWidgetHostHWND。
3. `windows.ts:findRenderWidget`优先取可见且面积最大的widget，找不到则返回首个隐藏widget；`sequence.ts`再次检查不可见时立即抛上述零输入拒绝，未重新枚举等待它变为可见。
4. `ResumeBatchReader.input()`识别exitCode=2为未投递输入，但仍抛向批次catch。批次记失败并退出；当前只对`isTransientCdpError`清除临时失败的处理标记，原生隐藏窗口不在其中。
5. `localOperationRecovery.ts`的确定性循环仅接受CHROME_UNAVAILABLE或CDP瞬态错误。UI_CHANGED/原生零输入拒绝不进入循环，转视觉恢复。
6. 视觉分支的`unexplained`包含 `stage==='input' && obstruction==='absent'`，未区分input_status=not_started，也未优先使用goal_state=satisfied。因此正常网页判断仍被转去窗口诊断。
7. 窗口诊断失败后`if (!a.diagnosis)`直接结束，原业务没有被自动续做；模型第二次调用只是再次经历同样路径，最终要求用户F5。

原生输入来源：[sequence.ts](../../clients/shared/win32-native-input/src/sequence.ts)、[windows.ts](../../clients/shared/win32-native-input/src/windows.ts)。业务及恢复入口：[ResumeBatchReader.ts](../../clients/boss-resume-assistant/src/main/boss/ResumeBatchReader.ts)、[localOperationRecovery.ts](../../clients/boss-resume-assistant/src/main/boss/localOperationRecovery.ts)。

## 4. 尚未证明的底层原因

可以确定“本机选中的widget被判隐藏”，但现有云端记录不能区分：窗口/标签切换后尚未重建widget、选到了隐藏回退widget、枚举后可见性改变，还是当前Chrome渲染结构与原生探测策略不兼容。代码每次会枚举窗口，不能无证据认定缓存了旧HWND。

用户截图及成功网页诊断支持网页可见、无可见阻挡；不能反推出那一瞬间每个原生HWND的可见性。鼠标被抢占、内存不足、Chrome自身故障均无现有证据。

window诊断的原始错误未保留，不能擅自认定是模型失败、缺包或CDP失败。

如需进一步定位上述底层原因，补客户机11:38:50～11:39:45的当前Runtime/BOSS CLI原始日志（尤其stderr及boss-input-timing/guard）；如现有日志未记窗口枚举，则后续开发只增加脱敏PID/HWND、target标识、各widget可见性/矩形、前台/最小化状态及阶段耗时，不采集简历正文。无需重新扫描整套安装目录。

## 5. 修复建议：优先改CLI，复用现有恢复链

1. **把明确零输入的窗口定位拒绝纳入确定性恢复。** 对隐藏/尚未出现widget、短暂激活失败，重新激活原target、还原窗口并有限等待，重新枚举窗口，再取fresh DOM及当前卡片坐标后重试。复用现有取消及退避，不把所有STALE或未知外发都放宽成盲重试。
2. **正常网页诊断不得被失败窗口诊断否决。** readonly且本次输入未发出，网页判断无阻挡时，直接走上述本机重定位/领域续读；不因stage=input就强制window。window诊断是需要时的补充，不是所有只读操作额外门禁。
3. **必要时复用已有CDP浏览点击。** 原生widget探测持续不可用、但CDP和页面正常时，可对打开卡片等已支持的读取动作fresh定位后调用现有clickBrowse，并核验详情/候选身份与读取成果；不将该降级用于打招呼/发消息，也不移除正常物理点击的归属/落点校验。先修重新探测，避免为本次故障新建通用桌面引擎。
4. **修复业务接续与错误事实。** 未读取卡片不永久标完成；同批恢复保留成果，服务端不重复处理原记录。窗口诊断保留脱敏失败子阶段/原code；最终提示按“正在自动恢复/真实人工依赖”呈现，不把原生窗口拒绝说成CDP读不到或页面观察超时。

单纯重连CDP可以刷新target/session，但**不会必然修好Win32选中隐藏渲染子窗口的问题**；本次缺少的是“输入前拒绝→原生重新探测→fresh业务步骤重试”的闭环。

修复沿用[原计划](../plans/plan-runner-local-tool-recovery-lockup-fix.md)及六个业务验收：在A2中加入本次真实零输入拒绝形态，验证页面正常且无可见widget时能重定位或按已有读取通道续做，不要求人工F5；不增加新一轮全系统验收矩阵。最初撰写本报告时上述仅为建议，尚未实现或故障注入验证；后续已完成开发及独立验证，实际状态与未验范围见[原计划Phase 8/第12节](../plans/plan-runner-local-tool-recovery-lockup-fix.md#12-2026-10-23现场增量隐藏渲染窗口未自动恢复开发验证中)，原现场证据未重新采集。
