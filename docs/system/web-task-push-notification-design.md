# Web 任务完成浏览器系统通知设计

> 日期：2026-10-10；状态：Web 适配及客户端开发、独立测试与内外部 CR 完成，真实到达待环境验收。
> 上位设计：[统一通知基础设施](notification-infrastructure-design.md)；[本次开发计划](../plans/plan-web-task-push-notification.md)。
> 本文只细化本次Web适配器与客户端；通用表、路由策略、重试及权限以统一设计为准。

## 1. 已确认行为与首期范围

用户主动开启通知并授予浏览器权限后，任务完成或失败时，即使网站标签页已全部关闭，也能收到浏览器系统通知。文案为 `${会话名称}相关任务已完成` / `${会话名称}相关任务执行失败`；中间进度不推送，首期也不提醒主动取消、token和结算补齐。点击通知登录并核验权限后回到原会话。

本次实现Runner完成/失败生产者、统一通知核心与Web Push适配器。当前任务限定 `source=chat/session_kind=web`，这是业务接入范围，不是通知渠道路由条件；Desktop也使用同一任务来源，不据此硬编码“只允许浏览器接收”。本次实际可登记的通知目标仅Web，其他渠道尚未实现。

会话名称来自服务端原 `chat_sessions.title`，按任务所属主体/租户核验并保存终态快照，发送重试不改变名称。空名称兜底“未命名会话”，清理控制字符并限定展示长度。不附带输入、回复正文、图片、租户名称或其他业务数据，不用LLM生成通知。

开启后新接单任务才纳入通知，不补发历史任务。同一浏览器首期一次开启一个账号/租户的提醒；多浏览器可以分别订阅，此限制属于Web绑定策略，不限制通用核心多目标/多渠道能力。首期页面仍开着时也提醒，不靠关页事件推断在线状态。

关标签页、浏览器完全退出、系统休眠和断网分别验收，后几种到达时机由浏览器/系统/推送服务决定，不承诺即时必达。

## 2. 当前接入点

| 接入点 | 事实与本次变化 |
|---|---|
| `src/services/agent_runner/event_repository.py` | 已有持久terminal事件；事务尾登记通用notification_messages，不修改公开事件wire |
| `src/services/agent_runner/finalizer.py` | 保留终态/历史/结算权威，不直接发送推送 |
| `src/background_runner.py` | 增加统一通知dispatcher，按Web provider启用投递 |
| `src/services/notification_service.py` | 保持现有EMAIL/WEBHOOK调用兼容，使用统一adapter注册机制，不另建Web队列 |
| `frontend/web/main.ts`及聊天页 | 仅Web入口注册Service Worker、通知设置与点击入口 |
| `frontend/web/components/ChatContainer.vue` | `_sid`现在用于预生成会话，新增已有会话定位逻辑，不能复用其发送语义 |

不把平台通知伪装成optional_modules租户定制模块；本次不修改Desktop/Runtime管理或执行协议。

## 3. Web适配器与目标配置

Web目标类型化配置包含endpoint、p256dh/auth、expirationTime、VAPID key_id及浏览器安装引用；敏感值使用既有保护存储。endpoint摘要在Web provider范围唯一，绑定归属和版本由服务端核验。通用表不直接要求这些浏览器字段。

服务端 Web adapter 使用 `pywebpush==2.1.2` 的标准 payload 加密及 py_vapid 签名，传输使用校验 DNS 后固定地址的 TLS 连接。VAPID 私钥从既有秘密配置注入，公钥通过统一 capabilities 的 Web 能力节点提供。配置轮换支持 key_id 和旧订阅兼容截止时间，实际 provider 到达尚未验收。

按实测推送服务配置endpoint主机允许列表：HTTPS、允许端口、无userinfo，不跟随任意重定向，拒绝内网/本地/元数据地址，解析与实际连接一起限制。供应商错误转为核心统一结果：404/410目标失效，429按Retry-After可重试，超时/5xx有限重试；401/403区分目标与全局VAPID配置故障，不能批量误删有效目标。

Web适配器遵从通用TTL、并发、领取/租约和重试政策；同一通知多个浏览器各有delivery，不另建web_push_deliveries或web_task_notifications。

## 4. 浏览器链路

1. 用户点击开启，Web运行时检查Service Worker/PushManager/Notifications能力，向系统申请权限。
2. 注册根scope的Service Worker并取得PushSubscription，经统一targets API提交channel=web_push的类型化配置；偏好启用成功才显示已开启。
3. Runner根任务terminal事务登记通用消息及会话名称快照，统一后台展开到已授权Web目标并发送加密推送。
4. 浏览器push事件唤醒Service Worker，显示系统通知。
5. 点击后打开同源通知入口，当前用户认证并解析resource_ref后，用原会话/Runner读取接口查看结果，不重发任务。

Web平台permission不等于服务端目标登记成功；登记失败要呈现失败状态并允许重新尝试。权限拒绝后提示浏览器设置，不重复申请。提供关闭和测试通知入口，复用Base*与现有语义token，只展示本次已支持的Web渠道。

通过HTTPS提供 `/task-notifications-sw.js`，正确MIME和更新缓存策略，不让SPA fallback返回HTML。Service Worker仅处理push、notificationclick和非凭据本地绑定状态，不缓存聊天/API、不接管fetch、不持有token、不轮询或运行Agent。只在Web入口注册，不注册到Desktop。

## 5. Wire、展示与点击

通用通知契约与Web传输封装分别版本化。Web payload最小化为schema_version、notification_ref、session_title、event_kind、绑定版本、expiry；内部resource_ref可由认证后的点击API解析，不将任意跳转URL发给客户端。

showNotification采用固定模板和图标：

- completed：`${会话名称}相关任务已完成`；
- failed：`${会话名称}相关任务执行失败`；
- 可另用body提示“点击查看结果/详情”。

固定notification_ref对应tag，renotify=false尽量减少重试重复展示，不能保证恰好一次。Safari userVisibleOnly要求收到push后展示通知，不根据页面可见性静默吞掉。未来前台抑制由通用路由策略在发出前决定。

notificationclick仅打开固定同源 `/notification?ref=...`。入口复用登录与合法return URL，然后调用 `GET /api/notifications/messages/{notification_ref}` 核验主体和原资源权限，定位原租户/会话/Runner。账号不匹配、无权限或会话已删除明确说明，不暴露另一账号名称/结果。未登录时不能靠通知引用越过认证。

`ChatContainer.vue`目前没有可靠的已有会话deep link契约，需新增明确的加载流程；不能仅拼 `_sid` 就声称已支持。

## 6. 账号与浏览器生命周期

同一浏览器切换提醒主体时撤销旧Web目标并重新订阅，不静默覆盖另一主体endpoint。跨tab同步绑定版本，旧tab不得复活旧绑定。发出前重新检查目标状态/版本和主体权限。

服务端登出流程撤销对应浏览器目标；前端更新本地绑定状态并unsubscribe。此动作只影响该Web客户端，不自动撤销用户其他浏览器、桌面或第三方目标。离线注销不能保证服务器即时撤销，需记录待处理状态并在再次联网时清理；已在途/已显示的含会话名称通知不能保证追回。

关tab不撤销目标、不取消任务，也不暂停统一后台投递。浏览器订阅失效或权限撤销在后续状态同步/供应商错误中处理，不能无限发送。测试通知走同一安全与调度边界，不能提供任意endpoint发送入口。

## 7. 本次验收

先在隔离HTTPS环境打通真实订阅→测试推送→关闭所有站点tab→系统通知→点击原会话，再做Runner终态接线和故障验证。生产服务器及用户设备均需连通各浏览器推送服务，国内/企业网络以真实订阅验证，Mock或浏览器API存在不等于到达。

Chrome/Edge优先，Firefox/macOS Safari分别验证；iPhone/iPad纳入支持时需主屏Web App及manifest/图标安装能力，不作为桌面Web首期门槛。

验收包含正确名称/完成失败文案、空/长/控制字符名称、终态后改名与重试、进度不推送、取消/结算不提醒、多浏览器同通知独立投递、目标撤销/权限拒绝/账号切换/租户隔离、后台重启和超时重试、点击登录回跳及禁用功能。桌面与第三方本次只做核心扩展契约验证，不宣称对应客户端可用。

## 8. 官方资料

- [MDN Push API](https://developer.mozilla.org/en-US/docs/Web/API/Push_API)：页面未加载时接收推送、订阅及Service Worker。
- [MDN showNotification](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/showNotification)：系统通知参数、tag与点击。
- [WebKit Meet Web Push](https://webkit.org/blog/12945/meet-web-push/)：关tab后的推送及userVisibleOnly要求。
- [WebKit iOS/iPadOS Web Push](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/)：主屏Web App与授权。
- [pywebpush源仓库](https://github.com/web-push-libs/pywebpush)：标准Python实现候选。
