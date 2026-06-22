namespace WeCom.PersonalRpa.Core.Queue;

/// <summary>
/// 队列项状态枚举（protocol.md §C.6）。本地 SQLite 队列与服务端 outbox 共用语义，
/// 取值集合与服务端 wecom_rpa_action_outbox.status 完全对齐。
/// </summary>
public enum QueueStatus
{
    /// <summary>待执行。</summary>
    Pending,

    /// <summary>执行中。</summary>
    Running,

    /// <summary>执行成功（终态）。</summary>
    Succeeded,

    /// <summary>可重试（等待退避后重试）。</summary>
    Retryable,

    /// <summary>执行失败（终态，转人工）。</summary>
    Failed,

    /// <summary>已暂停（等待恢复）。</summary>
    Paused,
}

/// <summary>
/// 出站动作队列项（protocol.md §C.6）。由本地 SQLite 队列持久化，串行执行。
/// </summary>
public sealed record SendAction
{
    /// <summary>自增主键。</summary>
    public long Id { get; init; }

    /// <summary>对应 ActionEnvelope.request_id。</summary>
    public string RequestId { get; init; } = string.Empty;

    /// <summary>目标账号 ID。</summary>
    public string AccountId { get; init; } = string.Empty;

    /// <summary>客户端侧会话标识，用于定位企微会话窗口。</summary>
    public string ConversationId { get; init; } = string.Empty;

    /// <summary>服务端会话 ID。</summary>
    public string SessionId { get; init; } = string.Empty;

    /// <summary>action_index（actions 列表下标，从 0）。</summary>
    public int ActionIndex { get; init; }

    /// <summary>action JSON（原始序列化后的单个 RpaAction）。</summary>
    public string ActionJson { get; init; } = string.Empty;

    /// <summary>当前队列状态。</summary>
    public QueueStatus Status { get; init; }

    /// <summary>已尝试次数。</summary>
    public int Attempts { get; init; }

    /// <summary>幂等去重键（wecom_rpa:{tenant_id}:{request_id}:{index}）。</summary>
    public string DedupKey { get; init; } = string.Empty;

    /// <summary>下次重试时间（UTC，可空）。</summary>
    public DateTimeOffset? NextRetryAt { get; init; }

    /// <summary>最后错误说明（脱敏，可空）。</summary>
    public string? ErrorMessage { get; init; }

    /// <summary>创建时间（UTC）。</summary>
    public DateTimeOffset CreatedAt { get; init; }

    /// <summary>最后更新时间（UTC）。</summary>
    public DateTimeOffset UpdatedAt { get; init; }
}
