# 企业微信个人账号 RPA 客户端设计

> 关联：
> - 主设计（架构、协议、安全、合规）：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md)
> - 协议：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)
> - 绑定管理 Tab：[wecom-personal-rpa-portal-binding-design.md](./wecom-personal-rpa-portal-binding-design.md)
> - 调试脚本（已验证可用）：[clients/wecom-personal-rpa/scripts/debug-navigate.ps1](../../clients/wecom-personal-rpa/scripts/debug-navigate.ps1)
> - 开发计划：[plans/plan-wecom-personal-rpa-client.md](../../plans/plan-wecom-personal-rpa-client.md)
>
> 登记位置：[docs/ideas.md](../ideas.md) #29（企业微信个人账号 RPA 接入）
>
> 创建日期：2026-06-26
> 状态：🔧 设计完成，待开发

---

## 0. 历史背景（看一眼就翻篇）

之前踩过的坑（**不要再走**）：

| 方案 | 失败原因 | 处置 |
|------|---------|------|
| FlaUI/UIA3 读企微控件 | Electron 应用 dump 出 0 控件 | 永久放弃，删除 FlaUiDriver.cs |
| Qwen3-VL 视觉定位 | bbox 不稳定 + 偏左上 + "自报家门"误识别 | 永久放弃 |
| PaddleOCR layout-parsing 整窗 | 不识别 placeholder、漏识密集列表项 | 仅用于"小区域 OCR"场景，不用于整窗 |
| Windows.Media.Ocr | 中文识别率 < 10% | 永久放弃 |
| 把 PS 重写到 C# | 已有可用 PS 代码，重写无收益 | 改为 C# 调 PS（本设计） |

调试脚本 `debug-navigate.ps1` 已真机验证通过的方案（**本设计的基础**）：
- 搜索框固定坐标 + DPI scale 自动换算
- Enter 键进入搜索结果第一项
- 剪贴板粘贴发送文本 / 截图
- 真机 3 用户（陆伟/孙晨/芮秀）+ 多种窗口尺寸全流程通过

---

## 1. 客户端职责全景（7 大功能 + 5 项基础设施）

### 1.1 7 大功能模块

```
┌──────────────────────────────────────────────────────────────────────┐
│                       RPA Client (C# + PowerShell)                    │
│                                                                       │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────────────────────┐ │
│  │ F1 服务端 │←──→│ F3 出站执行  │←──→│ F2 PowerShell 自动化层       │ │
│  │  通信     │    │  (调用 PS)   │    │  (wecom-ops.ps1)            │ │
│  └────┬─────┘    └─────────────┘    └──────────────────────────────┘ │
│       │ ↑                                                            │
│       │ │ report                                                     │
│       ▼ │                                                            │
│  ┌─────────────────────┐    ┌────────────────────────────────────┐  │
│  │ F6 入站消息解析+上报  │←──│ F4 会话存档 API 监听（主方案）       │  │
│  │  (sender/text/...)   │    │   或                                │  │
│  └─────────────────────┘    │   F5 Fallback 监听（占位/待定）     │  │
│                              └────────────────────────────────────┘  │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ F7 未登录二维码定时截取 + 上报服务端（base64 直推）              │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
                                ↑
                       基础设施 5 项（贯穿全部）
        进程监控 / 桌面健康检查 / WebSocket 心跳重连 / 暂停恢复 / 操作日志
```

### 1.2 模块速览

| # | 模块 | 当前状态 | 实现位置 |
|---|------|---------|---------|
| **F1** | 服务端通信 | ✅ 已有 | `Client.Core/AgentApi/` + `Client.Core/Protocol/` |
| **F2** | PowerShell 企微操作 | 🔧 部分（发送已验证，监听待开发） | `scripts/wecom-ops.ps1` + C# `PowershellOpsInvoker` |
| **F3** | 出站执行（服务端回复 → 调 PS） | 🔧 待开发 | `Client.App/OutboundActionDispatcher` |
| **F4** | 会话存档 API 监听（主方案） | ❌ 待开发 | `Client.App/MessageArchive/ChatArchiveListener` |
| **F5** | Fallback 监听（占位） | ❌ 方案待定 | 占位接口 `IMessageWatcher` |
| **F6** | 入站消息解析 + 上报 | 🔧 待开发（F4 拿到数据后做） | `Client.App/InboundEventReporter` |
| **F7** | 二维码截取上报 | ❌ 待开发 | `Client.App/QrCodeWatcher` |
| 基础设施 1 | 进程监控 + 自动拉起 | ✅ 已有 | `Client.Supervisor` + `AutostartRegistrar` |
| 基础设施 2 | 桌面健康检查 | 🔧 部分（接口在，真机探测待做） | `IHealthSupervisor` |
| 基础设施 3 | WebSocket 心跳 + 断线重连 | 🔧 部分（连接在，重连策略待加固） | `ClientConnectionRegistry` 服务端 + 客户端 `AgentApiClient` |
| 基础设施 4 | 暂停/恢复（账号/会话/租户三级） | ✅ 后端已就绪 | 服务端 `/pause` `/resume`；客户端响应待补 |
| 基础设施 5 | 操作日志（本地落盘 + 关键上报） | 🔧 部分（Serilog 在，结构化上报待做） | Serilog 配置 |

---

## 2. F1 服务端通信

### 2.1 现状

**已有**：
- HMAC-SHA256 签名（`X-Client-Id` / `X-Timestamp` / `X-Nonce` / `X-Signature`）
- 入站 callback：`POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`
- 出站 actions 下发：WebSocket `/t/{tenant_id}/wecom_personal_rpa/ws/{config_id}` + 离线 outbox
- 配置拉取：`GET /api/v1/channels/wecom-personal-rpa/config`
- 文件下载：`GET /api/v1/channels/wecom-personal-rpa/files/{id}`（短期签名）
- 集成测试 6/6 通过（HMAC、event_id 去重、签名失败 401、客户端在线直推/离线落 outbox、action_result 回执幂等）

详见 [protocol.md](./wecom-personal-rpa-protocol.md)。

### 2.2 本次补强

| 项 | 改动 |
|----|------|
| `/config` 响应新增 `monitor_users` 字段 | 下发绑定级监控白名单（见 §F6） |
| `/config` 响应新增 `archive_enabled` 字段 | 服务端告知客户端「会话存档是否启用」，客户端按此选择监听方式 |
| 客户端 WebSocket 重连策略 | 指数退避 + 心跳超时检测 + 重连时拉取 outbox 增量 |
| 客户端启动时校验服务端时间 | 时间戳偏差 > 60s 直接报警，避免签名被拒 |

---

## 3. F2 PowerShell 自动化层

### 3.1 架构

```
┌─────────────────────────────────────────────────────────┐
│ Client.App (C#)                                          │
│  PowershellOpsInvoker                                    │
│   Process.Start("powershell.exe", "-File wecom-ops...")  │
│      ↓ stdin JSON   ↑ stdout JSON                        │
├─────────────────────────────────────────────────────────┤
│ scripts/wecom-ops.ps1   ← 主入口（dispatch Action）       │
│ scripts/wecom-ops-lib.ps1 ← 共享函数库                    │
│  ├─ Search-WeComUser       操作 1                         │
│  ├─ Send-WeComText         操作 4                         │
│  ├─ Send-WeComImage        操作 4                         │
│  ├─ Send-WeComFile         操作 4                         │
│  ├─ Watch-WeComNewMessages fallback（占位）               │
│  └─ Get-WeComLoginState    F7 二维码截取                  │
├─────────────────────────────────────────────────────────┤
│ 企业微信 PC 客户端                                        │
└─────────────────────────────────────────────────────────┘
```

### 3.2 关键决策

| 决策 | 理由 |
|------|------|
| **C# 调 PS 而非重写到 C#** | `debug-navigate.ps1` 已真机验证可用，重写无收益、有风险 |
| **每次调用启动独立 PS 进程** | 故障隔离 + 调试方便；启动 < 1s 在轮询周期下可接受 |
| **stdin/stdout JSON 传参** | 命令行参数传中文易乱码；JSON 编码清晰 |
| **PS 文件存为 UTF-8 with BOM** | PS 5.1 看到 BOM 按 UTF-8 解析源码，中文字面量不乱码（`debug-navigate.ps1` 已验证） |
| **强制用 `powershell.exe`（5.1）** | 用户机器默认安装，不依赖 pwsh 7；编码坑已通过 BOM + keywords.txt 解决 |
| **PS 端不感知协议** | PS 只做"操作企微"，协议/HMAC/WebSocket 全在 C# 端 |

### 3.3 PS 函数清单（action 映射）

| PS 函数 | action | 输入 | 输出 | 状态 |
|---------|--------|------|------|------|
| `Search-WeComUser` | `search_user` | `{keyword}` | `{conversation_title, window_hwnd}` | ✅ 已有（debug-navigate） |
| `Send-WeComText` | `send_text` | `{keyword, text}` | `{sent_text}` | ✅ 已有 |
| `Send-WeComImage` | `send_image` | `{keyword, image_path}` | `{sent_image_size}` | ✅ 已有 |
| `Send-WeComFile` | `send_file` | `{keyword, file_path}` | `{sent_file_name}` | ❌ 待开发（`Clipboard.SetFileDropList`） |
| `Watch-WeComNewMessages` | `watch_new_messages` | `{since, allowed_users}` | `{new_messages[]}` | ❌ 待开发（fallback 占位） |
| `Get-WeComLoginState` | `get_login_state` | `{}` | `{state, qr_image_base64?}` | ❌ 待开发（F7 用） |

### 3.4 调用契约

**C# → PS（stdin）**：
```json
{"keyword": "陆伟", "text": "你好啊！"}
```

**PS → C#（stdout 最后一行）**：
```json
{
  "success": true,
  "action": "send_text",
  "data": {"sent_text": "你好啊！"},
  "duration_ms": 1234
}
```

**失败**：
```json
{
  "success": false,
  "action": "send_text",
  "error_code": "wecom_navigation_failed",
  "error_message": "Enter 后会话标题不匹配",
  "duration_ms": 500
}
```

错误码与 `protocol.md §A.8` 对齐：

| error_code | 触发条件 | C# 处理 |
|------------|---------|---------|
| `wecom_window_not_found` | EnumWindows 找不到 WeWorkWindow | 上报 `offline` 状态 + 重试 3 次 |
| `wecom_login_required` | 检测到二维码登录页 | 上报 `need_login` 状态，触发 F7 二维码上报 |
| `wecom_navigation_failed` | Enter 后会话标题不匹配 | 上报 `needs_review` |
| `clipboard_conflict` | 剪贴板重试 3 次仍失败 | action 失败回执 |
| `ps_script_exception` | PS 内部异常 | action 失败回执 + 上报客户端日志 |

### 3.5 复用 debug-navigate.ps1 的稳定函数

从 `debug-navigate.ps1` 提取到 `wecom-ops-lib.ps1`：

| 函数 | 用途 |
|------|------|
| `Click-At` | DPI-aware 鼠标点击（SetCursorPos + mouse_event） |
| `Type-Text` | 剪贴板 + Ctrl+V 中文输入 |
| `Press-CtrlA-Delete` | 清空输入框 |
| `Get-WeWorkWindowOrigin` | EnumWindows 找企微主窗口物理坐标 |
| `Capture-WeCom` | 截企微主窗口（封装 `capture-wecom-for-csharp.ps1`） |
| `Load-Keywords` | 从 `prompts/keywords.txt`（UTF-8）加载中文字典 |

**不提取**：`Load-State` / `Save-State`（业务参数走 stdin，不需要持久化）。

---

## 4. F3 出站执行（服务端回复 → 调 PS 发送）

### 4.1 流程

```
服务端 agent 推理完成
  → UnifiedResponse → adapter.send_message 转 actions
  → WebSocket 下发 OR 离线落 outbox（客户端拉取）
  → 客户端 ClientSession 接收 ActionEnvelope
  → OutboundActionDispatcher 入队（SQLite 持久化）
  → 账号级串行 Worker 出队
  → 调 PowershellOpsInvoker.InvokeAsync(action, parameters)
  → PS 执行结果 → 上报 action_result callback
```

### 4.2 ActionEnvelope → PS action 映射

| 服务端 action.type | PS action | 参数映射 |
|-------------------|-----------|---------|
| `send_text` | `send_text` | `{keyword: conversation_key, text}` |
| `send_image` | `send_image` | `{keyword, image_path: <本地下载后路径>}` |
| `send_file` | `send_file` | `{keyword, file_path: <本地下载后路径>}` |
| `noop` | （跳过，仅回执 success） | — |
| `handoff` | （暂停该会话，回执 success） | — |

### 4.3 文件附件下载

服务端下发的 `file_url` 是短期签名 URL。客户端：

1. `HttpClient.GetAsync(file_url)` 下载到本地临时目录
2. 校验 MIME + 大小（≤ 100MB）
3. 调 PS `send_image` / `send_file`
4. **PS 执行完成后立即删除本地临时文件**（避免敏感文件残留）
5. 失败重试 2 次（网络/剪贴板冲突）

### 4.4 串行队列

- 单账号全局串行：同一账号同时只跑一个 PS 进程，避免窗口抢占
- 跨账号并行：不同账号可并发（如果有多个客户端实例绑定多账号）
- 失败重试：`wecom_window_not_found` 重试 3 次；其他错误不重试直接上报
- 持久化：出站 actions 落 SQLite（`outbox_local` 表），客户端重启不丢

---

## 5. F4 会话存档 API 监听（主方案）

### 5.1 前置条件

| 项 | 要求 |
|----|------|
| 企业微信开通「会话存档」 | 管理后台 → 管理工具 → 会话内容存档 → 开启（付费） |
| 创建存档应用 | 拿到 `corpid` + `secret` + `private_key`（RSA 私钥） |
| 员工授权 | 被监控账号要在存档范围内（管理后台勾选员工/部门） |
| 客户端机器能访问企微 API | `qyapi.weixin.qq.com` 出网通畅 |

### 5.2 配置方式

`client.example.yaml`：

```yaml
message_source:
  # 监听方式：archive（会话存档 API）or fallback（占位）
  mode: archive

  archive:
    corpid: "ww8888888888888888"
    secret: "${ARCHIVE_SECRET}"           # 走环境变量，不写明文
    private_key_path: "config/archive_private_key.pem"
    # 拉取间隔（秒）
    poll_interval_seconds: 3
    # 单次拉取上限
    batch_limit: 1000
    # 媒体文件下载开关（图片/文件/语音）
    download_media: true
    # 媒体文件下载到（相对客户端根目录）
    media_temp_dir: "temp/archive-media"

  fallback:
    # 占位：F5 方案定下后填
    enabled: false
```

### 5.3 实现层

| 组件 | 文件 | 职责 |
|------|------|------|
| `ChatArchiveListener` | `Client.App/MessageArchive/ChatArchiveListener.cs` | 主循环：拉取 → 解密 → 解析 → 推 IInboundQueue |
| `ArchiveHttpClient` | `Client.App/MessageArchive/ArchiveHttpClient.cs` | 封装企微会话存档 HTTP API |
| `ArchiveCryptoService` | `Client.App/MessageArchive/ArchiveCryptoService.cs` | RSA 解密 encrypt_random_key + AES 解密 chat_data |
| `ArchiveSeqStore` | `Client.App/MessageArchive/ArchiveSeqStore.cs` | seq 持久化（SQLite），重启不丢 |
| `ArchiveMediaDownloader` | `Client.App/MessageArchive/ArchiveMediaDownloader.cs` | 媒体文件下载（短期 SDK URL） |

### 5.4 拉取流程

```
循环（每 poll_interval_seconds 秒一次）：
  1. 读 ArchiveSeqStore 拿当前 seq
  2. 调企微 API：get_chat_data(seq, limit, proxy, last_snap_shot)
     → 返回 chatdata_list（每条含 msgid, action, from, tolist, roomid, msgtime, msgtype, encrypt_random_key, encrypt_chat_msg）
  3. 对每条 chatdata：
     a. 用 private_key RSA 解密 encrypt_random_key → random_key
     b. 用 random_key AES 解密 encrypt_chat_msg → 明文 JSON
     c. 按 msgtype 解析（text/image/file/voice/video/...）
     d. download_media=true 时，下载媒体文件到 media_temp_dir
     e. 构造 InboundEvent 入队
  4. 更新 ArchiveSeqStore.seq = 最后一条的 seq
```

### 5.5 会话类型识别

会话存档返回的 `from` / `tolist` / `roomid` 字段直接是**稳定 ID**（external_userid / userid / roomid），不需要 OCR 推断，**比 fallback 方案稳定 100 倍**。

| msgtype | 解析后字段 |
|---------|----------|
| `text` | `{content: "..."}` |
| `image` | `{md5sum, sdkfileid}` → 下载 |
| `file` | `{filename, md5sum, sdkfileid}` → 下载 |
| `voice` | `{md5sum, sdkfileid, play_length}` → 下载 |
| `video` | `{md5sum, sdkfileid, play_length}` → 下载 |
| `revoke` | `{msgid, roomid?}` → 处理撤回事件 |
| `agree` / `disagree` | 同意/拒绝会话存档授权（合规相关） |

### 5.6 数据范围

| `action` | 含义 | 客户端处理 |
|---------|------|----------|
| `upload` | 新消息 | **入队** InboundEvent |
| `download` | 自己发出去的消息已读回执 | 仅审计，不入队 |
| `recall` | 撤回 | 入队（标记 `revoke=true`） |

只处理 `action=upload` 和 `action=recall`，`download` 跳过（避免污染对话）。

### 5.7 失败处理

| 错误 | 处理 |
|------|------|
| 网络超时 | 重试 3 次，间隔 1s/3s/9s |
| 企微 API 返回 45009（频率限制） | 暂停 60s 后重试 |
| RSA 解密失败 | 记日志跳过单条，**不更新 seq**（下次重拉） |
| seq 持久化失败 | 整批次丢弃，下次重新拉取同 seq 范围 |
| 客户端进程崩溃重启 | 从 SQLite 读 seq 续传，不丢消息 |

### 5.8 合规要点

| 项 | 要求 |
|----|------|
| 媒体文件本地存储 | `media_temp_dir` 下临时保存，**上报完服务端立即删除本地副本** |
| 私钥文件 | `archive_private_key.pem` 文件权限 600（仅当前 Windows 用户可读） |
| 日志脱敏 | chat_data 明文不入日志；只记 msgid + msgtype + 时间 |
| 员工撤销授权 | 服务端 `agree/disagree` 事件触发时，客户端停止该账号的拉取 |

---

## 6. F5 Fallback 监听（占位 / 方案待定）

### 6.1 占位接口

```csharp
// Client.Core/Protocol/IMessageWatcher.cs
public interface IMessageWatcher : IDisposable
{
    /// <summary>启动监听。</summary>
    Task StartAsync(CancellationToken ct = default);

    /// <summary>停止监听并释放资源。</summary>
    Task StopAsync(CancellationToken ct = default);

    /// <summary>新消息事件。每条 InboundEvent 触发一次。</summary>
    event EventHandler<InboundEventArgs>? NewMessageReceived;
}
```

`IMessageWatcher` 由两个实现：

| 实现 | 触发条件 |
|------|---------|
| `ChatArchiveListener`（F4） | `message_source.mode = archive` |
| `FallbackMessageWatcher`（F5 占位） | `message_source.mode = fallback` |

### 6.2 Fallback 方案选择（待定）

之前考虑过但**都不够稳**的方案：

| 方案 | 失败原因 | 处置 |
|------|---------|------|
| Windows 系统通知监听 | 实测企微 5.x 不走系统 Toast，用自己的私有弹窗；PS 监听 UIA 事件被吞（已验证不可行） | 放弃 |
| Qwen3-VL 视觉定位 | bbox 不稳定 | 已放弃 |
| OCR 整窗 | 中文 OCR 失败率高 | 已放弃 |
| 进程内存读 | 合规风险 + 升级就废 | 已放弃 |
| SQLite/LevelDB 文件监控 | schema 加密，逆向成本极高 | 已放弃 |

**当前候选方向**（待验证）：
- 红点检测 + 小区域 OCR（仅 OCR 会话名区域，文字少准确率高）
- 桌面通知 API 实测的"私有弹窗"事件监听（C# 端直接用 UIA，不走 PS 委托）
- 企业微信 IPC Hook（合规边界外，倾向不用）

**决策点**：Fallback 方案**先占位，不开发**。本次只实现 F4（会话存档 API）。若 F4 在某些企业不可用，再单独立项做 F5。

### 6.3 占位实现

```csharp
// Client.App/MessageWatchers/FallbackMessageWatcher.cs
public sealed class FallbackMessageWatcher : IMessageWatcher
{
    public Task StartAsync(CancellationToken ct = default)
    {
        // 占位：仅记录状态，不实际监听
        _logger.Warning("FallbackMessageWatcher 启动，但当前未实现，监听功能不可用");
        return Task.CompletedTask;
    }

    public event EventHandler<InboundEventArgs>? NewMessageReceived;
    // ...
}
```

客户端启动时若 `mode=fallback`，弹窗提示运维"当前未实现 fallback 方案，请开通会话存档"。

---

## 7. F6 入站消息解析 + 上报

### 7.1 流程

```
F4 ChatArchiveListener 拿到原始 chat_data
  → 构造 InboundEvent（含 conversation_id / sender_stable_id / msgtype / text / attachments）
  → MonitorUserWhitelistFilter 白名单过滤（绑定级）
  → InboundEventReporter.ReportAsync
  → POST /callback 上报服务端
  → 服务端二次校验白名单 + 投递 agent
```

### 7.2 绑定级白名单

详见 [portal-binding-design.md](./wecom-personal-rpa-portal-binding-design.md)，本设计补充客户端侧：

#### 7.2.1 数据来源
客户端启动时通过 `/config` 拉取 `monitor_users` 字段：

```json
{
  "monitor_users": {
    "binding_abc": {
      "user_names": ["陆伟", "孙晨"],
      "user_ids": ["wm_xxx", "wm_yyy"]
    }
  }
}
```

#### 7.2.2 客户端缓存
- `MonitorUsersCache`：内存缓存 + 每 60 分钟刷新一次
- 服务端可通过 WebSocket 推送 `config_invalidate` 事件强制刷新

#### 7.2.3 过滤逻辑
```csharp
public bool IsAllowed(string bindingId, string senderName, string? senderId)
{
    var entry = _cache.Get(bindingId);
    if (entry is null) return true;  // 未配置 = 监控所有

    bool namesConfigured = entry.UserNames.Count > 0;
    bool idsConfigured = entry.UserIds.Count > 0;
    if (!namesConfigured && !idsConfigured) return true;

    if (namesConfigured && entry.UserNames.Contains(senderName)) return true;
    if (idsConfigured && !string.IsNullOrEmpty(senderId) && entry.UserIds.Contains(senderId)) return true;

    return false;
}
```

#### 7.2.4 服务端二次校验
客户端过滤**只是优化**（减少 callback 调用），真正的过滤必须在服务端 callback 入口做（防客户端被绕过）。服务端在 `_process_inbound_message` 再次按 `wecom_rpa_conversation_bindings.monitor_user_names/ids` 校验。

### 7.3 InboundEvent 构造

```csharp
public sealed class InboundEvent
{
    public string EventId { get; init; }       // 客户端生成，去重键
    public string ClientId { get; init; }
    public string AccountId { get; init; }
    public EventType EventType { get; init; }   // Message / Status / ActionResult
    public DateTimeOffset OccurredAt { get; init; }
    public JsonElement Payload { get; init; }   // RpaMessagePayload 序列化
}
```

`Payload` 结构对齐 `protocol.md §A.3`：

```json
{
  "conversation_id": "wm_xxx_to_wm_yyy",       // 拼接 sender + receiver
  "conversation_type": "external_user",
  "sender_display_name": "陆伟",
  "sender_stable_id": "wm_xxx",                 // 会话存档直接给
  "message_type": "text",
  "text": "你好",
  "attachments": []
}
```

### 7.4 媒体附件处理

F4 拿到的图片/文件/语音，客户端：

1. 下载到 `media_temp_dir`（短期 SDK URL）
2. **重新上传到服务端**（短期签名 URL，复用 `POST /files` 接口或新增专用接口）
3. 把服务端 URL 填入 `attachments[].url`
4. **删除本地副本**
5. 上报 InboundEvent

> **服务端接口需要补**：客户端把媒体文件上传到服务端的接口（`POST /api/v1/channels/wecom-personal-rpa/media-upload`），返回短期签名 URL 供后续回调使用。**TODO**：协议文档 §A 需补充此接口定义。

### 7.5 去重

| 去重键 | 来源 |
|--------|------|
| 客户端→服务端 | `event_id` 客户端生成（基于 `archive_msgid`），服务端按 `client_id + event_id` 去重 |
| 客户端本地 | `archive_msgid` 直接用，重启不重复上报 |

---

## 8. F7 未登录二维码截取 + 上报

### 8.1 触发条件

- 客户端启动时 `Get-WeComLoginState` 返回 `need_login`
- 运行中账号掉线 → 进入 `need_login` 状态

### 8.2 流程

```
Get-WeComLoginState 返回 need_login
  → PS 截取企微登录窗口的二维码区域
  → 转为 base64 字符串（不带 data:image/png;base64, 前缀）
  → 客户端通过 WebSocket 推送 status 事件给服务端：
     {
       "event_type": "status",
       "payload": {
         "status": "need_login",
         "account_display_name": "销售-王经理",
         "detail": "二维码已展示，等待扫码",
         "qr_image_base64": "iVBORw0KG..."   // base64 PNG
       }
     }
  → 服务端保存二维码（内存中 + 30 秒 TTL，不入审计日志）
  → 推送给平台后台/租户管理员 UI 展示
  → 用户扫码后客户端检测到状态变 online → 推送 status=online 事件
```

### 8.3 PS 端实现

`Get-WeComLoginState` 函数：

```powershell
function Get-WeComLoginState {
    # 1. EnumWindows 找 WeWorkWindow
    $origin = Get-WeWorkWindowOrigin
    if (-not $origin) {
        return @{ success = $true; data = @{ state = 'offline' } }
    }

    # 2. 截企微主窗口
    $cap = Capture-WeCom

    # 3. PaddleOCR 小区域识别（识别窗口中央区域）
    #    - 包含"扫码登录" → need_login
    #    - 包含"二维码已失效" → qr_expired
    #    - 其他 → online
    $centerRegion = Calculate-CenterRegion $cap
    $ocrText = Invoke-PaddleOcrRegion $cap.png_path $centerRegion

    if ($ocrText -match '扫码登录|二维码') {
        # 4. 截取二维码区域（固定坐标，参考企微 5.0.8 登录页布局）
        $qrBbox = @(530, 200, 800, 470)  # 占位，真机校准后填
        $qrBase64 = Crop-And-Base64 $cap.png_path $qrBbox
        return @{
            success = $true
            data = @{
                state = if ($ocrText -match '失效') { 'qr_expired' } else { 'need_login' }
                qr_image_base64 = $qrBase64
            }
        }
    }

    return @{ success = $true; data = @{ state = 'online' } }
}
```

### 8.4 客户端定时检测

```csharp
// Client.App/QrCodeWatcher.cs
public sealed class QrCodeWatcher : IDisposable
{
    private readonly PowershellOpsInvoker _ps;
    private readonly IAgentApiClient _apiClient;
    private Timer? _timer;
    private TimeSpan _pollInterval = TimeSpan.FromSeconds(30);  // 配置可调
    private bool _wasOnline = true;

    public void Start()
    {
        _timer = new Timer(async _ => await PollOnceAsync(), null, TimeSpan.Zero, _pollInterval);
    }

    private async Task PollOnceAsync()
    {
        var result = await _ps.InvokeAsync("get_login_state", new { });
        if (!result.Success) return;

        var state = result.Data.GetProperty("state").GetString();
        var isOnline = state == "online";

        if (isOnline && !_wasOnline)
        {
            // 从 need_login 恢复 online
            await _apiClient.ReportStatusAsync(new StatusPayload
            {
                Status = "online",
                Detail = "扫码成功，账号已上线"
            });
            _wasOnline = true;
        }
        else if (!isOnline && _wasOnline)
        {
            // 进入未登录状态
            var qrBase64 = result.Data.GetProperty("qr_image_base64").GetString();
            await _apiClient.ReportStatusAsync(new StatusPayload
            {
                Status = state,  // need_login or qr_expired
                QrImageBase64 = qrBase64
            });
            _wasOnline = false;
        }
        else if (!isOnline)
        {
            // 持续未登录，每 30s 推一次最新二维码（避免二维码过期）
            var qrBase64 = result.Data.GetProperty("qr_image_base64").GetString();
            await _apiClient.ReportStatusAsync(new StatusPayload
            {
                Status = state,
                QrImageBase64 = qrBase64
            });
        }
    }
}
```

### 8.5 服务端处理

- 二维码 base64 收到后**仅保存在 Redis 30 秒**，键 `wecom_rpa:qr:{account_id}`
- 平台后台 / 租户管理员前端轮询 `GET /api/saas/wecom-personal-rpa/accounts/{id}/qr` 拿当前二维码
- **不入审计日志、不入数据库持久化**（合规要求，对齐 `design.md §9.1`）
- 二维码内容**禁止**打印到日志、调试输出

### 8.6 安全

| 风险 | 缓解 |
|------|------|
| 二维码 base64 在传输中被截获 | WebSocket 已有 HMAC 签名 + 强制 HTTPS |
| 服务端 Redis 被未授权访问 | 二维码查询接口走 `require_admin`，仅平台/租户管理员可见 |
| 二维码被截图外传 | 客户端截取后立即覆盖上次截图（不落盘历史），仅当前一张在内存 |
| 账号已登录后二维码仍残留 | 收到 `status=online` 立即从 Redis 删除对应 QR key |

---

## 9. 基础设施

### 9.1 进程监控 + 自动拉起（已有）

| 组件 | 状态 | 说明 |
|------|------|------|
| `Client.Supervisor`（Windows Service） | ✅ 已有 | 监控 `Client.App` 进程存活，崩溃后拉起 |
| `AutostartRegistrar` | ✅ 已有 | 注册表 Run 键，开机自启 |
| `ScheduledTaskHelper` | ✅ 已有 | schtasks 注册（备用方案） |
| 离线告警 | 🔧 | Supervisor 检测 App 离线 → 服务端上报（已有接口，待联调） |

### 9.2 桌面健康检查（部分）

`IHealthSupervisor` 接口已就绪，实现待真机探测：

| 检查项 | 触发条件 | 处理 |
|--------|---------|------|
| 桌面锁屏 | `OpenInputDesktop()` 检测 | 上报 `desktop_locked` 状态，暂停发送 |
| 企微窗口被遮挡 | `GetForegroundWindow` 不是 WeWorkWindow | 暂停发送 + 上报 `window_not_visible` |
| 分辨率/DPI 变化 | 窗口尺寸 ≠ 启动时 | 重算搜索框固定坐标 |
| 企微进程退出 | `Get-Process WXWork` 失败 | 上报 `offline` + 自动重启企微 |
| 剪贴板被占用 | Clipboard.Open 失败 | 重试 3 次后放弃当前 action |

### 9.3 WebSocket 心跳 + 断线重连（部分）

**已有**：WebSocket 连接 + 服务端 `ClientConnectionRegistry`

**待补**：
- 客户端心跳：每 30s 发一个 ping，服务端 60s 未收到视为离线
- 重连策略：指数退避（1s → 2s → 4s → 8s → 16s → 30s 封顶）
- 重连后拉取 outbox 增量：`GET /outbox?since=<last_action_id>`
- 网络变化检测：`NetworkChange.NetworkAvailabilityChanged` 事件触发立即重连

### 9.4 暂停/恢复（后端已就绪）

服务端 `/pause` `/resume` 支持三级（account/conversation/tenant），客户端响应：

| 服务端推送 | 客户端处理 |
|----------|----------|
| `paused_scope=tenant` | 整个客户端停止所有 action + 不再拉取消息 |
| `paused_scope=account` | 该账号停止 action + 该账号的 ChatArchiveListener 暂停 |
| `paused_scope=conversation` | 该会话的入站消息丢弃、出站 action 拒绝 |

### 9.5 操作日志（部分）

| 类型 | 落盘位置 | 上报服务端 |
|------|---------|----------|
| 启动/关闭 | 本地 Serilog 滚动文件 | 仅启动关闭事件 |
| PS 调用（每次） | 本地 | action_result（成功/失败 + duration_ms） |
| 消息监听事件 | 本地 | event_id + 概要（不入明细日志） |
| 二维码上报 | 本地**只记账号 ID + 时间**，不记二维码 | 服务端只记"已上报二维码"事件 |
| 错误堆栈 | 本地 + Windows Event Log | error_code 上报，堆栈留本地 |

日志脱敏规则（对齐 `design.md §9.1`）：
- 禁止：`client_secret`、二维码内容/base64、HMAC 签名、文件绝对路径、媒体文件内容
- 允许：`client_id`、`account_id`、`event_id`、`request_id`、`msgid`（脱敏后中间打 `*`）

---

## 10. 配置文件总览

`clients/wecom-personal-rpa/configs/client.example.yaml`：

```yaml
# 客户端身份
client:
  client_id: "client_001"
  client_secret_ref: "DPAPI:xxx"  # Windows DPAPI 加密，不写明文
  server_url: "https://agent.example.com"
  tenant_id: "tenant_xxx"

# 服务端通信
server:
  callback_path: "/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}"
  ws_path: "/t/{tenant_id}/wecom_personal_rpa/ws/{config_id}"
  request_timeout_seconds: 30
  ws_reconnect_backoff_seconds: [1, 2, 4, 8, 16, 30]
  heartbeat_interval_seconds: 30

# PowerShell 自动化
automation:
  powershell_executable: "powershell.exe"
  ops_script: "scripts/wecom-ops.ps1"
  invoke_timeout_seconds: 30
  search_box_bbox_base: [330, 34, 430, 66]
  search_box_base_width: 1936
  wait_after_click_search_ms: 400
  wait_after_type_keyword_ms: 1500
  wait_after_enter_ms: 2500
  wait_after_paste_image_ms: 1500
  wait_after_send_image_ms: 800

# 消息源（监听方式二选一）
message_source:
  # archive = 会话存档 API（推荐，需企业开通）
  # fallback = 占位（当前未实现，启动时弹窗告警）
  mode: archive

  archive:
    corpid: "ww8888888888888888"
    secret: "${ARCHIVE_SECRET}"           # 环境变量
    private_key_path: "config/archive_private_key.pem"
    poll_interval_seconds: 3
    batch_limit: 1000
    download_media: true
    media_temp_dir: "temp/archive-media"

  fallback:
    enabled: false

# 二维码检测
qr_code:
  poll_interval_seconds: 30
  region_bbox_base: [530, 200, 800, 470]   # 待真机校准

# 出站执行
outbound:
  download_temp_dir: "temp/outbound-downloads"
  max_attachment_size_mb: 100
  clipboard_retry: 3

# 健康检查
health:
  poll_interval_seconds: 60
  desktop_locked_pause: true

# 日志
logging:
  serilog_minimum_level: "Information"
  file_path: "logs/client-.log"
  file_roll_size_mb: 10
  file_retained_days: 7

# 监控白名单缓存
monitor_users:
  cache_refresh_minutes: 60
```

---

## 11. C# 工程结构（本次改造）

### 11.1 新增

```
src/Client.App/
├── MessageArchive/                         # F4 新增
│   ├── ChatArchiveListener.cs              # IMessageWatcher 实现
│   ├── ArchiveHttpClient.cs
│   ├── ArchiveCryptoService.cs             # RSA + AES
│   ├── ArchiveSeqStore.cs                  # SQLite seq 持久化
│   └── ArchiveMediaDownloader.cs
├── MessageWatchers/                        # F5 占位
│   └── FallbackMessageWatcher.cs           # 占位实现，启动告警
├── Powershell/
│   ├── PowershellOpsInvoker.cs             # 主调用入口
│   ├── PowershellResult.cs                 # PS 返回反序列化
│   └── PowershellOptions.cs                # 配置绑定
├── Outbound/                               # F3
│   ├── OutboundActionDispatcher.cs         # 出站 action 调度
│   ├── OutboundQueue.cs                    # SQLite 持久化队列
│   └── AttachmentDownloader.cs             # 文件附件下载
├── Inbound/                                # F6
│   ├── InboundEventReporter.cs             # 上报服务端
│   ├── InboundEventBuilder.cs              # chat_data → InboundEvent
│   └── MonitorUsersCache.cs                # 白名单缓存
├── QrCode/                                 # F7
│   └── QrCodeWatcher.cs                    # 二维码定时检测
├── Health/                                 # 基础设施补全
│   └── DesktopHealthSupervisor.cs          # 桌面健康检查
└── Session/                                # 状态机整合
    └── ClientSession.cs                    # 编排所有模块

src/Client.Core/Protocol/
├── IMessageWatcher.cs                      # F4/F5 共同接口
└── IAgentApiClient.cs                      # 补充方法
```

### 11.2 删除（被 PS 取代）

| 文件 | 原因 |
|------|------|
| `Client.Automation/FlaUi/FlaUiDriver.cs` | UIA3 实测企微 dump 不到控件 |
| `Client.Automation/Vision/QwenVisionLocator.cs` | bbox 不稳定，已放弃 |
| `Client.Automation/Vision/OcrVisionLocator.cs` | Windows OCR 中文失败率太高 |
| `Client.Automation/Vision/VisionCache.cs` | 仅服务于 QwenVisionLocator |
| `Client.Automation/Vision/IScreenCapturer.cs` | 仅服务于 Vision 路径 |
| `Client.Automation/WeCom/ConversationNavigator.cs` | 由 PS search_user 取代 |
| `Client.Automation/WeCom/SendMessageService.cs` | 由 PS send_* 取代 |
| `Client.Automation/WeCom/MessageWatcher.cs` | 由 F4 ChatArchiveListener 取代 |
| `Client.Automation/WeComAutomation.cs` | 旧实现 |
| `Client.Automation/Nodes/NodesConfig*.cs` | UIA3 节点常量已无意义 |

`Client.Automation` 工程改造后仅保留：
- `Win32/InputExecutor.cs`（PS 不直接调，但 C# 端 UI 操作还要用）
- `Contracts/IAutomationContracts.cs`（接口契约）
- `Powershell/PowershellAutomationBackend.cs`（IWeComAutomation + IActionExecutor 实现，调 PS）

### 11.3 修改

| 文件 | 改动 |
|------|------|
| `Client.App/RpaHost.cs` | DI 注册新组件 |
| `Client.Core/Protocol/IAgentApiClient.cs` | 加 `GetMonitorUsersAsync` / `ReportStatusAsync` / `UploadMediaAsync` |
| `Client.Core/Config/ClientOptions.cs` | 加 `MessageSource` / `QrCode` / `Outbound` 配置段 |
| `configs/client.example.yaml` | 完整配置模板 |
| `WeComPersonalRpaClient.sln` | 移除 `Client.VisionRegression` 工程（已无用） |

---

## 12. 协议扩展（需更新 protocol.md）

### 12.1 `/config` 响应新增字段

```json
{
  "protocol_version": "1.0.0",
  "archive_enabled": true,           // 新增：服务端是否启用会话存档
  "monitor_users": {                 // 新增：绑定级白名单
    "binding_abc": {
      "user_names": ["陆伟"],
      "user_ids": ["wm_xxx"]
    }
  },
  ...
}
```

### 12.2 `status` 事件新增字段

`RpaStatusPayload` 加 `qr_image_base64`：

```json
{
  "status": "need_login",
  "account_display_name": "销售-王经理",
  "detail": "二维码已展示",
  "qr_image_ref": null,             // 弃用（短期文件引用方式）
  "qr_image_base64": "iVBORw0KG..." // 新增：base64 PNG
}
```

### 12.3 新增「媒体上传」接口

客户端拿到会话存档的媒体文件后，需要上传到服务端：

```
POST /api/v1/channels/wecom-personal-rpa/media-upload
Headers: HMAC 签名 + Content-Type: multipart/form-data
Body: 文件二进制

Response:
{
  "url": "https://agent.example.com/files/xxx?sig=...",
  "expires_at": "2026-06-26T11:00:00+08:00"
}
```

服务端用短期签名 URL（24 小时有效），客户端把 URL 填入 `InboundEvent.attachments[].url` 上报。

---

## 13. 关键决策记录

| # | 决策 | 理由 |
|---|------|------|
| 1 | **C# 调 PS 而非重写到 C#** | PS 已是真机验证可用的唯一代码；重写无收益、有风险 |
| 2 | **会话存档 API 作为主方案** | 唯一不依赖 UI 视觉、稳定 ID、合规、官方支持的方案 |
| 3 | **Fallback 方案先占位不开发** | 已验证方案（Windows 通知、视觉、OCR、内存读）全部失败；剩余候选都不够稳；先 ship F4，F5 留待真实需求驱动 |
| 4 | **二维码 base64 直推** | 用户决策；服务端 Redis 30 秒 TTL，不入审计/DB |
| 5 | **媒体文件经服务端中转** | 客户端不长期保存敏感文件，下载后立即上传到服务端 + 删除本地 |
| 6 | **白名单服务端二次校验** | 客户端缓存只是优化，安全边界必须在服务端 |
| 7 | **删除所有视觉/UIA 相关代码** | 已永久放弃，留着误导后人 |
| 8 | **不做限速** | 用户决策；服务端已有 `rate_limits` 配置位，暂不启用 |
| 9 | **强制 powershell.exe (5.1)** | 用户机器默认安装；编码坑已通过 BOM + keywords.txt 解决 |
| 10 | **客户端 SQLite 持久化 seq + 出站队列** | 重启不丢消息、不丢待发送 action |

---

## 14. 不在范围内

| 事项 | 原因 |
|------|------|
| F5 Fallback 实际方案 | 当前所有候选都不可靠，留作占位 |
| 限速 | 用户决策不做 |
| 多账号并发 | 单账号验证完毕后再扩展 |
| 群聊定向 @ 提及 | 群聊单独设计 |
| 消息撤回的客户端处理 | 会话存档 `recall` 事件入队，但 agent 侧处理逻辑另议 |
| Linux/macOS 支持 | 项目仅 Windows |

---

## 15. 验收清单

### 15.1 单元测试

- [ ] `PowershellOpsInvoker` 全套（mock Process）
- [ ] `ChatArchiveListener` 拉取 + 解密 + 解析
- [ ] `ArchiveCryptoService` RSA + AES 解密（已知向量验证）
- [ ] `ArchiveSeqStore` 持久化 + 续传
- [ ] `MonitorUsersCache` 白名单过滤
- [ ] `OutboundActionDispatcher` 队列 + 串行 + 重试
- [ ] `QrCodeWatcher` 状态转换（online ↔ need_login）

### 15.2 真机回归

- [ ] 服务端 `/config` 下发 `archive_enabled=true` + `monitor_users` → 客户端正确缓存
- [ ] F4 拉取真实会话存档数据 → 解密成功 → 解析为 InboundEvent
- [ ] F4 媒体文件下载 → 上传服务端 → URL 回填 attachments
- [ ] F3 服务端下发 send_text/send_image/send_file action → 客户端执行 → action_result 回执
- [ ] F7 拔掉网络让企微掉线 → 30 秒内二维码截取 → 上报服务端 → 平台后台展示
- [ ] F6 白名单过滤：非白名单用户的消息客户端不上报（且服务端二次校验也拦住）
- [ ] 客户端崩溃重启 → 从 SQLite 续传 seq + 出站 action 不丢

### 15.3 集成测试

- [ ] 服务端 `/media-upload` 接口
- [ ] 服务端 `/config` 返回新字段
- [ ] 服务端 status callback 接受 `qr_image_base64`
- [ ] 完整链路：用户发消息 → F4 检测 → F6 上报 → agent 推理 → F3 发送 → 用户收到

### 15.4 文档

- [ ] `docs/system/wecom-personal-rpa-protocol.md` 更新（§A 新字段 + §media-upload 接口）
- [ ] `docs/system/wecom-personal-rpa-design.md` §3.4 / §6.2 / §13 更新
- [ ] `docs/ideas.md` #29 更新
- [ ] `clients/wecom-personal-rpa/docs/操作手册.md` 重写（按新架构）

---

## 16. 设计摘要

本设计按用户提出的 7 大功能模块组织客户端实现：

- **F1 服务端通信**：已有（HMAC + WebSocket + callback）
- **F2 PowerShell 自动化**：复用 `debug-navigate.ps1` 真机验证能力，C# 通过 Process + JSON 调用
- **F3 出站执行**：服务端回复 → ActionEnvelope → 串行队列 → 调 PS 发送
- **F4 会话存档 API**：主方案，企业开通后用 RSA 解密 + seq 持久化拿稳定 ID 消息
- **F5 Fallback**：占位，已验证方案（Windows 通知、视觉、OCR）全部失败，留待真实需求驱动
- **F6 入站解析+上报**：白名单双过滤（客户端 + 服务端）+ 媒体经服务端中转
- **F7 二维码上报**：未登录时 30 秒一次截取，base64 直推服务端 Redis 30 秒 TTL

配套基础设施：进程监控、桌面健康检查、WebSocket 重连、暂停恢复、操作日志（含二维码脱敏）。

清理动作：删除 FlaUi / Vision / OcrVisionLocator / ConversationNavigator / SendMessageService / MessageWatcher 等已永久放弃的实现，避免误导后人。
