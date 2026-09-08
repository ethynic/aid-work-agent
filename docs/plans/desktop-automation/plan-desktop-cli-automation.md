# 桌面 CLI 无人值守自动任务底座：实施计划

2026-09-08 · 📋 待开发。关联：[底座设计](../../design/desktop-automation/desktop-cli-automation-design.md) · [微信实施与 BOSS 衔接](../weixin/plan-weixin-marketing-automation.md) · [调研](../../research/weixin-cli/automation-readiness-2026-09-08.md)。所有模块、表与 API 变更都是计划。

## 1. 模块归位与兼容约束

src/desktop_automation/ 管事件/调度骨架、运行账本与额度；src/local_tools/ 扩展 LocalInvocationService、write-authorize、回执与设备鉴权；Runtime 承载 OS 锁、journal/outbox、多 Provider。微信和 BOSS 各自保留业务配置、身份、内容与决策。

通用新增核心表使用 desktop_automation_* 系统表名；本地通道扩展既有 local_tool_invocations，并拟增 local_tool_operation_permits。所有这些任务记录仍要求非空 tenant_id、user_id、created_at；租户复合键/外键、ACL 与可信后台上下文不因成为系统表而放宽。真正实现时登记 database_system_table.md；业务表登记按数据库规则进行，本轮不把拟建表登记为已存在。

## 2. 原微信表族逐表归属裁决

原计划所有 bs_weixin_marketing_* 均在下表裁决；“上移”是拟建表设计调整，没有真实迁移已存在数据。通用表不存场景正文、群名、简历内容，只保存受控引用、摘要和中立状态。

| 原后缀 | 归属 / 目标表 | 原因与关键约束 |
|---|---|---|
| automations | 保留 bs_weixin_marketing_automations | 微信业务任务状态、active/draft revision、version |
| revisions | 保留 bs_weixin_marketing_revisions | 微信触发/内容/目标与授权快照，发布不可变 |
| content_blocks | 保留 bs_weixin_marketing_content_blocks | 有序 text/link/image、UNIQUE(tenant_id,revision_id,position) |
| group_bindings | 保留 bs_weixin_marketing_group_bindings | 微信身份规则，不上移群字段 |
| account_bindings | 保留 bs_weixin_marketing_account_bindings | 微信 account anchor；底座仅引用 account/session 摘要 |
| schedules | 上移 desktop_automation_schedules | 通用 kind/timezone/anchor/next_fire/ends/count；场景持有触发配置，适配器编译 schedule |
| event_sources | 上移 desktop_automation_event_sources | source_type/key_ref/key_version/schema/allowed_event_types；场景定义 payload schema |
| events | 上移 desktop_automation_events | source_id/external_event_id/payload_ref/state/match_cursor/eligible_revision_refs；唯一源事件键 |
| occurrences | 上移 desktop_automation_occurrences | scenario_key/task_ref/revision_ref/trigger_key/due_at/expires_at；UNIQUE(tenant_id,scenario_key,task_ref,trigger_key) |
| runs | 上移 desktop_automation_runs | occurrence_id/state/device/lease/fence；UNIQUE(tenant_id,occurrence_id) |
| deliveries | 上移 desktop_automation_deliveries | run_id/position/operation/target_ref/payload_ref/hash/effect/phase；UNIQUE(tenant_id,run_id,position)，不存 block_id 或群字段 |
| attempts | 上移 desktop_automation_attempts | delivery_id/attempt_no/invocation_id/permit_id/result_ref/predecessor_attempt_id；invocation 唯一 |
| audit_events | 拆分：底座 audit_events + 原业务 audit_events | 底座只存 operation/许可/效果审计；业务保留任务/绑定/内容变更，correlation_id 关联 |
| assets | 保留 bs_weixin_marketing_assets | 首场景图片 ACL、hash、mime 与版本引用；底座用 payload resolver，不强建全场景素材库 |
| outbox | 上移 desktop_automation_outbox | kind/aggregate_ref/dedupe_key/state/available_at/lease/attempt_count；UNIQUE(tenant_id,kind,dedupe_key) |
| quota_buckets | 上移 desktop_automation_quota_buckets | scope_type/opaque scope_id/bucket_start/reserved/limit；scope 枚举 tenant/task/target/account/resource，无群专属列 |

上移表保留 UUID、TIMESTAMPTZ 与到期/待处理部分索引。schedule 唯一键为 (tenant_id,scenario_key,revision_ref)；delivery/attempt/result 引用走同租户复合外键。多态 task_ref/revision_ref/target_ref 通过中立 subject registry（desktop_automation_subjects：tenant_id,scenario_key,kind,id,version,owner_id,status）建立复合引用；注册与场景版本发布同事务，场景扩展行用同一 subject_id，禁止任意字符串绕过归属验证。底座只持授权快照和撤销 epoch；具体授权由适配器校验，许可事务同时锁定 subject/epoch，避免检查后授权漂移。

事件 payload 为受控租户存储引用（payload_ref/hash），条件匹配在 ACL 内加载；不可原样写通用日志。原算法中的 automation/revision 是中立 subject，读取业务状态必须经适配器；原 events.payload_json 调整为 payload_ref，原 block_id 由场景维护与 delivery 的映射。业务 init_tables 接现有 init_database；系统表与既有表扩展走 deploy/db_update.yaml 并验证空库初始化。background 启动不依赖先打开聊天。

### 2.1 本地通道扩展与标识

local_tool_invocations 拟增 provider_key、business_kind、business_ref、dedupe_key、deadline_at、authorization_epoch、write_phase，UNIQUE(tenant_id,business_kind,dedupe_key)。device capabilities 增受信 providers 数组、版本/digest/protocol_version，保持旧单 Provider 可读。local_tool_operation_permits 记录 invocation/device/claim token hash、request_id、target_ref/version、payload_hash、revision/epoch、resource_key、deadline、状态及额度预留关联，不存正文。

target_ref 是云端持久 subject 引用，target_handle 是 Provider 在当前设备/session 解析出的短期句柄，两者都为中立术语、不可互换。operation 执行描述携带二者及 target_version；许可绑定持久引用和该次解析的 handle 摘要，防止同一许可替换目标。旧微信 ref 仅在微信适配器内处理，不是底座的长期 target_ref。

调度与暂停的锁顺序统一为 subject(task)→schedule→occurrence/run；扫描须先选择候选 ID，再按此顺序加锁并复验到期条件，不能先锁 schedule 再锁 task。以下“领取 schedule”是逻辑描述，不允许与该顺序相反的 SQL 实现。

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
3. 查设备 manifest 与 session_epoch；probe/target resolve 经 local_tool queue 执行；target handle 只在同一设备/账号及有效版本内使用，身份规则由场景适配器验证。
4. 按场景编译的 operations 初始化 deliveries；payload 预加载并验 hash，所需资源准备成功后才开始第一项。
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

桌面 UI 与数据库不能原子提交。设计目标是“已知操作不重复、未知效果不自动重放”，允许产生待核查状态；不使用 exactly-once 描述桌面操作。数据库锁/fence 不能使已离线的旧桌面进程自动失去点击能力，因此须加本地协议。

新增内部 Runtime write-authorize API（不暴露给 LLM）：在最后一次目标复验后、输入/回车前申请短期一次许可。服务器在同一事务中验证 claim/device、active revision epoch、当前 delivery 未获许可、取消状态、deadline 和配额，保守预留配额并返回 permit_id/短 deadline。多个层级quota按固定scope顺序加行锁，以条件UPDATE确保reserved_count小于limit；多项中任一不足则整个许可事务回滚。许可发出后将该 delivery 标为 may_have_started，旧租约过期也不重新分配该条。

Runtime 先将 permit 和 payload hash 持久写入本地 journal，再执行；journal fsync 失败则不发送。许可过期或网络无法取得许可时禁止开始输入；用单调时钟限制本地许可有效时间，进程重启后不复用旧许可。permit 发放和点击之间收到暂停仍可能完成这一条，产品文案必须反映边界。

发送前再次检测桌面/账号/目标/取消。输入开始后的异常可能留下草稿，即使尚未回车也不可盲重试；只有驱动明确证明未发送且草稿已安全恢复为原始状态才返回 safe_to_retry。不能清空用户原有草稿。回车或粘贴图片若可能立即提交，应在该动作前进入 may_have_started。

### 5.3 Provider/Runtime 协议补强

旧 operation/MCP 契约保留兼容。新增受控 v2 operation 接收 target_handle、request_id、payload_ref/hash 与本地已校验 permit handle；两个消费方映射见底座设计 §3。新 schema 通过对应场景 probe 后才注册 manifest，未协商 v2 不下发新 operation。

Provider 本地以 request_id 防重，保存 prepared/may_have_started/verified/unknown 和结果。重复 request_id：已完成返回既有回执；may_have_started/unknown 不重新点击；prepared 且确认无副作用才允许恢复。新 API 不允许调用方凭 request_id 获得任意目标的权限。

driver 通过受限 stdin JSON 接收正文，不使用进程命令行明文参数；DRIVER_JSON 增加 phase、effect、safe_to_retry、evidence_ref。所有提交可能发生后的崩溃、缺输出、截屏/模型失败均映射 unknown，不能漏为 retryable INTERNAL_ERROR。取消/shutdown 分支未拿到可信结果时写工具一律 unknown。

Runtime 把终态先写本地 result outbox，再经原 invocation/claim 身份重复回传；收到持久化 ACK 才删除。云端迟到证据端点只追加审计并进入对账，验证原 device/claim/request_id/hash，不能重新授权发送。重复回执不产生重复账单。租约过期先停新发送许可，原 started 操作按 unknown 处理，不重新派发。

### 5.4 状态优先级

run 聚合规则：全部 delivery verified/applied 为 succeeded；任一 unknown 时若已有成功则 partial，否则 unknown；全部未提交且取消为 cancelled；部分已发送后截止则 partial，剩余条目标 expired；纯等待超时为 expired。attempt 原始结果与人工业务判定分别存储。

服务端、Runtime 和 UI 统一按 effect/phase 解释，不仅按 success/code：unknown 优先于 success；partial 表示本轮部分副作用；applied 还必须有本次验证证据才成功。协议矛盾落 unknown/protocol_error。旧 BOSS 行为的兼容分支单独测试，不在本功能中误改其恢复策略。

| 结果/故障 | 本条动作 | 后续 |
|---|---|---|
| 提交前设备离线/忙 | bounded backoff，到期停止 | 不产生新已发送记录 |
| target handle 过期且尚无输入 | 同设备重新resolve，最多2次 | 重新核验账号和 target |
| 身份不明/账号变化 | blocked | 暂停任务，重新绑定 |
| 明确effect=none且safe_to_retry | 最多2次，截止时间内 | 同delivery新attempt |
| applied并验证通过 | succeeded | 按顺序下一条 |
| unknown/提交后断网/崩溃 | 待核对，不重发 | 停止本包后续 |
| 用户取消 | 停止尚未开始的条目 | 已提交结果照实回传 |
| 回执网络失败 | 只重传outbox | 不再调用发送工具 |

人工核对追加 decision，原始 effect 不覆盖。若用户确认未发送并授权重试，建立带 predecessor_attempt_id 的新 attempt；先核验旧本地进程已停止、没有待执行许可，再允许操作。仅确认已发送可将 delivery 业务判定为 resolved_applied，但保留机器 unknown 标记。


## 6. 中立 API 契约（拟）

| 接口 / 服务 | 输入与行为 |
|---|---|
| LocalInvocationService.enqueue/get/cancel | provider_key,operation,target_ref/target_handle/target_version,payload_ref/hash,request_id,deadline,authorization_revision；认证身份由受信上下文注入 |
| POST /api/local-tools/runtime/invocations/{id}/write-authorize | claim 身份、request_id、target_version、payload_hash；校验 scope/epoch/资源所有权/额度，返回 permit_id/deadline；不接受 group 或场景正文 |
| POST /api/local-tools/runtime/invocations/{id}/operation-result | 原 claim/request_id/permit、effect/phase/evidence_ref；持久 ACK，迟到只对账；v2 新端点不改旧 result 契约 |
| GET /api/desktop-automation/deliveries/{id} | 授权查看中立 operation 状态与证据引用；场景授权服务控制可见范围 |
| Runtime resource acquire/release（内部） | resource_key,operation_id,lease/fence；OS 锁由 Runtime 持有，不提供 LLM 可调用解锁 API |
| payload resolver（内部） | invocation scope + payload_ref；由受信场景适配器提供受控字节，不接任意 URL/路径 |

write-authorize 许可必须绑定 invocation/device/claim/request_id/target_version/payload_hash/epoch/resource，任一变化拒绝。两个 operation 样例共享同一字段集，不增加微信群或 BOSS 候选人专用 permit 参数。微信图片下载接口可保留场景资源适配，但通用许可/运行协议无图片或群词汇。

## 7. P1 工作量与验收

P1“底座+微信执行基础”重估 7–11 人日：中立服务/表契约抽取 1–2，多 Provider/共享锁与兼容 2–3，许可/effect/journal/outbox 3–4，素材 resolver 与双消费方契约验证 1–2。此为同一工程人日口径的待验证估计；不计 BOSS 自动回复实现。完整微信阶段表见场景计划。

回归必测：

- 既有 invocation 长轮询：queued→claimed→running→result、超时、取消与迟到结果，响应 envelope/时间预算不漂移（tests/integration/test_local_tool_proxy_flow.py、test_local_tools_api.py）。
- 既有 BUSY/UI_CHANGED 自愈、写动作恢复及返回值保持（tests/unit/local_tools/test_overlay_heal.py、test_proxy_tool.py、test_repository_state.py）；新资源等待不进入旧弹层自愈。
- BOSS CLI boss-send-to、chat-send/open/read-executor、boss-read-chat、mcp-conformance 与 manifest；Runtime manifest/providerCrash，以及 pairing/security、write_result_billing/boss_tool_billing。新增 v2 能力缺失时拒绝，不降级旧发送。
- 同一 Windows 设备同时安装微信/BOSS CLI：两任务排队互斥，多 Runtime 不能绕锁；observer 不饿死发送，BOSS 长轮询仍能收结果；用户手动输入/切页后停止，提交可能发生则 unknown。
- 微信 v2 与 BOSS v2 契约样例均验证 target/hash/epoch/permit、防跨租户、防重放；BOSS 仅 fake adapter 契约样例，不能据此宣称其真机通过。
- 断网只重传 outbox；journal 写失败禁止操作；旧进程未停止禁止人工重试；无共享锁能力的旧 CLI 阻断同桌面混用。

本轮未运行这些测试。新协议灰度与回滚不改变既有 BOSS MCP、恢复/计费语义；P1 不通过上述套件不得发布混用能力。
