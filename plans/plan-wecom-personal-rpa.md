# 开发计划：企业微信个人账号 RPA 生产级独立客户端

> 关联设计：[docs/system/wecom-personal-rpa-design.md](../docs/system/wecom-personal-rpa-design.md)
> 关联调研：[docs/research/wecom-personal-rpa-client-implementation-research.md](../docs/research/wecom-personal-rpa-client-implementation-research.md)
> 登记：[docs/ideas.md](../docs/ideas.md) 渠道集成分区
> 创建日期：2026-06-16
> 更新日期：2026-06-22
> 状态：🔧 部分完成（服务端渠道完整、74 测试通过、休眠上线安全；C# 客户端 5 工程全部编译通过、36 测试通过；操作手册+9 个 PowerShell 脚本、准入验证探测工具、WiX v5 MSI 骨架已交付；P1.1 管理前端已交付——`frontend/src/components/saas/WecomPersonalRpaManager.vue` 三 Tab 接入全部 9 个管理端点，`npm run build` 0 错误，未真实联调。待：真实环境准入验证回填节点常量、删 Stubs 接真自动化、WiX 实编译+签名、14 天验收）

---

## 交付原则

首个可交付版本即为生产准入版本，必须同时包含客户端稳定性、异步队列、会话绑定、文本/图片/文件 actions、监控、审计、安装升级、异常暂停和回滚能力。

可以按工程顺序并行开发工作包，但不能把生产能力推迟到后续版本。

---

## 0. 编码前准入验证

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 1 | 准备标准 Windows 环境：固定企微版本、1920x1080、DPI 100%、远控保活 | ⬜ | 环境基线文档 |
| 2 | FlaUI/UIA3 探测企微主窗口、输入框、会话标题、消息区域 | ⬜ | UIA 探测报告 |
| 3 | Win32 SendInput 验证窗口置顶、粘贴文本、点击发送 | ⬜ | 输入执行报告 |
| 4 | OpenCvSharp 验证关键按钮、二维码区域、发送按钮模板匹配 | ⬜ | 模板匹配报告 |
| 5 | 验证登录态：已登录、未登录、二维码过期、账号异常 | ⬜ | 登录态识别报告 |
| 6 | 验证交互式会话保活：远控断开、重启、锁屏检测 | ⬜ | 桌面保活报告 |

> **§0 状态说明**：本节 6 项任务全部依赖真实 Windows + 企业微信桌面环境，本会话（无 Windows 企微环境、远程 DB 不可达）未实现，保持 ⬜。客户端自动化层的接口骨架（§2）已落，但节点常量 `wecom_nodes.yaml` 的真实控件/模板路径必须由 §0 准入探测产出后才能填充，属强阻塞。
>
> **准入验证工具已就绪**：`clients/wecom-personal-rpa/src/Client.Probe/`（net8.0-windows 诊断台，7 步：环境/主窗口/UIA 树/登录态/二维码截图/模板匹配/焦点夺取，输出 `probe-report.yaml` + 回填建议）+ `scripts/run-probe.ps1` + `docs/准入验证手册.md`（6 项成功标准 + probe-report→wecom_nodes.yaml 字段对照 + Go/No-Go）。在真实 Windows+企微环境运行即可产出回填所需数据。

准入失败规则：

- SendInput + OpenCV 在固定环境下仍无法稳定执行文本发送，则暂停 PC RPA 开发。
- 桌面会话无法稳定保活，则先解决运行环境，不写业务代码。
- 出现无法可靠检测的误发风险，则不进入正式开发。

---

## 1. 客户端生产工程

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 7 | 新建 `clients/wecom-personal-rpa/WeComPersonalRpaClient.sln` | ✅ | .NET 8 解决方案（5 工程 GUID 对齐） |
| 8 | `Client.App`：WPF/托盘 UI，展示二维码、状态、暂停恢复、错误 | ✅ | WPF 托盘+二维码/状态/错误窗口+RpaHost 编排，编译 0 错误（跨工程 using 已修）；当前注入 Stubs 桩，真实自动化待 §2 |
| 9 | `Client.Supervisor`：Windows Service/计划任务监督 App 存活 | ✅ | SupervisorService + OfflineReporter 编译通过 |
| 10 | `Client.Core`：状态机、配置、限速、协议、队列 | ✅ | 核心库编译通过 |
| 11 | `Client.Automation`：FlaUI、Win32、OpenCvSharp 封装 | ✅ | 自动化库编译通过 |
| 12 | 本地 SQLite schema：入站事件、出站 actions、绑定缓存、健康事件、审计缓存 | ✅ | SqliteSendQueue + 幂等 dedup_key + 单元测试通过 |
| 13 | Serilog + Windows Event Log + 本地日志轮转 | 🔧 | Serilog 接入，Event Log/轮转待真实环境 |
| 14 | 配置文件加密：client_secret、agent URL、账号配置 | 🔧 | 配置加载框架在，加密待真实环境验证 |

---

## 2. 生产级自动化能力

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 15 | 环境健康检查：锁屏、分辨率、DPI、窗口可见、磁盘空间、企微进程 | 🔧 | IHealthSupervisor 接口实现已落，真实环境探测待 §0 准入 |
| 16 | 登录态检测和二维码截图/展示/过期处理 | 🔧 | 登录窗口骨架在，二维码识别待 OpenCV 节点常量 |
| 17 | 三层定位：FlaUI → Win32 坐标 → OpenCV 模板 | 🔧 | 自动化层封装在，节点常量 wecom_nodes.yaml 待准入验证 |
| 18 | 会话切换：搜索、候选结果识别、歧义暂停 | 🔧 | 骨架在，真实会话树探测待 §0 |
| 19 | 消息监听：通知触发 + 会话列表轮询兜底 + 快照去重 | 🔧 | MessageWatcher 编译通过；文本/附件抓取与真实监听待 §0 节点常量回填 |
| 20 | 文本发送：粘贴、发送、发送后确认、失败暂停 | 🔧 | IActionExecutor 接口在，执行链待真实环境 |
| 21 | 图片发送：下载、校验、发送、清理、回执 | 🔧 | 接口在，待真实环境 |
| 22 | 文件发送：下载、校验、发送、清理、回执 | 🔧 | 接口在，待真实环境 |
| 23 | 剪贴板保护：执行前备份、执行后恢复/清空、异常清理 | 🔧 | IClipboardGuard 接口在，执行待真实环境 |
| 24 | 限速：账号级、会话级、时间窗、失败熔断 | 🔧 | IRateLimiter 接口在，真实速率待压测 |

---

## 3. 服务端 callback / adapter

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 25 | 新增 `src/channels/wecom_personal_rpa/` 渠道模块，对齐微信客服 adapter/callback 模型 | ✅ | adapter/message/action_client/auth/connection/secret_crypto 七模块 + 68 单元测试通过 |
| 26 | `ChannelType` / `ChannelFactory` / SaaS 渠道配置支持 `wecom_personal_rpa` | ✅ | ChannelType 枚举 + channel_factory + channel_config 注册齐全 |
| 27 | 客户端注册：生成 `client_id`、加密密钥、最低版本、租户绑定 | 🔧 | db.create_client + secret_crypto.encrypt_secret 已落，注册管理 API 路由待补全端到端 |
| 28 | `POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`：消息/状态/回执统一回调，验签、nonce、防重放、去重、accepted | ✅ | 路由已接入 main.py，集成测试覆盖正确 HMAC/event_id 去重/签名失败 401 |
| 29 | `adapter.parse_message()`：RPA 回调消息转换为 `UnifiedMessage` | ✅ | message.parse_rpa_message + 单元测试 |
| 30 | 后台处理复用微信客服链路：channel_sessions、SessionMessageQueue、SessionRecordManager、agent_router | ✅ | _process_inbound_message 复刻 wecom_kf 链路 |
| 31 | `adapter.send_message(UnifiedResponse)`：把文本/图片/文件回复转换为 RPA actions | ✅ | adapter._build_actions + set_reply_context 由路由注入 |
| 32 | WebSocket actions 下发，轮询兜底，客户端断线时写服务端待发送队列 | ✅ | WS 路由 + action_client.deliver_actions + outbox + 集成测试覆盖离线落 outbox/在线直推 |
| 33 | action result callback：幂等回执、失败记录、审计 | ✅ | _handle_action_result + action_result_id 去重 + 集成测试覆盖幂等 |
| 34 | `GET /config`：限速、暂停状态、协议版本、最低客户端版本 | ✅ | wecom_personal_rpa_config 路由 |
| 35 | 文件短期签名下载接口：图片/文件 actions 使用 | ✅ | wecom_personal_rpa_download_file + hmac 自签 token |

服务端约束：

- 不 import 客户端代码。
- 不包含 UIA、Win32、OpenCV、鼠标键盘逻辑。
- 入站到 agent 的处理逻辑复用微信客服渠道结构，不另建独立 agent 调用链。
- 所有密钥加密存储，不在日志和 API 明文返回。

---

## 4. 管理、绑定、审计

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 36 | 管理端客户端列表：在线、版本、账号、最后心跳、状态 | ✅ | wecom_personal_rpa_admin 路由 + db.list_clients 已落；前端 `WecomPersonalRpaManager.vue` 客户端 Tab 已交付（编译通过，未真实联调） |
| 37 | 账号暂停/恢复：租户级、账号级、会话级 | ✅ | /pause /resume 路由 + db.set_account_status/set_binding_status 已落；前端账号/会话/租户三级暂停恢复按钮已接入 |
| 38 | 会话绑定：搜索键、展示名、stable_id、人工确认 | ✅ | db.get_or_create_binding/find_binding_by_search_key + router.check_conversation_authorization 已落；前端绑定 Tab 已交付 |
| 39 | 绑定失效处理：重名、群名变化、搜索歧义进入 `needs_review` | ✅ | check_conversation_authorization 已处理 pending/ambiguous → needs_review；前端复核（confirm）已接入 |
| 40 | 审计日志：入站消息、agent 回复、actions、执行结果、暂停恢复 | ✅ | db.write_audit/list_audit + 路由各分支审计写入已落；前端审计 Tab 已交付（类别/多维过滤 + payload 详情） |
| 41 | 员工授权和撤销：授权记录、撤销后立即停用、清理本地敏感缓存 | 🔧 | 撤销即 set_binding_status=invalid/paused 已支持（前端可暂停/置失效），清理本地敏感缓存待客户端协同 |

---

## 5. 安装、升级、运行环境

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 42 | WiX/MSIX 安装包和代码签名 | 🔧 | installer/wix/WeComRpa.wxs + .wixproj（WiX v5，ServiceInstall/ServiceControl + MajorUpgrade）+ scripts/build-msi.ps1 已落；实编译需装 WiX v5 + 签名证书 |
| 43 | 标准 Windows 镜像：企微版本、DPI、分辨率、窗口基线、远控方式 | ⬜ | 镜像文档 |
| 44 | 开机自启：计划任务拉起交互式 App，Supervisor 监督 | 🔧 | AutostartRegistrar（注册表 Run）+ ScheduledTaskHelper（schtasks 占位）+ install-service.ps1（Supervisor 服务自启）已落，真机验证待 |
| 45 | 灰度升级：版本检查、下载、安装、回滚 | ⬜ | 升级器 |
| 46 | 远程诊断包：日志、健康事件、模板版本、配置摘要脱敏导出 | ✅ | scripts/diagnostics.ps1 导出脱敏诊断包（日志+配置摘要剔除 secret/路径+服务/进程状态） |
| 47 | 模板和节点配置版本绑定：企微版本 → `wecom_nodes.yaml` | ⬜ | 配置管理 |

> **§5 状态说明**：工程化交付已大幅推进——WiX v5 MSI 骨架（#42 🔧）、开机自启三套骨架（#44 🔧）、远程诊断包 `diagnostics.ps1`（#46 ✅）、操作手册 `docs/操作手册.md` + 9 个 PowerShell 脚本（编译/测试/发布/装服务/诊断/服务端冒烟/准入探测/打 MSI）。剩余 ⬜（#43 标准镜像、#45 灰度升级、#47 节点版本绑定）依赖真实部署/灰度/签名环境。

---

## 6. 可观测性和告警

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 48 | 指标：客户端在线率、账号在线率、action 成功率、延迟、暂停数 | 🔧 | 审计日志已埋点（category=inbound_message/agent_reply/action_result），指标聚合/导出待补 |
| 49 | 告警：客户端离线、账号未登录、连续失败、误发风险、版本过低 | 🔧 | 状态上报链路在，告警规则引擎待补 |
| 50 | Trace：`source_type=wecom_personal_rpa` 接入现有记录链路 | ✅ | _process_inbound_message 用 SessionRecordManager.start_record(source_type=...) |
| 51 | 本地与服务端日志关联：request_id/action_id/event_id | 🔧 | event_id/request_id/action_id 已贯穿审计与回执，客户端本地日志关联待联调 |

---

## 7. 生产准入验收

| # | 任务 | 状态 | 产出 |
|---|------|------|------|
| 52 | 3-5 个内部账号连续运行 14 天 | ⬜ | 稳定性报告 |
| 53 | 人工抽样核对漏抓、误抓、误发 | ⬜ | 对账报告 |
| 54 | 文本/图片/文件 action 压测和失败演练 | ⬜ | 压测报告 |
| 55 | 异常演练：锁屏、企微退出、未登录、网络断开、agent 超时、客户端崩溃 | ⬜ | 演练记录 |
| 56 | 安全测试：签名、防重放、越权、文件 URL、日志脱敏 | ⬜ | 安全测试报告 |
| 57 | 升级/回滚演练 | ⬜ | 发布演练记录 |

> **§7 状态说明**：本节 6 项全部依赖真实账号 + 长期运行环境，本会话未实现，保持 ⬜。其中 #56 安全测试的部分场景（签名校验、防重放 nonce、错误信封脱敏）已由服务端单元测试 + 集成测试覆盖，但越权/文件 URL/日志脱敏的渗透级测试尚未执行。

生产准入门槛：

- 误发 0 次；出现 1 次即阻断上线。
- 文本/图片/文件 action 成功率 > 95%。
- 人工抽样漏抓率 < 3%；会话存档可用时 < 1%。
- 客户端崩溃 2 分钟内自动恢复或告警。
- 登录/桌面/绑定异常必须暂停。
- 100% action 有 request、执行、回执审计记录。

---

## 风险与回退

| 风险 | 触发条件 | 回退 |
|------|----------|------|
| PC 自动化不稳定 | SendInput + OpenCV 仍无法稳定执行 | 暂停开发，重新评估运行环境和自动化可行性 |
| 误发 | 发送到错误会话或重复发送 | 停止上线，强化绑定和发送前确认 |
| 漏抓率高 | 人工抽样漏抓率 > 3% | 优化监听；接入会话存档对账；仍不达标则不生产 |
| 桌面不稳定 | 锁屏、RDP 断开、窗口不可见频繁发生 | 更换云桌面/VNC/物理机方案 |
| 账号异常 | 触发企微风控或登录限制 | 停止该账号托管，复盘频率和环境 |
| .NET 客户端维护成本高 | 安装升级/自动化库维护超预期 | 保留协议不变，替换客户端 runtime |

---

## 不在本计划内

- Hook / DLL 注入。
- 协议逆向、iPad 协议、Cookie 模拟。
- PC 多开企业微信。
- 自动加好友、朋友圈、点赞、评论、群发营销。
- 将 RPA 自动化逻辑塞回 aid-work-agent 后端。
