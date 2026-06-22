# 企业微信个人账号 RPA 独立客户端设计

> 关联调研：[wecom-personal-account-rpa-research.md](../research/wecom-personal-account-rpa-research.md)、[wecom-personal-rpa-client-implementation-research.md](../research/wecom-personal-rpa-client-implementation-research.md)
> 开发计划：[plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)
> 创建日期：2026-06-16
> 更新日期：2026-06-22
> 状态：📋 待开发

---

## 0. 核心结论

个人企业微信账号 RPA 不作为 aid-work-agent 服务端内部模块实现，而是设计为一个 **独立客户端**：

- 客户端负责登录个人企业微信账号、展示扫码二维码、维护登录态、监控新消息。
- 客户端把新消息转成标准渠道回调事件，POST 到 aid-work-agent 的 `wecom_personal_rpa` callback 入口。
- aid-work-agent 只负责会话路由、agent 推理、工具调用和生成回复结果。
- 客户端接收服务端通过 adapter 下发的结构化 actions 后，在本机企业微信客户端内发送文本、图片、文件等内容。
- 客户端与 agent 完全解耦：可同仓开发、独立目录、独立进程、独立部署、独立技术栈、独立升级。

该设计的边界是：**agent 是服务端推理系统，个人企微 RPA 客户端是一个 channel runtime**。两者只通过明确的渠道 callback、WebSocket 下行和消息协议协作，不共享进程、不共享数据库事务、不要求同一种语言或框架。

首版即按生产可用实现，必须同时具备稳定自动化、异步可靠队列、会话绑定、异常暂停、审计、监控、版本管理、安装部署和回滚能力。技术准入验证只用于确认 Windows RPA 可行性，不作为线上能力分期。

---

## 1. 设计目标与范围

### 1.1 目标

实现一个面向个人企业微信账号的独立 RPA 客户端，支持：

- 个人企微账号扫码登录和登录状态监控。
- 新消息监听：单聊、内部群、外部联系人单聊、外部联系人群。
- 将新消息投递给 aid-work-agent，由 agent 生成回复。
- 接收服务端下发的文本、图片、文件 actions，并由客户端直接发给对应会话。
- 支持人工暂停 / 恢复托管，避免员工本人操作和 RPA 操作冲突。
- 支持多客户端实例，每个实例绑定一个个人企微账号和一个桌面环境。

### 1.2 非目标

- 不在 agent 服务端内嵌 PC 自动化逻辑。
- 不让 agent 直接操作企业微信窗口、鼠标、键盘或剪贴板。
- 不把 RPA 客户端强行绑定到 agent 后端技术栈。
- 不做 Hook / DLL 注入 / 协议逆向 / iPad 协议 / mmtls MITM。
- 首版不做自动通过好友申请、朋友圈、群发营销、多开企业微信。

### 1.3 关键架构原则

| 原则 | 说明 |
|------|------|
| 客户端自治 | 登录、扫码、桌面健康、消息监听、发送动作都在客户端内闭环 |
| 服务端无 UI 依赖 | aid-work-agent 不依赖 Windows 桌面、企业微信进程或 RPA 库 |
| 协议解耦 | 双方通过渠道 callback、WebSocket 下行和标准消息协议协作，不共享内部对象 |
| 技术栈独立 | 客户端使用最适合 Windows RPA 稳定性的栈，不强制复用 agent 后端栈 |
| 渠道化接入 | 个人企微 RPA 是 aid-work-agent 的一个独立 channel，与钉钉、飞书、企微客服平级 |
| 可暂停可审计 | 所有自动回复必须可暂停、可追踪、可人工接管 |
| 生产首版 | 首次交付即包含队列、监控、审计、升级、回滚、异常暂停和多媒体 actions |

---

## 2. 顶层架构

### 2.1 总体拓扑

```
┌──────────────────────────────────────────────────────────────┐
│ aid-work-agent 服务端                                         │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ WeCom Personal RPA Callback / Adapter                    │  │
│  │ - 接收 RPA 客户端回调                                    │  │
│  │ - 做租户、账号、会话路由                                 │  │
│  │ - 调用 Agent.process_message                             │  │
│  │ - adapter.send_message 下发回复 actions                  │  │
│  └───────────────────────┬────────────────────────────────┘  │
│                          │                                    │
│  ┌───────────────────────▼────────────────────────────────┐  │
│  │ master_agent / tools / knowledge base / subagents        │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────▲───────────────────────────────────┘
                           │ HTTPS / WebSocket
                           │ 标准 channel 协议
┌──────────────────────────┴───────────────────────────────────┐
│ WeCom Personal RPA Client（独立客户端，可同仓独立目录）          │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Client Core                                             │  │
│  │ - 账号登录/扫码二维码/登录态                             │  │
│  │ - 消息监听与去重                                         │  │
│  │ - 回调 agent channel callback                            │  │
│  │ - 执行 agent 返回的发送动作                               │  │
│  │ - 本地任务队列、限速、暂停恢复                            │  │
│  └───────────────────────┬────────────────────────────────┘  │
│                          │                                    │
│  ┌───────────────────────▼────────────────────────────────┐  │
│  │ WeCom Desktop Automation                                │  │
│  │ - 企业微信 PC 客户端                                     │  │
│  │ - FlaUI/UIA3 / Win32 SendInput / OpenCvSharp             │  │
│  │ - 固定 Windows 桌面会话                                  │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 模块边界

| 模块 | 所属 | 职责 | 不负责 |
|------|------|------|--------|
| RPA Client | 独立客户端 | 登录、监听、发送、桌面健康、本地队列 | LLM 推理、工具调用、知识库 |
| Channel Callback / Adapter | aid-work-agent | 接收客户端回调、解析 UnifiedMessage、路由会话、调用 agent、通过 adapter.send_message 下发回复 | 控制鼠标键盘、维护企微登录态 |
| Agent Core | aid-work-agent | 生成回复、调用工具、保存会话记录 | 了解 RPA 细节 |
| 管理控制台 | 可复用现有前端或客户端 UI | 绑定账号、查看状态、暂停恢复 | 直接绕过协议操作客户端 |

### 2.3 同仓目录建议

客户端代码可以放在同一代码仓库，但作为独立工程维护：

```
clients/
└── wecom-personal-rpa/
    ├── README.md
    ├── WeComPersonalRpaClient.sln
    ├── src/
    │   ├── Client.App/              # WPF/托盘 UI，运行在交互式桌面会话
    │   ├── Client.Core/             # 状态机、队列、协议、限速
    │   ├── Client.Automation/       # FlaUI/UIA3、Win32、OpenCV 自动化
    │   ├── Client.Supervisor/       # Windows Service/计划任务监督进程
    │   └── Client.Tests/
    ├── assets/
    │   ├── wecom_nodes.yaml
    │   └── templates/
    ├── configs/
    │   └── client.example.yaml
    └── tests/
```

说明：

- `clients/wecom-personal-rpa/` 不 import `src/core/agent.py`，只通过渠道 callback 和 WebSocket 与服务端通信。
- 服务端新增 `src/channels/wecom_personal_rpa/`，复用微信客服渠道的 adapter/callback 处理模型，只实现渠道协议、路由和客户端下行投递。
- 客户端依赖可以独立锁定，不受 aid-work-agent 后端依赖约束。

---

## 3. 客户端职责设计

### 3.1 客户端核心能力

| 能力 | 说明 |
|------|------|
| 账号登录 | 启动企业微信 PC 客户端，检测登录态，未登录时提取或截图二维码并展示 |
| 登录状态监控 | 定期检测是否掉线、是否需要重新扫码、是否被限制 |
| 新消息监听 | 监听系统通知、未读会话列表或轮询最近消息，生成入站事件 |
| 入站投递 | 将新消息通过渠道 callback 发给 aid-work-agent |
| 回复执行 | 根据服务端下发的 actions 发送文本、图片、文件等内容 |
| 本地队列 | 同一账号发送动作串行执行，失败可重试或进入人工处理 |
| 人工接管 | 本地暂停、服务端暂停、异常自动暂停 |
| 健康检查 | 检查窗口、分辨率、DPI、锁屏、剪贴板、企微进程 |
| 审计上报 | 上报登录状态、消息处理状态、发送结果和错误原因 |

### 3.2 生产级技术栈

首版使用 **.NET 8 Windows 客户端**。原因：

- Windows UI Automation、Win32 窗口管理、剪贴板、输入注入、Windows Service、WPF/托盘 UI 都是 .NET 原生优势区。
- `FlaUI` 基于 UIA2/UIA3，适合做结构化控件访问和回归定位。
- `SendInput`、Win32 API、窗口句柄、进程监督可强类型封装，减少脚本式 RPA 的不可控行为。
- .NET 单文件发布、签名、安装器、Windows 事件日志、Serilog、OpenTelemetry 更适合生产部署。

| 组件 | 推荐选型 | 用途 |
|------|----------|------|
| 客户端主运行时 | .NET 8 Worker + WPF/托盘 App | 交互式桌面内运行 RPA、展示二维码、暂停恢复 |
| UI 自动化 | FlaUI UIA3，必要时 UIA2 兜底 | 读取窗口、控件、文本、可点击元素 |
| Win32 控制 | P/Invoke `SetForegroundWindow`、`SendInput`、窗口枚举、DPI API | 窗口置顶、输入、分辨率/DPI 检查 |
| 图像识别 | OpenCvSharp + 模板资源 | DirectUI 控件不可见时做局部模板匹配 |
| 本地存储 | SQLite + EF Core / Dapper | 入站事件、出站 actions、绑定缓存、审计缓存 |
| 通信 | `HttpClientFactory` + Polly + WebSocket/SignalR 客户端 | 发起渠道 callback、接收服务端下发 actions |
| 可观测性 | Serilog + Windows Event Log + OpenTelemetry exporter | 本地日志、服务端指标、链路追踪 |
| 安装部署 | MSIX / WiX Toolset + 代码签名 | 可重复安装、升级、回滚 |
| 进程托管 | 交互式 RPA App + Windows Supervisor Service + 计划任务 | 避免 Session 0 问题，同时支持监督和重启 |

**Session 0 约束**：RPA 自动化不能放在纯 Windows Service 中执行。Windows Service 运行在 Session 0，不能可靠操作用户桌面。生产设计采用双进程：

- `Client.App`：运行在已登录 Windows 用户会话中，真正执行企业微信 UI 自动化。
- `Client.Supervisor`：Windows Service 或计划任务监督进程，负责检测 `Client.App` 是否存活、拉起、记录崩溃、上报离线。

### 3.3 登录和二维码处理

客户端负责完整登录流程：

1. 启动或连接企业微信 PC 客户端。
2. 判断当前账号是否已登录。
3. 未登录时，定位登录二维码区域并截图。
4. 通过本地 UI 或管理控制台展示二维码。
5. 轮询登录状态，扫码成功后绑定当前账号身份。
6. 登录失败、二维码过期、账号异常时上报状态。

二维码传递方式：

- 客户端本地 WPF/托盘 UI 展示二维码。
- 客户端可把二维码图片以短期凭证上传到 aid-work-agent 管理端临时文件接口，由管理控制台展示。
- 二维码状态、过期时间、扫码结果通过 `/events/status` 上报。

二维码属于敏感登录凭证，必须短期有效、禁止长期存储，日志中不得打印二维码内容或图片路径。

### 3.4 消息监听

客户端按优先级使用三种方式：

| 优先级 | 方式 | 说明 |
|------|------|------|
| P0 | Windows 通知 / 未读角标触发 | 低频、低成本，减少轮询 |
| P1 | 会话列表轮询 | 每 3-10 秒扫描未读会话和最后消息摘要 |
| P2 | 会话存档对账 | 已开通官方存档时用于补漏，不作为客户端实时监听前提 |

客户端生成的入站消息必须包含本地去重键：

```json
{
  "event_id": "evt_20260622_xxx",
  "client_id": "client_001",
  "account_id": "wecom_account_001",
  "conversation_id": "binding_abc",
  "conversation_type": "external_user",
  "sender": {
    "display_name": "张三",
    "stable_id": null
  },
  "message": {
    "type": "text",
    "text": "你好",
    "attachments": []
  },
  "occurred_at": "2026-06-22T10:00:00+08:00"
}
```

### 3.5 回复执行

agent 不直接发送企微消息。服务端把 `UnifiedResponse` 通过 `WeComPersonalRpaAdapter.send_message()` 转换为 actions，并下发给客户端执行：

```json
{
  "request_id": "req_xxx",
  "session_id": "wecom_personal_rpa:account_001:binding_abc",
  "actions": [
    {
      "type": "send_text",
      "text": "您好，我已经收到，会尽快处理。"
    },
    {
      "type": "send_file",
      "file_url": "https://agent.example.com/files/xxx",
      "filename": "报价单.xlsx"
    }
  ]
}
```

客户端执行规则：

- 同一会话 actions 顺序执行。
- 同一账号全局串行发送，避免多个会话并发抢占窗口。
- 文件类 action 先下载到客户端本地临时目录，再通过企业微信 PC 客户端发送。
- 图片、文件下载地址必须带短期签名，客户端不得持久化敏感文件。
- 任一 action 失败时，上报失败结果，并按策略停止后续 action 或进入人工处理。

---

## 4. aid-work-agent 服务端改造

### 4.1 新增 channel 入口

服务端新增个人企微 RPA channel，但只处理协议，不包含 RPA 自动化。整体处理模型对齐微信客服渠道：

```
RPA 客户端回调
  → channel_routes callback 入口验签/去重
  → WeComPersonalRpaAdapter.parse_message()
  → UnifiedMessage
  → channel_session_manager.get_or_create_session()
  → agent_router.get_agent().process_message_sync()
  → UnifiedResponse
  → WeComPersonalRpaAdapter.send_message()
  → 下发 action 给 RPA 客户端执行
```

它与微信客服的差异只在渠道端能力来源：

- 微信客服：微信平台回调通知，后端调用 `sync_msg` 拉取完整消息，再通过微信客服 API `send_msg` 回复。
- 个人企微 RPA：RPA 客户端直接回调完整消息，后端通过客户端长连接/待发送队列把 `send_text/send_image/send_file` actions 下发给客户端。

```
src/channels/wecom_personal_rpa/
├── __init__.py
├── adapter.py             # ChannelAdapter 实现：parse_message / send_message
├── message.py             # RPA 回调消息 ↔ UnifiedMessage
├── action_client.py       # 下发 actions 到在线 RPA 客户端 / 队列
├── schemas.py             # 回调事件、回复 action、状态上报
├── router.py              # account_id + conversation_id → session_id
├── auth.py                # 客户端签名、token、时间戳校验
└── connection.py          # 客户端 WebSocket 连接和心跳
```

### 4.2 回调与下行接口

客户端作为渠道运行时，向服务端发起回调；这与微信客服 `POST /t/{tenant_id}/wecom_kf/callback/{config_id}` 的定位一致。

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}` | RPA 客户端回调新消息、状态事件、发送结果 |
| WS | `/t/{tenant_id}/wecom_personal_rpa/ws/{config_id}` | RPA 客户端主动连入，接收服务端下发 actions |
| GET | `/api/v1/channels/wecom-personal-rpa/config` | 客户端拉取配置、限速、托管状态、最低版本 |
| GET | `/api/v1/channels/wecom-personal-rpa/files/{id}` | 客户端下载待发送附件 |

回调入口必须快速返回 `success` / `accepted`，不阻塞等待 agent 处理完成。后台处理流程复用微信客服渠道：

1. callback 验签、校验租户配置、去重。
2. `adapter.parse_message(raw)` 转为 `UnifiedMessage`。
3. 创建/获取 `channel_sessions`，记录用户消息。
4. 调用 agent，生成回复文本和文件产物。
5. 构造 `UnifiedResponse`。
6. 调用 `adapter.send_message(response)`。
7. adapter 把 response 转为 RPA actions，写入服务端待发送队列或通过 WebSocket 下发客户端。
8. 客户端执行完成后再次通过 callback 上报 `action_result` 事件。

### 4.3 会话路由

`session_id` 必须包含账号和已确认会话绑定，避免不同个人账号下同名联系人串话：

```python
class WeComPersonalRpaRouter:
    def route(self, account_id: str, conversation_id: str, stable_id: str | None = None) -> str:
        route_key = stable_id or conversation_id
        return f"wecom_personal_rpa:{account_id}:{route_key}"
```

规则：

- `account_id` 必须由服务端配置或绑定表校验，不能只信任客户端自报。
- `conversation_id` 来自客户端本地绑定记录，首次发现重名或无法确认时不得自动发送。
- 如果后续拿到 `external_userid`、`userid`、`room_id`，可作为 `stable_id` 补充；首版不把这些官方稳定 ID 作为强依赖。

### 4.4 回复结构

服务端把 agent 内部响应转换为客户端可执行 action：

| agent 产物 | action | 说明 |
|------------|--------|------|
| 文本回复 | `send_text` | 长文本由服务端或客户端按渠道限制拆分 |
| 图片 artifact | `send_image` | 返回短期签名 URL |
| 文件 artifact | `send_file` | 返回短期签名 URL + 文件名 |
| 不回复 | `noop` | 用于仅记录、不自动回复 |
| 转人工 | `handoff` | 客户端暂停该会话或账号 |

服务端不得返回敏感明文 token、长期文件路径或本地服务器绝对路径。

---

## 5. 客户端与服务端协议

### 5.1 鉴权与签名

所有客户端请求必须带签名：

```
X-Client-Id: client_001
X-Timestamp: 1782100000
X-Nonce: nonce_xxx
X-Signature: hmac_sha256(client_id + timestamp + nonce + body, client_secret)
```

校验规则：

- `client_id` 必须存在且处于启用状态。
- 时间戳偏移超过 5 分钟拒绝。
- `nonce` 10 分钟内不可重复。
- HMAC 使用常量时间比较。
- `client_secret` 加密存储，不在 API、日志、错误信息中明文返回。

### 5.2 幂等

客户端必须为每条消息生成稳定 `event_id`。服务端以 `client_id + event_id` 去重：

```python
dedup_key = f"wecom_personal_rpa:event:{client_id}:{event_id}"
```

客户端必须为每个发送 action 回执生成 `action_result_id`，服务端同样去重，避免网络重试导致重复记录。

### 5.3 错误语义

| 错误码 | 含义 | 客户端处理 |
|--------|------|------------|
| `auth_failed` | 签名或 token 错误 | 停止请求并报警 |
| `client_disabled` | 客户端被禁用 | 暂停本地托管 |
| `account_paused` | 账号暂停 | 不再投递消息给 agent，可继续上报健康 |
| `conversation_needs_review` | 会话需要人工绑定 | 暂停该会话自动发送 |
| `agent_timeout` | agent 超时 | 本地稍后重试或转人工 |
| `unsupported_action` | 客户端不支持该回复类型 | 上报失败，服务端降级文本或转人工 |

---

## 6. 客户端自动化实现

### 6.1 Windows 环境约束

| 约束 | 要求 |
|------|------|
| 账号隔离 | 一个个人企微账号绑定一个 Windows 用户 / VM / 物理机 |
| 桌面会话 | 保持可见桌面，禁止锁屏后继续执行自动化 |
| 分辨率 | 固定 1920x1080，DPI 100%，企微窗口固定尺寸 |
| 版本 | 企业微信 PC 版本先验证再升级 |
| 输入独占 | 客户端执行发送任务时独占鼠标、键盘、剪贴板 |
| 网络出口 | 固定办公网或固定公网出口，减少异常登录 |

### 6.2 三层自动化策略

```
Layer 1: FlaUI UIA3/UIA2 读取控件树
    ↓ 失败
Layer 2: Win32 窗口相对坐标 + SendInput + 剪贴板输入
    ↓ 失败
Layer 3: OpenCvSharp 模板匹配 + 截图定位
```

所有节点、坐标、模板路径放在客户端配置中：

```yaml
main_window:
  class_name: "WeWorkWindow"
  title_contains: "企业微信"

message_input:
  strategy: flaui
  control_type: EditControl
  fallback:
    strategy: coordinate
    offset: [400, 580]

send_button:
  strategy: template_match
  template: assets/templates/send_btn.png
```

### 6.3 发送文本

```
1. 定位企业微信主窗口并置顶
2. 切换到目标会话
3. 定位输入框
4. 写入剪贴板并 Ctrl+V 粘贴
5. 点击发送或按 Enter
6. 检查发送结果并上报 action result
```

### 6.4 发送图片和文件

```
1. 根据 action 的短期 URL 下载文件到本地临时目录
2. 校验文件大小、MIME、扩展名
3. 切换到目标会话
4. 通过文件选择框或剪贴板路径发送
5. 等待上传完成
6. 点击发送并上报结果
7. 删除本地临时文件
```

### 6.5 限速与冲突控制

- 单账号发送任务串行执行。
- 单账号单分钟发送不超过 5 条。
- 单账号单日自动发送不超过 100 条，默认可配置。
- 发送失败连续 2 次后暂停该会话。
- 登录态异常、搜索结果歧义、桌面锁屏时暂停账号。
- 员工或管理员可通过控制台或客户端 UI 手动暂停。

---

## 7. 数据模型

### 7.1 服务端表

服务端只保存 channel 配置、绑定关系、审计和消息处理状态：

```sql
CREATE TABLE wecom_rpa_clients (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE wecom_rpa_accounts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    display_name TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    paused_reason TEXT,
    last_login_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE wecom_rpa_conversation_bindings (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    conversation_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    search_key TEXT NOT NULL,
    stable_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_verified_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 7.2 客户端本地存储

客户端可用 SQLite 保存：

- `local_messages`：已监听消息快照和 `event_id`。
- `send_queue`：待执行 actions。
- `conversation_cache`：本地会话识别结果和搜索键缓存。
- `health_events`：登录态、桌面状态、企微进程状态。

客户端本地数据库不作为长期业务数据源，只用于断网恢复、去重和本地诊断。

---

## 8. 管理与运维

### 8.1 管理功能

| 功能 | 位置 | 说明 |
|------|------|------|
| 客户端注册 | aid-work-agent 管理端 | 生成 `client_id` 和密钥 |
| 二维码扫码 | 客户端 UI + 管理端短期展示 | 完成个人企微登录 |
| 账号状态 | 管理端 + 客户端 UI | 在线、离线、需扫码、异常、暂停 |
| 会话绑定 | 管理端 | 人工确认联系人/群和业务会话的对应关系 |
| 暂停恢复 | 管理端 + 客户端 UI | 账号级、会话级暂停 |
| 审计查看 | 管理端 | 入站消息、agent 回复、发送结果、错误 |

### 8.2 关键指标

| 指标 | 告警阈值 | 含义 |
|------|---------|------|
| `wecom_rpa_client_online_ratio` | < 95% | 客户端在线率 |
| `wecom_rpa_account_online_ratio` | < 95% | 账号登录在线率 |
| `wecom_rpa_message_delivery_latency_p95` | > 30s | 从监听到 agent 返回的延迟 |
| `wecom_rpa_action_success_rate` | < 95% | 客户端发送动作成功率 |
| `wecom_rpa_duplicate_event_count` | 异常增长 | 消息监听重复 |
| `wecom_rpa_paused_account_count` | > 0 | 异常暂停账号数 |

---

## 9. 安全与合规

### 9.1 敏感信息处理

- 客户端密钥在服务端加密存储。
- 二维码图片短期展示，不长期保存。
- 文件下载 URL 必须短期签名。
- 日志不得记录密钥、二维码、完整敏感文件 URL。
- 原始聊天内容按项目数据最小化原则处理，不在客户端和服务端重复长期保存。

### 9.2 员工授权

个人账号接入前必须获得员工明确授权，授权内容至少包括：

- 公司会在指定 Windows 客户端上登录其企业微信 PC 端。
- AI 会读取和处理该账号收到的消息。
- AI 可能以该账号身份发送回复。
- 员工可随时暂停或撤销授权。
- 撤销授权后客户端必须停止托管并清理本地敏感缓存。

### 9.3 明确禁止

- Hook / DLL 注入 / 协议逆向。
- 多开企业微信。
- 批量加好友、群发广告、朋友圈营销动作。
- 绕过平台风控或规避检测的实现。
- 未经授权接入员工个人账号。

---

## 10. 生产级首版落地范围

首版一次性交付生产可用能力。允许内部按工程顺序开发，但对外只有一个生产准入版本。

### 10.1 编码前准入验证

编码前必须完成短周期准入验证，验证结果只决定技术细节，不降低首版范围：

| 验证项 | 成功标准 | 失败处理 |
|--------|----------|----------|
| UIA 可见性 | FlaUI 能稳定识别主窗口、输入框、部分会话元素 | 坐标 + OpenCV 前置为主策略 |
| 模板匹配 | OpenCvSharp 可在固定窗口 region 稳定识别发送、搜索、二维码区域 | 调整窗口基线或放弃 PC RPA |
| 桌面保活 | 远控断开、计划任务重启后交互式会话仍可恢复 | 更换云桌面/VNC/物理机方案 |
| 登录态检测 | 已登录、未登录、二维码过期、账号异常可区分 | 不进入开发 |

### 10.2 首版必须包含

| 能力 | 首版要求 |
|------|----------|
| 独立客户端 | `.NET 8 + WPF/托盘 + Supervisor`，同仓独立目录 |
| 异步协议 | 消息 accepted、服务端生成 actions、客户端拉取执行、回执上报 |
| 本地可靠队列 | SQLite 持久化入站事件、出站 actions、重试状态和审计缓存 |
| 自动化引擎 | FlaUI/UIA3 + Win32 + OpenCvSharp 三层定位与执行 |
| 登录与二维码 | 本地展示 + 管理端短期展示，状态全量上报 |
| 消息监听 | 通知/未读触发 + 轮询兜底 + 本地去重 |
| 发送能力 | 文本、图片、文件首版均支持；不支持的 action 必须拒绝并上报 |
| 会话绑定 | 人工确认、重名暂停、绑定失效暂停 |
| 安全 | HMAC、nonce、防重放、密钥加密、文件短期 URL、日志脱敏 |
| 可观测性 | 本地日志、Windows Event Log、服务端健康指标、告警 |
| 管理 | 客户端注册、账号状态、暂停恢复、版本兼容检查 |
| 部署 | 安装包、代码签名、开机自启、升级回滚、标准 Windows 镜像 |
| 审计 | 入站消息、agent 回复、action 执行、暂停恢复均可查 |

### 10.3 生产准入门槛

| 指标 | 门槛 |
|------|------|
| 连续运行 | 3-5 个内部账号连续 14 天 |
| 误发 | 0 次；出现 1 次即阻断上线 |
| 文本/图片/文件 action 成功率 | > 95% |
| 人工抽样漏抓率 | < 3%；已开通会话存档时 < 1% |
| 客户端崩溃恢复 | 2 分钟内自动恢复或告警 |
| 登录/桌面异常 | 必须自动暂停，不能继续发送 |
| 审计完整性 | 100% action 有 request、执行、回执记录 |
| 版本兼容 | 服务端可阻止低版本客户端继续托管 |

---

## 11. 关键决策记录

| # | 决策 | 理由 | 风险 |
|---|------|------|------|
| 1 | 个人企微 RPA 作为独立客户端，而不是 agent 内部模块 | agent 是服务端推理系统，RPA 依赖桌面和登录态，生命周期不同 | 需要维护客户端发布和升级体系 |
| 2 | 客户端与 agent 只通过渠道 callback、WebSocket 下行和标准消息协议通信 | 降低耦合，便于独立技术栈和独立部署 | 协议设计需要稳定，版本兼容要明确 |
| 3 | 客户端直接发送 agent 生成的文本、图片、文件 | 发送动作必须发生在已登录个人企微的桌面环境中 | 客户端需要处理文件下载、临时存储和失败重试 |
| 4 | 首版使用异步 actions 协议 | 避免 agent 长耗时阻塞客户端，便于断网恢复和重试 | 协议和队列实现复杂度更高 |
| 5 | 一个客户端实例绑定一个个人企微账号 | 避免账号串扰和多开风险 | 硬件/VM 成本随账号数线性增长 |
| 6 | 生产客户端采用 .NET 8 + FlaUI + Win32 + OpenCvSharp | 更适合 Windows 桌面自动化、安装部署和长期运维 | 团队需要维护独立 .NET 客户端 |

---

## 12. 不在范围内

- Hook / DLL 注入。
- iPad 协议 / Cookie 模拟。
- mmtls MITM。
- PC 多开企业微信。
- 自动加好友、朋友圈、点赞、评论、群发营销。
- 跨平台消息互通。

---

## 13. 实现进度（2026-06-22 起）

本设计已进入实现阶段。首版按「契约先行 → 并行实现 → 接入验证」推进，阶段切分与状态在开发计划中维护。

### 13.1 共享契约（阶段1，已完成）

下游服务端实现 agent 与 C# 客户端实现 agent 必须以以下契约为唯一真相源，签名与命名一经锁定不得擅自变更：

- **线协议权威模型**：`src/channels/wecom_personal_rpa/schemas.py`（Pydantic）
- **数据库访问层**：`src/channels/wecom_personal_rpa/db.py`（5 张表 CRUD）
- **渠道类型枚举**：`src/models/message.py` 新增 `ChannelType.WECOM_PERSONAL_RPA = "wecom_personal_rpa"`
- **数据库迁移**：`deploy/db_update.sql`（增量）与 `deploy/init-postgres.sql`（全新部署），新增 5 张表：
  - `wecom_rpa_clients`（客户端注册）
  - `wecom_rpa_accounts`（个人企微账号）
  - `wecom_rpa_conversation_bindings`（会话绑定，账号+搜索键唯一）
  - `wecom_rpa_action_outbox`（出站动作队列，`dedup_key` UNIQUE）
  - `wecom_rpa_audit_logs`（审计日志）

### 13.2 协议与签名文档

- **线协议逐字段说明 + Python 函数签名契约 + C# 工程约定**：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)

该文档是下游所有并行 agent 的唯一共享协议文档，包含：
- 鉴权头、签名串构造、timestamp/nonce 规则、幂等键前缀（`wecom_personal_rpa:`）
- 错误码语义表
- `auth.verify_request` / `auth.compute_signature` / `WeComPersonalRpaRouter.route` / `parse_rpa_message` / `deliver_actions` / `WeComPersonalRpaAdapter` / `ClientConnectionRegistry` 等服务端函数签名
- C# 解决方案结构、命名空间、协议 DTO 镜像表、跨工程接口、状态机枚举（`ClientState`）、队列项状态枚举（`QueueStatus`）、共享包版本

### 13.3 阶段2 并行实现（已完成主体，部分待真实环境）

下游 agent 按契约并行实现：
- 服务端：`auth.py` / `router.py` / `message.py` / `action_client.py` / `adapter.py` / `connection.py` / `secret_crypto.py` / 管理 API（`wecom_personal_rpa_admin.py` / `wecom_personal_rpa_routes.py`）
- 客户端：`Client.Core` / `Client.Automation` / `Client.App` / `Client.Supervisor` / `Client.Tests`

详细任务与开发状态见开发计划文档。

### 13.4 阶段4 验证结果（2026-06-22，Verify 会话）

本节如实记录阶段4（测试 / 构建 / 文档同步）的验证产出，遵守 CLAUDE.md Rule 9（失败就说失败、跳过就说跳过）。

**已落地的服务端模块**：`schemas.py`（契约）/ `db.py`（5 表 CRUD）/ `auth.py`（HMAC + nonce 防重放 + 常量时间比较）/ `router.py`（路由 + 会话授权判定）/ `message.py`（入站解析）/ `action_client.py`（在线直推 / 离线落 outbox）/ `adapter.py`（UnifiedResponse→actions + set_reply_context）/ `connection.py`（WS 连接注册表单例）/ `secret_crypto.py`（Fernet 加解密）；Wire 层 `src/saas/api/wecom_personal_rpa_routes.py`（callback / config / files / ws 四类入口）+ `src/saas/api/wecom_personal_rpa_admin.py`（客户端/账号/绑定/暂停恢复/审计管理）。

**已落地的客户端工程**：`clients/wecom-personal-rpa/WeComPersonalRpaClient.sln`（5 工程，GUID 1111…/2222…/3333…/4444…/5555… 与 sln 对齐）；`Client.Core`（net8.0）/ `Client.Automation`（net8.0-windows）/ `Client.Supervisor`（net8.0-windows）/ `Client.Tests`（net8.0-windows）/ `Client.App`（net8.0-windows）。

**测试结果**：
- 服务端单元测试 `tests/unit/channels/wecom_personal_rpa/`：**68 passed**（11 warnings 均为既有 Pydantic V1 弃用告警，与本渠道无关）。
- 新增集成测试 `tests/integration/test_wecom_personal_rpa_flow.py`：**6 passed**（pytest.mark.integration），覆盖：① message 事件 + 正确 HMAC → accepted 且后台 task 调度；② 同 event_id 第二次仍 accepted 但去重命中不重复处理；③ action_result 回执按 action_result_id 幂等（mark_outbox_status 仅调用一次）；④ 签名错误 → 401 + RpaErrorResponse(error=auth_failed) 且 debug 不含 secret；⑤ 客户端离线 → `db.enqueue_action` 被调用落 outbox（dedup_key 正确）；⑥ 客户端在线 → 直推不写 outbox。
  - **采用方式**：路由级最小 FastAPI app + mock 边界（`db` / `MessageDeduplicator` / `secret_crypto.decrypt_secret` / `_process_inbound_message`），未启动完整 `src.main`（避免 master_agent 单例 + apscheduler 重链）。签名使用**真实** `auth.compute_signature` 构造，契约逐字对齐。`tests/integration/conftest.py` 的 autouse DB pool fixture 因本会话远程 PostgreSQL 不可达（连接超时）会触发 `pytest.skip`，本测试文件在模块顶层把 `init_postgres_pool`/`get_postgres_pool`/`close_postgres_pool` 替换为占位以绕过（仅测试隔离用，不进生产）。
- C# 构建 `dotnet build WeComPersonalRpaClient.sln -c Debug`：**失败**，`Client.Core` / `Client.Automation` / `Client.Supervisor` / `Client.Tests` 四工程编译通过；`Client.App` **4 errors / 3 warnings**：
  - error CS0246：`MessageWatcher.cs` 未 `using WeCom.PersonalRpa.Automation.Contracts;`（IWeComAutomation 定义在该命名空间，非 protocol.md §C.2 的 `WeCom.PersonalRpa.Automation`）。
  - error CS0246：`SendMessageService.cs` 未 `using WeCom.PersonalRpa.Core.Protocol;`（RpaAction / ActionResultPayload 定义在该命名空间）。
  - warning CS0108：`StatusWindow` / `LoginQrWindow` / `ErrorWindow` 的 `Tag` 属性隐藏继承成员，建议加 `new`。
  - 根因是并行 agent 跨工程盲编的命名空间微调，属 protocol.md §C 已预警的「C# 跨工程盲编可能需一次集成修缮」。**未修复**（不在本会话拥有文件范围内）。

**一致性自检**：
- ✅ `channel_factory._ADAPTER_CLASSES["wecom_personal_rpa"]` 指向 `WeComPersonalRpaAdapter`；`channel_config` 配置项含 `client_id`；`ChannelType.WECOM_PERSONAL_RPA` 枚举存在；`main.py` include `wecom_personal_rpa_router`。
- ✅ `adapter.set_reply_context` 被 `wecom_personal_rpa_routes._process_inbound_message` 在 `send_message` 之前正确调用（注入 account_id / conversation_id / session_id / request_id / tenant_id）。
- ✅ `schemas.py` Pydantic 模型字段与 `db.py` SQL 表名/字段一致（`wecom_rpa_clients` / `wecom_rpa_accounts` / `wecom_rpa_conversation_bindings` / `wecom_rpa_action_outbox` / `wecom_rpa_audit_logs`，`dedup_key` UNIQUE 约束存在）。
- ✅ C# 5 工程 ProjectGuid 与 `WeComPersonalRpaClient.sln` 的 Project 引用一一对齐（1111…→Core、2222…→Automation、3333…→App、4444…→Supervisor、5555…→Tests）。
- ⚠️ 命名空间：`IWeComAutomation` 实际定义在 `WeCom.PersonalRpa.Automation.Contracts`（protocol.md §C.2 写的是 `WeCom.PersonalRpa.Automation`），Client.App 未补 using 致编译失败；`Client.Tests` 的 TargetFramework 实际为 `net8.0-windows`，protocol.md §C.7 要求 `net8.0`（Core 不依赖 Windows，Tests 应可在非 Windows CI 跑单测）——属协议与实现的轻微偏差，待一次集成修缮对齐。

**已知 TODO（需真实环境，本会话未实现）**：
- 节点常量 `wecom_nodes.yaml` 的真实控件/模板路径必须由 §0 准入探测（FlaUI/UIA3/Win32/OpenCV）产出后填充，属强阻塞。
- WebSocket 在 Gunicorn 多 worker 下的连接注册表一致性：当前 `client_connection_registry` 为模块级单例，多 worker 进程内存隔离，跨 worker 在线判定与 outbox 推送需 Redis 或 sticky session，首版单 worker 可用，多 worker 待加固（对齐 backend_dev.md 多 worker 规范）。
- C# Client.App 跨工程 using 缺失 + Client.Tests 目标框架偏差，需一次集成修缮。
- §0 / §5 / §7 全部任务依赖真实 Windows + 企微 + 账号环境，未实现（详见开发计划对应章节的状态说明）。

