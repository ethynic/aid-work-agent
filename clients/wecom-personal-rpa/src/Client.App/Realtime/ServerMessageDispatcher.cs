using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.Outbound;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Realtime;

/// <summary>
/// WebSocket 服务端消息分发器（Phase 4 测试阶段补齐跨块协调缺口）。
///
/// 背景：块 G 的 <see cref="WebSocketConnectionManager"/> 通过 <c>MessageReceived</c> 事件
/// 暴露原始字节流，但没有解析器把 JSON 协议事件转发到 <see cref="ClientSession"/>。
/// 不补这个缺口会导致服务端推送 paused/resumed 时客户端不响应。
///
/// 协议（首版约定，与服务端 Pydantic Literal 对齐；TODO 标记协议需对齐到 protocol.md）：
/// <code>
/// { "type": "paused",  "scope": "account"|"tenant"|"conversation", "conversation_id": "..." }
/// { "type": "resumed", "scope": "account"|"tenant"|"conversation", "conversation_id": "..." }
/// </code>
///
/// 实现：
///   - <see cref="IHostedService"/>：StartAsync 订阅 MessageReceived，StopAsync 解除订阅。
///   - 仅解析 paused/resumed 两类（actions / config_invalidate 由块 E/OutboundActionDispatcher 处理）。
///   - JSON 解析失败/格式异常：记 warning 后丢弃单条，不传染。
/// </summary>
internal sealed class ServerMessageDispatcher : IHostedService
{
    private const string Tag = "ServerMessageDispatcher";

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    private readonly WebSocketConnectionManager? _ws;
    private readonly ClientSession _session;
    private readonly OutboundActionDispatcher? _dispatcher;
    private readonly OutboxPoller? _outboxPoller;
    private readonly ILogger<ServerMessageDispatcher>? _logger;

    public ServerMessageDispatcher(
        WebSocketConnectionManager ws,
        ClientSession session,
        ILogger<ServerMessageDispatcher>? logger = null,
        // P0-6：可选 OutboundActionDispatcher，用于解析 type=actions 的服务端推送。
        // DI 容器注入时由容器解析；测试场景传 null 跳过 actions 分支（验证 paused/resumed 不需要）。
        OutboundActionDispatcher? dispatcher = null,
        // 可选 OutboxPoller，用于 type=outbox_available 时触发立即拉取。
        OutboxPoller? outboxPoller = null)
    {
        // ws 允许为 null：仅在 IHostedService.StartAsync/StopAsync 路径需要，DispatchAsync 测试入口不需要。
        // DI 容器注入时永远非 null；测试通过 internal DispatchAsync 验证协议解析时传 null 跳过事件订阅。
        _ws = ws;
        _session = session ?? throw new ArgumentNullException(nameof(session));
        _logger = logger;
        _dispatcher = dispatcher;
        _outboxPoller = outboxPoller;
    }

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        if (_ws is not null)
        {
            _ws.MessageReceived += OnMessageReceived;
            _logger?.LogInformation("[{Tag}] 已订阅 WebSocketConnectionManager.MessageReceived", Tag);
        }
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public Task StopAsync(CancellationToken cancellationToken)
    {
        if (_ws is not null)
        {
            _ws.MessageReceived -= OnMessageReceived;
            _logger?.LogInformation("[{Tag}] 已取消订阅 WebSocketConnectionManager.MessageReceived", Tag);
        }
        return Task.CompletedTask;
    }

    private async void OnMessageReceived(object? sender, ReadOnlyMemory<byte> data)
    {
        try
        {
            await DispatchAsync(data).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // 单条解析失败不影响后续事件流
            _logger?.LogError(ex, "[{Tag}] 处理服务端消息异常", Tag);
        }
    }

    /// <summary>同步可测试入口：直接传字节，绕开事件订阅。</summary>
    internal async Task DispatchAsync(ReadOnlyMemory<byte> data, CancellationToken ct = default)
    {
        ServerEventPayload? evt;
        try
        {
            evt = JsonSerializer.Deserialize<ServerEventPayload>(data.Span, JsonOpts);
        }
        catch (JsonException ex)
        {
            _logger?.LogWarning(ex, "[{Tag}] 服务端消息 JSON 解析失败，丢弃", Tag);
            return;
        }

        if (evt is null || string.IsNullOrEmpty(evt.Type))
        {
            _logger?.LogDebug("[{Tag}] 服务端消息为空或缺少 type，丢弃", Tag);
            return;
        }

        var scope = ParseScope(evt.Scope);
        switch (evt.Type)
        {
            case "paused":
                // P1-11：未知 scope 返回 null → 丢弃（不调 PauseAsync，避免错误暂停整个账号）
                if (scope is null)
                {
                    _logger?.LogWarning("[{Tag}] 收到 paused 但 scope 未知：{ScopeRaw}，丢弃", Tag, evt.Scope);
                    return;
                }
                await _session.PauseAsync(scope.Value, evt.ConversationId).ConfigureAwait(false);
                _logger?.LogInformation("[{Tag}] 收到 paused（scope={Scope}, conv={Conv}）",
                    Tag, scope, evt.ConversationId ?? "<none>");
                break;
            case "resumed":
                // 同上：未知 scope 丢弃
                if (scope is null)
                {
                    _logger?.LogWarning("[{Tag}] 收到 resumed 但 scope 未知：{ScopeRaw}，丢弃", Tag, evt.Scope);
                    return;
                }
                await _session.ResumeAsync(scope.Value, evt.ConversationId).ConfigureAwait(false);
                _logger?.LogInformation("[{Tag}] 收到 resumed（scope={Scope}, conv={Conv}）",
                    Tag, scope, evt.ConversationId ?? "<none>");
                break;
            case "actions":
                // P0-6：服务端推送 ActionEnvelope，解析后调 OutboundActionDispatcher.EnvelopeEnqueueAsync 入队。
                // dispatcher 为 null 时（测试场景）跳过；JSON 字段缺失由 ActionEnvelope 默认值兜底。
                if (_dispatcher is null)
                {
                    _logger?.LogDebug("[{Tag}] 收到 actions 但 OutboundActionDispatcher 未注入，丢弃", Tag);
                    return;
                }
                await HandleActionsAsync(data).ConfigureAwait(false);
                break;
            case "outbox_available":
                // 服务端通知有新 pending 动作。仅触发一次立即拉取，不直接执行（服务端 outbox 是权威源）。
                // poller 为 null 时（测试场景）跳过。
                if (_outboxPoller is null)
                {
                    _logger?.LogDebug("[{Tag}] 收到 outbox_available 但 OutboxPoller 未注入，忽略", Tag);
                    return;
                }
                _ = _outboxPoller.TriggerNowAsync().ContinueWith(t =>
                {
                    if (t.IsFaulted)
                        _logger?.LogWarning(t.Exception, "[{Tag}] outbox_available 触发拉取失败", Tag);
                }, TaskContinuationOptions.OnlyOnFaulted);
                _logger?.LogDebug("[{Tag}] 收到 outbox_available，触发立即拉取", Tag);
                break;
            case "config_invalidate":
                // 服务端通知配置失效。客户端目前没有需要响应此事件的本地缓存
                // （入站白名单缓存随 InboundEventReporter 一并删除），此处仅 log 留痕。
                _logger?.LogInformation("[{Tag}] 收到 config_invalidate（无订阅者）", Tag);
                break;
            default:
                // 未知 type 记 warning 后丢弃（不传染）
                _logger?.LogWarning("[{Tag}] 收到未识别事件 type={Type}，丢弃", Tag, evt.Type);
                break;
        }
    }

    /// <summary>
    /// P0-6：解析 ActionEnvelope JSON 并入队 OutboundActionDispatcher。
    /// 反序列化失败 → 记 warning 后丢弃单条。
    /// </summary>
    private async Task HandleActionsAsync(ReadOnlyMemory<byte> data)
    {
        ActionEnvelope? env;
        try
        {
            env = JsonSerializer.Deserialize<ActionEnvelope>(data.Span, JsonOpts);
        }
        catch (JsonException ex)
        {
            _logger?.LogWarning(ex, "[{Tag}] actions 信封 JSON 解析失败，丢弃", Tag);
            return;
        }
        if (env is null || string.IsNullOrEmpty(env.RequestId))
        {
            _logger?.LogWarning("[{Tag}] actions 信封缺少 request_id，丢弃", Tag);
            return;
        }
        if (env.Actions.Count == 0)
        {
            _logger?.LogDebug("[{Tag}] actions 信封 actions 为空，丢弃 request_id={Rid}", Tag, env.RequestId);
            return;
        }

        try
        {
            await _dispatcher!.EnvelopeEnqueueAsync(env, default).ConfigureAwait(false);
            _logger?.LogInformation("[{Tag}] 已入队 actions request_id={Rid} count={N}",
                Tag, env.RequestId, env.Actions.Count);
        }
        catch (Exception ex)
        {
            // EnqueueAsync 失败不传染（OutboundActionDispatcher 内部已 catch；这里兜底）
            _logger?.LogError(ex, "[{Tag}] EnvelopeEnqueueAsync 异常 request_id={Rid}", Tag, env.RequestId);
        }
    }

    private static PauseScope? ParseScope(string? scope)
    {
        // P1-11：未知 scope 返回 null（由调用方丢弃，不再默认 Account 级，避免错误暂停整个账号）
        return scope switch
        {
            "tenant" => PauseScope.Tenant,
            "account" => PauseScope.Account,
            "conversation" => PauseScope.Conversation,
            _ => null,
        };
    }

    /// <summary>
    /// 服务端事件载荷。字段名按协议约定（snake_case），通过 [JsonPropertyName] 显式映射，
    /// PropertyNameCaseInsensitive 仅兜底 PascalCase 输入。
    /// TODO(Phase 5)：与服务端 protocol.md 对齐确认字段名（type / scope / conversation_id）。
    /// </summary>
    private sealed class ServerEventPayload
    {
        [JsonPropertyName("type")]
        public string? Type { get; set; }

        [JsonPropertyName("scope")]
        public string? Scope { get; set; }

        [JsonPropertyName("conversation_id")]
        public string? ConversationId { get; set; }
    }
}
