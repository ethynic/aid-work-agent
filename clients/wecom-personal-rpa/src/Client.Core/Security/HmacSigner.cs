using System.Security.Cryptography;
using System.Text;

namespace WeCom.PersonalRpa.Core.Security;

/// <summary>
/// HMAC-SHA256 签名器，与服务端字节对字节一致。
/// 签名串构造：sig = hmac_sha256(key=secret_bytes, msg=clientId+timestamp+nonce+body).hex_lowercase()。
/// 全部按 ASCII / UTF-8 原始字节直接拼接，无分隔符。
/// </summary>
public static class HmacSigner
{
    /// <summary>
    /// 计算请求签名，返回小写十六进制 HMAC-SHA256。
    /// </summary>
    /// <param name="clientId">客户端 ID（与 X-Client-Id 一致）。</param>
    /// <param name="timestamp">Unix 秒级时间戳字符串。</param>
    /// <param name="nonce">一次性随机串。</param>
    /// <param name="body">原始请求体（UTF-8 编码）。</param>
    /// <param name="secretBytes">客户端密钥原始字节。</param>
    /// <returns>小写十六进制 HMAC-SHA256 签名。</returns>
    public static string ComputeSignature(string clientId, string timestamp, string nonce, string body, byte[] secretBytes)
    {
        // 全部按 UTF-8 原始字节直接拼接，无分隔符。
        var msgBytes = new byte[
            Encoding.UTF8.GetByteCount(clientId)
            + Encoding.UTF8.GetByteCount(timestamp)
            + Encoding.UTF8.GetByteCount(nonce)
            + Encoding.UTF8.GetByteCount(body)];
        var offset = 0;
        offset += Encoding.UTF8.GetBytes(clientId, 0, clientId.Length, msgBytes, offset);
        offset += Encoding.UTF8.GetBytes(timestamp, 0, timestamp.Length, msgBytes, offset);
        offset += Encoding.UTF8.GetBytes(nonce, 0, nonce.Length, msgBytes, offset);
        Encoding.UTF8.GetBytes(body, 0, body.Length, msgBytes, offset);

        using var hmac = new HMACSHA256(secretBytes);
        var hash = hmac.ComputeHash(msgBytes);
        // 小写十六进制
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    /// <summary>
    /// 重载：接受 byte[] body，用于直接对原始请求体字节签名（避免 re-serialize 改变字节）。
    /// </summary>
    public static string ComputeSignature(string clientId, string timestamp, string nonce, byte[] body, byte[] secretBytes)
    {
        var cid = Encoding.UTF8.GetBytes(clientId);
        var ts = Encoding.UTF8.GetBytes(timestamp);
        var nc = Encoding.UTF8.GetBytes(nonce);
        var msgBytes = new byte[cid.Length + ts.Length + nc.Length + body.Length];
        var offset = 0;
        Buffer.BlockCopy(cid, 0, msgBytes, offset, cid.Length); offset += cid.Length;
        Buffer.BlockCopy(ts, 0, msgBytes, offset, ts.Length); offset += ts.Length;
        Buffer.BlockCopy(nc, 0, msgBytes, offset, nc.Length); offset += nc.Length;
        Buffer.BlockCopy(body, 0, msgBytes, offset, body.Length);

        using var hmac = new HMACSHA256(secretBytes);
        var hash = hmac.ComputeHash(msgBytes);
        return Convert.ToHexString(hash).ToLowerInvariant();
    }
}
