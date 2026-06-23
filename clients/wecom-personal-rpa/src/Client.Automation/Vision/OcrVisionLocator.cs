// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（387-398 行）。
// Windows.Media.Ocr 用法参考 .NET WinRT projection 官方文档（OcrEngine.TryCreateFromLanguage）。
//
// 定位策略：截图 → Windows.Media.Ocr（zh-Hans-CN）→ 找包含 labelKeyword 的行 →
// 取该行所有 Word 的 BoundingRect 并集 → 转 BoundingBox → 返回中心点点击。
// 适合做 Qwen3-VL API 失败时的兜底，但只能定位 text（icon 类无文字无法识别）。
// ====================================================================================

using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices.WindowsRuntime;
using Serilog;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage.Streams;

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// Windows.Media.Ocr 降级视觉定位实现。
/// </summary>
public sealed class OcrVisionLocator : IVisionLocator, IDisposable
{
    private static readonly ILogger Logger = Log.ForContext<OcrVisionLocator>();

    private readonly IScreenCapturer _capturer;
    private readonly VisionConfig _config;
    private bool _disposed;

    /// <summary>构造。</summary>
    public OcrVisionLocator(IScreenCapturer capturer, VisionConfig config)
    {
        _capturer = capturer ?? throw new ArgumentNullException(nameof(capturer));
        _config = config ?? throw new ArgumentNullException(nameof(config));
    }

    /// <inheritdoc />
    public async Task<VisionProbeResult?> LocateAsync(
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        if (string.IsNullOrWhiteSpace(labelKeyword))
            throw new ArgumentException("labelKeyword 不能为空", nameof(labelKeyword));

        // 1. 截图
        CaptureResult capture = await _capturer.CaptureWeComMainWindowAsync(cancellationToken).ConfigureAwait(false);
        if (capture.Bitmap is null)
            throw new InvalidOperationException("ScreenCapturer 返回 Bitmap=null");

        try
        {
            // 2. 找 OCR 引擎（zh-Hans-CN 优先，回退任意 zh）
            OcrEngine? engine = CreateChineseEngine();
            if (engine is null)
            {
                Logger.Warning("后端日志：OcrVisionLocator 系统未安装中文 OCR 语言包，降级失败");
                return null;
            }

            // 3. Bitmap → SoftwareBitmap（Bgra8 给 OCR）
            using var softwareBmp = await BitmapToSoftwareBitmapAsync(capture.Bitmap).ConfigureAwait(false);
            if (softwareBmp is null) return null;

            // 4. 跑 OCR
            OcrResult ocrResult = await engine.RecognizeAsync(softwareBmp).AsTask(cancellationToken).ConfigureAwait(false);

        // 5. 找包含 labelKeyword 的行；多个命中取第一个（一般从上到下扫描）
        foreach (var line in ocrResult.Lines)
        {
            string lineText = line.Text ?? "";
            if (lineText.IndexOf(labelKeyword, StringComparison.OrdinalIgnoreCase) < 0) continue;

            BoundingBox? bbox = UnionWordsBoundingBox(line);
            if (bbox is null) continue;

            Logger.Information(
                "后端日志：OcrVisionLocator 命中 kw={Kw} line='{Line}' bbox={Bbox}",
                labelKeyword, lineText, bbox);
            return new VisionProbeResult
            {
                Bbox = bbox,
                Confidence = 0.5,
                Source = "ocr",
                ElementType = elementType,
                LabelKeyword = labelKeyword,
                ModelUsed = "windows-ocr"
            };
        }

        Logger.Information("后端日志：OcrVisionLocator 未命中 kw={Kw}", labelKeyword);
        return null;
        }
        finally
        {
            capture.Dispose();
        }
    }

    /// <inheritdoc />
    public Task InvalidateCacheAsync() => Task.CompletedTask;  // OCR 无缓存

    /// <inheritdoc />
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
    }

    // =================================================================================
    // 私有：OCR 引擎创建 / Bitmap 转 SoftwareBitmap / Word 并集 bbox
    // =================================================================================

    private static OcrEngine? CreateChineseEngine()
    {
        try
        {
            var lang = OcrEngine.AvailableRecognizerLanguages
                .FirstOrDefault(l => l.LanguageTag.StartsWith("zh", StringComparison.OrdinalIgnoreCase));
            if (lang is null) return null;
            return OcrEngine.TryCreateFromLanguage(lang);
        }
        catch (Exception ex)
        {
            Logger.Error(ex, "后端日志：OcrVisionLocator CreateChineseEngine 异常");
            return null;
        }
    }

    private static async Task<SoftwareBitmap?> BitmapToSoftwareBitmapAsync(Bitmap bmp)
    {
        // Bitmap → PNG bytes → InMemoryRandomAccessStream → BitmapDecoder → SoftwareBitmap (Bgra8)
        using var pngMs = new MemoryStream();
        bmp.Save(pngMs, ImageFormat.Png);
        pngMs.Position = 0;

        using var stream = new InMemoryRandomAccessStream();
        await stream.WriteAsync(pngMs.ToArray().AsBuffer()).AsTask().ConfigureAwait(false);
        stream.Seek(0);

        BitmapDecoder decoder = await BitmapDecoder.CreateAsync(stream).AsTask().ConfigureAwait(false);
        return await decoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied)
            .AsTask().ConfigureAwait(false);
    }

    private static BoundingBox? UnionWordsBoundingBox(OcrLine line)
    {
        // line.Words 每一项的 BoundingRect 是 Windows.Foundation.Rect，相对 Bitmap 像素
        double minX = double.MaxValue, minY = double.MaxValue;
        double maxX = double.MinValue, maxY = double.MinValue;
        bool any = false;

        foreach (var w in line.Words)
        {
            try
            {
                var br = w.BoundingRect;
                if (br.Width <= 0 || br.Height <= 0) continue;
                minX = Math.Min(minX, br.X);
                minY = Math.Min(minY, br.Y);
                maxX = Math.Max(maxX, br.X + br.Width);
                maxY = Math.Max(maxY, br.Y + br.Height);
                any = true;
            }
            catch
            {
                // 个别 Word 取 BoundingRect 失败忽略
            }
        }

        if (!any) return null;
        return new BoundingBox(
            (int)Math.Round(minX),
            (int)Math.Round(minY),
            (int)Math.Round(maxX),
            (int)Math.Round(maxY));
    }
}
