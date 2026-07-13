using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 客户端执行服务端下发 action 后的回执，对应 Python RpaActionResultPayload。
/// 服务端按 wecom_personal_rpa:{tenant_id}:{action_result_id} 去重。
/// </summary>
public sealed class ActionResultPayload
{
    /// <summary>对应 ActionEnvelope.request_id。</summary>
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = string.Empty;

    /// <summary>客户端生成的回执唯一 ID，用于幂等去重。</summary>
    [JsonPropertyName("action_result_id")]
    public string ActionResultId { get; set; } = string.Empty;

    /// <summary>该回执对应 actions 列表的下标（从 0 开始）。</summary>
    [JsonPropertyName("action_index")]
    public int ActionIndex { get; set; }

    /// <summary>回执对应的 action.type（send_text/send_image/...）。</summary>
    [JsonPropertyName("action_type")]
    public string ActionType { get; set; } = string.Empty;

    /// <summary>是否执行成功。</summary>
    [JsonPropertyName("success")]
    public bool Success { get; set; }

    /// <summary>失败时的错误码（见错误语义表，可空）。</summary>
    [JsonPropertyName("error_code")]
    public string? ErrorCode { get; set; }

    /// <summary>失败时的可读说明，需脱敏（不含密钥/绝对路径，可空）。</summary>
    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; set; }

    /// <summary>客户端实际执行完成时间。</summary>
    [JsonPropertyName("executed_at")]
    public DateTimeOffset ExecutedAt { get; set; }

    /// <summary>客户端首次真正开始执行该 action 的时间；未执行的中止动作可空。</summary>
    [JsonPropertyName("started_at")]
    public DateTimeOffset? StartedAt { get; set; }
}
