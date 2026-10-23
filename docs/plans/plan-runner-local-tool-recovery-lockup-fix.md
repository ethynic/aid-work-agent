# 本地工具失败导致会话阻断：排查清单与修复计划

2026-10-23统一交接入口：[今日BOSS问题汇总与最小修复交接](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md)。今日修复以该文档的局部失败隔离、同卡提取及编辑阶段恢复口径收敛，不将识别全部分区/元素作为任务前提；本计划继续作为唯一进度台账，Phase 9/10/11已完成本次代码及独立验证，Phase 8及已完成快捷选项修复先核对复用。原始日志后的[最小方案W4](../design/recruiting/boss-overlay-heal-design.md#w4-原始日志核验后的最小修复方案2026-10-23)及第15节已获用户确认：真实超屏允许原窗口自动归位一次，随后重新截图和定位；Phase 8复用。禁止提交、打包或部署。原六个业务验收不扩为大矩阵。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 现场事实、同源异常与恢复入口审查 | ✅ 完成（2026-10-22） | 18项审查结果已分类；客户端原始CDP超时原因仍待补证；未执行故障注入 |
| Phase 1 | 派发前业务拒绝与致命错误分离 | ✅ 完成（2026-10-22） | 显式回滚成功后收敛业务失败，回滚/保存失败保持致命；隔离PG回归通过 |
| Phase 2 | 浏览器自动重建、CDP重试及续筛恢复 | ✅ 完成（2026-10-22） | 当前调用续做、半批图片保留、原profile及页面恢复已开发；真实Chrome业务验收待执行 |
| Phase 3 | 旧checkpoint恢复与原会话退出路径 | ✅ 完成（2026-10-22） | 同形旧状态在真实PG证明收敛/接回原ID，v1冻结请求兼容；现场旧会话未操作 |
| Phase 4 | Runtime传输重试、回执持久化与失权处理 | ✅ 完成（2026-10-22） | started重试、v1持久原回执、408/429及撤权停止通过隔离测试 |
| Phase 5 | 生命周期、错误证据及跨版本联调 | 🔧 进行中 | 原因分类、公开动作与Web回归通过；真实发布切换/视觉检查未执行 |
| Phase 6 | 独立测试、CodeReview及旧会话验收 | 🔧 进行中 | 独立测试及CR完成本批验证，详情见第9节；A1～A6真实业务与部署仍待授权 |
| Phase 7 | 崩溃残留锁修复与测试包准备 | ✅ 完成（2026-10-22） | Windows真实子进程及打包产物复验通过，已交付0.0.24/0.3.22验收包；未部署/安装/业务续做 |
| Phase 8 | 原生窗口零输入拒绝的自动恢复 | ✅ 完成（2026-10-23） | 独立212项及CR通过；0.0.25/0.3.23验包通过，现场A2待用户更新后验收 |
| Phase 9 | 推荐页候选配对、混合列表与发送事实修正 | ✅ 完成（2026-10-23） | 同卡片、离屏原绑定及逐人事实已实现；独立TEST/CR通过，见第16节 |
| Phase 10 | 筛选面板clip与编辑阶段恢复 | ✅ 完成（2026-10-23） | scrollport/原spec草稿与提交接续已实现；独立TEST/CR通过，现场未验 |
| Phase 11 | 窗口截图超屏的局部恢复与诊断 | ✅ 完成（2026-10-23） | 一次归位预算与旧观察失效已实现；独立TEST/CR通过，真实移动未验 |

## 1. 范围、基线与交付边界

目标：普通工具失败、派发拒绝或可核对的只读失败，不得被错误升级为整轮不可恢复；用户能在原会话继续、明确结束旧执行或获知真实不可恢复原因。已有简历、费用及招呼结果不丢失、不重放。

2026-10-22用户补充的产品要求：**浏览器/CDP故障默认自动恢复，浏览器被关闭也要自行重开、回到指定业务页面继续；不能仅因浏览器状态或CDP回执异常就把整轮交给用户核对。**允许重新走页面与读取流程，已完成的识别、扣费、入库和招呼沿用原事实，不从业务零进度重做。下文4.3、4.6和第6节六个业务验收落实此要求；属于待实施行为，不代表现版已经具备。

初始排查阶段仅复核源码和修订文档，不修改代码、任务或服务，不提交、部署或重试现场任务；后续授权实施及验证以第9、12节为准。共享工作区其他任务的未提交改动均保留，不将它们当成本任务已验证的交付。

### 1.1 审查基线

- agent2本次现场部署目录HEAD为 `abcd8e91`。已只读核对容器中的 `src/local_tools/repository.py`、`src/services/agent_runner/local_owner.py`，SHA256与排查时本地文件一致。
- 扩展静态审查固定使用本地Git对象 `99c62216`，避免共享工作区切分支后把旧代码当成线上基线。该对象包括已实施的恢复、模型识别和Runtime性能增量，不表示客户机实际BOSS插件逐字等于该提交。
- 编写时共享工作区在 `feature/ask-user-question-card`，HEAD `ae31f2b1`；本会话不切分支，不处理他人改动。实施前由开发核对当时主线、部署版本、Runtime/插件release与digest，再整合文档；禁止用当前较旧分支回退已完成修复。
- 现有方案：复用[Runner架构](../system/agent-application-architecture-design.md)、[共同契约](../system/runner-desktop-runtime-integration-contract.md)、[BOSS恢复设计](../design/recruiting/boss-overlay-heal-design.md)、[既有续筛恢复计划](plan-boss-resume-next-recovery-fix.md)。`99c62216` 中既有恢复计划已到Phase 10；当前分支若只显示Phase 1～5，不代表后续功能未实现。

### 1.2 证据等级

- **A：现场确认**。有具体invocation、错误栈、数据库状态或实际客户端回执；不等于所有底层原因都查明。
- **B：代码确认的阻断条件/风险**。已走读触发链，尚未执行隔离故障注入；不能写成现场已发生。
- **C：待补证**。列为实施前验证项，不直接据此改变生产行为。

“全部”限定为本次审查覆盖的Runner本地工具、BOSS CLI、Runtime传输及Web恢复路径。不是全仓无遗漏或整个系统不存在其他死锁的保证。下文包含需要保留的安全拒绝及其交互缺口，不能把所有拒绝都删除。

## 2. 现场基线与可重复核对的事实

### 2.1 下午：页面正常，续筛后反复中断

| 时间（2026-10-22，北京时间） | 事实 |
|---|---|
| 15:38:40 | `runner_bb4f221a75174b3c9fe98a3418caf5a8` 接受“再继续筛选” |
| 15:38:44～15:39:04 | invocation `902f28fa-d607-4303-924d-52b8b23f1d3d` 执行 `boss_resume_next`，返回 `EXECUTION_UNKNOWN / effect=none`；数据库state为unknown |
| 同一回执 | 原因 `CDP request timed out: Input.dispatchMouseEvent`；stage=input、input_status=unknown；resumes为空、attempted=0、stop_reason=page_operation_failed |
| 同一视觉诊断 | HTTP 200；模型返回obstruction=absent、goal_state=inconclusive，说明推荐列表无可见遮挡；不能据截图证明历史输入是否生效 |
| 同一恢复记录 | policy=readonly；转window诊断后没有有效diagnosis，最终state=diagnosis_unavailable；不是模型确认了页面存在阻挡 |
| 15:39:07 | 原Runner正常结束，回复让用户看页面后说“继续”；不是此刻仍在verification等待 |
| 15:39:59 | 新Runner `runner_524e32337adb45568c9e0625ee1f7053` 接受“继续” |
| 15:40:02 | 服务端创建新invocation前抛 `ResumeContinuationConflict: STALE_CONTINUATION`，随后转为 `RUNNER_USAGE_STORAGE_FAILED`，工具约545ms中断 |
| 15:41:34～15:41:38 | `runner_5992aec1163d4c5397e3191b39666822` 接受“继续筛选”，同一冲突再次导致中断，工具约546ms |

两个后续Runner均沿用最后成功父游标 `483ad307-cb2b-40b4-a9d8-24db59ff33de`；该父游标已有非retry-safe后继 `902f28fa-...`。两次冲突都没有派发新的客户端操作。

旧状态已核对：`5992...` 为interrupted，pending仍有boss_resume_next；工具phase=dispatching、invocation_id为空，local_invocations为空，仅 `resume.next.request` 领域阶段completed。`524e...` 后续已cancelled，不能伪装为同一执行仍可resume。

用户界面基线：原会话 `session_8c3c333e81e7` 已有14份入库和2次招呼成功。验收要核对真实业务/费用记录；这里不保存候选人姓名或简历正文。

### 2.2 上午：50份任务在25份后失联

`runner_322b33c7640d46a28a81a1e6ae3ec45f` 的25份evaluation、fee、insert、match均completed。下一批invocation `84ab6890-0428-47c0-86f1-c2f0d2eac841` 在10:34:07被领取，无started、progress和result，10:35:26被租约回收置unknown；deadline_at为空，不是旧180秒截止。

同一时段10:33:32部署目录更新，API约10:34:16重建、worker约10:34:47重建。相关性已确认，客户端为什么没有上报started仍待10:33～10:36的Runtime日志；不能将部署写成已完整证明的唯一原因。后续Chrome不可用发生在显式停止受管资源后的核对中，不能倒推为最初原因。

### 2.3 历史回归样本

- 10月20日 `tr_runner_d470a2a18e114be58ccbfe8823523e2a`：六份图片取回，前三份入库，第四份GLM 400/1210被包装成核验等待。基线代码已有明确Provider结果分类、单份失败持久化及有限尝试处理，作为回归样本，**不重复实现旧缺陷**。
- 10月20日 `tr_runner_5941e6c15adf45a1b487e0209576ca1a`：旧持久180秒deadline与客户机慢输入叠加。基线新durable调用已不写默认总deadline，已有性能/无进展埋点；本计划不重新加总时限、不删除旧记录、不宣称内存泄漏已确定。
- 10月20日 `tr_runner_7df97d4956eb4b4d8c2da19bca229d57`：设备调用失联与同会话恢复拒绝。已实施的迟到回执、结束等待、受管停止证明须保留并回归，不另建一套恢复协议。

## 3. 审查清单：18项阻断机制与风险

优先级是开发顺序，不是声称所有项已现场复现。路径相对仓库根；行号以固定基线为准。

| 编号 | 等级/优先级 | 触发条件、影响与证据入口 | 必须达到的修复结果 |
|---|---|---|---|
| F01 | A / P0 | `repository.py:create_invocation_in_tx` 的游标冲突先经 `local_owner.py:bind_invocation/_fail` 污染usage_scope；外层proxy虽捕获STALE，后续check仍抛存储致命错误 | 业务拒绝成为有原因的工具结果，不能把整轮标成存储故障 |
| F02 | B / P0 | 同一bind路径的 `DeviceOperationUnresolved`（设备离线/未就绪、恢复繁忙、原操作待核对）也会先进入_fail，外层返回普通拒绝无效 | 逐类核对是否已派发，已知准入拒绝不污染用量致命状态；仍不允许绕过设备授权 |
| F03 | A / P0 | `continuation_retry_safe` 只接受failed及有限错误码；readonly、effect=none、零成果但state=unknown的902f记录永久占父游标 | 自动核验、重建页面/游标并接续；部分成果可重走读取路径且复用已完成事实，未知外发不重复发送 |
| F04 | A / P1 | `errorMapping.ts` 按timeout/timed out统一映射CHROME_UNAVAILABLE，Input事件回执超时被描述为端口未开 | 区分连接、session/target、单条CDP命令和原生输入错误；不能靠报错字符串推断没登录或有弹窗 |
| F05 | A / P0 | `localOperationRecovery.ts` 对readonly的input unknown且obstruction absent转window诊断；仍要求旧目标已完成，而不是验证现在是否可安全续读 | CDP重连、浏览器/页面重建和语义步骤重试纳入自动恢复；诊断不可用不阻断确定性续读，不要求模型证明过去未收到的回执 |
| F06 | A / P1 | window诊断记录只有branch/mode，最终diagnosis_unavailable；observe只投影visual字段，原operation错误被丢掉 | 保留脱敏code、失败子阶段和是否调用模型，不把诊断失败说成页面仍有问题 |
| F07 | A状态+B恢复 / P0 | 5992旧checkpoint为dispatching但无invocation；`can_restore` 在ref缺失时直接False | 对旧“派发未提交”状态做有证明的收敛；不能仅修未来调用 |
| F08 | B / P0 | `end_waiting` 要求存在设备引用及未知本机事实；无invocation的派发前中断不适用，可能恢复和结束入口都不可用 | 通过既有取消/终态收尾释放逻辑占位；不得制造设备hold或伪造停止证明 |
| F09 | A历史+B基线 / P1 | Web发送仅clarification可reply、无wait的paused/interrupted可resume，waiting/verification被普通输入guard挡住 | 展示匹配实际状态的可执行入口；明确消息在草稿、受理或消费哪一层 |
| F10 | A / P1 | 原工具已失败且游标不可续，助手仍承诺“说继续就接着做”；只显示工具开始进度后又泛化中断 | 从受信状态给操作选项，保留真实错误与成果；不可让LLM承诺绕过恢复门禁 |
| F11 | B / P1 | Runtime `started` 捕获任何ApiError即return；apiClient把500/502/503/504也包装为ApiError | 瞬态HTTP错误退避重试原started；409/撤销等确定失权必须零新输入退出 |
| F12 | B / P1 | v1 `sendFinalResultLegacy` 捕获任意ApiError就放弃原结果；单次503会失去回传机会 | 原invocation/原结果幂等重传；不重新执行工具 |
| F13 | B / P1 | v1结果仅内存重试，Runtime关闭明确记录“结果丢失”；v2已有outbox，两条路径可靠性不同 | 复用现有回执持久设施覆盖旧工具路径，ACK前保留；不另建通用后台服务 |
| F14 | B / P1 | v2 outbox将所有4xx视为确定拒绝并gave_up，包含可能瞬态的408/429 | 按状态与机器错误码分类并尊重Retry-After；401/403及绑定错误不能盲重试 |
| F15 | B / P1 | ProgressForwarder遇404仅停止续租，不触发执行中止；DeviceRevoked又与NetworkError一起按离线继续处理 | 确定失权应阻止下一次输入并保留已发事实；网络未知与明确撤销分开，不签假无效果回执 |
| F16 | B / P1 | worker心跳异常只interrupt/cancel，未记录原始原因；storage/lease/部署退出在界面混成中断 | 脱敏区分失权、存储、明确停机和未知心跳故障；不因观测失败授予新执行权 |
| F17 | A现象+C根因 / P1 | 领取后尚无started，续租也未开始；发布切换时归属消失后收敛unknown | 验证领取到started、worker更替和迟到结果的完整链；不把客户端真实执行状态等同于云端worker状态 |
| F18 | B / P1兼容门 | 旧任务无invocation、实际未知写、配置/adapter变更、Runtime能力不足、设备hold的可恢复性不同，泛化“继续”不能覆盖；设备hold可影响同设备其他会话 | 返回准确可用动作与阻断原因，区分会话释放和设备释放，明确支持的旧版本/旧状态；保留权限与未知写保护 |

### 3.1 同源复核项与明确保留的保护

1. `RunnerBrowserOwner._fail/_root_transaction/_observation` 也有宽泛存储包装；未取得Browser业务拒绝导致会话锁死的现场证据。Phase 1逐个调用者分类后才能决定是否修改，不能全局放宽Browser外部动作。
2. `UsageScope` 在真实receipt/checkpoint落库失败时中断是正确保护。断库、提交结果未知、身份/意图不符、owner失权必须继续阻止新动作；修复不等于所有异常都忽略。
3. v2已存在网络/5xx重传、原ACK保存及outbox，不重做；Web提交/control也已有原幂等键退避机制，网络错误后pending保留本身不是已证明缺陷。补断网/重连/刷新/切会话验证，防止错误清键造成重复受理。
4. 新durable调用无总deadline、迟到原回执的 `_late_result`、单份模型失败处理已存在。以兼容/回归为主；旧deadline、unknown费用事实不能被硬改为成功。
5. 原始CDP超时究竟由Chrome线程、目标/session、客户端调度还是其他因素导致，及window诊断失败的原始错误仍未完整取得。F04～F06可修错误分类/恢复缺口，不能据此声称底层超时已根治。

### 3.2 过度设计/门禁复审（2026-10-22用户纠正）

结论：此前把问题清单、内部异常分支及全量交付要求放在一起，导致验收过度展开；CLI改动写得过于概括。现只保留第6节六个业务验收，内部异常按实际改动做自动化回归。以下收敛规则优先于本文其他泛化表述，不撤销其他会话已经取得的开发授权。本排障会话只修订文档，不改正在实施的代码。

| 应删减/纠正的门禁 | 最小替代规则 |
|---|---|
| readonly恢复也须先通过视觉schema、配对环境检查和模型诊断 | 确定性CDP/页面恢复先执行；视觉字段校验和配对条件只约束真正需要视觉API的分支。诊断不可用不妨碍只读续筛 |
| 原tab、session、浏览器实例ID必须保持 | 它们是连接定位，可重建；既有设备/账号/职位意图保持即可，不增独立的“原浏览器实例证明”人工门禁 |
| 必须有“曾由本启动器拉起”的新磁盘凭据才允许重开 | 已有受信本机配置能确定原持久profile即可重开，手动启动的正常调试Chrome也须支持；确实无法确定原profile时才报告该具体缺失，不能强开陌生profile |
| 头像/显示名摘要缺失或变化就判未登录，每个点击前重新全页核验 | 优先使用已有可靠登录/账号信号，头像摘要只作辅助。连接建立、页面重建或检测到切号时检查；外发前保留原对象校验。DOM头像变化/加载中不能独立判定失登 |
| 缺少新增page_context、filters或recovery字段就阻断旧任务普通读取 | 当前页面仍满足原业务条件时直接读取；需要重建时从原任务参数/已保存有效筛选恢复。可选字段仅影响相应恢复能力，不能成为所有调用新前置条件 |
| 只读页面必须证明旧点击没发生，或重新扫描也被禁止 | 回读后态、重新定位并重试语义步骤；允许重走列表和读取。已完成业务结果按既有ID/逐份记录复用，读取路径无需“恰好一次” |
| 浏览器修复必须等待所有恢复API、outbox、跨版本全矩阵及旧事故根因查清 | CLI与服务端续聊补丁分别验证、按真实依赖组合交付；未触及路径不强制重做。原始Chrome无ACK原因继续调查，不阻塞已验证的自动恢复补丁 |

保留的核心约束只有既有权限/取消、避免并发执行、真实持久化失败不得虚报成功，以及未知招呼/消息不盲目重发。它们不能被扩展成每次浏览器读取的新增审批或全字段证明流程。

## 4. 最小修复设计

### 4.1 错误必须分层，不再共用“存储故障”兜底

| 类别 | 允许的结果 | 不能采用的处理 |
|---|---|---|
| 派发前明确业务拒绝 | 结构化工具失败/重核验指引；证明未提交后收敛叶子 | 把一般ValueError全部当可重试，或先设置fatal再在外层吞掉 |
| owner/租约/用户停止 | 保存最后可信状态，禁止旧owner新IO，显示原原因 | 伪装数据库损坏，或凭新worker存在就重发桌面动作 |
| 浏览器/CDP或只读页面状态故障 | 自动重连、重开及恢复业务页面；保留成果并核验后接续读取 | 仅凭超时或诊断不可用要求用户核对，或把页面恢复当成业务全部未执行 |
| 外部写动作结果未知 | 只观察原事实或显式合法结束，禁止自动再发送 | 将只读规则用于打招呼、发消息、接收简历或标记不合适 |
| 真实存储/凭证/身份/恢复不变量失败 | 停止推进，暴露准确安全原因，等底座恢复 | 为可用性删校验、补假receipt、清未知状态或强制置completed |

实现优先在异常源和owner边界修复：采用明确类型/错误码及事务结果证明，不能按字符串包含STALE或按所有ValueError放行。保留 `UsageScope.check` 对真实账本故障的保护。未分类的异常不能擅自继续执行，也不能统称存储故障。

### 4.2 原子派发边界与旧checkpoint恢复

在 `local_before_bind` 之后发生拒绝时，必须判断实际绑定事务：

1. 在原runner/execution/tool_call/branch/ordinal及可信身份下，查询准确business_ref、dedupe key与请求摘要。使用既有事务和锁边界，不用“当前没看到invocation_id”证明没执行。
2. **确定未提交**：以同一owner CAS收敛为已完成业务失败并返回原错误，沿用5.1已选定的实现，不再保留“重置prepared”第二方案。
3. **已提交**：只能接回原invocation/原回执，不新建、不更换设备，不让旧worker提交。
4. **无法确认**：保留未知，给出真实原因和合法结束路径；不得凭客户端页面看起来正常补造未执行证明。

兼容处理进入正常受权恢复/控制事务，不写修库脚本、不批量改线上状态。配置、adapter及资源不兼容仍明确拒绝恢复；用户可读历史，底座正常且安全收尾后可在原会话开始新意图。

### 4.3 只读游标与页面重建后的自动接续

从CLI已有工具分类确定读取/页面操作与外发的区别；服务器沿既有绑定、原结果和逐份记录接续。只读恢复不新增全字段证明协议，不要求视觉诊断成功，也不要求证明旧点击未生效。未知招呼/消息单独处理，不能仅凭`effect=none`重发。

- 零成果：保留原失败记录。自动核对原业务目标、当前列表/详情及职位；必要时按4.6重开浏览器和指定页面、重新设置筛选、重建游标。在父游标锁内签发唯一后续尝试，继续排除已处理候选人。
- 有部分成果：先保留本批已读取资料，再按原invocation逐份保存/复用已有识别、扣费、入库和匹配事实。允许从业务入口重走列表、翻页及只读采集，沿既有已处理集合排除完成项；已完成结果不重复识别、扣费或入库。当前代码主要按candidate_name去重，不能假称已有全局稳定候选ID；同名歧义沿原有消歧规则处理，不以新建候选身份系统作为本补丁前提。
- 数量口径：从逐份已完成结果计算本次目标的剩余量，不能将processed_names、失败卡片数或尝试次数当成已完成数量。临时输入失败不提前加入永久排除集合；内容确实不可读则记录单份失败并继续其他候选。列表刷新允许重新扫描，达到真实候选边界时准确报告数量，不用解除筛选或循环读取重复人补足目标。
- CDP target/session/tab是可重建的连接定位，不把其旧ID当成不可变业务身份。固定设备、受权浏览器profile/账号、职位、筛选意图及已处理身份仍须核验；重建后的新tab不能借机换账号或职位。
- 原调用仍active时由唯一有效owner在现有调用内恢复，不并发派发第二次操作。原调用已失权须先按既有协议收敛再接续；未知外发独立核对，不把它升级为整个会话不可用。
- 两个“继续”并发只能消费一次续筛权。STALE应返回最新受信进度/可用动作，不能污染usage_scope；不可简单删除successor或把unknown改为failed骗过门禁。

落点沿用`repository.continuation_retry_safe`及父游标锁；`prepare_next`和`continuation_arguments`共用相同排除名单口径，不分别新增一套恢复资格。修改`failed_attempt_names`时区分临时输入失败与最终单份失败，避免页面恢复后当前候选仍被当成已完成跳过。旧失败原记录保留，后继资格由真实读取类别/成果及现有调用归属决定，不引入通用证明schema。

复用现有 `continuation.resume`、原回执和领域后态检查，不新增本地Agent loop或第二套恢复器。视觉模型用于判断可见障碍，确定性代码负责输入前条件与恢复位置；不能要求截图证明历史CDP ACK，也不能让模型替代业务成功信号。

### 4.4 同会话的继续、结束与设备hold

至少区分五种可见状态：可重核验的只读失败、确定未派发、真实未知外部写、正在等待原回执、不可兼容旧执行。动作由服务端事实派生，并经原权限校验；前端和LLM不能自行创造继续授权。

- 对F07旧状态给原会话恢复或正常结束入口；没有设备IO就不制造本机hold，也不要求客户端提供不存在的停止证据。
- 已cancelled的 `524e...` 不复活；在原会话中沿用经验证的历史和已处理名单创建合法续筛执行。`5992...` 可恢复与否按4.2证明决定，不要求用户新建会话。
- 原会话可结束等待不等于设备可立即执行。原调用仍在执行时遵守设备独占；执行已停止而仅剩招呼/消息结果未知时，按该项核对、禁止重发，不扩大为禁止整个会话读页面。复用既有回执和受管停止核对；无法证明执行已停止的资源hold保留，不能把不同设备/用户的状态混在一起。
- 输入在草稿、已受理、已消费或被拒绝必须区分；没有提交的“继续”不能被描述为模型重试失败。
- `CHROME_UNAVAILABLE` 出现在停止资源后的观察时，应独立展示“停止证明结果”和“当前页面观察结果”，不能倒推旧故障原因。

### 4.5 Runtime原请求重传与终态保障

统一按endpoint、HTTP状态和已知机器码分类，而非所有ApiError/所有4xx同义：

- 网络、500/502/503/504及可重试408/429：退避、必要时遵守Retry-After，重传同一started/同一结果，保留原身份和绑定。
- 401/撤销、明确绑定不符、原调用已失权的404/409：停止新的业务输入，保留已发生事实，不为恢复临时故障绕过确定拒绝。
- 重试started在租约已经到期后收到409，必须零Provider新调用；不能延长旧授权或重新claim成另一操作。
- 为v1原结果复用现有outbox，区分wire版本及payload，ACK前不丢失。启动只能补传原结果，不能重执行；不把凭据或简历原文写入排障文档，持久回执沿用既有保护机制。
- 对progress失权触发协作停止下一次输入；网络中断与明确撤销分开处理。停止不是宣称已发鼠标/消息未生效。

不恢复健康后台任务的总墙钟deadline。CDP单命令超时、无进展诊断、设备续租及任务观察分别处理；断开Web订阅不取消任务。

### 4.6 浏览器/CDP故障自动恢复顺序（用户补充，待实施）

1. **先回读再重试**：命令超时先检查连接和当前业务后态。读取、导航、筛选设置、展开/关闭详情等可重复页面步骤，若后态已达成就直接续做；否则fresh定位后重试该语义步骤，不复用旧坐标、不机械重放整串鼠标按下/抬起事件。
2. **重连并重建页面会话**：CDP断开、target/session失效或tab关闭时，重新发现原受权浏览器；重新连接或建立页面，导航到保存的业务入口，恢复职位与筛选条件。不能把所有连接异常统一解释为未登录。
3. **浏览器关闭后自动重开**：由已有受管生命周期入口在固定设备上使用原受权profile启动Chrome并重新建立CDP；浏览器进程关闭本身不视为用户取消任务。不得清profile、清Cookie、杀无关Chrome或另开陌生账号。用户明确取消/停止或撤销授权时，立即停止自动恢复，不反向重开。
4. **重新进入指定页面并接续**：旧详情/列表无法恢复时，允许从BOSS业务入口重新进入职位、设置筛选、翻页、读取；按4.3的已完成集合去重。无需人工确认历史CDP点击是否得到ACK，也不要求用户再次发送“继续”。
5. **诊断失败不成为唯一门禁**：视觉不可用、window诊断失败或goal=inconclusive，均不能阻止已有确定性连接、导航及只读重建路径；视觉结果仍不能证明外发成功或授权新外发。
6. **避免无限忙重试，任务持续可恢复**：浏览器/CDP瞬态故障在当前CLI invocation内做确定性退避恢复，Runtime照常独立续租，Runner观察原调用；不假定已有通用的后台自动唤醒调度器。每轮只有一次页面步骤重试和一次会话重建，复用已消耗次数；仍失败时按1、2、5秒至10秒封顶的间隔退避，无任务总截止。每次成功推进后恢复间隔重置。等待可取消并输出进度，不反复付费诊断、不在Reader/wrapper/Socket各加一层重试。
7. **实际需要用户的条件准确呈现**：确实未登录、验证码/人工验证、缺少可确定的原profile等，准确报告并交回已有等待/续聊路径，保留成果；用户解决后在原会话接续。设备/Runtime进程已退出时不能假装CLI仍在自动重试，按原结果观察和旧执行恢复处理。普通CDP超时、浏览器关闭和页面变化不能直接归入人工依赖。真正未知的招呼/消息查原出站事实；已发送复用，可信未发才执行。

持续退避只用于连接拒绝/断连、target失效及命令无回执等瞬态错误；不支持的协议方法、参数错误、真实权限/付费限制或无法识别的页面结构不能同样无限重试。单份内容错误记录后继续其他候选；原目标确实无法恢复时保留进度、准确报告可解决的依赖，不制造“仍在自动恢复”的假进度。

优先复用CLI现有恢复wrapper、导航器、浏览器启动器和Runtime续租；若现有入口不支持原profile重开，则补齐该内部能力及组合验收，不把缺能力变成永久人工门禁。该策略覆盖既有设计“原tab保持、直接输入异常零普通重试”的冲突部分：允许重建tab，并对只读/可重设页面动作核验后重试；故障恢复不重新引入Escape，未知外发仍按原事实处理。详见[BOSS设计新增W节](../design/recruiting/boss-overlay-heal-design.md#w-浏览器cdp故障自动恢复2026-10-22用户补充待实施)。

### 4.7 最小实现闭环与职责（落地复审结论）

| 组件 | 本次唯一职责 | 不承担的工作 |
|---|---|---|
| BOSS CLI | 当前业务步骤的后验、重试、CDP重连、浏览器/页面重建，保留本批资料 | 新建本地Agent、跨任务调度、重新推理筛选目标 |
| Runtime Host | 原invocation执行、独立续租、取消、原回执补传 | 判断职位/候选人或重复执行整项工具 |
| Runner/后端 | 接纳原结果、逐份处理和费用、修复游标/异常归类及原会话接续 | 再跑一套浏览器视觉恢复、从模型文本猜客户端是否执行 |
| Web | 显示进度、保留补充输入、查询/停止及原会话继续 | 因订阅断开取消任务或并发派发第二个筛选任务 |

实现落点：Reader持有当前批次的resumes/failures/完成集合，在语义步骤边界调用现有恢复wrapper；wrapper调用会话工厂重新连接，并让后续依赖切到新session。只需当前operation内的session替换入口与统一重试计数，不建通用恢复框架。首次connect尚无browser_target_ref时，沿本次受信operation及本机profile配置恢复，不因缺少尚未生成的字段拒绝重连。

会话重建不再次调用整个`operation.execute`吞掉原半批数组。若必须退出CLI，先返回已有资料和准确stop_reason，由原服务端逐份保存后接续剩余量；没有成果不能伪装成功。输入、导航恢复只保留短期重试状态，持久事实仍用原invocation回执/领域checkpoint，不新增恢复队列或一张恢复任务表。

进程直接崩溃时，未回传的纯读取图片允许重新采集，不能承诺内存成果永不丢失；云端已完成结果仍复用。本次不为此新建每张图片本地事务日志，原最终回执的补传继续由Runtime outbox负责。

浏览器恢复期间Web可查询/停止，补充消息按现有机制保留到下一安全点应用；不承诺另起一个模型/Runner与当前设备调用同时执行。用户取消、Host失权或进程退出打断重试；单条连接/握手/命令仍有局部超时，均不变成整轮180秒截止。

旧会话兼容优先级：能在原冻结release上安全恢复的调用接回原ID；需要新版BOSS能力时，原执行正常收敛后在**同一会话创建使用新版插件的新执行**，沿用经验证的原设备、职位及已完成结果。已接受invocation的release/digest不改，不为了“原会话继续”设计热替换代码协议。UI需正常释放旧执行占位，不要求用户新建会话。

## 5. 实施阶段与文件范围

| 阶段 | 最小实施范围 | 完成门 |
|---|---|---|
| Phase 1 | `local_owner.py`、`local_invocations.py`、`proxy_tool.py`、必要的内部异常类型与测试；复核browser_owner同源包装 | STALE和设备准入拒绝成为普通结果，且真实断库/提交不明仍fail loud；不以本阶段单独上线宣称旧会话恢复 |
| Phase 2 | **首批CLI修复**详见5.3：`CdpSocket.ts`、`CdpGateway.ts`、`bossContext.ts`、`localOperationRecovery.ts`、`ResumeBatchReader.ts`、`bossResumeBatch.ts`、`ChromeLauncher.ts`及错误映射；后端游标文件只处理接续依赖 | CDP超时能本地回读/重试，tab/浏览器关闭能恢复，已有批次成果保留；不以仅改服务端拒绝逻辑替代CLI修复 |
| Phase 3 | `local_recovery.py`、`recovery.py`、`local_wait.py`、既有控制/终态接口、Web原会话入口 | 旧dispatching无调用状态可证明收敛；不可恢复状态也有准确退出；cancelled不复活 |
| Phase 4 | 共享Core `apiClient.ts`、`invocationRunner.ts`、`resultOutbox.ts`及Runtime适配/启动重投 | 503/429/丢ACK/重启不丢原回执、不重复业务；失权后无新输入 |
| Phase 5 | CLI错误映射/观察诊断，Worker错误原因、public projection、Web状态/输入；原设计/契约兼容记录 | 页面正常与业务未知分开；每次继续可追踪到受理、拒绝或未提交；升级不意外使旧会话再次失效 |
| Phase 6 | 项目风险流程要求的独立测试和CodeReview，以及实际受影响组件的启动检查 | 按本批范围验证第6节业务结果；遗留补证项如实列出；部署和现场续做仍待用户明确授权 |

实施按项目 `.claude/rules/dev_workflow.md` 及项目dev-workflow skill执行；本次纯调研/计划不启动开发三智能体流程，不把计划完成当代码完成。

### 5.1 共同接口与并行写入约束

实施检查点：共同契约2.6登记v1 outbox分目录兼容、CLI本机binding可选恢复信息及公开恢复动作投影；Device wire不变。无invocation旧叶子统一选择“证明未提交后收敛为已完成业务失败”，不重置prepared并盲目重派发。查询必须在原Runner行锁/身份/phase/dedupe及business_ref下完成；发现真实绑定只允许接回原ID，无法证明则保留未知。排查时旧分支HEAD为ae31f2b1，实际开发已迁移至master的9a7ce608d，保留其迟到回执/无总deadline/结束等待等已有能力，不批量迁移其他功能。

计划不直接变更现有wire。内部错误分类及数据库无invocation证明优先沿现有接口实现。若增加公开恢复动作/证明字段、扩展outbox持久格式或改变CLI回执语义，实施前必须在共同契约登记版本、唯一写入者、旧producer/consumer和旧任务兼容矩阵；需新API时不能只改前端按钮。

后端owner/仓储/恢复、CLI领域恢复、Host传输/outbox、Web各自边界明确，主控整合。共享入口、错误语义及绑定字段仅一个写入者；独立验证者不直接改实现。不得借本计划新建通用状态机、第二个本地执行循环或全局重试框架。

### 5.2 未查明根因的补证与判定

- **15:38～15:40 CDP命令无回执**：补同设备Runtime/CLI原始日志及实际release/digest，按invocation关联命令发送、接收、超时、target/session失效和连接关闭时间。优先用已有输入分段、队列等待及事件循环延迟埋点，区分命令未发出、Chrome未回ACK、回执已收到但本地未处理；仅有`Input.dispatchMouseEvent`超时不能确定鼠标被占用、页面丢失或内存不足。
- **window诊断无结果**：保留调用是否发起、失败子阶段、脱敏机器码/HTTP状态和耗时，核对是本机窗口捕获、请求构造、传输、模型返回还是解析失败。没有原错误前，不把`diagnosis_unavailable`等同于模型判定页面不可用。
- **10:33～10:36 claim后无started**：关联同一claim、客户端started尝试及HTTP结果、服务端接纳记录、进程退出和部署切换时间，验证F11是否实际发生，或客户端在发起started前已经退出/阻塞。不能只凭部署时间重合就关闭调查。

已有日志不足时，在隔离环境增加上述最小元数据后复现；不记录设备凭据、简历正文、原图或任意页面内容。修复恢复路径可以独立验收，但上述原因未补齐时，报告继续标为待证，不宣布原始卡顿已根治。

### 5.3 BOSS CLI首批修改清单与执行顺序

以下路径均位于 `clients/boss-resume-assistant/src/main/`。原事故源码链以`99c62216`为基线；复审时共享工作区已出现其他会话未提交的CDP、启动和页面恢复增量，尚未由本会话测试，不当成已验证修复。开发应补齐已有实现，不再另造一套恢复模块。

| 顺序/文件 | 已核对的问题或职责 | 必须落实的修改 |
|---|---|---|
| C1 `cdp/CdpSocket.ts`、`cdp/CdpGateway.ts` | 请求send有超时，但WebSocket握手等待open/error、`/json/version`请求缺少本地有限等待；`dispatchMouse`原样抛错 | 补连接/握手超时、取消和失败清理，防止无限挂起；保留连接/命令/target错误区别。沿现有日志保留请求ID、脱敏session、鼠标事件type、发送/回执/超时阶段及耗时，区分mouseWheel与按下/抬起；传输层不偷偷重发所有Input |
| C2 `operations/bossContext.ts` | `clickBrowse`依次等待mousePressed、mouseReleased，`mouseWheel`等待mouseWheel；任一步异常会直接抛出 | 将异常交给页面步骤恢复，明确失败发生在哪一步；重连后提供可用session，旧连接可靠清理。处理可能残留的按键态，不在未知旧target上盲补事件；恢复后fresh定位再点击 |
| C3 `boss/ResumeBatchReader.ts` | 打开卡片的再次点击只在首次输入正常返回后才有机会执行；`input()`抛错直达batch外层catch，置page_operation_failed；详情收尾失败也直接结束批次 | 在打开详情、滚动和关闭详情的语义步骤接入现有恢复wrapper；Reader保留数组并在换session后继续同一批。临时操作失败不永久排除当前候选；有效图片先加入成果，关详情异常不能丢当前份 |
| C4 `boss/localOperationRecovery.ts` | `admit`依赖视觉上下文结构，配对检查先于诊断；readonly输入unknown且无阻挡仍转window，最终可能diagnosis_unavailable，阻断原操作续做 | 抽取现有wrapper的确定性恢复供步骤边界复用，先连接/后验再按需视觉诊断；同一owner统一计数，外层不递归恢复或整批重跑。首次连接无target_ref不适用视觉准入门禁 |
| C5 `chrome/ChromeLauncher.ts`、`boss/browserTargetBinding.ts`、`operations/bossContext.ts` | 已有固定持久profile启动器；新未提交增量加入managed-profile证明及page_context，存在把手动调试Chrome/旧任务排除的风险 | 复用现有启动器和可信本机profile配置，不增加第二本授权账；tab失效重建页面，浏览器关闭自动重开，尊重明确取消。旧任务缺新可选字段时可补采或沿原参数恢复，不能一律拒绝 |
| C6 `boss/PageNavigator.ts`、`boss/JobSwitcher.ts`、`boss/FilterSetter.ts`；已有`boss/browserPageRecovery.ts`增量 | 页面恢复需要回指定职位/有效筛选，不能只打开BOSS首页就声称恢复 | 复用现有导航、选职位及筛选设置；只改确有缺口的调用点，不重写正常Executor。当前页已满足无需重设；头像DOM不可读不能等同未登录，更不能每点一下重做全页身份诊断 |
| C7 `operations/bossResumeBatch.ts`、`operations/errorMapping.ts` | 原始失败回传与批次成果、恢复事实必须一起保留；仅调整提示文案不能恢复运行 | 传回原资料/已处理集合与准确阶段，CLI可恢复的异常先消化；最终仍失败才报告具体依赖及剩余进度。复用现有错误码，新增内部typed error不必新增Device wire |

接线核对：本次复审的`bossResumeBatch.ts`把Reader的`click`接到`session.click`（Win32），把`scroll`接到`session.mouseWheel`（CDP）；不能把`clickBrowse`的存在当成批次卡片点击正在走它。现场错误只给出`Input.dispatchMouseEvent`，缺少事件type，尚不能判定是mouseWheel还是按下/抬起。必须覆盖实际scroll等调用链，不能只修改未被该批次使用的clickBrowse，也不借修复擅自切换正常输入方式。

实施顺序：先交付C1～C4及C7的最小闭环，即超时可恢复、半批成果不丢、最终回执正确；再补C5/C6的重开和页面重建。服务端Phase 1/3解决“继续→STALE→中断”及旧会话，Runtime Phase 4独立处理回传。原阶段编号仅作进度登记，不要求按编号串行全部做完才能交付；这里只描述工程安排，不要求新建会话或向其他会话发消息。

## 6. 六个业务验收

验收只看以下六个业务结果，不再保留原37项逐项签字清单。每批补丁验证受影响场景；整体验收覆盖六项。异常注入在隔离环境执行，部署及现场任务续做仍需用户明确授权。

| 编号 | 场景 | 通过标准 |
|---|---|---|
| A1 | 正常筛选50份 | 有足够候选人时完成目标，中途不固定停在25份、不因总时限停止；候选人确实不足则准确说明已完成数量和原因 |
| A2 | 筛选中连接异常或原生窗口零输入拒绝 | CDP超时/断连自动恢复；render widget hidden先重新激活、探测并fresh定位重试，必要时复用已有读取通道；诊断不可用不单独阻断，不要求人工F5 |
| A3 | 筛选中关掉tab或浏览器 | 自动恢复原登录环境、返回指定职位及筛选页面，接着完成剩余数量 |
| A4 | 中途失败后在原会话说继续 | 能接续剩余工作，已有成果和招呼不重复；筛选后批准名单仍在推荐页定位发送，沟通/收附件/面试等后续任务进入沟通页；覆盖旧故障状态，不能只测新会话 |
| A5 | 真正未登录，随后完成登录 | 准确提示登录依赖；登录后原会话可接续，不要求另开会话 |
| A6 | 正常执行或自动恢复中点击停止 | 停止后不再发新操作、不重新打开浏览器；原会话仍可交流、查看已有结果 |

### 6.1 开发侧回归（按实际改动）

CDP握手、鼠标事件超时分支、连接清理、回执重传、事务冲突及去重等由开发针对修改补自动化测试，不拆成人工验收项，不要求未改动模块全量重测。纯CLI测试复用真实wrapper/Reader及替代CDP端点，不强制启动PG或付费模型；修改服务端事务/费用时再验证对应真实实现。保留项目要求的独立测试与CodeReview。

A4的旧状态先在隔离环境构造现场同形数据验证，包含dispatching无invocation及父游标被零成果只读unknown占用；不能用mock恢复成功或删除旧记录代替修复。已有cancelled执行不复活，原会话可合法建立接续执行。未知招呼/消息核对原结果，禁止盲目重发。此处是A4的开发验证说明，不另增加人工验收关卡。

## 7. 发布、回滚与完成标准

- 分批实施并完成项目要求的独立测试与CodeReview；CLI恢复补丁通过受影响场景及对应自动化测试可独立交付，服务端旧状态修复另验。声明“原会话已恢复”须通过A4，不把它变成其他独立补丁的前置门禁。
- 发布只更新实际受影响组件，列明依赖版本和兼容性；无共同接口变化的CLI内部修复不强制重建后端/Web/Runtime。若声明端到端原会话恢复，必须包含两条故障链所需的实际组件及兼容验证。
- 既有存量task仍保留原身份、设备、意图及release绑定；新版内部兼容处理不能偷偷迁移正在执行的旧调用。已存旧deadline和receipt不删除。
- 不采用批量SQL清unknown、删除旧cursor/receipt、删除会话、重置费用或自动重发招呼作为恢复方案。
- 发布前记录在途任务及drain/观察方案。无用户部署指令不重建容器；任务中途发布的故障注入仅在隔离环境执行。
- 回滚保留新旧回执及恢复审计。新增可选字段/持久格式需证明旧版能忽略或明确拒绝；不能回滚后让已知未知写变成可自动重试。
- 每批完成标准：本批缺陷有对应代码和有效验证，未处理的审查风险标为后续或不相关。全计划最终收尾核对六个业务结果及本批修改；CDP底层超时/window诊断原始原因可继续补证，不阻塞自动恢复补丁交付，不宣称所有卡顿已根治。

## 8. 本次文档验证记录

完成只读源码审查及既有现场记录整理，未运行产品、未重放模型/设备调用、未运行开发测试。按用户纠正将验收压缩为A1～A6六个业务场景；再审纠正了不存在的调度假设、半批成果保留、重试单一归属、数量口径及冻结版本下的原会话接续，见4.7。交付前核对编号、文档链接、共同契约一致性并执行`git diff --check`。本任务仅修订文档，其他会话代码不作处理；计划审查完成不表示实现或运行验收已通过。

## 9. 开发实施与独立验证记录（2026-10-22）

本节属于用户授权的开发流程，与第8节排查文档会话的只读范围区分。按最新3.2、4.6～4.7、5.3实施，工作区位于 **master / 9a7ce608d**，未提交、推送、部署或重放现场任务。本任务修改已从旧 `feature/ask-user-question-card` 工作区清走；旧工作区保留其他WeCom任务改动，不删除他人工作。主线已具备的continuation、迟到回执、逐份记账及无总deadline行为保留。

### 9.1 本批实现

- F01/F02：只有明确类型的派发准入拒绝且同连接显式rollback成功，才能以原owner CAS保存completed业务失败。rollback或checkpoint保存失败仍阻断；LeaseLost保持原原因，不伪装普通拒绝。Browser owner同源路径本批只核对，未发现应与上述准入类型等同放行的证据，不全局吞异常。
- F03～F06：先在当前CLI调用内按1/2/5/10秒可取消退避续做既有continuation，再按需视觉恢复；不重跑整个operation。Reader保留图片与当前项，临时取卡失败不永久排除。恢复在会话工厂/登录/取消阶段退出也返回真实部分资料及剩余原因。传输不自动重放鼠标输入，外发未知保护保持。
- C1～C7：HTTP/WS有限等待与失败清理；区分连接、命令ACK、target/session和原生输入。日志含请求ID、脱敏session及鼠标event type，不含原图/简历。原持久profile可由真实调试Chrome进程补采；已知profile可重开，确实未知返回具体依赖。旧健康页面无新增字段仍可读。重建保存原ref及新target索引，等待页面就绪，复用原导航/职位/筛选；登录依赖保持restore_pending，登录后在同target继续。头像摘要暂缺不抹掉原有效筛选，历史摘要不升级为会员提示；恢复跳过原条件或替换选项时明确拒绝继续读取，不能静默放宽筛选。
- F07～F10/F18：在原Runner锁、revision、身份、phase、dedupe/business_ref与冻结请求下证明旧无引用Next状态。无绑定收敛completed业务失败，有绑定只接回原ID；结束等待沿原取消收尾，不凭空制造设备hold。公开动作只作展示，原接口仍验证权限；已取消执行不复活。
- 续筛历史：新准备保存私有history v2，仅最终单份失败进入失败排除；无字段旧v1保留旧失败资格和名单，不能因新增readonly unknown资格改变已接受请求。原始失败/费用/招呼事实不改。
- F11～F15：原started瞬态重试；v1原结果复用outbox持久化，旧消费者忽略子目录，新消费者按原endpoint补传；408/429尊重Retry-After。明确404/409/撤权终止后续输入，网络未知保持原回执，不重执行工具。
- F16/F17：区分心跳失权、存储异常、未知异常及明确服务退出，停止原owner并保留事实。F17原事故claim无started根因继续补证，隔离传输测试不能替代原Runtime日志。

### 9.2 有效验证

| 范围 | 命令/执行方式 | 实际结果及边界 |
|---|---|---|
| CLI独立集合及最终增量复验 | `npm run build:main`；`node --test dist/tests/{browser-page-recovery,connection-recovery,resume-batch,cdp-gateway,cdp-socket,local-operation-recovery,chrome-launcher,operations-result,browser-target-binding}.test.js`（实际逐个传入文件） | 初轮167通过；最终筛选增量后build及browser-page-recovery、browser-target-binding、connection-recovery、local-operation-recovery共62通过（含22项页面恢复测试），其余源码未变的结果复用；均无失败/跳过。Chrome传输/页面输入边界用替身，真实factory/Reader/wrapper/磁盘binding |
| CLI最终日志补充 | 最新build后重跑cdp-socket、connection-recovery | 14通过；ACK日志session/event与send/timeout一致 |
| 后端真实事务及相关单测 | `./scripts/dev_test.sh`：integration的test_local_recovery_lockup、test_local_recovery_proof、test_local_phase_storage、test_usage_storage；unit的test_resume_continuation、test_resume_next_owner_proof、test_worker_execution、test_worker_task_isolation | 163通过；新增F16测试后worker单文件17通过，独立worker+owner proof共39通过，无跳过 |
| Runtime Core | `npm run build`、`npm run typecheck`；`node --test tests/transport-recovery.test.mjs`，另独立启动/控制/完成证明相邻回归 | 新增5通过，相邻28通过；API与provider边界替身，真实runInvocation/outbox/锁；未发真实设备调用 |
| Runtime消费 | `clients/agent-tool-runtime` 中 `npm run typecheck` | 通过，共享Core改动可消费；不等于发行包验证 |
| Web | `frontend/npm run build`；useAgentRunner、runner API、MessageItem定向测试 | 构建通过，独立77通过；含新增10项可用动作/旧字段兼容/草稿保留测试；未做真实页面视觉验收 |
| 启动关键import | 同隔离环境导入RunnerWorker、ApplicationToolRecovery及agent_runner_web.router | 通过，无启动调度器、无业务派发 |
| 独立CodeReview | 两轮问题修复后只读复核最终diff | 无开放P0/P1；P2崩溃残留本机binding锁见9.3；`git diff --check`通过 |

Python测试使用现有API容器的 `/opt/venv/bin/python` 和当前挂载源码，经项目测试脚本执行；数据库为本次新建、测试后删除的独立PostgreSQL容器及随机数据库，未使用.env远程库。完整Runner/招聘/费用等schema真实初始化，仅省略无关vector扩展、chunks_vec表及索引；本批不涉及向量行为。隔离脚本位于忽略的.tmp目录，未形成线上修库工具。已有Pydantic弃用及前端chunk大小提示不计为失败。

测试过程发现并修复：实际session协议错误丢分类、显式回滚证明缺失、半批工厂前失败丢资料、新页未就绪/登录后未回入口、新target索引未继承筛选、v1新增unknown历史导致冻结请求误拒、头像null/undefined误删筛选、历史会员摘要误作当前凭据及恢复条件被静默跳过/替换。最后两项经独立CR定向复核，无新增问题。旧错误分类断言按新业务意图更新后复跑。一次主控命令用了不存在的测试路径，pytest未收集；更正路径后取得上表163通过，未将该次计为通过。

### 9.3 交付限制与后续

- A1～A6真实业务尚未执行，尤其原会话14份入库/2次招呼、真实Chrome关闭/冷启动/登录/职位筛选联动与发布切换未验收；不宣称现场已恢复。部署及续做现场任务需用户明确指示，当前未执行。
- 本批确定性续做沿已有continuation登记路径，重点验证batch/next；未为没有continuation的其他命令新增整操作重执行，不声称全部22个命令的故障矩阵通过。
- 初轮非阻断P2：binding更新时进程崩溃可能留下.lock，后续更新会返回stale。此项已按后续授权在第10节用内核句柄锁修复并独立验证关闭；不按固定TTL抢活锁，也未新增通用锁恢复框架。
- 暂不打包、升release或改在途绑定；生产升级/降级组合及旧事故底层无ACK原因仍按5.2、6、7节核对。计划保持部分完成，不把隔离验证代替整体验收。

## 10. 残留锁修复与测试包准备（2026-10-22追加授权）

用户已授权按“先补可脚本验证部分，再准备Runtime/BOSS真实联测包”推进。本节替代9.3中暂不打包的本批限制，不授予部署、现场续做、安装、提交或推送权限。

### 10.1 最小实现与成功标准

`browserTargetBinding`沿Runtime既有内核句柄互斥模式：Windows使用由规范本机目录及小写ref派生的命名管道，Linux开发环境使用Node20.8+的abstract UNIX socket；不把Core依赖引入CLI。成功listen后才进入短更新临界区，争用直接返回BUSY；异常与正常退出关闭句柄，崩溃由操作系统释放。不读取、删除或按TTL抢占旧.lock文件；旧未发布文件成为惰性残留，不再参与新版仲裁。旧调用release固定，Host原桌面互斥保持，不支持对在途旧进程热替换锁实现。不为此新增PID账本、恢复守卫、通用锁服务或业务重试器。

必须以真实子进程证明：活进程即使暂停/持续持锁也不被抢；杀死本测试创建的持锁进程后，同ref可更新；多竞争者不能同时提交；回调异常后可重获；UUID大小写不能分裂锁；旧.lock存在不阻断新版更新。只清测试自己创建的UUID文件，不操作真实浏览器/进程。独立测试、CodeReview后按[构建手册](../ops/runtime-boss-release-guide.md)准备新版本acceptance测试包，保留完整公开根集合与原回执，不覆盖既有交付物，不把测试包称正式签名发行。

### 10.2 验证与交付记录

锁修复已通过Windows真实子进程6项回归，与binding/page-recovery共31项通过，无跳过；覆盖强杀回收、阻塞活owner互斥、24次并发更新不丢写、异常释放、旧.lock、UUID大小写别名与不同ref隔离。独立CR确认原P2关闭，无新增P0/P1/P2。Linux abstract socket分支有显式Node版本守卫，但本机WSL只有Node12，未实测该分支，不计为跨平台验收。

测试包已完成：Runtime UI0.0.24、Host0.2.22、Core0.2.20、BOSS0.3.22，native保持0.1.0；新批次`20261022-180618`，固定Node22.23.3。按现有构建手册生成acceptance签名插件、完整公开信任根快照及安装器；使用已安装依赖，未对共享工作区执行全量npm ci以免干扰其他任务。独立release CR确认旧13个公钥对象原样保留，只追加本批唯一BOSS范围测试根，资源/登记表/快照一致。安装器明确NotSigned，不称正式签名发行；BOSS插件Ed25519验签通过。

| 实际验证 | 结果 |
|---|---|
| Runtime安装器构建及标准验包 | RUNTIME_PACKAGE_PASS、RUNTIME_VERIFY_PASS，固定Node22.23.3/ABI127、3811资源文件 |
| BOSS离线验包与原生依赖加载 | 3736文件、25工具，payload不变，NATIVE_INPUT_LOAD_OK；doctor ready=false，未启动调试Chrome/未登录的环境依赖原样保留 |
| 打包脚本回归 | 插件8项、Runtime20项通过，无失败/跳过 |
| 本批真实Host+签名插件隔离联测 | 新mkdtemp home真实导入、相同请求幂等、dispose/reopen库存保留、卸载通过；不配对、不发浏览器输入、不发云端业务 |
| 签名payload+固定Node崩溃锁复验 | 独立真实child验证活owner阻止竞争者、杀本测试owner后同ref成功更新；3736文件tree SHA前后相同 |
| 最终交付ZIP | 主控及独立测试者只读复核12个唯一根条目、CRC、11项文件SHA、8份JSON；两份联测报告通过，EXE/插件及内层3735个受控文件摘要一致，not_run准确保留 |

交付文件：[Runtime/BOSS验收ZIP](../../clients/release/AID-Work-Runtime-0.0.24-BOSS-0.3.22-20261022-180618.zip)及[校验值](../../clients/release/AID-Work-Runtime-0.0.24-BOSS-0.3.22-20261022-180618.zip.sha256)，大小132651095字节，ZIP SHA-256为`592bf9847b995e2f1116aa7461c078fedb1860a3df2833b9bd1a009360eb515d`。说明、版本及not_run清单位于ZIP内先读我.md/build-info.json；包仅含明确交付文件，无配对凭据/私钥/真实简历/日志。

压缩时复现编辑器watcher短暂占用本批插件/ZIP，现有重试及本批临时文件替换退避后成功；未杀占用进程，失败partial已清理。最终摘要以合入两份实测报告后的ZIP为准，未覆盖任何既有已交付版本。

真实A1～A6、原14份/2次业务事实、实际NSIS安装、旧库存升级、GUI运行及发布切换仍未验收；对应报告保持not_run。本包不部署后端/Web，不变更在途旧调用release；完整原会话联测须配套后台代码并由用户另行指示部署环境。当前master，未提交、推送、安装或续做现场任务。

## 11. 提交前验证（2026-10-22）

用户已明确授权提交并推送本次修改，agent2部署及新版客户端安装由用户手动执行。已fetch确认master上游；共享主线新增私有云及回复分享提交后，按当前基线`dccf978f`核对本任务61个暂存文件，索引只加入本任务一行，保留其他任务未提交改动。安装器/ZIP不进入Git，登记源码、版本、公开信任根及ZIP校验值。

从Git暂存树导出隔离源码，仅复用已安装依赖目录：CLI构建及10文件集合181通过、Web构建及useAgentRunner 62通过、真实独立PostgreSQL与相关单测168通过，均无失败/跳过；RunnerWorker/ApplicationToolRecovery/Web路由入口导入通过，未启动调度。数据库仍只省略无关vector扩展/表/索引，不连接现场数据库。`git diff --cached --check`通过；源码及产物此前独立CR结论保持。上述结果对应待提交代码，不依赖工作区未提交的其他功能代码；真实业务验收范围仍按第6、10节。

## 12. 2026-10-23现场增量：隐藏渲染窗口未自动恢复（开发验证中）

复盘：[agent2页面正常但续筛被隐藏渲染窗口守卫拒绝](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#61-隐藏渲染窗口)。trace `tr_runner_2afd4ac171aa4de4be4bd52945113618` 中两次续筛均为Win32 `render widget hidden`零输入拒绝，网页诊断两次均无阻挡且goal satisfied，但强转window诊断失败后退出；不是CDP读取超时，也不是后台Runner拒绝“继续”。

本批仅补四处现有链路，不新增恢复框架、Device协议或人工门禁：

- 共享`win32-native-input`窗口探测及CLI恢复入口：隐藏/暂不可用widget明确零输入时，重新激活原target、等待并重新枚举；fresh页面定位后重试，不移除真实落点与外发保护。
- `localOperationRecovery.ts`：纳入本机瞬态零输入拒绝，不限于CDP异常；readonly/input_status=not_started且网页无阻挡时不强制窗口视觉分支，直接本机重定位/续读。
- `ResumeBatchReader.ts`：恢复前保留当前批次资料；尚未读取的失败卡片不标完成或永久排除。必要时对已支持的打开卡片动作复用CDP clickBrowse并做领域后验；不改打招呼/消息通道。
- 诊断日志：保留窗口观察的原始脱敏错误和必要窗口元数据，区分可见性探测失败与CDP传输错误；模型回复不把STALE泛称为截图超龄或让用户手动F5兜底。

验收归入原A2，不增加场景总数。可恢复机制已按源码确认，具体选中隐藏widget的窗口现场及window诊断INTERNAL_ERROR底层原因仍需客户端日志；本次未重试现场操作，不宣称底层现场原因已确定。

### 12.1 最小实现与边界（2026-10-23）

- 绑定Chrome激活后，仅hidden/unavailable widget做6次200ms等待及重新枚举（最多1.2秒），不保留隐藏HWND。custom resolveTarget及非绑定路径保持原语义；隐藏旧HWND的诊断矩形读取失败不阻断后续可见实例。前台、归属、落点、光标及输入后unknown守卫保持。已有OpContext取消贯穿WinMouseClicker到native调用上下文，新等待每轮检查取消。
- 认证的`WinClickError exitCode=2`加精确hidden/unavailable/foreground/target activation原因生成内部`native_input_failure`；任意STALE、光标变化、遮挡及输入后unknown均不生成该证明。证明不进入视觉上下文，不清除同卡此前已投递输入事实。共享恢复复用原领域continuation、新session及取消/退避；原生连续无新资料最多2次重连，新增资料后重新计数，原CDP可取消退避语义保持。
- Reader保留原ref、当前阶段、图片对象及次数，open/read遇认证拒绝不把未读人加入processed_names，内层读取catch不吞掉该异常。开卡两次原生拒绝后，仅原ref仍可fresh解析且根HTML真实几何有效时使用已有clickBrowse；device bounds按根bounds/clientRects比例换CSS坐标，继续使用详情操作列/经历概览及画布后验。没有几何或身份依据不猜坐标。此兜底不扩到真实滚轮、筛选控件或招呼/消息/接受等写动作。
- readonly及保留半批成果的分支，page诊断absent+satisfied，且累计not_started或本次有认证零输入证明时，直接回到领域续做，不再由失败window诊断否决。其他未知输入仍保留原判据。窗口观察新增脱敏activate/capture/identity/read失败阶段、原业务code和白名单系统码，不复制任意异常正文。
- 不改Device/API/wire、配对、20倍计费、版本或发布配置；用户旧安装包暂不含本补丁。只使用合成Win32世界/DOM和隔离临时目录，没有点击客户Chrome、外发、付费模型请求或远程数据库操作。

### 12.2 验证记录

开发红复现：两项native及两项CLI回归均在旧源码失败，分别覆盖隐藏新widget未重枚举、持续隐藏未等待、正常page仍被window分支否决、零输入未读卡进入processed_names。首轮实现build通过；native40项、CLI原受影响集合79项通过，无失败/跳过。补充生产operation→wrapper→新session→Reader→同ref fresh开卡的125%/150%/200%缩放合成链、半批page判据及诊断脱敏后，5文件66项通过。

独立首轮native46项通过，CLI新增用例最初7/8通过：读中滚轮零输入中断后，Reader关闭残留详情仍用关闭前快照判断pending卡片，错误清除原ref并重复计attempt。已修为关闭后立即fresh列表再校验原pending/ref；不通过清ref或接受同名替代绕过。开发重跑build与Reader/新开发集/独立集，58/58通过，无失败/跳过，涵盖原图片保留、未完卡不排除、同名替代拒绝及persistent真实滚轮不改CDP。

独立最终验证覆盖212个唯一相关用例（native46、CLI166），无剩余产品失败或跳过。CLI最终9文件组合首跑165/166通过，唯一失败为独立新增job-scope合成fixture缺实体筛选节点，真实JobSwitcher返回null，产品仍安全拒绝了输入但错误码与测试预期不符；仅补合成DOM后编译通过，独立文件11/11复跑通过，其他155项在相同产品源码上的有效结果复用，不把首跑写成exit0。独立CR已复核最终Reader修正并通过，无待修阻断。

最终独立命令与退出状态（均为离线合成测试）：

- `clients/shared/win32-native-input`：`node --test tests/sequence.test.mjs tests/widget-hidden-independent.test.mjs`，exit0，46/46，无跳过。
- `clients/boss-resume-assistant`：`npm run build:main`，exit0；`node --test dist/tests/widget-hidden-recovery-independent.test.js dist/tests/widget-hidden-recovery.test.js dist/tests/resume-batch.test.js dist/tests/local-operation-recovery.test.js dist/tests/local-operation-recovery-independent.test.js dist/tests/win-mouse-clicker.test.js dist/tests/browser-page-recovery.test.js dist/tests/visual-recovery.test.js dist/tests/visual-v2-client.test.js`，exit1，165/166，失败仅上述fixture。
- 同目录补合成fixture后：`node node_modules/typescript/bin/tsc -p tsconfig.main.json`，exit0；`node --test dist/tests/widget-hidden-recovery-independent.test.js`，exit0，11/11，无跳过；其余155项有效结果复用。
- 同目录`node dist/src/cli/index.js --help`，exit0，仅显示帮助，未启动或操作Chrome。

该Phase已完成开发，原A2真实Chrome/客户会话尚未验收；上述开发验证结束时未提交、推送、升级版本、打包或部署，也未再次取线上记录。后续授权交付见12.3。实际隐藏HWND/window捕获底层原因仍待客户机日志，不宣称现场问题已根治。本补丁不要求更新后台API。

### 12.3 授权提交与一键打包交付（2026-10-23）

用户授权提交推送并使用已有一键脚本出包，不部署服务器。fetch后快进远程3个无关提交，保留其他任务全部工作区改动；只暂存本修复及关联文档，修复提交`54d5befc`已推送当前master的两个配置远程。主控对最终修复源码重新构建通过。

原样执行`clients/agent-desktop/scripts/release-batch.ps1`九阶段，exit0。该现有入口生成Runtime+BOSS组合包，每批新插件使用内存临时签名私钥；新公钥需随配套Runtime交付，不能宣称该新插件可单独导入不含此公钥的旧Runtime。Runtime UI升至0.0.25，BOSS升至0.3.23，Host0.2.22/Core0.2.20及私有native0.1.0保持脚本原有版本；native从当前构建重新pack，未复用旧tgz。登记表14个历史根原样保留，仅追加本批BOSS范围公钥；完整发布快照为`clients/runtime-plugin-packaging/trust/history/runtime-0.0.25.roots.json`。

- 最终组合ZIP：`clients/release/AID-Work-Runtime-0.0.25-BOSS-0.3.23-20261023-153417.zip`，132651379字节，SHA256 `91d8b284c720f19c4a56d5eb8ab83fa8def5cc86fcc8188413997d8ae8930e3b`。
- 同名交付目录中的插件：`boss-0.3.23-dev.aidplugin.zip`，5376431字节，SHA256 `2311aa5a73ef5c0ea1e7698aeeba7eea4250e68bc1d54abbedb08880443191f0`。插件Ed25519签名，Runtime Windows安装器仍为NotSigned验收版。
- 构建、`RUNTIME_PACKAGE_PASS`、`RUNTIME_VERIFY_PASS`、插件签名/3736文件清单/搬迁后version及只读payload不变、实际`NATIVE_INPUT_LOAD_OK`均通过；打包定向测试8+20项通过、无跳过。doctor如实为exit1/ready=false，不能当作业务验收通过。
- ZIP首次压缩遇编辑器短暂文件监视占用，原脚本诊断后自动退避重试成功，最终逐项文件摘要复读通过；未重启Windows、结束编辑器或修改打包脚本。
- 独立发行审查通过：ZIP10项清单、CRC及逐文件摘要、EXE与manifest关联、9个关键修复dist与当前构建一致；实际构建资源中的信任根与交付/历史快照一致。未另解NSIS或运行安装器，不将静态验包写成安装通过。

交付保留原配对及用户数据：先安装同包Runtime，再从Runtime导入本批BOSS插件；不解压插件本身。安装、覆盖升级、Runtime UI及A2客户业务恢复仍待用户更新后验证，未部署agent2或其他服务器。ZIP/EXE不进入Git，版本、公开根/完整快照及ZIP校验值随本次授权提交。

2026-10-23客户Runtime原始日志补证（只读，未新增代码）：见[统一交接§6.8](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#68-客户runtime原始日志核验2026-10-23)。84次调用的3270次已记录CDP请求均有ACK；15次隐藏widget前DOM/激活成功，原生discover仅0～2ms，无新重探测执行痕迹，不能当成客户已运行Phase 8而修复无效。窗口诊断底层错误现确认23次为`Browser window extends outside visible screen; capture refused`，须与初始隐藏widget分开处理；具体超屏矩形/隐藏HWND日志仍缺。Phase 8既有开发、打包及待现场验收状态不变，不据补证重复建设恢复链。

## 13. 2026-10-23现场增量：推荐页定向打招呼与页面前提

复盘：[筛选后定向打招呼失败并反复切到沟通页](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#62-定向招呼与沟通绕行)。trace `tr_runner_9984c8fd53054e4f9fd3c865ac383622`、`tr_runner_386f2626342d4ed69754f974e2fc0364`：三次boss_greet因未识别推荐分区结构失败，普通Error丢失业务阶段后误包装为INTERNAL_ERROR/unknown；模型随后显式切chat核查，搜索又失败。

2026-10-23用户纠正后的实施口径：**候选卡片优先，允许列表混入未知特殊项目；不再要求先完整识别推荐分区。**普通卡片在同一卡片/行内配对姓名及“打招呼/继续沟通”，广告、提示、无有效配对项直接跳过；陌生项目不代表离开推荐页、列表结束，也不能剔除其后整段普通卡片。

开发只补现有链路：

- `GreetExecutor`复用已加载目标索引及双向滚动，自动找回已批准的两人；必要时回列表顶部继续查找，保留原职位/筛选。不让用户手动滚动，不无条件重设筛选，不以转沟通页代替推荐页定位。
- `cardName.ts`、`LoadedCandidateNavigation.ts`会间接经过`excludedRecommendationNode`；共同解除未知插入项的全局抛错/全局排除，不能只绕过最外层边界检查。复用已有姓名与按钮配对，不为各类广告建立新规则库。
- 每人独立处理：“打招呼”执行后核验，“继续沟通”记已联系不重发，未找到准确记录并继续其他批准对象。同名沿原规则消歧，不跨卡拼接、不扩大批准名单。
- 保留真实not_started/confirmed/unknown及逐人回执，禁止把所有unknown一律置none。实际空列表/查找结束按列表后态判断，不以未知特殊项代替；搜索后验及筛选clip问题分别按已记录建议处理，不把其修复当成卡片定位的前置工程。

用户明确的页面约定：筛选、筛选读取简历和名单打招呼在recommend；后续聊天、收取/下载附件、面试和不合适在chat。复用已有URL检查与导航，不新增页面授权协议或通用工作流引擎；沿A4验证，场景总数仍为6。本次代码实现及独立验证见第16节；没有现场操作。

### 13.1 同源读取链补证（16:01 trace）

`tr_runner_50c38a7ab03c4b54a971c45e617a2b54`确认：先入库3份，再以page_operation_failed保留2份且允许续筛，第三次因同一未识别推荐分区报INTERNAL_ERROR。工具没有给出recommendations_boundary/无匹配终态，助手却解释为到达补充区。证据见[复盘§5](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#63-中间插入项导致续筛停止)。

本阶段覆盖`ResumeBatchReader`及共用卡片提取，不能只修打招呼；移除把中间插入项及其后全部普通卡片当终局/排除区的逻辑。原生普通卡片照常读取，插入推广项跳过自身；部分成果保存后继续补足原目标，报告真实失败，不能虚报候选耗尽。沿原A1/A4验证，不增加新框架、协议或验收项。

### 13.2 续筛误点顶部“推荐”标签（15:xx trace）

`tr_runner_dbdfad2bdfa04818b0f829d2de8bb776`的第二批回执把“推荐”记作候选name和target_label，input_status=attempted；前两批入库3+2份，没有每5份导航或boss_goto。完整时序、原生输入拒绝及证据边界见[误配复盘](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#64-顶部推荐标签误配)。

同批修`cardName.ts`、`ResumeBatchReader.ts`及既有候选索引：姓名和动作从同一卡片子树取得，不再从全iframe同行猜姓名；共用已有iframe裁剪/固定头部区域计算，遮挡卡片滚入后fresh定位。首次点击也必须来自合法卡片，不能reference=null就回退点任意文本；复用已有结构提取，不新增全局严格分区、词黑名单、公开协议或人工门禁。继续跳过特殊项自身，保留未完成项、已读图片/入库结果和剩余目标。

后续隐藏widget沿Phase 8检查实际运行版本和恢复链；光标位置拒绝与误配原因分开，明确零输入的只读动作复用既有fresh重试，不取消窗口/鼠标守卫，不把未知写动作放宽。模型不通过新boss_resume_batch丢弃原续筛上下文。沿原A1/A2/A4补必要自动回归，业务验收仍为6项；本节代码实现及独立验证见第16节；未操作现场。

## 14. 2026-10-23现场增量：筛选面板空clip与编辑阶段恢复

trace `tr_runner_059ae40d76284c0ba0e0c48629345d2e`已收到“性别不限、再读6份”，三次boss_filter均因空clip失败；网页诊断确认面板选项可见，年龄16、经验/学历不限，未完成请求。页面心跳正常，非Runner超时。证据、调用ID及推断边界见[筛选面板复盘](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#65-筛选面板空clip)。

最小实施：`FilterSetter`区分真正scrollport与目标/内部随动分组的裁剪，目标离屏先按真实可用视口滚入再fresh点击，不因内部空交集判整面板不可用。复用iframe/祖先坐标口径、现有取消和普通重试；必要时重开原面板恢复，保留放宽spec和剩余目标，不放开真实落点或遮挡保护。

`bossFilter`保留未开始/已编辑草稿/已提交的真实阶段，避免effect=none或旧徽章被解释成从未改动；恢复按原spec重设和核验，不新建事务协议。恢复观察不能把正常编辑面板当障碍关闭；网页正常而几何失败先回setter定位，不强制窗口视觉分支。局部不可读沿原部分筛选规则处理，不能重加全部行识别门禁；成功提交后接续原6份及已入库去重。

先修clip及针对性合成回归，再验证编辑阶段恢复和原会话继续；补失败节点几何/阶段的脱敏日志，以定位现场具体祖先。沿原A2/A4，验收仍6项；旧会员/部分探查修复的完成记录不改成本次已验收。仅诊断及文档，未改业务代码或现场。

## 15. 原始日志核验后的最小修复安排（2026-10-23）

设计唯一细化入口：[原设计W4](../design/recruiting/boss-overlay-heal-design.md#w4-原始日志核验后的最小修复方案2026-10-23)。事实依据：[客户原始日志核验](../incidents/boss-agent2-issues-and-minimal-repair-handoff-20261023.md#68-客户runtime原始日志核验2026-10-23)。用户已确认本节最小范围并授权按开发流程实施；真实超屏允许原窗口自动归位一次。独立测试与CR通过后同步文档，不提交、打包、部署，不运行客户任务或安装新包。

### 15.1 复用、新增与暂不纳入

| 处理包 | 沿用阶段/完成状态 | 本次最小工作 | 主要文件范围 |
|---|---|---|---|
| 滚动候选识别 | Phase 9已开发验证 | 同卡姓名/动作来源；固定头部和iframe可点击性；滚动/恢复后fresh定位；首次点击合法来源；共享分区局部容错和逐人事实 | `cardName.ts`、`RecommendationResultBoundary.ts`、`LoadedCandidateNavigation.ts`、Reader/Greet及必要原错误封装 |
| 隐藏widget | Phase 8已完成开发并出包，现场未验 | 核对实际release/Provider启动链及新日志；直接复用1.2秒重枚举、有界零输入续做、checkpoint及只读开卡兜底，新链仍失败才补明确缺口 | 先核对既有产物/日志，不预设重新修改`sequence.ts`或恢复wrapper |
| 截图超屏 | Phase 11已开发验证，本次新增 | 原窗口/屏幕同口径几何；确实超屏时原调用内至多归位一次并重采整窗；旧观察失效；固定原因与脱敏数字回传 | `window-experiment.ps1`、`BossWindowExperiment.cs`、`windowExperiment.ts`、`visualRecovery.ts`及必要本机恢复消费 |
| 筛选编辑接续 | Phase 10已开发验证 | 真实scrollport、草稿/提交阶段和原spec恢复；与窗口归位后fresh定位组合验证 | 原第14节范围，不能以其他包完成代替 |

正常跨调用引用传递及所有工具统一激活/定位协议暂不纳入上述最小包；它们是源码缺口，本批尚无换标签或激活引发布局变化的确证，不作其他包的开工前置。已完成快捷选项退役、CDP重连、原profile重开、计费及入库去重沿用，不重复实现。搜索后验按原Phase 9必要消费处理，不扩为新沟通工作流。

截图默认方案为原窗口局部归位后重新采集，保留完整窗口图及现有坐标契约。先确认物理DPI/屏幕口径和原窗口身份，平移优先，确实过大才还原/缩小一次；仍超屏或口径不一致就报告具体原因，不盲移、不裁图配旧坐标。窗口几何改变后，所有后续领域输入重新定位。归位本身不重发任何外部动作。

### 15.2 顺序、唯一写入范围与兼容

本次按Phase 9、10、11最小修改包实施；Phase 8只核验/复用。实现及独立验证见第16节；不新增独立进度文档。

三个包由本任务BOSS CLI开发角色分别维护，主控整合；独立测试与只读CodeReview分别验证，不审查自己的实现。除非实施前核对发现确实需要，否则不改Runtime core、公共Electron入口、服务端普通派发或Device API。现有`OperationResult`、`operation_recovery`和`visual_recovery`版本及`window_image_px`含义保持；只在既有诊断字典增加固定失败原因和脱敏数字，不通过字段扩展改变执行权限。

归位仅发生在本次受权调用内，消费既有取消/恢复预算，原调用内至多一次；旧观察引用/图片不能用于新几何。取消/失权后无新窗口动作；外发unknown继续核对原人、不重放。旧客户端无新增几何诊断时沿原结果消费，不能把缺字段改成拒绝普通读取的门禁。

### 15.3 与改动相称的验证及交付边界

沿原A1/A2/A4/A6补相关自动回归，不新增业务矩阵：

- 候选链：四次现场共同的1674×884五卡/半屏滚动模式，3+2后不会选“推荐”；头部遮挡可滚回；推广项后普通候选继续；已完成项不重读；批准名单不跨人、不重复招呼。
- 隐藏widget：复用Phase 8已有hidden→visible、持续hidden、半批恢复、只读开卡及unknown保护回归；新执行链必须可从实际枚举/续做日志识别，旧客户端日志不当成新版验收。
- 截图包：完全在屏零归位；真实超屏一次归位后新截图；多屏合法负坐标；窗口过大；归位后仍失败/身份变化/取消。核对新图尺寸及坐标一致，旧观察拒绝输入；没有额外付费模型调用或重复外发。
- 组合：窗口归位和列表滚动后使用新定位；正常筛选编辑面板不被误关；沿原会话补足剩余量，已有识别/入库/发送事实不丢。

运行与实际改动相关的构建、独立测试和审查；已验证且未变的源码结果复用，修复后仅重跑受影响检查。后续提交、版本打包、安装、客户浏览器业务操作及各环境部署仍分别按用户明确指令执行，本次开发授权不包含这些操作。

## 16. 本次最小修复实施与独立验证（2026-10-23）

授权边界：用户明确允许真实超屏时原窗口自动归位一次，随后重新截图/定位；按高风险开发、独立TEST与独立CR流程实施。**不提交、打包、部署，不操作客户浏览器。** 当前分支master；保留工作区其他任务脏改。

### 16.1 实际修改

1. Phase 9：同卡片结构姓名与BUTTON动作代替全iframe几何猜名，未知分区局部容错，真实空结果继续阻断fallback；离屏卡片最多三次fresh定位，名称不唯一/来源变化不点击、不永久processed。定向已联系/暂不可操作分别回执，不能扩大批准名单；generic INTERNAL保留逐人确认事实及正确effect。
2. Phase 10：真实scrollport与目标当前clip分开，保留内部/iframe裁剪；草稿/提交阶段保留原spec continuation；提交unknown只核验不重提，网页或窗口正常筛选面板均回原setter。
3. Phase 11：真实显示器并集判断整窗可见；原进程/HWND至多一次平移优先归位，确实放不下才缩小，重采完整窗口图；预算贯穿continuation、失败不返还，取消及原身份保护、旧观察epoch失效；固定原因及脱敏数字几何回传。
4. 复用Phase 8：未重写native widget等待枚举或另建重连路径。共同契约不变：无Runtime core/Device API/wire新版本，无新权限、模型直连或计费分支。

### 16.2 验证登记

独立TEST通过：**31个唯一相关测试文件，467项通过、0失败、0跳过**；重复复验不叠加用例数。独立CR通过，无未解决P0/P1/P2。命令、各轮失败/修复记录、去重计数及本批源码SHA256见[验证清单](../incidents/evidence/boss-cross-call-20261023/minimal-repair-validation-20261023.json)。

| 验证 | 命令/范围 | 最终结果 |
|---|---|---|
| 独立构建 | `npm run build:main`（CLI及共享native TS） | exit 0；未打包 |
| 独立回归 | `AID_BOSS_AUTO_CHROME=0; node --test dist/tests/<相关文件>.test.js`；31文件完整清单见验证JSON | 去重467/467，0失败/跳过 |
| P1修复后独立复验 | candidate-scroll-recovery、resume-batch、widget-hidden-recovery/resume、resume-obstruction-independent、local-operation-recovery | 六文件121/121，0失败/跳过 |
| 独立CodeReview | 本批diff及新文件；P1原复现修后零点击、零滚动且保留原引用 | 三项发现均已修复复核，无遗留阻断 |
| 主控最终检查 | `npm run typecheck`；本任务`git diff --check` | 均exit 0 |

CR关闭项：P1恢复时离屏原卡被误判消失并丢绑定，现全量源核验、原ref/sourceIdentity不清空；P2窗口诊断分支误关普通筛选面板，现最终诊断同样回setter；P2暂不可操作名单未纳入部分完成投影，现纳入。未将自测、合成几何或编译称为真机业务验收。

- 开发先复现：同行“推荐”误配与未知插入全局抛错的两个回归在修复前均失败，修复后通过。
- 测试夹具同步真实card-list/card-item/name-wrap/BUTTON结构，保留原坐标、匿名卡零点击、unknown不重发、取消及逐人进度意图；修正原Phase8旧断言为原checkpoint续未完成目标、已读图像保留。
- 已发现并修复：无目标窗口capture catch访问空window；正常草稿保护遗漏window fallback分支；由独立TEST/CR复验，不保留未解决的旧失败为“全绿”。

### 16.3 未验证与交付限制

没有操作真实客户窗口、移动真实Chrome、发起真实招呼/聊天/筛选，未安装或更新客户端。多显示器DPI切换/真实超屏归位、客户实际Provider/release执行链以及A1～A6现场业务验收仍待后续明确授权。原始日志缺失的HWND可见性/具体矩形不因合成回归而变成已证实的现场根因。正常跨调用页面ref传递与搜索工作流不在本次代码范围。

## 17. 本次提交与 Runtime/BOSS 组合包（2026-10-23）

用户在完成独立TEST/CR后明确授权本次提交推送，并运行现有`clients/agent-desktop/scripts/release-batch.ps1`打包Runtime和BOSS；**不部署**。此前第16节的未提交/打包限制已由本次授权更新；安装或客户业务验收仍未执行。

提交前已fetch两个配置远程，master快进4个上游提交至`b213bd76`；仅`docs/ideas.md`与本地索引改动重叠，经保留原内容、三方合并后恢复，其他任务工作区改动保留。本次仅提交BOSS最小修复、关联设计/计划和脱敏证据；无关Web搜索、B2B、企微改动不纳入。BOSS源码SHA256与独立验证清单一致，原467项与CR结论有效复用；提交前主控再做构建检查。

一键脚本会独立提升Runtime UI/BOSS patch版本、使用临时内存签名生成BOSS插件、追加公开信任根和完整版本快照、生成Host及Runtime安装器、验包并输出组合ZIP。实际版本、源码提交、产物摘要及验证结果待脚本完成后登记；不覆盖上一批交付。版本/公开根及最终发行记录作为同一授权范围的后续提交同步，ZIP/EXE不进入Git。
