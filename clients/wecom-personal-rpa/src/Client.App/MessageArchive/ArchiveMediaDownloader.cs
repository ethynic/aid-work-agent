using System.IO;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 会话存档媒体下载器（Phase 3 块 D）。
///
/// 调用 ArchiveHttpClient.DownloadMediaAsync 拿到流后写入本地临时文件，返回本地路径。
/// 临时文件位于 ArchiveOptions.MediaTempDir（默认 temp/archive-media）。
/// 调用方（Phase 4 InboundEventBuilder）负责在上传服务端后删除本地副本。
/// </summary>
public sealed class ArchiveMediaDownloader
{
    private const string Tag = "ArchiveMediaDownloader";
    private const long MaxMediaSizeBytes = 100L * 1024 * 1024; // 100MB

    private readonly ArchiveHttpClient _http;
    private readonly ArchiveOptions _opts;
    private readonly Func<string, string, CancellationToken, Task<string>>? _downloadImpl;
    private readonly ILogger<ArchiveMediaDownloader>? _logger;

    /// <summary>
    /// 构造。测试可注入 _downloadImpl 覆盖真实下载逻辑。
    /// </summary>
    public ArchiveMediaDownloader(
        ArchiveHttpClient http,
        ArchiveOptions opts,
        ILogger<ArchiveMediaDownloader>? logger = null,
        Func<string, string, CancellationToken, Task<string>>? downloadImpl = null)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _opts = opts ?? throw new ArgumentNullException(nameof(opts));
        _logger = logger;
        _downloadImpl = downloadImpl;
    }

    /// <summary>
    /// 下载媒体到本地，返回本地文件绝对路径。
    /// </summary>
    /// <param name="accessToken">企微 access_token。</param>
    /// <param name="sdkFileId">企微下发的 sdk_fileid。</param>
    /// <param name="filename">建议文件名（msgtype=file 时为企微 filename 字段）。</param>
    /// <param name="ct">取消令牌。</param>
    public async Task<string> DownloadAsync(string accessToken, string sdkFileId, string filename, CancellationToken ct = default)
    {
        if (_downloadImpl is not null)
        {
            return await _downloadImpl(sdkFileId, filename, ct).ConfigureAwait(false);
        }

        Directory.CreateDirectory(_opts.MediaTempDir);
        var safeName = string.IsNullOrEmpty(filename) ? $"{Guid.NewGuid():N}.bin" : SanitizeFileName(filename);
        var localPath = Path.Combine(_opts.MediaTempDir, $"{Guid.NewGuid():N}_{safeName}");

        await using var src = await _http.DownloadMediaAsync(accessToken, sdkFileId, filename, ct).ConfigureAwait(false);
        await using var dst = new FileStream(localPath, FileMode.CreateNew, FileAccess.Write, FileShare.None,
            bufferSize: 81920, useAsync: true);
        var buf = new byte[81920];
        long written = 0;
        int read;
        while ((read = await src.ReadAsync(buf, ct).ConfigureAwait(false)) > 0)
        {
            if (written + read > MaxMediaSizeBytes)
            {
                _logger?.LogWarning("[{Tag}] 媒体文件超过 100MB，中断：fileid={FileId}", Tag, sdkFileId);
                await dst.DisposeAsync();
                try { File.Delete(localPath); } catch { /* ignore */ }
                throw new InvalidOperationException($"媒体文件超过 {MaxMediaSizeBytes} 字节上限");
            }
            await dst.WriteAsync(buf.AsMemory(0, read), ct).ConfigureAwait(false);
            written += read;
        }
        _logger?.LogInformation("[{Tag}] 媒体下载完成：{Path} ({Size}B)", Tag, localPath, written);
        return localPath;
    }

    private static string SanitizeFileName(string name)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var sb = new System.Text.StringBuilder(name.Length);
        foreach (var c in name)
        {
            sb.Append(Array.IndexOf(invalid, c) >= 0 ? '_' : c);
        }
        return sb.ToString();
    }
}
