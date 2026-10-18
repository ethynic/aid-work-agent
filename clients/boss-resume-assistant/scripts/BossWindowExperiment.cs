using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

// Window-image experiments only. Page input retains its stricter render-widget guard.
public static class BossWindowExperiment
{
    public delegate bool EnumProc(IntPtr window, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr window, StringBuilder text, int size);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr window, StringBuilder text, int size);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll")] private static extern IntPtr GetAncestor(IntPtr window, uint flags);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool BringWindowToTop(IntPtr window);
    [DllImport("kernel32.dll")] private static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] private static extern bool AttachThreadInput(uint current, uint target, bool attach);
    [DllImport("user32.dll")] private static extern IntPtr WindowFromPoint(BossPhysicalInput.Point point);
    [DllImport("user32.dll")] private static extern void mouse_event(uint flags, uint x, uint y, uint data, UIntPtr extra);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr window, uint attribute, out BossPhysicalInput.Rect rect, int size);

    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int virtualKey);
    [DllImport("user32.dll", SetLastError = true)] private static extern uint SendInput(uint count, Input[] inputs, int size);
    [StructLayout(LayoutKind.Sequential)] private struct KeyboardInput
    {
        public ushort VirtualKey, Scan;
        public uint Flags, Time;
        public UIntPtr ExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)] private struct MouseInput
    {
        public int X, Y;
        public uint MouseData, Flags, Time;
        public UIntPtr ExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)] private struct InputUnion
    {
        [FieldOffset(0)] public KeyboardInput Keyboard;
        [FieldOffset(0)] public MouseInput Mouse;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Input
    {
        public uint Type;
        public InputUnion Data;
    }

    public static uint ProcessId(IntPtr window) { uint id; GetWindowThreadProcessId(window, out id); return id; }
    public static string Title(IntPtr window) { var text = new StringBuilder(1024); GetWindowText(window, text, text.Capacity); return text.ToString(); }
    public static string ClassName(IntPtr window) { var text = new StringBuilder(256); GetClassName(window, text, text.Capacity); return text.ToString(); }
    public static IntPtr Find(int processId)
    {
        var matches = new List<IntPtr>();
        EnumWindows((window, data) => {
            if (IsWindowVisible(window) && ProcessId(window) == processId && ClassName(window) == "Chrome_WidgetWin_1" && Title(window).Contains("BOSS")) matches.Add(window);
            return true;
        }, IntPtr.Zero);
        // Only one independent BOSS main window proves native ownership of the
        // activated CDP target. Foreground priority cannot distinguish two windows
        // of the same browser process. Owned print/dialog windows remain supported.
        var mainWindows = new List<IntPtr>();
        foreach (var candidate in matches)
        {
            bool owned = false;
            foreach (var other in matches)
                if (candidate != other && BelongsTo(candidate, other)) { owned = true; break; }
            if (!owned) mainWindows.Add(candidate);
        }
        if (mainWindows.Count != 1) throw new InvalidOperationException("Expected exactly one independent BOSS window in debug Chrome; found " + mainWindows.Count);
        return mainWindows[0];
    }
    public static bool BelongsTo(IntPtr window, IntPtr browser)
    {
        if (window == IntPtr.Zero) return false;
        window = GetAncestor(window, 2); // GA_ROOT, including owned top-level dialogs.
        if (ProcessId(window) != ProcessId(browser)) return false;
        for (int i = 0; i < 32 && window != IntPtr.Zero; i++, window = GetWindow(window, 4))
            if (window == browser) return true;
        return false;
    }
    public static IntPtr[] OwnedDialogs(IntPtr browser)
    {
        var windows = new List<IntPtr>();
        EnumWindows((window, data) => { if (window != browser && IsWindowVisible(window) && BelongsTo(window, browser)) windows.Add(window); return true; }, IntPtr.Zero);
        return windows.ToArray();
    }
    public static void Foreground(IntPtr browser)
    {
        if (IsIconic(browser)) ShowWindow(browser, 9);
        uint ignored;
        uint foregroundThread = GetWindowThreadProcessId(GetForegroundWindow(), out ignored);
        uint current = GetCurrentThreadId();
        bool attached = foregroundThread != current && AttachThreadInput(current, foregroundThread, true);
        try { BringWindowToTop(browser); SetForegroundWindow(browser); }
        finally { if (attached) AttachThreadInput(current, foregroundThread, false); }
    }
    public static void RequireForeground(IntPtr browser)
    {
        if (!BelongsTo(GetForegroundWindow(), browser)) throw new InvalidOperationException("Debug Chrome is not foreground; observation/input refused");
    }
    public static void RequireGeometry(IntPtr browser, int left, int top, int width, int height)
    {
        var rect = ReadCaptureRect(browser);
        if (rect.Left != left || rect.Top != top || rect.Right - rect.Left != width || rect.Bottom - rect.Top != height)
            throw new InvalidOperationException("Browser window moved/resized since observation; capture again");
    }
    public static BossPhysicalInput.Rect ReadCaptureRect(IntPtr browser)
    {
        BossPhysicalInput.RequirePhysicalContext();
        BossPhysicalInput.Rect rect;
        // Extended frame bounds omit invisible resize borders outside a maximized screen.
        if (DwmGetWindowAttribute(browser, 9, out rect, Marshal.SizeOf(typeof(BossPhysicalInput.Rect))) != 0 || rect.Right <= rect.Left || rect.Bottom <= rect.Top)
            throw new InvalidOperationException("Cannot read physical browser frame bounds");
        return rect;
    }
    private static string WindowDiagnostic(IntPtr window)
    {
        return "{hwnd=" + window.ToInt64() + ",class=" + ClassName(window) + ",pid=" + ProcessId(window)
            + ",root=" + GetAncestor(window, 2).ToInt64() + ",rootowner=" + GetAncestor(window, 3).ToInt64() + "}";
    }
    private static InvalidOperationException ClickGuardFailure(string reason, IntPtr browser, int expectedX, int expectedY, BossPhysicalInput.Point cursor, IntPtr hit)
    {
        // Native geometry/identity only: no window title or page text in diagnostics.
        return new InvalidOperationException(reason + "; input refused; expected_screen=(" + expectedX + "," + expectedY
            + "); actual_cursor=(" + cursor.X + "," + cursor.Y + "); hit=" + WindowDiagnostic(hit)
            + "; browser=" + WindowDiagnostic(browser) + "; foreground=" + WindowDiagnostic(GetForegroundWindow()));
    }
    public static void Click(IntPtr browser, int left, int top, int width, int height, int x, int y, BossPhysicalInput.Scope scope)
    {
        if (x < 0 || y < 0 || x >= width || y >= height) throw new InvalidOperationException("Point outside window image");
        int screenX = checked(left + x), screenY = checked(top + y);
        RequireGeometry(browser, left, top, width, height);
        RequireForeground(browser);
        var point = new BossPhysicalInput.Point { X = screenX, Y = screenY };
        var initialHit = WindowFromPoint(point);
        if (!BelongsTo(initialHit, browser)) throw ClickGuardFailure("ownership_changed_before_move", browser, screenX, screenY, BossPhysicalInput.ReadCursor(), initialHit);
        BossPhysicalInput.MoveCursor(screenX, screenY);
        System.Threading.Thread.Sleep(250);
        RequireGeometry(browser, left, top, width, height);
        RequireForeground(browser);
        var cursor = BossPhysicalInput.ReadCursor();
        var hit = WindowFromPoint(point);
        if (cursor.X != screenX || cursor.Y != screenY) throw ClickGuardFailure("cursor_changed", browser, screenX, screenY, cursor, hit);
        if (!BelongsTo(hit, browser)) throw ClickGuardFailure("ownership_changed", browser, screenX, screenY, cursor, hit);
        scope.MarkInputStarted();
        try { mouse_event(2, 0, 0, 0, UIntPtr.Zero); System.Threading.Thread.Sleep(70); }
        finally { mouse_event(4, 0, 0, 0, UIntPtr.Zero); }
    }
    public static void Escape(IntPtr browser, int left, int top, int width, int height, BossPhysicalInput.Scope scope)
    {
        RequireGeometry(browser, left, top, width, height);
        RequireForeground(browser);
        // Never release the user's keys or turn Escape into Ctrl/Alt/Shift/Win+Escape.
        foreach (int key in new int[] { 0x10, 0x11, 0x12, 0x5B, 0x5C })
            if ((GetAsyncKeyState(key) & 0x8000) != 0)
                throw new InvalidOperationException("Modifier key is pressed; Escape input refused");
        var down = new Input { Type = 1, Data = new InputUnion { Keyboard = new KeyboardInput { VirtualKey = 0x1B } } };
        var up = new Input { Type = 1, Data = new InputUnion { Keyboard = new KeyboardInput { VirtualKey = 0x1B, Flags = 2 } } };
        RequireForeground(browser);
        scope.MarkInputStarted();
        try
        {
            uint accepted = SendInput(2, new Input[] { down, up }, Marshal.SizeOf(typeof(Input)));
            if (accepted != 2)
            {
                int error = Marshal.GetLastWin32Error();
                // A partially accepted pair may leave Escape down: release only, never repeat DOWN.
                if (accepted == 1) SendInput(1, new Input[] { up }, Marshal.SizeOf(typeof(Input)));
                throw new InvalidOperationException("Escape SendInput accepted=" + accepted + "; win32_error=" + error);
            }
        }
        catch (Exception failure)
        {
            throw new InvalidOperationException("[BOSS_INPUT_UNKNOWN] " + failure.Message, failure);
        }
    }

}
