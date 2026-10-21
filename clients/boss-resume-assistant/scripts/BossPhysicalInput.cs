using System;
using System.Runtime.InteropServices;
using System.Text;

// Shared by window-experiment.ps1 (the deleted input fallback scripts were removed
// 2026-10-21; input now goes through @aid/win32-native-input). Page coordinates are
// mapped against physical HWND bounds; no DPI multiplier is inferred or cached.
public static class BossPhysicalInput
{
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct Point { public int X, Y; }

    private static class Native
    {
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("user32.dll")] public static extern IntPtr GetThreadDpiAwarenessContext();
        [DllImport("user32.dll")] public static extern bool AreDpiAwarenessContextsEqual(IntPtr a, IntPtr b);
        [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
        [DllImport("user32.dll", SetLastError = true)] public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);
        [DllImport("user32.dll", SetLastError = true)] public static extern bool GetPhysicalCursorPos(out Point point);
        [DllImport("user32.dll", SetLastError = true)] public static extern bool SetCursorPos(int x, int y);
        [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(Point point);
    }

    private static readonly IntPtr PhysicalContext = new IntPtr(-4); // PER_MONITOR_AWARE_V2

    public sealed class Scope : IDisposable
    {
        private readonly IntPtr previous;
        private readonly uint thread;
        private bool disposed;
        public bool InputStarted { get; private set; }

        internal Scope(IntPtr previous)
        {
            this.previous = previous;
            thread = Native.GetCurrentThreadId();
        }

        public void Dispose()
        {
            if (disposed) return;
            if (thread != Native.GetCurrentThreadId())
                throw new InvalidOperationException(FailureMessage("DPI context thread changed; input must stop"));
            if (Native.SetThreadDpiAwarenessContext(previous) == IntPtr.Zero ||
                !Native.AreDpiAwarenessContextsEqual(Native.GetThreadDpiAwarenessContext(), previous))
                throw new InvalidOperationException(FailureMessage("Cannot restore DPI context"));
            disposed = true;
        }

        private string FailureMessage(string message)
        {
            return InputStarted ? "[BOSS_INPUT_UNKNOWN] " + message : message;
        }

        public void MarkInputStarted()
        {
            RequirePhysicalContext();
            if (thread != Native.GetCurrentThreadId())
                throw new InvalidOperationException("DPI context thread changed; input refused");
            InputStarted = true;
        }
    }

    public static Scope Enter()
    {
        IntPtr previous = Native.SetThreadDpiAwarenessContext(PhysicalContext);
        if (previous == IntPtr.Zero)
            throw new InvalidOperationException("Cannot enter per-monitor-v2 DPI context; input refused");
        var scope = new Scope(previous);
        try { RequirePhysicalContext(); return scope; }
        catch { scope.Dispose(); throw; }
    }

    public static void RequirePhysicalContext()
    {
        if (!Native.AreDpiAwarenessContextsEqual(Native.GetThreadDpiAwarenessContext(), PhysicalContext))
            throw new InvalidOperationException("Physical DPI context lost; input refused");
    }

    public static Rect ReadRect(IntPtr hwnd)
    {
        RequirePhysicalContext();
        Rect rect;
        if (!Native.GetWindowRect(hwnd, out rect) || rect.Right <= rect.Left || rect.Bottom <= rect.Top)
            throw new InvalidOperationException("Cannot read physical render-widget bounds; input refused");
        return rect;
    }

    public static Point ReadCursor()
    {
        RequirePhysicalContext();
        Point point;
        if (!Native.GetPhysicalCursorPos(out point))
            throw new InvalidOperationException("Cannot read physical cursor; input refused");
        return point;
    }

    public static bool PointOnWidget(IntPtr widget, int x, int y)
    {
        RequirePhysicalContext();
        var point = new Point { X = x, Y = y };
        return widget != IntPtr.Zero && Native.WindowFromPoint(point) == widget;
    }

    public static void MoveCursor(int x, int y)
    {
        RequirePhysicalContext();
        if (!Native.SetCursorPos(x, y))
            throw new InvalidOperationException("Cannot move cursor; input refused");
        Point actual = ReadCursor();
        if (actual.X != x || actual.Y != y)
            throw new InvalidOperationException("Physical cursor differs from requested point; input refused");
    }

    // Call immediately before mouse DOWN / each WHEEL event, after all settling delays.
    public static void RequireCursorOnWidget(IntPtr widget, int x, int y)
    {
        Point actual = ReadCursor();
        if (actual.X != x || actual.Y != y || !PointOnWidget(widget, x, y))
            throw new InvalidOperationException("Cursor or render-widget ownership changed; input refused");
    }
}
