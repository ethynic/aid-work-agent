# AgentRunner / Desktop / Runtime 共同集成契约

2026-11-11，**共同契约 3.0 已确认，首轮待实施。** Runtime、BOSS CLI 与 WeCom CLI 同轮重构并发布 1.0.0，切换后统一使用新版作业协议。微信排除首轮范围。[第 12 节](#12-契约-30-与首轮实施)登记实施规则及旧条款处置，其余章节记录既有基线及历史决议。已确认的决议见[逐条确认记录](runtime-plugin-contract-revision-draft.md)。

2026-11-09 W32 企业微信标定配对身份检查点（契约 2.18，加性设备 API）：新版 Runtime 从既有配对配置读取服务端地址，并解密既有设备凭据，向受信 WeCom Provider 注入 `AIDWORK_RUNTIME_SERVER` / `AIDWORK_RUNTIME_DEVICE_TOKEN`；旧 `AID_WECOM_SERVER_URL` / `AID_WECOM_SERVER_TOKEN` 只保留原透传给已交付旧 WeCom 插件，新 CLI 不读取；设备凭据不向其他 Provider（既有 BOSS 除外）、前端或日志传递。新版 WeCom CLI 双点标定改调 `POST /api/local-tools/runtime/wecom/calibrate`，请求仅含截图 data URI；服务端沿现有 Runtime 设备 Bearer 鉴权，从已配对设备记录核定租户与用户，固定 GLM-5.3-Flash、坐标定位提示词、非零模型价目及原客户端 LLM ×10 积分规则，不接受客户端自报租户、模型或任意对话。响应保留标定所需 `content` 字符串，CLI 继续核对整数双点、自会话标题和缓存。本次不改 Device/H1/H3 wire、代理任务配置工具 schema 或全局最低客户端版本；原 `/api/client/v1/llm/chat` 与旧已交付 WeCom/其他客户端凭据路径保持可用，缺新版配对标定能力仅影响新客户端标定，旧任务恢复、原工具和计费不变。生产消费顺序：已配对 Runtime → WeCom Provider → 设备鉴权标定 API → 原 `wecom_config_calibrate` 回执；模块实施与验证见 [W32 计划](../plans/plan-wecom-cli-group-chat-experiment.md#w32-标定复用-runtime-配对身份2026-11-09)。

2026-11-08 代理任务 P1 契约检查点（契约 2.17，加性能力，已冻结为编码基线）：企业微信代理任务（[设计 §9](../design/wecom/wecom-conversation-operations-design.md#9-企业微信代理任务新消息触发回答2026-11-08设计待开发)、[P1 契约](../design/wecom/wecom-proxy-task-contract.md)）登记两项加性受信配置能力与一项受信监控调用，不改 Device/H1/H3 wire 字段结构与全局最低版本。CLI 新增 `wecom_config_doctor`/`wecom_config_calibrate` 两个仅受信配置流程可发起的本地工具（不加入模型工具集，同 D1 `wecom_session_directory` 页面专用先例）；结果均为既有 MCP `OperationResult` 外层、业务报告在 `data`：doctor 复用 `--localdb` 深检结构（含 `state_saved` 等实际诊断字段）并加 `schema_version:1` 与 `self_user_id`——后者是创建时当前登录账号的核定值，创建检查通过时必须非空且与标定、目录来源账号一致；就绪判定必须同时核对 `data.localdb.localdb_ready=true`、`mode='db'`、`runtime_readiness.ready` 与账号非空，CLI 退出码不进入网页通路；标定返回标题/输入双点坐标，身份未确认按 `IDENTITY_UNCONFIRMED` 拒绝，本机 `cache_path` 不进入云端契约。**创建检查与启动运行条件分离**：缺配置工具能力仅阻断新任务创建并提示升级；已存在任务的启动只核对固定设备在线、账号一致与监控/历史/发送等既有工具能力，不要求配置工具能力，坐标缓存缺失沿 CLI 发送路径原逻辑重标定。Runtime 沿 W30 先例在实际检查插件 MCP schema 后按工具名上报能力串；原三业务工具、目录、发送对账与旧客户端恢复/上报/计费不变。监控循环运行在现有 `background_runner` 进程内（按设备＋账号的逻辑职责，非新部署单元），是固定任务＋设备＋账号的 `wecom_read_sessions` 只读受信 invocation 唯一写入者（逐目标独立 `wb1` 游标、单会话串行、监控读与代理 Runner 不并行）；游标＋源库代际、位置三事实与批次结束 JSON 的唯一权威在服务端 `bs_wecom_proxy_*` 表，批次表即持久队列；任务以 `stop_requested` 持久表达"已请求停止、当前批执行中"（重启后不派新批；该标志仅在新批派发与等待恢复时检查，已运行批次允许完成至结束或进入等待，企微工具派发只核任务未删、目标有效与权限），发送 unknown 批次为不可自动恢复的 `reconcile_hold`（与可自动恢复的 `waiting` 严格区分），软删除仅在已停止后执行。批次保存本批入站消息原文与身份的 `input_snapshot`（建批时写入自已持久化的受信监控读回执），游标前移或服务端重启后派发/恢复均从快照取本批内容，不重读最新页。代理回答经新增内部受信 source `wecom_proxy` 提交：无用户 Bearer，tenant/user 从持久任务行核定，按固定 `web_session_id` 加载历史/活动摘要（非 scheduled 零历史、非 Web 网关），任务指令经内部 `prompt_augmentations` 追加，企微工具按当前目标会话装配。批次核定：仅"`needs_reply=false`＋无明确 @我＋零发送"按无需回复正常推进；至少一条 `verification='local-record'` 确认发送即推进本批上界（未回复项仅展示），`unknown` 只禁止对该发送自动重发；`needs_reply=true` 的零发送批次由后台依据本批发送工具调用事实与已保存原 CLI 回执分流——没有发送调用或所有调用均明确证明未执行发送动作才自动重试一次；发生过发送调用且任一次无法证明未发送（含 `unknown`、`partial`）只读对账不自动重发，结束 JSON 不含 Agent 转述的发送状态字段；明确 @我按监控读取消息事实服务端核定，仅拒"明确 @我＋全批无需回复＋零发送"为正常结束。回退须关闭入口并停止后台派发，已存在任务按停止语义处理执行中与等待中批次。版本化 schema、错误码、生产消费样例与决议记录统一见 P1 契约文档；本段与该文档为编码前约定，不宣称实现或真机通过。

2026-11-07 W30 实施前共同检查点（契约 2.16，加性能力）：WeCom CLI 新版 MCP `wecom_session_directory` 保留 `list/preview/resolve`，增加只读 `search` 模式及 `type=contact/group/any`；目录搜索的匹配与错名候选来自本地库，不操作企业微信 UI。Runtime Host/Core 是设备能力的唯一写入者，只有实际检查插件 MCP 工具 schema 接受 `search` 和 `type` 且该工具标记只读后，才额外上报 `wecom_session_directory_search`；原 `wecom_session_directory` 能力仍表示页面目录可用，不代表搜索可用。服务端 ToolCatalog 在正常能力查询时只消费新能力并向 Agent 投影第四项只读工具；无此能力的旧客户端保持原三项读发工具，页面目录与旧客户端的任务恢复、结果上报和计费沿用既有行为。能力查询暂时失败时沿既有可见性回退，Runtime 仍在调用前拒绝旧插件不支持的 `search`。Device API wire 仅增加能力字符串，不改字段结构。实施链路与共同验证点为 CLI MCP schema → Runtime 插件检查 → 设备心跳能力 → 服务端工具投影 → Agent 调用；正常能力查询下旧 0.2.9 插件须保持页面目录可用、搜索能力缺席、Agent 三工具，新插件须同时具备两项能力和 Agent 四工具。CLI schema、Runtime 能力、服务端投影分别由对应模块实现维护；独立测试和 CodeReview 只读检查跨端契约。发送后结果不明时不自动重发。

2026-11-04 D3 实施前共同检查点（契约 2.15，服务端内部加性事实）：沿 D2 的 `wecom_conversation_business` v2，手动入口只接受今日已登记 `conversation_ids` 和可选幂等请求 ID；拒绝客户端自报计划、设备、账号、时区、profile/instance 或历史业务日。企微专用 API 是手动受信描述的唯一生产者：从当前选定设备的只读目录取得 `account`，核对会话同租户/用户/账号/设备，构造 `{schema_version:2,kind:'daily',trigger:'manual',business_date:'YYYY-MM-DD',timezone:'Asia/Shanghai',account,device,conversation_ids:[业务会话 ID...]}`，`profile_id='main'` 且不带 instance/requirements；一次请求只提交一个 Runner，不按 50 个会话拆分。定时 v2 描述继续由 D2 的 `claim_due` 冻结生产，`task_id` 仅定时描述携带；Runner 原 `input` JSONB 与 `accepted_at` 在接单事务内持久保存请求事实，作为排队取消、业务接点前失败和首个 checkpoint 前失败的执行记录来源，不新建业务表、Runner 类型或队列。原 `client_request_id`/digest 覆盖完整描述，重投沿原幂等语义。无 Device API、Runtime/CLI、H1/H3 wire 改动；已交付旧 BOSS 客户端原工具、任务恢复、上报和计费保持，缺企微日期能力只影响本功能。

D3 同版执行与查询 schema 检查点：企微业务流是逐会话 checkpoint 事实的唯一写入者，沿原 `control.save` 在每个目标开始、成功报告保存后、普通业务失败后写入 `execution.resources.wecom_daily_progress` v1；每项只保留 `conversation_id,status,phase,started_at,finished_at,error_code` 等小型脱敏事实，不存消息正文/凭据；整轮尾的既有 `wecom_daily_result` 保留作现有通知兼容投影。Runner 每次日期读回执核对受信来源账号，设备沿原固定绑定/受信 invocation 核对；身份不一致、取消、checkpoint/所有权/计费失败整轮停止，不能被单会话异常分支吞掉。报告表仍按 `snapshot_at`（相同快照按 `generated_at`）新鲜度 UPSERT；成功报告落库后进入下个目标前必须持久保存对应完成事实。执行查询只读 `agent_runners.input` 的受理描述、Runner 状态/安全错误码、逐会话 checkpoint 及当前报告的 `source_runner_id`：无开始事实的终态目标标未执行，已开始未完成的终态目标标中断；报告已存而完成 checkpoint 失败时，当前报告仍指向该 runner 才可对账为成功，否则标“结果待核对／执行事实保存失败”，不得猜成功或未执行。报告日期页按会话 ID keyset 稳定分页，范围过滤在 SQL `LIMIT` 前；执行列表按 `(accepted_at DESC,runner_id DESC)` keyset 且租户/用户/业务日期过滤，详情按执行 ID 查受理事实，不依赖整轮尾 `wecom_daily_result` 才显示。非 JSON、截断或缺必需字段的模型输出记失败且不覆盖旧报告；空日按当日消息为零判定，不调用总结模型。生产/消费验证：专用手动 API 或 D2 Scheduler → 原 Runner 接单 `input` → 逐会话 checkpoint/报告 → 分页查询与通知，另回归普通 Runner/旧 BOSS 路径。D3 代码唯一写入者为开发角色，独立 TEST/CR 只读；此段为编码前约定，不宣称真机或真实 PostgreSQL 已通过。

D3 同版实施补充：受信 v2 日报的 `device` 是执行固定设备；用户在受理后改变选定设备，不改变首个 WeCom invocation 的设备。首次派发按原设备核活跃、在线及工具能力（不要求仍被选定），在原 Runner/invocation 行锁事务中再核已受理 `input` 的设备并写原 root v2 pin；后续页和恢复沿 pin，普通企微/BOSS 仍按原 selected 规则。日期读的 `CURSOR_UNSUPPORTED`、`NOT_LOGGED_IN`、`WECOM_NOT_FOUND` 与其他来源身份错误整轮停止；每次工具回执后先检查取消/暂停，再决定是否保存报告。报告 UPSERT 未应用时记录 `superseded`（已有更新日报，本次未覆盖），不计本次保存成功，定时通知保留原链接并说明未覆盖。以上只细化 2.15 服务端内部语义，未新增 Device/Runtime/CLI wire。

2026-11-04 D2 实施前共同检查点（契约 2.14，服务端内部加性 schema）：企微专属计划仍使用原 `scheduled_tasks`、Scheduler→Runner 受理、Runner 计费/恢复与已有设备绑定；Device API、Runtime/CLI、H1/H3 wire 均不变。计划定义的保留首行标记 `[wecom-conversation:daily]` 仅企微专用受信入口可写；配置 v2 固定 `schema_version:2,account,device,timezone:'Asia/Shanghai',target_day:'today'|'yesterday',scope_mode:'all'|'selected'`，自选成员使用既有 `bs_wecom_conversations.daily_plan_task_id` 归属，且同租户/用户/账号/设备校验。通用定时任务后端同一保留标记列表决定列表/统计/普通激活配额排除、普通创建/定义更新拒写及按 task_id 查看/变更拒绝；Scheduler 仍派发专属行。唯一配置写入者为企微专用服务；调度受理时 `ScheduledTaskDB.claim_due` 在锁定任务行的同一事务按当前上海业务日及当时已登记范围构造 `pending_snapshot.wecom_conversation_business` v2：`{schema_version:2,kind:'daily',trigger:'scheduled',task_id,business_date:'YYYY-MM-DD',timezone:'Asia/Shanghai',account,device,conversation_ids:[业务会话ID...]}`。动态全部按账号/设备展开，自选仅按当前计划关联展开；空集合记录明确 `scope_empty` 并沿现有 occurrence 条件暂停/通知，不向 Runner 提交假成功。`dispatch._build_submit_body` 只从该冻结快照复制到原 `request_data.wecom_conversation_business` 和 `wecom_task_device`，HTTP 未知重投仍使用同一 pending 内容与原 client_request_id/digest；Runner 执行消费非空冻结清单，不在排队后按 `task_id` 重新查询范围。普通任务 pending 与受理路径不变，旧 BOSS 客户端无需升级，原工具/任务恢复/结果上报/计费保持。生产/消费验证：专用配置→claim_due 冻结→pending 原样重投→Runner 只消费 ID 快照；普通任务和旧 BOSS 路径按受影响范围回归。D2 开发角色唯一写代码，独立 TEST/CR 只读；此段为编码前约定，当前尚未宣称实现或真机通过。

D2 同版实施补充（编码前）：`target_day='yesterday'` 仅每日固定时刻，缺省上海 02:00；`target_day='today'` 仅 1–24 整数小时间隔，创建、更新与暂停后恢复均在该次保存/恢复时点满 N 小时后首次到期，不改变普通 interval 调度规则。保留标记判定与旧企微解析相同，检查首行去空白后的前缀，非法 JSON 也不能经通用入口伪造；废止的 backfill 标记不再进入企微执行分支。通用列表在 SQL `LIMIT` 前排除，配额计数在创建/恢复原事务内排除。专属唯一性包括 paused 行，查重与创建/更新在同一租户用户事务锁内完成，不让普通入口绕过。

D2 退役标记补充：`[wecom-conversation:backfill]` 不再创建或进入企微业务执行分支，但通用创建/任务定义更新仍须拒绝这个历史保留前缀（含首行空白和非法 JSON），避免把旧业务标记当作普通可写任务。通用列表/统计与普通激活配额的专属排除列表当前仅含仍受支持的 `[wecom-conversation:daily]`；历史 backfill 行不获得新增配额豁免。

2026-11-04 D1 实施前共同检查点（契约 2.13，仅企微可选能力）：页面专用 `wecom_session_directory` 通过现有已选设备的 local-tool invocation、Runtime MCP Provider 和 CLI 本地库执行，不增加 Device API/H1/H3 wire、不改变 binding、claim、permit、ACK、恢复或计费；服务端是唯一业务会话登记写入者，CLI 是本地目录及预览结果唯一生产者，Runtime 只按原结果通路透传。工具不加入 `agent_tools` 或模型公开定义；只有企微专用登录接口可发起。请求 v1 为 `mode=list` 时 `{query?:string(<=100),cursor?:string(<=4096),limit?:integer(1..50)}`，`mode=preview` 时 `{session:string(ws1.),limit?:integer(1..3)}`；结果 v1 均带 `schema_version:1`、`self_user_id`、`source_generation`，列表带 `conversations:[{session,name,kind,peer_platform,same_name,avatar_url?}]`、`next_cursor?`，预览带 `session`、`messages`（最多 3 条）。来源账号/源库变更拒绝续页或预览；列表/预览只读且不推进监控 cursor，不触发旁路自动登记。错误沿现有 OperationResult：`INVALID_ARGUMENT`、`IDENTITY_UNCONFIRMED`、`CURSOR_UNSUPPORTED`、`LOCALDB_UNAVAILABLE`、`VERSION_UNSUPPORTED`、`TARGET_NOT_FOUND`、`CANCELLED`；缺工具能力仅企微添加入口提示升级，不阻断旧客户端原能力。显式 `wecom_read_sessions(sessions=[...])` 结果加性返回 `resolved_targets:[{session,name?,kind?,peer_platform?}]`（最多请求的 50 项、含成功定位的空会话），全账号扫描不返回可登记目标；原 `sessions`、`failed`、`cursor` 保持原语义。服务端仅从已接受的 Agent 读结果登记，日报日期读及目录/预览不登记。生产/消费检查：CLI 同名有消息/空单聊/空群 fixture → MCP schema/Runtime 受信名单 → 设备调用结果 → 专用 API 账号/设备/用户校验与幂等写入；显式批读空目标 → 接受回调 → 登记，原 BOSS 旧客户端的工具/恢复/结果/计费按受影响范围回归。唯一代码写入者为本 D1 开发角色，独立测试与 CodeReview 角色只读；当前为待实现契约，不宣称源码或真机已通过。

D1 同版补充（编码前）：`wecom_session_directory` v1 另支持 `mode=resolve`、`sessions:string[]`（1–50 个 `ws1.` 稳定标识），一次只读核验当前账号本地库确实存在的各目标；返回同一顶层身份/代次与 `resolved_targets:[{session,name,kind,peer_platform}]`，每个输入目标均须唯一匹配，否则整批报 `TARGET_NOT_FOUND` 或 `IDENTITY_UNCONFIRMED`，不做部分登记。此模式只供已选候选批量添加前的来源核验，不读消息、不推进 cursor、不自动登记；沿同一设备和超时/取消规则，不新增独立通道。

D1 批量添加补充：50 仅是本地目录 `resolve` 单次工具调用上限，不是页面多选或专用添加 API 的会话总上限。服务端对超过 50 个稳定 ID 的一次添加请求分批核验，同一已选设备、来源账号和用户身份贯穿所有批次；全部批次通过后在单一数据库事务登记，任一批次失败整次不写。D3 再移除手动总结 API 当前 50 ID 限制。

D1 实现限界：列表请求 `limit` 仍可取 1–50，CLI 实际单页最多返回 30 项并给下一页游标；名称最多投影 64 字，预览正文在 Python 结果输出前每条最多 256 字，目录最终结构化结果不超过 64 KiB。`resolve` 单批仍为 1–50 个稳定 ID；显式批读的 `resolved_targets` 保留所有已成功定位 locator，大小紧张时只省略可选描述字段。以上是本轮实施约束，不改变原工具或 Device wire。

2026-11-04 企微每日总结工作台设计检查点（待实施，契约仍 2.12）：保留已实现的原工具日期读取、发送核验片段、服务器结果受理后旁路日志、Runner 逐会话总结及原终态通知通路。新规则为：Agent 成功定位明确监控的稳定目标即登记、页面可独立添加空会话，登记不自动补录；每用户一个仅在每日总结页管理的专属计划，手动总结无需计划，默认主智能体模型，无特殊关注配置；计划标记由通用任务排除列表识别且通用入口不得伪造，普通任务激活数不计该计划；每次执行有独立 Runner ID 和逐会话结果，不互相打断。页面专用只读会话目录及显式批读空目标元数据需在实施前冻结请求/结果 schema、能力/错误、唯一写入者和生产消费样例，保持原 Device wire、设备绑定及旧 BOSS Runtime 的原工具/恢复/上报/计费可用。当前产品规则唯一依据见[更新设计](../design/wecom/wecom-conversation-operations-design.md)与[新计划](../plans/plan-wecom-daily-summary-workbench.md)。

2026-11-03 企微旁路日志实施记录（历史）：原`wecom_read_session`日期模式、`observed_chat`可选片段、原 Runtime 结果/ACK 通路及三张业务表的初版实现见[原计划](../plans/plan-wecom-conversation-operations.md)。当时的首次自动补录、Agent 建/拆日报计划、所选模型和特殊关注要求已被 2026-11-04 设计废止，不再作为开发或验收依据；原计划仅留真实实施与测试记录。附件原件及其他 Runner 后续优化仍见独立后续计划。

2026-11-02 U1.8共同检查点：新增可选本机H3 runtimeHost.connectionInfo返回{server:string|null}，只读Main实际活动home并投影HTTP(S) origin，无凭据/路径/业务、无网络或Host启动，失败不拦主流程。主控唯一写Main/preload/Controller/UI/菜单及打包，独测/CR只读；H1/Device/Provider与旧bridge语义不变，契约仍2.12。UI0.0.36，Host0.2.26/Core0.2.23保持；独立94测试/CR、实际EXE匿名IPC和来源核验通过。未安装、切换真实服务器、提交、部署或监听。见[计划15.4](../plans/plan-runtime-plugin-host.md#154-截图优化与runtime00362026-11-02)。

2026-11-02 U1.7：UI0.0.35仅补正常状态连接详情里的服务器切换入口，沿原register_new/confirmed和真实执行停止规则，不改Main/Host/Device/H1 schema或权限。Core0.2.23/Host0.2.26保持，实际包相同Core/Node/roots/依赖复用034兼容证据；独立77测试/CR与035实包/Downloads校验通过。未实际安装、切服务器、提交、部署或监听，契约仍2.12；见[统一计划15.3](../plans/plan-runtime-plugin-host.md#153-正常连接切换到测试服务器2026-11-02)。

2026-11-02 U1实包授权：仅构建Runtime UI0.0.34/Host0.2.26/Core0.2.23，沿当前acceptance安装身份、插件根与H1/Device语义，App不签名；显式隔离叠加已审查U1，复用BOSS0.3.29/WeCom0.2.4现包验兼容。主控唯一维护版本/打包，独测与CR只读；不安装、提交、部署或监听。契约仍2.12，见[统一计划15.2](../plans/plan-runtime-plugin-host.md#152-u1安装器打包2026-11-02用户追加授权)。

2026-11-02 U1界面/日志实施检查点：主控唯一维护Runtime页面/controller/图标，日志角色唯一维护core只读collector/Main/preload及可选agentDesktop.logs端口，TEST/CR只读。logs仅固定读取/导出本地安全诊断，不经Host管理请求、不联网或产生业务调用；不新增H1/Device/binding schema，契约仍2.12。旧bridge无logs仅缺新日志功能，旧Runtime0.0.29＋BOSS0.3.27原能力保留。沿现有Clean/installation路径，不重写恢复框架；用户已取消App签名，显式unsigned不改插件验签/产品身份，旧安装清单默认兼容。U1源码及追加授权的0.0.34实包独测/CR通过，两现有插件配套验签通过；未安装、部署、监听或提交。见[统一计划15](../plans/plan-runtime-plugin-host.md#15-runtime界面简化与正式发行准备2026-11-02)。

2026-11-02 P12出包闭环登记：仅扩展构建入口的Wecom/RuntimeWecom组件、隔离输出、缓存过滤及实际配套验证，不改Runtime/Device/binding或插件签名格式，契约仍2.12。脚本角色唯一维护出包与核验helper，主控维护文档/版本/实际构建，独测与CR只读。实际交付Runtime内置根/Core/三业务工具必须能接纳本批WeCom，不以额外测试根冒充安装前提满足；旧BOSS原功能及独立组件升级原则不变。见[计划P12](../plans/plan-wecom-cli-localdb.md#p12-统一打包脚本与可安装配套交付2026-11-02)，不授予安装、部署、监听或提交权限。

2026-10-31 P10附件实施检查点（2.12，可选扩展）：沿原三业务工具，`wecom_read_session`新增可选`attachment={message_id,offset?,max_bytes?}`，offset为文本字节位置、max_bytes默认4096/上限8192；旧输入含义不变。时间线只增加图片/文件类型及可选附件元数据，不默认读取内容；按需结果不推进时间线游标。图片使用原OperationResult内部`data.attachment.image`的有界标准MCP图片块跨原Device result/outbox传输，云端立即消费字节并转ImageRegistry/ImageRef与既有Runner图片资源引用，不将Base64/客户机路径混入模型文字。复用已有结果传输，不新增API、资源服务、授权或调度框架。CLI唯一写入者为附件CLI开发角色；云端proxy/结果消费/模型装配由桥接开发角色唯一维护；主控维护Runtime现有能力协商、契约与文档；独测/CR只读。缺新附件能力只返回该新请求不可用，旧Runtime0.0.29＋BOSS0.3.27普通工具/上报/恢复/计费不改、不强制升级。具体结果约定与组合验证见[计划P10](../plans/plan-wecom-cli-localdb.md#p10-图片与文件时间线及按需读取2026-10-30设计草案)。源码已通过独立测试与CR，验收范围见计划；用户后续授权仅本任务提交推送，不安装、部署或恢复监听。

2026-10-30 WeCom 消息读取共同检查点：沿用2.11与现有三工具输入，仅扩展已验证的本地消息类型。Markdown正文通过原text返回，可选text_format=markdown；系统事件使用kind=system、system=true及既有timeline方向，不算个人发言。旧Runtime/服务端无需升级即可透传，旧插件保持原能力，不新增能力门禁。旧v2/wb1游标摘要与普通文本发送对账语义保持；本任务主控唯一维护TS归一/消费与文档，解码开发角色唯一维护Python驱动，测试/CR只读独立验证。实施与验收记录见[本地库计划P9](../plans/plan-wecom-cli-localdb.md#p9-机器人markdown与群系统事件读取2026-10-30)，未安装新版插件前不宣称客户端已获得新能力。

> 契约 ID：`AID-RUNNER-DESKTOP-RUNTIME`
>
> 版本：`2.17`；日期：2026-11-08
>
> 状态：v2.0按用户逐条复核确定，替代v1.3的插件额外审批语义；候选0.3已有真实Host与Desktop消费验证，Windows隔离验收版已交付，尚未共同登记wire冻结。现有公开API以代码为基线；共享格式见第8.1节，任务binding、授权及资源占用wire仍待主导交付。隔离验收不代表生产发行或真实业务验收通过。
>
> 用户目标：两个会话可分别设计 Desktop 与 Runtime，桌面改版通过适配层接入，不重复开发 Runtime 或 CLI/skill。
>
> Desktop：[设计 v3](desktop-agent-client-design.md)、[开发计划](../plans/plan-desktop-agent-client.md)
>
> Runtime：[插件宿主设计](runtime-plugin-host-architecture-design.md)、[开发计划](../plans/plan-runtime-plugin-host.md)
>
> 上位基线：[已完成 AgentRunner 架构](agent-application-architecture-design.md)、[Provider 规范](first-party-cli-mcp-provider-standard.md)

## 1. 适用范围与约束层级

2026-10-30 S5验收授权登记：仅在现有acceptance fixture/隔离构建副本内打包与实际安装升级，原0.0.29＋BOSS0.3.27为升级基线，当前客户机不替换；公开固定验收签名者首次登记并保留旧根，wire/授权/恢复事实语义不变，契约仍2.11。主控唯一维护版本/锁、构建修复及公开根；独测执行自有fixture，CR只读验证边界与输入，进度唯一见[计划12.8](../plans/plan-runtime-plugin-host.md#128-s5打包与隔离安装验收2026-10-30)。未授权提交、部署或恢复监听，SMOKE与普通Main恢复的验证范围分别记录。

S5完成登记：最终Runtime0.0.32（Host0.2.25/Core0.2.22）、BOSS0.3.28与WeCom0.2.2验包/实际隔离安装通过；原0.0.29/0.3.27覆盖后的配置配对、已装core原结果补传/防重复及fresh/Clean通过，旧原始H1导入长cwd缺陷修复仅在新Runtime，不改旧端协议。原23公开根保留，首次追加固定验收发布者，非生产信任降级；契约仍2.11。仅FakeCloud/无副作用Provider及实际插件工具发现，普通Main自启/UAC/生产签名/真实业务计费未验；现用客户端未替换，自有fixture与测试认证材料已清理，证据范围见上述唯一计划。

2026-10-30 S3/S4完成登记：安装/home布局、稳定签名独立组件及受管包清理沿v2 pointer/原注册协议，H1/H3/Device/Provider字段和契约2.11不变。core HostAdapter仅增加可选内部cleanupPluginImports回调，不是wire方法，持久终态后释放自有冻结ZIP、保留未终结输入和轻量去重证据。独测114场景及旧0.0.29＋BOSS0.3.27两批真实MCP只读兼容通过，四项CR修订复审无遗留。唯一结果与旧Python/归档范围见[计划12.7](../plans/plan-runtime-plugin-host.md#127-s3s4实施登记2026-10-30)。旧根/服务端不变；未提交、安装、部署或恢复监听，S5实装未授权。

2026-10-30 S1/S2实施登记：复用core store/inventory、原Host停止证明与管理operation，解除可读历史记录的全局插件维护阻塞；通用已安装能力发现、缓存列表、可信同包修复/明确回退完成独测与CR。包格式与H1/H3/Device/Provider字段未变，契约保持2.11，不新增恢复协议/服务；真实secret损坏仍走原只读故障出口。主控维护management/Runtime/页面说明，限定开发者维护store/package及其测试；独测/CR只读验证76场景及旧0.0.29＋BOSS0.3.27两批实际MCP只读兼容。唯一结果见[计划12.6](../plans/plan-runtime-plugin-host.md#126-s1s2实施与验证2026-10-30)。下段为S0方案登记，覆盖安装/短路径/签名清理仍未实施；未提交、安装、部署或恢复监听。

2026-10-30 安装升级简化候选（仅文档，未实施）：[Runtime设计第13节](runtime-plugin-host-architecture-design.md#13-安装升级简化目标2026-10-30)与[统一计划第12节](../plans/plan-runtime-plugin-host.md#12-安装升级简化实施计划2026-10-30)为本轮唯一目标/进度。兼容覆盖升级保留原home/身份，不强制新代际；干净重装才白名单迁移。历史unknown保留原身份事实且禁重放，但不普遍阻塞安装/连接；真实未停止执行仍约束资源替换。插件readiness与已安装能力发现分离，优先复用原H1字段/store，不新建恢复服务或状态协议。

当前契约仍2.11，H1/H3/Device/Provider schema未改，本文不宣称目标已落地。实施主控统一维护core/Runtime/公共Desktop安装接线；后续独立测试与CR只读验证。服务端不预计变更，旧协议1/2/3、原BOSS工具/授权/回执/计费保持；Runtime0.0.29＋BOSS0.3.27无需升级/重配对。若实施确需wire变更，先登记具体可选字段/缺省值及旧端证据，不能借简化暗改共同接口。下文旧代际、全局历史结案和仅允许升版本的要求，如与该候选相冲突，保留为旧实现记录，以Runtime设计第13节作为后续实施依据；原执行授权与未知不重放边界继续生效。

2026-10-30 W16客户端打包收尾：驱动缓存过滤、WeCom包内AES依赖与Windows长路径启动不改变Device/Host/Provider wire或BOSS工具，历史验收根保留，仅追加WeCom验收根。契约仍2.11，不要求已交付旧客户端升级；新增功能沿原能力协商，不新增门禁。主控唯一修改打包/CLI/bootstrap，独立测试及CR只读验证0.0.29＋BOSS0.3.27与长路径合成场景，见[W16计划](../plans/plan-wecom-cli-group-chat-experiment.md#w16-master客户端打包改动复核2026-10-30)。

2026-10-30 W15服务端升级兼容：配置指纹仍写v3，恢复精确兼容master v1及分支v2；招聘profile保留master原配置，最新迁移水位后幂等补齐既有结构。仅服务端内部兼容，不改Device/Host/Provider wire、旧BOSS工具或执行权限，不要求客户机因此升级；契约仍2.11。主控唯一写入，独测/CR分别验证旧服务端checkpoint与真实存量库，见[W15计划](../plans/plan-wecom-cli-group-chat-experiment.md#w15-最小修复实施计划2026-10-30)。

2026-10-29 W14.5兼容纠正：现有显式allowed业务工具保留原模型可见性，设备capabilities只决定自动新增候选；旧provider_id继续兼容。Runtime/CLI/native工具名单、Device wire及执行权限不改，无需因此强制升级客户机。复用原registry/excluded/业务投影，详见[兼容计划](../plans/plan-wecom-cli-group-chat-experiment.md#w145-服务端单独升级的旧端兼容复核2026-10-29)；不改变2.11契约版本。

2026-10-29 W14内部装配登记：[计划](../plans/plan-wecom-cli-group-chat-experiment.md#w14-通用runtime业务工具自动装配2026-10-29)。主控唯一维护目录/Assembly/Runtime投影，独立TEST/CR只读。复用已上报capabilities和原actor设备查询，不变Device wire、权限、冻结恢复/unknown、既有域绑定或配置指纹，因此契约仍2.11，不另造协议。候选实现与模型可见性分离；安装即授权原则保持，无新审批/探测门禁。


2026-10-27 W10实施登记（2.11）：[计划](../plans/plan-wecom-cli-group-chat-experiment.md#w10-真机前综合收敛2026-10-27)。不改Device API claim/permit、OperationResult、原release、收费及授权wire；增量是三项Agent工具和内部恢复语义。唯一写入者：主控维护契约/云proxy/文档；Runner实现角色维护持续恢复，CLI维护native/TS/MCP结果，Runtime维护共用core/Host；独立TEST/CR只读验证。

2026-10-29 W13装配纠正（仍2.11，无wire变更）：内置main同样显式装配既有三项WeCom业务proxy，配置智能体仍遵守原allowed/excluded；不全量开放BOSS、Weixin或内部恢复。原Device授权/绑定/claim/permit与三工具schema均不改。唯一写入者为本会话主控（装配与文档），独立TEST/CR验证；仅本地服务器重载和用户请求的只读Web复验，不自动发送或监听。见[W13计划](../plans/plan-wecom-cli-group-chat-experiment.md#w13-main缺少企微三工具2026-10-29)。

W13实际执行接点：ToolExecutor的InputModel只校验/规范化公共业务字段，LocalToolAdapter注入的原可信上下文完整传给execute；无InputModel路径及纯normalize/冻结恢复证明不改。未增加LLM内部参数、放宽未知公共字段或修改Device wire，已用实际Web DB读取复验。

- Agent仅`wecom_read_session(session,cursor?,limit?)`、`wecom_message_send(session,text)`、`wecom_read_sessions(sessions?,cursor?,limit?)`；MCP保留原调用共15项，Runtime受信9项。CLI生产`ws1`会话、`wb1`分页及`sender_platform`，云端透传，不自建平台算法；缺字段按unknown。CLI0.2.0的schema_digest为`sha256:3cc508ef7b22b0daf2050e5843da644e56165c50bcfb3f479b1835141daa82db`。新Runtime默认兼容旧8项，真实MCP schema证据核实后更新9项；新参数缺能力返回明确升级错误，协商暂时故障可重试，旧入参正常执行。
- 私有`wecom_task_device` v2沿用原root/设备原子绑定，覆盖单聊/群聊现代调用；v1旧群调用继续兼容。`continuous_activation` v1是服务器可信接续来源，不替代权限或重放许可；任务依赖指纹v3仅将可验证匹配的旧v2迁移。暂停/取消优先，暂时源错误保留原wake并退避。
- 命名发送先持久化内部`wecom.resolve`只读phase（native read_session的resolve_only），main只使用该原设备有效结果的canonical；Agent schema不含该参数，稳定ID发送无额外查询。解析阶段不读正文或推进cursor，恢复保原模型意图并验证原解析/main，unknown不因改用稳定ID而重放。
- CLI读取消息保留不可变baseline/start_fence和未知身份的原消息恢复cursor。Runner仅在原invocation存私有`_wecom_inbound`待处理offset，当前结果与`_wecom_recovered`原子交接；checkpoint只存固定轮转位置，原调用/late_result/用量不改写。读unknown仅逻辑允许重读；外发unknown只读对账，不自动重放。
- 既有Runtime恢复观察可带`provider_key=wecom`，能力名`runtime_recovery_wecom_v1`；不伪造BOSS页面，WeCom page为not_applicable。可信resource_stopped可释放原物理hold，不将未知消息变成成功。outbox原payload/endpoint保留，仅本地可选next_attempt_at和有界补投轮次；AbortSignal为进程内参数，未新增wire。已ACK且效果已知、证据一致的journal才按7天清理。
- 生产/消费组合已覆盖旧调用、新schema、真实MCP只读协商及隔离Worker/SQL。签名发行包安装、真实业务UI、多天运行未验，不将源码组合通过当成正式部署验收。历史签名根不替换。


2026-10-26 W6实施检查点：新增`wecom_group_poll` schema v1（target_name/cursor）及文本发送可选`group_cursor`，CLI唯一生产、MCP/Runtime第8项受信工具及云proxy共同消费；旧CLI缺能力在实际listTools门禁零调用拒绝，旧7工具普通调用不变。首次context不作new，后续账号/群/generation/anchor及原群名独立游标；不消费共享watch水位。监控root文本回复必须带原group_cursor、不得ref替代，其他媒体发送不在本轮授权范围；CLI发送前验证原账号/群/库且不可OCR降级。Runner checkpoint私有`resources.wecom_task_device` v1（原root identity/runner_id/device_id）在首次poll与invocation同事务原子固定，后续root/child WeCom沿原设备；运行时配置指纹升级拒绝旧Worker静默忽略。Device/H1/H3 wire与release/unknown/outbox语义不改，新工具只用于新版release。唯一写入：主控CLI/MCP/core及文档，backend角色云端配置/代理/Runner及后端验证，独立TEST/CR只读。详见[W6计划](../plans/plan-wecom-cli-group-chat-experiment.md#w6-配置智能体完整链路2026-10-26)；不部署、不恢复监听，不宣称真实群及多天验收完成。

2026-10-22恢复增量实施检查点（[修复计划](../plans/plan-runner-local-tool-recovery-lockup-fix.md)，开发中）：Device wire、H1/H3及绑定身份不变。唯一写入者：主控维护后端恢复证明/公开投影与本契约；Runtime传输角色维护共享Core及Runtime消费；BOSS恢复角色维护CLI本机恢复。v2 `result-outbox/<id>.json`保持原格式；v1结果使用同一outbox设施的`legacy-v1/`子目录，显式`protocol_version=1`、`endpoint=result`，只补传原payload。旧消费者只扫描根目录，因此忽略并保留v1文件，不得误投operation-result或重执行；新版分别投递原endpoint，ACK前保留。CLI保持OperationResult/error枚举及operation_recovery schema2、visual_recovery schema1；现有诊断字典补充脱敏失败类别/子阶段。browserTargetBinding可增加本机可选业务URL及原profile信息；原profile可由已有可信本机配置或实际浏览器进程参数确认，不强制要求新增managed_profile_dir字段或“曾由本启动器拉起”的磁盘记录。旧记录缺字段但仍可确定原profile时支持自动重开，确实无法确定时准确说明，不猜测陌生profile。只读/可重设页面可重建tab，未知外发不重放；取消/失权禁止重开。当前CLI调用内确定性恢复、Runtime独立续租，不新增本地Agent或后台恢复调度协议；既有release绑定不改，需新版能力则在原会话合法创建新版接续执行。生产/消费与旧格式组合验证按本批影响范围登记，未验收前不宣称兼容通过。此前“只attach”的历史限制在用户明确授权的原profile重开范围内被本条替代；不得杀无关Chrome、清Cookie/profile或偷换账号。

2026-10-20共同检查点：用户授权BOSS视觉恢复直接复用Runtime配对身份，禁止额外URL/ClientBinding token配置。新增设备API`POST /api/local-tools/runtime/boss/visual-diagnose`（复用既有视觉body v1/v2及响应/20倍计费），不改H1/H3/Device配对/claim/permit格式。Host唯一从当前配对配置/DPAPI凭据为受信BOSS进程注入内存字段`AIDWORK_RUNTIME_SERVER`和`AIDWORK_RUNTIME_DEVICE_TOKEN`；不向其他Provider、renderer/IPC、日志或磁盘扩散，不接纳父env覆盖。CLI本地恢复职责保持。旧ClientBinding API不改，历史台账保留；新BOSS只消费新设备入口，旧插件不消费新字段且需升级。唯一写入：本任务主控维护Runtime Host/CLI/Main及版本，backend身份角色维护设备路由/视觉主体与台账；独立角色只读审查/测试。两端计划已同步登记，实施及实际producer/consumer组合测试记录见[BOSS计划23](../plans/desktop-automation/plan-boss-operation-verification-visual-recovery.md#23-runtime配对身份复用纠正2026-10-20)。本版本仅增加该设备API及受信BOSS调用边界，不声称其他共同wire已完成冻结。

涉及桌面重设计、Runtime、插件、设备/workspace 授权及相关 Runner 接线的会话，开始工作前必须读取本文与相应设计/计划。该要求同时登记在根 AGENTS.md，不能依赖某个会话的记忆或转述。

系统/会话指令及用户明确要求优先。本文定义两项工程的共同边界；各自设计只细化内部实现，不能单方面改写共同边界。发现当前代码与文档不符，先区分“代码已实现”和“计划扩展”，记录差异，不能用文档假装新 API 已存在，也不能把遗留耦合继续固化为新目标。

本文冻结架构职责和兼容规则，不冻结页面、组件、目录排版或全部技术实现。后续批准的架构调整可以演进，但须带版本、兼容和迁移记录；不承诺未来所有产品变化都零返工。

2026-10-08 定稿结论：Runtime 的 v1.3 补充与 Desktop 的同机/异机设计一致，无需继续增加架构条款。双方以本版本为实施基线，在职责范围内独立推进；内部实现细节和既定检查点的接口产出不要求再次讨论完整架构。后续只有实际改变共同语义或兼容规则时才升级本契约版本，普通审阅、进度登记不升版本。

2026-10-09用户复核决议：安装即本机使用授权，安装成功默认enabled=true，运行条件独立决定ready，不另设插件审批或默认逐次弹窗。账号/配对/设备使用关系、目标workspace及既有任务策略继续复用。允许安装分析调用云端模型：先读SKILL.md，信息不足时才补传必要源码；这不是本地任务Agent loop。日期标为v1.3的记录属于历史基线，冲突处以v2.0及第8.1节当前候选0.3为准。

当前实施范围：第一部分Runtime UI、BOSS/weixin/wecom第一方插件安装管理及必要核心/既有执行兼容已交付隔离验收版，人工与正式发行门见统一Runtime计划。第三方skill安装、AI分析、动态登记/执行为第二部分，契约/schema/fixture保留，暂不开发。当前以候选0.3为格式入口，不因文档收敛改变C01～C12、schema或版本；第一部分不能把保留的skill格式展示为已支持能力。公共壳/H2/H3职责保持。

恢复增量兼容细节（2.6）：公开 Runner 投影新增可选 `local_recovery_kind` / `local_recovery_actions`，只展示候选动作，不授予执行权；旧 Web 忽略新增字段，新 Web 遇旧服务端缺字段时沿原入口。后端继续在原受权事务核对恢复/结束能力。续筛领域阶段保存私有 `_resume_history_version=2`，旧缺字段沿 v1 原资格和排除名单证明，不改变已接受 invocation 参数；私有版本不发往 CLI/Device API。CLI 本机 binding 的可选 `restore_pending` 只记录已创建页面尚待登录/职位/筛选恢复，原 ref 和 target 索引同步，旧普通读取不依赖这些新增字段。CLI 内部持久记录不承诺在降级至旧插件后仍能自动恢复新 target；在途调用继续使用原 release，不热换代码。实际组合测试及未验收范围见修复计划第9节。

## 2. 必须稳定的十二项边界

C01～C12 保留原编号。契约 3.0 首轮对这些边界的细化及替代范围见[第 12.6 节](#126-原条款处置)。

| 编号 | 共同要求 |
|---|---|
| C01 | AgentRunner 是聊天任务、推理、等待/恢复及用量的唯一权威；Desktop/Runtime 不新增本地 Agent loop。 |
| C02 | Desktop 使用主 API 的公开 Runner 网关；Runtime 使用设备 API。客户端不持有内部 Runner service token。 |
| C03 | UI 发起/观察/控制任务；本地业务动作由 Runner 创建受权 invocation，经 Device API 到 Runtime。renderer 和 Host 管理接口不得直接发起插件业务调用。 |
| C04 | 同一 Runtime core 服务 CLI、独立 Runtime 客户端、完整 Agent Desktop；独立 Runtime 客户端复用同一 Electron 工程，是已确认产品形态。 |
| C05 | Runtime core 无 Electron/Vue/页面和具体 BOSS/微信业务依赖；Desktop 不导入 Provider domain 源码。 |
| C06 | 设备skill代码和依赖只在设备安装；云端保存原始手册/必要参考资料、能力入口/代码地图及摘要。安装分析先读手册，必要源码允许发给云端模型，不要求服务端部署代码。 |
| C07 | MCP CLI与skill共用安装、版本和执行生命周期，调用语义不同；用户安装即授权使用，安装成功默认enabled=true，运行条件独立决定ready，无额外插件审批。SKILL.md本身不等于脚本入口，手册型skill不虚构入口。 |
| C08 | tenant/user 来自可信认证；执行设备、workspace grant、插件版本来自验证后的绑定。LLM、prompt、自由 request_data 不创造权限。 |
| C09 | 新本地任务固定设备；调用固定 contract/release/digest。换全局 selected、页面、UI 产品或升级不能改变原任务执行位置和代码版本。 |
| C10 | Runner 状态与本机进程状态分别投影；隐藏页面、断开订阅不取消云端任务。退出本机执行进程也不等于副作用已取消。 |
| C11 | 已可能发生写动作的 unknown 不自动重放；ACK 前仅补传原结果。不同 Provider 的 v1/v2 能力按实测登记。 |
| C12 | 修改共同 schema 必须版本化并同时验证生产端/消费端；旧客户机、旧 Web/渠道和已接受任务不能因新版 UI 静默变义。 |

## 3. 三条接口与两个适配层

```mermaid
flowchart TD
  UI[Desktop / Runtime UI] -->|本机 Host Management Port| Main[Electron main adapter]
  Main --> Core[Runtime Core]
  Entry[Web / Agent Desktop] -->|公开 Runner API| Cloud[云端 AgentRunner]
  Cloud -->|受权 invocation| Device[Device API]
  Core -->|claim / progress / result| Device
  Core --> Plugins[MCP / skill / local-file adapter]
  Remote[其他电脑独立 Runtime] -->|claim / progress / result| Device
  Remote --> RemotePlugins[目标电脑文件 / 程序 / 软件]
```

- **Runner API**：任务提交、查询、观察和控制；不能经其普通 reply 提交伪造的设备结果。
- **Device API**：设备身份、能力、领取与执行事实接纳；只有受信 Host 使用 claim/permit/device token。
- **Host Management Port**：本机配对、安装、启停、状态、workspace grant 管理；不提供 `invokeTool`、任意 shell 或通用文件操作。

Desktop 的 Electron adapter 转换 IPC/凭证/选择器；Runtime 的 platform adapter 提供进程、安全存储、文件和桌面原语。调整 Electron 窗口、路由、IPC transport 或打包细节时，只改变 adapter 与产品组合，不要求 Provider/skill 重写。

现有 `LocalToolHostCore.invoke()` 是预留的内部执行接口，不是 renderer 管理 API；不能把该接口直接暴露来绕过云端 invocation。

### 3.1 已实现的 Runner API 基线

源码依据：`src/api/agent_runner_web.py`、`src/services/agent_runner/{contracts,control_contracts,event_contracts}.py` 和当前 Web client。

| 操作 | 客户端公开入口 |
|---|---|
| 能力 | `GET /api/chat/runners/capabilities` |
| 创建 | `POST /api/chat/runners` |
| 查询 | `GET /api/chat/runners/{runner_id}` |
| 找回会话任务 | `GET /api/chat/sessions/{session_id}/runners` |
| 观察 | `GET /api/chat/runners/{runner_id}/events?after_seq=...` |
| 取消 | `POST /api/chat/runners/{runner_id}/cancel` |
| 控制与结果 | `POST /api/chat/runners/{runner_id}/controls`、对应 GET |

桌面首期沿 `source=chat`、`session.kind=web`，不新造 desktop source/会话表。网关创建字段是 `message/session_id/files/subagent` 等，不是内部 RunnerSubmit 的 `text/session/attachments/profile_id`；两端之间的映射由网关负责。

当前能力响应 `contract_version=1` 是 Runner 公开 API 版本，与本文版本、Electron bridge version、Provider protocol_version 分开。公开 controls 只允许 pause/resume/reply；内部 browser_complete 不开放为普通 Desktop 控制。当前事件是 created/revision_changed/terminal/settlement_changed 等状态通知，不能当原始 token 流。

创建 202 只表示接单；相同请求键绑定不可变意图。事件游标用于补读，snapshot/result 是展示基础。新增 binding 字段尚不在 WebSubmission 中，不能自行塞进 request_data 充当已验证授权。

### 3.2 新 Host 管理接口的最小共同面

下面冻结操作语义，非已有 IPC 方法名；实际 DTO、schema、编码和 IPC channel 由第 8 节第一项产出统一冻结。

| 操作 | 输入与结果的稳定语义 |
|---|---|
| describe | API major、实现版本、支持特性、当前状态 revision；不能把 UI 版本作为 Runtime 协议版本 |
| getState / observe | 当前实例、谁监管它、设备绑定、在线/领取状态、插件就绪摘要；observe 提供取消订阅，断流后可重新查询 |
| pair | 可信 UI 提供服务地址、设备名、一次性码；Host 完成配对并保管 token；响应不含 token |
| start / stop | 管理执行实例；stop 先停止领取并 drain，返回实际停止/等待核对状态；不承诺杀进程等于业务取消 |
| plugins.list | 本机安装、启用、就绪及诊断；无插件也是合法状态 |
| plugins.import / enable / disable / uninstall | 本机选择器提供受控 selection_ref，返回 operation_id；执行入口与本地授权由 Host 校验 |
| operations.get / observe | 安装、环境检查、停止等管理操作进度；该 operation_id 不冒充 Runner runner_id 或 invocation_id |
| grants.create / revoke / describe | 受信选择器与策略形成 opaque grant_ref；云端授权接线共用同一模型，绝对根路径留本机 |

必须返回机器可判定错误码及可展示消息；凭证、配对码、claim/permit、环境变量和任意设备路径不进入状态投影。UI 展示类型与 wire schema可分离，但必须通过显式 adapter。

安装操作可以是本机管理操作，不必伪造聊天 Run；用户选择安装即本机授权；安装成功默认启用，依赖/配置决定ready。云端登记校验身份、格式和版本，不增加插件批准关口；上报失败应保留同步诊断，不能伪装已对云端可见。上述管理 port 不接纳模型直接发来的安装或执行参数。

## 4. 两个会话的责任和文件边界

| 唯一实现责任 | 负责工作流 | 另一侧如何使用 |
|---|---|---|
| Runtime core、CLI薄壳、Device API client、MCP/skill生命周期、插件导入/环境/状态/产物 | Runtime 工作流 | Desktop 经管理 port / Runtime child 使用 |
| 公共 Electron main/preload、安全 IPC 注册、全局 Desktop Shell、构建入口与共享 Node 打包策略 | Desktop 工作流 | Runtime 提交独立模块/port及 Runtime 产品需求，由公共壳接线 |
| 独立 Runtime 管理页面/模块及需求 | Runtime 工作流 | 放入独立 feature 模块；不在共享入口另写一套窗口/凭证/监管实现 |
| 公共 Runner client、桌面对话投影、提交/观察/控制 | Desktop 工作流 | Runtime UI 不复制聊天 client 或模型循环 |
| 固定设备 execution binding 的网关/Runner应用层接线 | Desktop 工作流主导共同边界变更 | Runtime 提供设备验证 adapter；不得另定义第二个 binding |
| 本地目录 grant、文件引用/Provider | Desktop 工作流的文件能力模块 | 接入同一 Runtime adapter registry，不建立第二个 Host；Runtime 提供平台原语 |
| 插件契约登记、安装状态、skill加载和执行适配 | Runtime 工作流 | 复用上述 binding/grant/Device API，不能另写任务归属和通用审批账本 |

源码建议模块：Runtime core 在 `clients/shared/local-tool-host-core`；公共壳在 `clients/agent-desktop/electron`；Runtime 专属模块在其 `runtime/` 子目录及 `frontend/desktop/features/runtime/`；路径是组织建议，port 才是稳定依赖边界。

开始修改共享入口、binding、Device API公共模型、schema、全局 package/lockfile 前，在两份计划的共同边界登记区指明本批唯一写入者和文件清单。同一批不得双方并行写这些文件；先交模块后接线。登记不是新的提交授权。

上述责任是工程分工，不意味着另一个会话可以向用户声称已协作验收。另一侧尚未读取、实现或验证时，按实际状态记录。

## 5. 任务绑定、资源和版本的共同语义

- **任务 binding**：网关验证设备归属、设备授权和必要 workspace grant后，持久化到原任务意图/ExecutionState，再经 ToolExecutionContext 派发；子执行继承。不能每个步骤重新查全局 selected。
- **旧 selected**：只保留在明确的 legacy路径；新契约拒绝静默回退。纯云端任务不必绑定设备。
- **workspace grant**：可撤销、有 revision 的受权目录引用，不是“知道 grant_id 就授权”。插件状态目录与用户文件 workspace 分开，安装 skill 不自动获得文件 grant。
- **插件选择**：每次 invocation 固定 release和 content/contract digest，不假设一个 runner 只能用一个插件。加载手册与对应调用版本必须一致。
- **资源结果**：本机文件引用、云端 file_id、device artifact_ref 保持有类型的归属；不能互用裸绝对路径。云端 Agent读取本机返回的内容会经过服务端/模型。
- **恢复**：分别检查 Runner attempt/owner、claim/permit、绑定/授权 revision 和本机操作事实。账号改变或旧 owner 失效不能借新身份重传业务意图或执行下一步。

execution_binding、grant与新增审批的精确 DTO 在共享 schema冻结前，双方可以完成内部设计、云端纯聊天、插件本地导入/检查和管理 UI mock。不得分别实现两套 wire协议后再拼接。授权与恢复的共同接口必须先于依赖它的业务代码定稿。

### 5.1 Runtime 作为 Runner 的统一任务执行环境

本节记录原执行上下文基线。3.0 首轮的完整插件作业、插件业务职责与可选会话关联按第 12.2～12.3 节适用，原受权设备、文件和版本归属原则继续适用。

2026-10-08 用户转交的桌面设计定位与 C01～C12 一致；补充以下语义，不另建调度或授权体系。

Runtime 除插件宿主外，还提供任务执行上下文和文件、进程、应用、产物等能力适配。任务上下文由同一 H2 受信 binding 派生，至少关联原 runner/execution、固定设备、可选 workspace grant及版本、策略引用、取消/执行权和本次 invocation。具体字段由 H2 schema统一冻结，Runtime 不自行定义第二个任务绑定 DTO。

read/edit、受控 command、skill 和软件操作均使用该上下文及相应能力授权；不能 read在客户机而 edit写服务端同名目录，也不能每步重新选设备。云端附件仍有独立 file_id位置语义，不因任务有本地 workspace 而自动变成本机资源。

“同一执行环境”是相同的任务/设备/资源授权语义，不要求每任务启动 VM、每插件共用一个 Python解释器或所有进程使用相同 cwd。插件包目录、任务workspace、可写状态与产物目录用途不同；脚本相对用户文件路径必须通过已验证的 workspace/输入引用解析，不能按插件源码cwd猜测。Plugin adapter可拥有独立venv，但不能借此更换设备或授权主体。

普通本地程序可执行循环、分支、过滤、统计、格式转换和批准的确定性动作链。需要新的模型判断、扩大权限或新的业务决策时，由原 Runner继续；不在 Runtime新建模型循环或把任意代码包装成批准的 batch。

批量能力通过已登记的结构化工具或受信脚本入口提供，先实现 batch search/read和必要本地聚合，完整PTC/任意 execute_code后置。批量中的文件操作仍遵守同一grant和版本检查；部分失败、漏读、截断要返回实际数量和完整性说明。带副作用的批次中断后，不自动从头重跑；只有有证据的动作适配器才可按原操作事实核对。

本次新增语义不代表脚本已有OS隔离：cwd/venv/目录引用不是沙箱。未隔离第三方代码仍可能访问当前用户资源，其运行必须符合明确本机授权和企业策略；要求强隔离的策略不能用一次授权弹窗代替实际隔离。结构化文件能力可约束文件访问，不能据此声称任意Python脚本也已被限制。

### 5.2 审批和计费的边界

Runner/服务端策略保存审批与执行许可权威；Desktop或独立Runtime UI展示批准交互，Host复查执行边界。安装即插件使用授权，不新增插件审批或默认逐次调用弹窗；身份/设备/workspace和确有既有策略要求的任务许可继续共用原体系，不分别建业务批准账本，也不能把普通澄清reply转换为许可。

保留云端Runner是为复用企业任务、授权、恢复与账本，不以“本地loop无法计费”为理由。Runtime不另建模型用量账本；必要的执行耗时、资源使用和业务回执由既有服务端计费规则接纳。模型供应商usage仍来自实际模型调用。

性能按领取等待、执行、回传、输出量和模型轮次分别测量；本地batch与脚本优先。后续快路径仅替换通信transport，不改变invocation身份、授权、去重、取消或原结果核对；本版不冻结尚未测量的低延迟承诺。

### 5.3 操作入口与执行电脑分离

用户已确认两种部署形态：Runtime 与完整 Desktop 同机，作为该电脑的执行入口；Runtime 独立安装在另一台电脑，由 Web 或 Desktop 经云端 Runner 调度。后者适用于特定网络、特定软件以及 BOSS 等长期占用交互桌面的专用电脑。两种形态共用执行核心和 Device API，不因入口位置新增一套 Runner。

| 操作入口 | 固定执行设备 | 共同语义 |
|---|---|---|
| Desktop A | A 上受管 Runtime | Main 可管理本机实例及选择本机目录；业务调用仍经 Runner |
| Desktop A | B 上独立 Runtime | A 不必启用自己的 Runtime；任务的文件、命令、软件与网络访问发生在 B |
| Web | A 或 B 上已配对 Runtime | 浏览器不安装执行核心；通过可信网关选择有权使用的设备 |

目标设备选择、可用能力及忙碌状态通过用户认证的服务端设备接口查询；Host Management Port 仅管理同机实例，不直接暴露为网络远程管理接口，也不把 B 的设备 token 发给 A。具体列表/选择 DTO 与现有接口适配由 H2 冻结。Web 与 Desktop 使用同一 binding 校验，不能让 Web 继续依赖全局 selected 来实现新任务语义。

每个新设备任务在接单时固定目标设备；界面展示执行设备、连接/忙碌状态及资源归属。本轮不扩展为一个任务随意跨多台电脑，后续如需多设备编排另行版本化。目标离线、缺少软件或特定网络不可用时返回等待/失败事实，不迁移到入口电脑或云端。

设备列表、心跳和忙碌投影是选择提示，不是执行许可或预留成功。实际执行前仍由服务端核验授权、Runtime核验能力和资源占用；列表查询后状态改变时返回原设备的等待/拒绝事实，不借“自动选择可用设备”改变已固定任务。

### 5.4 目标设备目录、网络与产物

Desktop A 的本机目录选择器只能建立 A 的 grant，不能据同名路径生成 B 的授权。远端文件任务使用 B 的 Runtime 本机建立、经服务端验证且允许当前用户/Agent 使用的 grant；新建远端 grant 由 B 的受信管理交互完成，首期可预先在 B 配置。Web/远端 Desktop 选择可用 grant 的引用，不获得任意枚举 B 文件系统的权限。

文件引用和产物始终携带固定设备归属。A 的 Main 本机打开接口拒绝 B 的文件引用；查看 B 的产物需经受权内容/产物接口，下载到 A 是显式复制操作，不改变原文件的位置和授权。云端附件继续沿自身 file_id 语义，不能将 B 的裸路径交给 A 或服务端解析。

Runtime 执行的请求使用目标电脑的实际网络、软件安装和登录态；拥有特定网络并不自动授权所有网络资源。网络和凭证能力按策略校验，状态只投影可用性和必要诊断。此契约不增加通用代理、网络隧道或把 B 的网络/凭证复制到 A。

### 5.5 专用电脑、长时间执行与资源独占

本节记录原资源基线与实测。3.0 首轮采用第 12.5 节确认的串行队列和人工可并行声明。本节的受管进程停稳、授权与真实运行条件原则继续适用。

独立 Runtime 在其配置的生命周期内持续领取/执行，Web 关页或 Desktop A 退出、注销不停止 B 的执行实例，也不取消已接受任务；显式撤权、取消、租约失效仍按原权威和核对链处理。目标电脑休眠、锁屏、断网和退出 Runtime 分别报告实际状态，无窗口运行不保证 GUI 在锁屏时可操作。

设备在线、Runtime 单实例和桌面资源独占是不同状态。同一电脑交互桌面的锁覆盖全部插件、CLI 与后台观察；纯文件/计算不机械占用桌面锁。Runtime 负责实际资源仲裁，服务端/入口展示等待、占用与阻断，不创建第二套业务调度器，也不凭远端 UI 状态强行解锁。

BOSS 等跨多次 invocation 的连续 GUI 流程若需保持桌面独占，不能假定已有单次 invocation 锁已经满足。H2 与 H4 实施前统一资源范围、原任务 owner、占用期限/续租、跨调用保留及释放/失联核对语义，并验证全部竞争入口。确定性程序可在一次已授权执行内持续持锁；模型思考或等待期间是否保留桌面，必须由流程契约明确，不默认永久锁整台电脑。未知副作用核对与资源占用分别处理，不能无限占锁或自动重放。

现状证据：`clients/agent-tool-runtime/src/desktopLock.ts` 的 `withDesktopLock(fn)` 在回调结束后释放锁，`invocationRunner.ts` 在单次Provider调用外持锁；其锁按本机用户/交互会话派生，不依赖云端device_id。这证明已有同桌面单调用仲裁，不证明跨调用独占已实现。

长任务按其实际支持的协议复用 progress、取消、claim/permit、journal/outbox 与续期机制，记录当前机制不能覆盖的超时/占用边界；不把旧skill v1视为具备完整v2恢复能力。新增字段、状态及超时规则先经 H2/H4 schema 冻结和兼容验证，再承诺长时间无人值守能力。

实施前还须冻结以下四项，均为上文边界的细化，不增加另一套业务调度器：

1. **资源需求来自Host核验登记的契约**。是否操作桌面、是否跨调用保留独占，由登记的工具/流程契约声明并在执行时核验，不能由LLM参数或脚本自报“只读/纯计算”解除锁。未知第三方脚本保留保守桌面占用；经明确核验的文件/计算能力才可免占桌面，不增加插件批准步骤。当前调用链统一持桌面锁，按资源分类并发属于待实现扩展。
2. **占用过期不等于进程停止**。跨调用占用记录关联原任务、执行owner和可核验的占用代次；旧owner不能续租、释放或借重启沿用新的占用。取消、超时、失联、Host崩溃后，确认原进程及相关后台动作停止才能将物理资源交给下一任务；不能仅凭云端租约到期或锁句柄消失认定桌面安全。无法确认时明确阻断冲突调用并进入核对，展示原因和受信本机处置入口。业务effect仍可为unknown，不能因释放资源就将其改成none或重放；反之，已确认动作停止后也不因业务结果unknown永久持锁。
3. **断网执行必须有可验证的许可边界**。只有协议与本机监管明确支持、且仍在有效许可内的已批准确定性执行才可推进；不得凭缓存授权新领任务、新建写动作许可或无限续租。要求在线复查的动作在断网时不得开始；本机撤权/停止及许可到期按预定停止策略处理，确认不了停止则按上一项阻断。云端撤权在离线设备上不能承诺即时生效，H2必须明确许可期限与失联策略，策略不允许离线继续时不得启用该能力。恢复联网仅核对和补传原事实。
4. **调用超时、占用期限与流程期限分开**。progress不自动延长执行许可或超时，资源续租也不自动延长脚本运行时间。当前skill默认900秒、硬上限1800秒，不因采用独立Runtime就获得无限时长。超长流程优先由原Runner按受权调用分段推进，跨调用占用不等于跨调用授权；确需扩大单次时限时先冻结监管、取消及兼容规则，不能用重复启动脚本规避限制。

### 5.6 通用持久化休眠增量（2026-10-22）

用户确认由 Agent 主动调用业务工具并自主休眠，到期由云端 Runner 恢复同一任务。候选[设计](agent-runner-durable-sleep-design.md)及[计划](../plans/plan-agent-runner-durable-sleep.md)扩展云端 timer 等待与持续任务，不要求 CLI 主动唤醒，也不在 Runtime 增加模型循环、长期业务调度器或 sleep Device/Host wire。

本条在契约2.7登记实施检查点；公开Runner API保持contract_version=1，候选0.3 Host格式不变。沿原会话、runner行wake_at与checkpoint特性扩展，不强制新会话、timer表或全局schema升级。云端timer不能延长原invocation的timeout/permit/资源独占；休眠与唤醒继续核验原主体、固定设备、任务claim和已有调用事实，unknown写不自动重放。单次调用版本固定规则保持。

实施前由本任务主控登记Runner接单/快照/能力协商/checkpoint的候选版本、唯一写入者和生产/消费fixture；Web/Desktop公共Runner消费与现有两项计划登记影响，Device/Host/Provider无变化则明确记录不受影响。旧worker当前没有新任务隔离领取保证，启用前须验证领取池版本一致；不以旧UI忽略可选字段代替严格解析及恢复兼容验证。此时再按实际差异登记契约版本，本次不宣称已共同验收。

2026-10-22用户反馈候选：普通请求不增加必填模式，仅可选continuous或经原ask_user/reply绑定确认启用；新普通任务默认50业务轮次且成功纯sleep不占数，实际模型调用/费用仍记账。会话显示累计积分，由用户决定任务寿命，不新增任务总预算。多个runner或单任务多目标的本机调用复用既有invocation队列/标准领取loop/桌面锁，CLI互斥不再复制成新队列；本条不扩张wecom受信工具面或宣称5群已验收。

实施共同检查点（生产/消费测试完成后才记为验收）：唯一实现写入方为本Runner功能工作流，休眠开发角色拥有park/恢复/worker，持续任务开发角色拥有模式确认/账本/公开投影，主控拥有Web消费者与契约；同一源码文件修改前交接所有权。

| 接口/格式 | 冻结增量 | 兼容及验证 |
|---|---|---|
| 创建与能力 | 可选`execution_mode="continuous"`，普通请求省略；能力`durable_sleep_v1`、`continuous_execution_v1` | 旧省略字段digest不变；缺能力即不支持；默认关闭，新API/worker组合隔离测试后才开放 |
| 公开视图 | 顶层可选`execution_mode`、脱敏`sleep_blocked.error_code`；`usage={task_credits,session_credits,pending,updated_at}`；snapshot保留实际iteration、新增business_iterations及可选history_version=1 | 白名单显式投影，无私有checkpoint泄露；Web真实producer/consumer fixture验证，旧消费者可查询/取消 |
| timer leaf | `kind=timer,version=1,seconds,reason,wake_at,wait_id,target_execution_id`，嵌套child_wait沿原树 | resume携带原wait/leaf，服务端消费再次核验；旧无定位客户端可取消，不能盲目继续timer |
| 分段执行保护 | `kind=continuous_guard`，assistance_id按实际iteration区分本段；同样定位原wait/leaf | 根或child均可显式继续，仅重置所选叶段起点，旧wait不得给新段额度；子执行正常委派回复仍可完成并返回父 |
| 持久格式 | 原runner行nullable wake_at、checkpoint.durable_sleep.version=1，continuous.version=1 | 同事务park/消费、当前owner fencing；未知版本拒绝，旧任务无字段沿原恢复规则 |
| Device/Host/Provider | 无新增字段或接口 | 无本地睡眠/模型loop；原claim/permit/release/digest/锁不改 |

回退必须先关闭新能力准入并保留支持版本处理存量，或安全结束新语义任务；旧worker不能与新timer同池，即使paused仍可能被旧cancel-only领取。不得以pause所有任务作为充分回退方案；配置/全局prompt严格指纹不关闭。实际producer/consumer测试与限制统一登记[休眠计划第13节](../plans/plan-agent-runner-durable-sleep.md#13-本轮实施基线与共同检查点)。

2026-10-22整体复审修复检查点（2.8，本地开发验证完成）：唯一修复角色维护云端恢复/工具身份/归档及schema，主控维护本契约和关联设计；独立测试与CR只读。Device/Host wire及API major 1保持。云端私有`tool_identity_version=1`按原持久模型调用区分新工具代次，旧pending及已接受invocation不重命名。历史产物复用chat_messages.metadata已有images/downloadableFiles；不在Runtime新增loop、队列或资源授权。累计BIGINT为数据库内部兼容扩大。公开snapshot新增可选`history_revision`（非负整数，单调历史投影水位，不是event_seq/权限），由原history_offset、artifact_history_offset及archived_child_count导出，同checkpoint重试不变；供客户端在窗口退休后拉取已提交历史。旧客户端忽略，新客户端遇旧服务缺字段保持原行为。Web消费保持每会话一个在途刷新，成功才推进已读取水位，失败允许后续事件重试；generation/认证变化丢弃旧结果，以messageId合并历史而不覆盖瞬态输入。需同时验证公开白名单/事件、TS、真实历史metadata消费者及旧端组合。实际实现/证据与未完成项统一见[休眠计划第17节](../plans/plan-agent-runner-durable-sleep.md#17-复审修复与验证2026-10-22)；本地Runner/Web生产消费、独立测试和CR已通过，未提交、部署或宣称真机/多天验收。

2.8的历史窗口读取面（本地Runner/Web验证完成，未部署）：复用`GET /api/sessions/{session_id}/messages`，无runner_id时沿原接口/缓存；有runner_id（≤128字符）时为原session授权下的归档窗口读取。可选before_id为默认"0"的十进制非负ID字符串（严格格式且≤INT64_MAX），0取最新页、正数取id更小的更早页，limit限定1～100。按session及runner共同过滤并直接PG读取，SQL先排不可展示tool声明，再倒序取limit+1、正序返回；涵盖control:user和child产物。响应沿原messages/failed_message_ui并加has_more:boolean（有更早展示行）、next_cursor:string（本页最早ID，空页沿输入before/0）。参数不授予执行或跨会话读取权。

Web自动刷新只更新最近100条归档展示窗口，不将全部历史回填浏览器；更早记录按页替换、返回最新重取。浏览旧页时live只更新当前Runner状态，不能冲走窗口；当前任务投影、乐观输入及停止控制保留，当前assistant按稳定投影ID定位。history_revision仅表示最新窗口更新水位，成功请求后才推进，失败保持当前窗口并可重试，不声称全历史已加载。旧客户端忽略新字段，新客户端遇旧服务缺字段沿原行为；默认接口/原历史缓存不改变。复用原MessageList/BaseButton及默认兼容followLatest，不新增历史中心、页面、模型loop或执行权限。已本地验证长期浏览器状态有界、当前输出/输入保留、旧页/最新切换、加载失败/并发与认证隔离、键盘/窄屏及真实历史metadata消费者，证据统一见休眠计划第17节；Desktop及真机Runtime组合不在本轮验收范围。

### 5.7 wecom发送对账接线（2026-10-26）

本节是原企微接线记录。WeCom CLI 1.0.0 按第 12.2～12.5 节迁移能力声明、作业、回执及续接规则，升级切换按第 12.1 节实施。

本增量版本2.9登记`wecom_send_status`业务schema v1；MCP/legacy工具集合及各自既有digest算法更新，Device v1/Host候选0.3/H3/Runner公开API major不变。唯一进度见[wecom计划](../plans/plan-wecom-cli-group-chat-experiment.md#2026-10-26-mcp与runtime发送对账接线)。主控唯一写MCP与共享core providers/providerManager及关联TS测试/本文；后端开发角色唯一写local_tools注册、代理、原发送scope及Python测试，另限域写Runner local_recovery.can_restore的status参数等价证明；既有owner/phase比较及_known不变。Runtime与Desktop计划均引用此记录，未实现/未测项不登记验收。

本地输入receipt_id仅为当前用户DPAPI证据目录标识；云端模型传send_invocation_id，由服务端从原可信wecom_message_send结果提取receipt，验证tenant/user/session/固定原device。detached hold只对服务端建立并在enqueue/claim重验的精确只读原发送scope开放，不据模型tool名/readonly/参数放行。对账结果保持独立，只读成功不等于发送confirmed；原unknown、hold、已接受release和invocation审计不修改，未知发送不重放，原Runner verification等待不自动解除。无receipt/失效来源保持原核验与权限门。初次对账仍核当前显式选机/固定binding；选B而原send在A时拒绝、需选回A，不自动换机。来源/phase在排队后失效仅收敛未started的status为failed/none并让队列继续，不改原send。该server拒绝可用result_json顶层私有version=1零派发证明恢复既有失败：status ID、Cloud send ID、business ref和完整native request/owner必须全部匹配，客户端data不可信；无标记/坏版本不享该窄恢复，旧严格source校验保留。不新增公共wire、结果outbox格式或恢复队列。

Runtime追加第7项wecom只读工具，写集合及锁/超时/许可/原结果outbox不变；旧CLI仍可用旧六项，新工具实际listTools/schema/readonly门禁不通过时明确none/升级，不静默调用未知工具或回退发送。能力缓存绑定子进程代次；signed旧release不原地改变，新版需后续显式升级。工具调用本身0积分，不替代模型实际用量计费。新增工具不是Device wire新增权限或资源停稳证明，不将本轮接线冒充长期群聊/多设备/锁屏验收。

2.9实施状态：本地MCP/Runtime/后端生产消费及独立TEST/CR已完成；PG63/TS32通过，单测173通过另2个HEAD旧BOSS基线失败保留。后端仅严格server no-dispatch证明可判该status自身物理完成，持久hold查询带齐必需字段，原send unknown/hold及全局完成码不变。真实企业微信、完整Worker受当前SDK阻断的旧场景、发行/安装及长期运行未验；没有提交或部署。全部命令、负例修复与范围统一见wecom计划最终结果，不以本条代替产品/线上验收。

## 6. 产品生命周期差异由壳配置处理

完整 Desktop 首期保持关闭最后窗口退出；独立 Runtime产品形态关窗口收至托盘。该区别是壳配置，不是两个 Runtime core。

实例明确记录由 Desktop、Runtime App 或 CLI 管理。完整 Desktop 退出/注销仅按其受管实例的策略停止领取、隔离其会话缓存与目录授权；不能自动停掉外置 Runtime App 的独立配对实例。对共享/外置实例的停止须有明确管理操作，不因观察窗口关闭而触发。

同一 runtime home/设备身份只有一个执行实例。遇到已有实例连接受控管理通道或显示冲突，不抢占、覆盖凭证或重新配对。两种产品 app identity/userData 分离，Runtime home及迁移规则共用。安装/更新一个 UI产品不得删除另一产品的设备记录或未 ACK结果。

Provider 的 Node executable、进程监管、凭证后端由平台 adapter注入。Provider 不根据 UI名称判断执行行为，普通 Node core 不 import Electron。windows交互会话是桌面操作前提；无窗口与 Session 0/锁屏操作不能混为一谈。

## 7. 兼容与变更规则

1. 本文小版本允许补充不改变旧语义的要求；移除/改义必须升 major，并在两项设计/计划登记影响与迁移。
2. wire schema采用自己的 api_version/feature协商。仅增加“可选且缺省兼容”的字段也须验证严格解析器是否接受；不能仅凭字段可选就认定兼容。
3. UI改布局、路由和 renderer实现可独立进行；如受控 Node启动或 IPC改变，由 Desktop adapter吸收，Provider和Runtime业务模块不追随改版。
4. wire破坏性变化提供新版本/显式 adapter，保留旧在途调用和恢复数据直到安全排空；不把旧 runner/release静默转换成新版。
5. 不支持的版本/特性返回明确状态；只禁用依赖该能力的功能，云端纯聊天或其他可用插件可继续。权限相关差异不得猜测兼容。
6. 每次共同接口变化必须同时更新 schema、生产/消费fixture及两项计划；未通过兼容门的实现只能留内部，不可宣布已集成。

不要以“另一个会话可能不同意”为理由阻止契约内可独立推进的工作。边界内决定由对应工作流负责；边界变更先形成可审阅差异与迁移方案，必要产品取舍再由用户决定。

## 8. 实现前共同检查点

2026-11-10，Runtime 配对协议 4 实施检查点。服务端 Local Tools 负责唯一写入票据准备绑定和设备授权，Runtime Main 持安装级租约编排，Runtime 注册存储唯一写入受保护本地候选。H1/H3、原设备鉴权与任务接口不改。协议 4 仅用于新配对，旧单步 `/runtime/pair` 固定 426 升级提示，不再写设备；已配对旧客户端仍用原 token。服务端迁移使用票据行可空 `preparation JSONB`，无旧数据回填。

| Device API 端点 | 请求 | 成功及状态 |
|---|---|---|
| `POST /runtime/pair/prepare` | `pairing_protocol=4`、一次性 `code`、注册 UUID、256-bit 候选 token、设备属性、可选原 token | 返回协议号、注册 UUID、票据 UUID `preparation_id`、随机 256-bit `preparation_token`。同一票据只有一个准备绑定，不消费码、不创建候选设备、不撤旧。 |
| `POST /runtime/pair/confirm` | `preparation_id`、`preparation_token`、注册 UUID 与候选 token，无配对码 | 返回协议号、匹配注册 UUID、已提交设备 UUID。票据仍有效、证明匹配且目标替换仍可授权时，原子消费码、创建设备、选定并撤销绑定的旧设备。 |
| `POST /runtime/pair/status` | 同 confirm，无配对码。POST 仅承载敏感请求，查询无写入。 | 固定状态 `prepared`、`committed`、`expired`、`rejected`；仅 `committed` 返回绑定设备 UUID。 |

准备凭据和候选 token 只存哈希于服务端，票据有效期同时约束准备及确认。准备绑定的目标来自登录用户票据，或同租户、同用户且活跃的原设备 token。确认、只读查询按注册 ID → 用户 → 票据顺序加锁，在锁后核实有效期，并复核替换胜者和原 token 的推断授权。请求错误使用固定原因码，敏感字段不入错误响应或日志。

Main 接收准备回执后先持久候选与证明，保留原目录指针。DPAPI v2 的 `prepared` 表示尚未发送确认，停稳失败或此时退出会清理本机候选。即将发送确认前先持久化 `confirming`，再发一次 confirm。确认结果未知时只读查 status，暂停原身份的自动和手动执行启动，重开客户端仍按同一记录判断。status 返回 `prepared` 时继续暂停，返回 `expired` 或 `rejected` 时清理候选、解除暂停。在同一 Main 进程中，若旧 Host 原来运行且用户未要求停止，解除暂停后尝试按现有启动流程恢复。`paired` 和 `installed` 表示服务端已确认并可继续本机切换。新版读取历史 DPAPI v1 已配对记录及废弃 `requested`，回退前先处理新版候选。

发布时先应用可空列迁移并完成所有 API worker 的版本切换，再开放新版客户端配对。过渡期旧 worker 仍能按单步协议写入，不能和协议 4 并行接收新配对流量。旧设备已登记的凭据继续使用原业务入口。开发验收与发布边界见[Runtime 开发计划](../plans/plan-runtime-plugin-host.md#164-服务端配对确认后续检查点)。

恢复 3A 保留 ZIP v1、文件名及旧事件字段。Core 日志投影可增量输出固定 `active`、`prior`、`desktop` 来源和分文件覆盖。Main 只从当前安装登记校验的活动及上一连接目录取证，禁止按输入路径或扫描历史目录。Core 的运行循环唯一记录 heartbeat 与 claim 的状态转折。Main 记录显式配对阶段及诊断心跳，字段仅允许固定事件、协议、原因和 HTTP 状态。ZIP manifest 的可选 `evidence` 由 Main 只读采集并标采样时间，字段限固定状态、布尔值、版本和时间。Windows 电源事件投影固定类型、时间及 Kernel-Boot 27 的已知 `bootType` 值 0、1、2。旧记录和旧投影消费方可忽略增量字段。Host H1/H3、Device API、配对和执行停稳判定保持原契约。

`bootMatchesMarker` 比较旧执行标记和当前交付 `boot-id` 取得的 Windows `BootIdentifier`，国腾现场对照到相同的 BCD 启动项 GUID。它无法标识每次开机实例。`sessionMatchesHost` 只表示标记与当前客户端所在 Windows 会话是否一致。这些布尔值供人工排查，独立于 Windows 电源事件的 `bootType`，均不放行执行。

恢复 1B 登记 Windows 直接进程证据。受信 native 辅助器是 Job 创建、停止、活动计数及退出回执的唯一生产者，Runtime 的既有 DPAPI 监督层是台账和标记的唯一写入者。新全局 Job 采用标记 v3 与台账 v2 区分本地 Job 历史；存量标记 v2、台账 v1 和受保护退出回执仍可读。新证据只在受信 home、generation、Windows 用户与 Job 对应时有效，跨会话不会把本地 Job 缺失当成零。辅助器原有本地模式保留。回退前先停稳并归档新版活动标记，旧程序不能解释新版活跃证据。恢复 1B 不改业务 API 或 H1/H3；恢复 1C 的资源 admission 与恢复 1D 的服务端任务语义分别登记后实施。

恢复 1C 登记客户端资源 admission。持 home 租约的共享 Host 唯一接替活动标记，缺停稳证据时先原样移入 `host-resource-pending-<UUID>.json`，Runtime DPAPI 监督层唯一读取这些记录与对应台账并求资源范围。待处理文件不能授予权限，只有绑定的直接进程或退出回执才能缩小范围。标记 v3、台账 v2 不变，新版 Windows SkillRunner 经固定受信 Node 入口在 Global Job 内启动 Python，原业务参数经父子 stdin 一次传递。native Global run 增加可选退出码透传，旧调用保持原退出语义；历史外部 Python 标识仍按原值读取。受限启动复用现有设备身份、心跳能力字段与结果 ACK。Main 私有 Host 启动意图区分中断续启和已确认停稳，既有 H1/H3 和 Device API schema 保持。未解决的范围记录仍在时，旧版不能解释该限制，回退前先按当前版确认资源停止。服务端旧任务结束与调度由恢复 1D 负责。

恢复 1D 登记可选 Device API 能力 `runtime_resource_admission` v1。独立 Runtime Host 是心跳 `capabilities.runtime_resource_admission` 的唯一生产者，取值 `{version: 1, scope: 'none' | 'chrome' | 'desktop'}`，与同次既有 tool 列表来自相同本机判断。普通与定向 claim、started、write-authorize 及会话 prepare-send 使用可选查询参数 `runtime_resource_admission=1` 明确启用。领取、开始和许可沿既有 admission 锁与设备锁读取认证设备的最新快照，按服务端目录约束工具和资源。`prepare-send` 仅据此拒绝重建已未知的原决策，保留既有会话授权。没有新请求参数的旧客户端即使抢在旧心跳前也按旧规则领取，旧服务端忽略新增字段与参数。H1/H3、本地标记、授权持有人、结果和计费接口不变。

新版在原任务结案时由 Runner 沿既有写入路径记录 `LOCAL_EXECUTION_INTERRUPTED`，保留原历史、计费及迟到 ACK，免业务核对或重放。引擎 checkpoint 的可选 `terminal_directive` 加入 `local_execution_interrupted`，新版仍读存量值；回退服务端 worker 前先让已持该指令的活动 checkpoint 结案。持续任务仍使用原会话决策与额度唯一写入者。

恢复 1C 会话换代复验。新版 Runtime 不接续已持久的旧 assignment，只以服务端下发的新 assignment、fence 和 input_version_base 建立新基线，不装载旧决策或 invocation。已见 task 或 fence 大于一时不启动旧开场白。仅活动进程冲突参与资源准入，旧业务历史原样保留，原结果按原 outbox/ACK 补传。SessionStore v1 字段与 Device API 保持原契约。

两份计划共用同一登记项；仅完成本文不代表下列项已完成。

每项实施记录以主导工作流的计划为唯一进度来源，另一计划引用；共同契约记录语义和兼容决议，不复制双方开发日志。

| 检查点 | 唯一主导 | 必须交付的证据 |
|---|---|---|
| H1 管理 port/DTO/错误与事件 | Runtime | 中性 schema/type、producer fixture、consumer fake；Desktop adapter验证；不依赖Electron对象 |
| H2 固定设备 binding/grant/审批 | Desktop | Web/Desktop共用目标设备与grant选择、可信网关与Runner扩展、Runtime核验；身份进入不可变摘要与恢复链；与H4统一长流程资源占用语义 |
| H3 实例监管与两产品生命周期 | Desktop | 受管/外置实例、Node路径、home/凭证、drain/退出/更新；Runtime conformance验证 |
| H4 插件登记与结果/图片 | Runtime | 受权契约/revision/schema、调用版本、设备归属artifact；提供长流程资源需求与真实仲裁证据，Desktop展示兼容 |

新增中性 wire schema建议落 `contracts/runtime-host/v1/`；在实际实现时再生成/维护类型与fixture。现有 `contracts/desktop-agent/` 属旧 D1，不能仅因名字相似当成新契约继承。测试不只比较 typecheck：必须同时使用真实生产端和真实 adapter检验生命周期与副作用边界。

架构定稿与 wire 冻结分别登记。每批接口至少明确请求/响应字段及单位、必填/缺省规则、错误码与状态、版本/能力协商、可信身份与幂等摘要、许可/超时/撤权/恢复时序，并交付生产和消费样例。接口可以先冻结 schema 再实现，但不能以本文定稿声称上述字段已经确定；样例验证也不冒充真实端到端验收。按 H1～H4 的唯一主导分批交付，不要求所有后续能力同时完成才能开发已有公开 Runner 对话或不依赖新增协议的内部模块。

### 8.1 Runtime schema / fixture 交付（候选0.3）

2026-10-09，Runtime依据用户复核更新[共享入口与消费说明](../../contracts/runtime-host/v1/README.md)。Desktop后续接线前读取该入口，不再按设计草案自行另写同名DTO。

开发前细化候选0.3：plugins.list成功result改为{instance_id, revision, plugins}，插件项可选真实version仅用于展示；旧数组需显式迁移，列表与Host状态共用revision水位及实例/旧连接隔离。0.2尚未冻结上线，本批在同目录替代，api_major/schema_version仍为1，架构v2.0及H2/Device协议不变。七项内部实施决议见[Runtime设计第12节](runtime-plugin-host-architecture-design.md#12-第一部分开发前实施决议)；H3设计输入由[Desktop第13节](desktop-agent-client-design.md#13-h3-首期设计输入runtime-ui与第一方cli)维护，设计交付不代表真实接线。候选0.2历史审阅保留，Desktop须重新消费0.3后登记结论。

- H1：[管理请求](../../contracts/runtime-host/v1/schemas/management-request.schema.json)、[响应](../../contracts/runtime-host/v1/schemas/management-response.schema.json)、[状态](../../contracts/runtime-host/v1/schemas/host-state.schema.json)、[通知](../../contracts/runtime-host/v1/schemas/host-event.schema.json)，配套[管理fixture](../../contracts/runtime-host/v1/fixtures/management.json)。
- H4首批内容：[插件契约](../../contracts/runtime-host/v1/schemas/plugin-contract.schema.json)、[安装清单](../../contracts/runtime-host/v1/schemas/plugin-inventory.schema.json)、[输出内容](../../contracts/runtime-host/v1/schemas/tool-output.schema.json)，配套插件、截图/unknown/截断及拒绝fixture。
- 可运行验证：目录内`scripts/check.py`和`tests/consumer.test.mjs`；消费fake仅供Desktop编写真实adapter参考，不是已接线的客户端。

这批有意保持最小格式，不定义H2 binding/grant/claim/许可/长占用，不替代现有Device结果与ACK，不引入另一套模型循环、事件账本或类型生成框架。沿用候选0.2的固定request_id/method/code/error/result；code为非负整数，0且error为空表示请求成功，失败code非零/error非空/result为null。管理operation也固定code/error，查询成功与operation失败分开。工具内容不带success，effect/complete独立保留。plugins.list无registration审批字段；skill有entry.description、可选output_schema及code_map（包内相对path/description），手册型允许空entries/code_map。

候选0.1/0.2未上线或冻结，当前由0.3替代；目录v1、api_major/schema_version=1保持，不能把架构2.0当成API major。旧候选回复需显式迁移/重新验证，严格schema拒绝旧形状；已有生产Device协议不改。首期签名规范化、真实Host/Desktop管理接线、进程监管及隔离验收包已有实现证据，但共同wire尚未登记冻结，H2/H4新登记与产物链仍未完成。当前实现及限制统一见[Runtime架构](runtime-plugin-host-architecture-design.md)和[Runtime计划](../plans/plan-runtime-plugin-host.md)，不沿用历史候选的待开发状态。

### 8.2 连接注册与设备替换增量（2026-10-13，实施中）

共同检查点由Runtime主控登记，保持架构2.0/H1候选0.3不变，新增注册能力修订1与Device pairing_protocol=2；不是已部署能力。唯一写入者：Runtime主控维护注册暂存/core/Main/preload及H3消费，服务端开发角色维护票据/替换持久关系和Web；独立测试与CR共同核对生产消费端。详见[恢复计划Phase13](../plans/plan-runtime-upgrade-recovery.md)。

| 接口/记录 | 本批冻结规则与兼容 |
|---|---|
| Web POST /api/local-tools/devices/{old_device_id}/replacement-ticket | 登录tenant/user校验旧设备归属，返回code/expires_at/replacement_device_id；普通票据接口不变。名称/机器指纹不创造撤销权限。 |
| POST /api/local-tools/runtime/pair | 可选pairing_protocol严格整数2；普通码旧客户端不变；替换码旧客户端明确拒绝且不消费。替换响应可选replacement_pending:true；只创建新设备/绑定，不撤销旧设备。 |
| POST /api/local-tools/runtime/replacement/complete | 新设备token鉴权，空请求/{}，只完成自身服务器绑定关系；success/replacement_completed，幂等且竞争替换不能多赢家。不得接纳请求指定oldID/租户/用户。保留旧任务事实，不自动改selected。 |
| H3可选registrationStatus() | ConnectionDiagnosis v1或null，仅投影受保护本机注册步骤，无凭据/路径，无HTTP/历史资源扫描；存在此端口才启用continue_registration。旧renderer忽略新端口，旧preload下新renderer不发新mode。 |
| H3 reconfigureConnection | v1新增register_new（地址/名称/配对码）和continue_registration+confirmed:true（不带码），仅registrationStatus能力存在时使用。旧renderer调用new_device时新Main在消费码/改状态前明确CLIENT_UPDATE_REQUIRED，不能静默改为stage；新renderer旧preload仍交旧Main处理原new_device。其余DTO字段不变，结果new_device只在活动home安装后为true。 |
| 注册修订1 | 固定home旁CurrentUser DPAPI事务/独占lease；requested无OTP，未知不自动重发；paired无claim，activation绑定同UUID/归档epoch。旧资源门用于安装/执行；active待注销失败仅重试complete，不再次pair或归档。 |

C09～C11不变：旧任务固定旧设备，不迁移/重放旧结果；停止证明与业务结案不同。pending注册阻止本产品自动/普通start和pair，stop/退出仍可用；真正旧CLI停止需资源证明。注册和凭据写入成功且资源交接后才撤销旧身份；服务不可用、身份无效、保护/权限/并发失败须明确可操作阶段，未知HTTP不能显示成功。旧服务器不支持替换接口时明确升级服务端，不降级误撤。

## 9. 兼容验收矩阵

### 2026-10-14 用户批准的干净安装增量（2.1）

本节替代第8.2节独立Runtime新版的历史资源核对激活门；旧客户端协议1/2及完整Desktop现有行为保持兼容。唯一进度来源：[干净安装计划](../plans/plan-runtime-clean-install.md)。H1候选0.3、Runner binding语义不变。安装角色唯一维护安装代际与旧进程处置，服务端角色唯一维护Device pairing_protocol=3/DDL，主控维护Main/H3自动注册及消费端。

用户明确授权安装/新配对自动停止本产品旧执行、移除旧自启、隔离/清理旧运行状态。新版只迁移有效身份配置及独立验证的兼容插件，不加载旧journal/outbox/session/管理记录决定新启动。旧业务副作用不承诺撤销，云端事实不标成功、不迁移、不重放；旧记录或删除失败不阻断新安装。旧进程须验证产品作用域和当前用户身份，不能批量终止Node或用户其他浏览器。

2026-10-14安装确认补充：安装器先只读检测；旧客户端运行中先提示“确定后自动退出并安装/取消则暂不安装”，任何停止或自启清理必须在确定之后。静默安装遇运行实例默认取消；检测后新启动实例也不能未经确认停止。卸载、新配对及普通运行时切换保持既有语义，H3/wire/Device协议不变；主控唯一维护NSIS/PowerShell，结果见干净安装计划Phase5。

协议3用受保护的新请求身份保证配对响应丢失/退出后的幂等恢复；同事务创建新设备、撤销凭原token或服务端替换票据验证的旧设备、选中新设备。名称/机器指纹不创造撤销权限，不撤销同账号其他电脑。不支持协议3时明确提示服务端升级，禁止静默退回两步pending注册。H3保留旧可选注册端口兼容，但新Runtime界面一次提交自动完成激活，不要求手动continue或历史重启恢复。

H3可选automaticConnection只由新版独立Runtime preload开启。Device `/runtime/activate {}`仅鉴权设备自身，兼容旧协议2暂存身份自动收尾。跨账号旧凭据不产生撤销权也不阻断可信新票据注册。旧授权事务门先锁device再锁任务，覆盖claim/许可/两类续租。用户本次明确批准验收包也接管当前Windows用户精确全局npm旧CLI（该旧CLI无发行profile），不扩展为终止其他用户、通用Node或另一完整Desktop；最终测试nonce另隔离真实进程/自启/安装登记。

| 情形 | 共同预期 |
|---|---|
| 新旧 UI适配同一管理 API major | 两边均可读状态，不改变业务 schema |
| UI断订阅/换页/改版 | 已接单云端 runner继续，重连可查询；无重复调用 |
| 新 UI连接外置/旧 CLI Runtime | 不抢占和重配对；明确协商能力或显示需升级 |
| 纯云端桌面会话，无插件 | 仍可正常工作 |
| 安装/升级/停用某插件 | 不替换在途 release；不影响其他插件 |
| selected改变、grant撤销、旧账号/owner | 原任务不得转移；失效执行权不能产生下一动作 |
| 写后崩溃或 ACK丢失 | 保存/核对原事实，只补传结果，不重复业务动作 |
| 未授权客户端伪造管理/设备消息 | 不获得代码执行、跨租户数据或工具结果接纳权限 |
| 新 schema超出支持范围 | 明确拒绝该能力，不静默回退旧 D1、云端执行或任意 shell |
| 本地read/edit/skill访问同名路径 | 使用同一任务环境及受权资源；不得误写服务端目录或按插件cwd猜测用户文件 |
| batch部分失败/输出截断/写后中断 | 返回实际完整性；不能静默跳过，也不能把整个批次从头重跑 |
| Desktop A / Web选择B，无A本机Runtime | 同一受权任务可在B执行；软件、文件和网络均以B为目标 |
| A选择本机目录，或打开B文件引用 | 不能建立B的grant或在A解析B路径；远端产物经受权接口显式查看/下载 |
| A退出/注销、Web关页，B仍在执行 | B不因观察入口生命周期而停止；显式撤权/取消仍按原任务规则处理 |
| BOSS长流程与其他插件/后台观察竞争 | 按声明的独占范围等待或拒绝；跨调用锁、续期及崩溃释放有实际证据 |
| 占用到期/Host崩溃，旧脚本或后台动作仍在运行 | 冲突调用保持阻断；旧owner不能操作新占用；确认停止后释放资源，业务unknown仍不重放 |
| 自报纯计算、心跳显示空闲或progress持续更新 | 不创造免锁/执行许可，不自动延长调用超时或占用期限 |
| 执行中断网、许可到期或本机撤权 | 按已冻结许可与停止策略处理；不新领或新批写动作，不能确认停止时阻断资源交接 |
| B锁屏/休眠/断网/特定网络失效 | 报告阻断或未知事实；不迁移设备，不将在线等同可交互或自动重放 |

后续代码开发按项目高风险流程执行，保留独立测试与CodeReview；协议fixture通过不替代最终Windows安装包的真实进程、Provider及结果验收。

## 10. 给新桌面会话的交接文本

> 基于当前已完成的 AgentRunner 重设计尚未开发的桌面能力。开始前读取根 AGENTS.md、`docs/system/runner-desktop-runtime-integration-contract.md`、桌面 v3设计/计划及Runtime插件宿主设计/计划。遵守共同契约v2.0（用户复核修订）：桌面负责任务工作台与公共Electron适配，Runtime负责统一任务执行环境、设备执行核心和插件生命周期；独立Runtime客户端复用同一工程。Web/Desktop可调度同机或另一电脑Runtime，目标设备固定，远端目录授权和产物保持设备归属，长GUI流程需明确资源独占；租约到期不代表旧动作已停止，断网许可和三类时限须先冻结。安装即授权/默认启用，先手册后必要源码生成能力说明与代码地图；读取候选0.3的code/error及列表水位/版本格式，旧候选需显式迁移。当前只实施A1～A4，第三方skill B1/B2暂不开发。先核对C01～C12和H1～H4，不恢复旧本地Agent/D1路线，不复制Runtime，也不单方面定义binding/管理wire协议。可以自由调整页面和交互；共同边界变化须给出版本和兼容方案。只设计未开发内容，已有成果、其他会话改动和旧设备身份保留；未获明确指令不提交或部署。

## 11. 登记记录

| 日期 | 版本 | 内容 | 状态 |
|---|---|---|---|
| 2026-10-27 | 2.11 | W10三业务工具、平台投影、continuous可信接续及WeCom原设备资源恢复；Device授权wire保持 | 开发、独测及CR通过；发行安装、真实发送和跨日运行未验 |
| 2026-10-08 | 1.0 | 建立职责、接口边界、文件责任与H1～H4共同检查点，登记到双方设计/计划和AGENTS | 文档约束建立；双方实现/契约测试未完成，未向其他会话发送消息 |
| 2026-10-08 | 1.1 | 根据用户转交的桌面定位，补充统一任务执行环境、本地批量计算、审批/计费与性能边界 | 兼容补充；新增wire schema及实现仍待H1～H4冻结，未向其他会话发送消息 |
| 2026-10-08 | 1.2 | 用户确认同机/异机Runtime，补充目标设备选择、远端grant/产物、特定网络及长流程独占 | 本批Desktop工作流唯一文档写入者：共同契约、双方设计/计划的引用与交接、ideas桌面索引；无wire/代码变更，Runtime实现未代为验收 |
| 2026-10-08 | 1.3 | Runtime侧审阅并保留v1.2方向，补充批准的资源需求、停止核对、断网许可及三类时限 | 本批Runtime工作流修改共同契约、双方设计/计划引用与交接及Runtime内部细化；仅文档核对，H1～H4实现与双向验收仍待完成 |
| 2026-10-08 | 1.3 定稿审阅 | Desktop工作流接受Runtime补充，无新增架构要求；明确架构定稿与wire冻结分开登记 | 本批Desktop仅修改共同契约、双方设计/计划的定稿状态与交接、桌面索引；版本不变，无schema/代码修改，接口及真实兼容验收待实施 |
| 2026-10-09 | 2.0 用户复核 | 用户逐条确认C01～C12及schema；取消插件额外审批，允许手册优先/必要源码的云端AI分析，统一数字code/error，候选0.2替代0.1 | Runtime唯一写入；用户产品规则确定，Desktop重新消费/真实接线与wire冻结待完成；不改变已上线Device协议 |
| 2026-10-09 | 2.0 / wire候选0.3 | 开发前七项实施决议，列表实例/revision包装与可选真实version；旧数组显式迁移 | Runtime唯一维护共享格式，独立测试/CR证据见Runtime计划第8节；Desktop须重新消费，真实接线与wire冻结未完成 |
| 2026-10-26 | 2.9 | wecom发送只读对账工具schema v1、原发送/设备scope及有限私有零派发证明 | 本地生产消费/独测/CR完成；真实群聊/发行/长期运行未验，未提交部署 |

### 2026-10-20 复审历史：恢复观察v1（2.3，未发行实现已纠正）

2.3曾将纯页面观察前置为scope登记后Windows重启，且Python/sessionTasks配置会禁用整个观察能力。复审确认这是过度门禁，相关新增见证和默认producer已删除；此前隔离测试只证明旧实现按当时规则运行，不能证明设计合理，也不能作为2.4修正验收。原已接受v1 invocation结果接纳保留兼容；后续请求统一按下方2.4。Phase9精确未决集合、缺指纹不连坐其他电脑、新意图不受历史登录错误限制等修正保留。

### 2026-10-20 复审纠正：观察与停止独立（2.4，四项修正已验证）

本记录优先于上方2.3未发行实现。唯一代码及文档写入者为恢复修复开发智能体，主控及独立测试/CR只读核对。Device claim/permit与H1/H3不变；Host观察请求/结果升级version=2、能力runtime_recovery_observe_v2，旧v1不能消费v2请求；旧回执没有代次时明确停稳未知，不推定已停止。

- 删除新增scope重启见证；纯CDP页面观察不前置Windows重启、Runtime重启或停稳证明，不因Python/sessionTasks配置禁用。
- resource_stopped与page_observed各自独立；纯观察成功不改变原业务unknown，不自动放行未知写动作。原认证迟到回执继续按已有完成判定解除自身hold。2.4未新增停稳producer，当时无回执历史hold未闭环；此阶段限制已由下方2.5设备受管边界停止路径替代。不能把新空home或页面可读当成旧执行已停。
- page_summary增加有限页面事实与match/mismatch/unknown，复用既有导航路径、可见筛选徽章及面板判定；不能核对完整原条件时保留unknown，不从计数相同推出选项相同。
- GET仅查询实际未决回执并投影入口，不修改冻结集合或审计历史；迟到明确回执与设备准入使用同一完成判定。
- 本轮四项修正已通过独立测试与CodeReview；实际范围、历史停止关联缺口及验证登记在BOSS恢复计划Phase10，不宣称资源恢复全部完成。旧v1测试不能作为本次修正通过证据。


### 2026-10-20 显式停止原设备受管资源（2.5，已完成开发）

本批唯一写入者为恢复开发智能体；主控、独立测试与CR只读。Web/内部verify-local请求可选stop_resources:boolean（默认false保留v2纯读），显式true派发version=3，独立能力runtime_recovery_stop_v1；Device claim/permit和H1/H3不变。原请求版本、模式、设备、scope及ID集合冻结于business_ref，结果仅核销本次集合，不改变原业务unknown或重放未知发送。

- 显式动作停止本Runtime/home启动的旧工具进程及旧孤儿Job，可能同时关闭由Runtime启动的浏览器；不停止其他设备或当前真实活动任务。服务端在同设备/同机实际claimed/running时拒绝；Host同桌面锁下暂停Provider新建，拒绝真实busy，关闭空闲Provider，复用原生terminate/probe逐一确认既有DPAPI Job为0，finally仅恢复临时fence，不重开永久停用的admission。Provider握手中的transport也纳入回收，原manager引用不得复活。
- 原设备配对身份、home独占lease与既有受保护Job账本构成设备级执行边界，不要求旧invocation逐一有generation；存量unknown/result_json=NULL可据此解除自身hold，原业务结果仍unknown。
- 既有私有DPAPI ledger添加可选business_started:boolean。新marker=false；受权普通BOSS Provider调度才置true，每代持久化一次，失败不保留内存成功标记；runtime_recovery_observe/boss_recovery_probe永不置true；旧无字段监督记录兼容。停止须有可信业务覆盖及非空受管Job集合，新空home先纯读产生的probe-only记录（包括重开Runtime后）不能自造历史停止证明。严格读取者同时兼容此可选字段，旧客户端需升级，不新增账本。
- 真正活跃的未受管旧实例与系统进程检查不可用分别报告。空Jobs的旧登录session不阻断；非空旧session以既有DPAPI原生停止回执核对，或核实该旧session已无进程后确认边界消失，不能仅凭当前session查不到Job放行。不凭空home、busy=false、截图或用户声明签发证明，不将重启Windows作为常规门。纯读v2始终独立可用，Python/sessionTasks配置不整体禁用。
- 版本3停止证明绑定scope/device/原ID集合/runtime_generation，证据为supervised_device_boundary、resource_scope=managed_provider_pool；消费仅限原BOSS调用，不用于未受管skill-runner/Python。页面是否观察/匹配单列，目标冲突或损坏标记page_target_unavailable，跳过页面观察，仍可提交真实stop proof；不静默换页。仅精确当前版本3显式scope可解自身资源hold。
- 版本3领取及started同时核验当前冻结scope与数据库时钟截止；过期或被新请求替代的queued stop永不执行物理回收，也不能阻塞新scope领取。迟到回执可保留历史，仅当前精确scope可改变hold。普通业务180秒与v2观察兼容语义不变。
- 冻结源码通过独立120项（native15、PG27、frontend78）及宽窄屏UI，CR无未解决P0/P1/P2。实际native Job dummy→Host→result API/PG→新意图create/claim/start已验证；跨端夹具的Runner终态不是新增真实Worker结算，不操作真实业务或重启用户系统。唯一进度与结果见BOSS恢复计划Phase10；未部署。

## 12. 契约 3.0 与首轮实施

### 12.1 适用范围与版本

本节落实 2026-11-11 确认的 N01～N13。共同契约以 3.0 登记。Runtime、BOSS CLI 与 WeCom CLI 的首轮产品发布目标均为 `1.0.0`。共同契约版本、产品版本和通信协议版本分别记录。具体接口字段和协议版本由实施阶段登记，随生产端和消费端样例一同验证。

| 首轮组合 | 适用规则 |
| --- | --- |
| 新版服务端、Runtime 1.0.0、BOSS CLI 1.0.0 | BOSS 新作业走共同契约，开放能力及内部命令由 BOSS CLI 设计确定。 |
| 新版服务端、Runtime 1.0.0、WeCom CLI 1.0.0 | 企微新作业走同一契约及调度器，开放能力及并行标记由 WeCom CLI 设计确定。 |
| 微信插件 | 排除首轮交付及验收。 |
| 已交付的旧 Runtime、BOSS CLI、WeCom CLI | 切换前按原协议收尾。1.0.0 上线后统一升级，退役旧作业协议及企微兼容入口。 |

已受理作业沿接单时绑定的设备、插件版本、契约和授权处理。版本切换前，作业按原版本执行至结束，或经 N10 取消并确认停稳，已保存回执完成交付。升级沿用有效设备配对身份。切换后新作业要求服务端、Runtime 与对应插件支持新版契约，旧版本用户收到升级提示。首轮启用顺序及验证记录见[实施计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与插件-100-实施计划待开发)。

### 12.2 职责与能力声明（N01、N02）

- Agent 负责理解用户目标，从插件向其开放的能力中选择并调用工具，根据返回结果继续处理。同一会话逐次调用，后续调用使用前次结果。AgentRunner 管理上层任务、推理、授权与用量。
- Runtime 负责通用插件安装、启停、升级、进程管理、调度、通信、状态和结果传输。新版核心按统一协议处理声明与作业。
- 插件实现完整业务，包括内部步骤、批量循环、单项失败处置、业务进度、恢复位置与汇总。插件业务可以通过既有授权接口使用云端识别、评分、入库或其他服务。

新版插件是业务能力声明的唯一生产者。声明包含插件标识与版本、开放功能的稳定标识、用途、参数、结果格式和可用状态。插件明确哪些功能面向 Agent。Runtime 校验并上报，服务端在授权范围内通用映射给 Agent。内部命令及配置专用能力按其入口使用。BOSS 和企微均自行上报同一格式的能力声明。

Runtime 安装、发现、准入、调度、调用和回传时，统一使用受信插件自身提供的身份、入口及能力声明。具体插件名称、标识、工具清单和业务规则禁止写死在 Runtime 中。发布信任配置用于验证插件身份和签名。Agent 通用工具装配直接消费授权范围内的声明。首轮以另一个已有测试发行授权的插件，从导入到 Agent 调用和回执验证通用接入。

### 12.3 作业派发、接单与关联（N03、N09、N12）

一次对外能力调用对应一个插件作业。服务端按既有身份与设备授权受理，固定作业编号、执行设备、插件版本、契约及授权范围。Runtime 把作业交给插件。插件组合所需的内部 CLI、分页和小批次。一个用户目标需要调用多项开放能力时，Agent 根据前次结果选择下一次调用；每次调用分别对应一个作业。

插件在开始执行前持久记录接单。网络重送沿用原作业编号和参数。重复送达根据已有记录返回接单确认、当前状态或已有结果，同编号参数不符时报请求冲突。原作业续接沿 N08。

`session_id` 和 `runner_id` 各自可选。服务端派发时可附带，Runtime 按原值保存，并随进度、状态、错误和结果回传。服务端按原作业核对归属并关联会话或 Runner。缺少任一字段时，按作业编号正常执行和留存。CLI 的业务参数沿插件能力声明传递。身份、文件或云端服务的访问范围沿原有授权接口核定。授权失效时停止启动新业务动作并按 N10 收尾。

### 12.4 过程结果、最终回执与故障诊断（N04～N07）

插件按原作业编号报告已取得的过程结果，作业结束时提供最终状态和汇总。插件定义进度及业务结果字段，支持部分完成的命令分别报告实际成果和失败项。Runtime 转发，Agent 展示。设备连接及进程状态作为通用运行事实报告。

Runtime 已取得最终结果时，持久保存原回执及唯一结果标识，网络失败或确认回包丢失时沿原作业与结果标识补传。服务端只记录一次，重复送达返回已接收确认。符合条件的独立作业在回执待补传期间继续调度。

Runtime 确认插件异常中断且尚未取得最终结果时，为该次中断保存唯一问题编号，关联原作业和已确认的进展。用户看到进展、可展示的错误说明和可复制的问题编号。运营与开发按权限查询同一编号关联的脱敏日志、版本、时间、最后进度及可取得的退出码或堆栈。断网时在本机保存，恢复连接后沿原编号补报。采集不到的证据标明缺失。

插件异常退出时直接报告中断。插件进程仍在、但长时间没有过程消息时，Runtime 按约定静默时限判为运行异常，结束对应受管执行并报告问题编号。静默按 Runtime 在本机接收插件过程消息的时间判断，设备心跳和 Runtime 自行发送的续租消息独立计时。确认实际停止后释放串行执行资格，其他可并行作业继续调度。具体静默及停止收尾时限在实施设计中登记。

插件按统一错误结构报告错误码、可展示说明、已确认的部分结果以及适用的问题编号。Runtime 遇插件退出、格式错误或缺少有效错误报告时，按同一结构报告可确认的通用事实。Agent 接收可展示的结果。异常堆栈、进程输出和日志进入有权限的诊断记录。

### 12.5 续接、取消与调度（N08、N10、N11）

插件声明是否支持续接，并保存自身业务进度。业务中断后由用户决定是否继续。用户明确要求继续、原授权有效且执行条件恢复后，Runtime 才转交关联原作业及进度的续接请求。是否可续及从哪里继续由插件决定。插件从已确认尚未处理的部分继续，原成果及已尝试但结果未知的记录保留。用户明确要求重做时另发新尝试。插件判断不可续接时，原作业按中断结案，其他符合条件的作业继续。BOSS 的具体继续筛选命令与参数在 BOSS CLI 独立设计中确定。

Runtime 重启后使用原身份自动恢复上线及通信，补传已经保存的原回执。服务端尚未被领取、仍有效且授权成立的排队作业继续按原记录调度。已启动作业依据原调用状态和保存的结果报告实际情况；已经中断的业务按上段由用户决定是否继续。服务端 Runner 根据原作业状态处理等待、结果或中断通知，聊天前端通过原 Runner 查询及事件通路取得状态。

取消按原作业编号转交。插件收到请求后停止开始新的业务步骤，安全收尾当前执行。插件确认作业停止或 Runtime 取得直接停稳证据后，才确认取消并释放资源。取消后保留已完成、结果未知及未执行的情况。请求到达时作业已经结束，则返回已有最终状态和结果。

对外业务 CLI 默认串行。插件提供方人工确认全程在后台完成、与串行作业无冲突的命令，可以声明可并行。Runtime 按该 CLI 的声明调度。同一 Runtime 接收的所有用户、会话及插件的串行作业共用一个队列，一次执行一项；BOSS 的对外 CLI 全部声明为串行。可并行作业直接执行，可以相互并行，也可以与一项串行作业同时执行，且不占用串行队列。串行执行资格在实际结束、取消完成或取得停稳证据后释放。受管进程停止沿原设备恢复规则验证。

并发场景以多个用户或会话向同一 Runtime 提交作业为基础。例如用户 A 正在执行 BOSS 串行作业，用户 B 提交已声明可并行的聊天记录数据库查询，该查询可以同时执行；用户 C 提交另一项串行作业，则进入同一队列等待。单个 Agent 会话按结果依赖逐次调用工具，其顺序由 Agent 的调用流程形成。`session_id` 和 `runner_id` 沿 N12 用于可选关联。

### 12.6 原条款处置

| 旧条款 | 处置 | 3.0 的适用规则 |
| --- | --- | --- |
| C01 | 保留并细化 | AgentRunner 管理上层任务、推理及用量。插件持有业务进度并决定原作业续接，见 N01、N08。 |
| C02 | 保留 | Desktop 走公开 Runner 网关，Runtime 走设备 API。 |
| C03 | 保留并细化作业边界 | 已授权派发沿现有入口，新版每次开放能力调用对应一个插件作业，见 N03、N12。 |
| C04 | 保留 | CLI、独立 Runtime 和 Desktop 共用 Runtime core。 |
| C05 | 保留并细化 | Runtime 核心只实现通用承载，插件实现完整业务并声明开放能力，见 N01、N02。 |
| C06 | 保留 | 现有设备 skill 的安装和分析规则继续适用。BOSS 作业按 N01、N12 使用授权的云端服务。 |
| C07 | 保留 | 插件安装即本机使用授权，安装、启用和就绪分别管理。 |
| C08 | 保留并细化 | 可信身份及受权绑定决定执行范围。会话和 Runner 编号用于可选关联，见 N12。 |
| C09 | 保留 | 已受理作业固定设备、插件版本、契约和授权，见 N13。 |
| C10 | 保留并细化 | 上层状态与本机执行事实分别报告，取消以实际停止为准，见 N06、N10。 |
| C11 | 保留并细化 | 已尝试且结果未知的动作结束在原尝试。原结果按唯一标识补传，见 N05、N08、N09。 |
| C12 | 保留 | 通信契约版本化，生产端与消费端共同验收，见 N13。 |

| 原机制或旧要求 | 处置 | 范围及对应条款 |
| --- | --- | --- |
| 由 Runtime 或通用服务端按 BOSS、企微名称维护工具清单、功能判断和投影 | 替代 | BOSS 和企微各自提供能力声明，通用层统一校验、映射及执行，见 N01、N02。 |
| §5.1 的批量能力与 §5.5 的跨调用资源约定 | 保留并细化职责 | 原条款未规定 Agent 必须拆分插件内部步骤。N03 明确 Agent 逐次调用开放能力，插件完成该能力内部业务。 |
| §5.5、§8 恢复 1C/1D 基于旧桌面范围及具体工具的准入 | 替代 | 新版 Runtime 按 CLI 并行声明调度，所有串行作业共用一个队列。多个用户的作业可按声明同时执行，见 N11。直接检查受管进程与停稳证据保留。 |
| §8 恢复 1D 中本机结果缺失后的原 Runner 结案 | 替代 | 新版按插件作业报告中断。插件决定是否续接，无法续接的作业按中断结案，见 N06、N08。 |
| 旧业务结果核对作为后续独立作业的放行条件 | 废弃 | 3.0 作业按实际运行及停稳事实调度。未知动作按 N08 结束在原尝试。 |
| 以历史会话或启动标识代替受管进程停稳证据 | 废弃 | 沿已完成的恢复 1B 使用直接进程证据，见 N10、N11。 |
| 原首轮范围（BOSS 新版、企微旧版兼容） | 替代 | BOSS 与企微同步迁移到 1.0.0，旧作业协议随切换退役。微信排除首轮范围，见 N13。 |

### 12.7 新增约定与实施状态

| 新增或细化的能力 | 条款 |
| --- | --- |
| 插件提供业务能力清单，通用组件发现、校验并映射 | N02 |
| 完整插件作业及统一作业编号，过程结果与部分完成汇总 | N03、N04 |
| 原结果唯一标识及重复送达只记一次，补传等待期间可继续独立作业 | N05 |
| 中断问题编号、运营查询诊断及统一错误兜底 | N06、N07 |
| 插件按自身进度续接原作业，重复派发按接单记录答复 | N08、N09 |
| 作业级取消、停稳确认及按 CLI 并行声明调度 | N10、N11 |
| 可选会话和 Runner 编号的关联与回传 | N12 |
| Runtime、BOSS CLI 与 WeCom CLI 1.0.0 首轮范围及统一升级 | N13 |

本节为已确认的实施契约。各能力的实际生效范围以对应代码、接口版本和发行验收记录为准。首轮开发与验收登记在[统一计划](../plans/plan-runtime-plugin-host.md#1610-runtime-与插件-100-实施计划待开发)。
