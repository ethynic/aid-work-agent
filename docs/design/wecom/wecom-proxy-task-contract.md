# 企业微信代理任务跨模块契约（P1）

状态：**P1 已冻结（2026-11-08 编码基线）。经设计者三轮评审定稿：首轮五项冲突与 Q1–Q6 裁决落地；二轮修订 needs_reply 分流、停止标志检查点与批次输入快照；终轮删除 `send_effect` 转述字段，规则 3/4 以"发送调用事实＋原回执"分流（无调用或全部证明未发送→重试一次；有调用且任一次无法证明未发送→只读对账）。后续变更须升 schema_version 并登记兼容影响。**
依据：[设计 §9](wecom-conversation-operations-design.md#9-企业微信代理任务新消息触发回答2026-11-08设计待开发)、[开发计划](../../plans/plan-wecom-proxy-event-driven.md)、[共同契约 2.17](../../system/runner-desktop-runtime-integration-contract.md)。
本文件只冻结跨模块输入/输出 schema、能力协商、身份边界、失败语义与生产消费样例；模块内部实现由 P2–P7 各自细化，不在本文展开。所有新业务结构带 `schema_version:1`，且均为 MCP `structuredContent` 外层 `OperationResult`（`success/code/message/effect/data/retryable/run_id`）中 **`data` 字段内的业务报告**；本文样例直接展示 `data` 内容，外层沿现有 OperationResult 语义不再重复。旧客户端缺新能力只影响代理任务对应环节，不改原 Device wire 字段结构或全局最低版本。

## 1. 唯一写入者与身份边界

| 事实 | 唯一写入者/生产者 | 边界 |
|---|---|---|
| 配置检查/标定受信 invocation | 服务端代理任务 API（创建检查、编辑加入水位读取） | 仅受信配置流程可发起；不加入 Agent 工具集，模型不可见（同 D1 `wecom_session_directory` 页面专用先例） |
| 监控读受信 invocation | 服务端代理监控循环 | `wecom_read_sessions` 只读调用；固定任务＋设备＋企微账号；用户保存授权下的读，不扩大授权 |
| `wb1` 游标、已处理位置、批次事实 | 服务端 `bs_wecom_proxy_*` 表 | CLI 只返回当次读取游标；服务端是进度唯一权威，崩溃后从持久事实恢复 |
| 批次结束 JSON | 代理回答 Runner 产出、服务端核定后落库 | 服务端按 §5 规则核定推进/重试；@我事实按监控读取消息核定，不信任模型自报 |
| 企微消息发送 | Runner 内 Agent 经现有 `wecom_message_send` | 沿原发送核验、`unknown` 不自动重发语义；无新发送通道 |

调用身份：tenant/user 来自认证；设备为任务配置固定的 Runtime 设备（不是页面"当前选定设备"）；企微账号为创建时核定的账号。LLM、prompt、自由 request_data 不创造权限（共同契约 C08）。代理 Runner 的执行身份由服务端从持久任务行核定（§6），不经用户请求令牌。

## 2. CLI 配置检查工具契约（P2 落点）

新增两个 wecom provider 受信 MCP 工具，与既有 `aid-wecom doctor --localdb` / `aid-wecom calibrate` 子命令同逻辑、同实现，只补结构化本地入口；**不加入 Agent 模型工具集**，不改原发送、读取、OCR 边界与三业务工具 schema。

### 2.1 `wecom_config_doctor` v1

- 输入：无参数（固定执行 localdb 深检语义）。
- 结果：`OperationResult.data` 内复用现有 doctor `--localdb` 报告形状（`DoctorReport`＋`LocaldbDoctorReport`）加性字段：

```json
{
  "schema_version": 1,
  "self_user_id": "1688xxxx",
  "success": true,
  "checks": [{"name": "…", "ok": true, "severity": "gate|info", "detail": "…"}],
  "runtime_readiness": {"ready": true, "reason": "…"},
  "localdb": {
    "success": true,
    "steps": [{"step": 1, "name": "…", "ok": true, "kind": null, "detail": "…", "elapsed_ms": 120}],
    "localdb_ready": true,
    "mode_decision": {"mode": "db", "kind": null, "version": "…", "checked_at": "…"},
    "ui_ready": {"ready": true, "platform_ok": true, "interactive_session": true, "powershell_available": true},
    "state_saved": true,
    "guidance": ["…"]
  }
}
```

- 保留现有诊断字段（含 `state_saved`、`guidance`、`steps`）；`state_saved=false` 不单独否定就绪，但须在页面提示"结论未落本机状态"。
- `self_user_id`：identity probe 可确认时返回当前登录账号，**是创建页账号的核定值**；不可确认时缺省，创建检查不得通过。创建检查通过时该值必须与后续标定、目录来源账号一致。
- **就绪判定（服务端消费规则，全部满足才算通过创建检查）**：`data.success===true && data.localdb.localdb_ready===true && data.localdb.mode_decision.mode==='db' && data.runtime_readiness.ready===true && data.self_user_id 非空`。CLI 退出码不进入 invocation 通路，退出 0 不代表就绪（深检结论 ui 也退出 0）；任何单项不满足须向页面返回具体不满足项。
- 检查未完成（引导阶段失败）时仍返回结构化报告（`success=false`＋steps＋guidance），不用 invocation 错误替代诊断内容；invocation 级错误仅用于传输/身份异常，沿现有 OperationResult：`IDENTITY_UNCONFIRMED`、`LOCALDB_UNAVAILABLE`、`VERSION_UNSUPPORTED`、`INTERNAL_ERROR`、`CANCELLED`。

### 2.2 `wecom_config_calibrate` v1

- 输入：无参数。执行与 `aid-wecom calibrate` 相同的身份预核＋双点标定（独占运行，走 CLI 既有互斥）。
- 真机创建检查必须能访问标定服务。按共同契约 2.18，新版 Desktop Runtime 从现有配对配置取得服务端地址和设备凭据，注入受信 WeCom Provider；CLI 通过设备鉴权的专用标定 API 请求 GLM。用户无需另配服务域名或直连 Token。旧已交付插件的 Runtime 旧变量透传和服务端原路径均保留；标定失败仍不创建任务。
- 结果：`OperationResult.data` 内：

```json
{
  "schema_version": 1,
  "self_user_id": "1688xxxx",
  "title_point": {"x": 1310, "y": 218},
  "input_point": {"x": 1310, "y": 976},
  "width": 2560, "height": 1440, "dpi": 96
}
```

- 驱动原始结果中的本机 `cache_path` 等**不进入契约字段**：仅存本机诊断，云端不读、页面不展示为契约内容。
- 身份预核失败返回 `IDENTITY_UNCONFIRMED`（现有行为）；驱动失败返回 `INTERNAL_ERROR`。坐标为整数像素。
- 创建流程必须真实执行 2.1 与 2.2 并双双通过；"插件已安装/能力已上报"不等于检查成功。

## 3. Runtime 能力上报与受信调用（P3 落点）

- **能力字符串＝MCP 工具名**（沿用现有粒度对齐约定）：`wecom_config_doctor`、`wecom_config_calibrate` 纳入 wecom manifest 工具名单；`deviceCapabilities` 在真实检查插件 MCP listTools 后上报对应能力串——`wecom_config_doctor` 须 schema 校验通过（`readOnlyHint=true` 且无参输入），`wecom_config_calibrate` 从实际 listTools **确认工具存在即上报**（存在性检查；P4 创建检查会真实执行该工具并核对坐标字段，安全兜底）。插件已安装但工具缺失/被裁剪 → 不上报对应能力串；能力上报不使配置工具进入模型工具集。**修改 P2 配置工具的输入格式时，须同步更新 Runtime 能力判断、后台调用与对应测试。**
- 心跳能力数组为加性元素，Device wire 无字段结构变化；manifest digest 按实际工具集合变化属既有协商机制的正常演进，旧客户端按旧 digest 正常运行，不强制升级。
- 两个配置工具与监控读共用**原 local-tool invocation/result 通路**（Device API → Runtime → MCP → CLI），不新建通道、不建本地 Agent loop、不让浏览器直连 CLI。
- **创建检查与启动运行条件分别定义**：
  - 创建检查（新任务保存时）：真实执行 §2.1＋§2.2，双双通过才允许保存。缺配置工具能力 → 仅新任务创建被明确拒绝并提示"客户端需升级"。
  - 启动运行条件（任务启动时）：核对固定设备在线、`self_user_id` 与账号一致、监控读取/历史读取/发送能力（均为既有工具能力）。**不要求配置工具能力**；已存在任务不因运行环境后来缺少创建专用配置能力而失去运行路径。后续坐标缓存缺失由现有 CLI 发送路径按原逻辑重标定，不额外阻断启动。
- 监控读受信调用语义：单次 invocation 只传一个稳定会话 `wecom_read_sessions({sessions:["ws1.…"], cursor?, limit?})`，逐目标独立 `wb1` 游标、按 `has_more` 排空分页；读取结果按现有 D1/W30 已冻结语义消费（`sessions`/`failed`/`cursor`/`has_more`/`resolved_targets`/`source_generation`），本文不重复冻结消息结构。监控读不推进目录、不触发旁路自动登记。

## 4. 后台数据与状态 schema（P4 落点，逻辑字段级；DDL/迁移/初始化 SQL 在 P4 按数据库规范产出）

| 表 | 字段（逻辑） | 说明 |
|---|---|---|
| `bs_wecom_proxy_tasks` | id, tenant_id, user_id, profile_id, instance_id, device_id, wecom_account_id, self_user_id, title, agent_id, instruction, status(`stopped`\|`running`\|`deleted`), stop_requested, revision, created_at, updated_at, deleted_at | Runtime/账号/智能体创建后固定，换任何一项须另建任务；`stop_requested=true` 持久表达"已请求停止、当前批仍在执行"，重启后不得派新批，当前批结束或进入等待后落 `stopped`；软删除仅可从已停止（无执行中、无待恢复派发）到达 |
| `bs_wecom_proxy_task_sessions` | id, task_id, session(ws1. 稳定 locator), web_session_id, source_generation, wb1_cursor(可空=未建基线), baseline_origin(`initial`\|`edit_join_read`), processed_position, latest_seen_msg_id, idle_read_count（2026-11-08 评审补充，加性）, last_checked_at（2026-11-08 评审补充，加性：最近一次实际监控读取时刻，仅真实读取写入，新建/核定不写）, status(`active`\|`halted`\|`removed`), halted_reason, halted_at, created_at, updated_at | 一任务多目标，一工作项一会话；每目标独立游标；`web_session_id` 按任务＋目标固定，新任务不复用旧智能体会话历史；`halted`＝零发送重试再失败、源库代际/游标失效等停住待人工，其他会话继续，页面展示 `halted_reason`；**`idle_read_count`（2026-11-08 评审补充，加性）＝空闲读取轮次计数**：监控读无候选仅推进游标的同事务 `idle_read_count=idle_read_count+1`（零回答 Runner、零模型请求的监控轮），列表按任务 SUM 展示，不做费用依据（监控读定价 0 积分） |
| `bs_wecom_proxy_batches` | id, task_id, session, batch_end_msg_id, input_snapshot JSONB, agent_id, instruction_snapshot, web_session_id, runner_id, status(`pending`\|`running`\|`waiting`\|`reconcile_hold`\|`succeeded`\|`failed`\|`ended_by_removal`\|`ended_by_delete`), submit_text TEXT, end_json JSONB, cli_error, retry_count, credit_cost（2026-11-08 评审补充，加性）, created_at, updated_at, started_at, finished_at | 批次表即持久队列（一行一工作项，`pending` 即排队项），**不另建队列表**；`waiting`＝Runner 持久等待、任务运行中可自动恢复重排；`reconcile_hold`＝发送 unknown 待对账，**不可自动恢复**，与 `waiting` 严格区分。**`input_snapshot`＝本批入站消息原文与身份**（msg_id、send_time、sender_id/sender_name/sender_platform、text、mentions、mentions_complete），建批时从已持久化的受信监控读回执写入；游标前移或服务端重启后，派发与恢复 Runner 均从该快照取得本批内容，不重读最新页猜测内容。**`submit_text`（2026-11-08 评审补充，加性）＝持久化提交意图**：首次派发在 HTTP 提交前构建（含本批之前的近期往来段，best-effort）并落库，Runner 提交受理后回写 runner_id 前服务端重启时，孤儿重提原样使用持久化文本——不重读最新页、不重渲染，同幂等键重试意图逐字节一致（幂等 digest 覆盖 text，漂移即 IDEMPOTENCY_INPUT_MISMATCH）。**`credit_cost`（2026-11-08 评审补充，加性）＝本批回答积分累计**：核定落库时取被核定 Runner 终态公共结果 `public_result.credit_cost`（finalizer 结算写入 `agent_runners.result`，权威仍在 chat_records），跨重试在同一批次行累加，列表按任务 SUM 展示；中断未结算（无终态 result）不写不猜 0 |

**位置三事实**（不可混用）：① `wb1_cursor`＋`source_generation`＝下次监控读起点与源库代际（编辑加入水位读取同样保存二者）；② `processed_position`＝已确认处理到的消息位置（推进语义见 §5）；③ `latest_seen_msg_id`＝最近一次实际读取观测的库内最新位置。派发批次时冻结 `batch_end_msg_id`。

**状态机要点**：创建后 `stopped`，首次启动为 `initial` 目标建基线（当前水位，历史不算新事件）；`running` 期间拒绝一切编辑；停止＝置 `stop_requested`，等当前 Runner 结束或进入 `waiting` 后任务落 `stopped`；停止期间可改标题/会话/指令，新增目标在编辑加入时由编辑 API 经同一受信读取能力一次性读取记水位（`baseline_origin='edit_join_read'`，保存 `wb1_cursor`＋`source_generation`＋当时水位，后续启动不得覆盖；该读取与监控循环共用同设备/账号串行占用）；软删除或移除目标使对应未决批次（含 `waiting`、`reconcile_hold` 中的未发送批次）转 `ended_by_*`，不得再恢复发送。并发启动仅允许同账号同稳定会话一个 `running` 归属，冲突在启动时明确报错。

## 5. 批次结束 JSON v1 与核定规则（P6 落点）

Runner 正常结束输出（服务端核定后存 `end_json`，页面据此展示）：

```json
{
  "schema_version": 1,
  "batch_id": "bpb_…",
  "task_id": "…", "session": "ws1.…", "runner_id": "runner_…",
  "batch_end_msg_id": 100,
  "needs_reply": true,
  "replies": [{
    "inbound_msg_ids": [97, 99],
    "text": "…",
    "receipt_id": "…",
    "verification": "local-record",
    "verification_detail": {"method": "local-record", "message_id": 110, "source_generation": "…"}
  }],
  "unanswered": [{"msg_id": 98, "reason": "send_failed", "cli_error": {"code": "…", "invocation_id": "…"}}]
}
```

- 字段来源：`receipt_id`/`verification`/`verification_detail.message_id`/`source_generation` 直接引用现有发送回执实际字段（`verification='local-record'`），页面文案仅称"本机客户端已记录发送"；`unanswered` 记原消息 ID、原因、CLI 错误码与调用/日志编号。**结束 JSON 不含发送状态转述字段**：Agent 只做"回复／不回复"二元决定与成功/未成功罗列，规则 3/4 的分流由后台依据**本批发送工具调用事实与已保存的原 CLI 发送回执**判定，不经 Agent 转述。非 JSON、缺必需字段记批次失败，不猜成功。
- **明确 @我是服务端核定事实，不是 Runner 自报**：服务端按本批监控读取消息的 `mentions`/`mentions_complete` 事实核定该批是否存在明确 @当前账号（`mentions_complete=false` 时不仅凭缺 @ 标记判定无人提及，结合原文交由智能体判断，不因此卡批）；核定结果不依赖结束 JSON 的自报字段。
- **服务端核定规则（按序判定）**：
  1. **正常无需回复**：`needs_reply===false`、`replies` 为空、零发送，且服务端核定本批无明确 @我 → 推进 `processed_position` 到 `batch_end_msg_id`，正常结束。`needs_reply===true` 的零发送批次不适用本条，落入规则 3 或 4。
  2. **至少一条确认发送即推进**：`replies` 中至少一条 `verification==='local-record'` → 推进本批上界；其余回复失败或仍为 `unknown` 只在 `unanswered` 展示，不阻塞推进；`unknown` 仅禁止**对该条发送**自动重发。此为设计 §9.3"个别未处理可丢失"规则，不以全批无 unknown 为推进前提。
  3. **重试一次**：不满足 1/2、零发送，且**本批没有发送工具调用，或所有发送调用的原回执都明确证明未执行发送动作**（明确失败、无副作用；含 @我门槛拒绝情形）→ 自动重试一次（新 `runner_id`、同批次冻结上界、指令快照与输入快照）；再失败该会话转 `halted` 停住并告知用户，其他会话继续。
  4. **只读对账、不重发**：本批**发生过发送调用，且有任何一次无法证明未执行发送动作**（含 `unknown`、`partial` 等结果不明或部分不明），且无任何已确认发送 → 批次转 `reconcile_hold` 只读对账；不自动重发、不推进，等待人工处理。
  5. **@我窄门槛**：仅拒绝"服务端核定的明确 @我＋全批无需回复＋零发送"作为正常结束（落入规则 3 重试路径）；**不设逐条 @ 门槛**，部分漏答由 `unanswered` 如实展示。

## 6. 受信 Runner 提交身份契约（P6 落点，本轮评审新增冻结）

- **受信 source：新增内部来源 `wecom_proxy`**（P1 冻结名称）。现有 `chat` 需用户 Bearer token、`scheduled` 为零历史，均不适配；`wecom_proxy` 由服务端内部提交，无用户请求令牌，**非零历史**——按固定 `web_session_id` 加载该会话历史＋活动摘要（区别于 `scheduled` 的 `zero_history` 语义）。
- **身份核定**：tenant/user 从 `bs_wecom_proxy_tasks` 持久行核定（创建时已认证固定），不经请求参数或模型上下文传入；模型用量与积分按该 tenant/user 记账（与 chat 同语义）。
- **Web session 登记与恢复**：`web_session_id` 由服务端在任务首次启动建基线时登记（复用现有 Web 会话存储语义）；跨批次、服务端重启、`waiting` 恢复均沿用同一 `web_session_id`；新任务新会话，不复用旧智能体的会话历史。
- **核对检查点（区分两处）**：`stop_requested` **仅在新批派发与 `waiting` 恢复排队时**核对为 `false`，停止请求不阻断已运行批次；已运行批次（含每次企微工具派发时）仅核对任务未软删、目标仍 `active`、智能体仍有权限，允许其完成至正常结束或进入持久等待。失权即停任务、保留位置与日志，恢复后由用户手动启动。
- 任务指令经内部 `prompt_augmentations` 追加（现有 Web 网关不接受该字段，属内部受信路径既有机制，不新增 wire）。每批新 `runner_id`；批次创建时冻结 `instruction_snapshot`/`agent_id`/`batch_end_msg_id`，等待恢复沿旧快照，编辑后新指令只用于新批次。
- 工具装配：仅装配租户有权且该任务选定智能体的工具集＋企微读发（企微调用限定当前目标会话），不改智能体共享 profile；业务提示词不当设备/会话授权，工具调用仍按原 tenant/user、设备与目标校验。

## 7. 生产/消费可核对样例（CLI → Runtime → 后台）

1. **CLI（P2）**：`wecom_config_doctor` 的 `OperationResult.data` 展示 §2.1 结构（`mode='db'`＋`localdb_ready=true`＋`self_user_id` 非空＝通过样例；`mode='ui'` 且其余通过＝创建被拒并显示具体项样例）；`wecom_config_calibrate` 返回 §2.2 双点坐标样例与 `IDENTITY_UNCONFIRMED` 失败样例（外层 `code` 非 `OK`）。
2. **Runtime（P3）**：心跳 `capabilities` 含两个新能力串样例（新版插件）与缺失样例（旧版插件，其余能力串不变）；`wecom_config_doctor` invocation 请求/结果经原 Device 通路的样例；启动运行条件核对不要求配置工具能力的样例（旧客户端上已存在任务可启动）。
3. **后台（P4–P6）**：创建落库样例（任务＋两目标）；首次启动建基线样例（`wb1_cursor`＋`source_generation` 置当前水位、历史不计新事件）；停止请求样例（`stop_requested=true` 期间不派新批、**已运行批次不受阻断**、当前批结束后落 `stopped`）；监控读发现新 peer 消息 → 建 `pending` 批次（冻结上界＋**`input_snapshot` 写入本批消息原文与身份**）→ 派发 Runner → `end_json` 落库 → `processed_position` 推进样例；服务端重启后从 `input_snapshot` 恢复同批内容样例（游标已前移仍不重读最新页）；消息 100/101–109/本人 110 位置样例（推进只到 100，下批从 101 起）；"部分回复 unknown 但有一条 local-record 确认→仍推进"样例；**`needs_reply=true` 零发送、回执证明未发送→重试一次→`halted`** 与 **回执无法证明未发送（unknown/partial）→`reconcile_hold`** 的分流样例（两者不得互混）；软删除仅在停止后执行样例；编辑加入目标保存 `wb1_cursor`＋代际＋水位样例。

## 8. 兼容与回退

- 本契约全部为**加性**能力：Device wire 仅能力数组新增元素；invocation/result、binding、claim/permit、ACK、恢复与计费通路不变；不提高旧客户端全局最低版本。
- 旧 Runtime＋旧 CLI（缺两个配置工具能力串）：仅**新任务创建检查**不可用并给出具体原因；已存在任务的启动、运行、恢复不受影响（启动只核对 §3 运行条件）。原三业务工具、页面目录、发送对账、任务恢复、结果上报、计费全部保持。发布兼容验证覆盖"只升级服务端、客户机保持旧版本"组合。
- 回退：服务端关闭代理功能入口**并停止后台派发**——监控循环不再发起监控读、不再建批/派发；已存在任务按既定停止语义处理：执行中 Runner 跑完或进入 `waiting` 后任务落 `stopped`，`waiting`/`reconcile_hold` 批次保留、不自动恢复发送，功能恢复后经用户"启动"再排队。无 schema 回滚需求。

## 9. 决议记录（2026-11-08 设计者评审裁决）

| # | 决议点 | 裁决 |
|---|---|---|
| Q1 | 监控循环宿主 | **现有 `background_runner` 进程**内按设备＋账号运行逻辑监控循环；"专属监控服务"指内部职责，不是新部署单元 |
| Q2 | doctor 账号核定 | doctor 返回 `self_user_id`（`data.self_user_id`）作为创建时当前登录账号核定值；创建检查通过时必须非空，且与标定、目录来源账号一致 |
| Q3 | 编辑加入水位读取 | **编辑 API 经同一受信读取能力发起一次性读取**（停止期间无运行监控循环），共用设备串行占用；须保存返回的 `wb1_cursor`、源库代际和当时水位——仅 `latest_seen_msg_id` 不足以保证启动后从编辑时点继续读；无需新 CLI 工具或独立通道 |
| Q4 | 队列载体 | 批次表即回答队列，不建独立队列表；监控读与 Runner 的互斥由同设备/账号队列占用规则保证 |
| Q5 | 能力串命名 | 工具名即能力串；能力上报不使配置工具进入模型工具集 |
| Q6 | 配置工具 schema 形状 | 复用现有 doctor/calibrate 结果加字段；按真实 `OperationResult.data` 结构呈现样例，不另造精简格式 |
