using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using WeCom.PersonalRpa.App.Inbound;
using WeCom.PersonalRpa.App.MessageArchive;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Inbound;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-client-design.md §F6 / protocol.md §A.3
//
// 字段映射：
//   - MsgId            → event_id（"msg_" + msgid，复用 archive msgid 做幂等）
//   - From             → sender_stable_id + sender_display_name
//   - MsgType          → message_type
//   - Text             → text
//   - RoomId           → conversation_id + external_group
//   - 单聊              → conversation_id = "{from}_{tolist[0]}" + external_user
//   - MediaSdkFileId   → ArchiveMediaDownloader.Download → UploadMediaAsync → URL 回填 → 删本地
// ====================================================================================

/// <summary>
/// IAgentApiClient stub：仅实现 BuildAsync 路径用到的 UploadMediaAsync。
/// </summary>
internal sealed class BuilderStubApiClient : IAgentApiClient
{
    public string UploadedUrl { get; set; } = "https://signed-url.example.com/media/abc.jpeg";
    public List<string> UploadedLocalPaths { get; } = new();

    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
    {
        UploadedLocalPaths.Add(localPath);
        return Task.FromResult(UploadedUrl);
    }

    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success, string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default) => throw new NotSupportedException();
    public void Dispose() { }
}

public sealed class InboundEventBuilderTests
{
    private const string AccountId = "acct-test";

    private static ClientOptions BuildOptions()
    {
        return new ClientOptions
        {
            ClientId = AccountId,
            MessageSource = new ArchiveOptions
            {
                Corpid = "test-corpid",
                Secret = "test-secret",
            },
        };
    }

    private static ArchiveMessage BuildText(string from = "ext_u1", string? roomId = null,
        string? text = "hello", List<string>? toList = null)
    {
        return new ArchiveMessage
        {
            MsgId = "mid-1",
            Action = "upload",
            From = from,
            MsgType = "text",
            MsgTime = 1717000000L,
            Text = text,
            RoomId = roomId,
            ToList = toList ?? new List<string>(),
        };
    }

    private static InboundEventBuilder CreateBuilder(
        ArchiveHttpClient? http = null,
        ArchiveMediaDownloader? downloader = null,
        BuilderStubApiClient? api = null,
        ClientOptions? opts = null)
    {
        http ??= new ArchiveHttpClient(new HttpClient());
        api ??= new BuilderStubApiClient();
        opts ??= BuildOptions();
        downloader ??= new ArchiveMediaDownloader(http, opts.MessageSource, NullLogger<ArchiveMediaDownloader>.Instance);
        return new InboundEventBuilder(http, downloader, api, opts, AccountId, NullLogger<InboundEventBuilder>.Instance);
    }

    [Fact]
    public async Task BuildAsync_TextMessage_MapsFields()
    {
        var builder = CreateBuilder();
        var msg = BuildText(from: "ext_alice", text: "hi", toList: new List<string> { "ext_bob" });

        var evt = await builder.BuildAsync(msg);

        Assert.Equal("msg_mid-1", evt.EventId);
        Assert.Equal(AccountId, evt.AccountId);
        Assert.Equal(EventType.Message, evt.EventType);
        Assert.Equal(DateTimeOffset.FromUnixTimeSeconds(1717000000L), evt.OccurredAt);

        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Equal("ext_alice", payload.SenderStableId);
        Assert.Equal("ext_alice", payload.SenderDisplayName);
        Assert.Equal(InboundMessageType.Text, payload.MessageType);
        Assert.Equal("hi", payload.Text);
        Assert.Empty(payload.Attachments);
    }

    [Fact]
    public async Task BuildAsync_GroupMessage_SetsGroupConversationType()
    {
        var builder = CreateBuilder();
        var msg = BuildText(roomId: "rid-group-1");

        var evt = await builder.BuildAsync(msg);

        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Equal("rid-group-1", payload.ConversationId);
        Assert.Equal(ConversationType.ExternalGroup, payload.ConversationType);
    }

    [Fact]
    public async Task BuildAsync_SingleChat_SetsExternalUserConversationType()
    {
        var builder = CreateBuilder();
        var msg = BuildText(from: "alice", toList: new List<string> { "bob" });

        var evt = await builder.BuildAsync(msg);

        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Equal("alice_bob", payload.ConversationId);
        Assert.Equal(ConversationType.ExternalUser, payload.ConversationType);
    }

    [Fact]
    public async Task BuildAsync_TextMessage_NoAttachments()
    {
        var builder = CreateBuilder();
        var msg = BuildText();

        var evt = await builder.BuildAsync(msg);

        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Empty(payload.Attachments);
    }

    [Fact]
    public async Task BuildAsync_ImageMessage_DownloadsAndUploadsMedia_ThenDeletesLocal()
    {
        // 准备：临时本地文件模拟下载产物
        var tempDir = Path.Combine(Path.GetTempPath(), $"inbound-builder-{Guid.NewGuid():N}");
        Directory.CreateDirectory(tempDir);
        var localFile = Path.Combine(tempDir, "fake-image.jpeg");
        await File.WriteAllTextAsync(localFile, "fake-image-bytes");

        // downloader：返回预制本地路径，跳过真实下载
        var downloader = new ArchiveMediaDownloader(
            new ArchiveHttpClient(new HttpClient()),
            new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance,
            downloadImpl: (_, _, _) => Task.FromResult(localFile));

        var api = new BuilderStubApiClient();
        var opts = BuildOptions();
        var http = new ArchiveHttpClient(new HttpClient());

        // 通过 tokenOverride 注入预制 token，绕过 ArchiveHttpClient 真实 HTTP 调用
        var builder = new InboundEventBuilder(
            http,
            downloader,
            api,
            opts,
            AccountId,
            NullLogger<InboundEventBuilder>.Instance,
            tokenOverride: _ => Task.FromResult("fake-token"));

        var msg = new ArchiveMessage
        {
            MsgId = "mid-img",
            Action = "upload",
            From = "ext_alice",
            MsgType = "image",
            MsgTime = 1717000000L,
            RoomId = "rid-1",
            MediaSdkFileId = "sfid-1",
            MediaFileName = "photo.jpeg",
        };

        var evt = await builder.BuildAsync(msg);

        Assert.Equal("msg_mid-img", evt.EventId);
        Assert.Equal(EventType.Message, evt.EventType);
        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Equal(InboundMessageType.Image, payload.MessageType);
        Assert.Equal("rid-1", payload.ConversationId);
        Assert.Equal(ConversationType.ExternalGroup, payload.ConversationType);

        // 附件完整链路验证
        Assert.Single(payload.Attachments);
        var att = payload.Attachments[0];
        Assert.Equal("image", att.Type);
        Assert.Equal(api.UploadedUrl, att.Url);
        Assert.Equal("photo.jpeg", att.Name);
        Assert.Equal("image/jpeg", att.MimeType);
        Assert.True(att.Size > 0, "Size 应反映本地文件字节数");

        // 验证：UploadMediaAsync 被调用一次（本地路径被消费）
        Assert.Single(api.UploadedLocalPaths);

        // 验证：本地副本已删除
        Assert.False(File.Exists(localFile), "本地临时媒体文件应在 UploadMedia 后被删除");

        // 清理
        try { if (Directory.Exists(tempDir)) Directory.Delete(tempDir, true); } catch { /* ignore */ }
    }

    [Fact]
    public async Task BuildAsync_MediaDownloadFailure_AttachmentSkippedButTextReported()
    {
        // downloader：抛异常模拟下载失败
        var downloader = new ArchiveMediaDownloader(
            new ArchiveHttpClient(new HttpClient()),
            new ArchiveOptions(),
            NullLogger<ArchiveMediaDownloader>.Instance,
            downloadImpl: (_, _, _) => throw new InvalidOperationException("download failed"));

        var api = new BuilderStubApiClient();
        var builder = CreateBuilder(downloader: downloader, api: api);

        var msg = new ArchiveMessage
        {
            MsgId = "mid-img-2",
            Action = "upload",
            From = "ext_alice",
            MsgType = "image",
            MsgTime = 1717000000L,
            RoomId = "rid-1",
            MediaSdkFileId = "sfid-2",
        };

        // 不抛异常：附件失败不阻断消息上报
        var evt = await builder.BuildAsync(msg);
        var payload = JsonSerializer.Deserialize<MessagePayload>(evt.Payload.GetRawText(), ProtocolJsonOptions.Instance)!;
        Assert.Empty(payload.Attachments);
    }
}
