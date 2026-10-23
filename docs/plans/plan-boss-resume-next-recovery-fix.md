# BOSS 续筛评分、恢复与故障反馈修复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 续筛模型归属和恢复证明 | ✅ 完成（2026-10-14） | 真实 Worker 续筛 3 项通过：正常、评分后恢复、扣费后恢复 |
| Phase 2 | 中断展示、阶段进度与 Trace | ✅ 完成（2026-10-14） | Web build、隔离浏览器宽窄屏、独立 Trace Worker/PG 5 项通过 |
| Phase 3 | Provider 回执兼容和安全诊断 | ✅ 完成（2026-10-14） | Core 9 项、Runtime 35 项通过；原始坏回执触发原因仍未知 |
| Phase 4 | 独立测试与 CodeReview | ✅ 完成（2026-10-14） | 独立定向验收通过，CR 无未解决 P0/P1/P2；源码提交 49556694，未部署 |
| Phase 5 | 客户端 r10 打包交付 | ✅ 完成（2026-10-14） | 标准验包、实际 NSIS 内容核对、独立隔离启动通过；未执行现场安装 |
| Phase 6 | 登录/设备错误分类与无效果重试 | ✅ 完成（2026-10-20） | 独立定向回归通过；含next原设备无效果登录重试，未部署 |
| Phase 7 | Worker切换原调用观察与迟到回执 | ✅ 完成（2026-10-20） | 独立111单元/18集成通过，CR无阻断；原ID消费及幂等计费已验 |
| Phase 8 | 同会话结束等待及恢复交互 | ✅ 完成（2026-10-20） | 普通取消blocked真实Worker增量通过；独立前端62项、宽窄屏通过，CR无阻断；未部署 |
| Phase 9 | 复审：精确未决集合与准入收窄 | ✅ 完成（2026-10-20） | 独立真实PG30项、相关单元69项通过，CR无阻断；旧bool兼容与transport负控已覆盖 |
| Phase 10 | 复审：受权只读页面核验及停止证明 | ✅ 完成（2026-10-20） | 2.5存量未知受管资源闭环；独立120项及宽窄UI通过，CR无阻断；未部署 |

## 存量未知执行资源闭环（2026-10-20，2.5已完成开发）

用户授权继续解决存量unknown/result_json=NULL且缺generation的永久hold。沿共同契约2.5新增显式stop_resources请求，不隐式取消/重发；以原设备配对身份、原home lease及现有DPAPI Job集合证明受管边界停止，不给旧调用硬补代次。唯一开发者同时维护Host/Core、后端、Web及回归；独立测试/CR只读。先拒绝其他真实活动调用，再同桌面锁暂停Provider新建、回收本Runtime空闲受管池、逐Job终止核验0，最后恢复惰性创建；只解绑定IDs并保留unknown。

最小覆盖标记在既有DPAPI ledger增加可选business_started；新false、probe永不置true，普通受权BOSS调度每代持久化true一次，旧缺字段记录兼容。必须有可信业务覆盖及非空Job集合；新home先pure probe（甚至重开）不能签历史证明。同步所有严格ledger读取者。旧非受管活实例与系统检查不可用分别报告；空Jobs旧session不阻断，非空旧session可用原生保护回执或确认已注销无进程闭环，不要求重启Windows。v3目标冲突/损坏不阻资源停止；过期或替代scope不能领取/started物理回收。消费仅限原BOSS调用，纯读保持独立。

### 2.5开发者自测与冻结（2026-10-20）

- Runtime/Core `npm run build:main`通过；`node --test dist/tests/recoveryStop.test.js dist/tests/recoveryObservation.test.js dist/tests/connectionRecoveryResources.test.js` 40 passed/0 failed/0 skipped。新增12项包括真实Windows任务专属native Job及子进程、旧无business字段、同代业务无generation、probe-only新home重开、非空Jobs、跨session保护回执、busy、每代一次DPAPI写失败、永久admission与MCP握手中途shutdown竞态。未操作用户Runtime/Chrome。
- PostgreSQL统一隔离入口先运行三文件42 passed；后续v3六项定向6 passed/19 deselected，覆盖旧unknown/result_json=NULL（真实cancel-only Worker收尾）、scope保留/新意图、busy/fence、页面失败、模式不可升级、过期A后B可派发和目标冲突。首次新增过期用例暴露时间比较问题，改为数据库时钟后通过，普通业务时限不改。最终独立测试仍需对冻结状态复验。
- 宿主统一脚本三个unit文件60 passed/0 failed/0 skipped；扩大回归首次2个旧Mock缺新增admission查询取值，修正fixture后通过，未放宽生产门。
- Web `useAgentRunner.test.ts` 52 passed，含显式stop及busy；最终`npm run build`退出0（1054模块/1129产物）。源码及dist已冻结，交独立测试/CodeReview；本节不冒充独立验收。无真实BOSS动作、Windows重启、提交或部署。

### 2.5独立验证与最终边界（2026-10-20）

- 独立不同用例共120项通过：Windows native/纯读15、真实PG27（正式verification25+独立跨端2）、前端78；下述两项补断言重跑不重复计数。无未解决失败或skip。
- Runtime目录`node --test dist/tests/recoveryStop.test.js dist/tests/recoveryObservation.test.js`：15 passed，28.60秒。实际DPAPI、Windows Job及dummy子进程，覆盖旧无business字段与当前代业务、新空home纯读伪覆盖、busy、跨session收尾、permanent admission、MCP握手关闭与每代一次写入。
- 根目录WSL统一入口`bash scripts/dev_test.sh --isolated-db .tmp/recovery-stop-test-20261020/test_native_api.py tests/integration/agent_runner_service/test_local_verification.py -p no:cacheprovider -q`：27 passed，168.83秒。Windows同步`node .tmp/recovery-stop-test-20261020/native-bridge.mjs`产生真实native→ProviderSet/Host→MCP（随机不可达CDP）结果，经实际设备鉴权result API/PG解除对应hold；旧unknown/result_json=NULL及业务历史不变。页面失败也保留真实stop proof，新意图create/claim/start实测到running。补充started断言后独立两项重跑2 passed，24.32秒；不另算新用例。
- 独立跨端桥接的云端terminal/settled为隔离测试夹具直接设置，不冒充该步真实Worker结算；正式verification用例保留已有cancel-only Worker/Finalizer覆盖。未结算或改动真实客户故障任务，无真实BOSS/Chrome操作、Windows重启或真实外发。
- 前端`npm exec vitest run web/__tests__/composables/useAgentRunner.test.ts web/__tests__/api/runner.test.ts web/__tests__/api/runnerEvents.test.ts web/__tests__/composables/useAgentRunnerEvents.test.ts`：78 passed。`node ../.tmp/recovery-stop-test-20261020/ui-stop.mjs`退出0；1280/390页面无溢出，只读不带stop_resources、显式停止带true，stop成功而页面未知正确分开。主控实看三张截图；ui-report.json为cleanup:true、无unknownAPI或external请求。
- 独立CodeReview通过，无未解决P0/P1/P2；主控最终Runtime/BOSS `tsc --noEmit`、dist模块import、改动/新增34个Python文件内存compile及`git diff --check`均退出0。生产源码/dist冻结后未再改动；本次仅文档收尾。
- 已解决原Runtime/home有可信受管Job证据的存量无generation资源hold。真正未受管旧实例须先结束旧实例；新空home没有历史覆盖、损坏证据不能签证明；不会用MCP池证明核销skill-runner/Python原调用。这是准确边界，不新增Windows重启常规门。旧业务结果仍unknown，继续操作必须是用户新意图；原未知外发不重放。未提交、打包发行或部署，提交推送由主控依授权执行。

## 观察与停止解耦纠正（2026-10-20，优先于下方Phase10历史设计）

按共同契约2.4实施，唯一开发写入者维护本批代码、关联设计与定向回归。删除scope专用重启见证；只读页面观察不依赖停稳，Python/sessionTasks配置不禁用观察。结果分别公开页面是否读到、有限期望是否匹配、旧资源是否有可信停止证明。导航/清除筛选复用既有判定；筛选计数相同不能证明完整条件相同。未知发信不重放、不变更业务结案；旧回执缺原代绑定的停止证据缺口如实保留，不要求用户常规重启Windows。公开历史核验入口按实际未决回执投影，不写数据库。

旧v1新增能力未发行，观察改为version=2/capability v2，两端同时验证；旧原调用回执保持兼容。本轮四项修正已完成自测、独立测试与CodeReview；停止闭环的历史缺口仍按下文保留，不声称整套恢复已完成，未提交或部署。

## 复审纠正（2026-10-20，优先于首轮准入设计）

用户复审指出历史已结束失败、缺失指纹和会话级登录限制均扩大准入阻断，且页面核验尚无实际路径。首轮Phase 6～8的通过结果仅保留为当时已覆盖场景的证据，不能据此宣布四项反例已解决；本轮继续沿用本计划登记，不另建进度文档。

### Phase 9 实施决定

- `end_waiting`在原owner/调用归属验证事务内，按明确的资源完成事实冻结真正未决调用ID集合，准入只查询该集合，不将普通历史失败一概封禁；Engine ToolFact.completed本身不证明子进程停稳，transport未知即使Engine已结束仍须留在集合。无法建立可信设备hold的legacy未决引用直接拒绝结束，不静默漏掉。再次调用幂等，不因后续历史变化扩大集合。
- 原始结构化业务失败与transport abort分开。历史已结束`WRONG_PAGE/effect=none`等不进入未决集合；迟到有明确业务完成语义的无效果失败可投影失败并解除该调用的资源未决状态，不能把`CANCELLED/INTERNAL_ERROR`或未知效果类推为停稳。旧存储late回执的stopped布尔只是旧派生值，按当前有限分类重新投影，不以旧false永久拒绝、也不以旧true放行transport未知；原历史和幂等计费不变。
- 设备冲突只匹配原device，或双方指纹非空且相同；缺失指纹不推出另一设备与其必然冲突。身份/tenant/user条件保持。
- 删除同会话最近`NOT_LOGGED_IN`全局准入限制。原操作恢复沿原Runner/LocalPhase/expected_device绑定，新授权意图可以使用当前可信选择的另一设备，不能借此迁移旧调用。
- 本批代码唯一写入者为开发子智能体，主控维护计划/设计/契约和索引；独立测试与CR只读。独立反例在`.tmp/recovery-counterexamples-20261020/`已复现8 failed，修复后纳入正式测试，不修改真实任务。

### Phase 10 已确认事实与设计边界

原“核对并继续”只提交resume，unknown仍拒绝，因此首轮尚未交付页面核验闭环。新增路径须沿原受权invocation/device/目标，严格无点击的快照观察与本机执行资源停止证明独立核对，不能将后者前置为纯读门禁；不得按`effect=none`或工具说明里的“只读”给整个工具类别豁免。`boss_filter_options/list_jobs/resume_batch`可能实际点击/滚动，不能直接当探针。

现有Provider busy、桌面锁、页面状态均不足以证明原进程树停稳；Runtime的受保护Windows Job账本、generation fence、重启见证才是可复用依据。先完成新观察能力与原目标/执行代次绑定的producer/consumer兼容登记，再实施依赖它的恢复接线。原目标未持久化、旧客户端缺能力等缺口必须明示，不重新挑标签假装原目标，不自动重放未知发送。精确接口待只读调查确定，不以本节声称已实现。

### Phase 10 历史实现：只读页面观察v2（2.4，四项修正已验证；停止缺口由上方2.5替代）

1. 复用既有Web/内部verify-local路由和受权invocation，仍固定可信tenant/user/session、原Runner、原device、资源未决集合及可取得的原目标。request严格为client_request_id；不隐式取消或重放原操作。
2. Host观察请求和结果version=2，能力runtime_recovery_observe_v2。只要BOSS插件包含纯读probe即可声明，不依赖Python/sessionTasks配置或原生监督是否启用。旧客户端缺能力明确提示升级，配对、claim、permit及H1/H3不改。
3. 删除scope专用重启见证和observeRecoveryStop。首次就可运行boss_recovery_probe，只连接已有Chrome并使用原目标；无原ref时明确current_unbound，陈旧ref不换页。不启动、激活、点击、滚动Chrome，也不进入视觉dismiss或恢复业务。
4. 最少expectation从原受理调用派生：boss_goto携带既有target，boss_clear_filter携带清空期望，boss_filter可使用原回执中的filter_count（没有就不猜）。CLI复用isBossPage和FilterSetter只读徽章/面板判定，page_summary给出observed、page_kind、result=match/mismatch/unknown及可读的filter_count/panel_open/count_result。导航路径与清零可匹配；完整筛选仅计数相同仍为unknown，不证明具体选项正确。
5. v2当前仅有纯读证据，因此resource_stopped=false，stop_error_code=LOCAL_RECOVERY_STOP_UNPROVED。页面成功仍可state=succeeded/page_observed=true；这只表示观察成功，不表示原业务成功或旧资源停止。页面失败单列有限page_error_code，不丢掉停止未知这一独立事实。服务器只接纳精确scope/device/IDs及有限页面字段；不能凭snapshot、busy=false或锁空解hold。
6. 2.4当时仅交付纯读，原state=unknown/result_json=NULL且缺代次关联的hold仍未解决。此历史限制已由上方2.5设备受管边界停止路径替代，不再要求补逐调用generation映射；旧ProcessSupervision.recover仍保留，原业务unknown历史不改、未知外发不重发。
7. 旧未发行v1不会被默认恢复链调用；为已有接受的v1 invocation保留原接纳语义，不修改其旧证明含义。新v2不会生产或接受伪造的resource_stopped=true。
8. Runner GET/list和事件reset仅查询回执，按与设备准入相同的实际未决判断投影核验入口，不删冻结ID/审计，不写数据库。已渲染按钮与迟到完成竞争时，verify-local返回最新纯读投影，不派探针、不报409；前端立即清理旧入口，无需刷新页面，不新增永久轮询历史。
9. Web文案分别说明已读页面、期望匹配情况、停止未知，不要求常规重启Windows或Runtime。不因页面匹配自动resume旧unknown；普通业务仍沿已有CLI本地恢复与20倍后台视觉API。

### Phase 10 本次修正自测（2026-10-20）

- Python定向3文件49 passed/0 failed/0 skipped，dev_test.sh宿主隔离配置，无外部数据库连接；新增4项late回执纯投影回归。
- Runtime build:main退出0，recoveryObservation 3 passed，证明首次即纯读、Provider字段不能伪造Host停稳、页面失败分别返回；不执行原生重启或真实Chrome。
- BOSS build:main退出0，recovery-probe 9 + filter-setter 40 =49 passed；复用真实纯读判定，CDP仅最外层替身，无页面点击。
- Web useAgentRunner 50 passed，含终态停止观察后旧按钮与迟到回执竞争、页面成功而停止仍未知。Web build首次退出0（1051模块/1127产物），静态文案纠正后最终build同样退出0（1051模块/1127产物）。
- 集成test_local_verification已更新v2语义并增加late成功/明确失败两种竞争；本机无docker及可用任务专属PG，本开发者尚未运行，不以旧v1结果代替。
- 独立测试及CodeReview已完成，最终结果见下方；未提交、推送、打包或部署，没有真实BOSS操作。

### Phase 10 本次修正独立验证与遗留（2026-10-20）

- 正式真实PG三文件覆盖38项：`test_local_verification.py`、`test_local_recovery_admission.py`、`test_local_recovery_admission_safety.py`。首轮36 passed/2 failed，失败为新增竞争fixture只detach而尚未真实结算；正式源码未变。复用现有CLI cancel-only Worker与Finalizer后，明确先到cancelled+settled再竞争，定向重跑2 passed/17 deselected、23.87秒、exit0；17 deselected是该次定向选择，不是遗漏，已有效的其余36项未机械重跑。
- 最后补验命令：WSL隔离环境经`bash scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_local_verification.py -k late_completion_racing_verify -p no:cacheprovider -q`。不用正式库或agent2；不为测试放宽LOCAL_VERIFICATION_NOT_READY。
- 额外10项真实PG与实际Host→ProviderManager→MCP→CDP→PG组合、旧v1兼容/跨scope负控、DAL get/list/event reset的迟到回执反例通过；覆盖页面match/mismatch/unknown独立于stop未知、没有虚构停止证明或重放旧业务。
- 独立前端4文件76 passed，含已经停止观察的终态旧按钮竞争；开发者最终Web build退出0（vue-tsc、依赖边界、1051模块、1127产物校验）。Runtime/BOSS两个build及定向回归见上方自测；没有执行真实BOSS操作或真实Windows重启。
- 独立CodeReview完成，无未解决P0/P1/P2；意见涉及的页面事实、静态文案、共同契约历史表及Desktop引用均已纠正。
- 2.4四项复审修正完成时，旧unknown且回执永久丢失的资源hold仍未闭环；此为历史阶段结论，现由2.5显式停止受管边界路径替代，不能读为当前永久遗留。纯读本身仍不签stop proof，也不能把页面可读视作原业务成功。

### Phase 10 旧v1验证历史（2026-10-20，不能代替上方v2修正验收）

- 最终独立CodeReview已完成：当前无未解决P0/P1/P2。已复核精确准入、归属/锁序、迟到回执/幂等计费、Host重启见证、纯读CLI、结果接纳与Web交互；旧请求scope错配、核验隐式取消、可信原目标遗漏、陈旧标签导致资源永久阻断均已修复。`git diff --check`通过。
- 开发自测：`test_local_verification.py`真实隔离PG17 passed/0 skipped（含双hold、旧key、原target冲突、只读不取消及disabled入口）；`npx vitest run web/__tests__/composables/useAgentRunner.test.ts --reporter=dot`49 passed/0 skipped；Runtime `npm run build:main`退出0，`node --test dist/tests/recoveryObservation.test.js`真实Windows原生/DPAPI3 passed/0 skipped；BOSS同build退出0，`node --test dist/tests/recovery-probe.test.js`3 passed；Web `npm run build`退出0（vue-tsc、边界、1051模块/1127产物校验）。PG命令沿隔离runner内`bash scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_local_verification.py -p no:cacheprovider --assert=plain --tb=short -q`。以上是开发者自测，不代替独立组合验收。
- 最终冻结后独立验收：verify-local17、admission8、safety11及WindowsHost/云端真实桥接2，共38 passed/0 failed/0 skipped，69.61秒、exit0；独立前端3文件64 passed。真实BFF→设备Bearer认证claim/started/result→Windows Host/原生DPAPI/实际BOSS stdio CLI→精确解hold已串联。原业务unknown保留，新普通页面意图可claim/started；双hold A证明仅解A，B仍阻普通GUI而可观察，伪read/provider拒绝。首轮失败是独立fixture缺实际Host会带的legacy provider_id，修fixture为真实cap后完整重跑通过，未改生产。
- 独立Windows正式原生适配3 passed/0 skipped；实际Host/ProviderSet/native/MCP CLI探针只调用getTargets/attach/DOMSnapshot/detach，无focus/click/scroll/launch。原target陈旧及SDK throw均保留真实取得的stop证明、page=false。跨boot边界仅受保护见证的隔离替身，未实际重启电脑、不执行真实BOSS业务；原生Job和DPAPI使用真实组件。最终真实浏览器1280px/390px通过：三入口、重启及页未核验提示、结束后历史核验入口、草稿附件保留、普通输入不隐式resume、用户新意图可发送；无未知API或外部业务请求。主控已实看390px三入口、重启及页未核验截图，布局与事实文案正常。
- 本状态开发者补做受影响6文件单元回归115 passed/0 skipped。证据目录`.tmp/recovery-phase10-20261020/`，含`cloud-bridge-report.json`、`dual-hold-report.json`、`bridge-produced.json`、`visual-report.json`、宽窄屏截图和可复现独立脚本；不存在的stdout日志文件不作证据链接。
- 独立补充真实并发verify/end竞争1 passed/0 skipped（1.17秒、exit0），无死锁、半创建或跨集合解锁，原unknown保持；最终PG及实际跨端合计39 passed，前端64 passed、Windows原生3 passed。最终视觉exit0，`visual-report.json`为cleanup:true/unknownAPI:[]/external:[]；测试专属runner、PG、匿名卷及internal网络均已清理并查询确认，Vite/Chrome/dummy及CLI Job已回收，未动其他服务。
- 尚未提交、推送、打包发行或部署；旧客户端没有该新增能力，需要升级匹配的Runtime/BOSS插件后使用。

## 2026-10-20 增量设计与开发范围（首轮历史，冲突以上方复审为准）

依据[本次诊断](../incidents/recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md)，用户授权优先开发错误分类、Worker切换恢复、同会话恢复死结三项。沿用[Runner设计](../system/agent-application-architecture-design.md)及[共同契约](../system/runner-desktop-runtime-integration-contract.md)。历史Phase 1～5完成不代表本次故障已解决；本批不修改180秒期限或PowerShell性能链路。

### 行为与安全边界

1. `NOT_LOGGED_IN/effect=none`明确引导登录；`DEVICE_UNAVAILABLE`引导检查原设备。合法新尝试仍核验原设备和浏览器目标，不靠修改全局selected或新配对绕过旧任务。
2. Worker中断不重新派发原桌面调用。恢复沿原invocation查询；原claim认证的迟到回执可作为对账证据保存，租约回收产生的unknown与客户端明确unknown分开。旧unknown历史不改写，用户显式恢复且归属和结果证明通过后才能消费已知结果；不从回执获得新的执行许可。
3. 新增Web明确结束等待动作，服务端核验tenant/user/session、无活跃Worker及本地执行事实，事务记录用户选择与取消标记，随后复用cancel-only Worker和Finalizer结算、保存历史并原子释放聊天占有。保留未知事实与输入附件，不伪报桌面动作成功或已停止。
4. 释放聊天后，未确认停止的原设备调用仍阻断新的冲突桌面动作；纯聊天可以继续。Runtime可能在MCP超时/abort后回报unknown而Provider仍未退出，因此收到回执本身不证明停止；未知回执仅保留对账事实，不解除设备阻断，不允许自动重放发消息/打招呼。
5. 前端展示结构化原因和可执行入口，不把每条后续消息隐式重复提交为已被拒绝的恢复控制。继续操作由用户明确选择，保留输入与附件。
6. 普通取消遇unknown而核验阻断时，`cancel_requested`不代表即将结束，仍保留明确结束等待入口并禁用恢复执行；只有已写`detached_local_wait`的真实收尾状态才隐藏入口、提示稍候，不能把两种状态合并。

### 共同检查点及文件责任

主控维护本计划、Runner设计、共同契约与索引；开发子智能体唯一维护本批代码和回归测试。代码范围为本地调用repository/proxy/恢复适配及续筛、发送、模型来源证明消费端，Runner恢复与公开Web网关、Web Runner client/composable/聊天组件及定向测试。不修改Runtime配对、Device claim/permit格式、H1/H3 schema、全局依赖或客户端安装包。新增Web入口为兼容增量，旧客户端不调用即保持原行为。

- Web `POST /api/chat/runners/{runner_id}/end-waiting`转内部`/v1/runners/{runner_id}/end-waiting`，请求严格空对象，响应沿既有`success/runner`；公开Runner仅对待核对本地调用增量投影`local_recovery:true`，不能凭历史已完成调用阻断普通暂停恢复。
- 入口拒绝非chat/Web主体、跨主体、活Worker及不适用等待；重复结束幂等。旧服务404明确提示更新，不降级为普通取消或创建新任务。
- `LOCAL_DEVICE_STOP_VERIFICATION_REQUIRED`阻断未核对设备；`LOCAL_LOGIN_ORIGINAL_DEVICE_REQUIRED`阻断未登录重试静默换设备。创建/领取/started/许可与结束标记使用短事务tenant准入锁，执行和长轮询等待不持锁；同主体同机指纹设备亦参与阻断，缺失指纹保守处理，指纹不创造授权。
- 迟到回执只写既有`result_json._late_result`，原unknown/state/effect/error保持；仅明确成功且effect已知或有限前置无效果错误可投影为已完成事实。发送、续筛父调用、截图模型来源及领域恢复均在原归属证明后使用同一观察视图，不改写原存储事实。

### 验收要求

- 登录失败与设备不可用分别提示；明确none后合法新尝试，unknown不能绕过证明。
- 切换Worker仍观察原ID；迟到结果幂等、原claim/租户/设备校验、已知终态不可覆写、不重复派发或计费。
- 复现unknown后重复恢复拒绝；明确结束等待后同会话可普通聊天，原未知事实仍在，新冲突设备动作被阻断。
- 活跃Worker、跨租户、伪造回执、重复结束、取消与迟到结果竞争均有定向验证；不修改正式服或agent2原任务。
- 开发自测后独立测试、独立CodeReview；前端build及相关隔离启动/import检查。完成后更新本区及索引，未获授权不提交、不部署。

### 2026-10-20 验证记录与交付限制

- 独立后端单元：经`dev_test.sh`运行本地等待、计费、repository状态、proxy、续筛与owner证明6个定向文件，111 passed / 0 failed / 0 skipped。
- 独立真实数据库集成：`test_local_wait_worker.py`（5项）、`test_local_wait_recovery.py`（5项）、`test_local_send_recovery.py`（4项）、`test_resume_next_owner_flow.py`（4项），18 passed / 0 failed / 0 skipped，171.08秒。普通取消阻断增量随后单独验证，不以旧18项结果冒充新增用例通过。
- 普通取消阻断增量：正式`test_actual_normal_cancel_blocked_unknown_then_explicit_end_can_finalize`独立1 passed / 0 failed / 0 skipped（同文件5项deselected），9.16秒，真实cancel→Worker核验等待→公开结束入口→end→Finalizer释放claim，原GUI继续阻断。受影响公开投影单元14 passed，最终独立前端三文件62 passed / 0 failed / 0 skipped；保留旧normal cancel出口、恢复按钮禁用与草稿附件不丢均已覆盖。
- 真实链路包括Runner API、Web Gateway lifespan、Worker进程、Finalizer与新聊天，无重复派发；同机不同设备并发准入、迟到结果并发只扣一次、账务失败回滚无半扣款且回执保留、跨主体与活Worker拒绝均有证据。仅最外层模型和设备IO使用隔离替身，没有执行真实BOSS动作。
- 测试采用任务专属internal Docker网络、PG16与官方pgvector源码编译扩展；随机测试库，禁用dotenv，数据库和日志库均隔离，不使用既有业务库。Docker Hub拉取TLS超时后改用专属容器编译扩展，未修改既有服务。
- 开发自测前端60项通过，最终`npm run build`退出0（vue-tsc、依赖边界、1051模块构建及Web产物校验）。独立浏览器曾发现具体提示被安全归约吞掉，已改固定文案精确白名单；未放宽任意服务器错误文本。独立前端及普通取消入口最终复验见随后完成记录。
- 本批不修改180秒执行截止或慢输入链路，不要求更换Runtime客户端。Runtime彻底崩溃且原回执无法找回时，没有可信停止证明，设备操作继续阻断；不能以用户一句“已停止”或重新配对伪造证明。结束等待只释放聊天，不撤销已发生的业务效果。
- 最终独立CodeReview未发现未解决P0/P1/P2；迟到成功消费链、普通取消unknown入口、具体提示精确白名单等审查发现均已修复并复核。主控复查最终diff及`git diff --check`通过，并查看390px恢复界面，提示、两按钮、草稿与附件无遮挡。
- 最终真实浏览器脚本退出0：1280px/390px覆盖普通输入不重复resume、拒绝恢复具体指引、旧服务404升级提示、结束中草稿附件保留、普通取消blocked仍可结束且恢复按钮禁用，无未知API或外部业务请求。过程中的补充输入fixture、toast定位和侧栏动画问题已修正测试脚本，未为错误fixture放宽产品。证据位于忽略目录`.tmp/recovery-independent-20261020/`（`final-backend.log`、`ui-report.json`及`ui-recovery-1280.png`、`ui-recovery-390.png`）。任务专属Runner/PG及匿名数据卷、网络、Vite/Chrome均已清理并核验。
- 尚未提交、推送或部署，未修改正式服/agent2故障任务。以下原有各节是2026-10-14历史批次记录，不作为本批新接口与验证结果。

## 事实与范围（2026-10-14历史批次）

2026-10-14 agent2 的 `tr_runner_c3477033de4d4118968c98bd9316cb0b` 在本地读取三份简历后，云端 `resume_recognition_covered` 调用被 `COVERED_USAGE_OWNER_REQUIRED` 拦截；14:26 的恢复又被 `LOCAL_PHASE_INTENT_CHANGED` 拒绝。前一轮 Batch 正常完成。另一个会话的 `tr_runner_71d638da69674df8ad203ca0c79a2bf6` 因 Provider 回执不可解析进入效果未知等待，未展示说明。

修复包括 Next 在评分、模型来源、领域恢复的完整证明；系统派生参数与模型原始输入分开核对；中断/核验等待始终显示可理解状态；工具阶段完成不冒充整个任务完成；Trace 保留安全错误码并结束未完成工具 span；拒绝恢复不得改变旧输出时间；Provider 回执兼容多文本块但拒绝歧义、错误包装和畸形结果。

## 共同契约检查点

- 遵守 `AID-RUNNER-DESKTOP-RUNTIME` v2.0，现有 Runner API v1 与 MCP 返回结构不升级、不新增业务入口。
- 本批唯一整合者为主控；续筛证明子任务仅写 Runner 证明模块与相关测试，Provider 子任务仅写共享 Host 解析模块与相关测试；主控负责状态展示、Trace 和文档。
- 兼容旧 checkpoint：有明确核验说明则保留；缺少说明由展示层提供保守提示。既有状态语义不变，不把 interrupted 当 completed/failed。
- 保持 C08/C09 原身份、设备和意图验证；保持 C11 效果未知不自动重放。任何外部副作用不以恢复请求作为新授权。
- 不修改公共 schema、Device API、binding、全局包或 lockfile；如确需修改，先补版本和生产消费兼容检查。

## 验证要求

1. 使用真实证明组件验证 Next 正常评分、续筛中断恢复、重复结果幂等；拒绝跨租户/会话/设备及伪造父引用、排重名单、截图和策略。
2. 前端验证 interrupted/verification 文案、阶段进度、拒绝恢复后的时间稳定；运行 Web build。
3. Trace 验证错误码不含异常敏感内容、未完成工具 span 不伪报完成、后续成功恢复不残留失败状态。
4. Host 验证合法 structuredContent/文本结果、错误包装、多块歧义及不泄露 Provider 原始内容。
5. 用户未授权部署；agent2 原故障任务不修改、不恢复，不发送真实招呼。真机发布验收待明确部署授权后执行。

## 验证记录

- 开发者：续筛证明/历史/识别定向单测 54 passed。Trace 与 Worker 初轮 24 passed；后续四种未完成 Span 状态已纳入独立重跑。
- 独立验证：证明与 Trace 单测 49 passed；真实 Worker + 隔离 PostgreSQL 5 passed（终态事务失败→重试成功清旧错误，四种未完成状态真实落库）。
- 独立 Next 集成：专属临时 Runner/pgvector 容器，外网禁用，模型与设备仅测试 IO，`test_resume_next_owner_flow.py` 3 passed / 0 failed / 0 skipped，148.92s，退出码 0。覆盖正常续筛、`resume.evaluate` 提交后强制中断恢复、`resume.fee` 提交后强制中断恢复；验证真实模型授权、截图证明、入库和计费，不重复派发、识别或扣费。
- 独立前端：工具函数和实际组件挂载 16 passed；等待状态中文标签调整后对应 4 项复验通过。开发者既有 Batch 真实读取、评分、识别费和入库场景 1 passed。
- Provider：Core build；解析/续筛能力 9 passed；Runtime build:main；真实 stdio、大回执、崩溃、v2 写路径及契约 35 passed。独立 SDK 回执 6 passed。
- 主控：Web build 最终状态通过；隔离 API 数据下真实 Chrome 检查 1280px/390px 中断正文为空提示及旧 verification 无 question 提示，无真实业务请求。临时验收脚本与截图存于忽略目录。
- CR：修复 finalization-only 重试成功仍残留错误、未完成 span 默认成功；同步 TraceDetail 状态颜色/中文标签。保留历史失败 span，成功最终态清错误摘要。
- 环境边界：Windows 单测使用项目 venv；完整 Runner 集成在本机 WSL 容器中，临时 pgvector 测试服务和随机隔离数据库，不使用容器原远程 DATABASE_URL，不修改业务部署。
- 测试环境时区统一为 `Asia/Shanghai`，避免临时库默认 UTC 与既有无时区设备心跳字段造成离线误判。
- 相邻旧用例 `test_finite_original_recognition_permit_and_untrusted_hints_have_distinct_prices[covered-detail]` 失败：其 probe 没有真实原始设备调用/策略证明，当前 HEAD 原有校验已拒绝该输入；本次不降低生产校验来适配旧 fixture。混跑还出现 pytest 失败报告进程崩溃，不把该次无最终摘要的输出作为通过证据；新增 Next 场景单独验收。
- 解释器异常排除：最终 Next 证据来自专属测试容器和私有 `PYTHONPYCACHEPREFIX`，使用 `--assert=plain --tb=line` 保留业务断言；没有修改业务依赖或重启既有容器。所有本任务测试容器已清理，agent2 原任务未改动。
- 提交前同步：2026-10-14 fetch 后快进到 `ef66f7d2`，远程增量为 LLM 上限参数修复；合并后 Gateway 参数、Next 证明及 Trace 定向回归 32 passed。

## 交付边界

- 代码开发和上述定向验收完成，不等同于全仓测试全部通过；旧 owner fixture 失败单独保留，未计入通过数。
- 原 Provider 坏回执未留存，无法证明当时具体触发原因；本次完成既有合法格式兼容、错误包装拒绝及不含原文的安全诊断。
- 用户已授权提交、推送与所需客户端打包，打包记录随后补充；agent2 由用户手动发布，本任务不部署服务端。需要更新后端 API、Runner API/Worker、网页前端，以及包含共享 Host 修复的新 Runtime 客户端；BOSS 插件无本次源码变更。

## 发布交接（2026-10-14）

- 修复源码：`495566943b923cc3e5176d11b50657773f0cfd6b`，已推送 `origin/master`。
- agent2 需要发布：更新 Python 后端、Runner API/Worker 和构建网页前端；仅更新客户端不能修复云端评分归属与恢复校验。此次没有新增数据库迁移。
- 用户在服务器手动执行既有命令：`cd /var/www/agent2 && ./deploy/agent2_update.sh`。该脚本 fetch 后 `git reset --hard origin/master`，部署执行时最新 master（并非固定到 49556694），构建 Web，并同时重建主服务与 Runner overlay；执行前确认服务器没有需要保留的未提交改动，按现有发布流程选择维护窗口、核对在途任务。助手未执行该命令。
- 新客户端用于本次 Provider 回执兼容与诊断；原 BOSS 0.3.5 插件不需要重新开发或替换。r10 组合包与旧 r9 分开保留，通过源码提交和 SHA256 区分，未执行真实用户安装/配对/招呼。
- 安装方式：手动执行 r10 安装器，沿用原用户和安装目录；现有 NSIS 同版本也执行旧版本卸载及新文件安装，保留应用数据，并经过 Runtime drain/延期安装保护。版本号相同不能用来判断是否已安装修复，应核对 r10 包与哈希；当前未启用自动更新。

### r10 打包事实

- 输入源码：`495566943b923cc3e5176d11b50657773f0cfd6b`；UI 0.0.6 / Host 0.2.18 / Core 0.2.17 / BOSS 0.3.5 / Node 22.23.3（Windows x64）。
- 安装器：`AID-Work-Runtime-0.0.6-win-x64-acceptance-unsigned.exe`，126399391 字节，SHA256 `1828ab4e8f5ef2d0d5e78ef343aaa31aad6ebcdf61e5dd8dea344ae2b6fafb9c`；Windows 签名状态 `NotSigned`，属于试用验收包。
- Host tgz SHA256：`4b965115e95b75cc0092d22c99ea0bb82f9a8cdbb3f6798e08917c2553788fab`。标准 `verify-runtime-package.mjs` 通过（3692 个资源文件）；安装器和 manifest 输出到独立 r10 目录，未覆盖 r9 ZIP。
- 组合包：`clients/release/AID-Work-Runtime-UI-BOSS-20261014-trial-r10.zip`，131167515 字节、20 文件，SHA256 `9cf5d9bdbe16e3170a1f925cede87d9a73a1b33993249eaf59c0cf93703bcfb0`。包含新安装器、原签名 BOSS 插件、客户/管理员说明及本次独立验证报告，旧 r9 ZIP 保留。
- 独立验包：从 NSIS 本体只读解出的 3770 个文件与冻结的 `win-unpacked` 路径、大小、SHA256 全部匹配；实际包内 Core 解析行为及源码哈希匹配，BOSS 签名和 3606 个载荷文件匹配现有信任根；实际打包 EXE 隔离 APPDATA 启动/握手/退出通过，Host 锁释放。未真实安装、配对或部署，不把历史 r9 验收记录作为 r10 新结果。
- 最终 ZIP 独立复核通过：CRC、20 个成员的清单路径/大小/哈希及 `SHA256SUMS` 全部匹配，3 份独立报告原样纳入；交付审查无阻断问题。临时解包目录清理被自动审批策略拒绝（未返回具体原因），保留在忽略目录，没有绕过限制。
