// ====================================================================================
// ScreenCapturer —— 视觉定位数据入口（PowerShell 委托实现）。
//
// 设计变更（2026-06-23）：
//   原版 C# 直接 CopyFromScreen，但实际运行遇到两个绕不开的坑：
//     1. C# 进程 DPI 感知与系统不一致 → GetSystemMetrics 返回虚拟分辨率，误判窗口越界
//     2. C# 进程前台权限受 Windows 限制 → SetForegroundWindow 失败，企微拿不到前台
//   而 PowerShell（系统级、交互式用户身份运行）的等价逻辑实测稳定。
//
//   本类改为「调用 PowerShell 脚本 capture-wecom-for-csharp.ps1 截图 + 自检」，
//   C# 只负责解析 JSON 结果 + 加载 PNG 转 Bitmap。截图相关 Windows API 复杂度全部
//   外包给 PowerShell。脚本已经真机验证可用（白色 12.9%，颜色多样性 174，企微版本 5.0.8）。
//
// 接口契约（IScreenCapturer）：CaptureWeComMainWindowAsync 返回 CaptureResult，签名不变。
//   调用方（QwenVisionLocator / OcrVisionLocator / VisionRegression）零改动。
//
// 硬约束：
//   - 脚本路径默认 clients/wecom-personal-rpa/scripts/capture-wecom-for-csharp.ps1，
//     开发环境相对 AppContext.BaseDirectory 推断；生产部署随 publish 包输出。
//   - 脚本必须输出单行 JSON 到 stdout（其他日志到 stderr）。
//   - 失败 loud：脚本返回 ok=false 或 process 非零退出 → 抛 WindowNotForegroundException。
//   - 调用方 Dispose CaptureResult 时清理 PNG 临时文件（避免 vision-probe-out 膨胀）。
// ====================================================================================

using System.Diagnostics;
using System.Drawing;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Win32;
using Serilog;

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 截图采集结果，包含 RGB Bitmap + 窗口矩形 + 窗口指纹 + 临时 PNG 路径。
/// 调用方负责 Dispose（释放 GDI 句柄 + 删除临时 PNG）。
/// </summary>
public sealed class CaptureResult : IDisposable
{
    private bool _disposed;

    /// <summary>截图位图（RGB / 24bpp 或 32bpp），调用方 Dispose 释放 GDI 句柄。采集成功后必非空。</summary>
    public Bitmap? Bitmap { get; init; }

    /// <summary>窗口指纹（缓存键输入），采集自当前窗口状态。</summary>
    public WindowFingerprint Fingerprint { get; init; } = new(
        WindowClass: string.Empty, X: 0, Y: 0, Width: 0, Height: 0,
        DpiScale: 1.0, WeComVersion: "unknown");

    /// <summary>窗口屏幕坐标矩形 (Left, Top, Width, Height)，供 SendInput 屏幕坐标换算。</summary>
    public (int Left, int Top, int Width, int Height) WindowRect { get; init; }

    /// <summary>
    /// 临时 PNG 路径（PowerShell 脚本输出）。Dispose 时清理。null 表示 C# 自己加载的、无临时文件。
    /// </summary>
    public string? TempPngPath { get; init; }

    /// <summary>释放 Bitmap 的 GDI 句柄 + 删除临时 PNG。多次调用安全。</summary>
    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        Bitmap?.Dispose();

        if (!string.IsNullOrEmpty(TempPngPath))
        {
            try { if (File.Exists(TempPngPath)) File.Delete(TempPngPath); }
            catch (Exception ex) { Log.Warning(ex, "清理临时 PNG 失败 path={Path}", TempPngPath); }
        }
    }
}

/// <summary>
/// 企微主窗口截图采集器。通过调用 PowerShell 脚本完成截图 + 像素自检（C# CopyFromScreen 在
/// DPI / 前台权限上有兼容性问题，已弃用，详见类注释）。
/// </summary>
public sealed class ScreenCapturer : IScreenCapturer
{
    /// <summary>企微主窗口默认类名（与 WeComMainWindow 一一致）。</summary>
    public const string DefaultWindowClassName = "WeWorkWindow";

    private readonly string _windowClassName;
    private readonly ScreenshotOptions _options;
    private readonly string _scriptPath;
    private readonly string _outDir;

    /// <summary>
    /// 构造截图采集器。
    /// </summary>
    /// <param name="windowClassName">企微主窗口类名，默认 <see cref="DefaultWindowClassName"/>。</param>
    /// <param name="options">截图自检选项（来自 VisionConfig.Screenshot 段，null 用默认）。</param>
    /// <param name="scriptPath">PowerShell 脚本绝对路径。null 时从 AppContext.BaseDirectory 推断。</param>
    /// <param name="outDir">PNG 临时输出目录。null 时用 Path.GetTempPath() 下的子目录。</param>
    public ScreenCapturer(
        string windowClassName = DefaultWindowClassName,
        ScreenshotOptions? options = null,
        string? scriptPath = null,
        string? outDir = null)
    {
        _windowClassName = string.IsNullOrWhiteSpace(windowClassName)
            ? DefaultWindowClassName
            : windowClassName;
        _options = options ?? new ScreenshotOptions();
        _scriptPath = ResolveScriptPath(scriptPath);
        _outDir = outDir ?? ResolveDefaultOutDir();
    }

    /// <summary>
    /// 采集一张企微主窗口截图。委托给 PowerShell 脚本完成窗口查找、前台化、截图、像素自检。
    /// </summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>截图 Bitmap + 窗口矩形 + 窗口指纹。</returns>
    /// <exception cref="WindowNotForegroundException">企微未在前台 / 未找到 / 脚本执行失败。</exception>
    /// <exception cref="SuspiciousScreenshotException">像素自检判定截图疑似非企微。</exception>
    public async Task<CaptureResult> CaptureWeComMainWindowAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        if (!File.Exists(_scriptPath))
        {
            throw new WindowNotForegroundException(
                $"截图脚本不存在：{_scriptPath}。请确认客户端部署完整。");
        }

        Directory.CreateDirectory(_outDir);

        var psi = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            WorkingDirectory = _outDir,
        };
        psi.ArgumentList.Add("-NoProfile");
        psi.ArgumentList.Add("-NonInteractive");
        psi.ArgumentList.Add("-ExecutionPolicy");
        psi.ArgumentList.Add("Bypass");
        psi.ArgumentList.Add("-File");
        psi.ArgumentList.Add(_scriptPath);
        psi.ArgumentList.Add("-OutDir");
        psi.ArgumentList.Add(_outDir);
        psi.ArgumentList.Add("-WindowClass");
        psi.ArgumentList.Add(_windowClassName);

        Log.Information("后端日志：调用 PowerShell 截图脚本 script={Script} outDir={OutDir}",
            _scriptPath, _outDir);

        string stdout;
        string stderr;
        int exitCode;
        using var proc = new Process { StartInfo = psi };
        if (!proc.Start())
        {
            throw new WindowNotForegroundException("无法启动 powershell.exe 进程。");
        }

        // 异步读取 stdout/stderr 防止管道死锁
        var stdoutTask = proc.StandardOutput.ReadToEndAsync();
        var stderrTask = proc.StandardError.ReadToEndAsync();

        // 注册取消令牌：超时或主动取消时杀进程
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        cts.CancelAfter(TimeSpan.FromSeconds(30));

        try
        {
            await proc.WaitForExitAsync(cts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (OperationCanceledException)
        {
            try { if (!proc.HasExited) proc.Kill(); } catch { /* ignore */ }
            throw new WindowNotForegroundException("PowerShell 截图脚本执行超时（>30s）。");
        }

        exitCode = proc.ExitCode;
        stdout = await stdoutTask.ConfigureAwait(false);
        stderr = await stderrTask.ConfigureAwait(false);

        if (!string.IsNullOrWhiteSpace(stderr))
        {
            Log.Debug("后端日志：PowerShell 脚本 stderr（仅 debug）：{Stderr}",
                stderr.Length > 500 ? stderr.Substring(0, 500) + "..." : stderr);
        }

        // 解析 stdout 最后一行 JSON（脚本只输出一行，但容忍额外空白）
        string? jsonLine = stdout.Trim().Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .LastOrDefault(s => s.TrimStart().StartsWith("{"));
        if (string.IsNullOrWhiteSpace(jsonLine))
        {
            throw new WindowNotForegroundException(
                $"PowerShell 脚本未输出 JSON。exit={exitCode} stdout='{(stdout.Trim().Length > 200 ? stdout.Trim()[0..200] : stdout.Trim())}'");
        }

        CaptureScriptResult result;
        try
        {
            result = JsonSerializer.Deserialize<CaptureScriptResult>(jsonLine,
                new JsonSerializerOptions { PropertyNameCaseInsensitive = true })
                ?? throw new InvalidOperationException("JSON 反序列化为 null");
        }
        catch (JsonException ex)
        {
            throw new WindowNotForegroundException(
                $"PowerShell 脚本输出 JSON 解析失败：{ex.Message} raw='{(jsonLine.Length > 200 ? jsonLine[0..200] : jsonLine)}'", ex);
        }

        if (!result.Ok)
        {
            Log.Warning("后端日志：PowerShell 截图脚本返回 ok=false error={Error}", result.Error);
            throw new WindowNotForegroundException(
                $"企微截图失败：{result.Error ?? "未知错误"}");
        }

        // 像素自检（PowerShell 已经算过，C# 复用结果）
        // 数值字段统一用 ?? 0 兜底（PowerShell 偶发输出 null 时不至于炸反序列化）
        double whiteRatio = result.WhiteRatio ?? 0;
        int colorDiversity = result.ColorDiversity ?? 0;
        bool tooWhite = whiteRatio > _options.MaxWhiteRatio;
        bool tooSparse = colorDiversity < _options.MinColorDiversity;
        if (_options.PixelSanityCheck && (tooWhite || tooSparse))
        {
            // 删 PNG（已经判废）
            try { if (File.Exists(result.PngPath)) File.Delete(result.PngPath); } catch { /* ignore */ }
            string reason = tooWhite
                ? $"白色区域占比 {whiteRatio:P1} 超过阈值 {_options.MaxWhiteRatio:P1}"
                : $"颜色多样性 {colorDiversity} 低于阈值 {_options.MinColorDiversity}";
            Log.Warning("后端日志：截图像素自检失败 hwnd={Hwnd} {Reason}", result.Hwnd, reason);
            throw new SuspiciousScreenshotException(
                "截图像素自检失败：" + reason + "。已拒绝下游视觉定位以防止误操作。",
                whiteRatio,
                colorDiversity);
        }

        // 加载 PNG 转 Bitmap（C# 自己用 System.Drawing）
        Bitmap bmp;
        try
        {
            bmp = new Bitmap(result.PngPath ?? throw new InvalidOperationException("PngPath is null"));
        }
        catch (Exception ex)
        {
            Log.Error(ex, "后端日志：加载 PNG 失败 path={Path}", result.PngPath);
            throw new WindowNotForegroundException($"加载截图 PNG 失败：{ex.Message}", ex);
        }

        int left = result.Left ?? 0;
        int top = result.Top ?? 0;
        int width = result.Width ?? 0;
        int height = result.Height ?? 0;
        double dpiScale = result.DpiScale ?? 1.0;

        var fingerprint = new WindowFingerprint(
            WindowClass: _windowClassName,
            X: left,
            Y: top,
            Width: width,
            Height: height,
            DpiScale: dpiScale,
            WeComVersion: result.WecomVersion ?? "unknown");

        Log.Information(
            "后端日志：截图采集成功 hwnd={Hwnd} rect={Left},{Top} {W}x{H} dpi={Dpi:F2} version={Version} white={White:P1} diversity={Diversity}",
            result.Hwnd, left, top, width, height,
            dpiScale, result.WecomVersion, whiteRatio, colorDiversity);

        return new CaptureResult
        {
            Bitmap = bmp,
            Fingerprint = fingerprint,
            WindowRect = (left, top, width, height),
            TempPngPath = result.PngPath,
        };
    }

    private static string ResolveScriptPath(string? overridePath)
    {
        if (!string.IsNullOrEmpty(overridePath) && File.Exists(overridePath))
            return Path.GetFullPath(overridePath);

        // 开发环境：clients/wecom-personal-rpa/src/Client.Automation/bin/Debug/.../Client.Automation.dll
        //           往上 5 层到 clients/wecom-personal-rpa/
        string baseDir = AppContext.BaseDirectory;
        var dir = new DirectoryInfo(baseDir);
        for (int i = 0; i < 8 && dir != null; i++)
        {
            string candidate = Path.Combine(dir.FullName, "scripts", "capture-wecom-for-csharp.ps1");
            if (File.Exists(candidate))
                return candidate;
            // publish 包：scripts/ 在 publish 根目录下
            candidate = Path.Combine(dir.FullName, "scripts", "capture-wecom-for-csharp.ps1");
            if (File.Exists(candidate)) return candidate;
            dir = dir.Parent;
        }
        // 兜底：返回最可能的路径，让调用方的 File.Exists 检查报错
        return Path.GetFullPath(Path.Combine(baseDir, "..", "..", "..", "..", "..",
            "scripts", "capture-wecom-for-csharp.ps1"));
    }

    private static string ResolveDefaultOutDir()
    {
        string baseTmp = Path.GetTempPath();
        return Path.Combine(baseTmp, "WeComRpa", "captures");
    }
}

/// <summary>
/// 像素自检算法（独立可测函数，便于单测）。算法：step = max(1, w/100)，对网格采样像素做
/// RGB/16*16 量化，统计唯一 key 数。保留供单测使用（生产路径走 PowerShell，不直接调用）。
/// </summary>
public static class PixelSanityChecker
{
    /// <summary>
    /// 量化到 16x16 色块后统计白色占比 + 颜色多样性。
    /// </summary>
    public static (double WhiteRatio, int ColorDiversity) Analyze(Bitmap bmp)
    {
        if (bmp is null) throw new ArgumentNullException(nameof(bmp));

        int w = bmp.Width;
        int h = bmp.Height;
        if (w <= 0 || h <= 0) return (0.0, 0);

        int step = Math.Max(1, w / 100);
        var colorCounts = new Dictionary<string, int>();
        long totalPixels = 0;

        for (int x = 0; x < w; x += step)
        {
            for (int y = 0; y < h; y += step)
            {
                Color px = bmp.GetPixel(x, y);
                int r = px.R / 16 * 16;
                int g = px.G / 16 * 16;
                int b = px.B / 16 * 16;
                string key = $"{r},{g},{b}";

                colorCounts.TryGetValue(key, out int cnt);
                colorCounts[key] = cnt + 1;
                totalPixels++;
            }
        }

        if (totalPixels == 0) return (0.0, colorCounts.Count);

        const string whiteKey = "240,240,240";
        colorCounts.TryGetValue(whiteKey, out int whiteCount);
        return (whiteCount / (double)totalPixels, colorCounts.Count);
    }
}

/// <summary>
/// capture-wecom-for-csharp.ps1 脚本的 JSON 输出契约（snake_case 由 PowerShell ConvertTo-Json 产生）。
/// 注意：PropertyNameCaseInsensitive=true 只忽略大小写，不忽略下划线，
/// 所以 snake_case 字段必须显式 [JsonPropertyName] 映射到 PascalCase 属性，
/// 否则 png_path / dpi_scale / wecom_version / white_ratio / color_diversity 全部映射失败。
/// </summary>
internal sealed class CaptureScriptResult
{
    public bool Ok { get; set; }
    public string? Error { get; set; }
    [JsonPropertyName("png_path")] public string? PngPath { get; set; }
    public int? Left { get; set; }
    public int? Top { get; set; }
    public int? Width { get; set; }
    public int? Height { get; set; }
    [JsonPropertyName("dpi_scale")] public double? DpiScale { get; set; } = 1.0;
    [JsonPropertyName("wecom_version")] public string? WecomVersion { get; set; }
    public string? Hwnd { get; set; }
    [JsonPropertyName("white_ratio")] public double? WhiteRatio { get; set; }
    [JsonPropertyName("color_diversity")] public int? ColorDiversity { get; set; }
}
