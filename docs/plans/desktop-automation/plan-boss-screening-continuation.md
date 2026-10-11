# BOSS 简历数量选择与连续筛选

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 续筛契约、CLI 翻页与排除 | ✅ 完成（2026-10-11） | 独立新工具，最多30屏，无进展停止，CLI定向101项通过 |
| Phase 2 | 云端可信引用与重复派发防护 | ✅ 完成（2026-10-11） | 可信历史、设备固定、同事务去重，恢复复用 |
| Phase 3 | 能力及 schema 驱动的编排，移除场景提示词 | ✅ 完成（2026-10-11） | Python72、CLI105通过；独立补验及3项P2复核通过 |
| 验证 | 自测、独立测试与CodeReview | ✅ 完成（2026-10-11） | 后端110、CLI定向101、core33通过；独立PG14、CR通过 |
| 真机验收 | Windows真实推荐页滚动与鼠标 | ✅ 完成（2026-10-11） | 隔离调度→真实Runtime/CLI，3+3+3无重复/失败；offset0→737→2211，37次完整画布 |

## 关联遗留项（2026-10-11 自已注销计划转入）

以下三项随 plan-boss-cli-vip-filter-perf.md、plan-boss-detail-greet.md 两份过程计划注销（功能均已交付）转入本计划，随 Windows 真机验收一并核验或加固：

1. VIP 账号筛选面板滚动验证：非 VIP 账号已真机验证（严格 clip 边界判定 + 最多 3 次滚动）；VIP 账号弹窗滚动适配待有 VIP 账号时验证。
2. open-detail 姓名校验竞态：canvas 先现、头部姓名晚一拍，6 次中 3 次误报 WRONG_PAGE，应改为轮询重试。
3. goto URL 直跳留 SPA 半水化态：点击失灵，同 URL 重新 Page.navigate 可治愈，待加固。

## 用户行为

用户需求和上下文决定职位、要求及总数量，工具默认值不代替业务目标。3是MCP单批容量，不是任务总量。agent通过工具schema发现页面职位切换、完整筛选替换、首批读取、连续排除读取等能力并组合调用；不在运行时提示词枚举自然语言场景或固定调用流程。缺项由通用澄清原则处理。

成功入库数为目标，失败如实报告；列表无进展/搜索上限明确停止。续筛排除已处理者（包含失败尝试），不能反复识别收费。新一轮与明确要求重评不同，不自动扩大打招呼授权。

## 业务契约v1与兼容

当前BOSS主控为本批唯一写入者。新增`boss_resume_next`独立工具名，防止旧Provider静默忽略排除参数后重复读取。Runner/Device/Host管理wire、binding、许可与跨调用资源占用均不改。

- 模型入参：`continue_from`（上一批invocation UUID，必填）、`limit`（1～3，默认3）。
- 云端从原invocation读取累计排除；校验tenant/user/session/device、工具名、成功终态。派发参数在原domain phase固化，恢复复用，不新增数据库表。
- Provider入参：`exclude_names`（1～999项，累计到1000停止）、`expected_job_name`（必填）、`limit`。MCP最多3；CLI最多10。
- CLI：`resume-next --exclude-names 甲,乙 --job 职位 --limit 3`。推荐页URL/职位检查后跳过排除者，当前屏处理完再滚列表，最多30屏；不确定是否到底时明确搜索边界，不宣称读完全部。
- batch新增输出`processed_names/stop_reason/job_name`；云端保留`continuation_id/stop_reason`，包括失败及空批结果。原batch参数兼容，旧客户机明确拒绝新续筛工具，不降级；缺少新停止原因或职位元数据的旧批次结果不可直接续筛。
- 按姓名排除是现有DOM身份的保守限制，同名候选人可能被跳过，不承诺稳定候选人ID。匿名失败行只在当前屏去重。
- 不在本批修复无关详情页Runtime注册与评分覆盖差异。

## 验证

连续批次不重复、重叠卡片/失败者排除、滚动加载、搜索上限、空批、取消/关闭失败停止；跨租户/用户/会话/设备引用拒绝，旧Provider拒绝，恢复不重复识别/扣费。CLI/MCP/schema/云端注册一致性及原batch回归；高风险独立测试与CR。

## 实施补充

- 父invocation在创建下一批的同一事务中 FOR UPDATE；已经存在后续批次时拒绝过期引用，保留 Runner 自有 dedupe。明确读前失败的code白名单、effect=none且无读取数据（仅run_id允许）才允许原引用再用；未知结果必须阻断。
- 共用 ProviderManager 仅对新续筛工具查询并缓存 listTools 的必需参数能力；不支持则返回 TOOL_NOT_ALLOWED/effect=none，不实际调用，重启清缓存。主控独占该文件及门禁测试，未修改管理接口。
- 招聘智能体 max_iterations=100；50份至少17批需留准备轮数。实际单轮容量受工具调用预算约束，不通过场景提示词规定200份硬目标；累计姓名达到1000即停止续筛。
- 页面职位变化有硬校验；实际筛选档位目前不做DOM指纹绑定。连续筛选依赖用户未在执行间手动更改页面条件，其他操作/人工改条件后需明确新一轮。不同条件或职位的新轮次可能与历史名单重叠，未实现跨轮稳定候选人身份去重。

## 20份示例与命令对应

用户选择职位2、20份 → 读取云端职位要求 → 推荐页/页面精确职位切换 → 校准筛选档位并设置。模型首次调用 `boss_resume_batch(limit=3)`；之后每次用上一批 `data.continuation_id` 调用 `boss_resume_next`。全成功时共7批：3+3+3+3+3+3+2；不是反复调7次batch。失败者排除，按实际成功数补目标，停止原因优先于凑数。

模型通过本机Runtime调用MCP工具（常驻 `boss-cli mcp --stdio`），不需要每批另启动shell CLI。人工CLI等价入口为 `boss-cli resume-batch --limit 3`，续筛为 `boss-cli resume-next --exclude-names "甲先生,乙先生,丙先生" --job "PHP开发" --limit 3`；人工调用需自行维护累计排除名单，云端调用由可信历史生成。

任务数量、条件调整及是否新建一轮由上下文决定，不以用户措辞匹配硬编码分支。`boss_filter`实际为完整替换：先清空再设置，省略维度也被清空；需要保留其他要求时，agent组合完整参数。schema已同步这一事实，避免误把它当局部更新。当前不同轮次不做全局身份去重。

## 最终验证记录（2026-10-11）

- 主控：BOSS CLI和共用Runtime的typecheck/build通过；CLI定向101项通过；后端相关110项通过；共用core最终33项通过。日志分别在 `/private/tmp/boss-screening-cli-targeted.log`、`boss-screening-python-tests-final.log`、`boss-screening-core-tests-final.log`。
- CLI全量实际429项：422通过、7失败。失败均由未修改的 `WinMouseClicker.defaultScriptPath` 使用Windows反斜杠在macOS无法找到ps1造成（包括MCP派生失败），未将全量声称为全绿。日志 `/private/tmp/boss-screening-cli-tests-final.log`。未改无关跨平台逻辑。
- 独立测试：host Python64/64、CLI批次/operation/manifest60/60、共用core33/33。真实临时PostgreSQL验证14/14，覆盖并发仅插一条、Runner dedupe重绑、明确失败恢复、未知失败阻断及null/标量数据；探测 `/private/tmp/boss-independent-sql-probe.py`，日志 `boss-independent-sql-probe.log`、`boss-independent-unit-final-test.log`、`boss-independent-cli-final-test.log`、`boss-independent-core-final-test.log`。
- Runner完整集成未执行完成：本机隔离PG缺pgvector扩展，初始化被阻断。独立测试初次自动入口误选Docker，不计验证成绩；精确中断该测试，脚本finally清理随机库路径执行，父/子测试进程均已退出。后续验证使用明确host解释器和任务PG，本机随机数据库0，任务PG已停止且目录删除。
- 独立CR及增量复核通过。已修复失败cursor占用、SQL NULL误放行、JSON null标量删除、旧CLI未知工具、50份循环上限等问题。遗留实际档位fingerprint及跨轮身份去重边界见实施补充；Windows真实DOM重排/列表滚动待验收。上述开发验证阶段未执行Git提交或部署；后续提交按用户单独授权执行。

## Schema 驱动的能力契约（2026-10-11 增量）

- agent接收的是云端Python工具的description/input_schema，本机MCP另有独立schema；两层均说明limit的单批含义、首批没有历史排除、续批累计排除、页面状态操作及能力边界。
- CLI结果 `data.continuation={available,reason,tool,arguments,processed_count,deduplication,filters_verified}`。可续筛时arguments携带累计exclude_names及expected_job_name；调用者按业务剩余量选择limit。云端结果保留同一能力结构，arguments替换为可信continue_from引用，排除名单不由模型控制。
- `available`仅表示允许继续查找，不保证存在更多候选人。无进展、扫描上限、详情未关闭或姓名累计到1000均不能直接续筛；云端对已停止引用在派发前拒绝。
- 删除新增的数量/继续/改条件/换职位场景分支，并删除旧的一键筛选固定流程；保留通用目标澄清、实际结果汇报和外部写动作授权。没有把这些自然语言分支搬进工具description。

- 增量CR修复：学历为多选；能力标志与派发门禁使用同一判定，旧结果缺上下文不宣告可续筛；CLI/MCP接受1～999项排除，reader累计1000停止，云端同边界。

### 增量最终验证

- 主控最终：Python相关72/72，CLI typecheck/build通过，reader/operation/manifest/CLI参数105/105。日志 `/private/tmp/boss-schema-python-final.log`、`/private/tmp/boss-schema-cli-final.log`。
- 独立初轮：Python65/65、CLI82/82；最终修复补验Python36/36、CLI3/3，跨层5/5，实际MCP zod数量及排除名单边界通过。日志 `/private/tmp/boss-schema-independent-final-python.log`、`boss-schema-independent-final-cli.log`、`boss-schema-independent-final-cross-layer.log`、`boss-schema-independent-final-schema.log`。
- 独立CR发现的学历语义、能力/门禁判定、1000人边界三项P2均修复并复核关闭，无新增P0/P1。复用原SQL/runtime验证，不重复未变化模块；没有调用真实模型或Windows页面，该验证记录形成时未提交、未部署。

## Windows 本机真机验证（2026-10-11）

- 代码快进至 `bc30deda`，使用仓库中的真实 BOSS CLI 0.3.0 编译产物；`goto recommend` 经 CLI 自带启动逻辑打开既有 `C:\chrome-debug` 持久目录，用户确认已登录。未修改或替换原 Runtime 配对信息。
- 用户选择隔离本机调度端模拟 Agent：Device API 使用现有 `FakeCloud` fixture；ApiClient、PollLoop、ProviderSet/MCP、Windows桌面检测和机器作用域桌面锁均为真实实现，调用固定本机 CLI 入口。不是实际云端 Runner、模型推理、可信续筛引用、识别入库或计费验收。
- BOSS CLI与Runtime/core构建通过；BOSS reader/operation/manifest/CLI校验105/105通过；core续筛能力门禁3/3通过。
- 真机通过：`boss_goto`、`boss_list_jobs`（2个已开放职位）、`boss_filter_options`；started/progress/result回传及心跳正常。续筛传入不符职位时返回 `JOB_CHANGED/effect=none`，未在受信manifest中的 `boss_close_detail` 返回 `TOOL_NOT_ALLOWED/effect=none`，不扩大原Host工具子集。
- **简历批量真机失败**：2504×1474视口有4张卡片，`boss_resume_batch(limit=3)`返回0份、4个匿名失败、attempted=0，continuation不可用；没有点击匿名卡片。工具success=true只是批次完成，不能据此认定读取验收通过；跨批去重、列表滚动和截图拼接未验收。
- 根因证据：同页 `doctor` 配对4/4。用本包已有只读 `scripts/locate-text.mjs`核对首卡几何，姓名中心Y=276，按钮原始Y=291.5；`ResumeBatchReader.locateCards`先取整按钮Y=292，`pairCardNameWithPoint`严格要求差值小于16，导致16被拒；doctor保留按钮小数坐标，差值15.5可通过。当轮未修改产品源码或放宽姓名校验。
- 旁路观察：`boss_filter_options`的薪资行结果混入到岗状态文案，尚未验证筛选设置正确性；该现象与本次续筛新增代码无直接修改关系，保留为后续核对项。
- 脱敏汇总与可复跑探查位于本机 `.tmp/boss-runtime-live.mjs`、`.tmp/boss-live-20261011/{smoke,batch,guard}/report.json`（git忽略，不随提交交付）。探查未保存fixture的device/claim凭据；各轮Provider已正常退出，隔离HTTP监听已关闭，用户Chrome保持打开。未提交、未推送、未执行服务器部署。

### 坐标修复与再验收（同日）

用户明确创建修复会话并授权两会话双向沟通；修复代码、独立验证及修复索引由该会话负责，见[坐标精度修复计划](plan-boss-card-coordinate-precision.md)。来源会话继续独占真实桌面及本计划验收记录；未扩大 Runtime 工具权限、修改配对或部署服务器。

- 原始小数精度修复后运行 `node .tmp/boss-runtime-live.mjs batch batch-fixed`：首屏4/4姓名配对恢复，attempted=4；但既有Win32点击未打开详情，0份成功，4个详情超时。代码修复未改变这些本来就是整数的姓名点击点。
- 原生仅移动诊断测得Windows 11 Insider Preview build26220、窗口DPI192；默认上下文虚拟矩形(25,284)–(1277,1021)将姓名映射到(367,422)，实际物理光标也落到该点；PMv2实测物理矩形(49,568)–(2553,2042)，正确点应为(733,844)。原守卫误判通过；只存在一个可见匹配BOSS窗口。不是仅按2倍比例猜测错位。
- 修复会话统一点击/滚轮的PMv2物理几何、精确HWND归属、移动回读及finally恢复，并处理输入后不确定错误的优先级。独立typecheck通过，全量37文件439项通过；最后局部分类修复另有build、operation38项及12组冲突/伪标记通过，独立CR两项P1闭环。辅助逻辑原生仅移动确认请求与回读均为(733,844)，守卫通过。
- 最终版运行 `node .tmp/boss-runtime-live.mjs batch batch-bridge-fixed`：首个点击前严格守卫返回 `UI_CHANGED/effect=none`，未发出点击，停止探查并退出Provider/隔离监听；报告保存在对应目录。
- 进一步完整前台化/10步移动/500ms悬停的无点击诊断确认：窗口矩形、目标HWND和期望点始终不变；即时光标回读正确，但部分25ms延迟后回到(3711,1494)的另一RenderWidget，500ms后仍在原点。独立单步记录第6/7/10步复现，该现象不依赖窗口刷新或完整脚本的变量流程。具体输入来源尚未确定，不能声称用户正在操作或某个软件确定导致；已向用户询问鼠标/触控板与远程同步情况，等待排除干扰。
- 当前保留守卫，不反复抢回光标、不重放业务点击。**实际连续读取、累计去重、列表滚动及简历截图仍未通过验收**；输入环境稳定后继续同一Runtime→真实Boss CLI链路。临时原生诊断及初轮脚本冻结副本均在`.tmp/`，光标/线程上下文均在finally恢复。没有提交、推送或部署。
- 用户补充当前通过RDP连接；`query session`确认执行环境为ethyn的Active `rdp-tcp#0`会话（ID 2）。微软RDP协议规定客户端鼠标移动事件按所带位置更新服务器指针，因此RDP输入覆盖是合理候选原因，但当前证据不能认定客户端持续发包或RDP连接本身必然导致失败。已请求保持连接、桌面可见并静止输入，以先做不点击的对照探查，再恢复Runtime→Boss CLI业务验收；不自行断开、切换或修改RDP会话配置。参考：[RDP Mouse Event](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/2c1ced34-340a-46cd-be6e-fc8cab7c3b17)。
- 用户确认保持鼠标静止后再次仅移动HoverProbe：窗口矩形仍不变，10步即时物理回读均正确，第2/6/9/10步25ms后回到本轮原点(3837,2425)，500ms后仍回原点，归属守卫拒绝exit1；finally恢复，没有业务点击。因此不能仅靠静止输入消除当前干扰；已请求RDP窗口模式且客户端鼠标移出远程画面后做进一步对照，仍不据此认定RDP根因。
- 用户确认RDP窗口模式且客户端鼠标移出远程画面后，同一HoverProbe exit0：新窗口矩形(34,286)–(2538,1756)，10步即时/25ms回读均正确，500ms后仍目标点(718,561)，归属守卫通过；证据保存在`batch-rdp-windowed/rdp-windowed-probe.json`。该对照支持RDP输入干扰判断，窗口模式同时改变位置/几何，不能据此确定具体协议事件来源。
- 运行`node .tmp/boss-runtime-live.mjs batch batch-rdp-windowed`，真实CLI内部首批attempted=3、成功3、失败0；拼接图分别1468×7025（7段）、1468×6633（7段）、1468×14796（15段），均到底；win32:click=3、cdp:screenshot=35、body约159s。随后MCP连接关闭，Runtime收到`EXECUTION_UNKNOWN/effect=unknown/data_bytes=0`；harness exit1，Provider/隔离监听已退出。**这是CLI内部读取成功，端到端回传仍未通过；续筛、去重、推荐列表滚动未执行**，未自动重放该unknown批次。
- 已反馈修复会话定位结果传输：本机MCP SDK ReadBuffer默认10MiB，超限时会关闭transport；Boss server把完整含图片结果同时写入JSON text和structuredContent，core未显式配置接收上限/错误记录。目前尚缺本次原始消息大小与明确溢出报错，不能将候选机制当确证；修复会话负责合成无真实数据stub复现与必要修复的独立验证，主会话继续独占后续真机验收。
- 传输修复独立core33项、Runtime/Host79项及CR通过后启动新的只读验收轮`batch-runtime-large-fixed`；首批3份+续读1份全部Runtime回传，4不同候选、0失败，累计processed_count3→4，PNG签名/声明尺寸校验通过、各图到底且suspect_seams为空。首批data JSON9,522,163 bytes；按完整text/structured双份图片base64计算仅图片下界19,041,280 bytes已超过旧10MiB，修复在本次真实路径有效。原失败批次的精确wire字节仍未测。wrong-job `JOB_CHANGED/none`与非受信工具`TOOL_NOT_ALLOWED/none`再次通过，199次心跳，Provider/隔离监听退出。filters_verified=false，不宣称筛选一致性、云端模型识别/入库/真实服务验收。
- 该轮续读尝试两次CDP wheel却返回`no_progress`，continuation不可用。初版临时harness只检查wheel计数误把滚动标通过，已明确纠正为必须累计处理数超过首屏可见卡片数并有scroll调用；旧报告保留`report-before-evidence-correction.json`，当前report.completed=false/listScrollVerified=false。本轮已处理4与首屏4相等，不满足实际推进验收；不能把no_progress说成全列表读完。
- 同编译CLI Gateway只读DOMSnapshot（既有白名单method，includeDOMRects=true、overflow-y/display）与截图：列表滚动条在顶部；推荐BODY overflow=scroll，contentHeight5676、clientRect高697、scrollRect高2838、offset0，明确仍可滚。root HTML布局bounds2504×1474但clientRect1252×737，比例2为实测几何，不根据Windows DPI硬猜；原中心(1252,737)在CSS右下边界。单变量仅wheel探查将设备中心及步长按实测比值映射为CSS(626,368.5)、deltaY368.5，推荐document.scrollOffsetY从0→737，其他document仍0，证明局部坐标单位错位；证据`css-wheel-probe.json`，无点击/键盘，不扩白名单或脚本注入。
- 已交修复会话实施续筛局部fresh DOMRects换算，保留CDP通道、无进展保护及历史/取消边界，独立验证后继续原Runtime→真实Boss CLI验收；当前诊断将列表滚至737，后续新验收轮以实际起始视口统计。未提交、推送、部署。
- CSS修复独立445个唯一测试证据（444全量+新增结构项）、最终受影响74项及额外几何边界/CR通过后执行`batch-css-fixed`。在诊断留下的列表737起点，首批返回3份图，续读返回1份图+18定位失败（attempted19），累计已尝试22；无重复，实际列表offset737→7967、12次CDP wheel，滚动本身确已推进，但两次续读未完成。PNG有效、bottomReached、seam标记不足以证明完整读取：与同人顶部起读相比图被明显裁短（14822→7890、4838→2279），本轮不认可完整性。
- 仅只读活动DOM与截图确证共享owner口径缺陷：推荐doc2.scrollY2948，简历doc3.scrollY0、canvas bounds(0,0,1468,1394)，当前accumulateOwnerOffset返回(336,3028)、locateResumeCanvas=null；实际截图详情自顶栏下开始完整可见。每层owner的bounds是该父文档绝对坐标，当前helper只累加bounds、未扣各父文档scrollOffset；737时错误裁掉上部，2948时整体出视口，解释假“未打开”。证据`active-detail-geometry.json`/`active-detail.png`；截图不公开展示候选人数据。
- 本轮自然结束exit1、Provider/隔离监听退出。尝试安全停止的检查时进程已不存在（ProviderStopped=false、无children），未实际终止任何进程，结果并非unknown；修复会话已收到更正与可构建通知。报告保留`report-before-integrity-correction.json`，当前imagesIntegrityAccepted=false/completed=false，明确滚动已推进但连续读图不通过。
- 已交修复会话处理共享Boss owner helper：每层扣owner所在父文档自身偏移、leaf仍调用方扣一次，需覆盖所有复用解析的独立回归与CR。临时harness新增fixture取消哨兵（检测读取失败或cancel-request文件），避免已定位失败继续耗尽扫描；最终新轮拟经Runtime boss_goto chat→recommend重入并核对起始偏移为0，再作至少两次续读。所有业务输入继续Runtime/MCP/真实CLI；无新wire/权限、无提交/推送/部署。
- owner修复已由修复会话独立验证：38文件452项全量通过、typecheck/build通过，额外25项几何/管线边界及独立CR通过；源码、脚本与dist保持稳定。真实起点准备轮`batch-owner-fixed`经Runtime菜单chat→recommend仍保留推荐iframe滚动offset8814，harness在任何简历读取前安全退出；不把goto的缓存/幂等行为当新缺陷。随后仅使用现有编译CLI Gateway `Page.navigate`重新加载原推荐URL，fresh DOM确认各可见文档offset0；这是验收准备，不是新增产品重置能力。
- 新轮`batch-owner-fixed-final`所有业务调用经真实Runtime→MCP→Boss CLI，fresh起始offset0、首屏4卡均通过。首批8次只读详情采样rect均为(336,80,1468或1460,1394)，与raw canvas完整尺寸一致；CLI内部首份7段到底、1468×6873成功。第2份原生滚轮`RequireCursorOnWidget`拒绝光标/RenderWidget归属变化，读取管线Escape关闭成功；fixture首次读取失败取消已生效，Runtime返回`CANCELLED/effect=none/data_bytes=0`，harness exit1、Provider与隔离监听退出。之后CLI下一点击也守卫拒绝；不将内部首份、几何采样或协作取消当完整批次成功，尚未续读或验收滚动后完整图片。
- 本轮前、后各10步/25ms与500ms仅移动探查均通过，但原始物理光标位置从约(1539,11)变为(3862,2200)；只能确认期间发生位置变化，不能据此认定用户操作或RDP具体事件来源。已请求确认RDP窗口外鼠标与远程输入情况，再决定后续业务轮；不放宽守卫、不自动重放unknown。新增本轮报告保存在`.tmp/boss-live-20261011/batch-owner-fixed-final/report.json`，未保存fixture凭据，没有提交、推送或部署。
- 用户随后确认上一轮期间操作过远程桌面，当前已移出鼠标且停止操作。新轮`batch-owner-stable-input`原生10步/25ms/500ms悬停通过、fresh推荐起始offset0。首卡3次姓名点击均未触发原生守卫错误，但页面无CANVAS；fixture立即取消、Runtime `CANCELLED/none/data_bytes=0`、Provider/隔离监听退出。使用现有编译CLI Gateway只读截图确认：BOSS提示当前职位「今日查看权益已用完」，显示付费升级弹层；不是新坐标失败证据，没有点击付费或绕过平台限制。已询问用户换另一已有职位只读验收或等额度恢复；跨批连续读取仍待验收。证据`.tmp/boss-live-20261011/batch-owner-stable-input/report.json`与`current.png`（截图不公开展示）。

### 最终真机验收通过（同日）

- 用户确认已自行充值后，继续当前职位、新fixture `batch-owner-quota-restored`，不复用旧continuation；原生悬停探查通过，现有编译CLI Gateway起点准备后fresh推荐offset0。全部业务调用继续真实Runtime→MCP→本机Boss CLI，无新增工具/权限或替代输入通道。
- 首批3份、连续两次next各3份完整回传，共9个不同候选人；每批attempted3、失败0，processed_count3→6→9，返回累计历史与排除名单检查通过。首屏4卡，实际读取9>4；两次next各有实际列表推进，doc2.scrollOffsetY从0→737→2211，共3次CDP列表wheel，不能仅据wheel计数判定成功的问题已消除。
- 37次fresh只读详情画布采样全部完整，rect为(336,80,1468或1460,1394)，与raw canvas尺寸一致；覆盖父文档滚动0/737/1474/2211。9份PNG签名及IHDR尺寸与声明一致，全部bottomReached=true、suspect_seams为空，长图高5039～10763。每批抽查一张原图的开头及末端内容：`3-resume-1.png`、`4-resume-2.png`、`5-resume-3.png`，均可见候选头部和末端内容，未出现此前祖先偏移导致的上部裁失。抽查与几何/拼接元信息不是逐像素完整性或OCR准确率保证。
- 三批耗时约133/138/148秒，脱敏报告中data JSON分别6,181,113/6,464,529/7,390,870 bytes，均完整到达fixture；started/progress/result及418次心跳正常。wrong-job `JOB_CHANGED/effect=none`与未受信工具`TOOL_NOT_ALLOWED/effect=none`拒绝通过。Provider和隔离HTTP监听已退出，用户Chrome及登录态保持，已通知用户可恢复RDP操作。
- 最终报告`.tmp/boss-live-20261011/batch-owner-quota-restored/report.json`的completed/providerExited/noDuplicateCandidates/cumulativeHistoryVerified/listScrollVerified均true，continuationBatchesVerified=2；各PNG及QA预览仅保留本机git忽略目录，不公开候选数据。修复会话据此更新修复计划Phase3及索引；本计划Windows验收完成。
- 验收范围仍是用户选择的隔离本机Agent调度模拟与真实桌面/CLI/Runtime。filters_verified=false，未验证实际筛选fingerprint、真实云端Runner、模型识别、可信续筛引用入库或计费；这些既有边界未因本轮成功改变。没有提交、推送或服务器部署。
- 提交前同步远程文档清理转入的「关联遗留项」：VIP筛选面板滚动、open-detail姓名竞态和goto半水化态仍单独待核验/加固；本轮续筛验收完成不代表这三项已完成，不在本次提交中扩展开发。
