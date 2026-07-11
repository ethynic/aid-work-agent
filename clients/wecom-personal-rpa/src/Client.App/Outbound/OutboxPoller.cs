using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.Realtime;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>
/// 服务端 outbox 可靠轮询器（服务端 DB outbox 是唯一权威消息源，WS 仅加速通知）。
///
/// 触发时机：
///   - 启动立即拉取（Timer dueTime=0）；
///   - 在线时按 <c>_currentInterval</c> 周期拉取（采纳服务端 poll_interval_seconds，clamp [2,60]）；
///   - WebSocket 重连后立即拉取（订阅 <see cref="IReconnectSource.Reconnected"/>）；
///   - 收到 WS <c>outbox_available</c> 事件后立即拉取（由 ServerMessageDispatcher 调 <see cref="TriggerNowAsync"/>）。
///
/// 并发：<see cref="SemaphoreSlim"/>(1,1) 重入保护，已有轮询在跑时新触发跳过（在途轮询覆盖最新数据）。
/// 退避：HTTP 失败指数退避 1/2/4/8/16/30s（封顶 30s），成功复位到 _currentInterval。
/// 幂等：拉到的信封逐个交 <see cref="OutboundActionDispatcher.EnvelopeEnqueueAsync"/>，由其按
///   request_id+action_index 去重并对终态重复项重报回执（at-least-once）。
/// 清理：每 ~50 次成功轮询调 <see cref="OutboundQueue.PruneTerminalAsync"/> 清理超期终态行（7 天）。
/// 日志：只记录 request_id / 数量 / 状态，不打印 inbound_text / agent_reply_text / 完整信封（规约#7）。
/// </summary>
internal sealed class OutboxPoller : IHostedService, IDisposable
{
    private const int Limit = 100;
    private const int MinPollSeconds = 2;
    private const int MaxPollSeconds = 60;
    private const int PruneEverySuccesses = 50;
    private static readonly TimeSpan PruneRetention = TimeSpan.FromDays(7);
    private static readonly TimeSpan[] Backoff =
    {
        TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(4),
        TimeSpan.FromSeconds(8), TimeSpan.FromSeconds(16), TimeSpan.FromSeconds(30),
    };

    private readonly IAgentApiClient _api;
    private readonly OutboundActionDispatcher _dispatcher;
    private readonly IReconnectSource _reconnect;
    private readonly OutboundQueue _queue;
    private readonly ClientOptions _options;
    private readonly ILogger<OutboxPoller>? _logger;

    private readonly SemaphoreSlim _gate = new(1, 1);
    private Timer? _timer;
    private CancellationTokenSource? _cts;
    private TimeSpan _currentInterval;
    private volatile int _backoffIndex = -1; // -1 = 不在退避（用 _currentInterval）；volatile：poll 线程写、下次调度读，跨线程需可见
    private int _pollSincePrune;
    private volatile bool _stopped;

    /// <summary>测试探针：累计触发轮询次数。</summary>
    internal volatile int PollInvocations;

    /// <summary>测试探针：当前退避档位（-1=正常）。</summary>
    internal int BackoffIndex => _backoffIndex;

    /// <summary>测试探针：当前正常轮询间隔。</summary>
    internal TimeSpan CurrentInterval => _currentInterval;

    internal OutboxPoller(
        IAgentApiClient api,
        OutboundActionDispatcher dispatcher,
        IReconnectSource reconnect,
        OutboundQueue queue,
        ClientOptions options,
        ILogger<OutboxPoller>? logger = null)
    {
        _api = api ?? throw new ArgumentNullException(nameof(api));
        _dispatcher = dispatcher ?? throw new ArgumentNullException(nameof(dispatcher));
        _reconnect = reconnect ?? throw new ArgumentNullException(nameof(reconnect));
        _queue = queue ?? throw new ArgumentNullException(nameof(queue));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _logger = logger;
    }

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        _cts = new CancellationTokenSource();
        _currentInterval = ClampInterval(_options.PollIntervalSeconds);
        _reconnect.Reconnected += OnReconnected;
        // 单次触发 Timer（period=Infinite），每次轮询后按退避/正常间隔重排，干净支持变长退避。
        _timer = new Timer(OnTimerCallback, null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        _timer.Change(TimeSpan.Zero, Timeout.InfiniteTimeSpan); // 立即首拉
        _logger?.LogInformation("OutboxPoller 启动 interval={S}s", (int)_currentInterval.TotalSeconds);
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public Task StopAsync(CancellationToken cancellationToken)
    {
        _stopped = true; // 先置停止标志，拦截 fire-and-forget 的 TriggerNowAsync（Reconnected/outbox_available/已派发的 timer 回调）
        _reconnect.Reconnected -= OnReconnected;
        _timer?.Change(Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        _cts?.Cancel();
        return Task.CompletedTask;
    }

    private void OnTimerCallback(object? state) => _ = TriggerNowAsync();
    private void OnReconnected(object? sender, EventArgs e) => _ = TriggerNowAsync();

    /// <summary>
    /// 立即触发一次拉取（受 semaphore 重入保护；已有轮询在跑则跳过）。
    /// 定时器 / WS 重连 / outbox_available 三路共用。拉取后按当前延迟重排下次。
    /// </summary>
    internal async Task TriggerNowAsync()
    {
        if (_stopped) return; // 已停止：不再触发，避免 StopAsync 之后 fire-and-forget 调用重武装 timer / 访问已 dispose 的 _cts/_gate
        if (!await _gate.WaitAsync(0).ConfigureAwait(false)) return; // 并发重入跳过
        try
        {
            PollInvocations++;
            await PollOnceAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // PollOnceAsync 已内部处理 GetOutboxAsync 失败；此处兜底其他未捕获异常
            _logger?.LogError(ex, "OutboxPoller 未捕获异常");
        }
        finally
        {
            try { _gate.Release(); } catch (ObjectDisposedException) { /* 关停中 */ }
            if (!_stopped) Reschedule(CurrentDelay());
        }
    }

    /// <summary>
    /// 执行一次 outbox 拉取（测试 seam）。GetOutboxAsync 失败时记日志 + 推进退避并返回（不抛）；
    /// 成功时复位退避、采纳服务端间隔、逐项入队、定期清理终态行。
    /// </summary>
    internal async Task PollOnceAsync()
    {
        var ct = _cts?.Token ?? CancellationToken.None;
        OutboxResponse resp;
        try
        {
            resp = await _api.GetOutboxAsync(Limit, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "Outbox 拉取失败，进入退避");
            _backoffIndex = Math.Min(_backoffIndex + 1, Backoff.Length - 1);
            return;
        }

        // 成功：复位退避，采纳服务端建议间隔（clamp 2-60）
        _backoffIndex = -1;
        if (resp.PollIntervalSeconds is int pi && pi > 0)
        {
            _currentInterval = ClampInterval(pi);
        }

        // 关停已取消：不处理本批 items，避免关停期间越界入队
        if (ct.IsCancellationRequested) return;

        var items = resp.Items ?? new List<ActionEnvelope>();
        foreach (var env in items)
        {
            if (env is null) continue;
            try
            {
                await _dispatcher.EnvelopeEnqueueAsync(env, ct).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // 单条入队失败不影响整体轮询；日志只出 request_id，不含消息内容
                _logger?.LogWarning(ex, "Outbox 信封入队失败 request_id={Rid}", env.RequestId);
            }
        }

        _logger?.LogInformation("Outbox 拉取完成 count={N} backoff={B} interval={S}s",
            items.Count, _backoffIndex, (int)_currentInterval.TotalSeconds);

        // 偶尔清理超期终态行（每 PruneEverySuccesses 次成功轮询一次）
        if (++_pollSincePrune >= PruneEverySuccesses)
        {
            _pollSincePrune = 0;
            try
            {
                var removed = await _queue.PruneTerminalAsync(PruneRetention, ct).ConfigureAwait(false);
                if (removed > 0)
                {
                    _logger?.LogInformation("Outbox 清理超期终态行 removed={N}", removed);
                }
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "Outbox 清理终态行失败");
            }
        }
    }

    private TimeSpan CurrentDelay()
        => _backoffIndex < 0 ? _currentInterval : Backoff[Math.Min(_backoffIndex, Backoff.Length - 1)];

    private void Reschedule(TimeSpan dueTime)
    {
        try
        {
            _timer?.Change(dueTime, Timeout.InfiniteTimeSpan);
        }
        catch (ObjectDisposedException)
        {
            // 正在停止，忽略
        }
    }

    private static TimeSpan ClampInterval(int seconds)
        => TimeSpan.FromSeconds(Math.Clamp(seconds, MinPollSeconds, MaxPollSeconds));

    public void Dispose()
    {
        _timer?.Dispose();
        _gate.Dispose();
        _cts?.Dispose();
    }
}
