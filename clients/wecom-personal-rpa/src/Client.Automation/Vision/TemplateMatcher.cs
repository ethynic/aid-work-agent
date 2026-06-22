using OpenCvSharp;
using WeCom.PersonalRpa.Automation.Contracts;
using Point = WeCom.PersonalRpa.Automation.Contracts.Point;

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// OpenCvSharp 模板匹配器（降级链第三层）。
/// 在 FlaUI / Win32 坐标失败时，按屏幕区域截图 + 预置模板图片匹配控件位置。
/// </summary>
public sealed class TemplateMatcher : IDisposable
{
    private readonly string _templatesRoot;
    private readonly Dictionary<string, Mat> _cache = new();
    private bool _disposed;

    /// <param name="templatesRoot">模板图片根目录（相对工程目录，由 INodesConfig 提供）。</param>
    public TemplateMatcher(string templatesRoot)
    {
        _templatesRoot = string.IsNullOrWhiteSpace(templatesRoot)
            ? Path.Combine(AppContext.BaseDirectory, "assets", "templates")
            : (Path.IsPathRooted(templatesRoot)
                ? templatesRoot
                : Path.Combine(AppContext.BaseDirectory, templatesRoot));
    }

    /// <summary>
    /// 在 <paramref name="regionBitmap"/> 范围内匹配 <paramref name="templatePath"/> 模板。
    /// </summary>
    /// <param name="regionBitmap">区域截图（OpenCvSharp.Extensions 或转 byte[] 后构造）。</param>
    /// <param name="templatePath">模板文件名或绝对路径；只传文件名时拼到 templatesRoot 下。</param>
    /// <param name="threshold">最低匹配度阈值（0..1），低于此值返回 null。</param>
    /// <returns>匹配点（模板左上角在 region 内坐标）；未命中返回 null。</returns>
    public Point? Match(Mat regionBitmap, string templatePath, double threshold = 0.8)
    {
        ArgumentNullException.ThrowIfNull(regionBitmap);
        if (string.IsNullOrWhiteSpace(templatePath))
        {
            return null;
        }

        Mat? template = LoadTemplate(templatePath);
        if (template is null)
        {
            return null;
        }

        // 模板必须严格小于源区域。
        if (template.Width > regionBitmap.Width || template.Height > regionBitmap.Height)
        {
            return null;
        }

        try
        {
            using var regionGray = new Mat();
            using var templateGray = new Mat();
            using var result = new Mat();

            if (regionBitmap.Channels() == 1)
            {
                regionBitmap.CopyTo(regionGray);
            }
            else
            {
                Cv2.CvtColor(regionBitmap, regionGray, ColorConversionCodes.BGR2GRAY);
            }

            if (template.Channels() == 1)
            {
                template.CopyTo(templateGray);
            }
            else
            {
                Cv2.CvtColor(template, templateGray, ColorConversionCodes.BGR2GRAY);
            }

            Cv2.MatchTemplate(regionGray, templateGray, result, TemplateMatchModes.CCoeffNormed);
            Cv2.MinMaxLoc(result, out double minVal, out double maxVal, out OpenCvSharp.Point minLoc, out OpenCvSharp.Point maxLoc);

            if (maxVal >= threshold)
            {
                return new Point(maxLoc.X, maxLoc.Y);
            }
            return null;
        }
        catch
        {
            // 视觉层异常不抛 AutomationLayerException（视觉是最后降级，失败即放弃定位）。
            return null;
        }
    }

    /// <summary>预加载所有模板到内存缓存，降低运行期 IO。</summary>
    public void LoadTemplates()
    {
        if (!Directory.Exists(_templatesRoot))
        {
            return;
        }

        foreach (var file in Directory.EnumerateFiles(_templatesRoot, "*.png"))
        {
            var name = Path.GetFileName(file);
            if (!_cache.ContainsKey(name))
            {
                var mat = Cv2.ImRead(file, ImreadModes.Color);
                if (!mat.Empty())
                {
                    _cache[name] = mat;
                }
                else
                {
                    mat.Dispose();
                }
            }
        }
    }

    /// <summary>从缓存或磁盘加载单个模板（找不到返回 null，不抛异常）。</summary>
    private Mat? LoadTemplate(string templatePath)
    {
        // 解析实际路径 / 缓存键。
        string key;
        string fullPath;
        if (Path.IsPathRooted(templatePath))
        {
            fullPath = templatePath;
            key = Path.GetFileName(templatePath);
        }
        else
        {
            key = templatePath;
            fullPath = Path.Combine(_templatesRoot, templatePath);
        }

        if (_cache.TryGetValue(key, out var cached))
        {
            return cached;
        }

        if (!File.Exists(fullPath))
        {
            return null;
        }

        var mat = Cv2.ImRead(fullPath, ImreadModes.Color);
        if (mat.Empty())
        {
            mat.Dispose();
            return null;
        }
        _cache[key] = mat;
        return mat;
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        foreach (var kv in _cache)
        {
            kv.Value.Dispose();
        }
        _cache.Clear();
    }
}
