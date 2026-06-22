namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// 监督进程运行配置（只读快照，从 appsettings.json / serilog.json / 环境变量加载一次后冻结）。
/// 对应 appsettings 节："Supervisor": { ... }
/// </summary>
public sealed class SupervisorOptions
{
    /// <summary>被监督的 Client.App 可执行文件绝对路径（例如 C:\Program Files\WeComRpa\Client.App.exe）。</summary>
    public string AppExePath { get; set; } = string.Empty;

    /// <summary>Client.App 进程名（不带扩展名），用于 <see cref="System.Diagnostics.Process.GetProcessesByName"/>。</summary>
    public string AppProcessName { get; set; } = "Client.App";

    /// <summary>拉起 Client.App 时使用的工作目录；为空则取 <see cref="AppExePath"/> 所在目录。</summary>
    public string AppWorkingDirectory { get; set; } = string.Empty;

    /// <summary>拉起 Client.App 时传入的命令行参数（可空）。</summary>
    public string AppArguments { get; set; } = string.Empty;

    /// <summary>存活探测周期（秒）。默认 30s。</summary>
    public int PollIntervalSeconds { get; set; } = 30;

    /// <summary>指数退避初始延迟（毫秒）。每次连续拉起失败后翻倍，直到 <see cref="MaxBackoffMilliseconds"/>。</summary>
    public int InitialBackoffMilliseconds { get; set; } = 2000;

    /// <summary>指数退避上限（毫秒）。默认 5 分钟。</summary>
    public int MaxBackoffMilliseconds { get; set; } = 300000;

    /// <summary>连续拉起失败次数阈值；达到后写 Windows Event Log error 并触发 <see cref="OfflineReporter"/>。</summary>
    public int ConsecutiveFailureThreshold { get; set; } = 5;

    /// <summary>Windows 事件日志源名（在 EventLogSink 初始化前需具备注册权限；服务安装时由 installer 注册）。</summary>
    public string EventLogSource { get; set; } = "WeComPersonalRpaSupervisor";

    /// <summary>本地 Serilog 文件日志路径（相对于工作目录或绝对路径）。</summary>
    public string LogFilePath { get; set; } = "logs\\supervisor\\.log";

    /// <summary>是否启用客户端离线上报（生产环境为 true；CI/单测可置 false 以隔离网络）。</summary>
    public bool OfflineReportEnabled { get; set; } = true;
}
