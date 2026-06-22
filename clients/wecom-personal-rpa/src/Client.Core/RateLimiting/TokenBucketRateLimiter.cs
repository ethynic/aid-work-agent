using System.Collections.Concurrent;

namespace WeCom.PersonalRpa.Core.RateLimiting;

/// <summary>
/// 令牌桶 + 滑动窗口限速器实现。
/// 按 per_minute（60 秒滑动窗口）与 per_day（86400 秒滑动窗口）控制单账号发送频率；
/// 同时维护连续失败计数，达 consecutive_failure_pause 阈值返回应暂停。
/// </summary>
public sealed class TokenBucketRateLimiter : IRateLimiter
{
    private readonly Func<Protocol.RateLimits> _limitsResolver;
    private readonly ConcurrentDictionary<string, AccountState> _states = new();

    /// <summary>构造限速器。</summary>
    /// <param name="limitsResolver">限速策略解析器（通常从 RpaConfigResponse 读取最新值）。</param>
    public TokenBucketRateLimiter(Func<Protocol.RateLimits> limitsResolver)
    {
        _limitsResolver = limitsResolver ?? throw new ArgumentNullException(nameof(limitsResolver));
    }

    /// <summary>用固定限速构造（便捷）。</summary>
    public TokenBucketRateLimiter(Protocol.RateLimits limits) : this(() => limits)
    {
    }

    /// <inheritdoc />
    public RateLimitDecision TryAcquire(string accountId)
    {
        if (string.IsNullOrEmpty(accountId)) throw new ArgumentNullException(nameof(accountId));
        var limits = _limitsResolver();
        var state = _states.GetOrAdd(accountId, _ => new AccountState());

        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        lock (state)
        {
            // 清理两个窗口外的旧记录
            state.MinuteWindow.RemoveAll(t => t <= now - 60);
            state.DayWindow.RemoveAll(t => t <= now - 86400);

            if (state.MinuteWindow.Count >= limits.PerMinute)
            {
                return RateLimitDecision.Denied($"per_minute 限速：{state.MinuteWindow.Count}/{limits.PerMinute}");
            }
            if (state.DayWindow.Count >= limits.PerDay)
            {
                return RateLimitDecision.Denied($"per_day 限速：{state.DayWindow.Count}/{limits.PerDay}");
            }

            // 通过：记录发送时刻
            state.MinuteWindow.Add(now);
            state.DayWindow.Add(now);
            return RateLimitDecision.Ok();
        }
    }

    /// <inheritdoc />
    public void RecordSuccess(string accountId)
    {
        if (string.IsNullOrEmpty(accountId)) return;
        var state = _states.GetOrAdd(accountId, _ => new AccountState());
        lock (state)
        {
            state.ConsecutiveFailures = 0;
        }
    }

    /// <inheritdoc />
    public RateLimitDecision RecordFailure(string accountId)
    {
        if (string.IsNullOrEmpty(accountId)) throw new ArgumentNullException(nameof(accountId));
        var limits = _limitsResolver();
        var state = _states.GetOrAdd(accountId, _ => new AccountState());
        lock (state)
        {
            state.ConsecutiveFailures++;
            if (limits.ConsecutiveFailurePause > 0
                && state.ConsecutiveFailures >= limits.ConsecutiveFailurePause)
            {
                return RateLimitDecision.Pause(
                    $"连续失败 {state.ConsecutiveFailures} 次，达阈值 {limits.ConsecutiveFailurePause}");
            }
            return new RateLimitDecision { Allowed = false, Reason = $"连续失败 {state.ConsecutiveFailures}" };
        }
    }

    /// <summary>暴露某账号的连续失败计数（供诊断 / 测试）。</summary>
    public int GetConsecutiveFailures(string accountId)
    {
        if (!_states.TryGetValue(accountId, out var state)) return 0;
        lock (state) return state.ConsecutiveFailures;
    }

    private sealed class AccountState
    {
        public List<long> MinuteWindow { get; } = new();
        public List<long> DayWindow { get; } = new();
        public int ConsecutiveFailures { get; set; }
    }
}
