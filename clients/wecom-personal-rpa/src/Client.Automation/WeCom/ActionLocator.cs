using FlaUI.Core.AutomationElements;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 控件定位三层降级链：
/// 1. FlaUI（AutomationId / Name，最稳）；
/// 2. Win32 坐标（相对主窗口左上角的 offset，UIA 不可达时兜底）；
/// 3. OpenCV 模板匹配（截图比对模板）。
/// 每层失败抛 AutomationLayerException 触发下一层；三层均失败返回 null。
/// </summary>
internal sealed class ActionLocator
{
    private readonly INodesConfig _nodes;
    private readonly FlaUi.FlaUiDriver _flaUi;
    private readonly WeComMainWindow _mainWindow;
    private readonly TemplateMatcher _templates;

    public ActionLocator(
        INodesConfig nodes,
        FlaUi.FlaUiDriver flaUi,
        WeComMainWindow mainWindow,
        TemplateMatcher templates)
    {
        _nodes = nodes ?? throw new ArgumentNullException(nameof(nodes));
        _flaUi = flaUi ?? throw new ArgumentNullException(nameof(flaUi));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
        _templates = templates ?? throw new ArgumentNullException(nameof(templates));
    }

    /// <summary>定位并聚焦消息输入框（发送前置）。</summary>
    public AutomationElement? LocateMessageInput()
    {
        // Layer 1: FlaUI
        try
        {
            var el = _flaUi.FindElementByAutomationId(_nodes.MessageInputAutomationId);
            if (el is not null)
            {
                _flaUi.Focus(el);
                return el;
            }
            Log.Debug("ActionLocator：FlaUI 未命中消息输入框，降级 Win32 坐标");
        }
        catch (AutomationLayerException ex)
        {
            Log.Debug(ex, "ActionLocator：FlaUI 查找消息输入框失败，降级 Win32 坐标");
        }

        // Layer 2: Win32 坐标点击
        if (TryClickOffset(_nodes.MessageInputClickOffset))
        {
            return null; // 坐标路径没有 AutomationElement 句柄，返回 null 表示已聚焦但无 element。
        }

        // Layer 3: OpenCV 模板
        var p = TryTemplateMatch("message_input.png");
        if (p is not null)
        {
            ClickAbsolute(p.Value.X, p.Value.Y);
            return null;
        }

        Log.Warning("ActionLocator：三层降级均失败，消息输入框未定位");
        return null;
    }

    /// <summary>点击搜索框（输入关键词前置）。</summary>
    public bool ClickSearchBox()
    {
        try
        {
            var el = _flaUi.FindElementByAutomationId(_nodes.SearchBoxAutomationId);
            if (el is not null)
            {
                _flaUi.Click(el);
                return true;
            }
        }
        catch (AutomationLayerException ex)
        {
            Log.Debug(ex, "ActionLocator：FlaUI 点击搜索框失败");
        }

        if (TryClickOffset(_nodes.SearchBoxClickOffset))
        {
            return true;
        }

        var p = TryTemplateMatch("search_box.png");
        if (p is not null)
        {
            ClickAbsolute(p.Value.X, p.Value.Y);
            return true;
        }
        return false;
    }

    /// <summary>Win32 坐标降级：相对主窗口左上角偏移点击。</summary>
    private bool TryClickOffset(Point offset)
    {
        try
        {
            if (_mainWindow.Handle == IntPtr.Zero && !_mainWindow.TryFind())
            {
                return false;
            }

            var (left, top, _, _) = _mainWindow.GetRect();
            int absX = left + offset.X;
            int absY = top + offset.Y;
            ClickAbsolute(absX, absY);
            return true;
        }
        catch (AutomationLayerException ex)
        {
            Log.Debug(ex, "ActionLocator：Win32 坐标点击失败");
            return false;
        }
    }

    /// <summary>OpenCV 降级：截主窗口区域后匹配模板。</summary>
    private Point? TryTemplateMatch(string templateName)
    {
        try
        {
            if (_mainWindow.Handle == IntPtr.Zero && !_mainWindow.TryFind())
            {
                return null;
            }

            var (left, top, w, h) = _mainWindow.GetRect();
            if (w <= 0 || h <= 0)
            {
                return null;
            }

            using var bmp = new System.Drawing.Bitmap(w, h, System.Drawing.Imaging.PixelFormat.Format24bppRgb);
            using (var g = System.Drawing.Graphics.FromImage(bmp))
            {
                g.CopyFromScreen(left, top, 0, 0, new System.Drawing.Size(w, h));
            }

            // 把 GDI+ Bitmap 锁定像素后构造 OpenCvSharp.Mat（避免依赖 OpenCvSharp4.Extensions 包）。
            var data = bmp.LockBits(
                new System.Drawing.Rectangle(0, 0, bmp.Width, bmp.Height),
                System.Drawing.Imaging.ImageLockMode.ReadOnly,
                bmp.PixelFormat);

            try
            {
                using var mat = OpenCvSharp.Mat.FromPixelData(
                    bmp.Height, bmp.Width, OpenCvSharp.MatType.MakeType(OpenCvSharp.MatType.CV_8U, 3),
                    data.Scan0, data.Stride);
                var rel = _templates.Match(mat, templateName, _nodes.TemplateMatchThreshold);
                if (rel is null)
                {
                    return null;
                }
                return new Point(left + rel.Value.X, top + rel.Value.Y);
            }
            finally
            {
                bmp.UnlockBits(data);
            }
        }
        catch (Exception ex)
        {
            Log.Debug(ex, "ActionLocator：OpenCV 模板匹配失败，template={Template}", templateName);
            return null;
        }
    }

    /// <summary>绝对坐标点击（SendInput 鼠标事件）。</summary>
    private static void ClickAbsolute(int x, int y)
    {
        // 鼠标移动 + 左键按下 + 抬起。坐标按 0..65535 归一化。
        var inputs = new NativeMethods.INPUT[3];

        // 移动：dx/dy 用绝对坐标映射到 0..65535。
        int screenWidth = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
        int screenHeight = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
        int dx = (int)((ulong)x * 65535UL / (ulong)screenWidth);
        int dy = (int)((ulong)y * 65535UL / (ulong)screenHeight);

        const uint MOUSEEVENTF_MOVE = 0x0001;
        const uint MOUSEEVENTF_ABSOLUTE = 0x8000;
        const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
        const uint MOUSEEVENTF_LEFTUP = 0x0004;

        inputs[0].type = NativeMethods.INPUT_MOUSE;
        inputs[0].u.mi.dx = dx;
        inputs[0].u.mi.dy = dy;
        inputs[0].u.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;

        inputs[1].type = NativeMethods.INPUT_MOUSE;
        inputs[1].u.mi.dwFlags = MOUSEEVENTF_LEFTDOWN;

        inputs[2].type = NativeMethods.INPUT_MOUSE;
        inputs[2].u.mi.dwFlags = MOUSEEVENTF_LEFTUP;

        NativeMethods.SendInput((uint)inputs.Length, inputs, System.Runtime.InteropServices.Marshal.SizeOf<NativeMethods.INPUT>());
        System.Threading.Thread.Sleep(120);
    }
}
