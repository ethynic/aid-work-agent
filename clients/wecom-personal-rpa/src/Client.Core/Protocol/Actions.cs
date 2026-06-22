using System.Text.Json;
using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>出站动作判别字段 type 的字面量取值集合。</summary>
public static class ActionTypeNames
{
    /// <summary>发送文本消息。</summary>
    public const string SendText = "send_text";

    /// <summary>发送图片。</summary>
    public const string SendImage = "send_image";

    /// <summary>发送文件。</summary>
    public const string SendFile = "send_file";

    /// <summary>空动作（仅记录、不自动回复）。</summary>
    public const string Noop = "noop";

    /// <summary>转人工（客户端暂停对应会话或账号）。</summary>
    public const string Handoff = "handoff";
}

/// <summary>
/// 所有出站动作的抽象基类。子类通过自定义 JsonConverter 按 type 字段判别反序列化。
/// 对应 Python 的 Union[SendTextAction, SendImageAction, SendFileAction, NoopAction, HandoffAction]。
/// </summary>
[JsonConverter(typeof(ActionJsonConverter))]
public abstract class RpaAction
{
    /// <summary>动作类型（判别字段）。</summary>
    [JsonPropertyName("type")]
    public abstract string Type { get; }
}

/// <summary>发送文本消息，对应 Python SendTextAction。</summary>
public sealed class SendTextAction : RpaAction
{
    /// <inheritdoc />
    public override string Type => ActionTypeNames.SendText;

    /// <summary>待发送的文本内容。</summary>
    [JsonPropertyName("text")]
    public string Text { get; set; } = string.Empty;
}

/// <summary>发送图片，对应 Python SendImageAction。file_url 必须为短期签名 URL。</summary>
public sealed class SendImageAction : RpaAction
{
    /// <inheritdoc />
    public override string Type => ActionTypeNames.SendImage;

    /// <summary>图片短期签名下载 URL。</summary>
    [JsonPropertyName("file_url")]
    public string FileUrl { get; set; } = string.Empty;

    /// <summary>建议文件名（可空）。</summary>
    [JsonPropertyName("filename")]
    public string? Filename { get; set; }
}

/// <summary>发送文件，对应 Python SendFileAction。file_url 必须为短期签名 URL。</summary>
public sealed class SendFileAction : RpaAction
{
    /// <inheritdoc />
    public override string Type => ActionTypeNames.SendFile;

    /// <summary>文件短期签名下载 URL。</summary>
    [JsonPropertyName("file_url")]
    public string FileUrl { get; set; } = string.Empty;

    /// <summary>文件名（必填，企微文件发送需要）。</summary>
    [JsonPropertyName("filename")]
    public string Filename { get; set; } = string.Empty;
}

/// <summary>空动作：仅记录、不自动回复，对应 Python NoopAction。</summary>
public sealed class NoopAction : RpaAction
{
    /// <inheritdoc />
    public override string Type => ActionTypeNames.Noop;
}

/// <summary>转人工：客户端暂停对应会话或账号，对应 Python HandoffAction。</summary>
public sealed class HandoffAction : RpaAction
{
    /// <inheritdoc />
    public override string Type => ActionTypeNames.Handoff;

    /// <summary>转人工原因（可空）。</summary>
    [JsonPropertyName("reason")]
    public string? Reason { get; set; }
}
