# 微信营销后台自动任务：技术实现方案与开发计划

日期：2026-09-08 · 状态：📋 待开发。配套：[产品/架构/UI](../../design/weixin/weixin-marketing-automation-design.md) · [当前代码调研](../../research/weixin-cli/automation-readiness-2026-09-08.md)。本文件定义目标实现，代码路径标“新增/修改”均为计划，不代表本次已实现。

## 1. 关键技术决策

1. 新建 `src/weixin_marketing/` 业务模块；复用现有 background runner、PostgreSQL、本地工具设备及 invocation 协议。旧 scheduled_tasks 的自然语言执行器不承接本场景。
2. APScheduler 只每5秒唤醒扫描器；时间事实与去重由 DB 负责，不为每个营销任务维护内存 job。固定间隔存 anchor，不受重启影响。
3. `executor_type=weixin.fixed_content.v1`，结构化有序流程：预检→解析群→准备素材→逐条发送→核验→记账。固定内容执行不调用 `master_agent.process_message_sync`。
4. Runtime 补齐多 Provider 注册与微信能力；设备执行位置为已登录 Windows 交互桌面。云端仅下发受信 operation 及 schema 参数，不下发 executable/cwd/env/raw argv。
5. 每条消息独立 delivery/attempt。非幂等写动作疑似发生后不自动重放。重试网络回执与重试微信发送是两件不同的事。
6. 所有新增业务表为 `bs_weixin_marketing_*`，必须含 tenant_id、user_id、created_at；本场景即使后台执行也要求非空可信租户与属主，不采用规则允许的 NULL 例外。

## 2. 数据模型

以下为字段级设计。id 使用 UUID，时间使用 TIMESTAMPTZ DEFAULT NOW()；JSONB 只存通过服务端 schema 验证的数据。每表均带 `(tenant_id,id)` 唯一约束，关联尽可能采用复合外键防止跨租户引用。

| 表（统一前缀 bs_weixin_marketing_） | 关键字段 | 约束/索引 |
|---|---|---|
| automations | id,name,status,active_revision_id,draft_revision_id,owner_scope,version,pause_reason | tenant/user/status/updated_at；version 乐观锁 |
| revisions | id,automation_id,revision_no,executor_type,trigger_json,policy_json,group_binding_id,content_hash,authorized_by,authorization_source,source_message_id,published_at | UNIQUE(tenant,automation,revision_no)；发布后不可变 |
| content_blocks | id,revision_id,position,kind,text_content,url,asset_id,payload_hash | UNIQUE(tenant,revision,position)；type 与字段互斥 CHECK |
| group_bindings | id,device_id,account_binding_id,label,identity_evidence_ref,identity_version,state,verified_at | tenant/user/device；label 非唯一身份 |
| account_bindings | id,device_id,account_anchor_ref,session_epoch,status,verified_at | 绑定经 probe 验证的账号依据；云端仅存受控摘要/引用 |
| schedules | id,revision_id,kind,timezone,anchor_at,next_fire_at,ends_at,accepted_count,max_occurrences | active 到期部分索引；UNIQUE(tenant,revision) |
| event_sources | id,name,source_type,key_ref,key_version,enabled,allowed_event_types | 凭据走现有 secret_crypto，不回显密钥 |
| events | id,source_id,external_event_id,event_type,occurred_at,received_at,payload_json,state,eligible_revisions_json,match_cursor | UNIQUE(tenant,source,external_event_id)；state/received_at |
| occurrences | id,automation_id,revision_id,trigger_kind,trigger_key,scheduled_for,due_at,expires_at,event_id,state | UNIQUE(tenant,automation,trigger_key)；state/due_at |
| runs | id,occurrence_id,revision_id,state,device_id,started_at,finished_at,lease_owner,lease_until,fence,error_code | UNIQUE(tenant,occurrence)；state/lease_until |
| deliveries | id,run_id,block_id,position,payload_hash,state,effect,verified_at | UNIQUE(tenant,run,position) |
| attempts | id,delivery_id,attempt_no,invocation_id,permit_id,write_phase,state,result_ref | UNIQUE(tenant,delivery,attempt_no)；invocation 唯一 |
| audit_events | id,automation_id,run_id,action,actor_type,actor_id,from_version,to_version,details_redacted | tenant/automation/created_at；追加写 |
| assets | id,storage_ref,sha256,mime,size,width,height,status,retention_until | tenant/hash 索引；引用中不可硬删 |
| outbox | id,kind,aggregate_id,dedupe_key,payload,state,available_at,lease_until,attempt_count | UNIQUE(tenant,kind,dedupe_key)；pending/available_at |
| quota_buckets | id,scope_type,scope_id,bucket_start,reserved_count,limit_value | UNIQUE(tenant,scope_type,scope_id,bucket_start) |

说明：表名中 tenant 的缩写仅用于说明，实际列统一 tenant_id。授权依据不存长段聊天记录，存来源 ID、动作范围、发布版本和最小必要快照；正文在有 ACL 的内容表，不进审计普通文本。

关键系统表扩展（通过独立增量迁移）：local_tool_invocations 增 provider_key、business_kind、business_ref、dedupe_key、deadline_at、authorization_epoch、write_phase；UNIQUE(tenant_id,business_kind,dedupe_key)。Local devices capabilities 增受信 providers 数组及各版本/digest，旧单 provider 保持可读。新增系统级本地操作许可表记录 invocation、device、claim token hash、task epoch、delivery、permit 状态/截止时间；不混入消息正文。

业务建表使用幂等 init_tables()，在现有 init_database 初始化体系和相关业务 skill 加载点接入，确保 background 不依赖用户先开聊天。已有表变更依照仓库当前 `deploy/db_update.yaml` 增量机制登记；更新数据库规则的表登记。不要仅建在开发库或机械沿用已过时的 db_update.sql 流程。

## 3. 触发接纳算法

### 3.1 时间扫描

每次 tick 处理有界批次（初始100项），短事务 `SELECT ... FOR UPDATE SKIP LOCKED` 领取到期 schedule；锁内读取 automation status/active revision，计算应接纳时间槽、迟到策略与次数限制，插入 occurrence、run 及 outbox，并前移 next_fire_at 后一起提交。人工操作与扫描器采用一致锁顺序（automation→schedule→occurrence/run），避免发布/暂停与tick死锁。

触发键：时间 `time:{revision_id}:{scheduled_for_utc}`；事件 `event:{source_id}:{external_event_id}`（同一 automation 默认跨 revision 只接纳一次同一事件）；手动 `manual:{request_id}`。采用 INSERT ON CONFLICT DO NOTHING，不依赖内存“已执行”集合。

interval 的第 n 次为 anchor+n×interval_seconds；停机后直接算最近可用槽，避免循环展开数百万次积压。cron 使用项目 APScheduler 3.x trigger 的计算能力；星期用 mon..sun 字符串，避免复制现有 cron helper 的星期数字映射差异。每月31日、闰年、DST、跨日窗口必须定例测试。

missed 落审计计数/区间摘要，宽限内最多接纳最近一次；一次性存 consumed 状态但保留 schedule 行便于对账。运行未结束又到新触发：默认 skip_overlap，该槽落 skipped，不积压无界队列。

### 3.2 事件接纳

内部业务：业务状态变更与事件 outbox 同一业务数据库事务提交。投递器写入营销 events，按唯一键去重；跨数据库场景需源侧可靠 outbox，不宣称跨库原子提交。

外部：HTTPS webhook 验证签名（成熟 HMAC-SHA256 库，覆盖时间戳、nonce、原始请求体）、±5分钟窗口与 nonce 防重放；密钥用现有 secret_crypto 管理并支持 key_id 轮换。tenant 由 source 身份解析，拒绝 payload 自报租户/用户覆盖。设置 body 大小、速率、事件类型/schema 限制；有效事件落库后202返回。重复有效事件返回已有接纳结果，不重复执行。

事件匹配 worker 按 event_type 找 active revisions，使用白名单条件 DSL，命中后事务插入 occurrence/run/outbox。events 不可在匹配完成前标 processed；大批匹配用稳定分页与 match_cursor，可重跑，依赖 occurrence 唯一键去重。先期只匹配事件接收时有效的版本集合并固定快照；配置发布不回放历史事件。延迟 due_at 默认 received_at+delay，occurred_at 仅业务条件/审计使用，避免不可信外部时钟改变队列。

事件洪峰按源速率、任务冷却和配额限流，事件和 skipped 原因持久保存；不静默把多个独立业务事件合为一个。实际事件适配器必须在对应业务完成后才出现在 UI 可选源中。

## 4. 场景执行器与并发

worker 领取 run 只占短事务，记录 lease/fence；锁外调用设备。每个状态步骤保存进度后即可退出，由下一次 tick 继续；不能在 API request 或长 DB 事务里等待整个 RPA 过程。

顺序：

1. 重验 tenant/user 活跃、功能授权、任务状态/版本、截止时间及配额；可信上下文来自持久任务，不来自模型参数。
2. 使用任务固定 device_id，不跟随用户临时“选中设备”漂移；设备离线进入 waiting_device，超过 expires_at 结束未提交条目。
3. 查设备 manifest 与账号 session_epoch；probe/群 resolve 经 local_tool queue 执行；群引用只在同一设备/账号短期内使用。
4. 按 blocks 初始化 deliveries；图片预下载并验 hash，所有内容准备成功后才开始第一条，降低半包风险。
5. 下一条只在上一条 applied 且 verified 后开始；再次读取任务 epoch、截止时间，取得发送许可，提交当前条。
6. 收到权威结果后推进状态。任何 unknown 停止后续；partial 保留每条结果。

本地操作通道抽取 `LocalInvocationService.enqueue/get/cancel`，聊天代理和场景执行器都调用它；后台不直接调用 `_dispatch_and_wait()` 私有轮询方法，不制造聊天 session 伪装执行。需要关联时保存 source_session_id 为可选审计字段。

资源锁：同一 Windows 用户的交互桌面作为串行资源（包含 BOSS/微信/其他会抢焦点的 Provider），Runtime 调度所有相关操作共用 OS 锁；云端设备锁只是第一层，本地锁是最终执行约束。防止多个 Runtime 实例并行控制同一个桌面。只读截图若占用桌面亦纳入资源仲裁。

## 5. 写动作许可、幂等与恢复

### 5.1 三层标识

- occurrence/run：业务这一次执行；重复 tick 不重复创建。
- delivery：这一次的第几条内容；hash 用于一致性，不作为跨天去重键。
- attempt/invocation：一次操作尝试；只有证明未提交或人工决定再次发送时才能新建 attempt。

### 5.2 不可消除的不确定窗口

微信 UI 与数据库不能原子提交。设计目标是“已知操作不重复、未知效果不自动重放”，允许产生待核查状态；不使用 exactly-once 描述微信发送。数据库锁/fence 不能使已离线的旧桌面进程自动失去点击能力，因此须加本地协议。

新增内部 Runtime write-authorize API（不暴露给 LLM）：在最后一次目标复验后、输入/回车前申请短期一次许可。服务器在同一事务中验证 claim/device、active revision epoch、当前 delivery 未获许可、取消状态、deadline 和配额，保守预留配额并返回 permit_id/短 deadline。多个层级quota按固定scope顺序加行锁，以条件UPDATE确保reserved_count小于limit；多项中任一不足则整个许可事务回滚。许可发出后将该 delivery 标为 may_have_started，旧租约过期也不重新分配该条。

Runtime 先将 permit 和 payload hash 持久写入本地 journal，再执行；journal fsync 失败则不发送。许可过期或网络无法取得许可时禁止开始输入；用单调时钟限制本地许可有效时间，进程重启后不复用旧许可。permit 发放和点击之间收到暂停仍可能完成这一条，产品文案必须反映边界。

发送前再次检测桌面/账号/目标/取消。输入开始后的异常可能留下草稿，即使尚未回车也不可盲重试；只有驱动明确证明未发送且草稿已安全恢复为原始状态才返回 safe_to_retry。不能清空用户原有草稿。回车或粘贴图片若可能立即提交，应在该动作前进入 may_have_started。

### 5.3 Provider/Runtime 协议补强

旧 `weixin_message_send(target_ref,text)` 保留兼容。新增受控 operation（拟名 `weixin_message_send_v2`）接收 target_ref、request_id、payload 与本地已校验 permit handle；配套 human CLI 仍为 `aid-weixin send`，对象只用参数表达。新 schema 通过 probe 后才注册 manifest。

Provider 本地以 request_id 防重，保存 prepared/may_have_started/verified/unknown 和结果。重复 request_id：已完成返回既有回执；may_have_started/unknown 不重新点击；prepared 且确认无副作用才允许恢复。新 API 不允许调用方凭 request_id 获得任意目标的权限。

driver 通过受限 stdin JSON 接收正文，不使用进程命令行明文参数；DRIVER_JSON 增加 phase、effect、safe_to_retry、evidence_ref。所有提交可能发生后的崩溃、缺输出、截屏/模型失败均映射 unknown，不能漏为 retryable INTERNAL_ERROR。取消/shutdown 分支未拿到可信结果时写工具一律 unknown。

Runtime 把终态先写本地 result outbox，再经原 invocation/claim 身份重复回传；收到持久化 ACK 才删除。云端迟到证据端点只追加审计并进入对账，验证原 device/claim/request_id/hash，不能重新授权发送。重复回执不产生重复账单。租约过期先停新发送许可，原 started 操作按 unknown 处理，不重新派发。

### 5.4 状态优先级

run 聚合规则：全部 delivery verified/applied 为 succeeded；任一 unknown 时若已有成功则 partial，否则 unknown；全部未提交且取消为 cancelled；部分已发送后截止则 partial，剩余条目标 expired；纯等待超时为 expired。attempt 原始结果与人工业务判定分别存储。

服务端、Runtime 和 UI 统一按 effect/phase 解释，不仅按 success/code：unknown 优先于 success；partial 表示本轮部分副作用；applied 还必须有本次验证证据才成功。协议矛盾落 unknown/protocol_error。旧 BOSS 行为的兼容分支单独测试，不在本功能中误改其恢复策略。

| 结果/故障 | 本条动作 | 后续 |
|---|---|---|
| 提交前设备离线/忙 | bounded backoff，到期停止 | 不产生新已发送记录 |
| ref过期且尚无输入 | 同设备重新resolve，最多2次 | 重新核验账号和群 |
| 身份不明/账号变化 | blocked | 暂停任务，重新绑定 |
| 明确effect=none且safe_to_retry | 最多2次，截止时间内 | 同delivery新attempt |
| applied并验证通过 | succeeded | 按顺序下一条 |
| unknown/提交后断网/崩溃 | 待核对，不重发 | 停止本包后续 |
| 用户取消 | 停止尚未开始的条目 | 已提交结果照实回传 |
| 回执网络失败 | 只重传outbox | 不再调用发送工具 |

人工核对追加 decision，原始 effect 不覆盖。若用户确认未发送并授权重试，建立带 predecessor_attempt_id 的新 attempt；先核验旧本地进程已停止、没有待执行许可，再允许操作。仅确认已发送可将 delivery 业务判定为 resolved_applied，但保留机器 unknown 标记。

## 6. 群身份与图片实现

### 6.1 账号/群身份门禁

新增 account probe 与 group resolve/bind 能力：使用微信界面能够可靠读取的账号标识作为 anchor（具体可用字段须 Windows probe 证明），与 Windows 用户/设备/session_epoch 绑定；不能把昵称或进程PID当永久账号身份。

短期 ref v2 绑定 account anchor 摘要、session epoch、群绑定身份版本、类型、有效期，保护使用项目既有本机 DPAPI/密钥机制，拒绝跨账号、跨机、过期和篡改。优先本地随机 handle 映射，避免 v1 base64 payload 泄露群名；不扩展自制加密。

群搜索要求完整候选集合/明确截断标志、exact group 与唯一性，发送 driver 不再用 best match 或子串标题比较。可接受的群标题规范化仅包含经过验证的 UI 人数后缀等，不能剥离任意后缀。缺少独立群身份时采用可识别唯一名称＋重新验证的受限模式；同名或依据变化即阻断。若实验不能得到足够可靠的账号和群身份依据，保持人工试发，停止无人值守发布门禁，不伪造 fingerprint 解决。

写后证据：发前记录底部区域，发后核验本次新增自方消息、正确会话、正文/图像匹配、输入恢复为空且无发送失败标志。相同正文重复发送作为必测项，视觉无法辨别新增时返回 unknown。证据只能证明客户端显示已发出，不证明对端已读。

### 6.2 图片素材通道

Web 上传到现有租户存储根目录，经 MIME 实测/解码、大小/像素限制、hash 计算；不接受任意服务器路径。素材发布后不可变，ACL 为租户＋属主/明确共享权限。保存版本引用期间不能删除；过期无引用素材由后台清理。

Runtime 使用设备身份和 invocation scope 调用素材下载接口（拟 `/api/local-tools/runtime/invocations/{id}/assets/{asset_id}`），只取该任务已绑定资源；禁止任意 URL 和重定向下载。云端返回受控字节流、hash、mime，短期授权不以长效 token 拼 URL。下载校验后进入受控本地缓存，路径只在本地由 handle 解析。

图片发送先做独立 lab/probe：验证图片格式剪贴板/粘贴是否作为图片气泡、是否出现预览/确认、Enter 行为及草稿保护；探明前不得硬写实现承诺。操作期间保存并安全恢复可支持的剪贴板格式，不能恢复则告知/阻断，不上传用户原剪贴板。核验新增自方图片气泡、加载结束、无失败图标，结合本次前后截图和视觉内容匹配；本地源hash用于素材完整性，不与微信压缩结果作字节相等比较。

截图与视觉响应使用每 invocation 随机隔离目录，文件 ACL/DPAPI按既有规范；默认短期诊断保留24小时，业务记录建议90天（租户可调，开发前核对全局留存策略）。最小裁剪到必要窗口区域，复用当前视觉代理，但需记录该步骤模型消耗。诊断导出必须脱敏。

## 7. 后端 API 与工具

业务前缀拟 `/api/weixin-marketing`。复用统一 auth/tenant middleware，user_id/tenant_id 从认证上下文取，列表与写入 SQL 同时限制租户/属主或显式共享 ACL；猜别人的ID统一404。FastAPI调用同步DB必须 asyncio.to_thread。

| 方法/路径 | 行为 |
|---|---|
| GET /automations | keyword/status/trigger_type，分页page/page_size≤100 |
| POST /automations | 创建草稿，Idempotency-Key；无发送副作用 |
| GET /automations/{id} | 配置、版本、权限、最近结果摘要 |
| PUT /automations/{id}/draft | If-Match/version更新，冲突409 |
| POST /automations/{id}/validate | 静态校验/允许的只读预检，返回字段错误、能力缺口、未来5次；无发送 |
| POST /automations/{id}/publish | 指定draft revision/version发布；事务校验授权和预检有效性 |
| POST /automations/{id}/pause、/resume、/archive | 版本CAS＋epoch递增＋撤销未开始的工作 |
| POST /automations/{id}/run | 独立手动run，指定已发布revision，返回202/run_id |
| POST /automations/{id}/test-send | 显式指定block_id/绑定群，只试发该条，独立审计/配额 |
| GET /runs、/runs/{id} | 分页运行记录、逐条delivery和脱敏证据引用 |
| POST /runs/{id}/cancel | 请求停止尚未提交条目，202 |
| POST /deliveries/{id}/resolve | 人工结论＋证据说明；重试需单独action明确授权 |
| POST /deliveries/{id}/retry | 校验可重试状态/显式人工决定，新attempt |
| GET /devices、POST /devices/{id}/preflight | 授权设备能力、账号与交互状态，读操作也经队列 |
| POST /group-searches、GET /group-searches/{id} | 异步搜索候选，不占长HTTP连接 |
| POST /group-bindings、POST /group-bindings/{id}/verify | 绑定/核验，要求同一设备账号和完整候选证据 |
| POST /assets、GET /assets/{id} | 上传/取授权元数据与预览；删除受引用检查 |
| GET/POST /event-sources | 查看/配置事件源，凭据掩码，不泄漏旧密钥 |
| POST /event-sources/{id}/rotate-key | 轮换凭据，需管理权限，旧新短窗口并行 |
| POST /webhooks/{source_id} | 独立签名认证接收，持久接纳后202 |

响应沿项目 envelope（success/data 或 error），错误使用稳定 code＋field_errors；语义错误422、权限403、跨域ID404、冲突409、配额429、执行能力缺失409。所有会创建run/attempt/publish的POST使用Idempotency-Key，scope=(tenant,user,route,key)，同key异payload返回409；保证并发双击也只写一次。

聊天工具新增 `weixin_automation_prepare/publish/manage`，prepare与publish分离并共用服务；InputModel只接业务字段，不接tenant、raw argv。按 Catalog/Assembly与subagent allowlist接入，在 `subagents/weixin-marketing/SUBAGENT.md` 声明固定内容规则、授权边界和unknown处理。通用 create_scheduled_task 对确定的微信后台任务应路由到专用工具，禁止先执行旧dry_run。

## 8. 前端实施清单

新增 `frontend/web/api/weixinMarketing.ts`；组件建议 `components/weixinMarketing/{AutomationList,AutomationEditor,TriggerEditor,ContentBlockEditor,MessagePreview,GroupBindingPicker,RunDetail,AssetLibrary,EventSourceSettings}.vue`。复用现有设备连接入口，若需微信多Provider展示则局部扩展 localTools API/设备页。

类型：TriggerConfig 判别联合（once/interval/calendar/event）；ContentBlock 判别联合（text/link/image）；RunState/DeliveryState强类型。后端schema是权威，前端使用共享生成类型或契约快照避免重复漂移。路由、page_metadata planned→developing→published、Agent业务页面授权同步实施。

编辑表单维护baseVersion/draft/dirty，服务端409时保留用户输入并显示差异；离开/关闭走useModalCloseGuard。不自动保存到浏览器长期明文存储。图片上传并发与大小有上限，取消上传可回收孤儿资源。

列表5秒轮询仅页面可见时进行；运行详情2秒直到终态再停止，退避网络错误，卸载/切路由用AbortController取消。刷新列表不覆盖编辑草稿；必要时以后增加授权SSE，不依赖聊天SSE。一次测试发送按钮显示目标和条目，提交中禁用但服务端仍幂等。

按产品文档线框实现全状态、键盘操作、窄屏和视觉检查；新页面用Base*与语义token，不从旧ScheduledTasks复制原生表格。此次仅提供线框/交互规范，未制作或验收可运行Vue原型。

## 9. 安全、计费与运维

设备pair token与weixin视觉代理激活token是不同链路，预检确保归同租户；不得互相冒充。正文不进命令行/普通日志，临时截图不提交仓库。签名/加密复用成熟库、DPAPI和现有secret_crypto，不另造算法。

计费复用现有视觉网关，固定内容不产生对话模型调用，但定位/校验可能计费；若增加业务按次费用，应独立price配置和唯一ledger键，避免Runtime result与视觉调用重复记同一费用。不指定新价格，默认不额外添加营销按次费；unknown的真实视觉成本保留，未确认发送不计成功发送费。

上线配置建议命名：weixin_marketing.enabled、time_triggers_enabled、event_triggers_enabled、images_enabled、max_blocks、max_interval_frequency、dispatch_batch_size、retention_days、tenant_allowlist。新系统启动先迁移DB，再就绪检查；DB不可用停止接纳和许可发放，Redis故障不能绕过DB去重。

可观测指标：due_to_start延迟、waiting_device、unknown、目标身份阻断、图像验证失败、journal/outbox积压、重复事件/触发拦截、各设备单条耗时与视觉费用。容量按设备串行实测耗时计算；云端多worker不增加单微信桌面吞吐。不承诺未压测并发数字。

## 10. 开发阶段与验收

按项目非平凡开发流程实施，每阶段开发后独立测试与独立CodeReview；本次纯设计文档不触发三智能体开发流程。以下为工程估算，1人日按实际团队工作量口径，非交付保证。

| 阶段 | 交付 | 依赖/门禁 | 估算人日 |
|---|---|---|---|
| P0 真机验证 | 账号/群身份、相同文字新增证据、图片probe、平台基线 | Windows与授权测试群；身份不可靠则不启无人值守 | 3–5 |
| P1 执行基础 | Provider注册/微信manifest、许可、journal/outbox、effect规范、素材传输 | 不破坏BOSS回归，真实断网/崩溃语义 | 5–8 |
| P2 时间闭环 | DB/API/调度/文字网址/逐条账本/取消配额 | 假时钟与崩溃恢复，旧dry_run不可触发 | 4–6 |
| P3 工作台与Agent | 列表编辑、群设备、预览、记录、聊天工具 | UI与API共用服务，权限/版本并发 | 4–6 |
| P4 完整MVP | 图片产品化、内部事件适配示例与签名webhook、有序内容包 | 图片真机通过、事件重复/洪峰/循环测试 | 5–8 |
| P5 发布 | 独立测试/CR修复、安装升级、灰度/回滚 | 全部关键门禁通过 | 3–5 |

合计约24–38人日，存在P0不确定性；可并行部分前端与后端，但不通过并行省掉P0/P1门禁。原生Mac driver另估：P0先做2–3人日可行性验证，再按证据排期，未验证前不承诺日期。

### 验收矩阵

| 范围 | 必测场景/预期 |
|---|---|
| 定时 | once只一次；interval重启不漂移；周/月/DST、宽限、错过多次、时间窗口一致 |
| 多worker | 并发tick同一槽只一个occurrence/run；DB失败无半个触发 |
| 事件 | 重复/乱序/无签名/过期/大payload/租户伪造/分页重启；不漏已接纳事件、不重复发 |
| 授权 | 跨租户ID/素材/设备拒绝；属主禁用、任务暂停、版本切换在许可前生效 |
| 目标 | 同名群、好友同名、账号切换、标题截断、群改名、候选被截断均不盲发 |
| 文字 | 中文/英文/emoji、500边界、换行拒绝、同内容相邻运行不能误判旧气泡 |
| 图片 | PNG/JPEG、超限/损坏/伪mime/hash不符、剪贴板/预览弹框/压缩、取消与上传失败 |
| 组合 | 第1成功第2unknown第3不发；恢复不重放第1；hash/顺序/版本冻结 |
| 故障 | journal失败不发；permit后崩溃unknown；回车后断网仅补回执；旧进程/租约不能重派 |
| 暂停 | 未许可不开始；已提交结果回收；UI明确当前条不可撤回 |
| 真机 | 支持微信版本、DPI/主题/单双屏、前台抢占、锁屏、休眠恢复、输入残留草稿 |
| UI | 加载/空/错/禁用/409/上传中、键盘排序/脏检测、移动端、轮询不覆盖草稿 |
| 回归 | BOSS运行时与manifest兼容、通用定时任务不变、工具装配和租户存储边界 |

P0 可在已授权「哈尼」群只发一条例如“自动任务发送测试：这是一条测试消息。”，前提是唯一确认目标且Windows执行入口可用；本次未实际执行。压力/矩阵发送使用专用测试群和另行明确范围，离线故障测试优先fake driver。

发布检查：迁移/空库启动、background重启、Runtime安装配对、微信manifest匹配、受限租户端到端、观察至少完整调度周期。放量先单设备/单群，再小租户；发现错群或自动重复立即停用。

回滚：先关闭新触发并撤销新许可，再停止尚未提交动作、收集在途回执；unknown保留，不清队列后重发。不删业务表/审计/journal，不将新任务降级交给旧prompt scheduler。新协议任务只派给支持版本，旧Runtime继续原BOSS链路；数据库变更先兼容增量、后清理。

## 11. 本轮交付边界

已完成源码研究、完整产品/架构/UI设计和本实施计划，并在 ideas.md 登记待开发。未修改业务代码、未提交Git、未创建后台自动任务、未实测发送。实际开发开始时更新索引为部分完成；P0–P5验收完成才移入ideas_finished.md。
