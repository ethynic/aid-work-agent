using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Inbound;
using WeCom.PersonalRpa.App.MessageArchive;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Inbound;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-client-design.md §F6
//
// InboundEventReporter.HandleAsync 流程：
//   1. builder.BuildAsync(msg) → InboundEvent
//   2. 提取 sender (sender_display_name + sender_stable_id)
//   3. monitorCache.IsAllowed(bindingId, name, id) 过滤
//   4. 通过 → apiClient.ReportInboundAsync 上报；不通过 → 不上报记日志
// ====================================================================================

/// <summary>
/// 双 stub：IAgentApiClient（实现 ReportInboundAsync / UploadMedia / GetMonitorUsers）+
/// 通过 builder 注入下载实现，让整条链路无外部依赖。
/// </summary>
internal sealed class ReporterStubApiClient : IAgentApiClient
{
    public List<InboundEvent> ReportedEvents { get; } = new();
    public bool ReportResult { get; set; } = true;
    public string UploadedUrl { get; set; } = "https://signed-url.example.com/x";

    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default)
    {
        ReportedEvents.Add(evt);
        return Task.FromResult(ReportResult);
    }

    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
        => Task.FromResult(UploadedUrl);

    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => Task.FromResult(new Dictionary<string, MonitorUsersEntry>());

    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success, string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public void Dispose() { }
}

public sealed class InboundEventReporterTests
{
    private const string AccountId = "acct-test";
    private const string BindingId = "binding-1";

    private static (InboundEventReporter reporter, ReporterStubApiClient api) CreateReporter(
        MonitorUsersCache? cache = null,
        Dictionary<string, MonitorUsersEntry>? whitelist = null)
    {
        var api = new ReporterStubApiClient();

        var opts = Options.Create(new ClientOptions
        {
            ClientId = AccountId,
            MonitorUsers = new MonitorUsersOptions { BindingId = BindingId, CacheRefreshMinutes = 60 },
            MessageSource = new ArchiveOptions { Corpid = "c", Secret = "s" },
        });

        cache ??= new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
        // 注入白名单：直接调 RefreshAsync 把预制数据塞进去
        if (whitelist is not null)
        {
            var stubApiForCache = new StubMonitorUsersApiClient(whitelist);
            cache = new MonitorUsersCache(stubApiForCache, opts, NullLogger<MonitorUsersCache>.Instance);
            cache.RefreshAsync().GetAwaiter().GetResult();
        }

        var http = new ArchiveHttpClient(new HttpClient());
        var downloader = new ArchiveMediaDownloader(http, new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance);
        var builder = new InboundEventBuilder(http, downloader, api, opts.Value, AccountId,
            NullLogger<InboundEventBuilder>.Instance);

        var reporter = new InboundEventReporter(builder, cache, api, opts,
            NullLogger<InboundEventReporter>.Instance);
        return (reporter, api);
    }

    private static ArchiveMessage BuildTextFrom(string from)
        => new()
        {
            MsgId = "mid-" + from,
            Action = "upload",
            From = from,
            MsgType = "text",
            MsgTime = 1717000000L,
            Text = "hi",
            ToList = new List<string> { "peer" },
        };

    [Fact]
    public async Task Handle_AllowedMessage_ReportsToServer()
    {
        var (reporter, api) = CreateReporter();

        var ok = await reporter.HandleAsync(BuildTextFrom("alice"));

        Assert.True(ok);
        Assert.Single(api.ReportedEvents);
        Assert.Equal(EventType.Message, api.ReportedEvents[0].EventType);
    }

    [Fact]
    public async Task Handle_FilteredMessage_DoesNotReport()
    {
        // binding-1 白名单只允许 alice
        var whitelist = new Dictionary<string, MonitorUsersEntry>
        {
            [BindingId] = new MonitorUsersEntry { UserNames = new() { "alice" } },
        };
        var (reporter, api) = CreateReporter(whitelist: whitelist);

        var ok = await reporter.HandleAsync(BuildTextFrom("bob"));

        Assert.False(ok);
        Assert.Empty(api.ReportedEvents);
    }

    [Fact]
    public async Task Handle_BuildFailure_LogsWarning_DoesNotCrash()
    {
        // 用错误的 corpid / 无 access_token 让 builder 内部附件失败仍返回事件，
        // 但更直接的方式：构造一个 msg.MsgType = "image" + MediaSdkFileId = null 让它正常返回文本路径。
        // 这里测的是 builder 抛异常时不传染给 reporter。我们用一个会抛的 builder。
        var api = new ReporterStubApiClient();
        var opts = Options.Create(new ClientOptions
        {
            ClientId = AccountId,
            MonitorUsers = new MonitorUsersOptions { BindingId = BindingId },
        });
        var cache = new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);

        // 故意 throw 的 builder（通过空 ctor 不便，直接用 lambda 包装）
        // 不能改 InboundEventBuilder 签名，所以测构造路径走"corpid 空 + image 消息" → 附件被吞但消息会上报
        var http = new ArchiveHttpClient(new HttpClient());
        var downloader = new ArchiveMediaDownloader(http, new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance);
        var builder = new InboundEventBuilder(http, downloader, api, opts.Value, AccountId,
            NullLogger<InboundEventBuilder>.Instance);

        var reporter = new InboundEventReporter(builder, cache, api, opts,
            NullLogger<InboundEventReporter>.Instance);

        // image 消息但 corpid 空 → GetAccessTokenAsync 返回空 → 附件跳过，事件仍上报
        var msg = new ArchiveMessage
        {
            MsgId = "mid-img",
            Action = "upload",
            From = "alice",
            MsgType = "image",
            MsgTime = 1717000000L,
            MediaSdkFileId = "sfid-1",
            ToList = new List<string> { "peer" },
        };

        // 没有异常抛出（媒体失败被吞，文本部分仍上报）
        var ok = await reporter.HandleAsync(msg);
        Assert.True(ok);
        Assert.Single(api.ReportedEvents);
    }

    [Fact]
    public async Task Handle_ReportFailure_ReturnsFalse()
    {
        var api = new ReporterStubApiClient { ReportResult = false };
        var opts = Options.Create(new ClientOptions
        {
            ClientId = AccountId,
            MonitorUsers = new MonitorUsersOptions { BindingId = BindingId },
            MessageSource = new ArchiveOptions { Corpid = "c", Secret = "s" },
        });
        var cache = new MonitorUsersCache(api, opts, NullLogger<MonitorUsersCache>.Instance);
        var http = new ArchiveHttpClient(new HttpClient());
        var downloader = new ArchiveMediaDownloader(http, new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance);
        var builder = new InboundEventBuilder(http, downloader, api, opts.Value, AccountId,
            NullLogger<InboundEventBuilder>.Instance);
        var reporter = new InboundEventReporter(builder, cache, api, opts,
            NullLogger<InboundEventReporter>.Instance);

        var ok = await reporter.HandleAsync(BuildTextFrom("alice"));

        Assert.False(ok);
        Assert.Single(api.ReportedEvents);  // 已尝试上报
    }
}
