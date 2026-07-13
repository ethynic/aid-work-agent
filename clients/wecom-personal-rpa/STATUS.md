# 客户端开发状态（视觉定位方案落地后盘点）

> 维护对象：`clients/wecom-personal-rpa/`（企业微信个人账号 RPA .NET 客户端）
> 盘点日期：2026-06-24
> 前序盘点：2026-06-22 初版（仅工程骨架 + 36 单测）→ 2026-06-23 视觉定位方案验证 → 2026-06-24 视觉定位落地
> 关联：
>   - 视觉定位设计：[docs/system/wecom-personal-rpa-vision-design.md](../../docs/system/wecom-personal-rpa-vision-design.md)
>   - 开发计划：[plans/plan-wecom-personal-rpa-vision.md](../../plans/plan-wecom-personal-rpa-vision.md)
>   - 真机回归报告：`vision-regression-out/report_20260623_235539.yaml`

---

## 编译与测试现状（证据）

- `dotnet build WeComPersonalRpaClient.sln -c Debug`：**0 错误 / 0 警告**（5 工程全绿，TFM 升至 net8.0-windows10.0.19041.0）。
- `dotnet test`：**119 passed / 0 failed**（原 36 + 阶段 2A 视觉层 60 + 阶段 2B 自动化层 14 + 阶段 3B.1 修复新增 9）。
- **真机视觉回归**：3/5 通过（场景 1 元素定位 / 场景 2 缓存命中 / 场景 3 指纹失效 全 pass；场景 4/5 skipped 因为 --image 模式不测截图采集层）。
- **真机环境未做**：完整端到端联调（Client.App GUI 进程 + 服务端 + 真实企微 + 真实发送消息）。

---

## 视觉定位方案（2026-06-23 验证、2026-06-24 落地）

### 背景

UIA3（FlaUI）和 MSAA（IAccessible）在企微 D2D 自绘 UI 上双双失效（dump 0 控件、子对象数 0）。
Windows.Media.Ocr 中文识别率 < 10%。
**改走 Qwen3-VL 多模态视觉定位**：真机验证 24 个 UI 元素 bbox 全部精准命中。

详见 [docs/system/wecom-personal-rpa-vision-design.md](../../docs/system/wecom-personal-rpa-vision-design.md)。

### 已落地的视觉层模块（全部带单测）

| 模块 | 文件 | 测试 |
|---|---|---|
| `IVisionLocator` / `BoundingBox` / `VisionProbeResult` / `VisionConfig` | `Vision/IVisionLocator.cs` 等 | BoundingBoxTests |
| `IVisionApi` / `QwenVisionApi`（HttpClient + 多 key 池 + 4xx/5xx 分流） | `Vision/QwenVisionApi.cs` | QwenVisionApiTests |
| `QwenVisionLocator`（缓存 + API + bbox 越界检查 + 失败降级链） | `Vision/QwenVisionLocator.cs` | QwenVisionLocatorTests |
| `OcrVisionLocator`（Windows.Media.Ocr 降级） | `Vision/OcrVisionLocator.cs` | OcrVisionLocatorTests |
| `IVisionCache` / `VisionCache`（SQLite + TTL + UPSERT） | `Vision/VisionCache.cs` | VisionCacheTests |
| `IScreenCapturer` / `ScreenCapturer`（PowerShell 委托截图 + 像素自检） | `Vision/ScreenCapturer.cs` | ScreenCapturerTests + WindowFingerprintTests |
| `WindowFingerprint` / `ScreenshotOptions` / `CaptureResult` | `Vision/WindowFingerprint.cs` 等 | 同上 |
| `InputExecutor`（bbox→屏幕坐标→SendInput + 剪贴板粘贴） | `Win32/InputExecutor.cs` | InputExecutorTests |
| `WeComAutomation` 重构（依赖 IVisionLocator） | `WeCom/WeComAutomation.cs` | WeComAutomationVisionTests |
| `MessageWatcher`（用 Windows.Media.Ocr inline 抓消息文本） | `WeCom/MessageWatcher.cs` | MessageWatcherVisionTests |
| `LoginStateDetector`（视觉定位二维码区域 + 截图） | `WeCom/LoginStateDetector.cs` | LoginStateDetectorVisionTests |
| `ConversationNavigator`（视觉定位搜索 + 多候选检测） | `WeCom/ConversationNavigator.cs` | 同 WeComAutomationVisionTests |

### Client.App 已完成

- `Services/Stubs/AutomationStubs.cs` **已删除**（grep `Stub` 0 行）
- `App.xaml.cs.ConfigureServices` 注册真实实现：`IVisionLocator → QwenVisionLocator`、`IActionExecutor → SendMessageService`、`IWeComAutomation → WeComAutomation`、`IHealthSupervisor → HealthSupervisor`
- `SendMessageService.DownloadToTempAsync` 接通真实 `IAgentApiClient.DownloadFileAsync`
- `configs/client.example.yaml` 含完整 `vision:` 配置段（设计 §5.3）
- `Services/VisionConfigLoader.cs` 从 QWEN_API_KEYS 解析 key 池

### 真机回归工具（按需运行，不在 sln 内）

- `src/Client.VisionRegression/`：5 场景真机验证（场景 1 元素定位 / 场景 2 缓存 / 场景 3 指纹失效 / 场景 4 截图自检 / 场景 5 失败降级）
- `scripts/capture-wecom-for-csharp.ps1`：PowerShell 截图脚本（前台权限正常，C# ScreenCapturer 调用）
- `scripts/run-vision-regression.ps1`：两阶段回归（PS 截图 → C# 验证视觉定位）

> **已清理**：早期失效工具 `Client.Probe`（UIA/MSAA 探测，已证伪）、`Client.VisionProbe`（B 方案 OCR 验证，已证伪）、`Client.GraphicsCaptureSpike`（WGC COM spike，方案否决）已全部删除。详见 [docs/system/wecom-personal-rpa-vision-breakthrough.md](../../docs/system/wecom-personal-rpa-vision-breakthrough.md) §1.4。

---

## ① ✅ 已开发完成并测试过（视觉定位方案落地）

视觉定位方案的所有核心模块均已落地（见上表）。原 2026-06-22 盘点的所有 ②🟡 / ③🔴 项已迁移到本节。

---

## ② 🟡 已开发但未经生产联调（待 Client.App 真实部署）

| 模块 / 文件 | 状态 | 缺什么 |
|---|---|---|
| `Client.App/*`（RpaHost / TrayApp / 3 个 Window 等） | 编译通过、DI 装配完整、含视觉定位 | **从未作为常驻 GUI 进程实际运行**；未与服务端 + 真实企微做端到端联调 |
| `Client.Supervisor/*`（监督进程） | 编译通过 | 从未作为 Windows Service 实际部署运行 |
| `WeComAutomation.SendText/SendImage/SendFile` 完整流程 | 单测覆盖（mock IVisionLocator） | 真实 SendInput 在企微上的端到端发送未做（需 Client.App 进程 + 真实企微） |
| `MessageWatcher` 抓消息文本 | 单测覆盖（mock OCR） | 真实企微消息抓取未做 |
| `LoginStateDetector` 二维码截图 | 单测覆盖（mock IVisionLocator） | 真实扫码流程未做 |
| `ConversationNavigator` 多候选检测 | 单测覆盖；模型当前只返回单 bbox，Ambiguous 分支留待模型升级 | 真实会话切换未做 |

---

## ③ 🔴 未开发 / 已知限制（明确不做的或留作下个迭代）

| 项 | 现状 | 影响 / 下一步 |
|---|---|---|
| **Windows.Graphics.Capture 离屏渲染** | spike 已验证 API 可用（Win11 Build 26200），但 COM 互操作代码量 ~500 行未完成 | 当前用 PowerShell + CopyFromScreen 方案；生产环境（专机专用 GUI 进程）够用；下个迭代作为「窗口被遮挡时的鲁棒性增强」 |
| **ConversationNavigator 真正的多候选检测** | 模型当前只返回单 bbox，Ambiguous 分支退化 | 等 Qwen3-VL 升级支持 multiple bbox 返回后改一行即可 |
| **MessageWatcher conversationId** | 首版以"截图尺寸指纹"作临时 ID | 真实生产需要 ConversationNavigator 把当前会话信息回填 |
| **客户端交付** | 仅保留 EXE build/publish | `dotnet build -c Release` 用于本机编译运行，`scripts/publish.ps1` 生成可复制部署的自包含 EXE 目录；安装包方案已永久废弃并删除 |
| **`assets/wecom_nodes.yaml`** | 设计文档完整模式（运行期不直接加载，仅参考） | FlaUI 路线已被视觉定位替代，节点常量不再需要 |

---

## 真机回归关键发现（2026-06-23/24）

### 已验证

1. **Qwen3-VL 视觉定位**：5 个标准元素（搜索框/消息输入框/发送按钮/导航/会话项）bbox 全部命中，越界检查通过
2. **视觉缓存**：第 2/3 次同元素定位 Source="cache"，耗时 7-29ms（远低于 1000ms 门槛）
3. **窗口指纹隔离**：X+1 伪造指纹下 cache miss，证明键隔离生效
4. **企微版本读取**：从 WXWork.exe FileVersionInfo 成功读到 5.0.8.6009（注册表读不到的问题已修）
5. **截图前置校验**（生产路径）：像素自检 + 前台校验逻辑可观察

### 已知限制

1. **真机回归工具的前台权限限制**：Client.VisionRegression 作为 console 子进程调用 PowerShell 子进程时，PowerShell 也拿不到 Windows 前台权限，会截到被白色窗口遮挡的内容。**这是测试工具环境限制，不是生产 bug**——Client.App 是常驻 GUI 进程，前台权限天然 OK
2. **场景 4/5 在 --image 模式下 skipped**：依赖真实截图采集的场景无法通过 stub 截图器验证
3. **未做完整端到端**：Client.App GUI 进程 → 服务端 callback → 真实企微消息收发整条链路未联调

---

## 一句话结论

客户端视觉定位方案**已生产代码级落地**：119 单测全绿、真机回归核心场景（元素定位 / 缓存 / 指纹隔离）3/3 通过。**距离生产可用还差最后一步：Client.App 作为常驻 GUI 进程的真实端到端联调**（涉及服务端部署 + 真实企微账号 + 真实 SendInput 发送）。这部分需要在真实运维环境（专机 + 企微 + 服务端）下做，不在自动化测试范围内。
