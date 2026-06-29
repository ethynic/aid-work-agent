using System.Text.Json;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Inbound;

/// <summary>
/// 入站消息事件上报器（Phase 4 块 E）。
///
/// 订阅 <see cref="IMessageWatcher.NewMessageReceived"/> 事件，对每条消息：
///   1. 调 <see cref="InboundEventBuilder.BuildAsync"/> 构造 InboundEvent
///   2. 从 payload 中提取 sender_display_name + sender_stable_id
///   3. 调 <see cref="MonitorUsersCache.IsAllowed"/> 做白名单预过滤
///   4. 通过 → <see cref="IAgentApiClient.ReportInboundAsync"/> 上报
///      不通过 → 写本地日志（不上报，但记日志方便排查）
///
/// 实现的 IHostedService（Phase 4 测试阶段补齐跨块协调缺口）：
///   - StartAsync：调 <see cref="Subscribe(IMessageWatcher)"/> 接通 ChatArchiveListener → 上报链路。
///   - StopAsync：调 <see cref="Unsubscribe(IMessageWatcher)"/> 解除订阅。
///   - 注入 <see cref="PauseState"/>（可空）：Tenant / Account 暂停时跳过上报。
///
/// 设计见 docs/system/wecom-personal-rpa-client-design.md §F6。
/// </summary>
public sealed class InboundEventReporter : IHostedService
{
    private const string Tag = "InboundEventReporter";

    private readonly InboundEventBuilder _builder;
    private readonly MonitorUsersCache _monitorCache;
    private readonly IAgentApiClient _apiClient;
    private readonly IMessageWatcher? _watcher;
    private readonly PauseState? _pauseState;
    private readonly ILogger<InboundEventReporter>? _logger;
    private readonly string _bindingId;

    public InboundEventReporter(
        InboundEventBuilder builder,
        MonitorUsersCache monitorCache,
        IAgentApiClient apiClient,
        IOptions<ClientOptions> options,
        ILogger<InboundEventReporter>? logger = null,
        // 可选依赖：DI 注入时由容器解析（块 G 单例）；测试场景传 null 跳过暂停检查
        IMessageWatcher? watcher = null,
        PauseState? pauseState = null)
    {
        _builder = builder ?? throw new ArgumentNullException(nameof(builder));
        _monitorCache = monitorCache ?? throw new ArgumentNullException(nameof(monitorCache));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _logger = logger;
        _watcher = watcher;
        _pauseState = pauseState;
        _bindingId = options?.Value?.MonitorUsers?.BindingId ?? string.Empty;
    }

    /// <summary>订阅 IMessageWatcher.NewMessageReceived 事件。</summary>
    public void Subscribe(IMessageWatcher watcher)
    {
        ArgumentNullException.ThrowIfNull(watcher);
        watcher.NewMessageReceived += HandleNewMessageAsync;
    }

    /// <summary>取消订阅。</summary>
    public void Unsubscribe(IMessageWatcher watcher)
    {
        ArgumentNullException.ThrowIfNull(watcher);
        watcher.NewMessageReceived -= HandleNewMessageAsync;
    }

    /// <inheritdoc />
    Task IHostedService.StartAsync(CancellationToken cancellationToken)
    {
        if (_watcher is not null)
        {
            Subscribe(_watcher);
            _logger?.LogInformation("[{Tag}] 已订阅 ChatArchiveListener.NewMessageReceived", Tag);
        }
        else
        {
            _logger?.LogWarning("[{Tag}] IMessageWatcher 未注入，订阅跳过（入站消息不会被上报）", Tag);
        }
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    Task IHostedService.StopAsync(CancellationToken cancellationToken)
    {
        if (_watcher is not null)
        {
            Unsubscribe(_watcher);
            _logger?.LogInformation("[{Tag}] 已取消订阅 ChatArchiveListener.NewMessageReceived", Tag);
        }
        return Task.CompletedTask;
    }

    /// <summary>核心处理：构造 → 白名单过滤 → 上报。</summary>
    private async void HandleNewMessageAsync(object? sender, InboundEventArgs e)
    {
        try
        {
            await HandleAsync(e.Message).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // 单条处理异常不影响后续事件流；记日志后吞掉
            _logger?.LogError(ex, "[{Tag}] 处理入站消息异常 msgid={MsgId}", Tag, e.Message.MsgId);
        }
    }

    /// <summary>同步可测试入口（直接传 ArchiveMessage，绕开事件订阅）。</summary>
    internal async Task<bool> HandleAsync(ArchiveMessage msg, CancellationToken ct = default)
    {
        // 0. 暂停检查（PauseState 由块 G 单例注入）：Tenant/Account 级暂停时跳过上报。
        //    注：仅综合 Tenant/Account 级判断（IsPaused = TenantPaused || AccountPaused）；
        //    会话级暂停目前未携带到入站消息（ArchiveMessage 没有 conversation_id 维度），不在此处过滤。
        if (_pauseState?.IsPaused == true)
        {
            _logger?.LogDebug("[{Tag}] 客户端暂停中（tenant/account），跳过消息上报 msgid={MsgId}", Tag, msg.MsgId);
            return false;
        }

        // 1. 构造 InboundEvent（含媒体下载 + 上传）
        InboundEvent? evt;
        try
        {
            evt = await _builder.BuildAsync(msg, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "[{Tag}] BuildAsync 失败 msgid={MsgId}（不上报，等下一条）", Tag, msg.MsgId);
            return false;
        }

        // 2. 从 payload 提取 sender 信息（JsonElement 反查 MessagePayload 字段）
        var (senderName, senderId) = ExtractSender(evt.Payload);

        // 3. 白名单预过滤（bindingId 为空时视为监控所有，直接放行）
        if (!_monitorCache.IsAllowed(_bindingId, senderName, senderId))
        {
            _logger?.LogInformation(
                "[{Tag}] 消息被白名单过滤 msgid={MsgId} binding={Binding} sender={Sender}",
                Tag, msg.MsgId, _bindingId, senderName ?? senderId ?? "<unknown>");
            return false;
        }

        // 4. 上报
        var ok = await _apiClient.ReportInboundAsync(evt, ct).ConfigureAwait(false);
        if (!ok)
        {
            _logger?.LogWarning("[{Tag}] 上报失败 msgid={MsgId}", Tag, msg.MsgId);
        }
        return ok;
    }

    /// <summary>
    /// 从 Payload JsonElement 中提取 sender_display_name + sender_stable_id。
    /// 字段缺失时返回空字符串（IsAllowed 会做 null/empty 检查）。
    /// </summary>
    private static (string? name, string? id) ExtractSender(JsonElement payload)
    {
        string? name = null;
        string? id = null;

        if (payload.ValueKind == JsonValueKind.Object)
        {
            if (payload.TryGetProperty("sender_display_name", out var nEl) && nEl.ValueKind == JsonValueKind.String)
            {
                name = nEl.GetString();
            }
            if (payload.TryGetProperty("sender_stable_id", out var iEl) && iEl.ValueKind == JsonValueKind.String)
            {
                id = iEl.GetString();
            }
        }
        return (name, id);
    }
}
