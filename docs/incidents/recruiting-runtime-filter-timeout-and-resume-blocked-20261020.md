# 招聘智能体筛选耗时与登录后恢复阻塞诊断（2026-10-20）

## 结论与完成范围

**当前开发检查点（共同契约2.5）**：已实现显式停止原Runtime受管旧工具进程的路径，以原设备身份、原home独占及既有DPAPI Job事实覆盖存量unknown/result_json=NULL且没有generation的BOSS调用；不重放原任务、不改业务unknown、不要求Windows重启。v2纯读仍独立，页面不可观察也不抹掉可信停稳。Runtime/Core及Web构建、自测通过；独立120项（native15、真实PG27、前端78）及1280/390页面验证通过，主控实看截图，独立CR无未解决P0/P1/P2。实际native→Host→result API/PG→用户新意图create/claim/start已串联；桥接Runner终态使用隔离夹具，不冒充新增真实Worker结算。范围与最新结果以[恢复计划Phase10](../plans/plan-boss-resume-next-recovery-fix.md)为准，尚未提交、发行或部署。

**上一轮复审纠正历史（共同契约2.4，2026-10-20）**：已删除新增scope专用Windows重启见证和“先停止证明、再读页面”的门禁；只读页面观察首次即可执行，Python/sessionTasks配置不会禁用观察。导航和清除筛选复用既有稳定判定，筛选计数相同不冒充完整条件相同。界面分别展示页面事实与停止未知；迟到明确回执解除自身hold后，旧核验按钮按实际事实清理。本轮四项修正已通过独立验证：真实PG38项（首批有效36项加真实Worker夹具纠正后重跑2项）、额外实际跨端/兼容/DAL10项、前端76项，独立CR无未解决P0/P1/P2；开发者客户端与Web构建通过。仍不能称整套资源恢复已完成；未提交、发行或部署。

**2.4时的遗留（已由2.5替代）**：当时纯读不能解决旧state=unknown/result_json=NULL且缺逐调用代次关联的资源hold。2.5改以真实停止原Runtime受管边界核销精确集合，不需要硬补generation，也不需要系统重启。业务结果仍unknown，原认证迟到明确回执仍可独立对账；不自动重放未知发送。尚无历史受管证据的新home不能自造证明，真正未受管旧实例需先结束旧实例，这些属于覆盖边界，不能把页面可读当stop proof。

**旧v1验证仅作历史**：Phase9精确准入已通过独立回归；旧Phase10曾用同scope登记后的Windows重启见证并通过当时隔离测试，但复审确认其设计存在过度门禁，现已按2.4纠正。旧39项跨端、64项前端、3项原生等结果不能代替本次修正验收，也不能继续支持“可信停止闭环已补齐”的完成声明。

**修复进展（2026-10-20）**：用户优先授权的登录/设备错误分类、Worker切换后的迟到原调用对账、同会话结束等待三项已完成代码开发、独立测试和CodeReview，未提交、未部署。含普通取消被unknown阻断的出口；结束聊天不伪造设备停止证明。证据及限制见[恢复计划Phase 6～8](../plans/plan-boss-resume-next-recovery-fix.md)。慢输入与180秒执行截止仍待后续处理。下文保留诊断时事实与原始范围，不将代码验证冒充正式故障任务已恢复。

本报告供开发接手，**诊断阶段尚未实施修复**。除明确标注UTC的原始日志外，时间均为北京时间。诊断阶段服务器操作仅限读取日志、数据库与源码；数据库查询使用只读事务。当时只新增诊断文档并登记索引，不修改代码、配置、数据库、客户端或任务，不部署、不恢复、不重试业务操作，未向其他会话发送信息。

两起故障存在不同直接原因，不能合并成“都是未登录”或“都是180秒超时”：

1. **正式服**：云端把本地工具默认180秒写成持久调用截止时间，在客户端尚未返回时使 Runner 进入 verification。客户端实际到约235秒才返回 `EXECUTION_UNKNOWN`，具体异常是物理输入前的光标/目标窗口校验失败。这次停在筛选面板设置，未进入逐份读取简历。
2. **agent2**：首次已有明确的 `NOT_LOGGED_IN / effect=none`，随后出现 `DEVICE_UNAVAILABLE`。用户后续发起的另一任务在发布/worker切换时段中断，原本地调用没有结果，续租中断后被过期回收置为 unknown；同会话的继续消息持续尝试恢复该未知操作，被 `RECOVERY_TOOL_VERIFICATION_REQUIRED` 拒绝。登录成功不会自动补足这些执行事实。
3. **性能底层根因尚未查明；现有证据只完成热点定位和部分原因区分**：本次3次点击78.705秒、2次拖动54.990秒，合计133.695秒；连接仅10ms、7次快照合计1.570秒。年龄只有2次拖动，不能诊断成反复校准很多轮。Provider内尚有81.381秒未被现有叶子计时覆盖，调用层另有约13.784秒；具体慢在PowerShell准备、启动、编译、窗口探测还是其他环节仍需细分。后续交叉对照发现，使用PowerShell桌面检查的工具在Provider前也普遍多耗11—15秒，是优先排查PowerShell公共路径的线索，尚非子阶段实测。不得把最后一次光标异常当作此前235秒的完整解释，也不得把取消180秒限制当作性能修复完成。
4. **用户补充：客户重启Windows后速度恢复正常**。这是用户实测反馈，尚未取得重启后的对应perf、资源指标和版本对照。若期间未升级代码或改变任务条件，该现象支持客户机进程/系统状态导致的性能退化；不能单凭重启恢复判定内存不足、内存泄漏或某个进程有缺陷。重启属于临时恢复，性能根因仍待取证。
5. **后续正式服trace `tr_runner_d470a2a18e114be58ccbfe8823523e2a` 是另一类故障**：客户端两批各返回3份，前3份已完成识别/入库/匹配；第二批第1份（整体第4份）的GLM识别返回HTTP 400 / 1210“图片输入格式/解析错误”，被通用模型异常处理转为 `LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED`，使整任务等待核对。该请求图像可被Pillow完整解码；不能据供应商错误直接认定本地图像损坏，也不能将本次归为180秒超时。细节与修复建议见补充故障节。

## 证据范围

| 项目 | 正式服 agent | 测试环境 agent2 |
|---|---|---|
| Trace | `tr_runner_5941e6c15adf45a1b487e0209576ca1a` | `tr_runner_7df97d4956eb4b4d8c2da19bca229d57` |
| Session | `session_eee099ee6a49` | `session_ce0a9f54599d` |
| 业务库 / 追踪库 | `aid_work_agent` / `aid_work_logs` | `aid_work_agent2` / `aid_work_logs2` |
| 故障调用 | `5ba449f0-7801-4c5b-8ab3-b1275e8ce7af`，`boss_filter` | `634cfc47-55e0-4b29-adf6-35fe7de87a43`，`boss_goto` |
| 宿主机部署目录 HEAD（排查时） | `56e97343` | `f5a16776` |

上述 HEAD 是服务器部署目录事实，不等于故障客户机的插件版本。客户机已读到 Runtime 版本为0.2.19，但不能据此推断 BOSS 插件的全部源码与当前仓库相同。源码定位以下列路径为入口，开发应再核对实际安装 release/digest。

数据来源：`obs_traces`、`obs_spans`、`agent_runners`、`agent_runner_controls`、`local_tool_invocations`、`local_tool_events`、宿主机应用日志、容器启动时间及部署目录 reflog，以及用户补交的客户机 `runtime.log`。未读取配对凭据或输出认证令牌，未采集简历正文。

## 一、正式服：固定截止时间与本地慢操作叠加

### 已核验时间线

| 时间 | 事实 |
|---|---|
| 14:21:43.738 | Runner 接单 |
| 此后至14:24:23 | 页面跳转成功；列出职位约45.861秒、选择职位约40.445秒、探查筛选项约63.526秒；模型调用均 completed |
| 14:24:23.945 | 创建 `boss_filter` 本地调用 |
| 14:24:24.281 | 客户端调用进入 started |
| 14:24:38.057—14:28:14.717 | 保存68条进度，全部为 `execute 设置筛选面板`，只有1种 message |
| 14:27:23.922 | 持久 `deadline_at` 到期，约为创建后180秒 |
| 14:27:24.441 | Runner 进入 `waiting / verification`；错误码 `LOCAL_DEADLINE_VERIFICATION_REQUIRED` |
| 14:28:18.742 | 客户端返回 `EXECUTION_UNKNOWN`；调用为 `state=unknown / effect=unknown`，`retryable=false` |

从 started 到结果入库约 **234.46秒**，从创建到结果入库约 **234.80秒**。因此不是“前端等待180秒断开后任务停止”：后台本地操作在云端暂停后仍继续了约54秒。

Runner 公共 waiting 记录关联上述 invocation 与工具调用 `call_00_movKklaHHFGBdRBmXVCh4728`。本地结果包含 Provider run ID：`4aae55c2-cb25-4a85-ba0c-25094d2cc8fc`。

客户端错误要点：

```text
筛选状态已开始改变但未完成校验，禁止自动重试。
win-click.ps1 执行失败(exit=1)
RequireCursorOnWidget:
Cursor or render-widget ownership changed; input refused
```

这只证明点击前发现光标不在预期点，或目标点已不属于预期 render widget。**不能区分人为移动、窗口遮挡、窗口几何变化或绑定变化**；缺少现场坐标/窗口证据，不能认定用户动了鼠标。已有部分筛选是否生效也不能由失败码推断。

### 180秒来自哪一层

- `src/local_tools/proxy_tool.py`：基础 `LocalToolProxyTool.timeout_seconds=180`；`_poll_invocation()` 在 durable deadline 到期时抛出 `LocalContinuationRequired('LOCAL_DEADLINE_VERIFICATION_REQUIRED', ...)`。
- `src/local_tools/durable_flow.py::original_invocation()`：首次调用使用当前时间加 `proxy.timeout_seconds`，保存为原调用的 `deadline_at`；恢复时复用原截止时间。
- `src/services/agent_runner/runtime/local_tools.py`：上述异常投影为 verification wait。

这是**本地工具持久执行截止时间**，不是前端 SSE/订阅超时，也不是整段 AgentRunner 的 HTTP 请求超时。仅修改前端订阅或 HTTP timeout 无效。已只读核对正式服 `_poll_invocation()` 确有该分支。

### 235秒究竟慢在哪里：初轮证据与后续补证

已确认的定位边界：

1. 前置工具也有40—64秒耗时，性能异常范围可能不止最后一次筛选；不能只盯最后的180秒。
2. 68条相同进度不是68个业务步骤。`clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts` 的 `ProgressForwarder` 会周期性重发最近 stage/message 续租；它能证明上报通路活跃，**不能证明筛选持续取得进展**。
3. 真实调用参数包含经验、两个学历选项、薪资、活跃度、性别、跳槽频率，以及年龄双滑块。该参数组涉及多个真实输入和快照，不是一次简单点击。
4. 最后一处错误来自 Win32 物理输入路径，确认已到达客户端脚本执行阶段；不是一直没有领取任务。
5. 从 started 到首条execute进度约14秒，之后直到终态仍有约221秒。前14秒可能混有启动、连接和上报延迟，不能未经性能日志核对就全部记成 `connect_ms`；后面的统一心跳也不能分辨卡在某一步还是反复执行多个步骤。

源码给出的**性能嫌疑点，尚非本次实测根因**：

| 入口 | 已读到的实现 | 后续核验/补证项 |
|---|---|---|
| `operations/bossContext.ts` | 每次操作记录 connect/body/close，包装 snapshot、win32点击/拖动等计时 | 本次perf已补齐见下节；继续细分输入内部、body漏计及调用层等待 |
| `input/WinMouseClicker.ts`、`input/powerShell.ts`、`scripts/win-click.ps1` | 每次物理输入进入独立 PowerShell 执行；当前代码含临时安全目录准备、另一 PowerShell 设置ACL、脚本加载/编译、窗口探查与固定 sleep | 子进程准备/启动/编译/守卫/真实输入各自耗时与调用次数，不能只看总 click 时间 |
| `boss/FilterSetter.ts::applyOnce()` | 多行逐选项 fresh snapshot、可视区定位、点击，再 fresh 校验 | snapshot次数和单次耗时、节点规模、重复采集比例 |
| 年龄滑块 `setAge()` | 当前代码每端最多7次修正拖动，每次包含真实输入、650ms等待和快照 | 两端实际拖动次数、每次目标/观测值、是否无变化或来回振荡；此上限不代表本次确实执行14次 |
| 控件滚动与终态校验 | 当前代码有有界滚动、500/650/800ms等等待和验证；当前版本还可能进行一次有证据的后态重试 | 各步骤累计耗时、重试次数、失败发生在哪个行/哪个阶段 |

首次服务端排查时，结果只有失败信息和 run ID，没有上述性能明细；当时工作电脑的旧日志也未命中该调用。**这项证据缺口已由客户后续提交的原始日志部分补齐，实测分解见下节；不能继续将“没有性能日志”作为当前结论。**

取证定位标识如下，客户现已提供命中的原始日志，无需为相同证据继续重复导出：

```text
4aae55c2-cb25-4a85-ba0c-25094d2cc8fc
5ba449f0-7801-4c5b-8ab3-b1275e8ce7af
[boss-perf] tool=boss_filter
[boss-op]
2026-10-20 14:21—14:29
```

默认正式 Runtime home 为 `%APPDATA%/aidwork-tool-runtime`，验收版为独立目录；启动配置或 `AIDWORK_RUNTIME_HOME` 可覆盖，不能拿错目录。优先读取本次 run ID 的 `total_ms/connect_ms/body_ms/close_ms`、`win32:click/drag`、`snapshot`、`filter:panel:*`、`sleep:*`。这些计时有嵌套，不能将所有 total_ms 相加当作端到端耗时。若安装版本没有该行，应先核对 release，并在开发环境补可观测性后复现，不能在正式客户机会话自动重跑。

### 原始runtime.log补证：已确认的慢输入与未覆盖耗时

用户提交 `runtime.log`，文件57,885字节，SHA256为 `e014208889c67a65332963c3fef0777488fcfa5b0b1f618228a5379ce088424c`。本次关键证据在319—325行：319/320行领取与开始同一 invocation，321行为光标守卫失败，323行为本次perf，325行为同一 invocation 终态已回传。日志将Provider run ID脱敏成 `run_id***`，因此按**完整invocation ID与相邻调用边界**关联，不能声称文件内保留了完整Provider run ID。

本文件未命中agent2的7df trace或634c invocation；以下客户端性能数据只用于正式服这次故障，不能冒充agent2的现场日志。

| 本次指标 | 次数 | 累计耗时 | 解读 |
|---|---:|---:|---|
| Runtime“已开始执行”至“终态已回传” | 1 | 234.454秒 | 与服务端约234.46秒相符，运行并非只持续180秒 |
| Provider `total_ms` | 1 | 220.670秒 | `body_ms=220.409秒`，另有connect/close |
| `win32:click` | 3 | 78.705秒 | 平均26.235秒，最大27.848秒 |
| `win32:drag` | 2 | 54.990秒 | 平均27.495秒，最大27.536秒 |
| `snapshot` | 7 | 1.570秒 | 本次快照采集不是主要耗时，最大301ms |
| `connect` | 1 | 0.010秒 | 本次不是长时间连不上Chrome |
| 已记录固定sleep | 5 | 3.746秒 | 两次650ms类，加1200/500/400ms各一次；不能解释数分钟 |
| `cdp:mouseWheel` | 1 | 0.017秒 | 本次非大量滚动或慢滚动 |
| `close` | 1 | 0.251秒 | 关闭不是主要耗时 |

同一条323行还记录 `filter:panel:open=27.067秒`、`clear=25.851秒`、`option`一次27.848秒、`age:drag`两次54.990秒。这些阶段与底层click/drag重叠，**不重复累加**。本次只有2次年龄拖动，没有出现7次/14次滑块校准；此前源码允许的最大修正次数不是本次实际执行次数。

按互不重复的已列叶子计时计算：body 220.409秒减点击78.705、拖动54.990、snapshot 1.570、sleep 3.746、wheel 0.017，仍剩 **81.381秒**。这只是未归入现有计时的body时间，不能直接认定全是同步CPU、网络或某个函数。另有调用层234.454减Provider total 220.670，剩 **13.784秒**，包含Provider进入前后的未细分开销；不能全部算成浏览器connect。

日志时间为UTC。对应同一ID，客户机记录的started为 `06:23:47.373Z`（北京时间14:23:47.373），回传为 `06:27:41.827Z`（14:27:41.827）；服务端对应记录分别为14:24:24.281和14:28:18.742，相差约36.91秒，持续时间却一致。应按ID和各自耗时对齐，记录双端时钟偏移，不以墙钟差推断先执行后接单；现有证据不足以判断究竟哪台时钟不准。

### 退化对照与开发排查优先级

- 文件内10月15日样本：83行7次点击累计15.517秒，平均2.217秒；128行8次点击19.050秒，平均2.381秒，2次拖动合计5.808秒。
- 10月20日本次：平均点击26.235秒、拖动27.495秒，相比上述历史样本约为10—12倍。样本并非控制变量基准，不能据此认定某次发布是唯一原因；但足以确认慢输入在同一文件的多个调用中反复出现，非最后一次偶发卡顿。
- 同次前置工具也有对应证据：306行列职位单次点击27.851秒，311行选职位单次点击25.113秒，316行探查筛选项2次点击合计51.769秒。列职位还存在3次snapshot累计14.030秒，是前置工具的另一独立热点，不能把本次filter的快照较快泛化为所有快照都快。
- 241行另一成功filter的Provider total为405.756秒，10次点击225.125秒、2次拖动51.011秒，说明即便没有最终异常，输入链路仍可拖慢到数分钟；此样本不等于本次trace。

**开发现在可以按明确方向开始定位，不必继续要求客户重复提供已命中的runtime.log：**

1. 分开记录物理输入整段的脚本准备、额外PowerShell ACL进程、主PowerShell启动、Add-Type编译、窗口/进程探查、激活/绑定检查、实际SendInput与收尾。当前 `win32:*` 只给整段，不能进一步区分这些成本；单次约27秒不是正常的约1秒拟人移动/点击节奏。
   这些是外层调用耗时，可能包含子进程准备、执行、回调排队和线程/CPU竞争，不能解释成“鼠标物理移动本身花了27秒”，也不能未经测量就认定某个PowerShell函数独占全部时间。
2. 调用方20秒timeout只覆盖当前实现中的主脚本run，准备阶段另有15秒run及文件操作，不能据外层click大于20秒就认定timeout参数未生效。应以细分计时找瓶颈，保留输入绑定、安全目录和防盲点约束。
3. 给body未覆盖的81.381秒增加互斥分段或trace：筛选结构解析、逐行预校验、可见区几何计算、年龄值读取/验证、失败后核验，以及已安装版本其他未包装调用。源码确有重复结构解析和节点遍历，但尚无本次函数级测量，不能直接把81秒归因这些函数。
4. 给调用层未细分的13.784秒增加桌面锁等待、交互桌面检查、Provider就绪/调度等计时；本次Provider connect仅10ms，不能混淆二者。
5. 核对故障插件实际release/digest后做隔离A/B。代码历史中10月15日 `47f25c16` 引入/调整长路径脚本安全准备，10月18日 `c8e0c3b1` 调整绑定窗口输入；它们是对照入口，不是已证实的回归提交。原客户端报错行号与当前仓库脚本并不一致，不能默认装机源码就是master。

**当前闭环程度：主要热点已定位为Win32输入链路明显退化；底层子阶段归因与81秒漏计仍待开发性能验证。** 日志未保存这些更细指标，继续读取同一个文件也无法补出缺失测量。无需客户为了补证立刻重跑正式业务或执行新点击。

### 继续下钻：页面丢失、鼠标占用与PowerShell慢路径分别能确定什么

用户指出“点击耗时长”只是现象，该意见成立。本节补充源码条件及跨调用对照，**不将热点定位改称性能根因已查明**。

| 假设 | 已核验事实 | 结论边界 |
|---|---|---|
| 一直找不到页面或窗口，在等待它出现 | 本次连接10ms；已获取7次快照并进入筛选面板、清除、年龄拖动及选项操作。脚本中窗口不存在、render widget不存在/不可见均有独立的早期失败分支，实际错误来自更后的点击前光标守卫 | 不支持“整段180秒都在等页面出现”。不能保证期间页面从未变化 |
| 等待其他任务释放鼠标/桌面锁 | `legacy/invocationRunner.ts` 在 `withDesktopLock()` 内调用Provider；锁获取在Provider计时之前，`win32:click/drag` 位于Provider内部 | 外层桌面锁排队不能解释每次输入内部重复的25—28秒。仍可能影响Provider前的未细分时间；也不能由锁推断真实用户没有操作鼠标 |
| 用户抢了鼠标或其他窗口遮挡 | `BossPhysicalInput.RequireCursorOnWidget()` 将两类条件合并为同一异常：实际光标不等于预期坐标，或 `WindowFromPoint` 不等于预期widget；日志未保留实际坐标和命中窗口 | 确认最后一次输入前的条件不成立，无法区分人为移动、窗口移动/尺寸变化、遮挡、控件重建等；不能定性为“用户动鼠标”。该守卫直接抛错，不会循环等待20多秒 |
| 拟人移动或滑块反复调整本身花了数分钟 | 脚本移动10次×25ms、悬停500ms，拖动16次×25ms；面板固定sleep累计3.746秒，实际年龄拖动2次 | 源码显式等待和本次重试次数解释不了这些长耗时；Windows调用、线程调度与进程准备成本尚未分段 |
| PowerShell公共执行路径变慢 | 除输入链路慢之外，进入Provider前的延迟与是否调用PowerShell桌面检查高度对应，见下表 | 比泛称页面卡住更具体的排查方向，但尚不能确定慢在进程启动、C#编译、安全扫描、文件系统或Windows窗口调用 |

日志错误定位 `win-click.ps1:200`。仓库历史 `47f25c16` 的第200行恰为首次按下前的 `RequireCursorOnWidget()`，第201行才标记输入开始；该对应关系加强了“最后这次点击在按下前被拒绝”的判断，**不足以证明装机文件完整等于该提交**。当前master增加过窗口绑定逻辑，不能直接把新增的 `Get-CimInstance` 分支认定为此次慢点。

进一步对 `runtime.log` 的started和perf时间作差：`perf输出时间 - started时间 - Provider total_ms`。这是Provider前后边界的近似间隔，包含少量日志/调度成本，**不是已埋点的desktopCheck耗时**。下列时间保留客户机日志UTC，仅在同一日志内作差：

| 客户机started UTC | 工具 | Provider之外的近似前置间隔 | 源码是否先检查交互桌面 |
|---|---|---:|---|
| 10-15 09:58:36 | boss_select_job | 0.440秒 | 是 |
| 10-15 09:58:41 | boss_filter_options | 0.006秒 | 否 |
| 10-15 09:58:56 | boss_filter | 1.014秒 | 是 |
| 10-20 06:21:58 | boss_select_job | 13.424秒 | 是 |
| 10-20 06:22:40 | boss_filter_options | 0.002秒 | 否 |
| 10-20 06:23:47 | boss_filter（本次） | 13.699秒 | 是 |

10月20日其他选职位/设置筛选调用也重复出现约11—15秒前置间隔。源码 `legacy/providers.ts` 将select_job/filter归入write工具，filter_options不在该集合；`invocationRunner.ts` 仅write工具先调用 `desktopCheck()`。`legacy/desktopCheck.ts` 的实现是新启PowerShell，`Add-Type`编译P/Invoke，再调用 `OpenInputDesktop`；它不执行页面查找或鼠标点击。输入路径同样新启PowerShell并执行 `Add-Type`，并且还含另一个用于临时目录ACL设置的PowerShell进程。因此存在**两个位置独立变慢、共同涉及PowerShell启动/编译的交叉线索**。外层锁排队、调度或其他开销尚未单独计时，故不能把上表全部时间直接归入desktopCheck。

现有 `runtime.log` 丢弃了成功输入脚本的stdout，失败路径也只输出stderr。`WinMouseClicker.ts` 的外层 `win32:*` 计时没有记录以下分界：临时目录准备开始/结束、ACL子进程开始/结束、主PowerShell启动、Add-Type开始/结束、窗口查找、前台切换、光标检查、子进程退出/回调。末次守卫也没有分别记录两项谓词。因此现有文件**无法复原每次多出的二十多秒实际消耗在哪一个子阶段，也无法追溯光标失配的具体来源**。

给开发的下一步要求应是：先固定实际装机release/digest，在用户另行授权的隔离环境对上述分界计时；同步记录PowerShell及其编译子进程的启动/退出时间与CPU时间，若主要是等待再对齐Windows安全软件/磁盘/进程事件。光标失败只记录预期/实际坐标、目标widget与命中HWND及前台/几何变化，不采集简历正文。若多个PowerShell入口均在启动/编译前消耗十多秒，优先查进程/编译公共路径；若PowerShell已就绪、时间消耗在窗口激活或守卫前，则转查窗口线程与焦点。不能仅凭“可能被杀毒扫描”关闭根因分析。

本会话未执行新的物理输入、未重跑任务、未添加埋点或修改客户机；上述开发验证尚未发生。当前交接状态仍为：**停止原因已确认，异常缓慢的底层原因待取证**。

### 用户补充实验：重启客户机Windows后恢复速度

**观察来源**：用户反馈“让客户重启了他们的系统，速度就恢复正常”。本会话未发起重启，未独立复测；没有得到重启时刻、同一工具重启后的perf、资源曲线或前后版本清单。应记录为用户观察，不改写成已验证的性能修复。

**推断边界**：若重启前后版本和任务条件保持一致，重启恢复使排查重点进一步转向客户机进程/系统运行状态。重启同时重建Runtime、Provider、Chrome及Windows交互会话，并改变内存、进程、句柄和后台服务的状态，因此本次实验没有只隔离“内存释放”这一个变量；不能用它区分内存压力、句柄/线程异常、浏览器长期运行状态、PowerShell启动/编译路径或后台软件干扰。

结合此前PowerShell桌面检查与真实输入同时变慢的线索，开发应同时核对系统资源和PowerShell公共路径，而不是直接把根因改写成“Chrome内存泄漏”。正式服180秒持久截止、未知结果恢复和agent2恢复封锁仍是独立问题，不能因客户机重启恢复速度而认定已解决。

**下一次出现卡顿时的最小证据**（在重启或关闭进程前保留，不要求客户现在重跑有副作用的招聘任务）：

- 对齐慢调用的时间、invocation ID与perf，采集系统可用内存、已提交内存/限制、分页及磁盘延迟、CPU；单看内存使用百分比不足以确认内存瓶颈。
- 对Runtime/Provider、Chrome和PowerShell相关进程采集私有内存、工作集、句柄数、线程数与CPU/I/O；与正常时基线及持续趋势比较。高占用单个快照不等于泄漏。
- 按前节拆分PowerShell准备/启动、Add-Type、窗口操作耗时。若资源指标正常而某个子阶段耗时异常，按该子阶段继续定位，不能继续泛称“系统内存问题”。
- 已中断操作不自动重放；先保留诊断证据并核对实际结果。进程重启或系统重启作为后续隔离实验时须由用户安排，本会话不自行执行。

Windows资源判读参考微软[内存泄漏排查](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/using-performance-monitor-to-find-a-user-mode-memory-leak)与[分页文件性能说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/introduction-to-the-page-file)：结合私有内存随时间变化、可用/提交内存和分页活动判断，不能仅由重启恢复推出内存泄漏。

### 15:02客户机诊断摘要与后续日志定位过程（原始日志现已补齐）

用户补充 `Runtime诊断摘要-20261020-150233.json`，其 `collectedAtUtc=2026-10-20T07:02:18.5884232Z`，即北京时间15:02:18。只读检查确认该文件是约2KB状态摘要，不包含runtime日志正文、Provider run记录或 `[boss-perf]`，未命中本次run/invocation。

- 摘要为 `homeExists=false`，`deviceId/server/runningMarker=null`，错误为 `config-unreadable` 和 `operation-store-unreadable`。因此空设备信息和零目录计数不能作为未配对、历史已丢失或没有业务操作的证据；采集没有读到所需运行目录。
- 进程清单包含 `AID Work Runtime 验收版.exe` 及其node子进程，只证明采集时存在这些进程；不能据发行名称确认其连接的服务端，也不能反推14:24的具体执行状态。
- 仓库同字段结构的采集器 `clients/release/runtime-ui/runtime-diagnostic-summary.ps1` 固定读取 `%APPDATA%/aidwork-tool-runtime-acceptance`。应核对故障机实际home，而非将摘要里的缺失字段诊断成登录或DPAPI故障。不能断言用户此次使用的脚本与仓库字节完全相同。
- 下一份必要证据仍是故障机实际home的 `logs/runtime.log` 和 `logs/runtime.log.old.log`。优先检查 `%APPDATA%/aidwork-tool-runtime-acceptance/logs` 与 `%APPDATA%/aidwork-tool-runtime/logs`，保留覆盖10月20日14:21—14:29的文件。只需日志，不需要config、credentials、配对文件或整个运行目录；不要为取证重装、清理或重跑业务。

### 新版安装代次目录补充定位

用户补充：客户机存在 `%APPDATA%/aidwork-runtime-app-acceptance`，原acceptance目录日志只到10月1日。前述固定legacy日志路径不足以覆盖新版安装。

已读源码 `clients/agent-desktop/electron/runtimeInstallation.ts`、`main.ts`：Runtime产品启动时通过 `prepareRuntimeInstallation()` 取得安装代次home，再将该home传给supervisor。验收版现有指针位于 `%APPDATA%/aidwork-runtime-app-acceptance/installation-v2/current.json`，字段 `home` 指向 `installation-v2/generations/<安装UUID>/connections/<连接UUID>`。实际日志应在该home的 `logs` 子目录；连接切换后不能只看旧固定目录。尚未取得客户机current.json的home值，不能把某个猜测UUID路径当作已核验现场路径。

客户机可执行下列**只读**PowerShell命令，只输出实际日志目录，不读取或导出凭据：

```powershell
$rtPointerFile = Join-Path $env:APPDATA 'aidwork-runtime-app-acceptance\installation-v2\current.json'
$rtPointer = Get-Content -LiteralPath $rtPointerFile -Raw | ConvertFrom-Json
Join-Path $rtPointer.home 'logs'
```

从输出目录提供覆盖故障时段的runtime日志。若current指向的日志只覆盖后续连接，应只读列出 `installation-v2/generations/*/connections/*/logs/runtime*.log` 的路径、大小与修改时间，再选故障时间文件；不修改current.json、不切换连接、不调用安装初始化函数来取证。诊断导出工具也应跟随已存在指针定位实际home，并保留legacy路径兼容，避免新版运行正常但摘要误报home缺失。

用户再次明确：`C:\Users\hr\AppData\Roaming\aidwork-tool-runtime\logs\runtime.log` 最晚只到10月10日。该文件不能用于10月20日故障，不应再要求重复查找或上传它。下一步只列出 `aidwork-runtime-app-acceptance` 内各安装代次/连接的日志路径、大小、修改时间，不再以旧固定目录为当前运行目录。

源码还显示一项可观测性风险：Provider stderr通过 `legacy/providerManager.ts` 的 `logProviderChunk()` 应落实际Runtime home日志；但 `legacy/log.ts` 的文件写失败会静默降级，而Electron `runtimeSupervisor.ts` 仅消费Host stdout/stderr、不保存这些原始输出。因此不能依赖壳层stdout兜底还原历史性能日志；需验证实际代次home是否有文件，不能仅凭旧日志过期断言本次一定没有落盘。开发应同时检查日志落盘失败的可见告警及当前home感知的导出能力。

## 二、agent2：登录前置、设备状态、中断及恢复封锁

### 已核验时间线

| 时间 | 事实 |
|---|---|
| 14:12:24.690 | 同会话前一 Runner `runner_b4056e0eb9874b77884839de16ce8a08` 接单 |
| 14:12:27—14:12:35 | `boss_goto` 调用 `3ce0f6da-f1bb-4396-b1e4-7b7fa13a0425` 明确失败：`NOT_LOGGED_IN / effect=none` |
| 14:12:40—14:32:37 | 原任务等待 `ask_user`；这约20分钟是等待用户，不应算成浏览器执行卡顿 |
| 14:32:38 | 后续 `boss_goto` 约544ms失败，追踪输出码 `DEVICE_UNAVAILABLE`；没有创建新的设备 invocation |
| 14:32:40.599 | 前一 Runner completed；截图此时助手引导启动/配对 Runtime |
| 14:33:58 | agent2 部署目录 reflog 记录已有更新到 `f5a16776` |
| 14:34:22.501 | 用户“再看下”对应本次 Runner `runner_7df97d4956eb4b4d8c2da19bca229d57` 接单 |
| 14:34:23.901 / 14:34:24.215 | 创建 / 开始本地 `boss_goto`，invocation为 `634cfc47-55e0-4b29-adf6-35fe7de87a43` |
| 14:34:25.625 | 只有一条 `connect` 进度；截图显示连接本机 Chrome 调试端口 |
| 14:34:37.164 | 本次 trace/span记录中断，`boss_goto` span约13.324秒，Runner为 interrupted |
| 14:35:10.867 | 当前 agent2 worker 容器启动；RestartCount=0只说明当前容器自身未重启，不能否认旧容器被重建 |
| 14:35:25.625 | 该设备 invocation 的60秒租约到期 |
| 14:35:29.295 | `expire_stale_claims` 日志确认1条过期调用被置 unknown；该调用 `state=unknown / effect=unknown`，`result_json=NULL`，error_code为空 |
| 14:39:25.211 / 14:42:45.826 | 两次 resume 均 rejected，错误码均为 `RECOVERY_TOOL_VERIFICATION_REQUIRED` |

### 因果与证据强度

- **确认**：最初的未登录已有结构化错误，应该明确提示登录，不能当作一般运行失败。后来一次 `DEVICE_UNAVAILABLE` 是另一类错误，不能因为此前未登录就覆盖成未登录提示。
- **确认**：本次7df trace没有收到可用的成功/未登录/失败结果；最终 unknown 来自租约回收，并非客户机明确返回了 `EXECUTION_UNKNOWN`。原调用最后一条心跳之后约60秒失效，不能把 unknown 推断成仍未登录。
- **高置信推断**：代码更新、13秒后任务中断和新 worker 启动的时序一致，发布/worker切换很可能参与了中断。旧容器对应时段 stdout 已不可从当前容器取得，未捕获该次 owner task 取消/LeaseLost 的直接异常；不能把发布写成已被完整堆栈证明的唯一原因。客户端为何停止续租/没有最终回执也待故障机日志补证。
- **确认**：`src/local_tools/repository.py::expire_stale_claims()` 会把过期 claimed/running 置为 unknown/effect=unknown，当前字段和同秒日志与此一致。

### 为什么登录后同会话仍继续不了

1. `frontend/web/composables/useAgent.ts::sendMessage()` 发现同会话 parked/interrupted Runner，便把后续输入发送为原任务的 resume/control，而不是普通新轮次。
2. `src/services/agent_runner/recovery.py` 要求未完成工具先通过原执行事实恢复校验。
3. `src/services/agent_runner/local_recovery.py::LocalRecoveryProof.can_restore()` 发现原 invocation 的 state/effect为unknown，返回False，恢复被拒；不能靠用户说“登录了”证明旧调用效果。
4. 前端将多种 control拒绝统一显示为“任务暂时无法继续，补充消息和附件已保留，但尚未应用”，缺少原始原因和可执行处理入口。后续发送再次走同一恢复路径，形成会话交互封锁。

用户另开会话后可以继续是用户提供的现象；本报告没有读取该新会话或验证其实际业务结果。新会话可能绕开旧任务的会话占位，**不等于旧未知调用已被正确核对、结束或解决**。

## 补充故障：重启后读取6份，GLM图片识别拒绝导致整任务暂停

### 身份、现态与时间线

- Trace：`tr_runner_d470a2a18e114be58ccbfe8823523e2a`；Runner：`runner_d470a2a18e114be58ccbfe8823523e2a`；会话：`session_bfdc2a8f2edb`。
- 正式服部署目录HEAD只读结果仍为 `56e97343`。本次相关容器源码 `local_owner.py`、`resume_vl_service.py`、`resume_flow.py` 的SHA256与本地已读文件一致，以下错误转换链已核对对应版本。
- 查询时Runner为 `waiting`、attempt=2，`pause_requested=false`、`cancel_requested=false`；最近更新时间16:41:54.773661。waiting.kind为verification，error_code为 `LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED`，tool_call_id为 `call_00_NM158ezzs7tYAE6nybCg1835`，waiting.invocation_id为空。

| 北京时间 | 已核验事实 |
|---|---|
| 16:35:15.266 | Runner接受任务 |
| 16:35:43—16:35:46 | 选职位设备调用成功；工具span约4.131秒，较前次约40秒显著缩短，但不是每次点击内部的实测计时 |
| 16:36:02.896—16:37:21.014 | 筛选设备调用成功，工具span约78.271秒 |
| 16:37:22.780—16:38:57.701 | 第一批 `boss_resume_batch` 成功返回3份，attempted=3、stop_reason=limit_reached、failures=0 |
| 16:38:58—16:39:31 | 第一批3次GLM识别成功，分别约8.805、9.192、14.109秒；三份的evaluation、fee、insert、match阶段均completed，insert结果applied=true |
| 16:39:34.053—16:41:23.203 | 第二批 `boss_resume_next` 设备调用成功返回另外3份，attempted=3、stop_reason=limit_reached、failures=0 |
| 16:41:23.883 | 第二批第1份进入 `resume.evaluate / ordinal=0`，调用zhipu / GLM-5.3-Flash |
| 16:41:54.272 | Provider日志记录约30.369秒后收到HTTP 400，body为 `{"error":{"code":"1210","message":"图片输入格式/解析错误"}}` |
| 16:41:54.774 | Runner进入verification；该模型阶段停留started，usage receipt为unknown；没有本次第二批的evaluation/fee/insert/match完成事实 |

**“看了6份”的准确含义**：客户端已分两批取回6份图片，但只有前3份完成云端识别、入库和匹配；后3份的云端处理在首份即停止。失败对象是整体第4份，不是已成功入库6份之后再失败。两个客户端调用均succeeded/effect=none，本次没有证据表明再次卡在物理输入或设备租约。第二批工具span约140.935秒由约109秒客户端读取加约30秒模型错误等环节组成，未触发原180秒截止码。

### 图片是否真的损坏

只读解析Provider实际请求日志 `/app/log/llm/llm_invoke_logs_20261020.jsonl`，未导出图像、候选人姓名、简历正文或任何凭据，也未重新请求供应商：

- 本地日志request_id：`dda8a7fd646d45db`，为失败调用的本地生成ID；不能冒充智谱服务端返回的request_id。
- 请求含1张 `data:image/png;base64` 图片；严格Base64解码成功，326,556字节，PNG / RGBA，774×1767；Pillow的PNG校验与完整像素加载均成功，alpha范围为255—255。
- 该图SHA256：`8e32d7b7c5dc7acbd5738d4d68b64eb603009a0e38ec5d400a616c88c64d22db`。
- 前3份成功请求也都是单张PNG / RGBA，尺寸分别778×1748、774×1032、774×1161，均能校验；不能仅凭RGBA模式将其定为本次原因。

因此已确认的是**智谱侧拒绝解析该请求图像**，并非已证明本地PNG损坏。供应商内部解码/预处理异常、特定图像兼容性等仍需进一步证据；本会话未进行收费重放、换模型、图片转码或重新发送简历。后续若做原图/RGB重编码对照，需另行授权并保护候选人资料；对照结果产生前不得声称某项编码调整已经修复。

### 为什么一个识别错误变成整任务暂停

1. `src/local_tools/resume_flow.py:64` 通过 `owner.model_phase(branch='resume.evaluate', ...)` 执行每份简历模型调用。
2. `src/services/agent_runner/local_owner.py:262` 将普通调用异常统一转换为 `LocalContinuationRequired('LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED')`，未在此区分本次已明确返回的HTTP 400拒绝与无响应/执行结果丢失。
3. `src/services/resume_vl_service.py:283` 检查到 `tool_continuation_required` 后直接抛出，不进入该函数通常的第2次尝试，也不转成 `ResumeVLError`。
4. 因此 `resume_flow.py` 的单份 `RESUME_VL_FAILED` 业务返回没有接住这次异常，整项Runner被挂起。当前模型阶段started且已存在unknown receipt，直接重复恢复也不能被当成一个从未发出的模型请求。

简历识别使用 `llm_gateway.chat_direct(zhipu, GLM-5.3-Flash)`，不走主聊天模型的跨provider failover；主聊天DeepSeek工具规划成功，不代表该专用视觉通道成功，也不能直接将视觉请求切到纯文本模型。

### 后续用户输入“继续”为何没有继续执行

用户确认是在原会话继续发送消息，本次继续沿原会话查询，不要求用户提供另一trace。只读查询结果：

- 原Runner仍是waiting / verification、attempt=2、更新时间16:41:54.773661，没有后续执行attempt。
- `agent_runner_controls` 仅有16:35:30.110082受理并于16:35:30.564668消费的初始澄清reply；暂停后没有已受理或rejected的resume/reply控制记录。不能把本次写成“已经查到后台拒绝了一条继续控制”。
- 同一Runner的简历模型调用仍只有前3次observed和第4次unknown，未新增识别调用；原会话只有这一个Runner。

进一步只读检查正式API容器的实际静态资源 `/app/frontend/dist/assets/useAgent-Da8tXAC7.js`，SHA256为 `7e3051104e433452b015c4263ad4b1fa2f14065b8b19bef3df8f37292501285b`，确认其中存在发送时的parked guard：当原任务仍waiting时，返回 `RUNNER_ASSISTANCE_PENDING`，显示“请先完成原任务的人工协助，本次输入已保留。”并 `restoreInput=true`，不提交新消息或控制。部署目录HEAD `56e97343` 对应源码 `frontend/web/composables/useAgent.ts` 的判定条件为：只有clarification可reply，只有paused/interrupted且没有waiting本体可resume；现场的waiting / verification两项都不满足。

因此，**已部署代码对现场状态的分支会在前端拦住普通“继续”输入**，与没有新控制/模型调用的后台事实一致。用户实际发送时刻及其浏览器HTTP轨迹未取得，不能虚构某次POST的拒绝时间或状态；当前证据也不能声称这条后续消息已经持久写入Runner上下文。“输入已保留”在此指前端恢复输入，不能等同于服务端已受理。

后端还有独立的恢复阻断：`domain_recovery.py::_model_proof()` 对started模型阶段要求该phase不存在usage receipt才允许作为“未发出”恢复；现场已有unknown receipt，因此该判断返回False，并会使上层恢复检查报告 `RECOVERY_TOOL_VERIFICATION_REQUIRED`。这是源码与保存状态推导的恢复结论，本会话没有发送新的resume来验证，也未绕过该保护。

前端把云端图片识别失败归到无法通过普通输入解除的人工协助等待，后端又缺少对明确400的单份失败收敛，构成两层缺口；用户再说“继续”不会自动重新识别或跳过失败项。

### GLM有无重试，以及这张图是否必然失败

- **存在重试设计，但本次未生效**：`resume_vl_service.evaluate_resume()` 是最多2次尝试，可处理普通调用异常、空输出或不合法JSON；本次异常在 `model_phase` 中被转成 `LocalContinuationRequired`，其 `tool_continuation_required=true` 使外层直接抛出，跳过剩余尝试。不能笼统声称完全没有重试，也不能声称本次已经自动重试过。
- **同图只有1次实际请求证据**：逐条只读核对当天已保存的智谱Provider请求，对与失败请求完全相同的image data URL做匹配，仅找到16:41:54这一条1210失败。该Runner账本同样没有后续视觉调用；“继续没有结果”不代表同一张图已多次重试失败。
- **暂不能区分偶发拒绝与特定图片兼容性**：本地可完整解码仅证明PNG基本有效；不同图片的前3次成功也不能证明这张图一定能被供应商解析。没有同图重复请求或有控制变量的编码对照，不能判定供应商偶发故障，也不能判定该图必坏。这里保持未决，本会话未重发原图、调用备用模型或进行任何收费实验。

### 给开发的修复建议

**2026-10-21 产品纠正**：下面的人工“重试该份/跳过该份”选项建议已撤销。批量筛选单份无法查看或识别应后台自动跳过、继续剩余份；不新增前端交互。开发与验证以[单点治理计划](../plans/plan-recruiting-error-containment-and-input-performance.md#2026-10-21-产品纠正单份失败后台自动跳过)为准。以下保留当时诊断建议。

- 按供应商结果分类：对有明确响应的1210图片拒绝保留原码与描述，形成可审计的单份识别失败事实；按产品策略跳过该份或让用户选择处理方式，不应只显示笼统“当前操作结果需要核对”。网络无响应/结果未知与设备外部写动作仍按各自证据保护。
- 检查 `model_phase` 与简历业务错误的边界，避免已明确拒绝的单份识别把整批永久阻塞；模型计费/响应账本仍按证据维护，不能为了继续任务伪造observed或成功结果。
- 恢复应消费第二批原invocation已保存的3份结果及逐份处理事实，保留前3份已入库记录，禁止重新点击/抓取前6份来绕过状态或重复收费。任何新的视觉调用须有独立可追溯尝试身份和原业务授权。
- 增加不含简历内容的图像格式、模式、尺寸、字节数、摘要及供应商错误分类诊断；验证明确400、网络无响应、返回内容不可解析三类路径，分别断言单份状态、剩余批次处理和计费边界。图像兼容性修复需先有原图对照证据，不直接按猜测缩图或截掉简历内容。
- 修正verification续聊入口：展示真实的云端识别错误及“重试该份/跳过该份/结束任务”等经授权的处理选项，明确输入是否已受理；覆盖waiting / verification下输入“继续”的前后端组合，避免只修后端但前端依然不发请求，或只放开前端却持续撞后端恢复保护。恢复和新模型尝试须有明确授权及账本，不允许简单删除receipt或把unknown伪造为成功。

本次仅只读取证并补充本文，未修改代码、服务配置、任务或业务数据，未恢复、取消、重试或部署。

## 三、修复建议与开发边界

2026-10-20开发接手：用户授权优先实施登录/设备错误分类、Worker切换后的原调用对账、同会话恢复死结三项，设计与实时进度统一见[恢复计划Phase 6～8](../plans/plan-boss-resume-next-recovery-fix.md)及[Runner增量设计](../system/agent-application-architecture-design.md#56-本地调用对账与同会话结束等待2026-10-20增量)。本报告保留原诊断事实；性能与180秒问题不属于该批开发，验证完成前不勾选对应验收项。

下列为诊断阶段建议；其中本批三项恢复改进已完成代码与隔离验证，详见顶部修复进展及关联计划，尚未部署。性能证据与执行期限仍待后续处理，不能将本批恢复改进当作这些问题已修复。2026-10-20 追加：性能取证、180秒截止拆除、GLM 1210 单份失败分类、输入链路原生化及可观测性缺口等剩余待办已登记专项开发计划 [招聘链路单点错误治理与 Win32 输入性能修复](../plans/plan-recruiting-error-containment-and-input-performance.md)。

| 优先级 | 建议 | 不能采用的替代做法 |
|---|---|---|
| P1 | 已取得本次perf：优先细分和优化单次约27秒的Win32输入，补body的81.381秒和调用层13.784秒漏计，再给出最大耗时子阶段及前后对比。连同前置40—64秒工具一起排查 | 只把180改600/无限等待就宣布“卡顿修复”；将本次仅2次拖动误报为年龄反复校准；把全部耗时归给某个未测函数 |
| P1 | 去掉以工具默认180秒作为健康后台任务总时限的语义。Runner观察与执行解耦，前端离开/订阅重连不结束任务；用持久原调用及活动续租观察完成，并兼容已保存旧deadline的任务 | 仅修改前端HTTP/SSE超时；忽略既有持久deadline；取消租约、身份校验、显式取消或效果未知保护 |
| P1 | 将“设备在线/续租”与“业务有进展”分开记录；报告当前步骤、最近语义进展、实际无进展时长。无进展应告警和诊断，不默认重放或继续盲点 | 将重复同一句心跳当作业务推进；用户只见泛化的“设置面板”数分钟 |
| P1 | `NOT_LOGGED_IN / effect=none` 显示明确登录入口/指引，并支持登录后在同会话校验原设备/原浏览器目标再进行合法新尝试；`DEVICE_UNAVAILABLE`另行解释 | 只改toast文案；重新配对或换设备被当作登录操作；未登录也进入不可解除unknown |
| P1 | 发布/worker退出时保留原调用归属和已受理事实，迁移后能继续观察活跃调用；对迟到回执持久保存诊断/对账证据，避免无结果直接成为永久恢复死结 | 重建worker就重发桌面动作；把“已发取消”当作“确定无副作用”；直接SQL清占位/改unknown为成功 |
| P1 | unknown按工具效果分层：BOSS导航/筛选页面状态先做受权只读核对，允许用户明确选择继续或结束原任务并在同会话开始新意图；外部打招呼/发消息等仍须严格证据和去重 | 将所有unknown一律safe_to_retry；为了聊天可继续而删除原执行事实或绕过共享Runtime授权 |
| P1 | 同会话恢复拒绝要呈现原因、当前事实、可选处理动作；普通聊天与恢复控制清晰区分，防止每条消息都撞同一rejected恢复 | 让用户只能新建会话；在原任务仍可能执行时偷偷创建并发副作用任务 |

遵循 [Runner / Desktop / Runtime共同契约](../system/runner-desktop-runtime-integration-contract.md)：任务权威仍是AgentRunner；不能新增本地Agent loop，不能因修复恢复交互更换原任务设备/目标，也不能自动重放效果未知的外部写动作。

相关旧方案：[BOSS简历恢复修复计划](../plans/plan-boss-resume-next-recovery-fix.md)。该计划处理过恢复证明和unknown提示，但不是本次故障已经修复的证据。开发接手后需核对已交付设计、补本次变更计划/接口兼容与验收登记，再实施代码。

## 四、验收与交接清单

- [x] 已补齐故障机本次runtime/perf并定位已测最大项：Win32点击与拖动合计133.695秒，占Provider body约60.7%；按原invocation边界完成关联。
- [ ] 继续细分单次约27秒输入成本，以及body未覆盖81.381秒和调用层13.784秒；在子阶段归因完成前不关闭“性能根因待定位”。
- [ ] 同参数在隔离授权环境复现，按冷/热启动区分PowerShell与浏览器成本；记录点击、拖动、快照次数及滑块是否反复修正，给出明确性能目标及前后对比。
- [ ] 模拟超过180秒但有有效执行归属的操作，Runner不因旧固定总时限进入错误验证态；前端断开再订阅不取消或重发；旧checkpoint/deadline兼容得到测试。
- [ ] 覆盖故障机本轮年龄双端校准、页面位置变化、光标/遮挡变化：不盲点、不报假成功，有准确的阶段提示和原始故障码；不可简单删除输入守卫。
- [ ] 未登录→`NOT_LOGGED_IN/effect=none`→明确登录提示→登录后原会话继续成功；另测设备不可用，不能混淆两类错误。
- [ ] 调用执行中切换worker：成功/明确失败迟到回执能与原调用核对，身份/目标/意图不可漂移，不创建重复调用；彻底失联仍诚实保留unknown。
- [ ] 重现本次unknown+两次resume rejected，用户在原会话有可执行的核对/结束/后续聊天入口；保留补充文字、附件、历史和审计，不靠删记录恢复。
- [ ] 外部效果未知与页面状态可核对的恢复分支分别验收，前者不因本次便捷恢复被自动重放。

本报告不等同代码修复、测试通过或部署完成。除新建本报告和索引登记外，工作区其他既有改动均未处理；正式服与agent2任务均未被本会话恢复、取消或重跑。
