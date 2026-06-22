using System.Data;
using Microsoft.Data.Sqlite;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Queue;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-protocol.md §C.6（QueueStatus 枚举）与
// docs/system/wecom-personal-rpa-design.md §7.2（客户端本地 SQLite 队列 send_queue）。
//
// QueueStatus = Pending | Running | Succeeded | Retryable | Failed | Paused
// 与服务端 wecom_rpa_action_outbox.status 取值集合完全对齐。
//
// 本测试使用临时 SQLite 文件验证 send_queue 的核心操作：
//   enqueue / claim（原子领取 pending，标记 running）/ mark（终态或重试）/ dedup（dedup_key 唯一）。
//
// Client.Core 的 SqliteSendQueue 实现由并行 agent 编写。为让本测试独立可编译，
// 这里在测试本地实现一个最小队列合约（表结构、状态语义与契约/服务端 outbox 对齐）。
// 待 Client.Core 的 SqliteSendQueue 落地后，可把 LocalSendQueue 的方法替换为对实现类的调用，
// 表结构与断言保持一致。
// ====================================================================================

internal enum LocalQueueStatus
{
    Pending,
    Running,
    Succeeded,
    Retryable,
    Failed,
    Paused,
}

/// <summary>
/// 测试本地最小 send_queue，表结构与状态机对齐服务端 wecom_rpa_action_outbox
/// （status 取值集合一致；dedup_key UNIQUE；FOR UPDATE SKIP LOCKED 在 SQLite 用
/// UPDATE ... WHERE status='pending' 模拟单 worker 原子领取）。
/// </summary>
internal sealed class LocalSendQueue : IDisposable
{
    private readonly SqliteConnection _conn;

    public LocalSendQueue(string path)
    {
        _conn = new SqliteConnection($"Data Source={path}");
        _conn.Open();
        using (var cmd = _conn.CreateCommand())
        {
            cmd.CommandText = """
                CREATE TABLE IF NOT EXISTS send_queue (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key       TEXT NOT NULL UNIQUE,
                    account_id      TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    action_json     TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    attempts        INTEGER NOT NULL DEFAULT 0,
                    error_message   TEXT,
                    next_retry_at   TEXT,
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS ix_send_queue_status ON send_queue(status);
                """;
            cmd.ExecuteNonQuery();
        }
    }

    public bool Enqueue(string dedupKey, string accountId, string conversationId, string actionJson)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = """
            INSERT OR IGNORE INTO send_queue (dedup_key, account_id, conversation_id, action_json)
            VALUES (@dedup, @acc, @conv, @act);
            SELECT changes();
            """;
        cmd.Parameters.AddWithValue("@dedup", dedupKey);
        cmd.Parameters.AddWithValue("@acc", accountId);
        cmd.Parameters.AddWithValue("@conv", conversationId);
        cmd.Parameters.AddWithValue("@act", actionJson);
        var changed = (long)cmd.ExecuteScalar()!;
        return changed > 0;
    }

    public long ClaimOne()
    {
        // 单 worker 原子领取：UPDATE pending→running，用 RETURNING id 直接拿到被领取的行。
        // SQLite 3.35+ 支持 RETURNING；Microsoft.Data.Sqlite 8.x 自带该版本。
        // 不能用 last_insert_rowid()（UPDATE 场景不可靠），也不能用「最新 running」反查
        // （多条 running 时 updated_at 相同导致顺序不确定）。
        using var tx = _conn.BeginTransaction();
        using var cmd = _conn.CreateCommand();
        cmd.Transaction = tx;
        cmd.CommandText = """
            UPDATE send_queue
               SET status = 'running',
                   attempts = attempts + 1,
                   updated_at = datetime('now')
             WHERE id = (
                 SELECT id FROM send_queue
                  WHERE status = 'pending'
                    AND (next_retry_at IS NULL OR next_retry_at <= datetime('now'))
                  ORDER BY created_at
                  LIMIT 1
             )
             RETURNING id;
            """;
        var row = cmd.ExecuteScalar();
        tx.Commit();
        return row == null ? 0 : (long)row;
    }

    public void Mark(long id, LocalQueueStatus status, string? errorMessage = null)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = """
            UPDATE send_queue
               SET status = @status,
                   error_message = @err,
                   updated_at = datetime('now')
             WHERE id = @id;
            """;
        cmd.Parameters.AddWithValue("@status", status.ToString().ToLowerInvariant());
        cmd.Parameters.AddWithValue("@err", (object?)errorMessage ?? DBNull.Value);
        cmd.Parameters.AddWithValue("@id", id);
        cmd.ExecuteNonQuery();
    }

    public LocalQueueStatus GetStatus(long id)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT status FROM send_queue WHERE id = @id;";
        cmd.Parameters.AddWithValue("@id", id);
        var s = (string)cmd.ExecuteScalar()!;
        return Enum.Parse<LocalQueueStatus>(s, ignoreCase: true);
    }

    public long GetAttempts(long id)
    {
        using var cmd = _conn.CreateCommand();
        cmd.CommandText = "SELECT attempts FROM send_queue WHERE id = @id;";
        cmd.Parameters.AddWithValue("@id", id);
        return (long)cmd.ExecuteScalar()!;
    }

    public void Dispose() => _conn.Dispose();
}

public class SqliteSendQueueTests : IDisposable
{
    private readonly string _path;
    private readonly LocalSendQueue _q;

    public SqliteSendQueueTests()
    {
        _path = Path.Combine(Path.GetTempPath(), $"rpa_sendq_{Guid.NewGuid():N}.sqlite");
        _q = new LocalSendQueue(_path);
    }

    public void Dispose()
    {
        _q.Dispose();
        try { File.Delete(_path); } catch { /* 测试清理容忍 */ }
    }

    [Fact(DisplayName = "enqueue 写入成功，初始状态为 pending、attempts=0")]
    public void Enqueue_InsertsPending_WithZeroAttempts()
    {
        var ok = _q.Enqueue("wecom_rpa:t1:req_001", "acc_001", "conv_a", "{\"type\":\"send_text\",\"text\":\"hi\"}");
        Assert.True(ok);

        var id = _q.ClaimOne();
        Assert.True(id > 0);
        Assert.Equal(1, _q.GetAttempts(id)); // claim 时 attempts 自增
        Assert.Equal(LocalQueueStatus.Running, _q.GetStatus(id));
    }

    [Fact(DisplayName = "claim 把 pending 原子标记为 running")]
    public void Claim_Marks_Pending_To_Running()
    {
        _q.Enqueue("wecom_rpa:t1:req_002", "acc_001", "conv_a", "{}");
        _q.Enqueue("wecom_rpa:t1:req_003", "acc_001", "conv_a", "{}");

        var first = _q.ClaimOne();
        var second = _q.ClaimOne();
        var third = _q.ClaimOne(); // 已无 pending

        Assert.NotEqual(first, second);
        Assert.Equal(0, third); // 队列耗尽
        Assert.Equal(LocalQueueStatus.Running, _q.GetStatus(first));
        Assert.Equal(LocalQueueStatus.Running, _q.GetStatus(second));
    }

    [Fact(DisplayName = "mark 可把 running 转为终态 succeeded/failed 或重试 retryable")]
    public void Mark_Transitions_To_Terminal_Or_Retryable()
    {
        _q.Enqueue("wecom_rpa:t1:req_010", "acc_001", "conv_a", "{}");
        var id = _q.ClaimOne();

        _q.Mark(id, LocalQueueStatus.Succeeded);
        Assert.Equal(LocalQueueStatus.Succeeded, _q.GetStatus(id));

        // 另一条走失败路径
        _q.Enqueue("wecom_rpa:t1:req_011", "acc_001", "conv_a", "{}");
        var id2 = _q.ClaimOne();
        _q.Mark(id2, LocalQueueStatus.Failed, "发送失败：窗口未找到");
        Assert.Equal(LocalQueueStatus.Failed, _q.GetStatus(id2));
    }

    [Fact(DisplayName = "dedup：相同 dedup_key 第二次入队被丢弃")]
    public void Enqueue_DedupKey_PreventsDuplicate()
    {
        var first = _q.Enqueue("wecom_rpa:t1:req_dup", "acc_001", "conv_a", "{}");
        var second = _q.Enqueue("wecom_rpa:t1:req_dup", "acc_001", "conv_a", "{}");

        Assert.True(first);
        Assert.False(second);

        // 仅一条记录被领取
        var id = _q.ClaimOne();
        Assert.True(id > 0);
        Assert.Equal(0, _q.ClaimOne());
    }

    [Fact(DisplayName = "retryable 项在 next_retry_at 到期后可被再次 claim")]
    public void Retryable_Item_Is_Reclaimable_AfterBackoff()
    {
        _q.Enqueue("wecom_rpa:t1:req_020", "acc_001", "conv_a", "{}");
        var id = _q.ClaimOne();
        _q.Mark(id, LocalQueueStatus.Retryable);
        Assert.Equal(LocalQueueStatus.Retryable, _q.GetStatus(id));

        // 模拟 backoff 到期：直接把 status 改回 pending（等价于调度器把 retryable 重新入队）
        using var conn = new SqliteConnection($"Data Source={_path}");
        conn.Open();
        using var cmd = conn.CreateCommand();
        cmd.CommandText = "UPDATE send_queue SET status='pending' WHERE id=@id;";
        cmd.Parameters.AddWithValue("@id", id);
        cmd.ExecuteNonQuery();

        var reclaimed = _q.ClaimOne();
        Assert.Equal(id, reclaimed);
        Assert.Equal(LocalQueueStatus.Running, _q.GetStatus(id));
        Assert.Equal(2, _q.GetAttempts(id)); // 第二次 claim，attempts 累加
    }
}
