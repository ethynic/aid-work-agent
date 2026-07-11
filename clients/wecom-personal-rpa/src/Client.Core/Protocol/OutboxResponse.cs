using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// GET /outbox 返回的待发送动作列表，对应服务端 OutboxResponse（protocol v1.2）。
/// 服务端 DB outbox 是唯一权威消息源，WS 仅作加速通知；客户端定期拉取本接口。
/// </summary>
/// <remarks>
/// 语义：GET 不删除、不确认、不抢占；同一 pending 信封可能被重复返回（at-least-once），
/// 客户端必须以 ActionEnvelope.request_id + action_index 幂等。执行完成后走 action_result
/// 回执，服务端收到回执后停止返回该信封。
/// items 里每项是 ActionEnvelope 形状，并多带一个顶层 ``type:"actions"`` 判别字段——
/// System.Text.Json 默认忽略未映射字段，故直接反序列化为 <see cref="ActionEnvelope"/>。
/// </remarks>
public sealed class OutboxResponse
{
    /// <summary>当前服务端线协议版本。</summary>
    [JsonPropertyName("protocol_version")]
    public string ProtocolVersion { get; set; } = string.Empty;

    /// <summary>服务端当前时间。</summary>
    [JsonPropertyName("server_time")]
    public DateTimeOffset ServerTime { get; set; }

    /// <summary>
    /// 建议轮询间隔（秒）。服务端默认 5；客户端采纳此值并 clamp 到 [2,60]。
    /// 标记可空仅为防御旧服务端不返回时的兜底。
    /// </summary>
    [JsonPropertyName("poll_interval_seconds")]
    public int? PollIntervalSeconds { get; set; }

    /// <summary>待发送动作信封列表（可能为空，空是正常结果）。</summary>
    [JsonPropertyName("items")]
    public List<ActionEnvelope> Items { get; set; } = new();
}
