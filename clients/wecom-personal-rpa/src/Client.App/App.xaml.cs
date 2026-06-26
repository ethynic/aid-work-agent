using System.IO;
using System.Windows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using WeCom.PersonalRpa.App.Autostart;
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
            .WriteTo.Async(a => a.File(
                logPath,
                rollingInterval: RollingInterval.Day,
                retainedFileCountLimit: 14,
                outputTemplate: "{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz} [{Level:u3}] [{App}] {SourceContext} {Message:lj}{NewLine}{Exception}"))
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
        services.AddSingleton<ClientSession>();
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
