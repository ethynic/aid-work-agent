# 开发计划 —— 企业微信个人账号 RPA 客户端

> 关联：
> - 设计文档：[docs/system/wecom-personal-rpa-client-design.md](../docs/system/wecom-personal-rpa-client-design.md)
> - 主计划（服务端 + 部署）：[plans/plan-wecom-personal-rpa.md](./plan-wecom-personal-rpa.md)
> - 调试脚本：[clients/wecom-personal-rpa/scripts/debug-navigate.ps1](../clients/wecom-personal-rpa/scripts/debug-navigate.ps1)
>
> 登记位置：[docs/ideas.md](../docs/ideas.md) #29
>
> 创建日期：2026-06-26
> 状态：🔧 计划已制定，待执行

---

## 0. 总览

### 0.1 工作分块

| 块 | 内容 | 工时估 | 依赖 |
|----|------|--------|------|
| **A** | 清理：删除已放弃的视觉/UIA C# 代码 + 文档更新 | 4h | 无 |
| **B** | F2 PS 工程化：`debug-navigate.ps1` → `wecom-ops.ps1` + lib + C# `PowershellOpsInvoker` | 6h | A |
| **C** | F3 出站执行：`OutboundActionDispatcher` + SQLite 队列 + 附件下载 | 6h | B |
| **D** | F4 会话存档 API 监听（主方案） | 12h | A（独立于 B/C） |
| **E** | F6 入站解析 + 上报 + 白名单过滤 | 6h | D |
| **F** | F7 二维码截取 + 上报 | 4h | B |
| **G** | 基础设施补全：桌面健康检查 + WebSocket 重连 + 暂停响应 | 4h | A |
| **H** | 服务端协议扩展：`/config` 字段、`/media-upload` 接口、`qr_image_base64` | 4h | 无（可与 A-G 并行） |
| **I** | 真机端到端联调 + 回归 | 8h | A-H 全部完成 |

**总工时**：约 54 小时（7 个工作日）。

### 0.2 阶段切分

```
阶段 1（A 单独做）：清理 → 编译通过 → 测试不回归
   ↓
阶段 2（B + H 并行）：PS 工程化 + 服务端协议扩展
   ↓
阶段 3（C + D + F 并行）：出站执行 + 会话存档 + 二维码
   ↓
阶段 4（E + G）：入站解析 + 白名单 + 基础设施
   ↓
阶段 5（I）：真机回归
```

### 0.3 关键里程碑

| 里程碑 | 验收 | 涉及阶段 |
|--------|------|---------|
| M1：旧代码清理完毕、视觉/UIA 全删、构建 0 错误 | 阶段 1 完成 |
| M2：服务端发文本 → C# → PS → 企微真机发送成功 | 阶段 2 完成 |
| M3：服务端发图片/文件 → 真机发送成功 | 阶段 3 完成 |
| M4：F4 拉取真实会话存档 → 解密 → 解析为 InboundEvent | 阶段 3 完成 |
| M5：未登录二维码截取 → 服务端展示 → 扫码恢复 | 阶段 3 完成 |
| M6：完整链路联调（发消息 → 监听 → 上报 → agent → 回复 → 发送）| 阶段 5 完成 |

---

## 阶段 1：清理 + 文档（块 A）

### 1.1 目标

删除已永久放弃的视觉/UIA/OCR 实现，避免误导后续开发。视觉/UIA/Qwen3-VL 路径在 status-2026-06-26.md 已实测全部失败。

### 1.2 任务清单

- [ ] **1.2.1** 删除 C# 文件
  - `Client.Automation/FlaUi/FlaUiDriver.cs`
  - `Client.Automation/Vision/QwenVisionLocator.cs`
  - `Client.Automation/Vision/OcrVisionLocator.cs`
  - `Client.Automation/Vision/VisionCache.cs`
  - `Client.Automation/Vision/IScreenCapturer.cs`
  - `Client.Automation/Vision/VisionApiExceptions.cs`
  - `Client.Automation/WeCom/ConversationNavigator.cs`
  - `Client.Automation/WeCom/SendMessageService.cs`
  - `Client.Automation/WeCom/MessageWatcher.cs`
  - `Client.Automation/WeComAutomation.cs`
  - `Client.Automation/Nodes/NodesConfig*.cs`（如有）
  - `Client.App/Services/MessageWatcher.cs`（旧 OCR 版）
  - `Client.App/Services/SendMessageService.cs`（如重复）
  - `Client.VisionRegression/` 整个工程（如已无用）

- [ ] **1.2.2** 删除关联测试
  - `Client.Tests/Vision/` 整个目录
  - `Client.Tests/Automation/` 下涉及 FlaUi/QwenVisionLocator 的测试

- [ ] **1.2.3** 修改 `WeComPersonalRpaClient.sln`
  - 移除 `Client.VisionRegression` 工程（如已无用）
  - 移除对视觉工程的引用

- [ ] **1.2.4** 修改 `Client.Automation/Client.Automation.csproj`
  - 移除 FlaUI 包引用：`FlaUI.UIA3`、`FlaUI.UIA2`、`FlaUI.Core`
  - 移除 OpenCvSharp 包引用（如有）
  - 移除 Qwen VL 相关依赖

- [ ] **1.2.5** 修改 `Client.App/RpaHost.cs`
  - 移除 DI 注册：`WeComAutomation`、`QwenVisionLocator`、`FlaUiDriver`、`VisionCache` 等
  - 临时占位：先注释掉，留待阶段 2 替换为 `PowershellOpsInvoker`

- [ ] **1.2.6** 修改 `Client.App/Services/` 下引用旧实现的代码
  - `MessageWatcher.cs` / `SendMessageService.cs` 等引用 `IWeComAutomation` 的地方
  - 先把调用点注释掉或改为 throw NotImplementedException（待阶段 2 替换）

- [ ] **1.2.7** 文档同步
  - `docs/system/wecom-personal-rpa-design.md` §6.2 三层自动化策略 → 改为「已废弃，见 [客户端设计](./wecom-personal-rpa-client-design.md)」
  - `docs/system/wecom-personal-rpa-design.md` §13.4 实现进度 → 更新当前状态（删除 Qwen3-VL 落地描述）
  - `docs/ideas.md` #29 → 更新（删除 vision/enter-fix 关联，加 client-design）

- [ ] **1.2.8** 构建验证
  - `dotnet build WeComPersonalRpaClient.sln` 0 错误（允许有 warning 暂时存在）
  - `dotnet test` 现有用例不回归（视觉测试已删，其他测试不受影响）

### 1.3 验收

- [ ] 视觉/UIA/Qwen3-VL 相关 C# 文件全部删除
- [ ] `dotnet build` 0 错误
- [ ] `dotnet test` 通过（视觉测试已删）
- [ ] `docs/ideas.md` #29 已更新
- [ ] 主设计 §6.2 / §13 已标注废弃

---

## 阶段 2：PS 工程化 + 服务端协议扩展（块 B + H 并行）

### 2.1 块 B：PS 工程化 + C# 调用层

#### 2.1.1 目标

`debug-navigate.ps1` → `wecom-ops.ps1` + `wecom-ops-lib.ps1`，加上 C# `PowershellOpsInvoker`，让客户端能通过 PS 完成 search_user / send_text / send_image 三个操作。

#### 2.1.2 任务清单

- [ ] **2.1.2.1** 提取 `scripts/wecom-ops-lib.ps1`
  - 从 `debug-navigate.ps1` 提取稳定函数（见设计 §3.5）
  - 文件存为 UTF-8 with BOM
  - 包含：`Click-At` / `Type-Text` / `Press-CtrlA-Delete` / `Get-WeWorkWindowOrigin` / `Capture-WeCom` / `Load-Keywords` / `Load-Win32` / `ConvertTo-Hashtable`

- [ ] **2.1.2.2** 写 `scripts/wecom-ops.ps1` 主入口
  - 顶部 param：`-Action`（必填，ValidateSet）
  - stdin 读 JSON 参数：`$params = [Console]::In.ReadToEnd() | ConvertFrom-Json`
  - dot-source `wecom-ops-lib.ps1`
  - `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`
  - switch 分发到各 action 函数
  - 异常统一捕获 → 输出 `ps_script_exception` 错误码
  - 文件存为 UTF-8 with BOM

- [ ] **2.1.2.3** 实现 PS `Search-WeComUser` 函数
  - 算法见设计 §3.4 + `debug-navigate.ps1` Step 2-5
  - 新增会话标题验证（PaddleOCR 识别顶部小区域）
  - 输出标准 JSON

- [ ] **2.1.2.4** 实现 PS `Send-WeComText` 函数
  - 内部调 `Search-WeComUser` 进入会话 → `Type-Text` + Enter
  - 复用 `debug-navigate.ps1` 的 keywords.txt（中文消息文本从字典读）

- [ ] **2.1.2.5** 实现 PS `Send-WeComImage` 函数
  - 内部调 `Search-WeComUser` → 读本地图片到 Bitmap → `Clipboard.SetImage` → Ctrl+V → Enter
  - 剪贴板重试 3 次

- [ ] **2.1.2.6** 实现 PS `Get-WeComLoginState` 函数（F7 准备）
  - EnumWindows 找 WeWorkWindow → 截图 → PaddleOCR 识别中央区域
  - 状态：`online` / `need_login` / `qr_expired` / `offline`
  - `need_login` 时截取二维码区域 base64 输出

- [ ] **2.1.2.7** 新增 C# `Powershell/PowershellOpsInvoker.cs`
  - 位置：`Client.App/Powershell/PowershellOpsInvoker.cs`
  - 核心方法 `InvokeAsync(string action, object parameters, CancellationToken ct)`
  - `Process.Start("powershell.exe", "-ExecutionPolicy Bypass -NoProfile -File ... -Action ...")`
  - stdin 写 JSON → Close
  - stdout 取最后一行 JSON 反序列化
  - 超时控制 + stderr 写日志

- [ ] **2.1.2.8** 新增 C# `Powershell/PowershellResult.cs`、`Powershell/PowershellOptions.cs`
  - `PowershellResult`：`Success` / `Action` / `Data`（JsonElement）/ `ErrorCode` / `ErrorMessage` / `DurationMs`
  - `PowershellOptions`：脚本路径 / powershell.exe 路径 / 超时

- [ ] **2.1.2.9** 修改 `Client.Core/Config/ClientOptions.cs`
  - 加 `Automation` 配置段（绑定 yaml `automation.*`）

- [ ] **2.1.2.10** 修改 `Client.App/RpaHost.cs` DI 注册
  - 注册 `PowershellOpsInvoker` 为 singleton
  - 实现 `IWeComAutomation` / `IActionExecutor` 接口（见 `IAutomationContracts.cs`）的薄封装委托给 PS

- [ ] **2.1.2.11** 扩展 `Client.Tests/Automation/`
  - 新增 `PowershellOpsInvokerTests.cs`
  - 测试用例：
    - `InvokeAsync_Success_ParsesJsonResponse`（mock Process）
    - `InvokeAsync_PsExitNonZero_ReturnsFailure`
    - `InvokeAsync_NoJsonInStdout_ReturnsFailure`
    - `InvokeAsync_Timeout_CancelsProcess`

- [ ] **2.1.2.12** PS 端独立冒烟测试
  - 命令：`echo '{"keyword":"陆伟"}' | powershell -ExecutionPolicy Bypass -File wecom-ops.ps1 -Action search_user`
  - 同样测 send_text、send_image、get_login_state
  - 验收：4 个命令 JSON 输出正确，企微真机对应操作执行

### 2.2 块 H：服务端协议扩展

#### 2.2.1 目标

服务端补齐设计 §12 列的协议扩展：`/config` 新字段、`/media-upload` 接口、`status.qr_image_base64`。

#### 2.2.2 任务清单

- [ ] **2.2.2.1** 修改 `src/channels/wecom_personal_rpa/schemas.py`
  - `RpaConfigResponse` 加 `archive_enabled: bool`、`monitor_users: Dict[str, MonitorUsersEntry]`
  - 新增模型 `MonitorUsersEntry`：`user_names: List[str]`、`user_ids: List[str]`
  - `RpaStatusPayload` 加 `qr_image_base64: Optional[str]`
  - 弃用 `qr_image_ref`（保留字段名兼容，但客户端不再使用）

- [ ] **2.2.2.2** 修改 `/config` 路由
  - 位置：`src/saas/api/wecom_personal_rpa_routes.py` 的 `get_config` 函数
  - 查询当前 client 下所有 binding 的 `monitor_user_names` / `monitor_user_ids`
  - 组装 `monitor_users` 字段返回
  - `archive_enabled` 字段从环境变量或租户配置读（暂定：环境变量 `WECOM_RPA_ARCHIVE_ENABLED=true`）

- [ ] **2.2.2.3** 新增 `/media-upload` 接口
  - 位置：`src/saas/api/wecom_personal_rpa_routes.py`
  - 路由：`POST /api/v1/channels/wecom-personal-rpa/media-upload`
  - 入参：multipart/form-data 文件二进制 + HMAC 签名
  - 出参：`{"url": <短期签名 URL>, "expires_at": <ISO>}`
  - 实现：保存到 `storage/tenants/{tenant_id}/conversation/` + 生成 24 小时短期签名 URL

- [ ] **2.2.2.4** 数据库加白名单字段（如未做）
  - `deploy/db_update.sql` 加：
    ```sql
    ALTER TABLE wecom_rpa_conversation_bindings
        ADD COLUMN IF NOT EXISTS monitor_user_names TEXT[] DEFAULT '{}';
    ALTER TABLE wecom_rpa_conversation_bindings
        ADD COLUMN IF NOT EXISTS monitor_user_ids TEXT[] DEFAULT '{}';
    ```
  - `deploy/init-postgres.sql` 同步

- [ ] **2.2.2.5** 修改 `src/channels/wecom_personal_rpa/schemas.py` + `db.py`
  - `BindingPublic` 加 `monitor_user_names` / `monitor_user_ids`
  - binding CRUD 读写这两个字段

- [ ] **2.2.2.6** 后端管理 API 接受白名单
  - `src/saas/api/wecom_personal_rpa_admin.py` 的 `update_binding` 加字段

- [ ] **2.2.2.7** 服务端白名单二次校验
  - 修改 `src/channels/wecom_personal_rpa/router.py` 或 `message.py`
  - 新增 `_is_allowed_by_monitor_whitelist(binding, sender_display_name, sender_stable_id)`
  - 在 `_process_inbound_message` 解析 payload 后调用
  - 不通过：写审计 + return

- [ ] **2.2.2.8** 二维码 Redis 存储优化
  - `src/saas/api/wecom_personal_rpa_routes.py` 的 status callback 收到 `qr_image_base64` 时
  - 写 Redis：`wecom_rpa:qr:{account_id}` = base64，TTL 30 秒
  - **不入审计、不入 DB**
  - 新增查询接口：`GET /api/saas/wecom-personal-rpa/accounts/{id}/qr`（require_admin）

- [ ] **2.2.2.9** 后端单元测试 + 集成测试
  - `tests/unit/channels/wecom_personal_rpa/test_router.py` 加白名单过滤用例
  - `tests/integration/test_wecom_personal_rpa_whitelist.py` 完整链路
  - `tests/integration/test_wecom_personal_rpa_media_upload.py` 媒体上传

### 2.3 验收

- [ ] PS `wecom-ops.ps1` 4 个 action 真机通过（search_user / send_text / send_image / get_login_state）
- [ ] C# `PowershellOpsInvoker` 单元测试通过
- [ ] 服务端 `/config` 返回 `archive_enabled` + `monitor_users`
- [ ] 服务端 `/media-upload` 接口可用
- [ ] 服务端 status callback 接受 `qr_image_base64`
- [ ] 服务端白名单二次过滤生效
- [ ] 后端测试全通过

---

## 阶段 3：F3 出站 + F4 会话存档 + F7 二维码（块 C + D + F 并行）

### 3.1 块 C：F3 出站执行

#### 3.1.1 目标

服务端下发 actions → 客户端 SQLite 队列 → 串行执行 → 调 PS 发送 → 回执上报。

#### 3.1.2 任务清单

- [ ] **3.1.2.1** 新增 `Client.App/Outbound/OutboundQueue.cs`
  - SQLite 持久化（`outbox_local` 表）：`action_id` / `action_type` / `payload` / `status` / `retry_count` / `created_at`
  - 入队 / 出队 / 标记完成 / 标记失败

- [ ] **3.1.2.2** 新增 `Client.App/Outbound/AttachmentDownloader.cs`
  - `HttpClient.GetAsync(url)` 下载到 `outbound.download_temp_dir`
  - 校验 MIME + 大小
  - 返回本地路径

- [ ] **3.1.2.3** 新增 `Client.App/Outbound/OutboundActionDispatcher.cs`
  - 监听 WebSocket 推送 + 启动时拉 outbox
  - 入队
  - 账号级串行 Worker
  - 出队 → 映射 PS action → 调 `PowershellOpsInvoker`
  - 文件类 action 先 `AttachmentDownloader` 下载 → PS 执行完删除本地副本
  - 结果上报 `IAgentApiClient.ReportActionResultAsync`

- [ ] **3.1.2.4** 实现 PS `Send-WeComFile` 函数
  - 算法见设计 §3.3
  - 用 `Clipboard.SetFileDropList`

- [ ] **3.1.2.5** 扩展 `Client.Core/Protocol/IAgentApiClient.cs`
  - 加 `ReportActionResultAsync(string requestId, ActionResultPayload payload, CancellationToken ct)`

- [ ] **3.1.2.6** 单元测试 `OutboundActionDispatcherTests`
  - mock PowershellOpsInvoker
  - 测试用例：入队/出队/串行/重试/失败回执

### 3.2 块 D：F4 会话存档 API 监听

#### 3.2.1 目标

客户端按 `message_source.mode=archive` 拉取企业微信会话存档数据，解密 + 解析为 InboundEvent。

#### 3.2.2 任务清单

- [ ] **3.2.2.1** 新增 `Client.Core/Protocol/IMessageWatcher.cs`
  - 接口见设计 §6.1
  - `StartAsync` / `StopAsync` / `NewMessageReceived` 事件

- [ ] **3.2.2.2** 新增 `Client.App/MessageArchive/ChatArchiveListener.cs`
  - 实现 `IMessageWatcher`
  - 主循环：拉取 → 解密 → 解析 → 推 InboundEvent
  - 用 `PeriodicTimer` 控制拉取周期

- [ ] **3.2.2.3** 新增 `Client.App/MessageArchive/ArchiveHttpClient.cs`
  - 封装企微会话存档 HTTP API
  - `GetAccessTokenAsync(corpid, secret)` → 缓存（2 小时 TTL）
  - `GetChatDataAsync(token, seq, limit)` → chatdata_list
  - `DownloadMediaAsync(token, sdkfileid, filename)` → 字节流

- [ ] **3.2.2.4** 新增 `Client.App/MessageArchive/ArchiveCryptoService.cs`
  - `DecryptRandomKey(rsaPrivateKeyPem, encryptRandomKey)` → random_key（RSA-OAEP）
  - `DecryptChatMsg(randomKey, encryptChatMsg)` → 明文 JSON（AES-256-CBC）
  - 用 .NET 内置 `RSA` + `AesCng`/`AesCryptoServiceProvider`

- [ ] **3.2.2.5** 新增 `Client.App/MessageArchive/ArchiveSeqStore.cs`
  - SQLite 表：`archive_seq(account_id PK, seq BIGINT, updated_at)`
  - 读 / 写 seq
  - 启动时读 → 续传

- [ ] **3.2.2.6** 新增 `Client.App/MessageArchive/ArchiveMediaDownloader.cs`
  - 接收 `(sdkfileid, filename, msgtype)`
  - 调 `ArchiveHttpClient.DownloadMediaAsync` → 保存到 `media_temp_dir`
  - 返回本地路径

- [ ] **3.2.2.7** 新增 `Client.Core/Config/ArchiveOptions.cs`
  - 配置绑定：`mode` / `corpid` / `secret` / `private_key_path` / `poll_interval_seconds` / `batch_limit` / `download_media` / `media_temp_dir`

- [ ] **3.2.2.8** 修改 `Client.Core/Config/ClientOptions.cs`
  - 加 `MessageSource` 配置段

- [ ] **3.2.2.9** 单元测试
  - `ArchiveCryptoServiceTests`：用已知向量验证 RSA + AES 解密正确
  - `ArchiveSeqStoreTests`：持久化 + 续传
  - `ChatArchiveListenerTests`：mock HttpClient，验证拉取/解析/推事件

#### 3.2.3 验收

- [ ] 真机用真实 `corpid`+`secret`+`private_key` 拉取一次数据
- [ ] 解密文本消息成功
- [ ] 媒体文件下载成功
- [ ] seq 持久化，重启续传

### 3.3 块 F：F7 二维码截取

#### 3.3.1 目标

未登录时 PS 30 秒一次截取二维码 → base64 → 上报服务端。

#### 3.3.2 任务清单

- [ ] **3.3.2.1** 新增 `Client.App/QrCode/QrCodeWatcher.cs`
  - 实现 + 算法见设计 §8.4
  - `Timer` 每 30s 调一次 `PowershellOpsInvoker.InvokeAsync("get_login_state", {})`
  - 状态转换：online ↔ need_login
  - 调 `IAgentApiClient.ReportStatusAsync`

- [ ] **3.3.2.2** 扩展 `Client.Core/Protocol/IAgentApiClient.cs`
  - 加 `ReportStatusAsync(StatusPayload payload, CancellationToken ct)`

- [ ] **3.3.2.3** 修改 `Client.Core/Protocol/StatusPayload.cs`
  - 加 `QrImageBase64` 字段

- [ ] **3.3.2.4** PS 端二维码区域校准
  - 用 `verify-toast-listener.ps1` 类似方式，写真机探测脚本 `verify-qr-region.ps1`
  - 真机截取企微登录页，找二维码固定坐标
  - 回填 `client.example.yaml: qr_code.region_bbox_base`

- [ ] **3.3.2.5** 单元测试 `QrCodeWatcherTests`
  - 状态转换：online → need_login → qr_expired → online
  - 持续未登录时每 30s 推一次

### 3.4 阶段 3 验收

- [ ] C# 客户端启动后能拉取真实会话存档数据
- [ ] 服务端下发 send_text/send_image/send_file → 客户端真机发送成功
- [ ] 企微未登录时 30s 内服务端展示二维码
- [ ] 单元测试全通过

---

## 阶段 4：F6 入站解析 + 基础设施（块 E + G）

### 4.1 块 E：F6 入站解析 + 上报 + 白名单

#### 4.1.1 目标

F4 拿到的 chat_data → InboundEvent → 白名单过滤 → 上报服务端。

#### 4.1.2 任务清单

- [ ] **4.1.2.1** 新增 `Client.App/Inbound/InboundEventBuilder.cs`
  - 把 ArchiveChatData（F4 输出）转成 InboundEvent
  - 字段映射见设计 §7.3
  - 媒体附件：调 ArchiveMediaDownloader 拿本地路径 → 调 `IAgentApiClient.UploadMediaAsync` 拿 URL → 填入 attachments

- [ ] **4.1.2.2** 扩展 `Client.Core/Protocol/IAgentApiClient.cs`
  - 加 `UploadMediaAsync(string localPath, CancellationToken ct) -> string url`
  - 加 `ReportInboundAsync(InboundEvent evt, CancellationToken ct)`

- [ ] **4.1.2.3** 新增 `Client.App/Inbound/MonitorUsersCache.cs`
  - 启动时调 `/config` 拉取 `monitor_users` 字段
  - 内存缓存 + 每 60 分钟刷新
  - 服务端 `config_invalidate` WebSocket 推送时立即刷新
  - `IsAllowed(bindingId, senderName, senderId) -> bool`

- [ ] **4.1.2.4** 新增 `Client.App/Inbound/InboundEventReporter.cs`
  - 订阅 `IMessageWatcher.NewMessageReceived`
  - 调 `InboundEventBuilder` 构造 InboundEvent
  - 调 `MonitorUsersCache.IsAllowed` 过滤
  - 通过 → 调 `IAgentApiClient.ReportInboundAsync`
  - 不通过 → 写本地日志（不上报，但记 audit）

- [ ] **4.1.2.5** 在 `RpaHost.cs` 串联
  - ChatArchiveListener.NewMessageReceived += InboundEventReporter.Handle
  - 注册 InboundEventReporter / MonitorUsersCache

- [ ] **4.1.2.6** 单元测试
  - `MonitorUsersCacheTests`：白名单过滤逻辑
  - `InboundEventBuilderTests`：字段映射
  - `InboundEventReporterTests`：白名单通过/拒绝两条路径

### 4.2 块 G：基础设施补全

#### 4.2.1 目标

桌面健康检查、WebSocket 重连、暂停响应。

#### 4.2.2 任务清单

- [ ] **4.2.2.1** 新增 `Client.App/Health/DesktopHealthSupervisor.cs`
  - 实现 `IHealthSupervisor`
  - 检查项：锁屏 / 企微窗口可见 / 分辨率变化 / 企微进程存活 / 剪贴板
  - 见设计 §9.2
  - 60 秒一次轮询

- [ ] **4.2.2.2** 加固 WebSocket 重连
  - 修改 `Client.Core/AgentApi/AgentApiClient.cs`
  - 指数退避：1s/2s/4s/8s/16s/30s
  - 心跳：30s 一次 ping
  - 重连后拉 outbox 增量
  - `NetworkChange.NetworkAvailabilityChanged` 事件触发立即重连

- [ ] **4.2.2.3** 暂停/恢复响应
  - 修改 `Client.App/Session/ClientSession.cs`
  - 处理服务端 `paused_scope` 推送
  - account/conversation/tenant 三级暂停的客户端侧表现
  - 见设计 §9.4

- [ ] **4.2.2.4** 操作日志结构化
  - 修改 `Client.App/Program.cs` 的 Serilog 配置
  - 加脱敏过滤器（剔除 secret/qr_base64/signature/绝对路径）
  - 关键事件（启动/关闭/PS 调用）写 Windows Event Log

### 4.3 阶段 4 验收

- [ ] F4 拉到消息后白名单生效（非白名单 sender 不上报）
- [ ] 桌面锁屏后客户端暂停发送 + 状态上报
- [ ] WebSocket 断网后自动重连 + outbox 续传
- [ ] 服务端 pause → 客户端停止 action

---

## 阶段 5：真机端到端 + 回归（块 I）

### 5.1 目标

完整真机验证 7 大功能 + 基础设施。

### 5.2 任务清单

- [ ] **5.2.1** 准备真机环境
  - 企微 5.0.8+、Windows 11、DPI 100%/150%
  - 测试账号：陆伟 / 孙晨 / 芮秀
  - 后端服务端启动 + 开通会话存档

- [ ] **5.2.2** F2 验证：搜索 + 发文本/图片/文件
  - 真机各 10 次 → 成功率 ≥ 90%
  - **误发 0 次**（每次发送前会话标题验证）

- [ ] **5.2.3** F4 验证：会话存档监听
  - 真机连续 1 小时
  - 3 个测试账号各发 20 条消息
  - 人工抽样：漏抓率 < 3%

- [ ] **5.2.4** F6 验证：白名单过滤
  - 配置 binding 只监控"陆伟"
  - 给陆伟 + 孙晨 + 芮秀 各发 5 条
  - 验证：只收到陆伟的 5 条

- [ ] **5.2.5** F7 验证：二维码
  - 主动让企微掉线（关闭进程）
  - 30 秒内服务端展示二维码
  - 扫码恢复 → 状态转为 online

- [ ] **5.2.6** 异常场景
  - 桌面锁屏 → 暂停 + 状态上报
  - 网络断开 → WebSocket 重连 + outbox 续传
  - 客户端崩溃重启 → seq 续传 + 出站 action 不丢

- [ ] **5.2.7** 完整链路联调
  - 用户发消息 → F4 监听 → F6 上报 → agent 推理 → 服务端 actions → F3 发送 → 用户收到回复
  - 端到端延迟 < 30s

- [ ] **5.2.8** 媒体附件验证
  - 用户发图片 → F4 下载 → F6 上传服务端 → agent 处理 → F3 发图片回复

### 5.3 验收

- [ ] 7 大功能真机全部通过
- [ ] 漏抓率 < 3%
- [ ] 误发 0 次
- [ ] 端到端延迟 < 30s
- [ ] 异常场景全部正确处理

---

## 风险与依赖

### 风险

| 风险 | 阶段 | 缓解 |
|------|------|------|
| PS 5.1 中文编码坑 | 阶段 2 | BOM + keywords.txt，已在 debug-navigate.ps1 验证 |
| 企微会话存档 API 调用失败（凭证/网络） | 阶段 3 | 失败重试 + 不更新 seq；运维侧准备好凭证 |
| RSA 解密在 .NET 8 实现坑 | 阶段 3 | 用 BouncyCastle 兜底（如有兼容性问题） |
| 媒体文件下载占用带宽 | 阶段 3 | download_media 配置开关，可关闭 |
| 二维码区域坐标不准 | 阶段 3 | 真机校准脚本 + 配置可调 |
| 企微升级导致固定坐标失效 | 阶段 5 | 所有坐标配置化，升级后改配置不改代码 |

### 依赖

- **强依赖**：企微 5.0.8+、Windows 11、.NET 8 SDK、PowerShell 5.1（系统自带）
- **F4 依赖**：企业微信开通「会话内容存档」+ 拿到 corpid/secret/private_key
- **无依赖**：F5 fallback 方案（占位不开发）

---

## 不在本计划范围内

| 事项 | 原因 |
|------|------|
| F5 Fallback 实际方案 | 当前候选都不可靠，留作占位 |
| 限速 | 用户决策不做 |
| 多账号并发 | 单账号验证完毕后再扩展 |
| 群聊 @ 提及 | 群聊单独设计 |
| Linux/macOS 支持 | 项目仅 Windows |
| 消息撤回的 agent 侧处理 | recall 事件入队，agent 逻辑另议 |

---

## 附录 A：阶段依赖关系图

```
阶段 1 (清理)
   ↓
   └────────────────┐
   ↓                ↓
阶段 2 (PS+协议)   阶段 4-G (基础设施)
   ↓                │
   ├──────────┐     │
   ↓          ↓     │
阶段 3-C  阶段 3-D  │
(F3 出站) (F4 监听) │
   │          ↓     │
   │       阶段 4-E (F6 入站) ←─┘
   ↓          │
   └──────┬───┘
          ↓
       阶段 5 (真机回归)
```

阶段 2 / 阶段 4-G / 块 H 可以并行启动（不互相依赖）。

---

## 附录 B：文件改动清单

### 新增（C#）

| 路径 | 用途 |
|------|------|
| `Client.App/Powershell/PowershellOpsInvoker.cs` | C# 调 PS 入口 |
| `Client.App/Powershell/PowershellResult.cs` | PS 返回反序列化 |
| `Client.App/Powershell/PowershellOptions.cs` | 配置绑定 |
| `Client.App/Outbound/OutboundQueue.cs` | SQLite 持久化队列 |
| `Client.App/Outbound/AttachmentDownloader.cs` | 文件附件下载 |
| `Client.App/Outbound/OutboundActionDispatcher.cs` | 出站调度 |
| `Client.App/MessageArchive/ChatArchiveListener.cs` | F4 主循环 |
| `Client.App/MessageArchive/ArchiveHttpClient.cs` | 企微 API 封装 |
| `Client.App/MessageArchive/ArchiveCryptoService.cs` | RSA + AES |
| `Client.App/MessageArchive/ArchiveSeqStore.cs` | seq 持久化 |
| `Client.App/MessageArchive/ArchiveMediaDownloader.cs` | 媒体下载 |
| `Client.App/MessageWatchers/FallbackMessageWatcher.cs` | F5 占位 |
| `Client.App/Inbound/InboundEventBuilder.cs` | F6 字段映射 |
| `Client.App/Inbound/InboundEventReporter.cs` | F6 上报 |
| `Client.App/Inbound/MonitorUsersCache.cs` | F6 白名单缓存 |
| `Client.App/QrCode/QrCodeWatcher.cs` | F7 二维码检测 |
| `Client.App/Health/DesktopHealthSupervisor.cs` | 桌面健康检查 |
| `Client.Core/Protocol/IMessageWatcher.cs` | F4/F5 共同接口 |
| `Client.Core/Config/ArchiveOptions.cs` | F4 配置 |

### 新增（PS）

| 路径 | 用途 |
|------|------|
| `clients/wecom-personal-rpa/scripts/wecom-ops.ps1` | 主入口 |
| `clients/wecom-personal-rpa/scripts/wecom-ops-lib.ps1` | 函数库 |

### 修改（C#）

| 路径 | 改动 |
|------|------|
| `Client.Core/Protocol/IAgentApiClient.cs` | 加 ReportActionResultAsync / ReportStatusAsync / UploadMediaAsync / ReportInboundAsync / GetMonitorUsersAsync |
| `Client.Core/Protocol/StatusPayload.cs` | 加 QrImageBase64 |
| `Client.Core/Config/ClientOptions.cs` | 加 Automation / MessageSource / QrCode / Outbound / MonitorUsers 段 |
| `Client.App/RpaHost.cs` | DI 注册全部新组件 |
| `Client.App/Session/ClientSession.cs` | 串联所有模块 + 暂停响应 |
| `Client.Core/AgentApi/AgentApiClient.cs` | WebSocket 重连加固 |
| `Client.App/Program.cs` | Serilog 脱敏配置 |
| `Client.Automation/Client.Automation.csproj` | 移除 FlaUI / OpenCvSharp 依赖 |
| `WeComPersonalRpaClient.sln` | 移除 VisionRegression 工程 |

### 修改（Python 服务端）

| 路径 | 改动 |
|------|------|
| `src/channels/wecom_personal_rpa/schemas.py` | RpaConfigResponse + RpaStatusPayload + BindingPublic 加字段；新增 MonitorUsersEntry |
| `src/channels/wecom_personal_rpa/db.py` | binding CRUD 加白名单字段 |
| `src/channels/wecom_personal_rpa/router.py` 或 `message.py` | 白名单二次校验 |
| `src/saas/api/wecom_personal_rpa_routes.py` | /config 下发新字段 + /media-upload 新接口 + 二维码 Redis |
| `src/saas/api/wecom_personal_rpa_admin.py` | update_binding 加白名单 |
| `deploy/db_update.sql` | 加 monitor_user_names / monitor_user_ids 字段 |
| `deploy/init-postgres.sql` | 同步 |

### 修改（配置 + 文档）

| 路径 | 改动 |
|------|------|
| `clients/wecom-personal-rpa/configs/client.example.yaml` | 完整配置模板（见设计 §10） |
| `docs/system/wecom-personal-rpa-protocol.md` | 新字段说明 + media-upload 接口 |
| `docs/system/wecom-personal-rpa-design.md` | §3.4 / §6.2 / §13 更新 |
| `docs/ideas.md` #29 | 更新关联文档 |
| `clients/wecom-personal-rpa/docs/操作手册.md` | 重写（按新架构） |
| `clients/wecom-personal-rpa/STATUS.md` | 更新 |

### 删除

| 路径 | 原因 |
|------|------|
| `Client.Automation/FlaUi/FlaUiDriver.cs` | UIA3 dump 不到企微控件 |
| `Client.Automation/Vision/QwenVisionLocator.cs` | bbox 不稳定 |
| `Client.Automation/Vision/OcrVisionLocator.cs` | OCR 中文失败率高 |
| `Client.Automation/Vision/VisionCache.cs` | 仅服务 Qwen |
| `Client.Automation/Vision/IScreenCapturer.cs` | 仅服务 Vision |
| `Client.Automation/Vision/VisionApiExceptions.cs` | 仅服务 Qwen |
| `Client.Automation/WeCom/ConversationNavigator.cs` | 由 PS search_user 取代 |
| `Client.Automation/WeCom/SendMessageService.cs` | 由 PS send_* 取代 |
| `Client.Automation/WeCom/MessageWatcher.cs` | 由 F4 ChatArchiveListener 取代 |
| `Client.Automation/WeComAutomation.cs` | 旧实现 |
| `Client.Automation/Nodes/NodesConfig*.cs` | UIA3 节点常量已无意义 |
| `Client.VisionRegression/` 整个工程 | 已无用 |
| `Client.Tests/Vision/` 整个目录 | 视觉测试已废 |

---

## 附录 C：PS 编码坑速查（防再次踩）

| 坑 | 表现 | 解 |
|----|------|---|
| 中文字面量被 GBK 解析 | "企业微信" 变成 "录1 "<22><>" | 文件存 UTF-8 with BOM |
| PS 5.1 命令行参数中文乱码 | `-Keyword "陆伟"` 进去变问号 | 走 stdin JSON 传参 |
| `Invoke-RestMethod` 中文响应乱码 | API 返回的中文 content 变 "é´ä¼" | 用 HttpClient 拿 raw bytes + UTF-8 decode |
| 子进程 stderr 触发 NativeCommandError | 调 capture-wecom 子脚本时中断 | `cmd /c "... 2>&1"` 包一层 + `$ErrorActionPreference='SilentlyContinue'` |
| `[int]'0x4F01'` 转换失败 | PS 5.1 不认 0x 前缀 | 不需要——存为 BOM 后直接写中文字面量 |
| `AddAutomationEventHandler` 重载找不到 | UIA 事件被吞 | **直接放弃 PS 监听 UIA 事件**，改用 EnumWindows 轮询或会话存档 API |
