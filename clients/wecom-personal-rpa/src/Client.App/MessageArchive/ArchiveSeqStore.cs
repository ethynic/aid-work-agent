using System.Globalization;
using System.IO;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Logging;

namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 会话存档 seq 游标持久化（Phase 3 块 D）。
///
/// 企微 get_chat_data 按 seq（单调递增）拉取增量，客户端必须在本地持久化上次拉到的最大 seq，
/// 避免重启后从 0 重拉导致已处理消息重复触发。SQLite 表 archive_seq：
///   account_id TEXT PRIMARY KEY
///   seq INTEGER NOT NULL
///   updated_at TEXT NOT NULL (ISO 8601)
///
/// 用 Microsoft.Data.Sqlite（已通过 Client.Core 间接引用），SemaphoreSlim(1,1) 串行化写。
/// DB 路径默认相对客户端根目录 data/archive.db。
/// </summary>
public sealed class ArchiveSeqStore
{
    private const string Tag = "ArchiveSeqStore";
    private readonly string _connectionString;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly ILogger<ArchiveSeqStore>? _logger;

    public ArchiveSeqStore(string dbPath, ILogger<ArchiveSeqStore>? logger = null)
    {
        if (string.IsNullOrEmpty(dbPath)) throw new ArgumentNullException(nameof(dbPath));
        var dir = Path.GetDirectoryName(dbPath);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

        _connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = dbPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
        }.ToString();
        _logger = logger;
        EnsureTable();
    }

    private void EnsureTable()
    {
        using var conn = new SqliteConnection(_connectionString);
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = """
            CREATE TABLE IF NOT EXISTS archive_seq (
                account_id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """;
        cmd.ExecuteNonQuery();
    }

    /// <summary>读取 account 上次持久化的 seq；不存在返回 0。</summary>
    public async Task<long> GetSeqAsync(string accountId, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(accountId)) throw new ArgumentNullException(nameof(accountId));
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync(ct).ConfigureAwait(false);
            using var cmd = conn.CreateCommand();
            cmd.CommandText = "SELECT seq FROM archive_seq WHERE account_id = @id";
            cmd.Parameters.AddWithValue("@id", accountId);
            var result = await cmd.ExecuteScalarAsync(ct).ConfigureAwait(false);
            if (result is long l) return l;
            if (result is int i) return (long)i;
            return 0;
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>更新 account 的 seq（UPSERT）。</summary>
    public async Task SetSeqAsync(string accountId, long seq, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(accountId)) throw new ArgumentNullException(nameof(accountId));
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            using var conn = new SqliteConnection(_connectionString);
            await conn.OpenAsync(ct).ConfigureAwait(false);
            using var cmd = conn.CreateCommand();
            cmd.CommandText = """
                INSERT INTO archive_seq (account_id, seq, updated_at)
                VALUES (@id, @seq, @now)
                ON CONFLICT(account_id) DO UPDATE SET seq = @seq, updated_at = @now;
                """;
            cmd.Parameters.AddWithValue("@id", accountId);
            cmd.Parameters.AddWithValue("@seq", seq);
            cmd.Parameters.AddWithValue("@now", DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture));
            await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
            _logger?.LogDebug("[{Tag}] seq 更新 account={Aid} seq={Seq}", Tag, accountId, seq);
        }
        finally
        {
            _gate.Release();
        }
    }
}
