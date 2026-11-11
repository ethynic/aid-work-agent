# Master 源码实录：Runtime 的插件安装、MCP 和云端辅助调用

源码基线为 2026-11-11 拉取的 `origin/master` `574aed3ccbdce6ae35b1b5008e1087dab33de820`。[总览与 Device API](runtime-master-communication-20261111.md) · [端侧会话任务与企微后台](runtime-master-session-communication-20261111.md)

## 1. 管理 IPC 与进程边界

Electron Main 用受信 Node 入口、已选 runtime home 和私有进程 IPC 启动 `managed-entry`。独立 Runtime 和完整 Desktop 经同一 Host 核心，产品身份及窗口生命周期在壳侧决定。CLI 直接打开 Host。Host 按 home 持有实例 lease，执行前写 marker；初始化失败时独立 Runtime 可提供只读诊断。Main 按当前子进程 generation 匹配答复，页面按 Host `instance_id` 与 `revision` 应用状态；连到新实例时重读状态和清单。

H1 请求为 `{request_id,method,params}`，答复为 `{request_id,method,code,error,result}`，核心 `describe` 返回 `api_major=1`、Host 版本、features、实例与 revision。Host 分开投影执行状态 `stopped/starting/running/draining/reconciling/blocked` 和连接状态 `unpaired/connecting/online/offline/revoked`。只读方法有 `describe`、`getState`、`plugins.list`、`operations.get`。写方法有 `start`、`stop`、旧 `pair` 和 `plugins.import/enable/disable/uninstall`。Main 与直属子进程另有受控 `runtime_connection_*`、`runtime_shutdown_*`、`runtime_platform_*` 帧。管理面无业务 `invoke`、任意命令或任意路径执行方法。

写请求带 `request_key`。同键的 method 与参数计算 HMAC，意图一致返回已有 operation，不一致拒绝。Host 先持久保存 operation，再串行执行，终态可查询。离线包先由 Main 的本地文件选择交付临时引用，Host 按实例和请求键取已冻结快照，校验类型、长度、SHA-256 后持久化自己的一份 ZIP 与引用。IPC 或页面重试共用原 operation。重启时未能证明已结束的管理动作进入 reconciling，已结束的快照回收，未决快照留存。

源码见[页面控制器](../../frontend/desktop/features/runtime/controller.ts)、[Electron 监管](../../clients/agent-desktop/electron/runtimeSupervisor.ts)、[文件选择快照](../../clients/agent-desktop/electron/runtimeSelection.ts)、[受管入口](../../clients/agent-tool-runtime/src/managed-entry.ts)、[共享管理实现](../../clients/shared/local-tool-host-core/src/management.ts)、[实例 lease](../../clients/shared/local-tool-host-core/src/instanceLease.ts)。

## 2. 离线发布包、安装与入口解析

| 环节 | 当前校验或状态 |
| --- | --- |
| 产品信任 | 受管入口相对固定路径加载 `config/runtime-product.json` 与 `runtime/trust-roots.json`。检查 productKind、profile、schemaVersion、文件边界、公钥与允许的 Provider。production profile 拒绝 test-only 根。CLI 无对应产品资源时没有受管安装权限。 |
| 外层 ZIP | 恰好 `envelope.json` 与 `payload.zip`，前者 ≤64 KiB。只允许 `ai.aidwork.boss-recruiting`、`ai.aidwork.weixin`、`ai.aidwork.wecom`。envelope v1 的 provider/release/version、平台、Runtime 版本窗、包长度/摘要、manifest 摘要、发布时间等字段均在签名覆盖范围。按 publisher key ID + Provider 选受信 Ed25519 公钥，验 canonical JSON 去签名字段后的字节。 |
| 内层 ZIP | 核 SHA-256 和长度，拒绝目录穿越、非规范路径、链接与重复路径等非法项目。通用上限为 ZIP 512 MiB、展开 2 GiB、单文件 256 MiB、50,000 条、压缩比 1,000；外层读取另有两项和载荷特化限制。 |
| 完整 manifest | `runtime-manifest.json`（v1），canonical JSON SHA-256 与 envelope 比较。校验 provider 与版本、`execution_target=local_required`、`[已登记 .js 文件,"mcp","--stdio"]`、平台架构、Node 精确版本和 modules ABI。除 manifest 自身，每个发行文件都必须被 `required_files` 列出并匹配长度和 SHA-256。 |
| 解包与探测 | 解到新 UUID release 目录，保存原 `archive.zip` 和展开的 `payload/`，逐文件核实际展开内容后才启动只读 probe。固定 Node 调 `version --json` 与 `doctor --json`，各 5 秒、2 MiB 输出。版本报告与 manifest 的 provider、version、protocol、transport、platforms、entrypoint、tools、schema_digest、execution_target 精确匹配。doctor 的退出码、success 和 `runtime_readiness.ready` 决定 ready，用户未登录等可以 `installed/enabled` 且 `ready=false`。 |
| 提交与恢复 | `plugins/pending.json` 记录操作意图，原子替换 `plugins/inventory.json` 是活动版本提交点。启动重读 inventory，再验存档和展开文件；遗留 pending 尝试清理，原已提交 inventory 继续有效。记录含 installation_id、plugin_id、release_id、version、内部目录、包/manifest 摘要、enabled、uninstalled。列表仅投影用户可用摘要及 readiness。 |
| 启停、修复、回退 | 安装默认 enabled，活动升级继承原 enabled。相同 release ID 的已知不同内容拒绝；同一可信内容、原展开损坏可解到新目录并切换。可信不同 release 可显式重新导入，版本窗继续生效。切换前 Host 停止领取并排空普通与会话执行，关闭 Provider pool，变更后恢复此前运行状态。保留当前及本次上一个目录供现场核对，按受控路径清理其他可回收 release。 |
| Legacy 入口 | 本机 `config.providers[key].entry` 提供显式旧入口，BOSS 的 `bossCliEntry` 优先。插件 store 中该 Provider 的记录覆盖旧配置，包括禁用/卸载屏蔽回退。通过完整性验证且 enabled 的已装 Provider 提供入口，doctor 的 ready 不作为入口的全局门。缺合法 manifest 或路径时停止声明该入口。 |

源树版本为 BOSS 0.3.30、WeCom 0.2.10、Weixin 0.1.0。签名包的 release ID、schema 摘要和产品 profile 仍需针对具体包核验。签名包身份、已提交的本机路径、MCP 工具 schema 和心跳 capability 是独立层。标准 claim body 没有签名 release ID/摘要，执行器用当前 Host 的 Provider 池；维护排空保护已接受动作，原回执补投也不重新进入插件。源码未提供靠旧历史 invocation 自动选择任意历史插件包重跑的通道。

源码见[包格式与验签](../../clients/shared/local-tool-host-core/src/plugins/package.ts)、[本地插件 store](../../clients/shared/local-tool-host-core/src/plugins/store.ts)、[产品信任](../../clients/agent-tool-runtime/src/productTrust.ts)、[版本与就绪探测](../../clients/agent-tool-runtime/src/pluginProbe.ts)、[打包脚本](../../clients/runtime-plugin-packaging/package.mjs)。

## 3. Runtime ↔ CLI 的 MCP 调用

1. `ProviderSet` 按 Provider key 缓存 `ProviderManager`。首个调用或 WeCom 能力发现启动进程，多方同时启动共用一个 promise。Host 使用固定 Node 执行本机入口 `mcp --stdio`；受管 Windows 环境可改由固定本机 supervisor 包裹这一命令。包校验与旧入口配置决定可执行文件，云端只传 provider/tool/arguments。
2. Host 用 MCP SDK `StdioClientTransport` 和 `Client`（客户端标识 `agent-tool-runtime`）连接，插件用 `McpServer` 与 `StdioServerTransport`。SDK 完成初始化，后续 `tools/list`、`tools/call` 和 `notifications/progress` 走子进程 stdin/stdout 的 JSON-RPC；stdout 留给协议，stderr 单独转发 Runtime 日志。接收 buffer 默认 128 MiB，manager 可配置为 1～128 MiB；传输解析错误记录固定类别和预算。
3. `tools/call` 参数为 `{name,arguments}`。默认超时 10 分钟，进度可重置调用超时，Runtime 传入可中止 signal，v2 写还叠加许可剩余时间。CLI 从 `TOOL_DEFS` 的 Zod schema 和 annotations 注册 MCP tool，在操作注册表取 handler。BOSS 内部 target/contact 引用从 arguments 解出再进执行 context。通知用 SDK 的 progressToken，字段是 progress/total/message，Runtime 将语义进度和续租分别处理后一起回传 Device API。
4. Host 锁的默认等待为 120 秒，Windows 上以按 `hostname|username|sessionId` 哈希命名的管道持有；sessionId 来自显式参数或 `AIDWORK_DESKTOP_SESSION_ID`，默认空。不同云端 device ID 不会分裂这把本机锁。Manager 拒并发调用，CLI 自己还做进程内单飞，微信/企微 MCP 另有跨进程 NamedMutex，覆盖独立 CLI/MCP 使用。
5. SDK 收到 transport close 标记进程崩溃。下一次新调用回收旧 client/transport、清除能力缓存并重启进程。原调用的错误根据写/读和是否有可信结果映射；manager 不在新进程重做原调用。关闭先停止接单、等 idle，再关 stdio、等待退出，必要时结束 PID，无法确认回收报错。Windows supervisor 另外用 Job 管理进程树。

见[ProviderManager / ProviderSet](../../clients/shared/local-tool-host-core/src/legacy/providerManager.ts)、[BOSS MCP](../../clients/boss-resume-assistant/src/mcp/server.ts)、[WeCom MCP](../../clients/wecom-cli/src/mcp/server.ts)、[Weixin MCP](../../clients/weixin-cli/src/mcp/server.ts)、[桌面锁](../../clients/shared/local-tool-host-core/src/legacy/desktopLock.ts)。

## 4. 能力发现与兼容

云端 catalog 控制可派发工具，模型投影控制模型可见工具，Host 静态 manifest 决定是否受理及写分类，CLI 的实际 `tools/list` 决定其 schema 能力。四者各自有消费点。旧设备的 `provider_id` 可以回退解析，已配置的原工具按原可见性路径保留。已装插件的 `ready=false` 可以继续给出可用工具入口和可读诊断。

| Provider | Host 受信集合与实际差异 |
| --- | --- |
| `boss-recruiting` | 静态 25 项，含 Host 专用 `boss_recovery_probe`。完整清单如下。云端 catalog 另有 `boss_jobs_list`、`boss_interview_notify` 这些云端业务工具，分别处理查职位和云端通知。普通 `boss_list_jobs` 则由本机执行。 |
| `wecom` | 静态 12 项，协议 1。普通 Agent 公开 `wecom_read_session`、`wecom_message_send`、`wecom_read_sessions`，有实际搜索能力时可加目录搜索。页面和后台可受信调用目录、doctor、calibrate、只读对账等。CLI 另提供聊天搜索/切换、watch、群列表等工具，Host 不通过安装包动态扩大静态集合。 |
| `weixin` | 静态默认六项 `weixin_probe/name_resolve/chat_search/message_send/history_read/unread_list`（均带 `weixin_` 前缀），其中旧 send 是写。源码中的真实 CLI 已注册 `weixin_session_observe` 与 `weixin_message_send_v2`。Host 仍只在本地 `providers.weixin.v2Send=true` 时追加两项、用协议 2 和共享锁能力；标准云端目录含 v2 send，普通队列排除 v1 send 和 observe。会话 engine 直接通过 manager 调 observe。 |
| `runtime-recovery` | Host 进程内 `runtime_recovery_observe`，无安装包。检查 BOSS probe 文件或受管 WeCom 执行资源，按能力上报观察、WeCom 和停止支持。 |
| `skill-runner` | 旧进程内 `skill_script_run` handler，解释器显式配置并存在才上报，统一作写动作。另见 §8。 |

BOSS 的 25 项工具（`*` 表示 Host 写集合）：

- 浏览与恢复：`boss_recovery_probe`、`boss_goto`、`boss_open_chat`、`boss_read_chat`、`boss_overlay_inspect`、`boss_overlay_dismiss`。
- 职位和筛选：`boss_list_jobs`、`boss_select_job*`、`boss_filter_options`、`boss_filter*`、`boss_clear_filter*`、`boss_lookup_candidates`。
- 简历与详情：`boss_resume_detail*`、`boss_resume_batch*`、`boss_resume_next*`、`boss_open_detail`、`boss_close_detail`、`boss_accept_resume*`、`boss_reject_current*`。
- 沟通：`boss_greet*`、`boss_greet_selected*`、`boss_greet_detail*`、`boss_send_to*`、`boss_send_current*`、`boss_interview_demo*`。

WeCom 的 12 项工具（`*` 表示 Host 写集合）为 `wecom_probe`、`wecom_message_send*`、`wecom_send_status`、`wecom_group_poll`、`wecom_send_image*`、`wecom_send_file*`、`wecom_read_session`、`wecom_read_sessions`、`wecom_session_directory`、`wecom_config_doctor`、`wecom_config_calibrate*`、`wecom_unread_list`。写分类由 Host 静态集合决定，例如 calibrate 会切换会话并保存标定。

| 真实 schema 检查 | 何时以及核对什么 |
| --- | --- |
| WeCom 现代单会话读/发、批量读 | 心跳并行触发单飞 `listTools`（5 秒），缓存按进程代次。读查 `session:string`，发送查 `session/text:string`，批读查 `sessions:string[]`、`cursor:string`、`limit:integer`。业务三项完整才加批读能力。现代参数调用前再以同份发现结果检查相应工具。发现暂时失败回 `PROVIDER_NOT_AVAILABLE/none` 可重试，实际缺能力回 `TOOL_NOT_SUPPORTED/none`。旧字段调用仍走原路径。 |
| WeCom 目录、附件、配置 | 目录检查只读注解、mode/session/sessions 类型；搜索另查 mode 枚举 search、query、type 枚举 contact/group/any 和整数 limit，产生额外的 `wecom_session_directory_search` 能力。附件内容读取检查 `attachment.message_id` 整数 schema，缺失只拒该形态。doctor 要只读空参 object，calibrate 检查工具存在。 |
| WeCom 群与对账 | 群 poll 检查 required `target_name`、cursor 字符串和只读。群 cursor 发送检查 required `text`、字符串 `group_cursor/target_name` 和写注解。对账检查仅必填字符串 `receipt_id` 与只读注解。缺支持在 `tools/call` 前结束，回 `TOOL_NOT_ALLOWED/none`。 |
| BOSS 安全续筛 | `boss_resume_next` 调用前查 MCP schema，要求 `exclude_names` 数组、`expected_job_name` 字符串且两者为 required。缺支持直接回 `TOOL_NOT_ALLOWED/none`。 |

Provider 的 `manifest_digest` 按 Provider ID、排序后的工具名和执行目标计算。WeCom 摘要对已探测的新工具过滤，设备心跳的顶层 `manifest_digest` 仍兼容 BOSS 历史静态摘要。插件发布包另有工具 schema digest 和签名 envelope 的完整 manifest digest，三种摘要所覆盖的字节各不相同。

见[本机受信名单](../../clients/shared/local-tool-host-core/src/legacy/providers.ts)、[实际发现和预检](../../clients/shared/local-tool-host-core/src/legacy/providerManager.ts#L244)、[能力上报](../../clients/shared/local-tool-host-core/src/legacy/config.ts)、[云端 catalog](../../src/local_tools/catalog.py)、[WeCom toolDefs](../../clients/wecom-cli/src/mcp/toolDefs.ts)、[Weixin toolDefs](../../clients/weixin-cli/src/mcp/toolDefs.ts)。

## 5. 插件返回与结果解释

三套 MCP server 都返回 `structuredContent` 及一份 JSON text content。Host 先查 MCP `isError`，为 true 时不采信正文。存在 structured 字段时严格验它，畸形不会回退 text；没有时在 text blocks 中只接受恰好一个可验证 JSON OperationResult。检查包括布尔 success、受控大写 code 且与 OK 一致、字符串 message、允许的 effect、对象或 null 的 data、非空 run_id，以及 retryable 或具合法 phase 的 safe_to_retry。空、多份有效结果或畸形统一回保守 `INTERNAL_ERROR/unknown`，诊断仅记录固定类别与块计数/长度。

v1 MCP OperationResult 支持 `none/applied/partial/unknown`。成功、可重试和物理效果分别使用字段。Device API v1 的 success 和 code 决定主状态，effect 继续单独透传；v2 受控回执另外核定 phase、证据和许可。只有回到云端获得 ACK 才能结案对应的回传。CLI 中的业务证据（例如 WeCom 本地发送记录）仍由业务域验证。

见[回执解析](../../clients/shared/local-tool-host-core/src/legacy/providerResult.ts)、[Device 回执构造](../../clients/shared/local-tool-host-core/src/legacy/invocationRunner.ts)、[v2 回执判定](../../src/local_tools/operation_result.py)。

## 6. 环境、文件与实际执行资源

- Host 构造基础环境白名单，如 Windows 系统目录、PATH、临时目录和当前用户目录。受信 BOSS、WeCom 在内存环境注入配对服务地址和解密后的设备凭据。WeCom 另保留给旧插件的 `AID_WECOM_SERVER_URL/TOKEN` 兼容透传。Weixin 只从此层获得每次 Host 启动新生成的桥接密钥，用于名称会话签名。安装 probe 用基础环境，不发业务请求。
- Windows 启动标记和受保护 ledger 按 home、Host generation、系统 boot ID、会话、owner PID 与 Job 记录。启动器核 owner 启动时间，创建挂起进程、纳入 Job 后恢复，受控资源退出有保护回执。Host 当前持有 home lease、桌面锁且 pool 空闲时，才做原受管资源核验/停止。对原会话、旧未受管进程、账本损坏等保守拒绝；旧无证明情形可要求确认重启。业务停止证明与发送结果对账各有证据。
- BOSS 在 CLI 内经 CDP 操作本机 Chrome 与页面 target，进展由 MCP handler 上报。WeCom 读会话通常进入本机数据库访问、账号和数据库代际校验，发送用 Windows 驱动执行并按本地记录验证，坐标缓存和本机证据由 CLI 管理。Weixin 使用本机窗口、OCR、键鼠与签名目标引用。插件以登录用户权限执行，包签名、工作目录与进程树监管提供各自边界。
- Runtime home 保存身份、管理操作、插件 release、执行证据及可选会话日志；各插件另有自己的本地可变状态和 artifacts，例如 `%LOCALAPPDATA%/AidWorkAgent/wecom-cli/` 与 `weixin-cli/`。服务端收到的是 MCP 回执投影或特定上传/下载通道，结果中的本机路径字段本身不会上传文件。
- Runtime 日志写当前 home 的 `logs/runtime.log`，插件 stderr 经 Runtime 转发，供按 invocation、tool、Provider run_id 和阶段耗时对照。独立客户端也有 Desktop 侧生命周期和 IPC 诊断。日志、业务回执和未提交 outbox 有不同的读取/保留口径。

见[Host 环境组合](../../clients/agent-tool-runtime/src/runtimeHost.ts#L220)、[进程监管](../../clients/agent-tool-runtime/src/processSupervision.ts)、[Windows 启动器](../../clients/agent-tool-runtime/native/RuntimeProcessSupervisor.cs)、[Runtime 日志](../../clients/shared/local-tool-host-core/src/legacy/log.ts)、[Desktop 日志](../../clients/agent-desktop/electron/runtimeLogs.ts)、[WeCom 发送证据](../../clients/wecom-cli/src/platform/sendReceipt.ts)、[WeCom 本地库入口](../../clients/wecom-cli/src/platform/localdb/access.ts)、[Weixin 目标引用](../../clients/weixin-cli/src/platform/targetRef.ts)。

## 7. 插件直接调用服务端

| 入口 | 实际调用和服务端逻辑 |
| --- | --- |
| `POST /api/local-tools/runtime/boss/visual-diagnose` | BOSS CLI 的 `visualDiagnoseClient` 读 Host 注入的地址和设备凭据，上传观测截图及摘要、`request_id`、API version 及操作上下文；v2 要求结构化上下文，`retrieve_only` 可用原请求 ID 查回执。服务端从设备取主体并核租户、用户有效性（平台管理员按配对租户规则），调用共享视觉诊断。处理中 202 + `Retry-After: 2`，网络未知时沿原 ID 只查回执。新诊断按实际视觉用量 ×20，旧回执和费率沿原记录。 |
| `POST /api/local-tools/runtime/wecom/calibrate` | 企微驱动 `_send-points.ps1` 用设备 Bearer 发送 `{image}`，60 秒超时。服务端只收 PNG data URI，核 1400 万 base64 字符、10 MiB 解码字节及 PNG 文件头，取设备主体、校验有效性和余额。固定 GLM-5.3-Flash、双点提示词、temperature 0、max_tokens 4000 与 max reasoning effort。返回 content、model/provider、usage、billing，沿客户端 LLM 台账的标准用量 ×10 记费。驱动解析整数坐标，结合本人会话标题和窗口校验后保存本机缓存。旧 `/api/client/v1/llm/chat` 仍为旧客户端入口。 |
| `POST /api/local-tools/runtime/resume/evaluate` | 保留的设备鉴权接口，收拼接图或分段图、候选人、可选职位及允许的视觉模型。切分长图后评估并确定性核姓名，成功按份记 `boss_resume_recognition`，返回摘要、评分和关键信息。当前普通 BOSS 简历路径由 CLI 通过 MCP 回传图和业务事实，云端 proxy/持久 `resume_flow` 调评估服务。此基线未发现普通 Host 或已接线 BOSS MCP handler 直接调用该 HTTP 路由。 |

见[BOSS 视觉客户端](../../clients/boss-resume-assistant/src/main/boss/visualDiagnoseClient.ts)、[WeCom 标定驱动](../../clients/wecom-cli/drivers/ps1/_send-points.ps1)、[设备辅助 API](../../src/local_tools/api.py#L521)、[云端简历流程](../../src/local_tools/resume_flow.py)、[客户端用量入口](../../src/api/client_routes.py)。

## 8. 旧本地脚本路径

`skill-runner` 与第一方 ZIP/MCP 共用 Device API、桌面锁和结果通道，执行形式是 Runtime 内部 `SkillRunnerHandler`。云端 skill 路由使用已批准的设备执行配置与 entry；Runtime 发现本机 `SKILL.md`、计算执行摘要，能力最多上报 50 项 `{name,hash}`。实际调用校验 `skill/entry/args/exec_hash/timeout_seconds`、本地批准入口、文件范围与真实路径。args 最多 16 个、单项 500 字符、总 4000 字符，默认 900 秒、硬顶 1800 秒。固定本地 Python，无 shell，回传有界 stdout/stderr（20 万/6.4 万字符）和受控效果。此路径可用于已配置的旧设备技能；第三方技能安装/AI 分析/动态登记仍为共同契约中预留设计。

见[服务端 skill proxy](../../src/local_tools/skill_runner_proxy.py)、[执行门禁及进程处理](../../clients/shared/local-tool-host-core/src/legacy/skillRunner.ts)、[服务端 skill 路由](../../src/core/skill_plugin_gate.py)。
