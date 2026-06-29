namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 企微 45009 频率限制异常（Phase 3 P0-3）。
///
/// 由 ArchiveHttpClient 在 errcode=45009 时抛出，携带建议的重试等待秒数。
/// ChatArchiveListener 捕获此异常后设置 _pausedUntil 暂停期，
/// 在暂停期内跳过实际请求（不再触发 HTTP 调用，避免持续 45009）。
///
/// 设计动机：原来 ArchiveHttpClient 内部 sleep 60s 后继续重试，
/// 但 ChatArchiveListener 的 PeriodicTimer 下个 tick（3s 后）会再次进入，
/// 实际并未暂停。把"暂停多久"决策权交给 listener，单一职责。
/// </summary>
public sealed class WeComRateLimitException : Exception
{
    /// <summary>建议的重试等待秒数（默认 60）。</summary>
    public int RetryAfterSeconds { get; }

    public WeComRateLimitException(int retryAfterSeconds = 60, string? message = null)
        : base(message ?? $"企微返回 45009 频率限制，建议等待 {retryAfterSeconds}s 后重试")
    {
        RetryAfterSeconds = retryAfterSeconds > 0 ? retryAfterSeconds : 60;
    }
}
