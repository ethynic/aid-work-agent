# 开发计划：企业微信个人账号 RPA —— 视觉定位客户端改造

> 关联设计：[docs/system/wecom-personal-rpa-vision-design.md](../docs/system/wecom-personal-rpa-vision-design.md)（权威设计，§3 模块设计 / §5 客户端改造计划 / §8 开发任务拆解）
> 关联主设计：[docs/system/wecom-personal-rpa-design.md](../docs/system/wecom-personal-rpa-design.md)（不在本计划范围内修改，后续收尾阶段由项目维护者统一更新）
> 关联协议：[docs/system/wecom-personal-rpa-protocol.md](../docs/system/wecom-personal-rpa-protocol.md)（**本计划禁止修改**，视觉定位是客户端内部细节）
> 关联客户端现状：[clients/wecom-personal-rpa/STATUS.md](../clients/wecom-personal-rpa/STATUS.md)
> 关联既有计划：[plans/plan-wecom-personal-rpa.md](./plan-wecom-personal-rpa.md)（原主计划，本计划是其视觉定位改造子项）
> 登记：[docs/ideas.md](../docs/ideas.md) #29 企业微信个人账号 RPA 接入（视觉定位补充）
>
> 创建日期：2026-06-23
> 状态：📋 待开发（计划已就绪，待阶段 2A 并行启动）

---

## 1. 背景与目标

### 1.1 背景

企业微信 PC 客户端是 DirectUI / Direct2D 自绘 UI，UIA3（FlaUI）和 MSAA 双双对其失效（dump 0 控件）。原主设计的三层降级链 FlaUI → Win32 坐标 → OpenCV 模板的前两层已彻底失效，第三层是「坐标方案的变体」也被项目方向否决。

经过真机验证（2026-06-23），**Qwen3-VL 多模态视觉定位方案可行**：24 个 UI 元素 bbox 全部精准命中（详见设计文档 §2.3）。

### 1.2 目标

把视觉定位方案落地到 `clients/wecom-personal-rpa/` 的 .NET 8 客户端，替换失效的 FlaUI 主路径，让客户端真正能定位并点击企微 PC 客户端的 UI 元素。

### 1.3 成功标准（验收门槛）

| 维度 | 验收标准 |
|------|---------|
| 编译 | `dotnet build WeComPersonalRpaClient.sln -c Debug` **0 错误 / 0 警告**（5 工程全绿） |
| 单测 | `dotnet test` 全部通过（现有 36 个 + 新增视觉层单测 ≥ 10 个） |
| 接口落地 | `IActionExecutor / IWeComAutomation / IHealthSupervisor` 全部由真实实现注入，**Stubs 全部删除** |
| 真机视觉回归 | 对企微真实窗口截图，bbox 准确率 ≥ 验证脚本（`probe-qwen-vl-full.py`）水平，即 24 个标准元素 bbox 全部命中 |
| 协议不变 | 服务端代码 0 改动、协议文档 0 改动 |
| 配置可切换 | 关闭 `vision.enabled` 时降级链仍能编译并启动（OCR / 坐标兜底） |

### 1.4 不在范围内（明确禁止触碰）

- 服务端代码：`src/saas/api/wecom_personal_rpa_*.py`、`src/channels/wecom_personal_rpa/`
- 协议文档：`docs/system/wecom-personal-rpa-protocol.md`
- 主设计文档：`docs/system/wecom-personal-rpa-design.md`（收尾阶段由项目维护者统一更新）
- WiX 打包 / 代码签名（属原计划 §5，与本计划正交）
- 自训练 YOLO / 本地多模态模型 / Hook 注入 / 协议逆向

---

## 2. 前置条件

| 项 | 状态 | 说明 |
|---|------|------|
| 设计文档 | ✅ 就绪 | `docs/system/wecom-personal-rpa-vision-design.md` 已完整（11 章节） |
| Qwen3-VL 真机验证证据 | ✅ 就绪 | `clients/wecom-personal-rpa/src/vision-probe-out/qwen3vl_eval_*/`，24 元素 bbox 全部命中 |
| QwenVisionLocator Python 原型 | ✅ 就绪 | `clients/wecom-personal-rpa/scripts/probe-qwen-vl-full.py`（API 调用 + JSON 解析 + bbox 画框，C# 实现的 1:1 参照） |
| ScreenCapturer PowerShell 原型 | ✅ 就绪 | `clients/wecom-personal-rpa/scripts/auto-capture-wecom.ps1`（Win32 P/Invoke + 像素自检，C# 实现的 1:1 参照） |
| SQLite 持久化参考 | ✅ 就绪 | `clients/wecom-personal-rpa/src/Client.Core/Queue/SqliteSendQueue.cs`（Dapper + Microsoft.Data.Sqlite，VisionCache 直接照搬此模式） |
| .NET 8 SDK | ⬜ 子智能体自检 | `dotnet --version` 必须 ≥ 8.0.x；Windows 平台必须 x64 |
| `QWEN_API_KEYS` 环境变量 | ⬜ 真机阶段需要 | 仅阶段 3B（真机视觉回归）需要；阶段 2/3A 编译与单测阶段不需要真实 API 调用 |
| 真实 Windows 11 + 企业微信 PC 环境 | ⬜ 仅阶段 3B 需要 | 阶段 2 的所有产出必须在 Windows 上可编译，但单测不能依赖真实企微运行 |

---

## 3. 任务清单

> 编号约定：**A\* 为并行组（无相互依赖），B\* 串行依赖 A，C\* 串行依赖 B**。每个任务标注「依赖 / 文件 / 关键 API / 验收 / 工时」。

### 阶段 2A：基础组件并行开发（3 个子智能体同时跑）

---

#### 任务 A1 —— ScreenCapturer + WindowFingerprint（截图采集 + 窗口指纹）

**职责**：实现截图采集（含「企微必须在前台」前置校验 + 像素自检）和窗口指纹（缓存键的输入）。

**依赖**：无。

**文件清单**（全部新建）：

| 路径 | 类型 | 说明 |
|------|------|------|
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/ScreenCapturer.cs` | 新建 | 截图采集主类，含 Win32 P/Invoke + 像素自检 |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/WindowFingerprint.cs` | 新建 | 窗口指纹 record |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/CaptureExceptions.cs` | 新建 | `WindowNotForegroundException` / `SuspiciousScreenshotException` |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/ScreenCapturerTests.cs` | 新建 | 像素自检算法纯逻辑单测（不依赖真实截图） |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/WindowFingerprintTests.cs` | 新建 | fingerprint 匹配/失配单测 |

**关键 API 签名**：

```csharp
namespace WeCom.PersonalRpa.Automation.Vision;

// 1) 截图采集
public sealed class ScreenCapturer
{
    /// <param name="windowClassName">企微主窗口类名，默认 "WeWorkWindow"。</param>
    /// <param name="expectedTitleKeyword">窗口标题必须包含的关键字（防类名误命中），默认 "企业微信"。</param>
    /// <param name="options">截图选项（来自 VisionConfig.Screenshot 段，可为 null 用默认）。</param>
    public ScreenCapturer(string windowClassName = "WeWorkWindow",
                          string expectedTitleKeyword = "企业微信",
                          ScreenshotOptions? options = null);

    /// <summary>采集一张企微主窗口截图，前置校验窗口在前台 + 后置像素自检。</summary>
    /// <returns>截图 Bitmap（RGB，调用方负责 Dispose）+ 窗口矩形 + 窗口指纹。</returns>
    /// <exception cref="WindowNotForegroundException">企微未在前台，重试仍失败。</exception>
    /// <exception cref="SuspiciousScreenshotException">像素自检判定截图疑似非企微（白色占比 > 阈值 或 颜色多样性 < 阈值）。</exception>
    public Task<CaptureResult> CaptureWeComMainWindowAsync(CancellationToken cancellationToken = default);
}

public sealed class CaptureResult : IDisposable
{
    public System.Drawing.Bitmap Bitmap { get; init; }       // RGB，调用方 Dispose
    public WindowFingerprint Fingerprint { get; init; }      // 窗口指纹（缓存键输入）
    public (int Left, int Top, int Width, int Height) WindowRect { get; init; }
    public void Dispose() => Bitmap?.Dispose();
}

/// <summary>截图自检选项（对应 vision.screenshot 配置段）。</summary>
public sealed class ScreenshotOptions
{
    public bool PreForegroundCheck { get; set; } = true;
    public bool PixelSanityCheck { get; set; } = true;
    public double MaxWhiteRatio { get; set; } = 0.5;
    public int MinColorDiversity { get; set; } = 30;
}

// 2) 窗口指纹（缓存键）
public sealed record WindowFingerprint(
    string WindowClass,       // "WeWorkWindow"
    int X, int Y,             // WindowRect 左上
    int Width, int Height,    // WindowRect 尺寸
    double DpiScale,          // 系统缩放（来自 Graphics.DpiX / 96）
    string WeComVersion)      // 企微版本（从注册表 HKLM\Software\Tencent\WeWork 读 ProductVersion；读失败填 "unknown"）
{
    /// <summary>逐字段精确匹配（任意字段变化 → 视为窗口变化 → 失效缓存）。</summary>
    public bool Matches(WindowFingerprint other);
}

// 3) 像素自检（独立可测函数，便于单测）
public static class PixelSanityChecker
{
    /// <summary>量化到 16x16 色块后统计：白色占比 / 颜色多样性。</summary>
    /// <returns>(白色占比 0..1, 颜色多样性)。</returns>
    public static (double WhiteRatio, int ColorDiversity) Analyze(System.Drawing.Bitmap bmp);
}
```

**实现要点（必须照搬原型）**：

- Win32 P/Invoke 参照 `scripts/auto-capture-wecom.ps1`：`EnumWindows / GetClassName / GetWindowText / IsWindowVisible / GetWindowRect / SetForegroundWindow / ShowWindow / IsIconic / GetForegroundWindow / keybd_event`（ALT-key trick 解除前台锁）。**注意：C# 工程已有 `Win32/NativeMethods.cs`，优先复用其中的 P/Invoke 定义，缺失的才在本文件内 internal 补充**。
- 窗口标题校验用 Unicode 码点构造（`new string(new char[]{0x4F01, 0x4E1A, 0x5FAE, 0x4FE1})`），**禁止在源码里写"企业微信"字面量**（避免 UTF-8 BOM / 编码坑）。
- 像素自检：`step = max(1, w/100)`，量化 RGB 到 `/16*16`，统计唯一 key 数。阈值 `whitePct > 0.5` 抛 `SuspiciousScreenshotException`，`colorDiversity < 30` 同抛。
- 自检失败时 Bitmap 必须先 Dispose 再抛异常（防 GDI 句柄泄漏）。
- DPI 采集：`using var g = Graphics.FromHwnd(hwnd); double dpi = g.DpiX / 96.0;`。
- 企微版本采集：`Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Tencent\WeWork")?.GetValue("Version")`；读不到填 `"unknown"`。

**验收标准**：

- [ ] 编译 0 错误 0 警告（`dotnet build src/Client.Automation/Client.Automation.csproj`）
- [ ] `WindowFingerprintTests` 覆盖：字段全等 → true；任一字段变 → false（至少 6 个 case：class/x/y/w/h/dpi/version）
- [ ] `PixelSanityCheckerTests` 覆盖：纯白图 → WhiteRatio=1.0；3x3 单色图 → ColorDiversity=1；构造一张「灰阶 16x16」图 → ColorDiversity ≥ 16
- [ ] `ScreenCapturer` 类签名与上面 API 签名**逐字一致**（任务 B 依赖）

**预估工时**：1.5 天。

---

#### 任务 A2 —— VisionCache（SQLite 持久化缓存层）

**职责**：实现视觉缓存（缓存键 = `(window_fingerprint, element_type, label_keyword)`，TTL 24h，SQLite 存储）。

**依赖**：无（**不依赖 A1**：A1 的 `WindowFingerprint` 由 A2 自行定义 record；若 A1 已完成则直接 using A1 的命名空间）。**协作约束**：A2 与 A1 必须在编码前对齐 `WindowFingerprint` 的命名空间和字段名（见下文「并行协同约束」）。

**文件清单**（全部新建）：

| 路径 | 类型 | 说明 |
|------|------|------|
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/VisionCache.cs` | 新建 | SQLite 持久化缓存 |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/CachedBbox.cs` | 新建 | 缓存值 DTO |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/VisionCacheTests.cs` | 新建 | CRUD + TTL + 失效单测（临时 sqlite 文件） |

**关键 API 签名**：

```csharp
namespace WeCom.PersonalRpa.Automation.Vision;

public sealed class VisionCache : IDisposable
{
    /// <param name="dbPath">SQLite 文件绝对路径，目录自动创建。</param>
    /// <param name="defaultTtl">默认 TTL，传入 TimeSpan.FromHours(24)。</param>
    public VisionCache(string dbPath, TimeSpan defaultTtl);

    /// <summary>查缓存。命中且未过期返回 bbox；否则返回 null。</summary>
    public Task<CachedBbox?> TryGetAsync(WindowFingerprint fp, string elementType, string labelKeyword,
                                         CancellationToken cancellationToken = default);

    /// <summary>写缓存（覆盖同 key）。bbox 必须通过 bbox 越界检查（调用方负责）。</summary>
    public Task SetAsync(WindowFingerprint fp, string elementType, string labelKeyword, BoundingBox bbox,
                         CancellationToken cancellationToken = default);

    /// <summary>失效特定窗口下所有缓存（窗口指纹变化时调用）。</summary>
    public Task InvalidateWindowAsync(WindowFingerprint fp, CancellationToken cancellationToken = default);

    /// <summary>清空所有缓存（手动「刷新定位」按钮调用）。</summary>
    public Task ClearAsync(CancellationToken cancellationToken = default);

    public void Dispose();
}

/// <summary>缓存值 DTO。</summary>
public sealed class CachedBbox
{
    public BoundingBox Bbox { get; init; }            // 见 A3 的 BoundingBox
    public DateTimeOffset CachedAt { get; init; }     // 写入时间（UTC）
    public DateTimeOffset ExpiresAt { get; init; }    // 过期时间 = CachedAt + TTL
    public string Source { get; init; } = "cache";    // 始终 "cache"，便于上层日志区分
}
```

**实现要点**：

- **直接照搬 `SqliteSendQueue.cs` 的模式**：`Dapper + Microsoft.Data.Sqlite`，`EnsureTableCreated` 用 `CREATE TABLE IF NOT EXISTS`。
- 表结构建议：
  ```sql
  CREATE TABLE IF NOT EXISTS vision_bbox_cache (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      window_class TEXT NOT NULL,
      window_rect TEXT NOT NULL,           -- "{x},{y},{w},{h}"
      dpi_scale REAL NOT NULL,
      wecom_version TEXT NOT NULL,
      element_type TEXT NOT NULL,
      label_keyword TEXT NOT NULL,
      bbox_x1 INTEGER NOT NULL,
      bbox_y1 INTEGER NOT NULL,
      bbox_x2 INTEGER NOT NULL,
      bbox_y2 INTEGER NOT NULL,
      cached_at TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      UNIQUE(window_class, window_rect, dpi_scale, wecom_version, element_type, label_keyword)
  );
  CREATE INDEX IF NOT EXISTS idx_vbc_lookup ON vision_bbox_cache(window_class, window_rect, element_type, label_keyword);
  CREATE INDEX IF NOT EXISTS idx_vbc_expires ON vision_bbox_cache(expires_at);
  ```
- `TryGetAsync`：`SELECT WHERE 键全等 AND expires_at > now`。
- `SetAsync`：`INSERT ... ON CONFLICT(键) DO UPDATE SET bbox/cached_at/expires_at`。
- `InvalidateWindowAsync`：`DELETE WHERE window_class/dpi_scale/version/rect 全等`。
- TTL 检查在 `TryGetAsync` 里做（不做后台清理）。
- 所有 SQL 用 `CommandDefinition(sql, new {...}, tx, cancellationToken: ct)` 异步执行，参照 `SqliteSendQueue` 风格。

**验收标准**：

- [ ] 编译 0 错误 0 警告
- [ ] `VisionCacheTests` 覆盖（用临时文件 `Path.GetTempFileName() + ".db"`）：
  - 写入后立即读取 → 命中
  - 过期（构造 `CachedAt = now - 25h`）→ 未命中
  - 任一 fingerprint 字段变化 → 未命中
  - `InvalidateWindowAsync` 后整窗口失效
  - `ClearAsync` 后全空
- [ ] API 签名与上面**逐字一致**（任务 B 依赖）

**预估工时**：1 天。

---

#### 任务 A3 —— QwenVisionLocator + OcrVisionLocator + DTO + IVisionLocator

**职责**：定义视觉定位抽象接口 + 实现 Qwen3-VL 主策略 + OCR 降级策略 + 所有 DTO。

**依赖**：无（**不依赖 A1/A2**：本任务定义接口和 DTO，是 A1/A2 的"消费者契约"；A1/A2 在落地后由本任务的 `using` 引入）。**协作约束**：本任务的 `BoundingBox / VisionProbeResult / IVisionLocator` 必须先确定（见下文「并行协同约束」）。

**文件清单**（全部新建）：

| 路径 | 类型 | 说明 |
|------|------|------|
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/IVisionLocator.cs` | 新建 | 视觉定位主抽象 |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/BoundingBox.cs` | 新建 | bbox DTO |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/VisionProbeResult.cs` | 新建 | 定位结果 DTO |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/VisionConfig.cs` | 新建 | 视觉配置 POCO（对应 yaml vision 段） |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/QwenVisionApi.cs` | 新建 | Qwen3-VL API 客户端（HttpClient，OpenAI 兼容协议） |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/QwenVisionLocator.cs` | 新建 | Qwen3-VL 主策略实现 |
| `clients/wecom-personal-rpa/src/Client.Automation/Vision/OcrVisionLocator.cs` | 新建 | Windows.Media.Ocr 降级策略实现 |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/BoundingBoxTests.cs` | 新建 | bbox 越界检查 / 中心点 / Contains 单测 |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/QwenVisionApiTests.cs` | 新建 | JSON 解析 / bbox 抽取 / 错误响应处理单测（mock HttpMessageHandler） |
| `clients/wecom-personal-rpa/src/Client.Tests/Vision/QwenVisionLocatorTests.cs` | 新建 | 缓存命中 / 缓存未命中触发 API / 越界 bbox 失效缓存 单测（mock IVisionApi） |

**关键 API 签名**：

```csharp
namespace WeCom.PersonalRpa.Automation.Vision;

// 1) bbox DTO
public sealed record BoundingBox(int X1, int Y1, int X2, int Y2)
{
    public int Width => X2 - X1;
    public int Height => Y2 - Y1;
    public (int Cx, int Cy) Center => ((X1 + X2) / 2, (Y1 + Y2) / 2);

    /// <summary>相对图像尺寸的越界检查（用于模型返回 bbox 校验）。</summary>
    public bool IsWithin(int imageWidth, int imageHeight, int tolerance = 0);

    /// <summary>与另一个 bbox 的 IoU（缓存命中后做"窗口未变"二次校验用）。</summary>
    public double IoU(BoundingBox other);
}

// 2) 定位结果
public sealed class VisionProbeResult
{
    public BoundingBox Bbox { get; init; }
    public double Confidence { get; init; }          // 0..1，缓存命中时填 1.0
    public string Source { get; init; } = "api";     // "api" / "cache" / "ocr"
    public string ElementType { get; init; } = "";
    public string LabelKeyword { get; init; } = "";
    public string? ModelUsed { get; init; }          // "qwen3-vl-plus" 等；cache 命中时复制原值
}

// 3) 视觉配置（对应 yaml vision 段）
public sealed class VisionConfig
{
    public bool Enabled { get; set; } = true;
    public string PrimaryModel { get; set; } = "qwen3-vl-plus";
    public string FallbackModel { get; set; } = "qwen3-vl-max";
    public string ApiEndpoint { get; set; } = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions";
    public string[] ApiKeys { get; set; } = Array.Empty<string>();
    public int TimeoutSeconds { get; set; } = 120;
    public int MaxRetries { get; set; } = 3;
    public VisionCacheConfig Cache { get; set; } = new();
    public ScreenshotOptions Screenshot { get; set; } = new();
    public string SQLitePath { get; set; } = "";

    public sealed class VisionCacheConfig
    {
        public bool Enabled { get; set; } = true;
        public int TtlHours { get; set; } = 24;
    }
}

// 4) 视觉定位主抽象
public interface IVisionLocator
{
    /// <summary>在当前企微主窗口截图上定位 UI 元素。内部走视觉缓存。</summary>
    /// <param name="elementType">button | input | list_item | icon | text</param>
    /// <param name="labelKeyword">元素标签关键字（如 "发送" / "搜索" / "文件传输助手"）。</param>
    Task<VisionProbeResult> LocateAsync(string elementType, string labelKeyword,
                                        CancellationToken cancellationToken = default);

    /// <summary>强制失效缓存（窗口位置/尺寸变化时调用）。</summary>
    Task InvalidateCacheAsync();
}

// 5) Qwen3-VL API 客户端（OpenAI 兼容协议，便于切 GPT/Claude）
public interface IVisionApi
{
    /// <summary>调用 Qwen3-VL 返回模型原始 JSON 内容（已剥 markdown fence）。</summary>
    /// <param name="model">qwen3-vl-plus / qwen3-vl-max</param>
    /// <param name="imageJpegBase64">RGB JPEG base64（不含 data: 前缀）。</param>
    /// <param name="prompt">定位 / OCR prompt。</param>
    /// <param name="temperature">默认 0.1。</param>
    /// <param name="maxTokens">默认 4096。</param>
    Task<VisionApiResponse> CallAsync(string model, string imageJpegBase64, string prompt,
                                      double temperature = 0.1, int maxTokens = 4096,
                                      CancellationToken cancellationToken = default);
}

public sealed class VisionApiResponse
{
    public string Content { get; init; } = "";       // 模型返回的原始文本（已剥 fence）
    public int TotalTokens { get; init; }
    public double ElapsedSeconds { get; init; }
    public string Model { get; init; } = "";
}

// 6) 主策略实现
public sealed class QwenVisionLocator : IVisionLocator, IDisposable
{
    public QwenVisionLocator(
        IVisionApi visionApi,              // QwenVisionApi 实例（便于单测 mock）
        ScreenCapturer capturer,           // A1 产出
        VisionCache cache,                 // A2 产出
        VisionConfig config);

    public async Task<VisionProbeResult> LocateAsync(string elementType, string labelKeyword,
                                                     CancellationToken cancellationToken = default);

    public Task InvalidateCacheAsync();
    public void Dispose();
}

// 7) 降级策略实现（Windows.Media.Ocr）
public sealed class OcrVisionLocator : IVisionLocator, IDisposable
{
    public OcrVisionLocator(ScreenCapturer capturer, VisionConfig config);

    /// <summary>OCR 降级仅能定位 text 类型（按关键字匹配最近一行文字的中心点）。</summary>
    public Task<VisionProbeResult> LocateAsync(string elementType, string labelKeyword,
                                               CancellationToken cancellationToken = default);

    public Task InvalidateCacheAsync() => Task.CompletedTask;  // OCR 无缓存
    public void Dispose();
}
```

**Qwen3-VL Prompt（C# 必须逐字复刻 Python 原型 `probe-qwen-vl-full.py` task2_grounding 的 prompt）**：

```
Look at this screenshot carefully. Image dimensions are {W}x{H} pixels.

Find these specific UI elements and return ONLY a JSON array with their bbox
(pixel coordinates relative to top-left of the image):

1. Search box at top (the input box where you type to search contacts/messages)
2. Each conversation item in the middle column (identify the conversation name)
3. The bottom message input box in the right panel
4. The send button (it might be an icon or text label)
5. The left navigation icons (message/mail/document/contacts etc.)

Format (output strictly this JSON, no markdown, no extra text):
[{"label": "<description>", "bbox": [x1, y1, x2, y2], "type": "button|input|list_item|icon|text"}]

bbox must be pixel coordinates. If you cannot find an element, omit it.
Do not make up elements.
```

**实现要点**：

- **`QwenVisionLocator.LocateAsync` 流程**（设计文档 §3.3）：
  1. 调 `ScreenCapturer.CaptureWeComMainWindowAsync` 得到 Bitmap + fingerprint + windowRect
  2. 查 `VisionCache.TryGetAsync`，命中直接返回 `Source="cache"`
  3. 未命中 → 把 Bitmap 转 JPEG base64（RGB 转换 + quality=92，**严格照搬 `probe-qwen-vl-full.py` 的 `encode_jpeg_b64`**）→ 调 `IVisionApi.CallAsync(PrimaryModel, ...)`
  4. 解析返回 JSON：剥 ```` ```json ``` ```` fence（**复刻 `parse_bbox_json` 的正则**），抽取元素数组
  5. 按 `(elementType, labelKeyword)` 匹配目标元素；匹配规则：`type` 精确等 + `label` 包含 `labelKeyword`（不区分大小写）
  6. **bbox 越界检查**：`bbox.IsWithin(windowRect.Width, windowRect.Height, tolerance: 5)`，越界抛异常 + 失效该元素缓存
  7. 写 `VisionCache.SetAsync`
  8. 返回 `Source="api"`
  9. 失败处理（设计 §4.4）：HTTP 4xx 抛 `VisionApiAuthException`（上层暂停账号）；5xx/超时按 `MaxRetries` 指数退避，仍失败切 `FallbackModel`；返回空数组/非法 JSON → 重试 1 次 → 仍失败返回 null（由调用方降级到 OCR）
- **API Key 池**：从 `VisionConfig.ApiKeys` 数组取，轮询 + 错误时跳到下一个；**禁止读环境变量**（必须从配置传入）
- **JSON 解析鲁棒性**：模型返回的 JSON 可能不规整，必须用 `JsonDocument.ParseAsync` 容错；抽出 `bbox` 字段可能叫 `bbox/box/rect`，都接受
- **`OcrVisionLocator` 用 Windows.Media.Ocr**：`TargetFramework` 必须在 `Client.Automation.csproj` 升级到 `net8.0-windows10.0.19041.0`（见「csproj 改动说明」）
- OCR 定位策略：把截图跑 OCR → 找到包含 `labelKeyword` 的行 → 返回该行边界框中心点；找不到 → 返回 null
- **HttpClient 走 `IHttpClientFactory`**：`QwenVisionApi` 构造函数注入 `HttpClient`，由 `Client.App` 的 DI 注册时配置 `AddHttpClient<QwenVisionApi>`

**csproj 改动说明**（**已在阶段 1.5 spike 中完成，A3 子智能体不需要再改**）：

阶段 1.5 spike（2026-06-23）已验证：3 个工程必须一起升 TFM，否则报 NU1201。

已完成的改动（在 master 上保留）：
- `clients/wecom-personal-rpa/src/Client.Automation/Client.Automation.csproj`：`<TargetFramework>net8.0-windows10.0.19041.0</TargetFramework>` + `<SupportedOSPlatformVersion>10.0.17763.0</SupportedOSPlatformVersion>` + `<UseWinUI>false</UseWinUI>`
- `clients/wecom-personal-rpa/src/Client.Tests/Client.Tests.csproj`：同上
- `clients/wecom-personal-rpa/src/Client.App/Client.App.csproj`：同上

验证结果：`dotnet build WeComPersonalRpaClient.sln -c Debug` → **0 错误 0 警告**（5 工程全绿）。

**验收标准**：

- [ ] 编译 0 错误 0 警告（`dotnet build WeComPersonalRpaClient.sln`）
- [ ] `BoundingBoxTests`：越界检查 4 case（左/上/右/下越界）+ IoU 重叠/不重叠/包含 3 case + 中心点 1 case
- [ ] `QwenVisionApiTests`：mock `HttpMessageHandler` 返回标准 OpenAI 响应 → 解析出 content / total_tokens / elapsed；返回 401 → 抛 `VisionApiAuthException`；返回 500 → 抛可重试异常
- [ ] `QwenVisionLocatorTests`：mock `IVisionApi` + 真实 `VisionCache`（临时 sqlite）+ mock `ScreenCapturer`（构造一个固定 Bitmap + fingerprint）
  - 缓存命中 → 不调 API
  - 缓存未命中 → 调 API → 写缓存 → 第二次命中
  - API 返回的 bbox 越界 → 抛异常 + 该元素缓存被失效
  - API 返回空数组 → 返回 null
- [ ] `OcrVisionLocator` 能在单测里被构造（用一张固定 PNG 跑 OCR，验证不抛异常）

**预估工时**：2 天。

---

### 并行协同约束（A1 / A2 / A3 必须在编码前对齐）

3 个子智能体并行启动前，**必须先在共享约定文件**（仅 A1 先创建，A2/A3 直接 using）**中固化以下符号**：

| 符号 | 定义归属 | 文件 |
|------|---------|------|
| `WeCom.PersonalRpa.Automation.Vision.WindowFingerprint` | A1 | `Vision/WindowFingerprint.cs` |
| `WeCom.PersonalRpa.Automation.Vision.BoundingBox` | A3 | `Vision/BoundingBox.cs` |
| `WeCom.PersonalRpa.Automation.Vision.VisionProbeResult` | A3 | `Vision/VisionProbeResult.cs` |
| `WeCom.PersonalRpa.Automation.Vision.ScreenshotOptions` | A1 | `Vision/ScreenCapturer.cs` |
| `WeCom.PersonalRpa.Automation.Vision.VisionConfig` | A3 | `Vision/VisionConfig.cs` |

**冲突预防**：A2 的 `VisionCache.TryGetAsync` 返回 `CachedBbox`，其中 `Bbox` 字段引用 A3 的 `BoundingBox`。**A2 编码时若 A3 未落地**，A2 可以在自己工程内放一个 `BoundingBox` 的 forward declaration（同字段同 record 签名），等 A3 完成后由任务 B 统一去重（**不允许最终存在两个 `BoundingBox`**）。

A1 / A2 / A3 全部完成后，**任务 B 开始前**，集成者必须确认：
- `WindowFingerprint` / `BoundingBox` / `VisionProbeResult` / `VisionConfig` 各只有 1 个定义
- `VisionCache.TryGetAsync` 的 `BoundingBox` 引用解析到 A3 的定义
- `QwenVisionLocator` 构造函数的 `ScreenCapturer / VisionCache` 引用解析到 A1 / A2 的定义

---

### 阶段 2B：WeCom 自动化层重构（1 个子智能体，串行）

---

#### 任务 B —— WeComAutomation 重构 + MessageWatcher/LoginStateDetector 补全

**职责**：把现有 `WeComAutomation` / `SendMessageService` / `MessageWatcher` / `LoginStateDetector` 从 FlaUI 主路径切换到 `IVisionLocator`。

**依赖**：A1 + A2 + A3 全部完成（编译通过、单测全绿）。

**文件清单**（全部修改现有文件）：

| 路径 | 类型 | 说明 |
|------|------|------|
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/WeComAutomation.cs` | 修改 | 重构为依赖 `IVisionLocator`，会话导航改用 Qwen3-VL 定位会话项 |
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/SendMessageService.cs` | 修改 | `ActionLocator.LocateMessageInput()` 调用替换为 `IVisionLocator.LocateAsync("input", "消息输入框")` + 点击 bbox 中心 |
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/MessageWatcher.cs` | 修改 | 补全 `Text=null`：用 `OcrVisionLocator`（OCR 比 grounding 便宜）或 `QwenVisionLocator.LocateAsync` + OCR 任务抓取消息内容 |
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/LoginStateDetector.cs` | 修改 | 补全二维码区域截图：用 `QwenVisionLocator.LocateAsync("icon", "二维码")` 定位二维码 bbox 后截图 |
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/ConversationNavigator.cs` | 修改 | `Navigate` 改为：调 `IVisionLocator.LocateAsync("input", "搜索")` → 点击 → 输入 keyword → 调 `LocateAsync("list_item", keyword)` → 点击候选会话 |
| `clients/wecom-personal-rpa/src/Client.Automation/WeCom/ActionLocator.cs` | 修改 | **降级为兜底**：保留 FlaUI/Win32/OpenCV 三层降级作为 Layer 3；主入口由 `IVisionLocator` 接管 |
| `clients/wecom-personal-rpa/src/Client.Automation/Win32/InputExecutor.cs` | 新建 | 抽出 `ClickElementAsync(BoundingBox bbox, IntPtr windowHandle)` / `TypeTextAsync(string text)`，供 WeComAutomation 调用（设计 §3.5） |
| `clients/wecom-personal-rpa/src/Client.Tests/Automation/WeComAutomationVisionTests.cs` | 新建 | mock IVisionLocator，验证 WeComAutomation.SendText 调用顺序（定位搜索框→输入→定位会话→点击→定位输入框→输入→定位发送按钮→点击） |
| `clients/wecom-personal-rpa/src/Client.Tests/Automation/MessageWatcherVisionTests.cs` | 新建 | mock IVisionLocator，验证 MessageWatcher 能抓到非空 Text |
| `clients/wecom-personal-rpa/src/Client.Tests/Automation/LoginStateDetectorVisionTests.cs` | 新建 | mock IVisionLocator，验证二维码定位 + 截图字节非空 |

**关键 API 签名**：

```csharp
// WeComAutomation 重构后（构造函数变化）
public sealed class WeComAutomation : IWeComAutomation
{
    public WeComAutomation(
        IVisionLocator visionLocator,        // A3 产出
        InputExecutor inputExecutor,         // 本任务新建
        ClipboardGuard clipboardGuard,
        INodesConfig? nodes = null);         // 仅作为兜底（FlaUI/OpenCV）的配置

    // 方法签名不变（IWeComAutomation 接口未改）
    public bool IsAttached { get; }
    public bool AttachMainWindow();
    public ConversationNavigateResult NavigateToConversation(string keyword);
    public bool SendText(string text);
    public bool SendImage(string localImagePath);
    public bool SendFile(string localFilePath);
    public void Dispose();
}

// InputExecutor（设计 §3.5）
public sealed class InputExecutor
{
    /// <summary>把 bbox 中心点换算成屏幕绝对坐标后点击。</summary>
    public void ClickElement(BoundingBox bbox, (int Left, int Top) windowOrigin);

    /// <summary>剪贴板粘贴文本（ClipboardGuard 已备份/清空）+ Enter。</summary>
    public void TypeText(string text);
}
```

**实现要点**：

- **WeComAutomation.SendText 流程**（重构后）：
  1. `visionLocator.LocateAsync("input", "搜索")` → 点击搜索框 → 输入 conversationKey
  2. `Thread.Sleep(400)` → `visionLocator.LocateAsync("list_item", conversationKey)` → 点击候选
  3. `visionLocator.LocateAsync("input", "消息输入框")` → 点击输入框
  4. `clipboardGuard.BackupAndEmpty()` → `inputExecutor.TypeText(text)` → `clipboardGuard.Restore()`
  5. `visionLocator.LocateAsync("button", "发送")` → 点击发送按钮
  6. 每一步失败都返回 false（不抛异常给上层），上层（SendMessageService）转为 `ActionExecResult.Success=false`
- **降级链**：`IVisionLocator.LocateAsync` 返回 null（API 失败 + OCR 失败）时，回退到现有 `ActionLocator`（保留 FlaUI/OpenCV 兜底）
- **MessageWatcher 补全**：去掉依赖 `FlaUiDriver` 的路径，改为：
  1. `visionLocator.InvalidateCacheAsync()`（确保窗口最新）
  2. `visionLocator.LocateAsync("list_item", "*")` 拿全部会话项 bbox
  3. 对每个未读会话点击 + 用 OCR 抓消息文本
  - **首版简化**：只抓会话名（已实现），文本抓取可以用 OCR 单独 prompt；若 OCR 也不可用，保留 `Text=null` + 日志警告，不要崩
- **LoginStateDetector 补全**：
  - 判定登录态：用 `LocateAsync("input", "搜索")` 命中 → Online；未命中 → NeedLogin
  - 二维码截图：`LocateAsync("icon", "二维码")` → 按 bbox 截图区域 → base64 PNG 填 `QrImageRef`
- **ConversationNavigator 重构**：保留 `Navigate` 方法签名（`ConversationNavigateResult Navigate(string keyword)`），内部改走视觉定位；多候选检测：让模型返回的元素列表过滤 `label.Contains(keyword)` 后计数

**验收标准**：

- [ ] 编译 0 错误 0 警告
- [ ] `WeComAutomationVisionTests` 至少 3 个 case：完整流程成功 / 搜索框定位失败返回 false / 发送按钮定位失败返回 false（全 mock，不真实点击）
- [ ] `MessageWatcherVisionTests`：mock 返回 3 个会话项 + OCR 抓取文本，验证产出的 `InboundEvent.Payload.Text` 非空
- [ ] `LoginStateDetectorVisionTests`：mock 定位到搜索框 → 返回 Online；mock 未定位到 → 返回 NeedLogin + QrImageRef 非空
- [ ] **现有 36 个单测不回归**（特别是 `ActionLocatorTests`，若因 ActionLocator 降级导致测试不通过，需同步调整测试或保留旧路径）

**预估工时**：2 天。

---

### 阶段 2C：删 Stubs + 改 Client.App DI + 配置扩展（1 个子智能体，串行）

---

#### 任务 C —— 删 AutomationStubs + 改 Client.App DI + 扩展 client.example.yaml

**职责**：删除占位桩、把真实实现注入 DI、把视觉配置加进示例 yaml。

**依赖**：任务 B 完成（所有真实实现编译通过）。

**文件清单**：

| 路径 | 类型 | 说明 |
|------|------|------|
| `clients/wecom-personal-rpa/src/Client.App/Services/Stubs/AutomationStubs.cs` | **删除** | 整文件删除 |
| `clients/wecom-personal-rpa/src/Client.App/App.xaml.cs` | 修改 | DI 注册：移除 Stub 三行，加 `IVisionLocator / VisionCache / ScreenCapturer / IVisionApi / IActionExecutor / IWeComAutomation / IHealthSupervisor` 真实实现 |
| `clients/wecom-personal-rpa/src/Client.App/Services/SendMessageService.cs` | 修改 | `DownloadToTempAsync` 占位空文件改为真实 `IAgentApiClient.DownloadFileAsync`（STATUS.md ③ 标记的缺口同步补上） |
| `clients/wecom-personal-rpa/configs/client.example.yaml` | 修改 | 加 `vision:` 配置段（设计 §5.3 完整内容） |
| `clients/wecom-personal-rpa/src/Client.App/Services/ClientOptionsLoader.cs` | 修改 | 解析 `vision:` 段，填入 `ClientOptions.Vision` |
| `clients/wecom-personal-rpa/src/Client.Core/Config/ClientOptions.cs` | 修改 | 加 `VisionConfig Vision { get; set; }` 字段（或在 Automation 工程的 `VisionConfig` 上做映射器；二选一，建议后者避免 Core 引用 Automation） |

**关键代码改动**（`App.xaml.cs.ConfigureServices`）：

```csharp
// 删除：
// services.AddSingleton<IActionExecutor, StubActionExecutor>();
// services.AddSingleton<IWeComAutomation, StubWeComAutomation>();
// services.AddSingleton<IHealthSupervisor, StubHealthCapture>();

// 替换为：
var visionConfig = LoadVisionConfig(opts);  // 从 ClientOptions.Vision 读
services.AddSingleton(visionConfig);

// 截图采集（无状态，单例）
services.AddSingleton<ScreenCapturer>();

// 视觉缓存（SQLite，单例，Dispose 由 DI 容器管）
services.AddSingleton<VisionCache>(sp => new VisionCache(
    Path.Combine(DataDirectory, "vision_cache.db"),
    TimeSpan.FromHours(visionConfig.Cache.TtlHours)));

// Qwen3-VL API 客户端（HttpClient 走 IHttpClientFactory）
services.AddHttpClient<QwenVisionApi>((sp, client) =>
{
    client.Timeout = TimeSpan.FromSeconds(visionConfig.TimeoutSeconds);
});
services.AddSingleton<IVisionApi>(sp => sp.GetRequiredService<QwenVisionApi>());

// 视觉定位器（主策略）
services.AddSingleton<IVisionLocator, QwenVisionLocator>();

// 自动化层（IActionExecutor / IWeComAutomation / IHealthSupervisor 真实实现）
services.AddSingleton<InputExecutor>();
services.AddSingleton<IWeComAutomation, WeComAutomation>();
services.AddSingleton<IActionExecutor>(sp =>
    sp.GetRequiredService<SendMessageService>() is IActionExecutor exec ? exec
    : throw new InvalidOperationException("SendMessageService must implement IActionExecutor"));
services.AddSingleton<IHealthSupervisor, HealthSupervisor>();
```

**`vision:` 配置段**（必须 1:1 复刻设计 §5.3）：

```yaml
vision:
  enabled: true
  primary_model: "qwen3-vl-plus"
  fallback_model: "qwen3-vl-max"
  api_endpoint: "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
  api_keys: ${QWEN_API_KEYS}        # 复用服务端同款 key 池（逗号分隔）
  timeout_seconds: 120
  max_retries: 3
  cache:
    enabled: true
    ttl_hours: 24
    sqlite_path: "${AppData}/WeComRpa/vision_cache.db"
  screenshot:
    pre_foreground_check: true
    pixel_sanity_check: true
    max_white_ratio: 0.5
    min_color_diversity: 30
```

**实现要点**：

- `ClientOptions.Vision` 字段：建议在 `Client.Core/Config/ClientOptions.cs` 加一个 `VisionConfig Vision { get; set; } = new();`，`VisionConfig` 类放在 Core 工程（纯 POCO，不引依赖），由 Automation 工程的 `VisionConfig` 映射；**或** 直接在 Automation 工程定义 `VisionConfig`，由 `ClientOptionsLoader` 反射加载 yaml → `VisionConfig`。**推荐后者**：避免 Core 引用 Windows-only 依赖。
- `api_keys: ${QWEN_API_KEYS}` 的变量替换：参照 `ClientOptionsLoader` 已有的 `${AppData}` 替换逻辑，扩展支持任意 `${ENV_VAR}` 占位符
- `DownloadToTempAsync` 接真实下载：调 `agentApiClient.DownloadFileAsync(fileUrl, tmpPath, ct)`（`IAgentApiClient` 已有此方法）

**验收标准**：

- [ ] `AutomationStubs.cs` 从磁盘删除，`grep -r "StubActionExecutor" clients/wecom-personal-rpa/src/` 返回 0 行
- [ ] `dotnet build WeComPersonalRpaClient.sln -c Debug` 0 错误 0 警告
- [ ] `dotnet test` 全绿（36 + 阶段 2A/2B 新增全部）
- [ ] `configs/client.example.yaml` 含完整 `vision:` 段，键名 / 嵌套层级与设计 §5.3 一致
- [ ] App 启动（不连真实服务端、不连真实企微）应能成功初始化 DI 容器（托盘窗口可显示），不抛「未注册」异常

**预估工时**：0.5 天。

---

### 阶段 3A：编译 + 单测验证（1 个子智能体）

#### 任务 D —— 全量编译 + 单测验证

**职责**：跑全量编译和单测，确认无回归、无警告、无遗漏 stub。

**依赖**：任务 C 完成。

**文件清单**：仅运行命令，不改代码（发现问题时反馈给前面任务的子智能体回修）。

**验收命令**：

```powershell
cd C:\repos\aid-work-agent\clients\wecom-personal-rpa
dotnet build WeComPersonalRpaClient.sln -c Debug
dotnet test WeComPersonalRpaClient.sln --no-build -c Debug
```

**验收标准**：

- [ ] `dotnet build` 输出 `0 Error(s) / 0 Warning(s)`
- [ ] `dotnet test` 全绿，测试数 ≥ 36 + 新增（预估 ≥ 50 个）
- [ ] `Get-ChildItem -Path src -Recurse -Filter "AutomationStubs.cs"` 返回 0 行
- [ ] `Select-String -Path "src/Client.App/App.xaml.cs" -Pattern "Stub"` 返回 0 行

**预估工时**：0.5 天。

---

### 阶段 3B：真机视觉回归（1 个子智能体，可选）

#### 任务 E —— 真机视觉回归测试

**职责**：在真实 Windows 11 + 企业微信 PC 环境上跑端到端验证。

**依赖**：任务 D 全绿、`QWEN_API_KEYS` 可用。

**测试矩阵**：

| 场景 | 验证项 | 期望结果 |
|------|--------|---------|
| 1. 视觉定位准确率 | 跑 `Client.VisionProbe` 或专门的 `VisionRegressionTests`，对 24 个标准元素定位 | bbox 命中率 ≥ 95%（vs Python 原型 100%） |
| 2. 缓存命中率 | 同一窗口连续定位同一元素 5 次 | 第 2-5 次 Source="cache"，API 调用次数 = 1 |
| 3. 窗口指纹失效 | 改变企微窗口尺寸后定位 | 缓存自动失效，触发新 API 调用 |
| 4. 截图前置校验 | 把 VSCode 拉到企微上方后定位 | 抛 `SuspiciousScreenshotException` 或 `WindowNotForegroundException` |
| 5. 端到端发送 | 真实发送一条消息给「文件传输助手」 | 消息发送成功，无报错 |
| 6. 失败降级 | 关闭网络后定位 | API 重试 → OCR 降级 → 返回 null（不崩） |
| 7. 登录态检测 | 退出登录后跑 `LoginStateDetector.Detect()` | 返回 NeedLogin + QrImageRef 非空 |

**验收标准**：

- [ ] 场景 1-4 全部通过（核心）
- [ ] 场景 5-7 至少手动验证 1 次（真实环境，可记录在 STATUS.md）
- [ ] 产出 `docs/system/wecom-personal-rpa-vision-regression-report.md`（测试结果截图 + bbox 标注图）

**预估工时**：2 天。

---

### 阶段 4：收尾文档更新（项目维护者本人执行，不在子智能体范围）

---

> **本节仅作记录**：阶段 4 由项目维护者本人执行，子智能体**不要做**。

**待更新的文档清单**：

| 文档 | 更新内容 |
|------|---------|
| `docs/system/wecom-personal-rpa-design.md` | §6.2 三层策略 → Qwen3-VL→OCR→Win32+OpenCV；§6.3 / §6.4 发送流程改走视觉定位；§10.1 准入验证增加多模态视觉准确性项 |
| `docs/system/wecom-personal-rpa-vision-design.md` | §1.1 状态改为「✅ 已落地」；§7 风险章节增加真机回归结论 |
| `clients/wecom-personal-rpa/STATUS.md` | ② 🟡 / ③ 🔴 清单按实际落地状态迁移到 ① ✅ |
| `plans/plan-wecom-personal-rpa.md` | §0 准入验证清单从「FlaUI/UIA3 探测」改为「Qwen3-VL 视觉定位验证」，标记已完成 |
| `docs/ideas.md` | #29 状态从「📋 待开发」→「✅ 已完成开发」或「🔧 部分完成」 |

---

## 4. 并行 / 串行执行编排

```
                  ┌─ A1: ScreenCapturer + WindowFingerprint  (1.5d) ─┐
阶段 2A (并行) ───┼─ A2: VisionCache (SQLite)                   (1d)  ─┼─→ 集成检查 (0.25d)
                  └─ A3: QwenVisionLocator + OCR + DTO          (2d)  ─┘           │
                                                                                    ↓
阶段 2B (串行) ────────────────────── B: WeComAutomation / MessageWatcher /         (2d)
                                          LoginStateDetector / ConversationNavigator
                                          重构 + InputExecutor 新建
                                                                                    ↓
阶段 2C (串行) ────────────────────── C: 删 Stubs + DI + yaml 配置                (0.5d)
                                                                                    ↓
阶段 3A (串行) ────────────────────── D: 全量编译 + 单测验证                      (0.5d)
                                                                                    ↓
阶段 3B (可选) ────────────────────── E: 真机视觉回归                            (2d)
                                                                                    ↓
阶段 4 (本人)  ────────────────────── 文档同步（不在子智能体范围）
```

**并行度**：阶段 2A 三个子智能体**完全并行**，2B/2C/3A 严格串行。

**总预估工时（端到端）**：
- 阶段 2A 并行段：max(A1, A2, A3) = **2 天**
- 集成检查：**0.25 天**
- 阶段 2B：**2 天**
- 阶段 2C：**0.5 天**
- 阶段 3A：**0.5 天**
- 阶段 3B（可选）：**2 天**
- **小计（不含真机回归）：5.25 天**
- **含真机回归：7.25 天**

---

## 5. 风险与回退

| 任务 | 失败场景 | 影响范围 | 回退方式 |
|------|---------|---------|---------|
| A1 | 像素自检阈值误判（把企微判成 VSCode） | 截图永远失败，整个视觉链失效 | 配置项 `vision.screenshot.pixel_sanity_check=false` 关闭自检；阈值可通过 `max_white_ratio` / `min_color_diversity` 调整 |
| A2 | SQLite 文件锁冲突 / 损坏 | 缓存读写失败 | `VisionCache` 内部 try-catch，失败时 log warning + 当作 miss（不影响主流程） |
| A3 | Qwen3-VL API 永久不可用（key 失效 / 服务下线） | 主策略失效 | 配置 `vision.enabled=false` → 全链降级到 `ActionLocator`（保留 FlaUI/OpenCV）；同时 OCR 降级仍可工作 |
| A3 | Windows.Media.Ocr 在 Client.Automation 引入后破坏编译 | Client.Automation 无法编译 | 回退 csproj TFM 改动（保留 `net8.0-windows`，移除 `OcrVisionLocator`，只用 Qwen 主策略 + ActionLocator 兜底） |
| B | WeComAutomation 重构破坏现有 36 单测 | 任务 D 失败 | 保留 `ActionLocator` 三层降级代码不删，重构后 `WeComAutomation` 在视觉定位失败时回退到 ActionLocator；同步调整 ActionLocatorTests 适配新调用路径 |
| B | ConversationNavigator 视觉定位多候选歧义 | 会话导航失败率上升 | 保留 `NeedsReview=true` 上报路径，让服务端人工绑定；不强行猜测 |
| C | 删除 AutomationStubs 后 Client.App 启动崩 | 客户端无法启动 | 检查 DI 注册完整性（每个 `services.AddSingleton<IXxx, Xxx>` 必须有对应实现类）；用 `dotnet build` 警告查未使用 using |
| E | 真机视觉回归 bbox 准确率 < 95% | 视觉方案不可用 | 不上线，回到设计文档 §7 重新评估（可能需要 prompt 调优 / 模型升级到 qwen3-vl-max） |

**整体回退预案**：若阶段 3A 编译失败或单测回归无法修复，**git revert 阶段 2A/2B/2C 的全部 commit**，恢复到当前 master（`215850d`）状态。**客户端当前虽不可用，但可编译可启动（通过 Stubs）**，回到这个状态不影响服务端。

---

## 6. 验收标准（端到端）

### 6.1 编译与测试

- `dotnet build WeComPersonalRpaClient.sln -c Debug` → **0 错误 / 0 警告**
- `dotnet test WeComPersonalRpaClient.sln` → **全部通过**（≥ 50 个测试，含现有 36 + 新增 ≥ 14）
- `grep -r "StubActionExecutor\|StubWeComAutomation\|StubHealthCapture" clients/wecom-personal-rpa/src/` → **0 行**

### 6.2 视觉定位质量（阶段 3B）

- 24 个标准元素（设计 §2.3 Task 2 列表）bbox 命中率 **≥ 95%**
- 缓存命中率（同窗口连续定位）：**100%**（第 2 次起 Source="cache"）
- 窗口尺寸变化后缓存失效：**100%**

### 6.3 接口落地完整性

| 接口 | 当前（master） | 目标 |
|------|---------------|------|
| `IActionExecutor` | StubActionExecutor | SendMessageService（真实） |
| `IWeComAutomation` | StubWeComAutomation | WeComAutomation（真实，走视觉定位） |
| `IHealthSupervisor` | StubHealthCapture | HealthSupervisor（真实） |
| `IVisionLocator` | 不存在 | QwenVisionLocator（真实） |
| `IVisionApi` | 不存在 | QwenVisionApi（真实） |

### 6.4 配置完整性

- `configs/client.example.yaml` 含完整 `vision:` 段
- `ClientOptionsLoader` 能解析 `vision:` 段并填入运行时配置
- `api_keys: ${QWEN_API_KEYS}` 占位符能从环境变量替换

### 6.5 协议与服务端不变

- `git diff src/saas/ src/channels/wecom_personal_rpa/` → **0 行**
- `git diff docs/system/wecom-personal-rpa-protocol.md` → **0 行**

---

## 7. 文档同步要求

每个任务完成后，**子智能体必须同步更新**：

| 任务 | 必须更新的文档 |
|------|---------------|
| A1 / A2 / A3 完成 | 在 `clients/wecom-personal-rpa/STATUS.md` ②🟡 或 ③🔴 表格标注「视觉定位改造：阶段 2A 已完成」 |
| B 完成 | 在 `STATUS.md` 标注「WeComAutomation 重构：阶段 2B 已完成」 |
| C 完成 | 在 `STATUS.md` ③🔴 表格中移除 `AutomationStubs.cs` / `MessageWatcher.cs Text=null` / `LoginStateDetector 二维码` / `SendMessageService.DownloadToTempAsync` 4 行（已迁移到 ① ✅） |
| D 完成 | 更新 `STATUS.md` 的「编译与测试现状」段：测试数从 36 → 新数 |
| E 完成 | 新建 `docs/system/wecom-personal-rpa-vision-regression-report.md`；在 `STATUS.md` 末尾「一句话结论」段更新视觉定位验证结论 |

**所有文档登记**：

- 本计划文档登记在 `docs/ideas.md` #29（已存在，由项目维护者管理）
- 阶段 4 的最终文档同步（更新主设计 / 协议 / ideas.md 状态）由项目维护者执行，**不在子智能体范围**

---

## 附录 A：关键技术约束清单（子智能体必读）

> **以下约束是硬性的，违反任一条都会导致返工。**

### A.1 工程规范

| 项 | 要求 |
|---|------|
| C# 版本 | `LangVersion=latest`（C# 12） |
| TFM | `Client.Automation.csproj` / `Client.Tests.csproj` 升级为 `net8.0-windows10.0.19041.0`（为 Windows.Media.Ocr）；其他工程不动 |
| 平台 | `x64` only |
| Nullable | `enable` |
| 日志 | **统一用 Serilog**（`Log.Information / Log.Warning / Log.Error`），禁止 `Console.WriteLine` / `System.Diagnostics.Debug` |
| 日志脱敏 | API key / base64 图片 / 签名等敏感信息**禁止打印**；日志里只打 `api_key=***` / `image=<byte长度>` |
| 异常 | 自定义异常继承 `Exception`，关键异常（`VisionApiAuthException` / `SuspiciousScreenshotException`）必须有中文 message |
| XML 注释 | 所有 public 类 / 方法 / 属性必须有 `/// <summary>` XML 注释（参照现有代码风格） |
| using 顺序 | System → 第三方 → 项目内（参照现有文件） |

### A.2 协议与服务端零改动

- **禁止修改** `src/saas/api/wecom_personal_rpa_*.py`
- **禁止修改** `src/channels/wecom_personal_rpa/`
- **禁止修改** `docs/system/wecom-personal-rpa-protocol.md`
- **禁止修改** `docs/system/wecom-personal-rpa-design.md`（阶段 4 由项目维护者统一改）

### A.3 API Key 管理

- **必须从配置文件读**：`VisionConfig.ApiKeys` 数组
- **禁止读环境变量**：`Environment.GetEnvironmentVariable("QWEN_API_KEYS")` 不允许出现在代码里（只在 yaml 占位符 `${QWEN_API_KEYS}` 替换层出现）
- **禁止硬编码**：源码里 0 处 API key 字面量
- **禁止日志打印**：key 永远是 `***`

### A.4 截图与缓存

- 截图前**必须**校验企微在前台（设计 §3.4，`auto-capture-wecom.ps1` 已验证）
- 截图后**必须**像素自检（同上）
- 缓存**必须**走 SQLite（`VisionCache`），不要用 `MemoryCache`（Gunicorn 多 worker 隔离规则在 Windows 服务同款问题）
- 缓存 TTL **默认 24h**，可通过配置改

### A.5 失败 loud 原则

- Qwen3-VL API 4xx（鉴权失败）→ **立即抛异常 + 上层暂停账号**，不要静默吞
- 模型返回 bbox 越界 → **抛异常 + 失效该元素缓存 + 日志 warning**，不要尝试"修正 bbox"
- 截图自检失败 → **抛 `SuspiciousScreenshotException` + 暂停账号**，不要继续往下走
- 任何视觉层异常都**不能让消息发送"半成功"**（点击了搜索框但没点中会话 → 不能继续 TypeText）

### A.6 并行协同（仅阶段 2A）

- A1 / A2 / A3 子智能体**并行启动**，但**必须在编码前**对齐：
  - `WindowFingerprint` 的命名空间和字段（A1 主导）
  - `BoundingBox` 的命名空间和字段（A3 主导）
  - `VisionConfig` 的字段结构（A3 主导）
- 对齐方式：每个子智能体先**只创建自己负责的 DTO 文件**并 push，然后再编码主类
- 集成检查（任务 B 之前的 0.25 天）由集成者（项目维护者或阶段 2B 子智能体）执行：确认所有 DTO 只有一份定义、所有跨任务引用能解析

---

## 附录 B：文件路径速查表

| 类型 | 路径 |
|------|------|
| 设计文档（权威） | `docs/system/wecom-personal-rpa-vision-design.md` |
| 客户端现状 | `clients/wecom-personal-rpa/STATUS.md` |
| ScreenCapturer 原型 | `clients/wecom-personal-rpa/scripts/auto-capture-wecom.ps1` |
| QwenVisionLocator 原型 | `clients/wecom-personal-rpa/scripts/probe-qwen-vl-full.py` |
| SQLite 用法参考 | `clients/wecom-personal-rpa/src/Client.Core/Queue/SqliteSendQueue.cs` |
| VisionProbe csproj 参考 | `clients/wecom-personal-rpa/src/Client.VisionProbe/Client.VisionProbe.csproj` |
| 要重构的 WeComAutomation | `clients/wecom-personal-rpa/src/Client.Automation/WeComAutomation.cs` |
| 要重构的 SendMessageService | `clients/wecom-personal-rpa/src/Client.Automation/WeCom/SendMessageService.cs` |
| 要重构的 MessageWatcher | `clients/wecom-personal-rpa/src/Client.Automation/WeCom/MessageWatcher.cs` |
| 要重构的 LoginStateDetector | `clients/wecom-personal-rpa/src/Client.Automation/WeCom/LoginStateDetector.cs` |
| 要重构的 ConversationNavigator | `clients/wecom-personal-rpa/src/Client.Automation/WeCom/ConversationNavigator.cs` |
| 要保留为兜底的 ActionLocator | `clients/wecom-personal-rpa/src/Client.Automation/WeCom/ActionLocator.cs` |
| 要删除的 AutomationStubs | `clients/wecom-personal-rpa/src/Client.App/Services/Stubs/AutomationStubs.cs` |
| DI 注册位置 | `clients/wecom-personal-rpa/src/Client.App/App.xaml.cs` |
| 配置示例 | `clients/wecom-personal-rpa/configs/client.example.yaml` |
| 解决方案文件 | `clients/wecom-personal-rpa/WeComPersonalRpaClient.sln` |

---

## 附录 C：设计文档需要补充/澄清的点

> 在编写本计划过程中发现以下设计文档**未明确**的点，已在计划中给出默认决策；若项目维护者有不同意见，请在阶段 2A 启动前提出。

### C.1 `BoundingBox` 越界检查的 tolerance

- **设计文档 §4.4** 只说「bbox 越界 → 抛异常」，未给 tolerance 值
- **本计划默认**：`tolerance=5` 像素（模型偶尔返回的边缘像素可容忍）
- **建议**：在真机回归（阶段 3B）时实测调整

### C.2 `MessageWatcher` 抓取消息文本的具体策略

- **设计文档 §5.1** 说「用 QwenVisionLocator 抓消息文本」，但没说：
  - 用 grounding（Task 2 prompt）还是 OCR（Task 3 prompt）？
  - 抓当前会话所有消息还是只抓最后一条？
  - 如何避免重复上报（去重键怎么设计）？
- **本计划默认**：
  - 优先用 OCR prompt（更便宜，~48s/次，能拿到全部文字）
  - 抓当前会话最后一条消息（去重键 = `account_id + conversation_id + last_message_text_hash`）
  - 若 OCR 失败回退到 grounding，再失败保留 `Text=null` + 日志 warning

### C.3 `LoginStateDetector` 二维码区域定位

- **设计文档 §5.1** 说「用 QwenVisionLocator 识别二维码/登录态」，未明确：
  - 二维码定位用什么 `elementType`？（icon / image / other）
  - 已登录态判定的「搜索框」label 关键词是什么？（"搜索" / "Search" / "搜索框"）
- **本计划默认**：
  - 二维码：`elementType="icon", labelKeyword="二维码"`
  - 已登录判定：`elementType="input", labelKeyword="搜索"`（设计文档 §3.4 grounding prompt 第 1 项）

### C.4 缓存键的 fingerprint 序列化方式

- **设计文档 §3.3** 说缓存键 = `(window_fingerprint, element_type, label_keyword)`，未明确 fingerprint 在 SQLite 里如何序列化
- **本计划默认**：把 fingerprint 的 6 个字段（class/x/y/w/h/dpi/version）拆成 6 个独立列 + 1 个 rect 字符串，**而不是 JSON 序列化**（便于 SQL 查询索引）

### C.5 多 key 池的轮询策略

- **设计文档 §4.1** 提到「多 key 池通过客户端配置传入」，未明确：
  - 轮询（round-robin）还是随机？
  - 某个 key 4xx 失败后是跳到下一个还是整池禁用？
- **本计划默认**：
  - 轮询（`Interlocked.Increment(ref _keyIndex) % keys.Length`）
  - 429（限流）→ 跳到下一个
  - 401（鉴权失败）→ 跳过该 key 但不立即禁用整池，所有 key 都 401 才抛 `VisionApiAuthException`

### C.6 csproj TFM 升级的副作用

- **设计文档 §5.1** 提到「`OcrVisionLocator` 封装 Windows.Media.Ocr」，未明确这意味着 Client.Automation 的 TFM 要升级
- **本计划已识别**：Client.Automation 必须从 `net8.0-windows` 升到 `net8.0-windows10.0.19041.0`，并连带 Client.Tests 同步升级
- **潜在副作用**：
  - Client.App 间接引用 Client.Automation，其 TFM 保持 `net8.0-windows` 是否兼容？**需要阶段 3A 验证**
  - 若不兼容，Client.App 也得升 TFM
- **回退方案**：若 TFM 升级导致大面积破坏，**先把 OcrVisionLocator 放到独立的 `Client.VisionOcr` 工程**（TFM=`net8.0-windows10.0.19041.0`），Client.Automation 保持原 TFM

---

**END OF PLAN**
