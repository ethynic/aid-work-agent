using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Inbound;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Inbound;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-client-design.md §F6
//
// 三级白名单策略（与服务端 _process_inbound_message 对齐）：
//   1. bindingId 不在缓存里 → 允许（视为监控所有，兼容未下发场景）
//   2. entry.user_names + entry.user_ids 都为空 → 允许（白名单为空 = 监控所有）
//   3. 任一字段命中 → 允许；都不命中 → 拒绝
// ====================================================================================

/// <summary>
/// 手写 IAgentApiClient stub：返回可编程的白名单字典，不引入 NSubstitute/Moq。
/// </summary>
internal sealed class StubMonitorUsersApiClient : IAgentApiClient
{
    private readonly Dictionary<string, MonitorUsersEntry> _data;

    public StubMonitorUsersApiClient(Dictionary<string, MonitorUsersEntry> data) => _data = data;

    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => Task.FromResult(_data);

    // 以下方法本测试不关注
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

/// <summary>
/// MonitorUsersCache 单元测试。
/// </summary>
public sealed class MonitorUsersCacheTests
{
    private static MonitorUsersCache CreateCache(Dictionary<string, MonitorUsersEntry> data)
    {
        var api = new StubMonitorUsersApiClient(data);
        var opts = Options.Create(new ClientOptions());
        var cache = new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
        // 同步预填缓存（IsAllowed 是同步方法，测试不能依赖异步 Initialize）
        cache.RefreshAsync().GetAwaiter().GetResult();
        return cache;
    }

    [Fact]
    public void IsAllowed_BindingNotInCache_ReturnsTrue()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>());
        Assert.True(cache.IsAllowed("binding-A", "alice", "uid-alice"));
    }

    [Fact]
    public void IsAllowed_BothFieldsEmpty_ReturnsTrue()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry(),
        });
        Assert.True(cache.IsAllowed("binding-A", "alice", "uid-alice"));
    }

    [Fact]
    public async Task RefreshAsync_UpdatesCache()
    {
        var api = new StubMonitorUsersApiClient(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry { UserNames = new() { "alice" } },
        });
        var cache = new MonitorUsersCache(api, Options.Create(new ClientOptions()), NullLogger<MonitorUsersCache>.Instance);

        // 初始为空：放行（监控所有）
        Assert.True(cache.IsAllowed("binding-A", "bob", "uid-bob"));

        await cache.RefreshAsync();
        // 拉新后：alice 在白名单，bob 不在 → 拒绝
        Assert.False(cache.IsAllowed("binding-A", "bob", "uid-bob"));
        Assert.True(cache.IsAllowed("binding-A", "alice", "uid-alice"));
    }

    [Fact]
    public void IsAllowed_NameMatches_ReturnsTrue()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry { UserNames = new() { "alice", "bob" } },
        });
        Assert.True(cache.IsAllowed("binding-A", "bob", "uid-bob"));
    }

    [Fact]
    public void IsAllowed_IdMatches_ReturnsTrue()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry { UserIds = new() { "uid-alice" } },
        });
        Assert.True(cache.IsAllowed("binding-A", "alice", "uid-alice"));
    }

    [Fact]
    public void IsAllowed_NoMatch_ReturnsFalse()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry
            {
                UserNames = new() { "alice" },
                UserIds = new() { "uid-alice" },
            },
        });
        Assert.False(cache.IsAllowed("binding-A", "bob", "uid-bob"));
    }

    [Fact]
    public void IsAllowed_EmptyBindingId_ReturnsTrue()
    {
        var cache = CreateCache(new Dictionary<string, MonitorUsersEntry>
        {
            ["binding-A"] = new MonitorUsersEntry { UserNames = new() { "alice" } },
        });
        // 空 bindingId 视为监控所有
        Assert.True(cache.IsAllowed("", "bob", "uid-bob"));
        Assert.True(cache.IsAllowed(null!, "bob", "uid-bob"));
    }
}
