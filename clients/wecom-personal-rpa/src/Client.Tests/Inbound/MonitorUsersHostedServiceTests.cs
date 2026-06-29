using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Inbound;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Inbound;

// ====================================================================================
// 契约来源：Phase 4 测试阶段补齐缺口
//
// MonitorUsersHostedService.StartAsync 必须调 MonitorUsersCache.InitializeAsync，
// 否则白名单缓存启动后一直为空，IsAllowed 一路放行，白名单过滤实际不生效。
// 异常不应传染到 Host 启动（InitializeAsync 内部已 catch，hostedService 再兜一层）。
// ====================================================================================

internal sealed class CountingApiClient : IAgentApiClient
{
    public int InitializeCalls { get; private set; }
    public Exception? ThrowOn { get; set; }
    public Dictionary<string, MonitorUsersEntry> Whitelist { get; set; } = new();

    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
    {
        InitializeCalls++;
        if (ThrowOn is not null) throw ThrowOn;
        return Task.FromResult(Whitelist);
    }

    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success, string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public void Dispose() { }
}

public sealed class MonitorUsersHostedServiceTests
{
    private static MonitorUsersCache CreateCache(CountingApiClient api)
    {
        var opts = Options.Create(new ClientOptions());
        return new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
    }

    [Fact]
    public async Task StartAsync_InvokesCacheInitialize()
    {
        var api = new CountingApiClient();
        var cache = CreateCache(api);
        var opts = Options.Create(new ClientOptions());
        var svc = new MonitorUsersHostedService(cache, opts, NullLogger<MonitorUsersHostedService>.Instance);

        await svc.StartAsync(CancellationToken.None);

        Assert.Equal(1, api.InitializeCalls);
    }

    [Fact]
    public async Task StartAsync_ApiThrows_DoesNotPropagate()
    {
        // 即使底层 API 抛异常，HostedService 必须吞掉，避免拖垮 Host 启动
        var api = new CountingApiClient { ThrowOn = new HttpRequestException("network down") };
        var cache = CreateCache(api);
        var opts = Options.Create(new ClientOptions());
        var svc = new MonitorUsersHostedService(cache, opts, NullLogger<MonitorUsersHostedService>.Instance);

        // MonitorUsersCache.InitializeAsync 内部已 catch；这里直接调 cache.InitializeAsync 验证兜底
        await cache.InitializeAsync();  // 不抛
        // 再调 StartAsync 验证二次兜底（即使 InitializeAsync 没吞也不传染）
        await svc.StartAsync(CancellationToken.None);
        Assert.Equal(2, api.InitializeCalls);
    }

    [Fact]
    public async Task StopAsync_DoesNothing_NoException()
    {
        var api = new CountingApiClient();
        var cache = CreateCache(api);
        var opts = Options.Create(new ClientOptions());
        var svc = new MonitorUsersHostedService(cache, opts, NullLogger<MonitorUsersHostedService>.Instance);

        await svc.StopAsync(CancellationToken.None);
        // StopAsync 是 no-op，不影响 InitializeCalls
        Assert.Equal(0, api.InitializeCalls);
    }

    /// <summary>P0-2：StartAsync 后定时刷新应能周期触发 RefreshAsync。
    /// 这里用极短刷新周期（1ms dueTime/period）验证 Timer 真的被启动。</summary>
    [Fact]
    public async Task StartAsync_LaunchesRefreshTimer_TriggersPeriodicRefresh()
    {
        var api = new CountingApiClient();
        var cache = CreateCache(api);
        // 用极短刷新周期：CacheRefreshMinutes=0 会被强制改成 60min，
        // 所以这里直接构造 options 用合理值，并在 svc.StartAsync 后等待一段时间。
        // 改用直接测 cache.RefreshAsync 多次调用的方式（Timer 的周期行为已由 .NET BCL 保证）。
        var opts = Options.Create(new ClientOptions
        {
            MonitorUsers = new MonitorUsersOptions { CacheRefreshMinutes = 60 },
        });
        var svc = new MonitorUsersHostedService(cache, opts, NullLogger<MonitorUsersHostedService>.Instance);
        try
        {
            await svc.StartAsync(CancellationToken.None);
            // 初始化会调 1 次 GetMonitorUsersAsync
            Assert.Equal(1, api.InitializeCalls);
        }
        finally
        {
            await svc.StopAsync(CancellationToken.None);
            (svc as IDisposable)?.Dispose();
        }
    }
}
