# Win32 真实鼠标点击 + 可选真实键盘逐字输入（设计文档 §10.3 通道 2 / §17 踩坑 6~13）
# 用法: powershell -File scripts/win-click.ps1 -X 904 -Y 700 [-CssW 1917] [-CssH 1905] [-Text "你好"]
# 输入为 page 坐标（DOMSnapshot device px，与截图 PNG 同尺寸），脚本内部：
#   1. PMv2 线程上下文内 GetWindowRect 实时取物理屏幕矩形（失败即拒绝；finally 恢复上下文）
#   2. scale = 视口宽 / CssW 换算屏幕坐标（自动覆盖 150% DPI 缩放）
#   3. WindowFromPoint 落点守卫：目标点必须归属 Chrome_RenderWidgetHostHWND，
#      否则 SetWindowPos 抬窗重试一次，仍不行 exit 2（fail-loud，不盲点）
#   4. SetCursorPos 拟人分步移动 + 悬停 + mouse_event down/up
#   5. -Text 非空：点击聚焦后（500ms）SendInput KEYEVENTF_UNICODE 逐字真实键入（200ms/字）
#      —— 点击与输入同进程原子完成，避免两次调用之间焦点被别的窗口抢走
# 退出码：0=已点击；1=窗口/参数错误；2=落点被遮挡（守卫拒绝）
param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [double]$CssW = 1917,
  [double]$CssH = 1905,
  [string]$Text = "",
  [int]$DragX = -1,
  [int]$DragY = -1,
  [int]$CdpPort = 0,
  [string]$TitleKeyword = "BOSS直聘 - Google Chrome"
)

$ErrorActionPreference = 'Stop'
$physicalInputSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'BossPhysicalInput.cs') -Raw
if ($CdpPort -gt 0) {
$windowSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'BossWindowExperiment.cs') -Raw
$windowSource = $windowSource -replace '(?m)^using [^;]+;\r?\n', ''
$physicalInputSource = "using System.Collections.Generic;`n" + $physicalInputSource + "`n" + $windowSource
}
Add-Type -TypeDefinition @"
$physicalInputSource

public class Win32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, ref uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [DllImport("user32.dll", SetLastError = true)] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    public const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    public const uint MOUSEEVENTF_LEFTUP   = 0x0004;
    public const uint SWP_NOMOVE = 0x0002;
    public const uint SWP_NOSIZE = 0x0001;
    public static readonly IntPtr HWND_TOP = IntPtr.Zero;
    public const uint INPUT_KEYBOARD = 1;
    public const uint KEYEVENTF_KEYUP = 0x0002;
    public const uint KEYEVENTF_UNICODE = 0x0004;

    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }
    // 官方 INPUT 是联合体：type(4)+pad(4)+最大成员 MOUSEINPUT(32) = 40 字节（x64）。
    // 用 Explicit 布局还原，cbSize 必须 = Marshal.SizeOf(INPUT) = 40，否则 SendInput 拒绝
    [StructLayout(LayoutKind.Explicit)]
    public struct INPUT {
        [FieldOffset(0)] public uint type;
        [FieldOffset(8)] public MOUSEINPUT mi;
        [FieldOffset(8)] public KEYBDINPUT ki;
    }

    // Unicode 真实键盘输入（中文/emoji 走 wScan 全 16 位）。
    // 注意：keybd_event 的 bScan 是单字节装不下中文，必须 SendInput；
    // §17 坑10 的「禁 SendInput」仅指鼠标绝对坐标（多显示器 VIRTUALDESK），键盘无此问题
    public static void TypeUnicode(char ch) {
        INPUT[] inputs = new INPUT[2];
        inputs[0].type = INPUT_KEYBOARD;
        inputs[0].ki.wScan = ch;
        inputs[0].ki.dwFlags = KEYEVENTF_UNICODE;
        inputs[1].type = INPUT_KEYBOARD;
        inputs[1].ki.wScan = ch;
        inputs[1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP;
        uint sent = SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
        if (sent != 2) throw new Exception("SendInput 键入失败（GetLastError=" + Marshal.GetLastWin32Error() + "）");
    }

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
        // 2026-08-31 真机修正：可见集合里取**面积最大**的，而非第一个可见——
        // Chrome 151 实测存在多个可见 render widget（弹层/扩展面等小部件），
        // 第一个可见可能是 96px 高的小部件，导致全部 Win32 点击换算错位集体打偏
        IntPtr firstHidden = IntPtr.Zero;
        IntPtr found = IntPtr.Zero;
        long bestArea = 0;
        EnumChildWindows(parent, (h, l) => {
            var sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            if (sb.ToString() == "Chrome_RenderWidgetHostHWND") {
                if (IsWindowVisible(h)) {
                    var r = BossPhysicalInput.ReadRect(h);
                    long area = (long)(r.Right - r.Left) * (long)(r.Bottom - r.Top);
                    if (area > bestArea) { bestArea = area; found = h; }
                } else if (firstHidden == IntPtr.Zero) {
                    firstHidden = h;
                }
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
  $r = [BossPhysicalInput]::ReadRect($rw)
  $vw = $r.Right - $r.Left
  $vh = $r.Bottom - $r.Top
  $sx = [int]($r.Left + $px * ($vw / $cssW))
  $sy = [int]($r.Top + $py * ($vh / $cssH))
  return @($sx, $sy)
}

function Test-PointOnRenderWidget([int]$sx, [int]$sy, $rw) {
  return [BossPhysicalInput]::PointOnWidget($rw, $sx, $sy)
}

# A thread scope avoids process-global DPI changes. Failure never falls back to virtualized bounds.
$physicalScope = [BossPhysicalInput]::Enter()
try {
if ($CdpPort -gt 0) {
  $portPattern = '(?:^|\s)--remote-debugging-port(?:=|\s+)' + $CdpPort + '(?:\s|$)'
  $processes = @(Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -match $portPattern -and $_.CommandLine -notmatch '(?:^|\s)--type=' })
  if ($processes.Count -ne 1) { throw 'STALE_OBSERVATION: debug Chrome process is ambiguous; input refused' }
  $win = [BossWindowExperiment]::Find([int]$processes[0].ProcessId)
  # Browser-level target activation does not necessarily grant OS foreground.
  # The unique independent BOSS window above proves which window may be raised.
  [BossWindowExperiment]::Foreground($win)
  Start-Sleep -Milliseconds 400
  [BossWindowExperiment]::RequireForeground($win)
} else { $win = [Win32]::FindWindowByTitle($TitleKeyword) }
function Assert-BoundForeground {
  if ($CdpPort -gt 0) { [BossWindowExperiment]::RequireForeground($win) }
}
if ($win -eq [IntPtr]::Zero) { Write-Error "未找到标题含「$TitleKeyword」的窗口" -ErrorAction Continue; exit 1 }

if ($CdpPort -le 0) {
# Chrome 最小化时 render widget 被销毁（EnumChildWindows 枚举不到，真机 2026-08-17 实证：
# 主窗口在但 0 子窗口）。必须先还原 + 前台化，再定位 render widget（与 cv-wheel.ps1 同款修复）
if ([Win32]::IsIconic($win)) {
  [Win32]::ShowWindow($win, 9) | Out-Null   # SW_RESTORE
  Start-Sleep -Milliseconds 400
}
# 强制前台（Windows 前台锁：后台进程直接 SetForegroundWindow 常被静默拒绝，真机 2026-08-17
# 实证：用户在别的窗口操作 Web 时调试 Chrome 被压后台 → 工具全线「渲染子窗口不可见」）。
# AttachThreadInput 把本线程挂到当前前台线程的输入队列后，SetForegroundWindow 即可成功
$fg = [Win32]::GetForegroundWindow()
$fgPid = [uint32]0
$fgThread = [Win32]::GetWindowThreadProcessId($fg, [ref]$fgPid)
$thisThread = [Win32]::GetCurrentThreadId()
[void][Win32]::AttachThreadInput($thisThread, $fgThread, $true)
[void][Win32]::BringWindowToTop($win)
[void][Win32]::SetForegroundWindow($win)
[void][Win32]::AttachThreadInput($thisThread, $fgThread, $false)
Start-Sleep -Milliseconds 400

}
Assert-BoundForeground
$rw = [Win32]::FindRenderWidget($win)
if ($rw -eq [IntPtr]::Zero) { if ($CdpPort -gt 0) { throw 'STALE_OBSERVATION: render widget unavailable; input refused' }; Write-Error "未找到 Chrome 渲染子窗口（窗口已还原仍无渲染子窗口）" -ErrorAction Continue; exit 1 }
if (-not [Win32]::IsWindowVisible($rw)) { if ($CdpPort -gt 0) { throw 'STALE_OBSERVATION: render widget hidden; input refused' }; Write-Error "渲染子窗口不可见：BOSS 标签页可能在后台，请切换到前台后重试" -ErrorAction Continue; exit 1 }

$pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
$sx = $pt[0]; $sy = $pt[1]
Write-Output "目标屏幕坐标: ($sx, $sy)"

if (-not (Test-PointOnRenderWidget $sx $sy $rw)) {
  # 落点被遮挡（会话终端/QQ 等窗口压住目标点会吃点击）：抬窗重试一次，重算坐标
  Write-Output "落点被其他窗口遮挡，抬窗重试"
  Assert-BoundForeground
  [Win32]::SetWindowPos($win, [Win32]::HWND_TOP, 0, 0, 0, 0, [Win32]::SWP_NOMOVE -bor [Win32]::SWP_NOSIZE) | Out-Null
  Start-Sleep -Milliseconds 300
  $pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
  $sx = $pt[0]; $sy = $pt[1]
  if (-not (Test-PointOnRenderWidget $sx $sy $rw)) {
    Write-Error "落点仍被遮挡（WindowFromPoint 校验失败），中止点击" -ErrorAction Continue
    exit 2
  }
}

# 拟人移动：从当前位置分 10 步移动过去
$cur = [BossPhysicalInput]::ReadCursor()
for ($i = 1; $i -le 10; $i++) {
    $mx = [int]($cur.X + ($sx - $cur.X) * $i / 10)
    $my = [int]($cur.Y + ($sy - $cur.Y) * $i / 10)
    [BossPhysicalInput]::MoveCursor($mx, $my)
    Start-Sleep -Milliseconds 25
}
Start-Sleep -Milliseconds 500
$expectedPoint = Get-ScreenPoint $rw $CssW $CssH $X $Y
[BossPhysicalInput]::RequireCursorOnWidget($rw, $expectedPoint[0], $expectedPoint[1])
Assert-BoundForeground
$physicalScope.MarkInputStarted()
$leftHeld = $false
try {
  if (($DragX -ge 0) -ne ($DragY -ge 0)) { throw "拖动终点必须同时提供 X/Y" }
  if ($DragX -ge 0) {
    if ($Text.Length -gt 0 -or $DragX -gt $CssW -or $DragY -gt $CssH) { throw "非法拖动参数" }
    $dragEnd = Get-ScreenPoint $rw $CssW $CssH $DragX $DragY
    if (-not [BossPhysicalInput]::PointOnWidget($rw, $dragEnd[0], $dragEnd[1])) { throw "拖动终点被遮挡" }
  }
  $leftHeld = $true
  [Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 70
  if ($DragX -ge 0) {
    $lastX = $sx; $lastY = $sy
    for ($step = 1; $step -le 16; $step++) {
      Assert-BoundForeground
      [BossPhysicalInput]::RequireCursorOnWidget($rw, $lastX, $lastY)
      $liveStart = Get-ScreenPoint $rw $CssW $CssH $X $Y
      $liveEnd = Get-ScreenPoint $rw $CssW $CssH $DragX $DragY
      if ($liveStart[0] -ne $sx -or $liveStart[1] -ne $sy -or $liveEnd[0] -ne $dragEnd[0] -or $liveEnd[1] -ne $dragEnd[1]) { throw "拖动期间窗口几何变化" }
      $lastX = [int]($sx + ($dragEnd[0] - $sx) * $step / 16)
      $lastY = [int]($sy + ($dragEnd[1] - $sy) * $step / 16)
      if (-not [BossPhysicalInput]::PointOnWidget($rw, $lastX, $lastY)) { throw "拖动路径被遮挡" }
      [BossPhysicalInput]::MoveCursor($lastX, $lastY)
      Start-Sleep -Milliseconds 25
    }
    [BossPhysicalInput]::RequireCursorOnWidget($rw, $lastX, $lastY)
    Write-Output "已拖动"
  } else { Write-Output "已点击" }
} finally {
  if ($leftHeld) { [Win32]::mouse_event([Win32]::MOUSEEVENTF_LEFTUP, 0, 0, 0, [UIntPtr]::Zero) }
}

# 点击后真实键盘逐字输入（-Text 非空时）。点完立即在同一进程键入：此刻 Chrome 刚被前台化
# 且落点守卫刚通过，焦点必在目标输入框——拆成两次进程调用会在间隙被抢焦点
if ($Text.Length -gt 0) {
  Start-Sleep -Milliseconds 500   # 等点击聚焦稳定（与原 FOCUS_DELAY 一致）
  foreach ($ch in $Text.ToCharArray()) {
    Assert-BoundForeground
    [Win32]::TypeUnicode($ch)
    Start-Sleep -Milliseconds 200 # 拟人逐字节奏（与 CDP typeChar 时代一致）
  }
  Write-Output "已输入 $($Text.Length) 字"
}
exit 0
} catch {
  if ($physicalScope.InputStarted) {
    Write-Error ("[BOSS_INPUT_UNKNOWN] " + $_.Exception.Message) -ErrorAction Continue
    exit 1
  }
  if ($CdpPort -gt 0) {
    Write-Error ('STALE_OBSERVATION: ' + $_.Exception.Message) -ErrorAction Continue
    exit 2
  }
  throw
} finally {
  $physicalScope.Dispose()
}
