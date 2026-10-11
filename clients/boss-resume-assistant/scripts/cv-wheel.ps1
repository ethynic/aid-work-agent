# Win32 mouse wheel scroll for canvas-rendered resume reading (design doc section 10.8).
# Usage: powershell -File scripts/cv-wheel.ps1 -X 531 -Y 658 -CssW 1249 -CssH 1277 [-DeltaY -120] [-Notches 6]
#
# Input X/Y are page coordinates (DOMSnapshot device px, same coordinate space as the
# full-page screenshot PNG). The script:
#   1. FindWindowByTitle(TitleKeyword) -> top-level Chrome window
#   2. FindRenderWidget -> the VISIBLE Chrome_RenderWidgetHostHWND child
#      (Chrome keeps hidden render widgets for background tabs, must pick the visible one)
#   3. In a verified PMv2 thread context, map page coords to physical screen coords:
#      sx = rect.Left + X * (viewportW / CssW)   (scale covers DPI zoom like win-click.ps1)
#   4. SetCursorPos, then send Notches wheel ticks of DeltaY each (~150ms apart)
#
# WHY Win32 instead of CDP (verified on device 2026-08-14): BOSS anti-bot blocks synthetic
# CDP Input.dispatchMouseEvent mouseWheel and synthetic PageDown/ArrowDown/Space keys on
# the resume canvas (the WASM virtual scroll never moves). Only real mouse_event WHEEL works.
#
# dwData trap (verified on device): PowerShell [uint32] cast of a negative number fails
# silently (InvalidCastIConvertible -> data=0 -> no scrolling). Negative DeltaY must be
# wrapped into the unsigned range: [uint32]($DeltaY + 4294967296).
#
# Borrows the real cursor for 1-2 seconds: caller must warn the user to keep hands off.
# Exit codes: 0=scrolled; 1=window/render widget not found or not visible; 2=bad params
param(
  [Parameter(Mandatory=$true)][int]$X,
  [Parameter(Mandatory=$true)][int]$Y,
  [double]$CssW = 1249,
  [double]$CssH = 1277,
  [int]$DeltaY = -120,
  [int]$Notches = 6,
  [string]$TitleKeyword = "BOSS直聘 - Google Chrome"
)

if ($CssW -le 0 -or $CssH -le 0) { Write-Error "CssW/CssH must be positive (got ${CssW}x${CssH})"; exit 2 }
if ($Notches -le 0) { Write-Error "Notches must be positive (got $Notches)"; exit 2 }
if ($DeltaY -eq 0) { Write-Error "DeltaY must not be 0"; exit 2 }

$ErrorActionPreference = 'Stop'
$physicalInputSource = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'BossPhysicalInput.cs') -Raw
Add-Type -TypeDefinition @"
$physicalInputSource

public class CvWin32 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet=CharSet.Ansi)] public static extern int GetClassName(IntPtr hWnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, ref uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    public const uint MOUSEEVENTF_WHEEL = 0x0800;

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
        IntPtr firstHidden = IntPtr.Zero;
        IntPtr found = IntPtr.Zero;
        long bestArea = 0;
        EnumChildWindows(parent, (h, l) => {
            var sb = new StringBuilder(256);
            GetClassName(h, sb, 256);
            // Chrome keeps several render widgets (visible tab + hidden background tabs);
            // prefer the visible instance or coordinates calibrate against the wrong one.
            if (sb.ToString() == "Chrome_RenderWidgetHostHWND") {
                if (IsWindowVisible(h)) {
                    var r = BossPhysicalInput.ReadRect(h);
                    long area = (long)(r.Right - r.Left) * (long)(r.Bottom - r.Top);
                    if (area > bestArea) { bestArea = area; found = h; }
                } else if (firstHidden == IntPtr.Zero) firstHidden = h;
            }
            return true;
        }, IntPtr.Zero);
        return found != IntPtr.Zero ? found : firstHidden;
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

$physicalScope = [BossPhysicalInput]::Enter()
try {
$win = [CvWin32]::FindWindowByTitle($TitleKeyword)
if ($win -eq [IntPtr]::Zero) { Write-Error "window not found by title keyword: $TitleKeyword" -ErrorAction Continue; exit 1 }

# Chrome minimized -> render widget is destroyed and EnumChildWindows yields nothing
# (verified on device: main window found but 0 children). Restore + foreground FIRST,
# then locate the render widget.
if ([CvWin32]::IsIconic($win)) {
    [CvWin32]::ShowWindow($win, 9) | Out-Null   # SW_RESTORE
    Start-Sleep -Milliseconds 400
}
# Force foreground (Windows foreground lock: SetForegroundWindow from a background process is
# silently denied; AttachThreadInput onto the current foreground thread makes it succeed).
$fg = [CvWin32]::GetForegroundWindow()
$fgPid = [uint32]0
$fgThread = [CvWin32]::GetWindowThreadProcessId($fg, [ref]$fgPid)
$thisThread = [CvWin32]::GetCurrentThreadId()
[void][CvWin32]::AttachThreadInput($thisThread, $fgThread, $true)
[void][CvWin32]::BringWindowToTop($win)
[void][CvWin32]::SetForegroundWindow($win)
[void][CvWin32]::AttachThreadInput($thisThread, $fgThread, $false)
Start-Sleep -Milliseconds 400

$rw = [CvWin32]::FindRenderWidget($win)
if ($rw -eq [IntPtr]::Zero) { Write-Error "Chrome_RenderWidgetHostHWND not found: the BOSS Chrome window is suspended in background (its render child windows are destroyed). Click the Chrome window to bring it to the foreground, then retry." -ErrorAction Continue; exit 1 }
if (-not [CvWin32]::IsWindowVisible($rw)) { Write-Error "render widget not visible (tab may be in background)" -ErrorAction Continue; exit 1 }

$pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
$sx = $pt[0]; $sy = $pt[1]
if (-not [BossPhysicalInput]::PointOnWidget($rw, $sx, $sy)) {
  [void][CvWin32]::SetWindowPos($win, [IntPtr]::Zero, 0, 0, 0, 0, 3) # HWND_TOP, NOMOVE | NOSIZE
  Start-Sleep -Milliseconds 300
  $pt = Get-ScreenPoint $rw $CssW $CssH $X $Y
  $sx = $pt[0]; $sy = $pt[1]
  if (-not [BossPhysicalInput]::PointOnWidget($rw, $sx, $sy)) { throw 'Render-widget ownership check failed; wheel refused' }
}
Write-Output "screen point: ($sx, $sy)"
[BossPhysicalInput]::MoveCursor($sx, $sy)
Start-Sleep -Milliseconds 200

# mouse_event expects dwData as uint; a negative DeltaY must be wrapped into the unsigned
# range or the [uint32] cast silently produces 0 (verified on device: no scrolling at all).
if ($DeltaY -ge 0) { $dwData = [uint32]$DeltaY } else { $dwData = [uint32]([double]$DeltaY + 4294967296) }

for ($i = 1; $i -le $Notches; $i++) {
  $expectedPoint = Get-ScreenPoint $rw $CssW $CssH $X $Y
  [BossPhysicalInput]::RequireCursorOnWidget($rw, $expectedPoint[0], $expectedPoint[1])
  $physicalScope.MarkInputStarted()
  [CvWin32]::mouse_event([CvWin32]::MOUSEEVENTF_WHEEL, 0, 0, $dwData, [UIntPtr]::Zero)
  if ($i -lt $Notches) { Start-Sleep -Milliseconds 150 }
}
Write-Output "wheeled $Notches tick(s) of $DeltaY at page ($X, $Y)"
exit 0
} catch {
  if ($physicalScope.InputStarted) {
    Write-Error ("[BOSS_INPUT_UNKNOWN] " + $_.Exception.Message) -ErrorAction Continue
    exit 1
  }
  throw
} finally {
  $physicalScope.Dispose()
}
