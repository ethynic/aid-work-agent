using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using WeCom.PersonalRpa.App.MessageArchive;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.MessageArchive;

/// <summary>
/// ChatArchiveListener 单元测试。
/// 通过 fetchBatchOverride delegate 注入 mock 批次，跳过 ArchiveHttpClient（避免企微域名调用）。
/// ArchiveCryptoService 用真实密钥对自加密自解密，覆盖端到端解密路径。
/// </summary>
public sealed class ChatArchiveListenerTests : IDisposable
{
    private readonly string _dbPath;
    private readonly ArchiveSeqStore _seqStore;
    private readonly string _privatePem;
    private readonly byte[] _randomKey;
    private readonly ArchiveCryptoService _crypto;

    public ChatArchiveListenerTests()
    {
        _dbPath = Path.Combine(Path.GetTempPath(), $"archive-listener-{Guid.NewGuid():N}.db");
        _seqStore = new ArchiveSeqStore(_dbPath);

        using var rsa = RSA.Create(2048);
        _privatePem = ExportPrivateKeyPem(rsa);
        _randomKey = RandomNumberGenerator.GetBytes(32);
        _crypto = new ArchiveCryptoService();
    }

    private ChatArchiveListener CreateListener(
        Func<string, long, int, CancellationToken, Task<ChatDataBatch>> fetchImpl)
    {
        // 构造一个真实 ArchiveHttpClient（不会真发请求：fetchBatchOverride + getTokenOverride
        // 完全覆盖了 PullBatch 的全部外部调用路径）。
        var http = new ArchiveHttpClient(new HttpClient());
        return new ChatArchiveListener(
            http: http,
            crypto: _crypto,
            seqStore: _seqStore,
            opts: new ArchiveOptions
            {
                Corpid = "test-corpid",
                Secret = "test-secret",
                PrivateKeyPath = "<test>",
                PollIntervalSeconds = 1,
                BatchLimit = 100,
            },
            accountId: "test-account",
            loadPrivateKey: () => _privatePem,
            resolveSecret: () => "test-secret",
            fetchBatchOverride: fetchImpl,
            getTokenOverride: _ => Task.FromResult("fake-token"));
    }

    /// <summary>构造一条 encrypt_random_key 已用真实公钥加密、encrypt_chat_msg 用 AES 加密的密文条目。</summary>
    private ChatDataItem BuildEncryptedItem(
        long seq, string action, string msgtype,
        string from = "u1", string msgId = "mid1",
        Action<Dictionary<string, object>>? addPayload = null,
        string text = "hello")
    {
        using var rsa = RSA.Create(2048);
        rsa.ImportFromPem(_privatePem);
        var cipherKey = rsa.Encrypt(_randomKey, RSAEncryptionPadding.OaepSHA1);

        var payload = new Dictionary<string, object> { ["msgtype"] = msgtype };
        if (msgtype == "text")
        {
            payload["text"] = new { content = text };
        }
        else if (msgtype is "image" or "file" or "voice" or "video")
        {
            payload[msgtype] = new { sdkfileid = "sfid-1", filename = "f.bin" };
        }
        addPayload?.Invoke(payload);

        var plainJson = System.Text.Json.JsonSerializer.Serialize(payload);
        var plainBytes = Encoding.UTF8.GetBytes(plainJson);

        var iv = RandomNumberGenerator.GetBytes(16);
        using var aes = Aes.Create();
        aes.Mode = CipherMode.CBC;
        aes.Padding = PaddingMode.PKCS7;
        aes.KeySize = 256;
        aes.Key = _randomKey;
        aes.IV = iv;
        using var enc = aes.CreateEncryptor();
        var cipher = enc.TransformFinalBlock(plainBytes, 0, plainBytes.Length);
        var combined = new byte[iv.Length + cipher.Length];
        Array.Copy(iv, 0, combined, 0, iv.Length);
        Array.Copy(cipher, 0, combined, iv.Length, cipher.Length);

        return new ChatDataItem
        {
            Seq = seq,
            MsgId = msgId,
            Action = action,
            From = from,
            ToList = new List<string>(),
            RoomId = null,
            MsgTime = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            MsgType = msgtype,
            EncryptRandomKey = Convert.ToBase64String(cipherKey),
            EncryptChatMsg = Convert.ToBase64String(combined),
        };
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task PollOnce_NewMessages_FiresNewMessageReceivedForEach()
    {
        var items = new List<ChatDataItem>
        {
            BuildEncryptedItem(101, "upload", "text", msgId: "m1"),
            BuildEncryptedItem(102, "upload", "text", msgId: "m2", text: "second"),
        };
        Task<ChatDataBatch> BatchOf(string _t, long seq, int _l, CancellationToken _c) =>
            Task.FromResult(new ChatDataBatch { Items = items });

        var listener = CreateListener(BatchOf);
        var received = new List<ArchiveMessage>();
        listener.NewMessageReceived += (_, e) => received.Add(e.Message);

        await listener.PollOnceAsync(CancellationToken.None);

        Assert.Equal(2, received.Count);
        Assert.Equal("m1", received[0].MsgId);
        Assert.Equal("hello", received[0].Text);
        Assert.Equal("m2", received[1].MsgId);
        Assert.Equal("second", received[1].Text);

        var seq = await _seqStore.GetSeqAsync("test-account", CancellationToken.None);
        Assert.Equal(102, seq); // 推进到本批次最后一条
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task PollOnce_DownloadAction_DoesNotFire()
    {
        var items = new List<ChatDataItem>
        {
            BuildEncryptedItem(201, "download", "text", msgId: "m1"),
            BuildEncryptedItem(202, "upload", "text", msgId: "m2"),
        };
        Task<ChatDataBatch> BatchOf(string _t, long seq, int _l, CancellationToken _c) =>
            Task.FromResult(new ChatDataBatch { Items = items });

        var listener = CreateListener(BatchOf);
        var received = new List<ArchiveMessage>();
        listener.NewMessageReceived += (_, e) => received.Add(e.Message);

        await listener.PollOnceAsync(CancellationToken.None);

        Assert.Single(received);
        Assert.Equal("m2", received[0].MsgId);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task PollOnce_DecryptFailure_PartiallyAdvancesSeq()
    {
        // P0-1：逐条推进 seq。第一条成功 → seq 推进到 301；
        //       第二条解密失败 → 不推进当前条（302），break 退出。
        //       重拉时只会重新拉取 302，不会重复触发 301。
        var items = new List<ChatDataItem>
        {
            BuildEncryptedItem(301, "upload", "text", msgId: "m1"),
            new ChatDataItem
            {
                Seq = 302, MsgId = "bad", Action = "upload", MsgType = "text",
                EncryptRandomKey = "!!not-base64!!", // 触发解密异常
                EncryptChatMsg = "AAAA",
            },
        };
        Task<ChatDataBatch> BatchOf(string _t, long seq, int _l, CancellationToken _c) =>
            Task.FromResult(new ChatDataBatch { Items = items });

        var listener = CreateListener(BatchOf);
        var received = new List<ArchiveMessage>();
        listener.NewMessageReceived += (_, e) => received.Add(e.Message);

        await listener.PollOnceAsync(CancellationToken.None);

        // 第一条成功触发；第二条解密失败后 break
        Assert.Single(received);
        Assert.Equal("m1", received[0].MsgId);
        // 第一条已推进 seq 到 301；第二条失败未推进（下次重拉只会拉到 302）
        var seq = await _seqStore.GetSeqAsync("test-account", CancellationToken.None);
        Assert.Equal(301, seq);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public async Task PollOnce_Success_AdvancesSeq()
    {
        var items = new List<ChatDataItem>
        {
            BuildEncryptedItem(401, "upload", "image", msgId: "img1"),
        };
        Task<ChatDataBatch> BatchOf(string _t, long seq, int _l, CancellationToken _c) =>
            Task.FromResult(new ChatDataBatch { Items = items });

        var listener = CreateListener(BatchOf);
        var received = new List<ArchiveMessage>();
        listener.NewMessageReceived += (_, e) => received.Add(e.Message);

        await listener.PollOnceAsync(CancellationToken.None);

        Assert.Single(received);
        Assert.Equal("img1", received[0].MsgId);
        Assert.Equal("sfid-1", received[0].MediaSdkFileId);
        Assert.Equal("f.bin", received[0].MediaFileName);

        var seq = await _seqStore.GetSeqAsync("test-account", CancellationToken.None);
        Assert.Equal(401, seq);
    }

    private static string ExportPrivateKeyPem(RSA rsa)
    {
        var der = rsa.ExportRSAPrivateKey();
        var b64 = Convert.ToBase64String(der);
        var sb = new StringBuilder();
        sb.AppendLine("-----BEGIN RSA PRIVATE KEY-----");
        for (int i = 0; i < b64.Length; i += 64)
        {
            sb.AppendLine(b64.Substring(i, Math.Min(64, b64.Length - i)));
        }
        sb.AppendLine("-----END RSA PRIVATE KEY-----");
        return sb.ToString();
    }

    public void Dispose()
    {
        try { if (File.Exists(_dbPath)) File.Delete(_dbPath); } catch { /* ignore */ }
    }
}
