using System.Globalization;
using Dapper;
using Microsoft.Data.Sqlite;

namespace WeCom.PersonalRpa.Core.Queue;

/// <summary>
/// 基于 SQLite 的本地出站动作队列实现（Dapper + Microsoft.Data.Sqlite）。
/// 建表 IF NOT EXISTS 保证幂等；dedup_key 唯一约束保证入队去重。
/// </summary>
public sealed class SqliteSendQueue : ISendQueue
{
    private readonly string _connectionString;

    /// <summary>构造队列。</summary>
    /// <param name="dbPath">SQLite 数据库文件绝对路径。</param>
    public SqliteSendQueue(string dbPath)
    {
        if (string.IsNullOrEmpty(dbPath)) throw new ArgumentNullException(nameof(dbPath));
        var dir = Path.GetDirectoryName(dbPath);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
        }.ToString();

        EnsureTableCreated();
    }

    private void EnsureTableCreated()
    {
        const string sql = """
            CREATE TABLE IF NOT EXISTS send_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action_index INTEGER NOT NULL,
                action_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                dedup_key TEXT NOT NULL,
                next_retry_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dedup_key)
            );
            CREATE INDEX IF NOT EXISTS idx_send_actions_status ON send_actions(status);
            CREATE INDEX IF NOT EXISTS idx_send_actions_account ON send_actions(account_id);
            CREATE INDEX IF NOT EXISTS idx_send_actions_next_retry ON send_actions(next_retry_at);
            """;
        using var conn = OpenConnection();
        conn.Execute(sql);
    }

    private SqliteConnection OpenConnection()
    {
        var conn = new SqliteConnection(_connectionString);
        conn.Open();
        return conn;
    }

    private static string FormatUtc(DateTimeOffset dto) => dto.UtcDateTime.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
    private static DateTimeOffset? ParseDto(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        if (DateTimeOffset.TryParseExact(s, "yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal, out var dto))
        {
            return dto.ToUniversalTime();
        }
        return DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out dto) ? dto : null;
    }

    private static readonly string[] StatusStringMap =
    {
        "pending", "running", "succeeded", "retryable", "failed", "paused",
    };

    private static string ToString(QueueStatus s) => StatusStringMap[(int)s];
    private static QueueStatus ToStatus(string s) => s switch
    {
        "pending" => QueueStatus.Pending,
        "running" => QueueStatus.Running,
        "succeeded" => QueueStatus.Succeeded,
        "retryable" => QueueStatus.Retryable,
        "failed" => QueueStatus.Failed,
        "paused" => QueueStatus.Paused,
        _ => QueueStatus.Pending,
    };

    private static SendAction MapRow(dynamic r) => new SendAction
    {
        Id = (long)r.id,
        RequestId = (string)r.request_id,
        AccountId = (string)r.account_id,
        ConversationId = (string)r.conversation_id,
        SessionId = (string)r.session_id,
        ActionIndex = (int)r.action_index,
        ActionJson = (string)r.action_json,
        Status = ToStatus((string)r.status),
        Attempts = (int)r.attempts,
        DedupKey = (string)r.dedup_key,
        NextRetryAt = ParseDto(r.next_retry_at is null ? null : (string)r.next_retry_at),
        ErrorMessage = r.error_message is null ? null : (string)r.error_message,
        CreatedAt = ParseDto((string)r.created_at) ?? DateTimeOffset.UtcNow,
        UpdatedAt = ParseDto((string)r.updated_at) ?? DateTimeOffset.UtcNow,
    };

    /// <inheritdoc />
    public async Task<SendAction?> EnqueueAsync(string requestId, string accountId, string conversationId,
        string sessionId, int actionIndex, string actionJson, string dedupKey,
        CancellationToken cancellationToken = default)
    {
        const string sql = """
            INSERT INTO send_actions
                (request_id, account_id, conversation_id, session_id, action_index,
                 action_json, status, attempts, dedup_key, next_retry_at, error_message,
                 created_at, updated_at)
            VALUES (@RequestId, @AccountId, @ConversationId, @SessionId, @ActionIndex,
                    @ActionJson, 'pending', 0, @DedupKey, NULL, NULL,
                    @Now, @Now)
            ON CONFLICT(dedup_key) DO NOTHING
            RETURNING *;
            """;
        var now = FormatUtc(DateTimeOffset.UtcNow);
        using var conn = OpenConnection();
        using var tx = conn.BeginTransaction();
        var row = await conn.QueryFirstOrDefaultAsync<dynamic>(
            new CommandDefinition(sql, new
            {
                RequestId = requestId,
                AccountId = accountId,
                ConversationId = conversationId,
                SessionId = sessionId,
                ActionIndex = actionIndex,
                ActionJson = actionJson,
                DedupKey = dedupKey,
                Now = now,
            }, tx, cancellationToken: cancellationToken));
        tx.Commit();

        if (row is null)
        {
            // dedup_key 冲突，未入队
            return null;
        }

        return MapRow(row);
    }

    /// <inheritdoc />
    public async Task<List<SendAction>> ClaimPendingAsync(int limit, CancellationToken cancellationToken = default)
    {
        const string sql = """
            UPDATE send_actions
            SET status = 'running',
                attempts = attempts + 1,
                updated_at = @Now
            WHERE id IN (
                SELECT id FROM send_actions
                WHERE status = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= @NowText)
                ORDER BY created_at
                LIMIT @Limit
            )
            RETURNING *;
            """;
        var now = DateTimeOffset.UtcNow;
        var nowText = FormatUtc(now);
        using var conn = OpenConnection();
        using var tx = conn.BeginTransaction();
        var rows = await conn.QueryAsync<dynamic>(
            new CommandDefinition(sql, new { Now = nowText, NowText = nowText, Limit = limit }, tx,
                cancellationToken: cancellationToken));
        tx.Commit();

        return rows.Select(MapRow).ToList();
    }

    /// <inheritdoc />
    public async Task<bool> MarkAsync(long id, QueueStatus status, string? errorMessage = null,
        DateTimeOffset? nextRetryAt = null, CancellationToken cancellationToken = default)
    {
        const string sql = """
            UPDATE send_actions
            SET status = @Status,
                error_message = @ErrorMessage,
                next_retry_at = @NextRetryAt,
                updated_at = @Now
            WHERE id = @Id;
            """;
        var nowText = FormatUtc(DateTimeOffset.UtcNow);
        var statusStr = ToString(status);
        var nextRetryText = nextRetryAt.HasValue ? FormatUtc(nextRetryAt.Value) : (string?)null;
        using var conn = OpenConnection();
        using var tx = conn.BeginTransaction();
        var affected = await conn.ExecuteAsync(
            new CommandDefinition(sql, new
            {
                Id = id,
                Status = statusStr,
                ErrorMessage = errorMessage,
                NextRetryAt = nextRetryText,
                Now = nowText,
            }, tx, cancellationToken: cancellationToken));
        tx.Commit();
        return affected > 0;
    }

    /// <inheritdoc />
    public async Task<List<SendAction>> ListByAccountAsync(string accountId, int limit = 100,
        CancellationToken cancellationToken = default)
    {
        const string sql = """
            SELECT * FROM send_actions
            WHERE account_id = @AccountId
            ORDER BY created_at DESC
            LIMIT @Limit;
            """;
        using var conn = OpenConnection();
        var rows = await conn.QueryAsync<dynamic>(
            new CommandDefinition(sql, new { AccountId = accountId, Limit = limit },
                cancellationToken: cancellationToken));
        return rows.Select(MapRow).ToList();
    }
}
