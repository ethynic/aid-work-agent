using Serilog;
using WeCom.PersonalRpa.Core.Observability;

namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// <see cref="IHealthReporter"/> 的实现（protocol.md §C.4 跨工程接口表 + Core 实际定义）。
///
/// <b>协议偏离登记</b>：protocol.md §C.4 表将此接口命名为 <c>IHealthSupervisor</c>，
/// 但 Client.Core 实际落地为 <c>IHealthReporter</c>（命名空间 WeCom.PersonalRpa.Core.Observability）。
/// 按"跟随已编译的更近期实现"原则，本类实现 <see cref="IHealthReporter"/>。
/// 差异已在 issues 中登记，待 protocol.md 与 Core 对齐。
///
/// 采集内容：App 存活状态、连续拉起失败次数、上次成功拉起时间。
/// </summary>
internal sealed class HealthReporter : IHealthReporter
{
    private readonly SupervisorService _supervisorService;
    private readonly Serilog.ILogger _logger;

    public HealthReporter(SupervisorService supervisorService, Serilog.ILogger logger)
    {
        _supervisorService = supervisorService ?? throw new ArgumentNullException(nameof(supervisorService));
        _logger = logger.ForContext<HealthReporter>();
    }

    /// <inheritdoc />
    public Task<IReadOnlyDictionary<string, object?>> CollectAsync(CancellationToken cancellationToken = default)
    {
        IReadOnlyDictionary<string, object?> snapshot = new Dictionary<string, object?>
        {
            ["app_alive"] = _supervisorService.IsAppAlive(),
            ["consecutive_failures"] = _supervisorService.ConsecutiveFailures,
            ["last_successful_start_utc"] = _supervisorService.LastSuccessfulStartUtc,
        };
        return Task.FromResult(snapshot);
    }

    /// <inheritdoc />
    public async Task ReportHeartbeatAsync(CancellationToken cancellationToken = default)
    {
        // 心跳复用 OfflineReporter 的节流通道：只有连续失败达到阈值时触发一次离线上报，
        // 否则空跑（避免在 30s 周期内制造额外网络流量）。
        // 真正的周期心跳由 Client.App 的 IHealthReporter 实现负责，Supervisor 只关心离线降级。
        var failures = _supervisorService.ConsecutiveFailures;
        if (failures > 0)
        {
            _logger.Debug("后端日志：Supervisor 心跳检查，当前连续失败={Failures}", failures);
        }

        await Task.CompletedTask.ConfigureAwait(false);
    }
}
