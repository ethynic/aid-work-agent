using System.Text.Json;
using System.Text.Json.Serialization;
using WeCom.PersonalRpa.Core.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 事件类型枚举，决定 payload 的结构（对应 Python EventType Literal）。
/// 用 SnakeCaseEnumJsonConverter 序列化为 snake_case 对齐服务端 Pydantic Literal["message", "status", "action_result"]。
/// </summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<EventType>))]
public enum EventType
{
    /// <summary>入站聊天消息（序列化为 "message"）。</summary>
    Message,

    /// <summary>账号 / 桌面健康状态（序列化为 "status"）。</summary>
    Status,

    /// <summary>客户端执行服务端下发 action 后的回执（序列化为 "action_result"）。</summary>
    ActionResult,
}

/// <summary>
/// 客户端上报事件的顶层信封（对应 Python RpaCallbackEnvelope）。
/// event_id 由客户端生成且必须全局稳定；同一逻辑事件重传必须携带相同 event_id。
/// </summary>
public sealed class InboundEvent
{
    /// <summary>客户端生成的全局稳定事件 ID，用于幂等去重。</summary>
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = string.Empty;

    /// <summary>发起上报的 RPA 客户端 ID（注册分配，与 X-Client-Id 一致）。</summary>
    [JsonPropertyName("client_id")]
    public string ClientId { get; set; } = string.Empty;

    /// <summary>事件归属的个人企微账号 ID。</summary>
    [JsonPropertyName("account_id")]
    public string AccountId { get; set; } = string.Empty;

    /// <summary>事件类型，决定 payload 的结构。</summary>
    [JsonPropertyName("event_type")]
    public EventType EventType { get; set; }

    /// <summary>事件在客户端发生的本地时间（ISO 8601）。</summary>
    [JsonPropertyName("occurred_at")]
    public DateTimeOffset OccurredAt { get; set; }

    /// <summary>
    /// 事件体，结构由 event_type 决定。以原始 JsonElement 承载，
    /// 由调用方按 event_type 解析为 MessagePayload / StatusPayload / ActionResultPayload。
    /// </summary>
    [JsonPropertyName("payload")]
    public JsonElement Payload { get; set; }
}
