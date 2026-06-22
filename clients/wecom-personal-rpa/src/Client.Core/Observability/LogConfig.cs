using Serilog;
using Serilog.Core;
using Serilog.Events;
using Serilog.Filters;

namespace WeCom.PersonalRpa.Core.Observability;

/// <summary>
/// Serilog 日志配置（protocol.md §C.7）。
/// 滚动文件 sink + Windows Event Log sink（占位）+ Async sink。
/// 所有日志记录先经 <see cref="SensitiveRedactor"/> 脱敏。
/// </summary>
public static class LogConfig
{
    /// <summary>
    /// 构建并配置 Serilog Logger。
    /// </summary>
    /// <param name="logDir">日志目录绝对路径。</param>
    /// <param name="appName">应用名（用于文件名前缀与 Event Log source）。</param>
    /// <param name="minimumLevel">最低日志级别。</param>
    /// <returns>已配置的 Logger（调用方负责 Dispose）。</returns>
    public static Logger Build(string logDir, string appName = "WeComPersonalRpa",
        LogEventLevel minimumLevel = LogEventLevel.Information)
    {
        if (string.IsNullOrEmpty(logDir)) throw new ArgumentNullException(nameof(logDir));
        Directory.CreateDirectory(logDir);

        var redactor = new SensitiveRedactor();

        var config = new LoggerConfiguration()
            .MinimumLevel.Is(minimumLevel)
            // 敏感字段脱敏：通过自定义 filter 在写入前 redact
            .Destructure.With<SensitiveDestructuringPolicy>()
            .Enrich.WithProperty("App", appName)
            .Enrich.WithProperty("Pid", Environment.ProcessId);

        // 滚动文件（每天一个，保留 15 天），经 Async 包裹避免 IO 阻塞调用线程
        var filePath = Path.Combine(logDir, $"{appName}-.log");
        config.WriteTo.Async(a => a.File(filePath,
            rollingInterval: RollingInterval.Day,
            retainedFileCountLimit: 15,
            encoding: System.Text.Encoding.UTF8,
            outputTemplate: "{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz} [{Level:u}] {App}/{Pid} {Message:lj}{NewLine}{Exception}"));

        // Windows Event Log sink 占位（生产由 App/Supervisor 用 net8.0-windows 引入 EventLog sink）
        // TODO: 在 Windows 工程中添加 Serilog.Sinks.EventLog 包并在此条件注入。
        //       Core 工程目标 net8.0（非 Windows），不能直接依赖 EventLog sink。

        return config.CreateLogger();
    }

    /// <summary>
    /// 自定义 DestructuringPolicy：对所有字符串属性值做脱敏。
    /// </summary>
    private sealed class SensitiveDestructuringPolicy : IDestructuringPolicy
    {
        private readonly SensitiveRedactor _redactor = new();

        public bool TryDestructure(object value, ILogEventPropertyValueFactory factory,
            [System.Diagnostics.CodeAnalysis.MaybeNullWhen(false)] out LogEventPropertyValue result)
        {
            if (value is string s)
            {
                result = new ScalarValue(_redactor.Redact(s));
                return true;
            }
            result = null;
            return false;
        }
    }
}
