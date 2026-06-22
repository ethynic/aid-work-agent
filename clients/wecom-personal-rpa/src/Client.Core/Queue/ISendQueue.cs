namespace WeCom.PersonalRpa.Core.Queue;

/// <summary>
/// 出站动作队列接口（protocol.md §C.4）。Core 定义，本地 SQLite 实现。
/// </summary>
public interface ISendQueue
{
    /// <summary>
    /// 入队一条出站动作。dedup_key 唯一约束保证幂等。
    /// </summary>
    /// <param name="requestId">对应 ActionEnvelope.request_id。</param>
    /// <param name="accountId">目标账号 ID。</param>
    /// <param name="conversationId">客户端侧会话标识。</param>
    /// <param name="sessionId">服务端会话 ID。</param>
    /// <param name="actionIndex">action 下标。</param>
    /// <param name="actionJson">单个 RpaAction 的序列化 JSON。</param>
    /// <param name="dedupKey">幂等去重键。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>若因 dedup_key 冲突未入队，返回 null；否则返回新入队的队列项。</returns>
    Task<SendAction?> EnqueueAsync(string requestId, string accountId, string conversationId,
        string sessionId, int actionIndex, string actionJson, string dedupKey,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// 原子领取 pending 动作，标记为 Running。多线程并发安全依赖 SQLite 事务。
    /// </summary>
    /// <param name="limit">单次领取上限。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>被领取的队列项列表。</returns>
    Task<List<SendAction>> ClaimPendingAsync(int limit, CancellationToken cancellationToken = default);

    /// <summary>
    /// 更新队列项状态（succeeded / retryable / failed / paused）。
    /// </summary>
    /// <param name="id">队列项主键。</param>
    /// <param name="status">新状态。</param>
    /// <param name="errorMessage">错误说明（脱敏，可空）。</param>
    /// <param name="nextRetryAt">下次重试时间（仅 retryable 需要，可空）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>是否更新成功。</returns>
    Task<bool> MarkAsync(long id, QueueStatus status, string? errorMessage = null,
        DateTimeOffset? nextRetryAt = null, CancellationToken cancellationToken = default);

    /// <summary>
    /// 按账号列出队列项（供管理 / 诊断）。
    /// </summary>
    Task<List<SendAction>> ListByAccountAsync(string accountId, int limit = 100,
        CancellationToken cancellationToken = default);
}
