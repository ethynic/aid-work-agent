using System.IO;
using System.Windows;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using WeCom.PersonalRpa.App.Autostart;
using WeCom.PersonalRpa.App.Services;
using WeCom.PersonalRpa.App.Services.Stubs;
using WeCom.PersonalRpa.App.Tray;
using WeCom.PersonalRpa.App.Views;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Queue;
using WeCom.PersonalRpa.Core.Security;
using WeCom.PersonalRpa.Core.StateMachine;

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

        // ---- Automation 依赖（占位实现；Automation 具体类落地后替换） ----
        // TODO: Client.Automation 完成后，改用真实实现注册（如 FlaUiActionExecutor 等）
        services.AddSingleton<IActionExecutor, StubActionExecutor>();
        services.AddSingleton<IWeComAutomation, StubWeComAutomation>();
        services.AddSingleton<IHealthSupervisor, StubHealthCapture>();

        // ---- App 编排组件 ----
        services.AddSingleton<InboundReporter>();
        services.AddSingleton<SendMessageService>();
        services.AddSingleton<OutboundActionSource>();
        services.AddSingleton<HealthSupervisor>();
        services.AddSingleton<LoginStateDetector>();
        services.AddSingleton<MessageWatcher>();
        services.AddSingleton<AutostartRegistrar>();

        // ---- 窗口（瞬态，按需创建） ----
        services.AddTransient<LoginQrWindow>();
        services.AddTransient<StatusWindow>();
        services.AddTransient<ErrorWindow>();

        // ---- 托盘 ----
        services.AddSingleton<TrayApp>();

        // ---- 编排后台服务 ----
        services.AddHostedService<RpaHost>();
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
