# 开发计划：企业微信会话每日总结工作台与专属定时配置

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| D0 | 逐项确认设计、更新原设计和建立本计划 | ✅ 完成（2026-11-04） | 独立评审问题已修，最终只读复核无阻断；仅文档 |
| D1 | 本地会话目录与独立添加、Agent 自动纳入 | ✅ 完成（2026-11-04） | 主控评审放行；独测与 CR 无阻断；真实 PG/真机及加密整链缺口留 D5 |
| D2 | 单一专属计划、通用任务排除与配额 | ✅ 完成（2026-11-04） | 主控评审放行；2.14 契约、独测 225＋22＋5 项通过、11 项跳过；真实 PG/真机留 D5 |
| D3 | 逐会话执行记录、API 与报告查询 | ✅ 完成（2026-11-04） | 主控评审放行；2.15 契约、旧工具清单断言已修、独测/CR 通过；真实 PG/真机留 D5 |
| D4 | 每日总结页面及配置抽屉 | ✅ 完成（2026-11-05） | 主控复评放行；独测前端 34 项、目录 API 5 项通过，CR 无阻断；真实链路留 D5 |
| D5 | 独立测试、CodeReview 与本地联调 | 🔧 进行中 | 本机回归、只读目录及独立 CR 无阻断；真实 PG、配套 Runtime 和旧客户机组合待验收 |

设计依据：[企业微信会话旁路日志、查询与按日日报设计](../design/wecom/wecom-conversation-operations-design.md)。原旁路日志、日期读取及初版页面的实施记录仍见[原计划](plan-wecom-conversation-operations.md)；本计划只追踪 2026-11-04 逐项确认的新开发内容。开发时先核对[Runner / Desktop / Runtime 共同契约](../system/runner-desktop-runtime-integration-contract.md)与仓库相关后端、前端、测试、数据库规则。文档完成不代表功能已实现、提交、部署或真机验收。

## 1. 成功标准与范围

1. 用户不设置计划也能从今日页选择已登记会话，点击“更新对话总结”；每个会话独立生成并即时显示一份当日报告，失败的会话保留旧报告并给出原因。昨日和自选历史日期只读。
2. 智能体成功定位监控目标后，空会话也自动进入每日总结；用户可通过添加弹窗独立选择当前企微账号的单聊/群聊。登记不触发自动补录或总结，停止监控不自动移除。
3. 每用户仅一个企微日报计划，只在每日总结页配置、暂停/恢复。默认总结昨天、北京时间每天 02:00；总结今天可设 1–24 小时整数间隔。手动范围与定时范围独立，定时“全部”在每次到期时动态展开。
4. 专属标记 `[wecom-conversation:daily]` 位于后端通用排除列表，通用任务列表/统计/激活数/编辑入口统一排除；不影响 Scheduler 派发、Runner 计费与已交付 BOSS 能力。
5. 手动和定时每次触发均有执行 ID、目标范围快照和逐会话开始/完成/失败/未执行事实；可查看时间、阶段、原因、错误码并复制一行最小诊断信息。运行互不打断；较旧快照不能覆盖较新成功报告。

不做跨会话合并报告、Agent 对话内计划管理、首次纳入自动补录、多个企微计划、设备选择器、周/月频率、模型/提示词配置、新采集守护进程、新失败业务表或附件原件能力。

## 2. 开发前共同检查点

- 代码事实基线：现有 `wecom_business_flow.py` 逐会话处理、`bs_wecom_daily_summaries` 按日期/会话 UPSERT；现有结果 checkpoint 只在整轮尾部记录、手动 API 限 50 ID、报告日期查询限 500 行、Agent Skill 可建多个计划且首录会建补录任务。实施以当前代码为准，旧文档完成记录不等于本轮完成。
- 会话目录能力涉及 CLI、Runtime 工具注册和云端消费。实施前在共同契约登记请求/结果 schema、错误码、版本/能力识别、唯一写入者、调用身份、设备绑定、超时/取消与生产消费样例；确认不修改 Device wire 或已交付客户端基础语义。缺目录命令只使添加弹窗提示安装最新版 Runtime 和 CLI。
- 明确用户选定设备与企微账号绑定：页面不提供设备切换，任务受理时固化来源；无有效选定设备、目录账号改变、混入其他账号会话时明确报错，账号在执行中改变则停止整次执行。不得按名称重绑或借另一个设备继续。
- 开发前检查工作区与原计划状态，保留本任务外改动。非平凡代码开发按项目开发流程进行独立测试和 CodeReview；阶段完成后先更新本计划进度，再同步 `docs/ideas.md`。

## 3. D1：会话目录、登记与 Agent 纳入

1. 在 CLI 本地会话库查询中增加页面专用只读目录：当前账号、关键词、分页游标、有界页大小；返回稳定会话标识、名称、类型、可确认的平台及同名标记，覆盖有消息和无消息的单聊/群聊。真实头像 URL 可得才带；同名且无头像时按稳定 ID 按需取最近 3 条有界消息，空会话回空预览。目录/预览不能推进监控 cursor。
2. 沿原 Runner 本地调用、Runtime 工具通路与现有设备授权暴露页面专用结果，不放入 Agent 模型可见工具集合、不新增 Device API 或本地服务。后端提供目录/预览及批量添加接口；添加时校验租户/用户、来源账号、选定设备与稳定 locator，幂等写 `bs_wecom_conversations`，已加入项标记但不重复创建。
3. 在 Agent 原读取成功且目标稳定定位的受理接点自动登记范围，覆盖无消息、未回复的会话。单读沿现有稳定 session；显式多目标 `wecom_read_sessions` 需加性返回已成功定位目标的有界稳定 locator 元数据（含空会话），服务器不能只从有消息的 `sessions`、名称或 cursor 猜范围。未显式指定目标的全账号批读扫描不自动纳入所有会话。区分 Agent 监控读取与页面目录/预览、日报读取，避免浏览候选时误登记。保留成功读取/发送的旁路日志；删除 Agent Skill 的 `setup-daily-report` 引导及首次纳入自动补录提交。

**验证**：空单聊/群聊可添加；同名不会按名称合并；Agent 只定位未回复也自动出现；目录预览不误纳入；重复添加幂等；账号/设备/用户隔离；缺目录能力与本地库错误可读；原 BOSS 与既有企微读/发路径回归。

### D1 实施与验证记录（2026-11-04，主控评审放行）

- CLI 本地库新增页面专用目录、按需预览和批量稳定 ID 核验；显式批读加性返回包含空目标的 `resolved_targets`。Runtime 按实际 MCP schema 上报可选目录能力，服务端只在选定设备已上报该能力时派发。目录不进入 Agent 模型工具集，不推进监控 cursor。
- 企微专用 API 提供目录、预览及批量添加。超过 50 个目标分批核验，全部批次通过后单事务登记；同账号会话不按名称合并，来源设备冲突拒绝改绑。Agent 已接受的显式读取结果自动登记目标，未指定目标的全账号批读、日报日期读与页面目录不登记。移除 Agent Skill 建日报计划引导和首次纳入自动补录提交；保留原读发旁路日志，并按 invocation 来源设备过滤已有范围。
- 实施中独立 CR 发现并修复旧设备目录能力误派发、50 个空目标结果超限、预览正文传输未限界、大目录 SQLite 参数超限、合法 Unicode 批量核验输入超限及跨设备静默改绑/混日志。最终独立 CR 无阻断。
- 独立测试：后端目录 API、日志回调和代理工具 37 项及目录服务模拟事务回滚 1 项通过；CLI Python 目录 6 项、原批读 17 项、50 空目标结果/续页 1 项、CLI manifest 6 项、Runtime 能力/Provider 25 项通过；CLI 与 Runtime `npm run build:main` 通过。均为当前工作区代码，未提交或部署。模拟事务测试只证明回滚控制流；真实 PostgreSQL 事务/并发、配套 Runtime＋CLI 真机账号切换及已交付旧 BOSS 包的服务端单独升级组合未验。
- `business-sessions.test.js` 合成加密整链 10 项未通过：9 个原用例和 1 个新增用例都在业务查询前因本机 PyCryptodome `_cpuid_c` 原生模块初始化失败；未安装依赖，不能计为功能通过。真机未读、未发送、未修改监控。

## 4. D2：专属计划与调度

1. 定时任务后端集中定义专属标记排除列表，当前含 `[wecom-conversation:daily]`；通用 API/Agent 工具列表、统计、激活数量的创建与恢复检查共用该规则。通用创建/任务定义更新拒绝写入任何保留标记，只有企微专用受信入口可写该标记；防止普通任务伪装成配额豁免计划或进入企微执行分支。通用 API 与 `manage_scheduled_task` 按已知 task_id 取得任务后统一守卫全部查看和变更动作（含通知规则、编辑、暂停/恢复、触发、删除），防止绕过列表过滤或改掉标记；专属计划仍正常派发。
2. 企微专用配置 API 在租户/用户范围内保证至多一条计划，包括已暂停计划；配置在原 `scheduled_tasks` 行及既有会话范围上维护，不新建业务表。创建/恢复不占普通任务激活名额。删除 Agent 自动创建、配置匹配/拆多计划逻辑；用户可在企微页创建、更新、暂停/恢复。
3. 计划目标日期为昨天时仅每日固定时刻，缺省 Asia/Shanghai 02:00；目标日期为今天时仅 1–24 整数小时间隔，首次到期为保存后完整 N 小时。动态全部在 Scheduler 接受本次 Runner 前展开当时已登记同账号/设备会话，把稳定 conversation_ids 与业务日期写入受信描述；执行只消费该快照，不在排队后重新按 `daily_plan_task_id` 查询。自选范围同样在受理时冻结；空范围不得派发成功假报告。

**验证**：普通激活名额已满仍可建唯一企微计划；企微计划不使普通任务创建/恢复触顶；通用创建/定义更新注入保留前缀被拒；侧栏和通用工具看不到企微计划，按 task_id 也无法通用查看或编辑；重复创建不产生第二条；日期/频率非法组合拒绝；新登记会话只进入动态全部范围，不改变自选范围；到期与暂停/恢复正确。

### D2 实施与验证记录（2026-11-04，主控评审放行）

- 企微专用 API 仅从当前选定设备的只读目录取得账号；同租户/用户 advisory 锁事务内保存唯一计划（含 paused）、调度字段与自选成员。昨天计划默认上海 02:00，今天只接受 1–24 整数小时间隔，创建、编辑、恢复后均满 N 小时首次到期。
- 通用任务 SQL 在列表 LIMIT 和激活配额计数前排除 daily；通用创建/定义更新拒 daily 与退役 backfill 保留前缀。通用 API/Agent 工具按 task_id 拒绝专属计划，DAL 写条件或行锁保护并发边界。Scheduler 认领行锁内冻结上海业务日期、来源及会话 ID 到 pending 快照；空范围同事务暂停并写通知账本，dispatch 原样重投快照，Runner v2 不回查可变范围。跨零点行锁等待的日期读取改为锁后 `clock_timestamp()`；旧 BOSS/普通任务原通路不变。
- 开发自测：受影响综合 276 passed、23 skipped，编译检查和 `git diff --check` 通过。独立测试第一轮 225 passed、11 skipped；补测后针对通用 7 路由/Agent 5 动作零写、受信来源、更新与恢复到期、SQL 谓词等 22 passed、0 skipped；CR 修复后针对锁后时钟、快照与空范围 5 passed、0 skipped。独立 Code Review 最终无剩余 P0/P1/P2。
- 本机无隔离 PostgreSQL：SQL 方言、真实 >100 行过滤/配额、同用户并发唯一、行锁跨午夜及通知事务均仅有 mock/构造验证，留 D5 在真实 DB 验证。未做真机 Runtime＋CLI、加密整链及“只升级服务端、旧 BOSS 客户端不升级”组合验收。企微功能尚未上线，旧多计划或旧 schema 行会明确报冲突并需人工清理；D4 前旧页面表单尚不能验收新专属配置。未提交或部署。

## 5. D3：逐会话执行事实与报告 API

1. 手动 API 不再要求 plan_id，按今日勾选稳定会话 ID 提交；去掉 50 ID 固定拒绝。删除现有 `submit_manual_report` 根据首个会话的 `daily_plan_task_id` 隐式继承计划 profile/instance/requirements 的路径；无论会话是否属于计划，手动都用默认主智能体模型配置和固定模板。一次点击一个 Runner，不按 50 拆多个任务。计划到期也冻结本次日期和目标 ID 清单。来源设备/账号在受理、进入逐会话循环前及每次本地回执后核验；账号/身份不符归类为整轮致命错误，不进入单会话“记录失败后继续”的 catch，其余目标标记未执行。
2. 受理时将触发方式、业务日期及目标快照放入可信 Runner 请求事实；运行后在现有 checkpoint 中增量记录逐会话开始时间、阶段、完成时间或脱敏错误码。执行记录查询合并原受理事实、Runner 状态/错误和已保存的逐会话 checkpoint，不能只按 `wecom_daily_result` 筛选；排队期取消、业务接点前失败或首个 checkpoint 前失败仍显示执行 ID、目标范围、全局错误，所有目标为未执行。逐会话成功保存报告后、进入下一会话前持久更新执行事实；失败也记录后继续。若报告已落库而后续 checkpoint 写入失败，查询可用当前报告来源 runner_id 对账；如报告随后被另一执行的新快照覆盖而已无法证明旧执行结果，则标“结果待核对／执行事实保存失败”，不能猜为未执行或已完成。中止时区分未开始和处理中断，权威 checkpoint/取消/计费异常不得被当作普通单会话失败吞掉。服务端详细日志带 runner_id、会话 ID、阶段与错误码。
3. 保持报告表一会话一日一份及 snapshot_at 新鲜度 UPSERT。空日存“当日无消息”且不调模型；删除 `_parse_summary_json` 将任意非 JSON 原文截成 overview 后当成功的回退，检查模型完成原因与必需结构字段，截断/结构不完整记失败；已存旧成功与本次失败分别投影。报告日期查询改为稳定分页，不再先取 500 行再内存过滤；执行记录按 Runner 归属和日期分页查询，提供最小诊断行。手动进度只在页内展示，定时终态复用原通知及日期深链。

**验证**：60 个会话一次执行有一个 Runner ID、逐个保存且无 50 上限；长单会话分组只混合同会话消息；第 501 个会话能通过分页显示；首个成功即可在页面读取；逐会话失败继续、全失败如实终态；整轮身份失败停止；中断后已完成/失败/未执行清楚；并发两轮互不取消、旧快照晚完成不覆盖新报告；错误码可用执行 ID 找到后台日志，无敏感内容外泄。

### D3 实施与验证记录（2026-11-04，主控评审放行）

- 手动 API 只收会话 ID 清单和可选幂等 ID，业务日由服务器取上海今日；拒绝客户端自报计划、历史日期、账号、设备、时区、模型等字段。从已选设备目录核账号，单次数据库查询核所有会话同租户/用户/来源，60 个目标仍以 main 配置和完整受信快照提交一个 Runner。定时继续消费 D2 冻结快照。Runner 原 `input`/`accepted_at` 作为排队及业务前失败的受理事实；业务流逐会话持久保存开始、阶段、完成/失败/未覆盖等脱敏 checkpoint，尾部 `wecom_daily_result` 保持通知兼容。
- 报告日期页按会话 ID 稳定分页且在 SQL `LIMIT` 前过滤范围，执行列表按接受时间/Runner ID 分页；执行详情合并受理事实、Runner 终态、逐项 checkpoint 与当前报告 `source_runner_id`，区分旧成功、最新失败、中断、未执行及无法证明的保存结果。模型非 JSON、截断或缺必需字段不再保存假成功；空日存“当日无消息”且不调模型。UPSERT 未应用的旧快照明确记“已有更新报告，本次未覆盖”，不提示本次已保存；通知沿原定时通路说明未覆盖。
- 独立 CR 发现并修复：旧快照未写入却记完成；受理设备 A 后选定改为 B 时首次误选 B；来源失效错误被当单会话失败后继续；取消/暂停时成功空页仍可能落报告。最终独立 CR 无剩余确认 P0/P1/P2。独测第一轮 97 passed/14 skipped；旧快照修复 9 passed/0 skipped；设备、来源、取消与空日修复 13 passed/0 skipped；最后取消时序修复 9 passed/0 skipped。开发者受影响合并自测 304 passed/23 skipped，最终 CR 修复定向 57 passed；编译与 `git diff --check` 通过。上述轮次有重叠，不合并成独立用例总数。
- 扩展回归曾为 1040 passed/23 skipped/1 failed，唯一失败是 D1 新增目录工具后 `test_all_tools_local_required` 仍断言静态总数 35（实际 36）。调度会话要求在 D3 收尾修复：测试现改为核注册类唯一、proxy/manifest/catalog 名称双向一致、逐类 `LOCAL_REQUIRED` 与 category，并确认目录工具不进入 Agent 可见集；仅修改测试文件。开发自测相关 68 passed，独立 TEST 对旧失败节点及相关 WeCom 清单 11 passed/0 skipped，简短独立 CR 无新增阻断。未在测试文件修订后机械重跑整个 1040 项扩展集，不能将历史扩展结果改写成全套通过。本机无隔离 PostgreSQL，真实 501 行分页、UPSERT/并发、固定设备原子绑定和通知事务仅由模拟/构造验证；未做真机 Runtime＋CLI、真实企微账号切换或“只升级服务端、旧 BOSS 客户端不升级”组合验收，留 D5。未提交、推送、安装或部署。

## 6. D4：前端每日总结工作台

1. 复用现有后台 Base* 组件与语义 token。顶部“今日｜昨日｜选择日期”横条，默认今日；移除计划下拉、旧两个生成按钮。今日显示“定时执行配置”“更新对话总结”，历史日期只读。首次加载/切换日期按纯日期和请求代次处理，避免异步串页。
2. “本次总结的会话”标题旁放“＋ 添加会话”，默认全选、可逐项取消；全部取消或无会话时禁用更新。添加弹窗显示当前设备/账号、搜索分页、多选、已加入状态、真实头像或同名预览/类型短 ID；确认后刷新会话卡。无会话文案沿设计第 6.2 节。
3. “定时执行配置”抽屉提供启用/暂停、昨天/今天、对应时刻或 1–24 小时间隔、动态全部/自选会话和下次执行时间，不露出通用任务的额外选项。执行区按会话卡即时显示总结、空日、失败原因、旧成功来源、未执行；“查看执行记录”展示每次执行与复制诊断信息。

**D4 范围与实现记录（2026-11-05，主控评审放行）**：工作台的会话 chip、默认全选及定时自选范围从已按会话 ID keyset 完整分页的日期报告构建；不使用会因中途更新而重排的会话 OFFSET 列表。目录添加携带弹窗读取时的设备和源代次预期值，服务端在选定设备后及首批核验后拒绝来源变化；旧调用省略预期值仍走原接口。今日未终态执行可在返回今日或刷新时恢复单轮询，终态后显示最新执行；暂停计划不展示旧到期时间，暂停/恢复成功但配置重查失败时提示待核对。页面仅今日有写入口，历史日期只读。

**验证记录**：开发自测前端工作台/API/路由 34 passed、目录 API 5 passed，前端 `npm run build`（类型、依赖边界、Vite、产物）通过。独立 TEST 最终复核前端 34 passed/0 failed/0 skipped，后端目录 API 5 passed/0 skipped；独立 CR 关闭原两项 P1、四项 P2 和后续失败原因映射问题，无剩余确认 P0/P1/P2。501 项会话与报告三页可见并一次提交完整 ID，同名空会话预览与添加、日期导航、执行轮询、计划重查失败及抽屉焦点行为均有定向测试。主控用本地 Vite、系统 Chrome 和符合实际 API 形状的受控响应检查桌面、添加弹窗、配置抽屉、历史页及 375px 页面/弹层；无页面异常或根节点横向溢出，初始焦点、反向/正向 Tab、Esc 与关闭后返回配置按钮均通过。主控评审发现普通失败仅展示技术错误码，已补受控脱敏可读原因并与安全码分别显示，卡片/执行详情共用；后续 CR 修复取消误报失败、快照失稳误说来源变化及单目标 fatal 来源码漏报。最终受控浏览器截图检查了 READ_FAILED、取消中断、单目标来源失效和 SNAPSHOT_UNSTABLE 的展示。该浏览器走查使用受控数据，并非真实后端联调；本机无运行中的后端与登录快照。真实 PostgreSQL 并发、Runtime＋CLI、旧 BOSS 客户端组合及完整前后端链路留 D5。可恢复暂停/中断执行每 5 秒刷新全量报告可能增加数据库读取，留 D5 压测评估。未提交或部署。

**验证**：使用假数据和真实 API 状态检查今日/昨日/历史、无会话/无计划/空日、同名/空会话、部分失败/中断/旧成功、定时配置校验及窄屏布局；通用任务侧栏不显示企微计划。手动点击不发定时通知。

## 7. D5：独立验证与交付边界

- 按开发流程完成开发自测、独立测试与独立 CodeReview；覆盖受影响的 Python/TS 单测、数据库隔离测试、前端 build 和关键 Runner→Runtime→CLI 调用。测试必须证明为什么状态、配额与来源隔离正确，不只断言文本相等。
- 在配套企微 Runtime＋CLI 组合中核验空会话目录、账号切换、同名预览、定时到期、逐会话结果和通知；按受影响范围验证“仅升级服务端、旧客户机保留 BOSS Runtime”的原工具/恢复/上报/计费能力。若仅完成本地模拟，明确标为未真机验收。
- 阶段完成实时更新本文件顶部「开发进度」，再更新 `docs/ideas.md` 的索引状态。用户未明确要求“提交代码”不提交；未当场明确指定环境部署，不执行安装、更新脚本、重启或部署。

**本机独立测试记录（2026-11-05）**：以下命令均针对当前工作区，`根目录` 指仓库根目录；未连接共享或生产数据库。两组后端用例有重叠，不合并计数。

| 工作目录 | 命令 | 结果 |
|---|---|---|
| 根目录 | `venv\Scripts\python.exe -m pytest tests/unit/wecom_conversation tests/unit/scheduler tests/unit/api/test_scheduled_task_api.py tests/unit/local_tools/test_proxy_tool.py -p no:cacheprovider -q` | 375 passed、23 skipped、0 failed；跳过项依赖缺失的隔离 PostgreSQL |
| 根目录 | `venv\Scripts\python.exe -m pytest tests/unit/local_tools/test_wecom_business.py tests/unit/local_tools/test_wecom_proxy_tool.py tests/unit/local_tools/test_boss_tool_billing.py tests/unit/local_tools/test_write_result_billing.py tests/unit/local_tools/test_boss_common_recovery_independent.py tests/unit/local_tools/test_proxy_tool.py tests/unit/test_credit_billing.py -p no:cacheprovider -q` | 198 passed、0 skipped/failed；含旧 BOSS 工具、恢复和模拟计费 |
| 根目录 | `venv\Scripts\python.exe -m pytest clients/wecom-cli/tests/py/test_session_directory.py clients/wecom-cli/tests/py/test_day_messages.py clients/wecom-cli/tests/py/test_business_sessions.py clients/wecom-cli/tests/py/test_wecom_localdb.py clients/wecom-cli/tests/py/test_group_poll.py -p no:cacheprovider -q` | 59 passed、0 skipped/failed |
| `clients/agent-tool-runtime` | `npm run build:main`；`node --test dist/tests/capabilities.test.js dist/tests/wecomProviderCompatibility.test.js dist/tests/providers.test.js dist/tests/manifest.test.js` | 构建成功；27 passed、0 skipped/failed |
| `frontend` | `npm run build`；`npm exec vitest run web/__tests__/components/WecomConversationDaily.workbench.test.ts web/__tests__/api/wecomConversation.test.ts web/__tests__/router/routes.test.ts` | 构建成功；34 passed、0 skipped/failed |
| `clients/wecom-cli` | `npm run build:main`；`node --test dist/tests/business-sessions.test.js dist/tests/directory-resolved-targets.test.js dist/tests/manifest.test.js dist/tests/localdb-driver.test.js` | 修复后构建成功；37 passed、0 skipped/failed |
| 根目录 | `venv\Scripts\python.exe -m pytest clients/wecom-cli/tests/py/test_python_bootstrap.py -p no:cacheprovider -q` | 1 passed、0 skipped/failed |

**CLI 启动路径修复与本机只读核验**：修复前 CLI 合成加密相关三文件 17 项中 7 passed、10 failed。独立测试用同一合成库、密钥和驱动定位到 Windows 开发态 venv 的解释器被默认转成 `\\?\` 路径：普通 Python＋bootstrap 可解密并通过完整性检查，命名空间路径＋bootstrap 在 PyCryptodome 原生库查找处返回 `INTERNAL_ERROR`，生产桥再折成 `LOCALDB_UNAVAILABLE`。这不是密文算法失败或原生模块安装损坏。`clients/wecom-cli/src/platform/localdb/driver.ts` 现仅在解释器同目录存在发行标记 `asset-provenance.json` 时使用命名空间路径；开发 venv 用普通路径，打包态长路径策略保留。修复后上述 CLI 37 项通过；真实打包长路径尚未运行。先前脱敏 `wecom_probe` 因本机 `SESSIONNAME` 缺失返回 `INTERACTIVE_SESSION_REQUIRED`；确认命令与企微进程同属 Windows 会话后，经目录 operation 自身的主窗口/账号身份检查直接调用只读 `list limit=1`，修复后返回成功、1 项、有下一页。未输出账号、会话名称、locator、路径或聊天正文，也未调用预览、发送和监控写入；空会话不能从目录 list 字段判定，仍未验收。

**独立 CodeReview 结论**：最终 diff 未确认新增 P0/P1/P2。发行构建生成并核验 `asset-provenance.json`，Python bootstrap 以同一标记调整打包路径，CLI driver 的条件与其一致；普通开发 venv 和无发行标记的覆盖解释器改走普通路径。合成链修复已独测通过；真实发行长路径及无标记的超长覆盖解释器均未实测，不列为已通过。D1–D4 的核心契约与前阶段 CR 闭环一致。

**尚未验收**：本机无任务隔离 PostgreSQL/Redis、后端服务与登录快照，23 项真实 PG 用例跳过，SQL 事务/并发/501 行实际查询、完整前后端和 Runner→Runtime→CLI 定时到期/逐会话结果/通知链路未实测。缺少可核的配套企微 Runtime＋CLI 和已交付旧 BOSS 客户机组合，账号切换、同名预览、真实空会话以及“仅升级服务端”后的旧工具/恢复/上报/计费仍需按受影响范围验收。前端工作台仅做受控浏览器与模拟接口走查。D5 保持进行中。未安装、部署、提交或推送。
