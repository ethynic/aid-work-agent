using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>账号 / 桌面状态枚举（对应 Python AccountStatus）。</summary>
[JsonConverter(typeof(JsonStringEnumConverter))]
public enum AccountStatus
{
    /// <summary>账号在线。</summary>
    Online,

    /// <summary>账号离线。</summary>
    Offline,

    /// <summary>需要扫码登录。</summary>
    NeedLogin,

    /// <summary>二维码已过期。</summary>
    QrExpired,

    /// <summary>账号被限制。</summary>
    AccountLimited,

    /// <summary>桌面被锁定。</summary>
    DesktopLocked,

    /// <summary>窗口不可见。</summary>
    WindowNotVisible,

    /// <summary>已暂停。</summary>
    Paused,

    /// <summary>恢复中。</summary>
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
}
