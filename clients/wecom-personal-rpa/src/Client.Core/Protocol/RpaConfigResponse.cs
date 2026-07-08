using System.Text.Json.Serialization;
using WeCom.PersonalRpa.Core.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>暂停范围（与 Python RpaConfigResponse.paused_scope 字面量对齐）。
/// 用 SnakeCaseEnumJsonConverter 序列化为 snake_case 对齐服务端。</summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<PausedScope>))]
public enum PausedScope
{
    /// <summary>租户级暂停（"tenant"）。</summary>
    Tenant,

    /// <summary>账号级暂停（"account"）。</summary>
    Account,

    /// <summary>会话级暂停（"conversation"）。</summary>
    Conversation,
}

/// <summary>服务端下发的限速策略，对应 Python RpaRateLimits。</summary>
public sealed class RateLimits
{
    /// <summary>单账号每分钟最大自动发送条数。</summary>
    [JsonPropertyName("per_minute")]
    public int PerMinute { get; set; } = 5;

    /// <summary>单账号每日最大自动发送条数。</summary>
    [JsonPropertyName("per_day")]
    public int PerDay { get; set; } = 100;

    /// <summary>同一会话连续失败 N 次后自动暂停。</summary>
    [JsonPropertyName("consecutive_failure_pause")]
    public int ConsecutiveFailurePause { get; set; } = 2;
}

/// <summary>GET /config 返回的客户端运行配置，对应 Python RpaConfigResponse。</summary>
public sealed class RpaConfigResponse
{
    /// <summary>当前服务端线协议版本。</summary>
    [JsonPropertyName("protocol_version")]
    public string ProtocolVersion { get; set; } = string.Empty;

    /// <summary>允许继续托管的最小客户端版本，低于此版本必须暂停。</summary>
    [JsonPropertyName("min_client_version")]
    public string MinClientVersion { get; set; } = string.Empty;

    /// <summary>服务端是否已整体暂停该客户端。</summary>
    [JsonPropertyName("paused")]
    public bool Paused { get; set; }

    /// <summary>暂停范围：租户级 / 账号级 / 会话级（可空）。</summary>
    [JsonPropertyName("paused_scope")]
    public PausedScope? PausedScope { get; set; }

    /// <summary>限速策略。</summary>
    [JsonPropertyName("rate_limits")]
    public RateLimits RateLimits { get; set; } = new();

    /// <summary>服务端当前时间，供客户端校准时钟漂移。</summary>
    [JsonPropertyName("server_time")]
    public DateTimeOffset ServerTime { get; set; }

    /// <summary>当前客户端 ID（与 X-Client-Id 一致），用于构造 callback/ws 路径。可空（旧服务端不返回）。</summary>
    [JsonPropertyName("client_id")]
    public string? ClientId { get; set; }

    /// <summary>客户端归属租户 ID，用于构造 callback/ws 路径 t/{tenant_id}/...。可空（旧服务端不返回）。</summary>
    [JsonPropertyName("tenant_id")]
    public string? TenantId { get; set; }

    /// <summary>tenant_channel_configs 记录 ID，用于构造 callback/ws 路径 .../callback/{config_id}。可空（旧服务端不返回）。</summary>
    [JsonPropertyName("config_id")]
    public string? ConfigId { get; set; }
}
