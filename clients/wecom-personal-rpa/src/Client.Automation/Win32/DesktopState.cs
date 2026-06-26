using System.Runtime.InteropServices;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.Win32;

/// <summary>
/// 桌面状态探测器：锁定 / DPI / 屏幕分辨率。
/// 在 FlaUI 操作之前先 <see cref="IsLocked"/> 判定，避免在锁定 Session 0 上盲目驱动 UI。
/// </summary>
public sealed class DesktopState
{
    /// <summary>
    /// 判定桌面是否处于锁定 / 屏保运行状态。
    /// 策略：屏幕保护运行 OR Shell 窗口句柄为 0（快速用户切换）。
    /// </summary>
    public bool IsLocked()
    {
        // SPI_GETSCREENSAVERRUNNING 的 pvParam 是 BOOL*（4 字节）。直接 P/Invoke
        // 真实的 SystemParametersInfo（user32.dll），不要自造入口点 — user32.dll 里
        // 没有 SystemParametersInfoGetScreensaver，会抛 EntryPointNotFoundException。
        IntPtr pBool = Marshal.AllocHGlobal(sizeof(int));
        try
        {
            Marshal.WriteInt32(pBool, 0);
            if (NativeMethods.SystemParametersInfo(
                    NativeMethods.SPI_GETSCREENSAVERRUNNING, 0, pBool, 0))
            {
                if (Marshal.ReadInt32(pBool) != 0)
                {
                    return true;
                }
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pBool);
        }

        // Shell (Progman/Explorer) 窗口不存在时通常意味着 Session 处于 Winlogon（锁定 / 注销中）。
        IntPtr shell = NativeMethods.GetShellWindow();
        if (shell == IntPtr.Zero)
        {
            return true;
        }

        // Shell 窗口可见但无标题也常见于桌面被屏保接管。
        return false;
    }

    /// <summary>取主屏 DPI 缩放（每逻辑英寸像素数；96 = 100% 缩放）。</summary>
    public int GetDpi()
    {
        // GetDeviceCaps(LOGPIXELSY)：通过屏幕 DC 取垂直 DPI，避免依赖 System.Drawing.Common。
        IntPtr hdc = Gdi32Extension.GetDC(IntPtr.Zero);
        if (hdc == IntPtr.Zero)
        {
            return 96; // 降级到 100% 假设值。
        }
        try
        {
            int dpi = Gdi32Extension.GetDeviceCaps(hdc, Gdi32Extension.LOGPIXELSY);
            return dpi <= 0 ? 96 : dpi;
        }
        finally
        {
            Gdi32Extension.ReleaseDC(IntPtr.Zero, hdc);
        }
    }

    /// <summary>取主屏逻辑分辨率（像素）。</summary>
    public (int Width, int Height) GetScreenSize()
    {
        int w = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
        int h = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
        return (w, h);
    }

    /// <summary>取工作区（不含任务栏）矩形：(x, y, width, height)。</summary>
    public (int X, int Y, int Width, int Height) GetWorkArea()
    {
        // SPI_GETWORKAREA 的 pvParam 是 RECT*。手动分配/读取，避免自造入口点。
        IntPtr pRect = Marshal.AllocHGlobal(Marshal.SizeOf<NativeMethods.RECT>());
        try
        {
            Marshal.StructureToPtr(new NativeMethods.RECT(), pRect, false);
            if (NativeMethods.SystemParametersInfo(
                    NativeMethods.SPI_GETWORKAREA, 0, pRect, 0))
            {
                var rc = Marshal.PtrToStructure<NativeMethods.RECT>(pRect);
                return (rc.Left, rc.Top, rc.Width, rc.Height);
            }
        }
        finally
        {
            Marshal.FreeHGlobal(pRect);
        }
        // 拿不到就回退到全屏（GetSystemMetrics）。
        int w = NativeMethods.GetSystemMetrics(NativeMethods.SM_CXSCREEN);
        int h = NativeMethods.GetSystemMetrics(NativeMethods.SM_CYSCREEN);
        return (0, 0, w, h);
    }
}

internal static class Gdi32Extension
{
    internal const int LOGPIXELSY = 90;

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetDC(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

    [DllImport("gdi32.dll", SetLastError = true)]
    internal static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
}
