# C5 端侧会话任务综合验收

日期：2026-09-15；起始代码 `8979eca4`。范围：隔离 fake 综合验证与发布准备，**C5 部分完成，不允许灰度**。关联 [计划](../../plans/desktop-automation/plan-edge-session-task.md)、[回滚手册](../../ops/edge-session-rollout.md)、[C4记录](edge-session-c4-validation.md)。

## 本轮交付与环境

- 新增 `scripts/edge_session_metrics.py`：离线统计阶段 p50/p95/max、失败/unknown/gap、调用数和客户等待；同条件与至少30成功样本检查。此工具不采集真实消息、不连接服务、不签发许可。
- 新增指标边界测试、文件热读回滚测试；空库安装测试新增会话任务15张表族检查。
- 新增便携Provider归档脚本与包根OCR优先定位，拒绝普通venv/链接依赖/外部搜索路径；路径及拒绝测试通过。缺embedded源，尚未实际产包，干净用户验证仍BLOCKED。
- 本轮不改运行时能力或生产开关；不提交代码。保留既有 BOSS 等未提交文件，未跟踪的营销3个测试文件不计入本轮。
- Windows Python3.12，Node 使用本机现有安装；Python 经 Git Bash `./scripts/dev_test.sh`，随机隔离租户/临时库与 fake 模型/Provider，不运行真实会话。

## A1–A11 门禁

PASS 仅指注明的测试范围，不能外推真机。包含待验证要求的整项保守 BLOCKED。

| 编号 | 状态 | 证据与缺口 |
|---|---|---|
| A1 独立任务 | PASS（fake） | Runtime engine 与真实进程联测，真实 API/DB、fake 模型/Provider，主 Agent 不参与循环；用户 SSE 不在执行依赖链 |
| A2 等待切换 | PASS（fake） | session-engine 就绪/等待/新消息切换，等待为网络查询；锁内重验由 session-execution/recheck 覆盖 |
| A3 持久恢复 | BLOCKED | session-store/engine/execution 覆盖日志、ACK、控制乱序、元数据损坏、四个执行恢复点；真实 Runtime kill恢复通过。尚非每个物理落盘/进程边界的完整故障注入证明 |
| A4 消息正确 | BLOCKED | Observer契约/重放与aligner synthetic测试；真机 sender、视口外/当前会话覆盖和OCR气泡证据缺失 |
| A5 权限并发 | PASS（fake） | session协议/门禁/竞争用例与底座permits/claim回归；不声称证明所有调度下无死锁 |
| A6 目标预算 | PASS（fake） | 三种完成规则、冻结引用、到期、上限、人工接管/unknown定向用例 |
| A7 费用去重 | PASS（fake） | 费用恢复、幂等账本、模型槽位/预留及结果重放测试；无真实供应商费用实测 |
| A8 兼容 | FAIL | Runtime standard与底座已通过；微信CLI4失败；固定营销193通过/28失败；BOSS当前有其他任务修改，不认定其完整回归通过 |
| A9 性能 | BLOCKED | 本轮合成图OCR n=30，但缺C0端到端各阶段基线，不能证明编排减少30%；客户/模型/真实写入均未测 |
| A10 真机 | BLOCKED | 尚无本场景授权范围；当前执行上下文doctor报告非交互桌面，真实截图未接线 |
| A11 安装回滚 | BLOCKED | 热读关停与空库15张表检查通过；便携定位/组装代码与路径测试已补，缺embedded源及干净用户实包验收，双进程启动未验收 |

## 可复核命令与结果

下列为本轮执行记录，独立收尾结果见文末。

| 命令（仓库根，Python经dev_test.sh） | 结果 |
|---|---|
| `npm --prefix clients/agent-tool-runtime test` | 165 passed，0失败/跳过；build通过，92.62s测试 |
| `npm --prefix clients/weixin-cli test` | build通过；121 passed，4 failed，0跳过；24.38s |
| `tests/unit/test_edge_session_metrics.py` | 首次10 passed；超大整数修复后12 passed |
| `tests/unit/test_edge_session_rollback.py` | 2 passed |
| `tests/integration/test_session_live_runtime.py` | 独立执行1 passed，真实Runtime进程+fake Provider；含kill恢复 |
| 底座/本地工具/API定向回归 | 独立执行272 passed，0失败/跳过，187.01s |
| `tests/unit/session_tasks tests/unit/tools/test_session_task_tools.py tests/integration/test_session_workbench.py` | 独立225 passed，0失败/跳过，487.46s |
| `tests/integration/test_empty_db_bootstrap.py` | 2 passed，临时空库/幂等重放，7.16s |
| `node --test` 的ocr-portable、session-observer-contract、session-observer-replay、aligner四文件 | 最终build通过，31 passed；新路径测试首次2通过/1路径拼错，修正后3通过 |

固定营销命令：`./scripts/dev_test.sh tests/unit/weixin_marketing --ignore=tests/unit/weixin_marketing/test_manual_sql_safety.py --ignore=tests/unit/weixin_marketing/test_p5_closeout.py --ignore=tests/unit/weixin_marketing/test_trusted_manifest_consistency.py tests/unit/test_edge_session_metrics.py -p no:cacheprovider -q --tb=short`。合计203通过/28失败（其中当时metrics10通过），346.40s。失败分布dispatch14、event_match_concurrency2、event_match_worker11、lifecycle1。扫描accepted=44而期望1表明共享库历史数据影响；另有明确 `events.py` eligible中UUID无法JSON序列化、生命周期UUID与字符串比较问题，不能全称环境故障。本轮未修改这些源码，也未绕过失败门禁；需单独隔离复现并闭环后才能将A8转PASS。

微信CLI四项失败均位于 `cli-commands.test.ts`：doctor JSON、人类可读doctor、probe假定Windows必为交互桌面；当前doctor实际返回非交互会话。缺参send测试预期INVALID_ARGUMENT退出2，但环境门禁优先返回退出1。未修改产品门禁来让测试通过，未进行真实发送。

另执行的 `test_session_endpoints.py` 属于旧聊天SessionCRUD，并非端侧任务协议：13 passed/3 failed，分别为旧 `limit` 参数、对dict调用json.loads、已不存在的MessageDB.add。该文件本轮未修改，失败不归入端侧功能成功数，也不称全仓全绿。

## OCR 同脚本复测

命令：`venv/Scripts/python.exe clients/weixin-cli/scripts/ocr_engine_bench.py --cold 3 --hot 30`。沿C0固定2100×1400/18条合成图，无预热；无真实桌面截图。同期其他验证负载存在，跨日期数字不能单独归因代码优化。

冷样本（import/init/first_ocr/total，ms）：

| 样本 | import | init | first OCR | total |
|---|---:|---:|---:|---:|
| 1 | 730.8 | 370.6 | 2114.7 | 3216.2 |
| 2 | 713.6 | 411.1 | 2238.2 | 3362.9 |
| 3 | 798.2 | 418.8 | 2468.2 | 3685.1 |

热样本ms：2290.0, 844.3, 821.0, 884.2, 885.0, 1122.9, 929.7, 909.0, 829.0, 859.3, 819.3, 873.9, 836.9, 851.4, 851.3, 828.7, 793.3, 856.0, 950.3, 988.3, 823.4, 881.6, 819.8, 784.5, 838.2, 841.0, 897.6, 808.3, 829.1, 835.0。

p50=847.8ms、p95=1122.9ms、max=2290.0ms，n=30；p50取median、p95取nearest-rank。C0热p50=1017.6ms，仅观察到16.7%差异，不满足30%，更不是端到端编排对照。冷样本仍仅3次，数量不足明确保留。

## 阶段统计工具使用

`python scripts/edge_session_metrics.py baseline.json candidate.json`。输入顶层严格包含mode（fake/real）、非空conditions对象、samples列表；conditions由采集者声明硬件、模型、Provider、脚本/对端行为、调度参数。两次必须相同，不应将待比较代码版本塞入conditions；版本另记报告。

每条sample严格包含outcome（verified/failed/unknown/coverage_gap）、timestamps、peer_wait_ms、model_calls、ocr_calls、ocr_cold_starts。timestamps使用**同一单调时钟**的毫秒数，按 `peer_message_visible→detected→batch_ready→decision_ready→lock_acquired→authorized→submitted→verified` 填连续前缀，失败也保留。跨机器时钟不能直接相减，采集者先建立共同测量时基。客户等待发生在visible之前，单列且不再次从响应耗时扣除。

命令退出0只表示p50响应比较达标，退出1表示比较门槛不足，退出2表示输入无效；`a9_gate` 始终BLOCKED，需独立证据核对正确性、LLM轮询、锁等待和OCR复用后人工验收。该工具不推测缺失样本、不把synthetic记录称真机、不输出输入正文。

## 下一步停止点

2026-09-15用户调整优先级：保留固定内容营销功能，专项优化后置，优先客户端监控聊天与自动回复。原A8固定业务失败记录不再作为自动聊天开发阻塞；共用底座验证继续必需。真实截图、受信绑定与真实send_v2三个缺口优先闭环，见计划§8当前实施顺序。

优先级调整前已完成的局部排查（未提交、尚未独立验收）：事件eligible快照的UUID主键转字符串、生命周期测试规范化UUID比较、新增 `dev_test.sh --isolated-db` 临时空库入口。定向复现 `test_dispatch.py test_event_match_worker.py test_event_match_concurrency.py test_lifecycle.py` 与新JSON边界测试共54 passed（123.20s）；没有据此宣称全量营销兼容通过，也不继续扩展营销修复范围。原共享库扫描污染与UUID序列化错误分别处理，未删除共享库存量数据。

继续补全真实截图/受信身份接线、便携OCR安装闭环和全边界故障注入；固定营销优化后置。其后按授权清单执行A9–A11。2026-09-15 已获本机个人联系人单次测试授权，范围见下节；不等于生产灰度放行。C5未全过，第78项保留进行中，不移完成索引。

## 本机自动聊天测试准备（2026-09-15）

用户明确授权本机已打开的微信，与个人联系人“覃姗”聊天10轮，开场说明产品正在测试、后续需要对方参与运营支持。要求编译客户端后由产品 Agent 下发任务并自主观察/回复，开发助手只观察，必要时截图。该授权不扩展到同名企业联系人或其他联系人。

本轮已完成：

- 通过现有编译后驱动搜索并打开精确个人联系人，未发送消息。原始截图仅留本机临时目录，未纳入仓库。
- 修正 SESSIONNAME 缺失时的会话误判：只查询当前进程所属会话，仍拒绝非交互会话。
- PowerShell 驱动 JSON 使用 ASCII 转义传输，解决中文联系人名称在控制台代码页变化后损坏的问题。
- 观察器校验采集实际身份和证据引用；修复滑窗及空基线连续观察的消息 ID 稳定性，身份版本隔离，最多64项LRU缓存；未知旧水位返回 gap。
- 桌面客户端 `npm --prefix clients/agent-desktop run build` 通过，含 renderer、TypeScript 及桌面产物检查。尚未生成安装包或连接服务端。
- 独立微信CLI全量测试138通过；后续三项审查修复后的受影响四文件38通过，独立边界探针通过，CodeReview原问题关闭。上述均不是真机发送验收。

后续本机连接已完成：venv API 监听 `127.0.0.1:8000`，网页监听 `127.0.0.1:5173`；用户登录并授权新增当前电脑，Runtime 0.2.13 配对为“本机微信联测”，网页确认在线且使用中。Runtime 指向本地 API，并配置编译后的真实微信 Provider；未启用未实现的 v2 能力。

真实只读队列验证：经服务端 repository 正式队列建立一次 `weixin_probe` invocation，由本机 Runtime 自动领取、调用真实 Provider 并回传。2026-09-15 数据库执行时间12:30:40至12:30:42，结果 `succeeded / OK / effect=none`，invocation `e24f3b0a-6cc7-46ed-9554-f40dd29c9c65`。这是服务端队列→Runtime→Provider联通证据，不是由产品 Agent 生成的聊天任务，也不是自动回复验收。

新增 `bubbleSegmentation.ts` 像素候选分割：长绿色气泡依据外缘推断self，OCR按候选边界聚合并保留换行；跨界、缺OCR、重复交叠、非法几何统一gap，不返回部分正文。校验RGB/布局及候选数、OCR框数、正文长度上限。既有授权截图的实验布局产出4个候选（peer3/self1），普通历史图片及卡片未纳入；此布局未经可信核验，定制图片仍可仿制颜色和停靠边，故结果明确为candidates，不是完整消息或身份的证明。新增模块未接到真实observer。独立build及7项测试通过，独立失败探针确认漏字/多行问题修复；CodeReview原P1/P2关闭，能力边界保留。

上述为先前准备阶段记录，后续用户明确取消账号标识采集要求，采用当前登录微信的名称定位策略。真实10轮仍未完成；不得用旧send或开发助手逐条驱动代替自主链路验证。A9–A11仍未通过。

### 名称策略与真实队列增量（2026-09-15）

**后续纠偏（本节下列队列记录为失败历史）**：用户再次明确原聊天内容读取为截图+OCR。新增 `name-session.ps1` 视觉全文转录偏离要求，已撤下；默认名称驱动显式unavailable，`weixin_session_observe` 注册恢复既有 `createWeixinSessionObserveOperation` OCR入口。原 `historyRead.ts`、p4 `run.ps1`、`ocr_chat.py` 均未修改。用本机当前截图运行原 `classify` 读取34行，识别到最新问候，耗时6.03秒（一次样本、非性能验收）。测试Runtime停止，两个服务端执行开关及本机sessionTasks/v2能力关闭。真实自动监听仍缺截图接线，不把本地OCR读取成功记为10轮通过。

- 用户将测试联系人改为 `waynelu`，本机实际显示名 `WayneLu`。后续不再向原目标下发测试。
- 已接入 `weixin_name_resolve`、真实名称观察、单条发送与 Runtime 受许可签名桥。服务端消费同租户/用户/设备的近期真实定位回执，创建 `resolved` 名称上下文；无需账号微信号，不写虚假 `verified`。发送仍受桌面锁、发送前复验、原许可单调时限、不可重复提交 journal 约束。
- 服务端名称/工具定向验证25通过；Provider 名称核心8通过（包含时钟回退取消），Runtime 签名桥与v2写路径16通过。Runtime全量首次164通过/2失败，失败来自与同期真实只读探测争用桌面锁；探测结束后受影响桌面锁测试4通过。独立测试及代码审查已执行，许可时限P1已修复复核。
- 本机 Runtime 0.2.13 已上报 `session_task_v1`、`session_observer_v1`、`weixin_message_send_v2`。两个执行开关仅对本次授权租户启用，不代表灰度验收通过。
- 正式队列定位 invocation `7ae7f841-c3ee-4aad-99bb-416844f6e09c` 已自动搜索并打开 `WayneLu`；联系人唯一性诊断为完整、1项、标签精确相等。随后完整聊天视觉读取返回 `CONTENT_UNAVAILABLE / WebException`，最终 invocation 失败，无发送。此前大小写严格匹配及群聊“查看更多”干扰已定位，联系人完整性提示词已限定分区并经独立只读审查。
- 当前没有已发布的本轮自动聊天任务。对方的新消息仍在窗口中，未被本轮 Agent 自动回复；视觉读取可用性和完整10轮仍待真机验证。

独立CodeReview初轮未发现P0/P1，指出超大整数输入处理P2，已限定安全数值并补测试；增量包审查指出链接/外部 `_pth` 可伪造可搬运自检，已拒绝源根及目录内链接和包外搜索路径。

## 独立收尾验证

最终独立CodeReview确认源根链接检查已生效，本轮新增改动的审查问题均闭环，无遗留P0/P1/P2；这不关闭A8既有回归失败或A9–A11发布门禁。

- `./scripts/dev_test.sh tests/unit/test_edge_session_metrics.py tests/unit/test_edge_session_rollback.py tests/integration/test_empty_db_bootstrap.py -p no:cacheprovider -q --tb=short`：16 passed，5.98s，无失败/跳过。
- `npm --prefix clients/weixin-cli run build:main`：退出0；`node --test clients/weixin-cli/dist/tests/ocr-portable.test.js`：3 passed，0失败/跳过。
- 主控 `node --check clients/weixin-cli/scripts/pack-portable.mjs`、`python -m py_compile scripts/edge_session_metrics.py`、`git diff --check` 通过（仅既有换行提示）。未触前端，无需机械重复C4前端build。
- 底座独立回归的完整参数为：`tests/unit/desktop_automation tests/unit/local_tools/test_write_result_billing.py tests/unit/local_tools/test_security.py tests/unit/local_tools/test_repository_state.py tests/unit/local_tools/test_proxy_tool.py tests/unit/local_tools/test_pairing.py tests/unit/local_tools/test_overlay_heal.py tests/unit/local_tools/test_catalog_providers.py tests/integration/test_local_tools_api.py tests/integration/test_desktop_automation_api.py -p no:cacheprovider -q --tb=short`，统一经dev_test.sh。
- 本轮没有DDL或应用入口行为修改；空库实测已补，两进程启动与生产安装仍保留为最终发布门禁。没有运行未获授权的真实观察/发送，也没有生成发布许可或提交代码。

## OCR 真机链路后续进展（2026-09-15，进行中）

以下进展取代前述停用状态，不将历史失败改记为通过。名称定位复用 Qt 搜索 popup 的 PrintWindow，支持本机“最常使用”分区，聊天正文使用本地常驻 RapidOCR。正式定位及 session observer 成功，后者建立真实消息基线。独立定向 mock 验证 18 项通过；单行正文视觉换行 receipt 修复经测试和独立审查。

首次任务已发布并由 Runtime 领取，开场白进入输入框；整窗哈希校验误拒绝 Enter，发送结果记 unknown、未重试。只读复核：输入 OCR 逐字一致，消息帧匹配、整窗帧不匹配。该任务已通过工作台停止并保留账本，当前正按用户要求缩小发送校验范围。

本地 API 启动缺少进程内会话适配器/钩子注册曾使 prepare-send 返回 500；已补生命周期注册，重启后实际写许可成功取得。当前采用本地单会话测试决策 worker，调用产品原 run_decision_tick；完整 background_runner 安装启动形态仍待验收。尚未完成 10 轮，通用媒体及全局同名场景仍非本次普通文字验证范围。

第二次普通文字任务 `c3074f59-f44e-4259-a192-5391e6b1eec7`：Runtime 实际按下发送，开场白已在本人气泡出现，对方也已回复。请求 `5e5f650a-455f-413e-a689-96467a9a8444` 的阶段诊断为 enter→post_read→UI_CHANGED；正式账本保留 unknown，未重发，任务已在工作台停止。离线复现发现上滚后的顶部历史气泡仅部分可见，OCR框跨候选上沿3px触发 crossing_boundary；修复及后续完整循环验证进行中。实际送达不等同机器回执或10轮验收通过。

后续修复已完成真实截图离线复测：顶部截断气泡作为 clipped 历史，不进入正文；固定顶部/右侧边线仅在原像素不变时豁免滚动比较；允许一次新增本人发送同时伴随对方文字回复。独立20项定向测试通过，另3项fake验证帧变化仅重读一次且不重发；独立审查通过。新任务 `6cddf962-9e20-48ae-bcf2-1920c0cf0076` 已从工作台发布，不再重复开场白，Runtime 连续观察成功，等待新入站。真实10轮仍待完成。

该任务随后收到新入站，但连续三次 `alignment_broken` 阻断。真实截图离线复现：居中“星期X HH:mm”被误作图片，滚动后OCR裁框轻微变化导致图片摘要不一致；文字对齐本身成功。已增加严格时间/日期校验、居中窄行及区域包含约束，并排除与气泡相交的时间豁免。前后截图重新解析得到恰一条新增peer消息、对齐通过、无pixel gap。最终独立22项测试及22个边界断言通过，独立审查通过。

已编译更新本机Provider，通过工作台暂停/新基线恢复同一任务（fence=2），真实基线8条，后续连续观察成功；本地决策worker已重新启动。既有恢复协议会跳过恢复前屏幕消息，因此本次入站未被自动回复，仍需恢复后的新入站验证完整决策与发送。没有重发开场白，没有把离线识别通过或监控恢复记为10轮通过。待完善观察器重启后保留待处理消息的恢复能力。

恢复后用户再次发来消息，完成第一轮真实自主闭环：OCR新增消息→batch input_version=1→实际模型调用一次→reply→Runtime单条发送→新增self气泡验证→服务端delivery及invocation均succeeded（`58b9a047-c212-42f9-b693-b9d9305272c2`）。随后OCR回显含一个视觉换行，服务端冻结单行正文比较不一致，误触人工接管；只读核对确认去CR/LF后完全相等，未重发该成功回复。当前继续修复成功发送回显归属，不能将第一轮通过记为连续10轮通过。

回显归属修复已完成：仅实际成功的单行正文允许OCR CR/LF换行差异，空格保持严格、成功发送次数容量继续封顶；纯self/system批次不调用模型，本批peer存在性以SQL检查，正文不可读仍走原错误门禁。独立最终15项通过，既有5项reply集成回归通过，独立审查P1已关闭。API编译检查及重启health healthy；工作台恢复同一任务至fence=3，已保留有效轮数1，等待后续真实入站验证连续循环。

### 第二次发送后的像素校验排障

第二个reply执行 `24f3ccc2-754b-41f0-b3da-f6cadfd0d1ac` 在enter之后post_read返回 `UI_CHANGED_PIXEL_GAP`，账本为unknown，任务blocked。只读当前截图确认本人回复气泡已出现，但不改写正式回执。最近成功证据与当前截图离线比较：8→9条完整文字、唯一重叠上滚262px、新增self及peer各一条、像素校验通过。失败瞬间截图此前没有保存，故没有复现原始gap，不认定滚动动画为确定根因。

已补发送后稳健读取：frame_changed和pixel_gap合计仅重读一次，受原signal/deadline限制，不重新submit、不更新失败像素基线；持续异常仍unknown。pixel_gap抛出前将最新失败帧存本机DPAPI密文（每联系人hash一个文件、原子替换、无明文临时图），诊断保护受原取消及2秒超时限制，失败仍拒绝。其他像素规则未放宽。

独立四组定向26项通过，其中新增11帧连续10次追加的合成序列，覆盖滚动、顶部截断、时间行、同时self/peer及四类拒绝分叉。诊断取消最终增量17项通过，包含真实PowerShell取消测试；独立审查P2关闭。上述不是实际10轮验收。已更新编译后的本机Provider，保留旧unknown请求及停止状态；不重发第二条，不把新的只读截图作为历史成功回执。

接续执行：用户要求立即恢复后，保留旧unknown账本并通过既有控制停止旧任务。经真实名称定位及工作台发布新任务 `d6677809-586d-4a2a-8090-eff057c8b940`，不发开场白，上限8次新回复；Runtime领取fence=1、基线已持久化、连续观察成功，单会话决策worker已启动。此为新基线接续，不是旧unknown回执改判或原任务解除阻断；旧成功1轮仍单独保留。后台只读监控已切换至新任务。

### 2026-09-15 16:12：交接恢复及浮层复现

以15:51发送前成功证据与15:52失败原帧，复用本地RapidOCR、气泡聚合和像素函数重跑：唯一滚动位移为-112px，3727个未覆盖像素全部位于右上浮层范围，OCR精确识别“0条新消息”。这补齐了原交接中尚未证明的根因。私聊原图和OCR正文仅存本机临时目录，未纳入仓库。

最小修复：右上精确浮层只触发有限重采样（每次read最多2次750ms等待）；不豁免像素、不更新基线、不重发submit，浮层持续存在则拒绝。首次基线同样执行该检查。发送的原绝对期限及取消信号覆盖等待、DPAPI和证据写盘，并在更新缓存/验证回执前复核。外层pixel_gap/frame_changed仍仅允许一次重读；最坏6次采样仍受原期限约束。

真实原帧回放：故障帧触发重采样，后续原帧与发送前基线完整像素比较通过。编译客户端真实名称定位和连续3次读取通过（基线完整，后2次unchanged）；初次25秒只读期限不足，60秒版本成功。独立最终定向35项全部通过，无跳过；独立CR发现的取消后可能写verified问题已修复并复核通过。命令：build:main；node --test dist/tests/name-overlay.test.js dist/tests/name-session.test.js dist/tests/name-pixel-coverage.test.js dist/tests/name-continuous-coverage.test.js dist/tests/name-operations.test.js dist/tests/dpapi-cancel.test.js。

旧任务d6677809-586d-4a2a-8090-eff057c8b940已通过工作台停止，保留unknown；Runtime通过下一次真实名称定位重新启动更新后的Provider。新实验准备中，实际发送遇到浮层后自动恢复及连续10轮尚未通过。持续浮层目前仅记录专属错误码，不自动保存该类原帧。正式unknown核对恢复仍无入口，停止后新建任务不算该能力完成。

### 2026-09-15 16:25：新实验再次阻断及验证方式讨论

新任务4cd7b1d5-5089-4f85-a653-72e193b52b50第一轮正式succeeded并ACK，第二轮6684d4a6-a6ef-46a4-b60d-0bb8914bf80a两次post_read pixel_gap后unknown；当前blocked，worker退出。原帧离线重现-112px滚动、9629个未覆盖像素位于浮层，OCR为“个0条新消息”(0.8639)，首版规则未命中且顶部气泡分割受影响。未重发、未改判，连续10轮未通过。

用户要求先解释像素比较原因并考虑替代。当前结论：发送效果与整窗覆盖需分离；直接删像素比较不能继续宣称能检测无文字媒体。默认替代尚未实施，保留文字对象对齐/新增self正文验证方向，后续同时处理frame_changed及非文字能力边界。详细状态以交接文档顶部为准。

### 用户要求：发送效果与观察覆盖分离（2026-09-15）

发送后采用独立read_receipt，本地OCR核对当前标题和文字气泡，通过唯一有序对齐确认新增本人正文与授权正文一致。不运行pixel_gap、浮层门禁或frame相等校验；不更新observer缓存、不声称完整消息覆盖。单次submit、journal、期限/取消、历史同文拒绝及缺文字锚点拒绝保留。常规观察未变。

第二轮失败原帧离线回放唯一对齐、新增1条self，正文摘要与服务端原授权一致。旧unknown未改判或重发。build成功，独立38项通过（name-overlay/name-session/name-pixel-coverage/name-continuous-coverage/name-operations/dpapi-cancel）；CR发现的非空无文字锚点误判已修复并复核通过。未重启活动Provider或进行新发送，真机连续10轮仍未完成。

运行验证补充：已在旧任务blocked时更新Provider（PID72576）；Runtime真实名称定位invocation33fa0930-fa96-46e2-9951-807b67ddfed5成功，未发送新消息。新发送验收仍待接续。

16:36接续：用户要求继续测试，旧4cd7b1d5任务已工作台停止保留1成功/1unknown；新db900756-4db5-410d-bb75-2dd98a9033a8经真实定位、草稿及工作台确认发布，active/fence1/baseline ACK，连续观察成功，worker85256运行，监控已切换恢复。独立OCR发送回执Provider72576已部署，当前0/10，期限17:34:35，等待新的对方消息。

16:38新回执真机进展：db900756任务首轮invocation72db4320-fc96-4651-9997-fd2c4aca39d7正式succeeded且operation-result ACK，Provider发送执行27.7秒，无pixel_gap发送门禁。后续完整观察也成功，下一批同时包含self回显和新peer，第二轮决策已启动。当前1/10，任务active，尚未完成连续验收。


## 2026-09-15 发送动作回执修订

用户明确不再以OCR文字或像素验证发送。实现submitted操作回执，旧unknown保留。第三次旧请求停于draft_ocr，未Enter。真机仅替换原草稿（无Enter）通过；后续self误判、独立验证及部署仍收尾中，不能算连续10轮验收。

17:02：独立Python117、TS32、Runtime1、前端6项及PS拒绝分支通过，build及CR通过。服务已更新，真实名称定位成功。接续任务6059a3ff-f649-46ad-8243-207cf0b764ee已发布active/fence1，baseline ACK及连续观察通过；worker已启动。新语义真正发送待新peer消息触发，不以部署替代10轮验收。

17:05首条新语义真机回执通过：771014ba-3773-4cb2-9495-60343fd0a7e0为succeeded/submitted/applied，Runtime operation-result ACK，Provider 11.1秒。任务active，计1条已执行发送，不宣称送达或10轮验收完成。

17:16：两轮后租约反复换代至fence7，alignment_broken，不能计稳定10轮。周期retention全历史解密阻塞已定位，16条只读实测14.792秒。当前任务维护暂停；修复保留磁盘门禁/历史数据，去掉无效周期回放，等待独立测试与部署。

17:21：retention修复build/独立48项有效测试/CR通过并部署，同任务fresh_baseline恢复active/version4/fence8，baseline ACK，连续普通observe约3.2秒，续租推进。累计2条、修复后0条，不能拼接为稳定10轮。

17:24：修复后第1条（累计第3条）submitted已ACK，32.345秒，结算后等待1.983秒；Provider10.983秒。fence8跨多个周期仍续租，继续观察，不算10轮通过。

17:27累计第4条/修复后第2条ACK；44.402s，模型及结算19.105s、结算后3.192s、桌面Provider11.375s。fence8无新增gap/unknown，继续观察。

17:33顶部截断历史被误作完整气泡导致crossing_boundary；修splitClippedTop平顶背景+墨迹计数并保留真实fill锚点。原图/窗口只读read已success，11objects/1clipped；无发送或草稿动作。当前4submitted，维护暂停version5，待最终独立复核部署。

17:38修复已build/独立34项与最终13项复核/CR通过。更新Provider84464，当前任务fresh_baseline恢复active/version6/fence9，baseline ACK，连续observe3.1/3.2秒成功。worker85948。累计4条，不计稳定10轮。


### 2026-09-15 用户批准：pixel_gap有界重读

生产readWithPixelRefresh包裹overlay读取，首次pixel变化退出当前采样并完整重读，持续变化不再抛pixel_gap。首次退出发生在证据和缓存写入前；第二次仍执行现有标题/OCR/frame_changed/取消检查。删除该提示分支的故障截图DPAPI写入。build通过，独立8文件43/43通过、无跳过，独立CR通过；覆盖持续变化、overlay插入不重置预算、最新读取结果与watermark去重、错误和取消。未部署到运行中Provider，未把合成回归当作真机连续10轮。用户其余流程先复盘再批准修改。
