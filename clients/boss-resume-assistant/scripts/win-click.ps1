# 一次性实验：用 Win32 SendInput 真实点击 BOSS 页面视口内坐标
# 用法: powershell -File scripts/win-click.ps1 -X 1787 -Y 105
param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [string]$TitleKeyword = "BOSS直聘 - Google Chrome"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
using System.Collections.Generic;

public class Win32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP   = 0x0004;

    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }

    public static IntPtr FindWindowByTitle(string keyword) {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(256);
            GetWindowText(h, sb, 256);
            if (sb.ToString().Contains(keyword)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static IntPtr FindRenderWidget(IntPtr parent) {
        IntPtr found = IntPtr.Zero;
        EnumChildWindows(parent, (h, l) => {
            var sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            if (sb.ToString() == "Chrome_RenderWidgetHostHWND" && IsWindowVisible(h)) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@

$win = [Win32]::FindWindowByTitle($TitleKeyword)
if ($win -eq [IntPtr]::Zero) { Write-Error "未找到标题含「$TitleKeyword」的窗口"; exit 1 }

$rw = [Win32]::FindRenderWidget($win)
if ($rw -eq [IntPtr]::Zero) { Write-Error "未找到 Chrome 渲染子窗口"; exit 1 }

$r = New-Object Win32+RECT
[Win32]::GetWindowRect($rw, [ref]$r) | Out-Null
$vw = $r.Right - $r.Left
$vh = $r.Bottom - $r.Top
Write-Output "渲染视口: left=$($r.Left) top=$($r.Top) ${vw}x${vh}"

# 视口 CSS 坐标 -> 屏幕坐标（截图视口 1917x1905，与渲染窗口像素不同时按比例缩放）
$scaleX = $vw / 1917.0
$scaleY = $vh / 1905.0
$sx = [int]($r.Left + $X * $scaleX)
$sy = [int]($r.Top + $Y * $scaleY)
Write-Output "目标屏幕坐标: ($sx, $sy)"

[Win32]::SetForegroundWindow($win) | Out-Null
Start-Sleep -Milliseconds 400

# 拟人移动：从当前位置分 10 步移动过去
$cur = [System.Windows.Forms.Cursor]::Position
for ($i = 1; $i -le 10; $i++) {
    $mx = [int]($cur.X + ($sx - $cur.X) * $i / 10)
    $my = [int]($cur.Y + ($sy - $cur.Y) * $i / 10)
    [Win32]::SetCursorPos($mx, $my) | Out-Null
    Start-Sleep -Milliseconds 25
}
Start-Sleep -Milliseconds 500
[Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 70
[Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
Write-Output "已点击"
