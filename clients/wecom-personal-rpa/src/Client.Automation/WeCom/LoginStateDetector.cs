using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 企微登录态探测（阶段 2B 视觉路径重构）。
/// 判定逻辑：
/// 1. 用 IVisionLocator 定位"搜索框"input → 命中即 Online；
/// 2. 未命中搜索框 → 用 IVisionLocator 定位"二维码"icon → 命中按 bbox 截图区域 → 转 base64 PNG 填 QrImageRef → NeedLogin；
/// 3. 两步都未命中 → 保守返回 Offline（不阻塞上报）。
/// </summary>
public sealed class LoginStateDetector
{
    private readonly IVisionLocator _visionLocator;
    private readonly WeComMainWindow _mainWindow;

    public LoginStateDetector(IVisionLocator visionLocator, WeComMainWindow mainWindow)
    {
        _visionLocator = visionLocator ?? throw new ArgumentNullException(nameof(visionLocator));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
    }

    /// <summary>探测当前登录态。</summary>
    public LoginState Detect()
    {
        try
        {
            if (_mainWindow.Handle == IntPtr.Zero)
            {
                _mainWindow.TryFind();
            }
            if (!_mainWindow.IsVisible)
            {
                return new LoginState(AccountStatus.WindowNotVisible, null, "企微主窗口不可见", null);
            }

            // 1. Online 判定：搜索框命中
            var searchProbe = _visionLocator.LocateAsync("input", "搜索").GetAwaiter().GetResult();
            if (searchProbe is not null)
            {
                return new LoginState(AccountStatus.Online, null, null, null);
            }

            // 2. NeedLogin 判定：搜索框未命中 + 二维码 icon 命中 → 按 bbox 截图
            var qrProbe = _visionLocator.LocateAsync("icon", "二维码").GetAwaiter().GetResult();
            if (qrProbe is not null)
            {
                string? qrImageRef = CaptureQrRegion(qrProbe.Bbox);
                return new LoginState(AccountStatus.NeedLogin, null, "等待扫码登录", qrImageRef);
            }

            // 3. 两步都未命中：保守 Offline
            return new LoginState(AccountStatus.Offline, null, "登录态探测未命中任何关键元素", null);
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：登录态探测异常");
            return new LoginState(AccountStatus.Offline, null, "登录态探测异常", null);
        }
    }

    /// <summary>
    /// 按 bbox 截取二维码区域为 PNG，转 base64 data URI。bbox 是截图坐标系（相对主窗口左上），
    /// 需要换算成屏幕坐标后用 CopyFromScreen。
    /// </summary>
    private string? CaptureQrRegion(BoundingBox bbox)
    {
        try
        {
            var (left, top, _, _) = _mainWindow.GetRect();
            int screenX = left + bbox.X1;
            int screenY = top + bbox.Y1;

            using var bmp = new System.Drawing.Bitmap(bbox.Width, bbox.Height);
            using var g = System.Drawing.Graphics.FromImage(bmp);
            g.CopyFromScreen(screenX, screenY, 0, 0, new System.Drawing.Size(bbox.Width, bbox.Height));

            using var ms = new MemoryStream();
            bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
            return $"data:image/png;base64,{Convert.ToBase64String(ms.ToArray())}";
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：二维码区域截图失败");
            return null;
        }
    }
}

/// <summary>登录态探测结果。</summary>
public sealed class LoginState
{
    public AccountStatus Status { get; }
    public string? AccountDisplayName { get; }
    public string? Detail { get; }
    public string? QrImageRef { get; }

    public LoginState(AccountStatus status, string? accountDisplayName, string? detail, string? qrImageRef)
    {
        Status = status;
        AccountDisplayName = accountDisplayName;
        Detail = detail;
        QrImageRef = qrImageRef;
    }
}
