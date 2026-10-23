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

    [DllImport("user32.dll")] private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] private static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
    [DllImport("user32.dll")] private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] private static extern bool IsZoomed(IntPtr window);
    [StructLayout(LayoutKind.Sequential)] private struct MonitorInfo { public int Size; public BossPhysicalInput.Rect Monitor, Work; public uint Flags; }
    private delegate bool MonitorProc(IntPtr monitor, IntPtr dc, ref BossPhysicalInput.Rect rect, IntPtr data);
    [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr clip, MonitorProc callback, IntPtr data);
    public sealed class Geometry { public BossPhysicalInput.Rect Window, Screen, Work; public BossPhysicalInput.Rect[] Screens; public int Dpi; }
    public static Geometry ReadGeometry(IntPtr browser)
    {
        BossPhysicalInput.RequirePhysicalContext();
        var info = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
        if (!GetMonitorInfo(MonitorFromWindow(browser, 2), ref info)) throw new InvalidOperationException("window_geometry_unavailable");
        int left = GetSystemMetrics(76), top = GetSystemMetrics(77);
        var screens = new List<BossPhysicalInput.Rect>();
        if (!EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (IntPtr monitor, IntPtr dc, ref BossPhysicalInput.Rect rect, IntPtr data) => { screens.Add(rect); return true; }, IntPtr.Zero) || screens.Count == 0) throw new InvalidOperationException("window_geometry_unavailable");
        int dpi = checked((int)GetDpiForWindow(browser));
        if (dpi <= 0) throw new InvalidOperationException("window_geometry_unavailable");
        return new Geometry { Window = ReadCaptureRect(browser), Screen = new BossPhysicalInput.Rect { Left = left, Top = top, Right = checked(left + GetSystemMetrics(78)), Bottom = checked(top + GetSystemMetrics(79)) }, Work = info.Work, Screens = screens.ToArray(), Dpi = dpi };
    }
    // Subtract monitor rectangles so an L-shaped virtual desktop does not authorize capturing a gap.
    public static bool Covered(BossPhysicalInput.Rect window, BossPhysicalInput.Rect[] screens)
    {
        var pending = new List<BossPhysicalInput.Rect> { window };
        foreach (var screen in screens) {
            var next = new List<BossPhysicalInput.Rect>();
            foreach (var r in pending) {
                int l = Math.Max(r.Left, screen.Left), t = Math.Max(r.Top, screen.Top), right = Math.Min(r.Right, screen.Right), bottom = Math.Min(r.Bottom, screen.Bottom);
                if (l >= right || t >= bottom) { next.Add(r); continue; }
                if (r.Top < t) next.Add(new BossPhysicalInput.Rect { Left = r.Left, Top = r.Top, Right = r.Right, Bottom = t });
                if (bottom < r.Bottom) next.Add(new BossPhysicalInput.Rect { Left = r.Left, Top = bottom, Right = r.Right, Bottom = r.Bottom });
                if (r.Left < l) next.Add(new BossPhysicalInput.Rect { Left = r.Left, Top = t, Right = l, Bottom = bottom });
                if (right < r.Right) next.Add(new BossPhysicalInput.Rect { Left = right, Top = t, Right = r.Right, Bottom = bottom });
            }
            pending = next;
        }
        return pending.Count == 0;
    }
    private static void RequireActive(string cancelFile)
    {
        if (!String.IsNullOrEmpty(cancelFile) && System.IO.File.Exists(cancelFile)) throw new InvalidOperationException("window_capture_cancelled");
    }
    // Pure physical-pixel plan: preserve size whenever it fits; negative monitor origins are valid.
    public static BossPhysicalInput.Rect Contain(BossPhysicalInput.Rect window, BossPhysicalInput.Rect work)
    {
        int width = Math.Min(checked(window.Right - window.Left), checked(work.Right - work.Left));
        int height = Math.Min(checked(window.Bottom - window.Top), checked(work.Bottom - work.Top));
        if (width <= 0 || height <= 0) throw new InvalidOperationException("window_geometry_unavailable");
        int left = Math.Max(work.Left, Math.Min(window.Left, work.Right - width));
        int top = Math.Max(work.Top, Math.Min(window.Top, work.Bottom - height));
        return new BossPhysicalInput.Rect { Left = left, Top = top, Right = checked(left + width), Bottom = checked(top + height) };
    }
    public static BossPhysicalInput.Rect Placement(Geometry before)
    {
        var target = Contain(before.Window, before.Work);
        int width = checked(before.Window.Right - before.Window.Left), height = checked(before.Window.Bottom - before.Window.Top);
        if (target.Right - target.Left == width && target.Bottom - target.Top == height) return target;
        // A wide window may fit across adjacent monitors. Exhaust translations before shrinking it.
        var xs = new HashSet<int> { before.Window.Left };
        var ys = new HashSet<int> { before.Window.Top };
        foreach (var screen in before.Screens) {
            xs.Add(screen.Left); xs.Add(checked(screen.Right - width));
            ys.Add(screen.Top); ys.Add(checked(screen.Bottom - height));
        }
        double distance = Double.PositiveInfinity;
        foreach (int x in xs) foreach (int y in ys) {
            var candidate = new BossPhysicalInput.Rect { Left = x, Top = y, Right = checked(x + width), Bottom = checked(y + height) };
            if (!Covered(candidate, before.Screens)) continue;
            double dx = (double)x - before.Window.Left, dy = (double)y - before.Window.Top;
            double score = dx * dx + dy * dy;
            if (score < distance) { distance = score; target = candidate; }
        }
        return target;
    }
    public static void Reposition(IntPtr browser, Geometry before, string cancelFile)
    {
        BossPhysicalInput.RequirePhysicalContext();
        RequireForeground(browser);
        RequireGeometry(browser, before.Window.Left, before.Window.Top, before.Window.Right - before.Window.Left, before.Window.Bottom - before.Window.Top);
        var target = Placement(before);
        bool resize = target.Right - target.Left != before.Window.Right - before.Window.Left || target.Bottom - target.Top != before.Window.Bottom - before.Window.Top;
        RequireActive(cancelFile);
        if (resize && IsZoomed(browser)) ShowWindow(browser, 9);
        // SetWindowPos takes Win32 outer bounds; DWM capture bounds omit invisible resize borders.
        var frame = ReadCaptureRect(browser);
        var raw = BossPhysicalInput.ReadRect(browser);
        int x = checked(target.Left + raw.Left - frame.Left), y = checked(target.Top + raw.Top - frame.Top);
        int width = checked(target.Right - target.Left + (raw.Right - raw.Left) - (frame.Right - frame.Left));
        int height = checked(target.Bottom - target.Top + (raw.Bottom - raw.Top) - (frame.Bottom - frame.Top));
        RequireActive(cancelFile);
        RequireForeground(browser);
        if (!SetWindowPos(browser, IntPtr.Zero, x, y, width, height, 0x0014)) throw new InvalidOperationException("window_reposition_failed"); // NOZORDER | NOACTIVATE
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

}
