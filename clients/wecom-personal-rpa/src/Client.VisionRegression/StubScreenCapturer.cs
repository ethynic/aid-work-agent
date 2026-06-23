// ====================================================================================
// StubScreenCapturer —— 真机回归专用 stub 截图器：从指定 PNG 文件加载 Bitmap，
// 跳过 C# ScreenCapturer 的截图采集逻辑。
//
// 背景：
//   Client.VisionRegression 本身是 console 子进程，没有 Windows 前台权限。
//   调用真实 ScreenCapturer 时，它内部启 PowerShell 子进程截图，PowerShell 也拿不到
//   前台权限（子进程继承调用方的前台状态），会截到被白色窗口遮挡的内容（实测白色 82.5%）。
//
//   改用「PowerShell 先截图（独立进程，前台权限正常）→ C# 加载 PNG」绕过这个测试环境限制。
//   生产环境 Client.App 是常驻 GUI 进程，前台权限天然 OK，用真实 ScreenCapturer 即可，
//   本 stub 仅供 Client.VisionRegression 在 --image 模式下使用。
//
// 反向关联：
//   - plans/plan-wecom-personal-rpa-vision.md 任务 E（阶段 3B.2）
//   - scripts/run-vision-regression.ps1（两阶段：先 PS 截图 → 再 C# 验证）
//
// 硬约束：
//   - TempPngPath 不填（stub 加载的 PNG 不由 stub 删除，由 PowerShell 脚本管）
//   - 窗口指纹由外部参数指定（left/top/dpi/version），与 PS 截图时的真实状态一致
// ====================================================================================

using System.Drawing;
using WeCom.PersonalRpa.Automation.Vision;

namespace WeCom.PersonalRpa.VisionRegression;

/// <summary>
/// 真机回归专用 stub：从指定 PNG 文件加载 Bitmap，绕过 C# ScreenCapturer 的截图采集。
/// </summary>
internal sealed class StubScreenCapturer : IScreenCapturer
{
    private readonly string _pngPath;
    private readonly int _windowLeft;
    private readonly int _windowTop;
    private readonly double _dpiScale;
    private readonly string _wecomVersion;

    /// <summary>
    /// 构造 stub 截图器。
    /// </summary>
    /// <param name="pngPath">外部 PowerShell 截好的 PNG 绝对路径。</param>
    /// <param name="windowLeft">窗口左上角屏幕 X 坐标（来自 PS 脚本输出）。</param>
    /// <param name="windowTop">窗口左上角屏幕 Y 坐标（来自 PS 脚本输出）。</param>
    /// <param name="dpiScale">DPI 缩放（来自 PS 脚本输出）。</param>
    /// <param name="wecomVersion">企微版本号（来自 PS 脚本输出）。</param>
    public StubScreenCapturer(
        string pngPath,
        int windowLeft,
        int windowTop,
        double dpiScale,
        string wecomVersion)
    {
        _pngPath = pngPath ?? throw new ArgumentNullException(nameof(pngPath));
        _windowLeft = windowLeft;
        _windowTop = windowTop;
        _dpiScale = dpiScale > 0 ? dpiScale : 1.0;
        _wecomVersion = string.IsNullOrWhiteSpace(wecomVersion) ? "unknown" : wecomVersion;
    }

    /// <inheritdoc />
    public Task<CaptureResult> CaptureWeComMainWindowAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();

        if (!File.Exists(_pngPath))
        {
            throw new InvalidOperationException($"Stub 截图文件不存在: {_pngPath}");
        }

        // 同步加载 PNG（文件很小，<5MB，不需要异步）
        var bmp = new Bitmap(_pngPath);
        int w = bmp.Width;
        int h = bmp.Height;

        var fingerprint = new WindowFingerprint(
            WindowClass: "WeWorkWindow",
            X: _windowLeft,
            Y: _windowTop,
            Width: w,
            Height: h,
            DpiScale: _dpiScale,
            WeComVersion: _wecomVersion);

        // 注意：TempPngPath 不填。stub 不负责删除外部 PNG（由 PS 脚本 / 回归工具管理）
        var result = new CaptureResult
        {
            Bitmap = bmp,
            Fingerprint = fingerprint,
            WindowRect = (_windowLeft, _windowTop, w, h),
        };

        return Task.FromResult(result);
    }
}
