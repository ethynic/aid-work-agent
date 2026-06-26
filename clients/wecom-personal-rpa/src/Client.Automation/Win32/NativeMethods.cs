using System.Runtime.InteropServices;
using System.Text;

namespace WeCom.PersonalRpa.Automation.Win32;

// ============================================================================
// P/Invoke 签名：仅声明、无实现。所有 DllImport 指向 user32.dll / kernel32.dll。
// 用于 FlaUI 不可达时的 Win32 坐标降级路径（点击 / 聚焦 / 取窗口矩形 / 发送输入 /
// 剪贴板独占）。
//
// 说明：
//   - IntPtr 用于 HWND / HANDLE；uint 用于 DWORD / WPARAM（32 位指针宽度）；int 用于 LPARAM 兼容。
//   - CharSet.Unicode 统一 W 后缀版本。
//   - SetLastError=true 让 Marshal.GetLastWin32Error() 可定位失败原因。
// ============================================================================

internal static class NativeMethods
{
    // ---------- 窗口前置 / 可见 / 矩形 ----------

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    /// <summary>取窗口客户区（相对窗口左上角的逻辑坐标），失败返回 false。</summary>
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);

    /// <summary>取窗口外框屏幕坐标矩形。</summary>
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    /// <summary>根据类名枚举顶层窗口。lParam 为自定义上下文句柄。</summary>
    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    internal static extern IntPtr FindWindowW(string? lpClassName, string? lpWindowName);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    internal static extern int GetClassNameW(IntPtr hWnd, StringBuilder lpClassName, int nMaxCount);

    /// <summary>设置窗口置顶（HWND_TOPMOST）。</summary>
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetDesktopWindow();

    // ---------- 系统参数 / DPI / 分辨率 ----------

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SystemParametersInfo(uint uiAction, uint uiParam, IntPtr pvParam, uint fWinIni);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int GetSystemMetrics(int nIndex);

    // ---------- 发送输入（键盘 / 鼠标） ----------

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetFocus();

    // ---------- 剪贴板独占 ----------

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool OpenClipboard(IntPtr hWndNewOwner);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EmptyClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr SetClipboardData(uint uFormat, IntPtr hMem);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetClipboardData(uint uFormat);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern bool IsClipboardFormatAvailable(uint format);

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern uint RegisterClipboardFormatW(string lpszFormat);

    // ---------- 全局内存（剪贴板 SetClipboardData 配套） ----------

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr GlobalAlloc(uint uFlags, UIntPtr dwBytes);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr GlobalFree(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr GlobalLock(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GlobalUnlock(IntPtr hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    internal static extern IntPtr GlobalSize(IntPtr hMem);

    // ---------- 桌面会话锁定探测 ----------

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern IntPtr GetShellWindow();

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern int GetWindowTextLengthW(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    internal delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    // ---------- 计时（SendInput 节奏控制） ----------

    [DllImport("kernel32.dll", SetLastError = false)]
    internal static extern uint GetTickCount();

    [DllImport("kernel32.dll", SetLastError = false)]
    internal static extern ulong GetTickCount64();

    // ---------- 常量 ----------

    // ShowWindow nCmdShow
    internal const int SW_HIDE = 0;
    internal const int SW_SHOWNORMAL = 1;
    internal const int SW_SHOWMINIMIZED = 2;
    internal const int SW_SHOWMAXIMIZED = 3;
    internal const int SW_RESTORE = 9;

    // SetWindowPos hWndInsertAfter 特殊句柄
    internal static readonly IntPtr HWND_TOPMOST = new(-1);
    internal static readonly IntPtr HWND_NOTOPMOST = new(-2);
    internal const uint SWP_NOSIZE = 0x0001;
    internal const uint SWP_NOMOVE = 0x0002;
    internal const uint SWP_NOACTIVATE = 0x0010;

    // SystemParametersInfo uiAction
    internal const uint SPI_GETSCREENSAVERRUNNING = 0x0072;
    internal const uint SPI_GETWORKAREA = 0x0030;

    // GetSystemMetrics nIndex
    internal const int SM_CXSCREEN = 0;
    internal const int SM_CYSCREEN = 1;

    // SendInput type
    internal const uint INPUT_MOUSE = 0;
    internal const uint INPUT_KEYBOARD = 1;

    internal const uint KEYEVENTF_KEYDOWN = 0x0000;
    internal const uint KEYEVENTF_KEYUP = 0x0002;
    internal const uint KEYEVENTF_UNICODE = 0x0004;
    internal const uint KEYEVENTF_EXTENDEDKEY = 0x0001;

    // Virtual key codes
    internal const ushort VK_RETURN = 0x0D;
    internal const ushort VK_CONTROL = 0x11;
    internal const ushort VK_V = 0x56;
    internal const ushort VK_ESCAPE = 0x1B;
    internal const ushort VK_A = 0x41;
    internal const ushort VK_DELETE = 0x2E;

    // 剪贴板格式
    internal const uint CF_UNICODETEXT = 13;
    internal const uint CF_TEXT = 1;

    // GlobalAlloc flags
    internal const uint GMEM_MOVEABLE = 0x0002;

    // ---------- 托管结构 ----------

    [StructLayout(LayoutKind.Sequential)]
    internal struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;

        public int Width => Right - Left;
        public int Height => Bottom - Top;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct MOUSEINPUT
    {
        public int dx;
        public int dy;
        public uint mouseData;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct KEYBDINPUT
    {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct HARDWAREINPUT
    {
        public uint uMsg;
        public short wParamL;
        public short wParamH;
    }

    [StructLayout(LayoutKind.Explicit)]
    internal struct INPUT_UNION
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public HARDWAREINPUT hi;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct INPUT
    {
        public uint type;
        public INPUT_UNION u;
    }
}
