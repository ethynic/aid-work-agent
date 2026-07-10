using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 服务端下发给客户端的动作信封，对应 Python ActionEnvelope。
/// 在线客户端：通过 WebSocket 直接推送本信封；离线客户端：作为一行写入 outbox，由客户端拉取执行。
/// </summary>
public sealed class ActionEnvelope
{
    /// <summary>本次下发的请求 ID（与回执 ActionResultPayload.request_id 对应）。</summary>
    [JsonPropertyName("request_id")]
    public string RequestId { get; set; } = string.Empty;

    /// <summary>服务端会话 ID（wecom_personal_rpa:{account_id}:{route_key}）。</summary>
    [JsonPropertyName("session_id")]
    public string SessionId { get; set; } = string.Empty;

    /// <summary>目标账号 ID。</summary>
    [JsonPropertyName("account_id")]
    public string AccountId { get; set; } = string.Empty;

    /// <summary>客户端侧会话标识，用于定位企微会话窗口。</summary>
    [JsonPropertyName("conversation_id")]
    public string ConversationId { get; set; } = string.Empty;

    /// <summary>回复上下文，仅用于校验和诊断；不得替代 conversation_id/actions 执行。</summary>
    [JsonPropertyName("reply_context")]
    public RpaReplyContext? ReplyContext { get; set; }

    /// <summary>顺序执行的动作列表。</summary>
    [JsonPropertyName("actions")]
    public List<RpaAction> Actions { get; set; } = new();
}

public sealed class RpaReplyContext
{
    [JsonPropertyName("sender_display_name")]
    public string? SenderDisplayName { get; set; }

    [JsonPropertyName("sender_stable_id")]
    public string? SenderStableId { get; set; }

    [JsonPropertyName("conversation_search_name")]
    public string? ConversationSearchName { get; set; }

    [JsonPropertyName("inbound_text")]
    public string? InboundText { get; set; }

    [JsonPropertyName("agent_reply_text")]
    public string? AgentReplyText { get; set; }
}
