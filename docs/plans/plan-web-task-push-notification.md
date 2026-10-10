# 统一通知基础设施与 Web 推送开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 通用通知契约、持久化与Web真实推送验证 | 🔧 进行中 | 开发完成；四表增量首次/重复迁移通过，真实到达待环境验收 |
| Phase 2 | Runner生产者与统一策略/投递后台 | ✅ 完成（2026-10-10） | 定向真实DB22项及独立DB21项通过；默认关闭，不改变Runner wire |
| Phase 3 | Web设置、点击原会话与客户端授权隔离 | ✅ 完成（2026-10-10） | 实际组件/hooks回归通过，两项P2及补充竞态修复经外部终审通过 |
| Phase 4 | 独立验证与Web部署验收 | 🔧 进行中 | 前端127项、独立41项及构建通过，外部终审通过；真实推送及部署待验收 |

上位设计：[统一通知基础设施](../system/notification-infrastructure-design.md)；本次适配：[Web任务完成通知](../system/web-task-push-notification-design.md)。2026-10-10登记并按用户要求修订，设计包含Desktop与第三方扩展边界，本次不实现这两个渠道。设计者外部最终审查的P2及补充竞态均已修复/复核通过，本次代码开发完成，索引移入完成开发；真实环境退出条件仍待验收，不将本机结果计为真实到达证据。

## Phase 1：统一底座与Web可行性

1. 核定通用消息/资源引用/目标/偏好/adapter结果契约及版本；业务来源与渠道分开，不因source=chat而写死Web投递。
2. 显式迁移notification_messages、notification_targets、notification_preferences、notification_deliveries，提供版本门和索引，更新系统数据库文档。原三张Web专用候选表取消，避免通用队列以后再迁移。
3. 建立通用仓储及adapter注册表，本次只注册web_push；保留现有NotificationService EMAIL/WEBHOOK原调用兼容，不迁移无关业务。
4. 配置分核心、provider、租户允许策略、用户偏好；同步settings.py和config.yaml。VAPID属于Web provider，私钥沿既有秘密配置。
5. 实施通用capabilities/targets/preferences/test API，当前只接受Web target DTO；后端核验主体/目标归属与出站安全。
6. Web Service Worker与成熟Web Push库打通真实HTTPS订阅、测试、关全部站点tab后的通知和点击入口，记录服务器及用户网络连通性。

退出条件：统一数据与接口不依赖所有目标具有浏览器字段，真实Web推送可行性有证据；实际部署网络未验证时不得宣布生产可用。

## Phase 2：生产者、策略与可靠投递

1. 在Runner根terminal事件外层事务尾发布task.completed/task.failed通用消息及会话名称快照，覆盖全部终态路径；公开Runner wire不变。中间进度、取消、结算补齐不产生用户通知。
2. 通用策略按接收主体、偏好、目标及接单/启用边界生成独立delivery，幂等展开并固定此次路由；后续登记目标不补发历史。
3. 通用dispatcher接background_runner生命周期，实现有限并发、领取版本/租约、TTL、重试和错误归类；多目标故障互不阻塞，不跨网络持有DB锁。
4. 发出前核验当前权限、偏好和目标版本，不持原bearer。支持accepted与真实回执能力区分，不将接纳自动称为显示/已读。
5. 用真实DB验证事务/唯一键/并发领取和崩溃恢复；不同真实Web目标验证fanout，用非Web独立fake验证adapter边界，不冒充Desktop/第三方已实现。

退出条件：网络投递失败不改变已提交任务结果，收尾DB失败按原finalizing重试；同一事件不重复创建消息、多目标各有账本和结果，扩展adapter不需修改Runner终态逻辑。

## Phase 3：Web产品闭环

1. 使用项目frontend-design skill，复用Base*与既有布局，加入开启/关闭/测试通知；不展示尚未实现的桌面/第三方设置。
2. 处理不支持、权限拒绝、服务禁用和登记失败，仅用户点击开启申请浏览器权限。
3. 接入服务端登出撤销对应Web目标、跨tab绑定版本和离线待处理，不撤销其他客户端/渠道。
4. 建立同源通知定位入口、认证后的messages引用解析、已有会话/Runner加载，不复用Lobby的_sid发送语义。
5. 文案为 `${会话名称}相关任务已完成` / `${会话名称}相关任务执行失败`；使用服务端终态快照，验证空/长/控制字符名称和重试稳定性。

退出条件：关tab后系统通知可点击回原会话；登录、账号切换和跨租户均正确且不重新发送任务。

## Phase 4：独立验证与交付

涉及数据库、租户鉴权、Runner事务和后台调度，按高风险流程：主控开发/自测，独立测试智能体与独立CodeReview智能体验证，主控整合。

- 后端定向测试用 `./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`，确认代码/环境匹配；相关配置/import/隔离启动检查。
- 前端相关交互测试及 `cd frontend && npm run build`，检查设置界面与定位路由。
- 核心验证业务事件幂等、多目标、偏好/目标撤销、版本变更、并发/租约、过期、adapter错误、provider禁用、资源权限与原调用兼容。
- Web真实浏览器验证关tab完成/失败、名称/点击登录回跳、进度不推送、失效订阅、后台重启、账号/租户及多个目标；记录未验收浏览器/网络。
- 本次不运行未实现Desktop/第三方真实发送测试；后续接入另立实施阶段，验证客户端展示/驻留、第三方授权/回执及协议兼容。
- 更新本计划进度后同步ideas；全部本次业务开发完成才移档。准备迁移、provider秘密配置、网络、回退和清理方案，不自动提交或部署。

## 实施决议（2026-10-10）

开发前已与设计者会话核对一致：scope复用`global` / `tenant:<id>`，用户单独隔离；接单依据为Runner实际`accepted_at`。通知读取使用主体/会话归属并额外检查租户active及未过期，不复用积分、执行或智能体订阅门。偏好与目标的启用时间均须不晚于接单；关闭再开重置边界，订阅刷新保留边界。仅注册Web adapter，默认核心/provider关闭。

通知schema与Web payload均为v1，唯一写入者为通知核心；Runner仅在原终态事务尾提交意图，不改变原wire/Runtime/Device/binding。数据库使用四张通用表；参数、目标配置及渲染快照复用EncryptionManager保护。秘密仅由环境注入，VAPID旧key显式设置兼容截止时间。出站只允许配置的HTTPS主机，解析全部为公网并将实际TLS socket连接固定到同次解析地址，验证原主机名，无重定向/环境代理。

真实VAPID、HTTPS部署、浏览器订阅和供应商/用户网络尚未验证；本次无部署授权，不宣称关tab真实到达已验收。

全局 Web 当前没有原聊天路由。本次为平台管理员的 global 通知增加 `/portal/notification-session` 原资源只读入口，复用会话/消息/Runner 读取权限；租户通知进入既有 ChatContainer 的显式已有会话加载流程。两者均不使用 `_sid` 重新提交任务。

## 启用、回退与环境验收

以下为交付准备，未执行服务器迁移、配置变更或重启。部署仍须用户明确指示目标环境。

1. 保持 `notifications.enabled=false` 和 `notifications.web_push.enabled=false`，按既有迁移流程执行 `2026-10-10 17:10:00` 块。初始化 SQL 与增量块同义；运行进程只核验 v1，不建表、不覆盖不兼容 schema。更新包含固定 `pywebpush==2.1.2` 的依赖。API、Runner 与 background_runner 使用相同数据库、EncryptionManager 配置及通知设置；只在专用 background_runner 运行 dispatcher。
2. VAPID 使用秘密环境注入 `WEB_PUSH_VAPID_PRIVATE_KEY`，公开部分使用 `WEB_PUSH_VAPID_PUBLIC_KEY`、`WEB_PUSH_VAPID_KEY_ID` 和联系地址 `WEB_PUSH_VAPID_SUBJECT`；不能把私钥写进仓库、日志或验收截图。确认公私钥匹配及 `mailto:`/HTTPS 联系地址。核心/provider 环境开关分别为 `NOTIFICATIONS_ENABLED`、`WEB_PUSH_ENABLED`，默认均关闭。
3. 按实测 provider 配置 `notifications.web_push.allowed_hosts`，只接受允许的 HTTPS/443 公网 endpoint。DNS 校验与真实 TLS 连接绑定同次地址，不跟随重定向、不使用环境代理。必须分别验证部署服务器至供应商、用户浏览器至供应商的网络；未经验证不能靠放宽内网限制解决连通问题。`allowed_tenants=null` 表示允许符合权限的租户；空列表不启用任何租户，全局管理员 scope 独立核验。
4. 发布 HTTPS Web 构建后检查 `/task-notifications-sw.js` 返回 JavaScript、根 scope、禁用长缓存且缺失时返回 404，不落入 SPA HTML。仓库五份 Web nginx 配置已增加 exact location，仿真环境保留原 IP 白名单；其他外部托管配置需同样检查。Worker 不持 bearer、不拦截 fetch。依次开启核心、Web provider 和测试租户，再由用户点击开启与测试。
5. 轮换前为旧 key_id 通过秘密环境 `WEB_PUSH_VAPID_PREVIOUS_KEYS` 配置私钥及含时区的 `accept_until`，新公私钥使用新 key_id；兼容期内已有订阅仍用旧密钥，用户显式同步使用新订阅。截止后旧密钥停止投递，不静默替换浏览器 applicationServerKey。删除过期旧秘密依既有秘密管理流程执行。
6. 默认 TTL 24 小时、最多 6 次、并发 4、每实例 5 次/秒、保留 30 天；多 background_runner 实例共享数据库领取与租约，但总出站速率为实例速率之和，需按供应商配额配置。测试入口的每用户限频由数据库共享。关闭 provider 停止外发，关闭核心同时停止产生/展开/发送新通知；不删除四表、旧秘密或业务数据。重新启用会复核权限、版本及 TTL，可能恢复仍有效的既有投递。清理有界删除过保留期 message/delivery，保留 target/preference 与原会话/Runner；直接回滚代码需先关闭核心并保留账本。

真实环境验收记录仍待补充：Chrome/Edge 真实订阅与多目标，完成/失败/名称快照，关闭全部标签页后系统展示及认证点击，账号/租户切换、失效订阅、后台重启与离线；Firefox/macOS Safari、浏览器完全退出和休眠分别记录能力与到达时间。Mock、真实隔离 PostgreSQL 及构建通过只证明实现契约，不能替代该到达证据。

## 本机验证记录（2026-10-10）

- 主控通知单元与独立 dispatcher/旧接口测试：`PATH=/private/tmp/aid-notification-venv/bin:$PATH bash scripts/dev_test.sh tests/unit/notifications -p no:cacheprovider -q`，31 passed，exit 0；Web 加密使用真实 pywebpush/py_vapid，网络连接为 fake，不发送真实通知。
- 主控定向真实 PostgreSQL：通知行为16项加既有 Runner 事件/事务/结算4项，20 passed；另补增量迁移全新 namespace 首次/重复执行及 dispatcher 真 schema 启动/发送/drain，2 passed，exit 0。对应 `tests/integration/agent_runner_service/test_notifications.py` 及受影响 Runner 回归文件。
- 独立真实 PostgreSQL：`tests/integration/agent_runner_service/test_notifications_independent.py`，21 passed，exit 0；覆盖账号换绑/CAS/启用边界、并发登记与测试限频、writer 幂等/回滚、权限/额度隔离、失效/过期/重试/保留清理、logout 原子性及 schema 拒绝。
- 共享工作区其他会话收口 Runner 接线后，对最终当前源码合并运行上述通知及受影响 Runner 测试，43 passed，exit 0（主控22项+独立21项），确认未受接线变化影响。
- DB 测试使用任务专属 `/private/tmp/aid-notification-pg-test.py` 启动临时本机 PostgreSQL，并经 `bash scripts/dev_test.sh` 使用已确认 Python 3.12/临时 venv；运行后数据库及进程销毁。初始化跳过本机二进制没有的、该验证不使用的 uuid-ossp 扩展，通知迁移完整执行。未访问配置中的企业数据库、未运行更新脚本。
- 当前挂载源码的本地开发容器中，API、background_runner、AgentRunner bootstrap 导入及 API/Runner OpenAPI 路由装配通过（核心、Redis 关闭，不运行业务生命周期）。真实通知 dispatcher 生命周期已在上述临时 DB 验证。旧配置 Pydantic/Starlette 弃用警告保留，本次不扩展处理。
- 初版前端：通知目录及router测试10文件82 passed，exit 0，构建通过。外部终审修复后的最终源码于18:51冻结验证：`cd frontend && npx vitest run web/__tests__/notifications web/__tests__/router/routes.test.ts web/__tests__/components/MenuSidebar.test.ts`，15文件127 passed，exit 0；包含独立client/global/实际ChatContainer导航/实际installed hooks测试41项及既有Sidebar19项。`npm run build`（含vue-tsc）通过；Web dependency boundary256文件、1036 transformed、artifact1117模块检查通过，保留既有大chunk/Browserslist提示。通知设置和global原会话只读页1280×900、390×844实际浏览器视觉检查通过，无横向溢出/pageErrors；本轮无视觉结构变更，复用初版检查；使用本地Mock资源，不是系统Push验收，本地Vite已停止。
- 独立 CR 已确认后端事务、scope/资源复验、租约 fencing、TLS 出站与默认关闭边界；前端账号切换 P1、响应丢失恢复 P2 及 logout 旧版本问题均已修复/复核，最终未发现未关闭 P0/P1/P2。切换屏障有界并续租，相关网络15秒截止；登出显式DELETE确认，HTTP200不冒充target撤销，失败保留pending；休眠/迟到结果不覆盖另一主体绑定。五份 nginx 的缺失 SW 资产继承 SPA error_page 问题已修复并复核；nginx-t 和部署 HTTP MIME/cache/404 检查待环境验收。

## 外部最终审查修复（2026-10-10）

设计者经用户授权进行最终源码审查，本轮只修其确认的问题；终审通过后按用户在该会话中明确授权的“审核通过后推送代码”流程提交推送，不部署。

| 问题 | 修复边界 | 状态与验证 |
|---|---|---|
| P2 通知会话定位未随用户导航失效 | 侧栏同path新会话/选会话清通知链接参数、错误/loading；pending响应不得切回原会话，卸载/身份变化不得污染共享会话 | 修复及独立实际组件8项通过，含认证/数字员工初始化await；外部终审通过 |
| P2 登出准备阶段的SW/PushManager等待无上界 | 登出finally覆盖prepare；SW/PushManager和Web Locks等待有界，锁超时同步抑制旧主体；同binding保留不可取消订阅清理标记，晚到登出按原凭据快照清理 | 独立实际hooks13项及补强实际setLogin13项通过，开发3项/2项通过；外部终审通过 |

本轮生产改动仅前端7文件：ChatContainer、MenuSidebar、chatSessionNavigation、通知client、auth/saasTenant API及useTenantAuth。同步侧栏导航意图、route/主体/auth/generation复核与卸载失效；浏览器各步骤及锁排队5秒截止，原主体binding在等待锁前捕获并建立身份屏障，超时fallback同步CAS标记revoked/pending并抑制Worker。native unsubscribe无法取消时保留共享清理标记，再次身份切换和跨模块重试不能丢失或过早复用订阅。登出清理逐项核对原token及user_info/admin/tenant快照，reactive登出也核对原token，迟到结果不清新登录。

主控对初版冻结清单核验后端、相关测试与迁移共27文件hash一致，沿用此前31/43通过证据，未重复运行后端。全工作区`git diff --check`通过。设计者最终核验73文件hash零差异、实际tool执行记录及补强独立测试，确认无未关闭的阻断问题并通知按用户授权提交/推送；已fetch确认master与origin/master一致，保留其他会话改动，仅纳入本任务。真实推送、部署和启用均未执行。
