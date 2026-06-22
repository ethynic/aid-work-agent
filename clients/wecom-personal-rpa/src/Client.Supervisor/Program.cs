using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Hosting.WindowsServices;
using Serilog;
using Serilog.Events;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Security;
using WeCom.PersonalRpa.Supervisor;

// Windows Service 入口：Host.CreateDefaultBuilder + UseWindowsService。
// 关键约束（protocol.md §C.1 / 设计文档 §3.2）：
//   - Supervisor 不操作企业微信 UI（Session 0 隔离），仅做存活检测 + 拉起 + 离线上报。
//   - 命名空间 WeCom.PersonalRpa.Supervisor，目标 net8.0-windows。
var configuration = new ConfigurationBuilder()
    .SetBasePath(AppContext.BaseDirectory)
    .AddJsonFile("appsettings.json", optional: true, reloadOnChange: false)
    .AddJsonFile($"appsettings.{Environment.GetEnvironmentVariable("DOTNET_ENVIRONMENT") ?? "Production"}.json",
        optional: true, reloadOnChange: false)
    .AddEnvironmentVariables(prefix: "WECOMRPA_")
    .Build();

var supervisorOptions = configuration.GetSection("Supervisor").Get<SupervisorOptions>() ?? new SupervisorOptions();
var clientOptions = configuration.GetSection(ClientOptions.SectionName).Get<ClientOptions>() ?? new ClientOptions();

// Serilog 启动期日志（Host 构建前先有一份可观测性，避免 bootstrap 阶段黑盒）。
var logFilePath = Path.IsPathRooted(supervisorOptions.LogFilePath)
    ? supervisorOptions.LogFilePath
    : Path.Combine(AppContext.BaseDirectory, supervisorOptions.LogFilePath);
var logDir = Path.GetDirectoryName(logFilePath);
if (!string.IsNullOrEmpty(logDir))
{
    Directory.CreateDirectory(logDir);
}

Log.Logger = new LoggerConfiguration()
    .ReadFrom.Configuration(configuration)
    .MinimumLevel.Information()
    .Enrich.FromLogContext()
    .WriteTo.File(logFilePath, rollingInterval: RollingInterval.Day, shared: true, retainedFileCountLimit: 14)
    .CreateBootstrapLogger();

try
{
    Log.Information("后端日志：Supervisor 启动中，AppExePath={AppExePath}, PollInterval={Poll}s",
        supervisorOptions.AppExePath, supervisorOptions.PollIntervalSeconds);

    var builder = Host.CreateDefaultBuilder(args);

    // 注册为 Windows Service（通过 sc.exe / 计划任务托管时生效；控制台调试运行时此方法为 no-op）。
    builder.UseWindowsService(options =>
    {
        options.ServiceName = "WeComPersonalRpaSupervisor";
    });

    builder.ConfigureAppConfiguration((_, cfg) => { cfg.AddConfiguration(configuration); });

    builder.ConfigureServices((_, services) =>
    {
        // 选项以单例方式注入：启动后冻结，禁止运行期热更（避免拉起循环中途改变 AppExePath）。
        services.AddSingleton(supervisorOptions);
        services.AddSingleton(clientOptions);

        // RequestSigner：复用 Core 的签名实现（protocol.md §A.1 HMAC-SHA256）。
        // secret 字节：从 ClientOptions.ClientSecret 取 UTF-8 字节。
        // TODO(installer)：生产环境 ClientSecret 应以加密形式落盘，由 EncryptedClientConfig 解密后注入；
        // 当前 Supervisor 启动阶段不引入解密依赖，secret 明文从受 ACL 保护的 appsettings / 环境变量读取。
        var secretBytes = string.IsNullOrEmpty(clientOptions.ClientSecret)
            ? Array.Empty<byte>()
            : System.Text.Encoding.UTF8.GetBytes(clientOptions.ClientSecret);
        var requestSigner = new RequestSigner(clientOptions.ClientId, secretBytes);
        services.AddSingleton(requestSigner);

        // AgentApiClient：通过 IHttpClientFactory 注入 HttpClient，BaseAddress 在构造函数内按 AgentBaseUrl 设置。
        services.AddHttpClient<IAgentApiClient, AgentApiClient>();

        services.AddSingleton<OfflineReporter>();
        services.AddSingleton<WindowsEventLogger>();

        // SupervisorService 同时以单例类型注册（供 HealthReporter 注入同一实例）并作为 HostedService 注册。
        // 仅 AddHostedService<T>() 会把 T 注册为 transient：那样 HealthReporter（单例）会捕获到一个
        // 与后台循环脱离的独立 SupervisorService 实例，采集到的失败计数恒为 0。
        // 显式 AddSingleton<T>() 让 HealthReporter 与 HostedService 解析到同一实例。
        services.AddSingleton<SupervisorService>();
        services.AddHostedService(sp => sp.GetRequiredService<SupervisorService>());
        services.AddSingleton<WeCom.PersonalRpa.Core.Observability.IHealthReporter, HealthReporter>();
    });

    builder.UseSerilog((context, loggerConfig) =>
    {
        loggerConfig.ReadFrom.Configuration(context.Configuration);
        loggerConfig.Enrich.FromLogContext();

        // Windows Service 场景：写本地事件日志，便于运维在不打开文件的情况下定位崩溃。
        // EventLogSink 在非 Windows 环境会抛 PlatformNotSupportedException，受 net8.0-windows TFM 保护。
        var source = supervisorOptions.EventLogSource;
        if (!string.IsNullOrWhiteSpace(source))
        {
            try
            {
                loggerConfig.WriteTo.EventLog(source,
                    restrictedToMinimumLevel: LogEventLevel.Error,
                    manageEventSource: false);
            }
            catch (Exception ex)
            {
                // 事件日志源未注册（需 installer 权限）属于可降级场景，不能阻断启动。
                Log.Warning(ex, "后端日志：Windows EventLog 源 {Source} 注册失败，继续以文件日志运行", source);
            }
        }
    }, preserveStaticLogger: true);

    var host = builder.Build();
    await host.RunAsync();
}
catch (Exception ex)
{
    Log.Fatal(ex, "后端日志：Supervisor 因未处理异常终止");
    throw;
}
finally
{
    Log.CloseAndFlush();
}
