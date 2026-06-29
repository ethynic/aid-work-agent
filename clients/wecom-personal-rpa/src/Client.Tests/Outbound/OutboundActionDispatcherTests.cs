using System.Net;
using System.Net.Http;
using System.Net.WebSockets;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using WeCom.PersonalRpa.App.Outbound;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;
using Xunit.Abstractions;

namespace WeCom.PersonalRpa.Tests.Outbound;

/// <summary>
/// OutboundActionDispatcher + OutboundQueue 单元测试。
///
/// 契约来源：docs/system/wecom-personal-rpa-client-design.md §F3 + plan-wecom-personal-rpa-client.md §3.1.2。
/// 验证目标：
///   - OutboundQueue FIFO + 失败重试计数
///   - Dispatcher send_text 调 PS + 回执 success
///   - Dispatcher send_image 下载 → 调 PS → 删本地副本
///   - PS 失败错误码透传（+ wecom_navigation_failed → unsupported_action 映射）
///   - wecom_window_not_found 重试 MaxRetries 次
///
/// 用真实 SQLite（不是 mock）+ 临时目录，每个测试独立 DB 文件，互不干扰。
/// PowershellOpsInvoker 是 sealed + 依赖 Process，无法直接 mock，故通过 Dispatcher.PsInvokerHook 注入桩函数。
/// </summary>
public sealed class OutboundActionDispatcherTests : IDisposable
{
    private readonly ITestOutputHelper _out;
    private readonly string _tempDir;
    private readonly string _dbPath;
    private readonly ClientOptions _options;

    public OutboundActionDispatcherTests(ITestOutputHelper outHelper)
    {
        _out = outHelper;
        _tempDir = Path.Combine(Path.GetTempPath(), "outbound_test_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        _dbPath = Path.Combine(_tempDir, "outbox.db");
        _options = new ClientOptions
        {
            ClientId = "test_client",
            Outbound = new OutboundOptions
            {
                DbPath = _dbPath,
                DownloadTempDir = Path.Combine(_tempDir, "downloads"),
                MaxAttachmentSizeMb = 10,
                MaxRetries = 3,
            },
        };
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { }
    }

    // ===== OutboundQueue 测试 =====

    [Fact]
    public async Task OutboundQueue_EnqueueThenDequeue_ReturnsFifo()
    {
        var q = new OutboundQueue(_options, logger: null);
        var t0 = DateTimeOffset.UtcNow;
        await Task.Delay(10);
        var t1 = DateTimeOffset.UtcNow;

        await q.EnqueueAsync(new OutboxItem
        {
            ActionId = "req_a",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "conv_a",
            Text = "first",
            CreatedAt = t0,
        });
        await q.EnqueueAsync(new OutboxItem
        {
            ActionId = "req_b",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "conv_b",
            Text = "second",
            CreatedAt = t1,
        });

        var first = await q.DequeueNextAsync();
        var second = await q.DequeueNextAsync();
        var third = await q.DequeueNextAsync();

        Assert.NotNull(first);
        Assert.NotNull(second);
        Assert.Null(third);
        Assert.Equal("req_a", first!.ActionId);
        Assert.Equal("req_b", second!.ActionId);
        Assert.Equal("running", first.Status);
        Assert.Equal("running", second.Status);
    }

    [Fact]
    public async Task OutboundQueue_MarkFailed_IncrementsRetryCount()
    {
        var q = new OutboundQueue(_options, logger: null);
        await q.EnqueueAsync(new OutboxItem
        {
            ActionId = "req_x",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "conv",
            Text = "t",
            CreatedAt = DateTimeOffset.UtcNow,
        });

        var claimed = await q.DequeueNextAsync();
        Assert.NotNull(claimed);

        await q.MarkFailedAsync("req_x", "wecom_window_not_found", "找不到窗口");

        // MarkFailedAsync 把 status 置为 'failed'，ListPendingAsync 只返回 'pending'，
        // 故这里直接读 SQLite 表行（白盒，路径来自 _options.Outbound.DbPath）。
        using (var conn = new SqliteConnection($"Data Source={_dbPath}"))
        {
            conn.Open();
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT status, retry_count, error_code FROM outbox_local WHERE action_id='req_x';";
            using var reader = cmd.ExecuteReader();
            Assert.True(reader.Read());
            Assert.Equal("failed", reader.GetString(0));
            Assert.Equal(1L, reader.GetInt64(1));
            Assert.Equal("wecom_window_not_found", reader.GetString(2));
        }
    }

    [Fact]
    public async Task OutboundQueue_DedupActionId_ReturnsFalseOnDuplicate()
    {
        var q = new OutboundQueue(_options, logger: null);
        var item = new OutboxItem
        {
            ActionId = "req_dup",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "c",
            Text = "t",
            CreatedAt = DateTimeOffset.UtcNow,
        };

        var first = await q.EnqueueAsync(item);
        var second = await q.EnqueueAsync(item);
        Assert.True(first);
        Assert.False(second);
    }

    [Fact]
    public async Task OutboundQueue_ListPendingAsync_RecoversHangingRunning()
    {
        var q = new OutboundQueue(_options, logger: null);
        await q.EnqueueAsync(new OutboxItem
        {
            ActionId = "req_h",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "c",
            Text = "t",
            CreatedAt = DateTimeOffset.UtcNow,
        });
        // 模拟"已被领取但未结束"的状态
        var claimed = await q.DequeueNextAsync();
        Assert.NotNull(claimed);
        Assert.Equal("running", claimed!.Status);

        // 假装进程崩溃重启 → ListPendingAsync 应把 running 重置为 pending 并返回
        var pending = await q.ListPendingAsync();
        var recovered = Assert.Single(pending);
        Assert.Equal("req_h", recovered.ActionId);
        Assert.Equal("pending", recovered.Status);
    }

    // ===== OutboundActionDispatcher 测试 =====

    private sealed class StubApi : IAgentApiClient
    {
        public List<(string Id, bool Success, string? Code, string? Msg)> Reports { get; } = new();
        public Task<bool> ReportActionResultAsync(string requestId, bool success,
            string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default)
        {
            Reports.Add((requestId, success, errorCode, errorMessage));
            return Task.FromResult(true);
        }
        // 以下方法本测试不关心，留 NotUsed 抛异常确保不被调用
        public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default)
            => throw new NotImplementedException();
        public void Dispose() { }
    }

    private OutboundActionDispatcher CreateDispatcher(
        IAgentApiClient api,
        AttachmentDownloader downloader,
        Func<string, object?, CancellationToken, Task<PowershellResult>> psHook,
        WeCom.PersonalRpa.Core.StateMachine.PauseState? pauseState = null,
        OutboundQueue? queueOverride = null)
    {
        // PowershellOpsInvoker sealed 类无法 mock，构造一个真实实例用于编译时占位（dispatcher 测试用 hook 覆盖）。
        var psOptions = new PowershellOptions
        {
            Executable = "powershell.exe",
            OpsScript = "scripts/wecom-ops.ps1",
            InvokeTimeoutSeconds = 5,
        };
        var ps = new PowershellOpsInvoker(psOptions, NullLogger<PowershellOpsInvoker>.Instance);
        var dispatcher = new OutboundActionDispatcher(
            queueOverride ?? new OutboundQueue(_options, logger: null),
            downloader,
            ps,
            api,
            _options,
            new ChannelActionSource(),
            logger: null,
            pauseState: pauseState)
        {
            PsInvokerHook = psHook,
        };
        return dispatcher;
    }

    private sealed class FakeHttpHandler : HttpMessageHandler
    {
        private readonly byte[] _body;
        private readonly string _contentType;
        public FakeHttpHandler(byte[] body, string contentType)
        {
            _body = body; _contentType = contentType;
        }
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var resp = new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(_body),
            };
            resp.Content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(_contentType);
            resp.Content.Headers.ContentLength = _body.Length;
            return Task.FromResult(resp);
        }
    }

    [Fact]
    public async Task OutboundActionDispatcher_SendText_CallsPsAndReportsSuccess()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);

        string? receivedAction = null;
        object? receivedParams = null;
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
        {
            receivedAction = action;
            receivedParams = p;
            return Task.FromResult(new PowershellResult { Success = true, Action = action });
        });

        var item = new OutboxItem
        {
            ActionId = "req_t",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "Alice",
            Text = "hello",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        Assert.Equal("send_text", receivedAction);
        Assert.NotNull(receivedParams);
        var report = Assert.Single(api.Reports);
        Assert.Equal("req_t", report.Id);
        Assert.True(report.Success);
        Assert.Null(report.Code);
    }

    [Fact]
    public async Task OutboundActionDispatcher_SendImage_DownloadsThenCallsPsThenDeletesLocal()
    {
        var api = new StubApi();
        // 准备假图片字节 + 对应 HTTP handler
        var pngBytes = new byte[] { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };
        var http = new HttpClient(new FakeHttpHandler(pngBytes, "image/png"));
        var downloader = new AttachmentDownloader(http, _options, logger: null);

        string? receivedAction = null;
        string? receivedPath = null;
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
        {
            receivedAction = action;
            if (p is Dictionary<string, object?> dict)
            {
                receivedPath = dict.TryGetValue("image_path", out var v) ? v as string : null;
            }
            return Task.FromResult(new PowershellResult { Success = true, Action = action });
        });

        var item = new OutboxItem
        {
            ActionId = "req_img",
            ActionType = ActionTypeNames.SendImage,
            ConversationKey = "Bob",
            FileUrl = "https://example.com/avatar.png",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        Assert.Equal("send_image", receivedAction);
        Assert.NotNull(receivedPath);
        Assert.True(File.Exists(receivedPath!) == false,
            "下载副本应在调用后删除");
        var report = Assert.Single(api.Reports);
        Assert.True(report.Success);
    }

    [Fact]
    public async Task OutboundActionDispatcher_PsFailure_ReportsFailureWithErrorCode()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
            Task.FromResult(new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "wecom_navigation_failed",
                ErrorMessage = "导航失败",
            }));

        var item = new OutboxItem
        {
            ActionId = "req_fail",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "X",
            Text = "hi",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        var report = Assert.Single(api.Reports);
        Assert.False(report.Success);
        // wecom_navigation_failed → unsupported_action 映射（对齐 protocol.md §A.8）
        Assert.Equal("unsupported_action", report.Code);
    }

    [Fact]
    public async Task OutboundActionDispatcher_RetriesOnWecomWindowNotFound()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);

        var callCount = 0;
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
        {
            callCount++;
            // 始终失败 wecom_window_not_found，验证重试 MaxRetries 次
            return Task.FromResult(new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "wecom_window_not_found",
                ErrorMessage = "窗口未找到",
            });
        });

        var item = new OutboxItem
        {
            ActionId = "req_retry",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "Y",
            Text = "yo",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        // MaxRetries=3，应调用 3 次
        Assert.Equal(3, callCount);
        var report = Assert.Single(api.Reports);
        Assert.False(report.Success);
        Assert.Equal("wecom_window_not_found", report.Code);
    }

    [Fact]
    public async Task OutboundActionDispatcher_Noop_SkipsPsAndReportsSuccess()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);
        var psCalled = false;
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
        {
            psCalled = true;
            return Task.FromResult(new PowershellResult { Success = true, Action = action });
        });

        var item = new OutboxItem
        {
            ActionId = "req_noop",
            ActionType = ActionTypeNames.Noop,
            ConversationKey = "Z",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        Assert.False(psCalled);
        var report = Assert.Single(api.Reports);
        Assert.True(report.Success);
    }

    // ===== P0-5：PauseState 命中时 Requeue action，不调 PS、不上报回执 =====

    [Fact]
    public async Task OutboundActionDispatcher_AccountPaused_RequeuesAndSkipsPs()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);
        var pause = new WeCom.PersonalRpa.Core.StateMachine.PauseState();
        pause.SetAccountPaused(true);
        var queue = new OutboundQueue(_options, logger: null);
        var psCalled = false;
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
        {
            psCalled = true;
            return Task.FromResult(new PowershellResult { Success = true, Action = action });
        }, pauseState: pause, queueOverride: queue);

        // 模拟出队后 Dispatch：item 已是 running 状态（DequeueNextAsync 会标记 running），
        // 这里直接构造 running item 调 DispatchOneAsync
        var item = new OutboxItem
        {
            ActionId = "req_paused",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "Conv-A",
            Text = "hi",
            Status = "pending",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await queue.EnqueueAsync(item);
        // 标记为 running（模拟出队）
        await queue.DequeueNextAsync();
        // 模拟出队后传入 running 状态的 item 给 dispatcher（与生产路径一致）
        item.Status = "running";

        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        // PS 不应被调
        Assert.False(psCalled);
        // 不应上报回执
        Assert.Empty(api.Reports);
        // action 应被 Requeue 回 pending
        // 通过 ListPendingAsync 验证（会把 running 重置 pending 并返回）
        var pending = await queue.ListPendingAsync();
        var recovered = Assert.Single(pending);
        Assert.Equal("req_paused", recovered.ActionId);
    }

    [Fact]
    public async Task OutboundActionDispatcher_ConversationPaused_RequeuesThatConversationOnly()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);
        var pause = new WeCom.PersonalRpa.Core.StateMachine.PauseState();
        pause.PauseConversation("Conv-Paused");
        var queue = new OutboundQueue(_options, logger: null);
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
            Task.FromResult(new PowershellResult { Success = true, Action = action }),
            pauseState: pause, queueOverride: queue);

        var pausedItem = new OutboxItem
        {
            ActionId = "req_conv_paused",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "Conv-Paused",
            Text = "x",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(pausedItem, CancellationToken.None);

        // 被暂停会话的 action 不应上报回执
        Assert.Empty(api.Reports);
    }

    [Fact]
    public async Task OutboundActionDispatcher_NotPaused_RunsNormally()
    {
        var api = new StubApi();
        var downloader = new AttachmentDownloader(new HttpClient(), _options, logger: null);
        var pause = new WeCom.PersonalRpa.Core.StateMachine.PauseState(); // 不暂停
        var dispatcher = CreateDispatcher(api, downloader, (action, p, ct) =>
            Task.FromResult(new PowershellResult { Success = true, Action = action }),
            pauseState: pause);

        var item = new OutboxItem
        {
            ActionId = "req_normal",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "Conv-Normal",
            Text = "x",
            CreatedAt = DateTimeOffset.UtcNow,
        };
        await dispatcher.DispatchOneAsync(item, CancellationToken.None);

        // 应正常上报 success
        var report = Assert.Single(api.Reports);
        Assert.True(report.Success);
    }

    // ===== OutboundQueue.RequeueAsync =====

    [Fact]
    public async Task OutboundQueue_RequeueAsync_ChangesRunningBackToPending()
    {
        var q = new OutboundQueue(_options, logger: null);
        await q.EnqueueAsync(new OutboxItem
        {
            ActionId = "req_rq",
            ActionType = ActionTypeNames.SendText,
            ConversationKey = "c",
            Text = "t",
            CreatedAt = DateTimeOffset.UtcNow,
        });
        var claimed = await q.DequeueNextAsync();
        Assert.NotNull(claimed);
        Assert.Equal("running", claimed!.Status);

        await q.RequeueAsync("req_rq");

        var pending = await q.ListPendingAsync();
        var recovered = Assert.Single(pending);
        Assert.Equal("req_rq", recovered.ActionId);
        Assert.Equal("pending", recovered.Status);
    }
}
