using System.Diagnostics;
using Microsoft.Extensions.Hosting;
using Serilog;
using WeCom.PersonalRpa.Core;

namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// 监督主循环（BackgroundService）。
///
/// 职责（设计文档 §3.2 / §C.1）：
///   - 周期（默认 30s）检查 <c>Client.App</c> 进程是否存活；
///   - 进程不存在则按指数退避（初始 2s，上限 5min）拉起 <c>Client.App.exe</c>；
///   - 连续失败达到阈值后：Serilog error + Windows Event Log，并经
///     <see cref="OfflineReporter"/> 上报 client offline；
///   - <b>不操作企业微信 UI</b>（Session 0 隔离）。
/// </summary>
internal sealed class SupervisorService : BackgroundService
{
    private readonly SupervisorOptions _options;
    private readonly OfflineReporter _offlineReporter;
    private readonly WindowsEventLogger _eventLogger;
    private readonly Serilog.ILogger _serilog;

    private int _consecutiveFailures;
    private DateTimeOffset _lastSuccessfulStartUtc = DateTimeOffset.MinValue;

    private const int EventIdStartFailedThreshold = 1002;

    public SupervisorService(
        SupervisorOptions options,
        OfflineReporter offlineReporter,
        WindowsEventLogger eventLogger,
        Serilog.ILogger serilog)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _offlineReporter = offlineReporter ?? throw new ArgumentNullException(nameof(offlineReporter));
        _eventLogger = eventLogger ?? throw new ArgumentNullException(nameof(eventLogger));
        _serilog = serilog ?? throw new ArgumentNullException(nameof(serilog));

        if (string.IsNullOrWhiteSpace(_options.AppExePath))
        {
            throw new InvalidOperationException(
                "Supervisor:AppExePath 未配置；请在 appsettings.json 或环境变量 WECOMRPA_Supervisor__AppExePath 中指定 Client.App 可执行文件路径。");
        }
    }

    /// <summary>供 <see cref="HealthReporter"/> 查询：当前 Client.App 是否存活。</summary>
    public bool IsAppAlive()
    {
        return Process.GetProcessesByName(_options.AppProcessName).Length > 0;
    }

    /// <summary>供 <see cref="HealthReporter"/> 主动触发一次拉起尝试。</summary>
    public async Task<bool> TryStartAsync(CancellationToken cancellationToken)
    {
        return await TryStartAppAsync(cancellationToken).ConfigureAwait(false);
    }

    /// <summary>当前连续拉起失败次数（供健康采集与离线上报诊断读取）。</summary>
    internal int ConsecutiveFailures => _consecutiveFailures;

    /// <summary>上次成功拉起的 UTC 时间（未拉起成功过则为 <see cref="DateTimeOffset.MinValue"/>）。</summary>
    internal DateTimeOffset LastSuccessfulStartUtc => _lastSuccessfulStartUtc;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _serilog.Information("后端日志：SupervisorService 启动。周期={Poll}s, 目标进程={Proc}, 路径={Path}",
            _options.PollIntervalSeconds, _options.AppProcessName, _options.AppExePath);

        // 首轮立即执行一次存活检测，避免冷启动后等一个完整周期。
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                await PollOnceAsync(stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                // 正常关闭路径，吞掉。
            }
            catch (Exception ex)
            {
                // 单轮异常不得拖垮监督循环（类比 backend_dev.md 的 fail-loud 但隔离单次故障）。
                _serilog.Error(ex, "后端日志：监督循环单轮异常，已隔离。");
            }

            try
            {
                await Task.Delay(TimeSpan.FromSeconds(Math.Max(1, _options.PollIntervalSeconds)), stoppingToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
        }

        _serilog.Information("后端日志：SupervisorService 已停止。");
    }

    private async Task PollOnceAsync(CancellationToken cancellationToken)
    {
        if (IsAppAlive())
        {
            // 存活：重置失败计数（恢复后下一次失败从 0 起算）。
            if (_consecutiveFailures > 0)
            {
                _serilog.Information("后端日志：Client.App 已恢复存活，重置失败计数。原计数={Prev}",
                    _consecutiveFailures);
                _consecutiveFailures = 0;
            }

            return;
        }

        // 进程不存在：写一条 Debug 级日志（频繁出现时不刷屏；阈值触达再升 Error）。
        _serilog.Debug("后端日志：Client.App 未检测到存活进程，尝试拉起。");

        var started = await TryStartAppAsync(cancellationToken).ConfigureAwait(false);
        if (!started)
        {
            // 拉起失败：按指数退避等待，避免在毫秒级疯狂 fork。
            var backoff = ComputeBackoff(_consecutiveFailures);
            _serilog.Warning(
                "后端日志：拉起失败，连续失败={Failures}，退避 {BackoffMs}ms 后进入下一轮。",
                _consecutiveFailures, backoff.TotalMilliseconds);

            // 达到阈值：写 EventLog + 上报离线。
            if (_consecutiveFailures >= _options.ConsecutiveFailureThreshold)
            {
                var reason =
                    $"Client.App 在 {_consecutiveFailures} 次连续拉起后仍未存活（脱敏：不含路径/密钥）。";
                _eventLogger.LogError(EventIdStartFailedThreshold,
                    $"WeComPersonalRpa Supervisor: {reason}");
                await _offlineReporter.ReportAsync(reason, _consecutiveFailures, cancellationToken)
                    .ConfigureAwait(false);
            }

            try
            {
                await Task.Delay(backoff, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                // 关闭信号：直接返回，由外层 while 退出。
            }
        }
    }

    private async Task<bool> TryStartAppAsync(CancellationToken cancellationToken)
    {
        try
        {
            var workingDir = !string.IsNullOrWhiteSpace(_options.AppWorkingDirectory)
                ? _options.AppWorkingDirectory
                : Path.GetDirectoryName(_options.AppExePath) ?? AppContext.BaseDirectory;

            var startInfo = new ProcessStartInfo
            {
                FileName = _options.AppExePath,
                WorkingDirectory = workingDir,
                UseShellExecute = false, // Windows Service 上下文必须为 false（Session 0 不允许 Shell）
            };

            if (!string.IsNullOrWhiteSpace(_options.AppArguments))
            {
                startInfo.Arguments = _options.AppArguments;
            }

            _serilog.Information("后端日志：拉起 Client.App。工作目录={Dir}, 参数={Args}",
                workingDir, _options.AppArguments);

            var process = Process.Start(startInfo);
            if (process is null)
            {
                _consecutiveFailures++;
                _serilog.Error("后端日志：Process.Start 返回 null，拉起失败。连续失败={Failures}",
                    _consecutiveFailures);
                return false;
            }

            // 给 OS 一点时间确认进程未立即退出（例如依赖缺失导致秒崩）。
            // 最多等 1s 或进程退出，先到先返。这是真实可观测性的最低代价探针。
            await Task.Delay(TimeSpan.FromMilliseconds(500), cancellationToken).ConfigureAwait(false);

            if (process.HasExited)
            {
                _consecutiveFailures++;
                _serilog.Error(
                    "后端日志：Client.App 启动后立即退出（ExitCode={Code}）。连续失败={Failures}",
                    process.ExitCode, _consecutiveFailures);
                return false;
            }

            // 成功：重置失败计数、记录时间（供未来状态查询与离线上报诊断使用）。
            _consecutiveFailures = 0;
            _lastSuccessfulStartUtc = DateTimeOffset.UtcNow;

            _serilog.Information("后端日志：Client.App 拉起成功，PID={Pid}。", process.Id);
            return true;
        }
        catch (Exception ex)
        {
            _consecutiveFailures++;
            _serilog.Error(ex, "后端日志：拉起 Client.App 抛出异常。连续失败={Failures}",
                _consecutiveFailures);
            return false;
        }
    }

    /// <summary>指数退避：2^k * InitialBackoff，封顶 MaxBackoff。</summary>
    private TimeSpan ComputeBackoff(int failures)
    {
        var raw = (long)_options.InitialBackoffMilliseconds * (1L << Math.Min(failures, 16));
        var capped = Math.Min(raw, _options.MaxBackoffMilliseconds);
        return TimeSpan.FromMilliseconds(Math.Max(_options.InitialBackoffMilliseconds, capped));
    }
}
