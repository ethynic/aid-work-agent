using System.Net.NetworkInformation;
using System.Net.WebSockets;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Realtime;

/// <summary>
/// WebSocket 自动重连 / 心跳管理器（设计文档 §9.2 / §F2）。
///
/// 设计：
///   - 包装 <see cref="ClientWebSocket"/>，由 <see cref="IAgentApiClient.ConnectWebSocketAsync"/> 拿到底层连接后接管。
///   - 心跳：周期发送文本 <c>ping</c>（30s 一次，仅作连接保活，不期望 pong）。
///   - 离线检测（P0-7 改动）：仅以 <c>ws.State != Open</c> 为判离线索索，不再用"60s 未收到消息"。
///     服务端可长期不主动推消息，应用层 ping 也无 pong，旧策略会频繁误判重连。
///   - 重连策略：指数退避 1s/2s/4s/8s/16s/30s（封顶 30s）。
///   - 网络变化：<see cref="NetworkChange.NetworkAvailabilityChanged"/> 触发立即重连（不走退避）。
///   - 重连成功：触发 <see cref="Reconnected"/> 事件；服务端会重新推送 pending actions（通过
///     <see cref="ServerMessageDispatcher"/> 解析 actions 入队 OutboundActionDispatcher）。
///   - 收到服务端推送（paused/resumed/actions/config_invalidate）→ 触发 <see cref="MessageReceived"/>，
///     由 <see cref="ServerMessageDispatcher"/> 解析 + 路由。
/// </summary>
public sealed class WebSocketConnectionManager : IHostedService, IDisposable, IReconnectSource
{
    private readonly IAgentApiClient _apiClient;
    private readonly ClientSession _session;
    private readonly PauseState _pauseState;
    private readonly ILogger<WebSocketConnectionManager>? _logger;
    private readonly TimeSpan _heartbeatInterval;
    private readonly TimeSpan _offlineThreshold;
    private readonly TimeSpan[] _backoffSchedule;

    private ClientWebSocket? _ws;
    private Timer? _heartbeatTimer;
    private CancellationTokenSource? _cts;
    private Task? _receiveLoopTask;
    private int _reconnectAttempt;
    private DateTime _lastReceivedAt = DateTime.UtcNow;
    private bool _networkAvailable = true;

    /// <summary>重连成功事件（OutboxPoller 订阅以触发立即 outbox 拉取）。</summary>
    public event EventHandler<EventArgs>? Reconnected;

    /// <summary>收到服务端消息事件（ServerMessageDispatcher 订阅以解析 paused/resumed/actions）。</summary>
    public event EventHandler<ReadOnlyMemory<byte>>? MessageReceived;

    public WebSocketConnectionManager(
        IAgentApiClient apiClient,
        ClientSession session,
        PauseState pauseState,
        ILogger<WebSocketConnectionManager>? logger = null)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _session = session ?? throw new ArgumentNullException(nameof(session));
        _pauseState = pauseState ?? throw new ArgumentNullException(nameof(pauseState));
        _logger = logger;
        _heartbeatInterval = TimeSpan.FromSeconds(30);
        _offlineThreshold = TimeSpan.FromSeconds(60);
        _backoffSchedule = new[]
        {
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(2),
            TimeSpan.FromSeconds(4),
            TimeSpan.FromSeconds(8),
            TimeSpan.FromSeconds(16),
            TimeSpan.FromSeconds(30),
        };
    }

    /// <inheritdoc />
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        _cts = new CancellationTokenSource();
        try
        {
            _networkAvailable = NetworkInterface.GetIsNetworkAvailable();
            NetworkChange.NetworkAvailabilityChanged += OnNetworkAvailabilityChanged;
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "订阅 NetworkChange 失败（继续运行，仅失去立即重连能力）");
        }

        // 启动后台连接任务（首次连接也走重连循环，失败会按退避重试）
        _receiveLoopTask = Task.Run(() => ConnectionLoopAsync(_cts.Token));
        _heartbeatTimer = new Timer(_ => HeartbeatTick(), null, _heartbeatInterval, _heartbeatInterval);
        await Task.CompletedTask;
    }

    /// <inheritdoc />
    public async Task StopAsync(CancellationToken cancellationToken)
    {
        try
        {
            NetworkChange.NetworkAvailabilityChanged -= OnNetworkAvailabilityChanged;
        }
        catch { /* 未订阅成功也无妨 */ }

        _cts?.Cancel();
        _heartbeatTimer?.Change(Timeout.Infinite, 0);

        if (_ws is not null && _ws.State == WebSocketState.Open)
        {
            try
            {
                await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "client stop",
                    CancellationTokenSource.CreateLinkedTokenSource(
                        _cts?.Token ?? CancellationToken.None,
                        cancellationToken).Token);
            }
            catch { /* 关闭失败忽略 */ }
        }

        if (_receiveLoopTask is not null)
        {
            try { await _receiveLoopTask.ConfigureAwait(false); }
            catch (OperationCanceledException) { }
            catch (Exception ex) { _logger?.LogWarning(ex, "ConnectionLoop 退出异常"); }
        }
    }

    /// <summary>
    /// 连接循环：连 → 收 → 断 → 退避 → 重连。
    /// 失败次数累加，按 _backoffSchedule 取下次延时。
    /// </summary>
    private async Task ConnectionLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                _ws = await _apiClient.ConnectWebSocketAsync(ct);
                _reconnectAttempt = 0;
                _lastReceivedAt = DateTime.UtcNow;
                _logger?.LogInformation("WebSocket 已连接");
                Reconnected?.Invoke(this, EventArgs.Empty);

                await ReceiveLoopAsync(_ws, ct);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "WebSocket 连接/接收异常，attempt={Attempt}", _reconnectAttempt);
            }
            finally
            {
                TryDisposeWs();
            }

            if (ct.IsCancellationRequested) break;

            // 指数退避
            var idx = Math.Min(_reconnectAttempt, _backoffSchedule.Length - 1);
            var delay = _backoffSchedule[idx];
            _reconnectAttempt++;
            _logger?.LogInformation("WebSocket 重连退避 {Delay}", delay);
            try { await Task.Delay(delay, ct); }
            catch (OperationCanceledException) { break; }
        }
    }

    /// <summary>
    /// 单次连接的 ReceiveAsync 循环。每条消息更新 _lastReceivedAt + 触发 MessageReceived 事件。
    /// </summary>
    private async Task ReceiveLoopAsync(ClientWebSocket ws, CancellationToken ct)
    {
        var buffer = new byte[8 * 1024];
        while (!ct.IsCancellationRequested && ws.State == WebSocketState.Open)
        {
            WebSocketReceiveResult result;
            try
            {
                result = await ws.ReceiveAsync(buffer, ct);
            }
            catch (WebSocketException ex)
            {
                _logger?.LogWarning(ex, "WebSocket ReceiveAsync 异常，将触发重连");
                return;
            }

            if (result.MessageType == WebSocketMessageType.Close)
            {
                _logger?.LogInformation("WebSocket 对端关闭，将重连");
                return;
            }

            _lastReceivedAt = DateTime.UtcNow;

            // 简化：完整消息（含分片组装）首版不处理，超过 8KB 的消息直接按一段发出
            // 完整分片留 TODO（设计文档 §9.2 完整版）。
            var data = buffer.AsMemory(0, result.Count);
            try
            {
                MessageReceived?.Invoke(this, data);
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "MessageReceived 订阅者异常");
            }
        }
    }

    /// <summary>
    /// 心跳 Tick：发送文本 ping（keepalive）+ 通过 ClientWebSocket.State 检测离线。
    ///
    /// P0-7 改动：旧版用"60s 未收到消息 → 触发重连"判离线，但服务端可能长期不主动推消息，
    /// 应用层 ping 也无 pong 响应，导致频繁误判重连。改为：
    ///   - 离线检测：定时检查 ws.State != Open → 触发重连（不再用消息时间阈值）
    ///   - 心跳：周期发文本 ping 作为连接保活（防止代理对静默长连接超时断开），
    ///     不期望 pong（应用层不依赖 pong）
    /// </summary>
    private void HeartbeatTick()
    {
        try
        {
            var ws = _ws;
            if (ws is null)
            {
                return;
            }

            // 离线检测：仅以 ws.State 为准（不再用 _offlineThreshold 判离线）
            if (ws.State != WebSocketState.Open)
            {
                _logger?.LogWarning("WebSocket State={State} 非 Open，触发重连", ws.State);
                TryAbortWs();
                return;
            }

            // Ping：发送明确的文本帧，兼容服务端应用层心跳处理（不期望 pong；失败 → 触发重连）
            _ = Task.Run(async () =>
            {
                try
                {
                    await ws.SendAsync("ping"u8.ToArray(), WebSocketMessageType.Text,
                        endOfMessage: true, CancellationToken.None);
                }
                catch (Exception ex)
                {
                    _logger?.LogWarning(ex, "WebSocket ping 失败，触发重连");
                    TryAbortWs();
                }
            });
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "HeartbeatTick 异常");
        }
    }

    /// <summary>网络变化立即重连（重置退避）。</summary>
    private void OnNetworkAvailabilityChanged(object? sender, NetworkAvailabilityEventArgs e)
    {
        var now = e.IsAvailable;
        _logger?.LogInformation("网络可用性变化：{From} → {To}", _networkAvailable, now);
        _networkAvailable = now;
        if (now)
        {
            // 网络恢复 → 重置退避 + abort 当前连接触发立即重连
            _reconnectAttempt = 0;
            TryAbortWs();
        }
    }

    private void TryAbortWs()
    {
        try { _ws?.Abort(); } catch { /* ignore */ }
    }

    private void TryDisposeWs()
    {
        try { _ws?.Dispose(); } catch { /* ignore */ }
        _ws = null;
    }

    public void Dispose()
    {
        _cts?.Dispose();
        _heartbeatTimer?.Dispose();
        TryDisposeWs();
    }
}
