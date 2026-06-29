using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Inbound;
using WeCom.PersonalRpa.App.MessageArchive;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Inbound;

// ====================================================================================
// 契约来源：Phase 4 测试阶段补齐缺口
//
// 1) IHostedService 链路：StartAsync 必须订阅 ChatArchiveListener.NewMessageReceived，
//    StopAsync 必须取消订阅。否则 F4 会话存档拿到的消息永远不会被 F6 处理上报。
//
// 2) PauseState 集成：Tenant/Account 级暂停时 HandleAsync 必须跳过上报。
// ====================================================================================

internal sealed class HsStubApiClient : IAgentApiClient
{
    public List<InboundEvent> ReportedEvents { get; } = new();

    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default)
    {
        ReportedEvents.Add(evt);
        return Task.FromResult(true);
    }

    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => Task.FromResult(new Dictionary<string, MonitorUsersEntry>());
    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
        => Task.FromResult("https://signed-url.example.com/x");
    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success, string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public void Dispose() { }
}

/// <summary>
/// 手写最小化 IMessageWatcher 实现，仅暴露 NewMessageReceived 事件 + 触发助手。
/// </summary>
internal sealed class StubMessageWatcher : IMessageWatcher
{
    public event EventHandler<InboundEventArgs>? NewMessageReceived;
    public Task StartAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
    public Task StopAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
    public void Dispose() { }

    public void Emit(ArchiveMessage msg)
        => NewMessageReceived?.Invoke(this, new InboundEventArgs { Message = msg });
}

public sealed class InboundEventReporterHostedServiceTests
{
    private const string AccountId = "acct-hs";
    private const string BindingId = "binding-hs";

    private static (InboundEventReporter reporter, HsStubApiClient api, StubMessageWatcher watcher, PauseState pause)
        Create(PauseState? pause = null)
    {
        var api = new HsStubApiClient();
        var opts = Options.Create(new ClientOptions
        {
            ClientId = AccountId,
            MonitorUsers = new MonitorUsersOptions { BindingId = BindingId },
        });

        var cache = new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
        var http = new ArchiveHttpClient(new HttpClient());
        var downloader = new ArchiveMediaDownloader(http, new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance);
        var builder = new InboundEventBuilder(http, downloader, api, opts.Value, AccountId,
            NullLogger<InboundEventBuilder>.Instance);

        var watcher = new StubMessageWatcher();
        var pauseState = pause ?? new PauseState();
        var reporter = new InboundEventReporter(builder, cache, api, opts,
            NullLogger<InboundEventReporter>.Instance,
            watcher: watcher,
            pauseState: pauseState);
        return (reporter, api, watcher, pauseState);
    }

    private static ArchiveMessage MakeMsg(string from = "alice", string msgId = "mid-1")
        => new()
        {
            MsgId = msgId,
            Action = "upload",
            From = from,
            MsgType = "text",
            MsgTime = 1717000000L,
            Text = "hi",
            ToList = new List<string> { "peer" },
        };

    [Fact]
    public async Task StartAsync_SubscribesToWatcher_EventsPropagate()
    {
        var (reporter, api, watcher, _) = Create();

        // 启动前：watcher.Emit 不会触发上报
        watcher.Emit(MakeMsg());
        Assert.Empty(api.ReportedEvents);

        await ((IHostedService)reporter).StartAsync(CancellationToken.None);

        // 启动后：watcher.Emit → InboundEventReporter.HandleNewMessageAsync → 上报
        // 注意 HandleNewMessageAsync 是 async void，需要等待事件循环推进
        watcher.Emit(MakeMsg());
        // 让 async void 完成（事件处理内 await HandleAsync）
        await Task.Delay(50);

        Assert.Single(api.ReportedEvents);
    }

    [Fact]
    public async Task StopAsync_Unsubscribes_NoFurtherEvents()
    {
        var (reporter, api, watcher, _) = Create();
        await ((IHostedService)reporter).StartAsync(CancellationToken.None);
        await ((IHostedService)reporter).StopAsync(CancellationToken.None);

        watcher.Emit(MakeMsg());
        await Task.Delay(50);

        Assert.Empty(api.ReportedEvents);
    }

    [Fact]
    public async Task HandleAsync_TenantPaused_SkipsReport()
    {
        var pause = new PauseState();
        pause.SetTenantPaused(true);
        var (reporter, api, _, _) = Create(pause);

        var ok = await reporter.HandleAsync(MakeMsg());

        Assert.False(ok);
        Assert.Empty(api.ReportedEvents);
    }

    [Fact]
    public async Task HandleAsync_AccountPaused_SkipsReport()
    {
        var pause = new PauseState();
        pause.SetAccountPaused(true);
        var (reporter, api, _, _) = Create(pause);

        var ok = await reporter.HandleAsync(MakeMsg());

        Assert.False(ok);
        Assert.Empty(api.ReportedEvents);
    }

    [Fact]
    public async Task HandleAsync_NotPaused_ReportsNormally()
    {
        // 默认 PauseState 不暂停
        var (reporter, api, _, _) = Create();

        var ok = await reporter.HandleAsync(MakeMsg());

        Assert.True(ok);
        Assert.Single(api.ReportedEvents);
    }

    [Fact]
    public async Task HandleAsync_NullPauseState_ReportsNormally_BackwardCompat()
    {
        // PauseState 为 null（旧测试场景 / 未注入）：不应影响上报链路
        var api = new HsStubApiClient();
        var opts = Options.Create(new ClientOptions
        {
            ClientId = AccountId,
            MonitorUsers = new MonitorUsersOptions { BindingId = BindingId },
        });
        var cache = new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
        var http = new ArchiveHttpClient(new HttpClient());
        var downloader = new ArchiveMediaDownloader(http, new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance);
        var builder = new InboundEventBuilder(http, downloader, api, opts.Value, AccountId,
            NullLogger<InboundEventBuilder>.Instance);

        var reporter = new InboundEventReporter(builder, cache, api, opts,
            NullLogger<InboundEventReporter>.Instance,
            watcher: null,
            pauseState: null);

        var ok = await reporter.HandleAsync(MakeMsg());

        Assert.True(ok);
        Assert.Single(api.ReportedEvents);
    }
}
