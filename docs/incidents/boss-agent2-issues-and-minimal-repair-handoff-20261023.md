# 2026-10-23 BOSS智能体问题汇总与最小修复交接

本文是今天agent2招聘问题的**统一开发交接入口**，包含事实、修改范围、实施顺序和验证要求，无需按截图各修一套逻辑。详细取证、调用ID和证据边界已并入本文；被替代的独立诊断文档合并删除。

状态：用户已确认最小方案并授权开发，允许真实超屏时原窗口一次归位后重新截图和定位；本次Phase 9/10/11已完成开发，独立467项回归及CodeReview通过，详细记录见[原计划§16](../plans/plan-runner-local-tool-recovery-lockup-fix.md#16-本次最小修复实施与独立验证2026-10-23)。修复`e44830e2`已提交推送，配套Runtime0.0.26/BOSS0.3.24已组合打包验包；详见[发行记录](../plans/plan-runner-local-tool-recovery-lockup-fix.md#17-本次提交与-runtimeboss-组合包2026-10-23)。晚间用户安装后已真机连续续读15份，非空筛选失败，根BODY裁剪遗漏已实证，见[§6.9](#69-本机php职位连续多轮验收2026-10-23晚间)；该增量及安装时发现的Host16根上限均已修，分别通过独立169/29项及CR；Runtime0.0.28已自主安装，BOSS0.3.26待原生导入，详见[原计划§19](../plans/plan-runner-local-tool-recovery-lockup-fix.md#19-根滚动视口修复与自主更新验收2026-10-23)；未部署服务器。新增[跨轮调用核查](#67-跨轮调用专项核查2026-10-23只读调查)与[客户Runtime原始日志核验](#68-客户runtime原始日志核验2026-10-23)，后者补齐窗口截图失败原因和四次“3+2份后误配推荐”的时序。部分既有修复已完成开发，见下表；开发前核对当前源码及实际运行版本，不重复实现。进度唯一登记在[原恢复开发计划](../plans/plan-runner-local-tool-recovery-lockup-fix.md)的Phase 9/10/11及既有Phase 8验证记录，不另建开发计划或恢复架构；最小新增范围及实现记录见原设计W4。

## 1. 目标与取舍

**局部页面异常只影响局部对象；程序应自己恢复页面、继续剩余工作，不能让一个广告、陌生元素、离屏控件或单份失败锁死整项任务。**

- 推荐牛人是混合列表。按同一卡片内的姓名和候选动作识别人；广告、推广、提示或无法配对的项目跳过自身，继续后续卡片。不要求完整理解所有插入项，不将某个分区标题作为列表终点或排除其后全部候选。
- 读取失败可换fresh定位、重连CDP，必要时按已有原profile恢复浏览器/页面。保留当前项、半批资料、已入库去重和剩余数量；完成数不等于尝试数，不从零重做整批。
- 筛选/读取/批准名单打招呼在推荐牛人页；后续聊天、收附件简历、面试、不合适在沟通页。用已有URL与导航，不建页面授权系统。
- 不盲点错误对象。单人确实无法定位或单份确实不可读，记该项失败并继续其他可处理对象；定向招呼只处理已批准名单。真实发送未知只核对该人的结果，不把整轮都标未知，也不重复发送。
- 真正未登录等需要用户完成的依赖准确提示，完成后在原会话接续；不能要求新开会话。持续恢复失败时保留进度、准确报告未完成项，仍允许原会话后续重试，避免无限空转。

本批不新建Agent、调度器、通用识别引擎、候选身份系统、事务回滚协议、授权/证明账本或Device wire。复用已有结构提取、checkpoint、BusinessOperationState、PageExecution和恢复入口；不靠扩大超时、吞掉全部异常、取消输入守卫或增加UI文案黑名单解决问题。

## 2. 今日问题与已确认事实

所有trace均为`tr_runner_`前缀，下表保留完整ID便于检索。时间为北京时间。

| 问题 | Trace及事实 | 当前结论/状态 |
|---|---|---|
| 页面正常但续筛被拒绝 | `tr_runner_2afd4ac171aa4de4be4bd52945113618`，11:39；两次`render widget hidden; input refused`；网页诊断无阻挡、目标已满足，窗口诊断失败 | 原生窗口探测与恢复覆盖不足。既有Phase 8已完成开发，0.0.25/0.3.23已出包，客户业务验收仍未证实；勿重做整套恢复 |
| 筛选后给批准两人打招呼失败，转沟通仍失败 | `tr_runner_9984c8fd53054e4f9fd3c865ac383622`、`tr_runner_386f2626342d4ed69754f974e2fc0364`，14:30～14:35；三次分区解析异常被包装成INTERNAL_ERROR/unknown；模型显式goto(chat)核查失败 | 原调用在正确推荐页；共享解析过严、发送事实丢失、搜索后验薄弱，不能说已经发送或一定未发送 |
| 提问卡片下方重复三个岗位标签 | `tr_runner_fad11fadee904df98a5851c0a5bc4880`，15:29；来自工具options→旧quickOptions展示链，非模型正文 | 保留ask_user卡片、去掉重复标签。关联计划Phase 4已完成旧快捷选项整体退役；核对当前实现，补回归而非恢复另一套局部过滤 |
| 读了5份附近误点顶部“推荐”刷新 | `tr_runner_dbdfad2bdfa04818b0f829d2de8bb776`，15:49～15:56；第二批真实回执`name=推荐 / target_label=推荐 / input_status=attempted`；两批入库3+2份，未调用goto | 全iframe同行姓名配对把UI标签当人名；本地真实函数合成复现。后续另叠加光标校验拒绝/隐藏widget；没有“每5份刷新”的固定规则 |
| 中间推广项使续筛提前停止 | `tr_runner_50c38a7ab03c4b54a971c45e617a2b54`，15:57～16:01；先入库3份，再保留2份并报page_operation_failed，第三次分区解析异常；没有真实列表终态回执 | 与招呼失败共用解析链。截图推广项之后仍有普通卡；AI“后面全不算筛选结果”的解释超出工具证据 |
| 放宽性别后卡在筛选面板 | `tr_runner_059ae40d76284c0ba0e0c48629345d2e`，16:05已收到“性别不限、再读6份”；三次filter空clip错误；网页诊断称面板可见，与报错不符；恢复失败 | clip算法与编辑阶段恢复有缺口。年龄16/经验学历不限，不能凭徽章7或effect=none宣称原条件未动；未成功提交新条件，也未完成6份 |

精确证据与限制：

- [混合列表、发送事实及沟通绕行](#62-定向招呼与沟通绕行)：未知插入项触发哪个DOM/class，现场尚未保存；共用全局抛错/排除路径已从源码确认。
- [“推荐”标签误配](#64-顶部推荐标签误配)：早前回执与合成样本确认机制；新增客户Runtime日志已确认四次均在前批3份、本批2份后滚动、fresh读取并误配“推荐”，详见§6.8；不能推广为每种布局每5份必触发。
- [筛选空clip](#65-筛选面板空clip)：合成验证“内部内容离屏使clip为空、真实scrollport仍存在”；现场具体裁剪祖先尚未确定。源码与截图强烈指向清除草稿后、年龄设置前中断，不能当作已提交清空列表的证明。
- [原生隐藏窗口](#61-隐藏渲染窗口)：后续trace出现同形拒绝，不足以证明该调用正在运行哪个新包；光标差异也不能直接归咎于用户移动鼠标。
- [提问卡片/旧快捷选项计划§8](../plans/plan-ask-user-interactive-question.md#8-2026-10-23旧版快捷选项整体退役)：已完成代码与未部署/未真机验收范围分开。

缺少某次现场DOM不妨碍修复已复现的算法缺口；不要把收齐所有广告类型、每个HWND或全部历史日志变成开工/合并的前置工程。仍不把合成样本冒充现场底因。

## 3. 最小代码修改包

### A. 共用候选提取：一次修读取和招呼

落点：`cardName.ts`、`RecommendationResultBoundary.ts`、`LoadedCandidateNavigation.ts`，及`ResumeBatchReader.ts`/`GreetExecutor.ts`消费处。

1. 复用最近候选卡祖先、卡内姓名和动作结构，让姓名与按钮来自**同一卡片**，并保留节点来源用于定位。废弃从全iframe按同行/最靠左中文猜姓名的路径；首次点击也必须来自合法卡片，不能索引未命中后仍点任意“姓名点”。不以新增全局ID、头像指纹或额外审批作为本补丁前提。
2. 陌生插入项不再全局抛错，不将其后所有card-item排除；缺合法配对只跳过该项。不能只删除GreetExecutor入口检查：普通按钮/索引也会间接经过`excludedRecommendationNode`，必须修通共享链。
3. 复用iframe裁剪、固定头部和已有滚动。离屏/被头部覆盖的普通卡片先滚入，fresh定位后再点；目标未读不算完成，不因一次定位失败永久排除。只在真实列表后态确认无更多可处理候选时结束，不靠“未认出分区”推断耗尽。
4. 批次按实际成功成果计算剩余量；一份失败继续其他候选，已完成项复用既有识别/入库/计费事实。定向招呼自动在原列表上下找回批准对象，必要时回顶部；一人找不到不阻止另一人，已联系不重发。不无条件重设筛选、不默认转沟通绕行。

### B. 筛选面板：真实scrollport与编辑接续

落点：`FilterSetter.ts`、`bossFilter.ts`，必要时调整`localOperationRecovery.ts`对筛选阶段的消费。

1. 分开**驱动滚动的真实scrollport**和**目标当前可见区域**。内部随动hidden分组/目标离屏不代表面板不存在；从真实scrollport内发滚动，fresh后按真实裁剪判断能否点击。不能简单删除overflow保护、把clip扩大到全屏或盲点旧坐标。
2. 面板可见而几何暂时不可用，复用原重试/取消预算，等待fresh快照，必要时收起重开再定位，继续原spec；不是让用户F5或无限重复同一失败调用。局部条件确实不可用，沿已实现的部分筛选规则跳过并说明，继续可匹配条件及后续读取；所有条件均不可用则不清空原条件，按已知实际状态继续并说明，不能虚报已应用放宽条件。
3. 复用现有阶段记录，区分未开始、已改草稿和已提交。尽量在清除前确认操作需要的面板/控件定位；中断后按原替换语义重设，核验实际选中值/年龄及提交结果。不得把旧徽章或none当成“完全没改”。不为此新建整页快照和事务回滚框架。
4. 编辑中面板展开是正常状态。恢复观察针对“让原面板可操作并继续选值”，最终完成仍由领域后验判定；不能用最终面板应关闭的要求把它当障碍dismiss，也不能页面无障碍就机械转失败window诊断。

### C. 错误事实与已有恢复：补通，不重建

落点：已有业务错误封装/`BusinessOperationState`、`localOperationRecovery.ts`；必要时修`ChatSearchExecutor.ts`。

- 保留每人真实not_started/confirmed/unknown/partial及已有成果，避免普通解析Error丢失阶段后被统一包装为发送未知。真实未知发送先观察该目标结果；不能将所有unknown改none，也不让单人的unknown冻结其他独立工作。
- 搜索输入后验从目标搜索框实际值和对应结果判断，不靠全页独立单字存在；未查成功不等于未发送。明确未发送的招呼仍在推荐页找回目标，不先去沟通搜人。
- Phase 8已有原生widget重新探测及只读开卡兜底：先核对是否已运行到该代码，补实际缺口/回归。明确零输入的只读操作可fresh定位重试，必要时沿已有CDP浏览点击并做详情后验；不取消光标/窗口归属守卫，不扩成外发盲重试。
- CLI当前调用内确定性恢复、Runtime独立续租、Runner保存业务进度，职责保持。已有CDP重连/原profile浏览器重开继续复用；不新建本地Agent或后台恢复任务。旧会话接续使用合法的新执行，不热换已受理调用的release。
- 回复反映实际业务数与真实失败，不把解析错说成候选耗尽，不把程序定位错说成网站坏了。只补必要的脱敏阶段/节点几何/原生诊断，不采整份简历或整DOM。

### D. 原始日志后的三项最小方案（讨论稿，未开发）

统一细化见[原设计W4](../design/recruiting/boss-overlay-heal-design.md#w4-原始日志核验后的最小修复方案2026-10-23)：滚动候选识别复用同卡提取/ref/几何，补通读取与招呼；隐藏widget先核对并复用Phase 8，不再建重试链；23次窗口截图超屏新增“确认几何→原绑定窗口至多归位一次→重采整窗”及具体原因回传，保留现有坐标语义与落点保护。普通跨调用引用派发暂不作为上述修复前置。开发安排归原计划第15节/Phase 11，用户要求讨论清楚后再开发。

## 4. 实施、状态与必要验证

| 顺序 | 开发内容 | 进度登记 | 本次状态 |
|---|---|---|---|
| 1 | A：同卡配对、混合列表与读取/招呼联动；C中发送事实和必要搜索后验 | 原计划Phase 9、§13 | 待开发，先核对当前源码 |
| 2 | B：筛选clip和编辑恢复 | 原计划Phase 10、§14 | 待开发 |
| 3 | C中已有恢复组合验证；旧标签修复回归 | 原Phase 8及ask_user Phase 4 | 已有开发成果，现场链路未证明，不整套重做 |
| 4 | D中窗口截图超屏局部归位与诊断 | 原计划Phase 11、§15；原设计W4 | 讨论稿，未开发；不与初始hidden错误合并为同一根因 |

每包做与变更相关的开发自测及项目要求的独立测试/审查；修正失败后只补跑受影响检查，不要求每个小改重复完整全仓矩阵。现有六个业务场景保留，不恢复37项门禁：

| 既有场景 | 本批要验证的结果 |
|---|---|
| A1 正常批量筛选 | 足量候选时完成原目标；中间广告/无按钮项目不截断，顶部标签不被当姓名，单份失败不计完成 |
| A2 输入/CDP异常 | 保留半批成果；widget零输入、clip异常等走对应恢复；正常编辑面板不被误关 |
| A3 浏览器关闭 | 沿既有原profile重开并回原业务页面，已有成果不重做 |
| A4 原会话继续 | 放宽条件成功后接续剩余数量；列表下方可找回批准两人；一人局部失败不阻断另一人；招呼不重复 |
| A5 登录恢复 | 真实未登录准确提示，登录后原会话继续 |
| A6 用户停止 | 取消生效，停止后不再自动恢复并继续输入 |

旧岗位标签用现有ask_user/Web相关回归验证：保留提问卡片，实时、历史和刷新均不再展示三个旧快捷标签，不另扩一套业务验收矩阵。合成/离线通过不等于客户Chrome验收，部署与客户操作必须另有用户明确指令。

本批成功标准：**单个不可识别元素不会全局退出；恢复回到原任务剩余量；不误点、不重复外发/入库、不虚报完成；原会话能继续。**

## 5. 可直接复制的开发交接文本

```text
请处理2026-10-23 agent2招聘智能体问题，统一入口：
docs/incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md
按其中事实和最小修改包落实代码，开发进度更新原恢复计划Phase 9/10，已有Phase 8及ask_user旧标签退役修复先核对，不重复建设。

重点：同一卡片内配姓名和动作，杜绝把顶部“推荐”当人名；广告/特殊插入项只跳过自身，继续后面的普通卡片。读取、定向打招呼及共享辅助函数一起修，不能只绕过一处入口检查。离屏控件自动滚入后fresh定位；单份/单人失败局部记录，继续其他可处理对象，保留已完成成果和剩余目标，不从头重做。

筛选面板修真实scrollport与目标裁剪混淆；正常编辑面板不是待关闭障碍，失败后自动重定位/重开接续原spec。区分草稿已改与条件已提交，核验实际结果后继续剩余6份；局部筛选项不可用沿已有部分匹配规则跳过并说明，不能整轮卡死或虚报放宽成功。

保留每个招呼目标真实发送阶段：未发送自动恢复，已联系不重复，真实未知先核对该人，不把整个任务锁死。筛选/读取/名单招呼留在推荐页，后续沟通业务才进入沟通页。

复用现有CLI恢复、checkpoint、逐人状态、CDP/原生输入和原profile重开能力，不新增框架、授权门禁、全局身份系统、广告白名单或超大验收矩阵。按现有六个业务场景补相关回归及独立测试/审查，汇报实际通过和未验范围。保留其他会话改动；提交、打包、部署及客户现场操作另等明确指令。
```

## 6. 详细取证记录

以下保留当日各次排查的时序、回执和源码因果链；其中源码及版本描述对应各次取证时点，当前开发/交付状态以第2、4节及原开发计划为准。本节是事实依据，不另立一套修复要求；实施统一按第1、3、4节，局部失败不能全局阻断。

### 6.1 隐藏渲染窗口

#### 1. 会话与版本

- 会话：`session_e23e102b8b92`。
- 用户11:38:59“继续筛选”，助手11:39:36回复“这轮没跑起来”。对应trace：`tr_runner_2afd4ac171aa4de4be4bd52945113618`。
- Runner为completed，attempt=1；可观测记录约36.38秒。两次tool span失败，分别约10.42秒、18.05秒。**不是昨日STALE_CONTINUATION导致Runner无法接单的同一故障。**
- agent2部署目录HEAD：`0442891e`。被审查的 `sequence.ts`、`localOperationRecovery.ts` Git内容与本地HEAD `65f69db9`相同。
- 用户确认昨晚更新Runtime/BOSS CLI/后台。设备上报的 `runtime_version=0.2.20` 对应Host Core版本，不能拿它与Runtime UI版本比较后断言未升级；当前仓库为Runtime UI 0.0.24、BOSS 0.3.22。设备manifest未提供BOSS显示版本，未独立核对客户机插件文件digest，不据此推测用户装了旧版。

#### 2. 现场证据

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

#### 3. 源码因果链

1. `bossResumeBatch.ts`将卡片点击接到`session.click`，即`WinMouseClicker`的原生输入；本次不是靠改变CDP超时参数就能解决。
2. `WinMouseClicker`先激活绑定CDP target，再进入共享 `@aid/win32-native-input`。`sequence.ts`抬起绑定BOSS主窗并短暂等待，随后`findRenderWidget`枚举Chrome_RenderWidgetHostHWND。
3. `windows.ts:findRenderWidget`优先取可见且面积最大的widget，找不到则返回首个隐藏widget；`sequence.ts`再次检查不可见时立即抛上述零输入拒绝，未重新枚举等待它变为可见。
4. `ResumeBatchReader.input()`识别exitCode=2为未投递输入，但仍抛向批次catch。批次记失败并退出；当前只对`isTransientCdpError`清除临时失败的处理标记，原生隐藏窗口不在其中。
5. `localOperationRecovery.ts`的确定性循环仅接受CHROME_UNAVAILABLE或CDP瞬态错误。UI_CHANGED/原生零输入拒绝不进入循环，转视觉恢复。
6. 视觉分支的`unexplained`包含 `stage==='input' && obstruction==='absent'`，未区分input_status=not_started，也未优先使用goal_state=satisfied。因此正常网页判断仍被转去窗口诊断。
7. 窗口诊断失败后`if (!a.diagnosis)`直接结束，原业务没有被自动续做；模型第二次调用只是再次经历同样路径，最终要求用户F5。

原生输入来源：[sequence.ts](../../clients/shared/win32-native-input/src/sequence.ts)、[windows.ts](../../clients/shared/win32-native-input/src/windows.ts)。业务及恢复入口：[ResumeBatchReader.ts](../../clients/boss-resume-assistant/src/main/boss/ResumeBatchReader.ts)、[localOperationRecovery.ts](../../clients/boss-resume-assistant/src/main/boss/localOperationRecovery.ts)。

#### 4. 尚未证明的底层原因

可以确定“本机选中的widget被判隐藏”，但现有云端记录不能区分：窗口/标签切换后尚未重建widget、选到了隐藏回退widget、枚举后可见性改变，还是当前Chrome渲染结构与原生探测策略不兼容。代码每次会枚举窗口，不能无证据认定缓存了旧HWND。

用户截图及成功网页诊断支持网页可见、无可见阻挡；不能反推出那一瞬间每个原生HWND的可见性。鼠标被抢占、内存不足、Chrome自身故障均无现有证据。

早前云端window诊断未保留原始错误，不能据此认定是模型失败、缺包或CDP失败。后补客户Runtime日志已定位为窗口超出虚拟屏幕范围导致本地截图拒绝，详见§6.8；隐藏widget本身的具体实例仍缺窗口枚举证据。

如需进一步定位上述底层原因，补客户机11:38:50～11:39:45的当前Runtime/BOSS CLI原始日志（尤其stderr及boss-input-timing/guard）；如现有日志未记窗口枚举，则后续开发只增加脱敏PID/HWND、target标识、各widget可见性/矩形、前台/最小化状态及阶段耗时，不采集简历正文。无需重新扫描整套安装目录。

### 6.2 定向招呼与沟通绕行

#### 1. 现场定位

- 会话：`session_58d8ad3527fb`；用户描述筛选33份后批准向两人打招呼。本文未核销整个会话的成功筛选总数，不将批次数或读图数当成最终业务数。
- trace一：`tr_runner_9984c8fd53054e4f9fd3c865ac383622`，约10.32秒，completed。
- trace二：`tr_runner_386f2626342d4ed69754f974e2fc0364`，约169.85秒，completed。第二轮不是Runner永久中断，而是工具失败后返回了说明文本。

#### 2. 工具调用时序

| 时间 | 工具与实际结果 |
|---|---|
| 14:30:47 | boss_goto(target=recommend)，成功，via=already，说明已在推荐页；**未刷新，也不自动回列表顶部** |
| 14:30:50～14:30:51 | boss_greet，invocation `1f744332-2cec-4a04-af28-bc3565a849fd`，failed/unknown/INTERNAL_ERROR，提示“推荐列表出现未确认的推荐分区结构” |
| 14:33:00 | 第二轮首个boss_filter在云端返回FILTER_CAPABILITY_REQUIRED，“原筛选未改动”，未形成客户端invocation |
| 14:33:04～14:33:13 | boss_filter_options成功，读取13行可选条件 |
| 14:33:20～14:33:54 | boss_filter，invocation `aa8d597d-f507-4aea-bb68-62ec8451def8`，UI_CHANGED，“筛选面板可视滚动区域为空”，恢复diagnosis_unavailable；**不能说重新应用筛选已成功** |
| 14:33:57～14:34:04 | 再次boss_filter_options成功 |
| 14:34:07 | boss_greet，`30afbe57-5f82-428b-aae7-513f3a75dd5c`，同一分区异常，运行约0.72秒 |
| 14:34:11～14:34:15 | boss_goto(target=chat)，菜单导航成功 |
| 14:34:19～14:34:51 | boss_open_chat(候选A)，UI_CHANGED；搜索输入按现有校验未落地，已清空重试一次仍失败，恢复diagnosis_unavailable |
| 14:34:54～14:34:57 | boss_goto(target=recommend)，菜单导航成功 |
| 14:35:00～14:35:01 | 第三次boss_greet，`37309365-7ec6-4dad-ae7e-376de8448b36`，同一分区异常，运行约0.72秒 |
| 14:35:05～14:35:09 | 再次boss_goto(target=chat)，菜单导航成功 |
| 14:35:11～14:35:42 | boss_open_chat(候选B)，同样搜索校验失败，未成功核对会话 |

三次greet回执没有business_effect或execution明细，visual_recovery.state=not_eligible；事件仅记录connect/attach，没有已成功招呼进度。首个客户端greet约1.28秒，后两个约0.72秒，均短于源码单次最终点击后固定等待1.5秒，结合入口检查位置，强烈指向发送前检查即失败。但云端回执已丢失关键业务阶段，**不能将此推断当成通用“unknown均可重发”的依据**，也不能推断用户历史上从未联系过这两人。

#### 3. 源码原因与页面前提

##### 3.1 推荐分区解析过严且退出范围过大

`RecommendationResultBoundary.ts:inspectRecommendationResults`要求card-list/list-body/card-list-wrap及mome-main-ui、recommend-mome-ui等结构，并精确匹配“暂无符合牛人，为你推荐”等文案；不匹配时抛普通Error。它先遍历已加载列表结构，再计算边界是否到达视口。

`SnapshotVisibility.ts`主要检查display/visibility/opacity及祖先可见性，并不等于节点位于当前视口。`GreetExecutor.greetVisible`又在目标候选索引、向上定位和点击之前调用该解析器。因此，列表下方已加载但当前视口外的未知推荐分区，也可能阻断上方原筛选目标的定位。

这是代码确认的影响范围；现场缺少完整DOM快照，无法确定具体是哪段结构/文案变化、临时渲染状态或其他解析假设触发。不能直接将“结构未识别”说成“两个批准目标都属于补充推荐”。

##### 3.2 普通Error丢失真实发送阶段

分区解析器抛普通Error，`mapExecutorError`兜底为INTERNAL_ERROR；`operationRecoveryFailure`遇到非WinClickError的INTERNAL_ERROR直接重抛，未带回`BusinessOperationState`已有的not_started/confirmed/unknown及逐人结果；外层`runBossOperation`再由`writeEffect`把写工具INTERNAL_ERROR统一置unknown。

所以截图中“可能点了也可能没点”的说法来自不完整的工具回执，不能证明实际发生过发送。修复必须保留真实阶段：入口未发送为none；已尝试最终点击而后验不明仍为unknown；部分成功逐人保留，不能全局把此错误降为none。

##### 3.3 沟通搜索失败也不能证明未发送

`ChatSearchExecutor`当前用全页strings中的独立单字是否包含姓名字符判断“输入落地”。该判据不是搜索框自身实际值：完整姓名以单个字符串或inputValue保存时可能误判，全页其他文本也可能误通过。现场未保存搜索框DOM，尚不能区分真实聚焦/输入失败和该判据误判。

因此两次open_chat失败，只能说明没有成功完成核查，不能作为“两人没有会话”的证明。筛选面板clip为空同样需客户端几何证据判断，不在本次无证据归因于客户页面损坏。

##### 3.4 本任务页面前提

| 业务步骤 | 应使用页面 | 说明 |
|---|---|---|
| 设置筛选、筛选读取简历、批准名单打招呼 | 推荐牛人 `/web/chat/recommend` | 找回已加载原目标，可向上定位；不是先去沟通页找人 |
| 已建立会话后的聊天、收取/下载附件简历、面试、标记不合适 | 沟通 `/web/chat/index` | 属后续会话业务，与筛选读取简历区分 |

现有SUBAGENT已写明filter/greet在recommend，accept/reject/interview在chat；boss_greet也有URL前置检查。本次并非完全缺少页面规则，而是结构错误被误作发送未知后，模型把“继续打招呼”转成了沟通核查。真正发送未知时可按原结果核查；明确未发送时应优先修复推荐页并继续原任务，不能默认切页绕行。

### 6.3 中间插入项导致续筛停止

trace：`tr_runner_50c38a7ab03c4b54a971c45e617a2b54`；会话：`session_6f5b69259d79`；2026-10-23 16:01:20回复，Runner completed，约244.38秒。用户截图显示红框“热搜牛人推荐/首推推荐”插入项，下方仍有正常姓名及“打招呼”按钮的候选卡片。

| 工具执行时间 | 原始回执与云端处理 |
|---|---|
| 15:57:18～15:59:21 | boss_resume_next成功，云端工具报告入库3份；stop_reason=limit_reached，continuation.available=true；invocation `989b3f29-2c58-4e9d-9dc7-b0fcba07ab8c` |
| 15:59:23～16:01:10 | boss_resume_next保留并处理2份；stop_reason=page_operation_failed，恢复error为INTERNAL_ERROR及“未确认的推荐分区结构”；continuation.available仍为true；invocation `2d2e3082-82b5-47d3-a60e-1281480dcd99` |
| 16:01:12～16:01:14 | 第三次续筛failed/none/INTERNAL_ERROR，同一结构异常；没有stop_reason或续筛元数据；invocation `c7dd5a52-0f8a-40a7-8d57-d4b306a718b2` |

本trace新增工具报告入库3+2份；截图总结称全任务累计10/目标12，本文未另行核销此前累计记录。关键事实是：**没有回执证明recommendations_boundary或no_matching_candidates，实际是分区解析异常。**不能把成功保存部分成果当成已完成全部目标，也不能把page_operation_failed改述成“已到补充区，后面都不算筛选结果”。

与前面定向打招呼同源：共享`RecommendationResultBoundary`及`excludedRecommendationNode`也被`ResumeBatchReader`、普通卡片按钮提取使用。只修GreetExecutor仍会让读取链继续停在插入项目；当前`excludedRecommendationNode`对边界卡片之后整段`card-item`一并排除，也与允许中间插入特殊项的用户要求冲突。

该截图和日志确认问题发生在混合列表插入项附近；未保存当时DOM，尚不能断言具体哪个class/文案匹配条件失败。无需等广告逐类识别后才允许继续：按普通卡片识别跳过干扰项，继续扫描后续普通候选；不把插入项当列表终点或将其后整段排除。即使返回部分成功，也保留原目标剩余量及准确失败原因，不自行降低评分阈值或更改筛选条件来解释操作失败。

修复验证合入原A1/A4：列表中间插入该类无普通动作的推广组件，组件后仍有普通候选时，读取继续到原数量目标或真实加载结束；已入库结果不重复，最终回复不把解析错误声称为候选人已耗尽。本次仍仅诊断及文档修订。

### 6.4 顶部推荐标签误配

#### 1. 回执证据

| 工具时间 | 事实 |
|---|---|
| 15:50:49～15:52:37 | boss_resume_batch，invocation `de63baa9-c872-4973-8df1-0782301ac2c8`；limit=3，入库3份，limit_reached |
| 15:53:16～15:55:26 | boss_resume_next，invocation `43dc1197-217b-48a1-a9ca-e597f4413c8f`；limit=3，入库2份、失败5项，attempted=7，page_operation_failed；部分成果保留，continuation.available=true |
| 15:55:51～15:56:09 | boss_resume_next，`75ce0440-a826-4fd2-b02a-8c4b2f8c67b4`；UI_CHANGED/none，render widget hidden |
| 15:56:12～15:56:24 | 再次boss_resume_next，`c3a603b2-8bdf-4324-935b-1ea2fee8e6bb`；同一隐藏widget拒绝 |
| 15:56:28～15:56:38 | 模型改用boss_resume_batch，`1aa7833a-b5b8-4fe4-8f0e-c260934d9f14`；仍是同一隐藏widget拒绝，没有新成果 |

第二批`failures`第一项为：

```text
name = 推荐
error = 点击后详情未打开，重试前原候选人卡片已从列表消失（列表已更新）
operation_recovery.operation.target_label = 推荐
operation_recovery.execution.stage = input
operation_recovery.execution.input_status = attempted
```

本机进度事件seq=35、15:55:15也记录“成功读取2份……失败5个（第一个：推荐……）”。因此“推荐”不是仅出现在AI自然语言里，而是已进入CLI的目标选择和输入路径。

其他失败中包含`Cursor or render-widget ownership changed; input refused`，回执显示`ownership_match=1 / cursor_match=0`；一项仅偏差(1,1)，另有较大位置差异。没有对应原生输入时序和桌面现场，不能由此确定是用户移动、坐标舍入、桌面缩放还是其他程序干扰，更不能直接取消光标/窗口守卫。

后续第一次隐藏widget失败的网页诊断为`obstruction=absent / goal_state=satisfied / operation_code=OK`，window分支则`INTERNAL_ERROR / window_capture_or_diagnosis`，最终`diagnosis_unavailable / steps=[]`。属于已有[隐藏窗口恢复问题](#61-隐藏渲染窗口)的同形记录；本次未取得客户机执行release的完整版本证明，不据回执推断其已运行或未运行某个新包。

数量边界：本trace工具报告共入库5份，不代表“每5份必触发”的程序规则。早前逐项云端回执没有每个候选的精确时间；新增Runtime日志现确认：第二批先于15:53:46、15:54:20读出两份，随后滚动并于15:54:30记录“推荐”误配失败，确在前批3份加本批2份之后。另检出10:54、11:29、14:01三次相同模式，详见§6.8；不将相同布局下重复出现推广为硬编码刷新规则。

#### 2. 源码原因与合成验证

1. `ResumeBatchReader.locateCards`先找“打招呼”文本按钮，再调用`pairCardNameWithPoint`。姓名配对遍历整个按钮所在document的文本，而非该按钮所在card子树。
2. 配对仅要求中文2～4字、与按钮中心纵向差小于16、位于按钮左侧60%区域，并取最靠左者。“推荐/精选/最新/更多”等UI文本都可能满足，不要求姓名节点CSS可见、有效iframe裁剪、位于固定头部之下或属于同一张卡。
3. `findButtonsByExactText`只按主视口及CSS可见性取按钮，不等于按钮没有被固定头部/iframe裁剪遮住。滚动后上方卡片与固定工具栏重叠，会放大跨卡/跨工具栏配对风险。
4. Reader虽查询`loadedCandidates`生成reference，但查不到对应候选时reference=null，并不阻止首次点击，于是错误“姓名点”仍可送入`session.click`。不能靠修重试阶段的reference核验修好首次误点。

使用本地现有dist的真实`pairCardNameWithPoint`执行内存合成快照（没有写入测试文件或启动Chrome）：同文档顶部“推荐”中心(340,74)、普通姓名“测试甲”中心(410,80)、按钮中心(1445,82)，返回`{name:"推荐", point:{x:340,y:74}}`。它验证跨工具栏配对机制，**不冒充客户机当时的DOM或实际坐标**。

本次与[混合列表分区异常](#62-定向招呼与沟通绕行)同属候选提取链缺陷，但具体根因不同：前者对插入项全局拒绝；本次把卡片外的工具栏文本配成姓名。两者必须一起收敛到“同一普通卡片内的姓名和动作”，不能只删除分区异常或增加UI词黑名单。

### 6.5 筛选面板空clip

#### 1. 实际执行顺序

| 时间 | 事实 |
|---|---|
| 16:04:52 | 先调用boss_resume_batch，约1.3秒报同一“未确认的推荐分区结构”INTERNAL_ERROR，未读取新简历 |
| 16:04:58～16:05:34 | ask_user等待后成功收到“性别不限、再读6份” |
| 16:05:37 | boss_filter在云端被FILTER_CAPABILITY_REQUIRED拒绝，要求重新探查；没有派发客户端调用 |
| 16:05:39～16:05:49 | boss_filter_options成功，引用`3743344f-bc73-48cd-a789-e51a5da0721d` |
| 16:05:54～16:06:58 | boss_filter，invocation `3c68ecb5-c401-4239-902d-79ec707b789b`；实际执行约64.5秒，空clip错误 |
| 16:07:03～16:07:33 | 第二次boss_filter，`601c8e3e-e69a-4933-b1c4-ded6b369b2c1`；约29.5秒，同一错误 |
| 16:07:37～16:07:48 | 模型显式goto(chat)，再goto(recommend)，均成功；切页没有解决计算错误 |
| 16:07:49～16:07:57 | 重新探查成功，引用`c068a736-5b5f-4799-bfc5-0843d08e9567` |
| 16:08:00～16:08:29 | 第三次boss_filter，`38cc072d-6922-4379-890f-a273da2c3071`；约29.6秒，同一错误；最终回复约16:08:36 |

三次参数一致：经验1–3年及3–5年、学历本科/大专、薪资5–10K、年龄min=30/max=null，不再包含“性别男”。现有apply为替换语义（先清除再重设），因此去掉性别请求本身与用户选择一致，不是LLM遗漏“放宽性别”。但执行没有完成，不能称条件已生效。

三次回执都为failed/effect=none，`stage=input / input_status=attempted / attempts_used=1 / ordinary_retries_used=0`，核验为`filter_button_count=7; expected=unknown; panel_open=true`，恢复最终`diagnosis_unavailable`。没有后续成功的筛选或6份读取回执。心跳事件持续续租（第三次在16:08:04/07/12/16均有记录），没有任务租约超时证据；不能把整个调用耗时都说成滚轮一直执行。

#### 2. 条件状态及恢复证据

##### 2.1 不能把effect=none解释成面板从未被修改

截图和最后一次网页诊断均见年龄16、经验/学历不限，而请求为年龄30以上、指定经验/学历。工具栏仍显示“筛选·7”，不能证明展开面板中的草稿仍等于原已应用条件，也不能证明本次条件已提交。

源码先点击“清除”，然后`setAge`，再设置经验/学历/薪资，最后才计算expectedCount和点“确定”。本参数包含age，最先进入panel模式clip计算的是`setAge → ensureHitVisible → visibleClipOf`；该路径位于清除之后。结合输入已尝试、expected仍unknown及画面控件归零，强烈指向**草稿清除后、年龄设置前被几何计算中断**。没有逐步输入日志或现场DOM，清除是否改变已应用列表、点击的精确后态未独立证明；不把源码推断写成已提交清空。

##### 2.2 恢复目标与正常编辑面板混淆

第一次视觉恢复：page诊断将打开的筛选面板/顶部提示视为阻挡，建议点“取消”；确有一项dismiss输入尝试。随后postcheck仍判面板展开、阻挡存在；下一轮又判没有UI阻挡、条件尚未设置，转window诊断后INTERNAL_ERROR退出。不能把模型所称“顶部提示条干扰”当作已确认事故根因。

第二次page为`obstruction=absent / goal_state=not_satisfied`；第三次page为`absent / inconclusive`，并明确说空滚动区域的错误与截图不符。两次都转window分支，`window_capture_or_diagnosis / INTERNAL_ERROR`，没有恢复动作。

正常编辑阶段本来就需要面板展开，不能仅因最终expected要求“面板关闭”就把该面板当成应关闭的障碍。当前恢复链没有修正clip几何或接回面板设置；反复调用相同解析器、切沟通再切回来，也不会改变该算法缺陷。

#### 3. 几何计算缺陷与证明边界

`FilterSetter.visibleClipOf(panel, hit)`从目标节点一路向祖先查找，把所有`overflow-y=auto/scroll/hidden/clip`节点的布局框与主视口逐一求交；一旦`bottom <= top`就直接抛错。此时尚未回到`ensureHitVisible`根据目标越界方向执行滚动。

这混合了两个不同概念：**实际可驱动的面板滚动视口**，以及**目标当前被内容分组/内部裁剪遮住的区域**。目标或其随滚动移动的内部hidden分组暂时在视口外，可以让求交为空；这并不意味着面板scrollport不存在，也不意味着无法滚回。当前异常把可恢复的“目标需滚入”提前升级为整个筛选失败。iframe owner及祖先裁剪还需在同一坐标口径内处理，不能只按主视口断言。

内存合成样本调用现有dist真实`visibleClipOf`：面板auto滚动视口为y=[100,700]，目标所在hidden内容组为y=[980,1080]，得到同一“可视滚动区域为空”异常。它证明“目标离屏导致全局空区域”的机制，**不是客户机现场DOM复原**。已有脱敏真实VIP top/down fixture的年龄clip分别仍非空，也说明该错误不是所有会员面板必现。

现场未保存触发瞬间的目标节点、祖先bounds/style和iframe偏移，因此尚不能确定客户机究竟是哪一个祖先、布局瞬态或坐标转换令交集为空。已确认的是错误来自该计算路径、截图/网页诊断不支持“面板没显示”，且恢复没有接续；不归咎于BOSS故障、内存、客户鼠标或CDP超时。

### 6.6 需要补证时的最小日志范围

仅在区分尚未确定的现场底因时补充，不作为已复现算法缺口的开发前置：

| 取证问题 | 客户机时间范围与内容 |
|---|---|
| 隐藏widget具体实例 | 11:38:50～11:39:45，当前Runtime/BOSS stderr、输入阶段及窗口枚举可见性/矩形 |
| 招呼前置解析与搜索 | 14:30:40～14:35:45，BOSS CLI日志及可取得的当时分区/搜索框状态 |
| 顶部标签误配后的原生拒绝 | 15:53:10～15:56:45，BOSS stderr/输入时序及实际插件版本/digest |
| 面板具体裁剪祖先 | 16:05:30～16:08:35，BOSS stderr/perf及目标/祖先脱敏几何、实际插件版本/digest |

不要求递归扫描安装目录；不采候选人正文、凭据或整页DOM。缺少旧现场材料时明确记录未知，补必要诊断后验证，不编造原因。

### 6.7 跨轮调用专项核查（2026-10-23，只读调查）

用户补充观察：新会话首轮通常正常，后续提问容易出现不可见、无法操作。此次按用户要求只读核查，不改业务代码、不操作客户浏览器、不部署。结论尚待客户机 `runtime.log` 补齐原生窗口证据；不能先把“跨轮/CDP失控”定为统一根因。

#### 1. 取证范围及可核验材料

直接查询 agent2 业务库中的 invocation、事件与 Runner 记录，均使用只读事务；不是仅复述前面的诊断文档。覆盖当天以下三个相关会话：

| 会话 | 客户端调用数 |
|---|---:|
| `session_e23e102b8b92` | 21 |
| `session_58d8ad3527fb` | 38 |
| `session_6f5b69259d79` | 25 |

合计84次，关联15个有实际客户端调用的用户轮次。53次invocation为succeeded、31次failed；succeeded也包含保留部分读取成果的情况，不代表全部业务目标完成。主错误码为9次INTERNAL_ERROR、22次UI_CHANGED；这84次的主错误文本中未检出CDP断线/超时、session not found或target closed标记，不能据此排除未进入回执的底层事件。

脱敏证据只保留调用ID、时间、参数键、数量、阶段和页面引用摘要，不含简历、姓名、凭据或图片：

- [调用参数与回执摘要](evidence/boss-cross-call-20261023/invocation-summary.json)
- [Runner轮次与调用对应关系](evidence/boss-cross-call-20261023/cross-turn-timeline.json)
- [恢复诊断及筛选后态](evidence/boss-cross-call-20261023/recovery-observations.json)

本次读取时agent2目录HEAD为`b213bd76`，与早前取证时点不同；服务器当前源码版本不证明客户机当时执行的CLI版本。相关正常派发、续筛参数、默认选页、分区解析与筛选几何逻辑，与`6871fdef`基线核对未变；原生输入及恢复已有Phase 8增量，不将当前改进冒充当时行为。

#### 2. 正常后续调用没有继承原页面引用，但本批未证实选错标签

源码链路已确认：

1. `resume_continuation.prepare_next`从上批生成`continue_from/limit/exclude_names/expected_job_name`并固定原设备，没有将结果中的`browser_target_ref`传给下一次CLI。
2. `LocalToolProxyTool.execute`构造普通派发参数时删除所有下划线开头字段；不能认为内部`_boss_target_ref`会自然跟随正常调用。
3. CLI MCP入口能消费`_boss_target_ref`，但无该字段时，`defaultSessionFactory`走`attachToRecommendPage`重新枚举BOSS标签；该选择器把推荐/沟通业务标签放在同一优先级。
4. 本机调用内恢复会沿已有结果注入`browserTargetRef`；旧服务端恢复也显式注入`_boss_target_ref`。这种绑定不能推广为正常跨调用已绑定。

现场核查：84次派发的`arguments_json`均无`_boss_target_ref`，但84次结果的`browser_target_ref`摘要全部相同，且属于同一设备。正常继承引用的缺口是代码事实，**没有证据表明本批事故实际因重新枚举选到了另一个标签**。同一opaque引用允许恢复owner更新target，因此也不把摘要一致当成targetId或原生HWND永远不变的证明。

三个会话返回相同引用还说明：聊天会话没有独立重建BOSS浏览器页面。新开聊天不能等同于重置Chrome或获得全新页面状态。

#### 3. 已有fresh读取，但原生激活之后没有重新定位

`runBossOperation`每次创建新的Gateway，连接browser WebSocket并attach页面；退出时detach/close该CDP连接。普通关闭链没有关闭BOSS标签或主动隐藏Chrome的动作。

`ResumeBatchReader`每批、逐项以及重试时会重新采集快照，不是持有首轮旧DOM一直续用。但普通首次物理点击的顺序是：

```text
snapshot → 计算候选/控件坐标 → WinMouseClicker.click(point, viewport)
→ Target.activateTarget → 原生抬窗/查找widget → 坐标换算/守卫 → 输入
```

激活后没有重新采集并按目标语义定位；原生实时窗口矩形换算不能代替DOM重新定位。这是已确认的时序缺口，**尚未从现场证明激活实际引发了布局变化或误点**。后验读取和失败后的fresh重试，不等于这次输入前已经重新定位。

特别区分：`render widget hidden`是在渲染窗口发现阶段、屏幕坐标换算和实际输入之前抛出的。旧坐标不新鲜不能直接解释这条报错；仅多读一次DOM也不能让隐藏HWND变成可用输入窗口。

#### 4. 上一轮收尾保留了哪些页面状态

| 上一轮动作 | 源码确定的收尾行为 | 本批现场证据及限制 |
|---|---|---|
| 正常批量读简历 | 每份读取后Escape，并轮询简历画布消失；最后一份关闭成功才返回limit_reached；不回列表顶部，不清除已加载列表 | 11:27、15:50及15:57批次返回limit_reached；源码路径支持详情已关闭，但回执没有列表滚动数值/原生窗口快照，不能精确复原位置 |
| 分区解析失败 | 新快照解析阶段即可抛错；没有自动重置列表或清理分区 | 14:24的批次保留2份后报分区异常，14:25续筛再次失败；14:30招呼入口又报同一异常 |
| goto推荐页且已在该页 | 只核对URL后返回via=already，不刷新、不回顶、不承诺窗口可输入 | 14:30:47确为already，随后招呼仍命中前述分区异常；13:51的goto已成功，随后的岗位读取仍报隐藏widget |
| 筛选探查成功 | 取得选项后尝试收起面板并核验，收尾错误单独保留 | 不能将成功探查推广成后续apply的几何一定正确；探查不等于所有目标均已滚入 |
| 筛选设置失败 | 可能已修改草稿；没有覆盖所有失败出口的统一取消/回滚 | 16:05、16:07、16:08三次回执均`panel_open=true / expected=unknown / filter_button_count=7`；不能称页面已还原或条件已提交 |

**后续任务面对的是上次留下的真实页面状态，重新建立CDP连接不会清空这些状态。**14:24读取阶段的分区异常在后续招呼阶段复现，有连续调用证据支撑；不需要假设CDP失联才能解释。

#### 5. 用真实轮次检验“首轮好、后续失控”

| 北京时间 | 实际调用链 | 可支持的结论 |
|---|---|---|
| 10:48起 | 同一首轮内batch及两次next均完成读取 | CLI内部多次断开再连接并非必然失败 |
| 11:21→11:24 | 同一会话先两次隐藏widget失败，随后探查、设置及两批读取成功 | 失败并非使该会话永久丢失页面控制；间隔期间具体桌面动作未知 |
| 13:51 | 新会话首轮goto=already，随后岗位读取、探查等四次隐藏widget失败 | 首轮也会发生，跨用户轮次不是必要触发条件 |
| 13:56→14:25 | 同一Runner用户轮次内多批读取成功，14:24出现分区异常，14:25再次失败 | 分区故障在轮内就发生，随后被后续业务继承；不是只在跨轮时出现 |
| 15:46 | 另一个新会话首轮也出现隐藏widget失败 | 新聊天不自动修复原生输入窗口状态 |
| 15:49→15:56 | 同一用户轮次先读3份，下一批读2份并出现“推荐”误配及输入拒绝，之后三次隐藏widget失败 | 定位/输入故障也可在单轮内部出现 |
| 15:57 | 原会话沿15:53的同一续筛父引用，重新成功读3份 | 不需要新建会话或丢弃游标才能再次成功；期间是否人工切窗/恢复，现有云端材料未知 |

不把上述三个样本推广为所有会话的发生率统计；它们足以纠正“后续对话统一导致CDP永久失控”的解释。

#### 6. 隐藏widget目前已定位到哪一层

84次中15次主失败为隐藏widget，原业务回执均为`input_status=not_started`。每次恢复的page分支均取得HTTP 200/completed的有效网页诊断；window分支均未取得有效诊断，最终均为diagnosis_unavailable。14次没有恢复动作，另有一例页面dismiss尝试，因此不能将原业务零输入推广成这15次的恢复过程也都完全零输入。

结合`WinMouseClicker → sequence.ts`的错误路径，可将隐藏widget拒绝定位为：已有页面数据、业务已能完成目标定位，进入物理输入后无法取得可见渲染子窗口；它不是“DOM读取已失效”的同义词。旧恢复未把这类拒绝接回确定性业务恢复，网页图可用后仍转到失败的窗口诊断，导致本来暂时的原生输入故障最终结束当前业务调用。

本节形成时尚缺客户机原始窗口枚举/输入时序。后续用户已提供客户`runtime(1).log`，输入时序与窗口截图错误已补证，见§6.8；该日志仍未记录隐藏widget实例的HWND/矩形，不能确定究竟是widget重建时序、隐藏回退实例，还是其他原生结构差异。当前开发机Runtime的设备摘要与上述84次调用的客户设备不同，未拿开发机日志替代客户现场。

目前应区分三种结论：**页面引用传递缺口、激活后未重新定位，是源码确认但未证明触发本批事故的风险；滚动/分区及筛选草稿状态跨调用保留，有本批连续回执支持；隐藏widget已确认是原生输入与恢复链故障，具体HWND底因仍待现场日志。**不据此启动新框架、统一刷新或盲重试，本轮继续诊断。

### 6.8 客户Runtime原始日志核验（2026-10-23）

用户提供`C:/Users/PC/Downloads/runtime(1).log`后，完整扫描7893行；文件内日期全部为2026-10-23，覆盖北京时间10:35:23～20:23:00。原日志为UTC `Z`时间，本节统一换算UTC+8；文件大小996442字节，SHA-256为`942c8c34c60a49465db750137d3a26df4b50776ae7fb4ffaf680e125f39f4a94`。按invocation对齐前述84次云端调用，未修改原日志。

仅在仓库保留脱敏派生材料，不复制完整客户日志：

- [全量计数、逐调用协议/原生输入摘要及四次误配模式](evidence/boss-cross-call-20261023/runtime-log-analysis.json)
- [保留原行号的协议与错误节选](evidence/boss-cross-call-20261023/runtime-excerpts.redacted.log)

#### 1. CDP没有显示跨轮失联；15次隐藏widget前后均能读页面

| CDP方法 | send | ack |
|---|---:|---:|
| Target.attachToTarget / detachFromTarget | 各135 | 各135 |
| DOMSnapshot.captureSnapshot | 1458 | 1458 |
| Target.activateTarget | 531 | 531 |
| Page.captureScreenshot | 363 | 363 |
| 所有已记录方法合计 | 3270 | 3270 |

全日志未检出CDP超时、断线、session not found或target closed标记。上述计数不是任意CDP操作必成功的保证，但本批不能再解释为“后续调用拿着已断开的首轮CDP连接”。

以11:39第一次失败`f8a02297-ff44-47ca-9304-5d56deb8cf59`为例：

| 原日志行号 | 证据 |
|---|---|
| 1889～1890 | 新Target.attachToTarget正常ACK |
| 1893～1903 | 连续四次DOMSnapshot采集正常ACK，最后一次约131ms |
| 1905～1906 | Target.activateTarget正常ACK，约22ms |
| 1907～1910 | 原生输入探测失败，render widget hidden；发现阶段约0ms，业务读取0份 |
| 1914～1927 | 恢复重新attach，DOM正常，Page.captureScreenshot也正常ACK |
| 1945～1947 | 窗口分支再次激活成功，随后本地窗口截图拒绝 |

15次隐藏widget都能找到失败前成功的DOM采集与标签激活；激活ACK之后到原生失败之前均无新的DOM采集，原生discover耗时0～2ms。当前代码的激活后定位缺口得到时序实证；但隐藏检查在坐标换算/物理输入之前，仍不能把隐藏widget归因为旧坐标。

Win32的`IsWindowVisible`检查的是窗口及祖先的`WS_VISIBLE`样式，不等同于页面DOM可读或屏幕无遮挡；参见[Microsoft定义](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-iswindowvisible)。因此本批确切断点是**CDP数据读取/标签激活成功后，原生输入未取得可见的渲染子窗口**。

#### 2. 窗口诊断失败已找到确切原因：23次本地超屏截图拒绝

全日志23次`boss_overlay_inspect`底层错误均为：

```text
[BOSS_WINDOW_REFUSED] Browser window extends outside visible screen; capture refused
```

11:39第一次对应原行1947、第二次2018；其余覆盖此前隐藏widget、搜索、光标拒绝及筛选clip失败后的窗口诊断。没有取得窗口诊断的原因已从笼统INTERNAL_ERROR缩小为**本地截图程序认为Chrome采集矩形超出虚拟屏幕边界而拒绝截图**，不是本次云端视觉模型判断失败。

对应`window-experiment.ps1`的capture分支：读取Chrome采集矩形，与`SystemInformation.VirtualScreen`比较，任一边超出就整体拒绝，再于catch包装`BOSS_WINDOW_REFUSED`。该异常正文未进入旧云端恢复观察，故前面只能看见`window_capture_or_diagnosis / INTERNAL_ERROR`。

不能继续无证据补全为“客户窗口没最大化”或“最大化不可见边框”：仓库对应基线的`ReadCaptureRect`已使用DWM extended frame bounds，并非直接拿含不可见边框的GetWindowRect。原日志没有打印DWM矩形、虚拟屏幕矩形、缩放及显示器信息，尚不能区分实际窗口部分超屏、屏幕配置或坐标口径差异。后续应针对这条具体几何拒绝修复/补证，不能删除全部遮挡及落点保护。

这23次是恢复链的第二个故障，会使原业务的局部问题恢复失败；它不证明所有最初的隐藏widget、分区解析或筛选clip都来自同一超屏原因。

#### 3. 四次“3+2份后误配推荐”形成重复证据链

| 误配失败北京时间 | invocation | 原日志行 | 前批读出 | 本批误配前读出 |
|---|---|---:|---:|---:|
| 10:54:47 | `924491a5-4c1d-4ca2-a528-5f4de8687cd4` | 702 | 3 | 2 |
| 11:29:22 | `5bdfd67d-93a7-46f3-bd5f-2315ff88681c` | 1814 | 3 | 2 |
| 14:01:15 | `e234dbb2-0499-4393-be29-1131adfafd60` | 2830 | 3 | 2 |
| 15:54:30 | `43dc1197-217b-48a1-a9ca-e597f4413c8f` | 6652 | 3 | 2 |

四次初始视口均为1674×884，按钮行y=[146,330,514,698,882]。每次在本批读出两份后，均有列表CDP mouseWheel成功ACK，随后两次fresh DOM采集，再激活并成功投递一次原生点击；之后轮询详情未打开，最后记录`卡片[推荐]`失败。不是“未重新读取旧页面”：滚动后的新快照已经取得，错误算法仍把卡片外的UI文本选为姓名。

15:54链路可精确定位到：6635～6636列表滚动、6637～6640两次fresh读取、6641～6643激活及物理点击成功、6652“推荐”失败。这补齐早前“无法确认是否恰在第五份之后”的时序边界。

结合源码，已确认问题在**滚动后跨卡/跨工具栏姓名配对与点击目标合法性**，不是固定五份刷新或CDP丢连接。相同布局使用户观察到的五份规律重复出现。源码的半屏滚动量与原行距可解释卡片靠近固定工具栏的风险，但日志没有滚后节点bounds，不能把推算的具体重叠坐标当成现场实测。修复仍须同卡姓名/动作来源、固定头部及iframe裁剪、合法reference约束首次点击，单加fresh或词黑名单不足。

10:54这次就在新会话首轮内部发生，后续仍补足数量，所以“首轮最终成功”也不等于首轮内部没有误点；它与后续叠加原生拒绝导致整体中止应分开看。

#### 4. 上轮收尾与筛选草稿的实证补充

11:29最后一份读取后，原行1872～1875是两次按键事件ACK（对应源码Escape按下/抬起），1876～1877随后fresh DOM ACK，再返回limit_reached并detach。不能将11:39失败直接归为上轮忘关简历详情；没有列表回顶动作仍与源码一致。

16:05放宽筛选的首个apply，原行7531～7538有两次原生click成功（对应源码开面板、清除），随后fresh DOM，7543报空clip。性能分段同时记录`filter:panel:clear`完成、两次click、四次setter快照，未出现年龄drag或最终确定的输入。因此能确认清除输入实际投递并返回成功，错误发生在继续设置/提交前；仍不以输入ACK冒充已应用条件。面板草稿与已提交列表必须分开记录，正常编辑面板不能按最终关闭目标去恢复。

#### 5. 实际客户端执行链与剩余未知

原日志仅记录一次Provider启动（10:49:18，pid=25128），后续无Provider退出/重启记录；所有调用继续在该执行链内。没有任何`boss-input-discovery`枚举日志，15次隐藏widget的discover均只耗0～2ms，也没有新增原生窗口等待重枚举/确定性接续的执行痕迹。这与Phase 8修复前路径一致，不能把15:34之后的同形错误当成客户已运行新修复而修复无效的证据。日志未给出插件版本/digest，仍不据此编造精确版本号或推断用户是否完成安装操作。

剩余的底层未知已缩小：**隐藏widget具体哪个HWND、为何隐藏，以及截图矩形究竟哪一边超屏**。完整日志没有这些字段；现有材料可确认故障分层和23次截图拒绝原因，不能继续从同一文件推出不存在的几何事实。本轮没有新增诊断代码、重试客户操作或部署。

当前方案讨论据此调整重点：先修已重复出现的同卡配对/混合列表和筛选编辑；复核既有Phase 8是否真正进入客户执行链；将窗口截图超屏拒绝作为独立恢复缺口处理。正常页面引用接续与激活后fresh属于已确认代码缺口，但本批没有证据将它们升级为所有事故的共同根因，也不采用每轮刷新/重开浏览器来替代修复。

### 6.9 本机PHP职位连续多轮验收（2026-10-23晚间）

用户已安装Runtime0.0.26/BOSS0.3.24并登录推荐页，主控通过agent2原“打开boss”会话连续测试，未刷新/新建会话。安装版18个相关生产文件与发行清单SHA256一致；原生新枚举日志存在。首批3份、随后3份、再3份、清除全部筛选后6份，共15份读取并由网页确认入库，指纹无重复；实际发生列表滚动，未误配“推荐”。只读简历，不发送招呼或聊天。

“全部不限”有效筛选数0，成功仅证明开面板/清除/提交。用户指出没有真实筛选条件后，第6轮21:53:15改为经验3–5年＋本科、其他不限，排除已读15位并读取3份。结果：21:53:39.932、21:54:45.685两次原始空clip，均为`left=499,right=1364,top=93,bottom=-78`；包装后分别UI_CHANGED/partial，中途一次高级行参数触发MEMBERSHIP_REQUIRED/none。新条件未成功提交，新增0份，不能写为整套真机验收通过。

**本次空clip的具体根因已确认。** 停止后保留原页面，使用已安装插件只读截图/快照接口导出真实几何和computed styles；直接用安装版FilterSetter离线计算两项，均复现相同负下边界。doc2文档已滚动1344px，iframe原点y=40，根BODY布局高度1226、overflow auto/scroll，HTML两轴visible，HTML/BODY contain none。程序将BODY当成普通裁剪祖先，按`40-1344+1226=-78`向上移动裁剪下边界。实际`.filter-panel > .top`范围`[499,93,865,796]`，经验/本科中心分别`(1251.96,671.5)`、`(1042.625,756.5)`，均可见。离线只将这一BODY改按文档视口裁剪，其他祖先限制不动，立即得到有效clip`[499,93,1364,889]`。依据[W3C overflow传播规则](https://www.w3.org/TR/css-overflow-3/#overflow-propagation)，此处computed BODY overflow不能作为BODY自身used裁剪，根视口不随自身内容滚动；不是DOM读取失败或真实面板隐藏。

因此，前轮保留的**列表滚动状态**确实能触发后轮筛选故障，但触发点是几何语义错误。重新采DOM会稳定重现，单纯再attach/激活、刷新/回顶或窗口归位不能构成修复。当前生产日志733次CDP请求全部ACK，29次原生click及103次wheel成功，本轮未触发隐藏widget或截图超屏拒绝；不将其历史底层未知改称已解决。现象与白天空clip同形，白天无祖先快照，不能追溯宣称所有白天clip均由该BODY导致。

关联独立能力缺口：21:45:38请求保留其他当前条件，`boss_filter_options`仅返回可选值及单/多选模式，缺当前selected值；整体替换无法保证未指明条件不丢，模型因此询问用户。会员确认是另一个参数/能力门槛，两者不归为CDP丢控制。

最小增量仍归Phase 10，修每文档根视口及BODY/HTML overflow传播判定，保留内层/iframe裁剪，补真实匿名祖先几何回归，之后在原滚动现场完成非空条件提交并续读剩余3份。Phase 8和11不重复建设；本轮未改生产代码、提交、打包或部署。完整轮次、未验项及后续安排见[原计划§18](../plans/plan-runner-local-tool-recovery-lockup-fix.md#18-本机php连续多轮业务验收2026-10-23晚间)，设计见[W4.8](../design/recruiting/boss-overlay-heal-design.md#w48-本机连续多轮验收根文档滚动裁剪遗漏2026-10-23)，脱敏证据见[live-php-acceptance-20261023.json](evidence/boss-cross-call-20261023/live-php-acceptance-20261023.json)。原始截图及DOM含候选资料，仅保留在本机忽略目录，不提交。

### 6.10 修复、发行与本机自主安装的最终状态

用户随后授权修复、打包并尽量自行安装。根BODY裁剪已修，新增真实匿名面板/祖先fixture；旧代码三项红、修后五项通过，独立169项及CR通过。安装首批Runtime0.0.27/BOSS0.3.25时又实证Host0.2.22硬限16根与发行追加历史根冲突：第17根触发冷H1 describe code9、NSIS exit2，首批已撤回。最小删除冗余根数上限，保留65536字节及全部逐项校验；独立29项及CR通过，Host升0.2.23。共同H1/Device/schema及信任权限不变。

最终Runtime0.0.28/BOSS0.3.26组合包已验，实际冷H1检查及NSIS安装exit0。Runtime已在原目录升级，原设备身份/配对和旧BOSS启用状态迁移保留，18根资源与验证产物一致。调试端口在Runtime更新期间曾不可达，已用可信原profile恢复，确认原账号和PHP职位一致，当前文档滚动0；未继续读取新简历，不能称非空筛选业务复验通过。

自主安装结束时实际BOSS仍0.3.24 enabled（用户后续导入及续验见§6.11），0.3.26签名包待用户通过Runtime原生选包导入。工具只有浏览器控制、没有原生App窗口能力，产品也未提供无UI导入CLI/API；未伪造selection_ref、改inventory或另写安装旁路。用户醒后导入最终插件即可沿原agent2会话继续，排除15份，先复现非零文档滚动再验3–5年＋本科和剩余3份。详情、最终包摘要与脱敏安装回执见[原计划§19.4](../plans/plan-runner-local-tool-recovery-lockup-fix.md#194-最终新包本机安装及人工剩余步骤)。本轮未Git提交/推送或部署服务器。

### 6.11 BOSS0.3.26原会话三轮续验通过

用户完成原生导入后，清单确认0.3.26 enabled，安装版FilterSetter.js摘要与受测发行包一致。22:49:31～22:57:58继续原“打开boss”会话、PHP职位，三轮分别为具体筛选后读3份、保持筛选续读3份、从597px滚动状态重设同一筛选再读3份。全部9份获网页入库确认，排除集合依次15/18/21，新增内部及与原15份均无重复，累计24份；没有招呼或聊天工具调用。

第三轮编辑中doc2仍滚动597px，经验及本科中心分别671.5/756.5，修复clip下边界889；离线仅撤去根视口特判，边界变为669，会误排两个可见选项。两次实际筛选均OK/applied且计数2；BOSS在提交后自行重绘回顶，未靠提前回顶或刷新绕过故障。此次不是原1344px现场，但真实覆盖同一错误分支。399次业务CDP均ACK，click19/wheel66成功，无负clip或控制异常；隐藏widget恢复和真实超屏归位均未触发，不扩大通过范围。原始截图/DOM/日志只留忽略目录；本轮仅文档更新，无生产修改、打包、提交或部署。详见[原计划§20](../plans/plan-runner-local-tool-recovery-lockup-fix.md#20-boss0326原会话三轮真机复验2026-10-23)及[脱敏证据](evidence/boss-cross-call-20261023/live-php-boss-0.3.26-20261023.json)。

### 6.12 隐藏及截图超屏的受控取证（2026-10-24）

用户手动最小化/还原、拖出/移回同一BOSS窗口，主控只读枚举并调用安装版截图接口；所有capture明确禁止自动归位，实际预算0、仅一次采集调用。PID51096、HWND15339516，显示器[0,0,2560,1440]、DPI96。最小化时原父窗口仅枚举到隐藏辅助widget，生产选择器返回hidden fallback4001528，但CDP仍可读原target及597px列表；还原后主widget5312714重新出现。51～64ms间隔1200次观察未采到未最小化而选择器仍hidden，不代表历史激活后错误已复现。

停稳真超屏DWM框[1297,2,2709,1356]右边超149px，实际captureWindow在allowReposition=false下拒绝window_outside_screen；手动回屏DWM框[1147,5,2559,1359]后，虽然普通GetWindowRect到2566，实际截图仍成功1412×1354。两组正式对照的前/中/后矩形均完全一致，无自动归位掩盖。当前本机没有发现这两类受控状态的误判；旧客户机15次hidden/23次超屏缺原生矩形，具体历史底因仍不能由本机对照替代。自动恢复/归位、混合DPI和多屏未验。第二次最小化直接追踪确认原widget5312714仍有效，换父至隐藏Chrome_WidgetWin_0（8129800），还原后挂回15339516且可见；其隐藏期间rect仍为[0,87,1410,1353]，不能只因坐标在屏内就认为可操作。详见[计划§21](../plans/plan-runner-local-tool-recovery-lockup-fix.md#21-隐藏窗口及超屏判断的只读取证2026-10-24)及[脱敏证据](evidence/boss-cross-call-20261023/window-visibility-and-screen-evidence-20261024.json)。本轮没有生产修改、提交、打包或部署。

### 6.13 完整恢复实测及CLI内接续边界（2026-10-24）

用户授权完整恢复测试后，原会话最小化续读1份成功、累计25份，无人工还原或候选通讯。第二次只读筛选选项也自动激活并收尾。加密观察确定主窗口已还原而widget仍在隐藏父下的窗口期存在，但本次widget于19:07:20.107可见，早于19:07:20.111的激活ACK约4ms，没有复现历史ACK后隐藏。没有触发原生持续拒绝的重连/兜底，不将正常激活成功扩大为所有Phase 8分支通过。19:14预置真实超屏266px后，首次输入被守卫拒绝，窗口观察自动归位一次、原HWND不变且尺寸保持，新截图1412×1354并更新geometry epoch。首调用仍因目标不确定返回失败；智能体随后重派筛选，新DOM定位后成功提交并再入库1份，累计26份，无重复或候选通讯。业务恢复完成，但同一CLI调用自动接回setter的衔接缺口保留，不扩大为完全确定性闭环。详细时序、验收边界和阶段证据见[计划§22](../plans/plan-runner-local-tool-recovery-lockup-fix.md#22-完整恢复真机测试2026-10-24)、[full-recovery-acceptance-20261024.json](evidence/boss-cross-call-20261023/full-recovery-acceptance-20261024.json)。未改生产代码、提交、打包或部署。


### 6.14 CLI接续缺口已由安装版回执重放定位（2026-10-24）

原window诊断reason已明确：输入被零输入拒绝，面板关闭和旧徽章2不足以证明3–5年＋本科确实匹配，所以absent/inconclusive正确。CLI page_state分支却要求原最终goal已经satisfied才能resume，无dismiss建议则返回原失败；本次not_started不命中已有draft_edited专用分支，cursor mismatch也不属于Phase 8的hidden/unavailable确定性恢复分类。这解释了19:14:43截图已归位、19:14:52首CLI仍失败、19:14:55智能体新派发后19:15:11才成功的时序，不能归咎于本次CDP失联或持续隐藏。

直接加载安装版withLocalRecovery并注入原page/window诊断离线重放，原回执continuation=0、no_supported_action；只把window goal_state改为satisfied作单字段对照，continuation=1，原binding、共享预算及执行计数均传入续做。四个相关安装编译模块与工作区dist hash一致。外层失败envelope按保存context重建、归位预算变化由替身模拟、对照续做为sentinel，未访问浏览器/视觉接口，不是新真机结果，也未独立取回现场outer state。见[重放证据](evidence/boss-cross-call-20261023/cli-continuation-replay-20261024.json)。

最小方案仅增加共享恢复消费端的窄条件：原filter/clear已认证UI_CHANGED/none、input/not_started与filter_phase=not_started，已有continuation；同次window观察预算0→1且实际窗口观察及completed回执有效、无stale/consumed等错误，absent/inconclusive接回原resume一次，最终由fresh setter后验决定成功。计数增加不单独证明恢复；不改模型结论、不扩全部page_state/外发、不新增窗口恢复器或wire。已有Phase 8/11、target/epoch/取消、submitted核验及预算继续复用。完整设计与待实施验收见[W4.11](../design/recruiting/boss-overlay-heal-design.md#w411-已归位零输入筛选的同调用接续2026-10-24已实现10-26真机通过)、[计划§23](../plans/plan-runner-local-tool-recovery-lockup-fix.md#23-cli内部接续缺口日志定位与最小修复2026-10-24)。此处为前次调查结论；用户随后已授权最小开发、独立测试与CodeReview，执行结果如下。


本轮仅修改localOperationRecovery消费判断，复用原归位/截图/epoch与续做闭包，未修改Runtime/native/FilterSetter。最终74项定向及typecheck通过，独立15文件261项与精确坐标探针通过，CR无新增P0/P1/P2。组合回归确认一次外层execute内完成归位、重采、新session读取及新工具栏坐标、3–5年＋本科两行选中、提交与实际后验；没有智能体第二次派发。外部OS/浏览器/模型为替身，不能写作新安装版真机已通过。

全量npm test并未全绿：1116项中1104通过、8失败、4跳过，包含已有工具数量断言、祖先滚动fixture、旧dist孤儿用例及MCP工作进程限时中断。失败及中断保留，不扩修无关范围；明细见[计划§23.4](../plans/plan-runner-local-tool-recovery-lockup-fix.md#234-最小增量开发独立测试及codereview结果)和[开发验证证据](evidence/boss-cross-call-20261023/cli-window-resume-validation-20261024.json)。本轮未提交、推送、打包、安装或部署，原安装版及原会话未替换；真正的新版本单调用验收仍待后续发行安装。


### 6.15 安装版单CLI接续及恢复后跨轮续读已真验（2026-10-26）

用户安装Runtime0.0.29/BOSS0.3.27后核对4个关键安装模块与交付包一致。原agent2“打开boss”会话/26份入库保留；预检由既有工具恢复已关闭的Chrome并确认PHP职位及登录。随后用户手动右拖同窗，DWM框[1381,22,2793,1376]在2560×1440屏上真实超右233px，DPI96；本次稳定基线PID10416/HWND6492670/widget135890，不冒称与10-24是同一进程。

10:06:48只派发一次boss_filter。先在输入前发生cursor mismatch拒绝，none/not_started；page与window视觉均absent/inconclusive。原CLI于10:07:02.481自行平移一次，window重采明确repositioned=true、新epoch；10:07:11.199取得fresh DOM ACK，接回原setter，10:07:26.906外层唯一MCP终态OK/applied、38793ms，实际3–5年＋本科、筛选计数2及面板关闭均已核验。ACK前提取的脱敏原始结果直接证明state=recovered、resumed=true、steps=window_reposition_resume、attempts_used=2/ordinary_retries_used=0。不是由智能体再次调用完成。

11851个原生只读样本、最大间隔17ms，只有一次位置变化，原句柄/父链/可见性不变；归位后DWM[1148,22,2560,1376]完整屏内，尺寸不变。主任务43次CDP均ACK、原生click拒绝1/成功5，无导航/刷新。原窗口采图本次已及时保存本地，公开证据只记摘要；未持久化或输出outbox claim/permit等凭据。网页把成功码描述为null，原CLI回执明确OK，以原结果判定。

下一轮保持条件，不重新筛选，单boss_resume_next读取1份，网页10:10:33确认成功入库、累计27；姓名指纹与原26份无交集，无招呼或聊天。此次真机补齐§6.14的接续门槛缺口，剩余持续隐藏、二次超屏预算耗尽及混合DPI等未验项不外推。[验收证据](evidence/boss-cross-call-20261023/live-single-cli-recovery-20261026.json)、[原计划§25](../plans/plan-runner-local-tool-recovery-lockup-fix.md#25-boss0327单调用归位后筛选真机验收2026-10-26)。本次仅验收及文档同步，没有改代码、提交、打包或部署。
