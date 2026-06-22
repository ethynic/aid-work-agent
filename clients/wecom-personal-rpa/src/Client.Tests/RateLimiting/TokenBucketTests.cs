using Xunit;

namespace WeCom.PersonalRpa.Tests.RateLimiting;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-protocol.md §A.7（RateLimits）、
// schemas.py RpaRateLimits（per_minute=5, per_day=100, consecutive_failure_pause=2）
// 与 design.md §6.5（单账号单分钟 5 条、单日 100 条、连续失败 2 次暂停）。
//
// 本测试验证三类限速：
//   1) per_minute：每分钟令牌耗尽后拒绝，时间推进后恢复；
//   2) per_day：每日配额耗尽后拒绝；
//   3) consecutive_failure_pause：同一会话连续失败 N 次后触发暂停。
//
// IRateLimiter / TokenBucket 由 Client.Core 实现（并行 agent）。为让本测试独立可编译，
// 这里在测试本地实现一个最小令牌桶 + 连续失败计数器，语义对齐契约的 RateLimits。
// 待 Client.Core 的 IRateLimiter / TokenBucket 落地后，替换为真实实现即可。
// ====================================================================================

/// <summary>
/// 测试本地最小令牌桶，提供 per_minute / per_day 双窗限速 + 连续失败暂停。
/// 时间通过注入的 IClock 抽象，便于测试不依赖真实墙钟。
/// </summary>
internal interface ITestClock
{
    DateTime UtcNow { get; }
    void Advance(TimeSpan delta);
}

internal sealed class FakeClock : ITestClock
{
    public DateTime UtcNow { get; private set; } = new DateTime(2026, 6, 22, 0, 0, 0, DateTimeKind.Utc);
    public void Advance(TimeSpan delta) => UtcNow = UtcNow.Add(delta);
}

internal sealed class LocalTokenBucket
{
    private readonly ITestClock _clock;
    private readonly int _perMinute;
    private readonly int _perDay;
    private readonly int _consecutiveFailurePause;

    private int _minuteTokens;
    private DateTime _minuteWindowStart;
    private int _dayTokens;
    private DateTime _dayWindowStart;

    private readonly Dictionary<string, int> _consecutiveFailures = new();
    private readonly HashSet<string> _pausedConversations = new();

    public LocalTokenBucket(ITestClock clock, int perMinute, int perDay, int consecutiveFailurePause)
    {
        _clock = clock;
        _perMinute = perMinute;
        _perDay = perDay;
        _consecutiveFailurePause = consecutiveFailurePause;
        _minuteTokens = perMinute;
        _minuteWindowStart = clock.UtcNow;
        _dayTokens = perDay;
        _dayWindowStart = clock.UtcNow;
    }

    public bool TryAcquire(string conversationId)
    {
        if (_pausedConversations.Contains(conversationId)) return false;
        RefillWindows();
        if (_minuteTokens <= 0 || _dayTokens <= 0) return false;
        _minuteTokens--;
        _dayTokens--;
        return true;
    }

    public void RecordResult(string conversationId, bool success)
    {
        if (success)
        {
            _consecutiveFailures.Remove(conversationId);
            return;
        }

        if (!_consecutiveFailures.TryGetValue(conversationId, out var n)) n = 0;
        n++;
        _consecutiveFailures[conversationId] = n;

        if (n >= _consecutiveFailurePause)
        {
            _pausedConversations.Add(conversationId);
        }
    }

    public bool IsPaused(string conversationId) => _pausedConversations.Contains(conversationId);

    private void RefillWindows()
    {
        if (_clock.UtcNow - _minuteWindowStart >= TimeSpan.FromMinutes(1))
        {
            _minuteTokens = _perMinute;
            _minuteWindowStart = _clock.UtcNow;
        }
        if (_clock.UtcNow - _dayWindowStart >= TimeSpan.FromDays(1))
        {
            _dayTokens = _perDay;
            _dayWindowStart = _clock.UtcNow;
        }
    }
}

public class TokenBucketTests
{
    [Fact(DisplayName = "per_minute：前 N 条放行，第 N+1 条被拒；窗口滚动后恢复")]
    public void PerMinute_Limit_Blocks_AfterThreshold_And_Refills_AfterWindow()
    {
        var clock = new FakeClock();
        var bucket = new LocalTokenBucket(clock, perMinute: 5, perDay: 100, consecutiveFailurePause: 2);

        for (var i = 0; i < 5; i++)
        {
            Assert.True(bucket.TryAcquire("conv_a"));
        }
        // 第 6 条超额，被拒
        Assert.False(bucket.TryAcquire("conv_a"));

        // 推进 61 秒，窗口刷新
        clock.Advance(TimeSpan.FromSeconds(61));
        Assert.True(bucket.TryAcquire("conv_a"));
    }

    [Fact(DisplayName = "per_day：日配额耗尽后拒绝，次日恢复")]
    public void PerDay_Limit_Blocks_AfterDailyQuota_And_Refills_NextDay()
    {
        var clock = new FakeClock();
        // perMinute 设很大，避免被分钟限速干扰，专门测 per_day
        var bucket = new LocalTokenBucket(clock, perMinute: 1000, perDay: 3, consecutiveFailurePause: 99);

        Assert.True(bucket.TryAcquire("conv_a"));
        Assert.True(bucket.TryAcquire("conv_a"));
        Assert.True(bucket.TryAcquire("conv_a"));
        // 第 4 条：日配额耗尽
        Assert.False(bucket.TryAcquire("conv_a"));

        // 跨日恢复
        clock.Advance(TimeSpan.FromDays(1).Add(TimeSpan.FromSeconds(1)));
        Assert.True(bucket.TryAcquire("conv_a"));
    }

    [Fact(DisplayName = "consecutive_failure_pause：连续失败达到阈值后该会话被暂停")]
    public void ConsecutiveFailurePause_Triggers_AfterThreshold()
    {
        var clock = new FakeClock();
        var bucket = new LocalTokenBucket(clock, perMinute: 10, perDay: 100, consecutiveFailurePause: 2);

        Assert.True(bucket.TryAcquire("conv_a"));
        bucket.RecordResult("conv_a", success: false);
        Assert.False(bucket.IsPaused("conv_a")); // 1 次失败未达阈值

        Assert.True(bucket.TryAcquire("conv_a"));
        bucket.RecordResult("conv_a", success: false); // 第 2 次连续失败
        Assert.True(bucket.IsPaused("conv_a"));

        // 暂停后即使令牌充足也拒绝
        Assert.False(bucket.TryAcquire("conv_a"));
    }

    [Fact(DisplayName = "成功一次清零连续失败计数（不误暂停）")]
    public void Success_Resets_ConsecutiveFailureCounter()
    {
        var clock = new FakeClock();
        var bucket = new LocalTokenBucket(clock, perMinute: 10, perDay: 100, consecutiveFailurePause: 2);

        bucket.RecordResult("conv_a", success: false);
        bucket.RecordResult("conv_a", success: true); // 清零
        bucket.RecordResult("conv_a", success: false); // 重新从 1 开始

        Assert.False(bucket.IsPaused("conv_a"));
    }

    [Fact(DisplayName = "暂停是会话级：A 暂停不影响 B")]
    public void Pause_Is_PerConversation()
    {
        var clock = new FakeClock();
        var bucket = new LocalTokenBucket(clock, perMinute: 10, perDay: 100, consecutiveFailurePause: 2);

        bucket.RecordResult("conv_a", success: false);
        bucket.RecordResult("conv_a", success: false);
        Assert.True(bucket.IsPaused("conv_a"));

        Assert.True(bucket.TryAcquire("conv_b")); // B 不受影响
        Assert.False(bucket.IsPaused("conv_b"));
    }

    [Fact(DisplayName = "per_minute 与 per_day 同时生效：取更严格的那个")]
    public void PerMinute_And_PerDay_BothApply_MostRestrictiveWins()
    {
        var clock = new FakeClock();
        // per_minute=2 比 per_day=100 先到顶
        var bucket = new LocalTokenBucket(clock, perMinute: 2, perDay: 100, consecutiveFailurePause: 99);

        Assert.True(bucket.TryAcquire("conv_a"));
        Assert.True(bucket.TryAcquire("conv_a"));
        Assert.False(bucket.TryAcquire("conv_a")); // 分钟限流先生效
    }
}
