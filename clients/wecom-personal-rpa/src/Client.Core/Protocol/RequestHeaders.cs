namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 鉴权 / 防重放请求头常量与时间窗口（与 Python schemas.HEADER_* 对齐）。
/// 所有客户端 -> 服务端请求都必须携带这四个头。
/// </summary>
public static class RequestHeaders
{
    /// <summary>客户端身份标识头（注册时由服务端分配，如 client_001）。</summary>
    public const string ClientId = "X-Client-Id";

    /// <summary>Unix 秒级时间戳头（字符串形式）。</summary>
    public const string Timestamp = "X-Timestamp";

    /// <summary>一次性随机串头（10 分钟内不可重复）。</summary>
    public const string Nonce = "X-Nonce";

    /// <summary>HMAC-SHA256 签名头（小写十六进制）。</summary>
    public const string Signature = "X-Signature";

    /// <summary>时间戳容忍窗口（秒）。超过该窗口的请求一律拒绝。</summary>
    public const int TimestampToleranceSeconds = 300;

    /// <summary>nonce 防重放保留时长（秒），与 Redis nonce set 的 TTL 对齐。</summary>
    public const int NonceTtlSeconds = 600;

    /// <summary>签名算法名称。</summary>
    public const string SignatureAlgorithm = "HMAC-SHA256";
}
