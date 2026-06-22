using System.Diagnostics;
using Serilog;

namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// Windows 事件日志薄封装。
/// 仅在 Supervisor 工程内使用（与 Serilog EventLogSink 互补：后者按级别写，
/// 本类用于显式标记"达到阈值"级别的运维事件，便于 SCOM / EventLog 触发告警）。
/// </summary>
internal sealed class WindowsEventLogger
{
    private readonly SupervisorOptions _options;
    private readonly Serilog.ILogger _logger;

    public WindowsEventLogger(SupervisorOptions options, Serilog.ILogger logger)
    {
        _options = options;
        _logger = logger;
    }

    /// <summary>写一条 Error 级别的运维事件。事件源不存在时降级为 Serilog error，不抛异常。</summary>
    public void LogError(int eventId, string message)
    {
        try
        {
            if (!EventLog.SourceExists(_options.EventLogSource))
            {
                // 源未注册：服务安装器应在 Administrator 上下文注册源；此处避免在运行期提升权限。
                _logger.Error("后端日志：EventLog 源 {Source} 未注册，降级 Serilog。eventId={EventId}, msg={Msg}",
                    _options.EventLogSource, eventId, message);
                return;
            }

            using var eventLog = new EventLog
            {
                Source = _options.EventLogSource,
                Log = "Application",
            };
            eventLog.WriteEntry(message, EventLogEntryType.Error, eventId);
        }
        catch (Exception ex)
        {
            _logger.Error(ex, "后端日志：写 Windows EventLog 失败，已降级。eventId={EventId}", eventId);
        }
    }
}
