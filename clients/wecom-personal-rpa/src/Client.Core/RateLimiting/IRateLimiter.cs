namespace WeCom.PersonalRpa.Core.RateLimiting;

/// <summary>
/// 限速决策结果。
/// </summary>
public sealed class RateLimitDecision
{
    /// <summary>是否允许发送。</summary>
    public bool Allowed { get; init; }

    /// <summary>不允许时的原因（用于日志，脱敏）。</summary>
    public string? Reason { get; init; }

    /// <summary>是否达到连续失败暂停阈值（调用方应转入 PausedError）。</summary>
    public bool ShouldPause { get; init; }

    /// <summary>允许发送。</summary>
    public static RateLimitDecision Ok() => new() { Allowed = true };

    /// <summary>被限速拒绝。</summary>
    public static RateLimitDecision Denied(string reason) => new() { Allowed = false, Reason = reason };

    /// <summary>达到连续失败阈值，建议暂停。</summary>
    public static RateLimitDecision Pause(string reason) => new() { Allowed = false, Reason = reason, ShouldPause = true };
}

/// <summary>
/// 限速器接口（protocol.md §C.4）。按 per_minute / per_day / consecutive_failure_pause 三维度决策。
/// </summary>
public interface IRateLimiter
{
    /// <summary>
    /// 尝试获取发送许可（按 per_minute / per_day 滑动窗口）。
    /// 不修改失败计数；调用方在发送完成后再调用 RecordSuccess / RecordFailure。
    /// </summary>
    /// <param name="accountId">账号 ID。</param>
    /// <returns>决策结果。</returns>
    RateLimitDecision TryAcquire(string accountId);

    /// <summary>
    /// 记录一次发送成功。重置该账号的连续失败计数。
    /// </summary>
    void RecordSuccess(string accountId);

    /// <summary>
    /// 记录一次发送失败。累加连续失败计数；达阈值返回应暂停决策。
    /// </summary>
    RateLimitDecision RecordFailure(string accountId);
}
