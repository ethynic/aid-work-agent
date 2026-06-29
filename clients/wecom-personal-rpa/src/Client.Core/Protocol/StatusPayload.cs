using System.Text.Json.Serialization;
using WeCom.PersonalRpa.Core.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>账号 / 桌面状态枚举（对应 Python AccountStatus Literal）。
/// 用 SnakeCaseEnumJsonConverter 序列化为 snake_case 对齐服务端。</summary>
[JsonConverter(typeof(SnakeCaseEnumJsonConverter<AccountStatus>))]
public enum AccountStatus
{
    /// <summary>账号在线（"online"）。</summary>
    Online,

    /// <summary>账号离线（"offline"）。</summary>
    Offline,

    /// <summary>需要扫码登录（"need_login"）。</summary>
    NeedLogin,

    /// <summary>二维码已过期（"qr_expired"）。</summary>
    QrExpired,

    /// <summary>账号被限制（"account_limited"）。</summary>
    AccountLimited,

    /// <summary>桌面被锁定（"desktop_locked"）。</summary>
    DesktopLocked,

    /// <summary>窗口不可见（"window_not_visible"）。</summary>
    WindowNotVisible,

    /// <summary>已暂停（"paused"）。</summary>
    Paused,

    /// <summary>恢复中（"recovering"）。</summary>
    Recovering,
}

/// <summary>账号 / 桌面健康状态上报，对应 Python RpaStatusPayload。</summary>
public sealed class StatusPayload
{
    /// <summary>账号当前状态枚举。</summary>
    [JsonPropertyName("status")]
    public AccountStatus Status { get; set; }

    /// <summary>账号显示名（在线 / 扫码时上报，可空）。</summary>
    [JsonPropertyName("account_display_name")]
    public string? AccountDisplayName { get; set; }

    /// <summary>状态补充说明（脱敏后文本，不含密钥/路径，可空）。</summary>
    [JsonPropertyName("detail")]
    public string? Detail { get; set; }

    /// <summary>二维码短期上传引用（短期凭证，不得长期存储；日志中禁止打印，可空）。</summary>
    [JsonPropertyName("qr_image_ref")]
    public string? QrImageRef { get; set; }

    /// <summary>
    /// base64 PNG 二维码（need_login/qr_expired 时填，online/offline 时为 null）。
    /// 协议对齐 protocol.md §A.4：30 秒 TTL，不入审计/DB，优先于 QrImageRef。
    /// </summary>
    [JsonPropertyName("qr_image_base64")]
    public string? QrImageBase64 { get; set; }
}
