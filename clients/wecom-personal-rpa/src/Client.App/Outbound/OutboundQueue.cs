using System.Globalization;
using System.IO;
using Dapper;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>
/// 出站执行本地持久化队列（基于 SQLite）。
///
/// 设计目的（docs/system/wecom-personal-rpa-client-design.md §F3）：
///   - 服务端通过 WebSocket 推送 ActionEnvelope，客户端入队后由单 Worker 串行消费。
///   - 进程崩溃后重启时通过 ListPendingAsync 恢复未完成的 action（status=pending）。
///   - status=running 但重启时未被标记终态的行视为"悬挂"，下次启动会被一并恢复。
///
/// 表结构：见 OutboundQueueSql。action_id 对应服务端 ActionEnvelope.request_id。
/// 线程安全：用 SemaphoreSlim(1,1) 串行化所有写操作，避免 SQLite "database is locked"。
/// </summary>
public sealed class OutboundQueue
{
    private readonly string _connectionString;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly ILogger<OutboundQueue>? _logger;

    /// <summary>构造队列。会在构造时确保目录与表存在。</summary>
    /// <param name="options">客户端配置（取 Outbound.DbPath）。</param>
    /// <param name="logger">日志（可空）。</param>
    public OutboundQueue(ClientOptions options, ILogger<OutboundQueue>? logger = null)
    {
        if (options is null) throw new ArgumentNullException(nameof(options));
        _logger = logger;

        var dbPath = string.IsNullOrWhiteSpace(options.Outbound.DbPath)
            ? "data/outbox.db"
            : options.Outbound.DbPath;
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
            CREATE TABLE IF NOT EXISTS outbox_local (
                action_id TEXT PRIMARY KEY,
                action_type TEXT NOT NULL,
                conversation_key TEXT NOT NULL,
                text TEXT,
                file_url TEXT,
                local_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status_created ON outbox_local(status, created_at);
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

    /// <summary>入队一个 outbox 项。action_id 已存在时直接返回 false（幂等去重）。</summary>
    public async Task<bool> EnqueueAsync(OutboxItem item, CancellationToken ct = default)
    {
        if (item is null) throw new ArgumentNullException(nameof(item));
        if (string.IsNullOrEmpty(item.ActionId))
            throw new ArgumentException("OutboxItem.ActionId 不能为空", nameof(item));

        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            const string sql = """
                INSERT OR IGNORE INTO outbox_local
                (action_id, action_type, conversation_key, text, file_url, local_path,
                 status, retry_count, error_code, error_message, created_at, updated_at)
                VALUES
                (@ActionId, @ActionType, @ConversationKey, @Text, @FileUrl, @LocalPath,
                 @Status, @RetryCount, @ErrorCode, @ErrorMessage, @CreatedAt, @UpdatedAt);
                SELECT changes();
                """;
            using var conn = OpenConnection();
            var changed = await conn.ExecuteScalarAsync<long>(sql, new
            {
                item.ActionId,
                item.ActionType,
                item.ConversationKey,
                item.Text,
                item.FileUrl,
                item.LocalPath,
                Status = item.Status,
                item.RetryCount,
                item.ErrorCode,
                item.ErrorMessage,
                CreatedAt = FormatIso(item.CreatedAt),
                UpdatedAt = FormatIso(DateTimeOffset.Now),
            }).ConfigureAwait(false);
            return changed > 0;
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>
    /// 取下一个 pending 项（按 created_at ASC），原子 UPDATE status='running' 后返回。
    /// 无 pending 项时返回 null。
    /// </summary>
    public async Task<OutboxItem?> DequeueNextAsync(CancellationToken ct = default)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            // 两步领取：先取下一个 pending 的 action_id，再原子 UPDATE status='running'。
            // SemaphoreSlim 已保证进程内串行；多 worker 跨进程时建议改用 UPDATE...RETURNING（SQLite 3.35+）。
            const string sqlStep1 = """
                SELECT action_id FROM outbox_local
                 WHERE status='pending'
                 ORDER BY created_at ASC
                 LIMIT 1;
                """;
            const string sqlStep2 = """
                UPDATE outbox_local
                   SET status='running', updated_at=@Now
                 WHERE action_id=@ActionId AND status='pending';
                 SELECT * FROM outbox_local WHERE action_id=@ActionId;
                """;
            using var conn = OpenConnection();
            var nextId = await conn.ExecuteScalarAsync<string?>(sqlStep1).ConfigureAwait(false);
            if (string.IsNullOrEmpty(nextId)) return null;

            var now = FormatIso(DateTimeOffset.Now);
            var row = await conn.QueryFirstOrDefaultAsync<dynamic>(
                sqlStep2, new { ActionId = nextId, Now = now }).ConfigureAwait(false);
            return row is null ? null : MapRow(row);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>标记完成：删除该行（避免长期累积；服务端已记录终态）。</summary>
    public async Task MarkDoneAsync(string actionId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(actionId)) throw new ArgumentNullException(nameof(actionId));
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            using var conn = OpenConnection();
            await conn.ExecuteAsync(
                "DELETE FROM outbox_local WHERE action_id=@Id;", new { Id = actionId })
                .ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>
    /// P0-5：把指定 action 重新入队（status='running' → 'pending'），用于暂停场景下
    /// Dispatcher 出队后发现客户端被 PauseState 命中，需要把 item 放回 pending 让下次
    /// Resume 后重新出队执行。仅当当前状态为 running/failed 时改回 pending，已是 pending 则不动。
    /// </summary>
    public async Task RequeueAsync(string actionId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(actionId)) throw new ArgumentNullException(nameof(actionId));
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            const string sql = """
                UPDATE outbox_local
                   SET status='pending', updated_at=@Now
                 WHERE action_id=@Id AND status IN ('running', 'failed');
                """;
            using var conn = OpenConnection();
            await conn.ExecuteAsync(sql, new
            {
                Id = actionId,
                Now = FormatIso(DateTimeOffset.Now),
            }).ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>标记失败：retry_count++，写错误码与说明。仍保留 status='failed'，不再被自动 Dequeue。</summary>
    public async Task MarkFailedAsync(string actionId, string? errorCode, string? errorMessage,
        CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(actionId)) throw new ArgumentNullException(nameof(actionId));
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            const string sql = """
                UPDATE outbox_local
                   SET status='failed',
                       retry_count = retry_count + 1,
                       error_code = @Code,
                       error_message = @Msg,
                       updated_at = @Now
                 WHERE action_id=@Id;
                """;
            using var conn = OpenConnection();
            await conn.ExecuteAsync(sql, new
            {
                Id = actionId,
                Code = errorCode,
                Msg = errorMessage,
                Now = FormatIso(DateTimeOffset.Now),
            }).ConfigureAwait(false);
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>
    /// 列出所有未完成（pending 或 running）的项。供 HostedService 启动时恢复使用。
    /// 重启时仍处于 running 的项视为"悬挂"，统一置为 pending 等待重试。
    /// </summary>
    public async Task<IReadOnlyList<OutboxItem>> ListPendingAsync(CancellationToken ct = default)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            using var conn = OpenConnection();
            // 把悬挂的 running 重置为 pending（同事务内顺序操作）
            await conn.ExecuteAsync(
                "UPDATE outbox_local SET status='pending' WHERE status='running';")
                .ConfigureAwait(false);
            var rows = await conn.QueryAsync<dynamic>(
                "SELECT * FROM outbox_local WHERE status='pending' ORDER BY created_at ASC;")
                .ConfigureAwait(false);
            return rows.Select(MapRow).ToList();
        }
        finally
        {
            _gate.Release();
        }
    }

    private static OutboxItem MapRow(dynamic r) => new()
    {
        ActionId = (string)r.action_id,
        ActionType = (string)r.action_type,
        ConversationKey = (string)r.conversation_key,
        Text = r.text is null ? null : (string?)r.text,
        FileUrl = r.file_url is null ? null : (string?)r.file_url,
        LocalPath = r.local_path is null ? null : (string?)r.local_path,
        Status = (string)r.status,
        RetryCount = (int)r.retry_count,
        ErrorCode = r.error_code is null ? null : (string?)r.error_code,
        ErrorMessage = r.error_message is null ? null : (string?)r.error_message,
        CreatedAt = ParseIso((string)r.created_at) ?? DateTimeOffset.Now,
    };

    private static string FormatIso(DateTimeOffset dto)
        => dto.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);

    private static DateTimeOffset? ParseIso(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        return DateTimeOffset.TryParse(s, CultureInfo.InvariantCulture,
            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var dto) ? dto : null;
    }
}

/// <summary>Outbox 表行映射对象。</summary>
public sealed class OutboxItem
{
    /// <summary>对应服务端 ActionEnvelope.request_id（主键）。</summary>
    public string ActionId { get; set; } = string.Empty;

    /// <summary>动作类型（send_text / send_image / send_file / noop / handoff）。</summary>
    public string ActionType { get; set; } = string.Empty;

    /// <summary>会话定位键（搜索关键词 / 稳定 ID）。</summary>
    public string ConversationKey { get; set; } = string.Empty;

    /// <summary>文本消息内容（send_text 用）。</summary>
    public string? Text { get; set; }

    /// <summary>远程文件 URL（send_image/send_file 用，下载后保留以便重试去重）。</summary>
    public string? FileUrl { get; set; }

    /// <summary>下载到本地的路径（运行时填）。</summary>
    public string? LocalPath { get; set; }

    /// <summary>当前状态（pending / running / done / failed）。done 状态已被删除。</summary>
    public string Status { get; set; } = "pending";

    /// <summary>已重试次数（失败时自增）。</summary>
    public int RetryCount { get; set; }

    /// <summary>失败错误码（透传 PS 错误码）。</summary>
    public string? ErrorCode { get; set; }

    /// <summary>失败说明（脱敏）。</summary>
    public string? ErrorMessage { get; set; }

    /// <summary>入队时间（UTC ISO 8601）。</summary>
    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.Now;
}
