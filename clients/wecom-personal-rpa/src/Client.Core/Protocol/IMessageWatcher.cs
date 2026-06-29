namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 会话存档消息监听器接口（Phase 3 块 D）。
/// 负责周期性拉取企微会话存档，对每条新消息触发 <see cref="NewMessageReceived"/>。
/// 实现见 WeCom.PersonalRpa.App.MessageArchive.ChatArchiveListener。
/// </summary>
public interface IMessageWatcher : IDisposable
{
    /// <summary>启动轮询循环（不阻塞，后台 Task）。</summary>
    Task StartAsync(CancellationToken cancellationToken = default);

    /// <summary>停止轮询并释放后台 Task。</summary>
    Task StopAsync(CancellationToken cancellationToken = default);

    /// <summary>每解出一条会话存档消息触发一次（download action 不触发）。</summary>
    event EventHandler<InboundEventArgs>? NewMessageReceived;
}

/// <summary>消息事件参数。</summary>
public sealed class InboundEventArgs : EventArgs
{
    /// <summary>解密后的会话存档消息。</summary>
    public required ArchiveMessage Message { get; init; }
}

/// <summary>
/// 会话存档解密后的消息（仅承载监听器需要对外暴露的字段）。
/// 完整密文结构由 ChatArchiveListener 内部 DTO 承载。
/// </summary>
public sealed class ArchiveMessage
{
    /// <summary>企微消息 ID（msgid，全局唯一，幂等去重用）。</summary>
    public required string MsgId { get; init; }

    /// <summary>动作类型："upload"（发送/接收新消息）或 "recall"（撤回）。</summary>
    public required string Action { get; init; }

    /// <summary>发送方 user_id（外部联系人以 external_userid 开头）。</summary>
    public required string From { get; init; }

    /// <summary>消息类型：text / image / file / voice / video / revoke / agree / disagree 等。</summary>
    public required string MsgType { get; init; }

    /// <summary>消息时间（秒级 Unix 时间戳）。</summary>
    public long MsgTime { get; init; }

    /// <summary>文本内容（msgtype=text 时非空）。</summary>
    public string? Text { get; init; }

    /// <summary>群会话 ID（群消息时非空）。</summary>
    public string? RoomId { get; init; }

    /// <summary>接收方列表（单聊为对端，群聊可能为空，roomid 优先）。</summary>
    public List<string> ToList { get; init; } = new();

    /// <summary>媒体文件 SDK fileid（image/file/voice/video 时由监听器透传，下载由后续 InboundEventBuilder 触发）。</summary>
    public string? MediaSdkFileId { get; init; }

    /// <summary>媒体文件名（msgtype=file 时由企微下发 filename 字段）。</summary>
    public string? MediaFileName { get; init; }
}
