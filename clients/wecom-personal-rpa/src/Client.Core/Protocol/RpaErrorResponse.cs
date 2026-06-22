using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>错误码枚举（对应 Python ErrorCode Literal）。</summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum ErrorCode
{
    /// <summary>签名 / token / 时间戳 / nonce 校验失败。</summary>
    AuthFailed,

    /// <summary>客户端被禁用。</summary>
    ClientDisabled,

    /// <summary>账号被服务端暂停。</summary>
    AccountPaused,

    /// <summary>会话需要人工绑定（重名 / 未确认）。</summary>
    ConversationNeedsReview,

    /// <summary>agent 推理超时。</summary>
    AgentTimeout,

    /// <summary>客户端不支持该回复类型。</summary>
    UnsupportedAction,

    /// <summary>请求体格式错误。</summary>
    BadRequest,

    /// <summary>服务端内部错误。</summary>
    InternalError,
}

/// <summary>所有 4xx / 5xx 错误统一信封，对应 Python RpaErrorResponse。</summary>
public sealed class RpaErrorResponse
{
    /// <summary>错误码，客户端据此决定停止 / 暂停 / 重试 / 转人工。</summary>
    [JsonPropertyName("error")]
    public ErrorCode Error { get; set; }

    /// <summary>面向客户端的可读错误说明（中文）。</summary>
    [JsonPropertyName("message")]
    public string Message { get; set; } = string.Empty;

    /// <summary>调试信息（必须脱敏：剔除 secret/token/signature/绝对路径），生产环境可为空。</summary>
    [JsonPropertyName("debug")]
    public string? Debug { get; set; }
}
