using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>
/// 远程附件下载器（docs/system/wecom-personal-rpa-client-design.md §F3.2）。
///
/// 职责：
///   - 从服务端短期签名 URL 下载图片/文件到本地临时目录。
///   - 校验 Content-Length（防 OOM 与超限）与 Content-Type（防假冒扩展名）。
///   - 失败指数退避重试 2 次（1s / 3s）。
///
/// 文件名：{uuid12}_{original}.{ext}。UUID 前缀避免多 action 同时下载撞名，original 来自 URL 路径。
///
/// 注意：当前实现仅使用注入的 HttpClient（来自 IHttpClientFactory）。
/// 构造函数接收 HttpClient 而非 IHttpClientFactory，简化单测。
/// </summary>
public sealed class AttachmentDownloader
{
    private static readonly TimeSpan[] RetryBackoffs =
    {
        TimeSpan.FromSeconds(1),
        TimeSpan.FromSeconds(3),
    };

    private readonly HttpClient _http;
    private readonly ClientOptions _options;
    private readonly ILogger<AttachmentDownloader>? _logger;

    /// <summary>构造下载器。</summary>
    public AttachmentDownloader(HttpClient http, ClientOptions options,
        ILogger<AttachmentDownloader>? logger = null)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _logger = logger;
    }

    /// <summary>
    /// 下载到本地。返回本地绝对路径。
    ///
    /// 校验链：
    ///   1. HEAD/GET 看响应头 Content-Length，超 MaxAttachmentSizeMb 直接拒（不读 body）。
    ///   2. Content-Type 提取主类型对应的扩展，校验和 expectedExt 一致（大小写不敏感）。
    /// </summary>
    /// <param name="url">远程短期签名 URL。</param>
    /// <param name="expectedExt">期望扩展名（如 "png" / "pdf"），不含点。</param>
    /// <param name="ct">取消令牌。</param>
    /// <returns>本地下载文件绝对路径。</returns>
    public async Task<string> DownloadAsync(string url, string expectedExt, CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(url)) throw new ArgumentNullException(nameof(url));
        if (string.IsNullOrEmpty(expectedExt))
            throw new ArgumentException("expectedExt 不能为空", nameof(expectedExt));

        var tempDir = string.IsNullOrWhiteSpace(_options.Outbound.DownloadTempDir)
            ? "temp/outbound-downloads"
            : _options.Outbound.DownloadTempDir;
        Directory.CreateDirectory(tempDir);

        var maxBytes = (long)_options.Outbound.MaxAttachmentSizeMb * 1024L * 1024L;
        var expected = expectedExt.TrimStart('.').ToLowerInvariant();

        Exception? lastError = null;
        for (var attempt = 0; attempt <= RetryBackoffs.Length; attempt++)
        {
            if (attempt > 0)
            {
                _logger?.LogWarning("附件下载重试 {Attempt}/{Total}，url={Url}",
                    attempt, RetryBackoffs.Length, url);
                try { await Task.Delay(RetryBackoffs[attempt - 1], ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { throw; }
            }

            try
            {
                return await DownloadOnceAsync(url, expected, tempDir, maxBytes, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { throw; }
            catch (AttachmentRejectedException ex)
            {
                // 业务级拒绝（超限 / MIME 不匹配）不重试，直接抛
                _logger?.LogWarning("附件下载被拒绝：{Reason}", ex.Message);
                throw;
            }
            catch (Exception ex)
            {
                lastError = ex;
                _logger?.LogWarning("附件下载失败 attempt={Attempt}：{Msg}", attempt, ex.Message);
            }
        }

        throw new InvalidOperationException($"附件下载失败（已重试 {RetryBackoffs.Length} 次）：{url}", lastError);
    }

    private async Task<string> DownloadOnceAsync(string url, string expectedExt,
        string tempDir, long maxBytes, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead, ct)
            .ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();

        // 1. 大小校验
        if (resp.Content.Headers.ContentLength is { } len && len > maxBytes)
        {
            throw new AttachmentRejectedException(
                $"附件过大：{len} bytes > 上限 {maxBytes} bytes ({_options.Outbound.MaxAttachmentSizeMb} MB)");
        }

        // 2. MIME 校验：从 Content-Type 推断扩展名，若与期望不符则拒绝
        //    Content-Type 未知时（如 application/octet-stream），用 URL 路径里的扩展名兜底校验。
        if (resp.Content.Headers.ContentType is { } ct2)
        {
            var inferredExt = InferExtensionFromContentType(ct2);
            if (!string.IsNullOrEmpty(inferredExt))
            {
                if (!string.Equals(inferredExt, expectedExt, StringComparison.OrdinalIgnoreCase))
                {
                    throw new AttachmentRejectedException(
                        $"附件 MIME 不匹配：Content-Type={ct2.MediaType} 推断 .{inferredExt}，期望 .{expectedExt}");
                }
            }
            else
            {
                // P1-8：Content-Type 未知 → 用 URL 路径里的扩展名兜底。
                // 例如：https://xxx.com/files/abc.png?sig=... → 推断 "png"
                var urlExt = TryGetExtensionFromUrl(url);
                if (!string.IsNullOrEmpty(urlExt) &&
                    !string.Equals(urlExt, expectedExt, StringComparison.OrdinalIgnoreCase))
                {
                    throw new AttachmentRejectedException(
                        $"附件扩展名兜底校验失败：Content-Type={ct2.MediaType}（未知），URL 路径扩展 .{urlExt}，期望 .{expectedExt}");
                }
                // URL 无扩展名或与期望一致 → 放行（下游 stream 校验 + 累计字节限制仍生效）
            }
        }

        // 3. 流式下载到磁盘（边读边检查累计大小，防 Content-Length 缺失时被灌爆）
        var originalName = TryGetOriginalName(url);
        var uuid = Guid.NewGuid().ToString("N")[..12];
        var fileName = $"{uuid}_{originalName}.{expectedExt}";
        var localPath = Path.Combine(tempDir, fileName);

        await using (var fs = File.Create(localPath))
        {
            await using var stream = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
            var buf = new byte[64 * 1024];
            long total = 0;
            int n;
            while ((n = await stream.ReadAsync(buf, ct).ConfigureAwait(false)) > 0)
            {
                total += n;
                if (total > maxBytes)
                {
                    await fs.DisposeAsync();
                    TryDelete(localPath);
                    throw new AttachmentRejectedException(
                        $"附件流式累计超限：{total} bytes > 上限 {maxBytes} bytes");
                }
                await fs.WriteAsync(buf.AsMemory(0, n), ct).ConfigureAwait(false);
            }
        }

        return localPath;
    }

    private static string? InferExtensionFromContentType(MediaTypeHeaderValue header)
    {
        var mime = header.MediaType?.ToLowerInvariant();
        if (string.IsNullOrEmpty(mime)) return null;
        return mime switch
        {
            "image/png" => "png",
            "image/jpeg" => "jpg",
            "image/gif" => "gif",
            "image/bmp" => "bmp",
            "image/webp" => "webp",
            "application/pdf" => "pdf",
            "application/zip" => "zip",
            "application/msword" => "doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document" => "docx",
            "application/vnd.ms-excel" => "xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" => "xlsx",
            "application/vnd.ms-powerpoint" => "ppt",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation" => "pptx",
            "text/plain" => "txt",
            _ => null,
        };
    }

    private static string TryGetOriginalName(string url)
    {
        try
        {
            var uri = new Uri(url);
            var seg = uri.Segments;
            if (seg.Length == 0) return "file";
            var last = Uri.UnescapeDataString(seg[^1].Trim('/'));
            if (string.IsNullOrEmpty(last)) return "file";
            var withoutQuery = last.Split('?')[0];
            var dot = withoutQuery.LastIndexOf('.');
            var name = dot > 0 ? withoutQuery[..dot] : withoutQuery;
            // 文件名安全化：去非法字符
            foreach (var c in Path.GetInvalidFileNameChars())
            {
                name = name.Replace(c, '_');
            }
            return string.IsNullOrEmpty(name) ? "file" : name;
        }
        catch
        {
            return "file";
        }
    }

    /// <summary>
    /// 从 URL 路径提取扩展名（不含点，小写）。用于 Content-Type 为 application/octet-stream
    /// 等未知类型时的兜底扩展名校验。提取失败返回 null。
    /// 例如：https://xxx.com/files/abc.png?sig=... → "png"
    /// </summary>
    private static string? TryGetExtensionFromUrl(string url)
    {
        try
        {
            var uri = new Uri(url);
            var seg = uri.Segments;
            if (seg.Length == 0) return null;
            var last = Uri.UnescapeDataString(seg[^1].Trim('/'));
            if (string.IsNullOrEmpty(last)) return null;
            var withoutQuery = last.Split('?')[0];
            var dot = withoutQuery.LastIndexOf('.');
            if (dot <= 0 || dot == withoutQuery.Length - 1) return null;
            var ext = withoutQuery[(dot + 1)..].ToLowerInvariant();
            return string.IsNullOrEmpty(ext) ? null : ext;
        }
        catch
        {
            return null;
        }
    }

    private static void TryDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch { /* 吞掉删除失败 */ }
    }
}

/// <summary>
/// 业务级下载拒绝异常（超限 / MIME 不匹配）。调用方不应重试。
/// </summary>
public sealed class AttachmentRejectedException : Exception
{
    public AttachmentRejectedException(string message) : base(message) { }
}
