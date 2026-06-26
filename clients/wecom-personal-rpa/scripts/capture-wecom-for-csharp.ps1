# Capture WeCom main window screenshot for C# caller.
# Output: a single JSON line on stdout with: { ok, png_path, left, top, width, height, dpi_scale, wecom_version, hwnd, white_ratio, color_diversity, error }
# No extra log output to stdout (logs go to stderr).
#
# Why this exists: C# CopyFromScreen had DPI awareness + SetWindowPos issues.
# PowerShell (system-level, runs as the interactive user) captures correctly.
# C# ScreenCapturer calls this script via Process.Start and reads the PNG.

param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$WindowClass = "WeWorkWindow"
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Result([hashtable]$r) {
    [Console]::Out.WriteLine(($r | ConvertTo-Json -Compress -Depth 3))
}

# helper: stderr log
function Log-Err([string]$msg) { [Console]::Error.WriteLine("[capture-ps] $msg") }

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

try {
    Add-Type -AssemblyName System.Drawing
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class WCap {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Auto)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rc);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);
    [DllImport("gdi32.dll")] public static extern int GetDeviceCaps(IntPtr hdc, int nIndex);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, ref uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    // DPI awareness（修复 GetWindowRect 在高 DPI 下返回虚拟坐标导致截图错位）
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
    # DPI awareness: 让 PS 进程按物理像素工作（PER_MONITOR_AWARE_V2 = -4）
    # 必要性：屏幕 DPI=150% 时，GetWindowRect 默认返回按 96 DPI 缩放的虚拟坐标，
    # CopyFromScreen 用此坐标截图会偏移+缩小一半。设为 per-monitor aware 后坐标系统一。
    try {
        # SetProcessDpiAwarenessContext(PerMonitorAwareV2 = -4)，Win10 1703+ 支持
        [WCap]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null
        Log-Err "DPI awareness set: PER_MONITOR_AWARE_V2 (-4)"
    } catch {
        # fallback: SetProcessDPIAware（系统级 aware，Vista+）
        try { [WCap]::SetProcessDPIAware() | Out-Null; Log-Err "DPI awareness set: SYSTEM_AWARE" }
        catch { Log-Err "DPI awareness 设置失败: $($_.Exception.Message)" }
    }
    # Pixel analyzer as separate Add-Type (needs System.Drawing reference).
    # Why split: Win32 P/Invoke class WCap doesn't need System.Drawing; isolating the analyzer
    # lets us add -ReferencedAssemblies only for the type that actually needs it.
    Add-Type -TypeDefinition @"
using System;
public static class ImgAn {
    public static int[] Analyze(string pngPath) {
        using (var bmp = new System.Drawing.Bitmap(pngPath)) {
            int width = bmp.Width;
            int height = bmp.Height;
            int step = Math.Max(1, width / 100);
            var colorCounts = new System.Collections.Generic.HashSet<int>();
            int totalPixels = 0;
            int whitePixels = 0;
            for (int x = 0; x < width; x += step) {
                for (int y = 0; y < height; y += step) {
                    var px = bmp.GetPixel(x, y);
                    int r = (px.R >> 4) & 0x0F;
                    int g = (px.G >> 4) & 0x0F;
                    int b = (px.B >> 4) & 0x0F;
                    int key = (r << 8) | (g << 4) | b;
                    colorCounts.Add(key);
                    if (px.R >= 240 && px.G >= 240 && px.B >= 240) whitePixels++;
                    totalPixels++;
                }
            }
            return new int[] { colorCounts.Count, whitePixels, totalPixels };
        }
    }
}
"@ -ReferencedAssemblies System.Drawing
} catch {
    # 记录 Add-Type 失败原因到 stderr（不静默吞掉）
    Log-Err "Add-Type failed: $($_.Exception.Message)"
    Log-Err "InnerException: $($_.Exception.InnerException.Message)"
}

# 1. 找尺寸最大的 WeWorkWindow 类可见窗口
# 必要性：企微某些版本会创建多个 WeWorkWindow 类窗口（主窗口 + 1000x650 的弹窗/webview 壳），
# 之前用「首个匹配」会误选 1000x650 的伪窗口，截到黑屏。
$target = [IntPtr]::Zero
$targetArea = 0
[WCap]::EnumWindows({
    param($h, $l)
    $sb = New-Object System.Text.StringBuilder 256
    [WCap]::GetClassName($h, $sb, 256) | Out-Null
    if ($sb.ToString() -eq $WindowClass -and [WCap]::IsWindowVisible($h)) {
        $r = New-Object WCap+RECT
        [void][WCap]::GetWindowRect($h, [ref]$r)
        $w = $r.Right - $r.Left
        $hgt = $r.Bottom - $r.Top
        $area = $w * $hgt
        # 过滤掉太小的弹窗（< 600x400），且选面积最大的
        if ($w -ge 600 -and $hgt -ge 400 -and $area -gt $script:targetArea) {
            $script:target = $h
            $script:targetArea = $area
        }
    }
    return $true
}, [IntPtr]::Zero) | Out-Null

if ($target -eq [IntPtr]::Zero) {
    Write-Result @{ ok = $false; error = "WeWorkWindow not found (no visible window >= 600x400)" }
    exit 1
}

# 2. （ Unicode ）
$expected = [string]::new([char[]](0x4F01, 0x4E1A, 0x5FAE, 0x4FE1))
$sbTitle = New-Object System.Text.StringBuilder 256
[WCap]::GetWindowText($target, $sbTitle, 256) | Out-Null
$title = $sbTitle.ToString()
if (-not $title.Contains($expected)) {
    Write-Result @{ ok = $false; error = "title not WeCom: '$title'" }
    exit 2
}

# 3.  +
if ([WCap]::IsIconic($target)) {
    [WCap]::ShowWindow($target, 9) | Out-Null
    Start-Sleep -Milliseconds 300
}
[WCap]::ShowWindow($target, 5) | Out-Null
[WCap]::SetForegroundWindow($target) | Out-Null
Start-Sleep -Milliseconds 800
$fg = [WCap]::GetForegroundWindow()
if ($fg -ne $target) {
    # ALT-key trick
    [WCap]::keybd_event(0xA4, 0, 0, [UIntPtr]::Zero)
    [WCap]::keybd_event(0xA4, 0, 2, [UIntPtr]::Zero)
    [WCap]::SetForegroundWindow($target) | Out-Null
    Start-Sleep -Milliseconds 600
    $fg = [WCap]::GetForegroundWindow()
}
if ($fg -ne $target) {
    # AttachThreadInput trick：把当前线程 attach 到前台窗口拥有者的线程，
    # 借此获得 SetForegroundWindow 的"前台锁"权限（绕过 Windows 前台锁定）。
    $curThread = [WCap]::GetCurrentThreadId()
    $fgHwnd = [WCap]::GetForegroundWindow()
    $fgPid = 0
    $fgTid = [WCap]::GetWindowThreadProcessId($fgHwnd, [ref]$fgPid)
    $targetTid = [WCap]::GetWindowThreadProcessId($target, [ref]$null)
    [WCap]::AttachThreadInput($curThread, $fgTid, $true) | Out-Null
    [WCap]::AttachThreadInput($curThread, $targetTid, $true) | Out-Null
    try {
        [WCap]::SetForegroundWindow($target) | Out-Null
        Start-Sleep -Milliseconds 400
    } finally {
        [WCap]::AttachThreadInput($curThread, $fgTid, $false) | Out-Null
        [WCap]::AttachThreadInput($curThread, $targetTid, $false) | Out-Null
    }
    $fg = [WCap]::GetForegroundWindow()
}
if ($fg -ne $target) {
    Write-Result @{ ok = $false; error = "cannot bring WeCom to foreground" }
    exit 3
}

# 4.  + 
$rect = New-Object WCap+RECT
[void][WCap]::GetWindowRect($target, [ref]$rect)
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 600 -or $height -lt 400) {
    Write-Result @{ ok = $false; error = "window too small: ${width}x${height}" }
    exit 4
}

# 5. 
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$pngPath = Join-Path $OutDir "wecom_main_$ts.png"
$bmp = New-Object System.Drawing.Bitmap $width, $height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, (New-Object System.Drawing.Size $width, $height))
$bmp.Save($pngPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()

# 6. pixel sanity check (C# impl via ImgAn.Analyze, fast + reliable)
try {
    $analysis = [ImgAn]::Analyze($pngPath)
    $colorDiversity = $analysis[0]
    $whitePixels = $analysis[1]
    $totalPixels = $analysis[2]
    $whitePct = if ($totalPixels -gt 0) { [Math]::Round($whitePixels / $totalPixels, 3) } else { 0 }
} catch {
    Log-Err "ImgAn.Analyze failed: $($_.Exception.Message)"
    Log-Err "ImgAn type available: $([ImgAn] -ne $null)"
    # fallback: try to load and analyze via raw PowerShell (slow but works)
    try {
        $bmpFallback = New-Object System.Drawing.Bitmap $pngPath
        $colorCounts = @{}
        $totalPixels = 0
        $whitePixels = 0
        $step = [Math]::Max(1, [int]($bmpFallback.Width / 100))
        for ($x = 0; $x -lt $bmpFallback.Width; $x += $step) {
            for ($y = 0; $y -lt $bmpFallback.Height; $y += $step) {
                $px = $bmpFallback.GetPixel($x, $y)
                $key = "$([int]($px.R/16)*16),$([int]($px.G/16)*16),$([int]($px.B/16)*16)"
                if ($colorCounts.ContainsKey($key)) { $colorCounts[$key]++ } else { $colorCounts[$key] = 1 }
                if ($px.R -ge 240 -and $px.G -ge 240 -and $px.B -ge 240) { $whitePixels++ }
                $totalPixels++
            }
        }
        $colorDiversity = $colorCounts.Count
        $whitePct = if ($totalPixels -gt 0) { [Math]::Round($whitePixels / $totalPixels, 3) } else { 0 }
        Log-Err "Fallback PS analyze OK: colorDiversity=$colorDiversity"
        $bmpFallback.Dispose()
    } catch {
        Log-Err "Fallback analyze also failed: $($_.Exception.Message)"
    }
}

# 7. DPI
$dpiScale = 1.0
try {
    $hDC = [WCap]::GetDC($target)
    $dpiX = [WCap]::GetDeviceCaps($hDC, 88)  # LOGPIXELSX
    [WCap]::ReleaseDC($target, $hDC) | Out-Null
    $dpiScale = [Math]::Round($dpiX / 96.0, 3)
} catch { }

# 8. （ exe ）
$wecomVersion = "unknown"
try {
    $proc = Get-Process -Name "WXWork" -ErrorAction Stop | Select-Object -First 1
    if ($proc) {
        $exePath = $proc.MainModule.FileName
        if ($exePath -and (Test-Path $exePath)) {
            $fvi = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($exePath)
            if ($fvi.FileVersion) { $wecomVersion = $fvi.FileVersion }
            elseif ($fvi.ProductVersion) { $wecomVersion = $fvi.ProductVersion }
        }
    }
} catch { }

Write-Result @{
    ok = $true
    png_path = $pngPath
    left = $rect.Left
    top = $rect.Top
    width = $width
    height = $height
    dpi_scale = $dpiScale
    wecom_version = $wecomVersion
    hwnd = $target.ToInt64().ToString('X')
    white_ratio = $whitePct
    color_diversity = $colorDiversity
}
exit 0
