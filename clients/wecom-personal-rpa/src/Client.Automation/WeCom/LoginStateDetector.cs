using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 企微登录态探测：区分 已登录 / 未登录（待扫码）/ 二维码过期 / 异常。
/// 输出二维码区域截图供 <see cref="StatusPayload.QrImageRef"/> 短期上报。
/// </summary>
internal sealed class LoginStateDetector
{
    private readonly INodesConfig _nodes;
    private readonly WeComMainWindow _mainWindow;
    private readonly FlaUi.FlaUiDriver _flaUi;

    public LoginStateDetector(INodesConfig nodes, WeComMainWindow mainWindow, FlaUi.FlaUiDriver flaUi)
    {
        _nodes = nodes ?? throw new ArgumentNullException(nameof(nodes));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
        _flaUi = flaUi ?? throw new ArgumentNullException(nameof(flaUi));
    }

    /// <summary>
    /// 探测当前登录态。判定优先级：
    /// 1. 桌面 / 窗口不可用 → 异常类状态；
    /// 2. FlaUI 主窗口已附加且可见功能区域（消息列表）→ Online；
    /// 3. 窗口可见但找不到登录态之后的元素 → NeedLogin；
    /// 4. 二维码元素 / 文本提示「二维码已失效」→ QrExpired；
    /// 5. 其它未知 → Offline（保守）。
    /// </summary>
    public LoginState Detect()
    {
        if (!_mainWindow.IsVisible)
        {
            return new LoginState(AccountStatus.WindowNotVisible, null, "企微主窗口不可见", null);
        }

        try
        {
            bool attached = _flaUi.IsAttached || _flaUi.AttachMainWindow();
            if (!attached)
            {
                return new LoginState(AccountStatus.Offline, null, "无法附加到企微主窗口", null);
            }

            // 已登录启发式：搜索框 / 消息列表元素存在且可交互。
            var searchBox = _flaUi.FindElementByAutomationId(_nodes.SearchBoxAutomationId);
            if (searchBox is not null && searchBox.IsAvailable)
            {
                // 尝试取账号显示名（右上角账号信息，AutomationId 各版本不一，这里保守返回 null）。
                return new LoginState(AccountStatus.Online, null, null, null);
            }

            // 未登录：尝试检测「二维码已失效」文本，区分 QrExpired vs NeedLogin。
            // 这里用 FlaUI 取主窗口全部文本不可靠（多嵌套），保守归类 NeedLogin，
            // 后续由 MessageWatcher 轮询补充判断。二维码区域截图留给上层。
            var qrBytes = CaptureQrRegion();
            return new LoginState(
                AccountStatus.NeedLogin,
                null,
                "等待扫码登录",
                qrBytes is null ? null : $"data:image/png;base64,{Convert.ToBase64String(qrBytes)}");
        }
        catch (AutomationLayerException ex)
        {
            Log.Warning(ex, "后端日志：登录态探测 FlaUI 层异常，Layer={Layer}", ex.Layer);
            return new LoginState(AccountStatus.Offline, null, "登录态探测异常", null);
        }
    }

    /// <summary>截取二维码区域为 PNG 字节数组；失败返回 null（不阻断上报）。</summary>
    private byte[]? CaptureQrRegion()
    {
        try
        {
            var (left, top, _, _) = _mainWindow.GetRect();
            var region = _nodes.QrRegion;

            // GDI+ 截图：使用 System.Drawing.Common（net8.0-windows 已包含）。
            using var bmp = new System.Drawing.Bitmap(region.Width, region.Height);
            using var g = System.Drawing.Graphics.FromImage(bmp);
            g.CopyFromScreen(left + region.X, top + region.Y, 0, 0,
                new System.Drawing.Size(region.Width, region.Height));

            using var ms = new MemoryStream();
            bmp.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
            return ms.ToArray();
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：二维码区域截图失败");
            return null;
        }
    }
}

/// <summary>登录态探测结果。</summary>
internal sealed class LoginState
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
