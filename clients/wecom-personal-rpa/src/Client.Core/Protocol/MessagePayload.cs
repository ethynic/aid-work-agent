using System.Text.Json.Serialization;
using WeCom.PersonalRpa.Core.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>会话类型，决定群/单聊、内外部语义（对应 Python ConversationType）。
/// 用 SnakeCaseEnumJsonConverter 序列化为 snake_case 对齐服务端。</summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<ConversationType>))]
public enum ConversationType
{
    /// <summary>内部单聊（序列化为 "internal_user"）。</summary>
    InternalUser,

    /// <summary>内部群聊（序列化为 "internal_group"）。</summary>
    InternalGroup,

    /// <summary>外部单聊（序列化为 "external_user"）。</summary>
    ExternalUser,

    /// <summary>外部群聊（序列化为 "external_group"）。</summary>
    ExternalGroup,
}

/// <summary>入站消息内容类型（对应 Python InboundMessageType）。单单词全小写序列化。</summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<InboundMessageType>))]
public enum InboundMessageType
{
    /// <summary>文本消息（序列化为 "text"）。</summary>
    Text,

    /// <summary>图片消息（序列化为 "image"）。</summary>
    Image,

    /// <summary>文件消息（序列化为 "file"）。</summary>
    File,

    /// <summary>语音消息（序列化为 "voice"）。</summary>
    Voice,

    /// <summary>视频消息（序列化为 "video"）。</summary>
    Video,

    /// <summary>链接消息（序列化为 "link"）。</summary>
    Link,
}

/// <summary>入站消息附件（图片 / 文件 / 语音 / 视频），对应 Python RpaAttachment。</summary>
public sealed class Attachment
{
    /// <summary>附件类型：image / file / voice / video / link。</summary>
    [JsonPropertyName("type")]
    public string Type { get; set; } = string.Empty;

    /// <summary>附件下载地址（可由客户端短期签名托管，或由服务端后续解析）。</summary>
    [JsonPropertyName("url")]
    public string Url { get; set; } = string.Empty;

    /// <summary>附件文件名（可空）。</summary>
    [JsonPropertyName("name")]
    public string? Name { get; set; }

    /// <summary>附件字节数（可空）。</summary>
    [JsonPropertyName("size")]
    public long? Size { get; set; }

    /// <summary>附件 MIME 类型（可空）。</summary>
    [JsonPropertyName("mime_type")]
    public string? MimeType { get; set; }
}

/// <summary>入站聊天消息体，对应 Python RpaMessagePayload。</summary>
public sealed class MessagePayload
{
    /// <summary>客户端本地会话标识，用于路由。</summary>
    [JsonPropertyName("conversation_id")]
    public string ConversationId { get; set; } = string.Empty;

    /// <summary>会话类型，决定群/单聊、内外部语义。</summary>
    [JsonPropertyName("conversation_type")]
    public ConversationType ConversationType { get; set; }

    /// <summary>发送人显示名（可能重名，仅用于展示）。</summary>
    [JsonPropertyName("sender_display_name")]
    public string SenderDisplayName { get; set; } = string.Empty;

    /// <summary>发送人稳定 ID（external_userid / userid / room_id），首版可为空。</summary>
    [JsonPropertyName("sender_stable_id")]
    public string? SenderStableId { get; set; }

    /// <summary>消息内容类型。</summary>
    [JsonPropertyName("message_type")]
    public InboundMessageType MessageType { get; set; }

    /// <summary>文本内容（message_type=text 时必填，其余可空）。</summary>
    [JsonPropertyName("text")]
    public string? Text { get; set; }

    /// <summary>附件列表（图片/文件/语音/视频），文本消息可为空。</summary>
    [JsonPropertyName("attachments")]
    public List<Attachment> Attachments { get; set; } = new();
}
