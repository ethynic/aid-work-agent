using System.IO;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.MessageArchive;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Inbound;

/// <summary>
/// 把 <see cref="ArchiveMessage"/> 转换为 <see cref="InboundEvent"/>（Phase 4 块 E）。
///
/// 设计见 docs/system/wecom-personal-rpa-client-design.md §F6 / protocol.md §A.3。
///
/// 字段映射：
///   - MsgId            → event_id（前缀 "msg_"，复用 archive msgid 做幂等）
///   - From             → sender_stable_id + sender_display_name（首版两者相等，外部联系人解析留待后续）
///   - MsgType          → message_type（text/image/file/voice/video/link）
///   - Text             → text
///   - RoomId           → conversation_id（群聊）+ conversation_type=external_group/internal_group
///   - 单聊              → conversation_id = "{from}_{tolist[0]}"，conversation_type=external_user/internal_user
///   - MediaSdkFileId   → 经 ArchiveMediaDownloader 下载到本地 → UploadMediaAsync 上传换 URL → 填入 attachments → 删本地副本
/// </summary>
public sealed class InboundEventBuilder
{
    private const string Tag = "InboundEventBuilder";

    private readonly ArchiveHttpClient _archiveHttp;
    private readonly ArchiveMediaDownloader _mediaDownloader;
    private readonly IAgentApiClient _apiClient;
    private readonly ClientOptions _options;
    private readonly ILogger<InboundEventBuilder>? _logger;
    private readonly string _accountId;
    private readonly Func<CancellationToken, Task<string>>? _tokenOverride;

    public InboundEventBuilder(
        ArchiveHttpClient archiveHttp,
        ArchiveMediaDownloader mediaDownloader,
        IAgentApiClient apiClient,
        ClientOptions options,
        string accountId,
        ILogger<InboundEventBuilder>? logger = null,
        Func<CancellationToken, Task<string>>? tokenOverride = null)
    {
        _archiveHttp = archiveHttp ?? throw new ArgumentNullException(nameof(archiveHttp));
        _mediaDownloader = mediaDownloader ?? throw new ArgumentNullException(nameof(mediaDownloader));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _accountId = accountId ?? throw new ArgumentNullException(nameof(accountId));
        _logger = logger;
        _tokenOverride = tokenOverride; // 测试注入用：生产环境为 null
    }

    /// <summary>
    /// 把 ArchiveMessage 转成 InboundEvent（event_type=message）。
    /// 媒体附件：下载本地 → 上传服务端 → URL 回填 attachments → 删本地副本。
    /// </summary>
    public async Task<InboundEvent> BuildAsync(ArchiveMessage msg, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(msg);

        var payload = await BuildPayloadAsync(msg, ct).ConfigureAwait(false);

        return new InboundEvent
        {
            EventId = BuildEventId(msg),
            ClientId = _options.ClientId,
            AccountId = _accountId,
            EventType = EventType.Message,
            OccurredAt = DateTimeOffset.FromUnixTimeSeconds(
                msg.MsgTime > 0 ? msg.MsgTime : DateTimeOffset.UtcNow.ToUnixTimeSeconds()),
            Payload = JsonSerializer.SerializeToElement(payload, ProtocolJsonOptions.Instance),
        };
    }

    private static string BuildEventId(ArchiveMessage msg)
    {
        // 复用 archive msgid 做幂等去重，服务端按 event_id 判重
        var raw = string.IsNullOrEmpty(msg.MsgId) ? Guid.NewGuid().ToString("N") : msg.MsgId;
        return $"msg_{raw}";
    }

    private async Task<MessagePayload> BuildPayloadAsync(ArchiveMessage msg, CancellationToken ct)
    {
        var (conversationId, conversationType) = InferConversation(msg);

        var payload = new MessagePayload
        {
            ConversationId = conversationId,
            ConversationType = conversationType,
            SenderStableId = msg.From,
            SenderDisplayName = msg.From, // 首版：display_name = stable_id；后续接 GetUserInfo 反查
            MessageType = MapMessageType(msg.MsgType),
            Text = msg.MsgType == "text" ? (msg.Text ?? string.Empty) : msg.Text,
        };

        // 媒体附件处理：image/file/voice/video
        if (IsMediaMessageType(msg.MsgType) && !string.IsNullOrEmpty(msg.MediaSdkFileId))
        {
            var attachment = await BuildMediaAttachmentAsync(msg, ct).ConfigureAwait(false);
            if (attachment is not null)
            {
                payload.Attachments.Add(attachment);
            }
        }

        return payload;
    }

    /// <summary>
    /// 推断 conversation_id 和 conversation_type。
    /// - RoomId 非空 → 群聊（首版统一 external_group，后续接 GetGroup 解析内部/外部）
    /// - 单聊 → conversation_id = "{from}_{tolist[0]}"（tolist 为空时回退到 from 账号本身）
    /// - 单聊 conversation_type 首版统一 external_user
    /// </summary>
    internal static (string conversationId, ConversationType conversationType) InferConversation(ArchiveMessage msg)
    {
        if (!string.IsNullOrEmpty(msg.RoomId))
        {
            return (msg.RoomId, ConversationType.ExternalGroup);
        }

        // 单聊：取对端 id（tolist 第一项；空时取 from 自身做兜底）
        var peer = msg.ToList.Count > 0 ? msg.ToList[0] : msg.From;
        if (string.IsNullOrEmpty(peer)) peer = "unknown";
        var conversationId = $"{msg.From}_{peer}";
        return (conversationId, ConversationType.ExternalUser);
    }

    /// <summary>
    /// 把企微 msgtype 映射为客户端 InboundMessageType 枚举。
    /// 未知 msgtype 默认归一为 link（不丢消息，但标记为非文本/媒体）。
    /// </summary>
    private static InboundMessageType MapMessageType(string msgType)
    {
        return msgType switch
        {
            "text" => InboundMessageType.Text,
            "image" => InboundMessageType.Image,
            "file" => InboundMessageType.File,
            "voice" => InboundMessageType.Voice,
            "video" => InboundMessageType.Video,
            "link" => InboundMessageType.Link,
            _ => InboundMessageType.Link,
        };
    }

    private static bool IsMediaMessageType(string msgType)
        => msgType is "image" or "file" or "voice" or "video";

    /// <summary>
    /// 下载媒体到本地 → 上传到服务端换 URL → 删除本地副本。
    /// 任一步失败：记日志返回 null（不阻断消息上报，仅丢失附件）。
    /// </summary>
    private async Task<Attachment?> BuildMediaAttachmentAsync(ArchiveMessage msg, CancellationToken ct)
    {
        var sdkFileId = msg.MediaSdkFileId!;
        var filename = msg.MediaFileName;

        string? localPath = null;
        try
        {
            // 拉取 access_token（每次都拉：archive token 有 7200s TTL 但频繁调用成本低，简单可靠）
            var token = await GetAccessTokenAsync(ct).ConfigureAwait(false);
            if (string.IsNullOrEmpty(token))
            {
                _logger?.LogWarning("[{Tag}] 取 access_token 失败，跳过附件 msgid={MsgId}", Tag, msg.MsgId);
                return null;
            }

            localPath = await _mediaDownloader.DownloadAsync(token, sdkFileId, filename ?? "", ct).ConfigureAwait(false);

            var fi = new FileInfo(localPath);
            var url = await _apiClient.UploadMediaAsync(localPath, ct).ConfigureAwait(false);

            return new Attachment
            {
                Type = msg.MsgType,
                Url = url,
                Name = filename,
                Size = fi.Exists ? fi.Length : null,
                MimeType = InferMimeType(msg.MsgType, filename),
            };
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "[{Tag}] 媒体附件处理失败 msgid={MsgId} fileid={FileId}（消息文本部分仍会上报）",
                Tag, msg.MsgId, sdkFileId);
            return null;
        }
        finally
        {
            // 清理本地临时文件（无论成功失败）
            if (!string.IsNullOrEmpty(localPath))
            {
                try { if (File.Exists(localPath)) File.Delete(localPath); }
                catch (Exception ex) { _logger?.LogDebug(ex, "[{Tag}] 删除本地临时媒体失败：{Path}", Tag, localPath); }
            }
        }
    }

    private async Task<string> GetAccessTokenAsync(CancellationToken ct)
    {
        // 测试注入路径
        if (_tokenOverride is not null)
        {
            return await _tokenOverride(ct).ConfigureAwait(false);
        }

        var a = _options.MessageSource;
        if (string.IsNullOrEmpty(a.Corpid) || string.IsNullOrEmpty(a.Secret))
        {
            _logger?.LogWarning("[{Tag}] ArchiveOptions corpid/secret 未配置，无法下载媒体附件", Tag);
            return string.Empty;
        }
        return await _archiveHttp.GetAccessTokenAsync(a.Corpid, a.Secret, ct).ConfigureAwait(false);
    }

    private static string? InferMimeType(string msgType, string? filename)
    {
        // 粗略映射，服务端按需重新识别
        return msgType switch
        {
            "image" => "image/jpeg",
            "voice" => "audio/amr",
            "video" => "video/mp4",
            "file" => InferByExtension(filename),
            _ => null,
        };
    }

    private static string? InferByExtension(string? filename)
    {
        if (string.IsNullOrEmpty(filename)) return null;
        var ext = Path.GetExtension(filename).ToLowerInvariant();
        return ext switch
        {
            ".pdf" => "application/pdf",
            ".doc" or ".docx" => "application/msword",
            ".xls" or ".xlsx" => "application/vnd.ms-excel",
            ".ppt" or ".pptx" => "application/vnd.ms-powerpoint",
            ".zip" => "application/zip",
            ".txt" => "text/plain",
            ".csv" => "text/csv",
            ".png" => "image/png",
            ".jpg" or ".jpeg" => "image/jpeg",
            ".gif" => "image/gif",
            _ => "application/octet-stream",
        };
    }
}
