# 企业微信个人账号 RPA 独立客户端生产级技术方案调研

> **历史调研（交付结论已废弃）**：其中 WiX/MSIX 安装包建议已于 2026-07-13 废弃。当前唯一交付方式为 Release build 或 `clients/wecom-personal-rpa/scripts/publish.ps1` 生成 EXE 目录；不得按本文恢复安装包流程。

> 关联设计：[wecom-personal-rpa-design.md](../system/wecom-personal-rpa-design.md)
> 关联计划：[plan-wecom-personal-rpa.md](../plans/plan-wecom-personal-rpa.md)
> 调研日期：2026-06-22
> 状态：📋 待开发

---

## 1. 结论

首版按生产可用设计。生产客户端技术栈：

| 层 | 选型 | 理由 |
|----|------|------|
| 客户端主运行时 | .NET 8 | Windows 原生集成、强类型、可发布单文件、适合长期运维 |
| 交互式 UI | WPF + 托盘 App | 运行在用户桌面会话，展示二维码、暂停恢复、状态 |
| 监督进程 | Windows Service + 计划任务 | 监督交互式 App 存活，处理开机自启、崩溃告警 |
| UI 自动化 | FlaUI UIA3 / UIA2 | .NET UIA 封装成熟，可访问控件树和 Control Pattern |
| Win32 控制 | P/Invoke + SendInput + 窗口枚举 | 前台窗口、输入、DPI、锁屏、剪贴板和进程控制 |
| 图像识别 | OpenCvSharp | DirectUI 控件不可见时做局部模板匹配 |
| 本地可靠队列 | SQLite + Dapper/EF Core | 单账号本地持久化、断网恢复、幂等去重 |
| 通信 | HttpClientFactory + Polly + WebSocket/SignalR | 异步 actions、重试、熔断、心跳 |
| 日志与指标 | Serilog + Windows Event Log + OpenTelemetry | 本地诊断和服务端统一观测 |
| 构建部署 | .NET 8 Release build / 自包含 EXE publish | 当前实施结论；复制完整发布目录部署 |

生产 RPA 的难点不只是“能点击”，还包括 Windows 会话、升级回滚、崩溃恢复、事件日志、安装包、长期守护、强约束状态机和可观测性，这些是 .NET Windows 客户端的优势区。

---

## 2. 生产级首版架构

```
aid-work-agent
  ├─ wecom_personal_rpa callback / adapter
  ├─ actions 队列 / 状态 / 审计
  └─ agent 推理和工具调用
          ▲
          │ HTTPS callback + WebSocket actions
          ▼
WeCom Personal RPA Client (.NET 8)
  ├─ Client.App（WPF/托盘，交互式桌面）
  │   ├─ 登录二维码展示
  │   ├─ 暂停/恢复
  │   └─ RPA 执行入口
  ├─ Client.Core
  │   ├─ 状态机
  │   ├─ 本地 SQLite 队列
  │   ├─ HMAC 协议
  │   ├─ 限速和重试
  │   └─ 审计缓存
  ├─ Client.Automation
  │   ├─ FlaUI/UIA3
  │   ├─ Win32 SendInput
  │   └─ OpenCvSharp 模板匹配
  └─ Client.Supervisor
      ├─ Windows Service
      ├─ 计划任务拉起交互式 App
      └─ 崩溃/离线上报
```

关键点：

- 真正执行 RPA 的进程必须运行在交互式用户会话，不能放在 Windows Service 的 Session 0。
- Supervisor 只负责监督、拉起、告警，不直接操作企业微信 UI。
- 客户端和 agent 只共享渠道回调 JSON 协议，不共享代码对象。
- 首版直接采用异步 actions 协议，避免 agent 长耗时阻塞客户端。

---

## 3. 生产首版必须具备的稳定性能力

### 3.1 状态机

客户端状态必须由状态机控制，任何不确定状态都暂停自动发送：

```
starting
  → checking_environment
  → need_login / running / paused_error
  → running
  → paused_by_user / paused_by_server / paused_error / recovering
```

只有 `running` 状态允许执行出站 action。以下情况必须自动暂停：

- 企业微信未登录、二维码过期、账号异常。
- Windows 锁屏、分辨率/DPI 不符合基线、窗口不可见。
- 搜索结果歧义、会话绑定失效、当前会话无法确认。
- 同一会话连续发送失败 2 次。
- 客户端版本低于服务端最低要求。
- 本地队列损坏、磁盘空间不足、文件校验失败。

### 3.2 三层自动化引擎

| 层 | 技术 | 用途 | 失败处理 |
|----|------|------|----------|
| L1 | FlaUI UIA3/UIA2 | 控件树、标题、输入框、按钮 | 降级 L2 |
| L2 | Win32 窗口相对坐标 + SendInput | 前台窗口内稳定点击、粘贴、快捷键 | 降级 L3 |
| L3 | OpenCvSharp 局部模板匹配 | DirectUI/自绘按钮、二维码区域、发送按钮 | 暂停 |

约束：

- 坐标必须基于企业微信窗口 client area，不使用全屏绝对坐标。
- 模板匹配必须限制在窗口局部 region。
- 每个 action 执行前后都做确认：目标会话、输入框焦点、发送按钮状态、发送后消息出现。
- 所有 UI 节点、坐标、模板阈值放在 `wecom_nodes.yaml`，支持热更新和版本绑定。

### 3.3 复用微信客服渠道处理链路

个人企微 RPA 的入站链路复用微信客服渠道设计：

```
RPA 客户端 callback
  → channel_routes 验签/去重
  → adapter.parse_message(raw)
  → UnifiedMessage
  → channel_session_manager.get_or_create_session()
  → agent_router / SessionMessageQueue / SessionRecordManager
  → UnifiedResponse
  → adapter.send_message(response)
  → actions 下发给 RPA 客户端
```

与微信客服的对应关系：

| 微信客服 | 个人企微 RPA |
|----------|--------------|
| 微信平台回调 `wecom_kf/callback` | RPA 客户端回调 `wecom_personal_rpa/callback` |
| `sync_msg` 拉取完整消息 | 客户端回调直接携带完整消息 |
| `parse_kf_message` 转 `UnifiedMessage` | `parse_rpa_message` 转 `UnifiedMessage` |
| `WeComKfAdapter.send_message()` 调微信客服 API | `WeComPersonalRpaAdapter.send_message()` 下发 actions 给客户端 |
| 微信客服 `send_msg` 完成发送 | 客户端在企业微信 PC 内执行发送 |

### 3.4 异步可靠协议

生产协议不使用同步阻塞回复：

1. 客户端通过 callback 上报入站消息，服务端返回 `accepted`。
2. 服务端后台调用 agent，生成 actions。
3. 服务端通过 `adapter.send_message()` 把 `UnifiedResponse` 转为 actions。
4. 客户端 WebSocket 接收 actions，断线后轮询兜底。
5. 客户端写入 SQLite，再按账号串行执行。
6. 执行结果通过 callback 上报服务端，服务端按 `action_result_id` 幂等。

本地队列状态：

| 状态 | 含义 |
|------|------|
| `pending` | 已接收，未执行 |
| `running` | 当前正在执行 |
| `succeeded` | 已执行并成功上报 |
| `retryable` | 网络/临时 UI 异常，可重试 |
| `failed` | 永久失败，需要人工处理 |
| `paused` | 账号或会话暂停，等待恢复 |

### 3.5 会话绑定与误发防护

误发是 P0 风险，首版必须有会话绑定：

- 首次发现联系人/群，只允许进入 `needs_review`，不能自动发。
- 管理端人工确认 `account_id + conversation_id + search_key + display_name`。
- 发送前检查当前会话标题/头像区域/最近消息摘要是否匹配绑定。
- 搜索结果不唯一时暂停该会话。
- 群名或备注名变化后进入重新确认。

### 3.6 可观测性和审计

首版必须包含：

- 本地滚动日志：Serilog，敏感字段脱敏。
- Windows Event Log：启动、崩溃、暂停、恢复、登录异常。
- 服务端指标：客户端在线、账号在线、action 成功率、延迟、暂停数量。
- 审计：入站消息、agent 请求、actions、执行结果、文件下载、暂停恢复。
- 远程诊断包：可手动导出最近日志、配置摘要、模板版本、健康事件，不包含二维码和密钥。

---

## 4. 客户端工程结构

```
clients/wecom-personal-rpa/
├── WeComPersonalRpaClient.sln
├── src/
│   ├── Client.App/
│   │   ├── App.xaml
│   │   ├── Tray/
│   │   └── Views/
│   ├── Client.Core/
│   │   ├── AgentApi/
│   │   ├── Queue/
│   │   ├── StateMachine/
│   │   ├── Security/
│   │   ├── Observability/
│   │   └── Config/
│   ├── Client.Automation/
│   │   ├── FlaUi/
│   │   ├── Win32/
│   │   ├── Vision/
│   │   └── WeCom/
│   ├── Client.Supervisor/
│   └── Client.Tests/
├── assets/
│   ├── wecom_nodes.yaml
│   └── templates/
├── installer/
│   └── wix/
└── docs/
```

---

## 5. 服务端配套要求

服务端新增 `wecom_personal_rpa` channel，但不包含 RPA 自动化逻辑。

必须提供：

- 客户端注册和密钥生成。
- 客户端配置下发：协议版本、最低客户端版本、限速、暂停状态。
- RPA 客户端 callback：消息、状态、action result。
- actions 下发：`adapter.send_message()` 写队列或 WebSocket 投递。
- 客户端/账号/会话状态 API。
- 管理端暂停恢复、会话绑定、审计查询。

服务端复用现有能力：

- `ChannelSessionManager` 创建渠道会话。
- `MessageDeduplicator` 做 `client_id + event_id` 去重。
- `SessionMessageQueue` 做同一 session 串行化。
- `agent_router` 路由数字员工。
- `SessionRecordManager` 记录 `source_type=wecom_personal_rpa`。
- `UnifiedResponse.downloadable_files` 在 `WeComPersonalRpaAdapter.send_message()` 中转换为 `send_file` actions。

---

## 6. 编码前准入验证

准入验证不改变生产首版范围。它只用于确认 PC RPA 是否值得进入开发。

| 验证项 | 成功标准 |
|--------|----------|
| UIA 探测 | FlaUI 能识别主窗口、输入框、至少部分文本区域 |
| Win32 输入 | SendInput 能稳定聚焦窗口、粘贴文本、点击发送 |
| OpenCV 模板 | 固定窗口和 DPI 下能稳定识别关键按钮/二维码区域 |
| 登录态 | 能区分已登录、未登录、二维码过期、账号异常 |
| 桌面保活 | 远控断开、重启后能恢复交互式会话 |

失败处理：

- UIA 不稳定：L2/L3 前置，不影响继续。
- Win32/模板也不稳定：不进入客户端开发，先重新评估运行环境和自动化可行性。
- 桌面保活不稳定：先解决运行环境，不写业务代码。

---

## 7. 生产准入测试

| 指标 | 门槛 |
|------|------|
| 连续运行 | 3-5 个内部账号连续 14 天 |
| 误发 | 0 次 |
| 文本/图片/文件 action 成功率 | > 95% |
| 人工抽样漏抓率 | < 3% |
| 已开通会话存档漏抓率 | < 1% |
| 客户端崩溃恢复 | 2 分钟内恢复或告警 |
| 审计完整性 | 100% action 有 request、执行、回执 |
| 异常暂停 | 登录/桌面/绑定异常必须暂停 |

出现任何误发，生产准入失败。

---

## 8. 风险与备选

| 风险 | 对策 |
|------|------|
| PC DirectUI 导致控件不可见 | FlaUI + Win32 + OpenCV 三层定位 |
| Windows Service 无法操作桌面 | 交互式 App 执行 RPA，Service 只监督 |
| 桌面会话不稳定 | 标准 Windows 镜像、VNC/云桌面保活、锁屏暂停 |
| 误发 | 人工绑定、发送前确认、歧义暂停、误发零容忍 |
| 漏抓 | 通知触发 + 轮询兜底 + 会话存档对账 |
| 客户端升级破坏稳定性 | 版本绑定、灰度升级、回滚、服务端最低版本控制 |

---

## 9. 参考资料

- [Microsoft UI Automation](https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32)
- [Microsoft UI Automation Overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview)
- [FlaUI](https://github.com/FlaUI/FlaUI)
