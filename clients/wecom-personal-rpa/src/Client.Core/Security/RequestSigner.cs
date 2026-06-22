using System.Net.Http.Headers;
using System.Security.Cryptography;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Core.Security;

/// <summary>
/// 请求签名器：为出站 HttpClient 请求注入 X-Client-Id / X-Timestamp / X-Nonce / X-Signature。
/// 签名基于原始请求体字节，避免 re-serialize 改变字节（protocol.md §A.1）。
/// </summary>
public sealed class RequestSigner
{
    private readonly string _clientId;
    private readonly byte[] _secretBytes;

    /// <summary>构造请求签名器。</summary>
    /// <param name="clientId">客户端 ID。</param>
    /// <param name="secretBytes">解密后的客户端密钥原始字节。</param>
    public RequestSigner(string clientId, byte[] secretBytes)
    {
        _clientId = clientId ?? throw new ArgumentNullException(nameof(clientId));
        _secretBytes = secretBytes ?? throw new ArgumentNullException(nameof(secretBytes));
    }

    /// <summary>
    /// 生成一次性 nonce（URL 安全 base64 截断）。
    /// </summary>
    public static string NewNonce()
    {
        Span<byte> buf = stackalloc byte[16];
        RandomNumberGenerator.Fill(buf);
        return Convert.ToHexString(buf.ToArray()).ToLowerInvariant();
    }

    /// <summary>
    /// 为 HttpClient 请求注入鉴权头。调用方需先设置 Content 为含原始字节的 HttpContent，
    /// 本方法读取其字节做签名（只读取一次，不修改 body）。
    /// </summary>
    /// <param name="request">待签名的请求。</param>
    /// <param name="bodyBytes">原始请求体字节（必须与实际发送字节一致）。</param>
    public void Sign(HttpRequestMessage request, byte[] bodyBytes)
    {
        var timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
        var nonce = NewNonce();
        var signature = HmacSigner.ComputeSignature(_clientId, timestamp, nonce, bodyBytes, _secretBytes);

        request.Headers.Remove(RequestHeaders.ClientId);
        request.Headers.Remove(RequestHeaders.Timestamp);
        request.Headers.Remove(RequestHeaders.Nonce);
        request.Headers.Remove(RequestHeaders.Signature);

        request.Headers.TryAddWithoutValidation(RequestHeaders.ClientId, _clientId);
        request.Headers.TryAddWithoutValidation(RequestHeaders.Timestamp, timestamp);
        request.Headers.TryAddWithoutValidation(RequestHeaders.Nonce, nonce);
        request.Headers.TryAddWithoutValidation(RequestHeaders.Signature, signature);
    }

    /// <summary>
    /// 为 GET / DELETE 等无 body 请求注入鉴权头（body 视为空字节）。
    /// </summary>
    public void SignNoBody(HttpRequestMessage request)
    {
        Sign(request, Array.Empty<byte>());
    }

    /// <summary>
    /// 构造 WebSocket 鉴权用的查询串参数（client_id / timestamp / nonce / signature）。
    /// 用于 WS 握手时携带签名（部分服务端实现通过查询串鉴权 WS）。
    /// </summary>
    public string BuildWebSocketQuery()
    {
        var timestamp = DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString();
        var nonce = NewNonce();
        var signature = HmacSigner.ComputeSignature(_clientId, timestamp, nonce, string.Empty, _secretBytes);
        return $"{RequestHeaders.ClientId}={Uri.EscapeDataString(_clientId)}"
               + $"&{RequestHeaders.Timestamp}={Uri.EscapeDataString(timestamp)}"
               + $"&{RequestHeaders.Nonce}={Uri.EscapeDataString(nonce)}"
               + $"&{RequestHeaders.Signature}={Uri.EscapeDataString(signature)}";
    }
}
