using System.IO;
using System.Windows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using WeCom.PersonalRpa.App.Autostart;
using WeCom.PersonalRpa.App.Outbound;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.App.Services;
using WeCom.PersonalRpa.App.Tray;
using WeCom.PersonalRpa.App.Views;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Win32;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Queue;
using WeCom.PersonalRpa.Core.Security;
using WeCom.PersonalRpa.Core.StateMachine;
// Phase 2 will replace with PowershellAutomationBackend
// using AutomationActionExecutor = WeCom.PersonalRpa.Automation.WeCom.SendMessageService;

namespace WeCom.PersonalRpa.App;

/// <summary>
/// WPF 应用入口。负责 DI 容器装配、Serilog 配置、Generic Host 启动托盘与后台服务。
/// </summary>
public partial class App : Application
{
    private const string AppTag = "Client.App";

    private static readonly string AppDir = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "WeComPersonalRpa", "Client.App");

    /// <summary>日志目录（用户可见，托盘"打开日志目录"指向此处）。</summary>
    public static readonly string LogDirectory = Path.Combine(AppDir, "logs");

    /// <summary>本地诊断/运行期数据目录。</summary>
    public static readonly string DataDirectory = Path.Combine(AppDir, "data");

    private IHost? _host;
    private TrayApp? _tray;

    /// <summary>活动 DI 容器（窗口/非 DI 代码取服务用）。</summary>
    public static IServiceProvider? Services { get; private set; }

    /// <summary>
    /// 构造函数：WPF SDK 自动生成的入口点会 new 本对象。负责初始化日志目录、
    /// Serilog 与全局异常兜底；真正的宿主装配放在 <see cref="OnStartup"/>。
    /// （不手写 Main：WPF SDK 会自动生成入口点，手写会触发 CS0017 多入口点冲突。）
    /// </summary>
    public App()
    {
        Directory.CreateDirectory(LogDirectory);
        Directory.CreateDirectory(DataDirectory);
        ConfigureLogging();
        Log.Information("[{Tag}] 进程启动，版本 {Version}", AppTag, ThisAssemblyInfo.Version);

        // 全局未捕获异常兜底（替代原手写 Main 的 try/catch Fatal 分支）
        DispatcherUnhandledException += OnDispatcherUnhandledException;
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
        {
            if (e.ExceptionObject is Exception ex)
            {
                Log.Fatal(ex, "[{Tag}] AppDomain 未捕获异常，进程终止", AppTag);
            }
            Log.CloseAndFlush();
        };
    }

    /// <summary>配置 Serilog：按天滚动文件（异步），保留 14 天。</summary>
    private static void ConfigureLogging()
    {
        var logPath = Path.Combine(LogDirectory, "client-.log");
        Log.Logger = new LoggerConfiguration()
            .MinimumLevel.Information()
            .Enrich.WithProperty("App", AppTag)
            .Enrich.WithProperty("Version", ThisAssemblyInfo.Version)
            // Phase 4 块 G：脱敏 Enricher（设计 §9.4 / 对齐 Python _sanitize_debug）
            // 把渲染后的 Message / 字符串属性 / Exception.Message 中的
            // secret / token / 绝对路径 / base64 二维码替换为占位符。
            .Enrich.With<WeCom.PersonalRpa.App.Logging.SensitiveDataFilter>()
            .WriteTo.Async(a => a.File(
                logPath,
                rollingInterval: RollingInterval.Day,
                retainedFileCountLimit: 14,
                outputTemplate: "{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz} [{Level:u3}] [{App}] {SourceContext} {MessageSanitized:lj}{NewLine}{ExceptionSanitized}"))
            // TODO(Phase 5)：Windows Event Log sink（需引入 Serilog.Sinks.EventLog NuGet 包，
            // 任务约束本阶段不引入新依赖）。届时用 .WriteTo.EventLog("WeComPersonalRpaClient",
            // manageEventSource: true) 启用，关键事件（启动/关闭/PS 失败/崩溃）写入系统事件日志，
            // 便于运维在客户端崩溃（文件日志未 flush）时排查。
            .CreateLogger();

        Log.Information("[{Tag}] 日志目录：{Dir}", AppTag, LogDirectory);
    }

    /// <summary>
    /// WPF 启动：装配 DI 容器、启动宿主、挂载托盘。消息循环由 WPF 自动入口点驱动，
    /// 此处不再调用 <c>Run()</c>（避免与 WPF 消息循环递归）。
    /// </summary>
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        _host = Host.CreateDefaultBuilder()
            .UseSerilog()
            .ConfigureServices(ConfigureServices)
            .Build();

        _host.Start();
        Services = _host.Services;
        Log.Information("[{Tag}] Generic Host 已启动", AppTag);

        // 托盘图标必须由 WPF UI 线程持有
        _tray = _host.Services.GetRequiredService<TrayApp>();
        _tray.Attach();
    }

    /// <summary>装配所有服务到 DI 容器。</summary>
    private static void ConfigureServices(IServiceCollection services)
    {
        // ---- 配置（ClientOptions：非密 + 解密后的密钥） ----
        services.AddSingleton<ClientOptions>(_ => ClientOptionsLoader.Load(DataDirectory));

        // ---- Phase 2：PowerShell 自动化后端 ----
        // ClientOptionsLoader 返回 ClientOptions（非 IConfiguration），故用工厂从 ClientOptions.Automation
        // 派生 PowershellOptions 单例。ILogger<T> 由 Microsoft.Extensions.Logging 抽象提供（Host 已注册）。
        services.AddSingleton<PowershellOptions>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            var a = opts.Automation;
            return new PowershellOptions
            {
                Executable = a.PowershellExecutable,
                OpsScript = a.PowershellOpsScript,
                InvokeTimeoutSeconds = a.InvokeTimeoutSeconds,
            };
        });
        services.AddSingleton<PowershellOpsInvoker>();

        // Phase 2 will replace with PowershellAutomationBackend
        // ---- 视觉配置（VisionConfig：API Key 池来自 QWEN_API_KEYS 环境变量） ----
        // 设计 §5.3 / plan §C：VisionConfig 定义在 Automation 工程，由 Client.App 反射加载并注入。
        // Core 不引入 Windows-only 依赖，故 Vision 字段不挂在 ClientOptions 上。
        // services.AddSingleton<VisionConfig>(_ => VisionConfigLoader.Load(DataDirectory));

        // ---- 签名器（解密后的 secret bytes 来自 ClientOptions.ClientSecret） ----
        services.AddSingleton<RequestSigner>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            var secretBytes = string.IsNullOrEmpty(opts.ClientSecret)
                ? Array.Empty<byte>()
                : System.Text.Encoding.UTF8.GetBytes(opts.ClientSecret);
            return new RequestSigner(opts.ClientId, secretBytes);
        });

        // ---- Agent API（HttpClientFactory 注入 BaseAddress） ----
        services.AddHttpClient<AgentApiClient>((sp, client) =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            if (!string.IsNullOrEmpty(opts.AgentBaseUrl))
            {
                client.BaseAddress = new Uri(opts.AgentBaseUrl);
            }
        });
        services.AddSingleton<IAgentApiClient>(sp => sp.GetRequiredService<AgentApiClient>());

        // ---- 状态机（Core 实现） ----
        // Phase 4 块 G：先注册 PauseState 单例，再注册 ClientSession，由 PauseState 注入到 ClientSession。
        // PauseState 设计为单账号作用域共享（OutboundActionDispatcher / ChatArchiveListener /
        // QrCodeWatcher / DesktopHealthSupervisor / InboundEventReporter 均注入此单例）。
        services.AddSingleton<WeCom.PersonalRpa.Core.StateMachine.PauseState>();
        services.AddSingleton<ClientSession>(sp =>
        {
            var session = new ClientSession
            {
                PauseState = sp.GetRequiredService<WeCom.PersonalRpa.Core.StateMachine.PauseState>(),
            };
            return session;
        });
        services.AddSingleton<IStateManager, StateManager>();

        // ---- 本地出站队列（Core SQLite 实现） ----
        services.AddSingleton<SqliteSendQueue>(_ =>
            new SqliteSendQueue(Path.Combine(DataDirectory, "send_queue.db")));
        services.AddSingleton<ISendQueue>(sp => sp.GetRequiredService<SqliteSendQueue>());

        // ---- 视觉模块（阶段 2A 产出，主路径 Qwen3-VL） ----
        // Phase 2 will replace with PowershellAutomationBackend
        // 截图采集（无状态，单例）；同时暴露实现类型供 QwenVisionLocator 等显式依赖使用。
        // services.AddSingleton<ScreenCapturer>();
        // services.AddSingleton<IScreenCapturer>(sp => sp.GetRequiredService<ScreenCapturer>());

        // 视觉缓存（SQLite 持久化，单例，Dispose 由 DI 容器接管）。
        // services.AddSingleton<VisionCache>(sp =>
        // {
        //     var cfg = sp.GetRequiredService<VisionConfig>();
        //     var dbPath = string.IsNullOrWhiteSpace(cfg.SQLitePath)
        //         ? Path.Combine(DataDirectory, "vision_cache.db")
        //         : cfg.SQLitePath;
        //     var dir = Path.GetDirectoryName(dbPath);
        //     if (!string.IsNullOrEmpty(dir))
        //     {
        //         Directory.CreateDirectory(dir);
        //     }
        //     return new VisionCache(dbPath, TimeSpan.FromHours(cfg.Cache.TtlHours));
        // });
        // services.AddSingleton<IVisionCache>(sp => sp.GetRequiredService<VisionCache>());

        // Qwen3-VL API 客户端（HttpClient 走 IHttpClientFactory，超时 / BaseAddress 在工厂回调里设）。
        // services.AddHttpClient<QwenVisionApi>((sp, client) =>
        // {
        //     var cfg = sp.GetRequiredService<VisionConfig>();
        //     client.Timeout = TimeSpan.FromSeconds(cfg.TimeoutSeconds > 0 ? cfg.TimeoutSeconds : 120);
        // });
        // services.AddSingleton<IVisionApi>(sp => sp.GetRequiredService<QwenVisionApi>());

        // 视觉定位器（主策略：Qwen3-VL grounding + 缓存）。
        // services.AddSingleton<IVisionLocator, QwenVisionLocator>();

        // ---- 自动化层（阶段 2B 产出，替换原 Stub 实现） ----
        // Win32 输入执行器：bbox→屏幕坐标点击 + 剪贴板粘贴。无状态，单例。
        services.AddSingleton<InputExecutor>();
        // 剪贴板备份/还原守卫。无状态，单例。
        services.AddSingleton<ClipboardGuard>();
        // 企微主窗口句柄封装：Phase 1 删除（无消费者，Phase 2 由 PowerShell 后端重写）。
        // services.AddSingleton<WeComMainWindow>();
        // 桌面状态探测器（锁定 / DPI / 分辨率）。无状态，单例。
        services.AddSingleton<DesktopState>();
        // 登录态视觉探测：Phase 1 删除（依赖已退役的视觉链路）。
        // services.AddSingleton<WeCom.PersonalRpa.Automation.WeCom.LoginStateDetector>();
        // 会话定位器：Phase 1 删除（依赖已退役的视觉链路）。
        // services.AddSingleton<ConversationNavigator>();

        // IActionExecutor：Phase 1 删除（视觉路径主实现已退役）。
        // services.AddSingleton<AutomationActionExecutor>();
        // services.AddSingleton<IActionExecutor>(sp => sp.GetRequiredService<AutomationActionExecutor>());

        // IWeComAutomation：Phase 1 删除（视觉主构造已退役）。
        // services.AddSingleton<IWeComAutomation, WeCom.PersonalRpa.Automation.WeComAutomation>();

        // IHealthSupervisor：Phase 1 删除（视觉组合已退役）。
        // services.AddSingleton<IHealthSupervisor, WeCom.PersonalRpa.Automation.WeCom.HealthSupervisor>();

        // ---- App 编排组件 ----
        // 保留：InboundReporter（独立，仅依赖 Core 的 IAgentApiClient/ClientOptions）。
        services.AddSingleton<InboundReporter>();
        services.AddSingleton<AutostartRegistrar>();

        // ---- Phase 3 块 D：会话存档（MessageArchive） ----
        // ArchiveHttpClient：IHttpClientFactory 创建，base 域名由调用方拼装（企微 endpoint 在类内常量）。
        services.AddHttpClient<WeCom.PersonalRpa.App.MessageArchive.ArchiveHttpClient>();
        services.AddSingleton<WeCom.PersonalRpa.App.MessageArchive.ArchiveCryptoService>();
        services.AddSingleton<WeCom.PersonalRpa.App.MessageArchive.ArchiveSeqStore>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            var dbPath = string.IsNullOrEmpty(opts.StoragePath)
                ? Path.Combine(DataDirectory, "archive.db")
                : Path.Combine(opts.StoragePath, "archive.db");
            return new WeCom.PersonalRpa.App.MessageArchive.ArchiveSeqStore(dbPath);
        });
        services.AddSingleton<WeCom.PersonalRpa.App.MessageArchive.ArchiveMediaDownloader>();
        // ChatArchiveListener：单账号实例（以 ClientId 作为 account_id 维度）。
        // 真实多账号场景由后续装配阶段替换。
        // P0-1：ChatArchiveListener 同时实现 IHostedService，由 AddHostedService 包装随 Host 启停。
        // DI 启动顺序：Host 按 AddHostedService 注册顺序启动——ChatArchiveListener 注册在前，
        // InboundEventReporter 注册在后（事件源先启动，订阅者再 subscribe，避免事件丢失）。
        services.AddSingleton<WeCom.PersonalRpa.App.MessageArchive.ChatArchiveListener>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            return new WeCom.PersonalRpa.App.MessageArchive.ChatArchiveListener(
                sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ArchiveHttpClient>(),
                sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ArchiveCryptoService>(),
                sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ArchiveSeqStore>(),
                opts.MessageSource,
                accountId: opts.ClientId);
        });
        services.AddHostedService(sp => sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ChatArchiveListener>());

        // ---- Phase 4 块 E：Inbound 入站解析 + 白名单 ----
        // MonitorUsersCache：白名单本地缓存（绑定级）。InitializeAsync 由 MonitorUsersHostedService
        // 在 Host 启动时触发（Phase 4 测试阶段补齐跨块协调缺口）。
        services.AddSingleton<WeCom.PersonalRpa.App.Inbound.MonitorUsersCache>();
        // MonitorUsersHostedService：启动钩子 → MonitorUsersCache.InitializeAsync
        services.AddHostedService<WeCom.PersonalRpa.App.Inbound.MonitorUsersHostedService>();
        // InboundEventBuilder：ArchiveMessage → InboundEvent 转换 + 媒体中转。
        services.AddSingleton<WeCom.PersonalRpa.App.Inbound.InboundEventBuilder>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            return new WeCom.PersonalRpa.App.Inbound.InboundEventBuilder(
                sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ArchiveHttpClient>(),
                sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ArchiveMediaDownloader>(),
                sp.GetRequiredService<IAgentApiClient>(),
                opts,
                accountId: opts.ClientId);
        });
        // InboundEventReporter：构造时注入 ChatArchiveListener + PauseState。
        // IHostedService.StartAsync 中调 Subscribe(chatArchiveListener) 串联事件链。
        services.AddSingleton<WeCom.PersonalRpa.App.Inbound.InboundEventReporter>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            var optionsWrapper = Microsoft.Extensions.Options.Options.Create(opts);
            var pauseState = sp.GetRequiredService<WeCom.PersonalRpa.Core.StateMachine.PauseState>();
            var listener = sp.GetRequiredService<WeCom.PersonalRpa.App.MessageArchive.ChatArchiveListener>();
            return new WeCom.PersonalRpa.App.Inbound.InboundEventReporter(
                sp.GetRequiredService<WeCom.PersonalRpa.App.Inbound.InboundEventBuilder>(),
                sp.GetRequiredService<WeCom.PersonalRpa.App.Inbound.MonitorUsersCache>(),
                sp.GetRequiredService<IAgentApiClient>(),
                optionsWrapper,
                watcher: listener,
                pauseState: pauseState);
        });
        services.AddHostedService(sp => sp.GetRequiredService<WeCom.PersonalRpa.App.Inbound.InboundEventReporter>());

        // ---- Phase 3 块 C：Outbound 出站执行 ----
        // OutboundQueue：本地 SQLite 持久化队列。DbPath 取 ClientOptions.Outbound.DbPath，
        // 若为相对路径则相对客户端工作目录。
        services.AddSingleton<OutboundQueue>(sp =>
        {
            var opts = sp.GetRequiredService<ClientOptions>();
            var dbPath = opts.Outbound.DbPath;
            // 临时：若是相对路径则保留原样（启动时 cd 客户端根目录）；后续可改用 IWebHostEnvironment
            return new OutboundQueue(opts, sp.GetService<Microsoft.Extensions.Logging.ILogger<OutboundQueue>>());
        });
        // AttachmentDownloader：依赖 HttpClient（短期签名 URL 直连）
        services.AddHttpClient<AttachmentDownloader>();
        // IActionSource stub：内存 Channel 实现，单测与早期接入使用。Phase 4 接入真实 WebSocket 后替换。
        services.AddSingleton<ChannelActionSource>();
        services.AddSingleton<IActionSource>(sp => sp.GetRequiredService<ChannelActionSource>());
        // OutboundActionDispatcher：单 Worker 串行执行 PS 调用。同时实现 IHostedService。
        // P0-5：注入 PauseState 单例，Tenant/Account/Conversation 暂停时 Requeue action。
        services.AddSingleton<OutboundActionDispatcher>(sp =>
            new OutboundActionDispatcher(
                sp.GetRequiredService<OutboundQueue>(),
                sp.GetRequiredService<AttachmentDownloader>(),
                sp.GetRequiredService<PowershellOpsInvoker>(),
                sp.GetRequiredService<IAgentApiClient>(),
                sp.GetRequiredService<ClientOptions>(),
                sp.GetRequiredService<IActionSource>(),
                logger: null,
                pauseState: sp.GetRequiredService<WeCom.PersonalRpa.Core.StateMachine.PauseState>()));
        services.AddHostedService(sp => sp.GetRequiredService<OutboundActionDispatcher>());

        // ---- Phase 3 块 F：QrCode 二维码监听 ----
        // QrCodeWatcher：周期轮询 PS get_login_state，未登录时截二维码上报。
        // 单例 + AddHostedService 让其随 Host 启停。
        services.AddSingleton<WeCom.PersonalRpa.App.QrCode.QrCodeWatcher>();
        services.AddHostedService(sp => sp.GetRequiredService<WeCom.PersonalRpa.App.QrCode.QrCodeWatcher>());

        // ---- Phase 4 块 G：桌面健康监督 + WebSocket 重连管理 ----
        // DesktopHealthSupervisor：60s 周期检查桌面环境（企微进程/锁屏/窗口可见），
        // 异常状态变化时上报 StatusPayload（offline / desktop_locked / window_not_visible）。
        // P1-12：注入 PauseState，桌面异常时本地 SetAccountPaused(true)，恢复时清本地标记。
        // 单例 + AddHostedService 让其随 Host 启停。
        services.AddSingleton<WeCom.PersonalRpa.App.Health.DesktopHealthSupervisor>(sp =>
            new WeCom.PersonalRpa.App.Health.DesktopHealthSupervisor(
                sp.GetRequiredService<IAgentApiClient>(),
                logger: null,
                pauseState: sp.GetRequiredService<WeCom.PersonalRpa.Core.StateMachine.PauseState>()));
        services.AddHostedService(sp => sp.GetRequiredService<WeCom.PersonalRpa.App.Health.DesktopHealthSupervisor>());
        // WebSocketConnectionManager：自动重连（指数退避 1/2/4/8/16/30s 封顶）+ 30s 心跳 +
        // 60s 离线检测 + NetworkChange 立即重连 + Reconnected 事件。
        // 重连成功后触发 Reconnected 事件（OutboundActionDispatcher 订阅做增量 outbox 拉取）。
        services.AddSingleton<WeCom.PersonalRpa.App.Realtime.WebSocketConnectionManager>();
        services.AddHostedService(sp => sp.GetRequiredService<WeCom.PersonalRpa.App.Realtime.WebSocketConnectionManager>());

        // ServerMessageDispatcher：Phase 4 测试阶段补齐跨块协调缺口。
        // 订阅 WebSocketConnectionManager.MessageReceived，解析 paused/resumed/actions JSON：
        //   - paused/resumed → 调 ClientSession.PauseAsync/ResumeAsync
        //   - actions（ActionEnvelope）→ 调 OutboundActionDispatcher.EnvelopeEnqueueAsync 入队
        // 协议字段对齐 protocol.md §A.11（type/scope/conversation_id）。
        services.AddHostedService(sp => new WeCom.PersonalRpa.App.Realtime.ServerMessageDispatcher(
            sp.GetRequiredService<WeCom.PersonalRpa.App.Realtime.WebSocketConnectionManager>(),
            sp.GetRequiredService<ClientSession>(),
            logger: null,
            dispatcher: sp.GetRequiredService<OutboundActionDispatcher>()));

        // Phase 1 删除：以下 5 个编排组件依赖已退役的视觉/UIA 链路，
        //             Phase 2 will rework with PowershellAutomationBackend
        // services.AddSingleton<WeCom.PersonalRpa.App.Services.SendMessageService>();
        // services.AddSingleton<OutboundActionSource>();
        // services.AddSingleton<WeCom.PersonalRpa.App.Services.HealthSupervisor>();
        // services.AddSingleton<WeCom.PersonalRpa.App.Services.LoginStateDetector>();
        // services.AddSingleton<WeCom.PersonalRpa.App.Services.MessageWatcher>();

        // ---- 窗口（瞬态，按需创建） ----
        services.AddTransient<StatusWindow>();
        services.AddTransient<ErrorWindow>();
        // Phase 1 删除：LoginQrWindow（视觉版二维码窗口，Phase 2 重写）。

        // ---- 托盘 ----
        services.AddSingleton<TrayApp>();

        // ---- 编排后台服务 ----
        // Phase 1 删除：RpaHost 依赖上述已退役的 5 个编排组件，注释避免 DI 解析失败。
        // services.AddHostedService<RpaHost>();
    }

    /// <summary>应用退出清理。</summary>
    protected override void OnExit(ExitEventArgs e)
    {
        Log.Information("[{Tag}] 应用退出，Code={Code}", AppTag, e.ApplicationExitCode);
        try
        {
            _tray?.Detach();
            _host?.StopAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult();
            _host?.Dispose();
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "[{Tag}] 退出清理异常", AppTag);
        }
        base.OnExit(e);
    }

    /// <summary>UI 线程未捕获异常兜底：记录后吞掉，避免闪退。</summary>
    private void OnDispatcherUnhandledException(object sender, System.Windows.Threading.DispatcherUnhandledExceptionEventArgs e)
    {
        Log.Error(e.Exception, "[{Tag}] UI 线程未捕获异常", AppTag);
        e.Handled = true;
    }
}

/// <summary>程序集版本信息（统一来源）。</summary>
internal static class ThisAssemblyInfo
{
    public static string Version { get; } = "1.0.0";
}
