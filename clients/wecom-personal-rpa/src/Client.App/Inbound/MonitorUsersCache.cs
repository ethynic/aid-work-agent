using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Inbound;

/// <summary>
/// 绑定级监控白名单本地缓存（Phase 4 块 E）。
///
/// 角色定位：监听器 → InboundEventReporter → [本类 IsAllowed] → IAgentApiClient.ReportInboundAsync。
/// 设计见 docs/system/wecom-personal-rpa-client-design.md §F6。
///
/// 三级白名单策略（与服务端 _process_inbound_message 对齐）：
///   1. bindingId 不在缓存里 → 允许（视为监控所有，兼容未下发场景）
///   2. entry.user_names + entry.user_ids 都为空 → 允许（白名单为空 = 监控所有）
///   3. 任一字段命中 → 允许；都不命中 → 拒绝
///
/// 缓存策略：
///   - 启动时 InitializeAsync 拉一次
///   - 每 CacheRefreshMinutes（默认 60 分钟）后台刷新一次
///   - 服务端 config_invalidate 推送时强制 RefreshAsync
///   - 服务端二次校验兜底（防止客户端缓存过期窗口被钻）
/// </summary>
public sealed class MonitorUsersCache
{
    private const string Tag = "MonitorUsersCache";

    private readonly IAgentApiClient _apiClient;
    private readonly ILogger<MonitorUsersCache>? _logger;
    private readonly TimeSpan _refreshInterval;

    /// <summary>缓存条目。空字典（默认）等价于"所有 binding 都监控所有"。</summary>
    private volatile Dictionary<string, MonitorUsersEntry> _cache = new(0);

    /// <summary>
    /// 缓存过期时间的 ticks（UTC）。用 long 包装便于 volatile，避免 DateTimeOffset 不能作为 volatile 字段。
    /// </summary>
    private long _cacheExpiryTicks = DateTimeOffset.MinValue.UtcTicks;

    private readonly SemaphoreSlim _lock = new(1, 1);

    public MonitorUsersCache(
        IAgentApiClient apiClient,
        IOptions<ClientOptions> options,
        ILogger<MonitorUsersCache>? logger = null)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _logger = logger;
        var mins = options?.Value?.MonitorUsers?.CacheRefreshMinutes ?? 60;
        if (mins <= 0) mins = 60;
        _refreshInterval = TimeSpan.FromMinutes(mins);
    }

    /// <summary>启动时拉一次白名单。失败不抛异常（默认空缓存 = 监控所有）。</summary>
    public async Task InitializeAsync(CancellationToken ct = default)
    {
        try
        {
            await RefreshAsync(ct).ConfigureAwait(false);
            _logger?.LogInformation("[{Tag}] 初始化完成，{Count} 个 binding 配了白名单", Tag, _cache.Count);
        }
        catch (Exception ex)
        {
            // 初始化失败不影响消息上报链路（默认放行，由服务端二次校验兜底）
            _logger?.LogWarning(ex, "[{Tag}] 初始化拉取白名单失败，按空缓存处理（监控所有）", Tag);
        }
    }

    /// <summary>强制刷新（服务端 config_invalidate 推送时调）。</summary>
    public async Task RefreshAsync(CancellationToken ct = default)
    {
        await _lock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            var fresh = await _apiClient.GetMonitorUsersAsync(ct).ConfigureAwait(false);
            _cache = fresh ?? new Dictionary<string, MonitorUsersEntry>(0);
            _cacheExpiryTicks = (DateTimeOffset.UtcNow + _refreshInterval).UtcTicks;
            _logger?.LogDebug("[{Tag}] 白名单刷新成功，{Count} 个 binding", Tag, _cache.Count);
        }
        finally
        {
            _lock.Release();
        }
    }

    /// <summary>
    /// 缓存是否已过期。供后台刷新任务判断是否该主动拉新。
    /// </summary>
    public bool IsExpired => DateTimeOffset.UtcNow.UtcTicks >= _cacheExpiryTicks;

    /// <summary>
    /// 检查消息是否被白名单允许。
    /// - bindingId 不在缓存里（或缓存里 user_names+user_ids 都空）→ 允许（监控所有）
    /// - 否则按"任一字段匹配"过滤
    ///
    /// 注：本方法只做客户端预过滤；服务端会再做二次校验，所以"允许"不等价于"最终入库"。
    /// </summary>
    public bool IsAllowed(string bindingId, string? senderName, string? senderId)
    {
        if (string.IsNullOrEmpty(bindingId)) return true;

        if (!_cache.TryGetValue(bindingId, out var entry) || entry is null)
        {
            // binding 未下发白名单 → 监控所有
            return true;
        }

        bool namesConfigured = entry.UserNames is { Count: > 0 };
        bool idsConfigured = entry.UserIds is { Count: > 0 };
        if (!namesConfigured && !idsConfigured)
        {
            // 白名单为空 = 监控所有
            return true;
        }

        if (namesConfigured && !string.IsNullOrEmpty(senderName) && entry.UserNames!.Contains(senderName))
        {
            return true;
        }
        if (idsConfigured && !string.IsNullOrEmpty(senderId) && entry.UserIds!.Contains(senderId))
        {
            return true;
        }

        return false;
    }
}
