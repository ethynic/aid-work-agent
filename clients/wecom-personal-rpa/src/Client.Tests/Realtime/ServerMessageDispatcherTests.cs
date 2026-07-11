using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using WeCom.PersonalRpa.App.Realtime;
using WeCom.PersonalRpa.App.Outbound;
using WeCom.PersonalRpa.Core.StateMachine;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Realtime;

// ====================================================================================
// 契约来源：Phase 4 测试阶段补齐缺口
//
// ServerMessageDispatcher 订阅 WebSocketConnectionManager.MessageReceived（原始字节），
// 解析 JSON 协议（{type, scope, conversation_id}），调 ClientSession.PauseAsync / ResumeAsync。
//
// 缺口场景：服务端推 paused/resumed 时客户端不响应。本测试通过 internal DispatchAsync
// 直接验证协议解析与 ClientSession 调用（绕开事件订阅，因为 WebSocketConnectionManager 是 sealed）。
// 事件订阅部分由 IHostedService 生命周期框架保证（StartAsync += / StopAsync -=）。
// ====================================================================================

public sealed class ServerMessageDispatcherTests
{
    [Theory]
    [InlineData("陆伟@微信", "陆伟")]
    [InlineData(" Alice ", "Alice")]
    [InlineData("名称@微信中间", "名称@微信中间")]
    [InlineData("   ", null)]
    public void NormalizeConversationSearchName_IsConservative(string input, string? expected)
    {
        Assert.Equal(expected, OutboundActionDispatcher.NormalizeConversationSearchName(input));
    }

    private static byte[] Json(object payload)
        => Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(payload));

    private static (ServerMessageDispatcher dispatcher, ClientSession session, PauseState pause) Create()
    {
        var pause = new PauseState();
        var session = new ClientSession { PauseState = pause };
        // WebSocketConnectionManager 是 sealed 且依赖真实 ws，这里传 null 触发 StartAsync/StopAsync
        // 不会执行实际订阅（OnMessageReceived 是 event += 形式，不会触发连接）。
        // 仅测 DispatchAsync 路径；StartStop 由 Host 框架保证。
        var dispatcher = new ServerMessageDispatcher(null!, session, NullLogger<ServerMessageDispatcher>.Instance);
        return (dispatcher, session, pause);
    }

    [Fact]
    public async Task DispatchAsync_PausedTenant_SetsTenantPaused()
    {
        var (dispatcher, _, pause) = Create();
        var data = Json(new { type = "paused", scope = "tenant" });

        await dispatcher.DispatchAsync(data);

        Assert.True(pause.TenantPaused);
    }

    [Fact]
    public async Task DispatchAsync_PausedAccount_SetsAccountPaused()
    {
        var (dispatcher, _, pause) = Create();
        var data = Json(new { type = "paused", scope = "account" });

        await dispatcher.DispatchAsync(data);

        Assert.True(pause.AccountPaused);
    }

    [Fact]
    public async Task DispatchAsync_PausedConversation_PausesConversation()
    {
        var (dispatcher, _, pause) = Create();
        var data = Json(new { type = "paused", scope = "conversation", conversation_id = "conv-123" });

        await dispatcher.DispatchAsync(data);

        Assert.True(pause.IsConversationPaused("conv-123"));
        Assert.False(pause.IsConversationPaused("conv-999"));
    }

    [Fact]
    public async Task DispatchAsync_ResumedTenant_ClearsTenantPaused()
    {
        var (dispatcher, _, pause) = Create();
        pause.SetTenantPaused(true);

        var data = Json(new { type = "resumed", scope = "tenant" });
        await dispatcher.DispatchAsync(data);

        Assert.False(pause.TenantPaused);
    }

    [Fact]
    public async Task DispatchAsync_ResumedAccount_ClearsAccountPaused()
    {
        var (dispatcher, _, pause) = Create();
        pause.SetAccountPaused(true);

        var data = Json(new { type = "resumed", scope = "account" });
        await dispatcher.DispatchAsync(data);

        Assert.False(pause.AccountPaused);
    }

    [Fact]
    public async Task DispatchAsync_ResumedConversation_ClearsConversation()
    {
        var (dispatcher, _, pause) = Create();
        pause.PauseConversation("conv-x");
        Assert.True(pause.IsConversationPaused("conv-x"));

        var data = Json(new { type = "resumed", scope = "conversation", conversation_id = "conv-x" });
        await dispatcher.DispatchAsync(data);

        Assert.False(pause.IsConversationPaused("conv-x"));
    }

    [Fact]
    public async Task DispatchAsync_InvalidJson_DoesNotThrow()
    {
        var (dispatcher, _, _) = Create();
        var data = Encoding.UTF8.GetBytes("not-json");

        // 不应抛异常
        await dispatcher.DispatchAsync(data);
    }

    [Fact]
    public async Task DispatchAsync_UnknownType_IsIgnored()
    {
        var (dispatcher, _, pause) = Create();
        var data = Json(new { type = "config_invalidate" });  // 未在 dispatcher 处理

        await dispatcher.DispatchAsync(data);

        // 暂停状态不应被改动
        Assert.False(pause.IsPaused);
    }

    [Fact]
    public async Task DispatchAsync_UnknownScope_DiscardedWithoutPausing()
    {
        // P1-11：未知 scope 不再默认 Account 级（避免错误暂停整个账号），改为丢弃 + warning
        var (dispatcher, _, pause) = Create();
        var data = Json(new { type = "paused", scope = "unknown_scope_value" });

        await dispatcher.DispatchAsync(data);

        // 暂停状态不应被改动
        Assert.False(pause.IsPaused);
    }

    // ===== P0-6：actions 分支解析 ActionEnvelope 并入队 OutboundActionDispatcher =====

    /// <summary>手写最小 IAgentApiClient stub：用于 OutboundActionDispatcher 测试构造。</summary>
    private sealed class CaptureApi : WeCom.PersonalRpa.Core.AgentApi.IAgentApiClient
    {
        public Task<bool> ReportActionResultAsync(string requestId, bool success,
            string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default)
            => Task.FromResult(true);
        public Task<bool> PostCallbackAsync(WeCom.PersonalRpa.Core.Protocol.InboundEvent env, CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<WeCom.PersonalRpa.Core.Protocol.RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<WeCom.PersonalRpa.Core.Protocol.OutboxResponse> GetOutboxAsync(int limit = 100, CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<bool> ReportStatusAsync(WeCom.PersonalRpa.Core.Protocol.StatusPayload payload, CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
            => throw new NotSupportedException();
        public void Dispose() { }
    }

    [Fact]
    public async Task DispatchAsync_ActionsPayload_EnqueuesIntoOutboundDispatcher()
    {
        // P0-6：type=actions 时应解析 ActionEnvelope 并入队 OutboundActionDispatcher
        // 构造一个真实 OutboundActionDispatcher（用临时 SQLite + noop PS hook），
        // 通过 EnvelopeEnqueueAsync 入队后用 ListPendingAsync 验证。
        var tempDir = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "p06_" + System.Guid.NewGuid().ToString("N"));
        System.IO.Directory.CreateDirectory(tempDir);
        try
        {
            var opts = new WeCom.PersonalRpa.Core.Config.ClientOptions
            {
                ClientId = "p06_test",
                Outbound = new WeCom.PersonalRpa.Core.Config.OutboundOptions
                {
                    DbPath = System.IO.Path.Combine(tempDir, "outbox.db"),
                    MaxRetries = 1,
                },
            };
            var queue = new WeCom.PersonalRpa.App.Outbound.OutboundQueue(opts, logger: null);
            var dispatcher = new WeCom.PersonalRpa.App.Outbound.OutboundActionDispatcher(
                queue,
                new WeCom.PersonalRpa.App.Outbound.AttachmentDownloader(new System.Net.Http.HttpClient(), opts, logger: null),
                new WeCom.PersonalRpa.App.Powershell.PowershellOpsInvoker(
                    new WeCom.PersonalRpa.App.Powershell.PowershellOptions(),
                    Microsoft.Extensions.Logging.Abstractions.NullLogger<WeCom.PersonalRpa.App.Powershell.PowershellOpsInvoker>.Instance),
                new CaptureApi(),
                opts,
                new WeCom.PersonalRpa.App.Outbound.ChannelActionSource(),
                logger: null);

            var pause = new PauseState();
            var session = new ClientSession { PauseState = pause };
            var smd = new ServerMessageDispatcher(null!, session, null!, dispatcher: dispatcher);

            var envJson = new
            {
                type = "actions",
                request_id = "req_p06",
                session_id = "sess1",
                conversation_id = "conv_p06",
                reply_context = new
                {
                    sender_display_name = "张三",
                    sender_stable_id = "wm_1",
                    conversation_search_name = "陆伟",
                    inbound_text = "用户问题",
                    agent_reply_text = "hello",
                },
                actions = new object[]
                {
                    new { type = "send_text", text = "hello" },
                },
            };
            var data = Json(envJson);

            await smd.DispatchAsync(data);

            // 等价验证：OutboundQueue.ListPendingAsync 应能拿到入队的 item
            // 但注意：dispatcher.EnvelopeEnqueueAsync 会同时入队 _workCh + queue（pending→enqueue）。
            // 入队后 status='pending'，ListPendingAsync 应能看到。
            var pending = await queue.ListPendingAsync();
            var item = Assert.Single(pending);
            Assert.Equal("req_p06", item.ActionId);
            Assert.Equal("send_text", item.ActionType);
            Assert.Equal("陆伟", item.ConversationKey);
            Assert.Equal("hello", item.Text);
        }
        finally
        {
            try { System.IO.Directory.Delete(tempDir, recursive: true); } catch { }
        }
    }

    [Fact]
    public async Task DispatchAsync_ActionsPayload_NoDispatcher_DoesNotThrow()
    {
        // 测试场景：未注入 OutboundActionDispatcher（构造函数传 null），actions 事件应被丢弃不抛异常
        var (dispatcher, _, _) = Create();
        var data = Json(new { type = "actions", request_id = "req_x", actions = Array.Empty<object>() });

        await dispatcher.DispatchAsync(data); // 不抛
    }

    [Fact]
    public async Task DispatchAsync_OutboxAvailable_NoPoller_DoesNotThrow()
    {
        // 未注入 OutboxPoller 时，outbox_available 应被忽略不抛异常
        var (dispatcher, _, _) = Create();
        var data = Json(new { type = "outbox_available", latest_request_id = "req1", pending_count = 1 });

        await dispatcher.DispatchAsync(data); // 不抛
    }
}
