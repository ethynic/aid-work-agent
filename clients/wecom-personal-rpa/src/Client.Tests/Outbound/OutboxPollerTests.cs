using System.Net.Http;
using Microsoft.Extensions.Logging.Abstractions;
using WeCom.PersonalRpa.App.Outbound;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.App.Realtime;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Outbound;

/// <summary>
/// OutboxPoller 单元测试。镜像既有 poller 测试模式（QrCodeWatcher/DesktopHealthSupervisor）：
/// 手写 IAgentApiClient 桩 + 假 IReconnectSource + 真 OutboundActionDispatcher（noop PS hook）+ 临时 SQLite。
/// 直接驱动 internal PollOnceAsync / TriggerNowAsync 测试 seam，避免依赖真实 Timer。
/// </summary>
public sealed class OutboxPollerTests : IDisposable
{
    private readonly string _tempDir;
    private readonly string _dbPath;
    private readonly ClientOptions _options;

    public OutboxPollerTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "outboxpoller_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        _dbPath = Path.Combine(_tempDir, "outbox.db");
        _options = new ClientOptions
        {
            ClientId = "test",
            PollIntervalSeconds = 5,
            Outbound = new OutboundOptions { DbPath = _dbPath, MaxRetries = 1 },
        };
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { }
    }

    // 构造 poller + 真实 dispatcher + 假 API + 假重连源
    private (OutboxPoller poller, OutboundQueue queue, FakeOutboxApi api, FakeReconnectSource recon) Create()
    {
        var queue = new OutboundQueue(_options, logger: null);
        var api = new FakeOutboxApi();
        var recon = new FakeReconnectSource();
        var ps = new PowershellOpsInvoker(
            new PowershellOptions { Executable = "powershell.exe", OpsScript = "x.ps1", InvokeTimeoutSeconds = 5 },
            NullLogger<PowershellOpsInvoker>.Instance);
        var dispatcher = new OutboundActionDispatcher(
            queue,
            new AttachmentDownloader(new HttpClient(), _options, logger: null),
            ps,
            api,
            _options,
            new ChannelActionSource(),
            logger: null)
        {
            PsInvokerHook = (_, _, _) => Task.FromResult(new PowershellResult { Success = true }),
        };
        var poller = new OutboxPoller(api, dispatcher, recon, queue, _options, NullLogger<OutboxPoller>.Instance);
        return (poller, queue, api, recon);
    }

    private static ActionEnvelope Env(string rid = "req_1", string name = "Alice")
        => new()
        {
            RequestId = rid,
            Actions = new List<RpaAction> { new SendTextAction { Text = "hi" } },
            ReplyContext = new RpaReplyContext { ConversationSearchName = name },
        };

    [Fact]
    public async Task StartAsync_FiresImmediatePoll()
    {
        var (poller, _, _, _) = Create();
        await poller.StartAsync(default);
        try
        {
            for (var i = 0; i < 30 && poller.PollInvocations == 0; i++) await Task.Delay(20);
            Assert.True(poller.PollInvocations >= 1);
        }
        finally { await poller.StopAsync(default); poller.Dispose(); }
    }

    [Fact]
    public async Task PollOnceAsync_OnSuccess_AdoptsServerInterval()
    {
        var (poller, _, api, _) = Create();
        api.Responses.Enqueue(new OutboxResponse { PollIntervalSeconds = 7 });

        await poller.PollOnceAsync();

        Assert.Equal(TimeSpan.FromSeconds(7), poller.CurrentInterval);
        Assert.Equal(-1, poller.BackoffIndex); // 成功复位退避
    }

    [Fact]
    public async Task PollOnceAsync_OnSuccess_PassesItemsToDispatcher()
    {
        var (poller, queue, api, _) = Create();
        api.Responses.Enqueue(new OutboxResponse
        {
            Items = new List<ActionEnvelope> { Env("req_a"), Env("req_b") },
        });

        await poller.PollOnceAsync();

        var pending = await queue.ListPendingAsync();
        Assert.Equal(2, pending.Count);
        Assert.Contains(pending, x => x.ActionId == "req_a");
        Assert.Contains(pending, x => x.ActionId == "req_b");
    }

    [Fact]
    public async Task TriggerNowAsync_FiresPoll()
    {
        var (poller, _, api, _) = Create();
        api.Responses.Enqueue(new OutboxResponse());
        Assert.Equal(0, poller.PollInvocations);

        await poller.TriggerNowAsync();
        Assert.Equal(1, poller.PollInvocations);
        Assert.Equal(1, api.GetOutboxCalls);
    }

    [Fact]
    public async Task OnReconnected_FiresPoll()
    {
        var (poller, _, api, recon) = Create();
        api.Responses.Enqueue(new OutboxResponse());
        await poller.StartAsync(default);
        try
        {
            recon.RaiseReconnected(); // 模拟 WS 重连
            for (var i = 0; i < 30 && poller.PollInvocations == 0; i++) await Task.Delay(20);
            Assert.True(poller.PollInvocations >= 1);
        }
        finally { await poller.StopAsync(default); poller.Dispose(); }
    }

    [Fact]
    public async Task ConcurrentTrigger_RunsOnce_SkipsReentrant()
    {
        // 慢响应 + 并发触发：semaphore 守卫，第二个触发应被跳过（不重复 GetOutbox）
        var (poller, _, api, _) = Create();
        var gate = new TaskCompletionSource<OutboxResponse>();
        api.PendingGate = gate;
        api.Responses.Enqueue(new OutboxResponse());

        var first = poller.TriggerNowAsync();                 // 占住 semaphore，等 gate
        await Task.Delay(50);                                  // 让第一个进入 GetOutbox 等待
        await poller.TriggerNowAsync();                        // 重入 → semaphore busy → 跳过
        Assert.Equal(1, api.GetOutboxCalls);                   // 只调用一次

        gate.SetResult(new OutboxResponse());                  // 放行第一个
        await first;
        poller.Dispose();
    }

    [Fact]
    public async Task PollOnceAsync_HttpFailure_AdvancesBackoff()
    {
        var (poller, _, api, _) = Create();
        var beforeInterval = poller.CurrentInterval;
        api.ThrowNext = new HttpRequestException("503");

        await poller.PollOnceAsync();

        Assert.True(poller.BackoffIndex >= 0);               // 进入退避
        Assert.Equal(beforeInterval, poller.CurrentInterval); // 正常间隔不变（退避是临时）
    }

    [Fact]
    public async Task PollOnceAsync_BackoffResetsOnFirstSuccess()
    {
        var (poller, _, api, _) = Create();
        api.ThrowNext = new HttpRequestException("x");
        await poller.PollOnceAsync();   // 失败 1
        Assert.Equal(0, poller.BackoffIndex);
        api.ThrowNext = new HttpRequestException("x");
        await poller.PollOnceAsync();   // 失败 2
        Assert.Equal(1, poller.BackoffIndex);

        api.Responses.Enqueue(new OutboxResponse { PollIntervalSeconds = 5 });
        await poller.PollOnceAsync();   // 成功
        Assert.Equal(-1, poller.BackoffIndex); // 成功复位
    }

    [Fact]
    public async Task PollOnceAsync_EmptyItems_DoesNotThrow()
    {
        var (poller, _, api, _) = Create();
        api.Responses.Enqueue(new OutboxResponse { Items = new List<ActionEnvelope>() });
        await poller.PollOnceAsync(); // 不抛
        Assert.Equal(-1, poller.BackoffIndex);
    }

    [Theory]
    [InlineData(0, 2)]
    [InlineData(1, 2)]
    [InlineData(999, 60)]
    [InlineData(5, 5)]
    public async Task StartAsync_ClampsPollInterval(int configured, int expected)
    {
        var opts = new ClientOptions
        {
            ClientId = "c",
            PollIntervalSeconds = configured,
            Outbound = new OutboundOptions { DbPath = _dbPath, MaxRetries = 1 },
        };
        var queue = new OutboundQueue(opts, logger: null);
        var api = new FakeOutboxApi();
        var ps = new PowershellOpsInvoker(new PowershellOptions(), NullLogger<PowershellOpsInvoker>.Instance);
        var dispatcher = new OutboundActionDispatcher(queue,
            new AttachmentDownloader(new HttpClient(), opts, logger: null), ps, api, opts,
            new ChannelActionSource(), logger: null);
        var poller = new OutboxPoller(api, dispatcher, new FakeReconnectSource(), queue, opts, NullLogger<OutboxPoller>.Instance);
        await poller.StartAsync(default);
        try
        {
            Assert.Equal(TimeSpan.FromSeconds(expected), poller.CurrentInterval);
        }
        finally { await poller.StopAsync(default); poller.Dispose(); }
    }

    [Fact]
    public async Task OutboxAvailableViaDispatcher_TriggersPoller()
    {
        // 端到端路由：ServerMessageDispatcher 收到 type=outbox_available → 调 poller.TriggerNowAsync
        var (poller, _, api, _) = Create();
        api.Responses.Enqueue(new OutboxResponse());
        var session = new ClientSession { PauseState = new PauseState() };
        var smd = new ServerMessageDispatcher(null!, session, NullLogger<ServerMessageDispatcher>.Instance,
            outboxPoller: poller);

        var data = System.Text.Encoding.UTF8.GetBytes(
            System.Text.Json.JsonSerializer.Serialize(new { type = "outbox_available", latest_request_id = "r1", pending_count = 1 }));
        await smd.DispatchAsync(data);

        for (var i = 0; i < 30 && poller.PollInvocations == 0; i++) await Task.Delay(20);
        Assert.True(poller.PollInvocations >= 1);
        poller.Dispose();
    }

    // ===== 测试桩 =====

    internal sealed class FakeOutboxApi : IAgentApiClient
    {
        public Queue<OutboxResponse> Responses { get; } = new();
        public Exception? ThrowNext;
        public TaskCompletionSource<OutboxResponse>? PendingGate; // 非空时 GetOutbox 等它（模拟慢响应）
        public int GetOutboxCalls;

        public Task<OutboxResponse> GetOutboxAsync(int limit = 100, CancellationToken cancellationToken = default)
        {
            Interlocked.Increment(ref GetOutboxCalls);
            var throwNext = ThrowNext;
            ThrowNext = null;
            if (throwNext is not null) return Task.FromException<OutboxResponse>(throwNext);

            var resp = Responses.Count > 0 ? Responses.Dequeue() : new OutboxResponse();
            if (PendingGate is not null)
            {
                var localGate = PendingGate;
                PendingGate = null;
                return localGate.Task.ContinueWith(_ => resp, TaskContinuationOptions.ExecuteSynchronously);
            }
            return Task.FromResult(resp);
        }

        public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<bool> ReportActionResultAsync(string requestId, bool success, string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default) => Task.FromResult(true);
        public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public void Dispose() { }
    }

    internal sealed class FakeReconnectSource : IReconnectSource
    {
        public event EventHandler<EventArgs>? Reconnected;
        public void RaiseReconnected() => Reconnected?.Invoke(this, EventArgs.Empty);
    }
}
