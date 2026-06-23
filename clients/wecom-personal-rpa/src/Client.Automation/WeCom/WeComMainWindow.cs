using System.Text;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 企微主窗口的低层句柄封装（独立于 FlaUI）：用 Win32 FindWindow 定位主窗口，
/// 提供置顶 / 可见性 / 矩形查询。供 <see cref="HealthSupervisor"/> 与
/// <see cref="ActionLocator"/> 第二层（Win32 坐标）共用。
/// </summary>
public class WeComMainWindow
{
    private readonly string _className;

    /// <summary>构造；传入企微主窗口类名（默认 WeWorkWindow）。</summary>
    public WeComMainWindow(string className = "WeWorkWindow")
    {
        _className = string.IsNullOrWhiteSpace(className) ? "WeWorkWindow" : className;
    }

    /// <summary>当前主窗口句柄（0 = 未找到）。</summary>
    public IntPtr Handle { get; private set; }

    /// <summary>句柄是否非 0 且窗口可见。virtual 以便单测覆盖（默认走 Win32 IsWindowVisible）。</summary>
    public virtual bool IsVisible => Handle != IntPtr.Zero && NativeMethods.IsWindowVisible(Handle);

    /// <summary>枚举顶层窗口找到企微主窗口句柄。virtual 以便单测注入假句柄。</summary>
    public virtual bool TryFind()
    {
        // 先精确 FindWindow。
        IntPtr h = NativeMethods.FindWindowW(_className, null);
        if (h != IntPtr.Zero)
        {
            Handle = h;
            return true;
        }

        // 退化：EnumWindows 逐个比对 ClassName（兼容子窗口托管场景）。
        IntPtr found = IntPtr.Zero;
        NativeMethods.EnumWindows((wnd, _) =>
        {
            if (!NativeMethods.IsWindowVisible(wnd))
            {
                return true;
            }

            var sb = new StringBuilder(256);
            int len = NativeMethods.GetClassNameW(wnd, sb, sb.Capacity);
            if (len > 0 && sb.ToString() == _className)
            {
                found = wnd;
                return false; // 停止枚举
            }
            return true;
        }, IntPtr.Zero);

        Handle = found;
        return found != IntPtr.Zero;
    }

    /// <summary>把主窗口置顶并恢复（解决被遮挡导致自动化失焦）。virtual 以便单测覆盖。</summary>
    public virtual bool BringToForeground()
    {
        if (Handle == IntPtr.Zero)
        {
            return false;
        }

        // 先恢复（如被最小化），再置顶。
        NativeMethods.ShowWindow(Handle, NativeMethods.SW_RESTORE);
        NativeMethods.SetWindowPos(Handle, NativeMethods.HWND_TOPMOST, 0, 0, 0, 0,
            NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOACTIVATE);
        NativeMethods.SetWindowPos(Handle, NativeMethods.HWND_NOTOPMOST, 0, 0, 0, 0,
            NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_NOACTIVATE);
        return NativeMethods.SetForegroundWindow(Handle);
    }

    /// <summary>取主窗口屏幕矩形。失败抛 AutomationLayerException。virtual 以便单测覆盖。</summary>
    public virtual (int Left, int Top, int Width, int Height) GetRect()
    {
        if (Handle == IntPtr.Zero)
        {
            throw new AutomationLayerException("Win32.Window", "主窗口句柄未就绪");
        }

        if (!NativeMethods.GetWindowRect(Handle, out var rc))
        {
            throw new AutomationLayerException("Win32.Window", "GetWindowRect 失败");
        }
        return (rc.Left, rc.Top, rc.Width, rc.Height);
    }
}
