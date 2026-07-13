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
        => await DownloadAsync(url, $"file.{expectedExt.TrimStart('.')}", false, ct).ConfigureAwait(false);

    /// <summary>按响应头、协议文件名和 URL 综合确定安全文件名并下载。</summary>
    public async Task<string> DownloadAsync(string url, string? suggestedFilename, bool requireImage,
        CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(url)) throw new ArgumentNullException(nameof(url));
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) ||
            (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
            throw new AttachmentRejectedException("附件 URL 仅支持 http/https 协议");

        var tempDir = string.IsNullOrWhiteSpace(_options.Outbound.DownloadTempDir)
            ? "temp/outbound-downloads"
            : _options.Outbound.DownloadTempDir;
        Directory.CreateDirectory(tempDir);

        var maxBytes = (long)_options.Outbound.MaxAttachmentSizeMb * 1024L * 1024L;
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
                return await DownloadOnceAsync(url, suggestedFilename, requireImage, tempDir, maxBytes, ct)
                    .ConfigureAwait(false);
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

    private async Task<string> DownloadOnceAsync(string url, string? suggestedFilename, bool requireImage,
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

        // 下载端点常把 Office/PDF 报成 octet-stream 或 text/plain，不能仅凭 MIME 拒绝。
        // send_image 只在服务端明确返回非通用、非图片 MIME 时拒绝，避免把 HTML 错误页粘贴到企微。
        var mediaType = resp.Content.Headers.ContentType?.MediaType?.ToLowerInvariant();
        if (requireImage && !string.IsNullOrEmpty(mediaType) &&
            mediaType != "application/octet-stream" && mediaType != "text/plain" &&
            !mediaType.StartsWith("image/", StringComparison.OrdinalIgnoreCase))
        {
            throw new AttachmentRejectedException($"图片 Content-Type 不受支持：{mediaType}");
        }

        // 3. 流式下载到磁盘（边读边检查累计大小，防 Content-Length 缺失时被灌爆）
        var responseName = GetContentDispositionFilename(resp.Content.Headers.ContentDisposition);
        var originalName = SanitizeFilename(responseName ?? suggestedFilename ?? TryGetUrlFilename(url));
        var mimeExt = resp.Content.Headers.ContentType is { } header
            ? InferExtensionFromContentType(header) : null;
        var currentExt = Path.GetExtension(originalName).TrimStart('.');
        if (string.IsNullOrEmpty(currentExt) && !string.IsNullOrEmpty(mimeExt))
            originalName += "." + mimeExt;
        if (string.IsNullOrEmpty(Path.GetExtension(originalName)))
            originalName += requireImage ? ".png" : ".bin";
        var uuid = Guid.NewGuid().ToString("N")[..12];
        var fileName = $"{uuid}_{originalName}";
        var localPath = Path.Combine(tempDir, fileName);

        try
        {
            await using var fs = File.Create(localPath);
            await using var stream = await resp.Content.ReadAsStreamAsync(ct).ConfigureAwait(false);
            var buf = new byte[64 * 1024];
            long total = 0;
            int n;
            while ((n = await stream.ReadAsync(buf, ct).ConfigureAwait(false)) > 0)
            {
                total += n;
                if (total > maxBytes)
                {
                    throw new AttachmentRejectedException(
                        $"附件流式累计超限：{total} bytes > 上限 {maxBytes} bytes");
                }
                await fs.WriteAsync(buf.AsMemory(0, n), ct).ConfigureAwait(false);
            }
        }
        catch
        {
            // 取消、网络中断、写盘失败和大小拒绝都不能遗留半截临时文件。
            TryDelete(localPath);
            throw;
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

    private static string? GetContentDispositionFilename(ContentDispositionHeaderValue? disposition)
    {
        var value = disposition?.FileNameStar ?? disposition?.FileName;
        return string.IsNullOrWhiteSpace(value) ? null : value.Trim().Trim('"');
    }

    private static string TryGetUrlFilename(string url)
    {
        try
        {
            var uri = new Uri(url);
            var seg = uri.Segments;
            if (seg.Length == 0) return "file";
            var last = Uri.UnescapeDataString(seg[^1].Trim('/'));
            if (string.IsNullOrEmpty(last)) return "file";
            var withoutQuery = last.Split('?')[0];
            return string.IsNullOrEmpty(withoutQuery) ? "file" : withoutQuery;
        }
        catch
        {
            return "file";
        }
    }

    private static string SanitizeFilename(string value)
    {
        // Path.GetFileName 同时去掉服务端传入的目录；再替换 Windows 非法字符和控制字符。
        var name = Path.GetFileName(value.Replace('\\', '/'));
        foreach (var c in Path.GetInvalidFileNameChars()) name = name.Replace(c, '_');
        name = new string(name.Select(c => char.IsControl(c) ? '_' : c).ToArray()).Trim().Trim('.');
        if (string.IsNullOrWhiteSpace(name) || name is "." or "..") return "file";
        return name.Length <= 180 ? name : name[..180];
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
