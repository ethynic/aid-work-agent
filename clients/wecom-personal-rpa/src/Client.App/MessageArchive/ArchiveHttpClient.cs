using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 企微会话存档 HTTP API 封装（Phase 3 块 D）。
/// 文档：https://developer.work.weixin.qq.com/document/path/91360
///
/// 三个 endpoint：
///   - GET  /cgi-bin/gettoken         获取 access_token（缓存 2h，提前 5min 刷新）
///   - POST /cgi-bin/msg/get_chat_data 按 seq 拉取会话密文批次
///   - POST /cgi-bin/msg/get_media_data 下载媒体文件流
///
/// 重试策略：网络异常指数退避 3 次（1s/3s/9s）；errcode=45009（频率限制）暂停 60s 后再试。
/// 所有 HTTP 调用通过注入的 HttpClient（IHttpClientFactory 创建），便于测试 mock。
/// </summary>
public sealed class ArchiveHttpClient
{
    private const string Tag = "ArchiveHttpClient";
    private static readonly Uri GetTokenUri = new("https://qyapi.weixin.qq.com/cgi-bin/gettoken");
    private static readonly Uri GetChatDataUri = new("https://qyapi.weixin.qq.com/cgi-bin/msg/get_chat_data");
    private static readonly Uri GetMediaUri = new("https://qyapi.weixin.qq.com/cgi-bin/msg/get_media_data");

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly HttpClient _http;
    private readonly ILogger<ArchiveHttpClient>? _logger;

    // access_token 缓存（提前 5 分钟刷新避免边界）。线程安全：SimpleTitle 锁内读写。
    private readonly SemaphoreSlim _tokenLock = new(1, 1);
    private string? _cachedToken;
    private DateTimeOffset _tokenExpireAt = DateTimeOffset.MinValue;

    /// <summary>构造。</summary>
    public ArchiveHttpClient(HttpClient http, ILogger<ArchiveHttpClient>? logger = null)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _logger = logger;
    }

    /// <summary>获取 access_token（缓存 + 提前 5 分钟刷新）。</summary>
    public async Task<string> GetAccessTokenAsync(string corpid, string secret, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(corpid)) throw new ArgumentException("corpid 不能为空", nameof(corpid));
        if (string.IsNullOrEmpty(secret)) throw new ArgumentException("secret 不能为空", nameof(secret));

        await _tokenLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_cachedToken is not null && DateTimeOffset.UtcNow < _tokenExpireAt)
            {
                return _cachedToken;
            }

            // 网络重试：1s / 3s / 9s
            var url = $"{GetTokenUri}?corpid={Uri.EscapeDataString(corpid)}&corpsecret={Uri.EscapeDataString(secret)}";
            var json = await GetJsonWithRetryAsync(url, ct).ConfigureAwait(false);

            // errcode 校验：0=成功，errcode!=0 抛异常
            EnsureWeComSuccess(json, "gettoken");

            // 7200 秒默认；提前 5 分钟视为过期
            var expiresIn = json.TryGetProperty("expires_in", out var ei) && ei.TryGetInt64(out var secs)
                ? secs : 7200;
            _cachedToken = json.GetProperty("access_token").GetString();
            _tokenExpireAt = DateTimeOffset.UtcNow.AddSeconds(expiresIn - 300);
            _logger?.LogInformation("[{Tag}] access_token 刷新成功，{Expire}s 后到期", Tag, expiresIn);
            return _cachedToken!;
        }
        finally
        {
            _tokenLock.Release();
        }
    }

    /// <summary>按 seq 拉取一批会话存档密文。</summary>
    public async Task<ChatDataBatch> GetChatDataAsync(string accessToken, long seq, int limit, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(accessToken)) throw new ArgumentException("accessToken 不能为空", nameof(accessToken));

        var url = $"{GetChatDataUri}?access_token={Uri.EscapeDataString(accessToken)}";
        var body = JsonSerializer.Serialize(new
        {
            seq,
            limit,
            proxy = string.Empty,
            last_snap_shot = 0,
        }, JsonOpts);
        var bodyBytes = Encoding.UTF8.GetBytes(body);

        var json = await PostJsonWithRetryAsync(url, bodyBytes, ct).ConfigureAwait(false);
        EnsureWeComSuccess(json, "get_chat_data");

        var list = new List<ChatDataItem>();
        if (json.TryGetProperty("chatdata", out var arr) && arr.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in arr.EnumerateArray())
            {
                list.Add(new ChatDataItem
                {
                    Seq = item.TryGetProperty("seq", out var se) && se.TryGetInt64(out var v) ? v : 0,
                    MsgId = item.TryGetProperty("msgid", out var mi) ? mi.GetString() ?? string.Empty : string.Empty,
                    Action = item.TryGetProperty("action", out var ac) ? ac.GetString() ?? string.Empty : string.Empty,
                    From = item.TryGetProperty("from", out var fr) ? fr.GetString() ?? string.Empty : string.Empty,
                    ToList = item.TryGetProperty("tolist", out var tl) && tl.ValueKind == JsonValueKind.Array
                        ? tl.EnumerateArray().Select(x => x.GetString() ?? string.Empty).Where(s => !string.IsNullOrEmpty(s)).ToList()
                        : new List<string>(),
                    RoomId = item.TryGetProperty("roomid", out var r) ? r.GetString() : null,
                    MsgTime = item.TryGetProperty("msgtime", out var mt) && mt.TryGetInt64(out var t) ? t : 0,
                    MsgType = item.TryGetProperty("msgtype", out var mty) ? mty.GetString() ?? string.Empty : string.Empty,
                    EncryptRandomKey = item.TryGetProperty("encrypt_random_key", out var rk)
                        ? rk.GetString() ?? string.Empty
                        : string.Empty,
                    EncryptChatMsg = item.TryGetProperty("encrypt_chat_msg", out var em)
                        ? em.GetString() ?? string.Empty
                        : string.Empty,
                });
            }
        }

        return new ChatDataBatch
        {
            Items = list,
        };
    }

    /// <summary>下载媒体文件流（调用方负责释放）。</summary>
    public async Task<Stream> DownloadMediaAsync(string accessToken, string sdkFileId, string filename, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(accessToken)) throw new ArgumentException("accessToken 不能为空", nameof(accessToken));
        if (string.IsNullOrEmpty(sdkFileId)) throw new ArgumentException("sdkFileId 不能为空", nameof(sdkFileId));

        var url = $"{GetMediaUri}?access_token={Uri.EscapeDataString(accessToken)}";

        // 大文件分块：先 POST 拿到第一段 + 后续 range 拉取。简单实现：单次 POST，流式读取。
        // 企微规范：单次最多返回 1MB；这里只做单次拉取（足够覆盖文本附件场景）。
        // TODO Phase 5：多段 range 拼接（用于 100MB+ 大文件）。
        using var form = new MultipartFormDataContent();
        var bodyJson = JsonSerializer.Serialize(new { fileid = sdkFileId }, JsonOpts);
        var bodyBytes = Encoding.UTF8.GetBytes(bodyJson);

        var resp = await PostStreamWithRetryAsync(url, bodyBytes, ct).ConfigureAwait(false);
        // 错误响应是 JSON（含 errcode），需要解析判断。成功响应是二进制流。
        // 简化策略：先 peek 头部判断 Content-Type。
        var ctHeader = resp.Content.Headers.ContentType?.MediaType ?? string.Empty;
        if (ctHeader.Contains("json", StringComparison.OrdinalIgnoreCase))
        {
            // 错误响应
            await using var es = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
            using var doc = await JsonDocument.ParseAsync(es, cancellationToken: ct).ConfigureAwait(false);
            EnsureWeComSuccess(doc.RootElement, "get_media_data");
        }

        var ms = new MemoryStream();
        // P1-9：流式校验累计字节数，超过 100MB 立即中断。
        // 原 CopyToAsync 全量拷贝，恶意/异常响应可能 OOM。
        const long maxBytes = 100L * 1024 * 1024;
        await using var src = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
        var buffer = new byte[8192];
        long totalRead = 0;
        int read;
        while ((read = await src.ReadAsync(buffer.AsMemory(0, buffer.Length), ct).ConfigureAwait(false)) > 0)
        {
            totalRead += read;
            if (totalRead > maxBytes)
            {
                _logger?.LogWarning("[{Tag}] 媒体流超过 100MB 上限，中断下载", Tag);
                await ms.DisposeAsync();
                throw new IOException($"媒体文件超过 {maxBytes} 字节上限");
            }
            await ms.WriteAsync(buffer.AsMemory(0, read), ct).ConfigureAwait(false);
        }
        ms.Position = 0;
        return ms;
    }

    // ---------- 内部工具 ----------

    private async Task<JsonElement> GetJsonWithRetryAsync(string url, CancellationToken ct)
    {
        const int maxAttempts = 3;
        int[] delays = { 1000, 3000, 9000 };
        Exception? last = null;
        for (int i = 0; i < maxAttempts; i++)
        {
            try
            {
                using var resp = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct)
                    .ConfigureAwait(false);
                resp.EnsureSuccessStatusCode();
                await using var s = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
                using var doc = await JsonDocument.ParseAsync(s, cancellationToken: ct).ConfigureAwait(false);
                return doc.RootElement.Clone();
            }
            catch (Exception ex)
            {
                last = ex;
                _logger?.LogWarning("[{Tag}] GET {Url} 第 {N} 次失败：{Msg}", Tag, GetTokenUri, i + 1, ex.Message);
                if (i < delays.Length) await Task.Delay(delays[i], ct).ConfigureAwait(false);
            }
        }
        throw new InvalidOperationException($"GET 失败（重试 {maxAttempts} 次）", last);
    }

    private async Task<JsonElement> PostJsonWithRetryAsync(string url, byte[] body, CancellationToken ct)
    {
        const int maxAttempts = 3;
        int[] delays = { 1000, 3000, 9000 };
        Exception? last = null;
        for (int i = 0; i < maxAttempts; i++)
        {
            try
            {
                using var content = new ByteArrayContent(body);
                content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
                using var resp = await _http.PostAsync(url, content, ct).ConfigureAwait(false);
                resp.EnsureSuccessStatusCode();
                await using var s = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
                using var doc = await JsonDocument.ParseAsync(s, cancellationToken: ct).ConfigureAwait(false);
                var root = doc.RootElement.Clone();

                // 45009 频率限制：直接抛 WeComRateLimitException，由调用方（ChatArchiveListener）
                // 决定暂停多久。不再在 HTTP 客户端内部 sleep 60s（会阻塞 listener 的 PeriodicTimer，
                // 但 PeriodicTimer 下个 tick 仍会立即重试，并未真正暂停）。
                if (root.TryGetProperty("errcode", out var ec) && ec.TryGetInt64(out var code) && code == 45009)
                {
                    _logger?.LogWarning("[{Tag}] 企微返回 45009（频率限制），抛 WeComRateLimitException", Tag);
                    throw new WeComRateLimitException(60);
                }
                return root;
            }
            catch (Exception ex)
            {
                last = ex;
                _logger?.LogWarning("[{Tag}] POST {Url} 第 {N} 次失败：{Msg}", Tag, url, i + 1, ex.Message);
                if (i < delays.Length) await Task.Delay(delays[i], ct).ConfigureAwait(false);
            }
        }
        throw new InvalidOperationException($"POST 失败（重试 {maxAttempts} 次）", last);
    }

    private async Task<HttpResponseMessage> PostStreamWithRetryAsync(string url, byte[] body, CancellationToken ct)
    {
        const int maxAttempts = 3;
        int[] delays = { 1000, 3000, 9000 };
        Exception? last = null;
        for (int i = 0; i < maxAttempts; i++)
        {
            try
            {
                using var content = new ByteArrayContent(body);
                content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
                var req = new HttpRequestMessage(HttpMethod.Post, url) { Content = new ByteArrayContent(body) };
                req.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
                var resp = await _http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct)
                    .ConfigureAwait(false);
                resp.EnsureSuccessStatusCode();
                return resp;
            }
            catch (Exception ex)
            {
                last = ex;
                _logger?.LogWarning("[{Tag}] POST(stream) {Url} 第 {N} 次失败：{Msg}", Tag, url, i + 1, ex.Message);
                if (i < delays.Length) await Task.Delay(delays[i], ct).ConfigureAwait(false);
            }
        }
        throw new InvalidOperationException($"POST(stream) 失败（重试 {maxAttempts} 次）", last);
    }

    private static void EnsureWeComSuccess(JsonElement json, string api)
    {
        if (json.TryGetProperty("errcode", out var ec) && ec.TryGetInt64(out var code) && code != 0)
        {
            var msg = json.TryGetProperty("errmsg", out var em) ? em.GetString() : string.Empty;
            throw new InvalidOperationException($"企微 {api} 调用失败：errcode={code} errmsg={msg}");
        }
    }
}

/// <summary>会话存档密文批次（chatdata_list）。</summary>
public sealed class ChatDataBatch
{
    /// <summary>本批次拉取到的密文条目。</summary>
    public List<ChatDataItem> Items { get; set; } = new();
}

/// <summary>
/// 单条会话存档密文条目。msgtype=text/image/file/voice/video/revoke/agree/disagree/...
/// 监听器对 action=upload/recall 解密 encrypt_chat_msg 拿到明文；action=download 跳过。
/// </summary>
public sealed class ChatDataItem
{
    public long Seq { get; set; }
    public string MsgId { get; set; } = string.Empty;
    public string Action { get; set; } = string.Empty;   // upload / recall / download / switch
    public string From { get; set; } = string.Empty;
    public List<string> ToList { get; set; } = new();
    public string? RoomId { get; set; }
    public long MsgTime { get; set; }
    public string MsgType { get; set; } = string.Empty;
    public string EncryptRandomKey { get; set; } = string.Empty;   // base64，需 RSA 解密
    public string EncryptChatMsg { get; set; } = string.Empty;     // base64，需 AES 解密
}
