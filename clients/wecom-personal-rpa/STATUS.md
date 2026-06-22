# 客户端开发状态（诚实三态盘点）

> 维护对象：`clients/wecom-personal-rpa/`（企业微信个人账号 RPA .NET 客户端）
> 盘点日期：2026-06-22
> 目的：提交前明确标记三态——**① 已开发完成并测试过 / ② 已开发但未测试 / ③ 未开发（占位·桩·TODO）**，不夸大完成度。

## 编译与测试现状（证据）

- `dotnet build WeComPersonalRpaClient.sln -c Debug`：**0 错误 / 0 警告**（5 工程全绿）。
- `dotnet test`：**36 passed / 0 failed**（StateManagerTests、HmacSignerTests、SqliteSendQueueTests、TokenBucketTests、ActionLocatorTests）。
- **真实环境联调：未做**——本会话无 Windows + 企业微信桌面环境，也未与服务端对打。所有 🟡/🔴 项要等真实环境或后续迭代补齐。

---

## ① ✅ 已开发完成并测试过（有单测且通过）

| 模块 / 文件 | 测试 | 说明 |
|---|---|---|
| `Client.Core/StateMachine/*`（StateManager / ClientSession / ClientState / IStateManager / InvalidStateTransitionException） | StateManagerTests | 状态迁移规则；仅 `Running` 允许出站；非法迁移抛异常 |
| `Client.Core/Security/HmacSigner.cs` | HmacSignerTests | 签名确定性（同输入同输出，与服务端 `auth.compute_signature` 字节对字节一致） |
| `Client.Core/Queue/SqliteSendQueue.cs`（+ ISendQueue / SendAction） | SqliteSendQueueTests | enqueue / claim / mark / 幂等 dedup_key（临时 sqlite） |
| `Client.Core/RateLimiting/TokenBucketRateLimiter.cs`（+ IRateLimiter） | TokenBucketTests | per_minute / per_day 滑动窗口、连续失败达阈值暂停 |
| `Client.Core/Protocol/*`（13 个 DTO + ActionJsonConverter + ProtocolJsonOptions） | 间接（ActionLocator / HmacSigner 测试覆盖序列化） | 镜像服务端 schemas.py，camelCase + type 判别 |
| `Client.Automation/WeCom/ActionLocator.cs` | ActionLocatorTests（mock 三层） | 验证 FlaUI→Win32 坐标→OpenCV 模板 的**降级调度路径** |

> 注：ActionLocator 测试用 mock 验证「降级逻辑」；底层 FlaUiDriver / Win32Input / TemplateMatcher 的真实 UI 行为**未**在测试中触发（见 ②③）。

---

## ② 🟡 已开发、编译通过，但未经测试 / 联调

| 模块 / 文件 | 状态 | 缺什么 |
|---|---|---|
| `Client.Core/AgentApi/AgentApiClient.cs`（HttpClient + Polly 重试熔断 + ClientWebSocket） | 实现完整、编译通过 | 无对真实服务端的联调测试（callback / WS / poll / files / config） |
| `Client.Core/Security/RequestSigner.cs` | 实现完整（HTTP 头注入） | 无独立单测（底层 HmacSigner 已测） |
| `Client.Core/Config/ClientOptions.cs` / `EncryptedClientConfig.cs`（DPAPI） | 实现完整 | 无单测 |
| `Client.Core/Observability/*`（LogConfig / SensitiveRedactor / IHealthReporter） | 实现完整 | 无单测 |
| `Client.Supervisor/*`（SupervisorService 周期监督拉起 / OfflineReporter / WindowsEventLogger / SupervisorOptions / Program） | 编译通过 | 从未作为 Windows Service 实际部署运行 |
| `Client.Automation/FlaUi/FlaUiDriver.cs` | UIA3 封装，API 调用真实 | **从未在真实企微窗口执行**（节点常量是占位，见 ③） |
| `Client.Automation/Win32/*`（NativeMethods P/Invoke / Win32Input / ClipboardGuard / ClipboardFileDrop / DesktopState） | P/Invoke 签名真实 | 从未在真实桌面执行 |
| `Client.Automation/Vision/TemplateMatcher.cs` | OpenCvSharp MatchTemplate 真实 | 无模板资源、从未真实匹配 |
| `Client.Automation/Nodes/*`（WeComNodesConfig / NodesConfigLoader） | YamlDotNet 加载器实现 | 加载的是占位 yaml（见 ③） |
| `Client.Automation/WeCom/HealthSupervisor.cs` | 桌面锁定 / 分辨率 / DPI 探测逻辑实现 | 未在真实环境验证 |
| `Client.Automation/WeCom/ConversationNavigator.cs` / `WeComMainWindow.cs` | 实现完整 | 未在真实企微验证（依赖占位节点常量） |
| `Client.App/*`（RpaHost / TrayApp / 3 个 Window / InboundReporter / OutboundActionSource / HealthSupervisor / LoginStateDetector / MessageWatcher / ClientOptionsLoader） | 编译通过、DI 装配完整 | **从未启动运行**；且当前依赖 ③ 中的桩，非真实自动化 |

---

## ③ 🔴 未开发 / 占位 / 桩（核心缺口，需真实环境或后续补齐）

| 项 | 现状 | 影响 / 下一步 |
|---|---|---|
| **`Client.App/Services/Stubs/AutomationStubs.cs`** | App 当前注入的 `IActionExecutor` / `IWeComAutomation` / `IHealthSupervisor` 全是桩：`StubActionExecutor.Execute` 直接 `return Success=true`，`StubWeComAutomation.SendText/SendImage/SendFile` 全 `return false`。文件头注明「Automation 具体类落地后删除本文件，并在 `App.xaml.cs.ConfigureServices` 改用真实实现」 | **客户端目前不会真正操作企业微信**。这是 App↔Automation 集成的核心缺口 |
| **`assets/wecom_nodes.yaml`（两份）** | 所有控件 AutomationId/Name、坐标 offset、模板路径、阈值均为**占位默认值**（如 `class_name=WeWorkWindow`、`offset=[400,580]`） | 自动化能否工作的**硬前提**；必须由计划第 0 节「编码前准入验证」在真实企微探测后回填 |
| **`Client.Automation/WeCom/MessageWatcher.cs`** | `Text=null`（"文本需点击会话后抓取，占位"）、`ConversationId=displayName`（"首版以显示名作临时会话 ID"）、`ConversationType` 默认 ExternalUser | 入站消息**只建会话路由骨架，未抓真实文本/附件** |
| **`Client.Automation/WeCom/LoginStateDetector.cs`** | 二维码区域截图"留给上层"未实现 | 扫码登录闭环未完成 |
| **`Client.App/Services/SendMessageService.cs`** `DownloadToTempAsync` | 占位写空文件（`File.WriteAllTextAsync(tmp, "", ct)`，注释 TODO：经 `IAgentApiClient.DownloadFileAsync` 真实下载） | 图片/文件发送的真实下载链路未接 |
| **`Client.App/Autostart/AutostartRegistrar.cs`** | 注册表 Run 键最小实现，未实测 | 开机自启未验证 |
| **`Client.Supervisor/ScheduledTaskHelper.cs`** | `schtasks` 占位 + TODO | 计划任务自启未实现/未测 |
| **`installer/wix/`** | 仅占位 README | WiX/MSIX 实际打包与代码签名**未实现**（属开发计划第 5 节，本会话明确 OUT） |

---

## 一句话结论

客户端是**可编译、含 36 个通过的纯逻辑单测的工程骨架**，但**未在真实 Windows+企业微信环境运行过**，且 `Client.App` 当前通过**桩**接入自动化、节点常量是**占位**、入站文本/附件抓取与二维码截图**未实现**。距离生产可用还需：① 计划第 0 节准入验证回填节点常量；② 删除 Stubs 接入真实 Automation；③ 补齐 MessageWatcher 文本抓取 / LoginStateDetector 二维码 / SendMessageService 下载；④ WiX 打包与签名；⑤ 真实环境 14 天连跑验收（计划第 7 节）。
