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
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
} catch {
    # ，
}

# 1. 
$target = [IntPtr]::Zero
[WCap]::EnumWindows({
    param($h, $l)
    $sb = New-Object System.Text.StringBuilder 256
    [WCap]::GetClassName($h, $sb, 256) | Out-Null
    if ($sb.ToString() -eq $WindowClass -and [WCap]::IsWindowVisible($h)) {
        $script:target = $h
    }
    return $true
}, [IntPtr]::Zero) | Out-Null

if ($target -eq [IntPtr]::Zero) {
    Write-Result @{ ok = $false; error = "WeWorkWindow not found" }
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

# 6.
$bmp2 = New-Object System.Drawing.Bitmap $pngPath
Log-Err "DEBUG: bmp2 size=$($bmp2.Width)x$($bmp2.Height)"
$colorCounts = @{}
$totalPixels = 0
$step = [Math]::Max(1, [int]($width / 100))
Log-Err "DEBUG: step=$step width=$width height=$height"
$firstPx = $bmp2.GetPixel(0, 0)
Log-Err "DEBUG: pixel(0,0) R=$($firstPx.R) G=$($firstPx.G) B=$($firstPx.B)"
$midPx = $bmp2.GetPixel([int]($width/2), [int]($height/2))
Log-Err "DEBUG: pixel(mid,mid) R=$($midPx.R) G=$($midPx.G) B=$($midPx.B)"
for ($x = 0; $x -lt $width; $x += $step) {
    for ($y = 0; $y -lt $height; $y += $step) {
        $px = $bmp2.GetPixel($x, $y)
        $r = [int]($px.R / 16) * 16
        $gC = [int]($px.G / 16) * 16
        $b = [int]($px.B / 16) * 16
        $key = "$r,$gC,$b"
        if ($colorCounts.ContainsKey($key)) { $colorCounts[$key]++ } else { $colorCounts[$key] = 1 }
        $totalPixels++
    }
}
Log-Err "DEBUG: totalPixels=$totalPixels colorCounts.Count=$($colorCounts.Count)"
$bmp2.Dispose()
$whiteKey = '240,240,240'
$whitePct = if ($colorCounts.ContainsKey($whiteKey)) { [Math]::Round($colorCounts[$whiteKey] / $totalPixels, 3) } else { 0 }
$colorDiversity = $colorCounts.Count

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
