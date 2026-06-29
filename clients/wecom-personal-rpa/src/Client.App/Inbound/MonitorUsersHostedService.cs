using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.Inbound;

/// <summary>
/// <see cref="MonitorUsersCache"/> 的宿主启动钩子（Phase 4 测试阶段补齐跨块协调缺口）。
///
/// 背景：块 E 的 MonitorUsersCache 提供 <see cref="MonitorUsersCache.InitializeAsync"/>
/// 用以在启动时拉一次白名单，但此前没有调用方。不补这个缺口会导致白名单缓存启动后一直为空，
/// <see cref="MonitorUsersCache.IsAllowed"/> 一路放行，白名单预过滤实际不生效。
///
/// 设计：
///   - 包装为 <see cref="IHostedService"/>，由 App.xaml.cs 的 AddHostedService 随 Host 启停。
///   - StartAsync 调 InitializeAsync；StopAsync 停止 Timer。
///   - P0-2：加周期 Timer，按 MonitorUsersOptions.CacheRefreshMinutes（默认 60 分钟）周期调
///     RefreshAsync。失败不传染（RefreshSafeAsync 兜底 catch + log warning）。
///   - 初始化失败不抛（InitializeAsync 内部已 catch）。
/// </summary>
internal sealed class MonitorUsersHostedService : IHostedService, IDisposable
{
    private const string Tag = "MonitorUsersHostedService";

    private readonly MonitorUsersCache _cache;
    private readonly ILogger<MonitorUsersHostedService> _logger;
    private readonly TimeSpan _refreshInterval;
    private Timer? _refreshTimer;

    public MonitorUsersHostedService(
        MonitorUsersCache cache,
        IOptions<ClientOptions> options,
        ILogger<MonitorUsersHostedService> logger)
    {
        _cache = cache ?? throw new ArgumentNullException(nameof(cache));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        var mins = options?.Value?.MonitorUsers?.CacheRefreshMinutes ?? 60;
        if (mins <= 0) mins = 60;
        _refreshInterval = TimeSpan.FromMinutes(mins);
    }

    /// <inheritdoc />
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        try
        {
            await _cache.InitializeAsync(cancellationToken).ConfigureAwait(false);
            _logger.LogInformation("[{Tag}] MonitorUsersCache 初始化完成", Tag);
        }
        catch (Exception ex)
        {
            // InitializeAsync 内部已 catch，这里兜底防止 Host 启动失败
            _logger.LogWarning(ex, "[{Tag}] MonitorUsersCache 初始化失败，将使用空缓存（监控所有）", Tag);
        }

        // P0-2：周期刷新 Timer。dueTime=_refreshInterval（首次刷新发生在初始化后的一个周期），
        // period=_refreshInterval。Timer 回调包装为 async void → RefreshSafeAsync 兜底 catch。
        _refreshTimer = new Timer(
            _ => _ = RefreshSafeAsync(),
            state: null,
            dueTime: _refreshInterval,
            period: _refreshInterval);
    }

    /// <summary>Timer 回调包装：刷新缓存 + 捕获异常（不让异常打进 ThreadPool 终结进程）。</summary>
    private async Task RefreshSafeAsync()
    {
        try
        {
            await _cache.RefreshAsync().ConfigureAwait(false);
            _logger.LogDebug("[{Tag}] MonitorUsersCache 周期刷新成功", Tag);
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "[{Tag}] MonitorUsersCache 周期刷新失败（保留旧缓存）", Tag);
        }
    }

    /// <inheritdoc />
    public Task StopAsync(CancellationToken cancellationToken)
    {
        _refreshTimer?.Change(Timeout.Infinite, 0);
        return Task.CompletedTask;
    }

    public void Dispose()
    {
        _refreshTimer?.Dispose();
    }
}
