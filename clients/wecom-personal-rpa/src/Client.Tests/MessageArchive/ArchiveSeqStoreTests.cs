using WeCom.PersonalRpa.App.MessageArchive;
using Xunit;

namespace WeCom.PersonalRpa.Tests.MessageArchive;

/// <summary>
/// ArchiveSeqStore SQLite 持久化测试。使用真实临时 DB 文件，验证 seq 跨实例持久化。
/// </summary>
public sealed class ArchiveSeqStoreTests : IDisposable
{
    private readonly string _dbPath;
    private readonly ArchiveSeqStore _store;

    public ArchiveSeqStoreTests()
    {
        _dbPath = Path.Combine(Path.GetTempPath(), $"archive-seq-{Guid.NewGuid():N}.db");
        _store = new ArchiveSeqStore(_dbPath);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task GetSeqAsync_NewAccount_ReturnsZero()
    {
        var seq = await _store.GetSeqAsync("test-account", CancellationToken.None);
        Assert.Equal(0, seq);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task SetSeqAsync_PersistsAcrossInstances()
    {
        await _store.SetSeqAsync("acct1", 12345, CancellationToken.None);

        // 关掉旧实例，开新实例读同一个 DB 文件
        var store2 = new ArchiveSeqStore(_dbPath);
        var seq = await store2.GetSeqAsync("acct1", CancellationToken.None);
        Assert.Equal(12345, seq);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task SetSeqAsync_Upsert_UpdatesExisting()
    {
        await _store.SetSeqAsync("acct", 100, CancellationToken.None);
        await _store.SetSeqAsync("acct", 200, CancellationToken.None);
        var seq = await _store.GetSeqAsync("acct", CancellationToken.None);
        Assert.Equal(200, seq);
    }

    public void Dispose()
    {
        try { if (File.Exists(_dbPath)) File.Delete(_dbPath); } catch { /* ignore */ }
    }
}
