# 桌面 CLI 自动任务：微信首场景实施与 BOSS 衔接计划

日期：2026-09-08 · 状态：📋 待开发。配套：[产品/架构/UI](../../design/weixin/weixin-marketing-automation-design.md) · [当前代码调研](../../research/weixin-cli/automation-readiness-2026-09-08.md)。本文件定义目标实现，代码路径标“新增/修改”均为计划，不代表本次已实现。

## 1. 关键技术决策

遵循[中立底座设计](../../design/desktop-automation/desktop-cli-automation-design.md)与[底座实施计划](../desktop-automation/plan-desktop-cli-automation.md)。src/weixin_marketing/ 仅承载微信任务/版本、内容包、账号/群绑定、触发配置及场景授权；LocalInvocationService、许可、journal/outbox、effect/phase、桌面锁、quota 与事件接纳骨架分别归 desktop_automation、local_tools 和 Runtime。

微信 executor 保持 weixin.fixed_content.v1，不调用通用自然语言 scheduled-task executor。首期 Windows、文字/网址/图片/有序内容包、时间与内部事件/签名 webhook 范围不变；BOSS 仅作为第二消费方设计，现有 MCP/恢复语义不改变。

## 2. 数据模型

微信业务表字段如下；原 17 张表已在[底座计划 §2](../desktop-automation/plan-desktop-cli-automation.md#2-原微信表族逐表归属裁决)逐表裁决，上移项不再创建微信前缀副本。业务表均保留非空 tenant_id/user_id/created_at、UUID、TIMESTAMPTZ 和租户复合约束。

| 表（统一前缀 bs_weixin_marketing_） | 关键字段 | 约束/索引 |
|---|---|---|
| automations | id,name,status,active_revision_id,draft_revision_id,owner_scope,version,pause_reason | tenant/user/status/updated_at；version 乐观锁 |
| revisions | id,automation_id,revision_no,executor_type,trigger_json,policy_json,group_binding_id,content_hash,authorized_by,authorization_source,source_message_id,published_at | UNIQUE(tenant,automation,revision_no)；发布后不可变 |
| content_blocks | id,revision_id,position,kind,text_content,url,asset_id,payload_hash | UNIQUE(tenant,revision,position)；type 与字段互斥 CHECK |
| group_bindings | id,device_id,account_binding_id,label,identity_evidence_ref,identity_version,state,verified_at | tenant/user/device；label 非唯一身份 |
| account_bindings | id,device_id,account_anchor_ref,session_epoch,status,verified_at | 绑定经 probe 验证的账号依据；云端仅存受控摘要/引用 |
| audit_events | id,automation_id,run_id,action,actor_type,actor_id,from_version,to_version,details_redacted | tenant/automation/created_at；追加写 |
| assets | id,storage_ref,sha256,mime,size,width,height,status,retention_until | tenant/hash 索引；引用中不可硬删 |

audit_events 此处仅保留微信配置/目标/内容变更；operation 审计在底座。revision 与中立 subject 同事务发布。content_blocks 与底座 delivery 通过场景映射关联，底座不含 block_id/group_binding_id。微信发送配额由场景映射 task/target/account scope，底座 quota_buckets 负责原子预留。

## 3. 触发接纳衔接

时间扫描、事件去重/分页、DB outbox 与锁顺序见[底座计划 §3](../desktop-automation/plan-desktop-cli-automation.md#3-触发接纳算法)。微信保留 once/interval/calendar/event 触发配置，发布后编译 schedule 与事件匹配订阅；原有宽限、skip_overlap、月底/DST、次数和事件不覆盖正文/目标规则不变。

## 4. 场景执行衔接

底座执行器持有 run/delivery/attempt；微信适配器固定执行账号与群复验→准备全部内容/图片→顺序提交→本次写后校验。任一 unknown 停后续，部分完成保留逐条状态。完整执行与资源锁见[底座计划 §4](../desktop-automation/plan-desktop-cli-automation.md#4-场景执行器与并发)。

## 5. 写动作许可与恢复衔接

许可、journal、结果 outbox、effect/phase、人工重试和迟到回执统一见[底座计划 §5](../desktop-automation/plan-desktop-cli-automation.md#5-写动作许可幂等与恢复)。weixin_message_send_v2 将核验后的 target ref 转成中立 target handle，冻结 payload；boss_send_to_v2 用同一契约作设计校验。本轮不新增任一 operation。

### 5.4 全部底座决策的兼容约束

现有 BOSS MCP 契约、长轮询、BUSY/UI_CHANGED、自愈/恢复、取消/关闭和计费语义保持不变。v2 需显式协商，旧 Provider 不下发 v2、不把新任务降级为旧发送；此要求适用于表、服务抽取、资源锁、许可和全部底座设计，不能仅在结果聚合处设兼容分支。

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
| P1 底座+微信执行基础 | 中立服务/表、Provider/共享锁、许可/journal/outbox/effect、素材 resolver 与双消费方契约校验 | 不破坏明确列出的 BOSS 回归，真实断网/崩溃语义 | 7–11 |
| P2 时间闭环 | DB/API/调度/文字网址/逐条账本/取消配额 | 假时钟与崩溃恢复，旧dry_run不可触发 | 4–6 |
| P3 工作台与Agent | 列表编辑、群设备、预览、记录、聊天工具 | UI与API共用服务，权限/版本并发 | 4–6 |
| P4 完整MVP | 图片产品化、内部事件适配示例与签名webhook、有序内容包 | 图片真机通过、事件重复/洪峰/循环测试 | 5–8 |
| P5 发布 | 独立测试/CR修复、安装升级、灰度/回滚 | 全部关键门禁通过 | 3–5 |

原微信方案基线为24–38人日；P1 分层及兼容工作重估后，底座+完整微信 MVP 合计约26–41人日（其余阶段不变），不含 BOSS 场景开发。人日仍为工程工作量、非日历工期或交付承诺，存在P0不确定性；可并行部分前端与后端，但不通过并行省掉P0/P1门禁。原生Mac driver另估：P0先做2–3人日可行性验证，再按证据排期，未验证前不承诺日期。

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
| BOSS 长轮询回归 | 既有 invocation queued/claimed/running/result、超时/取消/迟到回执 envelope 与时间预算不变 |
| BOSS BUSY/恢复回归 | BUSY/UI_CHANGED 弹层自愈、旧写结果/关闭/重试语义不变，新排队不触发旧自愈 |
| BOSS CLI/协议回归 | boss-send-to、chat-send/open/read-executor、boss-read-chat、mcp-conformance、manifest/providerCrash；旧契约和 capability 保持 |
| BOSS 通道回归 | pairing/security、write_result_billing/boss_tool_billing、租户隔离与原成功沟通记录回写不变 |
| 桌面互斥 | 同一 Windows 微信与 BOSS CLI 排队、多 Runtime 共享 OS 锁、observer 不饿死发送；手动抢焦点/输入停止且不盲重试 |
| 其他回归 | 通用定时任务不变、工具装配、租户存储；详细套件见底座计划 §7 |

P0 可在已授权「哈尼」群只发一条例如“自动任务发送测试：这是一条测试消息。”，前提是唯一确认目标且Windows执行入口可用；本次未实际执行。压力/矩阵发送使用专用测试群和另行明确范围，离线故障测试优先fake driver。

发布检查：迁移/空库启动、background重启、Runtime安装配对、微信manifest匹配、受限租户端到端、观察至少完整调度周期。放量先单设备/单群，再小租户；发现错群或自动重复立即停用。

回滚：先关闭新触发并撤销新许可，再停止尚未提交动作、收集在途回执；unknown保留，不清队列后重发。不删业务表/审计/journal，不将新任务降级交给旧prompt scheduler。新协议任务只派给支持版本，旧Runtime继续原BOSS链路；数据库变更先兼容增量、后清理。

## 11. 本轮交付边界

已完成源码研究、完整产品/架构/UI设计和本实施计划，并在 ideas.md 登记待开发。本次修订未修改业务代码或 CLI、未提交 Git、未创建后台自动任务、未实测发送；V1.0 设计已在交接提交 4f5acd61 中提交，此处不否认历史提交。实际开发开始时更新索引为部分完成；P0–P5验收完成才移入ideas_finished.md。

## 12. BOSS 聊天自动化实施衔接（📋 待独立立项）

产品、observer、水位/去重、绑定、单点决策、授权、数据/UI 和通知完整定义见[场景设计 §11](../../design/weixin/weixin-marketing-automation-design.md#11-第二场景boss-直聘聊天自动化待独立立项)，源码事实与缺口见[调研 §8](../../research/weixin-cli/automation-readiness-2026-09-08.md#8-boss-第二场景定向核验2026-09-08-修订)。本节不是开发授权。

| 里程碑 | 交付与验收 | 依赖 | 工程估算 |
|---|---|---|---|
| P0′ Observer/发送真机验证 | 未读覆盖、长会话虚拟化/去重、账号/候选人唯一性、增量气泡写证据、相同话术重复测试 | 可与微信 P0 并行；独立 BOSS 设备会话与试发授权 | 待立项估算，不计微信 |
| P2′ BOSS 观察与数据 | src/boss_chat observer、threads/messages/cursors、事件适配、身份绑定，快照/水位/outbox 原子接纳 | P1 + P0′ 门禁 | 待独立估算 |
| P3′ 决策与执行 | 关键词/受限 LLM 决策、话术版本、预授权服务、boss_send_to_v2、delivery 映射/时间线投影 | P2′；新协议 capability 与写证据通过 | 待独立估算 |
| P4′ 工作台与转人工 | 策略编辑/发布、会话时间线/接管、负责人路由、群 webhook 新通知类型 | P3′；明确通知授权与员工映射 | 待独立估算 |
| P5′ 灰度与回归 | 同桌面混用、故障恢复、观察缺口/循环抑制、旧 BOSS 全套回归 | 独立测试/CR、全部 BOSS P0′ 门禁 | 待独立估算 |

实施契约：新增 /api/boss-chat/policies 与 /draft/validate/publish/pause、/threads、/threads/{id}/takeover/resolve/resume、/observations 和 /handoffs；统一 tenant/owner ACL、If-Match、POST Idempotency-Key、403/404/409 与副作用明确的操作。复用底座 /deliveries 与许可，不新增 BOSS 私有 permit API。拟表 bs_boss_chat_* 的精确集合见设计 §11.5；底座通过 subject 注册对应 policy revision、target 和事件源。

通知扩展 recruiting_notify_service 的 handoff 类型、负责人路由与通知 outbox；不改变旧面试 pre/done 接口。沟通日志投影以已验证 resume_id 和 source_delivery_id/source_message_id 幂等关联，禁止新旧双回写。招聘话术按 ID/版本冻结；未来 SUBAGENT.md 与工具说明仅为专用已发布任务增加预授权例外，原 boss_send_to/send_current 逐次确认不动。

BOSS 必测：首次水位不回旧消息；重启/徽章清空/当前会话新消息不误漏；快照虚拟化或重复正文无法对齐时阻断；仅 them 触发；突发回复聚合且批次唯一、冷却/上限重启不清；LLM 枚举越界/无证据/超时转人工；人工已回复使旧决策失效；话术版本及 scope 变化撤销许可；unknown 不重发；通知失败仅补通知；沟通投影失败仅补留痕。假驱动通过不能代替 BOSS P0′。

微信 P0 与 P2–P5 范围、验收门禁不变；BOSS P0′ 可独立并行，P2′ 起依赖 P1，微信交付不等待 BOSS 场景完工。两条线路共享资源的实际真机验证不能在同一桌面同时操作。
