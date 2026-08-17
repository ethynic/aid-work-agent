# Win32 真实鼠标点击（设计文档 §10.3 通道 2 / §17 踩坑 6~13）
# 用法: powershell -File scripts/win-click.ps1 -X 904 -Y 700 [-CssW 1917] [-CssH 1905]
# 输入为 page 坐标（DOMSnapshot device px，与截图 PNG 同尺寸），脚本内部：
#   1. GetWindowRect(Chrome_RenderWidgetHostHWND) 实时取渲染视口屏幕矩形（每次校准，不缓存）
#   2. scale = 视口宽 / CssW 换算屏幕坐标（自动覆盖 150% DPI 缩放）
#   3. WindowFromPoint 落点守卫：目标点必须归属 Chrome_RenderWidgetHostHWND，
#      否则 SetWindowPos 抬窗重试一次，仍不行 exit 2（fail-loud，不盲点）
#   4. SetCursorPos 拟人分步移动 + 悬停 + mouse_event down/up
# 退出码：0=已点击；1=窗口/参数错误；2=落点被遮挡（守卫拒绝）
param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [double]$CssW = 1917,
  [double]$CssH = 1905,
  [string]$TitleKeyword = "BOSS直聘 - Google Chrome"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class Win32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP   = 0x0004;
    public const uint SWP_NOMOVE = 0x0002;
    public const uint SWP_NOSIZE = 0x0001;
    public static readonly IntPtr HWND_TOP = IntPtr.Zero;

    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }

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
        IntPtr firstHidden = IntPtr.Zero;
        IntPtr found = IntPtr.Zero;
        EnumChildWindows(parent, (h, l) => {
            var sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            // Chrome 会保留多个 render widget（当前标签页可见 + 后台标签页隐藏），
            // 必须优先取可见的，否则取到隐藏实例会误判「标签页在后台」
            if (sb.ToString() == "Chrome_RenderWidgetHostHWND") {
                if (IsWindowVisible(h)) { found = h; return false; }
                if (firstHidden == IntPtr.Zero) firstHidden = h;
            }
            return true;
        }, IntPtr.Zero);
        return found != IntPtr.Zero ? found : firstHidden;
    }

    public static string ClassNameOf(IntPtr hWnd) {
        var sb = new StringBuilder(256);
        GetClassName(hWnd, sb, 256);
        return sb.ToString();
    }
}
"@

function Get-ScreenPoint($rw, [double]$cssW, [double]$cssH, [int]$px, [int]$py) {
  $r = New-Object Win32+RECT
  [Win32]::GetWindowRect($rw, [ref]$r) | Out-Null
  $vw = $r.Right - $r.Left
  $vh = $r.Bottom - $r.Top
  $sx = [int]($r.Left + $px * ($vw / $cssW))
  $sy = [int]($r.Top + $py * ($vh / $cssH))
  return @($sx, $sy)
}

function Test-PointOnRenderWidget([int]$sx, [int]$sy, $rw) {
  $pt = New-Object Win32+POINT
  $pt.X = $sx; $pt.Y = $sy
  $h = [Win32]::WindowFromPoint($pt)
  if ($h -eq $rw) { return $true }
  # WindowFromPoint 可能返回 render widget 的子孙/同族 Chrome 图层，类名一致也视为命中
  return [Win32]::ClassNameOf($h) -eq "Chrome_RenderWidgetHostHWND"
}

$win = [Win32]::FindWindowByTitle($TitleKeyword)
if ($win -eq [IntPtr]::Zero) { Write-Error "未找到标题含「$TitleKeyword」的窗口"; exit 1 }

# Chrome 最小化时 render widget 被销毁（EnumChildWindows 枚举不到，真机 2026-08-17 实证：
# 主窗口在但 0 子窗口）。必须先还原 + 前台化，再定位 render widget（与 cv-wheel.ps1 同款修复）
if ([Win32]::IsIconic($win)) {
  [Win32]::ShowWindow($win, 9) | Out-Null   # SW_RESTORE
  Start-Sleep -Milliseconds 400
}
[Win32]::SetForegroundWindow($win) | Out-Null
Start-Sleep -Milliseconds 300

$rw = [Win32]::FindRenderWidget($win)
if ($rw -eq [IntPtr]::Zero) { Write-Error "未找到 Chrome 渲染子窗口（窗口已还原仍无渲染子窗口）"; exit 1 }
if (-not [Win32]::IsWindowVisible($rw)) { Write-Error "渲染子窗口不可见：BOSS 标签页可能在后台，请切换到前台后重试"; exit 1 }

$pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
$sx = $pt[0]; $sy = $pt[1]
Write-Output "目标屏幕坐标: ($sx, $sy)"

if (-not (Test-PointOnRenderWidget $sx $sy $rw)) {
  # 落点被遮挡（会话终端/QQ 等窗口压住目标点会吃点击）：抬窗重试一次，重算坐标
  Write-Output "落点被其他窗口遮挡，抬窗重试"
  [Win32]::SetWindowPos($win, [Win32]::HWND_TOP, 0, 0, 0, 0, [Win32]::SWP_NOMOVE -bor [Win32]::SWP_NOSIZE) | Out-Null
  Start-Sleep -Milliseconds 300
  $pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
  $sx = $pt[0]; $sy = $pt[1]
  if (-not (Test-PointOnRenderWidget $sx $sy $rw)) {
    Write-Error "落点仍被遮挡（WindowFromPoint 校验失败），中止点击"
    exit 2
  }
}

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
exit 0
