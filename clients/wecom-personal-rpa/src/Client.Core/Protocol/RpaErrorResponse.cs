using System.Text.Json.Serialization;
using WeCom.PersonalRpa.Core.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>错误码枚举（对应 Python ErrorCode Literal）。
/// 用 SnakeCaseEnumJsonConverter 序列化为 snake_case 对齐服务端。</summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<ErrorCode>))]
public enum ErrorCode
{
    /// <summary>签名 / token / 时间戳 / nonce 校验失败（"auth_failed"）。</summary>
    AuthFailed,

    /// <summary>客户端被禁用（"client_disabled"）。</summary>
    ClientDisabled,

    /// <summary>账号被服务端暂停（"account_paused"）。</summary>
    AccountPaused,

    /// <summary>会话需要人工绑定（重名 / 未确认）（"conversation_needs_review"）。</summary>
    ConversationNeedsReview,

    /// <summary>agent 推理超时（"agent_timeout"）。</summary>
    AgentTimeout,

    /// <summary>客户端不支持该回复类型（"unsupported_action"）。</summary>
    UnsupportedAction,

    /// <summary>请求体格式错误（"bad_request"）。</summary>
    BadRequest,

    /// <summary>服务端内部错误（"internal_error"）。</summary>
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
