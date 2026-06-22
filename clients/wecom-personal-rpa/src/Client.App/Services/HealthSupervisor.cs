using Serilog;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;
using AutomationHealth = WeCom.PersonalRpa.Automation.Contracts.IHealthSupervisor;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 进程内健康监督。
///
/// 周期调用 Automation 层 <see cref="AutomationHealth.CaptureStatus"/>（组合 DesktopState +
/// WeWork 窗口 + 登录态），把产出的 <see cref="StatusPayload"/> 经 <see cref="InboundReporter"/>
/// 上报，并在必要时驱动 <see cref="IStateManager"/> 迁移到 PausedError。
///
/// 跨进程的监督（拉起/重启 App）由 Client.Supervisor（Windows Service / 计划任务）实现，
/// 对应 protocol §C.4 的 IHealthSupervisor；本类为 App 进程内周期自检协调器。
/// </summary>
public sealed class HealthSupervisor
{
    private const string Tag = "HealthSupervisor";

    private readonly IStateManager _state;
    private readonly InboundReporter _reporter;
    private readonly AutomationHealth _capture;
    private readonly ClientSession _session;
    private Timer? _timer;

    public HealthSupervisor(
        IStateManager state,
        InboundReporter reporter,
        AutomationHealth capture,
        ClientSession session)
    {
        _state = state;
        _reporter = reporter;
        _capture = capture;
        _session = session;
    }

    /// <summary>启动周期自检（默认 60 秒）。</summary>
    public Task StartAsync(CancellationToken ct)
    {
        _timer = new Timer(_ => _ = CheckAsync(), null, TimeSpan.Zero, TimeSpan.FromSeconds(60));
        Log.Information("[{Tag}] 启动，周期 60s", Tag);
        return Task.CompletedTask;
    }

    public Task StopAsync(CancellationToken ct)
    {
        _timer?.Change(Timeout.Infinite, Timeout.Infinite);
        _timer?.Dispose();
        Log.Information("[{Tag}] 停止", Tag);
        return Task.CompletedTask;
    }

    /// <summary>执行一次健康检查。</summary>
    public async Task CheckAsync()
    {
        try
        {
            var payload = _capture.CaptureStatus();
            await _reporter.ReportStatusAsync(payload, _session.AccountId);

            // 非健康状态触发暂停（仅 Running 时迁移，避免重复迁移）
            if (IsUnhealthy(payload.Status) && _state.CurrentState == ClientState.Running)
            {
                Log.Warning("[{Tag}] 健康异常 {Status}，迁移到 PausedError", Tag, payload.Status);
                try
                {
                    _state.TransitionTo(ClientState.PausedError,
                        errorCode: payload.Status.ToString().ToLowerInvariant(),
                        errorMessage: payload.Detail);
                }
                catch (Exception ex)
                {
                    Log.Warning(ex, "[{Tag}] 状态迁移失败", Tag);
                }
            }
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 健康检查异常", Tag);
        }
    }

    /// <summary>判定是否为不可运行状态。</summary>
    private static bool IsUnhealthy(AccountStatus s) =>
        s is AccountStatus.Offline
            or AccountStatus.QrExpired
            or AccountStatus.AccountLimited
            or AccountStatus.DesktopLocked
            or AccountStatus.WindowNotVisible;
}
